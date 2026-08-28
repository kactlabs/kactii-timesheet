---
inclusion: manual
---

# PR Summary Maker

Generate a Pull Request summary by comparing the current branch against a target branch.

## Process

1. Ask the user which branch to compare against (default: `dev`)
2. Run `git log --oneline <target_branch>..HEAD` to get the list of commits
3. Run `git diff <target_branch>..HEAD --stat` to get the changed files summary
4. Run `git diff <target_branch>..HEAD` to understand the actual code changes
5. Generate a PR summary in the following format:

## Output Format

```
## Summary

<Brief one-paragraph description of what this PR does>

## Changes

- <Bullet list of meaningful changes, grouped logically>

## Files Changed

<List of files changed with a short note on what changed in each>
```

## Rules

- Output must be in markdown and text-code format (wrap the entire PR summary in a markdown code block)
- Focus on the "what" and "why", not the "how"
- Group related changes together
- Keep bullet points concise
- Do not include merge commits or trivial formatting-only changes unless they are the sole purpose
- If there are no differences between branches, inform the user that the branches are identical
