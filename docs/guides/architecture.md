# Guide: writing `docs/architecture.md`

One of a set of authoring guides for the documents the **ctrl-freaq** skills read
(`prd.md`, `architecture.md`, `architecture-notes.md`, `ui-design.md`, `constitution.md`).
This one covers the architecture doc.

The principles here are **stack-neutral**. The worked examples use **Ruby on Rails 8.1**
(a project that prefers native framework features over third-party gems), but the structure
applies to any stack — swap the example content for your framework's equivalents.

## Why this guide exists: your reader is an LLM

In this pipeline your architecture doc's primary reader is **not a human** — it's the
`planner` skill. That changes how you should write it.

The consumption chain is one-directional and lossy by design:

```
docs/architecture.md  ──read by──▶  planner  ──emits──▶  <!-- implementation-plan:v1 -->
                                                                    │
                                                          read by resolver
                                                          (writes the code)
```

The **resolver never reads `architecture.md`.** It is told to lift its grounding from the plan's
`## Doc grounding`, `## Architecture decisions`, `## Changes`, `## Data model / schema impact`,
and `## Test plan`, and to treat those as *the locked decisions it implements against*. So:

> **Anything your architecture doc fails to make *citable* by the planner becomes a gap in the
> plan — which the resolver then fills by guessing, or which forces a costly round-trip back to
> a human or the planner.**

Your job, when authoring the doc, is to be the **citable, prescriptive source of truth for every
cross-cutting decision the planner needs to lock** — especially the ones a fresh implementer
*could not infer by reading a single file*.

## Know your reader: how the planner consumes the doc

Five mechanics of the planner determine how you should write:

1. **It reads the whole doc, gated only on the file existing.** Reading isn't delegated or
   conditional, so length has a real context cost. Be dense and prescriptive, not narrative.
2. **It cites the doc by stable section anchor — it does not quote it.** The plan's
   `## Doc grounding` is *the citations, not a restatement* (e.g. `architecture.md §2 (layer
   rules), §5 (service contract)`). Every architecture decision in the plan carries a citation
   like `[architecture.md §3.2]` or `[precedent: app/services/x.rb:NN]`.
3. **An isolated reviewer later verifies each citation resolves and actually *governs* the
   claim.** A citation that points at a section that doesn't say what the plan claims is a review
   blocker. Stable anchors and one-claim-per-section are what keep `§3.2` meaning the same thing
   after you edit the doc.
4. **An `architecture.md` rule is a *deviable default*; a `constitution.md` rule is a *hard
   blocker*.** If a plan wants to depart from `architecture.md`, the planner stops and asks the
   user to approve the deviation. If it would violate `constitution.md`, the planner must reshape
   the plan to comply or declare the issue unbuildable. **Where you put a rule changes the
   escalation behavior** — this is a design decision, not filing.
5. **It has a currency / knowledge-gap path** that triggers web research when a plan rests on a
   version-specific default near the model's knowledge cutoff. If you *pin your versions and the
   defaults you rely on*, the planner grounds on stated fact instead of fetching or guessing.

## Seven authoring principles

Each principle is stated neutrally, then illustrated for Rails 8.1.

1. **Write for citation, not narration.** Give every section a stable, numbered anchor you won't
   renumber casually, and make each (sub)section assert a single governing rule. A citation
   should resolve to one checkable claim.
   *Rails:* `## §3.2 Background work` should say one thing — "async work runs through Active Job
   on Solid Queue" — not survey five queueing options across three paragraphs.

2. **Use prescriptive voice.** State the decision (`default to X`, `never Y`), not the landscape.
   Wishy-washy text ("you might consider…") produces *no deviation signal*, so the planner can't
   gate on it; options in the doc become open questions the planner must resolve.
   *Rails:* "Controllers contain no business logic" — not "controllers should usually stay
   relatively thin."

3. **Split *what* from *why*.** Keep `architecture.md` to the *what* (rules, decisions, APIs).
   Put the *why* (rationale, rejected alternatives, history) in `architecture-notes.md` — the
   planner reads that when it's tempted to deviate, and the rationale often pre-empts the
   deviation.
   *Rails:* `architecture.md`: "Use Solid Queue, not Sidekiq." `architecture-notes.md`: "We
   dropped the Redis dependency in 8.0; Solid Queue removed our only reason to run Redis."

4. **Document what the code can't teach by example.** Your highest-value content is the
   conventions that live *everywhere and nowhere* — layer boundaries, the stack-preference
   policy, error/transaction/idempotency rules. A decision with an obvious single code precedent
   the planner can already cite needs *less* doc; a decision with no single home needs the doc
   *most*.

5. **No TBDs, no open questions.** Anything ambiguous becomes a hedge the planner must resolve —
   from precedent if it can, else by interrupting a human. State decisions; route genuinely open
   questions out of the doc and into a real decision before you write it down.

