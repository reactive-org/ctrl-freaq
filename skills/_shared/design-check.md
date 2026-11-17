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
preps; the evaluator's reads the PR's planned closing issue, or with none on a PR into a non-default base (every story
PR) — where GitHub records no closing reference — the issue the branch names) says whether there is anything to compare:

| `facts.design.check` | Meaning | The check |
|---|---|---|
| `none` | the plan cites no design target | does not run, and posts no design thread; a design thread an earlier comparison posted still gates by its state, and with no target cited an `accept` clears it ("What the threads do") |
| `run` | the plan cites the record that stands (`citation: current`) | runs — when the checkout declares the `<!-- ui-prototype -->` block; with none it holds (routes below) |
| `hold` | the plan cites a record that is `missing`, `unapproved` or `revised` since, or its `## Phases` or `Build check:` line cannot be read | does not run; the caller names the reason and the route |

`facts.design.check_phase` is the phase whose build is checked: the plan's `- Build check: phase <N>` line under
`## UI decisions` (`design_record.cited_check`), else, when the plan cites a target but names no phase, its last
`code-shipping` phase. It is `null` when the phase cannot be read, and the check then holds.

Routes for a hold: `revised` → `/ctrl-freaq:planner #<issue>` (a revise adopts the revised record); `current` with
`facts.design.check_phase` `null` (the plan's `## Phases` cannot be read, or its `Build check:` line cannot be read or names no phase
that ships code) → `/ctrl-freaq:planner #<issue>` (a revise fixes the phase); `unapproved` or
`missing` → `/ctrl-freaq:designer #<issue>`; no `<!-- ui-prototype -->` block (a prep that read the checkout reports
`facts.design.block.present: false`) → `/ctrl-freaq:setup`. A hold whose `check_phase` is `null` belongs to no phase:
the resolver names it on the final pass.

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
   the format and how each state is reached. Empty `<facts.scratch>/design-build/` first — the scratch dir outlives
   the run, and a capture an earlier run left would stand in for a state this build no longer reaches — then write
   each state's capture there as `<state-id>.<ext>`, one per recorded state. Two outcomes, by cause. **The build does
   not reach a state** (the product): the running build offers no way to it, so it gets no file and the comparison
   reads `differs — the build does not reach this state`, a difference like any other. **The capture fails** (the
   tooling): the block's tool cannot run, the build cannot be started at the judged commit, or a state you reached
   cannot be written as a capture. That is a failed step: stop before step 3, pass no design input, and never compare
   a partial capture set. This contract names no capture tool or method: those are the repo's.
3. **Compare.** Dispatch the comparison `Explore` sub-agent
   (`skills/evaluator/references/design-comparison-prompt.md`) with the record's state descriptions
   (`<<record_states>>`: one line per state in `design-states.json`, its id then its description), the
   `design-record/` directory (`<<record_captures_dir>>`) and the `design-build/` directory (`<<build_captures_dir>>`),
   as absolute paths, and nothing else — not the plan, the diff or what the session changed. It is context-blind and reports only differences in
   what the record binds (flows, states, copy, layout), never usability opinions. Write its reply verbatim to
   `<facts.scratch>/design-comparison.md`.
4. **Name the sites.** Write `<facts.scratch>/design-sites.txt` afresh on every run — empty when no state differs,
   since step 5 always passes it — with one line for each state the verdict reports `differs`: `<state-id> <path>[:<line>[-<line>]]`, the build file in the diff that renders
   it. A state you cannot place gets no line; it is listed as `unanchored_design`, never dropped.
