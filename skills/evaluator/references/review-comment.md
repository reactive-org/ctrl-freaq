# PR review-body rendering

The PR review is a GitHub-native review object (no marker; the body is prose), posted via
`gh_persist.py comment … pr-review <PR> --review-action <approve|comment>`. The body always leads with
`HEALTH_BODY`, then a dimension-by-dimension assessment (spine S5).
Four structured fragments have a fixed shape: the `## DoD verification` section and the operator-attribution
header are byte-compatible with the S1 baseline behavior; the `## Security review` fragment (#95) and the
`## Review threads` fragment (#145) postdate it.

## `## DoD verification` section

Included **only** when the spine's per-phase verification ran (projection annotations were present)
and produced at least one un-tick — or, in the all-clean case, a single summary line. Omit the section
entirely when no per-phase verification ran (a historical-path issue) or when every projected tick
verified clean with nothing to surface. Per un-ticked bullet:

```markdown
- **Bullet <index>** — <verbatim bullet text>
  - **Resolver claimed:** phase <N>, commit `<short-sha>`
  - **Evidence:** <file:line range or short diff excerpt showing the mismatch>
  - **Why rejected:** <one-sentence rationale>
```

When the section is present but every projected tick verified clean, use a single line instead:
`All <K> projected DoD ticks verified against their attributed phase diffs.` Don't surface
operator-phase bullets here unless the verdict turns on them — the `verification: operator-phase claim
— accepted on faith` note belongs in the bullet-walkthrough body, not this summary.

Each un-ticked bullet must already have been written to the issue body via the spine's S4-untick
(`gh_persist.py edit-body`) **before** the review posts — a reader following this section to the issue
must see the un-tick already in place. `<short-sha>` values are 7-char, matching
[`../../_shared/dod-annotations.md`](../../_shared/dod-annotations.md).

## `## Security review` section

Included **only** when the PR body's `## Security review` record
([`../../_shared/security-review.md`](../../_shared/security-review.md)) holds an entry still `open` after
S4.5's card (an `unparsed` `SEC-` line counts as open), or when S4.5's pass did not complete: either is
what made the verdict COMMENT, and the reader needs each one to fix it. Omit the section when neither holds;
a `fixed` or `dismissed` entry stays in the PR body's record only. A pass that did not complete is one line,
`- **Security pass did not complete** — <the sub-agent's evidence>`. An unparsed line renders under the id
S4.5's write gave it (`security-review.md` "Format"). Per open entry:

```markdown
- **SEC-<n>** — <claim> — <anchor>
  - **Exploit path:** <how attacker-controlled input reaches it: this run's finding, else the entry's note>
```

The record must already have been written to the PR body (S4.5's `edit-pr-body`) **before** the review
posts, so a reader following an id from the review finds the entry in place.

## `## Review threads` section

Included **only** when S4.6 soft-rejected on threads: one line per gating `changes-requested` thread and per
`operator-thread` without `pending_reply` — never one a trusted successor names (`superseded_by` set, or this run's
`new[].supersedes`), which gates nothing — the threads the resolver must answer, by the IDs `## Review items` uses
([`pr-summary.md`](pr-summary.md)):

```markdown
- **F<n>** — <path>:<range | file> — <the latest trusted reply, one line> · <thread url>
- **O<n>** — <path>:<range | file> — <the latest trusted comment, one line> · <thread url>
```

The resolver replies in each and leaves it unresolved; the human resolves it in GitHub.

## Operator-attribution header (S7-gate step 3)

Prepended to the staged review body when the merge-approval gate ran, so an override of the automated
verdict is visible in the PR's audit trail as a **human** decision — mirroring the `operator action
<ISO-date>` form in [`../../_shared/dod-annotations.md`](../../_shared/dod-annotations.md):

```
**Operator decision: <Approve | Needs Revision | Reject>** — operator action <ISO-8601 UTC>

<rationale: the operator's reason from the gate's free-text / notes, or "confirmed the evaluator's <APPROVE|COMMENT> verdict" when they took the recommended option with no added note>
<when it overrides the run's verdict, add: "Overrides this run's automated verdict (<APPROVE|COMMENT>).">

_Recorded by `github-pr-evaluator` on behalf of the human operator._
```

The `_Recorded by `github-pr-evaluator`._` footer is preserved **verbatim** — a frozen contract
string a reader recognizes on the PR, unchanged by the v2 skill rename. This same operator-attributed
shape is what you post when the operator instructs a decision directly in chat ("approve #N", "reject
it") outside a full run: capture the decision + rationale, post it with this shape, act on it.
