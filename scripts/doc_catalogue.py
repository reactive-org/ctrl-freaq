#!/usr/bin/env python3
"""doc_catalogue.py — the consuming repo's grounding-doc catalogue (architecture.md §2, §7;
``skills/_shared/doc-catalogue.md``). One deterministic mechanism, one home: read the
``<!-- doc-catalogue -->`` block from the consuming repo's ``docs/README.md`` and hand back the
declared documents, so no prompt and no prep asserts a doc layout of its own.

**Why this module exists.** Before it, doc grounding was a hardcoded path list in two places —
``prep_planner._GROUNDING_DOC_PATHS`` (``docs/prd.md`` / ``docs/architecture.md`` /
``docs/constitution.md`` / ``CLAUDE.md``) and ``prep_drafter``'s four
``_PRD_CANDIDATE_PATHS``/``_ARCHITECTURE_PATH``/``_CONSTITUTION_PATH``/``_CLAUDE_MD_PATH``
constants, which disagreed with each other on the PRD's spelling and with the prompts, which named
two further docs (``docs/ui-design.md``, ``docs/architecture-notes.md``) neither prep inventoried.
A path list in the plugin is an assumption about a *consuming* repo's layout — the thing the
convention-driven coupling rule (CLAUDE.md "Coupling to a consuming repo is convention-driven")
exists to forbid. Every grounding reader's prep now calls this module — planner, drafter, slicer,
requirements-gatherer, and (since 4.27.0, which retired the last prose path lists) resolver,
evaluator, and question-resolver; it is the ``oq_tracker.py`` promotion pattern (one literal
algorithm serving one shared contract, not several similarly-shaped local helpers).

**No fallback, no walk.** When the catalogue is absent the answer is an empty set plus the
``DOC_CATALOGUE_ABSENT`` notice — never a built-in path list, and never a filesystem search for
doc-looking files. A guessed doc layout is what the block replaces, and a silent guess is worse than
a loud gap: it grounds a plan on a document the repo never nominated. What a consumer *does* about
the gap differs by consumer (planner/drafter proceed ungrounded; a documents-derived consumer
refuses) and that asymmetry lives in the shared contract, not here.

**A directory entry is listed, not walked.** An entry may name a directory — a document set the repo
declares under one role / authority / summary (a ``docs/architecture/`` of spokes beside a
``docs/architecture.md`` hub). Until 4.26.0 presence was ``is_file()``, so such an entry always read
as missing: every reader raised a false "stale entry" attention line, and the planner and slicer —
told to skip a ``present: false`` entry — wrote binding documents out of their grounding. Listing the
directory's direct members is not the forbidden walk: the repo nominated that directory, the listing
is one level and deterministic, and nothing searches for doc-shaped files elsewhere. The script lists
it so no prompt, and no context-blind reviewer, has to — and so a citation names a member file.

**The write-side check (#123).** Any session's model may now edit the catalogue, not only
``setup``, so the module also answers the writer's question: does the block I am about to land
hold, and does it change what grounds the work? ``build_check`` is that core and ``check`` its one
CLI subcommand. A reader skips a bad line silently — that posture stays, because a reader must never
break a session over a hand-edit — so the refusal lives on the write side, as the
``DOC_CATALOGUE_MALFORMED`` decision, where the session that broke the line can still fix it. The
same core reports ``confirm``: the entries whose change stops or starts a document grounding the
work (a ``binding`` entry added or removed, any ``authority`` changed), measured against the base
ref. A change that weakens a gate is one the operator confirms (skills/_shared/doc-catalogue.md
"Keeping it current"), and the evaluator reads the same fact at the PR head as the backstop for a
writer that never ran the check.

The read helpers are pure, non-emitting cores (architecture.md §2's pure-core pattern, the S8 lock):
``(value, notices)`` — never prints, never exits, never raises on malformed input. A prep composes
these in-process and merges the returned notices into its own envelope. ``build_check`` follows the
executor shape instead, ``(payload, notices, decision | None)``, with ``main()`` as the thin emit
wrapper.

    doc_catalogue.py check --root <checkout> [--base-ref <ref>]
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
from pipelib import process  # noqa: E402
from pipelib.decisions import (  # noqa: E402
    DOC_CATALOGUE_ABSENT,
    DOC_CATALOGUE_MALFORMED,
    needs_decision,
)
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_needs_decision, emit_ok  # noqa: E402

# The base ref could not be read (an unknown ref, or no `docs/README.md` history to compare), so
# `confirm` is `None` — "not measured", never "nothing changed". A notice, not a decision: the
# writer still confirms the authority edits it made itself.
DOC_CATALOGUE_BASE_UNAVAILABLE = "DOC_CATALOGUE_BASE_UNAVAILABLE"

# The block name and its fixed home (skills/_shared/doc-catalogue.md §Home). `docs/README.md` is a
# FIXED single candidate, not a search list: the catalogue belongs beside the documents it names, and
# a repo's own docs index is a file it maintains for its own sake. This is the first caller in the
# repo to override `config_block.read_block_anywhere`'s `candidate_filenames` — the override the
# module anticipated (config_block.py's DEFAULT_CONFIG_CANDIDATE_FILES comment) — because the
# COMMANDS.md/CLAUDE.md default is deliberately wrong here.
CATALOGUE_MARKER = "doc-catalogue"
CATALOGUE_FILE = "docs/README.md"

# `authority` is a CLOSED pair (skills/_shared/doc-catalogue.md §"Entry grammar"): `binding` means a
# conflict is a blocker, `informative` means a conflict is a judgment call. An entry whose authority
# is neither is skipped rather than coerced — silently defaulting an unrecognized value either
# invents a blocker or discards one, and both are worse than a skipped line setup will flag.
AUTHORITY_BINDING = "binding"
AUTHORITY_INFORMATIVE = "informative"
AUTHORITY_VALUES = (AUTHORITY_BINDING, AUTHORITY_INFORMATIVE)

# `role` is an OPEN set — these are the slugs a reader recognizes and can route on ("which document
# says what the architecture is"); any other slug is carried through untouched and its `summary` and
# `authority` still apply in full. Exported so a prompt-facing consumer can name the recognized set
# without re-deriving it; never used to filter or reject an entry.
RECOGNIZED_ROLES = (
    "prd",
    "architecture",
    "constitution",
    "ui-design",
    "conventions",
    "guide",
)

# Entry grammar (skills/_shared/doc-catalogue.md): `- ` then a backtick-quoted path, then three
# ` — `-separated fields. The separator is the em dash with a space on each side, matching every
# other list-style block in the plugin; the split is capped at 3 so the FINAL field (the summary)
# may contain its own em dashes.
_BULLET_RE = re.compile(r"^-\s+(.*)$")
_BACKTICK_SPAN_RE = re.compile(r"`([^`]+)`")
_FIELD_SEPARATOR = " — "
_FIELD_SPLIT_MAXSPLIT = 3


def _is_safe_relative_path(raw_path):
    """Reject a catalogue path that is absolute or climbs out of the vantage with ``..``.

    The catalogue is the consuming repo's own confirmed config, so this is not a trust boundary in
    the security sense — but an entry is handed to sub-agents and read verbatim, and a path escaping
    the checkout would point a reader at a file outside the tree it is supposed to be grounding on
    (the exact failure the workspace-path discipline in architecture.md §6 exists to prevent). Such
    an entry is skipped like any other malformed line.
    """
    if not raw_path or raw_path.startswith("/") or raw_path.startswith("~"):
        return False
    if Path(raw_path).is_absolute():
        return False
    return ".." not in Path(raw_path).parts


def _parse_entry_line(raw_line):
    """One interior line -> ``(entry, reason)``: the entry dict and ``None``, or ``None`` and why the
    line is not an entry (``None`` reason too for a blank line, which is not an error)."""
    if not raw_line.strip():
        return None, None
    bullet = _BULLET_RE.match(raw_line.strip())
    if bullet is None:
        return None, "not a `- ` list item"
    parts = bullet.group(1).split(_FIELD_SEPARATOR, _FIELD_SPLIT_MAXSPLIT)
    if len(parts) < 4:
        return None, "fewer than four ` — `-separated fields"
    span = _BACKTICK_SPAN_RE.search(parts[0])
    if span is None:
        return None, "no backtick-quoted path"
    rel_path = span.group(1).strip()
    if not _is_safe_relative_path(rel_path):
        return None, "path is absolute or leaves the checkout"
    authority = parts[2].strip().lower()
    if authority not in AUTHORITY_VALUES:
        return None, "authority is not `binding` or `informative`"
    return (
        {
            "path": rel_path,
            "role": parts[1].strip().lower(),
            "authority": authority,
            "summary": parts[3].strip(),
        },
        None,
    )


def parse_catalogue_entries(interior_lines):
    """Pure text -> entries. ``interior_lines`` is the block interior as a list of lines (exactly
    what ``config_block.read_block_anywhere`` returns); no filesystem access happens here, so this
    is unit-testable against literal text and reusable by any future at-ref reader.

    Returns a list of ``{"path", "role", "authority", "summary"}`` dicts, in declaration order.
    A line that is blank, is not a ``- `` bullet, carries no backtick-quoted path, yields fewer than
    four fields, names an unsafe path, or whose ``authority`` is outside the closed pair is
    **skipped** — best-effort, non-raising, the posture every block scan in the plugin shares (a
    reader never stops on a malformed block). Skipping is why every writer runs ``check`` before it
    commits: a skipped entry is a document that silently stops grounding anything.
    """
    entries = []
    for raw_line in interior_lines:
        entry, _reason = _parse_entry_line(raw_line)
        if entry is not None:
            entries.append(entry)
    return entries


def read_catalogue(vantage_path):
    """Read the catalogue from ``vantage_path`` and resolve each entry's presence there.

    ``vantage_path`` is the checkout the documents are read at — **the same vantage as the documents
    themselves** (the planner's asserted grounding checkout at its ``plan_ref``; the drafter's
    ambient root). Reading at the working vantage is the coherent choice — a branch that adds a
    document *and* its catalogue entry must ground on both. (Until v3.x this contrasted with gate
    config, which was pinned to ``origin/main``; that pin is retired, so every config family now
    reads at the working vantage and this is no longer a special case.)

    Returns ``(entries, notices)``:

    - ``entries`` — the parsed entries, each additionally carrying ``present`` (the path exists at
      this vantage as a file **or** a directory), ``kind`` (``"file"`` | ``"dir"`` when present,
      ``None`` when absent), ``members`` (a ``dir`` entry's documents — see ``_dir_members`` — else
      ``None``) and ``abs_path`` (absolute path when present, else ``None``). A declared-but-missing
      document is **reported, not dropped**: the caller raises it as an attention line, since a stale
      entry and legitimate branch drift look identical from here and only the operator can tell them
      apart.
    - ``notices`` — ``[DOC_CATALOGUE_ABSENT]`` when no well-formed block was found (no
      ``docs/README.md``, no block in it, or a malformed one — ``read_block_anywhere`` already treats
      malformed as absent), else ``[]``. A block that is *present but empty* is a repo explicitly
      declaring no grounding documents, which is not the same fact as no declaration at all, so it
      yields ``([], [])``; a caller wanting "nothing to read" keys off ``entries``, and one wanting
      "tell the operator to author a catalogue" keys off the notice.
    """
    present, interior_lines, _source_file = config_block.read_block_anywhere(
        vantage_path, CATALOGUE_MARKER, candidate_filenames=(CATALOGUE_FILE,)
    )
    if not present:
        return [], [DOC_CATALOGUE_ABSENT]

    entries = parse_catalogue_entries(interior_lines)
    vantage = Path(vantage_path)
    for entry in entries:
        doc_path = vantage / entry["path"]
        kind = "dir" if doc_path.is_dir() else "file" if doc_path.is_file() else None
        entry["present"] = kind is not None
        entry["kind"] = kind
        entry["members"] = _dir_members(doc_path, entry["path"]) if kind == "dir" else None
        entry["abs_path"] = str(doc_path) if kind is not None else None
    return entries, []


def _dir_members(dir_path, rel_path):
    """A ``dir`` entry's documents: the sorted repo-root-relative paths of the **non-hidden regular
    files directly inside it**.

    One level, by contract (``skills/_shared/doc-catalogue.md`` §"Directory entries"): a nested set
    is declared as its own entry, so the repo — not this listing — decides how deep its grounding
    goes, and a docs tree with an assets or archive subdirectory does not drag those in. No extension
    filter: what counts as a document is the repo's call (a directory of `.md`, `.adoc` or `.txt`
    alike), and the repo made it by naming the directory. Dotfiles are skipped as tooling, not docs.
    An unreadable directory yields ``[]`` rather than raising — the module's non-raising posture; the
    entry still reads as present, so the operator sees a set with nothing in it, not a stale path.
    """
    prefix = rel_path.rstrip("/")
    try:
        children = list(dir_path.iterdir())
    except OSError:
        return []
    return sorted(
        "%s/%s" % (prefix, child.name)
        for child in children
        if child.is_file() and not child.name.startswith(".")
    )


def entry_for_role(entries, role):
    """The first entry whose ``role`` matches, or ``None``.

    Role order is the repo's declaration order, so "first" is the repo's own preference when it
    declares two documents in one role (two guides, a PRD and a legacy PRD). Used by
    ``prep_drafter`` to keep its long-standing ``facts.repo_context.docs.prd`` fact stable across the
    migration off hardcoded PRD candidate paths.
    """
    for entry in entries:
        if entry.get("role") == role:
            return entry
    return None


def missing_entry_paths(entries):
    """The declared paths that do not exist at the vantage — the caller's attention-line input."""
    return [entry["path"] for entry in entries if not entry.get("present")]


