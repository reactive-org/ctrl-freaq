# Epic delivery log — shared contract

The **epic delivery log** records what each child story **actually delivered** as it lands. It is the cross-skill bridge between story implementation and the just-in-time planning of later stories, and it lives as **one durable comment per shipped story** on the epic issue.

This file is the single source of truth for its format and ownership. Both consumers cite it: `evaluator` (writer) and `planner` (reader).

## Ownership

- **Writer — `evaluator` (sole writer).** When it merges a story PR it records what that story delivered as **that story's own comment**. Recording **every** story — including the last — is what keeps the log complete.
- **Reader — `planner`.** During "Just-in-time story planning" it reads the log to (a) reconcile the epic plan's pinned `## Story contracts` against what actually shipped, and (b) feed Dimension 8's "consumes only what's shipped" check. The planner never writes it.

Keeping the log out of the verified `<!-- implementation-plan:v1 -->` plan is deliberate: the plan is verified and immutable, while this record changes on every merge.

**One comment per story, not one comment per epic.** A single accumulating comment is bounded by GitHub's 65,536-character body cap, which an epic reaches by construction — one observed epic hit 37,478 characters across 9 entries with 14 stories still open, projecting past the cap with ~6 merges left. Past that point the write returns `BODY_TOO_LONG` and **no merge can be recorded at all**, with no compaction path and no owner but the append (#41). Per-story comments make growth O(1) per artifact instead of O(n), so the cap stops being a deadline. It also removes the pressure to compress an entry: divergence narration a later story must read costs a fraction of its own budget.

## Format

The `<!-- epic-delivery-log:v2:story:<N> -->` marker is always the **first line** of the comment body, where `<N>` is the story's issue number (every reader locates entries with a `startswith` match — anything before the marker makes an entry undiscoverable):

```
<!-- epic-delivery-log:v2:story:<N> -->
- #<story> — delivered: <actual contract shape, as merged> @ `<commit-sha>` (PR #<M>, merged <ISO-8601 date>)
```

One entry per comment, and the marker names the same story the entry line does. `<actual contract shape>` is the public surface the story actually merged (the new/changed type, service, or API signature) — recorded from the merged diff, **not** copied from the plan's pinned contract, so a divergence between pinned and shipped is visible here.

**The entry line's grammar is deliberately identical to the legacy tier's**, so one parser serves both and a tier migration never becomes a parser migration. The per-comment body carries no epic header: a header repeated on every comment is N copies of a fact the epic issue already states.

**The marker is unique per story.** Two comments bearing the same `:story:<N>` marker are a genuine duplicate — `MARKER_AMBIGUOUS`, exactly as a duplicated single-comment log was. The story number in the marker is what preserves that check; a shared marker across entries would make "N entries" and "one entry duplicated N times" indistinguishable. Note `:story:15 -->` is not a prefix of `:story:150 -->` — the trailing ` -->` terminates the match, so no story's marker can collide with another's.

### The legacy tier — `<!-- epic-delivery-log:v1 -->`

Epics whose log was written before per-story comments carry **one** comment holding every entry:

```
<!-- epic-delivery-log:v1 -->
**Epic delivery log** — #<epic-N> <title>
- #<story> — delivered: <actual contract shape, as merged> @ `<commit-sha>` (PR #<M>, merged <ISO-8601 date>)
- ...
```

This tier is **read forever and never written again**. There is **no backfill path**: a legacy log stays as it is, stops growing, and its entries keep being read. Nothing repairs it automatically, and nothing needs to.

## Reading it — per-story entries first, the legacy comment second

An epic's delivery log comes from two sources, and `log_source` reports which one answered:

1. **`entries`** — one or more `<!-- epic-delivery-log:v2:story:<N> -->` comments and no legacy comment. The source of truth.
2. **`legacy`** — no per-story comments; the epic carries a `<!-- epic-delivery-log:v1 -->` comment. Parse its entry lines.
3. **`mixed`** — **both** are present. **Union them by story number, the per-story entry winning where a story appears in both.** Never take one tier and drop the other: that silently loses shipped stories, and a reader seeing two shapes for one story would hand Dimension 8 a contradiction.

Only a trusted author's log comment counts, in either tier (`OWNER` / `MEMBER` / `COLLABORATOR`, `scripts/pipelib/trust.py`, #179). Any other log-marked comment is skipped before the tiers are read, so it never records a story, never supplies a contract shape, never makes a story ambiguous, and is never the `comment_id` a re-record edits. It rides in `notices` as `MARKER_UNTRUSTED`.

`mixed` is reachable without anyone doing anything wrong, and is the normal state for every epic that predates per-story comments: the next merge on such an epic writes a per-story comment while its earlier entries remain in the legacy body. It is a real state to read correctly, not an error to report.

Entries are ordered by **comment creation order**, which is merge order — no field maintains it. An entry edited in place (a re-record) keeps its original position, which is the intent: the position records when the story shipped, not when the record was last touched.

A `Consumes:` claim in a story's `## Epic contract` must name a contract already recorded in the log, **with a matching shape**; a divergence between a recorded shape and the epic plan's pinned `## Story contracts` is the planner's signal to re-plan the epic before grounding later stories on a stale contract.

That comparison only works while the two sides stay independent. When an epic revise compresses a merged story's `## Story contracts` entry, the entry's `delivers` clause is preserved **verbatim as originally pinned** and is never re-pinned to the shape this log records — the compression is lossy in narration only. Re-pinning would leave the check comparing the log against a copy of itself, silently and permanently: no divergence could ever surface again, and each individual re-pin looks like a tidy-up.

## Writing it (evaluator)

Go through the single write path — never hand-roll `gh` (a raw `gh api` / `Write` + `gh` assembly re-opens the #626/#627 empty-body race). Stage **this story's entry alone** (marker line first) to the session's scratch dir, then take the arm that matches what already exists:

- **No record for this story** → create its comment:

  > `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py comment <owner/repo> issue <epic-N> <staged-entry>`

- **This story already has a per-story comment** (a re-run, or a corrected shape) → update it in place by its numeric REST comment id:

  > `${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py edit-comment <owner/repo> <entry-comment-id> <staged-entry>`

- **This story's line is in the legacy comment** → update the line in place inside a re-staged legacy body and repost it with `comment … --delete-marker-id <legacy-comment-id>`. Never leave one story recorded in both tiers: a second record with a possibly divergent shape is exactly what the idempotency rule exists to prevent, and the reader's precedence rule is a safety net, not a licence to create the conflict.

**Idempotent, per story.** A story has exactly one record. Re-evaluating a story updates that record; it never adds a second.

**Both write ops are required — `edit-comment` does not replace `comment`.** `comment` is the only op that can create the first record (there is no id to `PATCH` yet) and the only one that can collapse a duplicated marker, since `edit-comment` performs no delete. `edit-comment` is a single atomic `PATCH`: the comment URL stays stable and GitHub's own edit history becomes the supersession record.

> **Supersedes the standing ruling that this writer does not use `edit-comment`.** That ruling said adopting the op "would be a change to make on its own evidence, not a side effect of the planner's" — #41 is that evidence. Per-story comments make in-place update the natural mechanic: the alternative churns one entry's URL on every re-record, for a comment whose whole purpose is to be a stable citation target. `comment --delete-marker-id` stays documented above precisely because it is not subsumed.

Both ops pass the same `_verify_body_file` gate, so an over-cap entry is refused before any `gh` call — the backstop is unchanged, and a per-story entry has no realistic path to reaching it.
