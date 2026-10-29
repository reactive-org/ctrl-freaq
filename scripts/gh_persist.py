#!/usr/bin/env python3
"""gh_persist.py — the single write path for GitHub issue/PR persistence (architecture.md §7),
ported from ``gh-persist.sh`` (v1) under the architecture.md §3 envelope.

Subcommands (unchanged surface from v1 — same names, same flags, same positional order — plus
``create-pr``/``edit-labels``/``close-pr``, additive-only v2 extensions, see below):

    gh_persist.py create      <repo> <body_path> --title <title> [--label L]...
                               [--blocked-by <nums/urls>] [--blocking <nums/urls>]
                               [--parent <num/url>] [--attach F[#alt]]...         [--dry-run]
    gh_persist.py create-pr   <repo> <body_path> --title <title> --base <ref> --head <ref>
                               [--draft] [--attach F[#alt]]...                  [--dry-run]
    gh_persist.py edit-body   <repo> <issue>     <body_path> [--attach F[#alt]]...  [--dry-run]
    gh_persist.py edit-pr-body <repo> <pr>       <body_path> [--attach F[#alt]]...  [--dry-run]
    gh_persist.py edit-title  <repo> <issue>     --title <title>                  [--dry-run]
    gh_persist.py edit-pr-title <repo> <pr>      --title <title>                  [--dry-run]
    gh_persist.py edit-labels <repo> <issue>     [--add L]... [--remove L]...     [--dry-run]
    gh_persist.py link        <repo> <issue>
                               [--add-blocked-by N]... [--remove-blocked-by N]...
                               [--add-blocking N]...   [--remove-blocking N]...     [--dry-run]
    gh_persist.py add-parent  <repo> <child> <parent>                              [--dry-run]
    gh_persist.py comment     <repo> <target> <id> <body_path>
                               [--review-action approve|comment|request-changes]
                               [--delete-marker-id <id>] [--attach F[#alt]]...      [--dry-run]
    gh_persist.py edit-comment <repo> <comment_id> <body_path>                      [--dry-run]
    gh_persist.py review-post <repo> <pr> <spec.json>                               [--dry-run]
    gh_persist.py review-reply <repo> <pr> --thread <node-id> --head <sha> [--f <n>] <body_path>
                               [--decision accept|changes|dismiss]                 [--dry-run]
    gh_persist.py close       <repo> <issue> [--reason completed|"not planned"]     [--dry-run]
    gh_persist.py close-pr    <repo> <pr>     [--comment-file <path>]               [--dry-run]
    gh_persist.py reopen      <repo> <issue>                                        [--dry-run]

``<target>`` for ``comment`` is one of: issue | pr | pr-review. ``--review-action`` is required
when target=pr-review and forbidden otherwise; so is ``--attach`` forbidden there.

The body the script posts is the byte stream the *caller* already staged to its own scratch dir
(the drafter's ``/tmp/gh-drafter-<slug>/story-N.md``, the planner's ``/tmp/gh-planner-<N>/plan.md``,
etc. — v2: prep-script / playbook scratch dirs, same convention). Nothing re-serializes the body
across a prompt boundary — this closes the #626/#627 empty-body race (see ``_verify_body_file``
below) exactly as v1's leading ``test -s`` did; the only change here is that failing the gate is
now a ``needs_decision`` envelope (architecture.md §3: "needs_decision is a valid outcome, not an
error") rather than v1's bare ``exit 2`` + stderr line, because ``EMPTY_BODY_FILE`` is a §3 closed
decision code with a canonical emitter of this script.

``create-pr`` (added S10, additive-only — architecture.md §2's "if a needed operation has no
script, extend a script") is the resolver's PR-open write path — the one persisted-artifact write
the v1-ported six subcommands never covered (v1's own ``gh-persist.sh create`` was ``gh issue
create`` only; the v1 resolver hand-rolled a raw ``gh pr create``, the exact Rule-7 divergence
``docs/specs/resolver.md``'s "Known bugs / gaps" flags). It mirrors ``create``'s body-bearing-write
shape exactly: the same staged-file-path convention, the same leading ``_verify_body_file`` empty-
body gate, the same unconditional ``body_bytes``/``body_sha256`` receipt (dry-run or not), the same
``--dry-run`` preview convention, and the same ``AUTH_REQUIRED`` classification via the pipelib
runner. ``--base``/``--head`` are **required and explicit** — no ambient-cwd branch inference
(architecture.md §6 "No ambient cwd"): the caller (a prep-derived fact, e.g. the work workspace's
`base_ref`) always names both ends. ``--draft`` is optional and capability-universal (every ``gh``
version /repo supports it — no capability gate needed, unlike the native-dependency flags below);
it exists because the v1 resolver's multi-phase fresh-PR-open path passes ``--draft`` and flips
draft→ready via a separate ``gh pr ready`` once every planned phase has shipped
(``docs/specs/resolver.md`` "On the last-planned-
phase-shipped handoff, flip the PR draft→ready" invariant) — that flip is the pre-existing
sanctioned raw-``gh`` PR-state toggle (architecture.md §10), unaffected by this op. No native-
dependency support (``--blocked-by``/``--blocking``) is added to ``create-pr``: GitHub's issue-
dependency feature is a construct on **issues**, not PRs — the resolver's PR-open write never sets
one.

``edit-labels`` and ``close-pr`` (added S13, additive-only — the same architecture.md §2 "if a
needed operation has no script, extend a script" precedent ``create-pr`` established at S10) close
the two v1-write-path gaps the planner cutover surfaced: v1's ``planned``-label apply
(``docs/specs/planner.md``, ``gh issue edit --add-label``) and the HARD-revise
predecessor-PR close (``skills/planner/references/revise-reconciliation.md``'s "Start
fresh", ``gh pr close --comment``). Both are additive: every existing op's behavior, argv shape, and
envelope are byte-identical, and no existing test changed.

- ``edit-labels`` is **bodyless** (no ``--body-file``, no empty-body gate — mirrors
  ``close``/``reopen``, not ``comment``): a label add/remove is a relationship edit, not a body
  write. ``--add``/``--remove`` are each repeatable; at least one of the two is required (a usage
  error otherwise, mirroring ``link``'s "at least one relationship flag" rule) so a caller can never
  mistake a no-op invocation for a successful edit. No client-side idempotency special-casing —
  same discipline as ``close``/``reopen``: ``gh issue edit --add-label`` on an already-present label,
  and ``--remove-label`` on a label the issue doesn't carry, are themselves ``gh``/GitHub-API no-op
  successes (this script performs no "is it already set" pre-check), so a re-run of a partially-
  applied edit is always safe. Known future consumers (not this step's callers): S15 (drafter) and
  S16 (researcher, the ``researched`` label) — recorded here so a later cutover doesn't re-propose
  this op from scratch.
- ``close-pr`` closes a PR with an **optional** staged comment. ``gh pr close`` has no
  ``--body-file``/``--comment-file`` flag of its own (unlike every body-bearing op above) — the
  script reads the staged ``--comment-file`` itself and forwards its bytes as ``gh pr close``'s
  ``--comment <text>`` value, so the comment still crosses the *prompt* boundary as a path (the
  #626/#627 discipline), even though it crosses the *gh-argv* boundary as text (`gh` has no file
  form here to delegate to). The empty-body gate applies **only when** ``--comment-file`` is given
  (an included comment must be non-empty); omitting the flag is a bodyless close, exactly like
  ``close``. **The comment text is a cross-skill contract, not free prose**: this op's staged text
  becomes the PR's **close comment** (``gh pr close --comment``, a genuine comment, never the PR
  body/description) — the resolver's predecessor-PR detection greps a closed PR's close comment for
  the literal phrase ``Re-plan superseded this PR`` to find the branch a HARD re-plan superseded, for
  its ``-vN`` fresh-branch suffixing (``docs/specs/resolver.md`` "Deterministic steps": "Predecessor-PR
  detection"). The planner's HARD-revise flow (``skills/planner/playbooks/revise.md``) stages that
  exact phrase, byte-faithful, before calling this op — this script does not itself construct or
  validate the phrase (it is a generic bodyless-optional-comment PR close), the same way ``comment``
  does not validate marker-prefix conventions on the bodies it posts.

``edit-pr-body`` (additive-only, same precedent) is ``gh pr edit <pr> --body-file``. It exists
because ``edit-body`` is ``gh issue edit``, which **rejects a PR number** — so until now there was
no script-backed way to edit a PR body at all, and every caller that needed one (the resolver's
``## Phase tracker`` refresh; the epic integration PR's story-progress refresh, which is written on
every epic run while the PR is a draft) had no executor to reach for. It mirrors ``edit-body``
exactly: the same staged-file-path convention, the same leading ``_verify_body_file`` empty-body
gate, the same unconditional ``body_bytes``/``body_sha256`` receipt, the same ``--dry-run`` preview,
and the same ``AUTH_REQUIRED`` classification. The op name says ``pr`` because the ``gh`` noun
differs — a caller that passes a PR number to ``edit-body`` gets a ``gh`` error, not a silent
mis-write.

``edit-title``/``edit-pr-title`` (additive-only, same precedent) are the only ops that set the title
of an ALREADY-FILED issue/PR — until they existed, ``create``/``create-pr`` ``--title`` was the whole
title surface, so a flow that changed a title after filing had no executor to reach for and (skills
being forbidden raw ``gh`` writes, architecture.md §7 rule 7) silently dropped the change: the
drafter's revise mode and the slicer's S0 promotion both diff-show a ``Title old→new`` and take the
operator's explicit confirmation, then applied only ``edit-body`` + ``edit-labels``. The slicer case
was a correctness bug, not a cosmetic one — epic classification is LEXICAL
(``skills/_shared/epic-story-hierarchy.md``: an issue carrying neither an ``epic`` label nor an
``Epic:`` title prefix reads as a non-epic), so a promotion that never rewrote the title left the
target half-promoted. Both are **bodyless** (mirroring ``edit-labels``/``link``, not
``edit-body``): a title is not a body, so there is no ``--body-file``, no ``_verify_body_file``
empty-body gate, and no ``body_bytes``/``body_sha256`` receipt. That is also why the title is NOT a
flag on ``edit-body``: coupling the two would force a caller with a title-only change to restage and
rewrite the entire body just to reach it. ``--title`` is ``required``, which gives the same "a
caller can never mistake a no-op invocation for a successful edit" property ``edit-labels`` gets from
its at-least-one-flag rule; the value crosses the boundary as one argv element (never
shell-interpolated), exactly as ``create --title`` does. The issue/PR split has ``edit-body``'s
reason, unchanged: ``gh issue edit`` rejects a PR number, so the noun must be in the op name for a
mis-targeted call to fail loudly.

``edit-comment`` (additive-only, same precedent) updates an existing issue-thread comment IN PLACE,
by numeric REST id. Until it existed, replacing a marker comment meant delete-and-repost, which is
what destroys GitHub's own comment edit history — and a plan comment whose history vanishes on every
revise is exactly the pressure that produced an inline history layer inside the body, growing it
until GitHub refused the write (#38). With this op the edit history IS the supersession record.
``gh`` exposes no edit-by-id command, so the executor is ``gh api --method PATCH
repos/<repo>/issues/comments/<id> -F body=@<path>``; ``-F key=@file`` reads the value from the file,
so the body still crosses both the prompt and the argv boundary as a PATH (the #626/#627 discipline
is unchanged). Two properties the delete-and-repost path cannot offer: the comment URL is STABLE
(no issue-body pointer refresh), and a single ``PATCH`` is atomic — post-then-delete makes its
zero-or-two-marker window *safe*, this has no window at all. It is not a superset, though:
``comment --delete-marker-id`` stays, because only delete-and-repost can collapse a duplicated
marker, and ``edit-comment`` needs an id it does not have when no marker exists yet. It also cannot
rescue an over-cap body — REST enforces the same limit as GraphQL, so ``_verify_body_file`` gates it
identically. The id is the numeric REST comment id (a GraphQL node id is rejected up front, same
rationale as ``--delete-marker-id``), and the endpoint is the issue-comment one, which serves PR
conversation comments too but NOT PR review comments.

``create --blocked-by/--blocking`` and ``link`` set GitHub's NATIVE issue dependencies (gh >= 2.95
+ the repo feature enabled). They are capability-gated by ATTEMPTING the real write and classifying
its stderr (see ``_is_deps_error``) — not a ``--help`` probe, which can't tell a repo with the
feature disabled from one that has it, nor a broken gh from an unsupported one. On a
deps-capability failure the dependency is dropped with a ``DEPS_UNSUPPORTED`` notice (create
retries without it — a failed create is atomic, so no double-file) and the caller links via prose
(``Blocked by #N`` in the body) as the always-available fallback. Any OTHER failure surfaces via
the pipelib runner's ``AUTH_REQUIRED`` classification or a hard failure (non-zero exit, no
envelope). ``link`` changes relationships only; it writes no body, so it has no empty-body gate.

``create --parent`` sets GitHub's NATIVE parent/sub-issue relation, so an epic's stories become
real sub-issues — the epic's sub-issue panel and its ``subIssuesSummary`` progress rollup (and the
Sub-issues progress field on a GitHub Project) are then driven by issue state itself, rather than by
a hand-maintained markdown checklist that can't self-tick. It is capability-gated the same
attempt-and-classify way (see ``_is_subissues_error``), reporting ``SUBISSUES_UNSUPPORTED`` when
dropped. Dependencies and hierarchy are INDEPENDENT relations with independent gates, so a create
carrying both descends the ``_create_flag_ladder`` rungs and names exactly which one degraded — a
reader that sees ``SUBISSUES_UNSUPPORTED`` falls back to the legacy ``## Stories`` checklist
(``skills/_shared/epic-story-hierarchy.md``), which is a different fallback from prose dependency
linking.

``add-parent`` (added #16, additive-only) is the one **after-the-fact** parenting op: it adopts an
issue that is ALREADY FILED as a parent's sub-issue, which ``create --parent`` cannot do (that flag
only sets the relation in the same round-trip that files a new issue). Its caller is the slicer at
epic altitude, drawing an epic around stories authored upstream. It is **bodyless** (relationship
edit only — mirrors ``link``/``edit-labels``, not ``comment``) and takes **one child per call**, so
sequential calls in the operator-approved order give the epic's sub-issue panel that order for free
(sub-issues append). It is deliberately a SEPARATE subcommand rather than a flag on ``link``:
dependencies and hierarchy are independent relations with independent capability gates, and both a
rejected ``--parent`` and a rejected ``--add-blocked-by`` surface as ``unknown flag`` — carrying them
in one ``gh issue edit`` would make the capability-miss classification ambiguous about which relation
actually degraded, exactly what ``_create_flag_ladder`` descends a rung-per-relation to avoid. Same
attempt-and-classify gate as ``create --parent`` (``_is_subissues_error``; never a ``--help``/version
probe — ``gh issue edit --parent`` postdates the >= 2.40 floor this plugin declares): on a capability
miss the relation is dropped as a no-op success + ``SUBISSUES_UNSUPPORTED`` notice (``changed:
false``), mirroring ``link``'s deps degradation, because there is no without-the-relation retry to
make — the relation IS the whole edit. Any other failure surfaces hard: an integrity, quota or
permission error (``Sub-issue is already parented``, ``maximum number of sub-issues``, a 403 arriving
as ``Resource not accessible … (addSubIssue)``) must reach the caller, never be swallowed as a benign
notice. That property is NOT free from ``_SUBISSUES_ERROR_PATTERN`` alone — it matches the bare noun
``sub-issue``, which every one of those messages contains — so it is enforced by the
``_SUBISSUES_INTEGRITY_PATTERN`` deny list checked ahead of it. ``add-parent`` is what made the deny
list necessary: ``already parented`` is unreachable through ``create --parent``, since a
freshly-created issue has no parent to conflict with.

``--attach F[#alt]`` (repeatable, on ``create``/``create-pr``/``edit-body``/``edit-pr-body`` and on
``comment`` for the issue and pr targets) uploads an image or video with the write — gh >= 2.99's
``--attach``, which is how a PR that changes what a user sees carries screenshots for its reviewer.
gh rewrites each body reference that names the file to a repo-scoped ``user-attachments`` URL and
appends any attachment the body does not reference (``#alt`` is its alt text; gh's syntax, forwarded
verbatim). The match is the **exact string** passed as ``F`` (checked live: ``![a](/abs/shot.png)``
is rewritten for ``--attach /abs/shot.png``; ``./shot.png`` and ``shot.png`` are not, and get
appended instead), so a caller references each file by the same absolute path it attaches — and
the path must be absolute, refused otherwise (a relative one would resolve against ``--cwd`` for gh
but against this script's cwd for the existence check).
``gh pr review`` has no ``--attach``, so ``comment pr-review`` refuses it as a usage error. Every
other refusal is up front too, before any write: a missing or empty file, a type gh rejects, more
than gh's 50 per command. gh would refuse those itself without writing (checked live), but
refusing here keeps the failure a usage error, not a hard one. Four further rules:

- **Capability gate.** The plugin's floor is gh >= 2.40, so an older gh answers
  ``unknown flag: --attach``. That write is atomic (nothing filed), and unlike the native relations
  it is NOT retried without the flag: the staged body still references the local files, and
  posting it would ship dangling image links. The script exits 1 with stderr opening
  ``ATTACH_UNSUPPORTED:``; the caller restages the body without the references and retries without
  ``--attach``. ``_ATTACH_ERROR_PATTERN`` is its own narrow pattern, checked before the deps and
  sub-issue classifiers — ``_DEPS_ERROR_PATTERN``'s bare ``unknown flag`` would otherwise read an
  attach miss as a deps miss and descend ``create``'s ladder.
- **Partial upload.** When some attachments fail, gh still writes with the ones that succeeded and
  prints the URL, but exits non-zero. A non-zero exit whose stdout is a GitHub issue/PR/comment URL
  (``_GITHUB_URL_PATTERN``; any other stdout is not proof of a write) is therefore a landed write: ``ok`` with an ``ATTACH_PARTIAL`` notice and gh's stderr as ``attach_stderr``. Treating it
  as a hard failure would make the caller re-run and double-file.
- **Size headroom.** A rewritten reference is longer than the local path it replaces, and an
  appended attachment adds a line, so the size gate counts ``_ATTACH_CHAR_ALLOWANCE`` characters per
  attachment on top of the staged body: a body that fits as staged must still fit as posted.
- **Receipt and read-back.** ``body_bytes``/``body_sha256`` still describe the STAGED file, which no
  longer equals GitHub's copy once gh rewrites references; ``attachments`` lists what was passed. A
  body op (not ``comment``) then reads the posted body back and writes it beside the staged file as
  ``<stem>.posted<suffix>``, reported as ``posted_body_path``. That is not a verification re-read
  (§12 still holds: a zero exit with a URL is authoritative) — it derives the one thing the caller
  cannot: the body with GitHub's URLs in it. Without it, a caller that restages from its own local
  copy (the resolver rewrites a PR body several times a run) would put the dangling local paths back
  on its next write. A failed read-back is an ``ATTACH_READBACK_UNAVAILABLE`` notice; the write
  already landed. Comments skip it, since nothing restages a comment.

Exit codes (architecture.md §3 — the only two forms this script uses):
    0   envelope present on stdout; consult ``status`` (``ok`` or ``needs_decision`` — both are
        exit 0; ``needs_decision`` is not an error).
    2   usage error (malformed invocation) — no envelope; argparse's own ``error()`` path.

A hard `gh` failure that is not itself classified into a decision code (not auth, not a deps
capability miss) propagates as this script's own non-zero, non-0/2 exit with `gh`'s stderr
forwarded verbatim and no envelope guaranteed — architecture.md §3's "any other non-zero: hard
failure; stderr carries the faithful error; no envelope is guaranteed."
"""

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib import process, trust  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib.decisions import (  # noqa: E402
    ATTACH_PARTIAL,
    ATTACH_READBACK_UNAVAILABLE,
    AUTH_REQUIRED,
    BODY_TOO_LONG,
    DEPS_UNSUPPORTED,
    EMPTY_BODY_FILE,
    SUBISSUES_UNSUPPORTED,
    needs_decision,
)
from pipelib.envelope import EXIT_OK, emit_needs_decision, emit_ok  # noqa: E402
from pipelib.hashing import sha256_hex_file  # noqa: E402
from pipelib.limits import BODY_CHAR_LIMIT  # noqa: E402
import review_threads  # noqa: E402  (the marker and the stamp grammar, skills/_shared/review-threads.md)

