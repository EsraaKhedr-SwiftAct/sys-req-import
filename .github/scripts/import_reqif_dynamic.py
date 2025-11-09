#!/usr/bin/env python3
import os
import sys
import glob
import traceback
import requests
import html
import json # <--- NEW IMPORT for GraphQL error handling

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
            raise Exception("GitHub GraphQL API returned errors.")
            
        return data
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error for GraphQL: {e}")
        raise
    except Exception as e:
        print(f"❌ Error during GraphQL request: {e}")
        # Allows caller to continue if project integration fails
        pass
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
# Project Management Logic (NEW)
# -------------------------
def initialize_project_ids(repo_full_name, github_token):
    """
    Retrieves the Node IDs for the first Project associated with the repository 
    and its required custom fields ('Priority', 'System Requirement ID').
    """
    global PROJECT_NODE_ID, FIELD_ID_REQID, FIELD_ID_PRIORITY, PRIORITY_OPTIONS
    owner, repo = repo_full_name.split('/')
    
    # GraphQL query to get the repository's projects and their fields
    query = """
    query($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        id
        projectsV2(first: 20) {
          nodes {
            id
            title
            fields(first: 20) {
              nodes {
                ... on ProjectV2Field { id, name }
                ... on ProjectV2SingleSelectField { id, name, options { id, name } } 
              }
            }
          }
        }
      }
    }
    """
    
    data = github_graphql_request(github_token, query, {"owner": owner, "repo": repo})
    if not data or 'data' not in data or not data['data']['repository']:
        print("⚠️ Failed to query repository projects. Skipping project integration.")
        return

    projects = data['data']['repository']['projectsV2']['nodes']
    
    if not projects:
        print("⚠️ No projects found associated with this repository. Skipping project integration.")
        return

    # Use the first project found (Dynamic Selection)
    project = projects[0]
    PROJECT_NODE_ID = project['id']
    print(f"✅ Found Project '{project['title']}' with ID: {PROJECT_NODE_ID} (Using first project found).")
            
    for field in project['fields']['nodes']:
        if field['name'] == 'System Requirement ID':
            FIELD_ID_REQID = field['id']
            print(f"✅ Mapped 'System Requirement ID' field ID: {FIELD_ID_REQID}")
        
        elif field['name'] == 'Priority':
            FIELD_ID_PRIORITY = field['id']
            print(f"✅ Mapped 'Priority' field ID: {FIELD_ID_PRIORITY}")
            for option in field.get('options', []):
                PRIORITY_OPTIONS[option['name']] = option['id']
            print(f"   Collected {len(PRIORITY_OPTIONS)} Priority options.")
            
    if not FIELD_ID_REQID or not FIELD_ID_PRIORITY:
        print("⚠️ Warning: One or both custom fields ('System Requirement ID', 'Priority') were not found in the project. Field mapping will be incomplete.")


def set_issue_project_fields(github_token, issue_node_id, req):
    """Adds the issue to the project and sets custom fields."""
    if not PROJECT_NODE_ID:
        return
        
    # --- Step 1: Add Issue to Project (Returns the ProjectV2Item ID) ---
    query_add_item = """
    mutation($projectId: ID!, $issueId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $issueId}) {
        item { id }
      }
    }
    """
    project_item_id = None
    try:
        data = github_graphql_request(github_token, query_add_item, {
            "projectId": PROJECT_NODE_ID, 
            "issueId": issue_node_id
        })
        # If the item was already present, this mutation may fail.
        # Handling item ID retrieval after adding is complex, but we rely on the mutation to succeed.
        project_item_id = data['data']['addProjectV2ItemById']['item']['id']
        print(f"-> Added issue to project. Project Item ID: {project_item_id}")
    except Exception as e:
        # If it fails (likely already in project), we skip the field update to prevent further errors
        print(f"-> ⚠️ Failed to add item to project (Status: {e}). Fields will not be set.")
        return

    # --- Step 2: Set 'System Requirement ID' (Text field) ---
    req_id_value = req['id']
    if FIELD_ID_REQID and project_item_id:
        query_set_text = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId, 
            value: { text: $value }
          }) { projectV2Item { id } }
        }
        """
        try:
            github_graphql_request(github_token, query_set_text, {
                "projectId": PROJECT_NODE_ID, 
                "itemId": project_item_id, 
                "fieldId": FIELD_ID_REQID, 
                "value": req_id_value
            })
            print(f"-> Set 'System Requirement ID' to: {req_id_value}")
        except Exception:
            print(f"-> ❌ Failed to set 'System Requirement ID' field.")

    # --- Step 3: Set 'Priority' (Single Select field) ---
    priority_text = req['attributes'].get('Priority') or req['attributes'].get('PRIORITY')
    
    if FIELD_ID_PRIORITY and priority_text and priority_text in PRIORITY_OPTIONS and project_item_id:
        option_id = PRIORITY_OPTIONS[priority_text]
        query_set_single_select = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId, 
            value: { singleSelectOptionId: $optionId }
          }) { projectV2Item { id } }
        }
        """
        try:
            github_graphql_request(github_token, query_set_single_select, {
                "projectId": PROJECT_NODE_ID, 
                "itemId": project_item_id, 
                "fieldId": FIELD_ID_PRIORITY, 
                "optionId": option_id
            })
            print(f"-> Set 'Priority' to: {priority_text}")
        except Exception:
            print(f"-> ❌ Failed to set 'Priority' field.")
    elif FIELD_ID_PRIORITY and priority_text:
        print(f"-> ⚠️ ReqIF Priority '{priority_text}' not found as a valid Project Option. Skipping Priority set.")

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
