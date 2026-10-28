#!/usr/bin/env python3
"""prep_question_sweep.py — the question-sweep skill's complete starting-state facts block in one call
(architecture.md §4, §9.2; docs/implementation.md S18; docs/specs/question-sweep.md).

The **question-sweep** reconciles the open questions (OQs) scattered across a repo's docs against the
GitHub **question registry** — the set of `question`-labelled issues, the registry of record
(`skills/_shared/open-question-links.md` §"Status is the tracker's"). This prep assembles the session's
entire deterministic starting state — the registry snapshot **with the Tier-1 status join per entry**,
the doc-candidate detection inputs (the `<!-- drafter-open-question-markers -->` config block + the doc
inventory in scope), `root.sha`, staging, and `attention` — as ONE JSON envelope on stdout, so the
sweep session's startup is one Python process, never a subprocess chain from the router body.

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external processes
any script may spawn are git/gh"; S8 pattern lock)::

    gh issue list --label question --state all    -- the registry snapshot (a prep-owned direct `gh`
                                                     call — the same precedent every prep establishes
                                                     for a list read `oq_tracker` does not cover: its
                                                     `--search` shape is the de-dup lookup, not a full
                                                     registry enumeration)
    gh_gather.run(..., marker_prefix=<decision>)  -- per still-OPEN question: the decision-marker probe
                                                     that realizes the Tier-1 join's second half, and
                                                     stages the body/thread the router's Tier-2 reader
                                                     reads without a re-fetch
    config_block.read_block_anywhere              -- the `<!-- drafter-open-question-markers -->` OQ
                                                     detection hint (raw interior — meaning is the
                                                     router's read, per prep_drafter's identical use)
    git rev-parse HEAD                            -- root's own SHA (informational; no freshness gate
                                                     in prep — see the "No workspace" note)

No ``redirect_stdout``/``io.StringIO`` capture of another script's stdout is used anywhere (S9-on rule;
docs/specs/baseline.md §5): the composed cores return their payloads directly.

**The Tier-1 status join (the DoD box-1 matrix; `open-question-links.md` §"Status is the tracker's").**
Per registry entry, prep derives a deterministic Tier-1 `status` from two script-visible signals — the
issue `state` and, for an OPEN issue, whether a `<!-- question-decision:v1 -->` comment exists:

  - ``closed``          — the issue is `closed` (Tier-1 resolved; no marker fetch — closed short-
                          circuits, `open-question-links.md` §Status Tier 1).
  - ``decision-marked`` — the issue is `open` but carries a `<!-- question-decision:v1 -->` comment
                          (Tier-1 resolved by the durable decision `question-resolver` records).
  - ``still-open``      — the issue is `open` with no decision marker → ``tier2_needed: true``, the
                          signal that only the ROUTER-dispatched question-status reader (Tier 2,
                          judgment) can tell whether the thread already answered it. **Prep never runs
                          Tier 2** — the reader is a sub-agent the router dispatches (§4a), not a
                          deterministic step.
  - ``ambiguous``       — the issue is `open` and carries MORE than one decision comment (the gather's
                          `MARKER_AMBIGUOUS`): recorded as an `attention` fact + a per-entry status,
                          **not** forwarded as a `needs_decision` — one anomalous question must not
                          abort a project-wide sweep (the router surfaces it; never auto-resolves).

Only OPEN entries cost a marker fetch; a ``closed`` entry is resolved from its `state` alone. So the
call budget is: 1 registry list + one `gh_gather` round-trip (3 `gh` calls) per OPEN question — flat
and script-visible (the two-sided budget test).

**Detection inputs, not detection (facts by script, meaning by model).** Prep surfaces the
`<!-- drafter-open-question-markers -->` config block raw (register-location / inline-pattern / open-
status-rule prose is the router's read) with `heuristics_active` when absent, and a doc inventory of
the files in scope. The grep-prefilter + `Explore`-confirm detection and the doc-OQ↔question matching
stay judgment in the playbook (docs/specs/question-sweep.md "Judgment steps").

**No `suggested_playbook`** — the sweep is a single linear flow (no mode fork), like `setup`.

**No workspace, no root-freshness gate in prep** (the root-only shape `prep_setup`/`prep_researcher`
adopted): the registry read and detection inputs read the read-only `main` vantage; `root.sha` is a
plain informational `git rev-parse HEAD`. The sweep's tracked-file edits (doc fold-backs / back-links)
are staged in a work workspace and land via an operator-gated PR (prd.md §8.2) — that gate lives in the
flow's landing step, where `workspace.py ensure --work` gates root-freshness, not here.

Usage::

    prep_question_sweep.py <owner/repo> [--scope GLOB] [--root PATH] [--scratch-dir PATH] [--cwd PATH]

``<owner/repo>`` is required (the router resolves it via `gh repo view` and passes it, the same way
every pipeline prep receives its repo). ``--scope`` is the operator's optional docs path/glob (the
`[docs-path-or-glob]` sweep argument); it rides in `scope.arg` as a fact and defaults to `docs/**`.
``--root`` defaults to ``.`` (architecture.md §6's read-only trust vantage). ``--scratch-dir`` defaults
to ``/tmp/gh-question-sweep-<repo-basename>`` (CLAUDE.md's ``/tmp/gh-<skill>-<N>/`` convention) — where
each open question's body/thread spills and the flow later stages companion-issue bodies.

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"`` or
``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified hard
`gh`/`git` failure surfaced by the composed gather — stderr carries the faithful error.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import gh_gather  # noqa: E402
from pipelib import process, trust  # noqa: E402
from pipelib.decisions import AUTH_REQUIRED, needs_decision  # noqa: E402
from pipelib.envelope import emit_needs_decision, emit_ok  # noqa: E402

# The durable decision comment (skills/_shared/open-question-links.md §Status Tier 1; the sole writer is
# question-resolver) — gh_gather's `marker_prefix`, so `marker_comment_present` realizes Tier-1's marker
# half. Byte-identical to the frozen marker (docs/specs/examples/question-decision.md).
DECISION_MARKER = "<!-- question-decision:v1 -->"

# The `question`-issue label (skills/_shared/question-issue.md) — the registry's `--label` filter.
QUESTION_LABEL = "question"

# The OQ-detection config block the consuming repo declares (skills/_shared/open-question-detection.md
# §"Config block (preferred hint)"), read from CLAUDE.md/COMMANDS.md via config_block.read_block_anywhere
# — the SAME block prep_drafter reads. Absent -> heuristics_active (the built-in cue fallback).
OQ_MARKER_CONFIG_BLOCK = "drafter-open-question-markers"

# Default docs scope (docs/specs/question-sweep.md "Overview": "docs/** plus config-declared register
# locations"); the operator may narrow it via --scope.
DEFAULT_SCOPE_GLOB = "docs/**"


class _DiscardStream:
    """A minimal write-sink for `gh_gather.run(stream=...)` — the per-question gather envelope is read
    from the returned tuple, never printed here (see `prep_researcher._DiscardStream` for the full
    rationale: a two-method sink is not the in-process composition architecture.md §2 asks for)."""

    def write(self, _data):
        return None

    def flush(self):
        return None


def _forward_decision(decision, notices=None):
    """Emit a composed core's returned `decision` AS-IS on prep's own stdout and return `True` when a
    decision was present. Mirrors `prep_researcher._forward_decision` exactly."""
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


def _root_sha(root):
    result = process.run(["git", "rev-parse", "HEAD"], cwd=root)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Registry snapshot (docs/specs/question-sweep.md "Deterministic steps": tracker registry fetch).
# ---------------------------------------------------------------------------


def _fetch_registry(repo, cwd=None, env=None):
    """`gh issue list --repo <repo> --state all --label question --limit 500 --json ...` — the registry
    snapshot (docs/specs/question-sweep.md "Artifacts read"). Returns `(entries,
    decision_or_none)`; each entry carries `number`/`title`/`state`/`labels`/`url`. A prep-owned direct
    `gh` call (`oq_tracker` covers only the `--search` de-dup lookup, never a full enumeration)."""
    result = process.run(
        [
            "gh", "issue", "list", "--repo", repo, "--state", "all",
            "--label", QUESTION_LABEL, "--limit", "500",
            "--json", "number,title,state,labels,url",
        ],
        cwd=cwd,
        env=env,
    )
    if result.auth_required:
        # Reuse the AUTH_REQUIRED shape so the router's single decision rule handles it.
        return None, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    raw = json.loads(result.stdout)
    return [
        {
            "number": item.get("number"),
            "title": item.get("title"),
            "state": item.get("state"),
            "labels": [label.get("name") for label in item.get("labels") or []],
            "url": item.get("url"),
        }
        for item in raw
    ], None


# ---------------------------------------------------------------------------
# Tier-1 status join (docs/specs/question-sweep.md "Deterministic steps": Tier-1 derivation).
# ---------------------------------------------------------------------------


def _is_closed(entry):
    return (entry.get("state") or "").upper() == "CLOSED"


def _tier1_for_open_question(entry, repo, scratch_dir, env, notices):
    """Fetch the OPEN question's decision marker (and stage its body/thread for the router's Tier-2
    reader) and derive its Tier-1 `status`. Returns `(join_dict, fatal_decision_or_none)`. The
    gather's `MARKER_UNTRUSTED` notices (a decision marker an untrusted author wrote, skipped — #179)
    are appended to `notices`; its other notices are not forwarded.

    - `MARKER_AMBIGUOUS` (>1 decision comment) is recorded as `status: "ambiguous"` and continues — a
      single anomalous question must not abort a project-wide sweep (the router surfaces it).
    - `AUTH_REQUIRED` is fatal (auth affects every subsequent call) and is forwarded to the caller.
    """
    exit_code, envelope = gh_gather.run(
        str(entry["number"]),
        repo,
        marker_prefix=DECISION_MARKER,
        scratch_dir=scratch_dir,
        env=env,
        stream=_DiscardStream(),
    )
    if envelope is not None:
        notices.extend(n for n in trust.untrusted_notices(envelope.get("notices")) if n not in notices)
    if envelope is not None and envelope.get("status") == "needs_decision":
        code = (envelope.get("decision") or {}).get("code")
        if code == "AUTH_REQUIRED":
            return None, envelope["decision"]
        # MARKER_AMBIGUOUS (or any other non-auth decision): record + continue.
        ids = (envelope.get("decision") or {}).get("context", {}).get("marker_comment_ids") or []
        return {
            "status": "ambiguous",
            "resolved": False,
            "tier2_needed": False,
            "marker_comment_present": None,
            "marker_comment_count": len(ids),
        }, None
    if exit_code != 0:
        # A hard fetch failure on one question is surfaced as an attention-worthy per-entry status,
        # not a whole-sweep abort — same graceful-degradation posture as the ambiguous case.
        return {
            "status": "fetch-failed",
            "resolved": False,
            "tier2_needed": False,
            "marker_comment_present": None,
        }, None

    marker_present = bool(envelope.get("marker_comment_present"))
    sections = {
        key: value
        for key, value in envelope.items()
        if key.startswith(("issue_body", "thread", "marker_comment"))
    }
    join = {
        "status": "decision-marked" if marker_present else "still-open",
        "resolved": marker_present,
        "tier2_needed": not marker_present,
        "marker_comment_present": marker_present,
        "sections": sections,
    }
    if marker_present:
        join["marker_comment_id"] = envelope.get("marker_comment_id")
    return join, None


def _join_registry(entries, repo, scratch_dir, env, notices):
    """Attach the Tier-1 status join to every registry entry. Returns `(joined, fatal_decision_or_none)`.
    Only OPEN entries cost a marker fetch (`closed` short-circuits Tier 1). Each fetch's
    `MARKER_UNTRUSTED` notices are appended to `notices`."""
    joined = []
    for entry in entries:
        if _is_closed(entry):
            entry = dict(entry)
            entry.update({"status": "closed", "resolved": True, "tier2_needed": False})
            joined.append(entry)
            continue
        join, fatal = _tier1_for_open_question(entry, repo, scratch_dir, env, notices)
        if fatal is not None:
            return None, fatal
        entry = dict(entry)
        entry.update(join)
        joined.append(entry)
    return joined, None


# ---------------------------------------------------------------------------
# Detection inputs (docs/specs/question-sweep.md "Deterministic steps": detection-config read + scope).
# ---------------------------------------------------------------------------


def _read_oq_marker_config(root):
    """The `<!-- drafter-open-question-markers -->` config block (skills/_shared/open-question-detection.md
    §"Config block (preferred hint)") — raw interior text, never interpreted here (identical to
    prep_drafter's use). Absent -> `heuristics_active: True`, the built-in-cue fallback signal."""
    present, lines, source = config_block.read_block_anywhere(root, OQ_MARKER_CONFIG_BLOCK)
    return {
        "oq_markers": {
            "present": present,
            "raw": "\n".join(lines) if present else None,
            "source": source,
        },
        "heuristics_active": not present,
    }


def _doc_inventory(root, scope):
    """Candidate doc files in scope (docs/specs/question-sweep.md "Deterministic steps": grep-prefilter
    input). Default scope is `docs/**` — a recursive `*.md` listing under `docs/`; the actual grep-
    prefilter + `Explore`-confirm detection stays judgment (the playbook). `present` means ≥1 found."""
    root_path = Path(root)
    files = []
    docs_dir = root_path / "docs"
    if scope is None and docs_dir.is_dir():
        for match in sorted(docs_dir.rglob("*.md")):
            if match.is_file():
                files.append(str(match.relative_to(root_path)))
    elif scope is not None:
        # Honor an operator-named glob relative to root (a plain filename lists itself when present).
        for match in sorted(root_path.glob(scope)):
            if match.is_file():
                files.append(str(match.relative_to(root_path)))
    return {"present": len(files) > 0, "files": files}


# ---------------------------------------------------------------------------
# Attention (architecture.md §4 — script-detectable conditions worth surfacing, not decision cards).
# ---------------------------------------------------------------------------


def _build_attention(joined, docs):
    attention = []
    ambiguous = [q["number"] for q in joined if q.get("status") == "ambiguous"]
    if ambiguous:
        attention.append(
            "question(s) carrying more than one `<!-- question-decision:v1 -->` comment (surface, "
            "never auto-resolve): %s" % ", ".join("#%s" % n for n in ambiguous)
        )
    failed = [q["number"] for q in joined if q.get("status") == "fetch-failed"]
    if failed:
        attention.append(
            "question(s) whose thread fetch failed (retry or check access): %s"
            % ", ".join("#%s" % n for n in failed)
        )
    tier2 = [q["number"] for q in joined if q.get("tier2_needed")]
    if tier2:
        attention.append(
            "%d still-open question(s) need a Tier-2 thread read (the router dispatches the "
            "question-status reader): %s" % (len(tier2), ", ".join("#%s" % n for n in tier2))
        )
    if not docs["present"]:
        attention.append("no docs in scope — nothing to reconcile against the registry")
    return attention


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def build_facts(repo, scope=None, root=".", scratch_dir=None, cwd=None, env=None):
    """Assemble the sweep's complete facts block and return the envelope dict WITHOUT printing it (the
    testable core, mirroring `prep_researcher.build_facts`). Returns `None` after a `needs_decision`
    envelope has already been emitted on stdout (a fatal AUTH_REQUIRED)."""
    root = str(Path(root).resolve())
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-question-sweep-%s" % repo.split("/")[-1]
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    entries, decision = _fetch_registry(repo, cwd=cwd, env=env)
    if decision is not None:
        _forward_decision(decision)
        return None

    marker_notices = []
    joined, fatal = _join_registry(entries, repo, scratch_dir, env, marker_notices)
    if fatal is not None:
        _forward_decision(fatal, notices=marker_notices)
        return None

    detection = _read_oq_marker_config(root)
    docs = _doc_inventory(root, scope)

    facts = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root, "sha": _root_sha(root)},
        "scope": {"arg": scope, "default_glob": DEFAULT_SCOPE_GLOB},
        "detection": detection,
        "docs": docs,
        "registry": {"count": len(joined), "questions": joined},
        "attention": _build_attention(joined, docs),
        "notices": marker_notices,
    }
    return facts


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", help="owner/repo (the router resolves it via `gh repo view`)")
    parser.add_argument(
        "--scope",
        default=None,
        help="the operator's optional docs path/glob (the sweep's `[docs-path-or-glob]` argument); "
        "rides in scope.arg as a fact (default: docs/**)",
    )
    parser.add_argument("--root", default=".", help="project root (architecture.md §6 vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for spilled question threads + staged companion bodies "
        "(default: /tmp/gh-question-sweep-<repo-basename>)",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="explicit working directory (reserved for parity with the other preps' --cwd knob; the "
        "registry gather scopes every gh call with --repo, so it is unused in normal runs)",
    )
    args = parser.parse_args(argv)

    facts = build_facts(
        args.repo, scope=args.scope, root=args.root, scratch_dir=args.scratch_dir, cwd=args.cwd
    )
    if facts is None:
        return 0
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
