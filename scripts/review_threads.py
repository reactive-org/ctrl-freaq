#!/usr/bin/env python3
"""review_threads.py — the PR review threads the evaluator gates approval on (#145), read once for
both preps that need them.

`skills/_shared/review-threads.md` owns every shape read and written here: the
`<!-- review-thread:v1 -->` marker, the stamp line a script writes under it, the `F<n>` ids, the five
thread states and their precedence, and where a comment's or a resolution's trust comes from.

**Facts by script, meaning by model** (CLAUDE.md). Which state a thread is in, whose comment or
resolution counts, and whether the code a thread is anchored on has changed since are all derived
here, from GitHub's thread data and a local `git diff`. What a reply asks for stays the model's
judgment.

**Trust.** The pipeline posts as the operator's own GitHub account, so the marker, not the author,
says which comments are the pipeline's. A marked comment counts as the pipeline's only when its author
is trusted (`pipelib.trust.is_trusted`); a marked comment by anyone else is untrusted text, ignored
like any other. A comment's trust comes from its author's association. A resolution's comes from the
resolving account's repo permission (`pipelib.trust.is_trusted_permission`), because GitHub carries
no association for it, and the viewer's own resolution needs no lookup: the pipeline never resolves
or unresolves a thread, so this account's resolution is a human's. An unknown association or
permission is untrusted, as SEC-1 rules.

**Hunk unchanged** is a script `git diff -U0`, never GitHub's `isOutdated`, whose meaning is
undocumented and which would mark a thread the resolver fixed and a human then resolved as superseded.
Every case the diff cannot answer (a reference commit missing locally, a deleted or renamed path)
counts as changed: a resolved thread re-threads, it never wrongly counts as accepted.

Import-only in phase 1 of #145: `read_threads` is composed in-process by `prep_evaluator` and
`prep_resolver` as `facts.review_threads`. Its failures are notices (`REVIEW_THREADS_UNAVAILABLE`,
`REVIEW_THREADS_TRUNCATED`, `THREAD_PERMISSION_UNAVAILABLE`), never a decision code. `main` is a usage
error until the `flags` subcommand lands. Every `gh` and `git` call goes through `pipelib.process.run`.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib import process, trust  # noqa: E402

MARKER = "<!-- review-thread:v1 -->"

# The closed state set, in the contract's table order. `operator-thread` outranks `unreviewed` and
# `changes-requested`; the derivation's precedence is `derive_state`'s.
THREAD_STATES = ("unreviewed", "accepted", "changes-requested", "operator-thread", "superseded")

# At most this many new threads per review (the cap `flags` applies in phase 3). The review loop's
# ceiling: a review an operator can clear in one sitting.
THREAD_CAP = 8

REVIEW_THREADS_UNAVAILABLE = "REVIEW_THREADS_UNAVAILABLE"
REVIEW_THREADS_TRUNCATED = "REVIEW_THREADS_TRUNCATED"
THREAD_PERMISSION_UNAVAILABLE = "THREAD_PERMISSION_UNAVAILABLE"

# Connection sizes the one GraphQL document reads. Threads paginate; a thread's comments, the PR's
# comments and its reviews are read as their latest N, and more than N is a truncation notice.
THREADS_PER_PAGE = 50
THREAD_RECENT_COMMENTS = 50
PR_COMMENTS = 100
PR_REVIEWS = 100
# A backstop on cursor pages (2,500 threads); reaching it is a failed read, never looped on.
MAX_THREAD_PAGES = 50

# `state` is PENDING on a comment of this viewer's own unsubmitted review: GitHub shows it to its author
# only, so it is skipped — a stranded pipeline review would otherwise read as live, gating threads.
_COMMENT_FIELDS = "author { login } authorAssociation body createdAt url state originalCommit { oid }"
_ISSUE_COMMENT_FIELDS = "author { login } authorAssociation body createdAt url"
_REVIEW_FIELDS = "author { login } authorAssociation body createdAt url state"
_THREAD_FIELDS = (
    "id isResolved resolvedBy { login } path subjectType line startLine originalLine originalStartLine "
    "root: comments(first: 1) { nodes { " + _COMMENT_FIELDS + " } } "
    "recent: comments(last: " + str(THREAD_RECENT_COMMENTS) + ") { totalCount nodes { " + _COMMENT_FIELDS + " } }"
)

_HEX40 = r"[0-9a-f]{40}"
_ROOT_STAMP_RE = re.compile(
    r"^<!-- F(?P<f>[1-9][0-9]*) · head (?P<head>" + _HEX40 + r") · anchor (?P<anchor>.+?)"
    r" · flags (?P<flags>.*?)(?: · supersedes (?P<supersedes>F[1-9][0-9]*|https://\S+))? -->$"
)
_REPLY_STAMP_RE = re.compile(r"^<!-- reply(?: F(?P<f>[1-9][0-9]*))? · head (?P<head>" + _HEX40 + r") -->$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ---------------------------------------------------------------------------
# Stamps
# ---------------------------------------------------------------------------


def _check_field(value, name):
    text = str(value)
    if not text or "-->" in text or "\n" in text or " · " in text:
        raise ValueError("%s %r cannot be stamped (empty, or carries `-->`, ` · ` or a newline)" % (name, value))
    return text


def _encode_flag(key):
    """A flag key as the stamp writes it: `%` and `,` percent-encoded, since `,` separates keys and a key
    embeds a path, which may carry either. Spaces stay as they are."""
    return key.replace("%", "%25").replace(",", "%2C")


def _decode_flag(key):
    return key.replace("%2C", ",").replace("%25", "%")


def stamp_root(f, head, path, subject, start, end, flags, body, supersedes=None):
    """The full text of a pipeline thread's root comment: the marker, the stamp line, the visible
    `**F<n>**` heading, then the model-written ``body``. ``subject`` is ``"line"`` (``start``..``end``)
    or ``"file"``. Raises ``ValueError`` on a value the stamp grammar cannot carry, so a bad spec
    fails before anything is posted."""
    if not isinstance(f, int) or f < 1:
        raise ValueError("F id must be a positive int, got %r" % (f,))
    if not re.fullmatch(_HEX40, head or ""):
        raise ValueError("head must be a 40-hex commit sha, got %r" % (head,))
    path = _check_field(path, "path")
    if subject == "file":
        anchor = "%s:file" % path
    elif subject == "line":
        if not (isinstance(start, int) and isinstance(end, int) and 1 <= start <= end):
            raise ValueError("a line anchor needs 1 <= start <= end, got %r-%r" % (start, end))
        anchor = "%s:%d-%d" % (path, start, end)
    else:
        raise ValueError("subject must be 'line' or 'file', got %r" % (subject,))
    keys = [_encode_flag(_check_field(k, "flag")) for k in flags or []]
    stamp = "<!-- F%d · head %s · anchor %s · flags %s" % (f, head, anchor, ",".join(keys))
    if supersedes is not None:
        if not re.fullmatch(r"F[1-9][0-9]*|https://\S+", supersedes) or "-->" in supersedes:
            raise ValueError("supersedes must be F<m> or a comment URL, got %r" % (supersedes,))
        stamp += " · supersedes %s" % supersedes
    stamp += " -->"
    return "%s\n%s\n**F%d**\n\n%s" % (MARKER, stamp, f, body)


def stamp_reply(head, body, f=None):
    """The full text of a pipeline reply in a thread: the marker, the reply stamp (`reply F<n>`, or a
    bare `reply` when the thread has no `F` id), then ``body``."""
    if not re.fullmatch(_HEX40, head or ""):
        raise ValueError("head must be a 40-hex commit sha, got %r" % (head,))
    if f is not None and (not isinstance(f, int) or f < 1):
        raise ValueError("F id must be a positive int, got %r" % (f,))
    stamp = "<!-- reply%s · head %s -->" % (" F%d" % f if f is not None else "", head)
    return "%s\n%s\n\n%s" % (MARKER, stamp, body)


def is_marked(body):
    """Whether ``body`` opens with the marker line (leading whitespace ignored)."""
    return isinstance(body, str) and body.lstrip().startswith(MARKER)


def parse_stamp(body):
    """The stamp of a marked comment: ``{"kind": "root", f, head, path, subject, range, flags,
    supersedes}`` or ``{"kind": "reply", f, head}``; ``None`` for an unmarked body or a second line
    the grammar does not read. Never raises."""
    if not is_marked(body):
        return None
    lines = body.lstrip().split("\n")
    if len(lines) < 2:
        return None
    line = lines[1].strip()
    match = _ROOT_STAMP_RE.match(line)
    if match:
        anchor = match.group("anchor")
        path, _, where = anchor.rpartition(":")
        if not path:
            return None
        if where == "file":
            subject, rng = "file", None
        else:
            span = re.fullmatch(r"(\d+)-(\d+)", where)
            if not span or not 1 <= int(span.group(1)) <= int(span.group(2)):
                return None
            subject, rng = "line", [int(span.group(1)), int(span.group(2))]
        flags = [_decode_flag(k) for k in match.group("flags").split(",") if k]
        return {
            "kind": "root",
            "f": int(match.group("f")),
            "head": match.group("head"),
            "path": path,
            "subject": subject,
            "range": rng,
            "flags": flags,
            "supersedes": match.group("supersedes"),
        }
    match = _REPLY_STAMP_RE.match(line)
    if match:
        return {"kind": "reply", "f": int(match.group("f")) if match.group("f") else None, "head": match.group("head")}
    return None


def reason_line(text):
    """A reply normalised to one line: whitespace collapsed to one space and ` — ` written ` - `, so it
    can sit in a `## Security review` note, whose fields ` — ` separates. ``None`` stays ``None``."""
    if text is None:
        return None
    return " ".join(str(text).split()).replace(" — ", " - ")


# ---------------------------------------------------------------------------
# Hunks
# ---------------------------------------------------------------------------


def parse_hunks(diff_text):
    """The `@@` headers of a `git diff -U0` as ``[(old_start, old_count, new_start, new_count)]``.
    A count `git` omits is 1."""
    hunks = []
    for line in (diff_text or "").splitlines():
        match = _HUNK_RE.match(line)
        if match:
            os_, oc, ns, nc = match.groups()
            hunks.append((int(os_), 1 if oc is None else int(oc), int(ns), 1 if nc is None else int(nc)))
    return hunks


def _old_side_intersects(hunk, start, end):
    """Whether a hunk's old side touches lines ``start``..``end``. A pure insertion (old count 0) sits
    after its old start line, so it touches the range when it lands between two of its lines."""
    old_start, old_count = hunk[0], hunk[1]
    if old_count == 0:
        return start <= old_start < end
    return old_start <= end and old_start + old_count - 1 >= start


def map_range(hunks, rng):
    """Map line range ``rng`` (``[start, end]``) across a diff's ``hunks`` (`parse_hunks`): a line
    outside every hunk shifts by the running delta of the hunks above it, and a hunk whose old side
    intersects the range widens it to that hunk's new side. ``None`` (a file anchor) stays ``None``.
    Pure."""
    if rng is None:
        return None
    start, end = rng
    hunks = sorted(hunks)

    def shift(line):
        delta = 0
        for old_start, old_count, new_start, new_count in hunks:
            above = old_start < line if old_count == 0 else old_start + old_count - 1 < line
            if above:
                delta += new_count - old_count
        return line + delta

    new_start, new_end = shift(start), shift(end)
    for hunk in hunks:
        if _old_side_intersects(hunk, start, end):
            _, _, h_new_start, h_new_count = hunk
            h_new_end = h_new_start + h_new_count - 1 if h_new_count else h_new_start
            new_start = min(new_start, max(h_new_start, 1))
            new_end = max(new_end, h_new_end)
    new_start = max(new_start, 1)
    return [new_start, max(new_start, new_end)]


def _commit_exists(root, sha):
    if not sha:
        return False
    result = process.run(["git", "cat-file", "-e", "%s^{commit}" % sha], cwd=root)
    return result.returncode == 0


def _diff_hunks(root, base, head, path):
    """The hunks of `git diff -U0 base head -- path`, or ``None`` when the diff cannot be read in
    lines: an unreadable diff, and a diff with output but no `@@` hunk — a binary file, a mode-only
    change — which a line range cannot be checked against."""
    result = process.run(
        ["git", "diff", "--no-color", "--no-ext-diff", "-U0", base, head, "--", path], cwd=root
    )
    if result.returncode != 0:
        return None
    hunks = parse_hunks(result.stdout)
    if not hunks and (result.stdout or "").strip():
        return None
    return hunks


def map_range_between(root, base, head, path, rng):
    """``rng`` on ``path`` at commit ``base`` mapped to commit ``head``; ``rng`` itself when the two
    are one commit; ``None`` when it cannot be mapped (a commit missing locally, an unreadable diff)."""
    if base == head:
        return list(rng) if rng is not None else None
    if not (_commit_exists(root, base) and _commit_exists(root, head)):
        return None
    hunks = _diff_hunks(root, base, head, path)
    if hunks is None:
        return None
    return map_range(hunks, rng)


def hunk_changed(root, reference, head, path, rng, subject="line"):
    """Whether the code a thread is anchored on changed between ``reference`` and ``head``: any hunk
    of `git diff -U0 <reference> <head> -- <path>` whose old side intersects ``rng``; any hunk at all
    for a ``file`` subject. A reference commit missing locally (``git cat-file -e`` fails — after a
    force-push, say), an unreadable diff, a binary or mode-only change (output with no `@@` hunk) and a
    line thread with no range all count as changed, and so does a deleted or renamed path, whose diff
    under the old path deletes every line."""
    if not reference or not _commit_exists(root, reference) or not _commit_exists(root, head):
        return True
    if reference == head:
        return False
    hunks = _diff_hunks(root, reference, head, path)
    if hunks is None:
        return True
    if subject == "file":
        return bool(hunks)
    if rng is None:
        return True
    return any(_old_side_intersects(h, rng[0], rng[1]) for h in hunks)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def resolution_trusted(is_resolved, resolved_by, viewer, permissions):
    """Whether a thread's resolution counts. ``permissions`` maps a login to its repo permission
    (``None`` when the lookup failed). The viewer's own resolution is trusted without a lookup."""
    if not is_resolved or not resolved_by:
        return False
    if viewer and resolved_by == viewer:
        return True
    return trust.is_trusted_permission((permissions or {}).get(resolved_by))


