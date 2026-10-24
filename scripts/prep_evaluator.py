#!/usr/bin/env python3
"""prep_evaluator.py — the evaluator's complete facts block in one call (architecture.md §4;
docs/implementation.md S6). This is the **first prep script** — it pioneers the "compose the
executors in-process, one facts block" pattern S8 locks for every later prep script.

From-scratch v2 script (architecture.md §2/§9.2, docs/specs/evaluator.md): assembles the
evaluator's entire starting state — PR facts, the closing issue's DoD, gate config and the doc
catalogue read at the PR-head worktree, the CI rollup class, the health-cache hit/miss, PR-type detection, repo merge
config, and the self-review fact — as ONE JSON envelope on stdout, so the evaluator session's
state assembly is one Python process, never a subprocess chain and never more than one
model-mediated round-trip (prd.md §9.2).

Composition (architecture.md §2 "compose the executors in-process"; architecture.md §1 "the only
external processes any script may spawn are git/gh")::

    gh_pr_gather.build_pr_facts(...)      -- PR facts + thread + the health-cache marker comment
    gh_gather.run(..., stream=)           -- the closing issue's body + thread (per issue)
    workspace._build_attach(...)          -- assert the AMBIENT checkout IS the PR-head worktree
                                             (v3: the operator opened it via workspace-open and
                                             started this session inside it; ambient HEAD must
                                             equal pr.headRefOid — never health-check or cache
                                             against code the checkout doesn't contain)
    parse.parse_dod_bullets(...)          -- the closing issue's ## Definition of done, parsed
    config_block.read_block_anywhere
                                          -- the four gate-config blocks, read from the asserted
                                             PR-head worktree's WORKING TREE (the origin/main pin
                                             that once kept a PR from weakening its own gates is
                                             retired — architecture.md §6 rule 1)
    doc_catalogue.read_catalogue          -- the consuming repo's `<!-- doc-catalogue -->`
                                             grounding docs, read at the same PR-head worktree:
                                             a PR that legitimately edits a doc (or its catalogue
                                             entry) is judged against its own post-edit copy, and
                                             — the same knowing trade as gate config — a PR could
                                             also downgrade an entry's `binding` authority
    _fetch_base_compare(...)              -- one `gh api .../compare/<base>...<head>` read: does
                                             the head contain the base tip? With `--gated-head`
                                             it becomes the `merge_guard` fact (#93) — the merge
                                             lands only the gated head, and only on a base
                                             current at the last refresh
    sensitive_paths.changed_paths / match
                                          -- the `<!-- pr-evaluator-sensitive-paths -->` read
                                             (#96), on the full run with an open PR, at the
                                             gated head: the PR's changed paths against its merge
                                             base, matched against the head's declaration (a
                                             change to the declaration itself always matches).
                                             `config.sensitive_paths.forced_ask` makes S7 `ask`
                                             whatever the type's policy; a failed read forces
                                             `ask` too, never a hard exit or a decision

Every executor exposes a **pure, non-emitting core** — ``build_*(...) -> (payload, notices,
decision|None)`` — as of the S8 pattern lock (docs/specs/baseline.md §5): ``gh_pr_gather``,
``workspace`` (per subcommand), and ``config_block`` were retrofitted alongside the already-pure
``parse.py`` / ``gh_gather.run(stream=)`` references. This prep composes those cores **directly**
and acts on each core's returned ``decision`` channel (:func:`_forward_decision`),
emitting exactly one envelope of its own. The S6 pilot's ``contextlib.redirect_stdout`` capture
bridge — needed only because those executors used to emit-and-exit with no returnable core — is
**retired**: no prep captures another script's stdout, and no later prep may reintroduce it (§5
retro's rule for S9+).

Usage::

    prep_evaluator.py <pr-number> <owner/repo> [--root PATH] [--scratch-dir DIR] [--refresh]
                      [--gated-head <40-char SHA>]

``--root`` defaults to ``.`` and is normalized to the MAIN checkout via
``workspace._resolve_main_root`` — under v3 the session (and therefore the ambient cwd) sits
INSIDE the PR-head worktree. ``--scratch-dir`` defaults to ``/tmp/gh-evaluator-<pr-number>``
(CLAUDE.md's ``/tmp/gh-<skill>-<N>/`` convention) when omitted. ``--refresh`` re-derives the
volatile facts (PR state, CI rollup class, health-cache hit/miss, ``merge_guard``) without
re-asserting the workspace, re-running the setup hooks, or re-reading the gate config —
architecture.md §4: "prep supports --refresh and is re-run at the points where currency matters."
``--gated-head`` is the opening run's ``pr.headRefOid``, passed back on every later refresh so
``merge_guard.head_moved`` says whether the head changed since it was gated; absent, the gated
head is this run's own head (never pass a refresh's head — that would disable the pin).

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"``
or ``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified
hard `gh`/`git` failure surfaced by a composed executor — stderr carries the faithful error.
"""

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402  (the `<N>-<slug>` branch grammar — a story PR's story)
import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import doc_catalogue  # noqa: E402  (the consuming repo's declared grounding docs)
import gh_gather  # noqa: E402
import gh_pr_gather  # noqa: E402
import parse  # noqa: E402
import plan_parts  # noqa: E402  (the plan's part comments — skills/_shared/plan-parts.md)
import plan_shipped  # noqa: E402  (the shipped-phase records a relocated plan's detail lives in)
import sensitive_paths  # noqa: E402  (the sensitive-path declaration and its one matcher, #96)
import story_set  # noqa: E402  (the epic story set's two-tier read)
import workspace  # noqa: E402
from pipelib import process  # noqa: E402
from pipelib.decisions import AMBIGUOUS, DOC_CATALOGUE_ABSENT, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_needs_decision, emit_ok  # noqa: E402
from pipelib.spill import read_section, resolve_threshold, spill_bytes  # noqa: E402
from pipelib.thread import load_thread  # noqa: E402

import delivery_log  # noqa: E402  (in-process composition, not a subprocess chain)

# Health-cache marker comment prefix (docs/specs/evaluator.md "Artifacts written") — self-read by
# gh_pr_gather's marker-comment lookup so the health check needs no second fetch.
HEALTH_CACHE_MARKER = "<!-- pr-evaluator-health-cache:v1 -->"

# The closing issue's plan (skills/planner/references/plan-schema.md), for the S3 "Plan adherence"
# judgment. Located by a thread scan like its shipped-phase records, never a `marker_prefix` gather:
# a duplicate must degrade to a notice here, not stop the merge (see `_build_plan_facts`).
PLAN_MARKER = "<!-- implementation-plan:v1 -->"

# The epic delivery log (skills/_shared/epic-delivery-log.md) — the evaluator is its SOLE writer.
# The markers, tier resolution and `log_source` classification live in `delivery_log.py`, shared
# with prep_planner so the writer's view and the reader's cannot drift (#41). The ids this prep
# hands the story route are REST NUMERIC ids, never the normalized thread's GraphQL node id, which
# every REST comment endpoint 404s on (#34: the playbook used to fetch the comment itself, landed
# in node-id space, so the delete silently failed and the log accumulated duplicates).

# The four gate-config marker names (docs/specs/evaluator.md), read at the root `main` SHA
# (architecture.md §6/§12: "gate config is pinned to trust ... never from a PR head").
_STATIC_CHECKS_MARKER = "pr-evaluator-static-checks"
_TEST_TARGET_MARKER = "pr-evaluator-test-target"
_ESCALATION_LABELS_MARKER = "pr-evaluator-escalation-labels"
_MERGE_POLICY_MARKER = "pr-evaluator-merge-policy"
# Legacy single-block backward-compat marker (docs/specs/evaluator.md "Known bugs/gaps",
# docs/specs/evaluator.md "Legacy config: health checks") — read alongside static-checks/test-target
# so prep surfaces both arms and lets
# the evaluator playbook (not this script) decide which to honor.
_LEGACY_HEALTH_CHECKS_MARKER = "pr-evaluator-health-checks"

# Candidate config files, in priority order — same discovery list workspace.py's
# `_candidate_hook_files` already uses for the worktree-setup/teardown blocks (COMMANDS.md,
# CLAUDE.md, then one level of `@`-include from either) — the docs/specs/evaluator.md config
# blocks are discovered identically ("Scan COMMANDS.md and CLAUDE.md ... and any file either
# @-includes"). `config_block.read_block_anywhere` (S12 promotion) defaults to this exact tuple, so
# this module no longer needs its own copy of the discovery loop — see the "Gate-config discovery"
# section below.

# CI rollup classification terminal-state vocabulary (docs/specs/evaluator.md §5.3, preserved
# verbatim from v1's classification rule).
_CI_RUNNING_CHECK_STATUSES = frozenset({"QUEUED", "IN_PROGRESS"})
_CI_RUNNING_CONTEXT_STATE = "PENDING"
_CI_SUCCESS_EQUIVALENT = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
_CI_FAILURE_EQUIVALENT = frozenset(
    {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
)

# PR-type detection patterns (docs/specs/evaluator.md "PR-type classification"):
# `epic-integration` if headRefName matches `epic/<N>-<slug>` AND baseRefName == main; `story` if
# baseRefName matches `epic/<N>-<slug>`; `standard` otherwise (v1 names this bucket `regular`; the
# v2 architecture.md §4 vocabulary and this step's brief both name it `standard`).
_EPIC_BRANCH_RE = re.compile(r"^epic/\d+-.+$")

# Same pattern with the epic number captured — a story PR names its epic in its own base ref, so the
# epic is derivable without a search.
_EPIC_BRANCH_NUMBER_RE = re.compile(r"^epic/(\d+)-.+$")



# ---------------------------------------------------------------------------
# In-process composition (architecture.md §2 "compose the executors in-process"; §1: only git/gh
# may be spawned as external processes — a `python3 <executor>.py` subprocess would be an unlisted
# third spawned binary). As of the S8 pattern lock (docs/specs/baseline.md §5) every executor
# exposes a pure, non-emitting `build_*(...) -> (payload, notices, decision|None)` core, so this
# prep calls those cores DIRECTLY and forwards a returned decision to its own single envelope —
# the S6 `redirect_stdout`/`io.StringIO` capture bridge is retired.
# ---------------------------------------------------------------------------


class _DiscardStream:
    """A minimal write-sink for ``gh_gather.run(stream=...)`` — the one remaining executor prep
    composes that emits-through-a-``stream=`` (its accepted-reference shape, S8 retro §5) while
    ALSO returning ``(exit, envelope)``. prep consumes only the returned envelope, so the emit is
    routed here and discarded rather than captured into a buffer — no ``io.StringIO``, no
    ``redirect_stdout``. Implements just the ``write``/``flush`` surface
    :func:`pipelib.envelope.emit` touches on a stream without a ``.buffer`` attribute."""

    def write(self, _data):
        return None

    def flush(self):
        return None


def _forward_decision(decision, notices=None):
    """Emit a composed core's returned ``decision`` (a ``pipelib.decisions`` code dict) AS-IS on
    prep's own stdout — this is prep's one and only envelope for the run — and return ``True`` when
    a decision was present, else ``False`` so the caller continues assembling facts.

    This is how ``MARKER_AMBIGUOUS`` (gh_pr_gather / gh_gather), ``AUTH_REQUIRED`` (any gh call),
    ``WORKSPACE_MISMATCH``/``BRANCH_IN_USE`` (workspace.py), and
    ``DOD_MALFORMED`` (parse.py, raised in-process — see :func:`_parse_closing_issue_dod`)
    propagate through prep untouched, per architecture.md §3's closed decision-code set: a composed
    core's decision IS prep's decision, verbatim, never re-derived or reworded.
    """
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


# ---------------------------------------------------------------------------
# Gate-config discovery at the root `main` SHA (architecture.md §6/§12 "gate config is pinned to
# trust"). `--root` IS already the root main checkout (architecture.md §6: "project root ...
# always clean main"); prep reads these blocks directly from the root working tree rather than
# from any PR-head copy, and records the root SHA the read was pinned at.
#
# The multi-file candidate-discovery loop itself (COMMANDS.md, CLAUDE.md, one-level `@`-include,
# first well-formed block wins) is `config_block.read_block_anywhere` (S12 promotion) — this module
# used to carry its own copy (byte-identical to prep_resolver.py's), duplicated a third time by
# prep_planner.py; both preps now compose the shared, tested helper directly (architecture.md §2's
# in-process composition) instead.
# ---------------------------------------------------------------------------

_COMMAND_LIST_ITEM_RE = re.compile(r"^\s*-\s*`([^`]*)`")
_MERGE_POLICY_LINE_RE = re.compile(r"^\s*-\s*([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(ask|auto)\s*$")


def _parse_backtick_command_list(interior_lines):
    """One backtick-quoted command per Markdown list item — the shared shape
    ``pr-evaluator-static-checks``, ``pr-evaluator-escalation-labels``, and the legacy
    ``pr-evaluator-health-checks`` block all use (docs/specs/evaluator.md "Legacy config: health
    checks"), the
    exact same per-line grammar ``workspace.py``'s ``_parse_command_list`` already applies to
    worktree-hook blocks — restated locally so this module has no cross-module dependency on a
    workspace.py private helper for an unrelated block family."""
    items = []
    for line in interior_lines:
        match = _COMMAND_LIST_ITEM_RE.match(line)
        if match:
            items.append(match.group(1))
    return items


def _parse_merge_policy(interior_lines):
    """``- <pr-type>: ask|auto`` per line (docs/specs/evaluator.md "Config: merge policy"). Returns a
    dict keyed by PR type. Unparseable lines (blank, prose) are skipped, not fatal — the merge
    policy block has no closed decision-code the way ``dod``/``phases`` do (architecture.md §3
    names no MERGE_POLICY_MALFORMED code), so a stray non-conforming line is simply not a policy
    entry, matching v1's own tolerant Markdown-list reading rather than a strict grammar.
    """
    policy = {}
    for line in interior_lines:
        match = _MERGE_POLICY_LINE_RE.match(line)
        if match:
            policy[match.group(1)] = match.group(2)
    return policy


def _read_gate_config(gate_root):
    """Read all four gate-config blocks (plus the legacy single-block fallback) once, from
    ``gate_root``'s working tree — the asserted PR-head worktree, uncommitted edits included. Until
    v3.x these were ``origin/main`` blob reads at a pin, so a PR could not set the gates that judge
    it; the operator's checkout is now the trusted source by decision, and ``config.source``
    reports which checkout/branch/SHA answered. Returns the ``config`` facts-block sub-object
    (without ``source``, added by the caller) plus a ``notices`` list for
    graceful-degradation cases the evaluator playbook should know about (e.g. "legacy block
    only") — mirroring architecture.md §3's ``notices: []`` channel for non-blocking
    degradations. Never itself asks the operator anything (gates-only-for-decisions,
    architecture.md §9); an absent test-target or static-checks block is a fact for the
    evaluator playbook to gate on, not a prep-time decision.
    """
    notices = []

    static_present, static_lines, static_source = config_block.read_block_anywhere(gate_root, _STATIC_CHECKS_MARKER)
    test_target_present, test_target_lines, test_target_source = config_block.read_block_anywhere(gate_root, _TEST_TARGET_MARKER)
    escalation_present, escalation_lines, _escalation_source = config_block.read_block_anywhere(gate_root, _ESCALATION_LABELS_MARKER)
    merge_policy_present, merge_policy_lines, _merge_policy_source = config_block.read_block_anywhere(gate_root, _MERGE_POLICY_MARKER)
    legacy_present, legacy_lines, legacy_source = config_block.read_block_anywhere(gate_root, _LEGACY_HEALTH_CHECKS_MARKER)

    if not static_present and legacy_present:
        # Backward-compat: treat the legacy flat list as the static-checks list and note the
        # degradation (docs/specs/evaluator.md "Known bugs/gaps": "legacy single-block path
        # always runs the full command list") — the evaluator playbook, not prep, decides whether
        # to skip test-selection on this arm; prep only surfaces the fact + notice.
        notices.append("LEGACY_HEALTH_CHECKS_BLOCK")

    static_checks = _parse_backtick_command_list(static_lines) if static_present else (
        _parse_backtick_command_list(legacy_lines) if legacy_present else []
    )
    escalation_labels = _parse_backtick_command_list(escalation_lines) if escalation_present else []
    merge_policy = _parse_merge_policy(merge_policy_lines) if merge_policy_present else {}
    # epic-integration is never configurable — always "ask" regardless of block content
    # (docs/specs/evaluator.md: "Epic-integration merges are always gated ... hardcoded, not
    # configurable"). Set unconditionally, after parsing, so a stray `epic-integration:` line in
    # the block can never override it.
    merge_policy["epic-integration"] = "ask"

    config = {
        "static_checks": static_checks,
        "static_checks_present": static_present,
        "test_target_present": test_target_present,
        "test_target_raw": "\n".join(test_target_lines) if test_target_present else None,
        "test_target_source": test_target_source,
        "escalation_labels": escalation_labels,
        "merge_policy": merge_policy,
        "merge_policy_present": merge_policy_present,
        "legacy_health_checks_present": legacy_present,
        "legacy_health_checks_source": legacy_source,
    }
    return config, notices


# ---------------------------------------------------------------------------
# CI rollup classification (docs/specs/evaluator.md §5.3) — green / red / pending / none.
# ---------------------------------------------------------------------------


def _classify_ci_rollup(status_check_rollup):
    """Classify ``statusCheckRollup`` (the raw list from gh_pr_gather's PR fetch) into exactly one
    of ``green`` / ``red`` / ``pending`` / ``none``, plus the failing-check-name list when red.
    Preserves v1's exact terminal-state vocabulary (docs/specs/evaluator.md §5.3) — this is a
    CLASSIFICATION only; prep never blocks/watches a pending rollup to settle it (v1's own rule:
    "Do NOT route this through the executor sub-agent ... run it from the main loop" — the v2 analogue is
    "not synchronously inside a one-shot prep script"; the evaluator playbook is what decides
    whether/how to wait, using this fact as its starting point).
    """
    if not status_check_rollup:
        return "none", []

    has_running = False
    has_failing = False
    fail_checks = []
    for entry in status_check_rollup:
        status = entry.get("status")
        conclusion = entry.get("conclusion")
        state = entry.get("state")  # status-context shape uses `state` instead of `status`/`conclusion`
        if (status is not None and status in _CI_RUNNING_CHECK_STATUSES) or (
            state == _CI_RUNNING_CONTEXT_STATE
        ) or (status == "COMPLETED" and conclusion is None and state is None):
            has_running = True
            continue
        terminal_value = conclusion if conclusion is not None else state
        if terminal_value in _CI_FAILURE_EQUIVALENT:
            has_failing = True
            fail_checks.append(entry.get("name") or entry.get("context") or "(unnamed check)")
        elif terminal_value in _CI_SUCCESS_EQUIVALENT:
            continue
        else:
            # An unrecognized terminal value defaults to "still settling" rather than silently
            # counting as green or red — matching the spirit of v1's exhaustive enumeration
            # (docs/specs/evaluator.md §5.3 names every value explicitly; an unnamed value is
            # never assumed success-equivalent).
            has_running = True

    if has_running:
        return "pending", []
    if has_failing:
        return "red", fail_checks
    return "green", []


# ---------------------------------------------------------------------------
# PR-type detection (docs/specs/evaluator.md "PR-type classification").
# ---------------------------------------------------------------------------


def _detect_pr_type(base_ref_name, head_ref_name, root_branch):
    if _EPIC_BRANCH_RE.match(head_ref_name or "") and base_ref_name == root_branch:
        return "epic-integration"
    if _EPIC_BRANCH_RE.match(base_ref_name or ""):
        return "story"
    return "standard"


# ---------------------------------------------------------------------------
# Parent-epic story set for a story PR (skills/_shared/epic-story-hierarchy.md).
# ---------------------------------------------------------------------------


# The thread read-back lives in `pipelib.thread` (three preps scan threads; one home).
_load_thread = load_thread


def _build_plan_facts(issue_envelope, scratch_dir, issue_number, pr_number):
    """``facts.plans[<issue>]`` — the closing issue's whole plan (the main comment with its part
    comments put back, skills/_shared/plan-parts.md) plus any legacy shipped-phase records
    (skills/_shared/plan-shipped-phases.md). Plan adherence (evaluate-spine.md S3 step 5) judges the
    whole PR diff, and a plan revised before 4.35.0 may still hold a shipped phase's `## Changes` /
    `## Data model / schema impact` / `## Test plan` entries in its record — so the evaluator needs
    both, as paths, always.

    Both are located by scanning the thread the gather already returned — never a ``marker_prefix``
    gather, whose second match is a hard ``MARKER_AMBIGUOUS``. Adherence is one judgment among the
    merge gates, so a duplicated plan or record DEGRADES to a notice rather than stopping the run —
    the delivery log's posture in this same prep, for the same reason.

    Records are read for the PR the plan's pointer bullets name (normally this PR; after a
    Start-fresh the closed PR's records are still the only copy of their entries).

    Returns ``(facts, notices)``.
    """
    thread = _load_thread(issue_envelope)
    plans = [c for c in thread if (c.get("body") or "").startswith(PLAN_MARKER)]
    if not plans:
        return {"present": False}, []
    if len(plans) > 1:
        return {
            "present": True,
            "ambiguous": True,
            "comment_ids": [plan_shipped.comment_rest_id(c) for c in plans],
            "comment_urls": [c.get("url") for c in plans],
        }, [
            "closing issue #%s carries %d plan comments — plan adherence cannot pick one; judge "
            "without it and say so in the review" % (issue_number, len(plans))
        ]
    notices = []
    # The whole plan: the main comment with its part comments put back (skills/_shared/plan-parts.md).
    # A duplicated part degrades to a notice, as a duplicated plan does — adherence is one judgment
    # among the merge gates, never a stop.
    plan_body, parts, part_notes, part_decision = plan_parts.assemble_from_thread(
        plans[0].get("body") or "", thread
    )
    notices += ["closing issue #%s: %s" % (issue_number, note) for note in part_notes]
    if part_decision is not None:
        notices.append(
            "closing issue #%s: %s — plan adherence reads the plan without that part; judge the "
            "sections it carries without the plan and say so" % (issue_number, part_decision["summary"])
        )
    records, notes, decision = plan_shipped.read_pointed(thread, plan_body, pr_number)
    notices += ["closing issue #%s: %s" % (issue_number, note) for note in notes]
    if decision is not None:
        notices.append(
            "%s — plan adherence cannot read phase(s) %s from a record; judge them against the "
            "plan comment alone and say so (skills/_shared/plan-shipped-phases.md)"
            % (decision["summary"], ", ".join(str(p) for p in records["duplicated_phases"]))
        )
    shipped = plan_shipped.staged_fact(records, scratch_dir, "issue-%s-plan-shipped.md" % issue_number)
    return {
        "present": True,
        "comment_id": plan_shipped.comment_rest_id(plans[0]),
        "comment_url": plans[0].get("url"),
        "body_path": spill_bytes(
            plan_body.encode("utf-8"),
            "body",
            scratch_dir,
            force_path=True,
            filename="issue-%s-marker.md" % issue_number,
        )["body_path"],
        "parts": parts,
        "shipped": shipped,
    }, notices


def _build_delivery_log_facts(envelope, scratch_dir, epic_number, story_numbers):
    """``facts.epic.delivery_log`` — the log resolved across both tiers, plus THIS story's record.

    Returns ``(facts, notices)``. Tier resolution, ordering and ``log_source`` live in
    ``delivery_log.py``, shared with ``prep_planner`` so the classification cannot drift between the
    writer's prep and the reader's.

    ``present`` keeps its established meaning — *the epic has a log* — and is deliberately NOT
    redefined as "this story has an entry". Silently repurposing it is how a re-record ends up
    writing a second record for a story already logged; the story-scoped answer is its own key.
    """
    thread = _load_thread(envelope)
    log, decision = delivery_log.collect(thread)

    if decision is not None:
        # A duplicate — the #34 wreckage itself, or a hand-posted second copy. NOT forwarded as a
        # decision: the hierarchy is never a gate, and a merge already judged on its own gates must
        # not stall on its log. Under per-story comments this no longer blocks the whole epic: the
        # resolved half still comes back from `collect`, so a duplicate on story A leaves story B
        # recordable, and `duplicated_stories` says which stories are actually affected.
        context = decision.get("context") or {}
        ids = context.get("comment_ids") or []
        scope = (
            "name story #%s" % context["story"] if context.get("story") else "match the legacy marker"
        )
        return (
            {
                "present": True,
                "ambiguous": True,
                "comment_ids": ids,
                "comment_urls": context.get("comment_urls") or [],
            },
            [
                "%d epic-delivery-log comments on #%s %s — expected one; this story's entry was "
                "not recorded (skills/_shared/epic-delivery-log.md)" % (len(ids), epic_number, scope)
            ],
        )

    if not log["present"]:
        # Exactly the two keys, so an absent log stays a two-key fact: there is no tier to name and
        # no record to point at, and `present: False` already says so.
        return {"present": False, "ambiguous": False}, []

    facts = {
        "present": True,
        "ambiguous": False,
        "log_source": log["log_source"],
        "entry_count": log["entry_count"],
        # Every resolved record, always — not just the one for `story_number`. When the closing-issue
        # set cannot name a single story (below), this map is the only way the write can still find an
        # existing record instead of defaulting to a create and posting a duplicate. The old
        # single-comment design was idempotent without knowing the story number, because the whole
        # body was staged; per-story writes have to be handed the same reach.
        "entries": [
            {
                "story": e["story"],
                "tier": e["tier"],
                "comment_id": e.get("comment_id"),
                "comment_url": e.get("comment_url"),
            }
            for e in log["entries"]
        ],
    }
    if log["duplicated_stories"]:
        facts["duplicated_stories"] = log["duplicated_stories"]

    notices = []
    # Which story this write targets. A story PR normally closes exactly one issue; zero (the
    # `Plan override` / unlinked-PR case) and more than one are both legitimate, and neither yields
    # a single record to update — so say so rather than guessing which sibling to overwrite.
    if len(story_numbers) == 1:
        story_number = story_numbers[0]
        facts["story_number"] = story_number
        tier, record = delivery_log.record_for_story(log, story_number)
        facts["story_recorded_in"] = tier
        if tier == delivery_log.SOURCE_ENTRIES:
            facts["entry"] = {
                "comment_id": record["comment_id"],
                "comment_url": record["comment_url"],
            }
    else:
        facts["story_number"] = None
        facts["story_recorded_in"] = None
        notices.append(
            "this PR closes %d issues, so the delivery-log entry's story is not derivable from the "
            "PR — Action 3 resolves it against `delivery_log.entries` and names the story it "
            "recorded (skills/_shared/epic-delivery-log.md)" % len(story_numbers)
        )

    if log["legacy"] is not None:
        # The legacy body is staged because updating a story's line *inside* it is one of Action 3's
        # arms — the tier a story is already recorded in is the tier that keeps it.
        facts["legacy"] = {
            "comment_id": log["legacy"]["comment_id"],
            "comment_url": log["legacy"]["comment_url"],
        }
        facts["legacy"].update(
            spill_bytes(
                log["legacy"]["body"].encode("utf-8"),
                "body",
                scratch_dir,
                filename="epic-%s-delivery-log-legacy.md" % epic_number,
            )
        )
    return facts, notices


def _build_epic_facts(base_ref_name, repo, scratch_dir, story_numbers=()):
    """``facts.epic`` for a story PR: the parent epic plus its story set and progress.

    The epic number comes from the PR's own base ref (``epic/<N>-<slug>``) — no search needed. The
    story set resolves per ``skills/_shared/epic-story-hierarchy.md`` and ``stories_source`` reports
    which source answered (``sub-issues`` / ``checklist`` / ``mixed``), because that decides whether
    the story route has a checkbox to project onto the epic body at all (native: none — GitHub
    recomputes the rollup from issue state) and where its "do siblings remain" routing reads from.
    A ``mixed`` epic is unioned, never halved — dropping either half would under-count siblings and
    could route a merge to "last sibling closed" while stories remain open.

    The delivery log (``skills/_shared/epic-delivery-log.md``) rides on the SAME gather as
    ``delivery_log``: where this story is already recorded and under which **numeric REST id** (the
    only id space ``gh_persist.py edit-comment`` and ``comment --delete-marker-id`` can address), so
    Action 3 updates the existing record instead of fetching it itself and passing a node id that
    always 404s (#34).

    It is read by scanning the gather's own already-paginated thread, not through a
    ``marker_prefix``. Two reasons, both structural (#41): a per-story log is a SET of comments and
    ``marker_prefix`` is a single-match lookup that calls more than one hit ``MARKER_AMBIGUOUS``;
    and an exact per-story lookup would need a story number this function does not have — it is
    built from the base ref alone, and a PR's closing-issue set can be empty (the ``Plan override``
    case) or plural. Scanning also keeps the LEGACY tier visible, which is what stops a re-record
    writing a second, divergent record for a story already recorded there. The thread is in the
    envelope either way, so this costs zero extra ``gh`` calls — and removes one, since the old
    ambiguity path re-gathered the whole issue a second time.

    Returns ``(epic_facts_or_None, notices)``. A gather failure here is **non-fatal**: this is
    display/routing context for a merge that has already been judged on its own gates, so it must
    never take down the session (the hierarchy is deliberately never a gate).
    """
    match = _EPIC_BRANCH_NUMBER_RE.match(base_ref_name or "")
    if match is None:
        return None, []
    epic_number = int(match.group(1))

    envelope = _gather_epic(epic_number, repo, scratch_dir)
    if envelope is None:
        return _unavailable_story_set(epic_number), []

    # Name it `delivery_log_facts`, never `delivery_log`: a local of that name shadows the imported
    # module for this whole scope, so a later `delivery_log.collect(...)` here would raise
    # UnboundLocalError rather than resolve the import.
    delivery_log_facts, log_notices = _build_delivery_log_facts(
        envelope, scratch_dir, epic_number, story_numbers
    )
    facts = _story_set_facts(epic_number, envelope)
    facts["delivery_log"] = delivery_log_facts
    return facts, list(log_notices) + list(envelope.get("notices") or [])


def _gather_epic(epic_number, repo, scratch_dir, envelope=None):
    """The epic's gather envelope — ``envelope`` when the caller already holds it (the integration
    route's closing issue IS the epic), else one ``gh_gather`` run. ``None`` on a failed gather: the
    epic facts are routing/judgment context, never a gate, so the caller degrades rather than exits."""
    if envelope is not None:
        return envelope
    exit_code, envelope = gh_gather.run(
        str(epic_number),
        repo,
        scratch_dir=scratch_dir,
        stream=_DiscardStream(),
    )
    if exit_code != 0 or envelope is None or envelope.get("status") != "ok":
        return None
    return envelope


def _unavailable_story_set(epic_number):
    return {
        "number": epic_number,
        "stories_source": "unavailable",
        "stories": [],
        "sub_issues": [],
        "sub_issues_summary": {},
        "subissues_available": None,
    }


def _story_set_facts(epic_number, envelope):
    """The epic's story set from its gather envelope, per ``skills/_shared/epic-story-hierarchy.md``'s
    two-tier read — shared by the story route and the epic-integration route. `stories_source` reads
    the checklist half as every filed bullet, and an epic with no stories as `checklist`."""
    sub_issues = envelope.get("sub_issues") or []
    epic_body = envelope.get("issue_body")
    if epic_body is None and envelope.get("issue_body_mode") == "path":
        epic_body = Path(envelope["issue_body_path"]).read_text(encoding="utf-8")
    read, _decision = story_set.read(sub_issues, epic_body or "")
    stories = story_set.closed_view(read)
    stories_source = (
        story_set.source(read["native"], story_set.filed(read["checklist_raw"]))
        or story_set.SOURCE_CHECKLIST
    )

    return {
        "number": epic_number,
        "title": envelope.get("title"),
        "state": envelope.get("state"),
        "stories_source": stories_source,
        "stories": stories,
        "sub_issues": sub_issues,
        "sub_issues_summary": envelope.get("sub_issues_summary") or {},
        "subissues_available": envelope.get("subissues_available"),
    }


# ---------------------------------------------------------------------------
# The epic's story residue, for an epic-integration PR.
# ---------------------------------------------------------------------------

# Non-blocking notice (open notice vocabulary, architecture.md §3): the merged-story-PR list could
# not be read — no auth, no network, an older `gh`. The integration verdict is judged without the
# stories' residue and says so; never a decision, since the merge is always operator-gated and the
# gap is visible on the card (the `attention` line names it).
EPIC_RESIDUE_UNAVAILABLE = "EPIC_RESIDUE_UNAVAILABLE"

# `gh pr list` defaults to 30 — an epic with more merged story PRs would silently lose the rest.
_STORY_PR_LIST_LIMIT = 500
_STORY_PR_FIELDS = "number,title,url,headRefName,body,closingIssuesReferences,mergedAt"
# Follow-up states are read in one GraphQL query per chunk of this many references, never one
# `gh issue view` each (an epic's follow-ups run to dozens). `issueOrPullRequest`, because a PR linked
# under `## Follow-ups` is not an issue: `gh issue view` fails on it, and an unreadable reference is
# judged open — a PR must read as a PR, never as an open deferral.
_FOLLOW_UP_QUERY_CHUNK = 50
_FOLLOW_UP_NODE = (
    "i%d: issueOrPullRequest(number: %d) { __typename "
    "... on Issue { number title state url } ... on PullRequest { number title state url } }"
)
# Ledger dispositions a merged story PR should not still carry: the story's own evaluation puts
# every `open` entry to the operator and soft-rejects on an unfixed `apply`, so either one on a
# MERGED story PR means it merged around that backstop.
_UNDECIDED_POLISH = frozenset({"open", "apply"})


def _story_for_pr(pr, story_numbers):
    """The story a merged story PR delivered: its closing-issue set first, the pipeline's own
    `<N>-<slug>` branch grammar second (a `Fixes #N` on a non-default base may not register as a
    closing reference). ``None`` when neither names a story in the epic's set."""
    for issue in pr.get("closingIssuesReferences") or []:
        if issue.get("number") in story_numbers:
            return issue["number"]
    head_match = branching.ISSUE_BRANCH_NAME_RE.match(pr.get("headRefName") or "")
    if head_match and int(head_match.group(1)) in story_numbers:
        return int(head_match.group(1))
    return None


def _follow_up_query(repo, numbers):
    owner, _, name = repo.partition("/")
    return "query { repository(owner: %s, name: %s) { %s } }" % (
        json.dumps(owner), json.dumps(name), " ".join(_FOLLOW_UP_NODE % (n, n) for n in numbers)
    )


def _read_follow_up_states(repo, numbers, cwd=None):
    """``{number: node_or_None}`` for every referenced follow-up — ``None`` when unreadable (no such
    number, or the query failed). One ``gh api graphql`` call per chunk. GraphQL reports a missing
    number as an error beside the data that did resolve, and ``gh`` then exits non-zero, so a
    non-zero exit still reads whatever data the response carries."""
    nodes = {}
    for i in range(0, len(numbers), _FOLLOW_UP_QUERY_CHUNK):
        chunk = numbers[i:i + _FOLLOW_UP_QUERY_CHUNK]
        result = process.run(
            ["gh", "api", "graphql", "-f", "query=" + _follow_up_query(repo, chunk)], cwd=cwd
        )
        try:
            repository = ((json.loads(result.stdout or "null") or {}).get("data") or {}).get(
                "repository"
            ) or {}
        except (ValueError, AttributeError):
            repository = {}
        for number in chunk:
            node = repository.get("i%d" % number)
            nodes[number] = node if isinstance(node, dict) else None
    return nodes


def _build_epic_residue(epic_branch, epic_number, stories, repo, scratch_dir, cwd=None):
    """``facts.epic.residue`` — what the epic's merged story PRs left behind: each one's `## Polish`
    entries (everything but `applied`) and the issues its `## Follow-ups` section filed
    (skills/_shared/follow-ups-section.md), plus the live state of every such follow-up. Read from
    the story PR bodies themselves (the ledgers' and weaves' single home), so it covers every epic
    retroactively and copies nothing.

    A `file` ledger entry a `Filed: … · covers <id>` line ties to its issue is read through that
    issue, once: listed under the follow-up's `covers`, never again in `file_polish`, and resolved
    when the issue closes or the slicer adopts it as a story (it then leaves the follow-ups, being in
    the story set). Only an unlinked `file` entry — a story PR merged before the contract — is listed
    on its own, for the evaluator to re-check at head.

    Returns ``(residue_or_None, notices, attention)``. Facts only: whether a deferral maps to an
    epic `## Definition of done` bullet is the evaluator's judgment (epic-integration.md).
    """
    result = process.run(
        [
            "gh", "pr", "list",
            "--repo", repo,
            "--base", epic_branch,
            "--state", "merged",
            "--limit", str(_STORY_PR_LIST_LIMIT),
            "--json", _STORY_PR_FIELDS,
        ],
        cwd=cwd,
    )
    prs = None
    if result.returncode == 0:
        try:
            prs = json.loads(result.stdout)
        except ValueError:
            prs = None
    if not isinstance(prs, list):
        return None, [EPIC_RESIDUE_UNAVAILABLE], [
            "the epic's merged story PRs could not be listed — story residue (deferred follow-ups, "
            "polish ledgers) is not evaluated; say so in the review"
        ]

    notices, attention = [], []
    if len(prs) >= _STORY_PR_LIST_LIMIT:
        notices.append(
            "%d merged PRs listed against %s — the list limit; older story PRs may be missing from "
            "the residue" % (len(prs), epic_branch)
        )
    story_numbers = {s["number"] for s in stories if s.get("number") is not None}
    pr_numbers = {pr.get("number") for pr in prs}
    # A story PR's `## Follow-ups` may name the epic, a sibling story or a sibling PR in passing —
    # none of those is a deferral. An adopted follow-up IS a story now, so it leaves here too.
    not_follow_ups = story_numbers | pr_numbers | {epic_number}

    story_prs, referenced, covers_by_issue = [], [], {}
    for pr in sorted(prs, key=lambda p: p.get("number") or 0):
        body = pr.get("body") or ""
        scan = parse.scan_follow_ups(body, repo)
        linked = {
            ledger_id: issue for issue, ids in scan["covers"].items() for ledger_id in ids
        }
        polish = []
        for entry in parse.scan_polish(body)["entries"]:
            if entry["disposition"] == "applied":
                continue
            entry = dict(entry)
            if entry["id"] in linked:
                entry["issue"] = linked[entry["id"]]
                covers_by_issue.setdefault(linked[entry["id"]], []).append(
                    {"pr": pr.get("number"), "id": entry["id"]}
                )
            polish.append(entry)
        follow_ups = [n for n in scan["issues"] if n not in not_follow_ups]
        for number in follow_ups:
            if number not in referenced:
                referenced.append(number)
        story_prs.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "url": pr.get("url"),
                "head": pr.get("headRefName"),
                "merged_at": pr.get("mergedAt"),
                "story": _story_for_pr(pr, story_numbers),
                "polish": polish,
                "follow_ups": follow_ups,
            }
        )

    nodes = _read_follow_up_states(repo, referenced, cwd=cwd)
    follow_up_facts, unreadable = [], []
    for number in referenced:
        node = nodes.get(number)
        if node is None:
            unreadable.append(number)
            node = {}
        typename = node.get("__typename")
        follow_up_facts.append(
            {
                "number": number,
                # `pull_request`: a PR linked under `## Follow-ups` — context, never a deferral.
                "kind": {"Issue": "issue", "PullRequest": "pull_request"}.get(typename),
                "title": node.get("title"),
                # `null` = unreadable: unknown, never "closed" — judge it as possibly open.
                "state": node.get("state"),
                "url": node.get("url"),
                "from_prs": [p["number"] for p in story_prs if number in p["follow_ups"]],
                "covers": covers_by_issue.get(number, []),
            }
        )
    if unreadable:
        notices.append(
            "follow-up reference(s) %s could not be read — their state is null (unknown, not closed)"
            % ", ".join("#%s" % n for n in unreadable)
        )

    undecided = [
        {"pr": p["number"], "id": e["id"], "disposition": e["disposition"]}
        for p in story_prs
        for e in p["polish"]
        if e["disposition"] in _UNDECIDED_POLISH
    ]
    if undecided:
        attention.append(
            "%d undecided polish entr%s on merged story PRs (%s) — each merged around its story's "
            "evaluation; judge it as a deferral"
            % (
                len(undecided),
                "y" if len(undecided) == 1 else "ies",
                ", ".join("#%s %s" % (u["pr"], u["id"]) for u in undecided),
            )
        )

    residue = {
        "story_pr_count": len(story_prs),
        "follow_ups": follow_up_facts,
        "open_follow_ups": [
            f["number"]
            for f in follow_up_facts
            if f["kind"] != "pull_request" and (f["state"] or "OPEN").upper() == "OPEN"
        ],
        # Only `file` entries no `Filed:` line covers: a covered one is its issue's deferral.
        "file_polish": [
            {"pr": p["number"], "id": e["id"]}
            for p in story_prs
            for e in p["polish"]
            if e["disposition"] == "file" and "issue" not in e
        ],
        "undecided_polish": undecided,
    }
    # The per-PR detail carries every ledger line, so a long epic crosses the inline threshold:
    # spill it as one JSON file (the summary keys above stay inline either way).
    detail = json.dumps(story_prs, indent=2, ensure_ascii=False).encode("utf-8")
    if len(detail) > resolve_threshold():
        path = Path(scratch_dir) / ("epic-%s-story-prs.json" % epic_number)
        path.write_bytes(detail)
        residue.update({"story_prs_mode": "path", "story_prs_path": str(path)})
    else:
        residue.update({"story_prs_mode": "inline", "story_prs": story_prs})
    residue["story_prs_bytes"] = len(detail)
    return residue, notices, attention


