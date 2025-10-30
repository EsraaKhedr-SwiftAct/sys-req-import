import os
import sys
import glob
import requests
from reqif import ReqIf # We continue to use the 'reqif' library as requested

# --- Configuration ---
# GitHub API base URL
GITHUB_API_URL = "https://api.github.com/repos"
# Expected attribute names/IDs in the ReqIF file
REQIF_ATTRIBUTES = {
    'id': 'REQ-ID',
    'title': 'REQ-TITLE',
    'description': 'REQ-DESC'
}

def find_attribute_value(spec_object, definition_id):
    """
    Utility function to reliably find an attribute value in a SpecObject
    based on its Definition ID (e.g., 'REQ-TITLE').
    """
    for attribute_value in spec_object.values:
        # Check if the attribute definition matches the target ID
        if (attribute_value.definition and 
            hasattr(attribute_value.definition, 'identifier') and
            attribute_value.definition.identifier == definition_id):

            # Attribute values can be of different types, check for 'the_value'
            if hasattr(attribute_value, 'the_value'):
                # Clean up the value if it's a string, removing leading/trailing whitespace
                if isinstance(attribute_value.the_value, str):
                    # Simple HTML/XHTML cleanup for descriptions is often needed
                    return attribute_value.the_value.replace('</p>', '\n').replace('<p>', '').strip()
                return str(attribute_value.the_value).strip()
    return None

def create_or_update_github_issue(req_id, title, body, github_token, repo_full_name):
    """
    Placeholder for GitHub API interaction. In a real scenario, this function
    would search existing issues by a specific tag or title format (e.g., "[REQ] REQ-001")
    and either update it or create a new one.

    For this example, we will just create a new issue for demonstration.
    """
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    url = f"{GITHUB_API_URL}/{repo_full_name}/issues"

    # Define the issue title and body
    issue_title = f"[{req_id}] {title}"
    issue_body = f"## ReqIF Requirement: {req_id}\n\n**Title:** {title}\n\n---\n\n**Description:**\n\n{body}"

    # NOTE: In a complete implementation, you would check for an existing issue
    # by searching: GET /search/issues?q={req_id}+in:title+repo:{repo_full_name}
    # If found, you'd use PATCH (update), otherwise POST (create).

    print(f"Creating/Updating Issue for ID: {req_id}...")

    payload = {
        "title": issue_title,
        "body": issue_body,
        "labels": ["requirement", "reqif-import"]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"Successfully created issue for {req_id}. Status: {response.status_code}")
    except requests.exceptions.HTTPError as err:
        print(f"Error processing requirement {req_id}: {err}")
        print(f"GitHub API response: {response.text}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


def process_reqif_files():
    """
    Scans the repository for all ReqIF files and processes them.
    """
    github_token = os.environ.get("GITHUB_TOKEN")
    repo_full_name = os.environ.get("GITHUB_REPOSITORY")

    if not github_token or not repo_full_name:
        print("Error: GITHUB_TOKEN or GITHUB_REPOSITORY environment variables not set.")
        sys.exit(1)

    reqif_files = glob.glob('**/*.reqif', recursive=True)

    if not reqif_files:
        print("No .reqif files found in the repository.")
        return

    print(f"Found {len(reqif_files)} ReqIF file(s) to process.")

    for file_path in reqif_files:
        print(f"\n--- Processing file: {file_path} ---")
        try:
            # Load the ReqIF file
            reqif_data = ReqIf.open(file_path)

            # Iterate over all SpecObjects (requirements/artifacts)
            for spec_object in reqif_data.spec_objects:
                # Use the robust finder to get the specific attributes
                req_id = find_attribute_value(spec_object, REQIF_ATTRIBUTES['id'])
                title = find_attribute_value(spec_object, REQIF_ATTRIBUTES['title'])
                description = find_attribute_value(spec_object, REQIF_ATTRIBUTES['description'])

                if req_id and title and description:
                    print(f"  Extracted -> ID: {req_id}, Title: {title[:40]}...")
                    # Process and create GitHub Issue
                    create_or_update_github_issue(req_id, title, description, github_token, repo_full_name)
                else:
                    print(f"  Skipping SpecObject with identifier '{spec_object.identifier}' - Missing required attributes.")

        except Exception as e:
            print(f"Failed to process {file_path}: {e}")
            # Continue to the next file if one fails

if __name__ == "__main__":
    process_reqif_files()