# Precise capability signature for a deps-unsupported `gh` failure — ported verbatim from v1's
# is_deps_error (gh-persist.sh: `grep -qiE 'unknown (flag|json field)|issue[ -]?dependenc'`). The
# installed gh predates the --blocked-by/--blocking/--add-* flags, OR the repo hasn't enabled the
# issue-dependencies feature. A real `re` pattern (not a substring-marker approximation) so the
# separator-optional `issue[ -]?dependenc` clause (space, hyphen, OR no separator at all) is
# matched exactly the same as the bash regex would, not just the two spellings that happen to look
# likely. Match ONLY these two forms — err toward SURFACING: a real error we don't match fails
# loudly (safe, self-explanatory), whereas a non-deps error we wrongly match would be silently
# swallowed as a benign "use prose" (dangerous — a caller never learns the write failed).
# Deliberately does NOT match bare "blocking"/"dependency"/"blocked-by" — those appear in
# unrelated relationship-integrity errors (e.g. "#5 is already blocking this issue") that MUST
# surface.
_DEPS_ERROR_PATTERN = re.compile(r"unknown (flag|json field)|issue[ -]?dependenc", re.IGNORECASE)


def _is_deps_error(stderr_text):
    return bool(_DEPS_ERROR_PATTERN.search(stderr_text or ""))


# Capability signature for a sub-issue-unsupported `gh` failure — the installed gh predates
# `--parent` (added well after the >= 2.40 floor this plugin declares), or the host doesn't serve
# the parent/sub-issue relation. Same "match only these forms, err toward SURFACING" discipline as
# _DEPS_ERROR_PATTERN: a real error we don't match fails loudly; a non-capability error we wrongly
# matched would be silently swallowed as a benign "filed without the parent".
# Deliberately does NOT match a bare "parent": that word appears in ordinary relationship-integrity
# and validation errors ("the parent issue is closed", "cannot add a parent to itself") which MUST
# surface — matching it would file the story unparented while reporting a benign capability notice,
# so the caller never learns its real parenting attempt failed. An old gh rejects the flag itself
# with "unknown flag", which the first alternative already covers.
_SUBISSUES_ERROR_PATTERN = re.compile(r"unknown (flag|json field)|sub[ -]?issue", re.IGNORECASE)

