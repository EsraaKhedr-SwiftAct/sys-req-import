#!/usr/bin/env python3
"""
import_reqif_dynamic.py
----------------------------------
Parses .reqif files and syncs requirements as GitHub issues.
Each requirement becomes one GitHub issue.
"""

import os
import re
import xml.etree.ElementTree as ET
import requests

# ----------------- CONFIGURATION -----------------
GITHUB_API_URL = "https://api.github.com"
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "AhmedMaher-SwiftAct/Test-ReqIF")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

REQIF_FILE = "sample.reqif"  # Change if needed
HARD_CODED_LABEL = "System Requirement"
# -------------------------------------------------


def parse_reqif(reqif_path):
    """Parse ReqIF XML and extract requirements dynamically."""
    try:
        print(f"📄 Parsing ReqIF: {reqif_path}")
        tree = ET.parse(reqif_path)
        root = tree.getroot()

        reqs = []
        for spec_obj in root.findall(".//{*}SPEC-OBJECT"):
            req_data = {}
            req_data["identifier"] = spec_obj.attrib.get("IDENTIFIER", "Unknown-ID")

            # Attributes
            values = spec_obj.findall(".//{*}ATTRIBUTE-VALUE-STRING")
            attributes = {}
            for val in values:
                attr_ref = val.find(".//{*}DEFINITION")
                content = val.find(".//{*}THE-VALUE")
                if attr_ref is not None and content is not None:
                    name = attr_ref.attrib.get("REF", "Unnamed")
                    attributes[name] = content.text or ""

            # Extract title and description dynamically
            title = attributes.get("AD_TITLE", spec_obj.attrib.get("LONG-NAME", "Untitled Requirement"))
            description = attributes.get("AD_DESC", "No description provided.")
            req_data["title"] = title
            req_data["description"] = description
            req_data["attributes"] = attributes

            reqs.append(req_data)

        print(f"✅ Extracted {len(reqs)} requirements from ReqIF.")
        return reqs

    except Exception as e:
        raise RuntimeError(f"Failed to parse ReqIF file: {e}")


def fetch_all_issues(headers):
    """Fetch all issues from the repo."""
    url = f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/issues?state=all&per_page=100"
    r = requests.get(url, headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"Failed to fetch issues: {r.text}")
    return r.json()


def create_issue(title, body, labels, headers):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/issues"
    data = {"title": title, "body": body, "labels": labels}
    r = requests.post(url, headers=headers, json=data)
    if r.status_code == 201:
        print(f"✅ Created issue: {title}")
    else:
        print(f"❌ Failed to create issue: {title} -> {r.text}")


def update_issue(issue_number, title, body, labels, headers):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/issues/{issue_number}"
    data = {"title": title, "body": body, "labels": labels}
    r = requests.patch(url, headers=headers, json=data)
    if r.status_code == 200:
        print(f"✅ Issue #{issue_number} updated successfully.")
    else:
        print(f"❌ Failed to update issue #{issue_number}: {r.text}")


def reopen_issue(issue_number, headers):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/issues/{issue_number}"
    data = {"state": "open"}
    r = requests.patch(url, headers=headers, json=data)
    if r.status_code == 200:
        print(f"♻️ Reopened issue #{issue_number}.")
    else:
        print(f"❌ Failed to reopen issue #{issue_number}: {r.text}")


def close_issue(issue_number, headers):
    """Close an issue that no longer exists in ReqIF."""
    url = f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/issues/{issue_number}"
    data = {"state": "closed"}
    r = requests.patch(url, headers=headers, json=data)
    if r.status_code == 200:
        print(f"🔒 Closed issue #{issue_number} (not found in ReqIF).")
    else:
        print(f"❌ Failed to close issue #{issue_number}: {r.text}")


def sync_requirements_to_github(reqif_data):
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    existing_issues = fetch_all_issues(headers)
    existing_titles = {issue["title"]: issue for issue in existing_issues}

    reqif_titles = set([r["title"] for r in reqif_data])

    # --- SYNC ---
    for req in reqif_data:
        title = req.get("title", "Untitled Requirement")
        description = req.get("description", "No description provided.")
        req_id = req.get("identifier", "")
        attributes = req.get("attributes", {})

        labels = [HARD_CODED_LABEL]
        body = f"**System Requirement ID:** {req_id}\n\n**Description:** {description}\n\n"
        if attributes:
            body += "**Attributes:**\n"
            for key, value in attributes.items():
                body += f"- **{key}:** {value}\n"

        if title in existing_titles:
            issue = existing_titles[title]
            issue_number = issue["number"]
            current_title = issue.get("title", "").strip()
            current_body = issue.get("body", "").strip()
            issue_state = issue.get("state", "open")

            if current_title != title or current_body != body:
                print(f"🔄 Updating issue: {title}")
                update_issue(issue_number, title, body, labels, headers)

            if issue_state == "closed":
                reopen_issue(issue_number, headers)
        else:
            print(f"🆕 Creating new issue: {title}")
            create_issue(title, body, labels, headers)

    # --- CLOSE ISSUES MISSING IN REQIF ---
    for issue in existing_issues:
        title = issue["title"]
        issue_number = issue["number"]
        labels = [lbl["name"] for lbl in issue.get("labels", [])]
        if HARD_CODED_LABEL in labels and title not in reqif_titles and issue["state"] == "open":
            print(f"⚠️ Issue '{title}' no longer exists in ReqIF — closing it.")
            close_issue(issue_number, headers)


def main():
    try:
        if not os.path.exists(REQIF_FILE):
            raise FileNotFoundError(f"ReqIF file not found: {REQIF_FILE}")

        reqif_data = parse_reqif(REQIF_FILE)
        sync_requirements_to_github(reqif_data)
        print("✅ Sync completed successfully.")
    except Exception as e:
        print(f"❌ Error during sync: {e}")
        exit(1)


if __name__ == "__main__":
    main()







