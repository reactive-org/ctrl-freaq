# Polish triage — shared procedure

The polish ledger's undecided entries ([`polish-ledger.md`](polish-ledger.md)) are decided by the
operator on a `Polish` card, after a digest that shows the code behind each one. Two skills run this
procedure, in this order on a PR's way to merge:

- the **resolver's finalisation** (`resolver/references/finalisation.md`) — on the final pass, before the
  phase's push, so an applied entry costs one fix round in the same session;
- the **evaluator's S5.5** (`evaluator/references/polish-adjudication.md`) — the backstop for whatever is
  still undecided when the PR reaches it, before the review posts.

This file is the single source of truth for the steps both share: re-check each entry at head, propose a
disposition, group by theme, show the digest, ask, and record the answers. Each caller supplies **the entries** it triages, **the
checkout** it reads them in, **the plan** its scope test reads, **its apply bar**, and **what follows the
answers** (its write, its route); everything else stays in its own reference.

## 1. Re-check each entry's claim at head

Read the entry's anchor in the caller's checkout and check the entry's **claim**, not only that the
anchor still exists — the reviewer wrote it against an earlier diff, and a later phase or fix round may
have made it false or narrower. False as written → propose `drop` with the note
`premise false at head — <why>`; true only in part → keep the item text and put what is actually true at
head in the note (`narrowed: <what holds>`), so a filed follow-up carries the corrected claim, not the
reviewer's.

## 2. Propose a disposition

Propose one of the following, with a one-line reason as the entry's note:

- **`apply`** — the entry clears the caller's **apply bar** ("The apply bar").
- **re-plan** — the entry sits inside this PR's scope (the plan's `## Changes` / phase `ships`, or the
  issue's Definition of done in `facts.dod`) but its fix needs a decision the plan does not make: it would
  make a plan decision bullet **false as written** (reverses a choice, redefines a term the plan defines,
  moves work the plan places elsewhere — a list of required behaviour is a minimum, a definition is not),
  or the plan and the Definition of done leave the intended behaviour open, so two fixes fit, each serving
  a different intent. The note names the bullet, or the two intents. This is a proposal, not a
  disposition: the operator's **Re-plan** answer records `apply` and the caller routes to the planner.
- **`file`** — worth doing, but not in this PR at the caller's bar. Filed **after the merge** by the
  evaluator's routed playbook's residual step, grouped per
  [`follow-up-filing.md`](follow-up-filing.md) (polish groups file as `incomplete-feature`) — never
  earlier, so a PR that never merges files nothing. An entry **outside** this PR's scope that also needs
  an intent decided files too — the note states the open question, so the follow-up's planner answers it
  rather than inheriting a guess.
- **`drop`** — taste rather than improvement, a premise false at head, or **no longer applies at head**
  (a later phase removed or rewrote the site). The note says which.

The loop records a **no-shown-cost** finding as `drop` itself (`polish-ledger.md` "Rules"), so it never
reaches this step. An `open` entry whose note leads `no shown cost:` (one written before that rule) is
recorded `drop` the same way, without asking — it joins the dropped list (§4) — unless the re-check finds
the cost it lacked, which makes it an ordinary entry to propose. That second rule reads a note from the
body, so it holds only when `facts.polish.dispositions_trusted` is true: on an untrusted body every entry
already on the ledger is an ordinary entry to propose, whatever its disposition or note says
(`polish-ledger.md` "Rules", the untrusted-body rule). The loop's own findings this pass are unaffected.

An entry that turns out to be a real defect is not polish; each caller's reference says what happens to
it, and the card never asks about it.

### The apply bar

The one proposal rule that differs by caller, because applying costs each a different amount:

- **Resolver finalisation** — the entry is in this PR's scope (the re-plan bullet's scope test), still
  true at head, and clearly right rather than taste. Applying costs one fix round in this session, so
  size alone is not a reason to file: `file` is for an entry outside the scope, or one big enough to need
  its own plan (a new module, a cross-seam refactor). The proposer is the session whose loop left the
  entry, so judge it against this bar, not against the reason the loop left it — a guard-path refactor,
  a change beyond the PR's seam, or code the PR does not change were the loop's reasons to stop, not
  reasons to file.
- **Evaluator** — the entry meets [`polish-ledger.md`](polish-ledger.md) "Apply criteria": it would be
  **actively bad to merge**. Applying there costs a soft-reject, a whole resolver session and a second
  evaluation, and the operator has usually answered finalisation's card already.

## 3. Group by theme

Group the entries the way the operator decides them, not the way they would be filed: one group per
**theme** — what the entries have in common, in the operator's terms ("Stale comments and docs",
"Naming", "Duplicated helpers", "Error messages") — with a one-line reason they belong together.
**Every entry in a group shares one proposal**, so one answer fits the whole group: a theme whose entries
were proposed differently splits by proposal ("Naming — apply", "Naming — drop"). The seam-and-type
grouping that once shaped the card read as random and left the operator splitting answers by id.

Keep it to **at most 4 groups**, so they fit on one card: past 4, merge the smallest groups that share a
proposal into one labelled `Minor — <proposal>`. **Never merge re-plan groups**: each re-plan entry names a
different plan decision, so one answer must not cover two of them. Merging `apply` / `file` / `drop`
always reaches one group each, so only re-plan groups can still exceed the cap; those spill to further
cards (§5). A group of one is fine.

