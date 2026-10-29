#!/usr/bin/env python3
"""review_threads.py — the PR review threads the evaluator gates approval on (#145), read once for
each of the three preps that need them (the evaluator's, the resolver's, the review tool's).

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
like any other. A comment's trust comes from its author's association. A trusted marked comment also
needs its author to be this viewer or to hold a resolution-grade permission, because a marked reply
moves the reference commit a resolution is judged against (SEC-3); anyone else's is read as their human
text. A resolution's comes from the
resolving account's repo permission (`pipelib.trust.is_trusted_permission`), because GitHub carries
no association for it, and the viewer's own resolution needs no lookup: the pipeline never resolves
or unresolves a thread, so this account's resolution is a human's. An unknown association or
permission is untrusted, as SEC-1 rules. A **decision reply** (#169) — a reply whose stamp carries
`decision accept|changes|dismiss`, written by `gh_persist.py review-reply --decision` to record the
operator's consent-card answer — counts only from this viewer, and only while it is the thread's latest
trusted comment (`annotate_decisions`, `standing_decision`): the operator's word without a resolution.

**Hunk unchanged** is a script `git diff -U0`, never GitHub's `isOutdated`, whose meaning is
undocumented and which would mark a thread the resolver fixed and a human then resolved as superseded.
Every case the diff cannot answer (a reference commit missing locally, a path absent at the reference
or at the judged head that existed when the thread was opened) counts as changed: an accepted thread re-threads, it never wrongly counts
as accepted, and an unreviewed pipeline thread gets a successor (#169). The reference is the head on the thread's latest marked comment that counts as the pipeline's
(this viewer's or a resolution-grade author's, SEC-3), which is the code the
human last heard about before resolving only because the pipeline never replies in a resolved thread
(`gh_persist.py review-reply` refuses one); GitHub records no resolution time to check it against.

`read_threads` and `confirm_dismissals` are composed in-process by `prep_evaluator` and `prep_resolver`
(`facts.review_threads`, `facts.security.entries[].thread`), and `read_threads` alone by `prep_review` (its
`items`) and by this script's own `pause` subcommand (`run_pause`, whose posting decisions rest on it). Their
failures are notices
(`REVIEW_THREADS_UNAVAILABLE`, `REVIEW_THREADS_TRUNCATED`, `THREAD_PERMISSION_UNAVAILABLE`), never a
decision code. The `flags` subcommand (evaluator S4.6) decides which code gets a new thread: the pure
`build_flags` over parsed inputs, behind a thin git-reading wrapper; it is always `ok`. Before any flag is
built it narrows the out-of-plan list (#163): a file the plan named by a unique basename (never one naming a
root-level entry, which is a literal path), or one the repo marks
`linguist-generated` at both the merge base and the gated head, gets no out-of-plan thread. The `narrow`
subcommand runs the same narrowing on its own, so S4.6 can stage the list a later step reads. The files left
that the out-of-plan triage calls routine (`--triage`) share one **batch** thread, one `routine:<path>` key per
member, re-cut through a `supersedes` successor as members join or leave (`--splits` carries the operator's
split requests); a successor-named thread it replaced reads `replaced` and gates nothing. Neither touches
`blast_radius.out_of_plan` or the review-level raise it drives. The `pause` subcommand (#170) is the
resolver's: at a review pause it reads the threads at the pushed head and runs the same flags over the phase's
signals, anchored on the PR's whole diff, writing a spec whose `phase` the review and every root are stamped
with (an optional ` · phase <N>` stamp field; a stamp without it is byte-identical). Every `gh` and `git` call goes through
`pipelib.process.run`.
"""

import argparse
import json
import os
import posixpath
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse  # noqa: E402
import sensitive_paths  # noqa: E402
from pipelib import process, trust  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402

MARKER = "<!-- review-thread:v1 -->"

# The closed state set, in the contract's table order. `operator-thread` outranks `unreviewed` and
# `changes-requested`; a standing decision reply outranks it (#169); the derivation's precedence is
# `derive_state`'s. `accepted` / `superseded` follow a trusted resolution or the viewer's standing `accept` /
# `dismiss` decision reply, and an unresolved pipeline thread whose hunk moved is `superseded` too.
THREAD_STATES = ("unreviewed", "accepted", "changes-requested", "operator-thread", "superseded")

# The closed set a decision reply's stamp carries (#169): the operator's answer on a consent card, recorded
# by `review-reply --decision`. Only the viewer's trusted, marked reply carries one (`annotate_decisions`).
DECISIONS = ("accept", "changes", "dismiss")

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
    r" · flags (?P<flags>.*?)(?: · supersedes (?P<supersedes>F[1-9][0-9]*|https://\S+))?"
    r"(?: · phase (?P<phase>[1-9][0-9]*))? -->$"
)
_REPLY_STAMP_RE = re.compile(
    r"^<!-- reply(?: F(?P<f>[1-9][0-9]*))? · head (?P<head>" + _HEX40 + r")"
    r"(?: · decision (?P<decision>" + "|".join(DECISIONS) + r"))? -->$"
)
_REVIEW_STAMP_RE = re.compile(r"^<!-- review · head (?P<head>" + _HEX40 + r")(?: · phase (?P<phase>[1-9][0-9]*))? -->$")
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


def _check_phase(phase):
    if phase is not None and (not _is_int(phase) or phase < 1):
        raise ValueError("phase must be a positive int, got %r" % (phase,))


def stamp_root(f, head, path, subject, start, end, flags, body, supersedes=None, phase=None):
    """The full text of a pipeline thread's root comment: the marker, the stamp line, the visible
    `**F<n>**` heading, then the model-written ``body``. ``subject`` is ``"line"`` (``start``..``end``)
    or ``"file"``. ``phase`` (#170) is the review pause that posted it, stamped last as ` · phase <N>`;
    without it the stamp is byte-identical to the evaluator's. Raises ``ValueError`` — never ``TypeError`` — on a value the stamp grammar cannot
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
    _check_phase(phase)
    if phase is not None:
        stamp += " · phase %d" % phase
    stamp += " -->"
    return "%s\n%s\n**F%d**\n\n%s" % (MARKER, stamp, f, body)


def stamp_reply(head, body, f=None, decision=None):
    """The full text of a pipeline reply in a thread: the marker, the reply stamp (`reply F<n>`, or a
    bare `reply` when the thread has no `F` id, then ` · decision <d>` on a decision reply, #169), then
    ``body``. With no ``decision`` the stamp is today's, byte for byte. Raises ``ValueError`` only."""
    if not (isinstance(head, str) and re.fullmatch(_HEX40, head)):
        raise ValueError("head must be a 40-hex commit sha, got %r" % (head,))
    if f is not None and (not _is_int(f) or f < 1):
        raise ValueError("F id must be a positive int, got %r" % (f,))
    if decision is not None and decision not in DECISIONS:
        raise ValueError("decision must be one of %s, got %r" % ("/".join(DECISIONS), decision))
    stamp = "<!-- reply%s · head %s%s -->" % (
        " F%d" % f if f is not None else "", head, " · decision %s" % decision if decision is not None else "")
    return "%s\n%s\n\n%s" % (MARKER, stamp, body)


def stamp_review(head, body, phase=None):
    """The full text of the review body that carries the threads: the marker, the review stamp (with
    ` · phase <N>` on a resolver review pause's, #170), then ``body``. Raises ``ValueError`` only."""
    if not (isinstance(head, str) and re.fullmatch(_HEX40, head)):
        raise ValueError("head must be a 40-hex commit sha, got %r" % (head,))
    _check_phase(phase)
    return "%s\n<!-- review · head %s%s -->\n\n%s" % (
        MARKER, head, " · phase %d" % phase if phase is not None else "", body)


def is_marked(body):
    """Whether ``body`` opens with the marker line (leading whitespace ignored)."""
    return isinstance(body, str) and body.lstrip().startswith(MARKER)


def parse_stamp(body):
    """The stamp of a marked comment: ``{"kind": "root", f, head, path, subject, range, flags,
    supersedes, phase}``, ``{"kind": "reply", f, head, decision}`` (``decision`` ``None`` on a plain reply) or
    ``{"kind": "review", head, phase}`` (``phase`` an int on a review pause's stamps, #170, else ``None``);
    ``None`` for an unmarked body or a second line the grammar does not read. Never raises."""
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
            "phase": int(match.group("phase")) if match.group("phase") else None,
        }
    match = _REPLY_STAMP_RE.match(line)
    if match:
        return {"kind": "reply", "f": int(match.group("f")) if match.group("f") else None, "head": match.group("head"),
                "decision": match.group("decision")}
    match = _REVIEW_STAMP_RE.match(line)
    if match:
        return {"kind": "review", "head": match.group("head"),
                "phase": int(match.group("phase")) if match.group("phase") else None}
    return None


