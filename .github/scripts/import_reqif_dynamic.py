#!/usr/bin/env python3
"""
Universal ReqIF -> GitHub Issues + ProjectsV2 synchronizer.

Place in .github/scripts/import_reqif_universal.py and call from GH Actions.
Requires environment variables:
 - GITHUB_TOKEN
 - GITHUB_REPOSITORY (owner/repo)

Behaviour:
 - Finds first .reqif or .reqifz file in repo (recursive).
 - Parses using reqif library (ReqIFParser / ReqIFZParser).
 - Extracts every SpecObject's attributes dynamically (robust to schema variance).
 - Creates issues (title: "<REQID>: <title>") or updates existing ones.
 - Stores hidden metadata JSON in issue body (<!-- REQIF-META: {...} -->) to detect edits.
 - Closes issues for requirements removed from ReqIF (optional via CLOSE_MISSING_REQS env var).
 - Finds ProjectsV2 and fields, maps attributes to project fields by name heuristics and updates them.
"""
import os
import sys
import glob
import json
import hashlib
import re
import requests
from typing import Dict, Any, Tuple, Optional, List

# reqif parser imports
try:
    from reqif.parser import ReqIFParser, ReqIFZParser
except Exception as e:
    print("⚠️ Could not import reqif parser: ", e)
    print("Install reqif (e.g. pip install reqif or strictdoc).")
    sys.exit(1)

# ---------- Configuration & env ----------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPOSITORY")
CLOSE_MISSING = os.getenv("CLOSE_MISSING_REQS", "true").lower() in ("1", "true", "yes")
REQIF_SEARCH = os.getenv("REQIF_FILE", "")  # optional override path

if not GITHUB_TOKEN or not REPO:
    print("❌ Missing required GitHub environment variables (GITHUB_TOKEN, GITHUB_REPOSITORY).")
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

META_MARKER = "<!-- REQIF-META:"

