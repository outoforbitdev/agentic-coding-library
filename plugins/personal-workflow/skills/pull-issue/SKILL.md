---
name: pull-issue
description: Fetch a GitHub issue and save it locally as Markdown so it can be reviewed, referenced, or fed into planning. Use when the user asks to "pull", "fetch", or "grab" a GitHub issue, or names an issue number/URL they want documented locally.
---

# Pull Issue

Fetch a single GitHub issue and save it as a local Markdown file, without
starting any planning process. This is the building block other skills
(like brainstorming's "start from an issue" flow) call — but it is also a
complete, standalone action on its own.

## Steps

1. Determine the repo and issue number from the user's request. If a full
   GitHub URL was given, parse owner/repo/number from it. If only a number
   was given and the current directory is a git repo with a GitHub remote,
   infer the repo from `git remote get-url origin`. If neither is
   determinable, ask the user which repo.

2. Fetch the issue with labels and comments:

   ```bash
   gh issue view <number> -R <owner>/<repo> --json title,body,labels,comments,url,state,author,createdAt
   ```

3. Slugify the title (lowercase, spaces to hyphens, strip punctuation) and
   write the file to `.agent-wip/issues/<repo>-<number>-<slug>.md` relative
   to the current working directory's workspace root (create the
   `.agent-wip/issues/` directory if it doesn't exist). Content shape:

   ```markdown
   # [<repo>#<number>] <title>

   **URL:** <url>
   **State:** <state>
   **Author:** <author>
   **Labels:** <comma-separated labels, or "none">

   ## Body

   <body, verbatim>

   ## Comments

   <for each comment: "### <author> (<createdAt>)" followed by the comment body; if no comments, write "No comments.">
   ```

4. Report the saved file path back to the user in one sentence. Do not
   summarize, analyze, or propose next steps unless the user asked for
   that separately — this skill's job ends at "the issue is now a local
   file."

## Non-goals

- Does not start brainstorming, research, or planning. See
  `superpowers:brainstorming`'s issue-based entry point (this plugin's
  README links it) for that — it calls this skill first, then continues.
- Does not modify the issue on GitHub (no comments, no labels, no closing).

## Integration with brainstorming

When a user asks to plan or design a solution starting from a GitHub issue
(e.g. "let's plan out issue #47" or "help me tackle
outoforbitdev/library-galaxy-map#47"), invoke this skill first to save the
issue locally, then invoke `superpowers:brainstorming` and use the saved
file's content as the seed context for that skill's classification and
questioning steps — exactly as if the user had pasted the issue into chat.
Do not skip straight to brainstorming without saving the issue first: the
saved file is what makes the issue reviewable outside the chat transcript.
