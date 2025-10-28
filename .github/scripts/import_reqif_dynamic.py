#!/usr/bin/env python3
"""
import_reqif_dynamic.py

- Parses .reqif files (robust XML-based parser; supports common namespaces)
- Creates/updates GitHub issues for each requirement
- Finds/creates ProjectV2 items and maps attribute values to project fields via GraphQL
- Hard-codes Requirement Label to "System Requirement" on the project field
"""

import os
import sys
import glob
import json
import requests
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List

# -------------------------
# Configuration / Environment
# -------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPOSITORY")  # "owner/repo"
PROJECT_NAME = os.getenv("PROJECT_NAME")  # optional: to pick specific Projects v2 by title

if not GITHUB_TOKEN or not REPO:
    print("❌ Missing GITHUB_TOKEN or GITHUB_REPOSITORY env vars.")
    sys.exit(1)

# REST headers for Issues API (token auth)
REST_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

# GraphQL headers (Bearer)
GRAPHQL_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Content-Type": "application/json",
}

GRAPHQL_URL = "https://api.github.com/graphql"
REST_API_BASE = f"https://api.github.com/repos/{REPO}"

# Hard-code Requirement Label mapping (user requested the label be hard-coded)
HARDCODED_REQUIREMENT_LABEL = "System Requirement"

# -------------------------
# 1) Find ReqIF file
# -------------------------
reqif_files = glob.glob("**/*.reqif", recursive=True) + glob.glob("**/*.reqifz", recursive=True)
if not reqif_files:
    print("❌ No .reqif/.reqifz file found in repository.")
    sys.exit(1)

REQIF_FILE = reqif_files[0]
print(f"📄 Found ReqIF file: {REQIF_FILE}")
print(f"📄 Parsing ReqIF: {REQIF_FILE}")

