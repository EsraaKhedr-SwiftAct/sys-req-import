#!/usr/bin/env python3
"""
Synchronizes .reqif system requirements with GitHub Issues.
Compatible with the local StrictDoc substitute in 'strictdoc_local_fixed'.
"""

import os
import sys
import glob
import traceback
import requests

# =====================================================
# 🧱 Load StrictDoc ReqIF parser
# =====================================================
scripts_dir = os.path.dirname(__file__)
strictdoc_parent = os.path.join(scripts_dir)  # parent of strictdoc_local_fixed
strictdoc_folder = os.path.join(scripts_dir, "strictdoc_local_fixed")

if os.path.isdir(strictdoc_folder):
    sys.path.insert(0, strictdoc_parent)
    print(f"📂 Added to PYTHONPATH: {strictdoc_parent}")
    print("🔍 Files in PYTHONPATH directory:")
    for f in os.listdir(strictdoc_parent):
        print(f"  {f}")
else:
    print("❌ strictdoc_local_fixed directory not found.")
    sys.exit(1)

try:
    from strictdoc_local_fixed.reqif_importer import ReqIFImporter as ReqIFParser
    print("✅ Local StrictDoc-compatible ReqIF importer loaded successfully.")
except Exception as e:
    print(f"❌ Could not import reqif_importer: {e}")
    sys.exit(1)


# =====================================================
# 🔹 Parse .reqif file
# =====================================================
def parse_reqif_requirements():
    reqif_files = glob.glob("*.reqif")
    if not reqif_files:
        print("❌ No .reqif file found in current directory.")
        sys.exit(1)

    reqif_file = reqif_files[0]
    print(f"📄 Parsing ReqIF file: {reqif_file}")

    importer = ReqIFParser(reqif_file)
    req_list = importer.parse()
    req_dict = {req['id']: req for req in req_list}
    print(f"✅ Parsed {len(req_dict)} requirements.")
    return req_dict

# =====================================================
# 🔧 GitHub setup
# =====================================================
GITHUB_API_URL = "https://api.github.com/repos"
def github_headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

# =====================================================
# 🆕 Helper: choose title
# =====================================================
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

# =====================================================
# 🔹 Format requirement body
# =====================================================
def format_req_body(req):
    lines = [f"**Requirement ID:** {req.get('id', '(No ID)')}", ""]
    description = (req.get("description") or "").strip()
    desc_lines = [line.strip() for line in description.splitlines() if line.strip()]
    lines.append("**Description:**")
    lines.append("\n".join(desc_lines) if desc_lines else "(No description found)")
    lines.append("")
    attrs = {k: v for k, v in req.items() if k.lower() not in ["id", "title", "description"]}
    if attrs:
        lines.append("**Attributes:**")
        for k, v in attrs.items():
            key = k.replace("_", " ").title()
            val = str(v).strip()
            lines.append(f"{key}: {val}")
    return "\n".join(lines)

# =====================================================
# 🧭 GitHub issue management
# =====================================================
def get_existing_issues(repo, token):
    url = f"{GITHUB_API_URL}/{repo}/issues?state=all&labels=reqif-import&per_page=100"
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
    resp = requests.post(f"{GITHUB_API_URL}/{repo}/issues", headers=github_headers(token), json=data)
    if resp.status_code >= 300:
        print(f"❌ Failed to create issue for {req['id']}: {resp.text}")
    else:
        print(f"🆕 Created issue for {req['id']}")
    return resp.json()

def update_issue(repo, token, issue_number, req):
    data = {
        "title": f"[{req['id']}] {choose_title(req)}",
        "body": format_req_body(req),
        "state": "open",
    }
    resp = requests.patch(f"{GITHUB_API_URL}/{repo}/issues/{issue_number}", headers=github_headers(token), json=data)
    if resp.status_code >= 300:
        print(f"❌ Failed to update issue #{issue_number}: {resp.text}")
    else:
        print(f"♻️ Updated issue #{issue_number} ({req['id']})")
    return resp.json()

def close_issue(repo, token, issue_number, req_id):
    data = {"state": "closed"}
    resp = requests.patch(f"{GITHUB_API_URL}/{repo}/issues/{issue_number}", headers=github_headers(token), json=data)
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
        issue_map = {}
        for issue in issues:
            title = issue["title"]
            if title.startswith("[") and "]" in title:
                req_id = title.split("]")[0][1:]
                issue_map[req_id] = issue
        for req_id, req in reqs.items():
            if req_id in issue_map:
                issue = issue_map[req_id]
                if issue["title"] != f"[{req['id']}] {choose_title(req)}" or issue["body"] != format_req_body(req):
                    update_issue(repo_full_name, github_token, issue["number"], req)
            else:
                create_issue(repo_full_name, github_token, req)
        for req_id, issue in issue_map.items():
            if req_id not in reqs:
                close_issue(repo_full_name, github_token, issue["number"], req_id)
        print("✅ Synchronization complete.")
    except Exception:
        print("❌ Unexpected error during synchronization.")
        traceback.print_exc()

if __name__ == "__main__":
    sync_reqif_to_github()
