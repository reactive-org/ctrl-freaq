"""sensitive_paths.py — the `<!-- pr-evaluator-sensitive-paths -->` declaration: the paths a consuming
repo says always need a person at the merge gate (#96), and the one matcher every consumer composes.

**Why this module exists.** Two preps judge one PR against the declaration — ``prep_evaluator`` (S7
forces ``ask`` on a match) and ``prep_resolver`` (#97, the review level) — and they must not
disagree about it. This is the ``story_set.py`` / ``doc_catalogue.py`` promotion pattern: one read,
composed in-process by several preps. There is no CLI surface (no shebang, no ``main()``), exactly
like ``branching.py``.

Three reads:

- :func:`read_declaration` — the patterns, from the checkout's **working tree** only
  (architecture.md §6 rule 1). The base never supplies a pattern.
- :func:`changed_paths` — the branch's changed paths against its merge base, from the checkout's own
  git (``--no-renames``, so a rename lists both paths and a deletion is listed). Never raises.
- :func:`match` — the ``(pattern, path)`` pairs. A **change to the declaration itself** is always
  among them: its content (presence, interior lines, host path) at the head is compared with the same
  discovery at ``base_rev`` (``config_block.read_block_with``), so adding, editing, removing, moving or
  breaking the block matches while an unrelated edit to its host file does not. Each side reads a
  candidate as the content its in-repo target holds there: symlinks are followed component by
  component on both sides by one resolver (:func:`_resolve_in_root`), so a ``CLAUDE.md -> AGENTS.md``
  host compares like a plain file. A host outside the root — an absolute or ``..`` ``@``-include, or a
  symlink pointing out of the repo (or dangling) — reads as absent on both sides of that comparison:
  its content is under no PR's version control. An absent or empty block yields no *pattern* matches.

Pattern grammar: ``fnmatch.fnmatchcase`` over the whole repo-relative POSIX path — case-sensitive,
``*`` and ``?`` cross ``/`` (so ``*.tf`` matches at any depth); a trailing ``/`` means everything under
that folder; a leading ``/`` is stripped. A broad pattern errs toward an extra click, but three forms
under-match: ``[...]`` is a character class (write ``[[]`` for a literal ``[``), a folder named without
its trailing ``/`` matches only that exact path, and ``./`` or ``\\`` is not normalized.

Spawns only ``git``, through :mod:`pipelib.process` (architecture.md §12).
"""

import fnmatch
import os
import posixpath
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402
from pipelib import process  # noqa: E402

MARKER = "pr-evaluator-sensitive-paths"
# The pattern a self-edit pair carries — the block's own marker, so the operator reads "the
# declaration changed" rather than a glob that happened to cover the host file.
SELF_EDIT_PATTERN = "<!-- %s -->" % MARKER

# One list item per pattern: `` - `<pattern>` `` with an optional ` — <reason>`. A non-conforming
# line is skipped, not fatal — the merge-policy block's tolerant reading (no decision code).
_ENTRY_RE = re.compile(r"^- `([^`]+)`(?: — (.+))?$")
# A list item at all — a `-`, `*` or `+` bullet, or an ordered `1.` / `1)` marker. One that does not match `_ENTRY_RE` is a declared path
# the matcher cannot read, so it is reported (`unparsed`) rather than dropped: a typo must make the gate
# ask more, never less. Prose lines in the block are not list items and stay ignored.
_LIST_ITEM_RE = re.compile(r"^([-*+]|\d+[.)])(\s|`|$)")


class BaseReadError(RuntimeError):
    """`match` was given a ``base_rev`` it could not read — its one failure channel. The caller's
    safe value is known (``ask``), so this is a fact to report, never a decision."""


def parse_entries(interior_lines):
    """The block's interior lines → ``[{"pattern", "reason"}]``, in declaration order."""
    entries = []
    for line in interior_lines:
        found = _ENTRY_RE.match(line.strip())
        if found:
            entries.append({"pattern": found.group(1), "reason": found.group(2)})
    return entries


def unparsed_entries(interior_lines):
    """The block's list-item lines that :func:`parse_entries` cannot read, stripped, in order."""
    return [
        line.strip()
        for line in interior_lines
        if _LIST_ITEM_RE.match(line.strip()) and not _ENTRY_RE.match(line.strip())
    ]


