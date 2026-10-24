# Review-loop record — shared contract

The **review-loop record** is the fixed-shape part of the loop comment the resolver posts on a PR at each
phase's push: one line per finding the loop classified, and one line per loop pass counting its
`code-review` runs. It is the single source of truth for that shape. The writer is the **resolver**:
`resolver/references/review-fix-round.md` step 8 stages the lines, and `resolve-spine.md` S5.2 posts them.
The readers are **`review-report`**, through `parse.scan_review_loop` and `scripts/review_report.py`,
and #99's **`setup`** proposals, which compose that core in-process.

## Why it exists

Evidence for the loop's design, such as the 503-issue audit behind the rubric's provenance axis and the
retros behind its fix disciplines, used to be gathered once, by hand, from prose. A loop comment recorded
each item's provenance and tier in a prose parenthetical and never recorded its bucket, so no script
could say which findings were fixed, which were wrong, which the plan intended, and which did not matter.
The record fixes the facts on one line per finding. Judgment stays with the model, which picks the
bucket, the tier and the class key at classification. The script only counts (CLAUDE.md "facts by
script, meaning by model"). The loop comment is not a frozen artifact (`docs/prd.md` §7), so this format
is a story's change, not a contract break.

## Format

The first line of **every** loop comment the resolver posts is the marker `<!-- review-loop:v1 -->`, and
that includes each per-round comment a `BODY_TOO_LONG` split produces. The marker tells a reader that
the comment carries record lines. A comment without it is never read for them, so a human quoting a
finding line elsewhere is not counted. Under the marker, the comment uses two pinned headings:
`## Review loop — phase <N>`, then `### Round <k>` for each staged round section.

```
<!-- review-loop:v1 -->
## Review loop — phase 1

### Round 1
Defect count: 2
- R1.1 · addressable · introduced · defect · fixed · review · class: stale-read-after-write · site: scripts/importer.py — the cache is read before the write lands: the read now follows the write
- R1.2 · addressable · introduced · polish · ledger P1.1 · review · class: misleading-name · site: app/helpers/date_helper.rb — `format_short` returns the long form
- R1.3 · refuted · pre-existing · defect · settled · review · class: missing-nil-guard · site: Sources/Sync/SyncClient.swift · check: upheld — a nil token crashes `refresh`: the guard at its call site makes it unreachable
Defect fixes: phase 1 @ 3f9c2ab — scripts/importer.py:load_rows

### Round 2
Defect count: 0
- R1.4 · plan-settled · introduced · polish · settled · cold-read · class: config-read-vantage · site: (none) — config is read from the working tree, not a pinned ref

Settled (not addressed):
- refuted — a nil token crashes `refresh` — evidence: guard read at the call site — check: upheld
- plan-settled — config is read from the working tree — cites: ## Architecture decisions "<bullet>"
Review runs: phase 1 · 3 · settled
```

(The example's paths mix Python, Ruby and Swift on purpose: the format is stack-neutral.)

### The finding line

```
- R<phase>.<seq> · <bucket> · <provenance> · <tier> · <action> · <source> · class: <key> · site: <path>[ · check: <verdict>] — <item>
```

One line per classified item, written in the section of the round that classified it. The fields are
separated by ` · ` and end at the first ` — `. Everything after that is the item, so the item may itself
contain ` · ` or ` — `.

- **`R<phase>.<seq>`** is a stable id, minted at classification. It mirrors the ledger's `P<phase>.<seq>`:
  `<phase>` follows the ledger's rule (`polish-ledger.md` "Format"), so a revision run records under the
  last `kind: code-shipping` phase and an epic integration PR records under `1`. `<seq>` continues from
  the highest `R` id the PR's loop comments already carry for that phase. An id is never reused or
  renumbered. A line repeating an id already seen is unparsed.
- **`<bucket>`** is the item's **final** bucket, after the dismissal check (table below).
- **`<provenance>`** is `introduced`, `adjacent` or `pre-existing`.
- **`<tier>`** is `defect` or `polish`. For an Addressable item it is the tier. For every other bucket it
  is the rubric's **claim tier**: the claim read as if true. So the field is never empty.
- **`<action>`** records where the item ended this round, after any card's answer (table below).
- **`<source>`** is where the finding came from (table below).
- **`class: <key>`** is a kebab-case slug, `[a-z0-9]+(-[a-z0-9]+)*`, one to five words. It names the
  failure class, not the site (`stale-read-after-write`, `doc-overclaims-code`; never a path or a
  symbol). The model chooses it at classification and reuses it for the same class within the PR.
  Recurrence is judged by the model: the report counts exact keys, and whether two keys describe the same
  finding coming back is #99's judgment. A closed enum would put meaning in the script and could not fit
  every stack.
- **`site: <path>`** is one repo-relative file, never `path:line` (the ledger's durable-anchor rule). It
  is the first file the finding names. A class finding's other sites stay in the item text. Write
  `site: (none)` when the finding names no file.
- **`check: <verdict>`** is present only when step 4's dismissal check returned a verdict on this item. It
  is absent otherwise, including when the check returned none; the item text says so instead.
- **`— <item>`** is the finding in one line, then optionally `: <what changed>`. It is free text to the
  end of the line.

**No line** is written for:

- an `apply` ledger item being fixed — its ledger entry's `applied` is its record;
- a security-pass finding — its record is the PR body's `## Security review`;
- a verbatim repeat that step 3 re-settles silently — its `Settled (not addressed):` entry carries the
  repeat count.

A repeat that brings new evidence is classified fresh and gets a new line.

The loop comment's other fixed lines keep their shapes, and the record adds to them, never replaces
them. The resume hint seeds from `Settled (not addressed):`, `Defect fixes:` and `Cold read:`.
`Security review:` and `Finalisation:` summary lines stay as they are. The reader skips those two,
because the PR body's `## Security review` and `## Polish` sections hold what they summarise.

### The run-count line

```
Review runs: phase <N> · <count> · <exit>
```

This is the **last line** of each loop pass's comment, staged at S5.2 before the post. `<count>` is every
`code-review` invocation the pass made: S5.1's rounds, its light re-reviews, the run after the cold read,
and finalisation's light re-review. The last of these is excluded from S5.1's emergency ceiling, but it
counts here. The line has to be explicit, because a round that settles with nothing to record stages no
section, so counting `### Round` headings would undercount. A phase can have several passes, for example
a revision run, and each pass writes its own line.

## Closed sets

### Buckets

| Bucket | Rubric bucket (`review-fix-round.md` "Classification rubric") |
|---|---|
| `addressable` | Addressable |
| `pre-existing` | Pre-existing |
| `no-shown-cost` | No shown cost |
| `explicitly-deferred` | Explicitly-deferred |
| `decision-required` | Decision-required |
| `grounding-violation` | Grounding-violation |
| `plan-settled` | Plan-settled |
| `refuted` | Refuted |
| `deferred-by-plan` | Deferred-by-plan |

### Actions

| Action | Meaning |
|---|---|
| `fixed` | this round's edit addressed it — a card answered with a fix included |
| `ledger P<phase>.<seq>` | recorded for the `## Polish` ledger under that id, minted here at step 8 and written unchanged at S5.2; finalisation reads the id, never re-mints it |
| `settled` | it stands Plan-settled, Refuted or Deferred-by-plan, including an item a `Decision` card's "as it stands" answer settled and marked `kept` |
| `deferred` | Explicitly-deferred, or a defect a continuing answer (**Accept current**, **Accept + defer**) left unfixed |
| `decision` | only when its card's answer ended the run with the item unresolved: **Re-plan**, **Restructure**, **Abort** or **Abort loop** |

### Sources

| Source | Meaning |
|---|---|
| `review` | a `code-review` verdict, light re-reviews included |
| `cold-read` | the cold-read audit's findings |
| `evaluator` | the evaluator's soft-reject review, folded in on iteration 1 |
| `human` | any other PR comment or review folded in on iteration 1 |

### Check verdicts

| Verdict | Meaning |
|---|---|
| `upheld` | the dismissal check upheld the dismissal |
| `overturned` | the dismissal check overturned it; the line records the bucket it moved to |

### Exits

| Exit | Meaning |
|---|---|
| `settled` | the loop settled |
| `accepted` | **Accept current** |
| `re-plan` | **Re-plan** |
| `restructure` | **Restructure** |
| `abort` | **Abort** or **Abort loop** |

## Outcomes

The report sorts every finding into one of four outcomes: `fixed`, `wrong`, `intentional`,
`did-not-matter`. Everything else is excluded under a named category. The outcome is a total function of
the line's action, then its bucket, then, for a ledgered item, its ledger entry. The rules are evaluated
in order and the first match wins:

| # | When | Outcome |
|---|---|---|
| 1 | action `fixed` | `fixed` |
| 2 | action `deferred` | excluded `deferred` |
| 3 | action `decision` | excluded `decision` |
| 4a | action `ledger P…`, entry `applied` | `fixed` |
| 4b | action `ledger P…`, entry `drop` whose note leads `reclassified: defect` | excluded `reclassified` |
| 4c | action `ledger P…`, any other entry `drop` | `did-not-matter` |
| 4d | action `ledger P…`, entry `file` | excluded `deferred` |
| 4e | action `ledger P…`, entry `open` or `apply` | excluded `pending` |
| 4f | action `ledger P…`, no entry with that id | excluded `unlinked` |
| 5a | action `settled`, bucket `refuted` | `wrong` |
| 5b | action `settled`, bucket `plan-settled` | `intentional` |
| 5c | action `settled`, bucket `deferred-by-plan` | `intentional` |
| 6 | anything else | excluded `unmapped` |

A ledgered item's outcome is read from the ledger because the ledger holds the operator's later answer. A
`no shown cost` drop that the operator revived reads `applied`, not `drop`, and a `no-shown-cost` item
reaches did-not-matter through its `drop`. Deferred-by-plan is `intentional` because the code under review
is what the plan intends at that phase. Mapping it to `deferred` would file a phase's planned future work
beside real follow-ups. An overturned check needs no rule of its own: the line already records the item's
final bucket and action. A `reclassified: defect` drop is excluded because the item's later record, or the
evaluator's review, carries what happened to it.

**Ledger-only records.** A `## Polish` entry that no record line names becomes a record of its own. Its
bucket is `no-shown-cost` when its note leads `no shown cost:`, else none, and its site is the anchor's
path. It enters at rule 4 with its own disposition. This is how a legacy PR's ledger, and the `apply`
items that get no line, reach the outcome table exactly once.

**Security is counted beside, not in.** The `## Security review` record (`security-review.md`) is a
separate pass, not the code reviewer. Mixing it into the outcome table would misstate the reviewer's
precision, and a `dismissed` entry is distinguished only by the operator's prose reason, so splitting it
into wrong, intentional or didn't-matter would put meaning in the script. The report counts it in its own
tally: PRs carrying the record, entries by disposition, and unparsed lines.

## Legacy loop comments

Every repo the report meets first holds only loop comments written before this format, so the reader
takes them leniently, into a tally labelled **`legacy`** that is never pooled with the record tally. A
comment without the marker is a legacy loop comment when it carries a `## Review loop — phase <N>`
heading, or a `Settled (not addressed):`, `Defect fixes: phase` or `Cold read: phase` line. (A comment
whose rounds only fixed polish carries just the heading.) From such a comment the reader takes only the
shapes step 8 already fixed:

- each `Settled (not addressed):` entry: the bucket from its lead word, `check: upheld` from its suffix,
  deduplicated per PR on bucket and item text;
- each `Defect fixes:` site: bucket `addressable`, tier `defect`, action `fixed`, `site` being the part
  before `:`;
- the count of `### Round` headings under each phase heading (rounds before any phase heading count
  under phase `None`).

Its prose item lines are never parsed. Their parentheticals had no pinned shape, so parsing them would be
guessing.

Every summary figure is reported per **basis**, `record` and `legacy`, never pooled. A record line takes
the `record` basis and a legacy entry the `legacy` basis. A ledger-only record takes the basis of its own
(PR, phase), the phase being the one in its `P<phase>` id: `record` when that pair has a record-format
loop comment, else `legacy`. So on a PR whose early phases predate this format, those phases' ledger
entries stay `legacy`.

## Rounds-to-pass

Rounds are counted per (PR, phase), summing every pass's `Review runs:` count for that pair. The phase
**passed** when its last pass, in comment order, exited `settled`. The record basis reports the median,
mean, maximum and a histogram over passed phases, plus the **five-or-more share** over every phase that
has a `Review runs:` line, passed or not. A revision run's passes land on the last code phase's pair, so a
phase that needed a revision counts the runs the revision took. The legacy basis records no exit, so it
has no pass/fail split. It reports the same figures over every legacy (PR, phase), each pair's `### Round`
count summed across that PR's legacy comments. The legacy figure is a floor, because a round that staged
nothing wrote no heading.
