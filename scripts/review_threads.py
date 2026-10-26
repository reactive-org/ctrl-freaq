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
Every case the diff cannot answer (a reference commit missing locally, a path absent at the reference
or at the judged head that existed when the thread was opened) counts as changed: a resolved thread re-threads, it never wrongly counts as
accepted. The reference is the head on the thread's latest trusted marked comment, which is the code the
human last heard about before resolving only because the pipeline never replies in a resolved thread
(`gh_persist.py review-reply` refuses one); GitHub records no resolution time to check it against.

`read_threads` and `confirm_dismissals` are composed in-process by `prep_evaluator` and `prep_resolver`
(`facts.review_threads`, `facts.security.entries[].thread`). Their failures are notices
(`REVIEW_THREADS_UNAVAILABLE`, `REVIEW_THREADS_TRUNCATED`, `THREAD_PERMISSION_UNAVAILABLE`), never a
decision code. The `flags` subcommand (evaluator S4.6) decides which code gets a new thread: the pure
`build_flags` over parsed inputs, behind a thin git-reading wrapper; it is always `ok`. Every `gh` and
`git` call goes through `pipelib.process.run`.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse  # noqa: E402
from pipelib import process, trust  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402

MARKER = "<!-- review-thread:v1 -->"

# The closed state set, in the contract's table order. `operator-thread` outranks `unreviewed` and
# `changes-requested`; the derivation's precedence is `derive_state`'s.
THREAD_STATES = ("unreviewed", "accepted", "changes-requested", "operator-thread", "superseded")

# At most this many new threads per review (the cap `flags` applies). The review loop's
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
    "id isResolved resolvedBy { login } path subjectType diffSide line startLine originalLine originalStartLine "
    "root: comments(first: 1) { nodes { " + _COMMENT_FIELDS + " } } "
    "recent: comments(last: " + str(THREAD_RECENT_COMMENTS) + ") { totalCount nodes { " + _COMMENT_FIELDS + " } }"
)

