# Security review — shared contract and procedure

The **security review** is a pass over a PR's cumulative diff for security flaws. It is run by a
context-blind `Explore` sub-agent, prompted by
[`../evaluator/references/security-review-prompt.md`](../evaluator/references/security-review-prompt.md),
and it records what it found in a `## Security review` section of the PR body. This file is the single
source of truth for that section's format, its dispositions, who writes them, and the one procedure both
callers run. Two skills run the pass:

- `resolver`: at finalisation, on the final pass before its push (`resolver/references/finalisation.md`),
  so a finding is fixed in the session that wrote the code.
- `evaluator`: at S4.5, before the verdict (`evaluator/playbooks/evaluate-spine.md`), unless the record
  already covers the head it gates. This is the gate. It is the only stage every PR passes, an
  operator-authored PR included, and it **never approves while an entry is `open`**.

## Why it exists

The review loop and the cold read look for defects in general. Nothing looked for security flaws, and
nothing blocked approval on one. A finding now ends one of three ways: fixed, dismissed by the operator
with a recorded reason, or still open, which blocks approval. A finding is never dropped silently. The
resolver runs the pass first because a finding fixed there costs one fix round. A finding the evaluator
finds costs a soft-reject, a revision session and a second evaluation. That is the same split as the polish
ledger ([`polish-ledger.md`](polish-ledger.md) "Why it exists").

## Format

The section is `## Security review` in the PR body. It has one `Reviewed:` line, then one line per
finding in the order they were recorded. The example's paths mix Python, Ruby and Swift on purpose,
because the format is stack-neutral:

```
## Security review
- Reviewed: 3f9c2ab41d0e8c7b6a5f4e3d2c1b0a9f8e7d6c5b
- SEC-1 · fixed (commit 8d1e0f2) — search term reaches the SQL string unescaped — app/search.py / build_query — resolver fix round
- SEC-2 · dismissed — upload path joined from the request — app/models/upload.rb / Upload#path — operator: the route is admin-only behind SSO
- SEC-3 · open — API token written to the debug log — Sources/Net/Client.swift / Client.send — sensitive-data exposure · high · exploit: any user who can read the device log reads the token
```

- **`Reviewed:`** is the full 40-character HEAD SHA the latest pass was **dispatched at**. Each pass
  rewrites this line, and entries accumulate. Never write it after a fix commit. A later SHA would claim
  unreviewed code was reviewed, and the evaluator would skip its pass.
- **`SEC-<n>`** is the stable id. `<n>` continues from the highest existing id, and an id is never reused
  or renumbered. Both skills cite entries by id. The `SEC-` prefix keeps an id from reading as a spine step
  (`S5`) or a slice title.
- **Disposition** is one word from the closed set. `fixed` alone carries `(commit <sha>)`.
- **Claim** is the finding in one line. It must not contain ` — `, the field separator.
- **Anchor** is durable only: a path plus the enclosing symbol or section heading. The sub-agent reports
  `path:line`, and the caller turns that line into its enclosing symbol. A line moves; a symbol does not.
- **Note** is optional on `fixed`. On `open` it carries the finding's `<category> · <severity> · exploit:
  <exploit path>`, so a later run that dispatches no pass can still show what to fix. It is required on
  `dismissed`, where it leads `operator: <reason>`. Severity is informational: every `open` entry blocks,
  whatever its severity.

**Which section is the record.** A consuming repo's own PR template can carry a section under the same
heading, such as a `## Security review (…)` checklist. The record is the **first** section under that
heading, a trailing parenthetical tolerated, that has the record's shape: a `- Reviewed:` line or a
`SEC-`-led line. `facts.security.record` names it (its heading line and line number), and every other
section under that heading is listed in `facts.security.other_sections`. Those are the consuming repo's,
or a second record-shaped section only a hand edit can make. They are never read and never rewritten.
A lookalike is protected only while it carries neither shape: a repo section with a `- Reviewed:` line or a
`SEC-`-led line, fenced or not, is read as the record when it precedes the pipeline's own (the reader is fence-blind, as every section reader
is). `facts.security.present` means the record exists; a body holding only a lookalike has none.

