# Vercel Check - Deployment Error Scanner

## When to Use

This steering is activated when the user asks to check for Vercel deployment related errors in Python projects.

## Process

1. Read ALL error files from `~/.vercel_errors/` directory every single time this check runs.
2. Parse each error file to understand the error pattern, cause, and fix.
3. Scan all `.py` files in the current project for patterns that would trigger each documented error.
4. If issues are found, fix them directly in the current project.
5. If no issues are found, confirm the project is clean.

## Notes

- Always read `~/.vercel_errors/` fresh every time — do NOT rely on cached or hardcoded error definitions.
- The error files in `~/.vercel_errors/` are the single source of truth. New errors may be added at any time.
- Always scan all `.py` files in the project for detected patterns.
- Fix issues directly without asking for confirmation.
- Report what was found and fixed.
