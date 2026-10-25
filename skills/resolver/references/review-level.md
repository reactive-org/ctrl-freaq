# Review level — the `<level>` one loop pass reviews at

The spine's S5.1 "Invocation" passes `code-review` a `<level>`. This reference is where that level comes from,
how it is recorded, and the PR-body risk statement written from the same evidence. The level measures what one
missed bug costs: the change's **blast radius**, meaning the later phases or stories that build on it, the contracts
and seams it touches, and whether it touches security, data or money. A line or file count measures volume, not
cost, so it sets nothing.

## §1 The rule

- **The plan declares it, per phase.** The plan's `## Review level` section carries one
  `- phase <N> · <medium|high|xhigh> — <why>` line per phase (`skills/planner/references/plan-schema.md`
  "The `## Review level` section"). The planner judges the blast radius before the diff exists. It defaults to
  `medium` and writes `high` or `xhigh` only where its analysis calls for them.
- **No valid line means `high`.** That covers no section at all, and a line that is absent, does not parse
  (`low` and `max` included) or names its phase twice. The stricter value is the safe one, like an absent
  `checkpoint:` meaning `pause`.
- **The final cumulative pass starts from the highest level any phase declared** (an undeclared phase counts
  as `high`). Its range spans every phase, so its level does too.
- **The diff can raise it one step, capped at `xhigh`.** `medium` goes to `high`, and `high` goes to `xhigh`.
  Three signals raise it:
  - the range touches a path the consuming repo declares sensitive (`<!-- pr-evaluator-sensitive-paths -->`,
    read by the same `sensitive_paths.assess` the evaluator's merge gate calls, so the two cannot disagree);
  - that read fails (an unreadable declaration line included), which is the stricter outcome, as at the merge gate;
  - the range changes a file that no backticked span in the plan's `## Changes (file-level)` or `## Test plan`
    covers (a `` `dir/` `` span covers everything under it).

  The level is never raised to `max` automatically: `max` stays the operator's own `code-review` choice.
- **One level per pass.** The signals are read once, at loop entry, so the `<level>` and `<context>` assembled
  there stay fixed for the whole loop. Fix rounds' changes reach the next pass's entry check, never this pass's
  level.
- **Light re-reviews stay `medium`.** That means the one after a polish-only round (`resolve-spine.md` S5.1 step 3)
  and finalisation's (`finalisation.md`). Each re-reads one round's own commits for what that round's fixes
  broke, which is narrow by design, and the pass's level already governed the read that found the round's items.
- **A level is not a uniform depth.** `code-review` picks its recipe per model family × level, so the rule fixes
  the level, not the depth.

## §2 The call

The level is a script fact. Run this at S5.1 loop entry, after recording the loop-entry SHA, with the phase's work
committed (the script reads the committed range):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/blast_radius.py assess "<facts.workspace.path>" \
  --plan "<facts.distiller_bundle.plan_marker_path>" --phase <N> <range>
```

`<range>` is S5.1 "Scope"'s base, as a flag:

- **final** pass (and a revision run): `--final --base-ref <facts.workspace.base_ref>`. This is the merge gate's
  own call on the same cumulative range.
- **non-final** pass: `--since <sha>`, where `<sha>` is the Scope rule's base (`facts.tracker.last_shipped.commit_sha`).
  When that base is the branch name (phase 1, or an unreachable `last_shipped`), pass it as
  `--base-ref <facts.workspace.base_ref>` instead.

The envelope is always `ok`. Its `level` is the `<level>` token. Its `why` names the declared level and its
source, then each raising signal. `declared_level`, `sensitive`, `out_of_plan` and `raised` are the evidence for the
record and for `## Risk`. Each notice names a `## Review level` line the script could not use. Print the `why`
before the first `code-review` run.

## §3 The record

Stage the chosen level and why into the loop comment, as its own line directly under the pass's
`## Review loop — phase <N>` heading:

```
Review level: phase <N> · <level> — <why>
```

Write `<why>` from the envelope's `why`. Keep its facts (the declared level and source, each raising signal with its
first path), and add a clause of your own only when it says what the signal means for this change. Whichever write
opens `<facts.scratch>/loop-comment.md` writes the line: `review-fix-round.md` step 8's first round, or S5.2 when no
round staged anything. The record's shape is owned by
[`../../_shared/review-loop-record.md`](../../_shared/review-loop-record.md), whose reader skips this line.

## §4 `## Risk` and `## How to revert` in the PR body

Two resolver-written PR-body sections, for the reviewer and the operator to read before approving. Nothing parses
them.

- **`## Risk`**: what could break, and for whom, at this change's blast radius. Cover the seams and later work
  that build on it, every sensitive or out-of-plan path the §2 call reported, and the review level with its why.
- **`## How to revert`**: the revert of the merge, plus every step a revert alone does not undo, such as a migration
  or data change, a config or flag, or a published artifact. When there is none, say so.

Fresh mode writes both into the staged `pr.md` (`resolve-spine.md` S5). Each later phase rewrites both at S5.2,
with the PR-body write it already makes, so that they describe the whole PR as it stands, not only the latest phase.
The epic integration PR carries neither: its body is `epic.md`'s own, each story's PR carries its own risk and revert,
and the integration PR's revert is its merge.
