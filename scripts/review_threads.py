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
split requests); a successor-named thread it replaced reads `replaced` and gates nothing. A batch of tests and
fixtures is stamped ` · advisory` (#181); a consequential test the triage ties to a `subject` rides that file's
thread; sensitive-only files share one thread per declared pattern (` · group sensitive`). None of it touches
`blast_radius.out_of_plan`. The `pause` subcommand (#170) is the
resolver's: at a review pause it reads the threads at the pushed head and runs the same flags over the phase's
signals bar the out-of-plan one (#218: it posts no out-of-plan or batch thread and leaves those chain heads to
the evaluator), anchored on the PR's whole diff, writing a spec whose `phase` the review and every root are stamped
with (an optional ` · phase <N>` stamp field; a stamp without it is byte-identical). Given the design check's inputs
and `--design-build` (#237), `flags` and `pause` give every design thread in the spec at this run's target a `captures`
field, the record's and the build's capture at their fallback values (a successor keeping an older target carries none). The `captures` subcommand (#237, `skills/_shared/design-check.md`
step 6) reuses what a trusted `<!-- design-captures:v1 -->` comment already holds at the spec's head and target and
stages one comment carrying the rest, which `gh_persist.py comment … --attach` posts; `apply-captures` writes the
receipt's `attachment_urls` into the spec. `review-post` then stamps each design root's captures line and side-by-side
table (`stamp_root`, `parse_captures_line`). The `explain` subcommand (#241, design-check step 3.1) stages the departure
classification's two inputs — the comparison's differences, and the explanations a difference may be classified under
(the PR body's trusted `## Design departures` and `## Decided deviations`, the plan's agreed UI deviations) — and,
given its envelope and the classification (`--design-explanations`, `--design-classification`), `flags` and `pause`
post every explained difference in one **summary** thread per target (`design-departure:<id>` keys, which name no
path, stamped ` · summary <hash>`; its hunk check never runs, since its subject is the explanations its hash pins) and
every unexplained one as a design thread, states on one anchor sharing a ` · group design` thread. The `code-links`
subcommand (#245, `build_code_links`) decides which link each code anchor a decision card's supporting block names
gets — a line-anchored or file-level Files-view link (`diff_link`, whose `diff_anchor` gained optional lines), a link
to the file at the pushed head (`blob_link`), or none, the caller printing the code — so the link kind is a script's
answer, never a prompt's. Every `gh` and `git` call goes through `pipelib.process.run`.
"""

import argparse
import hashlib
import html
import json
import os
import posixpath
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import design_record  # noqa: E402  (`allowed_capture_url`, `CAPTURE_MAX_BYTES`: the capture allowlist and cap, #237)
import parse  # noqa: E402
import rigor  # noqa: E402
import sensitive_paths  # noqa: E402
from pipelib import process, trust  # noqa: E402
from pipelib.limits import ATTACH_IMAGE_TYPES, ATTACH_MAX  # noqa: E402
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

# The closed set a root stamp's ` · group <g>` field carries (#181): a sensitive group, one thread for the
# sensitive-only files whose first declaration entry is one pattern, one `sensitive:<path>` key per member; and a
# design group (#241), one thread for the recorded states with an unexplained difference whose sites resolve to one
# anchor, one `design:<state-id>:<path>` key per member.
GROUPS = ("sensitive", "design")
# The routine-batch member kinds that make a batch **advisory** (#181): a batch whose every member's triage kind
# this run is one of these is stamped ` · advisory`, and while `unreviewed` or `superseded` it gates nothing.
ADVISORY_KINDS = ("test", "fixture")

# The thread template's closing line (#181), the last line of every root `stamp_root` writes: how a person
# answers this kind of thread. The script writes it, so the instruction matches what the reader counts
# (contract §2 "Thread template", §4); `closing_kind` picks the key.
CLOSINGS = {
    "security": "To dismiss this finding, reply `dismiss: <reason>`, then resolve the thread. To ask for a fix, "
                "reply saying what to change and leave it unresolved.",
    # #206: a design thread clears only with a fix the comparison reads as a match, once every difference it lists is
    # explained by a declared departure (#241, `design-explained`: the difference moves to the summary), or once
    # `accepted` after a planner revise adopts a revised design record or withdraws the target (`design_clearance`);
    # resolving it, or accepting it against the record it was compared with, never does. A revise offers the withdrawal only once the record no
    # longer stands (unapproved or deleted, `skills/planner/references/design-detour.md` §2); deleting it is the one
    # step an operator can always take, since a designer revise of an approved record may re-approve it in one write.
    "design": "To clear this difference, fix the build to match the design record; or, if the build is right, reply "
              "asking for a departure: the build session declares it with its reason and the next check moves it to the "
              "summary; or change the target: have the designer revise the record and a planner revise adopt it, or, to "
              "drop the design, delete the design record and answer Plan without the record on the planner revise's "
              "card; then accept the thread. Resolving it alone does not clear it.",
    # #241: the summary of every difference a declared departure or an agreed deviation explains. One approval clears
    # every difference it lists; any change to an explanation re-asks, through a successor.
    "design-summary": "Resolve the thread, or accept it, to approve every departure listed here. Reply naming a "
                      "difference to ask for it to be fixed instead.",
    "group": "Resolve the thread to accept every file in this group, or reply to ask for a change.",
    "batch": "Resolve the thread to accept every file in this batch. Reply naming a file to split it out into "
             "its own thread, or reply to ask for a change.",
    "batch-advisory": "This batch does not hold approval while it waits. Resolve the thread to accept every "
                      "file in it. Reply naming a file to split it out into its own thread, or reply to ask "
                      "for a change, which does hold approval.",
    "general": "Resolve the thread once the code is fine, or reply to ask for a change.",
}

# A multi-file root links at most this many files, one `[View the change](…)` line each, then one
# `LINK_REMAINDER` line to the PR's Files view (#181). Each line is about 100 characters plus the PR URL plus the
# path, so the script's own link text stays near 10,000 characters whatever a batch's, a group's or a subject's
# member count, and a root never fails `review-post`'s size gate on links alone; the gate stays the backstop for
# pathological path lengths. Display only: acceptance still covers every file (`file_check_paths`).
LINK_LINE_CAP = 20
LINK_REMAINDER = "[View all files](%s) — %d more in this thread"

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
# A design thread's ` · target` (#206): the design record's fingerprint (`design_record.FINGERPRINT_LENGTH` hex).
_TARGET_HEX = r"[0-9a-f]{12}"
# A design summary's ` · summary` (#241): the first 12 hex of the SHA-256 over its target and its explanations
# (`summary_hash`), so any change to what it approves re-cuts it.
_SUMMARY_HEX = r"[0-9a-f]{12}"
_ROOT_STAMP_RE = re.compile(
    r"^<!-- F(?P<f>[1-9][0-9]*) · head (?P<head>" + _HEX40 + r") · anchor (?P<anchor>.+?)"
    r" · flags (?P<flags>.*?)(?: · supersedes (?P<supersedes>F[1-9][0-9]*|https://\S+))?"
    r"(?: · group (?P<group>" + "|".join(re.escape(g) for g in GROUPS) + r"))?(?P<advisory> · advisory)?"
    r"(?: · target (?P<target>" + _TARGET_HEX + r"))?(?: · summary (?P<summary>" + _SUMMARY_HEX + r"))?"
    r"(?: · phase (?P<phase>[1-9][0-9]*))? -->$"
)
_REPLY_STAMP_RE = re.compile(
    r"^<!-- reply(?: F(?P<f>[1-9][0-9]*))? · head (?P<head>" + _HEX40 + r")"
    r"(?: · decision (?P<decision>" + "|".join(DECISIONS) + r"))? -->$"
)
# A review stamp's ` · retires` (#206, R1.11): the `F` ids of the threads the run that posted it retired.
_F_REF = r"F[1-9][0-9]*"
_REVIEW_STAMP_RE = re.compile(
    r"^<!-- review · head (?P<head>" + _HEX40 + r")(?: · retires (?P<retires>" + _F_REF + r"(?:," + _F_REF + r")*))?"
    r"(?: · phase (?P<phase>[1-9][0-9]*))? -->$"
)
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


def diff_anchor(path, start=None, end=None):
    """The Files-view fragment GitHub gives a file's diff: `diff-<sha256 hex of the path>` (#155, #181). The
    one copy of the formula: `phase_review.build_links`, the thread template's link and `code-links` all use it.
    With ``start`` (#245) it points at a new-side line, `R<start>`, or a range, `R<start>-R<end>` when ``end`` is
    past ``start``; with no line it is byte-identical to the file-level fragment."""
    anchor = "diff-%s" % hashlib.sha256(path.encode("utf-8")).hexdigest()
    if start is None:
        return anchor
    if end is not None and end > start:
        return "%sR%d-R%d" % (anchor, start, end)
    return "%sR%d" % (anchor, start)


def files_url(pr_url):
    """PR ``pr_url``'s Files view (#181)."""
    return "%s/files" % pr_url.rstrip("/")


def diff_link(pr_url, path, start=None, end=None):
    """The one-click link to ``path``'s diff in PR ``pr_url``'s Files view (#181), line-anchored when ``start``
    is given (#245, :func:`diff_anchor`)."""
    return "%s#%s" % (files_url(pr_url), diff_anchor(path, start, end))


def closing_kind(flags, group=None, advisory=False):
    """The :data:`CLOSINGS` key for a root (#181), the first that applies: any `security:` key → `security`;
    a design summary (every key `design-departure:`, #241) → `design-summary`; any `design:` key → `design` (#206), a
    design group's included; a sensitive group (the stamp's field, its keys confirming it) → `group`; a batch (every
    key `routine:`) → `batch-advisory` when ``advisory``, else `batch`; otherwise `general`. Pure."""
    flags = list(flags or [])
    if any(flag_key_parts(k)[0] == "security" for k in flags):
        return "security"
    if summary_keys(flags):
        return "design-summary"
    if any(flag_key_parts(k)[0] == "design" for k in flags):
        return "design"
    if group is not None and group_members(flags, group):
        return "group"
    if batch_members(flags):
        return "batch-advisory" if advisory else "batch"
    return "general"


# A design thread's captures (#237, `skills/_shared/design-check.md` step 6): the record's capture and the build's,
# each `{kind, ref}`. The record's is a `url` (an attachment URL), a `path` (a repo path, linked at the judged head)
# or `none`; the build's is a `url` once uploaded, else `local` (the absolute path design-check step 2 wrote, which
# only that checkout can open) or `none` (the build does not reach the state). `url` is either side's once its upload
# landed. The root carries them as one script line, `CAPTURES_LINE_PREFIX` …, which a reader parses
# (`parse_captures_line`) and never the table below it.
CAPTURE_SIDES = ("record", "build")
CAPTURE_KINDS = {"record": ("url", "path", "none"), "build": ("url", "local", "none")}
CAPTURES_LINE_PREFIX = "<!-- captures · "
_CAPTURES_LINE_RE = re.compile(
    r"^<!-- captures · record (?P<rk>[a-z]+) (?P<rr>\S+) · build (?P<bk>[a-z]+) (?P<br>\S+) -->$", re.MULTILINE)
CAPTURE_TABLE_HEAD = "| Approved design | Build at `%s` |"


def _lf(text):
    """``text`` with every line ending (CRLF, a lone CR) as ``\n`` — CommonMark's line endings, which a body edited on
    the web carries — so each reader of the #237 grammars (the captures line, the captures comment) and the guard that
    keeps a forged captures line out of a root see the same lines. The text posted is never changed."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")
CAPTURE_CELL_LOCAL = "`%s` — local to the checkout that captured it; not uploaded"
CAPTURE_CELL_NONE = {"record": "no capture", "build": "the build does not reach this state"}
# The comment the upload step posts (#237, `skills/_shared/design-check.md` step 6): the marker, the stamp, one plain
# line, then one entry per capture, every byte script-written. `review_threads.py captures` reads it back to reuse an
# upload at the same head and target; only a trusted author's counts (#179).
CAPTURES_MARKER = "<!-- design-captures:v1 -->"
CAPTURES_STAMP = "<!-- head %s · target %s -->"
_CAPTURES_STAMP_RE = re.compile(r"^<!-- head (?P<head>[0-9a-f]{40}) · target (?P<target>[0-9a-f]{12}) -->$")
CAPTURES_INTRO = ("Captures of the build and the approved design for this pull request's design review threads, "
                  "uploaded by the design check.")
CAPTURES_ENTRY = "- `%s` · %s · ![%s %s](%s)"
# An entry's image is read by `image_ref`, Markdown or `<img>`: gh leaves the Markdown form, and the receipt's reader
# (`gh_persist.attachment_urls`) accepts both, so the re-run reader must too.
_CAPTURES_ENTRY_RE = re.compile(r"^- `(?P<state>[a-z0-9][a-z0-9-]*)` · (?P<side>build|record) · (?P<image>.+)$")
DESIGN_CAPTURES_UNREAD = "DESIGN_CAPTURES_UNREAD"
# Why a capture did not upload (#237): the closed set `captures` and `apply-captures` report, `gh_persist`'s
# `attachment_urls` reasons included.
NOT_UPLOADED_REASONS = ("type", "empty", "too-large", "over-cap", "comments-unread", "not-posted",
                        "not-rewritten", "unreferenced", "ambiguous", "readback-failed")
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_PR_URL_RE = re.compile(r"^(?P<repo_url>https://[^/\s]+/[^/\s]+/[^/\s]+)/pull/[0-9]+$")


# A Markdown image (`![alt](target)`, the target optionally `<…>`-wrapped or followed by a title) and an HTML
# `<img>` (GitHub's own upload shape). Only these two carry an alt text an attachment can be matched by.
_MD_IMAGE_RE = re.compile(r'!\[(?P<alt>[^\]]*)\]\(\s*<?(?P<target>[^)\s>]+)>?(?:\s+"[^"]*")?\s*\)')
_HTML_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
# The attribute name stands alone: `(?<![\w-])` keeps `data-src` / `data-alt` from reading as `src` / `alt`.
_HTML_ATTR_RE = re.compile(r'(?<![\w-])(?P<name>alt|src)\s*=\s*(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\')', re.IGNORECASE)


def image_refs(text):
    """``[(alt, target)]`` for every Markdown image and every HTML ``<img>`` with an ``alt`` and a ``src`` in ``text``
    (#237). The one image reader of a posted body: `gh_persist.attachment_urls` (where each ``--attach`` landed) and
    :func:`landed_captures` (what a captures comment already holds) both use it, so the two never disagree on which
    image form counts. Pure."""
    found = [(m.group("alt"), m.group("target")) for m in _MD_IMAGE_RE.finditer(text or "")]
    for tag in _HTML_IMG_RE.finditer(text or ""):
        attrs = {}
        for a in _HTML_ATTR_RE.finditer(tag.group(0)):
            attrs.setdefault(a.group("name").lower(), html.unescape(a.group("dq") if a.group("dq") is not None
                                                                    else a.group("sq")))
        if "alt" in attrs and "src" in attrs:
            found.append((attrs["alt"], attrs["src"]))
    return found


def image_ref(text):
    """``(alt, target)`` when the stripped ``text`` is exactly one image, Markdown or ``<img>`` (:func:`image_refs`'
    grammar), else ``None``. Pure."""
    stripped = (text or "").strip()
    if _MD_IMAGE_RE.fullmatch(stripped) or _HTML_IMG_RE.fullmatch(stripped):
        refs = image_refs(stripped)
        return refs[0] if len(refs) == 1 else None
    return None


def is_attachment_url(url):
    """Whether ``url`` is a durable GitHub attachment a root may show and a captures entry may count as landed (#237):
    an ``https`` URL :func:`design_record.allowed_capture_url` accepts that is either ``github.com``'s
    ``/user-attachments/…`` form (what ``--attach`` rewrites a reference to, and what a record holds) or the older
    public ``user-images.githubusercontent.com`` host. Any other ``github.com`` path (a PR or blob page) is no image,
    and a signed ``private-user-images`` URL expires, so neither counts. It must also be one a root's Markdown image and
    captures line can carry verbatim — no whitespace, control character, ``(``, ``)``, ``<``, ``>`` or ``|`` — so every
    URL the upload step writes into a spec (:func:`_set_capture`) is one ``review-post`` stamps, and an upload outcome
    can never make it refuse the spec. Pure."""
    if not isinstance(url, str) or re.search(r"[\s()<>|\x00-\x1f\x7f]", url):
        return False
    if not design_record.allowed_capture_url(url):
        return False
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "github.com":
        return parts.path.startswith("/user-attachments/")
    return host == "user-images.githubusercontent.com"


def _check_capture(side, capture):
    """``(kind, ref)`` for one side of a ``captures`` value, or ``ValueError`` on one the line cannot carry: a kind
    outside :data:`CAPTURE_KINDS` for its side; a ``url`` that is no durable attachment (:func:`is_attachment_url`) or
    that holds a character a Markdown image target cannot; a ``path`` that is absolute, climbs out with ``..``,
    or holds a backtick, a ``|`` or a control character; a ``local`` that is not absolute or holds one of those; a
    ``none`` with a ref."""
    if not isinstance(capture, dict):
        raise ValueError("captures.%s must be {kind, ref}, got %r" % (side, capture))
    kind, ref = capture.get("kind"), capture.get("ref")
    if kind not in CAPTURE_KINDS[side]:
        raise ValueError("captures.%s.kind must be one of %s, got %r" % (side, "/".join(CAPTURE_KINDS[side]), kind))
    if kind == "none":
        if ref not in (None, ""):
            raise ValueError("captures.%s: a `none` capture carries no ref, got %r" % (side, ref))
        return kind, None
    if not isinstance(ref, str) or not ref or re.search(r"[\x00-\x1f\x7f]", ref):
        raise ValueError("captures.%s.ref must be text with no control character, got %r" % (side, ref))
    if kind == "url":
        if not is_attachment_url(ref):
            raise ValueError("captures.%s: %r is not an attachment URL a root can show" % (side, ref))
    elif kind == "path":
        parts = ref.split("/")
        if ref.startswith("/") or ".." in parts or "`" in ref or "|" in ref or _URL_SCHEME_RE.match(ref):
            raise ValueError("captures.%s: %r is not a repo path a root can link" % (side, ref))
    else:  # local
        if not ref.startswith("/") or "`" in ref or "|" in ref:
            raise ValueError("captures.%s: %r is not an absolute local path a root can name" % (side, ref))
    return kind, ref


def check_captures(captures, flags):
    """The checked ``captures`` value (#237) for a thread with ``flags``: ``{state, record: (kind, ref), build:
    (kind, ref), caption}``. ``ValueError`` when the thread has no `design:` key, ``state`` is not its key's state, a
    side cannot be carried (:func:`_check_capture`) or ``caption`` is neither ``None`` nor text."""
    if not isinstance(captures, dict):
        raise ValueError("captures must be {state, record, build, caption}, got %r" % (captures,))
    states = [design_key_state(k) for k in flags or [] if isinstance(k, str)]
    states = [st for st in states if st]
    if not states:
        raise ValueError("only a design thread (a `design:` key) carries captures, got %r" % (list(flags or []),))
    if captures.get("state") not in states:
        raise ValueError("captures.state must be the design key's state %s, got %r"
                         % ("/".join(states), captures.get("state")))
    caption = captures.get("caption")
    if caption is not None and not isinstance(caption, str):
        raise ValueError("captures.caption must be text or null, got %r" % (caption,))
    return {"state": captures["state"], "record": _check_capture("record", captures.get("record")),
            "build": _check_capture("build", captures.get("build")), "caption": caption}


def captures_line(record, build):
    """The root's captures line (#237) for two checked ``(kind, ref)`` sides: each ref written
    ``urllib.parse.quote(ref, safe="/:")`` (so a space, ``%``, ``·`` or ``>`` inside one is encoded and the line always
    splits), a bare ``-`` for ``none``. :func:`parse_captures_line` reads it back."""
    def side(value):
        kind, ref = value
        return "%s %s" % (kind, "-" if kind == "none" else urllib.parse.quote(ref, safe="/:"))
    return "%srecord %s · build %s -->" % (CAPTURES_LINE_PREFIX, side(record), side(build))


def parse_captures_line(text):
    """The first captures line in ``text`` (#237), as ``{"record": {kind, ref}, "build": {kind, ref}}`` with each ref
    unquoted (``None`` for ``none``), or ``None`` when ``text`` holds no line in this grammar (a root posted before
    #237, a design successor posted with no comparison, or one at an older target); line endings are read as ``\n``
    (:func:`_lf`). ``stamp_root`` refuses a body holding
    a captures line, so on a root ``review-post`` posted the first match is the script's line. A kind outside its side's set, a ``none`` whose ref is
    not ``-``, or a ref that does not unquote to a value :func:`_check_capture` accepts reads as no line. Pure; the
    caller decides whose text it reads (a counted pipeline root only, phase 2's review tool)."""
    for match in _CAPTURES_LINE_RE.finditer(_lf(text)):
        out = {}
        for side, kind, ref in (("record", match.group("rk"), match.group("rr")),
                                ("build", match.group("bk"), match.group("br"))):
            if kind == "none":
                if ref != "-":
                    return None
                value = {"kind": kind, "ref": None}
            else:
                value = {"kind": kind, "ref": urllib.parse.unquote(ref)}
            try:
                _check_capture(side, value)
            except ValueError:
                return None
            out[side] = value
        return out
    return None


def _caption_text(caption):
    """A comparison difference made safe for a script line (#237): a newline becomes a space, and ``<!--``, ``-->``
    and ``|`` are escaped, so model text can never open or close a comment or forge a script line."""
    text = re.sub(r"\s*[\r\n]+\s*", " ", caption).strip()
    return text.replace("<!--", "&lt;!--").replace("-->", "--&gt;").replace("|", "\\|")


_DIFFERENCE_PREFIX = "**Difference:** "


def parse_captures_caption(text):
    """The difference a design root's script lines carry (#237), as the comparison gave it, or ``None``: read only from
    the `**Difference:**` line :func:`captures_block` writes right after its table — the captures line, the table's
    three lines, one blank line, then that line — never from a `**Difference:**` line further down the body, which the
    model wrote. A root with no caption (a comparison that reported no difference for the state, which a design thread
    rarely is) has none, and there the model body's first line, when it is such a line, is what reads: the same trusted
    root's text, shown as text. The escapes :func:`_caption_text` applied are undone (``|``, then ``-->`` and ``<!--``). Pure; like
    :func:`parse_captures_line`, the caller decides whose text it reads."""
    lines = _lf(text).split("\n")
    for i, line in enumerate(lines):
        if not _CAPTURES_LINE_RE.fullmatch(line):
            continue
        if parse_captures_line(line) is None or len(lines) < i + 6:
            return None
        if not (lines[i + 1].startswith("| Approved design | Build at `") and lines[i + 2] == "| --- | --- |"
                and lines[i + 3].startswith("| ") and lines[i + 4] == ""
                and lines[i + 5].startswith(_DIFFERENCE_PREFIX)):
            return None
        caption = lines[i + 5][len(_DIFFERENCE_PREFIX):]
        return caption.replace("\\|", "|").replace("--&gt;", "-->").replace("&lt;!--", "<!--") or None
    return None


def blob_link(pr_url, head, path, start=None, end=None):
    """The link to repo file ``path`` at commit ``head`` in the repo the PR URL ``pr_url`` names, or ``None`` when
    ``pr_url`` is not a PR URL (#237, #245). With ``start`` it adds `?plain=1#L<start>` (or `#L<start>-L<end>` when
    ``end`` is past ``start``): GitHub renders a Markdown file, where a line fragment scrolls nowhere, and
    `?plain=1` shows any file's source, so it is added for every path, never by extension."""
    match = _PR_URL_RE.match(pr_url or "")
    if not match:
        return None
    url = "%s/blob/%s/%s" % (match.group("repo_url"), head, urllib.parse.quote(path, safe="/"))
    if start is None:
        return url
    if end is not None and end > start:
        return "%s?plain=1#L%d-L%d" % (url, start, end)
    return "%s?plain=1#L%d" % (url, start)


def capture_blob_link(pr_url, head, path):
    """The link to repo file ``path`` at commit ``head`` in the repo the PR URL ``pr_url`` names, or ``None`` when
    ``pr_url`` is not a PR URL (#237): a design root's ``path`` cell and the review tool's capture link alike.
    :func:`blob_link` with no line (#245)."""
    return blob_link(pr_url, head, path)


def captures_block(checked, head, pr_url=None):
    """The script lines a design root carries for its captures (#237): the captures line, a two-column table — the
    record's capture left, the build's at ``head`` right — and, when there is a caption, one `**Difference:**` line.
    A ``url`` cell is an image; a ``path`` cell links the file at ``head`` in the repo ``pr_url`` names (its plain
    path with no ``pr_url``); a ``local`` cell names the path as local and not uploaded; a ``none`` cell says so."""
    state = checked["state"]

    def cell(side):
        kind, ref = checked[side]
        if kind == "url":
            return "![%s %s](%s)" % (side, state, ref)
        if kind == "path":
            link = capture_blob_link(pr_url, head, ref)
            return "[`%s`](%s)" % (ref, link) if link else "`%s`" % ref
        if kind == "local":
            return CAPTURE_CELL_LOCAL % ref
        return CAPTURE_CELL_NONE[side]

    lines = [captures_line(checked["record"], checked["build"]),
             CAPTURE_TABLE_HEAD % head[:7], "| --- | --- |", "| %s | %s |" % (cell("record"), cell("build"))]
    text = "\n".join(lines) + "\n"
    if checked["caption"]:
        text += "\n**Difference:** %s\n" % _caption_text(checked["caption"])
    return text


def stamp_root(f, head, path, subject, start, end, flags, body, supersedes=None, phase=None, group=None,
               advisory=False, pr_url=None, link_paths=None, target=None, captures=None, summary=None):
    """The full text of a pipeline thread's root comment, the thread template's script lines around the
    model-written ``body`` (#181, contract §2 "Thread template"): the marker, the stamp line, the visible
    `**F<n>**` heading, on a ``"file"`` subject with ``pr_url`` the line `[View the change](<diff_link>)` — on a
    multi-file thread one such line per file, its path after it, up to :data:`LINK_LINE_CAP`, then one
    :data:`LINK_REMAINDER` line (``link_paths``, ``flags``' head-name list; when empty, :func:`file_check_paths` of
    the anchor and keys) — then
    ``body``, then the one :data:`CLOSINGS` line `closing_kind` picks. ``subject`` is ``"line"``
    (``start``..``end``) or ``"file"``. ``phase`` (#170) is the review pause that posted it, stamped last as
    ` · phase <N>`; without it the stamp is byte-identical to the evaluator's. ``group`` (#181, one of
    :data:`GROUPS`) stamps ` · group sensitive` and needs every key `sensitive:`; ``advisory`` stamps
    ` · advisory` and needs every key `routine:` (a batch); both sit after ``supersedes`` and before ``phase``,
    and without them the stamp is byte-identical. ``target`` (#206) is a design thread's record fingerprint (12 hex),
    stamped ` · target <fp>` after ``advisory`` and before ``phase``, and needs a `design:` key or every key
    `design-departure:` (a design summary, #241); without it the stamp is byte-identical. ``summary`` (#241) is a design
    summary's hash (12 hex), stamped ` · summary <hash>` after ``target`` and before ``phase``; it needs every key
    `design-departure:` and a ``target``; without it the stamp is byte-identical. ``group`` may also be `design` (#241),
    which needs every key `design:`. ``captures`` (#237) is a design thread's two captures and difference
    (:func:`check_captures`): after the link line and before ``body`` the script writes the captures line, the
    two-column table and the `**Difference:**` line (:func:`captures_block`); without it the root is byte-identical.
    A ``body`` holding a captures line is refused, so a root's only captures line is the script's. Raises ``ValueError``
    — never ``TypeError`` — on a value the stamp grammar cannot carry, wrong types included, so a bad spec fails before
    anything is posted."""
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
    if group is not None:
        if group not in GROUPS:
            raise ValueError("group must be one of %s, got %r" % ("/".join(GROUPS), group))
        if not group_members(flags, group):
            raise ValueError("a %s group needs every flag key `%s:`, got %r" % (group, group, list(flags)))
        stamp += " · group %s" % group
    if not isinstance(advisory, bool):
        raise ValueError("advisory must be a bool, got %r" % (advisory,))
    if advisory:
        if not batch_members(flags):
            raise ValueError("only a batch (every flag key `routine:`) is advisory, got %r" % (list(flags),))
        stamp += " · advisory"
    if target is not None:
        if not (isinstance(target, str) and re.fullmatch(_TARGET_HEX, target)):
            raise ValueError("target must be a 12-hex record fingerprint, got %r" % (target,))
        if not is_design_flags(flags):
            raise ValueError("only a design thread (a `design:` key, or a summary's `design-departure:` keys) carries "
                             "a target, got %r" % (list(flags),))
        stamp += " · target %s" % target
    if summary is not None:
        if not (isinstance(summary, str) and re.fullmatch(_SUMMARY_HEX, summary)):
            raise ValueError("summary must be a 12-hex summary hash, got %r" % (summary,))
        if not summary_keys(flags):
            raise ValueError("only a design summary (every flag key `design-departure:`) carries a summary, got %r"
                             % (list(flags),))
        stamp += " · summary %s" % summary
    if summary_keys(flags) and (target is None or summary is None):
        # #241: every reader keys a summary on the pair; one without either would read as neither kind and never clear.
        raise ValueError("a design summary (every flag key `design-departure:`) carries both a target and a summary")
    _check_phase(phase)
    if phase is not None:
        stamp += " · phase %d" % phase
    stamp += " -->"
    if pr_url is not None and not (isinstance(pr_url, str) and re.fullmatch(r"https://\S+", pr_url)):
        raise ValueError("pr_url must be an https URL with no whitespace, got %r" % (pr_url,))
    if not isinstance(body, str):
        raise ValueError("body must be text, got %r" % (body,))
    if _CAPTURES_LINE_RE.search(_lf(body)):
        # #237: the captures line is the script's alone (`captures_block`), so the one a reader parses is never forged by
        # a body — and a design root meant to carry none never yields one.
        raise ValueError("body must not carry a captures line; the script writes it from `captures`")
    if link_paths is not None and not isinstance(link_paths, (list, tuple)):
        raise ValueError("link_paths must be a list of paths, got %r" % (link_paths,))
    link = ""
    if subject == "file" and pr_url:
        paths = [_check_field(p, "link path") for p in (link_paths or file_check_paths(path, flags))]
        if len(paths) <= 1:
            link = "[View the change](%s)\n\n" % diff_link(pr_url, path)
        else:
            shown = paths[:LINK_LINE_CAP]
            link = "".join("[View the change](%s) — `%s`\n" % (diff_link(pr_url, p), p) for p in shown)
            if len(paths) > len(shown):
                link += LINK_REMAINDER % (files_url(pr_url), len(paths) - len(shown)) + "\n"
            link += "\n"
    if captures is not None:
        link += captures_block(check_captures(captures, flags), head, pr_url) + "\n"
    closing = CLOSINGS[closing_kind(flags, group, advisory)]
    return "%s\n%s\n**F%d**\n\n%s%s\n\n%s" % (MARKER, stamp, f, link, body.rstrip("\n"), closing)


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


def retires_field(retires):
    """``retires`` (an iterable of `F<n>` strings) ascending by number and de-duplicated, as the review stamp's
    ` · retires` field carries it (#206); ``[]`` for ``None``. Raises ``ValueError`` on an entry that is not
    `F<n>`."""
    if retires is None:
        return []
    if isinstance(retires, str) or not isinstance(retires, (list, tuple, set, frozenset)):
        raise ValueError("retires must be a list of F<n> ids, got %r" % (retires,))
    for ref in retires:
        if not (isinstance(ref, str) and re.fullmatch(_F_REF, ref)):
            raise ValueError("a retires entry must be F<n>, got %r" % (ref,))
    return ["F%d" % n for n in sorted({int(ref[1:]) for ref in retires})]


def stamp_review(head, body, phase=None, retires=None):
    """The full text of the review body that carries the threads: the marker, the review stamp (with
    ` · retires F<n>,…` naming the threads the posting run retired, #206, then ` · phase <N>` on a resolver review
    pause's, #170), then ``body``. Without either the stamp is byte-identical. Raises ``ValueError`` only."""
    if not (isinstance(head, str) and re.fullmatch(_HEX40, head)):
        raise ValueError("head must be a 40-hex commit sha, got %r" % (head,))
    _check_phase(phase)
    refs = retires_field(retires)
    return "%s\n<!-- review · head %s%s%s -->\n\n%s" % (
        MARKER, head, " · retires %s" % ",".join(refs) if refs else "",
        " · phase %d" % phase if phase is not None else "", body)


def is_marked(body):
    """Whether ``body`` opens with the marker line (leading whitespace ignored)."""
    return isinstance(body, str) and body.lstrip().startswith(MARKER)


def parse_stamp(body):
    """The stamp of a marked comment: ``{"kind": "root", f, head, path, subject, range, flags,
    supersedes, group, advisory, target, summary, phase}`` (``group`` ``None`` or one of :data:`GROUPS`, ``advisory`` a bool, #181;
    ``target`` a design thread's 12-hex record fingerprint or ``None``, #206; ``summary`` a design summary's 12-hex hash or
    ``None``, #241), ``{"kind": "reply", f, head, decision}`` (``decision`` ``None`` on a plain reply) or
    ``{"kind": "review", head, retires, phase}`` (``retires`` the `F<n>` ids the posting run retired, #206, ``[]``
    without the field; ``phase`` an int on a review pause's stamps, #170, else ``None``);
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
            "group": match.group("group"),
            "advisory": bool(match.group("advisory")),
            "target": match.group("target"),
            "summary": match.group("summary"),
            "phase": int(match.group("phase")) if match.group("phase") else None,
        }
    match = _REPLY_STAMP_RE.match(line)
    if match:
        return {"kind": "reply", "f": int(match.group("f")) if match.group("f") else None, "head": match.group("head"),
                "decision": match.group("decision")}
    match = _REVIEW_STAMP_RE.match(line)
    if match:
        return {"kind": "review", "head": match.group("head"),
                "retires": match.group("retires").split(",") if match.group("retires") else [],
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


def file_check_paths(path, flags):
    """The distinct paths a pipeline `file` thread's hunk check reads (#181): its anchor ``path``, then every
    non-security key's path in key order. Pure. Public: the review tool's prep shows the same set as a
    multi-file thread's members (`prep_review.member_paths`), so what acceptance covers is what is shown."""
    paths = [path] if path else []
    for key in flags or []:
        kind, key_path = flag_key_parts(key)
        if kind != "security" and key_path and key_path not in paths:
            paths.append(key_path)
    return paths


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
    ``replaced`` (whether that successor replaced it, so it gates nothing — contract §4, #163) and ``retired``
    (whether a trusted review stamp retired it, so it gates nothing — §4, #206); a gate reads ``replaced`` and
    ``retired``, never ``superseded_by``.
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
        # A design summary's subject is its explanations, which its hash pins, not code (#241, contract §6): its hunk
        # check never runs, so a moved anchor never supersedes its approval. Read only from a trusted root whose keys
        # confirm it; every other thread kind, a design group's included, keeps the hunk-unchanged rule.
        summary = (root_stamp.get("summary") if root_stamp is not None and root_stamp.get("target")
                   and summary_keys(root_stamp["flags"]) else None)
        if summary is not None:
            hunk_check = lambda: False  # noqa: E731
        elif root_stamp is not None and subject == "file":
            # One rule for every multi-file thread (#181, contract §6): a pipeline `file` thread runs the file
            # check once per distinct path its anchor and keys name (security keys name none) — a batch's or a
            # group's members, a rider's test — and reads changed when any one did, as one file thread per path
            # would have been. A single-file thread checks its one path, as before.
            check_paths = file_check_paths(path, root_stamp["flags"])
            hunk_check = lambda: any(  # noqa: E731
                _anchored_hunk_changed(root, root_commit, reference, head, p, None, "file") for p in check_paths
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
            # #181: read only from a trusted pipeline root's stamp field, never inferred from its keys or members'
            # kinds. `group` needs every key `sensitive:`, `advisory` a batch; any other thread reads neither.
            "group": (root_stamp["group"] if root_stamp is not None
                      and group_members(root_stamp["flags"], root_stamp.get("group")) else None),
            "advisory": bool(root_stamp is not None and root_stamp.get("advisory")
                             and batch_members(root_stamp["flags"])),
            # The review pause that posted it (#170): `None` on an evaluator thread and an operator one.
            "phase": root_stamp.get("phase") if root_stamp is not None else None,
            # #206: the record fingerprint a design thread was compared against, read only from a trusted pipeline
            # root's stamp whose keys carry a `design:` key, or are all a summary's `design-departure:` keys (#241),
            # never inferred; `None` on every other thread.
            "target": (root_stamp.get("target") if root_stamp is not None
                       and is_design_flags(root_stamp["flags"]) else None),
            # #241: a design summary's hash, from a trusted root whose keys are all `design-departure:` and that carries
            # a target; `None` on every other thread.
            "summary": summary,
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
    # words. A named thread is `replaced` (gates nothing) when it is `superseded`, or when it is a batch or a
    # group (#181; a design group too, #241) in
    # `unreviewed` / `changes-requested` and no trusted human comment, nor the viewer's decision reply (#169), on
    # it postdates its earliest successor's root — a design summary (#241) in `accepted` too, since its successor carries
    # a changed set of explanations the old approval never saw. Every other thread gates by its state, named or not — a
    # newer human request is never dropped.
    for entry, comments in zip(threads, stored):
        at = successor_at.get(id(entry))
        replaced = False
        if entry["superseded_by"] is not None and at is not None:
            if entry["state"] == "superseded":
                replaced = True
            elif (entry["state"] in ("unreviewed", "changes-requested")
                  and (batch_members(entry["flags"]) or entry["group"] or entry["summary"])) or (
                    entry["state"] == "accepted" and entry["summary"]):
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
            stamp = parse_stamp(body) if kind == "review" else None
            marked = ((stamp or {}).get("kind") == "review" and _may_mark(_login(node), viewer, permissions))
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
                    "phase": stamp["phase"] if marked else None,
                    # The threads the run that posted it retired (#206): only a marked review's stamp counts.
                    "retires": stamp["retires"] if marked else [],
                    # The stamp's head, which a retirement it records reports (`_mark_retired`).
                    "stamp_head": stamp["head"] if marked else None,
                    "state": node.get("state") if kind == "review" else None,
                }
            )
    conversation.sort(key=lambda c: c.get("createdAt") or "")
    _mark_retired(threads, stored, conversation, viewer)
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


def _mark_retired(threads, stored, conversation, viewer):
    """Set ``retired`` on every listed thread (#206, R1.11, contract §4): ``{"review", "head"}`` of the latest
    marked review (by ``createdAt``, the order the conversation is sorted in) whose ` · retires` stamp names the
    pipeline thread's ``F``, else ``None``. Only a marked review counts, so only this viewer's or a resolution-grade
    author's stamp (SEC-3, the rule a marked root follows) — except on a **design** thread (a `design:` key, or a design
    summary's `design-departure:` keys, #241), whose
    retirement only this viewer's stamp records: a resolution never clears a design thread (§4), and the script
    alone decides when a difference stops needing a summary's approval (§4, #241), so a resolution-grade author's
    hand-written stamp must not retire either. A trusted human comment, or a decision reply, on
    the thread after that review drops the retirement — a person's later request is never dropped; a comment with
    no ``createdAt`` counts as newer, and a review with no ``createdAt`` retires nothing."""
    stamping = {}  # F -> the marked reviews naming it, oldest first
    for entry in conversation:
        if entry.get("kind") != "review" or not entry.get("marked"):
            continue
        for ref in entry.get("retires") or []:
            stamping.setdefault(int(ref[1:]), []).append(entry)
    for entry, comments in zip(threads, stored):
        reviews = stamping.get(entry["f"], []) if entry["origin"] == "pipeline" and entry["f"] is not None else []
        if is_design_flags(entry["flags"]):
            reviews = [r for r in reviews if viewer and r.get("author") == viewer]
        review = reviews[-1] if reviews else None
        at = review.get("createdAt") if review is not None else None
        if review is not None and at and not any(
                (not c["marked"] or c["decision"]) and (not c["createdAt"] or c["createdAt"] > at) for c in comments):
            entry["retired"] = {"review": review.get("url"), "head": review.get("stamp_head")}
        else:
            entry["retired"] = None


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


def design_clearance(threads_fact, design):
    """``threads_fact`` (`read_threads`' payload) with ``design`` set on every listed thread (#206): ``{"target",
    "adopted", "summary"}`` on a thread carrying a `design:` key or a design summary's `design-departure:` keys (#241),
    ``None`` on every other. ``summary`` is whether it is a design summary (its ``summary`` hash read): an `accepted`
    summary clears every difference it lists with no ``adopted`` condition — its subject is the explanations, which
    the operator approved — while every other design thread keeps the rule below.

    ``target`` is the record fingerprint the thread was compared against (its trusted root stamp's, ``None`` when it
    carries none). ``adopted`` says whether the plan no longer binds the thread to that record, on a thread that carries
    a ``target``: a planner revise **adopted** a revised record (``design["citation"]`` `current`, the record that
    stands having a fingerprint other than ``target``), or **withdrew** the plan's design target (citation `none`: the
    operator chose to plan without the record on the planner's card, `skills/planner/references/design-detour.md` §6 —
    an operator decision recorded on PR #221, extending the plan's definition). A design thread reading `accepted` (a
    resolution or an `accept` decision reply) gates unless ``adopted``: an `accept` against the record the thread was
    compared with never clears it, and the gate reads this fact, never the reply's text. The decision set is unchanged.
    A ``design`` of ``None`` — no plan read — adopts nothing; callers pass a fact only when they read a plan. Accepted
    residual: a malformed `Design target:` line reads citation `none` too, and so adopts; the plan reviewer blocks one,
    and the plan is a trusted marker. Pure; returns a new dict, and a fact that is not ``available`` is returned with
    its (empty) thread list annotated the same way."""
    out = dict(threads_fact or {})
    design = design if isinstance(design, dict) else {}
    record = design.get("record") if isinstance(design.get("record"), dict) else {}
    fingerprint = record.get("fingerprint")
    current = design.get("citation") == "current" and isinstance(fingerprint, str) and bool(fingerprint)
    withdrawn = design.get("citation") == "none"
    threads = []
    for thread in out.get("threads") or []:
        thread = dict(thread)
        if is_design_flags(thread.get("flags")):
            target = thread.get("target")
            targeted = isinstance(target, str) and bool(target)
            thread["design"] = {"target": target,
                                "adopted": bool(targeted and ((current and target != fingerprint) or withdrawn)),
                                # #241: a summary's `accepted` clears every difference it lists, whatever `adopted`
                                # reads; the gate keys on this, never on the thread's keys.
                                "summary": bool(thread.get("summary"))}
        else:
            thread["design"] = None
        threads.append(thread)
    if "threads" in out:
        out["threads"] = threads
    return out


# ---------------------------------------------------------------------------
# Flags: which code gets a new thread (evaluator S4.6)
# ---------------------------------------------------------------------------

# Cap order, highest first: security > design > sensitive > judgment > out-of-plan > routine > plan. `routine` is the
# batch (#163): every routine out-of-plan file in one thread, ranked after every per-file out-of-plan thread.
# `design` (#206) is exempt from the cap (the record bounds how many states differ, and every difference is a
# thread); it ranks second only so a future reader of the order sees where it sits.
FLAG_KINDS = ("security", "design", "sensitive", "judgment", "out-of-plan", "routine", "plan")
# Kinds whose signal names a file, not a site: they anchor on the whole file and match an existing thread on
# kind and path alone. A file thread's hunk check reads every change to the file, so a hunk pushed anywhere in
# it after the resolution supersedes the thread (SEC-2: an anchor on the first hunk alone let a later hunk in
# the same file pass under a resolved thread).
_FILE_LEVEL_KINDS = ("sensitive", "out-of-plan", "routine")
_SITE_RE = re.compile(r"^(?P<path>.+):(?P<start>[1-9][0-9]*)(?:-(?P<end>[1-9][0-9]*))?$")
_SECURITY_SITE_RE = re.compile(r"^(?P<id>SEC-[1-9][0-9]*)\s+(?P<site>\S.*)$")
_PLAN_THREAD_RE = re.compile(r"^(?P<site>\S.*?)\s+—\s+(?P<text>\S.*)$")
_SPLIT_RE = re.compile(r"^F(?P<f>[1-9][0-9]*)\s+(?P<path>\S.*?)\s*$")
# A design site (#206): `<state-id> <path>[:<line>[-<line>]]`, the build file that renders a differing state.
_DESIGN_SITE_RE = re.compile(r"^(?P<state>[a-z0-9][a-z0-9-]*)\s+(?P<site>\S.*?)\s*$")
DESIGN_VERDICT_UNREAD = "DESIGN_VERDICT_UNREAD"
DESIGN_TARGET_MISMATCH = "DESIGN_TARGET_MISMATCH"


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


def group_members(keys, group):
    """The member paths of a group thread (#181): every key's path when ``group`` is one of :data:`GROUPS`
    (the root stamp's field) and every key is a key of that kind, else ``[]`` (not a group). The field names a
    group and the keys confirm it, so an old per-file sensitive thread, which carries no field, is never one, nor an old
    single-state design thread (#241). A design group's members share one path, so its list may repeat it."""
    keys = list(keys or [])
    if group not in GROUPS or not keys or any(flag_key_parts(k)[0] != group for k in keys):
        return []
    return [flag_key_parts(k)[1] for k in keys]


# Flag-key kinds that name no path (#241): a security key names its finding, a design summary's key its explanation.
_PATHLESS_KINDS = ("security", "design-departure")


def flag_key_parts(key):
    """``(kind, path)`` of a flag key; ``path`` is ``None`` for a security key (`security:SEC-<n>`) and a design
    summary's key (`design-departure:<id>`, #241), which name no file. A judgment key (`judgment:<dimension>:<path>`)
    and a design key (`design:<state-id>:<path>`, #206) carry a middle segment, which is dropped: the path is the rest."""
    kind, _, rest = (key or "").partition(":")
    if kind in ("judgment", "design"):
        _, _, rest = rest.partition(":")
    return kind, (None if kind in _PATHLESS_KINDS else rest)


def summary_keys(keys):
    """The explanation ids of a design summary's keys (#241): every key's id when all of ``keys`` are
    `design-departure:<id>` keys, else ``[]`` (not a summary)."""
    keys = list(keys or [])
    if not keys or any(flag_key_parts(k)[0] != "design-departure" for k in keys):
        return []
    return [k.partition(":")[2] for k in keys]


def is_design_flags(keys):
    """Whether a thread's keys make it a design thread (#206, #241): any `design:` key, or a summary's keys."""
    keys = list(keys or [])
    return any(flag_key_parts(k)[0] == "design" for k in keys) or bool(summary_keys(keys))


def design_key_state(key):
    """The state id of a design key (`design:<state-id>:<path>`, #206), else ``None``."""
    kind, _, rest = (key or "").partition(":")
    if kind != "design":
        return None
    state, sep, _ = rest.partition(":")
    return state if sep and state else None


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


def _advisory(members):
    """Whether a batch is advisory (#181): its target members (`members_of` output, the final set) are all of a
    kind in :data:`ADVISORY_KINDS` this run. A member with no kind (``None``) or a `fallout` one makes it not.
    The one place advisory is computed, over the final target set, so a later cap or split of a batch (#165)
    re-derives it per resulting batch."""
    return bool(members) and all(m.get("kind") in ADVISORY_KINDS for m in members)


# The retirements the design block records (#206, #241): every difference of the thread's states now matches, or is
# now explained (a single-state thread, or a design group none of whose members has an unexplained difference left), or
# a summary no difference is explained by any more.
DESIGN_RETIREMENTS = ("design-matched", "design-explained", "summary-emptied")


def _anchor_key(anchor):
    """What makes two design anchors one (#241): the path, the subject and the line range."""
    return (anchor["path"], anchor["subject"], tuple(anchor["range"]) if anchor["range"] else None)


def _design_flags(target, verdict, recorded, sites, single_heads, group_heads, summary_heads, explanations,
                  classification, files, aliases, matched, retired, unanchored, settled=False, uncovered=(),
                  designs=None):
    """The design block of :func:`build_flags` (#206, #241). Pure; appends to ``matched``, ``retired`` and
    ``unanchored`` (``unanchored_design``) and returns the new design entries, each ``{"flags", "anchor", "group",
    "summary", "supersedes", "predecessor", "design"}``.

    Each difference the verdict reports for a recorded state is **explained** or **unexplained**
    (:func:`classify_differences` — every one is unexplained with no classification). A state is **clear** when the
    verdict reports it `match`, or `differs` with every difference explained; a state the verdict does not cover is
    never clear.

    - **Single-state heads** (a head carrying `design:` keys and no group) at this ``target``: one in `unreviewed`,
      `accepted` or `superseded` whose every state is clear retires — `design-matched` when every one matches, else
      `design-explained`; a `changes-requested` one keeps gating. Otherwise a state with an unexplained difference
      matches a live head carrying its key (posts nothing), rides a superseded head's generic successor while its file
      is in the diff, or posts.
    - **Design groups** (` · group design`) at this ``target``: one whose every member is clear retires as a
      single-state head does. An `unreviewed` or `superseded` one is re-cut like a sensitive group: its members that
      are not clear stay, a fresh state whose anchor equals its first kept member's anchor joins (the first such group
      in `F` order), and a changed member set — or a superseded head — takes a successor through ``supersedes``; an
      `accepted` or `changes-requested` group is left as it is, its members matched.
    - **Fresh states**: a state with an unexplained difference and no head posts one thread keyed
      `design:<state-id>:<path>` listing only its unexplained differences; fresh states with no predecessor whose
      sites resolve to one anchor post one design group. A state with no site, or a site outside the diff, is
      ``unanchored``.
    - **The summary**: every explanation covering at least one explained difference is one `design-departure:<id>`
      key on one thread stamped with ``target`` and :func:`summary_hash`. A summary head at this target with the same
      hash matches; a `changes-requested` one keeps gating and nothing new posts. Only on a **settled** run
      (``uncovered`` empty, and either no difference at all or ``settled``: the classification was read for every
      difference and nothing untrusted was dropped) does a head move: the first `unreviewed` or `accepted` one with another hash
      takes a successor (a summary is never `superseded`: its hunk check never runs), any other retires
      `summary-emptied`, and so does every head (bar a `changes-requested` one) when no difference is explained. On an
      unsettled run every head stays as it is, and what the run did explain posts as a fresh summary beside it [a
      `## Plan override` on PR #242: #206's "a failed step never retires a thread" wins over the plan's unconditional
      re-cut]. It anchors on the first covered state with a site, else the first differing state with one, else the
      first file in the diff (its subject is its explanations) [a `## Plan override` on PR #242: the plan's rule left
      it unanchored with no route out]; only on an empty diff are its differences ``unanchored``, each carrying
      ``explained_by`` (the explanation ids that explained it), which no unexplained entry carries.

    A head stamped with another target is never matched or retired here: it gates by its own state."""
    def ref(head):
        return _thread_ref(head)

    def norm(path):
        return aliases.get(path, path)

    differences = [d for d in verdict_differences(verdict, recorded) if d["state"] in recorded]
    classes = classify_differences(differences, explanations or [], classification)
    unexplained, explained = {}, []
    for d in differences:
        cls = classes[d["id"]]
        if cls["class"] == "explained":
            explained.append(dict(d, by=list(cls["by"])))
        else:
            unexplained.setdefault(d["state"], []).append(d)

    def verdict_of(state):
        return (verdict.get(state) or {}).get("verdict")

    def clear(state):
        return verdict_of(state) == "match" or (verdict_of(state) == "differs" and state not in unexplained)

    def clear_reason(states):
        return "design-matched" if all(verdict_of(st) == "match" for st in states) else "design-explained"

    def site_anchor(state):
        site = sites.get(state)
        return _anchor(files, norm(site[0]), site[1]) if site else None

    def joined(diffs):
        return "; ".join(d["text"] for d in diffs) if diffs else None

    def state_design(states):
        diffs = [d for st in states for d in unexplained.get(st, [])]
        return {"state": states[0], "states": list(states), "difference": joined(diffs),
                "differences": [{k: d[k] for k in ("id", "state", "aspect", "text")} for d in diffs]}

    taken, new = set(), []
    retired_heads = set()
    for head in single_heads:
        head_states = [design_key_state(k) for k in head.get("flags") or []]
        if (head.get("target") == target and head.get("state") in ("unreviewed", "accepted", "superseded")
                and head_states and all(head_states) and all(clear(st) for st in head_states)):
            retired.append({"thread": head.get("id"), "ref": ref(head), "reason": clear_reason(head_states)})
            retired_heads.add(id(head))
    eligible = []
    for head in group_heads:
        if head.get("target") != target:
            continue
        members = [design_key_state(k) for k in head.get("flags") or []]
        state = head.get("state")
        if state in ("unreviewed", "accepted", "superseded") and all(clear(m) for m in members):
            retired.append({"thread": head.get("id"), "ref": ref(head), "reason": clear_reason(members)})
            continue
        if state in ("unreviewed", "superseded"):
            kept = [m for m in members if not clear(m)]
            # A member whose site now resolves to another anchor than the first kept member's leaves (#241): states
            # share a group only while they share an anchor, so it posts as fresh. One with no site stays.
            anchored = [(m, site_anchor(m)) for m in kept]
            first = next((a for _, a in anchored if a is not None), None)
            kept = [m for m, a in anchored if a is None or first is None or _anchor_key(a) == _anchor_key(first)]
            taken.update(kept)
            eligible.append((head, kept))
            continue
        for key in head.get("flags") or []:
            if design_key_state(key) in unexplained:
                taken.add(design_key_state(key))
                matched.append({"key": key, "thread": ref(head)})

    fresh = []  # [{"state", "anchor", "pred"}]
    for state in recorded:
        if state not in unexplained or state in taken:
            continue
        hits = [h for h in single_heads if id(h) not in retired_heads and h.get("target") == target
                and state in [design_key_state(k) for k in h.get("flags") or []]]

        def key_of(h, state=state):
            return next(k for k in h.get("flags") or [] if design_key_state(k) == state)

        live = [h for h in hits if h.get("state") != "superseded"]
        if live:
            matched.append({"key": key_of(live[0]), "thread": ref(live[0])})
            continue
        riding = [h for h in hits if norm(h.get("path")) in files]
        if riding:
            matched.append({"key": key_of(riding[0]), "thread": ref(riding[0]), "rides_successor": True})
            continue
        anchor = site_anchor(state)
        if anchor is None:
            unanchored.append({"state": state, "difference": joined(unexplained[state])})
            continue
        # Every hit here is superseded with its file gone from the diff: the fresh thread names it, so it is `replaced`.
        pred = min(hits, key=lambda h: h.get("f") or 0) if hits else None
        fresh.append({"state": state, "anchor": anchor, "pred": pred})

    def predecessor(head):
        return {"ref": ref(head), "url": head.get("url"), "first_line": None}

    # Design groups re-cut (#241): a fresh state with no predecessor joins the first eligible group sharing its anchor.
    joined_states = set()
    for head, kept in eligible:
        anchors = [a for a in (site_anchor(m) for m in kept) if a is not None]
        group_key = _anchor_key(anchors[0]) if anchors else None
        joining = [f for f in fresh if f["pred"] is None and f["state"] not in joined_states
                   and group_key is not None and _anchor_key(f["anchor"]) == group_key]
        joined_states.update(f["state"] for f in joining)
        target_states = [st for st in recorded if st in set(kept) | {f["state"] for f in joining}]
        members = [design_key_state(k) for k in head.get("flags") or []]
        if head.get("state") != "superseded" and set(target_states) == set(members):
            for key in head.get("flags") or []:
                if design_key_state(key) in unexplained:
                    matched.append({"key": key, "thread": ref(head)})
            continue
        if not target_states:
            retired.append({"thread": head.get("id"), "ref": ref(head), "reason": "design-explained"})
            continue
        anchor = anchors[0] if anchors else (
            {"path": norm(head.get("path")), "subject": "file", "range": None, "hunk": None}
            if norm(head.get("path")) in files else None)
        if anchor is None:
            # No anchor this run: the head keeps gating, as a design head whose file left the diff does.
            for key in head.get("flags") or []:
                matched.append({"key": key, "thread": ref(head)})
            continue
        new.append({"flags": ["design:%s:%s" % (st, anchor["path"]) for st in target_states], "anchor": anchor,
                    "group": "design", "summary": None, "supersedes": ref(head), "predecessor": predecessor(head),
                    "design": state_design(target_states)})

    # Fresh states: those with no predecessor sharing one anchor post one design group.
    by_anchor, order = {}, []
    for f in fresh:
        if f["state"] in joined_states:
            continue
        key = ("pred", f["state"]) if f["pred"] is not None else _anchor_key(f["anchor"])
        if key not in by_anchor:
            by_anchor[key] = []
            order.append(key)
        by_anchor[key].append(f)
    for key in order:
        group = by_anchor[key]
        anchor, pred = group[0]["anchor"], group[0]["pred"]
        states = [f["state"] for f in group]
        new.append({"flags": ["design:%s:%s" % (st, anchor["path"]) for st in states], "anchor": anchor,
                    "group": "design" if len(states) > 1 else None, "summary": None,
                    "supersedes": ref(pred) if pred else None, "predecessor": predecessor(pred) if pred else None,
                    "design": state_design(states)})

    # The summary (#241): one thread per target for every explained difference.
    by_id = {e.get("id"): e for e in explanations or []}
    used, covers = {}, []
    for d in explained:
        for cited in d["by"]:
            used.setdefault(cited, []).append((d["state"], d["aspect"]))
        covers.append({k: d[k] for k in ("id", "state", "aspect", "text", "by")})
    used_list = [(by_id[i], used[i]) for i in sorted(used, key=lambda i: (i[0], int(i[1:])))]
    digest = summary_hash(target, used_list) if explained else None
    s_heads = [h for h in summary_heads if h.get("target") == target]
    keys = ["design-departure:%s" % e["id"] for e, _ in used_list]
    handled = False
    if explained:
        same = [h for h in s_heads if h.get("summary") == digest]
        asked = [h for h in s_heads if h.get("state") == "changes-requested"]
        if same or asked:
            for key in (same or asked)[0].get("flags") or []:
                matched.append({"key": key, "thread": ref((same or asked)[0])})
            handled = True
    covered_states = []
    for d in explained:
        if d["state"] not in covered_states:
            covered_states.append(d["state"])
    anchors = [a for a in (site_anchor(st) for st in covered_states) if a is not None]
    anchors = anchors or [a for a in (site_anchor(st) for st in recorded if verdict_of(st) == "differs")
                          if a is not None]
    if not anchors and explained:
        # The summary's subject is its explanations, not code (its hunk check never runs), so any file in the diff
        # anchors it (#241): the first, in sorted order, with new-side lines.
        fallback = _batch_anchor(files, {}, list(files))
        anchors = [fallback] if fallback else []
    # Only a settled run moves a summary head (#241): one whose verdict covers every recorded state and that either has
    # no difference to classify or read the classification for every difference with no explanation dropped as
    # untrusted. Any other run leaves every summary head as it is (a failed step never retires a thread), posting a
    # fresh summary beside it for what it did explain.
    decidable = (bool(settled) or not differences) and not list(uncovered)
    successor_of = None
    for head in s_heads:
        if handled and head.get("summary") == digest or head.get("state") == "changes-requested":
            continue
        if not decidable:
            continue
        if explained and not handled and successor_of is None and anchors:
            successor_of = head
            continue
        if explained and not anchors and not handled:
            # Nothing to anchor a successor on: the head keeps gating, and the differences are reported unanchored.
            continue
        retired.append({"thread": head.get("id"), "ref": ref(head), "reason": "summary-emptied"})
    if explained and not handled:
        if not anchors:
            # Explained, but nothing to anchor the summary on (#241): marked, so the evaluator asks for a site line
            # rather than a build change.
            for d in explained:
                unanchored.append({"state": d["state"], "difference": d["text"], "explained_by": list(d["by"])})
        else:
            new.append({"flags": keys, "anchor": anchors[0], "group": None, "summary": digest,
                        "supersedes": ref(successor_of) if successor_of else None,
                        "predecessor": predecessor(successor_of) if successor_of else None,
                        "design": {"explanations": [dict(e) for e, _ in used_list], "covers": covers}})
    # The `design` a superseded single-state head's generic successor carries (#241): its states' unexplained
    # differences, so its body and caption never show a difference the summary lists.
    if designs is not None:
        for head in single_heads:
            head_states = [design_key_state(k) for k in head.get("flags") or []]
            if (head.get("target") == target and head.get("state") == "superseded" and id(head) not in retired_heads
                    and any(head_states)):
                designs[id(head)] = state_design([st for st in head_states if st])
    return new


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
      "splits": [(f, path)], "reasons_trusted": bool, "subjects": {path: subject},
      "sensitive_patterns": {path: pattern}, "triage_read": bool, "group_blockers": [path]?}`` — security entries
      already resolved to a path (a
      model-written site, else the record's anchor path), ``rejected`` carrying input lines that did not
      parse, ``claims`` each security entry's recorded claim, ``routine`` the out-of-plan files the triage
      called routine (#163), ``splits`` the operator's split requests, ``subjects`` the rider candidates' subjects
      and ``sensitive_patterns`` each sensitive path's first declared pattern (#181), ``triage_read`` whether a
      readable triage verdict ran this run (no pause has one), and ``group_blockers`` (#218, a review pause's only:
      absent on the evaluator's path) the raw out-of-plan paths, which add no key and only keep such a file out of a
      sensitive group.
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
    no `routine` verdict; ``reason`` only when ``reasons_trusted``); every other entry ``None``. A batch entry is
    ``advisory`` when every target member's kind this run is `test` or `fixture` (:func:`_advisory`); every other
    entry is not. On a run with a readable triage (``triage_read``), an `unreviewed` head stamped advisory whose
    target set is no longer advisory gets a successor even with its members unchanged, so a stale stamp never
    keeps it from gating; a run without one never re-cuts for advisory, and any batch it re-cuts gets no field
    (every member's kind is ``None`` that run).

    **Riders** (#181). A fresh `out-of-plan` flag whose file ``signals["subjects"]`` maps to a subject rides that
    subject's thread as an extra `out-of-plan:<path>` key when the subject's `out-of-plan:` thread posts this run
    (fresh, or a successor), the file carries no sensitive, security or judgment flag, and no split names it;
    otherwise it keeps its own thread, and so does a test whose subject itself rides another file's thread (no
    chains). Each entry reports its ``riders`` (``[{"path", "subject", "reason"?}]``, ``reason`` only when
    ``reasons_trusted``); on a generic successor that is every `out-of-plan:` key on another file than its anchor,
    ``subject`` from ``signals["subjects"]``, else the anchor path.

    **Sensitive groups** (#181). A fresh sensitive flag whose file has no other flag this run and a pattern in
    ``signals["sensitive_patterns"]`` joins the group of that pattern: one file thread stamped ` · group sensitive`
    carrying one `sensitive:<path>` key per member. Group chain heads (:func:`group_members`) re-cut like batch
    heads — a member leaves when it gains another flag or leaves the diff; `unreviewed` and `superseded` heads take
    their kept members plus the fresh members of their pattern (the first eligible head of that pattern in `F`
    order), recomputed from the first member, in sorted order, that has a pattern this run; `accepted` and
    `changes-requested` heads are left as they are; an emptied group retires (`group-emptied`). A group is one cap
    candidate, ranked `sensitive`, never file-collapsed. A thread with no group field is never a group, so an old
    per-file sensitive head keeps matching its file. **The pause exception:** at a review pause
    ``sensitive_patterns`` comes from the phase-scoped assess, so a group head none of whose members this phase
    touched has no pattern and takes no fresh members; the pause may then open a second group of that pattern,
    which the evaluator's run never merges. Each entry reports ``group`` (`"sensitive"` or ``None``) and
    ``pattern`` (the group's, else ``None``).

    **Design** (#206, #241). ``signals["design_target"]`` (the record fingerprint the comparison read; ``None`` when
    no design inputs were given, which leaves every design thread as it is), ``design_verdict`` (`parse.
    scan_design_comparison`'s ``states``, ``None`` when unread), ``design_states`` (the recorded state ids),
    ``design_sites`` (``{state: (path, rng)}``), ``design_explanations`` and ``design_classification`` (the departure
    classification's inputs, #241). With a readable verdict at a target, :func:`_design_flags` decides every design
    thread at that target — the single-state threads, the design groups and the summary — and its docstring states
    the rules. A superseded single-state design head rides its generic successor (keeping its ``target``) while its
    file is in the diff, and so does a superseded design group the design block does not take (no comparison this
    run, or one stamped with another target), keeping its ``group``; one whose file left the diff takes no
    successor and is never retired `path-left-diff`. A head stamped with another ``target`` is never matched or
    retired by this run's flags: it gates by its own state. ``uncovered_design`` lists the recorded states the verdict
    does not cover (every one when it is unread). With ``signals["design_only"]`` (``pause --design-only``) only
    design heads take successors and no batch or sensitive group is re-cut. Every ``new`` entry carries ``target`` (a
    design thread's, ``None`` on every other), ``summary`` (a design summary's hash, else ``None``) and ``design``
    (:func:`_design_flags`' shape on a design thread it posts, ``None`` on every other).
    """
    rejected = list(signals.get("rejected") or [])
    claims = dict(signals.get("claims") or {})
    unanchored_security, matched, retired, overflow = [], [], [], []
    reasons = dict(signals.get("reasons") or {})
    routine_kinds = dict(signals.get("routine_kinds") or {})
    reasons_trusted = bool(signals.get("reasons_trusted"))
    subjects = dict(signals.get("subjects") or {})
    sensitive_patterns = dict(signals.get("sensitive_patterns") or {})
    triage_read = bool(signals.get("triage_read"))

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
    # A thread a trusted review stamp retired (#206, `retired`) is never a chain head: a signal that returns posts
    # a fresh thread, which gates, rather than matching one the gate reads as cleared.
    heads = [t for t in threads if t.get("origin") == "pipeline" and t.get("superseded_by") is None
             and not t.get("retired")]
    batch_heads = sorted((t for t in heads if batch_members(t.get("flags"))), key=lambda t: t.get("f") or 0)
    # A sensitive group head (#181) is handled by the group block, like a batch head; a design group head (#241) and a
    # design summary head by the design block — set apart here, before `design_heads` is computed, so no group or
    # summary is re-cut twice or read as a single-state design thread.
    group_heads = sorted((t for t in heads if t.get("group") == "sensitive"
                          and group_members(t.get("flags"), "sensitive")), key=lambda t: t.get("f") or 0)
    design_group_heads = sorted((t for t in heads if t.get("group") == "design"
                                 and group_members(t.get("flags"), "design")), key=lambda t: t.get("f") or 0)
    summary_heads = sorted((t for t in heads if t.get("summary") and summary_keys(t.get("flags"))),
                           key=lambda t: t.get("f") or 0)
    batch_ids = ({id(t) for t in batch_heads} | {id(t) for t in group_heads} | {id(t) for t in design_group_heads}
                 | {id(t) for t in summary_heads})
    heads = [t for t in heads if id(t) not in batch_ids]
    gating_superseded = [t for t in threads if t.get("state") == "superseded" and t.get("superseded_by") is None
                         and not t.get("retired") and id(t) not in batch_ids]
    # A superseded design group (#241) the design block does not re-cut this run — no readable comparison, or one
    # stamped with another target — rides the generic successor, as a single-state design head does, keeping its keys,
    # group and target; the block below takes the ones at this run's target out again.
    gating_superseded += [t for t in design_group_heads if t.get("state") == "superseded"]

    # Design (#206): its own block, as batch and group heads have, since a design flag matches on state and
    # target, never on kind and path.
    def is_design(thread):
        return is_design_flags(thread.get("flags"))

    if signals.get("design_only"):
        # `pause --design-only`: only design heads take successors, and no batch or sensitive group is re-cut; a design
        # group and the summary are the design block's, so they re-cut as on any run (#241).
        batch_heads, group_heads = [], []
        gating_superseded = [t for t in gating_superseded if is_design(t)]
    design_target = signals.get("design_target")
    design_verdict = signals.get("design_verdict")
    design_sites = dict(signals.get("design_sites") or {})
    recorded = signals.get("design_states")
    unanchored_design, uncovered_design, design_new = [], [], []
    designs = {}  # id(head) -> the `design` a superseded single-state head's generic successor carries (#241)
    design_heads = [t for t in heads if is_design(t)]
    if design_target is not None:
        recorded_list = list(recorded) if recorded is not None else sorted((design_verdict or {}).keys())
        uncovered_design = [st for st in recorded_list if design_verdict is None or st not in design_verdict]
        if design_verdict is not None:
            design_new = _design_flags(
                design_target, design_verdict, recorded_list, design_sites, design_heads, design_group_heads,
                summary_heads, signals.get("design_explanations"), signals.get("design_classification"), files,
                aliases, matched, retired, unanchored_design, settled=bool(signals.get("design_settled")),
                uncovered=uncovered_design, designs=designs)
            retired_ids = {r["thread"] for r in retired if r.get("reason") in DESIGN_RETIREMENTS}
            gating_superseded = [t for t in gating_superseded if t.get("id") not in retired_ids
                                 and not (t.get("group") == "design" and t.get("target") == design_target)]
            for state in sorted(set(design_verdict) - set(recorded_list)):
                rejected.append({"source": "design-verdict", "line": state, "reason": "not-a-recorded-state"})

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

    def norm(path):
        return aliases.get(path, path)

    # A file whose only flag this run is a sensitive match, and whose first match names a pattern, joins a group.
    other_signal = {norm(f["path"]) for f in flags if f["kind"] != "sensitive" and f["path"]}
    # A review pause's raw out-of-plan list (#218): it adds no key, but keeps such a file out of a group.
    other_signal |= {norm(p) for p in signals.get("group_blockers") or []}

    def grouped(flag):
        return (flag["kind"] == "sensitive" and bool(sensitive_patterns.get(flag["path"]))
                and norm(flag["path"]) not in other_signal)

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

    fresh, routine_open, sensitive_open = [], [], []
    # The successor an `out-of-plan:<path>` flag rides this run, by path: a subject whose thread posts (#181).
    oop_successor = {}

    def take_open(flag):
        if flag["kind"] == "routine":
            routine_open.append(flag)
        elif grouped(flag):
            sensitive_open.append(flag)
        else:
            fresh.append(flag)

    for flag in flags:
        hits = [t for t in heads if matches(flag, t)]
        if not hits:
            take_open(flag)
            continue
        live = [t for t in hits if t.get("state") != "superseded"]
        if live:
            matched.append({"key": flag["key"], "thread": _thread_ref(live[0])})
        elif aliases.get(hits[0].get("path"), hits[0].get("path")) not in files:
            # The head's successor cannot be posted, so the flag takes its own thread — a routine one in the batch.
            take_open(flag)
        elif flag["kind"] == "routine":
            # Its file's own superseded out-of-plan head gets a successor carrying `out-of-plan:<path>`: the
            # routine key never rides it, so a batch stays a thread whose keys are all `routine:`.
            matched.append({"key": flag["key"], "thread": _thread_ref(hits[0]), "rides_successor": True})
        else:
            riders[id(hits[0])].append(flag["key"])
            matched.append({"key": flag["key"], "thread": _thread_ref(hits[0]), "rides_successor": True})
            if flag["kind"] == "out-of-plan":
                oop_successor.setdefault(flag["path"], hits[0])
    for thread in gating_superseded:
        for key in thread.get("flags") or []:
            kind, path = flag_key_parts(key)
            if kind == "out-of-plan" and thread.get("origin") == "pipeline" and norm(thread.get("path")) in files:
                oop_successor.setdefault(path, thread)

    # Riders (#181): a fresh out-of-plan test or fixture rides its subject's thread when that thread posts this
    # run. Never a test with a sensitive, security or judgment flag, one a split names, or one whose subject is
    # itself riding; never onto a live head (re-cutting a thread a person may be reading saves one acknowledgement).
    rider_ok = {p for p in subjects if p not in split_paths}
    rider_ok -= {norm(f["path"]) for f in flags if f["kind"] in ("sensitive", "security", "judgment")}
    fresh_oop = {f["path"] for f in fresh if f["kind"] == "out-of-plan"}
    # A file whose `out-of-plan:` key is carried this run by a successor anchored on another file (it rode that
    # thread before, or rides it now from the matching loop above) posts no thread of its own, so it is no subject.
    rode_elsewhere = {path for path, head in oop_successor.items() if norm(head.get("path")) != norm(path)}
    rides_memo, visiting = {}, set()

    def rides(path):
        """Whether ``path``'s fresh out-of-plan flag rides its subject's thread: it may ride, and its subject's
        `out-of-plan:` thread posts this run — the subject has a fresh flag that does not itself ride, or rides a
        successor's thread. A test whose subject actually rides elsewhere keeps its own thread (no chains)."""
        if path in rides_memo:
            return rides_memo[path]
        if path in visiting or path not in rider_ok or path not in fresh_oop:
            return False
        visiting.add(path)
        subject = subjects.get(path)
        if subject in rode_elsewhere:
            result = False
        elif subject in fresh_oop:
            result = not rides(subject)
        else:
            result = oop_successor.get(subject) is not None
        visiting.discard(path)
        rides_memo[path] = result
        return result

    rider_flags = {}
    kept_fresh = []
    for flag in fresh:
        path = flag["path"]
        subject = subjects.get(path)
        if flag["kind"] == "out-of-plan" and rides(path):
            if subject in fresh_oop:
                rider_flags.setdefault(subject, []).append(flag)
                continue
            head = oop_successor[subject]
            riders[id(head)].append(flag["key"])
            matched.append({"key": flag["key"], "thread": _thread_ref(head), "rides_successor": True})
            continue
        kept_fresh.append(flag)
    fresh = kept_fresh

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
        # A stale ` · advisory` stamp (#181): on a run with a readable triage, an advisory head whose target set
        # is no longer advisory (a member now `fallout`, or kept with no verdict) is re-cut, so its successor
        # gates. A run with no readable triage never re-cuts for advisory; a batch it re-cuts for another reason
        # gets no field (every member's kind is `None` that run). A review pause never reaches a batch (#218).
        stale = triage_read and bool(head.get("advisory")) and not _advisory(members_of(target))
        if (head.get("state") != "superseded" and set(target) == set(batch_members(head.get("flags")))
                and not stale):
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
            "advisory": _advisory(members_of(target)),
        })
    if eligible:
        fresh_routine = []

    # Sensitive groups (#181), on the batch's model. A member leaves when it gains another flag this run (it takes
    # its own thread) or its path left the diff; a member merely no longer sensitive stays.
    sensitive_now = {f["path"]: f for f in sensitive_open}

    # A member of a re-cut (`unreviewed` or `superseded`) group head flagged sensitive this run through a match with
    # no pattern takes a per-file thread (not grouped), so it leaves that head's target set: one file is never
    # covered by two live threads.
    ungrouped = {norm(f["path"]) for f in flags if f["kind"] == "sensitive" and not grouped(f)}

    def stays_grouped(member):
        return norm(member) in files and norm(member) not in other_signal and norm(member) not in ungrouped

    def group_pattern(paths):
        # The pause exception (#181): at a review pause `sensitive_patterns` comes from the phase-scoped assess, so a
        # head none of whose members this phase touched has no pattern and takes no fresh members; the pause may
        # then open a second group of that pattern, which the evaluator's run never merges.
        return next((sensitive_patterns[p] for p in sorted(paths) if sensitive_patterns.get(p)), None)

    g_carried, g_assigned, g_eligible = set(), set(), []
    for head in group_heads:
        members = group_members(head.get("flags"), head.get("group"))
        g_carried.update(members)
        if head.get("state") in ("unreviewed", "superseded"):
            kept = [m for m in members if stays_grouped(m)]
            g_eligible.append((head, kept, group_pattern(kept)))
            continue
        # An `accepted` or `changes-requested` group is left as it is.
        for m in members:
            if m in sensitive_now and m not in g_assigned:
                g_assigned.add(m)
                matched.append({"key": "sensitive:%s" % m, "thread": _thread_ref(head)})
    fresh_by_pattern = {}
    for path in sensitive_now:
        if path not in g_carried:
            fresh_by_pattern.setdefault(sensitive_patterns[path], []).append(path)
    group_successors, taken = [], set()
    for head, kept, pattern in g_eligible:
        joining = set()
        if pattern is not None and pattern in fresh_by_pattern and pattern not in taken:
            taken.add(pattern)
            joining = set(fresh_by_pattern[pattern])
        target = sorted(set(kept) | joining)
        ref = _thread_ref(head)
        if head.get("state") != "superseded" and set(target) == set(group_members(head.get("flags"), head.get("group"))):
            for m in target:
                if m in sensitive_now and m not in g_assigned:
                    g_assigned.add(m)
                    matched.append({"key": "sensitive:%s" % m, "thread": ref})
            continue
        if not target:
            retired.append({"thread": head.get("id"), "ref": ref, "reason": "group-emptied"})
            continue
        group_successors.append({
            "anchor": _batch_anchor(files, aliases, target), "flags": ["sensitive:%s" % p for p in target],
            "supersedes": ref, "predecessor": {"ref": ref, "url": head.get("url"), "first_line": None},
            "group": "sensitive", "pattern": group_pattern(target),
        })
    fresh_groups = [(pattern, paths) for pattern, paths in fresh_by_pattern.items() if pattern not in taken]

    # Successors: one per superseded chain head, whatever fires now.
    successors = []
    for thread in gating_superseded:
        path = aliases.get(thread.get("path"), thread.get("path"))
        keys = list(thread.get("flags") or []) + [k for k in riders[id(thread)] if k not in (thread.get("flags") or [])]
        ref = _thread_ref(thread)
        if path not in files:
            if thread.get("origin") == "pipeline" and is_design(thread):
                # A design head clears only by `design-matched`, `design-explained` (#241) or an adopted or withdrawn
                # target (`CLOSINGS["design"]`),
                # never because its file left the diff: it keeps gating until a comparison at its target reports a
                # match or a fresh design thread for its state supersedes it. It carries no `security:` key.
                continue
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
            # A design head's successor keeps the record fingerprint it was compared against (#206), and a design
            # group's its group (#241).
            "target": thread.get("target") if thread.get("origin") == "pipeline" and is_design(thread) else None,
            "group": "design" if thread.get("origin") == "pipeline" and thread.get("group") == "design" else None,
            "design": designs.get(id(thread)),
            "supersedes": ref,
            "predecessor": {"ref": ref, "url": thread.get("url"), "first_line": first_line},
            # Every `out-of-plan:` key on another file than the anchor is a rider (#181): the predecessor's and the
            # ones joining now.
            "riders": [dict({"path": p, "subject": subjects.get(p) or path},
                            **({"reason": reasons.get(p)} if reasons_trusted else {}))
                       for p in (flag_key_parts(k)[1] for k in keys if flag_key_parts(k)[0] == "out-of-plan")
                       if thread.get("origin") == "pipeline" and norm(p) != path],
        })

    successors.extend(batch_successors)
    successors.extend(group_successors)

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
        if flag["kind"] == "out-of-plan" and flag["path"] in rider_flags:
            g.setdefault("riders", []).extend(rider_flags[flag["path"]])
    candidates = [groups[k] for k in order]
    if fresh_routine:
        paths = [f["path"] for f in fresh_routine]
        anchor = _batch_anchor(files, aliases, paths)
        candidates.append({"path": anchor["path"], "subject": "file", "range": None, "batch": True,
                           "flags": sorted(fresh_routine, key=lambda f: f["path"])})
    for pattern, paths in fresh_groups:
        anchor = _batch_anchor(files, aliases, paths)
        candidates.append({"path": anchor["path"], "subject": "file", "range": None, "group": "sensitive",
                           "pattern": pattern, "flags": sorted((sensitive_now[p] for p in paths),
                                                               key=lambda f: f["path"])})

    def rank(group):
        return min(FLAG_KINDS.index(f["kind"]) for f in group["flags"])

    if len(candidates) > THREAD_CAP:
        by_path = {}
        # A batch and a sensitive group are one candidate each, never file-collapsed.
        batch = [g for g in candidates if g.get("batch") or g.get("group")]
        candidates = [g for g in candidates if not (g.get("batch") or g.get("group"))]
        for g in candidates:
            by_path.setdefault(g["path"], []).append(g)
        collapsed = []
        for path, items in by_path.items():
            if len(items) == 1:
                collapsed.append(items[0])
            else:
                collapsed.append({"path": path, "subject": "file", "range": None,
                                  "flags": [f for g in items for f in g["flags"]],
                                  "riders": [r for g in items for r in g.get("riders") or []]})
        candidates = collapsed + batch
    candidates.sort(key=rank)
    if len(candidates) > THREAD_CAP:
        for g in candidates[THREAD_CAP:]:
            if g.get("batch"):
                overflow.append({"key": "routine", "paths": [f["path"] for f in g["flags"]], "reason": "cap"})
                continue
            if g.get("group"):
                overflow.append({"key": "sensitive", "pattern": g["pattern"],
                                 "paths": [f["path"] for f in g["flags"]], "reason": "cap"})
                continue
            for f in g["flags"]:
                overflow.append({"key": f["key"], "path": f["path"], "reason": "cap"})
                if f["kind"] == "security" and f["sec"] not in unanchored_security:
                    unanchored_security.append(f["sec"])
            for f in g.get("riders") or []:
                overflow.append({"key": f["key"], "path": f["path"], "subject": subjects.get(f["path"]),
                                 "reason": "cap"})
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
            "group": succ.get("group"), "pattern": succ.get("pattern"), "advisory": bool(succ.get("advisory")),
            "riders": list(succ.get("riders") or []),
            "target": succ.get("target"), "summary": None, "design": succ.get("design"),
        })
        f_next += 1
    for g in candidates:
        keys = []
        for f in list(g["flags"]) + list(g.get("riders") or []):
            if f["key"] not in keys:
                keys.append(f["key"])
        out_of_plan = [f["path"] for f in g["flags"] if f["kind"] == "out-of-plan"]
        members = members_of([f["path"] for f in g["flags"]]) if g.get("batch") else None
        new.append({
            "f": f_next, "path": g["path"], "subject": g["subject"],
            "start_line": g["range"][0] if g["range"] else None, "line": g["range"][1] if g["range"] else None,
            "flags": keys, "supersedes": None, "predecessor": None,
            "reason": reasons.get(out_of_plan[0]) if out_of_plan else None,
            "plan": [f["text"] for f in g["flags"] if f["kind"] == "plan"],
            "security": [f["sec"] for f in g["flags"] if f["kind"] == "security"],
            "members": members,
            "group": g.get("group"), "pattern": g.get("pattern"),
            "advisory": _advisory(members) if members is not None else False,
            "riders": [dict({"path": f["path"], "subject": subjects.get(f["path"])},
                            **({"reason": reasons.get(f["path"])} if reasons_trusted else {}))
                       for f in g.get("riders") or []],
            "target": None, "summary": None, "design": None,
        })
        f_next += 1
    # Design threads (#206, #241): one per state with an unexplained difference, one per design group, one summary —
    # after every capped candidate, exempt from the cap.
    for d in design_new:
        a = d["anchor"]
        new.append({
            "f": f_next, "path": a["path"], "subject": a["subject"],
            "start_line": a["range"][0] if a["range"] else None, "line": a["range"][1] if a["range"] else None,
            "flags": d["flags"], "supersedes": d.get("supersedes"), "predecessor": d.get("predecessor"),
            "reason": None, "plan": [], "security": [], "members": None, "group": d.get("group"), "pattern": None,
            "advisory": False, "riders": [], "target": design_target, "summary": d.get("summary"),
            "design": d["design"],
        })
        f_next += 1
    # Each security thread's root carries its finding's `Claim:` line, the record's claim verbatim (SEC-1).
    for thread in new:
        thread["claims"] = {sec: claims[sec] for sec in thread["security"] if claims.get(sec)}
        # The files a `file` root links (#181, contract §2 "Thread template"): every path its hunk check covers,
        # at its gated-head name. Keys stay raw (matching and the batch/group re-cut compare raw paths), so the
        # projection happens here, at the display edge.
        thread["link_paths"] = (head_paths(file_check_paths(thread["path"], thread["flags"]), aliases)
                                if thread["subject"] == "file" else [])
    # A finding that has a thread in this post is not unanchored, whatever an old head of it said.
    threaded = {sec for t in new for sec in t["security"]}
    unanchored_security = [sec for sec in unanchored_security if sec not in threaded]
    return {
        "new": new,
        "matched": matched,
        "rejected": rejected,
        "overflow": overflow,
        "unanchored_security": unanchored_security,
        "unanchored_design": unanchored_design,
        "uncovered_design": uncovered_design,
        "retired": retired,
        "next_f": f_next,
    }


def _name_status(root, base, head):
    """``[(status, old, new)]`` from one `git diff --name-status -z -M <base> <head>` read (exact paths, no
    quoting; ``old`` is ``None`` unless the entry is a rename), or ``None`` when it cannot be read."""
    listing = process.run(
        ["git", "diff", "--no-color", "--no-ext-diff", "--name-status", "-z", "-M", base, head], cwd=root
    )
    if listing.returncode != 0:
        return None
    parts = [p for p in (listing.stdout or "").split("\0")]
    entries, i = [], 0
    while i < len(parts) and parts[i]:
        status = parts[i]
        if status[:1] == "R":
            entries.append((status, parts[i + 1], parts[i + 2]))
            i += 3
        else:
            entries.append((status, None, parts[i + 1]))
            i += 2
    return entries


def rename_aliases(root, base, head):
    """``{old path: its name at head}`` for every file renamed between ``base`` and ``head`` (#181); ``{}``
    when the listing cannot be read. The review tool's prep projects a thread's raw key paths to head names
    with it (:func:`head_paths`), as ``build_flags`` does with its own ``aliases``."""
    return {old: new for status, old, new in (_name_status(root, base, head) or []) if old is not None}


def head_paths(paths, aliases):
    """``paths`` at their head names (``aliases`` maps an old path to its new one), duplicates dropped, order
    kept (#181). A pre-rename key path then names the file once, at the name GitHub shows. Pure."""
    out = []
    for path in paths:
        path = (aliases or {}).get(path, path)
        if path not in out:
            out.append(path)
    return out


def _diff_files(root, base, head):
    """The gated diff's files at their gated-head names, each with its new-side hunks
    (``build_flags``'s ``files``). One `--name-status -z -M` read for the set (exact paths, no
    quoting), then one literal-pathspec `-U0` read per file; a deleted, binary or pure-rename file has
    no new-side lines. ``None`` when the set cannot be read."""
    entries = _name_status(root, base, head)
    if entries is None:
        return None
    files = {}
    for status, old, new in entries:
        if old is not None:
            if status == "R100":
                files[new] = {"hunks": [], "renamed_from": old}
                continue
            paths = [old, new]
        else:
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


# `code-links` (#245): the link kinds an anchor can get, and why one gets no link.
CODE_LINK_KINDS = ("line", "file", "blob", "code")
CODE_LINK_REASONS = ("no-pr", "changed-since-push", "unread", "diff-unread")
_CODE_ANCHOR_LINES_RE = re.compile(r"^(?P<start>[0-9]+)(?:-(?P<end>[0-9]+))?$")


def parse_code_anchor(text):
    """``(path, start, end)`` for a `code-links` anchor (#245) — `<path>:<start>-<end>`, `<path>:<line>` (``end``
    equal to ``start``) or `<path>` (both ``None``) — else ``None`` when malformed. The line part is the text after
    the last `:` only when it is digits, so a path holding `:` still parses; a zero line, a range that runs
    backwards or an empty path is malformed. Pure."""
    text = text or ""
    path, sep, tail = text.rpartition(":")
    match = _CODE_ANCHOR_LINES_RE.match(tail) if sep else None
    if match is None:
        return (text, None, None) if text else None
    start = int(match.group("start"))
    end = int(match.group("end")) if match.group("end") is not None else start
    if not path or start < 1 or end < start:
        return None
    return path, start, end


def build_code_links(root, anchors, pr_url=None, base_ref=None, pr_head=None):
    """Which link each anchor a supporting block names gets (#245; `skills/_shared/asking-the-user.md`
    "Supporting blocks"): ``(payload, notices, None)``, ``payload`` ``{"links": [{anchor, path, start, end, kind,
    url, reason}]}``, decided per anchor, the first row that applies:

    1. no ``pr_url`` → `code` / `no-pr` (no PR yet: the caller prints the code);
    2. ``path`` in ``root``'s working tree differs from ``pr_head`` → `code` / `changed-since-push` (the code the
       entry names exists nowhere on GitHub); a diff that cannot be read → `code` / `unread`;
    3. the PR's merge base or diff set cannot be read → `blob` / `diff-unread`;
    4. ``path`` is in the pushed diff and the range sits inside one new-side hunk → `line`, the line-anchored
       :func:`diff_link`;
    5. ``path`` is in the pushed diff otherwise (no range, a range outside every hunk, a deleted or binary file) →
       `file`, the file-level :func:`diff_link`;
    6. ``path`` is not in the pushed diff → `blob`, :func:`blob_link` at ``pr_head`` (a Files-view fragment for a
       file outside the diff scrolls to nothing).

    Rows 4–5 follow :func:`_anchor`'s rule. The merge base is `sensitive_paths.changed_paths`' (it fetches
    ``origin/<base_ref>``), the hunks :func:`_diff_files`' — the same `-U0` new-side spans a thread anchors on —
    both read once, and only when an anchor reaches row 3. ``url`` is ``None`` exactly when ``kind`` is `code`.
    ``anchors`` are pre-parsed ``(anchor, path, start, end)`` tuples (:func:`parse_code_anchor`)."""
    notices = []
    links = []
    diff = {}

    def pushed_files():
        if "files" not in diff:
            _, merge_base, error = sensitive_paths.changed_paths(root, base_ref, head=pr_head)
            files = _diff_files(root, merge_base, pr_head) if not error and merge_base else None
            if files is None:
                notices.append("CODE_LINKS_DIFF_UNREAD: the diff of %s against origin/%s could not be read (%s); "
                               "anchors outside it link to the file at the pushed head"
                               % (pr_head[:7], base_ref, error or "git diff failed"))
            diff["files"] = files
        return diff["files"]

    for anchor, path, start, end in anchors:
        row = {"anchor": anchor, "path": path, "start": start, "end": end}
        if not pr_url:
            links.append(dict(row, kind="code", url=None, reason="no-pr"))
            continue
        local = process.run(["git", "--literal-pathspecs", "diff", "--quiet", "--no-ext-diff", pr_head, "--", path],
                            cwd=root)
        if local.returncode != 0:
            reason = "changed-since-push" if local.returncode == 1 else "unread"
            links.append(dict(row, kind="code", url=None, reason=reason))
            continue
        files = pushed_files()
        if files is None:
            links.append(dict(row, kind="blob", url=blob_link(pr_url, pr_head, path, start, end),
                              reason="diff-unread"))
            continue
        info = files.get(path)
        if info is None:
            links.append(dict(row, kind="blob", url=blob_link(pr_url, pr_head, path, start, end), reason=None))
            continue
        if start is not None and _contained_hunk(info.get("hunks") or [], (start, end)) is not None:
            links.append(dict(row, kind="line", url=diff_link(pr_url, path, start, end), reason=None))
            continue
        links.append(dict(row, kind="file", url=diff_link(pr_url, path), reason=None))
    return {"links": links}, notices, None


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


def gather_design(scan, site_lines, target, recorded, explanations=None, classification=None, settled=False):
    """The design signals ``build_flags`` reads (#206), from parsed inputs. Pure. Returns ``(signals, rejected)``:
    ``signals`` ``{"design_target", "design_verdict", "design_states", "design_sites"}`` — ``design_verdict`` the
    scan's ``states`` when it read a section with no `code: AMBIGUOUS`, else ``None`` (unread: nothing retires,
    every recorded state is uncovered); ``design_sites`` ``{state: (path, rng)}`` from the model-written
    `<state-id> <path>[:<line>[-<line>]]` lines, the first per state (a later one is ``rejected``, `duplicate-state`),
    a bare path anchoring on its whole file. A
    line that does not parse is ``rejected`` (`design-sites`, `unparsed`). With ``target`` ``None`` there are no
    design inputs, and every value is empty. ``explanations`` and ``classification`` (#241) pass through as
    ``design_explanations`` / ``design_classification`` (:func:`_read_classification`'s pair)."""
    if target is None:
        return {"design_target": None, "design_verdict": None, "design_states": None, "design_sites": {},
                "design_explanations": [], "design_classification": None, "design_settled": False}, []
    rejected, sites = [], {}
    for line in site_lines or []:
        match = _DESIGN_SITE_RE.match(line.strip())
        if not match:
            rejected.append({"source": "design-sites", "line": line, "reason": "unparsed"})
            continue
        text = match.group("site")
        site = _site(text) or (None if re.search(r":[0-9]+(-[0-9]+)?$", text) else (text, None))
        if site is None:
            rejected.append({"source": "design-sites", "line": line, "reason": "unparsed"})
            continue
        if match.group("state") in sites:
            # One site per state: the first stands, and a later line is reported, never silently dropped.
            rejected.append({"source": "design-sites", "line": line, "reason": "duplicate-state"})
            continue
        sites[match.group("state")] = site
    readable = isinstance(scan, dict) and scan.get("present") and not scan.get("ambiguous")
    return {
        "design_target": target,
        "design_verdict": dict(scan["states"]) if readable else None,
        "design_states": list(recorded) if recorded is not None else None,
        "design_sites": sites,
        "design_explanations": list(explanations or []),
        "design_classification": classification,
        "design_settled": bool(settled),
    }, rejected


def gather_signals(pr_body, assess, security_site_lines, plan_thread_lines, narrowed=None, triage=None,
                   split_lines=(), design=None):
    """``build_flags``'s ``signals`` from the parsed inputs. Pure. ``sensitive_patterns`` maps each sensitive
    path to the first declaration entry it matches, in the assess payload's ``matches`` order (#181). A failed sensitive-path read
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
    does not parse is ``rejected``. ``subjects`` (#181) maps a `consequential` out-of-plan file the verdict gave a
    subject — another out-of-plan file, not routine — to that subject, when it carries no sensitive, security or
    judgment signal. ``design`` (#206) is ``{"scan", "site_lines", "target", "states"}`` for :func:`gather_design`;
    ``None`` adds no design signal."""
    rejected = []
    payload = assess.get("payload", assess) if isinstance(assess, dict) else {}
    sensitive = (payload.get("sensitive") or {}) if isinstance(payload, dict) else {}
    sensitive_paths, sensitive_patterns = [], {}
    for match in sensitive.get("matches") or []:
        path = match.get("path") if isinstance(match, dict) else None
        if path and path not in sensitive_paths:
            sensitive_paths.append(path)
        # The first declaration entry a path matches (#181): `matches` is in declaration order (self-edit pairs
        # first). A match with no pattern names no group, so its file keeps a per-file thread.
        pattern = match.get("pattern") if isinstance(match, dict) else None
        if path and path not in sensitive_patterns and isinstance(pattern, str) and pattern:
            sensitive_patterns[path] = pattern
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
        if match.group("id") in sites:
            # One site per finding (#206): the first stands, and a later line is reported, never silently dropped.
            rejected.append({"source": "security-sites", "line": line, "reason": "duplicate-finding"})
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
    routine, routine_kinds, subjects = [], {}, {}
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
        # A consequential test or fixture whose subject is another out-of-plan file (#181): a rider candidate,
        # which `build_flags` puts on its subject's thread only when that thread posts this run. A file carrying
        # a sensitive, security or judgment signal never rides.
        for path in out_of_plan:
            verdict = verdicts.get(path) or {}
            subject = verdict.get("subject")
            if (verdict.get("class") == "consequential" and subject and subject != path and subject in out_of_plan
                    and subject not in routine_kinds and path not in flagged):
                subjects[path] = subject
    design_signals, design_rejected = gather_design(
        (design or {}).get("scan"), (design or {}).get("site_lines"), (design or {}).get("target"),
        (design or {}).get("states"), (design or {}).get("explanations"), (design or {}).get("classification"),
        bool((design or {}).get("settled")))
    rejected.extend(design_rejected)
    return dict(design_signals, **{
        "sensitive": sensitive_paths,
        "out_of_plan": [p for p in out_of_plan if p not in routine_kinds],
        "routine": routine,
        "routine_kinds": routine_kinds,
        "subjects": subjects,
        "sensitive_patterns": sensitive_patterns,
        "triage_read": bool(triage and triage.get("present") and not triage.get("ambiguous")),
        "splits": splits,
        "judgment": judgment,
        "security": security,
        "plan": plan,
        "reasons": reasons,
        "claims": claims,
        "rejected": rejected,
    })


def filter_sources(signals, rigor_level, full_rules):
    """``signals`` with every kind outside ``rigor.STEPS[rigor_level]["thread_sources"]`` emptied (#198): where a
    rigor level narrows which code gets a thread (``blast_radius.build_assess`` has already dropped the judgment's
    signals under a level whose ``judgment`` step is off). Pure. Nothing is dropped under ``full``, or when
    ``full_rules`` holds — a sensitive match or a failed sensitive read puts the pass on ``full`` rules, and a
    failed read leaves the sensitive signal empty, so the filter keys on ``full_rules``, never on that signal —
    or when ``full_rules`` is ``None`` (an assess envelope without the key: failures resolve upward). Dropping
    ``out-of-plan`` empties the triage's inputs too (``routine`` and the riders' ``subjects``), so no triage
    result reaches a thread. Dropping a kind never retires a thread: an absent signal leaves an existing one
    as it is (a batch member stays, `build_flags`)."""
    if full_rules is not False or rigor_level == "full":
        return signals
    keep = set(rigor.STEPS[rigor_level]["thread_sources"])
    out = dict(signals)
    if "security" not in keep:
        out["security"], out["claims"] = [], {}
    if "sensitive" not in keep:
        out["sensitive"], out["sensitive_patterns"] = [], {}
    if "judgment" not in keep:
        out["judgment"] = []
    if "plan" not in keep:
        out["plan"] = []
    if "out-of-plan" not in keep:
        out["out_of_plan"], out["subjects"] = [], {}
        out["routine"], out["routine_kinds"] = [], {}
    if "routine" not in keep:
        out["routine"], out["routine_kinds"] = [], {}
    if "design" not in keep:
        # No level drops it today (#206, `design` is in every level's sources); kept so the filter stays total.
        out["design_target"], out["design_verdict"], out["design_states"], out["design_sites"] = None, None, None, {}
        out["design_explanations"], out["design_classification"], out["design_settled"] = [], None, False
    return out


def design_only_signals(signals):
    """``signals`` with every kind but `design` emptied (#206, ``pause --design-only``): the final pass's design
    review gathers only the comparison's differences, and ``build_flags`` re-cuts no batch or sensitive group (a design
    group and the summary are re-cut as on any run, #241) and gives only
    design heads a successor. Pure."""
    out = dict(signals)
    out.update({"security": [], "claims": {}, "sensitive": [], "sensitive_patterns": {}, "judgment": [], "plan": [],
                "out_of_plan": [], "subjects": {}, "routine": [], "routine_kinds": {}, "splits": [],
                "design_only": True})
    return out


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


def is_waiting(thread):
    """Whether a `read_threads` thread waits on a person: not `replaced`, and `unreviewed` or an
    `operator-thread` the resolver has answered (`pending_reply`). A thread a trusted review stamp `retired`
    (#206) waits on no one."""
    if thread.get("replaced") or thread.get("retired"):
        return False
    state = thread.get("state")
    return state == "unreviewed" or (state == "operator-thread" and bool(thread.get("pending_reply")))


def in_phase(thread, phase):
    """Whether a `read_threads` thread is one phase ``phase``'s pause posted: its root stamp's `phase`."""
    return thread.get("phase") == phase


def phase_waiting(threads, new, phase, retired=()):
    """How many threads `/ctrl-freaq:review --phase <phase>` will walk once ``new`` posts: the phase's threads
    already waiting, less any a new thread supersedes and any this run retires (``retired``, `flags`' entries or
    their refs — the review that stamps them posts after this count is taken, #206), plus every new thread (each
    stamped with the phase and `unreviewed` by construction)."""
    superseded = {entry.get("supersedes") for entry in new}
    superseded |= {r.get("ref") if isinstance(r, dict) else r for r in retired}
    existing = [t for t in threads
                if in_phase(t, phase) and is_waiting(t) and "F%s" % t.get("f") not in superseded]
    return len(existing) + len(new)


def _no_spec(base, **extra):
    """The payload of a run that writes no spec: nothing new, matched, retired or exempt."""
    return dict({"diff_available": False, "spec_path": None, "base": base, "new": [], "matched": [],
                 "rejected": [], "overflow": [], "unanchored_security": [], "unanchored_design": [],
                 "uncovered_design": [], "retired": [], "exempt": [], "generated_read": None, "next_f": None},
                **extra)


def _read_design(design_verdict, design_sites, design_target, design_states, notices):
    """The design inputs for :func:`gather_signals` (#206), or ``None`` with no ``design_target``. The verdict is
    scanned by `parse.scan_design_comparison`; one that reads no section, or `code: AMBIGUOUS`, is the
    `DESIGN_VERDICT_UNREAD` notice, and every recorded state is then uncovered. ``design_states`` is
    `design_record.py stage`'s envelope (or its payload); its ``states[].id`` are the recorded states, and its
    ``fingerprint`` (the record the captures were staged from) must equal ``design_target``, else — or with no
    ``fingerprint`` — the `DESIGN_TARGET_MISMATCH` notice and the verdict reads as unread (R1.10). An unreadable
    file is a usage error, as every other input's is."""
    if design_target is None:
        return None
    try:
        scan = parse.scan_design_comparison(Path(design_verdict).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (design_verdict, exc))
        sys.exit(EXIT_USAGE_ERROR)
    if not scan["present"] or scan["ambiguous"]:
        notices.append("%s: %s holds %s; no design thread retires this run, and every recorded state is uncovered"
                       % (DESIGN_VERDICT_UNREAD, design_verdict,
                          "`code: AMBIGUOUS`" if scan["ambiguous"] else "no `## Comparison` section"))
    staged = _read_json(design_states)
    staged = staged.get("payload", staged) if isinstance(staged, dict) else {}
    recorded = [st.get("id") for st in (staged.get("states") or []) if isinstance(st, dict) and st.get("id")]
    if staged.get("fingerprint") != design_target:
        # The pin (#206, R1.10): the captures were staged from the record whose fingerprint is the target, or the
        # comparison read some other record. No fingerprint (an unpinned staging) proves nothing, so it fails the
        # same way: the verdict reads as unread — nothing posts or retires, and every recorded state is uncovered.
        notices.append("%s: %s was staged from the record %s, not the target %s; no design thread posts or retires "
                       "this run, and every recorded state is uncovered"
                       % (DESIGN_TARGET_MISMATCH, design_states, staged.get("fingerprint") or "(unpinned)",
                          design_target))
        scan = dict(scan, present=False)
    return {"scan": scan, "site_lines": _read_lines(design_sites), "target": design_target, "states": recorded,
            "staged": [st for st in (staged.get("states") or []) if isinstance(st, dict)]}


# ---- the departure classification's inputs (#241, `skills/_shared/design-check.md` steps 3.1 and 3.2) ----

DESIGN_DEPARTURES_UNTRUSTED = "DESIGN_DEPARTURES_UNTRUSTED"
DESIGN_CLASSIFICATION_UNREAD = "DESIGN_CLASSIFICATION_UNREAD"
DESIGN_DIFFERENCES_FILE = "design-differences.md"
DESIGN_EXPLANATIONS_FILE = "design-explanations.md"
DESIGN_CLASSIFICATION_FILE = "design-classification.md"


def verdict_differences(design_verdict, recorded):
    """The differences of a scanned comparison verdict (`parse.scan_design_comparison`'s ``states``), as
    ``[{"id", "state", "aspect", "text"}]``: every `differs` state's differences, in ``recorded`` order (the record's
    states), then any state the record does not list, by id. Pure."""
    states = dict(design_verdict or {})
    order = [st for st in (recorded or []) if st in states] + sorted(st for st in states if st not in (recorded or []))
    out = []
    for state in order:
        entry = states.get(state) or {}
        if entry.get("verdict") != "differs":
            continue
        for d in entry.get("differences") or []:
            out.append({"id": d.get("id"), "state": state, "aspect": d.get("aspect"), "text": d.get("text")})
    return out


def build_explanations(pr_body, plan_text, recorded, trusted):
    """The explanations a difference may be classified under (#241), as ``(explanations, notices, dropped)``:
    ``[{"id", "source", "states", "aspect", "text", "reason"}]`` in source order — ``D<n>`` from the PR body's
    `## Design departures` (``states`` its one state, its ``aspect``, ``text`` the departure, its ``reason``) and
    ``X<n>`` from its `## Decided deviations` (positional; ``states`` ``[]`` and ``aspect`` ``None``, which cover by
    existence alone; ``text`` `` `<path>` — <deviation> ``, ``reason`` the operator's answer), both only when
    ``trusted`` — else dropped with the `DESIGN_DEPARTURES_UNTRUSTED` notice; and ``U<n>`` from the plan's agreed UI
    deviations (`design_record.ui_deviations`, positional over that list, kept only when it names a recorded state,
    ``states`` the recorded ones it names, ``aspect`` ``None``, ``reason`` ``None``) — the plan is a trusted-author
    read. Returns ``(explanations, notices, dropped)``: ``dropped`` true when an untrusted body's departures or
    decided deviations were left out (the notice's condition), so `flags` never retires a summary on that run. Pure."""
    notices, explanations = [], []
    departures = parse.scan_design_departures(pr_body)
    decided = parse.scan_decided_deviations(pr_body)
    if trusted:
        for entry in departures["entries"]:
            explanations.append({"id": entry["id"], "source": "departure", "states": [entry["state"]],
                                 "aspect": entry["aspect"], "text": entry["departure"], "reason": entry["reason"]})
    elif departures["entries"] or decided["entries"]:
        notices.append("%s: the PR body's `## Design departures` and `## Decided deviations` are not trusted (the PR "
                       "is another author's, or its author's association could not be read); no departure or decided "
                       "deviation explains a design difference this run, so every one not in the plan gates on its own"
                       % DESIGN_DEPARTURES_UNTRUSTED)
    recorded = list(recorded or [])
    for n, deviation in enumerate(design_record.ui_deviations(plan_text), start=1):
        states = [st for st in deviation["states"] if st in recorded]
        if states:
            explanations.append({"id": "U%d" % n, "source": "plan", "states": states, "aspect": None,
                                 "text": deviation["text"], "reason": None})
    if trusted:
        for n, entry in enumerate(decided["entries"], start=1):
            explanations.append({"id": "X%d" % n, "source": "decided", "states": [], "aspect": None,
                                 "text": "`%s` — %s" % (entry["path"], entry["deviation"]), "reason": entry["answer"]})
    return explanations, notices, bool(not trusted and (departures["entries"] or decided["entries"]))


def explanation_sources_digest(pr_body):
    """The SHA-256 over the explanation sources a PR body carries (#241): its parsed `## Design departures` and
    `## Decided deviations` entries, canonically serialised. `explain` records it, and `flags` / `pause` recompute it
    from their own ``--pr-body``, so a classification is never applied with explanations from another body — while an
    edit elsewhere in the body (S4.5's `## Security review`, the ledger) leaves it unchanged. Pure."""
    data = json.dumps([parse.scan_design_departures(pr_body)["entries"],
                       parse.scan_decided_deviations(pr_body)["entries"]],
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def differences_text(differences):
    """The classifier's differences file: one `` - `<state-id>#<n>` · <aspect|unknown> — <text> `` line each."""
    lines = ["## Differences", ""]
    lines += ["- `%s` · %s — %s" % (d["id"], d["aspect"] or "unknown", d["text"]) for d in differences]
    return "\n".join(lines) + "\n"


def explanations_text(explanations):
    """The classifier's explanations file: one ``- <id> · <states|any> · <aspect|any> — <text>[ — reason: <reason>]``
    line each, the states backticked and comma-separated."""
    lines = ["## Explanations", ""]
    for e in explanations:
        states = ", ".join("`%s`" % st for st in e["states"]) if e["states"] else "any"
        lines.append("- %s · %s · %s — %s%s" % (e["id"], states, e["aspect"] or "any", e["text"],
                                                " — reason: %s" % e["reason"] if e["reason"] else ""))
    return "\n".join(lines) + "\n"


def run_explain(design_verdict, design_states, pr_body_path, plan_path, trusted, out_dir):
    """The `explain` subcommand (#241, design-check step 3.1): stage the departure classification's two inputs and
    return the explanation envelope, ``(payload, notices)``. No `gh` call; always `ok`. The differences come from the
    comparison's verdict at the recorded states of ``design_states`` (`design_record.py stage`'s envelope); an unread
    verdict has none, with the `DESIGN_VERDICT_UNREAD` notice. ``plan_path`` may be ``None`` (no plan read): no ``U``
    explanation. An unreadable input file is a usage error, as every other subcommand's is."""
    notices = []
    try:
        verdict_text = Path(design_verdict).read_text(encoding="utf-8")
        pr_body = Path(pr_body_path).read_text(encoding="utf-8")
        plan_text = Path(plan_path).read_text(encoding="utf-8") if plan_path else ""
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s\n" % exc)
        sys.exit(EXIT_USAGE_ERROR)
    scan = parse.scan_design_comparison(verdict_text)
    staged = _read_json(design_states)
    staged = staged.get("payload", staged) if isinstance(staged, dict) else {}
    recorded = [st.get("id") for st in (staged.get("states") or []) if isinstance(st, dict) and st.get("id")]
    if not scan["present"] or scan["ambiguous"]:
        notices.append("%s: %s holds %s; it reports no difference to classify"
                       % (DESIGN_VERDICT_UNREAD, design_verdict,
                          "`code: AMBIGUOUS`" if scan["ambiguous"] else "no `## Comparison` section"))
        differences = []
    else:
        differences = verdict_differences(scan["states"], recorded)
    explanations, explain_notices, dropped = build_explanations(pr_body, plan_text, recorded, trusted)
    notices.extend(explain_notices)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {"differences": str(out / DESIGN_DIFFERENCES_FILE), "explanations": str(out / DESIGN_EXPLANATIONS_FILE),
             "classification": None}
    Path(files["differences"]).write_text(differences_text(differences), encoding="utf-8")
    Path(files["explanations"]).write_text(explanations_text(explanations), encoding="utf-8")
    classification_file = out / DESIGN_CLASSIFICATION_FILE
    if not differences or not explanations:
        # Nothing to classify (#241): with no difference, or no explanation, the coverage rule explains nothing, so the
        # script writes the all-unexplained verdict itself and the caller dispatches no classifier. Both inputs still
        # go to `flags` together, so on a settled run (with differences, only when no untrusted departure was dropped)
        # a summary that no longer explains anything retires.
        classification_file.write_text("## Classification\n\n" + "".join(
            "- `%s` · unexplained\n" % d["id"] for d in differences), encoding="utf-8")
        files["classification"] = str(classification_file)
    elif classification_file.exists():
        # A stale verdict from an earlier run must never stand in for a dispatch that failed this run.
        classification_file.unlink()
    # #241: the explanation sources the body carried, so `flags` / `pause` refuse a classification whose departures or
    # decided deviations have changed since (one removed, a line rewritten) rather than explaining by a stale one.
    return {"differences": differences, "explanations": explanations, "files": files,
            "sources_sha256": explanation_sources_digest(pr_body), "departures_dropped": dropped}, notices


def _covers(explanation, difference):
    """Whether ``explanation`` may cover ``difference`` (#241): a departure needs the same state and the same aspect
    (so a difference with no aspect is never one's), a plan deviation the difference's state among its states, a
    decided deviation only its existence."""
    source = explanation.get("source")
    if source == "departure":
        return (difference.get("state") in (explanation.get("states") or [])
                and difference.get("aspect") is not None and difference.get("aspect") == explanation.get("aspect"))
    if source == "plan":
        return difference.get("state") in (explanation.get("states") or [])
    return source == "decided"


def classify_differences(differences, explanations, classification):
    """Each difference's class (#241, the coverage rule): ``{difference id: {"class", "by"}}``. A difference is
    ``explained`` only when ``classification`` (`parse.scan_design_classification`'s result, readable: present, not
    ambiguous) calls it explained and every explanation it cites exists and may cover it (:func:`_covers`); a
    missing, unparsed, conflicting or disallowed verdict, and any difference when ``classification`` is ``None`` or
    unreadable, is ``unexplained`` (fail closed: more threads, never fewer). Pure."""
    by_id = {e.get("id"): e for e in explanations or []}
    readable = isinstance(classification, dict) and classification.get("present") and not classification.get("ambiguous")
    verdicts = (classification or {}).get("verdicts") or {} if readable else {}
    out = {}
    for d in differences or []:
        verdict = verdicts.get(d.get("id")) or {}
        cited = list(verdict.get("by") or [])
        ok = (verdict.get("class") == "explained" and cited
              and all(c in by_id and _covers(by_id[c], d) for c in cited))
        out[d.get("id")] = {"class": "explained", "by": cited} if ok else {"class": "unexplained", "by": []}
    return out


def summary_hash(target, used):
    """A design summary's 12-hex hash (#241): the SHA-256 over the run's ``target`` and, per explanation in ``used``
    (``[(explanation, [(state, aspect), …])]``), its id, source, states, aspect, text and reason with the sorted
    (state, aspect) pairs it covers — never a difference's text, so the comparison's wording moves nothing, while any
    change to what the operator approves re-cuts the summary. Pure."""
    items = []
    for explanation, pairs in sorted(used, key=lambda u: str(u[0].get("id"))):
        items.append([explanation.get(k) for k in ("id", "source", "states", "aspect", "text", "reason")]
                     + [sorted([st, aspect or ""] for st, aspect in set(map(tuple, pairs)))])
    data = json.dumps([target, items], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]


def _read_classification(explanations_path, classification_path, design_verdict, notices, pr_body=None):
    """The classification inputs `flags` / `pause` take (#241), as ``(explanations, classification, settled)``;
    ``(None, None, False)`` with neither path (a caller that ran no classification: every difference is unexplained). The explanations file is
    `explain`'s envelope (or its payload). Its ``differences`` must equal this run's verdict's (``design_verdict``,
    scanned states), or the classification was made over another verdict; and its ``sources_sha256`` must be
    :func:`explanation_sources_digest` of ``pr_body`` (this run's `--pr-body` text), or its explanations were read from
    other departures or decided deviations: either reads as unread. An unreadable or ambiguous
    classification, or a mismatch, is the `DESIGN_CLASSIFICATION_UNREAD` notice, and every difference is then
    unexplained. Returns ``(explanations, classification, settled)``: ``settled`` is whether this run decided what is
    explained — false with no inputs, on any unread reason, when the envelope dropped untrusted departures, or when
    the classification left a line unparsed or a difference without a verdict — and, with every recorded state
    covered, only a settled classification (or a comparison with no difference at all) lets `_design_flags` move a
    design summary (retire or re-cut it): a failed step never retires a thread (#241)."""
    if not explanations_path:
        return None, None, False
    envelope = _read_json(explanations_path)
    envelope = envelope.get("payload", envelope) if isinstance(envelope, dict) else {}
    explanations = [e for e in envelope.get("explanations") or [] if isinstance(e, dict)]
    try:
        scan = parse.scan_design_classification(Path(classification_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (classification_path, exc))
        sys.exit(EXIT_USAGE_ERROR)
    staged = [{k: d.get(k) for k in ("id", "state", "aspect", "text")}
              for d in envelope.get("differences") or [] if isinstance(d, dict)]
    current = sorted(verdict_differences(design_verdict, None), key=lambda d: d["id"])
    if design_verdict is None:
        # An unread comparison (`DESIGN_VERDICT_UNREAD`, or `DESIGN_TARGET_MISMATCH`) already names why: there is no
        # verdict to have classified, so the run is unsettled with no second, misleading notice (#241, P3.5).
        return explanations, None, False
    reason = None
    if not scan["present"] or scan["ambiguous"]:
        reason = "holds %s" % ("`code: AMBIGUOUS`" if scan["ambiguous"] else "no `## Classification` section")
    elif sorted(staged, key=lambda d: str(d["id"])) != current:
        reason = "was made over other differences than this run's comparison reports"
    elif pr_body is None or envelope.get("sources_sha256") != explanation_sources_digest(pr_body):
        reason = "was made from explanations whose departures or decided deviations differ from this run's PR body's"
    if reason is not None:
        notices.append("%s: %s %s; every design difference this run is unexplained and gates on its own"
                       % (DESIGN_CLASSIFICATION_UNREAD, classification_path, reason))
        return explanations, None, False
    settled = (not envelope.get("departures_dropped") and not scan["unparsed"]
               and all(d["id"] in scan["verdicts"] for d in staged))
    return explanations, scan, settled


def record_capture(reference):
    """The record side of a design thread's captures at its fallback value (#237), from the record's own capture
    reference (``design-states.json``'s ``states[].capture``): ``url`` when it is, or is a Markdown image of, an
    attachment URL (:func:`is_attachment_url`, within :func:`design_record.allowed_capture_url`); ``path`` when it is a
    repo path; ``none`` otherwise (no reference, an off-allowlist or non-attachment URL, or a path a root cannot link).
    A ``none`` record whose staging fetched a copy still uploads it (:func:`plan_upload`), so a judged capture shows
    as an image once that upload lands [operator decision, #237 cold read]. Pure."""
    text = (reference or "").strip() if isinstance(reference, str) else ""
    image = design_record.MARKDOWN_IMAGE_RE.match(text)
    target = image.group(1) if image else text
    if not target:
        return {"kind": "none", "ref": None}
    value = {"kind": "url" if _URL_SCHEME_RE.match(target) else "path", "ref": target}
    try:
        _check_capture("record", value)
    except ValueError:
        return {"kind": "none", "ref": None}
    return value


def build_capture(build_dir, state):
    """The build side at its fallback value (#237): ``local`` with the absolute path of the one
    ``<build_dir>/<state>.<ext>`` file design-check step 2 wrote (the first by name, were there several), else
    ``none`` — the build does not reach the state. ``build_dir`` is the caller's ``--design-build``, never derived
    from ``--scratch``: a resolver pause's scratch is ``<facts.scratch>/pause-<N>``, not where step 2 wrote."""
    try:
        files = sorted(p for p in Path(build_dir).glob(state + ".*") if p.is_file())
    except OSError:
        files = []
    for found in files:
        value = {"kind": "local", "ref": str(found.resolve())}
        try:
            _check_capture("build", value)
        except ValueError:
            continue
        return value
    return {"kind": "none", "ref": None}


def fill_captures(new, staged_states, build_dir, verdict, target):
    """Set ``captures`` (#237) on every thread in ``new`` carrying a `design:` key and stamped with this run's comparison
    ``target`` — fresh and successor alike — at its fallback values: the record's from ``staged_states``
    (``design-states.json``'s ``states``), the build's from ``build_dir``, the caption the thread's own unexplained
    differences for that state (its ``design["differences"]``, #241; ``None`` with none). Called only when the comparison ran and was read: a
    design successor posted with no comparison gets no field, since the only captures it could show are an earlier
    head's. Nor does a successor that keeps an older record's fingerprint (its ``target`` is not ``target``): the staged
    record and the verdict are another comparison's, so its root would misrepresent the one it continues [operator
    decision on the resolver's Decision card, #237 review round 3; a `## Plan override` on the PR]."""
    by_id = {st.get("id"): st for st in staged_states or [] if isinstance(st, dict)}
    for thread in new:
        state = next((design_key_state(k) for k in thread.get("flags") or [] if design_key_state(k)), None)
        if state is None or thread.get("target") != target:
            continue
        # #241: a design thread lists only its state's unexplained differences, so its caption does too; a design
        # group shows its first member's captures (verdict order), and a summary, which carries no `design:` key, none.
        own = (thread.get("design") or {}).get("differences") or []
        caption = "; ".join(d["text"] for d in own if d.get("state") == state) or None
        thread["captures"] = {
            "state": state,
            "record": record_capture((by_id.get(state) or {}).get("capture")),
            "build": build_capture(build_dir, state),
            "caption": caption,
        }


def _out_of_plan_only(thread):
    """Whether ``thread`` is a pipeline thread with a non-empty flag list whose every key is `out-of-plan:` or
    `routine:` — a per-file out-of-plan head or a batch head, which a review pause leaves to the evaluator (#218)."""
    keys = thread.get("flags") or [] if isinstance(thread, dict) else []
    return (isinstance(thread, dict) and thread.get("origin") == "pipeline" and bool(keys)
            and all(flag_key_parts(k)[0] in ("out-of-plan", "routine") for k in keys))


def run_flags(root, pr_body_path, assess_path, threads_path, base_ref, head, scratch, security_sites=None,
              plan_threads=None, triage=None, splits=None, reasons_trusted=False, *, threads=None,
              anchor_base=None, out_of_plan=True, phase=None, rigor_level="full", design_verdict=None,
              design_sites=None, design_target=None, design_states=None, design_only=False, design_build=None,
              design_explanations=None, design_classification=None):
    """The `flags` subcommand: read the inputs, run :func:`build_flags`, and write the `review-post`
    spec (`<scratch>/review-spec.json`) naming one body path per new thread and the review body, which
    the model then writes. Returns ``(payload, notices)``.

    The keyword-only arguments are :func:`run_pause`'s (#170): ``threads`` the thread list in place of
    ``threads_path``, ``anchor_base`` the commit the anchor diff starts at in place of the assess payload's
    ``range.base`` (it anchors the diff only: the exemptions :func:`narrow` reads take the payload's own range, so
    a caller with an anchor of its own passes ``out_of_plan`` false, as :func:`run_pause` does), ``out_of_plan``
    (#218) false to drop the out-of-plan signal — no exemption read
    (:func:`narrow`), no out-of-plan, routine or rider flag, the raw out-of-plan list kept only as
    ``group_blockers`` so such a sensitive file keeps a per-file thread, and every pipeline head whose keys are all
    `out-of-plan:` or `routine:` left to the evaluator (it still reserves its `F` id) — and ``phase``, which goes
    into the spec — and with which the spec is written even when nothing is new, since every pause posts its
    review. The `flags` CLI passes none of them.

    ``rigor_level`` (#198, ``facts.rigor.level``; ``full`` when not given) narrows the thread sources through
    :func:`filter_sources`, read against the assess payload's ``full_rules``; the stricter of it and the level the
    assess call ran at (the payload's ``rigor``) is the one applied.

    The design inputs (#206) are the comparison's verdict (``design_verdict``), its sites (``design_sites``), the
    record fingerprint it read (``design_target``) and `design_record.py stage`'s payload (``design_states``); all
    but the sites come together or not at all. ``design_only`` (``pause --design-only``) gathers design flags
    alone; ``assess_path`` may then be ``None``. ``design_build`` (#237) is the directory design-check step 2 wrote the
    build's captures to: with it and a readable verdict, every design thread in the spec stamped with this run's target
    carries ``captures`` at its fallback values (:func:`fill_captures`); without it, none does.

    ``design_explanations`` (`explain`'s envelope) and ``design_classification`` (the departure classification's
    verdict) are #241's two optional inputs, given together and only with the design inputs: with them each difference
    the comparison reports is explained or unexplained (:func:`classify_differences`); without them every difference
    is unexplained."""
    notices = []
    assess = _read_json(assess_path) if assess_path else {}
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
    if out_of_plan:
        narrowed, generated_read, narrow_notices = narrow(root, payload, head, scratch)
        notices.extend(narrow_notices)
    else:
        # A review pause (#218) threads no out-of-plan file, so it reads no exemption.
        narrowed, generated_read = {"remaining": [], "exempt": []}, None
    design = _read_design(design_verdict, design_sites, design_target, design_states, notices)
    if design is not None:
        scanned = design["scan"]
        readable = scanned.get("present") and not scanned.get("ambiguous")
        explanations, classification, settled = _read_classification(
            design_explanations, design_classification, dict(scanned["states"]) if readable else None, notices,
            pr_body=pr_body)
        design["explanations"], design["classification"] = explanations, classification
        design["settled"] = settled
    signals = gather_signals(pr_body, assess, security_site_lines, plan_thread_lines, narrowed=narrowed,
                             triage=_read_triage(triage, notices), split_lines=split_lines, design=design)
    if not out_of_plan:
        # The raw out-of-plan list still keeps a sensitive file out of a group at the pause, as the evaluator's
        # out-of-plan flag later does, so the evaluator matches the pause's per-file `sensitive:` thread instead of
        # re-cutting a group; the evaluator then posts the file's out-of-plan flag as a thread of its own.
        signals["group_blockers"] = [p for p in (payload.get("out_of_plan") or []) if isinstance(p, str)]
    full_rules = payload.get("full_rules") if isinstance(payload, dict) else None
    # The stricter of `--rigor` and the level the assess call ran at: the two are one fact with two inputs,
    # and a mismatch must never narrow the sources below what the assess call judged.
    assessed = payload.get("rigor") if isinstance(payload, dict) else None
    if assessed in rigor.STEPS and rigor.LEVELS.index(assessed) < rigor.LEVELS.index(rigor_level):
        rigor_level = assessed
    signals = filter_sources(signals, rigor_level, full_rules if isinstance(full_rules, bool) else None)
    if design_only:
        signals = design_only_signals(signals)
    signals["reasons_trusted"] = bool(reasons_trusted)
    # Every listed thread reserves its `F` id, a head the pause leaves out included.
    next_f = max([0] + [max(t.get("f") or 0, _root_stamp_f(t)) for t in threads if isinstance(t, dict)]) + 1
    if not out_of_plan:
        # A pipeline head whose every key is `out-of-plan:` or `routine:` (a per-file out-of-plan head, or a batch)
        # is the evaluator's: the pause neither matches, succeeds, re-cuts nor retires it.
        threads = [t for t in threads if not _out_of_plan_only(t)]
    result = build_flags(
        files, threads, signals,
        lambda from_sha, path, rng: map_range_between(root, from_sha, head, path, rng) if from_sha else None,
        next_f,
    )
    if design is not None and design_build is not None and signals.get("design_verdict") is not None:
        fill_captures(result["new"], design["staged"], design_build, signals["design_verdict"], design["target"])
    spec_path = None
    # The `F` ids this run retired (#206, R1.11): the review it posts stamps them, so every reader sees the
    # retirement. A head with no `F` (a URL ref) cannot be named in the stamp and is left out.
    retires = retires_field([r["ref"] for r in result["retired"] if re.fullmatch(_F_REF, r.get("ref") or "")])
    if result["new"] or phase is not None or retires:
        out = Path(scratch)
        out.mkdir(parents=True, exist_ok=True)
        for thread in result["new"]:
            thread["body"] = str(out / ("thread-F%d.md" % thread["f"]))
        spec = {
            "head": head,
            **({"phase": phase} if phase is not None else {}),
            **({"retires": retires} if retires else {}),
            "review_body": str(out / "review-body.md"),
            "threads": [
                dict({k: t[k] for k in ("f", "path", "subject", "start_line", "line", "flags", "supersedes", "group",
                                        "advisory", "target", "link_paths", "body")},
                     # #237: only a design thread the comparison read carries the field, so every other spec is
                     # byte-identical to one written before it.
                     **({"captures": t["captures"]} if t.get("captures") is not None else {}),
                     # #241: only a design summary carries its hash, so every other spec is byte-identical.
                     **({"summary": t["summary"]} if t.get("summary") is not None else {}))
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
              security_sites=None, plan_threads=None, rigor_level="full", design_verdict=None, design_sites=None,
              design_target=None, design_states=None, design_only=False, design_build=None,
              design_explanations=None, design_classification=None):
    """The `pause` subcommand (#170, `skills/resolver/references/phase-continuation.md` §2.2): the review a
    resolver review pause posts. The threads are read here, at the pushed ``head`` — the resolver's prep read
    them before the push, and a fresh first phase had no PR then. The signals are the phase's own (S5.2's
    shipped-diff ``assess``); the anchors are the PR's whole diff, from the merge base of ``origin/<base_ref>``
    and ``head``, so an earlier pause's thread whose code this phase moved gets a successor rather than
    retiring `path-left-diff`. No out-of-plan signal (#218): the pause opens no out-of-plan, routine or rider
    thread (a superseded head carrying another key beside its `out-of-plan:` ones still gets a successor keeping
    every key), reads no exemption, and leaves every head whose keys are all `out-of-plan:` or `routine:` (a batch
    included) to the evaluator's S4.6 `flags`. With the read and the diff available, the spec is always written,
    carrying ``phase``, even when nothing anchors. A failed read writes no spec (`REVIEW_THREADS_UNAVAILABLE`):
    without every thread each flag would look new and reuse a live `F` id. Returns ``(payload, notices)``:
    `flags`' payload plus ``threads_available``, ``phase`` and ``waiting`` (:func:`phase_waiting`), the count
    behind the Review card's offer of the in-chat review, ``None`` when no spec was written, and
    ``waiting_truncated``, true when a cut-short read makes that count only a floor.

    The design inputs (#206) are `flags`' own, ``design_build`` (#237) among them. ``design_only`` is the final pass's design review: only design flags
    are gathered, no batch or group is re-cut, and ``assess_path`` may be ``None`` (the final pass runs no
    shipped-diff `assess`)."""
    # The local inputs first, so a wrong path is a usage error before any GitHub read.
    if assess_path:
        _read_json(assess_path)
    try:
        Path(pr_body_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (pr_body_path, exc))
        sys.exit(EXIT_USAGE_ERROR)
    # Read for their usage errors only; `run_flags` reads them again.
    _read_lines(security_sites)
    _read_lines(plan_threads)
    _read_lines(design_sites)
    for path in (design_verdict, design_states, design_explanations, design_classification):
        if path:
            try:
                Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s: %s\n" % (path, exc))
                sys.exit(EXIT_USAGE_ERROR)
    read, notices = read_threads(repo, pr, root, head, scratch)
    if not read.get("available"):
        return _no_spec(None, diff_available=None, threads_available=False, phase=phase, waiting=None,
                        waiting_truncated=False), notices
    merge_base, _, error = _pr_range(root, base_ref, head)
    if merge_base is None:
        notices.append("REVIEW_THREADS_DIFF_UNAVAILABLE: the PR's diff origin/%s...%s could not be read (%s); "
                       "nothing is posted at this pause" % (base_ref, head, error))
        return _no_spec(None, threads_available=True, phase=phase, waiting=None, waiting_truncated=False), notices
    threads = _read_json(read["threads_path"])
    result, flag_notices = run_flags(
        root, pr_body_path, assess_path, None, base_ref, head, scratch, security_sites=security_sites,
        plan_threads=plan_threads, reasons_trusted=reasons_trusted, threads=threads, anchor_base=merge_base,
        out_of_plan=False, phase=phase, rigor_level=rigor_level, design_verdict=design_verdict,
        design_sites=design_sites, design_target=design_target, design_states=design_states,
        design_only=design_only, design_build=design_build, design_explanations=design_explanations,
        design_classification=design_classification,
    )
    notices.extend(flag_notices)
    result["threads_available"] = True
    result["phase"] = phase
    result["waiting"] = (phase_waiting(threads, result["new"], phase, result.get("retired") or ())
                         if result.get("spec_path") else None)
    result["waiting_truncated"] = bool(read.get("truncated")) and result["waiting"] is not None
    return result, notices


# ---- the upload step (#237, `skills/_shared/design-check.md` step 6) ----


def read_pr_comments(repo, pr, cwd=None, env=None):
    """The PR's conversation comments, raw REST objects (every page), or ``None`` when the read fails. One paginated
    ``gh api`` read; every top-level JSON value gh prints is flattened, whichever page shape it uses."""
    result = process.run(["gh", "api", "--paginate", "repos/%s/issues/%s/comments" % (repo, pr)], cwd=cwd, env=env)
    if result.returncode != 0:
        return None
    decoder, text, pos, out = json.JSONDecoder(), result.stdout, 0, []
    try:
        while pos < len(text):
            while pos < len(text) and text[pos] in " \t\r\n":
                pos += 1
            if pos >= len(text):
                break
            page, pos = decoder.raw_decode(text, pos)
            if not isinstance(page, list):
                return None
            out.extend(c for c in page if isinstance(c, dict))
    except ValueError:
        return None
    return out


def landed_captures(comments, head, target):
    """``{(state, side): url}`` (#237): every capture a trusted captures comment holds at ``head`` and ``target`` — a
    comment whose first line is :data:`CAPTURES_MARKER` and second the stamp for that head and target, by an
    `OWNER` / `MEMBER` / `COLLABORATOR` author (`pipelib.trust`, #179; anyone else's is skipped silently, as any
    marked comment of theirs is). An entry counts only as one image (:func:`image_ref`, Markdown or ``<img>``, the
    receipt reader's grammar) at a durable attachment URL (:func:`is_attachment_url`, within
    :func:`design_record.allowed_capture_url`), its alt naming the same side and state: a local path gh did not rewrite is no upload. The first
    landed entry for a key stands. Pure."""
    landed = {}
    for comment in comments or []:
        if not trust.is_trusted_comment(comment):
            continue
        lines = _lf(comment.get("body")).split("\n")
        if len(lines) < 2 or lines[0].strip() != CAPTURES_MARKER:
            continue
        stamp = _CAPTURES_STAMP_RE.match(lines[1].strip())
        if not stamp or stamp.group("head") != head or stamp.group("target") != target:
            continue
        for line in lines[2:]:
            entry = _CAPTURES_ENTRY_RE.match(line.strip())
            ref = image_ref(entry.group("image")) if entry else None
            if (ref is None or ref[0] != "%s %s" % (entry.group("side"), entry.group("state"))
                    or not is_attachment_url(ref[1])):
                continue
            landed.setdefault((entry.group("state"), entry.group("side")), ref[1])
    return landed


def _design_spec_threads(spec):
    return [t for t in (spec.get("threads") or []) if isinstance(t, dict) and isinstance(t.get("captures"), dict)]


def _set_capture(spec, state, side, url):
    for thread in _design_spec_threads(spec):
        if thread["captures"].get("state") == state:
            thread["captures"][side] = {"kind": "url", "ref": url}


def _prefilter(path):
    """``None`` when ``path`` may be uploaded, else the `NOT_UPLOADED_REASONS` reason it may not: ``type`` (not an
    image type gh uploads, or a path gh's ``FILE#ALT`` split, its absolute-path rule or the entry line cannot take), ``empty`` (no
    file, or an empty one), ``too-large`` (over :data:`design_record.CAPTURE_MAX_BYTES`). Keeps `gh_persist`'s
    up-front usage errors unreachable from the design check."""
    candidate = Path(path) if isinstance(path, str) and path else None
    # A path the staged entry line `![<alt>](<path>)` cannot carry verbatim (whitespace, a paren, an angle bracket)
    # would never match gh's exact-path rewrite: gh appends it instead, and a re-run could not read it back as landed,
    # so it would upload again at every run (#237: a re-run never duplicates the comment).
    if candidate is None or not candidate.is_absolute() or re.search(r"[#\s()<>]", path):
        return "type"
    if candidate.suffix.lower().lstrip(".") not in ATTACH_IMAGE_TYPES:
        return "type"
    try:
        size = candidate.stat().st_size if candidate.is_file() else 0
    except OSError:
        size = 0
    if size == 0:
        return "empty"
    if size > design_record.CAPTURE_MAX_BYTES:
        return "too-large"
    return None


def plan_upload(spec, staged_states, landed):
    """The upload step's plan (#237), pure over parsed inputs: ``(reused, pending, not_uploaded)``, rewriting
    ``spec`` in place for every reused capture. The candidates are, per design thread in the spec (one with
    ``captures``), its build's ``local`` capture and its record's ``path`` capture (uploaded from its staged copy,
    ``staged_states``' ``staged_path``), once per (state, side). ``landed`` (``None`` when the comments could not be
    read) supplies a reused URL; with it ``None`` every candidate is ``comments-unread`` and nothing uploads, so a
    re-run can never duplicate. The rest are pre-filtered (:func:`_prefilter`) and capped at
    :data:`pipelib.limits.ATTACH_MAX` (``over-cap`` past it). ``pending`` entries are ``{state, side, path, alt}``,
    ``path`` the absolute file the upload body references and ``--attach`` passes, ``alt`` ``<side> <state>``."""
    staged = {st.get("id"): st for st in staged_states or [] if isinstance(st, dict)}
    reused, pending, not_uploaded, seen = [], [], [], set()
    for thread in _design_spec_threads(spec):
        captures = thread["captures"]
        state = captures.get("state")
        for side in CAPTURE_SIDES:
            capture = captures.get(side) or {}
            if side == "build" and capture.get("kind") == "local":
                source = capture.get("ref")
            elif side == "record" and (capture.get("kind") == "path" or (
                    capture.get("kind") == "none" and (staged.get(state) or {}).get("staged_path"))):
                # A repo path, or a reference the root cannot show as it stands (a non-attachment URL the staging
                # still fetched and the comparison judged): its staged copy uploads [operator decision, #237 cold read].
                source = (staged.get(state) or {}).get("staged_path")
            else:
                continue
            if (state, side) in seen:
                continue
            seen.add((state, side))
            if landed is None:
                not_uploaded.append({"state": state, "side": side, "reason": "comments-unread"})
                continue
            if (state, side) in landed:
                _set_capture(spec, state, side, landed[(state, side)])
                reused.append({"state": state, "side": side, "url": landed[(state, side)]})
                continue
            reason = _prefilter(source)
            if reason is None and len(pending) >= ATTACH_MAX:
                reason = "over-cap"
            if reason is not None:
                not_uploaded.append({"state": state, "side": side, "reason": reason})
                continue
            pending.append({"state": state, "side": side, "path": source, "alt": "%s %s" % (side, state)})
    return reused, pending, not_uploaded


def captures_body(head, target, pending):
    """The captures comment (#237): every byte script-written — the marker, the stamp, :data:`CAPTURES_INTRO`, one
    :data:`CAPTURES_ENTRY` per pending capture referencing its file by the exact absolute path ``--attach`` passes,
    which gh rewrites to the attachment URL."""
    lines = [CAPTURES_MARKER, CAPTURES_STAMP % (head, target), "", CAPTURES_INTRO, ""]
    lines += [CAPTURES_ENTRY % (p["state"], p["side"], p["side"], p["state"], p["path"]) for p in pending]
    return "\n".join(lines) + "\n"


def _load_spec(spec_path):
    spec = _read_json(spec_path)
    if not isinstance(spec, dict) or not re.fullmatch(_HEX40, spec.get("head") or ""):
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s is not a review spec with a 40-hex `head`\n" % spec_path)
        sys.exit(EXIT_USAGE_ERROR)
    return spec


def run_captures(repo, pr, spec_path, design_states, scratch, cwd=None, env=None):
    """The `captures` subcommand (#237): reuse what a trusted captures comment already holds at the spec's head and the
    staged record's fingerprint (``--design-states``' ``fingerprint``, the comparison's target), and stage one comment carrying the captures still to upload. Rewrites ``spec_path`` in place for each
    reused capture; writes ``<scratch>/design-captures.md`` when anything is left to upload (removing a stale one
    otherwise) and always ``<scratch>/design-captures-pending.json`` = ``{head, target, pending, not_uploaded}``.
    Returns ``(payload, notices)``: ``{body_path, attach, reused, pending_path, not_uploaded}``. A failed comment read
    uploads nothing (`DESIGN_CAPTURES_UNREAD`) and every capture keeps its fallback."""
    spec = _load_spec(spec_path)
    staged = _read_json(design_states)
    staged = staged.get("payload", staged) if isinstance(staged, dict) else {}
    staged_states = [st for st in (staged.get("states") or []) if isinstance(st, dict)]
    head = spec["head"]
    # The key every upload is stamped and reused under: the record the captures were staged from (`design-states.json`'s
    # `fingerprint`, which `flags` pins to `--design-target`), never whichever design thread comes first in the spec.
    target = staged.get("fingerprint")
    notices, landed = [], {}
    if _design_spec_threads(spec):
        if not (isinstance(target, str) and re.fullmatch(_TARGET_HEX, target)) or not any(
                t.get("target") == target for t in _design_spec_threads(spec)):
            sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s has no design thread with captures at the staged "
                             "record %s\n" % (spec_path, target))
            sys.exit(EXIT_USAGE_ERROR)
        comments = read_pr_comments(repo, pr, cwd=cwd, env=env)
        if comments is None:
            landed = None
            notices.append("%s: the PR's comments could not be read; nothing is uploaded this run, and every design "
                           "thread keeps its labelled fallback captures" % DESIGN_CAPTURES_UNREAD)
        else:
            landed = landed_captures(comments, head, target)
    reused, pending, not_uploaded = plan_upload(spec, staged_states, landed)
    out = Path(scratch)
    out.mkdir(parents=True, exist_ok=True)
    if reused:
        Path(spec_path).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    body_path = out / "design-captures.md"
    if pending:
        body_path.write_text(captures_body(head, target, pending), encoding="utf-8")
    else:
        body_path.unlink(missing_ok=True)
    pending_path = out / "design-captures-pending.json"
    pending_path.write_text(json.dumps({"head": head, "target": target, "pending": pending,
                                        "not_uploaded": not_uploaded}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    return {"body_path": str(body_path) if pending else None,
            "attach": ["%s#%s" % (p["path"], p["alt"]) for p in pending],
            "reused": reused, "pending_path": str(pending_path), "not_uploaded": not_uploaded}, notices


def apply_receipt(spec, pending, receipt):
    """The upload's outcome written into ``spec`` (#237), pure: ``(uploaded, not_uploaded)``. Each pending capture
    maps to the receipt's ``attachment_urls`` entry with the exact same ``path`` (the one key both carry); a ``url``
    :func:`is_attachment_url` accepts upgrades that capture to ``url``, anything else leaves its
    fallback with the entry's reason. With no receipt (``None``: ``ATTACH_UNSUPPORTED``, or a post that failed or
    never ran) every pending capture is ``not-posted``. ``not_uploaded`` starts with the pending file's own
    (`captures`' pre-filter), so the one list names every capture that did not upload."""
    entries = []
    if isinstance(receipt, dict):
        receipt = receipt.get("payload", receipt) if isinstance(receipt.get("payload"), dict) else receipt
        entries = [e for e in receipt.get("attachment_urls") or [] if isinstance(e, dict)]
    uploaded = []
    not_uploaded = [dict(e) for e in pending.get("not_uploaded") or [] if isinstance(e, dict)]
    for item in pending.get("pending") or []:
        hit = next((e for e in entries if e.get("path") == item.get("path")), None)
        if receipt is not None and hit and isinstance(hit.get("url"), str) and is_attachment_url(hit["url"]):
            _set_capture(spec, item["state"], item["side"], hit["url"])
            uploaded.append({"state": item["state"], "side": item["side"], "url": hit["url"]})
            continue
        reason = hit.get("reason") if receipt is not None and hit else None
        if reason not in NOT_UPLOADED_REASONS:
            # A matched entry carrying a URL no root can show (a stale or hand-built receipt) did land somewhere, but
            # not as a durable attachment: `not-rewritten`, as `gh_persist.attachment_urls` would report it.
            reason = ("not-rewritten" if receipt is not None and hit and isinstance(hit.get("url"), str)
                      else "not-posted")
        not_uploaded.append({"state": item["state"], "side": item["side"], "reason": reason})
    return uploaded, not_uploaded


def run_apply_captures(spec_path, pending_path, receipt_path=None):
    """The `apply-captures` subcommand (#237): :func:`apply_receipt` over the files, rewriting ``spec_path`` in place.
    Returns ``(payload, notices)``: ``{uploaded, not_uploaded}``. A pending file for another head is a usage error."""
    spec = _load_spec(spec_path)
    pending = _read_json(pending_path)
    if not isinstance(pending, dict) or pending.get("head") != spec["head"]:
        sys.stderr.write("REVIEW_THREADS_INPUT_UNREADABLE: %s is not the pending captures of %s's head\n"
                         % (pending_path, spec_path))
        sys.exit(EXIT_USAGE_ERROR)
    receipt = _read_json(receipt_path) if receipt_path else None
    uploaded, not_uploaded = apply_receipt(spec, pending, receipt)
    Path(spec_path).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"uploaded": uploaded, "not_uploaded": not_uploaded}, []


def _positive_int(text):
    if not re.fullmatch(r"[1-9][0-9]*", text or ""):
        raise argparse.ArgumentTypeError("expects a positive integer, got %r" % (text,))
    return int(text)


def _design_arguments(sub_parser):
    """The design inputs `flags` and `pause` share (#206, `skills/_shared/design-check.md`)."""
    sub_parser.add_argument("--design-verdict", help="the comparison's verdict file (`## Comparison`)")
    sub_parser.add_argument("--design-sites",
                            help="`<state-id> <path>[:<line>[-<line>]]` lines: the build file each differing state "
                                 "renders in")
    sub_parser.add_argument("--design-target", help="the record fingerprint the comparison read (12 hex)")
    sub_parser.add_argument("--design-states", help="`design_record.py stage`'s envelope: the recorded states")
    sub_parser.add_argument("--design-build",
                            help="#237: the directory design-check step 2 wrote the build's captures to "
                                 "(<facts.scratch>/design-build); never derived from --scratch")
    sub_parser.add_argument("--design-explanations",
                            help="#241: `explain`'s envelope (design-explanations.json); with --design-classification")
    sub_parser.add_argument("--design-classification",
                            help="#241: the departure classification's verdict (`## Classification`)")


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
    p_flags.add_argument("--rigor", choices=rigor.LEVELS, default="full",
                         help="facts.rigor.level (#198): narrows the thread sources unless the assess payload's "
                              "full_rules holds (the stricter of this and the payload's own `rigor` applies); "
                              "default `full`, every source")
    _design_arguments(p_flags)
    p_pause = sub.add_parser("pause", help="the review a resolver review pause posts (#170)")
    p_pause.add_argument("root", help="the resolver's workspace, at the pushed head (facts.workspace.path)")
    p_pause.add_argument("--repo", required=True)
    p_pause.add_argument("--pr", required=True, type=_positive_int)
    p_pause.add_argument("--head", required=True, help="the pushed head's 40-hex sha")
    p_pause.add_argument("--base-ref", required=True, help="facts.workspace.base_ref")
    p_pause.add_argument("--phase", required=True, type=_positive_int, help="the phase this pass shipped")
    p_pause.add_argument("--assess", help="S5.2's shipped-diff blast_radius.py assess envelope (required but with "
                                          "--design-only)")
    p_pause.add_argument("--pr-body", required=True, help="the body this S5.2's PR-body write posted")
    p_pause.add_argument("--scratch", required=True)
    p_pause.add_argument("--reasons-trusted", required=True, choices=("true", "false"),
                         help="facts.out_of_plan.reasons_trusted (true on a fresh first phase)")
    p_pause.add_argument("--security-sites", help="`SEC-<n> <path>:<line>[-<line>]` lines")
    p_pause.add_argument("--plan-threads", help="`<path>:<line>[-<line>] — <deviation>` lines")
    p_pause.add_argument("--rigor", choices=rigor.LEVELS, default="full",
                         help="facts.rigor.level (#198): narrows the thread sources unless the assess payload's "
                              "full_rules holds (the stricter of this and the payload's own `rigor` applies); "
                              "default `full`, every source")
    _design_arguments(p_pause)
    p_pause.add_argument("--design-only", action="store_true",
                         help="#206: the final pass's design review — gather only the design flags")
    p_narrow = sub.add_parser("narrow", help="the out-of-plan files left after the exemptions (evaluator S4.6)")
    p_narrow.add_argument("root", help="the checkout at the gated head (facts.workspace.path)")
    p_narrow.add_argument("--assess", required=True, help="the S4.6 blast_radius.py assess envelope")
    p_narrow.add_argument("--head", required=True, help="the gated head's 40-hex sha")
    p_narrow.add_argument("--pr-body", required=True)
    p_narrow.add_argument("--reasons-trusted", required=True, choices=("true", "false"),
                          help="facts.out_of_plan.reasons_trusted, verbatim")
    p_narrow.add_argument("--scratch", required=True)
    p_captures = sub.add_parser("captures", help="#237: reuse uploaded captures, stage the ones to upload")
    p_captures.add_argument("root", help="the checkout the session runs in (facts.workspace.path)")
    p_captures.add_argument("--repo", required=True)
    p_captures.add_argument("--pr", required=True, type=_positive_int)
    p_captures.add_argument("--spec", required=True, help="the review spec `flags` / `pause` wrote")
    p_captures.add_argument("--design-states", required=True, help="`design_record.py stage`'s envelope")
    p_captures.add_argument("--scratch", required=True)
    p_explain = sub.add_parser("explain", help="#241: stage the departure classification's two inputs")
    p_explain.add_argument("--design-verdict", required=True, help="the comparison's verdict file (`## Comparison`)")
    p_explain.add_argument("--design-states", required=True, help="`design_record.py stage`'s envelope")
    p_explain.add_argument("--pr-body", required=True, help="the PR body: its `## Design departures` and "
                                                            "`## Decided deviations`")
    p_explain.add_argument("--plan", help="the staged plan, for its agreed UI deviations; omit with no plan")
    p_explain.add_argument("--trusted", required=True, choices=("true", "false"),
                           help="facts.design_departures.trusted (true on a fresh first phase pass)")
    p_explain.add_argument("--out", required=True, help="the directory the two files are written to")
    p_apply = sub.add_parser("apply-captures", help="#237: write the upload's URLs into the review spec")
    p_apply.add_argument("--spec", required=True)
    p_apply.add_argument("--pending", required=True, help="`captures`' pending_path")
    p_apply.add_argument("--receipt", help="the `gh_persist.py comment --attach` envelope; omit when none posted")
    p_links = sub.add_parser("code-links", help="#245: the link each anchor a supporting block names gets")
    p_links.add_argument("--root", required=True, help="the checkout the session runs in (facts.workspace.path)")
    p_links.add_argument("--pr-url", help="the PR's URL; omit when no PR exists yet (every anchor is `code`)")
    p_links.add_argument("--base-ref", help="the PR's base branch (with --pr-url)")
    p_links.add_argument("--pr-head", help="the PR's pushed head, 40-hex (with --pr-url) — never local HEAD")
    p_links.add_argument("anchor", nargs="*", help="`<path>:<start>-<end>`, `<path>:<line>` or `<path>`")
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand not in ("flags", "narrow", "pause", "captures", "apply-captures", "explain", "code-links"):
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    if args.subcommand == "code-links":
        if not args.anchor:
            parser.error("code-links needs at least one anchor")
        anchors = []
        for text in args.anchor:
            parsed = parse_code_anchor(text)
            if parsed is None:
                parser.error("malformed anchor %r: want <path>:<start>-<end>, <path>:<line> or <path>" % text)
            anchors.append((text,) + parsed)
        if args.pr_url:
            if not _PR_URL_RE.match(args.pr_url):
                parser.error("--pr-url must be a PR URL (https://<host>/<owner>/<repo>/pull/<n>)")
            if not args.base_ref or not re.fullmatch(_HEX40, args.pr_head or ""):
                parser.error("--pr-url needs --base-ref and a 40-hex --pr-head")
        payload, notices, _ = build_code_links(args.root, anchors, pr_url=args.pr_url, base_ref=args.base_ref,
                                               pr_head=args.pr_head)
        emit_ok(payload=payload, notices=notices)
        sys.exit(EXIT_OK)
    if args.subcommand == "captures":
        payload, notices = run_captures(args.repo, args.pr, args.spec, args.design_states, args.scratch,
                                        cwd=args.root)
        emit_ok(payload=payload, notices=notices)
        sys.exit(EXIT_OK)
    if args.subcommand == "explain":
        payload, notices = run_explain(args.design_verdict, args.design_states, args.pr_body, args.plan,
                                       args.trusted == "true", args.out)
        emit_ok(payload=payload, notices=notices)
        sys.exit(EXIT_OK)
    if args.subcommand == "apply-captures":
        payload, notices = run_apply_captures(args.spec, args.pending, args.receipt)
        emit_ok(payload=payload, notices=notices)
        sys.exit(EXIT_OK)
    if not re.fullmatch(_HEX40, args.head):
        parser.error("--head must be a 40-hex commit sha")
    if args.subcommand in ("flags", "pause"):
        given = [args.design_verdict, args.design_target, args.design_states, args.design_build]
        if any(given) and not all(given):
            parser.error("--design-verdict, --design-target, --design-states and --design-build go together")
        if args.design_sites and not all(given):
            parser.error("--design-sites needs --design-verdict, --design-target, --design-states and --design-build")
        if args.design_target and not re.fullmatch(_TARGET_HEX, args.design_target):
            parser.error("--design-target must be the record's 12-hex fingerprint")
        pair = [args.design_explanations, args.design_classification]
        if any(pair) and not all(pair):
            parser.error("--design-explanations and --design-classification go together")
        if any(pair) and not all(given):
            parser.error("--design-explanations and --design-classification need --design-verdict, --design-target, "
                         "--design-states and --design-build")
    if args.subcommand == "pause":
        if not args.design_only and not args.assess:
            parser.error("--assess is required (it may be omitted only with --design-only)")
        payload, notices = run_pause(
            args.root, args.repo, args.pr, args.head, args.base_ref, args.phase, args.assess, args.pr_body,
            args.scratch, args.reasons_trusted == "true", security_sites=args.security_sites,
            plan_threads=args.plan_threads, rigor_level=args.rigor, design_verdict=args.design_verdict,
            design_sites=args.design_sites, design_target=args.design_target, design_states=args.design_states,
            design_only=args.design_only, design_build=args.design_build,
            design_explanations=args.design_explanations, design_classification=args.design_classification,
        )
    elif args.subcommand == "narrow":
        payload, notices = run_narrow(args.root, args.assess, args.head, args.pr_body,
                                      args.reasons_trusted == "true", args.scratch)
    else:
        payload, notices = run_flags(
            args.root, args.pr_body, args.assess, args.threads, args.base_ref, args.head, args.scratch,
            security_sites=args.security_sites, plan_threads=args.plan_threads, triage=args.triage,
            splits=args.splits, reasons_trusted=args.reasons_trusted == "true", rigor_level=args.rigor,
            design_verdict=args.design_verdict, design_sites=args.design_sites, design_target=args.design_target,
            design_states=args.design_states, design_build=args.design_build,
            design_explanations=args.design_explanations, design_classification=args.design_classification,
        )
    emit_ok(payload=payload, notices=notices)
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