def derive_state(comments, root_origin, resolved_trusted, hunk_changed_fn):
    """One thread's state over :data:`THREAD_STATES`. Pure.

    ``comments`` are the thread's comments in order, each ``{"marked": bool, "trusted": bool}``;
    ``root_origin`` is ``"pipeline"`` (a trusted marked root) or ``"operator"`` (a trusted unmarked
    root); ``resolved_trusted`` is :func:`resolution_trusted`'s answer — an untrusted resolution reads
    as unresolved; ``hunk_changed_fn`` is called only for a trusted resolution. Untrusted comments are
    ignored, marked or not. Returns ``(state, pending_reply)``.

    Precedence: a trusted resolution is ``accepted`` with the hunk unchanged, else ``superseded``;
    unresolved, an operator-rooted thread is ``operator-thread`` (``pending_reply`` while the latest
    trusted comment is a marked one); unresolved, a trusted human comment after the latest trusted
    marked comment is ``changes-requested``; anything else is ``unreviewed``.
    """
    if resolved_trusted:
        return ("superseded" if hunk_changed_fn() else "accepted"), False
    counted = [c for c in comments if c.get("trusted")]
    if root_origin == "operator":
        return "operator-thread", bool(counted) and bool(counted[-1].get("marked"))
    last_marked = max((i for i, c in enumerate(counted) if c.get("marked")), default=-1)
    if any(not c.get("marked") for c in counted[last_marked + 1:]):
        return "changes-requested", False
    return "unreviewed", False


