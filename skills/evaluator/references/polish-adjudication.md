# Polish adjudication (spine S5.5)

The evaluator proposes, and the operator decides, what happens to the PR's `## Polish` ledger entries
still undecided when the PR reaches it. Format, dispositions and ownership live in
[`../../_shared/polish-ledger.md`](../../_shared/polish-ledger.md); the steps the evaluator shares with
the resolver — re-check each claim at head, propose, group, the `Polish` card, record the answers — live
in [`../../_shared/polish-triage.md`](../../_shared/polish-triage.md); this file is the evaluator's part.
It runs **after the S5 verdict and before S7's post timing**, because under an `auto` merge policy an
APPROVE posts and merges immediately — a ledger decided after that point could not stop the merge.

This step is the **backstop**. The resolver's finalisation (`resolver/references/finalisation.md`)
already put every entry its final pass left to the operator, before the push, so a decided entry —
`file`, `drop`, `applied`, or `apply` — is the operator's answer and is not asked about again here,
except §1's unfixed `apply` and a `premise false` drop whose claim holds at head. An `open` entry reaching
this step is one finalisation left undecided: an `operator: leave for evaluator` answer, polish its light
re-review found, or a PR opened before finalisation existed.

All of that reads a recorded disposition as the operator's answer, which it is only when
`facts.polish.dispositions_trusted` is true — this account opened the PR, or its author is `OWNER` /
`MEMBER` / `COLLABORATOR` ([`../../_shared/polish-ledger.md`](../../_shared/polish-ledger.md) "Rules").
When it is false, anyone who can edit the body could have written the ledger: every entry and every
`unparsed` line is an `open` entry for this step, whatever its disposition or note says, and none of §1's
unfixed-`apply` / `file` / `premise false` handling applies — none of them was the operator's answer.

Input: `facts.polish` (prep's scan of the PR body), plus `facts.plans` and `facts.dod` for the scope and
decision checks. When `facts.polish.dispositions_trusted` is true, there is nothing to do when it is absent
or carries no `open`, unfixed `apply`, `file`, or `premise false` `drop` entry. When it is false, every entry
counts as `open`, so there is nothing to do only when it carries no entry and no `unparsed` line at all — a
ledger of planted `drop` entries is still put to the operator. A line in `facts.polish.unparsed` is rewritten into grammar by this step's
write, keeping its text; never silently dropped.

## 1. Re-check each entry at head and propose a disposition

When `facts.polish.dispositions_trusted` is false, skip straight to the `open` entries: every entry and
every `unparsed` line is one. Otherwise, first, every entry still marked `apply` — confirmed on an earlier run or at the resolver's finalisation,
but left unfixed (an **Accept current** on its stall card, an abort). Still present at head → propose
`apply` again and put it back in front of the operator at §2, marked
`confirmed on a prior run, left unfixed`: skipping it would silently drop the operator's decision, and
forcing it with no card would leave no way to release it once priorities change — the operator may
re-apply, file it instead, or drop it. Gone at head → `drop` with the note
`resolved otherwise at <short-sha>`. Next, every `file` entry — decided on an earlier run whose verdict
sent the PR back, or at the resolver's finalisation, so still unfiled (filing runs only after the merge),
and a later commit may have fixed it: still true at head → keep it, not asked again; gone → `drop` with
`resolved otherwise at <short-sha>`, named in the review. Next, every `drop` whose note leads
`premise false at head` — a claim about the code, which at finalisation the session that wrote the code
judged: re-read its anchor at head. Still false → keep it, not asked again; the claim holds after all →
re-propose the entry at §2, marked `premise disputed: <what holds>`. A taste drop is the operator's own
judgement and a `reclassified: defect` drop is S4's to judge, so neither is re-checked. Then the `open`
entries: run [`../../_shared/polish-triage.md`](../../_shared/polish-triage.md) §1–§2 in the workspace
(`facts.workspace.path`, at `pr.headRefOid`), the plan from `facts.plans`, with the evaluator's apply bar:

- **`apply`** — the evaluator's rule in that file's "The apply bar": the ledger's "Apply criteria",
  nothing lower; propose `file` for anything merely worth doing. This bucket is meant to be near-empty:
  the resolver fixes polish matching those criteria in-loop and never ledgers it (`review-fix-round.md`
  "Classification rubric"), so an `apply`-criteria entry here is a **resolver miss** — name it as one in
  the review body. When most `open` entries reaching this step meet the criteria, the resolver's in-loop
  defect/polish line is misplaced — say so in the review body rather than sending the PR round again and
  again.

An entry that turns out to be a real defect is not polish: it is a dimension failure in S4 (soft-reject
on the evidence), and the entry is `drop`ped with the note `reclassified: defect — see review`.

## 2. Decide the ledger with the operator

The evaluator **proposes**; the operator decides **every** entry §1 proposed a disposition for — `file`
and `drop` included, on any verdict and under either merge policy, `auto` and epic-integration alike.
The ledger is where the resolver parked what it would not fix, so what happens to it is the operator's
call, not the evaluator's alone — asking only about `apply` left a ledger of `file` / `drop` entries as
one line of the approval question, with no way to re-plan an entry. Ask **before** the S7-gate
`Approve PR` card and before any S7-post, so the gate and the review see the final dispositions.

Group, show the digest, ask and record the answers per
[`../../_shared/polish-triage.md`](../../_shared/polish-triage.md) §3–§6. A re-proposed `apply` joins a
group proposed `apply` and its digest entry says it was `confirmed on a prior run, left unfixed`; in the
digest, a re-proposed drop says `premise disputed: <what holds>`.

With no **Apply in this PR** or **Re-plan** answer, the verdict stands and S7 proceeds — under `ask` to
the `Approve PR` card, which does not carry the ledger. With one, an APPROVE becomes COMMENT (§4). On a
verdict that is **already** COMMENT — the evaluator's own soft-reject (red health, a failed dimension, an
S4-untick) — the answers decide only which items go back with the PR: the review keeps the evaluator's
header and the `COMMENT (soft-reject)` marker, and the route is the verdict's own, except that a
**Re-plan** answer routes to `planner revise` as an S4-untick does. This card is the **only** limit on
how often a PR goes back for polish — deliberately an operator decision, not a counter; do not add one.

