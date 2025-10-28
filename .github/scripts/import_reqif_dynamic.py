#!/usr/bin/env python3
import os
import sys
import glob
import json
import requests
from reqif.parser import ReqIFParser, ReqIFZParser

# ============================================================
# CONFIGURATION
# ============================================================
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPOSITORY")

if not GITHUB_TOKEN or not REPO:
    print("❌ Missing required GitHub environment variables.")
    sys.exit(1)

REST_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}
GRAPHQL_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json"
}
GRAPHQL_URL = "https://api.github.com/graphql"
BASE_URL = f"https://api.github.com/repos/{REPO}"

# ============================================================
# Locate ReqIF / ReqIFZ file
# ============================================================
reqif_files = glob.glob("**/*.reqif", recursive=True) + glob.glob("**/*.reqifz", recursive=True)
if not reqif_files:
    print("❌ No ReqIF or ReqIFZ file found in the repository.")
    sys.exit(1)

REQIF_FILE = reqif_files[0]
print(f"📄 Found ReqIF file: {REQIF_FILE}")

# ============================================================
# Parse ReqIF dynamically
# ============================================================
try:
    print(f"📄 Parsing ReqIF: {REQIF_FILE}")
    if REQIF_FILE.endswith(".reqifz"):
        bundle = ReqIFZParser.parse(REQIF_FILE)
    else:
        bundle = ReqIFParser.parse(REQIF_FILE)
except Exception as e:
    print(f"❌ Failed to parse ReqIF file: {e}")
    sys.exit(1)

core = getattr(bundle, "core_content", None) or bundle
spec_objects = getattr(core, "spec_objects", None) or []
print(f"🔍 Parsed {len(spec_objects)} SpecObjects from ReqIF")

# ============================================================
# Helper to determine Requirement Label
# ============================================================
def determine_requirement_label(attrs: dict) -> str:
    candidates = [
        k for k in attrs.keys()
        if k.lower() in ("label", "type", "category", "classification")
        or "type" in k.lower()
        or "label" in k.lower()
        or "category" in k.lower()
    ]
    for cand in candidates:
        val = str(attrs[cand]).lower()
        if "feature" in val:
            return "Major Feature"
        if "epic" in val or "function" in val:
            return "Epic/Function"
        if "story" in val or "requirement" in val:
            return "Story (Atomic Requirement)"
    return "Story (Atomic Requirement)"

# ============================================================
# Extract all attributes dynamically
# ============================================================
requirements = {}
for so in spec_objects:
    rid = getattr(so, "identifier", None) or f"REQ-{len(requirements) + 1}"
    attrs = {}
    values = getattr(so, "values", None) or getattr(so, "attribute_values", None) or []
    for v in values:
        name = getattr(v.definition, "long_name", None) or getattr(v.definition, "name", None)
        value = getattr(v, "the_value", None) or getattr(v, "value", None)
        if name:
            attrs[name.strip()] = str(value).strip()

    label_val = determine_requirement_label(attrs)
    attrs["Requirement Label"] = label_val

    title = attrs.get("Title") or attrs.get("Name") or rid
    desc_lines = [f"**{k}:** {v}" for k, v in attrs.items() if v]
    body = "\n".join(desc_lines)

    requirements[rid] = {"title": title, "body": body, "attrs": attrs}

print(f"✅ Extracted {len(requirements)} requirements.")
if not requirements:
    print("⚠️ No requirements found in the ReqIF file.")
    sys.exit(0)

# ============================================================
# GitHub Issues Handling (Create / Update / Close)
# ============================================================
def list_issues():
    issues = []
    page = 1
    while True:
        resp = requests.get(f"{BASE_URL}/issues?state=all&per_page=100&page={page}", headers=REST_HEADERS)
        if resp.status_code != 200:
            print("⚠️ Error listing issues:", resp.text)
            break
        batch = resp.json()
        if not batch:
            break
        issues.extend(batch)
        page += 1
    return issues

def find_issue(issues, rid):
    for i in issues:
        if i["title"].startswith(f"{rid}:"):
            return i
    return None

