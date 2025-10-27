import os
import sys
import glob
import requests
from strictdoc.export.reqif import reqif_to_strictdoc
from strictdoc.core.document_tree import DocumentTreeReader
from io import StringIO

print("🚀 Starting ReqIF import script...")
print("Working directory:", os.getcwd())

# --- Locate the ReqIF file automatically ---
reqif_files = glob.glob("**/*.reqif", recursive=True)
if not reqif_files:
    print("❌ No .reqif file found in the repository.")
    sys.exit(1)

REQIF_FILE = reqif_files[0]
print(f"📄 Found ReqIF file: {REQIF_FILE}")

# --- GitHub setup ---
repo = os.environ["GITHUB_REPOSITORY"]
token = os.environ["GITHUB_TOKEN"]
headers = {"Authorization": f"token {token}"}

# --- Test API connectivity ---
url = f"https://api.github.com/repos/{repo}/issues"
test_data = {"title": "ReqIF Import Test", "body": "✅ GitHub API connection successful."}
r = requests.post(url, headers=headers, json=test_data)
print("🧩 Test issue creation:", r.status_code, r.text[:200])

if r.status_code != 201:
    print("❌ Unable to create test issue. Check token permissions.")
    sys.exit(1)

# --- Parse the ReqIF content dynamically ---
try:
    print("📦 Parsing ReqIF file using strictdoc...")
    with open(REQIF_FILE, "r", encoding="utf-8") as f:
        reqif_content = f.read()

    # Convert ReqIF XML into StrictDoc format
    strictdoc_content = reqif_to_strictdoc(reqif_content)
    document_tree = DocumentTreeReader(StringIO(strictdoc_content)).read()
except Exception as e:
    print("❌ Failed to parse ReqIF:", e)
    sys.exit(1)

# --- Extract requirements ---
requirements = {}
for doc in document_tree.document_list:
    for req in doc.free_texts + doc.requirements:
        attrs = {a.title: a.value for a in getattr(req, "fields", [])}
        rid = attrs.get("ID") or req.uid or "REQ-" + str(len(requirements) + 1)
        title = attrs.get("Title") or getattr(req, "statement", rid)
        desc = getattr(req, "statement", "") + "\n\n" + "\n".join([f"**{k}:** {v}" for k, v in attrs.items()])
        requirements[rid] = {"title": title, "body": desc, "attrs": attrs}

print(f"🔍 Extracted {len(requirements)} requirements from ReqIF.")
if not requirements:
    print("⚠️ No requirements found. Please verify the ReqIF structure.")
    sys.exit(0)

# --- Create or update GitHub issues ---
created_count = 0
for rid, req in requirements.items():
    title = f"{rid}: {req['title']}"
    body = req["body"]

    # Check for an existing issue with same title
    search_url = f"https://api.github.com/search/issues?q=repo:{repo}+in:title+{rid}"
    search_resp = requests.get(search_url, headers=headers)
    existing = search_resp.json().get("items", [])

    if existing:
        issue_url = existing[0]["url"]
        print(f"✏️ Updating existing issue for {rid}")
        r = requests.patch(issue_url, headers=headers, json={"title": title, "body": body})
    else:
        print(f"🆕 Creating issue for {rid}")
        r = requests.post(url, headers=headers, json={"title": title, "body": body})
        created_count += 1

    if r.status_code not in (200, 201):
        print(f"⚠️ Failed to create/update issue {rid}: {r.status_code} {r.text[:200]}")

print(f"✅ Import complete. {created_count} new issues created / updated.")

