#!/usr/bin/env python3
"""prep_designer.py — the designer's complete facts block in one call (architecture.md §4, #204).

The designer is the pipeline stage that shapes an issue's user interface with the operator on a
prototype and records the approved target as the ``<!-- ux-design:v1 -->`` design record
(``skills/_shared/design-record.md``). This prep assembles the session's whole starting state as ONE
JSON envelope on stdout:

- ``target`` — the issue (``type`` from ``branching.detect_type_with_question``).
- ``epic`` — for a target under an open epic, the epic and its story counts (the handoff's ``Epic:``
  line), else ``null``.
- ``record`` — the issue's one trusted design record as ``design_record.read_record`` reads it, plus
  ``body_path``: its verbatim body staged to ``<scratch>/record.md`` (the base a writer restages from,
  so an approving write keeps GitHub's attachment URLs), ``null`` when there is no record.
- ``vector.mode`` — ``revise`` exactly when ``record.present``, else ``fresh``; ``suggested_playbook``
  maps it onto ``fresh.md`` / ``revise.md``.
- ``block`` — the consuming repo's ``<!-- ui-prototype -->`` block (``design_record.read_block``) read
  from the asserted checkout's working tree. Its prose is the model's to read: where a prototype lives
  and how it is captured is never a script fact.
- ``ui_design`` — every ``role: ui-design`` doc-catalogue entry at the asserted checkout, in
  declaration order (``[]`` when none; ``DOC_CATALOGUE_ABSENT`` rides in ``notices`` when the repo
  declares no catalogue).
- ``workspace`` — the asserted checkout (``workspace._build_attach``'s payload) plus ``seat``.

**``workspace.seat`` is a fact; the prototype's location is a judgment.** At the repository's main
checkout the attach passes ``allow_main_root`` with the ambient branch as the expected one, and
``seat`` is ``root``: the operator's own checkout, where the designer writes nothing tracked (PRD §8).
Anywhere else the checkout must be on this issue's own branch (``<N>-…`` or ``epic/<N>-…``, the review
tool's pattern) and ``seat`` is ``own-branch``, the only seat at which a session may write tracked
files. The root test runs first, so a main checkout that happens to sit on the issue's branch is still
``root``. Remote staleness is checked (``ls-remote``, never a fetch): a checkout behind or diverged from
``origin/<branch>`` is ``WORKSPACE_MISMATCH`` (``stale_checkout``), so a session never commits or pushes
from stale code; one ahead of it passes. Setup hooks are not run — the designer builds no product code.

Composition (architecture.md §2): ``gh_gather.run`` (with ``design_record.MARKER`` as the marker
prefix), ``design_record``, ``doc_catalogue``, ``branching``, ``story_set`` and ``workspace`` cores are
composed in-process; no other script's stdout is captured. Every decision a composed core returns is
forwarded unchanged: ``AUTH_REQUIRED`` and ``TARGET_IS_PR`` from the gather, ``MARKER_AMBIGUOUS`` from
the gather or ``read_record``, ``AMBIGUOUS`` from the parent-epic search, ``WORKSPACE_MISMATCH`` from
the attach. ``MARKER_UNTRUSTED`` and ``DOC_CATALOGUE_ABSENT`` are notices. Nothing here reads a plan.

Usage::

    prep_designer.py <issue> <owner/repo> [--root PATH] [--scratch-dir PATH] [--cwd PATH]

``--root`` is the checkout the session runs in (default ``.``), the one asserted. ``--scratch-dir``
defaults to ``/tmp/gh-designer-<issue>``.

Exit codes (architecture.md §3): 0 with the envelope present (``ok`` or ``needs_decision``); 2 on a
usage error; any other non-zero is a hard ``gh``/``git`` failure, stderr carrying it.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import design_record  # noqa: E402
import doc_catalogue  # noqa: E402
import gh_gather  # noqa: E402
import story_set  # noqa: E402
import workspace  # noqa: E402
from pipelib import atomic, process  # noqa: E402
from pipelib.trust import MARKER_UNTRUSTED  # noqa: E402
from pipelib.envelope import emit_needs_decision, emit_ok  # noqa: E402

# The catalogue role the designer grounds on (skills/_shared/doc-catalogue.md: an open role set).
UI_DESIGN_ROLE = "ui-design"

SEAT_ROOT = "root"
SEAT_OWN_BRANCH = "own-branch"

RECORD_FILENAME = "record.md"


class _DiscardStream:
    """A write-sink for ``gh_gather.run(stream=...)`` (see ``prep_researcher._DiscardStream``)."""

    def write(self, _data):
        return None

    def flush(self):
        return None


def _forward_decision(decision, notices=None):
    """Emit a composed core's ``decision`` as-is and return ``True`` when there was one."""
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


