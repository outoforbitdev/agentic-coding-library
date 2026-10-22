---
name: pr-review-partner
description: Use when the user wants to review a large PR or branch diff interactively, chunk by chunk. Splits the diff into small ordered chunks, walks through them one at a time, and records progress and questions in resumable JSON files.
---

# PR Review Partner

Help the user review a large PR by (1) splitting the diff into small chunks, (2) ordering them so they tell a coherent story, and (3) walking through them one at a time, recording progress, questions, and findings in JSON files.

## Step 0: Identify the PR and check for existing state

1. Determine what to review. Accept a PR number/URL, a branch name, or a base ref. If none is given, use the current branch against the repo's default branch.
   - PR number/URL: `gh pr diff <n>` and `gh pr view <n> --json title,body,baseRefName,headRefName,headRefOid,number`
   - Branch: `git diff <base>...HEAD`
   - Record the head commit SHA (`headRefOid` from `gh pr view`, or `git rev-parse <head>`) for `head_sha`.
2. Make sure the code you read matches the diff. Chunk line numbers are new-file numbers, so file contents must come from the PR head. If the PR head is not the current checkout (`git rev-parse HEAD` differs from `head_sha`), do not switch branches or otherwise change the working tree without asking. Instead read files with `git show <head_sha>:<path>` (fetching the ref first if needed, e.g. `git fetch origin pull/<n>/head`), and use that for all later Read/Grep-style lookups too. If the user prefers to check the PR out, let them do it or ask first.
3. Review data lives outside the repo, in a temp directory, as two files:
   - Directory: `${TMPDIR:-/tmp}/pr-review/<repo>/pr-<id>/`, where `<repo>` is `owner-name` (from `gh repo view --json nameWithOwner -q .nameWithOwner` with `/` replaced by `-`, falling back to the basename of `git rev-parse --show-toplevel`) and `<id>` is the PR number, or the branch name with `/` replaced by `-`. Create the directory if needed.
   - `chunks.json`: the chunks themselves (diff text, summaries, ordering). Written once when chunking finishes, and rewritten only if the user asks to re-order.
   - `state.json`: review progress (status, questions, findings). Only ever changed through the helper script below.
   - **Helper script**: `scripts/review.py` in this skill's directory (alongside this file). Every command below written as `review <command>` means `python3 <skill dir>/scripts/review.py --dir <review dir> <command>`. It validates the files, writes `state.json` atomically with timestamps, and formats chunks for display. Run it with `--help` to see all commands.
   - The OS may clear temp directories. If the files are gone, start fresh.
4. If the directory already contains these files, ask the user whether to **resume** or **start over**.
   - On **resume**, reuse the stored `order_mode` and `test_placement` and skip Step 2 (offer to re-order only if the user asks). Continue from the chunk `review next` reports. Chunks with `status: "skipped"` are not revisited automatically; mention how many were skipped and offer to go back to them once the pending chunks are done.
   - If the PR's head commit differs from `pr.head_sha` in `chunks.json`, warn that the diff has changed and recommend starting over.
   - On **start over**, first rename the old directory to `pr-<id>.prev` (replacing any earlier `.prev`) so nothing is lost. If the diff had changed, offer to carry over questions and findings from the old `state.json`. Match an old chunk to a new chunk only when `file` and `function` are equal and the chunk's changed lines (the `+`/`-` lines of `diff`, ignoring the `@@` header and context) are identical. Carry over the matched chunk's `questions` and `findings`, keeping `resolved` as it was. Report how many could not be matched, listing their text so the user can re-ask them.

## Step 1: Chunk the diff

Read the full diff and the surrounding code as needed (use Read/Grep to see whole functions, callers, and callees). Then produce chunks following these rules:

