# Common pitfalls

The resolver's anti-patterns, extracted here so they don't consume the load budget. The spine's S5 reads
this file before any code or review-loop work begins. Each bullet is the full text of one pitfall; the
`§N` references name the v2 flow points (the spine's §8 pre-push gate, its §10.6 review-loop re-push gate,
its §review-loop, its S6 DoD projection) that carry the same numbering v1 used, so cross-references from
`retry-ladder.md` / `review-fix-round.md` still resolve.

- **Don't ignore in-progress PRs.** Prep classifies the prior-PR state; a gated row means another author's
  PR already exists — never fall through to opening a duplicate. Opening a duplicate PR wastes everyone's
  time and is rude.
- **Don't take over someone else's PR silently.** If a PR by another author exists for this issue (a gated
  row), the router renders the gate before doing anything that would compete with or supersede it.
- **Don't implement code without grounding in project docs.** Read the docs the repo declares
  (`facts.grounding_docs`, under `facts.grounding_path`) before designing the change and cite the relevant
  sections in the PR. Skipping this leads to implementations that violate non-negotiable project rules
  (layer boundaries, banned APIs, naming, scope) that the docs encode.
- **Don't start non-trivial code work without a finalized plan.** The plan gate stops on a
  `<!-- implementation-plan:v1 -->` plan comment. Missing on a non-trivial issue → stop and ask for one
  (or take the user's explicit `proceed without a plan` override, recorded in the PR body). The plan is
  where the approach was researched and verified; improvising past a missing plan throws that away.
  Trivial fixes and comment-only flows are exempt — the gate doesn't fire for them.
- **Don't re-plan when a plan exists.** When the plan gate finds a plan, consume it — lift its grounding
  and implement its locked decisions. Re-deriving the approach in the main conversation duplicates the
  planner's verified work and risks diverging from the artifact the evaluator will check against. The
  exception is the plan-currency check: if the code or issue body drifted since the plan's SHA, route back
  to the planner in revise mode rather than patching around the staleness.
- **Don't silently deviate from a locked plan decision.** Implementing freely *within* the plan's
  decisions is expected; reversing one (a planned API, layer assignment, or data-model shape that turns out
  wrong) is not — stop and route back to the planner in revise mode with evidence. A quiet workaround
  diverges the diff from the plan, breaks the evaluator's adherence check, and loses the decision's
  provenance.
- **Don't skip the green-baseline check for the integration target.** The integration target is `main` for
  regular issues and the epic integration branch for stories under an open epic. A story under an open epic
  *inherits* the epic-level baseline and shouldn't re-run it — unless `main` has been merged into the epic
  branch since that baseline, or a prior story under the epic landed under an explicit baseline override.
  The point of the gate is correct failure attribution and not shipping over a broken codebase, not running
  tests for their own sake. If the baseline is red, stop and surface every failing test — silent fixes
  scope-creep the PR. Acceptable next moves are detour first, or explicit user override with a documented
  reason.
- **Don't silently fix unrelated pre-existing failures.** If the baseline reveals broken tests outside the
  scope of this issue, surface them — don't fold the fix in without telling the user. It scope-creeps the
  PR and obscures what your change actually did.
- **Don't push code without running the §8 pre-push verification gate.** The test gate runs at §8 (before
  the review loop starts) AND at §10.6 (after addressing review feedback), both ahead of the phase's one
  S5.2 push. Both are mandatory pre-push gates. On a
  clean first-pass review approval, §10.6 never fires — the §8 gate is the only test invocation that runs
  before the push that opens (or updates) the PR. Skipping §8's tests on the assumption that "review will catch it" or "the
  evaluator will catch it" is a bug: the `code-review` skill is a code-quality reviewer that does not run tests,
  and the evaluator runs at PR-readiness time *after* the PR is already open with possibly-broken code on
  the branch.