# A security thread's dismissal reply leads with this word (SEC-1): any other trusted reply, a fix request
# included, is never read as a dismissal. Case-insensitive; the reason is the text after it.
DISMISS_PREFIX = "dismiss:"
# The line a pipeline security thread's root carries, the finding's claim verbatim from the record, which
# binds a thread-cited dismissal to the one finding the thread was posted for (SEC-1).
_CLAIM_LINE_RE = re.compile(r"^\s*Claim:\s*(\S.*?)\s*$", re.MULTILINE)


def dismissal_reason(reply):
    """The reason of a dismissal reply (`dismiss: <reason>`), normalised by :func:`reason_line`; ``None``
    when ``reply`` is ``None``, does not lead with :data:`DISMISS_PREFIX`, or gives no reason."""
    line = reason_line(reply)
    if not line or not line.lower().startswith(DISMISS_PREFIX):
        return None
    return line[len(DISMISS_PREFIX):].strip() or None


def decision_text(body):
    """The text of a stamped comment below its stamp line, stripped: what the operator's decision reply
    says after the marker and the stamp (`changes: <notes>`, `dismiss: <reason>`). ``None`` for a body with
    no readable stamp, or nothing under it."""
    if parse_stamp(body) is None:
        return None
    rest = body.lstrip().split("\n", 2)
    return (rest[2].strip() if len(rest) > 2 else "") or None


def _decision_reason(body, decision):
    """The text after `<decision>:` in a decision reply, normalised by :func:`reason_line`; ``None`` when
    the visible text does not lead with it or gives nothing after it."""
    line = reason_line(decision_text(body))
    prefix = decision + ":"
    if not line or not line.lower().startswith(prefix):
        return None
    return line[len(prefix):].strip() or None


def root_claim(body):
    """The first `Claim: <claim>` line of a pipeline thread root's body, normalised; ``None`` when absent."""
    match = _CLAIM_LINE_RE.search(body or "")
    return reason_line(match.group(1)) if match else None


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


def annotate_decisions(comments, viewer):
    """A new list of ``comments`` (:func:`thread_comments` output), each with ``decision`` set: its reply
    stamp's decision on a trusted, marked comment whose author is ``viewer``, else ``None`` (#169). The one
    place a comment's decision is set: another account's decision stamp, a resolution-grade one included, is a
    plain marked reply, and a decision word in visible text is never read."""
    out = []
    for comment in comments:
        stamp = comment.get("stamp") or {}
        decision = None
        if (comment.get("trusted") and comment.get("marked") and viewer and comment.get("author") == viewer
                and stamp.get("kind") == "reply" and stamp.get("decision") in DECISIONS):
            decision = stamp["decision"]
        out.append(dict(comment, decision=decision))
    return out


def standing_decision(comments):
    """The thread's standing decision: the ``decision`` of its latest trusted comment (``None`` when that
    comment carries none). Any later trusted comment, a human's or another marked reply, ends a decision.
    ``comments`` are :func:`annotate_decisions` output. Pure."""
    counted = [c for c in comments if c.get("trusted")]
    return counted[-1].get("decision") if counted else None


def derive_state(comments, root_origin, resolved_trusted, hunk_changed_fn):
    """One thread's state over :data:`THREAD_STATES`. Pure.

    ``comments`` are the thread's comments in order, each ``{"marked": bool, "trusted": bool}`` and
    optionally ``"decision"`` (set only by :func:`annotate_decisions`); ``root_origin`` is ``"pipeline"`` (a
    root marked by the viewer or a resolution-grade author, SEC-3) or ``"operator"`` (any other trusted root);
    ``marked`` is already false on a comment whose author could not mark it; ``resolved_trusted`` is
    :func:`resolution_trusted`'s answer — an untrusted resolution reads as unresolved; ``hunk_changed_fn`` is
    called only for a trusted resolution, a standing `accept` / `dismiss` decision, and an unreviewed
    pipeline thread, so a `changes-requested` or `operator-thread` thread costs no `git` call. Untrusted
    comments are ignored, marked or not. Returns ``(state, pending_reply)``.

    Precedence, first match wins (#169): a trusted resolution is ``accepted`` with the hunk unchanged, else
    ``superseded``; a standing `accept` / `dismiss` decision is ``accepted`` with the hunk unchanged since
    the decision reply (the reference commit), else ``superseded``; a standing `changes` decision is
    ``changes-requested``; unresolved, an operator-rooted thread is ``operator-thread`` (``pending_reply``
    while the latest trusted comment is a marked one); a trusted human comment after the latest trusted
    marked comment is ``changes-requested``; a pipeline thread is ``superseded`` when its hunk changed since
    the reference commit, else ``unreviewed``.
    """
    if resolved_trusted:
        return ("superseded" if hunk_changed_fn() else "accepted"), False
    decision = standing_decision(comments)
    if decision in ("accept", "dismiss"):
        return ("superseded" if hunk_changed_fn() else "accepted"), False
    if decision == "changes":
        return "changes-requested", False
    counted = [c for c in comments if c.get("trusted")]
    if root_origin == "operator":
        return "operator-thread", bool(counted) and bool(counted[-1].get("marked"))
    last_marked = max((i for i, c in enumerate(counted) if c.get("marked")), default=-1)
    if any(not c.get("marked") for c in counted[last_marked + 1:]):
        return "changes-requested", False
    return ("superseded" if hunk_changed_fn() else "unreviewed"), False


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


def thread_comments(node):
    """The thread's comments in order: its root, then its latest comments, the root not repeated. Public:
    `gh_persist.py review-reply` reads its pre-read thread through it, so the op and the reader agree."""
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


def _demote_unmarkable(comment, viewer, permissions):
    """A trusted marked comment counts as the pipeline's only when this viewer wrote it, or an account
    whose repo permission could resolve the thread (SEC-3): a marked reply moves the reference commit a
    resolution is judged against, so an author trusted to comment but not to resolve (a `triage`
    collaborator, a `read` org member) could otherwise re-base a write-holder's resolution onto code no
    one reviewed. Anyone else's marked comment is read as their human text, never dropped."""
    if not comment["marked"] or not comment["trusted"] or _may_mark(comment["author"], viewer, permissions):
        return comment
    return dict(comment, marked=False, stamp=None)


def _may_mark(author, viewer, permissions):
    """Whether ``author``'s marked comment or review body can be the pipeline's: this viewer's, or an
    account whose repo permission is resolution-grade (SEC-3)."""
    return bool(viewer and author == viewer) or trust.is_trusted_permission((permissions or {}).get(author))


