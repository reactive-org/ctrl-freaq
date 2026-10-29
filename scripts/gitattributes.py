#!/usr/bin/env python3
"""gitattributes.py — the single write path for the consuming repo's `linguist-generated` block in its
repo-root `.gitattributes` (#181; architecture.md §7: a write that has a bundled script goes through it).

Setup proposes `linguist-generated` lines for the generated paths it finds, so GitHub collapses those
files' diffs in the PR view and the evaluator's #163 `generated` exemption
(`review_threads.narrow_out_of_plan`, which reads the committed attribute at the merge base and the
gated head) can apply to them. This script owns exactly one block, fenced by two comment lines:

    # ctrl-freaq:linguist-generated:begin
    <pattern> linguist-generated
    # ctrl-freaq:linguist-generated:end

and leaves every line outside the fence byte-identical. File I/O only: no `git`, no `gh`.

Subcommands (each emits one architecture.md §3 envelope on stdout):

    gitattributes.py read <file>
        `ok` payload `{present, lines, path}` — whether the block exists, its interior lines, and the
        file read. An absent file or block is `present: false, lines: []`.

    gitattributes.py upsert <file> <body-path> [--dry-run]
        Replace the block's interior with the body file's non-blank lines, or append the block when it
        is absent, creating the file when it is absent. Every body line must be
        `<pattern> linguist-generated`; any other line is a usage error (exit 2, nothing written). `ok`
        payload `{path, changed, created, dry_run, lines}`; `changed: false` leaves the file untouched.

A malformed fence — two begin lines, two end lines, a begin with no end, or an end before a begin — is a
`MARKER_AMBIGUOUS` `needs_decision` envelope from both subcommands, with nothing written, as
`config_block.py` does for a duplicated marker. `prep_setup.py` composes `build_read` in-process.

Exit codes (architecture.md §3): 0 with an envelope; 2 on a usage error (no envelope); 1 when the file
exists but cannot be read as UTF-8 text (`GITATTRIBUTES_UNREADABLE:` on stderr, nothing written).
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib.decisions import MARKER_AMBIGUOUS, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_needs_decision, emit_ok  # noqa: E402

BEGIN = "# ctrl-freaq:linguist-generated:begin"
END = "# ctrl-freaq:linguist-generated:end"
# One body line: a whitespace-free pattern, then exactly the `linguist-generated` attribute.
_BODY_LINE_RE = re.compile(r"^\S+[ \t]+linguist-generated$")


class Unreadable(Exception):
    """The file exists but cannot be read as UTF-8 text (permissions, a directory, bad bytes). A hard
    failure, not a decision: there is no fence to judge until a person makes the file readable."""


class _FenceMalformed(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _read_text(path):
    """The file's text with its line endings untouched (`newline=""`), or `None` when absent. Raises
    `Unreadable` when it exists but cannot be read as UTF-8 text."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise Unreadable("%s: %s" % (path, exc)) from exc


def _locate(lines):
    """`(begin_index, end_index)` of the fence lines in `lines` (each keeping its line ending), or
    `None` when the file has no block. Raises `_FenceMalformed` on a fence a reader could not trust.
    Surrounding whitespace is ignored, so a hand edit that leaves a trailing space still finds its
    block rather than having a second one appended beside it."""
    begins = [i for i, line in enumerate(lines) if line.strip() == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == END]
    if not begins and not ends:
        return None
    if len(begins) > 1:
        raise _FenceMalformed("two begin lines")
    if len(ends) > 1:
        raise _FenceMalformed("two end lines")
    if not ends:
        raise _FenceMalformed("a begin line with no end line")
    if not begins:
        raise _FenceMalformed("an end line with no begin line")
    if ends[0] < begins[0]:
        raise _FenceMalformed("the end line comes before the begin line")
    return begins[0], ends[0]


