# coordinator — the turn loop (shared spine)

Every coordinator turn runs these steps, in order. The target's playbook (`epic.md` / `single.md`)
supplies only what differs by shape: which stage follows which, and what is said at the ends.

## 1. Prep

Run the router §1 call, passing an action flag only when running that routine step (§4, §5), and
`--story <M>` whenever this run is focused on a story (named by the operator or the relayed `Next:`,
or picked at `next-story`).

## 2. Verify (on a wake)

When a stage session's end woke you, its report arrives as the host's end-of-turn note (about 400
characters) and, if the brief was followed, its full `## Handoff` by message. Before reporting
anything, read the new facts — GitHub, through prep, says where the issue is now — and:

1. Check the stage's claims that matter against them: a claimed merge against `unit.pr.merged` and
   the issue's `closed`; a claimed PR or its draft/ready state against `unit.pr`; a claimed close
   against the target's `state`. A claim the facts contradict is said plainly, with both sides.
2. If the stage stopped without finishing — its note says it waits on the operator, or neither a
   handoff nor any GitHub change arrived — say where it stopped and that its session is still open;
   offer to re-start that stage only if the operator says the session is gone.

## 3. Reconcile the relayed handoff

Compare the handoff's next command with `next.command`:

- **They agree** (same skill, same target): continue to §4.
- **The handoff names the step after one the coordinator runs itself** — a stage never opens,
  closes or syncs a worktree, so its next command lands one step later: the planner hands off
  `resolver #N` while `next.kind` is `open`; the evaluator's story merge, `planner #<next>` while it
  is `close` or `pick`; a handoff naming `workspace-open` / `workspace-close` names exactly the
  coordinator's step. That is agreement, never a card: run the coordinator's own step (§4) first,
  and take a story number the handoff names as the pick.
- **The handoff names a route GitHub cannot show** — resolver → planner (re-plan), planner →
  researcher, planner → slicer, anything → drafter (revise), evaluator → slicer (adopt): the facts
  still read the old stage, because a re-route leaves no trace until its stage runs. Render a
  judgment card: the brief quotes the handoff's `Why:` line verbatim; option 1 is the route the
  evidence supports (usually the handoff's), the other `next.command`; each option names the folder
  from `routes[<skill>]`. The chosen start then goes through §4 like any other.
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
  receipt in one line (the branch and the PR stay), and continue with the new `next` in the same
  turn. Each `epic.stories[]` entry that is `closed` and still has a `worktree` (held back while a
  story is in focus) closes the same way, by that `worktree.branch` from the facts, not the
  `attention` line.
- **`open`** — end the turn with a button naming the open and the stage after it ("Open the worktree
  for #152, then start resolver #152 in it"); the folder is unknown until the open mints the branch,
  which it links to the issue on GitHub. An epic worktree opened before any story is in focus is
  followed by a `pick`: make it now (below) and announce it, so the button names that story and the
  click runs `--open <N> --story <M>`.
- **`pick`** — the next story: the relayed `Next:` when it names one of `epic.candidates`, else the
  first in the epic plan's story order (at `epic.plan_path`, when given), announced in one line
  (`Picked #N because …; say if you'd rather #M`); then re-run prep with `--story <N>`.
- **`none`** — `refused` and `gated` render their card (router §2); `terminal`, the summary (router §4).

## 5. Start the stage (on the operator's click — their own message naming the step), in order

1. Run the workspace step the go-ahead named: `--open` (with its `--story`) or `--sync`. A card from
   it renders and the start waits for it. Announce the receipt in one line (`receipt.sync.before` →
   `after`, or "already at the tip"); a `setup_failed` receipt stops the start: a `Your step` card,
   `Done` (fixed — re-run prep, offer the start button) / `Start anyway` (a resolver or evaluator
   re-runs setup on entry; a planner or slicer starts with the failure in its brief's "Where the run
   is" line). After an open, start only when the post-open `next` is a `session` with the command
   the button named, in its `next.folder`; otherwise report what prep says now and offer that step.
2. Where a session-start tool is available, start a **fresh** session in that exact folder (as it
   is, no new worktree) with the brief from [`../references/stage-brief.md`](../references/stage-brief.md)
   filled in, the effort the operator set for this run (else inherited), and a request to wake you
   when it finishes; send it one short message right away, so it has your address for its updates.
   Report the start in one line, linking the session, and end the turn.

Where no session-start tool is available, print the command in its own fenced block and the folder,
in the same message as a `Run stage` card (`header: "Run stage"`): `Done` (it ran and finished — the
next turn starts at §1) / `Stop the run` (end with the status line).

## 6. While a stage runs

The stage's decision cards are mirrored into this chat with the text beside them, and the operator
may answer in either session. Never answer or restate one; if asked about one, name its stage and
issue (from its identity line). Relay updates and summaries per §3. Start nothing else for this
issue until the stage ends, and close nothing.
