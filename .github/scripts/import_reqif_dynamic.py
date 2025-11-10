#!/usr/bin/env python3
import os
import sys
import glob
import traceback
import requests
import html
import json

# Universal ReqIF parser
from reqif_parser_full import ReqIFParser # Must handle EA/Polarion/DOORS/Jama

# -------------------------
# Parse .reqif files
# -------------------------
def parse_reqif_requirements():
    reqif_files = glob.glob("*.reqif")
    if not reqif_files:
        print("❌ No .reqif file found in current directory.")
        sys.exit(1)

    reqif_file = reqif_files[0]
    print(f"📄 Parsing ReqIF file: {reqif_file}")

    parser = ReqIFParser(reqif_file)
    req_objects = parser.parse()

    req_dict = {}
    for i, req in enumerate(req_objects):

        # Convert object attributes
        attributes = getattr(req, "attributes", {}) or {}

        req_id = getattr(req, "identifier", None) or attributes.get("ID") or f"REQ-{i+1}"
        title = getattr(req, "title", None) or attributes.get("Title") or req_id
        description = getattr(req, "description", None) or attributes.get("Description") or "(No description found)"

        # Cleanup formatting
        req_id = req_id.strip() if req_id else f"REQ-{i+1}"
        title = title.strip() if title else req_id
        description = description.strip() if description else "(No description found)"

        title = html.unescape(title)
        description = html.unescape(description)

        # Normalize custom attributes
        normalized_attrs = {}
        for k, v in attributes.items():
            normalized_attrs[str(k).strip()] = str(v).strip() if v is not None else "(No value)"

        # Ensure required fields always exist
        normalized_attrs.setdefault("ID", req_id)
        normalized_attrs.setdefault("Title", title)
        normalized_attrs.setdefault("Description", description)

        # Include hierarchy support (children → parent)
        children = [c.identifier for c in getattr(req, "children", [])]
        parent = None
        for possible_parent in req_objects:
            if req in getattr(possible_parent, "children", []):
                parent = possible_parent.identifier
                break

        req_dict[req_id] = {
            "id": req_id,
            "title": title,
            "description": description,
            "attributes": normalized_attrs,
            "__children__": children,
            "__parent__": parent,
        }

    print(f"✅ Parsed {len(req_dict)} requirements.")
    return req_dict


# -------------------------
# GitHub helpers
# -------------------------
# FIXED: Base URL for both REST and GraphQL
GITHUB_API_URL = "https://api.github.com"

# --- GLOBAL PROJECT & GRAPHQL VARIABLES ---
PROJECT_NODE_ID = None
FIELD_ID_REQID = None
FIELD_ID_PRIORITY = None
PRIORITY_OPTIONS = {} # Map Priority text (e.g., 'High') to its Project Field Option ID


def github_headers(token):
    # FIXED: Using Bearer token for better compatibility with GraphQL and modern REST
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}


# --- NEW GRAPHQL HELPER FUNCTION ---
def github_graphql_request(token, query, variables=None):
    """Sends a request to the GitHub GraphQL API."""
    url = f"{GITHUB_API_URL}/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}
    
    try:
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        
        if 'errors' in data:
            print("❌ GraphQL Errors:", json.dumps(data['errors'], indent=2))
            # Do not raise exception here, let the caller handle failure for optional features
            return {'errors': data['errors']}
            
        return data
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error for GraphQL: {e}")
    except Exception as e:
        print(f"❌ Error during GraphQL request: {e}")
    return {}


def choose_title(req):
    req_id = str(req.get('id', '')).strip()
    title = (req.get('title') or '').strip()
    if title and title != req_id:
        return title
    desc = (req.get('description') or '').strip()
    for line in desc.splitlines():
        clean = line.strip()
        if clean and clean.upper() != req_id.upper() and len(clean.split()) >= 3:
            return clean
    return title or req_id


# -------------------------
# Improved formatting (full attribute table)
# -------------------------
def format_req_body(req):
    desc = req.get("description", "(No description found)").strip()
    attrs = req.get("attributes", {})

    ignored = {"ID", "Id", "Title", "Description"}
    other_attrs = {k: v for k, v in attrs.items() if k not in ignored}

    body = f"""**Requirement ID:** `{req.get('id', '(No ID)')}`

### 📝 Description
{desc}

### 📄 Attributes
| Attribute | Value |
|------------|--------|
| ID | {req.get('id', '(No ID)')} |
| Title | {req.get('title', '(Untitled)')} |
"""

    for k, v in other_attrs.items():
        body += f"| {k} | {v} |\n"

    return body.strip()


