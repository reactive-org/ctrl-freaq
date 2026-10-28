#!/usr/bin/env python3
"""prep_workspace_open.py — open the work workspace for an issue, in one call (the v3
workspace-model inversion's operator-side opener; architecture.md §6).

This prep IS the workspace-open tool's action: it derives the branch, creates the GitHub
issue↔branch link (the native "create a branch for this issue" — `gh issue develop`), creates or
reuses the worktree under ``.worktrees/``, and runs the consuming repo's ``<!-- worktree-setup -->``
hooks — then reports where the operator should start the next session. The pipeline stages
(resolver/evaluator) then merely ASSERT the checkout this prep opened.

Composition (architecture.md §2 "compose the executors in-process")::

    gh_gather.run(..., stream=)          -- the target issue (title/labels/state/open PRs/plan
                                            marker), one round-trip; forwards TARGET_IS_PR /
                                            AUTH_REQUIRED / MARKER_AMBIGUOUS
    branching.*                          -- type detection, prior-PR row classification, epic
                                            branch discovery, parent-epic search, fresh naming
                                            (`-vN` collision suffixing), linked-branch listing
    workspace.branch_in_use_decision     -- BRANCH_IN_USE, asked BEFORE the link create (a
                                            declined card must leave no remote branch or link)
    gh issue develop                     -- the branch↔issue link (create); degrades to a local
                                            branch + ISSUE_LINK_UNSUPPORTED notice
    workspace._build_ensure_work(...)    -- root freshness (ROOT_*), BRANCH_IN_USE, the worktree
                                            create/reuse, info/exclude, setup hooks

Branch/base selection mirrors the resolver's retired ensure-side table exactly:
continue (an open/draft PR of yours) -> the PR's own head branch, NO linking (the PR already
binds branch to issue); epic -> the discovered ``epic/<N>-<slug>`` integration branch or its
bootstrap name — **workspace-open owns epic integration-branch creation in v3**; story under an
open parent epic -> base = the epic branch; standard -> ``compute_branch_name`` off ``main``.
A foreign open/draft PR is the GATED row: prep reports the gate fact with ZERO side effects (no
branch, no link, no worktree) — the skill renders the AskUserQuestion card.

Usage::

    prep_workspace_open.py <issue> <owner/repo> [--root PATH] [--scratch-dir PATH]

Exit codes (architecture.md §3): 0 with the envelope present (``ok`` or ``needs_decision``); 2 on
a usage error; any other non-zero is a hard `gh`/`git` failure with faithful stderr. A setup-hook
failure is an ``ok`` envelope with ``workspace.setup.succeeded: false`` and process exit 1
(mirroring ``workspace.py ensure --work``).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402
import gh_gather  # noqa: E402
import workspace  # noqa: E402
from pipelib import process, trust  # noqa: E402
from pipelib.decisions import AMBIGUOUS, TARGET_IS_SLICE, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, emit_needs_decision, emit_ok  # noqa: E402

PLAN_MARKER = "<!-- implementation-plan:v1 -->"

class _DiscardStream:
    """Write-sink for `gh_gather.run(stream=...)` — same shape as the other preps' local copies."""

    def write(self, _data):
        return 0

    def flush(self):
        return None


def _fetch_current_user(cwd):
    """`gh api user` -> (login, decision_or_none). Mirrors prep_resolver's identical helper."""
    result = process.run(["gh", "api", "user"], cwd=cwd)
    if result.auth_required:
        from pipelib.decisions import AUTH_REQUIRED

        return None, needs_decision(
            AUTH_REQUIRED,
            summary="gh authentication required",
            context={"stderr": result.stderr, "returncode": result.returncode},
            options=["run: gh auth login"],
        )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)
    import json

    return json.loads(result.stdout).get("login"), None


def _create_linked_branch(repo, issue_number, branch, base, cwd=None):
    """`gh issue develop <N> --name <branch> --base <base>` — creates the branch ON ORIGIN and
    links it to the issue. Returns ``(created, notice_or_none)``: any non-auth failure degrades
    to ``(False, ISSUE_LINK_UNSUPPORTED)`` (older gh, missing permission) — the caller falls back
    to a plain local branch fork. Auth failures were already surfaced by the earlier gh calls."""
    result = process.run(
        [
            "gh", "issue", "develop", str(issue_number),
            "--repo", repo, "--name", branch, "--base", base,
        ],
        cwd=cwd,
    )
    if result.returncode != 0:
        return False, branching.ISSUE_LINK_UNSUPPORTED
    return True, None


