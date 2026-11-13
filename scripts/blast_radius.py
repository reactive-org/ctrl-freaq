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
starts from the highest level across every phase. Three signals raise it, one step in total however many
fire, capped at ``xhigh`` — never ``max``, which stays the operator's own ``code-review`` choice: a sensitive
match, a failed sensitive read, and the blast-radius judgment's ``raise`` (``--judgment``, its verdict file
scanned by :func:`parse.scan_blast_judgment`; a verdict it cannot read counts as a raise). A ``hold`` changes
nothing: no verdict removes a level or a pause the fact signals add. A line or file count sets nothing.

**Out of plan is a record, never a raise (#218).** A changed file no planned span matches is reported in
``out_of_plan``, which the resolver's ``## Out-of-plan changes`` and the evaluator's S4.6 out-of-plan threads
read; it moves neither ``raised``, ``level`` nor ``why``.

**Rigor (#198).** ``--rigor <level>`` (``facts.rigor.level``, default ``full``) sets the rules above
for one pass. ``full`` is every rule as stated. ``medium`` counts a phase with no valid line (and a run with
no plan) as ``medium`` and ignores the judgment, so it has no raise of its own. ``light`` reviews at
``low``, a script-issued level outside the plan's grammar, and nothing raises it. A sensitive match (a
change to the ``<!-- ctrl-freaq-rigor -->`` block is one, ``sensitive_paths.match``) or a failed sensitive
read makes the pass run on ``full`` rules whatever its level: the payload's ``full_rules`` is true and its
``effective_rigor`` is ``full``.

Always an ``ok`` envelope when it runs (architecture.md §3): no decision code, since every unreadable
input it reads past has a known strict value. Exit 2 on a usage error (a missing or unreadable plan or
``--judgment`` file, an unknown flag, neither or both range flags). Spawns only ``git``, through :mod:`pipelib.process`, inside :mod:`sensitive_paths`.
"""

import argparse
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse  # noqa: E402
import rigor  # noqa: E402
import sensitive_paths  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402

# The ladder a level is read on. `low` (#198) is script-issued only — `light`'s fixed level — so
# `parse.REVIEW_LEVELS`, the plan's grammar, stays without it and a plan's `low` line stays unparsed.
LEVELS = ("low",) + parse.REVIEW_LEVELS
# The level a phase gets when the plan gives it no valid line under `full` — cautious, like an absent
# `checkpoint:` meaning `pause`. A lower rigor sets its own (`rigor.STEPS[<level>]["unplanned_level"]`).
DEFAULT_LEVEL = rigor.STEPS["full"]["unplanned_level"]


def step_up(level):
    """One step up the ladder, capped at ``xhigh``: ``medium`` → ``high`` → ``xhigh`` → ``xhigh``. ``low`` is
    never stepped from: it is ``light``'s fixed level, which no signal raises."""
    return LEVELS[min(LEVELS.index(level) + 1, len(LEVELS) - 1)]


def _declared(plan_text, phase, final, notices, default=DEFAULT_LEVEL):
    if plan_text is None:
        return default, "default"
    levels = parse.parse_review_levels(plan_text)
    for line in levels["unparsed"]:
        notices.append("`## Review level` line not read (its phase reviews at `%s`): %s" % (default, line))
    for number in levels["duplicated"]:
        notices.append("`## Review level` names phase %d twice (it reviews at `%s`)" % (number, default))
    if not final:
        numbers = [phase]
    else:
        try:
            numbers = parse.review_level_phases(parse.parse_phases(plan_text))
        except parse._PhasesMalformed:
            # An unknown phase set: every phase counts as undeclared (`default`), and a declared level
            # above it still wins. Unreachable from a resolver session (prep gates PHASES_MALFORMED).
            notices.append(
                "`## Phases` not read: the final pass counts every phase as undeclared (`%s`) unless"
                " a declared level is higher" % default
            )
            numbers = sorted(levels["levels"]) + [None]
    best, source = None, None
    for number in numbers:
        entry = levels["levels"].get(number)
        level, from_plan = (entry["level"], True) if entry else (default, False)
        if best is None or LEVELS.index(level) > LEVELS.index(best) or (level == best and from_plan):
            best, source = level, "plan" if from_plan else "default"
    return best, source


def _first_and_more(items):
    return "%s%s" % (items[0], " (+%d more)" % (len(items) - 1) if len(items) > 1 else "")


def _why(plan_text, phase, final, declared_level, declared_source, sensitive, judgment, level,
         default=DEFAULT_LEVEL, effective_rigor="full"):
    if effective_rigor != "full" and rigor.STEPS[effective_rigor]["fixed_level"] is not None:
        return "rigor `%s`: the level is `%s`, and no signal raises it" % (effective_rigor, level)
    if plan_text is None:
        head = "no plan: `%s` by default" % declared_level
    elif final:
        head = "final pass: highest declared level `%s`" % declared_level
    elif declared_source == "plan":
        head = "plan declares `%s` for phase %d" % (declared_level, phase)
    else:
        head = "no valid `## Review level` line for phase %d: `%s` by default" % (phase, declared_level)
    if declared_source == "default" and final and plan_text is not None:
        head += " (a phase without a valid line counts as `%s`)" % default
    signals = []
    if sensitive["error"] is not None:
        signals.append("sensitive-path read failed (%s)" % sensitive["error"])
    if sensitive["matches"]:
        signals.append(
            "sensitive path %s"
            % _first_and_more(["`%s` matched `%s`" % (m["pattern"], m["path"]) for m in sensitive["matches"]])
        )
    if judgment is not None and judgment["verdict"] is None:
        signals.append("judgment not read (counts as a raise)")
    elif judgment is not None and judgment["judged_raise"]:
        sites = ["%s at `%s`" % (s["dimension"], s["site"]) for s in judgment["signals"]]
        signals.append("judged blast radius: %s" % (_first_and_more(sites) if sites else "no site cited"))
    if effective_rigor != "full":
        head = "rigor `%s`: %s" % (effective_rigor, head)
    if not signals:
        return head
    return "%s; raised to `%s`: %s" % (head, level, "; ".join(signals))


def build_assess(root, plan_text, phase, final, base_ref=None, since=None, records=(), judgment=None,
                 rigor_level="full"):
    """``(payload, notices, None)`` — the level for one loop pass. Exactly one of ``base_ref`` (the
    cumulative range against ``origin/<base_ref>``, the merge gate's own call) or ``since`` (a phase
    delta from a local commit, no fetch) is given. ``plan_text`` ``None`` is a run with no plan (a
    trivial fix, or the operator's `proceed without a plan`): the level is the rigor's undeclared level (``high``
    under ``full``) and only the sensitive-path signal and the judgment can raise it. ``records`` are legacy
    shipped-phase records' texts (`skills/_shared/plan-shipped-phases.md`): their spans are planned
    too, since a pre-4.35.0 revise moved shipped phases' `## Changes` / `## Test plan` there.
    ``judgment`` is the blast-radius judgment's verdict text (#128), or ``None`` when the pass dispatched
    none (no ``--judgment``: the payload's ``judgment`` is ``None`` and nothing else changes). Its
    ``raise``, or a verdict that does not parse, joins the sensitive signals' OR — one step in total.
    ``out_of_plan`` (the changed files no planned span matches, ``[]`` with no plan) is a record (#218): the
    resolver's ``## Out-of-plan changes`` and the evaluator's out-of-plan threads read it, and it raises nothing.
    The payload's ``planned_spans`` is the span list ``out_of_plan`` was computed from (``None`` with no plan),
    reported for `review_threads.py`'s thread-side narrowing (#163); it changes nothing here.

    ``rigor_level`` is the pass's rigor (#198, ``facts.rigor.level``; ``full`` when not given, today's
    rules). ``full_rules`` is true when the sensitive-path signal fired — a match (a rigor-block change
    included) or a failed read — and the pass then runs on ``full`` rules whatever its level:
    ``effective_rigor`` is ``full``. Otherwise ``effective_rigor`` is ``rigor_level``, which sets the
    rules: under ``full``, today's (an undeclared phase is ``high``); under ``medium``, an undeclared phase
    or a no-plan run is ``medium`` and the judgment is ignored, so nothing raises; under
    ``light``, the level is ``low`` and nothing raises. ``steps`` is a copy of ``rigor.STEPS[effective_rigor]``,
    the step values every rule inside the pass reads."""
    if rigor_level not in rigor.STEPS:
        raise ValueError("rigor_level must be one of %s, got %r" % (", ".join(rigor.LEVELS), rigor_level))
    notices = []
    fact = sensitive_paths.assess(root, base_ref=base_ref, since=since)
    full_rules = bool(fact["matches"]) or fact["error"] is not None
    effective_rigor = "full" if full_rules else rigor_level
    steps = rigor.STEPS[effective_rigor]
    default = steps["unplanned_level"]
    if not steps["judgment"]:
        judgment = None
    if plan_text is None and steps["fixed_level"] is None:
        notices.append(
            "no plan: the level is `%s`, and only a sensitive path can raise it" % default
            if judgment is None
            else "no plan: the level is `%s`, and only a sensitive path or the judgment can raise it" % default
        )
    # Under a fixed level (`light`) the plan's lines set nothing, so their notices would misstate what was read.
    declared_level, declared_source = _declared(
        plan_text, phase, final, notices if steps["fixed_level"] is None else [], default=default
    )
    changed = fact["paths"]
    spans = None
    if plan_text is not None:
        spans = parse.planned_paths(plan_text)
        for record in records:
            spans += [span for span in parse.planned_paths(record) if span not in spans]
    out_of_plan = [
        path for path in changed if not any(sensitive_paths.pattern_matches(span, path) for span in spans)
    ] if spans is not None else []
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
    # `out_of_plan` is a record, never a raise (#218).
    raised = (
        bool(sensitive["matches"])
        or sensitive["error"] is not None
        or (judged is not None and judged["judged_raise"])
    )
    if steps["fixed_level"] is not None:
        # `light` (#198): a script-issued fixed level, which no signal raises — a sensitive signal would
        # have made the pass `full_rules` and never reaches here.
        declared_level, declared_source, raised = steps["fixed_level"], "rigor", False
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
        "planned_spans": spans,
        "sensitive": sensitive,
        "out_of_plan": out_of_plan,
        "judgment": judged,
        "raised": raised,
        "level": level,
        "rigor": rigor_level,
        "full_rules": full_rules,
        "effective_rigor": effective_rigor,
        # The pass's step values (#198): the rules inside the pass read these, so a pass on `full` rules runs
        # `full`'s row whatever `facts.rigor` says.
        "steps": copy.deepcopy(steps),
        "why": _why(plan_text, phase, final, declared_level, declared_source, sensitive, judged, level,
                    default=default, effective_rigor=effective_rigor),
    }
    return payload, notices, None


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
        "--rigor",
        choices=rigor.LEVELS,
        default="full",
        help="the pass's rigor level (facts.rigor.level); default `full`, the rules before #198",
    )
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
        _read_or_die(args.plan[0]) if args.plan else None,
        args.phase,
        args.final,
        base_ref=args.base_ref,
        since=args.since,
        records=[_read_or_die(path) for path in args.plan[1:]],
        judgment=_read_or_die(args.judgment, "BLAST_RADIUS_JUDGMENT_UNREADABLE") if args.judgment is not None else None,
        rigor_level=args.rigor,
    )
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
