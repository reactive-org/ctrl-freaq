#!/usr/bin/env python3
"""prep_resolver.py — the resolver's complete facts block in one call (architecture.md §4;
docs/implementation.md S9; docs/specs/resolver.md). v1's ~130 lines of prompt-side state
assembly — state-vector derivation, epic-branch discovery, branch naming/collision suffixing,
phase/DoD/open-question parsing, and workspace setup — collapse into ONE JSON envelope on stdout,
so the resolver session's startup is one Python process, never a subprocess chain.

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external
processes any script may spawn are git/gh")::

    gh_gather.run(..., stream=)                    -- the target issue's body/thread/plan-marker/
                                                       native deps envelope (one round-trip)
    gh_gather.run(..., stream=)                    -- per open-question tracker issue, for the
                                                       Tier 1 status read (state + decision marker)
    workspace._build_attach / _build_ensure_read   -- assert the AMBIENT checkout is the expected
                                                       work worktree (v3: the operator opened it
                                                       via workspace-open and started this session
                                                       inside it; prep observes + asserts, never
                                                       creates) and, for Epic/Story, the audit
                                                       read workspace
    config_block.read_block_anywhere               -- the three resolver-side gate-config blocks
                                                       (+ the pr-evaluator fallback chain), read
                                                       from the asserted work workspace's WORKING
                                                       TREE — the operator's checkout is the
                                                       trusted source (architecture.md §6)
    review_guidance.read                           -- the repo's owner-written
                                                       `<!-- issue-resolver-review-guidance -->`
                                                       block, at the same working tree, its
                                                       reviewer flags neutralised, for the S5.1
                                                       reviewer `<context>`
    doc_catalogue.read_catalogue                   -- the consuming repo's `<!-- doc-catalogue -->`
                                                       grounding docs, read at the audit view
                                                       (read workspace, else work workspace) and
                                                       emitted with that vantage as
                                                       `grounding_path`
    parse.parse_phases / parse.parse_dod_bullets / parse.parse_oq_links
                                                    -- the plan's ## Phases, the issue's
                                                       ## Definition of done, and its
                                                       ## Open questions section
    parse.dod_summary / parse.dod_coverage         -- `facts.dod_coverage`: the plan's
                                                       `closes-dod` claims against the DoD's
                                                       shape, `drift` the projection rule's
                                                       bullet-count drift as a fact
    branching.delivered_paths                      -- epic route: the files the PRs merged into
                                                       the epic branch changed, staged as
                                                       `facts.epic.delivered` (a path) for the
                                                       integration pass's `blast_radius.py
                                                       assess --delivered` (#149); a failed
                                                       listing is the EPIC_DELIVERED_UNAVAILABLE
                                                       notice, never a decision

Every executor composed here exposes a **pure, non-emitting core** — ``build_*(...) -> (payload,
notices, decision|None)`` (docs/specs/baseline.md §5, the S8 pattern lock). This prep calls those
cores **directly** and forwards each core's returned ``decision`` verbatim
(:func:`_forward_decision`), emitting exactly one envelope of its own. No ``redirect_stdout`` /
``io.StringIO`` capture of another script's stdout is used anywhere in this module — that bridge
was S6-pilot-only and is retired for every prep from S9 on (docs/specs/baseline.md §5's "Rule for
S9+").

`git ls-remote --heads origin <pattern>` (epic-branch discovery, story parent-epic branch lookup,
and branch-collision suffixing), `gh issue list --label epic ...` (story parent-epic search), and
`gh pr list --state closed ...` (the closed-PR half of the prior-PR state table) have no existing
executor core — architecture.md §1 permits any script to spawn `git`/`gh` directly via
`pipelib.process.run`, so this module calls them directly (:func:`_list_remote_branches`,
:func:`_search_parent_epic`, :func:`_search_closed_prs`), the same way `prep_evaluator.py` calls
`gh api repos/<owner>/<repo>` directly for repo-merge-config (no existing executor covers it
either).

Usage::

    prep_resolver.py <issue-number> <owner/repo> [--root PATH] [--scratch-dir PATH] [--refresh]

``--root`` defaults to ``.`` and is normalized to the MAIN checkout via
``workspace._resolve_main_root`` — under v3 the session (and therefore the ambient cwd) sits
INSIDE the work worktree, and every derived path (the audit ``ro-*`` view, scratch staging) must
land relative to the main checkout regardless. ``--scratch-dir`` defaults to
``/tmp/gh-resolver-<issue-number>`` (CLAUDE.md's ``/tmp/gh-<skill>-<N>/`` convention) when
omitted. ``--refresh`` re-derives the volatile facts (issue/PR state, branch discovery) without
re-asserting the workspace, re-running the setup hooks, or re-reading the three gate-config
blocks — mirroring ``prep_evaluator.py``'s ``--refresh`` contract (architecture.md §4: "prep
supports --refresh and is re-run at the points where currency matters").

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"``
or ``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified
hard `gh`/`git` failure surfaced by a composed executor — stderr carries the faithful error.

``vector.mode`` is a THREE-value closed set — ``"continue"`` / ``"gated"`` / ``"fresh"`` — not the
two-value fresh/continue an earlier revision of this module used. ``"gated"`` is a deliberate
distinct value (never an overload of ``"fresh"``): docs/specs/resolver.md's prior-PR state table
("Fresh/continue mode from the prior-PR state table") makes an open PR by ANOTHER author (active
or stale) — and a DRAFT PR by another author, which that table's Draft row scopes to "the same
author," not any draft — operator-gated
via `AskUserQuestion` with NO work in the checkout until the operator decides. This prep therefore
reports ``mode: "gated"`` plus a ``vector.gate`` fact block (``reason``, the exact
`AskUserQuestion` ``header``/``options`` S10 renders verbatim, and the driving ``prior_pr`` fact)
and does **not** assert the ambient workspace or derive an expected branch for that row — S10's
router must render the gate on ``mode == "gated"`` and must never fall through to the
fresh-branch or continue-on-existing flow. Prep still emits ``status: "ok"`` on a gated row (this
is a flow gate, not an assembly failure — identical reasoning to the ``open_questions_gate`` hard
gate below). A **read** workspace (the audit ref) may still be assembled on a gated row since it
never touches the other author's branch; only the WORK-workspace assertion is withheld.

``audit_ref`` is always a BARE branch name (``"main"`` / ``"epic/42-journal"``), never
`origin/`-prefixed — see :func:`_audit_ref`'s docstring. The origin-prefixed form (what a read
workspace actually checked out) rides on ``read_workspaces.audit.ref`` instead once that workspace
is ensured; a consumer must not assume one prefixing convention for both fields.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import doc_catalogue  # noqa: E402  (the consuming repo's declared grounding docs)
import gh_gather  # noqa: E402
import gh_pr_gather  # noqa: E402  (continue mode only: the prior PR's body, for the tracker diff)
import oq_tracker  # noqa: E402  (the open-question gate: tiered tracker status joined to the body's entries)
import branching  # noqa: E402  (shared branch/type/prior-PR cores; aliased below)
import parse  # noqa: E402
import plan_shipped  # noqa: E402  (the shipped-phase records a relocated plan's detail lives in)
import review_guidance  # noqa: E402  (the repo's owner-written guidance for the S5.1 reviewer)
import review_threads  # noqa: E402  (the PR's review threads, the revision run's input, #145)
import workspace  # noqa: E402
from pipelib import process, trust  # noqa: E402
from pipelib.decisions import (  # noqa: E402
    AMBIGUOUS,
    DOC_CATALOGUE_ABSENT,
    PLAN_MISSING,
    needs_decision,
)
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_needs_decision, emit_ok  # noqa: E402
from pipelib.spill import read_section  # noqa: E402
from pipelib.thread import load_thread  # noqa: E402

# The implementation-plan marker (skills/planner/references/plan-schema.md;
# docs/specs/resolver.md "Artifacts read") — the plan comment is always the first marker_prefix
# lookup GATHER_ISSUE performs, exactly as v1's step 2 documents.
PLAN_MARKER = "<!-- implementation-plan:v1 -->"

# The question-decision marker (skills/_shared/open-question-links.md "Status is the tracker's",
# Tier 1) — read per open-question tracker issue to detect a recorded operator decision.
QUESTION_DECISION_MARKER = oq_tracker.QUESTION_DECISION_MARKER

# The three resolver-side gate-config marker names (docs/specs/resolver.md §P3.1), read at the
# root `main` SHA (architecture.md §6/§12: "gate config is pinned to trust ... never from a PR
# head"). Each has a pr-evaluator-side fallback per the spec's documented fallback chain.
_FAST_CHECKS_MARKER = "issue-resolver-fast-checks"
_TEST_TARGET_MARKER = "issue-resolver-test-target"
_CANONICAL_SUITE_MARKER = "issue-resolver-canonical-suite"
_FALLBACK_STATIC_CHECKS_MARKER = "pr-evaluator-static-checks"
_FALLBACK_HEALTH_CHECKS_MARKER = "pr-evaluator-health-checks"
_FALLBACK_TEST_TARGET_MARKER = "pr-evaluator-test-target"

# Candidate config files, in priority order — same discovery list prep_evaluator.py /
# workspace.py's `_candidate_hook_files` already use (COMMANDS.md, CLAUDE.md, then one level of
# `@`-include from either). `config_block.read_block_anywhere` (S12 promotion) defaults to this
# exact tuple, so this module no longer carries its own copy of the discovery loop — see
# "Gate-config discovery" below.

# ---------------------------------------------------------------------------
# Branch naming, type detection, epic discovery, prior-PR classification — extracted to
# `branching.py` at the v3 workspace-model inversion so `prep_workspace_open.py` (which owns
# branch creation) and this prep (which asserts the ambient branch) share ONE implementation.
# Module-level aliases keep this prep's public surface — and the direct-call tests against it —
# unchanged (the `build_oq_query = oq_tracker.build_oq_query` precedent in prep_planner.py).
# ---------------------------------------------------------------------------

_EPIC_TITLE_PREFIX_RE = branching.EPIC_TITLE_PREFIX_RE
_EPIC_BRANCH_LS_REMOTE_PATTERN = branching.EPIC_BRANCH_LS_REMOTE_PATTERN
_EPIC_BRANCH_NAME_RE = branching.EPIC_BRANCH_NAME_RE
_BRANCH_VERSION_SUFFIX_RE = branching.BRANCH_VERSION_SUFFIX_RE

_list_remote_branches = branching.list_remote_branches
_detect_type = branching.detect_type

PRIOR_PR_ROW_OPEN_YOURS = branching.PRIOR_PR_ROW_OPEN_YOURS
PRIOR_PR_ROW_OPEN_OTHER_ACTIVE = branching.PRIOR_PR_ROW_OPEN_OTHER_ACTIVE
PRIOR_PR_ROW_OPEN_OTHER_STALE = branching.PRIOR_PR_ROW_OPEN_OTHER_STALE
PRIOR_PR_ROW_DRAFT = branching.PRIOR_PR_ROW_DRAFT
PRIOR_PR_ROW_CLOSED_RESOLVED = branching.PRIOR_PR_ROW_CLOSED_RESOLVED
PRIOR_PR_ROW_CLOSED_NOT_RESOLVED = branching.PRIOR_PR_ROW_CLOSED_NOT_RESOLVED
PRIOR_PR_ROW_NONE = branching.PRIOR_PR_ROW_NONE

MODE_CONTINUE = branching.MODE_CONTINUE
MODE_GATED = branching.MODE_GATED
MODE_FRESH = branching.MODE_FRESH

_CONTINUE_ROWS = branching.CONTINUE_ROWS
_GATED_ROWS = branching.GATED_ROWS
_GATE_CARDS = branching.GATE_CARDS
_STALE_ACTIVITY_DAYS = branching.STALE_ACTIVITY_DAYS

_search_closed_prs = branching.search_closed_prs
_classify_prior_pr_row = branching.classify_prior_pr_row
_classify_open_other_activity = branching.classify_open_other_activity
compute_fresh_slug = branching.compute_fresh_slug
_discover_epic_branch = branching.discover_epic_branch
_search_parent_epic = branching.search_parent_epic
_resolve_parent_epic_branch = branching.resolve_parent_epic_branch
_find_open_pr_for_head = branching.find_open_pr_for_head
_count_commits_ahead = branching.count_commits_ahead
_branch_belongs_to_issue = branching.branch_belongs_to_issue
_prior_prs_for_issue = branching.prior_prs_for_issue
_partition_prs_for_issue = branching.partition_prs_for_issue
compute_branch_name = branching.compute_branch_name


# ---------------------------------------------------------------------------
# Gate-config discovery in the ambient workspace's WORKING TREE (v3.x: the origin/main pin is
# retired — the operator's checkout supplies gate config, uncommitted edits included; mirrors
# prep_evaluator's _read_gate_config shape, resolver-side marker names + fallback chain per
# docs/specs/resolver.md §P3.1).
# ---------------------------------------------------------------------------


def _read_gate_config(gate_root):
    """Read the resolver's three gate-config blocks from ``gate_root``'s working tree (the asserted
    work workspace, or the invoking checkout when no workspace was asserted), applying the
    spec's fallback chain (docs/specs/resolver.md §P3.1): `issue-resolver-fast-checks` -> (no
    fallback; static checks only); `issue-resolver-test-target` -> `pr-evaluator-test-target`;
    `issue-resolver-canonical-suite` -> `pr-evaluator-test-target`'s full-suite-command
    (approximated here as its raw text, since prep surfaces facts, not judgment about which
    sub-line is the full-suite command — the resolver playbook parses the fallback block's prose
    itself, matching v1's own "read it as natural language; don't try to parse it" instruction
    for `issue-resolver-test-target`). Absent both `issue-resolver-fast-checks` and
    `pr-evaluator-static-checks`, falls back once more to the legacy `pr-evaluator-health-checks`
    block (docs/specs/resolver.md "Fallback config blocks"'s documented worst-case chain). It also
    reads the repo's `issue-resolver-review-guidance` block (#99) at the same root — not a gate,
    the owner's prose for the S5.1 reviewer `<context>` — as ``review_guidance``, `review_guidance.read`'s
    dict verbatim.
    Returns `(config_dict_without_source, notices)`; each `*_source` is the absolute path of the
    file the block was read from (until v3.x these were `<sha7>:<rel_path>` at-ref strings —
    there is no ref to name any more, and the file path is what a reader can go open).
    """
    notices = []

    fast_present, fast_lines, fast_source = config_block.read_block_anywhere(gate_root, _FAST_CHECKS_MARKER)
    if not fast_present:
        fast_present, fast_lines, fast_source = config_block.read_block_anywhere(gate_root, _FALLBACK_STATIC_CHECKS_MARKER)
        if fast_present:
            notices.append("FAST_CHECKS_FALLBACK_STATIC_CHECKS")
    if not fast_present:
        fast_present, fast_lines, fast_source = config_block.read_block_anywhere(gate_root, _FALLBACK_HEALTH_CHECKS_MARKER)
        if fast_present:
            notices.append("FAST_CHECKS_FALLBACK_LEGACY_HEALTH_CHECKS")

    test_target_present, test_target_lines, test_target_source = config_block.read_block_anywhere(gate_root, _TEST_TARGET_MARKER)
    if not test_target_present:
        test_target_present, test_target_lines, test_target_source = config_block.read_block_anywhere(gate_root, _FALLBACK_TEST_TARGET_MARKER)
        if test_target_present:
            notices.append("TEST_TARGET_FALLBACK_PR_EVALUATOR")

    canonical_present, canonical_lines, canonical_source = config_block.read_block_anywhere(gate_root, _CANONICAL_SUITE_MARKER)
    if not canonical_present:
        # Fallback chain per docs/specs/resolver.md "Fallback config blocks":
        # pr-evaluator-test-target's full-suite-command line —
        # prep surfaces the raw fallback block for the playbook to extract the labelled line from
        # (this module does not parse `full-suite-command:` out of free-form prose; see the
        # function docstring).
        canonical_present, canonical_lines, canonical_source = config_block.read_block_anywhere(gate_root, _FALLBACK_TEST_TARGET_MARKER)
        if canonical_present:
            notices.append("CANONICAL_SUITE_FALLBACK_PR_EVALUATOR_TEST_TARGET")

    config = {
        "static_checks": _parse_backtick_command_list(fast_lines) if fast_present else [],
        "static_checks_present": fast_present,
        "static_checks_source": fast_source,
        "test_target_present": test_target_present,
        "test_target_raw": "\n".join(test_target_lines) if test_target_present else None,
        "test_target_source": test_target_source,
        "canonical_suite_present": canonical_present,
        "canonical_suite_raw": "\n".join(canonical_lines) if canonical_present else None,
        "canonical_suite_source": canonical_source,
        "review_guidance": review_guidance.read(gate_root),
    }
    return config, notices


_COMMAND_LIST_ITEM_RE = re.compile(r"^\s*-\s*`([^`]*)`")


def _parse_backtick_command_list(interior_lines):
    items = []
    for line in interior_lines:
        match = _COMMAND_LIST_ITEM_RE.match(line)
        if match:
            items.append(match.group(1))
    return items


# ---------------------------------------------------------------------------
# Open-question facts: parse.parse_oq_links joined with tracker state + native blocked_by.
# ---------------------------------------------------------------------------


class _DiscardStream:
    """A minimal write-sink for `gh_gather.run(stream=...)` — the one remaining executor prep
    composes that emits-through-a-`stream=` while ALSO returning `(exit, envelope)`. Identical in
    shape to `prep_evaluator._DiscardStream`; restated locally rather than imported across module
    boundaries for a two-method sink (architecture.md §2's composition guidance is about calling
    another script's *pure core* directly, not about sharing tiny private test-doubles)."""

    def write(self, _data):
        return None

    def flush(self):
        return None


# The open-question gate lives in oq_tracker.py (promoted when the coordinator became its second
# consumer); the private names stay as aliases so this module's call sites read unchanged.
_oq_tracker_status = oq_tracker.tracker_status
_build_open_question_facts = oq_tracker.build_open_question_facts


# ---------------------------------------------------------------------------
# Audit-ref derivation (docs/specs/resolver.md §4.5's 4-row table) + suggested_playbook
# ---------------------------------------------------------------------------


def _audit_ref(epic_branch_name, root_branch):
    """docs/specs/resolver.md §4.5's audit-ref table, reduced to its one real question: is there an
    epic branch in play? `epic_branch_name` is the discovered/bootstrap branch for an Epic-as-target
    run and the parent's integration branch for any target under an open epic; it is `None` for "no
    epic context" — in which case the ref is the derived default branch, matching the bootstrap-path
    rule that the zero-match Epic-as-target audit still runs against it (the bootstrap branch would
    fork from it anyway).

    The parameter used to be `(issue_type, epic_branch_name, root_branch)` with a
    `issue_type in ("standard",)` short-circuit in front. That short-circuit was the third of the
    three lexical-type gates #31 retired: it discarded a parent epic branch that the caller had
    already discovered, purely because the target lacked a `story` label. The branch fact answers
    the question on its own — a target with no epic context has no branch to pass. `root_branch` is
    the derived default branch (`workspace.default_branch`), never a hardcoded `main`.

    Returns a BARE branch name (the default branch / `"epic/42-journal"`), never an `origin/`-prefixed ref
    — this is the facts-block `audit_ref` value verbatim. `workspace._build_ensure_read` is what
    prepends `origin/` internally (its own `git fetch origin <ref>` / `git worktree add --detach
    ... origin/<ref>` calls); the resulting **origin-prefixed** ref rides separately on
    `read_workspaces.audit.ref` once a read workspace is ensured (§6 below), since that workspace
    really did check out `origin/<ref>`, not the bare name. **S10's router/playbooks must read
    `audit_ref` as bare and never hand it to `git`/the state-distiller with an assumed `origin/`
    prefix themselves** (v1 passed an `origin/`-prefixed `audit_ref`/
    `integration_target_ref` to the sub-agents directly — v2 does not carry that prefixing
    convention on THIS field; consumers needing the prefixed form read
    `read_workspaces.audit.ref` instead).
    """
    return epic_branch_name if epic_branch_name is not None else root_branch


def _suggested_playbook(issue_type, comment_only, epic_branch_name=None):
    """Map `(type, comment_only)` to the suggested playbook filename (architecture.md §5: "Prep
    proposes; the router confirms"). `comment_only` per the spec's routing signal: a `blocked`
    hard-gate state, or (should a future fact source detect it) any other no-code-work
    classification — named `comment-only.md` per this step's Work list.

    `epic_branch_name` adds one arm and subtracts none (#31): an untyped target that IS under an
    open epic routes to `story.md` too. That is a correctness requirement, not a tidiness one —
    once the PR bases on `epic/<N>-<slug>`, `standard.md` is the wrong instruction set. It states
    the base is the default branch, renders `base main` in its handoff, and omits the
    integration-branch caveat sentence the PR body owes a reviewer who would otherwise see a PR
    into an epic branch with no explanation. Type still decides everything it legitimately
    decides; the branch fact decides which base the flow is written for.
    """
    if comment_only:
        return "comment-only.md"
    if issue_type == "epic":
        return "epic.md"
    if issue_type == "story" or epic_branch_name:
        return "story.md"
    return "standard.md"


# ---------------------------------------------------------------------------
# attention
# ---------------------------------------------------------------------------


def _catalogue_attention(grounding_docs, catalogue_absent):
    """The doc-catalogue attention lines, prep_planner's three-state shape: `grounding_docs` is `None`
    when the catalogue was never read (`--refresh`, comment-only and gated rows), `[]` when it was read
    and declares nothing, a list otherwise — "we didn't look" must not render as "the repo declared
    nothing". The resolver proceeds ungrounded on an absent catalogue (skills/_shared/doc-catalogue.md),
    so these are attention, never a decision."""
    if grounding_docs is None:
        return []
    attention = []
    if catalogue_absent:
        attention.append(
            "no doc catalogue at the audit ref — the fitness audit and doc grounding run ungrounded; "
            "run /ctrl-freaq:setup to declare this repo's grounding docs in docs/README.md"
        )
    elif not grounding_docs:
        attention.append(
            "doc catalogue declares no documents — the fitness audit and doc grounding run "
            "ungrounded until it names some"
        )
    for missing in doc_catalogue.missing_entry_paths(grounding_docs):
        attention.append(
            "doc catalogue names '%s', absent at the audit ref — a stale entry, or a doc this "
            "branch has not merged yet" % missing
        )
    return attention


def _dod_drift_attention(dod_coverage):
    """One line when `facts.dod_coverage.drift` — projection is blocked this run and the handoff
    re-routes to the planner (`dod-projection-rule.md`, "Bullet count drift")."""
    if not dod_coverage or not dod_coverage["drift"]:
        return []
    note = parse.misplaced_dod_note(dod_coverage)
    if note is not None:
        found = note
    elif not dod_coverage["present"]:
        found = "the issue body has no `## Definition of done`"
    else:
        found = "the issue's `## Definition of done` has %d bullet(s)" % dod_coverage["dod_count"]
    if dod_coverage["max_claimed"]:
        claimed = "claims `closes-dod` up to %d" % dod_coverage["max_claimed"]
    else:
        claimed = "claims no `closes-dod` index"
    return [
        "DoD drift: the plan %s but %s — projection is blocked; re-route to the planner, never "
        "project onto another heading" % (claimed, found)
    ]


def _build_attention(work_workspace_envelope, prior_pr_row, epic_facts, story_epic_matches):
    attention = []
    if work_workspace_envelope is not None:
        setup = work_workspace_envelope.get("setup") or {}
        if setup.get("succeeded") is False:
            first_failure = setup.get("first_failure") or {}
            attention.append(
                "work worktree setup hook failed at step %s (`%s`) — worktree exists but is not "
                "ready" % (first_failure.get("step"), first_failure.get("command"))
            )
        if work_workspace_envelope.get("dirty"):
            attention.append("work worktree has uncommitted changes")
        unpushed = work_workspace_envelope.get("unpushed_commits") or 0
        if unpushed:
            attention.append("work worktree has %d unpushed commit(s)" % unpushed)
    if prior_pr_row == PRIOR_PR_ROW_OPEN_OTHER_ACTIVE:
        attention.append("an open PR by another author already references this issue and is actively worked")
    if prior_pr_row == PRIOR_PR_ROW_OPEN_OTHER_STALE:
        attention.append("an open PR by another author references this issue but looks stale")
    if prior_pr_row == PRIOR_PR_ROW_CLOSED_NOT_RESOLVED:
        attention.append("a closed/merged PR references this issue but did not resolve it")
    if epic_facts and epic_facts.get("match_count", 0) == 0 and "bootstrap_slug" in epic_facts:
        attention.append("no epic integration branch exists yet on origin — bootstrap required")
    # Epic-only (a story's `facts.story` shape carries neither key).
    if epic_facts and epic_facts.get("match_count") == 1:
        integration_pr = epic_facts.get("integration_pr")
        if integration_pr:
            attention.append(
                "epic integration PR #%s is already open%s — this run refreshes its body, it "
                "never opens a second one"
                % (integration_pr.get("number"), " as a draft" if integration_pr.get("is_draft") else "")
            )
            if not integration_pr.get("is_yours"):
                attention.append(
                    "the open epic integration PR #%s is authored by %s"
                    % (integration_pr.get("number"), integration_pr.get("author"))
                )
        elif epic_facts.get("commits_ahead") == 0:
            attention.append(
                "epic branch has no commits ahead of %s — the integration PR cannot be opened yet"
                % epic_facts.get("commits_ahead_base")
            )
    return attention


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# `## Phase tracker` reconciliation facts (continue mode only)
# ---------------------------------------------------------------------------
#
# The tracker and the plan's `## Phases` are two artifacts keyed by the same integer phase numbers,
# written by different sessions. The tracker is authoritative for **tick state** (what shipped); the
# plan is authoritative for the **row set** (what exists). A planner revise may insert a phase and
# renumber the unshipped tail (`skills/planner/references/revise-reconciliation.md`, "Inserting a
# phase after work has shipped"), which changes the row set — and the resolver's continue cursor
# selects by row number, so an unreconciled tracker silently points at different work and can read
# as complete while a plan phase has no row at all.
#
# This is a fact, not a judgment: the diff is fully determined by the two artifacts, so it is
# computed here and the playbook only *acts* on it.


def build_tracker_diff(rows, phases, unparsed=None):
    """Classify a `## Phase tracker` row set against the plan's parsed `## Phases`.

    Returns the `diff` dict. Titles are compared whitespace-collapsed and case-folded
    (`parse.normalize_tracker_title`), so a re-wrap or a capitalization change is not drift.

    `shifted` is tested BEFORE `removed_shipped`: a ticked row whose phase number is gone from the
    plan but whose title matches some other plan phase is work that MOVED, which is the more specific
    (and more alarming) finding — reporting it as "removed" would lose the destination number the
    gate needs to quote.

    A ticked row whose plan phase was merely *retitled* is NOT a conflict. `revise-reconciliation.md`
    makes a shipped phase's `ships`/`deliverable`/`kind` change HARD at the planner, and `title` is
    deliberately not on that list — so a cosmetic retitle is legitimately SOFT, and a title-only
    conflict would fire on every one of them. A tracker row carries no `ships`, so the resolver
    structurally cannot see the field that HARD list keys on; it must not invent a stricter rule.
    """
    by_number = {}
    for phase in phases or []:
        number = phase.get("number")
        if number is not None:
            by_number[number] = phase
    # title -> every phase number carrying it. A LIST, not first-wins: two phases can share a
    # normalized title ("integration tests"), and naming one of them as a `shifted` destination on
    # nothing better than list order would quote a wrong number at the operator gate. An ambiguous
    # title yields no destination, so the row falls through to the conservative case below.
    title_to_numbers = {}
    for number, phase in by_number.items():
        title_to_numbers.setdefault(
            parse.normalize_tracker_title(phase.get("title")), []
        ).append(number)

    def _sole_destination(title_key, excluding=None):
        candidates = [n for n in title_to_numbers.get(title_key, []) if n != excluding]
        return candidates[0] if len(candidates) == 1 else None

    # Only MAIN rows are reconciled against the plan. A `Phase <N>-<label>` sub-row is bound to phase
    # `<N>` and has no plan counterpart to match (the plan's labels are integers — `plan-schema.md`,
    # "Phase numbering"), so it is carried through untouched. Folding sub-rows in was #48's defect:
    # `dod-projection-rule.md` Example C's own `Phase 2-measurement` row then read as a second row OF
    # phase 2, and every operator-phase PR raised the drift gate with nothing drifting.
    main_rows = [row for row in rows if not row.get("sub_label")]
    sub_rows = [row for row in rows if row.get("sub_label")]

    row_numbers = {row["phase"] for row in main_rows}
    missing = sorted(number for number in by_number if number not in row_numbers)
    # Two MAIN rows for one phase: a tick state nothing can reconcile, and the case the gate is for.
    duplicated = sorted(
        number
        for number in row_numbers
        if sum(1 for row in main_rows if row["phase"] == number) > 1
    )

    dropped, retitled, shifted, removed_shipped = [], [], [], []
    for row in main_rows:
        number = row["phase"]
        plan_phase = by_number.get(number)
        row_title_key = parse.normalize_tracker_title(row["title"])
        if plan_phase is not None:
            plan_title = plan_phase.get("title") or ""
            if row["checked"] and row_title_key != parse.normalize_tracker_title(plan_title):
                elsewhere = _sole_destination(row_title_key, excluding=number)
                if elsewhere is not None:
                    shifted.append(
                        {
                            "row_phase": number,
                            "plan_phase": elsewhere,
                            "title": row["title"],
                            "commit_sha": row["commit_sha"],
                        }
                    )
                else:
                    retitled.append(
                        {
                            "phase": number,
                            "row_title": row["title"],
                            "plan_title": plan_title,
                            "commit_sha": row["commit_sha"],
                        }
                    )
            continue
        if not row["checked"]:
            dropped.append({"phase": number, "title": row["title"]})
            continue
        elsewhere = _sole_destination(row_title_key)
        if elsewhere is not None:
            shifted.append(
                {
                    "row_phase": number,
                    "plan_phase": elsewhere,
                    "title": row["title"],
                    "commit_sha": row["commit_sha"],
                }
            )
        else:
            removed_shipped.append(
                {"phase": number, "title": row["title"], "commit_sha": row["commit_sha"]}
            )

    return {
        "missing": missing,
        "dropped": dropped,
        "retitled": retitled,
        "shifted": shifted,
        "removed_shipped": removed_shipped,
        "unparsed": list(unparsed or []),
        "duplicated": duplicated,
        # Informational, never a conflict: the rows a rebuild must carry through verbatim. Reported so
        # the resolver knows they exist — rewriting the tracker without them would drop an operator
        # phase's `(operator phase <N>, applied <ISO-date>)`, the only record that it landed.
        "sub_rows": sub_rows,
        # One unmissable top-level boolean the playbook gates on, mirroring `open_questions_gate`'s
        # `blocked`: the router must never have to derive a gate from a disjunction of lists.
        #
        # `unparsed` and `duplicated` conflict for a different reason than the other two: those two
        # say shipped work moved or vanished, these say the tick state is UNKNOWABLE. Rebuilding
        # under either would mean guessing what shipped, which is the one thing this fact exists to
        # stop — so they gate rather than rebuild.
        "conflict": bool(shifted or removed_shipped or unparsed or duplicated),
    }


def _absent_tracker():
    """The tracker fact when there is nothing to reconcile (fresh mode, or a continue-mode PR with no
    `## Phase tracker` section).

    A FACTORY, not a module-level constant copied with `dict(...)`: that copy is shallow, so every
    caller would share one nested `diff` dict and one `rows` list with the constant itself, and the
    first in-process mutation would corrupt every later call. Preps are composed in-process by
    design (`build_facts` is the testable core other preps' docstrings point at), so that aliasing
    is reachable rather than theoretical.
    """
    return {
        "present": False,
        "rows": [],
        "last_shipped": None,
        "diff": {
            "missing": [],
            "dropped": [],
            "retitled": [],
            "shifted": [],
            "removed_shipped": [],
            "unparsed": [],
            "duplicated": [],
            "sub_rows": [],
            "conflict": False,
        },
    }


def _load_json_list(pr_facts, key):
    """A JSON-list section of the prior PR's gather (`reviews`, `thread`), from its spilled-or-inline
    text (a list already in the legacy inline mode). Returns `None` on anything unreadable — an absent
    section included; a readable empty list is `[]`."""
    try:
        raw = read_section(pr_facts, key)
        if isinstance(raw, str):
            raw = json.loads(raw) if raw else None
    except (OSError, ValueError, KeyError):
        return None
    return raw if isinstance(raw, list) else None


def _load_reviews(pr_facts):
    """The prior PR's review list (`[]` on anything unreadable) — the only consumer is a convenience
    reason in `facts.revision`."""
    return _load_json_list(pr_facts, "reviews") or []


def review_at_head(reviews, head_oid):
    """Whether a non-approving review (`COMMENTED` / `CHANGES_REQUESTED`) was posted on the PR's
    current head commit — i.e. nothing was pushed after it. The evaluator's soft-reject is a
    `COMMENTED` review. Reads `reviews[]`, never `latestReviews`: GitHub leaves `COMMENTED` reviews
    out of `latestReviews`, so the soft-reject is invisible there (observed on the sandbox)."""
    if not head_oid:
        return False
    for review in reviews or []:
        state = (review.get("state") or "").upper()
        oid = ((review.get("commit") or {}).get("oid")) or ""
        if state in ("COMMENTED", "CHANGES_REQUESTED") and oid == head_oid:
            return True
    return False


def _absent_pr_extras():
    """What `_build_tracker` reports beside the tracker when there is no PR fetch to read. A factory,
    for `_absent_tracker`'s reason."""
    return {
        "polish": parse.absent_polish(),
        "security": parse.absent_security(),
        "out_of_plan": parse.absent_out_of_plan(),
        "review_at_head": False,
        "resume": _absent_resume(),
    }


def _absent_resume():
    """`facts.resume` when no continued PR's comments were read (fresh mode, no prior PR). A factory."""
    return {"present": False, "comments_path": None, "trusted": 0, "skipped": 0}


def stage_resume_comments(comments, path):
    """Stage the trusted-author comments of a continued PR's thread — the only PR comments the review
    loop's iteration-1 resume hint seeds from (#139; `review-fix-round.md` "Resume hint"). Any GitHub
    user can comment on a public PR, so a forged `Settled (not addressed):` block, finding line or
    `Cold read:` line from an untrusted author would otherwise settle a finding or skip a check. The
    filter is `pipelib.trust.is_trusted` on each comment's `authorAssociation` (the #99 rule; a missing
    key is untrusted). Writes a JSON list of `{body, createdAt, url}` in thread order, always to a path
    (the distiller bundle's staged-paths rule), and returns the `facts.resume` shape. `present` means a
    thread was read and staged, even an empty one; an unreadable thread (not a list) stages nothing and
    reports `present: false`, so the seed is absent rather than silently empty."""
    if not isinstance(comments, list):
        return _absent_resume()
    kept, skipped = [], 0
    for comment in comments:
        if isinstance(comment, dict) and trust.is_trusted(comment.get("authorAssociation")):
            kept.append(
                {"body": comment.get("body") or "", "createdAt": comment.get("createdAt"), "url": comment.get("url")}
            )
        else:
            skipped += 1
    Path(path).write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"present": True, "comments_path": str(path), "trusted": len(kept), "skipped": skipped}


def _absent_revision():
    """The revision fact outside a revision run. A factory, for `_absent_tracker`'s reason."""
    return {"active": False, "reasons": []}


# The re-plan's mark on a veto it absorbed (skills/planner/references/revise-reconciliation.md
# "Evaluator-rejected bullet"): appended inside the evaluator's reason, so the veto stays un-ticked and
# its evidence stays readable while the resolver can tell it has been re-planned.
_VETO_REASSIGNED_RE = re.compile(r"re-plan reassigned to phase (\d+)")


def dod_vetoes(dod):
    """`facts.dod_vetoes` — one `{index, commit_sha, reassigned_to}` per evaluator-vetoed DoD bullet
    (`resolver-claimed-evaluator-rejected`). `reassigned_to` is the phase a later re-plan assigned the
    veto's fix to (the `re-plan reassigned to phase <Y>` mark), else `None`: a veto no re-plan has
    touched yet.

    String-level on purpose. A git probe ("is the vetoed commit an ancestor of the plan's SHA?") false-
    positives on a plan revised for an unrelated reason after the PR opened — grounded on the PR head, it
    "contains" a veto nobody re-planned. Only the re-plan's own mark says it re-planned. Never raises.
    """
    vetoes = []
    for bullet in dod or []:
        annotation = bullet.get("annotation") or {}
        if annotation.get("form") != "resolver-claimed-evaluator-rejected":
            continue
        match = _VETO_REASSIGNED_RE.search(annotation.get("reason") or "")
        vetoes.append(
            {
                "index": bullet.get("index"),
                "commit_sha": annotation.get("sha"),
                "reassigned_to": int(match.group(1)) if match else None,
            }
        )
    return vetoes


def build_revision(mode, phases, tracker, prior_pr_fact, vetoes, polish, reviewed_at_head, threads=None):
    """`facts.revision` — whether this continue-mode run is a **revision run** (spine S4): the PR
    exists and no `kind: code-shipping` phase is left unshipped, so the S4 cursor ("the first
    unticked phase") selects nothing. Today's evaluator soft-reject re-entry lands exactly here.

    Multi-phase, "shipped" means **every** phase's main tracker row is ticked — an operator /
    decision-only phase included: a pending trailing operator phase is S4's operator-phase handoff, and
    a revision run there would flip the PR ready before the operator's work exists.

    `reasons` (closed set, in this order) shape what iteration 1 reads and whether the run re-routes
    to the planner — they never short-circuit the loop:
      - `dod_rejected` — a veto (`dod_vetoes`) no re-plan has reassigned yet: only a re-plan clears it.
        A reassigned veto is not a reason — the run builds its fix and the push projects it;
      - `polish_apply` — a `## Polish` ledger entry marked `apply` (the operator's answer, recorded by the
        evaluator or by the resolver's finalisation — e.g. a Re-plan the planner folded into phase 1),
        counted only when `polish["dispositions_trusted"]` holds (#144): an `apply` someone else could
        have typed into the body is not the operator's answer, so it is not a reason either;
      - `review_threads` — a review thread a trusted human is waiting on (#145,
        skills/_shared/review-threads.md §4): one in state `changes-requested`, or an `operator-thread`
        without `pending_reply`. Counted only on an `available` read of `facts.review_threads` — an
        absent or failed read is no evidence of a request;
      - `review_at_head` — a non-approving review posted on the current head of a **draft** PR (a
        soft-reject flips it to draft; the self-approval downgrade's COMMENTED review does not).

    A convenience fact: no decision, no notice, never raises. An epic target's plan carries no
    `## Phases`, so it takes the single-phase branch and can read `active: True` — harmless, since
    `epic.md` never reads this fact (only the standard/story spine does).
    """
    if mode != MODE_CONTINUE or not prior_pr_fact or not prior_pr_fact.get("number"):
        return _absent_revision()
    if len(phases or []) > 1:
        ticked = {
            row["phase"] for row in (tracker or {}).get("rows") or []
            if row.get("checked") and row.get("sub_label") is None
        }
        active = all(p.get("number") in ticked for p in phases)
    else:
        # Single-phase: the PR opens at the phase's one push (S5.2), so an existing PR means it shipped.
        active = True
    if not active:
        return _absent_revision()
    reasons = []
    if any(veto.get("reassigned_to") is None for veto in vetoes or []):
        reasons.append("dod_rejected")
    if (polish or {}).get("dispositions_trusted") and any(
        entry.get("disposition") == "apply" for entry in polish.get("entries") or []
    ):
        reasons.append("polish_apply")
    if threads_waiting(threads):
        reasons.append("review_threads")
    if reviewed_at_head:
        reasons.append("review_at_head")
    return {"active": True, "reasons": reasons}


def threads_waiting(threads):
    """The review threads a trusted human is waiting on (skills/_shared/review-threads.md §4): every
    `changes-requested` thread and every `operator-thread` whose `pending_reply` is false — the
    resolver's iteration-1 thread input — that no trusted successor names: a named thread gates nothing
    (§4; a batch re-cut on a pure-split reply stays `changes-requested`, #163). Empty unless the read is
    `available`; never raises."""
    if not isinstance(threads, dict) or not threads.get("available"):
        return []
    return [
        thread for thread in threads.get("threads") or []
        if isinstance(thread, dict)
        and thread.get("superseded_by") is None
        and (
            thread.get("state") == "changes-requested"
            or (thread.get("state") == "operator-thread" and not thread.get("pending_reply"))
        )
    ]


def _build_tracker(prior_pr_fact, phases, repo, scratch_dir=None, cwd=None, current_user_login=None):
    """Fetch the prior PR's body and diff its `## Phase tracker` against `phases`. Returns
    `(tracker_facts, pr_extras, notices, decision_or_none)` — `pr_extras` carries what the same
    fetch yields for free: the `## Polish` ledger scan and whether a non-approving review sits on
    the current head (`facts.polish` / `facts.revision`), so neither costs another `gh` call — and the
    trusted-author comments the resume hint seeds from (`facts.resume`, #139), from the same thread.

    One extra `gh pr view`, on continue mode only. `gh_gather` fetches an open PR's body and then
    strips it (`_REFERENCE_FILTER_ONLY_FIELDS`) because it is only needed for reference filtering;
    keeping it there would widen the payload every prep carries, so this targeted fetch is the
    narrower change. Mirrors `prep_planner._build_revise_facts`.
    """
    notices = []
    if not prior_pr_fact or not prior_pr_fact.get("number"):
        return _absent_tracker(), _absent_pr_extras(), notices, None

    pr_facts, pr_notices, decision = gh_pr_gather.build_pr_facts(
        prior_pr_fact["number"], repo, scratch_dir=scratch_dir, cwd=cwd
    )
    # Merge BEFORE the decision check: `build_pr_facts` populates notices at each of its decision
    # returns too, and the caller forwards them alongside the decision (the same shape as this
    # module's issue-envelope forward) — dropping them would lose a diagnostic on the one path where
    # the operator has least to go on.
    for notice in pr_notices or []:
        if notice not in notices:
            notices.append(notice)
    if decision is not None:
        return None, _absent_pr_extras(), notices, decision

    pr_body = read_section(pr_facts, "body")
    scan = parse.scan_phase_tracker(pr_body)

    tracker = {
        # `present` is whether the SECTION exists, never `bool(rows)`: a section whose rows are all
        # malformed parses to zero rows while its unknown tick state is exactly what must be
        # reconciled. The spine gates its whole reconciliation step on this flag, so conflating the
        # two would skip the step precisely where it is needed.
        "present": scan["present"],
        "rows": scan["rows"],
        "diff": build_tracker_diff(scan["rows"], phases, unparsed=scan["unparsed"]),
        "body_mode": pr_facts.get("body_mode"),
    }
    if pr_facts.get("body_mode") == "path":
        tracker["body_path"] = pr_facts.get("body_path")
    elif scratch_dir is not None and pr_facts.get("body_mode") == "inline":
        # Staged paths, always (the distiller bundle's rule): an inline body is written out once so
        # every continue-mode PR-body write has a fetched base to restage from — the copy the
        # spine's S6 and an in-session continuation (references/phase-continuation.md) rely on.
        staged = Path(scratch_dir) / ("pr-%s-body.md" % prior_pr_fact["number"])
        staged.write_text(pr_body, encoding="utf-8")
        tracker["body_path"] = str(staged)
    # Who could have written the body: the `## Polish` ledger's dispositions (#144) and the
    # `## Security review` record's dismissals (#139) count only on this account's PR. The resolver
    # continues another author's PR only on a shared epic head, and reads no association, so a
    # teammate's ledger and record fail closed and are re-asked.
    own_pr = bool(current_user_login and prior_pr_fact.get("author") == current_user_login)
    extras = {
        "polish": dict(parse.scan_polish(pr_body), dispositions_trusted=own_pr),
        # The `## Security review` record (skills/_shared/security-review.md) — finalisation's
        # security pass matches its findings against these entries.
        "security": dict(parse.scan_security(pr_body), dismissals_trusted=own_pr),
        # The `## Out-of-plan changes` lines (skills/_shared/review-threads.md §8) S5.2 keeps.
        "out_of_plan": parse.scan_out_of_plan(pr_body),
        # A soft-reject flips the PR back to draft; the evaluator's self-approval downgrade (an
        # APPROVE posted as a COMMENTED review on a self-authored PR) leaves it ready. The draft
        # flag is what tells the two apart — the review objects look the same.
        "review_at_head": bool(pr_facts.get("isDraft"))
        and review_at_head(_load_reviews(pr_facts), pr_facts.get("headRefOid")),
        "resume": _absent_resume(),
    }
    if scratch_dir is not None:
        extras["resume"] = stage_resume_comments(
            # `None` on an unreadable thread: the resume seed is then absent — the safe side.
            _load_json_list(pr_facts, "thread"),
            Path(scratch_dir) / ("pr-%s-resume-comments.json" % prior_pr_fact["number"]),
        )
    return tracker, extras, notices, None


def last_shipped_phase(rows, workspace_path):
    """The shipped phase nearest HEAD — the non-final review's diff base (spine S5.1 "Scope").

    Candidates are the ticked MAIN rows carrying a `(commit <sha>)`: a sub-row records an operator
    phase, which ships no commits. Phases ship in `depends-on` order, not numeric order, so "the
    highest ticked number" can name a commit that is not the branch's latest shipped state; the
    rule is instead the candidate that is an ancestor of HEAD with the fewest commits between it and
    HEAD — one `git rev-list --left-right --count <sha>...HEAD` per candidate, probed in the work
    workspace: `behind<TAB>ahead`, an ancestor iff `behind == 0`, the distance `ahead`. Returns
    `{"phase", "commit_sha", "reachable"}` or `None` when nothing has shipped. Candidates present but
    none reachable (a force-pushed-away SHA, no workspace to probe) → the numerically last ticked row
    with `reachable: false`, so the spine falls back to `facts.workspace.base_ref`.

    A convenience fact: no decision, no notice, never raises — an unprobeable SHA must not break a
    session that reviews fine on the cumulative diff. `build_facts` calls it only for a multi-phase
    plan; the spine never reads it on a single-phase issue.
    """
    candidates = [
        row for row in rows or []
        if row.get("checked") and row.get("sub_label") is None and row.get("commit_sha")
    ]
    if not candidates:
        return None
    best = None
    if workspace_path:
        for row in candidates:
            sha = row["commit_sha"]
            try:
                counts = process.run(
                    ["git", "rev-list", "--left-right", "--count", "%s...HEAD" % sha],
                    cwd=workspace_path,
                )
                if counts.returncode != 0:
                    continue
                behind, ahead = (int(part) for part in (counts.stdout or "").split())
            except (OSError, ValueError):
                continue
            if behind != 0:
                continue
            distance = ahead
            if best is None or distance < best[0]:
                best = (distance, row)
    if best is not None:
        return {"phase": best[1]["phase"], "commit_sha": best[1]["commit_sha"], "reachable": True}
    fallback = max(candidates, key=lambda row: row["phase"])
    return {"phase": fallback["phase"], "commit_sha": fallback["commit_sha"], "reachable": False}


def _forward_decision(decision, notices=None):
    """Emit a composed core's returned `decision` (or a directly-raised one) AS-IS on prep's own
    stdout and return `True` when a decision was present. Mirrors `prep_evaluator._forward_decision`
    exactly — see that function's docstring for the full rationale."""
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


# Non-blocking notice (open notice vocabulary, architecture.md §3): the open integration PR's
# current body could not be read. The epic route's S4.2 refresh then SKIPS its write — re-rendering
# without the current body would drop every section it does not own (`## Polish`, `## Follow-ups`).
INTEGRATION_PR_BODY_UNAVAILABLE = "INTEGRATION_PR_BODY_UNAVAILABLE"


def _stage_delivered(root, repo, epic_branch, scratch_dir, notices, cwd=None):
    """``facts.epic.delivered`` (#149): the files the PRs merged into the epic branch changed
    (:func:`branching.delivered_paths`), staged to ``<scratch>/epic-delivered.json`` — always a path,
    since an epic's union runs to hundreds of paths — and reported as ``{"path", "prs", "paths",
    "unreadable"}`` (the two counts, and the PR numbers whose commit could not be read). The resolver
    passes the path to ``blast_radius.py assess --delivered`` on the integration pass, so a file a
    merged PR delivered does not raise it just because the epic plan, written at coordination
    altitude, names no story's files. ``None`` plus :data:`branching.EPIC_DELIVERED_UNAVAILABLE` in
    ``notices`` when the listing failed: the pass then raises as it did before, the strict outcome.
    Never a decision. Needs no asserted workspace (``gh`` plus ``git`` at the main checkout), so it
    runs on ``--refresh`` too."""
    fact, notice = branching.delivered_paths(root, repo, epic_branch, cwd=cwd)
    if notice:
        notices.append(notice)
    if fact is None:
        return None
    if fact["at_limit"]:
        notices.append(
            "%d merged PRs listed against %s — the list limit; older PRs' files may be missing from the "
            "delivered set, and raise the integration pass's review level"
            % (branching.DELIVERED_PR_LIST_LIMIT, epic_branch)
        )
    if fact["unreadable"]:
        # Named up to a few: an offline run can leave every merged PR unreadable, and the notice rides
        # in every epic prep's envelope.
        named = ["#%s" % (number if number is not None else "?") for number in fact["unreadable"][:5]]
        more = len(fact["unreadable"]) - len(named)
        notices.append(
            "merged PR(s) %s%s into %s: merge commit not readable — their files are not in the delivered "
            "set, and raise the integration pass's review level"
            % (", ".join(named), " (+%d more)" % more if more else "", epic_branch)
        )
    staged = Path(scratch_dir) / "epic-delivered.json"
    staged.write_text(json.dumps(fact, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "path": str(staged),
        "prs": len(fact["prs"]),
        "paths": len(fact["paths"]),
        "unreadable": fact["unreadable"],
    }


def _stage_integration_pr_body(integration_pr, repo, scratch_dir, cwd=None, with_comments=False):
    """Stage the open integration PR's current body (``integration_pr["body_path"]``, always a path)
    for the epic route's S4.2: the refresh re-renders only the sections it owns and keeps the rest —
    the PR's own review loop writes `## Polish` / `## Follow-ups` there, and the evaluator writes the
    ledger's dispositions. The head-scoped lookup carries no body, hence this one extra read.
    Returns a notice or ``None``; never a decision.

    Deliberately not `gh_pr_gather.build_pr_facts` (which `_build_tracker` uses for the prior PR's
    body): that core ``sys.exit(1)``s on a failed view and also fetches the comment thread. Here a
    failed read must degrade to the notice, because the refresh it feeds is skippable and the prep
    must not die over it — and only the body is needed, plus its comments when `with_comments`. The
    comments feed the resume seed (`resume_comments`, #139): a teammate-opened integration PR was a
    gated row when `_build_tracker` would have staged them, so this read is the only one that sees its
    thread. They are fetched only when a PR is being continued, so a fresh run's body read cannot fail
    over its comments; otherwise `resume_comments` is absent."""
    fields = "body,comments" if with_comments else "body"
    result = process.run(
        ["gh", "pr", "view", str(integration_pr["number"]), "--repo", repo, "--json", fields],
        cwd=cwd,
    )
    view = None
    if result.returncode == 0:
        try:
            view = json.loads(result.stdout) or {}
        except ValueError:
            view = None
    body = view.get("body") if isinstance(view, dict) else None
    if not isinstance(body, str):
        integration_pr["body_path"] = None
        integration_pr["resume_comments"] = _absent_resume()
        return INTEGRATION_PR_BODY_UNAVAILABLE
    staged = Path(scratch_dir) / ("epic-integration-pr-%s-current.md" % integration_pr["number"])
    staged.write_text(body, encoding="utf-8")
    integration_pr["body_path"] = str(staged)
    integration_pr["resume_comments"] = (
        stage_resume_comments(
            view.get("comments"),
            Path(scratch_dir) / ("epic-integration-pr-%s-resume-comments.json" % integration_pr["number"]),
        )
        if with_comments
        else _absent_resume()
    )
    return None


def build_facts(issue_number, repo, root=".", scratch_dir=None, refresh=False, cwd=None):
    """Assemble the resolver's complete facts block and return the envelope dict WITHOUT printing
    it (the testable core, mirroring `prep_evaluator.build_facts`'s split). Returns `None` after a
    `needs_decision` envelope has already been emitted on `stream` (the caller's `main()` reads
    `EXIT_OK` regardless — `needs_decision` is exit 0 per architecture.md §3).
    """
    # Normalize to the MAIN checkout: under v3 the session — and therefore any relative --root —
    # sits inside the work worktree; the audit ro-* view and the ls-remote queries key off the main
    # checkout regardless.
    root = str(workspace._resolve_main_root(root))
    root_branch = workspace.default_branch(root)
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-resolver-%s" % issue_number
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    # 1) The target issue — one round-trip gh_gather.run() call: body/thread/plan-marker/native
    #    deps + the open-PR search already folded in (architecture.md §7's GATHER_ISSUE contract).
    exit_code, issue_envelope = gh_gather.run(
        str(issue_number),
        repo,
        marker_prefix=PLAN_MARKER,
        scratch_dir=scratch_dir,
        env=None,
        stream=_DiscardStream(),
    )
    if issue_envelope is not None and issue_envelope.get("status") == "needs_decision":
        if _forward_decision(issue_envelope["decision"], notices=issue_envelope.get("notices")):
            return None
    if exit_code != 0:
        sys.stderr.write(
            "prep_resolver: gh_gather on issue #%s failed (exit %d)\n" % (issue_number, exit_code)
        )
        sys.exit(1)

    issue_body = issue_envelope.get("issue_body")
    if issue_body is None and issue_envelope.get("issue_body_mode") == "path":
        issue_body = Path(issue_envelope["issue_body_path"]).read_text(encoding="utf-8")
    issue_body = issue_body or ""

    labels = [label.get("name") for label in issue_envelope.get("labels") or []]
    issue_title = issue_envelope.get("title") or ""
    issue_type = _detect_type(labels, issue_title)

    # 2) State vector: prior-PR row -> mode.
    current_user_login, user_decision = _fetch_current_user(cwd)
    if user_decision is not None:
        emit_needs_decision(user_decision)
        return None
    # This issue's OWN open PRs, not every PR that mentions it (an epic integration PR lists every
    # story by number). Narrowed BEFORE the emptiness gate below, which is what decides whether
    # the closed search runs.
    open_prs, mention_only_prs = _partition_prs_for_issue(
        issue_envelope.get("open_prs"), issue_number
    )
    closed_prs = None
    if not open_prs:
        # Only searched when no open PR exists (docs/specs/resolver.md step-5 table's rows are
        # mutually exclusive; an open PR always wins) — keeps the canonical no-prior-PR/open-PR
        # paths at their minimal call count, per this step's call-budget DoD box.
        closed_prs, closed_pr_decision = _search_closed_prs(repo, issue_number, cwd=cwd)
        if _forward_decision(closed_pr_decision):
            return None
    prior_pr_row, prior_pr_fact = _classify_prior_pr_row(
        open_prs, current_user_login, closed_prs, issue_envelope.get("state"), issue_number
    )
    # Diagnostic only — omitted when empty, never `attention`, nothing gates on it. It exists so a
    # surprising route ("why did it ignore the epic's PR?") is answerable from the envelope.
    prior_pr_rejected = [
        {"number": pr.get("number"), "headRefName": pr.get("headRefName")}
        for pr in mention_only_prs
    ]
    if prior_pr_row in _CONTINUE_ROWS:
        mode = MODE_CONTINUE
    elif prior_pr_row in _GATED_ROWS:
        mode = MODE_GATED
    else:
        mode = MODE_FRESH
    gate = None
    if mode == MODE_GATED:
        # An unmissable gate fact, never a pre-empting side effect — mirrors the open-question
        # hard gate below. S10's router renders this AskUserQuestion card verbatim; prep does not
        # ensure a work worktree, does not fabricate a branch name, and does not otherwise act on
        # this issue's code until the operator answers.
        card = _GATE_CARDS[prior_pr_row]
        gate = {
            "reason": prior_pr_row,
            "header": card["header"],
            "options": card["options"],
            "prior_pr": prior_pr_fact,
        }

    # 3) Plan facts (marker present/absent, SHA parsed from its "planned <ISO> at `<ref>@<sha>`"
    #    header line, comment id, body staged to scratch).
    plan_present = bool(issue_envelope.get("marker_comment_present"))
    plan_body = None
    if plan_present:
        plan_body = issue_envelope.get("marker_comment_body")
        if plan_body is None and issue_envelope.get("marker_comment_mode") == "path":
            plan_body = Path(issue_envelope["marker_comment_path"]).read_text(encoding="utf-8")
    plan_sha = _extract_plan_sha(plan_body) if plan_body else None
    plan_facts = {
        "present": plan_present,
        "sha": plan_sha,
        "comment_id": issue_envelope.get("marker_comment_id"),
        "comment_url": issue_envelope.get("marker_comment_url"),
    }
    if plan_present:
        plan_facts["body_mode"] = issue_envelope.get("marker_comment_mode")
        if issue_envelope.get("marker_comment_mode") == "path":
            plan_facts["body_path"] = issue_envelope.get("marker_comment_path")

    # 4) Phase facts (parse.parse_phases, pure core) — only when a plan exists.
    phases = []
    if plan_present and plan_body:
        try:
            phases = parse.parse_phases(plan_body)
        except parse._PhasesMalformed as exc:  # noqa: SLF001
            from pipelib.decisions import PHASES_MALFORMED

            emit_needs_decision(
                needs_decision(
                    PHASES_MALFORMED,
                    summary=exc.reason,
                    context={"issue": issue_number, "line_number": exc.line_number, "raw_line": exc.raw_line},
                    options=[
                        "fix the '## Phases' section by hand to match the structured-key grammar "
                        "in skills/planner/references/plan-schema.md, then re-run"
                    ],
                )
            )
            return None

    # 4b) `## Phase tracker` reconciliation facts — continue mode only, and only once `phases` is
    #     in hand (the diff is plan-versus-tracker). Fresh mode has no prior tracker to reconcile.
    if mode == MODE_CONTINUE:
        tracker, pr_extras, tracker_notices, tracker_decision = _build_tracker(
            prior_pr_fact, phases, repo, scratch_dir=scratch_dir, cwd=cwd,
            current_user_login=current_user_login,
        )
        if _forward_decision(tracker_decision, notices=tracker_notices):
            return None
    else:
        tracker, tracker_notices = _absent_tracker(), []
        pr_extras = _absent_pr_extras()

    # 4c) Shipped-phase records (skills/_shared/plan-shipped-phases.md). A revise moves a shipped
    #     phase's `## Changes` / `## Data model / schema impact` / `## Test plan` entries into a
    #     record keyed to the PR it shipped on, so the plan comment alone is no longer the whole locked
    #     plan. Read by the PR the plan's own pointer bullets name — normally the continuing PR, but on
    #     a fresh run after that PR closed the records are the ONLY copy of those entries, and reading
    #     by the continuing PR alone would drop them silently.
    shipped_attention = []
    plan_facts["shipped"] = {"present": False, "pr": None, "prs": [], "entries": [], "unkeyed": []}
    if plan_present:
        current_pr = prior_pr_fact.get("number") if (mode == MODE_CONTINUE and prior_pr_fact) else None
        records, shipped_attention, shipped_decision = plan_shipped.read_pointed(
            load_thread(issue_envelope), plan_body, current_pr
        )
        if _forward_decision(shipped_decision):
            return None
        plan_facts["shipped"] = plan_shipped.staged_fact(
            records, scratch_dir, "issue-%s-plan-shipped.md" % issue_number
        )

    # 5) DoD facts (parse.parse_dod_bullets, pure core) — over the ISSUE body (the resolver
    #    projects ticks onto the issue's own DoD, distinct from prep_evaluator's per-closing-issue
    #    keying since the resolver has exactly one target issue, not a PR's closing-issue set).
    try:
        dod = parse.parse_dod_bullets(issue_body)
    except parse._DodMalformed as exc:  # noqa: SLF001
        from pipelib.decisions import DOD_MALFORMED

        emit_needs_decision(
            needs_decision(
                DOD_MALFORMED,
                summary=exc.reason,
                context={"issue": issue_number, "line_number": exc.line_number, "raw_line": exc.raw_line},
                options=[
                    "fix the bullet's annotation by hand to match one of the closed-set forms in "
                    "skills/_shared/dod-annotations.md, then re-run"
                ],
            )
        )
        return None

    #    ...and how the plan's `closes-dod` claims line up with it (`parse.dod_coverage`, `None`
    #    under the single-phase fallback). `drift` is the projection rule's "bullet count drift" as a
    #    FACT: before it, the rule assumed a multi-phase plan against a DoD-less issue could not get
    #    past the plan reviewer, so a session that met one improvised — projecting the plan's indexes
    #    onto an `## Acceptance criteria` list no downstream parser reads.
    dod_coverage = parse.dod_coverage(phases, parse.dod_summary(issue_body))

    # 6) Open-question facts (parse.parse_oq_links joined with tracker state + native blocked_by).
    blocked_by = issue_envelope.get("blocked_by") or []
    open_questions, oq_blocked, oq_decision = _build_open_question_facts(
        issue_body, repo, blocked_by, scratch_dir, cwd=cwd
    )
    if _forward_decision(oq_decision):
        return None

    # Native `blocked by` is ALSO a hard gate independent of the OQ-links section (docs/specs/
    # resolver.md step 3: "if any blocker is open ... the issue is blocked"), so the comment-only
    # classification fires on either signal.
    any_open_native_blocker = any((b.get("state") or "").upper() == "OPEN" for b in blocked_by)
    comment_only = bool(oq_blocked) or any_open_native_blocker

    # 7) Epic / story facts + audit-ref derivation.
    epic_facts = None
    story_epic_matches = None
    epic_branch_for_audit = None
    epic_notices = []
    if issue_type == "epic":
        epic_facts, epic_decision = _discover_epic_branch(root, issue_number, issue_title)
        if _forward_decision(epic_decision):
            return None
        epic_branch_for_audit = epic_facts.get("branch")

        # An epic integration PR carries `Fixes #<epic>`, so the generic `#<N> in:body` open-PR
        # search finds it too — and when a TEAMMATE opened it, that classifies as a gated row,
        # which suppresses the work-workspace assertion and offers a "start fresh" card whose
        # answer on an epic is a SECOND integration PR. Opening the PR early makes that state
        # routine rather than rare, so downgrade it: the `epic/<N>-<slug>` branch is shared
        # infrastructure (created by workspace-open, advanced by every story merge), not one
        # author's claimed work — the takeover gate exists to stop two people racing on a feature
        # branch and misfires on a container branch. Head-scoped via `branch_belongs_to_issue`, so
        # a foreign PR on any OTHER head still gates exactly as before. Decided before the
        # integration PR's body read below, which fetches its comments only for a continued PR.
        if mode == MODE_GATED and _branch_belongs_to_issue(
            (prior_pr_fact or {}).get("headRefName"), issue_number
        ):
            mode = MODE_CONTINUE
            gate = None

        # The integration PR opens EARLY — as a draft, as soon as the epic branch carries commits
        # `main` does not — so a human can review overall epic progress instead of waiting for the
        # last story to close. Both facts below exist so the playbook consumes the trigger and the
        # idempotency guard as DATA rather than re-deriving either in prose.
        epic_facts["commits_ahead_base"] = root_branch
        if epic_branch_for_audit:
            commits_ahead, ahead_notice = _count_commits_ahead(
                root, epic_branch_for_audit, root_branch
            )
            epic_facts["commits_ahead"] = commits_ahead
            if ahead_notice:
                epic_notices.append(ahead_notice)
            integration_pr, open_pr_notice = _find_open_pr_for_head(
                repo, epic_branch_for_audit, cwd=cwd
            )
            if integration_pr is not None:
                integration_pr["is_yours"] = (
                    integration_pr.get("author") is not None
                    and integration_pr.get("author") == current_user_login
                )
                body_notice = _stage_integration_pr_body(
                    integration_pr, repo, scratch_dir, cwd=cwd, with_comments=(mode == MODE_CONTINUE)
                )
                if body_notice:
                    epic_notices.append(body_notice)
            epic_facts["integration_pr"] = integration_pr
            if open_pr_notice:
                epic_notices.append(open_pr_notice)
            epic_facts["delivered"] = _stage_delivered(
                root, repo, epic_branch_for_audit, scratch_dir, epic_notices, cwd=cwd
            )
        else:
            # No branch on origin yet (bootstrap): 0 commits ahead by construction and no PR can
            # exist, so neither lookup runs — a branch that does not exist cannot carry either, nor
            # a merged PR, so there is no delivered set and no notice.
            epic_facts["commits_ahead"] = 0
            epic_facts["integration_pr"] = None
            epic_facts["delivered"] = None

        # The `## Polish` ledger and `## Security review` record finalisation reads live on the
        # integration PR. Read them from the body staged above rather than the prior-PR fetch: a
        # teammate-opened PR was still a gated row when step 4b chose `_absent_pr_extras()`, so that
        # fetch never ran, and a section read as absent would re-mint ids the body already carries.
        # Its ledger dispositions (#144) and dismissals (#139) count only on this account's PR.
        # Its resume seed too (#139): `resume_comments` is present only when the body read fetched
        # the comments, which it does only for a continued PR — the resume hint runs only then.
        integration_pr_fact = epic_facts.get("integration_pr") or {}
        integration_body_path = integration_pr_fact.get("body_path")
        if integration_body_path:
            integration_body = Path(integration_body_path).read_text(encoding="utf-8")
            own_integration_pr = bool(integration_pr_fact.get("is_yours"))
            pr_extras = dict(
                pr_extras,
                polish=dict(
                    parse.scan_polish(integration_body), dispositions_trusted=own_integration_pr
                ),
                security=dict(
                    parse.scan_security(integration_body), dismissals_trusted=own_integration_pr
                ),
                out_of_plan=parse.scan_out_of_plan(integration_body),
            )
            if (integration_pr_fact.get("resume_comments") or {}).get("present"):
                pr_extras["resume"] = integration_pr_fact["resume_comments"]
    else:
        # Hierarchy lookup gated on HAVING a parent, not on the lexical type (#31) — the same gate
        # move as prep_workspace_open's, and it has to happen HERE too or the asserted worktree and
        # the PR base disagree: `base_ref` below is what the create-pr step's `--base` slot
        # consumes, so an untyped sub-issue would open a PR targeting the default branch and take
        # its parent epic's unmerged work with it.
        epic_facts, parent_notices, epic_decision = _resolve_parent_epic_branch(
            root, repo, issue_number, issue_type, issue_envelope.get("parent"), cwd=cwd
        )
        epic_notices.extend(parent_notices)
        if _forward_decision(epic_decision):
            return None
        if epic_facts is not None:
            story_epic_matches = (
                [epic_facts["parent_epic"]] if epic_facts.get("parent_epic") else []
            )
            epic_branch_for_audit = (epic_facts.get("branch_facts") or {}).get("branch")

    audit_ref = _audit_ref(epic_branch_for_audit, root_branch)

    # A gated row is a flow gate, not an assembly blocker (identical reasoning to comment_only /
    # the open-question hard gate) — prep still completes and reports `status: ok`, it just never
    # asserts (or derives an expected branch for) a WORK workspace impersonating someone else's
    # in-flight PR. `read_workspaces.audit` may still be assembled below (it's a detached,
    # reused-by-ref view of the audit target, never a branch checkout of the other author's own
    # work).
    skip_work_workspace = comment_only or mode == MODE_GATED

    # 8) Expected-branch ladder (v3) — the session runs INSIDE the worktree the operator opened
    #    (workspace-open); prep no longer mints a branch, it derives what the ambient checkout is
    #    EXPECTED to be on, then asserts it (step 9's attach). Linked-branch first: once
    #    workspace-open has pushed the branch, re-running collision naming would count that very
    #    branch via ls-remote and yield `-v2` — a guaranteed self-mismatch — so the ladder adopts
    #    existing state and only ever *words* a computed name into the mismatch card.
    expected_branch = None
    accept_branch_pattern = None
    link_notices = []
    if not refresh and not skip_work_workspace:
        if mode == MODE_CONTINUE and prior_pr_fact and prior_pr_fact.get("headRefName"):
            # (1) Continue: the prior PR's own head branch, exact.
            expected_branch = prior_pr_fact["headRefName"]
        else:
            # (2) The issue's GitHub-linked branch (gh issue develop), exact — adopt, never
            #     re-derive. Degrades with a notice where linking is unsupported.
            linked, link_notice, link_decision = branching.list_linked_branches(
                repo, issue_number, cwd=cwd
            )
            if _forward_decision(link_decision):
                return None
            if link_notice:
                link_notices.append(link_notice)
            if linked and len(linked) > 1:
                emit_needs_decision(
                    needs_decision(
                        AMBIGUOUS,
                        summary="%d GitHub-linked branches exist for issue #%s — expected at most one"
                        % (len(linked), issue_number),
                        context={"issue": issue_number, "candidates": linked},
                        options=[
                            "unlink or delete the stray branch(es), then re-run",
                            "start the session in the worktree of the branch you mean, and re-run "
                            "against the issue it belongs to",
                        ],
                    )
                )
                return None
            if linked:
                expected_branch = linked[0]
            elif issue_type == "epic":
                # (3) Epic: the discovered integration branch, exact; on bootstrap (zero matches,
                #     e.g. linking unsupported when workspace-open created it locally), accept any
                #     ambient `epic/<N>-…` — the number is the invariant.
                if epic_branch_for_audit:
                    expected_branch = epic_branch_for_audit
                else:
                    bootstrap_slug = (epic_facts or {}).get("bootstrap_slug") or ""
                    expected_branch = "epic/%s-%s" % (issue_number, bootstrap_slug)
                    accept_branch_pattern = r"^epic/%s-" % issue_number
            else:
                # (4) Standard/story: accept any ambient `<N>-…` branch — never recompute the
                #     `-vN` suffix against a branch workspace-open already pushed; the computed
                #     name is derived only to word the mismatch card.
                worded_name, _collided = compute_branch_name(
                    root, issue_number, compute_fresh_slug(issue_title)
                )
                expected_branch = worded_name
                accept_branch_pattern = r"^%s-" % issue_number

    # 9) Workspace assertion + audit view + config, per mode (skipped on --refresh, same contract
    #    as prep_evaluator's). Gate config and the attach's hook discovery both read the ambient
    #    work workspace's working tree — the same checkout the session runs in, so what the
    #    operator sees is what gates them.
    config_attention = []
    grounding_path, grounding_docs, catalogue_notices = None, None, []
    if not refresh:
        work_workspace_envelope = None
        work_base = None
        if not skip_work_workspace and expected_branch:
            work_base = (
                root_branch
                if (issue_type == "epic" or audit_ref == root_branch)
                else audit_ref
            )
            work_workspace_envelope, _ws_notices, ws_decision = workspace._build_attach(
                cwd if cwd is not None else ".",
                expected_branch,
                base_for_unpushed=work_base,
                run_hooks=True,
                accept_branch_pattern=accept_branch_pattern,
                check_remote_staleness=True,
            )
            if _forward_decision(ws_decision):
                return None

        read_workspace_envelope = None
        if not comment_only and audit_ref != root_branch:
            # Epic-as-target / story-under-open-epic: a genuinely second ref from the work
            # workspace's own branch, so a dedicated read workspace is ensured (architecture.md
            # §6: "a skill with no second view omits the key entirely" — the standard/no-epic path
            # never reaches this branch, since audit_ref == 'main' there). Script-internal ro-*
            # plumbing: the operator never opens or sees this view.
            read_workspace_envelope, _rw_notices, rw_decision = workspace._build_ensure_read(
                root, audit_ref
            )
            if _forward_decision(rw_decision):
                return None

        # The gate-config vantage: the asserted work workspace when there is one, else the
        # invoking checkout (comment-only and gated rows never assert a workspace).
        # `invoking_checkout`, not a bare `_toplevel_of(".")`: the process cwd is only the right
        # answer when it is a checkout of THIS repo, and it may not be a checkout at all — a bare
        # toplevel call then raises an uncaught RuntimeError and the run emits no envelope, which
        # is both a §3 violation and a crash on a route that has a perfectly good `root` to use.
        gate_root = (
            work_workspace_envelope["path"]
            if work_workspace_envelope is not None
            else workspace.invoking_checkout(cwd if cwd is not None else root)
        )
        gate_config, config_notices = _read_gate_config(gate_root)
        gate_config["source"] = workspace._hook_source_facts(gate_root, None)
        gate_config["source"].pop("file", None)  # per-block *_source names the file; this is the tree
        if gate_config["source"].get("dirty"):
            config_attention.append(
                "gate config was read from a checkout with uncommitted changes (%s) — the checks "
                "that judge this work are the ones in your working tree" % gate_root
            )

        # Grounding docs (skills/_shared/doc-catalogue.md) — read at the AUDIT view: the read
        # workspace when one was ensured (epic-as-target, story under an open epic), else the work
        # workspace (the standard path, where audit_ref == main and the worktree IS the audit view).
        # One resolved vantage, emitted as `grounding_path`, so neither the spine nor the audit
        # prompt branches on which view exists. Comment-only and gated rows ground nothing, so they
        # read nothing — `None` keeps their attention silent rather than reporting a catalogue
        # nobody looked for.
        if not skip_work_workspace:
            audit_view = read_workspace_envelope or work_workspace_envelope
            if audit_view is not None:
                grounding_path = audit_view["path"]
                grounding_docs, catalogue_notices = doc_catalogue.read_catalogue(grounding_path)
    else:
        work_workspace_envelope = None
        read_workspace_envelope = None
        work_base = None
        gate_config, config_notices = {}, []

    # 9b) The review threads of the PR this run continues (#145, skills/_shared/review-threads.md):
    #     the prior PR in continue mode, else an epic target's open integration PR — the PR the run
    #     would push to. Judged at the asserted workspace HEAD, the code this run starts from; a failed
    #     read is a notice and `available: false`. Never on --refresh or without an asserted workspace.
    review_threads_fact = review_threads.absent_review_threads()
    thread_notices = []
    if not refresh and work_workspace_envelope is not None and work_workspace_envelope.get("sha"):
        thread_pr = None
        if mode == MODE_CONTINUE and prior_pr_fact and prior_pr_fact.get("number"):
            thread_pr = prior_pr_fact["number"]
        elif issue_type == "epic" and (epic_facts or {}).get("integration_pr"):
            thread_pr = epic_facts["integration_pr"].get("number")
        if thread_pr:
            review_threads_fact, thread_notices = review_threads.read_threads(
                repo, thread_pr, work_workspace_envelope["path"], work_workspace_envelope["sha"],
                scratch_dir, cwd=cwd,
            )

    # 10) Distiller input bundle — staged PATHS only (never above-threshold inline bytes; this
    #     step's DoD box). Reuses gh_gather's own spill files rather than re-writing copies.
    distiller_bundle = _build_distiller_bundle(issue_envelope, scratch_dir, issue_number)
    distiller_bundle["plan_shipped_path"] = plan_facts["shipped"].get("body_path")

    suggested_playbook = _suggested_playbook(issue_type, comment_only, epic_branch_for_audit)

    vector = {
        "type": issue_type,
        "mode": mode,
        "prior_pr_row": prior_pr_row,
        "comment_only": comment_only,
    }
    if gate is not None:
        vector["gate"] = gate

    # One unmissable top-level boolean for the open-question hard gate (S10 should not have to
    # derive it from a conjunction of comment_only + per-entry statuses) — mirrors the prior-PR
    # gate fact above: a fact the router reads directly, never re-derives.
    blocking_oq_entries = [
        entry for entry in (open_questions or []) if entry.get("status") == "blocked"
    ]
    open_questions_gate = {"blocked": bool(blocking_oq_entries), "blocking": blocking_oq_entries}

    facts = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root, "default_branch": root_branch, "fresh": not refresh},
        "target": {
            "kind": "issue",
            "number": issue_envelope["number"],
            "title": issue_envelope["title"],
            "state": issue_envelope["state"],
            "labels": labels,
            "blocked_by": blocked_by,
            "blocking": issue_envelope.get("blocking") or [],
            "deps_available": issue_envelope.get("deps_available"),
            # Native epic↔story hierarchy (skills/_shared/epic-story-hierarchy.md): the native source of the
            # story-set / parent-epic read the epic flow and the audit's dimension 5 consume.
            "parent": issue_envelope.get("parent"),
            "sub_issues": issue_envelope.get("sub_issues") or [],
            "sub_issues_summary": issue_envelope.get("sub_issues_summary") or {},
            "subissues_available": issue_envelope.get("subissues_available"),
        },
        "vector": vector,
        "prior_pr": prior_pr_fact,
        "plan": plan_facts,
        "phases": phases,
        "tracker": tracker,
        # The `## Polish` ledger (skills/_shared/polish-ledger.md), with `dispositions_trusted` (#144),
        # and the revision-run fact (spine S4) — both read from the continue-mode PR fetch above, no
        # extra call.
        "polish": pr_extras["polish"],
        # The `## Security review` record, from the same fetch; `absent_security()` in fresh mode. Each
        # entry's `thread` says whether a thread-cited dismissal is confirmed by the thread it cites (#145).
        "security": review_threads.confirm_dismissals(pr_extras["security"], review_threads_fact),
        # The trusted-author comments the review loop's resume hint seeds from (#139), staged to a
        # path; absent in fresh mode.
        "resume": pr_extras["resume"],
        # The review threads and trusted conversation of the PR this run pushes to (#145): the
        # continued PR, or an epic target's open integration PR; `{present: false}` when there is
        # none, and on --refresh.
        "review_threads": review_threads_fact,
        # The `## Out-of-plan changes` lines already on that PR, which S5.2 keeps (#145).
        "out_of_plan": pr_extras["out_of_plan"],
        "revision": build_revision(
            mode, phases, tracker, prior_pr_fact, dod_vetoes(dod), pr_extras["polish"],
            pr_extras["review_at_head"], review_threads_fact,
        ),
        "dod": dod,
        "dod_vetoes": dod_vetoes(dod),
        "dod_coverage": dod_coverage,
        "open_questions": open_questions,
        "open_questions_gate": open_questions_gate,
        "audit_ref": audit_ref,
        "suggested_playbook": suggested_playbook,
        "config": gate_config,
        "distiller_bundle": distiller_bundle,
        "grounding_path": grounding_path,
        "grounding_docs": grounding_docs if grounding_docs is not None else [],
        "attention": _build_attention(
            work_workspace_envelope, prior_pr_row, epic_facts, story_epic_matches
        ) + config_attention + shipped_attention
        + _catalogue_attention(grounding_docs, DOC_CATALOGUE_ABSENT in catalogue_notices)
        + _dod_drift_attention(dod_coverage),
        "notices": list(config_notices) + link_notices + epic_notices + tracker_notices
        + catalogue_notices + thread_notices,
    }
    if prior_pr_rejected:
        facts["prior_pr_rejected"] = prior_pr_rejected
    if issue_type == "epic":
        facts["epic"] = epic_facts
    elif epic_facts is not None:
        # `story` is the key `story.md` reads for the parent + its branch facts, and an untyped
        # target under an open epic runs that same playbook — so it publishes under the same key.
        # Present-but-null-inside now means "asked, no parent"; the key is absent only when no
        # lookup ran, which is the "never asked" the #31 receipt could not distinguish.
        facts["story"] = epic_facts

    if work_workspace_envelope is not None:
        # v3: the OBSERVED ambient checkout (attach), not a constructed worktree. `base_ref` is a
        # DERIVED fact (continue -> the audit-ref formula; story -> the epic branch; else main) —
        # attach cannot observe a base, and the create-pr step's `--base` slot consumes this.
        facts["workspace"] = {
            "path": work_workspace_envelope["path"],
            "branch": work_workspace_envelope["branch"],
            "expected_branch": work_workspace_envelope.get("expected_branch"),
            "base_ref": work_base,
            "sha": work_workspace_envelope.get("sha"),
            "dirty": work_workspace_envelope.get("dirty"),
            "unpushed_commits": work_workspace_envelope.get("unpushed_commits"),
            "upstream_sha": work_workspace_envelope.get("upstream_sha"),
            "source": "ambient",
            "setup": work_workspace_envelope.get("setup"),
        }
    # The non-final review's diff base — probed in the asserted work workspace (the only checkout
    # whose HEAD is the branch under review); None-workspace routes get the unreachable fallback.
    tracker["last_shipped"] = (
        last_shipped_phase(
            tracker.get("rows"),
            work_workspace_envelope["path"] if work_workspace_envelope is not None else None,
        )
        if len(phases or []) > 1
        else None
    )
    if read_workspace_envelope is not None:
        facts["read_workspaces"] = {
            "audit": {
                "path": read_workspace_envelope["path"],
                "ref": read_workspace_envelope["ref"],
                "sha": read_workspace_envelope.get("sha"),
            }
        }

    sections = {}
    for key, value in issue_envelope.items():
        if key.startswith(("issue_body", "thread", "marker_comment")):
            sections[key] = value
    facts["sections"] = sections

    return facts


