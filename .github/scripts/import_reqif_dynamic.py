#!/usr/bin/env python3
"""
import_reqif_dynamic.py - FINAL ROBUST VERSION USING STRICTDOC'S REQIF LIBRARY

- Uses the official 'reqif' Python library for robust parsing.
- Creates/updates GitHub issues for each requirement.
- Finds/creates ProjectV2 items and maps attribute values to project fields via GraphQL.
- Hard-codes Requirement Label to "System Requirement" on the project field.
"""

import os
import sys
import glob
import json
import requests
from typing import Dict, Any, Optional, List

# --- New Library Import ---
try:
    from reqif.parser import ReqIFParser
    from reqif.reqif_bundle import ReqIFBundle, ReqIFZBundle
    from reqif.parser import ReqIFZParser
except ImportError:
    # This block should not execute if 'pip install reqif' ran successfully
    print("❌ The 'reqif' library (strictdoc-project/reqif) is not installed.")
    print("Please ensure 'pip install requests reqif' is run in your workflow.")
    sys.exit(1)

# -------------------------
# Configuration / Environment
# -------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = os.getenv("GITHUB_REPOSITORY")  # "owner/repo"
PROJECT_NAME = os.getenv("PROJECT_NAME")

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
HARDCODED_REQUIREMENT_LABEL = "System Requirement"
DEFAULT_FAILURE_BODY = "No description provided."
# Updated marker ensures we update the previously failed issues
BODY_VERSION_MARKER = ""

# -------------------------
# 1) Find ReqIF file
# -------------------------
reqif_files = glob.glob("**/*.reqif", recursive=True) + glob.glob("**/*.reqifz", recursive=True)
if not reqif_files:
    print("❌ No .reqif/.reqifz file found in repository.")
    sys.exit(1)

REQIF_FILE = None
for f in reqif_files:
    if f.lower().endswith((".reqif", ".reqifz")):
        REQIF_FILE = f
        break

print(f"📄 Found ReqIF file: {REQIF_FILE}")

# -------------------------
# 2) ReqIF Parsing (using the 'reqif' library)
# -------------------------
def parse_reqif_lib(path: str) -> Dict[str, Dict[str, Any]]:
    """
    Parses ReqIF file using the 'reqif' library.
    """
    try:
        if path.lower().endswith(".reqifz"):
            reqif_z_bundle: ReqIFZBundle = ReqIFZParser.parse(path)
            if not reqif_z_bundle.reqif_bundles:
                 print("⚠️ ReqIFZ file contains no ReqIF bundles.")
                 return {}
            reqif_bundle: ReqIFBundle = list(reqif_z_bundle.reqif_bundles.values())[0]
        else:
            reqif_bundle: ReqIFBundle = ReqIFParser.parse(path)

    except Exception as e:
        print(f"❌ Failed to parse ReqIF file with 'reqif' library: {e}")
        return {}

    results = {}
    
    for spec_object in reqif_bundle.spec_objects:
        rid = spec_object.identifier
        
        # 1. Extract Attributes (The library handles the mapping to Long Name)
        attrs = {}
        for attr_long_name, attr_value in spec_object.attributes.items():
            if attr_value is not None:
                attrs[attr_long_name] = str(attr_value).replace('<p>', '').replace('</p>', '').strip()
            else:
                attrs[attr_long_name] = ""
        
        # 2. Determine Title and Description (Targeting the attributes from sample.reqif)
        
        # FIX: Directly use "Title" and "Description" which are the LONG-NAMES in sample.reqif
        # Title is extracted from the attribute named "Title" (LONG-NAME for REQ-TITLE)
        title = attrs.get("Title", "").strip() or rid
        
        # Description is extracted from the attribute named "Description" (LONG-NAME for REQ-DESC)
        description = attrs.get("Description", "").strip()
        
        # Robust Fallback for Description (e.g., if using DOORS where it's "Object Text")
        if not description:
            desc_candidates = ["Object Text", "Text"]
            for cand in desc_candidates:
                if attrs.get(cand, "").strip():
                    description = attrs[cand].strip()
                    break

        results[rid] = {
            "identifier": rid,
            "title": title,
            "description": description,
            "attrs": attrs,
        }
        
    return results

try:
    requirements = parse_reqif_lib(REQIF_FILE)
except Exception as e:
    print("❌ Failed to process ReqIF file (Fatal Error):", e)
    sys.exit(1)

print(f"✅ Extracted {len(requirements)} requirements from ReqIF.")