def _anchored_hunk_changed(root, root_commit, reference, head, path, rng, subject):
    """§6's check for one thread: its anchor mapped from the root's commit to the reference commit, then
    any hunk from there to ``head`` intersecting it. Run only where `derive_state` asks (a trusted
    resolution, a standing `accept` / `dismiss` decision, an unreviewed pipeline thread, #169), so a
    `changes-requested` or `operator-thread` thread costs no `git` call."""
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
    HEAD, the review tool's PR head (`review.head`). On a read that succeeds, ``threads_path`` (each listed thread with its trusted comments) and
    ``conversation_path`` (trusted top-level comments and review bodies) are written under
    ``scratch_dir``; a failed read writes neither and reports both as ``None``. A failed
    or partial read is ``available: false`` with `REVIEW_THREADS_UNAVAILABLE`; a gate reading it must
    not approve. The payload shape is the contract's (`skills/_shared/review-threads.md`). Every listed
    thread carries ``superseded_by`` (the trusted successor naming it, which decides chain heads) and
    ``replaced`` (whether that successor replaced it, so it gates nothing — contract §4, #163); a gate reads
    ``replaced``, never ``superseded_by``.
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
    logins = set()  # the accounts whose repo permission decides a resolution
    markers = set()  # the accounts whose repo permission decides whether their marked comment is the pipeline's
    for node in thread_nodes:
        comments = thread_comments(node)
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
            logins.add(resolved_by)
        # A deleted account's comment has no author: it can never be the viewer's, and has nothing to look up.
        markers.update(c["author"] for c in comments
                       if c["trusted"] and c["marked"] and c["author"] and c["author"] != viewer)
        # On a security thread a human reply can dismiss the finding, which only a resolution-grade author
        # may do (SEC-4): look up every trusted human on one. A failed lookup only means they cannot dismiss.
        root_stamp = comments[0]["stamp"]
        if root_stamp is not None and any(k.startswith("security:") for k in root_stamp.get("flags") or []):
            logins.update(c["author"] for c in comments
                          if c["trusted"] and not c["marked"] and c["author"] and c["author"] != viewer)
        raw.append((node, comments, resolved_by))

    for node in _nodes((pr_node or {}).get("reviews")) or []:
        if (node.get("state") != "PENDING" and trust.is_trusted(node.get("authorAssociation"))
                and (parse_stamp(node.get("body") or "") or {}).get("kind") == "review"
                and _login(node) and _login(node) != viewer):
            markers.add(_login(node))

    permissions, failed = _read_permissions(repo, logins | markers, cwd, env)
    for login in failed:
        if login in markers:
            # Their marked comments would read as a human's: their roots would lose their F ids (reused
            # by the next thread) and their chain heads (so `flags` posts every signal again). A read
            # that cannot say whose threads are the pipeline's gates nothing and posts nothing.
            notices.append(
                "%s: the repo permission of %s, who wrote marked review-thread comments on #%s, could not be "
                "read (%s); nothing gates on the threads this run, and the evaluator must not approve"
                % (REVIEW_THREADS_UNAVAILABLE, login, pr, THREAD_PERMISSION_UNAVAILABLE)
            )
            return _empty_payload(head), notices
        notices.append(
            "%s: the repo permission of %s could not be read; their resolution, and any `dismiss:` reply of "
            "theirs, counts as untrusted" % (THREAD_PERMISSION_UNAVAILABLE, login)
        )

    threads, stored, extras = [], [], []
    for node, comments, resolved_by in raw:
        comments = annotate_decisions([_demote_unmarkable(c, viewer, permissions) for c in comments], viewer)
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
        members = batch_members(root_stamp["flags"]) if root_stamp is not None else []
        if members:
            # A batch (#163) is accepted only while every member is unchanged: a new hunk in any member after
            # the resolution supersedes it, as one file thread per member would have been.
            hunk_check = lambda: any(  # noqa: E731
                _anchored_hunk_changed(root, root_commit, reference, head, member, None, "file") for member in members
            )
        else:
            hunk_check = lambda: _anchored_hunk_changed(  # noqa: E731
                root, root_commit, reference, head, path, rng, check_subject)
        state, pending_reply = derive_state(comments, origin, trusted_resolution, hunk_check)
        # The latest trusted human comment after the pipeline's last counted marked one — on an
        # operator-rooted thread with no reply, its root. A comment the pipeline has since answered is not
        # the human's last word, so it is never read as a reply (nor as a dismissal's reason).
        counted = [c for c in comments if c["trusted"]]
        last_marked = max((i for i, c in enumerate(counted) if c["marked"]), default=-1)
        humans = [c for c in counted[last_marked + 1:] if not c["marked"]]
        reply = humans[-1]["body"] if humans else None
        # Only a resolution-grade author's (or the viewer's) reply dismisses a security finding (SEC-4); anyone
        # else's still reads as a reply — a fix request gates either way.
        dismiss = dismissal_reason(reply) if humans and _may_mark(humans[-1]["author"], viewer, permissions) else None
        reply_reason = reason_line(reply)
        # A standing decision reply (#169) is the operator's last word: a `changes` decision's notes are the
        # reply, a `dismiss` decision's reason the dismissal, and an `accept` carries neither.
        decision = standing_decision(comments)
        if decision is not None:
            decided = counted[-1]["body"]
            reply_reason = _decision_reason(decided, "changes") if decision == "changes" else None
            dismiss = _decision_reason(decided, "dismiss") if decision == "dismiss" else None
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
            # The review pause that posted it (#170): `None` on an evaluator thread and an operator one.
            "phase": root_stamp.get("phase") if root_stamp is not None else None,
            "superseded_by": None,
            "reply_reason": reply_reason,
            "dismiss_reason": dismiss,
            # A pipeline security thread's `Claim:` line (SEC-1); `None` on an operator thread, which the
            # pipeline did not write.
            "claim": root_claim(root_comment["body"]) if root_stamp is not None else None,
            "_created": root_comment["createdAt"] or "",
        }
        threads.append(entry)
        stored.append(
            [
                {k: c[k] for k in ("author", "body", "createdAt", "url", "marked", "decision")}
                for c in comments if c["trusted"]
            ]
        )

    operator_threads = sorted((t for t in threads if t["origin"] == "operator"), key=lambda t: t["_created"])
    for ordinal, entry in enumerate(operator_threads, start=1):
        entry["o"] = ordinal
    # One F id can name two threads (a review re-posted after a lost response): a successor naming it
    # supersedes every copy, so none is left a gating chain head.
    # A root demoted under SEC-3 keys by its stamp's F too: a successor posted while its author could still
    # mark names it by that id, and its id is never reused. A demoted root supersedes nothing (its `f` is None).
    by_f = {}
    for t, c in zip(threads, stored):
        f = t["f"] if t["f"] is not None else _root_stamp_f({"comments": c}) or None
        if f is not None:
            by_f.setdefault(f, []).append(t)
    by_url = {t["url"]: t for t in threads if t["url"]}
    successor_at = {}  # id(target) -> the earliest root `createdAt` among the successors naming it
    for entry in threads:
        ref = entry["supersedes"]
        if not ref or entry["f"] is None:
            continue
        targets = by_f.get(int(ref[1:]), []) if ref.startswith("F") else [by_url[ref]] if ref in by_url else []
        for target in targets:
            if target is not entry:
                target["superseded_by"] = "F%d" % entry["f"]
                at = successor_at.get(id(target))
                if at is None or entry["_created"] < at:
                    successor_at[id(target)] = entry["_created"]
    # What a successor replaces (#163, contract §4): a successor carries keys or members, never a person's
    # words. A named thread is `replaced` (gates nothing) when it is `superseded`, or when it is a batch in
    # `unreviewed` / `changes-requested` and no trusted human comment, nor the viewer's decision reply (#169), on
    # it postdates its earliest successor's root. Every other thread gates by its state, named or not — a newer human request is never dropped.
    for entry, comments in zip(threads, stored):
        at = successor_at.get(id(entry))
        replaced = False
        if entry["superseded_by"] is not None and at is not None:
            if entry["state"] == "superseded":
                replaced = True
            elif entry["state"] in ("unreviewed", "changes-requested") and batch_members(entry["flags"]):
                # A comment with no `createdAt` counts as newer: a person's request is never dropped on a gap.
                replaced = not any((not c["marked"] or c["decision"]) and (not c["createdAt"] or c["createdAt"] > at)
                                   for c in comments)
        entry["replaced"] = replaced
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
            marked = (kind == "review" and (parse_stamp(body) or {}).get("kind") == "review"
                      and _may_mark(_login(node), viewer, permissions))
            conversation.append(
                {
                    "kind": kind,
                    "author": _login(node),
                    "body": body,
                    "createdAt": node.get("createdAt"),
                    "url": node.get("url"),
                    # Only a review body can carry the pipeline's stamp (the review that carries its
                    # threads); a top-level comment never does. As in a thread, only this viewer's or a
                    # resolution-grade author's stamp marks it (SEC-3).
                    "marked": marked,
                    # A marked review's pause phase (#170): what a reader selects a pause's review body by.
                    "phase": parse_stamp(body).get("phase") if marked else None,
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
    its state is `accepted` or `superseded` (since #169 a `superseded` thread may never have been accepted —
    an unreviewed pipeline thread whose hunk moved — so the state alone proves no acceptance; with the
    `dismiss_reason` below it does, since such a thread never carries one: it has no trusted human comment
    after the pipeline's last and no decision; an untrusted resolution reads unresolved), its root's `Claim:` line equals
    the entry's claim (so a body line rewritten to describe another finding never borrows this thread), the
    entry's anchor path is the thread's path (so a body
    anchor re-pointed at another file never borrows it either, SEC-5 — a rename after the dismissal
    un-confirms it, and the operator is asked again), and its `dismiss_reason` — the `dismiss: <reason>`
    of the trusted human comment after the pipeline's last one, counted only from the viewer or a
    resolution-grade author (SEC-4) — equals the entry's recorded reason, both normalised by
    :func:`reason_line`; on a standing `dismiss` decision reply it is that reply's reason instead. A fix
    request is never a dismissal, whatever a body line later cites it as (SEC-1). On a read that is not
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
                and thread.get("claim") is not None
                and thread.get("claim") == reason_line(entry.get("claim"))
                and thread.get("path") is not None
                and _entry_path(entry.get("anchor")) == thread.get("path")
                and thread.get("dismiss_reason") is not None
                and thread.get("dismiss_reason") == reason_line(entry.get("reason"))
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

# Cap order, highest first: security > sensitive > judgment > out-of-plan > routine > plan. `routine` is the
# batch (#163): every routine out-of-plan file in one thread, ranked after every per-file out-of-plan thread.
FLAG_KINDS = ("security", "sensitive", "judgment", "out-of-plan", "routine", "plan")
# Kinds whose signal names a file, not a site: they anchor on the whole file and match an existing thread on
# kind and path alone. A file thread's hunk check reads every change to the file, so a hunk pushed anywhere in
# it after the resolution supersedes the thread (SEC-2: an anchor on the first hunk alone let a later hunk in
# the same file pass under a resolved thread).
_FILE_LEVEL_KINDS = ("sensitive", "out-of-plan", "routine")
_SITE_RE = re.compile(r"^(?P<path>.+):(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?$")
_SECURITY_SITE_RE = re.compile(r"^(?P<id>SEC-[1-9][0-9]*)\s+(?P<site>\S.*)$")
_PLAN_THREAD_RE = re.compile(r"^(?P<site>\S.*?)\s+—\s+(?P<text>\S.*)$")
_SPLIT_RE = re.compile(r"^F(?P<f>[1-9][0-9]*)\s+(?P<path>\S.*?)\s*$")


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


def batch_members(keys):
    """The member paths of a batch thread (#163): every key's path when all of ``keys`` are `routine:` keys,
    else ``[]`` (not a batch)."""
    keys = list(keys or [])
    if not keys or any(flag_key_parts(k)[0] != "routine" for k in keys):
        return []
    return [flag_key_parts(k)[1] for k in keys]


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
    that range, else on the whole file; a file-level signal (``rng`` ``None``) anchors on the whole file,
    so its thread's hunk check covers every later change to it."""
    info = files.get(path)
    if info is None:
        return None
    hunks = info.get("hunks") or []
    if rng is not None:
        index = _contained_hunk(hunks, rng)
        if index is not None:
            return {"path": path, "subject": "line", "range": list(rng), "hunk": index}
    return {"path": path, "subject": "file", "range": None, "hunk": None}


def _root_stamp_f(thread):
    """The ``F<n>`` a listed thread's root stamp carries, ``0`` when none: a demoted root's too (SEC-3), so
    its id is never reused even though the thread reads as an operator's (contract §3)."""
    stamp = parse_stamp(((thread.get("comments") or [{}])[0] or {}).get("body") or "")
    return stamp["f"] if stamp is not None and stamp.get("kind") == "root" else 0


def _thread_ref(thread):
    return "F%d" % thread["f"] if thread.get("f") is not None else thread.get("url")


def _head_from_sha(thread):
    """The commit a thread's ``range`` numbers: ``root_commit``, which `read_threads` set to the root
    stamp's head on a pipeline thread and to the root comment's commit on an operator one — a root whose
    stamp its author could not mark with included (SEC-3)."""
    return thread.get("root_commit")


def _batch_anchor(files, aliases, paths):
    """Where a batch thread anchors: a whole-file anchor on the first member, in sorted path order, with
    new-side lines in the gated diff, else on the first member in the diff (a deleted one), else ``None``."""
    present = [p for p in (aliases.get(m, m) for m in sorted(paths)) if p in files]
    pick = ([p for p in present if files[p].get("hunks")] or present or [None])[0]
    return None if pick is None else {"path": pick, "subject": "file", "range": None, "hunk": None}


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
      "rejected": [...], "claims": {sec_id: claim}, "routine": [path], "routine_kinds": {path: kind},
      "splits": [(f, path)], "reasons_trusted": bool}`` — security entries already resolved to a path (a
      model-written site, else the record's anchor path), ``rejected`` carrying input lines that did not
      parse, ``claims`` each security entry's recorded claim, ``routine`` the out-of-plan files the triage
      called routine (#163), ``splits`` the operator's split requests.
    - ``mapper(from_sha, path, rng)`` — ``rng`` at ``from_sha`` mapped to the gated head, ``None`` when
      it cannot be (`map_range_between`).

    Returns the `flags` payload without ``spec_path``: ``new`` (successors first, then new threads in
    cap-priority order, each with its ``f`` and ``claims``), ``matched``, ``rejected``, ``overflow``,
    ``unanchored_security``, ``retired``, ``next_f``. The rules are the contract's
    (`skills/_shared/review-threads.md` §7) and the plan's: grouping by hunk, `THREAD_CAP` with the
    file-collapse and the priority order, idempotence against chain heads, one successor per superseded
    chain head (exempt from the cap).

    **The batch** (#163). Routine files share one file-level thread whose stamp carries one `routine:<path>`
    key per member. A routine flag whose file already has a per-file `out-of-plan:` head stays with it. Each
    batch chain head (every key `routine:`) gets a **target set** by the leave rule — never "its members still
    routine now", which emptied every batch on a run with no plan (R2.17): its members, minus each member that
    leaves, plus (on the first eligible head in `F` order) every fresh routine flag. A member leaves only when
    it takes its own out-of-plan thread this run (it is in this run's out-of-plan signals, a validated split
    line's file included) or its path, followed through a rename, left the gated diff; a member merely no
    longer out of plan stays. An `accepted` batch, or a `changes-requested` one no split line names, is left as
    it is (its routine members are `matched`; a member that left it takes its own thread). An `unreviewed` one
    and a `changes-requested` one a split line names get a successor stamped `supersedes F<m>` carrying exactly
    the target set when that set differs from its members; a `superseded` one always gets one; an empty target
    set retires it (`batch-emptied`). Fresh routine flags no eligible head takes form one new batch: one
    candidate against `THREAD_CAP`, never file-collapsed, one `{"key": "routine", "paths", "reason": "cap"}`
    overflow entry past it. A split line naming a thread that is not a batch chain head, or a path that is not
    its member, is ``rejected`` (`not-a-member`). Every batch entry in ``new`` carries ``members``
    (``[{"path", "kind", "reason"}]``: ``kind`` the member's triage kind this run, ``None`` for a member kept with
    no `routine` verdict; ``reason`` only when ``reasons_trusted``); every other entry ``None``.
    """
    rejected = list(signals.get("rejected") or [])
    claims = dict(signals.get("claims") or {})
    unanchored_security, matched, retired, overflow = [], [], [], []
    reasons = dict(signals.get("reasons") or {})
    routine_kinds = dict(signals.get("routine_kinds") or {})
    reasons_trusted = bool(signals.get("reasons_trusted"))

    def members_of(paths):
        # A member the leave rule keeps with no `routine` verdict this run (no plan, now planned, now exempt)
        # has no kind: the evaluator lists it as unclassified.
        return [{"path": p, "kind": routine_kinds.get(p) if p in routine_set else None,
                 "reason": reasons.get(p) if reasons_trusted else None} for p in sorted(paths)]

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

    # Chain heads: trusted pipeline threads no successor names. A batch head (every key `routine:`) is
    # handled by the batch block below, never by the generic matching or successor loop.
    heads = [t for t in threads if t.get("origin") == "pipeline" and t.get("superseded_by") is None]
    batch_heads = sorted((t for t in heads if batch_members(t.get("flags"))), key=lambda t: t.get("f") or 0)
    batch_ids = {id(t) for t in batch_heads}
    heads = [t for t in heads if id(t) not in batch_ids]
    gating_superseded = [t for t in threads if t.get("state") == "superseded" and t.get("superseded_by") is None
                         and id(t) not in batch_ids]

    # Split lines (#163): each must name a batch chain head and one of its members.
    by_f = {t.get("f"): t for t in batch_heads if t.get("f") is not None}
    split_for = {}
    for f, path in signals.get("splits") or []:
        head = by_f.get(f)
        if head is None or path not in batch_members(head.get("flags")):
            rejected.append({"source": "splits", "line": "F%d %s" % (f, path), "reason": "not-a-member"})
            continue
        split_for.setdefault(f, []).append(path)
    split_paths = {p for paths in split_for.values() for p in paths}
    # A file the operator split out of its batch takes its own out-of-plan thread, routine now or not, while it
    # is in the diff (a rejected line moves nothing).
    out_of_plan_paths = list(signals.get("out_of_plan") or [])
    out_of_plan_paths += sorted(p for p in split_paths
                                if p not in out_of_plan_paths and aliases.get(p, p) in files)
    routine_paths = [p for p in signals.get("routine") or [] if p not in split_paths]
    routine_set = set(routine_paths)

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
    for path in out_of_plan_paths:
        add("out-of-plan", "out-of-plan:%s" % path, path, None)
    for path, rng, text in signals.get("plan") or []:
        add("plan", "plan:%s" % path, path, rng, text=text, source="plan-threads")
    for path in routine_paths:
        add("routine", "routine:%s" % path, path, None)

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
            # A routine file that already has its own out-of-plan thread stays with it, never joins a batch.
            if k_kind != kind and not (kind == "routine" and k_kind == "out-of-plan"):
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

    fresh, routine_open = [], []
    for flag in flags:
        hits = [t for t in heads if matches(flag, t)]
        if not hits:
            (routine_open if flag["kind"] == "routine" else fresh).append(flag)
            continue
        live = [t for t in hits if t.get("state") != "superseded"]
        if live:
            matched.append({"key": flag["key"], "thread": _thread_ref(live[0])})
        elif aliases.get(hits[0].get("path"), hits[0].get("path")) not in files:
            # The head's successor cannot be posted, so the flag takes its own thread — a routine one in the batch.
            (routine_open if flag["kind"] == "routine" else fresh).append(flag)
        elif flag["kind"] == "routine":
            # Its file's own superseded out-of-plan head gets a successor carrying `out-of-plan:<path>`: the
            # routine key never rides it, so a batch stays a thread whose keys are all `routine:`.
            matched.append({"key": flag["key"], "thread": _thread_ref(hits[0]), "rides_successor": True})
        else:
            riders[id(hits[0])].append(flag["key"])
            matched.append({"key": flag["key"], "thread": _thread_ref(hits[0]), "rides_successor": True})

    # The batch (#163). A member leaves its batch only when it takes its own out-of-plan thread this run (it is
    # in this run's out-of-plan signals: split out, reclassified consequential, omitted or unparsed by the
    # verdict, flagged, or no readable verdict) or its path, followed through a rename, left the gated diff. A
    # member merely no longer out of plan (no plan, now planned, now exempt) stays: it still waits on a
    # person's acknowledgement, so an absent signal never empties a batch (R2.17).
    routine_now = {f["path"]: f for f in routine_open}
    leaving = set(out_of_plan_paths)

    def stays(member):
        return member not in leaving and aliases.get(member, member) in files

    carried, assigned, eligible = set(), set(), []
    for head in batch_heads:
        members = batch_members(head.get("flags"))
        carried.update(members)
        state = head.get("state")
        if state in ("unreviewed", "superseded") or (state == "changes-requested" and head.get("f") in split_for):
            eligible.append((head, [m for m in members if stays(m)]))
            continue
        # An `accepted` batch, or a `changes-requested` one no split line names, is left as it is.
        for m in members:
            if m in routine_now and m not in assigned:
                assigned.add(m)
                matched.append({"key": "routine:%s" % m, "thread": _thread_ref(head)})
    fresh_routine = [routine_now[p] for p in routine_now if p not in carried]
    batch_successors = []
    for index, (head, kept) in enumerate(eligible):
        target = sorted(set(kept) | ({f["path"] for f in fresh_routine} if index == 0 else set()))
        ref = _thread_ref(head)
        if head.get("state") != "superseded" and set(target) == set(batch_members(head.get("flags"))):
            for m in target:
                if m in routine_now and m not in assigned:
                    assigned.add(m)
                    matched.append({"key": "routine:%s" % m, "thread": ref})
            continue
        if not target:
            retired.append({"thread": head.get("id"), "ref": ref, "reason": "batch-emptied"})
            continue
        # Every target member is in the gated diff, so the batch always has an anchor.
        anchor = _batch_anchor(files, aliases, target)
        batch_successors.append({
            "anchor": anchor, "flags": ["routine:%s" % p for p in target], "supersedes": ref,
            "predecessor": {"ref": ref, "url": head.get("url"), "first_line": None}, "members": members_of(target),
        })
    if eligible:
        fresh_routine = []

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
            # A demoted root (SEC-3) still opens with the marker and a stamp: quote its text, not those.
            first_line = next((ln.strip() for ln in body.splitlines()
                               if ln.strip() and not ln.strip().startswith("<!--")
                               and not re.fullmatch(r"\*\*F[1-9][0-9]*\*\*", ln.strip())), None)
        successors.append({
            "anchor": anchor,
            "flags": keys if thread.get("origin") == "pipeline" else [],
            "supersedes": ref,
            "predecessor": {"ref": ref, "url": thread.get("url"), "first_line": first_line},
        })

    successors.extend(batch_successors)

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
    if fresh_routine:
        paths = [f["path"] for f in fresh_routine]
        anchor = _batch_anchor(files, aliases, paths)
        candidates.append({"path": anchor["path"], "subject": "file", "range": None, "batch": True,
                           "flags": sorted(fresh_routine, key=lambda f: f["path"])})

    def rank(group):
        return min(FLAG_KINDS.index(f["kind"]) for f in group["flags"])

    if len(candidates) > THREAD_CAP:
        by_path = {}
        batch = [g for g in candidates if g.get("batch")]
        candidates = [g for g in candidates if not g.get("batch")]
        for g in candidates:
            by_path.setdefault(g["path"], []).append(g)
        collapsed = []
        for path, items in by_path.items():
            if len(items) == 1:
                collapsed.append(items[0])
            else:
                collapsed.append({"path": path, "subject": "file", "range": None,
                                  "flags": [f for g in items for f in g["flags"]]})
        candidates = collapsed + batch
    candidates.sort(key=rank)
    if len(candidates) > THREAD_CAP:
        for g in candidates[THREAD_CAP:]:
            if g.get("batch"):
                overflow.append({"key": "routine", "paths": [f["path"] for f in g["flags"]], "reason": "cap"})
                continue
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
            "members": succ.get("members"),
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
            "members": members_of([f["path"] for f in g["flags"]]) if g.get("batch") else None,
        })
        f_next += 1
    # Each security thread's root carries its finding's `Claim:` line, the record's claim verbatim (SEC-1).
    for thread in new:
        thread["claims"] = {sec: claims[sec] for sec in thread["security"] if claims.get(sec)}
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


def gather_signals(pr_body, assess, security_site_lines, plan_thread_lines, narrowed=None, triage=None,
                   split_lines=()):
    """``build_flags``'s ``signals`` from the parsed inputs. Pure. A failed sensitive-path read
    (`sensitive.error`) contributes nothing: it has nothing to anchor, and it forces the merge gate to
    `ask` on its own. ``narrowed`` is :func:`narrow`'s result: its ``remaining`` list stands in for the
    assess payload's ``out_of_plan``, so an exempt file gets no out-of-plan flag; ``None`` takes
    ``out_of_plan`` as it is.

    ``triage`` is `parse.scan_out_of_plan_triage`'s result (#163). An out-of-plan path moves to ``routine``
    only when its verdict is `routine` (with its kind, in ``routine_kinds``) and it carries no sensitive,
    security or judgment signal. Everything else stays ``out_of_plan`` — and with no verdict, an absent
    section or an `ambiguous` one, every path does (fail closed). ``split_lines`` are the model-written
    `F<m> <path>` lines (`--splits`): parsed here into ``splits``; :func:`build_flags` checks each against the
    batch threads and moves a valid one's file out of the batch (a rejected line moves nothing). A line that
    does not parse is ``rejected``."""
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
    security, claims = [], {}
    for entry in parse.scan_security(pr_body).get("entries") or []:
        if entry.get("disposition") != "open":
            continue
        anchor_path = _entry_path(entry.get("anchor"))
        if entry["id"] in sites:
            path, rng = sites[entry["id"]]
        else:
            path, rng = anchor_path, None
        security.append((entry["id"], path, rng, anchor_path))
        claims[entry["id"]] = entry.get("claim")
    plan = []
    for line in plan_thread_lines:
        match = _PLAN_THREAD_RE.match(line.strip())
        site = _site(match.group("site")) if match else None
        if not site:
            rejected.append({"source": "plan-threads", "line": line, "reason": "unparsed"})
            continue
        plan.append((site[0], site[1], match.group("text")))
    reasons = {e["path"]: e["reason"] for e in parse.scan_out_of_plan(pr_body).get("entries") or []}
    out_of_plan = (list(narrowed["remaining"]) if narrowed is not None
                   else [p for p in (payload.get("out_of_plan") or []) if isinstance(p, str)])
    splits = []
    for line in split_lines:
        match = _SPLIT_RE.match(line.strip())
        if not match:
            rejected.append({"source": "splits", "line": line, "reason": "unparsed"})
            continue
        splits.append((int(match.group("f")), match.group("path")))
    routine, routine_kinds = [], {}
    verdicts = (triage or {}).get("verdicts") or {}
    if triage and triage.get("present") and not triage.get("ambiguous"):
        flagged = set(sensitive_paths) | {p for _, p, _ in judgment}
        flagged |= {e[1] for e in security} | {e[3] for e in security}
        for path in out_of_plan:
            verdict = verdicts.get(path) or {}
            if (verdict.get("class") == "routine" and verdict.get("kind") in parse.OUT_OF_PLAN_ROUTINE_KINDS
                    and path not in flagged):
                routine.append(path)
                routine_kinds[path] = verdict["kind"]
    return {
        "sensitive": sensitive_paths,
        "out_of_plan": [p for p in out_of_plan if p not in routine_kinds],
        "routine": routine,
        "routine_kinds": routine_kinds,
        "splits": splits,
        "judgment": judgment,
        "security": security,
        "plan": plan,
        "reasons": reasons,
        "claims": claims,
        "rejected": rejected,
    }


# ---------------------------------------------------------------------------
# Narrowing the out-of-plan thread set (#163)
# ---------------------------------------------------------------------------

OUT_OF_PLAN_GENERATED_UNREAD = "OUT_OF_PLAN_GENERATED_UNREAD"
OUT_OF_PLAN_ROOT_UNREAD = "OUT_OF_PLAN_ROOT_UNREAD"
OUT_OF_PLAN_TRIAGE_UNREAD = "OUT_OF_PLAN_TRIAGE_UNREAD"
OUT_OF_PLAN_FILES = "out-of-plan-files.txt"
_GENERATED_VALUES = ("set", "true")


def _bare_spans(planned_spans):
    """The planned spans with no ``/``: the only ones the basename rule reads."""
    return {span for span in (planned_spans or []) if isinstance(span, str) and span and "/" not in span}


def narrow_out_of_plan(out_of_plan, changed_paths, planned_spans, generated, root_files=frozenset()):
    """Which out-of-plan paths still get an out-of-plan thread. Pure. Returns
    ``{"remaining": [path], "exempt": [{"path", "rule": "basename" | "generated"}]}``, both in
    ``out_of_plan`` order.

    - **basename** — ``posixpath.basename(path)`` equals a planned span with no ``/`` (``planned_spans``, the
      assess payload's: `parse.planned_paths` already drops glob spans), and exactly one entry of
      ``changed_paths`` has that basename. Two changed paths sharing it keep every thread. ``planned_spans``
      ``None`` (no plan, or an envelope that predates the key) or no ``changed_paths`` exempts nothing. A
      slash-free span in ``root_files`` (it names an entry, file or directory, at the repo root at the gated head)
      is a literal path, not a basename, so it exempts nothing: a plan naming the root `README.md` does not
      exempt `docs/README.md` [user decision 2026-10-27]. ``root_files`` ``None`` (the root could not be
      listed) exempts nothing by this rule.
    - **generated** — ``path`` is in ``generated`` (the paths marked `linguist-generated` at both the merge base
      and the gated head). ``generated`` ``None`` (a failed read) exempts nothing.

    A file whose basename is ``.gitattributes`` is never exempt by either rule: it decides what the generated
    rule reads, so a change to it always gets its thread. A path both rules exempt is reported under
    ``basename``, the rule checked first.

    This is a thread-side exception to the "literal paths only" planned-span rule (`parse.planned_paths`): it
    changes only how a file's acknowledgement reaches the operator, never `blast_radius.out_of_plan` or the
    raise, so it never goes through `sensitive_paths.pattern_matches`.
    """
    bare = set() if root_files is None else _bare_spans(planned_spans) - set(root_files)
    counts = {}
    for path in changed_paths or []:
        if isinstance(path, str):
            name = posixpath.basename(path)
            counts[name] = counts.get(name, 0) + 1
    remaining, exempt = [], []
    for path in out_of_plan or []:
        name = posixpath.basename(path)
        if name == ".gitattributes":
            remaining.append(path)
        elif name in bare and counts.get(name) == 1:
            exempt.append({"path": path, "rule": "basename"})
        elif generated is not None and path in generated:
            exempt.append({"path": path, "rule": "generated"})
        else:
            remaining.append(path)
    return {"remaining": remaining, "exempt": exempt}


def _generated_paths(root, rev, paths, scratch):
    """The subset of ``paths`` marked `linguist-generated` (value `set` or `true`) by the `.gitattributes` files
    committed at ``rev``, or ``None`` when that cannot be read. The tree is read into a temporary index in
    ``scratch`` and checked with `git check-attr --cached`, so the working tree (a dirty `.gitattributes`
    included) is never consulted, and the user's and system attribute files are switched off
    (`core.attributesFile=/dev/null`, `GIT_ATTR_NOSYSTEM`). The checkout's own `$GIT_DIR/info/attributes` is
    still read: git has no switch for it, and no PR can write it. `GIT_INDEX_FILE` names that index for both
    calls: `read-tree --index-output` would lock the checkout's own index and need the output renameable
    beside it, which a scratch dir on another filesystem is not. `check-attr --source` would avoid the index
    but needs git 2.40, above the documented 2.38 floor."""
    if not paths:
        return set()
    if not rev:
        return None
    try:
        # Absolute: git resolves a relative GIT_INDEX_FILE against the worktree top it runs in, Python against
        # this process's cwd, and the cleanup below must remove the file git wrote.
        out = Path(scratch).resolve()
        out.mkdir(parents=True, exist_ok=True)
        index = out / ("attr-%s.index" % re.sub(r"[^0-9A-Za-z]", "_", rev)[:64])
        if index.exists():
            index.unlink()
    except OSError:
        return None
    env = dict(os.environ, GIT_INDEX_FILE=str(index), GIT_ATTR_NOSYSTEM="1")
    try:
        read = process.run(["git", "read-tree", rev], cwd=root, env=env)
        if read.returncode != 0:
            return None
        check = process.run(["git", "-c", "core.attributesFile=/dev/null", "check-attr", "--cached", "-z", "--stdin",
                             "linguist-generated"],
                            cwd=root, env=env, input_text="".join(p + "\0" for p in paths))
        if check.returncode != 0:
            return None
    finally:
        try:
            index.unlink()
        except OSError:
            pass
    fields = (check.stdout or "").split("\0")
    generated = set()
    for i in range(0, len(fields) - 2, 3):
        path, _, value = fields[i], fields[i + 1], fields[i + 2]
        if value in _GENERATED_VALUES and path in paths:
            generated.add(path)
    return generated


def _root_files(root, rev, names):
    """The subset of ``names`` that are entries (files, directories or submodules) at the root of ``rev``'s
    tree, or ``None`` when the tree cannot be listed. One literal-pathspec `git ls-tree` read."""
    if not names:
        return set()
    listing = process.run(["git", "--literal-pathspecs", "ls-tree", "-z", "--name-only", rev, "--"] + sorted(names),
                          cwd=root)
    if listing.returncode != 0:
        return None
    return {name for name in (listing.stdout or "").split("\0") if name in names}


def narrow(root, assess_payload, head, scratch):
    """The narrowing both `narrow` and `flags` apply, from one assess payload: ``(result, generated_read,
    notices)``, ``result`` as :func:`narrow_out_of_plan` returns it. The generated set is read at the payload's
    merge base (``range.base``) and at ``head``; a failed read on either side (or no merge base) is
    ``generated_read: "failed"`` with the `OUT_OF_PLAN_GENERATED_UNREAD` notice, and the basename rule still
    applies. The slash-free spans naming a root-level file are listed at ``head``; a failed listing is the
    `OUT_OF_PLAN_ROOT_UNREAD` notice and exempts nothing by basename, while the generated rule still applies.
    With nothing out of plan there is nothing to read (``"ok"``)."""
    payload = assess_payload if isinstance(assess_payload, dict) else {}
    out_of_plan = [p for p in (payload.get("out_of_plan") or []) if isinstance(p, str)]
    changed = payload.get("changed_paths")
    spans = payload.get("planned_spans")
    notices, generated, read, root_files = [], set(), "ok", set()
    if out_of_plan:
        root_files = _root_files(root, head, _bare_spans(spans if isinstance(spans, list) else None))
        if root_files is None:
            notices.append("%s: the repository root at the gated head %s could not be listed; no out-of-plan file is "
                           "exempted by basename this run" % (OUT_OF_PLAN_ROOT_UNREAD, head))
        base = (payload.get("range") or {}).get("base")
        at_base = _generated_paths(root, base, out_of_plan, scratch) if base else None
        at_head = _generated_paths(root, head, out_of_plan, scratch) if at_base is not None else None
        if at_base is None or at_head is None:
            generated, read = None, "failed"
            notices.append("%s: the `linguist-generated` attribute could not be read at the merge base %s or the "
                           "gated head %s; no out-of-plan file is exempted as generated this run"
                           % (OUT_OF_PLAN_GENERATED_UNREAD, base or "(none)", head))
        else:
            generated = at_base & at_head
    result = narrow_out_of_plan(out_of_plan, changed if isinstance(changed, list) else None,
                                spans if isinstance(spans, list) else None, generated, root_files=root_files)
    return result, read, notices


def run_narrow(root, assess_path, head, pr_body_path, reasons_trusted, scratch):
    """The `narrow` subcommand (evaluator S4.6, after `assess`): :func:`narrow` plus the out-of-plan file list
    a later step reads, `<scratch>/out-of-plan-files.txt` — one remaining path per line, followed by
    `` — <reason>`` from the PR body's `## Out-of-plan changes` only when ``reasons_trusted`` (the evaluator's
    `facts.out_of_plan.reasons_trusted`, verbatim): a reason an untrusted author wrote never reaches it.
    ``files_path`` is ``None``, and no file is written, when nothing remains. Returns ``(payload, notices)``."""
    assess = _read_json(assess_path)
    payload = assess.get("payload", assess) if isinstance(assess, dict) else {}
    # Read whether or not the reasons are trusted, as `flags` does, so a wrong path fails the same way on every PR.
    try:
        pr_body = Path(pr_body_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (pr_body_path, exc))
        sys.exit(EXIT_USAGE_ERROR)
    result, read, notices = narrow(root, payload, head, scratch)
    files_path = None
    if result["remaining"]:
        reasons = {}
        if reasons_trusted:
            reasons = {e["path"]: e["reason"] for e in parse.scan_out_of_plan(pr_body).get("entries") or []}
        lines = [path + (" — %s" % reasons[path] if reasons.get(path) else "") for path in result["remaining"]]
        out = Path(scratch)
        out.mkdir(parents=True, exist_ok=True)
        target = out / OUT_OF_PLAN_FILES
        target.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        files_path = str(target)
    return {"remaining": result["remaining"], "exempt": result["exempt"], "generated_read": read,
            "files_path": files_path}, notices


def _read_triage(path, notices):
    """The triage verdict file scanned (`parse.scan_out_of_plan_triage`), or ``None`` with no ``path``. An
    unreadable file, an absent `## Triage` section or `code: AMBIGUOUS` batches nothing: each is the
    `OUT_OF_PLAN_TRIAGE_UNREAD` notice, and every out-of-plan file keeps its own thread (fail closed)."""
    if not path:
        return None
    try:
        scan = parse.scan_out_of_plan_triage(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        notices.append("%s: %s could not be read (%s); no file is batched this run"
                       % (OUT_OF_PLAN_TRIAGE_UNREAD, path, exc))
        return None
    if not scan["present"] or scan["ambiguous"]:
        notices.append("%s: %s holds %s; no file is batched this run" % (
            OUT_OF_PLAN_TRIAGE_UNREAD, path, "`code: AMBIGUOUS`" if scan["ambiguous"] else "no `## Triage` section"))
    return scan


def _no_spec(base, **extra):
    """The payload of a run that writes no spec: nothing new, matched, retired or exempt."""
    return dict({"diff_available": False, "spec_path": None, "base": base, "new": [], "matched": [],
                 "rejected": [], "overflow": [], "unanchored_security": [], "retired": [], "exempt": [],
                 "generated_read": None, "next_f": None}, **extra)


def run_flags(root, pr_body_path, assess_path, threads_path, base_ref, head, scratch, security_sites=None,
              plan_threads=None, triage=None, splits=None, reasons_trusted=False, *, threads=None,
              anchor_base=None, anchor_paths=None, phase=None):
    """The `flags` subcommand: read the inputs, run :func:`build_flags`, and write the `review-post`
    spec (`<scratch>/review-spec.json`) naming one body path per new thread and the review body, which
    the model then writes. Returns ``(payload, notices)``.

    The keyword-only arguments are :func:`run_pause`'s (#170): ``threads`` the thread list in place of
    ``threads_path``, ``anchor_base`` the commit the anchor diff starts at in place of the assess payload's
    ``range.base``, with ``anchor_paths`` the paths that range changes — the out-of-plan exemptions read the same
    range, so a PR cannot exempt a file at a later phase's base that it could not at its own (the signals stay
    the payload's) — and ``phase``, which goes into the spec — and with which
    the spec is written even when nothing is new, since every pause posts its review. The `flags` CLI passes
    none of them."""
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
    if threads is None:
        if not threads_path:
            sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: --threads names no file (facts.review_threads.threads_path "
                             "is null: the read failed, and nothing may be posted)\n")
            sys.exit(EXIT_USAGE_ERROR)
        threads = _read_json(threads_path)
    if not isinstance(threads, list):
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s is not a list of threads\n"
                         % (threads_path or "the thread read"))
        sys.exit(EXIT_USAGE_ERROR)
    security_site_lines, plan_thread_lines = _read_lines(security_sites), _read_lines(plan_threads)
    split_lines = _read_lines(splits)
    # The merge base `assess` read the range at. Without it (its fetch or merge-base failed) no range can be
    # trusted to be the PR's: a two-dot diff against the branch tip would anchor on base-only commits.
    base = anchor_base or (payload.get("range") or {}).get("base")
    files = _diff_files(root, base, head) if base else None
    if files is None:
        # Without the diff nothing can be anchored, and no head can be judged to have left it: post nothing,
        # retire nothing, and let the gate hold on the threads as read.
        notices.append("REVIEW_THREADS_DIFF_UNAVAILABLE: the gated diff %s...%s could not be read; nothing is "
                       "posted or retired this run%s"
                       % (base or "origin/%s (no merge base)" % base_ref, head,
                          ", and the evaluator must not approve" if phase is None else ""))
        return _no_spec(base), notices
    narrow_input = payload
    if anchor_base:
        narrow_input = dict(payload, range=dict(payload.get("range") or {}, base=anchor_base))
        if anchor_paths is not None:
            narrow_input["changed_paths"] = list(anchor_paths)
    narrowed, generated_read, narrow_notices = narrow(root, narrow_input, head, scratch)
    notices.extend(narrow_notices)
    signals = gather_signals(pr_body, assess, security_site_lines, plan_thread_lines, narrowed=narrowed,
                             triage=_read_triage(triage, notices), split_lines=split_lines)
    signals["reasons_trusted"] = bool(reasons_trusted)
    next_f = max([0] + [max(t.get("f") or 0, _root_stamp_f(t)) for t in threads if isinstance(t, dict)]) + 1
    result = build_flags(
        files, threads, signals,
        lambda from_sha, path, rng: map_range_between(root, from_sha, head, path, rng) if from_sha else None,
        next_f,
    )
    spec_path = None
    if result["new"] or phase is not None:
        out = Path(scratch)
        out.mkdir(parents=True, exist_ok=True)
        for thread in result["new"]:
            thread["body"] = str(out / ("thread-F%d.md" % thread["f"]))
        spec = {
            "head": head,
            **({"phase": phase} if phase is not None else {}),
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
    result["exempt"] = narrowed["exempt"]
    result["generated_read"] = generated_read
    result["diff_available"] = True
    return result, notices


def _pr_range(root, base_ref, head):
    """``(merge_base, paths, error)``: the PR's whole range at ``head`` — `sensitive_paths.changed_paths` taken
    against ``head`` (never the checkout's ``HEAD``, which a local commit may have moved). Never raises."""
    paths, merge_base, error = sensitive_paths.changed_paths(root, base_ref, head)
    return merge_base, paths, error


def run_pause(root, repo, pr, head, base_ref, phase, assess_path, pr_body_path, scratch, reasons_trusted,
              security_sites=None, plan_threads=None):
    """The `pause` subcommand (#170, `skills/resolver/references/phase-continuation.md` §2.2): the review a
    resolver review pause posts. The threads are read here, at the pushed ``head`` — the resolver's prep read
    them before the push, and a fresh first phase had no PR then. The signals are the phase's own (S5.2's
    shipped-diff ``assess``); the anchors are the PR's whole diff, from the merge base of ``origin/<base_ref>``
    and ``head``, so an earlier pause's thread whose code this phase moved gets a successor rather than
    retiring `path-left-diff`, and a batch member outside this phase stays in its batch. No triage: every
    out-of-plan file keeps its own thread. With the read and the diff available, the spec is always written,
    carrying ``phase``, even when nothing anchors. A failed read writes no spec (`REVIEW_THREADS_UNAVAILABLE`):
    without every thread each flag would look new and reuse a live `F` id. Returns ``(payload, notices)``:
    `flags`' payload plus ``threads_available`` and ``phase``."""
    # The local inputs first, so a wrong path is a usage error before any GitHub read.
    _read_json(assess_path)
    try:
        Path(pr_body_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (pr_body_path, exc))
        sys.exit(EXIT_USAGE_ERROR)
    # Read for their usage errors only; `run_flags` reads them again.
    _read_lines(security_sites)
    _read_lines(plan_threads)
    read, notices = read_threads(repo, pr, root, head, scratch)
    if not read.get("available"):
        return _no_spec(None, diff_available=None, threads_available=False, phase=phase), notices
    merge_base, paths, error = _pr_range(root, base_ref, head)
    if merge_base is None:
        notices.append("REVIEW_THREADS_DIFF_UNAVAILABLE: the PR's diff origin/%s...%s could not be read (%s); "
                       "nothing is posted at this pause" % (base_ref, head, error))
        return _no_spec(None, threads_available=True, phase=phase), notices
    threads = _read_json(read["threads_path"])
    result, flag_notices = run_flags(
        root, pr_body_path, assess_path, None, base_ref, head, scratch, security_sites=security_sites,
        plan_threads=plan_threads, reasons_trusted=reasons_trusted, threads=threads, anchor_base=merge_base,
        anchor_paths=paths, phase=phase,
    )
    notices.extend(flag_notices)
    result["threads_available"] = True
    result["phase"] = phase
    return result, notices


def _positive_int(text):
    if not re.fullmatch(r"[1-9][0-9]*", text or ""):
        raise argparse.ArgumentTypeError("expects a positive integer, got %r" % (text,))
    return int(text)


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
    p_flags.add_argument("--triage",
                         help="the out-of-plan triage's verdict file (<facts.scratch>/out-of-plan-triage.md)")
    p_flags.add_argument("--splits", help="`F<m> <path>` lines: a member the operator split out of batch F<m>")
    p_flags.add_argument("--reasons-trusted", required=True, choices=("true", "false"),
                         help="facts.out_of_plan.reasons_trusted, verbatim")
    p_pause = sub.add_parser("pause", help="the review a resolver review pause posts (#170)")
    p_pause.add_argument("root", help="the resolver's workspace, at the pushed head (facts.workspace.path)")
    p_pause.add_argument("--repo", required=True)
    p_pause.add_argument("--pr", required=True, type=_positive_int)
    p_pause.add_argument("--head", required=True, help="the pushed head's 40-hex sha")
    p_pause.add_argument("--base-ref", required=True, help="facts.workspace.base_ref")
    p_pause.add_argument("--phase", required=True, type=_positive_int, help="the phase this pass shipped")
    p_pause.add_argument("--assess", required=True, help="S5.2's shipped-diff blast_radius.py assess envelope")
    p_pause.add_argument("--pr-body", required=True, help="the body this S5.2's PR-body write posted")
    p_pause.add_argument("--scratch", required=True)
    p_pause.add_argument("--reasons-trusted", required=True, choices=("true", "false"),
                         help="facts.out_of_plan.reasons_trusted (true on a fresh first phase)")
    p_pause.add_argument("--security-sites", help="`SEC-<n> <path>:<line>[-<line>]` lines")
    p_pause.add_argument("--plan-threads", help="`<path>:<line>[-<line>] — <deviation>` lines")
    p_narrow = sub.add_parser("narrow", help="the out-of-plan files left after the exemptions (evaluator S4.6)")
    p_narrow.add_argument("root", help="the checkout at the gated head (facts.workspace.path)")
    p_narrow.add_argument("--assess", required=True, help="the S4.6 blast_radius.py assess envelope")
    p_narrow.add_argument("--head", required=True, help="the gated head's 40-hex sha")
    p_narrow.add_argument("--pr-body", required=True)
    p_narrow.add_argument("--reasons-trusted", required=True, choices=("true", "false"),
                          help="facts.out_of_plan.reasons_trusted, verbatim")
    p_narrow.add_argument("--scratch", required=True)
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand not in ("flags", "narrow", "pause"):
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    if not re.fullmatch(_HEX40, args.head):
        parser.error("--head must be a 40-hex commit sha")
    if args.subcommand == "pause":
        payload, notices = run_pause(
            args.root, args.repo, args.pr, args.head, args.base_ref, args.phase, args.assess, args.pr_body,
            args.scratch, args.reasons_trusted == "true", security_sites=args.security_sites,
            plan_threads=args.plan_threads,
        )
    elif args.subcommand == "narrow":
        payload, notices = run_narrow(args.root, args.assess, args.head, args.pr_body,
                                      args.reasons_trusted == "true", args.scratch)
    else:
        payload, notices = run_flags(
            args.root, args.pr_body, args.assess, args.threads, args.base_ref, args.head, args.scratch,
            security_sites=args.security_sites, plan_threads=args.plan_threads, triage=args.triage,
            splits=args.splits, reasons_trusted=args.reasons_trusted == "true",
        )
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
