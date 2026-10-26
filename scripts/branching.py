"""branching.py — shared branch-naming, type-detection, and prior-PR classification cores.

Import-only module (no shebang, no ``main`` — like ``oq_tracker.py`` it is composed in-process by
the preps, never dispatched). Extracted verbatim from ``prep_resolver.py`` at the v3
workspace-model inversion so that ``prep_workspace_open.py`` (which now owns branch creation) and
``prep_resolver.py`` (which now only *asserts* the ambient branch) derive branch names, issue
types, epic-branch discovery, and prior-PR rows from ONE implementation. ``prep_resolver.py``
keeps module-level aliases to every extracted name (the ``build_oq_query = oq_tracker.build_oq_query``
precedent in ``prep_planner.py``), so its public surface — and the direct-call tests against it —
are unchanged.

Everything here is a pure derivation over ``git ls-remote`` / ``gh`` list queries: no worktree
side effects, no GitHub writes. The behavioral contracts (the 7-row prior-PR table, the 6-step
fresh-slug derivation, `-vN` collision suffixing, epic-branch discovery) are specified in
``docs/specs/resolver.md`` and were not altered by the extraction.
"""

import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gh_gather  # noqa: E402  (in-process composition — the reference-filter fix lives there)
import parse  # noqa: E402  (the `## Stories` checklist scan — the legacy parent edge)
from pipelib import process  # noqa: E402
from pipelib.decisions import AMBIGUOUS, AUTH_REQUIRED, needs_decision  # noqa: E402

# State-vector `type` detection (docs/specs/resolver.md "State-vector derivation: labels -> type";
# the spec's "Epic-branch discovery" row): case-insensitive `epic`/`story` label match, OR a
# title `Epic:` prefix (case-insensitive) for the epic arm — the fresh-slug derivation uses the
# identical prefix test, so the type-detection prefix check mirrors it exactly.
EPIC_TITLE_PREFIX_RE = re.compile(r"^\s*epic\s*:", re.IGNORECASE)

# Epic branch pattern: `epic/<N>-<slug>` (docs/specs/resolver.md "Epic-branch discovery").
EPIC_BRANCH_LS_REMOTE_PATTERN = "epic/%s-*"
EPIC_BRANCH_NAME_RE = re.compile(r"^epic/(\d+)-(.+)$")

# Branch-collision suffixing: `<issue>-<slug>` optionally followed by `-v<N>`
# (docs/specs/resolver.md's "Branch-collision suffixing (`-vN`)" row; unsuffixed counts as v1).
BRANCH_VERSION_SUFFIX_RE = re.compile(r"^-v(\d+)$")


# ---------------------------------------------------------------------------
# git ls-remote — no existing executor core covers this query shape (architecture.md §1 permits
# any script to spawn git/gh directly via pipelib.process.run).
# ---------------------------------------------------------------------------


def list_remote_branches(root, pattern):
    """`git ls-remote --heads origin <pattern>` -> sorted list of bare branch names (the
    `refs/heads/` prefix stripped). Returns `(names, decision_or_none)` — a hard git failure other
    than "no matches" still `sys.exit(1)`s with faithful stderr (§3, unchanged); "no matches" is
    `git ls-remote`'s own exit 0 with empty stdout, not a failure.
    """
    result = process.run(["git", "ls-remote", "--heads", "origin", pattern], cwd=str(root))
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    names = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        ref = parts[-1]
        if ref.startswith("refs/heads/"):
            names.append(ref[len("refs/heads/") :])
    return sorted(names), None


# ---------------------------------------------------------------------------
# State-vector: type detection, prior-PR-row -> mode (docs/specs/resolver.md's 7-row table)
# ---------------------------------------------------------------------------


def detect_type(labels, title):
    """Case-insensitive `epic`/`story` label match, or a title `Epic:` prefix (docs/specs/
    resolver.md "State-vector derivation: labels -> type"). `epic` takes precedence over `story`
    if (pathologically) both labels are present, matching the spec's listed priority order.
    """
    lowered_labels = {(label or "").strip().lower() for label in labels or []}
    if "epic" in lowered_labels or EPIC_TITLE_PREFIX_RE.match(title or ""):
        return "epic"
    if "story" in lowered_labels:
        return "story"
    return "standard"


def detect_type_with_question(labels, title):
    """:func:`detect_type` widened by one arm: a `question` label wins over the `standard`
    fallback but never over `epic`/`story` (a pathologically double-labelled issue keeps the
    core's documented precedence). Promoted here from per-prep copies once the arm reached its
    third consumer (the prep_planner S14-promotion precedent: a literal-same algorithm serving a
    shared vocabulary lives in a shared module, not restated per prep)."""
    core = detect_type(labels, title)
    if core == "standard" and "question" in {
        (label or "").strip().lower() for label in labels or []
    }:
        return "question"
    return core


def fetch_parent_state(parent_number, repo, cwd=None):
    """The typed parent lookup — `gh issue view <parent> --json state,title,labels` — needed
    because the native parent/sub-issue node shape carries **no labels**
    (`epic-story-hierarchy.md` "The facts"), so a parent cannot be typed from a gather alone.
    The type answers the by-construction slice test (parent-not-epic ⇒ the child is a slice).
    Returns ``(state_dict, decision_or_none)`` with the standard `AUTH_REQUIRED` handling.
    Promoted here at its second byte-identical prep consumer (the S14 precedent)."""
    result = process.run(
        ["gh", "issue", "view", str(parent_number), "--repo", repo, "--json", "state,title,labels"],
        cwd=cwd,
    )
    if result.auth_required:
        return None, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    data = json.loads(result.stdout or "{}")
    labels = [label.get("name") for label in data.get("labels") or []]
    return {
        "number": int(parent_number),
        "state": data.get("state"),
        "title": data.get("title"),
        "labels": labels,
        "type": detect_type_with_question(labels, data.get("title") or ""),
    }, None


# The v1 step-5 prior-PR state table (docs/specs/resolver.md "Fresh/continue mode from the
# prior-PR state table" — its seven rows), carried as the row name -> mode mapping. See
# prep_resolver.py's vector assembly for the mode semantics (`continue` / `gated` / `fresh`) and
# why a gated row is still an `ok` envelope (a flow gate the router raises, not a prep failure).
PRIOR_PR_ROW_OPEN_YOURS = "open-pr-yours"
PRIOR_PR_ROW_OPEN_OTHER_ACTIVE = "open-pr-other-active"
PRIOR_PR_ROW_OPEN_OTHER_STALE = "open-pr-other-stale"
PRIOR_PR_ROW_DRAFT = "draft"
PRIOR_PR_ROW_CLOSED_RESOLVED = "closed-resolved"
PRIOR_PR_ROW_CLOSED_NOT_RESOLVED = "closed-not-resolved"
PRIOR_PR_ROW_NONE = "no-prior-pr"

