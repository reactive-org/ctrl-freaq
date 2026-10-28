# Review flow

The one flow: prep (the router's §1) → each item, in `items` order → the closing re-read → the plain
summary. The thread states, the decision reply and its refusals are
[`../../_shared/review-threads.md`](../../_shared/review-threads.md)'s (§2, §4, §9, §10); this playbook
renders them. `<N>` is `facts.issue.number`, `<PR>` is `facts.pr.number`, `<head>` is `facts.review.head`.

## 1. Before the first item

- `facts.pr` is `null` → say the branch carries no open PR, and stop: there are no threads to review.
- `facts.review.available` is false → say the threads could not be read (quote the
  `REVIEW_THREADS_UNAVAILABLE` notice), and stop.
- `facts.items` is empty → say nothing waits on a person, give `left_out` by why (the summary's last
  bullet, §4), and stop.

Otherwise print one line: the PR, its head (7 characters), how many items wait, and every notice
(`REVIEW_THREADS_TRUNCATED`, `REVIEW_EXCERPTS_UNAVAILABLE`, `THREAD_PERMISSION_UNAVAILABLE`), since each
qualifies what follows.

## 2. Each item

In `items` order, one item at a time. Hold a tally for the summary: each item's answer, or its refusal.

1. **Progress line:** `Item <seq> of <of> · <key> · <path>`.
2. **The brief**, one labelled line each, a blank line between them:

   ```text
   Why you're reviewing it: <from the rank: a security finding · a review-level raise (a sensitive path, or the blast-radius judgment) · a plan deviation · a file the plan never named · a thread no pipeline flag names (your own, answered, or the pipeline's re-post of one) · the routine batch>

   What triggered it: <the item's flags and its root text (`root_text_path`), in plain words; on a security item its `claim`; on a thread the resolver has answered (your own thread, or a pipeline thread back to unreviewed after a change request), that answer and what it answered: the thread's trusted `comments` in `review.threads_path`, matched by `id`>

   What the plan said: <what the staged plan (`facts.plan.body_path`) says about this file or change — or "the plan names no change to this file" on an out-of-plan item>

   For users: <what differs for the product's users if this code is wrong, or "no difference — this only affects <X>">

   What to decide: <the one question this thread asks>

   Answer "more" in Other for the evidence.
   ```

   A routine batch's brief also says that **Accept** accepts every member, and that **Changes required**
   is the operator's notes, a change request the resolver reads and answers on the batch — never a split:
   every member the notes name stays in the batch (`review-threads.md` §7 "Batch membership").
3. **The excerpt**, verbatim from its file, in a ` ```diff ` block, followed by the item's `link`. A
   `kind: none` excerpt is one line naming its `reason` (`binary`, `no-hunk`: the PR leaves the file
   unchanged in its range, `unread`), with the link. A batch shows each member in turn: its path, its
   excerpt or that line, its link; an `elided` member is its path and link only. Never paste a whole file.
4. **Discussion.** The operator may ask about the code before answering. Answer from
   `facts.workspace.path` and the staged plan (the router's read-only invariant).
5. **The `Thread` card** — a consent card that recommends nothing
   ([`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md) "Consent vs judgment"): the
   operator's own review is the decision. `header: "Thread"`; the question points at the brief; the
   options are fixed per item kind and listed in this order, which is order, not a recommendation:
   - **Not a security item** (`security: false`): **Accept** / **Changes required** / **File as follow-up**
     / **Skip for now**.
   - **A security item** (`security: true`): **Changes required** / **Dismiss** / **File as follow-up** /
     **Skip for now**. No **Accept**: an accept leaves the finding `open` (`review-threads.md` §9), and
     the evaluator would then ask for the dismissal reason on its own card.

   Each option's description says what it posts on this thread. An answer is a pick of one of the four.
   Text typed in Other is never mapped to a decision: "more" (or "why", "I don't understand") prints the
   evidence and re-asks the same card (`asking-the-user.md` "More context"); any other text is
   discussion — answer it, then re-ask the same card.
