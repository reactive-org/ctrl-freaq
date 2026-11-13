# Design check — shared procedure

The **design check** (#206) compares what a build shows a user with the design the operator approved: the
issue's `<!-- ux-design:v1 -->` record ([`design-record.md`](design-record.md)). Each recorded state the build
shows differently becomes a review thread of the `design` kind ([`review-threads.md`](review-threads.md) §7), so a
difference is answered on GitHub, where every other review question is. Two callers run this one procedure and
differ only in when it runs and how its threads post: the **resolver**, at the plan's build-check phase, and the
**evaluator**, at the gated head. Facts by script, meaning by model: scripts stage the record's captures and turn
differences into threads, the model captures the build, and a context-blind sub-agent judges each state.

## When it runs

`facts.design` (`scripts/design_record.py` `design_fact`, composed by the planner, resolver, evaluator and review
preps) says whether there is anything to compare:

| `facts.design.check` | Meaning | The check |
|---|---|---|
| `none` | the plan cites no design target | does not run, and posts no design thread |
| `run` | the plan cites the record that stands (`citation: current`) | runs |
| `hold` | the plan cites a record that is `missing`, `unapproved` or `revised` since, or its `## Phases` cannot be read | does not run; the caller names the reason and the route |

`facts.design.check_phase` is the phase whose build is checked: the plan's `- Build check: phase <N>` line under
`## UI decisions` (`design_record.cited_check`), else, when the plan cites a target but names no phase, its last
`code-shipping` phase. It is `null` when the phase cannot be read, and the check then holds.

Routes for a hold: `revised` → `/ctrl-freaq:planner #<issue>` (a revise adopts the revised record); `unapproved` or
`missing` → `/ctrl-freaq:designer #<issue>`; no `<!-- ui-prototype -->` block (a prep that read the checkout reports
`facts.design.block.present: false`) → `/ctrl-freaq:setup`.

`facts.design.block` is the block a prep read at its checkout, `{present, source, text}`, `present: false` when the
checkout declares none. It is `null` when the prep read no checkout (a `--refresh`, or no asserted workspace): that
means **not read**, never absent — take the block from the pass's full or opening prep. A hold never adds a stop of its own: the caller says it where the operator already reads, and
the evaluator does not approve while it holds.

## The procedure

1. **Stage the record's captures.** Run, from the checkout the session runs in:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/design_record.py stage <owner/repo> \
     --comment-id <facts.design.record.comment_id> --record "<facts.design.record.body_path>" \
     --root "<facts.workspace.path>" --out "<facts.scratch>/design-record" \
     > "<facts.scratch>/design-states.json"
   ```

   The envelope, saved to `design-states.json`, is step 5's `--design-states`. Each state's capture lands at
   `design-record/<state-id>.<ext>`. A path reference is copied only from inside the checkout. A URL or image
   reference is fetched from the record comment's rendered body, which is how a private repo's attachment is
   reached: only `https` on GitHub's attachment hosts, every redirect checked, at most 20 MiB. Each state it cannot
   stage is one `DESIGN_CAPTURE_UNAVAILABLE` notice. Any such notice is a **failed step**: stop here.

   **The staging is pinned to the staged record** (#206, R1.10). The comment is read again to reach its rendered
   images, so `stage` checks that the comment still holds the staged record, by fingerprint. When the record has been
   edited since, its rendered images are another record's: they are not used, each URL state falls back to its own
   reference URL, and a failure there names the moved record. The envelope's `fingerprint` is the staged record's,
   and step 5's thread script refuses a staging whose `fingerprint` is not the target (`DESIGN_TARGET_MISMATCH`).

   **A path capture is read from the checkout being judged** [operator decision 2026-11-14, #206]. A repo that keeps
   captures in a tracked store has the designer commit them on this issue's own branch, so there is no other copy
   to read; the record's fingerprint hashes its text, not the capture files. A capture file this branch changes
   therefore changes the baseline it is compared against, and only a review of the PR's diff sees that. Attached
   captures (the designer's default when the repo declares no store) are immutable URLs and carry no such residual.
2. **Capture the build's states.** Bring the build at the judged commit to each recorded state, and capture each
   one the way the repo's `<!-- ui-prototype -->` block says (`facts.design.block.text`): its prose names the tool,
   the format and how each state is reached. Write each state's capture to `<facts.scratch>/design-build/` as
   `<state-id>.<ext>`, one per recorded state. A state the build cannot reach gets no file; the comparison reads its
   absence. This contract names no capture tool or method: those are the repo's.
3. **Compare.** Dispatch the comparison `Explore` sub-agent
   (`skills/evaluator/references/design-comparison-prompt.md`) with the record's state descriptions, the
   `design-record/` directory and the `design-build/` directory. It is context-blind and reports only differences in
   what the record binds (flows, states, copy, layout), never usability opinions. Write its reply verbatim to
   `<facts.scratch>/design-comparison.md`.
4. **Name the sites.** For each state the verdict reports `differs`, write one line to
   `<facts.scratch>/design-sites.txt`: `<state-id> <path>[:<line>[-<line>]]`, the build file in the diff that renders
   it. A state you cannot place gets no line; it is listed as `unanchored_design`, never dropped.
5. **Pass them to the thread script**, beside the run's other inputs:

   ```
   --design-verdict "<facts.scratch>/design-comparison.md" --design-sites "<facts.scratch>/design-sites.txt" \
   --design-target <facts.design.record.fingerprint> --design-states "<facts.scratch>/design-states.json"
   ```

   on `review_threads.py flags` (the evaluator) or `pause` (the resolver; `pause --design-only` on the final pass of
   a last build-check phase).

## The verdict

```
## Comparison
- `<state-id>` · match
- `<state-id>` · differs — <what the build shows differently, in plain words>
```

One line per recorded state; `code: AMBIGUOUS` instead when the captures cannot be read. A state with no build
capture is `differs — the build does not reach this state`. `scripts/parse.py` `scan_design_comparison` reads it,
never raising: an unknown verdict, a `differs` with no difference or a bad state id is `unparsed`, a conflicting
duplicate reads `differs`, and a section it cannot find, or `code: AMBIGUOUS`, is the `DESIGN_VERDICT_UNREAD` notice
from the thread script.

## What the threads do

The thread script owns this ([`review-threads.md`](review-threads.md) §2, §4, §7):

- One thread per differing state, keyed `design:<state-id>:<path>` and stamped ` · target <fingerprint>`, exempt
  from the thread cap. A re-run at the same target posts nothing new for a state that already has a thread,
  whatever file the sites name.
- A thread clears with a **fix**: a later comparison at the same target reports its state `match`, and an
  `unreviewed`, `accepted` or `superseded` thread retires `design-matched`. A `changes-requested` one keeps gating
  until the resolver replies.
- Or with an **adopted revision**: once the designer revises the record and a planner revise adopts it, an
  `accept` decision (or a resolution) clears the thread (`threads[].design.adopted`). An `accept` against the record
  it was compared with never does. The decision-reply set is unchanged.
- `uncovered_design` lists every recorded state the verdict did not cover, and `unanchored_design` every
  differing state with no anchor. Either one, an unread verdict, or a hold keeps the evaluator from approving.

## What holds when a step fails

| Failure | Result |
|---|---|
| `check: none` | no check, no design thread |
| `check: hold`, or no `<!-- ui-prototype -->` block (`facts.design.block.present: false` from a prep that read the checkout) | no check, no design thread; the reason and its route are named (the pause review's body and the Review card's brief, or on a final pass the design-only review's body and the `## Session summary`; the evaluator's verdict review) |
| a capture unavailable (step 1), a build state the session could not capture at all, or an unread verdict | no design thread posts from this run; the failure is named the same way, and the evaluator does not approve |
| a staging that is not the target's (`DESIGN_TARGET_MISMATCH`: the `design-states.json` `fingerprint` is another record's, or missing) | read as an unread verdict: no design thread posts or retires, every recorded state is in `uncovered_design`, and the evaluator does not approve; stage again from the current prep's record |

A failed step never retires a thread and never counts as a match.