MODE_CONTINUE = "continue"
MODE_GATED = "gated"
MODE_FRESH = "fresh"

CONTINUE_ROWS = frozenset({PRIOR_PR_ROW_OPEN_YOURS, PRIOR_PR_ROW_DRAFT})
GATED_ROWS = frozenset({PRIOR_PR_ROW_OPEN_OTHER_ACTIVE, PRIOR_PR_ROW_OPEN_OTHER_STALE})

# Per-gated-row AskUserQuestion shape the resolver's router renders (the exact headers/options in
# docs/specs/resolver.md "Operator gates"), carried as a fact so the router never re-derives which
# card goes with which row. A foreign draft reuses whichever of the two "someone else" rows its
# activity classifies as (see classify_prior_pr_row) — there is no third, draft-specific card.
GATE_CARDS = {
    PRIOR_PR_ROW_OPEN_OTHER_ACTIVE: {
        "header": "Open PR",
        "options": ["Review it", "Leave a comment", "Wait"],
    },
    PRIOR_PR_ROW_OPEN_OTHER_STALE: {
        "header": "Stale PR",
        "options": ["Take it over", "Start fresh"],
    },
}

# "Stale" activity threshold in days. The spec row's text is deliberately unquantified ("no recent
# activity ... for a long time") — this is a CHOSEN deterministic default needed to classify
# open-other PRs at all, not a value derived from any other doc in this repo. Surfaced as
# `prior_pr.stale_cutoff_days` (see classify_prior_pr_row) so the operator/router can see the
# exact driver rather than trust an opaque classification. Active vs stale changes ONLY which gate
# card is shown (GATE_CARDS) — both rows are `mode: "gated"`.
STALE_ACTIVITY_DAYS = 14


def search_closed_prs(repo, issue_number, cwd=None, fatal=True):
    """Targeted closed/merged-PR search referencing this issue (docs/specs/resolver.md step-5
    table's two "closed" rows; mirrors the spec's own predecessor-PR-detection search convention:
    `gh pr list --state closed --search "<N> in:body"`). Returns `(prs, decision_or_none)`; each
    PR dict carries `number`/`state`/`mergedAt` (`state == "MERGED"` classifies resolved, since a
    merged PR referencing the issue closed it — v1's "closed PR that resolved the issue" row).

    The raw search result is filtered through `gh_gather.references_issue` (via
    `_filter_and_strip_reference_fields`) — `--search "<N> in:body"` is a GitHub full-text search,
    not a literal-string containment check; see `gh_gather.py`'s module docstring "Open-PR search
    false-positive fix" for the evidence.

    The returned list is **mention-scoped, not ownership-scoped** — a sibling's PR that genuinely
    references `#<N>` survives on purpose, because `prep_workspace_close.py`'s issue-to-branch
    ladder needs to *report* the heads it rejected. Narrowing to the issue's own PRs is
    :func:`prior_prs_for_issue`'s job, applied by :func:`classify_prior_pr_row`.

    ``fatal=False`` turns every failure — auth included — into ``(None, None)`` for a caller whose
    use of the result is a convenience fact (`prep_planner.py`'s closed-PR attention line), where
    a lookup that cannot be answered must degrade, never stop the run.
    """
    result = process.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "closed",
            "--search",
            "%s in:body" % issue_number,
            "--json",
            "number,title,author,state,mergedAt,headRefName,headRefOid,url,updatedAt,body,closingIssuesReferences",
        ],
        cwd=cwd,
    )
    if not fatal:
        try:
            items = json.loads(result.stdout) if result.returncode == 0 else None
        except ValueError:
            items = None
        if items is None:
            return None, None
        return gh_gather._filter_and_strip_reference_fields(items, issue_number), None
    if result.auth_required:
        return None, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    return gh_gather._filter_and_strip_reference_fields(json.loads(result.stdout), issue_number), None


def pr_belongs_to_issue(pr, issue_number):
    """True iff `pr` is one of issue `issue_number`'s OWN prior/competing PRs, rather than a PR
    that merely *mentions* it. Two independent pieces of evidence, either sufficient:

    1. its head branch is one this pipeline would have minted for the issue
       (:func:`branch_belongs_to_issue` — `<N>-<slug>`, `-vN`-suffixed, or `epic/<N>-<slug>`), or
    2. it CLOSES the issue — `closes_issue`, derived by
       `gh_gather._filter_and_strip_reference_fields` from GitHub's own `closingIssuesReferences`
       link set.

    Both arms are required. Head-name alone would regress every branch this pipeline ADOPTED but
    did not name — a `gh issue develop` linked branch taken verbatim (`prep_workspace_open.py`
    adopts "whatever this run would have named"), a hand-named branch, a fork branch — from
    `continue` to `fresh`, minting a duplicate branch and a duplicate PR over live work.
    Closes-link alone would miss a PR opened before its closing keyword was written, and degrades
    to nothing on a `gh` build that returns no `closingIssuesReferences`. Neither arm scans body
    text: a `#<N>` in a body is exactly the mention signal this function exists to reject.

    The rejected case is the one it was written for: an epic integration PR carries `Fixes #<epic>`
    and lists every story by number, so the `#<N> in:body` search surfaces it once per story — and
    adopting it made a story classify `continue` onto the shared `epic/<N>-<slug>` branch, silently
    demoting the story to a deliverable slice (`skills/_shared/epic-story-hierarchy.md`:
    own-branch-and-PR is the ONE parameter separating the two). Its head belongs to the epic and
    its close link names the epic, so both arms reject it for the story while both still accept it
    for the epic itself — this is issue-scoped, not a blanket exclusion of epic PRs.

    Known residual, accepted: a PR that genuinely IS this issue's but sits on a branch neither
    pipeline-named nor close-linked (an external contributor's fork branch with no closing keyword)
    now reads as `fresh`. Every PR this pipeline opens carries `Fixes #<N>`, and workspace-open's
    linked-branch rung adopts before minting, so pipeline-created PRs always satisfy an arm; the
    residual is strictly narrower than the epic-story breakage this closes.
    """
    if branch_belongs_to_issue(pr.get("headRefName"), issue_number):
        return True
    return bool(pr.get("closes_issue"))


