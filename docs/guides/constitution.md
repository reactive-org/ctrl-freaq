# Guide: writing `docs/constitution.md`

One of a set of authoring guides for the documents the **ctrl-freaq** skills read
(`prd.md`, `architecture.md`, `architecture-notes.md`, `ui-design.md`, `constitution.md`).
This one covers the constitution.

The principles here are **stack-neutral**. The worked examples use **Ruby on Rails 8.1**, but
the shape applies to any stack — swap the example rules for your project's equivalents.

## Why this guide exists: the constitution is different from every other doc

Two properties set the constitution apart, and both shape how you write it.

**1. It is the pipeline's only *hard-blocker* doc.** Every other doc states defaults the planner
*may* deviate from with user approval. A constitution rule is non-negotiable: a change that
violates it is **rejected, not negotiated**, at every stage —

- the `drafter`'s review flags an issue that asks for something the constitution forbids;
- the `planner` must reshape a plan to comply (or declare the issue unbuildable) — and
  its isolated plan-reviewer treats a constitution violation as a **BLOCKER even if it's disclosed
  as a deviation**, because the constitution is not a deviation surface;
- the `resolver` stops on a `Doc conflict` gate if the issue contradicts it;
- the `evaluator` soft-rejects a PR that clearly violates a constitution directive.

So the constitution is where you put the rules you want **mechanically enforced across the whole
pipeline**, with no "but the planner approved a deviation" escape hatch.

**2. It is loaded on essentially every run.** By convention `CLAUDE.md` `@`-includes
`docs/constitution.md` — and the skills assume it does — so the constitution sits in the model's
context for every Claude Code session and every skill invocation in the repo, not just when a
planner happens to read it. That is why it **must be very concise**: every line is a permanent
context tax paid on every run. Conciseness isn't a style preference here; it's a budget. (Even
where the `@`-include is absent, the drafter, planner, resolver, and evaluator each read the
constitution per issue — so terse pays off either way.)

> **The bar:** if a rule needs a paragraph to justify itself, it doesn't belong in the
> constitution. Put the *rule* here in one line; put the *why* in `architecture-notes.md`.

## Know your reader: how the constitution is consumed

- **Read often, read whole, by everything.** When `@`-included by `CLAUDE.md` (the assumed
  convention), treat the whole file as always-in-context; even when it isn't, every doc-reading
  skill loads it per issue. Length is the cost.
- **Cited by stable section anchor.** Plans and reviews reference rules as `constitution §N`
  (e.g. `constitution §2` for layer rules, `§5` for coverage targets). Number your rules and
  keep the numbering stable — renumbering dangles citations already posted in plans and reviews.
- **Enforced as a hard gate, not a suggestion.** A reviewer checks a diff or plan *against* each
  rule. So each rule must be **prescriptive and checkable** — a reader can tell, mechanically,
  what would violate it.

## Authoring principles

1. **Conciseness above all.** One line per rule. Aim for roughly a single screen. A constitution
   that grows past ~a dozen rules has usually absorbed material that belongs in `architecture.md`.

2. **Only *inviolable* rules belong here.** The test: *would you ever approve a deviation from
   this?* If yes, it's a default — put it in `architecture.md` (where the planner can surface a
   deviation gate). If no — it's never OK to break — it's a constitution rule.
   *Rails:* "Prefer Solid Queue over Sidekiq" is a default (→ `architecture.md`). "No secret is
   ever stored or logged in plaintext" is inviolable (→ here).

3. **Prescriptive and checkable.** State what is forbidden or required in terms a reviewer can
   verify against a diff. Avoid aspirations ("code should be clean").
   *Rails:* "Controllers contain no business logic" — checkable. "Write maintainable
   controllers" — not.

4. **Stable, numbered anchors.** Number every rule; append rather than reshuffle. The pipeline
   cites `constitution §N`.

5. **Durable, not version-pinned.** Constitution rules should outlive a framework upgrade. Put
   version-specific defaults in `architecture.md` (which the planner re-reads per issue), not
   here (which everyone carries on every run).

6. **Rule here, rationale elsewhere.** No "why" prose. Rationale → `architecture-notes.md`;
   deviable specifics → `architecture.md`.

## Make thorough automated testing a constitution rule

This is the highest-leverage rule in the file, because the testing rule is the one the **entire
pipeline actively enforces**:

- the planner's `## Test plan` is written *per* your testing rule (the plan schema literally says
  "Unit: suites to add/extend, **per constitution §5 coverage targets**");
