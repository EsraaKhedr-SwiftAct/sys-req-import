#!/usr/bin/env python3
import os
import sys
import glob
import traceback
import requests
import html
import json
from collections import defaultdict
import re # <-- ADDED: Necessary for regular expression cleaning

# Universal ReqIF parser
# NOTE: This script assumes 'reqif_parser_full' is available in the Python environment path.
from reqif_parser_full import ReqIFParser 

# Always save config file in repository root, not script folder
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
CONFIG_FILE = os.path.join(REPO_ROOT, "reqif_config.json")

# --- NEW: GLOBAL DRY RUN FLAG ---
IS_DRY_RUN = False 
# ---------------------------------

def load_config():
    """Loads the requirement configuration from a JSON file."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            try:
                # Use a default structure if file is empty or malformed
                config = json.load(f)
                config.setdefault("attributes", {})
                return config
            except json.JSONDecodeError:
                print("⚠️ Warning: Could not decode existing config file. Starting fresh.")
                return {"attributes": {}}
    return {"attributes": {}}

def save_config(config):
    """Saves the requirement configuration to a JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4, sort_keys=True)
    print(f"✅ Updated {CONFIG_FILE}. **MANUAL ACTION REQUIRED:** Review and commit this file to apply schema changes.")

# -------------------------
# Schema Management (PATCHED)
# -------------------------
def perform_schema_detection(reqif_attrs):
    """Detects new attributes and the intrinsic ReqIF Type, updating the config file using normalized keys."""
    config = load_config()
    config_attrs = config.setdefault("attributes", {})
    new_attr_found = False

    REQ_TYPE_KEY = "__REQ_TYPE__"

    # Normalize existing config keys
    normalized_config = {str(k).strip(): v for k, v in config_attrs.items()}
    config_attrs.clear()
    config_attrs.update(normalized_config)

    # Track attributes in config but not in current ReqIF
    config_keys = set(config_attrs.keys())

    # 🆕 HANDLE THE INTRINSIC REQUIREMENT TYPE FIRST
    if REQ_TYPE_KEY in reqif_attrs and REQ_TYPE_KEY not in config_attrs:
        config_attrs[REQ_TYPE_KEY] = {
            "include_in_body": True, # Default to TRUE: visible in the issue body
            "description": "Intrinsic ReqIF Type (Spec Object Type). Set 'include_in_body' to false to hide it in the issue body.",
        }
        new_attr_found = True
        print(f"📢 Detected intrinsic ReqIF Type. Added '{REQ_TYPE_KEY}' to config.")
    
    # Handle all other custom attributes (existing logic)
    for attr_name in sorted(reqif_attrs):
        key = str(attr_name).strip()  # normalized key
        if key == REQ_TYPE_KEY:
            continue # Skip, already handled above
            
        if key in ("ID", "Title", "Description", "Text"):
            continue  # never add core fields to config

        # Skip hierarchy fields if they are not present in the current ReqIF (preserving original logic)
        if key in ("__parent__", "__children__") and key not in reqif_attrs:
            continue

        if key not in config_attrs:
            config_attrs[key] = {
                "include_in_body": True,
                "description": (
                    "Auto-detected attribute. Change 'include_in_body' to false "
                    "to hide in GitHub issue body."
                ),
            }
            new_attr_found = True
            print(f"📢 Detected new normalized attribute: '{key}'. Added to config.")

        config_keys.discard(key)

    # Remove attributes from config no longer in ReqIF
    for attr_name in list(config_keys):
        print(f"🗑️ Removing obsolete attribute from config: '{attr_name}'")
        config_attrs.pop(attr_name, None)

    # Save config after normalization
    if IS_DRY_RUN and (new_attr_found or normalized_config):
        save_config(config)