def partition_prs_for_issue(prs, issue_number):
    """Split `prs` into ``(own, mentions_only)`` by :func:`pr_belongs_to_issue`, order preserved
    within each half; `None` splits to ``([], [])``. `mentions_only` exists ONLY as a diagnostic
    (the preps' `prior_pr_rejected` fact) — it is never an `attention` entry and nothing gates on
    it, because an epic listing 11 stories would otherwise put an unactionable line in 11 sessions.
    """
    own, mentions_only = [], []
    for pr in prs or []:
        (own if pr_belongs_to_issue(pr, issue_number) else mentions_only).append(pr)
    return own, mentions_only


def prior_prs_for_issue(prs, issue_number):
    """`prs` narrowed to the issue's own PRs (:func:`partition_prs_for_issue`'s first half).

    Callers must narrow with THIS function *before* their `if not open_prs:` test, not rely on
    :func:`classify_prior_pr_row` narrowing internally: the closed-PR search is gated on that
    emptiness test, so an issue whose only open PR is a mention would otherwise skip the closed
    search entirely and report `no-prior-pr` over a real closed PR. Idempotent — the predicate is
    per-item and reads only fields the filter keeps — so the classifier re-applying it is a no-op,
    which is what lets the invariant live at the classifier regardless of caller discipline.
    """
    return partition_prs_for_issue(prs, issue_number)[0]


def classify_prior_pr_row(open_prs, current_user, closed_prs, issue_state, issue_number):
    """Classify the issue's prior-PR state into exactly one of the 7 named rows, returning
    ``(row_name, prior_pr_fact_or_none)``. ``open_prs`` is `gh_gather`'s `open_prs` list (from the
    `gh pr list ... "<N> in:body"` search); ``closed_prs`` is :func:`search_closed_prs`'s result
    (only consulted when no open PR exists); ``issue_state`` (`OPEN`/`CLOSED`) disambiguates the
    two closed-PR rows exactly as docs/specs/resolver.md's table does ("closed PR that resolved
    the issue" implies the issue itself is closed; a merged PR against a still-open issue is the
    partial-fix/abandoned case).

    BOTH candidate lists are first narrowed by :func:`prior_prs_for_issue` — the two searches
    behind them are `#<N> in:body` MENTION searches, and a genuine mention by a sibling's PR is
    still not evidence about this issue. The open arm is where an epic integration PR (which lists
    all its stories by number) made every one of its stories classify `continue` onto the epic's
    own branch; the closed arm is the same defect one merge later, where a merged epic PR yields a
    spurious `closed-not-resolved` row and its "did not resolve it" attention line on every story.
    Dropped mentions are SILENT here — the callers surface them as a `prior_pr_rejected` diagnostic
    fact, never as `attention` or a notice.

    Authorship is decided BEFORE draft state: the prior-PR table's "Draft PR" row explicitly
    scopes to "the same author" — it is not a draft-vs-ready split independent of who owns the PR.
    So an open PR by someone else classifies via :func:`classify_open_other_activity` regardless
    of its draft state; only YOUR own open PR can ever land on `open-pr-yours` or `draft`.
    """
    open_prs = prior_prs_for_issue(open_prs, issue_number)
    if open_prs:
        # Prefer a PR by the current user (matches v1's per-row priority: "yours" outranks
        # "other's" when the caller happens to own more than one referencing PR).
        yours = [pr for pr in open_prs if (pr.get("author") or {}).get("login") == current_user]
        chosen = yours[0] if yours else open_prs[0]
        if yours:
            row = PRIOR_PR_ROW_DRAFT if chosen.get("isDraft") else PRIOR_PR_ROW_OPEN_YOURS
        else:
            # Someone else's PR — draft or not, it's still claimed work someone else owns; a
            # foreign draft therefore gates exactly like a foreign ready PR, classified by the
            # same activity check.
            row = classify_open_other_activity(chosen)
        fact = {
            "number": chosen.get("number"),
            "author": (chosen.get("author") or {}).get("login"),
            "isDraft": chosen.get("isDraft"),
            "headRefName": chosen.get("headRefName"),
            "updatedAt": chosen.get("updatedAt"),
            "url": chosen.get("url"),
            "stale_cutoff_days": STALE_ACTIVITY_DAYS,
        }
        return row, fact

    closed_prs = prior_prs_for_issue(closed_prs, issue_number)
    if closed_prs:
        chosen = closed_prs[0]
        merged = (chosen.get("state") or "").upper() == "MERGED" or bool(chosen.get("mergedAt"))
        # "Resolved" per the spec's row split: a merged PR against an issue GitHub itself now
        # shows closed is the "closed PR that resolved the issue" row; anything else (merged PR
        # but the issue is still open, or a closed-without-merge/abandoned PR) is the
        # "closed/merged PR that did NOT resolve the issue" row (partial fix, reverted, abandoned).
        resolved = merged and (issue_state or "").upper() == "CLOSED"
        row = PRIOR_PR_ROW_CLOSED_RESOLVED if resolved else PRIOR_PR_ROW_CLOSED_NOT_RESOLVED
        fact = {
            "number": chosen.get("number"),
            "author": (chosen.get("author") or {}).get("login"),
            "headRefName": chosen.get("headRefName"),
            "merged": merged,
            "resolved": resolved,
            "url": chosen.get("url"),
        }
        return row, fact

    return PRIOR_PR_ROW_NONE, None


def classify_open_other_activity(pr):
    """Active vs stale for an open PR authored by someone else, including a foreign draft
    (docs/specs/resolver.md step-5 table's two "someone else" rows) — `updatedAt` age vs
    :data:`STALE_ACTIVITY_DAYS` (a chosen default, not a derived one — see that constant's
    comment). Missing `updatedAt` defaults to "active" (never silently downgrade a PR to stale
    on absent data). Both outcomes are `mode: "gated"` (see `GATED_ROWS`) — this only decides
    WHICH `AskUserQuestion` card the router shows (`GATE_CARDS`), never whether the row gates."""
    updated_at = pr.get("updatedAt")
    if not updated_at:
        return PRIOR_PR_ROW_OPEN_OTHER_ACTIVE
    try:
        updated = datetime.datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return PRIOR_PR_ROW_OPEN_OTHER_ACTIVE
    age = datetime.datetime.now(datetime.timezone.utc) - updated
    if age.days >= STALE_ACTIVITY_DAYS:
        return PRIOR_PR_ROW_OPEN_OTHER_STALE
    return PRIOR_PR_ROW_OPEN_OTHER_ACTIVE


# ---------------------------------------------------------------------------
# Fresh-slug computation (bootstrap only) — the spec's 6-step derivation, verbatim.
# ---------------------------------------------------------------------------

