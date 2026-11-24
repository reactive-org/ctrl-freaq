# Pre-push test selection — the §8 and §10.6 gates

How the resolver's two pre-push test gates choose what to run (#244). They used to share one mechanism, the
test-selection sub-agent; now each gate uses the one its diff calls for:

- **§8** (the spine's pre-push verification gate, once per phase, before the review loop): the diff is the whole
  branch, which on a re-entry session this conversation did not write. The test-selection sub-agent
  ([`test-selection-sub-agent.md`](test-selection-sub-agent.md)) keeps the diff hunks and grep output out of the
  main context — which the resolver's context rule (`phase_review.py`, 65% / 90% of the auto-compact point) ends
  sessions on — and its context-blind read guards against the author narrowing its own change. It is dispatched
  alongside the static checks, so its verdict is in hand when they finish.
- **§10.6** (the review loop's re-push gate, `review-fix-round.md` step 7): the diff is the fix this round just
  wrote, minutes earlier, in this conversation. A cold sub-agent re-reading it is almost all overhead, so the main
  conversation selects.

Both gates sit under [`retry-ladder.md`](retry-ladder.md)'s 3-run cap, its run-2 narrowing and its forced research
breakpoint, and both keep the spine's rules unchanged: the `xcodebuild` → `apple-platform-build-tools:builder`
delegation, the `Tests red` card on escalation, and never the full canonical suite.

## §8 — concurrent dispatch

1. **T0.** Run, in the workspace:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/workspace.py tree-state --path "<facts.workspace.path>" --scratch "<facts.scratch>"
   ```

   Keep its `tree` as T0. It fingerprints the working tree — tracked and untracked files, `.gitignore`
   honoured — without touching the checkout's index; a failure is `tree: null` with a
   `TREE_STATE_UNAVAILABLE` notice.
2. **One message, two calls.** Issue both in the same message, so they run concurrently and return together:
   - the `Agent` dispatch — `subagent_type: "Explore"`, `model: "sonnet"`, the prompt template of
     [`test-selection-sub-agent.md`](test-selection-sub-agent.md) filled as it says;
   - one Bash call running every `facts.config.static_checks` command in declared order, each in its own
     subshell, the groups joined by ` && `: `( <check 1> ) && ( <check 2> ) && …`. Each check's own exit status
     is what ` && ` sees, so the first failure stops the rest even when a declared check is itself compound
     (`gen || true`, an embedded `;`), and one check's `cd` cannot leak into the next. A bare ` && ` join breaks
     on a compound check, and a `;` or newline join runs later checks after a failure. Every check's time lands
     on this one call, so give it the host's longest timeout, or run it in the background and wait for its exit;
     a call the host stopped for time is not a failed check — run it again in the background and wait.

   The `model: "sonnet"` pin is a deliberate deviation from the plugin's model-pin convention, under which a
   dispatch inherits the session's model (`CLAUDE.md` "Model/effort are not pinned", `docs/architecture.md` §8): a dispatch-time pin on this one call, not a
   frontmatter key, fixed at every rigor level. It keeps the gate responsive — the slowest dispatches each spent
   135–200 s in one model turn on the inherited model. Every §8 dispatch carries it: this one, the re-dispatch in
   step 5, and retry-ladder run 3's. A dispatch written without the `model` key silently reverts to the inherited
   model.

   With no static checks declared, skip step 1's T0: the dispatch is the only call, and the gate goes straight to
   step 5's print and run once the verdict is in.

   On a host that returns an `Agent` dispatch at once and delivers its verdict later as a completion notice,
   the two calls still run concurrently but do not return together: wait for that notice before step 5, and take
   T1 (step 4) when the checks finish, whether or not the verdict has arrived.
3. **A static check failed** → the gate stops before any test command. The verdict is neither printed nor run,
   even when the fix looks unrelated. Fix the failure, then restart this procedure at step 1.
4. **The checks passed** → run `tree-state` again (the same command) and keep its `tree` as T1.
5. **Compare**, with the sub-agent's verdict in hand. T0 and T1 both non-null and equal → print the sub-agent's `RATIONALE:` verbatim, then run its
   `COMMAND:` in the workspace (skip on `(none)`). Otherwise — a static check rewrote the tree after the
   sub-agent read it, or either reading is null — discard the verdict, dispatch the sub-agent once more alone
   (the same `model: "sonnet"`, no check running), and act on that verdict: print its `RATIONALE:`, run its
   `COMMAND:`. A null never counts as a match: it costs one more dispatch, never an unguarded verdict.

## §10.6 — main-conversation selection

The main conversation selects tests itself, with no sub-agent dispatch. That gives up the context-blind read at
§10.6, an accepted gap: the explicit checklist below and "widen when uncertain" stand in for it, and the
evaluator's canonical run is the backstop — the argument `retry-ladder.md` makes for run 2.

**Inputs.** The round's starting HEAD — `git rev-parse HEAD` in the workspace before the first fix edit; commits
wait for step 8, so it is the last committed, gate-verified state — and `facts.config.test_target_raw`.

**The diff.** `git diff <round-start-sha>` (the working tree, staged and unstaged — no `...HEAD`, which would come
back empty) plus `git ls-files --others --exclude-standard` (new untracked files), both in the workspace. Read
the hunks from your own edits, and a new untracked file's contents in full, since it has no hunk; this is the fix
you just wrote. The diff is empty only when both are.

**Selection.** Apply [`test-selection-sub-agent.md`](test-selection-sub-agent.md)'s prompt-template steps by
reference, so the heuristics have one source:

- step 2's directory listing, only for a declared target the diff can reach;
- step 3's heuristics, building the union: **3a** direct filename mapping per the target's `naming` rule,
  **3b** test files changed directly, **3c** symbol references (introduced, modified, removed or renamed — both
  names), **3d** test-time imports;
- step 4's per-target widening rules: helpers-fallback, broad-change-fallback, and a `none` fallback;
- step 6: a pure-docs or comment-only diff selects `(none)`.

Every grep is name-only (`grep -l` / `grep -rln`): no hunk or match line enters this context beyond the fix you
wrote. Printed grep output would erode the context rule the §8 sub-agent exists to protect.

**The step-5 checklist.** Work it for each changed source file, and print it:

1. **5a** — is the file a high-fanout integration-surface file, by the prompt template's 5a criteria? Answer
   **yes**, **no** or **unsure**. For example, a SwiftUI view, the `@main` entry point or a top-level navigation
   container; a Rails layout, `ApplicationController`, a shared partial or `config/routes.rb`.
2. **5b** — for a yes, trace its consumers with name-only greps across the app tree, excluding tests. Every
   integration test of a consumer is selected, whether or not it references the diff.
3. **5c** — for a yes, does the fix touch a root-reachable surface by the template's 5c criteria (a presentation
   modifier or `@Environment` injection on a root-reachable SwiftUI view; the application layout, a
   broadly-applied `before_action` or routing in Rails)? Then widen to that target's broad-change-fallback; where
   the fallback is `none`, widen to every integration test that transits the surface.
4. **5d** — an unsure at 5a, or a surface 5b or 5c cannot settle, widens. A yes that 5c does not reach keeps
   5b's consumer-traced tests.

**The rationale print**, before the command runs:

```
Selection (§10.6, main conversation):
- <suite> — <3a | 3b | 3c | 3d | 5b>: <what matched>
- widened: <4 helpers-fallback | 4 broad-change-fallback | 5c | 5d> — <the file and why>
- 5a: <file> — <yes | no | unsure>
COMMAND: <the command, or (none) — <why>>
```

One line per suite naming the heuristic or checklist item that picked it, one per widening trigger, one 5a answer
per changed source file, then the command. Then run it in the workspace (skip on `(none)`). The same rules as
the sub-agent apply: zero tests only for an empty or pure-docs diff, and a code change you cannot narrow with
confidence widens rather than running nothing.
