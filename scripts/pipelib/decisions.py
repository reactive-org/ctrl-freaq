"""The closed decision-code vocabulary (architecture.md §3) and the ``needs_decision`` payload
builder.

Every script and judgment sub-agent in v2 signals an operator-only choice with one of exactly
these 15 codes — contract tokens, exact strings, never paraphrased or extended ad hoc. Adding a
code is a contract change: update architecture.md §3 and this module together, in the same
change, or ``tests/test_pipelib.py``'s drift-check test (which parses the code list back out of
the doc and asserts it equals :data:`DECISION_CODES`) fails.

Meaning + canonical emitter per code (architecture.md §3):

- ``AUTH_REQUIRED`` — `gh` authentication/permission failure; detected by the pipelib subprocess
  runner (:mod:`pipelib.process`), any script.
- ``EMPTY_BODY_FILE`` — body-bearing write given an empty or missing staged file; ``gh_persist.py``.
- ``BODY_TOO_LONG`` — body-bearing write given a staged file over the platform's per-body character
  limit; ``gh_persist.py``. The same gate as ``EMPTY_BODY_FILE`` at the other end, and raised for the
  same reason: GitHub rejects the write at every endpoint, so without this the caller gets a raw `gh`
  error at exit 1 with no envelope, after the whole session's work is spent.
- ``MARKER_AMBIGUOUS`` — more than one candidate marker comment/block where the contract expects
  one; the gathers + ``config_block.py``.
- ``TARGET_IS_PR`` — the requested issue number resolves to a pull request, not an issue;
  ``gh_gather.py``, emitted before any further fetch so a composing prep forwards it before any
  workspace side effect.
- ``DOD_MALFORMED`` — a DoD bullet or annotation outside the closed set; ``parse.py dod``.
- ``PHASES_MALFORMED`` — a plan ``## Phases`` section that doesn't parse; ``parse.py phases``.
- ``BRANCH_IN_USE`` — the branch is checked out in another worktree (``ensure --work``, reached
  via the landing tools and workspace-open); ``workspace.py``.
- ``WORKSPACE_MISMATCH`` — the ambient checkout is not the expected workspace (wrong or detached
  branch, session at the project root, checkout stale against the expected remote state, or
  removal attempted from inside the target worktree); ``workspace.py``, forwarded by the stage
  preps.
- ``PLAN_MISSING`` — a required plan is absent; prep scripts + the state-distiller.
- ``THREAD_SUPERSEDED_PLAN`` — thread direction supersedes the recorded plan; the state-distiller.
- ``AMBIGUOUS`` — residual non-marker ambiguity (e.g. multiple epic-branch matches); scripts +
  sub-agents.
- ``BLOCKED_ON_USER`` — progress requires operator input beyond a listable option set; sub-agents.
- ``TARGET_IS_SLICE`` — the requested issue is a deliverable slice (its native parent classifies as
  a non-epic, and a non-epic's sub-issues are slices by construction); ``prep_workspace_open.py``
  and ``prep_planner.py``. A slice has no branch and no PR of its own, so opening a workspace for it
  promotes it to a story and planning it standalone authors a plan competing with its parent's.
  Classification is lexical on the parent, so an epic carrying neither the ``epic`` label nor an
  ``Epic:`` title prefix reads as a non-epic — the card's options include relabelling it and
  re-running, external remedies like every other card's rather than a proceed-anyway override
  neither prep implements.
- ``DOC_CATALOGUE_MALFORMED`` — a ``<!-- doc-catalogue -->`` block about to land does not hold
  (duplicate/unterminated markers, an interior line readers would skip, a repeated path, or a path
  absent from the checkout); ``doc_catalogue.py check``, run by every catalogue writer before it
  commits. Readers skip a bad line silently, so the gate sits on the write side, where the session
  that broke the line can still fix it.
"""

# Contract tokens — exact strings, order matches architecture.md §3's enumeration.
AUTH_REQUIRED = "AUTH_REQUIRED"
EMPTY_BODY_FILE = "EMPTY_BODY_FILE"
BODY_TOO_LONG = "BODY_TOO_LONG"
MARKER_AMBIGUOUS = "MARKER_AMBIGUOUS"
TARGET_IS_PR = "TARGET_IS_PR"
DOD_MALFORMED = "DOD_MALFORMED"
PHASES_MALFORMED = "PHASES_MALFORMED"
BRANCH_IN_USE = "BRANCH_IN_USE"
WORKSPACE_MISMATCH = "WORKSPACE_MISMATCH"
PLAN_MISSING = "PLAN_MISSING"
THREAD_SUPERSEDED_PLAN = "THREAD_SUPERSEDED_PLAN"
AMBIGUOUS = "AMBIGUOUS"
BLOCKED_ON_USER = "BLOCKED_ON_USER"
TARGET_IS_SLICE = "TARGET_IS_SLICE"
DOC_CATALOGUE_MALFORMED = "DOC_CATALOGUE_MALFORMED"

# The closed set, as a frozenset for O(1) membership tests. This is the single object the
# drift-check test compares against the doc-parsed set, and the object other pipelib code
# (e.g. the conformance assertions) validates a decision's ``code`` field against.
DECISION_CODES = frozenset(
    {
        AUTH_REQUIRED,
        EMPTY_BODY_FILE,
        BODY_TOO_LONG,
        MARKER_AMBIGUOUS,
        TARGET_IS_PR,
        DOD_MALFORMED,
        PHASES_MALFORMED,
        BRANCH_IN_USE,
        WORKSPACE_MISMATCH,
        PLAN_MISSING,
        THREAD_SUPERSEDED_PLAN,
        AMBIGUOUS,
        BLOCKED_ON_USER,
        TARGET_IS_SLICE,
        DOC_CATALOGUE_MALFORMED,
    }
)