- **Don't run the full unit + integration suite at the §8/§10.6 story gates.** Those gates run targeted
  tests; the full canonical suite runs in the evaluator (for epic-integration and labelled PRs) and on CI.
  Reproducing the full suite at a story gate defeats the targeted-tests strategy and re-imposes the
  cost-per-iteration this design exists to avoid. If you find yourself reaching for
  `<!-- pr-evaluator-static-checks -->` or `<!-- pr-evaluator-test-target -->` at a story gate, you've
  drifted off the path. One case is *not* drift: the project's declared `broad-change-fallback` **is**
  its canonical suite — a setup-time cost choice, flagged and confirmed at setup's propose-and-confirm
  gate. Honoring that declared fallback when the widening rules land on it is honoring the config; this
  pitfall bans escalating past the resolver's declared blocks to the evaluator's, not following the
  resolver's own. (The epic-baseline / bootstrap / post-rectification flow *does* run the full
  canonical suite — that is the documented exception; see `epic-flow.md`'s "Running the full canonical
  suite".)
- **Don't fall back to zero tests when uncertain.** "Zero tests" is reserved for empty-diff and pure-docs
  paths. Any code change that the sub-agent can't narrow with confidence should hit the project's
  `broad-change-fallback` (typically "all unit tests, no UI") for the unit target. UI uncertainty defers to
  the evaluator (the `none` broad-change-fallback path) — that's intentional. But never push code with zero
  tests run on the theory that "the evaluator will catch it" when widening was the right call.
- **Don't inline the test-selection reasoning in main context.** The diff hunks, directory listings, and
  grep output stay inside the `Explore` sub-agent. Main context sees only the resolved `COMMAND:` and the
  one-line `RATIONALE:`. Inlining the reasoning regresses on token cost and clutters the conversation;
  pulling diff content into main context is exactly what the sub-agent indirection prevents.
- **Don't skip the rationale audit.** Print the sub-agent's `RATIONALE:` line to the user verbatim before
  executing the command. The user must see what was selected and why; silent selection is a regression even
  when correct, and bad selections are how this design fails — make them visible so they can be corrected.
- **Don't let the build subagent become a coder.** When delegating to `apple-platform-build-tools:builder`,
  the prompt must scope the subagent to "run the command and report result" only. No code edits, no
  failure-investigation expansion, no automatic re-runs with different flags. A subagent that silently
  turns a 30-second test run into a 55-minute diagnose-edit-rebuild loop hides changes from your commit
  history and the user's audit trail, and breaks the review-loop's contract that you control when code
  changes happen. If the build subagent reports a failure, surface it; don't hand it carte blanche to fix
  things.
- **Don't iterate small fixes when failures are sticky.** Two §8 (or §10.6) runs with the same failing
  test means the underlying understanding is wrong, not the patch. Take the research breakpoint per
  `retry-ladder.md` — read the full failure output, capture the stack's richest failure context for
  integration tests (e.g. XCUITest `app.debugDescription`, a Capybara `page.html`/screenshot), spawn an
  `Explore` sub-agent for structural read of the failure. Continuing to tweak burns 10–20 minutes per
  attempt with no information gain, and the same loop bounded by the **Don't let the build subagent become
  a coder** pitfall applies one layer up: the *parent* model running tweak → re-run → tweak → re-run is the
  same anti-pattern, just at a different layer.
- **Don't fix the instance when the finding names a class.** Before writing a fix — your own or a
  review finding's — enumerate every site sharing the defect's concept (grep the module/concept, list
  the sites), then fix them all together or record why the siblings differ. A finding names an instance;
  the unit of fix is the class — partial application is how the same defect ships round after round,
  sometimes re-introduced by the very commit fixing it elsewhere. Reading this bullet did not stop it in
  practice, so on a seam the loop has already changed the fix round hands the design to the fix-design
  sub-agent (`review-fix-round.md` step 4) — structure, not vigilance.
