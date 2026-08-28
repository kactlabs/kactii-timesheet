# Vulture - Vulnerability Check

## When to Use

This steering is activated when the user asks to check for vulnerabilities, security issues, or dependency safety in the Python project.

## Process

1. Ensure `pip-audit` is listed in `requirements.txt`. If not, add it.
2. Run a vulnerability scan using:
   ```
   /opt/miniconda3/envs/test12/bin/python -m pip_audit -r requirements.txt
   ```
3. If vulnerabilities are found, display them clearly to the user with:
   - Package name
   - Installed version
   - Vulnerability ID (e.g., CVE)
   - Severity (if available)
   - Recommended fix version
4. If no vulnerabilities are found, confirm the project is clean.

## Notes

- Do NOT silently skip or suppress any findings.
- Do NOT auto-fix vulnerabilities without user confirmation.
- Always show the full audit output to the user.
