# PR summary and review items — evaluator S4.6

Two blocks S4.6 prints before it gates on threads ([`review-threads.md`](review-threads.md) §5), for the
operator rather than a parser: what the PR changed, then every open review item under an ID the operator can
discuss. Modelled on [`../../_shared/plan-summary.md`](../../_shared/plan-summary.md): fixed bold-labelled
sections, bounded bullets, compress never extend, omit never pad. Only the evaluator renders them, so the
contract lives here.

## `## PR summary`

Sections, in this order:

```
## PR summary

**What changed:** <2–4 plain sentences, from the diff and the PR body>

**By phase:**                      (omitted when single-phase)
- Phase <N> — <title> — <one line>

**Decisions & deviations:**
- <decision or deviation> — <why>

**Risk:** <the PR body's `## Risk`, compressed, and the S4.6 assess envelope's `level` and `why`>
```

| Section | Source | Bound |
|---|---|---|
| `**What changed:**` | the diff and the PR body | 2–4 sentences, plain words |
| `**By phase:**` | one line per `## Phase tracker` row | omitted when single-phase |
| `**Decisions & deviations:**` | the plan's `## Architecture decisions` the diff implements, `## Plan override`, `## Deviations from project docs`, the `## Out-of-plan changes` reasons | at most 5 |
| `**Risk:**` | the PR body's `## Risk`, the `assess.json` `level` / `why` | a few lines |

## `## Review items`

One line per item:

```
- <ID> · <kind> · <state> — <one line> — <anchor> · <link>
```

The IDs are the existing stable ones, so the operator can name any item in chat:

- `F<n>` — a pipeline thread (its stamped id);
- `O<n>` — a human-opened thread (the reader's `o` ordinal);
- `SEC-<n>` — a `## Security review` entry;
- `P<phase>.<seq>` — a `## Polish` ledger entry.

**Order:** the items needing the operator first — `changes-requested`, `operator-thread`, `unreviewed`,
`superseded` with no fresh thread, `unanchored_security`, then `open` polish. `accepted` and `fixed` items
collapse into one count line. `overflow` flags are listed under the reason they have no thread.

## When it renders

From facts in hand — the opening facts, the S4.6 `assess` and `flags` envelopes, and the `review-post`
payload — after posting and before the `Threads` card or the soft-reject, and again after every **Re-check**.
In a coordinator run both blocks are printed in the same message as the card
([`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md) "Relayed cards").

## Discussing items

The operator may ask about any item by its ID, in chat or in Other on the card. Answer from the facts, diff and
thread text already held, then re-ask the same card. Discussion never changes an item: threads resolve in
GitHub, and `SEC-<n>` / `P<phase>.<seq>` change only through their own cards.
