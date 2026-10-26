#!/usr/bin/env python3
"""``gh_gather.py`` — the fixed issue-fetch envelope (architecture.md §3, §7's ``GATHER_ISSUE``),
ported from v1's ``gh-gather.sh``. One round-trip issue fetch (body + full comment thread + labels
+ state) instead of several, with the same byte-threshold spill routing and native-dependency
capability-gating v1 established — re-expressed as a §3 envelope instead of v1's ad-hoc JSON.

Usage::

    gh_gather.py <issue> <owner/repo> [marker_prefix] [scratch_dir] [--extra-json FIELDS]

Positional order matches v1 exactly (the v1 executor agent's ``GATHER_ISSUE(issue, repo,
marker_prefix?, scratch_dir?)``); ``--extra-json`` is the one caller-facing knob v1 handled as a
v1-executor special case (its "If the caller passes extra_json=<fields>..." paragraph) and is
ported here as an optional flag rather than a fifth positional, since it's rare and named at every
real call site (the drafter's revise-mode ``closedByPullRequestsReferences,projectItems`` lookup).

Behavior preserved from v1 (see module-level comments at each step below for the "why"):

- **Thread pagination** — the full comment thread via the paginated REST endpoint, never the
  ``gh issue view --json comments`` ~100-comment cap.
- **Marker discovery** — comments whose body starts with ``marker_prefix``, with
  ``marker_comment_count`` always reported; **more than one is a v2-native ``MARKER_AMBIGUOUS``
  decision** (v1 only reported the count and left the ambiguity call to its caller; the port makes
  it a first-class §3 decision per this step's DoD and architecture.md §3's decision-code table
  naming "the gathers" as a ``MARKER_AMBIGUOUS`` emitter).
- **Whole-plan assembly** — when ``marker_prefix`` is the implementation-plan marker, the marker
  body is the plan with its part comments put back (``plan_parts.py``,
  ``skills/_shared/plan-parts.md``), and ``marker_parts`` lists those parts' ids and URLs. A
  duplicated part is ``MARKER_AMBIGUOUS``, like a duplicated plan.
- **PR-number guard (v2-native)** — issues and PRs share one number space and ``gh issue view``
  silently returns the PR when given a PR number; the fetched ``url`` (``/pull/<N>`` vs
  ``/issues/<N>``) is the discriminator, and a PR target is a ``TARGET_IS_PR`` decision emitted
  **before any further round-trip**, with the PR body's closing-keyword issue references
  (``Fixes #62``) surfaced in the decision context so the operator can re-target the origin issue.
- **Threshold spill** — body / thread / marker comment body via ``pipelib.spill``.
- **Native issue dependencies** — ``blocked_by`` / ``blocking`` + ``deps_available``,
  capability-gated: an "unknown field" rejection from `gh` degrades to the base field set plus a
  ``DEPS_UNSUPPORTED`` notice; any other failure is a hard failure (exit 1, no envelope) because
  silently defaulting ``deps_available: false`` on a real transient/auth/rate-limit error would
  hide a genuine native blocker from the resolver's/evaluator's hard gate on ``blocked_by``.
- **Native parent/sub-issue hierarchy** — ``parent`` / ``sub_issues`` / ``sub_issues_summary`` +
  ``subissues_available``, gated the same way and INDEPENDENTLY (see
  :func:`_fetch_issue_with_relation_capability_gates`), with its own ``SUBISSUES_UNSUPPORTED``
  notice because its fallback differs: epic↔story hierarchy readers drop back to parsing the
  legacy ``## Stories`` checklist, not to prose dependency links. This is the relation that drives
  GitHub's sub-issue panel, the epic's progress rollup, and a Project's Sub-issues progress field —
  none of which a markdown checklist can drive.
- **`AUTH_REQUIRED` classification** — every `gh` call this module makes is checked for the
  pipelib runner's auth-failure classification (``CommandResult.auth_required``) ahead of any
  other failure branch; an authenticated-required `gh` exit (gh's own exit code 4) emits a
  ``needs_decision`` ``AUTH_REQUIRED`` envelope (exit 0) instead of the undifferentiated hard
  failure (exit 1) a plain non-zero `gh` exit would otherwise produce — same treatment
  ``gh_pr_gather.py`` and ``gh_persist.py`` give every one of their `gh` calls.

Every ``gh`` call passes ``--repo <owner/repo>`` explicitly — this module never relies on ambient
cwd (S21 brief's cwd-discipline advisory), so it behaves identically regardless of where the
caller (a prep script, S6+) is sitting.

**Open-PR search false-positive fix (S12 follow-up round).** ``_fetch_open_prs``'s ``--search "<N>
in:body"`` query is a GitHub full-text search, not a literal-string match — live evidence against
the sandbox repo (``gh pr list --search "2 in:body"``) showed it returning PRs whose body merely
contains the digit ``2`` as part of unrelated prose (``"## Phase tracker\n- [x] Phase 2 — ..."``,
present in nearly every multi-phase PR body), never referencing issue ``#2`` at all — and a second
control run with ``--search "#2 in:body"`` (the hash-prefixed form) returned the **identical**
false-positive set, proving GitHub's server-side search does not use the ``#`` as an anchor either
(the query *form* does not protect against this; only a client-side post-filter does). This module
now fetches ``body``/``closingIssuesReferences`` alongside the search results and filters them
through :func:`references_issue` before returning — a genuine ``#<N>`` reference (boundary-guarded
on both sides, matching GitHub's own autolink semantics: ``#2`` never matches ``#20``/``#12`` (a
longer shared-prefix number) or ``#2abc``/``#2E8B57`` (a hex color or any other alphanumeric token
glued onto the digits — a false positive the S12 acceptance review reproduced directly), and prose
like "Phase 2"/"issue 2" with no ``#`` never matches) or membership in ``closingIssuesReferences``.
The returned ``open_prs`` payload shape is
unchanged (``body``/``closingIssuesReferences`` are stripped back off after filtering) — this is a
correctness fix to *which* PRs are returned, not a facts-block schema change. See
``docs/specs/resolver.md`` / ``docs/specs/planner.md`` "Known bugs/gaps" for the newly-discovered
v1-inherited defect this closes (v1's ``gh-gather.sh`` has the identical false-positive exposure;
v2 fixes it here, an intentional, explained parity divergence).
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib import process  # noqa: E402  (import after sys.path setup, by necessity)
import plan_parts  # noqa: E402  (the plan's part comments — skills/_shared/plan-parts.md)
from pipelib.decisions import AUTH_REQUIRED, MARKER_AMBIGUOUS, TARGET_IS_PR, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, emit_needs_decision, emit_ok  # noqa: E402
from pipelib.spill import resolve_threshold, spill_bytes  # noqa: E402

# Base issue fields, always fetched — same set v1 requested (BASE_FIELDS in gh-gather.sh).
BASE_FIELDS = (
    "number,title,body,state,labels,author,createdAt,updatedAt,assignees,milestone,url"
)

# The native parent/sub-issue relation fields, capability-gated exactly like `blockedBy,blocking`
# and INDEPENDENTLY of them (different GitHub relations, different gh version floors).
# `subIssuesSummary` carries GitHub's own {total, completed, percentCompleted} rollup, so a reader
# never has to count states itself to render epic progress.
SUBISSUE_FIELDS = ",parent,subIssues,subIssuesSummary"

# gh's own phrasing for "you asked for a JSON field that doesn't exist on this object" — the
# capability-gate signature v1 grepped for (case-insensitively) to distinguish "this gh/repo
# genuinely lacks the issue-dependencies feature" from any other failure. Kept as the same
# substring test v1 used (`grep -qiE 'unknown (json )?field'`), re-expressed without a regex
# engine dependency: a plain case-insensitive substring check covers both of gh's observed
# phrasings ("unknown JSON field" / "unknown field").
_UNKNOWN_FIELD_MARKERS = ("unknown json field", "unknown field")


def _is_unknown_field_error(stderr):
    lowered = (stderr or "").lower()
    return any(marker in lowered for marker in _UNKNOWN_FIELD_MARKERS)


def _fail_hard(message):
    """Any non-capability-gate `gh` failure (network blip, auth, rate-limit, bad issue/repo) MUST
    surface loudly rather than as a clean-but-wrong envelope — v1's rationale, carried verbatim:
    silently degrading here would set ``deps_available=false`` on a supported repo and drop a real
    native blocker, defeating the resolver/evaluator hard-gate on ``blocked_by``. No envelope is
    emitted; stderr carries the faithful error; exit 1 (architecture.md §3: "any other non-zero —
    hard failure; ... no envelope is guaranteed").
    """
    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")
    return 1


def _auth_required_decision(result):
    """Build the ``AUTH_REQUIRED`` decision payload for a `gh` call the pipelib runner classified
    as an authentication failure (architecture.md §3: "detected by the pipelib runner, any
    script") — mirrors ``gh_pr_gather.py``'s identical helper so both gathers signal the same
    decision shape for the same underlying condition. Checked ahead of every other failure branch
    (capability-gate, generic hard-failure) at each `gh` call site in :func:`run`, so an auth
    failure never gets misclassified as a genuine "unknown field" capability miss or an
    undifferentiated hard failure the caller can't act on.
    """
    return needs_decision(
        AUTH_REQUIRED,
        summary="gh authentication required",
        context={"stderr": result.stderr, "returncode": result.returncode},
        options=["run: gh auth login"],
    )


def _fetch_issue_with_relation_capability_gates(issue, repo, env):
    """Try the fetch with BOTH native-relation field sets first, capability-gating each one
    independently: on an "unknown field" rejection, descend a ladder that drops one relation per
    rung, so a host serving dependencies but not sub-issues (or the reverse) still gets the
    relation it does serve.

    Returns ``(issue_obj, deps_available, subissues_available, notices)`` on success, or
    ``(None, None, None, result)`` on failure, where ``result`` is the failing
    :class:`pipelib.process.CommandResult` itself (not just its stderr text) so the caller can
    branch on ``result.auth_required`` before falling back to the generic hard-failure path —
    see :func:`run`'s auth-check-before-_fail_hard convention, applied uniformly at every `gh`
    call site in this module.

    The ladder descends rather than reading which field gh named in its error: gh's "unknown JSON
    field" phrasing is not a contract, and a read is idempotent, so retrying costs a round-trip on
    an unsupported host and nothing at all on a supported one (the first rung succeeds). The two
    relations report SEPARATE notices because they have separate fallbacks — prose ``Blocked by #N``
    linking for dependencies, the legacy ``## Stories`` checklist for epic hierarchy.
    """
    ladder = [
        (",blockedBy,blocking" + SUBISSUE_FIELDS, True, True, []),
        (",blockedBy,blocking", True, False, ["SUBISSUES_UNSUPPORTED"]),
        (SUBISSUE_FIELDS, False, True, ["DEPS_UNSUPPORTED"]),
        ("", False, False, ["DEPS_UNSUPPORTED", "SUBISSUES_UNSUPPORTED"]),
    ]
    for index, (extra_fields, deps_available, subissues_available, notices) in enumerate(ladder):
        attempt = process.run(
            ["gh", "issue", "view", str(issue), "--repo", repo, "--json", BASE_FIELDS + extra_fields],
            env=env,
        )
        if attempt.returncode == 0:
            return json.loads(attempt.stdout), deps_available, subissues_available, notices
        if attempt.auth_required:
            return None, None, None, attempt
        # Any OTHER failure must surface — see _fail_hard's docstring. Likewise a capability-shaped
        # failure on the LAST rung, which asked for no optional fields at all: that is not a
        # capability miss, it is a broken fetch.
        if index == len(ladder) - 1 or not _is_unknown_field_error(attempt.stderr):
            return None, None, None, attempt


# GitHub's closing keywords (close/closes/closed, fix/fixes/fixed, resolve/resolves/resolved),
# used only on the TARGET_IS_PR path to derive the PR's linked issue number(s) from the body the
# fetch already returned — no extra round-trip.
_CLOSING_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.IGNORECASE
)


def linked_issue_numbers(body_text):
    """Ordered, de-duplicated issue numbers referenced via a GitHub closing keyword (``Fixes
    #62``, ``Closes: #7``) in `body_text`. Best-effort derivation for the ``TARGET_IS_PR``
    decision's context — the origin issue of a mistakenly-targeted PR is almost always named this
    way in the PR body."""
    seen = []
    for match in _CLOSING_KEYWORD_RE.finditer(body_text or ""):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return seen


def _is_pull_request(issue_obj):
    """`gh issue view <N>` silently returns the pull request when `<N>` is a PR number (issues
    and PRs share one number space). The fetched `url` is the reliable discriminator in the base
    field set: an issue lives under `/issues/<N>`, a PR under `/pull/<N>`."""
    return "/pull/" in ((issue_obj or {}).get("url") or "")


def _target_is_pr_decision(issue, repo, issue_obj):
    linked = linked_issue_numbers(issue_obj.get("body"))
    options = []
    for number in linked:
        options.append(
            "resolve the linked issue instead — re-run against #%d (the PR body closes it)" % number
        )
    options.append("re-run against the intended issue number")
    options.append("if the PR itself is the target, use the PR-consuming flow (gh_pr_gather / the evaluator)")
    return needs_decision(
        TARGET_IS_PR,
        summary="#%s in %s is a pull request, not an issue — refusing to treat it as an issue target"
        % (issue, repo),
        context={
            "number": issue_obj.get("number"),
            "repo": repo,
            "url": issue_obj.get("url"),
            "title": issue_obj.get("title"),
            "state": issue_obj.get("state"),
            "linked_issues": linked,
        },
        options=options,
    )


def _fetch_paginated_comments(issue, repo, env):
    """Fetch the COMPLETE comment thread via the paginated REST endpoint — ``gh issue view --json
    comments`` caps at ~100 comments and does not paginate, silently truncating a long thread (v1's
    documented reason for using this endpoint instead). Returns the raw REST comment objects (list
    of dicts), not yet normalized to the ``gh issue view`` comment shape, or ``(None, result)`` on
    failure — ``result`` is the failing ``CommandResult`` itself (see
    ``_fetch_issue_with_deps_capability_gate``'s docstring for why: the caller branches on
    ``result.auth_required``).
    """
    result = process.run(
        ["gh", "api", "--paginate", "repos/%s/issues/%s/comments" % (repo, issue)], env=env
    )
    if result.returncode != 0:
        return None, result
    # `gh api --paginate` (without `--slurp`) auto-merges a REST list endpoint's pages into one
    # JSON array in the common case (confirmed empirically against a real multi-page comment
    # thread), but its own docs describe the general contract as "each page is a separate JSON
    # array" — i.e. one-or-more top-level JSON values concatenated on stdout. v1 was robust to
    # either shape by design: `--jq '.[]' | jq -s '.'` unwraps whatever arrays gh prints into a
    # flat object stream, then re-slurps that stream into one array, rather than assuming exactly
    # one array. This loop is the direct Python equivalent — walk the buffer with
    # json.JSONDecoder.raw_decode, flattening every top-level array found (one iteration for
    # today's single-merged-array case, N iterations if gh ever prints N) — so it stays correct
    # under either observed shape instead of encoding an assumption about which one gh uses today.
    decoder = json.JSONDecoder()
    text = result.stdout
    pos = 0
    length = len(text)
    all_comments = []
    while pos < length:
        while pos < length and text[pos] in " \t\r\n":
            pos += 1
        if pos >= length:
            break
        page, pos = decoder.raw_decode(text, pos)
        all_comments.extend(page)
    return all_comments, None


def _normalize_comment(raw):
    """REST comment shape -> the ``gh issue view`` comment shape callers expect (v1's jq
    transform, restated in Python): ``{id, author: {login}, authorAssociation, body, createdAt,
    url}``, plus ``databaseId``.

    ``databaseId`` is additive — the v1 shape above is preserved key-for-key, so an existing reader
    of ``id`` is untouched. It carries the REST **numeric** id the raw payload already holds, under
    GraphQL's own name for it, because ``id`` here is the GraphQL node id and
    ``gh_persist.py comment --delete-marker-id`` deletes over REST
    (``repos/{owner}/{repo}/issues/comments/{id}``), where a node id is always a 404 (#34). A
    consumer that locates a marker by scanning this thread — rather than through the
    ``marker_prefix`` lookup below, which reads the raw objects directly — takes its id from here.
    """
    return {
        "id": raw.get("node_id"),
        "databaseId": raw.get("id"),
        "author": {"login": (raw.get("user") or {}).get("login")},
        "authorAssociation": raw.get("author_association"),
        "body": raw.get("body"),
        "createdAt": raw.get("created_at"),
        "url": raw.get("html_url"),
    }


# The false-positive fix (see module docstring): fetched ONLY to filter, then stripped back off.
# The returned PR objects' field set gains exactly one derived boolean in their place —
# `closes_issue` (see `_filter_and_strip_reference_fields`) — because the raw link list is gone by
# the time any caller sees the item, and `branching.pr_belongs_to_issue` needs the close linkage.
_OPEN_PR_SEARCH_FIELDS = "number,title,author,isDraft,headRefName,url,updatedAt,body,closingIssuesReferences"
_REFERENCE_FILTER_ONLY_FIELDS = ("body", "closingIssuesReferences")


def references_issue(body_text, issue_number, closing_issue_numbers=None):
    """True iff `body_text` contains a genuine `#<issue_number>` reference, or `issue_number` is a
    member of `closing_issue_numbers` (a PR's `closingIssuesReferences` number set — the "matches
    via GitHub's own close-linkage even though the literal `#N` string is absent from the body
    text" case, e.g. a PR whose body says "Closes this" with the link established some other way).

    The `#<N>` match is boundary-guarded on both sides, matching GitHub's own autolink semantics (a
    genuine reference needs a word boundary immediately after the digits, not just "not another
    digit"): `#2` never matches `#20`/`#12` (a longer number sharing the same leading digits),
    never matches `#2abc`/`#2E8B57` (a hex color or any other alphanumeric token glued onto the
    digits — reviewer-reproduced false positive), and is never fooled by a preceding word character
    or another `#` (so an adjacent token like `foo#2` doesn't count). A genuine reference followed
    by whitespace, punctuation, or end-of-string (`"#2 "`, `"#2)"`, `"#2,"`, `"#2"` at EOL) still
    matches. Bare-digit prose with no `#` at all (`"Phase 2"`, `"issue 2"`) never matches — the `#`
    is required, exactly as a real GitHub issue reference always carries one. See the module
    docstring's "Open-PR search false-positive fix" for the live evidence this function closes:
    `--search "<N> in:body"` is a GitHub full-text search that returns any PR whose body merely
    contains the digit `<N>` (a "Phase 2" line, an unrelated "#12"), never a literal-string
    containment check — this function is the actual reference-correctness gate, applied client-side
    after the (necessarily loose) server-side search returns its candidate set.
    """
    issue_number = int(issue_number)
    if closing_issue_numbers:
        if issue_number in {int(n) for n in closing_issue_numbers if n is not None}:
            return True
    pattern = re.compile(r"(?<![\w#])#%d(?![0-9A-Za-z])" % issue_number)
    return bool(pattern.search(body_text or ""))


def _filter_and_strip_reference_fields(items, issue_number):
    """Apply :func:`references_issue` to each item's `body`/`closingIssuesReferences`, keep only
    the genuine matches, strip those two filter-only fields back off, and add ONE derived boolean
    in their place: ``closes_issue`` — whether `issue_number` is a member of the item's
    `closingIssuesReferences` link set.

    `closes_issue` survives the strip because the distinction those raw fields carry is not only
    "does this PR reference the issue" but "is this the issue's OWN PR":
    :func:`branching.pr_belongs_to_issue` needs the close linkage to accept a PR on a branch this
    pipeline did not name, and the raw list is gone by the time any caller sees the item. It is
    derived from GitHub's own resolved link set ONLY, never re-parsed from `Fixes #<N>` prose —
    body text is exactly the loose signal this whole filter exists to distrust, and GitHub has
    already resolved the keyword into the link set. A `gh` build or repo returning no
    `closingIssuesReferences` yields `closes_issue: false` for every item, degrading ownership to
    the head-name arm alone — the same capability-gated degradation shape as the native-relation
    ladders, never an error."""
    issue_number = int(issue_number)
    filtered = []
    for item in items:
        closing_numbers = [c.get("number") for c in (item.get("closingIssuesReferences") or [])]
        if not references_issue(item.get("body"), issue_number, closing_issue_numbers=closing_numbers):
            continue
        kept = {k: v for k, v in item.items() if k not in _REFERENCE_FILTER_ONLY_FIELDS}
        kept["closes_issue"] = issue_number in {int(n) for n in closing_numbers if n is not None}
        filtered.append(kept)
    return filtered


def _fetch_open_prs(issue, repo, env):
    """Returns ``(prs, None)`` on success or ``(None, result)`` on failure — see
    ``_fetch_paginated_comments``'s docstring for the failure-result convention. The raw search
    result is filtered through :func:`references_issue` before being returned (module docstring's
    "Open-PR search false-positive fix") — a PR search hit whose body/closing-links don't actually
    reference `issue` is dropped, never surfaced to a caller as a genuine prior/competing PR."""
    result = process.run(
        [
            "gh", "pr", "list", "--repo", repo, "--state", "open",
            "--search", "%s in:body" % issue,
            "--json", _OPEN_PR_SEARCH_FIELDS,
        ],
        env=env,
    )
    if result.returncode != 0:
        return None, result
    return _filter_and_strip_reference_fields(json.loads(result.stdout), issue), None


def _fetch_extra_json(issue, repo, fields, env):
    """Returns ``(fields, None)`` on success or ``(None, result)`` on failure — see
    ``_fetch_paginated_comments``'s docstring for the failure-result convention."""
    result = process.run(
        ["gh", "issue", "view", str(issue), "--repo", repo, "--json", fields], env=env
    )
    if result.returncode != 0:
        return None, result
    return json.loads(result.stdout), None


def _fail_hard_or_auth(failure, stream):
    """Uniform handling for a fetch helper's failure value (``deps_result``/``err`` in
    :func:`run`): if it's a `gh` call the pipelib runner classified as an auth failure
    (``failure.auth_required``), emit the ``AUTH_REQUIRED`` needs_decision envelope and return
    ``(EXIT_OK, envelope)`` — an auth failure is a decision-worthy outcome (architecture.md §3),
    not an undifferentiated hard failure. Otherwise fall back to :func:`_fail_hard` on
    ``failure.stderr`` exactly as before, returning ``(1, None)``. Every one of the four `gh`
    call sites in :func:`run` routes its failure branch through this one function, so the
    auth-check-before-hard-fail rule can't be forgotten at a site added later.
    """
    if getattr(failure, "auth_required", False):
        envelope = emit_needs_decision(_auth_required_decision(failure), stream=stream)
        return EXIT_OK, envelope
    return _fail_hard(failure.stderr), None


def run(issue, repo, marker_prefix=None, scratch_dir=None, extra_json=None, env=None, stream=None):
    """Do the full gather and return ``(exit_code, envelope_or_none)``. ``envelope_or_none`` is
    ``None`` exactly when ``exit_code`` is a hard failure (1) — no envelope is emitted for those,
    matching v1's ``exit 1`` with no JSON on a real fetch failure — except an ``AUTH_REQUIRED``
    failure, which is exit 0 with a ``needs_decision`` envelope like any other §3 decision (see
    :func:`_fail_hard_or_auth`). Split out from ``main`` so tests can call this in-process too,
    though the DoD's subprocess-driven tests exercise the CLI path directly (the two must agree,
    and this split makes that agreement structural rather than incidental). ``stream`` is
    forwarded to ``pipelib.envelope.emit_*`` (defaults to real stdout); an in-process caller (a
    future prep script composing this module directly, per architecture.md §7 "Prep scripts
    compose them in-process") can pass an ``io.StringIO`` to capture the built envelope without a
    subprocess boundary.
    """
    (
        issue_obj,
        deps_available,
        subissues_available,
        relation_result,
    ) = _fetch_issue_with_relation_capability_gates(issue, repo, env)
    if issue_obj is None:
        return _fail_hard_or_auth(relation_result, stream)
    notices = list(relation_result)

    # PR-number guard, checked before any further round-trip: `gh issue view` silently returns a
    # pull request when the number is a PR's (the live-incident failure mode: prep reported
    # `target.kind: "issue"` and ensured a work worktree for a target that doesn't exist as an
    # issue). A composing prep forwards this decision at its own step 1, structurally before any
    # workspace ensure.
    if _is_pull_request(issue_obj):
        envelope = emit_needs_decision(
            _target_is_pr_decision(issue, repo, issue_obj), notices=notices, stream=stream
        )
        return EXIT_OK, envelope

    raw_comments, err = _fetch_paginated_comments(issue, repo, env)
    if raw_comments is None:
        return _fail_hard_or_auth(err, stream)
    thread = [_normalize_comment(c) for c in raw_comments]
    issue_obj["comments"] = thread

    open_prs, err = _fetch_open_prs(issue, repo, env)
    if open_prs is None:
        return _fail_hard_or_auth(err, stream)

    # Marker (plan) comments come from the SAME complete fetch — no second round-trip. Note the
    # asymmetry with _normalize_comment: a marker's `id` is the REST **numeric** id (`c["id"]`),
    # NOT the GraphQL node id (`c["node_id"]`) the thread's own comment shape uses — v1 encoded
    # this exact split (`{id: .node_id, ...}` for the thread vs `{id: .id, ...}` for markers) and
    # it is load-bearing: `gh-persist.sh`'s `--delete-marker-id` calls `gh api -X DELETE
    # .../comments/<id>`, whose REST endpoint requires the numeric id, not the node id. Getting
    # this backwards would silently break marker-comment deletion on replacement.
    # `updated_at` rides along free: the REST comment objects already carry it, so no extra field
    # selection and no extra round-trip. It is the left-hand side of the planner's rescope
    # comparison ("was this sub-issue edited since the plan was posted?" — #18).
    # `created_at` rides along the same way: when the plan was posted, the board history's
    # plan-posted point (#156).
    if marker_prefix:
        markers = [
            {
                "id": c.get("id"),
                "url": c.get("html_url"),
                "body": c.get("body"),
                "updated_at": c.get("updated_at"),
                "created_at": c.get("created_at"),
            }
            for c in raw_comments
            if (c.get("body") or "").startswith(marker_prefix)
        ]
    else:
        markers = []
    marker_count = len(markers)

    # The implementation plan spans a main comment and its part comments
    # (skills/_shared/plan-parts.md): every caller gets the WHOLE plan as the marker body, so no
    # reader ever needs to know the split exists. `marker_parts` is what a revise edits in place.
    # Parts are reported even with no main comment: a fresh post that failed after its parts went up
    # leaves them orphaned, and the next run must rewrite them in place rather than post duplicates.
    marker_parts = None
    parts_decision = None
    if marker_prefix == plan_parts.MAIN_MARKER:
        assembled, marker_parts, parts_notes, parts_decision = plan_parts.assemble_from_thread(
            (markers[0].get("body") or "") if marker_count == 1 else "", raw_comments
        )
        if marker_count == 1:
            markers[0]["body"] = assembled
            notices.extend(parts_notes)

    blocked_by = (issue_obj.get("blockedBy") or {}).get("nodes") or []
    blocking = (issue_obj.get("blocking") or {}).get("nodes") or []

    # Native parent/sub-issue relation. `parent` is null on an issue with no parent and `sub_issues`
    # is empty on a leaf — both are ordinary states, NOT degradations (that's what
    # `subissues_available: false` means). An epic filed before the relation existed also reads as
    # empty here, which is why hierarchy readers keep the legacy `## Stories` fallback
    # (skills/_shared/epic-story-hierarchy.md).
    parent = issue_obj.get("parent")
    sub_issues = (issue_obj.get("subIssues") or {}).get("nodes") or []
    sub_issues_summary = issue_obj.get("subIssuesSummary") or {}

    payload = {
        "number": issue_obj["number"],
        "title": issue_obj["title"],
        "state": issue_obj["state"],
        "labels": issue_obj["labels"],
        "author": issue_obj["author"],
        "createdAt": issue_obj["createdAt"],
        "updatedAt": issue_obj["updatedAt"],
        "assignees": issue_obj["assignees"],
        "milestone": issue_obj["milestone"],
        "url": issue_obj["url"],
        "thread_comment_count": len(thread),
        "marker_comment_present": marker_count > 0,
        "marker_comment_count": marker_count,
        "deps_available": deps_available,
        "blocked_by": blocked_by,
        "blocking": blocking,
        "subissues_available": subissues_available,
        "parent": parent,
        "sub_issues": sub_issues,
        "sub_issues_summary": sub_issues_summary,
        "open_prs": open_prs,
    }

    if scratch_dir:
        payload["inline_threshold_bytes"] = resolve_threshold(env=env)
        body_fields = spill_bytes(
            (issue_obj.get("body") or "").encode("utf-8"),
            "issue_body",
            scratch_dir,
            env=env,
            filename="issue-%s-body.md" % issue,
        )
        payload.update(body_fields)

        # `thread` is spilled exactly like every other verbatim section (issue_body,
        # marker_comment): the inline bare field is the decoded UTF-8 *text* spill_bytes produces
        # (the JSON-encoded string), NOT re-parsed back into a structured list. This matches
        # gh_pr_gather.py's identical `thread`/`reviews` handling and
        # tests/support/envelope_asserts.assert_spill_sections_well_formed's conformance rule
        # (architecture.md §4: "the bare field carries the content" — a str, decoded UTF-8 text, is
        # the contract for every spill_bytes section uniformly). A consumer reading an inline
        # `thread` field does its own `json.loads` on the string, exactly as it would on a spilled
        # thread_path file's contents — "text in, text out" regardless of mode. (v1's own inline
        # branch re-parsed with `fromjson`; that is NOT carried forward here, since v2's spill
        # contract is uniform across every section, and this was S21 review finding: gh_gather was
        # the one section diverging from gh_pr_gather's already-correct pattern.)
        thread_fields = spill_bytes(
            json.dumps(thread, ensure_ascii=False).encode("utf-8"),
            "thread",
            scratch_dir,
            env=env,
            filename="issue-%s-thread.json" % issue,
        )
        payload.update(thread_fields)

        if marker_count > 0:
            first_marker = markers[0]
            marker_body = first_marker.get("body") or ""
            marker_fields = spill_bytes(
                marker_body.encode("utf-8"),
                "marker_comment",
                scratch_dir,
                env=env,
                filename="issue-%s-marker.md" % issue,
            )
            payload["marker_comment_id"] = first_marker.get("id")
            payload["marker_comment_url"] = first_marker.get("url")
            payload["marker_comment_updated_at"] = first_marker.get("updated_at")
            payload["marker_comment_created_at"] = first_marker.get("created_at")
            payload.update(
                {
                    "marker_comment_bytes": marker_fields["marker_comment_bytes"],
                    "marker_comment_mode": marker_fields["marker_comment_mode"],
                }
            )
            if "marker_comment_path" in marker_fields:
                payload["marker_comment_path"] = marker_fields["marker_comment_path"]
            if "marker_comment" in marker_fields:
                payload["marker_comment_body"] = marker_fields["marker_comment"]
    else:
        # Legacy inline envelope shape (no scratch_dir): the full issue object (with its now
        # complete `.comments`) plus the single first marker or null, matching v1's
        # no-scratch-dir branch field-for-field.
        payload["issue"] = issue_obj
        payload["marker_comment"] = markers[0] if marker_count > 0 else None
        # marker_comment_present isn't part of v1's legacy shape; keep the v2 addition anyway since
        # architecture.md §3 is envelope-first and a bool alongside marker_comment_count costs
        # nothing a v1 legacy caller would trip on (v1 legacy callers don't exist in v2 — no
        # config-driven caller switches shapes at runtime).

    if marker_parts is not None:
        payload["marker_parts"] = marker_parts

    if extra_json:
        extra, err = _fetch_extra_json(issue, repo, extra_json, env)
        if extra is None:
            return _fail_hard_or_auth(err, stream)
        for key, value in extra.items():
            if key in payload:
                continue  # never clobber a base scalar with an extra_json collision
            payload[key] = value

    if parts_decision is not None:
        envelope = emit_needs_decision(parts_decision, notices=notices, stream=stream)
        return EXIT_OK, envelope

    if marker_count > 1:
        decision = needs_decision(
            MARKER_AMBIGUOUS,
            summary="%d marker comments match prefix %r on issue #%s — expected at most one"
            % (marker_count, marker_prefix, issue),
            context={
                "issue": issue,
                "repo": repo,
                "marker_prefix": marker_prefix,
                "marker_comment_ids": [m.get("id") for m in markers],
                "marker_comment_urls": [m.get("url") for m in markers],
            },
            options=[
                "inspect each marker comment and pick the one to treat as current",
                "delete the stale duplicate marker comment(s) then re-run the gather",
            ],
        )
        envelope = emit_needs_decision(decision, notices=notices, stream=stream)
        return EXIT_OK, envelope

    envelope = emit_ok(payload=payload, notices=notices, stream=stream)
    return EXIT_OK, envelope


def main(argv):
    parser = argparse.ArgumentParser(
        description="gh_gather.py — the fixed issue-fetch envelope (GATHER_ISSUE)."
    )
    parser.add_argument("issue", help="issue number")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument(
        "marker_prefix", nargs="?", default="", help="marker comment body prefix to match"
    )
    parser.add_argument(
        "scratch_dir", nargs="?", default="", help="scratch dir for spilled sections (omit for legacy inline envelope)"
    )
    parser.add_argument(
        "--extra-json",
        default=None,
        help="comma-separated extra --json fields to fold into the envelope as scalars",
    )
    args = parser.parse_args(argv)

    exit_code, _envelope = run(
        args.issue,
        args.repo,
        marker_prefix=args.marker_prefix or None,
        scratch_dir=args.scratch_dir or None,
        extra_json=args.extra_json,
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
