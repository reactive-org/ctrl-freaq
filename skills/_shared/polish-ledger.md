# Polish ledger — shared contract

The **polish ledger** is the `## Polish` section of a PR body: the review findings the resolver's loop
recorded instead of fixing — polish it left, polish in code the PR does not change, and findings with no
shown cost — carried on the PR so the operator can decide what happens to them. This file is the single source of truth for its format and ownership; the procedure that puts entries
to the operator is [`polish-triage.md`](polish-triage.md). Three skills cite it: `resolver` (writer of
`open` / `applied`, of `drop` on a no-shown-cost finding or a reclassified defect, and of the operator's `apply` / `file` / `drop` at its finalisation), `evaluator`
(writer of the operator's `apply` / `file` / `drop` for whatever is still `open` when the PR reaches it, and of
the `reclassified: defect` `drop`),
and `planner` (reader, on a revise either of them routed to it).

## Why it exists

The review loop used to force every ≤ ~20-line fix (the retired Cheap-fix-override bucket) and exit
only on a round that changed nothing. With a `high`-effort reviewer that finds something new and small
on every run, that produced an unbounded tail of polish rounds that only a fixed iteration cap ended.
The loop now exits on **defect** progress alone (`resolve-spine.md` S5.1), and polish is decided once,
by the operator, before merge. It is decided **first at the resolver's finalisation**
(`resolver/references/finalisation.md`) — on the final pass, before the push — because the operator
usually wants polish applied, and applying it there costs one fix round in the same session; applying it
from the evaluator costs a soft-reject, a whole revision session and a second evaluation. The evaluator
stays the backstop: the reader that did not write the PR re-checks at head every decided entry that
rests on a claim about the code — an unfixed `apply`, a `file`, a `premise false` `drop` — and puts any
entry still `open` to the operator. The resolver fixes polish on the PR's own code; only what it leaves
lands here.

## Format

The section is `## Polish` in the PR body, one line per item, in the order they were recorded (the
example's paths mix Python, Ruby and Swift on purpose — the format is stack-neutral):

```
## Polish
- P1.1 · open — rename `parse_row` to say it scans, not parses — scripts/importer.py / parse_row
- P1.2 · file — dedupe the two date-format helpers — app/helpers/date_helper.rb / format_short — follow-up: same seam as P2.1
- P2.1 · applied (commit 3f9c2ab) — comment on `retry_after` states the old unit — lib/client.rb / retry_after — evaluator: misleading on a public API
- P2.2 · drop — prefer early return in `Checkout#submit` — app/models/checkout.rb / Checkout#submit — no longer applies at head
- P2.3 · apply — the error message still names the removed flag — Sources/Sync/SyncError.swift / SyncError.message — misleading to operators
```

- **`P<phase>.<seq>`** — the stable id: the phase whose loop recorded it, then a per-phase sequence.
  A revision run ships no phase, so it records under the last `kind: code-shipping` phase's number
  (`1` on a single-phase issue); an epic integration PR has no phases of its own and records under `1`. `<seq>` continues from the highest existing seq for that phase, so a
  re-entered session never mints a duplicate. Both skills cite items by id; an id is never reused or
  renumbered.
- **Disposition** — one word from the closed set below; `applied` alone carries `(commit <sha>)`.
- **Item** — the finding in one line, in the reviewer's terms. It must not contain ` — ` (the field
  separator).
- **Anchor** — durable only: a path plus a symbol or section heading. Never `path:line` — the line
  moves, and downstream sessions re-derive sites fresh (the rule `follow-up-filing.md` applies to
  follow-up descriptions).
- **Note** — optional; required on `apply` / `file` / `drop` (the proposer's reason, led by
  `operator: <answer>` when the operator overrode it) and on `applied` when it records why the entry was
  applied. An `open` entry carries `operator: leave for evaluator` when the operator answered that at
  finalisation: no later finalisation asks again, and the evaluator does.

## Dispositions (closed set)

| Disposition | Written by | Meaning |
|---|---|---|
| `open` | resolver, at the phase's S5.2 push | recorded, undecided — left by finalisation, or recorded before the final pass |
| `apply` | the operator's answer — resolver (finalisation) or evaluator | must be fixed in this PR before it merges (a note led by `operator: re-plan` routes it through a planner revise first) |
| `file` | the operator's answer — resolver (finalisation) or evaluator | becomes a follow-up (grouped per `follow-up-filing.md`) after the merge |
| `drop` | the operator's answer — resolver (finalisation) or evaluator; or the resolver itself, at the S5.2 push, for a no-shown-cost finding; or, for an entry found to be a defect, the resolver (finalisation, or S5.2 for an overturned dismissal) or the evaluator (S4) | not worth doing, or no longer applies at head, or found to be a defect to fix (the note leads `reclassified: defect — see loop comment`, from the evaluator `— see review`); the note says which |
| `applied (commit <sha>)` | resolver | an `apply` item fixed — at finalisation or in a revision run |

## Apply criteria

The single list of what makes polish **actively bad to merge** — the evaluator's `apply` bucket
(`evaluator/references/polish-adjudication.md`) and the resolver's must-fix-in-loop rule
(`resolver/references/review-fix-round.md` "Classification rubric") both read it from here. Finalisation
proposes `apply` against a lower bar — in scope, true at head, clearly right
([`polish-triage.md`](polish-triage.md) "The apply bar"):

- a comment, doc or message that states something false **that would mislead a reader into a wrong
  decision** (`resolver/references/review-fix-round.md` "Materiality"). Bookkeeping drift (a stale date
  stamp, version row or index echo) does not meet it: the resolver records it `drop`, no shown cost, at
  S5.2, and an `open` one that reaches the evaluator is proposed `drop` on its card, never recorded
  without asking;
- a misleading name on a public or cross-module surface;
- wrong user-visible copy.

## Rules

- **Only what the loop chose not to fix enters** (`resolver/references/review-fix-round.md`
  "Classification rubric"): polish on the PR's own code that hit one of the rubric's exceptions (a
  guard-path refactor, a change beyond the PR's seam), polish in code the PR does not change, and
  findings with **no shown cost** (a doc or comment finding that is not material included), which enter
  already `drop`, the note leading `no shown cost: <why>`.
  Polish the loop fixes itself is never recorded; a **defect** is never recorded here — it is
  fixed, deferred with a filed follow-up, or escalated. Polish meeting the "Apply criteria" never enters
  either: the resolver always fixes it in-loop, because parked it only buys a revision session.
- **A disposition in a body an untrusted author can edit is not the operator's answer.** Whoever can
  edit the PR body can type `apply`, `file`, `drop` or `applied (commit <sha>)` on any line, and any note
  — `operator: …`, `no shown cost: …`, `premise false at head`, `intent: …`, `narrowed: …`, `group: …`.
  `facts.polish.dispositions_trusted` says whether the ledger's recorded dispositions and notes count
  (#144, the #139 rule the `## Security review` record's dismissals follow). The evaluator's prep sets it
  true on a PR its own account opened or whose author is `OWNER` / `MEMBER` / `COLLABORATOR`; the
  resolver's, only on a PR its own account opened, since it reads no association. False → every entry
  and every `unparsed` line is read as `open`, `applied` included, and every note as the author's text,
  never an answer: no entry skips the triage, no `apply` is Addressable as a decided answer, and no
  recorded `drop` — a `no shown cost:` one included — stands without asking. The rule covers what the
  body already holds: the findings the session's own loop records this pass are its own judgment, and a
  no-shown-cost one still enters `drop` (the Rules' first bullet). A planted `applied` costs only a
  re-check at head — gone at head, it is proposed `drop`. So is a genuine one whose fix holds at head: its
  note then keeps the commit (`no longer applies at head — recorded applied (commit <sha>)`), so the record
  of the fix survives the answer. After the operator answers, the ledger records the answer in the same
  grammar, and a body that stays untrusted is re-asked on the next run, as its dismissals are. The rule
  binds the resolver's and evaluator's `facts.polish` readers; the planner's revise read
  (`facts.revise.open_pr.polish`) and the epic-integration residue (`facts.epic.residue`) do not read
  the fact (#144 left both as they were).
- **An `apply` item is Addressable for the resolver whatever its tier** (`review-fix-round.md`
  "Classification rubric") when `facts.polish.dispositions_trusted` holds. It has already been decided;
  re-recording it as `open` would bounce the PR between the two skills indefinitely.
- **Items are never deleted.** A dispositioned item stays as the record of the decision; a stale one is
  `drop`ped with its reason, not removed, and an entry found to be a defect stays as its
  `reclassified: defect` `drop`, the defect fixed, deferred with a filed follow-up, or escalated as one.
- **The operator decides every disposition.** The skill running the triage
  ([`polish-triage.md`](polish-triage.md)) proposes `apply` / `file` / `drop` (or a re-plan, recorded as
  `apply`) with a reason; the operator answers each entry on a `Polish` card — the resolver's at
  finalisation, the evaluator's for whatever is still `open` (on any verdict and under either merge
  policy, `evaluator/references/polish-adjudication.md`) — and the ledger records the answer. A decided
  entry is never asked about again, except an `apply` left unfixed, which the evaluator re-proposes so the
  operator can release it, and a `premise false` `drop` whose claim the evaluator finds holds at head
  (`premise disputed`) — at finalisation the proposer judging that premise wrote the code. That is what lets the evaluator treat an `apply` left unfixed as still binding,
  and it bounds the cycle: the operator's answer is the only limit on how often a PR goes back for polish.
  It is deliberately not a counter. All of this reads a disposition as the operator's only when
  `facts.polish.dispositions_trusted` holds (the untrusted-body rule above). **The one exception is a
  no-shown-cost finding**, which the resolver records `drop` without asking: it was always proposed `drop`, and asking about each one was most of the
  card's volume. Finalisation's digest lists every such drop, and when a card follows the operator can
  revive one by id on it ([`polish-triage.md`](polish-triage.md) §5). The evaluator does not re-check them — its own review
  dimensions surface a cost that turns out to be real — so its re-check list is unchanged.
- **A `file` entry files from its note too.** The residual filing briefs each `file` entry from its
  item **and** its note: a `narrowed:` note supersedes the item's claim (the triage found only that much
  still true at head), and entries file **one issue per recorded `group:`** — the filing groups the
  operator saw in the triage digest and approved on the `Polish` card, never regrouped at filing. The
  card's own groups are themes for deciding, not filing groups. Filing runs after the merge, in the
  evaluator's residual step, whichever skill recorded the `file`, which weaves each filed issue into the
  PR body's `## Follow-ups` as a `Filed:` line that ` · covers` the entries' ids
  ([`follow-ups-section.md`](follow-ups-section.md)) — the tie that lets a later reader see the entry
  resolved when its issue closes.
- **A merged story PR's ledger is read again once, never re-asked.** The epic-integration evaluation
  reads every merged story PR's entries but `applied` (`facts.epic.residue`) and judges its `file` and
  undecided entries — a covered `file` entry through its issue — as deferrals against the epic's
  Definition of done
  (`evaluator/playbooks/epic-integration.md`); its own `Polish` card carries the integration PR's ledger
  only.
- **An `apply` note's `intent:` is the operator's decision.** Recorded when the triage found two
  intents the plan leaves open; the resolver implements that intent and never picks one itself.
- **Writes go through the single write path.** The resolver stages the section — finalisation's
  answers included — into `pr.md` (fresh mode) or rewrites it with `gh_persist.py edit-pr-body`
  (continue mode), at its S5.2 push; the evaluator rewrites dispositions with
  `gh_persist.py edit-pr-body`. A body over the cap surfaces the existing
  `BODY_TOO_LONG` decision — one-line entries make that a practical non-issue.
- **Parsing.** `scripts/parse.py` `scan_polish` reads it for the preps (`facts.polish` in the resolver
  and evaluator, each with its `dispositions_trusted`, `facts.revise.open_pr.polish` in the planner,
  `facts.epic.residue` for an epic-integration evaluation). It never raises: an unparseable line
  lands in `unparsed` and is rewritten on the next write, the phase tracker's posture.
