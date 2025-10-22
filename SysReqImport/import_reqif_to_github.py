#!/usr/bin/env python3
"""import_reqif_to_github.py
Real version: parses .reqif files and creates/synchronizes GitHub issues.
Behavior:
  - If GITHUB_TOKEN and GITHUB_REPOSITORY are provided (via Action), it will create/manage issues.
  - It avoids duplicates by checking existing issues for the same requirement ID.
  - NEW: It closes any open 'requirement' issues in GitHub that are no longer found in the latest ReqIF file(s).
  - If REQIF_FILE env var is set, it will parse that path; otherwise it scans the repo for any .reqif files.
"""
import os
import sys
import xml.etree.ElementTree as ET
import requests
from pathlib import Path
from urllib.parse import urljoin
import time
import re

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
GITHUB_REPOSITORY = os.getenv('GITHUB_REPOSITORY')  # owner/repo
REQIF_FILE = os.getenv('REQIF_FILE')  # optional; if empty we search for any .reqif files
LABELS = ['requirement']

if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
    print('ERROR: GITHUB_TOKEN and GITHUB_REPOSITORY environment variables are required to create GitHub issues.')
    print('Set them in your environment or run the script inside GitHub Actions where they are available.')
    sys.exit(1)

API_BASE = f'https://api.github.com/repos/{GITHUB_REPOSITORY}/'
ISSUES_URL = urljoin(API_BASE, 'issues')

HEADERS = {
    'Authorization': f'token {GITHUB_TOKEN}',
    'Accept': 'application/vnd.github+json'
}

def find_reqif_files():
    if REQIF_FILE:
        p = Path(REQIF_FILE)
        if p.exists():
            return [p]
        else:
            print(f'WARN: REQIF_FILE is set but file not found: {REQIF_FILE}')
            return []
    else:
        return list(Path('.').rglob('*.reqif'))

def extract_requirements_from_tree(tree):
    root = tree.getroot()
    # ReqIF uses namespaces; get any namespace if present
    ns = {}
    if root.tag.startswith('{'):
        uri = root.tag.split('}')[0].strip('{')
        ns['r'] = uri
        spec_obj_xpath = './/r:SPEC-OBJECT'
    else:
        spec_obj_xpath = './/SPEC-OBJECT'

    requirements = []
    # find SPEC-OBJECT elements
    for spec in root.findall(spec_obj_xpath, ns):
        # identifier
        identifier = spec.attrib.get('IDENTIFIER') or spec.attrib.get('id') or spec.attrib.get('identifier') or 'NoID'
        # try multiple paths for the descriptive text
        text = None
        # path 1: ATTRIBUTE-VALUE-STRING/THE-VALUE (element text)
        if ns:
            tv = spec.find('.//r:ATTRIBUTE-VALUE-STRING/r:THE-VALUE', ns)
        else:
            tv = spec.find('.//ATTRIBUTE-VALUE-STRING/THE-VALUE')
        if tv is not None and (tv.text and tv.text.strip()):
            text = tv.text.strip()
        # path 2: VALUES/ATTRIBUTE-VALUE-STRING @THE-VALUE (attribute)
        if text is None:
            if ns:
                av = spec.find('.//r:VALUES/r:ATTRIBUTE-VALUE-STRING', ns)
            else:
                av = spec.find('.//VALUES/ATTRIBUTE-VALUE-STRING')
            if av is not None:
                text_attr = av.attrib.get('THE-VALUE') or av.attrib.get('the-value') or av.attrib.get('the_value')
                if text_attr:
                    text = text_attr.strip()
        if text is None:
            # fallback: use full spec text representation
            import xml.etree.ElementTree as ET2
            text = ET.tostring(spec, encoding='unicode', method='text').strip()[:200]
        requirements.append({'id': identifier, 'text': text})
    return requirements

def parse_reqif_file(path):
    try:
        tree = ET.parse(path)
        return extract_requirements_from_tree(tree)
    except Exception as e:
        print(f'ERROR parsing {path}: {e}')
        return []

def get_existing_issue_ids():
    # Retrieve all issues (open and closed) and build a set of requirement IDs already present in titles or bodies
    existing_ids = set()
    url = urljoin(API_BASE, 'issues')
    params = {'state': 'all', 'per_page': 100}
    while url:
        r = requests.get(url, headers=HEADERS, params=params)
        if r.status_code != 200:
            print('ERROR fetching existing issues:', r.status_code, r.text)
            break
        items = r.json()
        for it in items:
            title = it.get('title','') or ''
            body = it.get('body','') or ''
            # Look for IDs at the start of title like "REQ-001:"
            m = re.match(r'\s*(?P<id>REQ[-_0-9A-Za-z]+)[:\s-]', title)
            if m:
                existing_ids.add(m.group('id').strip())
            # Also check body for **Requirement ID:** REQ-001
            m2 = re.search(r'\*\*Requirement ID:\*\*\s*(?P<id>REQ[-_0-9A-Za-z]+)', body)
            if m2:
                existing_ids.add(m2.group('id').strip())
        # pagination (Link header)
        link = r.headers.get('Link','')
        next_url = None
        if 'rel="next"' in link:
            parts = link.split(',')
            for p in parts:
                if 'rel="next"' in p:
                    next_url = p[p.find('<')+1:p.find('>')]
                    break
        url = next_url
        params = None  # subsequent pages have params in link
    return existing_ids

