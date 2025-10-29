#!/usr/bin/env python3
"""
import_reqif_dynamic.py - GENERALIZED AND ROBUST VERSION

- Parses .reqif files (robust XML-based parser; supports common namespaces and rich text)
- Creates/updates GitHub issues for each requirement
- Finds/creates ProjectV2 items and maps attribute values to project fields via GraphQL
- Hard-codes Requirement Label to "System Requirement" on the project field
"""

import os
import sys
import glob
import json
import requests
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List

# Define the official ReqIF Namespace for robust lookups
REQIF_NS = "{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}"
# Define XHTML Namespace for rich-text parsing
XHTML_NS = "{http://www.w3.org/1999/xhtml}"

# -------------------------
# Configuration / Environment
# -------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPOSITORY")  # "owner/repo"
PROJECT_NAME = os.getenv("PROJECT_NAME")  # optional: to pick specific Projects v2 by title

if not GITHUB_TOKEN or not REPO:
    print("❌ Missing GITHUB_TOKEN or GITHUB_REPOSITORY env vars.")
    sys.exit(1)

# REST headers for Issues API (token auth)
REST_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

# GraphQL headers (Bearer)
GRAPHQL_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json",
}

GRAPHQL_URL = "https://api.github.com/graphql"
REST_API_BASE = f"https://api.github.com/repos/{REPO}"

# Hard-code Requirement Label mapping (user requested the label be hard-coded)
HARDCODED_REQUIREMENT_LABEL = "System Requirement"

# -------------------------
# 1) Find ReqIF file
# -------------------------
reqif_files = glob.glob("**/*.reqif", recursive=True) + glob.glob("**/*.reqifz", recursive=True)
if not reqif_files:
    print("❌ No .reqif/.reqifz file found in repository.")
    sys.exit(1)

REQIF_FILE = reqif_files[0]
print(f"📄 Found ReqIF file: {REQIF_FILE}")
print(f"📄 Parsing ReqIF: {REQIF_FILE}")

# -------------------------
# 2) ReqIF XML parsing (General & Robust Two-Pass Logic)
# -------------------------
def get_text_from_node(node: ET.Element) -> str:
    """
    Recursively extract text content from a node, handling simple text and complex
    nested rich text (XHTML) structures commonly found in ReqIF descriptions.
    """
    if node is None:
        return ""

    # Start with the direct text content of the node
    text = (node.text or "").strip()

    # Look for XHTML content, which is the standard for rich-text descriptions
    xhtml_content = node.find(f"{REQIF_NS}XhtmlContent") or node.find("XhtmlContent")

    if xhtml_content is not None:
        # Use itertext() to safely extract text from all nested XHTML/HTML tags
        ET.register_namespace('xhtml', XHTML_NS.strip('{}'))
        # Concatenate all text from the rich content tree
        text += "".join(xhtml_content.itertext()).strip()

    # If not rich text, iterate over normal child elements
    for child in list(node):
        # Recursively get text from children and their tails
        text += get_text_from_node(child)
        text += (child.tail or "").strip() 
            
    return text.strip()