_HEX40 = r"[0-9a-f]{40}"
_ROOT_STAMP_RE = re.compile(
    r"^<!-- F(?P<f>[1-9][0-9]*) · head (?P<head>" + _HEX40 + r") · anchor (?P<anchor>.+?)"
    r" · flags (?P<flags>.*?)(?: · supersedes (?P<supersedes>F[1-9][0-9]*|https://\S+))? -->$"
)
_REPLY_STAMP_RE = re.compile(r"^<!-- reply(?: F(?P<f>[1-9][0-9]*))? · head (?P<head>" + _HEX40 + r") -->$")
_REVIEW_STAMP_RE = re.compile(r"^<!-- review · head (?P<head>" + _HEX40 + r") -->$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# ---------------------------------------------------------------------------
# Stamps
# ---------------------------------------------------------------------------


def _check_field(value, name):
    """``value`` when the stamp can carry it: a non-empty string with no `-->`, ` · `, newline or NUL.
    Never coerced — a ``None`` path is refused, not stamped as "None"."""
    if not isinstance(value, str):
        raise ValueError("%s must be a string, got %r" % (name, value))
    if not value or "-->" in value or "\n" in value or " · " in value or "\x00" in value:
        raise ValueError("%s %r cannot be stamped (empty, or carries `-->`, ` · `, a newline or NUL)" % (name, value))
    return value


def _is_int(value):
    # bool is an int subclass; `True` is not a line or an id.
    return isinstance(value, int) and not isinstance(value, bool)


def _encode_flag(key):
    """A flag key as the stamp writes it: `%` and `,` percent-encoded, since `,` separates keys and a key
    embeds a path, which may carry either. Spaces stay as they are."""
    return key.replace("%", "%25").replace(",", "%2C")


def _decode_flag(key):
    return key.replace("%2C", ",").replace("%25", "%")


def stamp_root(f, head, path, subject, start, end, flags, body, supersedes=None):
    """The full text of a pipeline thread's root comment: the marker, the stamp line, the visible
    `**F<n>**` heading, then the model-written ``body``. ``subject`` is ``"line"`` (``start``..``end``)
    or ``"file"``. Raises ``ValueError`` — never ``TypeError`` — on a value the stamp grammar cannot
    carry, wrong types included, so a bad spec fails before anything is posted."""
    if not _is_int(f) or f < 1:
        raise ValueError("F id must be a positive int, got %r" % (f,))
    if not (isinstance(head, str) and re.fullmatch(_HEX40, head)):
        raise ValueError("head must be a 40-hex commit sha, got %r" % (head,))
    path = _check_field(path, "path")
    if subject == "file":
        anchor = "%s:file" % path
    elif subject == "line":
        if not (_is_int(start) and _is_int(end) and 1 <= start <= end):
            raise ValueError("a line anchor needs 1 <= start <= end, got %r-%r" % (start, end))
        anchor = "%s:%d-%d" % (path, start, end)
    else:
        raise ValueError("subject must be 'line' or 'file', got %r" % (subject,))
    flags = flags or []
    if not isinstance(flags, (list, tuple)):
        raise ValueError("flags must be a list of keys, got %r" % (flags,))
    keys = [_encode_flag(_check_field(k, "flag")) for k in flags]
    stamp = "<!-- F%d · head %s · anchor %s · flags %s" % (f, head, anchor, ",".join(keys))
    if supersedes is not None:
        if not isinstance(supersedes, str) or not re.fullmatch(r"F[1-9][0-9]*|https://\S+", supersedes) or "-->" in supersedes:
            raise ValueError("supersedes must be F<m> or a comment URL, got %r" % (supersedes,))
        stamp += " · supersedes %s" % supersedes
    stamp += " -->"
    return "%s\n%s\n**F%d**\n\n%s" % (MARKER, stamp, f, body)


def stamp_reply(head, body, f=None):
    """The full text of a pipeline reply in a thread: the marker, the reply stamp (`reply F<n>`, or a
    bare `reply` when the thread has no `F` id), then ``body``. Raises ``ValueError`` only."""
    if not (isinstance(head, str) and re.fullmatch(_HEX40, head)):
        raise ValueError("head must be a 40-hex commit sha, got %r" % (head,))
    if f is not None and (not _is_int(f) or f < 1):
        raise ValueError("F id must be a positive int, got %r" % (f,))
    stamp = "<!-- reply%s · head %s -->" % (" F%d" % f if f is not None else "", head)
    return "%s\n%s\n\n%s" % (MARKER, stamp, body)


def stamp_review(head, body):
    """The full text of the review body that carries the threads: the marker, the review stamp, then
    ``body``. Raises ``ValueError`` only."""
    if not (isinstance(head, str) and re.fullmatch(_HEX40, head)):
        raise ValueError("head must be a 40-hex commit sha, got %r" % (head,))
    return "%s\n<!-- review · head %s -->\n\n%s" % (MARKER, head, body)


def is_marked(body):
    """Whether ``body`` opens with the marker line (leading whitespace ignored)."""
    return isinstance(body, str) and body.lstrip().startswith(MARKER)


def parse_stamp(body):
    """The stamp of a marked comment: ``{"kind": "root", f, head, path, subject, range, flags,
    supersedes}``, ``{"kind": "reply", f, head}`` or ``{"kind": "review", head}``; ``None`` for an
    unmarked body or a second line the grammar does not read. Never raises."""
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
    match = _REVIEW_STAMP_RE.match(line)
    if match:
        return {"kind": "review", "head": match.group("head")}
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


def _path_exists(root, rev, path):
    """Whether ``path`` is in commit ``rev``'s tree (a literal tree lookup, no pathspec)."""
    return process.run(["git", "cat-file", "-e", "%s:%s" % (rev, path)], cwd=root).returncode == 0


def _diff_hunks(root, base, head, path):
    """The hunks of `git diff -U0 base head -- path`, or ``None`` when the diff cannot be read in
    lines: an unreadable diff, and a diff with output but no `@@` hunk — a binary file, a mode-only
    change — which a line range cannot be checked against. The path is matched literally, never as a
    glob or `:` pathspec, and read raw (no textconv), the lines GitHub numbers."""
    result = process.run(
        ["git", "--literal-pathspecs", "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-U0",
         base, head, "--", path],
        cwd=root,
    )
    if result.returncode != 0:
        return None
    hunks = parse_hunks(result.stdout)
    if not hunks and (result.stdout or "").strip():
        return None
    return hunks


def map_range_between(root, base, head, path, rng):
    """``rng`` on ``path`` at commit ``base`` mapped to commit ``head``; ``rng`` itself when the two
    are one commit; ``None`` when it cannot be mapped (a commit missing locally, the path absent at
    either commit, an unreadable diff)."""
    if base == head:
        return list(rng) if rng is not None else None
    if not (_commit_exists(root, base) and _commit_exists(root, head)):
        return None
    if not (_path_exists(root, base, path) and _path_exists(root, head, path)):
        return None
    hunks = _diff_hunks(root, base, head, path)
    if hunks is None:
        return None
    return map_range(hunks, rng)


def _opened_on_absent_path(root, anchor, path):
    """Whether a thread was opened where its path did not exist: a base-side comment on a file the PR
    deletes, or a file thread on one. A root commit missing locally never counts, so the guard stays."""
    return bool(anchor) and _commit_exists(root, anchor) and not _path_exists(root, anchor, path)


def hunk_changed(root, reference, head, path, rng, subject="line", anchor=None):
    """Whether the code a thread is anchored on changed between ``reference`` and ``head``: any hunk
    of `git diff -U0 <reference> <head> -- <path>` whose old side intersects ``rng``; any hunk at all
    for a ``file`` subject. A reference commit missing locally (``git cat-file -e`` fails — after a
    force-push, say), an unreadable diff, a binary or mode-only change (output with no `@@` hunk), a
    line thread with no range, and a path absent at the reference or at ``head`` that was present at
    the thread's root commit ``anchor`` (deleted or renamed after the thread was opened) all count as
    changed. A ``file`` check whose path was absent at ``anchor`` too was opened on the deletion: the
    diff alone decides (absent at both is unchanged, restored is changed). The path check comes before
    the same-commit shortcut, so a thread reads the same at every head."""
    if not reference or not _commit_exists(root, reference) or not _commit_exists(root, head):
        return True
    if not (_path_exists(root, reference, path) and _path_exists(root, head, path)):
        if not (subject == "file" and _opened_on_absent_path(root, anchor, path)):
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
        # A cursor page reads the threads only: the PR's comments and reviews came with the first.
        return (
            "query { repository(owner: %s, name: %s) { pullRequest(number: %d) { "
            "reviewThreads(%s, after: %s) { pageInfo { hasNextPage endCursor } nodes { %s } } } } }"
            % (json.dumps(owner), json.dumps(name), int(pr), page, json.dumps(after), _THREAD_FIELDS)
        )
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
    """``(viewer, threads, pr_node)`` from every cursor page, or ``None`` on a failed or
    partial read. GraphQL reports an error beside the data that did resolve and `gh` then exits
    non-zero, so any non-zero exit is a failed read: a partial thread list would gate on less than
    the PR carries."""
    threads, viewer, first_pr, after = [], None, None, None
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
    return viewer, threads, first_pr


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


def _empty_payload(head=None):
    return {
        "present": True,
        "available": False,
        "viewer": None,
        "head": head,
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
    stamp = parse_stamp(body)
    return {
        "author": _login(node),
        "association": node.get("authorAssociation"),
        "body": body,
        "createdAt": node.get("createdAt"),
        "url": node.get("url"),
        "commit": ((node.get("originalCommit") or {}).get("oid")) if isinstance(node.get("originalCommit"), dict) else None,
        # Marked means the marker AND a stamp the grammar reads: a pasted marker line with no stamp
        # under it is not something the pipeline wrote.
        "stamp": stamp,
        "marked": stamp is not None,
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
    # A stamp counts only in its own position: a root stamp opening the thread, a reply stamp after
    # it. Anything else (a review stamp pasted into a thread, a root stamp as a reply, a reply stamp
    # opening one) is a human's text, and moves no reference commit.
    for index, comment in enumerate(out):
        expected = "root" if index == 0 else "reply"
        if comment["stamp"] is not None and comment["stamp"]["kind"] != expected:
            out[index] = dict(comment, marked=False, stamp=None)
    return out


def _anchored_hunk_changed(root, root_commit, reference, head, path, rng, subject):
    """§6's check for one resolved thread: its anchor mapped from the root's commit to the reference
    commit, then any hunk from there to ``head`` intersecting it. Run only for a trusted resolution, so
    an unresolved thread costs no `git` call."""
    if subject == "file":
        return hunk_changed(root, reference, head, path, None, subject, anchor=root_commit)
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
    HEAD. On a read that succeeds, ``threads_path`` (each listed thread with its trusted comments) and
    ``conversation_path`` (trusted top-level comments and review bodies) are written under
    ``scratch_dir``; a failed read writes neither and reports both as ``None``. A failed
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
        return _empty_payload(head), notices
    viewer, thread_nodes, pr_node = read
    truncated = False

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

    threads, stored, extras = [], [], []
    for node, comments, resolved_by in raw:
        root_comment = comments[0]
        root_stamp = root_comment["stamp"]
        origin = "pipeline" if root_stamp is not None else "operator"
        heads = [c["stamp"]["head"] for c in comments if c["trusted"] and c["marked"]]
        path = node.get("path")
        subject = "file" if (node.get("subjectType") == "FILE") else "line"
        # What the hunk check compares. A human comment on the diff's base (LEFT) side numbers its
        # lines in the base file, not at its commit, so a line range cannot be checked: any change to
        # the path counts, the conservative side. The root commit also decides whether the path check
        # applies: a base-side comment on a file the PR deletes was opened where the path is absent.
        check_subject = subject
        if root_stamp is not None:
            root_commit, rng, subject = root_stamp["head"], root_stamp["range"], root_stamp["subject"]
            path = root_stamp["path"] or path
            check_subject = subject
        else:
            if node.get("diffSide") == "LEFT":
                check_subject = "file"
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
            lambda: _anchored_hunk_changed(root, root_commit, reference, head, path, rng, check_subject),
        )
        # The latest trusted human comment after the pipeline's last trusted marked one — on an
        # operator-rooted thread with no reply, its root. A comment the pipeline has since answered is not
        # the human's last word, so it is never read as a reply (nor as a dismissal's reason).
        counted = [c for c in comments if c["trusted"]]
        last_marked = max((i for i, c in enumerate(counted) if c["marked"]), default=-1)
        humans = [c for c in counted[last_marked + 1:] if not c["marked"]]
        reply = humans[-1]["body"] if humans else None
        extras.append({"root_commit": root_commit, "side": node.get("diffSide")})
        entry = {
            "id": node.get("id"),
            "url": root_comment["url"],
            "f": root_stamp["f"] if root_stamp is not None else None,
            "o": None,
            "origin": origin,
            "state": state,
            "pending_reply": pending_reply,
            "path": path,
            "subject": subject,
            "range": rng if subject == "line" else None,
            "reference_sha": reference if reference and re.fullmatch(_HEX40, reference) else None,
            "flags": list(root_stamp["flags"]) if root_stamp is not None else [],
            "supersedes": root_stamp["supersedes"] if root_stamp is not None else None,
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
    # One F id can name two threads (a review re-posted after a lost response): a successor naming it
    # supersedes every copy, so none is left a gating chain head.
    by_f = {}
    for t in threads:
        if t["f"] is not None:
            by_f.setdefault(t["f"], []).append(t)
    by_url = {t["url"]: t for t in threads if t["url"]}
    for entry in threads:
        ref = entry["supersedes"]
        if not ref or entry["f"] is None:
            continue
        targets = by_f.get(int(ref[1:]), []) if ref.startswith("F") else [by_url[ref]] if ref in by_url else []
        for target in targets:
            if target is not entry:
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
                    # Only a review body can carry the pipeline's stamp (the review that carries its
                    # threads); a top-level comment never does.
                    "marked": kind == "review" and (parse_stamp(body) or {}).get("kind") == "review",
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
        # Beside each listed thread: its trusted comments, the commit its range numbers (`root_commit`)
        # and GitHub's diff side, which `flags` needs to anchor a successor and the payload omits.
        json.dumps([dict(t, comments=c, **x) for t, c, x in zip(threads, stored, extras)], ensure_ascii=False,
                   indent=1),
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
        "head": head,
        "threads_path": str(threads_path),
        "threads": threads,
        "counts": counts,
        "untrusted": untrusted,
        "conversation_path": str(conversation_path),
        "next_f": max(by_f, default=0) + 1,
        "truncated": truncated,
    }
    return payload, notices




# ---------------------------------------------------------------------------
# Thread-cited dismissals
# ---------------------------------------------------------------------------


def confirm_dismissals(security, threads_fact):
    """``security`` (`parse.scan_security`'s fact) with ``thread`` set on every entry: ``{"url",
    "confirmed"}`` on a `dismissed` entry that cites a review thread, ``None`` on every other.

    ``confirmed`` is true only when the cited thread is listed in an `available` read, is a pipeline thread
    on **this finding** (its flags carry `security:<entry id>`, so no other thread's reply can dismiss it),
    its resolution is trusted (state `accepted` or `superseded` — an untrusted resolution reads
    unresolved), and its `reply_reason` (the trusted human comment after the pipeline's last one) equals
    the entry's recorded reason, both normalised by :func:`reason_line`. On a read that is not
    `available` it is ``None``: unknown, not false, so no caller rewrites a dismissal on a failed read. So a body
    reason that differs from the thread's trusted reply never counts, and the reason is re-read from the
    thread on every run. A dismissal that cites no thread keeps the #139 `dismissals_trusted` rule; this
    function does not touch it. Pure; returns a new dict."""
    out = dict(security or {})
    available = isinstance(threads_fact, dict) and bool(threads_fact.get("available"))
    by_url = {t.get("url"): t for t in (threads_fact or {}).get("threads") or [] if isinstance(t, dict)} if available else {}
    entries = []
    for entry in out.get("entries") or []:
        entry = dict(entry)
        url = entry.get("thread_url") if entry.get("disposition") == "dismissed" else None
        if url:
            thread = by_url.get(url)
            confirmed = None if not available else bool(
                thread
                and thread.get("origin") == "pipeline"
                and "security:%s" % entry.get("id") in (thread.get("flags") or [])
                and thread.get("state") in ("accepted", "superseded")
                and thread.get("reply_reason") is not None
                and reason_line(thread.get("reply_reason")) == reason_line(entry.get("reason"))
            )
            entry["thread"] = {"url": url, "confirmed": confirmed}
        else:
            entry["thread"] = None
        entries.append(entry)
    out["entries"] = entries
    return out


# ---------------------------------------------------------------------------
# Flags: which code gets a new thread (evaluator S4.6)
# ---------------------------------------------------------------------------

# Cap order, highest first: security > sensitive > judgment > out-of-plan > plan.
FLAG_KINDS = ("security", "sensitive", "judgment", "out-of-plan", "plan")
# Kinds whose signal names a file, not a site: they match an existing thread on kind and path alone,
# since the file's first hunk — their anchor — moves as the PR is revised.
_FILE_LEVEL_KINDS = ("sensitive", "out-of-plan")
_SITE_RE = re.compile(r"^(?P<path>.+):(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?$")
_SECURITY_SITE_RE = re.compile(r"^(?P<id>SEC-[1-9][0-9]*)\s+(?P<site>\S.*)$")
_PLAN_THREAD_RE = re.compile(r"^(?P<site>\S.*?)\s+—\s+(?P<text>\S.*)$")


def _site(text):
    """``(path, [start, end])`` of a `path:line[-line]` site, or ``None``."""
    match = _SITE_RE.match((text or "").strip())
    if not match:
        return None
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if end < start:
        return None
    return match.group("path"), [start, end]


def flag_key_parts(key):
    """``(kind, path)`` of a flag key; ``path`` is ``None`` for a security key (`security:SEC-<n>`)."""
    kind, _, rest = (key or "").partition(":")
    if kind == "judgment":
        _, _, rest = rest.partition(":")
    return kind, (None if kind == "security" else rest)


def _contained_hunk(hunks, rng):
    """The index of the hunk whose new side holds all of ``rng``, else ``None``. ``hunks`` are new-side
    ``(start, count)`` pairs with ``count > 0``."""
    for index, (start, count) in enumerate(hunks):
        if start <= rng[0] and rng[1] <= start + count - 1:
            return index
    return None


def _anchor(files, path, rng):
    """Where a flag on ``path`` anchors at the gated head: ``{"path", "subject", "range", "hunk"}``, or
    ``None`` when the path is not in the diff. A site (``rng``) inside one hunk's new side anchors on
    that range, else on the whole file; a file-level signal (``rng`` ``None``) anchors on the file's
    first hunk's new side, or on the whole file when it has no new-side lines (deleted, binary,
    rename-only)."""
    info = files.get(path)
    if info is None:
        return None
    hunks = info.get("hunks") or []
    if rng is not None:
        index = _contained_hunk(hunks, rng)
        if index is not None:
            return {"path": path, "subject": "line", "range": list(rng), "hunk": index}
        return {"path": path, "subject": "file", "range": None, "hunk": None}
    if hunks:
        start, count = hunks[0]
        return {"path": path, "subject": "line", "range": [start, start + count - 1], "hunk": 0}
    return {"path": path, "subject": "file", "range": None, "hunk": None}


def _thread_ref(thread):
    return "F%d" % thread["f"] if thread.get("f") is not None else thread.get("url")


def _head_from_sha(thread):
    """The commit a thread's ``range`` numbers: its root stamp's head (a pipeline thread), else the
    human root comment's commit."""
    stamp = parse_stamp(((thread.get("comments") or [{}])[0] or {}).get("body") or "")
    if stamp is not None and stamp.get("kind") == "root":
        return stamp["head"]
    return thread.get("root_commit")


def build_flags(files, threads, signals, mapper, next_f):
    """Decide which code gets a new thread. Pure: every git read comes in as ``files`` and ``mapper``.

    - ``files`` — every path in the gated diff (``origin/<base>...HEAD``) at its gated-head name:
      ``{path: {"hunks": [(new_start, new_count), ...], "renamed_from": old_path?}}``, new-side ranges
      only, empty for a file with no new-side lines. A flag on a renamed file's old path (the assess
      envelope reads paths with `--no-renames`) anchors on its new name.
    - ``threads`` — the listed threads (`facts.review_threads.threads_path`): each payload entry plus its
      ``comments``, ``root_commit`` and ``side``.
    - ``signals`` — ``{"sensitive": [path], "out_of_plan": [path], "judgment": [(dimension, path, rng)],
      "security": [(id, path, rng_or_None)], "plan": [(path, rng, text)], "reasons": {path: reason},
      "rejected": [...]}`` — security entries already resolved to a path (a model-written site, else the
      record's anchor path), ``rejected`` carrying input lines that did not parse.
    - ``mapper(from_sha, path, rng)`` — ``rng`` at ``from_sha`` mapped to the gated head, ``None`` when
      it cannot be (`map_range_between`).

    Returns the `flags` payload without ``spec_path``: ``new`` (successors first, then new threads in
    cap-priority order, each with its ``f``), ``matched``, ``rejected``, ``overflow``,
    ``unanchored_security``, ``retired``, ``next_f``. The rules are the contract's
    (`skills/_shared/review-threads.md` §7) and the plan's: grouping by hunk, `THREAD_CAP` with the
    file-collapse and the priority order, idempotence against chain heads, one successor per superseded
    chain head (exempt from the cap).
    """
    rejected = list(signals.get("rejected") or [])
    unanchored_security, matched, retired, overflow = [], [], [], []
    reasons = dict(signals.get("reasons") or {})

    flags = []  # each {"kind", "key", "path", "anchor", "sec", "text"}
    aliases = {info["renamed_from"]: p for p, info in files.items() if info.get("renamed_from")}

    def add(kind, key, path, rng, sec=None, text=None, source=None):
        anchor = _anchor(files, aliases.get(path, path), rng)
        if anchor is None:
            if kind == "security":
                unanchored_security.append(sec)
            else:
                rejected.append({"source": source or kind, "line": text or "%s:%s" % (path, rng),
                                 "reason": "not-in-diff"})
            return
        flags.append({"kind": kind, "key": key, "path": path, "anchor": anchor, "sec": sec, "text": text})

    for entry in signals.get("security") or []:
        sec, path, rng = entry[:3]
        fallback = entry[3] if len(entry) > 3 else None
        if rng is not None and _anchor(files, aliases.get(path, path), rng) is None:
            # A model-written site outside the diff: report it, and fall back to the record's anchor path.
            rejected.append({"source": "security-sites", "line": "%s %s:%d-%d" % (sec, path, rng[0], rng[1]),
                             "reason": "not-in-diff"})
            path, rng = fallback, None
        add("security", "security:%s" % sec, path, rng, sec=sec)
    for path in signals.get("sensitive") or []:
        add("sensitive", "sensitive:%s" % path, path, None)
    for dimension, path, rng in signals.get("judgment") or []:
        add("judgment", "judgment:%s:%s" % (dimension, path), path, rng, source="judgment",
            text="%s · %s:%d-%d" % (dimension, path, rng[0], rng[1]))
    for path in signals.get("out_of_plan") or []:
        add("out-of-plan", "out-of-plan:%s" % path, path, None)
    for path, rng, text in signals.get("plan") or []:
        add("plan", "plan:%s" % path, path, rng, text=text, source="plan-threads")

    # Chain heads: trusted pipeline threads no successor names.
    heads = [t for t in threads if t.get("origin") == "pipeline" and t.get("superseded_by") is None]
    gating_superseded = [t for t in threads if t.get("state") == "superseded" and t.get("superseded_by") is None]
    riders = {id(t): [] for t in gating_superseded}
    mapped_cache = {}

    def head_range(thread, path):
        """The head's range mapped to the gated head, once per head (a git read)."""
        if id(thread) not in mapped_cache:
            mapped_cache[id(thread)] = mapper(_head_from_sha(thread), thread.get("path") or path, thread["range"])
        return mapped_cache[id(thread)]

    def same(a, b):
        return aliases.get(a, a) == aliases.get(b, b)

    def matches(flag, thread):
        kind, path = flag["kind"], flag["path"]
        for key in thread.get("flags") or []:
            k_kind, k_path = flag_key_parts(key)
            if k_kind != kind:
                continue
            if kind == "security":
                if key != flag["key"]:
                    continue
                # One SEC-<n> keeps one live thread wherever its site moved. A superseded head is matched
                # only on the finding's own file: its successor lands there, and a site elsewhere takes its
                # own thread.
                if thread.get("state") != "superseded":
                    return True
                return same(thread.get("path"), flag["anchor"]["path"])
            elif not same(k_path, path):
                continue
            if kind in _FILE_LEVEL_KINDS or thread.get("subject") == "file" or flag["anchor"]["subject"] == "file":
                return True
            if thread.get("range") is None:
                return True
            mapped = head_range(thread, path)
            if mapped is None:
                return True  # unmappable: match on kind and path, so nothing posts twice
            a = flag["anchor"]["range"]
            return mapped[0] <= a[0] and a[1] <= mapped[1]
        return False

    fresh = []
    for flag in flags:
        hits = [t for t in heads if matches(flag, t)]
        if not hits:
            fresh.append(flag)
            continue
        live = [t for t in hits if t.get("state") != "superseded"]
        if live:
            matched.append({"key": flag["key"], "thread": _thread_ref(live[0])})
        elif aliases.get(hits[0].get("path"), hits[0].get("path")) not in files:
            fresh.append(flag)  # the head's successor cannot be posted, so the flag takes its own thread
        else:
            riders[id(hits[0])].append(flag["key"])
            matched.append({"key": flag["key"], "thread": _thread_ref(hits[0]), "rides_successor": True})

    # Successors: one per superseded chain head, whatever fires now.
    successors = []
    for thread in gating_superseded:
        path = aliases.get(thread.get("path"), thread.get("path"))
        keys = list(thread.get("flags") or []) + [k for k in riders[id(thread)] if k not in (thread.get("flags") or [])]
        ref = _thread_ref(thread)
        if path not in files:
            # Nothing to anchor: the head stops gating (no successor can replace it), and a security
            # finding on it goes to the `Security` card's first case instead.
            retired.append({"thread": thread.get("id"), "ref": ref, "reason": "path-left-diff"})
            secs = [k.partition(":")[2] for k in keys if flag_key_parts(k)[0] == "security"]
            unanchored_security.extend(s for s in secs if s not in unanchored_security)
            continue
        anchor = {"path": path, "subject": "file", "range": None, "hunk": None}
        side = thread.get("side")
        if thread.get("subject") == "line" and thread.get("range") and not (thread.get("origin") == "operator" and side == "LEFT"):
            mapped = head_range(thread, path)
            if mapped is not None and _contained_hunk(files[path]["hunks"], mapped) is not None:
                anchor = {"path": path, "subject": "line", "range": list(mapped), "hunk": None}
        first_line = None
        if thread.get("origin") == "operator":
            body = ((thread.get("comments") or [{}])[0] or {}).get("body") or ""
            first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), None)
        successors.append({
            "anchor": anchor,
            "flags": keys if thread.get("origin") == "pipeline" else [],
            "supersedes": ref,
            "predecessor": {"ref": ref, "url": thread.get("url"), "first_line": first_line},
        })

    # Group fresh flags by hunk (line anchors) or by file (file anchors).
    groups, order = {}, []
    for flag in fresh:
        a = flag["anchor"]
        gkey = (a["path"], a["hunk"]) if a["subject"] == "line" else (a["path"], "file")
        if gkey not in groups:
            groups[gkey] = {"path": a["path"], "subject": a["subject"],
                            "range": list(a["range"]) if a["range"] else None, "flags": []}
            order.append(gkey)
        g = groups[gkey]
        if a["subject"] == "line":
            g["range"] = [min(g["range"][0], a["range"][0]), max(g["range"][1], a["range"][1])]
        g["flags"].append(flag)
    candidates = [groups[k] for k in order]

    def rank(group):
        return min(FLAG_KINDS.index(f["kind"]) for f in group["flags"])

    if len(candidates) > THREAD_CAP:
        by_path = {}
        for g in candidates:
            by_path.setdefault(g["path"], []).append(g)
        collapsed = []
        for path, items in by_path.items():
            if len(items) == 1:
                collapsed.append(items[0])
            else:
                collapsed.append({"path": path, "subject": "file", "range": None,
                                  "flags": [f for g in items for f in g["flags"]]})
        candidates = collapsed
    candidates.sort(key=rank)
    if len(candidates) > THREAD_CAP:
        for g in candidates[THREAD_CAP:]:
            for f in g["flags"]:
                overflow.append({"key": f["key"], "path": f["path"], "reason": "cap"})
                if f["kind"] == "security" and f["sec"] not in unanchored_security:
                    unanchored_security.append(f["sec"])
        candidates = candidates[:THREAD_CAP]

    new = []
    f_next = next_f
    for succ in successors:
        a = succ["anchor"]
        new.append({
            "f": f_next, "path": a["path"], "subject": a["subject"],
            "start_line": a["range"][0] if a["range"] else None, "line": a["range"][1] if a["range"] else None,
            "flags": succ["flags"], "supersedes": succ["supersedes"], "predecessor": succ["predecessor"],
            "reason": None, "plan": [], "security": [k.partition(":")[2] for k in succ["flags"]
                                                     if flag_key_parts(k)[0] == "security"],
        })
        f_next += 1
    for g in candidates:
        keys = []
        for f in g["flags"]:
            if f["key"] not in keys:
                keys.append(f["key"])
        out_of_plan = [f["path"] for f in g["flags"] if f["kind"] == "out-of-plan"]
        new.append({
            "f": f_next, "path": g["path"], "subject": g["subject"],
            "start_line": g["range"][0] if g["range"] else None, "line": g["range"][1] if g["range"] else None,
            "flags": keys, "supersedes": None, "predecessor": None,
            "reason": reasons.get(out_of_plan[0]) if out_of_plan else None,
            "plan": [f["text"] for f in g["flags"] if f["kind"] == "plan"],
            "security": [f["sec"] for f in g["flags"] if f["kind"] == "security"],
        })
        f_next += 1
    # A finding that has a thread in this post is not unanchored, whatever an old head of it said.
    threaded = {sec for t in new for sec in t["security"]}
    unanchored_security = [sec for sec in unanchored_security if sec not in threaded]
    return {
        "new": new,
        "matched": matched,
        "rejected": rejected,
        "overflow": overflow,
        "unanchored_security": unanchored_security,
        "retired": retired,
        "next_f": f_next,
    }