def create_issue(requirement):
    short = requirement['text'][:72].rstrip()
    title = f"{requirement['id']}: {short}"
    body = f"**Requirement ID:** {requirement['id']}\n\n{requirement['text']}\n\n_Imported from ReqIF_"
    payload = {'title': title, 'body': body, 'labels': LABELS}
    r = requests.post(ISSUES_URL, headers=HEADERS, json=payload)
    if r.status_code == 201:
        print('Created issue:', title)
        return True
    else:
        print('Failed to create issue:', title)
        print(r.status_code, r.text)
        return False

# ----------------------------------------------------------------------
# NEW FUNCTION: Closes issues that are no longer in the ReqIF file
# ----------------------------------------------------------------------
def close_stale_issues(latest_req_ids):
    """
    Retrieves all open issues labeled 'requirement' and closes those whose IDs 
    are not found in the latest_req_ids set.
    """
    closed_count = 0
    url = urljoin(API_BASE, 'issues')
    # Filter by state=open and label=requirement
    params = {'state': 'open', 'labels': ','.join(LABELS), 'per_page': 100}
    
    # 1. Fetch all open requirement issues
    issues_to_check = []
    issues_url = url
    while issues_url:
        r = requests.get(issues_url, headers=HEADERS, params=params)
        if r.status_code != 200:
            print('ERROR fetching open issues to check for staleness:', r.status_code, r.text)
            return closed_count
        items = r.json()
        issues_to_check.extend(items)
        
        # Handle pagination
        link = r.headers.get('Link','')
        next_url = None
        if 'rel="next"' in link:
            parts = link.split(',')
            for p in parts:
                if 'rel="next"' in p:
                    next_url = p[p.find('<')+1:p.find('>')]
                    break
        issues_url = next_url
        params = None # Subsequent pages have params in link

    # 2. Iterate and close stale issues
    for issue in issues_to_check:
        title = issue.get('title','')
        issue_number = issue.get('number')
        
        # Extract the Requirement ID from the issue title (e.g., REQ-001: ...)
        m = re.match(r'\s*(?P<id>REQ[-_0-9A-Za-z]+)[:\s-]', title)
        
        if m:
            issue_req_id = m.group('id').strip()
            
            # Check if this ID is missing from the current ReqIF file
            if issue_req_id not in latest_req_ids:
                # Issue is stale, close it
                close_url = urljoin(API_BASE, f'issues/{issue_number}')
                close_payload = {'state': 'closed'}
                
                r = requests.patch(close_url, headers=HEADERS, json=close_payload)
                if r.status_code == 200:
                    print(f'CLOSED STALE ISSUE: {issue_req_id} (Issue #{issue_number})')
                    closed_count += 1
                else:
                    print(f'FAILED to close issue {issue_req_id} (Issue #{issue_number}):', r.status_code, r.text)
                
                # Wait to respect rate limits
                time.sleep(0.5)

    return closed_count
# ----------------------------------------------------------------------
# END NEW FUNCTION
# ----------------------------------------------------------------------

def main():
    files = find_reqif_files()
    if not files:
        print('No .reqif files found. Nothing to import.')
        return
    print(f'Found {len(files)} .reqif file(s):', files)

    existing = get_existing_issue_ids()
    print('Existing requirement IDs in repo issues:', existing)
    
    # 1. Collect all valid IDs from the latest ReqIF file(s) and identify new requirements
    all_latest_req_ids = set()
    requirements_to_create = []

    for f in files:
        print('Parsing', f)
        reqs = parse_reqif_file(f)
        for req in reqs:
            rid = req['id']
            # normalize id (remove surrounding spaces)
            rid_norm = rid.strip()
            all_latest_req_ids.add(rid_norm)
            
            if rid_norm not in existing:
                requirements_to_create.append(req)

    # --- Step A: Create NEW Issues ---
    total_created = 0
    print(f'Found {len(requirements_to_create)} requirements to create.')
    for req in requirements_to_create:
        ok = create_issue(req)
        if ok:
            total_created += 1
            # Add newly created ID to the set to prevent immediate re-creation checks
            existing.add(req['id'].strip()) 
            # Be gentle on API
            time.sleep(0.5)
            
    print(f'Done. Created {total_created} new issues.')
    
    # --- Step B: Close STALE Issues ---
    # Now check if any existing issues are missing from the latest files
    total_closed = close_stale_issues(all_latest_req_ids)
    print(f'Done. Closed {total_closed} stale issues.')

if __name__ == '__main__':
    main()