- **Target about 20 lines per chunk**, counting the changed lines plus the context lines you display. Aim for 5-15. This is a guideline, not a hard limit: go over when splitting would make a chunk harder to understand.
- **Never span more than one function** (or method, class body-level block, or top-level declaration). A chunk may be a _part_ of one function, never parts of two. This one is a hard rule.
- If a function's change is much larger than the target, split it at logical seams (guard clauses, loop body, error handling, return construction). Each sub-chunk must say which function it belongs to and include the signature line as context only if it fits comfortably; otherwise reference it in `summary`.
- Non-function changes (imports, constants, type/interface definitions, config, schema/migrations, docs) get their own chunks under the same guideline. Group trivially related lines (e.g., a block of imports) into one chunk.
- Test changes are chunked the same way and should be linked to the code under test via `related_chunks`.
- Skip generated or mechanical files (lockfiles, snapshots, vendored code, minified output). Do not chunk them, but list them in `skipped_files` with a reason so the user knows they were not reviewed.
- Pure renames/moves with no logic change: one chunk per file at most, flagged `"kind": "mechanical"`.

For each chunk, capture: file, line range (new-file numbers), enclosing function name, the diff text, a one-sentence plain-language summary of what it does, and a short "why it matters / what to look for" note (risks, edge cases, behavior changes). Be accurate; if unsure what a chunk does, say so rather than guessing.

Also record, for each chunk, the functions it **calls** and is **called by** among the other changed functions (`calls`, `called_by`). Write each as `functionName (path/to/file.ts)`, so functions with the same name in different files stay distinct. This is what drives ordering.

### Large diffs: delegate chunking to a subagent

Chunking a large diff in the main context is slow and crowds out the walkthrough. If the diff is large (roughly more than 800 changed lines or 15 changed files), ask the user with AskUserQuestion whether to dispatch a subagent to do the chunking. Do not dispatch without their confirmation. If they decline, chunk inline.

When dispatching (Agent tool, general-purpose):

- Give the subagent the PR identification from Step 0 (diff command, `head_sha`, how to read files at the PR head), all the chunking rules and per-chunk fields from this step, the `chunks.json` schema from Step 3, and the output path.
- Tell it to write `chunks.json` with `order` set to `null` on every chunk, `order_mode` and `test_placement` set to `null`, and not to modify any source files.
- When it returns, validate the result yourself: run `review validate` (it checks the schema and that related chunk ids exist), confirm every changed file is either covered by chunks or listed in `skipped_files`, and spot-check a few chunks against the real diff. Fix or re-dispatch if it is wrong. Then continue with Step 2, which assigns `order`.

## Step 2: Ask how to order the chunks and handle tests

Before showing anything, use AskUserQuestion to ask two questions in one call.

**Question 1: ordering.** Offer exactly these two options:

1. **Flow (top-down)** - start at entry points and follow execution: "this function, which calls this function, which calls..." Best for understanding what the change _does_ end to end.
2. **Building blocks (bottom-up)** - start at leaf helpers and move up to their callers: "this function, which is called by this function..." Best for verifying each piece before seeing how it's composed.

Briefly describe each in the option text. Do not choose for the user.

**Question 2: tests.** Only ask this if the diff contains test chunks. Offer exactly these three options:

1. **After each function** - review a function's tests right after that function's chunks.
2. **At the end** - review all test chunks after all other chunks.
3. **Skip tests** - do not review test chunks. They are still listed and marked `skipped`, so they can be revisited later.

Record the answers as `order_mode` (`flow` or `building_blocks`) and `test_placement` (`after_function`, `end`, or `skip`) in `chunks.json`. If there are no test chunks, record `test_placement` as `null`.

### Ordering algorithm

Build a graph over changed functions using `calls`/`called_by`.

- **Flow**: entry points first (changed functions with no changed callers, e.g., handlers, CLI commands, public API, exported functions), then depth-first along calls. Visit the callee chunks right after the caller's chunks.
- **Building blocks**: topologically sort so callees come before callers (leaves first).
- Keep all chunks of one function contiguous and in source order.
- Cycles (recursion/mutual recursion): break at the function with the most external callers and note it in the chunk.
- Non-function chunks: types/constants/config go _first_ in building blocks (they are foundations) and _adjacent to their first user_ in flow.
- Test chunks, by `test_placement`:
  - `after_function`: place each test chunk immediately after the last chunk of the function it tests (via `related_chunks`). A test chunk that covers several functions goes after the last of them in the order. This never splits a function's chunks apart.
  - `end`: place all test chunks after every non-test chunk, ordered by where the code they test appears in the order.
  - `skip`: order test chunks as for `end`, but mark them `skipped` in `state.json` when it is initialized.
