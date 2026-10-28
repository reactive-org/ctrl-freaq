#!/usr/bin/env python3
"""prep_review.py — the `review` tool's prep (#171): the PR's review threads still waiting on a person,
in the order the operator reviews them, each with a script-cut code excerpt and a Files-view link.

    prep_review.py <issue> <owner/repo> [--refresh --head <40-hex>] [--scratch-dir PATH] [--cwd PATH]

The target is the issue; the PR is the open PR whose head is the checkout's branch. One envelope
(architecture.md §3) whose payload is:

    issue      {number, title}
    pr         {number, url, head_ref, base_ref, head} | null   — null: the branch carries no open PR
    workspace  the attach envelope (workspace.py's)
    plan       {present, body_path}
    review     {head, available, truncated, viewer, counts, untrusted, next_f} | null (no PR)
    items      [{key, seq, of, id, f, o, origin, url, path, subject, rank, flags, phase, claim,
                 reply_reason, security, root_text_path, link, excerpt, members}]
    left_out   {replaced, changes-requested, superseded, accepted, operator_awaiting_resolver}
    scratch

**Facts by script, meaning by model** (CLAUDE.md). Which threads wait on a person, their order and the
code each one shows are derived here, from `review_threads.read_threads` — the reader the evaluator gates
on, so the tool sees exactly the states that gate approval (`skills/_shared/review-threads.md` §4). The
brief, the discussion and the operator's answer are the model's and the operator's.

**The judged head.** The checkout must sit at exactly the PR head (`workspace._build_attach` with
`require_exact_remote_sha`, the evaluator's rule), and `review.head` = that head is the commit the threads
are judged at, the commit every excerpt is cut at, and the `--head` of every decision reply: a decision
holds for the code the operator was shown and no later code (§6, §10). `--refresh --head <sha>` (the
playbook's closing re-read) keeps the branch check, skips the exact-head check, and judges at `--head`, which
must be a commit in the checkout: a missing one would read every pipeline thread as moved (`superseded`) and
report that nothing waits, so it is a hard failure instead.
No setup hooks run: the tool builds and tests nothing.

**Selection** (`is_waiting`): a thread is an item when it is not `replaced` and is `unreviewed`, or an
`operator-thread` with `pending_reply` (the resolver answered; a person is still to look). Every other
thread is counted once in `left_out`. **Order** (`rank`, `order_items`): the lowest rank among a thread's
keys — security 1; sensitive, judgment 2; plan 3; out-of-plan 4; an operator thread 5; routine 6 — then
`F<n>`, then `O<n>`. No phase logic: #172's phase filter composes in front of these.

**Excerpts** (`cut_excerpt`): the PR's diff of the thread's file, merge base to judged head at `-U3`
(literal pathspec, no textconv — the lines GitHub numbers), its anchor mapped from the root's commit with
`review_threads.map_range_between`. A line anchor keeps the hunks its range intersects and centres a
window on it; a file anchor, a base-side human comment, an unmappable range or one outside every hunk
takes the hunks from the top. At most `EXCERPT_LINES` lines, `@@` headers included; a hunk cut by the
window gets a header recomputed for the lines kept. A batch gives each member up to `MEMBER_LINES` within
the item's budget, and lists the rest `elided`.

It writes nothing to GitHub and changes no file, branch or commit in the repo. Its one repo write is
`sensitive_paths.changed_paths`' `git fetch origin <base>`, which updates the remote-tracking ref (as the
evaluator's and resolver's preps do); otherwise it writes only its scratch dir (`/tmp/gh-review-<N>/` by
default).

Exit codes: 0 with an envelope (`ok`, or `needs_decision`: `AUTH_REQUIRED`, `TARGET_IS_PR`,
`MARKER_AMBIGUOUS`, `WORKSPACE_MISMATCH`, all forwarded from their producers); 2 on a usage error; 1 with
no envelope on a hard failure — a failed issue gather, a failed open-PR lookup (`OPEN_PR_LOOKUP_UNAVAILABLE`
on stderr: unknown, never read as "no PR"), or a `--refresh --head` that is not a commit in the checkout,
each with a `prep_review:` line on stderr; or a composed producer's own hard failure (a failed `gh pr view`,
a failed `git ls-remote`, a path that is not a checkout), with that producer's stderr.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402
import gh_gather  # noqa: E402
import gh_pr_gather  # noqa: E402
import phase_review  # noqa: E402
import plan_parts  # noqa: E402
import review_threads  # noqa: E402
import sensitive_paths  # noqa: E402
import workspace  # noqa: E402
from pipelib import process  # noqa: E402
from pipelib.envelope import emit_needs_decision, emit_ok  # noqa: E402

EXCERPT_LINES = 40
MEMBER_LINES = 12

# The issue body's review order (#171 "Prep (a script)"): security findings, review-level raises, plan
# deviations, out-of-plan files, the routine batch last. An operator's own thread carries no pipeline risk
# signal, so it goes just before the batch. Not §7's cap order, which is the evaluator's posting priority.
KEY_RANKS = {"security": 1, "sensitive": 2, "judgment": 2, "plan": 3, "out-of-plan": 4, "routine": 6}
OPERATOR_RANK = 5

LEFT_OUT_KEYS = ("replaced", "changes-requested", "superseded", "accepted", "operator_awaiting_resolver")

REVIEW_EXCERPTS_UNAVAILABLE = "REVIEW_EXCERPTS_UNAVAILABLE"

_HEX40_RE = re.compile(r"[0-9a-f]{40}")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


class _DiscardStream:
    """A write-sink for ``gh_gather.run(stream=...)``: prep consumes only the returned envelope."""

    def write(self, _data):
        return None

    def flush(self):
        return None


def default_scratch(issue):
    return "/tmp/gh-review-%s" % issue


# ---------------------------------------------------------------------------
# Selection and order — pure
# ---------------------------------------------------------------------------


def is_waiting(thread):
    """Whether a `read_threads` thread waits on a person: not `replaced`, and `unreviewed` or an
    `operator-thread` the resolver has answered (`pending_reply`)."""
    if thread.get("replaced"):
        return False
    state = thread.get("state")
    return state == "unreviewed" or (state == "operator-thread" and bool(thread.get("pending_reply")))


def left_out_key(thread):
    """The one `left_out` key a thread that is not an item counts under: `replaced` first, then its state."""
    if thread.get("replaced"):
        return "replaced"
    if thread.get("state") == "operator-thread":
        return "operator_awaiting_resolver"
    return thread.get("state")


def rank(thread):
    """The lowest rank among the thread's flag keys; `OPERATOR_RANK` for a thread with none it knows."""
    ranks = [KEY_RANKS[kind] for kind in (review_threads.flag_key_parts(k)[0] for k in thread.get("flags") or [])
             if kind in KEY_RANKS]
    return min(ranks) if ranks else OPERATOR_RANK


