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
# 2) ReqIF XML parsing (robust)
# -------------------------
def parse_reqif(path: str) -> Dict[str, Dict[str, Any]]:
    """
    Parses a ReqIF file using XML parsing with namespace handling.
    """
    ns = {}
    tree = ET.parse(path)
    root = tree.getroot()

    for k, v in root.attrib.items():
        if k.startswith("xmlns"):
            nsname = k.split(":", 1)[1] if ":" in k else ""
            ns[nsname] = v

    # Build map of ATTRIBUTE-DEFINITION IDENTIFIER -> LONG-NAME
    attr_def_map = {}  # IDENTIFIER -> LONG-NAME
    # Use the official namespace for robust finding
    REQIF_NS = "{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}"

    for ad in root.findall(f".//{REQIF_NS}ATTRIBUTE-DEFINITION-STRING") + \
              root.findall(f".//{REQIF_NS}ATTRIBUTE-DEFINITION-INTEGER") + \
              root.findall(f".//{REQIF_NS}ATTRIBUTE-DEFINITION-REAL") + \
              root.findall(f".//{REQIF_NS}ATTRIBUTE-DEFINITION-ENUMERATION"):
        
        ident = ad.attrib.get("IDENTIFIER") or ad.attrib.get("identifier")
        long_name = ad.attrib.get("LONG-NAME") or ad.attrib.get("long-name") or ad.findtext(f"{REQIF_NS}LONG-NAME") or ident
        if ident:
            attr_def_map[ident] = long_name

    # now iterate SPEC-OBJECTS
    results = {}
    spec_objects = root.findall(f".//{REQIF_NS}SPEC-OBJECT")
    if not spec_objects:
        spec_objects = root.findall(".//SPEC-OBJECT")  # fallback

    for so in spec_objects:
        rid = so.attrib.get("IDENTIFIER") or so.attrib.get("identifier") or "REQ-UNKNOWN"
        long_name = so.attrib.get("LONG-NAME") or so.attrib.get("long-name") or so.findtext(f"{REQIF_NS}LONG-NAME") or so.findtext("LONG-NAME") or rid

        attrs = {}
        values_node = so.find(f"{REQIF_NS}VALUES") or so.find("VALUES")

        if values_node is not None:
            for av in list(values_node):
                defnode = av.find(f"{REQIF_NS}DEFINITION") or av.find("DEFINITION")
                def_ref = defnode.attrib.get("REF") if defnode is not None and defnode.attrib else None
                
                # Try common locations for the value
                valnode = av.find(f"{REQIF_NS}THE-VALUE") or av.find("THE-VALUE")
                if valnode is None:
                     valnode = av.find(f"{REQIF_NS}VALUE") or av.find("VALUE")
                
                # Check for a value attribute (e.g., ATTRIBUTE-VALUE-INTEGER has a VALUE attr)
                value_text = None
                if valnode is not None:
                    value_text = valnode.text.strip() if valnode.text is not None else None
                elif av.attrib.get("VALUE"):
                    value_text = av.attrib.get("VALUE").strip()
                elif av.text and av.text.strip():
                    value_text = av.text.strip()
                
                if def_ref and value_text is not None:
                    friendly = attr_def_map.get(def_ref, def_ref)
                    attrs[friendly] = value_text

        # Map to common titles/descriptions
        title_candidates = ["Title", "Name", "REQ-TITLE", "Short Description", "Requirement Text"]
        desc_candidates = ["Description", "Desc", "REQ-DESC", "Object Text", "Content"]
        
        title = long_name
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
            "attrs": attrs, # All attributes, including the ones used for title/desc
        }
    return results


try:
    requirements = parse_reqif(REQIF_FILE)
except Exception as e:
    print("❌ Failed to parse ReqIF file:", e)
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
        "body": info["full_issue_body"], # Use the complete generated body
        "labels": [HARDCODED_REQUIREMENT_LABEL]
    }
    r = rest_request("POST", f"/repos/{REPO}/issues", json=payload)
    if r.ok:
        return r.json()
    return None

def update_issue(issue_number: int, info: Dict[str, Any], state: str):
    """
    Updates the issue content and optionally its state.
    """
    payload = {
        "title": f"{info['identifier']}: {info['title']}", 
        "body": info["full_issue_body"],
    }
    # Explicitly set the state if required (e.g., reopening)
    if state:
         payload["state"] = state
         
    r = rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json=payload)
    return r.ok

def close_issue(issue_number: int):
    rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json={"state": "closed"})

# --- START FIX FOR COMPLETE DESCRIPTION ---
# Prepare the full issue body text combining description and attributes
for rid, info in requirements.items():
    attrs = info.get("attrs", {})
    
    # 1. Start with the main description (from a mapped field like "Object Text")
    full_body = info.get("description") or ""

    attr_lines = []
    
    # Identify which attribute keys were used for the main title/description
    title_candidates = ["Title", "Name", "REQ-TITLE", "Short Description", "Requirement Text"]
    desc_candidates = ["Description", "Desc", "REQ-DESC", "Object Text", "Content"]
    
    # 2. Build the formatted attribute list, skipping attributes already used for the main description/title
    for k, v in attrs.items():
        if v is None: continue
        
        # Skip if the key was used for the main title
        if k in title_candidates and v.strip() == info["title"].strip() and info["title"].strip():
            continue
            
        # Skip if the key was used for the main description (and the description is non-empty)
        if k in desc_candidates and v.strip() == info["description"].strip() and info["description"].strip():
            continue
            
        attr_lines.append(f"**{k}:** {v}")

    # 3. Combine description and attributes
    if attr_lines:
        # If there is a main description, separate it from the attributes with a divider
        if full_body:
            full_body += "\n\n---\n### ReqIF Attributes\n"
        else:
            full_body = "### ReqIF Attributes\n" # Start with attributes if no main description
            
        full_body += "\n".join(attr_lines)

    info["full_issue_body"] = full_body or "No description provided."
