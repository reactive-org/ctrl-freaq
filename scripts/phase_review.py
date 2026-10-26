#!/usr/bin/env python3
"""phase_review.py — the resolver's context-usage rule and its Review card's links (#155).

**Why a script.** Whether a resolver session has room for another phase is arithmetic over the
host's context-usage reading, and a link into a PR's phase-scoped Files view is string assembly, so
both are a script's (CLAUDE.md "facts by script, meaning by model"). The model still decides when a
milestone is reached, what the operator's review notes ask for, and which files a "What to check"
item names (`skills/resolver/references/phase-continuation.md`).

**`context`.** One reading of the session's own context usage, taken at a milestone (`--point`:
`start` of a phase, after a review `round`, `end` of a phase after its push, or a Review card
`answer`). The reading is appended to ``<scratch>/context-usage.jsonl`` (:data:`USAGE_FILE`), the
session's ledger: a growth figure is a fact of this session only. The scratch dir is the issue's and
outlives the session, so the session's first reading passes ``--new-session``, which empties the ledger
before appending; a later prep in the same session (an in-session continuation) does not. The share is measured against the host-reported auto-compact
point, never the raw window — a share of the window runs below the same share of the auto-compact
point, so a wrap-up measured against the window could arrive after compaction fired:

    limit = window × auto_compact_pct / 100;  share = used / limit

Bands: ``under`` (share < :data:`FINISH_SHARE`), ``finish`` (≥ :data:`FINISH_SHARE`: finish the
current phase, then end), ``wrap`` (≥ :data:`WRAP_SHARE`: wrap up now). With no
``--auto-compact-pct`` the band is ``unknown`` and ``share`` / ``projected_share`` are null, which the
resolver treats as a host with no usage tool. A pass's growth is its ``end`` reading minus the ``start``
reading that opened it (a changes-required pass of a shipped phase is a pass of its own);
``projected_share`` adds the largest growth so far (max, not mean: one heavy
phase is the evidence the next may be heavy), and ``projection_crosses_wrap`` is whether that reaches
:data:`WRAP_SHARE`. No completed phase yet → no projection. A growth below zero (the host compacted
mid-phase) counts as zero.

**`links`.** ``phase_url`` is ``<pr-url>/files/<entry7>..<head7>`` — the PR's Files view scoped to
one phase's commits — or ``<pr-url>/files`` with no ``--entry`` (a fresh first phase, whose PR is
that phase). Each path's ``url`` adds GitHub's Files-view anchor, ``#diff-<sha256 hex of the path>``;
if GitHub changes that convention the link still opens the phase view, only the scroll target is lost.

Always an ``ok`` envelope (architecture.md §3): no decision code. Exit 2 on a usage error (a missing
flag, a non-positive ``--window``, a negative ``--used``, an ``--auto-compact-pct`` outside (0, 100],
``--entry`` without ``--head`` or the reverse, an ``--entry`` / ``--head`` that is not a 7–40 character
hex SHA). An unwritable ``--scratch`` fails with a traceback, no envelope. Spawns nothing.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402

# The context thresholds, as a share of the auto-compact point — the one source; the coordinator's
# stage brief restates them, and a test pins the two equal.
FINISH_SHARE = 0.65
WRAP_SHARE = 0.90
USAGE_FILE = "context-usage.jsonl"
POINTS = ("start", "round", "end", "answer")
_SHA_RE = re.compile(r"[0-9a-f]{7,40}")


def _band(share):
    if share is None:
        return "unknown"
    if share >= WRAP_SHARE:
        return "wrap"
    if share >= FINISH_SHARE:
        return "finish"
    return "under"


def _read_ledger(path):
    """Every well-formed reading in the ledger, in order. A line that does not parse is skipped: the
    ledger is this script's own output, and a torn line must not stop the reading that follows it."""
    readings = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return readings
    for line in text.splitlines():
        try:
            reading = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(reading, dict)
            and isinstance(reading.get("phase"), int)
            and reading.get("point") in POINTS
            and isinstance(reading.get("used"), int)
        ):
            readings.append(reading)
    return readings


def _phase_growth(readings):
    """``[{phase, tokens}]``, one entry per completed pass in ledger order. A ``start`` reading opens a
    pass of its phase and the next ``end`` reading for that phase closes it, so a phase run twice (a
    changes-required pass of a shipped phase) is two entries. A ``start`` with no ``end`` yet is an
    open pass and yields nothing; a further ``start`` for a phase whose pass is open (a prep re-run
    within the pass) keeps the first, so the pass's growth is never shortened; an ``end`` with no open
    ``start`` is ignored. Growth below zero (the host compacted mid-pass) counts as zero."""
    open_passes, growth = {}, []
    for reading in readings:
        phase = reading["phase"]
        if reading["point"] == "start":
            open_passes.setdefault(phase, reading["used"])
        elif reading["point"] == "end" and phase in open_passes:
            growth.append({"phase": phase, "tokens": max(0, reading["used"] - open_passes.pop(phase))})
    return growth