def _build_epic_integration_facts(
    head_ref_name, repo, scratch_dir, gathered_envelopes, cwd=None
):
    """``facts.epic`` for an epic-integration PR: the epic's story set (the same read the story
    route makes, from the PR's own HEAD ref) plus ``residue``. No delivery log — that is the story
    route's write. Reuses the closing issue's envelope when the epic is it (the normal
    ``Fixes #<epic>`` case), so the epic is gathered once.

    Returns ``(facts_or_None, notices, attention)``; never a decision.
    """
    match = _EPIC_BRANCH_NUMBER_RE.match(head_ref_name or "")
    if match is None:
        return None, [], []
    epic_number = int(match.group(1))
    envelope = _gather_epic(
        epic_number, repo, scratch_dir, envelope=gathered_envelopes.get(epic_number)
    )
    if envelope is None:
        facts = _unavailable_story_set(epic_number)
        notices = []
    else:
        facts = _story_set_facts(epic_number, envelope)
        notices = [] if epic_number in gathered_envelopes else list(envelope.get("notices") or [])
    facts["branch"] = head_ref_name
    residue, residue_notices, attention = _build_epic_residue(
        head_ref_name, epic_number, facts["stories"], repo, scratch_dir, cwd=cwd
    )
    facts["residue"] = residue
    return facts, notices + residue_notices, attention


