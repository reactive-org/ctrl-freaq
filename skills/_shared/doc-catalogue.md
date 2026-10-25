# Doc catalogue — shared reference

The consuming repo's **`<!-- doc-catalogue -->` block**: the external contract
([prd.md §7](../../docs/prd.md) "Config marker blocks") by which a repo declares **which documents
ground pipeline work and how much authority each carries**. A repo names its own docs; the plugin
never assumes a doc layout.

**Scope of this file.** The block's home, its format and per-field semantics, who may change it and
how, and what an absent catalogue means to each reader. The *read mechanics* — candidate discovery,
the best-effort line scan, the `DOC_CATALOGUE_ABSENT` notice — and the write-side check belong to
`scripts/doc_catalogue.py`; `setup`'s seeding flow belongs to
`skills/setup/references/block-authoring.md`. This file does not restate either.

## Home

The block lives in the consuming repo's **`docs/README.md`**, always — the fixed-home rule
`claude-code-stack-profile` also has (it always lives in `CLAUDE.md`). Two reasons it is `docs/README.md`
and not a plugin-owned file: a catalogue of documents belongs beside the documents, where a human
maintaining the docs will see it drift; and `docs/README.md` is a file a repo maintains for its own
sake, so the catalogue rides along on upkeep that already happens. A plugin-only file rots unread.

## The block a consuming repo declares

```markdown
<!-- doc-catalogue -->
- `<path>` — <role> — <binding | informative> — <one-line summary>
<!-- /doc-catalogue -->
```

One Markdown list item per document or document set, in the repo's own preferred reading order. The
shape rhymes with the command-list blocks (`issue-resolver-fast-checks`, `worktree-setup`) —
backtick-quoted value, then ` — `, then fields — but carries three fields instead of one description.

## Entry grammar

Each entry is **one line**; a reader strips the leading `- ` and splits the remainder on ` — `
(space, em dash, space) into **at most four** parts:

| Field | Form | Meaning |
| --- | --- | --- |
| `path` | the line's first backtick-quoted span, repo-root-relative | the document, or a directory naming a document set |
| `role` | short lowercase slug | what kind of document this is |
| `authority` | `binding` or `informative` — the closed pair | how a reader must treat a conflict |
| `summary` | free text, one line | what is in it, so a reader can judge relevance unopened |

**`authority` is the load-bearing field.** `binding` means a session must not contradict the
document: a conflict is a **blocker**, raised rather than designed around. `informative` means
context — read it, cite it, but a tension with it is a judgment call, not a stop. A repo that marks
everything `binding` gets a pipeline that stops constantly; one that marks nothing `binding` gets no
safeguards at all. Choose per document.

**`role` is an open set** — a reader recognizes `prd`, `architecture`, `constitution`, `ui-design`,
`conventions`, `guide`, and treats any other slug as an unrecognized kind whose `summary` and
`authority` still apply in full. Roles are a routing hint for the reader ("which doc answers *what*
the architecture is"), never a gate: no reader refuses to work because a role it wanted is missing.

Splitting on **at most** four parts is what lets a summary contain its own em dashes. A line that
yields fewer than four parts, or an `authority` outside the closed pair, is **skipped** by readers
(best-effort, the posture every block scan in the plugin shares) — which is why every writer runs
the check before it commits ("Keeping it current"): a silently skipped entry is a document that
silently stops grounding anything.

## Directory entries

A `path` naming a directory declares a **document set**: the documents directly inside it, all under
the one `role`, `authority`, and `summary` the line carries — an architecture hub's spokes, a folder of
per-feature specs. Write it with a trailing `/` so a human reads it as a set; readers resolve file
versus directory from the checkout, not from the spelling. The set is **one level**: a nested
directory that grounds work is declared as its own entry. When members need different authority,
declare them one per line instead.

A reader treats the set as the documents it lists — the read mechanics report them as the entry's
members — reads the ones the work at hand touches, and **cites the member file**, never the directory.
Listing a directory the repo declared is not the walk the absent-catalogue rule forbids: the repo
nominated it, and nothing outside it is searched.

## Handing the catalogue to a sub-agent

A context-blind sub-agent that checks work against the docs (the plan reviewer, the resolver's fitness
audit, the drafter's issue reviewer, the question-resolver's constraint audit) receives the entries as a
`<<catalogue_entries>>` placeholder, filled from the prep's `grounding_docs` fact: one line per entry as
`<path> — <role> — <binding|informative> — <summary>`, a directory entry's `members` following it as
indented `- <path>` lines, and an entry whose `present` is false **omitted** (prep already raised it as
attention; a sub-agent handed a dead path would report the absence as a finding about the work). An empty
placeholder means the repo declares no grounding docs, and the sub-agent says so rather than inventing a
path. Each prompt restates this shape at its input, because the sub-agent never reads this file.

## Ownership — kept current by any session

The catalogue is **session-maintained**: it belongs to the repo, and any session whose work changes
the docs keeps it current — a pipeline skill, an ordinary session, or a human editing by hand. Until
#123 it was user-owned with `setup` as its sole writer, and in practice it drifted between `setup`
runs: a new guide went unlisted, a renamed file left a dead path, a rewritten section kept a summary
that no longer described it. `setup` still **seeds** it when absent and, on re-run, proposes
refinements against the existing interior; it is one writer among several. The planner, drafter,
slicer, requirements-gatherer, resolver, evaluator, and question-resolver preps read it.

A writer edits freely, without asking:

- **add** an entry for a document that should ground pipeline work, **remove** the entry of a
  document that no longer exists, and **fix** a path that moved;
- **update a summary** whenever it no longer describes the document — the summary is how a reader
  judges relevance unopened, so a stale one misleads. A summary a human wrote is reworded like any
  other once it stops being true; and
- **change a role** to match what the document has become.

**Anything that stops or starts a document grounding the work is the operator's call.** `binding`
makes a conflict a blocker, and the evaluator reads the catalogue at the PR head, so a change that
downgrades a binding entry, removes it, or repoints its path would weaken the very check judging that
change. Adding a `binding` entry adds a gate. So these need the operator's confirmation, always on a
card and never assumed: adding an entry as `binding`, changing any entry's `authority`, and removing
or repointing a `binding` entry. A repointed entry is a removal plus an addition to every reader, and
the check reports it that way.

The ownership posture differs from `claude-code-stack-profile`'s, which stays user-owned (nothing
parses that block, so `setup` only seeds and refreshes it). This block **is** machine-parsed, so every
line must hold the one-line grammar above, and the check enforces that before a write lands.

