# Block authoring reference

The authoring spec for every block `setup` writes: exact shape, what belongs
in each, how to infer the contents from the repo, and how to migrate the legacy single-block
declaration. Read this before drafting anything in §3 of the skill.

The source of truth for *how each block is consumed* lives in the skills that read them — the
`resolver`'s test-selection / static-gate config and the `evaluator`'s repo health-check declaration.
This file mirrors those formats for the *authoring* side; if they ever disagree, the consuming
skill wins. The one exception is `claude-code-stack-profile`, which no skill reads — it is
model-consumed via the CLAUDE.md auto-load, so it has no consuming-skill source of truth.
`issue-resolver-review-guidance` is user-owned for a different reason: its one reader, the resolver,
passes it to the reviewer verbatim and defines no format, so this file owns its seed and the
recurring-class proposals setup adds to it.

## Contents

- [Two shared shapes](#two-shared-shapes)
- [The blocks](#the-blocks)
  - [issue-resolver-fast-checks](#issue-resolver-fast-checks)
  - [issue-resolver-test-target](#issue-resolver-test-target)
  - [issue-resolver-canonical-suite](#issue-resolver-canonical-suite)
  - [pr-evaluator-static-checks](#pr-evaluator-static-checks)
  - [pr-evaluator-test-target](#pr-evaluator-test-target)
  - [pr-evaluator-escalation-labels](#pr-evaluator-escalation-labels)
  - [pr-evaluator-merge-policy](#pr-evaluator-merge-policy)
  - [pr-evaluator-sensitive-paths](#pr-evaluator-sensitive-paths)
  - [worktree-setup / worktree-teardown](#worktree-setup--worktree-teardown)
  - [claude-code-stack-profile](#claude-code-stack-profile)
  - [doc-catalogue](#doc-catalogue)
  - [issue-resolver-review-guidance](#issue-resolver-review-guidance)
  - [github-pipeline-config (file header)](#github-pipeline-config-file-header)
- [Detection heuristics](#detection-heuristics)
- [Legacy migration: health-checks → static-checks + test-target](#legacy-migration)
- [Two worked examples — Swift and Rails](#worked-examples)

## Two shared shapes

Every block is one of two shapes:

**Command-list** (`*-fast-checks`, `*-static-checks`, `worktree-*`) — one Markdown list entry per
command: a backtick-quoted command, then ` — `, then a short human description. **Order matters** —
commands run in declaration order, and the static lists are *fail-fast* (the first non-zero exit
stops the run), so put the cheapest/fastest commands first.

```markdown
- `<command>` — <description>
- `<command>` — <description>
```

**Prose config** (`*-test-target`) — structured Markdown the consuming skill reads as natural
language (it is *not* parsed). Don't compress it into a one-liner; the per-target indentation and
labels are what make it legible to the test-selection sub-agent.

## The blocks

### issue-resolver-fast-checks

Fail-fast **static** commands the resolver runs at its pre-build baseline and before every push:
codegen, dependency resolution, lints, layer-import boundary checks. **No test invocations belong
here** — tests are the test-target block's job. Keep it to things that finish in seconds.

```markdown
<!-- issue-resolver-fast-checks -->
- `<command>` — <description>
<!-- /issue-resolver-fast-checks -->
```

### issue-resolver-test-target

Configuration for the resolver's test-selection sub-agent at the story gates (targeted selection
only). Declares the test `wrapper` and, per target, how source files map to suite identifiers and
what to fall back to when a change can't be mapped to one suite.

```markdown
<!-- issue-resolver-test-target -->
- wrapper: `<test-runner command>`
- targets:
  - `<TargetName>` (unit | UI)
    - naming: <how source files map to suite identifiers>
    - helpers-fallback: <command, or "none">
    - broad-change-fallback: <command, or "none">
<!-- /issue-resolver-test-target -->
```

- **wrapper** — the test runner the project invokes (`./scripts/xcb.sh`, `bin/rails test`, `pytest`, `go test`, …).
- **naming** — the convention mapping a changed source file to the suite(s) that cover it. Project
  knowledge; interview the user rather than inventing it.
- **helpers-fallback** — what to run when a changed file is a shared helper that doesn't map to one
  suite (run the whole target, or `none`).
- **broad-change-fallback** — what to run when a change is too broad to scope (run the whole target,
  or `none`). When a target's drafted value equals the canonical suite —
  `issue-resolver-canonical-suite`'s `full-suite`, or `pr-evaluator-test-target`'s
  `full-suite-command` where that block stands in — that is a **flag for the propose-and-confirm
  gate**, not a silent default: interview for a genuinely narrower rung (one target's whole suite, a
  directory subset) and, when the full suite really is the honest answer, record the user's explicit
  acknowledgment in that confirmation. The block bytes stay canonical either way — no annotation. This
  line sets the per-iteration cost ceiling that targeted selection exists to lower: the resolver honors
  it at every story-gate visit, on every review round.

### issue-resolver-canonical-suite

The **full** suite, read only by the resolver's epic-baseline / bootstrap / post-rectification flow —
never at a story gate. Three labelled commands so re-runs don't cold-rebuild:

```markdown
<!-- issue-resolver-canonical-suite -->
- full-suite: `<one-shot canonical command>`
- build-once: `<compile-the-test-bundle-once command>`
- retry-without-rebuild: `<re-run-without-recompile command>`
<!-- /issue-resolver-canonical-suite -->
```

If the project's runner can't separate compile from run (most non-Apple stacks), set all three to the
same command and note it — the labels still satisfy the reader, you just lose the no-rebuild re-run
optimisation. Skip this block on projects that have no "epic" flow; the resolver falls back to
`pr-evaluator-test-target`'s `full-suite-command` when it's absent.

### pr-evaluator-static-checks

The evaluator's equivalent of `issue-resolver-fast-checks` — fail-fast always-run hygiene, run first
at every gate. Same command-list shape and the same "no test invocations here" rule. Commands use
repo-root-relative paths (the evaluator session runs inside the PR's worktree).

```markdown
<!-- pr-evaluator-static-checks -->
- `<command>` — <description>
<!-- /pr-evaluator-static-checks -->
```

### pr-evaluator-test-target

Like `issue-resolver-test-target`, plus a **`full-suite-command`** — the command returned when
escalation rules fire (epic-integration PR, or an escalation label matched).

```markdown
<!-- pr-evaluator-test-target -->
- wrapper: `<test-runner command>`
- full-suite-command: `<full canonical suite command>`
- targets:
  - `<TargetName>` (unit | UI)
    - naming: <how source files map to suite identifiers>
    - helpers-fallback: <command, or "none">
    - broad-change-fallback: <command, or "none">
<!-- /pr-evaluator-test-target -->
```

The resolver and evaluator test-target blocks usually share the same `wrapper`, target names, and
naming conventions — draft them together and keep them consistent. The only structural difference is
this block's `full-suite-command` line. The broad-change-fallback flag applies here too, against this
block's own `full-suite-command`: a target whose fallback equals it widens every unscopeable change to
the canonical run — confirm that deliberately or interview for narrower, same as the resolver block.

### pr-evaluator-escalation-labels

GitHub PR labels that force the full suite instead of targeted selection. **An empty or absent block
means "no label-based escalation"** — which is a perfectly normal choice. If the user wants no
escalation labels, write the block empty (it documents the decision) or skip it.

```markdown
<!-- pr-evaluator-escalation-labels -->
- `full-suite-required` — bypass targeted selection
- `pre-release` — run everything before a release cut
<!-- /pr-evaluator-escalation-labels -->
```

### pr-evaluator-merge-policy

Governs whether the evaluator's merge step runs hands-free or routes through its
**operator decision gate** (a human approves/rejects the merge). One list item per PR type,
`<pr-type>: <ask | auto>` — a small key/value list, not the command-list or prose-config shape:

```markdown
<!-- pr-evaluator-merge-policy -->
- standard: ask
- story: ask
<!-- /pr-evaluator-merge-policy -->
```

- **Keys** are `standard` and `story`. **Values** are `ask` (gate before merge — the operator must
  Approve / Needs Revision / Reject) or `auto` (merge directly on a clean automated approval).
- **Default is `ask`.** The evaluator treats an absent block — or a PR type omitted from a present
  block — as `ask`, so a repo gets human-in-the-loop merges with no configuration at all. `auto` is
  strictly opt-in, per PR type.
- **`epic-integration` is not configurable** and isn't a valid key here — epic integration PRs land
  every child story's diff on `main` at once and are *always* gated. The evaluator ignores an
  `epic-integration:` line if one appears.
- **Not detected — it's a preference, not a repo fact.** There's nothing in the repo to infer this
  from; ask the user which PR types they want to approve by hand. Propose `ask` for both (the safe
  default) and let them opt specific types into `auto`. Frame the low-risk `auto` knob explicitly as
  the **`docs: auto`-style option** — "auto-merge routine doc/config-style changes" — since a docs /
  config PR (including the ones `setup` / `question-sweep` / `doc-reviewer` land) classifies as
  `standard`, not a distinct evaluator PR-type. The block **keys stay the canonical `standard` /
  `story`**: a `docs:` line is not a valid key (the evaluator would ignore it, same as
  `epic-integration`), so the framing selects `standard: auto`; never write a `docs:` key into the block.
- **Stack-independent.** Unlike the command and test blocks, this one's content never varies by
  language, framework, or test runner — there's no per-stack variant. The one shape above *is* the
  whole schema, which is why the worked examples below don't repeat it.

### pr-evaluator-sensitive-paths

The paths that always need a person at the merge gate (#96). When a PR changes a path matching one of
these patterns, the evaluator's merge step routes through its **operator decision gate** — whatever
`pr-evaluator-merge-policy` says for the PR's type. One list item per pattern: a backtick-quoted
pattern, then optionally ` — ` and the reason a person must look:

```markdown
<!-- pr-evaluator-sensitive-paths -->
- `.github/workflows/` — GitHub Actions CI definitions
- `.gitlab-ci.yml` — GitLab CI pipeline
<!-- /pr-evaluator-sensitive-paths -->
```

- **Grammar.** Each pattern is a case-sensitive shell glob matched against the **whole** repo-relative
  path: `*` and `?` cross `/`, so `*.tf` matches at any depth. A pattern ending in `/` covers
  everything under that folder; a leading `/` is ignored. A broad pattern errs toward an extra click,
  but three forms under-match: `[...]` is a character class (write `[[]` for a literal `[`, as in
  `app/[[]id]/page.tsx`), a folder named without its trailing `/` matches only that exact path, and
  `./` is not normalized — write paths from the repo root. A list item not in the `` - `<pattern>` ``
  / `` - `<pattern>` — <reason>`` shape forces the gate to ask (the evaluator names the line), so a
  typo never drops a path silently; prose lines in the block are ignored.
- **A change to the declaration is always a match.** The evaluator reads the patterns from the PR's
  own head, so a PR that adds, edits, removes, moves or breaks this block is itself gated — it is
  compared with the block at the merge base, and an unrelated edit elsewhere in the same file is not.
  Keep the block inside the repo. A block whose host is outside it — an `@`-include with an absolute
  path or one that climbs out with `../`, or a symlink pointing out of the repo, even back in — is not
  read for patterns, and the gate asks on every PR until the block moves into a file in the repo.
- **An absent or empty block matches nothing** — a perfectly normal choice, like the escalation
  labels. Write it empty to document the decision, or skip it.
- **Detected, then interviewed.** Propose only the CI-definition paths that exist in the tree —
  `.github/workflows/`, `.gitlab-ci.yml`, `.circleci/`, `Jenkinsfile`, `azure-pipelines.yml`, and the
  like — each grounded with `Glob` before it is drafted. Then ask what else needs a person: deploy or
  infrastructure definitions, secrets tooling, the pipeline's own config files. Draft folders with
  their trailing `/` and escape brackets. Never fabricate a path the tree does not have.
- **Stack-independent shape.** The block's shape never varies by stack; the paths it proposes depend on
  the CI system and the repo's layout, which is why the detection above reads the tree rather than a
  per-stack table.

### worktree-setup / worktree-teardown

**Optional**, and most repos don't need them — skip unless the project provisions a per-worktree
resource the test commands depend on (an isolated iOS Simulator on a Swift project, a free localhost
port or scratch DB on a Rails one, a branch-keyed cache). Command-list shape. Setup runs after a
worktree is created/entered and is *fail-fast*; teardown runs before a worktree is removed and is
*best-effort*. Both must be **idempotent** — setup may re-run on a reused worktree, teardown may run
on a half-provisioned or already-cleaned one. The full runtime contract (discovery, idempotency
rationale, and what a repo's hook commands must guarantee) lives in
[`../../_shared/worktree-lifecycle.md`](../../_shared/worktree-lifecycle.md) — this section only
covers the authoring shape.

```markdown
<!-- worktree-setup -->
- `<command>` — <description>
<!-- /worktree-setup -->

<!-- worktree-teardown -->
- `<command>` — <description>
<!-- /worktree-teardown -->
```

**Authored by research-and-propose, not detection** (setup §3): no heuristic infers per-worktree
provisioning, so setup researches the best-practice approach for the detected stack and proposes the
commands for confirmation. Propose the two blocks **as a pair** — setup that allocates a resource must
ship with the teardown that releases it — and prefer commands that are idempotent by construction
(guard-then-create / create-if-absent), per the idempotency contract above.

**Parser constraint.** The runtime executor (`workspace.py`) extracts the *first* backtick-quoted
span of each `- ` item, so every command must be a single backtick-quoted span on one line with no
embedded backticks — a multi-line or backtick-containing command is silently dropped. Chain a compound
command with `&&` on one line, or wrap it in a checked-in script and reference that script.

### claude-code-stack-profile

**Optional but broadly useful**, and the only block here not read by a pipeline skill at a defined
step. It's general operating guidance written into the consuming repo's
**CLAUDE.md** so Claude Code auto-loads it into *every* session in that repo — the resolver, the
evaluator, and any ad-hoc session (someone running only the drafter, or just hand-coding). It
answers one question: **how do you run this stack's commands inside a Claude Code session without
drowning the context window?**

Because the value is the auto-load, this block always lives in **CLAUDE.md** (or a file CLAUDE.md
`@`-includes), regardless of where the pipeline config blocks live — the one deliberate exception
to the "config defaults to `COMMANDS.md`" rule. The interior is free prose under a human-facing
heading; `config_block.py` copies it verbatim.

**Ownership — user-owned, not plugin-owned.** Unlike the machine-parsed config blocks, this one is
**user-owned**: external edits are allowed and expected. Setup *seeds* it when absent; on re-run it
**re-ingests** the existing interior as the authoritative base and proposes only currency
refinements on top — it does **not** reconcile-to-canonical the way the plugin-owned blocks do, and
never overwrites the user's prose.

```markdown
<!-- claude-code-stack-profile -->
## Running this stack with Claude Code

<concise operating guidance — see scope and constraints below>
<!-- /claude-code-stack-profile -->
```

**No parser constraint.** Nothing parses this block at runtime — it's model-read via the CLAUDE.md
auto-load, not extracted by a script. So unlike `worktree-*`, the one-backtick-span-per-line rule
does **not** apply: multi-line prose, lists, and several code spans on a line are all fine.

**Scope — the operating/efficiency layer only.** What belongs:
- which commands are slow enough to run backgrounded (`run_in_background: true`) and waited on;
- when to redirect output to a log and read back the tail / `grep` the failures, vs. let a terse
  command stream inline;
- the stack's terse/machine-readable output formatter, and its fast-subset / re-run-only-failures
  invocation syntax;
- parallelism knobs and any per-worker resource (e.g. a test database per worker) a session should
  know about;
- for a stack with a user-facing UI, how a session runs the app and captures a screenshot of a
  changed state, so a PR that changes what a user sees carries them. The resolver attaches what it
  captures to the PR body (`skills/resolver/references/pr-screenshots.md`), and this line is the
  guidance that procedure defers to. Name the run command and the capture route; a stack with no UI
  omits it.

What does **not** belong: coding conventions, architecture, or style (those are the human's
CLAUDE.md), and a plain command list (that's what `/init` produces — complement it, don't
duplicate). Keeping it narrow is what keeps it concise enough to justify the always-loaded weight.

**Surface the signal, never suppress it.** Guidance is always *redirect-then-read-back* (`… | tee
<log>`, then `grep`/tail), never "hide output" — the reader still has to see pass/fail and the
failures, or it's blinded.

**Authored by research-and-propose with a default-on currency check** (setup §3): when the block is
absent, draft from stack knowledge; when present, the existing prose is the base. Either way run a
lightweight web check that the idioms are still current — currency is this block's whole point — and
escalate to fuller research for an unfamiliar stack. Never fabricate; if the stack is unrecognized,
ask or skip.

**Two worked examples.** State the generic principle first — *name the slow/noisy commands and how
to run them cheaply; leave the fast ones alone* — then fill it in. Same shape, two stacks.

*Rails:*

```markdown
<!-- claude-code-stack-profile -->
## Running this stack with Claude Code

- Unit/integration tests are terse — run them inline and targeted: `bin/rails test test/models/book_test.rb` (append `:LINE` for one test).
- System tests and the full suite (`bin/rails test:system`, or `bin/rails test:all` for everything) are slow and noisy (headless browser, server logs, screenshots under `tmp/screenshots/`). Background them, log the output, read only the summary: `bin/rails test:system 2>&1 | tee tmp/test.log`, then `grep -E 'runs|failures|errors|Failure|Error' tmp/test.log`.
- Re-run only what failed; keep iterating on the named test rather than the whole suite.
- Tests parallelize above ~50 examples, one test DB per worker (auto-created, suffixed by worker number) — don't assume a single shared DB; pair with `worktree-setup` when isolating per worktree.
- One-time slow setup (`bundle install`, `bin/rails db:prepare`, `assets:precompile`) — run backgrounded and wait, don't poll the output.
- A PR that changes what a user sees carries screenshots: run `bin/dev` backgrounded, open each changed page in the in-app browser, and save a capture of each changed state.
<!-- /claude-code-stack-profile -->
```

*Node / TS:*

```markdown
<!-- claude-code-stack-profile -->
## Running this stack with Claude Code

- Unit tests are fast — run targeted: `npm test -- path/to/foo.test.ts`.
- The full suite, e2e runs, and coverage are slow/noisy — background and log: `npm run test:e2e > /tmp/e2e.log 2>&1`, then read the tail / `grep` failures. Prefer the reporter's terse mode (`--reporter=dot`) over the verbose default.
- Re-run only failures (`vitest --changed`, `jest --onlyFailures`) instead of the full run.
- One-time slow steps (`npm ci`, a cold production build) — background and wait.
- A PR that changes what a user sees carries screenshots: run `npm run dev` backgrounded, open each changed view in the in-app browser, and save a capture of each changed state.
<!-- /claude-code-stack-profile -->
```

### doc-catalogue

The repo's **grounding documents**, declared by the repo itself. The planner, drafter, slicer,
requirements-gatherer, resolver, evaluator, and question-resolver read it to learn which docs to ground
on and how much authority each carries;
without it they have no declared documents to ground on. The full contract — home, per-field semantics,
directory entries, the absent-catalogue rule — is
[`../../_shared/doc-catalogue.md`](../../_shared/doc-catalogue.md); read it before drafting. This
section covers only the authoring side.

**Lives only in `docs/README.md`** — the second fixed-home block, for the same kind of reason as
`claude-code-stack-profile`'s CLAUDE.md: a catalogue of documents belongs beside the documents, in the
index a repo already maintains for its own sake. It never goes in `COMMANDS.md`/`CLAUDE.md`, and
readers look nowhere else.

```markdown
<!-- doc-catalogue -->
- `<path>` — <role> — <binding | informative> — <one-line summary>
<!-- /doc-catalogue -->
```

**Ownership — user-owned**, exactly like `claude-code-stack-profile`: seed it when absent, and on a
re-run **re-ingest the existing interior as the authoritative base**, proposing only refinements (a
dead path, a document the index names that the catalogue lacks, a line that does not parse). Never
reword a summary a human wrote. It differs from the stack profile in one way that matters: this block
**is** machine-parsed, so every line must hold the entry grammar.

**Authored by a context-blind sub-agent, not inline** — the only block whose drafting leaves the main
loop, because deriving it means reading a whole docs index and judging each document's force, and that
reasoning should not land in the setup session's context. Dispatch
[`doc-catalogue-derivation-prompt.md`](doc-catalogue-derivation-prompt.md), resolving the prompt path
and the `_shared/doc-catalogue.md` contract path yourself (raw-read reference paths are not
substituted) and passing them as placeholders alongside
`facts.inventory.doc_catalogue.readme_path`, its `base_path` (or the literal `(absent)`), and
`facts.root.path`. Treat what it returns as a draft like any other block: show it at §4 and gate.

**No `docs/README.md` → skip, never invent.** Report *no docs index — skipped*, with the one-line
remedy (create `docs/README.md` describing the repo's grounding docs, then re-run setup). Do **not**
walk the tree looking for doc-shaped files, and do **not** interview the operator for a doc list: a
catalogue with no index behind it has nothing to re-ingest on the next run, which breaks the
user-owned posture, and inventing a grounding set is precisely what the block exists to prevent. The
readers degrade loudly on their side (a `DOC_CATALOGUE_ABSENT` notice), so the gap is
visible without setup guessing at it.

**Target the committed file.** The staged upsert writes `<workspace>/docs/README.md` inside a
worktree of `main`, so a `docs/README.md` that exists only as an uncommitted edit in the operator's
root is not there — and `config_block.py` creates a file but not a missing parent directory, so the
write fails outright rather than silently. Surface it ("commit `docs/README.md` first"); never work
around it by creating a `docs/` directory holding nothing but this block.

**Validate what you wrote.** After the upsert, re-read the block and check every non-blank interior
line against the contract's grammar: four ` — `-separated fields, a backtick-quoted path that exists
(a file, or a directory declared as a document set), an `authority` of exactly `binding` or
`informative`. A line that fails is **silently skipped by readers** — the same failure class as a
dropped `worktree-*` command — so report it and fix it rather than shipping a catalogue with a hole in
it.

Summary vocabulary is the user-owned one: **seeded** / **refreshed** / **already-current** / **skipped**.

### issue-resolver-review-guidance

The repo's **owner-written guidance for the resolver's reviewer** (#99): what failure looks like in this
repo, which paths to skip, how many nits to report, and what evidence a finding needs. The resolver's
prep reads it (`facts.config.review_guidance`) and the review loop appends its text, verbatim, to the
reviewer's `<context>` once per loop; nothing parses its structure.

**Home — the pipeline config file.** It sits beside the other `issue-resolver-*` blocks: the file it
already lives in — `facts.inventory.review_guidance.source_file`, the well-formed copy the resolver reads
(a candidate file, or a file one `@`-includes) and the base was staged from; write there, an empty copy
included (a new copy elsewhere would be shadowed by it) — else `facts.target_file.suggested`. Unlike the
stack profile it has no fixed home — one stage reads it, so the every-session auto-load buys nothing.
It is reported in its own facts object and never counts toward the target-file decision.

**A copy outside the repository is reported, never landed.** When `source_file` is absolute, the copy
the resolver reads sits in an `@`-include outside the root, and setup cannot write it: its landing
upserts `<workspace>/<target-file>` (setup-flow §5). Land nothing for this block then — no seed, no
entries — and create no copy in a candidate file either: candidate files are read before includes, so a
new copy would shadow the owner's and silently replace their guidance for the resolver. The summary
prints the proposed entries with that path, for the owner to add by hand. (#119 decides this rule for
every block; this covers only the guidance block.)

**Ownership — user-owned**, like `claude-code-stack-profile`: seed it when absent; when present,
re-ingest `facts.inventory.review_guidance.base_path` as the authoritative base and propose only
additions on top of it. Never reword, reorder or drop the owner's text — repo-specific prose has no
canonical form to reconcile to.

```markdown
<!-- issue-resolver-review-guidance -->
## Review guidance

**What failure looks like here:** <the failure modes this repo's binding docs name>
**Skip:** <generated or vendored paths a review should not read>
**Nits:** <how many style nits a review reports, if any>
**Evidence:** <what every finding must cite>

**Recurring findings:**
<!-- /issue-resolver-review-guidance -->
```

**Seed from repo evidence, never invent.** Draft *What failure looks like here* from the repo's
`binding` documents (its doc catalogue, when one exists — an invariant registry, layer rules, a
security policy), and *Skip* from paths the tree shows are generated or vendored (build output, checked-in
dependencies, files marked generated in `.gitattributes`). *Nits* and *Evidence* are the owners' policy,
not a fact of the tree. An item with no evidence behind it keeps its label followed by
`_(for the code owners to fill)_`; the `**Recurring findings:**` list starts empty.

**Write reviewer flags as code spans.** The reviewer reads `--comment`, `--fix`, `--post`, `--no-post`
and `--max-findings` wherever they stand as whitespace-delimited tokens. The resolver's prep wraps any bare
one before the text reaches the reviewer (`facts.config.review_guidance.neutralised` lists them), but
write each as its own code span (`` `--fix` ``) so the block reads the way it is sent.

**Recurring-class proposals** (`facts.review_history`, present when the router passed `--repo`). A
finding class that keeps coming back in the review loop is fixed at its source by saying so in the
guidance, not only by filing it again. Prep read the history through review-report's core; which
findings are one class coming back, and what the entry says, is yours. Two **labelled bases, never
pooled** — rank them separately and never add their counts:

- **Record basis** — `facts.review_history.class_counts`, one row per exact class key the loop recorded,
  and the findings it tallies staged at `record_path` (`pr`, `id`, `class_key`, `bucket`, `outcome`,
  `site`, `item`). A key recurs at `count` ≥ 2: `count` above `len(prs)` means some PR carried more than
  one finding of it (in one round or across rounds), two or more `prs` means across PRs. You may judge
  two keys to be one class; say so and cite both. The merged entry carries the key with the most
  findings (ties broken by class key in alphabetical order), its counts are the merged class's totals,
  and its guidance names each other merged key as `` `<key>` ``, so the next run reads them all as
  `listed`. A merged class that takes in a `listed` key is itself listed: the owner's entry already
  covers it.
- **Legacy basis** — the findings staged at `legacy_path` (`pr`, `id`, `bucket`, `outcome`, `site`,
  `item`; `id` is the ledger id on a ledger row, else null), from loop comments written before the
  record format. Group them into classes from `item` and `site`, mint each class a key in the record's
  `class:` grammar (kebab-case, one to five words), and count a class as recurring at two or more
  findings. This basis is looser than a recorded key, so each of its proposals names the items it rests
  on.

`unkeyed_findings` counts the findings on neither basis — a ledger entry no record line names, or one
whose PR's comments did not read. Never group them; the summary reports the count when above zero.

**The kind follows the repo-wide outcomes only**, on either basis. Two sides count: **watch for**
(`fixed`) and **not a finding here** (`wrong` and `did-not-matter`). `intentional` counts toward
neither, and neither does any excluded outcome: `intentional` comes from `plan-settled` and
`deferred-by-plan`, which say what one plan intends at one phase, not what holds in the repo, and
counting it toward a "do not flag" entry would turn a plan's deferral into standing guidance that
suppresses the finding in every later PR. The kind is the side with more findings, and a class is
proposed only when that side carries **at least two** — one finding is an instance, not a class:

- *watch for* — "watch for <failure>" (what failure looks like here);
- *not a finding* — "<pattern> is not a finding here: <reason>" (what not to flag), the reason being
  what the class's findings' `item` text records (a settled finding's item carries its recorded reason).
  When the items record none, state the pattern alone; the owner can add the reason at setup-flow §4.

So a class that recurs mostly as `intentional` is usually left with too few counted findings to be
proposed (`no counted outcome` or `one finding`), and the summary still names it with that reason —
that is how "plans keep settling or deferring this" reaches the operator. Its `intentional` findings
never weigh against a side that does carry two.

**Which ones, and how many.** Never re-propose a class the base already lists: a record-basis row
marked `listed`, or a legacy class an entry in the base's `**Recurring findings:**` list covers by
meaning, whatever key this run would mint — its summary line reuses that entry's key (you mint legacy
keys anew each run, so matching a fresh key alone would propose one class twice). Propose at most
**five** new entries per run — record-basis classes first, then legacy-basis classes; within a basis,
rank by the winning side's findings, then by total findings, then by class key in alphabetical order —
so the guidance stays short. Every recurring class not proposed gets one reason, the first that holds,
checked in this order: `listed`; `no counted outcome` (every finding is `intentional` or excluded);
`one finding` (neither side carries two or more); `tie`; `over cap`. On `available: false` (a
`REVIEW_HISTORY_UNAVAILABLE` notice) propose nothing and say why; a `REVIEW_COMMENTS_UNAVAILABLE` /
`REVIEW_COMMENTS_TRUNCATED` notice still proposes from what was read, and the summary quotes it.

**The entry line** goes under the base's `**Recurring findings:**` list, one line per class, and nothing
else in the owner's text moves:

```markdown
- <guidance> — recurring: `<class-key>` · <n> findings · <m> PRs · <record|legacy>
```

At setup-flow §4 the diff is *their content + proposed entries*; beside each entry (not in the block),
cite what it rests on — the PR numbers, and the record ids from `record_path` or the legacy items from
`legacy_path`. The same generic principle on two stacks:

- *Swift:* `` - watch for a view model read on a background queue — UI state must be set on the main actor — recurring: `main-actor-read` · 4 findings · 3 PRs · record ``
- *Rails:* `` - a `find_by` returning nil in an admin-only controller is not a finding here: those routes are guarded upstream — recurring: `nil-find-by` · 3 findings · 2 PRs · legacy ``

Summary vocabulary is the user-owned one: **seeded** / **refreshed** / **already-current** / **skipped**.
The proposals add: **proposed** (each entry, with its basis), **not proposed** (each recurring class left
out, with its reason), **history unavailable** (with the notice), **history not read** (no
`review_history`: the router could not resolve the repo, so it passed no `--repo`) — or *no recurring
class* when the history held none. Also report `unkeyed_findings` when above zero, and, when
`prs_scanned` equals `limit`, that the proposals rest on the `<limit>` most recent PRs.

### github-pipeline-config (file header)

The human-facing header at the top of `COMMANDS.md`. It is **not a pipeline config block** — no
skill parses it — but it is **plugin-owned and reconciled to canonical** (a re-run restores the exact
wording; that self-heal is its point). It exists to tell anyone opening the file that these blocks
are pipeline config and that **hand-edits are fine as long as the `<!-- … -->` markers stay intact**.

**Lives only in `COMMANDS.md`** (the dedicated config file), never atop a human-facing `CLAUDE.md` —
skip it when the pipeline config blocks live in `CLAUDE.md`.

Write it via `config_block.py upsert <COMMANDS.md> github-pipeline-config <body-path> --prepend` —
`--prepend` places a newly created block at the top of the file (a no-op once the block exists, so
re-runs stay idempotent). The verbatim body:

```markdown
<!-- github-pipeline-config -->
Pipeline configuration for the `github-pipeline` skills (resolver / evaluator / planner), read at use-time. You can edit these blocks by hand — just keep each block's `<!-- … -->` marker pair intact so the skills can find it. Re-run `github-pipeline-setup` to reconcile them (idempotent).
<!-- /github-pipeline-config -->
```

The illustrative `<!-- … -->` in the prose uses an ellipsis and sits inline with other words, so it
never matches `config_block.py`'s whole-line marker scan — keep it that way (don't put a bare
look-alike marker on its own line). **Never** write an "edit only via setup / not by hand" notice in
its place: hand-edits are allowed; the only ask is that the markers are maintained.

## Detection heuristics

Propose drafts from what the repo already declares, then confirm. Sources, roughly in order of
signal strength:

1. **CI workflows** (`.github/workflows/*.yml`) — the strongest signal for the *shape* of the static
   and test commands (wrapper, flags, ordering), because CI is already the project's source of truth
   for "is this green". Map CI's lint/typecheck steps → static checks; its test step → the test
   wrapper / full-suite-command. But a *green* CI job is not proof the suite it names exists —
   scaffold generators ship test scaffolding for suites that may have no tests (`rails new` even adds
   a CI workflow with a `system-test` job; an Xcode template adds empty `<App>UITests` stubs) — so
   treat a CI-derived command as a **candidate** and ground it against the tree (see "Ground every
   candidate" below) before writing it.
2. **Task runner manifests:**
   - **Node / TS** — `package.json` `scripts`: `lint`, `typecheck`/`tsc`, `build` → static checks;
     `test`, `test:unit`, `test:e2e` → test wrapper (`npm run <script>` / `pnpm <script>`). Test
     framework + dir (`jest`, `vitest`, `__tests__/`, `*.test.ts`) → target names and naming.
   - **Go** — `go vet ./...`, `golangci-lint run` → static; `go test ./...` → wrapper; packages are
     the targets; naming maps `foo.go` ↔ `foo_test.go` in the same package.
   - **Python** — `ruff check`/`flake8`, `mypy` → static; `pytest` → wrapper; `tests/` modules are
     the targets; naming maps `module.py` ↔ `tests/test_module.py`.
   - **Swift / Apple** — `swiftlint`, layer-import scripts, `scripts/xcb.sh` style wrappers → static;
     `xcodebuild test` or the project's `xcb.sh` → wrapper; `<App>Tests` (unit) / `<App>UITests` (UI)
     are the targets; canonical-suite's three labels come from the wrapper's build-vs-run flags.
   - **Ruby / Rails** — `rubocop`, `brakeman` → static; `bin/rails test` (or `bundle exec rspec`) →
     wrapper; `test/` (Minitest) or `spec/` (RSpec) modules are the targets, with system/feature
     tests (`test/system`, `spec/system`/`spec/features`) as the integration target; naming maps
     `app/<layer>/<x>.rb` ↔ `test/<layer>/<x>_test.rb` (or `spec/<layer>/<x>_spec.rb`). No separate
     compile step, so canonical-suite's three labels collapse to the one test command.
   - **Make** — `Makefile` targets: `make lint`/`make check` → static; `make test` → wrapper.
3. **Project-type signals** — `Package.swift`/`*.xcodeproj`, `go.mod`, `pyproject.toml`/`setup.cfg`,
   `Cargo.toml`, `pom.xml`/`build.gradle`, `Gemfile`/`config/application.rb` — disambiguate stack when
   the manifests above are absent.
4. **`scripts/*.sh`** — repos often wrap their real commands (`scripts/check-*.sh`, `scripts/test.sh`,
   `scripts/xcb.sh`); prefer the wrapper over the raw tool when one exists, since that's what the
   project maintains.

Rules of thumb:
- **Ground every candidate — verify viability before writing.** Detection from *any* source (CI, a
  task-runner manifest, `scripts/*.sh`, a project-type signal) yields *candidates*, not facts; a
  source naming a command isn't proof it runs. Before drafting a command into any block, verify it's
  viable against the working tree:
  - *static / fast checks* — the script or target it invokes exists (a `package.json` `scripts`
    entry, a `Makefile` target, a `scripts/*.sh` file, or a tool on `PATH`);
  - *test targets / suites* — real test files back the target (`Glob` / `find` / `git ls-tree`);
  - *canonical / full suite* — the suites it chains actually exist.
  Exclude — and tell the user you did — any candidate you can't ground. Be **conservative**: drop one
  only on positive evidence of absence; when you can't tell, keep it and flag it for the setup §6
  dry-run or a user confirm, rather than over-pruning. For example, include a Rails `test/system`
  target / `bin/rails test:system` only if `test/system/**/*_test.rb` (or `spec/system/**/*_spec.rb`)
  exists, and a Swift/Apple `<App>UITests` target only if the UITest bundle holds test files; a `package.json` script CI names
  but `scripts` doesn't define is dropped whatever the stack. Same verify-it's-in-the-tree discipline
  the resolver's audit applies to an issue body.
- **Prefer the project's own wrapper** over a raw tool invocation — it's what's maintained and what
  CI uses.
- **Static lists exclude tests.** If detection surfaces a single "test everything" command, that's
  the test-target wrapper / full-suite-command, not a static check.
- **A broad-change-fallback equal to the full suite is a flag, not a default.** Legitimate on a small
  project — but it must reach the user as a deliberate, acknowledged choice at the propose-and-confirm
  gate (interview for a narrower rung first), never arrive silently out of detection. The story gates
  honor whatever that line declares, so it is where the per-round test cost is actually set.
- **Never fabricate.** Two distinct failures, each worse than an absent block the pipeline skill asks
  about at runtime: a source that yields *nothing* for a block (present it empty and ask — don't
  invent a command), and a source that yields a *non-viable* one (a command the viability rule above
  can't ground — exclude it and surface why). Never confidently wire in something you didn't verify.

## Legacy migration

Older repos declare one `pr-evaluator-health-checks` block holding both static checks and the test
invocation as a flat command list. Re-running setup offers to split it:

1. `config_block.py read <file> pr-evaluator-health-checks` — get the current commands.
2. **Static commands** (lints, codegen, dep resolution, boundary checks) → `pr-evaluator-static-checks`.
3. **The test invocation** → the basis for `pr-evaluator-test-target`: that command becomes both the
   `wrapper` and the `full-suite-command`. The per-target `naming` / fallbacks aren't recoverable from
   a flat command — interview the user (or carry over from `issue-resolver-test-target` if it already
   exists and matches).
4. Confirm the split, `upsert` both new blocks, then `remove pr-evaluator-health-checks`.

Migration is opt-in and reversible-by-eye (the user sees the diff). Don't remove the legacy block
until both replacements are written and confirmed.

## Worked examples

The same schema, filled in for two different stacks. Read them side by side: the block *shapes*
and field names are identical — only the commands, target names, and naming rules change with the
stack. That's the contract working as intended; nothing here is stack-specific by design.

### Swift / Apple (`food-journal`) — the canonical shape the evaluator documents

```markdown
<!-- pr-evaluator-static-checks -->
- `./scripts/check-layer-imports.sh` — Layer-import boundary lint (fast, <5s)
- `CI=1 ./scripts/run-swiftlint.sh` — SwiftLint in CI strict mode
<!-- /pr-evaluator-static-checks -->

<!-- pr-evaluator-test-target -->
- wrapper: `./scripts/xcb.sh`
- full-suite-command: `./scripts/xcb.sh`
- targets:
  - `FoodJournalTests` (unit)
    - naming: source `<X>.swift` ↔ `FoodJournalTests/<X>Tests.swift`; suite id `FoodJournalTests/<X>Tests`.
    - helpers-fallback: `./scripts/xcb.sh -only-testing FoodJournalTests`
    - broad-change-fallback: `./scripts/xcb.sh -only-testing FoodJournalTests`
  - `FoodJournalUITests` (UI)
    - naming: flow-oriented; map by symbol references and `@testable import`.
    - helpers-fallback: `./scripts/xcb.sh -only-testing FoodJournalUITests`
    - broad-change-fallback: none
<!-- /pr-evaluator-test-target -->

<!-- pr-evaluator-escalation-labels -->
- `full-suite-required` — bypass targeted selection
<!-- /pr-evaluator-escalation-labels -->
```

The `FoodJournalUITests` target appears because `food-journal` ships UI tests; per "Ground every
candidate", omit it on a project whose UITest bundle holds no test files.

The resolver side mirrors this — same `wrapper`, target names, naming, and fallbacks — minus the
`full-suite-command` line, plus the `issue-resolver-canonical-suite` block for the epic flow. Swift
builds and runs tests in separate steps, so the canonical-suite's three labels are distinct:

```markdown
<!-- issue-resolver-canonical-suite -->
- full-suite: `./scripts/xcb.sh test`
- build-once: `./scripts/xcb.sh build-for-testing`
- retry-without-rebuild: `./scripts/xcb.sh test-without-building`
<!-- /issue-resolver-canonical-suite -->
```

### Ruby on Rails (`books-api`) — the same schema, a different stack

```markdown
<!-- pr-evaluator-static-checks -->
- `bin/rubocop` — Ruby style + lint
- `bin/brakeman --no-pager -q` — static security scan
<!-- /pr-evaluator-static-checks -->

<!-- pr-evaluator-test-target -->
- wrapper: `bin/rails test`
- full-suite-command: `bin/rails test && bin/rails test:system`
- targets:
  - `test` (unit)
    - naming: source `app/<layer>/<x>.rb` ↔ `test/<layer>/<x>_test.rb`; suite id `test/<layer>/<x>_test.rb`.
    - helpers-fallback: `bin/rails test test/<layer>`
    - broad-change-fallback: `bin/rails test`
  - `test/system` (system / integration)
    - naming: flow-oriented; map by symbol references and rendered partials/routes.
    - helpers-fallback: `bin/rails test test/system`
    - broad-change-fallback: none
<!-- /pr-evaluator-test-target -->

<!-- pr-evaluator-escalation-labels -->
- `full-suite-required` — bypass targeted selection
<!-- /pr-evaluator-escalation-labels -->
```

The `test/system` target and the `test:system` half of `full-suite-command` appear because
`books-api` has tests under `test/system/`; per "Ground every candidate", omit both on a repo whose
`test/system/` holds no `*_test.rb` (e.g. a fresh `rails new`).

Rails runs tests directly — there's no separate compile step — so the canonical-suite's three
labels collapse to the single test command (the all-three-identical degenerate case the
`issue-resolver-canonical-suite` authoring notes call out for non-compile stacks):

```markdown
<!-- issue-resolver-canonical-suite -->
- full-suite: `bin/rails test && bin/rails test:system`
- build-once: `bin/rails test && bin/rails test:system`
- retry-without-rebuild: `bin/rails test && bin/rails test:system`
<!-- /issue-resolver-canonical-suite -->
```