def _gather_notices(envelope):
    """A gather's success-path notices, minus its ``MARKER_UNTRUSTED`` count: that one reads
    ``startswith``, while ``read_record`` — the record's one reader — counts by the exact first line,
    and two counts of one fact would disagree. The rest (``SUBISSUES_UNSUPPORTED``,
    ``DEPS_UNSUPPORTED``) are forwarded: without them a degraded relation read reports ``epic: null``
    as if the target had no epic."""
    return [n for n in envelope.get("notices") or []
            if isinstance(n, str) and not n.startswith(MARKER_UNTRUSTED + ":")]


def _dedupe(notices):
    seen = []
    for notice in notices:
        if notice not in seen:
            seen.append(notice)
    return seen


def _section_text(envelope, key):
    """A spilled section's text: the inline field, else the staged file."""
    if envelope.get("%s_mode" % key) == "path":
        return Path(envelope["%s_path" % key]).read_text(encoding="utf-8")
    return envelope.get(key)


def _gather(issue, repo, scratch, marker_prefix=None):
    """``(envelope, decision)`` for one issue; a hard failure exits 1 with the gather's stderr."""
    exit_code, envelope = gh_gather.run(
        str(issue), repo, marker_prefix=marker_prefix, scratch_dir=scratch, env=None,
        stream=_DiscardStream(),
    )
    if envelope is not None and envelope.get("status") == "needs_decision":
        return envelope, envelope["decision"]
    if exit_code != 0 or envelope is None:
        sys.stderr.write("prep_designer: gh_gather on issue #%s failed (exit %s)\n" % (issue, exit_code))
        sys.exit(1)
    return envelope, None


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


def _mode(record):
    return "revise" if record.get("present") else "fresh"


def _suggested_playbook(mode):
    """The router's table, byte-consistent: one thin playbook per mode, each reading the spine."""
    return "%s.md" % mode


# ---------------------------------------------------------------------------
# The design record
# ---------------------------------------------------------------------------


def _stage_record(thread, record, scratch):
    """Stage the trusted record's verbatim body to ``<scratch>/record.md``; ``None`` with no record.

    Located by the REST id ``read_record`` reported, so the staged body is the record it read."""
    if not record.get("present"):
        return None
    for comment in thread:
        if design_record.comment_rest_id(comment) == record["comment_id"] and design_record.is_record(comment):
            path = Path(scratch) / RECORD_FILENAME
            atomic.write_text(path, comment.get("body") or "", prefix=".record-")
            return str(path)
    return None


# ---------------------------------------------------------------------------
# The asserted checkout
# ---------------------------------------------------------------------------


def _workspace_fact(checkout, issue, title):
    """``(payload, notices, decision)``: the attach payload plus ``seat``.

    ``root`` is decided first: the checkout's top level is the main checkout. There the ambient branch
    is the expected one (``allow_main_root``), so only a detached head or a stale checkout refuses.
    Elsewhere the branch must be this issue's own (``^(?:epic/)?<N>-``); the computed ``<N>-<slug>``
    only words the mismatch card, never renames a pushed branch."""
    top_result = process.run(["git", "rev-parse", "--show-toplevel"], cwd=str(Path(checkout).resolve()))
    if top_result.returncode != 0:
        sys.stderr.write(top_result.stderr)
        sys.exit(1)
    top = str(Path(top_result.stdout.strip()).resolve())
    main_root = str(workspace._resolve_main_root(top))

    if top == main_root:
        seat = SEAT_ROOT
        payload, notices, decision = workspace._build_attach(
            top, workspace._current_branch(top), run_hooks=False, allow_main_root=True,
            check_remote_staleness=True,
        )
    else:
        seat = SEAT_OWN_BRANCH
        worded = "%s-%s" % (issue, branching.compute_fresh_slug(title or ""))
        payload, notices, decision = workspace._build_attach(
            top, worded, run_hooks=False, accept_branch_pattern=r"^(?:epic/)?%s-" % issue,
            check_remote_staleness=True,
        )
    if decision is not None:
        return None, notices, decision
    payload = dict(payload)
    payload["seat"] = seat
    return payload, notices, None


# ---------------------------------------------------------------------------
# The parent epic
# ---------------------------------------------------------------------------


def _epic_fact(repo, target, native_parent, scratch, notices, cwd=None):
    """``(epic_or_none, decision)``: the open epic this target is a story of, with its story counts.

    Looked up only for a ``story`` or a target with a native parent (the planner's gate, #31); the
    parent counts only when its own labels or title type it ``epic`` and it is open — a non-epic parent
    makes the target a slice, whose handoff is an ``Issue:`` line."""
    if target["type"] != "story" and not native_parent:
        return None, None
    matches, decision = branching.search_parent_epic(repo, target["number"], native_parent=native_parent,
                                                     cwd=cwd)
    if decision is not None:
        return None, decision
    if len(matches) != 1 or (matches[0].get("state") or "").upper() != "OPEN":
        return None, None
    parent_number = matches[0].get("number")
    envelope, decision = _gather(parent_number, repo, scratch)
    if decision is not None:
        return None, decision
    notices.extend(_gather_notices(envelope))
    labels = [label.get("name") for label in envelope.get("labels") or []]
    if branching.detect_type(labels, envelope.get("title")) != "epic":
        return None, None
    if (envelope.get("state") or "").upper() != "OPEN":
        return None, None
    read, decision = story_set.read(envelope.get("sub_issues"), _section_text(envelope, "issue_body") or "")
    if decision is not None:  # pragma: no cover — no fetch_state, so read returns no decision
        return None, decision
    stories = story_set.closed_view(read)
    return {
        "number": envelope["number"],
        "title": envelope.get("title"),
        "state": envelope.get("state"),
        "closed": sum(1 for story in stories if story["closed"]),
        "total": len(stories),
    }, None