# ---------------------------------------------------------------------------
# Reading GitHub
# ---------------------------------------------------------------------------


def _query(repo, pr, after=None):
    owner, _, name = repo.partition("/")
    page = "first: %d" % THREADS_PER_PAGE
    if after is not None:
        page += ", after: %s" % json.dumps(after)
    return (
        "query { viewer { login } repository(owner: %s, name: %s) { pullRequest(number: %d) { "
        "reviewThreads(%s) { pageInfo { hasNextPage endCursor } nodes { %s } } "
        "comments(last: %d) { totalCount nodes { %s } } "
        "reviews(last: %d) { totalCount nodes { %s } } } } }"
        % (json.dumps(owner), json.dumps(name), int(pr), page, _THREAD_FIELDS, PR_COMMENTS,
           _ISSUE_COMMENT_FIELDS, PR_REVIEWS, _REVIEW_FIELDS)
    )


def query_argv(repo, pr, after=None):
    """The exact `gh` argv one page read runs — public so a test fixture can name it."""
    return ["gh", "api", "graphql", "-f", "query=" + _query(repo, pr, after)]


def permission_argv(repo, login):
    """The exact `gh` argv one repo-permission lookup runs."""
    return ["gh", "api", "repos/%s/collaborators/%s/permission" % (repo, login), "--jq", ".permission"]


