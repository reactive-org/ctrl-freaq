#!/usr/bin/env python3
"""prep_slicer.py — the slicer's complete facts block in one call (architecture.md §4; #17, #16).
Assembles the session's entire starting state — the target issue + thread, its type, the **altitude**
the cut runs at, the **refusal** set (the conditions under which decomposing would be wrong), the
existing child set (so a re-run resumes instead of duplicating), the consuming repo's declared
grounding docs, and `suggested_playbook` — as ONE JSON envelope on stdout, so the slicer session's
startup is one Python process, never a subprocess chain.

The slicer cuts one filed issue into ordered children and files them as native sub-issues
(`skills/_shared/epic-story-hierarchy.md`). **One operation, two altitudes** (#16), differing in
exactly one parameter — the independence bar each child must clear, set by whether the child gets its
own branch and PR:

  - ``story`` altitude — a story or standalone issue cut into **deliverable slices**. A slice is a
    *phase marker*: the resolver ships it as a phase on the parent's branch and closes it as that
    phase lands, so its bar is independently *demonstrable*.
  - ``epic`` altitude — an epic cut into **stories**. Each story gets its own branch, PR, review and
    merge, so its bar is independently *shippable*.

Either way the point of filing children as issues is that GitHub's rollup then tracks delivery
progress at parent altitude. `vector.altitude` is the fact the flow reads; the bar itself is stated
once, in `skills/slicer/references/slicing-method.md` §1.

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external
processes any script may spawn are git/gh")::

    gh_gather.run(..., stream=)      -- the target's body/thread + native blocked_by/blocking + the
                                        native parent/sub_issues relation + the research-dossier
                                        marker, in ONE round-trip
    branching.detect_type            -- the shared epic/story/standard core (import-only), with a
                                        local `question` arm layered on (prep_drafter's precedent)
    doc_catalogue.read_catalogue     -- the consuming repo's `<!-- doc-catalogue -->` grounding docs
    parse.parse_oq_links             -- the target's `## Open questions` section, for the
                                        `in-scope (blocked)` disposition

Plus ONE direct `gh issue view <parent> --json state,title,labels` — and only when the target
actually has a parent — because the sub-issue/parent node shape carries **no labels**
(`epic-story-hierarchy.md` "The facts"), so the parent cannot be typed from the gather alone. Same
shape and the same `AUTH_REQUIRED` handling as `prep_planner._fetch_story_state` /
`prep_drafter`'s equivalent; architecture.md §1 permits a prep to spawn `gh` directly. Two further
`gh` calls are made only when the epic-altitude paths need them: one per FILED `## Stories` checklist
entry on a legacy epic (the relocated epic-revise read, below), and one per `--adopt` candidate.

Usage::

    prep_slicer.py <issue> <owner/repo> [--root PATH] [--scratch-dir PATH] [--cwd PATH]
                                        [--promote] [--adopt N]...
    prep_slicer.py <owner/repo> --adopt-check N [--adopt-check N]... [--cwd PATH]

``--promote`` and ``--adopt`` are passed by the **router**, which can read the operator's invocation
prose; prep cannot. ``--promote`` says "the operator asked for this standard issue to become an Epic"
(the planner's seam-gate off-ramp A), so the altitude is forced to ``epic`` and `facts.promotion` is
true — the flow then rewrites the target as an Epic behind its own diff-and-confirm gate before
cutting. ``--adopt N`` names an ALREADY-FILED issue to adopt as a child; `facts.adoption_candidates`
carries each one's live state so the flow can present it, and the write itself is
`gh_persist.py add-parent`. ``--adopt-check`` is the one-shot mode for candidates the operator names
mid-session (the `prep_drafter --oq-query` precedent): it emits only the candidates, no facts block,
so a mid-flow lookup never re-runs the whole gather.

**No workspace — the slicer grounds on the CURRENT checkout**, the same root-only vantage
`prep_drafter.py` and `prep_researcher.py` take, and for the same threat-model reason: the slicer
reads no PR head and gates no merge, so architecture.md §6's "a PR must not weaken its own gates"
cannot apply. It runs *before* a workspace exists (its whole output is the input to planning, which
precedes `workspace-open`). `root` therefore carries only `{path, sha}` — informational, never
enforced — and the doc catalogue is read at that same working vantage, deliberately NOT through
a pinned ref: a catalogue names no gate (`skills/_shared/doc-catalogue.md` "Where the catalogue is
read from"), and an uncommitted catalogue edit should count for a decomposition the operator is
running right now.

**Refusals are facts, not decision codes.** `vector.refusals` is a list of reason tokens from a
closed set; the router renders the matching refusal handoff and stops. They are deliberately NOT
`needs_decision` codes: a refusal is not an ambiguity for the operator to resolve with one answer —
it is a *routing outcome* with its own handoff (to `setup`, to the question-resolver, or terminal),
and architecture.md §3's decision set is for genuine one-card decisions. Adding four codes to a
closed set to express "this stage does not apply here" would be a contract change that buys nothing.

An **epic target is no longer refused** (#16): it is the epic-altitude happy path.

  - ``slice-target``   — the target is itself a slice (it has a parent, and that parent is not an
                         epic). **A slice is never sliced**: it has no branch of its own, so a
                         sub-slice could not ship, and a fourth level would break the
                         by-construction identification every reader relies on.
  - ``question-target`` — a `question` issue is answered by a human in its thread; there is no
                         buildable scope to cut.
  - ``closed-target``  — decomposing delivered work files slices nothing will ever close, leaving a
                         permanently false rollup.
  - ``blocked``        — an OPEN native blocker, or an `in-scope (blocked)` open-question entry.
                         Slicing against an unanswered question produces slices the answer may
                         invalidate. Read from LIVE state (`blocked_by`, and the tracker for OQ
                         entries), never from a prose claim in the body — a recorded blocker line is
                         a claim made at authoring time, not a fact.

`DOC_CATALOGUE_ABSENT` stays a **notice**, not a refusal, even though the grounding gate is a hard
refusal in the playbook. The distinction is load-bearing: prep cannot see whether the operator named
grounding sources at invocation, so only the flow can decide that the gate fails. Prep reports the
absence loudly and lets the playbook refuse.

**`vector.mode` — the two-value closed set ``"fresh"`` / ``"resume"``.** Derived from one
script-visible signal: whether the target already has children. `resume` is what makes a re-run after
a partial failure safe — the flow reports what exists and cuts only the remainder, rather than
re-filing.

**`facts.children` — one key, both altitudes**, with `kind` naming which (`"slices"` / `"stories"`):

  - Story altitude reads the native sub-issue set only. `children.next_index` is parsed best-effort
    from the existing `<N>/S<K>` designator titles so a resumed run continues the numbering instead
    of colliding; `source` is null (slices postdate the native relation, so they have no fallback —
    `epic-story-hierarchy.md`'s "The slice edge has no checklist fallback and never will").
  - Epic altitude reads through the **two-tier read** that contract defines, and `source` reports
    which tier answered: `sub-issues` (native), `checklist` (the legacy `## Stories` section on an
    epic filed before the relation existed, or on a host that doesn't serve it), or `mixed` (both,
    unioned by number with native state winning — never one half dropped, which would silently lose
    stories). This is v1's epic-revise gather, relocated here from `prep_drafter.py` with #16: the
    drafter no longer decomposes epics, so the read belongs where the reconciliation now happens.
    Stories are titled plainly, so `next_index` is null.

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"`` or
``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified hard
`gh`/`git` failure surfaced by the composed gather — stderr carries the faithful error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402  (import after sys.path setup, by necessity; shared type-detection core)
import doc_catalogue  # noqa: E402  (the consuming repo's declared grounding docs)
import gh_gather  # noqa: E402
import parse  # noqa: E402  (the `## Open questions` parse for the in-scope-blocked refusal)
import story_set  # noqa: E402  (the epic story set's two-tier read)
from pipelib import process  # noqa: E402
from pipelib.decisions import (  # noqa: E402
    AUTH_REQUIRED,
    DOC_CATALOGUE_ABSENT,
    SUBISSUE_FIELD_UNAVAILABLE,
    needs_decision,
)
from pipelib.envelope import emit_needs_decision, emit_ok  # noqa: E402

# The research-dossier marker (`skills/researcher/references/dossier-schema.md`). The slicer treats a
# dossier as an OPTIONAL grounding input — current external truth the cut may cite — never a
# requirement, and never a mode signal the way it is for the researcher.
RESEARCH_MARKER = "<!-- issue-research:v1 -->"

# The `question` label arm `branching.detect_type` doesn't carry (it returns epic/story/standard
# only). Layered locally, exactly as `prep_drafter.py` does for its own four-way type space — see
# `prep_planner.py`'s module docstring for the "no prep-to-prep imports" convention that keeps this a
# local arm over the shared core rather than a fourth copy of the whole rule.
_QUESTION_LABEL = "question"
_EPIC_LABEL = "epic"

# The slice title designator: `<parent#>/S<K> — <behaviour>` (recorded on #17). Parsed best-effort to
# resume numbering; a child whose title doesn't match simply doesn't contribute an index, which is
# why `next_index` falls back to "one past the child count".
_SLICE_DESIGNATOR_RE = re.compile(r"^\s*(\d+)\s*/\s*S(\d+)\b")


# The closed refusal set (see the module docstring for each token's rationale). There is deliberately
# no `epic-target`: #16 made an epic the epic-altitude happy path.
REFUSAL_SLICE_TARGET = "slice-target"
REFUSAL_QUESTION_TARGET = "question-target"
REFUSAL_CLOSED_TARGET = "closed-target"
REFUSAL_BLOCKED = "blocked"


class _DiscardStream:
    """A minimal write-sink for `gh_gather.run(stream=...)` — see `prep_researcher._DiscardStream` /
    `prep_evaluator._DiscardStream` for the full rationale (restated locally per those modules' own
    note: sharing a two-method sink across prep modules is not the in-process composition
    architecture.md §2 asks for)."""

    def write(self, _data):
        return None

    def flush(self):
        return None


def _forward_decision(decision, notices=None):
    """Emit a composed core's returned `decision` AS-IS on prep's own stdout and return `True` when a
    decision was present. Mirrors `prep_researcher._forward_decision` exactly."""
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


def _root_sha(root):
    result = process.run(["git", "rev-parse", "HEAD"], cwd=root)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Type detection (shared core + the local `question` arm).
# ---------------------------------------------------------------------------


def _has_epic_label(labels):
    """Whether the `epic` LABEL is present — a stronger signal than `branching.detect_type`'s title
    arm, which also types any issue titled `Epic: …` as an epic. The refusal set needs the stronger
    signal in one place: overriding a structural hierarchy signal (this target has a parent) must rest
    on more than a human titling convention."""
    return _EPIC_LABEL in {(label or "").strip().lower() for label in labels or []}


def _detect_type(labels, title):
    """`branching.detect_type` widened by one arm. The shared core answers epic/story/standard; a
    `question` label wins over the `standard` fallback but never over `epic`/`story` (a
    pathologically double-labelled issue keeps the core's documented precedence)."""
    core = branching.detect_type(labels, title)
    if core == "standard" and _QUESTION_LABEL in {
        (label or "").strip().lower() for label in labels or []
    }:
        return "question"
    return core


def _fetch_parent_state(parent_number, repo, cwd=None):
    """The parent's `state,title,labels` — the ONE extra `gh` call this prep makes, and only when the
    target has a parent. Needed because the native relation's node shape carries no labels, so the
    parent cannot be typed from the gather. Returns `(state_dict, decision_or_none)`, the same shape
    (and the same `AUTH_REQUIRED` handling) as `prep_planner._fetch_story_state`."""
    result = process.run(
        ["gh", "issue", "view", str(parent_number), "--repo", repo, "--json", "state,title,labels"],
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
    data = json.loads(result.stdout or "{}")
    labels = [label.get("name") for label in data.get("labels") or []]
    return {
        "number": int(parent_number),
        "state": data.get("state"),
        "title": data.get("title"),
        "labels": labels,
        "type": _detect_type(labels, data.get("title") or ""),
    }, None


def _fetch_story_state(repo, story_number, cwd=None):
    """`gh issue view <NN> --json state,title,labels` for one FILED `## Stories` checklist entry — the
    per-story live-state read a legacy epic needs, since a checklist bullet carries no state of its
    own. Relocated from `prep_drafter.py` with #16 (same shape, same `AUTH_REQUIRED` handling).
    Returns `(state_dict, decision_or_none)`."""
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
    data = json.loads(result.stdout or "{}")
    return {
        "state": data.get("state"),
        "title": data.get("title"),
        "labels": [label.get("name") for label in data.get("labels") or []],
    }, None


def _fetch_adoption_candidate(repo, number, cwd=None):
    """One `--adopt` candidate's live state, including its CURRENT parent — the field that decides
    whether adopting it would steal another parent's child. `parent` is capability-gated exactly as
    the gather's ladder gates it, so a host that doesn't serve the relation degrades to
    `parent: None` (unknown) + a notice rather than failing the whole prep.

    The degradation notice is deliberately NOT ``SUBISSUES_UNSUPPORTED``: the flow reads that token as
    "a child was filed but the relation was not established — stop, file no more"
    (``skills/slicer/playbooks/cut.md``). Nothing has been written here, so reusing it would abort the
    cut and report a nonexistent unparented child. ``SUBISSUE_FIELD_UNAVAILABLE`` says the narrower,
    true thing: this host could not tell us the candidate's CURRENT parent.

    Returns `(candidate_dict, notices, decision_or_none)`."""
    notices = []
    fields = "state,title,labels,parent"
    result = process.run(
        ["gh", "issue", "view", str(number), "--repo", repo, "--json", fields], cwd=cwd
    )
    parent_field_unavailable = False
    if (
        result.returncode != 0
        and not result.auth_required
        and gh_gather._is_unknown_field_error(result.stderr)
        and "parent" in (result.stderr or "").lower()
    ):
        # Scoped to `parent`: `_is_unknown_field_error` matches any unknown-field text, and the retry
        # below drops ONLY that field, so attributing an unrelated unknown-field error to it would
        # mislabel a failure the retry cannot fix.
        parent_field_unavailable = True
        notices.append(SUBISSUE_FIELD_UNAVAILABLE)
        result = process.run(
            ["gh", "issue", "view", str(number), "--repo", repo, "--json", "state,title,labels"],
            cwd=cwd,
        )
    if result.auth_required:
        return None, notices, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    data = json.loads(result.stdout or "{}")
    labels = [label.get("name") for label in data.get("labels") or []]
    parent_node = data.get("parent")
    return (
        {
            "number": int(number),
            "state": data.get("state"),
            "title": data.get("title"),
            "labels": labels,
            "type": _detect_type(labels, data.get("title") or ""),
            "parent": (parent_node or {}).get("number") if parent_node else None,
            # None (unknown), never False, when the field could not be read: `False` is a CLAIM that
            # the candidate is unparented, and acting on it silently re-parents another epic's child.
            "already_parented": None if parent_field_unavailable else bool(parent_node),
        },
        notices,
        None,
    )


def _build_adoption_candidates(numbers, repo, cwd=None):
    """Every `--adopt` candidate, in the order the operator named them (which becomes their panel
    order if approved). Returns `(candidates, notices, decision_or_none)`."""
    candidates = []
    notices = []
    for number in numbers or []:
        candidate, candidate_notices, decision = _fetch_adoption_candidate(repo, number, cwd=cwd)
        for notice in candidate_notices:
            if notice not in notices:
                notices.append(notice)
        if decision is not None:
            return None, notices, decision
        candidates.append(candidate)
    return candidates, notices, None


# ---------------------------------------------------------------------------
# Altitude + the child set (resume, don't duplicate).
# ---------------------------------------------------------------------------


def _detect_altitude(target_type, promote=False):
    """Which bar this cut runs at (module docstring). An epic's children are stories (*shippable*);
    everything else's are deliverable slices (*demonstrable*). `--promote` forces epic altitude
    because the target BECOMES an epic before the cut — the flow rewrites it behind its own gate."""
    return "epic" if (target_type == "epic" or promote) else "story"


def _build_story_set(sub_issues, issue_body, repo, cwd=None):
    """The epic's existing story set through `epic-story-hierarchy.md`'s two-tier read, with `source`
    reporting which tier answered so the flow knows whether there are checkboxes to reconcile at all.

    A checkbox/live-state MISMATCH surfaces in `attention` rather than being reconciled here:
    reconciling the body text is a write the flow proposes at its gate, not a fact this script
    computes. Returns `(children_dict, attention_lines, decision_or_none)`.
    """
    stories, decision = story_set.read(
        sub_issues, issue_body, fetch_state=lambda number: _fetch_story_state(repo, number, cwd=cwd)
    )
    if decision is not None:
        return None, None, decision
    attention = [
        "story #%s checkbox is %s but live state is %s"
        % (e["number"], "checked" if e["checked"] else "unchecked", e["state"])
        for e in story_set.filed(stories["checklist"])
        if e["checked"] != ((e["state"] or "").upper() == "CLOSED")
    ]
    # `mixed` only when BOTH tiers actually contributed. A checklist whose every entry is already
    # native contributes nothing after the dedup, so calling that `mixed` would be untrue — and the
    # attention line it triggers claims those entries have "no native relation", which is exactly
    # backwards, on the strength of which the flow proposes a parent-body write.
    source = story_set.source(stories["native"], stories["checklist"])
    return _children("stories", stories["native"] + stories["checklist"], source=source), attention, None


def _children(kind, entries, source=None, next_index=None):
    """The one `facts.children` shape both altitudes fill (module docstring).

    `count`/`open_count` deliberately count only children that are actually **filed** — an entry with
    `number: None` is a legacy `## Stories` bullet naming a story nobody has filed yet. Counting those
    would make `mode` read `resume` on an epic with zero real children, so the flow would report
    "cut only the remainder" and file nothing, leaving the permanently empty rollup this stage exists
    to prevent. It would also make `count - open_count` read as "all closed" for stories that were
    never filed. `placeholder_count` keeps them representable, and `total_named` is what a progress
    line should describe.
    """
    filed = [e for e in entries if e.get("number") is not None]
    placeholders = [e for e in entries if e.get("number") is None]
    return {
        "kind": kind,
        "entries": entries,
        "count": len(filed),
        "open_count": sum(1 for e in filed if (e.get("state") or "").upper() == "OPEN"),
        "placeholder_count": len(placeholders),
        "total_named": len(entries),
        "next_index": next_index,
        "source": source,
    }


def _build_slice_set(sub_issues, target_number):
    """The existing slice set — one entry per live sub-issue in the sub-issue panel's own order (which
    IS delivery order, since `addSubIssue` appends).

    `next_index` is the numbering a resumed run continues from: one past **whichever is greater** of
    the highest `S<K>` parsed out of the children's titles and the child count. Taking the max (not
    just the parsed highest) is what makes a hand-retitled child safe: if someone renamed `103/S3`,
    its index is unparseable, and continuing from the parsed highest alone would re-issue S3.
    Over-counting collides with nothing; under-counting collides with a live slice.
    """
    entries = []
    highest = 0
    for child in sub_issues or []:
        title = child.get("title") or ""
        match = _SLICE_DESIGNATOR_RE.match(title)
        index = None
        if match is not None and int(match.group(1)) == int(target_number):
            index = int(match.group(2))
            highest = max(highest, index)
        entries.append(
            {
                "number": child.get("number"),
                "title": title,
                "state": child.get("state"),
                "designator_index": index,
            }
        )
    return _children(
        "slices", entries, source=None, next_index=max(highest, len(entries)) + 1
    )


# ---------------------------------------------------------------------------
# Refusals + attention
# ---------------------------------------------------------------------------


def _build_refusals(target_type, target_state, parent, blocked_by, oq_blocked, target_labels=None):
    """The closed refusal set (module docstring). Order is stable so the router's rendering choice is
    deterministic when more than one applies — the router renders the FIRST."""
    refusals = []
    if target_type == "question":
        refusals.append(REFUSAL_QUESTION_TARGET)
    # A parent that is not an epic means the target is itself a slice (epic -> story -> slice, and
    # the relation has exactly three levels), so slicing it would create a fourth.
    #
    # The one exemption is a LABEL-confirmed epic: an epic is the top of the hierarchy, so a parent on
    # it is malformed data rather than evidence the epic is a slice, and refusing there would block
    # the epic-altitude happy path for the one shape it can never legitimately be. The exemption is
    # deliberately NOT extended to a title-detected epic: `branching.detect_type` types any issue
    # titled `Epic: …` as an epic, so a plainly-titled sub-issue of a story would otherwise escape
    # this guard entirely and the flow would file stories beneath a story — the fourth level the
    # by-construction identification rule forbids. A title is a human convention; only the label is
    # evidence strong enough to override a structural signal.
    if parent is not None and parent.get("type") != "epic" and not _has_epic_label(target_labels):
        refusals.append(REFUSAL_SLICE_TARGET)
    if (target_state or "").upper() == "CLOSED":
        refusals.append(REFUSAL_CLOSED_TARGET)
    if blocked_by or oq_blocked:
        refusals.append(REFUSAL_BLOCKED)
    return refusals


def _open_blockers(blocked_by):
    """The OPEN entries of the native `blocked_by` set. A closed blocker is stale, not a gate — the
    live state decides, every time, never the body's recorded line."""
    return [
        {"number": node.get("number"), "title": node.get("title"), "url": node.get("url")}
        for node in blocked_by or []
        if (node.get("state") or "").upper() == "OPEN"
    ]


def _in_scope_blocked_oqs(oq_entries):
    """The `## Open questions` entries whose disposition is `in-scope (blocked)`
    (`skills/_shared/open-question-links.md`). These gate the cut for the same reason an open native
    blocker does; the section is a tracked-dependency registry, not buildable scope."""
    blocked = []
    for entry in oq_entries or []:
        disposition = (entry.get("disposition") or "").strip().lower()
        if disposition.startswith("in-scope"):
            blocked.append(
                {
                    "oq_id": entry.get("oq_id"),
                    "question": entry.get("question"),
                    "disposition": entry.get("disposition"),
                }
            )
    return blocked


def _build_attention(
    target,
    refusals,
    children,
    grounding_docs,
    catalogue_absent,
    open_blockers,
    altitude="story",
    promotion=False,
    promote_requested=False,
    adoption_candidates=None,
):
    """Script-detectable conditions worth surfacing with evidence (architecture.md §4)."""
    attention = []
    for token in refusals:
        if token == REFUSAL_SLICE_TARGET:
            attention.append(
                "target #%s is itself a deliverable slice (its parent is not an epic) — a slice is "
                "never sliced" % target["number"]
            )
        elif token == REFUSAL_QUESTION_TARGET:
            attention.append(
                "target #%s is a question issue — it carries no buildable scope to cut"
                % target["number"]
            )
        elif token == REFUSAL_CLOSED_TARGET:
            attention.append("target #%s is closed — nothing would ever close its slices" % target["number"])
        elif token == REFUSAL_BLOCKED:
            attention.append(
                "target #%s has an open blocker (%s) — slicing against an unanswered question "
                "produces slices the answer may invalidate"
                % (
                    target["number"],
                    ", ".join(
                        ["#%s" % b["number"] for b in open_blockers]
                        or ["an in-scope (blocked) open question"]
                    ),
                )
            )
    if catalogue_absent:
        attention.append(
            "no doc catalogue — the grounding gate will refuse unless the operator names sources; "
            "run /ctrl-freaq:setup to declare this repo's grounding docs in docs/README.md"
        )
    elif not grounding_docs:
        attention.append("doc catalogue declares no documents — the grounding gate has nothing to read")
    for missing in doc_catalogue.missing_entry_paths(grounding_docs):
        attention.append(
            "doc catalogue names '%s', absent in this checkout — a stale entry, or a doc this branch "
            "has not merged yet" % missing
        )
    if children["count"] and not refusals:
        attention.append(
            "target #%s already has %d filed %s — resume mode: report them and cut only the "
            "remainder, never re-file" % (target["number"], children["count"], children["kind"])
        )
    if children.get("placeholder_count") and not refusals:
        attention.append(
            "target #%s's `## Stories` checklist names %d story/stories with no issue number — they "
            "are planned, NOT filed, so they are not part of the resume set: file them as part of "
            "this cut" % (target["number"], children["placeholder_count"])
        )
    if target.get("type") == "epic" and target.get("parent") is not None and not refusals:
        # An epic is the top of the hierarchy, so this is malformed data. The refusal set exempts a
        # label-confirmed epic rather than calling it a slice, which means the anomaly would otherwise
        # be invisible — surface it with evidence instead of swallowing it.
        attention.append(
            "epic #%s has a parent (#%s) — an epic is the top of the hierarchy, so this relation is "
            "malformed; cutting proceeds, but check whether #%s was mislabelled"
            % (target["number"], (target["parent"] or {}).get("number"), target["number"])
        )
    if children.get("source") in ("checklist", "mixed"):
        attention.append(
            "epic #%s carries a legacy `## Stories` checklist (children.source: %s) — it has no native "
            "relation for those entries, so the checklist is still their only record"
            % (target["number"], children["source"])
        )
    if promotion:
        attention.append(
            "promotion requested: #%s is a %s issue that becomes an Epic before the cut — the body "
            "rewrite, title rewrite and label swap ride their own confirm gate, ahead of the cut's "
            "write gate"
            % (target["number"], target["type"])
        )
        if children["count"]:
            # Its existing children are slices by construction, so promoting reinterprets them as
            # stories. Loud, and left to the gate: the operator can drop the promotion or accept a
            # hierarchy that no longer reads the way it was filed.
            attention.append(
                "#%s already has %d sub-issue(s), which are deliverable slices by construction — "
                "promoting it to an Epic reinterprets them as stories; confirm that is intended"
                % (target["number"], children["count"])
            )
    elif promote_requested and altitude == "epic":
        attention.append(
            "promotion requested but #%s is already an epic — nothing to promote; cutting at epic "
            "altitude as usual" % target["number"]
        )
    for candidate in adoption_candidates or []:
        if candidate.get("already_parented"):
            attention.append(
                "adoption candidate #%s already has parent #%s — adopting it would move it out of "
                "that parent; confirm before the write" % (candidate["number"], candidate["parent"])
            )
        if (candidate.get("state") or "").upper() == "CLOSED":
            attention.append(
                "adoption candidate #%s is closed — legitimate for already-shipped scope an epic "
                "collects, but it will never contribute open progress to the rollup"
                % candidate["number"]
            )
        if candidate.get("type") == "epic":
            attention.append(
                "adoption candidate #%s is itself an epic — an epic is never another epic's child "
                "(the hierarchy has exactly three levels)" % candidate["number"]
            )
    return attention


def _suggested_playbook(refusals):
    """One playbook (`cut.md`); `fresh` and `resume` differ only in VALUES, never in actions taken
    (CLAUDE.md's "parameterize before you playbook"). A refusal routes to no playbook at all — the
    router renders the matching refusal handoff and stops — so this returns `None`."""
    return None if refusals else "cut.md"


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def build_facts(issue, repo, root=".", scratch_dir=None, cwd=None, promote=False, adopt=None):
    """Assemble the slicer's complete facts block and return the envelope dict WITHOUT printing it
    (the testable core, mirroring `prep_researcher.build_facts`). Returns `None` after a
    `needs_decision` envelope has already been emitted on stdout."""
    root = str(Path(root).resolve())
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-slicer-%s" % issue
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    # Every composed core's non-blocking degradations accumulate here and ride out in the envelope's
    # own `notices` (architecture.md §3) — including on the `needs_decision` paths below, since a
    # decision emitted without them would silently drop the fact that this repo declares no
    # grounding docs at all.
    notices = []

    root_sha = _root_sha(root)

    # 1) The target — one round-trip: body, thread, native deps, the parent/sub-issue relation, and
    #    the optional research-dossier marker.
    exit_code, issue_envelope = gh_gather.run(
        str(issue),
        repo,
        marker_prefix=RESEARCH_MARKER,
        scratch_dir=scratch_dir,
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
        sys.stderr.write("prep_slicer: gh_gather on issue #%s failed (exit %d)\n" % (issue, exit_code))
        sys.exit(1)
    notices.extend(issue_envelope.get("notices") or [])

    issue_labels = [label.get("name") for label in issue_envelope.get("labels") or []]
    issue_title = issue_envelope["title"]
    target_type = _detect_type(issue_labels, issue_title)

    # 2) The parent, typed — the one extra gh call, made only when a parent exists.
    parent_node = issue_envelope.get("parent")
    parent = None
    if parent_node:
        parent, parent_decision = _fetch_parent_state(parent_node.get("number"), repo, cwd=cwd)
        if _forward_decision(parent_decision, notices=notices):
            return None

    # 3) Grounding — the consuming repo's declared docs, read at the ambient checkout (the same
    #    vantage as the docs themselves; see the module docstring).
    grounding_docs, catalogue_notices = doc_catalogue.read_catalogue(root)
    notices.extend(catalogue_notices)

    # 4) Blockers — live native state plus the `in-scope (blocked)` OQ entries. Best-effort on the OQ
    #    parse: a malformed `## Open questions` section is not this stage's to repair, and the native
    #    `blocked_by` set already carries the load-bearing half.
    issue_body = _extract_body(issue_envelope)
    try:
        oq_entries = parse.parse_oq_links(issue_body)
    except Exception:  # noqa: BLE001  (best-effort; see above)
        oq_entries = []
    open_blockers = _open_blockers(issue_envelope.get("blocked_by"))
    oq_blocked = _in_scope_blocked_oqs(oq_entries)

    # 5) The altitude, then the child set through the read that altitude's contract defines.
    altitude = _detect_altitude(target_type, promote=promote)
    child_attention = []
    if altitude == "epic":
        children, child_attention, children_decision = _build_story_set(
            issue_envelope.get("sub_issues"), issue_body, repo, cwd=cwd
        )
        if _forward_decision(children_decision, notices=notices):
            return None
    else:
        children = _build_slice_set(issue_envelope.get("sub_issues"), issue_envelope["number"])

    target = {
        "kind": "issue",
        "number": issue_envelope["number"],
        "title": issue_title,
        "state": issue_envelope["state"],
        "labels": issue_labels,
        "type": target_type,
        "parent": parent,
        "blocked_by": open_blockers,
        "deps_available": issue_envelope.get("deps_available"),
        "subissues_available": issue_envelope.get("subissues_available"),
    }

    refusals = _build_refusals(
        target_type,
        issue_envelope["state"],
        parent,
        open_blockers,
        oq_blocked,
        target_labels=issue_labels,
    )
    # Gated on `not refusals` so the fact set can never contradict itself: a refusal means the router
    # stops without reading the playbook, so `promotion: true` would advertise an S0 gate that never
    # runs (and an attention line describing it).
    promotion = bool(promote and target_type != "epic" and not refusals)

    # 6) Adoption candidates — only when the router named some, and only when the run can proceed: a
    #    refusal stops the router before the playbook, so a lookup per candidate would be a wasted
    #    round-trip on a guaranteed-terminal path.
    adoption_candidates = []
    if adopt and not refusals:
        adoption_candidates, adoption_notices, adoption_decision = _build_adoption_candidates(
            adopt, repo, cwd=cwd
        )
        notices.extend(n for n in adoption_notices if n not in notices)
        if _forward_decision(adoption_decision, notices=notices):
            return None
    mode = "resume" if children["count"] else "fresh"

    sections = {
        key: value
        for key, value in issue_envelope.items()
        if key.startswith(("issue_body", "thread", "marker_comment"))
    }

    facts = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root, "sha": root_sha},
        "target": target,
        "vector": {
            "type": target_type,
            "altitude": altitude,
            "mode": mode,
            "refusals": refusals,
        },
        "suggested_playbook": _suggested_playbook(refusals),
        "promotion": promotion,
        "children": children,
        "adoption_candidates": adoption_candidates,
        "grounding_docs": grounding_docs,
        "research": {"present": bool(issue_envelope.get("marker_comment_present"))},
        "open_questions": oq_blocked,
        "sections": sections,
        "attention": child_attention
        + _build_attention(
            target,
            refusals,
            children,
            grounding_docs,
            DOC_CATALOGUE_ABSENT in notices,
            open_blockers,
            altitude=altitude,
            promotion=promotion,
            promote_requested=bool(promote),
            adoption_candidates=adoption_candidates,
        ),
        "notices": notices,
    }
    return facts


def _extract_body(envelope):
    """The issue body, whether the gather kept it inline or spilled it to the scratch dir (spill
    routing, architecture.md §3). Mirrors `prep_planner._extract_body`."""
    body = envelope.get("issue_body")
    if body is None and envelope.get("issue_body_mode") == "path":
        body = Path(envelope["issue_body_path"]).read_text(encoding="utf-8")
    return body or ""


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "issue",
        nargs="?",
        default=None,
        help="issue number to decompose (into deliverable slices, or into stories when it is an "
        "epic); omitted in --adopt-check mode",
    )
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--root", default=".", help="project root (the grounding vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for spilled sections + the flow's staged slice bodies "
        "(default: /tmp/gh-slicer-<issue>)",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="explicit working directory for the underlying gh calls (cwd discipline: never rely on "
        "ambient cwd)",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="the operator asked for this issue to become an Epic before it is cut (router-passed: "
        "prep cannot read the invocation prose). Forces epic altitude",
    )
    parser.add_argument(
        "--adopt",
        action="append",
        default=[],
        metavar="N",
        help="an already-filed issue to adopt as a child (repeatable, in the order named)",
    )
    parser.add_argument(
        "--adopt-check",
        action="append",
        default=[],
        metavar="N",
        help="one-shot mode: report only these adoption candidates' live state, no facts block "
        "(repeatable; for candidates the operator names mid-session)",
    )
    args = parser.parse_args(argv)

    # One-shot adoption lookup — deliberately before the facts path so a mid-flow check costs one
    # `gh` call per candidate instead of a whole re-gather (the `prep_drafter --oq-query` precedent).
    if "/" not in args.repo:
        parser.error("repo must be <owner>/<repo>, got %r" % args.repo)

    if args.adopt_check:
        if args.issue is not None:
            parser.error(
                "--adopt-check is a one-shot mode and takes no issue argument (got %r); it reports "
                "only candidate state, so silently discarding the facts run would leave the flow "
                "with no facts block" % args.issue
            )
        candidates, notices, decision = _build_adoption_candidates(
            args.adopt_check, args.repo, cwd=args.cwd
        )
        if decision is not None:
            emit_needs_decision(decision, notices=notices)
            return 0
        emit_ok(payload={"adoption_candidates": candidates}, notices=notices)
        return 0

    if args.issue is None:
        parser.error("issue is required unless --adopt-check is given")

    facts = build_facts(
        args.issue,
        args.repo,
        root=args.root,
        scratch_dir=args.scratch_dir,
        cwd=args.cwd,
        promote=args.promote,
        adopt=args.adopt,
    )
    if facts is None:
        return 0
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
