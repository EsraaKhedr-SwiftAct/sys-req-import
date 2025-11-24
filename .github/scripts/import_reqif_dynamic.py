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
# Schema Management
# -------------------------
def perform_schema_detection(reqif_attrs):
    """Detects new attributes and updates the config file using normalized keys."""
    config = load_config()
    config_attrs = config.setdefault("attributes", {})
    new_attr_found = False

    # Normalize existing config keys
    normalized_config = {str(k).strip(): v for k, v in config_attrs.items()}
    config_attrs.clear()
    config_attrs.update(normalized_config)

    # Track attributes in config but not in current ReqIF
    config_keys = set(config_attrs.keys())

    # Detect new normalized attributes
    for attr_name in sorted(reqif_attrs):
        key = str(attr_name).strip()  # normalized key
        if key in ("ID", "Title", "Description", "Text"):
            continue
        # ✅ Skip __parent__ and __children__ if they are not in this ReqIF
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
# Parse .reqif files
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
        # Normalize ID, Title, Description/Text from attributes
        req_id = getattr(req, "identifier", None) \
                or attributes.get("ID") \
                or f"REQ-{i+1}"

        title = getattr(req, "title", None) \
                or attributes.get("Title") \
                or req_id

        # ⚡ Normalize Description/Text
        description = getattr(req, "description", None) \
                    or attributes.get("Description") \
                    or attributes.get("Text") \
                    or "(No description found)"

        # Cleanup formatting
        req_id = str(req_id).strip()
        title = str(title).strip()
        description = str(description).strip()

        title = html.unescape(title)
        description = html.unescape(description)

        # Replace non-breaking spaces
        title = title.replace('\xa0', ' ')
        description = description.replace('\xa0', ' ')


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
            description = description.replace('\xa0', ' ')

        # Normalize custom attributes and track unique names
        normalized_attrs = {}
        for k, v in attributes.items():
            key = str(k).strip()
            normalized_attrs[key] = str(v).strip() if v is not None else "(No value)"
            all_unique_attrs.add(key) # 🆕 Track unique attribute key

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

    # --- Add hierarchy attributes only if SPEC-HIERARCHY exists ---
    has_hierarchy = any(getattr(req, "children", []) for req in req_objects)
    if has_hierarchy:
        all_unique_attrs.add("__children__")
        all_unique_attrs.add("__parent__")

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

# --- NEW: GLOBAL DRY RUN FLAG ---
IS_DRY_RUN = False 
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
# Improved formatting (full attribute table using config)
# -------------------------
def format_req_body(req):
    config = load_config() 
    config_attrs = config.get("attributes", {})

    # DEBUG
    print("DEBUG RAW ATTR KEYS:", repr(list(req.get("attributes", {}).keys())))
    print("DEBUG RAW CONFIG KEYS:", repr(list(config_attrs.keys())))
    print(f"DEBUG: All attributes for {req.get('id')}: {list(req.get('attributes', {}).keys())}")
    print(f"DEBUG: Config attributes: {list(config_attrs.keys())}")
    if 'Priority' in req.get('attributes', {}):
        priority_config = config_attrs.get('Priority', {})
        print(f"DEBUG: Priority attribute found, config: {priority_config}")
        print(f"DEBUG: Priority value: {req.get('attributes', {}).get('Priority')}")    

    # Core fields
    CORE_FIELDS = {"ID", "Title", "Description", "Text"}

    show_description = True # config_attrs.get("Description", {}).get("include_in_body", True)
    show_text = True #config_attrs.get("Text", {}).get("include_in_body", True)
    show_id = True #config_attrs.get("ID", {}).get("include_in_body", True)
    show_title = True #config_attrs.get("Title", {}).get("include_in_body", True)

    include_description = config.get("include_description", True)


    # Case-insensitive detection of description fields
    desc_key = None
    attrs_lower = {k.lower(): k for k in req.get("attributes", {}).keys()}

    for candidate in ("description", "text"):
        if candidate in attrs_lower:
            desc_key = attrs_lower[candidate]   # original key
            break

    desc = req.get("attributes", {}).get(desc_key, "(No description found)").strip() if desc_key else "(No description found)"

    # Clean embedded Priority mentions
    desc = re.sub(r'(?:\s*|^\s*)[Pp]riority\s*[:=]\s*[^\n]+', '', desc).strip()

    attrs = req.get("attributes", {})

    # --- Build body ---
    body = f"**Requirement ID:** `{req.get('id', '(No ID)')}`\n\n"
    # Only show the description section if the specific attribute (Description or Text)
    # is enabled in the config AND include_description is true.
    if include_description and (
        (desc_key and desc_key.lower() == "description" and show_description) or
        (desc_key and desc_key.lower() == "text" and show_text)
    ):
        body += f"### 📝 Description\n{desc}\n\n"


    # Core values for table
    CORE_VALUES = {
        "ID": req.get('id', '(No ID)'),
        "Title": req.get('title', '(Untitled)')
    }

    table_lines = ["### 📄 Attributes", "| Attribute | Value |", "|------------|--------|"]

    if show_id:
        table_lines.append(f"| ID | {CORE_VALUES['ID']} |")
    if show_title:
        table_lines.append(f"| Title | {CORE_VALUES['Title']} |")

    filtered_attrs = {}
    for k, v in attrs.items():
        if k in {"ID", "Title", "Description", "Text"}:
            continue
        attr_config = config_attrs.get(k, {})
        if attr_config.get("include_in_body", True):
            filtered_attrs[k] = v

    for k, v in filtered_attrs.items():
        # Skip core fields and description/text
        if k in CORE_FIELDS or k.lower() in ("description", "text"):
            continue
        safe_v = str(v).replace("\n", " ").replace("|", "\\|")
        table_lines.append(f"| {k} | {safe_v} |")

    if len(table_lines) > 3:
        body += "\n".join(table_lines)
    else:
        body += "### 📄 Attributes\n(No attributes configured to display.)"

    return body.strip()




