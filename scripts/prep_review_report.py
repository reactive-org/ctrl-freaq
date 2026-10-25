#!/usr/bin/env python3
"""prep_review_report.py — the `review-report` tool's prep, which is its whole action (#98).

    prep_review_report.py <owner/repo> [--state merged|all] [--limit N] [--scratch-dir PATH] [--cwd PATH]

Reads `<owner/repo>`'s PRs (merged by default, newest first, up to `--limit`) through the import-only
core `scripts/review_report.py`, and emits one envelope (architecture.md §3) whose payload is the report:

    repo, scratch,
    scope        {state, limit, prs_scanned}
    coverage     {prs_with_record, prs_with_legacy, prs_with_ledger}
    skipped      {comments, loop_comments, pr_bodies} — text an untrusted author wrote, left unread (SEC-1)
    summary      {record: <tally>, legacy: <tally>}   — review_report.summarize, never pooled
    class_counts [...]                                — review_report.class_counts (record basis)
    security     {prs_with_record, entries: {open, fixed, dismissed}, unparsed, other_sections}
    counts       {records: {record, legacy, none}, unparsed}       — `none`: a basis the scan could not know
    records      — spilled: records_bytes / records_mode / records_path (or inline `records`, JSON text)
    unparsed     — spilled the same way

Read-only: it writes nothing to GitHub and nothing to the repo, only its scratch dir
(`/tmp/gh-review-report-<repo-name>/` by default), where a large `records` or `unparsed` section spills.

Exit codes: 0 with an envelope (`ok`, or `needs_decision` `AUTH_REQUIRED`); 2 on a usage error; 1 with
no envelope when the PR list itself cannot be read (stderr carries `gh`'s error). A comments chunk that
fails is not fatal: its PRs are named in a `REVIEW_COMMENTS_UNAVAILABLE` notice.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import review_report  # noqa: E402
from pipelib.decisions import AUTH_REQUIRED, needs_decision  # noqa: E402
from pipelib.envelope import emit_needs_decision, emit_ok  # noqa: E402
from pipelib.spill import spill_bytes  # noqa: E402

STATES = ("merged", "all")
DEFAULT_LIMIT = 100


def default_scratch(repo):
    return "/tmp/gh-review-report-%s" % repo.split("/")[-1]


def build_report(repo, state="merged", limit=DEFAULT_LIMIT, scratch_dir=None, cwd=None, env=None):
    """`(payload, notices, decision)` — the testable core, as `build_*` is in every prep. `decision` is
    an `AUTH_REQUIRED` decision (payload None) when `gh` is not authenticated. Raises
    `review_report.PrListFailed` when the PR list cannot be read."""
    scratch_dir = scratch_dir or default_scratch(repo)
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)
    try:
        collected = review_report.collect(repo, state=state, limit=limit, cwd=cwd, env=env)
    except review_report.AuthRequired as exc:
        return None, [], needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": str(exc)},
            options=["run: gh auth login"],
        )
    payload = {
        "repo": repo,
        "scratch": scratch_dir,
        "scope": {"state": state, "limit": limit, "prs_scanned": collected["prs_scanned"]},
        "coverage": collected["coverage"],
        "skipped": collected["skipped"],
        "summary": review_report.summarize(collected["records"], collected["rounds"]),
        "class_counts": review_report.class_counts(collected["records"]),
        "security": collected["security"],
        # What the playbook reports beside the tallies, so it never recounts from the records.
        "counts": {
            "records": {
                "record": sum(1 for r in collected["records"] if r["basis"] == "record"),
                "legacy": sum(1 for r in collected["records"] if r["basis"] == "legacy"),
                "none": sum(1 for r in collected["records"] if r["basis"] is None),
            },
            "unparsed": len(collected["unparsed"]),
        },
    }
    for section in ("records", "unparsed"):
        data = json.dumps(collected[section], indent=1).encode("utf-8")
        payload.update(spill_bytes(data, section, scratch_dir, env=env, filename="%s.json" % section))
    return payload, collected["notices"], None


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", help="owner/repo whose PRs to report on (the router resolves it via `gh repo view`)")
    parser.add_argument("--state", choices=STATES, default="merged", help="which PRs (default: merged)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="most recent N PRs (default: 100)")
    parser.add_argument("--scratch-dir", default=None, help="spill dir (default: /tmp/gh-review-report-<repo-name>)")
    parser.add_argument("--cwd", default=None, help="working directory for the gh calls")
    args = parser.parse_args(argv)
    if "/" not in args.repo or args.limit < 1:
        parser.error("repo must be owner/repo and --limit at least 1")

    try:
        payload, notices, decision = build_report(
            args.repo, state=args.state, limit=args.limit, scratch_dir=args.scratch_dir, cwd=args.cwd
        )
    except review_report.PrListFailed as exc:
        sys.stderr.write("prep_review_report: the PR list could not be read: %s\n" % exc)
        return 1
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
    else:
        emit_ok(payload=payload, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
