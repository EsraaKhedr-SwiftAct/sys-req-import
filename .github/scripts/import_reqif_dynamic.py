#!/usr/bin/env python3
"""
import_reqif_dynamic.py
------------------------
Imports requirements from .reqif into GitHub Issues and syncs with a GitHub Project (V2).

✅ Parses .reqif using StrictDoc (fix for broken reqif library)
✅ Creates/updates issues
✅ Closes removed ones
✅ Adds/updates issues in GitHub Project V2 via GraphQL
"""

import os
import sys
import glob
import json
import requests
import traceback
from typing import Dict, Any, List

# ----------------------------------------------------------------------
# 🧩 StrictDoc Parser (instead of reqif library)
# ----------------------------------------------------------------------
try:
    from strictdoc.export.reqif.reqif_importer import ReqIFImporter
except ImportError:
    print("❌ Missing 'strictdoc' package. Please install it via: pip install strictdoc")
    sys.exit(1)

# ----------------------------------------------------------------------
# ⚙️ Configuration
# ----------------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPOSITORY")
PROJECT_NAME = os.getenv("PROJECT_NAME")

if not GITHUB_TOKEN or not REPO:
    print("❌ Missing GITHUB_TOKEN or GITHUB_REPOSITORY environment variables.")
    sys.exit(1)

REST_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}
GRAPHQL_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json",
}
GRAPHQL_URL = "https://api.github.com/graphql"
HARDCODED_REQUIREMENT_LABEL = "System Requirement"

# ----------------------------------------------------------------------
# 1️⃣ Locate .reqif
# ----------------------------------------------------------------------
reqif_files = glob.glob("**/*.reqif", recursive=True) + glob.glob("**/*.reqifz", recursive=True)
if not reqif_files:
    print("❌ No .reqif or .reqifz file found in repository.")
    sys.exit(1)

REQIF_FILE = reqif_files[0]
print(f"📄 Found ReqIF file: {REQIF_FILE}")
print("📄 Parsing using StrictDoc ReqIFImporter...")

# ----------------------------------------------------------------------
# 2️⃣ Parse .reqif
# ----------------------------------------------------------------------
def parse_reqif(path: str) -> Dict[str, Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        importer = ReqIFImporter()
        reqif_tree = importer.import_from_string(content)
        documents = list(reqif_tree.document_iterator)
    except Exception as e:
        print("❌ Failed to parse ReqIF file.")
        traceback.print_exc()
        raise Exception(f"Error during ReqIF parsing: {type(e).__name__}: {e}")

    results = {}
    for doc in documents:
        for req in getattr(doc, "requirements", []):
            rid = req.uid or req.identifier or req.title or "Unknown"
            title = req.title or req.uid or "Untitled"
            description = req.statement or ""
            attrs = {}
            if hasattr(req, "custom_fields") and req.custom_fields:
                for k, v in req.custom_fields.items():
                    attrs[k] = str(v)

            results[rid] = {
                "identifier": rid,
                "title": title,
                "description": description,
                "attrs": attrs,
            }
    return results

try:
    requirements = parse_reqif(REQIF_FILE)
except Exception as e:
    print(f"❌ {e}")
    sys.exit(1)

print(f"✅ Parsed {len(requirements)} requirements from {REQIF_FILE}")

if not requirements:
    print("⚠️ No requirements found in the file.")
    sys.exit(0)

# ----------------------------------------------------------------------
# 3️⃣ GitHub REST API Helpers
# ----------------------------------------------------------------------
def rest_request(method: str, endpoint: str, **kwargs):
    url = f"https://api.github.com{endpoint}"
    r = requests.request(method, url, headers=REST_HEADERS, **kwargs)
    if not r.ok:
        print(f"⚠️ REST API {method} {endpoint} -> {r.status_code}: {r.text}")
    return r

def list_all_issues() -> List[Dict[str, Any]]:
    issues = []
    page = 1
    while True:
        r = rest_request("GET", f"/repos/{REPO}/issues?state=all&per_page=100&page={page}")
        if not r.ok:
            break
        batch = r.json()
        if not batch:
            break
        issues.extend(batch)
        page += 1
    return issues

def create_issue(rid: str, info: Dict[str, Any]):
    body = info["description"] or "No description"
    if info.get("attrs_text"):
        body += "\n\n" + info["attrs_text"]
    payload = {
        "title": f"{rid}: {info['title']}",
        "body": body,
        "labels": [HARDCODED_REQUIREMENT_LABEL],
    }
    r = rest_request("POST", f"/repos/{REPO}/issues", json=payload)
    return r.json() if r.ok else None

def update_issue(issue_number: int, info: Dict[str, Any]):
    body = info["description"] or "No description"
    if info.get("attrs_text"):
        body += "\n\n" + info["attrs_text"]
    payload = {
        "title": f"{info['identifier']}: {info['title']}",
        "body": body,
    }
    return rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json=payload).ok

