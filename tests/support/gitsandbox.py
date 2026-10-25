"""Ephemeral git origin + clone for offline workspace/lifecycle tests.

Provides ``mk_origin()`` and ``mk_clone(origin)`` for building a temp **bare** origin repo and a
working clone of it, each in its own ``tempfile``-managed directory, with guaranteed cleanup —
pass or fail — via ``unittest.TestCase.addCleanup`` or the ``git_sandbox()`` context manager
below.

No shim is involved here (per architecture.md §10, "Git sandbox: ... no shim needed") — this
module shells out to the real ``git`` binary, which is an allowed spawnable per architecture.md
§1. It never touches the network (everything happens between two local temp directories) and
never invokes ``gh``.

All temp paths are resolved through ``Path.resolve()`` before being handed back, because on
macOS ``/tmp`` is a symlink to ``/private/tmp`` — comparing an un-resolved path against a
resolved one elsewhere in the codebase would spuriously mismatch. Paths are built with
``pathlib.Path`` per architecture.md §1; ``str()`` is applied only at the boundary where
``subprocess`` needs a plain string argument.

**Per-process template.** Building an origin and a clone the plain way costs ten ``git``
processes, which dominated the sandbox-heavy modules. So the first ``mk_origin(initial_branch)`` in
a process builds a template bare origin and a template clone that way, once per
``initial_branch``, and every later call copies them instead:

- ``mk_origin`` copies the template origin into a fresh resolved temp dir, so every test still gets
  its own origin (with the same seed commit sha as every other origin of that branch name).
- ``mk_clone(origin)`` copies the template clone only while ``origin`` is still **pristine**: its
  ``HEAD``, ``packed-refs`` and every file under ``refs/`` byte-equal to the template origin's, as
  snapshotted when the template was built (plain file reads, no ``git``). The copy's
  ``.git/config`` then has the template origin's absolute path (a unique ``mkdtemp`` string)
  replaced by the new origin's, and one ``git update-index --refresh`` re-stats its index, so
  every git command, plumbing included, sees it as clean as a real clone. An origin that has been
  pushed to, or that did not come from a template, gets a real ``git clone``. The check compares
  refs only: a test that rewrites an origin's objects without touching a ref would be cloned from
  the template, so such a test must clone an origin that did not come from a template.

Templates are removed at process exit by a ``multiprocessing.util.Finalize`` hook, which runs both
in a spawned worker (which leaves through ``os._exit`` after multiprocessing's own exit hook, never
running plain ``atexit`` handlers) and in a serial process (where multiprocessing registers that
same hook with ``atexit``).
"""

import contextlib
import multiprocessing.util
import shutil
import subprocess
import tempfile
from pathlib import Path

_GIT = shutil.which("git")
if _GIT is None:  # pragma: no cover - environment precondition, not exercised in CI
    raise RuntimeError("gitsandbox: `git` was not found on PATH; the offline harness needs it")


def _run_git(args, cwd, env=None):
    """Run `git <args>` in `cwd`, argument-list only, UTF-8 pinned. Raises on nonzero exit."""
    result = subprocess.run(
        [_GIT] + args,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "gitsandbox: `git %s` (cwd=%s) failed with exit %d\nstdout:\n%s\nstderr:\n%s"
            % (" ".join(args), cwd, result.returncode, result.stdout, result.stderr)
        )
    return result


class Origin:
    """A temp bare origin repository."""

    def __init__(self, path, template_key=None):
        self.path = path  # resolved absolute Path to the bare repo directory
        # The `initial_branch` of the template this origin was copied from; None when it was not.
        self.template_key = template_key

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)


class Clone:
    """A temp working clone of an Origin."""

    def __init__(self, path, origin):
        self.path = path  # resolved absolute Path to the clone's working directory
        self.origin = origin

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)


class _Template:
    """One process's template origin + clone for an ``initial_branch``."""

    def __init__(self, origin_path, clone_path, refs):
        self.origin_path = origin_path
        self.clone_path = clone_path
        self.refs = refs  # the template origin's _ref_snapshot, taken at build


_TEMPLATES = {}  # initial_branch -> _Template, built lazily, once per process
_TEMPLATE_ROOTS = []  # temp dirs holding the templates, removed at process exit


def _remove_templates():
    for root in _TEMPLATE_ROOTS:
        shutil.rmtree(root, ignore_errors=True)
    _TEMPLATE_ROOTS.clear()
    _TEMPLATES.clear()


multiprocessing.util.Finalize(None, _remove_templates, exitpriority=10)


def _ref_snapshot(bare_dir):
    """``HEAD``, ``packed-refs`` and every file under ``refs/``, by relative path, as bytes."""
    snapshot = {}
    for name in ("HEAD", "packed-refs"):
        path = bare_dir / name
        if path.is_file():
            snapshot[name] = path.read_bytes()
    refs_dir = bare_dir / "refs"
    for path in sorted(refs_dir.rglob("*")):
        if path.is_file():
            snapshot[path.relative_to(bare_dir).as_posix()] = path.read_bytes()
    return snapshot