def _ambiguous(op, path, reason):
    return needs_decision(
        MARKER_AMBIGUOUS,
        summary="the ctrl-freaq linguist-generated block in %s is malformed: %s" % (path, reason),
        context={"op": op, "file": str(path), "reason": reason, "begin": BEGIN, "end": END},
        options=["fix the block's two fence lines in %s by hand, then re-run" % path],
    )


def invalid_body_lines(lines):
    """The lines that are not `<pattern> linguist-generated` (blank lines are skipped, not invalid)."""
    return [line for line in lines if line.strip() and not _BODY_LINE_RE.match(line.strip())]


def build_read(file):
    """`(payload, notices, decision)` — never emits. `payload` is `{present, lines, path}`; a malformed
    fence is `(None, [], MARKER_AMBIGUOUS decision)`."""
    path = Path(file)
    text = _read_text(path)
    if text is None:
        return {"present": False, "lines": [], "path": str(path)}, [], None
    lines = text.splitlines(keepends=True)
    try:
        located = _locate(lines)
    except _FenceMalformed as exc:
        return None, [], _ambiguous("read", path, exc.reason)
    if located is None:
        return {"present": False, "lines": [], "path": str(path)}, [], None
    begin, end = located
    interior = [line.rstrip("\r\n") for line in lines[begin + 1:end]]
    return {"present": True, "lines": interior, "path": str(path)}, [], None


def build_upsert(file, body_lines, dry_run=False):
    """`(payload, notices, decision)` — never emits. Writes the file only when the result differs and
    not `dry_run`. `body_lines` must already pass `invalid_body_lines` (a `ValueError` otherwise: the
    wrapper turns that into exit 2 before calling this)."""
    if invalid_body_lines(body_lines):
        raise ValueError("body lines must each be `<pattern> linguist-generated`")
    body = [line.strip() for line in body_lines if line.strip()]
    path = Path(file)
    text = _read_text(path)
    created = text is None
    lines = [] if created else text.splitlines(keepends=True)
    try:
        located = _locate(lines)
    except _FenceMalformed as exc:
        return None, [], _ambiguous("upsert", path, exc.reason)
    block = [BEGIN + "\n"] + [line + "\n" for line in body] + [END + "\n"]
    if located is None:
        prefix = list(lines)
        if prefix and not prefix[-1].endswith(("\n", "\r")):
            prefix[-1] += "\n"
        new_lines = prefix + block
    else:
        begin, end = located
        # The fence lines keep their own endings; only the interior is replaced.
        new_lines = lines[:begin + 1] + block[1:-1] + lines[end:]
    new_text = "".join(new_lines)
    changed = new_text != (text or "") or created
    if changed and not dry_run:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
    payload = {
        "path": str(path),
        "changed": changed,
        "created": created,
        "dry_run": dry_run,
        "lines": body,
    }
    return payload, [], None


def _emit(payload, notices, decision):
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
    else:
        emit_ok(payload=payload, notices=notices)
    return EXIT_OK


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="op", required=True)
    p_read = sub.add_parser("read")
    p_read.add_argument("file")
    p_upsert = sub.add_parser("upsert")
    p_upsert.add_argument("file")
    p_upsert.add_argument("body_path")
    p_upsert.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.op == "read":
        try:
            return _emit(*build_read(args.file))
        except Unreadable as exc:
            sys.stderr.write("GITATTRIBUTES_UNREADABLE: %s\n" % exc)
            return 1

    try:
        with open(args.body_path, "r", encoding="utf-8") as fh:
            body_lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("MISSING_BODY_FILE: %s: %s\n" % (args.body_path, exc))
        return EXIT_USAGE_ERROR
    bad = invalid_body_lines(body_lines)
    if bad:
        sys.stderr.write(
            "INVALID_BODY_LINE: each line must be `<pattern> linguist-generated`: %r\n" % bad[0]
        )
        return EXIT_USAGE_ERROR
    try:
        return _emit(*build_upsert(args.file, body_lines, dry_run=args.dry_run))
    except Unreadable as exc:
        sys.stderr.write("GITATTRIBUTES_UNREADABLE: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