def _diff_files(root, base, head):
    """The gated diff's files at their gated-head names, each with its new-side hunks
    (``build_flags``'s ``files``). One `--name-status -z -M` read for the set (exact paths, no
    quoting), then one literal-pathspec `-U0` read per file; a deleted, binary or pure-rename file has
    no new-side lines. ``None`` when the set cannot be read."""
    listing = process.run(
        ["git", "diff", "--no-color", "--no-ext-diff", "--name-status", "-z", "-M", base, head], cwd=root
    )
    if listing.returncode != 0:
        return None
    parts = [p for p in (listing.stdout or "").split("\0")]
    files, i = {}, 0
    while i < len(parts) and parts[i]:
        status = parts[i]
        if status[:1] == "R":
            old, new = parts[i + 1], parts[i + 2]
            i += 3
            if status == "R100":
                files[new] = {"hunks": [], "renamed_from": old}
                continue
            paths = [old, new]
        else:
            new = parts[i + 1]
            i += 2
            if status[:1] == "D":
                files[new] = {"hunks": []}
                continue
            paths = [new]
        result = process.run(
            ["git", "--literal-pathspecs", "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", "-U0",
             base, head, "--"] + paths,
            cwd=root,
        )
        hunks = parse_hunks(result.stdout) if result.returncode == 0 else []
        files[new] = {"hunks": [(ns, nc) for _, _, ns, nc in hunks if nc > 0]}
        if len(paths) == 2:
            files[new]["renamed_from"] = old
    return files