def build_context(scratch, phase, point, used, window, auto_compact_pct=None, new_session=False):
    """Append one reading to ``<scratch>/context-usage.jsonl`` — after emptying it when ``new_session``
    (the session's first reading: the scratch dir is the issue's, shared by every session on it, so a
    prior session's passes would otherwise feed this session's projection) — and return the payload
    ``{share, band, limit_tokens, phase_growth, projected_share, projection_crosses_wrap, thresholds}``.
    Raises ``ValueError`` on an invalid argument (the CLI's exit 2)."""
    if point not in POINTS:
        raise ValueError("--point must be one of %s" % "|".join(POINTS))
    if not isinstance(window, int) or window <= 0:
        raise ValueError("--window must be a positive integer")
    if not isinstance(used, int) or used < 0:
        raise ValueError("--used must be a non-negative integer")
    if auto_compact_pct is not None and not 0 < auto_compact_pct <= 100:
        raise ValueError("--auto-compact-pct must be in (0, 100]")
    directory = Path(scratch)
    directory.mkdir(parents=True, exist_ok=True)
    ledger = directory / USAGE_FILE
    reading = {"phase": phase, "point": point, "used": used, "window": window, "auto_compact_pct": auto_compact_pct}
    with ledger.open("w" if new_session else "a", encoding="utf-8") as handle:
        handle.write(json.dumps(reading) + "\n")

    growth = _phase_growth(_read_ledger(ledger))
    limit = window * auto_compact_pct / 100 if auto_compact_pct is not None else None
    share = used / limit if limit else None
    projected = None
    if share is not None and growth:
        projected = share + max(entry["tokens"] for entry in growth) / limit
    return {
        "share": share,
        "band": _band(share),
        "limit_tokens": int(limit) if limit is not None else None,
        "phase_growth": growth,
        "projected_share": projected,
        "projection_crosses_wrap": projected is not None and projected >= WRAP_SHARE,
        "thresholds": {"finish": FINISH_SHARE, "wrap": WRAP_SHARE},
    }


def build_links(pr_url, paths, entry=None, head=None):
    """``{phase_url, files: [{path, url}]}``, files in argument order. Raises ``ValueError`` when exactly
    one of ``entry`` / ``head`` is given, or either is not a 7–40 character lowercase hex SHA — a ref name
    would be cut to seven characters into a range GitHub cannot resolve (the CLI's exit 2)."""
    if (entry is None) != (head is None):
        raise ValueError("--entry and --head go together")
    if entry is not None and not (_SHA_RE.fullmatch(entry) and _SHA_RE.fullmatch(head)):
        raise ValueError("--entry and --head must be commit SHAs (7-40 lowercase hex)")
    base = pr_url.rstrip("/") + "/files"
    phase_url = base if entry is None else "%s/%s..%s" % (base, entry[:7], head[:7])
    files = [
        {"path": path, "url": "%s#diff-%s" % (phase_url, hashlib.sha256(path.encode("utf-8")).hexdigest())}
        for path in paths
    ]
    return {"phase_url": phase_url, "files": files}


def _usage(parser, message):
    parser.print_usage(sys.stderr)
    sys.stderr.write("phase_review.py: error: %s\n" % message)
    sys.exit(EXIT_USAGE_ERROR)


def main(argv):
    parser = argparse.ArgumentParser(prog="phase_review.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="subcommand")
    p_context = sub.add_parser("context")
    p_context.add_argument("--scratch", required=True, help="the run scratch dir (facts.scratch)")
    p_context.add_argument("--phase", required=True, type=int)
    p_context.add_argument("--point", required=True, choices=POINTS)
    p_context.add_argument("--used", required=True, type=int, help="the host's context tokens used")
    p_context.add_argument("--window", required=True, type=int, help="the host's context window, in tokens")
    p_context.add_argument(
        "--new-session",
        action="store_true",
        help="the session's first reading: start the ledger afresh (the scratch dir outlives the session)",
    )
    p_context.add_argument(
        "--auto-compact-pct", type=float, help="where the host auto-compacts, as a percent of the window"
    )
    p_links = sub.add_parser("links")
    p_links.add_argument("--pr-url", required=True)
    p_links.add_argument("--entry", help="the phase's entry SHA (omit for a fresh first phase)")
    p_links.add_argument("--head", help="the phase's head SHA")
    p_links.add_argument("paths", nargs="*")
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "context":
            payload = build_context(
                args.scratch, args.phase, args.point, args.used, args.window, args.auto_compact_pct,
                new_session=args.new_session,
            )
        elif args.subcommand == "links":
            payload = build_links(args.pr_url, args.paths, entry=args.entry, head=args.head)
        else:
            _usage(parser, "a subcommand is required")
    except ValueError as exc:
        _usage(parser, str(exc))
    emit_ok(payload=payload)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
