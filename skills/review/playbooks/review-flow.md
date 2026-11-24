# Review flow

The one flow: prep (the router's §1) → each item, in `items` order → the closing re-read → the plain
summary. The thread states, the decision reply and its refusals are
[`../../_shared/review-threads.md`](../../_shared/review-threads.md)'s (§2, §4, §9, §10); this playbook
renders them. `<N>` is `facts.issue.number`, `<PR>` is `facts.pr.number`, `<head>` is `facts.review.head`.

## 1. Before the first item

- `facts.pr` is `null` → say the branch carries no open PR, and stop: there are no threads to review.
- `facts.review.available` is false → say the threads could not be read (quote the
  `REVIEW_THREADS_UNAVAILABLE` notice), and stop.
- `facts.items` is empty → in phase mode first print each `facts.pause.reviews` body, verbatim, under the heading
  `Review pause — phase <P>` — the pause's anchorless triggers, context that asks for no decision and gets no
  card — so a pause that posted only its body still shows it. Then say nothing waits on a person, give `left_out` by why (the summary's last
  bullet, §4), end with §4's phase-mode lines in phase mode (the opening read deciding, as §4's fallback), and stop.

Otherwise open with the **orientation**, one or two paragraphs in the router's voice ("Voice"): what is being
reviewed and why — the issue (`[#<N> <title>](<facts.issue.url>)`), the PR (`[#<PR>](<facts.pr.url>)`) and its
[Files view](<facts.pr.files_url>), `<head>` (7 characters), the phase in phase mode, how many items wait and of which
kinds, and which deserve the most attention (a security finding or design difference first; the gating items ahead of
an advisory batch, which holds no approval). Every notice follows (`REVIEW_THREADS_TRUNCATED`,
`REVIEW_EXCERPTS_UNAVAILABLE`, `THREAD_PERMISSION_UNAVAILABLE`, `MARKER_UNTRUSTED`, `PAUSE_REVIEW_NOT_FOUND`,
`REVIEW_CAPTURE_UNSTAGED`, `REVIEW_COMPARE_PAGE_UNWRITTEN`), since each qualifies what follows. In **phase mode**
(`facts.phase` set) the pause's review bodies come next, each verbatim under the heading `Review pause — phase <P>`:
context that asks for no decision and gets no card. Then the **agenda**,
one line per item, `<seq>. <key> · <path> — <its kind>`, the kind from its rank in plain words: a security finding · a design difference (the build shows a recorded state differently from the approved design) · a design summary (the departures from the approved design the build declared, or the plan or the operator agreed, to approve at once) · a review-level raise (a sensitive path, or the blast-radius judgment) · a plan deviation · a file the plan never named · a thread no pipeline flag names (your own, answered, or the pipeline's re-post of one) · the routine batch.

## 2. Each item

In `items` order, one item at a time. Hold a tally for the summary: each item's answer, or its refusal.

1. **Heading:** `### <key> · <path> · <seq> of <of>`.
2. **The summary** — one paragraph saying what the finding is (its kind), what triggered it (the item's flags and its
   root text, `root_text_path`, in plain words; on a security item its `claim`), what the plan said (what the staged
   plan, `facts.plan.body_path`, says about this file or change — or that the plan names no change to this file, on an
   out-of-plan item, or that there is no plan when `facts.plan.present` is false), and, on a thread the resolver has
   answered (your own thread, or a pipeline thread back to unreviewed after a change request), that answer and what it
   answered: the thread's trusted `comments` in `review.threads_path`, matched by `id`. A routine batch's summary also says that
   **Accept** accepts every member, and that **Changes required** is the operator's notes, a change request the resolver
   answers on the batch — never a split: every member the notes name stays in the batch (§7 "Batch membership"). An `advisory`
   batch comes last, its summary saying it does not hold approval while it waits (a change request does, §4). A sensitive group's **Accept** accepts every file. A **design summary** item (`design.summary` true, #241; every key `design-departure:`) is read first, never by the design item's `adopted` rule: its summary lists each explanation its `explanations` names (`D<n>` a departure the build declared, `U<n>` a plan's agreed deviation, `X<n>` a decided deviation) with its reason and the differences it covers, from its root text, and says **Accept** approves every departure listed at once and clears every difference it covers, whatever the record's revision, while **Changes required** names the difference to fix instead, and **File as follow-up** posts an `accept` too, so it approves them all. A **design** item (a `design:` key, #206) names the recorded state, the difference (`captures.caption` when set) and both captures — the approved design's and the build's (step 4 shows them) — and adds one sentence from the item's `design` (`threads[].design`, never the reply text): `adopted: true` → **Accept** clears it now, since a planner revise adopted a revised record or withdrew the target (on a PR into the default branch that names no closing issue, the evaluator reads no design fact and still holds it until the PR's `Fixes #<issue>` line is restored); `adopted: false` → **Accept** records the answer, but the thread keeps holding the evaluator until the build is fixed to match the record (a later comparison retires it) or the designer revises the record and a planner revise adopts it, or a planner revise withdraws the target. A **design group** (`group: design`, #241) is one design item for every state it names: its summary lists each state with its differences, and **Accept** or **Changes required** answers them all at once, on the same `adopted` terms. A design difference's summary may also say that, when the build is right, **Changes required** with notes asking for a departure has the build session declare it, and the next check moves it to the summary. The options are a non-security item's, unchanged.
3. **Decide, Impact and the links**, formatted markdown:

   ```markdown
   **Decide:** <the item's `what_to_decide`, verbatim; when `null`, the one question this thread asks>

   **Impact:** <the item's `impact`, verbatim; when `null`, in the Impact form: "<affected party> — <what changes for it>", or "None outside the code — <what it touches>">

   [Thread](<url>) · [Change](<link>)
   ```

   The two verbatim lines are the poster's (`review-threads.md` §2 template), words kept, markdown markers stripped.
   Both links are prep's, never a URL the session builds: `url` is the thread's root comment, `link` its change in the
   Files view, line-anchored where the thread's lines are in the PR's diff. An item with `members` (a batch, a sensitive
   group, a file and its riding tests: one thread) gives one change link per member, `[<path>](<member link>)`, an
   `elided` member's too. A design summary item (#241) links its thread and its file-level `link`.
4. **The excerpt, the captures, and discussion.** The operator may ask about the code before answering: answer from
   `facts.workspace.path` and the staged plan (the router's read-only invariant). The excerpt prints only on "more"
   (with the root text and what the plan said) or when it helps answer a question: verbatim from its file, in a
   ` ```diff ` block. A `kind: none` excerpt is one line naming its `reason` (`binary`, `no-hunk`: the PR leaves the
   file unchanged in its range, `unread`); a multi-file item shows each member in turn, its path then its excerpt or
   that line, an `elided` member its path only. Never paste a whole file. A design summary item (#241) has no excerpt: its subject is its explanations, not code.
   **A design item's captures** (#237), when its `captures` is not `null`, show with steps 1–3, before the `Thread` card, whose options are a non-security item's. With `captures.compare_page` set, open it once with a tool that opens a URL in a browser pane inside the host, when the session has one (in the Claude desktop app, its built-in browser's `preview_start` with `url` `file://<compare_page>`), then print one line with the page path, so the operator can open it in their own browser if the pane shows it without its **Flip** and **Overlay** controls; with no such tool, or when the call fails or is refused, print the page path and both captures' links instead, and go on: an unopened page never stops the walk. With `compare_page` `null`, print each side's `link` (`record` the approved design's, `build` the build's) and name each side whose `staged_path` is `null` with its `error`. A `link` is a GitHub URL, or `null` for a `local` capture (named by its `ref`, a path on the machine that captured it) and a `none` one; a design item whose `captures` is `null` (a root posted before #237, or with no comparison) shows only what its root text gives.
5. **The `Thread` card** — a consent card that recommends nothing
   ([`../../_shared/asking-the-user.md`](../../_shared/asking-the-user.md) "Consent vs judgment"): the
   operator's own review is the decision. `header: "Thread"`; the question is the compact core, plain text, a blank line
   between lines:

   ```text
   <key> · <seq> of <of> · <path>

   Decide: <step 3's Decide line, markdown markers stripped>

   Answer "more" in Other for the evidence.
   ```

   It opens with the identity line `review · #<N> · thread <key>` in a coordinator-started session ("Relayed cards"),
   and has no clear/close-call line. On a design item whose captures show (step 4), the line after `<key> · <seq> of
   <of> · <path>` names them: `The approved design and the build are shown above.` Steps 1–3 (and a design item's captures) print in the same message, before it,
   and every re-ask (after discussion, or a "more") prints them again before the card. The options are fixed per item kind and listed in this order, which is order, not a recommendation:
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

   For **Changes required** and **Dismiss**, ask in one line of prose for the notes or the reason, after the pick and before the post — in a coordinator-started session take them from the card's notes field instead, since a prose ask is not mirrored. An empty answer posts nothing and re-asks the card.

   For **File as follow-up**, file first, through
   [`../../_shared/follow-up-filing.md`](../../_shared/follow-up-filing.md) with a group of one — one
   `general-purpose` sub-agent. The item is the thread's root text, its flags and its excerpt's anchor,
   in your words; the type is your classification of the finding as `bug`, `incomplete-feature`, `feature` or `deferred-test`, never `revise-existing`, which edits another issue and files none; the parent reference is the PR's URL,
   issue #<N>, and the parent epic when the issue names one. The item ends with the one line `File a new issue only: if the drafter proposes revising an existing issue, approve nothing and return "error: would revise #<M>".` Only a returned URL leads to the reply. A result whose final type is `revise-existing` filed nothing and edited an issue anyway: report that issue in one line, post nothing, keep it for the summary, and re-ask the same card. An
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
${CLAUDE_PLUGIN_ROOT}/scripts/prep_review.py <N> <owner/repo> --refresh --head "<head>" [--phase <P>] --cwd "<facts.workspace.path>"
```

`--phase <P>` is passed when the opening run had it. `--cwd` names the worktree the opening prep asserted, because the shell's directory may have moved during §2 (a `cd` into `facts.scratch` to stage a reply), and run from outside the checkout the re-read fails.

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
  then with **Accept** (on a security item, **Dismiss** with `Filed as #<M>.` as the reason), not with **File as
  follow-up**, which would file a second issue. Then each existing issue a follow-up filing edited instead
  (a `revise-existing` result), by URL, so the operator can check the edit.
- On a re-read, each answered thread whose state differs from its answer, with the state it reads, and each
  answered `id` it does not list. When the threads were not re-read, that line instead, with why (§3).
- What the review did not cover: `left_out` by why (replaced, retired — a trusted review stamp retired it, #206, changes requested — waiting on the resolver,
  superseded, accepted, an operator's thread waiting on the resolver), `review.untrusted`, and the notices; in
  phase mode also `outside_phase`, every listed thread outside phase `<P>` whatever its state.

When a change request waits on the resolver, end with the one line

```
Next: /ctrl-freaq:resolver #<N>
```

— the resolver run that picks the change requests up, on the issue (never `continue #<PR>`). Outside phase mode, put before it `If an evaluator waits at its Threads card for this PR, answer **Re-check** there first: it routes the change request to the resolver.` One waits when
a re-read shows any thread reading `changes-requested`. When the threads were not re-read, the tally's posted
Changes required answers and the opening read's `left_out` `changes-requested` count stand in for that,
except when `<PR>` is no longer open (§3): no resolver run picks its threads up.

**Phase mode** points at the Review card the resolver raised after phase <P> shipped, while `<PR>` is open (no
longer open, it adds no line), each card line ending `(if that card is still open)`. When a change request waits, the last lines are
`Answer **Reviewed, changes required** on the resolver's Review card for phase <P>.` and then the `Next:` line,
the fallback once that card has closed. When none waits, the last line is
`Answer **Reviewed, continue** or **End session** on the resolver's Review card for phase <P>.` Where the
opening read stands in, phase mode counts the `changes-requested` threads in `review.threads_path` (every thread's, not `left_out`'s), leaving out a `replaced` or `retired` one, which waits on no one (#206).
