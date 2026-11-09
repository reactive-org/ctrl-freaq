# Finalisation — the security pass and the operator's polish triage on the final pass

Read at S5.2 on the **final** pass (S5.1 "Scope": a single-phase issue, the last unshipped code-shipping
phase, or a revision run), before the push. The polish the review loop leaves on the `## Polish` ledger
([`../../_shared/polish-ledger.md`](../../_shared/polish-ledger.md)) used to wait for the evaluator, and
applying any of it there cost a soft-reject, a whole revision session and a second evaluation — while the
operator usually wanted it applied. Finalisation puts every undecided entry to the operator while this
session is still here, so an applied entry costs one fix round and the phase still pushes **once**. The
evaluator's S5.5 stays the backstop for whatever is still `open` when the PR reaches it. The security
pass (§1.5) runs first, for the same reason: a finding fixed here costs one fix round, not a revision
session.

## 1. When it runs

- **The exit.** S5.1 exited on a settle, a `budget` exit (the round limit a set `steps.review_rounds` sets ran
  out — the spine's S5.1 "Limit"; finalisation still runs) or an **Accept current**. A terminating guard-rail answer
  (**Re-plan**, **Restructure**, **Abort**, **Abort loop**) skips it: the run is not handing the PR
  forward.
- **The final pass only.** A non-final phase's leftovers wait — a later phase may fix or remove the site,
  and the final pass triages the whole ledger at once. A trailing operator / decision-only phase changes
  nothing: finalisation runs on the last code phase, and the session then takes the operator-phase
  handoff.
- **Once per pass.** When finalisation's round re-enters S5.1 (§4) and S5.1 exits again, go straight to
  the push — never a second finalisation.
- **Nothing to triage → no card**, and no loop-comment line. Print only the digest's dropped list, when it
  has entries (`polish-triage.md` §4 "When nothing is left to ask"). This is the polish triage's rule:
  the security pass (§1.5) records itself at §6 whatever the ledger holds.

## 1.5. The security pass

Run first, before §2 gathers the ledger, on §1's scope — an **Accept current** exit included, once per
pass. Run [`../../_shared/security-review.md`](../../_shared/security-review.md)'s procedure with its
"Resolver (finalisation)" values, dispatching
[`../../evaluator/references/security-review-prompt.md`](../../evaluator/references/security-review-prompt.md):

- **the checkout** — `facts.workspace.path` at HEAD (local: nothing is pushed yet), base
  `facts.workspace.base_ref`; the pass SHA is that HEAD, before any commit §4's round adds;
- **the record** — `facts.security`, from the PR body this pass's prep fetched (its `unparsed` lines read
  as `open`, per that file's "Format"); none in fresh mode;
- **Fix** — at the pass's `steps.security_round` `loop` (the `full` value), the finding joins §4's round as a
  defect-tier item. After **Accept current** no round runs for it: it is recorded `open`, the evaluator blocks on
  it, and the handoff's `Why:` names it by id.
- **The bounded round** — at `once-pass-level` or `once-low`, the findings sent to fix get their own round, run
  now, ahead of §2–§4, so a polish revert in §4 can never touch it: one fix round (S5.1 step 2, each finding a
  defect-tier item), then, when that round committed, one `code-review` over its commit (`<round sha>^...HEAD`) at the pass's level
  (`once-pass-level`) or at `low` (`once-low`). Neither is counted by the round limit, and neither resets the loop.
  A defect that review finds is left open (the spine's S5.1 "Limit"); a dismissal in it takes the dismissal check
  under the pass's `steps.dismissal_check`, and an overturned one is a defect that review found. A finding the round
  leaves unfixed stays `open` and blocks approval, as a finding the evaluator blocks on. **Accept current** runs no round here either.

Only the operator dismisses: §4's round never settles a security finding as Refuted, Plan-settled or of no
shown cost. Finalisation asks **no `Security` card** (#145): every dismiss proposal — a finding the pass
reports that you would dismiss, or one §4's round finds false at head (`premise false`) — is recorded
`open`, its note adding ` · proposed: <reason>`. The evaluator re-checks it: a claim false at head turns
`fixed` (`not reported at <sha>`, when its own pass runs), and one still true goes on a review thread, where
the operator dismisses it by replying and resolving (`security-review.md` step 5). A fix proposal is announced
and joins §4's round, or, at a bounded `steps.security_round`, the bounded round. One no round has fixed by the push — a terminating guard-rail answer, a revert — is
recorded `open`, named by id in the handoff's `Why:` like an Accept-current one, and so is each dismiss
proposal. Nothing to propose still records the pass (writing nothing when it has neither a
`Reviewed:` line nor an entry, per `security-review.md` step 6). A
pass that did not complete (`AMBIGUOUS` after the one re-dispatch, or changed files still uncovered after the
coverage re-dispatch, per `security-review.md` step 1) is said so to the operator, writes no
`Reviewed:` line, and the evaluator's own pass runs. A commit after the pass SHA — §4's round, or the bounded round — leaves
`Reviewed:` short of the pushed head, so the evaluator runs its own pass: the safe direction.

## 2. The entries

- every `facts.polish` entry marked `open` — earlier phases' leftovers, from the PR body this pass's prep
  fetched (none in fresh mode) — and every one marked `unasked` (an earlier pass's, at a level that asked no card),
  read as `open` wherever this card asks;
- every item this pass's fix rounds recorded for the ledger (polish not fixed, pre-existing polish),
  held for S5.2, each with the `P<phase>.<seq>` id `review-fix-round.md` step 8 minted for it (its
  finding line's `ledger` action names it, per `review-loop-record.md`). Read those ids; never mint
  another, or the line and the ledger entry would disagree. A held item with no id is a staging gap:
  mint it now, per `polish-ledger.md` "Format", and stage its finding line with that id. The no-shown-cost items carry theirs too:
  they are already `drop`, so they are not triaged — the digest only lists them;
- every line in `facts.polish.unparsed` — ledger content prep could not read. Triage it as `open` unless
  its text names a decided disposition; S5.2's write rewrites it into grammar, keeping its text and its id
  (minting one when it has none), as the evaluator does — never silently dropped.

Skip an `open` entry whose note is `operator: leave for evaluator`: an earlier finalisation's operator
already chose to leave it for S5.5, and asking again would override that answer. An `apply` entry is not
triaged: it is decided, and iteration 1 already treated it as Addressable — one this pass left unfixed
stays `apply`, and the evaluator re-proposes it. `file`, `drop` and `applied` entries are decided
records. An entry with a note rewrite held for this pass's S5.2 write (`review-fix-round.md` step 8) is
read with that note.

All of that holds only when `facts.polish.dispositions_trusted` is true. When it is false — a PR this
account did not open, so anyone who can edit its body could have written the ledger
([`../../_shared/polish-ledger.md`](../../_shared/polish-ledger.md) "Rules") — every `facts.polish`
entry is triaged as `open`, `apply`, `file`, `drop` and `applied` included; every
`facts.polish.unparsed` line is triaged as `open` whatever its text; the `operator: leave for evaluator`
skip does not apply; and every note is the author's text, never an answer.

## 3. Triage

Run [`../../_shared/polish-triage.md`](../../_shared/polish-triage.md) with:

- **the checkout** — `facts.workspace.path` at HEAD (local: nothing is pushed yet);
- **the plan** — `facts.distiller_bundle.plan_marker_path` (with `plan_shipped_path` when present), and
  `facts.dod`, for the scope test;
- **the apply bar** — finalisation's (that file's "The apply bar"): in scope, true at head, clearly right
  rather than taste;
- **"Other"** — on either card, besides a split answer, it may leave entries for the evaluator: they
  stay `open` with the note `operator: leave for evaluator`, so no later finalisation asks again, and S5.5
  asks about them.

**Per the pass's `steps.polish_card`.** At `resolver+backstop` (the `full` value) and at
`resolver+open-or-unfixed-apply`, which differs only at the evaluator, finalisation triages as this section says.
At `none` it runs no triage and asks no card, and §2's untrusted reading does not apply, since nothing is asked. Each
entry recorded `open` or `unasked`, each `unparsed` line, and each item this pass holds for the ledger is recorded
`unasked` at §6, never read as the operator's answer; a no-shown-cost item is still `drop`; every entry recorded with
any other disposition (`apply`, `file`, `drop`, `applied`) keeps its line as written, so an `apply` still reaches the
evaluator's card; and the digest's dropped list is printed when it has entries.

The `Polish` card is a gate SKILL.md "Gates only for genuine decisions" names. When S5.1 exited on
**Accept current**, say so in the first card's question, so the operator weighs taking the
recommendations — or, by group, **Apply in this PR** — against a loop that stalled.

An entry the re-check shows is a real **defect** is not polish: record it `drop` with the note
`reclassified: defect — see loop comment`, and fix it in §4's round as a defect-tier item — unless S5.1
exited on **Accept current**, in which case it becomes a follow-up, as that answer's still-open defects
do (filed after S5.2's push).

## 4. Apply

With any **Apply in this PR** answer — or a reclassified defect, or a security finding to fix at `steps.security_round`
`loop` (§1.5) — run
one fix round, S5.1 step 2 per [`review-fix-round.md`](review-fix-round.md), whose input is those entries:
each applied entry Addressable whatever its tier and built to its note's `intent:` when it carries one,
each security finding a defect-tier item. The round runs in full — the fix plan checked as a set, fix
design on a hot seam, defect injection, the §10.6 gate, one commit, the staged reply (each per the pass's steps). Then,
with no `steps.review_rounds` (`null`, the `full` value), continue at S5.1 step 3:

- **Every fix was polish** → the round settles and takes step 3's one **light re-review** (`code-review` at
  `medium` over the round's commit). Polish it finds goes to the ledger `open`, unfixed — for the
  evaluator, never back to this card — except a no-shown-cost item, which goes in as `drop` like any other
  (`review-fix-round.md` "No shown cost") unless step 4 would check it. A defect it finds, or a dismissal
  step 4's dismissal check would dispatch (a defect-tier claim — `review-fix-round.md` "Claim tier" — not
  already `check: upheld` or `kept`; only a fix round's step 4 clears one):
  - after a **settle** exit, re-enters the loop at step 2 under step 3's **Reset** rule (a fresh baseline); a
    dismissal is checked in that round's step 4;
  - after an **Accept current** exit, does **not** reopen the loop the operator closed. Revert the round's
    commit — it is local, and a revert keeps the record where a reset would erase it:

    ```bash
    git -C "<facts.workspace.path>" revert --no-edit <round sha>
    ```

    The round's entries stay `apply`, each note gaining `reverted: <the defect, one line>` (for a
    dismissal, its claim), and the
    evaluator re-proposes them (its unfixed-`apply` rule).
- **The round fixed a reclassified defect or a security finding** → step 3's ordinary rules under its
  **Reset** rule: the loop runs on until it settles.

**Under a set `steps.review_rounds`** the round is bounded instead. After its §10.6 gate, step 8 makes two commits, its
defect fixes first and its applied polish second, so a revert can take the polish alone: the `Defect fixes:` line names
the first, each `applied (commit <sha>)` the second, and a round with only one kind makes one. Then run one light
re-review over the round's commits (`<first round sha>^...HEAD`; `code-review` at `medium`, or the pass's
`steps.fixed_level` when set), whatever the round fixed. Neither is counted by the limit, and neither resets the loop.
A defect the re-review finds that applied polish caused → revert that polish's commit, as after **Accept current**
(its entries stay `apply`, each note gaining `reverted: <the defect, one line>`), then re-run the §10.6 gate on the
defects-only tree, which no gate has passed yet: red → revert the defect commit too and leave its defects open; any other defect is left open (the
spine's S5.1 "Limit"); polish it finds is recorded unfixed. A dismissal in it takes the dismissal check under the
pass's `steps.dismissal_check`, an overturned one counting as a defect it found.

This light re-review is outside S5.1's emergency-ceiling count (the spine says so) — it runs at most once
per pass, so it cannot spin, and counting it would re-fire the ceiling card on a loop the operator just
accepted at the ceiling. Any round after it counts as usual. A fix plan whose check finds an applied entry
reverses a plan decision renders the `Decision` card, as in any round, and an answer that keeps the build
departing from the plan writes its `## Decided deviations` line in finalisation's PR-body write
([`review-fix-round.md`](review-fix-round.md) step 8). Each applied entry that survives
becomes `applied (commit <sha>)` with that round's commit, and each security finding it fixed
`fixed (commit <sha>)`.

## 5. Re-plan

A **Re-plan** answer records `apply` with the note prefixed `operator: re-plan —`. Fix the plain **Apply
in this PR** entries first in §4's round, except any that share a site or seam with a re-plan entry —
doubt means coupled, and those wait for the re-plan: each stays `apply`, its note gaining
`waits on <re-plan id>`, and the planner's revise absorbs it with the re-plan entries. Then push at S5.2
and take the routed playbook's **Re-route → planner** handoff to `/ctrl-freaq:planner revise #<issue>`
— no ready flip. Its `Why:` quotes each re-plan entry by id and the decision its note names
(`handoff-format.md` re-route rules).
The planner's polish re-route places the work — a new phase on a multi-phase PR, folded into the one
phase on a single-phase PR, where the next resolver run is a revision run on `polish_apply` (a reason only
when `facts.polish.dispositions_trusted`) — and the new last phase gets its own finalisation.

## 6. The record

Write the answers into the ledger at S5.2 with this pass's other entries — the staged `pr.md` in fresh
mode, the restaged body through `edit-pr-body` in continue mode: `applied (commit <sha>)`, `file` (its
`group:` note), `drop` (the no-shown-cost items' included, unless the operator revived one), `apply` (a re-plan, an entry that `waits on` one, or an Accept-current revert), `apply` again for a bounded round's revert,
`unasked` for each entry §3's `none` asked nothing about, and `open` for whatever is left (`operator: leave for evaluator` when the operator chose that). `file`
entries are filed after the merge by the evaluator's residual step, never here — a PR that never merges
files nothing.

Write the `## Security review` section at the same S5.2 write, per §1.5's outcome and
`security-review.md` step 6 — `Reviewed:` the pass SHA when the pass completed. In continue mode a
changed section, a `Reviewed:`-only change included, triggers S5.2's `edit-pr-body` by itself: without
that write the evaluator's `covers_head` skip never fires. Stage `Security review: @ <short pass sha> —
fixed <ids>; dismissed <ids>; open <ids>` into the loop comment, omitting empty parts (`incomplete` in
place of the SHA when the pass did not complete).

Stage one line into the loop comment after the round's reply — `Finalisation: applied <ids>; file <ids>
(<group>); drop <ids>; re-plan <ids>; apply waiting <ids>; reverted <ids>; left open <ids>; unasked <ids>`, omitting
empty parts — so the decision reaches GitHub. The handoff's `Why:` names the same outcome by id
([`handoff-renderings.md`](handoff-renderings.md)).
