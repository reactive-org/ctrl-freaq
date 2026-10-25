# Guide: writing `docs/ui-design.md`

One of a set of authoring guides for the documents the **ctrl-freaq** skills read
(`prd.md`, `architecture.md`, `architecture-notes.md`, `ui-design.md`, `constitution.md`).
This one covers the UI-design doc.

The principles here are **stack-neutral**. The worked examples use **Ruby on Rails 8.1**
(a project that prefers native framework features over third-party gems — here, server-rendered
views with Hotwire), but the structure applies to any stack — swap the example content for your
framework's UI equivalents.

## Why this guide exists: it's the authority for every UI surface

In this pipeline your UI-design doc's primary reader is **not a human** — it's the
`planner` skill, on any issue that touches a UI surface. Its distinguishing job is
narrow and specific: it is **the authority the planner grounds UI decisions in**, so the planner
reuses the components, sizes, and patterns you *named* rather than inventing new ones.

The planner is told, verbatim, to "ground UI decisions in named components, sizes, and patterns it
defines rather than inventing new ones." That instruction only has teeth if your doc actually
*names* things the planner can cite. The consumption chain is one-directional and lossy by design:

```
docs/ui-design.md  ──read by──▶  planner  ──emits──▶  <!-- implementation-plan:v1 -->
                                                                 │  (## UI decisions)
                                                       read by resolver
                                                       (writes the code)
```

The **resolver never reads `ui-design.md`.** It grounds its implementation in the plan's locked
sections (`## Doc grounding`, `## UI decisions`, `## Changes`, …) and treats them as the decisions
it builds against. So whatever the planner failed to cite from your doc is simply not available
when the code gets written:

> **A vague UI-design doc means the planner has no named component to cite — so it invents one. The
> resolver then builds that invented component, and your UI drifts: two issues that touch the same
> surface ship two slightly different cards. Every UI primitive you fail to make *citable* becomes
> a primitive the pipeline hallucinates.**

Your job, when authoring the doc, is to be the **named, citable catalog of UI primitives** — the
components, the size/spacing scale, the interaction and state patterns — that any plan touching the
UI can point at instead of guessing.

## Know your reader: how the planner consumes the doc

Four mechanics determine how you should write:

1. **It's read per-issue, only when a UI surface is touched — not always-in-context.** Unlike
   `constitution.md` (which `CLAUDE.md` `@`-includes, so it sits in every session's context),
   `ui-design.md` is read on demand by the planner's Step-5 doc sweep when the issue has a UI
   surface. Its cost is paid per relevant issue, not per run — so it can be a fuller catalog than
   the constitution, but it still earns its length by being citable, not narrative.
2. **It's cited by stable section anchor — the planner does not quote it.** The plan carries a
   dedicated `## UI decisions` section, and each bullet's citation is `ui-design §X` (or a
   `DEVIATION (agreed <date>)` marker). That section is *omitted entirely when the issue has no UI
   surface*. Your anchors are the citation targets — keep them stable.
3. **An isolated plan-reviewer verifies the citation grounds the claim.** The reviewer's
   precedent-grounding dimension requires **every** `## UI decisions` entry to cite `ui-design.md`
   precedent, a named existing component, or a `[user decision <date>]` marker. A UI decision with
   no such citation is flagged as under-grounded; a citation that points at a section that doesn't
   say what the plan claims fails verification. Stable anchors and one-primitive-per-section are
   what keep `§X` meaning the same thing after you edit the doc.
4. **A `ui-design.md` rule is a *deviable default*, not a hard blocker.** Like `architecture.md`
   (and unlike `constitution.md`), if a plan wants to depart from your UI doc the planner stops at
   its Step-6 gate and asks the user to approve the deviation — recording it as
   `DEVIATION (agreed <date>)`. It does not *refuse* to build the alternative. So a UI rule you
   want mechanically un-deviable doesn't belong here; it belongs in `constitution.md`.

## Authoring principles

Each principle is stated neutrally, then illustrated for Rails 8.1.

