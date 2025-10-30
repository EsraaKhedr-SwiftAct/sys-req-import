import sys
import os
import glob
import requests
import traceback
import pkgutil

# Directory of this script
scripts_dir = os.path.dirname(__file__)

# Path to the inner folder inside the zip that contains __init__.py
strictdoc_inner_path = os.path.join(scripts_dir, "strictdoc_local_fixed", "strictdoc_local_fixed")
sys.path.insert(0, strictdoc_inner_path)

# Import the module using the actual folder name
import strictdoc_local_fixed as strictdoc

# Import the ReqIF parser
from reqif_importer import ReqIFImporter as ReqIFParser

# Optional diagnostic to verify import
print("StrictDoc imported from:", strictdoc.__file__)
for _, name, _ in pkgutil.iter_modules(strictdoc.__path__):
    print("Found module:", name)

# --- Configuration ---
GITHUB_API_URL = "https://api.github.com/repos"

def create_or_update_github_issue(req_id, title, body, github_token, repo_full_name):
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.com"
    }
    url = f"{GITHUB_API_URL}/{repo_full_name}/issues"

    issue_title = f"[{req_id}] {title}"
    issue_body = f"## ReqIF Requirement: {req_id}\n\n**Title:** {title}\n\n---\n\n**Description:**\n\n{body}"

    print(f"Creating/Updating Issue for ID: {req_id}...")

    payload = {
        "title": issue_title,
        "body": issue_body,
        "labels": ["requirement", "reqif-import"]
    }

    try:
        print(f"  [Mock API Call] Successfully prepared data for issue: {issue_title}") 
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

def process_reqif_files():
    github_token = os.environ.get("GITHUB_TOKEN")
    repo_full_name = os.environ.get("GITHUB_REPOSITORY")

    if not github_token or not repo_full_name:
        print("Warning: GITHUB_TOKEN or GITHUB_REPOSITORY environment variables not set. Running in local/mock mode.")

    reqif_files = glob.glob('**/*.reqif', recursive=True)

    if not reqif_files:
        print("No .reqif files found in the repository.")
        return

    print(f"Found {len(reqif_files)} ReqIF file(s) to process.")

    for file_path in reqif_files:
        print(f"\n--- Processing file: {file_path} ---")
        try:
            # Parse the ReqIF file
            parser = ReqIFParser(file_path)
            requirements = parser.parse()  # <-- returns a list of dicts

            for req in requirements:
                req_id = req.get('id', 'UNKNOWN')
                title = req.get('title', 'Untitled Requirement')
                description = req.get('description', '')

                if req_id and title:
                    print(f"  Extracted -> ID: {req_id}, Title: {title[:40]}...")
                    create_or_update_github_issue(req_id, title, description, github_token, repo_full_name)
                else:
                    print(f"  Skipping requirement with missing required attributes: {req}")

        except Exception:
            print(f"Failed to process {file_path}. Details below:")
            traceback.print_exc()

if __name__ == "__main__":
    process_reqif_files()