def order_items(threads):
    """The waiting threads in review order: by `rank`, then `F<n>` ascending, then `O<n>` ascending."""
    def key(thread):
        f, o = thread.get("f"), thread.get("o")
        return (rank(thread), 0 if f is not None else 1, f if f is not None else (o or 0))
    return sorted((t for t in threads if is_waiting(t)), key=key)


# ---------------------------------------------------------------------------
# The excerpt — pure
# ---------------------------------------------------------------------------


def _parse_diff(diff_text):
    """The hunks of one file's unified diff: ``[{suffix, old, new, lines: [(text, old_pos, new_pos)]}]``.
    ``old_pos`` / ``new_pos`` are the old- and new-side line numbers each body line sits at (a `+` line's
    old position is the old line it precedes, and the other way round), which is what a recomputed header
    and the window's centre read."""
    hunks = []
    current = None
    # Split on newlines only: `str.splitlines` also breaks on a form feed, `\x1c`–`\x1e` or a lone `\r` inside
    # a content line, which would shift every later line's number.
    for line in (diff_text or "").rstrip("\n").split("\n"):
        match = _HUNK_HEADER_RE.match(line)
        if match:
            old_start, _, new_start, _, suffix = match.groups()
            old, new = int(old_start), int(new_start)
            # A side with count 0 numbers the line before the change; its next line is one on.
            if match.group(2) == "0":
                old += 1
            if match.group(4) == "0":
                new += 1
            current = {"suffix": suffix, "old": old, "new": new, "lines": []}
            hunks.append(current)
            continue
        if current is None:
            continue  # the file header
        old, new = current["old"], current["new"]
        tag = line[:1]
        if tag == "\\":
            current["lines"].append((line, None, None))
            continue
        current["lines"].append((line, old, new))
        if tag in (" ", ""):
            current["old"], current["new"] = old + 1, new + 1
        elif tag == "-":
            current["old"] = old + 1
        elif tag == "+":
            current["new"] = new + 1
    return hunks


