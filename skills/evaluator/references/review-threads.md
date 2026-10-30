# Review threads — evaluator S4.6

The evaluator's rendering of [`../../_shared/review-threads.md`](../../_shared/review-threads.md) (#145): which
code gets a review thread, the text each thread carries, and how the threads gate APPROVE. The contract owns
the marker, the stamps, the `F<n>` ids, the five states, trust, the hunk rule, the flag kinds and the cap; this
file does not restate them. **Facts by script, meaning by model**: `blast_radius.py assess` and
`review_threads.py flags` decide which code gets a thread and where it anchors; you write each thread's text,
propose plan-adherence threads, pick the line for a security finding, and judge what a reply asks for.

S4.6 runs on every evaluation that reaches it, whatever the health result, after S4.5 and before S5, so one
revision run fixes everything. The pipeline never resolves or unresolves a thread (contract §11): the human
does, in GitHub.

## 1. The signals at the gated head

1. **The plan.** The first closing issue, in `closingIssuesReferences` order, whose `facts.plans[<issue>]` is
   `present` and not `ambiguous`, supplies the plan: its `body_path`. Every other closing issue's present,
   unambiguous plan is an extra `--plan`, so its spans count as planned. An `ambiguous` plan has no
   `body_path` and is never passed.
2. **The diff.** Reuse `<facts.scratch>/security-diff.patch` when S4.5 staged it. Otherwise (the
   `covers_head` skip) run `git -C "<facts.workspace.path>" fetch origin <facts.pr.baseRefName>` and stage
   `git diff origin/<base>...HEAD` to `<facts.scratch>/blast-radius-diff.patch`.
3. **The judgment.** Dispatch [`../../resolver/references/blast-radius-judgment-prompt.md`](../../resolver/references/blast-radius-judgment-prompt.md)
   (`subagent_type: Explore`) over that diff, and write its verdict verbatim to
   `<facts.scratch>/blast-radius-judgment.md`. Fill `<<workspace_path>>` and `<<grounding_path>>` with
   `facts.workspace.path`, `<<diff_path>>` with the diff, `<<base_ref>>` with `origin/<facts.pr.baseRefName>`,
   `<<plan_path>>` with the chosen plan's `body_path`, `<<plan_shipped_path>>` with its `.shipped.body_path` or
   `(none)`, `<<phase>>` with the plan's last `## Phases` number (1 for a single-phase plan), `<<final>>` with
   `true`, and `<<catalogue_entries>>` with `facts.grounding_docs` as `security-review.md` step 1 renders them.
   An empty diff dispatches none.
4. **The assess call**, staged to `<facts.scratch>/assess.json`:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/blast_radius.py assess "<facts.workspace.path>" \
     --plan "<plan body_path>" [--plan "<.shipped.body_path>"] [--plan "<each other closing issue's body_path>"] \
     --judgment "<facts.scratch>/blast-radius-judgment.md" --phase <last phase> --final \
     --base-ref <facts.pr.baseRefName> > "<facts.scratch>/assess.json"
   ```

   **No plan** (no closing issue with a present, unambiguous plan), and every **epic-integration PR**: no
   judgment dispatch, and `assess "<facts.workspace.path>" --phase 1 --final --base-ref <base>` with no `--plan`
   and no `--judgment`. Its `out_of_plan` is empty, so only sensitive matches and security entries thread; each
   story PR threaded and cleared its own files, and existing and operator threads still gate the integration PR.
   An empty diff passes no `--judgment` either.

   Take the sensitive signals from this call, never from `facts.config.sensitive_paths`, which is full-run only:
   a **Re-check** would otherwise gate on a stale match set.
5. **The narrow call**, staged to `<facts.scratch>/narrow.json`: the out-of-plan files left after the
   contract's exemptions (§7, a unique-basename planned file or one `linguist-generated` at both base and head),
   with `--pr-body` the body §2 passes to `flags`:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/review_threads.py narrow "<facts.workspace.path>" \
     --assess "<facts.scratch>/assess.json" --head "<merge_guard.gated_head>" --pr-body "<pr body>" \
     --reasons-trusted <facts.out_of_plan.reasons_trusted> --scratch "<facts.scratch>" > "<facts.scratch>/narrow.json"
   ```

   It is always `ok`. `files_path` is the list of files left, each with its recorded reason only when the
   reasons are trusted, or `null` when none is left; `flags` re-applies the same exemptions itself.
