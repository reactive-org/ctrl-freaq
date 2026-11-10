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
  every config block is (architecture.md §6 rule 1), and passes its text through unchanged.

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

Import-only, stdlib-only, pure (architecture.md §2's pure-core pattern): no CLI, no ``gh`` call; it never
prints, exits, or raises on malformed input. The preps of the stages that read a design compose it
in-process, as they compose ``delivery_log`` and ``review_guidance``.
"""

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
from delivery_log import comment_rest_id  # noqa: E402  (the one REST-id rule, #34)
from pipelib.decisions import MARKER_AMBIGUOUS, needs_decision  # noqa: E402
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
_APPROVED_RE = re.compile(r"^approved (\d{4}-\d{2}-\d{2})$")

REASON_STATE_MALFORMED = "state line does not match the grammar"
REASON_STATE_DUPLICATE = "duplicate state id"
REASON_STATE_OUTSIDE = "state-shaped line outside the ## States section"
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
    # matched without its trailing space because the line was stripped, so an empty reference leaves
    # the line ending "— capture:".
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
    # A state-shaped line outside the region is never a state, but it is a finding: one appended below
    # the Approval: line would otherwise hide an agreed state with no capture behind an approved record.
    for index, text in enumerate(lines):
        if _STATE_HEAD_RE.match(text) and (start is None or not start <= index < end):
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


def is_record(comment):
    """Whether ``comment`` (a normalized thread comment) is a design record: its body's first line, once
    stripped, is exactly :data:`MARKER`. A marker anywhere else in a body is quoted text."""
    if not isinstance(comment, dict):
        return False
    lines = _lines(comment.get("body"))
    return bool(lines) and lines[0] == MARKER


def _absent():
    record = {"present": False, "comment_id": None, "url": None, "created_at": None}
    record.update(_empty_parse())
    return record


def read_record(thread, issue=None):
    """The issue's one trusted design record, from its already-fetched comment ``thread``.

    Returns ``(record, notices, decision)``. ``record`` always carries every key —
    ``present``, ``comment_id``, ``url``, ``created_at`` plus :func:`parse_record`'s — so a consumer never
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
    }
    record.update(parse_record(comment.get("body")))
    return record, notices, None


def read_block(root):
    """The consuming repo's ``<!-- ui-prototype -->`` block as ``{"present", "source", "text"}``.

    Read through ``config_block.read_block_anywhere`` — ``COMMANDS.md``, then ``CLAUDE.md``, then one
    level of their ``@``-includes, from the working tree under ``root``. ``source`` is the absolute path
    of the file read; ``text`` the interior, unchanged, joined with ``\\n``. An absent, malformed or
    whitespace-only block reads as not present.
    """
    try:
        present, lines, source = config_block.read_block_anywhere(root, BLOCK_MARKER)
    except (OSError, UnicodeDecodeError, ValueError):
        present, lines, source = False, [], None
    text = "\n".join(lines or [])
    if not present or not text.strip():
        return {"present": False, "source": None, "text": None}
    return {"present": True, "source": source, "text": text}
