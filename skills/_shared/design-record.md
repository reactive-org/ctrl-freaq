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
- **`Prototype:`** — where the prototype lives, verbatim. Exactly one such line, with a value.
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
and the block carries them.

## Readers

`scripts/design_record.py` is the one read. It is import-only and pure; the preps of the stages that read
a design compose it in-process once those stages land (#204–#206):

- `parse_record(body)` — the record's grammar, shared by the reader and a writer's pre-post check:
  `prototype`, `states` (`[{id, description, capture}]` in body order), `approval` (`approved` |
  `pending` | `None`), `approved_on`, `approved` and `findings` (`[{line_number, raw_line, reason}]`, 1-based; both `None` for a
  label with no line at all).
- `read_record(thread, issue=None)` — the issue's one trusted record from an already-fetched thread, as
  `(record, notices, decision)`. The record carries `present`, `comment_id` (the REST id
  `gh_persist.py edit-comment` takes), `url` and `created_at` plus `parse_record`'s keys, with the same
  keys when absent.
- `read_block(root)` — the block as `{present, source, text}`; an absent, malformed or whitespace-only
  block reads as not present.
