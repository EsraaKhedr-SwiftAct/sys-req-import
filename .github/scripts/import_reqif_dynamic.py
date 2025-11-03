#!/usr/bin/env python3
"""
import_reqif_dynamic.py
-----------------------
Synchronizes .reqif system requirements with GitHub Issues.

✅ Creates new issues if not found.
✅ Updates existing issues if description/title changes.
✅ Reopens closed issues if requirement reappears.
✅ Closes issues that no longer exist in .reqif.

Expected environment variables:
  - GITHUB_TOKEN: GitHub PAT or Actions token.
  - GITHUB_REPOSITORY: "owner/repo" format.

Compatible with the `reqif_importer` (StrictDoc-based) local library.
"""

import os
import sys
import glob
import traceback
import requests

# =====================================================
# 🧱 Load local reqif_importer (StrictDoc-based)
# =====================================================
scripts_dir = os.path.dirname(__file__)
strictdoc_path = os.path.join(scripts_dir, "strictdoc_local_fixed")
if os.path.isdir(strictdoc_path):
    sys.path.insert(0, strictdoc_path)

try:
    from reqif_importer import ReqIFImporter as ReqIFParser
except ImportError:
    print("❌ Could not import reqif_importer. Please ensure 'strictdoc_local_fixed' exists.")
    sys.exit(1)

print("✅ ReqIF importer loaded successfully.")
# =====================================================
# 🔹 Parse .reqif file into a dict keyed by requirement ID
# =====================================================
def parse_reqif_requirements():
    """
    Loads the first .reqif file found in the current directory,
    parses it, and returns a dict keyed by requirement ID.
    """
    # Find .reqif file
    reqif_files = glob.glob("*.reqif")
    if not reqif_files:
        print("❌ No .reqif file found in current directory.")
        sys.exit(1)

    reqif_file = reqif_files[0]
    print(f"📄 Parsing ReqIF file: {reqif_file}")

    importer = ReqIFParser(reqif_file)
    req_list = importer.parse()

    # Convert to dict keyed by 'id'
    req_dict = {req['id']: req for req in req_list}

    print(f"✅ Parsed {len(req_dict)} requirements.")
    return req_dict


# =====================================================
# 🔧 GitHub setup
# =====================================================
GITHUB_API_URL = "https://api.github.com/repos"


def github_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

# =====================================================
# 🔹 Helper: format requirement body (universal & clean)
# =====================================================
def format_req_body(req):
    """
    Format requirement for GitHub issue body in Markdown:
      - Requirement ID
      - Title
      - Description (only descriptive text)
      - Attributes (all remaining key-value pairs)
    Works generically with ReqIF files from Polarion, DOORS, Jama, PTC, etc.
    """
    lines = [f"**Requirement ID:** {req.get('id', '(No ID)')}", ""]

    # --- Title ---
    title = req.get("title", "").strip()
    if title:
        lines.append("**Title:**")
        lines.append(title)
        lines.append("")

    # --- Description ---
    description = req.get("description", "").strip()
    lines.append("**Description:**")
    if description:
        lines.append(description)
    else:
        lines.append("(No description found)")
    lines.append("")

    # --- Attributes ---
    attrs = {k: v for k, v in req.items() if k.lower() not in ["id", "title", "description"]}
    if attrs:
        lines.append("**Attributes:**")
        for k, v in attrs.items():
            # Normalize key name and value formatting
            clean_key = k.replace("_", " ").title()
            clean_val = str(v)
            # Optional: simplify ENUM or coded values like STATUS_APPROVED → Approved
            if isinstance(clean_val, str):
                clean_val = (
                    clean_val
                    .replace("STATUS_", "")
                    .replace("ENUM:", "")
                    .replace("_", " ")
                    .strip()
                    .capitalize()
                )
            lines.append(f"{clean_key}: {clean_val}")

    return "\n".join(lines)

