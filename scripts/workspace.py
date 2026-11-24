#!/usr/bin/env python3
"""workspace.py — the workspace-lifecycle owner (architecture.md §6): ``ensure``/``remove``/``gc``
for the two-tier trust topology (project root = read-only vantage, always clean ``main``;
``.worktrees/`` = all mutable and pinned-ref state), plus root-freshness, ``root-status``, and
``lint``. This is authored from scratch against architecture.md §6/§3/§12 and the
``skills/_shared/worktree-lifecycle.md`` contract — not a transliteration of the v1 hook-runner
script (retired at S20; the v1 tree is in git history).

What this absorbs from v1, and what's new
------------------------------------------
The v1 hook runner only ran a consuming repo's ``<!-- worktree-setup/teardown -->``
commands inside an *already-existing* worktree (fail-fast setup, best-effort teardown, plus
``lint``) — creation/removal/reuse were the calling skill's own cwd-stateful ``git worktree``
calls. This script absorbs **both**: the hook execution (preserving v1's exact fail-fast /
best-effort / lint semantics — see :func:`_run_setup_hooks` for the full v1-key correspondence
table and the MALFORMED-block divergence note) *and* the work/read workspace lifecycle those v1
skill bodies used to hand-roll (create, reuse, remove, gc, root-freshness) — all now one
deterministic script per architecture.md §2's "facts by script, meaning by model."

Subcommands
-----------
    workspace.py ensure --work <branch> --base <ref> [--root <path>]
    workspace.py ensure --read <ref>                 [--root <path>]
    workspace.py attach --expect-branch <branch>     [--path <p>] [--base <ref>] [--no-hooks]
                        [--allow-root] [--pin-sha <sha>] [--expect-remote-sha <sha>]
    workspace.py remove --work <branch>              [--root <path>]
    workspace.py sync --work <branch>                [--root <path>]
    workspace.py gc [--max-age <days>]               [--root <path>]
    workspace.py root-status                         [--root <path>]
    workspace.py lint <setup|teardown>               [--root <path>]
    workspace.py tree-state --scratch <dir>          [--path <p>]

``--root`` defaults to ``.`` purely so a caller already sitting at the project root doesn't have
to repeat the obvious — every internal git call is still scoped with an explicit ``cwd`` derived
from the resolved ``--root`` or a resolved workspace path, never from the invoking process's
ambient cwd (architecture.md §6, "no ambient cwd"). Every ``--root``-taking subcommand first
normalizes to the MAIN checkout via :func:`_resolve_main_root`, so ``--root .`` from *inside* a
linked worktree still lands ``.worktrees/``, gc, removal, and ``info/exclude`` under the main
checkout — the v3 posture, where sessions run inside worktrees, depends on this.

``attach`` (v3) and ``tree-state`` (#244) are the deliberate exceptions to the no-ambient-cwd rule: each
one's *subject* is the ambient checkout, so its default ``--path .`` reads the invoking cwd on purpose —
for ``attach`` the worktree the operator started the session in, for ``tree-state`` the checkout whose
working tree the resolver's §8 gate fingerprints. ``attach`` verifies the checkout (expected branch, not the project
root, not detached, not stale against ``--expect-remote-sha``), records ``sha``/``dirty``/
``unpushed_commits``, and re-runs the ``<!-- worktree-setup -->`` hooks discovered at the
worktree's own working tree — every mismatch is a ``WORKSPACE_MISMATCH`` decision, never a
silent proceed. ``remove --work`` also emits ``WORKSPACE_MISMATCH`` (reason ``cwd_inside_target``)
when invoked from inside the worktree it would remove.

Path conventions (architecture.md §6)
--------------------------------------
    work workspace : <root>/.worktrees/<branch>
    read workspace : <root>/.worktrees/ro-<ref-slug>

``<ref-slug>`` replaces every character unsafe in a single path segment (starting with ``/``, so a
multi-segment ref like ``epic/42-journal`` collapses to one segment) with ``-`` — one worktree
directory per logical ref regardless of how many slashes its name has.

The operator's checkout is never inspected or written (architecture.md §6, §12)
------------------------------------------------------------------------------
No subcommand gates on the state of the checkout it was invoked from. Through v3 this module ran a
"root-freshness protocol" before ``ensure --work`` and ``root-status`` — on-the-default-branch +
clean + fast-forwarded, with ``ROOT_NOT_ON_MAIN`` / ``ROOT_DIRTY`` / ``ROOT_DIVERGED`` decisions and
a ``git merge --ff-only`` that MUTATED the operator's checkout. All three codes are retired: the
operator decides which branch they stand on, and that branch's working tree is exactly what supplies
hook and gate config now, so gating on it would forbid the workflow the change exists to enable
(testing an unmerged config edit). What ``ensure --work`` actually needs is ``origin/<base>``, and
it fetches that itself — a fresh worktree forks from the real remote tip no matter what the local
branch is doing. ``root-status`` survives as pure reporting (branch, HEAD, dirty, default branch).

Hook execution (the §1 carve-out)
-----------------------------------
Setup (fail-fast) and teardown (best-effort) commands come from the consuming repo's
``<!-- worktree-setup -->`` / ``<!-- worktree-teardown -->`` marker blocks (discovery: scan
``COMMANDS.md`` then ``CLAUDE.md``, then any file either ``@``-includes one level, for the first
non-empty block — matching v1's ``discover_and_parse``/``find_includes`` exactly). Each candidate
file's block is read via ``config_block.py``'s own line-scan/parse primitives — imported and
called in-process (module import + function call), never subprocess-invoked, because
architecture.md §1 permits a script to spawn only ``git``/``gh`` as *external processes*;
spawning a second ``python3 config_block.py ...`` process would be an unlisted second carve-out.
Calling its module-level scan functions directly is "delegate the block read — don't re-parse"
without re-implementing the marker-scan algorithm — see :func:`_read_hook_block_from_file`. The
extracted commands then run through :mod:`pipelib.hooks`, the ONE other explicit carve-out (a
repo-owned opaque shell string, cwd'd to the workspace); this module is its only importer, per
that module's own docstring.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
from pipelib import hooks, process  # noqa: E402
from pipelib.decisions import (  # noqa: E402
    AMBIGUOUS,
    BRANCH_IN_USE,
    WORKSPACE_MISMATCH,
    needs_decision,
)
from pipelib.envelope import EXIT_OK, emit_needs_decision, emit_ok  # noqa: E402

DEFAULT_MAX_AGE_DAYS = 7
WORKTREES_EXCLUDE_ENTRY = ".worktrees/"
READ_WORKTREE_PREFIX = "ro-"

# ---------------------------------------------------------------------------
# git helpers — every call takes an explicit cwd; nothing relies on ambient cwd
# (architecture.md §6 "no ambient cwd"). All spawns go through pipelib.process.run, the
# locked-down git/gh-only, argument-list-only, shell=False runner.
# ---------------------------------------------------------------------------


def _git(argv, cwd):
    """Run ``git <argv>`` cwd'd to ``cwd`` via the locked-down runner. Returns the
    :class:`pipelib.process.CommandResult`. Never raises on a non-zero exit — callers branch on
    ``returncode`` themselves, since "the git command failed" means different things in different
    contexts here (an ``ls-remote`` miss is a fact, not an error to bubble up)."""
    return process.run(["git"] + list(argv), cwd=str(cwd))


def _git_stdout(argv, cwd):
    """Run ``git <argv>`` and return stripped stdout, or raise ``RuntimeError`` with the faithful
    stderr on a non-zero exit — for calls this module treats as "must succeed or something is
    fundamentally wrong with the repo/workspace," not a decision-worthy outcome (e.g.
    ``rev-parse HEAD`` on a workspace this same call just created/reused)."""
    result = _git(argv, cwd)
    if result.returncode != 0:
        raise RuntimeError("git %s (cwd=%s) failed: %s" % (" ".join(argv), cwd, result.stderr))
    return result.stdout.strip()


def _is_dirty(cwd):
    return _git_stdout(["status", "--porcelain"], cwd) != ""


def _current_branch(cwd):
    return _git_stdout(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def _current_sha(cwd):
    return _git_stdout(["rev-parse", "HEAD"], cwd)


def contains_commit(cwd, sha):
    """Tri-state: does ``cwd``'s HEAD history already contain ``sha``?

    ``True`` contained — ``git merge-base --is-ancestor`` exit 0. Note a commit is its own
    ancestor, so a HEAD *equal* to ``sha`` is contained.
    ``False`` genuinely not contained (exit 1) — the checkout is behind or diverged.
    ``None`` unanswerable (any other exit, in practice 128): the object is not in this checkout's
    object DB because it was never fetched, or git failed.

    **Never fetches.** A prep that fetched to answer this would mutate the operator's checkout
    mid-read, and could turn a genuine "behind" into a false pass. Callers degrade on ``None``
    rather than reaching for the network; the tri-state exists so "not contained" and "cannot
    tell" stay distinguishable at the call site."""
    result = _git(["merge-base", "--is-ancestor", sha, "HEAD"], cwd)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _remote_branch_exists(cwd, branch):
    """True iff ``origin/<branch>`` exists on the remote — a live probe via ``git ls-remote``, not
    a local remote-tracking-ref lookup. A freshly created local branch has no remote-tracking ref
    yet even when the remote branch already exists (e.g. pushed from another workspace since this
    one last fetched), so a local-only check would be a false negative."""
    result = _git(["ls-remote", "--exit-code", "--heads", "origin", branch], cwd)
    return result.returncode == 0


