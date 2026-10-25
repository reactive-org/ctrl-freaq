#!/usr/bin/env python3
"""prep_drafter.py — the drafter's complete facts block in one call (architecture.md §4;
docs/implementation.md S14; docs/specs/drafter.md). Assembles the session's entire starting
state — the two-way vector (new / revise), repo-context inventory (issue
templates, labels, grounding-doc presence), the `<!-- drafter-open-question-markers -->` config
block (or the heuristic-cue fallback signal), revise-mode facts (plan-marker presence, open-PR
list, closed-by-PR/project references), and the search-before-file open-question tracker de-dup — as
ONE JSON envelope on stdout, so the drafter session's startup is one Python process, never a
subprocess chain.

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external
processes any script may spawn are git/gh")::

    gh_gather.run(..., stream=)                    -- the target issue's body/thread/plan-marker/
                                                       native-deps/open-PR envelope (one round-trip);
                                                       revise mode ONLY — `new` mode has
                                                       no target issue yet, so this call is skipped
                                                       entirely
    config_block.read_block_anywhere                -- the OQ-marker config block
                                                       (`<!-- drafter-open-question-markers -->`)
    oq_tracker.build_open_question_candidates        -- the target issue body's `## Open questions`
                                                       section, search-before-file de-dup (S14
                                                       promotion of prep_planner's Bug (a) mechanism —
                                                       see that module's docstring)
    oq_tracker.build_oq_query                        -- the one-shot `--oq-query` lookup `new` mode
                                                       uses for an OQ the operator names from the
                                                       feedback/doc text itself (no issue body to scan)
    branching.detect_ambient_issue                   -- which issue the CURRENT branch is standing
                                                       in (`facts.ambient`), so a session invoked
                                                       from inside `epic/<N>-<slug>` can offer a
                                                       relationship instead of filing an orphan
    parse.dod_summary (+ branching.classify_parent)  -- `facts.revise.dod`: the target body's DoD
                                                       shape, so the revise renames a checklist
                                                       under another heading; the parent lookup
                                                       runs ONLY when that rename would fire, to
                                                       exempt a deliverable slice

Every executor composed here exposes a **pure, non-emitting core** — ``build_*(...) -> (payload,
notices, decision|None)`` (docs/specs/baseline.md §5, the S8 pattern lock). This prep calls those
cores **directly** and forwards each core's returned ``decision`` verbatim (:func:`_forward_decision`),
emitting exactly one envelope of its own. No ``redirect_stdout``/``io.StringIO`` capture of another
script's stdout is used anywhere in this module (S9-on rule; docs/specs/baseline.md §5).

``git rev-parse HEAD`` (root's own SHA, informational only) and ``gh label list`` / ``gh issue
view <NN> --json state,title,labels`` (per-story live-state fetch, mirroring `prep_planner.py`'s
identical `GATHER_EPIC`-reconciliation precedent) have no existing executor core — architecture.md
§1 permits any script to spawn `git`/`gh` directly via `pipelib.process.run`, the same precedent
`prep_planner.py`/`prep_resolver.py`/`prep_evaluator.py` already established for their own
prep-owned direct calls.

Usage::

    prep_drafter.py <owner/repo> [--issue N] [--root PATH] [--scratch-dir PATH] [--cwd PATH]
    prep_drafter.py <owner/repo> --oq-query "<topic>" [--oq-query "<topic>" ...]

``--root`` defaults to ``.`` (the project root — architecture.md §6's read-only trust vantage).
``--issue`` selects revise mode (any already-filed target, epic included — the target's type is read
mechanically from its own labels/title, never from operator intent); omitting it selects new mode. ``--scratch-dir`` defaults to ``/tmp/gh-drafter-<issue>`` in revise mode and
``/tmp/gh-drafter-new-<pid>`` in new mode (CLAUDE.md's ``/tmp/gh-<skill>-<N>/`` convention — ``<N>``
is the session's key: the issue number, or the process id when no issue exists yet, so the
concurrently-spawned proxy-filed drafters of one follow-up batch never share a staging dir and so
never clobber each other's staged body) when omitted — this IS "staging conventions" from this step's
Work list: prep establishes and creates the directory every staged draft/revised body gets written
under at flow time (docs/specs/drafter.md "Artifacts written"); prep itself stages nothing, since no draft exists
yet at session start. ``--oq-query`` mirrors `prep_planner.py`'s identical one-shot flag (see
`oq_tracker.build_oq_query`'s docstring) and, when given, short-circuits to that one-shot payload —
no full facts assembly, matching planner's own CLI contract.

No ``--refresh`` flag: unlike the planner/resolver/evaluator, this prep runs no root-freshness
protocol and ensures no read/work workspace (see the "No workspace" paragraph below) — every fact
this script derives is already re-derived fresh on every invocation, so there is no expensive
one-time setup step for a `--refresh` flag to skip; adding one would be dead surface with no
distinguishable behavior.

**No workspace — the drafter grounds on the CURRENT checkout, not a pinned ref (docs/specs/drafter.md
"Repo context probe").** v1 drafts from wherever the operator's working tree
happens to be — `ls .github/ISSUE_TEMPLATE/`, `gh label list`, `ls docs/prd.md ...` all run inline,
with no requirement that the tree be clean or on `main` (the skill's own primary trigger is "the user
is mid-development ... notices things," i.e. very plausibly *while* the tree is dirty on a feature
branch). Unlike the resolver/evaluator (whose gate config — test-target/checks/merge-policy — would weaken
a real merge gate if read from an untrusted ref, which is why their preps read those blocks from
the ambient checkout's working tree), the drafter's OQ-marker block only
informs a **detection hint** for drafting, never a merge/build gate — so reading the ambient
checkout "as-is" carries no gate-weakening exposure, and asserting or pinning anything here would
make this prep fail on the exact mid-feature-branch working tree its most common real trigger
describes. `root` therefore carries only `{path, sha}` (a plain,
non-gating `git rev-parse HEAD` — informational, never enforced) — no `workspace.py` import, no
`fresh` key (there is no freshness protocol here for that key to describe). Flagged for reviewer
scrutiny in the implementor report; not a defect if the reviewer weighs the threat model the same way.

**``facts.ambient`` — noticing that branch, not asserting it.** The same "grounds on the current
checkout" posture is what makes `branching.detect_ambient_issue` belong here: the branch the operator
is standing in is a *fact about this session* the drafter was previously blind to, so a session run
from inside `epic/95-<slug>` filed an issue with no link to #95 at all unless the operator typed one.
This is an offer, not a gate, and the distinction is the whole design — an unparseable branch, a
detached HEAD, or a failed issue lookup yields no fact (at most a
``AMBIENT_ISSUE_LOOKUP_UNAVAILABLE`` notice), never a ``needs_decision``, so nothing that files today
can start failing. It is emitted in BOTH modes, minus the self-referential case (revise mode standing
in the target's own branch has no relationship to offer). Adding it costs one `git` call always plus
one `gh` call only when the branch parses. The drafter still writes **no** parent edge: since #16 both
hierarchy edges are the slicer's (`skills/_shared/epic-story-hierarchy.md`), so the "child of #N"
answer resolves through the handoff pointing at a slicer adoption run.

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"``
or ``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified
hard `gh`/`git` failure surfaced by a composed executor — stderr carries the faithful error.

**``vector.mode`` — the TWO-value closed set ``"new"`` / ``"revise"``.** Derived purely mechanically,
never from operator intent the script can't see:

  - no ``--issue`` -> ``"new"`` (``vector.type`` is ``None`` — Step 1's bug/incomplete/feature/
    epic/question classification is judgment over freeform feedback text; a script has no target
    issue to inspect yet, so it cannot and does not predict it — architecture.md §5: the router
    overrides `suggested_playbook` post-classification "on evidence the script cannot see").
  - ``--issue`` given -> ``"revise"``, whatever the target's type. An **epic target is an ordinary
    revise** since #16: the drafter revises one issue's body, and an epic's story set is the native
    sub-issue relation, so changing *which* stories exist is a slicer run at epic altitude rather than
    a body edit. A Story revise additionally verifies its Epic backlink line at flow time — the revise
    steps apply uniformly regardless of sub-type, so this prep forks no further mode for it.

  Before #16 an epic target selected a third mode, ``"epic-revise"``, whose facts carried the epic's
  story set (reconciled against live per-story state) for the drafter's epic-split playbook to
  re-order and batch-file into. That playbook is gone; the two-tier story read it needed now lives in
  ``prep_slicer.py``, where the reconciliation happens.

**``suggested_playbook``** (architecture.md §5 "Prep proposes; the router confirms") maps onto the
three playbook names — mirroring `prep_planner.py`'s identical `_suggested_playbook` precedent:

  - ``mode == "new"`` -> ``"new.md"`` (the router may override to ``question.md``
    post-classification — evidence this script cannot see ahead of reading the feedback text. An
    ``Epic`` classification needs no override: an epic is one issue, filed by ``new.md`` like any
    other, and its stories are cut afterwards by the slicer).
  - ``mode == "revise"``, ``vector.type == "question"`` -> ``"question.md"``.
  - ``mode == "revise"``, any other type (including ``epic``) -> ``"revise.md"``.

**The shared question-tracker search-before-file mechanism (S14 promotion; see `oq_tracker.py`'s own
module docstring for the full rationale).** `prep_planner.py`'s `_search_question_tracker` /
`_build_open_question_candidates` / `build_oq_query` — authored for S12/S13 as that module's own
local helpers — are now `oq_tracker.py`, a new single-purpose module, because this step needs the
byte-identical mechanism for its own search-before-file companion-question de-dup
(docs/specs/drafter.md Step 3.5/R4). `prep_planner.py` was refactored to call `oq_tracker`
in-process instead of carrying its own copy; its own test suite (`tests/test_prep_planner.py`) is
unmodified and green (verified). This module composes `oq_tracker.build_open_question_candidates`
(revise mode — the target issue body IS the source to scan) and `oq_tracker.build_oq_query`
(new mode's ``--oq-query`` one-shot — there is no issue body yet, so the operator names the topic
directly, mirroring `prep_planner.py`'s identical additive extension for a plan-time-detected OQ).

**Repo-context inventory (docs/specs/drafter.md "Repo context probe").** Issue templates
(`.github/ISSUE_TEMPLATE/*` — a plain filesystem listing at `root`, no `git`/`gh` call needed, since
`root` IS the drafter's grounding vantage per the "No workspace" note above), `gh label list` (repo
labels, live GitHub state, independent of `root`'s own git state), and the grounding docs the
CONSUMING repo declares in its `<!-- doc-catalogue -->` block (`skills/_shared/doc-catalogue.md`,
read via `doc_catalogue.read_catalogue` at `root` — the drafter's vantage is the ambient checkout,
so an uncommitted catalogue edit counts, unlike the planner's ensured checkout at `plan_ref`). The
long-standing `docs.prd` fact survives as the catalogue's `prd`-role entry; the four hardcoded
PRD candidate paths that preceded it are gone, along with the doc-layout assumption they encoded.

**Referenced-issue lookup, epic-backlink-open check: deliberately out of scope for this step.**
docs/specs/drafter.md's "Deterministic steps" table also names a referenced-issue lookup (`gh issue
view <N> --json title,state,body,labels`, for issue numbers the OPERATOR'S FEEDBACK TEXT names) and
docs/specs/drafter.md's "Epic closed (revising a Story)" gate — the epic-backlink-open check. Both depend on text the model
reads (the feedback, or the target's own body) that prep cannot see ahead of a session starting — the
router/playbook reads that text directly and, per architecture.md §1's "any script may spawn git/gh
directly," calls `gh issue view` itself at flow time for either case; this step's Build list (per the
implementor brief) does not name either as a facts-block field, so no dedicated one-shot CLI mode is
added here for them (unlike `--oq-query`, which the brief DOES name explicitly). Recorded here, not
silently dropped.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402  (the shared ambient-branch grammar; import-only, like oq_tracker)
import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import doc_catalogue  # noqa: E402  (the consuming repo's declared grounding docs)
import gh_gather  # noqa: E402
import oq_tracker  # noqa: E402
import parse  # noqa: E402  (the DoD-shape core, `dod_summary`)
from pipelib import process  # noqa: E402
from pipelib.decisions import AUTH_REQUIRED, DOC_CATALOGUE_ABSENT, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, emit_needs_decision, emit_ok  # noqa: E402

# The implementation-plan marker (skills/planner/references/plan-schema.md;
# docs/specs/drafter.md "Artifacts read" (plan-comment row) — revise mode fetches it to ground the
# revise against the planner's own approach, and to know whether Step R6's staleness flag applies).
# Same constant as `prep_planner.py`'s `PLAN_MARKER` — restated locally per this codebase's
# "no prep-to-prep imports" design convention (see `prep_planner.py`'s module docstring).
PLAN_MARKER = "<!-- implementation-plan:v1 -->"

# The OQ-marker config block name (skills/_shared/open-question-detection.md; docs/specs/drafter.md
# "OQ marker config read") — read
# via `config_block.read_block_anywhere` from CLAUDE.md/COMMANDS.md (its default candidate set
# already includes CLAUDE.md, the block's documented home).
OQ_MARKER_CONFIG_BLOCK = "drafter-open-question-markers"

# Revise-mode's extra `--json` fields (docs/specs/drafter.md "Artifacts read", closed-by-PR row):
# surfaced before editing so the operator can
# coordinate around an in-flight PR or a project-board placement, per docs/specs/drafter.md
# "Artifacts read"'s "Closed-by-PR / project references" row.
REVISE_EXTRA_JSON = "closedByPullRequestsReferences,projectItems"

# State-vector `type` detection (docs/specs/resolver.md's identical epic/story rule, restated here
# per the "no prep-to-prep imports" convention `prep_planner.py`'s module docstring names) PLUS a
# `question` branch `prep_planner.py` doesn't need (the planner never targets a question issue) —
# the drafter's revise-mode type space is `standard` / `story` / `epic` / `question`, all four
# label/title-derivable without any judgment call.
_EPIC_TITLE_PREFIX_RE = re.compile(r"^\s*epic\s*:", re.IGNORECASE)

# Grounding docs are the CONSUMING repo's declaration (`skills/_shared/doc-catalogue.md`;
# `scripts/doc_catalogue.py`), not a path list here. This module previously carried four constants —
# a four-candidate PRD search plus one canonical path each for architecture / constitution /
# CLAUDE.md — which asserted a doc layout the repo never agreed to and disagreed with
# `prep_planner.py`'s own copy on the PRD's spelling. The `prd` fact below survives the migration
# because the drafter's PRD-tension step reads it; it is now derived from the catalogue's `prd`-role
# entry instead of a filesystem guess.
_PRD_ROLE = "prd"

# `## Stories` bullet grammar (skills/drafter/references/issue-templates.md;
# docs/specs/drafter.md "Artifacts written", Epic `## Stories` row) — byte-identical regexes to
# `prep_planner.py`'s (restated locally; see the
# module docstring's "no prep-to-prep imports" note).
_SECTION_HEADING_RE = re.compile(r"^##(?!#)")


# ---------------------------------------------------------------------------
# _DiscardStream / _forward_decision — identical pattern to prep_planner.py / prep_resolver.py /
# prep_evaluator.py (S8 retro §5's accepted-reference emit-through-a-stream shape).
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
    a decision was present. Mirrors `prep_planner._forward_decision` / `prep_resolver._forward_decision`
    exactly."""
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


