#!/usr/bin/env python3
"""
import_reqif_dynamic.py

- Parses .reqif files using the 'reqif' Python library (robust, standard-compliant).
- Creates/updates GitHub issues for each requirement.
- Finds/creates ProjectV2 items and maps attribute values to project fields via GraphQL.
- Hard-codes Requirement Label to "System Requirement" on the project field.
"""

import os
import sys
import glob
import json
import requests
import io 
import traceback # NEW: Import for detailed error logging

# NEW: Import the ReqIF library
from reqif.parser import ReqIFParser

# FIX: Robust import for ReqIFParserException. This handles multiple versions.
try:
    from reqif.parser_exception import ReqIFParserException
except ImportError:
    try:
        from reqif.reqif_exceptions import ReqIFParserException
    except ImportError:
        # Failsafe: Use a standard base exception if the library hides its custom exception.
        class ReqIFParserException(Exception):
            pass

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

# Hard-code Requirement Label mapping
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
print(f"📄 Parsing ReqIF: {REQIF_FILE} using reqif library.")


# -------------------------
# 2) ReqIF XML parsing (using 'reqif' library)
# -------------------------
def parse_reqif(path: str) -> Dict[str, Dict[str, Any]]:
    """
    Parses a ReqIF file using the 'reqif' library by passing the file path (standard approach).
    Returns mapping: { rid: { 'title': ..., 'attrs': { long_name: value, ... }, 'desc': ... } }
    """
    try:
        # FIX: Pass the file path (str) directly. This is the correct, standard usage 
        # and avoids the issues encountered with manual content or stream passing.
        reqif_bundle = ReqIFParser.parse(path) 
        
    except ReqIFParserException as e:
        # Catch library-specific parsing errors and ensure verbose output
        print(f"--- ReqIFParserException ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print("----------------------------", file=sys.stderr)
        raise Exception(f"ReqIF library failed to parse file (ReqIFParserException): {e}")
    except Exception as e:
        # Catch generic errors and ensure verbose output is directed to stderr
        print("--- GENERIC ERROR TRACEBACK ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print("-------------------------------", file=sys.stderr)
        raise Exception(f"An unexpected error occurred during ReqIF parsing: {type(e).__name__}: {e}")


    results = {}
    
    # Iterate over all specification objects found in the bundle
    for spec_object in reqif_bundle.spec_objects:
        rid = spec_object.identifier
        
        attrs = {}
        title = ""
        description = ""
        
        # Collect attributes
        for attr_value in spec_object.attribute_values:
            # The 'reqif' library automatically resolves the definition and value
            long_name = attr_value.definition.long_name or attr_value.definition.identifier
            value = attr_value.value
            
            # The library handles the correct type, we convert to string for GitHub issue body
            attrs[long_name] = str(value)

        # Map special attributes to 'title' and 'description' keys
        title_candidates = ["Title", "Name", "REQ-TITLE", "Short Description", "Requirement Text"]
        desc_candidates = ["Description", "Desc", "REQ-DESC", "Content"]
        
        # Find Title
        for cand in title_candidates:
            if cand in attrs and attrs[cand]:
                title = attrs[cand]
                # Remove from generic attributes to avoid redundancy in the body
                del attrs[cand] 
                break
        if not title:
            # Fallback to long name or identifier
            title = spec_object.long_name or rid
            
        # Find Description
        for cand in desc_candidates:
            if cand in attrs and attrs[cand]:
                description = attrs[cand]
                # Remove from generic attributes to avoid redundancy in the body
                del attrs[cand]
                break
        
        # Clean up attributes that were used as ID/Title/Description in the sample
        if "ID" in attrs: del attrs["ID"]
        if "Identifier" in attrs: del attrs["Identifier"]
        if "Requirement ID" in attrs: del attrs["Requirement ID"] 
        if "Requirement Text" in attrs and "Requirement Text" not in title_candidates: del attrs["Requirement Text"]

        
        results[rid] = {
            "identifier": rid,
            "title": title,
            "description": description,
            "attrs": attrs,
        }
    return results


try:
    requirements = parse_reqif(REQIF_FILE)
except Exception as e:
    # This exception will contain the verbose error details if the above fix works
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
        # Avoid printing full text on common GraphQL selectionMismatch warnings if possible
        error_details = r.text if 'selectionMismatch' not in r.text else "GraphQL selection errors (see log above)"
        print(f"⚠️ REST API {method} {endpoint} -> {r.status_code}: {error_details}")
    return r

def list_all_issues() -> List[Dict[str, Any]]:
    issues = []
    page = 1
    # Only fetch open issues and recently closed issues to improve performance
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

def find_issue_by_rid(issues, rid):
    # We store issue title as "RID: Title" to keep mapping stable
    prefix = f"{rid}:"
    for i in issues:
        if i["title"].startswith(prefix):
            return i
    return None

def create_issue_for_req(rid: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Prefer description, then attribute list, then fallback
    issue_body = info.get("description")
    if info.get("attrs_text"):
        # Append attributes after the main description
        issue_body = f"{issue_body}\n\n{info.get('attrs_text')}" if issue_body else info.get("attrs_text")
    issue_body = issue_body or "No description"
    
    payload = {
        "title": f"{rid}: {info['title']}",
        "body": issue_body,
        "labels": [HARDCODED_REQUIREMENT_LABEL]
    }
    r = rest_request("POST", f"/repos/{REPO}/issues", json=payload)
    if r.ok:
        return r.json()
    return None

def update_issue(issue_number: int, info: Dict[str, Any]):
    # Rebuild the full body
    issue_body = info.get("description")
    if info.get("attrs_text"):
        issue_body = f"{issue_body}\n\n{info.get('attrs_text')}" if issue_body else info.get("attrs_text")
    issue_body = issue_body or "No description"
    
    payload = {"title": f"{info['identifier']}: {info['title']}", "body": issue_body}
    r = rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json=payload)
    return r.ok

def close_issue(issue_number: int):
    rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json={"state": "closed"})

# Prepare attributes_text for issue body
for rid, info in requirements.items():
    attrs = info.get("attrs", {})
    lines = []
    
    if attrs:
        lines.append("---")
        lines.append("### ReqIF Attributes")
        for k, v in attrs.items():
            lines.append(f"**{k}:** {v}")
    
    info["attrs_text"] = "\n".join(lines)


# Fetch existing issues
existing_issues = list_all_issues()
existing_map_by_rid = {}
for iss in existing_issues:
    # try to extract rid from title "RID: title"
    if ":" in iss["title"]:
        rid_candidate = iss["title"].split(":", 1)[0]
        existing_map_by_rid[rid_candidate] = iss

# Sync issues (create / update)
created_or_updated_issues = {}  # rid -> issue dict
for rid, info in requirements.items():
    existing = existing_map_by_rid.get(rid)
    
    # Construct the full body text for comparison/update
    new_issue_body = info.get("description")
    if info.get("attrs_text"):
        new_issue_body = f"{new_issue_body}\n\n{info.get('attrs_text')}" if new_issue_body else info.get("attrs_text")
    new_issue_body = new_issue_body or "No description"
    
    new_title = f"{info['identifier']}: {info['title']}"

    if existing:
        existing_body = existing.get("body") or ""
        
        # Check if either body or title has changed
        if existing_body.strip() != new_issue_body.strip() or existing['title'] != new_title:
            ok = update_issue(existing["number"], info)
            if ok:
                print(f"✏️ Updated issue for {rid} -> #{existing['number']}")
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
def run_graphql(query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GRAPHQL_URL, headers=GRAPHQL_HEADERS, json=payload)
    if r.status_code != 200:
        # This is a critical error, stop execution
        raise Exception(f"GraphQL HTTP Error {r.status_code}: {r.text}")
    data = r.json()
    if "errors" in data:
        # print and continue where possible, but warn clearly
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
            __typename
            ... on ProjectV2Field { id name dataType }
            ... on ProjectV2SingleSelectField { id name dataType options { id name } }
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
    fields = []
    for node in project.get("fields", {}).get("nodes", []) or []:
        if node.get('__typename') in ['ProjectV2Field', 'ProjectV2SingleSelectField']:
             fields.append(node)
             
    field_map = {f["name"].strip().lower(): f for f in fields if f.get("name")}
    
    # Candidate field names we want to map to (must match your project's field titles)
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
                  id # Need issue node ID for adding item
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
    
    # map by issue number and get issue node ID
    project_item_by_issue = {}
    issue_node_id_by_number = {}
    for it in item_nodes:
        cont = it.get("content")
        if cont and cont.get("number") is not None:
            project_item_by_issue[cont["number"]] = it
            issue_node_id_by_number[cont["number"]] = cont["id"]

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
    # mutation to update Text/Number/Date field
    mutation_update_text_field = """
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
    
    # Helper to find the Option ID for Single Select fields
    def get_single_select_option_id(field_obj: Dict[str, Any], value: str) -> Optional[str]:
        if field_obj.get('dataType') == 'SINGLE_SELECT':
            options = field_obj.get('options', {}).get('nodes', [])
            for opt in options:
                # Case-insensitive match for select options
                if opt['name'].lower() == value.lower():
                    return opt['id']
        return None

    # Mutation to update Single Select field
    mutation_update_single_select = """
    mutation($project:ID!, $item:ID!, $field:ID!, $optionId: ID!) {
      updateProjectV2ItemFieldValue(input:{
        projectId: $project,
        itemId: $item,
        fieldId: $field,
        value: { singleSelectOptionId: $optionId }
      }) {
        projectV2Item { id }
      }
    }
    """

    def ensure_project_item_for_issue(issue_number: int) -> Optional[str]:
        # 1. Check if item already exists (and we have its ID)
        if issue_number in project_item_by_issue:
            return project_item_by_issue[issue_number]["id"]
            
        # 2. Get the global issue node ID (if not already fetched)
        node_id = issue_node_id_by_number.get(issue_number)
        if not node_id:
            # We must fetch the Node ID using a separate query if it wasn't in the initial batch
            q = "query($owner:String!, $name:String!, $num:Int!){ repository(owner:$owner, name:$name) { issue(number:$num) { id } } }"
            res = run_graphql(q, {"owner": owner, "name": repo_name, "num": issue_number})
            node_id = res.get("data", {}).get("repository", {}).get("issue", {}).get("id")
            if not node_id:
                print(f"⚠️ Could not find global node ID for issue #{issue_number}.")
                return None
            issue_node_id_by_number[issue_number] = node_id

        # 3. Create the item in the project
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
        if not item_id or not field_obj or not value:
            return
            
        field_type = field_obj.get("dataType")
        
        if field_type == "SINGLE_SELECT":
            option_id = get_single_select_option_id(field_obj, value)
            if option_id:
                vars = {"project": project_id, "item": item_id, "field": field_obj["id"], "optionId": option_id}
                run_graphql(mutation_update_single_select, vars)
            else:
                print(f"⚠️ Warning: Single Select field '{field_obj['name']}' has no option matching value '{value}'. Skipping update.")
        else: # TEXT, NUMBER, DATE etc.
            vars = {"project": project_id, "item": item_id, "field": field_obj["id"], "value": value}
            run_graphql(mutation_update_text_field, vars)

    # iterate created_or_updated_issues and set fields
    for rid, issue in created_or_updated_issues.items():
        issue_number = issue.get("number")
        if not issue_number:
            continue
        
        # get project item id (create if missing)
        item_id = ensure_project_item_for_issue(issue_number)
        if not item_id:
            print(f"⚠️ Could not find/create Project item for issue #{issue_number}. Skipping field sync.")
            continue

        # get the requirement attrs from requirements dict (we have rid)
        reqinfo = requirements.get(rid, {})
        attrs = reqinfo.get("attrs", {})

        # map and update fields:
        
        # System Requirement ID field gets the RID (hard mapping)
        if system_field:
            update_project_field(item_id, system_field, rid)

        # Priority field from attr (if present)
        # Check common priority/severity attribute names
        priority_val = attrs.get("Priority") or attrs.get("PRIORITY") or attrs.get("Severity") or ""
        if priority_field and priority_val:
            update_project_field(item_id, priority_field, priority_val)

        # Label field: Hard-coded Requirement Label
        if label_field:
            update_project_field(item_id, label_field, HARDCODED_REQUIREMENT_LABEL)

        print(f"🔧 Mapped fields for issue #{issue_number} (RID={rid})")

print("✅ Completed ReqIF → GitHub synchronization (issues + project fields).")