def _repo_relative(path):
    """A discovery path as a normalized repo-relative POSIX path, or ``None`` for a host outside the
    root (absolute, or escaping it through ``..``)."""
    if path is None or posixpath.isabs(path):
        return None
    normalized = posixpath.normpath(path)
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def read_declaration(root):
    """The declaration at ``root``'s working tree: ``{"present", "source", "entries", "unparsed"}``.
    ``unparsed`` lists the list items no pattern could be read from — a consumer treats any as a failed
    read (the gate asks), never as an absent pattern. ``source`` is
    the host file's repo-relative path — the physical file, after symlinks, where the self-edit
    comparison keys on the discovery path — or, for a block hosted outside the root, its normalized
    absolute path; ``None`` when no well-formed block is found."""
    present, interior, source = config_block.read_block_anywhere(root, MARKER)
    if not present:
        return {"present": False, "source": None, "entries": [], "unparsed": []}
    source = os.path.normpath(source)
    try:
        source = Path(source).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        pass  # outside the root: the normalized absolute path
    return {
        "present": True,
        "source": source,
        "entries": parse_entries(interior),
        "unparsed": unparsed_entries(interior),
    }


# Symlink hops followed before a candidate is given up as a loop.
_MAX_LINK_HOPS = 40


def _resolve_in_root(relative, readlink):
    """A repo-relative path → the repo-relative path of the file it names after following symlinks
    component by component, or ``None`` when it leaves the root (an absolute link target, a ``..`` past
    the root) or loops. ``readlink(path)`` returns a symlink's target text, ``None`` for anything else —
    the one place the head and base sides differ."""
    parts = [part for part in relative.split("/") if part not in ("", ".")]
    resolved, hops = [], 0
    while parts:
        part = parts.pop(0)
        if part == "..":
            if not resolved:
                return None
            resolved.pop()
            continue
        target = readlink("/".join(resolved + [part]))
        if target is None:
            resolved.append(part)
            continue
        hops += 1
        if hops > _MAX_LINK_HOPS or posixpath.isabs(target):
            return None
        parts = [p for p in target.split("/") if p not in ("", ".")] + parts
    return "/".join(resolved) or None


def _worktree_reader(root):
    """The head side of the self-edit comparison: a repo-relative candidate's working-tree text, read
    through its in-repo symlinks; a host outside the root reads as absent, symmetric with the base
    reader. ``reader.physical`` collects the files it actually read."""
    root_path = Path(root)
    cache = {}

    def readlink(path):
        full = root_path / path
        return os.readlink(str(full)) if os.path.islink(str(full)) else None

    def reader(path):
        relative = _repo_relative(path)
        if relative is None:
            return None
        if relative not in cache:
            physical = _resolve_in_root(relative, readlink)
            text = None
            if physical is not None and (root_path / physical).is_file():
                text = (root_path / physical).read_text(encoding="utf-8")
                reader.physical.add(physical)
            cache[relative] = text
        return cache[relative]

    reader.physical = set()
    return reader


def _base_reader(root, base_rev):
    """The base side: a repo-relative candidate's blob at ``base_rev`` (a ``git ls-tree`` /
    ``cat-file`` read is a script-internal tool, architecture.md §6), read through its in-repo symlinks
    (mode ``120000``); ``None`` when it is absent there, not a file, or leaves the root. Raises
    :class:`BaseReadError` when ``base_rev`` is not a readable commit or a present blob won't read as
    UTF-8 text. ``reader.physical`` collects the files it actually read."""
    check = process.run(["git", "cat-file", "-e", "%s^{commit}" % base_rev], cwd=str(root))
    if check.returncode != 0:
        raise BaseReadError("base %s is not a readable commit" % base_rev)
    entries, cache = {}, {}

    def entry(path):
        """``(mode, type, oid)`` of ``path`` at the base, or ``None`` when it has none."""
        if path not in entries:
            listed = process.run(["git", "ls-tree", "-z", base_rev, "--", path], cwd=str(root))
            if listed.returncode != 0:
                raise BaseReadError(
                    "could not list %s:%s: %s" % (base_rev, path, (listed.stderr or "").strip()[:200])
                )
            record = listed.stdout.split("\0")[0]
            meta, _tab, name = record.partition("\t")
            entries[path] = tuple(meta.split(" ")) if record and name == path else None
        return entries[path]

    def blob(oid, spec):
        try:
            shown = process.run(["git", "cat-file", "blob", oid], cwd=str(root))
        except UnicodeDecodeError:
            raise BaseReadError("%s is not UTF-8 text" % spec)
        if shown.returncode != 0:
            raise BaseReadError("could not read %s: %s" % (spec, (shown.stderr or "").strip()[:200]))
        return shown.stdout

    def readlink(path):
        found = entry(path)
        if found is None or found[0] != "120000":
            return None
        return blob(found[2], "%s:%s" % (base_rev, path))

    def reader(path):
        relative = _repo_relative(path)
        if relative is None:
            return None
        if relative not in cache:
            physical = _resolve_in_root(relative, readlink)
            found = entry(physical) if physical is not None else None
            text = None
            if found is not None and found[1] == "blob" and found[0] != "120000":
                text = blob(found[2], "%s:%s" % (base_rev, physical))
                reader.physical.add(physical)
            cache[relative] = text
        return cache[relative]

    reader.physical = set()
    return reader