# ---------------------------------------------------------------------------
# Health-cache marker SHA compare (docs/specs/evaluator.md "Health-cache marker (self-read)").
# ---------------------------------------------------------------------------

_CACHE_SHA_LINE_RE = re.compile(r"^SHA:\s*([0-9a-f]+)\s*$", re.MULTILINE)


def _health_cache_fact(pr_envelope, head_sha):
    """Build the ``health_cache`` fact from the PR envelope's marker-comment fields (populated by
    ``gh_pr_gather.run`` when called with ``marker=HEALTH_CACHE_MARKER``). ``hit`` iff a marker
    comment is present and its embedded ``SHA:`` line equals the current PR head SHA
    (architecture.md §4: "hit iff the cached SHA == PR head SHA").
    """
    if not pr_envelope.get("marker_comment_present"):
        return {"sha": None, "hit": False}

    marker_body = pr_envelope.get("marker_comment") or pr_envelope.get("marker_comment_body")
    if marker_body is None and pr_envelope.get("marker_comment_mode") == "path":
        marker_body = Path(pr_envelope["marker_comment_path"]).read_text(encoding="utf-8")
    match = _CACHE_SHA_LINE_RE.search(marker_body or "")
    cached_sha = match.group(1) if match else None
    return {"sha": cached_sha, "hit": cached_sha is not None and cached_sha == head_sha}


