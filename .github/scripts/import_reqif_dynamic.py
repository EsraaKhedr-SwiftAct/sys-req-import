#!/usr/bin/env python3
"""
import_reqif_dynamic.py

- Parses .reqif files (robust XML-based parser; supports common namespaces)
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
# 2) ReqIF XML parsing (FINAL ROBUST VERSION)
# -------------------------
def parse_reqif(path: str) -> Dict[str, Dict[str, Any]]:
    """
    Returns mapping: { rid: { 'title': ..., 'attrs': { long_name: value, ... }, 'description': ... } }
    Uses XML parsing with explicit namespace fallback for maximum compatibility.
    """
    # Register default namespace for robust parsing using standard ReqIF namespace
    REQIF_NS = "{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}"
    
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except ET.ParseError as e:
        # For a compressed ReqIFZ, ET.parse won't work, but this file is .reqif
        print(f"❌ Failed to parse XML file: {e}")
        return {}


    # 1. Build map of ATTRIBUTE-DEFINITION IDENTIFIER -> LONG-NAME
    attr_def_map = {}  # IDENTIFIER -> LONG-NAME
    
    # Use XPath to find all definition types, ignoring namespaces for robustness (././ is necessary to find nested)
    attr_defs = root.findall(".//" + f"{REQIF_NS}ATTRIBUTE-DEFINITION-STRING")
    attr_defs += root.findall(".//" + "ATTRIBUTE-DEFINITION-STRING") # Fallback non-namespaced
    
    for ad in attr_defs:
        ident = ad.attrib.get("IDENTIFIER") or ad.attrib.get("identifier")
        long_name = ad.attrib.get("LONG-NAME") or ad.attrib.get("long-name")
        
        # If no LONG-NAME attribute, try child element text
        if not long_name:
             long_name = ad.findtext(f"{REQIF_NS}LONG-NAME")
        if not long_name:
             long_name = ad.findtext("LONG-NAME") # Fallback non-namespaced
        
        # Use IDENTIFIER as fallback for LONG-NAME
        long_name = long_name or ident 

        if ident:
            attr_def_map[ident] = long_name

    # 2. Iterate SPEC-OBJECTS
    results = {}
    spec_objects = root.findall(".//" + f"{REQIF_NS}SPEC-OBJECT")
    spec_objects += root.findall(".//" + "SPEC-OBJECT") # Fallback non-namespaced

    for so in spec_objects:
        # Get requirement identifier (RID)
        rid = so.attrib.get("IDENTIFIER") or so.attrib.get("identifier") or "REQ-UNKNOWN"
        # Get long name/title candidate from SPEC-OBJECT attribute
        long_name_attr = so.attrib.get("LONG-NAME") or so.attrib.get("long-name")

        attrs = {}
        
        # Find VALUES node (Crucial fix is here to ensure one is found reliably)
        values_node = so.find(f"{REQIF_NS}VALUES")
        if values_node is None:
            values_node = so.find("VALUES")
        
        if values_node is not None:
            for av in list(values_node):
                # 1. Find the DEFINITION tag to get the attribute ID
                defnode = av.find(f"{REQIF_NS}DEFINITION")
                if defnode is None:
                    defnode = av.find("DEFINITION")
                
                def_ref = None
                if defnode is not None and defnode.text and defnode.text.strip():
                    # Your file uses the text content as the ID
                    def_ref = defnode.text.strip()
                elif defnode is not None and defnode.attrib.get("REF"):
                    # Standard ReqIF uses the REF attribute
                    def_ref = defnode.attrib.get("REF")

                # 2. Extract the value from THE-VALUE attribute (Your file's style)
                value_text = av.attrib.get("THE-VALUE") 
                
                # 3. Fallback for other common attribute styles
                if value_text is None:
                    value_text = av.attrib.get("VALUE")
                
                # 4. Fallback for child element text (e.g., Xhtml/rich text)
                if value_text is None:
                    valnode = av.find(f"{REQIF_NS}THE-VALUE")
                    if valnode is None:
                         valnode = av.find("THE-VALUE")
                    
                    if valnode is not None and valnode.text is not None:
                        value_text = valnode.text.strip()

                if def_ref and value_text is not None and value_text:
                    friendly = attr_def_map.get(def_ref, def_ref)
                    attrs[friendly] = value_text.strip()

        # 3. Map to common titles/descriptions
        title_candidates = ["Title", "Name", "REQ-TITLE"]
        desc_candidates = ["Description", "Desc", "REQ-DESC", "Object Text"]
        
        # Start with long_name_attr as the default title
        title = long_name_attr or rid 
        for cand in title_candidates:
            if cand in attrs and attrs[cand].strip():
                title = attrs[cand].strip()
                break

        description = ""
        # IMPORTANT: Iterate through candidates and set the description using the first one found
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
        # print(f"⚠️ REST API {method} {endpoint} -> {r.status_code}: {r.text}") # suppressed to avoid excessive output
        pass
    return r

def list_all_issues() -> List[Dict[str, Any]]:
    issues = []
    page = 1
    # Fetch ALL issues to find closed ones that need reopening/updating
    while True:
        r = rest_request("GET", f"/repos/{REPO}/issues?state=all&per_page=100&page={page}&sort=updated")
        if not r.ok:
            break
        batch = r.json()
        if not batch:
            break
        issues.extend(batch)
        page += 1
    return issues

def create_issue_for_req(rid: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = {
        "title": f"{rid}: {info['title']}",
        "body": info["full_issue_body"],
        "labels": [HARDCODED_REQUIREMENT_LABEL]
    }
    r = rest_request("POST", f"/repos/{REPO}/issues", json=payload)
    if r.ok:
        return r.json()
    return None

def update_issue(issue_number: int, info: Dict[str, Any], state: Optional[str] = None):
    """
    Updates the issue content and optionally its state (e.g., to 'open').
    """
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
DEFAULT_FAILURE_BODY = "No description provided." # Key message to check for force-update
# V1.2.5: New marker for final robust parser fix (force update)
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
        if k.lower() == 'id' or k.lower() == 'req-id':
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
    
    # Appending a unique, hidden comment ensures the body changes and forces the update this one time.
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
        
        # Check if the body changed (using the new, unique marker will force this to be true)
        # We need to compare the cleaned version to handle markdown differences, but rely on marker for initial force
        existing_body_clean = existing_body.replace(BODY_VERSION_MARKER, "").strip()
        new_issue_body_clean = new_issue_body.replace(BODY_VERSION_MARKER, "").strip()
        body_changed = existing_body_clean != new_issue_body_clean
        
        # Force update if the old marker (or any prior marker) is missing, and the new body is not the default failure body.
        # This handles the case where the previous run failed to update the content, and forces the new content.
        force_update = BODY_VERSION_MARKER not in existing_body and new_issue_body_clean != DEFAULT_FAILURE_BODY

        if title_changed or body_changed or force_update:
            
            state_to_set = None
            action_verb = "Updated"
            if existing.get("state") == "closed":
                 state_to_set = "open"
                 action_verb = "Reopened and Updated"

            # Update the issue, explicitly setting state to 'open' if it was closed.
            ok = update_issue(existing["number"], {"identifier": rid, "title": info["title"], "full_issue_body": info["full_issue_body"]}, state=state_to_set) 
            
            if ok:
                # The log should now show this message
                print(f"✏️ {action_verb} issue for {rid} -> #{existing['number']} (FORCE APPLIED)")
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
# (GraphQL logic is unchanged from previous working versions, omitted for brevity)
# (It will execute if your project is configured)
# ... [Unchanged GraphQL logic to follow] ...

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
    if project is None:
        project = project_nodes[0]
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
    query_project_items = """
    query($projectId: ID!, $perPage:Int!) {
      node(id:$projectId) {
        ... on ProjectV2 {
          items(first:$perPage) {
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
    items_resp = run_graphql(query_project_items, {"projectId": project_id, "perPage": 100})
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
    # mutation to update field
    mutation_update_field = """
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
    
    # update field helper
    def update_project_field(item_id: str, field_obj: Dict[str, Any], value: str):
        if not item_id or not field_obj or value is None:
            return
        
        # We assume the fields we are setting (ReqID, Priority, Label) are text-based for now.
        vars = {"project": project_id, "item": item_id, "field": field_obj["id"], "value": value}
        run_graphql(mutation_update_field_text, vars)


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