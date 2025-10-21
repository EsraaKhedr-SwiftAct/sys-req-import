# GitHub ReqIF Importer (Real Version)

This repo contains a real implementation that parses `.reqif` files and creates GitHub issues
using the GitHub REST API. It is intended to be run inside GitHub Actions where `GITHUB_TOKEN`
and `GITHUB_REPOSITORY` are available as environment variables.

## How to use

1. Create a new repository on GitHub.
2. Upload the contents of this folder to the repository (or push via git).
3. When you add or modify any `.reqif` file and push the change, the workflow will run automatically.
4. The Action will create one GitHub issue per requirement found in the `.reqif` files, unless the
   requirement ID already exists in an existing issue (it will skip duplicates).

## Notes & Safety

- The workflow uses the built-in `GITHUB_TOKEN` provided by Actions: it has permission to create issues.
- The script attempts to detect previously-imported requirements by scanning existing issue titles and bodies.
- Test in a safe repository first to confirm behavior before using in production.