def close_issue(issue_number: int):
    rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json={"state": "closed"})

# ----------------------------------------------------------------------
# 4️⃣ Prepare attributes and sync issues
# ----------------------------------------------------------------------
for rid, info in requirements.items():
    attrs = info.get("attrs", {})
    if attrs:
        lines = ["---", "### ReqIF Attributes"]
        for k, v in attrs.items():
            lines.append(f"**{k}:** {v}")
        info["attrs_text"] = "\n".join(lines)
    else:
        info["attrs_text"] = ""

existing_issues = list_all_issues()
existing_map = {i["title"].split(":")[0]: i for i in existing_issues if ":" in i["title"]}

for rid, info in requirements.items():
    if rid in existing_map:
        issue = existing_map[rid]
        updated = update_issue(issue["number"], info)
        if updated:
            print(f"✏️ Updated issue for {rid}")
        else:
            print(f"⚠️ Failed to update {rid}")
    else:
        created = create_issue(rid, info)
        if created:
            print(f"🆕 Created issue #{created['number']} for {rid}")
        else:
            print(f"⚠️ Failed to create {rid}")

# Close deleted issues
to_close = set(existing_map.keys()) - set(requirements.keys())
for rid in to_close:
    issue = existing_map[rid]
    if issue["state"] != "closed":
        close_issue(issue["number"])
        print(f"🗑️ Closed deleted requirement {rid}")

# ----------------------------------------------------------------------
# 5️⃣ GitHub ProjectV2 GraphQL Mapping
# ----------------------------------------------------------------------
def graphql_query(query: str, variables=None):
    r = requests.post(GRAPHQL_URL, headers=GRAPHQL_HEADERS, json={"query": query, "variables": variables})
    if not r.ok or "errors" in r.text:
        print("⚠️ GraphQL query failed:", r.text)
    return r.json()

# Fetch organization or user login
owner, _ = REPO.split("/")

# Find the project by name
query_project = """
query($login: String!, $first: Int!) {
  user(login: $login) {
    projectsV2(first: $first) {
      nodes { id title }
    }
  }
}
"""
resp = graphql_query(query_project, {"login": owner, "first": 20})
project_nodes = resp.get("data", {}).get("user", {}).get("projectsV2", {}).get("nodes", [])
project = next((p for p in project_nodes if p["title"] == PROJECT_NAME), None)
if not project:
    print(f"⚠️ Project '{PROJECT_NAME}' not found for user '{owner}'.")
    sys.exit(0)

project_id = project["id"]
print(f"📁 Found ProjectV2 '{PROJECT_NAME}' (id={project_id})")

# Add all created/updated issues to the project
for rid, info in requirements.items():
    issue = existing_map.get(rid)
    if not issue:
        continue
    content_id = issue["node_id"]
    mutation = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item { id }
      }
    }
    """
    result = graphql_query(mutation, {"projectId": project_id, "contentId": content_id})
    if "errors" not in str(result):
        print(f"📌 Linked issue {rid} to project {PROJECT_NAME}")
    else:
        print(f"⚠️ Failed to link issue {rid} to project")

print("✅ Done syncing issues and linking to project.")