6. **The triage** (#163), only when `files_path` is not `null`: dispatch
   [`out-of-plan-triage-prompt.md`](out-of-plan-triage-prompt.md) (`subagent_type: Explore`), filling
   `<<workspace_path>>` with `facts.workspace.path`, `<<diff_path>>` with the diff of step 2, `<<base_ref>>` with
   `origin/<facts.pr.baseRefName>`, `<<plan_path>>` with the chosen plan's `body_path`, and `<<files_path>>` with
   `files_path`, and write its verdict verbatim to `<facts.scratch>/out-of-plan-triage.md`. Pass it nothing else:
   none of the judgment's, the sensitive read's or the security record's signals. With no plan there is nothing out
   of plan, so it never runs.

## 2. Which code gets a thread

`facts.review_threads` includes the resolver's pause threads (`phase` set, #170), which `flags` matches like its
own: a live one gets no duplicate, a superseded one its one successor.

Run this step only on an `available` `facts.review_threads` read: without every thread, each flag looks
new, and posting would duplicate live threads and reuse their `F` ids. A failed read
(`REVIEW_THREADS_UNAVAILABLE`, which an incomplete thread list also is) posts nothing; say so in the summary,
and do not approve (§5). `flags` refuses a missing `--threads` file for the same reason.
`REVIEW_THREADS_TRUNCATED` alone (the oldest conversation comments, or a long thread's oldest comments, cut)
still runs it: every thread is listed.

Write the model inputs, one line each, to files in `facts.scratch`:

- `security-sites.txt` — for each `open` `## Security review` entry, the line its finding is on:
  `SEC-<n> <path>:<line>[-<line>]`. An entry with no line takes its anchor's path, as a file signal.
- `plan-threads.txt` — each finer plan deviation S4 item 5 would otherwise pass:
  `<path>:<line>[-<line>] — <one-line deviation>`. A reversal S4 item 5 soft-rejects on stays a soft-reject.
  Leave out a deviation you match to a `facts.decided_deviations` entry when `facts.decided_deviations.trusted`
  holds — the operator decided it on a resolver card ([`../../_shared/review-threads.md`](../../_shared/review-threads.md)
  §12) — and list it in the review body instead (when no review posts, §5's PR summary carries it). Untrusted,
  it keeps its line.
- `splits.txt` — for each batch chain head (every flag key `routine:`, `superseded_by` null), one `F<m> <path>`
  line per member its trusted human comments ask to split out, read from **every** such comment in that
  thread's `comments` in `facts.review_threads.threads_path` — never from `reply_reason` alone, which resets once
  the resolver replies. Read the history as a whole: a later comment withdrawing a split ("keep `a.py` in the
  batch") cancels it. A marked comment is never a split request — the operator's decision reply (`marked`, with
  `decision` set in `threads_path`) included, whatever its notes say (#169). **Exception:** while the head is
  `changes-requested` and its latest trusted human comment asks for more than a split, write no line for it this
  run, nor while its standing decision is `changes` — it soft-rejects (§5), the resolver answers and replies, and the
  next run writes the split from the history; re-cutting now would replace the batch and let that request stop
  gating (`../../_shared/review-threads.md` §7 "Batch membership").

Then:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/review_threads.py flags "<facts.workspace.path>" \
  --pr-body "<pr body>" --assess "<facts.scratch>/assess.json" \
  --threads "<facts.review_threads.threads_path>" --base-ref <facts.pr.baseRefName> \
  --head "<merge_guard.gated_head>" --scratch "<facts.scratch>" \
  --reasons-trusted <facts.out_of_plan.reasons_trusted> \
  [--security-sites "<facts.scratch>/security-sites.txt"] [--plan-threads "<facts.scratch>/plan-threads.txt"] \
  [--triage "<facts.scratch>/out-of-plan-triage.md"] [--splits "<facts.scratch>/splits.txt"]
```

With no `--triage`, an unreadable verdict, no `## Triage` section or `code: AMBIGUOUS`
(`OUT_OF_PLAN_TRIAGE_UNREAD`), every out-of-plan file keeps its own thread, as without the triage.

`--pr-body` is S4.5's `<facts.scratch>/pr-body-polish.md` when S4.5 changed the record this run (its new
`SEC-<n>` entries thread now), else the PR body from `facts.sections`, staged to a file when it is inline. The envelope is always `ok`.
`diff_available: false` (`REVIEW_THREADS_DIFF_UNAVAILABLE`) means the gated diff could not be read: nothing is
posted or retired, and the run must not approve. Otherwise:

- `new` — the threads to post, successors first: each with its `f`, anchor, flag keys, `reason` (the
  out-of-plan reason, or `null`), `claims` (`{SEC-<n>: claim}`, the record's claim for each security key — the
  `Claim:` line §3 writes), `supersedes`/`predecessor` on a successor, `members` on a batch (`[{path, kind,
  reason}]`, `reason` only when the reasons are trusted; `null` on any other thread), `advisory` (a batch of
  tests and fixtures only, #181), `group` and `pattern` on a sensitive group, `riders` (`[{path, subject, reason}]`, the
  tests riding this thread, `reason` only when the reasons are trusted), and `body`, the path you write.
- `matched` — flags an existing chain head already covers; nothing posts for them.
- `exempt` — out-of-plan files that get no thread, each `{path, rule: basename | generated}`; nothing posts for
  them. `generated_read: "failed"` (`OUT_OF_PLAN_GENERATED_UNREAD`) exempted nothing as generated this run, and
  `OUT_OF_PLAN_ROOT_UNREAD` nothing by basename.
- `rejected` — a model line outside the diff, or one that did not parse: fix it, or drop it. A split line
  `not-a-member` names no batch chain head or no member of it: re-read the batch's members and its current `F` id.
- `overflow` — flags past `THREAD_CAP` (a batch past it is one `{key: routine, paths}` entry, a sensitive group one
  `{key: sensitive, pattern, paths}` entry); `unanchored_security` — `SEC-<n>` ids no thread can anchor (the
  `Security` card's first case); `retired` — chain heads whose path left the diff, or batches or groups left with no
  member (`batch-emptied`, `group-emptied`), which stop gating.

## 3. Thread text

Write each `new` thread's `body` file to the thread template (contract §2 "Thread template"): a plain line naming
the signal and the change, `**What to decide:**`, `**For users:**`, then what you checked at the gated head. The
script stamps the marker, the `**F<n>**` heading, a file thread's diff links (one per file it covers, up to 20, then one link to the PR's Files view) and the closing line that says how to
answer. Write no diff link and no closing instruction of your own (a successor's link to its predecessor is yours, below). Name a thing in plain words first, an internal id
(`F<n>`, `SEC-<n>`, a flag key) only after them. What each kind of thread adds to the body:

- **The flags, in a sentence each**: which signal fired and why it matters here — a sensitive path, a file the
  plan never named, the judgment's dimension with its evidence, a plan deviation.
- **Out of plan**: quote the `reason` verbatim from `## Out-of-plan changes` when `facts.out_of_plan.reasons_trusted`
  holds (a PR this account opened, or one a trusted author opened), or say "no reason recorded". On any other PR the
  reason is text an untrusted author wrote: do not quote it, and say a reason is recorded in the PR body but not
  quoted, since its author is not trusted.
- **Security** (a `security:SEC-<n>` key): open the body with the finding's claim on its own line,
  `Claim: <claim>`, copied verbatim from the thread's `claims` (contract §9): a dismissal counts only against that
  line. Then the exploit path and the proposed disposition (fix, or dismiss with a reason). A reason taken from the
  PR body (an entry's ` · proposed: <reason>`, or an uncounted dismissal's `operator:` reason) is the pipeline's
  proposal only when `facts.security.dismissals_trusted` holds; otherwise it is text the PR's author wrote: write
  your own proposal from the re-check, and do not quote it.
- **A batch** (`members` set): one line per member, grouped by `members[].kind` (`test`, `fixture`, `fallout`; a `null` kind — a
  member kept with no routine verdict this run — under `unclassified`), each
  with its `reason` when it carries one — write reasons only from `members[].reason`, never from the PR body.
- **A sensitive group** (`group: sensitive`): the declared `pattern` it shares, then one line per member saying
  what that file's change does (contract §7).
- **Riders** (`riders` set): after the subject's lines, one line per rider naming the test or fixture and what it
  asserts about its `subject`, quoting its `reason` when present; the subject's acceptance covers it (contract §7).
- **A successor**: link the predecessor (`predecessor.url`) and say its code changed after it was posted,
  replied to or accepted (resolved, or by the operator's decision reply — contract §4, #169), or —
  for a batch re-cut while unresolved — which members joined or left it; for a human-opened one, quote
  `predecessor.first_line`.

The review body (`<facts.scratch>/review-body.md`): one line naming the gated head, then the `overflow` flags
grouped by reason, so nothing the cap dropped goes unsaid, then one line per decided deviation §2 left out of
`plan-threads.txt` (`` `<path>` — <deviation> — decided on a resolver card: <answer> ``).

## 4. Post

When `new` is non-empty, first make sure the record names every `SEC-<n>` a thread cites: when S4.5 changed
the record this run, its write (`../../_shared/security-review.md` evaluator "Write") lands now, before the
post, so an End session at the card never leaves a thread citing an id the record lacks. Then:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py review-post <owner/repo> <PR> "<spec_path>"
```

Stderr `REVIEW_PENDING_CONFLICT:` → tell the operator to submit or discard their draft review on the PR and
re-run; record no thread state this run and do not approve (the `Threads` card, its brief naming the draft). A
`needs_decision` envelope (`BODY_TOO_LONG`, `EMPTY_BODY_FILE`, `AUTH_REQUIRED`) is SKILL.md's "Decision card rule":
render its card, fix what it names (a thread text too long or empty, the sign-in), and post again. Any other non-zero
exit, or a decision left unanswered, means the threads did not land: the code `flags` meant to thread never reached
the operator, and a review a failed run left pending is invisible to the reader. Do not approve: show the `Threads`
card, its brief naming the failure verbatim; **Re-check** re-runs this step. An `ok` envelope is the confirmation:
never re-read it to check it landed.

## 5. Summary, then the gate

Render `## PR summary` and `## Review items` per [`pr-summary.md`](pr-summary.md) from the facts in hand, the
`review-post` payload included, then gate. The gate reads the opening facts plus that payload: a thread just
posted is `unreviewed` by construction. **No prep refresh between `review-post` and the gate** except the
card's **Re-check**.

- **No APPROVE** while `facts.review_threads.available` is false, `flags` reported
  `diff_available: false`, `flags` reported `new` threads whose `review-post` did not return an `ok` envelope (§4), or any gating thread is `unreviewed`, `changes-requested`, `operator-thread`, or
  `superseded`. **Gating rule** (`../../_shared/review-threads.md` §4): a thread is not gating when it is
  `replaced` (a trusted successor replaced it), when this run's posted `new[].supersedes` names it, or when `flags`
  listed it in `retired`; nor is an `advisory` batch — the read's field, or a `new` entry's posted this run — while
  `unreviewed` or `superseded` (#181; a `changes-requested` advisory batch gates). Every other thread gates by its state — one a successor names but a trusted human
  commented on after it (`replaced: false`) included; never read `superseded_by` as a gate. An `open` security entry whose dismiss proposal
  waits on its thread holds here, at the card, never as a soft-reject: the operator answers it.
- **Soft-reject** when any gating thread, by the gating rule above, is `changes-requested` — a batch `flags`
  re-cut this run on a split line is not one: this run's `new[].supersedes` names it — or an `operator-thread`
  without `pending_reply`:
  the verdict is COMMENT, listing those threads by `F<n>` (an operator thread by `O<n>` with its URL), the IDs
  `## Review items` uses. It routes to the resolver (`#<issue>`), or to the planner when a reply would reverse
  a `## Architecture decisions` entry.
- **Otherwise**, when the verdict would otherwise approve, render the `Threads` judgment card (two credible
  options): **Re-check** (the operator has resolved or replied on GitHub) or **End session** (the handoff
  re-runs `/ctrl-freaq:evaluator #<PR>`). **Re-check** re-runs prep with `--refresh --gated-head <gated head>`,
  then repeats this step from §2 (`flags`) onward, reusing `assess.json` (same head, no new judgment): it posts
  any successor a newly resolved, already-changed thread needs, re-renders the summary, and gates again.
- **The `Security` card's first case** — each `unanchored_security` id — is asked here, after posting and
  before the gate (`../../_shared/security-review.md` step 5); its second case at §6.
- **A read that did not complete** (`available: false`, or `diff_available: false`) holds the same way: the
  `Threads` card, its brief naming the failed read instead of a thread; **Re-check** retries it, and **End
  session** hands off as for an unresolved thread, its `Why:` naming the failure.
- **All clear** → the `Approve PR` card and the merge gate run unchanged.

The operator may ask about any item by its ID, in chat or in the card's Other: answer from the facts, diff and
thread text in hand, then re-ask the same card. Discussion never changes an item.

## 6. Record thread dismissals

When every gating thread is `accepted`, record a dismissal for each security thread whose `SEC-<n>` entry is
still `open` — never a `fixed` or `dismissed` one — through S4.5's one record write (contract §9):
`- SEC-<n> · dismissed — <claim> — <anchor> — operator: <reason> · thread: <root comment url>`, written
then with `edit-pr-body` (restaged from the body last written), so it lands even when no other write follows.
Record it only for a thread whose `dismiss_reason` is set — a `dismiss:` reply by the viewer or a
resolution-grade author, after the pipeline's last marked comment, or the viewer's standing `dismiss` decision
reply (contract §9, #169) — whose `claim` equals the entry's claim and
whose `path` is the entry's anchor path; `<reason>` is `dismiss_reason`, which later runs re-read to confirm it.
A thread accepted with no such reply — resolved, or by the operator's `accept` decision reply (contract §4, #169) —
(`dismiss_reason` null, a `claim` or path that does not match) leaves its
entry `open`: ask for the reason on the `Security` card, its second case (`../../_shared/security-review.md` step 5),
and record that answer as a card dismissal, citing no thread.