# ---------------------------------------------------------------------------
# State-vector type detection (docs/specs/drafter.md "Overview" + the two "Special case" rows).
# ---------------------------------------------------------------------------


def _detect_type(labels, title):
    lowered_labels = {(label or "").strip().lower() for label in labels or []}
    if "epic" in lowered_labels or _EPIC_TITLE_PREFIX_RE.match(title or ""):
        return "epic"
    if "story" in lowered_labels:
        return "story"
    if "question" in lowered_labels:
        return "question"
    return "standard"


def _suggested_playbook(mode, issue_type):
    """Map `(mode, issue_type)` to the suggested S15 playbook filename (see module docstring's
    `suggested_playbook` paragraph for the full rationale)."""
    if mode == "new":
        return "new.md"
    if issue_type == "question":
        return "question.md"
    return "revise.md"


# ---------------------------------------------------------------------------
# git rev-parse HEAD — root's own SHA (informational only; see module docstring's "No workspace").
# ---------------------------------------------------------------------------


def _root_sha(root):
    result = process.run(["git", "rev-parse", "HEAD"], cwd=root)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Repo-context inventory (docs/specs/drafter.md "Repo context probe").
# ---------------------------------------------------------------------------


def _template_inventory(root):
    """`.github/ISSUE_TEMPLATE/*` listing at `root` (docs/specs/drafter.md "Repo context probe") — a
    plain filesystem read,
    no `git`/`gh` call (root IS the drafter's grounding vantage; see module docstring). `present`
    means "at least one template file exists"; an empty (or absent) directory both report
    `present: False` so the router falls back to the built-in templates identically either way.
    """
    dir_path = Path(root) / ".github" / "ISSUE_TEMPLATE"
    if not dir_path.is_dir():
        return {"present": False, "dir": str(dir_path), "files": []}
    files = sorted(entry.name for entry in dir_path.iterdir() if entry.is_file())
    return {"present": len(files) > 0, "dir": str(dir_path), "files": files}


