#!/usr/bin/env python3
import os
import sys
import glob
import requests
from reqif.parser import ReqIFParser

# ============================================================
# CONFIG
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
# 1️⃣ Locate .reqif file
# ============================================================
reqif_files = glob.glob("**/*.reqif", recursive=True)
if not reqif_files:
    print("❌ No ReqIF file found in the repository.")
    sys.exit(1)

REQIF_FILE = reqif_files[0]
print(f"📄 Found ReqIF file: {REQIF_FILE}")

# ============================================================
# 2️⃣ Parse ReqIF file dynamically (universal)
# ============================================================
try:
    print(f"📄 Parsing ReqIF: {REQIF_FILE}")
    parser = ReqIFParser()
    with open(REQIF_FILE, "r", encoding="utf-8") as f:
        xml_content = f.read()
    reqif_document = parser.parse_string(xml_content)
    print("✅ ReqIF file parsed successfully.")
except Exception as e:
    print(f"❌ Failed to parse ReqIF file: {e}")
    sys.exit(1)

# ============================================================
# 3️⃣ Extract dynamic requirements
# ============================================================
requirements = {}
try:
    core = getattr(reqif_document, "core_content", None)
    spec_objects = getattr(core, "spec_objects", None) or []
    print(f"🔍 Parsed {len(spec_objects)} SpecObjects from ReqIF")

    for so in spec_objects:
        rid = getattr(so, "identifier", None) or f"REQ-{len(requirements)+1}"
        attrs = {}

        # extract all attributes dynamically
        values = getattr(so, "values", None) or getattr(so, "attribute_values", None) or []
        for v in values:
            defn = getattr(v, "definition", None)
            name = getattr(defn, "long_name", None) or getattr(defn, "identifier", None)
            value = getattr(v, "the_value", None) or getattr(v, "value", None)
            if name:
                attrs[name.strip()] = str(value).strip()

        # Title and description
        title = attrs.get("Title") or attrs.get("Name") or rid
        desc_lines = [f"**{k}:** {v}" for k, v in attrs.items() if v]
        body = "\n".join(desc_lines)

        requirements[rid] = {"title": title, "body": body, "attrs": attrs}

except Exception as e:
    print(f"❌ Failed to extract requirements: {e}")
    sys.exit(1)

print(f"✅ Extracted {len(requirements)} requirements.")

if not requirements:
    print("⚠️ No requirements found in the ReqIF file.")
    sys.exit(0)

# ============================================================
# 4️⃣ GitHub Issues Handling (Create / Update / Close)
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
    data = {"title": f"{info['title']}", "body": info["body"]}
    requests.patch(f"{BASE_URL}/issues/{num}", headers=REST_HEADERS, json=data)

def close_issue(issue):
    num = issue["number"]
    requests.patch(f"{BASE_URL}/issues/{num}", headers=REST_HEADERS, json={"state": "closed"})

issues = list_issues()
existing_rids = {i["title"].split(":")[0] for i in issues}

# Create or update issues
for rid, info in requirements.items():
    existing = find_issue(issues, rid)
    if existing:
        print(f"✏️ Updating: {rid}")
        update_issue(existing, info)
    else:
        print(f"🆕 Creating: {rid}")
        create_issue(rid, info)

# Close deleted ones
to_close = existing_rids - set(requirements.keys())
for rid in to_close:
    print(f"🗑️ Closing deleted requirement: {rid}")
    issue = find_issue(issues, rid)
    if issue and issue["state"] != "closed":
        close_issue(issue)

# ============================================================
# 5️⃣ Get Project & Field IDs (System Requirement ID, Priority, Label)
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

project_nodes = projects["data"]["repository"]["projectsV2"]["nodes"]
if not project_nodes:
    print("⚠️ No GitHub Projects found.")
    sys.exit(0)

project = project_nodes[0]
project_id = project["id"]
fields = {f["name"].lower(): f for f in project["fields"]["nodes"]}
print(f"📋 Using project: {project['title']} ({project_id})")

priority_field = fields.get("priority")
reqid_field = fields.get("system requirement id")
label_field = fields.get("requirement label")

# ============================================================
# 6️⃣ Update Custom Field Values
# ============================================================
mutation_update_field = """
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
    variables = {
        "project": project_id,
        "item": item_id,
        "field": field["id"],
        "value": str(value)
    }
    run_graphql(mutation_update_field, variables)

# Retrieve project items
query_project_items = """
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
project_items = run_graphql(query_project_items, {"project": project_id})
items = project_items["data"]["node"]["items"]["nodes"]

for item in items:
    content = item.get("content", {})
    if not content:
        continue
    issue_title = content.get("title", "")
    rid = issue_title.split(":")[0] if ":" in issue_title else None
    if rid not in requirements:
        continue

    req = requirements[rid]
    attrs = req["attrs"]

    # Priority mapping
    if priority_field and "Priority" in attrs:
        update_field(item["id"], priority_field, attrs["Priority"])

    # System Requirement ID mapping
    if reqid_field:
        update_field(item["id"], reqid_field, rid)

    # Requirement Label mapping (3 options)
    if label_field:
        label_value = attrs.get("Label", "").lower()
        if "safety" in label_value:
            label_val = "Safety"
        elif "functional" in label_value:
            label_val = "Functional"
        else:
            label_val = "Story (Atomic Requirement)"
        update_field(item["id"], label_field, label_val)

print("✅ Completed ReqIF → GitHub synchronization with project fields.")