def _extract_plan_sha(plan_body):
    """Extract the short/long SHA from the plan's header line: '... planned <ISO> at
    `<plan-ref>@<short-sha>`' (skills/planner/references/plan-schema.md). Returns
    `None` if the header doesn't match the documented shape rather than guessing."""
    match = re.search(r"@`?([0-9a-f]{7,40})`?", plan_body or "")
    return match.group(1) if match else None


def _build_distiller_bundle(issue_envelope, scratch_dir, issue_number):
    """Stage the state-distiller's three inputs (docs/specs/resolver.md §P6: issue body, full
    comment thread, plan marker body) as PATHS, always — never above-threshold inline bytes (this
    step's DoD box). Reuses `gh_gather`'s own spill files when a section already spilled there
    rather than re-writing a copy; an inline (small) section is written out once here so the
    bundle's contract ("staged paths, always") holds regardless of the section's own threshold
    outcome.
    """

    def _stage(mode_key, path_key, bare_key, filename):
        mode = issue_envelope.get(mode_key)
        if mode == "path":
            return issue_envelope.get(path_key)
        content = issue_envelope.get(bare_key)
        if content is None:
            return None
        target = Path(scratch_dir) / filename
        target.write_text(content, encoding="utf-8")
        return str(target)

    return {
        "issue_body_path": _stage(
            "issue_body_mode", "issue_body_path", "issue_body", "issue-%s-body.md" % issue_number
        ),
        "thread_path": _stage(
            "thread_mode", "thread_path", "thread", "issue-%s-thread.json" % issue_number
        ),
        "plan_marker_path": _stage(
            "marker_comment_mode",
            "marker_comment_path",
            "marker_comment_body",
            "issue-%s-marker.md" % issue_number,
        )
        if issue_envelope.get("marker_comment_present")
        else None,
    }


def _fetch_current_user(cwd):
    result = process.run(["gh", "api", "user"], cwd=cwd)
    if result.auth_required:
        from pipelib.decisions import AUTH_REQUIRED

        return None, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    return json.loads(result.stdout).get("login"), None


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue", help="issue number")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--root", default=".", help="project root (architecture.md §6 vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for spilled sections (default: /tmp/gh-resolver-<issue>)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-derive volatile facts (issue/PR state, branch discovery) without re-running hooks",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="explicit working directory for the gh calls that have no --repo scoping of their "
        "own (current-user, parent-epic search, closed-PR search, per-OQ tracker reads); optional "
        "in normal use (those calls don't depend on cwd), provided for test-injection — mirrors "
        "prep_evaluator.py's / gh_pr_gather.py's identical --cwd knob",
    )
    args = parser.parse_args(argv)

    facts = build_facts(
        args.issue,
        args.repo,
        root=args.root,
        scratch_dir=args.scratch_dir,
        refresh=args.refresh,
        cwd=args.cwd,
    )
    if facts is None:
        return EXIT_OK
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
