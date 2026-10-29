#!/usr/bin/env python3
"""plan_shipped.py — the legacy shipped-phase records' read model and fold-back check
(``skills/_shared/plan-shipped-phases.md``).

Until 4.35.0 a revise MOVED each shipped phase's ``## Changes`` / ``## Data model / schema impact`` /
``## Test plan`` entries out of the plan comment into one record comment per phase, keyed
``(PR, phase)``, leaving a pointer bullet behind — the only way a growing plan stayed under the
per-comment cap. Those sections now live in the plan's own build part (``plan_parts.py``), so
nothing writes a record any more. Existing ones are still the only copy of their entries, so they
are read until a revise folds them back.

Two surfaces:

- **The read model** (import-only, pure — never prints, exits or raises): :func:`collect` resolves
  the records for one PR out of an already-fetched thread (a thread scan, zero extra ``gh`` calls)
  and :func:`read_pointed` follows the plan's pointer bullets to every PR they name. The resolver
  and evaluator preps read records this way until the plan is revised; the planner's prep reads
  them as the fold-back source.
- **``check``** (CLI): run by the planner after folding records back. Reports any pointer bullet
  still in the plan and any record entry that did not land in it, verbatim. Findings are the
  planner's to fix before persisting (the ``PHASES_MALFORMED`` posture) — ``ok`` always, no
  decision code.

Usage:
    plan_shipped.py check <plan> --restored <record> [--restored <record> ...]
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib.decisions import MARKER_AMBIGUOUS, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402
from pipelib.spill import spill_bytes  # noqa: E402
from pipelib.trust import partition_trusted  # noqa: E402

# A distinct marker FAMILY, deliberately: `<!-- implementation-plan-shipped:` never starts with
# `<!-- implementation-plan:v1 -->`, so every existing plan lookup (gh_gather's `startswith`, the
# resolver audit's jq) stays blind to the records — no MARKER_AMBIGUOUS on the plan itself.
# `:phase:1 -->` is not a prefix of `:phase:10 -->`: the trailing ` -->` terminates the match.
MARKER_PREFIX = "<!-- implementation-plan-shipped:v1:phase:"
MARKER_SUFFIX = " -->"

_MARKER_RE = re.compile(re.escape(MARKER_PREFIX) + r"(\d+)" + re.escape(MARKER_SUFFIX))

# Line 2 carries the rest of the key. The PR number scopes a record to the PR its phase shipped on:
# a HARD Start-fresh closes that PR, and its records go inert without any delete.
# The separators are the template's `·` and `—`, but an ASCII `-` / `|` or an en dash reads the same:
# a record that fails to key is invisible to every reader, so the grammar is lenient on punctuation
# and strict only on what carries the key (the PR and phase numbers).
_SHIPPED_ON_RE = re.compile(
    r"^\*\*Shipped on:\*\*\s+#(\d+)\s+[·|\-–—]\s+Phase\s+(\d+)\s+[—–\-:]\s+(\S.*?)\s*$"
)

# The pointer bullet a pre-4.35.0 revise left in place of moved entries. Read leniently — any
# bullet saying "shipped on #<N>" and naming shipped-phase records — so a pointer reworded on some
# later revise still leads to its records.
_POINTER_LOOSE_RE = re.compile(r"^\s*[-*]\s.*\bshipped on #(\d+)\b.*shipped-phase record", re.IGNORECASE)

# The three sections a record could carry entries for.
SECTIONS = (
    ("changes", r"Changes \(file-level\)"),
    ("data_model", r"Data model / schema impact"),
    ("test_plan", r"Test plan"),
)

_HEADING_RE = re.compile(r"^##(?!#)")
_BULLET_RE = re.compile(r"^(\s*)[-*] ")


def entry_marker(phase_number):
    """The record marker line for ``phase_number``. The single place this string is built."""
    return "%s%s%s" % (MARKER_PREFIX, phase_number, MARKER_SUFFIX)


def parse_head(body):
    """``(phase, pr, title)`` from a record body's first two lines, or ``None`` when line 1 is not a
    record marker. ``(phase, None, None)`` when line 1 is a marker but line 2 is missing, malformed,
    or names a different phase — a record that cannot be keyed."""
    lines = (body or "").split("\n")
    marker = _MARKER_RE.fullmatch(lines[0].strip()) if lines else None
    if marker is None:
        return None
    phase = int(marker.group(1))
    shipped_on = _SHIPPED_ON_RE.match(lines[1].strip()) if len(lines) > 1 else None
    if shipped_on is None or int(shipped_on.group(2)) != phase:
        return phase, None, None
    return phase, int(shipped_on.group(1)), shipped_on.group(3)


def pointer_prs(plan_body):
    """The distinct PR numbers the plan's pointer bullets name, sorted — read with the lenient
    grammar. A plan relocated on one PR names exactly one; none means nothing was ever relocated."""
    prs = set()
    for line in (plan_body or "").split("\n"):
        match = _POINTER_LOOSE_RE.match(line.rstrip())
        if match:
            prs.add(int(match.group(1)))
    return sorted(prs)


def read_prs(thread_list, prs):
    """:func:`collect` merged across ``prs``. Returns ``(records, decision)``: ``records`` adds
    ``prs`` and gives each entry its ``pr``; ``untrusted`` is counted once over the thread; ``pr`` is set only when exactly one PR was read.
    ``decision`` is the first ``MARKER_AMBIGUOUS`` any PR produced."""
    merged = {
        "present": False,
        "pr": prs[0] if len(prs) == 1 else None,
        "prs": list(prs),
        "entries": [],
        "phases": [],
        "duplicated_phases": [],
        "text": "",
    }
    # PR-agnostic: an unkeyed record has no PR, and the untrusted count is taken over the whole
    # thread, so every collect reports the same values — read once here, never summed per PR.
    agnostic = collect(thread_list, None)[0]
    merged["unkeyed"] = agnostic["unkeyed"]
    merged["untrusted"] = agnostic["untrusted"]
    decision = None
    texts = []
    for pr in prs:
        records, pr_decision = collect(thread_list, pr)
        decision = decision or pr_decision
        merged["entries"] += [dict(entry, pr=pr) for entry in records["entries"]]
        merged["phases"] += records["phases"]
        merged["duplicated_phases"] += records["duplicated_phases"]
        if records["text"]:
            texts.append(records["text"])
    merged["present"] = bool(merged["entries"])
    merged["text"] = "\n\n".join(texts)
    merged["text_chars"] = len(merged["text"])
    return merged, decision


def read_pointed(thread_list, plan_body, current_pr):
    """Every record a reader of this plan needs, and the notes a reader surfaces about them.

    The plan's pointer bullets are the authority: they name the PR(s) whose records hold the entries
    the plan no longer carries. That is normally the open PR, but not after it closed (a HARD
    Start-fresh, a hand-closed PR) — then those records are the ONLY copy of their entries, so they
    are read anyway. Every named PR is read, never one chosen among several: a half-restored plan
    naming a closed PR and the open one needs both. With no pointer, ``current_pr``'s records are read.

    Returns ``(records, notes, decision)`` — :func:`read_prs`'s records (``records["untrusted"]`` the
    skipped untrusted record comments, which a prep forwards as a ``MARKER_UNTRUSTED`` notice) plus
    ``foreign_prs`` (the PRs read that are not ``current_pr``).
    """
    named = pointer_prs(plan_body)
    prs = named or ([int(current_pr)] if current_pr is not None else [])
    records, decision = read_prs(thread_list, prs)
    records["foreign_prs"] = [pr for pr in prs if current_pr is None or pr != int(current_pr)]

    notes = []
    if records["foreign_prs"]:
        notes.append(
            "the plan points at shipped-phase records on %s, not the %s — those records are the only "
            "copy of their entries; a revise restores them into the plan (skills/_shared/"
            "plan-shipped-phases.md)"
            % (
                ", ".join("#%d" % pr for pr in records["foreign_prs"]),
                "open PR #%s" % current_pr if current_pr is not None else "open PR (there is none)",
            )
        )
    if records["unkeyed"]:
        notes.append(
            "%d shipped-phase record(s) cannot be keyed (line 2 unreadable) — no reader sees their "
            "entries" % len(records["unkeyed"])
        )
    return records, notes, decision


def staged_fact(records, scratch_dir, filename):
    """The one shape every prep's shipped-record fact takes: ``present``, ``pr`` (single PR or
    ``None``), ``prs``, ``entries``, ``unkeyed``, and — when any record was read — the records' text
    staged to ``body_path`` (always a path: it goes to ``check`` or a sub-agent)."""
    fact = {
        "present": records["present"],
        "pr": records.get("pr"),
        "prs": records.get("prs", [records["pr"]] if records.get("pr") is not None else []),
        "entries": records["entries"],
        "unkeyed": records["unkeyed"],
    }
    if records["present"]:
        fact["body_path"] = spill_bytes(
            records["text"].encode("utf-8"), "body", scratch_dir, force_path=True, filename=filename
        )["body_path"]
    return fact


def comment_rest_id(comment):
    """A thread-located comment's REST **numeric** id (``databaseId``) — the id space every REST
    comment endpoint addresses (#34). Same rule as ``delivery_log.comment_rest_id``."""
    if comment is None:
        return None
    return comment.get("databaseId", comment.get("id"))


def collect(thread_list, pr_number):
    """Resolve the shipped-phase records for ``pr_number`` from an issue's comment thread.

    Returns ``(records, decision)``. ``decision`` is ``MARKER_AMBIGUOUS`` when one phase carries more
    than one record on this PR; that phase is omitted (its content is exactly what is unknown) and
    every other phase still resolves. Records on other PRs are counted, never returned — they belong
    to a PR a Start-fresh closed.

    ``records`` keys: ``present``, ``pr``, ``entries`` (``{phase, title, comment_id, comment_url}``
    in phase order), ``phases``, ``other_pr_entries``, ``unkeyed`` (``{comment_id, comment_url}`` of
    record-marked comments whose line 2 cannot be read), ``duplicated_phases``, ``untrusted`` (how many
    record-marked comments an untrusted author wrote — skipped before anything else, ``pipelib.trust``,
    #179, so one can neither supply a phase's entries nor make it ambiguous), ``text`` (every record
    body, phase order, for staging to one path), ``text_chars``.
    """
    by_phase = {}
    other_pr = 0
    unkeyed = []
    marked, untrusted = partition_trusted(
        [c for c in thread_list or [] if parse_head((c or {}).get("body")) is not None]
    )
    for comment in marked:
        head = parse_head(comment.get("body"))
        phase, pr, title = head
        if pr is None:
            unkeyed.append({"comment_id": comment_rest_id(comment), "comment_url": comment.get("url")})
            continue
        if pr_number is None or pr != int(pr_number):
            other_pr += 1
            continue
        by_phase.setdefault(phase, []).append((comment, title))

    duplicated = sorted(phase for phase, hits in by_phase.items() if len(hits) > 1)
    decision = None
    if duplicated:
        phase = duplicated[0]
        clashing = [comment for comment, _ in by_phase[phase]]
        decision = needs_decision(
            MARKER_AMBIGUOUS,
            summary="%d shipped-phase records name phase %s on PR #%s — expected at most one"
            % (len(clashing), phase, pr_number),
            context={
                "marker_prefix": entry_marker(phase),
                "pr": pr_number,
                "phase": phase,
                "duplicated_phases": duplicated,
                "comment_ids": [comment_rest_id(c) for c in clashing],
                "comment_urls": [c.get("url") for c in clashing],
            },
            options=[
                "inspect each comment and pick the one to treat as current",
                "delete the stale duplicate comment(s), then re-run",
            ],
        )

    entries = []
    bodies = []
    for phase in sorted(p for p, hits in by_phase.items() if len(hits) == 1):
        comment, title = by_phase[phase][0]
        entries.append(
            {
                "phase": phase,
                "title": title,
                "comment_id": comment_rest_id(comment),
                "comment_url": comment.get("url"),
            }
        )
        bodies.append((comment.get("body") or "").strip())
    text = "\n\n".join(bodies)
    records = {
        "present": bool(entries),
        "pr": pr_number,
        "entries": entries,
        "phases": [entry["phase"] for entry in entries],
        "other_pr_entries": other_pr,
        "unkeyed": unkeyed,
        "duplicated_phases": duplicated,
        "untrusted": untrusted,
        "text": text,
        "text_chars": len(text),
    }
    return records, decision


# ---------------------------------------------------------------------------------------------
# `check` — the fold-back gate
# ---------------------------------------------------------------------------------------------


def _section_lines(lines, heading_pattern):
    """The body lines of ``## <heading>`` (heading excluded; a trailing ``(…)`` annotation on the
    heading tolerated), or ``None`` when absent. Same section-finder shape as ``parse.py``."""
    heading_re = re.compile(r"^##\s+" + heading_pattern + r"\s*(?:\(.*\))?\s*$", re.IGNORECASE)
    for i, line in enumerate(lines):
        if heading_re.match(line):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if _HEADING_RE.match(lines[j]):
                    end = j
                    break
            return lines[i + 1:end]
    return None


def _blocks(section_lines):
    """Split a section into bullet blocks: a bullet line (any depth) plus its non-blank, non-bullet
    continuation lines. Returns ``[(indent, lines_tuple, first_line_number_in_section)]``. This is
    the unit ``check`` compares at — the same algorithm on both sides, so a boundary choice can
    never itself make a verbatim move look edited."""
    blocks = []
    current = None
    for number, raw in enumerate(section_lines or []):
        line = raw.rstrip()
        bullet = _BULLET_RE.match(line)
        if bullet:
            if current is not None:
                blocks.append(current)
            current = [len(bullet.group(1)), [line], number]
        elif line and current is not None:
            current[1].append(line)
        else:
            if current is not None:
                blocks.append(current)
            current = None
    if current is not None:
        blocks.append(current)
    return [(indent, tuple(body), number) for indent, body, number in blocks]


def _is_parent(blocks, index):
    """A block that the next block nests under. Only a `## Test plan` kind line (`- Unit:`) may
    legitimately stay in main while some of its children move, so only there is a parent exempt from
    the moved-not-copied check. A `## Changes` parent left in main with a child moved IS the split
    the shared-entry rule forbids — the unit there is the whole top-level bullet."""
    return index + 1 < len(blocks) and blocks[index + 1][0] > blocks[index][0]


def check(plan_text, restored):
    """The fold-back facts for one revise. ``restored`` is ``[(path, text)]`` — the records this
    revise put back into the plan. Every entry they carry must be in the plan verbatim
    (``not_restored``: dropping or rewording one loses the only copy of it), and no pointer bullet
    may remain (``pointer_left``: nothing will ever write the record it would point at).

    Returns the payload dict; ``clean`` is true only when both lists are empty.
    """
    plan_lines = _lf(plan_text).split("\n")
    findings = {"pointer_left": [], "not_restored": []}
    for line in plan_lines:
        if _POINTER_LOOSE_RE.match(line.rstrip()):
            findings["pointer_left"].append(line.strip())
    for path, text in restored:
        lines_r = _lf(text).split("\n")
        for key, pattern in SECTIONS:
            restored_blocks = _blocks(_section_lines(lines_r, pattern))
            if not restored_blocks:
                continue
            landed = {body for _, body, _ in _blocks(_section_lines(plan_lines, pattern))}
            for index, (_, body, _) in enumerate(restored_blocks):
                # A `## Test plan` kind line (`- Unit:`) is repeated in each record; the plan carries
                # it once, so only its children are checked.
                if key == "test_plan" and _is_parent(restored_blocks, index):
                    continue
                if body not in landed:
                    findings["not_restored"].append({"path": path, "section": key, "line": body[0]})
    return {"findings": findings, "clean": not any(findings.values())}


def _lf(text):
    """``text`` with every line ending as ``\n`` — what universal-newline reading would have produced."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_or_die(path_str):
    # `newline=""`: a record arrives from GitHub, so CRLF is real; `check` normalizes it itself.
    try:
        with open(path_str, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("plan_shipped: cannot read %s: %s\n" % (path_str, exc))
        sys.exit(EXIT_USAGE_ERROR)


def main(argv):
    parser = argparse.ArgumentParser(prog="plan_shipped.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="subcommand")
    p_check = sub.add_parser("check")
    p_check.add_argument("plan")
    p_check.add_argument(
        "--restored", action="append", default=[], help="a shipped-phase record this revise folded back"
    )
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand != "check":
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    payload = check(
        _read_or_die(args.plan),
        [(path, _read_or_die(path)) for path in args.restored],
    )
    emit_ok(payload=payload)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
