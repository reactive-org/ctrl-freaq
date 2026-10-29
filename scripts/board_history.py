"""board_history.py — the board's ``history`` fact: when each story reached each point (#156).

Import-only. ``prep_coordinator.py`` composes it; nothing else reads it. It owns the one batched
``gh api graphql`` read a coordinator prep makes for the board (:func:`build_query` + :func:`read`)
and the pure builders that turn the response into ordered points (:func:`points`,
:func:`integration_points`). The board mod places what these report and derives nothing from
GitHub itself.

A point is ``{point, at, source}``: ``point`` one of :data:`POINTS`, ``at`` an ISO-8601 UTC string
in GitHub's shape, ``source`` the field it came from (``reflog`` for the one local point below). A
line's points are sorted by ``at``, ties broken by :data:`POINTS` order. The set is closed (epic #136,
settled design item 3): the keys and point names are contract tokens the board mod parses. #157
added ``workspace-opened`` without renaming any of the others — the one point not read from GitHub:
the branch's local reflog creation entry (:func:`branch_created_at`), else its PR's earliest commit.

A closed story's line is treated as final, so the prep caches it across runs in its scratch dir
(:func:`load_cache` / :func:`save_cache`, #157): a run that serves every closed story from the cache
asks only for the open stories and their PRs. The scratch dir is a fixed ``/tmp`` path another local
account can pre-create, so the read-back trusts a cache only when it and its directory belong to the
current user, and only when every point's ``source`` is one of :data:`SOURCES` and its ``at`` is in
GitHub's shape (SEC-1 on PR #175): nothing but those closed tokens and a timestamp reaches the
envelope the coordinator model reads. A story reopened and closed again between two prep runs
is never seen reopened, so it keeps its first-close line (an accepted residual, 2026-10-28).

The query string is deterministic — fixed aliases, numbers in ascending order — because the
offline ``gh`` shim matches argv exactly: a reordered field makes every coordinator fixture miss,
which reads as ``BOARD_HISTORY_UNAVAILABLE``, not as a failure.
"""

