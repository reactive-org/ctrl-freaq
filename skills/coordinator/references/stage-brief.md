# The stage brief — what every stage session is started with

Fill the `<<…>>` placeholders and send the result as the stage session's opening prompt. It is the
operator's go-ahead, relayed: the operator clicked to start this stage, and the brief is the only
context the fresh session has. Keep the background short — the stage's own prep reads GitHub; the
brief carries what GitHub can't (why this stage, now, and what the operator decided in this run).

`<<contract_path>>` is the absolute path of the shared card contract — the router's
`${CLAUDE_PLUGIN_ROOT}/skills/_shared/asking-the-user.md`, resolved (this file is read raw, so the
coordinator substitutes it). The command sits inside the prose, not on a line of its own: a
prompt that opens with a slash command is parsed as that command's arguments, which would feed the
whole brief to the skill as its target.

```
You are one stage of a coordinated pipeline run. Run `<<command>>` — exactly that skill and
argument — in this folder: <<folder>>

Background
- Target: <<target>> (<<type>>). This stage: <<stage>>.
- Where the run is: <<one or two lines — what earlier stages did, with their PR/issue numbers>>.
- Operator decisions that bind this stage: <<each one verbatim, or "none">>.
- Base check: <<an epic story's evaluator: before merging, fetch and confirm with `git merge-base
  --is-ancestor` that the PR head contains the current `origin/<epic branch>` tip; a miss is
  base-moved — no merge, hand back to the resolver. A `base_moved` resolver re-entry: merge the
  epic tip into the story branch (never rebase), re-run the gates, push. Any other stage: drop
  this line>>.

How to report — a coordinator session is driving this run, and the operator is watching it there
- Send milestone updates to the coordinator as you go, one line each: when your prep finishes, at
  each phase or review-loop milestone, and before any long-running step. Its address is the sender
  of the first message you receive — hold any update until that message arrives, then reply to it. Include every announced choice ("Did X because Y; say if you'd
  rather Z") word for word, so the operator can still redirect it.
- Your decision cards are mirrored into the coordinator, where the operator may answer them. Follow
  "Relayed cards" in <<contract_path>>: every card is self-contained — its question opens with
  "<<stage>> · #<<number>> · <step>" and then the brief — and any block the card relies on, or the
  evidence for "more", is printed in the same message as the card.
- Send your human-readable summaries to the coordinator too, each verbatim as its own message, as
  you produce each — skip any that isn't your stage's: the planner's `## Plan summary` once the plan is
  posted, before the `## Handoff`; the resolver's `## Plan summary` at session start, its Polish
  digest alongside the `Polish` card, and its `## Session summary` before the `## Handoff`; the
  evaluator's verdict and merge plan, before it merges or asks to.
- Anything the operator must do themselves (a command to run, a manual step) goes on a card, never
  only in running output.
- When you finish, send your full `## Handoff` block to the coordinator verbatim, as one message.
  If you stop early (a card left unanswered, an error), say so in one line instead.

Scope
- Run this one stage. Don't start another stage, open or close a worktree, or chain to the next
  skill — the coordinator does that, starting a stage only on the operator's word.
- If the operator asks you here for something outside this stage, do it if asked, and say in your
  next update that it is outside the pipeline record.
```