def parse_reqif(path: str) -> Dict[str, Dict[str, Any]]:
    """
    Returns mapping: { rid: { 'title': ..., 'attrs': { long_name: value, ... }, 'description': ... } }
    Implements a general two-pass XML parser for ReqIF.
    """
    try:
        # NOTE: ET.parse() handles both .reqif (XML) and .reqifz (ZIP, if unzipped) 
        tree = ET.parse(path)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"❌ Failed to parse XML file: {e}")
        return {}

    # --- Pass 1: Build map of ATTRIBUTE-DEFINITION IDENTIFIER -> LONG-NAME ---
    attr_def_map = {}  # IDENTIFIER -> LONG-NAME
    
    # Target all possible attribute definition types (Namespaced + Fallback)
    attr_types = ["ATTRIBUTE-DEFINITION-STRING", "ATTRIBUTE-DEFINITION-INTEGER", 
                  "ATTRIBUTE-DEFINITION-REAL", "ATTRIBUTE-DEFINITION-ENUMERATION", 
                  "ATTRIBUTE-DEFINITION-XHTML", "ATTRIBUTE-DEFINITION-DATE"]

    all_attr_defs = []
    for t in attr_types:
        all_attr_defs.extend(root.findall(".//" + f"{REQIF_NS}{t}"))
        all_attr_defs.extend(root.findall(".//" + t)) # Fallback

    for ad in all_attr_defs:
        ident = ad.attrib.get("IDENTIFIER") or ad.attrib.get("identifier")
        long_name = ad.attrib.get("LONG-NAME") or ad.attrib.get("long-name")
        
        # Fallback to finding LONG-NAME as a child element text
        if not long_name:
            long_name = ad.findtext(f"{REQIF_NS}LONG-NAME") or ad.findtext("LONG-NAME")
            
        # Use IDENTIFIER as fallback for LONG-NAME if nothing is found
        long_name = long_name or ident  

        if ident and long_name:
            # Store ID -> Long Name (e.g., REQ-ID -> ID)
            attr_def_map[ident] = long_name
            # Store Long Name -> Long Name (for cases where it's used directly)
            attr_def_map[long_name] = long_name 
            # Store ID without namespace (for some tools that omit the full ID)
            attr_def_map[ident.split(':')[-1]] = long_name
            
    # --- Pass 2: Iterate SPEC-OBJECTS and extract attributes ---
    results = {}
    
    # Search for SPEC-OBJECTs (Namespaced + Fallback)
    spec_objects = root.findall(".//" + f"{REQIF_NS}SPEC-OBJECT")
    spec_objects += root.findall(".//" + "SPEC-OBJECT")  # Fallback

    for so in spec_objects:
        # Get requirement identifier (RID)
        rid = so.attrib.get("IDENTIFIER") or so.attrib.get("identifier") or "REQ-UNKNOWN"
        # Get long name/title candidate from attribute
        long_name_attr = so.attrib.get("LONG-NAME") or so.attrib.get("long-name")

        attrs = {}
        
        # Find VALUES node (Namespaced + Fallback)
        values_node = so.find(f"{REQIF_NS}VALUES") or so.find("VALUES")
        
        if values_node is not None:
            # Iterate through attribute value elements (ATTRIBUTE-VALUE-STRING, etc.)
            for av in list(values_node):
                
                # 1. Find the DEFINITION tag
                defnode = av.find(f"{REQIF_NS}DEFINITION") or av.find("DEFINITION")
                def_ref = None
                
                # a) Check for standard 'REF' attribute (most reliable)
                if defnode is not None and defnode.attrib.get("REF"):
                    def_ref = defnode.attrib.get("REF")
                
                # b) Fallback: Check for text content as the identifier (like in your provided sample.reqif)
                if not def_ref and defnode is not None and defnode.text and defnode.text.strip():
                    def_ref = defnode.text.strip()
                
                # 2. Extract the value
                value_text = None
                
                # a) Check for simple attribute value storage (e.g., STRING, INTEGER, DATE)
                value_text = av.attrib.get("THE-VALUE") or av.attrib.get("VALUE")
                
                # b) Check for rich-text content or nested child value (most robust way)
                if value_text is None or not value_text.strip():
                     # Use the new helper for robust text extraction (handles XHTML)
                     value_text = get_text_from_node(av)

                if def_ref and value_text is not None and value_text.strip():
                    # Map the found reference (ID or text) to the friendly long name
                    friendly = attr_def_map.get(def_ref, def_ref)
                    attrs[friendly] = value_text.strip()

        # 3. Map extracted attributes to common titles/descriptions
        title_candidates = ["Title", "Name", "REQ-TITLE"]
        # Added "Object Text" which is the common name for the rich-text body
        desc_candidates = ["Description", "Desc", "REQ-DESC", "Object Text"]
        
        # Default title is the Long Name attribute or the RID
        title = long_name_attr or rid 
        for cand in title_candidates:
            if cand in attrs and attrs[cand].strip():
                title = attrs[cand].strip()
                break

        description = ""
        for cand in desc_candidates:
            if cand in attrs and attrs[cand].strip():
                description = attrs[cand].strip()
                break

        results[rid] = {
            "identifier": rid,
            "title": title,
            "description": description, # Main requirement text
            "attrs": attrs, # All attributes
        }
    return results


try:
    requirements = parse_reqif(REQIF_FILE)
except Exception as e:
    print("❌ Failed to parse ReqIF file (Fatal Error):", e)
    sys.exit(1)

print(f"✅ Extracted {len(requirements)} requirements from ReqIF.")

if not requirements:
    print("⚠️ No requirements found. Exiting.")
    sys.exit(0)