1. **Define named, citable primitives — not prose about look-and-feel.** The single highest-value
   thing this doc does is give the planner *names to cite*: components, a spacing/size scale,
   interaction patterns, and component states. If a UI concept doesn't have a name and an anchor,
   the planner can't reuse it.
   *Rails:* a component catalog entry — "`SectionCard` (`app/components/section_card_component.rb`):
   the standard bordered container for a titled content group" — gives the planner something to cite
   as `ui-design §X` and a real class to reuse, instead of inventing `PanelBox`.

2. **Give every section a stable, numbered anchor.** A citation should resolve to one checkable
   primitive. Don't renumber casually — a posted plan's `ui-design §X` dangles the moment you
   reshuffle.
   *Rails:* `§2.3 Buttons` should resolve to your one button component and its variants, forever —
   not move to `§2.5` next quarter.

3. **Be prescriptive: one component or pattern per section.** State the decision ("the bordered
   container is `SectionCard`"), not the landscape ("there are a few container styles"). A survey
   of options produces *no deviation signal*, so the planner can't gate on it — the "options"
   become open questions it must resolve by inventing.
   *Rails:* "Primary actions use `ButtonComponent` with `variant: :primary`" — not "buttons can be
   styled a few ways depending on context."

4. **One source of truth per visual decision.** Each size, spacing token, color role, or
   interaction pattern is defined in exactly one place and cited from everywhere. Two sections that
   both define "the card padding" will drift, and the planner can cite the stale one.
   *Rails:* define the spacing scale once in `§1 Design tokens` (`--space-3 = 0.75rem`) and have
   every component section reference it, rather than restating pixel values per component.

5. **Place each UI rule by its *deviability*.** A strong-default-but-deviable-with-approval UI
   convention belongs here, where the planner can run a Step-6 deviation gate. A UI rule that is
   *never* OK to break (e.g. an accessibility floor you mechanically enforce) belongs in
   `constitution.md`, which the planner treats as a hard blocker.
   *Rails:* "Use `SectionCard` for grouped content" is a deviable default (→ here). "Every
   interactive control has an accessible name" is inviolable (→ `constitution.md`).

## What belongs here vs. the sibling docs

| Content | Put it in | Why |
|---|---|---|
| Named UI components, the size/spacing scale, visual patterns, component states | `ui-design.md` | Planner cites `ui-design §X` for UI decisions specifically |
| Structure, layers, where logic lives, the front-end *stack* (e.g. "we use Hotwire") | `architecture.md` | The view *stack and its rules* are an architecture decision; the *components* are UI |
| A UI rule that's never OK to break (e.g. an accessibility floor) | `constitution.md` | Planner treats it as a hard blocker, not a deviation to negotiate |
| Product behavior — what a screen is *for*, the user flow, acceptance criteria | `prd.md` | Drives *what* gets built; `ui-design.md` governs *how it looks/behaves* |
| *Why* a visual decision was made; rejected alternatives | `architecture-notes.md` | Planner reads it when tempted to deviate; keeps `ui-design.md` prescriptive |

The seam with `architecture.md` is the common confusion: `architecture.md §7` says "Hotwire is our
front-end stack — Turbo Frames/Streams usage rules, Stimulus conventions"; `ui-design.md` says
"here are the named components and the Turbo interaction patterns those rules produce." The stack
is architecture; the catalog of named primitives is UI.

## Recommended outline

The **purpose** of each section is universal; the **example content** is Rails 8.1. Number your
sections and keep the numbering stable.

- **§1 Design tokens / the size & spacing scale.** Your single source of truth for spacing, sizing,
  typography scale, and color *roles* (not raw hex scattered through prose). Every component section
  cites back to this.
  *Rails:* a token table — spacing scale (`--space-1…6`), the type scale, semantic color roles
  (`--color-surface`, `--color-danger`) — defined once.
- **§2 Component catalog.** The named, reusable building blocks the planner reuses by name. One
  subsection per component: its name, where it lives, its purpose, its variants, and the props/slots
  it takes. This is the section the planner cites most.
  *Rails:* ViewComponents or partials, *named explicitly* — `SectionCard`, `ButtonComponent`,
  `FormFieldComponent`, `EmptyStateComponent` — each with its file path and variant list.
- **§3 Interaction patterns.** How dynamic behavior is expressed, as named conventions the plan can
  cite rather than re-decide per issue.
  *Rails:* Hotwire/Turbo conventions — when a surface uses a Turbo Frame vs. a Turbo Stream, the
  naming convention for frame IDs, how a list appends a new row, the Stimulus controller naming
  rule.
- **§4 Component states.** The required states every interactive surface must render, so the planner
  always plans them and the resolver never forgets one.
  *Rails:* the loading / empty / error / success states a Turbo-driven list must define, and the
  named partial or component that renders each (`EmptyStateComponent`, the error flash pattern).
- **§5 Form patterns.** How forms are built, validated, and surface errors — a high-reuse surface
  worth one prescriptive home.
  *Rails:* the form builder/component, inline-error placement, the required/optional affordance, how
  a failed submit re-renders.
- **§6 CSS / styling approach.** The styling system and its conventions, so new styles follow the
  existing one rather than inventing a parallel approach.
  *Rails:* the utility-vs-component-stylesheet policy (e.g. Tailwind utility conventions, or a
  per-component stylesheet under `app/components/`), and the rule for when a new class is warranted.

Omit sections your project genuinely doesn't have; don't pad. The principle is constant — **define
named primitives the planner can cite**; the section list is yours.

## A worked example, end to end

Doc (`ui-design.md §2.1`):

```
## §2.1 SectionCard

The standard bordered container for a titled group of content. Defined as
`SectionCardComponent` (`app/components/section_card_component.rb`).

- Use it for every titled content group on a page; do not hand-roll a bordered `div`.
- Slots: `title:` (required), default slot for body content.
- Variant: `emphasis: :default | :warning` — `:warning` applies the `--color-warning`
  surface role (see §1).
- Padding and border-radius come from the §1 token scale; never inline pixel values.
```

What the planner emits into the plan (and all the resolver ever sees):

```
## UI decisions
- Group the new "export history" rows inside an existing SectionCard, title "Past exports",
  rather than a new container — [precedent: ui-design §2.1]
```

The primitive is named once, prescriptively, under a stable anchor; the planner cites it; the
resolver inherits a locked decision — reuse `SectionCardComponent` — and never opens the doc. That
is the whole game: the named component survived the lossy handoff because it was *citable*.

## Authoring checklist

Before you consider the doc done:

- [ ] Every reusable UI building block has a **name** and a stable section anchor.
- [ ] Every section asserts a single primitive/pattern (a citation resolves to one checkable thing).
- [ ] Components name their real file/class so the resolver reuses code, not a description.
- [ ] The size/spacing/token scale is defined **once** and cited from each component, not restated.
- [ ] Each rule is prescriptive (a reader can tell what would *deviate* from it).
- [ ] Inviolable UI rules (e.g. accessibility floors) are in `constitution.md`; the front-end *stack*
      is in `architecture.md`; rationale is in `architecture-notes.md`.
- [ ] No "TBD", no surveyed-but-undecided component options.

**Validate against the real consumer:** run `planner` on an issue that touches a UI
surface and read the posted `<!-- implementation-plan:v1 -->`. Success looks like: every
`## UI decisions` bullet carries a `ui-design §X` citation that resolves, and the planner **reused a
named component** from your catalog instead of inventing a new one. If the plan invents `PanelBox`
when you have `SectionCard`, your catalog wasn't named or citable enough — and that invented
component is what the resolver would have built.

## Anti-patterns

- **Mood-board prose** ("the UI should feel clean and modern") → nothing the planner can cite; it
  invents primitives to fill the gap.
- **Unnamed components** ("use a card-style container") → the planner can't cite a name, so each
  issue builds a slightly different card and the UI drifts.
- **Surveyed options** ("buttons can be primary, secondary, or ghost depending") with no rule → no
  deviation signal fires; the "options" become open questions resolved by guessing.
- **Renumbering / reshuffling sections** → dangles `ui-design §X` citations already posted in plans.
- **Restating sizes per component** instead of citing the one token scale → the planner can cite a
  stale copy after you edit the canonical one.
- **Inviolable UI rules left here as a "default"** → the planner offers to *deviate* from something
  that should be a hard blocker; move it to `constitution.md`.
- **Pasting component source** instead of naming the rule and pointing at the class — the resolver
  reads the real code; the doc's job is the *named, citable primitive*.
