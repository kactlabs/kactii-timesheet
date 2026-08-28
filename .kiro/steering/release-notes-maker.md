---
inclusion: manual
---

# Release Notes Maker

Generate release notes from the current branch's commit history.

## Process

1. Detect the current branch name via `git branch --show-current`
2. Run `git log --oneline main..HEAD` to get all commits on this branch (if `main` doesn't exist, try `master`)
3. Run `git diff main..HEAD --stat` to get the changed files summary
4. Categorize commits into groups: Features, Fixes, Improvements, Breaking Changes, Other
5. Generate release notes in the format below

## Output Format

```
# Release Notes — <branch_name>

## Features
- <New features added>

## Fixes
- <Bugs fixed>

## Improvements
- <Refactors, performance, DX improvements>

## Breaking Changes
- <Any breaking changes, if applicable>

## Summary

<One-paragraph high-level summary of what this release includes>
```

## Rules

- Output must be in markdown and text-code format (wrap the entire release notes in a markdown code block)
- Omit any category section that has no entries (e.g., skip "Breaking Changes" if there are none)
- Keep bullet points concise and user-facing (describe impact, not implementation details)
- Do not include merge commits or trivial formatting-only changes
- If there are no commits on the branch, inform the user that the branch has no changes
- Use conventional commit prefixes (feat, fix, refactor, etc.) as hints for categorization when available