# -------------------------
# Project Management Logic (DYNAMIC DISCOVERY)
# -------------------------
def initialize_project_ids(repo_full_name, github_token):
    """
    Dynamically finds Project and Field IDs using GraphQL query
    based on PROJECT_OWNER and PROJECT_TITLE environment variables.
    """

    global PROJECT_NODE_ID, FIELD_ID_REQID, FIELD_ID_PRIORITY, FIELD_ID_LABEL, FIELD_ID_STATUS

    owner = os.getenv("PROJECT_OWNER")
    project_title = os.getenv("PROJECT_TITLE")

    if not owner or not project_title:
        print("❌ Missing PROJECT_OWNER or PROJECT_TITLE environment variables.")
        print("   Please set them in your workflow YAML:")
        print("   PROJECT_OWNER: ${{ secrets.PROJECT_OWNER }}")
        print("   PROJECT_TITLE: ${{ secrets.PROJECT_TITLE }}")
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

    repo_name = repo_full_name.split("/")[-1]   # Extract repo name only

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
            print(f"   - {p.get('title')}")
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

    FIELD_ID_REQID = FIELD_ID_MAP.get("System Requirement ID", {}).get("id")
    FIELD_ID_PRIORITY = FIELD_ID_MAP.get("Priority", {}).get("id")
    FIELD_ID_LABEL = FIELD_ID_MAP.get("Requirement Label", {}).get("id")
    FIELD_ID_STATUS = FIELD_ID_MAP.get("Status", {}).get("id")

    print("✅ Initialized project configuration using dynamic lookup.")
    print(f"  Project ID: {PROJECT_NODE_ID}")
    print(f"  System Requirement Field ID: {FIELD_ID_REQID}")
    print(f"  Priority Field ID: {FIELD_ID_PRIORITY}")
    print(f"  Requirement Label Field ID: {FIELD_ID_LABEL}")
    print(f"  Status Field ID: {FIELD_ID_STATUS}")

    