# -------------------------
# 2) ReqIF XML parsing (robust)
#    - builds a list of spec objects and attributes
# -------------------------
def parse_reqif(path: str) -> Dict[str, Dict[str, Any]]:
    """
    Returns mapping: { rid: { 'title': ..., 'attrs': { long_name: value, ... }, 'desc': ... } }
    Uses XML parsing with namespace handling and supports common ReqIF structure.
    """
    ns = {}
    tree = ET.parse(path)
    root = tree.getroot()

    # collect namespaces from the root element (common pattern)
    for k, v in root.attrib.items():
        if k.startswith("xmlns"):
            if ":" in k:
                nsname = k.split(":", 1)[1]
            else:
                nsname = ""
            ns[nsname] = v

    # fallback default ns (if no prefix)
    default_ns = ""
    # build tag helpers
    def qname(tag: str) -> str:
        # if default namespace declared, use it
        if '' in ns and ns['']:
            return f"{{{ns['']}}}{tag}"
        return tag

    # Build map of ATTRIBUTE-DEFINITION IDENTIFIER -> LONG-NAME (so DEFINITION REF maps to friendly name)
    # Search for SPEC-TYPES -> SPEC-OBJECT-TYPE -> SPEC-ATTRIBUTES -> ATTRIBUTE-DEFINITION-*
    attr_def_map = {}  # IDENTIFIER -> LONG-NAME
    for ad in root.findall(".//{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}ATTRIBUTE-DEFINITION-STRING"):
        ident = ad.attrib.get("IDENTIFIER") or ad.attrib.get("identifier")
        long_name = ad.attrib.get("LONG-NAME") or ad.attrib.get("long-name") or ad.findtext("{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}LONG-NAME") or ident
        if ident:
            attr_def_map[ident] = long_name

    # Also handle ATTRIBUTE-DEFINITION-STRING under different nesting (safe fallback)
    if not attr_def_map:
        for ad in root.findall(".//ATTRIBUTE-DEFINITION-STRING"):
            ident = ad.attrib.get("IDENTIFIER") or ad.attrib.get("identifier")
            long_name = ad.attrib.get("LONG-NAME") or ad.attrib.get("long-name") or ad.findtext("LONG-NAME") or ident
            if ident:
                attr_def_map[ident] = long_name

    # now iterate SPEC-OBJECTS
    results = {}
    spec_objects = root.findall(".//{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}SPEC-OBJECT")
    if not spec_objects:
        spec_objects = root.findall(".//SPEC-OBJECT")  # fallback

    for so in spec_objects:
        rid = so.attrib.get("IDENTIFIER") or so.attrib.get("identifier") or so.findtext("IDENTIFIER") or "REQ-UNKNOWN"
        long_name = so.attrib.get("LONG-NAME") or so.attrib.get("long-name") or so.findtext("{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}LONG-NAME") or so.findtext("LONG-NAME") or rid

        attrs = {}
        # Under each SPEC-OBJECT there is a VALUES element containing ATTRIBUTE-VALUE-* elements
        values_node = so.find("{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}VALUES")
        if values_node is None:
            values_node = so.find("VALUES")

        if values_node is not None:
            # find children like ATTRIBUTE-VALUE-STRING, ATTRIBUTE-VALUE-BOOLEAN, etc.
            for av in list(values_node):
                tag = av.tag
                # get DEFINITION REF
                defnode = av.find("{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}DEFINITION")
                if defnode is None:
                    defnode = av.find("DEFINITION")
                def_ref = defnode.attrib.get("REF") if defnode is not None else None

                # fetch the textual value stored in THE-VALUE or VALUE child
                valnode = av.find("{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}THE-VALUE")
                if valnode is None:
                    valnode = av.find("THE-VALUE")
                if valnode is None:
                    valnode = av.find("{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}VALUE")
                if valnode is None:
                    valnode = av.find("VALUE")

                value_text = None
                if valnode is not None and valnode.text is not None:
                    value_text = valnode.text.strip()
                else:
                    # sometimes the value is nested deeper or is attribute
                    if av.text and av.text.strip():
                        value_text = av.text.strip()

                key = def_ref or "UNKNOWN_DEF"
                # try mapping def_ref (which may be AD_ID) to long name
                friendly = attr_def_map.get(key, key)
                if value_text is not None:
                    attrs[friendly] = value_text

        # fallback: attempt to read child simple elements Title/Description
        title = attrs.get("Title") or attrs.get("Name") or long_name
        description = attrs.get("Description") or attrs.get("Desc") or ""

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
    print("❌ Failed to parse ReqIF file:", e)
    sys.exit(1)

print(f"✅ Extracted {len(requirements)} requirements from ReqIF.")

if not requirements:
    print("⚠️ No requirements found. Exiting.")
    sys.exit(0)

# -------------------------
# 3) GitHub Issue sync (REST)
# -------------------------
def rest_request(method: str, endpoint: str, **kwargs) -> requests.Response:
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

def find_issue_by_rid(issues, rid):
    # We store issue title as "RID: Title" to keep mapping stable
    prefix = f"{rid}:"
    for i in issues:
        if i["title"].startswith(prefix):
            return i
    return None

def create_issue_for_req(rid: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = {
        "title": f"{rid}: {info['title']}",
        "body": info.get("description") or info.get("body") or info.get("attrs_text") or "No description",
        # don't set labels here (we map requirement label via Project field; but we add the hard-coded label for visibility)
        "labels": [HARDCODED_REQUIREMENT_LABEL]
    }
    r = rest_request("POST", f"/repos/{REPO}/issues", json=payload)
    if r.ok:
        return r.json()
    return None

def update_issue(issue_number: int, info: Dict[str, Any]):
    payload = {"title": f"{info['identifier']}: {info['title']}", "body": info.get("description") or info.get("attrs_text")}
    r = rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json=payload)
    return r.ok

def close_issue(issue_number: int):
    rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json={"state": "closed"})

# Prepare attributes_text for issue body
for rid, info in requirements.items():
    attrs = info.get("attrs", {})
    lines = []
    for k, v in attrs.items():
        lines.append(f"**{k}:** {v}")
    info["attrs_text"] = "\n".join(lines) if lines else info.get("description", "")

