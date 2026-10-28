"""sensitive_paths.py — the `<!-- pr-evaluator-sensitive-paths -->` declaration: the paths a consuming
repo says always need a person at the merge gate (#96), and the one matcher every consumer composes.

**Why this module exists.** Two consumers judge one PR against the declaration — ``prep_evaluator``
(S7 forces ``ask`` on a match) and ``blast_radius.py`` (#97, the resolver's review level and pause) —
and they must not disagree about it, so both call :func:`assess`. This is the ``story_set.py`` /
``doc_catalogue.py`` promotion pattern: one read, composed in-process by several callers. There is no CLI surface (no shebang, no ``main()``), exactly
like ``branching.py``.

Three reads:

- :func:`read_declaration` — the patterns, from the checkout's **working tree** only
  (architecture.md §6 rule 1), through the same in-root discovery as the self-edit comparison's head
  side. The base never supplies a pattern.
- :func:`changed_paths` — the branch's changed paths against its merge base, from the checkout's own
  git (``--no-renames``, so a rename lists both paths and a deletion is listed). Never raises.
  :func:`changed_paths_since` is the same list from a local commit (a phase's base), with no fetch.
- :func:`match` — the ``(pattern, path)`` pairs. A **change to the declaration itself** is always
  among them: its content (presence, interior lines, host path) at the head is compared with the same
  discovery at ``base_rev`` (``config_block.read_block_with``), so adding, editing, removing, moving or
  breaking the block matches while an unrelated edit to its host file does not. Each side reads a
  candidate as the content its in-repo target holds there: the path is normalized lexically (so
  ``link/../x.md`` is ``x.md``, where the OS would follow ``link`` first), then symlinks are followed component by
  component on both sides by one resolver (:func:`_resolve_in_root`), so a ``CLAUDE.md -> AGENTS.md``
  host compares like a plain file. A host outside the root — an absolute or ``..`` ``@``-include, or a
  symlink pointing out of the repo (or dangling), or one that leaves the repo and comes back in —
  reads as absent on both sides of that comparison **and** for the patterns, so the three reads are
  one discovery (SEC-1). Such a block is reported in ``unparsed``, which forces ``ask``: its patterns
  are lost, and the gate must not quietly ask less. An absent or empty block yields no *pattern*
  matches.

:func:`assess` composes the three into one fact, folding every failed read into ``error``.

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

    The patterns come from the **same discovery** as the self-edit comparison's head side
    (:func:`_worktree_reader`): a host reached outside the root — an absolute or ``..`` include, a
    symlink out of the repo, or one that leaves it and comes back in — reads as absent here too
    (SEC-1: a separate any-route read let a PR's out-and-back symlink put an empty block ahead of the
    real one, unseen by the comparison). When that any-route read finds a different block than this
    one, ``unparsed`` names it, so the gate asks instead of silently losing its patterns.

    ``unparsed`` lists what the matcher cannot read: list items no pattern parses from, and such an
    unreached block — a consumer treats any as a failed read (the gate asks), never as an absent
    pattern. ``source`` is the host file's repo-relative path — the physical file, after in-repo
    symlinks, where the self-edit comparison keys on the discovery path; ``None`` when no
    well-formed block is found."""
    reader = _worktree_reader(root)
    (present, interior, host), _candidates = _side(reader)
    source = _resolve_in_root(host, reader.readlink) if present else None
    unreached = _unreached_block(root, interior, source)
    if not present:
        return {"present": False, "source": None, "entries": [], "unparsed": unreached}
    return {
        "present": True,
        "source": source,
        "entries": parse_entries(interior),
        "unparsed": unparsed_entries(interior) + unreached,
    }


def _unreached_block(root, interior, source):
    """``[<description>]`` when the any-route working-tree read (``read_block_anywhere``, which
    follows every include and symlink as the OS does) finds a block other than the in-root one —
    other interior lines, or another file; else ``[]``. An in-repo alias of the in-root host (an
    absolute symlink or include naming that same file) is the same block, not an unreached one."""
    any_present, any_interior, any_source = config_block.read_block_anywhere(root, MARKER)
    if not any_present:
        return []
    root_path = Path(root).resolve()
    any_file = Path(any_source).resolve()
    if source is not None and tuple(any_interior) == interior and any_file == root_path / source:
        return []
    # Named by the candidate that led there (the link to fix), repo-relative, so the local checkout
    # path stays out of the attention line and the S7 card; else by the in-repo file it reached; and a
    # host outside the repo (an absolute or `..` include) by its normalized absolute path.
    lexical = os.path.normpath(any_source)
    relative = os.path.relpath(lexical, str(Path(root)))
    if not relative.startswith(".."):
        shown = relative
    elif any_file.is_relative_to(root_path):
        shown = any_file.relative_to(root_path).as_posix()
    else:
        shown = lexical
    return ["a `<!-- %s -->` block in %s that the in-repo read does not reach: its patterns are not read" % (
        MARKER, shown
    )]


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
    reader.readlink = readlink
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


def pattern_matches(pattern, path):
    """Whether ``path`` (repo-relative POSIX) matches ``pattern`` under the declaration's grammar —
    the one test :func:`match` applies, public so another reader of path patterns (``blast_radius.py``'s
    planned spans) cannot drift from it."""
    return fnmatch.fnmatchcase(path, _glob(pattern))


