#!/usr/bin/env python3
"""blast_radius.py — the resolver's review level for one loop pass (#97): the level the plan declares
for the phase, raised one step when the diff shows the plan underjudged its blast radius.

**Why a script, and why not prep.** The level is arithmetic over facts — the plan's `## Review level`
line, the declared sensitive paths, the files the plan names — so it is a script's (CLAUDE.md "facts
by script, meaning by model"). Prep runs before the phase's diff exists, so the resolver calls this
mid-session instead: at S5.1 loop entry, over the pass's review range
(`skills/resolver/references/review-level.md`). The blast-radius *judgment* stays the planner's; this
only reads what it wrote and what the diff touched.

**One read with the merge gate.** The sensitive-path signal is :func:`sensitive_paths.assess`, the
exact function ``prep_evaluator`` calls for the merge gate, so the resolver and the gate cannot
disagree about one PR. A failed read (``error``) raises, as it forces ``ask`` there: the stricter
outcome.

**Rules.** The plan may write ``medium``, ``high`` or ``xhigh`` per phase; a phase with no valid line
(absent, unparsed — ``low`` and ``max`` included — or duplicated) counts as ``high``. A final pass
starts from the highest level across every phase. A sensitive match, a failed sensitive read, or a
changed file no planned span matches raises one step, capped at ``xhigh`` — never ``max``, which stays
the operator's own ``code-review`` choice. A line or file count sets nothing.

Always an ``ok`` envelope (architecture.md §3): no decision code, since every unreadable input has a
known strict value. Exit 2 on a usage error (a missing or unreadable plan file, neither or both range
flags). Spawns only ``git``, through :mod:`pipelib.process`, inside :mod:`sensitive_paths`.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse  # noqa: E402
import sensitive_paths  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402

LEVELS = parse.REVIEW_LEVELS
# The level a phase gets when the plan gives it no valid line — cautious, like an absent
# `checkpoint:` meaning `pause`.
DEFAULT_LEVEL = "high"


def step_up(level):
    """``medium`` → ``high`` → ``xhigh`` → ``xhigh``."""
    return LEVELS[min(LEVELS.index(level) + 1, len(LEVELS) - 1)]


def _expected_phases(plan_text, phase):
    try:
        numbers = [entry["number"] for entry in parse.parse_phases(plan_text)]
    except parse._PhasesMalformed:
        numbers = []
    return numbers or [phase]


def _declared(plan_text, phase, final, notices):
    levels = parse.parse_review_levels(plan_text)
    for line in levels["unparsed"]:
        notices.append("`## Review level` line not read (its phase reviews at `high`): %s" % line)
    for number in levels["duplicated"]:
        notices.append("`## Review level` names phase %d twice (it reviews at `high`)" % number)
    numbers = _expected_phases(plan_text, phase) if final else [phase]
    best, source = None, None
    for number in numbers:
        entry = levels["levels"].get(number)
        level, from_plan = (entry["level"], True) if entry else (DEFAULT_LEVEL, False)
        if best is None or LEVELS.index(level) > LEVELS.index(best) or (level == best and from_plan):
            best, source = level, "plan" if from_plan else "default"
    return best, source


def _first_and_more(items):
    return "%s%s" % (items[0], " (+%d more)" % (len(items) - 1) if len(items) > 1 else "")


def _why(phase, final, declared_level, declared_source, sensitive, out_of_plan, level):
    if final:
        head = "final pass: highest declared level `%s`" % declared_level
    elif declared_source == "plan":
        head = "plan declares `%s` for phase %d" % (declared_level, phase)
    else:
        head = "no valid `## Review level` line for phase %d: `%s` by default" % (phase, declared_level)
    if declared_source == "default" and final:
        head += " (a phase without a valid line counts as `high`)"
    signals = []
    if sensitive["error"] is not None:
        signals.append("sensitive-path read failed (%s)" % sensitive["error"])
    if sensitive["matches"]:
        signals.append(
            "sensitive path %s"
            % _first_and_more(["`%s` matched `%s`" % (m["pattern"], m["path"]) for m in sensitive["matches"]])
        )
    if out_of_plan:
        signals.append("outside the plan: %s" % _first_and_more(["`%s`" % path for path in out_of_plan]))
    if not signals:
        return head
    return "%s; raised to `%s`: %s" % (head, level, "; ".join(signals))


def build_assess(root, plan_text, phase, final, base_ref=None, since=None):
    """``(payload, notices, None)`` — the level for one loop pass. Exactly one of ``base_ref`` (the
    cumulative range against ``origin/<base_ref>``, the merge gate's own call) or ``since`` (a phase
    delta from a local commit, no fetch) is given."""
    notices = []
    declared_level, declared_source = _declared(plan_text, phase, final, notices)
    fact = sensitive_paths.assess(root, base_ref=base_ref, since=since)
    changed = fact["paths"]
    spans = parse.planned_paths(plan_text)
    out_of_plan = [
        path for path in changed if not any(sensitive_paths.pattern_matches(span, path) for span in spans)
    ]
    sensitive = {
        "present": fact["present"],
        "source": fact["source"],
        "patterns": fact["patterns"],
        "matches": fact["matches"],
        "error": fact["error"],
    }
    raised = bool(sensitive["matches"]) or sensitive["error"] is not None or bool(out_of_plan)
    level = step_up(declared_level) if raised else declared_level
    payload = {
        "phase": phase,
        "final": final,
        "declared_level": declared_level,
        "declared_source": declared_source,
        "range": {
            "kind": "since" if since is not None else "base-ref",
            "ref": since if since is not None else base_ref,
            "base": fact["base"],
        },
        "changed_paths": changed,
        "sensitive": sensitive,
        "out_of_plan": out_of_plan,
        "raised": raised,
        "level": level,
        "why": _why(phase, final, declared_level, declared_source, sensitive, out_of_plan, level),
    }
    return payload, notices, None


def _read_or_die(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("BLAST_RADIUS_PLAN_UNREADABLE: %s: %s\n" % (path, exc))
        sys.exit(EXIT_USAGE_ERROR)


def main(argv):
    parser = argparse.ArgumentParser(prog="blast_radius.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="subcommand")
    p_assess = sub.add_parser("assess")
    p_assess.add_argument("root")
    p_assess.add_argument("--plan", required=True, help="the whole assembled plan (facts.distiller_bundle.plan_marker_path)")
    p_assess.add_argument("--phase", required=True, type=int)
    p_assess.add_argument("--final", action="store_true")
    span = p_assess.add_mutually_exclusive_group(required=True)
    span.add_argument("--base-ref", help="the cumulative range: HEAD against origin/<branch>")
    span.add_argument("--since", help="a phase delta: HEAD against this local commit")
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand != "assess":
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    payload, notices, _ = build_assess(
        args.root,
        _read_or_die(args.plan),
        args.phase,
        args.final,
        base_ref=args.base_ref,
        since=args.since,
    )
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
