#!/usr/bin/env python3
"""prep_planner.py — the planner's complete facts block in one call (architecture.md §4;
docs/implementation.md S12; docs/specs/planner.md). Assembles the session's entire starting
state — revise detection, the `plan_ref` selection table moved into code, epic/JIT-story facts,
revise facts, the deterministic open-question tracker de-dup search (the frozen Bug (a) fix), and
the catalogue-declared grounding docs at the pinned `plan_ref` SHA — as ONE JSON envelope on stdout, so the
planner session's startup is one Python process, never a subprocess chain.

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external
processes any script may spawn are git/gh")::

    gh_gather.run(..., stream=)            -- the target issue's body/thread/plan-marker/native
                                               deps envelope (one round-trip); reused a SECOND time
                                               (still in-process) on the parent epic's issue number
                                               for "Just-in-time story planning" facts
    gh_pr_gather.build_pr_facts(...)       -- the open PR's body, ONLY in revise mode with an open
                                               PR (for the `## Phase tracker` parse/stage)
    workspace._build_root_status / _build_ensure_read
                                            -- root freshness and the ONE read workspace this skill
                                               ever gets, grounded at `plan_ref` (§6/prd §8.4: the
                                               planner is read-only — it never gets a work
                                               workspace)
    parse.parse_oq_links                    -- the issue's `## Open questions` section (the Bug (a)
                                               tracker de-dup search's input)
    parse.parse_phases                      -- the PRIOR plan's `## Phases`, for the sub-issue
                                               plan-versus-live diff. BEST-EFFORT: a malformed
                                               section is reported as a fact, never as
                                               `PHASES_MALFORMED` (step 4.5)
    parse.dod_summary                       -- `facts.dod`: the issue DoD's shape (present / count /
                                               a checklist under another heading) + the body staged
                                               at `dod.body_path`. Never raises — no annotation is
                                               parsed, so a malformed one never stops a revise
    doc_catalogue.read_catalogue            -- the CONSUMING repo's `<!-- doc-catalogue -->` block
                                               (its declared grounding docs), read at the grounding
                                               checkout. Replaces this module's former hardcoded
                                               four-path tuple; composes
                                               `config_block.read_block_anywhere` internally with a
                                               `docs/README.md` candidate list.
    config_block.read_block_anywhere        -- not called directly (no gate-config block the planner
                                               reads is named yet); reached through
                                               `doc_catalogue` above, and kept imported for parity
                                               with the sibling preps' composition style.

Every executor composed here exposes a **pure, non-emitting core** — ``build_*(...) -> (payload,
notices, decision|None)`` (docs/specs/baseline.md §5, the S8 pattern lock). This prep calls those
cores **directly** and forwards each core's returned ``decision`` verbatim (:func:`_forward_decision`),
emitting exactly one envelope of its own. No ``redirect_stdout``/``io.StringIO`` capture of another
script's stdout is used anywhere in this module (S9-on rule; docs/specs/baseline.md §5).

``git ls-remote --heads origin <pattern>`` (epic-branch discovery), ``gh issue list --label epic
...`` (story parent-epic search), ``gh issue view <NN> --json state,title,labels`` (per-story
live-state fetch, mirroring v1's ``GATHER_EPIC`` reconciliation), and ``gh api --paginate
repos/<owner>/<repo>/issues/<N>/sub_issues`` (the deliverable-sub-issue detail fetch, step 4.5 —
kept HERE rather than in ``gh_gather`` because six preps compose that module and only the planner
reads this fact) have no existing executor core —
architecture.md §1 permits any script to spawn `git`/`gh` directly via `pipelib.process.run`, the
same precedent `prep_resolver.py` and `prep_evaluator.py` already established for their own
prep-owned direct calls.

Usage::

    prep_planner.py <issue-number> <owner/repo> [--root PATH] [--scratch-dir PATH] [--refresh]
                    [--ground-ambient]
    prep_planner.py <issue-number> <owner/repo> --oq-query "<topic>" [--oq-query "<topic>" ...]

``--root`` defaults to ``.`` (the project root — architecture.md §6's read-only trust vantage).
``--scratch-dir`` defaults to ``/tmp/gh-planner-<issue-number>`` (CLAUDE.md's ``/tmp/gh-<skill>-
<N>/`` convention) when omitted. ``--refresh`` re-derives the volatile facts (issue/PR state,
marker detection, the open-question tracker search) without re-running root freshness or
re-ensuring the grounding read workspace — mirroring `prep_resolver.py`'s / `prep_evaluator.py`'s
identical `--refresh` contract (architecture.md §4).

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"``
or ``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified
hard `gh`/`git` failure surfaced by a composed executor — stderr carries the faithful error.

``vector.mode`` is the TWO-value closed set ``"fresh"`` / ``"revise"`` (docs/specs/planner.md
"Artifacts read": "the presence of a plan comment switches you to revise mode") — derived purely
from whether the TARGET issue's own `<!-- implementation-plan:v1 -->` marker comment is present
(the same `GATHER_ISSUE` marker lookup v1's Step 2 documents). There is no third value analogous
to the resolver's ``"gated"``: the planner never withholds its one grounding read workspace behind
an operator gate — grounding is always safe (a detached, read-only view), unlike the resolver's
work worktree, which can impersonate another author's in-flight branch.

**`plan_ref` selection — the FULL v1 table (docs/specs/planner.md Step 4.5), moved into code.**
Distinct facts (:func:`_select_plan_ref`), collapsing to the same v1 table (whose row 5
bundles two cases this module reports as two distinct, independently-testable facts) — precedence
is fixed: when more than one row applies (a story under an open epic that ALSO has an open PR),
the open-PR-head row always wins (v1's documented rationale: that head is a strict superset of the
epic branch, and is what the resolver actually continues on) — outranked only by row 0, the
operator's explicit answer. The own-branch rows (4 and 7b) win over every base row they are
eligible against (:data:`ROWS_ELIGIBLE_FOR_OWN_BRANCH`: 5, 6, 7, 8) whenever BOTH their
preconditions hold:

  0. ``PLAN_REF_ROW_AMBIENT_ACCEPTED`` — the ambient checkout is another branch carrying commits
     beyond the base tip, and the operator answered the card with ``--ground-ambient`` -> that
     branch. Never derived; only the flag sets it, and only for a checkout containing the tip.

  1. ``PLAN_REF_ROW_OPEN_PR_HEAD`` — an open PR already exists for this issue -> that PR's
     `headRefName`. Checked FIRST, unconditionally, so it wins the precedence rule for free.
  2. ``PLAN_REF_ROW_EPIC_BRANCH`` — epic-as-target, `epic/<N>-<slug>` branch discovered -> that
     branch.
  3. ``PLAN_REF_ROW_EPIC_BOOTSTRAP`` — epic-as-target, zero `git ls-remote` matches -> `main`.
  4. ``PLAN_REF_ROW_STORY_OWN_BRANCH`` — story (or untyped sub-issue) under an OPEN parent epic
     where the AMBIENT checkout is this issue's OWN branch (`branching.branch_belongs_to_issue`)
     and that checkout's HEAD already CONTAINS the tip of the ref rows 5/6 would have chosen ->
     that story branch. **Added post-v3:** row 1 already makes a story's own branch the `plan_ref`
     once a PR exists, so this row closes the window BEFORE the first PR — between
     `workspace-open` (which bases a story branch on the epic branch, or on `main` pre-bootstrap)
     and the first push. Without it a story worktree is refused for a ref it is frequently
     bit-identical to: the attach gate's branch-name equality check stands in for a content check
     it never performs. Containment is what makes the substitution sound — a contained base tip
     means the story branch is a strict SUPERSET of that ref, so grounding on it can only see
     more truth, never less. It is probed offline (:func:`workspace.contains_commit`, never a
     fetch); a branch that is behind or diverged, and a base tip this checkout never fetched, both
     fall through to rows 5/6 and refuse exactly as before (notices
     ``OWN_BRANCH_BEHIND_BASE_TIP`` / ``BASE_TIP_CONTAINMENT_UNAVAILABLE``) — a later story must
     never ground on code a predecessor has since moved past, and that is a containment property,
     not a naming one. The base it was measured against rides out as
     `story.own_branch_base_ref`, so ONE row label carries both eligible cases without losing
     provenance.
  5. ``PLAN_REF_ROW_STORY_PARENT_BRANCH`` — story under an OPEN parent epic, `epic/<N>-<slug>`
     branch discovered -> that branch.
  6. ``PLAN_REF_ROW_STORY_PARENT_BOOTSTRAP`` — story under an OPEN parent epic, zero
     `git ls-remote` matches (the parent epic itself hasn't bootstrapped its integration branch
     yet — no story has been resolved for it) -> `main`. **Added post-S13 (D4 fix):** the story
     branch of :func:`_select_plan_ref` previously collapsed this case into row 7 below by keying
     on branch ABSENCE alone (`epic_branch_name is None`), which is truthful for `plan_ref` itself
     (both rows fall back to `main`) but produces a self-contradictory `vector.plan_ref_row` label
     when `story.parent_epic_open` is simultaneously `true` — a fact the row name flatly denies
     ("story-no-open-parent-epic" while the parent IS open). `plan_ref`/routing behavior is
     UNCHANGED by this fix (`main`; `_suggested_playbook` keys on `parent_epic_open`, never on the
     row name — see that function); only the row's truthfulness is fixed, per architecture.md §4's
     "facts are data, never re-derived" invariant applied to the row label itself.
  7. ``PLAN_REF_ROW_STORY_NO_PARENT`` — story with NO parent epic found, or a CLOSED one -> `main`.
     Distinguished from row 6 by `parent_epic_open` (`False` here, `True` there) — both still ride
     the same `plan_ref` (`main`), but the row now says which reality produced it. Eligible for
     row 4 since 4.33.0; its "grounding at main" attention tail is dropped whenever the checkout
     is not `main` itself, so the two never contradict.
  7b. ``PLAN_REF_ROW_OWN_BRANCH`` — row 4's argument for a standalone issue: the ambient checkout
     is this issue's own branch and contains the default branch's tip -> that branch. Its own
     label, because `story-own-branch` on a non-story would be a row name denying the facts (D4).
  8. ``PLAN_REF_ROW_DEFAULT`` — everything else (standalone bug/feature/incomplete/multi-phase,
     no open PR) -> `main`.

**Content, not names (4.33.0).** Whatever row answered, the planner accepts the ambient checkout
by what it CONTAINS (:func:`_probe_ambient`): `plan_ref` itself; this issue's own branch containing
the base tip (rows 4/7b); another branch whose HEAD IS the tip (`grounding.basis: identical` —
the same code, so `plan_ref` and the footer stand); another branch with commits beyond the tip
only by the operator's answer (row 0). Behind, diverged, or unfetched still refuses. The resolver
and evaluator keep exact-branch attach: there the branch is what they push or judge.

Every row (:func:`build_facts`) ends the same way regardless of which fired: an unconditional
``workspace._build_ensure_read(root, plan_ref)`` — the plan's (and eventually the handoff's)
`<plan-ref>@<short-sha>` footer sha comes from THIS read workspace's own head, never a second,
independently-derived sha. ``plan_ref`` itself always rides as a BARE branch name (`"main"` /
`"epic/1-x"` / a PR's `headRefName`), matching `prep_resolver.py`'s `audit_ref` convention — the
`origin/`-prefixed read form lives only inside `workspace.py`'s own internals, and the footer's
"`origin/main` for the default branch, bare branch name otherwise" RENDERING split is explicitly
S13's job (docs/specs/planner.md's "Read ref vs recorded ref"), not a fact this prep computes.

**Bug (a) — the deterministic open-question tracker de-dup search
(now `oq_tracker.build_open_question_candidates`; see the "S14 promotion" paragraph below).**
docs/specs/planner.md's frozen falsifiable requirement: a genuinely-filed `question` issue must
NEVER be recorded "(not filed)" in a posted plan. `open-question-detection.md` §Matching names the
exact mechanism and its query-derivation rule: run `gh issue list --repo <owner/repo> --state all
--label question --search "<query>"` for every entry the issue's own `## Open questions` section
(`parse.parse_oq_links`) currently records as `question: (not filed)` — `<query>` is
deterministically the entry's own `oq_id` (the backtick-quoted id/topic-phrase every `- OQ: `<id>`
...` line already carries; per the shared contract this is "the OQ's tracker id when it has one
... or its distinctive topic keywords when detection was heuristic" — either way, the SAME field,
no separate keyword-extraction heuristic needed). Entries whose `question` field is already `#N`
are NOT re-searched (already resolved; no "(not filed)" claim is being made). A repo/issue with no
`## Open questions` section makes ZERO `gh` calls for this fact (this step's canonical call-budget
fixture has none). Results ride verbatim as `open_question_candidates: [{"oq_id", "query",
"candidates": [{"number","title","state","labels"}, ...]}, ...]` — the planner's own prompt can
therefore never silently record "(not filed)" for an entry this fact lists without first consulting
it; :func:`_build_attention` also surfaces an unmissable `attention` line per non-empty candidate
group. For an OQ the plan detects **anew during grounding** (never in the issue body, so absent
from the body-driven search above), the ``--oq-query`` one-shot mode (now
`oq_tracker.build_oq_query`, aliased below as :func:`build_oq_query`) runs the identical search on
demand — the S13-authorized additive extension that keeps the raw `gh issue list` out of the
playbook prompt.

**Deliverable sub-issues (#18) — `slices`.** A NON-EPIC target's sub-issues are its deliverable
slices by construction (the hierarchy is epic → story → slice), so no label and no per-child
classification fetch is involved. When the target is non-epic AND has sub-issues, `facts.slices`
carries the live set (`number`, `title`, `state`, `position` — the sub-issue panel's own order, the
sequencing source of truth — `updated_at`, `maybe_rescoped`, and the body staged to a path) plus
`slices.diff`, the plan-versus-live diff computed HERE and never re-derived in a prompt:
`uncovered_open` / `closed` / `removed` / `rescoped` / `order_changed`, alongside the map facts
`mapped` / `substrate_phases` / `unmapped_phases`. Each non-empty case also surfaces an `attention`
line (:func:`_slice_attention`). `rescoped` is a SUSPICION — an issue's `updated_at` bumps on comments
and labels too, so it over-reports — with two carve-outs that stop it lying in the other direction: a
CLOSED sub-issue is excluded (closing bumps the timestamp, and closure has its own disposition), and a
served sub-issue with no usable timestamp is named in `attention` as uncovered by the comparison.
`rescope_basis` distinguishes `updated_at` (it ran) from `no_prior_plan` (nothing to compare against)
and `unavailable` (this host cannot answer). A childless target and an epic target both cost ZERO
extra `gh` calls, which is what keeps the canonical call budget unchanged.

**S14 promotion.** `_search_question_tracker` / `_build_open_question_candidates` / `build_oq_query`
were this module's own S12/S13 helpers until S14's `prep_drafter.py` needed the byte-identical
mechanism for its own search-before-file companion-question de-dup (docs/specs/drafter.md Step
3.5/R4 — the SAME deterministic query rule, not a similarly-shaped-but-distinct local `gh` call).
Unlike this module's epic-branch/parent-epic/story-state helpers (which stay local per-prep because
their shapes only coincidentally rhyme with a sibling prep's), this one **is** the literal same
algorithm serving the same shared contract (`open-question-detection.md` §Matching, named by both
`docs/specs/planner.md` and `docs/specs/drafter.md`) — so it is now `oq_tracker.py` (a new,
single-purpose module; `scripts/pipelib/` is the wrong layer, since `pipelib` must never import a
`scripts/*.py` sibling and this mechanism itself composes `parse.parse_oq_links`). This module was
refactored to call `oq_tracker.build_open_question_candidates` in-process instead of carrying its
own copy — its envelope is byte-identical (verified: `tests/test_prep_planner.py` is unmodified and
green). `build_oq_query` is kept as a module-level alias (`build_oq_query = oq_tracker.build_oq_query`)
so the one direct-call test line (`prep_planner.build_oq_query(...)`) and this module's own CLI
`--oq-query` call site keep working unchanged.

**The shared config-block reader promotion (S9-carried advisory, authorized for S12).**
`_read_block_anywhere` (plus its `_candidate_config_files`/`_find_includes_one_level` helpers) was
duplicated byte-for-byte in `prep_evaluator.py` and `prep_resolver.py`; this module would have been
the third copy. It is now `config_block.read_block_anywhere` (a public function on the ONE existing
script that already owns the config-block-reading domain — `scripts/pipelib/` is the wrong layer,
since `pipelib` must never import a `scripts/*.py` sibling), and both existing preps were refactored
to call it in-process instead of carrying their own copy — their envelopes are byte-identical
(verified: their own test suites are unmodified and green). This module imports `config_block` for
composition-style parity with the sibling preps even though it has no gate-config block of its own
to read today.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402  (import-only: the issue's-own-PR narrowing)
import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import doc_catalogue  # noqa: E402  (the consuming repo's declared grounding docs)
import gh_gather  # noqa: E402
import gh_pr_gather  # noqa: E402
import oq_tracker  # noqa: E402
import plan_shipped  # noqa: E402  (legacy shipped-phase records: the revise folds them back)
import story_set  # noqa: E402  (the epic story set's two-tier read)
import parse  # noqa: E402  (the prior plan's `## Phases` parse for the sub-issue diff — best-effort,
# see step 4.5; also keeps `prep_planner.parse` resolving for tests/test_prep_planner.py's direct
# `prep_planner.parse.parse_oq_links(...)` call, per the S14-promotion's "tests unmodified" bar.
# `oq_tracker.py` is this module's OQ-search composition path)
import workspace  # noqa: E402
from pipelib import process, trust  # noqa: E402
from pipelib.decisions import (  # noqa: E402
    AMBIGUOUS,
    AUTH_REQUIRED,
    DOC_CATALOGUE_ABSENT,
    MARKER_AMBIGUOUS,
    TARGET_IS_SLICE,
    needs_decision,
)
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_needs_decision, emit_ok  # noqa: E402
from pipelib.spill import spill_bytes  # noqa: E402
from pipelib.thread import load_thread  # noqa: E402

import delivery_log  # noqa: E402  (in-process composition, not a subprocess chain)

# The implementation-plan marker (skills/planner/references/plan-schema.md;
# docs/specs/planner.md "Artifacts read") — GATHER_ISSUE's marker_prefix on both the TARGET issue
# (the revise-mode trigger) and, for a story under an open epic, the parent epic (the JIT epic
# plan the story grounds on).
PLAN_MARKER = "<!-- implementation-plan:v1 -->"

# The research-dossier marker (docs/specs/planner.md Step 4; the researcher's dossier schema).
# Detected by scanning the TARGET issue's own thread (already fully paginated by GATHER_ISSUE) —
# never a second `gh api` round-trip the way v1's Step 4 targeted fetch needed, since v2's thread
# fetch is already complete.
RESEARCH_MARKER = "<!-- issue-research:v1 -->"

# The epic delivery log (skills/_shared/epic-delivery-log.md) — read (never written) by the planner
# and staged for both an epic-as-target run (its OWN log, informational) and a story-under-epic JIT
# run (the reconciliation input against the epic plan's pinned contracts). The tier resolution,
# ordering and `log_source` classification live in `delivery_log.py`, composed in-process: the log
# is now a SET of per-story comments plus a legacy monolithic tier (#41), and a copy of that
# algorithm here is how `log_source` would come to disagree with prep_evaluator's.

# Grounding docs come from the CONSUMING repo's `<!-- doc-catalogue -->` block, not from any path
# list here (`skills/_shared/doc-catalogue.md`; `scripts/doc_catalogue.py`). This prep previously
# carried a hardcoded four-path tuple, which asserted a doc layout on a repo that never agreed to
# one and disagreed with both `prep_drafter.py`'s own copy and the prompts (which name two further
# docs neither prep inventoried). The read happens INSIDE the already-ensured grounding checkout
# (a real directory at `plan_ref`) rather than via `git ls-tree -r --name-only <ref>` against a ref
# pointer — architecture.md §6's "no ref arithmetic" principle extended: once the workspace is
# ensured, a plain filesystem read is simpler and needs no second git invocation.

# State-vector `type` detection (docs/specs/resolver.md's identical rule, restated here rather than
# imported cross-prep — see the module docstring's "no prep-to-prep imports" design note in the
# implementor report): case-insensitive `epic`/`story` label match, OR a title `Epic:` prefix.
_EPIC_TITLE_PREFIX_RE = re.compile(r"^\s*epic\s*:", re.IGNORECASE)

# Epic branch pattern: `epic/<N>-<slug>` (docs/specs/planner.md Step 4.5's table).
_EPIC_BRANCH_LS_REMOTE_PATTERN = "epic/%s-*"




# ---------------------------------------------------------------------------
# _DiscardStream / _forward_decision — identical pattern to prep_resolver.py / prep_evaluator.py
# (S8 retro §5's accepted-reference emit-through-a-stream shape for gh_gather.run).
# ---------------------------------------------------------------------------


class _DiscardStream:
    """A minimal write-sink for `gh_gather.run(stream=...)` — see `prep_evaluator._DiscardStream`
    for the full rationale (restated locally per that module's own note: sharing a two-method sink
    across prep modules is not the in-process composition architecture.md §2 asks for)."""

    def write(self, _data):
        return None

    def flush(self):
        return None


def _forward_decision(decision, notices=None):
    """Emit a composed core's returned `decision` AS-IS on prep's own stdout and return `True` when
    a decision was present. Mirrors `prep_resolver._forward_decision` / `prep_evaluator._forward_decision`
    exactly."""
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


def _merge_notices(accumulated, more):
    """Append a composed core's notices to prep's own list, in first-seen order and without
    duplicates (both gh_gather calls degrade identically on a host that doesn't serve a relation, so
    the same code arrives twice).

    A prep that drops its cores' notices reports the degraded read as if it were the full one: on a
    host without the native parent/sub-issue relation, `SUBISSUES_UNSUPPORTED` is the ONLY signal
    that an empty sub-issue set means "unavailable" rather than "no children", and the planner's
    sub-issue reconciliation would otherwise skip silently.
    """
    for notice in more or []:
        # `None` is a composed reader's "nothing to report" (`trust.untrusted_notice`), never a notice.
        if notice and notice not in accumulated:
            accumulated.append(notice)
    return accumulated


# ---------------------------------------------------------------------------
# State-vector type detection (docs/specs/planner.md "Route by shape").
# ---------------------------------------------------------------------------


def _detect_type(labels, title):
    lowered_labels = {(label or "").strip().lower() for label in labels or []}
    if "epic" in lowered_labels or _EPIC_TITLE_PREFIX_RE.match(title or ""):
        return "epic"
    if "story" in lowered_labels:
        return "story"
    return "standard"


# ---------------------------------------------------------------------------
# git ls-remote — epic-branch discovery (no existing executor core covers this query shape;
# architecture.md §1 permits any script to spawn git/gh directly via pipelib.process.run).
# ---------------------------------------------------------------------------


def _list_remote_branches_with_sha(root, pattern):
    """`git ls-remote --heads origin <pattern>` -> sorted list of `{"branch", "sha"}` dicts (the
    `refs/heads/` prefix stripped from the branch name). Unlike `prep_resolver._list_remote_branches`
    (which discards the sha), this module needs the epic branch's head sha as a first-class fact
    ("epic branch + its head SHA" — this step's Work list) — `git ls-remote` already reports it, so
    no second call/workspace-ensure is needed just to learn it.
    """
    result = process.run(["git", "ls-remote", "--heads", "origin", pattern], cwd=str(root))
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    entries = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.startswith("refs/heads/"):
            entries.append({"branch": ref[len("refs/heads/") :], "sha": sha})
    return sorted(entries, key=lambda e: e["branch"])


def _ambient_branch(path):
    """The ambient checkout's branch name, or `None` when it cannot be read — not a repo, a
    detached HEAD, or any git failure.

    Deliberately non-raising, unlike `workspace._current_branch`. This fact can only ever ADD the
    `story-own-branch` row, so its absence must degrade to today's behaviour and never to a crash
    on a path that reads no checkout today (`--refresh`, which skips the grounding assertion
    entirely and may run from anywhere)."""
    result = process.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(path))
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return None if branch in ("", "HEAD") else branch


def _remote_tip_sha(root, branch):
    """`origin/<branch>`'s tip sha, or `None` when the remote has no such branch.

    Matched EXACTLY: `git ls-remote --heads origin main` also matches `refs/heads/foo/main`, so the
    result is filtered on the full branch name rather than trusted as a single-row answer."""
    for entry in _list_remote_branches_with_sha(root, "refs/heads/%s" % branch):
        if entry["branch"] == branch:
            return entry["sha"]
    return None


def _discover_epic_branch(root, epic_number):
    """Discover `epic/<epic_number>-*` on origin. Returns `(facts_dict, decision_or_none)`:
      - zero matches -> `{"match_count": 0, "branch": None, "sha": None}` (bootstrap — the planner
        never creates the branch itself, so no bootstrap-slug fact is needed here the way
        `prep_resolver.py`'s identical helper computes one for branch NAMING).
      - one match -> `{"match_count": 1, "branch": <name>, "sha": <head-sha>}`.
      - multiple matches -> `(None, AMBIGUOUS decision)` (context lists every candidate).
    """
    matches = _list_remote_branches_with_sha(root, _EPIC_BRANCH_LS_REMOTE_PATTERN % epic_number)
    if len(matches) == 0:
        return {"match_count": 0, "branch": None, "sha": None}, None
    if len(matches) == 1:
        return {"match_count": 1, "branch": matches[0]["branch"], "sha": matches[0]["sha"]}, None
    return None, needs_decision(
        AMBIGUOUS,
        summary="%d candidate epic branches match 'epic/%s-*' on origin — expected at most one"
        % (len(matches), epic_number),
        context={"epic_number": epic_number, "candidates": [m["branch"] for m in matches]},
        options=[
            "pick the canonical branch and re-run with it recorded",
            "delete or rename the orphaned/duplicate branch, then re-run",
        ],
    )


# The story's parent-epic lookup — ONE implementation, shared with prep_resolver (the
# `prep_resolver.py` alias precedent) and, through `resolve_parent_epic_branch`, prep_workspace_open.
# Native `parent` first; the legacy tier keeps only an epic whose `## Stories` checklist lists the
# story. This module carried a byte-for-byte copy until 4.33.1, which is how the mention-is-parent
# bug lived in two places.
_search_parent_epic = branching.search_parent_epic


def _epic_story_set(sub_issues, issue_body, repo, cwd=None):
    """The epic's story set (`story_set.read`), each filed checklist-only story's live state fetched
    so the flow can reconcile body checkboxes against reality. `stories_source` reads the checklist
    half as every parsed bullet, and an epic with no stories as `checklist`. Returns
    `(story_entries, stories_source, decision_or_none)`."""
    stories, decision = story_set.read(
        sub_issues, issue_body, fetch_state=lambda number: _fetch_story_state(repo, number, cwd=cwd)
    )
    if decision is not None:
        return None, None, decision
    story_entries = [
        {
            "number": e["number"],
            "title": e["title"],
            "checked": e["checked"],
            "state": e["state"],
            "live_title": e["live_title"],
        }
        for e in stories["native"] + stories["checklist"]
    ]
    stories_source = story_set.source(stories["native"], stories["checklist_raw"]) or story_set.SOURCE_CHECKLIST
    return story_entries, stories_source, None


def _fetch_story_state(repo, story_number, cwd=None):
    """`gh issue view <NN> --json state,title,labels` — the per-filed-story live-state fetch v1's
    `GATHER_EPIC` performs so the caller can reconcile body checkboxes against reality
    (the v1 executor agent's `GATHER_EPIC` description). Returns `(state_dict, decision_or_none)`."""
    result = process.run(
        ["gh", "issue", "view", str(story_number), "--repo", repo, "--json", "state,title,labels"],
        cwd=cwd,
    )
    if result.auth_required:
        return None, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    return {
        "state": data.get("state"),
        "title": data.get("title"),
        "labels": [label.get("name") for label in data.get("labels") or []],
    }, None


# ---------------------------------------------------------------------------
# Deliverable sub-issues (#18) — the live slice set and the plan-versus-live diff.
#
# A NON-EPIC target's sub-issues are its deliverable slices by construction: the hierarchy is
# epic -> story -> slice, so no label and no per-child classification fetch is needed (an epic's
# sub-issues are stories, and an epic plan carries no `## Phases` at all).
#
# `gh issue view --json subIssues` serializes a FIXED node shape (`{id, number, state, title,
# url}`) with no sub-selection syntax, so it can supply neither a per-child timestamp nor a body.
# The REST list-sub-issues endpoint returns full Issue objects instead — one paginated call for the
# whole set rather than one call per child, and it carries the bodies the rescope prompt needs to
# be actionable (the planner has no scriptless raw-`gh` executor, so a "re-read it" note the prompt
# cannot act on would be useless).
# ---------------------------------------------------------------------------


# Non-blocking: the REST list-sub-issues endpoint is unavailable on this host, so the fixed
# `subIssues` node data already in hand is the fallback — numbers/titles/states survive, per-child
# timestamps and bodies do not (notices are open by contract, pipelib/decisions.py).
SUBISSUE_DETAIL_UNSUPPORTED = "SUBISSUE_DETAIL_UNSUPPORTED"

# Non-blocking, both emitted only on the own-branch probe's fall-through paths (module docstring
# row 4). The run that emits either ALSO ends `needs_decision` with no facts block — the
# `WORKSPACE_MISMATCH` card is built generically inside `workspace._build_attach` and forwarded
# verbatim, so prep cannot enrich its `options`. `_forward_decision(..., notices=notices)` carries
# these into that envelope, making them the only machine-readable channel that survives to say WHY
# the issue's own worktree was refused.
#
# The ambient checkout IS this issue's own branch, but its HEAD does not contain the base ref's tip
# — behind, or diverged. Grounding falls back to the base ref, which this checkout is not. Remedy:
# rebase this branch onto `origin/<base_ref>`. (Named STORY_BRANCH_BEHIND_BASE_TIP until the row
# widened past stories in 4.33.0; notices are an open set, and no prompt reads the old name.)
OWN_BRANCH_BEHIND_BASE_TIP = "OWN_BRANCH_BEHIND_BASE_TIP"

# Same fall-through, different cause: the base tip commit is not in this checkout's object DB (it
# was never fetched — typically another machine pushed it), so containment is UNANSWERABLE here.
# Never escalates and never triggers a fetch; a prep does not mutate the operator's checkout to
# answer a question.
BASE_TIP_CONTAINMENT_UNAVAILABLE = "BASE_TIP_CONTAINMENT_UNAVAILABLE"

# The own-branch row fired, and the closed-PR lookup that says whether that branch heads a closed,
# unmerged PR (a Start-fresh's predecessor) could not be answered. Only the attention line naming
# that PR is lost: the row stands, because re-grounding in place on such a branch is the operator's
# chosen behaviour, and a convenience fact must never stop a run that grounds fine without it.
CLOSED_PR_LOOKUP_UNAVAILABLE = "CLOSED_PR_LOOKUP_UNAVAILABLE"

# The grounding checkout's tracked-file listing (#181) could not be read. `facts.grounding.files_path`
# is then `null`, and S7's `parse.py phases` runs without `--files`: its plan-path check keeps its
# grammar findings and loses only the bare-basename one. Never a decision.
GROUNDING_FILES_UNAVAILABLE = "GROUNDING_FILES_UNAVAILABLE"


def _fetch_sub_issue_details(repo, issue_number, env=None, cwd=None):
    """`gh api --paginate repos/<owner>/<repo>/issues/<N>/sub_issues` — the full Issue object for
    every sub-issue in ONE call. Returns `(objects, notices, decision_or_none)`; `objects` is
    `None` when the endpoint is unavailable, which is a degradation (notice), not a failure.
    """
    result = process.run(
        ["gh", "api", "--paginate", "repos/%s/issues/%s/sub_issues" % (repo, issue_number)],
        env=env,
        cwd=cwd,
    )
    if result.auth_required:
        return None, [], needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        # A host that doesn't serve the endpoint (older GHES, a token without the scope) answers
        # 404/410 — degrade to the node data rather than failing a planning session over a fact
        # that only enriches the reconciliation. Anything else is still a hard failure. Matched on
        # the HTTP status only: bare "not found"/"gone" prose would also swallow a wrong-repo or
        # revoked-scope failure, and the caller has already read this very issue through gh_gather,
        # so a status here really does mean the endpoint.
        stderr = (result.stderr or "")
        if "404" in stderr or "410" in stderr:
            return None, [SUBISSUE_DETAIL_UNSUPPORTED], None
        sys.stderr.write(result.stderr)
        sys.exit(1)

    # Same one-or-more-concatenated-top-level-arrays tolerance as
    # gh_gather._fetch_paginated_comments: `gh api --paginate` merges pages in the common case, but
    # its documented contract is one array per page.
    decoder = json.JSONDecoder()
    text = result.stdout
    pos, length, objects = 0, len(result.stdout), []
    while pos < length:
        while pos < length and text[pos] in " \t\r\n":
            pos += 1
        if pos >= length:
            break
        page, pos = decoder.raw_decode(text, pos)
        objects.extend(page)
    return objects, [], None


def _parse_iso8601(text):
    """Parse a GitHub timestamp into a comparable `datetime`, or `None`. Compared as *datetimes*, not
    strings: github.com always emits `…Z`, but a GHES or proxy emitting `+00:00` would invert a raw
    string comparison silently (`"+"` sorts below `"Z"`), turning rescope detection off with no
    signal at all."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_slice_set(sub_issue_nodes, detail_objects, plan_updated_at, scratch_dir):
    """Assemble `slices.set` — one entry per live sub-issue, in the sub-issue panel's own order
    (`position`, the sequencing source of truth). `detail_objects` is the REST enrichment or `None`
    when unavailable, in which case the node data alone is carried.

    `maybe_rescoped` is a SUSPICION, never proof: an issue's `updated_at` bumps on comments, labels
    and assignment as well as a body rewrite, so it over-reports — and never under-reports while the
    timestamp is present and parseable. Tightening the comparison is not a fix; the prompt wording
    ("may have changed — re-read it") is what carries the uncertainty.
    """
    by_number = {}
    for obj in detail_objects or []:
        if obj.get("number") is not None:
            by_number[int(obj["number"])] = obj

    plan_stamp = _parse_iso8601(plan_updated_at)
    entries = []
    position = -1
    for node in sub_issue_nodes or []:
        number = node.get("number")
        if number is None:
            continue
        # Position is assigned over the KEPT entries: a node with no number cannot appear in a
        # phase map at all, and consuming its index would leave a hole in the very ordering
        # `order_changed` compares against.
        position += 1
        number = int(number)
        detail = by_number.get(number) or {}
        # REST states are lowercase (`open`), the GraphQL node's are upper (`OPEN`). Normalize to
        # the node spelling, which is what every other fact in this block already carries.
        state = detail.get("state") or node.get("state") or ""
        updated_at = detail.get("updated_at")
        stamp = _parse_iso8601(updated_at)
        entry = {
            "number": number,
            "title": detail.get("title") or node.get("title"),
            "state": (state or "").upper(),
            "position": position,
            "url": node.get("url") or detail.get("html_url"),
            "updated_at": updated_at,
            "maybe_rescoped": bool(stamp and plan_stamp and stamp > plan_stamp),
        }
        if detail_objects is not None:
            # force_path: a target with ten small sub-issues would otherwise put ten inline bodies
            # in the envelope. Uniform paths keep the envelope's size independent of child count.
            entry.update(
                spill_bytes(
                    (detail.get("body") or "").encode("utf-8"),
                    "body",
                    scratch_dir,
                    filename="slice-%d-body.md" % number,
                    force_path=True,
                )
            )
        entries.append(entry)
    return entries


def _build_slice_diff(slice_set, prior_phases, prior_phases_parsed, rescope_basis):
    """Compute the plan-versus-live diff from the live slice set and the PRIOR plan's parsed
    `## Phases`. Pure — no I/O, no gh, no git.

    Three states leave `computed: False` with every case list empty, because a finding *about a phase
    map* cannot exist before one does — each is reported as its own fact so a consumer never has to
    guess which it is: no prior plan at all (`prior_phases is None`, the fresh path), a prior plan
    whose `## Phases` did not parse (`prior_phases_parsed: False`), and a prior plan that carries no
    `## Phases` section (`prior_phases_present: False` — a legitimate single-phase plan; reporting
    every open sub-issue as "unserved" against a plan that has no phases would gate on a shape the
    reviewer is never asked to check). The coverage obligation itself still binds in all three — it is
    carried by the playbook and reviewer Dimension 7, not by this diff.
    """
    diff = {
        "computed": False,
        "prior_phases_parsed": prior_phases_parsed,
        "prior_phases_present": bool(prior_phases),
        "rescope_basis": rescope_basis,
        "mapped": [],
        "substrate_phases": [],
        "unmapped_phases": [],
        "closed": [],
        "removed": [],
        "rescoped": [],
        "order_changed": [],
        "uncovered_open": [],
    }
    if not prior_phases:
        return diff
    diff["computed"] = True

    live_by_number = {entry["number"]: entry for entry in slice_set}
    # Not-CLOSED rather than == OPEN: an unknown state (a node the detail fetch didn't cover, a host
    # spelling it differently) must fail SAFE — surfaced as uncovered, never silently dropped from
    # the set the map has to cover.
    open_numbers = [e["number"] for e in slice_set if e["state"] != "CLOSED"]
    position = {e["number"]: e["position"] for e in slice_set}

    served = []
    for phase in prior_phases:
        sub_issue = phase.get("sub_issue")
        if sub_issue == "(none)":
            diff["substrate_phases"].append(phase["number"])
        elif sub_issue is None:
            diff["unmapped_phases"].append(phase["number"])
        else:
            diff["mapped"].append({"phase": phase["number"], "sub_issue": sub_issue})
            served.append(sub_issue)

    served_set = set(served)
    # ONE key for one fact. A genuinely *newly added* sub-issue is indistinguishable from one the
    # plan simply never mapped: no prior snapshot of the set exists anywhere, so "open and unserved"
    # is the whole observable truth. Two keys carrying it would invite a consumer to double-report.
    diff["uncovered_open"] = [n for n in open_numbers if n not in served_set]
    diff["closed"] = sorted(
        {n for n in served_set if (live_by_number.get(n) or {}).get("state") == "CLOSED"}
    )
    diff["removed"] = sorted(n for n in served_set if n not in live_by_number)
    # A CLOSED sub-issue is excluded from `rescoped` deliberately: closing an issue bumps its
    # `updated_at`, so without this every closure would ALSO read as a rescope — and since `rescoped`
    # gates while `closed` does not, the non-gating case would be unreachable in production. A closed
    # sub-issue is governed by the shipped-phase rules, one disposition per event.
    diff["rescoped"] = sorted(
        n
        for n in served_set
        if (live_by_number.get(n) or {}).get("maybe_rescoped")
        and (live_by_number.get(n) or {}).get("state") != "CLOSED"
    )

    # `depends-on` versus the panel order. Surfaced, never corrected: disagreeing with the panel can
    # be a deliberate call (an ordering-only dependency), so it is the operator's finding to judge.
    by_phase_number = {p["number"]: p for p in prior_phases}
    seen_pairs = set()
    for phase in prior_phases:
        later = phase.get("sub_issue")
        if not isinstance(later, int) or later not in position:
            continue
        depends_on = phase.get("depends_on")
        if not isinstance(depends_on, list):
            continue
        for earlier_phase_number in depends_on:
            earlier_phase = by_phase_number.get(earlier_phase_number)
            if earlier_phase is None:
                continue
            earlier = earlier_phase.get("sub_issue")
            if not isinstance(earlier, int) or earlier not in position or earlier == later:
                continue
            if position[later] < position[earlier]:
                # One row per disagreeing sub-issue PAIR, not per dependency edge: two phases both
                # serving the earlier sub-issue would otherwise emit two rows — and two near-identical
                # attention lines — for one panel-order disagreement.
                if (later, earlier) in seen_pairs:
                    continue
                seen_pairs.add((later, earlier))
                diff["order_changed"].append(
                    {
                        "phase": phase["number"],
                        "depends_on_phase": earlier_phase_number,
                        "sub_issue": later,
                        "after_sub_issue": earlier,
                        # The panel's actual order for the disagreeing pair. Reaching here MEANS
                        # `later` sits first, so that is the order — stated as data so the prompt
                        # quotes it rather than re-deriving it from `position`.
                        "live_order": [later, earlier],
                    }
                )
    return diff


def _slice_attention(slices_facts, target_number):
    """The `attention` lines for the slice facts — one per non-empty diff case, plus the availability
    degradations. Findings ride in facts + `attention`; nothing is re-derived in a prompt."""
    lines = []
    diff = slices_facts["diff"]
    by_number = {e["number"]: e for e in slices_facts["set"]}

    if not slices_facts["detail_available"]:
        lines.append(
            "sub-issue detail is unavailable on this host (%s) — numbers, titles and states are "
            "from the sub-issue nodes; no bodies and no rescope detection"
            % SUBISSUE_DETAIL_UNSUPPORTED
        )
    if not diff["prior_phases_parsed"]:
        lines.append(
            "the prior plan's '## Phases' section does not parse — no plan-versus-live sub-issue "
            "diff was computed; this revise re-authors that section, which repairs it"
        )
    elif slices_facts["rescope_basis"] == "no_prior_plan":
        lines.append(
            "no prior plan, so there is no plan-versus-live sub-issue diff — cut '## Phases' against "
            "the live set from the start; the coverage rule still binds"
        )
    elif not diff["prior_phases_present"]:
        lines.append(
            "the prior plan carries no '## Phases' section while %d sub-issue(s) are open — a sliced "
            "target needs a phase map; no diff was computed against a plan that has no phases"
            % slices_facts["open_count"]
        )
    if slices_facts["rescope_basis"] == "unavailable" and diff["prior_phases_present"]:
        lines.append(
            "rescope detection is unavailable — a sub-issue body rewritten since the plan was posted "
            "will NOT be flagged; re-read any sub-issue whose scope you rely on"
        )
    # A served sub-issue with no usable timestamp is a hole in an otherwise-working comparison: the
    # basis says `updated_at`, so silence would read as "nothing changed" (the one thing the
    # suspicion signal must never do).
    if slices_facts["rescope_basis"] == "updated_at":
        served = {row["sub_issue"] for row in diff["mapped"]}
        blind = sorted(
            n
            for n in served
            if n in by_number and not _parse_iso8601(by_number[n].get("updated_at"))
        )
        if blind:
            lines.append(
                "no usable timestamp for sub-issue(s) %s — they are NOT covered by rescope "
                "detection even though the rest of the set is; re-read them"
                % ", ".join("#%d" % n for n in blind)
            )

    for number in diff["uncovered_open"]:
        lines.append(
            "sub-issue #%d is open and no phase in the prior plan serves it — re-cut phases to "
            "cover it, record it out-of-scope with a disposition, or route the sub-issue set back "
            "to whoever authors it" % number
        )
    for number in diff["removed"]:
        lines.append(
            "sub-issue #%d is no longer a sub-issue of #%s (removed or re-parented) but a prior "
            "phase serves it" % (number, target_number)
        )
    for number in diff["rescoped"]:
        entry = by_number.get(number) or {}
        lines.append(
            "sub-issue #%d may have changed since the plan was posted (updated %s) — re-read it at "
            "%s; an issue's timestamp also bumps on comments and labels, so this is a prompt to "
            "look, not proof"
            % (number, entry.get("updated_at"), entry.get("body_path") or "its body")
        )
    for number in diff["closed"]:
        lines.append(
            "sub-issue #%d is CLOSED — the phase serving it behaves like a shipped phase (the "
            "shipped-phase rules in references/revise-reconciliation.md govern; there is no second "
            "rule set)" % number
        )
    for row in diff["order_changed"]:
        lines.append(
            "phase %d (sub-issue #%d) depends on phase %d (sub-issue #%d), but the sub-issue panel "
            "lists them in the order %s — surfaced, not corrected: an ordering-only dependency can "
            "be a deliberate call"
            % (
                row["phase"],
                row["sub_issue"],
                row["depends_on_phase"],
                row["after_sub_issue"],
                ", ".join("#%d" % n for n in row["live_order"]),
            )
        )
    return lines


# ---------------------------------------------------------------------------
# `## Stories` section parsing (epic body) — no shared parse.py subcommand covers this grammar.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# `## Phase tracker` section parsing (PR body, revise mode only) — the parser lives in `parse.py`
# because `prep_resolver` needs it too (continue mode reconciles the tracker's row set against the
# plan's `## Phases`). Aliased rather than re-implemented so a third copy cannot drift into being.
# ---------------------------------------------------------------------------

_parse_phase_tracker = parse.parse_phase_tracker


# ---------------------------------------------------------------------------
# Thread scanning — research dossier + epic delivery log are both located by scanning the ALREADY-
# fetched thread from a `gh_gather.run` call, never a second `gh api` round-trip (architecture.md
# §2's in-process composition applied to "read what you already fetched", not just "call the pure
# core").
# ---------------------------------------------------------------------------


# The thread read-back lives in `pipelib.thread` (three preps scan threads; one home).
_load_thread = load_thread


def _marker_comment_id(comment):
    """A thread-located marker comment's REST **numeric** id (``databaseId``).

    A comment found by scanning the normalized thread carries the GraphQL node id as its ``id``
    (`gh_gather._normalize_comment` mirrors the `gh issue view` shape), and the one thing a caller
    ever does with a marker id is delete it on replacement — over REST, where a node id is always a
    404 (#34). `gh_gather`'s own `marker_prefix` lookup reads the numeric id off the raw objects for
    exactly this reason; a thread scan has to reach for `databaseId` to land in the same id space.
    Falls back to `id` so a caller reading a thread from some other source still gets an id rather
    than `None`.
    """
    if comment is None:
        return None
    return comment.get("databaseId", comment.get("id"))


def _find_one_marker(thread_list, prefix, context_label):
    """Locate the (at most one) trusted-author comment in `thread_list` whose body starts with
    `prefix`. Returns `(comment_or_none, untrusted_count, decision_or_none)` — `untrusted_count` the
    matching comments an untrusted author wrote, skipped (#179); more than one trusted match is
    `MARKER_AMBIGUOUS`
    (architecture.md §3: "the gathers + config_block.py" are its CANONICAL emitters, but the code's
    own meaning — "more than one candidate marker comment ... where the contract expects one" —
    applies identically to a marker this module locates via its own thread scan rather than through
    `gh_gather`'s own single-`marker_prefix` lookup; `gh_pr_gather.py` independently applies the
    same code to its own thread scan, so this is an established pattern, not a novel one).
    """
    # Only a trusted author's marked comment counts (pipelib.trust, #179), so the duplicate check runs
    # over the trusted subset and an untrusted comment is only counted.
    matches, untrusted = trust.partition_trusted(
        [c for c in thread_list if (c.get("body") or "").startswith(prefix)]
    )
    if len(matches) > 1:
        return None, untrusted, needs_decision(
            MARKER_AMBIGUOUS,
            summary="%d comments match the %s marker prefix %r — expected at most one"
            % (len(matches), context_label, prefix),
            context={
                "marker_prefix": prefix,
                # `databaseId`, never the thread's `id` — see `_marker_comment_id`.
                "comment_ids": [_marker_comment_id(m) for m in matches],
                "comment_urls": [m.get("url") for m in matches],
            },
            options=[
                "inspect each comment and pick the one to treat as current",
                "delete the stale duplicate comment(s), then re-run",
            ],
        )
    return (matches[0] if matches else None), untrusted, None


def _stage_comment_body(comment, scratch_dir, filename):
    """Stage `comment`'s body through the standard inline-vs-path threshold (`pipelib.spill`),
    using section name `"body"` so the returned keys (`body_mode`/`body_bytes`/`body_path`/`body`)
    match the naming convention this module's `plan`/`research`/`epic_plan` sub-objects already use
    for their own staged bodies — a caller reads `<fact>.body_mode` uniformly regardless of which
    section it is."""
    body = (comment.get("body") or "") if comment is not None else ""
    return spill_bytes(body.encode("utf-8"), "body", scratch_dir, filename=filename)


def _read_shipped_records(thread_list, revise_facts, plan_body, scratch_dir, issue_number, notices):
    """``facts.plan.shipped`` — the legacy shipped-phase records this plan still points at
    (skills/_shared/plan-shipped-phases.md). Returns ``(facts, notes, decision)``.

    Nothing writes a record any more: the build sections live in their own part comment
    (skills/_shared/plan-parts.md), which is what records were a workaround for. A plan revised
    before that still carries pointer bullets in place of moved entries, and the records are the
    only copy of them — so this revise folds every one back into the plan and drops the pointers.
    Every PR the pointers name is read, the open PR's included (``plan_shipped.read_pointed``); with
    no pointer, the open PR's records are read, so an unpointed record (a main edit that failed after
    the record posted) is still surfaced.

    A duplicated record is ``MARKER_AMBIGUOUS``, forwarded: an unknown shape for a shipped phase's
    entries is what a revise must not re-author around.
    """
    open_pr = (revise_facts or {}).get("open_pr")
    open_number = open_pr["number"] if open_pr else None
    records, notes, decision = plan_shipped.read_pointed(thread_list, plan_body, open_number)
    # The records an untrusted author wrote were skipped (pipelib.trust, #179).
    _merge_notices(
        notices,
        [trust.untrusted_notice(records["untrusted"], plan_shipped.MARKER_PREFIX, "issue #%s" % issue_number)],
    )
    if decision is not None:
        return None, notes, decision
    return plan_shipped.staged_fact(records, scratch_dir, "issue-%s-plan-shipped.md" % issue_number), notes, None


def _read_delivery_log(thread_list, scratch_dir, epic_number, notices):
    """The epic delivery log as one fact, resolved across both tiers by `delivery_log.collect`.

    Returns `(facts, decision)`. The whole log — legacy entries plus every per-story entry — is
    staged to ONE path under the same `body_*` key convention the single-comment fact used, so the
    plan reviewer's `<<epic_delivery_log>>` stays a single path and Dimension 8 needs no change.

    `entry_count` / `text_chars` ride along because the *write* is bounded per story (#41) while
    this *read* is not: the reviewer sub-agent is told to `Read` the staged file whole, so growth
    here has to stay observable rather than becoming the same unbounded artifact one level down.
    """
    log, decision = delivery_log.collect(thread_list)
    # The log comments an untrusted author wrote were skipped by `collect` (pipelib.trust, #179).
    _merge_notices(
        notices, [trust.untrusted_notice(log["untrusted"], delivery_log.MARKER_FAMILY, "epic #%s" % epic_number)]
    )
    if decision is not None:
        return {"present": False}, decision

    facts = {
        "present": log["present"],
        "log_source": log["log_source"],
        "entry_count": log["entry_count"],
        "entries": log["entries"],
    }
    if log["legacy"] is not None:
        facts["legacy_comment_id"] = log["legacy"]["comment_id"]
        facts["legacy_comment_url"] = log["legacy"]["comment_url"]
    if log["present"]:
        facts.update(
            spill_bytes(
                log["text"].encode("utf-8"),
                "body",
                scratch_dir,
                filename="epic-%s-delivery-log.md" % epic_number,
            )
        )
    return facts, None


# ---------------------------------------------------------------------------
# Grounding-doc inventory (docs/specs/planner.md Step 5's read set, now declared by the consuming
# repo rather than assumed here) — read INSIDE the already-ensured grounding workspace, never via
# `git ls-tree -r --name-only <ref>` against a bare ref (architecture.md §6/§10's ref-arithmetic
# discipline; the workspace is already checked out at the right ref, so a plain filesystem read is
# both simpler and needs no second git invocation).
# ---------------------------------------------------------------------------


def _grounding_doc_inventory(grounding_path):
    """The catalogue-declared docs at `grounding_path`, plus this read's own notices. A thin
    delegation to `doc_catalogue.read_catalogue` — kept as a named function so the call site reads
    the same as it did before the migration and so the module's own tests have a seam."""
    return doc_catalogue.read_catalogue(grounding_path)


# ---------------------------------------------------------------------------
# `plan_ref` selection — docs/specs/planner.md Step 4.5's table, moved into code (see the module
# docstring for the full row-by-row mapping and the precedence rule).
# ---------------------------------------------------------------------------

PLAN_REF_ROW_OPEN_PR_HEAD = "open-pr-head"
PLAN_REF_ROW_EPIC_BRANCH = "epic-as-target"
PLAN_REF_ROW_EPIC_BOOTSTRAP = "epic-as-target-bootstrap"
PLAN_REF_ROW_STORY_OWN_BRANCH = "story-own-branch"
PLAN_REF_ROW_STORY_PARENT_BRANCH = "story-under-open-epic"
PLAN_REF_ROW_STORY_PARENT_BOOTSTRAP = "story-parent-epic-bootstrap"
PLAN_REF_ROW_STORY_NO_PARENT = "story-no-open-parent-epic"
PLAN_REF_ROW_OWN_BRANCH = "own-branch"
PLAN_REF_ROW_DEFAULT = "no-open-pr-default-branch"
PLAN_REF_ROW_AMBIENT_ACCEPTED = "ambient-accepted"

# The rows an own-branch row may displace: every row whose `plan_ref` is a BASE the issue's own
# branch descends from — the three story rows and the standalone default. The story arm reports the
# substitution as `story-own-branch`, the default arm as `own-branch`, so a standalone issue is
# never labelled a story (the D4 row-truthfulness rule). Excluded: `open-pr-head` and the two epic
# rows, where `plan_ref` already IS the issue's own branch (or none exists yet).
ROWS_ELIGIBLE_FOR_OWN_BRANCH = frozenset(
    (
        PLAN_REF_ROW_STORY_PARENT_BRANCH,
        PLAN_REF_ROW_STORY_PARENT_BOOTSTRAP,
        PLAN_REF_ROW_STORY_NO_PARENT,
        PLAN_REF_ROW_DEFAULT,
    )
)


def _select_plan_ref(
    issue_type,
    epic_branch_name,
    open_pr_headref,
    root_branch,
    parent_epic_open=False,
    *,
    own_branch_at_base_tip=None,
    accepted_ambient=None,
):
    """Pure lookup table: `(issue_type, epic_branch_name, open_pr_headref, parent_epic_open)` ->
    `(plan_ref, plan_ref_row)`. `epic_branch_name` is the discovered branch (or `None` on
    bootstrap/no-parent); `open_pr_headref` is the target issue's own first open PR's
    `headRefName`, or `None`; `parent_epic_open` (story only — ignored for every other
    `issue_type`) distinguishes row 5 (D4 fix: an open parent whose branch hasn't bootstrapped yet)
    from row 6 (no parent, or a closed one) — both still resolve `plan_ref` to the default
    branch (`root_branch`, derived — never a hardcoded `main`), but the row
    LABEL must say which reality produced it (architecture.md §4: facts are data, never
    re-derived — including the row name itself, not just the ref value). Checked in table order —
    the open-PR-head check runs FIRST and unconditionally, so the "open-PR-head wins when more than
    one row applies" precedence rule (docs/specs/planner.md Step 4.5) falls out of the ordering
    rather than needing a separate conflict check.

    `own_branch_at_base_tip` (story arm only) is the AMBIENT checkout's branch when — and ONLY
    when — the caller has already verified BOTH of the row-4 preconditions: it is this target
    issue's own branch (`branching.branch_belongs_to_issue`) AND its HEAD already contains the tip
    of the ref rows 5/6 would have chosen (`workspace.contains_commit` returned `True`). Passing a
    branch that fails either one is a caller bug: this stays a pure table and cannot re-verify the
    second, which needs git. Keyword-only so the four-positional call form the unit tests use
    cannot silently absorb it; `parent_epic_open` deliberately stays ahead of the `*`, since
    narrowing an existing parameter's calling convention is not part of this change. The default
    arm honours it too (row `own-branch`); the caller only passes it for a base row in
    :data:`ROWS_ELIGIBLE_FOR_OWN_BRANCH`.

    `accepted_ambient` is the ambient branch the OPERATOR accepted (`--ground-ambient`) after the
    card for a checkout carrying commits beyond the base tip. It outranks every row, the open-PR
    head included: it is an explicit answer about this session's vantage, not a derived fact.
    """
    if accepted_ambient:
        return accepted_ambient, PLAN_REF_ROW_AMBIENT_ACCEPTED
    if open_pr_headref:
        return open_pr_headref, PLAN_REF_ROW_OPEN_PR_HEAD
    if issue_type == "epic":
        if epic_branch_name:
            return epic_branch_name, PLAN_REF_ROW_EPIC_BRANCH
        return root_branch, PLAN_REF_ROW_EPIC_BOOTSTRAP
    # Keyed on the epic-context facts, not the `story` label (#31): an untyped sub-issue of an open
    # epic reaches here with the same two facts set, and rows 4-7 describe its grounding exactly.
    if issue_type == "story" or epic_branch_name or parent_epic_open:
        # Row 4 — eligibility (which base rows qualify, branch ownership, tip containment) is the
        # caller's, already settled before this call; see the parameter's docstring.
        if own_branch_at_base_tip:
            return own_branch_at_base_tip, PLAN_REF_ROW_STORY_OWN_BRANCH
        if epic_branch_name:
            return epic_branch_name, PLAN_REF_ROW_STORY_PARENT_BRANCH
        if parent_epic_open:
            return root_branch, PLAN_REF_ROW_STORY_PARENT_BOOTSTRAP
        return root_branch, PLAN_REF_ROW_STORY_NO_PARENT
    if own_branch_at_base_tip:
        return own_branch_at_base_tip, PLAN_REF_ROW_OWN_BRANCH
    return root_branch, PLAN_REF_ROW_DEFAULT


def _probe_ambient(base_ref, base_row, epic_branch_facts, root, issue_number, cwd):
    """Classify the AMBIENT checkout against ``base_ref`` — the ref the row table chose before any
    substitution — by content, not by branch name. Returns ``(probe, notices)``.

    ``probe["kind"]`` is ``None`` when there is nothing to classify (no readable branch — detached
    HEAD, not a repo — or the ambient branch already IS ``base_ref``, the canonical posture, which
    therefore costs no extra call), else one of:

    - ``own`` — this issue's own branch (`branching.branch_belongs_to_issue`), ``base_row`` in
      :data:`ROWS_ELIGIBLE_FOR_OWN_BRANCH`, and HEAD contains the base tip -> the own-branch row.
    - ``identical`` — any other branch whose HEAD IS the base tip: the same code, so grounding there
      leaves ``plan_ref`` and the footer true.
    - ``foreign_ahead`` — any other branch containing the tip plus ``ahead_commits`` more: code that
      may never ship with this issue, so only the operator's ``--ground-ambient`` admits it.
    - ``not_contained`` / ``unanswerable`` — behind or diverged, or a tip this checkout never
      fetched. Both fall through to today's refusal; for the issue's own branch they carry the
      notice that says which (rebase vs fetch).

    Containment is what makes any substitution sound — a contained base tip means the checkout is a
    superset of that ref, so grounding on it sees more truth, never less. Never fetches:
    :func:`workspace.contains_commit` is tri-state, and a prep does not mutate the operator's
    checkout to answer a question.
    """
    probe = {
        "kind": None,
        "ambient_branch": None,
        "base_ref": base_ref,
        "base_tip_sha": None,
        "contains": None,
        "own": False,
        "ahead_commits": None,
    }
    probe_cwd = Path(cwd if cwd is not None else ".").resolve()
    ambient_branch = _ambient_branch(probe_cwd)
    probe["ambient_branch"] = ambient_branch
    if ambient_branch is None or ambient_branch == base_ref:
        return probe, []
    probe["own"] = bool(
        base_row in ROWS_ELIGIBLE_FOR_OWN_BRANCH
        and branching.branch_belongs_to_issue(ambient_branch, issue_number)
    )
    # The epic branch's tip is already in hand from `_discover_epic_branch`'s ls-remote; any other
    # base costs one `ls-remote` — on this non-canonical path only.
    base_tip_sha = (
        epic_branch_facts.get("sha")
        if epic_branch_facts and epic_branch_facts.get("branch") == base_ref
        else None
    ) or _remote_tip_sha(root, base_ref)
    if not base_tip_sha:
        return probe, []
    probe["base_tip_sha"] = base_tip_sha
    contains = workspace.contains_commit(probe_cwd, base_tip_sha)
    probe["contains"] = contains
    if contains is not True:
        probe["kind"] = "not_contained" if contains is False else "unanswerable"
        if not probe["own"]:
            return probe, []
        # Both end in the SAME refusal the pre-row behaviour produced; the notice is what
        # distinguishes "rebase this branch" from "this checkout has never seen that commit".
        return probe, [
            OWN_BRANCH_BEHIND_BASE_TIP if contains is False else BASE_TIP_CONTAINMENT_UNAVAILABLE
        ]
    if probe["own"]:
        probe["kind"] = "own"
        return probe, []
    count = process.run(
        ["git", "rev-list", "--count", "%s..HEAD" % base_tip_sha], cwd=str(probe_cwd)
    )
    ahead = int(count.stdout.strip()) if count.returncode == 0 else None
    probe["ahead_commits"] = ahead
    probe["kind"] = "identical" if ahead == 0 else "foreign_ahead"
    return probe, []


def _closed_pr_for_branch(repo, issue_number, branch, cwd):
    """The closed, UNMERGED PR of this issue whose head is ``branch`` — a Start-fresh's predecessor
    — or ``None``. Returns ``(pr_or_none, notices)``. Asked only when the own-branch row fired, so a
    canonical run pays nothing; any failure degrades to :data:`CLOSED_PR_LOOKUP_UNAVAILABLE`."""
    prs, decision = branching.search_closed_prs(repo, issue_number, cwd=cwd, fatal=False)
    if decision is not None or prs is None:
        return None, [CLOSED_PR_LOOKUP_UNAVAILABLE]
    for pr in branching.prior_prs_for_issue(prs, issue_number):
        if pr.get("headRefName") == branch and pr.get("state") != "MERGED":
            return pr, []
    return None, []


def _foreign_checkout_decision(probe, plan_ref, root, root_branch, cwd):
    """The card for a checkout carrying commits beyond ``plan_ref``'s tip that is not this issue's
    own branch. Built here rather than by :func:`workspace._build_attach` because only prep knows
    the containment facts and the ``--ground-ambient`` remedy; the reason stays ``branch_mismatch``,
    so the closed attach-reason set is unchanged."""
    branch = probe["ambient_branch"]
    ahead = probe["ahead_commits"]
    probe_cwd = str(Path(cwd if cwd is not None else ".").resolve())
    top = process.run(["git", "rev-parse", "--show-toplevel"], cwd=probe_cwd)
    elsewhere = (
        "start this session in a current %s checkout (the project root serves) instead" % root_branch
        if plan_ref == root_branch
        else "start this session inside the worktree for %r instead" % plan_ref
    )
    return workspace._workspace_mismatch(
        "branch_mismatch",
        summary="session checkout is on branch %r, which contains %r's tip plus %s commit(s) that "
        "%r does not have" % (branch, plan_ref, "more" if ahead is None else ahead, plan_ref),
        context={
            "path": top.stdout.strip() if top.returncode == 0 else probe_cwd,
            "actual_branch": branch,
            "expected_branch": plan_ref,
            "plan_ref": plan_ref,
            "base_tip_sha": probe["base_tip_sha"],
            "ahead_commits": ahead,
            "main_root": root,
        },
        options=[
            "plan against this checkout anyway: re-run prep with --ground-ambient (carry it on every "
            "later prep run this session, --refresh included); the plan footer records %r, and the "
            "plan may cite code from those commits that %r does not have" % (branch, plan_ref),
            elsewhere,
            "if this checkout is intentional, re-run against the issue this branch belongs to",
        ],
    )


# ---------------------------------------------------------------------------
# Bug (a) — the deterministic open-question tracker de-dup search. S14-promoted to `oq_tracker.py`
# (see the module docstring's "S14 promotion" paragraph) — `build_oq_query` is kept as a
# module-level alias so `prep_planner.build_oq_query(...)` (this module's own CLI `--oq-query` call
# site, and tests/test_prep_planner.py's one direct-call test line) keeps working unchanged.
# ---------------------------------------------------------------------------

build_oq_query = oq_tracker.build_oq_query


# ---------------------------------------------------------------------------
# Revise facts (mode == "revise")
# ---------------------------------------------------------------------------


def _extract_plan_sha(plan_body):
    """Extract the short/long SHA from the plan's header line: '... planned <ISO> at
    `<plan-ref>@<short-sha>`' (skills/planner/references/plan-schema.md). Returns
    `None` if the header doesn't match the documented shape rather than guessing."""
    match = re.search(r"@`?([0-9a-f]{7,40})`?", plan_body or "")
    return match.group(1) if match else None


def _build_revise_facts(issue_envelope, open_prs, plan_sha, grounding_sha, repo, scratch_dir, cwd=None):
    """Assemble the revise-mode-only facts (docs/specs/planner.md Revise mode step 1): the prior
    plan body's already-staged path (reused from the target's own `GATHER_ISSUE` marker fetch — no
    second read), the prior plan's SHA and the current grounding sha side by side (BOTH raw facts,
    no drift judgment computed here — the spec's own words: "no judgment, just both SHAs"), and,
    when an open PR exists, that PR's body + its parsed `## Phase tracker`. Returns `(revise_facts,
    notices, decision_or_none)`.
    """
    revise = {"prior_plan_sha": plan_sha, "grounding_sha": grounding_sha}
    notices = []

    if open_prs:
        pr = open_prs[0]
        pr_facts, pr_notices, decision = gh_pr_gather.build_pr_facts(
            pr["number"], repo, scratch_dir=scratch_dir, cwd=cwd
        )
        if decision is not None:
            return None, notices, decision
        _merge_notices(notices, pr_notices)
        pr_body = pr_facts.get("body")
        if pr_body is None and pr_facts.get("body_mode") == "path":
            pr_body = Path(pr_facts["body_path"]).read_text(encoding="utf-8")
        open_pr_fact = {
            "number": pr_facts["number"],
            "headRefName": pr_facts["headRefName"],
            "url": pr_facts.get("url"),
            "isDraft": pr_facts.get("isDraft"),
            "body_mode": pr_facts.get("body_mode"),
        }
        if pr_facts.get("body_mode") == "path":
            open_pr_fact["body_path"] = pr_facts.get("body_path")
        # The `## Polish` ledger (skills/_shared/polish-ledger.md): on a revise the evaluator
        # routed here, its `apply` entries are the work the new phase absorbs — and an inline PR
        # body is otherwise dropped, so this is the planner's only view of them.
        open_pr_fact["polish"] = parse.scan_polish(pr_body)
        revise["open_pr"] = open_pr_fact
        revise["phase_tracker"] = _parse_phase_tracker(pr_body)
    else:
        revise["open_pr"] = None
        revise["phase_tracker"] = []

    return revise, notices, None


# ---------------------------------------------------------------------------
# suggested_playbook / attention
# ---------------------------------------------------------------------------


def _suggested_playbook(issue_type, mode, parent_epic_open=False):
    """Map `(issue_type, mode, parent_epic_open)` to the suggested playbook filename
    (architecture.md §5: "Prep proposes; the router confirms"). The four real S13 playbook names
    (`docs/specs/planner.md`; `skills/planner/SKILL.md` §2's routing table):

      - ``revise.md`` — ``mode == "revise"`` (a prior plan comment on the TARGET issue), for a
        standalone issue OR an epic; revise is its own flow parameterized by the type FACTS
        (architecture.md §5 "revise is a distinct action flow: reconcile old-vs-new plan +
        projected-DoD, diff-show, SOFT/HARD gate, ## Predecessor").
      - ``story-jit.md`` — a story under an OPEN parent epic. Owns BOTH the fresh and the revise
        path for such a story (v1's "don't branch to Revise mode here — it's handled
        by Just-in-time story planning"), so it wins over `revise.md` even when the story already
        has a plan.
      - ``epic.md`` — a fresh epic-as-target run.
      - ``single.md`` — everything else fresh: a standalone bug/feature/incomplete/multi-phase
        issue, or a story with no open parent epic (v1's "Everything else …
        continue with Steps 4–10").

    ``mode`` is the `fresh`/`revise` value `build_facts` already derived; ``parent_epic_open`` is
    whether a story's parent epic was found OPEN (``False`` for every non-story type). The
    ordering encodes the precedence: the story-under-open-epic short-circuit runs before the
    revise check, matching v1's Step-2 exception.
    """
    # `parent_epic_open` alone (#31): it is set only by the parent-lookup arm, which now admits an
    # untyped sub-issue of an open epic — and that target wants the same just-in-time plan against
    # current epic HEAD as its `story`-labelled siblings.
    if parent_epic_open:
        return "story-jit.md"
    if mode == "revise":
        return "revise.md"
    if issue_type == "epic":
        return "epic.md"
    return "single.md"


def _build_attention(
    open_question_candidates,
    epic_facts,
    story_facts,
    grounding_docs=None,
    catalogue_absent=False,
    grounding_basis="plan_ref",
    probe=None,
    plan_ref=None,
    closed_pr=None,
):
    """`grounding_docs` is `None` when the catalogue was never read (`--refresh`, which skips the
    whole grounding assertion), `[]` when it was read and declared nothing, and a list otherwise —
    three distinct states, because "we didn't look" must not render as "the repo declared nothing".

    `grounding_basis` / `probe` / `plan_ref` / `closed_pr` word the vantage lines: whenever the
    checkout is not `plan_ref`'s own branch, say which checkout was read and why that is honest —
    and never claim "grounding at main" while the plan grounds elsewhere.
    """
    attention = []
    if grounding_docs is not None:
        if catalogue_absent:
            attention.append(
                "no doc catalogue at the grounding ref — planning ungrounded; run "
                "/ctrl-freaq:setup to declare this repo's grounding docs in docs/README.md"
            )
        elif not grounding_docs:
            attention.append(
                "doc catalogue declares no documents — planning ungrounded until it names some"
            )
        for missing in doc_catalogue.missing_entry_paths(grounding_docs):
            attention.append(
                "doc catalogue names '%s', absent at the grounding ref — a stale entry, or a doc "
                "this branch has not merged yet" % missing
            )
    for group in open_question_candidates or []:
        attention.append(
            "open question '%s' has %d tracker candidate(s) — do not record it as (not filed)"
            % (group["oq_id"], len(group["candidates"]))
        )
    if epic_facts is not None:
        branch = epic_facts.get("branch") or {}
        if branch.get("match_count", 0) == 0:
            attention.append(
                "no epic integration branch exists yet on origin — grounding at main until bootstrap"
            )
        # Reads the RESOLVED log (both tiers), never one tier's marker: an epic whose entries are
        # all per-story comments has a complete log, and a v1-only lookup here would report "no
        # story has merged" while nine had (#41). The claim is about merges, so it has to be
        # derived from the same union the reader grounds on.
        if not (epic_facts.get("delivery_log") or {}).get("present", False):
            attention.append("no epic delivery-log entries yet — no story has merged")
    probe = probe or {}
    base_ref = probe.get("base_ref")
    if grounding_basis == "own_branch":
        attention.append(
            "grounding on this issue's own branch '%s' — it already contains '%s' tip, so the plan "
            "footer records '%s', not '%s'" % (plan_ref, base_ref, plan_ref, base_ref)
        )
        if closed_pr is not None:
            attention.append(
                "grounding on '%s', the head of closed PR #%s — its commits are visible to this plan"
                % (plan_ref, closed_pr.get("number"))
            )
    elif grounding_basis == "identical":
        attention.append(
            "grounding from the checkout of '%s', identical to '%s' tip — the plan footer records '%s'"
            % (probe.get("ambient_branch"), plan_ref, plan_ref)
        )
    elif grounding_basis == "accepted":
        attention.append(
            "grounding on '%s' by the operator's answer — its %s commit(s) beyond '%s' tip are "
            "visible to this plan and may not ship with this issue"
            % (plan_ref, probe.get("ahead_commits"), base_ref)
        )
    at_main = grounding_basis in ("plan_ref", "identical")
    if story_facts is not None:
        if story_facts.get("parent_epic") is None:
            attention.append(
                "no parent epic found referencing this story"
                + (" — grounding at main" if at_main else "")
            )
        elif not story_facts.get("parent_epic_open"):
            attention.append(
                "parent epic is closed"
                + (" — grounding at main, not the epic branch" if at_main else "")
            )
    return attention


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def _extract_body(envelope, key):
    body = envelope.get(key)
    if body is None and envelope.get("%s_mode" % key) == "path":
        body = Path(envelope["%s_path" % key]).read_text(encoding="utf-8")
    return body or ""


def _build_dod_facts(issue_body, scratch_dir):
    """`facts.dod` — the issue DoD's shape (`parse.dod_summary`: `present` / `count` /
    `other_checklists`) plus `body_path`, the issue body staged at a FIXED scratch path.

    `body_path` is ALWAYS a file, unlike `sections.issue_body`, which is inline when small. Two
    consumers need a path: the spine's S7 `parse.py phases --issue-body` (the `closes-dod` coverage
    check), and the session's issue-body edits — the S1 DoD-heading rename edits this file and S8's
    pointer staging starts from it, so a whole-body `edit-body` never writes the session-start body
    back over a rename made minutes earlier.

    Lenient by design: `dod_summary` never parses annotations, so a malformed one (which the
    resolver and evaluator refuse on) leaves this fact intact — a revise run is how annotations get
    repaired, the same posture as the prior plan's best-effort `## Phases` parse.
    """
    body_path = Path(scratch_dir) / "issue-body.md"
    with open(body_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(issue_body)
    dod = parse.dod_summary(issue_body)
    dod["body_path"] = str(body_path)
    return dod


def _stage_grounding_files(grounding_path, scratch_dir):
    """`(files_path, notices)` — the grounding checkout's tracked paths (one `git ls-files -z` read,
    so a non-ASCII name arrives unquoted), staged one per line to a FIXED scratch path for S7's
    `parse.py phases --files`, the plan-path check's `basename` source (#181); a name holding a newline,
    which a line cannot carry, is left out. A tracked list rather
    than a walk: an `os.walk` would read untracked trees (dependency and build directories). A failed
    read is `(None, [GROUNDING_FILES_UNAVAILABLE])`, never a decision."""
    result = process.run(["git", "ls-files", "-z"], cwd=str(grounding_path))
    if result.returncode != 0:
        return None, [GROUNDING_FILES_UNAVAILABLE]
    paths = [path for path in result.stdout.split("\0") if path and "\n" not in path]
    files_path = Path(scratch_dir) / "grounding-files.txt"
    try:
        with open(files_path, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(path + "\n" for path in paths))
    except OSError:
        return None, [GROUNDING_FILES_UNAVAILABLE]
    return str(files_path), []


def _dod_attention(dod_facts):
    note = parse.misplaced_dod_note(dod_facts)
    if note is None:
        return []
    return [
        note + " — no reader parses it as the DoD, so `closes-dod` has nothing to index; resolve "
        "the DoD-heading card (plan-schema.md, \"The `closes-dod` target\") before planning"
    ]


def build_facts(
    issue_number, repo, root=".", scratch_dir=None, refresh=False, cwd=None, ground_ambient=False
):
    """Assemble the planner's complete facts block and return the envelope dict WITHOUT printing
    it (the testable core, mirroring `prep_resolver.build_facts` / `prep_evaluator.build_facts`).
    Returns `None` after a `needs_decision` envelope has already been emitted on stdout.
    """
    # Normalize to the MAIN checkout (v3): the session may sit inside a worktree (epic-branch
    # grounding for a story, a plan-PR head on the revise row); ls-remote discovery and the
    # origin/main pin key off the main checkout regardless.
    root = str(workspace._resolve_main_root(root))
    root_branch = workspace.default_branch(root)
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-planner-%s" % issue_number
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    # Every composed core's non-blocking degradations accumulate here and ride out in the envelope's
    # own `notices` (architecture.md §3) — see `_merge_notices`.
    notices = []

    # 1) The target issue — one round-trip gh_gather.run() call: body/thread/plan-marker/native
    #    deps + the open-PR search already folded in.
    exit_code, issue_envelope = gh_gather.run(
        str(issue_number),
        repo,
        marker_prefix=PLAN_MARKER,
        scratch_dir=scratch_dir,
        env=None,
        stream=_DiscardStream(),
    )
    if issue_envelope is not None and issue_envelope.get("status") == "needs_decision":
        _merge_notices(notices, issue_envelope.get("notices"))
        if _forward_decision(issue_envelope["decision"], notices=notices):
            return None
    if exit_code != 0:
        sys.stderr.write(
            "prep_planner: gh_gather on issue #%s failed (exit %d)\n" % (issue_number, exit_code)
        )
        sys.exit(1)
    _merge_notices(notices, issue_envelope.get("notices"))

    issue_body = _extract_body(issue_envelope, "issue_body")
    labels = [label.get("name") for label in issue_envelope.get("labels") or []]
    issue_title = issue_envelope.get("title") or ""
    issue_type = _detect_type(labels, issue_title)

    # 2) Marker detection -> revise-mode trigger (docs/specs/planner.md "Artifacts read").
    plan_present = bool(issue_envelope.get("marker_comment_present"))
    mode = "revise" if plan_present else "fresh"

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
        # The left-hand side of the sub-issue rescope comparison: "was this sub-issue edited since
        # the plan was posted?" (#18). Free from the marker fetch — no extra round-trip.
        "updated_at": issue_envelope.get("marker_comment_updated_at"),
    }
    if plan_present:
        plan_facts["body_mode"] = issue_envelope.get("marker_comment_mode")
        if issue_envelope.get("marker_comment_mode") == "path":
            plan_facts["body_path"] = issue_envelope.get("marker_comment_path")
        elif plan_body is not None:
            # Staged even when small enough to ride inline: a revise diffs against the prior plan,
            # and a named path beats the model improvising one from `sections.marker_comment_body`.
            # Same file name gh_gather spills a large marker to, so the path is uniform whichever
            # mode applied.
            plan_facts["body_path"] = spill_bytes(
                plan_body.encode("utf-8"),
                "body",
                scratch_dir,
                force_path=True,
                filename="issue-%s-marker.md" % issue_number,
            )["body_path"]

    # The plan's part comments (skills/_shared/plan-parts.md) — `plan_body` above is already the
    # whole plan with them put back; these are what the persist edits in place. Empty for a plan
    # posted before parts existed (its revise posts them fresh); present without a plan when a fresh
    # post failed after its parts went up, so the retry rewrites them instead of duplicating them.
    plan_facts["parts"] = issue_envelope.get("marker_parts") or []

    # The prior plan's `## Phases` is parsed BEST-EFFORT — never `PHASES_MALFORMED`. A revise run
    # exists to *repair* a bad plan, so hard-failing here would mean the one tool that can rewrite the
    # section refuses to start because the section is broken, and the plan footer forbids
    # hand-editing. prep_resolver DOES hard-fail on the identical body (it executes the plan and
    # cannot ship a phase it cannot read), so a malformed plan stays re-plannable but never
    # executable. Same best-effort posture as `_parse_phase_tracker`. Parsed ONCE here: the slices
    # diff reads it.
    prior_phases = None
    prior_phases_parsed = True
    prior_phases_error = None
    if plan_present and plan_body:
        try:
            prior_phases = parse.parse_phases(plan_body)
        except parse._PhasesMalformed as exc:  # noqa: SLF001 (mirrors prep_resolver's use)
            prior_phases = None
            prior_phases_parsed = False
            prior_phases_error = {
                "reason": exc.reason,
                "line_number": exc.line_number,
                "raw_line": exc.raw_line,
            }

    # 3) Research dossier — scan the ALREADY-fetched thread (no second gh call).
    thread_list = _load_thread(issue_envelope)
    research_comment, research_untrusted, research_decision = _find_one_marker(
        thread_list, RESEARCH_MARKER, "research-dossier"
    )
    _merge_notices(
        notices, [trust.untrusted_notice(research_untrusted, RESEARCH_MARKER, "issue #%s" % issue_number)]
    )
    if _forward_decision(research_decision, notices=notices):
        return None
    research_facts = {"present": research_comment is not None}
    if research_comment is not None:
        research_facts["comment_id"] = _marker_comment_id(research_comment)
        research_facts["comment_url"] = research_comment.get("url")
        research_facts.update(
            _stage_comment_body(research_comment, scratch_dir, "issue-%s-research.md" % issue_number)
        )

    # 4) Epic / story facts + epic-branch discovery (feeds plan_ref selection).
    # This issue's OWN open PRs (branching.prior_prs_for_issue), not every PR that mentions it: an
    # epic integration PR lists every story by number, and taking its head here would ground a
    # story's plan on the epic's ref AND feed the epic's `## Phase tracker` into that story's
    # revise facts via `_build_revise_facts` below.
    open_prs = branching.prior_prs_for_issue(issue_envelope.get("open_prs"), issue_number)
    open_pr_headref = open_prs[0]["headRefName"] if open_prs else None

    epic_branch_name = None
    # Bound up-front rather than only inside the two arms that discover a branch: step 5's row-4
    # probe reads it, and a standalone target enters neither arm.
    epic_branch_facts = None
    epic_facts = None
    story_facts = None
    parent_epic_open = False

    if issue_type == "epic":
        epic_branch_facts, decision = _discover_epic_branch(root, issue_number)
        if _forward_decision(decision, notices=notices):
            return None
        epic_branch_name = epic_branch_facts.get("branch")

        # The epic's story set, per skills/_shared/epic-story-hierarchy.md. The native source
        # is the native sub-issue relation: every entry is filed by construction and its live state
        # rides along, so no per-story round-trip is needed. Tier 2 parses a legacy epic's
        # `## Stories` section and fetches each filed story's state. A fresh epic has NO `## Stories`
        # section at all, so reading only the body would report `stories_filed: false` for an epic
        # whose stories are filed — routing the planner's handoff to the drafter to file stories that
        # already exist.
        story_entries, stories_source, decision = _epic_story_set(
            issue_envelope.get("sub_issues"), issue_body, repo, cwd=cwd
        )
        if _forward_decision(decision, notices=notices):
            return None

        delivery_log_facts, dl_decision = _read_delivery_log(
            thread_list, scratch_dir, issue_number, notices
        )
        if _forward_decision(dl_decision, notices=notices):
            return None

        epic_facts = {
            "branch": epic_branch_facts,
            "stories_filed": any(e["number"] is not None for e in story_entries),
            "stories_source": stories_source,
            "stories": story_entries,
            "delivery_log": delivery_log_facts,
        }

    elif issue_type == "story" or issue_envelope.get("parent"):
        # Gated on HAVING a parent, not on the lexical type (#31) — the same gate move as
        # prep_workspace_open's and prep_resolver's. Grounding a plan on the default branch while
        # the resolver builds it on `epic/<N>-<slug>` plans against a tree missing every predecessor
        # story's merged work. Tier rule identical to the other two preps: native `parent` first
        # (exact, no round-trip), and the legacy `## Stories` checklist tier ONLY for a `story`
        # filed before the relation was written (skills/_shared/epic-story-hierarchy.md) — an
        # untyped target gets the exact native answer or no lookup at all. The legacy tier counts an
        # epic only when its `## Stories` checklist lists this story — a mention is not membership.
        matches, decision = _search_parent_epic(
            repo, issue_number, native_parent=issue_envelope.get("parent"), cwd=cwd
        )
        if _forward_decision(decision, notices=notices):
            return None
        parent_epic = matches[0] if len(matches) == 1 else None
        parent_open = parent_epic is not None and (parent_epic.get("state") or "").upper() == "OPEN"

        epic_branch_facts = None
        jit_epic_plan = None
        jit_delivery_log = None
        if parent_open:
            epic_branch_facts, decision = _discover_epic_branch(root, parent_epic["number"])
            if _forward_decision(decision, notices=notices):
                return None
            epic_branch_name = epic_branch_facts.get("branch")

        # Which hierarchy edge is this? An `epic/<parent>-*` branch on origin answers it for free —
        # the parent is an epic and this is a story. Without one, the `parent` node's number/title/
        # state cannot tell an epic whose workspace is not open yet from a STORY parent, and the two
        # mean opposite things: the second makes this target a deliverable slice.
        if not epic_branch_name and parent_epic is not None and not open_pr_headref:
            # `open_pr_headref` suppresses the whole check: the target already has a PR of its OWN,
            # so if it is a slice it was promoted to a story some sessions ago, and that promotion
            # is a fact on GitHub that a card here cannot unwind. Refusing would put a blocking
            # gate on every planner session for work already in flight — and would contradict both
            # `_select_plan_ref`'s "open-PR-head wins when more than one row applies" precedence
            # (docs/specs/planner.md Step 4.5) and `prep_workspace_open`, whose continue row
            # short-circuits ahead of its own copy of this refusal.
            #
            # No `epic/<parent>-*` branch proved epic-ness for free, so ask the parent directly:
            # its own type IS the answer to which hierarchy edge this is
            # (skills/_shared/epic-story-hierarchy.md's "by construction").
            parent_kind, kind_notice = branching.classify_parent(
                repo, parent_epic["number"], cwd=cwd
            )
            if kind_notice:
                notices.append(kind_notice)
            if parent_kind == branching.PARENT_KIND_NON_EPIC:
                # This target is a deliverable slice. A slice is planned as a PHASE of its parent's
                # plan — the phase's `sub-issue:` key names it — so authoring a standalone plan here
                # would put a second, competing decomposition against the same work.
                emit_needs_decision(
                    needs_decision(
                        TARGET_IS_SLICE,
                        summary="#%s is a deliverable slice of #%s — a slice is planned as a phase "
                        "of its parent's plan, not as a plan of its own"
                        % (issue_number, parent_epic["number"]),
                        context={
                            "issue": issue_number,
                            "parent": parent_epic,
                            "parent_kind": parent_kind,
                        },
                        # External remedies, then re-run — the house pattern for every card. A
                        # "plan anyway" option would be unactionable: this prep has no override
                        # flag, so re-running after it would raise the identical card.
                        options=[
                            "plan the parent instead: /ctrl-freaq:planner %s"
                            % parent_epic["number"],
                            "if #%s IS an epic, label it `epic` (or retitle it `Epic: …`), "
                            "then re-run" % parent_epic["number"],
                            "if #%s should be planned on its own, re-parent it to the epic (or "
                            "clear its parent edge), then re-run" % issue_number,
                        ],
                    ),
                    notices=notices,
                )
                return None

        # Not a slice (or unknown). An UNTYPED target still enters the story-JIT machinery only on
        # the branch-existence oracle — an unbootstrapped epic and an unreadable parent both leave
        # epic-ness unproven, so it grounds on the default branch exactly as before, and says so.
        if issue_type != "story" and not epic_branch_name:
            if parent_epic is not None:
                notices.append(
                    branching.PARENT_CLOSED
                    if not parent_open
                    else branching.PARENT_HAS_NO_INTEGRATION_BRANCH
                )
            epic_branch_facts = None
        else:
            parent_epic_open = parent_open
            if parent_open:
                epic_exit, epic_envelope = gh_gather.run(
                    str(parent_epic["number"]),
                    repo,
                    marker_prefix=PLAN_MARKER,
                    scratch_dir=scratch_dir,
                    env=None,
                    stream=_DiscardStream(),
                )
                if epic_envelope is not None and epic_envelope.get("status") == "needs_decision":
                    _merge_notices(notices, epic_envelope.get("notices"))
                    if _forward_decision(epic_envelope["decision"], notices=notices):
                        return None
                if epic_exit != 0:
                    sys.stderr.write(
                        "prep_planner: gh_gather on parent epic #%s failed (exit %d)\n"
                        % (parent_epic["number"], epic_exit)
                    )
                    sys.exit(1)
                _merge_notices(notices, epic_envelope.get("notices"))

                jit_epic_plan = {"present": bool(epic_envelope.get("marker_comment_present"))}
                if jit_epic_plan["present"]:
                    jit_epic_plan["body_mode"] = epic_envelope.get("marker_comment_mode")
                    if epic_envelope.get("marker_comment_mode") == "path":
                        jit_epic_plan["body_path"] = epic_envelope.get("marker_comment_path")
                    else:
                        jit_epic_plan["body"] = epic_envelope.get("marker_comment_body")

                epic_thread = _load_thread(epic_envelope)
                jit_delivery_log, dl_decision = _read_delivery_log(
                    epic_thread, scratch_dir, parent_epic["number"], notices
                )
                if _forward_decision(dl_decision, notices=notices):
                    return None

            story_facts = {
                "parent_epic": parent_epic,
                "parent_epic_open": parent_open,
                "epic_branch": epic_branch_facts,
                # The three `story-own-branch` (row 4) facts. Declared here so the story schema is
                # readable in one place; filled in at step 5 by `_probe_own_branch`, which owns
                # what each `None` means.
                "ambient_branch": None,
                "ambient_contains_base_tip": None,
                "own_branch_base_ref": None,
                "epic_plan": jit_epic_plan,
                "epic_delivery_log": jit_delivery_log,
            }

    # 4.5) Deliverable sub-issues (#18) — a NON-EPIC target's sub-issues are its slices by
    #      construction, so the gate is the target's type plus a non-empty node list already in
    #      hand: an epic's children are stories, and a childless target costs zero extra gh calls
    #      (which is what keeps the canonical call budget unchanged).
    slices_facts = None
    sub_issue_nodes = issue_envelope.get("sub_issues") or []
    if issue_type != "epic" and sub_issue_nodes:
        detail_objects, detail_notices, detail_decision = _fetch_sub_issue_details(
            repo, issue_number, cwd=cwd
        )
        if _forward_decision(detail_decision, notices=notices):
            return None
        _merge_notices(notices, detail_notices)

        plan_updated_at = plan_facts.get("updated_at")
        slice_set = _build_slice_set(
            sub_issue_nodes, detail_objects, plan_updated_at, scratch_dir
        )
        # Three distinguishable states, so the fact never contradicts itself: `unavailable` means
        # THIS HOST cannot answer (the endpoint degraded), `no_prior_plan` means there is nothing to
        # compare against yet, and `updated_at` means the comparison actually ran.
        detail_available = detail_objects is not None
        if not detail_available:
            rescope_basis = "unavailable"
        elif not plan_updated_at:
            rescope_basis = "no_prior_plan"
        else:
            rescope_basis = "updated_at"

        # `prior_phases` / `prior_phases_parsed` / `prior_phases_error`: parsed once, best-effort, at
        # step 2.
        slices_facts = {
            "detail_available": detail_available,
            "source": "sub_issues_rest" if detail_available else "sub_issues_node",
            "open_count": sum(1 for e in slice_set if e["state"] == "OPEN"),
            "rescope_basis": rescope_basis,
            "set": slice_set,
            "diff": _build_slice_diff(
                slice_set, prior_phases, prior_phases_parsed, rescope_basis
            ),
        }
        slices_facts["diff"]["prior_phases_error"] = prior_phases_error

    # 5) plan_ref selection (docs/specs/planner.md Step 4.5's FULL table — see module docstring).
    #    Two passes over the SAME pure table, never a second copy of the row logic: pass 1 answers
    #    "which ref would this have grounded on", which is exactly the ref the ambient probe has to
    #    measure against; pass 2 re-runs it with what the probe verified. The table stays the single
    #    source of the row set, `_select_plan_ref` stays pure, and the one impure step (reading the
    #    ambient checkout, probing containment) sits here, in the open. The probe runs on
    #    `--refresh` too: a row that applied on the full run but not on refresh would flip
    #    plan_ref mid-session and make the rendered footer lie.
    base_ref, base_row = _select_plan_ref(
        issue_type, epic_branch_name, open_pr_headref, root_branch,
        parent_epic_open=parent_epic_open,
    )
    probe, probe_notices = _probe_ambient(
        base_ref, base_row, epic_branch_facts, root, issue_number, cwd
    )
    _merge_notices(notices, probe_notices)
    kind = probe["kind"]
    if story_facts is not None:
        if base_row in ROWS_ELIGIBLE_FOR_OWN_BRANCH:
            story_facts["ambient_branch"] = probe["ambient_branch"]
        if probe["own"]:
            story_facts["ambient_contains_base_tip"] = probe["contains"]
        if kind == "own":
            story_facts["own_branch_base_ref"] = base_ref
    accepted = kind == "foreign_ahead" and ground_ambient
    plan_ref, plan_ref_row = _select_plan_ref(
        issue_type, epic_branch_name, open_pr_headref, root_branch,
        parent_epic_open=parent_epic_open,
        own_branch_at_base_tip=probe["ambient_branch"] if kind == "own" else None,
        accepted_ambient=probe["ambient_branch"] if accepted else None,
    )
    if kind == "foreign_ahead" and not accepted:
        # Full run AND refresh: a refresh that silently fell back to the base ref here would flip
        # an accepted session's plan_ref, so a missing flag re-asks instead.
        _forward_decision(
            _foreign_checkout_decision(probe, plan_ref, root, root_branch, cwd), notices=notices
        )
        return None
    # `facts.grounding.basis` — why the asserted checkout is an honest vantage for `plan_ref`, a
    # closed set. `plan_ref`: the checkout IS `plan_ref` (or the project root on the default
    # branch). `own_branch`: the issue's own branch, containing the base ref's tip (rows
    # `story-own-branch` / `own-branch`). `identical`: another branch whose HEAD is exactly
    # `plan_ref`'s tip — the same code, so `plan_ref` and the footer stand. `accepted`: another
    # branch carrying commits beyond the tip, grounded on only because the operator answered the
    # card with `--ground-ambient`.
    basis = {"own": "own_branch", "identical": "identical"}.get(
        kind, "accepted" if accepted else "plan_ref"
    )
    closed_pr = None
    # Not on `--refresh`: the line it feeds cannot change mid-session, and a refresh must stay cheap.
    if basis == "own_branch" and not refresh:
        closed_pr, closed_notices = _closed_pr_for_branch(
            repo, issue_number, probe["ambient_branch"], cwd
        )
        _merge_notices(notices, closed_notices)

    # 6) Ambient grounding (v3): the planner grounds on the CHECKOUT THE SESSION WAS STARTED IN,
    #    asserted by CONTENT against the selected plan_ref — never a script-created read worktree.
    #    - basis `plan_ref`: the checkout must be plan_ref itself. For a default-branch plan_ref,
    #      any up-to-date checkout of it — including the project root — passes (`allow_main_root`;
    #      plan-before-open is the canonical posture). Otherwise branch_mismatch / at_project_root,
    #      exactly as before.
    #    - `own_branch` / `accepted`: plan_ref IS the ambient branch; staleness is measured against
    #      that branch's own remote, the ref the footer names.
    #    - `identical`: plan_ref stays the base; staleness is measured against the base tip the probe
    #      already compared — never `origin/<ambient-branch>`, which is the wrong reference here.
    #    A checkout strictly behind its reference is WORKSPACE_MISMATCH(stale_checkout) — a stale
    #    footer SHA would be an immediate `plan: stale` downstream. No hooks (grounding is
    #    read-only). Skipped on --refresh, mirroring prep_resolver.py's / prep_evaluator.py's
    #    identical contract. `root` reports the main checkout's path and the derived default branch
    #    — uniform across the three stage preps; the grounding SHA a plan footer renders is
    #    `grounding.sha`, this checkout's own HEAD.
    if not refresh:
        if basis == "plan_ref":
            attach_kwargs = {
                "allow_main_root": plan_ref == root_branch,
                "check_remote_staleness": True,
            }
            expected_branch = plan_ref
        elif basis == "identical":
            attach_kwargs = {"allow_main_root": True, "expected_remote_sha": probe["base_tip_sha"]}
            expected_branch = probe["ambient_branch"]
        else:
            attach_kwargs = {"allow_main_root": True, "check_remote_staleness": True}
            expected_branch = probe["ambient_branch"]
        grounding_envelope, gw_notices, gw_decision = workspace._build_attach(
            cwd if cwd is not None else ".",
            expected_branch,
            run_hooks=False,
            **attach_kwargs
        )
        if _forward_decision(gw_decision, notices=notices):
            return None
        _merge_notices(notices, gw_notices)
        grounding_docs, catalogue_notices = _grounding_doc_inventory(grounding_envelope["path"])
        _merge_notices(notices, catalogue_notices)
    else:
        grounding_envelope = None
        grounding_docs = []

    # 7) Bug (a) — the deterministic open-question tracker de-dup search (S14-promoted; see module
    #    docstring's "S14 promotion" paragraph).
    open_question_entries, open_question_candidates, oq_decision = (
        oq_tracker.build_open_question_candidates(issue_body, repo, cwd=cwd)
    )
    if _forward_decision(oq_decision, notices=notices):
        return None

    # 8) Revise facts (mode == "revise" only).
    revise_facts = None
    shipped_attention = []
    if mode == "revise":
        revise_facts, revise_notices, revise_decision = _build_revise_facts(
            issue_envelope,
            open_prs,
            plan_sha,
            grounding_envelope.get("sha") if grounding_envelope is not None else None,
            repo,
            scratch_dir,
            cwd=cwd,
        )
        if _forward_decision(revise_decision, notices=notices):
            return None
        _merge_notices(notices, revise_notices)
        shipped_facts, shipped_notes, shipped_decision = _read_shipped_records(
            thread_list, revise_facts, plan_body, scratch_dir, issue_number, notices
        )
        if _forward_decision(shipped_decision, notices=notices):
            return None
        plan_facts["shipped"] = shipped_facts
        shipped_attention = shipped_notes

    suggested_playbook = _suggested_playbook(issue_type, mode, parent_epic_open)
    vector = {"type": issue_type, "mode": mode, "plan_ref_row": plan_ref_row}
    dod_facts = _build_dod_facts(issue_body, scratch_dir)

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
            "blocked_by": issue_envelope.get("blocked_by") or [],
            "blocking": issue_envelope.get("blocking") or [],
            "deps_available": issue_envelope.get("deps_available"),
            # Native epic↔story hierarchy (skills/_shared/epic-story-hierarchy.md): `parent` names
            # the epic a story belongs to, `sub_issues` the epic's story set, `sub_issues_summary`
            # its progress rollup. Tier 1 of the story-set read; the `## Stories` checklist in the
            # body is the `checklist` source for epics filed before the relation was written.
            "parent": issue_envelope.get("parent"),
            "sub_issues": issue_envelope.get("sub_issues") or [],
            "sub_issues_summary": issue_envelope.get("sub_issues_summary") or {},
            "subissues_available": issue_envelope.get("subissues_available"),
        },
        "vector": vector,
        "plan_ref": plan_ref,
        "suggested_playbook": suggested_playbook,
        "plan": plan_facts,
        "research": research_facts,
        "grounding_docs": grounding_docs,
        "open_questions": open_question_entries,
        "open_question_candidates": open_question_candidates,
        "dod": dod_facts,
        "attention": _build_attention(
            open_question_candidates,
            epic_facts,
            story_facts,
            grounding_docs=None if refresh else grounding_docs,
            catalogue_absent=DOC_CATALOGUE_ABSENT in notices,
            grounding_basis=basis,
            probe=probe,
            plan_ref=plan_ref,
            closed_pr=closed_pr,
        ),
        "notices": notices,
    }
    if epic_facts is not None:
        facts["epic"] = epic_facts
    if story_facts is not None:
        facts["story"] = story_facts
    if revise_facts is not None:
        facts["revise"] = revise_facts
    facts["attention"].extend(_dod_attention(dod_facts))
    facts["attention"].extend(shipped_attention)
    if slices_facts is not None:
        # Named `slices`, not `sub_issues`: `target.sub_issues` already carries the raw relation
        # nodes, and a second top-level key by that name would be a genuine confusion hazard. A
        # playbook keys on this key's PRESENCE — never on the target's type.
        facts["slices"] = slices_facts
        facts["attention"].extend(_slice_attention(slices_facts, issue_envelope["number"]))
    if grounding_envelope is not None:
        # v3: the OBSERVED ambient checkout, asserted against plan_ref — replaces the
        # read_workspaces.grounding ro-* view. `grounding.sha` feeds the plan footer's
        # `@<short-sha>` and `revise.grounding_sha`.
        facts["grounding"] = {
            "path": grounding_envelope["path"],
            "ref": plan_ref,
            "branch": grounding_envelope.get("branch"),
            "sha": grounding_envelope.get("sha"),
            "dirty": grounding_envelope.get("dirty"),
            # Why this checkout is an honest vantage for `ref` (see `basis` above), and the base the
            # content check measured it against — `base_tip_sha` is `None` when no check ran
            # (basis `plan_ref`: the checkout is the ref itself).
            "basis": basis,
            "base_ref": base_ref,
            "base_tip_sha": probe["base_tip_sha"] if basis != "plan_ref" else None,
        }
        files_path, files_notices = _stage_grounding_files(grounding_envelope["path"], scratch_dir)
        facts["grounding"]["files_path"] = files_path
        _merge_notices(notices, files_notices)
        if grounding_envelope.get("dirty"):
            facts["attention"].append(
                "grounding checkout has uncommitted changes — the plan footer SHA may not "
                "reflect the files read"
            )

    sections = {}
    for key, value in issue_envelope.items():
        if key.startswith(("issue_body", "thread", "marker_comment")):
            sections[key] = value
    facts["sections"] = sections

    return facts


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
        help="scratch dir for spilled sections (default: /tmp/gh-planner-<issue>)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-derive volatile facts (issue/PR state, marker detection, OQ tracker search) "
        "without re-running root freshness or re-ensuring the grounding read workspace",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="explicit working directory for the gh calls that have no --repo scoping of their "
        "own (parent-epic search, per-story state fetch, OQ tracker search); optional in normal "
        "use, provided for test-injection — mirrors prep_resolver.py's / prep_evaluator.py's "
        "identical --cwd knob",
    )
    parser.add_argument(
        "--ground-ambient",
        action="store_true",
        help="the operator's answer to the WORKSPACE_MISMATCH card for a checkout that contains "
        "plan_ref's tip plus commits of another branch: ground on that checkout (row "
        "ambient-accepted). Honoured only when the checkout contains the tip; carry it on every "
        "later prep run this session, --refresh included",
    )
    parser.add_argument(
        "--oq-query",
        action="append",
        default=None,
        metavar="TEXT",
        help="one-shot tracker de-dup lookup for a newly-detected open-question topic (repeatable) "
        "— runs the deterministic question-tracker search and emits `oq_query_candidates`, without "
        "assembling the full facts block; the playbook consults it before recording a NEWLY-"
        "detected OQ as (not filed) (docs/specs/planner.md Bug (a))",
    )
    args = parser.parse_args(argv)

    if args.oq_query:
        payload, notices, decision = build_oq_query(args.repo, args.oq_query, cwd=args.cwd)
        if decision is not None:
            emit_needs_decision(decision, notices=notices)
            return EXIT_OK
        emit_ok(payload=payload, notices=notices)
        return EXIT_OK

    facts = build_facts(
        args.issue,
        args.repo,
        root=args.root,
        scratch_dir=args.scratch_dir,
        refresh=args.refresh,
        cwd=args.cwd,
        ground_ambient=args.ground_ambient,
    )
    if facts is None:
        return EXIT_OK
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
