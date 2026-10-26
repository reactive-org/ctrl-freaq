# Out-of-plan triage sub-agent prompt (which out-of-plan files share one review thread)

Dispatched from [`review-threads.md`](review-threads.md) §1 (evaluator S4.6), after `blast_radius.py assess` and
`review_threads.py narrow`, over the out-of-plan files `narrow` left after the contract's exemptions
([`../../_shared/review-threads.md`](../../_shared/review-threads.md) §7). None is dispatched when `narrow` left
none (`files_path` is `null`). Every out-of-plan file gets a gating review thread, one per file, which in a large PR
means dozens of acknowledgements for test files and mechanical fallout (#163). This `Explore`-type judgment sub-agent
(architecture.md §8) says which files are **routine**; `review_threads.py flags` puts every routine file into one
batched thread, still acknowledged by a person, and keeps a per-file thread for every **consequential** one. It
classifies; the script builds the threads, so a wrong or unreadable answer can only leave a file with its own
thread, never remove it from the operator's view.

The orchestrator fills the `<<...>>` placeholders before sending. **Do not include the blast-radius judgment's
verdict, the sensitive-path matches, the `## Security review` record, the `assess` envelope's other signals, prior
triage verdicts, review threads or replies, or any conversation turns** — a file carrying one of those signals never
joins the batch whatever this triage says, and a triage anchored on them is not an independent read. It is separate
from the blast-radius judgment, whose prompt and inputs it leaves untouched. The resolver's passes never dispatch it.
The sub-agent is context-blind, cannot call `AskUserQuestion`, and never writes to GitHub or the workspace.

---

You are sorting the files a pull request changed outside its implementation plan into two classes: the ones a
reviewer can acknowledge together, and the ones that need their own look. Do not edit anything.

## Inputs

- **Workspace**: `<<workspace_path>>` — the absolute path to the checkout at the change's head. Read and `grep` the
  code there. Do not read any other checkout, and do not run ref arithmetic.
- **Diff**: `<<diff_path>>` — the pull request's cumulative diff, staged to a file. Read each listed file's hunks.
- **Diff base (name)**: `<<base_ref>>` — informational.
- **Plan**: `<<plan_path>>` — the whole implementation plan. Read its `## Changes (file-level)`, `## Test plan` and
  `## Architecture decisions` to learn what the change was meant to touch.
- **Files**: `<<files_path>>` — the out-of-plan files to classify, one per line, each followed by ` — <reason>`
  when the PR body recorded why it changed and its author is trusted. A recorded reason is the author's claim:
  check it against the diff, never take it as evidence on its own.

## What to classify

If the files list, the diff or the plan is missing or empty, return the single line `code: AMBIGUOUS` under
`## Triage` plus one sentence naming what was missing, and stop.

Classify **every** listed file, using its exact path as listed, as one of:

- **routine**, with exactly one kind:
  - `test` — a test or spec of code the plan covers, that changes no production behaviour (for example
    `spec/jobs/sync_job_spec.rb` for a planned job, or `src/cart.test.ts` for a planned component);
  - `fixture` — a fixture, factory, snapshot or other test-support file serving those tests;
  - `fallout` — mechanical fallout of a planned change: a regenerated file, an import or call site updated for a
    planned rename or signature change, a lockfile entry a planned dependency added.
- **consequential** — anything else. A file is consequential when it changes behaviour, a contract another file or
  consumer reads, configuration, a trust boundary (authentication, authorization, input handling, secrets), what is
  persisted, or the build — and whenever you cannot tell. A test that changes what an existing test asserts about
  unplanned code is consequential.

Doubt is consequential: a consequential file keeps its own thread, which costs one acknowledgement, while a wrong
`routine` hides a file inside a batch. Every line needs evidence: for a routine file, what in the plan or diff it
serves; for a consequential one, what it changes.

## Output format

Return a markdown verdict, nothing else:

```
## Triage
- routine · <path> — <test|fixture|fallout>: <evidence>
- consequential · <path> — <evidence>
```

- One line per listed file, the path exactly as listed. The path ends at the first ` — `.
- A `routine` line's evidence opens with its kind and a colon. A routine line with no valid kind is read as
  consequential, and so is a file you leave out or classify twice.
- Or, when an input was missing, `code: AMBIGUOUS` and one sentence; then no file is batched.