def match(root, changed_paths, base_rev=None):
    """The ``(pattern, path)`` pairs for ``changed_paths`` (repo-relative POSIX paths) against the
    declaration at ``root``: self-edit pairs first (carrying :data:`SELF_EDIT_PATTERN`), then pattern
    pairs in declaration order, each pattern's paths in input order, carrying the pattern as written.

    ``base_rev`` is the merge base; the evaluator always passes it. ``None`` keeps the epic contract's
    two-argument call valid, detecting a change only as the head host file being among
    ``changed_paths`` (it misses a removed block, and fires on unrelated edits to that file). Raises
    :class:`BaseReadError` when ``base_rev`` is given and cannot be read.

    ``match`` never reads ``unparsed``: a declaration whose block the in-repo read cannot reach, or
    whose list items will not parse, returns fewer pairs (``[]`` for a block outside the repo), which
    reads like an absent one. A consumer must also treat a non-empty
    ``read_declaration(root)["unparsed"]`` as a failed read, or it asks less than the merge gate.
    """
    changed = list(changed_paths)
    pairs = _self_edit_pairs(root, changed, base_rev)
    for entry in read_declaration(root)["entries"]:
        pairs.extend((entry["pattern"], path) for path in changed if pattern_matches(entry["pattern"], path))
    return pairs


def assess(root, base_ref=None, since=None):
    """The declaration judged against one range: ``{"present", "source", "patterns", "base", "paths",
    "matches", "error"}``. Exactly one of ``base_ref`` (the branch's merge base with
    ``origin/<base_ref>``, fetched — the merge gate's range) or ``since`` (the merge base of a local
    commit and ``HEAD``, no fetch — a resolver phase's range) is given.

    A failed read reports ``error`` and is never raised: a git command that fails (a failed fetch
    included), a ``base_rev`` that will not read, a candidate that is not UTF-8 text, and a non-empty
    ``unparsed`` (a declared path the matcher
    cannot read is a failed read, not an absent pattern). Every consumer treats ``error`` as the
    stricter outcome, so the merge gate and the resolver read one PR the same way by construction.
    Two things still raise: ``ValueError`` for a bad range (a caller bug), and a failure to spawn
    ``git`` at all (no ``git`` on ``PATH``), which no consumer could read past either.
    """
    if (base_ref is None) == (since is None):
        raise ValueError("assess takes exactly one of base_ref or since")
    if since is not None:
        paths, merge_base, error = changed_paths_since(root, since)
    else:
        paths, merge_base, error = changed_paths(root, base_ref)
    declaration = {"present": False, "source": None, "entries": [], "unparsed": []}
    matches = []
    paths_read = error is None
    try:
        declaration = read_declaration(root)
        if paths_read and declaration["unparsed"]:
            # A declared path the matcher cannot read is a failed read, not an absent pattern.
            error = "unreadable declaration: %s" % (
                "; ".join('"%s"' % line for line in declaration["unparsed"]),
            )
        if paths_read:
            pairs = match(root, paths, base_rev=merge_base)
            matches = [{"pattern": pattern, "path": path} for pattern, path in pairs]
    except BaseReadError as exc:
        error = str(exc)
    except (UnicodeDecodeError, OSError) as exc:
        error = "could not read a candidate config file: %s" % exc
    return {
        "present": declaration["present"],
        "source": declaration["source"],
        "patterns": declaration["entries"],
        "base": merge_base,
        "paths": paths,
        "matches": matches,
        "error": error,
    }


def _first_line(text):
    lines = (text or "").strip().splitlines()
    return lines[0][:200] if lines else "no output"


def changed_paths(root, base_ref, head="HEAD"):
    """``(paths, merge_base, error)`` for ``head`` (``root``'s ``HEAD`` by default; the resolver's review pause
    passes the pushed sha, #170) against ``origin/<base_ref>``: fetch the
    base, take the merge base, list ``git diff --name-only --no-renames``. Never raises — any git
    failure (a failed fetch included; there is no "stale but usable"), or output that is not UTF-8 (a
    path name in another encoding), returns ``([], None, "<short reason>")``. A stale base only gives
    an older merge base: a superset, never a subset."""
    try:
        return _changed_paths(root, base_ref, head)
    except UnicodeDecodeError as exc:
        return [], None, "git output for %s is not UTF-8: %s" % (base_ref, exc.reason)


def _changed_paths(root, base_ref, head="HEAD"):
    fetched = process.run(["git", "fetch", "origin", base_ref], cwd=str(root))
    if fetched.returncode != 0:
        return [], None, "git fetch origin %s failed: %s" % (base_ref, _first_line(fetched.stderr))
    return _diff_from_merge_base(root, "origin/%s" % base_ref, head)


def changed_paths_since(root, rev):
    """``(paths, merge_base, error)`` for ``root``'s ``HEAD`` against the merge base of ``rev`` and
    ``HEAD`` — for a phase base, ``rev`` itself. No fetch: ``rev`` is a local commit. Never raises,
    with :func:`changed_paths`' failure shape."""
    try:
        return _diff_from_merge_base(root, rev)
    except UnicodeDecodeError as exc:
        return [], None, "git output for %s is not UTF-8: %s" % (rev, exc.reason)


def _diff_from_merge_base(root, rev, head="HEAD"):
    base = process.run(["git", "merge-base", head, rev], cwd=str(root))
    if base.returncode != 0:
        return [], None, "git merge-base %s %s failed: %s" % (head, rev, _first_line(base.stderr))
    merge_base = base.stdout.strip()
    diff = process.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", merge_base, head], cwd=str(root)
    )
    if diff.returncode != 0:
        return [], None, "git diff %s HEAD failed: %s" % (merge_base[:7], _first_line(diff.stderr))
    return [path for path in diff.stdout.split("\0") if path], merge_base, None