6. **The write**, at once, before the next item — so a review stopped part-way keeps what it recorded.
   Stage the text after the op's `<decision>: ` prefix to `<facts.scratch>/reply-<seq>-<key>.md`:

   | Answer | `--decision` | The staged text |
   |---|---|---|
   | **Accept** | `accept` | `Accepted in a chat review at <head, 7 characters>.` |
   | **Changes required** | `changes` | the operator's notes, verbatim |
   | **Dismiss** | `dismiss` | the operator's reason, verbatim |
   | **File as follow-up** | `accept`, or `dismiss` on a security item | `Filed as #<M>.` |
   | **Skip for now** | — | nothing: post nothing |

   For **Changes required** and **Dismiss**, ask in one line of prose for the notes or the reason, after
   the pick and before the post. An empty answer posts nothing and re-asks the card.

   For **File as follow-up**, file first, through
   [`../../_shared/follow-up-filing.md`](../../_shared/follow-up-filing.md) with a group of one — one
   `general-purpose` sub-agent. The item is the thread's root text, its flags and its excerpt's anchor,
   in your words; the type is your classification of the finding as `bug`, `incomplete-feature`, `feature` or `deferred-test`, never `revise-existing`, which edits another issue and files none; the parent reference is the PR's URL,
   issue #<N>, and the parent epic when the issue names one. Only a returned URL leads to the reply. A result whose final type is `revise-existing` filed nothing: report the issue it edited in one line, post nothing, and re-ask the same card. An
   `error:` posts nothing: report it in one line and re-ask the same card. On a security item, `Filed as
   #<M>.` becomes the dismissal reason the evaluator records.

   Then post:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/gh_persist.py review-reply <owner/repo> <PR> --thread "<item id>" \
     --head "<head>" [--f <item f>] --decision <accept|changes|dismiss> "<facts.scratch>/reply-<seq>-<key>.md"
   ```

   `--thread` is the item's `id`; pass `--f` only on an item with an `f`. `posted: false` with
   `refused: resolved | decided | not-security | other-pr | not-a-thread` is one line naming the refusal,
   counted as refused, never retried. A `needs_decision` envelope (`EMPTY_BODY_FILE`, `BODY_TOO_LONG`, `AUTH_REQUIRED`) posted nothing:
   take the router's decision card rule (its §1), fix what it names (the notes or reason empty or too long: ask
   the operator again for them, never trim them; the sign-in), and post the same answer again (a File as
   follow-up's same `Filed as #<M>.`, never a second filing). Left unanswered, the item is not recorded. A
   non-zero exit posted nothing: report its stderr line and count the item as not recorded. After a File as
   follow-up's filing, a refusal or an item not recorded leaves the issue filed and no thread naming it: count
   it as refused or not recorded with that issue's URL.

## 3. The closing re-read

After the last item, re-read the threads at the same head:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/prep_review.py <N> <owner/repo> --refresh --head "<head>"
```

It keeps the branch check, skips the exact-head check, and judges at `<head>`, the commit every reply
recorded as its reference (`review-threads.md` §6): it shows whether each answer stands as recorded, and a
push past `<head>` is never something it reads. It ends one of two ways.

- **Re-read:** `pr.number` is `<PR>` and `review.available` is true. Match each answered item to its thread
  by `id` in the re-read's `review.threads_path`, which lists every thread with its `state`. A thread whose
  state is not what its answer records (`accepted` for Accept, Dismiss and File as follow-up;
  `changes-requested` for Changes required) is said so, with the state it reads — most often a resolution or
  a later comment on the thread, which outranks or ends the decision (`review-threads.md` §4), or §6's
  conservative side on an anchor the check cannot follow. So is an answered `id` the list does not carry (a
  `REVIEW_THREADS_TRUNCATED` read). Neither is re-posted, or reported as a failed answer.
- **Not re-read:** exit 1 with no envelope (its stderr line); a `pr` that is `null` or names another PR —
  `<PR>` is no longer open, merged or closed during the review; or a re-read whose `review.available` is
  false (its `review.threads_path` is then `null`; its `REVIEW_THREADS_UNAVAILABLE` notice). Say the threads
  could not be re-read, with that line, and summarise from the tally alone — never report a recorded answer
  as failed for want of a re-read. A `needs_decision` envelope takes the router's decision card rule (its
  §1), then this re-read again; stopped there, the threads were not re-read.

A re-read `pr` of `<PR>` whose `head` is not `<head>` means the PR moved during the review: say so, with its
7 characters. The decisions still hold at `<head>`; an accepted one whose hunk the push changed reads
`superseded` on the next read at the new head (the evaluator's), the conservative outcome
(`review-threads.md` §6), never an error.

## 4. Summary — plain, no `## Handoff`

- The PR, `<head>`, and how many items the review covered; the PR's new head when it moved (§3).
- Counts by answer: accepted, changes required, dismissed, filed as follow-up (each filed issue's URL),
  skipped; then each refusal and each item not recorded, by key, with the URL of any issue it filed, since
  no thread names that issue and a later review will offer the thread again: tell the operator to answer it
  then by naming that issue in discussion, not by filing another.
- On a re-read, each answered thread whose state differs from its answer, with the state it reads, and each
  answered `id` it does not list. When the threads were not re-read, that line instead, with why (§3).
- What the review did not cover: `left_out` by why (replaced, changes requested — waiting on the resolver,
  superseded, accepted, an operator's thread waiting on the resolver), `review.untrusted`, and the notices.

When a change request waits on the resolver, end with the one line

```
Next: /ctrl-freaq:resolver #<N>
```

— the resolver run that picks the change requests up, on the issue (never `continue #<PR>`). One waits when
a re-read shows any thread reading `changes-requested`. When the threads were not re-read, the tally's posted
Changes required answers and the opening read's `left_out` `changes-requested` count stand in for that,
except when `<PR>` is no longer open (§3): no resolver run picks its threads up.
