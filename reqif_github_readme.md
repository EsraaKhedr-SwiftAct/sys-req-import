# Tool Overview
ReqIF to GitHub Synchronization Tool:

This tool processes ReqIF  requirement files and synchronizes their content with a GitHub repository, generating or updating issues, maintaining hierarchy, mapping fields into GitHub Project V2, and optionally closing removed requirements.

It supports ReqIF exports from:

- IBM DOORS
- IBM DOORS Next
- Polarion
- Jama
- ReqIF Studio
- Enterprise Architect
- Any tool conforming to the OMG ReqIF standard

## 📌 Recommended Use Cases

Use this tool when you want to:

* Import requirements from a ReqIF file into GitHub.
* Keep GitHub Issues synchronized with an evolving ReqIF file.
* Automatically maintain Project V2 fields for each requirement.
* Ensure requirements in GitHub always match the external system of record.
* Detect attribute schema changes from ReqIF files.
* Automate requirements management inside CI/CD (GitHub Actions).

---

## 📌 How to use this tool

### 1. Add Your .reqif  File
Place your file in the root of repo, e.g., `system_requirements.reqif`.

### 2. Run Dry-Run Mode

This generates the configuration file without modifying GitHub.
```bash
# Set dry-run mode environment variable
set REQIF_DRY_RUN=True  # Use 'set' on Windows: set REQIF_DRY_RUN=True

# Run the tool
python import_reqif_dynamic.py 
```

### 3. Edit Configuration
Edit `reqif_config.json` to enable or disable specific attributes for the issue body.

### 4. Add Required GitHub Secrets
In **Settings → Actions → Secrets and variables → New repository secret**, create the following secrets:

- `PAT_TOKEN`  
  * Set this to (Personal Access Token) Generate it is value  in **GitHub → Settings → Developer Settings → Personal access tokens (classic)**.*

- `PROJECT_OWNER`  
  *  Set this to your project V2 owner name. *

- `PROJECT_TITLE`  
  *  Set this to your project V2 title.*
  *  
### 5. Trigger the GitHub Action
It will:
- Parse requirements
- Sync issues
- Sync hierarchy
- Sync project fields
- Close missing requirements



## 📌 Key Features
- Extracts all requirement attributes
- Normalizes types (int, bool, date, enums, XHTML → plain text)
- Handles vendor variations and missing definitions
- Supports SPEC-HIERARCHY and parent–child links
- Supports SPEC-RELATION / SPEC-RELATIONSHIP
- Generates and uses `reqif_config.json`
- Syncs with GitHub Issues and GitHub Project V2
- Closes issues when requirements are removed from ReqIF
- Provides full debugging output when needed



## 📌 Folder Structure Example

```
.github/
  workflows/
    import_reqif.yml
  scripts/
    import_reqif_dynamic.py
    reqif_parser_full.py
reqif_config.json
system_requirements.reqif
```
---



## 📌 Workflow Summary
`ReqIF → Parse → Normalize → Build Hierarchy → Generate Config → Create/Update Issues → Map to Project V2 → Close Removed`



## 📌 Example GitHub Issue Output
```text
[REQ-2.1] Steering Angle Sensor Precision
Description:
The steering angle sensor shall provide precision within ±0.5 degrees.
Attributes:
| Attribute | Value |
|-----------|--------|
| ID        | REQ-2.1 |
| Priority  | High    |
| Status    | Approved |
Hierarchy:
Parent: REQ-2
Children: REQ-2.1.1, REQ-2.1.2
```



## 📌 Configuration Example
```json
{
  "attributes": {
    "Priority": { "include_in_body": true },
    "System Requirement ID": { "include_in_body": true },
    "Status": { "include_in_body": false }
  }
}
```


## 📌 Troubleshooting
- **Missing Attributes:** Enable them in `reqif_config.json`.
- **Project Fields Not Updated:** Ensure their names exactly match fields in your GitHub Project V2.


## 📌 Limitations
- Extremely large ReqIF files may slow down processing
- Vendor extensions vary greatly
- API rate limits from GitHub may apply


## 📌 Additional informations 
### **1.Handling `.reqif` Files From Different Vendors**
- Check vendor.md 