Separately, give every entry proposed `file` its **filing group** — the follow-up issue it would file
under, per [`follow-up-filing.md`](follow-up-filing.md) "Grouping" (same type, same seam, at most ~5
items). A theme can span seams and would file as one unfocused issue, so the card's groups never decide
the filing; the digest shows the filing groups so the operator approves those too.

## 4. Show the digest

Before any card, print the digest as plain text — it is the context the card's question has no room for,
so the operator can judge an entry without opening the code. One block per group: its label, its
proposal, and per entry:

- **the id** and **what is wrong**, in plain words rather than the reviewer's;
- **the code at the anchor** — a short excerpt (at most ~10 lines) read from the caller's checkout, or a
  before → after sketch of the change the entry asks for. A group proposed `drop` gets the other lines
  only: an excerpt there is mostly skimmed, and the operator who doubts a drop can ask for the code;
- **the reason** for the proposal, and **what applying it costs** (the sites and files it touches);
- any `premise false` / `narrowed` correction (§1), and any marker the caller adds to a re-proposed entry.

A group proposed `file` ends with the follow-up issues it would file as — each filing group's title and
its ids. The digest closes with **Dropped, no shown cost** — one line per entry (its id, item and why),
so the operator can revive one (§5): finalisation lists every `drop` whose note leads `no shown cost:`,
in the ledger or held for this pass's S5.2 write; the evaluator lists the `open` ones §2 dropped this run.

**When nothing is left to ask**, print the dropped list alone whenever it has entries — the operator
still sees what was dropped; no card follows, and the drops stand.

The digest is not a gate: the card that follows is the one place the operator answers.

## 5. Ask

Ask a `Polish` card (`header: "Polish"`) whose single question offers:

- **Take all recommendations** — first, marked recommended; every entry takes its proposal;
- **Review by group** — a second card asks group by group.

The question sums up the proposals ("apply 4; file 3 as 2 issues; drop 5"). **A re-plan proposal is
always named with its route** ("re-plan 1 — P2.3 goes to the planner before the resolver continues"):
taking it re-routes the session, so the take-all answer must not hide it. Its "Other" takes overrides by
id ("all, but apply P1.3"), including reviving a no-shown-cost drop from the digest's dropped list — the
revived entry takes the answer given. With only one group, skip this question and ask that group's
question directly; that question's "Other" then takes the revivals too.

On **Review by group**, ask a second `Polish` card with one question per group — at most 4; re-plan
groups past the cap (§3) follow on further cards, 4 questions each. Each question names the group's theme
and ids (the digest carries the detail) and offers the options below, the proposal first and marked
recommended:

- **Apply in this PR** — the resolver fixes it before the merge.
- **File as follow-up** — filed after the merge, as the digest's filing groups.
- **Re-plan** — the planner revises the plan for it before the resolver continues. Offered only on a
  group proposed `apply` or re-plan: re-plan is an in-scope answer, and a group proposed `file` or `drop`
  takes the other three.
- **Drop**

Each option's description says what the choice does **for this group** ("fixes the 3 stale comments in
one fix round"), not the generic effect. The tool's "Other" takes a split answer by id ("file all but
P1.3; re-plan P1.3"); apply it entry by entry. A caller may give "Other" one reading of its own, named in
its reference.

## 6. Record the answers

- **File as follow-up** → `file`, the note carrying `group: <its filing group from the digest>` — the
  residual step files one issue per recorded group, as the operator saw it
  ([`polish-ledger.md`](polish-ledger.md) "Rules"). An entry answered File that the digest gave no filing
  group joins the digest's filing group on the same seam when one exists, else forms a group of its own.
- **Drop** → `drop`.
- **Apply in this PR** → `apply`. On an entry proposed as re-plan because two intents fit, ask one
  follow-up question first — one option per intent the note names — and record the answer as
  `intent: <chosen>` in the note: the resolver implements that intent and never picks one itself.
- **Re-plan** → `apply`, the note prefixed `operator: re-plan —`, which forces the caller's planner route
  whatever its own plan-change test says.
- **Take all recommendations** → each entry's proposal, recorded as if answered entry by entry (a
  re-plan proposal as **Re-plan**, whose planner revise settles any open intent).

When an answer differs from the proposal, the note leads with `operator: <answer>` and keeps the
proposer's reason after it. Every answer is the operator's decision, so a later triage never asks about a
decided entry again, with two exceptions the evaluator owns: an `apply` the resolver left unfixed, which
it re-proposes so the operator can release it, and a `premise false` drop whose claim it finds holds at
head (`premise disputed`). An `open` entry noted `operator: leave for evaluator` is the operator's answer
to wait for the evaluator: finalisation skips it. All of this holds only when
`facts.polish.dispositions_trusted` is true: on an untrusted body no recorded disposition or note is an
answer, so every entry is asked again and neither exception nor the skip applies (`polish-ledger.md`
"Rules"). The caller writes the answers once, through the single write
path; the ledger never records a decision the operator overrode, and entries are never deleted
([`polish-ledger.md`](polish-ledger.md) "Rules").