# ---------------------------------------------------------------------------
# Grounding and attention
# ---------------------------------------------------------------------------


def _ui_design_fact(checkout):
    """``(entries, notices)``: the ``role: ui-design`` catalogue entries at the asserted checkout."""
    entries, notices = doc_catalogue.read_catalogue(checkout)
    keys = ("path", "authority", "summary", "present", "kind", "members", "abs_path")
    picked = [
        {key: entry.get(key) for key in keys}
        for entry in entries
        if (entry.get("role") or "").strip() == UI_DESIGN_ROLE
    ]
    return picked, list(notices)


def _build_attention(target, ui_design):
    attention = []
    if (target.get("state") or "").upper() == "CLOSED":
        attention.append(
            "target issue #%s is closed — the designer designs an open issue; refuse before any "
            "prototype work" % target["number"]
        )
    if target.get("type") == "question":
        attention.append(
            "target issue #%s is a question — a question is answered by a human, never designed; refuse "
            "before any prototype work" % target["number"]
        )
    for entry in ui_design:
        if not entry["present"]:
            attention.append(
                "the doc catalogue declares `%s` (role: ui-design) but it is not present at this checkout"
                % entry["path"]
            )
    return attention


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def build_facts(issue, repo, root=".", scratch_dir=None, cwd=None):
    """Assemble the designer's facts block and return it WITHOUT printing (the testable core).
    Returns ``None`` after a forwarded ``needs_decision`` envelope has been emitted."""
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-designer-%s" % issue
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    # 1) The issue, its thread and the record marker — before any checkout read, so a PR number is
    #    TARGET_IS_PR without touching the workspace.
    envelope, decision = _gather(issue, repo, scratch_dir, marker_prefix=design_record.MARKER)
    if _forward_decision(decision, notices=envelope.get("notices") if envelope else None):
        return None
    notices = _gather_notices(envelope)

    labels = [label.get("name") for label in envelope.get("labels") or []]
    target = {
        "kind": "issue",
        "number": envelope["number"],
        "title": envelope["title"],
        "state": envelope["state"],
        "labels": labels,
        "type": branching.detect_type_with_question(labels, envelope["title"]),
    }

    # 2) The record, read by #202's one reader over the trusted thread.
    thread = json.loads(_section_text(envelope, "thread") or "[]")
    record, record_notices, decision = design_record.read_record(thread, issue=envelope["number"])
    notices.extend(record_notices)
    if _forward_decision(decision, notices=_dedupe(notices)):
        return None
    record = dict(record)
    record["body_path"] = _stage_record(thread, record, scratch_dir)

    # 3) The checkout the session runs in.
    ws, ws_notices, decision = _workspace_fact(root, envelope["number"], envelope["title"])
    notices.extend(ws_notices)
    if _forward_decision(decision, notices=_dedupe(notices)):
        return None
    checkout = ws["path"]

    # 4) The parent epic, for the handoff's Epic: line.
    epic, decision = _epic_fact(repo, target, envelope.get("parent"), scratch_dir, notices, cwd=cwd)
    if _forward_decision(decision, notices=_dedupe(notices)):
        return None

    # 5) The block and the ui-design docs, from the asserted checkout's working tree.
    block = design_record.read_block(checkout)
    ui_design, catalogue_notices = _ui_design_fact(checkout)
    notices.extend(catalogue_notices)

    mode = _mode(record)
    sections = {
        key: value
        for key, value in envelope.items()
        if key.startswith(("issue_body", "thread")) and key != "thread_comment_count"
    }
    return {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": ws["main_root"], "sha": workspace._current_sha(ws["main_root"])},
        "target": target,
        "epic": epic,
        "vector": {"mode": mode},
        "suggested_playbook": _suggested_playbook(mode),
        "record": record,
        "block": block,
        "ui_design": ui_design,
        "workspace": ws,
        "sections": sections,
        "attention": _build_attention(target, ui_design),
        "notices": _dedupe(notices),
    }


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue", help="the issue number to design")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--root", default=".", help="the checkout the session runs in (asserted)")
    parser.add_argument(
        "--scratch-dir", default=None, help="scratch dir (default: /tmp/gh-designer-<issue>)"
    )
    parser.add_argument(
        "--cwd", default=None,
        help="the working directory for the legacy parent-epic search (every gh call is scoped with --repo)",
    )
    args = parser.parse_args(argv)

    facts = build_facts(args.issue, args.repo, root=args.root, scratch_dir=args.scratch_dir, cwd=args.cwd)
    if facts is None:
        return 0
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
