#!/usr/bin/env python3
"""prep_setup.py — the setup skill's complete starting-state facts block in one call
(architecture.md §4; §9.2 "inventory-in-one-call"; docs/implementation.md S17; docs/specs/setup.md).

Setup is the SINGLE write path for the consuming repo's marker-delimited configuration blocks
(``<!-- issue-resolver-* -->`` / ``<!-- pr-evaluator-* -->`` / ``<!-- worktree-setup/teardown -->`` /
``<!-- claude-code-stack-profile -->`` / ``<!-- issue-resolver-review-guidance -->`` /
``<!-- ctrl-freaq-config -->`` / ``<!-- doc-catalogue-upkeep -->``). It also seeds and refines the
``<!-- doc-catalogue -->`` grounding declaration in ``docs/README.md``, but there it is one writer among
several: since #123 any session whose change alters a listed document keeps the catalogue current
(skills/_shared/doc-catalogue.md "Ownership — kept current by any session"). Unlike the five pipeline
preps, setup does **no** GitHub gather — its whole subject is *local Markdown files*. So this prep
assembles a purely local starting state: the tool-availability preflight, the git-repo/``root.sha``
facts, and the multi-file block **inventory** (both candidate files' block lists, per-marker
classification, the legacy ``pr-evaluator-health-checks`` signal, the user-owned
``claude-code-stack-profile`` and ``issue-resolver-review-guidance`` interiors staged for re-ingest,
the ``doc-catalogue`` state with its README and existing interior staged for the derivation
sub-agent, and the same-marker-in-both-files ambiguity) — as ONE JSON envelope on stdout, so the setup
session's startup is one Python process, never a chain of ``config_block.py list``/``read`` subprocess
calls run from the router body. With ``--repo`` it adds the one GitHub read setup makes: the review
loop's history across the repo's PRs (``facts.review_history``), the source of recurring-class
proposals for the review-guidance block (#99).

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external processes
any script may spawn are git/gh"; S8 pattern lock)::

    config_block.build_list(file)                  -- each candidate file's `<status> <name>` inventory
    config_block.build_read(file, marker)          -- the user-owned stack-profile and review-guidance
                                                      interiors, staged for the re-ingest base
    review_guidance.locate / has_text / listed     -- the review-guidance copy the resolver reads (the
                                                      re-ingest base), and which recurring class keys
                                                      it already names
    review_report.collect / class_counts           -- with ``--repo`` only: the review-loop record across
                                                      the repo's PRs, both labelled bases (#98's core)
    gitattributes.build_read(.gitattributes)       -- the existing linguist-generated block (#181)
    git ls-files -z + each file's first lines      -- the generated-file candidates setup proposes
                                                      `linguist-generated` patterns from (#181)
    shutil.which("jq"/"git"/"gh")                  -- pure-Python tool presence (no subprocess)
    git rev-parse --is-inside-work-tree / HEAD     -- prep-owned direct git call (the precedent every
                                                      other prep already established)

No ``redirect_stdout``/``io.StringIO`` capture of another script's stdout is used anywhere (S9-on
rule; docs/specs/baseline.md §5) — the composed cores return their payloads directly.

**No `gh` call without ``--repo``, and no ``AUTH_REQUIRED`` from this prep.** Setup's inventory reads
only local files; the report-only ``gh auth status`` readiness probe stays a router-level environment
check (v1's §1 main-loop probe), and the landing gate's own ``workspace.py ensure --work`` runs the real
root-freshness protocol + ``gh_persist.py create-pr`` runs the authenticated write. ``--repo`` is
opt-in so the inventory stays testable without ``gh`` and prep's inputs stay visible: with it, the
history read's ``AuthRequired`` (at the PR list or the comments read) / ``PrListFailed`` degrade to a
``REVIEW_HISTORY_UNAVAILABLE`` notice, and a failed comments chunk passes ``review_report``'s own notices through with the partial data
kept, so the envelope is still ``ok``. A malformed
(``dup``/``open``) config block is reported as an inventory **fact** (``class: "malformed"``), not a
``needs_decision`` — the router surfaces it to the operator via ``AskUserQuestion`` (docs/specs/setup.md
Invariants: "Malformed input is refused, not guessed"), matching v1's "surface these to the user"
handling. So this prep has no decision path (like ``workspace.py gc``/``lint``): its emitting envelope
is always ``status: "ok"``.

**No workspace, no root-freshness gate in prep** (same root-only shape ``prep_drafter``/
``prep_researcher`` adopted): the inventory reads the read-only ``main`` vantage; ``root.sha`` is a
plain informational ``git rev-parse HEAD``. Setup's tracked-file writes are staged in a work workspace
and land via an operator-gated PR (prd.md §8.2) — that gate lives in the flow's landing step, where
``workspace.py ensure --work`` gates root-freshness, not here.

Usage::

    prep_setup.py [--root PATH] [--scratch-dir PATH] [--repo OWNER/REPO]

``--root`` defaults to ``.`` (the project root — architecture.md §6's read-only trust vantage).
``--scratch-dir`` defaults to ``/tmp/gh-setup-<root-basename>`` (CLAUDE.md's ``/tmp/gh-<skill>-<N>/``
convention) — where the flow later stages each approved block body before ``config_block.py upsert``,
and where this prep stages the user-owned re-ingest bases and, with ``--repo``, each basis's findings
(``review-history.record.json``, ``review-history.legacy.json``).

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status: "ok"``); 2 on a
usage error (no envelope). Any other non-zero is an unclassified hard `git` failure surfaced by the
composed git call — stderr carries the faithful error.
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import gitattributes  # noqa: E402  (the `.gitattributes` linguist-generated block's read core, #181)
import review_guidance  # noqa: E402  (the review-guidance block's marker name, one spelling)
import review_report  # noqa: E402  (with --repo: the review-loop record across the repo's PRs)
from pipelib import process  # noqa: E402
from pipelib.decisions import REVIEW_HISTORY_UNAVAILABLE  # noqa: E402
from pipelib.envelope import emit_ok  # noqa: E402

# The two candidate config files, in discovery/preference order — COMMANDS.md is the preferred home
# (docs/specs/setup.md "Operator gates": "Default proposal when neither exists: create COMMANDS.md").
CANDIDATE_FILES = ("COMMANDS.md", "CLAUDE.md")

# The ten machine-parsed blocks setup reconciles to canonical on every run (docs/specs/setup.md
# "Ownership split" names v1's nine; #96 added `pr-evaluator-sensitive-paths`) — read by the resolver / evaluator preps and `workspace.py`'s hook runner.
MACHINE_PARSED_MARKERS = (
    "issue-resolver-fast-checks",
    "issue-resolver-test-target",
    "issue-resolver-canonical-suite",
    "pr-evaluator-static-checks",
    "pr-evaluator-test-target",
    "pr-evaluator-escalation-labels",
    "pr-evaluator-merge-policy",
    "pr-evaluator-sensitive-paths",
    "worktree-setup",
    "worktree-teardown",
)

# Plugin-owned-but-not-machine-parsed header (COMMANDS.md only) and the user-owned stack profile
# (CLAUDE.md only), plus the legacy pre-split block a re-run offers to migrate.
CONFIG_HEADER_MARKER = "ctrl-freaq-config"
# The pre-5.0.0 header. Reported as a legacy fact and migrated write-first by the flow; kept OUT of
# `known_markers` (classified there it would read `present` and be reconciled, not migrated).
LEGACY_CONFIG_HEADER_MARKER = "github-pipeline-config"
STACK_PROFILE_MARKER = "claude-code-stack-profile"
LEGACY_HEALTH_CHECKS_MARKER = "pr-evaluator-health-checks"

# The doc catalogue (skills/_shared/doc-catalogue.md) — the repo's declared grounding docs, read by
# the planner and drafter preps. Deliberately kept OUT of `MACHINE_PARSED_MARKERS` and
# `CANDIDATE_FILES`: it lives only in `docs/README.md`, so classifying it through the
# COMMANDS.md/CLAUDE.md machinery would report it permanently `missing` and would let it skew
# `_target_file_suggestion`'s per-file config counts. It gets a parallel read instead, the way the
# CLAUDE.md-only stack profile gets its own facts object.
DOC_CATALOGUE_MARKER = "doc-catalogue"
DOC_CATALOGUE_FILE = "docs/README.md"

# The repo's owner-written guidance for the resolver's reviewer (#99) — user-owned like the stack
# profile, and likewise kept OUT of `MACHINE_PARSED_MARKERS`: it lives in the pipeline config file
# (either candidate file), but it is prose with no canonical form, so it must never count toward
# `_target_file_suggestion` or be classified with the blocks setup reconciles to canonical.
REVIEW_GUIDANCE_MARKER = review_guidance.MARKER

# The plugin-owned `CLAUDE.md` guidance that tells every session how to keep the catalogue current
# (#123; skills/setup/references/block-authoring.md "doc-catalogue-upkeep"). Classified like the
# config header — plugin-owned, parsed by nothing — and, like the stack profile, kept out of
# `_target_file_suggestion`'s config counts: its home is fixed, so it says nothing about where config
# lives.
DOC_CATALOGUE_UPKEEP_MARKER = "doc-catalogue-upkeep"

# The generated-file scan (#181): tracked files whose first lines carry a generator's marker, the
# evidence setup proposes `linguist-generated` patterns from. Capped, because a vendored client can
# carry thousands; the flow proposes directory patterns, so the first few hundred are enough to see them.
GENERATED_MARKER_RE = re.compile(r"@generated|DO NOT EDIT|Code generated|[Aa]uto-generated")
GENERATED_HEAD_LINES = 5
GENERATED_CANDIDATE_CAP = 200
# The scan's one `git ls-files` read failed: `generated.candidates` is `null`. Never a decision.
GENERATED_SCAN_UNAVAILABLE = "GENERATED_SCAN_UNAVAILABLE"

# Tools the scripts and skills need at use-time (docs/specs/setup.md "Preflight checks").
PREFLIGHT_TOOLS = ("jq", "git", "gh")


def _is_git_repo(root):
    result = process.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _root_sha(root):
    """The root's own HEAD SHA — informational only (no freshness gate in prep; see the module
    docstring's "No workspace" note). Returns None when root is not a git repo, so prep still emits a
    faithful envelope on a non-repo target rather than hard-failing (the flow's §1 preflight reports
    the not-a-git-repo readiness line and stops)."""
    result = process.run(["git", "rev-parse", "HEAD"], cwd=root)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Preflight (report-only — never auto-fixed; docs/specs/setup.md Invariants).
# ---------------------------------------------------------------------------


def _preflight(root):
    """Deterministic environment readiness (docs/specs/setup.md "Deterministic steps": preflight is
    "pure environment probes with a fixed pass/fail → readiness-line mapping, no judgment involved").
    Tool presence via ``shutil.which`` (pure Python — hermetic, no subprocess); the git-repo check via
    a real ``git`` call. The report-only ``gh auth status`` probe is a router-level readiness line
    (a live `gh` call, never a block-authoring gate), so it is deliberately NOT run here — this keeps
    prep free of any `gh` call without ``--repo``, and of an `AUTH_REQUIRED` path (module docstring)."""
    return {
        "git_repo": _is_git_repo(root),
        "tools": {tool: shutil.which(tool) is not None for tool in PREFLIGHT_TOOLS},
    }


# ---------------------------------------------------------------------------
# Inventory (the §9.2 one-call multi-file block inventory).
# ---------------------------------------------------------------------------


def _file_inventories(root):
    """Per-candidate-file block inventory via ``config_block.build_list`` (composed in-process — the
    same core ``config_block.py list`` wraps). Returns an ordered dict-like list preserving
    CANDIDATE_FILES order; a nonexistent file lists as ``present: False`` with an empty block list
    (``build_list`` treats "no file" as "no blocks" — exit 0, docs/specs/setup.md)."""
    root_path = Path(root)
    files = {}
    for name in CANDIDATE_FILES:
        path = root_path / name
        pairs = config_block.build_list(str(path))
        files[name] = {
            "present": path.is_file(),
            "blocks": [{"status": status, "name": marker} for status, marker in pairs],
        }
    return files


def _marker_locations(files):
    """Map every discovered marker name → {file_name: status} across all candidate files. Drives the
    per-marker classification and the same-marker-in-both-files ambiguity flag."""
    locations = {}
    for file_name, info in files.items():
        for block in info["blocks"]:
            locations.setdefault(block["name"], {})[file_name] = block["status"]
    return locations


def _classify(marker, locations):
    """Classify one known marker as present / legacy / malformed / missing (docs/specs/setup.md
    "Judgment steps": inventory classification), with the files it lives in.

      - not found anywhere            -> "missing"
      - found `dup`/`open` anywhere   -> "malformed" (refused, not guessed — surfaced to the operator)
      - found `ok` (in ≥1 file)       -> "present"
    """
    where = locations.get(marker, {})
    if not where:
        return {"name": marker, "class": "missing", "files": [], "statuses": {}}
    statuses = dict(where)
    if any(status in ("dup", "open") for status in statuses.values()):
        cls = "malformed"
    else:
        cls = "present"
    return {
        "name": marker,
        "class": cls,
        "files": sorted(statuses.keys()),
        "statuses": statuses,
    }


def _stack_profile_facts(root, locations, scratch_dir):
    """User-owned ``claude-code-stack-profile`` facts (docs/specs/setup.md "Ownership split": seed when
    absent, re-ingest the existing interior as the base when present — never overwrite). When a
    well-formed block is present, stage its interior to ``<scratch>/claude-code-stack-profile.base.md``
    so the flow re-ingests the operator's actual prose as the authoritative base, layering only
    currency refinements on top."""
    where = locations.get(STACK_PROFILE_MARKER, {})
    present = bool(where) and all(status == "ok" for status in where.values())
    facts = {
        "present": present,
        "files": sorted(where.keys()),
        "statuses": dict(where),
    }
    if present:
        # Read the interior from the single well-formed location and stage it as the re-ingest base.
        source_file = sorted(where.keys())[0]
        interior, exit_code = config_block.build_read(
            str(Path(root) / source_file), STACK_PROFILE_MARKER
        )
        if exit_code == config_block.EXIT_OK:
            base_path = Path(scratch_dir) / "claude-code-stack-profile.base.md"
            base_path.write_text("".join(line + "\n" for line in interior), encoding="utf-8")
            facts["interior_present"] = len(interior) > 0
            facts["base_path"] = str(base_path)
    return facts


def _review_guidance_facts(root, locations, scratch_dir):
    """User-owned ``issue-resolver-review-guidance`` facts (#99), shaped like ``_stack_profile_facts``:
    seed when absent; when present, stage its interior to
    ``<scratch>/issue-resolver-review-guidance.base.md`` as the re-ingest base, so the owner's prose stays
    authoritative and setup proposes only additions on top of it.

    The base is the copy the resolver reads — ``review_guidance.locate``: COMMANDS.md, then CLAUDE.md,
    then one level of their ``@``-includes; the first well-formed copy wins, an empty one included. That
    copy is ``source_file`` (root-relative; absolute only when an include points outside the root), and
    ``interior_present`` is ``review_guidance.has_text``, so "present but empty" here is exactly the
    reader's "no guidance". ``files`` / ``statuses`` stay the candidate-file inventory the malformed and
    both-files attention lines read; neither follows includes."""
    where = locations.get(REVIEW_GUIDANCE_MARKER, {})
    found, lines, source = review_guidance.locate(root)
    facts = {
        "present": found,
        "files": sorted(where.keys()),
        "statuses": dict(where),
    }
    if found:
        # normpath first: `relative_to` keeps `..`, so `@../x.md` would read as a repo path.
        source = os.path.normpath(source)
        try:
            facts["source_file"] = Path(source).relative_to(root).as_posix()
        except ValueError:
            facts["source_file"] = source
        base_path = Path(scratch_dir) / ("%s.base.md" % REVIEW_GUIDANCE_MARKER)
        base_path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        facts["interior_present"] = review_guidance.has_text(lines)
        facts["base_path"] = str(base_path)
    return facts


# The history read's PR cap (``review_report.collect``'s ``limit``), reported as ``limit`` so the summary
# can say when the proposals rest on only the most recent PRs.
HISTORY_LIMIT = 100

# The per-finding record fields staged for the model, one tuple per basis (the plan's history-fact
# shape): the record basis's findings carry their class key and record id, so a proposal can cite them;
# a legacy record's ``id`` is its ledger id on a ledger row, else null.
RECORD_FIELDS = ("pr", "id", "class_key", "bucket", "outcome", "site", "item")
LEGACY_FIELDS = ("pr", "id", "bucket", "outcome", "site", "item")


def _stage(path, records, fields):
    path.write_text(json.dumps([{field: record.get(field) for field in fields} for record in records],
                               indent=1), encoding="utf-8")
    return str(path)


def _review_history(root, repo, base_text, scratch_dir):
    """``facts.review_history`` for ``--repo`` (#99), plus its notices: the review loop's history across
    the repo's PRs, open ones included, read through ``review_report``'s core in-process.

    Facts only, two labelled bases never pooled: ``class_counts`` is the record basis's exact class
    keys (each marked ``listed`` when the guidance base already names it as a code span), with the
    findings it tallies staged to ``<scratch>/review-history.record.json``; the legacy basis — every
    record ``review_report`` gives ``basis: "legacy"``, ledger rows included — is staged to
    ``<scratch>/review-history.legacy.json``. Both are staged for the model to read, never inline.
    ``unkeyed_findings`` counts the records on neither: a record-basis record with no class key (a
    ledger entry no record line names) and a record with no knowable basis (``None``). Whether a class
    recurs, which keys are one class, and how an entry reads are the model's (block-authoring.md).
    Only trusted authors' loop comments and PR bodies are read (``review_report``'s trust rule, SEC-1),
    and ``skipped`` counts the rest.
    Whenever ``collect`` raises — ``PrListFailed`` on the PR list, ``AuthRequired`` at the list or the
    comments read — the history degrades to ``REVIEW_HISTORY_UNAVAILABLE`` with no partial data;
    ``review_report``'s own comments notices pass through verbatim with the partial data kept."""
    try:
        collected = review_report.collect(repo, state="all", limit=HISTORY_LIMIT, cwd=root)
    except (review_report.AuthRequired, review_report.PrListFailed) as exc:
        reason = "gh is not authenticated" if isinstance(exc, review_report.AuthRequired) else (
            "the PR list could not be read: %s" % (str(exc).strip().splitlines() or ["no detail"])[0])
        notice = ("%s: the review-loop history of %s could not be read (%s); setup proposes no "
                  "recurring-class entries this run" % (REVIEW_HISTORY_UNAVAILABLE, repo, reason))
        return {"repo": repo, "available": False, "limit": HISTORY_LIMIT, "prs_scanned": 0,
                "coverage": None, "skipped": None, "class_counts": [], "legacy_findings": 0,
                "unkeyed_findings": 0}, [notice]

    records = collected["records"]
    counts = review_report.class_counts(records)
    named = review_guidance.listed(base_text, [entry["class_key"] for entry in counts])
    # The findings class_counts tallies — its own filter, so the staged file and the counts agree.
    keyed = [r for r in records if r["format"] == "record" and r["class_key"]]
    legacy = [r for r in records if r["basis"] == "legacy"]
    unkeyed = [r for r in records if r["basis"] is None or (r["basis"] == "record" and not r["class_key"])]
    scratch = Path(scratch_dir)
    return {
        "repo": repo,
        "available": True,
        "limit": HISTORY_LIMIT,
        "prs_scanned": collected["prs_scanned"],
        "coverage": collected["coverage"],
        # Text by an untrusted or unknown author, left unread by review_report's trust rule (SEC-1): counts only.
        "skipped": collected["skipped"],
        "class_counts": [dict(entry, listed=entry["class_key"] in named) for entry in counts],
        "record_path": _stage(scratch / "review-history.record.json", keyed, RECORD_FIELDS),
        "legacy_findings": len(legacy),
        "legacy_path": _stage(scratch / "review-history.legacy.json", legacy, LEGACY_FIELDS),
        "unkeyed_findings": len(unkeyed),
    }, list(collected["notices"])


def _doc_catalogue_facts(root, scratch_dir):
    """Session-maintained ``doc-catalogue`` facts (skills/_shared/doc-catalogue.md) — a parallel read of
    ``docs/README.md``, not part of the CANDIDATE_FILES inventory (see DOC_CATALOGUE_MARKER).

    Stages two files for the derivation sub-agent, because a context-blind sub-agent receives
    prep-staged files rather than reading a repo itself:

      - ``<scratch>/doc-catalogue.readme.md`` — the whole docs index, the sub-agent's ONLY derivation
        source when no block exists yet (it must not go hunting for docs the index doesn't name);
      - ``<scratch>/doc-catalogue.base.md`` — the existing block interior, the authoritative
        re-ingest base, staged only when a well-formed block is present.

    ``readme_present`` and ``present`` are reported separately because they lead to different flow
    behavior: no README at all means "skipped, tell the operator to create one", while a README
    without the block means "derive and propose".
    """
    readme_path = Path(root) / DOC_CATALOGUE_FILE
    readme_present = readme_path.is_file()
    facts = {"readme_present": readme_present, "present": False, "status": None}
    if not readme_present:
        return facts

    staged_readme = Path(scratch_dir) / "doc-catalogue.readme.md"
    staged_readme.write_text(readme_path.read_text(encoding="utf-8"), encoding="utf-8")
    facts["readme_path"] = str(staged_readme)

    # `build_list` reports this file's own blocks with their status; a `dup`/`open` catalogue is
    # surfaced and NOT staged as a base — a malformed block is fixed by hand, never guessed at.
    for status, marker in config_block.build_list(str(readme_path)):
        if marker == DOC_CATALOGUE_MARKER:
            facts["status"] = status
            break
    if facts["status"] != "ok":
        return facts

    facts["present"] = True
    interior, exit_code = config_block.build_read(str(readme_path), DOC_CATALOGUE_MARKER)
    if exit_code == config_block.EXIT_OK:
        base_path = Path(scratch_dir) / "doc-catalogue.base.md"
        base_path.write_text("".join(line + "\n" for line in interior), encoding="utf-8")
        facts["interior_present"] = len(interior) > 0
        facts["base_path"] = str(base_path)
    return facts


def _target_file_suggestion(files, locations):
    """Decide the pipeline-config target file (docs/specs/setup.md "Operator gates": keep config in
    one file; default to COMMANDS.md when neither exists). Counts machine-parsed + header markers per
    file (the stack-profile is CLAUDE.md-only and excluded from this decision). ``split`` is True when
    config markers are scattered across both files (an ambiguity the router confirms with the
    operator)."""
    config_markers = set(MACHINE_PARSED_MARKERS) | {CONFIG_HEADER_MARKER, LEGACY_CONFIG_HEADER_MARKER}
    counts = {name: 0 for name in CANDIDATE_FILES}
    for marker, where in locations.items():
        if marker not in config_markers:
            continue
        for file_name in where:
            if file_name in counts:
                counts[file_name] += 1

    files_with_config = [name for name in CANDIDATE_FILES if counts[name] > 0]
    if len(files_with_config) == 1:
        suggested = files_with_config[0]
        reason = "existing-config"
    elif len(files_with_config) > 1:
        # Split across both — keep the file holding more, but flag it for the router to confirm.
        suggested = max(files_with_config, key=lambda name: counts[name])
        reason = "split-existing"
    else:
        suggested = "COMMANDS.md"
        reason = "default-new"
    return {
        "suggested": suggested,
        "reason": reason,
        "exists": files[suggested]["present"],
        "split": len(files_with_config) > 1,
        "config_marker_counts": counts,
    }


def _same_marker_both_files(locations):
    """Every marker declared in BOTH candidate files — an ambiguity the pipeline skills would also hit
    (they scan both), so the router asks which file is canonical and plans to remove the duplicate
    (docs/specs/setup.md Invariants: "Keep each config marker in exactly one file")."""
    return sorted(
        marker
        for marker, where in locations.items()
        if len([f for f in where if f in CANDIDATE_FILES]) > 1
    )


def _guidance_reader_view(guidance):
    """What the resolver's reviewer gets from the guidance block, in the attention line's words."""
    if guidance.get("interior_present"):
        return "the well-formed copy in `%s`" % guidance["source_file"]
    if guidance["present"]:
        return ("no repo guidance (the well-formed copy in `%s` is empty and shadows any later one)"
                % guidance["source_file"])
    return "no repo guidance"


def _build_attention(inventory):
    """Script-detectable conditions worth surfacing with evidence (architecture.md §4). These are
    informational facts the router acts on via its gates — not decision cards from prep."""
    attention = []
    malformed = [m["name"] for m in inventory["known_markers"] if m["class"] == "malformed"]
    if malformed:
        attention.append(
            "malformed config block(s) (dup/open) — the operator must fix by hand before setup "
            "reconciles them: %s" % ", ".join(malformed)
        )
    guidance = inventory["review_guidance"]
    malformed_guidance = sorted(f for f, st in guidance["statuses"].items() if st in ("dup", "open"))
    if malformed_guidance:
        attention.append(
            "malformed `issue-resolver-review-guidance` block (dup/open) in %s — the operator must fix "
            "it by hand; the resolver skips a malformed copy, so its reviewer reads %s"
            % (", ".join(malformed_guidance), _guidance_reader_view(guidance))
        )
    if inventory["legacy_health_checks"]["present"]:
        attention.append(
            "legacy `pr-evaluator-health-checks` block present — a re-run offers to split it into "
            "`pr-evaluator-static-checks` + `pr-evaluator-test-target` (operator-confirmed)"
        )
    if inventory["legacy_config_header"]["present"]:
        attention.append(
            "legacy `github-pipeline-config` header present in %s — a re-run offers to replace it with "
            "`ctrl-freaq-config` (the 5.0.0 rename; operator-confirmed)"
            % ", ".join(inventory["legacy_config_header"]["files"])
        )
    if inventory["same_marker_both_files"]:
        attention.append(
            "config marker(s) declared in BOTH candidate files (ambiguous — one file must win): %s"
            % ", ".join(inventory["same_marker_both_files"])
        )
    catalogue = inventory["doc_catalogue"]
    if not catalogue["readme_present"]:
        attention.append(
            "no `docs/README.md` — doc catalogue skipped; until one exists the planner and drafter "
            "ground on no documents at all (create it, then re-run setup)"
        )
    elif catalogue["status"] in ("dup", "open"):
        attention.append(
            "malformed `doc-catalogue` block in `docs/README.md` — the operator must fix it by hand; "
            "readers treat a malformed block as absent, so grounding is currently off"
        )
    elif not catalogue["present"]:
        attention.append(
            "`docs/README.md` carries no `doc-catalogue` block — setup derives one from the index and "
            "proposes it"
        )
    return attention


def _file_head_is_generated(path):
    """Whether the file's first `GENERATED_HEAD_LINES` lines carry a generator's marker. A file that
    cannot be read as text (binary, unreadable) is not a candidate."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for _ in range(GENERATED_HEAD_LINES):
                line = fh.readline()
                if not line:
                    break
                if GENERATED_MARKER_RE.search(line):
                    return True
    except (OSError, UnicodeDecodeError):
        return False
    return False


def _generated_facts(root):
    """`(generated, notices, attention)` — `generated` is `{block_lines, candidates,
    candidates_truncated}` (#181). `block_lines` is the existing `.gitattributes` block's interior (`[]`
    when absent), or `None` with the decision's brief in `attention` when its fence is malformed.
    `candidates` lists the tracked files whose head carries a generated marker, sorted, at most
    `GENERATED_CANDIDATE_CAP`; `None` with the `GENERATED_SCAN_UNAVAILABLE` notice when the one
    `git ls-files` read fails."""
    notices, attention = [], []
    payload, _, decision = gitattributes.build_read(str(Path(root) / ".gitattributes"))
    if decision is not None:
        block_lines = None
        attention.append(
            "%s — %s" % (decision["summary"], "; ".join(decision["options"]))
        )
    else:
        block_lines = payload["lines"]
    result = process.run(["git", "ls-files", "-z"], cwd=root)
    if result.returncode != 0:
        notices.append(GENERATED_SCAN_UNAVAILABLE)
        return {"block_lines": block_lines, "candidates": None, "candidates_truncated": False}, notices, attention
    candidates = []
    truncated = False
    for path in sorted(p for p in result.stdout.split("\0") if p):
        if _file_head_is_generated(Path(root) / path):
            if len(candidates) == GENERATED_CANDIDATE_CAP:
                truncated = True
                break
            candidates.append(path)
    return (
        {"block_lines": block_lines, "candidates": candidates, "candidates_truncated": truncated},
        notices,
        attention,
    )


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def build_facts(root=".", scratch_dir=None, repo=None):
    """Assemble the setup skill's complete facts block and return the envelope dict WITHOUT printing
    it (the testable core, mirroring ``prep_researcher.build_facts``). Emits no ``needs_decision`` (no
    `gh` call without ``repo``, an unreadable history is a notice, and a malformed block is an inventory
    fact, not a decision — module docstring), so this
    core always returns a dict."""
    root = str(Path(root).resolve())
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-setup-%s" % Path(root).name
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    files = _file_inventories(root)
    locations = _marker_locations(files)

    known_markers = [_classify(marker, locations) for marker in MACHINE_PARSED_MARKERS]
    known_markers.append(_classify(CONFIG_HEADER_MARKER, locations))

    legacy = _classify(LEGACY_HEALTH_CHECKS_MARKER, locations)
    legacy_health_checks = {
        "present": legacy["class"] in ("present", "malformed"),
        "files": legacy["files"],
    }

    legacy_header = _classify(LEGACY_CONFIG_HEADER_MARKER, locations)
    legacy_config_header = {
        "present": legacy_header["class"] in ("present", "malformed"),
        "files": legacy_header["files"],
    }

    inventory = {
        "files": files,
        "known_markers": known_markers,
        "legacy_health_checks": legacy_health_checks,
        "legacy_config_header": legacy_config_header,
        "stack_profile": _stack_profile_facts(root, locations, scratch_dir),
        "review_guidance": _review_guidance_facts(root, locations, scratch_dir),
        "doc_catalogue": _doc_catalogue_facts(root, scratch_dir),
        "doc_catalogue_upkeep": _classify(DOC_CATALOGUE_UPKEEP_MARKER, locations),
        "config_header": _classify(CONFIG_HEADER_MARKER, locations),
        "same_marker_both_files": _same_marker_both_files(locations),
    }

    facts = {
        "scratch": scratch_dir,
        "root": {"path": root, "sha": _root_sha(root)},
        "preflight": _preflight(root),
        "inventory": inventory,
        "target_file": _target_file_suggestion(files, locations),
        "attention": _build_attention(inventory),
        "notices": [],
    }
    facts["generated"], generated_notices, generated_attention = _generated_facts(root)
    facts["notices"].extend(generated_notices)
    facts["attention"].extend(generated_attention)
    if repo is not None:
        guidance = inventory["review_guidance"]
        base_text = (Path(guidance["base_path"]).read_text(encoding="utf-8")
                     if guidance.get("base_path") else "")
        facts["review_history"], history_notices = _review_history(root, repo, base_text, scratch_dir)
        facts["notices"].extend(history_notices)
    return facts


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", default=".", help="project root (architecture.md §6 vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for staged block bodies + the stack-profile re-ingest base "
        "(default: /tmp/gh-setup-<root-basename>)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="owner/repo whose review-loop history to read for recurring-class proposals (the one "
        "`gh` read; omitted, prep makes no `gh` call)",
    )
    args = parser.parse_args(argv)
    if args.repo is not None and "/" not in args.repo:
        parser.error("--repo must be owner/repo")

    facts = build_facts(root=args.root, scratch_dir=args.scratch_dir, repo=args.repo)
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
