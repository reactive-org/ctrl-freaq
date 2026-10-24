"""Import-only core of the `review-report` tool (#98): the review-loop record read across a repo's PRs.

skills/_shared/review-loop-record.md owns the shapes read here. `collect` lists a repo's PRs, reads each
one's loop comments (`parse.scan_review_loop`), `## Polish` ledger (`parse.scan_polish`) and
`## Security review` record (`parse.scan_security`), and turns them into per-finding records, each
carrying the outcome the contract's "Outcomes" table gives it. `summarize` tallies them, and the rounds,
per **basis** (`record` and `legacy`, never pooled); `class_counts` counts the record basis's class keys.

Facts only. Which bucket, tier and class key a finding has was the model's judgment when the loop
classified it; whether two class keys are one finding coming back is #99's (CLAUDE.md "facts by script,
meaning by model"). This module writes nothing, and every `gh` call goes through `pipelib.process.run`.

Two consumers compose it in-process: `scripts/prep_review_report.py` (the tool's prep, which emits the
envelope) and #99's `setup` proposals. A failed PR list raises `PrListFailed` and an authentication
failure raises `AuthRequired`, so each consumer maps them to its own exit contract.
"""

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse  # noqa: E402
from pipelib import process  # noqa: E402
from pipelib.decisions import REVIEW_COMMENTS_TRUNCATED, REVIEW_COMMENTS_UNAVAILABLE  # noqa: E402

OUTCOMES = ("fixed", "wrong", "intentional", "did-not-matter")
EXCLUDED = ("deferred", "decision", "pending", "reclassified", "unlinked", "unmapped")
BASES = ("record", "legacy")
# The contract's threshold for a phase that needed many review runs (#98 DoD 3).
FIVE_PLUS = 5

# The `gh pr list` fields every PR dict carries (`records_for_pr`'s `pr` argument).
PR_FIELDS = "number,title,url,state,mergedAt,body"
# Comments are read in one `gh api graphql` query per chunk of this many PRs, each PR's first
# `COMMENTS_PER_PR` comments with its `totalCount`, never one `gh pr view` each.
COMMENT_QUERY_CHUNK = 20
COMMENTS_PER_PR = 100
_COMMENT_NODE = (
    "p%d: pullRequest(number: %d) { comments(first: " + str(COMMENTS_PER_PR) + ") "
    "{ totalCount nodes { body } } }"
)
_SETTLED_ACTION_OUTCOMES = {"refuted": "wrong", "plan-settled": "intentional", "deferred-by-plan": "intentional"}
_NO_SHOWN_COST_NOTE = "no shown cost:"
_RECLASSIFIED_NOTE = "reclassified: defect"


class AuthRequired(RuntimeError):
    """`gh` is not authenticated (the pipelib runner's classification). Carries its stderr."""


class PrListFailed(RuntimeError):
    """The PR list could not be read. Carries its stderr: nothing can be reported without it."""


# ---------------------------------------------------------------------------
# Reading GitHub
# ---------------------------------------------------------------------------


def list_prs(repo, state="merged", limit=100, cwd=None, env=None):
    """The repo's PRs, newest first, as `gh pr list --json PR_FIELDS` dicts. Raises `AuthRequired` or
    `PrListFailed`."""
    result = process.run(
        ["gh", "pr", "list", "--repo", repo, "--state", state, "--limit", str(limit), "--json", PR_FIELDS],
        cwd=cwd,
        env=env,
    )
    if result.auth_required:
        raise AuthRequired(result.stderr)
    try:
        prs = json.loads(result.stdout) if result.returncode == 0 else None
    except ValueError:
        prs = None
    if not isinstance(prs, list):
        raise PrListFailed(result.stderr or "gh pr list returned no PR list")
    return [pr for pr in prs if isinstance(pr, dict) and isinstance(pr.get("number"), int)]


def _comment_query(repo, numbers):
    owner, _, name = repo.partition("/")
    return "query { repository(owner: %s, name: %s) { %s } }" % (
        json.dumps(owner), json.dumps(name), " ".join(_COMMENT_NODE % (n, n) for n in numbers)
    )