SLUG_STRIP_EPIC_PREFIX_RE = re.compile(r"^\s*epic\s*:\s*", re.IGNORECASE)
SLUG_NON_ALNUM_RUN_RE = re.compile(r"[^a-z0-9]+")
SLUG_MAX_LENGTH = 50


def compute_fresh_slug(title):
    """The 6-step epic-title -> slug derivation (docs/specs/resolver.md "Fresh-slug computation
    (bootstrap only)"):
    strip an `Epic:` prefix, lowercase, replace non-`[a-z0-9]` runs with `-`, strip leading/
    trailing `-`, truncate to <=50 chars on a `-` boundary. Used ONLY on the bootstrap path (zero
    `git ls-remote` matches) — an existing branch's slug is always taken from the discovered
    branch name verbatim, never recomputed (the spec's "Discover the epic branch slug by prefix;
    only compute fresh on bootstrap" invariant).
    """
    stripped = SLUG_STRIP_EPIC_PREFIX_RE.sub("", title or "").strip()
    lowered = stripped.lower()
    collapsed = SLUG_NON_ALNUM_RUN_RE.sub("-", lowered)
    trimmed = collapsed.strip("-")
    if len(trimmed) <= SLUG_MAX_LENGTH:
        return trimmed
    truncated = trimmed[:SLUG_MAX_LENGTH]
    # "if the truncation would land mid-word ... keep truncating back to the previous '-'."
    if len(trimmed) > SLUG_MAX_LENGTH and trimmed[SLUG_MAX_LENGTH] not in ("-",):
        last_dash = truncated.rfind("-")
        if last_dash != -1:
            truncated = truncated[:last_dash]
    return truncated.rstrip("-")


# ---------------------------------------------------------------------------
# Epic-branch discovery (zero/one/multiple) + story parent-epic search
# ---------------------------------------------------------------------------


def discover_epic_branch(root, epic_number, epic_title):
    """Discover `epic/<epic_number>-*` on origin (docs/specs/resolver.md "Epic-branch discovery").
    Returns `(facts_dict, decision_or_none)`:
      - zero matches -> `{"match_count": 0, "branch": None, "bootstrap_slug": <computed>}`.
      - one match -> `{"match_count": 1, "branch": <name>}`.
      - multiple matches -> `(None, AMBIGUOUS decision)` (context lists every candidate).
    """
    matches, _ = list_remote_branches(root, EPIC_BRANCH_LS_REMOTE_PATTERN % epic_number)
    if len(matches) == 0:
        return {
            "match_count": 0,
            "branch": None,
            "bootstrap_slug": compute_fresh_slug(epic_title),
        }, None
    if len(matches) == 1:
        return {"match_count": 1, "branch": matches[0]}, None
    return None, needs_decision(
        AMBIGUOUS,
        summary="%d candidate epic branches match 'epic/%s-*' on origin — expected at most one"
        % (len(matches), epic_number),
        context={"epic_number": epic_number, "candidates": matches},
        options=[
            "pick the canonical branch and re-run with it recorded",
            "delete or rename the orphaned/duplicate branch, then re-run",
        ],
    )


# Non-blocking notice (open notice vocabulary, architecture.md §3): the commits-ahead count could
# not be read — no network on a fresh clone, a remote-tracking ref that does not exist locally.
# The caller degrades to "unknown" and declines to open a PR rather than attempting a `create-pr`
# that `gh` would reject.
COMMITS_AHEAD_UNAVAILABLE = "COMMITS_AHEAD_UNAVAILABLE"


def count_commits_ahead(root, branch, base_ref):
    """How many commits `origin/<branch>` carries that `origin/<base_ref>` does not, as
    `(count, notice_or_none)` — `(None, COMMITS_AHEAD_UNAVAILABLE)` when the count could not be
    read even after one fetch attempt.

    Counted **remote-vs-remote**, never `origin/<base>..HEAD`: `gh pr create` compares the
    published refs and refuses a PR with no commits between base and head, so a count that
    included the local worktree's unpushed commits would green-light exactly the create that
    fails. Remote-vs-remote is also what keeps this fact available under `--refresh`, where no
    workspace is asserted.
    """
    def _count():
        return process.run(
            ["git", "rev-list", "--count", "origin/%s..origin/%s" % (base_ref, branch)],
            cwd=str(root),
        )

    result = _count()
    if result.returncode != 0:
        # One fetch, then retry: a fresh clone or a worktree that has never fetched the epic
        # branch has no remote-tracking ref to count against yet.
        process.run(["git", "fetch", "--quiet", "origin", branch, base_ref], cwd=str(root))
        result = _count()
    if result.returncode != 0:
        return None, COMMITS_AHEAD_UNAVAILABLE
    try:
        return int(result.stdout.strip()), None
    except ValueError:
        return None, COMMITS_AHEAD_UNAVAILABLE


# Non-blocking notice (open notice vocabulary, architecture.md §3): the PRs merged into the epic
# branch could not be listed. The caller reports no delivered set, so every integration-range file
# the epic plan does not name raises the review level, as it did before #149: the strict outcome.
EPIC_DELIVERED_UNAVAILABLE = "EPIC_DELIVERED_UNAVAILABLE"