# Fetch existing issues
existing_issues = list_all_issues()
existing_map_by_rid = {}
for iss in existing_issues:
    # try to extract rid from title "RID: title"
    if ":" in iss["title"]:
        rid_candidate = iss["title"].split(":", 1)[0]
        existing_map_by_rid[rid_candidate] = iss

# Sync issues (create / update)
created_or_updated_issues = {}  # rid -> issue dict
for rid, info in requirements.items():
    existing = existing_map_by_rid.get(rid)
    if existing:
        # update if body differs
        existing_body = existing.get("body") or ""
        if existing_body.strip() != info["attrs_text"].strip():
            ok = update_issue(existing["number"], {"identifier": rid, "title": info["title"], "attrs_text": info["attrs_text"], "description": info.get("description")})
            if ok:
                print(f"✏️ Updated issue for {rid}")
            else:
                print(f"⚠️ Failed to update issue for {rid}")
        else:
            print(f"↩️ No change for issue {rid}")
        created_or_updated_issues[rid] = existing
    else:
        created = create_issue_for_req(rid, {"title": info["title"], "description": info.get("description"), "attrs_text": info["attrs_text"]})
        if created:
            print(f"🆕 Created issue for {rid} -> #{created['number']}")
            created_or_updated_issues[rid] = created
        else:
            print(f"⚠️ Failed to create issue for {rid}")

# Close deleted issues: existing_rids not in requirements -> close
existing_rids = set(existing_map_by_rid.keys())
to_close = existing_rids - set(requirements.keys())
for rid in to_close:
    iss = existing_map_by_rid[rid]
    if iss and iss.get("state") != "closed":
        close_issue(iss["number"])
        print(f"🗑️ Closed deleted requirement issue: {rid}")