# -------------------------
# Project Management Logic (UPDATED FOR DYNAMIC DISCOVERY)
# -------------------------
def initialize_project_ids(repo_full_name, github_token):
    """
    Hardcoded Project and Field IDs for @Test Project.
    """
    global PROJECT_NODE_ID, FIELD_ID_REQID, FIELD_ID_PRIORITY, FIELD_ID_LABAL

    PROJECT_NODE_ID = "PVT_kwHOCWTIsM4BHvNr"

    # --- System Requirement ID field ---
    FIELD_ID_REQID = "PVTF_lAHOCWTIsM4BHvNrzg4Z25I"

    # --- Priority field ---
    FIELD_ID_PRIORITY = "PVTF_lAHOCWTIsM4BHvNrzg4a35w"
    FIELD_ID_LABAL= "PVTSSF_lAHOCWTIsM4BHvNrzg4Z25M"


    print("✅ Using hardcoded project + field configuration")
    print(f"   Project ID: {PROJECT_NODE_ID}")
    print(f"   System Requirement Field ID: {FIELD_ID_REQID}")
    print(f"   Priority Field ID: {FIELD_ID_PRIORITY}")

    

def set_issue_project_fields(req, project_item_id, github_token):
    """
    Assigns System Requirement ID, Requirement Label (single-select), and Priority fields
    for each requirement imported from ReqIF.
    """

    # -----------------------------------------------------------------
    # Step 2: Set "System Requirement ID" (Text)
    # -----------------------------------------------------------------
    sys_req_id = req.get("id") or req.get("ID")
    if FIELD_ID_REQID and sys_req_id:
        query_set_sys_id = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
            value: { text: $value }
          }) { projectV2Item { id } }
        }
        """
        github_graphql_request(github_token, query_set_sys_id, {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_REQID,
            "value": str(sys_req_id)
        })
        print(f"-> Set 'System Requirement ID' to: {sys_req_id}")

    # -----------------------------------------------------------------
    # Step 3: Set "Requirement Label" (Single Select = 'System Requirement')
    # -----------------------------------------------------------------
    if FIELD_ID_LABEL:
        query_set_label = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
            value: { singleSelectOptionId: $optionId }
          }) { projectV2Item { id } }
        }
        """
        github_graphql_request(github_token, query_set_label, {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_LABEL,
            "optionId": "ccd91893"  # <-- 'System Requirement' option ID
        })
        print("-> Set 'Requirement Label' to: System Requirement")

    # -----------------------------------------------------------------
    # Step 4: Set "Priority" (Text)
    # -----------------------------------------------------------------
    priority_text = None
    attrs = req.get("attributes")
    if isinstance(attrs, dict):
        priority_text = attrs.get("Priority") or attrs.get("PRIORITY")

    if FIELD_ID_PRIORITY and priority_text:
        query_set_priority_text = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
            value: { text: $value }
          }) { projectV2Item { id } }
        }
        """
        github_graphql_request(github_token, query_set_priority_text, {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_PRIORITY,
            "value": str(priority_text)
        })
        print(f"-> Set 'Priority' (Text) to: {priority_text}")


# -------------------------
# GitHub issue management (UPDATED URLs)
# -------------------------
def get_existing_issues(repo, token):
    # FIXED URL: Prepends /repos/
    url = f"{GITHUB_API_URL}/repos/{repo}/issues?state=all&labels=reqif-import&per_page=100"
    issues = []
    while url:
        resp = requests.get(url, headers=github_headers(token))
        resp.raise_for_status()
        issues += resp.json()
        url = resp.links.get("next", {}).get("url")
    return issues


def create_issue(repo, token, req):
    data = {
        "title": f"[{req['id']}] {choose_title(req)}",
        "body": format_req_body(req),
        "labels": ["requirement", "reqif-import"],
    }
    # FIXED URL: Prepends /repos/
    resp = requests.post(f"{GITHUB_API_URL}/repos/{repo}/issues", headers=github_headers(token), json=data)
    
    if resp.status_code >= 300:
        print(f"❌ Failed to create issue for {req['id']}: {resp.text}")
        return None
    else:
        new_issue = resp.json()
        print(f"🆕 Created issue #{new_issue['number']} for {req['id']}")
        return new_issue # Return full JSON (which contains node_id)


def update_issue(repo, token, issue_number, req):
    data = {
        "title": f"[{req['id']}] {choose_title(req)}",
        "body": format_req_body(req),
        "state": "open",
    }
    # FIXED URL: Prepends /repos/
    resp = requests.patch(f"{GITHUB_API_URL}/repos/{repo}/issues/{issue_number}", headers=github_headers(token), json=data)
    if resp.status_code >= 300:
        print(f"❌ Failed to update issue #{issue_number}: {resp.text}")
    else:
        print(f"♻️ Updated issue #{issue_number} ({req['id']})")
    return resp.json()


def close_issue(repo, token, issue_number, req_id):
    # 'repo' is the full name, e.g., 'owner/repo-name'
    
    # FIXED URL: Prepends /repos/
    url = f"{GITHUB_API_URL}/repos/{repo}/issues/{issue_number}"
    
    data = {
        "state": "closed",
        "state_reason": "not_planned" # Good practice for removed requirements
    }
    
    resp = requests.patch(url, headers=github_headers(token), json=data)
    
    if resp.status_code >= 400: # Check for 4xx errors
        print(f"❌ Failed to close issue #{issue_number} (Status: {resp.status_code}). Response: {resp.text}")
    else:
        print(f"🔒 Closed issue #{issue_number} ({req_id})")


# -------------------------
# Main synchronization (UPDATED)
# -------------------------
def sync_reqif_to_github():
    github_token = os.getenv("GITHUB_TOKEN")
    repo_full_name = os.getenv("GITHUB_REPOSITORY")
    if not github_token or not repo_full_name:
        print("❌ Missing GITHUB_TOKEN or GITHUB_REPOSITORY.")
        sys.exit(1)

    # --- NEW: Initialize Project IDs ---
    try:
        initialize_project_ids(repo_full_name, github_token)
    except Exception as e:
        print(f"❌ Project initialization failed: {e}")
        # PROJECT_NODE_ID will be None, skipping project integration
        
    try:
        reqs = parse_reqif_requirements()
        issues = get_existing_issues(repo_full_name, github_token)

        # Map existing issues by ReqIF ID
        issue_map = {}
        for issue in issues:
            title = issue.get("title", "")
            if title.startswith("[") and "]" in title:
                req_id = title.split("]")[0][1:]
                issue_map[req_id] = issue
            # Check for missing node_id (critical for project API)
            if not issue.get('node_id'):
                 print(f"⚠️ Warning: Issue #{issue['number']} is missing 'node_id'. Project sync will be skipped for this issue.")


        # Create or update issues
        for req_id, req in reqs.items():
            if req_id in issue_map:
                issue = issue_map[req_id]
                # Update issue details if body or title changed
                if issue["title"] != f"[{req['id']}] {choose_title(req)}" or issue["body"] != format_req_body(req):
                    update_issue(repo_full_name, github_token, issue["number"], req)
                
                # --- NEW: Set Project Fields for existing issue ---
                if PROJECT_NODE_ID and issue.get('node_id'):
                    set_issue_project_fields(github_token, issue['node_id'], req)
            else:
                # Issue creation returns full JSON object
                new_issue_json = create_issue(repo_full_name, github_token, req)
                
                # --- NEW: Add to Project and Set Fields for new issue ---
                if PROJECT_NODE_ID and new_issue_json and new_issue_json.get('node_id'):
                    set_issue_project_fields(github_token, new_issue_json['node_id'], req)

        # Close removed issues (Your existing, proven logic)
        for req_id, issue in issue_map.items():
            if req_id not in reqs:
                close_issue(repo_full_name, github_token, issue["number"], req_id)

        print("✅ Synchronization complete.")
    except Exception:
        print("❌ Unexpected error during synchronization.")
        traceback.print_exc()


if __name__ == "__main__":
    sync_reqif_to_github()
