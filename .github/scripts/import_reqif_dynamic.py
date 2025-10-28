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

# ============================================================
# 1️⃣ Locate .reqif or .reqifz
# ============================================================
reqif_files = glob.glob("**/*.reqif", recursive=True) + glob.glob("**/*.reqifz", recursive=True)
if not reqif_files:
    print("❌ No ReqIF or ReqIFZ file found.")
    sys.exit(1)

REQIF_FILE = reqif_files[0]
print(f"📄 Found ReqIF file: {REQIF_FILE}")

# ============================================================
# 2️⃣ Parse ReqIF File (Universal)
# ============================================================
print(f"📄 Parsing ReqIF: {REQIF_FILE}")

try:
    if REQIF_FILE.endswith(".reqifz"):
        bundle = ReqIFZParser.parse(REQIF_FILE)
    else:
        parser = ReqIFParser()
        try:
            # Try newer parser interface
            with open(REQIF_FILE, "r", encoding="utf-8") as f:
                xml_data = f.read()
            if hasattr(parser, "parse_string"):
                reqif_document = parser.parse_string(xml_data)
            else:
                # Fallback for older versions
                with open(REQIF_FILE, "r", encoding="utf-8") as f:
                    reqif_document = parser.parse(f)
            bundle = reqif_document
        except Exception as e:
            raise RuntimeError(f"Failed to parse ReqIF file: {e}")
except Exception as e:
    print(f"❌ Failed to parse ReqIF file: {e}")
    sys.exit(1)

print("✅ ReqIF file parsed successfully.")

# ============================================================
# 3️⃣ Extract Requirements Dynamically
# ============================================================
core = getattr(bundle, "core_content", None) or bundle
spec_objects = getattr(core, "spec_objects", None) or []
print(f"🔍 Parsed {len(spec_objects)} SpecObjects")

requirements = {}
for so in spec_objects:
    rid = getattr(so, "identifier", None) or f"REQ-{len(requirements)+1}"
    attrs = {}
    values = getattr(so, "values", None) or getattr(so, "attribute_values", None) or []

    for v in values:
        name = getattr(v.definition, "long_name", None) or getattr(v.definition, "name", None)
        value = getattr(v, "the_value", None) or getattr(v, "value", None)
        if name:
            attrs[name.strip()] = str(value).strip()

    title = attrs.get("Title") or attrs.get("Name") or rid
    desc_lines = [f"**{k}:** {v}" for k, v in attrs.items() if v]
    body = "\n".join(desc_lines)
    requirements[rid] = {"title": title, "body": body, "attrs": attrs}

print(f"✅ Extracted {len(requirements)} requirements.")

if not requirements:
    print("⚠️ No requirements found in the ReqIF file.")
    sys.exit(0)

# ============================================================
# 4️⃣ GitHub Issue Management
# ============================================================
BASE_URL = f"https://api.github.com/repos/{REPO}"

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
    return r.json() if r.status_code in (200,201) else None

def update_issue(issue, info):
    num = issue["number"]
    data = {"title": f"{reqid}: {info['title']}", "body": info["body"]}
    requests.patch(f"{BASE_URL}/issues/{num}", headers=REST_HEADERS, json=data)

def close_issue(issue):
    num = issue["number"]
    requests.patch(f"{BASE_URL}/issues/{num}", headers=REST_HEADERS, json={"state": "closed"})

issues = list_issues()
existing_rids = {i["title"].split(":")[0] for i in issues}

# Sync Issues
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
# 5️⃣ Fetch Project + Field IDs from GitHub
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

if not projects["data"]["repository"]["projectsV2"]["nodes"]:
    print("⚠️ No projects found.")
    sys.exit(0)

project = projects["data"]["repository"]["projectsV2"]["nodes"][0]
project_id = project["id"]
fields = {f["name"].lower(): f for f in project["fields"]["nodes"]}
print(f"📋 Using project: {project['title']} ({project_id})")

# Map Fields Dynamically
priority_field = fields.get("priority")
reqid_field = fields.get("system requirement id")
label_field = fields.get("requirement label")

# ============================================================
# 6️⃣ Update Field Values Dynamically
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

# Get project issues
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

# ============================================================
# 7️⃣ Map Label Based on ReqIF Content
# ============================================================
def map_label(attrs):
    label = attrs.get("Label", "").lower()
    if "safety" in label:
        return "Safety"
    elif "performance" in label:
        return "Performance"
    elif "functional" in label:
        return "Functional"
    return "Functional"  # default fallback

for item in items:
    content = item.get("content", {})
    if not content:
        continue
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
    if label_field:
        update_field(item["id"], label_field, map_label(attrs))

print("✅ Completed ReqIF → GitHub synchronization with dynamic labels and fields.")