def _read_lines(path):
    if not path:
        return []
    try:
        return [ln.rstrip("\n") for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (path, exc))
        sys.exit(EXIT_USAGE_ERROR)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (path, exc))
        sys.exit(EXIT_USAGE_ERROR)


def _entry_path(anchor):
    """A `## Security review` anchor's path (`<path> / <symbol>`, the symbol optional)."""
    path = (anchor or "").split(" / ", 1)[0].strip()
    site = _site(path)
    return site[0] if site else path or None


def gather_signals(pr_body, assess, security_site_lines, plan_thread_lines):
    """``build_flags``'s ``signals`` from the parsed inputs. Pure. A failed sensitive-path read
    (`sensitive.error`) contributes nothing: it has nothing to anchor, and it forces the merge gate to
    `ask` on its own."""
    rejected = []
    payload = assess.get("payload", assess) if isinstance(assess, dict) else {}
    sensitive = (payload.get("sensitive") or {}) if isinstance(payload, dict) else {}
    sensitive_paths = []
    for match in sensitive.get("matches") or []:
        path = match.get("path") if isinstance(match, dict) else None
        if path and path not in sensitive_paths:
            sensitive_paths.append(path)
    judgment = []
    for signal in ((payload.get("judgment") or {}).get("signals") or []):
        site = _site(signal.get("site"))
        if site:
            judgment.append((signal.get("dimension"), site[0], site[1]))
        else:
            rejected.append({"source": "judgment", "line": "%s · %s" % (signal.get("dimension"), signal.get("site")),
                             "reason": "unparsed"})
    sites = {}
    for line in security_site_lines:
        match = _SECURITY_SITE_RE.match(line.strip())
        site = _site(match.group("site")) if match else None
        if not site:
            rejected.append({"source": "security-sites", "line": line, "reason": "unparsed"})
            continue
        sites[match.group("id")] = site
    security = []
    for entry in parse.scan_security(pr_body).get("entries") or []:
        if entry.get("disposition") != "open":
            continue
        anchor_path = _entry_path(entry.get("anchor"))
        if entry["id"] in sites:
            path, rng = sites[entry["id"]]
        else:
            path, rng = anchor_path, None
        security.append((entry["id"], path, rng, anchor_path))
    plan = []
    for line in plan_thread_lines:
        match = _PLAN_THREAD_RE.match(line.strip())
        site = _site(match.group("site")) if match else None
        if not site:
            rejected.append({"source": "plan-threads", "line": line, "reason": "unparsed"})
            continue
        plan.append((site[0], site[1], match.group("text")))
    reasons = {e["path"]: e["reason"] for e in parse.scan_out_of_plan(pr_body).get("entries") or []}
    return {
        "sensitive": sensitive_paths,
        "out_of_plan": [p for p in (payload.get("out_of_plan") or []) if isinstance(p, str)],
        "judgment": judgment,
        "security": security,
        "plan": plan,
        "reasons": reasons,
        "rejected": rejected,
    }


