"""board_history.py — the board's ``history`` fact: when each story reached each point (#156).

Import-only. ``prep_coordinator.py`` composes it; nothing else reads it. It owns the one batched
``gh api graphql`` read a coordinator prep makes for the board (:func:`build_query` + :func:`read`)
and the pure builders that turn the response into ordered points (:func:`points`,
:func:`integration_points`). The board mod places what these report and derives nothing from
GitHub itself.

A point is ``{point, at, source}``: ``point`` one of :data:`POINTS`, ``at`` an ISO-8601 UTC string
as GitHub reports it, ``source`` the field it came from. A line's points are sorted by ``at``, ties
broken by :data:`POINTS` order. The set is closed (epic #136, settled design item 3, less the
workspace-opened point, which #157 adds without renaming any of these): the keys and point names
are contract tokens the board mod parses.

The query string is deterministic — fixed aliases, numbers in ascending order — because the
offline ``gh`` shim matches argv exactly: a reordered field makes every coordinator fixture miss,
which reads as ``BOARD_HISTORY_UNAVAILABLE``, not as a failure.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402  (story_for_pr: a story PR's story, by closing set then head branch)
import plan_parts  # noqa: E402  (MAIN_MARKER: the plan comment a plan-posted point reads)
from pipelib import process  # noqa: E402

# Every connection reads one page; a ``totalCount`` past it is :func:`truncated`.
PAGE = 100

POINT_ELIGIBLE = "eligible"
POINT_PLAN_POSTED = "plan-posted"
POINT_PR_OPENED = "pr-opened"
POINT_COMMIT = "commit"
POINT_EVALUATE = "evaluate"
POINT_REWORK = "rework"
POINT_MERGED = "merged"
POINT_CLOSED = "closed"
# The closed point set, in tie-break order.
POINTS = (
    POINT_ELIGIBLE, POINT_PLAN_POSTED, POINT_PR_OPENED, POINT_COMMIT, POINT_EVALUATE,
    POINT_REWORK, POINT_MERGED, POINT_CLOSED,
)
_ORDER = {name: index for index, name in enumerate(POINTS)}

READY_EVENT = "ReadyForReviewEvent"
DRAFT_EVENT = "ConvertToDraftEvent"

_PR_FIELDS = (
    "number headRefName isDraft createdAt mergedAt "
    "closingIssuesReferences(first: 10) { nodes { number } } "
    "commits(first: %(page)d) { totalCount nodes { commit { committedDate } } } "
    "timelineItems(first: %(page)d, itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT]) "
    "{ totalCount nodes { __typename ... on ReadyForReviewEvent { createdAt } "
    "... on ConvertToDraftEvent { createdAt } } }"
) % {"page": PAGE}
_COMMENTS = "comments(first: %d) { totalCount nodes { createdAt body } }" % PAGE


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


def truncated(repository):
    """Whether any connection in the response reports a ``totalCount`` past its page."""
    def walk(node):
        if isinstance(node, dict):
            count = node.get("totalCount")
            if isinstance(count, int) and count > PAGE:
                return True
            return any(walk(value) for value in node.values())
        if isinstance(node, list):
            return any(walk(value) for value in node)
        return False
    return walk(repository or {})


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


def story_pr(prs, number):
    """Among the ``stories`` connection's PRs, the one that delivers story ``number``
    (:func:`branching.story_for_pr`), by :func:`_pick`; ``None`` when none does."""
    mine = []
    for pr in prs or []:
        flat = dict(pr, closingIssuesReferences=_nodes(pr.get("closingIssuesReferences")))
        if branching.story_for_pr(flat, {int(number)}) == int(number):
            mine.append(pr)
    return _pick(mine)


def integration_pr(nodes):
    """The epic's integration PR among the ``integration`` connection's PRs, by :func:`_pick`."""
    return _pick([node for node in nodes or [] if isinstance(node, dict)])


def plan_created_at(comments):
    """When the plan was posted: the earliest comment whose body starts with the plan's main
    marker (a part comment carries its own marker, so it never matches)."""
    times = [c.get("createdAt") for c in comments or []
             if isinstance(c, dict) and (c.get("body") or "").startswith(plan_parts.MAIN_MARKER)
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
        out.append(_point(POINT_PR_OPENED, pr["createdAt"], "pr.createdAt"))
    if commits:
        for node in _nodes(pr.get("commits")):
            at = (node.get("commit") or {}).get("committedDate")
            if at:
                out.append(_point(POINT_COMMIT, at, "commit.committedDate"))
    events = sorted((node for node in _nodes(pr.get("timelineItems"))
                     if node.get("__typename") in (READY_EVENT, DRAFT_EVENT) and node.get("createdAt")),
                    key=lambda node: node["createdAt"])
    opened_ready = (events[0]["__typename"] == DRAFT_EVENT) if events else not pr.get("isDraft")
    if opened_ready and pr.get("createdAt"):
        out.append(_point(POINT_EVALUATE, pr["createdAt"], "pr.createdAt"))
    for node in events:
        if node["__typename"] == READY_EVENT:
            out.append(_point(POINT_EVALUATE, node["createdAt"], READY_EVENT))
        else:
            out.append(_point(POINT_REWORK, node["createdAt"], DRAFT_EVENT))
    if pr.get("mergedAt"):
        out.append(_point(POINT_MERGED, pr["mergedAt"], "pr.mergedAt"))
    return out


def points(issue, pr, plan_at, waited_merged_ats=()):
    """One story's (or one issue's) line. ``issue`` is its ``i<N>`` node, ``pr`` its PR node or
    ``None``, ``plan_at`` the plan comment's creation time or ``None``, and ``waited_merged_ats`` the
    ``mergedAt`` of each story its ``start`` edges wait on (``None`` for one not merged yet, which
    moves nothing): ``eligible`` is the latest of those after the issue's own creation."""
    issue = issue or {}
    out = []
    eligible = issue.get("createdAt")
    source = "issue.createdAt"
    for merged_at in waited_merged_ats or ():
        if merged_at and (eligible is None or merged_at > eligible):
            eligible, source = merged_at, "start-gate.mergedAt"
    if eligible:
        out.append(_point(POINT_ELIGIBLE, eligible, source))
    if plan_at:
        out.append(_point(POINT_PLAN_POSTED, plan_at, "plan-comment.createdAt"))
    if pr:
        out.extend(_pr_points(pr, commits=True))
    if issue.get("closedAt"):
        out.append(_point(POINT_CLOSED, issue["closedAt"], "issue.closedAt"))
    return _sorted(out)


def integration_points(pr):
    """The integration PR's line: its own points with no commits — those are the stories' own,
    already drawn on their lines. ``[]`` with no PR."""
    if not pr:
        return []
    return _sorted(_pr_points(pr, commits=False))
