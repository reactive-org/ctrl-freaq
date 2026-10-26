# Phase continuation — the checkpoint after a non-final phase ships

Read at the spine's "Return to the routed playbook" when a phase has shipped and an unshipped phase
remains (S5.2 pushed it, S6 projected it, S7 filed its follow-ups). §2 is read earlier, at S5.2, which
decides the checkpoint and records it before the loop comment posts. It decides whether this session
ships the next phase too or ends with a session summary and the non-final handoff. This **phase checkpoint** is not
[`follow-up-tracking.md`](follow-up-tracking.md)'s end-of-loop checkpoint: that `file-at-checkpoint`
batch ran at S7, minutes earlier, and is never a reason to pause.

**The rule this file serves:** a continuation is a new **pass** through the spine — a fresh
continue-mode run in everything but the conversation. Each phase gets its own prep, push, tracker tick,
DoD projection, cold read, and loop comment, seeded from GitHub exactly as a new session's would be — so
the evaluator, the tracker, and a later session cannot tell which phases shared a session. Wherever the
spine or its references scope a rule to "this run" or "one run", it means this pass. The `## Handoff`
ends the **session** (SKILL.md §4), never a pass: a pass that continues emits none, except that a review
(§4) prints one before its card, and that one stands unless the session continues.

Two questions meet here and stay apart. **Review** is whether a human must look at the shipped phase before
the next one builds on it (§2's triggers, §4's card). **Session length** is whether this session has room
for another phase, a rule the resolver applies to its own context usage (§4.2) and never asks about.

## 1. Hard stops — no card, the existing handoff

The next phase is the first unticked row of the `## Phase tracker` S6 just wrote whose `depends-on` is
satisfied — never selected by title.

- The next phase is `operator` / `decision-only` — mid-plan, or trailing the last code phase → the
  operator-phase handoff. The resolver cannot run it, and the PR stays draft: the ready flip waits for
  every phase to be ticked (the spine's "Return to the routed playbook").
- The run is exiting on a re-route, or is a revision run → that run's own handoff; there is nothing to
  chain.

Only a shipped **last planned** phase skips this file — nothing remains, and the spine's ready flip and
forward handoff own it.

## 2. Review, continue or end

Decide **continue**, **review** or **end** at S5.2, after the push and its `end` usage reading (§4.2) and before
the loop comment posts, and record it there as the pass's `Checkpoint:` line (§2.1):

- `end` when the session ends here whatever the answer: it holds `--pause` (SKILL.md §1), the operator
  asked to stop after every phase; the pass is leaving on a terminating answer (**Re-plan**,
  **Restructure**, **Abort**, **Abort loop**), so its routed playbook's handoff follows; the next
  unshipped phase in `facts.phases` (by `depends-on`) is `operator` / `decision-only`, §1's hard stop; or
  **`context`** — the `end` reading's band is `finish` or `wrap`, or its `projection_crosses_wrap` is true:
  the session has no room for another phase (§4.2).
- Otherwise **review** when **any** of these holds — a **trigger**, each meaning a human should review this
  phase before the next phase builds on it:
  - the shipped phase's `checkpoint` (`facts.phases`) is not `continue`: `pause`, or `null` (absent: a
    plan authored before the key, or a phase the planner left unmarked);
  - a **warning sign** fired during this phase's pass:
    - a gate card rendered — `Audit`, `Doc conflict`, `Tracker`, `Tests red`, `Review loop`,
      `Settled item`, `Decision`, `Loop stall` (the emergency ceiling included) — whatever the continuing
      answer was;
    - this pass wrote an override or failure record to the PR body — `## Audit override`,
      `## Plan override`, `## Known failures`, an `Accept current` override, a `## Tracker reconciliation`
      note;
  - the shipped diff raised its review level: S5.2's `blast_radius.py assess` call
    ([`review-level.md`](review-level.md) §5) reports `raised` (a declared sensitive path, a failed
    sensitive read, a file the plan does not name, or the blast-radius judgment's `raise`, in what this phase
    shipped).
- Otherwise `continue`.