- **Don't conform code to a stated invariant a finding contradicts.** An invariant the code states (a
  comment, doc line, or assertion message) that a review finding contradicts is a hypothesis, not a
  target — re-derive whether it is right before routing code through it. Narrowing the statement ("the
  comment is too broad") is a valid and common resolution; forcing a path through an over-broad "one
  doorway" claim makes the sentence true and the behavior wrong. Record which way it was resolved and why.
- **Don't split an atomic call without naming its implicit properties.** A single call often carries
  properties for free — lock+existence, write+state coherence, check+use adjacency. When a fix
  decomposes it, name each property and show where it lands in the decomposition; an unplaced property
  silently disappears (the classic result is a time-of-check/time-of-use gap). If you can't name them,
  don't split.
- **Don't trust an assertion that has never failed.** The vacuous shapes: the "bad" state constructed
  *after* the code under test already read the good state (a record destroyed after context capture, a
  value poisoned after the fixture resolved), and an absence-assertion with no positive control proving
  it can ever fail. In review-loop fix rounds, defect injection (inject → red → revert) is mandatory per
  `review-fix-round.md`'s injection step; for initial-implementation tests, construct the bad state before
  the code under test runs and give every absence-assertion a positive control.
- **Don't `rm` snapshot/golden files to force regeneration.** A failing snapshot test means recorded
  output changed in a way that needs human eyes — a pixel-level visual diff, a serialized-output change, an
  approved-file mismatch — and surfacing that is the whole point of the test category. Surface the diff to
  the user and ask before deleting. Auto-regenerating goldens silently accepts the regression and defeats
  the test's purpose. If the user confirms the change is intended, *then* delete and regenerate; record the
  confirmation in the PR body so reviewers can audit.
- **§8 is the pre-push gate, not the dev inner loop.** Iterate at unit-test granularity locally first —
  most wrappers support targeted single-suite runs (e.g. `xcodebuild -only-testing <Suite>`,
  `bin/rails test path/to/foo_test.rb`, `pytest tests/x.py::TestY`), which typically run in well under a
  minute. Reserve the §8 invocation (which legitimately includes integration tests via the test-selection
  sub-agent's widening rules) for the once-before-push integration check. Treating every code change as
  "make change → §8 → react" turns a 30-second feedback cycle into a 20-minute one and is the most direct
  cause of the small-fix spiral.
- **Don't read only the PR diff.** PR comments and code review threads are where decisions actually got
  made. Skipping them leads to redoing rejected work or contradicting settled directions. Prep reads them
  for you — `facts.review_threads`, the threads a trusted human waits on and the trusted conversation
  (`review-fix-round.md` "Resume hint") — so read those facts, never a fetch of your own: a raw fetch
  brings in an outside account's comments the reader's trust rule keeps out.
- **Don't trust the issue title alone.** The title often reflects the original report; the actual problem
  may have shifted in the comments.
- **Don't re-litigate decided questions.** If a maintainer said "let's go with approach B" three comments
  ago, go with approach B. Re-litigating means asking to make a decision false; a finding that adds a case
  the decision never named is not re-litigation — it completes the decision.
- **Don't open a PR for a question.** Some issues are resolved by an answer, not a code change (the
  comment-only route).
- **Don't skip the review loop.** For any PR, `code-review` must approve before the work is considered done. No
  exceptions, no "this change is too small to review."
- **Don't exit the loop just because the verdict says "approved".** Reviews routinely approve with
  `Medium`, `Low`, or `Nitpick` items — issues *and* suggestions — that the reviewer still expects fixed
  (e.g., "Approved with minor fixes"). The loop settles on the round's **defect count** — zero
  defect-tier Addressable items after your re-classification (spine S5.1 step 3) — never on the approval
  line. Items the reviewer routes elsewhere with a **concrete tracking target** (filed as #N, depends on
  un-landed sibling, citable PRD/scope exclusion) are deferred to the follow-up checkpoint. Soft politeness
  alone ("could be fast-follow", "not blocking", "deferrable", "informational only", "future PR",
  "consider for a future change") is **not** sufficient — re-classify per `review-fix-round.md`'s rubric,
  where provenance comes first and the **default for any concretely-named change is Addressable** when
  this PR introduced it or it sits at a sibling site, then tier it: a real defect keeps the loop open
  whatever the reviewer called it, while polish on the PR's own code is fixed and the rest goes to the
  `## Polish` ledger. Don't tier a defect as polish to settle sooner, or call an introduced finding
  pre-existing to defer it — provenance and tier are recorded in the round reply so the call can be
  audited. Nothing structural enforces this any more:
  the classify-and-act body used to sit behind a sub-agent boundary that made skipping it visible, and it
  now runs in the same conversation that just read the verdict. The rubric applied to every listed item is
  the only guard left, so the hazard this bullet exists for is fully live.
- **Don't move `code-review` into a sub-agent.** The bundled `code-review` skill is not reachable from
  inside an `Agent`-dispatched sub-agent — the `Skill` tool inside a sub-agent only reaches *project, user,
  and plugin* skills (per the sub-agents reference). Putting the reviewer's `Skill` call inside a
  dispatched prompt was the original design and it consistently failed: the sub-agent had no path to the
  actual review and was forced to improvise a manual one, returning prose instead of a real verdict
  (PR #607). `code-review` runs in the main conversation per the spine's S5.1 control — the host may run
  the skill inline or forked from that call, and either way its result returns to the main conversation,
  which is not the dispatched-sub-agent case — and since 4.11.0 the fix round does too. The loop's own
  steps run in main; the sub-agents it dispatches (test selection at the gates, fix design on a hot seam,
  the dismissal check on a round's serious dismissals, the post-settle cold read) are judgment reads that
  never invoke `code-review`. Don't reintroduce a dispatched `code-review` thinking "this time stronger
  emphasis will work" — the constraint is the harness, not the model.
- **Don't reach for Plan-settled, Deferred-by-plan or Refuted without the citation or evidence the
  rubric requires.** The buckets exist to stop re-litigation, not to dismiss findings — a Refuted item
  needs a cited code read or run, never a disagreement, and a fix that leaves the decision bullet true as
  written is not settled (the rubric's still-true test); `review-fix-round.md`'s classification rubric
  owns the rule and its escape hatches. A Refuted, Plan-settled or No shown cost dismissal of a defect-tier
  claim goes to the dismissal check (`review-fix-round.md` step 4; a Deferred-by-plan one does not), so its
  citation must survive a cold reader.
- **Don't scale down verification with the review scope.** Scope narrows the adversarial review only;
  the deterministic gates stay at full strength on every phase, per S5.1 "Scope".
- **Don't stop at any turn-boundary beat in the review loop.** S5.1 has three beats where the
  model can summarize-and-stop before the run finishes; all three are the *PR #416 failure mode* and the
  *#653 missing-handoff failure mode* re-imported one layer up. (a) **After `Skill(code-review)` returns** — the
  verdict reads like a finished deliverable, but `code-review`'s job is only to emit the verdict, not to
  close the loop. Your next beats in the same turn are the classification, the fix plan, and the fix
  edits — or, on a settled verdict, staging the scope diff and dispatching the cold read per S5.1 step 4. (b) **After the fix round's
  commit** — the next beat is re-invoking `Skill(skill="code-review")` with S5.1's Invocation arguments, not
  a recap of what you fixed. (c) **After the cold-read sub-agent returns** — the next beat is a fix round on
  its findings, or S6 when it found nothing. Treat every one of these as a step inside S5.1, not the end of
  it.
- **Don't post review feedback on the issue.** Review feedback on a PR goes on the PR, not on the
  originating issue.
- **Don't mis-route comments between issue and PR.** Problem questions go on the issue, solution questions
  go on the PR. Cross-posting or wrong-routing fragments the discussion and leaves future contributors
  hunting.
- **Don't assume the issue is still relevant.** If the thread has gone quiet for a long time, flag this and
  ask whether to proceed.
- **Don't work outside the asserted workspace, and don't nest worktrees.** All code work happens
  in `facts.workspace.path` — the ambient worktree the operator opened (workspace-open) and prep
  asserted; when a second view is needed, read `facts.read_workspaces.audit.path`. `main` changes
  only via PR — never commit, branch, or stash in any other checkout. workspace-open +
  `workspace.py` own worktree creation, reuse, and the `.worktrees/` exclusion — the prompt never
  runs `git worktree add`.
- **Never remove or `git worktree`-manipulate a worktree — least of all the one this session is
  running in.** A worktree may contain unpushed commits or in-flight edits, and removing the
  session's own checkout pulls the floor out from under it. Every removal — merged, abandoned,
  mis-opened — is the operator's `/github-pipeline:workspace-close <branch>`, which runs the
  teardown hooks and gates dirty/unpushed state as a decision (raw `git worktree remove --force` /
  `git branch -D` silently discard both). When this run surfaces a stale or mis-opened worktree,
  say so and name that command **at that point** — don't defer the flag to the handoff.
- **Don't open a single feature PR for an epic.** Epics are containers; child stories are where code lands.
  Opening a monolithic PR for an epic conflates resolution with implementation and makes the PR
  unreviewable.
- **Don't target `main` for a story under an open epic.** The whole point of the integration branch is to
  keep `main` stable while the epic is in flight. If a story PR points at `main`, that defeats the model.
  The base must be `epic/<N>-<slug>` while the epic is open (the story route bases on `facts.audit_ref`,
  the parent epic branch).
- **Don't let the epic branch drift silently — but only act from epic-as-target runs.** Check epic-vs-main
  drift on every epic-as-target run and rectify when drift is found, choosing rebase or merge per
  `epic-flow.md`'s strategy table. When rebasing, push with `--force-with-lease` (never bare `--force`);
  when merging, push normally. Story runs surface epic-vs-main drift as an informational note only — they
  never rectify the epic branch, because the rectification crosses responsibility boundaries and a
  story-flow rebase would force-push under sibling story PRs. Long-lived branches that aren't periodically
  synced with main become unmergeable, but the rectification belongs to the epic owner.
- **Don't recompute the epic branch slug — discover it.** The slug rule is deterministic for the bootstrap
  path, but the epic title can change *after* a branch is created, and a stricter or shorter informal slug
  rule on a future run silently fails to match. This is exactly how issue #102 was hit: run 1 created
  `epic/102-visual-redesign`, run 2 computed `epic/102-daily-journal-visual-redesign`, and an exact-match
  existence check would have orphaned all the story commits already on the original branch. Prep discovers
  by prefix (`git ls-remote --heads origin "epic/<N>-*"`) and reports `facts.epic.branch`; recompute only
  on the bootstrap path (`facts.epic.bootstrap_slug`, zero matches).
- **Don't run epic baseline in the main checkout.** Both bootstrap and legacy recovery use the asserted
  epic worktree. Running the canonical suite in the main checkout would force a checkout, prevent the
  user from using it for unrelated work during the long suite run, and put a long-lived process outside
  the workspace the run owns.
- **Don't cold-rebuild the canonical suite on every re-run, and don't background it with a relative `cd`.**
  Re-issuing `full-suite` on a re-run re-pays the cold build that dominates wall time (what turned one
  re-baseline into a multi-hour hang), and a relative `cd .worktrees/<branch> && …` `&&`-short-circuits to
  a false "exit 0" with no tests run when the shell is already in the worktree. The full `full-suite` /
  `build-once` / `retry-without-rebuild` re-run discipline, the main-loop-background-bash rule (never a
  sub-agent, which can end its turn mid-build and lose the tally), and the absolute-path rule are specified
  once in `epic-flow.md`'s "Running the full canonical suite" — follow them there.
- **Don't merge the integration PR without running the review loop.** The integration PR lands the entire
  epic on `main` at once — it carries more risk than a single story PR. Apply the review loop to it just as
  you would any story PR.
- **Don't ignore the body checkboxes when closing an epic.** Body checkboxes don't auto-sync. A stale
  `- [ ]` in `## Definition of done` — or, on a legacy epic, next to a closed story — misleads the next
  person who reads the epic. Always tick them before (or as part of) closing. A native story set has no
  checkboxes to tick: GitHub's rollup follows issue state on its own.
- **Don't restructure the epic body template.** The `## Goal` / `## Background` /
  `## Definition of done` section names are load-bearing for traceability from the repo's PRD. Preserve them
  exactly. Don't add a `## Stories` section to an epic that doesn't have one — the story set is the
  native sub-issue relation ([`../../_shared/epic-story-hierarchy.md`](../../_shared/epic-story-hierarchy.md)).
- **Don't edit a parent epic's body from inside a story-target run.** The epic's body is authoritative
  state; it should only be updated from an epic-target run where you can see the full story-reconciliation
  picture.
- **Don't hand-craft follow-up issue bodies.** Every follow-up that warrants an issue goes through the
  sub-agent protocol in `follow-up-tracking.md`. Hand-crafting (writing the body inline, running
  `gh issue create` directly) bypasses the drafter's PRD-grounded review loop and produces issues with
  inconsistent format, missing parent references, and unvalidated framing against the project's
  architecture / constitution. The drafter exists exactly for this. Use it.
- **Don't conflate filing with capturing.** Procedural reminders (drift, epic-checkbox sync, "watch out
  for X in the next iteration") belong in the PR body or the handoff, not as filed issues. Issues are for
  trackable work that needs a separate place to discuss, plan, or assign; PR-body notes are for
  informational caveats a future contributor can act on with the PR context alone. Use the
  filing-vs-capturing criterion in `follow-up-tracking.md`.
- **Don't omit the evaluator handoff on PR outcomes.** The handoff's `Next:` line is the resolver's
  explicit handoff to the merge-readiness gate. Without it, users finish a resolution thinking the work is
  done — but the resolver has only run `code-review` for code quality and targeted tests for verification;
  issue-fit against the originating issue, full canonical-suite execution, and merge-strategy selection
  happen inside the evaluator. Emit it on every PR outcome, including epic-integration PRs (where the merge
  risk is higher and the handoff matters more, not less).
- **Don't hand off to the evaluator until the plan's last phase has shipped.** On a multi-phase issue, the
  PR must not be evaluator-bound while phases in the plan's `## Phases` are still unshipped. Emitting the
  evaluator handoff after a non-final phase invites merging a partial implementation — which is exactly how
  Phase 1 of #640 landed on `main` as #648 before any DoD item was satisfied. Re-route to the resolver (for
  the next code phase) or surface the operator action verbatim (for the next non-code phase) instead. Only
  the *last planned phase shipped* row fires the evaluator handoff.
- **Don't mark a multi-phase PR ready, and don't add `Closes #N` in reaction to shipping the last phase.**
  A multi-phase PR opens as draft and stays draft through every phase the resolver pushes — including the
  last one, until you flip it draft→ready immediately before the last-phase handoff. Fiddling with the
  `Closes` directive in the title or body is judgment that belongs to the evaluator: it owns the DoD check
  that decides whether the shipped phases actually satisfy the issue.
- **Don't tick DoD bullets the phase's `closes-dod` doesn't claim.** The resolver projects the planner's
  declaration onto the issue body; it doesn't infer. If a phase's `closes-dod` lists bullets `[1, 3]`,
  those are the only bullets that flip on this push — even if the diff happens to also satisfy bullet 2.
  Bullet 2 is claimed by a different phase (per the planner's dimension-7 exact-coverage invariant), and
  ticking it here would mis-attribute the closure. If you believe the plan is wrong, route back to the
  planner in revise mode; never silently tick beyond the declaration.
- **Don't re-tick DoD bullets the evaluator has rejected.** A bullet annotated `- [ ] <text> (resolver
  claimed phase <N>, ...; evaluator rejected: ...)` is the evaluator's sticky veto — the diff in the
  attributed commit(s) didn't actually satisfy the bullet. Projection logic treats annotated-as-rejected
  bullets as not-projected even when `Phase tracker × closes-dod` would tick them. The disagreement is
  resolved by re-planning, by a new code phase whose diff actually satisfies the bullet, or by user
  intervention — not by silent re-ticking on the next push. Re-ticking would clobber the evaluator's
  evidence and re-introduce the silent rubber-stamping failure mode the per-phase verification exists to
  prevent.
