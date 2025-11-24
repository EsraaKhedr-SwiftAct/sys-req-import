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

## Files and Their Roles

The synchronization system is comprised of three main files, each with a distinct role:

| File Name | Role in the Project | Description |
| :--- | :--- | :--- |
| **`reqif_parser_full.py`** | **The Core Parser** (Dependency) | A robust, namespace-agnostic Python library designed to parse complex **ReqIF** and **ReqIFZ** (zipped ReqIF) files. It handles complex attribute types (e.g., Enumeration, XHTML), extracts requirement metadata, title, description, hierarchy, and relations, providing structured data for the synchronization logic. |
| **`import_reqif_dynamic.py`** | **The Synchronization Logic** (Main Executable) | The primary Python script responsible for the synchronization process. It uses `reqif_parser_full.py` to get the requirement data, loads configuration from `reqif_config.json`, and then communicates with the GitHub GraphQL API to: create new issues, update existing issues with revised content, close issues for removed requirements, and set Project V2 fields (like Status, Priority) based on the requirement attributes. |
| **`import_reqif.yml`** | **The Automation Trigger** (GitHub Actions Workflow) | A GitHub Actions workflow definition that automates the execution of `import_reqif_dynamic.py`. It is typically configured to run on a push to a specific configuration file (`reqif_config.json`) or manually via a `workflow_dispatch` trigger, allowing the user to specify the ReqIF file to import. It handles environment setup, dependency installation, and secret management. |

### Relationship Summary

The **`import_reqif_dynamic.py`** script *imports* and *depends on* **`reqif_parser_full.py`** to translate the raw ReqIF file into usable requirement objects. The entire process is automated and scheduled by the **`import_reqif.yml`** GitHub Actions workflow, which provides the necessary execution environment and credentials to run the Python synchronization script.

## 📌 How to use this tool

### 1. Download tool files.

### 2. Create your personal repo, past tool files.
### 3. create new github project, copy of  `https://github.com/users/EsraaKhedr-SwiftAct/projects/5` .
### 4. Link the project to your github repo.

### 5. Add Required GitHub Secrets to the Repo.
Note: this can be done only by the owner of the repo.
## Steps: 
In **Repo Settings → Actions → Secrets and variables → New repository secret**, create the following secrets:

- `PAT_TOKEN`  
  * Set it's value to your (Personal Access Token).
  How to create personal access token?
  To create Personal Access Token, follow steps **GitHub account → Settings → Developer Settings → Personal access tokens (classic) → Generate new one**.

- `PROJECT_OWNER`  
*   Set this to your project V2 owner name, e.g., `EsraaKhedr-SwiftAct`.* 

- `PROJECT_TITLE`  
  *  Set this to your project V2 title, e.g., `@System requirement Project`*

### 6. Replace .reqif  File with your file.
Place your .reqif file in the root of repo, e.g., `system_requirements.reqif`.

### 7. Run import_reqif_dynamic.py in Dry-Run Mode.
This generates the configuration file `reqif_config.json` in the root of the repo.
## Steps:
* cd github\scripts 
* open cmd 
* Run below commands
```bash
# Set dry-run mode environment variable
1. set REQIF_DRY_RUN=True  # Use 'set' on Windows: set REQIF_DRY_RUN=True

# Run the tool
2. python import_reqif_dynamic.py 
```

### 8. Edit Configuration
Edit `reqif_config.json` to enable or disable specific attributes for the issue body.
  
### 9. Commit your files
 This will Trigger the GitHub Action
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
- The github action will run only when you push new or edited config file.
- Extremely large ReqIF files may slow down processing
- Vendor extensions vary greatly
- API rate limits from GitHub may apply


## 📌 Additional informations 
### **1.Handling `.reqif` Files From Different Vendors**
- Check vendor.md 