# The merged-PR listing's limit, the evaluator's `_STORY_PR_LIST_LIMIT` value.
DELIVERED_PR_LIST_LIMIT = 500
# A full commit id (SHA-1 or SHA-256 object format). Anything else from the listing is never handed to
# git, where a leading `-` would read as an option.
_COMMIT_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def delivered_paths(root, repo, branch, cwd=None):
    """The files the PRs merged into `branch` changed, as ``(fact, None)`` — or
    ``(None, EPIC_DELIVERED_UNAVAILABLE)`` when the merged PRs could not be listed (#149). The fact is
    ``{"prs": [{"number", "merge_commit", "paths"}], "paths": <sorted union>, "unreadable": [<PR
    numbers>], "at_limit": <bool>}``.

    The unit is a PR **merged into the branch**, not a story PR: every PR based on the epic branch went
    through its own review loop and evaluator gate, which is what lets the resolver's epic-integration
    pass count its files as planned (`skills/resolver/references/review-level.md` §1). The closing
    reference is not consulted.

    Paths come from git over each PR's merge commit, ``git diff --name-only --no-renames -z <oid>^1
    <oid>`` at `root`, not from `gh`'s `files` field, whose connection is paged so a large PR would come
    back short. The flags are :func:`sensitive_paths.changed_paths`' own, so a path is spelled the same
    on both sides (without ``-z`` git quotes a non-ASCII path). It reads a squash commit and a merge
    commit alike. A merge commit missing locally gets one ``git fetch --quiet origin <branch>`` for the
    whole call, then a retry.

    Every fallback is strict: a PR with no ``mergeCommit`` (or one that is not a full hex commit id), whose commit cannot be read even after the
    fetch, or whose output is not UTF-8, lands in ``unreadable`` and contributes no paths, so its files
    still raise. Never a decision.
    """
    result = process.run(
        [
            "gh", "pr", "list",
            "--repo", repo,
            "--base", branch,
            "--state", "merged",
            "--limit", str(DELIVERED_PR_LIST_LIMIT),
            "--json", "number,mergeCommit",
        ],
        cwd=cwd,
    )
    if result.returncode != 0:
        return None, EPIC_DELIVERED_UNAVAILABLE
    try:
        listed = json.loads(result.stdout)
    except ValueError:
        return None, EPIC_DELIVERED_UNAVAILABLE
    if not isinstance(listed, list):
        return None, EPIC_DELIVERED_UNAVAILABLE

    def _diff(oid):
        return process.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", "%s^1" % oid, oid], cwd=str(root)
        )

    fetched = False
    prs, union, unreadable = [], set(), []
    for pr in sorted((p for p in listed if isinstance(p, dict)), key=lambda p: p.get("number") or 0):
        number = pr.get("number")
        commit = pr.get("mergeCommit")
        oid = commit.get("oid") if isinstance(commit, dict) else None
        if not isinstance(oid, str) or not _COMMIT_OID_RE.fullmatch(oid):
            unreadable.append(number)
            continue
        try:
            diff = _diff(oid)
            if diff.returncode != 0 and not fetched:
                fetched = True
                process.run(["git", "fetch", "--quiet", "origin", branch], cwd=str(root))
                diff = _diff(oid)
        except UnicodeDecodeError:
            unreadable.append(number)
            continue
        if diff.returncode != 0:
            unreadable.append(number)
            continue
        paths = [path for path in diff.stdout.split("\0") if path]
        prs.append({"number": number, "merge_commit": oid, "paths": paths})
        union.update(paths)
    return {
        "prs": prs,
        "paths": sorted(union),
        "unreadable": unreadable,
        "at_limit": len(listed) >= DELIVERED_PR_LIST_LIMIT,
    }, None


PARENT_EPIC_SEARCH_LIMIT = 1000


def search_parent_epic(repo, story_number, native_parent=None, cwd=None):
    """Story parent-epic lookup. Returns `(matches, decision_or_none)` where `matches` is a
    `gh issue list`-shaped result list (empty on zero genuine matches).

    Two tiers, per `skills/_shared/epic-story-hierarchy.md`:

    1. **`native_parent`** — the `parent` node the caller's gather already carried. Exact and
       single-valued by construction, so it returns immediately: no round-trip, and the `AMBIGUOUS`
       decision below is unreachable (an issue has at most one parent, where a full-text search can
       match many epics).
    2. **The legacy checklist tier** — for a story filed before the native relation was written.
       `gh issue list --label epic --state all --search '#<N> in:body'` only gathers candidates; an
       epic is the parent when its `## Stories` checklist carries a filed bullet for `#<N>`
       (`parse.stories_checklist_lists` — the `checklist` edge of
       `skills/_shared/epic-story-hierarchy.md`). Until 4.33.1 any genuine `#<N>` reference passed
       (`gh_gather.references_issue`), so an epic that only said "related: #N" or "blocked by #N"
       became the story's parent — the wrong `epic/<N>-<slug>` base for planner, resolver and
       workspace-open alike, or a spurious `AMBIGUOUS` beside the real parent. The membership test
       runs on the raw items, because `_filter_and_strip_reference_fields` strips `body`; that
       filter still runs after it for the result's shape (`closes_issue`, no `body`).
    """
    if native_parent:
        return [
            {
                "number": native_parent.get("number"),
                "title": native_parent.get("title"),
                "state": native_parent.get("state"),
            }
        ], None

    result = process.run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            repo,
            "--label",
            "epic",
            "--state",
            "all",
            "--search",
            "#%s in:body" % story_number,
            "--json",
            "number,title,state,body",
            # Membership is filtered client-side, so a capped candidate set could drop the real
            # parent behind mention-only epics; gh's default cap is 30.
            "--limit",
            str(PARENT_EPIC_SEARCH_LIMIT),
        ],
        cwd=cwd,
    )
    if result.auth_required:
        return None, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    members = [
        item
        for item in json.loads(result.stdout)
        if parse.stories_checklist_lists(item.get("body"), story_number)
    ]
    matches = gh_gather._filter_and_strip_reference_fields(members, story_number)
    if len(matches) <= 1:
        return matches, None
    return None, needs_decision(
        AMBIGUOUS,
        summary="%d epics list story #%s in their `## Stories` checklist — expected at most one"
        % (len(matches), story_number),
        context={"story_number": story_number, "candidates": matches},
        options=[
            "pick the canonical parent epic and re-run",
            "remove the duplicate checklist bullet so only one epic lists this story",
        ],
    )


# Non-blocking notice (open notice vocabulary, architecture.md §3): the target's native parent was
# consulted and found, but no `epic/<parent>-*` integration branch exists on origin, so the caller
# based on the default branch instead. Named for what the lookup actually established — a parent
# with no integration branch — NOT "parent epic": the `parent` node carries no labels, so at this
# point an epic whose branch is not yet open and a *story* parent (which makes the target a
# deliverable slice, `skills/_shared/epic-story-hierarchy.md`) are indistinguishable. A notice that
# named the parent an epic would be the same silent misreport as the `epic: null` this replaces.
PARENT_HAS_NO_INTEGRATION_BRANCH = "PARENT_HAS_NO_INTEGRATION_BRANCH"

# Non-blocking notice (open notice vocabulary, architecture.md §3): the native parent was found but
# is CLOSED, so its branch is not a base — the caller uses the default branch. Distinct from the
# notice above because the receipts differ: nothing to open, versus a branch that may yet be opened.
PARENT_CLOSED = "PARENT_CLOSED"


# Non-blocking notice (open notice vocabulary, architecture.md §3): the parent's own labels/title
# could not be read, so whether it is an epic (this target is a story) or a non-epic (this target is
# a deliverable slice) is UNKNOWN. Deliberately non-fatal and deliberately not a `TARGET_IS_SLICE`
# decision: a convenience classification must never block a session that files fine today, and
# refusing on an unread parent would refuse on every offline run.
PARENT_KIND_UNAVAILABLE = "PARENT_KIND_UNAVAILABLE"

