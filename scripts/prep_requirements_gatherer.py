#!/usr/bin/env python3
"""prep_requirements_gatherer.py — the requirements-gatherer's complete facts block in one call
(architecture.md §4). Assembles the session's entire starting state — the target issue + thread,
its type, the **refusal** set (the conditions under which gathering requirements onto this issue
would be wrong), the parsed `## Definition of done` (so the flow appends without re-deriving
indexes), and the consuming repo's declared grounding docs — as ONE JSON envelope on stdout, so
the session's startup is one Python process, never a subprocess chain.

The requirements-gatherer interactively elicits requirements from the operator for one target
issue and appends them to that issue's `## Definition of done` as plain unticked criterion
bullets, each opening with a stable `**REQ-<issue>-<seq>**` id and closing with a provenance
tail — a source-doc citation, or `operator elicited <date>`
(`skills/requirements-gatherer/references/requirements-format.md`). Its only write surface is the
issue body via `gh_persist.py edit-body`; it edits no tracked files and files no issues.

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external
processes any script may spawn are git/gh")::

    gh_gather.run(..., stream=)      -- the target's body/thread + native blocked_by/blocking + the
                                        native parent/sub_issues relation + the implementation-plan
                                        marker, in ONE round-trip
    branching.detect_type_with_question -- the shared epic/story/question/standard core (import-only)
    parse.parse_dod_bullets          -- the existing DoD, parsed with 1-based indexes so the flow
                                        appends after the last top-level bullet, never re-counting
    parse.dod_summary                -- whether the section exists at all (create-vs-append fact),
                                        and any checklist under another heading
    parse.dod_malformed_decision     -- the shared DOD_MALFORMED decision builder
    pipelib.spill.read_section       -- the inline|path read-back for the spilled body
    doc_catalogue.read_catalogue     -- the consuming repo's `<!-- doc-catalogue -->` grounding docs

Plus ONE direct `gh issue view <parent> --json state,title,labels` via
`branching.fetch_parent_state` — and only when the target actually has a parent — because the
sub-issue/parent node shape carries **no labels** (`epic-story-hierarchy.md` "The facts"), so the
parent cannot be typed from the gather alone.

Usage::

    prep_requirements_gatherer.py <issue> <owner/repo> [--root PATH] [--scratch-dir PATH] [--cwd PATH]

**No workspace — the gatherer grounds on the CURRENT checkout**, the same root-only vantage
`prep_drafter.py` and `prep_slicer.py` take, and for the same threat-model reason: it reads no PR
head and gates no merge. It runs before any workspace exists (its output is input to planning,
which precedes `workspace-open`). `root` therefore carries only `{path, sha}` — informational,
never enforced — and the doc catalogue is read at that same working vantage, which since v3.x is
simply what every config family does (`skills/_shared/doc-catalogue.md` "Where the catalogue is
read from"; the `origin/main` pin this once contrasted with is retired).

**Refusals are facts, not decision codes** (the `prep_slicer` ruling, applied verbatim):
`vector.refusals` is a list of reason tokens from a closed set; the router renders the matching
refusal summary and stops. They are deliberately NOT `needs_decision` codes — a refusal is a
routing outcome with its own breadcrumb, not an ambiguity one card resolves.

  - ``epic-target``     — an epic's DoD is outcome-level ("all stories are closed"); nothing ever
                          projects or verifies criterion bullets on it — requirements belong on
                          its stories. The breadcrumb points at the open stories, or the drafter
                          when none exist yet.
  - ``slice-target``    — a slice carries `## Acceptance criteria` owned by the slicer, not a DoD
                          (`epic-story-hierarchy.md`: a DoD on a slice would be a checkbox set
                          nothing ticks). The breadcrumb points at the parent story.
  - ``question-target`` — a `question` issue carries no DoD by contract
                          (`skills/_shared/question-issue.md`); a human answers it in its thread.
  - ``closed-target``   — criteria appended to delivered work can never be projected or verified.

An OPEN native blocker is deliberately NOT a refusal — elicitation is upstream human input, and
an unanswered dependency does not invalidate what the operator states this issue must do. Open
blockers ride in `attention` instead, so the operator sees them.

`DOC_CATALOGUE_ABSENT` stays a **notice**, never a refusal: this skill's output is
operator-elicited, so it is the proceeding kind of catalogue consumer
(`skills/_shared/doc-catalogue.md` "When the catalogue is absent") — the grounding-selection gate
simply has nothing to suggest and collects operator-named sources instead.

A malformed DoD annotation IS a blocking `DOD_MALFORMED` decision (forwarded from the parse
core): appending to a section the parser cannot index risks colliding with whatever the malformed
line was trying to record — the operator repairs the bullet or aborts before this skill writes.
**Refusals win over the malformed-DoD decision**: on a refused target the DoD is not parsed at
all (`dod: null`) — the write-collision rationale only applies when a write can happen, and a
repair card demanding a hand-fix on an issue the session refuses anyway is noise.

The gather's marker lookup targets the `<!-- implementation-plan:v1 -->` comment, and
`plan.present` is a first-class fact: an issue with a verified plan is **mid-flight** even before
any bullet carries an annotation — a plan with no `## Phases` section makes the resolver's
single-phase fallback tick EVERY top-level DoD bullet on its next push, so appended REQ bullets
would be silently claimed as delivered. The flow's mid-flight gate keys on `plan.present` OR
`dod.annotated_count`.

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"``
or ``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified
hard `gh`/`git` failure surfaced by the composed gather — stderr carries the faithful error.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402  (import after sys.path setup, by necessity; shared type-detection + parent-typing cores)
import doc_catalogue  # noqa: E402  (the consuming repo's declared grounding docs)
import gh_gather  # noqa: E402
import parse  # noqa: E402  (the DoD parse: indexes the flow appends after, never re-counting)
from pipelib import process, spill  # noqa: E402
from pipelib.decisions import DOC_CATALOGUE_ABSENT  # noqa: E402
from pipelib.envelope import emit_needs_decision, emit_ok  # noqa: E402

# The planner's durable plan comment (`skills/_shared/handoff-format.md` names the marker; the
# planner writes it). Its PRESENCE — not its content — is this prep's fact: a planned issue is
# mid-flight even with zero annotations (see the module docstring's single-phase-fallback note).
PLAN_MARKER = "<!-- implementation-plan:v1 -->"

# The stable requirement id this skill writes as a bold bullet prefix: `**REQ-<issue>-<seq>**`
# (the slicer's `**AC-<n>**` form, issue-qualified like its `<parent#>/S<K>` titles so a citation
# read out of context — a plan phase, a slice's grounding, a sibling thread — is unambiguous).
# The id is ISSUE-MINTED: identity must outlive provenance, so a source doc's own register id
# never becomes part of it (doc renumbering must not break existing citations); the doc id rides
# in the same bullet's provenance tail instead. Parsed here so a re-run continues the sequence —
# append-only, never renumbered, never reused.
_REQ_ID_RE = re.compile(r"^\*\*REQ-(\d+)-(\d+)\*\*")

# The closed refusal set (see the module docstring for each token's rationale).
REFUSAL_EPIC_TARGET = "epic-target"
REFUSAL_SLICE_TARGET = "slice-target"
REFUSAL_QUESTION_TARGET = "question-target"
REFUSAL_CLOSED_TARGET = "closed-target"


class _DiscardStream:
    """A minimal write-sink for `gh_gather.run(stream=...)` — see `prep_researcher._DiscardStream`
    for the full rationale (restated locally per that module's own note: sharing a two-method sink
    across prep modules is not the in-process composition architecture.md §2 asks for)."""

    def write(self, _data):
        return None

    def flush(self):
        return None


def _forward_decision(decision, notices=None):
    """Emit a composed core's returned `decision` AS-IS on prep's own stdout and return `True`
    when a decision was present. Mirrors `prep_researcher._forward_decision` exactly."""
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
# DoD facts
# ---------------------------------------------------------------------------


def _req_id_facts(text, issue_number):
    """Parse a bullet's `**REQ-<issue>-<seq>**` prefix. Returns `(req_id_or_none, seq_or_none)`;
    `seq` is reported only when the embedded issue number matches THIS issue (the slicer's
    designator guard, same reason: a foreign id copy-pasted into a bullet must not hijack the
    sequence), while `req_id` reports whatever literal id the bullet carries."""
    match = _REQ_ID_RE.match(text or "")
    if match is None:
        return None, None
    req_id = "REQ-%s-%s" % (match.group(1), match.group(2))
    seq = int(match.group(2)) if int(match.group(1)) == int(issue_number) else None
    return req_id, seq


def _parse_dod(issue_body, issue_number):
    """Compose `parse.parse_dod_bullets` + `parse.dod_summary` (pure cores — architecture.md
    §2's in-process composition). `parse._DodMalformed` is caught here and turned into the same
    `DOD_MALFORMED` decision the CLI path emits, exactly as `prep_evaluator._parse_closing_issue_dod`
    does. Returns `(dod_facts, decision_or_none)`.

    `next_req_seq` is one past the highest OWN-issue `**REQ-<issue>-<seq>**` sequence found —
    append-only continuation, so ids are never renumbered and (unless the operator hand-deleted
    the highest-numbered bullet) never reused. Id-less bullets are legitimate (drafter-written
    criteria predate this skill) and contribute nothing to the sequence."""
    try:
        bullets = parse.parse_dod_bullets(issue_body)
    except parse._DodMalformed as exc:  # noqa: SLF001  (the sanctioned cross-module catch; prep_resolver precedent)
        return None, parse.dod_malformed_decision(exc, issue=issue_number)
    entries = []
    highest_seq = 0
    for b in bullets:
        req_id, seq = _req_id_facts(b["text"], issue_number)
        if seq is not None:
            highest_seq = max(highest_seq, seq)
        entries.append(
            {
                "index": b["index"],
                "ticked": b["checked"],
                "text": b["text"],
                "annotation": b["annotation"],
                "req_id": req_id,
            }
        )
    shape = parse.dod_summary(issue_body)
    return {
        "present": shape["present"],
        # A checklist under another heading: when `present` is false this is where the issue's
        # criteria actually live, and appending a fresh `## Definition of done` beside it would leave
        # them out of every DoD reader (gather.md stops instead).
        "other_checklists": shape["other_checklists"],
        "bullet_count": len(bullets),
        "annotated_count": sum(1 for b in bullets if b.get("annotation") is not None),
        "next_req_seq": highest_seq + 1,
        "bullets": entries,
    }, None


# ---------------------------------------------------------------------------
# Refusals + attention
# ---------------------------------------------------------------------------


def _build_refusals(target_type, target_state, parent):
    """The closed refusal set (module docstring). Order is stable so the router's rendering choice
    is deterministic when more than one applies — the router renders the FIRST."""
    refusals = []
    if target_type == "epic":
        refusals.append(REFUSAL_EPIC_TARGET)
    if target_type == "question":
        refusals.append(REFUSAL_QUESTION_TARGET)
    # A parent that is not an epic means the target is itself a deliverable slice (epic -> story
    # -> slice, and the relation has exactly three levels); a slice carries the slicer's
    # `## Acceptance criteria`, never a DoD.
    if parent is not None and parent.get("type") != "epic":
        refusals.append(REFUSAL_SLICE_TARGET)
    if (target_state or "").upper() == "CLOSED":
        refusals.append(REFUSAL_CLOSED_TARGET)
    return refusals


def _open_blockers(blocked_by):
    """The OPEN entries of the native `blocked_by` set — attention material, never a refusal (the
    module docstring says why). A closed blocker is stale, not a fact worth surfacing."""
    return [
        {"number": node.get("number"), "title": node.get("title"), "url": node.get("url")}
        for node in blocked_by or []
        if (node.get("state") or "").upper() == "OPEN"
    ]


def _build_attention(target, refusals, dod, plan, grounding_docs, catalogue_absent, open_blockers):
    """Script-detectable conditions worth surfacing with evidence (architecture.md §4)."""
    attention = []
    for token in refusals:
        if token == REFUSAL_EPIC_TARGET:
            attention.append(
                "target #%s is an epic — its DoD is outcome-level; requirements belong on its "
                "stories" % target["number"]
            )
        elif token == REFUSAL_SLICE_TARGET:
            attention.append(
                "target #%s is a deliverable slice (its parent is not an epic) — a slice carries "
                "the slicer's acceptance criteria, never a DoD; gather on the parent story instead"
                % target["number"]
            )
        elif token == REFUSAL_QUESTION_TARGET:
            attention.append(
                "target #%s is a question issue — it carries no DoD by contract; a human answers "
                "it in its thread" % target["number"]
            )
        elif token == REFUSAL_CLOSED_TARGET:
            attention.append(
                "target #%s is closed — criteria on delivered work can never be projected or "
                "verified" % target["number"]
            )
    # Not on a refused target: a slice's `## Acceptance criteria` is the slicer's by design, and the
    # refusal line already says where to gather instead.
    if dod is not None and not refusals:
        note = parse.misplaced_dod_note(dod)
        if note is not None:
            attention.append(
                note + " — appending a second checklist would leave those criteria out of every "
                "DoD reader; stop and route to /ctrl-freaq:drafter revise #%s, which renames "
                "the heading, then re-run" % target["number"]
            )
    if dod is not None and dod["annotated_count"]:
        attention.append(
            "%d DoD bullet(s) on #%s carry resolver/evaluator annotations — appending changes the "
            "bullet count, so the resolver's next projection will block and re-route to the "
            "planner (dod-annotations.md index stability); warn the operator and require explicit "
            "confirmation" % (dod["annotated_count"], target["number"])
        )
    if plan["present"] and not refusals:
        attention.append(
            "#%s carries an implementation plan — the issue is mid-flight even with zero "
            "annotations: a plan with no ## Phases section makes the resolver's single-phase "
            "fallback tick EVERY top-level DoD bullet on its next push, silently claiming "
            "appended bullets as delivered; warn the operator and require explicit confirmation"
            % target["number"]
        )
    if catalogue_absent:
        attention.append(
            "no doc catalogue — the grounding-selection gate has nothing to suggest; collect "
            "operator-named sources and carry a /ctrl-freaq:setup breadcrumb in the summary"
        )
    elif not grounding_docs:
        attention.append(
            "doc catalogue declares no documents — the grounding-selection gate has nothing to "
            "suggest"
        )
    for missing in doc_catalogue.missing_entry_paths(grounding_docs):
        attention.append(
            "doc catalogue names '%s', absent in this checkout — a stale entry, or a doc this "
            "branch has not merged yet" % missing
        )
    for blocker in open_blockers:
        attention.append(
            "target #%s has an open blocker #%s (%s) — not a refusal (elicitation is upstream "
            "human input), but the operator should know"
            % (target["number"], blocker["number"], blocker.get("title") or "untitled")
        )
    return attention


def _suggested_playbook(refusals):
    """One playbook (`gather.md`) — with-DoD and without-DoD differ only in VALUES (`dod.present`),
    never in actions taken (CLAUDE.md's "parameterize before you playbook"). A refusal routes to
    no playbook at all — the router renders the matching refusal summary and stops."""
    return None if refusals else "gather.md"


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def build_facts(issue, repo, root=".", scratch_dir=None, cwd=None):
    """Assemble the requirements-gatherer's complete facts block and return the envelope dict
    WITHOUT printing it (the testable core, mirroring `prep_slicer.build_facts`). Returns `None`
    after a `needs_decision` envelope has already been emitted on stdout."""
    root = str(Path(root).resolve())
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-requirements-gatherer-%s" % issue
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    # Every composed core's non-blocking degradations accumulate here and ride out in the
    # envelope's own `notices` (architecture.md §3) — and each `needs_decision` forward below
    # carries whatever notices have accumulated by that point, so no already-observed degradation
    # is silently dropped by a decision.
    notices = []

    root_sha = _root_sha(root)

    # 1) The target — one round-trip: body, thread, native deps, the parent/sub-issue relation,
    #    and the implementation-plan marker (presence only — a planned issue is mid-flight even
    #    with zero annotations; see the module docstring's single-phase-fallback note).
    exit_code, issue_envelope = gh_gather.run(
        str(issue),
        repo,
        marker_prefix=PLAN_MARKER,
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
        sys.stderr.write(
            "prep_requirements_gatherer: gh_gather on issue #%s failed (exit %d)\n"
            % (issue, exit_code)
        )
        sys.exit(1)
    notices.extend(issue_envelope.get("notices") or [])

    issue_labels = [label.get("name") for label in issue_envelope.get("labels") or []]
    issue_title = issue_envelope["title"]
    target_type = branching.detect_type_with_question(issue_labels, issue_title)

    # 2) The parent, typed — the one extra gh call, made only when a parent exists.
    parent_node = issue_envelope.get("parent")
    parent = None
    if parent_node:
        parent, parent_decision = branching.fetch_parent_state(
            parent_node.get("number"), repo, cwd=cwd
        )
        if _forward_decision(parent_decision, notices=notices):
            return None

    # 3) Refusals — computed BEFORE the DoD parse, because a refusal wins: a `DOD_MALFORMED`
    #    repair card on an issue the session refuses anyway is noise (the write-collision
    #    rationale only applies when a write can happen).
    refusals = _build_refusals(target_type, issue_envelope["state"], parent)

    # 4) The existing DoD, parsed once — the flow appends after `bullet_count`, never re-counting,
    #    and the annotated-count fact drives the mid-flight warning gate. Skipped (dod: null) on
    #    any refusal path: the router stops before reading it.
    dod = None
    if not refusals:
        issue_body = spill.read_section(issue_envelope, "issue_body")
        dod, dod_decision = _parse_dod(issue_body, issue_envelope["number"])
        if _forward_decision(dod_decision, notices=notices):
            return None

    # 5) Grounding — the consuming repo's declared docs, read at the ambient checkout (the same
    #    vantage as the docs themselves; see the module docstring).
    grounding_docs, catalogue_notices = doc_catalogue.read_catalogue(root)
    notices.extend(catalogue_notices)

    plan = {"present": bool(issue_envelope.get("marker_comment_present"))}
    open_blockers = _open_blockers(issue_envelope.get("blocked_by"))

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

    sections = {
        key: value
        for key, value in issue_envelope.items()
        if key.startswith(("issue_body", "thread"))
    }

    facts = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root, "sha": root_sha},
        "target": target,
        "vector": {"type": target_type, "refusals": refusals},
        "suggested_playbook": _suggested_playbook(refusals),
        "dod": dod,
        "plan": plan,
        "grounding_docs": grounding_docs,
        "sections": sections,
        "attention": _build_attention(
            target,
            refusals,
            dod,
            plan,
            grounding_docs,
            DOC_CATALOGUE_ABSENT in notices,
            open_blockers,
        ),
        "notices": notices,
    }
    return facts


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue", help="issue number to gather requirements onto")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--root", default=".", help="project root (the grounding vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for spilled sections + the flow's staged DoD body "
        "(default: /tmp/gh-requirements-gatherer-<issue>)",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="explicit working directory for the underlying gh calls (cwd discipline: never rely "
        "on ambient cwd)",
    )
    args = parser.parse_args(argv)

    facts = build_facts(
        args.issue,
        args.repo,
        root=args.root,
        scratch_dir=args.scratch_dir,
        cwd=args.cwd,
    )
    if facts is None:
        return 0
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