# ---------------------------------------------------------------------------
# Repo merge config + self-review (new prep-owned gh calls — no existing executor covers these;
# architecture.md §1 permits any script to spawn `git`/`gh` directly via pipelib.process.run).
# ---------------------------------------------------------------------------

_AUTH_REQUIRED_OPTIONS = ["run: gh auth login"]


def _auth_decision(result):
    from pipelib.decisions import AUTH_REQUIRED

    return needs_decision(
        AUTH_REQUIRED,
        summary="gh authentication required",
        context={"stderr": result.stderr, "returncode": result.returncode},
        options=_AUTH_REQUIRED_OPTIONS,
    )


def _fetch_repo_merge_config(repo, cwd):
    """``gh api repos/<owner>/<repo>`` — the five merge-capability booleans
    (docs/specs/evaluator.md "Repo merge config") plus the repo's ``default_branch``.

    The default branch rides on THIS call, not on ``workspace.default_branch``, because it is
    needed by ``_detect_pr_type`` on **every** run including ``--refresh`` — whose contract is
    "never requires a root", so it must not spawn git. GitHub's REST payload is also the
    authoritative answer, and this call was already unconditional, so it costs nothing extra. It is
    returned SEPARATELY rather than inside the config dict: ``merge_config`` is a published facts
    key whose documented shape is the five booleans, and consumers compare it whole.
    Returns ``(config_dict_or_none, default_branch_or_none, decision_or_none)``."""
    result = process.run(["gh", "api", "repos/%s" % repo], cwd=cwd)
    if result.auth_required:
        return None, None, _auth_decision(result)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    return {
        "allow_squash_merge": data.get("allow_squash_merge"),
        "allow_merge_commit": data.get("allow_merge_commit"),
        "allow_rebase_merge": data.get("allow_rebase_merge"),
        "delete_branch_on_merge": data.get("delete_branch_on_merge"),
        "allow_auto_merge": data.get("allow_auto_merge"),
    }, data.get("default_branch"), None


