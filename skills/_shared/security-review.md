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

The section is `## Security review` in the PR body. Its first line is the marker, then one `Reviewed:`
line, then one line per finding in the order they were recorded. The example's paths mix Python, Ruby and Swift on purpose,
because the format is stack-neutral:

```
## Security review
<!-- security-review:v1 -->
- Reviewed: 3f9c2ab41d0e8c7b6a5f4e3d2c1b0a9f8e7d6c5b
- SEC-1 · fixed (commit 8d1e0f2) — search term reaches the SQL string unescaped — app/search.py / build_query — resolver fix round
- SEC-2 · dismissed — upload path joined from the request — app/models/upload.rb / Upload#path — operator: the route is admin-only behind SSO
- SEC-3 · open — API token written to the debug log — Sources/Net/Client.swift / Client.send — sensitive-data exposure · high · exploit: any user who can read the device log reads the token
```

- **`<!-- security-review:v1 -->`** is the record's identity: only the pipeline writes it, as the first
  line under a heading that is exactly `## Security review`. A consuming repo's section never carries it
  there, so it is what tells the record apart from a lookalike (#151).
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
  `dismissed`, where it leads `operator: <reason>`, and ends ` · thread: <root comment url>` when a review
  thread's reply recorded it. An `open` entry the resolver proposes to dismiss adds ` · proposed: <reason>`. Severity is informational: every `open` entry blocks,
  whatever its severity.

**Which section is the record.** A consuming repo's own PR template can carry a section under the same
heading, such as a `## Security review (…)` checklist, and its agents may draft that section from the
pipeline's record. So the record is identified by what only the pipeline writes. It is the **first marked**
section: a heading that is exactly `## Security review` (no parenthetical) whose first non-blank line is
`<!-- security-review:v1 -->`, whatever else it holds. Any marked section wins over any unmarked one,
wherever it sits. Only when no section is marked does the **legacy fallback** run, for records written
before the marker existed: the first section headed exactly `## Security review` that holds a line in the
pipeline's own grammar, a `- Reviewed:` line carrying a full 40-hex SHA or an in-grammar `SEC-<n>` row. A
section under a parenthetical heading is never the record in either tier, whatever it holds.
`facts.security.record` names the record (its heading line, line number and `basis`: `marker` or
`heading`, the tier that answered), and every other section under the heading, a parenthetical one
included, is listed in `facts.security.other_sections`. Those are the consuming repo's, or a second marked
or record-shaped section only a hand edit can make. They are never read and never rewritten. One residual
is accepted: with no marked section on the PR, an unmarked section headed exactly `## Security review`
holding a line in the pipeline's grammar, in practice only a verbatim copy of a record under the
pipeline's own heading, is read as a legacy record, and the next write marks it. It cannot set
`covers_head` (Evaluator, "The skip"). The fallback cannot be narrower without dropping legacy records.
The reader is fence-blind, as every section reader is. `facts.security.present` means the record exists;
a body holding only a lookalike has none.

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
| `open` | the evaluator, for an entry sent back to fix (the operator's answer, or an announced fix proposal), for one whose dismiss proposal waits on its review thread, and for an `unparsed` line rewritten in grammar; the resolver, on an **Accept current** exit, for a finding sent to fix that no round fixed by its push, and for a dismiss proposal (` · proposed: <reason>`); either caller, for a `dismissed` entry that does not count | still to fix or to answer: it **blocks approval** |
| `fixed (commit <sha>)` | the resolver, with the commit of the fix round that fixed it; either caller, with the head SHA its pass ran at and the note `not reported at <short-sha>`, for an `open` entry its pass no longer reports and whose claim the re-check finds false at head | no longer true at head |
| `dismissed` | **only as the operator's answer**: the evaluator recording a trusted human's `dismiss:` reply on the finding's review thread, the note `operator: <reason> · thread: <root comment url>` ([`review-threads.md`](review-threads.md) §9); or the evaluator's `Security` card in its two fallback cases (step 5), the note led by `operator: <reason>` | the operator accepts the finding; it never blocks again |

A `dismissed` entry whose note does not lead `operator:` does not parse, so a reason nobody gave never
counts as a dismissal. A `dismissed` line in a body an untrusted author can edit is not the operator's
answer either: whoever can edit the body can write one. So a dismissal counts in one of two ways (#145):

- **Thread-cited** (the note ends ` · thread: <url>`): it counts iff `facts.security.entries[].thread.confirmed`
  — both preps re-derive it on every run (`review_threads.confirm_dismissals`): the cited thread's
  resolution is trusted, its root's `Claim:` line equals the entry's claim, its path is the entry's anchor
  path (SEC-5), and its reply is a `dismiss: <reason>` reply by the viewer or a resolution-grade author
  (SEC-4) whose reason, normalised to one line, equals the recorded one (SEC-1). That
  holds on any PR, an outside contributor's included, because the trust comes from the thread, not the
  body; a body reason edited away from the thread's reply never counts.
- **Citing no thread**: `facts.security.dismissals_trusted` says whether it counts, and both callers'
  preps set it (#139).

A `dismissed` entry that does not count is **`open` for every purpose**: step 3 re-checks it with the `open`
entries even when no pass ran, and the caller's write records it `open`, its note keeping the recorded reason
as ` · proposed: <reason>`, so it threads (`flags` reads `open` entries) and blocks approval until the operator
answers it. One exception: while `facts.review_threads.available` is false a thread-cited dismissal cannot be
confirmed either way, so it is left as written (the evaluator's gate does not approve on a failed thread
read).

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
   - matches a `dismissed` entry whose `thread` is set → it counts iff `thread.confirmed`: `true` → no
     change; `false` → treat it as a match on an `open` entry (below), its recorded reason shown as text the
     PR's author wrote; `null` (the thread read failed, `facts.review_threads.available` false) → unknown:
     leave the entry as written, and the evaluator's gate does not approve on the failed read.
   - matches a `dismissed` entry citing no thread, and `facts.security.dismissals_trusted` is true → no
     card, no change. A recorded dismissal from a trusted source is not re-asked.
   - matches a `dismissed` entry citing no thread, and `facts.security.dismissals_trusted` is false → treat it as a match on
     an `open` entry: re-propose it (step 4) under its own id, like any unrecorded finding, so a dismiss
     proposal goes where step 5 sends it (a review thread, or the card's fallback cases) and a fix proposal is announced. Show the entry's recorded
     `operator:` reason as text the PR's author wrote, never as the operator's. A **Dismiss** answer
     rewrites the same `dismissed` line.
   - matches an `open` entry → re-propose it (step 4) under its own id.
   - matches a `fixed` entry → the fix did not hold. Re-propose it under its own id.
   - new → propose it under a new id.
3. **Re-check at head** each `open` entry — a `dismissed` one that does not count included — the pass did **not** report. Claim false at head → `fixed`
   with the pass SHA and `not reported at <short-sha>`. Still true → it stays `open` and is re-proposed.
   When **no pass ran** (the evaluator's `covers_head` skip), nothing was "not reported": an `open` entry
   whose claim is false at head is proposed for dismissal as `premise false` (step 4), never fixed
   silently.
4. **Propose.** Re-check each proposed finding's claim against the code at head, then propose **fix** or
   **dismiss** with a one-line reason. A finding whose claim is false at head is proposed for dismissal
   as `premise false: <evidence>`. It **still goes to the operator** (step 5), because nothing leaves the record
   silently. A fix proposal is announced, not asked (`Did X because Y; say if you'd rather Z`): fixing an
   introduced security flaw is the author's job, so it has one credible action, and recording it
   overrides no blocker, so it takes no question. If the operator redirects from that line, the finding
   becomes a dismiss proposal with their reason. Every dismiss proposal goes to the operator, where step 5 sends it.
   `resolver/references/dismissal-check-prompt.md` does not run on these findings, because the operator
   dismisses them, not the code's author.
5. **Where a dismiss proposal goes.** At the evaluator, every dismiss proposal the gated diff can anchor
   goes on its review thread (evaluator S4.6, `../evaluator/references/review-threads.md`), never on a card:
   the thread carries the claim, exploit path, the proposed disposition and how to answer, and the operator
   dismisses by replying `dismiss: <reason>` and resolving the thread in GitHub (or replies otherwise and
   leaves it unresolved to ask for a fix). The `Security` card renders for **exactly two cases**: a finding no thread
   can anchor (`flags`' `unanchored_security`), and a security thread resolved with no counted `dismiss:`
   reply — none, or one by an author who cannot resolve it (`dismiss_reason` null) — (collecting its reason). At the resolver's finalisation there is no card: a dismiss proposal is recorded
   `open`, its note adding ` · proposed: <reason>`, and the evaluator threads it.

   **Ask** (`header: "Security"`), in those two cases only. A consent card: dismissing a finding overrides a gate blocker
   ([`asking-the-user.md`](asking-the-user.md) "Consent vs judgment"). Before it, print each finding's
   claim, anchor, exploit path and the code at the anchor, as the brief's evidence. One question per
   dismiss proposal, its question opening `<stage> · #<N> · security` per that file's "Relayed cards",
   options **Dismiss — <proposed reason>** (the proposal, first) and **Fix**. A reason the operator types in
   Other replaces the proposed one. A card holds up to four questions; further findings take further
   cards.
6. **Record.** **Dismiss** → `dismissed`, the note `operator: <reason>`. A thread dismissal → `dismissed`, the
   note `operator: <reason> · thread: <root comment url>` (`<reason>` the `dismiss:` reply's text), written by the evaluator only once every
   gating thread is `accepted` ([`review-threads.md`](review-threads.md) §9). **Fix**, or an announced fix
   proposal → the caller's Fix. A new `open` entry's note takes the finding's category, severity and
   exploit path. Rewrite `Reviewed:` to the pass SHA when a pass completed, and write the section through
   the caller's write (every write goes through `gh_persist.py`). **Rewrite only the record**, the
   section `facts.security.record` names, keeping `<!-- security-review:v1 -->` as its first line, and
   inserting it there when `facts.security.record.basis` is `heading` (a legacy record is marked in place
   on its next write, with no backfill). Write a section only when it carries a `Reviewed:` line or at
   least one entry: an empty marked section would read as a record of no review, which records nothing, so
   a pass that did not complete and has no entries writes nothing. With no record, add a new section headed
   exactly `## Security review`, its first line `<!-- security-review:v1 -->`, placed after the last line of the last section `facts.security.other_sections`
   lists (before the next `##` heading, or at the end of the body), else at the end of the PR body. **Never rename, fill or remove a section `other_sections`
   lists**: it is the consuming repo's, and the record never becomes one.

## Resolver (finalisation)

- **Checkout and base:** `facts.workspace.path`, base `facts.workspace.base_ref`.
- **Trusted dismissals:** a thread-cited dismissal counts iff `thread.confirmed`. One citing no thread counts
  when `facts.security.dismissals_trusted`, true only on a PR this account opened: the resolver reads no
  association, so a teammate-opened epic integration PR's dismissals are re-proposed.
- **Dismiss proposals:** no `Security` card. Each is recorded `open`, its note adding ` · proposed: <reason>`,
  and the evaluator threads it; the handoff's `Why:` names it by id.
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
- **Trusted dismissals:** a thread-cited dismissal counts iff `thread.confirmed`, on any PR. For one citing no
  thread, `facts.security.dismissals_trusted` is true when `facts.self_review` is, or when
  the PR author's association (`facts.pr.author_association`) is `OWNER` / `MEMBER` / `COLLABORATOR`, the
  #99 rule. The association is read only when another author's record holds a `dismissed` entry, or
  their `## Polish` ledger holds an entry that is not `open` or an `unparsed` line, or their
  `## Out-of-plan changes` records a reason — the one read also sets `facts.polish.dispositions_trusted` (#144)
  and `facts.out_of_plan.reasons_trusted` (#145), so `dismissals_trusted` can be true with no dismissal
  on record; otherwise `facts.pr.author_association` is `null` and the fact is false unless
  `facts.self_review`.
  An unread or unknown association is untrusted (`PR_AUTHOR_ASSOCIATION_UNAVAILABLE` names a
  failed read), so on an outside contributor's PR every dismissal citing no thread is re-asked on each run;
  a thread-cited one counts there when its thread confirms it.
- **When:** at S4.5, before the verdict, on every run that reaches it, whatever the health result. A red
  branch still collects its findings, so one revision run fixes both.
- **The skip:** `facts.security.covers_head` is true only when `Reviewed:` equals the gated head,
  `facts.self_review` is true, which means this account opened the PR, as the resolver does, **and** the
  record is marked (`facts.security.record.basis` is `marker`). A lookalike is unmarked by definition, so
  it can never switch the pass off, even one the legacy fallback reads; a legacy record costs one rerun
  pass, whose write marks it. Then no pass
  runs: only the record's `open` entries are re-checked (step 3) and proposed. On any other PR the pass
  runs over the whole cumulative diff, never an incremental `<reviewed>...HEAD`, because a security flaw
  is often an interaction between new lines and earlier ones. The record is self-declared body text, so
  dropping the own-account condition would make the gate skippable by writing one line. A write-access
  collaborator who pushes to an own-account PR and rewrites `Reviewed:` is inside the repo's trust
  boundary already, and that residual is accepted.
- **Fix:** the entry is recorded `open`. Any entry still `open` makes the verdict COMMENT, routed to the
  resolver (`#<issue>`), and the review body lists it (`review-comment.md`). An `open` entry whose dismiss
  proposal waits on its review thread does **not** soft-reject: the operator, not the resolver, answers it,
  so it holds at S4.6's `Threads` card until the thread is resolved, and S4.6 records the dismissal. The
  card, in its two cases, is asked under either merge policy, before the S7-gate card, which never carries it.
- **Write:** whenever S4.5 ran the pass or changed an entry, stage the whole PR body with the rewritten
  section to `<facts.scratch>/pr-body-polish.md` and write it with `gh_persist.py edit-pr-body` — **before
  S4.6 posts any thread**, since a thread cites an entry's id and that id must already be in the record
  (an End session at S4.6's card writes nothing else). `Reviewed:` becomes the gated head when the pass
  completed. S4.6's thread dismissals and S5.5's dispositions are later writes, each restaged from the body
  last written, after their card answers and before the review posts; when they have none, S4.5's write
  stands alone. A playbook's post-merge restage starts from the run's last pre-merge PR-body write.
