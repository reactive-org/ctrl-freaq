# Guide: writing `docs/architecture-notes.md`

One of a set of authoring guides for the documents the **ctrl-freaq** skills read
(`prd.md`, `architecture.md`, `architecture-notes.md`, `ui-design.md`, `constitution.md`).
This one covers the architecture-notes doc — the *why* companion to `architecture.md`.

The principles here are **stack-neutral**. The worked examples use **Ruby on Rails 8.1**
(a project that prefers native framework features over third-party gems), but the structure
applies to any stack — swap the example content for your framework's equivalents.

## Why this guide exists

`architecture.md` states *what* your architecture is. `architecture-notes.md` records *why* —
the rationale, the rejected alternatives, the history behind each decision. The architecture
guide's principle 3 ("Split *what* from *why*") names this split and tells you to put the
rationale here; this guide is the other half of that split.

Two properties set this doc apart, and both shape how you write it.

**1. It is rationale, not rules.** Nothing in it is prescriptive, and nothing in it is a gate.
Where `architecture.md` is a *deviable default* and `constitution.md` is a *hard blocker*, this
doc has **no enforcement role at all** — it neither permits nor forbids. Its only job is to
*inform* the deviation decision: when the planner is about to depart from a rule, the recorded
"why" either talks it out of the deviation or grounds the deviation in stated intent. So you
never write "do X" here; you write "we chose X over Y because Z".

**2. It is read on-demand, not always-in-context.** Unlike `constitution.md`, this doc is **not**
`@`-included by `CLAUDE.md`, so it does not sit in every session's context and you pay no
permanent context tax for its length. The `planner` reads it at one specific
moment — when it's tempted to deviate from `architecture.md` — and reads only the note relevant
to that decision. That buys you room to be fuller than the constitution can afford, but it shifts
the burden onto **navigability**: a note the planner can't *find* at the deviation moment may as
well not exist.

> **The mechanic to design for:** a planner tempted to deviate from `architecture.md §X` reads
> the rationale for §X here. The rationale often already addresses the case, so the deviation
> evaporates — or, if a deviation really is warranted, it lands grounded in stated intent rather
> than guesswork. A good notes doc *reduces deviation churn* by pre-recording the "why".

## Know your reader: how the planner consumes the doc

Three mechanics determine how you should write:

1. **The planner reads it on-demand, at the deviation moment.** The `planner`'s deviation step
   reads it with the instruction: *"Read this when you're tempted to deviate; the rationale often
   already addresses your case, and citing it keeps the plan aligned with intent rather than just
   letter."* It is not read on every plan and not read whole — so a note is useful only if it's
   reachable from the `architecture.md` rule that prompted the deviation.
2. **It is cited by stable section anchor as `architecture-notes §Y`.** In a plan, an entry under
   `## Architecture decisions` may carry the citation `architecture-notes §Y` — a valid
   precedent/grounding citation alongside `[precedent: path:line]` and `architecture.md §X`. The
   plan's `## Doc grounding` lists these citations; it does **not** restate them.