# Non-blocking notice codes ride in ``notices: []`` (architecture.md §3) rather than as a
# ``needs_decision`` — they are not part of the closed decision-code set and are not exhaustively
# enumerated here (a script may introduce a new notice without a contract change, since notices
# are informational, not gating). ``DEPS_UNSUPPORTED``, ``SUBISSUES_UNSUPPORTED`` and
# ``DOC_CATALOGUE_ABSENT`` are named because pipelib/tests reference them directly (the two
# capability-gated native-relation degradations, plus the missing-grounding-declaration one).
DEPS_UNSUPPORTED = "DEPS_UNSUPPORTED"

# The native parent/sub-issue relation (``gh issue create --parent``, ``gh issue view --json
# parent,subIssues,subIssuesSummary``) is unavailable — the installed `gh` predates the flags, or
# the repo/host doesn't serve the feature. Kept SEPARATE from ``DEPS_UNSUPPORTED`` because the two
# features gate independently: blocked-by/blocking dependencies and parent/sub-issue hierarchy are
# different GitHub relations, and reporting one when the other degraded would send a reader to the
# wrong fallback. On this notice, epic↔story hierarchy readers fall back to parsing the legacy
# ``## Stories`` checklist (skills/_shared/epic-story-hierarchy.md).
SUBISSUES_UNSUPPORTED = "SUBISSUES_UNSUPPORTED"

# A READ of the parent/sub-issue relation could not retrieve one field (`gh issue view --json parent`
# rejected), so a caller that needed the CURRENT parent of an issue does not know it. Kept separate
# from ``SUBISSUES_UNSUPPORTED`` for the same "wrong fallback" reason that keeps that one separate from
# ``DEPS_UNSUPPORTED``: consumers read ``SUBISSUES_UNSUPPORTED`` as a WRITE outcome — the relation was
# not established, so a filed child is unparented — and the slicer's flow aborts its cut on it. A
# failed read has written nothing and leaves nothing unparented; conflating the two would abort a cut
# and report a child that does not exist. On this notice a reader treats the parent as UNKNOWN, never
# as absent.
SUBISSUE_FIELD_UNAVAILABLE = "SUBISSUE_FIELD_UNAVAILABLE"

# A write carrying ``--attach`` (gh >= 2.99 uploads the file and rewrites the body's reference to a
# ``user-attachments`` URL) LANDED, but gh reported that some attachments did not upload — gh exits
# non-zero yet still prints the written URL. A notice, not a failure: the issue/PR/comment exists,
# so a caller that read the exit as "nothing written" would re-run and double-file. gh's stderr
# rides in the payload as ``attach_stderr``.
ATTACH_PARTIAL = "ATTACH_PARTIAL"

# A body write carrying ``--attach`` landed, but reading the posted body back (the copy whose
# references gh rewrote) failed, so no ``posted_body_path`` is reported. The caller's next restage
# of that body has no rewritten base; it re-reads the body through its gather instead.
ATTACH_READBACK_UNAVAILABLE = "ATTACH_READBACK_UNAVAILABLE"

# The consuming repo declares no grounding documents: no ``docs/README.md``, no
# ``<!-- doc-catalogue -->`` block in it, or a malformed one
# (skills/_shared/doc-catalogue.md). Deliberately loud rather than silent, because the fallback is
# *no doc grounding at all* — there is no built-in path list to fall back to and no filesystem walk,
# so a planner or drafter that would once have read a hardcoded ``docs/prd.md`` now grounds on
# nothing. The remedy is always the same (author the catalogue, or re-run `setup`), which is why one
# notice serves every reader; what a reader DOES about it differs by consumer, and that asymmetry
# lives in the shared contract, not here.
DOC_CATALOGUE_ABSENT = "DOC_CATALOGUE_ABSENT"


def needs_decision(code, summary, context=None, options=None):
    """Build the ``decision`` payload object for a ``needs_decision`` envelope.

    ``code`` must be one of :data:`DECISION_CODES` — a ``ValueError`` on anything else means an
    invalid code is caught at the point it's constructed, not silently shipped in an envelope.
    ``context`` and ``options`` default to ``{}`` / ``[]`` (never omitted; architecture.md §3's
    decision payload shape always carries all four keys).

    Returns the payload matching architecture.md §3 verbatim:
    ``{"code": "<CODE>", "summary": "…", "context": {…}, "options": ["…"]}`` — the caller (see
    :func:`pipelib.envelope.emit_needs_decision`) wraps this under ``{"status": "needs_decision",
    "decision": ...}``.
    """
    if code not in DECISION_CODES:
        raise ValueError(
            "pipelib.decisions.needs_decision: %r is not in the closed decision-code set %r"
            % (code, sorted(DECISION_CODES))
        )
    if not isinstance(summary, str) or not summary:
        raise ValueError("pipelib.decisions.needs_decision: summary must be a non-empty string")
    return {
        "code": code,
        "summary": summary,
        "context": dict(context) if context is not None else {},
        "options": list(options) if options is not None else [],
    }