# =====================================================
# 🧭 GitHub issue management
# =====================================================
def get_existing_issues(repo, token):
    """Fetch all issues with label 'reqif-import'."""
    print("📡 Fetching existing GitHub issues...")
    url = f"{GITHUB_API_URL}/{repo}/issues?state=all&labels=reqif-import&per_page=100"
    issues = []
    while url:
        resp = requests.get(url, headers=github_headers(token))
        resp.raise_for_status()
        issues += resp.json()
        url = resp.links.get("next", {}).get("url")
    print(f"🔍 Found {len(issues)} existing 'reqif-import' issues.")
    return issues


def create_issue(repo, token, req):
    data = {
        "title": f"[{req['id']}] {req['title']}",
        "body": format_req_body(req),
        "labels": ["requirement", "reqif-import"],
    }
    url = f"{GITHUB_API_URL}/{repo}/issues"
    resp = requests.post(url, headers=github_headers(token), json=data)
    if resp.status_code >= 300:
        print(f"❌ Failed to create issue for {req['id']}: {resp.text}")
    else:
        print(f"🆕 Created issue for {req['id']}")
    return resp.json()


def update_issue(repo, token, issue_number, req):
    url = f"{GITHUB_API_URL}/{repo}/issues/{issue_number}"
    data = {
        "title": f"[{req['id']}] {req['title']}",
        "body": format_req_body(req),
        "state": "open",
    }
    resp = requests.patch(url, headers=github_headers(token), json=data)
    if resp.status_code >= 300:
        print(f"❌ Failed to update issue #{issue_number}: {resp.text}")
    else:
        print(f"♻️ Updated issue #{issue_number} ({req['id']})")
    return resp.json()


def close_issue(repo, token, issue_number, req_id):
    url = f"{GITHUB_API_URL}/{repo}/issues/{issue_number}"
    data = {"state": "closed"}
    resp = requests.patch(url, headers=github_headers(token), json=data)
    if resp.status_code >= 300:
        print(f"❌ Failed to close issue #{issue_number}: {resp.text}")
    else:
        print(f"🔒 Closed issue #{issue_number} ({req_id})")

# =====================================================
# 🔁 Main synchronization
# =====================================================
def sync_reqif_to_github():
    github_token = os.getenv("GITHUB_TOKEN")
    repo_full_name = os.getenv("GITHUB_REPOSITORY")

    if not github_token or not repo_full_name:
        print("❌ Missing GITHUB_TOKEN or GITHUB_REPOSITORY.")
        sys.exit(1)

    try:
        reqs = parse_reqif_requirements()
        issues = get_existing_issues(repo_full_name, github_token)

        # Map GitHub issues by REQ-ID (parsed from title)
        issue_map = {}
        for issue in issues:
            title = issue["title"]
            if title.startswith("[") and "]" in title:
                req_id = title.split("]")[0][1:]
                issue_map[req_id] = issue

        # === Create/Update/Reopen ===
        for req_id, req in reqs.items():
            new_title = f"[{req['id']}] {req['title']}"
            new_body = format_req_body(req)

            if req_id in issue_map:
                issue = issue_map[req_id]
                # Only update if title or body changed
                if issue["title"] != new_title or issue["body"] != new_body:
                    if issue["state"] == "closed":
                        print(f"🔄 Reopening and updating issue for {req_id}")
                    else:
                        print(f"♻️ Updating issue for {req_id}")
                    update_issue(repo_full_name, github_token, issue["number"], req)
                else:
                    print(f"✅ No changes detected for {req_id}, skipping update.")
            else:
                create_issue(repo_full_name, github_token, req)

        # === Close missing ===
        for req_id, issue in issue_map.items():
            if req_id not in reqs:
                close_issue(repo_full_name, github_token, issue["number"], req_id)

        print("✅ Synchronization complete.")

    except Exception as e:
        print("❌ Unexpected error during synchronization.")
        traceback.print_exc()


if __name__ == "__main__":
    sync_reqif_to_github()


