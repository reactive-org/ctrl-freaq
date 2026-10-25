# Retry ladder for the verification gate

The pre-push verification gate (the spine's §8 gate before the review loop starts, and the review loop's
§10.6 gate after each round of review feedback — both ahead of the phase's single S5.2 push) treats "tests must be green before push" as a goal, not
a license to retry indefinitely. Without a cap, a complex integration-test failure can spiral into a
sequence of small fixes — tweak, re-run a slow (10–20 minute) integration suite, tweak, re-run, repeat —
that burns hours of wall-clock time and produces nothing the review loop couldn't have surfaced in the
first place. The cost compounds because each iteration re-pays the suite's fixed setup overhead — a cold
build, a booted simulator, a provisioned server/database, whatever the stack pays once per run — and
small fixes on a shallow read of a failure usually don't help anyway.

The ladder below caps a single visit to the gate at **3 test runs total** before escalation, and forces a
research breakpoint between cheap fixes and any deep fix. It applies identically at both gates.

## The ladder

| Run | Trigger | Allowed action after |
|---|---|---|
| 1 (initial) | First invocation of the gate this visit | If green → proceed. If red → **run the unrelated-failure triage first** ("Triage unrelated failures" below); then 1 cheap fix on any *remaining* diff-transiting failure. |
| 2 | After cheap fix #1 | If green → proceed. If red AND the failing-test set strictly changed (some original failures resolved) → 1 more cheap fix allowed. If red AND the failing-test set is sticky (same set or grew) → **research breakpoint, mandatory**, even though only 2 runs have happened. |
| 3 (deep) | After research-informed fix | If green → proceed. If red → **escalate to user** per "Escalation" below. No further runs this visit. |

A "cheap fix" is a small, narrowly-targeted edit (typo, a missing test identifier, off-by-one, an obvious
binding/wiring mistake) on the immediate failure mode. A "deep fix" is a structural change informed by the
research breakpoint — e.g. restructuring a SwiftUI gesture or lifting view state, reworking a Rails
`before_action` chain or a shared partial, etc.

## Triage unrelated failures: check for a known issue before spending the fix budget

A red gate run does not always mean *your change* broke something. Targeted selection deliberately widens
beyond the diff — the integration blast-radius rules and the broad-change-fallback pull in tests that
don't transit your edit at all — and those tests can be red for reasons that predate this branch: a flaky
interaction, an environmental timing bug, a genuinely separate defect someone has already filed. The
expensive way to find that out is to revert your diff and re-run; it works, but it re-pays the suite's
setup cost to answer a question GitHub may already answer for free. So triage cheaply first, before the
ladder spends a single fix attempt.

**When the triage fires.** On the *first* red run only. Partition the failing tests using the selection
sub-agent's own rationale from this gate:

- **Diff-transiting failures** — selected by a direct filename/symbol match (heuristics 3a–3d): the test
  exercises code your diff touched. A known issue does *not* excuse these — your change could break the
  same path the issue describes in a new way. Hand them straight to the ladder.
- **Seemingly-unrelated failures** — pulled in only by widening (integration blast-radius rule, step 5;
  broad-change-fallback) and referencing no symbol or file in your diff. These, and only these, are the
  triage candidates.

**The check.** For each seemingly-unrelated failing test, search the repo's open issues for the test and
its symptom — one `gh` call, no revert:

```bash
gh issue list --repo <owner/repo> --state open \
  --search "<failing test method> OR <failing suite name> OR <distinctive assertion keyword>" \
  --json number,title,url
```

Read the candidate titles (and the body of any plausible hit) and decide whether one genuinely describes
*this* failure — same test, same symptom — not a keyword collision.

**On a confirmed match.** Treat the failure as a known pre-existing red: exclude it from this gate's
pass/fail, record `Known pre-existing failure: <Suite>/<test> — tracked by #<NNN>` in the state summary,
and add it to the PR body's `## Known failures` section with the issue link. Do **not** file a duplicate
follow-up (the matched issue is the tracker), do **not** spend a cheap fix on it, and do **not** revert to
confirm — the open issue plus the absent diff-relationship is sufficient evidence. If every red test this
run matched a known issue, the gate passes for your diff → proceed. If diff-transiting failures remain,
run the ladder for those only.

**On no match.** The seemingly-unrelated failure has no tracker, so the shortcut doesn't apply — fall back
to the ladder's normal path. The research breakpoint may revert the diff / re-run without your changes to
settle whether the failure is pre-existing (still valid — just no longer the *first* thing tried). If the
revert confirms it is pre-existing and untracked, that's a defer-by-retry follow-up: file it per
"Follow-up issue tracking" (urgency `file-now`, type `bug` or `deferred-test`) so the next run's triage
finds it.

## The adaptive cheap-fix rule

Whether the second cheap fix is allowed depends on whether the first cheap fix made *any* of the
originally-failing tests pass. Compare the failing-test sets across runs:

- **Strictly changed** (e.g., run 1 fails {A, B}; run 2 fails {B, C}): at least one originally-failing
  test newly passed. The first fix worked on something; the new failure is plausibly a
  separately-trivial issue. One more cheap fix is allowed.
- **Sticky** (run 2 fails {A, B} again, or {A, B, C}): no originally-failing test resolved. The model's
  read of the failure is wrong. Force the research breakpoint immediately.

This rule exists because the small-fix spiral's signature is exactly *sticky failures with shrinking
patches*: each iteration tweaks the same code path on the same wrong hypothesis. Detecting non-progress at
run 2 cuts off the spiral before it doubles.

## Test selection on retry

Run 1 and run 3 use the test-selection sub-agent's full verdict; which diff that verdict covers depends
on the gate (see "§10.6 selection scope" below). Run 2 does **not** — it skips the sub-agent and
re-runs only the tests that failed in run 1.

| Run | Selection mechanism |
|---|---|
| 1 | Full sub-agent verdict (per `test-selection-sub-agent.md`) — on the cumulative diff at §8; at §10.6, on the diff since the last gate-verified push, via the diff-base override. |
| 2 | **Skip the sub-agent.** Build the command directly: re-run only the tests that failed in run 1, using the wrapper's targeted-run syntax (e.g. `<wrapper> -only-testing <Suite>/<TestMethod>`, `bin/rails test <path>:<line>`, `pytest <path>::<test>`), joined into a single invocation. Use the failing tests' fully-qualified identifiers as reported by the previous run. |
| 3 | Full sub-agent verdict, same scope rule as run 1. The deep fix may have changed the blast radius (lifted state, restructured a view tree or shared layout, modified a root-reachable surface), so the sub-agent's heuristics — especially the integration blast-radius rules at step 5 of the prompt template — need a fresh look. |

The narrowing at run 2 is a deliberate departure from "trust the sub-agent on every gate visit." The
justification: the sub-agent's job is selecting tests *given a diff*; on a cheap-fix retry the only new
information is which specific tests failed, and the parent already has that. Re-running the sub-agent would
either re-derive the same broad selection (no win) or narrow incorrectly without seeing the failure list
(worse). Bypassing it on run 2 is faster and more correct for the cheap-fix case.

A previously-passing test that the cheap fix breaks will not be caught at run 2 — it will surface at run 3
(where the sub-agent's full verdict runs again on the now-larger diff) or, failing that, in the
evaluator's full canonical run at PR-readiness time. That gap is acceptable: the cheap fix is by definition
small, and the safety net at the evaluator is exactly the reason this skill runs targeted tests rather than
the canonical suite. The alternative — re-running the broad selection on every retry — is what produced the
small-fix spiral this ladder exists to prevent.

## §10.6 selection scope

At §8 nothing on the branch is gate-verified, so the selection diff is the cumulative
`git diff <integration-target>...HEAD`. At §10.6 the branch was verified green at its last commit and only
this iteration's fix edits have changed since — so runs 1 and 3 pass the sub-agent a **diff-base
override**: HEAD at the start of the fix round (`git rev-parse HEAD` before the first fix edit). Commits wait until after the gate, so that HEAD
is the last gate-verified commit and the fix is working-tree-only; the override diff therefore reads the working
tree (`git diff <sha>`, not `<sha>...HEAD`, which would come back empty and select nothing).

This is run 2's narrowing argument one level up, with the same accepted gap and the same safety net: a
fix whose effect surfaces only through earlier branch code it shares no symbol or file with is caught by
the evaluator's canonical run, not by this gate. Three consequences worth stating. A comment-and-
assertion fix round now legitimately selects `(none)` instead of re-running the whole prior selection.
The blast-radius pass still runs, on the fix diff — a fix touching a high-fanout surface still widens,
and "widen when uncertain" still applies within that diff. And a "Push with reds" push doesn't reset
correctness: those failures stay tracked in the PR's `## Known failures`, not silently dropped from
selection.

## Research breakpoint requirements

When the ladder forces a research breakpoint (run 2 was sticky, or run 2 was a second cheap fix that also
failed), the next step is **not** a code change. It's a forced, structured information-gathering pass. The
point is to replace the model's shallow read of the failure — which has now demonstrably failed twice —
with a real understanding before any deep fix is attempted.

During the breakpoint:

1. **Read each failing test's full output** — the assertion message, the stack frame, and the relevant
   runner/diagnostic log lines (simulator logs, server logs, etc.). Not just the one-line summary the test
   runner prints.
2. **For integration tests, capture the stack's richest failure context** — e.g. XCUITest
   `app.debugDescription`, a Capybara `page.html` / screenshot — from inside the failing test and read the
   dump. If the project's CLAUDE.md mandates such a capture (the Swift convention mandates
   `app.debugDescription` on element-lookup timeouts), restate and follow that mandate here. The captured
   surface usually points at the cause directly — for a UI test: a collapsed parent, hidden element,
   dropped identifier, overlay absorbing children; for a request/system spec: a redirect chain, a missing
   record, an authorization filter rejecting the request.
3. **Spawn one `Explore` sub-agent** with: the failing test files, the source they exercise, the recent
   diff (`git diff <integration-target>...HEAD`). Have it return three things, in order:
   - What is *actually* failing (not what the test name suggests, not what the assertion line says — what's
     happening in the code paths the test transits)
   - What code paths the failing tests transit, including any indirection (e.g. SwiftUI gesture → focus →
     state → re-render, or Rails request → `before_action` → service → view render)
   - What structural change is implied by (a) and (b) — explicitly *not* a tweak

No code edits during the breakpoint. The deep fix happens only after the sub-agent returns and the model
has internalised its findings.

## Escalation

When run 3 (the research-informed deep fix) is also red, stop. Do not run the gate a fourth time. Surface
the failure analysis, then ask the user to choose among three equally-weighted paths forward — no default —
via `AskUserQuestion` (`header: "Tests red"`, options **Push with reds** / **Defer the tests** /
**Restructure**, each described by the matching path below):

1. **Push with documented reds.** Open the PR (or push to the existing branch) with a `## Known failures`
   section in the PR body listing each red test, the reproduction signal, and what was tried. Let `code-review`
   decide whether any of the reds are blocking. Best when the failures look like CI/timing flakiness or
   genuinely separate edge cases that don't block the headline change.
2. **Defer the failing tests with linked issues.** File one follow-up issue per failure (or one umbrella
   issue if the failures share a root cause), spawned in one message, via the sub-agent protocol in
   "Follow-up issue tracking" —
   urgency `file-now`, type `deferred-test`. The filed URLs become `// TODO(#NNN)` markers and skip
   annotations (`XCTSkip("Deferred to #NNN — <reason>")`, Minitest/RSpec `skip "..."`) before push. Push
   the rest green. Best when the failure is a real structural problem that needs more design than fits this
   PR.
3. **Restructure.** Abandon the current approach. Return to a planning conversation, with the
   research-breakpoint findings as input, and propose a different shape. Best when the deep fix revealed
   that the original approach itself was wrong (e.g., a SwiftUI gesture that fundamentally fights its
   parent's gesture system, or a Rails callback chain that can't express the transaction boundary the
   feature needs).

The summary at the handoff records which path was taken and why. The user picks; the skill does not pick a
default. You are the main loop at both gates — ask via `AskUserQuestion` directly. At the §10.6 gate the
card is `review-fix-round.md`'s **Verification failure** guard rail (`header: "Tests red"`), with these
same three options.

## What the ladder is and isn't

It **is** a cap on retries within a single visit to the gate. Each entry to the §8 gate starts a fresh
ladder (run 1 again). Each entry to the §10.6 gate (one per review-loop iteration) starts a fresh ladder.
The review loop's progress rule and emergency ceiling govern how many times `code-review` can flag changes; this ladder governs
how many times the model may re-run tests within one of those iterations.

It **isn't** a license to give up after one failure. Run 1 failing is normal — that's why the gate exists.
The ladder activates when the model is about to enter a small-fix spiral, not on every red run.

It also doesn't count defect-injection runs. An injection run (inject → red → revert, per
`review-fix-round.md`'s injection step) verifies the *test*, not the diff — it is *supposed* to go red. Only runs
asserting the diff is green count toward the 3-run cap; without this exemption a routine injection cycle
would spuriously trip the escalation.