PARENT_KIND_EPIC = "epic"
PARENT_KIND_NON_EPIC = "non-epic"
PARENT_KIND_UNKNOWN = "unknown"


def classify_parent(repo, parent_number, cwd=None):
    """Is this issue's parent an epic? Returns ``(kind, notice_or_none)`` where `kind` is
    `PARENT_KIND_EPIC` / `PARENT_KIND_NON_EPIC` / `PARENT_KIND_UNKNOWN`.

    The `parent` node in a gather envelope carries `number`/`title`/`state`/`url` and **no labels**,
    so the relation alone cannot say which of the hierarchy's two edges it is. That matters because
    the two edges mean opposite things for the child: an epic's sub-issue is a **story** (own branch
    and PR), a non-epic's sub-issue is a **deliverable slice** (neither — it ships as a phase on its
    parent's branch). `skills/_shared/epic-story-hierarchy.md` calls this identification "by
    construction": the parent's own type IS the answer, and one `--json labels,title` read gets it.

    Costs one `gh` round-trip, so callers ask only when the cheap oracle already failed — an
    `epic/<parent>-*` branch on origin proves epic-ness for free, and `resolve_parent_epic_branch`
    calls this only when there is no such branch.

    Any failure degrades to `(PARENT_KIND_UNKNOWN, PARENT_KIND_UNAVAILABLE)` — including auth,
    which is NOT escalated to a decision here (`find_merged_pr_for_head`'s precedent): every caller
    has already completed an authenticated gather by this point, so a failure here is a lookup
    problem, not a session-blocking one.
    """
    result = process.run(
        [
            "gh", "issue", "view", str(parent_number),
            "--repo", repo, "--json", "labels,title",
        ],
        cwd=cwd,
    )
    if result.returncode != 0:
        return PARENT_KIND_UNKNOWN, PARENT_KIND_UNAVAILABLE
    try:
        parent_obj = json.loads(result.stdout)
    except ValueError:
        return PARENT_KIND_UNKNOWN, PARENT_KIND_UNAVAILABLE
    labels = [label.get("name") for label in parent_obj.get("labels") or []]
    kind = detect_type(labels, parent_obj.get("title") or "")
    return (PARENT_KIND_EPIC if kind == "epic" else PARENT_KIND_NON_EPIC), None


def resolve_parent_epic_branch(
    root, repo, issue_number, issue_type, native_parent, cwd=None, classify=False
):
    """The hierarchy lookup every base/ref selection keys off. Returns
    ``(facts_or_none, notices, decision_or_none)``; `facts` is
    ``{"parent_epic": <node|None>, "branch_facts": <discover_epic_branch facts|None>}``, and
    ``facts["branch_facts"]["branch"]`` — when present — is the integration branch to base on.

    **The lookup is gated on having a parent, never on the target's lexical type** (#31).
    `detect_type` matches an `epic`/`story` label or an `Epic:` title prefix, so a sub-issue
    labelled by *kind of work* (`bug`, `tech-debt`, `follow-up`) reads as `standard` — while
    carrying the very same native parent edge as its `story`-labelled siblings. Gating the lookup
    on the label used a proxy for a fact the gather envelope already carries exactly, and failed
    silently: the parent was fetched, then never consulted, and the receipt said `epic: null`
    ("no parent") where the truth was "never asked".

    ``issue_type`` still selects the **tier set**, which is the one thing it legitimately answers:

    - `native_parent` present -> tier 1 only. Exact, single-valued, already in hand, no round-trip.
    - absent + `issue_type == "story"` -> the legacy `## Stories` checklist tier (candidates from a
      `#<N> in:body` search, kept only when the epic's checklist lists the story), for a story filed
      before the relation was written (`skills/_shared/epic-story-hierarchy.md`).
    - absent + any other type -> **no lookup**. The legacy tier costs a `gh` round-trip on every
      standard issue and matches loosely, so an untyped target gets the exact native answer or
      nothing — never a full-text guess at a hierarchy it probably has no place in.

    ``classify`` opts into the extra `--json labels,title` read of the parent (`classify_parent`)
    that distinguishes an epic from a *story* parent — i.e. a story target from a deliverable slice.
    It costs a `gh` round-trip and only ever runs when the free oracle came up short (no
    `epic/<parent>-*` branch on origin), so it is **off by default**: a caller that has no refusal
    to make from the answer should not pay for it. `prep_workspace_open.py` passes `True` because
    it can refuse (`TARGET_IS_SLICE`); the resolver does not, and does not need to — a slice with no
    workspace opened for it fails the resolver's own attach assertion first.

    Returns ``(None, notices, decision)`` on a forwarded decision, matching the executor-core
    contract (architecture.md §3).
    """
    notices = []
    if not native_parent and issue_type != "story":
        return None, notices, None

    matches, decision = search_parent_epic(
        repo, issue_number, native_parent=native_parent, cwd=cwd
    )
    if decision is not None:
        return None, notices, decision
    if len(matches) != 1:
        return (
            {"parent_epic": None, "branch_facts": None, "parent_kind": None},
            notices,
            None,
        )

    parent = matches[0]
    if (parent.get("state") or "").upper() != "OPEN":
        notices.append(PARENT_CLOSED)
        # A closed parent has no branch to prove epic-ness with, and its KIND is still load-bearing:
        # a closed story's sub-issue is a slice just as much as an open one's.
        parent_kind = None
        if classify:
            parent_kind, kind_notice = classify_parent(repo, parent["number"], cwd=cwd)
            if kind_notice:
                notices.append(kind_notice)
        return (
            {"parent_epic": parent, "branch_facts": None, "parent_kind": parent_kind},
            notices,
            None,
        )

    branch_facts, branch_decision = discover_epic_branch(
        root, parent["number"], parent.get("title") or ""
    )
    if branch_decision is not None:
        return None, notices, branch_decision
    if branch_facts.get("branch"):
        # An `epic/<parent>-*` branch on origin proves the parent is an epic — no fetch needed.
        parent_kind = PARENT_KIND_EPIC
    else:
        notices.append(PARENT_HAS_NO_INTEGRATION_BRANCH)
        parent_kind = None
        if classify:
            parent_kind, kind_notice = classify_parent(repo, parent["number"], cwd=cwd)
            if kind_notice:
                notices.append(kind_notice)
    return (
        {"parent_epic": parent, "branch_facts": branch_facts, "parent_kind": parent_kind},
        notices,
        None,
    )


# ---------------------------------------------------------------------------
# GitHub-linked branches (`gh issue develop`) — the native "create a branch for this issue"
# association. prep_workspace_open creates/adopts them; prep_resolver's expected-branch ladder
# reads them (linked-first, so an already-opened workspace is recognized instead of re-derived).
# ---------------------------------------------------------------------------