6. **Place each rule by its *deviability*, not its topic.** Truly non-negotiable → `constitution.md`.
   Strong-default-but-deviable-with-approval → `architecture.md`. This is what makes a
   stack-preference behave correctly (see *Encoding a stack-wide preference* below).

7. **Pin versions and the defaults you depend on.** Name the framework/runtime versions and the
   specific defaults your decisions rest on, so the planner grounds on stated truth.
   *Rails:* "Rails 8.1, Ruby 3.x. Defaults we rely on: Solid Queue/Cache/Cable, Propshaft,
   importmaps, Minitest." If you've overridden a framework default, say so — don't make the
   planner assume.

## What belongs here vs. the sibling docs

| Content | Put it in | Why |
|---|---|---|
| Non-negotiable rules ("secrets are never stored unencrypted") | `constitution.md` | Planner treats it as a hard blocker, not a deviation to negotiate |
| *Why* a decision was made; rejected alternatives; history | `architecture-notes.md` | Planner reads it when tempted to deviate; keeps `architecture.md` prescriptive |
| UI component catalog, sizes, named visual patterns | `ui-design.md` | Planner cites `ui-design §X` for UI decisions specifically |
| Test/build/gate **command targets** (`<!-- issue-resolver-test-target -->`, `<!-- pr-evaluator-*-checks -->`, merge policy) | `CLAUDE.md` / `COMMANDS.md` marker blocks | Read by the **resolver/evaluator at run-time**, not by the planner-grounding path — different consumer, different mechanism. Run `setup` to generate these |
| Setup / deploy runbooks, environment bootstrapping | `README` / ops docs | Not part of plan grounding |

`architecture.md` itself = the prescriptive *what* of your structure and your stack decisions.

## Recommended section outline

The **purpose** of each section is universal; the **example content** is Rails 8.1. Number your
sections and keep the numbering stable.

- **§1 System overview & boundaries.** What the app is, its top-level domains, the external
  systems it talks to, the request lifecycle in a line. Orientation only — no decisions here.
- **§2 Layer model & where logic lives.** *The* section the planner cites on nearly every plan,
  because the plan's `## Changes` must name the *file path and layer* of every new symbol. Define
  each layer, its directory, and the dependency rules between them.
  *Rails:* thin controllers (orchestration only); persistence-bound behavior on Active Record
  models + concerns; multi-model workflows in `app/services/` POROs (`ApplicationService`,
  `.call`, returns a Result); read/query logic in scopes or `app/queries/`; no logic in views
  (partials + helpers); authorization in `app/policies/`.
- **§3 Default stack / framework-feature map.** Your stack decisions as a table: concern → the
  chosen default → the bar for deviating. (For a "prefer native" project this is the most
  load-bearing section — see below.)
- **§4 Data & persistence conventions.** Primary-key type, foreign-key/index policy, enum
  approach, encryption of sensitive fields, soft-delete policy, naming, multi-database posture.
  Feeds the plan's `## Data model / schema impact`.
  *Rails:* native AR enums; Active Record Encryption for PII; Solid Queue/Cache/Cable each on
  their own database by default (so migrations land in the right DB).
- **§5 Domain & cross-cutting conventions.** Error handling (raise vs. return a Result),
  transaction boundaries, job idempotency/retry, money/decimal handling, time-zone policy, i18n,
  feature flags, multi-tenancy. These become the plan's `## Risks & watchpoints` — runtime
  invariants the resolver must *preserve*.
- **§6 HTTP / controllers / routing / API.** Param handling, REST conventions, API versioning +
  serialization, pagination, rate limiting, error-response shape.
- **§7 Front-end conventions.** Your view/interaction stack and its rules. Point UI *component*
  decisions at `ui-design.md`.
  *Rails:* Hotwire — Turbo Frames/Streams usage rules, Stimulus controller conventions; Propshaft
  + importmaps as the asset/JS policy.
- **§8 Background / async / caching.** Queue names + priorities, what may run in-request vs. must
  be a job, cache-key and expiry conventions.
- **§9 Auth & authorization.** Session model, the authentication mechanism, the authorization
  layer, the "who can do what" boundary.
- **§10 Testing strategy & coverage bar.** Test framework, fixture/factory approach, system-test
  tooling, the per-layer coverage expectation. The plan's `## Test plan` is written *per* this
  section (cross-reference `constitution.md §N` if the bar is non-negotiable).
  *Rails:* Minitest + fixtures; Capybara system tests; parallelized.
- **§11 Performance & operational invariants (NFRs).** N+1 / eager-loading expectations, query
  budgets, latency targets — the things the resolver must not regress.