- Tie-break by file path, then line number.
- If there are several disconnected clusters, order clusters by size (largest first) and tell the user where each cluster begins.

Assign `order` (1-based) to every chunk.

## Step 3: Save the JSON files

Write both files before showing the first chunk.

### `chunks.json`

Chunk content and ordering. Treat it as read-only during the walkthrough. Schema:

```json
{
  "version": 2,
  "pr": {
    "id": "123",
    "title": "Add retry logic to uploader",
    "base_ref": "main",
    "head_ref": "feature/retry",
    "head_sha": "abc1234"
  },
  "order_mode": "flow",
  "test_placement": "after_function",
  "created_at": "2026-10-22T07:10:00-04:00",
  "skipped_files": [{ "path": "package-lock.json", "reason": "lockfile" }],
  "chunks": [
    {
      "id": "c001",
      "order": 1,
      "file": "src/upload.ts",
      "start_line": 40,
      "end_line": 56,
      "function": "uploadFile",
      "kind": "function",
      "diff": "@@ ... unified diff text ...",
      "summary": "One-sentence description of what this chunk does.",
      "review_notes": "What to look for.",
      "calls": ["retryWithBackoff (src/retry.ts)"],
      "called_by": [],
      "related_chunks": ["c002", "c014"]
    }
  ]
}
```

- `kind`: `function`, `types`, `config`, `test`, `docs`, `mechanical`, or `other`.

### `state.json`

Review progress, keyed by chunk id. Do not write it by hand: `review init-state` creates it from `chunks.json`, with every chunk `pending` (or `skipped` for test chunks when `test_placement` is `skip`). Schema:

```json
{
  "version": 2,
  "updated_at": "2026-10-22T07:25:00-04:00",
  "current": "c001",
  "chunks": {
    "c001": {
      "status": "reviewed",
      "reviewed_at": "2026-10-22T07:20:00-04:00",
      "questions": [
        {
          "text": "Why is the timeout doubled here?",
          "asked_at": "2026-10-22T07:25:00-04:00",
          "answer_summary": "Short summary of what you answered, or null if unanswered.",
          "resolved": true
        }
      ],
      "findings": [
        {
          "text": "Retry loop never resets the attempt counter after a success.",
          "noted_at": "2026-10-22T07:26:00-04:00"
        }
      ]
    }
  }
}
```

- `status`: `pending`, `reviewed`, or `skipped`. A chunk is `reviewed` only when the user moves on from it with `next`. Merely presenting a chunk does not change its status.
- `reviewed_at` is `null` until the chunk is reviewed.
- `current` is the id of the chunk most recently presented, or `null`.
- `findings` holds suspected bugs or concerns you noticed. Keep them out of `review_notes`, which is part of `chunks.json`.

### Rules for writing the files

- Write `chunks.json` once, then leave it alone (except to re-order on request). Keep it valid JSON with 2-space indentation.
- After writing `chunks.json` (with `order` assigned), run `review validate --ordered`, fix any errors, then run `review init-state`.
- Never edit `state.json` directly. Use these commands, each of which saves immediately and sets `updated_at` and any timestamps itself:
  - `review show <chunk>`: print a chunk formatted for presentation and set it as `current`.
  - `review reviewed <chunk>` / `review skip <chunk>`: set the chunk's status.
  - `review ask <chunk> "<text>"`, then `review answer <chunk> "<text>" [--unresolved]`: record a question, then answer the most recent one.
  - `review finding <chunk> "<text>"`: record a suspected bug or concern.
  - `review next`: print the id of the chunk to present next (the current chunk if it is still pending, else the next pending chunk), or `none`.
  - `review status`: print counts, the current and next chunk, unresolved questions, and findings.
- `<chunk>` is a chunk id (`c007`) or the chunk's 1-based position in the order (`7`).
- Save as you go: call the matching command after **every** `next`, `skip`, question, answer, and finding, so progress survives interruptions.

## Step 4: Walk through the chunks