# The deny list, checked FIRST. `_SUBISSUES_ERROR_PATTERN` above matches the bare noun `sub-issue`,
# which `_DEPS_ERROR_PATTERN` deliberately refuses to do for its own noun — and every non-capability
# way GitHub can reject a parenting attempt NAMES the feature: "Sub-issue is already parented",
# "maximum number of sub-issues", "sub-issues cannot be nested", "A sub-issue must belong to the same
# repository". Worse, `gh` appends the failing GraphQL path to API errors, so an ordinary 403 arrives
# as "Resource not accessible by personal access token (addSubIssue)" and the bare-noun alternative
# matches `SubIssue` inside `addSubIssue`. Without this list every one of those is swallowed as a
# benign "the host doesn't serve the relation", which is the exact inversion of the
# err-toward-SURFACING discipline both patterns claim: an integrity, quota or permission failure MUST
# reach the caller. Ordering matters — a message that trips this list is never a capability miss,
# even when it also contains "unknown flag".
_SUBISSUES_INTEGRITY_PATTERN = re.compile(
    r"already (a )?(sub[ -]?issue|parented)|maximum number|exceed|nest|same repository"
    r"|its own parent|not accessible|permission|forbidden|unauthor|rate limit",
    re.IGNORECASE,
)


def _is_subissues_error(stderr_text):
    text = stderr_text or ""
    if _SUBISSUES_INTEGRITY_PATTERN.search(text):
        return False
    return bool(_SUBISSUES_ERROR_PATTERN.search(text))


# Capability signature for a gh that predates `--attach` (added in gh 2.99, well after the >= 2.40
# floor). Names the flag itself, unlike the two patterns above: gh's parser reports the exact flag it
# rejected (`unknown flag: --attach`), and a bare `unknown flag` would claim a deps or parent miss as
# an attach one. Checked BEFORE those two, because their bare alternative matches this message too.
_ATTACH_ERROR_PATTERN = re.compile(r"unknown flag:? --attach\b", re.IGNORECASE)

# gh's own per-command limit and accepted types (`gh pr create --help`; the type list is the one gh
# prints when it rejects a file). Refused here as usage errors so a bad attachment never reaches gh.
_ATTACH_MAX = 50
_ATTACH_TYPES = frozenset({"png", "jpg", "jpeg", "gif", "webp", "svg", "mp4", "mov", "webm"})

# Characters the size gate reserves per attachment: a rewritten reference
# (`https://github.com/user-attachments/assets/<uuid>`, ~85 characters) is longer than the local
# path it replaces, and an unreferenced attachment is appended as a new `![alt](url)` line.
_ATTACH_CHAR_ALLOWANCE = 200

# The one thing gh prints on stdout for a write that landed: the issue, PR or comment URL.
_GITHUB_URL_PATTERN = re.compile(r"https://[^\s]+/(issues|pull)/\d+(#issuecomment-\d+)?")

_ATTACH_UNSUPPORTED_MESSAGE = (
    "ATTACH_UNSUPPORTED: gh >= 2.99 is required for --attach; nothing was written. Restage the "
    "body without the attachment references and retry without --attach.\n"
)


def _is_attach_error(stderr_text):
    return bool(_ATTACH_ERROR_PATTERN.search(stderr_text or ""))


def _check_attachments(attach, parser):
    """Refuse a bad ``--attach`` list as a usage error, before the body gate and before any gh call.
    ``#alt`` is split off only to find the file; the value is forwarded to gh unchanged.

    Paths must be absolute. gh resolves a relative one against ``--cwd`` while this check would
    resolve it against the script's own cwd, so the two could disagree; and gh matches the body's
    reference by exact string, which only an absolute path keeps stable across both.
    """
    if len(attach) > _ATTACH_MAX:
        parser.error("--attach takes at most %d files per write (gh's limit), got %d"
                     % (_ATTACH_MAX, len(attach)))
    for value in attach:
        path = Path(value.split("#", 1)[0])
        if not path.is_absolute():
            parser.error("--attach needs an absolute path (the body must reference the same "
                         "string), got: %s" % path)
        if not path.is_file():
            # gh splits FILE#ALT at the first `#` too, so a path containing one can't be attached
            # at all; say that rather than report a file the caller can see as missing.
            hashes = [i for i, ch in enumerate(value) if ch == "#"][1:]
            if any(Path(value[:i]).is_file() for i in hashes) or Path(value).is_file():
                parser.error("--attach path contains '#', which FILE#ALT reads as the start of the "
                             "alt text; rename the file: %s" % value)
            parser.error("--attach file not found: %s" % path)
        if path.stat().st_size == 0:
            parser.error("--attach file is empty: %s" % path)
        if path.suffix.lower().lstrip(".") not in _ATTACH_TYPES:
            parser.error("--attach file type is not one gh uploads (%s): %s"
                         % (", ".join(sorted(_ATTACH_TYPES)), path))


def _attach_flags(attach):
    flags = []
    for value in attach:
        flags += ["--attach", value]
    return flags


def _landed_write(result, attach):
    """``(url, notices, extra_payload)`` when the write landed, else ``None``. A zero exit always
    landed. With ``--attach``, so did a non-zero exit that still printed a URL: gh writes with the
    attachments that uploaded and exits non-zero for the rest (see the module docstring)."""
    url = result.stdout.strip()
    if result.returncode == 0:
        return url, [], {}
    # Only a URL counts: any other stdout on a failed write (a warning, a stray line) is not proof
    # that anything was written, and reading it as a landed write would swallow a real failure.
    if attach and _GITHUB_URL_PATTERN.fullmatch(url):
        return url, [ATTACH_PARTIAL], {"attach_stderr": result.stderr}
    return None


def _fail_write(result, attach):
    if attach and _is_attach_error(result.stderr):
        sys.stderr.write(_ATTACH_UNSUPPORTED_MESSAGE)
    sys.stderr.write(result.stderr)
    return 1


def _read_back_posted_body(noun, target, repo, body_path, cwd):
    """Write the body GitHub now holds beside the staged file; ``(payload, notices)``.

    Not a verification re-read (§12): the write is already authoritative. This derives the body with
    gh's rewritten attachment URLs, which only GitHub has, so the caller's next restage starts from
    it instead of from local paths that no longer resolve.
    """
    result = process.run(
        ["gh", noun, "view", target, "--repo", repo, "--json", "body", "--jq", ".body"], cwd=cwd
    )
    if result.returncode != 0:
        return {"posted_body_path": None}, [ATTACH_READBACK_UNAVAILABLE]
    staged = Path(body_path)
    posted = staged.with_name("%s.posted%s" % (staged.stem, staged.suffix))
    # `--jq` prints the string plus one newline of its own; the body is otherwise byte-faithful.
    posted.write_text(result.stdout[:-1] if result.stdout.endswith("\n") else result.stdout,
                      encoding="utf-8")
    return {"posted_body_path": str(posted)}, []


def _attach_payload(attach):
    # Present only when attachments were passed, so a write without them reports exactly what it
    # did before `--attach` existed.
    return {"attachments": list(attach)} if attach else {}


def _finish_attach_write(payload, notices, args, noun, target):
    """Body ops only: report the attachments and read the posted body back (see the docstring)."""
    if not args.attach:
        return
    payload.update(_attach_payload(args.attach))
    readback, readback_notices = _read_back_posted_body(
        noun, target, args.repo, args.body_path, args.cwd
    )
    payload.update(readback)
    notices.extend(readback_notices)


def _create_flag_ladder(dep_flags, parent_flags):
    """The ordered ``(extra_flags, notices_for_what_was_dropped)`` attempts a `create` walks, most
    complete first.

    Native dependencies (``--blocked-by``/``--blocking``) and the native parent/sub-issue relation
    (``--parent``) gate INDEPENDENTLY — different GitHub relations, different gh versions — and
    gh's "unknown flag" phrasing doesn't reliably say which one it rejected when a create carries
    both. Rather than guess from the stderr text, descend a ladder: each rung drops one relation
    and reports exactly what it dropped. Every failed `create` is atomic (nothing is filed), so
    descending can't double-file — the same property v1's single-rung deps retry relied on.

    With no extras the ladder is a single bare attempt with no notices, so the common case stays
    one `gh` call.
    """
    ladder = [(dep_flags + parent_flags, [])]
    if dep_flags and parent_flags:
        # Can't tell which relation gh rejected — try each alone before giving up on both.
        ladder.append((dep_flags, [SUBISSUES_UNSUPPORTED]))
        ladder.append((parent_flags, [DEPS_UNSUPPORTED]))
    if dep_flags or parent_flags:
        dropped = ([DEPS_UNSUPPORTED] if dep_flags else []) + (
            [SUBISSUES_UNSUPPORTED] if parent_flags else []
        )
        ladder.append(([], dropped))
    return ladder


def _quote_cmd(argv):
    """Shell-quote each arg for a faithfully-reproducible --dry-run preview string. shlex.quote
    is the POSIX-portable stdlib equivalent of v1's `printf '%q'` (bash-specific) — this must run
    identically on macOS and Linux (architecture.md §1).
    """
    return " ".join(shlex.quote(a) for a in argv)


