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
  breaking the block matches while an unrelated edit to its host file does not. A host outside the
  root (an absolute or ``..`` ``@``-include) is skipped on both sides of that comparison — its content
  is under no PR's version control. An absent or empty block yields no *pattern* matches.

Pattern grammar: ``fnmatch.fnmatchcase`` over the whole repo-relative POSIX path — case-sensitive,
``*`` and ``?`` cross ``/`` (so ``*.tf`` matches at any depth); a trailing ``/`` means everything under
that folder; a leading ``/`` is stripped. These globs only ever over-match, which costs a click.

Spawns only ``git``, through :mod:`pipelib.process` (architecture.md §12).
"""

import fnmatch
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


def _repo_relative(path):
    """A discovery path as a normalized repo-relative POSIX path, or ``None`` for a host outside the
    root (absolute, or escaping it through ``..``)."""
    if path is None or posixpath.isabs(path) or Path(path).is_absolute():
        return None
    normalized = posixpath.normpath(path)
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized


def read_declaration(root):
    """The declaration at ``root``'s working tree: ``{"present", "source", "entries"}``. ``source`` is
    the host file's repo-relative path — or, for a block hosted outside the root through an absolute
    ``@``-include, that absolute path — and ``None`` when no well-formed block is found."""
    present, interior, source = config_block.read_block_anywhere(root, MARKER)
    if not present:
        return {"present": False, "source": None, "entries": []}
    try:
        source = Path(source).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        pass
    return {"present": True, "source": source, "entries": parse_entries(interior)}


def _worktree_reader(root):
    """The head side of the self-edit comparison: a repo-relative candidate's working-tree text; a
    host outside the root reads as absent, symmetric with the base reader."""
    root_path = Path(root)

    def reader(path):
        relative = _repo_relative(path)
        if relative is None:
            return None
        resolved = root_path / relative
        return resolved.read_text(encoding="utf-8") if resolved.is_file() else None

    return reader


def _base_reader(root, base_rev):
    """The base side: a repo-relative candidate's blob at ``base_rev`` (``git show <rev>:<path>`` is a
    script-internal tool, architecture.md §6), ``None`` when it is absent there. Raises
    :class:`BaseReadError` when ``base_rev`` is not a readable commit or a present blob won't read."""
    check = process.run(["git", "cat-file", "-e", "%s^{commit}" % base_rev], cwd=str(root))
    if check.returncode != 0:
        raise BaseReadError("base %s is not a readable commit" % base_rev)
    cache = {}

    def reader(path):
        relative = _repo_relative(path)
        if relative is None:
            return None
        if relative not in cache:
            spec = "%s:%s" % (base_rev, relative)
            kind = process.run(["git", "cat-file", "-t", spec], cwd=str(root))
            if kind.returncode != 0 or kind.stdout.strip() != "blob":
                cache[relative] = None
            else:
                shown = process.run(["git", "show", spec], cwd=str(root))
                if shown.returncode != 0:
                    raise BaseReadError(
                        "could not read %s: %s" % (spec, (shown.stderr or "").strip()[:200])
                    )
                cache[relative] = shown.stdout
        return cache[relative]

    return reader


def _side(reader):
    """One side of the self-edit comparison: ``(state, candidates)`` — ``state`` the block's
    ``(present, interior, host)`` with an absent block normalized to one value."""
    present, interior, source = config_block.read_block_with(reader, MARKER)
    state = (True, tuple(interior), _repo_relative(source)) if present else (False, (), None)
    candidates = [c for c in map(_repo_relative, config_block.candidate_paths_with(reader)) if c]
    return state, candidates


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
    candidates = set(head_candidates) | set(base_candidates)
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
    failure (a failed fetch included; there is no "stale but usable") returns ``([], None,
    "<short reason>")``. A stale base only gives an older merge base: a superset, never a subset."""
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