# ---------- Helpers ----------
def sha1_of_obj(o: Any) -> str:
    """Return a stable sha1 of a JSON-serializable object."""
    j = json.dumps(o, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(j.encode("utf-8")).hexdigest()

def embed_meta_in_body(body: str, meta: Dict[str, Any]) -> str:
    """Append hidden metadata JSON at the end of issue body."""
    meta_json = json.dumps(meta, sort_keys=True, ensure_ascii=False)
    return f"{body}\n\n{META_MARKER}{meta_json} -->"

def extract_meta_from_body(body: str) -> Optional[Dict[str, Any]]:
    """Find meta JSON embedded in issue body. Returns dict or None."""
    if not body:
        return None
    idx = body.find(META_MARKER)
    if idx == -1:
        return None
    # find closing ' -->' after marker
    try:
        payload = body[idx + len(META_MARKER):]
        # remove trailing ' -->' if present
        if payload.endswith(" -->"):
            payload = payload[:-4]
        payload = payload.strip()
        return json.loads(payload)
    except Exception:
        return None

def run_graphql(query: str, variables: Dict = None) -> Dict:
    r = requests.post(GRAPHQL_URL, headers=GRAPHQL_HEADERS, json={"query": query, "variables": variables or {}})
    if r.status_code != 200:
        raise Exception(f"GraphQL HTTP {r.status_code}: {r.text}")
    js = r.json()
    if "errors" in js:
        raise Exception(f"GraphQL errors: {js['errors']}")
    return js

# ---------- 1) locate reqif file ----------
def find_reqif_file() -> str:
    if REQIF_SEARCH:
        if os.path.exists(REQIF_SEARCH):
            return REQIF_SEARCH
        else:
            raise FileNotFoundError(f"REQIF_FILE override specified but not found: {REQIF_SEARCH}")
    files = glob.glob("**/*.reqif", recursive=True) + glob.glob("**/*.reqifz", recursive=True)
    if not files:
        raise FileNotFoundError("No .reqif or .reqifz found in repository.")
    # prefer plain .reqif if both types present
    files_sorted = sorted(files, key=lambda p: (p.endswith(".reqifz"), p))
    return files_sorted[0]

# ---------- 2) parse reqif robustly ----------
def parse_reqif(path: str) -> List[Dict[str, Any]]:
    """
    Parse a ReqIF file and return a list of requirements (each as dict):
     { id: str, attributes: {name: value, ...}, title: str, body: str, checksum: str }
    """

    print(f"📄 Parsing ReqIF: {path}")
    try:
        if path.lower().endswith(".reqifz"):
            bundle = ReqIFZParser.parse(path)
        else:
            bundle = ReqIFParser.parse(path)
    except Exception as e:
        # Provide helpful debug output (some reqif files embed namespaces differently)
        raise RuntimeError(f"Failed to parse ReqIF file: {e}")

    # Many reqif libraries wrap the 'core' content; be defensive
    core = getattr(bundle, "core_content", None) or getattr(bundle, "core", None) or bundle

    # Try multiple attribute container names
    spec_objects = getattr(core, "spec_objects", None) or getattr(core, "specObjects", None) or getattr(core, "specifications", None) or []
    if getattr(core, "specObjects", None):
        spec_objects = core.specObjects

    # If the object is a dict or similar, attempt to find nested lists
    if isinstance(spec_objects, dict):
        # heuristic: flatten dict values if they look like lists
        maybe = []
        for v in spec_objects.values():
            if isinstance(v, (list, tuple)):
                maybe.extend(v)
        if maybe:
            spec_objects = maybe

    # Last-ditch: check bundle for 'core_content' with nested 'specObjects'
    if not spec_objects:
        # inspect attributes for anything that looks like spec objects
        for attrname in dir(core):
            attr = getattr(core, attrname, None)
            if isinstance(attr, (list, tuple)) and attr and hasattr(attr[0], "definition") or hasattr(attr[0], "identifier"):
                spec_objects = attr
                break

    # If still empty, return empty list
    if not spec_objects:
        print("⚠️ No spec objects found in parsed ReqIF; returning empty.")
        return []

    requirements = []
    for so in spec_objects:
        # Get identifier robustly
        rid = getattr(so, "identifier", None) or getattr(so, "id", None) or getattr(so, "identifierRef", None)
        if not rid:
            # try to find an attribute like 'ID' in values
            pass

        # Extract attribute values robustly: some libs use 'values', 'attribute_values', 'valuesList', etc.
        values = getattr(so, "values", None) or getattr(so, "attribute_values", None) or getattr(so, "attributeValues", None) or getattr(so, "valuesList", None) or []

        # Normalize to list
        if not isinstance(values, (list, tuple)):
            values = list(values) if values else []

        attrs = {}
        for v in values:
            # each 'v' may have definition (attribute definition) and value storage names differ across libs
            # attempt to read a human-friendly name: long_name, longname, name
            defn = getattr(v, "definition", None) or getattr(v, "definitionRef", None) or getattr(v, "attribute_definition", None)
            name = None
            if defn:
                name = getattr(defn, "long_name", None) or getattr(defn, "longname", None) or getattr(defn, "name", None)
            # if still none, try v's own name props
            if not name:
                name = getattr(v, "name", None) or getattr(v, "long_name", None)

            # value storage can be the_value, value, theValue, attribute_value, raw
            value = getattr(v, "the_value", None)
            if value is None:
                value = getattr(v, "value", None)
            if value is None:
                value = getattr(v, "theValue", None)
            if value is None:
                # sometimes value wrapped in objects (like XML elements)
                for candidate in ("string", "real", "integer", "boolean", "xhtml"):
                    value = getattr(v, candidate, None)
                    if value is not None:
                        break
            # fallback: try to stringify the whole object (safe)
            if value is None:
                try:
                    value = str(v)
                except Exception:
                    value = ""

            if name:
                attrs[str(name).strip()] = str(value).strip()

        # If we couldn't find rid, try common attribute names inside attrs
        if not rid:
            for k in ("ID", "Identifier", "ReqID", "Req-Id", "ReqId"):
                if k in attrs:
                    rid = attrs[k]
                    break

        # If still no rid, try to create a stable fingerprint using title + attrs
        title_guess = attrs.get("Title") or attrs.get("Name") or attrs.get("ShortName") or None

        if not rid:
            # generate synthetic stable id from checksum of attributes + title (not ideal but stable across runs if file unchanged)
            fingerprint = sha1_of_obj({"title": title_guess, "attrs": attrs})
            rid = f"REQ-{fingerprint[:10]}"

        # pick title heuristically
        title = attrs.get("Title") or attrs.get("Name") or title_guess or rid

        # create human-friendly issue body from attributes (all present attributes)
        lines = []
        # order attributes so "Title" and "ID" top if present
        ordered_keys = []
        for prefer in ("ID", "Identifier", "ReqID", "Title", "Name", "Description"):
            if prefer in attrs and prefer not in ordered_keys:
                ordered_keys.append(prefer)
        for k in sorted(attrs.keys()):
            if k not in ordered_keys:
                ordered_keys.append(k)
        for k in ordered_keys:
            v = attrs.get(k, "")
            lines.append(f"**{k}:** {v}")
        body = "\n\n".join(lines).strip()

        checksum = sha1_of_obj({"title": title, "attrs": attrs})

        requirements.append({"id": str(rid), "title": str(title), "body": body, "attrs": attrs, "checksum": checksum})

    return requirements

# ---------- 3) GitHub issue helpers ----------
def list_all_issues() -> List[Dict]:
    issues = []
    page = 1
    while True:
        resp = requests.get(f"{BASE_URL}/issues?state=all&per_page=100&page={page}", headers=REST_HEADERS)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to list issues: {resp.status_code} {resp.text}")
        batch = resp.json()
        if not batch:
            break
        issues.extend(batch)
        page += 1
    return issues

def find_issue_by_reqid(issues: List[Dict], reqid: str) -> Optional[Dict]:
    # We'll use the hidden meta in body when possible; fallback to title prefix "<REQID>:"
    for i in issues:
        meta = extract_meta_from_body(i.get("body", "") or "")
        if meta and meta.get("reqif_id") == reqid:
            return i
    # fallback: title startswith
    for i in issues:
        t = i.get("title", "")
        if t.startswith(f"{reqid}:") or t.split(":")[0] == reqid:
            return i
    return None

def create_github_issue(reqid: str, title: str, body: str, meta: Dict) -> Dict:
    payload = {"title": f"{reqid}: {title}", "body": embed_meta_in_body(body, meta)}
    r = requests.post(f"{BASE_URL}/issues", headers=REST_HEADERS, json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create issue: {r.status_code} {r.text}")
    return r.json()

def update_github_issue(issue_number: int, title: str, body: str) -> Dict:
    payload = {"title": title, "body": body}
    r = requests.patch(f"{BASE_URL}/issues/{issue_number}", headers=REST_HEADERS, json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Failed to update issue: {r.status_code} {r.text}")
    return r.json()

def close_github_issue(issue_number: int) -> Dict:
    r = requests.patch(f"{BASE_URL}/issues/{issue_number}", headers=REST_HEADERS, json={"state": "closed"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Failed to close issue #{issue_number}: {r.status_code} {r.text}")
    return r.json()

# ---------- 4) ProjectV2 helpers (fetch project and fields) ----------
def get_first_project_with_fields() -> Tuple[Optional[Dict], Dict[str, Dict]]:
    # Query first repository projectV2 and its fields (id, name, dataType)
    owner, name = REPO.split("/")
    query = """
    query($owner:String!, $repo:String!) {
      repository(owner:$owner, name:$repo) {
        projectsV2(first:20) {
          nodes {
            id
            title
            fields(first:200) {
              nodes {
                id
                name
                dataType
                settings
              }
            }
          }
        }
      }
    }
    """
    res = run_graphql(query, {"owner": owner, "repo": name})
    nodes = res["data"]["repository"]["projectsV2"]["nodes"]
    if not nodes:
        return None, {}
    project = nodes[0]
    fields = {}
    for f in project["fields"]["nodes"]:
        fields[f["name"].lower()] = f
    return project, fields

def get_project_items(project_id: str) -> List[Dict]:
    query = """
    query($project:ID!) {
      node(id:$project) {
        ... on ProjectV2 {
          items(first:500) {
            nodes {
              id
              content {
                ... on Issue { number title url }
                ... on DraftIssue { title }
              }
            }
          }
        }
      }
    }
    """
    res = run_graphql(query, {"project": project_id})
    items = res["data"]["node"]["items"]["nodes"]
    return items

def update_project_field_text(project_id: str, item_id: str, field_id: str, text_value: str):
    mutation = """
    mutation($project:ID!, $item:ID!, $field:ID!, $value:String!) {
      updateProjectV2ItemFieldValue(
        input: {projectId:$project, itemId:$item, fieldId:$field, value:{text:$value}}
      ) { projectV2Item { id } }
    }
    """
    vars = {"project": project_id, "item": item_id, "field": field_id, "value": text_value}
    try:
        run_graphql(mutation, vars)
    except Exception as e:
        print(f"⚠️ Failed to set project field text (item {item_id}): {e}")

# ---------- 5) Synchronization main ----------
def sync():
    reqif_path = find_reqif_file()
    print(f"📄 Found ReqIF file: {reqif_path}")

    requirements = parse_reqif(reqif_path)
    print(f"🔍 Extracted {len(requirements)} requirements from ReqIF")

    if not requirements:
        print("⚠️ No requirements found.")
        return

    # fetch issues
    issues = list_all_issues()
    print(f"📥 Found {len(issues)} existing issues in repo (including closed).")

    # build lookup of existing issues by reqid
    existing_reqids = set()
    issues_by_reqid = {}
    for i in issues:
        meta = extract_meta_from_body(i.get("body", "") or "")
        if meta and meta.get("reqif_id"):
            rid = meta.get("reqif_id")
        else:
            # fallback: title's prefix
            title = i.get("title", "")
            rid = title.split(":")[0] if ":" in title else title
        existing_reqids.add(rid)
        issues_by_reqid[rid] = i

    # create/update
    for req in requirements:
        rid = req["id"]
        meta = {"reqif_id": rid, "checksum": req["checksum"]}
        existing = issues_by_reqid.get(rid)
        if existing:
            # compare checksums
            existing_meta = extract_meta_from_body(existing.get("body", "") or {}) or {}
            existing_checksum = existing_meta.get("checksum")
            if existing_checksum == req["checksum"]:
                print(f"✔️ No change for {rid}, skipping update.")
            else:
                print(f"✏️ Updating issue for {rid} (changed).")
                # update: update visible title and body (keep meta)
                new_body = embed_meta_in_body(req["body"], meta)
                # title: keep "<RID>: <title>"
                new_title = f"{rid}: {req['title']}"
                try:
                    update_github_issue(existing["number"], new_title, new_body)
                except Exception as e:
                    print(f"⚠️ Failed to update issue for {rid}: {e}")
        else:
            print(f"🆕 Creating issue for {rid}.")
            try:
                created = create_github_issue(rid, req["title"], req["body"], meta)
                # add to map so project mapping later can find it
                issues_by_reqid[rid] = created
            except Exception as e:
                print(f"⚠️ Failed to create issue for {rid}: {e}")

    # close deleted (if requested)
    if CLOSE_MISSING:
        current_reqids = {r["id"] for r in requirements}
        removed = existing_reqids - current_reqids
        for rid in removed:
            issue = issues_by_reqid.get(rid)
            if not issue:
                continue
            if issue.get("state") == "closed":
                continue
            try:
                print(f"🗑️ Closing deleted requirement issue: {rid}")
                close_github_issue(issue["number"])
            except Exception as e:
                print(f"⚠️ Failed to close issue for {rid}: {e}")

    # ---------- Project field mapping ----------
    try:
        project, fields = get_first_project_with_fields()
    except Exception as e:
        print(f"⚠️ Could not fetch ProjectV2 info: {e}")
        project = None
        fields = {}

    if not project:
        print("ℹ️ No ProjectsV2 found; skipping project field mapping.")
        return

    project_id = project["id"]
    print(f"📋 Using project '{project['title']}' ({project_id}) with {len(fields)} fields.")

    # heuristics: try to map these three target concepts from ReqIF attributes to project fields:
    #  - Priority  -> field named 'priority' or similar
    #  - System Requirement ID -> field named 'system requirement id', 'req id' etc.
    #  - Requirement Label -> field named 'requirement label', 'label', 'type', etc.
    # We'll also attempt to map any attribute names that exactly match project field names (case-insensitive).
    # Build lowercased field map (done already).
    # Now build mapping per requirement: for each created/updated issue, find corresponding project item and update fields.

    # get project items
    try:
        items = get_project_items(project_id)
    except Exception as e:
        print(f"⚠️ Failed to get project items: {e}")
        return

    # Build map: issue number => project item id
    issue_to_item = {}
    for it in items:
        content = it.get("content") or {}
        if not content:
            continue
        if content.get("number"):
            issue_to_item[content["number"]] = it["id"]

    # field alias map
    field_aliases = {
        "priority": ["priority", "prio"],
        "system requirement id": ["system requirement id", "system requirement", "reqid", "req id", "id", "identifier"],
        "requirement label": ["requirement label", "label", "type", "category"]
    }

    # reverse alias => field id lookup
    alias_to_field = {}
    for fname_lower, fld in fields.items():
        for alias_list in field_aliases.values():
            for alias in alias_list:
                if fname_lower == alias.lower():
                    alias_to_field[alias.lower()] = fld

    # Also build a mapping from attribute-names to field definitions by exact match
    # e.g., if reqif attribute "Priority" and project has a field named "Priority" (case-insensitive)
    for fld_name_lower, fld in fields.items():
        alias_to_field[fld_name_lower] = fld

    # For each requirement, update project fields if possible
    for req in requirements:
        rid = req["id"]
        # find issue number first
        issue = issues_by_reqid.get(rid)
        if not issue:
            continue
        issue_number = issue.get("number")
        item_id = issue_to_item.get(issue_number)
        if not item_id:
            # no project item for this issue (issue may not be added to project) -> skip
            continue

        attrs = req["attrs"] or {}
        # normalize attribute keys lowercased for matching
        attrs_lower = {k.lower(): v for k, v in attrs.items()}

        # set system requirement id field (if exists)
        # find best field for system requirement id
        sr_field = None
        for alias in field_aliases["system requirement id"]:
            sr_field = alias_to_field.get(alias)
            if sr_field:
                break
        if not sr_field:
            # fallback: find first field name that contains 'req' or 'id' tokens
            for nm, f in fields.items():
                if "req" in nm or "id" in nm:
                    sr_field = f
                    break

        if sr_field:
            # value: use rid or attribute if exists
            candidate = attrs.get("ID") or attrs.get("Identifier") or attrs.get("ReqID") or rid
            try:
                update_project_field_text(project_id, item_id, sr_field["id"], candidate)
            except Exception as e:
                print(f"⚠️ Failed mapping SR id for {rid}: {e}")

        # set priority field
        pr_field = None
        for alias in field_aliases["priority"]:
            pr_field = alias_to_field.get(alias)
            if pr_field:
                break
        if pr_field:
            # search in attributes common priority names
            pr_val = None
            for candidate_key in ("priority", "Priority", "PRIORITY", "prio"):
                if candidate_key in attrs:
                    pr_val = attrs[candidate_key]
                    break
            # fallback to "Severity" etc.
            if not pr_val:
                for k in attrs_lower:
                    if "priority" in k or "severity" in k:
                        pr_val = attrs[k]
                        break
            if pr_val:
                update_project_field_text(project_id, item_id, pr_field["id"], pr_val)

        # set requirement label field
        lb_field = None
        for alias in field_aliases["requirement label"]:
            lb_field = alias_to_field.get(alias)
            if lb_field:
                break
        if lb_field:
            # find likely label attr
            label_val = None
            for candidate_key in ("Label", "label", "Type", "type", "Category", "category"):
                if candidate_key in attrs:
                    label_val = attrs[candidate_key]
                    break
            if not label_val:
                # fallback first non-empty attribute besides Title/ID/Description
                for k, v in attrs.items():
                    if k.lower() not in ("title", "id", "description", "name"):
                        if v:
                            label_val = v
                            break
            if label_val:
                update_project_field_text(project_id, item_id, lb_field["id"], label_val)

        # Also, attempt to map any attribute name that exactly matches a project field name
        for attr_name, attr_val in attrs.items():
            fld = alias_to_field.get(attr_name.lower())
            if fld:
                try:
                    update_project_field_text(project_id, item_id, fld["id"], attr_val)
                except Exception as e:
                    print(f"⚠️ Failed mapping attribute '{attr_name}' to field '{fld.get('name')}' for {rid}: {e}")

    print("✅ Completed synchronization.")

# ---------- script entry ----------
if __name__ == "__main__":
    try:
        sync()
    except Exception as e:
        print("❌ Error during sync:", e)
        sys.exit(1)