# -------------------------
# 3) GitHub Issue sync (REST)
# -------------------------
def rest_request(method: str, endpoint: str, **kwargs) -> requests.Response:
    url = f"https://api.github.com{endpoint}"
    r = requests.request(method, url, headers=REST_HEADERS, **kwargs)
    if not r.ok:
        print(f"⚠️ REST API {method} {endpoint} -> {r.status_code}: {r.text}")
    return r

def list_all_issues() -> List[Dict[str, Any]]:
    issues = []
    page = 1
    while True:
        r = rest_request("GET", f"/repos/{REPO}/issues?state=all&per_page=100&page={page}")
        if not r.ok:
            break
        batch = r.json()
        if not batch:
            break
        issues.extend(batch)
        page += 1
    return issues

def find_issue_by_rid(issues, rid):
    # We store issue title as "RID: Title" to keep mapping stable
    prefix = f"{rid}:"
    for i in issues:
        if i["title"].startswith(prefix):
            return i
    return None

def create_issue_for_req(rid: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = {
        "title": f"{rid}: {info['title']}",
        "body": info["full_issue_body"],
        # Add the hard-coded label for visibility
        "labels": [HARDCODED_REQUIREMENT_LABEL]
    }
    r = rest_request("POST", f"/repos/{REPO}/issues", json=payload)
    if r.ok:
        return r.json()
    return None

def update_issue(issue_number: int, info: Dict[str, Any], state: Optional[str] = None):
    payload = {
        "title": f"{info['identifier']}: {info['title']}", 
        "body": info["full_issue_body"],
    }
    if state:
        payload["state"] = state
        
    r = rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json=payload)
    return r.ok

def close_issue(issue_number: int):
    rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json={"state": "closed"})

# --- Prepare the full issue body text combining description and attributes ---
title_candidates = ["Title", "Name", "REQ-TITLE"]
desc_candidates = ["Description", "Desc", "REQ-DESC", "Object Text"]
DEFAULT_FAILURE_BODY = "No description provided."
# Using a fixed marker helps reliably detect if the body has been updated
BODY_VERSION_MARKER = "" 

for rid, info in requirements.items():
    attrs = info.get("attrs", {})
    
    # 1. Start with the main description
    full_body = info.get("description") or ""

    attr_lines = []
    
    # 2. Build the formatted attribute list, skipping attributes already used for the main description/title
    for k, v in attrs.items():
        if v is None or not v.strip(): continue
        
        # Skip if the attribute value was used for the main title
        if k in title_candidates and v.strip() == info["title"].strip() and info["title"].strip():
            continue
            
        # Skip if the attribute value was used for the main description
        if k in desc_candidates and v.strip() == info["description"].strip() and info["description"].strip():
            continue
            
        # Skip ID which is already in the issue title
        if k.lower() == 'id' or k.lower() == 'req-id' or k == info["identifier"]:
            continue

        attr_lines.append(f"**{k}:** {v.strip()}")

    # 3. Combine description and attributes
    if attr_lines:
        # If there is a main description, separate it from the attributes with a divider
        if full_body and full_body.strip():
            full_body += "\n\n---\n### ReqIF Attributes\n"
        elif not full_body or not full_body.strip():
            full_body = "### ReqIF Attributes\n" # Start with attributes if no main description
            
        full_body += "\n".join(attr_lines)

    # Store the complete, single body string
    final_body_content = full_body or DEFAULT_FAILURE_BODY
    
    # Appending the version marker for reliable body change detection
    info["full_issue_body"] = final_body_content.strip() + "\n\n" + BODY_VERSION_MARKER
# --- END: Full Issue Body ---


# Fetch existing issues
existing_issues = list_all_issues()
existing_map_by_rid = {}
for iss in existing_issues:
    if ":" in iss["title"]:
        rid_candidate = iss["title"].split(":", 1)[0]
        existing_map_by_rid[rid_candidate] = iss

