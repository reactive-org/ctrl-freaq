# Test-selection sub-agent (carried from v1)

Carried from the v1 resolver's `references/test-selection-sub-agent.md` per the S10
cutover — a judgment sub-agent prompt (architecture.md §9). The sub-agent receives the **workspace path**
from the prep facts block (`facts.workspace.path`) rather than resolving a worktree itself, and the
integration target is the **bare** `facts.audit_ref` name (`main`, or the parent epic's branch for a story).
The heuristics, per-target widening rules, and blast-radius pass are unchanged apart from step 5's tool-call
budget (#244).

The evaluator's twin of this prompt (`skills/evaluator/references/test-selection-sub-agent.md`) is unchanged
and deliberately differs: its scope is the PR's whole cumulative diff at merge-readiness. This copy's step 1
reads the working tree, because the §8 gate runs before the phase's commit, and its step 5 carries a budget;
neither is ported there. This copy once also carried a scope input for the review loop's §10.6 gate, retired
when §10.6 moved to selecting in the main conversation.

The resolver's §8 pre-push verification gate (spine §S5) spawns this read-only `Explore` sub-agent, with
`model: "sonnet"`, per [`pre-push-test-selection.md`](pre-push-test-selection.md) "§8", to pick which test
suites to run for the branch's diff. Reasoning happens entirely inside the sub-agent so the main conversation
never sees the diff hunks, the test directory listings, or the grep output — only the two-section verdict.
The sub-agent is context-blind, cannot call `AskUserQuestion`, and never writes to GitHub (architecture.md §8);
it returns exactly two sections (`COMMAND:` + `RATIONALE:`) to the main loop.

## Prompt template

Substitute the placeholders (`facts.workspace.path`, the bare `facts.audit_ref` integration target, and
the project's `<!-- issue-resolver-test-target -->` block contents = `facts.config.test_target_raw`) at
call time.

```
You are selecting which test suites to run for a Claude Code dev iteration of the
resolver skill. Your output drives the next test command; the rest of
the workflow does not see your reasoning, so be explicit in your rationale.

Inputs:
- Worktree path: <facts.workspace.path>
- Integration target: <facts.audit_ref — main, or epic/<N>-<slug> for stories under an open epic>
- Test-target config (verbatim from the project's COMMANDS.md / CLAUDE.md):
  <contents of the <!-- issue-resolver-test-target --> block = facts.config.test_target_raw>

Steps:

1. Compute the change set from the worktree, reading the working tree as well as
   the commits. Run `git merge-base <integration-target> HEAD`, then
   `git diff <that sha>`, then `git ls-files --others --exclude-standard` for new
   untracked files — three plain commands, no command substitution. When the
   merge-base fails (no local branch of that name), use `origin/<integration-target>`
   in its place; when that fails too, return the broad-change-fallback and say why.
   The phase's work may not be committed yet, and it is the change under test.
   Read both the file paths and the hunk contents — don't decide based on paths alone;
   a new untracked file has no hunk, so read its contents in full.
   If the diff and the untracked list are both empty, return COMMAND: (none) and a
   one-line rationale.
2. List each declared target's directory: `ls <target>/` for each target in the config.
3. Apply these heuristics in order, building a union of suite identifiers across all
   declared targets:
   a. Direct filename mapping per the target's `naming` rule (when a 1:1 mapping is
      declared and the corresponding test file exists).
   b. Test files modified directly → include their suite.
   c. Symbol references — identify symbols introduced, modified, removed, or renamed
      in the diff (types, functions, identifiers, error cases,
      i18n/string keys). For renames, search both the old and new names.
      `grep -l` across each target's directory; include any test file that mentions
      any matching symbol. Translate file paths to suite identifiers using the
      target's `naming` rule.
   d. Test-time import tracking — a test file that imports a sub-module touched
      by the diff is a candidate even if no explicit symbol matches (e.g. a Swift
      `@testable import` pulling in extension methods / Codable conformances, a Ruby
      `require`/autoload pulling in a monkey-patch).
4. Apply per-target widening rules from the config:
   - Helpers-fallback triggers when a test-side helper changes (a file in the
     target's directory not matching the suite-naming pattern).
   - Broad-change-fallback triggers when the diff changes a widely-referenced type
     (more than ~5 test files mention it), a persistence-model schema, generated
     config, or any change you cannot narrow with confidence.
   - If a target declares a fallback as `none`, do not widen for that target —
     return zero suites for it instead and let the evaluator's canonical
     run cover the gap.
5. Integration blast-radius exploration. Name/symbol proximity is fine for unit
   tests but a poor proxy for *integration tests* (UI, system, feature, or
   request tests), which exercise shared application surface that a symbol-grep
   won't connect back to the diff. A small change to a **high-fanout
   integration-surface file** — one many parts of the app route through — can
   break integration tests that never mention the changed symbol, and step 3's
   symbol-grep won't catch it. Before finalising the integration-test set, do a
   focused exploration pass:

   a. For each modified source file, decide whether it is a high-fanout
      integration-surface file: one that a large share of the app's integration
      tests transit, directly or indirectly. What that looks like depends on the
      stack — infer it from the wrapper command and the source layout:
      - **SwiftUI / XCUITest** — a view (declares `: View`, or matches the
        project's view-naming convention, e.g. `*View.swift`); the app entry
        point (`@main`); a top-level navigation container or a view that gates
        the rest of the UI behind a sheet.
      - **Rails / system specs** — a shared layout (`app/views/layouts/*`),
        `ApplicationController` or a broadly-applied `before_action`, a shared
        partial / concern / helper, or routing (`config/routes.rb`).

   b. For each such file, trace its consumers: `grep -rln "<TypeName>("` (or the
      stack's equivalent reference — a rendered partial, a route helper) across
      the app tree, excluding tests. Build a small list of "files that use this
      one." If any consumer has integration tests (by symbol grep or name
      proximity), those tests are candidates regardless of whether they
      reference the diff directly.

   c. Treat the change as broad integration impact — and widen the integration
      selection to the per-target broad-change-fallback (or, if that is `none`,
      to the union of every integration-test file that transits the affected
      surface) — when the diff touches a root-reachable, high-fanout surface:

      - **SwiftUI / XCUITest** — the diff modifies the app entry point (`@main`)
        or the top-level body composition reachable from it; modifies a View
        instantiated in another View's `body` that is reached by existing UI
        tests; adds/removes/modifies a presentation modifier on a root-reachable
        View (`.sheet`, `.fullScreenCover`, `.alert`, `.confirmationDialog`,
        `.popover`, `.overlay`) — these insert global UI surface that intercepts
        unrelated tests; or changes `@Environment` / `.environment(...)`
        injection at or near the app root, or launch-environment / initial-state
        gating logic.
      - **Rails / system specs** — the diff modifies the application layout or a
        layout most pages render; changes `ApplicationController` or a
        broadly-applied `before_action` / authentication filter; changes routing
        many specs traverse; or modifies a partial / concern rendered across many
        views. These sit on the path most system/feature specs walk.

   d. When uncertain about a file's blast radius, widen rather than narrow. The
      targeted-selection win on integration tests is bounded (they're already
      expensive per case); the cost of merging a root-surface regression
      masquerading as a leaf change is the entire next baseline, plus the
      diagnostic cost. The asymmetry strongly favours widening.

   Budget: at most 6 tool calls across 5a–5c. Once the 6-call budget is spent,
   treat every file not yet decided as root-reachable for 5c and widen. Stop
   when you can name the affected surface confidently, when further reads
   aren't changing the test set, or when the 6-call budget is spent, whichever
   comes first; widen rather than continue exploring.
6. Pure-docs / comment-only diffs (only `*.md`, `docs/`, code-comment changes) →
   COMMAND: (none).

Output exactly two sections, in this order, with these literal headers:

COMMAND:
<single shell command using the wrapper from the config plus -only-testing flags
for each selected suite, OR one of the fallback commands declared in the config,
OR the literal string `(none)` if zero suites selected>

RATIONALE:
<one or two sentences naming the selected suites and the heuristic that produced
them. Be specific about what you searched and what matched. If you returned
`(none)` or invoked a fallback, name the trigger explicitly so the user can audit.>

Examples of well-formed output:

COMMAND:
./scripts/xcb.sh -only-testing FoodJournalTests/ProposalServiceTests -only-testing FoodJournalTests/IngredientMatcherTests

RATIONALE:
Selected ProposalServiceTests (direct mapping: ProposalService.swift) and
IngredientMatcherTests (symbol-grep hit: NutritionCalculator referenced from
matcher tests). UI tests deferred to the evaluator — no UI test references the
changed views.

—

COMMAND:
bin/rails test

RATIONALE:
`Money` value object renamed `cents` → `amount_cents`; symbol-grep matches 14 test
files across unit suites. Triggered the unit `broad-change-fallback`. System specs
deferred — none reference the renamed attribute and `test/system` broad-change is "none".

—

COMMAND:
./scripts/xcb.sh -only-testing FoodJournalTests/OnboardingStepTests -only-testing FoodJournalUITests

RATIONALE:
Diff adds a `.sheet(...)` to `DailyJournalView` (root-reachable view) and introduces
`OnboardingView`. Step-5 blast-radius rule (5c, SwiftUI branch) fires on the new
presentation modifier on a root view — widened UI selection to the full FoodJournalUITests target
rather than just an onboarding-named suite, because every UI test transits
DailyJournalView. Unit selection stays tight: only OnboardingStepTests references
the new types.

—

COMMAND:
(none)

RATIONALE:
Diff touches only docs/architecture.md. Pure-docs path — no tests selected.
```
