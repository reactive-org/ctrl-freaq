# Phase continuation — the checkpoint after a non-final phase ships

Read at the spine's "Return to the routed playbook" when a phase has shipped and an unshipped phase
remains (S5.2 pushed it, S6 projected it, S7 filed its follow-ups). It decides whether this session
ships the next phase too or ends with a session summary and the non-final handoff. This **phase checkpoint** is not
[`follow-up-tracking.md`](follow-up-tracking.md)'s end-of-loop checkpoint: that `file-at-checkpoint`
batch ran at S7, minutes earlier, and is never a reason to pause.

**The rule this file serves:** a continuation is a new **pass** through the spine — a fresh
continue-mode run in everything but the conversation. Each phase gets its own prep, push, tracker tick,
DoD projection, cold read, and loop comment, seeded from GitHub exactly as a new session's would be — so
the evaluator, the tracker, and a later session cannot tell which phases shared a session. Wherever the
spine or its references scope a rule to "this run" or "one run", it means this pass. The `## Handoff`
ends the **session** (SKILL.md §4), never a pass: a pass that continues emits none.

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

## 2. Pause or continue

Pause when **any** of these holds; otherwise continue:

- the shipped phase's `checkpoint` (`facts.phases`) is not `continue` — `pause`, or `null` (absent: a
  plan authored before the key, or a phase the planner left unmarked);
- the session holds `--pause` (SKILL.md §1);
- a **warning sign** fired during this phase's pass:
  - a gate card rendered — `Audit`, `Doc conflict`, `Tracker`, `Tests red`, `Review loop`,
    `Settled item`, `Decision`, `Loop stall` (the emergency ceiling included) — whatever the continuing
    answer was;
  - this pass wrote an override or failure record to the PR body — `## Audit override`,
    `## Plan override`, `## Known failures`, an `Accept current` override, a `## Tracker reconciliation`
    note.

Warning signs only turn `continue` into a pause. Nothing turns a `pause` into `continue`: the planner's
key and the operator's flag are the floor, and "this phase was routine" is a judgment about your own
work. Not warning signs: the S7 follow-up confirmation, settled findings (Plan-settled,
Deferred-by-plan, Refuted), and cold-read findings the loop fixed.

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
7-char.

## 4. A pause ends the session

A pause asks nothing: it ends the session. The operator often comes back hours later, after the prompt
cache has expired, and a card answered then reloads the whole conversation only to discard it — so the
session ends at the pause and the next one starts cold on purpose. Print one line first, so the link is
clickable in the transcript:

> **Phase <N> — <title> shipped** (<K> of <M>) · <phase link> · paused: <`checkpoint: pause` | `--pause` | the warning sign>

Then the `## Session summary` block ("The `## Session summary` block"), then the non-final handoff (§6). Nothing follows the handoff.

No pause → print the same line ending `· continuing to Phase <N+1> — <title> (checkpoint: continue)`,
and continue.

### The `## Session summary` block

The operator reads it cold, so it stands alone: plain words, no pipeline vocabulary left undecoded, each
section a few bullets. It sits outside `## Handoff`, never a segment of it
([`../../_shared/handoff-format.md`](../../_shared/handoff-format.md)).

- **Why the session ended** — the trigger in plain words: the plan asked for a review after this phase
  (`checkpoint: pause`, or none set), the operator asked to stop after each phase (`--pause`), or the
  warning sign — which card rendered and why, which override or failure record was written, or the
  thread comment that refines the plan (quoted).
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
   `issue-body-projected.md`, `review-diff.patch`, `cold-read-diff.patch`, any `slice-<M>-ticked.md`. A surviving fresh-mode
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
   it before the first re-run; every later prep reports the next phase's start instead.
3. Re-enter the spine at **S1** in continue mode (S2 skips there, as it always does) and skip S1.5 —
   the printed line already named the phase. Every per-run state in the spine and its references starts
   over, seeded only from GitHub as a new session's would be: S5.1's loop-entry SHA, the addressed- and
   refuted-items lists, defect counts, the grace round and the ceiling count, the record's run count and
   id counters (`review-fix-round.md`), the cold read, and any guard rail's settled answer.
4. **Warning sign before building.** The thread may have moved during the phase or at the card. Compare
   this pass's `facts.sections.thread_comment_count` with the previous prep's: when it rose **and** this
   pass's distiller reports `thread-vs-plan: refines` citing one of those new comments, print the
   refinement and end the session before S3 (§4: no phase line — this pass shipped nothing — then the
   `## Session summary`, its "Why" quoting the new comment, and the handoff). A refinement the thread
   already carried when the session started never pauses a later pass: the session's first pass built
   on it, and `refines` is a whole-thread judgment, so it would otherwise fire on every pass.

## 6. Ending the session

A pause (or a hard stop) → the routed playbook's handoff, composed per
[`handoff-renderings.md`](handoff-renderings.md): it owns the session-wide `Changes:` line (anchored on
the value step 2 carried, with per-phase ranges when the session shipped more than one phase) and the
trailing `--pause` on resolver re-entry commands.