import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402  (story_for_pr: a story PR's story, by closing set then head branch)
import plan_parts  # noqa: E402  (MAIN_MARKER: the plan comment a plan-posted point reads)
from pipelib import atomic, process, trust  # noqa: E402

# Every connection reads one page; a drawn line's ``totalCount`` past it is :func:`truncated`.
PAGE = 100

POINT_ELIGIBLE = "eligible"
POINT_PLAN_POSTED = "plan-posted"
POINT_WORKSPACE_OPENED = "workspace-opened"
POINT_PR_OPENED = "pr-opened"
POINT_COMMIT = "commit"
POINT_EVALUATE = "evaluate"
POINT_REWORK = "rework"
POINT_MERGED = "merged"
POINT_CLOSED = "closed"
# The closed point set, in tie-break order.
POINTS = (
    POINT_ELIGIBLE, POINT_PLAN_POSTED, POINT_WORKSPACE_OPENED, POINT_PR_OPENED, POINT_COMMIT,
    POINT_EVALUATE, POINT_REWORK, POINT_MERGED, POINT_CLOSED,
)
_ORDER = {name: index for index, name in enumerate(POINTS)}
# An issue node's alias, `i<N>` — never `integration`, which shares its first letter.
_ISSUE_ALIAS_RE = re.compile(r"i\d+")

# The closed `source` set: the field each point came from. Every `_point` call names one of these, so
# the cache's read-back can hold a line to the same set.
SOURCE_ISSUE_CREATED = "issue.createdAt"
SOURCE_START_GATE_MERGED = "start-gate.mergedAt"
SOURCE_PLAN_COMMENT_CREATED = "plan-comment.createdAt"
SOURCE_REFLOG = "reflog"
SOURCE_COMMIT_DATE = "commit.committedDate"
SOURCE_PR_CREATED = "pr.createdAt"
SOURCE_PR_MERGED = "pr.mergedAt"
SOURCE_ISSUE_CLOSED = "issue.closedAt"
# GitHub's DateTime shape, which `branch_created_at` normalises to: `_sorted` orders points by the
# `at` string, so a cached `at` must be in exactly this shape.
_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# The reflog subject `git branch` / `git worktree add -b` / `git checkout -b` write on creation.
_CREATED_SUBJECT = "branch: Created from"

# The history cache (#157): one file per prep scratch dir, versioned, scoped to one repo.
CACHE_VERSION = 1

READY_EVENT = "ReadyForReviewEvent"
DRAFT_EVENT = "ConvertToDraftEvent"
SOURCES = frozenset((
    SOURCE_ISSUE_CREATED, SOURCE_START_GATE_MERGED, SOURCE_PLAN_COMMENT_CREATED, SOURCE_REFLOG,
    SOURCE_COMMIT_DATE, SOURCE_PR_CREATED, READY_EVENT, DRAFT_EVENT, SOURCE_PR_MERGED,
    SOURCE_ISSUE_CLOSED,
))

_PR_FIELDS = (
    "number headRefName isDraft createdAt mergedAt "
    "closingIssuesReferences(first: 10) { nodes { number } } "
    "commits(first: %(page)d) { totalCount nodes { commit { committedDate } } } "
    "timelineItems(first: %(page)d, itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT]) "
    "{ totalCount nodes { __typename ... on ReadyForReviewEvent { createdAt } "
    "... on ConvertToDraftEvent { createdAt } } }"
) % {"page": PAGE}
# `authorAssociation` rides along so the plan-posted point reads trusted authors only (#179): without
# it every node would read untrusted, and with no check a stranger's earlier marker would move it.
_COMMENTS = "comments(first: %d) { totalCount nodes { createdAt body authorAssociation } }" % PAGE


def build_query(repo, issues, *, comment_issues=(), pr_numbers=(), story_base=None,
                integration_head=None, default_branch=None):
    """The one query a prep run makes. ``i<N>`` per issue (with its comments when ``N`` is in
    ``comment_issues``), ``p<N>`` per PR, ``stories`` — the PRs based on ``story_base`` — and
    ``integration`` — the PRs from ``integration_head`` into ``default_branch``."""
    owner, _, name = repo.partition("/")
    comment_issues = set(int(n) for n in comment_issues)
    parts = []
    for number in sorted(set(int(n) for n in issues)):
        fields = "createdAt closedAt"
        if number in comment_issues:
            fields += " " + _COMMENTS
        parts.append("i%d: issue(number: %d) { %s }" % (number, number, fields))
    for number in sorted(set(int(n) for n in pr_numbers)):
        parts.append("p%d: pullRequest(number: %d) { %s }" % (number, number, _PR_FIELDS))
    if story_base:
        parts.append("stories: pullRequests(baseRefName: %s, first: %d, states: [OPEN, MERGED]) "
                     "{ totalCount nodes { %s } }" % (json.dumps(story_base), PAGE, _PR_FIELDS))
    if integration_head and default_branch:
        parts.append("integration: pullRequests(headRefName: %s, baseRefName: %s, first: 5, "
                     "states: [OPEN, MERGED]) { nodes { %s } }"
                     % (json.dumps(integration_head), json.dumps(default_branch), _PR_FIELDS))
    return "query { repository(owner: %s, name: %s) { %s } }" % (
        json.dumps(owner), json.dumps(name), " ".join(parts))


def branch_created_at(root, branch):
    """When ``branch`` was created, from the local reflog of ``refs/heads/<branch>`` in ``root`` (any
    checkout: worktrees share one reflog): the oldest ``branch: Created from`` entry's date as
    ``YYYY-MM-DDTHH:MM:SSZ`` UTC — the shape GitHub reports, because :func:`_sorted` orders points by
    the ``at`` string. ``None`` with no such entry (the branch came from elsewhere, or its reflog
    expired) or a failed ``git`` call — never a raise."""
    if not branch:
        return None
    try:
        result = process.run(["git", "reflog", "show", "--date=iso-strict", "--format=%gd%x09%gs",
                              "refs/heads/" + branch], cwd=root)
    except (OSError, ValueError, TypeError):
        return None
    if result.returncode != 0:
        return None
    created = None
    for line in (result.stdout or "").splitlines():  # newest first: the last match is the oldest
        selector, _, subject = line.partition("\t")
        if subject.startswith(_CREATED_SUBJECT):
            created = selector
    if created is None:
        return None
    start, end = created.rfind("@{"), created.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        when = datetime.fromisoformat(created[start + 2:end].replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when.astimezone(timezone.utc).strftime(_AT_FORMAT)


def _valid_at(at):
    if not (isinstance(at, str) and _AT_RE.fullmatch(at)):
        return False
    try:
        datetime.strptime(at, _AT_FORMAT)
    except ValueError:
        return False
    return True


def _valid_line(line):
    return isinstance(line, list) and all(
        isinstance(p, dict) and set(p) == {"point", "at", "source"}
        and isinstance(p["point"], str) and p["point"] in _ORDER  # a str first: a list is unhashable
        and _valid_at(p["at"]) and isinstance(p["source"], str) and p["source"] in SOURCES
        for p in line)


def _owned(mode_check, st):
    # No `os.getuid` (not a POSIX host) proves no owner, so the cache is refused there.
    getuid = getattr(os, "getuid", None)
    return mode_check(st.st_mode) and getuid is not None and st.st_uid == getuid()


def _read_own(path):
    """``path``'s text, only when it is a regular file the current user owns in a directory the
    current user owns — neither a symlink. The file is opened without following a symlink and its
    owner checked on the open descriptor, so a swap between the check and the read reads nothing
    else; opened non-blocking, so a FIFO is refused rather than waited on. ``None`` otherwise; never
    a raise."""
    path = Path(path)
    try:
        if not _owned(stat.S_ISDIR, os.lstat(str(path.parent))):
            return None
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            if not _owned(stat.S_ISREG, os.fstat(fh.fileno())):
                return None
            return fh.read()
    except (OSError, ValueError):
        return None


def load_cache(path, repo):
    """The cached lines, ``{story number: points}``, from ``path``. A missing, unreadable or
    malformed file, another version, or another repo's file is ``{}`` — a cold start, never a
    notice and never a raise. So is a file or directory another user owns, a symlink, a point with
    a key beyond ``point`` / ``at`` / ``source``, a ``source`` outside :data:`SOURCES` and an ``at``
    not in GitHub's ``YYYY-MM-DDTHH:MM:SSZ`` shape (SEC-1): the scratch dir is a fixed ``/tmp`` path,
    and the lines reach the envelope the coordinator model reads."""
    text = _read_own(path)
    if text is None:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("v") != CACHE_VERSION or data.get("repo") != repo:
        return {}
    lines = data.get("lines")
    if not isinstance(lines, dict):
        return {}
    out = {}
    for key, line in lines.items():
        # ASCII digits only: `isdigit` alone passes a key such as "²" that `int` then raises on.
        if not (isinstance(key, str) and key.isascii() and key.isdigit() and _valid_line(line)):
            return {}
        out[int(key)] = line
    return out


def save_cache(path, repo, lines):
    """Write ``lines`` (``{story number: points}``) to ``path`` atomically, through
    :func:`pipelib.atomic.write_text` — a temp file in its directory, then a rename — so a concurrent
    prep reads the whole previous file or the whole new one. Raises ``OSError`` to the caller,
    removing the temp file."""
    text = json.dumps({"v": CACHE_VERSION, "repo": repo,
                       "lines": {str(n): lines[n] for n in sorted(lines)}}, sort_keys=True)
    atomic.write_text(path, text, ".history-cache-")


def argv(query):
    """The exact ``gh`` argv :func:`read` runs — what a shim fixture entry matches."""
    return ["gh", "api", "graphql", "-f", "query=" + query]


def read(repo, query, cwd=None):
    """``data.repository`` of one ``gh api graphql`` call, or ``None`` without one. GraphQL reports
    a number it cannot resolve as an error beside the data that did, and ``gh`` then exits
    non-zero, so a non-zero exit still reads whatever the response carries. ``repo`` is named in
    ``query``; it is taken for the caller's symmetry with the other readers."""
    del repo
    result = process.run(argv(query), cwd=cwd)
    try:
        repository = ((json.loads(result.stdout or "null") or {}).get("data") or {}).get("repository")
    except (ValueError, AttributeError):
        return None
    return repository if isinstance(repository, dict) else None


def _past_page(connection):
    count = connection.get("totalCount") if isinstance(connection, dict) else None
    return isinstance(count, int) and count > PAGE


def _pr_cut(pr):
    return isinstance(pr, dict) and (_past_page(pr.get("commits")) or _past_page(pr.get("timelineItems")))


def _comments_cut(node):
    if not isinstance(node, dict) or not _past_page(node.get("comments")):
        return False
    return plan_created_at((node.get("comments") or {}).get("nodes")) is None


def line_truncated(node, pr, stories=None):
    """Whether one story's line misses points past a page: its ``i<N>`` comments (with no plan
    marker in the page), its PR's ``commits`` or ``timelineItems``, or — for a story the read found no
    PR for — the ``stories`` connection (the cold read's; ``None`` on the cached path), where its PR
    may be the node past the page. What the history cache keeps out (#157)."""
    return _comments_cut(node) or _pr_cut(pr) or (pr is None and _past_page(stories))


def truncated(repository, drawn_prs, integration=None):
    """Whether a drawn line is missing points past a page — never a connection the board does not
    draw. True when the ``stories`` connection runs past its page (a missing node may be a story's
    PR); when a PR in ``drawn_prs`` (the PRs the lines are drawn from) does, in its ``commits`` or
    its ``timelineItems``; when the ``integration`` PR's ``timelineItems`` does (its commits are
    never drawn); or when an ``i<N>`` node's ``comments`` does and its page carries no plan marker
    (comments come oldest first, so a marker in the page is the earliest). A superseded or
    unmapped story-base PR never counts."""
    repository = repository or {}
    if _past_page(repository.get("stories")):
        return True
    if any(_pr_cut(pr) for pr in drawn_prs or ()):
        return True
    if isinstance(integration, dict) and _past_page(integration.get("timelineItems")):
        return True
    for alias, node in repository.items():
        if _ISSUE_ALIAS_RE.fullmatch(alias) and _comments_cut(node):
            return True
    return False


def _nodes(connection):
    return [node for node in (connection or {}).get("nodes") or [] if isinstance(node, dict)]


def _pick(prs):
    """The merged PR first (the latest merge, should there be two), else the open one opened last."""
    merged = [pr for pr in prs if pr.get("mergedAt")]
    if merged:
        return max(merged, key=lambda pr: pr["mergedAt"])
    if prs:
        return max(prs, key=lambda pr: pr.get("createdAt") or "")
    return None


def story_prs(prs, numbers):
    """Each story's PR among the ``stories`` connection's PRs: every PR mapped **once** against the
    whole story set (:func:`branching.story_for_pr` — the closing set first, then the head branch),
    so it lands on one story at most, then each story's group picked by :func:`_pick`. Every number
    in ``numbers`` is a key, ``None`` for a story with no PR. A ``null`` node (a PR GraphQL could not
    read, beside the ones it could) is skipped."""
    numbers = [int(n) for n in numbers]
    story_set = set(numbers)
    groups = {number: [] for number in numbers}
    for pr in prs or []:
        if not isinstance(pr, dict):
            continue
        flat = dict(pr, closingIssuesReferences=_nodes(pr.get("closingIssuesReferences")))
        story = branching.story_for_pr(flat, story_set)
        if story in groups:
            groups[story].append(pr)
    return {number: _pick(group) for number, group in groups.items()}


def integration_pr(nodes):
    """The epic's integration PR among the ``integration`` connection's PRs, by :func:`_pick`."""
    return _pick([node for node in nodes or [] if isinstance(node, dict)])


def plan_created_at(comments):
    """When the plan was posted: the earliest trusted-author comment (``pipelib.trust``, #179) whose
    body starts with the plan's main marker (a part comment carries its own marker, so it never
    matches)."""
    times = [c.get("createdAt") for c in comments or []
             if trust.is_trusted_comment(c) and (c.get("body") or "").startswith(plan_parts.MAIN_MARKER)
             and c.get("createdAt")]
    return min(times) if times else None


def _point(name, at, source):
    return {"point": name, "at": at, "source": source}


def _sorted(points_):
    return sorted(points_, key=lambda p: (p["at"], _ORDER[p["point"]]))


def _pr_points(pr, commits):
    """A PR's own points: opened, its commits (when ``commits``), every ready / draft flip, merged.
    A PR opened ready — its earliest flip is to draft, or it has none and is not a draft — gets an
    ``evaluate`` at its creation, since no ``ReadyForReviewEvent`` will say so."""
    out = []
    if pr.get("createdAt"):
        out.append(_point(POINT_PR_OPENED, pr["createdAt"], SOURCE_PR_CREATED))
    if commits:
        for node in _nodes(pr.get("commits")):
            at = (node.get("commit") or {}).get("committedDate")
            if at:
                out.append(_point(POINT_COMMIT, at, SOURCE_COMMIT_DATE))
    events = sorted((node for node in _nodes(pr.get("timelineItems"))
                     if node.get("__typename") in (READY_EVENT, DRAFT_EVENT) and node.get("createdAt")),
                    key=lambda node: node["createdAt"])
    opened_ready = (events[0]["__typename"] == DRAFT_EVENT) if events else not pr.get("isDraft")
    if opened_ready and pr.get("createdAt"):
        out.append(_point(POINT_EVALUATE, pr["createdAt"], SOURCE_PR_CREATED))
    for node in events:
        if node["__typename"] == READY_EVENT:
            out.append(_point(POINT_EVALUATE, node["createdAt"], READY_EVENT))
        else:
            out.append(_point(POINT_REWORK, node["createdAt"], DRAFT_EVENT))
    if pr.get("mergedAt"):
        out.append(_point(POINT_MERGED, pr["mergedAt"], SOURCE_PR_MERGED))
    return out


def _first_commit_at(pr):
    times = [(node.get("commit") or {}).get("committedDate") for node in _nodes((pr or {}).get("commits"))]
    times = [t for t in times if t]
    return min(times) if times else None


def points(issue, pr, plan_at, waited_merged_ats=(), workspace_opened=None):
    """One story's (or one issue's) line. ``issue`` is its ``i<N>`` node, ``pr`` its PR node or
    ``None``, ``plan_at`` the plan comment's creation time or ``None``, and ``waited_merged_ats`` the
    ``mergedAt`` of each story its ``start`` edges wait on (``None`` for one not merged yet, which
    moves nothing): ``eligible`` is the latest of those after the issue's own creation.
    ``workspace_opened`` is the branch's reflog creation time (:func:`branch_created_at`); without
    it, or when it is later than the PR's earliest commit (a local branch created after the work
    began elsewhere — a fresh checkout of a pushed branch — dates this checkout, not the workspace
    the work started in), ``workspace-opened`` falls back to that earliest commit, and without either
    there is no such point."""
    issue = issue or {}
    out = []
    eligible = issue.get("createdAt")
    source = SOURCE_ISSUE_CREATED
    for merged_at in waited_merged_ats or ():
        if merged_at and (eligible is None or merged_at > eligible):
            eligible, source = merged_at, SOURCE_START_GATE_MERGED
    if eligible:
        out.append(_point(POINT_ELIGIBLE, eligible, source))
    if plan_at:
        out.append(_point(POINT_PLAN_POSTED, plan_at, SOURCE_PLAN_COMMENT_CREATED))
    first_commit = _first_commit_at(pr) if pr else None
    if workspace_opened and not (first_commit and workspace_opened > first_commit):
        out.append(_point(POINT_WORKSPACE_OPENED, workspace_opened, SOURCE_REFLOG))
    elif first_commit:
        out.append(_point(POINT_WORKSPACE_OPENED, first_commit, SOURCE_COMMIT_DATE))
    if pr:
        out.extend(_pr_points(pr, commits=True))
    if issue.get("closedAt"):
        out.append(_point(POINT_CLOSED, issue["closedAt"], SOURCE_ISSUE_CLOSED))
    return _sorted(out)


def integration_points(pr):
    """The integration PR's line: its own points with no commits — those are the stories' own,
    already drawn on their lines. ``[]`` with no PR."""
    if not pr:
        return []
    return _sorted(_pr_points(pr, commits=False))