# ---- check: the write-side gate ----


def _scan_block(text):
    """``text`` -> ``(status, interior_lines, first_interior_line)``: ``status`` is ``absent`` |
    ``ok`` | ``dup`` | ``open``, the same classes ``config_block.py list`` reports, read from one
    file's text; ``first_interior_line`` is the 1-based file line of the interior's first line."""
    lines = text.splitlines()
    open_count, close_count, open_index, close_index = config_block._scan_marker(lines, CATALOGUE_MARKER)
    if open_count == 0:
        return "absent", [], 0
    if open_count > 1 or close_count > 1:
        return "dup", [], 0
    if open_count != close_count or close_index < open_index:
        return "open", [], 0
    # `_scan_marker`'s indices are 1-based, so the interior's first line is `open_index + 1`.
    return "ok", lines[open_index : close_index - 1], open_index + 1


def _read_base_text(root, base_ref):
    """The catalogue file's text at ``base_ref`` (``git show`` inside the script — prompts never do
    ref arithmetic, architecture.md §10). ``None`` when the ref or the file is not there."""
    try:
        result = process.run(["git", "show", "%s:%s" % (base_ref, CATALOGUE_FILE)], cwd=str(root))
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _confirm_changes(base_entries, entries):
    """The changes that stop or start a document grounding the work, keyed by path: a ``binding``
    entry added or removed, and any ``authority`` change. A repointed binding entry reads as one
    removal plus one addition, which is what it is to a reader. Summary and role edits are absent on
    purpose — they are the routine upkeep any session makes without asking."""
    base_by_path = {entry["path"]: entry for entry in base_entries}
    by_path = {entry["path"]: entry for entry in entries}
    changes = []
    for entry in entries:
        before = base_by_path.get(entry["path"])
        if before is None:
            if entry["authority"] == AUTHORITY_BINDING:
                changes.append({"kind": "binding_added", "path": entry["path"]})
        elif before["authority"] != entry["authority"]:
            changes.append(
                {
                    "kind": "authority_changed",
                    "path": entry["path"],
                    "from": before["authority"],
                    "to": entry["authority"],
                }
            )
    for before in base_entries:
        if before["path"] not in by_path and before["authority"] == AUTHORITY_BINDING:
            changes.append({"kind": "binding_removed", "path": before["path"]})
    return changes