- the plan-reviewer raises a **BLOCKER** if the plan's tests don't satisfy it (e.g. "constitution
  mandates full-method coverage on a new service" but the test plan omits it);
- the resolver implements to that test plan and runs the suite;
- the evaluator soft-rejects a PR whose diff violates the rule or ships failing/skipped tests.

So putting a **thorough automated-testing mandate** in the constitution is how you make testing
non-optional everywhere downstream.

Frame it as a **discipline, not just a coverage number.** The core discipline is **TDD**, applied
as a practical middle ground:

- **Test-first** for **core domain/business logic** (the layer §1 names) and **every bug fix** —
  for a bug, a test that **reproduces the defect** (fails before the fix, passes after).
- **Test-after is fine** for exploratory spikes and thin glue code. This is not a deviation: the
  universal bar — every behavior change ships with tests, no merge on red/skip — still covers all
  of it; test-first is simply the *scope* where it's mandatory, the same way "raw SQL only inside
  `app/queries/`" is a scoped rule, not a deviable preference.

**State the rule as the checkable artifact, not the unobservable order.** A diff can't show a test
was written *first*, so don't make "write the test first" the rule — a reviewer can't gate on it
(see Anti-patterns). Name test-first as the discipline, and gate on what *is* visible in the diff:
a bug fix carries a reproducing test; a core-logic change carries success- and failure-path tests.
Make it specific enough to check, e.g. (Rails 8.1):

> **§5 Testing.** Core domain/business logic and bug fixes are developed test-first; a bug fix
> ships a test that reproduces the defect (fails without the fix). Every behavior change ships with
> automated tests; service/domain objects cover every public method on success and failure paths;
> user-facing flows get a system test; exploratory/glue code may be tested after. CI runs the full
> suite on every push; a PR with a failing or skipped test does not merge.

State the *bar* — the discipline and the artifact it must leave behind (what coverage, which
layers, the test-first scope, the no-skip / no-merge-on-red rule) — in one block; not how to write
the tests. The test *framework and fixture style* are a default and live in `architecture.md §10`;
the **non-negotiable bar** lives here.

## What belongs here vs. the sibling docs

| If the rule… | Put it in | Because |
|---|---|---|
| Is never OK to break (no deviation, ever) | `constitution.md` | Enforced as a hard blocker pipeline-wide |
| Is a strong default you'd occasionally approve deviating from | `architecture.md` | Planner can run a deviation gate |
| Explains *why* a rule exists / its history | `architecture-notes.md` | Keeps the constitution to one line per rule |
| Is version- or tooling-specific and churns | `architecture.md` | Re-read per issue, not carried on every run |
| Is a build/test/lint *command* | `CLAUDE.md` / `COMMANDS.md` marker blocks | Read by the resolver/evaluator at run-time (`setup` generates these) |

The constitution states the **bar** ("every behavior change ships with tests"); the marker blocks
hold the **command** that runs them (`<!-- issue-resolver-test-target -->`). Don't conflate them.

## Recommended shape

A short, numbered list of inviolable rules — pick only the handful that are truly non-negotiable
for *your* project; do not copy the whole list. Common categories (one line each):

- **Layers / dependencies** — what may depend on what (cited as `§2` by the planner).
- **Persistence** — migration discipline, encryption of sensitive fields, where raw queries may live.
- **Secrets** — never in source, logs, or plaintext storage.
- **Testing** — the test-first discipline (core logic + bug fixes), the coverage bar, and the
  no-merge-on-red rule (see above).
- **Logging / observability** — no PII in logs; use the structured logger.
- **Background work** — idempotency / retry-safety expectations.
- **Authorization** — default-deny; every entry point authorizes.

## A worked constitution (Rails 8.1, concise on purpose)

This is a *complete* example — note it fits on one screen. The numbering is the author's, and
stable; the rules are illustrative.

```markdown
# Constitution

Non-negotiable rules. A change that violates one is rejected, not negotiated. Rationale lives in
`docs/architecture-notes.md`; deviable defaults live in `docs/architecture.md`.

§1  Layers. Domain logic lives in models and `app/services/`; controllers orchestrate only;
    views contain no logic. Nothing depends inward on a controller.
§2  Persistence. Every schema change is a reversible migration. Sensitive fields use Active
    Record Encryption. Raw SQL only inside `app/queries/`.
§3  Secrets. No credential, key, or token in source, logs, or the database in plaintext —
    use `Rails.application.credentials`.
§4  Authorization. Every controller action authorizes the current user; default-deny.
§5  Testing. Core domain logic and bug fixes are developed test-first; a bug fix ships a test
    that reproduces the defect. Every behavior change ships with automated tests; service/domain
    methods cover every public method on success and failure paths; user-facing flows have a
    system test (glue/exploratory may be tested after). CI runs the full suite on every push; a
    PR with a failing or skipped test does not merge.
§6  Logging. No PII in logs; use the structured logger, never `puts`/`p` in app code.
§7  Background work. Jobs are idempotent and retry-safe; none assumes exactly-once delivery.
```

That's the whole file. Anything you're tempted to add past this is probably a *default* (→
`architecture.md`) or a *rationale* (→ `architecture-notes.md`).

## Authoring checklist

- [ ] Every rule fits on one line and is something you would **never** approve deviating from.
- [ ] The whole file fits on roughly one screen.
- [ ] Every rule is prescriptive and checkable against a diff.
- [ ] Rules are numbered; the numbering is stable.
- [ ] A **thorough automated-testing rule** is present: the test-first discipline (core logic +
      bug fixes, reproducing test for bugs), the coverage bar, and no-merge-on-red.
- [ ] No rationale prose (it's in `architecture-notes.md`); no version-specific defaults (they're
      in `architecture.md`); no build/test commands (they're in `CLAUDE.md` marker blocks).

**Validate against the real consumer:** run `planner` on an issue that brushes a
constitution rule. Success looks like the plan either complying silently or — if the issue can't
comply — the planner stopping rather than offering a deviation. A planner that proposes
"deviate from the constitution, approve?" means that rule was miscategorized and belongs in
`architecture.md`.

## Anti-patterns

- **Prose / rationale in the file** → inflates a doc that's carried in context on every run.
- **Soft preferences stated as law** → the planner refuses to build legitimate alternatives
  instead of running a deviation gate; move them to `architecture.md`.
- **Version-pinned rules** → churn on every framework upgrade and tax every run; keep versions in
  `architecture.md`.
- **Renumbering rules** → dangles `constitution §N` citations already posted in plans and reviews.
- **Aspirational, uncheckable rules** ("code should be clean") → can't gate a diff against them.
- **Omitting the testing bar** → the pipeline has nothing to enforce, and test coverage becomes
  optional everywhere downstream.
- **Mandating test-first as an unverifiable process** ("the test must be written first") → a diff
  can't show ordering; state the checkable artifact instead (a bug fix carries a reproducing test;
  core-logic changes carry success- and failure-path tests).
