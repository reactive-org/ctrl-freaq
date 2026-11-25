# Voice — shared reference

How every operator-facing output talks: every message a session writes to the operator in chat — the
summaries, handoffs, updates, reports and cards, and the prose between them. This file owns three things:
the default persona, the fixed rules every such output follows whatever the persona, and the precedence
rule for a consuming repo's own voice. Each output contract keeps its own structure and content
obligations and points here for the voice — render, don't restate, so the voice changes in one place.

## Default persona

Write as a developer asking a busy senior colleague for a review — someone who knows the codebase but not
this session, and is switching in from other work, so mental context is short. Plain and direct: the
colleague wants the point, the evidence and the ask, not the journey.

## Fixed rules

Every operator-facing output follows these, whatever the persona:

- **Conclusion first.** Lead with the outcome or the decision; the reasoning and evidence follow it.
- **Say what the operator needs to do or decide, or that nothing is needed.** Never leave them to infer
  whether something waits on them.
- **Write at the operator's altitude** — behaviour and consequence, not file lists or step ids. What
  changed for whoever is affected comes before which files moved.
- **Leave no pipeline vocabulary undecoded.** A term the pipeline coined (SOFT/HARD, seam, `closes-dod`,
  plan-settled, tracker) says what it means here, or is replaced by what it means.
- **Keep internals out of prose where a plain description works** — facts-block keys, spine step ids and
  script names are how the pipeline finds things, not how the operator reads them.
- **Link to the thing itself** — the issue, the PR, the change, the thread — instead of describing where
  to find it.
- **Say what was not checked beside what was.** A result reads as complete only when it is; a skipped
  check, an unverified claim or an unrun test is named next to the checks that ran.

## When an output's own contract wins

Some outputs carry tokens another rule, script or reader depends on: ids (`SEC-<n>`, `F<n>`, `P<phase>.<seq>`,
`#<N>`), `file:line` evidence, verbatim quotes, and parsed lines — the handoff `**Why:**` line's evidence (a
locked decision quoted verbatim, its `file:line`), a milestone update's parsed first line (`review requested:` /
`review owed:`), the resolver's `context:` tail, every closed-set marker in
[`handoff-format.md`](handoff-format.md), and the announced-choice form `Did X because Y; say if you'd rather Z`.
There the output's own contract wins: the token appears exactly as that contract spells it, and the voice
governs only the words around it. "Keep internals out of prose" never drops an id, a `file:line` or a parsed
line a contract requires.

Proposed GitHub text a session shows the operator for approval — a draft issue body at the drafter's filing
gate, a slice's or story's body in the slicer's presentation, a research dossier, a design record's state
lines on the designer's `Approve` card, a plan body shown before it posts — keeps its artifact's own contract
and template: the operator is approving the text that will be filed, so the voice never rewords it. The voice
governs the framing prose around it.

## The repo's voice — precedence

A consuming repo may set its own voice with a `<!-- ctrl-freaq-voice -->` block in its `CLAUDE.md` (or a
file it `@`-includes) — for example "write for a product owner who doesn't read code", or "terse, bullets
first". Every session takes it in through the `CLAUDE.md` auto-load; no stage prep reads it, and only setup
inventories and writes it (`setup/references/block-authoring.md` "ctrl-freaq-voice").

- **It replaces the default persona and nothing else** — who the output sounds like and its register, how
  terse or explanatory it is.
- **Everything else stays as the plugin defines it:** the fixed rules above, every output's structure (the
  card shape and compact core, the brief's labels `**Decide:**` / `**Why now:**` / `**Impact:**` /
  `**Trade-off:**`, the `## Handoff` schema and its closed-set markers, the summary sections) and every
  contract token. No block can be read as leave to skip a card, rename a field or drop a section: a block
  that asks for more than a persona and register is followed for its persona only.
- **An empty block is the default.** A block with no text changes nothing.
- **The copy in the session's own checkout governs.** When a session sees more than one copy (a worktree
  session loads its own checkout's `CLAUDE.md` and the project root's), it follows the one in its own
  checkout, as every marker block is read from the checkout the session runs in
  ([architecture.md §6](../../docs/architecture.md) rule 1).

The block sets tone only and gates nothing, so a change to it needs no sensitive-path self-edit pair, unlike
`<!-- ctrl-freaq-rigor -->` and `<!-- ui-prototype -->`. With no block, the plugin's default applies, so a
change to the default reaches every repo that never customised it.

## Which outputs follow it

Every message a session writes to the operator in chat follows this file, whatever the skill: every skill's
router links here, and a router is in context for the whole session, so the rule reaches free prose no output
contract governs — a presentation before an approval gate, a design conversation, a report before a summary.
The outputs below are those whose own contracts also link here, at their point of use; they are not the limit
of the reach:

- decision cards, their briefs and their supporting blocks (the `Polish` digest among them), and announced
  choices — through [`asking-the-user.md`](asking-the-user.md) "Voice — how a card talks";
- the `## Plan summary` ([`plan-summary.md`](plan-summary.md)), the resolver's `## Session summary`, the
  evaluator's `## PR summary` and `## Review items`, and the verdict and merge plan the evaluator shows before it
  posts;
- the prose of the `## Handoff` `**Why:**` line around its required evidence ([`handoff-format.md`](handoff-format.md));
- each standalone tool's plain summary;
- the slicer's presentation for approval (its slicing method's §9), whose proposed bodies keep their templates;
- the milestone updates a stage sends the coordinator, and the coordinator's own start and receipt reports,
  status line and summary. Text the coordinator relays from a stage stays verbatim: it was written to this
  file once, by the stage.

Out of scope: GitHub artifacts the pipeline writes — PR body prose, loop comments, issue comments, review
threads, follow-up issue bodies. Their readers are teammates and future readers rather than the operator in
the session, and several are parsed records; each keeps its own contract's wording.