3. **An isolated plan-reviewer verifies the citation resolves and governs the claim.** When a plan
   cites `architecture-notes §Y`, the reviewer reads §Y (at the plan's git ref) and checks that it
   actually says what the plan claims. A note that doesn't address the decision it's cited for is a
   review finding, the same way a dangling `architecture.md §X` is. Stable anchors and
   one-rationale-per-section are what keep `§Y` meaning the same thing after you edit the doc.

## Authoring principles

Each principle is stated neutrally, then illustrated for Rails 8.1.

1. **Record the *why* and the *rejected alternatives*, not the rule.** The rule already lives in
   `architecture.md`. Your content is what that doc deliberately leaves out: the reasoning, the
   options you evaluated and dropped, the history. If a note could be pasted into `architecture.md`
   unchanged, it's a rule in the wrong file.
   *Rails:* `architecture.md` says "Use Solid Queue, not Sidekiq." The note says: "We dropped Redis
   in the 8.0 upgrade; Solid Queue was the only piece still pulling it in, so adopting it removed
   our last Redis dependency. Sidekiq would re-introduce Redis for marginal throughput we don't
   need."

2. **Anchor each note to the decision it explains.** The planner arrives here *from* an
   `architecture.md` rule, so the note must be findable from that rule. The simplest reliable
   scheme is to **mirror `architecture.md`'s section numbers**: `architecture-notes §3` explains
   `architecture.md §3`, `architecture-notes §3.2` explains `architecture.md §3.2`. A rationale the
   planner can't trace back to its rule is a rationale it won't read.
   *Rails:* if `architecture.md §3.2` is "Background work → Solid Queue", the matching rationale
   lives at `architecture-notes §3.2`, headed for the decision it explains.

3. **Write so the rationale answers the obvious "why not X?" deviation question.** The planner
   reaches a note because it's considering an alternative. Write each note to pre-empt the
   alternative a fresh implementer would reach for — frame the heading as the question they'd ask
   ("Why Solid Queue and not Sidekiq?") and answer it. A note that explains the decision but never
   addresses the tempting alternative leaves the deviation live.
   *Rails:* don't just say "we like Solid Queue"; say which capability of Sidekiq you weighed and
   why it didn't justify the dependency — that's the line that closes the deviation.

4. **Keep it durable.** Rationale outlives line-level churn — *why* you chose an approach changes
   far less often than the file paths and signatures that implement it. Write the reasoning and the
   tradeoff, not today's class names. A note pinned to a specific method or version rots the moment
   the code moves; the decision behind it usually doesn't.
   *Rails:* "service objects keep multi-model workflows out of fat models" stays true across
   refactors; "see `ReportService#generate` line 40" does not.

5. **Navigable over terse.** You pay no always-in-context tax here, so you have room the
   constitution doesn't — use it for clarity, not volume. But the planner reads only the *one*
   relevant note, so the win comes from **findability**: clear anchored headings, one decision per
   section, the alternative named in the heading. Fuller than the constitution; never a wall of
   prose the planner has to scan to find the §Y it came for.

## What belongs here vs. the sibling docs

The split runs in both directions: the rule goes one place, the reasoning behind it another.

| Content | Put it in | Why |
|---|---|---|
| *Why* a decision was made; rejected alternatives; history | `architecture-notes.md` | Read on-demand when the planner is tempted to deviate; cited as `architecture-notes §Y` to ground a plan in intent |
| The prescriptive rule / decision / API the reasoning is *about* | `architecture.md` | Planner cites `architecture.md §X` for the shape of the solution; deviating from it runs a user-approval gate |
| A rule that is never OK to break (no deviation, ever) | `constitution.md` | Enforced as a hard blocker pipeline-wide; rationale for *why* it's inviolable still belongs here |
| UI rationale tied to a visual pattern | `architecture-notes.md` (the *why*) + `ui-design.md` (the *what*) | Same what/why split; the planner cites `ui-design §Z` for the rule |
| Test/build/gate **command targets** | `CLAUDE.md` / `COMMANDS.md` marker blocks | Read by the resolver/evaluator at run-time, not the planner-grounding path |

`architecture-notes.md` itself = the *why* behind the decisions, with the alternatives you rejected
and the reason you'd revisit. If a note tells the planner *what to do*, it's misfiled — move the
rule to `architecture.md` and leave the reasoning here.

## Recommended shape

A list of decision-keyed notes, each headed by the **question a deviating implementer would ask**
and anchored to the matching `architecture.md` section. The decision-as-question heading is what
makes the note answer the deviation; the mirrored anchor is what makes it findable. The *shape* is
universal; the example content is Rails 8.1.

- **`§3 Why Solid Queue and not Sidekiq?`** — the alternatives evaluated (Sidekiq, Resque,
  GoodJob), the deciding factor (removing the last Redis dependency in the 8.0 upgrade), and the
  *one* driver that would justify revisiting (a concrete throughput or scheduling capability Solid
  Queue lacks). Anchored to `architecture.md §3` (the stack-preference table).
- **`§2 Why service objects rather than fat models?`** — what pulled multi-model workflows out of
  Active Record models, the cohesion/testability tradeoff, and what *wouldn't* warrant a service
  (single-model behavior, which stays on the model). Anchored to `architecture.md §2` (the layer
  model).
- **`§4 Why Active Record Encryption and not an external KMS?`** — the threat model that the native
  approach covers, what it deliberately doesn't, and the bar (a compliance requirement) that would
  flip the decision. Anchored to `architecture.md §4` (data conventions).

Each note: name the alternatives, name the deciding factor, name the condition that would reopen
the question. That last part is what lets the planner ground a *legitimate* deviation in your
stated intent instead of guessing.

## A worked note, end to end

The prescriptive rule and its rationale live in sibling docs, anchored to the same number:

```markdown
# architecture.md

## §3.2 Background work
All asynchronous work runs through Active Job backed by **Solid Queue** (the Rails 8 default;
we run it in the `queue` database). Do not add Sidekiq, Resque, or GoodJob. Introducing an
alternative adapter requires a step-6 deviation stating the concrete capability Solid Queue lacks.
```

```markdown
# architecture-notes.md

## §3.2 Why Solid Queue and not Sidekiq?
We evaluated Sidekiq, Resque, and GoodJob during the 8.0 upgrade. Solid Queue won on one
deciding factor: it runs on the existing database and let us drop Redis, which Sidekiq was the
last component still requiring. The throughput edge Sidekiq offers doesn't matter at our volume.
We'd revisit only if we needed a feature Solid Queue genuinely lacks (e.g. a specific scheduling
or rate-limiting capability) — and that case is exactly the step-6 deviation `architecture.md §3.2`
calls for.
```

Now a plan touches a high-volume export and the planner is tempted to reach for Sidekiq. It reads
`architecture-notes §3.2`, finds the throughput concern already weighed and dismissed for this
volume, and **drops the deviation** — emitting instead:

```markdown
## Architecture decisions
- Report export runs on Solid Queue, not a Sidekiq adapter — throughput at our volume doesn't
  justify re-introducing Redis — [architecture-notes §3.2]
```

The decision stays aligned with intent, grounded in a citation the plan-reviewer can verify,
without a human round-trip. Had the volume genuinely exceeded what §3.2 anticipated, the same note
would instead *ground the deviation*: the planner surfaces it at the step-6 gate citing
`architecture-notes §3.2` for the stated reopen condition, and the `## Deviations from project docs`
entry rests on intent rather than guesswork. That is the whole game — the note decides the
deviation either way.

## Authoring checklist

Before you consider the doc done:

- [ ] Every note records the *why* and the rejected alternatives — never the rule itself.
- [ ] Every note is anchored to the `architecture.md` section it explains (mirror the numbers).
- [ ] Every heading names the alternative a deviating implementer would reach for, and the note
      answers it.
- [ ] Every note names the condition that would justify revisiting the decision.
- [ ] The reasoning is durable — no pinning to a method, line, or version that will churn.
- [ ] Headings are navigable; one decision per section; no rule, no command, no PRD content.

**Validate against the real consumer:** run `planner` on an issue that *tempts a
deviation* from `architecture.md`. Success looks like the planner either complying — citing
`architecture-notes §Y` for the rationale that closed the case — or surfacing a deviation at the
step-6 gate that is grounded in the stated reopen condition. A planner that guesses, or asks the
user a question the rationale already answered, means the relevant note was missing, unanchored, or
didn't address the tempting alternative.

## Anti-patterns

- **Restating the rule instead of the rationale** ("Use Solid Queue") → that's `architecture.md`'s
  job; here it carries no information the planner doesn't already have.
- **Rationale with no anchor to its decision** → the planner can't trace it back from the
  `architecture.md §X` it's deviating from, so it never reads it and the deviation churn returns.
- **Prescriptive rules or commands in the file** → the planner has no enforcement path for this
  doc; a "rule" here is invisible. Move it to `architecture.md` (deviable) or `constitution.md`
  (inviolable).
- **A note that explains the decision but ignores the tempting alternative** → leaves the
  "why not X?" question open, so the deviation it was meant to close stays live.
- **Letting it rot out of sync with `architecture.md`** → a renamed/renumbered rule dangles its
  matching note, and an `architecture-notes §Y` citation in an old plan stops resolving for the
  reviewer.
- **Pinning rationale to a line or version** → the reasoning outlives the code, but a note keyed to
  `ReportService#generate:40` or "Rails 8.1 default" rots the moment that detail moves.
