#!/usr/bin/env python3
"""prep_question_resolver.py — the question-resolver skill's complete starting-state facts block in one
call (architecture.md §4; docs/implementation.md S18; docs/specs/question-resolver.md).

The **question-resolver** is the assisted-closing path for ONE open `question`-type issue: it grounds
the question against the docs, helps the operator reach a decision, records that decision durably as a
`<!-- question-decision:v1 -->` comment, offers to close the issue, and PROPOSES (never applies) the doc
fold-back. This prep assembles the session's entire deterministic starting state — the target question +
thread + the decision-marker **reentrancy** signal, the not-a-question guard, the native **`blocking`**
list (the build issues this question gates — the reverse edge that fills the decision comment's
`## Unblocks`), `root.sha`, and `attention` — as ONE JSON envelope on stdout, so the resolver session's
startup is one Python process, never a subprocess chain.

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external processes any
script may spawn are git/gh"; S8 pattern lock)::

    gh_gather.run(..., marker_prefix=<decision>)  -- the question body/thread + the
                                                     `<!-- question-decision:v1 -->` decision-marker
                                                     envelope (one round-trip; the marker via
                                                     `marker_prefix`). A marker present -> revise mode;
                                                     its `id` is what the flow's `gh_persist.py comment
                                                     --delete-marker-id` deletes on the replace.
    git rev-parse HEAD                            -- root's own SHA (informational; no freshness gate in
                                                     prep — the resolver writes no tracked files)
    doc_catalogue.read_catalogue                  -- the consuming repo's `<!-- doc-catalogue -->`
                                                     grounding docs, read at root's working tree —
                                                     the constraint audit's doc set (filesystem only;
                                                     no gh/git call)

`gh_gather.run` exposes the S8-locked emit-through-a-stream shape; this prep passes a discard stream and
reads the returned ``(exit, envelope)`` directly, forwarding any ``needs_decision`` verbatim (see the
reentrancy note) and emitting exactly one envelope of its own. No ``redirect_stdout``/``io.StringIO``
capture of another script's stdout is used anywhere (S9-on rule; docs/specs/baseline.md §5).

**Reentrancy (docs/specs/question-resolver.md "Invariants": revise, never duplicate).** The decision
marker's count drives the mode — the skill is reentrant, a re-run must revise, not duplicate:

  - ``marker_comment_count == 0`` -> ``mode: "fresh"`` — no prior decision.
  - ``marker_comment_count == 1`` -> ``mode: "revise"`` — a prior decision exists; its `comment_id` is
    staged so the flow's `gh_persist.py comment --delete-marker-id` REPLACES (post-new-then-delete-old,
    gh_persist's built-in) rather than stacking a second decision comment.
  - ``marker_comment_count > 1`` -> the gather's ``MARKER_AMBIGUOUS`` `needs_decision` (v1's
    "DECISION_NEEDED: which decision is current"), forwarded verbatim — prep emits it and stops.

**Already-closed** (`state == CLOSED`) is a fact, not a gate: a re-run revises the comment in place and
`gh_persist.py close` on an already-closed issue is a `gh` no-op (safe for a reentrant caller); the flow
offers `reopen` only when a materially-changed decision needs the issue visible again (§7).

**Not a `question` issue** (`question` label absent) is a fact (`is_question: false` + an `attention`
line), not a `needs_decision` — the router stops and points at the `resolver`, matching v1's
"stop and say so" and the same "facts by script, decision by router" posture prep_setup uses for a
malformed block.

**Native `blocking` list** — the build issues this question gates (the reverse edge, from `gh_gather`'s
`blocking`): surfaced so the decision comment's `## Unblocks` and the Step-9 summary breadcrumb name
them. `blocked_by` rides along informationally.

**No workspace, no root-freshness gate in prep** (the root-only shape prep_researcher/prep_setup adopted):
the resolver writes no tracked files — its two write surfaces are the decision comment and an offered
close, and the doc fold-back is proposal-only (docs/specs/question-resolver.md "Invariants"). `root.sha`
is a plain informational `git rev-parse HEAD` (the docs are read at the working tree — a question isn't
tied to a branch).

Usage::

    prep_question_resolver.py <issue> <owner/repo> [--root PATH] [--scratch-dir PATH] [--cwd PATH]

``<issue>`` is the one question-issue number (required). ``--root`` defaults to ``.`` (architecture.md
§6's read-only trust vantage). ``--scratch-dir`` defaults to ``/tmp/gh-question-resolver-<issue>``
(CLAUDE.md's ``/tmp/gh-<skill>-<N>/`` convention) — where the flow later stages `decision.md`.

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"`` or
``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified hard
`gh`/`git` failure surfaced by the composed gather — stderr carries the faithful error.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import doc_catalogue  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
import gh_gather  # noqa: E402
from pipelib import process, trust  # noqa: E402
from pipelib.decisions import DOC_CATALOGUE_ABSENT  # noqa: E402
from pipelib.envelope import emit_needs_decision, emit_ok  # noqa: E402

# The durable decision comment (skills/_shared/open-question-links.md §Status; the sole writer is this
# skill) — gh_gather's `marker_prefix` for reentrancy detection. Byte-identical to the frozen marker
# (docs/specs/examples/question-decision.md).
DECISION_MARKER = "<!-- question-decision:v1 -->"

# The `question`-issue label (skills/_shared/question-issue.md) — the not-a-question guard's check.
QUESTION_LABEL = "question"


class _DiscardStream:
    """A minimal write-sink for `gh_gather.run(stream=...)` — see `prep_researcher._DiscardStream` for
    the full rationale (a two-method sink is not the in-process composition architecture.md §2 asks for).
    """

    def write(self, _data):
        return None

    def flush(self):
        return None


def _forward_decision(decision, notices=None):
    """Emit a composed core's returned `decision` AS-IS on prep's own stdout and return `True` when a
    decision was present. Mirrors `prep_researcher._forward_decision` exactly."""
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


def _root_sha(root):
    result = process.run(["git", "rev-parse", "HEAD"], cwd=root)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _build_reentrancy(issue_envelope):
    """The reentrancy facts (docs/specs/question-resolver.md "Invariants": revise, never duplicate). The
    >1 case never reaches here — `gh_gather` already forwarded MARKER_AMBIGUOUS. So `marker_comment_count`
    is 0 (fresh) or 1 (revise); on revise, stage the prior comment's id/url + body for the flow to show
    the operator and to pass to `gh_persist.py comment --delete-marker-id` on the replace."""
    count = issue_envelope.get("marker_comment_count") or 0
    present = bool(issue_envelope.get("marker_comment_present"))
    reentrancy = {
        "mode": "revise" if present else "fresh",
        "marker_comment_present": present,
        "marker_comment_count": count,
    }
    if present:
        prior = {
            "present": True,
            "comment_id": issue_envelope.get("marker_comment_id"),
            "comment_url": issue_envelope.get("marker_comment_url"),
            "body_mode": issue_envelope.get("marker_comment_mode"),
        }
        if issue_envelope.get("marker_comment_mode") == "path":
            prior["body_path"] = issue_envelope.get("marker_comment_path")
        else:
            prior["body"] = issue_envelope.get("marker_comment_body")
        reentrancy["prior_decision"] = prior
    return reentrancy


def _build_attention(is_question, target, blocking, grounding_docs=None, catalogue_absent=False):
    """Script-detectable conditions worth surfacing with evidence (architecture.md §4) — facts the
    router acts on via its gates, not decision cards from prep. `grounding_docs` is `None` when the
    catalogue was not read (a non-question issue stops before any audit), the prep_planner three-state
    shape; an absent catalogue is attention, never a decision — the audit proceeds on the docs the
    question itself cites (skills/_shared/doc-catalogue.md)."""
    attention = []
    if not is_question:
        attention.append(
            "issue #%s is not a `question` issue (no `question` label) — the resolver stops and points "
            "at `/ctrl-freaq:resolver` for build work" % target["number"]
        )
    if blocking:
        attention.append(
            "this question natively blocks %d build issue(s): %s — name them in the decision comment's "
            "`## Unblocks`" % (len(blocking), ", ".join("#%s" % b.get("number") for b in blocking))
        )
    if grounding_docs is not None:
        if catalogue_absent:
            attention.append(
                "no doc catalogue in docs/README.md — the constraint audit checks only the docs the "
                "question cites; run /ctrl-freaq:setup to declare this repo's grounding docs"
            )
        elif not grounding_docs:
            attention.append(
                "doc catalogue declares no documents — the constraint audit checks only the docs the "
                "question cites"
            )
        for missing in doc_catalogue.missing_entry_paths(grounding_docs):
            attention.append(
                "doc catalogue names '%s', absent in this checkout — a stale entry, or a doc this "
                "branch has not merged yet" % missing
            )
    return attention


def build_facts(issue, repo, root=".", scratch_dir=None, cwd=None, env=None):
    """Assemble the resolver's complete facts block and return the envelope dict WITHOUT printing it (the
    testable core, mirroring `prep_researcher.build_facts`). Returns `None` after a `needs_decision`
    envelope has already been emitted on stdout (AUTH_REQUIRED or the >1-marker MARKER_AMBIGUOUS)."""
    root = str(Path(root).resolve())
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-question-resolver-%s" % issue
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    exit_code, issue_envelope = gh_gather.run(
        str(issue),
        repo,
        marker_prefix=DECISION_MARKER,
        scratch_dir=scratch_dir,
        env=env,
        stream=_DiscardStream(),
    )
    if issue_envelope is not None and issue_envelope.get("status") == "needs_decision":
        if _forward_decision(issue_envelope["decision"], notices=issue_envelope.get("notices")):
            return None
    if exit_code != 0:
        sys.stderr.write(
            "prep_question_resolver: gh_gather on issue #%s failed (exit %d)\n" % (issue, exit_code)
        )
        sys.exit(1)

    labels = [label.get("name") for label in issue_envelope.get("labels") or []]
    is_question = QUESTION_LABEL in labels
    blocking = issue_envelope.get("blocking") or []
    target = {
        "kind": "issue",
        "number": issue_envelope["number"],
        "title": issue_envelope["title"],
        "state": issue_envelope["state"],
        "labels": labels,
    }

    sections = {
        key: value
        for key, value in issue_envelope.items()
        if key.startswith(("issue_body", "thread", "marker_comment"))
    }

    # The constraint audit's doc set — read at root's working tree, the vantage the audit reads docs
    # at. Only for a question issue: a non-question stops at the router, so its catalogue is noise.
    grounding_docs, catalogue_notices = (
        doc_catalogue.read_catalogue(root) if is_question else (None, [])
    )

    facts = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root, "sha": _root_sha(root)},
        "target": target,
        "is_question": is_question,
        "reentrancy": _build_reentrancy(issue_envelope),
        "already_closed": (issue_envelope.get("state") or "").upper() == "CLOSED",
        "blocking": blocking,
        "blocked_by": issue_envelope.get("blocked_by") or [],
        "deps_available": issue_envelope.get("deps_available"),
        "sections": sections,
        "grounding_docs": grounding_docs if grounding_docs is not None else [],
        "attention": _build_attention(
            is_question,
            target,
            blocking,
            grounding_docs,
            DOC_CATALOGUE_ABSENT in catalogue_notices,
        ),
        # The gather's success-path notices are not forwarded, except a skipped untrusted decision
        # marker (#179): a decision that reads absent because a stranger wrote it must say so.
        "notices": trust.untrusted_notices(issue_envelope.get("notices")) + list(catalogue_notices),
    }
    return facts


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue", help="the question-issue number (required)")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--root", default=".", help="project root (architecture.md §6 vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for spilled thread + the staged decision.md "
        "(default: /tmp/gh-question-resolver-<issue>)",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="explicit working directory (reserved for parity with the other preps' --cwd knob; the "
        "gather scopes every gh call with --repo, so it is unused in normal runs)",
    )
    args = parser.parse_args(argv)

    facts = build_facts(
        args.issue, args.repo, root=args.root, scratch_dir=args.scratch_dir, cwd=args.cwd
    )
    if facts is None:
        return 0
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