def read_comments(repo, numbers, cwd=None, env=None):
    """`({number: [comment body, …] in comment order}, notices)`. A PR whose comments could not be read
    is absent from the map and named by a `REVIEW_COMMENTS_UNAVAILABLE` notice; one with more comments
    than were read is present, truncated, and named by `REVIEW_COMMENTS_TRUNCATED`. GraphQL reports a
    bad node beside the data that did resolve and `gh` then exits non-zero, so a non-zero exit still
    reads whatever data the response carries. Raises `AuthRequired`."""
    comments, unavailable, truncated = {}, [], []
    for i in range(0, len(numbers), COMMENT_QUERY_CHUNK):
        chunk = numbers[i:i + COMMENT_QUERY_CHUNK]
        result = process.run(
            ["gh", "api", "graphql", "-f", "query=" + _comment_query(repo, chunk)], cwd=cwd, env=env
        )
        if result.auth_required:
            raise AuthRequired(result.stderr)
        try:
            repository = ((json.loads(result.stdout or "null") or {}).get("data") or {}).get("repository") or {}
        except (ValueError, AttributeError):
            repository = {}
        for number in chunk:
            node = repository.get("p%d" % number) if isinstance(repository, dict) else None
            block = node.get("comments") if isinstance(node, dict) else None
            if not isinstance(block, dict) or not isinstance(block.get("nodes"), list):
                unavailable.append(number)
                continue
            bodies = [c.get("body") or "" for c in block["nodes"] if isinstance(c, dict)]
            comments[number] = bodies
            total = block.get("totalCount")
            if isinstance(total, int) and total > len(bodies):
                truncated.append((number, total, len(bodies)))
    notices = []
    if unavailable:
        notices.append(
            "%s: the comments of %s could not be read; the report omits their loop comments"
            % (REVIEW_COMMENTS_UNAVAILABLE, ", ".join("#%d" % n for n in unavailable))
        )
    for number, total, read in truncated:
        notices.append(
            "%s: #%d has %d comments and the first %d were read; later loop comments are missing"
            % (REVIEW_COMMENTS_TRUNCATED, number, total, read)
        )
    return comments, notices


# ---------------------------------------------------------------------------
# Per-PR records
# ---------------------------------------------------------------------------


def outcome_of(action, bucket, ledger_entry):
    """The contract's "Outcomes" table: action, then bucket, then (ledgered) the ledger entry; the first
    matching rule wins. `ledger_entry` is the `parse.scan_polish` entry the action's id names, or None."""
    if action == "fixed":
        return "fixed"
    if action == "deferred":
        return "deferred"
    if action == "decision":
        return "decision"
    if action == "ledger":
        if ledger_entry is None:
            return "unlinked"
        disposition, note = ledger_entry["disposition"], ledger_entry.get("note") or ""
        if disposition == "applied":
            return "fixed"
        if disposition == "drop":
            return "reclassified" if note.startswith(_RECLASSIFIED_NOTE) else "did-not-matter"
        if disposition == "file":
            return "deferred"
        return "pending"
    if action == "settled" and bucket in _SETTLED_ACTION_OUTCOMES:
        return _SETTLED_ACTION_OUTCOMES[bucket]
    return "unmapped"


def _record(pr, fmt, basis, **fields):
    record = dict.fromkeys(
        ("phase", "id", "bucket", "provenance", "tier", "action", "ledger_id", "source", "class_key",
         "site", "check", "item")
    )
    record.update(fields)
    record.update({"pr": pr, "format": fmt, "basis": basis})
    return record


def _anchor_path(anchor):
    """The path part of a ledger anchor (`<path> / <symbol>`), never the symbol."""
    head = (anchor or "").split(" / ", 1)[0].strip()
    return head.split()[0] if head else None