def build_open(issue_number, repo, root=".", scratch_dir=None, cwd=None):
    """The pure open core (architecture.md §2's executor pattern): perform the open and return
    ``(payload, notices, decision)`` — **never emits**. ``decision is not None`` means nothing past
    the point that raised it ran, and ``payload`` is ``None``; otherwise ``payload`` is the receipt
    (``notices`` travel beside it, never inside). ``main`` is the thin emit wrapper; the
    coordinator's prep composes this same core in-process, so every card it raises renders there
    exactly as it does here. A hard failure still exits non-zero with no envelope (§3)."""
    # Capture the INVOKER's checkout before normalizing: `root` becomes the main checkout on the
    # next line, and the setup-hook block is discovered in the checkout the operator actually ran
    # this from (a different worktree whenever they invoke from inside one).
    invoker_root = workspace.invoking_checkout(cwd if cwd is not None else root)
    root = str(workspace._resolve_main_root(root))
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-workspace-open-%s" % issue_number
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)

    # 1) The target issue — one round-trip (plan-marker lookup included: the summary's next-step
    #    routing needs plan presence — plan-before-open).
    exit_code, issue_envelope = gh_gather.run(
        str(issue_number),
        repo,
        marker_prefix=PLAN_MARKER,
        scratch_dir=scratch_dir,
        env=None,
        stream=_DiscardStream(),
    )
    if issue_envelope is not None and issue_envelope.get("status") == "needs_decision":
        if issue_envelope.get("decision") is not None:
            return None, issue_envelope.get("notices"), issue_envelope["decision"]
    if exit_code != 0:
        sys.stderr.write(
            "prep_workspace_open: gh_gather on issue #%s failed (exit %d)\n"
            % (issue_number, exit_code)
        )
        sys.exit(1)

    labels = [label.get("name") for label in issue_envelope.get("labels") or []]
    issue_title = issue_envelope.get("title") or ""
    issue_type = branching.detect_type(labels, issue_title)
    attention = []
    # The gather's success-path notices are not forwarded, except a skipped untrusted plan marker or
    # part (#179): the plan-presence routing above it must say why a plan reads absent.
    notices = trust.untrusted_notices(issue_envelope.get("notices"))

    if (issue_envelope.get("state") or "").upper() != "OPEN":
        attention.append("issue #%s is %s — opening a workspace for it is unusual"
                         % (issue_number, issue_envelope.get("state")))

    # 2) Prior-PR row (the resolver's 7-row table, shared via branching) — a foreign open/draft
    #    PR gates BEFORE any side effect.
    current_user, user_decision = _fetch_current_user(cwd)
    if user_decision is not None:
        return None, None, user_decision
    # This issue's OWN open PRs, not every PR that mentions it (an epic integration PR lists every
    # story by number). The narrowing must precede the `if not open_prs` gate below, because that
    # gate is what decides whether the closed search runs at all.
    open_prs, mention_only_prs = branching.partition_prs_for_issue(
        issue_envelope.get("open_prs"), issue_number
    )
    closed_prs = None
    if not open_prs:
        closed_prs, closed_decision = branching.search_closed_prs(repo, issue_number, cwd=cwd)
        if closed_decision is not None:
            return None, None, closed_decision
    prior_pr_row, prior_pr_fact = branching.classify_prior_pr_row(
        open_prs, current_user, closed_prs, issue_envelope.get("state"), issue_number
    )
    # Diagnostic only — omitted when empty, never `attention`, nothing gates on it. It exists so a
    # surprising route ("why did it ignore the epic's PR?") is answerable from the envelope.
    prior_pr_rejected = [
        {"number": pr.get("number"), "headRefName": pr.get("headRefName")}
        for pr in mention_only_prs
    ]
    if prior_pr_row in branching.CONTINUE_ROWS:
        mode = branching.MODE_CONTINUE
    elif prior_pr_row in branching.GATED_ROWS:
        mode = branching.MODE_GATED
    else:
        mode = branching.MODE_FRESH

    vector = {"type": issue_type, "mode": mode, "prior_pr_row": prior_pr_row}
    target = {
        "kind": "issue",
        "number": issue_envelope["number"],
        "title": issue_envelope["title"],
        "state": issue_envelope["state"],
        "labels": labels,
    }

    if mode == branching.MODE_GATED:
        card = branching.GATE_CARDS[prior_pr_row]
        vector["gate"] = {
            "reason": prior_pr_row,
            "header": card["header"],
            "options": card["options"],
            "prior_pr": prior_pr_fact,
        }
        # Zero side effects on a gated row — the skill renders the card; nothing was created.
        gated_envelope = {
            "repo": repo,
            "scratch": scratch_dir,
            "root": {"path": root},
            "target": target,
            "vector": vector,
            "prior_pr": prior_pr_fact,
            "attention": attention,
        }
        if prior_pr_rejected:
            gated_envelope["prior_pr_rejected"] = prior_pr_rejected
        return gated_envelope, notices, None

    # 3) Branch/base selection (the resolver's retired ensure-side table).
    branch_source = None
    collided_with = None
    epic_facts = None
    base = workspace.default_branch(root)

    if mode == branching.MODE_CONTINUE and prior_pr_fact and prior_pr_fact.get("headRefName"):
        branch = prior_pr_fact["headRefName"]
        branch_source = "pr-head"
    elif issue_type == "epic":
        epic_facts, epic_decision = branching.discover_epic_branch(root, issue_number, issue_title)
        if epic_decision is not None:
            return None, None, epic_decision
        if epic_facts.get("branch"):
            branch = epic_facts["branch"]
            branch_source = "epic-discovered"
        else:
            branch = "epic/%s-%s" % (issue_number, epic_facts["bootstrap_slug"])
            branch_source = "epic-bootstrap"
    else:
        # Hierarchy lookup gated on HAVING a parent, not on the lexical type (#31) — a sub-issue
        # labelled by kind of work (`bug`, `tech-debt`) reads as `standard` and carries the same
        # native parent edge as its `story`-labelled siblings. Basing it on the default branch
        # takes the epic's unmerged work with it at PR time.
        epic_facts, parent_notices, parent_decision = branching.resolve_parent_epic_branch(
            root,
            repo,
            issue_number,
            issue_type,
            issue_envelope.get("parent"),
            cwd=cwd,
            classify=True,
        )
        notices.extend(parent_notices)
        if parent_decision is not None:
            return None, None, parent_decision
        if epic_facts is not None:
            if epic_facts.get("parent_kind") == branching.PARENT_KIND_NON_EPIC:
                # A non-epic's sub-issue is a deliverable slice by construction
                # (skills/_shared/epic-story-hierarchy.md), and a slice has NO branch and no PR of
                # its own — it ships as a phase on its parent's branch. Minting one here would
                # silently promote it to a story, the same demotion-in-reverse #30 fixed. Emitted
                # from step 3, BEFORE the link create and the worktree ensure, so a declined card
                # leaves zero side effects.
                return None, notices, needs_decision(
                        TARGET_IS_SLICE,
                        summary="#%s is a deliverable slice of #%s — a slice ships as a phase on "
                        "its parent's branch and has no branch or PR of its own"
                        % (issue_number, epic_facts["parent_epic"]["number"]),
                        context={
                            "issue": issue_number,
                            "parent": epic_facts["parent_epic"],
                            "parent_kind": epic_facts.get("parent_kind"),
                        },
                        # Every option is an action the operator takes OUTSIDE and then re-runs,
                        # matching every other card in the pipeline. A "proceed anyway" option
                        # would be unactionable: this prep has no override flag, so re-running
                        # after it would raise the identical card. Both remedies below fix the
                        # cause instead of this one session — the classification is lexical, so a
                        # labelled parent classifies correctly for every future session too.
                        options=[
                            "open the parent's workspace instead: "
                            "/ctrl-freaq:workspace-open %s"
                            % epic_facts["parent_epic"]["number"],
                            "if #%s IS an epic, label it `epic` (or retitle it `Epic: …`), "
                            "then re-run" % epic_facts["parent_epic"]["number"],
                            "if #%s should ship on its own branch, re-parent it to the epic (or "
                            "clear its parent edge), then re-run" % issue_number,
                        ],
                    )
            branch_facts = epic_facts.get("branch_facts") or {}
            if branch_facts.get("branch"):
                base = branch_facts["branch"]
            elif (
                issue_type == "story"
                and epic_facts.get("parent_epic")
                and branching.PARENT_CLOSED not in parent_notices
            ):
                # A story's parent IS an epic by construction, so naming it one is safe here and
                # the operator can act on it (open the epic's workspace first). The untyped case
                # cannot say that much — it rides the notice instead.
                #
                # A CLOSED parent is excluded because the advice would be wrong, not merely
                # unhelpful: forking from main is the correct and FINAL outcome there, and there is
                # no workspace left to open. That case rides `PARENT_CLOSED` alone.
                attention.append(
                    "parent epic #%s has no integration branch yet — this story forks from "
                    "main; open the epic's workspace first if it should stack on the epic"
                    % epic_facts["parent_epic"]["number"]
                )
        branch, collided_with = branching.compute_branch_name(
            root, issue_number, branching.compute_fresh_slug(issue_title)
        )
        branch_source = "computed"

    # 4) Linking — adopt an existing linked branch, else create one (fresh paths only; a continue
    #    row's PR already binds branch to issue).
    link = {"attempted": False, "created": False, "existing": [], "method": "gh-issue-develop"}
    if mode != branching.MODE_CONTINUE:
        linked, link_notice, link_decision = branching.list_linked_branches(
            repo, issue_number, cwd=cwd
        )
        if link_decision is not None:
            return None, None, link_decision
        link["attempted"] = True
        if link_notice:
            notices.append(link_notice)
        elif linked and len(linked) > 1:
            return None, None, needs_decision(
                AMBIGUOUS,
                summary="%d GitHub-linked branches already exist for issue #%s — expected at most one"
                % (len(linked), issue_number),
                context={"issue": issue_number, "candidates": linked},
                options=[
                    "unlink or delete the stray branch(es), then re-run",
                    "open the workspace for the branch you mean by hand",
                ],
            )
        elif linked:
            # Adopt — the linked branch IS the issue's branch, whatever this run would have named.
            branch = linked[0]
            branch_source = "linked"
            link["existing"] = linked
            collided_with = None
        else:
            # Exclusivity first: the create pushes the branch to origin and links it, so a
            # BRANCH_IN_USE raised by step 5 after it would leave both behind on a declined card
            # (skills/workspace-open/SKILL.md §1 — every decision before any link or worktree side
            # effect). The adopt and continue rows create nothing, so step 5's own check already
            # comes first there.
            in_use = workspace.branch_in_use_decision(root, branch)
            if in_use is not None:
                return None, None, in_use
            created, create_notice = _create_linked_branch(repo, issue_number, branch, base, cwd=cwd)
            link["created"] = created
            if create_notice:
                notices.append(create_notice)

    # 5) The worktree: root freshness (ROOT_*), BRANCH_IN_USE (re-checked — for a fresh create,
    #    step 4 already asked), create/reuse, info/exclude, setup hooks — all the existing
    #    ensure --work contract. A develop-created remote branch is
    #    found by the ensure's own ls-remote probe and checked out at its head.
    workspace_envelope, ws_notices, ws_decision = workspace._build_ensure_work(
        root, branch, base, hook_root=invoker_root
    )
    if ws_decision is not None:
        return None, None, ws_decision
    # Forward the core's notices (e.g. HOOK_SOURCE_DIRTY — the hook block came from a checkout
    # with uncommitted changes). Nothing gates on it; dropping it would make the report invisible.
    notices.extend(ws_notices)

    setup = workspace_envelope.get("setup") or {}
    if setup.get("succeeded") is False:
        first_failure = setup.get("first_failure") or {}
        attention.append(
            "worktree setup hook failed at step %s (`%s`) — worktree exists but is not ready"
            % (first_failure.get("step"), first_failure.get("command"))
        )

    envelope = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": {"path": root},
        "target": target,
        "vector": vector,
        "prior_pr": prior_pr_fact,
        "branch": {
            "name": branch,
            "base": base,
            "source": branch_source,
            "collided_with": collided_with,
        },
        "link": link,
        "plan": {"present": bool(issue_envelope.get("marker_comment_present"))},
        "epic": epic_facts,
        "workspace": workspace_envelope,
        "next_step": "start the next session inside workspace.path",
        "attention": attention,
    }
    if prior_pr_rejected:
        envelope["prior_pr_rejected"] = prior_pr_rejected
    return envelope, notices, None


def setup_failed(payload):
    """True iff the open's worktree setup hook failed — the one ok outcome that exits non-zero."""
    workspace_facts = (payload or {}).get("workspace") or {}
    return (workspace_facts.get("setup") or {"succeeded": True}).get("succeeded", True) is False


def main(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue", help="issue number")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--root", default=".", help="any checkout of the repo (normalized to the main checkout)")
    parser.add_argument("--scratch-dir", dest="scratch_dir", default=None)
    parser.add_argument("--cwd", default=None, help="explicit cwd for the gh calls (test injection)")
    args = parser.parse_args(argv)

    payload, notices, decision = build_open(
        args.issue, args.repo, root=args.root, scratch_dir=args.scratch_dir, cwd=args.cwd
    )
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return EXIT_OK
    emit_ok(payload=payload, notices=notices)
    return 1 if setup_failed(payload) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
