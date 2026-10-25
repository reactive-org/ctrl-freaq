# sweep flow — scope → detect → reconcile → report → apply → land

Prep gave you the facts; run in order. Gates go through `AskUserQuestion` per
[`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md). No docs in scope (`docs.present`
false) → report "nothing to reconcile" and stop.

## 1. Detect OQs across the docs (judgment)

Read [detection](../../_shared/open-question-detection.md). Grep-prefilter `docs.files` (in scope) for the
cues — `detection.oq_markers.raw` when present, else the heuristic cues (`heuristics_active`). Fan out
`Explore` sub-agents over candidate files (one per file, or batched); each returns per real OQ `{source
doc + location, topic/text, native id, inline "tracked in #N", gated scope}`. Report prefiltered-vs-read.

## 2. Reconcile (judgment)

Match each doc-OQ to a `registry.questions` entry (native id first — often the question `title`'s tracker
id — else topic keywords + a `Read` of the body to confirm sameness; de-dup per detection §Matching).
Read resolution from prep's Tier-1 `status`, not the doc; for a `tier2_needed` entry dispatch the Tier-2
reader ([`../references/question-status-reader-prompt.md`](../references/question-status-reader-prompt.md),
fill its `<<...>>` from `sections`) — `resolved-in-thread` counts as resolved, `AMBIGUOUS` is surfaced.
Classify each pairing into one class: **untracked** (no matching question → file a companion) · **stale-doc**
(question resolved, doc still open → propose fold-back) · **missing-back-link** (open, no `tracked in #N`
and/or the question's `## Tracked in` omits the doc → add both ways) · **orphaned-issue** (open question,
doc marker gone → surface, never close) · **in-sync** (count only).

## 3. Report and gate

One consolidated report grouped by the five classes — each entry: the OQ, doc location, matched issue,
proposed action, evidence (a quote + the Tier-1/2 finding). **Every per-class header count and every
summary total IS that class's row count in this one table — derive each count by counting the table's
rows, never tally a class independently; a header/summary/table row-count mismatch is a defect.** Then
gate: GitHub writes (companions + back-link body patches) via `AskUserQuestion`; doc edits
propose-then-apply (show the exact diff). Never auto-close.

## 4. Apply the approved GitHub writes

- **Companion** (untracked): build the body per [question-issue](../../_shared/question-issue.md) (template,
  `## Tracked in` naming the source doc, audience labels), stage to `facts.scratch/q-<slug>.md`, create any
  missing `audience:*` label inline (`gh label create … 2>/dev/null || true`), then
  `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create <owner/repo> <path> --title <t> --label question --label audience:<x>`.
- **Back-link, issue side** (missing-back-link): stage the patched body, then `gh_persist.py edit-body
  <owner/repo> <N> <path>`.

## 5. Apply doc edits + land (prd.md §8.2)

Any approved doc edit (stale-doc fold-back per [links](../../_shared/open-question-links.md) §"Doc
fold-back": rewrite to the decided state / remove the marker / flip the register + `tracked in #N`; or the
missing-back-link doc-side `tracked in #N`) never touches the read-only root. Create a **work workspace**
(`${CLAUDE_PLUGIN_ROOT}/scripts/workspace.py ensure --work question-sweep/oq-<slug> --base main --root
<root>`; a `ROOT_*` freshness result is one `AskUserQuestion` card), `Edit` each doc **inside the workspace
path** it returns — a fold-back that changes what a catalogued doc says also updates its `<!-- doc-catalogue -->` summary there, checked with `${CLAUDE_PLUGIN_ROOT}/scripts/doc_catalogue.py check --root <workspace>` per [the catalogue contract](../../_shared/doc-catalogue.md) "Keeping it current" — then **stage the PR body (the doc/link-change summary) to `<facts.scratch>/pr.md`
BEFORE the gate** — so both paths share that one authored file. Offer the landing as **one explicit gate**:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py create-pr <owner/repo> "<facts.scratch>/pr.md" \
  --title "Reconcile open questions" --base main --head question-sweep/oq-<slug>
```

On **decline**: perform **no git actions** — report the workspace path and the ready-to-run landing
commands (`git -C <workspace> add`/`commit`, `git -C <workspace> push -u origin <branch>`, then the
`create-pr` above). **These commands must run exactly as printed — `pr.md` is already staged, so citing it
is safe; citing an unstaged file is a defect.** When no doc edit was approved, skip workspace + landing.

## 6. Summary

Close with the plain summary the router §4 describes — not a `## Handoff`.