def records_for_pr(pr, comments):
    """`(records, unparsed, rounds)` for one PR dict (`PR_FIELDS`) and its comment bodies in order.

    Record-format comments give one record per finding line (an id repeated across the PR's comments
    is unparsed, the first kept) and one rounds element per `Review runs:` line; their `Settled` and
    `Defect fixes` blocks restate those lines and are not read. Legacy comments give their settled
    entries (deduplicated on bucket and item), defect-fix sites and per-phase round counts, summed
    across the PR's legacy comments. Ledger entries no record line names become `ledger` records.
    """
    number = pr.get("number")
    polish = parse.scan_polish(pr.get("body") or "")
    ledger = {entry["id"]: entry for entry in polish["entries"]}
    records, unparsed, rounds = [], [], []
    record_phases, seen_ids, legacy_settled, legacy_rounds = set(), set(), set(), {}

    for body in comments or []:
        scan = parse.scan_review_loop(body)
        if scan["format"] == "record":
            unparsed.extend({"pr": number, "source": "loop-comment", "line": line} for line in scan["unparsed"])
            record_phases.update(h["phase"] for h in scan["phase_headings"] if h["phase"] is not None)
            for finding in scan["findings"]:
                record_phases.add(finding["phase"])
                if finding["id"] in seen_ids:
                    unparsed.append({"pr": number, "source": "loop-comment", "line": finding["id"] + " (repeated)"})
                    continue
                seen_ids.add(finding["id"])
                fields = {k: finding[k] for k in finding if k != "seq"}
                fields["outcome"] = outcome_of(
                    finding["action"], finding["bucket"], ledger.get(finding["ledger_id"])
                )
                records.append(_record(number, "record", "record", **fields))
            for run in scan["runs"]:
                record_phases.add(run["phase"])
                rounds.append({"pr": number, "phase": run["phase"], "format": "record",
                               "count": run["count"], "exit": run["exit"]})
        elif scan["format"] == "legacy":
            for heading in scan["phase_headings"]:
                legacy_rounds[heading["phase"]] = legacy_rounds.get(heading["phase"], 0) + heading["rounds"]
            for entry in scan["settled"]:
                key = (entry["bucket"], entry["item"])
                if key in legacy_settled:
                    continue
                legacy_settled.add(key)
                bucket = entry["bucket"] if entry["bucket"] in parse.REVIEW_BUCKETS else None
                records.append(_record(
                    number, "legacy", "legacy", bucket=bucket, action="settled", check=entry["check"],
                    item=entry["item"], outcome=outcome_of("settled", bucket, None),
                ))
            for fix in scan["defect_fixes"]:
                for site in fix["sites"]:
                    records.append(_record(
                        number, "legacy", "legacy", phase=fix["phase"], bucket="addressable", tier="defect",
                        action="fixed", site=site.split(":", 1)[0].strip() or None, item=site,
                        outcome="fixed",
                    ))

    for phase, count in legacy_rounds.items():
        rounds.append({"pr": number, "phase": phase, "format": "legacy", "count": count, "exit": None})

    named = {r["ledger_id"] for r in records if r["format"] == "record" and r["ledger_id"]}
    for entry in polish["entries"]:
        if entry["id"] in named:
            continue
        note = entry.get("note") or ""
        records.append(_record(
            number, "ledger", "record" if entry["phase"] in record_phases else "legacy",
            phase=entry["phase"], id=entry["id"], action="ledger", ledger_id=entry["id"],
            bucket="no-shown-cost" if note.startswith(_NO_SHOWN_COST_NOTE) else None,
            site=_anchor_path(entry["anchor"]), item=entry["item"],
            outcome=outcome_of("ledger", None, entry),
        ))
    unparsed.extend({"pr": number, "source": "ledger", "line": line} for line in polish["unparsed"])
    return records, unparsed, rounds


# ---------------------------------------------------------------------------
# Across PRs
# ---------------------------------------------------------------------------


def _security_tally(prs):
    tally = {"prs_with_record": 0, "entries": dict.fromkeys(parse.SECURITY_DISPOSITIONS, 0), "unparsed": 0}
    for pr in prs:
        scan = parse.scan_security(pr.get("body") or "")
        if not scan["present"]:
            continue
        tally["prs_with_record"] += 1
        for entry in scan["entries"]:
            tally["entries"][entry["disposition"]] += 1
        tally["unparsed"] += len(scan["unparsed"])
    return tally