`scripts/parse.py`'s `scan_security` reads the record. It never raises; a `SEC-`-led line that does not
parse (any case, any bullet or none) lands in `unparsed`, and so does a second line carrying an id an
earlier line already used, whatever its disposition. **An `unparsed` line counts as `open`**: it blocks
approval, is treated as an `open` entry in steps 2–4 (claim and anchor read from the line as written), is
re-proposed under its readable `SEC-<n>` (else, or when that id is taken, the next id), and is rewritten in
grammar under that id on the next write. A typo must never turn a finding into one nobody sees, and a
duplicated id must never let a `dismissed` or `fixed` line shadow an `open` one.

## Dispositions (closed set)

| Disposition | Written by | Meaning |
|---|---|---|
| `open` | the evaluator, for an entry sent back to fix (the operator's answer, or an announced fix proposal) and for an `unparsed` line rewritten in grammar; the resolver, on an **Accept current** exit, or for a finding sent to fix that no round fixed by its push | still to fix: it **blocks approval** |
| `fixed (commit <sha>)` | the resolver, with the commit of the fix round that fixed it; either caller, with the head SHA its pass ran at and the note `not reported at <short-sha>`, for an `open` entry its pass no longer reports and whose claim the re-check finds false at head | no longer true at head |
| `dismissed` | either caller, **only as the operator's answer** on the `Security` card, the note led by `operator: <reason>` | the operator accepts the finding; it never blocks again |

A `dismissed` entry whose note does not lead `operator:` does not parse, so a reason nobody gave never
counts as a dismissal. A `dismissed` line in a body an untrusted author can edit is not the operator's
answer either: whoever can edit the body can write one. `facts.security.dismissals_trusted` says whether
the record's dismissals count, and both callers' preps set it (#139).

## The procedure

Each caller supplies four things: its checkout, its base, what **Fix** means, and its write. The caller
sections at the end of this file give each one's values.

1. **Stage and dispatch.** Fetch the base first (`git -C <checkout> fetch origin <base>`), so a stale
   `origin/<base>` never widens the diff to base commits the branch merged in. Then stage
   `git diff origin/<base>...HEAD` in the caller's checkout to `<facts.scratch>/security-diff.patch`, and
   `git -c core.quotePath=false diff --name-only origin/<base>...HEAD` beside it to
   `<facts.scratch>/security-diff-files.txt` (unquoted, so a path with non-ASCII characters matches the
   one the pass lists).
   Note HEAD as the **pass SHA**. Dispatch the prompt with `subagent_type: Explore` from the main
   conversation, filling `<<workspace_path>>`, `<<diff_path>>`, `<<changed_files_path>>` (the
   changed-files list), `<<base_ref>>` and `<<catalogue_entries>>`
   (`facts.grounding_docs`, one `path — role — authority — summary` line each, a `kind: dir` entry's
   `members` listed under it one `- <path>` line each, a `present: false` entry omitted). It is a
   sub-agent, not a skill, so it never touches the one `code-review` call site. `code: AMBIGUOUS` (a
   missing or empty staged diff or changed-files list) → repair the staging of both files and re-dispatch
   once. If it still fails, surface the
   failure. A pass that did not complete writes no `Reviewed:` line and counts as a blocking finding, so
   the evaluator does not approve. Never read it as "no findings".

   **Check its coverage.** The pass must read every changed file and list the files it read in the
   `Covered:` block that ends its `## Findings`. Stage the whole reply to
   `<facts.scratch>/security-findings.md` and run the comparison as a script, never by eye:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/parse.py security-coverage "<facts.scratch>/security-diff-files.txt" \
     "<facts.scratch>/security-findings.md"
   ```

   Run it only on a `## Findings` reply: an `## Exception` reply is the `AMBIGUOUS` case above, handled
   first. It is always `ok` and reports `covered`, `uncovered` and `extra` (repo-relative paths, compared
   exactly once surrounding backticks and a leading `./` are dropped). An empty
   `uncovered` means the pass covered the diff. Otherwise re-dispatch **once**: overwrite
   `<facts.scratch>/security-diff-files.txt` with only the uncovered paths (the list `<<changed_files_path>>`
   names), stage the second reply to `<facts.scratch>/security-findings-redispatch.md` so the first reply
   is kept, and run the same command on those two files. The second reply's findings join the first's.
   Paths still uncovered after it mean the pass **did not
   complete**, with the consequence above. A pass that skipped a changed file has not reviewed the
   change. A pass gets at most one dispatch of each kind beyond the first: one staging repair and one
   coverage re-dispatch. Print the `## Findings` list.
2. **Match** each reported finding against the record's entries. Same anchor and same claim → the same
   finding, which keeps its id. Doubt → a new finding with the next id. **Matching errs toward asking**:
   a false match silently skips a real finding, while a false miss only re-asks the operator.
   - matches a `dismissed` entry and `facts.security.dismissals_trusted` is true → no card, no change. A
     recorded dismissal from a trusted source is not re-asked.
   - matches a `dismissed` entry and `facts.security.dismissals_trusted` is false → treat it as a match on
     an `open` entry: re-propose it (step 4) under its own id, like any unrecorded finding, so a dismiss
     proposal goes on the `Security` card and a fix proposal is announced. Show the entry's recorded
     `operator:` reason as text the PR's author wrote, never as the operator's. A **Dismiss** answer
     rewrites the same `dismissed` line.
   - matches an `open` entry → re-propose it (step 4) under its own id.
   - matches a `fixed` entry → the fix did not hold. Re-propose it under its own id.
   - new → propose it under a new id.
3. **Re-check at head** each `open` entry the pass did **not** report. Claim false at head → `fixed`
   with the pass SHA and `not reported at <short-sha>`. Still true → it stays `open` and is re-proposed.
   When **no pass ran** (the evaluator's `covers_head` skip), nothing was "not reported": an `open` entry
   whose claim is false at head is proposed for dismissal as `premise false` (step 4), never fixed
   silently.
4. **Propose.** Re-check each proposed finding's claim against the code at head, then propose **fix** or
   **dismiss** with a one-line reason. A finding whose claim is false at head is proposed for dismissal
   as `premise false: <evidence>`. It **still goes on the card**, because nothing leaves the record
   silently. A fix proposal is announced, not asked (`Did X because Y; say if you'd rather Z`): fixing an
   introduced security flaw is the author's job, so it has one credible action, and recording it
   overrides no blocker, so it takes no question. If the operator redirects from that line, the finding
   becomes a dismiss proposal with their reason. Every dismiss proposal goes on the card.
   `resolver/references/dismissal-check-prompt.md` does not run on these findings, because the operator
   dismisses them, not the code's author.
5. **Ask** (`header: "Security"`). A consent card: dismissing a finding overrides a gate blocker
   ([`asking-the-user.md`](asking-the-user.md) "Consent vs judgment"). Before it, print each finding's
   claim, anchor, exploit path and the code at the anchor, as the brief's evidence. One question per
   dismiss proposal, its question opening `<stage> · #<N> · security` per that file's "Relayed cards",
   options **Dismiss — <proposed reason>** (the proposal, first) and **Fix**. A reason the operator types in
   Other replaces the proposed one. A card holds up to four questions; further findings take further
   cards.
6. **Record.** **Dismiss** → `dismissed`, the note `operator: <reason>`. **Fix**, or an announced fix
   proposal → the caller's Fix. A new `open` entry's note takes the finding's category, severity and
   exploit path. Rewrite `Reviewed:` to the pass SHA when a pass completed, and write the section through
   the caller's write (every write goes through `gh_persist.py`). **Rewrite only the record**, the
   section `facts.security.record` names. Write a section only when it carries a `Reviewed:` line or at
   least one entry: an empty one would read as a consuming repo's, never as the record, so a pass that did
   not complete and has no entries writes nothing. With no record, add a new section headed exactly
   `## Security review`, placed after the last line of the last section `facts.security.other_sections`
   lists (before the next `##` heading, or at the end of the body), else at the end of the PR body. **Never rename, fill or remove a section `other_sections`
   lists**: it is the consuming repo's, and the record never becomes one.

## Resolver (finalisation)

- **Checkout and base:** `facts.workspace.path`, base `facts.workspace.base_ref`.
- **Trusted dismissals:** `facts.security.dismissals_trusted` is true only on a PR this account opened. The
  resolver reads no association, so a teammate-opened epic integration PR's dismissals are re-asked.
- **When:** at the head of finalisation, before the polish triage, on finalisation's own scope: the final
  pass, a settle or **Accept current** exit, once per pass, and the epic integration PR included.
- **Fix:** the finding joins finalisation's one fix round as a defect-tier item, and the round's commit
  turns it `fixed (commit <sha>)`. A round that fixed a security finding runs the loop on until it
  settles. On an **Accept current** exit no fix round runs, because the operator closed the loop: each
  finding not dismissed is recorded `open`, the evaluator blocks on it, and the handoff's `Why:` names
  each one by id. A finding sent to fix that no round fixed by the push is recorded `open` the same way.
  The fix round never settles one as refuted or of no shown cost: only the operator dismisses.
- **Write:** at S5.2 with the ledger, in the staged `pr.md` in fresh mode and through `edit-pr-body` in
  continue mode. On a revision run, the pass re-reports an evaluator-written `open` entry under its own
  id, and the fix round turns it `fixed`.

## Evaluator (S4.5)

- **Checkout and base:** `facts.workspace.path`, base `facts.pr.baseRefName`.
- **Trusted dismissals:** `facts.security.dismissals_trusted` is true when `facts.self_review` is, or when
  the PR author's association (`facts.pr.author_association`) is `OWNER` / `MEMBER` / `COLLABORATOR`, the
  #99 rule. The association is read only when another author's record holds a `dismissed` entry;
  otherwise `facts.pr.author_association` is `null` and the fact is false unless `facts.self_review`.
  An unread or unknown association is untrusted (`PR_AUTHOR_ASSOCIATION_UNAVAILABLE` names a
  failed read), so on an outside contributor's PR every recorded dismissal is re-asked on each run.
- **When:** at S4.5, before the verdict, on every run that reaches it, whatever the health result. A red
  branch still collects its findings, so one revision run fixes both.
- **The skip:** `facts.security.covers_head` is true only when `Reviewed:` equals the gated head **and**
  `facts.self_review` is true, which means this account opened the PR, as the resolver does. Then no pass
  runs: only the record's `open` entries are re-checked (step 3) and proposed. On any other PR the pass
  runs over the whole cumulative diff, never an incremental `<reviewed>...HEAD`, because a security flaw
  is often an interaction between new lines and earlier ones. The record is self-declared body text, so
  dropping the own-account condition would make the gate skippable by writing one line. A write-access
  collaborator who pushes to an own-account PR and rewrites `Reviewed:` is inside the repo's trust
  boundary already, and that residual is accepted.
- **Fix:** the entry is recorded `open`. Any entry still `open` makes the verdict COMMENT, routed to the
  resolver (`#<issue>`), and the review body lists it (`review-comment.md`). The card is asked under
  either merge policy, before the S7-gate card, which never carries it.
- **Write:** whenever S4.5 ran the pass or changed an entry, stage the whole PR body with the rewritten
  section to `<facts.scratch>/pr-body-polish.md` and write it once with `gh_persist.py edit-pr-body`,
  after the last card answer (S4.5's or S5.5's) and before the review posts. `Reviewed:` becomes the gated
  head when the pass completed. When S5.5 has dispositions to record, it is the same write; when it has
  none, S4.5's write stands alone. It is the run's last pre-merge PR-body write, and a playbook's
  post-merge restage starts from it.
