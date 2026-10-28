#!/usr/bin/env python3
"""prep_researcher.py — the researcher's complete facts block in one call (architecture.md §4;
docs/implementation.md S16; docs/specs/researcher.md). Assembles the session's entire starting
state — the three-way mode vector (broad / targeted / revise), the target issue + thread +
`<!-- issue-research:v1 -->` dossier-marker envelope (the revise signal), the manifest/governing-doc
inventory the currency check reads, `suggested_playbook`, and `attention` — as ONE JSON envelope on
stdout, so the researcher session's startup is one Python process, never a subprocess chain.

Composition (architecture.md §2 "compose the executors in-process"; §1 "the only external
processes any script may spawn are git/gh")::

    gh_gather.run(..., stream=)   -- the target issue's body/thread + the `<!-- issue-research:v1 -->`
                                     dossier-marker envelope (one round-trip; the marker via
                                     `marker_prefix`). A marker present -> revise mode; its `id` is
                                     what the flow's `PERSIST_COMMENT` (via `gh_persist.py comment
                                     --delete-marker-id`) deletes on delete-and-repost.

`gh_gather.run` exposes the S8-locked emit-through-a-stream shape (docs/specs/baseline.md §5); this
prep passes a discard stream and reads the returned ``(exit, envelope)`` directly, forwarding any
``needs_decision`` (``MARKER_AMBIGUOUS`` for a duplicate dossier comment, ``AUTH_REQUIRED``) verbatim
and emitting exactly one envelope of its own. No ``redirect_stdout``/``io.StringIO`` capture of another
script's stdout is used anywhere in this module (S9-on rule; docs/specs/baseline.md §5).

``git rev-parse HEAD`` (root's own SHA, informational only) has no existing executor core —
architecture.md §1 permits any script to spawn `git`/`gh` directly via ``pipelib.process.run``, the
same precedent `prep_drafter.py`/`prep_planner.py`/`prep_resolver.py`/`prep_evaluator.py` already
established for their own prep-owned direct calls.

Usage::

    prep_researcher.py <issue> <owner/repo> [--question TEXT ...] [--root PATH]
                       [--scratch-dir PATH] [--cwd PATH]

``<issue>`` is required — the researcher researches an already-filed issue (docs/specs/researcher.md
"Overview"; it never files one). ``--question`` is the operator-supplied ``— <question>`` from the
targeted trigger (repeatable), and selects targeted vs broad mode (see ``vector.mode`` below); its
text rides in ``vector.questions`` so the playbook has it as a fact. ``--root`` defaults to ``.``
(the project root — architecture.md §6's read-only trust vantage). ``--scratch-dir`` defaults to
``/tmp/gh-researcher-<issue>`` (CLAUDE.md's ``/tmp/gh-<skill>-<N>/`` convention) when omitted — this
is where the flow later stages the dossier body (``research.md``) before persisting; prep itself
stages nothing (no dossier exists yet at session start).

**No workspace — the researcher grounds on the CURRENT checkout, not a pinned ref (S14 root-only
precedent; docs/specs/researcher.md "Overview": "no epic/story branching, no worktree involvement").**
v1 discovers the project's stack and governing docs at runtime from wherever the
working tree happens to be (``read whatever the repo actually has``), with no requirement that the
tree be clean or on ``main`` — the manifest inventory is a **currency-check input** for filtering web
research, never a build/merge gate. Unlike the resolver/evaluator (whose gate config reads from the
ambient checkout's working tree, and whose sessions run inside an asserted worktree —
architecture.md §6's "Gate config is pinned to trust"), the researcher reads no PR head and gates no
merge, so architecture.md §6's threat model ("a PR weakening its own gates") cannot apply. ``root``
therefore carries only ``{path, sha}`` (a plain, non-gating ``git rev-parse HEAD`` — informational,
never enforced) — no ``workspace.py`` import, no ``fresh`` key. Same root-only shape `prep_drafter.py`
adopted (see that module's "No workspace" note), for the same threat-model reason.

**``vector.mode`` — the THREE-value closed set ``"broad"`` / ``"targeted"`` / ``"revise"``**
(docs/specs/researcher.md "Modes"). Derived purely mechanically from two script-visible signals — the
dossier marker's presence and whether the operator supplied a ``--question``:

  - an ``<!-- issue-research:v1 -->`` dossier already exists on the issue -> ``"revise"``
    (docs/specs/researcher.md "Artifacts read": an existing dossier is the revise-mode trigger —
    the dominant one; the flow refreshes what
    changed and delete-and-reposts, whether or not a ``--question`` narrows the refresh).
  - no dossier, a ``--question`` supplied -> ``"targeted"`` (the questions are given,
    typically because the planner hit a specific knowledge gap and routed here; the flow skips
    derivation and the confirm gate).
  - no dossier, no ``--question`` -> ``"broad"`` (the default first run; the flow
    derives the questions, runs the decline gate, and confirms them before spending fetches).

**``suggested_playbook``** (architecture.md §5 "Prep proposes; the router confirms") maps the mode onto
its thin variant playbook: ``"broad.md"`` / ``"targeted.md"`` / ``"revise.md"``. Each variant supplies
only its distinct front-end (broad derives questions + runs the decline and confirm gates; targeted uses
the given questions; revise reads the prior dossier and refreshes only what changed) and then runs the
shared ``research-spine.md`` (gather -> synthesize -> validate -> persist -> handoff). The split is the
§5-honest one — the three modes differ in genuine front-end/persist **actions** (broad can exit posting
nothing; revise delete-and-reposts and shows a diff), not merely in values — the largest single loaded
file (router + ``research-spine.md``) stays under half the v1 router's line count (docs/specs/baseline.md §1), recorded in
``docs/specs/parity/researcher.md``. The router routing table is byte-consistent with this map.

**Manifest / governing-doc inventory (docs/specs/researcher.md "Deterministic steps",
"Manifest/doc inventory list").**
A filesystem inventory at ``root`` of whichever dependency manifest and governing-doc files actually
exist — the *inventory* is deterministic; the *reading and synthesizing* into a one-line stack-context
summary stays judgment (the playbook's Step 3). Manifests carry the pinned versions that are "the
single most important signal for is-my-recall-current". The candidate names are v1's exact list
(that same row's list) treated as **examples, not a checklist** — first-match reporting per candidate, plus a
shallow root-level glob for the pattern-named ones (``*.csproj`` / ``*.bazel``). Scanned at
root-level (not a full recursive tree walk — bounded, and it never blows up on ``node_modules`` /
vendored trees); a deeply-nested manifest the shallow scan misses is still reachable by the model's
own grep ("grep for version pins rather than assuming the repo has none" — same row), so the
inventory is a convenience signal, never a gate.

Exit codes (architecture.md §3): 0 with the facts-block envelope present (``status`` is ``"ok"`` or
``"needs_decision"``); 2 on a usage error (no envelope). Any other non-zero is an unclassified hard
`gh`/`git` failure surfaced by the composed gather — stderr carries the faithful error.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_gather  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)
from pipelib import process, trust  # noqa: E402
from pipelib.envelope import emit_needs_decision, emit_ok  # noqa: E402

# The dossier marker (docs/specs/researcher.md "Artifacts written") — the literal
# first line of the research dossier comment, and gh_gather's `marker_prefix` for revise detection.
RESEARCH_MARKER = "<!-- issue-research:v1 -->"

# Dependency / build manifest candidates (the list in docs/specs/researcher.md's "Manifest/doc
# inventory list" row) — treated as EXAMPLES, not a checklist. Exact-name candidates checked at
# root; the pattern-named ones are a shallow root glob.
_MANIFEST_EXACT = (
    "package.json",
    "Gemfile",
    "Gemfile.lock",
    "go.mod",
    "Cargo.toml",
    "pyproject.toml",
    "requirements.txt",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "Package.swift",
    "project.yml",
    "Project.swift",
)
_MANIFEST_GLOBS = ("*.csproj", "*.bazel")

# Governing-doc candidates (same spec row) — README / CONTRIBUTING / CLAUDE.md at root, plus a listing
# of everything under `docs/` (v1: "anything under docs/"). The CLAUDE.md `@`-reference expansion is
# a READING concern (judgment), so prep reports only CLAUDE.md's presence, never resolves the graph.
_GOVERNING_DOC_EXACT = ("README.md", "README", "CONTRIBUTING.md", "CONTRIBUTING", "CLAUDE.md")


class _DiscardStream:
    """A minimal write-sink for `gh_gather.run(stream=...)` — see `prep_drafter._DiscardStream` /
    `prep_evaluator._DiscardStream` for the full rationale (restated locally per those modules' own
    note: sharing a two-method sink across prep modules is not the in-process composition
    architecture.md §2 asks for)."""

    def write(self, _data):
        return None

    def flush(self):
        return None


def _forward_decision(decision, notices=None):
    """Emit a composed core's returned `decision` AS-IS on prep's own stdout and return `True` when
    a decision was present. Mirrors `prep_drafter._forward_decision` exactly."""
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return True
    return False


def _root_sha(root):
    result = process.run(["git", "rev-parse", "HEAD"], cwd=root)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Manifest / governing-doc inventory (docs/specs/researcher.md "Deterministic steps").
# ---------------------------------------------------------------------------


def _manifest_inventory(root):
    """Root-level inventory of the dependency/build manifests that actually exist
    (docs/specs/researcher.md "Manifest/doc inventory list").
    `present` means "at least one manifest was found". `files` is the relative names found (sorted);
    `candidates_checked` rides along so a caller sees the exact search set without re-deriving it."""
    root_path = Path(root)
    files = []
    for name in _MANIFEST_EXACT:
        if (root_path / name).is_file():
            files.append(name)
    for pattern in _MANIFEST_GLOBS:
        for match in sorted(root_path.glob(pattern)):
            if match.is_file():
                files.append(match.name)
    files = sorted(set(files))
    return {
        "present": len(files) > 0,
        "files": files,
        "candidates_checked": list(_MANIFEST_EXACT) + list(_MANIFEST_GLOBS),
    }


def _doc_inventory(root):
    """Root-level governing-doc inventory (same spec row): README / CONTRIBUTING / CLAUDE.md at root,
    plus a listing of `docs/*.md` ("anything under docs/"). `present` means at least one was found."""
    root_path = Path(root)
    files = []
    for name in _GOVERNING_DOC_EXACT:
        if (root_path / name).is_file():
            files.append(name)
    docs_dir = root_path / "docs"
    if docs_dir.is_dir():
        for match in sorted(docs_dir.glob("*.md")):
            if match.is_file():
                files.append("docs/%s" % match.name)
    return {"present": len(files) > 0, "files": files}


# ---------------------------------------------------------------------------
# Mode / revise facts / attention
# ---------------------------------------------------------------------------


def _mode(marker_present, questions):
    if marker_present:
        return "revise"
    if questions:
        return "targeted"
    return "broad"


def _suggested_playbook(mode):
    """Map the mode onto its thin variant playbook filename (see the module docstring's
    `suggested_playbook` paragraph). One variant per mode; each runs the shared `research-spine.md`."""
    return "%s.md" % mode


def _build_revise_facts(issue_envelope):
    """Revise-mode-only facts (docs/specs/researcher.md "Artifacts read" + "Stage dossier to disk"):
    the existing dossier comment's id/url (the flow
    passes the id to `gh_persist.py comment --delete-marker-id` on delete-and-repost) and its staged
    body (the flow reads it to refresh what changed, never re-researching untouched sections)."""
    dossier = {
        "present": True,
        "comment_id": issue_envelope.get("marker_comment_id"),
        "comment_url": issue_envelope.get("marker_comment_url"),
        "body_mode": issue_envelope.get("marker_comment_mode"),
    }
    if issue_envelope.get("marker_comment_mode") == "path":
        dossier["body_path"] = issue_envelope.get("marker_comment_path")
    else:
        dossier["body"] = issue_envelope.get("marker_comment_body")
    return {"dossier": dossier}


def _build_attention(target):
    """Script-detectable conditions worth surfacing with evidence (architecture.md §4). The researcher
    has no build/merge gate, so this is normally empty — the one genuine script-visible ambiguity
    (more than one dossier comment) is already a `MARKER_AMBIGUOUS` decision from the gather, not an
    attention line. A closed target is surfaced as an INFORMATIONAL note (not a gate — v1 has no
    closed-issue gate for the researcher) so the operator sees it before posting onto a closed issue.
    """
    attention = []
    if target is not None and (target.get("state") or "").upper() == "CLOSED":
        attention.append(
            "target issue #%s is closed (informational — the researcher normally researches open "
            "issues; not a gate)" % target["number"]
        )
    return attention


# ---------------------------------------------------------------------------
# Facts-block assembly
# ---------------------------------------------------------------------------


def build_facts(issue, repo, questions=None, root=".", scratch_dir=None, cwd=None):
    """Assemble the researcher's complete facts block and return the envelope dict WITHOUT printing
    it (the testable core, mirroring `prep_drafter.build_facts`). Returns `None` after a
    `needs_decision` envelope has already been emitted on stdout."""
    root = str(Path(root).resolve())
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-researcher-%s" % issue
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    root_sha = _root_sha(root)

    # Target-issue gather — issue body + full thread + the `<!-- issue-research:v1 -->` dossier
    # marker, in one round-trip (marker via marker_prefix). No extra_json (the researcher coordinates
    # around no in-flight PR / project item the way the drafter's revise does).
    exit_code, issue_envelope = gh_gather.run(
        str(issue),
        repo,
        marker_prefix=RESEARCH_MARKER,
        scratch_dir=scratch_dir,
        env=None,
        stream=_DiscardStream(),
    )
    if issue_envelope is not None and issue_envelope.get("status") == "needs_decision":
        if _forward_decision(issue_envelope["decision"], notices=issue_envelope.get("notices")):
            return None
    if exit_code != 0:
        sys.stderr.write(
            "prep_researcher: gh_gather on issue #%s failed (exit %d)\n" % (issue, exit_code)
        )
        sys.exit(1)

    marker_present = bool(issue_envelope.get("marker_comment_present"))
    issue_labels = [label.get("name") for label in issue_envelope.get("labels") or []]
    target = {
        "kind": "issue",
        "number": issue_envelope["number"],
        "title": issue_envelope["title"],
        "state": issue_envelope["state"],
        "labels": issue_labels,
    }

    mode = _mode(marker_present, questions)
    revise_facts = _build_revise_facts(issue_envelope) if marker_present else None

    sections = {
        key: value
        for key, value in issue_envelope.items()
        if key.startswith(("issue_body", "thread", "marker_comment"))
    }

    facts = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root, "sha": root_sha},
        "target": target,
        "vector": {"mode": mode, "questions": list(questions) if questions else None},
        "suggested_playbook": _suggested_playbook(mode),
        "inventory": {
            "manifests": _manifest_inventory(root),
            "docs": _doc_inventory(root),
        },
        "sections": sections,
        "attention": _build_attention(target),
        # The gather's success-path notices are not forwarded, except a skipped untrusted dossier
        # (#179): a dossier that reads absent because a stranger wrote it must say so.
        "notices": trust.untrusted_notices(issue_envelope.get("notices")),
    }
    if revise_facts is not None:
        facts["revise"] = revise_facts

    return facts


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue", help="issue number (required — the researcher researches a filed issue)")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument(
        "--question",
        action="append",
        default=None,
        metavar="TEXT",
        help="an operator-supplied research question (the `— <question>` from the targeted trigger); "
        "repeatable. Its presence selects targeted mode over broad (see vector.mode).",
    )
    parser.add_argument("--root", default=".", help="project root (architecture.md §6 vantage)")
    parser.add_argument(
        "--scratch-dir",
        default=None,
        help="scratch dir for spilled sections + the flow's staged dossier "
        "(default: /tmp/gh-researcher-<issue>)",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="explicit working directory (reserved for parity with the other preps' --cwd knob; the "
        "researcher gather scopes every gh call with --repo, so it is unused in normal runs)",
    )
    args = parser.parse_args(argv)

    facts = build_facts(
        args.issue,
        args.repo,
        questions=args.question,
        root=args.root,
        scratch_dir=args.scratch_dir,
        cwd=args.cwd,
    )
    if facts is None:
        return 0
    notices = facts.pop("notices", [])
    emit_ok(payload=facts, notices=notices)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
