#!/usr/bin/env python3
"""blast_radius.py — the resolver's review level for one loop pass (#97): the level the plan declares
for the phase, raised one step when the diff shows the plan underjudged its blast radius.

**Why a script, and why not prep.** The level is arithmetic over facts — the plan's `## Review level`
line, the declared sensitive paths, the files the plan names — so it is a script's (CLAUDE.md "facts
by script, meaning by model"). Prep runs before the phase's diff exists, so the resolver calls this
mid-session instead: at S5.1 loop entry, over the pass's review range
(`skills/resolver/references/review-level.md`). The blast-radius judgment is the planner's before the
diff exists and the judgment sub-agent's after it; this script combines what they wrote with what the
diff touched.

**One read with the merge gate.** The sensitive-path signal is :func:`sensitive_paths.assess`, the
exact function ``prep_evaluator`` calls for the merge gate, so the resolver and the gate cannot
disagree about one PR. A failed read (``error``) raises the level, as it forces ``ask`` there: the
stricter outcome.

**Rules.** The plan may write ``medium``, ``high`` or ``xhigh`` per phase; a phase with no valid line
(absent, unparsed — ``low`` and ``max`` included — or duplicated) counts as ``high``. A final pass
starts from the highest level across every phase. A sensitive match, a failed sensitive read, a
changed file no planned span matches, or the blast-radius judgment's ``raise`` (``--judgment``, its
verdict file scanned by :func:`parse.scan_blast_judgment`; a verdict it cannot read counts as a raise)
raises one step in total, however many fire, capped at ``xhigh`` — never ``max``, which stays the
operator's own ``code-review`` choice. On the epic-integration pass the epic plan names no story's files
(an epic plan is written at coordination altitude), so ``--delivered`` — the files the PRs merged into
the epic branch changed, ``facts.epic.delivered`` (#149) — counts as planned too. A file in the range no
merged PR changed (a commit made directly on the epic branch, a sync merge's conflict resolution) still
raises: no PR's review loop read it. Only the epic route's prep sets the fact, so every other route's
unplanned-file signal is unchanged. A ``hold`` changes nothing: no verdict removes a level or a pause
the fact signals add. A line or file count sets nothing.

**The look's clock** (phase 2). When a pause raises a look (`skills/resolver/references/
phase-continuation.md` §4), ``look start`` records when its card first rendered and ``look check`` reports
whether the operator's answer came within :data:`LOOK_WINDOW_SECONDS` of it. Elapsed time is a fact, so
the model never compares two clock readings itself. A missing or unreadable start file is outside the
window, which is the safe outcome: the session ends and its printed handoff stands.

Always an ``ok`` envelope when it runs (architecture.md §3): no decision code, since every unreadable
input it reads past has a known strict value. Exit 2 on a usage error (a missing or unreadable plan or
``--judgment`` file, a ``--delivered`` file that is missing, unreadable, not JSON or whose ``paths`` is
not a list of strings, neither or both range flags). ``look start`` into an unwritable ``--scratch`` fails with a traceback, no envelope. Spawns only ``git``, through :mod:`pipelib.process`, inside :mod:`sensitive_paths`.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse  # noqa: E402
import sensitive_paths  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402

LEVELS = parse.REVIEW_LEVELS
# A look's in-session window: 55 minutes, inside the subscription prompt cache's hour, so a "Continue
# here" answer after it — which would reload the whole conversation uncached — ends the session instead.
LOOK_WINDOW_SECONDS = 3300
LOOK_STARTED_FILE = "look-started"
# The level a phase gets when the plan gives it no valid line — cautious, like an absent
# `checkpoint:` meaning `pause`.
DEFAULT_LEVEL = "high"


def step_up(level):
    """``medium`` → ``high`` → ``xhigh`` → ``xhigh``."""
    return LEVELS[min(LEVELS.index(level) + 1, len(LEVELS) - 1)]


def _declared(plan_text, phase, final, notices):
    if plan_text is None:
        return DEFAULT_LEVEL, "default"
    levels = parse.parse_review_levels(plan_text)
    for line in levels["unparsed"]:
        notices.append("`## Review level` line not read (its phase reviews at `high`): %s" % line)
    for number in levels["duplicated"]:
        notices.append("`## Review level` names phase %d twice (it reviews at `high`)" % number)
    if not final:
        numbers = [phase]
    else:
        try:
            numbers = parse.review_level_phases(parse.parse_phases(plan_text))
        except parse._PhasesMalformed:
            # An unknown phase set: every phase counts as undeclared (`high`), and a declared level
            # above it still wins. Unreachable from a resolver session (prep gates PHASES_MALFORMED).
            notices.append(
                "`## Phases` not read: the final pass counts every phase as undeclared (`high`) unless"
                " a declared level is higher"
            )
            numbers = sorted(levels["levels"]) + [None]
    best, source = None, None
    for number in numbers:
        entry = levels["levels"].get(number)
        level, from_plan = (entry["level"], True) if entry else (DEFAULT_LEVEL, False)
        if best is None or LEVELS.index(level) > LEVELS.index(best) or (level == best and from_plan):
            best, source = level, "plan" if from_plan else "default"
    return best, source


def _first_and_more(items):
    return "%s%s" % (items[0], " (+%d more)" % (len(items) - 1) if len(items) > 1 else "")


def _read_delivered(path):
    """The ``paths`` list of a ``facts.epic.delivered`` file, or exit 2 with
    ``BLAST_RADIUS_DELIVERED_UNREADABLE:`` (a usage error, like an unreadable plan)."""
    text = _read_or_die(path, "BLAST_RADIUS_DELIVERED_UNREADABLE")
    try:
        paths = json.loads(text).get("paths")
    except (ValueError, AttributeError) as exc:
        paths, reason = None, "not a JSON object: %s" % exc
    else:
        reason = "`paths` is not a list of strings"
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        sys.stderr.write("BLAST_RADIUS_DELIVERED_UNREADABLE: %s: %s\n" % (path, reason))
        sys.exit(EXIT_USAGE_ERROR)
    return paths


def _why(plan_text, phase, final, declared_level, declared_source, sensitive, out_of_plan, judgment, level):
    if plan_text is None:
        head = "no plan: `%s` by default" % declared_level
    elif final:
        head = "final pass: highest declared level `%s`" % declared_level
    elif declared_source == "plan":
        head = "plan declares `%s` for phase %d" % (declared_level, phase)
    else:
        head = "no valid `## Review level` line for phase %d: `%s` by default" % (phase, declared_level)
    if declared_source == "default" and final and plan_text is not None:
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
    if judgment is not None and judgment["verdict"] is None:
        signals.append("judgment not read (counts as a raise)")
    elif judgment is not None and judgment["judged_raise"]:
        sites = ["%s at `%s`" % (s["dimension"], s["site"]) for s in judgment["signals"]]
        signals.append("judged blast radius: %s" % (_first_and_more(sites) if sites else "no site cited"))
    if not signals:
        return head
    return "%s; raised to `%s`: %s" % (head, level, "; ".join(signals))


def build_assess(root, plan_text, phase, final, base_ref=None, since=None, records=(), judgment=None, delivered=None):
    """``(payload, notices, None)`` — the level for one loop pass. Exactly one of ``base_ref`` (the
    cumulative range against ``origin/<base_ref>``, the merge gate's own call) or ``since`` (a phase
    delta from a local commit, no fetch) is given. ``plan_text`` ``None`` is a run with no plan (a
    trivial fix, or the operator's `proceed without a plan`): the level is ``high`` and only the
    sensitive-path signal can raise it — there is no plan to stray outside. ``records`` are legacy
    shipped-phase records' texts (`skills/_shared/plan-shipped-phases.md`): their spans are planned
    too, since a pre-4.35.0 revise moved shipped phases' `## Changes` / `## Test plan` there.
    ``judgment`` is the blast-radius judgment's verdict text (#128), or ``None`` when the pass dispatched
    none (no ``--judgment``: the payload's ``judgment`` is ``None`` and nothing else changes). Its
    ``raise``, or a verdict that does not parse, joins the fact signals' OR — one step in total.
    ``delivered`` is the epic-integration pass's delivered set (#149): repo-relative paths the PRs
    merged into the epic branch changed, or ``None`` on every other pass (the payload's ``delivered`` is
    then ``None``). A changed path in it is not out of plan. Membership is exact string equality, never
    :func:`sensitive_paths.pattern_matches`: a delivered path is a literal file name, and one containing
    ``*``, ``?`` or ``[`` must not act as a glob. It adds no signal and removes none but the unplanned
    file's, and is ignored on a run with no plan, which has no out-of-plan signal to cover."""
    notices = []
    if plan_text is None:
        notices.append(
            "no plan: the level is `high`, and only a sensitive path can raise it"
            if judgment is None
            else "no plan: the level is `high`, and only a sensitive path or the judgment can raise it"
        )
    declared_level, declared_source = _declared(plan_text, phase, final, notices)
    fact = sensitive_paths.assess(root, base_ref=base_ref, since=since)
    changed = fact["paths"]
    spans = None
    if plan_text is not None:
        spans = parse.planned_paths(plan_text)
        for record in records:
            spans += [span for span in parse.planned_paths(record) if span not in spans]
    unplanned = [
        path for path in changed if not any(sensitive_paths.pattern_matches(span, path) for span in spans)
    ] if spans is not None else []
    delivered_set = set(delivered) if delivered is not None and spans is not None else set()
    out_of_plan = [path for path in unplanned if path not in delivered_set]
    delivered_fact = None if delivered is None else {
        "count": len(set(delivered)),
        "covered": [path for path in unplanned if path in delivered_set],
    }
    sensitive = {
        "present": fact["present"],
        "source": fact["source"],
        "patterns": fact["patterns"],
        "matches": fact["matches"],
        "error": fact["error"],
    }
    judged = None
    if judgment is not None:
        scan = parse.scan_blast_judgment(judgment)
        judged = dict(scan, judged_raise=scan["verdict"] in ("raise", None))
        if scan["verdict"] is None:
            notices.append(
                "blast-radius judgment not read (it counts as a raise): no `## Judgment` section with one"
                " `verdict: raise|hold` line, or a `code: AMBIGUOUS` line in it"
            )
    raised = (
        bool(sensitive["matches"])
        or sensitive["error"] is not None
        or bool(out_of_plan)
        or (judged is not None and judged["judged_raise"])
    )
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
        "delivered": delivered_fact,
        "judgment": judged,
        "raised": raised,
        "level": level,
        "why": _why(plan_text, phase, final, declared_level, declared_source, sensitive, out_of_plan, judged, level),
    }
    return payload, notices, None


def look_start(scratch, now=None):
    """Record the look card's first render: ``<scratch>/look-started`` (epoch seconds). Returns
    ``{"started_at"}``. A "more" re-ask must not call it again — the window runs from the first render."""
    started = int(time.time() if now is None else now)
    path = Path(scratch)
    path.mkdir(parents=True, exist_ok=True)
    (path / LOOK_STARTED_FILE).write_text("%d\n" % started, encoding="utf-8")
    return {"started_at": started}


def look_check(scratch, now=None):
    """``{"started_at", "elapsed_seconds", "within_window"}`` against :data:`LOOK_WINDOW_SECONDS`
    (inclusive). A missing or unreadable start file reports ``started_at``/``elapsed_seconds`` ``None``
    and ``within_window: false``."""
    current = int(time.time() if now is None else now)
    try:
        started = int((Path(scratch) / LOOK_STARTED_FILE).read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError, ValueError):
        return {"started_at": None, "elapsed_seconds": None, "within_window": False}
    elapsed = current - started
    return {"started_at": started, "elapsed_seconds": elapsed, "within_window": 0 <= elapsed <= LOOK_WINDOW_SECONDS}


def _read_or_die(path, prefix="BLAST_RADIUS_PLAN_UNREADABLE"):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("%s: %s: %s\n" % (prefix, path, exc))
        sys.exit(EXIT_USAGE_ERROR)


def main(argv):
    parser = argparse.ArgumentParser(prog="blast_radius.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="subcommand")
    p_assess = sub.add_parser("assess")
    p_assess.add_argument("root")
    p_assess.add_argument(
        "--plan",
        action="append",
        default=[],
        help="the whole assembled plan (facts.distiller_bundle.plan_marker_path), then, repeated, the "
        "legacy shipped-phase records (plan_shipped_path); omit on a run with no plan",
    )
    p_assess.add_argument(
        "--judgment",
        help="the blast-radius judgment's verdict file (<facts.scratch>/blast-radius-judgment.md); omit when "
        "the pass dispatched none",
    )
    p_assess.add_argument(
        "--delivered",
        help="the epic-integration pass's delivered set (facts.epic.delivered.path); omit on every other pass",
    )
    p_assess.add_argument("--phase", required=True, type=int)
    p_assess.add_argument("--final", action="store_true")
    span = p_assess.add_mutually_exclusive_group(required=True)
    span.add_argument("--base-ref", help="the cumulative range: HEAD against origin/<branch>")
    span.add_argument("--since", help="a phase delta: HEAD against this local commit")
    p_look = sub.add_parser("look")
    p_look.add_argument("action", choices=("start", "check"))
    p_look.add_argument("--scratch", required=True, help="the run scratch dir (facts.scratch)")
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand == "look":
        emit_ok(payload=(look_start if args.action == "start" else look_check)(args.scratch))
        sys.exit(EXIT_OK)
    if args.subcommand != "assess":
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    payload, notices, _ = build_assess(
        args.root,
        _read_or_die(args.plan[0]) if args.plan else None,
        args.phase,
        args.final,
        base_ref=args.base_ref,
        since=args.since,
        records=[_read_or_die(path) for path in args.plan[1:]],
        judgment=_read_or_die(args.judgment, "BLAST_RADIUS_JUDGMENT_UNREADABLE") if args.judgment is not None else None,
        delivered=_read_delivered(args.delivered) if args.delivered is not None else None,
    )
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