def _side(reader):
    """One side of the self-edit comparison: ``(state, candidates)`` — ``state`` the block's
    ``(present, interior, host)`` with an absent block normalized to one value."""
    present, interior, source = config_block.read_block_with(reader, MARKER)
    state = (True, tuple(interior), _repo_relative(source)) if present else (False, (), None)
    candidates = {c for c in map(_repo_relative, config_block.candidate_paths_with(reader)) if c}
    # A changed symlink target (`AGENTS.md` behind `CLAUDE.md`) is the declaration's file too.
    return state, candidates | reader.physical


def _self_edit_pairs(root, changed, base_rev):
    if base_rev is None:
        # No base to compare: all a caller can check is that the head host file changed.
        declaration = read_declaration(root)
        host = declaration["source"] if declaration["present"] else None
        return [(SELF_EDIT_PATTERN, host)] if host in changed else []
    head_state, head_candidates = _side(_worktree_reader(root))
    base_state, base_candidates = _side(_base_reader(root, base_rev))
    if head_state == base_state:
        return []
    candidates = head_candidates | base_candidates
    hits = [path for path in changed if path in candidates]
    if hits:
        return [(SELF_EDIT_PATTERN, path) for path in hits]
    host = head_state[2] if head_state[0] else base_state[2]
    return [(SELF_EDIT_PATTERN, host)] if host else []


def _glob(pattern):
    glob = pattern.lstrip("/")
    return glob + "*" if glob.endswith("/") else glob


def match(root, changed_paths, base_rev=None):
    """The ``(pattern, path)`` pairs for ``changed_paths`` (repo-relative POSIX paths) against the
    declaration at ``root``: self-edit pairs first (carrying :data:`SELF_EDIT_PATTERN`), then pattern
    pairs in declaration order, each pattern's paths in input order, carrying the pattern as written.

    ``base_rev`` is the merge base; the evaluator always passes it. ``None`` keeps the epic contract's
    two-argument call valid, detecting a change only as the head host file being among
    ``changed_paths`` (it misses a removed block, and fires on unrelated edits to that file). Raises
    :class:`BaseReadError` when ``base_rev`` is given and cannot be read.
    """
    changed = list(changed_paths)
    pairs = _self_edit_pairs(root, changed, base_rev)
    for entry in read_declaration(root)["entries"]:
        glob = _glob(entry["pattern"])
        pairs.extend((entry["pattern"], path) for path in changed if fnmatch.fnmatchcase(path, glob))
    return pairs


def _first_line(text):
    lines = (text or "").strip().splitlines()
    return lines[0][:200] if lines else "no output"


def changed_paths(root, base_ref):
    """``(paths, merge_base, error)`` for ``root``'s ``HEAD`` against ``origin/<base_ref>``: fetch the
    base, take the merge base, list ``git diff --name-only --no-renames``. Never raises — any git
    failure (a failed fetch included; there is no "stale but usable"), or output that is not UTF-8 (a
    path name in another encoding), returns ``([], None, "<short reason>")``. A stale base only gives
    an older merge base: a superset, never a subset."""
    try:
        return _changed_paths(root, base_ref)
    except UnicodeDecodeError as exc:
        return [], None, "git output for %s is not UTF-8: %s" % (base_ref, exc.reason)


def _changed_paths(root, base_ref):
    fetched = process.run(["git", "fetch", "origin", base_ref], cwd=str(root))
    if fetched.returncode != 0:
        return [], None, "git fetch origin %s failed: %s" % (base_ref, _first_line(fetched.stderr))
    base = process.run(["git", "merge-base", "HEAD", "origin/%s" % base_ref], cwd=str(root))
    if base.returncode != 0:
        return [], None, "git merge-base HEAD origin/%s failed: %s" % (
            base_ref, _first_line(base.stderr)
        )
    merge_base = base.stdout.strip()
    diff = process.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", merge_base, "HEAD"], cwd=str(root)
    )
    if diff.returncode != 0:
        return [], None, "git diff %s HEAD failed: %s" % (merge_base[:7], _first_line(diff.stderr))
    return [path for path in diff.stdout.split("\0") if path], merge_base, None