def _fetch_current_user(cwd):
    """``gh api user`` -> login (docs/specs/evaluator.md "Self-approval pre-check").
    Returns ``(login_or_none, decision_or_none)``."""
    result = process.run(["gh", "api", "user"], cwd=cwd)
    if result.auth_required:
        return None, _auth_decision(result)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    return json.loads(result.stdout).get("login"), None


# ---------------------------------------------------------------------------
# The merge guard (#93): the merge lands only the gated head, and only when that head contains the
# base tip as of the last refresh. Both halves are facts so the spine compares nothing itself (CLAUDE.md "facts by script,
# meaning by model"); `gh pr merge --match-head-commit` covers the head in the window
# after the last refresh — nothing covers the base there.
# ---------------------------------------------------------------------------

# The compare reduced to the four values the guard reads. `--jq` keeps the multi-KB commit/file
# payload off stdout.
_BASE_COMPARE_JQ = "{status, ahead_by, behind_by, sha: .base_commit.sha}"

# `skip_reason` values — members of the closed `merge: skipped (<reason>)` set in
# skills/_shared/handoff-format.md, spelled exactly as the handoff renders them.
SKIP_HEAD_MOVED = "head-moved"
SKIP_BASE_MOVED = "base-moved"


def _fetch_base_compare(repo, base_ref, head_sha, cwd):
    """``gh api repos/<repo>/compare/<base>...<head>`` — whether the head contains the base tip.

    An ancestry fact, deliberately not ``mergeStateStatus == BEHIND``: BEHIND is reported only when
    the base's branch protection requires up-to-date branches, which an `epic/<N>-<slug>` branch
    never has, so a gate keyed on it would not fire where the pipeline runs. ``behind_by > 0``
    means the base gained commits the head lacks, however the base moved.

    A failed read FAILS CLOSED — ``AUTH_REQUIRED``, else a hard exit with no envelope — never a
    notice with a null fact: this guards a merge, and a missed read must never read as clear.
    Needs no checkout, so it runs on ``--refresh`` too — on an OPEN PR only (``build_facts``). Returns ``(base, decision_or_none)``.
    """
    # A ref may carry `#`, `?` or `%`, which would otherwise end or corrupt the REST path.
    result = process.run(
        [
            "gh", "api",
            "repos/%s/compare/%s...%s" % (repo, urllib.parse.quote(base_ref, safe="/"), head_sha),
            "--jq", _BASE_COMPARE_JQ,
        ],
        cwd=cwd,
    )
    if result.auth_required:
        return None, _auth_decision(result)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    base = _parse_base_compare(result.stdout, base_ref)
    if base is None:
        sys.stderr.write(
            "prep_evaluator: the base compare for %s returned no ahead_by/behind_by counts: %r\n"
            % (base_ref, result.stdout)
        )
        sys.exit(1)
    return base, None


