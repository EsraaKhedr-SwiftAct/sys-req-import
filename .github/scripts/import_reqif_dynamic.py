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
FIELD_ID_LABEL = None 
FIELD_ID_MAP = {} # 🆕 NEW: Stores all dynamically resolved field metadata (name -> IDs)


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
            return {'errors': data['errors']}
            
        return data
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error for GraphQL: {e}")
    except Exception as e:
        print(f"❌ Error during GraphQL request: {e}")
    return {}

# Fix for retrieving the ProjectV2Item ID (PVTI_...)
def get_project_item_id(issue_node_id, project_node_id, github_token):
    """
    Queries GitHub to find the ProjectV2Item ID (PVTI_...) 
    associated with an issue's node ID (I_...) by querying the Issue's projects.
    """
    query = """
    query GetProjectItem($issueId: ID!) {
      node(id: $issueId) {
        ... on Issue {
          projectItems(first: 10) { 
            nodes {
              id # ProjectV2Item ID (PVTI_...)
              project {
                id # ProjectV2 ID (PVT_...)
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "issueId": issue_node_id
    }
    
    response = github_graphql_request(github_token, query, variables)
    
    try:
        if 'errors' in response:
            return None
            
        project_items = response.get('data', {}).get('node', {}).get('projectItems', {}).get('nodes', [])
        
        for item in project_items:
            if item.get('project', {}).get('id') == project_node_id:
                return item.get('id')
        
        return None
        
    except (KeyError, TypeError, AttributeError) as e:
        print(f"Error parsing Project Item ID response: {e}")
        return None

# -------------------------
# New Project V2 Helper: Add Issue to Project
# -------------------------
def add_issue_to_project(issue_node_id, project_node_id, github_token):
    """Adds a GitHub Issue (by Node ID) to a ProjectV2 (by Node ID)."""
    query = """
    mutation AddProjectItem($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {
        projectId: $projectId, contentId: $contentId
      }) { item { id } }
    }
    """
    variables = {
        "projectId": project_node_id,
        "contentId": issue_node_id # The Issue Node ID (I_...) is the content ID
    }
    
    response = github_graphql_request(github_token, query, variables)
    
    # Check if the project item ID was returned
    if response.get('data', {}).get('addProjectV2ItemById', {}).get('item', {}).get('id'):
        print(f"🔗 Successfully added issue ({issue_node_id}) to project.")
        return True
    else:
        print(f"❌ Failed to add issue ({issue_node_id}) to project. Check GraphQL errors above.")
        return False

# 🆕 NEW FUNCTION: Fetch all Field/Option IDs dynamically
def fetch_project_metadata(project_node_id, github_token):
    """Fetches all field IDs and Single Select Option IDs for a project."""
    global FIELD_ID_MAP
    
    query = """
    query GetProjectFields($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          fields(first: 20) { # Fetch up to 20 fields
            nodes {
              ... on ProjectV2Field {
                id
                name
              }
              ... on ProjectV2SingleSelectField {
                id
                name
                options {
                  id
                  name
                }
              }
            }
          }
        }
      }
    }
    """
    response = github_graphql_request(github_token, query, {"projectId": project_node_id})
    
    try:
        if 'errors' in response:
            raise Exception("Failed to fetch project fields metadata.")
            
        fields = response.get('data', {}).get('node', {}).get('fields', {}).get('nodes', [])
        
        for field in fields:
            field_name = field.get('name')
            field_id = field.get('id')
            
            if not field_name or not field_id:
                continue

            FIELD_ID_MAP[field_name] = {
                'id': field_id,
                'options': {}
            }
            
            # Extract options if it's a Single Select Field
            options = field.get('options')
            if options:
                for option in options:
                    FIELD_ID_MAP[field_name]['options'][option['name'].strip()] = option['id']
        
        print(f"✅ Successfully fetched metadata for {len(FIELD_ID_MAP)} project fields.")
    except Exception as e:
        print(f"❌ Failed to parse project metadata: {e}")


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
# Project Management Logic (DYNAMIC DISCOVERY)
# -------------------------
def initialize_project_ids(repo_full_name, github_token):
    """
    Dynamically finds Project and Field IDs using names.
    """
    global PROJECT_NODE_ID, FIELD_ID_REQID, FIELD_ID_PRIORITY, FIELD_ID_LABEL

    # 1. Hardcoded Project ID (Must be the Node ID, PVT_...)
    PROJECT_NODE_ID = "PVT_kwHOCWTIsM4BHvNr"

    # 2. Fetch all field metadata
    fetch_project_metadata(PROJECT_NODE_ID, github_token)

    # 3. Lookup the Field IDs by name
    FIELD_ID_REQID = FIELD_ID_MAP.get("System Requirement ID", {}).get("id")
    FIELD_ID_PRIORITY = FIELD_ID_MAP.get("Priority", {}).get("id")
    FIELD_ID_LABEL = FIELD_ID_MAP.get("Requirement Label", {}).get("id")


    if not FIELD_ID_REQID:
        print("❌ Field 'System Requirement ID' not found on the project.")
    if not FIELD_ID_PRIORITY:
        print("❌ Field 'Priority' not found on the project.")
    if not FIELD_ID_LABEL:
        print("❌ Field 'Requirement Label' not found on the project.")

    print("✅ Initialized project configuration using dynamic lookup.")
    print(f"   Project ID: {PROJECT_NODE_ID}")
    print(f"   System Requirement Field ID: {FIELD_ID_REQID}")
    print(f"   Priority Field ID: {FIELD_ID_PRIORITY}")
    print(f"   Requirement Label Field ID: {FIELD_ID_LABEL}")

    

# 🎯 FIX: Updated to handle Priority as a Single Select field using the dynamic map, AND automatically adds missing items.
def set_issue_project_fields(req, issue_node_id, github_token):
    """
    Assigns Project V2 fields by first resolving the Issue ID (I_...) to the 
    required Project V2 Item ID (PVTI_...), adding the item if it's missing.
    """
    
    # Use the Issue Node ID to find the Project V2 Item ID (PVTI_...)
    project_item_id = get_project_item_id(issue_node_id, PROJECT_NODE_ID, github_token)
    
    # 🆕 FIX: If Item ID is missing for an existing issue, add it to the project and retry lookup.
    if not project_item_id:
        print(f"🔎 Existing Issue for {req.get('id', 'Unknown Req')} is not a Project V2 item. Attempting to add...")
        
        if add_issue_to_project(issue_node_id, PROJECT_NODE_ID, github_token):
            # Try to get the item ID again after successful addition
            project_item_id = get_project_item_id(issue_node_id, PROJECT_NODE_ID, github_token)
        
        if not project_item_id:
            print(f"⚠️ Skipping project field update for {req.get('id', 'Unknown Req')}: Item still not found on Project V2 board after attempted addition.")
            return # Exit if addition and second lookup failed

    # -----------------------------------------------------------------
    # Step 2: Set "System Requirement ID" (Text Field)
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
    # We must look up the Option ID for 'System Requirement' dynamically
    req_label_data = FIELD_ID_MAP.get("Requirement Label", {})
    option_id_label = req_label_data.get("options", {}).get("System Requirement")
    
    if FIELD_ID_LABEL and option_id_label:
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
            "optionId": option_id_label 
        })
        print("-> Set 'Requirement Label' to: System Requirement")

    # -----------------------------------------------------------------
    # Step 4: Set "Priority" (Single Select Field) - WITH MAPPING
    # -----------------------------------------------------------------
    priority_text = (req.get("attributes") or {}).get("Priority") or (req.get("attributes") or {}).get("PRIORITY")

    # 🆕 Priority Mapping for robustness against data entry errors 
    # Map the value from ReqIF (key) to the exact option name in GitHub (value).
    PRIORITY_MAPPING = {
        "High": "High", 
        "Medium": "Medium",
        "Mid": "Medium",     # Map alternative terms to the official option
        "Low": "Low",
        "medium": "Medium",  # Handle lower-case input
    }
    
    # Use the mapping. If no mapping is found, use the original text as a fallback.
    mapped_priority_text = PRIORITY_MAPPING.get(priority_text, priority_text)

    priority_data = FIELD_ID_MAP.get("Priority", {})
    # Use the mapped text for option lookup
    option_id_priority = priority_data.get("options", {}).get(mapped_priority_text) 
    
    if FIELD_ID_PRIORITY and mapped_priority_text and option_id_priority:
        # Use the same Single Select mutation as for the label
        query_set_priority = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
            value: { singleSelectOptionId: $optionId }
          }) { projectV2Item { id } }
        }
        """
        github_graphql_request(github_token, query_set_priority, {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_PRIORITY,
            "optionId": option_id_priority
        })
        print(f"-> Set 'Priority' (Single Select) to: {mapped_priority_text}")
    elif FIELD_ID_PRIORITY and mapped_priority_text:
        print(f"⚠️ Priority '{mapped_priority_text}' (mapped from '{priority_text}') not found as a selectable option in the project. Please ensure the option exists in GitHub.")


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

    # --- Initialize Project IDs ---
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
            if not issue.get('node_id'):
                print(f"⚠️ Warning: Issue #{issue['number']} is missing 'node_id'. Project sync will be skipped for this issue.")

        # Create or update issues
        for req_id, req in reqs.items():
            if req_id in issue_map:
                issue = issue_map[req_id]
                # Update issue details if body or title changed
                if issue["title"] != f"[{req['id']}] {choose_title(req)}" or issue["body"] != format_req_body(req):
                    update_issue(repo_full_name, github_token, issue["number"], req)

                # Set Project Fields for existing issue (now handles missing item addition)
                if PROJECT_NODE_ID and issue.get('node_id'):
                    set_issue_project_fields(req, issue['node_id'], github_token)

            else:
                # Issue creation returns full JSON object
                new_issue_json = create_issue(repo_full_name, github_token, req)

                # Set Project Fields for new issue (handles addition implicitly within the function call)
                if PROJECT_NODE_ID and new_issue_json and new_issue_json.get('node_id'):
                    # Note: Since the issue is brand new, set_issue_project_fields will try to find the item ID,
                    # fail, and then successfully add it via add_issue_to_project, and then set the fields.
                    set_issue_project_fields(req, new_issue_json['node_id'], github_token)

        # Close removed issues
        for req_id, issue in issue_map.items():
            if req_id not in reqs:
                close_issue(repo_full_name, github_token, issue["number"], req_id)

        print("✅ Synchronization complete.")
    except Exception:
        print("❌ Unexpected error during synchronization.")
        traceback.print_exc()


if __name__ == "__main__":
    sync_reqif_to_github()