5. **Pass them to the thread script**, beside the run's other inputs:

   ```
   --design-verdict "<facts.scratch>/design-comparison.md" --design-sites "<facts.scratch>/design-sites.txt" \
   --design-target <facts.design.record.fingerprint> --design-states "<facts.scratch>/design-states.json" \
   --design-build "<facts.scratch>/design-build"
   ```

   on `review_threads.py flags` (the evaluator) or `pause` (the resolver; `pause --design-only` on the final pass of
   a last build-check phase). All four of `--design-verdict`, `--design-target`, `--design-states` and
   `--design-build` go together. `--design-build` names step 2's directory explicitly, because the resolver's thread
   script runs with `--scratch "<facts.scratch>/pause-<N>"`, so a path derived from `--scratch` would find no build
   capture (#237). Every design thread in the spec then carries a `captures` field: the record's capture and the
   build's at their fallback values, with the verdict's difference as the caption.
6. **Upload the captures** (#237), only when that spec carries a design thread with `captures`, and always before
   `review-post`. `review-post` reads nothing before its size gate, so the URLs must already be in the spec. Run
   these, with `<scratch>` the thread script's own `--scratch`:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/review_threads.py captures "<facts.workspace.path>" --repo <owner/repo> \
     --pr <PR> --spec "<spec_path>" --design-states "<facts.scratch>/design-states.json" \
     --scratch "<scratch>" > "<scratch>/captures.json"
   ```

   When its `body_path` is set, post that comment with one `--attach` per `attach` entry, verbatim, and save the
   envelope as the receipt:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> pr <PR> "<body_path>" \
     --attach "<attach entry>" [--attach "<attach entry>"]... > "<scratch>/captures-receipt.json"
   ```

   Then write the outcome into the spec. Pass `--receipt` only when that post returned an `ok` envelope. Omit it
   when no comment was posted, on `ATTACH_UNSUPPORTED` (exit 1, nothing written), or after any other failed post:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/review_threads.py apply-captures --spec "<spec_path>" \
     --pending "<pending_path>" [--receipt "<scratch>/captures-receipt.json"]
   ```

   Its `not_uploaded` (`[{state, side, reason}]`) names every capture that stays at its fallback. The caller names
   them where it already names the check's outcome: the pause review's body and the Review card's brief, the
   design-only review's body, or the evaluator's verdict review. `review-post` then stamps each design root with the
   captures line and the side-by-side table. An upload never blocks, retires or withholds a design thread.

## The verdict

```
## Comparison
- `<state-id>` · match
- `<state-id>` · differs — <what the build shows differently, in plain words>
```

One line per recorded state; `code: AMBIGUOUS` instead when the captures cannot be read. A state with no build
capture is `differs — the build does not reach this state`. `scripts/parse.py` `scan_design_comparison` reads it,
never raising: an unknown verdict, a `differs` with no difference or a bad state id is `unparsed` and leaves every
state it names (a backticked token, or a bare token another line gives a verdict for) uncovered whatever another line says — a line naming none leaves every state uncovered — a conflicting duplicate reads `differs`, and a section it cannot find, or `code: AMBIGUOUS`, is the `DESIGN_VERDICT_UNREAD` notice
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
  `accept` decision (or a resolution) clears the thread (`threads[].design.adopted`) — and so it does once a planner
  revise withdraws the plan's target (the operator answered **Plan without the record**, which the revise card offers
  only once the record no longer stands: unapproved or deleted, `../planner/references/design-detour.md` §2). An `accept` against the record
  it was compared with never does. The decision-reply set is unchanged.
- `unanchored_design` lists every differing state with no anchor in the diff: the difference is real but has no site
  to thread, so the evaluator soft-rejects to the resolver, naming each state. `uncovered_design` lists every
  recorded state the verdict did not cover; it, an unread verdict or a hold keeps the evaluator from approving.

## The captures comment

Step 6 posts at most one PR conversation comment per new set of design threads. `review_threads.py captures` writes
every byte of it:

```
<!-- design-captures:v1 -->
<!-- head <40-hex> · target <12-hex> -->

Captures of the build and the approved design for this pull request's design review threads, uploaded by the design check.

- `<state-id>` · build · ![build <state-id>](<ref>)
- `<state-id>` · record · ![record <state-id>](<ref>)
```

`<ref>` is staged as the absolute local path that `--attach` uploads. gh rewrites it to the attachment URL, and
`gh_persist.py` reports where each one landed (`attachment_urls`).

- **What uploads.** Only the captures of the design threads in this run's spec that carry them, fresh and
  successor alike: each thread's build capture, plus the record's capture when the record holds it as a repo path. The record capture is
  uploaded from its staged copy (`design-record/<state-id>.<ext>`), so it renders whatever the repo's visibility.
  A record capture that is already an attachment URL is shown as it stands. Uploads are pre-filtered, never
  refused: an image type gh uploads (`png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`), non-empty, at most 20 MiB, and at
  most 50 per run. Anything else stays at its fallback.
- **Idempotence** is per (head, target, state, side). `captures` reads the PR's conversation comments and reuses a
  capture that a captures comment already holds at the same head and target, as an `https` attachment URL. Only a
  trusted author's comment counts: `OWNER`, `MEMBER` or `COLLABORATOR`, as for every pipeline marked comment
  (#179). A re-run at the same head with nothing new posts nothing. The comment is never edited to top it up:
  `edit-comment` takes no `--attach`, so captures still missing go in the next comment.
- **Fallbacks**, which the root shows when an upload does not land:
  - the build's capture: its local path, labelled as local to the checkout that captured it and not uploaded;
  - the record's repo-path capture: a link to the file at the judged head;
  - a state the build does not reach: says so.

  Two design threads carry no captures line. One is a successor posted with no comparison (a hold or a failed step),
  since the only captures it could show are an earlier head's. The other is a successor that keeps an older record's
  fingerprint (its `target` is not this run's), since this run's record and difference belong to another comparison
  [operator decision, #237]. The upload is stamped and reused under the staged record's fingerprint, which is the
  comparison's target.

## What holds when a step fails

| Failure | Result |
|---|---|
| `check: none` | no check, no design thread |
| `check: hold`, or no `<!-- ui-prototype -->` block (`facts.design.block.present: false` from a prep that read the checkout) | no check, no design thread; the reason and its route are named (the pause review's body and the Review card's brief, or on a final pass the design-only review's body — a phase-stamped review the resolver posts with no comparison input and so no new design thread (a superseded design head still takes its successor, or, its file gone from the diff, keeps gating) — and the `## Session summary`; the evaluator's verdict review) |
| a capture unavailable (step 1), a capture that failed (step 2: the tooling, not a state the build does not reach), or an unread verdict | no design thread posts from this run; the failure is named the same way, and the evaluator does not approve |
| a staging that is not the target's (`DESIGN_TARGET_MISMATCH`: the `design-states.json` `fingerprint` is another record's, or missing) | read as an unread verdict: no design thread posts or retires, every recorded state is in `uncovered_design`, and the evaluator does not approve; stage again from the current prep's record |
| an upload that did not land: `ATTACH_UNSUPPORTED`, an `ATTACH_PARTIAL` capture with no URL, a failed read-back, a pre-filtered capture, or `DESIGN_CAPTURES_UNREAD` (the PR's comments could not be read, so nothing uploads and a re-run cannot duplicate) | the capture keeps its fallback (above); the design threads still post; `apply-captures`' `not_uploaded` names each capture and its reason where the run names the check's outcome |

A failed step never retires a thread and never counts as a match.