def _parse_base_compare(stdout, base_ref):
    """The compare's reduced JSON as the ``merge_guard.base`` dict, or ``None`` when the counts are
    not integers — a response the guard cannot read must stop the run, never read as "base current"
    (a null ``behind_by`` would otherwise make ``base_moved`` false: fail open)."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    counts = (data.get("ahead_by"), data.get("behind_by"))
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in counts):
        return None
    return {
        "ref": base_ref,
        "sha": data.get("sha"),
        "ahead_by": counts[0],
        "behind_by": counts[1],
        "status": data.get("status"),
    }


def _merge_guard(gated_head, head_sha, base):
    """The ``merge_guard`` fact. ``gated_head`` is the opening run's ``pr.headRefOid`` — the SHA
    the workspace was asserted at and the health gate keyed on — passed back as ``--gated-head`` on
    every later refresh; comparing it here, not in prose, is what lets the offline suite pin it.

    ``head-moved`` wins over ``base-moved``: a moved head needs a fresh session whatever the base
    did. DIRTY/BLOCKED stay on ``pr.mergeStateStatus``; the spine ranks the guard ahead of both.
    """
    head_moved = gated_head != head_sha
    base_moved = base["behind_by"] > 0  # an int: _parse_base_compare refuses anything else
    if head_moved:
        skip_reason = SKIP_HEAD_MOVED
    elif base_moved:
        skip_reason = SKIP_BASE_MOVED
    else:
        skip_reason = None
    return {
        "gated_head": gated_head,
        "head_sha": head_sha,
        "head_moved": head_moved,
        "base": base,
        "base_moved": base_moved,
        "skip_reason": skip_reason,
    }


# ---------------------------------------------------------------------------
# Closing-issue DoD (parse.py dod — a pure function; no redirect_stdout needed).
# ---------------------------------------------------------------------------


def _parse_closing_issue_dod(body_text, issue_number):
    """Compose ``parse.parse_dod_bullets`` directly (a pure function — architecture.md §2's
    cleanest composition case). Returns ``(dod_list, decision_or_none)`` — a
    ``parse._DodMalformed`` is caught here and turned into the same ``DOD_MALFORMED`` decision
    ``parse.py dod`` itself would emit as a subprocess, so composing in-process changes nothing
    about what the caller observes (S6 brief's single-envelope invariant: this decision, if any,
    becomes prep's own envelope, verbatim).
    """
    try:
        return parse.parse_dod_bullets(body_text), None
    except parse._DodMalformed as exc:
        from pipelib.decisions import DOD_MALFORMED

        decision = needs_decision(
            DOD_MALFORMED,
            summary=exc.reason,
            context={"issue": issue_number, "line_number": exc.line_number, "raw_line": exc.raw_line},
            options=[
                "fix the bullet's annotation by hand to match one of the closed-set forms in "
                "skills/_shared/dod-annotations.md, then re-run"
            ],
        )
        return None, decision


# ---------------------------------------------------------------------------
# suggested_playbook / attention / notices
# ---------------------------------------------------------------------------


def _suggested_playbook(pr_type, pr_state):
    """Map ``(pr_type, pr_state)`` to the suggested playbook filename (architecture.md §5: "Prep
    proposes (suggested_playbook); the router confirms against the table"). A closed/merged PR
    still gets a playbook name — the router, not prep, decides whether re-running evaluate on an
    already-closed PR makes sense; prep states the fact (`target.state`) and proposes the playbook
    matching what the PR type *would* need, since architecture.md §5's override rule exists
    precisely for "the router may override only on evidence the script cannot see."""
    if pr_type == "epic-integration":
        return "epic-integration.md"
    if pr_type == "story":
        return "story.md"
    return "standard.md"


def _catalogue_attention(grounding_docs, catalogue_absent):
    """The doc-catalogue attention lines, prep_planner's three-state shape: `grounding_docs` is `None`
    on --refresh (no checkout read), `[]` when the catalogue was read and declares nothing, a list
    otherwise. The evaluator proceeds ungrounded on an absent catalogue (S4's doc-grounding dimension
    is skipped; skills/_shared/doc-catalogue.md), so these are attention, never a decision."""
    if grounding_docs is None:
        return []
    attention = []
    if catalogue_absent:
        attention.append(
            "no doc catalogue in the PR head — doc grounding is skipped; run /github-pipeline:setup "
            "to declare this repo's grounding docs in docs/README.md"
        )
    elif not grounding_docs:
        attention.append(
            "doc catalogue declares no documents — doc grounding is skipped until it names some"
        )
    for missing in doc_catalogue.missing_entry_paths(grounding_docs):
        attention.append(
            "doc catalogue names '%s', absent in the PR head — a stale entry, or a doc this PR "
            "removes" % missing
        )
    return attention


def _build_attention(workspace_envelope, pr_envelope, blocked_by_by_issue=None, merge_guard=None,
                     sensitive_paths_fact=None):
    attention = []
    if workspace_envelope is not None:
        setup = workspace_envelope.get("setup") or {}
        if setup.get("succeeded") is False:
            first_failure = setup.get("first_failure") or {}
            attention.append(
                "work worktree setup hook failed at step %s (`%s`) — worktree exists but is not "
                "ready" % (first_failure.get("step"), first_failure.get("command"))
            )
        if workspace_envelope.get("dirty"):
            attention.append("work worktree has uncommitted changes")
        unpushed = workspace_envelope.get("unpushed_commits") or 0
        if unpushed:
            attention.append("work worktree has %d unpushed commit(s)" % unpushed)
    guard = merge_guard or {}
    if guard.get("head_moved"):
        attention.append(
            "PR head moved to %s after it was gated at %s — the merge will skip as head-moved; a "
            "fresh session re-gates the new head"
            % ((guard.get("head_sha") or "")[:7], (guard.get("gated_head") or "")[:7])
        )
    if guard.get("base_moved"):
        base = guard.get("base") or {}
        attention.append(
            "%s is %s commit(s) ahead of this PR's head — the merge will skip as base-moved until "
            "the branch is updated and re-gated" % (base.get("ref"), base.get("behind_by"))
        )
    if pr_envelope.get("mergeStateStatus") == "DIRTY":
        attention.append("Branch has conflicts — resolve before merging.")
    if pr_envelope.get("mergeStateStatus") == "BLOCKED":
        attention.append("Merge blocked by branch protection — check status checks.")
    if pr_envelope.get("reviewDecision") == "REVIEW_REQUIRED":
        attention.append("Awaiting review from another reviewer — approval will still post, but merge may be gated.")
    if pr_envelope.get("isDraft"):
        attention.append("PR is a draft — the evaluator does not evaluate a DRAFT-state PR.")
    for issue_number, blockers in (blocked_by_by_issue or {}).items():
        open_blockers = [b for b in blockers if (b.get("state") or "").upper() == "OPEN"]
        if open_blockers:
            attention.append(
                "closing issue #%s has %d open native blocker(s) — docs/specs/evaluator.md: "
                "any open blocker holds the merge" % (issue_number, len(open_blockers))
            )
    attention.extend(_sensitive_paths_attention(sensitive_paths_fact))
    return attention


def _sensitive_paths_fact(gate_root, base_ref):
    """``config.sensitive_paths`` (#96): the head's declaration, the PR's changed paths against
    their merge base, and the matched ``(pattern, path)`` pairs. ``forced_ask`` is its own fact —
    ``merge_policy`` stays the configured per-type policy, and S7 reads the two side by side. A failed
    read (any git failure, a failed fetch included, or an unreadable merge base) reports
    ``forced_ask: true`` with its ``error``: ``ask`` is the merge gate's known-safe value, so the
    evaluation continues — unlike ``merge_guard``, whose null would read as "clear".
    """
    declaration = sensitive_paths.read_declaration(gate_root)
    paths, merge_base, error = sensitive_paths.changed_paths(gate_root, base_ref)
    matches = []
    if error is None:
        try:
            pairs = sensitive_paths.match(gate_root, paths, base_rev=merge_base)
        except sensitive_paths.BaseReadError as exc:
            error = str(exc)
        else:
            matches = [{"pattern": pattern, "path": path} for pattern, path in pairs]
    return {
        "present": declaration["present"],
        "source": declaration["source"],
        "patterns": declaration["entries"],
        "base": merge_base,
        "matches": matches,
        "forced_ask": bool(matches) or error is not None,
        "error": error,
    }


def _sensitive_paths_attention(fact):
    if not (fact or {}).get("forced_ask"):
        return []
    if fact.get("error") is not None:
        return ["sensitive-path read failed (%s) — merge gate forced to `ask`" % fact["error"]]
    first, more = fact["matches"][0], len(fact["matches"]) - 1
    if first["pattern"] == sensitive_paths.SELF_EDIT_PATTERN:
        what = "the sensitive-path declaration changed (`%s`)" % first["path"]
    else:
        what = "`%s` matched `%s`" % (first["pattern"], first["path"])
    return ["merge gate forced to `ask`: %s%s" % (what, " (+%d more)" % more if more else "")]


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def build_facts(pr_number, repo, root=".", scratch_dir=None, refresh=False, cwd=None, gated_head=None):
    """Assemble the evaluator's complete facts block and return the envelope dict WITHOUT
    printing it (the testable core, mirroring gh_gather.run's split — main() does the printing).
    ``refresh`` re-derives PR state + CI classification + health-cache hit/miss + ``merge_guard``
    (against ``gated_head``) without
    re-`ensure`-ing the work worktree (so setup hooks are not re-run) and without re-reading gate
    config (config is pinned at the root SHA already recorded on a prior run's facts, per
    architecture.md §4: "prep supports --refresh and is re-run at the points where currency
    matters" — the "matters" set for the evaluator is PR state and CI, not the gate config or
    workspace setup, per this step's Work list: "--refresh re-derives volatile facts without
    re-running hooks").
    """
    # Normalize to the MAIN checkout: under v3 the session — and therefore any relative --root —
    # sits inside the PR-head worktree. NOT on --refresh: that path touches no git at all (its
    # contract is "never requires a root"), so it must not spawn the normalizing rev-parse either.
    root = str(Path(root).resolve()) if refresh else str(workspace._resolve_main_root(root))
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-evaluator-%s" % pr_number
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    # 1) PR facts (+ thread + the health-cache marker) — compose gh_pr_gather's pure core
    #    directly; its returned decision (AUTH_REQUIRED / MARKER_AMBIGUOUS) becomes prep's own.
    pr_envelope, _pr_notices, pr_decision = gh_pr_gather.build_pr_facts(
        pr_number,
        repo,
        marker=HEALTH_CACHE_MARKER,
        scratch_dir=scratch_dir,
        cwd=cwd,
    )
    if _forward_decision(pr_decision):
        return None  # already emitted — main() reads EXIT_OK regardless (needs_decision is exit 0)

    # 2) Workspace ASSERTION on the PR head branch (v3) — the operator opened the worktree
    #    (workspace-open) and started this session inside it; prep verifies the ambient checkout
    #    IS that worktree, at exactly the PR's head OID (require_exact: the evaluator must never
    #    health-check, diff-read, or cache against code the checkout doesn't contain — a stale or
    #    diverged checkout is WORKSPACE_MISMATCH(stale_checkout), a decision, never a silent
    #    proceed), and re-runs the setup hooks from that worktree's own working tree — the same
    #    checkout the gate config below is read from.
    config_attention = []
    grounding_docs, catalogue_notices = None, []
    if not refresh:
        workspace_envelope, _ws_notices, ws_decision = workspace._build_attach(
            cwd if cwd is not None else ".",
            pr_envelope["headRefName"],
            base_for_unpushed=pr_envelope["baseRefName"],
            run_hooks=True,
            expected_remote_sha=pr_envelope["headRefOid"],
            require_exact_remote_sha=True,
        )
        if _forward_decision(ws_decision):
            return None
        # A setup-hook failure is a partial-but-honest payload (setup.succeeded == False) that the
        # evaluator playbook reads off `workspace.setup` + an `attention` line — never a hard exit.
        # A hard git failure already sys.exit(1)'d inside the core with faithful stderr and no
        # envelope (§3), so there is nothing to guard for here anymore.

        # 3) Gate config, read from the PR-head worktree just asserted.
        gate_root = workspace_envelope["path"]
        gate_config, config_notices = _read_gate_config(gate_root)
        gate_config["source"] = workspace._hook_source_facts(gate_root, None)
        gate_config["source"].pop("file", None)  # per-block *_source names the file; this is the tree
        if gate_config["source"].get("dirty"):
            config_attention.append(
                "gate config was read from a checkout with uncommitted changes (%s) — this PR is "
                "being judged by the checks in that working tree" % gate_root
            )

        # 3b) Grounding docs (skills/_shared/doc-catalogue.md), read at the same PR-head worktree,
        #     so a PR that edits a doc — or its catalogue entry — is judged against its own copy
        #     (S4's doc-grounding dimension). `None` on --refresh, which reads no checkout.
        grounding_docs, catalogue_notices = doc_catalogue.read_catalogue(gate_root)

        # 3c) The sensitive-path declaration (#96), computed once here at the gated head and never
        #     on --refresh — a moved head already skips the merge as head-moved and is re-gated by a
        #     fresh session. Only on an OPEN PR: any other state has no merge to gate.
        gate_config["sensitive_paths"] = (
            _sensitive_paths_fact(gate_root, pr_envelope["baseRefName"])
            if pr_envelope.get("state") == "OPEN"
            else None
        )
    else:
        # --refresh: re-derive only PR state + CI + health-cache hit/miss + merge_guard. Root freshness,
        # workspace ensure (and its setup hooks), and gate config are NOT re-run/re-read — the S6
        # brief and this step's DoD are explicit that --refresh must not re-run hooks. A caller
        # that needs a workspace/config fact on a --refresh run is expected to hold onto the
        # prior full run's facts block (this is what "the router confirms against the table"
        # sessions already do — the facts block from the first prep call stays in context).
        workspace_envelope = None
        gate_config, config_notices = {}, []

    # 4) The closing issue's DoD (parse.py dod, composed as a pure function) — for each issue in
    #    closingIssuesReferences. gh_gather.run is the accepted-reference emit-through-a-stream
    #    executor (S8 retro §5): it returns (exit, envelope) and emits into a discard sink so its
    #    envelope never reaches prep's own stdout — no io.StringIO/redirect_stdout capture.
    closing_issues = pr_envelope.get("closingIssuesReferences") or []
    dod_by_issue = {}
    blocked_by_by_issue = {}
    deps_available_by_issue = {}
    plans_by_issue = {}
    issue_envelopes = {}
    issue_gather_notices = []
    for closing_issue in closing_issues:
        issue_number = closing_issue.get("number")
        if issue_number is None:
            continue
        issue_exit, issue_envelope = gh_gather.run(
            str(issue_number), repo, scratch_dir=scratch_dir, stream=_DiscardStream()
        )
        if issue_envelope is not None and issue_envelope.get("status") == "needs_decision":
            if _forward_decision(issue_envelope["decision"], notices=issue_envelope.get("notices")):
                return None
        if issue_exit != 0:
            sys.stderr.write(
                "prep_evaluator: gh_gather on closing issue #%s failed (exit %d)\n"
                % (issue_number, issue_exit)
            )
            sys.exit(1)
        issue_gather_notices.extend(issue_envelope.get("notices") or [])
        issue_envelopes[issue_number] = issue_envelope
        issue_body = issue_envelope.get("issue_body")
        if issue_body is None and issue_envelope.get("issue_body_mode") == "path":
            issue_body = Path(issue_envelope["issue_body_path"]).read_text(encoding="utf-8")
        dod_list, dod_decision = _parse_closing_issue_dod(issue_body or "", issue_number)
        if dod_decision is not None:
            emit_needs_decision(dod_decision)
            return None
        dod_by_issue[str(issue_number)] = dod_list
        # Native issue dependencies (docs/specs/evaluator.md "Artifacts read": "Any open blocker
        # holds the merge — soft-reject") — gh_gather's base payload already carries these; the
        # evaluator playbook (not prep) decides the soft-reject, prep only surfaces the fact.
        blocked_by_by_issue[str(issue_number)] = issue_envelope.get("blocked_by") or []
        deps_available_by_issue[str(issue_number)] = issue_envelope.get("deps_available")
        plan_facts, plan_notices = _build_plan_facts(
            issue_envelope, scratch_dir, issue_number, pr_envelope["number"]
        )
        plans_by_issue[str(issue_number)] = plan_facts
        issue_gather_notices.extend(plan_notices)

    # 4b) `facts.epic`. A story PR: the parent epic's story set, which the story route needs to
    #     decide whether progress projection has anything to write and to route on whether siblings
    #     remain (skills/evaluator/playbooks/story.md Action 2 + handoff). An epic-integration PR: the
    #     epic's story set plus the residue its merged story PRs left (deferred follow-ups, polish
    #     ledgers), which the integration verdict re-adjudicates against the epic's DoD
    #     (skills/evaluator/playbooks/epic-integration.md). Built after PR-type detection (step 6).
    epic_facts = None
    epic_attention = []

    # 5) Repo merge config + self-review (new prep-owned gh calls).
    merge_config, root_branch, merge_decision = _fetch_repo_merge_config(repo, cwd)
    if merge_decision is not None:
        emit_needs_decision(merge_decision)
        return None
    current_user, user_decision = _fetch_current_user(cwd)
    if user_decision is not None:
        emit_needs_decision(user_decision)
        return None
    pr_author_login = (pr_envelope.get("author") or {}).get("login")
    self_review = pr_author_login is not None and pr_author_login == current_user

    # 5b) The merge guard (#93), on the full run AND on --refresh — it is the pre-merge currency
    #     fact. Without --gated-head the gated head IS this run's head (the opening run). Only on an
    #     OPEN PR: any other state has no merge to guard (every merge-reaching step stops on state
    #     first), and its base branch may be gone — a compare there would turn a readable merged PR
    #     into a hard failure. `merge_guard` is then null, never a partial guard reading "clear".
    pr_open = pr_envelope.get("state") == "OPEN"
    merge_guard = None
    if pr_open:
        head_sha = pr_envelope.get("headRefOid")
        base, compare_decision = _fetch_base_compare(
            repo, pr_envelope.get("baseRefName"), head_sha, cwd
        )
        if compare_decision is not None:
            emit_needs_decision(compare_decision)
            return None
        # Only an ABSENT pin means "this run's head": an empty or other present value is compared.
        merge_guard = _merge_guard(head_sha if gated_head is None else gated_head, head_sha, base)

    # 6) CI rollup classification, PR-type detection, health-cache SHA compare.
    ci_class, ci_fail_checks = _classify_ci_rollup(pr_envelope.get("statusCheckRollup"))
    # `root_branch` came from the repo API fetched above — never `workspace.default_branch`, which
    # would spawn git on the --refresh path whose contract is "never requires a root".
    pr_type = _detect_pr_type(
        pr_envelope.get("baseRefName"), pr_envelope.get("headRefName"), root_branch
    )
    health_cache = _health_cache_fact(pr_envelope, pr_envelope.get("headRefOid"))

    if pr_type == "story":
        epic_facts, epic_notices = _build_epic_facts(
            pr_envelope.get("baseRefName"),
            repo,
            scratch_dir,
            [issue["number"] for issue in closing_issues if issue.get("number") is not None],
        )
        issue_gather_notices.extend(epic_notices)
    elif pr_type == "epic-integration" and not refresh:
        # Not on --refresh: its contract is PR state + CI, and the residue costs a list call plus a
        # view per follow-up. The first run's facts block stays in context.
        epic_facts, epic_notices, epic_attention = _build_epic_integration_facts(
            pr_envelope.get("headRefName"), repo, scratch_dir, issue_envelopes, cwd=cwd
        )
        issue_gather_notices.extend(epic_notices)

    # 7) Assemble the facts block (architecture.md §4 common core + evaluator extensions).
    vector = {"type": pr_type, "mode": "continue", "pr_state": "open-by-you" if self_review else "open"}
    if not pr_open:
        vector["pr_state"] = pr_envelope.get("state", "unknown").lower()

    facts = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root, "default_branch": root_branch, "fresh": not refresh},
        "target": {
            "kind": "pr",
            "number": pr_envelope["number"],
            "title": pr_envelope["title"],
            "state": pr_envelope["state"],
            "labels": pr_envelope.get("labels") or [],
        },
        "vector": vector,
        "suggested_playbook": _suggested_playbook(pr_type, pr_envelope["state"]),
        "pr": {
            "headRefName": pr_envelope["headRefName"],
            "baseRefName": pr_envelope["baseRefName"],
            "headRefOid": pr_envelope["headRefOid"],
            "isDraft": pr_envelope["isDraft"],
            "author": pr_envelope.get("author"),
            "additions": pr_envelope.get("additions"),
            "deletions": pr_envelope.get("deletions"),
            "changedFiles": pr_envelope.get("changedFiles"),
            "commit_count": pr_envelope.get("commit_count"),
            "mergeStateStatus": pr_envelope.get("mergeStateStatus"),
            "mergeable": pr_envelope.get("mergeable"),
            "reviewDecision": pr_envelope.get("reviewDecision"),
            "url": pr_envelope.get("url"),
            "closingIssuesReferences": closing_issues,
        },
        "pr_type": pr_type,
        "ci": {"class": ci_class, "fail_checks": ci_fail_checks},
        "health_cache": health_cache,
        "merge_guard": merge_guard,
        "self_review": self_review,
        "current_user": current_user,
        "config": gate_config,
        "merge_config": merge_config,
        "dod": dod_by_issue,
        "blocked_by": blocked_by_by_issue,
        "deps_available": deps_available_by_issue,
        "plans": plans_by_issue,
        "grounding_docs": grounding_docs if grounding_docs is not None else [],
        "attention": _build_attention(
            workspace_envelope, pr_envelope, blocked_by_by_issue, merge_guard,
            gate_config.get("sensitive_paths"),
        )
        + config_attention
        + _catalogue_attention(grounding_docs, DOC_CATALOGUE_ABSENT in catalogue_notices)
        + epic_attention,
        "notices": list(config_notices) + list(issue_gather_notices) + catalogue_notices,
    }
    if epic_facts is not None:
        facts["epic"] = epic_facts
    if workspace_envelope is not None:
        # v3: the OBSERVED ambient checkout (attach), asserted at exactly pr.headRefOid.
        # `base_ref` is the PR's own baseRefName — a derived fact (attach cannot observe a base).
        facts["workspace"] = {
            "path": workspace_envelope["path"],
            "branch": workspace_envelope["branch"],
            "expected_branch": workspace_envelope.get("expected_branch"),
            "base_ref": pr_envelope["baseRefName"],
            "sha": workspace_envelope.get("sha"),
            "dirty": workspace_envelope.get("dirty"),
            "unpushed_commits": workspace_envelope.get("unpushed_commits"),
            "source": "ambient",
            # Carried through so a failed setup hook is visible to the evaluator playbook (also
            # surfaced as an `attention` line, per _build_attention) rather than silently
            # dropped — `sha`/`dirty`/`unpushed_commits` are null on this path (workspace.py
            # itself skips computing them when setup fails, since they describe a workspace
            # ready for use).
            "setup": workspace_envelope.get("setup"),
        }

    # Threshold-spill sections gh_pr_gather already staged (body/thread/reviews/marker_comment)
    # are surfaced by field name pass-through, per architecture.md §4's "sections{...}" example —
    # every *_mode/*_bytes/*_path (or bare inline field) gh_pr_gather produced rides through
    # unchanged, so the evaluator playbook reads them exactly as gh_pr_gather itself documents.
    sections = {}
    for key, value in pr_envelope.items():
        if key.startswith(("body", "thread", "reviews", "marker_comment", "diff", "line_comments")):
            sections[key] = value
    facts["sections"] = sections

    # The `## Polish` ledger (skills/_shared/polish-ledger.md) — the adjudication input
    # (references/polish-adjudication.md). Scanned from the same PR body, so no extra call; the scan
    # never raises (an unparseable line is `unparsed`, rewritten on the evaluator's disposition write).
    pr_body = read_section(pr_envelope, "body")
    facts["polish"] = parse.scan_polish(pr_body)

    # The `## Security review` record (skills/_shared/security-review.md) — S4.5's input. The record
    # is self-declared body text, so a `Reviewed:` line at head lets S4.5 skip its pass only on a PR
    # this account opened (the resolver's); on anyone else's PR, one edited line would skip the gate.
    security = parse.scan_security(pr_body)
    security["covers_head"] = bool(
        security["reviewed_sha"]
        and security["reviewed_sha"] == pr_envelope.get("headRefOid")
        and self_review
    )
    facts["security"] = security

    return facts


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pr", help="PR number")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--root", default=".", help="project root (architecture.md §6 vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for spilled sections (default: /tmp/gh-evaluator-<pr>)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-derive volatile facts (PR state, CI, health-cache hit, merge_guard) without re-running hooks",
    )
    parser.add_argument(
        "--gated-head",
        default=None,
        help="the opening run's full 40-character pr.headRefOid; merge_guard.head_moved compares the "
        "current head to it",
    )
    parser.add_argument("--cwd", default=None, help="explicit cwd for the repo-merge-config/user gh calls")
    args = parser.parse_args(argv)
    # The guard compares by exact string equality, so a short or empty SHA would read every head as
    # moved, or (empty) slip past the pin — refuse it as a usage error, before any gh call.
    if args.gated_head is not None and not re.fullmatch(r"[0-9a-f]{40}", args.gated_head):
        parser.error(
            "--gated-head takes the opening run's full 40-character pr.headRefOid, got %r"
            % args.gated_head
        )

    facts = build_facts(
        args.pr,
        args.repo,
        root=args.root,
        scratch_dir=args.scratch_dir,
        refresh=args.refresh,
        cwd=args.cwd,
        gated_head=args.gated_head,
    )
    if facts is None:
        # A needs_decision envelope was already emitted by build_facts (via a composed executor's
        # forwarded decision, or a directly-raised one) — nothing left to print.
        return EXIT_OK
    # pipelib.envelope.build_ok reserves "notices" as its own top-level envelope key (populated
    # via emit_ok's notices= kwarg, never inside payload) — pop it out of the logical facts dict
    # build_facts assembled so the wire shape stays architecture.md §4's flat
    # {"status": ..., ...facts-fields..., "notices": [...]}, not a nested "notices" collision.
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