def _build_origin(bare_dir, initial_branch):
    """Init ``bare_dir`` as a bare repo seeded with one commit on ``initial_branch``.

    A bare repo alone has no commits and no default branch until something pushes to it, so this
    seeds it via a throwaway seed clone (created and discarded internally).
    """
    _run_git(["init", "--bare", "--initial-branch=" + initial_branch, str(bare_dir)], cwd=None)

    seed_dir = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-origin-seed-"))
    try:
        _run_git(["clone", str(bare_dir), str(seed_dir)], cwd=None)
        _run_git(["config", "user.email", "sandbox@example.invalid"], cwd=seed_dir)
        _run_git(["config", "user.name", "gh-pipeline sandbox"], cwd=seed_dir)
        readme = seed_dir / "README.md"
        readme.write_text("gh-pipeline offline test sandbox origin\n", encoding="utf-8")
        _run_git(["add", "README.md"], cwd=seed_dir)
        _run_git(["commit", "-m", "seed commit"], cwd=seed_dir)
        _run_git(["push", "origin", "HEAD:" + initial_branch], cwd=seed_dir)
    finally:
        shutil.rmtree(seed_dir, ignore_errors=True)


def _clone_into(origin_path, clone_dir):
    """``git clone`` ``origin_path`` into the empty ``clone_dir`` and set a throwaway identity."""
    # tempfile.mkdtemp already created clone_dir; `git clone` is fine cloning into an *empty*
    # pre-existing directory (it only refuses a non-empty one), so this is not a conflict.
    _run_git(["clone", str(origin_path), str(clone_dir)], cwd=None)
    _run_git(["config", "user.email", "sandbox@example.invalid"], cwd=clone_dir)
    _run_git(["config", "user.name", "gh-pipeline sandbox"], cwd=clone_dir)


def _template(initial_branch):
    template = _TEMPLATES.get(initial_branch)
    if template is None:
        root = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-template-")).resolve()
        _TEMPLATE_ROOTS.append(root)
        origin_path = root / "origin"
        clone_path = root / "clone"
        origin_path.mkdir()
        clone_path.mkdir()
        _build_origin(origin_path, initial_branch)
        _clone_into(origin_path, clone_path)
        template = _Template(origin_path, clone_path, _ref_snapshot(origin_path))
        _TEMPLATES[initial_branch] = template
    return template


def mk_origin(initial_branch="main"):
    """Create a temp bare origin repo, seeded with one commit on ``initial_branch``.

    Copied from this process's template for ``initial_branch`` (built on first use; see the module
    docstring), so callers get back an ``Origin`` that already has history to clone from, which is
    what every real caller needs. Returns an ``Origin`` whose ``.path`` is a resolved ``Path``.
    Caller is responsible for calling ``.cleanup()`` (or use the ``git_sandbox()`` context manager
    / ``addCleanup`` below).
    """
    template = _template(initial_branch)
    bare_dir = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-origin-")).resolve()
    shutil.copytree(str(template.origin_path), str(bare_dir), symlinks=True, dirs_exist_ok=True)
    return Origin(bare_dir, template_key=initial_branch)


def mk_clone(origin):
    """Clone ``origin`` (an ``Origin``) into a fresh temp working directory.

    Returns a ``Clone`` whose ``.path`` is a resolved ``Path``, with a configured throwaway
    ``user.name``/``user.email`` so commits made against it don't depend on ambient git config.
    A still-pristine template origin gets a copy of the template clone (one index refresh);
    anything else gets a real ``git clone`` (see the module docstring).
    Caller is responsible for calling ``.cleanup()`` (or use ``git_sandbox()`` / ``addCleanup``).
    """
    clone_dir = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-clone-")).resolve()
    template = _TEMPLATES.get(origin.template_key) if origin.template_key is not None else None
    if template is not None and _ref_snapshot(origin.path) == template.refs:
        shutil.copytree(str(template.clone_path), str(clone_dir), symlinks=True, dirs_exist_ok=True)
        config = clone_dir / ".git" / "config"
        text = config.read_text(encoding="utf-8")
        config.write_text(
            text.replace(str(template.origin_path), str(origin.path)), encoding="utf-8"
        )
        # The copied index carries the template's stat data (inode, ctime), so plumbing that does
        # not refresh it (`git diff-files`, `git diff-index HEAD`) would report the copy dirty.
        _run_git(["update-index", "-q", "--refresh"], cwd=clone_dir)
    else:
        _clone_into(origin.path, clone_dir)
    return Clone(clone_dir, origin)


@contextlib.contextmanager
def git_sandbox(initial_branch="main"):
    """Context manager yielding ``(origin, clone)``; cleans both up on exit, pass or fail.

    Usage::

        with git_sandbox() as (origin, clone):
            ...

    Equivalent to calling ``mk_origin()`` / ``mk_clone()`` directly and registering cleanup via
    ``addCleanup`` in a ``unittest.TestCase`` — this form is for use outside a TestCase (or when a
    test wants scoped cleanup instead of end-of-test cleanup).
    """
    origin = mk_origin(initial_branch=initial_branch)
    try:
        clone = mk_clone(origin)
        try:
            yield origin, clone
        finally:
            clone.cleanup()
    finally:
        origin.cleanup()