def _prd_presence(entries):
    """The PRD fact, derived from the catalogue's first `prd`-role entry.

    Kept as its own key (rather than making the playbook hunt through `entries`) because the
    drafter's PRD-tension step reads exactly this one fact, and it read it before the catalogue
    existed — so the fact path `facts.repo_context.docs.prd` survives the migration unchanged.
    A repo that declares no `prd`-role document reports `present: False`, the same shape the
    four-candidate filesystem search reported when it found nothing. A `prd` entry naming a
    directory (a document set) carries its `members` — repo-root-relative, like every catalogue path
    — so the PRD-tension step reads files rather than being handed a bare directory; a file entry
    carries `members: None`.
    """
    entry = doc_catalogue.entry_for_role(entries, _PRD_ROLE)
    if entry is None or not entry.get("present"):
        return {"present": False, "path": None, "members": None}
    return {"present": True, "path": entry["abs_path"], "members": entry.get("members")}


def _grounding_docs(root):
    """`(docs_fact, notices)` — the catalogue as read at `root` (the drafter's grounding vantage is
    the ambient checkout, so an uncommitted catalogue edit counts here, unlike the planner's
    already-ensured checkout at `plan_ref`)."""
    entries, notices = doc_catalogue.read_catalogue(root)
    return {"entries": entries, "prd": _prd_presence(entries)}, notices


