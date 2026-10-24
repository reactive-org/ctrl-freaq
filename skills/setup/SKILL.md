---
name: setup
description: Configure (or re-configure) a repository so the github-pipeline skills — resolver, evaluator, planner — actually work in it, by writing the marker-delimited command blocks they read from COMMANDS.md / CLAUDE.md. Use this skill right after installing the plugin, or any time the pipeline can't find how to test/check a repo: phrases like "set up the pipeline", "configure the pipeline for this repo", "onboard this repo to github-pipeline", "the resolver doesn't know how to run my tests", "configure the fast-checks / static-checks", "set up the COMMANDS.md markers", "how do I tell the evaluator which suite to run", "migrate my health-checks block", or "re-run setup" all qualify. Trigger this even when the user doesn't name a specific marker — if they're wiring this plugin into a project, or a pipeline skill reported a missing `<!-- … -->` block, this is the skill. Detects the project's existing lint/test/build commands and proposes drafts; is safe to run repeatedly (idempotent — reconciles in place, never duplicates); offers to migrate legacy single-block declarations and to dry-run the commands it writes. Also proposes a concise, up-to-date `claude-code-stack-profile` block for CLAUDE.md — general guidance on running the repo's tech stack efficiently in a Claude Code session (background slow commands, log verbose output instead of flooding context) — and a `doc-catalogue` block for docs/README.md declaring which documents ground the pipeline's work and how binding each is, so trigger it too for "the planner doesn't know which docs to read", "set up the doc catalogue", "declare my grounding docs", or a skill reporting `DOC_CATALOGUE_ABSENT`. Does NOT itself draft, plan, or resolve issues — it only configures the conventions the other skills depend on, plus that one general operating-guidance block.
---

# setup — router

Make a repository ready for the `github-pipeline` skills. The resolver, evaluator, and planner carry
no per-project config — they read it at use-time from **marker-delimited blocks** the consuming repo
declares in `COMMANDS.md` (preferred) or `CLAUDE.md`. Setup is the single place the **plugin** writes
those blocks: it inventories what's declared, grounds candidates from repo evidence, interviews the
operator for what can't be inferred, confirms every draft as a diff, stages the writes in a workspace,
and **offers** the landing (commit + push + PR) as one final gate. One interactive session; it edits
local Markdown, never GitHub state. Read this router, run prep, run the one flow, close with a summary.
Your judgment is detection/grounding, the interview answers, the diff-and-confirm, and the
research-and-propose blocks.

## 1. Prep

Assemble the entire starting state in **one** call — no `gh` gather (setup's subject is local files):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_setup.py [--root <path>]
```

It returns one JSON **facts block** (`architecture.md §4`): `preflight` (`git_repo` + `tools.jq/git/gh`
presence), `root` (`path`/`sha`), `inventory` (`files` — each candidate file's `blocks`; `known_markers`
— every block classified **present / legacy / malformed / missing** with its file(s); `legacy_health_checks`;
`stack_profile` — present + the staged re-ingest `base_path`; `doc_catalogue` — `readme_present`/`present`/`status` + the staged `readme_path` and re-ingest `base_path`; `config_header`; `same_marker_both_files`),
`target_file` (suggested canonical file + `split`), `scratch`, and `attention`. Consume each as **data**
— never re-scan the files prep already listed. A `needs_decision` from prep has no path here (no `gh`
call); a `malformed` block is a **fact to surface** (§3), not a decision.

## 2. Route — one linear flow

Setup has **no mode fork**: a single linear flow — inventory → detect → propose → stage → validate →
land → summary. Legacy migration and the landing approve/decline are **fact/runtime gates inside** the
flow, never routes. One playbook — read it and run it end to end:

| Facts | Playbook |
|---|---|
| any setup run | [`playbooks/setup-flow.md`](playbooks/setup-flow.md) |

Before drafting any block, the flow forces a `Read` of
[`references/block-authoring.md`](references/block-authoring.md) — exact shape per block, detection
heuristics, the stack-profile scope, the legacy mapping, worked examples. Its canonical block **forms**
are byte-identical to the frozen schema in [`config-blocks.md`](../../docs/specs/examples/config-blocks.md)
(prd.md §7, row 11) — except blocks v1 never had (`doc-catalogue`, `pr-evaluator-sensitive-paths`), whose
fence is block-authoring.md's own — write exactly those bytes; never restate a form divergently.

## 3. Invariants

- **`config_block.py` is the single write path.** Every block read/write goes through it
  (`read`/`list`/`upsert`/`remove`) — never hand-roll `sed`/`Edit`/`Write` against a marker block.
  Idempotency lives in that code, not in a byte-perfect edit; `upsert` returns `changed: false` when
  already correct. Stage each body to `facts.scratch/<marker>.md`, then pass the **path** (the
  `gh_persist.py` discipline — nothing re-serializes a body across a prompt boundary).
- **Malformed input is refused, not guessed.** A `dup`/`open` block means the repo is already in a state
  the resolver/evaluator parsers trip on — surface it via `AskUserQuestion` for a hand fix.
- **`claude-code-stack-profile` and `doc-catalogue` are user-owned: re-ingest, never overwrite.**
  Machine-parsed blocks + the `github-pipeline-config` header are plugin-owned, reconciled to canonical
  every run; these two are seeded when absent and, when present, kept as the base (`*.base_path`) with
  only refinements layered on. Their homes are fixed (`CLAUDE.md`, `docs/README.md`).
- **Everything lands via an operator-gated PR (prd.md §8.2).** Setup never edits the read-only root:
  approved writes are staged in a **work workspace**, and the landing (commit + push + a PR whose body
  summarizes the block diffs) is **one explicit final gate**. On decline: **no git actions** — the
  summary reports the workspace path + ready-to-run landing commands.
- **Preflight reports gaps; it never auto-fixes.** A missing `jq`/`git`/`gh` or an unauthenticated `gh`
  is surfaced with a one-line fix — setup never logs in, installs, or touches the machine. Leave the
  *runtime* markers (`implementation-plan:v1`, `issue-research:v1`, the health-cache marker) alone.

## 4. Summary — not a `## Handoff`

Setup is **not** a pipeline stage: no cross-session handoff, no GitHub state. It ends with a plain
**summary** (the flow's last step): the target file(s) + per-block disposition (written / reconciled /
already-correct / skipped; the stack-profile and doc-catalogue in their shared seeded / refreshed /
already-current vocabulary), the landing outcome (PR opened with the block-diff summary, **or** the workspace path +
ready-to-run commands on decline), outstanding preflight `✗`s, and a copy-pasteable next-step pointer
(`/github-pipeline:drafter` on your first feedback, or `/github-pipeline:resolver <issue#>`) — a
pointer, not a pipeline command, since there is no session state to carry.
