---
name: pr-template
description: Use before creating any GitHub pull request. Finds the repo's PR template (if any), and requires the PR body to actually fulfill every section rather than just including its headers.
---

# PR Template

Before running `gh pr create`, always do this — do not skip even if you
believe you already know the template from a prior PR in the same session,
since templates can change.

## Steps

1. Check for a template:

   ```bash
   gh repo view --json pullRequestTemplates
   ```

2. If `pullRequestTemplates` is empty, use this default structure instead of
   guessing a repo-specific one:

   ```markdown
   ## Summary
   <1-3 bullet points of what changed and why>

   ## Test plan
   <bulleted checklist of what was actually run/verified, not what should be>
   ```

3. If a template exists, its content is already in the JSON's `body` field
   (no separate fetch needed). Treat every section header as a required
   section, not a suggestion. For each section, write content that actually answers what
   the section is asking for:
   - A "Test Plan" / "Testing" section needs the actual commands run and
     their results, or actual manual verification steps taken — never
     "N/A" unless the change is genuinely untestable (e.g. a typo-only
     docs fix), and say why in that case rather than leaving it blank.
   - A "Breaking Changes" section needs an explicit "None" if there are
     none — don't omit the section.
   - A checklist section (checkboxes) needs each box actually checked or
     explicitly left unchecked with a one-line reason, not left as an
     unaddressed template artifact.
   - Do not add, remove, or reorder sections from what the template
     defines. If a section genuinely doesn't apply, say so explicitly
     inside that section rather than deleting it.

4. Compose the full PR body matching the template's exact section
   headers/order, then run `gh pr create --body "$(cat <<'EOF' ... EOF)"`
   (heredoc, per your global git conventions).

## Common failure this skill exists to prevent

Producing a PR body that includes the template's headers but fills them
with generic, non-committal content ("Tests pass", "See code changes")
that doesn't actually demonstrate the section was thought about. The bar is:
a reviewer reading only the PR body should be able to tell specifically
what was tested and how, not just that "testing happened."
