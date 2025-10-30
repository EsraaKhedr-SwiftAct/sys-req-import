# .github/scripts/import_reqif_dynamic.py

import os
import requests
import json
import glob
from reqif.parser import ReqIFParser
import traceback # NEW: Import traceback for detailed error logging
# NOTE: Removed the problematic dependency on ReqIFException.

# --- Configuration and Constants ---
REQIF_FILE_PATH = 'sample.reqif'

# --- GitHub API Setup ---
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
# GITHUB_REPOSITORY is expected to be in the format 'owner/repo'
GITHUB_REPOSITORY = os.environ.get('GITHUB_REPOSITORY') 

if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
    print("Error: GITHUB_TOKEN or GITHUB_REPOSITORY environment variable not set.")
    exit(1)

REPO_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
HEADERS = {
    'Authorization': f'token {GITHUB_TOKEN}',
    'Accept': 'application/vnd.github.v3+json'
}

# --- ReqIF Parsing Function ---

def parse_reqif_file(filepath):
    """
    Parses the ReqIF file by passing the filepath directly to the parser. 
    Returns a dictionary of requirements, keyed by REQ-ID, or an empty dictionary on failure.
    """
    try:
        # CORRECTED: Use ReqIFParser.parse(filepath) which is the correct static
        # method to parse the file using its path, resolving the 'parse_string' error.
        reqif_bundle = ReqIFParser.parse(filepath)
        
        print(f"✅ Successfully parsed ReqIF file: {filepath}")
        
        requirements = {}
        
        # Check if spec_objects exist in the core content
        if reqif_bundle.core_content and reqif_bundle.core_content.spec_objects:
            for spec_object in reqif_bundle.core_content.spec_objects:
                req_id = None
                req_title = None
                req_desc = None
                
                # Extract REQ-ID, REQ-TITLE, and REQ-DESC from the object's attributes
                for attr_value in spec_object.attribute_map.values():
                    # The definition.long_name is used to find the correct attribute
                    if attr_value.definition.long_name == 'REQ-ID':
                        req_id = attr_value.value
                    elif attr_value.definition.long_name == 'REQ-TITLE':
                        req_title = attr_value.value
                    elif attr_value.definition.long_name == 'REQ-DESC':
                        req_desc = attr_value.value
                
                if req_id and req_title and req_desc:
                    requirements[req_id] = {
                        'title': req_title,
                        'body': req_desc
                    }
        
        print(f"✅ Extracted {len(requirements)} requirements from ReqIF.")
        return requirements

    except Exception as e:
        # MODIFIED: Print the full traceback to diagnose the root cause of the empty error message.
        print(f"❌ Failed to parse ReqIF file with 'reqif' library: {e}")
        print("--- FULL TRACEBACK START ---")
        # Print the traceback using the imported module
        print(traceback.format_exc())
        print("--- FULL TRACEBACK END ---")
        return {}


# --- GitHub API Functions (Unchanged) ---

def find_issue_by_title(title_prefix):
    """
    Searches for an open issue whose title starts with the given prefix (e.g., '[REQ-001]').
    Returns the issue number or None.
    """
    url = f"{REPO_URL}/issues"
    # Search for open issues and limit results
    params = {'state': 'open', 'per_page': 30}
    response = requests.get(url, headers=HEADERS, params=params)

    if response.status_code == 200:
        issues = response.json()
        for issue in issues:
            # Check if the title starts with the exact prefix
            if issue['title'].startswith(title_prefix):
                return issue['number']
        return None
    else:
        print(f"❌ Failed to search issues. Status: {response.status_code}, Response: {response.text}")
        return None


def update_issue(issue_number, title, body):
    """Updates an existing GitHub issue with new title and body."""
    url = f"{REPO_URL}/issues/{issue_number}"
    data = {
        'title': title,
        'body': body
    }
    
    response = requests.patch(url, headers=HEADERS, data=json.dumps(data))
    
    if response.status_code == 200:
        print(f"✅ Issue #{issue_number} updated successfully.")
    else:
        print(f"❌ Failed to update Issue #{issue_number}. Status: {response.status_code}, Response: {response.text}")

def create_issue(title, body):
    """Creates a new GitHub issue."""
    url = f"{REPO_URL}/issues"
    data = {
        'title': title,
        'body': body,
    }
    
    response = requests.post(url, headers=HEADERS, data=json.dumps(data))
    
    if response.status_code == 201:
        issue_number = response.json().get('number')
        print(f"✨ New Issue #{issue_number} created for requirement: {title}")
        return issue_number
    else:
        print(f"❌ Failed to create Issue: {title}. Status: {response.status_code}, Response: {response.text}")
        return None


# --- Main Logic (Unchanged) ---

def main():
    print(f"Starting ReqIF synchronization for repository: {GITHUB_REPOSITORY}")

    # 1. Check for the ReqIF file
    reqif_files = glob.glob(REQIF_FILE_PATH)
    if not reqif_files:
        print(f"Error: No ReqIF file found at {REQIF_FILE_PATH}")
        return
    
    print(f"📄 Found ReqIF file: {reqif_files[0]}")
    
    # 2. Parse the ReqIF file
    extracted_reqs = parse_reqif_file(reqif_files[0])

    if not extracted_reqs:
        print("🛑 No requirements extracted or parsing failed. Exiting synchronization.")
        return

    # 3. Iterate over ALL extracted requirements and sync with GitHub Issues
    print(f"\n--- Starting synchronization of {len(extracted_reqs)} requirements ---")
    
    for req_id, req_data in extracted_reqs.items():
        # The prefix is used to uniquely identify this requirement's issue
        issue_title_prefix = f"[{req_id}]"
        full_issue_title = f"{issue_title_prefix} {req_data['title']}"
        
        # 3a. Try to find an existing issue
        existing_issue_number = find_issue_by_title(issue_title_prefix)

        if existing_issue_number:
            # 3b. If found, update the issue
            update_issue(existing_issue_number, 
                         full_issue_title, 
                         req_data['body'])
        else:
            # 3c. If not found, create a new issue
            create_issue(full_issue_title, 
                         req_data['body'])


    print(f"\n✅ Completed ReqIF → GitHub synchronization (issues).")


if __name__ == "__main__":
    main()






