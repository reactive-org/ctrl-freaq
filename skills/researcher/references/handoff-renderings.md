# Handoff renderings — researcher

Every clean run of the researcher ends with a single `## Handoff` block. The schema, omission rules, and
closed-set state-marker vocabulary live in [`../../_shared/handoff-format.md`](../../_shared/handoff-format.md);
this file holds the researcher's worked rendering shapes. **Pick the one worked example that matches the
run's outcome, copy its shape, and substitute only the issue number, title, state, and dossier URL** — fill
the snapshot from the data in hand. The field names, block structure, and closed-set markers below are
rendered exactly as shown — they are **contract, not a style to imitate**: never rename a field (`**Filed:**`
for `**Issue:**` is a defect, not a variant), never drop the `· <state> ·` segment, never add a block the
shape below doesn't have (no invented `Snapshot` section), never inline the fenced `Next:` command into
prose. Both shapes forward to the `planner`; next-command skills are namespaced `/ctrl-freaq:<name>`.

The `research:` marker carries the dossier comment's URL in parentheses on the clean-exit (posted) shape,
per the shared schema's rule that "when the marker carries a URL (the researcher's clean exit), append it in
parentheses" ([`../../_shared/handoff-format.md`](../../_shared/handoff-format.md)). The researcher never
authors a plan, so `plan:` is **omitted** on both shapes (the issue has no plan yet — the planner posts one
next). The `<type>` segment (`bug`/`feature`/`incomplete`/`story`/`epic`/`question`) is the repo's own label
mapped onto that closed set, never leaked raw — e.g. an `enhancement` label renders as `feature`
([`../../_shared/handoff-format.md`](../../_shared/handoff-format.md)'s Issue `type` row).

## Renderings

**Dossier posted (broad, targeted, or revise).** Forward to the planner with `research: ✓` and the dossier
URL.

```
## Handoff

**Issue:** #142 — Migrate to <dependency> v<X> · open · feature · research: ✓ (https://github.com/owner/repo/issues/142#issuecomment-XXXXX)

**Next:** plan the approach in a fresh session; the planner ingests the dossier.

    /ctrl-freaq:planner #142

**Why:** the dossier captures the current, fetched behaviour of <dependency> v<X> with provenance. The planner grounds its decisions in it and records the sources in its plan's `## External sources consulted`.
```

For a **targeted** refresh (or a revise driven by a planner gap), the shape is identical — `research: ✓`
carries the *new* dossier URL, and the `Why:` quotes the specific gap the refresh closed so the re-run
planner can act without re-investigating.

**Nothing to research (the broad-mode decline gate fired).** Forward straight to the planner with
`research: ✗`; no dossier was posted.

```
## Handoff

**Issue:** #142 — Rename config key · open · bug · research: ✗

**Next:** plan the approach in a fresh session.

    /ctrl-freaq:planner #142

**Why:** this issue touches nothing with currency risk — the model's knowledge is sufficient, so no dossier was posted. The planner proceeds directly.
```
