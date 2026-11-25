# Asking the user a decision — shared reference

When you need a decision from the user — an approval gate, a choice between named
paths, or a confirmation before a GitHub write — ask it through the `AskUserQuestion`
tool, not as freeform prose. The tool renders the same multiple-choice card every
time, so the user pattern-matches the decision at a glance instead of re-parsing a
differently-worded question on each run.

A card is only as good as the operator's grasp of it. Cards asked with no context drew
"I need more context", "I don't understand the question" and "how does this affect the
end user?", and cards whose recommended option was the only real one gave the
appearance of choice without any. The rules below exist to stop both.

## Voice — how a card talks

Every brief, card, supporting block and announced choice (`Did X because Y; say if you'd rather Z`, whose
form is unchanged) is written per [`voice.md`](voice.md) (read it first if it is not already in context) — the
persona, the fixed rules and a repo's own voice. What is card-specific: say
what the decision is, what you checked and what you found, and, on a card that recommends, which way you lean
("Honest options").

## Consent vs judgment — whether a card renders at all

- **A consent card always renders.** It is one of a closed set:
  - an irreversible or outward-facing act — a merge, closing a PR, filing or posting
    where the skill gates it, a landing gate, removing a worktree, pushing with reds —
    except the coordinator's routine close of a worktree whose PR merged (or whose
    issue closed), which is not a consent card (the branch, and any PR, stay, and the
    core's own card still gates dirty, unpushed or diverged state);
  - a decision a contract reserves to the human — a tracked open question, a deviation
    from a project doc, overriding a gate or audit blocker, a hard block, deciding
    what shipped (un-ticking or re-attributing a ticked phase
    or DoD row — the progress record the evaluator verifies against), changing what grounds
    the work (a doc-catalogue `confirm` entry: a `binding` entry added or removed, an
    `authority` changed — `doc-catalogue.md` "Keeping it current"), whether a user-facing issue's
    interface is designed before it is planned (the planner's design detour, `header: "Design"`,
    `planner/references/design-detour.md`, and its revise variant when the design a plan cites is
    unapproved or gone — both render at every rigor level), how strictly builds are held to a repo's design
    records (setup's `Fidelity` card, `setup/references/block-authoring.md`, #241), whether a repo replaces
    the default voice (setup's `Voice` card, the same reference, #250). A security dismissal is a `Security` card
    only for a finding no review thread can anchor, or to collect the reason a thread accepted without one
    lacks (resolved, or by an `accept` decision reply);
    dismissing by resolving a review thread is the human's act in GitHub, never a card.
  - a resolver **review** after a non-final phase (`header: "Review"`) — a human review is required
    before the next phase builds on this one, and only the operator's **Reviewed, continue** answer
    continues the session past it, so it renders even when continuing looks like the only sensible
    answer (`resolver/references/phase-continuation.md` §4). It recommends nothing — the card asks for
    the operator's review — so its brief has no trade-off or clear/close-call line, and **Reviewed,
    continue** is listed first as the card's order, never as a recommendation ("Honest options" does
    not apply), and carries no `(Recommended)` suffix. Its four answers are **Reviewed, continue** / **Reviewed, changes required** / **Review with
    Claude** / **End session**, the third only when the pause's review posted and threads wait on a person, labelled with their count: it prints the phase-scoped
    `/ctrl-freaq:review` command and folder (in a coordinator run the coordinator starts that session on the
    operator's click) and re-asks the card without it — not a "more" re-ask, since the options change — and it
    stays off every re-raise until a new pause review posts.
  - the review tool's per-thread card (`header: "Thread"`, `review/playbooks/review-flow.md`) — each
    answer is posted at once as the operator's decision in that thread. It recommends nothing, for the
    resolver Review card's reason, so its brief has no trade-off or clear/close-call line; its options are
    fixed per item kind (no **Accept** on a security finding), the first listed as order, with no
    `(Recommended)` suffix.

  A consent card with one credible action is an honest confirm — that action +
  **Cancel** — never padded with alternatives nobody would pick.
- **A judgment card renders only when the genuine-choice test (below) leaves two or
  more options.** Left with one, announce it in one line and proceed: `Did X because Y;
  say if you'd rather Z`, naming the strongest alternative as Z and any write X made
  (which issue, which heading). The operator can still redirect from that line, and is
  not stopped to rubber-stamp it. An announced choice is **never** recorded as
  `[user decision <date>]` or as the operator's answer — the operator didn't make it.
- **An operator-task card always renders.** It hands the operator something only they
  can do outside the session — a command to run elsewhere, a checkout to move, a failed
  hook to fix — with the command printed in the same message, and offers `Done` plus
  the task's honest alternative. It is neither a consent nor a judgment card.

## The brief — what the operator reads first

The brief prints in the message before the card, as formatted markdown: bold labels, links and
a short paragraph a reader can skim. It does not go in the card. Only the card's supporting blocks ("Supporting
blocks"), the evidence on "more" ("More context") and output the card's own definition prints in that message (the
resolver `Review` card's session summary and handoff, `resolver/references/phase-continuation.md` §4 step 1) stand
between the brief and the card. The card's `question` renders as plain text
only, so a brief there could not be formatted. #231 put the brief in the card because the dialog can hide text
printed earlier in the turn; #245 moved it back out for the formatting. The card keeps a **compact core**
("The card's question") so it can still be answered when the message is out of view. The brief covers:

1. **An opening paragraph** — one or two sentences: what the decision is and why it comes up now.
2. **What is being decided**, in one sentence — the `**Decide:**` line.
3. **Why now** — the one piece of evidence that raised it, linked where there is a link.
4. **Impact** — `<affected party> — <what changes for it>` on each path, the party being whoever is
   actually affected: customers, on-call, stability, the business, a downstream team. Or
   `None outside the code — <what it touches>` (the PR, the board, the plan's history). Name the real party:
   a decision that changes nothing for end users is not forced through them.
5. **The trade-off** between the recommended path and the strongest alternative; on an
   honest confirm (one action + **Cancel**), between the action and **Cancel** — what
   leaving things as they are costs.
6. **Clear call** or **close call** — so the operator knows whether to deliberate.

A card that recommends nothing has neither of the last two ("Honest options").

**Layout** of the brief — the opening paragraph, then one bold-labelled line per element, a blank line between
them, never one run-on paragraph: the operator scans it, they don't read it. Wrap any line that runs past about
two sentences into the next element or cut it.

```markdown
<One or two sentences: what is being decided, and why it comes up now.>

**Decide:** <what is being decided>

**Why now:** <the evidence, with its link>

**Impact:** <affected party> — <what changes for it>, or None outside the code — <what it touches>

**Trade-off:** <recommended path vs the strongest alternative>

**Close call.**
```

**The card's question** carries the compact core, enough to answer the card from the card alone. It is plain
text, one line per element and a blank line between them, in this order:

1. the identity line, in a session the coordinator started ("Relayed cards");
2. `Decide: <what is being decided>`;
3. any short plain-text line a card's own definition puts there (the `Review` card's `Human review required` line, trigger, phase
   link, file paths and pause-review link with its status, the `Polish` card's proposal tally, the revise confirm's
   classification), and the name of each supporting block the card relies on ("the Polish digest above");
4. the clear/close-call line, on a card whose brief has one;
5. `Answer "more" in Other for the evidence.`

```text
Decide: <what is being decided>

Close call. Answer "more" in Other for the evidence.
```

Why now, Impact and Trade-off leave the card: they are the brief's. Option 1's `description` carries the reason
for the recommendation ("Honest options"). So a card answered with the message out of view still shows what is
being chosen and, on a card that recommends, which way the skill leans and why. The card's `question` renders as plain text — markdown (`*`, `**`,
backticks) shows literally — so text that goes *in* a card carries no markdown; line breaks are its only formatting.

Each option's `description` says what that choice does **here**, in this instance, and what it costs — not
the option's generic definition.

**Supporting blocks.** Whether something is too large for the card is decided by its
shape, not its length: the `question` carries the compact core, and everything else is a block.
Anything else a site shows for the decision — a digest, a table, a diff, a code excerpt, a
list of links, a multi-block confirm (the `Polish` digest, the revise confirm's blocks,
the review-notes block, the slicer's summary table, setup's diffs) — is a **supporting
block**. It prints in the same message as the card, after the brief and before the card, and the `question` names
it ("the Polish digest above"). A card raised again in a later message (a follow-on card, a re-raise, a re-ask
after "more" or discussion) prints its brief and its blocks again with it: the earlier message may be out of
view. A block never replaces the compact core: with one, the `question` still carries every line of its
compact core, so the decision can be made from the card alone.

**A block that pastes code at an anchor** (the `Polish` digest, the `Security` card's findings block) shows, for
each anchor, a one-line gist of what the code there does and a link to it. The code itself prints on "more"
("More context"), and where the anchor gets no link. A before → after sketch, or a security finding's claim, may
stand in for the gist. The link is a script's answer, never a URL the model builds: one `review_threads.py code-links`
call over every anchor the block shows, each caller's reference naming its values. Write the anchors, one
`<path>:<start>-<end>` per line, to `<scratch>/code-links-anchors.txt` with the file-write tool, never on the command
line, in an `echo` or in a heredoc: an anchor is a path from the diff under review, which that PR's author chose, and a
shell would run the `$(…)` or backtick a legal file name can hold (SEC-1 on PR #247). Then run:

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/review_threads.py code-links --root "<checkout>" \
  [--pr-url <pr-url> --base-ref <base branch> --pr-head <pushed 40-hex head>] --anchors-file "<scratch>/code-links-anchors.txt"
```

A non-zero exit (2, a usage error) prints nothing on stdout: treat every anchor as `code`, so the block prints the
code as before, and add one line under the block saying its links could not be built. A `CODE_LINKS_DIFF_UNREAD`
notice comes back with its anchors as `blob` (`diff-unread`): add one line under the block saying the PR's diff
could not be read, so those links open the file at the pushed head rather than its diff. Each anchor's `kind`
decides what prints:

- `line` — a link to the anchor's lines in the PR's Files view;
- `file` — a link to the file's diff there (the lines are outside every hunk, or there are none);
- `blob` — a link to the file at the pushed head (outside the diff, or the diff unread);
- `code` — no link, so the block prints the code as before: there is no PR yet (`no-pr`), or the checkout's file
  differs from the pushed head (`changed-since-push`, `unread`), so the code the entry names is not on GitHub.

`--pr-head` is the pushed head, never local `HEAD`.

On a card with several questions, the message prints one brief per question, each opening with its decision's
short name in bold, and each question carries its own compact core: each is its own decision, and the dialog
shows one question at a time.

## More context — when the operator asks

When the operator answers "more", "why", "I don't understand" or similar in Other,
render the evidence the brief summarised: the code behind every link a block showed, at its anchor, the doc or
plan bullet cited, the reviewer finding verbatim, and what each option does downstream (the next
session it starts, the PR or issue it changes). The evidence prints in the same message as the re-asked card, before it, in every session, after the brief and the card's supporting blocks, which print again ("Supporting blocks"). Then re-ask **the same card** — same
header, same options. Never answer it for them, narrow it, or swap in a different
question: the brief was the summary, this is the detail behind it.

## Honest options — the genuine-choice test

- **Each option needs a "pick this when …"** that a reasonable operator could satisfy
  in *this* instance. An option that fails is dropped, not shown — if that leaves one,
  the consent/judgment rule above decides between a confirm and an announce line.
- **The recommendation is reasoned from this instance's evidence**, goes first as
  option 1, and its `label` ends ` (Recommended)`; its `description` gives the reason — the brief's Trade-off
  line, cut to that option's own terms — so the card alone shows which way the skill leans and why.
  The suffix applies when this instance recommends: on a card offering two or more real actions, a reasoned
  recommendation marks option 1 and gives its reason in that option's description; no recommendation, no suffix. A card that recommends nothing — the resolver's `Review` card, the
  review tool's `Thread` card, setup's `Fidelity` card (how strictly builds are held to a design is the owner's
  preference, #241), setup's `Voice` card (the voice is the team's preference, #250), the retry ladder's equally weighted paths — has no Trade-off or
  clear/close-call line in its brief and lists its first option as order only, with no suffix. A consent card's fixed safe action (Cancel, Abort)
  is never marked, and an honest confirm (one action + **Cancel**) carries no suffix:
  there is no alternative to recommend over. The suffix is applied at render time, only
  when this instance recommends — never written into a skill's option spec — and the
  picked label comes back with it, so read an answer with the suffix stripped.
- A fixed per-card default is allowed only as a consent card's safe action (Cancel,
  Abort) — a recommendation that would be the same whatever the evidence is not a
  judgment.

## Card shape

- One decision per question. `header` ≤ 12 chars (e.g. "Post plan", "Merge mode").
- 2–4 options. Each `label` is the action in imperative form ("Post it", "Squash",
  "Approve"); a reasoned recommendation adds its suffix per "Honest options".
- The tool always appends an "Other" free-text choice, so don't pad to four options
  with a catch-all — leave room for the user to type a custom answer, *more* included.
- `multiSelect: true` only when the choices genuinely combine (rare here).
- Ask once, act on the answer. Don't re-state the same gate in prose afterwards.

When the candidate paths aren't fixed (e.g. "which of these issues did you mean?"),
generate the options dynamically from what you found. When the answer is inherently
open-ended (e.g. "paste any external doc URLs"), a prose ask is still fine — don't
force it into options.

## Relayed cards — when a coordinator started the session

A stage session started by the `coordinator` (its opening brief says so), or a review session it started, is not the only place the
operator reads its cards. The host mirrors the card, and the text printed with it, into the
coordinator's chat. The operator may answer in either session, and the card blocks as usual until
they do. The brief, its supporting blocks and the evidence for "more" print in the same message as the card ("The brief", "More context"), so the mirror carries them with it, and the card's compact core carries what answering it needs. Two things differ here:

- **The identity line.** The `question` opens with `<stage> · #<N> · <step>` (e.g.
  `resolver · #152 · finalisation`; in a review session the coordinator started for a resolver's Review
  card, `review · #<N> · thread <key>`, or `review · #<N> · <step>` for a card no thread owns) on a line
  of its own, then a blank line, before the rest of the compact core: the operator there has not followed this session.
  Only a session the coordinator started carries it.
- **An announced choice** (`Did X because Y; say if you'd rather Z`) raises no card, so nothing
  mirrors it. Put it in the next milestone update to the coordinator, word for word, so the operator
  can still redirect it.

The coordinator never answers a mirrored card itself. An answer typed in either session is the
operator's, and is recorded as `[user decision <date>]` like any other.

## Sub-agents never ask

`AskUserQuestion` is not available inside a sub-agent spawned via the `Agent` tool.
Any gate that arises during sub-agent work must be surfaced by the sub-agent
returning a structured "decision needed" signal to this main loop, which asks the
user and re-dispatches with the answer. Never tell a sub-agent to call
`AskUserQuestion` itself. One closed vocabulary spans scripts and sub-agents:
[architecture.md §3](../../docs/architecture.md)'s decision-code set — the single
source of truth for the typed codes, and the router's universal rule (render a
`decision` as one `AskUserQuestion` card, its brief printed before it and its compact core in its question) for what the main loop does
with one. A script's `decision` carries `summary` and `context`, not a brief: write
the brief from them and the facts block.
