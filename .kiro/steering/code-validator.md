# Code Validator

## When to Use

This steering is activated when the user asks to validate code, run a code check, or uses the acronym `cov`, `cv`, `#cov`, or `#cv`.

## Process

Run all three sections below in order:

---

### 1. Regular Code Check (Python Only)

- Scan all `.py` files in the project.
- Check for common issues:
  - Syntax errors
  - Unused imports
  - Undefined variables or references
  - Hardcoded secrets or credentials
  - Missing type hints on public functions (warning, not blocking)
  - Obvious logic errors (e.g., unreachable code, infinite loops)
- Run a lint check using:
  ```
  /opt/miniconda3/envs/test12/bin/python -m py_compile <file>
  ```
  for each `.py` file to confirm they compile without errors.
- Report any issues found with file name, line number, and description.
- If all files pass, confirm the code is clean.

---

### 2. Vulnerability Check

- Follow the full process defined in `vulture.md` steering file.
- This includes running `pip-audit` against `requirements.txt` and reporting any findings.

---

### 3. Vercel Related Errors

- Follow the full process defined in `vercel-check.md` steering file.
- This includes scanning for known Vercel deployment error patterns and fixing them if found.

## Notes

- All three sections must be executed. Do not skip any.
- Report results from each section clearly and separately.
- Do NOT auto-fix issues in Section 1 (Regular Code Check) without user confirmation.
- Section 2 and Section 3 follow their own fix policies as defined in their respective steering files.