def build_check(root, base_ref=None):
    """Check the catalogue in ``root``'s working tree before a writer commits it.

    Returns ``(payload, notices, decision)``:

    - ``decision`` — ``DOC_CATALOGUE_MALFORMED`` when the markers are duplicated or unterminated, a
      non-blank interior line is not an entry readers accept, a path repeats, or an entry names a
      path absent from ``root``. ``None`` otherwise.
    - ``payload`` — ``file`` (absolute), ``present`` (a well-formed block exists), ``marker_status``
      (``absent`` | ``ok`` | ``dup`` | ``open``), ``entries`` (the
      count), ``malformed_lines`` (``{"line", "text", "reason"}``, 1-based within the file),
      ``missing_paths``, ``base_ref``, and ``confirm``: the ``_confirm_changes`` list against the
      block at ``base_ref``, or ``None`` when no base was given or it could not be read. A base with
      no block compares as an empty catalogue, so every ``binding`` entry is new.
    - ``notices`` — ``DOC_CATALOGUE_BASE_UNAVAILABLE`` when ``base_ref`` was given but unreadable.

    An absent block is not malformed: a repo may declare no catalogue, and a writer that removed the
    block sees every former ``binding`` entry in ``confirm``.
    """
    root_path = Path(root)
    file_path = root_path / CATALOGUE_FILE
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    status, interior, first_interior_line = _scan_block(text)

    payload = {
        "file": str(file_path),
        "present": status == "ok",
        "marker_status": status,
        "entries": 0,
        "malformed_lines": [],
        "missing_paths": [],
        "base_ref": base_ref,
        "confirm": None,
    }
    notices = []

    if status in ("dup", "open"):
        summary = (
            "docs/README.md has %s `<!-- doc-catalogue -->` markers, so every reader treats the "
            "catalogue as absent" % ("duplicate" if status == "dup" else "unterminated")
        )
        return payload, notices, needs_decision(
            DOC_CATALOGUE_MALFORMED,
            summary,
            context={"file": str(file_path), "marker_status": status},
            options=["Fix the markers, then re-run the check"],
        )

    entries = []
    seen = set()
    for offset, raw_line in enumerate(interior):
        entry, reason = _parse_entry_line(raw_line)
        if entry is not None and entry["path"] in seen:
            entry, reason = None, "path already listed"
        if reason is not None:
            payload["malformed_lines"].append(
                {"line": first_interior_line + offset, "text": raw_line, "reason": reason}
            )
            continue
        if entry is None:
            continue
        seen.add(entry["path"])
        entries.append(entry)
        if not (root_path / entry["path"]).exists():
            payload["missing_paths"].append(entry["path"])
    payload["entries"] = len(entries)

    if base_ref:
        base_text = _read_base_text(root_path, base_ref)
        if base_text is None:
            notices.append(DOC_CATALOGUE_BASE_UNAVAILABLE)
        else:
            base_status, base_interior, _first = _scan_block(base_text)
            base_entries = parse_catalogue_entries(base_interior) if base_status == "ok" else []
            payload["confirm"] = _confirm_changes(base_entries, entries)

    if payload["malformed_lines"] or payload["missing_paths"]:
        problems = []
        if payload["malformed_lines"]:
            problems.append("%d line(s) readers would skip" % len(payload["malformed_lines"]))
        if payload["missing_paths"]:
            problems.append("%d path(s) absent from the checkout" % len(payload["missing_paths"]))
        return payload, notices, needs_decision(
            DOC_CATALOGUE_MALFORMED,
            "the doc catalogue has %s" % " and ".join(problems),
            context={
                "file": str(file_path),
                "malformed_lines": payload["malformed_lines"],
                "missing_paths": payload["missing_paths"],
            },
            options=["Fix the listed lines, then re-run the check"],
        )
    return payload, notices, None


def main(argv):
    parser = argparse.ArgumentParser(prog="doc_catalogue.py", description="Check the doc catalogue before a commit.")
    sub = parser.add_subparsers(dest="subcommand")
    p_check = sub.add_parser("check")
    p_check.add_argument("--root", required=True, help="the checkout whose docs/README.md is being landed")
    p_check.add_argument(
        "--base-ref",
        default=None,
        help="the ref to measure `confirm` against (default: origin/<default branch>)",
    )
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand != "check":
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    base_ref = args.base_ref
    if base_ref is None:
        import workspace  # deferred: only the CLI derives a default; preps pass their own base

        try:
            base_ref = "origin/%s" % workspace.default_branch(args.root)
        except SystemExit:
            base_ref = None
    payload, notices, decision = build_check(args.root, base_ref=base_ref)
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
    else:
        emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