def _fetch_labels(repo, cwd=None):
    """`gh label list --repo <repo> --limit 100 --json name,description,color` (docs/specs/drafter.md
    "Repo context probe"
    `gh label list --limit 100`) — so the router maps feedback to existing labels rather than
    inventing new ones (docs/specs/drafter.md "Artifacts read", existing-labels row), and so it can
    tell whether a needed `audience:*` label
    already exists before offering to create it (`_shared/question-issue.md`). Returns
    `(labels, decision_or_none)`.
    """
    result = process.run(
        ["gh", "label", "list", "--repo", repo, "--limit", "100", "--json", "name,description,color"],
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
    raw = json.loads(result.stdout)
    return [
        {"name": item.get("name"), "description": item.get("description"), "color": item.get("color")}
        for item in raw
    ], None


def _read_oq_marker_config(root):
    """The `<!-- drafter-open-question-markers -->` config block (docs/specs/drafter.md
    "OQ marker config read";
    `_shared/open-question-detection.md`'s "Config block (preferred hint)") — raw interior text,
    never interpreted here (facts by script, meaning by model: the register-location/inline-pattern/
    open-status-rule prose is the router's own reading job). Absent -> `heuristics_active: True`,
    the signal to fall back to the built-in heuristic cue list (`open-question-detection.md`'s
    "Heuristic fallback" cues — also not enumerated here; they are prompt-side prose, not a fact a
    script derives).
    """
    present, lines, source = config_block.read_block_anywhere(root, OQ_MARKER_CONFIG_BLOCK)
    return {
        "oq_markers": {
            "present": present,
            "raw": "\n".join(lines) if present else None,
            "source": source,
        },
        "heuristics_active": not present,
    }


# ---------------------------------------------------------------------------
# Revise facts (mode == "revise" only).
# ---------------------------------------------------------------------------


def _extract_body(envelope, key):
    body = envelope.get(key)
    if body is None and envelope.get("%s_mode" % key) == "path":
        body = Path(envelope["%s_path" % key]).read_text(encoding="utf-8")
    return body or ""


def _build_revise_dod(issue_body, target, repo, cwd=None):
    """`facts.revise.dod` — the body's DoD shape (`parse.dod_summary`) plus `slice`, and the
    notice `branching.classify_parent` returned, if any.

    The revise playbook renames a checklist sitting under another heading to `## Definition of
    done` — the one heading the planner, resolver and evaluator parse — EXCEPT on a deliverable
    slice, whose `## Acceptance criteria` is the slicer's deliberate shape
    (`skills/slicer/references/slicing-method.md`). A slice is "a sub-issue of a non-epic" by
    construction, and the parent node carries no labels, so telling the two apart costs one `gh`
    read. `slice` is therefore resolved LAZILY — only when the rename would otherwise fire (no DoD,
    another checklist, a parent) — and is `false` when it cannot apply, `true`/`false` from the
    parent's type, or `null` when that lookup failed (the playbook then leaves the heading alone — the
    planner's DoD-heading card still offers the rename, so an unknown never costs a card here).
    """
    dod = parse.dod_summary(issue_body)
    dod["slice"] = False
    notice = None
    parent = (target or {}).get("parent")
    if not dod["present"] and dod["other_checklists"] and parent:
        kind, notice = branching.classify_parent(repo, parent["number"], cwd=cwd)
        dod["slice"] = {
            branching.PARENT_KIND_EPIC: False,
            branching.PARENT_KIND_NON_EPIC: True,
        }.get(kind)
    return dod, notice


def _build_revise_facts(issue_envelope):
    """Assemble revise-mode-only facts (docs/specs/drafter.md "Revise-mode gather"): the plan-marker's presence + staged
    body (grounds the revise against the planner's approach; lets the router decide whether Step
    R6's staleness flag applies), the open-PR list (already filtered by `gh_gather.references_issue`
    — surfaced so the router can coordinate before editing, docs/specs/drafter.md "Artifacts read"),
    and the
    closed-by-PR/project references `--extra-json` folded in (same row).
    """
    plan_present = bool(issue_envelope.get("marker_comment_present"))
    plan_facts = {
        "present": plan_present,
        "comment_id": issue_envelope.get("marker_comment_id"),
        "comment_url": issue_envelope.get("marker_comment_url"),
    }
    if plan_present:
        plan_facts["body_mode"] = issue_envelope.get("marker_comment_mode")
        if issue_envelope.get("marker_comment_mode") == "path":
            plan_facts["body_path"] = issue_envelope.get("marker_comment_path")
        else:
            plan_facts["body"] = issue_envelope.get("marker_comment_body")

    return {
        "plan": plan_facts,
        "open_prs": issue_envelope.get("open_prs") or [],
        "closed_by_pull_requests_references": issue_envelope.get("closedByPullRequestsReferences"),
        "project_items": issue_envelope.get("projectItems"),
    }


# ---------------------------------------------------------------------------
# attention
# ---------------------------------------------------------------------------


def _build_attention(
    open_question_candidates,
    target,
    mode,
    extra=None,
    docs=None,
    catalogue_absent=False,
    ambient=None,
):
    attention = []
    if ambient is not None:
        attention.append(
            "invoked from branch '%s' (%s #%s — %s) — raise the ambient-issue gate before filing; "
            "unrelated is a normal answer" % (
                ambient["branch"],
                ambient["type"],
                ambient["number"],
                ambient["state"],
            )
        )
    if docs is not None:
        entries = docs.get("entries") or []
        if catalogue_absent:
            attention.append(
                "no doc catalogue in docs/README.md — drafting ungrounded; run "
                "/ctrl-freaq:setup to declare this repo's grounding docs"
            )
        elif not entries:
            attention.append(
                "doc catalogue declares no documents — drafting ungrounded until it names some"
            )
        for missing in doc_catalogue.missing_entry_paths(entries):
            attention.append(
                "doc catalogue names '%s', absent in this checkout — a stale entry, or a doc this "
                "branch has not merged yet" % missing
            )
    for group in open_question_candidates or []:
        attention.append(
            "open question '%s' has %d tracker candidate(s) — do not record it as (not filed)"
            % (group["oq_id"], len(group["candidates"]))
        )
    if target is not None and mode == "revise" and (target.get("state") or "").upper() == "CLOSED":
        attention.append(
            "target issue #%s is closed — resolve the 'Closed issue' gate before revising"
            % target["number"]
        )
    if extra:
        attention.extend(extra)
    return attention


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def build_facts(repo, issue=None, root=".", scratch_dir=None, cwd=None):
    """Assemble the drafter's complete facts block and return the envelope dict WITHOUT printing
    it (the testable core, mirroring `prep_planner.build_facts` / `prep_resolver.build_facts` /
    `prep_evaluator.build_facts`). Returns `None` after a `needs_decision` envelope has already
    been emitted on stdout.
    """
    root = str(Path(root).resolve())
    if scratch_dir is None:
        # New mode has no issue number to key on, so the process id stands in as <N>: a follow-up
        # batch spawns its proxy drafters concurrently, and a shared dir would let one run's staged
        # body clobber another's before gh_persist reads it.
        #
        # The separation this buys is strong but NOT absolute, and the difference matters if you
        # ever lean on it harder. THIS process exits in about a second; the directory it names stays
        # in use for the minutes until the session stages its body and gh_persist reads the path. So
        # the key is unique against every prep alive at the same moment (which is what a concurrent
        # batch is), not against one whose pid the OS has since recycled — a wrap of the pid space
        # inside that window (macOS wraps at 99999; older Linux at pid_max 32768) would re-issue it.
        # Reaching for a stronger key would mean a collision-resistant token instead of the pid, at
        # the cost of the `/tmp/gh-<skill>-<N>/` shape every other prep follows.
        scratch_dir = "/tmp/gh-drafter-%s" % (issue if issue else "new-%d" % os.getpid())
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    root_sha = _root_sha(root)

    # Every composed core's non-blocking degradations accumulate here and ride out in the envelope's
    # own `notices` (architecture.md §3) — including on the `needs_decision` paths below, since a
    # decision emitted without them would silently drop the fact that this repo declares no grounding
    # docs at all.
    notices = []

    # 1) Repo-context inventory (common core — every mode gets it; docs/specs/drafter.md "Repo
    #    context probe"). Templates/docs are local filesystem reads; labels is the one gh call every
    #    invocation makes.
    config = _read_oq_marker_config(root)
    docs_fact, docs_notices = _grounding_docs(root)
    notices.extend(docs_notices)
    repo_context = {
        "templates": _template_inventory(root),
        "docs": docs_fact,
    }
    labels, labels_decision = _fetch_labels(repo, cwd=cwd)
    if _forward_decision(labels_decision, notices=notices):
        return None
    repo_context["labels"] = labels

    # 1b) Ambient-branch issue — which issue this checkout is standing in (see the module
    #     docstring's `facts.ambient` paragraph). Non-gating by construction: `(None, [])` on
    #     anything unparseable, so this can never turn a working session into a decision.
    ambient, ambient_notices = branching.detect_ambient_issue(root, repo, cwd=cwd)
    notices.extend(n for n in ambient_notices if n not in notices)

    target = None
    vector_type = None
    mode = "new"
    open_questions = []
    open_question_candidates = []
    revise_facts = None
    extra_attention = []
    sections = {}

    # 2) Target-issue gather — revise mode ONLY (`--issue` given). `new` mode has no
    #    target yet, so this whole block (and its 3-4 gh calls) is skipped entirely.
    if issue:
        exit_code, issue_envelope = gh_gather.run(
            str(issue),
            repo,
            marker_prefix=PLAN_MARKER,
            scratch_dir=scratch_dir,
            extra_json=REVISE_EXTRA_JSON,
            env=None,
            stream=_DiscardStream(),
        )
        if issue_envelope is not None and issue_envelope.get("status") == "needs_decision":
            if _forward_decision(
                issue_envelope["decision"],
                notices=notices + list(issue_envelope.get("notices") or []),
            ):
                return None
        if exit_code != 0:
            sys.stderr.write(
                "prep_drafter: gh_gather on issue #%s failed (exit %d)\n" % (issue, exit_code)
            )
            sys.exit(1)

        issue_body = _extract_body(issue_envelope, "issue_body")
        issue_labels = [label.get("name") for label in issue_envelope.get("labels") or []]
        issue_title = issue_envelope.get("title") or ""
        vector_type = _detect_type(issue_labels, issue_title)
        mode = "revise"

        target = {
            "kind": "issue",
            "number": issue_envelope["number"],
            "title": issue_envelope["title"],
            "state": issue_envelope["state"],
            "labels": issue_labels,
            "blocked_by": issue_envelope.get("blocked_by") or [],
            "blocking": issue_envelope.get("blocking") or [],
            "deps_available": issue_envelope.get("deps_available"),
            "parent": issue_envelope.get("parent"),
            "sub_issues": issue_envelope.get("sub_issues") or [],
            "sub_issues_summary": issue_envelope.get("sub_issues_summary") or {},
            "subissues_available": issue_envelope.get("subissues_available"),
        }

        # 3) Search-before-file open-question candidates — the target body IS the source to scan
        #    (S14-promoted oq_tracker.build_open_question_candidates; see module docstring).
        open_questions, open_question_candidates, oq_decision = oq_tracker.build_open_question_candidates(
            issue_body, repo, cwd=cwd
        )
        if _forward_decision(oq_decision, notices=notices):
            return None

        # 4) Mode-specific facts. Every `--issue` run is a revise, epic targets included (#16).
        revise_facts = _build_revise_facts(issue_envelope)
        revise_facts["dod"], dod_notice = _build_revise_dod(issue_body, target, repo, cwd=cwd)
        if dod_notice and dod_notice not in notices:
            notices.append(dod_notice)

        sections = {
            key: value
            for key, value in issue_envelope.items()
            if key.startswith(("issue_body", "thread", "marker_comment"))
        }

    # The self-referential case: revising #164 while standing in #164's own branch names no
    # relationship the drafter could offer — the issue cannot be related to itself.
    if ambient is not None and target is not None and ambient["number"] == target["number"]:
        ambient = None

    suggested_playbook = _suggested_playbook(mode, vector_type)
    vector = {"mode": mode, "type": vector_type}

    facts = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root, "sha": root_sha},
        "ambient": ambient,
        "target": target,
        "vector": vector,
        "suggested_playbook": suggested_playbook,
        "config": config,
        "repo_context": repo_context,
        "open_questions": open_questions,
        "open_question_candidates": open_question_candidates,
        "attention": _build_attention(
            open_question_candidates,
            target,
            mode,
            extra=extra_attention,
            docs=docs_fact,
            catalogue_absent=DOC_CATALOGUE_ABSENT in notices,
            ambient=ambient,
        ),
        "notices": notices,
    }
    if revise_facts is not None:
        facts["revise"] = revise_facts
    if sections:
        facts["sections"] = sections

    return facts


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument(
        "--issue",
        default=None,
        help="target issue number — selects revise mode; omit for new-issue mode",
    )
    parser.add_argument("--root", default=".", help="project root (architecture.md §6 vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for spilled sections (default: /tmp/gh-drafter-<issue>, or "
        "/tmp/gh-drafter-new-<pid> in new mode)",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="explicit working directory for the gh calls that have no --repo scoping of their "
        "own (per-story state fetch, OQ tracker search); optional in normal use, provided for "
        "test-injection — mirrors prep_planner.py's/prep_resolver.py's identical --cwd knob",
    )
    parser.add_argument(
        "--oq-query",
        action="append",
        default=None,
        metavar="TEXT",
        help="one-shot tracker de-dup lookup for an open-question topic named directly (repeatable) "
        "— mirrors prep_planner.py's identical flag (oq_tracker.build_oq_query); short-circuits to "
        "the one-shot payload, no full facts assembly, regardless of --issue",
    )
    args = parser.parse_args(argv)

    if args.oq_query:
        payload, notices, decision = oq_tracker.build_oq_query(args.repo, args.oq_query, cwd=args.cwd)
        if decision is not None:
            emit_needs_decision(decision, notices=notices)
            return EXIT_OK
        emit_ok(payload=payload, notices=notices)
        return EXIT_OK

    facts = build_facts(
        args.repo, issue=args.issue, root=args.root, scratch_dir=args.scratch_dir, cwd=args.cwd
    )
    if facts is None:
        return EXIT_OK
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
