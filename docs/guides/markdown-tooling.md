# Guide: Markdown tooling and merge-safe doc conventions

One of the authoring guides for a **consuming repo's** docs. The other guides say *what* each doc
holds. This one covers keeping those docs **well formed, cheap for an agent to read, and free of
merge conflicts** when parallel branches edit them. It uses
[mdsmith](https://github.com/jeduden/mdsmith) as the linter. Facts and rule IDs below were checked
against mdsmith v0.57.0, released 2026-10-03; check the rule docs when you pin a newer version.

The conventions (slug IDs, lists for growing content, headings as the index) stand on their own.
mdsmith is what enforces them and keeps the generated parts in sync.

## Install and run

Pin the version so every machine and CI run lints the same way. With
[mise](https://mise.jdx.dev), in `mise.toml`:

```toml
[tools]
"github:jeduden/mdsmith" = "0.57.0"
```

`mise use github:jeduden/mdsmith@0.57.0` writes that entry. Other documented routes, if your repo
doesn't use mise:

- Go: `go install github.com/jeduden/mdsmith/cmd/mdsmith@v0.57.0`
- Homebrew: `brew install jeduden/mdsmith/mdsmith`
- npm: `npx @mdsmith/cli` (the unscoped `mdsmith` npm package is someone else's)
- Python: `pipx install mdsmith` or `uvx mdsmith`
- A release binary, checked against the release's `checksums.txt`

Run it:

- `mdsmith check` is read-only. Exit `0` is clean, `1` means lint issues, `2` a runtime or config
  error.
- `mdsmith fix` applies every fixable rule and regenerates generated sections. It exits `1` if
  issues remain that it can't fix.

With no file arguments both lint the `files:` globs from `.mdsmith.yml` (default every `*.md`). In
CI, run `mdsmith check` on every PR, through the pinned tool or the GitHub Action
(`uses: jeduden/mdsmith@v0.57.0` with `args: check .`, and its `version` input set to the same
release: it defaults to `latest`, whatever ref the action is used at). Add
`mdsmith merge-driver ci-install` (see [the merge driver](#the-merge-driver)) so a stale
`.gitattributes` fails CI too.

## A recommended `.mdsmith.yml`

`mdsmith init` writes a config listing every rule at its default. Keep only your deviations from
the defaults, so the file states your choices and a reader sees what's different. The shape:
**strict on link integrity, IDs and front matter; mdsmith's defaults for size; explicit,
commented exceptions.**

```yaml
convention: github            # GFM, dash bullets, asterisk bold, underscore italic

ignore:
  - "docs/specs/**"           # frozen historical records: never edited, never linted

rules:
  # IDs: no numbered requirement or section IDs (see "Slug IDs")
  occurrence:
    pattern: '\b[RACU]-?[0-9]+\b'
    max: 0
  # Front matter: every doc carries a one-line summary for the catalog
  required-frontmatter:
    fields: [summary]
    include: ["docs/**"]
  # Size: cap sections, not just files (file limits stay at the defaults)
  max-section-length:
    per-level: {2: 120, 3: 60}
  # Prose and links (opt-in rules)
  paragraph-structure: true
  descriptive-link-text: true
  git-hook-sync: true
```

Why each setting:

- **`convention: github`** pins list, emphasis and flavor styles in one line, so two authors
  never fight over `*` vs `-`.
- **Link integrity stays on.** `cross-file-reference-integrity` (MDS027) is on by default and
  catches a link to a moved or renamed doc. Don't turn it off; a broken link in a grounding doc
  is a citation that no longer resolves.
- **`occurrence`** (MDS060) with `max: 0` rejects numeric IDs in prose and list items; see
  [Slug IDs](#slug-ids).
- **`required-frontmatter`** (MDS071) makes `summary` mandatory, which feeds the
  [catalog](#summary-front-matter-and-the-catalog).
- **Size limits: mdsmith's defaults.** `max-file-length` (MDS022, 300 lines) and `token-budget`
  (MDS028, 8000 tokens) are on by default. Aim for them; see [Size](#size-aim-for-the-defaults).
- **`max-section-length`** (MDS036) is off by default. Turn it on with per-level limits, because a
  long section hurts an agent more than a long file with good headings.
- **Prose limits.** `paragraph-readability` (MDS023, ARI 14) is on by default.
  `paragraph-structure` (MDS024) is opt-in and caps sentences per paragraph and words per
  sentence. Short paragraphs keep one claim per paragraph, which is what a citation needs.
- **LLM-tell word lists.** `mdsmith init --add wordlists` writes word lists under
  `.mdsmith/wordlists/`. It doesn't edit `.mdsmith.yml`: wire the lists into `forbidden-text`
  (MDS056) and `forbidden-paragraph-starts` (MDS055) the way mdsmith's `init` docs show.
- **`descriptive-link-text`** (MDS063) rejects "click here" links, so a link says where it goes.
- **`git-hook-sync`** (MDS048) flags a merge-driver setup that has drifted from `ignore:`.
- **Line length** (MDS001) is on by default at 80. Keep your repo's existing width if it has one;
  `reflow: true` makes it fixable for prose paragraphs.

### Exceptions and existing repos

- **Make exceptions explicit.** A doc that legitimately runs long gets a per-glob budget with a
  comment saying why: `token-budget: {budgets: [{glob: "docs/architecture/**", max: 12000}]}`, or an
  `overrides:` entry (`overrides: [{glob: ["CHANGELOG.md"], rules: {...}}]`; later entries win).
- **Exclude frozen records outright** with `ignore:`. Specs, journals and parity records are
  history; linting them only invites edits nobody should make.
- **Tighten an existing repo step by step.** Start with link integrity and the ID guard, which are
  cheap. Add the size and section limits as exceptions with a budget each, then lower the budgets
  as docs are split. Don't split every doc in one PR.

## Size: aim for the defaults

`max-file-length` 300 and `token-budget` 8000 are the target, not a stricter limit. A doc within
them gives you:

- An agent reads the whole file in one go: no offsets, follow-up reads or truncation.
- A lookup costs less context.
- A sub-agent can be handed the whole file rather than one section of it.
- Fewer merge conflicts: small files with one topic each are touched by parallel branches less
  often.
- The one-line `summary` matches the contents, so the catalog is more useful.
- A diff to a small doc is easier to review.

## Slug IDs

Every section a pipeline skill may cite carries a stable ID in its heading. The prefix marks which
doc the section is in, not what kind of section it is:

- `R-` for `docs/prd.md` (`## R-dry-run-default: Dry run is the default`, `## R-scope: Scope`)
- `A-` for `docs/architecture.md` (`## A-background-work: Background work`)
- `C-` for `docs/constitution.md` (`## C-secrets: Secrets`, over a one-line rule)
- `U-` for `docs/ui-design.md` (`## U-section-card: SectionCard`)
- `docs/architecture-notes.md` reuses the `A-` ID of the rule it explains
  (`## A-background-work: Why Solid Queue and not Sidekiq?`)

A citation is `<doc> §<ID>`: `PRD §R-dry-run-default`, `constitution §C-secrets`. Finding it is
one grep: `grep -n 'R-dry-run-default' docs/prd.md` returns one line number, and the agent reads
that section only. `grep -rn 'A-background-work' docs/` returns a rule and its rationale together.

**Why slugs, not numbers.** Two branches that each add "§12" or "R22" collide, and renumbering
dangles every citation already posted in issues, plans and reviews. Two branches that add
`R-csv-export` and `R-pdf-export` don't collide, and sections can be reordered freely. The one rule
is: **retitle a section freely, but never rename or remove a cited ID.**

**The lint guard.** The `occurrence` rule above (`pattern: '\b[RACU]-?[0-9]+\b'`, `max: 0`) rejects
a numeric ID written in prose or a list item: a citation such as "per PRD §R12", or a requirement
list that numbers its entries. It can't see headings: `occurrence` skips heading text, code spans,
code blocks and tables, so a numbered heading (`## R12: …`) passes and stays a reviewer's catch.
For the same reason, write IDs in prose as plain text (R-dry-run-default), not in backticks, so
the guard sees them. The pattern also fires on ordinary terms shaped like an ID (R2, C4, the A4
paper size, C99): put those in a code span, or narrow the pattern for your repo. Register IDs such
as `PRD-OQ-05` don't match it and stay as they are.

**Citations are grep keys, not URL links.** GitHub builds a heading's link anchor from the whole
heading text (`#r-dry-run-default-dry-run-is-the-default`), so a link to it breaks when the title
changes. Cite by ID; link to the file, not the heading.

**Requirement IDs on issues.** The `requirements-gatherer` mints its own `REQ-<issue>-<seq>` IDs
on an issue's Definition of done. They don't replace PRD IDs: a DoD criterion cites the PRD
section it comes from by its `R-` ID, as its source.

### Migrating a repo that uses numbered sections

Don't renumber. Add the ID next to the number:

```markdown
## 4. R-entries-immutable: Entries are immutable
```

Old `PRD §4` citations and new `PRD §R-entries-immutable` citations then both resolve. New
sections get an ID and no number. Dropping the numbers later is a deliberate change of its own,
made once nothing cites them; a review or a lint fix never strips them.

## Lists, not tables, for growing content

Use a list for content that grows a row at a time: requirements, decisions, a register of open
questions. mdsmith's `table-format` rule (MDS025, on by default and fixable) aligns every column,
so a new row with a wider cell re-pads the whole table. Two branches that each add a row then
conflict on every line. A list item is one line per entry, and two additions merge cleanly.

Tables are fine for small, stable content, such as a fixed comparison.

## Structure for large docs

When a doc can't meet the size defaults, these keep it cheap to search with a minimal grep:

- **Headings are the index.** Each heading names its topic in words someone would search for and
  carries its slug ID. Headings are unique within the file (`no-duplicate-headings`, MDS005, on by
  default). Keep to H2 and H3; no mdsmith rule enforces the depth, so it's a convention. An
  agent's first move on a large file is `grep -n '^#' file`, which returns an outline with line
  numbers; it then reads only the section it needs.
- **Each section opens with a one-sentence lead** saying what it decides or describes, so the
  few lines after a grep hit confirm the agent is in the right place.
- **A generated `<?toc?>` near the top** (MDS038, kept in sync by `mdsmith fix`), within the first
  40 or so lines, so a short read of the top shows the structure. It mainly serves humans and
  rendered views; headings are what agents use.
- **Cap sections, not files.** `max-section-length` with per-level limits; see the config above.
- **Split a growing doc into a hub and spokes.** The doc becomes a directory. A short hub holds
  the overview and a `<?catalog?>` of the spokes, generated from their summaries, and each spoke
  stays within the defaults. The pipeline's doc catalogue accepts a directory entry, so a hub
  declares the whole set at once.
- **Keep rationale and history out of current-state docs.** Move them to a decision log (or
  `architecture-notes.md`). This is usually the biggest single size win.

## `summary` front matter and the catalog

Every doc under `docs/` starts with front matter carrying a one-line `summary`:

```markdown
---
summary: How the app is built — layers, stack decisions, data and async conventions.
---
```

`required-frontmatter` enforces it. A `<?catalog?>` block (MDS019) then builds a one-line-per-doc
index from those fields, for example in the agent instructions file or a hub doc:

```markdown
<?catalog
glob: "docs/**/*.md"
row: "- [{filename}]({filename}) — {summary}"
?>
<?/catalog?>
```

`row` can name any front-matter field; `summary` is the convention. `mdsmith check` reports the
block as out of date when a doc's summary changes, and `mdsmith fix` regenerates it.

**`<?catalog?>` is not the doc catalogue.** The pipeline's `<!-- doc-catalogue -->` block in
`docs/README.md` declares which docs *ground the pipeline's work* and how binding each is
(`role`, `authority`). Any session that adds, moves or rewrites a listed doc keeps it current. The
`<?catalog?>` index is navigation, generated and drift-checked. They coexist; keep a doc's
`summary` consistent with its doc-catalogue entry so the two never describe it differently.

## The merge driver

Generated sections (`<?catalog?>`, `<?toc?>`, `<?include?>`) conflict whenever two branches
change their inputs. mdsmith's merge driver resolves those conflicts itself: it drops the
conflict markers inside a generated block and regenerates it.

- `mdsmith merge-driver install` registers the driver in your git config and writes a managed
  block into `.gitattributes` (`*.md merge=mdsmith`, plus one line per `ignore:` pattern). Each
  clone runs it once; commit the `.gitattributes` change.
- `mdsmith pre-merge-commit install` adds a hook that re-runs `mdsmith fix` after a merge.
- `mdsmith merge-driver ci-install` checks the committed `.gitattributes` without writing it and
  fails on drift. Run it in CI.
- The `git-hook-sync` rule (MDS048) flags drift between `ignore:` and the driver setup, for
  example after you change `ignore:` or upgrade mdsmith.

## Checklist

- [ ] mdsmith is pinned (mise or another route) and `mdsmith check` runs in CI.
- [ ] `.mdsmith.yml` lists only deviations, each with its reason; exceptions are commented.
- [ ] Every citable section carries a stable `R-` / `A-` / `C-` / `U-` ID in its heading, and the
      `occurrence` guard rejects numeric IDs in prose.
- [ ] Growing content (requirements, decisions, registers) is a list, not a table.
- [ ] Every doc under `docs/` has a `summary`, and the `<?catalog?>` index is in sync.
- [ ] The merge driver is installed and `merge-driver ci-install` runs in CI.
- [ ] Docs are within the size defaults, or carry a commented per-glob budget.