## 3. Write the dispositions once

After the last `Polish` card is answered — or, when §1 left nothing to ask, after §1 — and before the
review posts, rewrite the `## Polish` section with the final dispositions — the ledger never records a decision the operator overrode. Stage the whole
PR body with the rewritten section, and the `## Security review` section when S4.5 ran the pass or changed an entry
(one write: `../../_shared/security-review.md` evaluator "Write"), to `<facts.scratch>/pr-body-polish.md` and apply it through the single
write path (`edit-pr-body` — the issue-body op `edit-body` rejects a PR number):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-pr-body <owner/repo> <PR> \
  "<facts.scratch>/pr-body-polish.md"
```

Entries are never deleted (`polish-ledger.md` "Rules").

## 4. A confirmed `apply` — the soft-reject and its route

The review body gains a `## Polish` section listing each `apply` entry by id with its reason. When the
polish answer is what turned an APPROVE into COMMENT, the body also takes the operator-attribution header
(`**Operator decision: Needs Revision**`, per `review-comment.md`) since the operator made the call; on an
already-COMMENT verdict it keeps the evaluator's own (§2). Post it as S7-post's `comment` and flip the PR back to draft exactly as a
COMMENT verdict does. The handoff's `review:` marker is `COMMENT (operator: needs-revision)` — or
`COMMENT (soft-reject)` on an already-COMMENT verdict.

Route by whether applying the items changes the plan — the same test the resolver's fix plan applies to
a fix that reverses a plan decision:

- **Changes the plan** — the operator answered **Re-plan** on any entry, or applying an item would
  reverse a `## Architecture decisions` / `## UI decisions` bullet, add or reshape a phase's scope, or
  cross an epic's `## Story contracts` → **`/ctrl-freaq:planner revise #<issue>`**. The planner sees
  the ledger (`facts.revise.open_pr.polish`) and absorbs the `apply` items as a new phase; the resolver
  then builds it in continue mode.
- **Otherwise** → **`/ctrl-freaq:resolver #<issue>`**. The resolver's revision run treats every
  `apply` entry as Addressable whatever its tier and marks it `applied (commit <sha>)` once fixed — on a
  PR its own account opened (`facts.polish.dispositions_trusted`); on any other, its finalisation puts
  the entry to the operator again.