def _local_branch_exists(cwd, branch):
    """True iff a local branch named ``<branch>`` exists in ``cwd``'s repo (``refs/heads/<branch>``),
    regardless of whether any worktree currently has it checked out. Used only when no worktree is
    already attached to ``<branch>`` (that case is handled earlier by ``_find_worktree_for_branch``)
    — e.g. a clone that once had a worktree for this branch, removed it, but kept the local branch
    ref. A plain ``git worktree add -b <branch> ...`` would fail with "branch already exists" in
    that case, so callers must check this first and attach rather than create."""
    result = _git(["show-ref", "--verify", "--quiet", "refs/heads/%s" % branch], cwd)
    return result.returncode == 0


def _upstream_ref(cwd, branch, base_ref):
    """The remote ref a branch's "not yet pushed" state is measured against: ``origin/<branch>``
    when the branch has been published, else ``origin/<base_ref>`` (a never-pushed branch)."""
    return "origin/%s" % (branch if _remote_branch_exists(cwd, branch) else base_ref)


def _unpushed_commits(cwd, branch, base_ref):
    """Count commits on ``HEAD`` not yet on the remote. If ``origin/<branch>`` exists, count
    relative to it (the true "not yet pushed" count for a branch with some history already
    published). Otherwise the branch has never been pushed — count relative to
    ``origin/<base_ref>`` instead, so a freshly created worktree with zero new commits reports 0,
    not the entire base branch's history."""
    upstream = _upstream_ref(cwd, branch, base_ref)
    return int(_git_stdout(["rev-list", "--count", "%s..HEAD" % upstream], cwd))


def _unpushed_commits_vs_all_remotes(cwd):
    """Count commits reachable from ``HEAD`` but from no ``origin/*`` ref — the "is any of this
    work only local?" question, answered without naming a base branch, without the network, and
    without a default-branch derivation that could fail. Used by ``remove --work``, whose whole
    point is that reclaiming a workspace works offline."""
    return int(_git_stdout(["rev-list", "--count", "HEAD", "--not", "--remotes=origin"], cwd))


def _list_worktrees(root):
    """Parse ``git worktree list --porcelain`` into a list of dicts:
    ``{"path": <resolved Path>, "sha": <str>, "branch": <str|None>, "detached": <bool>}``.
    ``branch`` is the bare branch name (``refs/heads/`` stripped) or ``None`` for a detached
    worktree. Blocks are separated by a blank line; each has a ``worktree <path>`` line, a
    ``HEAD <sha>`` line, then either ``branch refs/heads/<name>`` or the literal ``detached``."""
    stdout = _git_stdout(["worktree", "list", "--porcelain"], root)
    entries = []
    current = None
    for line in stdout.splitlines():
        if line.startswith("worktree "):
            current = {
                "path": Path(line[len("worktree "):]).resolve(),
                "sha": None,
                "branch": None,
                "detached": False,
            }
            entries.append(current)
        elif line.startswith("HEAD ") and current is not None:
            current["sha"] = line[len("HEAD "):]
        elif line.startswith("branch ") and current is not None:
            ref = line[len("branch "):]
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached" and current is not None:
            current["detached"] = True
    return entries


def _find_worktree_for_branch(worktrees, branch):
    for entry in worktrees:
        if entry["branch"] == branch:
            return entry
    return None


def _find_worktree_at_path(worktrees, path):
    resolved = Path(path).resolve()
    for entry in worktrees:
        if entry["path"] == resolved:
            return entry
    return None