# Non-blocking notice (open notice vocabulary, architecture.md §3): `gh issue develop` failed for
# capability/permission/format reasons — the caller degrades (workspace-open falls back to a
# local branch; the resolver ladder skips the linked rung), never crashes.
ISSUE_LINK_UNSUPPORTED = "ISSUE_LINK_UNSUPPORTED"


def list_linked_branches(repo, issue_number, cwd=None):
    """List the GitHub-linked branches for an issue (`gh issue develop --list <N> --repo <r>`).
    Returns ``(branches_or_none, notice_or_none, decision_or_none)``:

    - success -> ``([<bare branch name>, ...], None, None)`` (possibly empty).
    - auth failure -> ``(None, None, AUTH_REQUIRED decision)``.
    - any other non-zero exit -> ``(None, ISSUE_LINK_UNSUPPORTED, None)`` — the capability/
      permission/older-gh degradation; the exit code of `--list` on an issue with zero linked
      branches is not contractually stable across gh versions, so a caller treats this notice as
      "linking unavailable", never as an error.

    Output is a text table (not JSON — `gh issue develop --list` has no `--json`): one line per
    linked branch, first whitespace/tab-separated field = the branch name; parsed defensively so
    a format drift degrades to the notice path, never a crash.
    """
    result = process.run(
        ["gh", "issue", "develop", "--list", str(issue_number), "--repo", repo], cwd=cwd
    )
    if result.auth_required:
        return None, None, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        return None, ISSUE_LINK_UNSUPPORTED, None
    branches = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        first_field = line.split("\t")[0].split()[0].strip()
        if first_field:
            branches.append(first_field)
    return branches, None, None


# ---------------------------------------------------------------------------
# Head-scoped merged-PR lookup (`gh pr list --head`) — the precise counterpart to
# `search_closed_prs`. That search is a `#<N> in:body` full-text query answering "which PRs mention
# this issue"; this one answers "did THIS branch's PR merge", which is an exact `--head` match with
# no reference filtering involved. prep_workspace_close needs the second question: the merged head
# OID is what lets a clean post-merge worktree be removed after squash-merge +
# delete-branch-on-merge (see `workspace._build_remove_work`'s `merged_pr`).
# ---------------------------------------------------------------------------

# Non-blocking notice (open notice vocabulary, architecture.md §3): the head-scoped merged-PR
# lookup could not run — no auth, no network, an older `gh`. The caller degrades to "no merged PR
# known" and keeps its generic gate, so workspace-close still works offline as the reclamation path
# for an abandoned workspace; an auth failure here is deliberately NOT the AUTH_REQUIRED decision.
MERGED_PR_LOOKUP_UNAVAILABLE = "MERGED_PR_LOOKUP_UNAVAILABLE"

_MERGED_PR_HEAD_FIELDS = "number,headRefName,headRefOid,mergedAt,state"


def find_merged_pr_for_head(repo, branch, cwd=None):
    """The most recently merged PR whose head is `branch`, as
    ``({"number": N, "head_oid": <sha>}, notice_or_none)`` — ``(None, None)`` when the branch has
    no merged PR, ``(None, MERGED_PR_LOOKUP_UNAVAILABLE)`` when the lookup itself could not run
    (any non-zero exit, auth included — see the notice's comment for why this never escalates to a
    decision).
    """
    result = process.run(
        [
            "gh", "pr", "list",
            "--repo", repo,
            "--head", branch,
            "--state", "merged",
            "--json", _MERGED_PR_HEAD_FIELDS,
        ],
        cwd=cwd,
    )
    if result.returncode != 0:
        return None, MERGED_PR_LOOKUP_UNAVAILABLE
    try:
        prs = json.loads(result.stdout)
    except ValueError:
        return None, MERGED_PR_LOOKUP_UNAVAILABLE
    # `--head` is an exact match, but a branch can carry more than one merged PR over its life
    # (reopened work, a revert-and-remerge): the most recent merge is the one whose head OID a
    # worktree sitting at the merged head would match.
    merged = [pr for pr in prs if (pr.get("state") or "").upper() == "MERGED" or pr.get("mergedAt")]
    if not merged:
        return None, None
    newest = sorted(merged, key=lambda pr: pr.get("mergedAt") or "")[-1]
    return {"number": newest.get("number"), "head_oid": newest.get("headRefOid")}, None


# Non-blocking notice (open notice vocabulary, architecture.md §3): the head-scoped OPEN-PR lookup
# could not run — no auth, no network, an older `gh`. Same never-a-decision rationale as
# MERGED_PR_LOOKUP_UNAVAILABLE above (prep already fetched the issue, so auth demonstrably worked;
# a failure here is a capability problem, not AUTH_REQUIRED). The caller must treat a `None` PR
# carried by this notice as **unknown**, not **absent** — the resolver's epic route gates its
# `create-pr` on the notice's absence precisely so an unavailable lookup can never drive a
# duplicate integration PR.
OPEN_PR_LOOKUP_UNAVAILABLE = "OPEN_PR_LOOKUP_UNAVAILABLE"

_OPEN_PR_HEAD_FIELDS = "number,title,author,isDraft,headRefName,baseRefName,url,updatedAt"


def find_open_pr_for_head(repo, branch, cwd=None):
    """The most recently updated OPEN PR whose head is `branch`, as
    ``({"number", "title", "author", "is_draft", "base_ref", "url", "updated_at"}, notice_or_none)``
    — ``(None, None)`` when the branch carries no open PR, ``(None, OPEN_PR_LOOKUP_UNAVAILABLE)``
    when the lookup itself could not run.

    The exact `--head` match is the point: it answers "does THIS branch already have an open PR",
    which the loose `#<N> in:body` search cannot — that search also returns a sibling story's PR
    that merely mentions the issue. The resolver's epic route uses this as the single source of
    truth for "is the integration PR already open", so a false positive there would suppress the
    open and a false negative would file a duplicate.

    No `head_oid` counterpart to :func:`find_merged_pr_for_head`: the caller needs identity and
    draft state, never a worktree-HEAD comparison.
    """
    result = process.run(
        [
            "gh", "pr", "list",
            "--repo", repo,
            "--head", branch,
            "--state", "open",
            "--json", _OPEN_PR_HEAD_FIELDS,
        ],
        cwd=cwd,
    )
    if result.returncode != 0:
        return None, OPEN_PR_LOOKUP_UNAVAILABLE
    try:
        prs = json.loads(result.stdout)
    except ValueError:
        return None, OPEN_PR_LOOKUP_UNAVAILABLE
    if not prs:
        return None, None
    newest = sorted(prs, key=lambda pr: pr.get("updatedAt") or "")[-1]
    author = newest.get("author") or {}
    return {
        "number": newest.get("number"),
        "title": newest.get("title"),
        "author": author.get("login") if isinstance(author, dict) else author,
        "is_draft": bool(newest.get("isDraft")),
        "base_ref": newest.get("baseRefName"),
        "url": newest.get("url"),
        "updated_at": newest.get("updatedAt"),
    }, None