def collect(repo, state="merged", limit=100, cwd=None, env=None):
    """Read `repo`'s PRs and return `{"records", "unparsed", "rounds", "security", "coverage",
    "prs_scanned", "notices"}`. Raises `AuthRequired` or `PrListFailed` (the list only; a comments
    chunk that fails is a notice)."""
    prs = list_prs(repo, state=state, limit=limit, cwd=cwd, env=env)
    comments, notices = read_comments(repo, [pr["number"] for pr in prs], cwd=cwd, env=env)
    records, unparsed, rounds = [], [], []
    coverage = {"prs_with_record": 0, "prs_with_legacy": 0, "prs_with_ledger": 0}
    for pr in prs:
        pr_records, pr_unparsed, pr_rounds = records_for_pr(pr, comments.get(pr["number"], []))
        records.extend(pr_records)
        unparsed.extend(pr_unparsed)
        rounds.extend(pr_rounds)
        formats = {r["format"] for r in pr_records} | {r["format"] for r in pr_rounds}
        coverage["prs_with_record"] += "record" in formats
        coverage["prs_with_legacy"] += "legacy" in formats
        coverage["prs_with_ledger"] += parse.scan_polish(pr.get("body") or "")["present"]
    return {
        "records": records,
        "unparsed": unparsed,
        "rounds": rounds,
        "security": _security_tally(prs),
        "coverage": coverage,
        "prs_scanned": len(prs),
        "notices": notices,
    }


def _counts(keys, values):
    counts = dict.fromkeys(keys, 0)
    for value in values:
        key = "none" if value is None else value
        if key in counts:
            counts[key] += 1
    return counts


def _round_figures(counts):
    if not counts:
        return {"median": None, "mean": None, "max": None, "histogram": {}}
    histogram = {}
    for count in sorted(counts):
        histogram[str(count)] = histogram.get(str(count), 0) + 1
    return {
        "median": statistics.median(counts),
        "mean": round(statistics.mean(counts), 2),
        "max": max(counts),
        "histogram": histogram,
    }


def _rounds_tally(rounds, basis):
    pairs = {}
    for element in rounds:
        if element["format"] != basis:
            continue
        pair = pairs.setdefault((element["pr"], element["phase"]), {"count": 0, "exit": None})
        pair["count"] += element["count"]
        pair["exit"] = element["exit"]
    if basis == "record":
        measured = [p["count"] for p in pairs.values() if p["exit"] == "settled"]
        figures = dict(_round_figures(measured), phases_passed=len(measured))
    else:
        figures = dict(_round_figures([p["count"] for p in pairs.values()]), phases_passed=None)
    heavy = sum(1 for p in pairs.values() if p["count"] >= FIVE_PLUS)
    five_plus = {
        "phases": len(pairs),
        "count": heavy,
        "share": round(heavy / len(pairs), 3) if pairs else None,
    }
    return figures, five_plus


def summarize(records, rounds):
    """`{"record": tally, "legacy": tally}` — never pooled. A tally is `{"outcomes", "excluded",
    "by_bucket", "by_source", "checks", "rounds", "five_plus"}`; every map holds each member of its
    closed set (and `"none"`, for the three record fields that may be absent) at 0 or more."""
    summary = {}
    for basis in BASES:
        mine = [r for r in records if r["basis"] == basis]
        figures, five_plus = _rounds_tally(rounds, basis)
        summary[basis] = {
            "outcomes": _counts(OUTCOMES, (r["outcome"] for r in mine)),
            "excluded": _counts(EXCLUDED, (r["outcome"] for r in mine)),
            "by_bucket": _counts(parse.REVIEW_BUCKETS + ("none",), (r["bucket"] for r in mine)),
            "by_source": _counts(parse.REVIEW_SOURCES + ("none",), (r["source"] for r in mine)),
            "checks": _counts(parse.REVIEW_CHECKS + ("none",), (r["check"] for r in mine)),
            "rounds": figures,
            "five_plus": five_plus,
        }
    return summary


def class_counts(records):
    """`[{"class_key", "count", "outcomes", "prs"}]` over the record basis's finding lines, most
    frequent first. Exact keys only: whether two keys are one class is #99's judgment."""
    by_key = {}
    for record in records:
        if record["format"] != "record" or not record["class_key"]:
            continue
        entry = by_key.setdefault(record["class_key"], {"class_key": record["class_key"], "count": 0,
                                                        "outcomes": {}, "prs": set()})
        entry["count"] += 1
        entry["outcomes"][record["outcome"]] = entry["outcomes"].get(record["outcome"], 0) + 1
        entry["prs"].add(record["pr"])
    ordered = sorted(by_key.values(), key=lambda e: (-e["count"], e["class_key"]))
    return [dict(e, prs=sorted(e["prs"])) for e in ordered]