- **§12 External integrations & boundaries.** Third-party services, the adapter / anti-corruption
  pattern, and where credentials live.

Omit sections your project genuinely doesn't have; don't pad. Add stack-specific sections as
needed — the numbering is yours.

## Encoding a stack-wide preference (the "prefer native" pattern)

A project-wide stance like *"prefer native framework features over third-party libraries"* is
exactly the kind of cross-cutting decision the planner can't infer from one file — so encode it
explicitly, as a **deviable default plus a clear bar for deviating**, in §3.

*Rails 8.1 example — §3 as a table:*

| Concern | Default (native Rails 8.1) | Don't reach for |
|---|---|---|
| Background jobs | Active Job + **Solid Queue** | Sidekiq, Resque, GoodJob |
| Caching | **Solid Cache** | Redis/Memcached cache stores |
| WebSockets | **Solid Cable** / Action Cable | external pub/sub |
| Authentication | `bin/rails generate authentication` + `has_secure_password` | Devise |
| Front-end | **Hotwire** (Turbo + Stimulus) + **importmaps** | React/Vue + JS bundler |
| Assets | **Propshaft** | Sprockets, Webpacker |
| File uploads | **Active Storage** | Shrine, CarrierWave |
| Rich text / inbound mail | **Action Text** / **Action Mailbox** | — |
| Tests | **Minitest** + fixtures | RSpec + FactoryBot |
| Deploy | **Kamal** + **Thruster** | — |

Then state the bar in prose:

> Introducing a third-party gem for any concern above requires a deviation approved by the user
> (planner step 6): state the concrete capability the native feature lacks and the maintenance
> cost being accepted.

Because this lives in `architecture.md` (not `constitution.md`), the planner *may* still propose
Sidekiq for a job with a genuine need — it just has to surface the deviation for approval rather
than silently reaching for the popular gem. If a particular constraint is truly absolute (e.g.
"no Redis in production, ever"), put *that* line in `constitution.md` so the planner treats it as
a hard blocker.

*For another stack* the same pattern holds — a Django project might map "async → Celery vs.
native `BackgroundTasks`", a Phoenix project "jobs → Oban", etc. The table changes; the
"default + deviation bar" shape doesn't.

## A worked section, end to end

Doc (`architecture.md §3.2`):

> **§3.2 Background work.** All asynchronous work runs through Active Job backed by **Solid
> Queue** (the Rails 8 default; we run it in the `queue` database). Do not add Sidekiq, Resque,
> or GoodJob. Introducing an alternative adapter requires a step-6 deviation stating the concrete
> capability Solid Queue lacks.

What the planner emits into the plan (and all the resolver ever sees):

> `## Architecture decisions`
> - Report export runs in a background job, not inline — `ExportReportJob < ApplicationJob`,
>   enqueued on the `:low` queue — [architecture.md §3.2]

The rule is stated once, prescriptively, under a stable anchor; the planner cites it; the
resolver inherits a locked decision and never opens the doc. That is the whole game.

## Authoring checklist

Before you consider the doc done:

- [ ] Every section has a stable number and asserts a single governing rule.
- [ ] Every rule is prescriptive (a reader can tell what would *violate* it).
- [ ] No "TBD", no "we might", no surveyed-but-undecided options.
- [ ] Hard constraints are in `constitution.md`; rationale is in `architecture-notes.md`.
- [ ] §2 (layer model) names a directory and dependency rule for every layer.
- [ ] §3 encodes your stack-preference policy with an explicit deviation bar.
- [ ] Versions and relied-on defaults are pinned (§1/§3).
- [ ] Test/build command targets are **not** here (they're in `CLAUDE.md` marker blocks).

**Validate against the real consumer:** run `planner` on a representative issue and
read the posted `<!-- implementation-plan:v1 -->`. Success looks like: every
`## Architecture decisions` bullet carries an `architecture.md §X` (or `[precedent: …]`) citation
that resolves, and the planner needed *no* human Decision-gate for a convention the doc should
have settled. If the planner stops to ask "Solid Queue or Sidekiq?", §3 wasn't prescriptive
enough.

## Anti-patterns

- **Narrative/essay sections** with no single governing claim → citations can't be verified.
- **Hedging** ("we generally prefer…") → no deviation signal fires; the planner can't gate.
- **Renumbering / reshuffling sections** → dangles `§`-citations already posted in plans.
- **Hard rules in `architecture.md`** (planner offers to deviate from something that's
  non-negotiable) **or soft preferences in `constitution.md`** (planner refuses to build a
  legitimate alternative).
- **Pasting code** instead of stating the rule and pointing at the canonical file — the planner
  reads the actual code for `[precedent: path:line]`; the doc's job is the *rule*.
- **Implicit versions/defaults** → the planner web-fetches or guesses framework behavior.