def _slugify_ref(ref):
    """Replace every character unsafe in a single filesystem path segment with ``-`` (this
    includes ``/``, so a multi-segment ref collapses to one segment) — the result is always one
    path-segment-safe string, matching architecture.md §6's ``ro-<ref-slug>`` convention."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", ref)


def _work_path(root, branch):
    return (Path(root) / ".worktrees" / branch).resolve()


def _read_path(root, ref):
    return (Path(root) / ".worktrees" / (READ_WORKTREE_PREFIX + _slugify_ref(ref))).resolve()


def list_work_branches(root):
    """Sorted bare branch names of the WORK worktrees under ``<root>/.worktrees/`` — never a
    ``ro-*`` read workspace, never the main checkout, never a detached one. The local, offline
    answer to "which branches does this repo currently have a workspace open for", which
    ``prep_workspace_close.py`` uses as branch-resolution evidence for an issue number.

    An ``epic/<N>-<slug>`` work worktree lives at ``.worktrees/epic/<N>-<slug>`` (``_work_path``
    keeps the slash), so the ``ro-`` test is on the first segment BELOW ``.worktrees/``, not the
    basename."""
    root = str(_resolve_main_root(root))
    worktrees_dir = (Path(root) / ".worktrees").resolve()
    branches = []
    for entry in _list_worktrees(root):
        if entry["branch"] is None or entry["detached"]:
            continue
        try:
            relative = entry["path"].relative_to(worktrees_dir)
        except ValueError:
            continue  # the main checkout, or a worktree the operator put elsewhere
        if not relative.parts or relative.parts[0].startswith(READ_WORKTREE_PREFIX):
            continue
        branches.append(entry["branch"])
    return sorted(branches)


# ---------------------------------------------------------------------------
# `.worktrees/` exclusion — idempotent single-line append to the repo's `info/exclude`
# (architecture.md §6). D4/D6 fix (docs/specs/parity/planner.md): this used to write
# `<root>/.gitignore` — a WORKING-TREE file, so `git status --porcelain` at root reported it as a
# new untracked/modified file the instant it was written, and the NEXT root-freshness check (the
# very next `ensure`/`root-status` call in the same clone/session) read that self-inflicted dirt as
# `ROOT_DIRTY` — making a prep script that calls `ensure` more than once in one clone (e.g. the
# planner's just-in-time composite epic+story session, which grounds twice) non-re-runnable, and
# forcing the consuming repo to commit a plugin-authored `.gitignore` edit it never asked for.
# The `ROOT_DIRTY` gate itself is retired, but this stays load-bearing for a plainer reason: a
# plugin-authored write must never show up in the operator's `git status`, whether or not anything
# gates on it.
# `.git/info/exclude` has the identical ignore semantics as `.gitignore` (both are gitignore-syntax
# pattern files git consults when computing untracked status) but lives OUTSIDE the working tree —
# under the git directory itself — so writing to it can never appear in `git status --porcelain`
# and root-freshness can never trip on our own write. It also already exists as the repo's own
# "local, un-shared exclude list" convention (`git help gitignore`), so this isn't a novel
# mechanism, just the correct one for a tool-authored exclusion no consuming repo should have to
# commit.
# ---------------------------------------------------------------------------


def _toplevel_of(path):
    """The working-tree root of the checkout containing ``path`` — ``git rev-parse
    --show-toplevel``, NOT ``_resolve_main_root``. The two differ inside a linked worktree, and
    the callers that want this one want the checkout the operator is actually standing in."""
    return str(Path(_git_stdout(["rev-parse", "--show-toplevel"], str(Path(path).resolve()))).resolve())


def invoking_checkout(root):
    """The checkout the operator is standing in — the process cwd's working-tree root — when that
    is a checkout of the SAME repo as ``root``; otherwise ``root``'s own toplevel.

    This is what "the operator decides which branch they are in before running workspace-open"
    needs: a caller that normalizes ``--root`` to the main checkout (every prep does) would
    otherwise lose the invoker's worktree entirely and silently read hook config from the main
    checkout instead. The same-repo guard keeps a session started in some unrelated directory
    (a test runner, another project) from supplying this repo's hook commands.
    """
    root_top = _toplevel_of(root)
    try:
        cwd_top = _toplevel_of(Path.cwd())
        same_repo = str(_resolve_main_root(cwd_top)) == str(_resolve_main_root(root_top))
    except (RuntimeError, OSError):
        # cwd isn't a git checkout — or has been deleted out from under the process, which raises
        # OSError from Path.cwd() rather than RuntimeError. Either way the root argument is the
        # only answer, and neither case is worth failing an otherwise-valid open over.
        return root_top
    return cwd_top if same_repo else root_top


def _resolve_main_root(root):
    """Normalize ``root`` to the MAIN checkout: the parent of ``git rev-parse --git-common-dir``.
    Identity when ``root`` already is the main checkout (common dir = ``<root>/.git``); hops to
    the main checkout when ``root`` — or the ambient cwd behind ``--root .`` — is a linked
    worktree. Every ``_build_*`` core applies this first, so ``.worktrees/`` placement, gc,
    removal, branch exclusivity, and ``info/exclude`` behave identically no matter which checkout
    the invoking session sits in (the v3 operator-owned-worktree posture). Assumes a non-bare main
    checkout (the common dir's parent IS the checkout) — true for every ``git clone``; exotica
    (bare repos, detached git dirs) hard-fail earlier in ``_git_common_dir``'s rev-parse with
    faithful stderr rather than being guessed at."""
    return _git_common_dir(str(Path(root).resolve())).parent


_DEFAULT_BRANCH_CACHE = {}


def default_branch(root):
    """The repo's DEFAULT branch, derived — never the hardcoded ``"main"`` this module carried
    through v3 (a repo on ``master``/``develop``/``trunk`` was simply unsupported).

    Derivation order, and why it matters:

    1. ``git symbolic-ref refs/remotes/origin/HEAD`` — offline, recorded by ``git clone``, so it
       answers in every checkout and in the offline test sandbox. **This must stay first**: the
       test harness's ``gh`` shim matches fixture argv by exact list equality and exits 2 on a
       miss, so a ``gh``-first order would break every fixtured prep case, and
       ``prep_workspace_close.py``'s zero-``gh`` branch-resolution path (its whole point is that
       closing a workspace needs no network) would start spawning ``gh``.
    2. ``gh repo view --json defaultBranchRef`` — the authoritative answer, for a checkout whose
       ``origin/HEAD`` was never set (an old clone, or ``git init`` + ``git remote add``).
    3. Neither → hard failure (exit 1) naming the one-line remedy, rather than guessing a name and
       cutting branches off the wrong base.

    Cached per resolved main root: a prep is one process per run, and several call sites
    (base selection, attach's unpushed-count fallback, plan-ref selection) want the same answer.
    """
    main_root = str(_resolve_main_root(root))
    if main_root in _DEFAULT_BRANCH_CACHE:
        return _DEFAULT_BRANCH_CACHE[main_root]

    prefix = "refs/remotes/origin/"
    symbolic = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], main_root)
    name = None
    if symbolic.returncode == 0 and symbolic.stdout.strip().startswith(prefix):
        name = symbolic.stdout.strip()[len(prefix):]

    if not name:
        try:
            view = process.run(["gh", "repo", "view", "--json", "defaultBranchRef"], cwd=main_root)
        except OSError:
            view = None  # `gh` not installed at all — fall through to the remedy below
        if view is not None and view.returncode == 0:
            try:
                name = (json.loads(view.stdout).get("defaultBranchRef") or {}).get("name")
            except ValueError:
                name = None

    if not name:
        sys.stderr.write(
            "cannot determine the repo's default branch: refs/remotes/origin/HEAD is unset in %s "
            "and `gh repo view` did not answer.\n"
            "Remedy: run `git remote set-head origin --auto` in that checkout, then re-run.\n"
            % main_root
        )
        sys.exit(1)

    _DEFAULT_BRANCH_CACHE[main_root] = name
    return name


def _git_common_dir(root):
    """Resolve the repo's COMMON git directory (`git rev-parse --git-common-dir`), never
    `--git-dir` — the two differ the moment `root` is itself a linked worktree (verified directly
    against real `git` behavior, not assumed): `--git-dir` from inside a worktree resolves to that
    worktree's PRIVATE per-worktree admin dir (`<main-repo>/.git/worktrees/<name>`), which has no
    `info/exclude` of its own; `--git-common-dir` resolves to the ONE shared dir every linked
    worktree (and the main checkout) points back at, so a write there is visible from all of them —
    exactly the "applies to all linked worktrees" property this fix needs, since `.worktrees/`
    itself lives under the MAIN checkout, not under any of the worktrees it names. The result may
    be relative (bare `.git` when `root` is a plain, non-worktree clone) or absolute (when `root`
    is itself a worktree) — `(Path(root) / result).resolve()` handles both: joining a `Path` with
    an absolute second operand discards the first per POSIX path-join semantics, so the relative
    case resolves against `root` and the absolute case passes through unchanged.
    """
    common_dir = _git_stdout(["rev-parse", "--git-common-dir"], root)
    return (Path(root) / common_dir).resolve()


def _ensure_worktrees_excluded(root):
    """Ensure the repo's `info/exclude` (resolved via :func:`_git_common_dir`, never a hardcoded
    `.git/info/exclude`) contains a line exactly `.worktrees/`. Idempotent: if already present, the
    file is left byte-for-byte untouched (not even rewritten) — the same "changed:false means
    untouched on disk" discipline `config_block.py` uses for its own idempotent writes. Creates
    `info/exclude` (and its parent `info/` dir) from scratch if absent. Returns `True` iff a write
    happened. Deliberately does NOT touch any `.gitignore` the consuming repo already carries
    (including a pre-existing `.worktrees/` bootstrap line an earlier v2 run may have left there —
    that line is harmless dead weight, not migrated or removed; this function only ever reads
    `info/exclude`, never `.gitignore`, on the write path or the idempotency check)."""
    path = _git_common_dir(root) / "info" / "exclude"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(WORKTREES_EXCLUDE_ENTRY + "\n", encoding="utf-8")
        return True

    text = path.read_text(encoding="utf-8")
    if any(line.strip() == WORKTREES_EXCLUDE_ENTRY.rstrip("/") + "/" for line in text.splitlines()):
        return False  # already present — file left byte-for-byte untouched

    if text and not text.endswith("\n"):
        text += "\n"
    text += WORKTREES_EXCLUDE_ENTRY + "\n"
    path.write_text(text, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Hook discovery + execution — the §1 carve-out. workspace.py is pipelib.hooks's only importer.
# ---------------------------------------------------------------------------

_LIST_ITEM_COMMAND_RE = re.compile(r"^\s*-\s*`([^`]*)`")


def _parse_command_list(interior_lines):
    """Extract one backtick-quoted command per Markdown list item — v1's
    ``sed -n 's/^[[:space:]]*-[[:space:]]*`\\([^`]*\\)`.*/\\1/p'``, re-expressed: a line is
    ``- `<command>` — <description>``; the description is ignored, matching
    ``skills/_shared/worktree-lifecycle.md``'s documented block format."""
    commands = []
    for line in interior_lines:
        match = _LIST_ITEM_COMMAND_RE.match(line)
        if match:
            commands.append(match.group(1))
    return commands


def _discover_hook_commands(root, phase):
    """Discover the ``<!-- worktree-<phase> -->`` block in ``root``'s WORKING TREE, delegating the
    candidate walk and the marker scan to ``config_block.read_block_anywhere`` (COMMANDS.md ->
    CLAUDE.md -> one level of ``@``-include; a malformed block in one candidate is treated exactly
    like an absent one, so the walk continues). This module carried its own byte-identical copies
    of that walk and scan until the pin retired; one implementation now serves hook discovery and
    gate-config discovery alike.

    Returns ``(phase_present, commands, source_file_or_none)``."""
    present, interior, source = config_block.read_block_anywhere(str(root), "worktree-%s" % phase)
    return (present, _parse_command_list(interior) if present else [], source)


def _tail_lines(text, n):
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:])


def _execute_setup_commands(phase_present, commands, workspace_path):
    """The shared fail-fast setup-command executor behind every setup-hook run (ensure and
    attach alike — both now discover from a working tree, so there is one discovery path and one
    executor). Same result shape as always: ``{"phase_present", "commands_run", "succeeded",
    "first_failure"?}``."""
    result = {"phase_present": phase_present, "commands_run": 0, "succeeded": True}
    for step, command_text in enumerate(commands, start=1):
        hook_result = hooks.run_repo_owned_hook_command(command_text, cwd=workspace_path)
        result["commands_run"] += 1
        if hook_result.returncode != 0:
            result["succeeded"] = False
            result["first_failure"] = {
                "step": step,
                "command": command_text,
                "output_tail": _tail_lines(hook_result.stdout + (hook_result.stderr or ""), 50),
            }
            break
    return result


def _hook_source_facts(root, source_file):
    """Provenance for a hook/teardown run: WHICH checkout supplied the block, on what branch, at
    what commit, and whether that tree had uncommitted changes. Reporting, never gating — the v3.x
    posture trusts the operator's checkout, so the compensating control is that every run says
    where its commands came from. ``file`` is ``None`` when no block was found at all."""
    facts = {"path": str(root), "file": source_file}
    try:
        facts["branch"] = _current_branch(root)
        facts["sha"] = _current_sha(root)
        facts["dirty"] = _is_dirty(root)
    except RuntimeError:
        pass  # not a git checkout (never happens on the three real call paths) — report what we have
    return facts