def run_flags(root, pr_body_path, assess_path, threads_path, base_ref, head, scratch, security_sites=None,
              plan_threads=None):
    """The `flags` subcommand: read the inputs, run :func:`build_flags`, and write the `review-post`
    spec (`<scratch>/review-spec.json`) naming one body path per new thread and the review body, which
    the model then writes. Returns ``(payload, notices)``."""
    notices = []
    assess = _read_json(assess_path)
    payload = assess.get("payload", assess) if isinstance(assess, dict) else {}
    try:
        pr_body = Path(pr_body_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (pr_body_path, exc))
        sys.exit(EXIT_USAGE_ERROR)
    # The threads are required: without them every flag would look new, post a duplicate of a live thread
    # and reuse its F id. S4.6 runs `flags` only on an available read (`REVIEW_THREADS_TRUNCATED` alone still
    # lists every thread).
    if not threads_path:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: --threads names no file (facts.review_threads.threads_path "
                         "is null: the read failed, and nothing may be posted)\n")
        sys.exit(EXIT_USAGE_ERROR)
    threads = _read_json(threads_path)
    if not isinstance(threads, list):
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s is not a list of threads\n" % threads_path)
        sys.exit(EXIT_USAGE_ERROR)
    signals = gather_signals(pr_body, assess, _read_lines(security_sites), _read_lines(plan_threads))
    # The merge base `assess` read the range at. Without it (its fetch or merge-base failed) no range can be
    # trusted to be the PR's: a two-dot diff against the branch tip would anchor on base-only commits.
    base = (payload.get("range") or {}).get("base")
    files = _diff_files(root, base, head) if base else None
    if files is None:
        # Without the diff nothing can be anchored, and no head can be judged to have left it: post nothing,
        # retire nothing, and let the gate hold on the threads as read.
        notices.append("REVIEW_THREADS_DIFF_UNAVAILABLE: the gated diff %s...%s could not be read; nothing is "
                       "posted or retired this run, and the evaluator must not approve"
                       % (base or "origin/%s (no merge base)" % base_ref, head))
        return {"diff_available": False, "spec_path": None, "base": base, "new": [], "matched": [],
                "rejected": [], "overflow": [], "unanchored_security": [], "retired": [], "next_f": None}, notices
    next_f = max((t.get("f") or 0 for t in threads if isinstance(t, dict)), default=0) + 1
    result = build_flags(
        files, threads, signals,
        lambda from_sha, path, rng: map_range_between(root, from_sha, head, path, rng) if from_sha else None,
        next_f,
    )
    spec_path = None
    if result["new"]:
        out = Path(scratch)
        out.mkdir(parents=True, exist_ok=True)
        for thread in result["new"]:
            thread["body"] = str(out / ("thread-F%d.md" % thread["f"]))
        spec = {
            "head": head,
            "review_body": str(out / "review-body.md"),
            "threads": [
                {k: t[k] for k in ("f", "path", "subject", "start_line", "line", "flags", "supersedes", "body")}
                for t in result["new"]
            ],
        }
        spec_path = out / "review-spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        spec_path = str(spec_path)
    result["spec_path"] = spec_path
    result["base"] = base
    result["diff_available"] = True
    return result, notices