# -------------------------
# Parse .reqif files (PATCHED)
# -------------------------
def parse_reqif_requirements():
    # --- FIX: Search in the repo root (../../) AND the current directory ---
    repo_root = "../../"
    reqif_files = glob.glob(os.path.join(repo_root, "*.reqif")) + \
                  glob.glob(os.path.join(repo_root, "*.reqifz"))
    
    # Fallback to current directory search (for local testing flexibility)
    if not reqif_files:
        reqif_files = glob.glob("*.reqif") + glob.glob("*.reqifz")
    
    if not reqif_files:
        print("❌ No .reqif or .reqifz file found in current directory OR in the repo root (../../).") 
        sys.exit(1)
    
    reqif_file = reqif_files[0]
    print(f"📄 Parsing ReqIF file: {reqif_file}")

    parser = ReqIFParser(reqif_file)
    req_objects = parser.parse()

    req_dict = {}
    all_unique_attrs = set() # 🆕 Set to track all unique attribute names

    for i, req in enumerate(req_objects):

        # Convert object attributes
        attributes = getattr(req, "attributes", {}) or {}

        # 🟢 EXTRACT REQ TYPE: Robustly find the type name (Spec Object Type)
        req_type = getattr(req, "type_name", None) or \
                   getattr(req, "type", None) or \
                   getattr(req, "spec_object_type_name", "System Requirement") 
        # ----------------------------------------------------

        req_id = getattr(req, "identifier", None) or attributes.get("ID") or f"REQ-{i+1}"
        title = getattr(req, "title", None) or attributes.get("Title") or req_id
        description = getattr(req, "description", None) or attributes.get("Description") or "(No description found)"

        # Cleanup formatting
        req_id = req_id.strip() if req_id else f"REQ-{i+1}"
        title = title.strip() if title else req_id
        description = description.strip() if description else "(No description found)"

        title = html.unescape(title)
        description = html.unescape(description)

        # FIX 2: Replace non-breaking space (\xa0) with a regular space to prevent API errors
        if isinstance(title, str):
            title = title.replace('\xa0', ' ')
        if isinstance(description, str):
            description.replace('\xa0', ' ')

        # Normalize custom attributes and track unique names
        normalized_attrs = {}
        for k, v in attributes.items():
            key = str(k).strip()
            # FIX: Skip core fields here for better config management
            if key in ("ID", "Title", "Description"): 
                continue
                
            normalized_attrs[key] = str(v).strip() if v is not None else "(No value)"
            all_unique_attrs.add(key) # 🆕 Track unique attribute key

        # Extract hierarchy data
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
            "type": req_type, # <-- Stored Type (ADDED)
            "attributes": normalized_attrs,
            "__children__": children,
            "__parent__": parent,
        }

    print(f"✅ Parsed {len(req_dict)} requirements.")

    # --- Add hierarchy attributes only if SPEC-HIERARCHY exists ---
    has_hierarchy = any(getattr(req, "children", []) for req in req_objects)
    if has_hierarchy:
        all_unique_attrs.add("__children__")
        all_unique_attrs.add("__parent__")

    # 🆕 ADD the intrinsic Requirement Type key to the set of attributes for detection
    all_unique_attrs.add("__REQ_TYPE__") 

    # Run schema detection after parsing
    perform_schema_detection(all_unique_attrs)


    return req_dict


# -------------------------
# GitHub helpers
# -------------------------
GITHUB_API_URL = "https://api.github.com"

# --- GLOBAL PROJECT & GRAPHQL VARIABLES ---
PROJECT_NODE_ID = None
FIELD_ID_REQID = None
FIELD_ID_PRIORITY = None
FIELD_ID_LABEL = None 
FIELD_ID_STATUS = None # 🆕 ADDED: Global variable for the Status Field ID
FIELD_ID_MAP = {} 

# ---------------------------------


def github_headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}


