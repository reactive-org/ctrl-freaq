#!/usr/bin/env python3
"""design_record.py — the design record's and the ``ui-prototype`` block's read model
(``skills/_shared/design-record.md``, #202).

Two artifacts, one module, because the block exists only to say how the record's prototype and captures
are produced:

- **The design record** — the ``<!-- ux-design:v1 -->`` issue comment holding the user-interface design
  the operator agreed for an issue: where the prototype lives, the agreed states with one capture
  reference each, and the operator's approval. :func:`parse_record` is its one grammar (the reader's and
  a writer's pre-post check alike); :func:`read_record` finds the issue's one trusted record in a thread
  the caller already fetched.
- **The ``ui-prototype`` block** — user-owned prose in the consuming repo's ``COMMANDS.md`` /
  ``CLAUDE.md`` saying where a prototype lives, what it is built with, how to run it and how to capture
  each state. :func:`read_block` reads it from the working tree of the checkout the command runs in, as
  every config block is (architecture.md §6 rule 1), and passes its text through unchanged;
  :func:`locate` is its raw discovery, which ``prep_setup.py`` also composes for setup's re-ingest base
  (#203).

**Why ``approved`` fails closed.** A later stage compares a build against the record's captures, so a
record that reads as approved while one of its states has no capture would let a state merge unchecked.
Any finding — a state line the grammar cannot read, a duplicated id, a missing or doubled label, an
approval value it does not know — makes ``approved`` ``False``, because an unreadable state line could
hide a state with no capture (the posture ``parse.scan_security`` takes with an unparsed line).

**Why only a trusted author's record counts.** Anyone can comment on a public issue, so a forged record
would let its author set the design a build is checked against. Marked comments are partitioned by
``pipelib.trust`` before anything else, so an untrusted copy can neither be the record nor make it
ambiguous (#179); the skip is one ``MARKER_UNTRUSTED`` notice naming the marker, the count and the issue,
never the comment's text, URL or author.

Stdlib-only pure functions plus a thin CLI (architecture.md §2's pure-core pattern): no function but
:func:`main` prints, exits or raises on malformed input, and the only I/O outside it is named — :func:`read_and_stage`
writes the staged record body, and :func:`stage_captures` (#206) reads one comment with ``gh`` and fetches its
captures, through injectable defaults the offline suite replaces. ``check <body_path>``
runs :func:`parse_record` over a staged record for a writer's pre-post check (#204) — always ``ok``, its
payload :func:`parse_record`'s dict; an unreadable path is exit 2. ``prep_setup.py`` composes :func:`locate`
and :func:`read_block` for setup's re-ingest base (#203); ``prep_designer.py`` composes :func:`read_record`
and :func:`read_block` (#204), and ``prep_planner.py`` composes :func:`read_record`, :func:`read_block` and
:func:`cited_target` into ``facts.design`` (#205), as the preps compose ``delivery_log`` and
``review_guidance``.

**A plan's citation (#205).** A plan built to an approved record opens its ``## UI decisions`` with
``- Design target: <record url> · fingerprint <12 hex> · approved <YYYY-MM-DD>``. :func:`fingerprint` is the
record's content hash — the one way to tell a revised record from the one a plan cited, since an in-place edit
keeps the URL and a same-day revision keeps the approval date — and :func:`cited_target` reads the line back,
for the planner's revise (``facts.design.citation``) and #206's build check.

**The build check (#206).** :func:`design_fact` is ``facts.design`` for the planner, resolver, evaluator and review
preps alike — the record (:func:`read_and_stage`), the block, the plan's citation (:func:`citation`) and whether the
check runs (``check``: ``none`` / ``run`` / ``hold``) at which phase (:func:`cited_check`, the ``- Build check: phase
<N>`` line). ``stage`` stages the record's captures for the comparison (``skills/_shared/design-check.md``). A
comment supplies the capture URLs, so the fetch is bounded: ``https`` to :data:`ALLOWED_CAPTURE_HOSTS` only, on every
redirect hop and at the final URL, at most :data:`CAPTURE_MAX_BYTES`, with no credential sent; a path reference is
copied only when its real path stays inside the checkout.
"""

import argparse
import html.parser
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import parse  # noqa: E402  (the one section finder; the plan's `## Phases`)
from delivery_log import comment_rest_id  # noqa: E402  (the one REST-id rule, #34)
from pipelib import atomic  # noqa: E402
from pipelib.decisions import MARKER_AMBIGUOUS, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402
from pipelib.hashing import sha256_hex  # noqa: E402
from pipelib.trust import partition_trusted, untrusted_notice  # noqa: E402

# The record's marker: the body's first line, and nowhere else. Contract token.
MARKER = "<!-- ux-design:v1 -->"

# The consuming repo's config block (`<!-- ui-prototype -->` ... `<!-- /ui-prototype -->`).
BLOCK_MARKER = "ui-prototype"

