# Review fix round — main-loop procedure (spine S5.1)

Carried from the v1 resolver's review-loop sub-agent per the S10 cutover — the v1 §10 outer loop, whose
classification rubric (v1's §10.4) is split between this file and [`common-pitfalls.md`](common-pitfalls.md).
Until 4.11.0 this was a `general-purpose` sub-agent prompt dispatched once per iteration. It was retired
because the per-iteration cold start plus the JSON → card → re-dispatch serialization was slow and left the
operator with no view of what was being fixed (a sub-agent's tool calls don't stream to the parent). The
steps now run in the **main conversation**, which also already holds the plan, the audit, the doc grounding,
and the code it just wrote. `code-review` itself always ran in main and still does — it is unreachable from
inside an `Agent`-dispatched sub-agent (PR #607).

This is **not** a sub-agent prompt: no placeholders, no JSON return, and every gate is a direct
`AskUserQuestion` card you render yourself. The fix disciplines are no longer copied here — you hold
`common-pitfalls.md` in context, which is the file that owns them.

## What you hold at round entry

- **The verdict** — this round's `code-review` output (text or a `ReportFindings` call), or the cold read's
  `## Findings` when S5.1 step 4 fed you one. Classify it; act on it.
- **PR** number, URL, and `<owner/repo>` in continue mode — in fresh mode the PR does not exist until
  S5.2 and you hold the staged `<facts.scratch>/pr.md` instead; the originating issue and its parent epic (or none);
  `facts.audit_ref` (bare) as the integration target.
- **Workspace**: `facts.workspace.path` — the cwd for every command you run.
- **Iteration number** — the 1-based index in S5.1's outer loop, and the **previous round's defect
  count** (S5.1 step 3 compares this round's against it).
- **The record's counters** ([`../../_shared/review-loop-record.md`](../../_shared/review-loop-record.md)) —
  the pass's running count of `code-review` invocations for S5.2's `Review runs:` line, which you keep:
  add one for every `code-review` run S5.1 or finalisation makes in this pass, its light re-reviews
  included (not the emergency ceiling's count, which is per phase and leaves finalisation's out); and
  the highest `R<phase>.<seq>` and `P<phase>.<seq>` ids the current phase carries — posted (the `R` ids
  from the trusted loop comments in `facts.resume.comments_path`, the `P` ids from the ledger — the resume
  hint), or staged this pass — so step 8 continues each sequence.
  A `loop-comment.md` an interrupted run left unposted (spine S5 "Interrupted prior run"), or an active
  `facts.interrupted` record's own comment ("Resume hint" — a wrap-up posts and deletes its file), counts as staged: its
  ids seed both counters, and every item one of its lines records `ledger P<…>` is held again for S5.2
  under that id, its text and site from the line, since that run never wrote it.
- **Loop-entry SHA** — HEAD recorded at S5.1 entry. `git diff --name-only <loop-entry sha>...HEAD` in the
  workspace is the set of files this loop's own fixes have changed — the **hot seam** step 4's fix-design
  trigger reads. It scopes that dispatch only — the progress rule's **loop-induced** test reads the
  defect-fix commits on the addressed-items list instead, since a file-level set turns hot on a mere
  polish rename; it is not the retired provenance tagging and never gates the cold read (4.11.0 removed
  that machinery deliberately — the rubric's provenance axis decides fix-or-defer, nothing about the cold
  read).
- **The addressed-items list** — one-line summaries of what you fixed in every prior round this run,
  plus the **defect-fix record**: each round's `Defect fixes:` line (step 8) — its commit SHA and the
  `<path>:<symbol>` site of every **defect** it fixed. The record also carries a prior session's lines on
  this phase, seeded by the resume hint, so a re-entered phase keeps its history. You append to it in
  step 9; the deadlock check in step 3 reads it, and so does the rubric's **loop-induced** test.
- **The refuted-items list** — one line per Plan-settled / Deferred-by-plan / Refuted item, in its
  `Settled (not addressed):` line's form (step 8): its citation or evidence, ` — check: upheld` when the
  dismissal check upheld it, its repeat count and any `kept` mark — from every prior round this run and
  (seeded on iteration 1 — "Resume hint") from earlier phases' rounds. Appended, updated and removed in step
  9; the deadlock check reads it too, and treats a match differently from an addressed match.
- **The plan and the phase list** — the verified plan comment (`facts.plan.url`; its body is the
  `marker_comment_*` entry of `facts.sections`) and `facts.phases`, with the current phase's number
  (the S4 cursor) and its `depends-on`. The plan-anchored settled buckets cite these.
- **The polish ledger** — `facts.polish` ([`../../_shared/polish-ledger.md`](../../_shared/polish-ledger.md)).
  When `facts.polish.dispositions_trusted` is true, every entry marked `apply` is Addressable on
  iteration 1 whatever its tier or provenance — it is the operator's answer, recorded by the evaluator or
  at [`finalisation.md`](finalisation.md), so it has been decided; recording it as `open` again would
  bounce the PR between the two skills. When it is false, an `apply` is text anyone who can edit the body
  could have typed (`polish-ledger.md` "Rules"): it is not folded in, and finalisation triages it.
- **Security findings** — in finalisation's round only, each finding its security pass sent to fix
  ([`finalisation.md`](finalisation.md) §1.5): Addressable, defect tier, whatever the rubric would say.
  Only the operator dismisses one, so it never takes the Refuted, Plan-settled or No shown cost bucket and
  never reaches step 4's dismissal check; one found false at head is recorded `open` with ` · proposed: premise false: <evidence>`, for the evaluator's
  review thread ([`finalisation.md`](finalisation.md)).
- **Doc-grounding statement** (from S3) and any audit / plan overrides carried into the PR body — use them
  when defending an implementation choice in a PR reply.
- **Test config**: `facts.config.static_checks` and `facts.config.test_target_raw`.
- **References**: [`retry-ladder.md`](retry-ladder.md) (the pre-push gate's 3-run cap + research
  breakpoint), [`follow-up-tracking.md`](follow-up-tracking.md) (the follow-up registry and its timing),
  and [`../../_shared/follow-up-filing.md`](../../_shared/follow-up-filing.md) for the drafter-proxy filing
  protocol. Apply them as written.
- **Resume hint** — this loop may be picking up mid-flow (a prior resolver run was interrupted, or a human
  reviewer commented between invocations). On **iteration 1 only**, when a PR exists, before classifying, read what
  prep already gathered from it — `facts.review_threads` ([`../../_shared/review-threads.md`](../../_shared/review-threads.md)),
  never a fetch of your own — and treat as additional Addressable input alongside the verdict, and nothing else
  from the PR:
  - every `facts.review_threads.threads` entry in state `changes-requested`, or an `operator-thread` without `pending_reply` (§4
    of the contract: a trusted human is waiting on this run), that is not `replaced` (a replaced one gates nothing
    and its successor carries what is left; a named one a person commented on after its successor still waits —
    never filter on `superseded_by`). Its comments are in `threads_path` — a request may be the operator's marked
    `changes` decision reply (`decision: changes`, its notes in the thread's `reply_reason`, #169); note its `id`,
    `url` and `f` (or `o` for a human-opened thread) in the round's record now — S5.2 replies by that `id`
    ("Thread replies"), and a later prep (a `--refresh` one included) may no longer carry it. A `changes-requested`
    batch thread (every flag key `routine:`, #163) may name one member of many: address that member's code and reply
    in the batch thread by its `id`, never opening a thread of your own; a reply that only asks to split a member out
    is the evaluator's to apply, not a code change. The evaluator reads split requests only on a batch chain head, so
    on a batch a successor names (`superseded_by` set) a split request cannot be applied there: reply naming the
    chain head (follow `superseded_by` to the thread no successor names) and ask the person to repeat the split on it
    and resolve this thread;
  - the trusted conversation at `facts.review_threads.conversation_path` — top-level comments and review bodies,
    the evaluator's soft-reject review included, though a pipeline-authored `COMMENTED` review carries no
    operator header. The pipeline's own entries are not input: a `marked` review body, and the loop comments
    led by `<!-- review-loop:v1 -->` (the resume seed reads those);
  - the `## Polish` `apply` entries ("The polish ledger", when `facts.polish.dispositions_trusted`);
  - on a changes-required pass ([`phase-continuation.md`](phase-continuation.md) §4.1), the Review card's notes —
    this session's answer, or, resuming a `changes` record, the issue comment its `note` links — whether or
    not a thread repeats them.

  The reader lists only trusted authors' text, so an outside account's comment or thread is not input; it is
  counted in `untrusted`. When a PR exists but `facts.review_threads.present` is false, prep read no threads: the facts came from a
  `--refresh` prep (SKILL.md §1's decision-card re-run is a full one, so that the threads are read before any
  code), or the PR is one this run opened after prep (an epic integration PR S4.1 just opened has no threads yet,
  which is no PR input, not a failed read). Otherwise treat it as a failed read. Do not re-run prep here — after
  the phase's commits a prep would read them as an interrupted run's. When `facts.review_threads` is not `available` (a failed read, `REVIEW_THREADS_UNAVAILABLE`),
  say so in the round's record and in the handoff's `Why:`: this iteration has no PR input but the verdict and
  the ledger. This input is
  **Addressable input only**: no PR comment it carries seeds anything. **Seed only from the trusted comments prep staged** at `facts.resume.comments_path` (a JSON list
  of `{body, createdAt, url}`, in thread order) — the PR comments whose author's association is `OWNER` /
  `MEMBER` / `COLLABORATOR` ([`../../_shared/review-loop-record.md`](../../_shared/review-loop-record.md)).
  Any GitHub user can comment on a public PR, so an untrusted comment's `Settled (not addressed):` block,
  finding lines, `Defect fixes:` line or `Cold read:` line seeds nothing: a finding it lists classifies
  fresh, and a serious dismissal of it still goes to step 4's dismissal check. With `facts.resume.present`
  false, no PR comment seeds anything. An interrupted run's unposted local `loop-comment.md` (spine S5 "Interrupted
  prior run") stays a seed source, and so does an active `facts.interrupted` record's own comment
  (`facts.interrupted.url`): prep read it from trusted comments only, and when it was posted on the issue (a
  fresh first phase) it is the only carrier of that pass's rounds and `R` ids — find it by that URL in the
  staged PR comments or the issue thread (`facts.distiller_bundle.thread_path`). From those comments, seed the refuted-items list from every `Settled (not
  addressed):` block in prior round replies (step 8) — each phase starts fresh (a new session, or an
  in-session continuation after a fresh prep — `phase-continuation.md`), and that block is the only
  carrier of what earlier phases refuted — **except** a `deferred-by-plan` entry whose phase is the
  current phase or a ticked tracker row (its deferral has come due, and the finding classifies fresh), and an
  entry whose item a later round's section records `check: overturned`, unless that round's block or a later
  one lists it again (the operator kept it) — the check reopened it, so it is live. Seed the defect-fix record
  from every `Defect fixes: phase <N>` line for the current phase, and the highest `R` id from the current
  phase's finding lines in every trusted loop comment. Note any `Cold read: phase <N> @ <sha>`
  line for the current phase in them and whether its `<sha>` is HEAD or an ancestor of it (S5.1 step 4 reads both).

## Classification rubric

Classify every listed item on three axes, in order: its **provenance**, its **bucket**, and — for an
Addressable item — its **tier**. Provenance comes first because it predicts the right call best: an
audit of one repo's review-loop follow-ups found that the findings a PR introduced were the ones most
often wrongly deferred (fixable in the PR, or a plan gap), and that the ones it did not introduce were
correctly deferred four times in five. A finding on a doc change also passes the **materiality** test
after provenance and before its bucket. Record provenance, bucket and tier on each item's finding line in
the round reply (step 8), so the call can be audited.

**Provenance** — whose problem it is:

- **introduced** — the finding's site is in lines this PR adds or changes, or this PR's change is what
  makes it reachable or untrue: a path the PR opens, a comment or doc its change made false, a test or
  control it wrote that cannot fail. Or it is work the plan's `## Changes` / phase `ships` or the issue's
  Definition of done assigns to this PR, built or not — an unfinished planned change is this PR's, not
  pre-existing.
- **adjacent** — not in this PR's lines, but the same change this PR made applies at a **sibling site** —
  the same invariant, guard, helper, contract row or ruling, in the same file or seam — unless a later
  phase in `facts.phases` ships that site, which is Deferred-by-plan.
- **pre-existing** — neither: found in code this PR does not change and the plan does not assign to it.

**Materiality** — whether a finding on a doc change matters. It applies to every finding whose site is a
doc file or a code comment, in any PR. A finding about what code does is never tested here. The test:
would the doc or comment, as it stands, mislead a builder, reviewer or auditor into a wrong decision?

- **material** — yes: a documented key or contract that disagrees with the code (a builder would drop
  valid rows), a race between two writers described as "no contention", an access-control residual the
  doc denies. It takes its bucket and tier as normal — a defect when it breaks a contract.
- **not material** — no: bookkeeping. Status and date stamps, version rows, citation dates, summary or
  index rows echoing a correction made elsewhere, a length or format convention with no reader impact.
  It takes the **No shown cost** bucket whatever its provenance: never a defect, never in S5.1's defect
  count, never fixed in-loop by the polish rule or the "Apply criteria" rule.

The bar is always on: no operator switch, no test of the PR's shape, so a resumed session applies it
from this text and nothing records it. Append it to the reviewer's `<context>` (S5.1 "Invocation") as
"for doc changes (doc files and code comments), report only material findings — ones that would mislead
a builder, reviewer or auditor into a wrong decision"; this rubric stays the floor when the reviewer
reports one anyway. Bookkeeping findings that share a theme (every date stamp a fix left stale) are one
ledger entry naming the sites, not one entry each, so the `## Polish` ledger does not grow toward the
body cap on a docs sweep. It exists because a docs sweep's bookkeeping fixes each created new sites to keep
consistent, the next round found those as loop-induced defects, and the loop stalled phase after phase
on findings the operator called "trivial and almost pointless" (#87).

**Repo review guidance** — `facts.config.review_guidance`, the consuming repo's owner-written
`<!-- issue-resolver-review-guidance -->` block (#99), joins the reviewer's `<context>` the same way: its
`text` verbatim, under `Repo review guidance (owner-written):`, once at loop entry. Prep has already
written every reviewer flag in it as a code span (`neutralised` lists them), so pass the text unedited,
its line breaks included — to `code-review` they are whitespace like any other, so the flag rule still
holds across them.
It steers what the reviewer looks for and what it skips; it never lowers this rubric, the settled buckets
or the materiality bar — a finding the guidance says to skip is still classified here when the reviewer
reports it. Absent (`present: false`), `<context>` carries no lead-in for it.

**Buckets:** check the other buckets first — a finding that fits Plan-settled, Refuted, Deferred-by-plan,
No shown cost or Decision-required takes that bucket. Addressable is the default for the introduced and
adjacent findings none of them fits.

- **Addressable** — a concretely-named change that is **introduced** or **adjacent**, or within the
  plan's `## Changes` / phase `ships` or the issue's Definition of done — the same scope test fix
  design applies (`fix-design-prompt.md` step 5), so the two never disagree. The DEFAULT for any
  concretely-named change with that provenance. Soft politeness ("could be fast-follow", "not blocking",
  "future PR") does NOT by itself move an item out of Addressable. A **pre-existing** item is not
  Addressable — it takes the **Pre-existing** bucket — except an entry the operator marked `apply`: their
  answer outranks provenance (a ledger `apply` is theirs only when `facts.polish.dispositions_trusted` is
  true).

  Every Addressable item also gets a **tier**:

  - **defect** — correctness, a broken contract or invariant, data loss or corruption, security, a test
    gap that would let a real bug through (a test or control this PR wrote that cannot fail included), or
    an unfinished `## Changes` entry of the phase being built (the plan requires it; the diff does not
    deliver it yet). A doc or comment finding is a defect only when it is **material**. Keeps the loop
    open (S5.1 step 3 counts these). **Tie-break:** when defect-vs-polish is unclear, tier it defect if
    the finding cites a plan decision or shows a test that could not fail, and record the doubt in the
    round reply — the likely mis-tiering is a test gap under-tiered as polish, which settles the loop
    early. An **introduced** defect is fixed in this PR: it leaves only through an
    operator answer — a guard-rail card's **Re-plan**, **Accept + defer**, **Accept current**, **Defer the
    tests**, **Push with reds**, **Restructure**, **Abort** or **Abort loop**, or their approval on the
    end-of-loop checkpoint card (step 6's unprovable assertion) — never through a filing of your own, and
    the follow-up it becomes states why it was not fixed.
  - **polish** — everything else: naming, structure, comments, a small refactor. On **introduced** or
    **adjacent** code, fix it this round — Fix it **on merit**, where the merit is the polish being right,
    not cheap: a follow-up costs an issue, a plan and a session, more than the edit, and the audit found
    polish parked for its cost should have been fixed more often than not. Size alone never parks it.
    Five exceptions:
    - it needs a decision the plan does not make → **Decision-required**;
    - it is a refactor touching a guard, a raise, or any fail-closed path, which is not fixed as polish
      — a polish refactor that dropped a non-nil assertion turned a raising path fail-open, unnoticed by
      review — so record it for the ledger;
    - it reaches beyond this PR's seam (an extraction across modules, a convention changed in files this
      PR does not touch) → record it for the ledger;
    - it is a **material** finding on a doc the repo routes through a named command or process this
      session may not run → the **docs lane** (Explicitly-deferred); a non-material one is no shown cost;
    - it has no shown cost — taste (two reasonable forms, and the reviewer prefers the other), a
      speculative performance or memoisation win, hardening code the plan is removing, a doc or comment
      finding that is not material → record it for the ledger as `drop` with the note
      `no shown cost: <why>` (the **No shown cost** bucket).

    **Always fix in-loop, never ledger**, polish matching the evaluator's `apply` criteria
    ([`../../_shared/polish-ledger.md`](../../_shared/polish-ledger.md) "Apply criteria"), whatever it
    costs: parked, it only buys a revision session later. A doc or comment finding meets those criteria
    only when it is material. Polish never keeps the loop open. (This
    replaced the Cheap-fix-override bucket, which forced every ≤ ~20-line fix and fed an unbounded tail
    of polish rounds — and then a cost gate, which parked the PR's own small fixes as follow-ups.)
    "Record it for the ledger" means hold it for S5.2's `## Polish` write (step 8); the final pass's
    finalisation puts it to the operator ([`finalisation.md`](finalisation.md)) and the evaluator
    backstops.

  A defect is **loop-induced** when its cited site is a `<path>:<symbol>` an earlier round's defect fix
  changed — a site in the defect-fix record, this session's or a prior session's on this phase — or when
  it is the same change at a sibling site of a class fix already addressed (that fix was incomplete). The
  record names defect-fix sites only, because one round commit also carries that round's polish. A file
  touched only by polish or comment edits never makes a finding loop-induced, and a finding that is not
  material is never a defect, so it never counts as loop-induced either. S5.1 step 3 reads this;
  nothing else does.

- **Pre-existing** — what the reviewer found in code this PR does not change:
  - a defect or latent risk → **Explicitly-deferred**. A **security or privacy** defect (exposed personal
    data, an authorisation gap, a secret in a log) is its own follow-up group, never grouped with
    lower-severity items, and leads the handoff's "Follow-ups filed" bullet. When its fix is one site in a
    file this PR edits, fix it here, classified adjacent.
  - polish → record it for the ledger, unfixed; finalisation puts it to the operator.
- **No shown cost** — a claim with no reachable path or demonstrated cost: a speculative performance or
  memoisation win, a bypass with no instance, hardening code the plan is removing, a dev-only tool with no
  failure — and the bookkeeping class: a doc or comment finding that fails the **materiality** test.
  Record it for the ledger as `drop` with the note `no shown cost: <why>` — decided, so it never
  reaches a `Polish` card (finalisation's digest lists it, and the operator can revive it); never file
  it. It applies whatever the provenance — the PR's own code included, which the polish rule's
  no-shown-cost exception routes here. It is not Refuted: Refuted needs evidence the claim is false,
  and this one may be true and still not worth doing. A latent risk with a named trigger (a later issue, a
  second caller, a config change) is not no shown cost: it is Explicitly-deferred, latent until that
  trigger.
- **Explicitly-deferred** — routed elsewhere with a concrete tracking target (filed as #M, depends on an
  un-landed sibling, a citable PRD/scope exclusion). Record it in the follow-up registry for the
  end-of-loop checkpoint ([`follow-up-tracking.md`](follow-up-tracking.md)) — filed mid-round only when
  this round's commit needs its number. Four rules before it is recorded:
  - **Verify the owner.** A target named as "owned by #M" or "a later story" must carry the item: read its
    body or Definition of done with `gh_gather.py` ([`follow-up-tracking.md`](follow-up-tracking.md)
    "Before filing"). When it doesn't, the item is a gap — Addressable when introduced or adjacent, else
    Decision-required, **Re-plan** recommended.
  - **File the class, not the instance.** Items sharing a root cause are one entry naming the class and
    every known site. Before it files, search open issues for that root cause
    ([`follow-up-tracking.md`](follow-up-tracking.md) "Before filing"): a match is cited, never re-filed,
    and a site it does not name goes on it as one comment.
  - **Latent until X ships.** A risk that becomes live when a named issue X ships is recorded with X; once
    filed, X is marked blocked by it.
  - **The docs lane.** Drift in docs the repo routes through a named command or process is one entry per
    owning process per PR, one item per claim — never one issue per claim.
- **Decision-required** — an architectural / API-break / scope-change tradeoff the reviewer named candidate
  paths for: render the `Decision` card. For an **introduced** item the card offers no deferral path, and
  **Re-plan** is recommended when the plan leaves the behaviour open: a follow-up for a PR's own unfinished
  design ships the gap (in the audit, 32 of 49 deferred introduced defects left as "needs a design
  decision", and 25 of those were plan gaps).
- **Grounding-violation** — a diff that violates a documented constraint the issue/epic kept in-scope: if
  addressable here, fix it; else render the `Grounding` card. NEVER filed as a follow-up.
- **Plan-settled** — the finding contests a decision the plan records in `## Architecture decisions`,
  `## UI decisions`, or `## Deviations from project docs`. (A verbatim repeat of a refuted-items entry
  keeps the bucket that entry records — step 3 — so a repeated Refuted item stays Refuted.) Settled, not
  addressed: no edit, no follow-up; the PR reply quotes
  the decision bullet **verbatim** as the citation. No citation → the item is not plan-settled and stays
  in its default bucket — this bucket exists to stop re-litigation, never to dismiss a finding. A finding
  that *demonstrates* the decision is defective (a reproducible fault, a documented-constraint violation)
  is not plan-settled: it is Decision-required or Grounding-violation.

  **The still-true test** decides "contests". Imagine the fix done and re-read each decision bullet the
  finding touches, as written. **Now false** → the finding contests it: the fix reverses a choice the plan
  made, redefines a term the plan defines, or moves work the plan puts elsewhere. **Still true** → it does
  not, and the item is Addressable (a defect when it is correctness) — it adds a case the plan never
  named, and fixing it stays inside the plan. A list of **required behaviour** ("must refuse …", "checks
  …") is a **minimum**: a refusal it did not name leaves it true, unless the plan says "only" / "exactly"
  or records the omission as a choice. A **definition** ("X means …") is not: adding a case to what X
  means makes it false. So a "must refuse" list gaining a refusal is not settled; an "unanswered means …"
  definition gaining a case is; work "owned by #M" done here is. Settling a still-true finding hides a
  defect behind the plan it was meant to complete.

  **A plan defect is not settled.** Run the test the other way too: applied as written, does the decision
  produce the wrong behaviour the finding shows — a misleading message, a dead end, a double charge, lost
  input? Then the plan is wrong, not the finding: Decision-required, **Re-plan** recommended — never
  settled, never filed.
- **Refuted** — the finding is factually wrong or unreachable, shown by evidence you cite: a code read
  naming the guard or call site that makes it impossible, or a run or reproduction showing the claimed
  failure does not occur. Settled, not addressed: no edit, no follow-up; the PR reply carries the
  evidence. No evidence → not refuted, and the item stays in its default bucket. Disagreeing on taste is
  not refutation — that is polish.
- **Deferred-by-plan** — the finding names a seam, consumer, or wiring that a **later** phase in
  `facts.phases` ships (a phase after the current cursor). Settled: cite the phase number and title in
  the PR reply, file nothing — `Explicitly-deferred` files a follow-up, and an issue for work a planned
  phase already owns is a duplicate. A seam no phase ships is a gap, not deferred: it stays Addressable
  or Decision-required.

**Claim tier** — for every item not Addressable. Such an item takes no tier, but its **claim**, read as if
true, does: `defect` when it falls in the Addressable bucket's **defect** tier
(correctness, a broken contract or invariant, data loss or corruption, security, a test gap that would let
a real bug through — a test or control that cannot fail included — or an unfinished `## Changes` entry of
the phase being built), and when that is unclear — the tier tie-break, applied to the claim; otherwise
`polish`. It is the `<tier>` field of the item's finding line (step 8), so that field is never empty; step
4's dismissal check reads it on the Refuted, Plan-settled and No shown cost items. The one exclusion: a doc or comment finding that fails
materiality is never a defect. Under-tiering a claim skips the check, so the recorded tier is the audit trail.

## Steps (one round — no inner loop)

1. **Classify** every issue and suggestion per the rubric — provenance, bucket, then tier — reading the
   plan and `facts.phases` for the plan-anchored settled buckets. The reviewer's own "approved" verdict
   line is NOT the exit condition — re-classify each listed item, then tier every Addressable one, record
   the claim tier of every other one (the rubric's "Claim tier"), and give each its class key
   ([`../../_shared/review-loop-record.md`](../../_shared/review-loop-record.md)). On
   iteration 1, fold in the human PR activity from the resume hint and, when
   `facts.polish.dispositions_trusted` is true, every `apply` entry in `facts.polish`.
2. **Gates before any edit.** A Decision-required item → render the `Decision` card now. A
   Grounding-violation item that is **not** addressable on this PR → render the `Grounding` card now. When
   a finding matches **both**, render `Grounding` — a hard block outranks a soft approval gate. Act on the
   answer within this round. (A Grounding-violation that *is* addressable was reclassified Addressable in
   step 1 and is fixed in step 4; it is never filed as a follow-up.)
3. **Deadlock check.** If any item in the current verdict matches a summary in the addressed-items list
   (same file, same surface, same suggested change with no acknowledgement of your prior fix), render the
   `Review loop` card. Don't address it a second time on the same hypothesis. The same change at a
   **sibling site** of a class fix you already made is not a match — the hypothesis held and the coverage
   fell short: fix it per the class discipline, and it counts as loop-induced. The `Review loop` card is
   for these addressed-item deadlocks only. An item matching the **refuted-items list** verbatim — the
   same claim, carrying no reproduction, instance, or case the settled entry did not answer — is
   re-settled silently on its second occurrence: no card, no second reply and no finding line — a dismissal check's upheld
   verdict on it rides on its entry's suffix, and an overturned one re-enters classification, which writes
   its line (step 8) — only the entry's repeat count bumped — in the bucket the entry records, never re-bucketed.
   Whether it goes to step 4's dismissal check again is step 4's rule — an entry carrying `check: upheld` or
   marked `kept` does not. An entry marked `kept` (the operator answered **Keep settled**, or chose the
   decision as it stands on a `Decision` card — step 8 — this session or an earlier one) is re-settled
   silently on **every** verbatim occurrence: that question is answered. A repeat that **brings new evidence**
   is classified fresh on the merits — a `kept` one included, its mark dropped, and a refuted-items entry's
   ` — check: upheld` dropped too: the prior settlement, the operator's answer to it and the check's verdict on
   it are not a citation for evidence they never saw. A **recurrence that stays settled** — the third
   occurrence in one run, or a fresh classification that lands on Plan-settled again — bumps the count and
   takes step 4's **settled-item pre-check** this same round (never waiting for another
   occurrence, so a reviewer widening the finding cannot cycle it), then the `Settled item` card only if
   that check leaves it standing — the reviewer's persistence is evidence the citation may not answer it. An
   item already recorded for the ledger — held this pass, or on the PR's `## Polish` ledger — is not
   recorded again.
4. **Fix plan, then fix.**

   **Dismissal check** — first, once per round (plus one re-dispatch, for an unparsed
   verdict's items only), whenever at least one item this round dismissed
   qualifies, a round with nothing else to fix included: there it matters most. An item qualifies when
   step 1 classified it **Refuted**, **Plan-settled** or **No shown cost** and its **claim tier** is `defect`
   (the rubric's "Claim tier"), including a verbatim repeat step 3 re-settled silently. An item already
   checked is not dispatched again: a refuted-items entry carrying `check: upheld`, or a verbatim repeat of an
   item recorded for the ledger whose note ends ` — check: upheld` — held this pass or on the PR's `## Polish`
   ledger, read with any note rewrite held for it (step 8). One without the suffix (recorded before the check
   existed, or never given a verdict) is. An entry marked `kept` is not dispatched either: its operator
   answered **Keep settled**, or kept the decision as it stands on a `Decision` card (step 8),
   and a machine read must not re-raise an answered question — on an item the check raised, it
   would put the same card up again. A repeat that brings new evidence drops the mark (step 3) and is
   checked if it lands dismissed again. **Scope**, and
   why: No shown cost dismissals are checked — such an item "may be true and still not worth doing", so
   on a defect-tier claim it sets aside a possibly real defect with no evidence it is false, the rubric's
   own "a bypass with no instance" being a security claim. **Deferred-by-plan** ones
   are not: the deferral comes due when its phase is current (the resume hint classifies it fresh), and
   the final phase's cumulative review and cold read read it there — checking it now would verify a seam
   this phase deliberately leaves unbuilt. Explicitly-deferred and Pre-existing items are filed or
   ledgered for a person to see, so they are not dismissals. Stage the scope diff to
   `<facts.scratch>/dismissal-check-diff.patch` (`git diff <base>...HEAD` in the workspace, `<base>` per
   S5.1's scope rule) and dispatch the dismissal-check `Explore` sub-agent per
   [`dismissal-check-prompt.md`](dismissal-check-prompt.md) — **one** dispatch for every qualifying item,
   none when none qualifies — filling `<<items>>` with each item numbered: the finding verbatim with its
   site, its bucket, and its citation exactly as the round reply will record it (the Refuted evidence line,
   the verbatim decision bullet, the `no shown cost: <why>` note); `<<plan_decisions>>` with the plan's
   decision bullets; `<<plan_changes>>` with the plan's `## Changes (file-level)` section verbatim, read
   from the assembled plan at `facts.distiller_bundle.plan_marker_path` — the main comment holds only a
   stub link to it (plan text, so a refutation of an unfinished-`## Changes` claim is tested against the
   entry as the plan writes it); `<<phase_context>>` as for the cold read; `<<base_ref>>` with the scope base. Never
   the round history, prior verdicts or your reasoning. Print its `## Verdicts`, then route each item:
   - `upheld` → it stays in its bucket, recorded `check: upheld` (step 8).
   - `overturned` → it re-enters classification with the check's evidence as new evidence, and the
     rubric places it: **introduced** or **adjacent** → Addressable, tier `defect`, into this round's
     fix plan; **pre-existing** → Explicitly-deferred (a check never widens the PR's scope); a
     Plan-settled item whose decision the evidence shows is defective → Decision-required, **Re-plan**
     recommended — render step 2's `Decision` card now, its paths the decision as it stands and **Re-plan**,
     recommended. Recorded `check: overturned`. An overturned item
     is no longer settled, so it skips step 3's settled-item pre-check and its `Settled item` card; an
     upheld recurring one still takes them.
   - `code: AMBIGUOUS`, or an item with no parseable verdict line → repair the staging and re-dispatch
     once (those items only); still unparsed → tell the operator in one line naming those items, and
     continue the round with each in its bucket, its reply line saying the check returned no verdict.
     Never read it as `upheld`, so it carries no ` — check: upheld` suffix and is checked on recurrence.

   The check is not a `code-review` run: it counts toward no ceiling and resets nothing in S5.1 step 3.
   An overturned defect counts in this round's defect count, and the loop-induced test applies to it
   unchanged.

   Then the **fix plan**. Before the first edit, list the intended change of every item you will fix
   (each defect-tier Addressable item, each `apply` item — built to its note's `intent:` when
   it carries one — and each polish item the polish rule fixes) as text in this conversation — not in the
   PR reply, not in a file (on a hot seam, the fix-design dispatch — "Fix design on a hot seam" — comes
   first and supplies those items' lines); one line per item:
   `<item> — <file>:<function> — siblings: <the sites sharing the concept, the class per the first
   fix-discipline bullet> — interacts with: <other items this round: same file, same function, or a fix
   that alters another's premise> — plan: <any `## Architecture decisions` / `## UI decisions` /
   `## Deviations from project docs` bullet it touches, or none>`. Then check the list as a set, and only
   then edit:
   - Two fixes that cannot both hold → one change that satisfies both, or name which premise wins and
     drop the other from this round's edits (it stays listed; the PR reply says why).
   - A fix that reverses a plan decision → that item is not a fix: reclassify it Decision-required and
     render step 2's `Decision` card now, before any edit, its paths the decision as it stands (the item
     settles Plan-settled, citing it, marked `kept` — step 8) and **Re-plan**.
   An empty list (nothing to fix, after the dismissal check returns) needs no plan — step 5 owns that
   round.

   **Fix design on a hot seam.** Before writing the fix plan, dispatch the fix-design `Explore` sub-agent
   per [`fix-design-prompt.md`](fix-design-prompt.md) when any Addressable or polish item you will fix
   names a file in the hot seam (Loop-entry SHA — a cold-read-fed round included); dispatch it after
   writing the plan when the set check finds two fixes that cannot both hold or one that alters another's
   premise. At most once per round — except that a round whose dispatch the settled-item pre-check forced
   before the plan may dispatch once more when its set check then fails, so the pre-check never spends
   the set check's design. Stage the scope diff to `<facts.scratch>/fix-design-diff.patch` (`git diff
   <base>...HEAD` in the workspace, `<base>` per S5.1's scope rule) and fill `<<loop_files>>` with the
   hot-seam file list, `<<findings>>` with those items verbatim, `<<addressed_items>>` with the
   addressed-items list's surfaces only (no reasoning), `<<plan_decisions>>` with the plan's decision
   bullets, `<<phase_context>>` as for the cold read, and the scope from `facts.distiller_bundle`:
   `<<plan_path>>` ← `plan_marker_path`, `<<plan_shipped_path>>` ← `plan_shipped_path` (or `(absent)`),
   `<<issue_body_path>>` ← `issue_body_path`. Print its `## Seam` and `## Change set`, then adopt the
   change set as the plan lines for those items (main's own lines stand for the rest, and the set check
   runs over the union); a `## Plan conflicts` entry is Decision-required — render step 2's `Decision`
   card, as for a fix that reverses a plan decision; a `## Needs a plan decision` entry is
   Decision-required too, never adopted as a fix — the `Decision` card with its candidate designs as
   options plus **Re-plan** and, when the entry is **out of scope** (outside the diff, the plan, and the
   Definition of done), **File as follow-up**, recommended (scope creep is follow-up work —
   Explicitly-deferred, filed grouped; a re-plan would widen the issue); when the plan leaves the
   **intent open**, **Re-plan** is recommended. An `## Out of reach` entry (it could not read far enough)
   keeps your own plan line. `code: AMBIGUOUS` → repair the staging and re-dispatch once, else proceed on
   your own plan and say so. The sub-agent designs; you edit. It exists because a loop's own fixes kept
   creating findings on the seam they touched: each changed a guard, and nobody re-derived the sibling
   properties sharing it.

   **Settled-item pre-check** (step 3's recurring settled item), by the bucket it stands in. A
   **Plan-settled** item joins this round's fix-design dispatch — dispatch one when the round had none,
   still at most once per round — as a finding to design. Its `## Change set` has an entry and `## Plan
   conflicts` does not name it → it was never settled: reclassify it Addressable, tier it, and fix it
   this round with **no card**, the round reply noting "recurring settled item re-examined: fix design
   found no plan conflict". Named in `## Plan conflicts`, or in `## Needs a plan decision` with an open
   intent → the `Settled item` card, **Re-plan** recommended; in `## Needs a plan decision` as out of
   scope → the card, **Keep settled** recommended (the plan put that work elsewhere). Named in `## Out of
   reach` → the card, **Fix it here** recommended (it could not read far enough — the design failed, not
   the plan). A recurring **Deferred-by-plan** item skips the dispatch — a later phase already owns that
   seam, so the plan has placed the work and there is no design to decide: re-check its phase citation —
   no unshipped later phase ships it any more → classify fresh (a gap: Addressable or Decision-required);
   still owned → the card, **Keep settled** recommended. A recurring **Refuted** item skips the dispatch
   too: re-check its cited evidence — gone → classify fresh; still holds → the card, **Keep settled**
   recommended.

   Then fix every item the check left standing, applying `common-pitfalls.md`'s three fix-discipline
   bullets to each fix *before* writing it — "Don't fix the instance when the finding names a class",
   "Don't conform code to a stated invariant a finding contradicts", "Don't split an atomic call without
   naming its implicit properties". A retro found fix rounds carrying several times the defect density of
   the code they corrected, and a later run had three of eleven findings introduced by the loop's own
   fixes: the disciplines catch defects inside one fix, the fix plan catches the ones between fixes, and
   the fix design catches the ones between a fix and the seam it lands in. Record every Explicitly-deferred
   item in the follow-up registry per the rubric's four rules — urgency `file-at-checkpoint`, type per the
   reviewer's framing; `file-now` only when this round's commit needs its number (a `// TODO(#NNN)`
   marker, a skip annotation), filed via the follow-up filing protocol with its URL captured. Never file a
   Grounding-violation item.
5. **No edits** (no defect, no `apply` item, no polish fixed — every item Explicitly-deferred, settled,
   or recorded for the ledger), judged after the dismissal check returns: an overturned item that became
   Addressable is an edit; one that became Explicitly-deferred, or ended in a continuing `Decision` answer
   with no edit, leaves the round here → this round is complete. Skip steps 6–7 and step 8's commit; stage
   step 8's reply only when this round classified any item afresh (anything but a silent verbatim repeat —
   so it has finding lines to write, ledger items' ids included), was fed by the cold read, or the
   dismissal check recorded any verdict or returned none for an item (the `Settled (not addressed):` block,
   the `Cold read:` line, and the round's finding lines, each checked one with its `check:` field or
   no-verdict note). Then step 9, then back to S5.1 step 3, whose zero-defect branch settles the loop —
   whether or not the verdict's own line said approved.
6. **Defect-inject** every new or changed assertion step 4 added. An assertion written to catch a finding's
   defect does not count as coverage until an injection has made it fail: stage the fix first (the
   injection revert restores the staged state; committing waits for step 8, after the gate), inject the
   defect the assertion targets, run the assertion red, revert the injection. Run injections with the
   wrapper's targeted single-test invocation (the run-2 syntax in `retry-ladder.md`), batching
   distinct-site injections into one run — never the full selected suite per assertion. Green-against-the-
   defect means the test is vacuous — the usual shapes are "bad" state constructed after the code under
   test already read the good state, and an absence-assertion with no positive control proving it can ever
   fail. Rewrite and re-inject, at most twice per assertion; still green after that, register the
   assertion as a `deferred-test` follow-up for the end-of-loop checkpoint — its description says the
   injection stayed green after two rewrites — instead of looping; it files `file-now` only when a skip
   annotation in this round's commit needs its number, and the checkpoint card still lists it. Injection
   runs verify the test, not the diff, and do not count against the retry ladder's 3-run cap
   (`retry-ladder.md`).
7. **Run the §10.6 pre-push verification gate** (static checks → test-selection sub-agent → test
   execution). Dispatch the test-selection sub-agent with its **diff-base override** set to current HEAD
   (`git rev-parse HEAD` in the workspace): your commits wait for step 8, so HEAD is still the last committed,
   gate-verified state and your fixes are working-tree-only — the override scopes selection to what this
   round actually changed. Scope rationale and its accepted gap: `retry-ladder.md`'s "§10.6 selection
   scope". The retry ladder caps a single visit at 3 runs with a forced research breakpoint between cheap
   and deep fixes. On escalation, render the `Tests red` card.
8. **Commit. Stage the reply** — append this round's section to `<facts.scratch>/loop-comment.md`, under
   `### Round <k>`, in the record's format
   ([`../../_shared/review-loop-record.md`](../../_shared/review-loop-record.md) — the marker, headings,
   finding line, which items get none, and the `Review runs:` line S5.2 adds are defined there, never
   here): the round's defect count on its own line (the stall card's evidence), then one finding line per
   classified item, minting its `R` id, recording where it ended and what changed. Hold every item this
   round recorded for the ledger — polish it did not fix, pre-existing polish, and no-shown-cost items
   with their note — for S5.2's `## Polish` ledger write, minting its `P<phase>.<seq>` id now
   ([`../../_shared/polish-ledger.md`](../../_shared/polish-ledger.md) "Format"), the id its line's
   `ledger` action names; and every `apply` item it fixed for its `applied (commit <sha>)` update. An item
   recorded with no fix round (a light re-review's polish, an **Accept current** cold read's findings) is
   staged the same way, in a section of its own (the contract's "Items recorded outside a fix round"). Nothing is pushed or posted
   here: S5.2 pushes once and posts the whole file as the loop comment, because every per-round push
   started CI on code the next round was about to change. That comment is the GitHub-side record — how
   a reviewer, and the next session, follows what this loop did without replaying the conversation. When the
   round settled anything new, marked an existing entry `kept` (re-listed with its mark), or the dismissal
   check upheld an existing refuted-items entry (re-listed with its suffix and count), the reply ends with
   this fixed block — the next session's resume seed (`facts.resume`, the trusted loop comments — "Resume
   hint") seeds its refuted-items list from it (each phase
   starts fresh, in a new session or an in-session continuation; this comment is the only carrier):

   ```
   Settled (not addressed):
   - plan-settled — <one-line item> — cites: <section> "<decision bullet, verbatim>"[ — check: upheld] (×<repeats>)
   - deferred-by-plan — <one-line item> — phase <N> "<title>" (×<repeats>)
   - refuted — <one-line item> — evidence: <what was read or run>[ — check: upheld] (×<repeats>)
   ```

   `(×<repeats>)` is omitted on a first occurrence; carrying it is what lets the count survive a phase
   boundary. An entry the operator answered **Keep settled** on, or kept by choosing the decision as it
   stands on a `Decision` card (step 4's fix plan, fix design's or the dismissal check's), carries `kept`
   in the same parenthetical (`(×3, kept)`), so the next session's resume seed never asks the question again.

   A round whose **dismissal check** ran (step 4) records each verdict in two places. Every checked item's
   finding line carries `check: upheld` or `check: overturned` (a silently re-settled repeat has none: its
   verdict rides on its `Settled (not addressed):` entry). An upheld Refuted or Plan-settled item's `Settled (not addressed):` entry carries
   ` — check: upheld` before its repeat count, so the next session's resume seed re-settles a verbatim repeat
   without another dispatch; an overturned one is not re-listed — its `check: overturned` line in the
   round's section is what the next session's resume seed (`facts.resume`) reads to drop it — unless the
   operator kept it on the `Decision` card, when it is re-listed marked `kept`. An upheld No shown
   cost item's ledger note is `no shown cost: <why> — check: upheld` — the `no shown cost:` lead
   finalisation's digest keys on is unchanged — and an existing ledger entry without the suffix that was
   upheld on recurrence is held for S5.2 to rewrite in place, appending ` — check: upheld` to its note, so
   it is checked once. A note rewrite held for S5.2 is the entry's note for every reader this pass — step
   4's exclusion and finalisation's digest included. An overturned item leaves the ledger as a live item:
   held this pass, it is not recorded; already on the PR's `## Polish` ledger, its entry is held for S5.2 to
   rewrite in place to `drop` with the note `reclassified: defect — see loop comment` — finalisation's
   record for an entry found to be a defect ([`finalisation.md`](finalisation.md) "3. Triage"), kept rather
   than deleted. Its note no longer leads `no shown cost:`, so finalisation's digest stops listing it as
   dropped, and the evaluator does not re-check it (a `reclassified: defect` drop is its S4's to judge,
   `polish-adjudication.md` §1).

   A round that fixed a defect also carries one `Defect fixes: phase <N> @ <sha> — <path>:<symbol>;
   <path>:<symbol>` line: the round's commit and every defect fix's site, never a polish site. `<N>` is
   the phase number (in a revision run, the last code phase's — the ledger id rule), so the next
   session's resume seed (`facts.resume`) finds it.

   A round fed by the cold read (S5.1 step 4) also carries the `Cold read: phase <N> @ <sha>` line. A
   round that dispatched the fix design names the seam it re-derived in one line, so a reviewer sees why a
   fix reached past the finding's own site.

   **Decided deviations.** When a `Settled item` or `Decision` card's answer this round makes the build depart
   from the plan at a code site in the PR diff — a settled item fixed here, a decision kept as it stands that
   the diff builds differently from the plan's text, a named path that changes what the plan said — hold its
   line for S5.2's PR-body write, in the same body update as the ledger:
   `` - `<path>` — <the deviation, one line> — operator: <the card answer, one line> ``
   ([`../../_shared/review-threads.md`](../../_shared/review-threads.md) §12). The evaluator then posts no plan
   thread re-asking it. An answer with no code site gets no line: it stays in the session summary.
9. **Record.** Append this round's one-line item summaries to the addressed-items list, and its
   `Defect fixes:` line to the defect-fix record, and its settled items to the refuted-items list, each
   as its `Settled (not addressed):` line — a new item appended, an existing entry this round re-settled,
   upheld or marked `kept` updated in place, and one the dismissal check
   overturned removed (the item is live, unless the operator kept it on the `Decision` card) — so a later
   round's steps 3 and 4 read the verdict; keep its
   defect count for the next round's comparison. Hold the filed follow-up URLs for the PR body and the
   handoff. Carry any **procedural note** (something the next
   session should know that is not worth an issue) as a capture-not-file item per
   `follow-up-tracking.md` — it lands in the PR body or the handoff `Why:`, never as a filed issue.

## Thread replies

Spine S5.2, after the phase's push, once per pass, on a **settle** or **Accept current** exit from S5.1 — a pass
that committed nothing and so pushes nothing replies too, since a refuted or plan-settled thread left unanswered still
reads as requested and the evaluator would soft-reject on it again. A **terminating** exit (**Re-plan**,
**Restructure**, **Abort**, **Abort loop**) replies in no thread: a reply is a marked comment, which takes the thread
out of the next run's input, and that pass addressed nothing. Reply once in every thread iteration 1 took as input
("Resume hint"), by the `id` its round recorded, saying what changed and at which commit, or why nothing did (refuted,
plan-settled, deferred with its follow-up URL). Stage each reply to `<facts.scratch>/thread-reply-<k>.md` and post it
at the pushed head (`git rev-parse HEAD` in the workspace after the push, 40 hex — the PR's tip either way):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py review-reply <owner/repo> <PR> --thread "<thread id>" \
  --head "<pushed sha>" [--f <n>] "<facts.scratch>/thread-reply-<k>.md"
```

`--thread` is the thread's `id`, `--f` its `f` when it has one; the script stamps the marker
([`../../_shared/review-threads.md`](../../_shared/review-threads.md) §2, §10). These replies never pass `--decision`:
it records only an answer the operator gave on a consent card in the same session (§10, #169), and a fix-round reply
is the resolver's own. `posted: false` — a human resolved the thread after prep read it (`refused: resolved`), the
operator accepted or dismissed it in a chat session since (`refused: decided`), or the id is on another PR or not a
thread — skips that thread:
leave it out of the handoff's `Why:`, which names only the threads replied to. A non-zero exit posted nothing: name
that thread in `Why:` as not replied to, with the stderr line. The reply leaves the thread unresolved: the resolver never resolves or unresolves a
thread (§11). The human reads the reply and resolves it in GitHub, or answers in it, which puts it back in the next
run's input: a pipeline thread as `changes-requested`, an operator-opened one as `operator-thread` with `pending_reply`
false.

The threads iteration 1 reads include the resolver's own **pause threads** (`phase` set,
[`phase-continuation.md`](phase-continuation.md) §2.2) once a person replies to one; they are pipeline threads like
the evaluator's, answered the same way.

## Guard rails — direct cards

Render each via `AskUserQuestion` per [`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md)
at the point it fires. An answer settles that gate for the phase — don't re-raise it on a later round.

Two kinds of answer, and the difference is load-bearing. A **continuing** answer (Try another angle,
Accept + defer, Fix it here, Keep settled, File as follow-up, Push with reds, Defer the tests, a named
architectural path)
is acted on inside this round, which then finishes normally. A **terminating** answer — **Re-plan** (from
the `Decision`, `Grounding` or `Settled item` card, or the stall card's churn option) and
**Restructure** (re-route to the planner), **Abort** and **Abort loop** — ends the round *and* S5.1 on
the spot, except "The Re-plan independent-defect pass": stop fixing, run no further gate, and hand
back to S5.2 (it pushes what is committed), then S7 — the end-of-loop checkpoint for the follow-ups this
loop registered, so a terminating exit never drops one — and then the routed playbook's handoff, quoting
the trigger in the `Why:`. Don't try to satisfy a re-route inside the round; there is nothing here that can.

**The Re-plan independent-defect pass.** A Re-plan leaves the round's other defects behind, and some are
unrelated to what the re-plan will reshape — a run once handed the planner two such defects it could have
fixed. Before S5.2, fix each still-unfixed defect-tier item of this round that is **independent** of the
re-planned finding; both must hold:
- its fix site shares no `<path>:<symbol>` with the re-planned finding's site or the fix-design seam
  drawn for it — when no seam was drawn (a Re-plan from `Decision` or `Grounding`), sharing a **file**
  with the re-planned finding's site stands in for sharing its seam;
- its fix would be the same whichever way the re-plan goes — it neither reads nor changes what the
  re-planned decision governs.

When in doubt, it is **coupled**: fixing code the re-plan is about to reshape is wasted or conflicting.
Where the answer arrives decides what is left to do:
- **A `Decision` or `Grounding` card raised in step 2** (from the verdict, before any fix plan) → run
  **steps 4 and 6–8 for the independent subset**: step 4's dismissal check, fix plan with its set check,
  defect injection, the §10.6 gate, one commit. An item the check overturns into Decision-required there
  is coupled: recorded, with no second card.
- **A `Decision` card raised by step 4's dismissal check** (an overturned Plan-settled item, before fix
  design and the fix plan) → as for a card raised in step 2, except that the dismissal check has already
  run this round and is not dispatched again: its verdicts stand, and an item it overturned into
  Addressable joins the independent subset when it is independent.
- **A card raised in step 4** — the `Settled item` card, or a `Decision` card from fix design's
  `## Plan conflicts` / `## Needs a plan decision` or the fix plan's plan-reversal check (after this
  round's fix design and fix plan) → keep the existing plan lines for the independent subset and drop
  the rest — no second fix-design dispatch — then steps 6–8.
- **Stall card** (S5.1 step 3) → **no pass**: the round's fixes are already committed, and the churn that
  offered Re-plan says its defects are coupled to the loop's own sites.

Either way **no further `code-review`**: the next session's final-scope review re-reads them. A gate that
escalates past
the retry ladder → revert those fixes and record them, with no `Tests red` card. Coupled defects are
recorded in the round reply, the PR body, and the handoff `Why:`. **Restructure** (the gate is already
red) and **Abort** / **Abort loop** (the operator asked to stop) take no pass.

- **Same-feedback-twice deadlock.** The current verdict flags an item matching the addressed-items list.
  Don't address it a second time on the same hypothesis. `header: "Review loop"`, options: **Try another
  angle** (continue with a different fix — the operator can name it in the free-text "Other"), **Accept +
  defer** (stop fixing it, file the item as a deferred follow-up), **Abort loop**.
- **Settled item recurring.** Step 3's recurring settled item that step 4's pre-check left standing.
  `header: "Settled item"`, options with the recommended one first (step 4 says which): **Re-plan**
  (terminating; offered only for a plan-anchored item — Plan-settled or Deferred-by-plan — since the
  question is whether the plan is wrong), **Fix it here** (reclassify Addressable, fix this round),
  **Keep settled** (recorded as `kept` on the entry, step 8 — never re-raised, this session or later).
  Each `description` quotes the
  settlement's citation or evidence and, when fix design ran, its conflict or out-of-reach entry. Never
  **Accept + defer**: a plan question is not a follow-up.
- **Decision required.** The verdict flags an architectural choice, an API break, or a scope-change
  tradeoff. Don't guess. `header: "Decision"`, with one option per candidate path the reviewer named, each
  `description` carrying the reviewer's framing for that path — or, when step 4's fix plan raised it, the plan
  decision as it stands (the item settles Plan-settled, marked `kept`) and **Re-plan** — or, when step 4's
  dismissal check raised it (an overturned Plan-settled item), the plan decision as it stands (the item stays
  Plan-settled on that answer, marked `kept`) and **Re-plan**, recommended — or, when fix design returned
  `## Needs a plan decision`, its candidate designs (each `description` naming the intent it serves) plus
  **Re-plan**, and **File as follow-up** for an out-of-scope entry, with step 4's recommendation first. For an
  **introduced** item no deferral path is offered — no **File as follow-up**, no reviewer-named "defer
  it" — and **Re-plan** is recommended when the plan leaves the behaviour open (the rubric's
  Decision-required bucket).
- **Verification failure.** The retry ladder ran 3 times and the gate is still red. `header: "Tests red"`,
  options: **Push with reds** / **Defer the tests** / **Restructure**, per the retry-ladder Escalation
  section.
- **Grounding violation (in-scope), not addressable here.** A finding cites a diff that violates a
  documented constraint the issue/epic kept in-scope, and the fix can't ship on this PR (an integration PR
  of already-merged code, or it needs a plan/story). Don't file it — this is a hard block.
  `header: "Grounding"`, with options generated from the routes available and **no** defer/ship option (the
  violation must not reach the integration target): **Re-plan** (route to the planner to revise the plan —
  for an epic the planner scopes the missing in-scope work as a story) and **Abort**. Each `description`
  carries the violated doc citation and the in-scope evidence.

The stall card and the emergency ceiling are not fix-round gates: S5.1 tracks the defect counts and
asks. Its **Accept current** description names each still-open defect with its provenance, introduced
ones first: an introduced defect leaving this PR as a follow-up is the operator's call, made with that
list in view.