def _run_setup_hooks(root, workspace_path):
    """Fail-fast: run every discovered setup command inside ``workspace_path`` in declaration
    order; stop at the first non-zero exit. Returns ``{"phase_present", "commands_run",
    "succeeded", "first_failure"?}``.

    Hook result-key mapping (v1 hook runner -> v2 ``workspace.py``)
    ---------------------------------------------------------------------
    Preserved verbatim (same key, same meaning, same shape) across both setup and teardown:
    ``phase_present``, ``commands_run``, ``succeeded``, ``first_failure`` (``{step, command,
    output_tail}``, setup only), ``failures`` (list of that same shape, teardown only). ``step`` is
    1-based; ``output_tail`` is the last 50 lines of the failing command's combined stdout+stderr.

    Dropped from v1, and why:
      - ``dry_run`` — v1's ``setup``/``teardown`` subcommands had a ``--dry-run`` flag that ran the
        discover/parse step but skipped execution. v2's ``ensure``/``remove`` have no dry-run mode
        at all (nothing to opt out of mid-envelope); ``lint`` is v2's non-executing preview instead,
        used standalone rather than as a flag on the executing subcommand.
      - ``reused`` (setup) / ``op`` — v1 put these on the hook-result object itself because
        ``setup``/``teardown`` were the whole CLI surface. v2 hoists both up a level: ``reused``
        lives on the parent ``ensure --work`` envelope's own payload (alongside ``path``, ``branch``,
        ``sha``, etc.), and ``op`` is likewise the parent envelope's field (``"ensure"``/``"remove"``)
        — the hook result itself is just one nested value under that envelope's ``"setup"``/
        ``"teardown"`` key, not a top-level, self-describing result.

    ``lint`` (a separate subcommand here, see :func:`_cmd_lint`) reports ``{"phase",
    "phase_present", "command_count", "would_run"}`` — this is v2's rendering of v1's
    ``lint``/``--dry-run`` preview shape (discover + parse, no execution, no worktree needed).

    Deliberate v1 divergence: a MALFORMED block. The v1 hook runner's ``discover_and_parse``
    hard-exits 2 with ``MALFORMED_BLOCK: ...`` the moment a candidate file's block is malformed
    (duplicate/unterminated), even though earlier or later candidates might still yield a good one.
    ``config_block.read_block_anywhere`` (the shared discovery this delegates to) instead treats a
    malformed block in one candidate file exactly like an absent one — ``phase_present=False`` for that file, try the next candidate — and
    only reports no-block-found after exhausting every candidate. This is intentional, not an
    oversight: the closed decision-code set (architecture.md §3, ``pipelib/decisions.py``) has no
    malformed-block code, so degrading gracefully to "try the next candidate" is safer than
    inventing a new code or hard-blocking the whole ``ensure``/``remove`` envelope over one
    malformed candidate file when a later candidate (or "no block at all") would have been fine.
    """
    phase_present, commands, source_file = _discover_hook_commands(root, "setup")
    result = _execute_setup_commands(phase_present, commands, workspace_path)
    result["source"] = _hook_source_facts(root, source_file)
    return result


def _run_teardown_hooks(root, workspace_path):
    """Best-effort: run every discovered teardown command inside ``workspace_path`` in order,
    logging (never stopping on) each failure. Always reports ``succeeded: True`` (v1's own
    contract: teardown always exits 0). Returns ``{"phase_present", "commands_run", "succeeded"
    (always True), "failures": [...], "source"}`` — see :func:`_run_setup_hooks` for the full
    v1-key correspondence table (shared by both hook runners) and the MALFORMED-block divergence
    note.

    ``remove --work`` passes the worktree BEING CLOSED as ``root``, so a branch's teardown block
    is the one that runs for that branch's workspace — the same working-tree rule setup follows.
    Note the interaction with the dirty gate: an *uncommitted* teardown edit can never execute,
    because a dirty worktree is refused (``AMBIGUOUS``) before teardown is reached. ``lint
    --phase teardown --root <worktree>`` is the preview for an uncommitted edit."""
    phase_present, commands, source_file = _discover_hook_commands(root, "teardown")
    result = {"phase_present": phase_present, "commands_run": 0, "succeeded": True, "failures": []}
    result["source"] = _hook_source_facts(root, source_file)
    for step, command_text in enumerate(commands, start=1):
        hook_result = hooks.run_repo_owned_hook_command(command_text, cwd=workspace_path)
        result["commands_run"] += 1
        if hook_result.returncode != 0:
            result["failures"].append({
                "step": step,
                "command": command_text,
                "output_tail": _tail_lines(hook_result.stdout + (hook_result.stderr or ""), 50),
            })
    return result


# ---------------------------------------------------------------------------
# ensure --work
# ---------------------------------------------------------------------------


def branch_in_use_decision(root, branch, worktrees=None):
    """The branch-exclusivity check, alone and non-mutating: the ``BRANCH_IN_USE`` decision when
    ``branch`` is already checked out somewhere other than its ``.worktrees/<branch>`` path, else
    ``None``. ``root`` must already be the main checkout. ``_build_ensure_work`` raises it from
    here, and ``prep_workspace_open`` asks it first, BEFORE its ``gh issue develop`` link create —
    a create pushes a branch to origin and links it, so a card raised after it would leave both
    behind when declined (skills/workspace-open/SKILL.md §1)."""
    if worktrees is None:
        worktrees = _list_worktrees(root)
    target_path = _work_path(root, branch)
    existing_for_branch = _find_worktree_for_branch(worktrees, branch)
    if existing_for_branch is None or existing_for_branch["path"] == target_path:
        return None
    return needs_decision(
        BRANCH_IN_USE,
        summary="branch %r is already checked out at %s" % (branch, existing_for_branch["path"]),
        context={
            "branch": branch,
            "existing_path": str(existing_for_branch["path"]),
            "requested_path": str(target_path),
        },
        options=[
            "use the existing worktree at %s" % existing_for_branch["path"],
            "remove that worktree first, then re-run",
        ],
    )


def _build_ensure_work(root, branch, base, hook_root=None):
    """Pure ``ensure --work`` core (architecture.md §2's pure-core pattern, S8 pattern lock):
    create/reuse the work worktree on ``branch`` (checked out at its own head when it exists, else
    forked at ``origin/<base>``), run setup hooks, and return ``(payload, notices, decision)`` —
    **never emits** (no ``print``/``emit_ok``/``emit_needs_decision``). ``prep_evaluator`` calls
    this directly, retiring the S6 ``redirect_stdout`` bridge.

    Hook discovery reads **the invoking checkout's working tree**, which is ``hook_root`` when the
    caller supplies it and otherwise ``root`` as given, resolved BEFORE the main-root normalization
    every other operation here applies. The explicit parameter exists because a caller that has
    already normalized ``root`` itself (``prep_workspace_open``) would otherwise lose the invoker's
    checkout entirely and silently fall back to the main one. The two differ whenever the operator
    runs this from inside another worktree, and the invoking checkout is the one whose branch they
    chose: "the operator decides which branch they are in before opening a workspace" is the whole
    point of retiring the pin. The freshly created worktree cannot be the source — on a fresh
    branch it has no content of its own yet.

    Return contract (the type-level distinction the S8 retro asks for):
      - ``decision is not None`` → a ``needs_decision`` (``ROOT_*`` from freshness, or
        ``BRANCH_IN_USE``); ``payload`` is ``None`` and the caller propagates the decision.
      - ``decision is None`` → success or a *setup-hook failure*: both carry a ``payload``. A
        setup-hook failure is a **partial-but-honest** envelope (``payload["setup"]["succeeded"]``
        is ``False`` and ``sha``/``dirty``/``unpushed_commits`` are absent), rides in ``payload``
        exactly as v1 emitted it, and the emit wrapper maps it to process exit 1 by inspecting
        ``setup.succeeded`` — the caller (prep) likewise reads ``setup.succeeded`` rather than a
        process exit code, so nothing about the observed behavior changes.
      - a hard `git` failure still ``sys.exit(1)`` with faithful stderr and no envelope (§3
        exit-code contract — unchanged), not a returnable decision.
    """
    hook_root = _toplevel_of(hook_root if hook_root is not None else root)
    root = str(_resolve_main_root(root))
    # No root-freshness gate and no fast-forward: v3.x never inspects or mutates the operator's
    # checkout. The branch/dirty/diverged state of wherever they are standing is their business —
    # what this call needs is `origin/<base>`, which the fetch below makes current regardless of
    # the local branch's state, so a fresh worktree still forks from the real remote tip.
    fetch_result = _git(["fetch", "origin", base], root)
    if fetch_result.returncode != 0:
        sys.stderr.write(fetch_result.stderr)
        sys.exit(1)

    target_path = _work_path(root, branch)
    worktrees = _list_worktrees(root)

    in_use = branch_in_use_decision(root, branch, worktrees)
    if in_use is not None:
        return None, [], in_use

    reused = target_path.is_dir() and _find_worktree_at_path(worktrees, target_path) is not None
    if not reused:
        # No worktree to reuse — pick the checkout source by branch existence, so an EXISTING
        # branch (e.g. a PR head an evaluator must check out) lands the worktree at that branch's
        # actual head, never silently at --base (architecture.md §6's "SHA" fact must be the
        # branch's own head, not main's).
        branch_on_remote = _remote_branch_exists(root, branch)
        if branch_on_remote:
            fetch_branch_result = _git(["fetch", "origin", branch], root)
            if fetch_branch_result.returncode != 0:
                sys.stderr.write(fetch_branch_result.stderr)
                sys.exit(1)

        if branch_on_remote and _local_branch_exists(root, branch):
            # Local branch ref exists (e.g. left behind by a worktree removed earlier) but no
            # worktree currently has it checked out (ruled out above) — attach it, then fast-
            # forward/reset it to origin/<branch>'s head so a stale local ref never wins over the
            # remote's actual head.
            add_result = _git(["worktree", "add", str(target_path), branch], root)
            if add_result.returncode != 0:
                sys.stderr.write(add_result.stderr)
                sys.exit(1)
            reset_result = _git(
                ["reset", "--hard", "origin/%s" % branch], str(target_path),
            )
            if reset_result.returncode != 0:
                sys.stderr.write(reset_result.stderr)
                sys.exit(1)
        elif branch_on_remote:
            # No local branch at all — create one tracking origin/<branch>, checked out at its
            # head, in the same worktree-add call.
            add_result = _git(
                ["worktree", "add", "-b", branch, str(target_path), "origin/%s" % branch],
                root,
            )
            if add_result.returncode != 0:
                sys.stderr.write(add_result.stderr)
                sys.exit(1)
        else:
            # Neither a worktree nor an origin branch — the resolver's fresh-work case: create a
            # new branch at origin/<base> (unchanged from prior behavior).
            add_result = _git(
                ["worktree", "add", "-b", branch, str(target_path), "origin/%s" % base],
                root,
            )
            if add_result.returncode != 0:
                sys.stderr.write(add_result.stderr)
                sys.exit(1)

    _ensure_worktrees_excluded(root)

    hook_result = _run_setup_hooks(hook_root, str(target_path))
    notices = ["HOOK_SOURCE_DIRTY"] if (hook_result.get("source") or {}).get("dirty") else []
    if not hook_result["succeeded"]:
        # Fail-fast (matching v1's setup exit 1): the worktree exists but isn't ready — report the
        # failure with as much fact context as we already have, but do NOT compute/report
        # dirty/unpushed/sha, since those facts are about a workspace ready for use. This is the
        # partial-but-honest payload the emit wrapper maps to exit 1 (setup.succeeded == False).
        return {
            "op": "ensure",
            "kind": "work",
            "path": str(target_path),
            "branch": branch,
            "base_ref": base,
            "reused": reused,
            "setup": hook_result,
        }, notices, None

    dirty = _is_dirty(str(target_path))
    unpushed = _unpushed_commits(str(target_path), branch, base)
    sha = _current_sha(str(target_path))

    return {
        "op": "ensure",
        "kind": "work",
        "path": str(target_path),
        "branch": branch,
        "base_ref": base,
        "reused": reused,
        "dirty": dirty,
        "unpushed_commits": unpushed,
        "sha": sha,
        "setup": hook_result,
    }, notices, None