Start with a short overview: PR title, total chunk count, chosen mode and test handling, any skipped files, and a one-line description of the first cluster. Then present chunks one at a time, in `order`, passing over chunks that are already `reviewed` or `skipped` when resuming.

To get a chunk, run `review show <chunk>`. It prints the header, links, fenced diff, summary and notes, and sets the chunk as `current`. Present that content to the user, copying the diff block exactly. For each chunk, show:

1. Header: `Chunk 7 of 42 - src/upload.ts:40-56 - uploadFile()`
2. One line of connective context in the chosen mode (e.g., "This is called by X, which you just saw" / "This calls Y, which is next").
3. The code, as a diff (see **Formatting the diff** below).
4. The summary and the "what to look for" notes, in plain prose, kept short.
5. A prompt that makes the options clear, for example: "Ask a question about this chunk, say `next` to mark it reviewed and continue, or `skip` to move on without marking it reviewed."

Accepted input:

- **A question** (any free text): answer it about the current chunk.
- `next`/`n`: mark the current chunk reviewed and present the next one.
- `skip`: present the next chunk, but mark the current chunk `skipped` so it is recorded as not actually reviewed.
- `back`/`b`, `jump <chunk id or number>`, `status`, `stop`.

### Formatting the diff

Show each chunk's diff in a fenced code block with the `diff` language tag, so the terminal highlights added lines green and removed lines red:

````
```diff
@@ -42,7 +42,9 @@ async function uploadFile(path) {
   const body = await read(path);
-  const res = await post(url, body);
+  const res = await retryWithBackoff(() => post(url, body));
+  if (!res.ok) throw new UploadError(res.status);
   return res;
```
````

- Keep the unified-diff prefixes exactly: `+` for added lines, `-` for removed lines, and a single space for context lines, with the original indentation after the prefix. The `diff` tag only colors lines that start with `+` or `-`.
- Keep the `@@` hunk header (it carries the function name and line numbers) on its own line at the top.
- Do not use a language tag such as `ts` or `python` for the diff. It would drop the red/green coloring. Mention the language in the header line instead if it helps.
- Do not reflow, wrap, or trim lines, and do not leave out context lines that are part of the chunk.

Behavior:

- Presenting a chunk (`review show`) sets it as `current` and leaves its `status` alone.
- On `next`, run `review reviewed <chunk>`, then `review next` to find the following chunk, then `review show` it. On `skip`, run `review skip <chunk>` (which leaves `reviewed_at` null), then continue the same way.
- Questions never advance the walkthrough. After answering, stay on the same chunk until the user says `next` or `skip`.
- When the user asks a question, record it with `review ask`, answer it using the code (read more files if needed; be honest if you can't tell), and record the answer with `review answer` (add `--unresolved` if the question is still open). Then ask whether they have follow-ups before moving on.
- Do not show the next chunk until the user asks to continue. Never dump multiple chunks at once.
- `back` re-shows the previous chunk without altering its record beyond appending new questions. `jump` moves directly to the given chunk without changing the status of the chunk being left. A chunk that was `skipped` stays `skipped` until the user says `next` on it, which marks it `reviewed`.
- `status` runs `review status`, which prints: chunks reviewed / skipped / pending, a list of unresolved questions with chunk ids, and a list of findings with chunk ids.
- Do not offer a verdict on the PR or approve/reject it unless the user asks. If you notice a likely bug while presenting a chunk, say so plainly alongside the chunk's notes and record it with `review finding`.

## Step 5: Wrap up

When all chunks are reviewed or skipped (or the user says `stop`), run `review status` and print a brief summary: counts reviewed/skipped/pending, the questions still unresolved (with chunk ids and file:line), the findings (with chunk ids and file:line), and the path to the review directory. Offer to draft review comments from the recorded questions and findings, but only if the user wants that.

## Notes

- Prefer accuracy over speed when chunking: read the real files, not just the diff, so function boundaries and call relationships are correct.
- If the PR is small (fewer than ~5 chunks), mention it and still proceed unless the user says otherwise.
- Never modify source files in the repo. The only files this skill writes are `chunks.json` and `state.json` (plus their temp directory and the `.prev` backup).