def _nodes(connection):
    nodes = (connection or {}).get("nodes") if isinstance(connection, dict) else None
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else None


def _login(node):
    author = node.get("author") if isinstance(node, dict) else None
    return author.get("login") if isinstance(author, dict) else None


def _read_pages(repo, pr, cwd, env):
    """``(viewer, threads, pr_node, truncated)`` from every cursor page, or ``None`` on a failed or
    partial read. GraphQL reports an error beside the data that did resolve and `gh` then exits
    non-zero, so any non-zero exit is a failed read: a partial thread list would gate on less than
    the PR carries."""
    threads, viewer, first_pr, after, truncated = [], None, None, None, False
    for page in range(MAX_THREAD_PAGES):
        result = process.run(query_argv(repo, pr, after), cwd=cwd, env=env)
        if result.returncode != 0:
            return None
        try:
            data = (json.loads(result.stdout or "null") or {}).get("data") or {}
            pr_node = data["repository"]["pullRequest"]
            connection = pr_node["reviewThreads"]
        except (ValueError, AttributeError, KeyError, TypeError):
            return None
        nodes = _nodes(connection)
        if nodes is None or not isinstance(connection.get("pageInfo"), dict):
            return None
        if page == 0:
            viewer = ((data.get("viewer") or {}).get("login")) if isinstance(data.get("viewer"), dict) else None
            first_pr = pr_node
        threads.extend(nodes)
        info = connection["pageInfo"]
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
        if not after:
            return None
    else:
        # The backstop: a thread list cut short is a partial read, which must not gate as complete.
        return None
    return viewer, threads, first_pr, truncated


