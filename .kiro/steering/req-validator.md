# Requirements Validator

## When to Use

This steering is activated when the user asks to validate requirements, check for dependency conflicts, or uses the acronym `rv`, `#rv`, `reqv`, or `#reqv`.

## Process

1. Read `requirements.txt` in the project root.
2. Run a dependency resolution check using:
   ```
   /opt/miniconda3/envs/test12/bin/pip install --dry-run -r requirements.txt
   ```
3. If the dry-run fails or reports conflicts, identify:
   - Which packages conflict
   - What version constraints are incompatible
   - Which package requires a newer/older version of the conflicting dependency
4. Fix the conflicts by bumping or adjusting version pins in `requirements.txt`:
   - Prefer `>=` minimum bounds over exact `==` pins when a package is a transitive dependency (e.g., `pydantic>=2.7.4` instead of `pydantic==2.7.1`)
   - Keep exact pins (`==`) only for top-level packages where the user explicitly chose a version
   - Never downgrade a package to resolve a conflict — always bump the outdated one
5. After fixing, re-run the dry-run to confirm resolution.
6. Report what was found and fixed.

## Checks Performed

- **Version conflict detection**: e.g., `pydantic==2.7.1` being too old for `langchain-core` which requires `pydantic>=2.7.4`
- **Incompatible transitive dependencies**: packages that pull in conflicting sub-dependencies
- **Duplicate entries**: same package listed more than once with different versions
- **Missing dependencies**: packages imported in `.py` files but not listed in `requirements.txt`
- **Deprecated/yanked versions**: versions that have been yanked from PyPI

## Rules

- Always fix conflicts directly in `requirements.txt` without asking for confirmation.
- Show the user what was changed (before → after for each line modified).
- If multiple resolution paths exist, prefer the one that bumps fewer packages.
- Never remove a package from `requirements.txt` to resolve a conflict.
- After fixing, run the dry-run again to verify the resolution is clean.
- If a conflict cannot be resolved automatically, explain the issue clearly and suggest manual options.

## Notes

- This validator focuses on installability and compatibility, not security (use `vulture.md` for vulnerability scanning).
- Common conflict pattern: an exact pin (`==`) on a shared dependency that's too old for another package's minimum requirement. The fix is to relax it to `>=` with the minimum version that satisfies all dependents.