def create_issue(reqid, info):
    data = {"title": f"{reqid}: {info['title']}", "body": info["body"]}
    r = requests.post(f"{BASE_URL}/issues", headers=REST_HEADERS, json=data)
    return r.json() if r.status_code in (200, 201) else None

def update_issue(issue, info):
    num = issue["number"]
    data = {"title": f"{info['title']}", "body": info["body"]}
    requests.patch(f"{BASE_URL}/issues/{num}", headers=REST_HEADERS, json=data)

def close_issue(issue):
    num = issue["number"]
    requests.patch(f"{BASE_URL}/issues/{num}", headers=REST_HEADERS, json={"state": "closed"})

issues = list_issues()
existing_rids = {i["title"].split(":")[0] for i in issues}

# Create / Update Issues
for rid, info in requirements.items():
    existing = find_issue(issues, rid)
    if existing:
        print(f"✏️ Updating: {rid}")
        update_issue(existing, info)
    else:
        print(f"🆕 Creating: {rid}")
        create_issue(rid, info)

# Close deleted
to_close = existing_rids - set(requirements.keys())
for rid in to_close:
    print(f"🗑️ Closing deleted requirement: {rid}")
    issue = find_issue(issues, rid)
    if issue and issue["state"] != "closed":
        close_issue(issue)

# ============================================================
# Get Project + Field IDs and Map Values
# ============================================================
def run_graphql(query, variables=None):
    r = requests.post(GRAPHQL_URL, headers=GRAPHQL_HEADERS, json={"query": query, "variables": variables})
    if r.status_code != 200:
        raise Exception(f"GraphQL failed: {r.text}")
    return r.json()

query_projects = """
query($owner:String!, $repo:String!) {
  repository(owner:$owner, name:$repo) {
    projectsV2(first:10) {
      nodes {
        id
        title
        fields(first:50) {
          nodes { id name dataType }
        }
      }
    }
  }
}
"""
owner, name = REPO.split("/")
projects = run_graphql(query_projects, {"owner": owner, "repo": name})
project = projects["data"]["repository"]["projectsV2"]["nodes"][0]
project_id = project["id"]
fields = {f["name"].lower(): f for f in project["fields"]["nodes"]}
print(f"📋 Using project: {project['title']} ({project_id})")

priority_field = fields.get("priority")
reqid_field = fields.get("system requirement id")
label_field = fields.get("requirement label")

# ============================================================
# Assign Custom Field Values
# ============================================================
mutation_template = """
mutation($project:ID!, $item:ID!, $field:ID!, $value:String!) {
  updateProjectV2ItemFieldValue(
    input: {projectId:$project, itemId:$item, fieldId:$field, value:{text:$value}}
  ) {
    projectV2Item { id }
  }
}
"""

def update_field(item_id, field, value):
    if not field or not value:
        return
    vars = {"project": project_id, "item": item_id, "field": field["id"], "value": str(value)}
    run_graphql(mutation_template, vars)

# Find the items (issues) in the project and map values
query_items = """
query($project:ID!) {
  node(id:$project) {
    ... on ProjectV2 {
      items(first:100) {
        nodes { id content { ... on Issue { number title } } }
      }
    }
  }
}
"""
project_items = run_graphql(query_items, {"project": project_id})
items = project_items["data"]["node"]["items"]["nodes"]

for item in items:
    content = item.get("content", {})
    if not content:
        continue
    issue_number = content.get("number")
    title = content.get("title", "")
    rid = title.split(":")[0] if ":" in title else None
    if rid not in requirements:
        continue
    req = requirements[rid]
    attrs = req["attrs"]

    if priority_field and "Priority" in attrs:
        update_field(item["id"], priority_field, attrs["Priority"])
    if reqid_field:
        update_field(item["id"], reqid_field, rid)
    if label_field and attrs.get("Requirement Label"):
        update_field(item["id"], label_field, attrs["Requirement Label"])

print("✅ Completed ReqIF → GitHub synchronization with project fields.")