class _OrderedLinkFlagAction(argparse.Action):
    """Records every --add/remove-blocked-by/blocking occurrence, in the exact interleaved order
    the caller passed them on the command line, into a single shared ``args.link_edit_flags``
    list of ``(flag, value)`` pairs.

    v1 (gh-persist.sh's cmd_link) accumulates all four relationship flags into ONE bash array via
    a single ``while`` loop over the remaining args, so a caller's exact interleaving (e.g.
    ``--add-blocked-by 9 --remove-blocking 3 --add-blocking 7``) is preserved verbatim into the
    ``gh issue edit`` argv. argparse's ordinary per-destination ``action="append"`` cannot express
    this — four separate list destinations lose the *relative* order across different flag names,
    even though each destination's own internal order is fine. This action is the fix: every one
    of the four flags in the parser (below) points at this SAME action/dest, so occurrences land
    in one list in true CLI order regardless of which of the four flag names each one used.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        if not hasattr(namespace, self.dest) or getattr(namespace, self.dest) is None:
            setattr(namespace, self.dest, [])
        getattr(namespace, self.dest).append((option_string, values))


def _verify_body_file(body_path, attachments=0):
    """The size gate (architecture.md §12, #626/#627 empty-body race): the caller stages the
    verbatim body to ``body_path`` and passes only the path — never the bytes — across the prompt
    boundary. This check runs *before* any `gh` call, so the failure mode that bit #626/#627 (an
    in-agent Write-vs-Bash race landing an empty body on `gh issue create --body-file`) is
    unrepresentable here: there is no intermediate file this script populates, only one the caller
    already wrote.

    Returns ``None`` when the file exists, is non-empty, and fits :data:`BODY_CHAR_LIMIT`. Returns
    the ``needs_decision`` payload (``EMPTY_BODY_FILE`` or ``BODY_TOO_LONG``) otherwise — the caller
    emits it and returns EXIT_OK without touching `gh`. (v1 exited 2 with a bare stderr line here;
    §3 makes EMPTY_BODY_FILE a proper decision code, so v2 emits it as a normal needs_decision
    envelope at exit 0 instead.)

    The upper bound is the same gate at the other end, and it is measured in **characters**, not
    bytes — that is the unit GitHub enforces. The write receipts next door report ``body_bytes``,
    which for non-ASCII text runs *ahead* of the character count, so gating on bytes would refuse
    a legal multibyte body (a false refusal it can never detect) while a character gate can only
    ever be right. Do not "simplify" this to ``stat().st_size``: reading the file to decode it is
    the whole point, and both counts are reported so a caller can see the difference.
    """
    path = Path(body_path)
    if not path.is_file():
        return needs_decision(
            EMPTY_BODY_FILE,
            summary="staged body file not found: %s" % body_path,
            context={"body_path": str(body_path), "reason": "not found"},
            options=["re-stage the verbatim body to %s and retry" % body_path],
        )
    if path.stat().st_size == 0:
        return needs_decision(
            EMPTY_BODY_FILE,
            summary="staged body file is empty: %s" % body_path,
            context={"body_path": str(body_path), "reason": "zero bytes"},
            options=["re-stage the verbatim body to %s and retry" % body_path],
        )
    data = path.read_bytes()
    # `errors="replace"` rather than a raise: a body a skill staged is always UTF-8, and a decode
    # crash here would surface as a traceback with no envelope — strictly worse than a decision.
    # The cost is that an invalid byte counts as one U+FFFD, so a non-UTF-8 file is measured
    # approximately; that is acceptable because such a file is already malformed for `gh`.
    # Each attachment reserves `_ATTACH_CHAR_ALLOWANCE` on top: gh lengthens the body as it rewrites
    # references and appends unreferenced files, so what is gated is the body as POSTED.
    allowance = attachments * _ATTACH_CHAR_ALLOWANCE
    chars = len(data.decode("utf-8", errors="replace"))
    overage = chars + allowance - BODY_CHAR_LIMIT
    if overage > 0:
        return needs_decision(
            BODY_TOO_LONG,
            summary="staged body file is %d characters%s; the limit is %d (over by %d): %s"
            % (chars, " plus %d reserved for attachments" % allowance if allowance else "",
               BODY_CHAR_LIMIT, overage, body_path),
            context={
                "body_path": str(body_path),
                "reason": "over character limit",
                "actual_chars": chars,
                "actual_bytes": len(data),
                "limit_chars": BODY_CHAR_LIMIT,
                "overage_chars": overage,
                "attachment_allowance_chars": allowance,
            },
            options=[
                "shorten the staged body to %d characters or fewer and retry" % BODY_CHAR_LIMIT,
                "split the content so the write that carries this marker fits",
            ],
        )
    return None


def _write_receipt_fields(body_path):
    """Byte-fidelity fields for a body-bearing write (architecture.md §3/§12): ``body_bytes`` +
    ``body_sha256`` computed directly from the staged file on disk — the same bytes `gh` was
    handed via ``--body-file`` — so a caller can verify byte-for-byte without re-reading GitHub's
    copy back (§12: "a zero exit with a URL is authoritative and is never re-read to verify").
    """
    data = Path(body_path).read_bytes()
    return {"body_bytes": len(data), "body_sha256": sha256_hex_file(body_path)}


def _auth_decision(result):
    return needs_decision(
        AUTH_REQUIRED,
        summary="gh authentication required",
        context={"stderr": result.stderr, "returncode": result.returncode},
        options=["run: gh auth login"],
    )


# ---- create ----


def _cmd_create(args, parser):
    _check_attachments(args.attach, parser)
    gate = _verify_body_file(args.body_path, len(args.attach))
    if gate is not None:
        emit_needs_decision(gate)
        return EXIT_OK

    cmd = ["gh", "issue", "create", "--repo", args.repo, "--title", args.title,
           "--body-file", args.body_path]
    for label in args.label or []:
        cmd += ["--label", label]
    # On every rung: the ladder drops native relations, never the attachments (see the docstring).
    cmd += _attach_flags(args.attach)

    dep_flags = []
    if args.blocked_by:
        dep_flags += ["--blocked-by", args.blocked_by]
    if args.blocking:
        dep_flags += ["--blocking", args.blocking]

    # Native parent/sub-issue relation: files the new issue as a sub-issue of `--parent` in the SAME
    # round-trip, so an epic batch establishes the hierarchy without a follow-up write. addSubIssue
    # appends, so filing in dependency order gives the epic's sub-issue panel that order for free.
    parent_flags = ["--parent", str(args.parent)] if args.parent else []

    if args.dry_run:
        preview = cmd + dep_flags + parent_flags
        # v1's emit_envelope computes body_bytes/body_sha256 unconditionally, dry-run or not (it
        # hashes the caller-staged file directly — no `gh` round-trip needed for that, since
        # there's nothing to verify a write against yet). Preserved here: a dry-run preview still
        # reports what WOULD be posted, byte-for-byte, not just the command line.
        payload = {"op": "create", "dry_run": True, "would_run": _quote_cmd(preview)}
        payload.update(_write_receipt_fields(args.body_path))
        payload.update(_attach_payload(args.attach))
        emit_ok(payload=payload)
        return EXIT_OK

    # Attempt WITH every native relation first; on a capability failure (old gh, or the host
    # doesn't serve that relation) descend the ladder, dropping one relation per rung and
    # signalling what was dropped so the caller falls back — prose linking for dependencies, the
    # legacy `## Stories` checklist for hierarchy. A failed create is atomic (nothing is filed), so
    # descending can't double-file — v1's cmd_create relied on the same property for its single
    # deps retry. If a rung fails for a NEW, non-capability reason, surface that instead: never
    # claim "created without them" ahead of a create that didn't actually happen.
    notices = []
    url = None
    extra_payload = {}
    ladder = _create_flag_ladder(dep_flags, parent_flags)
    for index, (extra_flags, dropped_notices) in enumerate(ladder):
        attempt = process.run(cmd + extra_flags, cwd=args.cwd)
        if attempt.auth_required:
            emit_needs_decision(_auth_decision(attempt))
            return EXIT_OK
        # A landed write stops the descent — including a partial upload, whose non-zero exit would
        # otherwise read as a capability miss and file the issue a second time on the next rung.
        landed = _landed_write(attempt, args.attach)
        if landed is not None:
            url, write_notices, extra_payload = landed
            notices = dropped_notices + write_notices
            break
        # An attach miss is checked first: the deps/sub-issue patterns' bare `unknown flag` matches
        # it too, and dropping a relation can't cure it.
        attach_miss = bool(args.attach) and _is_attach_error(attempt.stderr)
        is_last_rung = index == len(ladder) - 1
        capability_miss = (dep_flags and _is_deps_error(attempt.stderr)) or (
            parent_flags and _is_subissues_error(attempt.stderr)
        )
        if attach_miss or is_last_rung or not capability_miss:
            return _fail_write(attempt, args.attach)

    payload = {"op": "create", "dry_run": False, "url": url}
    payload.update(_write_receipt_fields(args.body_path))
    payload.update(extra_payload)
    _finish_attach_write(payload, notices, args, "issue", url)
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---- create-pr ----


def _cmd_create_pr(args, parser):
    _check_attachments(args.attach, parser)
    gate = _verify_body_file(args.body_path, len(args.attach))
    if gate is not None:
        emit_needs_decision(gate)
        return EXIT_OK

    cmd = ["gh", "pr", "create", "--repo", args.repo, "--title", args.title,
           "--body-file", args.body_path, "--base", args.base, "--head", args.head]
    if args.draft:
        cmd.append("--draft")
    cmd += _attach_flags(args.attach)

    if args.dry_run:
        # Same convention as every other body-bearing op: body_bytes/body_sha256 are hashed
        # straight from the staged file, so a dry-run preview reports the write receipt without a
        # live `gh` round-trip.
        payload = {"op": "create-pr", "dry_run": True, "would_run": _quote_cmd(cmd)}
        payload.update(_write_receipt_fields(args.body_path))
        payload.update(_attach_payload(args.attach))
        emit_ok(payload=payload)
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    landed = _landed_write(result, args.attach)
    if landed is None:
        return _fail_write(result, args.attach)
    url, notices, extra_payload = landed

    payload = {"op": "create-pr", "dry_run": False, "url": url}
    payload.update(_write_receipt_fields(args.body_path))
    payload.update(extra_payload)
    _finish_attach_write(payload, notices, args, "pr", url)
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---- edit-body ----


def _cmd_edit_body(args, parser):
    _check_attachments(args.attach, parser)
    gate = _verify_body_file(args.body_path, len(args.attach))
    if gate is not None:
        emit_needs_decision(gate)
        return EXIT_OK

    cmd = ["gh", "issue", "edit", args.issue, "--repo", args.repo, "--body-file", args.body_path]
    cmd += _attach_flags(args.attach)

    if args.dry_run:
        # See _cmd_create's matching comment: v1's emit_envelope reports body_bytes/body_sha256
        # unconditionally, dry-run or not.
        payload = {"op": "edit-body", "dry_run": True, "would_run": _quote_cmd(cmd)}
        payload.update(_write_receipt_fields(args.body_path))
        payload.update(_attach_payload(args.attach))
        emit_ok(payload=payload)
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    landed = _landed_write(result, args.attach)
    if landed is None:
        return _fail_write(result, args.attach)
    url, notices, extra_payload = landed

    payload = {"op": "edit-body", "dry_run": False, "url": url}
    payload.update(_write_receipt_fields(args.body_path))
    payload.update(extra_payload)
    _finish_attach_write(payload, notices, args, "issue", url or args.issue)
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---- edit-pr-body ----


def _cmd_edit_pr_body(args, parser):
    _check_attachments(args.attach, parser)
    gate = _verify_body_file(args.body_path, len(args.attach))
    if gate is not None:
        emit_needs_decision(gate)
        return EXIT_OK

    cmd = ["gh", "pr", "edit", args.pr, "--repo", args.repo, "--body-file", args.body_path]
    cmd += _attach_flags(args.attach)

    if args.dry_run:
        payload = {"op": "edit-pr-body", "dry_run": True, "would_run": _quote_cmd(cmd)}
        payload.update(_write_receipt_fields(args.body_path))
        payload.update(_attach_payload(args.attach))
        emit_ok(payload=payload)
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    landed = _landed_write(result, args.attach)
    if landed is None:
        return _fail_write(result, args.attach)
    url, notices, extra_payload = landed

    payload = {"op": "edit-pr-body", "dry_run": False, "url": url}
    payload.update(_write_receipt_fields(args.body_path))
    payload.update(extra_payload)
    _finish_attach_write(payload, notices, args, "pr", url or args.pr)
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---- edit-title / edit-pr-title ----


def _cmd_edit_title(args):
    # Bodyless — a title is not a body, so no empty-body gate (mirrors edit-labels/link, not
    # edit-body: see the module docstring's edit-title paragraph). --title is required at the
    # parser, so a no-op invocation is a usage error rather than a silent success.
    cmd = ["gh", "issue", "edit", args.issue, "--repo", args.repo, "--title", args.title]

    if args.dry_run:
        emit_ok(payload={
            "op": "edit-title", "issue": args.issue, "dry_run": True,
            "title": args.title, "would_run": _quote_cmd(cmd),
        })
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1

    emit_ok(payload={
        "op": "edit-title", "issue": args.issue, "dry_run": False,
        "title": args.title, "url": result.stdout.strip(),
    })
    return EXIT_OK


def _cmd_edit_pr_title(args):
    # Mirrors _cmd_edit_title with the `pr` noun, for edit-pr-body's reason: `gh issue edit`
    # rejects a PR number, so a mis-targeted call must fail loudly instead of mis-writing.
    cmd = ["gh", "pr", "edit", args.pr, "--repo", args.repo, "--title", args.title]

    if args.dry_run:
        emit_ok(payload={
            "op": "edit-pr-title", "pr": args.pr, "dry_run": True,
            "title": args.title, "would_run": _quote_cmd(cmd),
        })
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1

    emit_ok(payload={
        "op": "edit-pr-title", "pr": args.pr, "dry_run": False,
        "title": args.title, "url": result.stdout.strip(),
    })
    return EXIT_OK


# ---- edit-labels ----


def _cmd_edit_labels(args, parser):
    # Bodyless relationship edit — no empty-body gate (mirrors close/reopen, not comment/edit-body:
    # see the module docstring's edit-labels paragraph). At least one of --add/--remove is required,
    # the same "don't silently no-op" usage-error rule `link` applies to its own relationship flags.
    if not args.add and not args.remove:
        parser.error("edit-labels requires at least one --add/--remove flag")

    cmd = ["gh", "issue", "edit", args.issue, "--repo", args.repo]
    for label in args.add or []:
        cmd += ["--add-label", label]
    for label in args.remove or []:
        cmd += ["--remove-label", label]

    if args.dry_run:
        emit_ok(payload={
            "op": "edit-labels", "issue": args.issue, "dry_run": True,
            "added": list(args.add or []), "removed": list(args.remove or []),
            "would_run": _quote_cmd(cmd),
        })
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1

    emit_ok(payload={
        "op": "edit-labels", "issue": args.issue, "dry_run": False,
        "added": list(args.add or []), "removed": list(args.remove or []),
        "url": result.stdout.strip(),
    })
    return EXIT_OK


# ---- link ----


def _cmd_link(args, parser):
    # args.link_edit_flags is a list of (flag, value) pairs in the exact interleaved order the
    # caller passed them (see _OrderedLinkFlagAction) — flatten to a plain argv tail, preserving
    # that order exactly as v1's single bash while-loop did.
    edit_flags = []
    for flag, value in args.link_edit_flags or []:
        edit_flags += [flag, value]

    # A link with no relationship flags is a caller error (a dropped/empty flag list upstream) —
    # fail loudly (usage error) rather than return a silent no-op success that looks like the
    # relationship was set. Ported verbatim from v1's cmd_link.
    if not edit_flags:
        parser.error("link requires at least one --add/remove-blocked-by/blocking flag")

    cmd = ["gh", "issue", "edit", args.issue, "--repo", args.repo] + edit_flags

    if args.dry_run:
        emit_ok(payload={
            "op": "link",
            "issue": args.issue,
            "dry_run": True,
            "changed": True,
            "deps_unsupported": False,
            "would_run": _quote_cmd(cmd),
        })
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode == 0:
        emit_ok(payload={
            "op": "link",
            "issue": args.issue,
            "dry_run": False,
            "changed": True,
            "deps_unsupported": False,
            "url": result.stdout.strip(),
        })
        return EXIT_OK

    if _is_deps_error(result.stderr):
        # `link` writes no body, so a rejected attempt changes nothing — nothing to roll back
        # (unlike create, there is no without-deps retry: relationship-only, so "without deps"
        # would be an empty edit). Reported as a no-op success + notice, mirroring v1's cmd_link.
        emit_ok(payload={
            "op": "link",
            "issue": args.issue,
            "dry_run": False,
            "changed": False,
            "deps_unsupported": True,
        }, notices=[DEPS_UNSUPPORTED])
        return EXIT_OK

    sys.stderr.write(result.stderr)
    return 1


# ---- add-parent ----


def _issue_ref_key(value):
    """A comparison key for the two interchangeable issue-reference spellings `--parent` accepts (a
    bare number or a URL), so `42`, `#42`, ` 42 ` and `https://github.com/o/r/issues/42` compare
    equal. Used only by the self-parenting guard below; the value passed to `gh` stays verbatim,
    because gh resolves the reference itself and this script never parses it."""
    text = (value or "").strip().rstrip("/")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text.lstrip("#")


def _cmd_add_parent(args, parser):
    # Adopting an issue into a parent it is already a child of is a gh/GitHub-API no-op success, so
    # no "is it already parented?" pre-check happens here — same discipline as close/reopen/
    # edit-labels. Parenting an issue to ITSELF, though, is a caller error; catch it as a usage error
    # so the failure names the real problem. Compared on the normalized key rather than raw strings:
    # gh accepts a number and a URL interchangeably, so a raw compare would let `42` + `#42` (or a
    # URL for the same issue) through to gh, and the only backstop would be gh's own rejection text.
    if _issue_ref_key(args.child) == _issue_ref_key(args.parent):
        parser.error("child and parent must differ (cannot parent an issue to itself)")

    cmd = ["gh", "issue", "edit", args.child, "--repo", args.repo, "--parent", args.parent]

    if args.dry_run:
        # `changed`/`subissues_unsupported` are deliberately OMITTED here, not set to True: they are
        # outcome claims, and no write happened. `cut.md` documents that the caller reads `changed`,
        # so a rehearsal run asserting `changed: true` would record every adoption as applied. This
        # follows close/reopen/edit-body/comment (which omit their outcome flag on the dry-run path)
        # rather than `link`, whose `changed: True` predates that convention being consistent.
        emit_ok(payload={
            "op": "add-parent",
            "issue": args.child,
            "parent": args.parent,
            "dry_run": True,
            "would_run": _quote_cmd(cmd),
        })
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode == 0:
        emit_ok(payload={
            "op": "add-parent",
            "issue": args.child,
            "parent": args.parent,
            "dry_run": False,
            "changed": True,
            "subissues_unsupported": False,
            "url": result.stdout.strip(),
        })
        return EXIT_OK

    if _is_subissues_error(result.stderr):
        # Relationship-only, so a rejected attempt changed nothing — and unlike `create` there is no
        # lower rung to descend to: dropping the relation would leave an empty edit. Reported as a
        # no-op success + notice, mirroring `link`'s deps degradation. The caller (the slicer) keeps
        # the adoption out of the epic's panel and says so; it never retries.
        emit_ok(payload={
            "op": "add-parent",
            "issue": args.child,
            "parent": args.parent,
            "dry_run": False,
            "changed": False,
            "subissues_unsupported": True,
        }, notices=[SUBISSUES_UNSUPPORTED])
        return EXIT_OK

    sys.stderr.write(result.stderr)
    return 1


# ---- comment ----


def _cmd_comment(args, parser):
    if args.target in ("issue", "pr") and args.review_action:
        parser.error("--review-action is forbidden when target is '%s'" % args.target)
    if args.target == "pr-review" and not args.review_action:
        parser.error("--review-action is required when target is 'pr-review'")
    if args.target == "pr-review" and args.attach:
        parser.error("--attach is forbidden when target is 'pr-review' (gh pr review has no "
                     "--attach); attach in a target=pr comment instead")
    if args.delete_marker_id is not None and not args.delete_marker_id.isdigit():
        # The delete below is REST (`repos/{owner}/{repo}/issues/comments/{id}`), which takes the
        # NUMERIC comment id only — a GraphQL node id (`IC_kwDO…`, what gh_gather's normalized
        # thread carries as `id`) can never resolve there, so this is a guaranteed 404, not a
        # transient failure the best-effort WARN below is for (#34). Rejected UP FRONT, before the
        # post: a caller passing the wrong id space gets an actionable usage error with nothing
        # written, instead of a posted comment plus an undeleted stale duplicate. Marker ids come
        # from a gather's marker-comment lookup (`marker_comment_id`, read off the raw REST
        # objects), never from a thread comment's `id`.
        parser.error(
            "--delete-marker-id expects a numeric REST comment id, got %r — a GraphQL node id "
            "always 404s on the REST delete endpoint; pass the gather's marker_comment_id"
            % args.delete_marker_id
        )

    _check_attachments(args.attach, parser)
    gate = _verify_body_file(args.body_path, len(args.attach))
    if gate is not None:
        emit_needs_decision(gate)
        return EXIT_OK

    if args.target == "issue":
        cmd = ["gh", "issue", "comment", args.id, "--repo", args.repo, "--body-file", args.body_path]
    elif args.target == "pr":
        cmd = ["gh", "pr", "comment", args.id, "--repo", args.repo, "--body-file", args.body_path]
    else:  # pr-review
        cmd = ["gh", "pr", "review", args.id, "--repo", args.repo,
               "--%s" % args.review_action, "--body-file", args.body_path]
    cmd += _attach_flags(args.attach)

    if args.dry_run:
        # See _cmd_create's matching comment: v1's emit_envelope reports body_bytes/body_sha256
        # unconditionally, dry-run or not.
        payload = {"op": "comment", "dry_run": True, "would_run": _quote_cmd(cmd)}
        payload.update(_write_receipt_fields(args.body_path))
        payload.update(_attach_payload(args.attach))
        emit_ok(payload=payload)
        return EXIT_OK

    # POST the new comment first, THEN delete the old marker (post-then-delete). If the post
    # fails, we return before ever attempting the delete — the prior marker comment is left
    # intact, so a failed revise never destroys the existing decision/plan comment with no
    # replacement. This ordering is the invariant (architecture.md §12: "a crash must not lose the
    # marker") — ported verbatim from v1's cmd_comment; do not reorder these two calls.
    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    landed = _landed_write(result, args.attach)
    if landed is None:
        return _fail_write(result, args.attach)
    url, notices, extra_payload = landed

    if args.delete_marker_id:
        delete_cmd = ["gh", "api", "-X", "DELETE",
                      "repos/%s/issues/comments/%s" % (args.repo, args.delete_marker_id)]
        delete_result = process.run(delete_cmd, cwd=args.cwd)
        if delete_result.returncode != 0:
            # Best-effort: the new comment already posted (see above), so a delete failure here
            # never loses the record — the stale duplicate surfaces as marker_comment_count > 1 on
            # the next gather (recoverable), strictly safer than losing the marker. Surfaced as a
            # notice, not a decision or a hard failure — ported from v1's stderr WARN line.
            notices.append(
                "WARN: posted new comment but could not delete prior marker comment %s "
                "(a stale duplicate may remain)" % args.delete_marker_id
            )

    payload = {"op": "comment", "dry_run": False, "url": url}
    payload.update(_write_receipt_fields(args.body_path))
    payload.update(extra_payload)
    payload.update(_attach_payload(args.attach))
    emit_ok(payload=payload, notices=notices)
    return EXIT_OK


# ---- edit-comment ----


def _cmd_edit_comment(args, parser):
    if not args.comment_id.isdigit():
        # Same id-space rule as `comment --delete-marker-id`: this endpoint is REST, which takes the
        # NUMERIC comment id only. A GraphQL node id (`IC_kwDO…`, what a gather's normalized thread
        # carries as `id`) can never resolve here, so it is a guaranteed 404 — rejected up front as a
        # usage error, with nothing written. Marker ids come from a gather's marker-comment lookup
        # (`marker_comment_id`, read off the raw REST objects), never from a thread comment's `id`.
        parser.error(
            "comment_id expects a numeric REST comment id, got %r — a GraphQL node id always "
            "404s on the REST comment endpoint; pass the gather's marker_comment_id"
            % args.comment_id
        )

    gate = _verify_body_file(args.body_path)
    if gate is not None:
        emit_needs_decision(gate)
        return EXIT_OK

    # `-F body=@<path>` makes `gh` read the value from the file, so the body never crosses argv as
    # text (the #626/#627 staged-path discipline). --jq extracts the same html_url the `comment` op
    # returns, so both write paths report a comparable `url`.
    cmd = [
        "gh", "api", "--method", "PATCH",
        "repos/%s/issues/comments/%s" % (args.repo, args.comment_id),
        "-F", "body=@%s" % args.body_path,
        "--jq", ".html_url",
    ]

    if args.dry_run:
        payload = {"op": "edit-comment", "dry_run": True, "would_run": _quote_cmd(cmd)}
        payload.update(_write_receipt_fields(args.body_path))
        emit_ok(payload=payload)
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1

    # One atomic write — there is no second call to fail partway, so unlike the post-then-delete
    # path there is no window in which the issue carries zero or two markers.
    payload = {"op": "edit-comment", "dry_run": False, "url": result.stdout.strip()}
    payload.update(_write_receipt_fields(args.body_path))
    emit_ok(payload=payload)
    return EXIT_OK


# ---- review-post / review-reply (#145) ----
#
# skills/_shared/review-threads.md owns the format. Every body is stamped HERE, never by the caller:
# the marker and the stamp line are what make a comment the pipeline's, and the model writes only the
# text under them. Each stamped copy is written beside the caller's file as `<body>.stamped.md`, gated
# by `_verify_body_file` and hashed by `_write_receipt_fields` before the first `gh` call, and handed to
# `gh api graphql` as `-F body=@<stamped path>` (a GraphQL variable read from the file, so no body
# crosses argv as text). There is deliberately no op that resolves or unresolves a thread: a resolution
# is the human's act in GitHub, which is what lets a reader count one as a human's
# (tests/test_review_threads_contract.py fails on any such call under skills/ or scripts/).

# The stderr prefix for the one refusal review-post makes on GitHub state: this account already has a
# pending review on the PR that is not the pipeline's — the operator's own draft. GitHub allows one
# pending review per account per PR, so posting would fail or fold the operator's draft into ours.
REVIEW_PENDING_CONFLICT = "REVIEW_PENDING_CONFLICT"


_ADD_REVIEW_MUTATION = (
    "mutation($pr: ID!, $commit: GitObjectID!, $body: String!) { addPullRequestReview(input: "
    "{pullRequestId: $pr, commitOID: $commit, body: $body}) { pullRequestReview { id url } } }"
)
_THREAD_RESULT = "{ thread { id comments(first: 1) { nodes { url } } } }"
_ADD_LINE_THREAD_MUTATION = (
    "mutation($review: ID!, $path: String!, $body: String!, $line: Int!) { addPullRequestReviewThread("
    "input: {pullRequestReviewId: $review, path: $path, body: $body, line: $line, side: RIGHT}) "
    + _THREAD_RESULT + " }"
)
_ADD_RANGE_THREAD_MUTATION = (
    "mutation($review: ID!, $path: String!, $body: String!, $startLine: Int!, $line: Int!) { "
    "addPullRequestReviewThread(input: {pullRequestReviewId: $review, path: $path, body: $body, "
    "startLine: $startLine, startSide: RIGHT, line: $line, side: RIGHT}) " + _THREAD_RESULT + " }"
)
_ADD_FILE_THREAD_MUTATION = (
    "mutation($review: ID!, $path: String!, $body: String!) { addPullRequestReviewThread(input: "
    "{pullRequestReviewId: $review, path: $path, body: $body, subjectType: FILE}) " + _THREAD_RESULT + " }"
)
_SUBMIT_REVIEW_MUTATION = (
    "mutation($review: ID!) { submitPullRequestReview(input: {pullRequestReviewId: $review, "
    "event: COMMENT}) { pullRequestReview { id url } } }"
)
_DELETE_REVIEW_MUTATION = (
    "mutation($review: ID!) { deletePullRequestReview(input: {pullRequestReviewId: $review}) "
    "{ clientMutationId } }"
)
# The pre-reads: a thread before replying in it (review-reply refuses a resolved one, one on another PR,
# a plain reply under an operator's standing `accept` / `dismiss` decision, and a `dismiss` decision on a
# thread that is not a security finding's, #169), and a review whose submit exited non-zero (it may have
# landed). The thread's comments are read with the reader's own fields, so the op and `read_threads` see
# the same standing decision.
_THREAD_QUERY = (
    "query($thread: ID!) { viewer { login } node(id: $thread) { ... on PullRequestReviewThread { isResolved "
    "pullRequest { number repository { nameWithOwner } } "
    "root: comments(first: 1) { nodes { " + review_threads._COMMENT_FIELDS + " } } "
    "recent: comments(last: " + str(review_threads.THREAD_RECENT_COMMENTS) + ") { nodes { "
    + review_threads._COMMENT_FIELDS + " } } } } }"
)
_REVIEW_STATE_QUERY = "query($review: ID!) { node(id: $review) { ... on PullRequestReview { state url } } }"

_REPLY_MUTATION = (
    "mutation($thread: ID!, $body: String!) { addPullRequestReviewThreadReply(input: "
    "{pullRequestReviewThreadId: $thread, body: $body}) { comment { url } } }"
)


def _pending_review_query(repo, pr):
    owner, _, name = repo.partition("/")
    return (
        "query { viewer { login } repository(owner: %s, name: %s) { pullRequest(number: %d) { id "
        "reviews(states: PENDING, first: 20) { nodes { id body author { login } "
        "comments(first: 100) { totalCount nodes { body } } } } } } }"
        % (json.dumps(owner), json.dumps(name), int(pr))
    )


def _pending_review_foreign(review):
    """Why a pending review of this account is not wholly the pipeline's, or ``None`` when it is: its
    body carries the review stamp and every one of its comments a thread-root stamp. A line comment the
    operator adds in GitHub's UI while a review is pending goes INTO that review, so the body alone
    cannot say the review is safe to delete."""
    if (review_threads.parse_stamp(review.get("body")) or {}).get("kind") != "review":
        return "is not the pipeline's"
    comments = review.get("comments") if isinstance(review.get("comments"), dict) else {}
    nodes = [c for c in comments.get("nodes") or [] if isinstance(c, dict)]
    total = comments.get("totalCount")
    if not isinstance(total, int) or total > len(nodes):
        return "has more comments than were read"
    if any((review_threads.parse_stamp(c.get("body")) or {}).get("kind") != "root" for c in nodes):
        return "carries comments the pipeline did not write"
    return None


def _stamped_path(body_path):
    return Path(str(body_path) + ".stamped.md")


def _graphql(fields, cwd):
    """Run one `gh api graphql` call (``fields``: the ``-f``/``-F`` pairs after ``graphql``). Returns
    ``(data, result)`` — ``data`` the response's ``data`` object, or ``None`` on a non-zero exit or an
    unreadable response."""
    result = process.run(["gh", "api", "graphql"] + fields, cwd=cwd)
    data = None
    if result.returncode == 0 and not result.auth_required:
        try:
            data = (json.loads(result.stdout or "null") or {}).get("data")
        except (ValueError, AttributeError):
            data = None
    return data, result


def _fail_graphql(result, step):
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    sys.stderr.write("gh_persist.py: %s failed\n%s" % (step, result.stderr or result.stdout or ""))
    return 1


def _load_review_spec(path, parser):
    """The spec, its shape checked once: every thread a dict, `review_body` and every thread `body` a
    non-empty path string naming its own file, `head` a 40-hex string, and the optional `phase` (#170, a
    resolver review pause's) a positive int. `threads` may be empty only with `phase`: every pause posts
    its review, anchorless triggers and all, while the evaluator posts only when a thread is new. A wrong
    shape is a usage error before anything is read, stamped or posted — never a coerced value or a
    traceback."""
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        parser.error("spec %s is not readable JSON: %s" % (path, exc))
    if not isinstance(spec, dict) or not isinstance(spec.get("threads"), list):
        parser.error("spec %s needs a `threads` list" % path)
    phase = spec.get("phase")
    if phase is not None and (not isinstance(phase, int) or isinstance(phase, bool) or phase < 1):
        parser.error("spec %s: `phase` must be a positive integer, got %r" % (path, phase))
    if not spec["threads"] and phase is None:
        parser.error("spec %s needs a non-empty `threads` list (an empty one only with `phase`)" % path)
    if not all(isinstance(t, dict) for t in spec["threads"]):
        parser.error("spec %s: every entry of `threads` must be an object" % path)
    head = spec.get("head")
    if not (isinstance(head, str) and re.fullmatch(r"[0-9a-f]{40}", head)):
        parser.error("spec %s needs `head`, the gated head's 40-hex sha" % path)
    bodies = [spec.get("review_body")] + [t.get("body") for t in spec["threads"]]
    if not all(isinstance(b, str) and b for b in bodies):
        parser.error("spec %s needs `review_body` and every thread's `body` as the path of the text the "
                     "model wrote" % path)
    # Each body is stamped to `<body>.stamped.md`, so two entries naming one file would share a stamped
    # copy and the later stamp (its F id) would be posted on both threads.
    try:
        resolved = [str(Path(b).resolve()) for b in bodies]
    except (OSError, ValueError) as exc:
        parser.error("spec %s names a body path that cannot be resolved: %s" % (path, exc))
    if len(set(resolved)) != len(resolved):
        parser.error("spec %s names one body file for two entries; give each thread its own" % path)
    return spec


def _read_body(path, parser):
    """A body file's text; unreadable or not UTF-8 is a usage error, never a traceback."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        parser.error("body file %s is not readable as UTF-8: %s" % (path, exc))


def _stamp_review_spec(spec, parser):
    """Stamp the review body and every thread body to their `.stamped.md` copies. Returns
    ``(review_stamped, [(thread, stamped_path)])``. Every text is built before any file is written, so
    a value the stamp grammar cannot carry (a usage error) leaves no stamped copy behind."""
    head = spec["head"]
    phase = spec.get("phase")
    review_text = review_threads.stamp_review(head, _read_body(spec["review_body"], parser), phase=phase)
    built = []
    for thread in spec["threads"]:
        subject = thread.get("subject")
        start = thread.get("start_line")
        line = thread.get("line")
        if subject == "line" and start is None:
            start = line
        try:
            text = review_threads.stamp_root(
                thread.get("f"), head, thread.get("path"), subject, start, line,
                thread.get("flags") or [], _read_body(thread["body"], parser),
                supersedes=thread.get("supersedes"), phase=phase,
            )
        except ValueError as exc:
            parser.error("thread F%s cannot be stamped: %s" % (thread.get("f"), exc))
        built.append((dict(thread, start_line=start), _stamped_path(thread["body"]), text))
    review_stamped = _stamped_path(spec["review_body"])
    review_stamped.write_text(review_text, encoding="utf-8")
    stamped = []
    for thread, path, text in built:
        path.write_text(text, encoding="utf-8")
        stamped.append((thread, path))
    return review_stamped, stamped


def _thread_fields(thread, review_id, body_path):
    fields = ["-f", "review=%s" % review_id, "-f", "path=%s" % thread["path"], "-F", "body=@%s" % body_path]
    if thread["subject"] == "file":
        return ["-f", "query=" + _ADD_FILE_THREAD_MUTATION] + fields
    if thread["start_line"] != thread["line"]:
        return (["-f", "query=" + _ADD_RANGE_THREAD_MUTATION] + fields
                + ["-F", "startLine=%d" % thread["start_line"], "-F", "line=%d" % thread["line"]])
    return ["-f", "query=" + _ADD_LINE_THREAD_MUTATION] + fields + ["-F", "line=%d" % thread["line"]]


def _cmd_review_post(args, parser):
    if not args.pr.isdigit():
        parser.error("pr expects a PR number, got %r" % args.pr)
    spec = _load_review_spec(args.spec_path, parser)
    # The caller's own files first: a stamped copy is never empty (the marker alone fills it), so the
    # empty-body gate has to read what the model wrote.
    for path in [spec["review_body"]] + [t["body"] for t in spec["threads"]]:
        gate = _verify_body_file(path)
        if gate is not None:
            emit_needs_decision(gate)
            return EXIT_OK
    review_stamped, stamped = _stamp_review_spec(spec, parser)
    for path in [review_stamped] + [p for _, p in stamped]:
        gate = _verify_body_file(path)
        if gate is not None:
            emit_needs_decision(gate)
            return EXIT_OK

    receipts = [
        dict({"f": thread.get("f")}, **_write_receipt_fields(path)) for thread, path in stamped
    ]
    if args.dry_run:
        would_run = [_quote_cmd(["gh", "api", "graphql", "-f", "query=" + _pending_review_query(args.repo, args.pr)]),
                     _quote_cmd(["gh", "api", "graphql", "-f", "query=" + _ADD_REVIEW_MUTATION, "-f", "pr=<pr id>",
                                 "-f", "commit=%s" % spec["head"], "-F", "body=@%s" % review_stamped])]
        would_run += [_quote_cmd(["gh", "api", "graphql"] + _thread_fields(thread, "<review id>", path))
                      for thread, path in stamped]
        would_run.append(_quote_cmd(["gh", "api", "graphql", "-f", "query=" + _SUBMIT_REVIEW_MUTATION,
                                     "-f", "review=<review id>"]))
        payload = {"op": "review-post", "dry_run": True, "would_run": would_run, "threads": receipts,
                   "review_body_path": str(review_stamped), "phase": spec.get("phase")}
        payload.update(_write_receipt_fields(review_stamped))
        emit_ok(payload=payload)
        return EXIT_OK

    data, result = _graphql(["-f", "query=" + _pending_review_query(args.repo, args.pr)], args.cwd)
    try:
        viewer = data["viewer"]["login"]
        pr_node = data["repository"]["pullRequest"]
        pr_id = pr_node["id"]
        pending = [r for r in pr_node["reviews"]["nodes"] if isinstance(r, dict)]
    except (TypeError, KeyError):
        return _fail_graphql(result, "reading the PR and its pending review")
    for review in pending:
        if (review.get("author") or {}).get("login") != viewer:
            continue
        cause = _pending_review_foreign(review)
        if cause:
            sys.stderr.write(
                "%s: this account has a pending review on #%s (%s) that %s — the operator's own draft, "
                "or a stranded pipeline review the operator added to. Submit or discard it on GitHub, "
                "then re-run; nothing was posted.\n"
                % (REVIEW_PENDING_CONFLICT, args.pr, review.get("id"), cause)
            )
            return 1
        # The pipeline's own pending review, stranded by a run that failed between creating and
        # submitting it: every part of it carries a pipeline stamp, so nothing the operator wrote is in
        # it, and no one else can see a pending review.
        _, deleted = _graphql(["-f", "query=" + _DELETE_REVIEW_MUTATION, "-f", "review=%s" % review["id"]], args.cwd)
        if deleted.returncode != 0:
            return _fail_graphql(deleted, "deleting the stranded pipeline pending review %s" % review["id"])

    data, result = _graphql(
        ["-f", "query=" + _ADD_REVIEW_MUTATION, "-f", "pr=%s" % pr_id, "-f", "commit=%s" % spec["head"],
         "-F", "body=@%s" % review_stamped],
        args.cwd,
    )
    try:
        review_id = data["addPullRequestReview"]["pullRequestReview"]["id"]
    except (TypeError, KeyError):
        return _fail_graphql(result, "creating the pending review")

    # From here a failure leaves a pending review carrying the marker; the next run's stranded-review
    # delete clears it. Nothing is visible until the submit.
    for receipt, (thread, path) in zip(receipts, stamped):
        data, result = _graphql(_thread_fields(thread, review_id, path), args.cwd)
        try:
            node = data["addPullRequestReviewThread"]["thread"]
            receipt["thread_id"] = node["id"]
            receipt["url"] = ((node.get("comments") or {}).get("nodes") or [{}])[0].get("url")
        except (TypeError, KeyError, IndexError, AttributeError):
            return _fail_graphql(result, "adding thread F%s on %s" % (thread.get("f"), thread.get("path")))

    data, result = _graphql(["-f", "query=" + _SUBMIT_REVIEW_MUTATION, "-f", "review=%s" % review_id], args.cwd)
    if result.auth_required:
        return _fail_graphql(result, "submitting the review")
    url = None
    if result.returncode != 0:
        # The submit may have landed with a failed response; then nothing is pending any more, and a
        # re-run would post every thread again. A review no longer PENDING is a landed submit.
        state, readback = _graphql(["-f", "query=" + _REVIEW_STATE_QUERY, "-f", "review=%s" % review_id], args.cwd)
        node = (state or {}).get("node") if isinstance(state, dict) else None
        if readback.returncode != 0 or not isinstance(node, dict) or node.get("state") in (None, "PENDING"):
            if readback.returncode != 0:
                sys.stderr.write("gh_persist.py: the review's state could not be read back; the submit may "
                                 "have landed — check #%s before re-running\n" % args.pr)
            return _fail_graphql(result, "submitting the review")
        url = node.get("url")
    else:
        # A zero exit is the submit landing: a response that does not carry the URL must not read as a
        # failure, or the caller re-runs and posts the threads twice (nothing is pending to delete).
        try:
            url = data["submitPullRequestReview"]["pullRequestReview"]["url"]
        except (TypeError, KeyError):
            url = None

    payload = {"op": "review-post", "dry_run": False, "url": url, "review_id": review_id,
               "threads": [{k: r.get(k) for k in ("f", "thread_id", "url", "body_bytes", "body_sha256")}
                           for r in receipts],
               "review_body_path": str(review_stamped), "phase": spec.get("phase")}
    payload.update(_write_receipt_fields(review_stamped))
    emit_ok(payload=payload)
    return EXIT_OK


def _is_security_root(comments, viewer, repo, cwd):
    """Whether the thread's root is the pipeline's own security finding, judged as `read_threads` judges it:
    a trusted, marked root stamp carrying a `security:` key, which counts only when this viewer wrote it or
    its author's repo permission is resolution-grade (SEC-3, `review_threads._demote_unmarkable`). A
    `triage` / `read` author's pasted stamp is human text to the reader, so it carries no finding here
    either; a failed permission lookup reads as untrusted, the refusing side."""
    root = comments[0] if comments else None
    if not root or not root.get("trusted") or not root.get("marked"):
        return False
    stamp = root.get("stamp") or {}
    if stamp.get("kind") != "root" or not any(k.startswith("security:") for k in stamp.get("flags") or []):
        return False
    author = root.get("author")
    if viewer and author == viewer:
        return True
    if not author:
        return False
    result = process.run(review_threads.permission_argv(repo, author), cwd=cwd)
    value = (result.stdout or "").strip() if result.returncode == 0 else ""
    return trust.is_trusted_permission(value or None)


def _cmd_review_reply(args, parser):
    if not args.pr.isdigit():
        parser.error("pr expects a PR number, got %r" % args.pr)
    if not re.fullmatch(r"[0-9a-f]{40}", args.head or ""):
        parser.error("--head expects the pushed commit's 40-hex sha, got %r" % args.head)
    # The caller's own file first: the stamped copy is never empty (the marker alone fills it).
    gate = _verify_body_file(args.body_path)
    if gate is not None:
        emit_needs_decision(gate)
        return EXIT_OK
    body = _read_body(args.body_path, parser)
    if args.decision is not None and not body.lstrip().lower().startswith(args.decision + ":"):
        # The op writes the visible word; the caller's file is only the acknowledgement, the notes or the
        # reason, and a file that already leads with the word is not prefixed twice. Readers key on the
        # stamp's decision field, never on this word (#169).
        body = "%s: %s" % (args.decision, body)
    try:
        stamped_text = review_threads.stamp_reply(args.head, body, f=args.f, decision=args.decision)
    except ValueError as exc:
        parser.error("the reply cannot be stamped: %s" % exc)
    stamped = _stamped_path(args.body_path)
    stamped.write_text(stamped_text, encoding="utf-8")
    gate = _verify_body_file(stamped)
    if gate is not None:
        emit_needs_decision(gate)
        return EXIT_OK

    fields = ["-f", "query=" + _REPLY_MUTATION, "-f", "thread=%s" % args.thread, "-F", "body=@%s" % stamped]
    check = ["-f", "query=" + _THREAD_QUERY, "-f", "thread=%s" % args.thread]
    if args.dry_run:
        payload = {"op": "review-reply", "dry_run": True,
                   "would_run": [_quote_cmd(["gh", "api", "graphql"] + check),
                                 _quote_cmd(["gh", "api", "graphql"] + fields)],
                   "thread": args.thread, "decision": args.decision, "stamped_path": str(stamped)}
        payload.update(_write_receipt_fields(stamped))
        emit_ok(payload=payload)
        return EXIT_OK

    # The pipeline never replies in a resolved thread: a reply moves the thread's reference commit
    # (skills/_shared/review-threads.md §6), so one posted after a resolution would make the human's
    # resolution count for code they never saw. GitHub records no resolution time, so the write side
    # holds the rule. A thread on another PR, or no thread at all, is refused the same way. The refusal
    # is an `ok` with `posted: false` and a `refused` reason, never a failure: a human may resolve an
    # addressed thread between the caller's read and this reply, and the caller skips it.
    # An operator's standing `accept` / `dismiss` decision reply (#169) is refused for a different reason: a
    # decision stands only while it is the thread's latest trusted comment, so a plain reply would void the
    # recorded acceptance; it is refused `decided` while that decision stands. A decision reply is never refused `decided` — it is the
    # operator's own later word, on the code they just reviewed. A `dismiss` decision means something only
    # on a security finding's own thread (its root stamp carries a `security:` key), so anywhere else it is
    # refused `not-security`.
    data, result = _graphql(check, args.cwd)
    if result.returncode != 0 or result.auth_required or not isinstance(data, dict):
        return _fail_graphql(result, "reading thread %s before replying" % args.thread)
    node = data.get("node")
    refused = None
    if not isinstance(node, dict) or "isResolved" not in node:
        refused = "not-a-thread"
    else:
        pull = node.get("pullRequest") or {}
        repo = ((pull.get("repository") or {}).get("nameWithOwner") or "")
        if pull.get("number") != int(args.pr) or repo.lower() != args.repo.lower():
            refused = "other-pr"
        elif node.get("isResolved"):
            refused = "resolved"
        else:
            viewer = ((data.get("viewer") or {}).get("login")) if isinstance(data.get("viewer"), dict) else None
            comments = review_threads.annotate_decisions(review_threads.thread_comments(node), viewer)
            if args.decision is None and review_threads.standing_decision(comments) in ("accept", "dismiss"):
                refused = "decided"
            elif args.decision == "dismiss" and not _is_security_root(comments, viewer, args.repo, args.cwd):
                refused = "not-security"
    if refused:
        payload = {"op": "review-reply", "dry_run": False, "posted": False, "refused": refused,
                   "url": None, "thread": args.thread, "decision": args.decision, "stamped_path": str(stamped)}
        payload.update(_write_receipt_fields(stamped))
        emit_ok(payload=payload)
        return EXIT_OK

    data, result = _graphql(fields, args.cwd)
    try:
        url = data["addPullRequestReviewThreadReply"]["comment"]["url"]
    except (TypeError, KeyError):
        return _fail_graphql(result, "replying in thread %s" % args.thread)
    payload = {"op": "review-reply", "dry_run": False, "posted": True, "refused": None, "url": url,
               "thread": args.thread, "decision": args.decision, "stamped_path": str(stamped)}
    payload.update(_write_receipt_fields(stamped))
    emit_ok(payload=payload)
    return EXIT_OK


# ---- close / reopen ----


def _cmd_close(args):
    # Bodyless state change — no empty-body gate (architecture.md §7: "close/reopen idempotency").
    # `close` on an already-closed issue is a `gh` no-op, which is what makes this safe for a
    # reentrant caller (e.g. question-resolver re-running after an interrupted session).
    cmd = ["gh", "issue", "close", args.issue, "--repo", args.repo]
    if args.reason:
        cmd += ["--reason", args.reason]

    if args.dry_run:
        emit_ok(payload={"op": "close", "issue": args.issue, "dry_run": True,
                          "would_run": _quote_cmd(cmd)})
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1

    emit_ok(payload={"op": "close", "issue": args.issue, "dry_run": False, "closed": True})
    return EXIT_OK


# ---- close-pr ----


def _cmd_close_pr(args):
    # The comment is OPTIONAL: --comment-file given -> the empty-body gate applies to that staged
    # file (an included comment must be non-empty, per the standard body-bearing-write discipline);
    # --comment-file omitted -> bodyless close, exactly like `close` (module docstring: "close-pr
    # closes a PR with an optional staged comment").
    comment_text = None
    if args.comment_file:
        gate = _verify_body_file(args.comment_file)
        if gate is not None:
            emit_needs_decision(gate)
            return EXIT_OK
        comment_text = Path(args.comment_file).read_text(encoding="utf-8")

    cmd = ["gh", "pr", "close", args.pr, "--repo", args.repo]
    if comment_text is not None:
        # `gh pr close` has no --body-file of its own; the staged comment crosses the gh-argv
        # boundary as text (it already crossed the prompt boundary as a path — see module
        # docstring). The caller is responsible for staging the exact byte-faithful text a
        # downstream reader (e.g. the resolver's predecessor-PR detection) may grep for.
        cmd += ["--comment", comment_text]

    if args.dry_run:
        payload = {"op": "close-pr", "pr": args.pr, "dry_run": True, "would_run": _quote_cmd(cmd)}
        if args.comment_file:
            payload.update(_write_receipt_fields(args.comment_file))
        emit_ok(payload=payload)
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1

    payload = {
        "op": "close-pr", "pr": args.pr, "dry_run": False, "closed": True,
        "commented": comment_text is not None,
    }
    if args.comment_file:
        payload.update(_write_receipt_fields(args.comment_file))
    emit_ok(payload=payload)
    return EXIT_OK


def _cmd_reopen(args):
    # Sibling of _cmd_close for the reentrant case where a prematurely-closed question must be
    # reopened before its decision is revised. No --reason, no empty-body gate.
    cmd = ["gh", "issue", "reopen", args.issue, "--repo", args.repo]

    if args.dry_run:
        emit_ok(payload={"op": "reopen", "issue": args.issue, "dry_run": True,
                          "would_run": _quote_cmd(cmd)})
        return EXIT_OK

    result = process.run(cmd, cwd=args.cwd)
    if result.auth_required:
        emit_needs_decision(_auth_decision(result))
        return EXIT_OK
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return 1

    emit_ok(payload={"op": "reopen", "issue": args.issue, "dry_run": False, "reopened": True})
    return EXIT_OK


# ---- argument parsing ----


def _build_parser():
    """Returns ``(parser, subparsers_by_name)`` — the second element lets ``main()`` pass a
    subcommand's OWN parser (not the top-level one) to ``_cmd_link``/``_cmd_comment`` for their
    ``.error()`` calls, so a usage error reports that subcommand's specific usage line rather than
    the generic top-level one listing all six subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="gh_persist.py",
        description="Single write path for GitHub issue/PR persistence (architecture.md §7).",
    )
    parser.add_argument(
        "--cwd", default=None,
        help="working directory for the underlying git/gh calls (cwd discipline: never rely on "
             "ambient cwd; callers pass this explicitly derived from their own arguments)",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    subparsers_by_name = {}

    p_create = sub.add_parser("create")
    p_create.add_argument("repo")
    p_create.add_argument("body_path")
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--label", action="append", default=[])
    p_create.add_argument("--blocked-by", default="")
    p_create.add_argument("--blocking", default="")
    p_create.add_argument("--parent", default="")
    p_create.add_argument("--attach", action="append", default=[], metavar="FILE[#ALT]")
    p_create.add_argument("--dry-run", action="store_true")

    p_create_pr = sub.add_parser("create-pr")
    p_create_pr.add_argument("repo")
    p_create_pr.add_argument("body_path")
    p_create_pr.add_argument("--title", required=True)
    p_create_pr.add_argument("--base", required=True)
    p_create_pr.add_argument("--head", required=True)
    p_create_pr.add_argument("--draft", action="store_true")
    p_create_pr.add_argument("--attach", action="append", default=[], metavar="FILE[#ALT]")
    p_create_pr.add_argument("--dry-run", action="store_true")

    p_edit = sub.add_parser("edit-body")
    p_edit.add_argument("repo")
    p_edit.add_argument("issue")
    p_edit.add_argument("body_path")
    p_edit.add_argument("--attach", action="append", default=[], metavar="FILE[#ALT]")
    p_edit.add_argument("--dry-run", action="store_true")

    p_edit_pr_body = sub.add_parser("edit-pr-body")
    p_edit_pr_body.add_argument("repo")
    p_edit_pr_body.add_argument("pr")
    p_edit_pr_body.add_argument("body_path")
    p_edit_pr_body.add_argument("--attach", action="append", default=[], metavar="FILE[#ALT]")
    p_edit_pr_body.add_argument("--dry-run", action="store_true")

    p_edit_title = sub.add_parser("edit-title")
    p_edit_title.add_argument("repo")
    p_edit_title.add_argument("issue")
    p_edit_title.add_argument("--title", required=True)
    p_edit_title.add_argument("--dry-run", action="store_true")

    p_edit_pr_title = sub.add_parser("edit-pr-title")
    p_edit_pr_title.add_argument("repo")
    p_edit_pr_title.add_argument("pr")
    p_edit_pr_title.add_argument("--title", required=True)
    p_edit_pr_title.add_argument("--dry-run", action="store_true")

    p_edit_labels = sub.add_parser("edit-labels")
    p_edit_labels.add_argument("repo")
    p_edit_labels.add_argument("issue")
    p_edit_labels.add_argument("--add", action="append", default=[])
    p_edit_labels.add_argument("--remove", action="append", default=[])
    p_edit_labels.add_argument("--dry-run", action="store_true")

    p_link = sub.add_parser("link")
    p_link.add_argument("repo")
    p_link.add_argument("issue")
    # All four relationship flags share one dest/action (_OrderedLinkFlagAction) so their
    # occurrences land in a single list in true CLI order — see that class's docstring for why
    # this must not be four separate action="append" destinations.
    for flag in ("--add-blocked-by", "--remove-blocked-by", "--add-blocking", "--remove-blocking"):
        p_link.add_argument(flag, dest="link_edit_flags", action=_OrderedLinkFlagAction, metavar="N")
    p_link.add_argument("--dry-run", action="store_true")

    p_add_parent = sub.add_parser("add-parent")
    p_add_parent.add_argument("repo")
    p_add_parent.add_argument("child")
    p_add_parent.add_argument("parent")
    p_add_parent.add_argument("--dry-run", action="store_true")

    p_comment = sub.add_parser("comment")
    p_comment.add_argument("repo")
    p_comment.add_argument("target", choices=["issue", "pr", "pr-review"])
    p_comment.add_argument("id")
    p_comment.add_argument("body_path")
    p_comment.add_argument("--review-action", choices=["approve", "comment", "request-changes"],
                            default=None)
    p_comment.add_argument("--delete-marker-id", default=None)
    p_comment.add_argument("--attach", action="append", default=[], metavar="FILE[#ALT]")
    p_comment.add_argument("--dry-run", action="store_true")

    p_edit_comment = sub.add_parser("edit-comment")
    p_edit_comment.add_argument("repo")
    p_edit_comment.add_argument("comment_id")
    p_edit_comment.add_argument("body_path")
    p_edit_comment.add_argument("--dry-run", action="store_true")

    p_review_post = sub.add_parser("review-post")
    p_review_post.add_argument("repo")
    p_review_post.add_argument("pr")
    p_review_post.add_argument("spec_path")
    p_review_post.add_argument("--dry-run", action="store_true")

    p_review_reply = sub.add_parser("review-reply")
    p_review_reply.add_argument("repo")
    p_review_reply.add_argument("pr")
    p_review_reply.add_argument("--thread", required=True, help="the thread's GraphQL node id")
    p_review_reply.add_argument("--head", required=True, help="the pushed commit's 40-hex sha")
    p_review_reply.add_argument("--f", type=int, default=None, help="the thread's F id, when it has one")
    p_review_reply.add_argument("body_path")
    p_review_reply.add_argument("--decision", choices=review_threads.DECISIONS, default=None,
                                help="record an operator's consent-card answer from this session (#169)")
    p_review_reply.add_argument("--dry-run", action="store_true")

    p_close = sub.add_parser("close")
    p_close.add_argument("repo")
    p_close.add_argument("issue")
    p_close.add_argument("--reason", choices=["completed", "not planned"], default=None)
    p_close.add_argument("--dry-run", action="store_true")

    p_close_pr = sub.add_parser("close-pr")
    p_close_pr.add_argument("repo")
    p_close_pr.add_argument("pr")
    p_close_pr.add_argument("--comment-file", default=None)
    p_close_pr.add_argument("--dry-run", action="store_true")

    p_reopen = sub.add_parser("reopen")
    p_reopen.add_argument("repo")
    p_reopen.add_argument("issue")
    p_reopen.add_argument("--dry-run", action="store_true")

    subparsers_by_name.update(
        {
            "create": p_create,
            "create-pr": p_create_pr,
            "edit-body": p_edit,
            "edit-pr-body": p_edit_pr_body,
            "edit-title": p_edit_title,
            "edit-pr-title": p_edit_pr_title,
            "edit-labels": p_edit_labels,
            "link": p_link,
            "add-parent": p_add_parent,
            "comment": p_comment,
            "edit-comment": p_edit_comment,
            "review-post": p_review_post,
            "review-reply": p_review_reply,
            "close": p_close,
            "close-pr": p_close_pr,
            "reopen": p_reopen,
        }
    )
    return parser, subparsers_by_name


def main(argv):
    parser, subparsers_by_name = _build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "create":
        return _cmd_create(args, subparsers_by_name["create"])
    if args.subcommand == "create-pr":
        return _cmd_create_pr(args, subparsers_by_name["create-pr"])
    if args.subcommand == "edit-body":
        return _cmd_edit_body(args, subparsers_by_name["edit-body"])
    if args.subcommand == "edit-pr-body":
        return _cmd_edit_pr_body(args, subparsers_by_name["edit-pr-body"])
    if args.subcommand == "edit-title":
        return _cmd_edit_title(args)
    if args.subcommand == "edit-pr-title":
        return _cmd_edit_pr_title(args)
    if args.subcommand == "edit-labels":
        # Pass edit-labels' own sub-parser for its .error() call, same rationale as link below.
        return _cmd_edit_labels(args, subparsers_by_name["edit-labels"])
    if args.subcommand == "link":
        # Pass the link sub-parser (not the top-level one) for its .error() calls, so a usage
        # error (e.g. "no relationship flags given") reports link's own usage line, not the
        # generic top-level one listing all six subcommands.
        return _cmd_link(args, subparsers_by_name["link"])
    if args.subcommand == "add-parent":
        # Own sub-parser for the self-parenting usage error, same rationale as link above.
        return _cmd_add_parent(args, subparsers_by_name["add-parent"])
    if args.subcommand == "comment":
        return _cmd_comment(args, subparsers_by_name["comment"])
    if args.subcommand == "edit-comment":
        return _cmd_edit_comment(args, subparsers_by_name["edit-comment"])
    if args.subcommand == "review-post":
        return _cmd_review_post(args, subparsers_by_name["review-post"])
    if args.subcommand == "review-reply":
        return _cmd_review_reply(args, subparsers_by_name["review-reply"])
    if args.subcommand == "close":
        return _cmd_close(args)
    if args.subcommand == "close-pr":
        return _cmd_close_pr(args)
    return _cmd_reopen(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
