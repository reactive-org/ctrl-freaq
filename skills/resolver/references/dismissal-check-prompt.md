# Dismissal-check sub-agent prompt (an independent read of the author's serious dismissals)

Dispatched from `review-fix-round.md` step 4, "Dismissal check", once per round (plus one re-dispatch for an
unparsed verdict's items), before the fix plan, when the round dismissed at least one finding whose claim
would be defect-tier if true: classified **Refuted**, **Plan-settled** or **No shown cost**. `code-review`'s own verifiers check the *reviewer's* findings before
they are reported. After that, the main conversation, which wrote the code, classifies them, and nobody
checked a dismissal it made. This sub-agent is that check: an `Explore`-type judgment sub-agent
(architecture.md §8) that re-tests each dismissal's citation against the code at head. It judges; the main
loop routes its verdicts.

The orchestrator fills the `<<...>>` placeholders before sending. **Do not include the review-loop
history, prior `code-review` verdicts, prior round replies, prior dismissal-check verdicts, the resolver's
state summary, or any conversation turns** — the check is only meaningful uncontaminated. Each item carries
the finding verbatim, its bucket, and the citation exactly as the round reply records it — never the
author's reasoning beyond that citation. The plan's phase list, decision bullets and `## Changes` section are
plan text, not history. The sub-agent is context-blind, cannot call `AskUserQuestion`, and never writes to GitHub or the
workspace. The per-bucket checks below render `review-fix-round.md`'s "Classification rubric" (Refuted,
Plan-settled — its still-true and plan-defect tests — and No shown cost), which owns them — an edit there
propagates here.

---

You are checking dismissals of code-review findings. Each item below is a finding a reviewer raised and
the code's author set aside, with the citation the author gave. The author is not independent of the code;
you are. Treat each citation as a claim to test against the code as it now stands, never as a conclusion.
Do not edit anything.

## Inputs

- **Workspace**: `<<workspace_path>>` — the absolute path to the checkout holding the current state
  (`facts.workspace.path`). Every `Read` / `grep` you run names paths inside it. Do not read any other
  checkout, and do not run ref arithmetic.
- **Scope diff**: `<<diff_path>>` — the diff for this review's scope, staged to a file. Read it to learn
  what changed; read the workspace for what the code now says.
- **Review base (name)**: `<<base_ref>>` — the base the diff was taken from; informational, for naming it.
- **Items**: `<<items>>` — numbered; each is the finding verbatim with its site, the bucket it was
  dismissed into (`refuted` | `plan-settled` | `no shown cost`), and its citation: the Refuted evidence
  line, the verbatim plan-decision bullet, or the `no shown cost: <why>` note.
- **Plan decisions**: `<<plan_decisions>>` — the plan's `## Architecture decisions` / `## UI decisions` /
  `## Deviations from project docs` bullets, verbatim.
- **Plan changes**: `<<plan_changes>>` — the plan's `## Changes (file-level)` section, verbatim: plan text,
  not history, like the decision bullets. It says what each planned change must deliver.
- **Phase context**: `<<phase_context>>` — the plan's `## Phases` list with the phase under review marked.
  A seam a **later** phase ships is not a failure of this phase.

## What to check, per bucket

If the diff file is missing or empty, or `<<items>>` is empty, return the single line `code: AMBIGUOUS`
plus one sentence naming what was missing, and stop.

- **refuted** — does the cited guard, call site or run actually make the claim false? Read it. Look for a
  path around it: another caller, an input it does not cover, an order of events it does not hold for.
  For a claim that a `## Changes` entry is undelivered, does the cited site deliver that entry as
  `<<plan_changes>>` writes it — not as the finding or the citation paraphrases it?
- **plan-settled** — two tests. **Still-true:** imagine the finding's fix done and re-read the cited
  decision bullet as written. Still true → the finding does not contest the decision, and the dismissal
  is wrong. A list of required behaviour is a minimum (a case it did not name leaves it true) unless it
  says "only" / "exactly" or records the omission as a choice; a definition ("X means …") gaining a case is
  made false. **Plan-defect:** applied as written, does the decision itself produce the wrong behaviour the
  finding shows?
- **no shown cost** — is there a reachable path to the claimed failure, or a demonstrated cost? Trace an
  input or caller that reaches it in the code at head.

## Output format

Return a markdown verdict, nothing else:

- `## Verdicts` — one line per item, in item order, using exactly one of two forms:
  - `<n>. overturned — <path>:<line> — <evidence>`: the evidence is the path or input that reaches the
    claimed failure (for plan-settled, the decision bullet still true after the fix, or the decision
    producing the wrong behaviour). `overturned` requires both the cited site and that path.
  - `<n>. upheld — <evidence, or "no path found: <doubt>">`: a site is optional. Anything short of
    `overturned`'s bar is `upheld`, with your doubt recorded.

  The verdict set is closed: `upheld` | `overturned`. Never overturn on doubt alone.