def _cmd_ensure_work(args):
    payload, notices, decision = _build_ensure_work(args.root, args.branch, args.base)
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return EXIT_OK
    emit_ok(payload=payload, notices=notices)
    # A setup-hook failure is an ok envelope but a non-zero process exit (v1's fail-fast exit 1).
    if not (payload.get("setup") or {}).get("succeeded", True):
        return 1
    return EXIT_OK


# ---------------------------------------------------------------------------
# ensure --read
# ---------------------------------------------------------------------------


def _build_ensure_read(root, ref):
    """Pure ``ensure --read`` core (S8 pattern lock): create/refresh the detached read workspace at
    ``origin/<ref>`` and return ``(payload, notices, decision)`` — **never emits**. ``ensure
    --read`` has no root-freshness gate and no ``needs_decision`` path (it grounds directly at a
    freshly fetched ``origin/<ref>``), so ``decision`` is always ``None`` here; the tuple shape is
    kept uniform with every other retrofitted core. A hard `git` failure still ``sys.exit(1)`` with
    faithful stderr and no envelope (§3 — unchanged)."""
    root = str(_resolve_main_root(root))

    fetch_result = _git(["fetch", "origin", ref], root)
    if fetch_result.returncode != 0:
        sys.stderr.write(fetch_result.stderr)
        sys.exit(1)

    target_path = _read_path(root, ref)
    remote_ref = "origin/%s" % ref

    _ensure_worktrees_excluded(root)

    if target_path.is_dir():
        reset_result = _git(["reset", "--hard", remote_ref], str(target_path))
        if reset_result.returncode != 0:
            sys.stderr.write(reset_result.stderr)
            sys.exit(1)
        # `reset --hard` only rewrites tracked content; it leaves untracked files (e.g. scratch
        # output a prior grounding read left behind) in place. A read workspace must be a pristine
        # view of exactly origin/<ref> on every ensure, so also discard untracked files/dirs.
        clean_result = _git(["clean", "-fd"], str(target_path))
        if clean_result.returncode != 0:
            sys.stderr.write(clean_result.stderr)
            sys.exit(1)
        reused = True
    else:
        add_result = _git(["worktree", "add", "--detach", str(target_path), remote_ref], root)
        if add_result.returncode != 0:
            sys.stderr.write(add_result.stderr)
            sys.exit(1)
        reused = False

    sha = _current_sha(str(target_path))
    return {
        "op": "ensure",
        "kind": "read",
        "path": str(target_path),
        "ref": ref,
        "reused": reused,
        "sha": sha,
    }, [], None


def _cmd_ensure_read(args):
    payload, notices, decision = _build_ensure_read(args.root, args.ref)
    if decision is not None:  # pragma: no cover — ensure --read has no decision path today
        emit_needs_decision(decision, notices=notices)
        return EXIT_OK
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---------------------------------------------------------------------------
# attach (v3) — verify the AMBIENT checkout is the expected workspace, record its facts, re-run
# the setup hooks. The observe-and-assert replacement for ensure --work on the pipeline stages:
# the operator opens the worktree (workspace-open) and starts the session inside it; the session's
# prep attaches instead of creating.
# ---------------------------------------------------------------------------

# The code-wide closed reason set for WORKSPACE_MISMATCH decisions, with per-operation
# applicability: attach emits the first four; remove --work emits only `cwd_inside_target`
# (attach runs *inside* the worktree by design, so that reason can never gate attach).
ATTACH_MISMATCH_REASONS = ("at_project_root", "detached_head", "branch_mismatch", "stale_checkout")
REMOVE_MISMATCH_REASON = "cwd_inside_target"


def _workspace_mismatch(reason, summary, context, options):
    context = dict(context)
    context["reason"] = reason
    return needs_decision(WORKSPACE_MISMATCH, summary=summary, context=context, options=options)


