# Design record and the `ui-prototype` block — shared contract

Two artifacts serve designing a user interface before it is built (#201): the **design record**, an issue
comment holding the design the operator agreed, and the **`ui-prototype` block**, the consuming repo's own
prose on how it prototypes. This file is the single source of truth for both shapes. One module reads
both, `scripts/design_record.py`; no stage parses the comment or the block itself.

## The design record

The record is one comment on the issue whose interface it designs. Its first line is the marker
`<!-- ux-design:v1 -->`, and a marker anywhere else in a body is quoted text, never a record. The lines
under it are fixed, so a script reads them and the operator reads the same text on GitHub:

```
<!-- ux-design:v1 -->
**Design record** — #<N> <title> — recorded <ISO-8601 UTC>

Prototype: <where the prototype lives, verbatim: a path, a branch, a URL — as the repo's ui-prototype block says>

## States
- `<state-id>` — <what the user sees and does in this state> — capture: <capture reference | (none)>

Approval: approved <YYYY-MM-DD> | pending
```

- **Header line.** The second line names the issue and when the record was written. It is informational:
  the reader neither parses nor checks it.
- **`Prototype:`** — where the prototype lives, verbatim. Exactly one such line, with a value. An adopted
  existing prototype (#227) read in place is named `` `<path>` — existing, read at `<branch>@<12-hex sha>` ``:
  free text like any other value, which the grammar does not parse.
- **`## States`** — one line per agreed state. `<state-id>` matches `[a-z0-9][a-z0-9-]*` and is unique in
  the record. The id ends at the first ` — ` after it, and the capture reference begins at the **last**
  ` — capture: `, so a description may carry an em dash of its own. The state lines are the non-blank lines
  between the heading and the `Approval:` line (to the end of the body when there is none). Every such
  line that does not match the grammar is a finding. Any list item anywhere else (`-`, `*`, `+` or numbered,
  as GitHub renders them all) is never a state, and it is a finding too: the record has no other list items, and one left below the `Approval:` line, well-formed
  or not, would otherwise sit outside an approved record unchecked.
- **A capture reference** is the text after ` — capture: `: a GitHub attachment URL, a Markdown image, or
  a path where the repo's `ui-prototype` block says captures are stored. Empty or the literal `(none)`
  means the state has no capture (`capture: None` in the reader's payload). The reader checks only that
  a reference is present, never that it resolves: it runs offline in a prep with no fetch. Uploading or
  storing each capture before approval is the writer's job.
- **`Approval:`** — `approved <YYYY-MM-DD>` once the operator approves the design, `pending` until then.
  Exactly one such line. Any other value is a finding.

The body is read with its line endings normalised and each line stripped of surrounding whitespace, so a
record edited on GitHub's web UI (stored with CRLF) reads the same as one the pipeline posted.

**What binds.** The record binds what the user experiences: flows, states (empty, loading and error
states included), copy and layout. It never binds the prototype's code structure: the build may reach
the agreed states any way the plan chooses.

**`approved` fails closed.** The record counts as approved only when all of these hold: the `Approval:`
line reads `approved <YYYY-MM-DD>` with a valid date, at least one state line parses, every state has a
capture, and the parse has no findings. A finding (a malformed or duplicated state line, a list item
outside `## States`, a missing, empty or duplicated `Prototype:` line, a missing or duplicated `Approval:`
line, an unrecognised `Approval:` value) makes it unapproved,
because an unreadable state line could hide a state with no capture, and a build is later checked
against every captured state.

**One record per issue.** A revision replaces the record: edited in place, or, when it attaches new
captures, posted anew and the old comment deleted afterwards (post before delete, so the issue is never
without a record). Two trusted records on one issue are an anomaly the reader reports as
`MARKER_AMBIGUOUS` rather than picking one.

**Only a trusted author's record counts.** Like every pipeline marked comment (`docs/architecture.md`
§12, #179), a record counts only when its author's `authorAssociation` is `OWNER`, `MEMBER` or
`COLLABORATOR`. An untrusted copy is skipped before duplicates are counted, so it can neither be the
record nor make it ambiguous, and the skip is one `MARKER_UNTRUSTED` notice naming only the marker, the
count and the issue.

## The `ui-prototype` block

The consuming repo declares how it prototypes in a `<!-- ui-prototype -->` marker block in its
`COMMANDS.md` or `CLAUDE.md`: where a prototype lives, what it is built with, how to run it, and how to
capture each state. The block is **user-owned** prose. It has no canonical form and the pipeline has no
built-in default: with no block, nothing is assumed about how this repo prototypes. Like every config
block, it is read from the working tree of the checkout the command runs in, committed or not
(`docs/architecture.md` §6 rule 1).

This contract names no prototyping tool, location or capture method. Those are the consuming repo's,
and the block carries them. Setup proposes the block for a repo with a user-facing interface, seeding it
when absent and re-ingesting the operator's edits when present
(`skills/setup/references/block-authoring.md`, `### ui-prototype`).

**Existing prototypes (#227).** The block may declare where the prototypes the repo already keeps live,
with one labelled line: after an optional list marker, `**Existing prototypes:**` (or `Existing
prototypes:`) followed by one or more backticked patterns, separated by commas, spaces or ` and `. Only that
leading run of backticked spans is read; prose after it, and every other line of the block, stays prose the
session reads — how a prototype is tied to its issue or spec, for one — and a backticked file named in that
prose is never a pattern. Each pattern is a git glob pathspec over **tracked** files (`*` and `?` within a
path segment, `**` across segments, `[...]` a set), listed from the index, so nothing outside it is walked. A
path in the index but not committed at `HEAD` (staged, never committed) reads `modified: true`, because `git diff
HEAD` reports it, so it is never adopted read-only; a tracked symlink whose real path leaves the checkout is
never listed or adopted. A pattern that is empty, has leading or trailing whitespace, starts with `/` or `:`,
has a `..` segment or contains a NUL is refused and reported in `findings`; a later second labelled line is a
finding and ignored. A label with no valid pattern lists nothing and is the `UI_PROTOTYPE_DECLARATION_MALFORMED`
notice; a failed git listing lists nothing and is the `UI_PROTOTYPE_LIST_UNAVAILABLE` notice. The script
lists and never matches: which listed file an issue is about is the designer session's judgment, from the
issue's text. With no declaration nothing is listed and the designer runs as it would without this item. The
block's prose sets no document's authority: whether a screen spec binds the design is the doc catalogue's
`authority` alone ([`doc-catalogue.md`](doc-catalogue.md)).

## Writer

The **designer** (`skills/designer/`, #204) is the record's one writer: it posts, replaces and approves
records, and no other stage does. It runs `scripts/design_record.py check` on every staged record before
writing, and it writes `Approval: approved` only in a write that carries no `--attach`, after every attached
capture has uploaded, so an approved record never holds a capture that did not upload (its spine's D8).

## Readers

`scripts/design_record.py` is the one read. Its functions are pure but `read_and_stage` (which writes the staged
body), `list_existing` and `existing_fact` (which read the checkout with `git`, #227) and `stage_captures` (which reads the comment and fetches its captures, #206); its CLI `check <body_path>`
runs `parse_record` over a staged record for the writer's pre-post check, and `stage` stages the captures for the
design check ([`design-check.md`](design-check.md)). `scripts/prep_setup.py` composes
`locate` and `read_block` for setup's re-ingest base (#203), `scripts/prep_designer.py` composes
`read_and_stage` (staging at its own `record.md`) and `read_block` (#204) and `existing_fact` (#227), and the planner, resolver, evaluator and review preps compose
`read_and_stage`, `read_block` and `design_fact` into `facts.design` (#205, #206), so every stage that reads a design
reads the same record, approval and citation:

- `parse_record(body)` — the record's grammar, shared by the reader and a writer's pre-post check:
  `prototype`, `states` (`[{id, description, capture}]` in body order), `approval` (`approved` |
  `pending` | `None`), `approved_on`, `approved` and `findings` (`[{line_number, raw_line, reason}]`, 1-based; both `None` for a
  label with no line at all).
- `read_record(thread, issue=None)` — the issue's one trusted record from an already-fetched thread, as
  `(record, notices, decision)`. The record carries `present`, `comment_id` (the REST id
  `gh_persist.py edit-comment` takes), `url` and `created_at` plus `parse_record`'s keys, with the same
  keys when absent.
- `locate(root)` — the block's raw discovery, `(found, interior_lines, absolute_source)`: `COMMANDS.md`,
  then `CLAUDE.md`, then one level of their `@`-includes, the first well-formed copy winning, an empty one
  included; an unreadable or non-UTF-8 candidate reads as `(False, [], None)`. `read_block` and setup's
  re-ingest base share it, so setup never bases on a copy the stages do not read.
- `read_block(root)` — the block as `{present, source, text}`; an absent, malformed or whitespace-only
  block reads as not present.
- `read_declaration(block_text)` (#227) — the block's `Existing prototypes:` line as `{declared, patterns,
  findings}`, pure; `findings` are `[{pattern, reason}]`, `pattern` `None` for a duplicate label or a label
  with no pattern.
- `list_existing(root, patterns)` (#227) — `(candidates, error)`: the tracked files the patterns fit, sorted,
  each `{path, modified}` (`modified` when the working tree or index differs from `HEAD`, a staged-only file
  included), from one `git ls-files` and
  one `git diff`; a failed call is `([], <its stderr line>)`, and no pattern lists nothing without calling git.
- `existing_fact(root, block, adopt=None, no_adopt=False)` (#227) — `(fact, notices, attention)` composing the
  two with an `--adopt` path's validation (exactly one tracked file at the checkout); `prep_designer.py` encodes
  its `candidates` as the `prototype_candidates` spill section and fills the adopted path's `branch`, `sha` and
  `published` from the asserted workspace, as `facts.existing`.
- `check <body_path>` — the CLI over `parse_record`: one `ok` envelope whose payload is `parse_record`'s
  dict, whatever it finds, so a writer fixes a finding in the staged body before posting and a record
  posts as approved only when `approved` is `true`; an unreadable path is exit 2.
- `read_and_stage(thread, scratch_dir, issue, notices, filename="design-record.md")` — `read_record` plus `body_path`, the trusted record's
  body staged at `<scratch>/<filename>` (removed when no record stands; the designer passes `record.md`), as `(record, decision)` (#206; moved
  from the planner's prep).
- `cited_target(plan_text)` / `cited_check(plan_text)` — the plan's `## UI decisions` citation of the record
  (`- Design target: <url> · fingerprint <12 hex> · approved <date>`, #205) and the phase its build is checked at
  (`- Build check: phase <N>`, #206), each the first such bullet in that section, `None` when absent or malformed;
  a `Build check:` line present but malformed holds the check (`design_fact`), never reading as no line.
- `citation(record, plan_target)` — how the plan's citation stands: `none` / `missing` / `unapproved` /
  `current` / `revised` (`CITATION_STATES`).
- `design_fact(record, block, plan_body)` — `facts.design`: `{record, block, plan_target, citation, check_phase,
  check}`, `check` being `none` (no citation), `run` (`current`) or `hold` (otherwise, or a `## Phases` section or `Build check:` line that
  cannot be read). The build check's procedure is [`design-check.md`](design-check.md).
- `stage <owner/repo> --comment-id <id> --record <body_path> --root <checkout> --out <dir>` — the CLI over
  `stage_captures` (#206): each state's capture staged at `<dir>/<state-id>.<ext>`, a path reference copied from
  inside the checkout, a URL or image reference fetched from the record comment's rendered `body_html` (one
  `gh api` read; only `https` on `ALLOWED_CAPTURE_HOSTS`, every redirect hop and the final URL re-checked, at most
  `CAPTURE_MAX_BYTES`). Each state it cannot stage is one `DESIGN_CAPTURE_UNAVAILABLE` notice, never a hard
  exit.
