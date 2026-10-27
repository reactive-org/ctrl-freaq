# Review level — the `<level>` one loop pass reviews at

The spine's S5.1 "Invocation" passes `code-review` a `<level>`. This reference is where that level comes from,
how it is recorded, the PR-body risk statement written from the same evidence, (§5) the same signals read
again over the shipped diff for the pause decision, and (§6) the judgment of what the diff actually reaches. The level measures what one
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
  Four signals raise it, one step in total however many fire:
  - the range touches a path the consuming repo declares sensitive (`<!-- pr-evaluator-sensitive-paths -->`,
    read by the same `sensitive_paths.assess` the evaluator's merge gate calls, so the two cannot disagree);
  - that read fails (an unreadable declaration line included), which is the stricter outcome, as at the merge gate;
  - the range changes a file that no backticked span in the plan's `## Changes (file-level)` or `## Test plan`
    covers (a `` `dir/` `` span covers everything under it, a `` `path:NN` `` citation names its file, and a span
    containing `*`, `?` or `[` names nothing, so a glob cannot cover the files it would match). On the
    epic-integration pass a file a PR merged into the epic branch changed counts as covered too
    (`facts.epic.delivered`, #149), because the epic plan is written at coordination altitude and names no
    story's files. A file in that range no merged PR changed — a commit made directly on the epic branch — still
    raises. Coverage is by file, not hunk, so a sync merge's conflict resolution or a direct edit inside a
    delivered file is covered: only the sensitive-path signals and the judgment can raise it. Every other
    route passes no delivered set, so this signal is unchanged there;
  - the blast-radius judgment returns `raise`: the diff reaches a dependent, contract, seam, security, data or money
    site the plan's level reason did not account for (§6), on a planned run only.

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
committed (the script reads the committed range).

On a planned run, first get the judgment (§6). Stage `git diff <base>...HEAD` in the workspace to
`<facts.scratch>/blast-radius-diff.patch`, where `<base>` is S5.1 "Scope"'s base, the same range `code-review` reads:
the `--since` SHA, or `origin/<facts.workspace.base_ref>` on a `--base-ref` pass. Dispatch the `Explore` sub-agent per
[`blast-radius-judgment-prompt.md`](blast-radius-judgment-prompt.md), filling `<<workspace_path>>` with
`facts.workspace.path`, `<<grounding_path>>` with `facts.grounding_path`, `<<diff_path>>` with the staged diff,
`<<base_ref>>` with that `<base>`, `<<plan_path>>` with `facts.distiller_bundle.plan_marker_path`,
`<<plan_shipped_path>>` with `plan_shipped_path` or `(none)`, `<<phase>>` and `<<final>>`, and `<<catalogue_entries>>`
with `facts.grounding_docs`, one `path — role — authority — summary` line each, a `kind: dir` entry's `members` listed
under it and a `present: false` entry omitted. Write its returned verdict verbatim to
`<facts.scratch>/blast-radius-judgment.md`. When the verdict is one the script cannot read — no `## Judgment` section,
no valid `verdict: raise|hold` line or two that disagree, or a `code: AMBIGUOUS` line — repair what it names and re-dispatch
once; pass the second answer whatever it says (the script counts an unread verdict as a raise). When the staged
diff is **empty** there is nothing to judge: dispatch none and omit `--judgment`, since an empty range cannot be
repaired and its `code: AMBIGUOUS` would count as a raise. The diff is staged without a fetch, as the `code-review` range is; on a `--base-ref` pass
`assess` fetches first, so its fact signals may read a newer base tip than the judged diff. That lag is accepted: the
judgment only adds to the script's signals, which read the fetched range. Then run:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/blast_radius.py assess "<facts.workspace.path>" \
  --plan "<facts.distiller_bundle.plan_marker_path>" [--plan "<facts.distiller_bundle.plan_shipped_path>"] \
  --judgment "<facts.scratch>/blast-radius-judgment.md" [--delivered "<facts.epic.delivered.path>"] \
  --phase <N> <range>
```

Add the second `--plan` when `plan_shipped_path` is set: a legacy plan's shipped phases keep their `## Changes`
entries there, and without them the earlier phases' files read as out of plan. Add `--delivered "<facts.epic.delivered.path>"`
when `facts.epic.delivered` is set: only the epic route's prep sets it, so standard and story runs never pass it,
and a null fact (no merged PR listing, `EPIC_DELIVERED_UNAVAILABLE`) passes nothing, so every unnamed file raises. On a run with **no plan**
(`plan_marker_path` is null: a trivial fix, or the operator's `proceed without a plan`), omit `--plan` entirely: the
level is `high`, and only a sensitive path can raise it, since there is no plan to stray outside. Dispatch no
judgment and omit `--judgment` too (§6).

`<range>` is S5.1 "Scope"'s base, as a flag:

- **final** pass (and a revision run): `--final --base-ref <facts.workspace.base_ref>`. This is the merge gate's
  own call on the same cumulative range.
- **non-final** pass: `--since <sha>`, where `<sha>` is the Scope rule's base (`facts.tracker.last_shipped.commit_sha`).
  When that base is the branch name (phase 1, or an unreachable `last_shipped`), pass it as
  `--base-ref <facts.workspace.base_ref>` instead.

The envelope is always `ok`. Its `level` is the `<level>` token. Its `why` names the declared level and its
source, then each raising signal. `declared_level`, `sensitive`, `out_of_plan`, `delivered` (`null` without `--delivered`, else
the set's `count` and the changed paths only it `covered`), `judgment` (the parsed verdict, its
`signals` each with a dimension, site and evidence sentence) and `raised` are the evidence for the record and for
`## Risk`; the `out_of_plan` paths are also the source of `## Out-of-plan changes` (§4). Each notice names a `## Review level` line the script could not use. Print the `why`
before the first `code-review` run.

## §3 The record

Stage the chosen level and why into the loop comment, as its own line directly under the pass's
`## Review loop — phase <N>` heading:

```
Review level: phase <N> · <level> — <why>
```

Write `<why>` from the envelope's `why`. Keep its facts (the declared level and source, each raising signal with its
first path), and add a clause of your own only when it says what the signal means for this change. Stage it right after the
§2 call, at loop entry. When `<facts.scratch>/loop-comment.md` does not exist, open it with the marker, the phase
heading and this line. When it survives from an interrupted run (spine S5 "Interrupted prior run"), replace its
`Review level:` line with this pass's, or insert one under the phase heading if it has none. So the line always records
this pass's loop-entry call. The record's shape is owned by
[`../../_shared/review-loop-record.md`](../../_shared/review-loop-record.md), whose reader skips this line.

## §4 `## Risk` and `## How to revert` in the PR body

Two resolver-written PR-body sections, for the reviewer and the operator to read before approving. Nothing parses
them.

- **`## Risk`**: what could break, and for whom, at this change's blast radius. Cover the seams and later work
  that build on it, every sensitive or out-of-plan path the §2 call reported, each dimension and site the judgment
  cited when it raised (with its evidence, in your words), and the review level with its why.
- **`## How to revert`**: the revert of the merge, plus every step a revert alone does not undo, such as a migration
  or data change, a config or flag, or a published artifact. When there is none, say so.

Write both at S5.2, from the §2 call and, on a non-final pass, the §5 call, which runs before the PR-body write, so
they cite every path the phase shipped, its fix rounds' commits included. Fresh mode writes them into the staged
`pr.md` before `create-pr` (`resolve-spine.md` S5 lists the sections). Each later phase rewrites both with the PR-body
write it already makes, so they describe the whole PR as it stands, not only the latest phase.
The epic integration PR carries neither: its body is `epic.md`'s own, each story's PR carries its own risk and revert,
and the integration PR's revert is its merge.

**`## Out-of-plan changes`** is the third section written from the same calls, and the only one a script parses
([`../../_shared/review-threads.md`](../../_shared/review-threads.md) §8, `parse.scan_out_of_plan`): one
`` - `<path>` — <reason> `` line per path any `assess` call in the run reported in `out_of_plan` — the §2 call and, on a
non-final pass, the §5 call — the reason one line of yours saying why the change departs from the plan. It is keyed
by path and kept across phases: keep every `facts.out_of_plan.entries` line (a path reported again keeps its line
unless its reason changed), rewrite each `facts.out_of_plan.unparsed` line (every bullet that did not parse) in the grammar — a repeated path's line
merging into that path's one line — and drop none. The scan reads bullets only; restage from the current body, so
any prose under the heading stays as written. Nothing
reported and no section yet → no section. `## Plan override` keeps its own meaning: the PR proceeded without a plan,
and a no-plan run reports no `out_of_plan`. Keep it apart from `## Risk`: `## Risk` says what could break and may cite the same paths in prose; this section is a
per-path record of why each one is there, which the evaluator quotes in that file's review thread when the PR's author is trusted (`review-threads.md` §8). The epic
integration PR's run writes none: its plan's `## Changes` names only cross-story surfaces, and each story PR records
its own.

## §5 The shipped-diff call at S5.2 (non-final pass)

The pause decision reads the same signals over what the phase actually shipped: the pass's review range plus
everything its fix rounds committed. After S5.2's push, and before its PR-body write and the loop comment, a **non-final** pass
runs the §2 call again with **the same range flag this pass's S5.1 call used** (`--since <sha>`, or
`--base-ref <facts.workspace.base_ref>` when that base was the branch).

When `HEAD` has moved past the loop-entry SHA (a fix round committed), re-dispatch the judgment over the shipped
diff first, staged and filled as in §2 with the same `<base>`, and overwrite `<facts.scratch>/blast-radius-judgment.md`
with its verdict. When `HEAD` is still the loop-entry SHA, the range is the S5.1 range: pass the S5.1 verdict file
again, without a dispatch. A run with no plan, or an empty staged diff, dispatches none and omits `--judgment`, as at
§2. Then run:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/blast_radius.py assess "<facts.workspace.path>" \
  <the S5.1 call's --plan arguments> --judgment "<facts.scratch>/blast-radius-judgment.md" \
  --phase <N> <the S5.1 range flag>
```

Its `raised` is a review trigger ([`phase-continuation.md`](phase-continuation.md) §2), and its `why` goes into the
`Checkpoint:` line's why when it raised. Its `level` sets nothing: this pass's review is done. The range is never
`facts.workspace.upstream_sha` (the phase link's anchor), which after an interrupted run or an S4 **Rebuild un-ticked**
would miss commits this phase ships. A final pass makes no S5.2 call: the session stops after it anyway.

## §6 The blast-radius judgment

The planner's `## Review level` judges the blast radius before the diff exists, and the §2 script's fact signals
see only which paths changed. The judgment reads what the diff actually does: a context-blind `Explore` sub-agent
([`blast-radius-judgment-prompt.md`](blast-radius-judgment-prompt.md)) that returns `verdict: raise` or
`verdict: hold`, a `raise` citing each `dependents`, `contract`, `seam`, `security`, `data` or `money` site the
plan's level reason did not account for. The issue (#128) left four questions to the plan; these are the answers:

- **It only raises.** A `raise` raises the level and, at §5, raises a review; a `hold` changes nothing. It never
  lowers either, and no verdict removes a level or a pause the fact signals add: they stay the floor beneath it.
- **One step total.** A `raise` joins the fact signals in the one `raised` the script computes, and the level then
  steps up once, capped at `xhigh`. A fact signal plus a judged raise is still one step.
- **What it reads.** The staged scope diff, the workspace (for the callers, consumers and seams the diff reaches),
  the assembled plan — its `## Phases`, the phase's `## Review level` line and why, `## Architecture decisions`,
  `## Changes (file-level)`, and on a story its `## Epic contract`, the epic's cross-story contracts as the story
  plan carries them (resolver prep stages no parent-epic plan) — the phase number and final flag, and the declared
  catalogue entries. Never the loop history, prior verdicts, `code-review` output, or the script's own signals, so
  the judgment stays independent of what already counts. A phase with no valid `## Review level` line is judged
  against the default `high` with an empty reason, so a raise still needs a cited site. The final pass is judged
  against the highest declared level and every phase's reason together, since its diff holds every phase's work.
- **What it costs.** One dispatch per `assess` call on a planned run: every S5.1 loop entry, and at S5.2 on a
  non-final pass only when `HEAD` has moved past the loop-entry SHA. When `HEAD` is still the loop-entry SHA the
  S5.2 range is the S5.1 range, so the S5.1 verdict file is passed again. That is at most two dispatches on a
  non-final phase and one on the final pass. Light re-reviews call no `assess`, so they dispatch none, and neither
  does an empty staged diff.

**No plan, no judgment.** On a run with no plan the level is already `high` and there is no declared level for the
diff to have underjudged, so neither call dispatches one and `assess` runs without `--judgment`.

**Strict fallbacks.** Without `--judgment` the envelope's `judgment` is `null` and nothing else changes. A
`--judgment` file the script cannot read is a usage error (exit 2, `BLAST_RADIUS_JUDGMENT_UNREADABLE:` on stderr).
A verdict it reads but cannot parse, or `code: AMBIGUOUS`, counts as a raise, with a notice; §2 re-dispatches once
before passing such a file. The `why` names the judgment's dimension and site, never its evidence sentences, which
stay in `judgment.signals` for `## Risk`.
