#!/usr/bin/env python3
"""config_block.py — deterministic, idempotent read/write of the marker-delimited configuration
blocks the github-pipeline skills consume (e.g. `<!-- issue-resolver-fast-checks -->` … `<!--
/issue-resolver-fast-checks -->`), under the architecture.md §3 envelope.

This is the S21 Python port of v1's ``scripts/config-block.sh`` — the v1 file is a frozen
behavioral reference (still used by v1 callers until S20) and is **not** edited by this port. The
behavior contract is preserved exactly, re-expressed in envelope terms: same subcommands, same
argument shape, same canonical block form, same idempotency/no-op guarantees, same nonexistent-
file handling, same duplicate/unterminated-marker refusals. What changes is the wire format: `read`
still prints a block's interior to stdout as raw text, `list` still prints `<status> <name>` lines
as raw text, and `upsert`/`remove` now emit an architecture.md §3 JSON envelope (`{"status": "ok",
...}` or `{"status": "needs_decision", ...}`) instead of v1's bare `{"op": ..., "changed": ...}`
object, plus proper usage-error exit code 2.

This module does **no** GitHub or git I/O and shells out to nothing — it only reads/writes local
Markdown files. `workspace.py` (§6) delegates block reads to it for `<!-- worktree-setup/teardown
-->` discovery; `skills/setup/` uses it for all block I/O.

Canonical block form: the line ``<!-- NAME -->`` on its own line, the block's interior lines, and
the line ``<!-- /NAME -->`` on its own line — each delimiter tolerant of surrounding leading/
trailing whitespace on its line, matched only after trimming. ``<marker-name>`` is the bare name
without comment syntax (e.g. ``issue-resolver-fast-checks``), constrained to ``^[A-Za-z0-9:_-]+$``
so a stray name can never smuggle in comment syntax that would confuse the delimiter scan (v1's
same rule, restated in Python-native terms rather than as a regex fed to `awk`).

Subcommands:
    config_block.py read   <file> <marker-name>
    config_block.py list   <file>
    config_block.py upsert <file> <marker-name> <body-path> [--dry-run] [--prepend]
    config_block.py remove <file> <marker-name>             [--dry-run]

``read``/``list`` print raw text (not an envelope) on success, matching v1 exactly, so a caller can
capture a block's contents or the inventory directly:
    - `read` exit 0: interior lines to stdout, one per line, no trailing envelope.
    - `read` exit 3: the named block is absent (this **includes** a nonexistent file — v1 treats
      "file doesn't exist" as "block doesn't exist," not a distinct error; verified against v1
      directly rather than inferred from prose).
    - `read` exit 4: `MARKER_AMBIGUOUS` — the marker's delimiter appears more than once.
    - `read` exit 5: the marker is unterminated (open with no close) or closes before it opens.
    - `list` exit 0 always, even against a nonexistent file (empty stdout — "no file" is "no
      blocks," not an error, matching v1 and docs/specs/setup.md's explicit note on this).
      Prints one `<status> <name>` line per discovered marker name: `ok` (well-formed, matching
      close present), `open` (unterminated), `dup` (declared more than once).

``upsert``/``remove`` emit exactly one architecture.md §3 JSON envelope on stdout:
    - `upsert` ok: `{"status": "ok", "op": "upsert", "marker": ..., "file": ..., "changed": ...,
      "dry_run": ..., "body_bytes": ..., "body_sha256": ..., "notices": []}`.
    - `remove` ok: `{"status": "ok", "op": "remove", "marker": ..., "file": ..., "changed": ...,
      "dry_run": ..., "notices": []}` (no `body_bytes`/`body_sha256` — remove stages no body).
    - Either, on a duplicate marker where the contract expects one: `{"status": "needs_decision",
      "decision": {"code": "MARKER_AMBIGUOUS", ...}, "notices": []}`, exit 0 (`needs_decision` is
      a valid outcome, not an error, per architecture.md §3).
    - `changed: false` means the file is already in the desired state and was **not touched on
      disk at all** — not even its mtime — matching v1's `finish_write`'s `cmp -s` short-circuit
      exactly (this is the idempotency guarantee the whole script exists to make deterministic:
      re-running `upsert` with the same body is a byte-level no-op, never a model-landed edit that
      merely happens to reproduce the same bytes).

Exit codes (usage/malformed-input paths only — these are NOT envelope-carrying; architecture.md §3:
exit 2 means no envelope is emitted):
    2   usage error, invalid marker name (`INVALID_MARKER_NAME: ...` on stderr), or a missing
        staged body file for `upsert` (`MISSING_BODY_FILE: ...` on stderr) — v1's own usage-error
        exit code, preserved verbatim rather than folded into a decision (these are malformed
        *invocations*, not a state the operator resolves via a decision card).
    3   (`read` only) the named block is absent.
    4   the named marker appears more than once — surfaced as exit code for `read`, and as a
        `MARKER_AMBIGUOUS` needs_decision envelope (exit 0) for `upsert`/`remove`, since those two
        already emit an envelope and architecture.md §3's decision-code path is how they express
        "found a state only the operator can resolve."
    5   the named marker is unterminated / closes before it opens (`read` only — `upsert`/`remove`
        also refuse this shape, but v1 has no dedicated decision code for it distinct from
        malformed content, so it stays a bare exit 5 on all four subcommands for consistency with
        `read`, matching v1's own uniform treatment).

Marker discovery order: `list`'s per-name ordering is **not** part of the v1 contract — the
`.sh` implementation's order is `awk`'s associative-array iteration order (verified directly:
hash order, not file order, not alphabetical), an implementation artifact of that interpreter, not
a documented behavior. This port instead orders `list` output deterministically by each marker's
first line of first appearance in the file, so the same input always produces the same output
across runs and across platforms — a strictly stronger guarantee than v1 offered, and consistent
with "same output fields" (the fields are `status name`; the field *order* was never specified).
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib.decisions import MARKER_AMBIGUOUS, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_needs_decision, emit_ok  # noqa: E402
from pipelib.hashing import sha256_hex  # noqa: E402

# Exit codes not covered by pipelib.envelope's EXIT_OK / EXIT_USAGE_ERROR — v1-specific codes this
# port preserves verbatim (module docstring's "Exit codes" section).
EXIT_BLOCK_ABSENT = 3
EXIT_MARKER_AMBIGUOUS = 4
EXIT_MARKER_UNTERMINATED = 5

# Marker names are matched as literal strings (never as regex) against a trimmed line, but this
# constrains the *input* name anyway — v1's own rule, restated here rather than fed to `awk` as a
# string-interpolated comparison target — so a stray name can never smuggle in comment syntax that
# would confuse the delimiter scan.
_VALID_MARKER_NAME_RE = re.compile(r"^[A-Za-z0-9:_-]+$")

# The whole-line delimiter shape `list` scans for: an optional leading `/` (close), then the name,
# each tolerant of surrounding whitespace on the line (matched after `.strip()`, not by this
# regex's own anchors doing the trimming — see `_scan_all_marker_lines`).
_DELIMITER_LINE_RE = re.compile(r"^<!--\s*(/?)([A-Za-z0-9:_-]+)\s*-->$")

# Default multi-file candidate list for `read_block_anywhere` (below): `COMMANDS.md`, `CLAUDE.md`,
# then one level of `@`-include from either — the discovery order every config-block consumer in
# this repo shares (workspace.py's worktree-setup/teardown blocks; prep_resolver.py's/
# prep_evaluator.py's gate-config blocks). A caller with a genuinely different candidate set (none
# exists today) may override via `read_block_anywhere`'s `candidate_filenames` kwarg.
DEFAULT_CONFIG_CANDIDATE_FILES = ("COMMANDS.md", "CLAUDE.md")


def _die_usage(parser):
    parser.print_usage(sys.stderr)
    sys.exit(EXIT_USAGE_ERROR)


def _validate_marker_name(name):
    """Exit 2 with `INVALID_MARKER_NAME: ...` on stderr if `name` fails the allowed-charset check
    — v1's `validate_name`, same message text and exit code (docstring's "Exit codes" table).
    """
    if not _VALID_MARKER_NAME_RE.match(name):
        sys.stderr.write("INVALID_MARKER_NAME: %s (allowed: A-Z a-z 0-9 : _ -)\n" % name)
        sys.exit(EXIT_USAGE_ERROR)


def _read_lines_or_empty(file_path):
    """Read `file_path` as a list of lines with trailing newlines stripped (each element is one
    logical line's content, matching `awk`'s `$0`/record semantics — v1's `AWK_SCAN` buffers every
    line into `lines[n]` the same way). Returns `[]` when the file does not exist, so `upsert`
    (which treats an absent file as empty input, per v1's `input="/dev/null"` fallback) and
    `remove`/`list`'s "no file ⇒ no blocks" degrade to the same empty-input path as a genuinely
    empty file, without special-casing existence at every call site.
    """
    path = Path(file_path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    if text == "":
        return []
    # splitlines(keepends=False) drops the line terminators themselves; a trailing newline in the
    # source produces no phantom empty final element (matching `awk`'s per-record read, which
    # never yields a record for the text after a file's final "\n").
    return text.splitlines()


def _scan_marker(lines, name):
    """Re-implements v1's `AWK_SCAN` for one target `name`: returns `(open_count, close_count,
    open_index, close_index)`, all 0/None-safe 1-based indices into `lines` (1-based to mirror the
    awk source's `n`/`oi`/`ci` exactly, so the index arithmetic in `_render_*` below reads the same
    way as `run_upsert`/`run_remove` in the `.sh`).

    A line "is" an open/close delimiter for `name` after `.strip()` equals the exact string
    `<!-- NAME -->` / `<!-- /NAME -->` — literal string comparison, not the `_DELIMITER_LINE_RE`
    pattern (which is `list`'s tool for discovering *any* marker name present; a targeted scan for
    one already-known name only needs an exact-match compare, matching v1's `openm`/`closem`
    string-equality check exactly rather than re-deriving it through a name-extracting regex).
    """
    open_marker = "<!-- %s -->" % name
    close_marker = "<!-- /%s -->" % name
    open_count = close_count = 0
    open_index = close_index = 0
    for i, raw_line in enumerate(lines, start=1):
        trimmed = raw_line.strip()
        if trimmed == open_marker:
            open_count += 1
            if open_index == 0:
                open_index = i
        elif trimmed == close_marker:
            close_count += 1
            if close_index == 0:
                close_index = i
    return open_count, close_count, open_index, close_index


# ---------------------------------------------------------------------------
# Multi-file candidate-block discovery (S12 promotion) — the "scan COMMANDS.md, CLAUDE.md, then
# one level of `@`-include from either, first well-formed block wins" loop three consumers
# (workspace.py's worktree-hook blocks; prep_resolver.py's/prep_evaluator.py's gate-config blocks)
# independently reimplemented before this promotion. Public (no leading underscore) so a caller
# outside this module composes it directly (architecture.md §2's in-process composition), the same
# way workspace.py already composes `_read_lines_or_empty`/`_scan_marker` in-process. Only
# prep_resolver.py and prep_evaluator.py are refactored to call this as of S12 — workspace.py's own
# `_candidate_hook_files`/`_read_hook_block_from_file` are a similar but distinctly-shaped read
# (they return `(present, interior_lines)`, no source-file tracking, and are tolerant of a
# malformed block in one candidate the way `read_block_anywhere` also is) and are left as-is here;
# folding them in too is future, unauthorized-for-this-step surgery, not a correctness gap.
# ---------------------------------------------------------------------------


def extract_include_tokens(text):
    """Return the `@`-include path tokens found in `text` — matches an `@`-prefixed token that
    looks like a file path (has a slash or a dotted extension), which excludes bare `@mentions`.
    Pure text -> tokens, no filesystem access: `find_includes_one_level` composes it over a file
    on disk, and `workspace.py` composes it for worktree-hook discovery, so the
    working-tree and at-ref discovery loops share ONE include parser.
    """
    return [
        tok
        for tok in re.findall(r"@([A-Za-z0-9._/-]+)", text)
        if re.search(r"(/|\.[A-Za-z0-9]+$)", tok)
    ]


def find_includes_one_level(file_path):
    """Return the `@`-included paths (one level) found in `file_path` — see
    `extract_include_tokens` for the token grammar. Existence is re-checked by the caller
    (`candidate_config_files`), so a stray match here is harmless.
    """
    path = Path(file_path)
    if not path.is_file():
        return []
    return extract_include_tokens(path.read_text(encoding="utf-8"))


def candidate_config_files(root, candidate_filenames=DEFAULT_CONFIG_CANDIDATE_FILES):
    """Ordered candidate list for config-block discovery: `candidate_filenames` (default
    `COMMANDS.md`, `CLAUDE.md`), then every file either one `@`-includes (one level)."""
    root_path = Path(root)
    candidates = [root_path / name for name in candidate_filenames]
    for root_file in list(candidates):
        for inc in find_includes_one_level(root_file):
            inc_path = Path(inc)
            candidates.append(inc_path if inc_path.is_absolute() else root_path / inc)
    return candidates


def candidate_paths_with(reader, candidate_filenames=DEFAULT_CONFIG_CANDIDATE_FILES):
    """`candidate_config_files` over an injected reader: the candidate filenames, then every path
    either one `@`-includes (one level), each **as discovery produced it** — a candidate filename or
    a relative include as written (repo-relative), or an absolute include as written. `reader(path)
    -> str | None` returns a candidate's text, `None` when it does not exist."""
    candidates = list(candidate_filenames)
    for name in candidate_filenames:
        text = reader(name)
        if text:
            candidates.extend(extract_include_tokens(text))
    return candidates


def read_block_with(reader, marker_name, candidate_filenames=DEFAULT_CONFIG_CANDIDATE_FILES):
    """`read_block_anywhere`'s discovery (candidate order, one-level includes, first well-formed
    block wins, a malformed candidate skipped) over an injected `reader(path) -> str | None`, so a
    second source — `sensitive_paths.py`'s merge-base read (#96) — shares this ONE discovery loop
    instead of growing a second parser. Returns `(present, interior_lines, source_or_none)`, with
    `source` the discovery path exactly as `candidate_paths_with` produced it.
    """
    for candidate in candidate_paths_with(reader, candidate_filenames=candidate_filenames):
        text = reader(candidate)
        lines = text.splitlines() if text else []
        open_count, close_count, open_index, close_index = _scan_marker(lines, marker_name)
        if open_count == 0:
            continue
        if open_count > 1 or close_count > 1:
            continue
        if open_count != close_count or close_index < open_index:
            continue
        interior = lines[open_index : close_index - 1]
        return True, interior, candidate
    return False, [], None


def read_block_anywhere(root, marker_name, candidate_filenames=DEFAULT_CONFIG_CANDIDATE_FILES):
    """Read the first well-formed `<!-- marker_name -->` block across the candidate config files
    (`candidate_config_files`) — first present, well-formed block wins. Delegates to
    `read_block_with` with a working-tree reader, composing this module's own non-emitting scan
    primitives — never the CLI/`sys.exit` surface (`run_read`), matching how `workspace.py` already
    composes them in-process. A malformed block (duplicate/unterminated) in one candidate file is
    treated exactly like an absent one — try the next candidate — matching every existing caller's
    own graceful-degradation rule (the closed decision-code set has no malformed-block code).

    Returns `(present, interior_lines, source_file_or_none)`, the source an absolute path (the root
    joined with a relative candidate, or an absolute include itself).
    """
    root_path = Path(root)

    def _resolve(path):
        return Path(path) if Path(path).is_absolute() else root_path / path

    def _worktree_reader(path):
        resolved = _resolve(path)
        if not resolved.is_file():
            return None
        return resolved.read_text(encoding="utf-8")

    present, interior, source = read_block_with(
        _worktree_reader, marker_name, candidate_filenames=candidate_filenames
    )
    return present, interior, (str(_resolve(source)) if present else None)


def _read_body_lines(body_path):
    """Read the staged body file as a list of lines (trailing newlines stripped), matching v1's
    `emit_body()` — which streams the body file line-by-line via `getline` and re-adds one newline
    per line, canonicalizing whatever trailing-newline state the body file is in (present, absent,
    or a body with embedded blank lines) so the append path and the replace path always produce
    identical interior bytes regardless of how the caller staged the file.
    """
    with open(body_path, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    if text == "":
        return []
    return text.splitlines()


# ---- read ----


def build_read(file, marker_name):
    """Pure ``read`` core (architecture.md §2's pure-core pattern, S8 pattern lock): scan the named
    marker block and return ``(interior_lines, exit_code)`` — **never writes stdout, never exits**.

    ``read`` is one of the two raw-text (non-envelope) subcommands, so its core returns the exit
    code the wrapper should apply rather than a §3 ``decision`` object (there is no envelope to
    carry a decision on this path; the wrapper writes the interior text on success and exits with
    the returned code otherwise):
      - ``(interior_lines, EXIT_OK)`` — the block is well-formed; ``interior_lines`` is the list of
        interior source lines the wrapper prints one-per-line.
      - ``(None, EXIT_BLOCK_ABSENT)`` — the block is absent (including a nonexistent file).
      - ``(None, EXIT_MARKER_AMBIGUOUS)`` — the delimiter appears more than once.
      - ``(None, EXIT_MARKER_UNTERMINATED)`` — open with no close, or closes before it opens.

    ``_validate_marker_name`` (a usage error, exit 2 on stderr) still fires in the wrapper before
    the core runs — a malformed *invocation* is not a returnable core outcome.
    """
    lines = _read_lines_or_empty(file)
    open_count, close_count, open_index, close_index = _scan_marker(lines, marker_name)
    if open_count == 0:
        return None, EXIT_BLOCK_ABSENT
    if open_count > 1 or close_count > 1:
        return None, EXIT_MARKER_AMBIGUOUS
    if open_count != close_count or close_index < open_index:
        return None, EXIT_MARKER_UNTERMINATED
    interior = lines[open_index:close_index - 1]  # lines is 0-indexed; open_index/close_index are 1-based
    return interior, EXIT_OK


def run_read(args):
    """Thin wrapper over :func:`build_read` (S8 pattern lock): validate the marker name, call the
    core, then write the block's interior to stdout one line per source line (no trailing envelope,
    module docstring) and exit with the core's code. Byte-identical to the pre-retrofit output.
    """
    _validate_marker_name(args.marker_name)
    interior, exit_code = build_read(args.file, args.marker_name)
    if exit_code != EXIT_OK:
        sys.exit(exit_code)
    out_lines = [line + "\n" for line in interior]
    sys.stdout.write("".join(out_lines))
    sys.exit(EXIT_OK)


# ---- list ----


def build_list(file):
    """Pure ``list`` core (S8 pattern lock): scan every discovered marker and return the list of
    ``(status, name)`` pairs, ordered by first line of first appearance (module docstring's "Marker
    discovery order" note) — **never writes stdout, never exits**. ``list`` is the other raw-text
    subcommand and has no failure path (exit 0 always, even against a nonexistent file), so this
    core returns just the ordered pairs; the wrapper renders them as ``<status> <name>`` lines.
    """
    lines = _read_lines_or_empty(file)
    open_counts = {}
    close_counts = {}
    first_seen_at = {}
    for i, raw_line in enumerate(lines, start=1):
        trimmed = raw_line.strip()
        match = _DELIMITER_LINE_RE.match(trimmed)
        if not match:
            continue
        is_close, name = match.group(1) == "/", match.group(2)
        if name not in first_seen_at:
            first_seen_at[name] = i
        if is_close:
            close_counts[name] = close_counts.get(name, 0) + 1
        else:
            open_counts[name] = open_counts.get(name, 0) + 1

    ordered_names = sorted(open_counts.keys(), key=lambda n: first_seen_at[n])
    pairs = []
    for name in ordered_names:
        opens = open_counts[name]
        closes = close_counts.get(name, 0)
        if opens > 1:
            status = "dup"
        elif closes >= 1:
            status = "ok"
        else:
            status = "open"
        pairs.append((status, name))
    return pairs


def run_list(args):
    """Thin wrapper over :func:`build_list` (S8 pattern lock): print `<status> <name>` per
    discovered marker, one per line (no envelope). Exit 0 always. Byte-identical to the
    pre-retrofit output.
    """
    pairs = build_list(args.file)
    out_lines = ["%s %s\n" % (status, name) for status, name in pairs]
    sys.stdout.write("".join(out_lines))
    sys.exit(EXIT_OK)


# ---- shared render for upsert's four placement shapes ----


def _render_upsert(lines, name, body_lines, prepend):
    """Re-implements v1's `run_upsert` awk `END` block: given the existing file's `lines`, the
    target marker `name`, the staged `body_lines`, and whether a *newly created* block should be
    prepended, return the new full line list — or raise `_MarkerAmbiguous` / `_MarkerUnterminated`
    on the same malformed-input shapes v1 refuses rather than guesses through.

    Four placement shapes, matching v1 exactly:
      1. An existing well-formed block: body replaces the interior in place, position unchanged.
      2. No existing block, `prepend=True`: new block at the very top, then one blank line (only
         if the file has content — `n>0 and trim(lines[1])!=""` in v1), then the original content.
      3. No existing block, `prepend=False` (default): original content, then one blank line (only
         if the file has content and its last line is non-blank), then the new block appended.
      (Shape 4 — `oc>1` — is not a placement shape; it's the ambiguity refusal below.)
    """
    open_count, close_count, open_index, close_index = _scan_marker(lines, name)
    if open_count > 1 or close_count > 1:
        raise _MarkerAmbiguous()
    if open_count != close_count:
        raise _MarkerUnterminated()

    open_marker = "<!-- %s -->" % name
    close_marker = "<!-- /%s -->" % name

    if open_count == 1:
        if close_index < open_index:
            raise _MarkerUnterminated()
        # Up to and including the open delimiter, then the body, then the close delimiter onward —
        # v1's `for(i=1;i<=oi;i++) ... emit_body() ... for(i=ci;i<=n;i++)`.
        return lines[:open_index] + body_lines + lines[close_index - 1:]

    if prepend:
        new_block = [open_marker] + body_lines + [close_marker]
        if lines and lines[0].strip() != "":
            return new_block + [""] + lines
        return new_block + lines

    new_block = [open_marker] + body_lines + [close_marker]
    if lines and lines[-1].strip() != "":
        return lines + [""] + new_block
    return lines + new_block


def _render_remove(lines, name):
    """Re-implements v1's `run_remove` awk `END` block: return `(new_lines, changed)`.

    `changed=False` with `new_lines` equal to the input means the block was already absent — v1's
    `if(oc==0){ ...; exit 0 }` branch, which the caller (`run_remove`) uses to skip the
    write/envelope machinery's dry-run/no-op distinction entirely (v1 short-circuits to the
    envelope before even invoking `finish_write`'s `cmp`, since it already knows nothing changed).
    """
    open_count, close_count, open_index, close_index = _scan_marker(lines, name)
    if open_count == 0:
        return lines, False
    if open_count > 1 or close_count > 1:
        raise _MarkerAmbiguous()
    if open_count != close_count or close_index < open_index:
        raise _MarkerUnterminated()
    drop_start = open_index
    if open_index > 1 and lines[open_index - 2].strip() == "":  # one blank line above, if present
        drop_start = open_index - 1
    new_lines = [
        line for i, line in enumerate(lines, start=1) if not (drop_start <= i <= close_index)
    ]
    return new_lines, True


class _MarkerAmbiguous(Exception):
    """Internal signal: the target marker's delimiter appears more than once — caught by the
    `upsert`/`remove` command handlers and turned into a `MARKER_AMBIGUOUS` needs_decision
    envelope (module docstring). Never escapes this module.
    """


class _MarkerUnterminated(Exception):
    """Internal signal: the target marker is open with no matching close, or closes before it
    opens — caught by the `upsert`/`remove` command handlers and turned into exit 5 (module
    docstring: "v1 has no dedicated decision code for it distinct from malformed content").
    """


def _serialize(lines):
    """Join a line list back into file text: one trailing newline per line, matching both v1's
    `print` (which always appends `ORS`, default `"\\n"`) and `_read_lines_or_empty`'s
    `splitlines()` (which drops terminators) — so a round-trip through this module's own
    read → render → serialize path reproduces the source bytes exactly when nothing changed, and
    an empty `lines` list serializes to `""` (an empty file), not a single stray newline.
    """
    return "".join(line + "\n" for line in lines)


# ---- upsert ----


def build_upsert(file, marker_name, body_path, dry_run=False, prepend=False):
    """Pure ``upsert`` core (architecture.md §2's pure-core pattern, S8 pattern lock): render the
    block (creating/replacing it), apply the write when ``changed and not dry_run``, and return
    ``(payload, notices, decision)`` — **never emits** (no ``emit_ok``/``emit_needs_decision``).

    Return contract (the type-level distinction the S8 retro asks for):
      - ``decision is None`` → success; ``payload`` is the write receipt (``changed``/``dry_run``/
        ``body_bytes``/``body_sha256``) the wrapper emits as an ``ok`` envelope.
      - ``decision is not None`` → a ``MARKER_AMBIGUOUS`` ``needs_decision`` (duplicate delimiter);
        ``payload`` is ``None``, nothing is written, the wrapper emits the decision.
      - an unterminated/inverted marker raises :class:`_MarkerUnterminated` (v1 has no dedicated
        decision code for it distinct from malformed content — exit 5, applied by the wrapper), and
        a missing/invalid invocation still exits 2 on stderr from the wrapper before the core runs.

    "pure" here is architecture.md §2's non-emitting sense, not side-effect-free: this core owns the
    idempotent file write (``changed: false`` leaves the file byte-for-byte untouched — not even
    opened), exactly as before, so the wrapper only serializes the returned receipt.
    """
    file_path = Path(file)
    existing_lines = _read_lines_or_empty(file)
    body_lines = _read_body_lines(Path(body_path))

    try:
        new_lines = _render_upsert(existing_lines, marker_name, body_lines, prepend)
    except _MarkerAmbiguous:
        return None, [], needs_decision(
            MARKER_AMBIGUOUS,
            summary="marker '%s' appears more than once in %s" % (marker_name, file),
            context={"op": "upsert", "marker": marker_name, "file": file},
            options=["resolve the duplicate block by hand, then re-run"],
        )

    new_text = _serialize(new_lines)
    old_text = _serialize(existing_lines) if file_path.exists() else None
    changed = old_text != new_text

    if changed and not dry_run:
        with open(file_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
    # changed=False (or dry_run=True): the target file is left byte-for-byte untouched — not even
    # opened for writing — matching v1's `finish_write`, which only ever `mv`s the temp file over
    # the real one inside that same guard (module docstring's `changed: false` note).

    with open(body_path, "rb") as fh:
        body_bytes_data = fh.read()

    payload = {
        "op": "upsert",
        "marker": marker_name,
        "file": file,
        "changed": changed,
        "dry_run": dry_run,
        "body_bytes": len(body_bytes_data),
        "body_sha256": sha256_hex(body_bytes_data),
    }
    return payload, [], None


def run_upsert(args):
    """Thin emit wrapper over :func:`build_upsert` (S8 pattern lock). Byte-identical to the
    pre-retrofit envelope for every input."""
    _validate_marker_name(args.marker_name)
    body_path = Path(args.body_path)
    if not body_path.is_file():
        sys.stderr.write("MISSING_BODY_FILE: %s\n" % args.body_path)
        sys.exit(EXIT_USAGE_ERROR)

    try:
        payload, notices, decision = build_upsert(
            args.file, args.marker_name, args.body_path, dry_run=args.dry_run, prepend=args.prepend
        )
    except _MarkerUnterminated:
        sys.exit(EXIT_MARKER_UNTERMINATED)

    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        sys.exit(EXIT_OK)
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


# ---- remove ----


def build_remove(file, marker_name, dry_run=False):
    """Pure ``remove`` core (S8 pattern lock): drop the named block (with one blank line above it,
    if present), apply the write when ``changed and not dry_run``, and return ``(payload, notices,
    decision)`` — **never emits**.

    Return contract mirrors :func:`build_upsert`: ``decision is None`` → the removal receipt
    (``changed``/``dry_run``, no ``body_*`` — remove stages no body); ``decision is not None`` →
    ``MARKER_AMBIGUOUS`` (duplicate delimiter, nothing written); :class:`_MarkerUnterminated`
    raised → exit 5 at the wrapper. A nonexistent file is a ``changed: False`` no-op (v1's identical
    short-circuit), not an error. Same non-emitting-but-owns-the-idempotent-write sense as
    :func:`build_upsert`.
    """
    file_path = Path(file)

    if not file_path.exists():
        # Nothing to remove — report a no-op without inventing a file (v1's identical short-
        # circuit: `[[ ! -f "$file" ]]` returns before ever building a temp file).
        return {
            "op": "remove",
            "marker": marker_name,
            "file": file,
            "changed": False,
            "dry_run": dry_run,
        }, [], None

    existing_lines = _read_lines_or_empty(file)
    try:
        new_lines, changed = _render_remove(existing_lines, marker_name)
    except _MarkerAmbiguous:
        return None, [], needs_decision(
            MARKER_AMBIGUOUS,
            summary="marker '%s' appears more than once in %s" % (marker_name, file),
            context={"op": "remove", "marker": marker_name, "file": file},
            options=["resolve the duplicate block by hand, then re-run"],
        )

    if changed and not dry_run:
        with open(file_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(_serialize(new_lines))
    # changed=False (absent block): file is left byte-for-byte untouched, same guard as upsert.

    payload = {
        "op": "remove",
        "marker": marker_name,
        "file": file,
        "changed": changed,
        "dry_run": dry_run,
    }
    return payload, [], None


def run_remove(args):
    """Thin emit wrapper over :func:`build_remove` (S8 pattern lock). Byte-identical to the
    pre-retrofit envelope for every input."""
    _validate_marker_name(args.marker_name)
    try:
        payload, notices, decision = build_remove(
            args.file, args.marker_name, dry_run=args.dry_run
        )
    except _MarkerUnterminated:
        sys.exit(EXIT_MARKER_UNTERMINATED)

    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        sys.exit(EXIT_OK)
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


# ---- dispatch ----


def build_parser():
    parser = argparse.ArgumentParser(
        prog="config_block.py",
        description="Deterministic marker-block read/write (architecture.md §3, §6).",
    )
    sub = parser.add_subparsers(dest="subcommand")

    p_read = sub.add_parser("read")
    p_read.add_argument("file")
    p_read.add_argument("marker_name")

    p_list = sub.add_parser("list")
    p_list.add_argument("file")

    p_upsert = sub.add_parser("upsert")
    p_upsert.add_argument("file")
    p_upsert.add_argument("marker_name")
    p_upsert.add_argument("body_path")
    p_upsert.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_upsert.add_argument("--prepend", dest="prepend", action="store_true")

    p_remove = sub.add_parser("remove")
    p_remove.add_argument("file")
    p_remove.add_argument("marker_name")
    p_remove.add_argument("--dry-run", dest="dry_run", action="store_true")

    return parser


def main(argv):
    parser = build_parser()
    if not argv:
        _die_usage(parser)
    args = parser.parse_args(argv)
    if args.subcommand is None:
        _die_usage(parser)

    if args.subcommand == "read":
        run_read(args)
    elif args.subcommand == "list":
        run_list(args)
    elif args.subcommand == "upsert":
        run_upsert(args)
    elif args.subcommand == "remove":
        run_remove(args)
    else:
        _die_usage(parser)  # pragma: no cover — argparse's own `choices` already excludes this


if __name__ == "__main__":
    main(sys.argv[1:])