# --- END FIX FOR COMPLETE DESCRIPTION ---


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
        
        # Check if an update is needed (title or body changed)
        if existing_body.strip() != new_issue_body.strip() or existing['title'] != new_title:
            
            # --- START FIX FOR REOPENING CLOSED ISSUES ---
            state_to_set = None
            action_verb = "Updated"
            if existing.get("state") == "closed":
                 state_to_set = "open"
                 action_verb = "Reopened and Updated"
            # --- END FIX FOR REOPENING CLOSED ISSUES ---

            # Update the issue, explicitly setting state to 'open' if it was closed.
            ok = update_issue(existing["number"], info, state=state_to_set) 
            
            if ok:
                print(f"✏️ {action_verb} issue for {rid} -> #{existing['number']}")
            else:
                print(f"⚠️ Failed to update issue for {rid}")
        else:
            print(f"↩️ No change for issue {rid} -> #{existing['number']}")
            
        created_or_updated_issues[rid] = existing
    else:
        created = create_issue_for_req(rid, info)
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
# ... (rest of the script for GraphQL is unchanged)

def run_graphql(query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GRAPHQL_URL, headers=GRAPHQL_HEADERS, json=payload)
    if r.status_code != 200:
        raise Exception(f"GraphQL error {r.status_code}: {r.text}")
    data = r.json()
    if "errors" in data:
        print("⚠️ GraphQL returned errors:", data["errors"])
    return data

query_projects = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
    projectsV2(first:50) {
      nodes {
        id
        title
        fields(first:100) {
          nodes {
            id
            name
            dataType
          }
        }
      }
    }
  }
}
"""
owner, repo_name = REPO.split("/")
projects_resp = run_graphql(query_projects, {"owner": owner, "name": repo_name})

project_nodes = projects_resp.get("data", {}).get("repository", {}).get("projectsV2", {}).get("nodes", []) or []
if not project_nodes:
    print("⚠️ No Projects V2 found in repository — skipping project field mapping.")
else:
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

    fields = project.get("fields", {}).get("nodes", []) or []
    field_map = {f["name"].strip().lower(): f for f in fields if f.get("name")}
    FIELD_NAME_SYSTEM_REQ_ID = "system requirement id"
    FIELD_NAME_PRIORITY = "priority"
    FIELD_NAME_LABEL = "requirement label"

    system_field = field_map.get(FIELD_NAME_SYSTEM_REQ_ID.lower())
    priority_field = field_map.get(FIELD_NAME_PRIORITY.lower())
    label_field = field_map.get(FIELD_NAME_LABEL.lower())

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
                  id 
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
    issue_node_id_by_number = {}
    for it in item_nodes:
        cont = it.get("content")
        if cont and cont.get("number") is not None:
            project_item_by_issue[cont["number"]] = it
            issue_node_id_by_number[cont["number"]] = cont.get("id")

    mutation_create_item = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item {
          id
        }
      }
    }
    """
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
        if issue_number in project_item_by_issue:
            return project_item_by_issue[issue_number]["id"]
            
        node_id = issue_node_id_by_number.get(issue_number)
        if not node_id:
            q = "query($owner:String!, $name:String!, $num:Int!){ repository(owner:$owner, name:$name) { issue(number:$num) { id } } }"
            res = run_graphql(q, {"owner": owner, "name": repo_name, "num": issue_number})
            node_id = res.get("data", {}).get("repository", {}).get("issue", {}).get("id")
            if not node_id:
                return None
            issue_node_id_by_number[issue_number] = node_id

        vars = {"projectId": project_id, "contentId": node_id}
        r = run_graphql(mutation_create_item, vars)
        item = r.get("data", {}).get("addProjectV2ItemById", {}).get("item")
        if item:
            item_id = item.get("id")
            project_item_by_issue[issue_number] = {"id": item_id}
            return item_id
        return None

    def update_project_field(item_id: str, field_obj: Dict[str, Any], value: str):
        if not item_id or not field_obj or value is None:
            return
        vars = {"project": project_id, "item": item_id, "field": field_obj["id"], "value": value}
        run_graphql(mutation_update_field, vars)

    for rid, issue in created_or_updated_issues.items():
        issue_number = issue.get("number")
        if not issue_number:
            continue
            
        item_id = ensure_project_item_for_issue(issue_number)
        if not item_id:
            print(f"⚠️ Could not find/create Project item for issue #{issue_number}")
            continue

        reqinfo = requirements.get(rid, {})
        attrs = reqinfo.get("attrs", {})

        if system_field:
            update_project_field(item_id, system_field, rid)

        priority_val = attrs.get("Priority") or attrs.get("PRIORITY") or attrs.get("Severity") or ""
        if priority_field and priority_val:
            update_project_field(item_id, priority_field, priority_val)

        if label_field:
            update_project_field(item_id, label_field, HARDCODED_REQUIREMENT_LABEL)

        print(f"🔧 Mapped fields for issue #{issue_number} (RID={rid})")

print("✅ Completed ReqIF → GitHub synchronization (issues + project fields).")