# Sync issues (create / update / reopen)
created_or_updated_issues = {}  # rid -> issue dict
for rid, info in requirements.items():
    existing = existing_map_by_rid.get(rid)
    
    new_issue_body = info["full_issue_body"]
    new_title = f"{rid}: {info['title']}"

    if existing:
        existing_body = existing.get("body") or ""
        
        # Check if an update is needed:
        title_changed = existing['title'] != new_title
        
        # Check if the body changed (compare clean bodies, ignoring the version marker)
        existing_body_clean = existing_body.replace(BODY_VERSION_MARKER, "").strip()
        new_issue_body_clean = new_issue_body.replace(BODY_VERSION_MARKER, "").strip()
        body_changed = existing_body_clean != new_issue_body_clean
        
        if title_changed or body_changed:
            
            state_to_set = None
            action_verb = "Updated"
            if existing.get("state") == "closed":
                 state_to_set = "open"
                 action_verb = "Reopened and Updated"

            # Update the issue, explicitly setting state to 'open' if it was closed.
            ok = update_issue(existing["number"], {"identifier": rid, "title": info["title"], "full_issue_body": info["full_issue_body"]}, state=state_to_set) 
            
            if ok:
                print(f"✏️ {action_verb} issue for {rid} -> #{existing['number']}")
            else:
                print(f"⚠️ Failed to update issue for {rid}")
        else:
            print(f"↩️ No change for issue {rid} -> #{existing['number']}")
            
        created_or_updated_issues[rid] = existing
    else:
        # Issue doesn't exist, create it (uses the new full_issue_body)
        created = create_issue_for_req(rid, {"title": info["title"], "full_issue_body": info["full_issue_body"]})
        if created:
            print(f"🆕 Created issue for {rid} -> #{created['number']}")
            created_or_updated_issues[rid] = created
        else:
            print(f"⚠️ Failed to create issue for {rid}")

# Close deleted issues: existing_rids not in requirements -> close
existing_rids = set(existing_map_by_rid.keys())
to_close = existing_rids - set(requirements.keys())
for rid in to_close:
    iss = existing_map_by_rid[rid]
    if iss and iss.get("state") != "closed":
        close_issue(iss["number"])
        print(f"🗑️ Closed deleted requirement issue: {rid}")

# -------------------------
# 4) Projects V2 & Field mapping (GraphQL)
# -------------------------
def run_graphql(query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GRAPHQL_URL, headers=GRAPHQL_HEADERS, json=payload)
    if r.status_code != 200:
        raise Exception(f"GraphQL error {r.status_code}: {r.text}")
    data = r.json()
    if "errors" in data:
        # print and continue where possible
        print("⚠️ GraphQL returned errors:", data["errors"])
    return data

