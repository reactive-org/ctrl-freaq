# coordinator — the turn loop (shared spine)

Every coordinator turn runs these steps, in order. The target's playbook (`epic.md` / `single.md`)
supplies only what differs by shape: which stage follows which, and what is said at the ends.

## 1. Prep

Run the router §1 call, passing an action flag only when running that routine step (§4, §5).

## 2. Verify (on a wake)

A stage's end arrives as the host's end-of-turn note (about 400 characters) and, if it followed the
brief, its full `## Handoff` by message. Before reporting anything, read the new facts (prep) and:

1. Check the stage's claims that matter against them: a claimed merge against `unit.pr.merged` and
   the issue's `closed`; a claimed PR or its draft/ready state against `unit.pr`; a claimed close
   against the target's `state`. A claim the facts contradict is said plainly, with both sides.
2. If the stage stopped without finishing — its note says it waits on the operator, or neither a
   handoff nor any GitHub change arrived — say where it stopped and that its session is still open;
   offer to re-start that stage only if the operator says the session is gone.

## 3. Reconcile the relayed handoff

Compare the handoff's next command with `next.command` — at `stories`, with the `next` of the entry
for the story the session was started for (the issue in its brief and start name), never the number
in the command, since a resolver's `evaluator #<PR>` names a PR:

- **They agree** (same skill, same target): continue to §4.
- **The handoff names the step after one the coordinator runs itself** — a stage never opens,
  closes or syncs a worktree, so its next command lands one step later: the planner hands off
  `resolver #N` while `next.kind` is `open`; the evaluator's story merge, `planner #M` while it is
  `close`; a handoff naming `workspace-open` / `workspace-close` names exactly the coordinator's
  step. That is agreement, never a card: run the coordinator's own step (§4) first. The story-merge
  `planner #M` also reads M's entry, never a pick: the gates decide, and a gated `#M` is announced.
- **The handoff names a route GitHub cannot show** — resolver → planner (re-plan), planner →
  researcher, planner → slicer, anything → drafter (revise), evaluator → slicer (adopt): the facts
  still read the old stage, because a re-route leaves no trace until its stage runs. Render a
  judgment card: the brief quotes the handoff's `Why:` line verbatim; option 1 is the route the
  evidence supports (usually the handoff's), the other `next.command`; each names the folder from
  `routes[<skill>]` (a story's resolver or evaluator: its entry's `worktree.path`), then §4.
- **They contradict on state** (the handoff says merged, the facts say open): a card, with the
  facts' reading first — GitHub is the authority. Never pick silently either way.
- **No handoff arrived:** proceed on the facts, and say in one line that no handoff came back.

Milestone updates the stage sent while it ran, and any announced choice in them, are relayed in one
line each as they arrive; a summary it sends (a `## Plan summary`, a Polish digest, a `## Session
summary`, a verdict and merge plan) is posted verbatim in this chat as it arrives.

## 4. Offer the next step, by `next.kind`

- **`session`** — report in one or two lines, then end the turn offering the start button (router
  §3) carrying `next.command` and `next.folder`; with `next.sync_first` it names the sync too
  ("Sync the epic worktree, then start planner #152 in <folder>"). An `attention` line saying the
  project root is off the default tip (the session would refuse its checkout) is a `Your step` card
  first (router §3); `Start anyway` keeps the warning in the button's message.
- **`close`** — the routine close (router §3), never on a milestone-update wake. A run's first turn
  can't see sessions an earlier run started, so it does not close unprompted: it offers the turn's
  button ("Close the worktree for #152, then carry on"). Run `--close <next.branch>`, announce the
  receipt in one line (the branch, and any PR, stay), and continue with the new `next` in the same
  turn; an epic entry's `close` runs the same way. After a close, the archive point (router §3).
- **`open`** — end the turn with a button naming the open and the stage after it ("Open the worktree
  for #152, then start resolver #152 in it"); the folder is unknown until the open mints the branch,
  which it links to the issue on GitHub. A run's first open of a work worktree says beside the button
  that Desktop may ask once to trust that folder in the new session, citing the first item (accept
  once per worktree folder) of `${CLAUDE_PLUGIN_ROOT}/skills/workspace-open/references/workspace-trust.md` `## What you can set yourself`.
- **`stories`** — read each entry's `next` against its gates (`epic.md` §2): closes first, then one
  button carrying, in order, the first-turn close, the archive offer and every start the gates allow.
  The second button: the same without the archive when it rides beside other steps; `Not now` when it
  is alone (the summary names the sessions left unarchived); else, with two or more starts, the first
  start alone. A behind epic worktree's sync is named once, before the first planner start.
- **`none`** — `refused` and `gated` render their card (router §2); `terminal`, the summary (router §4).

## 5. Start the stage (on the operator's click — their own message naming the step), in order

1. Run the steps the go-ahead named, in its order: one prep call per folded `--open` or `--sync`,
   then its start; a card from one holds only its own start. Announce each receipt in one line
   (`receipt.sync.before` → `after`, or "already at the tip"); a `setup_failed` receipt stops the
   start: a `Your step` card, `Done` (fixed — re-run prep, offer the start button) / `Start anyway`
   (a resolver or evaluator re-runs setup on entry; a planner or slicer starts with the failure in
   its brief's "Where the run is" line). After an open, start only when the post-open `next` (on an
   epic, the named story's entry) is a `session` with the command the button named, in its
   `next.folder`; otherwise report what prep says now and offer that step.
2. Where a session-start tool is available, start a **fresh** session in that exact folder (as it
   is, no new worktree) with the brief from [`../references/stage-brief.md`](../references/stage-brief.md)
   filled in, the run's effort (else inherited), and a request to wake you when it finishes; send it
   one short message at once (your address for its updates). Report the start in one line, linked.

Where no session-start tool is available, print the command in its own fenced block and the folder,
in the same message as a `Run stage` card (`header: "Run stage"`): `Done` (it ran and finished —
re-run prep, §1) / `Stop the run` (end with the status line).

## 6. While a stage runs

Its decision cards are mirrored here with the text beside them, answerable in either session. Never
answer or restate one; asked about one, name its stage and issue (its identity line). Relay updates
and summaries per §3. Start nothing else for that issue until it ends, and close nothing it runs in.
