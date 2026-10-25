# Documentation map

`github-pipeline` is a Claude Code plugin — skill prompts plus stdlib-only Python, no build step and
no compiled artifact. These are the documents that govern it.

Start at [`CLAUDE.md`](../CLAUDE.md) if you are **modifying** the plugin, or
[`README.md`](../README.md) if you are **using** it.

## The grounding set

The block below is this repo's own `<!-- doc-catalogue -->` — the declaration the pipeline reads when
it runs against this repo, per [`skills/_shared/doc-catalogue.md`](../skills/_shared/doc-catalogue.md).
`binding` means a session must raise a conflict rather than design around it; `informative` means the
tension is a judgment call. Any session that adds, moves, removes or rewrites one of these documents
updates its entry in the same change; a `binding` entry added or removed, or any authority changed,
is confirmed by the operator first. Keep the marker pair intact and every line in the entry format.

<!-- doc-catalogue -->
- `CLAUDE.md` — conventions — binding — How to modify this plugin: the facts-by-script/meaning-by-model rule, the editing conventions (contract tokens, stable §-anchors, stack-agnosticism, compression), and the validator greps that stand in for a compiler.
- `docs/prd.md` — prd — binding — What the plugin does and why: the fourteen skills' requirements (§5, §6), the frozen persisted-artifact compatibility contract (§7), the grounding and workspace requirements (§8), and the prompt-economy metric (§10).
- `docs/architecture.md` — architecture — binding — How it is built: the one-envelope contract and closed decision-code set (§3), the facts block (§4), the workspace and grounding model (§6), write discipline (§7), skill anatomy (§9), and the non-deviable invariant registry (§12).
- `docs/implementation.md` — guide — informative — Why the current shape exists: the step-by-step v1→v2 rewrite plan, the parity protocol, and the per-step records. Historical — it explains decisions, it does not set requirements.
<!-- /doc-catalogue -->

## Deliberately not in the catalogue

Both of these are real documentation; neither *grounds* work on the plugin, and a catalogue that
names everything stops being read.

- **`docs/specs/**`** — the frozen behavioral record of v1 plus the live-parity results, exempt from
  every validator by [`CLAUDE.md`](../CLAUDE.md). It is evidence about what v1 *did*, cited when
  adjudicating a divergence, not a requirement for new work.
- **`docs/guides/**`** — see [`docs/guides/README.md`](guides/README.md). These teach a **consuming
  repo's** maintainer how to author the documents the skills read (`prd.md`, `architecture.md`,
  `architecture-notes.md`, `ui-design.md`, `constitution.md`). They describe *another* repo's docs, so
  they constrain nothing about how this plugin is built.

Note the naming collision when reading either tree: `docs/architecture.md` is *this plugin's*
architecture, while `docs/guides/architecture.md` is the guide for writing a consuming repo's.