# The record's fixed line labels — contract tokens a writer emits and this module reads.
PROTOTYPE_LABEL = "Prototype:"
STATES_HEADING = "## States"
CAPTURE_SEP = " — capture: "
APPROVAL_LABEL = "Approval:"
NO_CAPTURE = "(none)"

APPROVAL_APPROVED = "approved"
APPROVAL_PENDING = "pending"

# A state line: "- `<state-id>` — <description> — capture: <reference | (none)>". The id ends at the
# first " — " after its code span; the capture begins at the LAST " — capture: ", so a description may
# itself carry an em dash or the word "capture".
_STATE_ID = r"[a-z0-9][a-z0-9-]*"
_STATE_HEAD_RE = re.compile(r"^- `(" + _STATE_ID + r")` — (.*)$")
# Any Markdown list item: GitHub renders `-`, `*`, `+` and `1.` / `1)` items alike, so an operator's web
# edit can add a state with any of them.
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s")
# A thematic break (`* * *`, `- - -`) starts like a list item but is not one.
_THEMATIC_BREAK_RE = re.compile(r"^([-*_])(?:\s*\1){2,}$")
_APPROVED_RE = re.compile(r"^approved (\d{4}-\d{2}-\d{2})$")

# The record's content fingerprint (#205): this many leading hex digits of a sha256.
FINGERPRINT_LENGTH = 12

# A plan's citation of the record (#205): the first `- Design target:` bullet in its `## UI decisions`
# section (the plan opens the section with it),
# `- Design target: <record url> · fingerprint <12 hex> · approved <YYYY-MM-DD>`. Contract token.
UI_DECISIONS_HEADING_PATTERN = r"UI decisions"
DESIGN_TARGET_PREFIX = "- Design target:"
_DESIGN_TARGET_RE = re.compile(
    r"^- Design target: (\S+) · fingerprint ([0-9a-f]{%d}) · approved (\d{4}-\d{2}-\d{2})$" % FINGERPRINT_LENGTH
)

# The build-check phase a plan names (#206): the first `- Build check: phase <N>` bullet in its `## UI decisions`
# section, written right after the `Design target:` line. Contract token.
BUILD_CHECK_PREFIX = "- Build check:"
_BUILD_CHECK_RE = re.compile(r"^- Build check: phase ([1-9][0-9]*)$")

# `facts.design.citation` (#205): how a plan's `- Design target:` citation stands against the issue's current
# record — a closed set the prep derives, so a playbook consumes it as data. Moved here from `prep_planner.py`
# (#206) so every prep that reads a design derives it from one copy.
CITATION_NONE = "none"
CITATION_MISSING = "missing"
CITATION_UNAPPROVED = "unapproved"
CITATION_CURRENT = "current"
CITATION_REVISED = "revised"
CITATION_STATES = (CITATION_NONE, CITATION_MISSING, CITATION_UNAPPROVED, CITATION_CURRENT, CITATION_REVISED)

# `facts.design.check` (#206): whether the build check can run. `none` — the plan cites no target, so there is
# nothing to compare; `run` — it cites the record that stands (`citation: current`); `hold` — it cites one that
# is missing, unapproved or revised since, or its `## Phases` cannot be read. A closed set.
CHECK_NONE = "none"
CHECK_RUN = "run"
CHECK_HOLD = "hold"
CHECK_STATES = (CHECK_NONE, CHECK_RUN, CHECK_HOLD)

# The trusted record's verbatim body, staged for a reader (the plan reviewer's `<<design_record>>`, #205; the
# capture staging's `--record`, #206).
DESIGN_RECORD_FILENAME = "design-record.md"

# Capture staging (#206, `stage`). A comment supplies the capture URLs, so the fetch is bounded: `https` only, to
# these hosts only, on every redirect hop and at the final URL, and at most `CAPTURE_MAX_BYTES` per capture. GitHub
# serves a private repo's attachment through a short-lived signed `private-user-images` URL that only the
# comment's `body_html` carries; `user-images` is the older public host, and `github.com` the
# `user-attachments/assets` form a record holds.
ALLOWED_CAPTURE_HOSTS = (
    "private-user-images.githubusercontent.com",
    "user-images.githubusercontent.com",
    "github.com",
)
CAPTURE_MAX_BYTES = 20 * 1024 * 1024
# The one notice a failed capture is (a state whose capture could not be staged): never a hard exit, and never a
# pass — a state with no staged capture is compared as unreachable, or held.
DESIGN_CAPTURE_UNAVAILABLE = "DESIGN_CAPTURE_UNAVAILABLE"
_MAX_REDIRECTS = 5
_CONTENT_TYPE_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "application/pdf": ".pdf",
}
_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
_MARKDOWN_IMAGE_RE = re.compile(r"^!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)$")

