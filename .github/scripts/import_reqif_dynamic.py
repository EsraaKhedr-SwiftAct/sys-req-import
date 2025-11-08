#!/usr/bin/env python3
import os
import sys
import glob
import traceback
import requests
import html

# Universal ReqIF parser
from reqif_parser_full import ReqIFParser  # Must handle EA/Polarion/DOORS/Jama

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
    parser.parse()

    # The parser stores requirements in parser.requirements (dict of id → object)
    req_objects = parser.get_all_requirements()

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
GITHUB_API_URL = "https://api.github.com/repos"

def github_headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

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
# GitHub issue management
# -------------------------
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


# -------------------------
# Main synchronization
# -------------------------
def sync_reqif_to_github():
    github_token = os.getenv("GITHUB_TOKEN")
    repo_full_name = os.getenv("GITHUB_REPOSITORY")
    if not github_token or not repo_full_name:
        print("❌ Missing GITHUB_TOKEN or GITHUB_REPOSITORY.")
        sys.exit(1)

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

        # Create or update issues
        for req_id, req in reqs.items():
            if req_id in issue_map:
                issue = issue_map[req_id]
                if issue["title"] != f"[{req['id']}] {choose_title(req)}" or issue["body"] != format_req_body(req):
                    update_issue(repo_full_name, github_token, issue["number"], req)
            else:
                create_issue(repo_full_name, github_token, req)

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

