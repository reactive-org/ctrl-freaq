---
name: researcher
description: You're prepping a filed GitHub issue for planning and it hinges on something the model may not recall accurately — a dependency/SDK/framework/platform version, a migration path, breaking changes, current API behavior or defaults, or vendor guidance. Use this skill to look it up on the web, verify it against authoritative/official sources, and post a dated, cited research summary as a comment on that issue. Trigger on requests to pull a current migration guide or breaking changes for #N, check the latest behavior of a named technology, verify a claim against official docs, or re-ground research a planner flagged as stale — then write it onto the ticket. Common framing is "my memory/training is out of date, ground this in real docs before we plan #N"; even so, the goal is gathering external truth, not designing. Do NOT use for: filing a new issue (drafter); settling the approach or architecture or file changes (planner); writing or fixing code (resolver); reviewing or merging a PR (evaluator); the project's own internal code; or quick questions with no issue and no currency risk.
---

# researcher — router

Turn a filed GitHub issue into a verified, durable **research dossier** of current external truth the
planner ingests before it designs the approach — *what does whoever plans this need to know from the
outside world that the model can't reliably recall?* One session; nothing survives except the marker
comment. Read this router, run prep, run the one routed playbook, hand off. Your judgment is the
currency-risk / decline call, question derivation, source tiering, synthesis, and the `Why:`.

## 1. Prep

Assemble the entire starting state in **one** call. `--question "<q>"` carries the operator's
`— <question>` from a targeted trigger (repeatable):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_researcher.py <issue> <owner/repo> [--question "<q>"]
```

It returns one JSON **facts block** (`architecture.md §4`): `vector.mode` (`broad`/`targeted`/`revise` — the
routing contract) + `vector.questions`, `suggested_playbook`, `target` (number/title/state/labels), `inventory`
(the `manifests`+`docs` presence lists the currency check reads — each `present`+`files`), `sections` (spilled
issue-body/thread/dossier-marker paths), `revise` (the existing dossier's `comment_id`/`comment_url` + staged
body — revise mode only), and `attention`. Consume each as **data**, never re-deriving the mode or inventory.

**Decision card rule.** If prep exits `status: needs_decision`, render its `decision` as one
`AskUserQuestion` card with its brief (per [`../_shared/asking-the-user.md`](../_shared/asking-the-user.md)), act, and
re-run prep — the single universal handler for every closed-set code (`AUTH_REQUIRED`; `MARKER_AMBIGUOUS`
when more than one dossier comment matches — disambiguate which is the live dossier).

## 2. Route

Prep proposes `suggested_playbook`, keyed on `vector.mode`. Read **exactly one** playbook; it supplies its
mode's distinct front-end and then runs the shared spine
[`playbooks/research-spine.md`](playbooks/research-spine.md) (gather → synthesize → validate → persist →
handoff). The modes differ in genuine actions (broad can decline and post nothing; revise delete-and-reposts
and shows a diff), so each is its own thin variant — the route *is* the branch, no interleaving.

| `vector.mode` | Playbook | Front-end it supplies before the spine |
|---|---|---|
| `broad` (no dossier, no question) | `playbooks/broad.md` | discover stack → derive questions + decline gate + confirm gate |
| `targeted` (no dossier, question given) | `playbooks/targeted.md` | discover stack → use the given `vector.questions` (skip derivation + confirm) |
| `revise` (dossier exists) | `playbooks/revise.md` | read the prior dossier → refresh only what changed (the spine's persist delete-and-reposts + shows a diff) |

No route override: `vector.mode` is fully mechanical (dossier presence + `--question`). The one judgment
fork — the **decline gate** — lives *inside* `broad.md`, never as routing (a no-currency-risk issue posts
nothing and hands straight to the planner with `research: ✗`; declining well is a correct outcome).

## 3. Invariants

- **Research is input, never authority.** The dossier reports external truth, implications, and tensions
  (a clearly-marked strawman is allowed); it **never** posts a plan, settles an architecture decision, or
  edits code. It flows *through* the planner, which owns every decision; the resolver reads only the plan.
- **Web access is mandatory; recall is never a fallback.** If `WebSearch`/`WebFetch` is unavailable, stop
  and say so — a dossier from memory is indistinguishable from a real one, so the failure must be loud.
  **Fetch, don't recall:** every claim traces to a page fetched *this run*, with its fetch date.
- **Staged-body writes.** Every GitHub write goes through `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py`:
  stage the verbatim body (`<!-- issue-research:v1 -->` as the literal first line) to
  `facts.scratch/research.md`, pass the **path** — the script gates empty bodies (`EMPTY_BODY_FILE`),
  returns `body_sha256`, so no body crosses a dispatch prompt (#626/#627). The persist mechanics (revise
  `--delete-marker-id` post-before-delete; the `researched` label) are in the spine's `S-persist`. No
  scriptless raw-`gh` executor — an op that doesn't fit a subcommand is a gap to report; a zero exit with
  a URL is self-confirming, never re-read.
- **Governing-doc conflicts are surfaced under `## Tensions for the planner to resolve`, never decided.**
  A judgment sub-agent never calls `AskUserQuestion` — it returns findings (or a typed `architecture.md §3`
  decision) to this loop. One `## Handoff` block ends every clean run (§4); nothing follows it.

## 4. Handoff

Every clean run ends with a single `## Handoff` block — the only bridge to the next session. The schema,
omission rules, and closed-set state-marker vocabulary are owned by
[`../_shared/handoff-format.md`](../_shared/handoff-format.md); the researcher's shapes (dossier posted →
planner with `research: ✓`; decline → planner with `research: ✗`) are in
[`references/handoff-renderings.md`](references/handoff-renderings.md). **Read that reference immediately
before composing the handoff — not earlier — then emit the matching shape verbatim.** The field names
(`**Issue:**`, `**Next:**`, `**Why:**`), block structure, and closed-set markers are **contract, not prose
to summarize**: copy the shape and substitute only the issue number, title, state, and dossier URL — never
paraphrase, restructure, rename a field, drop a segment, or add a block the shape doesn't have. Concretely
forbidden (the S15 live-parity drift): renaming `**Issue:**` to `**Filed:**`; dropping the `· <state> ·`
segment; adding an invented `Snapshot` block; inlining the fenced `Next:` command into prose. The `Why:`
line is yours; the forward route is the `planner` (`/ctrl-freaq:planner #N`).