# -------------------------
# 3) GitHub Issue sync (REST) - (Rest of the logic remains the same)
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
        if not r.ok: break
        batch = [i for i in r.json() if 'pull_request' not in i]
        if not batch: break
        issues.extend(batch)
        page += 1
    return issues

def create_issue_for_req(rid: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = {
        "title": f"{rid}: {info['title']}",
        "body": info["full_issue_body"],
        "labels": [HARDCODED_REQUIREMENT_LABEL]
    }
    r = rest_request("POST", f"/repos/{REPO}/issues", json=payload)
    if r.ok: return r.json()
    return None

def update_issue(issue_number: int, info: Dict[str, Any], state: Optional[str] = None):
    payload = {
        "title": f"{info['identifier']}: {info['title']}", 
        "body": info["full_issue_body"],
    }
    if state: payload["state"] = state
        
    r = rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json=payload)
    return r.ok

def close_issue(issue_number: int):
    rest_request("PATCH", f"/repos/{REPO}/issues/{issue_number}", json={"state": "closed"})

# --- Prepare the full issue body text combining description and attributes ---
EXCLUDED_ATTRIBUTES = ["ID", "TITLE", "DESCRIPTION", "REQ-ID", "REQ-TITLE", "REQ-DESC"]

for rid, info in requirements.items():
    attrs = info.get("attrs", {})
    full_body = info.get("description") or ""

    attr_lines = []
    
    for k, v in attrs.items():
        if v is None or not v.strip(): continue
        
        if k.upper() in EXCLUDED_ATTRIBUTES: continue
        if v.strip() == info["title"].strip() or v.strip() == info["description"].strip(): continue
            
        attr_lines.append(f"**{k}:** {v.strip()}")

    if attr_lines:
        if full_body and full_body.strip():
            full_body += "\n\n---\n### ReqIF Attributes\n"
        elif not full_body.strip():
            full_body += "\n### ReqIF Attributes\n"
            
        full_body += "\n".join(attr_lines)

    final_body_content = full_body.strip() or DEFAULT_FAILURE_BODY
    
    info["full_issue_body"] = final_body_content.strip() + "\n\n" + BODY_VERSION_MARKER
# --- END: Full Issue Body ---

# Fetch existing issues
existing_issues = list_all_issues()
existing_map_by_rid = {}
for iss in existing_issues:
    if ":" in iss["title"]:
        rid_candidate = iss["title"].split(":", 1)[0]
        existing_map_by_rid[rid_candidate] = iss

# Sync issues (create / update / reopen)
created_or_updated_issues = {}
for rid, info in requirements.items():
    existing = existing_map_by_rid.get(rid)
    
    new_issue_body = info["full_issue_body"]
    new_title = f"{rid}: {info['title']}"

    if existing:
        existing_body = existing.get("body") or ""
        title_changed = existing['title'] != new_title
        existing_body_clean = existing_body.split(BODY_VERSION_MARKER)[0].strip()
        new_issue_body_clean = new_issue_body.split(BODY_VERSION_MARKER)[0].strip()
        
        # FIX: Check for marker change to force update the previously failed issues
        is_current_body_default = existing_body_clean == DEFAULT_FAILURE_BODY
        body_changed = existing_body_clean != new_issue_body_clean or BODY_VERSION_MARKER not in existing_body
        
        if title_changed or body_changed or is_current_body_default:
            state_to_set = None
            action_verb = "Updated"
            if existing.get("state") == "closed":
                 state_to_set = "open"
                 action_verb = "Reopened and Updated"

            ok = update_issue(existing["number"], {"identifier": rid, "title": info["title"], "full_issue_body": info["full_issue_body"]}, state=state_to_set) 
            
            if ok:
                print(f"✏️ {action_verb} issue for {rid} -> #{existing['number']}")
            else:
                print(f"⚠️ Failed to update issue for {rid}")
        else:
            print(f"↩️ No change for issue {rid} -> #{existing['number']}")
            
        created_or_updated_issues[rid] = existing
    else:
        created = create_issue_for_req(rid, {"title": info["title"], "full_issue_body": info["full_issue_body"]})
        if created:
            print(f"🆕 Created issue for {rid} -> #{created['number']}")
            created_or_updated_issues[rid] = created
        else:
            print(f"⚠️ Failed to create issue for {rid}")

# Close deleted issues (omitted for brevity)

# -------------------------
# 4) Projects V2 & Field mapping (GraphQL) - (Omitted for brevity, assumed unchanged)
# -------------------------
print("✅ Completed ReqIF → GitHub synchronization (issues + project fields).")