def _build_attach(
    path,
    expected_branch,
    base_for_unpushed=None,
    run_hooks=True,
    allow_main_root=False,
    expected_remote_sha=None,
    require_exact_remote_sha=False,
    accept_branch_pattern=None,
    check_remote_staleness=False,
):
    """Pure ``attach`` core (v3): verify the checkout at ``path`` (default the ambient cwd — the
    ONE deliberate ambient read in this module; attach's subject IS the checkout the session was
    started in), record its facts, and re-run the setup hooks. Returns
    ``(payload, notices, decision)`` — **never emits**.

    Checks, in order (each failure is a ``WORKSPACE_MISMATCH`` decision with ``context.reason``
    from :data:`ATTACH_MISMATCH_REASONS`):

    1. ``at_project_root`` — the checkout is the main checkout itself, and ``allow_main_root`` is
       False (the "operator forgot workspace-open" surface; the planner passes True for a ``main``
       plan_ref, where planning from the project root is the canonical posture).
    2. ``detached_head`` — always a mismatch, ``allow_main_root`` or not.
    3. ``branch_mismatch`` — ambient branch != ``expected_branch`` (a NAME check, right for the
       resolver and evaluator, whose branch is what they push or judge; the planner settles its
       vantage by content first — ``prep_planner._probe_ambient`` — and names the branch it
       accepted here). With
       ``accept_branch_pattern`` (a regex string), a pattern match on the ambient branch passes
       instead of exact equality — the resolver's `-vN` no-recompute rule: it accepts any ambient
       `^<N>-` branch rather than re-running collision naming against a branch workspace-open
       already pushed (which would count the pushed branch itself and yield `-v2`, a guaranteed
       self-mismatch); ``expected_branch`` then only words the mismatch card.
    4. ``stale_checkout`` — two triggers. Explicit: ``expected_remote_sha`` is supplied and
       ambient HEAD is not equal to it and not ahead of it (i.e. behind or diverged — "ahead"
       means the remote SHA is an ancestor of HEAD, which is the normal unpushed-local-work shape
       and passes); with ``require_exact_remote_sha`` (the evaluator: never health-check or cache
       against code the checkout doesn't contain), any inequality is the mismatch. Self-derived:
       ``check_remote_staleness`` looks up ``origin/<ambient-branch>``'s tip via ``git ls-remote``
       (a remote query, not a fetch) and applies the same behind/diverged rule — no fetch is
       needed for the ahead test, because an ahead checkout by definition already contains the
       remote SHA in its local history, and a behind one by definition cannot.

    Hooks: with ``run_hooks``, the ``<!-- worktree-setup -->`` commands are discovered in **this
    checkout's own working tree** — the worktree the session runs in, uncommitted edits included —
    and run fail-fast inside it, the every-session-entry hook cadence
    ``skills/_shared/worktree-lifecycle.md`` requires. Until v3.x this read ``origin/main`` blobs at
    a pin, so a branch could not supply hook commands to an automatic path; that pin is retired by
    operator decision (the operator chooses the checkout, and a branch versioning its own hooks is
    the point — it is the only way to test a hook change before merging it). ``setup.source``
    reports which checkout/branch/SHA answered and whether it was dirty: reporting is the
    compensating control, and there is deliberately no confirmation gate. A setup-hook failure is
    the same partial-but-honest payload shape as ``ensure --work`` (``setup.succeeded: false``, no
    ``sha``/``dirty``/``unpushed_commits``), mapped to process exit 1 by the emit wrapper.
    """
    top_result = _git(["rev-parse", "--show-toplevel"], str(Path(path).resolve()))
    if top_result.returncode != 0:
        sys.stderr.write(top_result.stderr)
        sys.exit(1)
    top = str(Path(top_result.stdout.strip()).resolve())
    main_root = str(_resolve_main_root(top))

    if top == main_root and not allow_main_root:
        return None, [], _workspace_mismatch(
            "at_project_root",
            summary="session is running at the project root, not inside a work worktree",
            context={"path": top, "expected_branch": expected_branch, "main_root": main_root},
            options=[
                "run the workspace-open skill for this issue, then start this session inside the "
                "worktree it prints",
                "if a worktree already exists under .worktrees/, start the session there instead",
            ],
        )

    branch = _current_branch(top)
    if branch == "HEAD":
        return None, [], _workspace_mismatch(
            "detached_head",
            summary="session checkout is on a detached HEAD, not branch %r" % expected_branch,
            context={"path": top, "expected_branch": expected_branch, "main_root": main_root},
            options=["checkout the expected branch in this worktree, then re-run"],
        )

    branch_accepted = branch == expected_branch
    if not branch_accepted and accept_branch_pattern is not None:
        branch_accepted = re.match(accept_branch_pattern, branch) is not None
    if not branch_accepted:
        return None, [], _workspace_mismatch(
            "branch_mismatch",
            summary="session checkout is on branch %r but the target expects branch %r"
            % (branch, expected_branch),
            context={
                "path": top,
                "actual_branch": branch,
                "expected_branch": expected_branch,
                "main_root": main_root,
            },
            options=[
                "start this session inside the worktree for %r instead" % expected_branch,
                "if this checkout is intentional, re-run against the issue/PR this branch belongs to",
            ],
        )

    sha = _current_sha(top)
    if expected_remote_sha is None and check_remote_staleness:
        # Self-derived staleness: the remote tip of the AMBIENT branch (which pattern-acceptance
        # may have matched, so it can differ from `expected_branch`'s wording). ls-remote is a
        # remote query, never a fetch — see the docstring for why the ahead test needs no fetch.
        ls_result = _git(["ls-remote", "--heads", "origin", branch], top)
        if ls_result.returncode == 0 and ls_result.stdout.strip():
            expected_remote_sha = ls_result.stdout.strip().split("\t")[0].strip()
    if expected_remote_sha is not None and sha != expected_remote_sha:
        # "Ahead" = the remote SHA is an ancestor of HEAD (unpushed local work — normal for the
        # resolver). Anything else (behind, or diverged) means the checkout does not contain the
        # remote's state — a decision, never a silent proceed on stale code.
        # `is True` preserves the pre-refactor behaviour exactly: an unanswerable probe (exit 128 —
        # the remote sha is not in this object DB) counted as "not ahead" then and does now.
        ahead = contains_commit(top, expected_remote_sha) is True
        if require_exact_remote_sha or not ahead:
            return None, [], _workspace_mismatch(
                "stale_checkout",
                summary="session checkout HEAD %s does not match the expected remote state %s"
                % (sha[:7], expected_remote_sha[:7]),
                context={
                    "path": top,
                    "branch": branch,
                    "head_sha": sha,
                    "expected_remote_sha": expected_remote_sha,
                    "ahead_of_remote": ahead,
                    "main_root": main_root,
                },
                options=[
                    "pull/fast-forward this worktree to the remote state, then re-run",
                    "if the divergence is intentional, resolve it (push or rebase) and re-run",
                ],
            )

    setup_result = None
    if run_hooks:
        setup_result = _run_setup_hooks(top, top)
        if not setup_result["succeeded"]:
            return {
                "op": "attach",
                "kind": "work",
                "path": top,
                "branch": branch,
                "expected_branch": expected_branch,
                "main_root": main_root,
                "setup": setup_result,
            }, [], None

    unpushed_base = base_for_unpushed if base_for_unpushed is not None else default_branch(main_root)
    payload = {
        "op": "attach",
        "kind": "work",
        "path": top,
        "branch": branch,
        "expected_branch": expected_branch,
        "main_root": main_root,
        "sha": sha,
        "dirty": _is_dirty(top),
        "unpushed_commits": _unpushed_commits(top, branch, unpushed_base),
        # The pushed tip `unpushed_commits` counts from — `sha` itself whenever nothing is
        # unpushed. The resolver's diff links anchor here, so an interrupted run's unpushed
        # commits stay inside the range of the push that finally ships them.
        "upstream_sha": _git_stdout(
            ["rev-parse", _upstream_ref(top, branch, unpushed_base)], top
        ),
    }
    if setup_result is not None:
        payload["setup"] = setup_result
    return payload, [], None


def _cmd_attach(args):
    payload, notices, decision = _build_attach(
        args.path,
        args.expected_branch,
        base_for_unpushed=args.base,
        run_hooks=not args.no_hooks,
        allow_main_root=args.allow_root,
        expected_remote_sha=args.expected_remote_sha,
        accept_branch_pattern=args.accept_branch_pattern,
        check_remote_staleness=args.check_remote_staleness,
    )
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return EXIT_OK
    emit_ok(payload=payload, notices=notices)
    # A setup-hook failure is an ok envelope but a non-zero process exit (mirrors ensure --work).
    if not (payload.get("setup") or {"succeeded": True}).get("succeeded", True):
        return 1
    return EXIT_OK


# ---------------------------------------------------------------------------
# remove --work
# ---------------------------------------------------------------------------