On the **no-usage path** (§4.2: the host has no usage tool, or its reading carries no auto-compact point,
band `unknown`) there is no `context` end and no card: a trigger is `end` with the review printed (§4's
shape, the review-required line, the summary and the handoff), and `continue` continues.

Signals only add reviews: each turns `continue` into a review, and nothing in this list turns a review into
`continue`. Only the operator's **Reviewed, continue** answer on the Review card does, and only while the
context rule leaves room (§4). The context rule only ends a session earlier — it never turns a review into
`continue`. The planner's key and the operator's flag are the floor, and "this phase was routine" is a
judgment about your own work. Not warning signs: the S7 follow-up confirmation, settled findings (Plan-settled,
Deferred-by-plan, Refuted), and cold-read findings the loop fixed. S6 and S7 run after this decision and
raise no card that is a warning sign, so deciding at S5.2 loses nothing.

### 2.1 The record

Stage, immediately before the pass's `Review runs:` line, on a **non-final** pass (and on any pass leaving
on a terminating answer, which records `end`):

```
Checkpoint: phase <N> · <continue|review|end> — <why>
```

`<why>` names each trigger: the `checkpoint` value, each warning sign (the card's header, the record's
heading), and the S5.2 call's `why` when it raised. `end` names which ending: `--pause`, the terminating
answer, §1's operator-phase hard stop, `context` (with the reading's share, and `; review required:
<trigger>` when a trigger also held), or the no-usage path's `no usage reading; review required: <trigger>`.
The **review** value records that a Review card was raised; a changes-required pass records `review — changes required`
(§4.1). The operator's answer is not known when the comment posts, so it lands in the session summary and in
whether a next pass exists. The shape is
[`../../_shared/review-loop-record.md`](../../_shared/review-loop-record.md)'s, whose reader skips the line.

## 3. The phase link

`<pr-url>/files/<entry-sha>..<head-sha>` — the PR's Files-changed view scoped to **this phase's** push.
`<entry-sha>` is this pass's prep `facts.workspace.upstream_sha` — the pushed tip the phase started
from, which is `facts.workspace.sha` unless an interrupted run left commits unpushed (those are this
phase's work, so the link must include them). It is not the session-entry anchor the handoff's
`Changes:` line uses. `<head-sha>` is `git rev-parse HEAD` in `facts.workspace.path` after S5.2. When
the entry SHA is not a commit in the PR — fresh-mode phase 1, where it is the PR's base commit and the
whole PR is this phase — link `<pr-url>/files`: the discriminating test of
[`../../_shared/handoff-format.md`](../../_shared/handoff-format.md)'s `Changes:` rule. The PR url is
`facts.prior_pr.url`, or the `create-pr` envelope's `url` on a fresh first phase; SHAs in the text are
7-char. Use the whole-PR form too when an epic run's drift rebase rewrote the entry SHA out of the history
(GitHub 404s the range form there).

The link and a link per file the summary's "What to check" names are a script's, so the model never
assembles a Files-view anchor:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/phase_review.py links --pr-url "<pr-url>" \
  --entry <entry-sha> --head <head-sha> <path>...
```

Omit `--entry` and `--head` for the whole-PR form. `phase_url` is the phase link; each `files[].url` opens
its file in that view (`#diff-<sha256 of the path>`, GitHub's anchor — if that convention changes, the link
still opens the phase view). A changes-required pass links from the shipped phase's entry SHA to its new head
(§4.1).

## 4. A trigger raises a review

A review is a human-review checkpoint: one of §2's triggers says a human should look at this phase before
the next one builds on it. The card says so in its header and question, so an operator answering a stream
of cards cannot read it as a routine "carry on?". Whether the session then has room to go on is not the
card's question; the context rule answers it from the usage reading at the answer (§4.2).

**`end`** (`--pause`, a `context` end, or a trigger on the no-usage path) asks nothing and ends the
session. Print one line, so the link is clickable in the transcript, ending in the one that applies:

> **Phase <N> — <title> shipped** (<K> of <M>) · <phase link> · paused: `--pause`
>
> **Phase <N> — <title> shipped** (<K> of <M>) · <phase link> · ended: context at <share>% of auto-compact[ · review required: <trigger>]
>
> **Phase <N> — <title> shipped** (<K> of <M>) · <phase link> · review required: <trigger>

The second form adds `· review required: <trigger>` when a trigger also held; the third is the no-usage
path's. Then the
`## Session summary` block ("The `## Session summary` block"), then the non-final handoff (§6). Nothing
follows the handoff. A trigger ending the session this way still owes the operator the review: the summary's
"What to check" carries the phase link and its file links.

**review**, in this order:

1. In one message, print the line ending `· review required: <the trigger>` (`checkpoint: pause`, the
   warning sign, the raise, or `changes required` after §4.1), then the `## Session summary`, then the
   non-final handoff (§6), then render the card (step 2). Whatever happens to the card, the operator has the
   ending they would have had. In a coordinator-started session (the stage brief is in the opening prompt),
   the host mirrors that message to the coordinator
   ([`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md) "Relayed cards"), and the handoff
   carries `(provisional: a review card is open)` on its own line above its heading, outside the block.
2. The consent card, `header: "Review"`. Its question opens `Human review required before phase <N+1>`
   (after the relayed-card identity line, `resolver · #<N> · review`, in a coordinator-started session). The
   brief names the trigger, carries the phase link (§3), and lists the summary's "What to check" items, each
   linking its file in that view (§3's `links`); it says that changes can be written in the card's notes, and
   that unresolved review threads on the PR count too. Offer **Reviewed, continue** (ship the next phase in
   this session, when the context rule leaves room), **Reviewed, changes required** (a fix round on this
   phase first, §4.1) and **End session** (the printed handoff stands). **Reviewed, continue** is listed first
   but never marked "(Recommended)": the card asks for the operator's review, and the resolver does not
   recommend having reviewed. A "more" answer re-asks the same card.
3. On the answer, take the `answer` usage reading first (§4.2), then:
   - **Reviewed, continue** → §5 only when the band is `under` and `projection_crosses_wrap` is false. Any
     other reading → one line, `context at <share>% — ending here`, and the printed handoff stands as the
     session's.
   - **Reviewed, changes required** → §4.1 at any band below `wrap`. At `wrap` → record the notes
     (§4.1 step 1), post the pending-round record (§4.2 "90% wrap-up"), print one line saying the round is
     recorded for the next session, and the printed handoff stands.
   - **End session**, or the host's question timeout → one line saying so; the printed handoff stands.

   Whenever the printed handoff stands, nothing follows it. In a coordinator-started session, the finishing
   message to the coordinator is that handoff, verbatim, without the `(provisional: …)` line (no card is open
   any more): the session ended cleanly at its checkpoint, so the brief's "When you finish, send your full
   `## Handoff` block" applies, not its "stop early (a card left unanswered …)" one-liner, which is for a
   session that stopped without a handoff. On **Reviewed, continue** within the context rule the printed
   handoff is superseded, and the session's final handoff, with the session-wide `Changes:` line, is the one
   that counts.

**`continue`** → print the same line ending `· continuing to Phase <N+1> — <title> (checkpoint:
continue)`, and continue (§5).

### 4.1 Changes required

**Reviewed, changes required** runs a new pass of the **shipped** phase, not the next one — a revision of
work already pushed, like the spine's revision run (S4):

1. **Record the notes first.** When the answer carries notes (the card's notes field, or an Other answer's
   text), stage them to `<facts.scratch>/review-notes-<k>.md` (`<k>` counting this session's rounds) under
   the heading `Review — phase <N> @ <short-sha> — changes required`, and post them before any edit:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> issue <N> "<facts.scratch>/review-notes-<k>.md"
   ```

   Add the comment's URL to the session's **own-comments list** (§5 step 4).
2. **Re-run prep in full** (§5 steps 1–2), so `facts.review_threads` is read at the pushed head. Take the
   `start` reading for phase `<N>` (§4.2): below `wrap` it ends nothing and runs no projection test — the
   round finishes the shipped phase rather than starting a new one; at `wrap` take the pending-round path
   (step 3 of §4, at `wrap`).
3. **The change set** is the notes plus every thread the resume hint already takes
   ([`review-fix-round.md`](review-fix-round.md) "Resume hint": `changes-requested`, or `operator-thread`
   without `pending_reply`). Empty → one line saying nothing was asked for, and the Review card re-raises.
4. **A change that contradicts a locked plan decision** (`## Architecture decisions`, a `[user decision …]`)
   is the existing **Re-plan** re-route to the planner, quoting the decision.
5. **Otherwise** S5.1 runs at the phase-delta scope of the shipped phase — base = that phase's **entry SHA**
   (the `<entry-sha>` its phase link used), never `facts.tracker.last_shipped`, which is the shipped phase
   itself — with iteration 1's input the change set. Then S5.2 pushes once, the loop comment posts a new pass
   for phase `<N>`, S6 re-projects the phase's tracker row and DoD ticks onto the new head, and the threads get
   their replies.
6. **After the push**, the `end` reading decides: `under` with no crossing projection → `Checkpoint: phase
   <N> · review — changes required` and the Review card re-raises (trigger `changes required`, phase link from
   the shipped phase's entry SHA to the new head); `finish`, `wrap` or a crossing projection → `Checkpoint:
   phase <N> · end — context; review required: changes required`, no card, the review printed as on the
   no-usage path (the line, the summary with the phase link, the handoff). The push has happened, so this is a
   normal `context` end, never an interruption.

### 4.2 Context usage

The resolver reads its own context usage from the host's context-usage tool. If the host has one — on Claude
Desktop, `get_usage` with `session_id: "self"`, which returns `context.tokensUsed`, `contextWindow`,
`percentUsed` and `autoCompactsAtPercent` — read it at each **milestone**; otherwise take the **no-usage
path**. The coordinator never reads usage on the resolver's behalf.

**Milestones**, and the `--point` each passes: a pass's prep (`start`), after each S5.1 round (`round`), S5.2
after the push (`end`), and a Review card answer (`answer`). Each reading goes through the script, which keeps
this session's ledger (`<facts.scratch>/context-usage.jsonl`) and does the arithmetic; the session's **first**
reading passes `--new-session`, because the scratch dir outlives the session:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/phase_review.py context --scratch "<facts.scratch>" --phase <N> \
  --point <start|round|end|answer> --used <tokensUsed> --window <contextWindow> \
  --auto-compact-pct <autoCompactsAtPercent> [--new-session]
```

Omit `--auto-compact-pct` when the host reports no auto-compact point: the band is then `unknown`, which is
the no-usage path. Quote every reading in the next coordinator milestone update as `context: <share>% of
auto-compact`.

**Bands** — `share` is used tokens over the auto-compact point (`limit_tokens`), never the raw window:
`under` (below 65%), `finish` (from 65%: finish the current phase, then end), `wrap` (from 90%: wrap up now).
The thresholds are the script's `FINISH_SHARE` / `WRAP_SHARE`, the one source. `projected_share` adds the
largest growth a completed phase has shown this session; `projection_crosses_wrap` says the next phase would
likely reach 90%, and counts as `finish`.

What each reading decides:

- **`start`** — `under` with no crossing projection → build the phase. `finish`, `wrap` or a crossing
  projection → `end` (`context`) with **no record**: nothing of the phase is built, so there is nothing to
  resume, and the next session starts it from the tracker cursor (§4's `end` shape, without a phase line,
  since this pass shipped nothing). The changes-required round's `start` is the exception §4.1 step 2 names.
- **`round`** — `wrap` → the 90% wrap-up, below. Any other band continues the loop: past 65% the phase is
  finished, not abandoned.
- **`end`** — §2 (`context`). A `wrap` reading here is a `context` end, not a wrap-up: the phase is complete
  and pushed.
- **`answer`** — §4 step 3.
- **No-usage path** — no milestone ends a session on usage: phases run as §2 decides, and every trigger ends
  the session with its review printed.

**90% wrap-up** — on a `wrap` reading mid-phase, stop without completing the phase:

1. Commit every uncommitted edit locally — `wip: phase <N> interrupted at <share>%` — and never push.
2. Append to `<facts.scratch>/loop-comment.md` (open it with the marker and the `## Review loop — phase <N>`
   heading if no loop entry did) the line

   ```
   Interrupted: phase <N> @ <40-hex HEAD> · build — <where it stopped>
   ```

   or, when the reading lands during a changes-required round (§4.1), or at a changes-required answer
   (§4 step 3, the pending-round record),

   ```
   Interrupted: phase <N> @ <40-hex HEAD> · changes from <40-hex entry SHA> — <notes comment url, or `threads only`>
   ```

   (`<entry SHA>` the shipped phase's entry, §4.1 step 5's base), then close it with
   `Review runs: phase <N> · <count> · interrupted` (`0` for a pending round). No `Checkpoint:` line.
3. Post it, led by `<!-- review-loop:v1 -->`, on the PR when one exists, else on the issue (a fresh first
   phase opens its PR only at S5.2):

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> <pr|issue> <N> "<facts.scratch>/loop-comment.md"
   ```

4. Print `## Session summary` — "Why": context at `<share>%`, phase `<N>` interrupted at `<where it
   stopped>` — and the resolver re-entry handoff ([`handoff-renderings.md`](handoff-renderings.md), the
   wrap-up shape). No S6 tick, no S7, no thread replies.

The next session's prep reports the record as `facts.interrupted`, and its first pass resumes that phase
(§4.3). The record format is
[`../../_shared/review-loop-record.md`](../../_shared/review-loop-record.md)'s "Interrupted passes".

### 4.3 Resuming an interrupted phase

A session whose prep reports `facts.interrupted.active` resumes that phase first (spine S4: it overrides the
tracker cursor). Read the fact from the pass's **full** prep: a `--refresh` prep has no workspace, so it cannot
retire a record by its closed PR or report `head_contains`.

- `kind: build`, `head_contains` → resume the **build** at S5: implement the phase's remaining `ships` from the
  record's `note` and the local commits, then S5.1 as usual. Seed the loop from the record's own comment
  ([`review-fix-round.md`](review-fix-round.md) "Resume hint").
- `kind: build`, not `head_contains` (the local commits are gone) → rebuild the phase from
  `facts.workspace.upstream_sha`, and say so in one line.
- `kind: changes` → the changes-required round (§4.1 steps 2–6): its notes from the comment the record's `note`
  links (or `threads only`), `facts.interrupted.entry` as step 5's base and step 6's phase-link entry. With
  `head_contains`, step 5 continues from the local wip commits; otherwise it starts from
  `facts.workspace.upstream_sha`, said in one line — the same rule as `build`.

A record stops being active once a later trusted pass of its phase settles (any exit but `interrupted`), or —
a `build` record only — once a closed PR this account opened from the work branch has a head past its SHA:
only a completed pass opens a PR, so that PR records the work done.

### The `## Session summary` block

The operator reads it cold, so it stands alone: plain words, no pipeline vocabulary left undecoded, each
section a few bullets. It sits outside `## Handoff`, never a segment of it
([`../../_shared/handoff-format.md`](../../_shared/handoff-format.md)).

- **Why the session ended** — the trigger in plain words: the plan asked for a review after this phase
  (`checkpoint: pause`, or none set), the operator asked to stop after each phase (`--pause`), the
  warning sign — which card rendered and why, which override or failure record was written, or the
  thread comment that refines the plan (quoted) — or the shipped diff raised its review level (which
  path, which dimension and site the judgment cited, or that the judgment's verdict could not be read, which
  counts as a raise). Before a Review card it says a human review is required before the next phase, and
  that the session goes on only on **Reviewed, continue** while its context usage leaves room. A context end
  says the session's context reached `<share>%` of the auto-compact point (or that the next phase would
  likely cross 90%); a 90% wrap-up says where the phase stopped.
- **What changed** — per phase this session shipped: what it does now that it didn't before, at the
  operator's altitude (behaviour, not file lists), its phase link, and the DoD bullets it ticked.
- **Decisions and deviations** — every answer the operator gave on a card this session, every
  announced choice (`Did X because Y`), every override or failure record written to the PR body, and
  every place the build departs from the plan (a settled item fixed here, a refinement built on, a
  `## Changes` entry built differently) — each with its reason. `None — built as planned.` when empty,
  never omitted: a missing section reads as one left out.
- **What to check** — before the next session: the specific places a reviewer should look (the
  riskiest change, the code a warning sign touched, a deviation's effect), each with its file and the
  phase link, and what the next phase will build on top of them. End with how to steer it: a comment
  on the issue before the next session starts reaches it — that session reads the thread from GitHub,
  and nothing else carries a note across.

## 5. Continue

1. Delete this phase's staging files from `facts.scratch` — `pr.md`, `pr.posted.md`, `shots/`,
   `issue-body-projected.md`, `review-diff.patch`, `cold-read-diff.patch`, any `slice-<M>-ticked.md` — but
   never `context-usage.jsonl`: it holds every completed phase's growth, which the projection reads (§4.2). A surviving fresh-mode
   `pr.md` restaged by a later `edit-pr-body` would overwrite the tracker tick S6 just wrote. The next
   pass's first PR-body write restages from `facts.tracker.body_path` — the PR body the new prep
   fetched, which at that point *is* the body this session last wrote — and S6's "restage from the body
   this run last wrote" rule governs every write after that pass's own S5.2.
2. Re-run prep **in full** — `prep_resolver.py <issue> <owner/repo>`, **no `--refresh`**. `--refresh`
   skips the workspace assertion, so it returns no `facts.workspace` and no reachable
   `facts.tracker.last_shipped` (the next phase's review base). The setup hooks re-run, deliberately: a
   phase can change what setup installs (a new dependency), and a new session would re-run them too, so
   skipping them would break the rule above. The new facts block replaces the old one wholesale — it now
   reads `vector.mode: continue` on this session's own PR — with one value carried over: the **first**
   prep's `facts.workspace.upstream_sha`, the session-entry anchor of the handoff's `Changes:` line. Note
   it before the first re-run; every later prep reports the next phase's start instead. Then take the
   `start` usage reading for the next phase (§4.2), which may end the session before anything is built.
3. Re-enter the spine at **S1** in continue mode (S2 skips there, as it always does) and skip S1.5 —
   the printed line already named the phase. Every per-run state in the spine and its references starts
   over, seeded only from GitHub as a new session's would be: S5.1's loop-entry SHA, the addressed- and
   refuted-items lists, defect counts, the grace round and the ceiling count, the record's run count and
   id counters (`review-fix-round.md`), the cold read, and any guard rail's settled answer.
4. **Warning sign before building.** The thread may have moved during the phase or at the card. Compare
   this pass's `facts.sections.thread_comment_count` with the previous prep's: when it rose **and** this
   pass's distiller reports `thread-vs-plan: refines` citing one of those new comments, print the
   refinement and end the session before S3 (§4's `end` shape: no phase line — this pass shipped nothing —
   then the `## Session summary`, its "Why" quoting the new comment, and the handoff). A refinement the thread
   already carried when the session started never pauses a later pass: the session's first pass built
   on it, and `refines` is a whole-thread judgment, so it would otherwise fire on every pass. Ignore every
   comment whose URL is in the session's **own-comments list** (§4.1's changes-required notes, an
   issue-posted interrupted record): the notes are the operator's, but this session already acted on them,
   so they cannot be news.

## 6. Ending the session

A session that ends at a checkpoint (`end`, a `context` end, a review not continued past, or a hard stop) →
the routed playbook's handoff, composed per
[`handoff-renderings.md`](handoff-renderings.md): it owns the session-wide `Changes:` line (anchored on
the value step 2 carried, with per-phase ranges when the session shipped more than one phase) and the
trailing `--pause` on resolver re-entry commands. A 90% wrap-up (§4.2) ends with the re-entry handoff
too, its `Why:` naming the interrupted phase and where it stopped.