def set_issue_project_fields(req, issue_node_id, github_token):
    """
    Assigns Project V2 fields by first resolving the Issue ID (I_...) to the 
    required Project V2 Item ID (PVTI_...), adding the item if it's missing.
    """
    config = load_config()
    config_attrs = config.get("attributes", {})

    global IS_DRY_RUN
    if IS_DRY_RUN:
        print(f"⏩ SKIPPED: Project field update for {req.get('id', 'Unknown Req')} skipped (Dry Run Mode).")
        return
    
    # Use the Issue Node ID to find the Project V2 Item ID (PVTI_...)
    project_item_id = get_project_item_id(issue_node_id, PROJECT_NODE_ID, github_token)
    
    # FIX: If Item ID is missing for an existing issue, add it to the project and retry lookup.
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
    # Step 4: Set "Priority" (Single Select Field) - Controlled by config
    # -----------------------------------------------------------------

    priority_config = config["attributes"].get("Priority", {})
    priority_text = (req.get("attributes") or {}).get("Priority") or (req.get("attributes") or {}).get("PRIORITY")

    # If config says include_in_body=false → DO NOT set project Priority
    if not priority_config.get("include_in_body", True):
        print("-> Skipping Priority (config: include_in_body=false)")
        priority_text = None   # Prevent any further processing

    if priority_text:
        PRIORITY_MAPPING = {
            "High": "P0", 
            "Medium": "P1",
            "Low": "P2",
            "high": "P0", 
            "medium": "P1",
            "low": "P2",
        }

        mapped_priority_text = PRIORITY_MAPPING.get(priority_text, priority_text)
        priority_data = FIELD_ID_MAP.get("Priority", {})
        option_id_priority = priority_data.get("options", {}).get(mapped_priority_text)

        if FIELD_ID_PRIORITY and mapped_priority_text and option_id_priority:
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
            print(f"⚠️ Priority '{mapped_priority_text}' (mapped from '{priority_text}') not found as a selectable option in the project.")


    # -----------------------------------------------------------------
    # 🆕 Step 5: Set "Status" (Single Select = 'Backlog')
    # -----------------------------------------------------------------
    status_text = "Backlog" 

    status_data = FIELD_ID_MAP.get("Status", {})
    option_id_status = status_data.get("options", {}).get(status_text) 
    
    if FIELD_ID_STATUS and option_id_status:
        query_set_status = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
          updateProjectV2ItemFieldValue(input: {
            projectId: $projectId, itemId: $itemId, fieldId: $fieldId,
            value: { singleSelectOptionId: $optionId }
          }) { projectV2Item { id } }
        }
        """
        github_graphql_request(github_token, query_set_status, {
            "projectId": PROJECT_NODE_ID,
            "itemId": project_item_id,
            "fieldId": FIELD_ID_STATUS,
            "optionId": option_id_status
        })
        print(f"-> Set 'Status' (Single Select) to: {status_text}")
    elif FIELD_ID_STATUS:
        print(f"⚠️ Status '{status_text}' not found as a selectable option in the project. Please ensure the option exists in GitHub.")


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


def create_issue(repo, token, req):
    global IS_DRY_RUN
    if IS_DRY_RUN:
        print(f"⏩ SKIPPED: Issue creation for {req['id']} skipped (Dry Run Mode).")
        # Return a mock object with node_id to allow subsequent logic (like set_issue_project_fields) to run in dry-run simulation
        return {'node_id': f"I_mock_{req['id']}", 'number': 'MOCK'}

    # Ensure all newly created issues use the expected format: [ID] Title
    data = {
        "title": f"[{req['id']}] {choose_title(req)}",
        "body": format_req_body(req),
        "labels": ["System Requirement"],
    }
    resp = requests.post(f"{GITHUB_API_URL}/repos/{repo}/issues", headers=github_headers(token), json=data)
    
    if resp.status_code >= 300:
        print(f"❌ Failed to create issue for {req['id']}: {resp.text}")
        return None
    else:
        new_issue = resp.json()
        print(f"🆕 Created issue #{new_issue['number']} for {req['id']}")
        return new_issue


def update_issue(repo, token, issue_number, req):
    global IS_DRY_RUN
    if IS_DRY_RUN:
        print(f"⏩ SKIPPED: Issue #{issue_number} ({req['id']}) update skipped (Dry Run Mode).")
        return None # Return None to simulate no update occurred

    # Enforce the proper title format and single label on all updates
    data = {
        "title": f"[{req['id']}] {choose_title(req)}",
        "body": format_req_body(req),
        "state": "open",
        # 🟢 CRITICAL: This line forces the label to be ONLY "System Requirement"
        "labels": ["System Requirement"], 
    }
    resp = requests.patch(f"{GITHUB_API_URL}/repos/{repo}/issues/{issue_number}", headers=github_headers(token), json=data)
    if resp.status_code >= 300:
        print(f"❌ Failed to update issue #{issue_number}: {resp.text}")
        return None
    else:
        print(f"♻️ Updated issue #{issue_number} ({req['id']}) - Content and single label enforced.")
    return resp.json()


def close_issue(repo, token, issue_number, req_id):
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


# -------------------------
# Main synchronization (FIXED Issue Mapping & Update Logic)
# -------------------------
def sync_reqif_to_github():
    global IS_DRY_RUN
    
    # 1. Set the Dry Run Mode based on environment variable
    IS_DRY_RUN = os.getenv("REQIF_DRY_RUN", "False").lower() in ('true', '1', 't')

    github_token = os.getenv("GITHUB_TOKEN")
    repo_full_name = os.getenv("GITHUB_REPOSITORY")

    if not github_token or not repo_full_name:
        if IS_DRY_RUN:
            print("📢 Running in DRY RUN MODE. GITHUB_TOKEN or GITHUB_REPOSITORY are missing, but execution will continue...")
        else:
            print("❌ Missing GITHUB_TOKEN or GITHUB_REPOSITORY. Cannot run without them in production mode.")
            sys.exit(1)

    # --- Initialize Project IDs ---
    try:
        # NOTE: initialize_project_ids needs GITHUB_TOKEN to run even in dry run mode
        initialize_project_ids(repo_full_name, github_token)
    except Exception as e:
        print(f"❌ Project initialization failed: {e}")

    try:
        # This function now also performs schema detection and saves reqif_config.json
        reqs = parse_reqif_requirements() 
        issues = get_existing_issues(repo_full_name, github_token) 

        # Map existing issues by ReqIF ID (Flexible mapping added previously)
        issue_map = {}
        for issue in issues:
            title = issue.get("title", "")
            req_id = None
            
            # 1. Try format: [ID] Title
            if title.startswith("[") and "]" in title:
                req_id = title.split("]")[0][1:].strip()
            
            # 2. Try format: ID: Title 
            elif ":" in title:
                temp_id = title.split(":")[0].strip()
                if 0 < len(temp_id.split()) <= 3: 
                    req_id = temp_id

            if req_id:
                issue_map[req_id] = issue
            else:
                print(f"⚠️ Warning: Issue #{issue.get('number')} with title '{title}' skipped. Title does not match a recognizable ID format ([ID] Title or ID: Title).")


        # Create or update issues
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


        # Close removed issues
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