def _new_span(hunk):
    """The new-side lines a hunk covers, ``(first, last)``; a pure deletion covers the line it sits before."""
    news = [new for text, _, new in hunk["lines"] if new is not None and text[:1] in (" ", "", "+")]
    if news:
        return min(news), max(news)
    positions = [new for _, _, new in hunk["lines"] if new is not None]
    first = positions[0] if positions else 1
    return first - 1, first


def _header(group):
    """A `@@` header for a run of one hunk's body lines, numbered for exactly those lines."""
    numbered = [(text, old, new) for text, old, new in group if old is not None]
    old_count = sum(1 for text, _, _ in numbered if text[:1] in (" ", "", "-"))
    new_count = sum(1 for text, _, _ in numbered if text[:1] in (" ", "", "+"))
    old_start = numbered[0][1] if numbered else 1
    new_start = numbered[0][2] if numbered else 1
    if old_count == 0:
        old_start -= 1
    if new_count == 0:
        new_start -= 1

    def side(start, count):
        return "%d" % start if count == 1 else "%d,%d" % (start, count)

    return "@@ -%s +%s @@" % (side(old_start, old_count), side(new_start, new_count))


def cut_excerpt(diff_text, rng, subject, cap):
    """``(lines, truncated)``: at most ``cap`` lines of one file's diff, `@@` headers included.

    ``rng`` is the anchor mapped to the judged head (``[start, end]``, or ``None``); ``subject`` is
    ``"line"`` or ``"file"``. A line anchor whose range intersects a hunk keeps only those hunks and centres
    the window on the range; anything else keeps every hunk from the top. A hunk cut by the window gets a
    header recomputed for the lines kept, so its numbers stay true. ``truncated`` says a kept hunk lost a
    line. Pure."""
    hunks = _parse_diff(diff_text)
    if not hunks:
        return [], False
    anchored = subject == "line" and rng is not None
    if anchored:
        start, end = rng
        hit = [h for h in hunks if _new_span(h)[0] <= end and _new_span(h)[1] >= start]
        if hit:
            hunks = hit
        else:
            anchored = False
    body = [(index, line) for index, hunk in enumerate(hunks) for line in hunk["lines"]]
    full = len(body) + len(hunks)
    if full <= cap:
        width, begin = len(body), 0
    else:
        width = max(cap - 1, 0)
        centre = 0
        if anchored:
            start, end = rng
            inside = [i for i, (_, (text, _, new)) in enumerate(body)
                      if new is not None and start <= new <= end]
            centre = (inside[0] + inside[-1]) // 2 if inside else 0
        while True:
            begin = max(0, min(centre - width // 2, len(body) - width)) if anchored else 0
            groups = len({index for index, _ in body[begin:begin + width]})
            if width + groups <= cap or width == 0:
                break
            width -= 1
    window = body[begin:begin + width]
    out = []
    for index in sorted({i for i, _ in window}):
        group = [line for i, line in window if i == index]
        hunk = hunks[index]
        header = _header(group)
        out.append(header + hunk["suffix"])
        out.extend(text for text, _, _ in group)
    return out, len(window) < len(body)


# ---------------------------------------------------------------------------
# The git reads
# ---------------------------------------------------------------------------


def _file_diff(root, merge_base, head, path):
    """``(text, reason)``: the PR's `-U3` diff of ``path``; ``reason`` is ``"binary"`` or ``"no-hunk"`` when it
    has no text hunk to show, ``"unread"`` when `git` failed or its output is not UTF-8 (a file in another
    encoding: the runner decodes strictly, and one such file must not cost the whole review)."""
    try:
        result = process.run(
            ["git", "--literal-pathspecs", "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-U3",
             merge_base, head, "--", path],
            cwd=root,
        )
    except UnicodeDecodeError:
        return None, "unread"
    if result.returncode != 0:
        return None, "unread"
    text = result.stdout or ""
    lines = text.split("\n")
    if any(_HUNK_HEADER_RE.match(line) for line in lines):
        return text, None
    binary = any(line.startswith("Binary files ") or line == "GIT binary patch" for line in lines)
    return None, "binary" if binary else "no-hunk"


def _excerpt(root, pr_range, head, path, rng, subject, cap, out_path, diffs):
    """One file's excerpt fact, the kept lines written to ``out_path``; with ``out_path`` ``None`` only its
    kind is read (an elided batch member), and nothing is written. ``diffs`` caches each path's diff for the
    run, since several threads often sit on one file."""
    merge_base, error = pr_range
    if error is not None:
        return {"path": None, "kind": "none", "reason": "unread"}
    if path not in diffs:
        diffs[path] = _file_diff(root, merge_base, head, path)
    text, reason = diffs[path]
    if text is None:
        return {"path": None, "kind": "none", "reason": reason}
    if out_path is None:
        return {"path": None, "kind": "diff"}
    lines, truncated = cut_excerpt(text, rng, subject, cap)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(out_path), "lines": len(lines), "truncated": truncated, "kind": "diff"}


def _mapped_range(root, extra, thread, head):
    """The thread's line range at ``head``, or ``None`` (a file anchor, a base-side human comment, or a
    range that cannot be mapped — each takes the file's hunks from the top)."""
    if thread.get("subject") != "line" or thread.get("range") is None:
        return None
    if extra.get("side") == "LEFT" and thread.get("origin") == "operator":
        return None
    root_commit = extra.get("root_commit")
    if not root_commit:
        return None
    return review_threads.map_range_between(root, root_commit, head, thread["path"], thread["range"])


def _root_text(comment):
    """A stored root comment's text after the marker and stamp lines when the reader counted it as the
    pipeline's (`marked`, after its position and SEC-3 demotions); anyone else's is their text, all of it."""
    body = (comment or {}).get("body") or ""
    lines = body.split("\n")
    if comment and comment.get("marked"):
        lines = body.lstrip().split("\n")[2:]
    return "\n".join(lines).strip("\n") + "\n"


def build_items(threads, stored, root, head, pr_range, pr_url, scratch):
    """``(items, left_out)`` from `read_threads`' threads and the entries of its ``threads_path`` (same
    order). Writes each item's root text and excerpts under ``scratch``."""
    left_out = {key: 0 for key in LEFT_OUT_KEYS}
    extras = {}
    for thread, entry in zip(threads, stored):
        extras[id(thread)] = entry
        if not is_waiting(thread):
            left_out[left_out_key(thread)] += 1
    ordered = order_items(threads)
    items = []
    diffs = {}
    for seq, thread in enumerate(ordered, start=1):
        entry = extras.get(id(thread), {})
        key = "F%d" % thread["f"] if thread.get("f") is not None else "O%d" % (thread.get("o") or 0)
        # Staged files carry the item's `seq` as well as its key: one F id can name two threads (a review
        # re-posted after a lost response, `read_threads`), and each must keep its own text and excerpt.
        stem = "%d-%s" % (seq, key)
        root_text_path = scratch / ("root-%s.md" % stem)
        comments = entry.get("comments") or []
        root_text_path.write_text(_root_text(comments[0] if comments else None), encoding="utf-8")
        path = thread.get("path")
        members = []
        excerpt = None
        flags = thread.get("flags") or []
        batch = sorted(review_threads.batch_members(flags))
        if batch:
            budget = EXCERPT_LINES
            for n, member in enumerate(batch, start=1):
                link = phase_review.build_links(pr_url, [member])["files"][0]["url"]
                out_path = scratch / ("excerpt-%s-m%d.diff" % (stem, n)) if budget >= MEMBER_LINES else None
                fact = _excerpt(root, pr_range, head, member, None, "file", MEMBER_LINES, out_path, diffs)
                if fact["kind"] == "diff" and out_path is None:
                    members.append({"path": member, "kind": "diff", "link": link, "excerpt": None, "elided": True})
                    continue
                if fact["kind"] == "diff":
                    budget -= fact["lines"]
                members.append({"path": member, "kind": fact["kind"], "link": link, "excerpt": fact,
                                "elided": False})
        elif path:
            rng = _mapped_range(root, entry, thread, head)
            excerpt = _excerpt(root, pr_range, head, path, rng, "line" if rng is not None else "file",
                               EXCERPT_LINES, scratch / ("excerpt-%s.diff" % stem), diffs)
        items.append({
            "key": key,
            "seq": seq,
            "of": len(ordered),
            "id": thread.get("id"),
            "f": thread.get("f"),
            "o": thread.get("o"),
            "origin": thread.get("origin"),
            "url": thread.get("url"),
            "path": path,
            "subject": thread.get("subject"),
            "rank": rank(thread),
            "flags": list(flags),
            "phase": thread.get("phase"),
            "claim": thread.get("claim"),
            "reply_reason": thread.get("reply_reason"),
            "security": any(review_threads.flag_key_parts(k)[0] == "security" for k in flags),
            "root_text_path": str(root_text_path),
            "link": phase_review.build_links(pr_url, [path])["files"][0]["url"] if path else None,
            "excerpt": excerpt,
            "members": members,
        })
    return items, left_out


# ---------------------------------------------------------------------------
# The core
# ---------------------------------------------------------------------------


class LookupFailed(Exception):
    """A hard failure with no envelope: stderr carries the message, exit 1."""


def _plan_fact(envelope, issue, scratch):
    if not envelope.get("marker_comment_present"):
        return {"present": False, "body_path": None}
    path = envelope.get("marker_comment_path")
    if path is None:
        staged = scratch / ("issue-%s-plan.md" % issue)
        staged.write_text(envelope.get("marker_comment_body") or "", encoding="utf-8")
        path = str(staged)
    return {"present": True, "body_path": path}


def build_review(issue, repo, scratch_dir, cwd=None, refresh=False, head=None, env=None):
    """``(payload, notices, decision)`` — the testable core. ``decision`` is a forwarded producer's
    (payload ``None``). Raises `LookupFailed` on a hard failure."""
    scratch = Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    checkout = cwd if cwd is not None else "."

    # 1) The issue and its plan. A PR number is `TARGET_IS_PR` here, before any checkout read.
    exit_code, envelope = gh_gather.run(str(issue), repo, marker_prefix=plan_parts.MAIN_MARKER,
                                        scratch_dir=str(scratch), env=env, stream=_DiscardStream())
    if envelope is not None and envelope.get("status") == "needs_decision":
        return None, list(envelope.get("notices") or []), envelope["decision"]
    if exit_code != 0 or envelope is None:
        raise LookupFailed("the issue gather for #%s failed (exit %s)" % (issue, exit_code))
    notices = list(envelope.get("notices") or [])
    title = envelope.get("title") or ""

    # 2) The branch: the issue's own (`<N>-…` or `epic/<N>-…`, the two forms branching reads). The computed
    #    name only words the mismatch card; a pushed branch is never re-named.
    root = str(workspace._resolve_main_root(checkout))
    worded, _ = branching.compute_branch_name(root, issue, branching.compute_fresh_slug(title))
    ws, _ws_notices, decision = workspace._build_attach(
        checkout, worded, run_hooks=False, accept_branch_pattern=r"^(?:epic/)?%s-" % issue)
    if decision is not None:
        return None, notices, decision

    # 3) The PR this worktree pushes to: the exact-head lookup, never a body-mention search.
    found, lookup_notice = branching.find_open_pr_for_head(repo, ws["branch"], cwd=cwd)
    if lookup_notice is not None:
        raise LookupFailed("%s: the open PR for branch %s could not be looked up" % (lookup_notice, ws["branch"]))
    payload = {
        "issue": {"number": int(issue), "title": title},
        "pr": None,
        "workspace": ws,
        "plan": _plan_fact(envelope, issue, scratch),
        "review": None,
        "items": [],
        "left_out": {key: 0 for key in LEFT_OUT_KEYS},
        "scratch": str(scratch),
    }
    if found is None:
        return payload, notices, None
    pr_facts, _pr_notices, decision = gh_pr_gather.build_pr_facts(found["number"], repo, scratch_dir=str(scratch),
                                                                  cwd=cwd)
    if decision is not None:
        return None, notices, decision

    # 4) The judged head: exactly the PR head (the evaluator's rule), or `--head` on the closing re-read.
    if not refresh:
        ws, _ws_notices, decision = workspace._build_attach(
            checkout, pr_facts["headRefName"], run_hooks=False,
            expected_remote_sha=pr_facts["headRefOid"], require_exact_remote_sha=True)
        if decision is not None:
            return None, notices, decision
        payload["workspace"] = ws
        head = pr_facts["headRefOid"]
    # Every consumer below degrades quietly on a missing commit (a moved hunk, an unmappable range), which is
    # right for a thread's own commits and wrong for the judged head: it must exist before anything reads it.
    if not review_threads._commit_exists(ws["path"], head):
        raise LookupFailed("--head %s is not a commit in the checkout %s" % (head, ws["path"]))
    payload["pr"] = {"number": pr_facts["number"], "url": pr_facts["url"], "head_ref": pr_facts["headRefName"],
                     "base_ref": pr_facts["baseRefName"], "head": pr_facts["headRefOid"]}

    # 5) The threads, judged at that head, and the PR's whole range from its merge base (§7 "Anchoring").
    git_root = ws["path"]
    threads_fact, thread_notices = review_threads.read_threads(repo, pr_facts["number"], git_root, head,
                                                               str(scratch), cwd=cwd, env=env)
    notices.extend(thread_notices)
    payload["review"] = {key: threads_fact.get(key) for key in
                         ("head", "available", "truncated", "viewer", "counts", "untrusted", "next_f")}
    if not threads_fact.get("available"):
        return payload, notices, None
    stored = json.loads(Path(threads_fact["threads_path"]).read_text(encoding="utf-8"))
    _paths, merge_base, error = sensitive_paths.changed_paths(git_root, pr_facts["baseRefName"], head)
    if error is not None:
        notices.append("%s: the PR's diff could not be read (%s); every excerpt reads `unread`"
                       % (REVIEW_EXCERPTS_UNAVAILABLE, error))
    payload["items"], payload["left_out"] = build_items(
        threads_fact["threads"], stored, git_root, head, (merge_base, error), pr_facts["url"], scratch)
    return payload, notices, None


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("issue", help="the issue whose PR's review threads to review")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--refresh", action="store_true",
                        help="the closing re-read: keep the branch check, skip the exact-head check, judge at --head")
    parser.add_argument("--head", default=None,
                        help="with --refresh: the opening run's full 40-character review.head, a commit in the checkout")
    parser.add_argument("--scratch-dir", default=None, help="scratch dir (default: /tmp/gh-review-<issue>)")
    parser.add_argument("--cwd", default=None, help="the checkout to assert, and the gh calls' working directory")
    args = parser.parse_args(argv)
    if not args.issue.isdigit() or "/" not in args.repo:
        parser.error("issue must be a number and repo owner/repo")
    if args.refresh != (args.head is not None):
        parser.error("--refresh and --head go together")
    if args.head is not None and not _HEX40_RE.fullmatch(args.head):
        parser.error("--head takes the opening run's full 40-character review.head, got %r" % args.head)

    try:
        payload, notices, decision = build_review(
            args.issue, args.repo, args.scratch_dir or default_scratch(args.issue), cwd=args.cwd,
            refresh=args.refresh, head=args.head)
    except LookupFailed as exc:
        sys.stderr.write("prep_review: %s\n" % exc)
        return 1
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
    else:
        emit_ok(payload=payload, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