# --- NEW GRAPHQL HELPER FUNCTION ---
def github_graphql_request(token, query, variables=None):
    """Sends a request to the GitHub GraphQL API."""
    url = f"{GITHUB_API_URL}/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables or {} }
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
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
              id 
              project {
                id 
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
            # Check if the item belongs to the specific project ID
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
        "contentId": issue_node_id 
    }
    
    response = github_graphql_request(github_token, query, variables)
    
    if response.get('data', {}).get('addProjectV2ItemById', {}).get('item', {}).get('id'):
        print(f"🔗 Successfully added issue ({issue_node_id}) to project.")
        return True
    else:
        # Avoid printing full error text unless required, usually covered by github_graphql_request
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
          fields(first: 20) { 
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
        
        FIELD_ID_MAP.clear() # Clear map before population
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
        # Find a clean line that is not the ID and has at least 3 words
        if clean and clean.upper() != req_id.upper() and len(clean.split()) >= 3:
            return clean
    return title or req_id


# -------------------------
# Improved formatting (full attribute table using config) (PATCHED)
# -------------------------
def format_req_body(req):
    
    config = load_config() 
    config_attrs = config.get("attributes", {})
    
    # --- 🆕 PHASE 1: Conditionally Add Intrinsic ReqIF Type (Spec Object Type) ---
    body = []
    REQ_TYPE_KEY = "__REQ_TYPE__"
    req_type_config = config_attrs.get(REQ_TYPE_KEY)
    
    # Check if the user configured 'include_in_body: true' for the ReqIF Type
    if req_type_config and req_type_config.get("include_in_body"):
        req_type_value = req.get('type') # 'type' is now stored in the req dict
        if req_type_value:
            # Prepend the Req Type clearly at the top
            body.append(f"**ReqIF Type:** `{req_type_value}`") 
            body.append("---") # Separator
    
    # Initialize the main issue body string from the list
    body_str = "\n".join(body)
    # --------------------------------------------------------------------------
    
    # Define core fields and internal fields that might appear in the attributes dictionary
    # and whose display is controlled by the config.
    CORE_FIELDS = {"ID", "Title", "Description"}
    
    # 1. Check config for the core fields
    # Defaulting to True for backward compatibility if the attribute is missing from config
    show_description = config_attrs.get("Description", {}).get("include_in_body", True)
    show_id = config_attrs.get("ID", {}).get("include_in_body", True)
    show_title = config_attrs.get("Title", {}).get("include_in_body", True)
 
     # NEW → global description flag
    include_description = config.get("include_description", True)
    desc = req.get("description", "(No description found)").strip()
    
    # --- FIX: Clean description of Priority mentions before display ---
    # This regex removes patterns like "Priority: High", "priority=medium", etc., usually found on a single line.
    desc = re.sub(r'(?:\s*|^\s*)[Pp]riority\s*[:=]\s*[^\n]+', '', desc).strip()
    # -----------------------------------------------------------------
    
    attrs = req.get("attributes", {})
    
    # Append the Requirement ID tracking line
    body_str += f"\n\n**Requirement ID:** `{req.get('id', '(No ID)')}`\n\n"

    # 2. Conditionally add the Description section
    if include_description and show_description :
        body_str += f"### 📝 Description\n{desc}\n\n"

        
    # 3. Build the Attributes list dynamically based on configuration
    
    # Map the core fields to their values for easy lookup
    CORE_VALUES = {
        "ID": req.get('id', '(No ID)'),
        "Title": req.get('title', '(Untitled)')
    }
    
    table_lines = ["### 📄 Attributes", "| Attribute | Value |", "|------------|--------|"]

    # Check and add primary fields (ID, Title) first if configured to show
    if show_id :
        table_lines.append(f"| ID | {CORE_VALUES['ID']} |")
    if show_title :
        table_lines.append(f"| Title | {CORE_VALUES['Title']} |")


    filtered_attrs = {}
    for k, v in attrs.items():
        attr_config = config_attrs.get(k, {})
        # Only include if config says true
        if attr_config.get("include_in_body", True): # Default is True for custom attrs
            filtered_attrs[k] = v
    # -------------------------------------------

    # Build attributes table
    for k, v in filtered_attrs.items():
        # Skip core fields completely
        if k in CORE_FIELDS:
            continue
            
        # 🆕 Skip the intrinsic type key to prevent redundant display in the table
        if k == REQ_TYPE_KEY:
            continue

        # EXTRA FIX: Ensure Description never appears again in the attributes table
        if k.lower() == "description":
            continue

        safe_v = str(v).replace("\n", " ").replace("|", "\\|")
        table_lines.append(f"| {k} | {safe_v} |")

    # Only append the table if there is content beyond the headers (3 lines)
    if len(table_lines) > 3:
        body_str += "\n".join(table_lines)
    else:
        body_str += "### 📄 Attributes\n(No attributes configured to display.)"


    return body_str.strip()


# -------------------------
# GitHub issue management
# -------------------------
def get_existing_issues(repo, token):
    # Filter issues using ONLY the 'System Requirement' label.
    url = f"{GITHUB_API_URL}/repos/{repo}/issues?state=all&labels=System Requirement&per_page=100"
    issues = []
    while url:
        resp = requests.get(url, headers=github_headers(token))
        resp.raise_for_status()
        issues += resp.json()
        url = resp.links.get("next", {}).get("url")
    return issues


def map_req_to_issue(issues):
    """Maps requirement IDs to existing issues using the unique issue body format."""
    mapping = {}
    pattern = re.compile(r"\*\*Requirement ID:\*\* `([^`]+)`")
    for issue in issues:
        match = pattern.search(issue.get('body', ''))
        if match:
            req_id = match.group(1).strip()
            # Store issue data including the node_id for Project V2 operations
            mapping[req_id] = {
                "number": issue["number"],
                "node_id": issue["node_id"]
            }
    return mapping


def update_issue(repo, token, issue_number, req):
    """Updates an existing GitHub issue with new content, title, and ensures the label is set."""
    global IS_DRY_RUN
    
    new_title = choose_title(req)
    new_body = format_req_body(req)
    
    if IS_DRY_RUN:
        print(f"⏩ SKIPPED: Issue #{issue_number} ({req['id']}) update skipped (Dry Run Mode).")
        print(f"   -> New Title: {new_title}")
        print(f"   -> New Body: \n{new_body[:200]}...")
        # Return a mock object (or original issue data if available) to allow subsequent logic
        return {"number": issue_number, "node_id": f"I_kwDO_{issue_number}_DRY"}
    
    url = f"{GITHUB_API_URL}/repos/{repo}/issues/{issue_number}"
    data = {
        "title": new_title,
        "body": new_body,
        "labels": ["System Requirement"],
        "state": "open" # Ensure issue is open if it was closed
    }

    resp = requests.patch(url, headers=github_headers(token), json=data)

    if resp.status_code >= 400:
        print(f"❌ Failed to update issue #{issue_number} (Status: {resp.status_code}). Response: {resp.text}")
        return None
    else:
        print(f"⬆️ Updated issue #{issue_number} ({req['id']})")
        return resp.json() # Return the updated issue data


def create_issue(repo, token, req):
    """Creates a new GitHub issue for a requirement."""
    global IS_DRY_RUN
    
    new_title = choose_title(req)
    new_body = format_req_body(req)
    
    if IS_DRY_RUN:
        print(f"⏩ SKIPPED: Issue creation for {req['id']} skipped (Dry Run Mode).")
        # Return a mock object with node_id to allow subsequent logic (like set_issue_project_fields)
        return {"number": 99999, "node_id": f"I_kwDO_DRY_RUN_{req['id']}", "title": new_title, "body": new_body}
        
    url = f"{GITHUB_API_URL}/repos/{repo}/issues"
    data = {
        "title": new_title,
        "body": new_body,
        "labels": ["System Requirement"],
    }

    resp = requests.post(url, headers=github_headers(token), json=data)

    if resp.status_code >= 400:
        print(f"❌ Failed to create issue for {req['id']} (Status: {resp.status_code}). Response: {resp.text}")
        return None
    else:
        issue_number = resp.json().get('number')
        print(f"➕ Created issue #{issue_number} for {req['id']}")
        return resp.json()


def set_issue_project_fields(req, issue_node_id, github_token):
    """Sets the custom fields (Req ID, Priority, Label, Status) for an issue in the Project V2 board."""
    global PROJECT_NODE_ID, FIELD_ID_REQID, FIELD_ID_PRIORITY, FIELD_ID_LABEL, FIELD_ID_STATUS, IS_DRY_RUN
    
    if IS_DRY_RUN:
        print(f"⏩ SKIPPED: Project field update for {req.get('id', 'Unknown Req')} skipped (Dry Run Mode).")
        return

    # --- Step 1: Ensure the Issue is on the Project Board ---
    project_item_id = get_project_item_id(issue_node_id, PROJECT_NODE_ID, github_token)

    if not project_item_id:
        print(f"⚠️ Issue ({req.get('id', 'Unknown Req')}) not found on Project V2 board. Attempting to add...")
        if add_issue_to_project(issue_node_id, PROJECT_NODE_ID, github_token):
            # Try to get the item ID again after successful addition
            project_item_id = get_project_item_id(issue_node_id, PROJECT_NODE_ID, github_token)
            if not project_item_id:
                print(f"⚠️ Skipping project field update for {req.get('id', 'Unknown Req')}: Item still not found on Project V2 board after attempted addition.")
                return # Exit if addition and second lookup failed
        else:
            return # Exit if addition failed

    # GraphQL mutation template for updating a field value
    mutation_template = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue) {
        updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, 
            itemId: $itemId, 
            fieldId: $fieldId, 
            value: $value
        }) { projectV2Item { id } }
    }
    """
    
    # -----------------------------------------------------------------
    # Step 2: Set "System Requirement ID" (Text Field)
    # -----------------------------------------------------------------
    sys_req_id = req.get("id") or req.get("ID") 
    if FIELD_ID_REQID and sys_req_id:
        variables = {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_REQID,
            "value": {"text": str(sys_req_id)}
        }
        github_graphql_request(github_token, mutation_template, variables)
        print(f"-> Set 'System Requirement ID' to: {sys_req_id}")

    # -----------------------------------------------------------------
    # Step 3: Set "Requirement Label" (Single Select = 'System Requirement')
    # -----------------------------------------------------------------
    # Hardcode 'System Requirement' as the label for all imported requirements
    req_label_data = FIELD_ID_MAP.get("Requirement Label", {})
    option_id_label = req_label_data.get("options", {}).get("System Requirement")
    
    if FIELD_ID_LABEL and option_id_label:
        variables = {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_LABEL,
            "value": {"singleSelectOptionId": option_id_label}
        }
        github_graphql_request(github_token, mutation_template, variables)
        print("-> Set 'Requirement Label' to: System Requirement")
    elif FIELD_ID_LABEL:
        print("⚠️ Requirement Label 'System Requirement' not found as a selectable option in the project.")


    # -----------------------------------------------------------------
    # Step 4: Set "Priority" (Single Select Field)
    # -----------------------------------------------------------------
    # Get the raw Priority value from the ReqIF attributes
    priority_text = req.get('attributes', {}).get('Priority')
    
    # Use a default mapping if raw value is not found or is empty
    if not priority_text:
        priority_text = "Medium" # Default if not specified

    # Mapping ReqIF values to GitHub values (customize this if needed)
    priority_map = {
        "High": "High",
        "Medium": "Medium",
        "Low": "Low",
    }
    mapped_priority_text = priority_map.get(priority_text, "Medium") # Default to Medium if unknown
    
    priority_data = FIELD_ID_MAP.get("Priority", {})
    option_id_priority = priority_data.get("options", {}).get(mapped_priority_text)

    if FIELD_ID_PRIORITY and option_id_priority:
        variables = {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_PRIORITY,
            "value": {"singleSelectOptionId": option_id_priority}
        }
        github_graphql_request(github_token, mutation_template, variables)
        print(f"-> Set 'Priority' (Single Select) to: {mapped_priority_text}")
    elif FIELD_ID_PRIORITY:
        print(f"⚠️ Priority '{mapped_priority_text}' (mapped from '{priority_text}') not found as a selectable option in the project.")

    # -----------------------------------------------------------------
    # 🆕 Step 5: Set "Status" (Single Select = 'Backlog')
    # -----------------------------------------------------------------
    status_text = "Backlog" # Always default to Backlog for new/updated requirements
    status_data = FIELD_ID_MAP.get("Status", {})
    option_id_status = status_data.get("options", {}).get(status_text)
    
    if FIELD_ID_STATUS and option_id_status:
        variables = {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_STATUS,
            "value": {"singleSelectOptionId": option_id_status}
        }
        github_graphql_request(github_token, mutation_template, variables)
        print(f"-> Set 'Status' (Single Select) to: {status_text}")
    elif FIELD_ID_STATUS:
        print(f"⚠️ Status '{status_text}' not found as a selectable option in the project. Please ensure the option exists in GitHub.")


def close_issue(repo, token, issue_number, req_id):
    """Closes a GitHub issue."""
    global IS_DRY_RUN
    if IS_DRY_RUN:
        print(f"⏩ SKIPPED: Closing issue #{issue_number} ({req_id}) skipped (Dry Run Mode).")
        return
        
    url = f"{GITHUB_API_URL}/repos/{repo}/issues/{issue_number}"
    data = {
        "state": "closed",
        "state_reason": "not_planned"
    }
    resp = requests.patch(url, headers=github_headers(token), json=data)
    if resp.status_code >= 400:
        print(f"❌ Failed to close issue #{issue_number} (Status: {resp.status_code}). Response: {resp.text}")
    else:
        print(f"🔒 Closed issue #{issue_number} ({req_id})")


def init_github_project(owner, repo_full_name, project_title, github_token):
    """Initializes the global project variables by fetching Project and Field IDs."""
    global PROJECT_NODE_ID, FIELD_ID_REQID, FIELD_ID_PRIORITY, FIELD_ID_LABEL, FIELD_ID_STATUS
    
    # -------------------------------------------------------------
    # Check for required environment variables
    # -------------------------------------------------------------
    if not all([owner, repo_full_name, project_title, github_token]):
        print("❌ Missing required environment variables (OWNER, REPO_FULL_NAME, PROJECT_TITLE, GITHUB_TOKEN). Cannot initialize project fields.")
        print("Please set them in your workflow YAML:")
        print(" PROJECT_OWNER: ${{ secrets.PROJECT_OWNER }}")
        print(" PROJECT_TITLE: ${{ secrets.PROJECT_TITLE }}")
        return

    # -------------------------------------------------------------
    # 1. Query GitHub to get the Project V2 Node ID dynamically
    # -------------------------------------------------------------
    query = """
    query GetProjectID($owner: String!, $repo: String!) {
        repository(owner: $owner, name: $repo) {
            projectsV2(first: 20) {
                nodes {
                    id
                    title
                    url
                }
            }
        }
    }
    """
    repo_name = repo_full_name.split("/")[-1] # Extract repo name only
    variables = {
        "owner": owner,
        "repo": repo_name
    }
    print(f"🔎 Looking up Project ID for owner='{owner}', repo='{repo_name}', title='{project_title}'...")
    response = github_graphql_request(github_token, query, variables)
    
    try:
        projects = (
            response
            .get("data", {})
            .get("repository", {})
            .get("projectsV2", {})
            .get("nodes", [])
        )
    except:
        projects = []
        
    if not projects:
        print("❌ No ProjectV2 boards found in the repository.")
        return

    # Find project by title (exact match)
    matched = [p for p in projects if p.get("title") == project_title]

    if not matched:
        print(f"❌ Project titled '{project_title}' not found.")
        print("📌 Available project titles:")
        for p in projects:
            print(f" - {p.get('title')}")
        return

    PROJECT_NODE_ID = matched[0]["id"]
    print(f"✅ Found Project Node ID: {PROJECT_NODE_ID}")

    # -------------------------------------------------------------
    # 2. Fetch all field metadata (same as before)
    # -------------------------------------------------------------
    try:
        fetch_project_metadata(PROJECT_NODE_ID, github_token)
    except Exception as e:
        print(f"❌ Failed to fetch project metadata: {e}")
        return

    # -------------------------------------------------------------
    # 3. Assign global field IDs
    # -------------------------------------------------------------
    FIELD_ID_REQID = FIELD_ID_MAP.get("System Requirement ID", {}).get("id")
    FIELD_ID_PRIORITY = FIELD_ID_MAP.get("Priority", {}).get("id")
    FIELD_ID_LABEL = FIELD_ID_MAP.get("Requirement Label", {}).get("id")
    FIELD_ID_STATUS = FIELD_ID_MAP.get("Status", {}).get("id")

    print("✅ Initialized project configuration using dynamic lookup.")
    print(f" Project ID: {PROJECT_NODE_ID}")
    print(f" System Requirement Field ID: {FIELD_ID_REQID}")
    print(f" Priority Field ID: {FIELD_ID_PRIORITY}")
    print(f" Requirement Label Field ID: {FIELD_ID_LABEL}")
    print(f" Status Field ID: {FIELD_ID_STATUS}")

    # Log warnings if required fields are missing
    if not FIELD_ID_REQID: print("⚠️ Warning: Project field 'System Requirement ID' not found.")
    if not FIELD_ID_PRIORITY: print("⚠️ Warning: Project field 'Priority' not found.")
    if not FIELD_ID_LABEL: print("⚠️ Warning: Project field 'Requirement Label' not found.")
    if not FIELD_ID_STATUS: print("⚠️ Warning: Project field 'Status' not found.")


def sync_reqif_to_github():
    """Main function to parse ReqIF, sync to GitHub issues, and update project fields."""
    global IS_DRY_RUN
    
    # 1. Set the Dry Run Mode based on environment variable
    IS_DRY_RUN = os.getenv("REQIF_DRY_RUN", "False").lower() in ('true', '1', 't')
    if IS_DRY_RUN:
        print("⚠️ Running in DRY RUN mode. No changes will be committed to GitHub or config files.")

    # 2. Get environment variables
    github_token = os.getenv("GITHUB_TOKEN")
    repo_full_name = os.getenv("GITHUB_REPOSITORY")
    owner = os.getenv("PROJECT_OWNER")
    project_title = os.getenv("PROJECT_TITLE")

    if not all([github_token, repo_full_name, owner, project_title]):
        print("❌ Missing one or more required environment variables for GitHub synchronization (GITHUB_TOKEN, GITHUB_REPOSITORY, PROJECT_OWNER, PROJECT_TITLE). Skipping GitHub sync.")
        # If running only to generate the config file, we can continue to parse_reqif_requirements
        try:
            reqs = parse_reqif_requirements()
            print(f"Parsed {len(reqs)} requirements. Exiting gracefully without GitHub sync.")
        except SystemExit: # parse_reqif_requirements can exit if file not found
            pass 
        except Exception:
            print("❌ Unexpected error during parsing.")
            traceback.print_exc()
        return

    try:
        # 3. Parse ReqIF file and run schema detection
        reqs = parse_reqif_requirements()
        
        # 4. Initialize GitHub Project fields
        init_github_project(owner, repo_full_name, project_title, github_token)

        # 5. Get existing issues and create a map
        existing_issues = get_existing_issues(repo_full_name, github_token)
        issue_map = map_req_to_issue(existing_issues)
        
        # 6. Create/Update issues and update Project Fields
        for req_id, req in reqs.items():
            issue = issue_map.get(req_id)
            issue_node_id = None
            
            if issue:
                # 🟢 UNCONDITIONALLY update the issue if found in ReqIF (check inside function).
                updated_issue = update_issue(repo_full_name, github_token, issue["number"], req)
                # Use the original issue's node_id for project fields if no actual update occurred in dry run
                issue_node_id = issue.get('node_id')

            else:
                # Issue creation returns full JSON object (check inside function).
                new_issue_json = create_issue(repo_full_name, github_token, req)
                if new_issue_json:
                    issue_node_id = new_issue_json.get('node_id')
            
            # Set Project Fields for new or existing issue 
            if PROJECT_NODE_ID and issue_node_id:
                # set_issue_project_fields now contains the IS_DRY_RUN check
                set_issue_project_fields(req, issue_node_id, github_token)


        # 7. Close removed issues
        for req_id, issue in issue_map.items():
            if req_id not in reqs:
                # close_issue now contains the IS_DRY_RUN check
                close_issue(repo_full_name, github_token, issue["number"], req_id)

        print("✅ Synchronization complete.")
    except Exception:
        print("❌ Unexpected error during synchronization.")
        traceback.print_exc()

if __name__ == "__main__":
    sync_reqif_to_github()