def _build_remove_work(root, branch, invoker_cwd=None, merged_pr=None):
    """Pure ``remove --work`` core (S8 pattern lock): tear down and remove the work worktree for
    ``branch`` and return ``(payload, notices, decision)`` — **never emits**.

    Return contract (the type-level distinction the S8 retro asks for):
      - ``decision is not None`` → ``AMBIGUOUS`` when the worktree is dirty or has unpushed commits
        (architecture.md §6: "dirty or unpushed state is a decision, never a silent discard"), or
        ``WORKSPACE_MISMATCH`` (reason ``cwd_inside_target``) when ``invoker_cwd`` sits inside the
        very worktree being removed — a typed decision instead of git's own hard failure, since
        removing the checkout a session is standing in can never be what the operator meant;
        ``payload`` is ``None`` and the worktree is left in place.
      - ``decision is None`` → success, carrying the removal receipt (or the ``removed: False,
        reason: not_found`` no-op when nothing is there to remove).
      - a hard `git` failure still ``sys.exit(1)`` with faithful stderr and no envelope (§3 —
        unchanged).

    ``merged_pr`` (v3, the routine post-merge close): ``{"number": N, "head_oid": <sha>}`` for a
    branch whose PR has MERGED. Without it, squash-merge + delete-branch-on-merge makes every
    routine close trip the unpushed gate — ``_unpushed_commits`` falls back to ``origin/main``
    when ``origin/<branch>`` is gone, and a squashed branch's commits are never ancestors of
    main, so they all count "unpushed" with a nonsensical "push first" option. With it: a clean
    worktree whose HEAD **is exactly the merged PR's head OID** is removable (those commits ARE
    the merged content — GitHub holds them via the PR even after the branch delete); a merged
    branch with extra post-merge local commits gets a merged-specific ``AMBIGUOUS`` card instead
    of the generic push-first wording. ``dirty`` still always gates.
    """
    root = str(_resolve_main_root(root))

    target_path = _work_path(root, branch)

    if invoker_cwd is not None:
        resolved_cwd = Path(invoker_cwd).resolve()
        if resolved_cwd == target_path or target_path in resolved_cwd.parents:
            return None, [], _workspace_mismatch(
                REMOVE_MISMATCH_REASON,
                summary="refusing to remove the worktree the invoking session is inside",
                context={
                    "branch": branch,
                    "path": str(target_path),
                    "invoker_cwd": str(resolved_cwd),
                    "main_root": root,
                },
                options=["re-run from the project root (%s)" % root],
            )
    worktrees = _list_worktrees(root)
    entry = _find_worktree_at_path(worktrees, target_path)
    if entry is None:
        return {
            "op": "remove",
            "kind": "work",
            "branch": branch,
            "path": str(target_path),
            "removed": False,
            "reason": "not_found",
        }, [], None

    dirty = _is_dirty(str(target_path))
    # remove --work has no --base (the subcommand signature is `remove --work <branch>` only), so
    # it counts commits reachable from HEAD but from NO origin ref at all — a purely local
    # rev-list, no default branch and no network. That matters twice over: this is the one path
    # that must work offline (reclaiming an abandoned workspace), and deriving a default branch
    # here would hard-fail on a clone whose `refs/remotes/origin/HEAD` was never recorded, killing
    # the removal over a secondary detail. It is also STRICTER than the old "relative to the
    # default branch" reference, which over-counted a story branch forked off an unmerged epic
    # branch by including that epic's own already-pushed commits (a known, accepted edge case that
    # this form simply does not have).
    unpushed = _unpushed_commits_vs_all_remotes(str(target_path))

    head_sha = _current_sha(str(target_path))
    merged_at_head = (
        merged_pr is not None
        and not dirty
        and merged_pr.get("head_oid") is not None
        and head_sha == merged_pr["head_oid"]
    )

    if (dirty or unpushed > 0) and not merged_at_head:
        # architecture.md §6: "dirty or unpushed state is a decision, never a silent discard." The
        # closed decision-code set (architecture.md §3) has no bespoke code for this hazard; per
        # prd.md's own framing ("a dirty root" is grouped under "ambiguous state" as a mechanical
        # blocker), AMBIGUOUS is the closed-set code for a residual, listable-options blocker that
        # isn't one of the narrowly-named codes. Teardown is intentionally NOT run here: a
        # failing or resource-releasing teardown must not run against a workspace we are about to
        # leave in place specifically so the operator can recover its uncommitted/unpushed state.
        if merged_pr is not None and not dirty:
            # Merged PR, but the worktree has moved past its merged head — a merged-specific
            # card, never the generic push-first wording (pushing a squash-merged branch back is
            # nonsense).
            return None, [], needs_decision(
                AMBIGUOUS,
                summary=(
                    "PR #%s merged, but the worktree for branch %r has local commits past the "
                    "merged head — refusing to remove" % (merged_pr.get("number"), branch)
                ),
                context={
                    "branch": branch,
                    "path": str(target_path),
                    "dirty": dirty,
                    "unpushed_commits": unpushed,
                    "merged_pr": merged_pr,
                    "head_sha": head_sha,
                },
                options=[
                    "discard the post-merge local commits if they were scratch work, then re-run",
                    "salvage them onto a new branch first (they are NOT in the merged PR), then re-run",
                ],
            )
        hazard = "uncommitted changes" if dirty else "%d unpushed commit(s)" % unpushed
        return None, [], needs_decision(
            AMBIGUOUS,
            summary="work worktree for branch %r has %s — refusing to remove" % (branch, hazard),
            context={
                "branch": branch,
                "path": str(target_path),
                "dirty": dirty,
                "unpushed_commits": unpushed,
            },
            options=[
                "push the branch, then re-run remove --work",
                "commit or discard the uncommitted changes, then re-run remove --work",
                "remove the worktree manually if the state is intentionally being discarded",
            ],
        )

    teardown_result = _run_teardown_hooks(str(target_path), str(target_path))

    remove_result = _git(["worktree", "remove", str(target_path)], root)
    if remove_result.returncode != 0:
        sys.stderr.write(remove_result.stderr)
        sys.exit(1)

    return {
        "op": "remove",
        "kind": "work",
        "branch": branch,
        "path": str(target_path),
        "removed": True,
        "teardown": teardown_result,
    }, [], None


def _cmd_remove_work(args):
    payload, notices, decision = _build_remove_work(args.root, args.branch, invoker_cwd=Path.cwd())
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return EXIT_OK
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---------------------------------------------------------------------------
# sync --work — fast-forward a work worktree to its own branch's remote tip
# ---------------------------------------------------------------------------


def _build_sync_work(root, branch):
    """Pure ``sync --work`` core: bring the work worktree for ``branch`` to ``origin/<branch>`` by
    fast-forward only, re-run the setup hooks, and return ``(payload, notices, decision)`` —
    **never emits**.

    Why it exists: before each just-in-time story plan the epic worktree must contain the stories
    merged since it was opened, or the planner's content grounding refuses a checkout that is
    behind. The coordinator composes this as a named part of the operator's go-ahead for that plan
    — prd.md §8.5's one carve-out from "a stale checkout is never auto-corrected". It is the
    worktree's OWN branch, fast-forward only: a ref moves, no commit is made, so the §12 rule that a
    session never commits outside its own workspace still holds.

    Only a ``.worktrees/<branch>`` work worktree is a target — never the main checkout, which no
    path writes (architecture.md §6). Every refusal is a decision, taken before anything moves:
      - no work worktree for ``branch`` → ``AMBIGUOUS`` (nothing to sync; open it first);
      - uncommitted changes → ``AMBIGUOUS``;
      - local commits the remote lacks (ahead, or diverged) → ``AMBIGUOUS`` — syncing would either
        do nothing useful or need a merge/rebase, which is the operator's call.
    A HEAD already at the tip is an ok no-op (``fast_forwarded: False``). A setup-hook failure is an
    ok payload with ``setup.succeeded: False`` (the caller exits non-zero, as ``ensure`` does).
    """
    root = str(_resolve_main_root(root))
    target_path = _work_path(root, branch)
    entry = _find_worktree_at_path(_list_worktrees(root), target_path)
    if entry is None or entry["branch"] != branch:
        return None, [], needs_decision(
            AMBIGUOUS,
            summary="no work worktree for branch %r at %s — nothing to sync" % (branch, target_path),
            context={"branch": branch, "path": str(target_path), "main_root": root},
            options=["open the worktree first (workspace-open), then re-run"],
        )
    path = str(target_path)

    if _is_dirty(path):
        return None, [], needs_decision(
            AMBIGUOUS,
            summary="work worktree for branch %r has uncommitted changes — refusing to sync" % branch,
            context={"branch": branch, "path": path, "dirty": True},
            options=[
                "commit or discard the uncommitted changes, then re-run the sync",
                "skip the sync and leave the worktree as it is",
            ],
        )

    fetch = _git(["fetch", "origin", branch], path)
    if fetch.returncode != 0:
        sys.stderr.write(fetch.stderr)
        sys.exit(1)
    remote_ref = "origin/%s" % branch
    before = _current_sha(path)
    tip = _git_stdout(["rev-parse", remote_ref], path)

    if before != tip:
        if contains_commit(path, tip) is True:
            # The remote tip is already in HEAD's history, and HEAD isn't it: local-only commits.
            ahead = int(_git_stdout(["rev-list", "--count", "%s..HEAD" % remote_ref], path))
            return None, [], needs_decision(
                AMBIGUOUS,
                summary="work worktree for branch %r has %d commit(s) the remote lacks — refusing to sync"
                % (branch, ahead),
                context={"branch": branch, "path": path, "head_sha": before, "remote_sha": tip,
                         "unpushed_commits": ahead, "diverged": False},
                options=[
                    "push the branch, then re-run the sync",
                    "skip the sync and leave the worktree as it is",
                ],
            )
        if _git(["merge-base", "--is-ancestor", "HEAD", remote_ref], path).returncode != 0:
            return None, [], needs_decision(
                AMBIGUOUS,
                summary="work worktree for branch %r has diverged from %s — refusing to sync"
                % (branch, remote_ref),
                context={"branch": branch, "path": path, "head_sha": before, "remote_sha": tip,
                         "diverged": True},
                options=[
                    "reconcile the branch yourself (rebase or merge), then re-run the sync",
                    "skip the sync and leave the worktree as it is",
                ],
            )
        merge = _git(["merge", "--ff-only", remote_ref], path)
        if merge.returncode != 0:
            sys.stderr.write(merge.stderr)
            sys.exit(1)

    after = _current_sha(path)
    setup_result = _run_setup_hooks(path, path)
    return {
        "op": "sync",
        "kind": "work",
        "branch": branch,
        "path": path,
        "sync": {"before": before, "after": after, "fast_forwarded": before != after},
        "setup": setup_result,
    }, [], None


def _cmd_sync_work(args):
    payload, notices, decision = _build_sync_work(args.root, args.branch)
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return EXIT_OK
    emit_ok(payload=payload, notices=notices)
    if not payload["setup"]["succeeded"]:
        return 1
    return EXIT_OK


# ---------------------------------------------------------------------------
# gc — removes only aged ro-* worktrees; work worktrees are never touched regardless of age.
# ---------------------------------------------------------------------------


def _build_gc(root, max_age):
    """Pure ``gc`` core (S8 pattern lock): age out ``ro-*`` read workspaces older than ``max_age``
    days (work worktrees are never touched) and return ``(payload, notices, decision)`` — **never
    emits**. ``gc`` has no ``needs_decision`` path, so ``decision`` is always ``None`` here."""
    root = str(_resolve_main_root(root))
    max_age_seconds = max_age * 86400
    now = time.time()

    worktrees = _list_worktrees(root)
    removed = []
    skipped = []
    for entry in worktrees:
        basename = entry["path"].name
        if not basename.startswith(READ_WORKTREE_PREFIX):
            continue  # never touch a work worktree, regardless of age (architecture.md §6)
        try:
            age_seconds = now - entry["path"].stat().st_mtime
        except OSError:
            continue  # listed by git but missing on disk — nothing for gc to remove
        if age_seconds < max_age_seconds:
            skipped.append(str(entry["path"]))
            continue
        remove_result = _git(["worktree", "remove", "--force", str(entry["path"])], root)
        if remove_result.returncode == 0:
            removed.append(str(entry["path"]))
        else:
            skipped.append(str(entry["path"]))

    return {
        "op": "gc",
        "max_age_days": max_age,
        "removed": removed,
        "skipped": skipped,
    }, [], None


