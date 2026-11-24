# Design check — shared procedure

The **design check** (#206) compares what a build shows a user with the design the operator approved: the
issue's `<!-- ux-design:v1 -->` record ([`design-record.md`](design-record.md)). Each difference the build shows
from a recorded state is answered on GitHub, where every other review question is ([`review-threads.md`](review-threads.md)
§7): one the build session declared as a departure, or the plan or the operator already agreed, is **explained** and
joins one summary thread the operator approves once (#241); any other is **unexplained** and gates as a review
thread of the `design` kind. Two callers run this one procedure and differ only in when it runs and how its threads
post: the **resolver**, at the plan's build-check phase, and the **evaluator**, at the gated head. Facts by script,
meaning by model: scripts stage the record's captures and the explanations and turn differences into threads, the
model captures the build, one context-blind sub-agent judges each state, and a second classifies each difference.

## When it runs

`facts.design` (`scripts/design_record.py` `design_fact`, composed by the planner, resolver, evaluator and review
preps; the evaluator's reads the PR's planned closing issue, or with none on a PR into a non-default base (every story
PR) — where GitHub records no closing reference — the issue the branch names) says whether there is anything to compare:

| `facts.design.check` | Meaning | The check |
|---|---|---|
| `none` | the plan cites no design target | does not run, and posts no design thread; a design thread an earlier comparison posted still gates by its state, and with no target cited an `accept` clears it ("What the threads do") |
| `run` | the plan cites the record that stands (`citation: current`) | runs — when the checkout declares the `<!-- ui-prototype -->` block **and** its `**Fidelity:**` line reads a level (`facts.design.fidelity.level` set); otherwise it holds (routes below) |
| `hold` | the plan cites a record that is `missing`, `unapproved` or `revised` since, or its `## Phases` or `Build check:` line cannot be read | does not run; the caller names the reason and the route |

`facts.design.check_phase` is the phase whose build is checked: the plan's `- Build check: phase <N>` line under
`## UI decisions` (`design_record.cited_check`), else, when the plan cites a target but names no phase, its last
`code-shipping` phase. It is `null` when the phase cannot be read, and the check then holds.

Routes for a hold: `revised` → `/ctrl-freaq:planner #<issue>` (a revise adopts the revised record); `current` with
`facts.design.check_phase` `null` (the plan's `## Phases` cannot be read, or its `Build check:` line cannot be read or names no phase
that ships code) → `/ctrl-freaq:planner #<issue>` (a revise fixes the phase); `unapproved` or
`missing` → `/ctrl-freaq:designer #<issue>`; no `<!-- ui-prototype -->` block (a prep that read the checkout reports
`facts.design.block.present: false`) → `/ctrl-freaq:setup`; a block with no fidelity level (#241) →
`/ctrl-freaq:setup`, named as a **missing** line (`facts.design.fidelity.declared: false`) or a **malformed** one
(`declared: true`, `level: null`, with each of its `findings`). There is no default level: the check never assumes
one, so a repo whose block predates the line holds every design check until setup is re-run and records the level.
(The planner, with no level, plans against everything the record shows — the strictest reading, so no plan departs
silently — and names the missing line; that is not a level the check runs at.) A hold whose `check_phase` is `null` belongs to no phase:
the resolver names it on the final pass.

`facts.design.block` is the block a prep read at its checkout, `{present, source, text}`, `present: false` when the
checkout declares none. It is `null` when the prep read no checkout (a `--refresh`, or no asserted workspace): that
means **not read**, never absent — take the block from the pass's full or opening prep. `facts.design.fidelity`
(#241) is the block's `**Fidelity:**` line, `{declared, level, findings}` ([`design-record.md`](design-record.md)
"The fidelity line"): `null` exactly when the block is, `{declared: false, level: null, findings: []}` with no block. A hold never adds a stop of its own: the caller says it where the operator already reads, and
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
   reports it `differs` with the one difference `state — the build does not reach this state`, a difference like any
   other. **The capture fails** (the
   tooling): the block's tool cannot run, the build cannot be started at the judged commit, or a state you reached
   cannot be written as a capture. That is a failed step: stop before step 3, pass no design input, and never compare
   a partial capture set. This contract names no capture tool or method: those are the repo's.
3. **Compare.** Dispatch the comparison `Explore` sub-agent
   (`skills/evaluator/references/design-comparison-prompt.md`) with the record's state descriptions
   (`<<record_states>>`: one line per state in `design-states.json`, its id then its description), the
   `design-record/` directory (`<<record_captures_dir>>`) and the `design-build/` directory (`<<build_captures_dir>>`),
   as absolute paths, the declared level (`<<fidelity>>`: `facts.design.fidelity.level`) and the block's text verbatim
   (`<<prototype_guidance>>`: `facts.design.block.text`, the owner's word on how this repo's prototypes are read), and
   nothing else — not the plan, the diff, what the session changed, declared departures or an earlier verdict, so the
   build's author never judges its own work. The guidance is passed as written, with no neutralisation: the
   comparison is an `Explore` sub-agent that reads no reviewer flags. It is context-blind and reports only differences
   in what the record binds at that level (flows, states, copy, layout), each with its aspect, never usability
   opinions. Write its reply verbatim to `<facts.scratch>/design-comparison.md`.

   Steps 3.1 and 3.2 run after every comparison, never reusing an earlier run's files: step 5's thread script reads a
   classification made over other differences than this run's comparison reports as unread ("What holds when a step
   fails").

   3.1. **Stage the explanations** (#241). Run, from the checkout the session runs in:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/review_threads.py explain \
     --design-verdict "<facts.scratch>/design-comparison.md" --design-states "<facts.scratch>/design-states.json" \
     --pr-body "<pr body>" [--plan "<the staged plan>"] --trusted <true|false> \
     --out "<facts.scratch>" > "<facts.scratch>/design-explanations.json"
   ```

   `--pr-body` is the PR body whose `## Design departures` ("The departures section") and `## Decided deviations`
   ([`review-threads.md`](review-threads.md) §12) it reads: the evaluator's `facts.design_departures.body_path`, the
   resolver's body this S5.2 posted (the same file its `pause` call passes). `--plan` is the staged plan (the
   resolver's `facts.distiller_bundle.plan_marker_path`, the evaluator's chosen plan's `body_path`), omitted with
   none. `--trusted` is `facts.design_departures.trusted`; on a fresh first phase pass `true`, since this session's
   own `create-pr` opened the PR. It makes no `gh` call and is always `ok`. Its envelope, saved to
   `design-explanations.json`, is step 5's `--design-explanations`: `differences` (`[{id, state, aspect, text}]`,
   each id `<state-id>#<n>`), `explanations` (`[{id, source, states, aspect, text, reason}]`), `files`, the two
   files it wrote, `design-differences.md` and `design-explanations.md`, and `sources_sha256`, the hash of the
   `## Design departures` and `## Decided deviations` entries its `--pr-body` carried — so step 5's `--pr-body` must
   carry those same entries (an edit elsewhere in the body changes nothing), or the classification reads as unread.
   The explanations are, in this order:
   - `D<n>` — each `## Design departures` entry, only when `--trusted true`;
   - `U<n>` — each `## UI decisions` bullet of the plan marked `DEVIATION (agreed <date>)` that names a recorded
     state by its backticked id (`design_record.ui_deviations`), from the plan, a trusted-author read;
   - `X<n>` — each `## Decided deviations` entry, only when `--trusted true`.

   Untrusted, it drops every `D` and `X` with the `DESIGN_DEPARTURES_UNTRUSTED` notice when the body carries any. An
   unread verdict reports no difference (`DESIGN_VERDICT_UNREAD`).

   3.2. **Classify** (#241), unless the envelope's `files.classification` is set: with no difference or no explanation
   nothing can be explained, and `explain` has written the all-unexplained verdict to
   `<facts.scratch>/design-classification.md` itself (it reports that path there), so dispatch nothing. Otherwise
   `files.classification` is null (and `explain` removed any file an earlier run left there). Dispatch the departure classification `Explore`
   sub-agent (`skills/evaluator/references/design-departure-prompt.md`) with the two staged files as absolute paths
   (`<<differences_path>>`: `files.differences`; `<<explanations_path>>`: `files.explanations`), and nothing else —
   not the captures, the plan, the diff, what the session changed, the comparison's reasoning or the threads. So the
   build's author never decides which of its own differences are explained, and the comparison never sees a
   departure. Write its reply verbatim to `<facts.scratch>/design-classification.md`. It only classifies: the thread
   script decides what counts ("What the threads do"). When the dispatch fails, pass step 5 no classification
   input, and every difference is unexplained. Step 5 passes both classification inputs whenever this run's
   `design-classification.md` exists (written by `explain` or by this dispatch).
4. **Name the sites.** Write `<facts.scratch>/design-sites.txt` afresh on every run — empty when no state differs,
   since step 5 always passes it — with one line for each state the verdict reports `differs`: `<state-id> <path>[:<line>[-<line>]]`, the build file in the diff that renders
   it. A state you cannot place gets no line; it is listed as `unanchored_design`, never dropped.
5. **Pass them to the thread script**, beside the run's other inputs:

   ```
   --design-verdict "<facts.scratch>/design-comparison.md" --design-sites "<facts.scratch>/design-sites.txt" \
   --design-target <facts.design.record.fingerprint> --design-states "<facts.scratch>/design-states.json" \
   --design-build "<facts.scratch>/design-build" \
   [--design-explanations "<facts.scratch>/design-explanations.json" \
    --design-classification "<facts.scratch>/design-classification.md"]
   ```

   on `review_threads.py flags` (the evaluator) or `pause` (the resolver; `pause --design-only` on the final pass of
   a last build-check phase). All four of `--design-verdict`, `--design-target`, `--design-states` and
   `--design-build` go together. The two classification inputs (#241) go together, only with those four, and only
   when step 3.2 wrote its verdict; without them every difference is unexplained. A missing file named by either is
   a usage error (exit 2), so a failed dispatch passes neither. `--design-build` names step 2's directory explicitly, because the resolver's thread
   script runs with `--scratch "<facts.scratch>/pause-<N>"`, so a path derived from `--scratch` would find no build
   capture (#237). Every design thread in the spec at this run's target then carries a `captures` field: the record's
   capture and the build's at their fallback values, with the thread's unexplained differences as the caption; a
   design group carries its first member's captures, and the summary none ("The captures comment" names the
   successor that carries none).
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
- `<state-id>` · differs
  - <aspect> — <what the build shows differently, in plain words>
```

One line per recorded state, and under each `differs` line one indented line per difference, its aspect one of
`flow`, `state`, `copy` or `layout` (`parse.DESIGN_ASPECTS`, #241); `code: AMBIGUOUS` instead when the captures cannot
be read. Each difference's id is `<state-id>#<n>`, 1-based in verdict order. A state with no build capture is
`differs` with the one difference `state — the build does not reach this state`. `scripts/parse.py`
`scan_design_comparison` reads it, never raising. These lines are `unparsed`: an unknown verdict, a `differs` with no
difference, a difference line with no known aspect or no text, a `match` carrying a difference, an indented line
under a `match` or under no state line, or a bad state id. Each leaves every state it names (its parent state line's,
a backticked token, or a bare token another line gives a verdict for) uncovered whatever another line says, and a
line naming none leaves every state uncovered. A conflicting duplicate reads `differs`, and a section it cannot find,
or `code: AMBIGUOUS`, is the `DESIGN_VERDICT_UNREAD` notice from the thread script. The one-line
`` - `<state-id>` · differs — <difference> `` form (#206) still reads, as one difference with no aspect, which no
departure covers; the prompt no longer writes it, and one with indented lines under it too is `unparsed`.

## The departures section

A resolver-written PR-body section, read by a script (`parse.scan_design_departures`), #241. It records each
recorded state the build departs from on purpose, with what was found after the design that made it right:

```
## Design departures
- D1 · `empty-list` · copy — reads "No entries yet" — reason: the plan's grounding found the importer's existing empty-state copy, which every other list uses
```

- **Grammar.** `D<n>`, the backticked recorded state id, the aspect (`flow`, `state`, `copy` or `layout`), then what
  the build does instead; the reason is the text after the **last** ` — reason: `, so a departure that itself holds
  ` — ` still splits once. A line that does not parse is `unparsed`, never an entry; so is a repeated id, the first
  standing. One line covers one state on one aspect: a difference on another aspect needs its own line.
- **Writer.** The resolver only, at S5.2's PR-body write of the build-check phase and of every later pass, before
  the comparison: one line per recorded state the build departs from at the declared fidelity
  (`facts.design.fidelity.level`), its reason naming what was found after the design (the plan's grounding, a review
  finding, an edge case). A difference the build did not intend is a fix, never a departure. A fix round adds a line
  when the operator's answer on an unexplained design thread keeps the build, and withdraws one when the operator
  rejects it on the design summary (`skills/resolver/references/review-fix-round.md` step 8). The section is kept
  across phases: a line stays while the build departs, ids are never reused (a withdrawn `D<n>` included), and
  re-wording a line keeps its id.
- **Readers.** `facts.design_departures` is `{present, entries: [{id, state, aspect, departure, reason}], unparsed,
  trusted, body_path}`, reported by the evaluator's and the resolver's preps (the resolver's `body_path` `null`; a
  fresh run's absent, `trusted: false`). `trusted` is the evaluator's `reasons_trusted` value
  ([`review-threads.md`](review-threads.md) §8, §12), a parsed entry being one more trigger of the one association
  read, and the resolver's own-PR value. Planted text would clear a gating thread, so untrusted, no departure and no
  decided deviation explains a difference (step 3.1).

## What the threads do

The thread script owns this ([`review-threads.md`](review-threads.md) §2, §4, §6, §7):

- **Explained or unexplained** (#241, `review_threads.classify_differences`). A difference is explained only when the
  classification calls it explained and every explanation it cites exists and may cover it: a `D` on the same state
  and the same aspect (so a difference with no aspect never), a `U` when the state is among the states it names, an
  `X` by existing. The script enforces this, never the model. A missing, unparsed, conflicting or disallowed verdict,
  and every difference when no classification was passed or it was unread, is unexplained: more threads, never fewer.
- **Unexplained** differences gate. One thread per state with an unexplained difference, keyed
  `design:<state-id>:<path>`, stamped ` · target <fingerprint>` and listing only that state's unexplained
  differences, exempt from the thread cap. States whose sites resolve to one anchor share one **design group**
  (` · group design`, one `design:` key per state), which re-cuts as a sensitive group does when its member set
  changes. A re-run at the same target posts nothing new for a state that already has a thread, whatever file the
  sites name.
- **Explained** differences join one **summary** per target: one `design-departure:<id>` key per explanation that
  covers at least one of them, stamped ` · target <fingerprint>` and ` · summary <12 hex>`, the hash of the target and
  of what the summary approves, never of the comparison's wording. It anchors on the first covered state's site, else
  the first differing state's, else the first file in the diff (its subject is its explanations, not code); only an
  empty diff leaves its differences in `unanchored_design`, marked `explained_by`. A summary head moves — takes a
  successor or retires — only on a **settled** run: the classification was read for every difference with no line
  unparsed and no untrusted departure dropped, and the verdict covers every recorded state (or there was no
  difference to classify). Any other run leaves every summary head as it is and posts a fresh summary beside it for
  what it did explain. A changed hash (a departure
  added, removed or re-worded, a new state or aspect covered, a new target) takes a successor, an `unreviewed` one
  asking afresh, and replaces an `accepted` one too, whose approval never saw the change; the same hash posts
  nothing; a `changes-requested` summary keeps gating and no new one posts until the resolver replies; no explained
  difference retires it `summary-emptied`.
- An unexplained difference clears by one of three routes:
  - a **fix**: a later comparison at the same target reports the state `match`;
  - a **departure**: if the build is right, a reply asks for one, the resolver declares it under `## Design
    departures` with its reason, and the next check classifies the difference explained and moves it to the summary;
  - a **changed target**: once the designer revises the record and a planner revise adopts it, an `accept` decision
    (or a resolution) clears the thread (`threads[].design.adopted`) — and so it does once a planner revise withdraws
    the plan's target (the operator answered **Plan without the record**, which the revise card offers only once the
    record no longer stands: unapproved or deleted, `../planner/references/design-detour.md` §2).

  On a fix or a departure, an `unreviewed`, `accepted` or `superseded` thread whose every state is clear retires,
  `design-matched` when every state matches, else `design-explained`; a group member that is clear leaves the group.
  An `accept` against the record it was compared with never clears one. A `changes-requested` thread keeps gating
  until the resolver replies.
- The **summary** clears with one approval: `accepted` (an `accept` decision reply, or a trusted resolution) clears
  every difference it lists, with no `adopted` condition (`threads[].design.summary`). Its hunk check never runs, so
  a moved anchor never supersedes the approval. A reply naming a difference asks for it to be fixed instead. The
  decision-reply set is unchanged.
- `unanchored_design` lists every differing state whose unexplained differences have no anchor in the diff (an
  explained difference needs none of its own: the summary anchors elsewhere): the difference is real but has no site
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
  A record capture that is already an attachment URL is shown as it stands; one the root cannot show as it stands (a
  non-attachment URL the staging still fetched) uploads its staged copy too [operator decision, #237]. Uploads are pre-filtered, never
  refused: an image type gh uploads (`png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`), non-empty, at most 20 MiB, and at
  most 50 per run. Anything else stays at its fallback.
- **Idempotence** is per (head, target, state, side). `captures` reads the PR's conversation comments and reuses a
  capture that a captures comment already holds at the same head and target, as an `https` attachment URL, in
  either image form gh leaves (Markdown or `<img>`, the same reader the receipt's `attachment_urls` uses). Only a
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
| `check: hold`, no `<!-- ui-prototype -->` block (`facts.design.block.present: false` from a prep that read the checkout), or a block with no readable fidelity level (`facts.design.fidelity.level: null`, #241) | no check, no design thread; the reason and its route are named (the pause review's body and the Review card's brief, or on a final pass the design-only review's body — a phase-stamped review the resolver posts with no comparison input and so no new design thread (a superseded design head still takes its successor, or, its file gone from the diff, keeps gating) — and the `## Session summary`; the evaluator's verdict review) |
| a capture unavailable (step 1), a capture that failed (step 2: the tooling, not a state the build does not reach), or an unread verdict | no design thread posts from this run; the failure is named the same way, and the evaluator does not approve |
| a staging that is not the target's (`DESIGN_TARGET_MISMATCH`: the `design-states.json` `fingerprint` is another record's, or missing) | read as an unread verdict: no design thread posts or retires, every recorded state is in `uncovered_design`, and the evaluator does not approve; stage again from the current prep's record |
| no classification (step 3.2 not dispatched, or its dispatch failed), or an unread one (`DESIGN_CLASSIFICATION_UNREAD`: no `## Classification` section, `code: AMBIGUOUS`, a classification made over other differences than this run's comparison reports, or explanations whose departures or decided deviations differ from step 5's `--pr-body`'s) | no design summary retires or takes a successor; every difference is unexplained and gates on its own thread; the design threads still post |
| untrusted departures (`facts.design_departures.trusted` false: another author's PR, or an association that could not be read; `DESIGN_DEPARTURES_UNTRUSTED` when the body carries any) | no departure and no decided deviation explains a difference; the plan's agreed deviations still do; when the body carried one, the run is not settled, so no design summary retires or takes a successor |
| an upload that did not land: `ATTACH_UNSUPPORTED`, an `ATTACH_PARTIAL` capture with no URL, a failed read-back, a pre-filtered capture, or `DESIGN_CAPTURES_UNREAD` (the PR's comments could not be read, so nothing uploads and a re-run cannot duplicate) | the capture keeps its fallback (above); the design threads still post; `apply-captures`' `not_uploaded` names each capture and its reason where the run names the check's outcome |

A failed step never retires a thread and never counts as a match.