# -------------------------
# 4) Projects V2 & Field mapping (GraphQL)
#    - Get project (first or by PROJECT_NAME)
#    - Get fields and map by LONG-NAME (case-insensitive)
#    - For each issue, find or create ProjectV2 item and update field values:
#        - System Requirement ID -> system requirement id field
#        - Priority -> priority field
#        - Label (HARDCODED_REQUIREMENT_LABEL) -> label field (text)
# -------------------------
def run_graphql(query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(GRAPHQL_URL, headers=GRAPHQL_HEADERS, json=payload)
    if r.status_code != 200:
        raise Exception(f"GraphQL error {r.status_code}: {r.text}")
    data = r.json()
    if "errors" in data:
        # print and continue where possible
        print("⚠️ GraphQL returned errors:", data["errors"])
    return data

# 4.1 get repository projects v2 and their fields
query_projects = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
    projectsV2(first:50) {
      nodes {
        id
        title
        fields(first:100) {
          nodes {
            id
            name
            dataType
          }
        }
      }
    }
  }
}
"""
owner, repo_name = REPO.split("/")
projects_resp = run_graphql(query_projects, {"owner": owner, "name": repo_name})

project_nodes = projects_resp.get("data", {}).get("repository", {}).get("projectsV2", {}).get("nodes", []) or []
if not project_nodes:
    print("⚠️ No Projects V2 found in repository — skipping project field mapping.")
else:
    # choose project
    project = None
    if PROJECT_NAME:
        for p in project_nodes:
            if p.get("title") == PROJECT_NAME:
                project = p
                break
    if project is None:
        project = project_nodes[0]
    project_id = project["id"]
    print(f"📋 Using Project: {project.get('title')} ({project_id})")

    # build field map by normalized name
    fields = project.get("fields", {}).get("nodes", []) or []
    field_map = {f["name"].strip().lower(): f for f in fields if f.get("name")}
    # candidate field names we want to map to
    # NOTE: field names must match your project's field titles (case-insensitive)
    FIELD_NAME_SYSTEM_REQ_ID = "system requirement id"
    FIELD_NAME_PRIORITY = "priority"
    FIELD_NAME_LABEL = "requirement label"  # example; adapt if your field has a different name

    system_field = field_map.get(FIELD_NAME_SYSTEM_REQ_ID.lower())
    priority_field = field_map.get(FIELD_NAME_PRIORITY.lower())
    label_field = field_map.get(FIELD_NAME_LABEL.lower())

    # GraphQL helpers: find project item for issue (by issue number) or create one
    query_project_items = """
    query($projectId: ID!, $perPage:Int!) {
      node(id:$projectId) {
        ... on ProjectV2 {
          items(first:$perPage) {
            nodes {
              id
              content {
                ... on Issue {
                  number
                }
              }
            }
          }
        }
      }
    }
    """
    items_resp = run_graphql(query_project_items, {"projectId": project_id, "perPage": 100})
    item_nodes = items_resp.get("data", {}).get("node", {}).get("items", {}).get("nodes", []) or []
    # map by issue number
    project_item_by_issue = {}
    for it in item_nodes:
        cont = it.get("content")
        if cont and cont.get("number") is not None:
            project_item_by_issue[cont["number"]] = it

    # mutation to create project item (add issue to project)
    mutation_create_item = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item {
          id
        }
      }
    }
    """
    # mutation to update field
    mutation_update_field = """
    mutation($project:ID!, $item:ID!, $field:ID!, $value: String!) {
      updateProjectV2ItemFieldValue(input:{
        projectId: $project,
        itemId: $item,
        fieldId: $field,
        value: { text: $value }
      }) {
        projectV2Item { id }
      }
    }
    """

    def ensure_project_item_for_issue(issue_number: int, issue_node_id: str) -> Optional[str]:
        # if exists return id
        if issue_number in project_item_by_issue:
            return project_item_by_issue[issue_number]["id"]
        # else create one
        # contentId expects the issue node id (global node id); we need to fetch that via GraphQL
        # get issue node id:
        q = "query($owner:String!, $name:String!, $num:Int!){ repository(owner:$owner, name:$name) { issue(number:$num) { id } } }"
        res = run_graphql(q, {"owner": owner, "name": repo_name, "num": issue_number})
        node_id = res.get("data", {}).get("repository", {}).get("issue", {}).get("id")
        if not node_id:
            return None
        vars = {"projectId": project_id, "contentId": node_id}
        r = run_graphql(mutation_create_item, vars)
        item = r.get("data", {}).get("addProjectV2ItemById", {}).get("item")
        if item:
            item_id = item.get("id")
            # update local map
            project_item_by_issue[issue_number] = {"id": item_id}
            return item_id
        return None

    # update field helper
    def update_project_field(item_id: str, field_obj: Dict[str, Any], value: str):
        if not item_id or not field_obj or value is None:
            return
        vars = {"project": project_id, "item": item_id, "field": field_obj["id"], "value": value}
        run_graphql(mutation_update_field, vars)

    # iterate created_or_updated_issues and set fields
    for rid, issue in created_or_updated_issues.items():
        issue_number = issue.get("number")
        if not issue_number:
            # if REST create returned different shape, try to fetch by title
            continue
        # get project item id (create if missing)
        item_id = None
        if issue_number in project_item_by_issue:
            item_id = project_item_by_issue[issue_number]["id"]
        else:
            item_id = ensure_project_item_for_issue(issue_number, None)
        if not item_id:
            print(f"⚠️ Could not find/create Project item for issue #{issue_number}")
            continue

        # get the requirement attrs from requirements dict (we have rid)
        reqinfo = requirements.get(rid, {})
        attrs = reqinfo.get("attrs", {})

        # map and update fields:
        # System Requirement ID field gets the RID (hard mapping)
        if system_field:
            update_project_field(item_id, system_field, rid)

        # Priority field from attr (if present)
        priority_val = attrs.get("Priority") or attrs.get("PRIORITY") or attrs.get("Severity") or ""
        if priority_field and priority_val:
            update_project_field(item_id, priority_field, priority_val)

        # Label field: user asked to hard-code Requirement Label to "System Requirement"
        if label_field:
            update_project_field(item_id, label_field, HARDCODED_REQUIREMENT_LABEL)

        print(f"🔧 Mapped fields for issue #{issue_number} (RID={rid})")

print("✅ Completed ReqIF → GitHub synchronization (issues + project fields).")