# ---------------------------------------------------------------------------
# Branch naming + collision suffixing
# ---------------------------------------------------------------------------


def branch_belongs_to_issue(branch, issue_number):
    """True iff `branch` is a branch this pipeline would have minted for `issue_number` —
    `<N>-<slug>` (optionally `-vN`-suffixed) or `epic/<N>-<slug>`. Every branch the pipeline
    creates goes through :func:`compute_branch_name` or the `epic/<N>-<slug>` convention, so this
    is the check that distinguishes "this issue's branch" from a sibling story's branch that merely
    mentions `#<N>` in its PR body (the loose `#<N> in:body` search's blind spot)."""
    if not branch:
        return False
    epic_match = EPIC_BRANCH_NAME_RE.match(branch)
    if epic_match:
        return epic_match.group(1) == str(issue_number)
    prefix = "%s-" % issue_number
    return branch.startswith(prefix) and len(branch) > len(prefix)


# Non-blocking notice (open notice vocabulary, architecture.md §3): the current branch parsed as
# one of the pipeline's two branch grammars, but the issue it names could not be read back — no
# auth, no network, a number that is a PR, or a branch that only LOOKS like `<N>-<slug>` (a
# `2024-roadmap-cleanup` scratch branch parses as issue #2024 and 404s). The caller degrades to "no
# ambient issue" and files exactly as it does today; this never escalates to a decision, because the
# ambient fact is a convenience the drafter offers, never a gate it depends on.
AMBIENT_ISSUE_LOOKUP_UNAVAILABLE = "AMBIENT_ISSUE_LOOKUP_UNAVAILABLE"

# The non-epic half of `branch_belongs_to_issue`'s grammar, read in the other direction: that
# function asks "is this branch issue #N's?", this one asks "which issue is this branch's?".
# `-vN` collision suffixes need no special handling — the slug arm swallows them.
ISSUE_BRANCH_NAME_RE = re.compile(r"^(\d+)-(.+)$")

_AMBIENT_ISSUE_FIELDS = "number,title,state,labels"


def detect_ambient_issue(root, repo, cwd=None):
    """Which issue, if any, the checkout at `root` is standing in — the fact that lets a skill
    notice it was invoked from inside `epic/95-<slug>` or `164-<slug>` instead of filing an orphan.
    Returns ``(fact, notices)``; `fact` is ``None`` whenever there is nothing to offer.

    Deliberately **non-gating**: an unparseable branch (`main`, a scratch name), a detached HEAD, a
    non-repo `root`, or a failed issue lookup all yield ``None`` rather than a decision, so adding
    this fact can never stop a session that files fine today. Only the lookup failure emits a
    notice — a branch that never parsed had nothing to look up and stays silent.

    The `gh` confirmation is what makes the loose `<N>-<slug>` arm safe: the parse is a candidate,
    the live issue is the evidence.
    """
    result = process.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(root))
    if result.returncode != 0:
        return None, []
    branch = (result.stdout or "").strip()
    # `--abbrev-ref` prints the literal "HEAD" for a detached checkout — including every `ro-*`
    # read workspace, which is detached by construction.
    if not branch or branch == "HEAD":
        return None, []

    epic_match = EPIC_BRANCH_NAME_RE.match(branch)
    if epic_match:
        number, pattern = epic_match.group(1), "epic"
    else:
        issue_match = ISSUE_BRANCH_NAME_RE.match(branch)
        if not issue_match:
            return None, []
        number, pattern = issue_match.group(1), "issue"

    view = process.run(
        ["gh", "issue", "view", number, "--repo", repo, "--json", _AMBIENT_ISSUE_FIELDS],
        cwd=cwd,
    )
    if view.returncode != 0:
        return None, [AMBIENT_ISSUE_LOOKUP_UNAVAILABLE]
    try:
        data = json.loads(view.stdout or "{}")
    except ValueError:
        return None, [AMBIENT_ISSUE_LOOKUP_UNAVAILABLE]
    if not data:
        return None, [AMBIENT_ISSUE_LOOKUP_UNAVAILABLE]

    labels = [label.get("name") for label in data.get("labels") or []]
    title = data.get("title") or ""
    return {
        "branch": branch,
        "number": int(number),
        "pattern": pattern,
        "title": title,
        "state": data.get("state"),
        "labels": labels,
        "type": detect_type_with_question(labels, title),
    }, []


def compute_branch_name(root, issue_number, slug):
    """`<issue>-<slug>` with collision suffixing (docs/specs/resolver.md "Branch-collision
    suffixing"; the spec's branch-creation convention): inspect
    `git ls-remote --heads origin "<issue>-<slug>*"`, take the highest existing `-vN` suffix + 1
    (an unsuffixed match counts as v1 — the first collision yields `-v2`). Returns
    `(branch_name, collided_with_or_none)`.

    v3 caution (the `-vN` recompute drift): this collision logic is for MINTING a new branch —
    `prep_workspace_open.py`'s fresh path. A prep that merely *asserts* an ambient branch (the
    resolver) must never re-run this against a branch workspace-open already pushed: the pushed
    branch itself would count as the collision and yield `-v2`, a guaranteed self-mismatch.
    """
    base = "%s-%s" % (issue_number, slug)
    matches, _ = list_remote_branches(root, base + "*")
    # Only exact-base or exact-base+`-vN` count as a real collision — a match that merely starts
    # with the same prefix by coincidence (e.g. "<N>-<slug>-extra-words") is not a versioned
    # sibling of this branch and must not perturb the version count.
    relevant = [m for m in matches if m == base or BRANCH_VERSION_SUFFIX_RE.match(m[len(base) :])]
    if not relevant:
        return base, None
    highest = 1
    for match in relevant:
        if match == base:
            highest = max(highest, 1)
            continue
        suffix_match = BRANCH_VERSION_SUFFIX_RE.match(match[len(base) :])
        if suffix_match:
            highest = max(highest, int(suffix_match.group(1)))
    return "%s-v%d" % (base, highest + 1), sorted(relevant)