def main(argv):
    parser = argparse.ArgumentParser(prog="review_threads.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="subcommand")
    p_flags = sub.add_parser("flags", help="decide which code gets a new review thread (evaluator S4.6)")
    p_flags.add_argument("root", help="the checkout at the gated head (facts.workspace.path)")
    p_flags.add_argument("--pr-body", required=True)
    p_flags.add_argument("--assess", required=True, help="the S4.6 blast_radius.py assess envelope")
    p_flags.add_argument("--threads", required=True, help="facts.review_threads.threads_path")
    p_flags.add_argument("--base-ref", required=True)
    p_flags.add_argument("--head", required=True, help="the gated head's 40-hex sha")
    p_flags.add_argument("--scratch", required=True)
    p_flags.add_argument("--security-sites", help="`SEC-<n> <path>:<line>[-<line>]` lines")
    p_flags.add_argument("--plan-threads", help="`<path>:<line>[-<line>] — <deviation>` lines")
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand != "flags":
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    if not re.fullmatch(_HEX40, args.head):
        parser.error("--head must be a 40-hex commit sha")
    payload, notices = run_flags(
        args.root, args.pr_body, args.assess, args.threads, args.base_ref, args.head, args.scratch,
        security_sites=args.security_sites, plan_threads=args.plan_threads,
    )
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