def _cmd_gc(args):
    payload, notices, _decision = _build_gc(args.root, args.max_age)
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---------------------------------------------------------------------------
# root-status
# ---------------------------------------------------------------------------


def _build_root_status(root):
    """Pure ``root-status`` core: report the main checkout's branch, HEAD, dirty state, and the
    repo's derived default branch. Purely informational since v3.x — it used to run the
    root-freshness protocol and could return ``ROOT_NOT_ON_MAIN`` / ``ROOT_DIRTY`` /
    ``ROOT_DIVERGED``, but no path gates on the operator's checkout any more, so there is nothing
    left to decide. ``decision`` is therefore always ``None``."""
    root = str(_resolve_main_root(root))
    return {
        "op": "root-status",
        "root": root,
        "branch": _current_branch(root),
        "default_branch": default_branch(root),
        "sha": _current_sha(root),
        "dirty": _is_dirty(root),
    }, [], None


def _cmd_root_status(args):
    payload, notices, decision = _build_root_status(args.root)
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return EXIT_OK
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---------------------------------------------------------------------------
# lint <setup|teardown>
# ---------------------------------------------------------------------------


def _build_lint(root, phase):
    """Pure ``lint`` core (S8 pattern lock): discover the ``<!-- worktree-<phase> -->`` block's
    commands (no execution, no worktree) and return ``(payload, notices, decision)`` — **never
    emits**. ``lint`` has no ``needs_decision`` path, so ``decision`` is always ``None`` here.

    Deliberately NOT normalized via :func:`_resolve_main_root`: ``lint``'s subject is the block
    text at exactly the path it was given — the setup skill lints the STAGED (edited) blocks
    inside its landing workspace (``lint setup --root <workspace>``), and hopping to the main
    checkout would silently lint the un-edited originals instead."""
    root = str(Path(root).resolve())
    phase_present, commands, _source = _discover_hook_commands(root, phase)
    return {
        "op": "lint",
        "phase": phase,
        "phase_present": phase_present,
        "command_count": len(commands),
        "would_run": commands,
    }, [], None


def _cmd_lint(args):
    payload, notices, _decision = _build_lint(args.root, args.phase)
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---------------------------------------------------------------------------
# tree-state
# ---------------------------------------------------------------------------

TREE_STATE_UNAVAILABLE = "TREE_STATE_UNAVAILABLE"


def build_tree_state(path, scratch):
    """Pure ``tree-state`` core (#244): the git tree id of the checkout at ``path`` as its working tree
    stands — tracked and untracked content, ``.gitignore`` honoured — plus its toplevel and HEAD. The
    resolver's §8 gate takes it before and after its static checks: a static check that rewrote the tree
    while the test-selection sub-agent read the diff changes the id, and the gate selects again. Comparing
    ``git status --porcelain`` would miss a check rewriting a file that was already modified.

    The tree is built in a temporary index (``read-tree HEAD``, ``add -A``, ``write-tree``) that
    ``GIT_INDEX_FILE`` names under ``scratch``, so the checkout's own index is never read or written.
    The index path is absolute: git resolves a relative ``GIT_INDEX_FILE`` against the worktree top it
    runs in, Python against this process's cwd, and the ``finally`` below must remove the file git wrote
    (and any ``.lock`` it left). Any failure — not a repository, an unborn HEAD, an unwritable scratch dir
    — is ``tree: null`` with the ``TREE_STATE_UNAVAILABLE`` notice, never a decision: the gate reads a
    null as a mismatch, so a failure costs one more dispatch, never an unguarded verdict.

    Returns ``(payload, notices, None)``; never emits."""
    payload = {"op": "tree-state", "path": None, "head": None, "tree": None}

    def unavailable(reason):
        return payload, ["%s: %s" % (TREE_STATE_UNAVAILABLE, reason)], None

    try:
        top = _git(["rev-parse", "--show-toplevel"], path)
    except OSError as exc:
        return unavailable("cannot run git in %s (%s)" % (path, exc))
    if top.returncode != 0:
        return unavailable("not a git checkout: %s" % (top.stderr.strip() or path))
    top_path = top.stdout.strip()
    payload["path"] = top_path
    head = _git(["rev-parse", "--verify", "HEAD"], top_path)
    if head.returncode != 0:
        return unavailable("HEAD does not resolve: %s" % head.stderr.strip())
    payload["head"] = head.stdout.strip()
    try:
        out = Path(scratch).resolve()
        out.mkdir(parents=True, exist_ok=True)
        index = out / ("tree-state-%d.index" % os.getpid())
        for stale in (index, Path(str(index) + ".lock")):
            if stale.exists():
                stale.unlink()
    except OSError as exc:
        return unavailable("scratch dir unusable: %s" % exc)
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    try:
        for argv in (["read-tree", "HEAD"], ["add", "-A"]):
            step = process.run(["git"] + argv, cwd=top_path, env=env)
            if step.returncode != 0:
                return unavailable("git %s failed: %s" % (" ".join(argv), step.stderr.strip()))
        written = process.run(["git", "write-tree"], cwd=top_path, env=env)
        if written.returncode != 0:
            return unavailable("git write-tree failed: %s" % written.stderr.strip())
    except OSError as exc:
        return unavailable("cannot run git in %s (%s)" % (top_path, exc))
    finally:
        for leftover in (index, Path(str(index) + ".lock")):
            try:
                leftover.unlink()
            except OSError:
                pass
    payload["tree"] = written.stdout.strip()
    return payload, [], None


def _cmd_tree_state(args):
    payload, notices, _decision = build_tree_state(args.path, args.scratch)
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="workspace.py",
        description="The workspace-lifecycle owner (architecture.md §6).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_ensure = sub.add_parser("ensure")
    p_ensure.add_argument("--root", default=".")
    group = p_ensure.add_mutually_exclusive_group(required=True)
    group.add_argument("--work", dest="branch", metavar="BRANCH")
    group.add_argument("--read", dest="ref", metavar="REF")
    p_ensure.add_argument("--base", default=None, help="required with --work")

    p_attach = sub.add_parser("attach")
    p_attach.add_argument("--expect-branch", dest="expected_branch", metavar="BRANCH", required=True)
    p_attach.add_argument("--path", default=".", help="the checkout to attach to (default: ambient cwd — attach's subject)")
    p_attach.add_argument(
        "--base",
        default=None,
        help="fallback base for the unpushed-commit count (default: the repo's default branch)",
    )
    p_attach.add_argument("--no-hooks", dest="no_hooks", action="store_true")
    p_attach.add_argument("--allow-root", dest="allow_root", action="store_true")
    p_attach.add_argument("--expect-remote-sha", dest="expected_remote_sha", default=None, metavar="SHA")
    p_attach.add_argument("--accept-branch-pattern", dest="accept_branch_pattern", default=None, metavar="REGEX")
    p_attach.add_argument("--check-remote-staleness", dest="check_remote_staleness", action="store_true")

    p_remove = sub.add_parser("remove")
    p_remove.add_argument("--root", default=".")
    p_remove.add_argument("--work", dest="branch", metavar="BRANCH", required=True)

    p_sync = sub.add_parser("sync")
    p_sync.add_argument("--root", default=".")
    p_sync.add_argument("--work", dest="branch", metavar="BRANCH", required=True)

    p_gc = sub.add_parser("gc")
    p_gc.add_argument("--root", default=".")
    p_gc.add_argument("--max-age", dest="max_age", type=int, default=DEFAULT_MAX_AGE_DAYS)

    p_root_status = sub.add_parser("root-status")
    p_root_status.add_argument("--root", default=".")

    p_lint = sub.add_parser("lint")
    p_lint.add_argument("phase", choices=["setup", "teardown"])
    p_lint.add_argument("--root", default=".")

    p_tree_state = sub.add_parser("tree-state")
    p_tree_state.add_argument("--path", default=".", help="the checkout to fingerprint (default: ambient cwd)")
    p_tree_state.add_argument("--scratch", required=True, help="where the temporary index is written")

    return parser, {"ensure": p_ensure, "remove": p_remove}


def main(argv):
    parser, subparsers_by_name = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "ensure":
        if args.branch is not None:
            if not args.base:
                subparsers_by_name["ensure"].error("--work requires --base <ref>")
            return _cmd_ensure_work(args)
        return _cmd_ensure_read(args)
    if args.subcommand == "attach":
        return _cmd_attach(args)
    if args.subcommand == "remove":
        return _cmd_remove_work(args)
    if args.subcommand == "sync":
        return _cmd_sync_work(args)
    if args.subcommand == "gc":
        return _cmd_gc(args)
    if args.subcommand == "root-status":
        return _cmd_root_status(args)
    if args.subcommand == "tree-state":
        return _cmd_tree_state(args)
    return _cmd_lint(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