def _read_permissions(repo, logins, cwd, env):
    """``({login: permission or None}, failed_logins)``."""
    permissions, failed = {}, []
    for login in sorted(logins):
        result = process.run(permission_argv(repo, login), cwd=cwd, env=env)
        value = (result.stdout or "").strip() if result.returncode == 0 else ""
        permissions[login] = value or None
        if not value:
            failed.append(login)
    return permissions, failed


def absent_review_threads():
    """`facts.review_threads` when no PR is in scope. A factory."""
    return {"present": False}


def _empty_payload(viewer=None):
    return {
        "present": True,
        "available": False,
        "viewer": viewer,
        "threads_path": None,
        "threads": [],
        "counts": {state: 0 for state in THREAD_STATES},
        "untrusted": {"comments": 0, "threads": 0, "resolutions": 0},
        "conversation_path": None,
        "next_f": 1,
        "truncated": False,
    }


def _comment(node):
    body = node.get("body") or ""
    return {
        "author": _login(node),
        "association": node.get("authorAssociation"),
        "body": body,
        "createdAt": node.get("createdAt"),
        "url": node.get("url"),
        "commit": ((node.get("originalCommit") or {}).get("oid")) if isinstance(node.get("originalCommit"), dict) else None,
        # Marked means the marker AND a stamp the grammar reads: a pasted marker line with no stamp
        # under it is not something the pipeline wrote.
        "marked": parse_stamp(body) is not None,
        "trusted": trust.is_trusted(node.get("authorAssociation")),
    }


def _thread_comments(node):
    """The thread's comments in order: its root, then its latest comments, the root not repeated."""
    root = [c for c in _nodes(node.get("root")) or [] if c.get("state") != "PENDING"]
    recent = [c for c in _nodes(node.get("recent")) or [] if c.get("state") != "PENDING"]
    out = [_comment(c) for c in root[:1]]
    seen = {c["url"] for c in out if c["url"]}
    for c in recent:
        if c.get("url") and c.get("url") in seen:
            continue
        out.append(_comment(c))
    return out