REASON_STATE_MALFORMED = "state line does not match the grammar"
REASON_STATE_DUPLICATE = "duplicate state id"
REASON_STATE_OUTSIDE = "list item outside the ## States section"
REASON_PROTOTYPE_MISSING = "no Prototype: line, or one with no value"
REASON_PROTOTYPE_DUPLICATE = "more than one Prototype: line"
REASON_APPROVAL_MISSING = "no Approval: line"
REASON_APPROVAL_DUPLICATE = "more than one Approval: line"
REASON_APPROVAL_UNRECOGNISED = "Approval: value is neither `approved <YYYY-MM-DD>` nor `pending`"


def _lines(body):
    """The body's lines, CRLF-normalised (a body edited on GitHub's web UI is stored with CRLF) and
    each stripped of surrounding whitespace, so a trailing space or ``\\r`` never hides a label."""
    if not isinstance(body, str):
        return []
    return [line.strip() for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _empty_parse():
    return {
        "prototype": None,
        "states": [],
        "approval": None,
        "approved_on": None,
        "approved": False,
        "findings": [],
    }


def _parse_state(text):
    """``{id, description, capture}`` for one state line, or ``None`` when the grammar does not hold.
    ``capture`` is ``None`` when the reference is empty or :data:`NO_CAPTURE`."""
    head = _STATE_HEAD_RE.match(text)
    if not head:
        return None
    state_id, rest = head.group(1), head.group(2)
    # The head consumed the " — " after the id, so put it back before looking for the separator: an
    # empty description then still yields a separator match (and is refused below). The separator is
    # matched exactly, trailing space included; the one exception is a line ENDING in the bare
    # " — capture:", because stripping the line removed the space an empty reference leaves.
    lead = " — "
    bare = CAPTURE_SEP.rstrip()
    probe = lead + rest
    cut = probe.rfind(CAPTURE_SEP)
    width = len(CAPTURE_SEP)
    if probe.endswith(bare) and probe.rfind(bare) >= cut:
        cut, width = len(probe) - len(bare), len(bare)
    if cut < 0:
        return None
    description = probe[len(lead):cut].strip()
    capture = probe[cut + width:].strip()
    if not description:
        return None
    if not capture or capture == NO_CAPTURE:
        capture = None
    return {"id": state_id, "description": description, "capture": capture}


def parse_record(body):
    """The design record's fields, read from ``body`` (the comment text, marker line included or not).

    Returns ``{prototype, states, approval, approved_on, approved, findings}``: ``states`` is
    ``[{id, description, capture}]`` in body order, ``approval`` is ``"approved"`` / ``"pending"`` /
    ``None``, ``approved_on`` the ISO date string or ``None``, ``findings`` ``[{line_number, raw_line,
    reason}]`` (1-based line numbers of ``body``; ``line_number`` and ``raw_line`` are ``None`` for a
    label with no line at all). ``approved`` is ``True`` only when the approval reads
    ``approved <YYYY-MM-DD>`` with a valid date, at least one state parsed, every state has a capture,
    and there are no findings. Never raises.
    """
    result = _empty_parse()
    if not isinstance(body, str):
        return result
    lines = _lines(body)
    findings = result["findings"]

    def finding(index, reason):
        findings.append({"line_number": index + 1, "raw_line": lines[index], "reason": reason})

    prototype_at = [i for i, line in enumerate(lines) if line.startswith(PROTOTYPE_LABEL)]
    approval_at = [i for i, line in enumerate(lines) if line.startswith(APPROVAL_LABEL)]
    states_at = [i for i, line in enumerate(lines) if line == STATES_HEADING]

    if not prototype_at:
        findings.append({"line_number": None, "raw_line": None, "reason": REASON_PROTOTYPE_MISSING})
    else:
        result["prototype"] = lines[prototype_at[0]][len(PROTOTYPE_LABEL):].strip() or None
        if result["prototype"] is None:
            finding(prototype_at[0], REASON_PROTOTYPE_MISSING)
        for index in prototype_at[1:]:
            finding(index, REASON_PROTOTYPE_DUPLICATE)

    if not approval_at:
        findings.append({"line_number": None, "raw_line": None, "reason": REASON_APPROVAL_MISSING})
    else:
        value = lines[approval_at[0]][len(APPROVAL_LABEL):].strip()
        approved = _APPROVED_RE.match(value)
        valid_date = None
        if approved:
            try:
                valid_date = date.fromisoformat(approved.group(1)).isoformat()
            except ValueError:
                valid_date = None
        if approved and valid_date:
            result["approval"] = APPROVAL_APPROVED
            result["approved_on"] = valid_date
        elif value == APPROVAL_PENDING:
            result["approval"] = APPROVAL_PENDING
        else:
            finding(approval_at[0], REASON_APPROVAL_UNRECOGNISED)
        for index in approval_at[1:]:
            finding(index, REASON_APPROVAL_DUPLICATE)

    start = end = None
    if states_at:
        start = states_at[0] + 1
        later_approval = [i for i in approval_at if i > states_at[0]]
        end = later_approval[0] if later_approval else len(lines)
    # A list item outside the region is never a state, but it is a finding: the grammar has no other
    # list items, and one appended below the Approval: line — well-formed or not — would otherwise hide an
    # agreed state with no capture behind an approved record.
    for index, text in enumerate(lines):
        if _LIST_ITEM_RE.match(text) and not _THEMATIC_BREAK_RE.match(text) and (start is None or not start <= index < end):
            finding(index, REASON_STATE_OUTSIDE)
    if states_at:
        seen = set()
        for index in range(start, end):
            text = lines[index]
            if not text:
                continue
            state = _parse_state(text)
            if state is None:
                finding(index, REASON_STATE_MALFORMED)
                continue
            if state["id"] in seen:
                finding(index, REASON_STATE_DUPLICATE)
                continue
            seen.add(state["id"])
            result["states"].append(state)

    result["approved"] = bool(
        result["approval"] == APPROVAL_APPROVED
        and result["states"]
        and all(state["capture"] is not None for state in result["states"])
        and not findings
    )
    return result


def fingerprint(body):
    """The record's content fingerprint: the first :data:`FINGERPRINT_LENGTH` hex digits of the sha256
    of ``body`` after this module's own normalisation (line endings normalised, each line stripped,
    joined with ``\\n``, leading and trailing blank lines dropped). A revision changes it whether it was
    edited in place or reposted; a repost of identical text keeps it. Neither the comment URL (an in-place
    edit keeps it) nor ``approved_on`` (a same-day revision keeps it) can tell a revised record from the one a plan cited (#205)."""
    normalised = "\n".join(_lines(body)).strip("\n")
    return sha256_hex(normalised.encode("utf-8"))[:FINGERPRINT_LENGTH]


def cited_target(plan_text):
    """The design record a plan cites, as ``{"url", "fingerprint"}``, or ``None``.

    Reads the first ``- Design target:`` bullet under the plan's ``## UI decisions`` section
    (``- Design target: <record url> · fingerprint <12 hex> · approved <YYYY-MM-DD>``). Fence-blind, as
    ``parse._find_section`` is. ``None`` with no section, no such bullet, or a first bullet whose URL,
    fingerprint or approval date does not read (an impossible date included); a ``Design target:`` line under any other heading is never the citation.
    The one reader of the line: the planner's prep compares its fingerprint with the current record's
    (``facts.design.citation``), and #206 composes it to check that a revise adopted a revised record."""
    if not isinstance(plan_text, str):
        return None
    lines = plan_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    span = parse._find_section(lines, UI_DECISIONS_HEADING_PATTERN)  # noqa: SLF001 (the one section finder)
    if span is None:
        return None
    for line in lines[span[0]:span[1]]:
        text = line.strip()
        if not text.startswith(DESIGN_TARGET_PREFIX):
            continue
        match = _DESIGN_TARGET_RE.match(text)
        if not match:
            return None
        try:
            date.fromisoformat(match.group(3))
        except ValueError:
            # An impossible date is malformed here as it is on the record's own Approval: line.
            return None
        return {"url": match.group(1), "fingerprint": match.group(2)}
    return None


def cited_check(plan_text):
    """The build-check phase a plan names, as an int, or ``None`` (#206).

    Reads the first ``- Build check:`` bullet under the plan's ``## UI decisions`` section
    (``- Build check: phase <N>``, ``N`` a positive integer), fence-blind as :func:`cited_target` is. ``None``
    with no section, no such bullet, or a first bullet that does not read (``phase 0``, ``phase x``); a line under
    any other heading is never it."""
    if not isinstance(plan_text, str):
        return None
    lines = plan_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    span = parse._find_section(lines, UI_DECISIONS_HEADING_PATTERN)  # noqa: SLF001 (the one section finder)
    if span is None:
        return None
    for line in lines[span[0]:span[1]]:
        text = line.strip()
        if not text.startswith(BUILD_CHECK_PREFIX):
            continue
        match = _BUILD_CHECK_RE.match(text)
        return int(match.group(1)) if match else None
    return None


def citation(record, plan_target):
    """`facts.design.citation`, first match wins: `none` — no plan cites a record; `missing` — it does and no
    trusted record is present (deleted, or only untrusted copies remain); `unapproved` — the record is `pending`,
    has findings, has no state or has a state with no capture; `current` — the cited fingerprint is the record's;
    `revised` — otherwise, a record the designer revised and re-approved since (#205)."""
    if plan_target is None:
        return CITATION_NONE
    if not (record or {}).get("present"):
        return CITATION_MISSING
    if not record.get("approved"):
        return CITATION_UNAPPROVED
    if plan_target.get("fingerprint") == record.get("fingerprint"):
        return CITATION_CURRENT
    return CITATION_REVISED


def _check_phase(plan_body, plan_target):
    """``(check_phase, readable)``: the plan's `Build check:` phase, or — when it cites a target but names none —
    its last `code-shipping` phase (``1`` with no `## Phases`). ``readable`` is ``False`` when the `## Phases`
    section is present but `parse.parse_phases` refuses it, or names no `code-shipping` phase the check could run
    at, or the `Build check:` line names a phase that is not one: then ``check_phase`` is ``None`` and the check
    holds, so a check is never placed on a phase that ships no build."""
    cited = cited_check(plan_body)
    if cited is None and plan_target is None:
        return None, True
    try:
        phases = parse.parse_phases(plan_body or "")
    except parse._PhasesMalformed:  # noqa: SLF001
        return None, False
    code = [p["number"] for p in phases if p.get("kind") == "code-shipping"]
    if not phases:
        return (cited if cited is not None else 1), cited in (None, 1)
    if cited is not None:
        return (cited, True) if cited in code else (None, False)
    return (code[-1], True) if code else (None, False)


def design_fact(record, block, plan_body):
    """`facts.design = {record, block, plan_target, citation, check_phase, check}` — one builder every prep that
    reads a design composes (the planner's #205, the resolver's, the evaluator's and the review tool's #206), so all
    four read the same record, approval and citation.

    ``record`` is :func:`read_and_stage`'s, ``block`` :func:`read_block` at the prep's checkout (``None`` on a
    ``--refresh``, which reads no checkout), ``plan_body`` the issue's whole plan (``None`` with none).
    ``plan_target`` is the record the plan cites (:func:`cited_target`); ``citation`` the closed set
    :func:`citation` derives; ``check_phase`` the phase the build is checked at (:func:`cited_check`, else the last
    `code-shipping` phase when a target is cited); ``check`` `none` with no citation, `run` on `current`, `hold`
    otherwise or when ``check_phase`` cannot be read."""
    plan_target = cited_target(plan_body) if plan_body else None
    state = citation(record, plan_target)
    check_phase, readable = _check_phase(plan_body, plan_target)
    if state == CITATION_NONE:
        check = CHECK_NONE
    elif state == CITATION_CURRENT and readable:
        check = CHECK_RUN
    else:
        check = CHECK_HOLD
    return {
        "record": record,
        "block": block,
        "plan_target": plan_target,
        "citation": state,
        "check_phase": check_phase if readable else None,
        "check": check,
    }


def is_record(comment):
    """Whether ``comment`` (a normalized thread comment) is a design record: its body's first line, once
    stripped, is exactly :data:`MARKER`. A marker anywhere else in a body is quoted text."""
    if not isinstance(comment, dict):
        return False
    lines = _lines(comment.get("body"))
    return bool(lines) and lines[0] == MARKER


def _absent():
    record = {"present": False, "comment_id": None, "url": None, "created_at": None, "fingerprint": None}
    record.update(_empty_parse())
    return record


def read_record(thread, issue=None):
    """The issue's one trusted design record, from its already-fetched comment ``thread``.

    Returns ``(record, notices, decision)``. ``record`` always carries every key —
    ``present``, ``comment_id``, ``url``, ``created_at``, ``fingerprint`` (:func:`fingerprint`, ``None``
    when absent) plus :func:`parse_record`'s — so a consumer never
    branches on a missing key. Untrusted records are skipped and reported once as ``MARKER_UNTRUSTED``
    (``where`` names ``issue #<issue>`` when ``issue`` is given). Two or more trusted records are a
    ``MARKER_AMBIGUOUS`` decision with the record absent: one record per issue is the contract, so a
    second one (a replace whose delete failed) is an anomaly a silent pick would hide.
    """
    notices = []
    candidates = [c for c in (thread or []) if is_record(c)]
    trusted, untrusted = partition_trusted(candidates)
    notice = untrusted_notice(untrusted, MARKER, "issue #%s" % issue if issue is not None else None)
    if notice:
        notices.append(notice)

    if len(trusted) > 1:
        decision = needs_decision(
            MARKER_AMBIGUOUS,
            summary="%d design records (`%s`) by trusted authors%s — expected one"
            % (len(trusted), MARKER, " on issue #%s" % issue if issue is not None else ""),
            context={
                "marker_prefix": MARKER,
                "comment_ids": [comment_rest_id(c) for c in trusted],
                "comment_urls": [c.get("url") for c in trusted],
            },
            options=[
                "inspect each comment and pick the one to treat as current",
                "delete the stale duplicate comment(s), then re-run",
            ],
        )
        return _absent(), notices, decision

    if not trusted:
        return _absent(), notices, None

    comment = trusted[0]
    record = {
        "present": True,
        "comment_id": comment_rest_id(comment),
        "url": comment.get("url"),
        "created_at": comment.get("createdAt"),
        "fingerprint": fingerprint(comment.get("body") or ""),
    }
    record.update(parse_record(comment.get("body")))
    return record, notices, None


def read_and_stage(thread, scratch_dir, issue, notices):
    """`facts.design.record`: :func:`read_record`'s payload (its ``fingerprint`` included) plus ``body_path``, the
    record's verbatim body staged at ``<scratch_dir>/design-record.md`` (:data:`DESIGN_RECORD_FILENAME`). With no
    record the payload is the reader's absent shape with ``body_path`` ``None``, and a file an earlier run staged
    is removed — on a `MARKER_AMBIGUOUS` decision too — so no reader takes a deleted record for the current one.
    Returns ``(record, decision)``,
    ``record`` ``None`` only beside a decision; `MARKER_UNTRUSTED` joins ``notices`` and `MARKER_AMBIGUOUS` is
    the decision, which the prep forwards as its own. Moved from ``prep_planner.py`` (#206); the read is
    :func:`read_record`'s, never a re-parse."""
    record, record_notices, decision = read_record(thread, issue)
    for notice in record_notices:
        if notice not in notices:
            notices.append(notice)
    path = Path(scratch_dir) / DESIGN_RECORD_FILENAME
    if decision is not None:
        # No record stands while two do: a body an earlier run staged must not read as the current one.
        path.unlink(missing_ok=True)
        return None, decision
    record = dict(record)
    record["body_path"] = None
    if not record["present"]:
        path.unlink(missing_ok=True)
        return record, None
    for comment in thread or []:
        if is_record(comment) and comment_rest_id(comment) == record["comment_id"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_text(path, comment.get("body") or "", prefix=".design-record-")
            record["body_path"] = str(path)
            break
    return record, None


def locate(root):
    """The copy of the ``<!-- ui-prototype -->`` block the stages read, raw: ``(found, interior_lines,
    absolute_source)`` from ``config_block.read_block_anywhere`` — COMMANDS.md, then CLAUDE.md, then one
    level of their ``@``-includes; the first well-formed copy wins, an empty one included. A candidate
    file that cannot be read, or is not valid UTF-8, reads as ``(False, [], None)``. The one discovery
    rule :func:`read_block` and ``prep_setup.py``'s re-ingest base share (#203), so setup can never base
    on a copy the designer does not see."""
    try:
        return config_block.read_block_anywhere(root, BLOCK_MARKER)
    except (OSError, UnicodeDecodeError, ValueError):
        return False, [], None


def read_block(root):
    """The consuming repo's ``<!-- ui-prototype -->`` block as ``{"present", "source", "text"}``.

    Read through ``config_block.read_block_anywhere`` — ``COMMANDS.md``, then ``CLAUDE.md``, then one
    level of their ``@``-includes, from the working tree under ``root``. ``source`` is the absolute path
    of the file read; ``text`` the interior, unchanged, joined with ``\\n``. An absent, malformed or
    whitespace-only block reads as not present.
    """
    present, lines, source = locate(root)
    text = "\n".join(lines or [])
    if not present or not text.strip():
        return {"present": False, "source": None, "text": None}
    return {"present": True, "source": source, "text": text}


class _ImageSources(html.parser.HTMLParser):
    """Every ``<img>``'s ``(alt, src)`` in a comment's rendered ``body_html``, in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            values = dict(attrs)
            self.images.append((values.get("alt"), values.get("src")))

    handle_startendtag = handle_starttag


def image_sources(body_html):
    """``{alt: src}`` for every ``<img>`` in ``body_html`` with both, the first per ``alt`` winning. GitHub renders
    a record's Markdown-image capture ``![<state-id>](…)`` as an ``<img alt="<state-id>">`` whose ``src`` is, on a
    private repo, the short-lived signed URL the attachment is fetched from. Pure; never raises."""
    parser = _ImageSources()
    try:
        parser.feed(body_html if isinstance(body_html, str) else "")
        parser.close()
    except Exception:  # noqa: BLE001 (html.parser is lenient; anything it still raises reads as no images)
        return {}
    sources = {}
    for alt, src in parser.images:
        if alt and src and alt not in sources:
            sources[alt] = src
    return sources


def allowed_capture_url(url):
    """Whether ``url`` may be fetched for a capture: ``https``, no credentials, the default port, and a host in
    :data:`ALLOWED_CAPTURE_HOSTS` exactly (no suffix match, so ``evil-github.com`` is refused). Pure."""
    try:
        parts = urllib.parse.urlsplit(url if isinstance(url, str) else "")
        port = parts.port
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.username is None and parts.password is None
            and port in (None, 443) and (parts.hostname or "").lower() in ALLOWED_CAPTURE_HOSTS)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to an allowlisted ``https`` URL (:func:`allowed_capture_url`), at most
    :data:`_MAX_REDIRECTS` hops; any other hop is refused as an ``HTTPError``, so the state is unavailable."""

    max_redirections = _MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not allowed_capture_url(target):
            raise urllib.error.HTTPError(target, code, "redirect off the capture allowlist", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, target)


def _default_opener(url):
    """Fetch ``url`` with no credentials, following only allowlisted redirects."""
    opener = urllib.request.build_opener(_CheckedRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": "ctrl-freaq-design-check"})
    return opener.open(request, timeout=30)


def _default_runner(argv):
    from pipelib import process  # the locked-down runner; imported here so the pure core never loads it

    return process.run(argv)


def _comment_html(repo, comment_id, runner):
    """``(body_html, error)``: the record comment's rendered body, read by one ``gh api`` call with the
    ``full+json`` media type, which carries the signed attachment URLs a private repo serves."""
    try:
        result = runner(["gh", "api", "-H", "Accept: application/vnd.github.full+json",
                         "repos/%s/issues/comments/%s" % (repo, comment_id)])
    except (OSError, ValueError, TypeError) as exc:
        return None, "the comment could not be read (%s)" % exc
    if getattr(result, "returncode", 1) != 0:
        return None, "the comment could not be read (gh exit %s)" % getattr(result, "returncode", "?")
    try:
        body_html = json.loads(getattr(result, "stdout", "") or "").get("body_html")
    except (ValueError, AttributeError):
        return None, "the comment read returned no JSON"
    if not isinstance(body_html, str):
        return None, "the comment read carried no body_html"
    return body_html, None


def _suffix(content_type, reference):
    """The staged file's suffix: from the content type when known, else the reference's own suffix when it reads
    as one, else ``.bin``. The name itself is always the state id, never a comment-supplied string."""
    known = _CONTENT_TYPE_SUFFIX.get((content_type or "").split(";")[0].strip().lower())
    if known:
        return known
    path = urllib.parse.urlsplit(reference).path if "://" in reference else reference
    suffix = Path(path).suffix
    return suffix.lower() if _SUFFIX_RE.match(suffix) else ".bin"


def _fetch_capture(url, opener):
    """``(data, content_type, error)`` for an allowlisted ``url``: at most :data:`CAPTURE_MAX_BYTES` read, the
    final URL re-checked against the allowlist."""
    if not allowed_capture_url(url):
        return None, None, "the capture URL is not https on an allowlisted host"
    try:
        response = opener(url)
    except Exception as exc:  # noqa: BLE001 (urllib, http.client and socket failures alike: the state is unavailable)
        message = "the capture could not be fetched (%s)" % exc
        # An HTTPError (a refused redirect included) carries the response it refused: close it, or its socket leaks.
        close = getattr(exc, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        return None, None, message
    try:
        final = response.geturl() if hasattr(response, "geturl") else url
        if not allowed_capture_url(final):
            return None, None, "the capture redirected off the allowlist"
        data = response.read(CAPTURE_MAX_BYTES + 1)
        headers = getattr(response, "headers", None) or {}
        content_type = headers.get("Content-Type") if hasattr(headers, "get") else None
    except Exception as exc:  # noqa: BLE001
        return None, None, "the capture could not be read (%s)" % exc
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if not isinstance(data, (bytes, bytearray)):
        return None, None, "the capture read returned no bytes"
    if len(data) > CAPTURE_MAX_BYTES:
        return None, None, "the capture is larger than %d bytes" % CAPTURE_MAX_BYTES
    return bytes(data), content_type, None


def _copy_capture(root, reference, target_stem):
    """``(staged_path, error)``: a path reference copied from inside ``root`` — its real path (symlinks followed)
    must stay inside ``root``'s, be a regular file, and be no larger than :data:`CAPTURE_MAX_BYTES`."""
    rel = reference[2:] if reference.startswith("./") else reference
    try:
        root_real = Path(root).resolve()
        source = (root_real / rel).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return None, "the capture path could not be resolved (%s)" % exc
    if root_real not in source.parents or not source.is_file():
        return None, "the capture path is not a file inside the checkout"
    try:
        if source.stat().st_size > CAPTURE_MAX_BYTES:
            return None, "the capture is larger than %d bytes" % CAPTURE_MAX_BYTES
        target = target_stem.with_name(target_stem.name + _suffix(None, rel))
        shutil.copyfile(source, target)
    except OSError as exc:
        return None, "the capture could not be copied (%s)" % exc
    return str(target), None


def stage_captures(repo, comment_id, record, root, out_dir, runner=None, opener=None):
    """Stage the design record's captures for the comparison (#206): ``(payload, notices)``.

    ``record`` is :func:`parse_record`'s dict (or :func:`read_record`'s). Each state's capture is staged at
    ``<out_dir>/<state-id><suffix>``:

    - a **path** reference is copied from inside ``root`` (:func:`_copy_capture`);
    - a **URL** or **Markdown-image** reference is downloaded from the ``src`` of the ``<img>`` whose ``alt`` is
      the state id in the comment's ``body_html`` (one ``gh api`` read, made only when a state needs it, the
      fetch made at once because a private repo's signed URLs are short-lived), else from the reference's own
      URL — either way only an allowlisted ``https`` URL, every redirect hop and the final URL re-checked, at
      most :data:`CAPTURE_MAX_BYTES`.

    Each failure is one `DESIGN_CAPTURE_UNAVAILABLE` notice naming the state, its ``staged_path`` ``None`` and its
    ``error`` the reason — never a hard exit. ``payload`` is ``{states: [{id, description, capture, staged_path,
    error}], staged, missing}``. ``runner`` (an argv list to a ``CommandResult``-like) and ``opener`` (a URL to a
    response with ``read`` / ``geturl`` / ``headers``) are injectable for the offline suite; no credential is
    ever sent with a capture fetch."""
    runner = runner or _default_runner
    opener = opener or _default_opener
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    html_state = {"read": False, "sources": {}, "error": None}

    def sources():
        if not html_state["read"]:
            html_state["read"] = True
            body_html, error = _comment_html(repo, comment_id, runner)
            html_state["sources"], html_state["error"] = image_sources(body_html), error
        return html_state["sources"]

    states, notices = [], []
    for state in (record or {}).get("states") or []:
        state_id = state.get("id")
        reference = (state.get("capture") or "").strip()
        entry = {"id": state_id, "description": state.get("description"), "capture": state.get("capture") or None,
                 "staged_path": None, "error": None}
        states.append(entry)
        if not isinstance(state_id, str) or not re.fullmatch(_STATE_ID, state_id):
            entry["error"] = "the state id cannot name a file"
        else:
            stem = out / state_id
            for stale in out.glob(state_id + ".*"):
                stale.unlink(missing_ok=True)
            if not reference:
                entry["error"] = "the state has no capture"
            else:
                image = _MARKDOWN_IMAGE_RE.match(reference)
                target = image.group(1) if image else reference
                if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", target):
                    url = sources().get(state_id) or target
                    data, content_type, error = _fetch_capture(url, opener)
                    if error is None:
                        staged = stem.with_name(state_id + _suffix(content_type, target))
                        try:
                            staged.write_bytes(data)
                            entry["staged_path"] = str(staged)
                        except OSError as exc:
                            error = "the capture could not be written (%s)" % exc
                    if error is not None and html_state["error"] and url == target:
                        error = "%s; %s" % (error, html_state["error"])
                    entry["error"] = error
                else:
                    entry["staged_path"], entry["error"] = _copy_capture(root, target, stem)
        if entry["staged_path"] is None:
            notices.append("%s: state `%s`: %s; the design check cannot run until it stages"
                           % (DESIGN_CAPTURE_UNAVAILABLE, state_id, entry["error"]))
    missing = [s["id"] for s in states if s["staged_path"] is None]
    return {"states": states, "staged": len(states) - len(missing), "missing": missing}, notices


def main(argv):
    """The CLI. ``check <body_path>`` — :func:`parse_record` over a staged record, emitted as one ``ok``
    envelope whatever it finds (a finding is fixed in the staged body, never a gate). ``stage <owner/repo>
    --comment-id <id> --record <body_path> --root <checkout> --out <dir>`` (#206) — :func:`stage_captures` over
    the staged record (``facts.design.record.body_path`` / ``comment_id``), one ``ok`` envelope whose notices name
    every state it could not stage. A missing subcommand or an unreadable path is a usage error (exit 2, no
    envelope)."""
    parser = argparse.ArgumentParser(
        prog="design_record.py", description="Check a staged design record, or stage its captures."
    )
    sub = parser.add_subparsers(dest="subcommand")
    p_check = sub.add_parser("check")
    p_check.add_argument("body_path", help="the staged record body")
    p_stage = sub.add_parser("stage")
    p_stage.add_argument("repo", help="owner/repo")
    p_stage.add_argument("--comment-id", required=True, help="facts.design.record.comment_id (the REST id)")
    p_stage.add_argument("--record", required=True, help="facts.design.record.body_path")
    p_stage.add_argument("--root", required=True, help="the checkout a path capture is copied from")
    p_stage.add_argument("--out", required=True, help="the directory the captures are staged in")
    args = parser.parse_args(argv)
    if args.subcommand not in ("check", "stage"):
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    path = args.body_path if args.subcommand == "check" else args.record
    try:
        body = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("design_record.py %s: cannot read %s: %s\n" % (args.subcommand, path, exc))
        sys.exit(EXIT_USAGE_ERROR)
    if args.subcommand == "check":
        emit_ok(payload=parse_record(body))
        sys.exit(EXIT_OK)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) or not re.fullmatch(r"[1-9][0-9]*",
                                                                                           args.comment_id):
        parser.error("stage expects <owner/repo> and a numeric --comment-id")
    payload, notices = stage_captures(args.repo, args.comment_id, parse_record(body), args.root, args.out)
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