## Keeping it current

A session whose change adds, moves, removes, or materially rewrites a document in the catalogue's
scope updates the catalogue **in the same change**, so a PR that adds a document and its entry is
judged with both (the vantage rule in "Where the catalogue is read from"). Scope is the documents the
catalogue lists, plus any new one that should ground pipeline work — a requirement, a design rule, a
convention a reviewer would cite — not every Markdown file the change touches.

Before committing a catalogue edit, run the check from the checkout being committed:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/doc_catalogue.py check --root "<checkout>"
```

It reads `docs/README.md` in that checkout's working tree and measures it against the block at the
merge-base of `HEAD` and `origin/<default branch>` (`--base-ref <ref>` overrides; a story branch passes
its epic branch), so `confirm` lists what this branch changed — never an entry the base gained or
dropped after the branch point (`base_sha` names the commit compared against):

- **`needs_decision` — `DOC_CATALOGUE_MALFORMED`**: duplicate or unterminated markers, an interior
  line readers would skip (`malformed_lines`, each with its file line and reason), a repeated path,
  or an entry naming a path absent from the checkout (`missing_paths`). Fix the block and re-run; never
  commit it as it stands, since every reader would silently drop those entries.
- **`ok` with `confirm`**: the list of changes that stop or start a document grounding the work
  (`binding_added`, `binding_removed`, `authority_changed`). Empty → land it. Non-empty → put the list
  to the operator on one consent card before committing, and revert any change they decline.
  `confirm: null` with a `DOC_CATALOGUE_BASE_UNAVAILABLE` notice means the base could not be read: ask
  about the authority edits this session made itself.

Catalogue edits are tracked-file edits, so they land the way the writing session lands every other
one — in its work workspace, through an operator-gated PR ([prd.md §8.2](../../docs/prd.md),
[architecture.md §7](../../docs/architecture.md)). A writer that cannot run the check — an ordinary
session in the consuming repo, or a human — follows the same rules from the guidance `setup` writes
into that repo's `CLAUDE.md`, and the **evaluator** re-runs the check at the PR head as the backstop:
a line readers would skip becomes a finding, and every `confirm` entry is put to the operator before
approval.

## When the catalogue is absent

An absent `docs/README.md`, an absent block, or a malformed one (duplicate or unterminated markers —
treated exactly like absent) all mean the same thing: **there is no declared grounding set.** No
reader walks the filesystem hunting for documents, and no reader falls back to a built-in list of
paths — a guessed doc layout is what this block exists to remove. Readers emit the
`DOC_CATALOGUE_ABSENT` notice so the gap is loud rather than silent, and the consequence differs by
consumer:

- **Planner, drafter, resolver, evaluator, question-resolver** — proceed **ungrounded**, exactly as
  they did when a hardcoded path happened not to exist. The absence is recorded, not fatal. Each of
  these judges work whose substance comes from somewhere else — an issue, a diff, an operator's
  decision — and uses the docs to check it; the question-resolver's constraint audit falls back to the
  docs the question itself cites.
- **A consumer whose whole output is derived from documents** — refuse, and say what would fix it.
  Decomposing or specifying against nothing invents scope, and invented scope becomes real issues.

That asymmetry is deliberate: one identical fact, two correct responses. Do not "fix" a refusing
consumer to match a proceeding one.

## Where the catalogue is read from

The catalogue is read from **the same vantage as the documents it names** — the planner's asserted
grounding checkout at its `plan_ref`; the resolver's audit view (its audit read workspace, else its
work worktree — reported as `grounding_path`); the evaluator's PR-head worktree, so a PR that edits a
doc or its entry is judged against its own copy; the drafter's, slicer's, requirements-gatherer's, and
question-resolver's ambient checkout. A branch that adds a document
*and* its catalogue entry must ground on both, and a branch that has not yet merged its entry must
not. (Through v3 this was a deliberate exception: gate config was pinned to `origin/main` so a PR
could not weaken the gates judging it, and the catalogue was called out as *grounding* config that
names no gate. That pin is retired — every config family now reads at the working vantage
([architecture.md §6](../../docs/architecture.md)) — so this is the ordinary rule, not an exception.)