# 4.1 get repository projects v2 and their fields
query_projects = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
    projectsV2(first:50) {
      nodes {
        id
        title
        fields(first:100) {
          nodes {
            # ProjectV2FieldConfiguration is a Union type, we need inline fragments
            ... on ProjectV2Field { id name dataType }
            ... on ProjectV2IterationField { id name dataType }
            ... on ProjectV2SingleSelectField { id name dataType }
          }
        }
      }
    }
  }
}
"""
owner, repo_name = REPO.split("/")
try:
    projects_resp = run_graphql(query_projects, {"owner": owner, "name": repo_name})
except Exception as e:
    print(f"⚠️ Failed to fetch Projects V2 (GraphQL error): {e}")
    projects_resp = {"data": {"repository": {"projectsV2": {"nodes": []}}}}


project_nodes = projects_resp.get("data", {}).get("repository", {}).get("projectsV2", {}).get("nodes", []) or []
if not project_nodes:
    print("⚠️ No Projects V2 found in repository — skipping project field mapping.")
else:
    # choose project
    project = None
    if PROJECT_NAME:
        for p in project_nodes:
            if p.get("title") == PROJECT_NAME:
                project = p
                break
    if project is None and project_nodes:
        # If PROJECT_NAME isn't specified or not found, use the first project
        project = project_nodes[0]
        
    if project:
        project_id = project["id"]
        print(f"📋 Using Project: {project.get('title')} ({project_id})")

        # build field map by normalized name
        fields = project.get("fields", {}).get("nodes", []) or []
        field_map = {f["name"].strip().lower(): f for f in fields if f.get("name")}
        # candidate field names we want to map to
        FIELD_NAME_SYSTEM_REQ_ID = "system requirement id"
        FIELD_NAME_PRIORITY = "priority"
        FIELD_NAME_LABEL = "requirement label"

        system_field = field_map.get(FIELD_NAME_SYSTEM_REQ_ID.lower())
        priority_field = field_map.get(FIELD_NAME_PRIORITY.lower())
        label_field = field_map.get(FIELD_NAME_LABEL.lower())

        # GraphQL helpers: find project item for issue (by issue number) or create one
        # Note: In a large project, this query would need pagination, but for simplicity, we assume
        # a reasonable number of items (100)
        query_project_items = """
        query($projectId: ID!) {
          node(id:$projectId) {
            ... on ProjectV2 {
              items(first:100) {
                nodes {
                  id
                  content {
                    ... on Issue {
                      number
                      id # Include ID to map issue number to node ID
                    }
                  }
                }
              }
            }
          }
        }
        """
        try:
            items_resp = run_graphql(query_project_items, {"projectId": project_id})
        except Exception as e:
             print(f"⚠️ Failed to fetch Project Items (GraphQL error): {e}")
             items_resp = {"data": {"node": {"items": {"nodes": []}}}}
             
        item_nodes = items_resp.get("data", {}).get("node", {}).get("items", {}).get("nodes", []) or []
        
        project_item_by_issue = {}
        issue_node_id_by_number = {} # Store mapping for faster item creation
        for it in item_nodes:
            cont = it.get("content")
            if cont and cont.get("number") is not None:
                project_item_by_issue[cont["number"]] = it
                issue_node_id_by_number[cont["number"]] = cont.get("id")


        # mutation to create project item (add issue to project)
        mutation_create_item = """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item {
              id
            }
          }
        }
        """
        # mutation to update field (text value)
        mutation_update_field_text = """
        mutation($project:ID!, $item:ID!, $field:ID!, $value: String!) {
          updateProjectV2ItemFieldValue(input:{
            projectId: $project,
            itemId: $item,
            fieldId: $field,
            value: { text: $value }
          }) {
            projectV2Item { id }
          }
        }
        """

        def ensure_project_item_for_issue(issue_number: int) -> Optional[str]:
            # if exists return id
            if issue_number in project_item_by_issue:
                return project_item_by_issue[issue_number]["id"]
            
            # Check if we already have the node ID from the item list
            node_id = issue_node_id_by_number.get(issue_number)
            
            # If not, fetch issue node id:
            if not node_id:
                q = "query($owner:String!, $name:String!, $num:Int!){ repository(owner:$owner, name:$name) { issue(number:$num) { id } } }"
                res = run_graphql(q, {"owner": owner, "name": repo_name, "num": issue_number})
                node_id = res.get("data", {}).get("repository", {}).get("issue", {}).get("id")
                if not node_id:
                    return None
                issue_node_id_by_number[issue_number] = node_id # Store for later

            # Create the item
            vars = {"projectId": project_id, "contentId": node_id}
            r = run_graphql(mutation_create_item, vars)
            item = r.get("data", {}).get("addProjectV2ItemById", {}).get("item")
            if item:
                item_id = item.get("id")
                # update local map
                project_item_by_issue[issue_number] = {"id": item_id}
                return item_id
            return None

        # update field helper
        def update_project_field(item_id: str, field_obj: Dict[str, Any], value: str):
            if not item_id or not field_obj or value is None:
                return
            
            # Only update if the field is a Text type (as we only have mutation for text here)
            if field_obj["dataType"] in ["TEXT", "SINGLE_SELECT"]:
                vars = {"project": project_id, "item": item_id, "field": field_obj["id"], "value": value}
                run_graphql(mutation_update_field_text, vars)
            else:
                 print(f"   ⚠️ Skipping field '{field_obj['name']}' as it is a '{field_obj['dataType']}' type (requires complex mutation).")


        # iterate created_or_updated_issues and set fields
        for rid, issue in created_or_updated_issues.items():
            issue_number = issue.get("number")
            if not issue_number:
                continue
                
            # get project item id (create if missing)
            item_id = ensure_project_item_for_issue(issue_number)
            
            if not item_id:
                print(f"⚠️ Could not find/create Project item for issue #{issue_number}")
                continue

            # get the requirement attrs from requirements dict (we have rid)
            reqinfo = requirements.get(rid, {})
            attrs = reqinfo.get("attrs", {})

            # map and update fields:
            # System Requirement ID field gets the RID (hard mapping)
            if system_field:
                update_project_field(item_id, system_field, rid)

            # Priority field from attr (if present)
            priority_val = attrs.get("Priority") or attrs.get("PRIORITY") or attrs.get("Severity") or ""
            if priority_field and priority_val:
                update_project_field(item_id, priority_field, priority_val)

            # Label field: user asked to hard-code Requirement Label to "System Requirement"
            if label_field:
                update_project_field(item_id, label_field, HARDCODED_REQUIREMENT_LABEL)

            print(f"🔧 Mapped fields for issue #{issue_number} (RID={rid})")

print("✅ Completed ReqIF → GitHub synchronization (issues + project fields).")