def _anchored_hunk_changed(root, root_commit, reference, head, path, rng, subject):
    """§6's check for one resolved thread: its anchor mapped from the root's commit to the reference
    commit, then any hunk from there to ``head`` intersecting it. Run only for a trusted resolution, so
    an unresolved thread costs no `git` call."""
    if subject == "file":
        return hunk_changed(root, reference, head, path, None, subject)
    if rng is None:
        return True
    mapped = rng if reference == root_commit else map_range_between(root, root_commit, reference, path, rng)
    if mapped is None:
        return True
    return hunk_changed(root, reference, head, path, mapped, subject)


def read_threads(repo, pr, root, head, scratch_dir, cwd=None, env=None):
    """`facts.review_threads` for PR ``pr``: ``(payload, notices)``, never raising on a GitHub read.

    ``root`` is a checkout the hunk check runs `git` in (any worktree of the repo: they share objects),
    ``head`` the commit the threads are judged at — the evaluator's gated head, the resolver's workspace
    HEAD. ``threads_path`` (each listed thread with its trusted comments) and ``conversation_path``
    (trusted top-level comments and review bodies) are always written under ``scratch_dir``. A failed
    or partial read is ``available: false`` with `REVIEW_THREADS_UNAVAILABLE`; a gate reading it must
    not approve. The payload shape is the contract's (`skills/_shared/review-threads.md`).
    """
    notices = []
    read = _read_pages(repo, pr, cwd, env)
    if read is None:
        notices.append(
            "%s: the review threads of #%s could not be read; nothing gates on them this run, and the "
            "evaluator must not approve" % (REVIEW_THREADS_UNAVAILABLE, pr)
        )
        return _empty_payload(), notices
    viewer, thread_nodes, pr_node, truncated = read

    untrusted = {"comments": 0, "threads": 0, "resolutions": 0}
    raw = []
    resolvers = set()
    for node in thread_nodes:
        comments = _thread_comments(node)
        if not comments:
            continue
        recent = node.get("recent") if isinstance(node.get("recent"), dict) else {}
        total = recent.get("totalCount")
        if isinstance(total, int) and total > len(_nodes(recent) or []):
            truncated = True
        root_comment = comments[0]
        if not root_comment["trusted"]:
            untrusted["threads"] += 1
            continue
        untrusted["comments"] += sum(1 for c in comments if not c["trusted"])
        resolved_by = _login({"author": node.get("resolvedBy")}) if isinstance(node.get("resolvedBy"), dict) else None
        if node.get("isResolved") and resolved_by and resolved_by != viewer:
            resolvers.add(resolved_by)
        raw.append((node, comments, resolved_by))

    permissions, failed = _read_permissions(repo, resolvers, cwd, env)
    for login in failed:
        notices.append(
            "%s: the repo permission of %s could not be read; their resolution counts as untrusted"
            % (THREAD_PERMISSION_UNAVAILABLE, login)
        )

    threads, stored = [], []
    for node, comments, resolved_by in raw:
        root_comment = comments[0]
        root_stamp = parse_stamp(root_comment["body"]) if root_comment["marked"] else None
        if root_stamp is not None and root_stamp["kind"] != "root":
            # A reply stamp opening a thread is not a pipeline root: the human's thread, its root
            # read as their text.
            root_stamp = None
            root_comment = dict(root_comment, marked=False)
            comments = [root_comment] + comments[1:]
        origin = "pipeline" if root_stamp is not None else "operator"
        trusted_marked = [c for c in comments if c["trusted"] and c["marked"]]
        stamps = [parse_stamp(c["body"]) for c in trusted_marked]
        heads = [s["head"] for s in stamps if s]
        path = node.get("path")
        subject = "file" if (node.get("subjectType") == "FILE") else "line"
        if root_stamp and root_stamp["kind"] == "root":
            root_commit, rng, subject = root_stamp["head"], root_stamp["range"], root_stamp["subject"]
            path = root_stamp["path"] or path
        else:
            root_commit = root_comment["commit"]
            end = node.get("originalLine")
            start = node.get("originalStartLine") or end
            rng = [start, end] if subject == "line" and isinstance(end, int) and isinstance(start, int) else None
        reference = heads[-1] if heads else root_commit
        is_resolved = bool(node.get("isResolved"))
        trusted_resolution = resolution_trusted(is_resolved, resolved_by, viewer, permissions)
        if is_resolved and not trusted_resolution:
            untrusted["resolutions"] += 1
        state, pending_reply = derive_state(
            comments, origin, trusted_resolution,
            lambda: _anchored_hunk_changed(root, root_commit, reference, head, path, rng, subject),
        )
        # The latest trusted human comment — on an operator-rooted thread with no reply, its root.
        humans = [c for c in comments if c["trusted"] and not c["marked"]]
        reply = humans[-1]["body"] if humans else None
        entry = {
            "id": node.get("id"),
            "url": root_comment["url"],
            "f": root_stamp["f"] if root_stamp and root_stamp["kind"] == "root" else None,
            "o": None,
            "origin": origin,
            "state": state,
            "pending_reply": pending_reply,
            "path": path,
            "subject": subject,
            "range": rng if subject == "line" else None,
            "reference_sha": reference if reference and re.fullmatch(_HEX40, reference) else None,
            "flags": list(root_stamp["flags"]) if root_stamp and root_stamp["kind"] == "root" else [],
            "supersedes": root_stamp["supersedes"] if root_stamp and root_stamp["kind"] == "root" else None,
            "superseded_by": None,
            "reply_reason": reason_line(reply),
            "_created": root_comment["createdAt"] or "",
        }
        threads.append(entry)
        stored.append(
            [
                {k: c[k] for k in ("author", "body", "createdAt", "url", "marked")}
                for c in comments if c["trusted"]
            ]
        )

    operator_threads = sorted((t for t in threads if t["origin"] == "operator"), key=lambda t: t["_created"])
    for ordinal, entry in enumerate(operator_threads, start=1):
        entry["o"] = ordinal
    by_f = {t["f"]: t for t in threads if t["f"] is not None}
    by_url = {t["url"]: t for t in threads if t["url"]}
    for entry in threads:
        ref = entry["supersedes"]
        if not ref or entry["f"] is None:
            continue
        target = by_f.get(int(ref[1:])) if ref.startswith("F") else by_url.get(ref)
        if target is not None and target is not entry:
            target["superseded_by"] = "F%d" % entry["f"]
    for entry in threads:
        entry.pop("_created")

    conversation = []
    for kind, connection, limit in (("comment", pr_node.get("comments"), PR_COMMENTS),
                                    ("review", pr_node.get("reviews"), PR_REVIEWS)):
        nodes = _nodes(connection) or []
        total = connection.get("totalCount") if isinstance(connection, dict) else None
        if isinstance(total, int) and total > len(nodes):
            truncated = True
        for node in nodes:
            if kind == "review" and node.get("state") == "PENDING":
                continue  # this viewer's own unsubmitted review: no one else can see it
            if not trust.is_trusted(node.get("authorAssociation")):
                untrusted["comments"] += 1
                continue
            body = node.get("body") or ""
            conversation.append(
                {
                    "kind": kind,
                    "author": _login(node),
                    "body": body,
                    "createdAt": node.get("createdAt"),
                    "url": node.get("url"),
                    "marked": is_marked(body),
                    "state": node.get("state") if kind == "review" else None,
                }
            )
    conversation.sort(key=lambda c: c.get("createdAt") or "")
    if truncated:
        notices.append(
            "%s: #%s has more review threads, thread comments, comments or reviews than one read "
            "carries; the oldest are missing" % (REVIEW_THREADS_TRUNCATED, pr)
        )

    scratch = Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    threads_path = scratch / ("pr-%s-review-threads.json" % pr)
    threads_path.write_text(
        json.dumps([dict(t, comments=c) for t, c in zip(threads, stored)], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    conversation_path = scratch / ("pr-%s-conversation.json" % pr)
    conversation_path.write_text(json.dumps(conversation, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = {state: 0 for state in THREAD_STATES}
    for entry in threads:
        counts[entry["state"]] += 1
    payload = {
        "present": True,
        "available": True,
        "viewer": viewer,
        "threads_path": str(threads_path),
        "threads": threads,
        "counts": counts,
        "untrusted": untrusted,
        "conversation_path": str(conversation_path),
        "next_f": max(by_f, default=0) + 1,
        "truncated": truncated,
    }
    return payload, notices


def main(argv):
    sys.stderr.write(
        "usage: review_threads.py is import-only for now (read_threads is composed by "
        "prep_evaluator and prep_resolver); the `flags` subcommand lands with #145 phase 3\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
