#!/usr/bin/env python3
"""prep_coordinator.py — where an issue stands in the pipeline, as one fact (#83; the
``coordinator`` standalone tool's prep, ``skills/coordinator/SKILL.md``).

"Facts by script, meaning by model", applied to orchestration. The coordinator never decides
from a session's note where an issue stands: every coordinator turn begins with this one call,
which derives the issue's ``stage`` from GitHub state plus the local work-worktree list and names
the next step — the command, and the absolute folder it runs in. The model writes the stage
brief, relays progress and renders cards; it never re-derives the stage.

Read-only by default. At most one **action** flag, passed only after the operator answered the
coordinator's consent card, runs the matching workspace core in-process and then re-derives:

    --open <N>         prep_workspace_open.build_open     (the workspace-open tool's own core)
    --close <branch>   prep_workspace_close.build_close   (the workspace-close tool's own core)
    --sync             workspace._build_sync_work          (the epic worktree, fast-forward only)

A decision a core raises is emitted **unchanged**, so its card renders here exactly as in the
owning tool. State derivation never calls those cores: they act immediately with no dry-run, so
an action that ran during a read would be an open or close the operator never confirmed.

It composes existing cores and derives nothing they already own: ``gh_gather.run`` (body, thread,
plan marker, native relations), ``branching`` (type, the 7-row prior-PR table, parent / slice
classification, epic-branch discovery, branch ownership), ``workspace`` (work-worktree list, root
status), ``delivery_log``, ``parse`` (stories checklist, phases, phase tracker), the resolver's
open-question gate (``oq_tracker.build_open_question_facts``, the resolver's own — blocked iff
``in-scope (blocked)`` and the tracker unresolved), and ``gh_pr_gather``. It
never composes a stage prep's ``build_facts``: those assert the ambient worktree, and the
coordinator runs from the project root.

The stage table itself is :func:`derive_stage`, a pure function over the gathered state, so the
offline suite pins every row without a fixture per row. For an epic, every ``epic.stories`` entry
also carries that story's own ``stage`` and ``next`` (:func:`derive_story_stages`), derived from
the story's own-branch facts through the same function a direct run on the story uses.

Usage::

    prep_coordinator.py <issue> <owner/repo> [--root PATH] [--scratch-dir DIR] [--story M]
                        [--open N | --close BRANCH | --sync]

Exit codes (architecture.md §3): 0 with the envelope present (``ok`` or ``needs_decision``); 1
after an action whose worktree setup hook failed (the ok envelope still reports it, as the
workspace tools do); 2 on a usage error; any other non-zero is a hard `gh`/`git` failure.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402
import delivery_log  # noqa: E402
import gh_gather  # noqa: E402
import gh_pr_gather  # noqa: E402
import parse  # noqa: E402
import plan_parts  # noqa: E402
import oq_tracker  # noqa: E402  (the open-question gate the resolver also composes)
import prep_workspace_close  # noqa: E402
import prep_workspace_open  # noqa: E402
import story_set  # noqa: E402
import workspace  # noqa: E402
from pipelib.decisions import AUTH_REQUIRED  # noqa: E402
from pipelib.envelope import EXIT_OK, emit_needs_decision, emit_ok  # noqa: E402
from pipelib.thread import load_thread  # noqa: E402

RESEARCH_MARKER = "<!-- issue-research:v1 -->"

# The closed stage set. The router and both playbooks name every one of these (the routing test
# pins it against this tuple), so a new stage is a contract change in three places at once.
STAGE_REFUSED = "refused"
STAGE_GATED = "gated"
STAGE_NEEDS_SLICING = "needs-slicing"
STAGE_NEEDS_PLAN = "needs-plan"
STAGE_NEEDS_WORKSPACE = "needs-workspace"
STAGE_RESOLVING = "resolving"
STAGE_PR_READY = "pr-ready"
STAGE_MERGED_NEEDS_CLOSE = "merged-needs-close"
STAGE_NEXT_STORY = "next-story"
STAGE_EPIC_INTEGRATION = "epic-integration"
STAGE_TERMINAL = "terminal"
STAGES = (
    STAGE_REFUSED, STAGE_GATED, STAGE_NEEDS_SLICING, STAGE_NEEDS_PLAN, STAGE_NEEDS_WORKSPACE,
    STAGE_RESOLVING, STAGE_PR_READY, STAGE_MERGED_NEEDS_CLOSE, STAGE_NEXT_STORY,
    STAGE_EPIC_INTEGRATION, STAGE_TERMINAL,
)

# Why a target is refused — facts the router renders as a card (the slicer's refusal pattern), not
# §3 decision codes: nothing here is a mechanical blocker a re-run resolves.
REFUSAL_QUESTION = "question"
REFUSAL_SLICE = "slice"
REFUSAL_OQ_BLOCKED = "oq-blocked"
REFUSALS = (REFUSAL_QUESTION, REFUSAL_SLICE, REFUSAL_OQ_BLOCKED)

# What the next step is: start a stage session, run a workspace action after a consent card, pick
# the next story, or nothing.
NEXT_SESSION = "session"
NEXT_OPEN = "open"
NEXT_CLOSE = "close"
NEXT_PICK = "pick"
NEXT_NONE = "none"

PLAYBOOK_EPIC = "epic.md"
PLAYBOOK_SINGLE = "single.md"


class _DiscardStream:
    def write(self, _data):
        return 0

    def flush(self):
        return None


def _command(skill, arg):
    return "/github-pipeline:%s %s" % (skill, arg)


# ---------------------------------------------------------------------------
# The stage table — pure.
# ---------------------------------------------------------------------------


def _session(skill, arg, folder, **extra):
    nxt = {"kind": NEXT_SESSION, "skill": skill, "command": _command(skill, arg), "folder": folder}
    nxt.update(extra)
    return nxt


def _open(number):
    # `command` is the manual equivalent, for the report-only fallback and the operator's own
    # reference; the coordinator itself runs the open through `--open`.
    return {"kind": NEXT_OPEN, "issue": number, "command": _command("workspace-open", number),
            "folder": None}


def _close(branch):
    return {"kind": NEXT_CLOSE, "branch": branch, "command": _command("workspace-close", branch),
            "folder": None}


def derive_unit(unit, plan_folder, sync_first=False):
    """One issue's stage on its own branch — a standalone issue, a story, or (with
    ``plan_folder`` its epic worktree) an epic story. Returns ``(stage, next)``, or
    ``(None, None)`` when the unit is finished (closed, worktree released)."""
    worktree = unit.get("worktree")
    pr = unit.get("pr") or {}
    if unit.get("closed"):
        if worktree:
            return STAGE_MERGED_NEEDS_CLOSE, _close(worktree["branch"])
        return None, None
    if unit.get("gate"):
        return STAGE_GATED, {"kind": NEXT_NONE}
    if pr.get("merged"):
        # The PR merged while the issue stayed open: the work has shipped, so release the worktree
        # if one is left and otherwise stop — never open a fresh one for merged work. The
        # attention line (see _unit_attention) says the issue still needs closing.
        if worktree:
            return STAGE_MERGED_NEEDS_CLOSE, _close(worktree["branch"])
        return None, None
    if not unit.get("plan_present"):
        return STAGE_NEEDS_PLAN, _session("planner", "#%s" % unit["number"], plan_folder,
                                          sync_first=bool(sync_first))
    if not worktree:
        return STAGE_NEEDS_WORKSPACE, _open(unit["number"])
    if pr.get("number") and not pr.get("draft"):
        return STAGE_PR_READY, _session("evaluator", "#%s" % pr["number"], worktree["path"])
    return STAGE_RESOLVING, _session("resolver", "#%s" % unit["number"], worktree["path"])


def derive_under_parent(unit, parent, root):
    """One issue's stage as a direct coordinator run on it derives it, under ``parent`` — its
    epic's facts (``number`` / ``branch`` / ``worktree`` / ``behind``), or ``{}`` for a standalone
    issue. Returns ``(stage, next)``; a finished unit is ``terminal``."""
    plan_folder = root
    if parent.get("branch"):
        # A story under an epic plans against the epic branch: in its worktree, or — when none
        # is open yet — open the epic's first (the planner refuses a checkout behind its base).
        if not parent.get("worktree") and not unit.get("plan_present") and not unit.get("closed"):
            return STAGE_NEEDS_WORKSPACE, _open(parent["number"])
        if parent.get("worktree"):
            plan_folder = parent["worktree"]["path"]
    stage, nxt = derive_unit(unit, plan_folder, sync_first=parent.get("behind"))
    if stage is None:
        return STAGE_TERMINAL, {"kind": NEXT_NONE}
    return stage, nxt


def derive_story_stages(state):
    """Every epic story's own ``{stage, next}``, through :func:`derive_under_parent` with the epic
    facts as its parent — plus ``refusal`` when refused and ``gate`` when gated. ``{}`` for a
    non-epic target.

    It departs from a direct run on the story in four places, each by design: a sibling whose
    read raised a decision is ``gated`` on it (``gate: {reason, decision}``) where a direct run
    raises the card; a closed story is not read, so no open-question refusal is checked for it;
    an open story gets only the open-question refusal (it is the epic's story, so the question
    and slice refusals do not apply); and the parent is always this epic, which a direct run
    finds only through the native relation or a ``story``-labelled checklist entry."""
    epic = state.get("epic")
    if epic is None:
        return {}
    root = state["root_path"]
    records = state.get("story_units") or {}
    stages = {}
    for story in epic.get("stories") or []:
        number = story["number"]
        if story.get("closed"):
            unit = {"number": number, "closed": True, "worktree": story.get("worktree")}
        else:
            record = records[number]  # gather_state reads every open story
            if record.get("decision") is not None:
                decision = record["decision"]
                stages[number] = {"stage": STAGE_GATED, "next": {"kind": NEXT_NONE},
                                  "gate": {"reason": decision.get("code"), "decision": decision}}
                continue
            if record.get("refusal") is not None:
                stages[number] = {"stage": STAGE_REFUSED, "next": {"kind": NEXT_NONE},
                                  "refusal": record["refusal"]}
                continue
            unit = record["unit"]
        stage, nxt = derive_under_parent(unit, epic, root)
        facts = {"stage": stage, "next": nxt}
        if stage == STAGE_GATED:
            facts["gate"] = unit.get("gate")
        stages[number] = facts
    return stages


def derive_stage(state):
    """The whole stage table over the gathered ``state`` (see :func:`gather_state`). Returns
    ``{"stage", "next", "focus"}`` — ``focus`` is the issue number the stage is about (a story
    inside an epic run, else the target)."""
    target = state["target"]
    root = state["root_path"]
    if state.get("refusal"):
        return {"stage": STAGE_REFUSED, "next": {"kind": NEXT_NONE}, "focus": target["number"]}

    epic = state.get("epic")
    if target["type"] != "epic":
        stage, nxt = derive_under_parent(state["unit"], state.get("parent_epic") or {}, root)
        return {"stage": stage, "next": nxt, "focus": target["number"]}

    number = target["number"]
    worktree = epic.get("worktree")
    if epic.get("closed"):
        if worktree:
            return {"stage": STAGE_MERGED_NEEDS_CLOSE, "next": _close(worktree["branch"]), "focus": number}
        return {"stage": STAGE_TERMINAL, "next": {"kind": NEXT_NONE}, "focus": number}
    stories = epic.get("stories") or []
    if not stories:
        return {"stage": STAGE_NEEDS_SLICING, "next": _session("slicer", "#%s" % number, root),
                "focus": number}
    if not epic.get("plan_present"):
        return {"stage": STAGE_NEEDS_PLAN, "next": _session("planner", "#%s" % number, root),
                "focus": number}
    if not worktree:
        return {"stage": STAGE_NEEDS_WORKSPACE, "next": _open(number), "focus": number}

    # A merged story's worktree is offered for release before the next story is picked — but never
    # ahead of a story already in focus (in flight, or named by --story), so an operator who
    # declines the close is not stuck re-answering it; the pending close then rides `attention`.
    focus = state.get("focus_unit")
    if focus is None:
        for story in stories:
            if story.get("closed") and story.get("worktree"):
                return {"stage": STAGE_MERGED_NEEDS_CLOSE, "next": _close(story["worktree"]["branch"]),
                        "focus": story["number"]}

    if any(not story.get("closed") for story in stories):
        if focus is None:
            return {"stage": STAGE_NEXT_STORY, "next": {"kind": NEXT_PICK}, "focus": None}
        stage, nxt = derive_unit(focus, worktree["path"], sync_first=epic.get("behind"))
        if stage is None:  # an open story whose PR already merged: nothing left to run on it
            return {"stage": STAGE_NEXT_STORY, "next": {"kind": NEXT_PICK}, "focus": None}
        return {"stage": stage, "next": nxt, "focus": focus["number"]}

    # Every story closed: the integration PR. It has been a draft all along (the resolver opens it
    # early, while stories are still landing), so draft here means "the epic route has not run",
    # and only a ready PR is the evaluator's.
    integration = epic.get("integration") or {}
    if integration.get("gate"):
        return {"stage": STAGE_GATED, "next": {"kind": NEXT_NONE}, "focus": number}
    if integration.get("number") and not integration.get("draft"):
        return {"stage": STAGE_PR_READY,
                "next": _session("evaluator", "#%s" % integration["number"], worktree["path"]),
                "focus": number}
    return {"stage": STAGE_EPIC_INTEGRATION,
            "next": _session("resolver", "#%s" % number, worktree["path"]), "focus": number}


def routes_for(state, focus):
    """Skill -> the absolute folder its session runs in, for the issue the stage is about — so a
    relayed handoff's command (a re-route the GitHub state cannot show) maps to a folder by script,
    not prose. ``None`` where that skill has no folder yet (no worktree open)."""
    root = state["root_path"]
    epic = state.get("epic")
    parent = state.get("parent_epic") or {}
    unit = state.get("unit")
    if epic is not None and focus is not None and focus != state["target"]["number"]:
        unit = state.get("focus_unit")
    epic_worktree = (epic or {}).get("worktree") or parent.get("worktree")
    plan_folder = root
    if epic is not None and focus != state["target"]["number"] and epic_worktree:
        plan_folder = epic_worktree["path"]
    elif epic is None and epic_worktree:
        plan_folder = epic_worktree["path"]
    if epic is not None and focus == state["target"]["number"]:
        work_folder = (epic.get("worktree") or {}).get("path")
    else:
        work_folder = ((unit or {}).get("worktree") or {}).get("path")
    return {
        "drafter": root,
        "researcher": root,
        "slicer": root,
        "question-resolver": root,
        "planner": plan_folder,
        "resolver": work_folder,
        "evaluator": work_folder,
    }


# ---------------------------------------------------------------------------
# Gathering — every GitHub/git read the table needs.
# ---------------------------------------------------------------------------


class _Gatherer:
    """One run's reads, with each successfully gathered issue read at most once (an action and
    the re-derivation after it both read the same issue)."""

    def __init__(self, repo, root, scratch_dir, cwd):
        self.repo = repo
        self.root = root
        self.scratch_dir = scratch_dir
        self.cwd = cwd
        self.notices = []
        self._issues = {}
        self._user = None
        self._cache = {}

    def issue(self, number):
        number = int(number)
        if number in self._issues:
            return self._issues[number], None
        scratch = str(Path(self.scratch_dir) / ("issue-%s" % number))
        Path(scratch).mkdir(parents=True, exist_ok=True)
        exit_code, envelope = gh_gather.run(
            str(number), self.repo, marker_prefix=plan_parts.MAIN_MARKER, scratch_dir=scratch,
            env=None, stream=_DiscardStream(),
        )
        if envelope is not None and envelope.get("status") == "needs_decision":
            # Never cached, so `--sync` re-reads a degraded sibling: keep its notices once.
            self.notices.extend(n for n in envelope.get("notices") or [] if n not in self.notices)
            return None, envelope["decision"]
        if exit_code != 0:
            sys.stderr.write("prep_coordinator: gh_gather on #%s failed (exit %d)\n" % (number, exit_code))
            sys.exit(1)
        for notice in envelope.get("notices") or []:
            if notice not in self.notices:
                self.notices.append(notice)
        self._issues[number] = envelope
        return envelope, None

    def current_user(self):
        if self._user is None:
            login, decision = prep_workspace_open._fetch_current_user(self.cwd)
            if decision is not None:
                return None, decision
            self._user = login or ""
        return self._user, None

    def _memo(self, key, fetch):
        """Every remote read below is made once per run: `--sync` derives the state both before
        and after the sync, and only the local worktree facts can have changed in between."""
        if key not in self._cache:
            value = fetch()
            if value[-1] is not None:  # a decision is never cached
                return value
            self._cache[key] = value
        return self._cache[key]

    def closed_prs(self, number):
        return self._memo(("closed", number),
                          lambda: branching.search_closed_prs(self.repo, number, cwd=self.cwd))

    def pr_facts(self, pr_number):
        def fetch():
            payload, notices, decision = gh_pr_gather.build_pr_facts(pr_number, self.repo, cwd=self.cwd)
            self.notices.extend(n for n in notices or [] if n not in self.notices)
            return payload, decision
        return self._memo(("pr", pr_number), fetch)

    def parent_facts(self, number, issue_type, native_parent):
        def fetch():
            facts, notices, decision = branching.resolve_parent_epic_branch(
                self.root, self.repo, number, issue_type, native_parent, cwd=self.cwd, classify=True,
            )
            self.notices.extend(n for n in notices or [] if n not in self.notices)
            return facts, decision
        return self._memo(("parent", number), fetch)

    def open_questions(self, envelope):
        number = int(envelope["number"])

        def fetch():
            scratch = Path(self.scratch_dir) / ("oq-%s" % number)
            scratch.mkdir(parents=True, exist_ok=True)
            return oq_tracker.build_open_question_facts(
                _text(envelope, "issue_body"), self.repo, envelope.get("blocked_by"), str(scratch), cwd=self.cwd,
            )
        return self._memo(("oq", number), fetch)


def _text(envelope, key):
    """A gather section's text, inline or spilled."""
    if envelope.get("%s_mode" % key) == "path":
        return Path(envelope["%s_path" % key]).read_text(encoding="utf-8")
    return envelope.get(key) or ""


def _labels(envelope):
    return [label.get("name") for label in envelope.get("labels") or []]


def _issue_type(envelope):
    return branching.detect_type_with_question(_labels(envelope), envelope.get("title") or "")


def _worktree_for(root, number, branches, epic=False):
    """The work worktree for issue ``number``. For an epic, only its ``epic/<N>-<slug>``
    integration worktree counts — a ``<N>-<slug>`` branch would be some other workspace."""
    for branch in branches:
        if epic and not branch.startswith("epic/"):
            continue
        if branching.branch_belongs_to_issue(branch, number):
            return {"branch": branch, "path": str(workspace._work_path(root, branch))}
    return None


def _remote_tip(cwd, branch):
    result = workspace._git(["ls-remote", "--heads", "origin", branch], cwd)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip().split("\t")[0].strip()


def _behind(worktree):
    """Is the worktree's HEAD missing its branch's remote tip? A read, never a fetch: an
    unanswerable containment probe (the tip was never fetched here) counts as behind, which is
    exactly the case a sync fixes."""
    path = worktree["path"]
    tip = _remote_tip(path, worktree["branch"])
    head = workspace._current_sha(path)
    worktree["head"] = head
    worktree["remote_tip"] = tip
    if tip is None or tip == head:
        return False
    return workspace.contains_commit(path, tip) is not True


def _plan_text(envelope):
    if not envelope.get("marker_comment_present"):
        return None
    if envelope.get("marker_comment_mode") == "path":
        return Path(envelope["marker_comment_path"]).read_text(encoding="utf-8")
    return envelope.get("marker_comment_body") or ""


def _phase_facts(plan_text, pr_body):
    """`{total, shipped, next_checkpoint}` from the plan's `## Phases` and the PR's tracker —
    the status line's "phase 2 of 3". Either side unreadable degrades to ``None``."""
    try:
        phases = parse.parse_phases(plan_text or "")
    except parse._PhasesMalformed:  # noqa: SLF001
        return None
    if not phases:
        return None
    tracker = parse.scan_phase_tracker(pr_body or "")
    shipped = {row["phase"] for row in tracker.get("rows") or [] if row.get("checked") and not row.get("sub_label")}
    pending = [phase for phase in phases if phase.get("number") not in shipped]
    return {
        "total": len(phases),
        "shipped": len(shipped),
        "tracker_present": tracker.get("present", False),
        "next_phase": pending[0].get("number") if pending else None,
        "next_checkpoint": pending[0].get("checkpoint") if pending else None,
    }


def build_unit(g, envelope, branches):
    """One issue's own-branch facts: plan, worktree, prior-PR row, PR state. Returns
    ``(unit, decision)``."""
    number = int(envelope["number"])
    user, decision = g.current_user()
    if decision is not None:
        return None, decision
    open_prs, _mention_only = branching.partition_prs_for_issue(envelope.get("open_prs"), number)
    closed_prs = None
    if not open_prs:
        closed_prs, decision = g.closed_prs(number)
        if decision is not None:
            return None, decision
    row, fact = branching.classify_prior_pr_row(open_prs, user, closed_prs, envelope.get("state"), number)
    plan_text = _plan_text(envelope)
    unit = {
        "number": number,
        "title": envelope.get("title"),
        "state": envelope.get("state"),
        "closed": (envelope.get("state") or "").upper() == "CLOSED",
        "plan_present": bool(envelope.get("marker_comment_present")),
        "research_present": any(
            (c.get("body") or "").startswith(RESEARCH_MARKER) for c in load_thread(envelope)
        ),
        "worktree": _worktree_for(g.root, number, branches),
        "prior_pr_row": row,
        "pr": None,
        "gate": None,
    }
    if row in branching.GATED_ROWS:
        card = branching.GATE_CARDS[row]
        unit["gate"] = {"reason": row, "header": card["header"], "options": card["options"], "prior_pr": fact}
    elif row in branching.CONTINUE_ROWS and fact:
        unit["pr"] = {"number": fact["number"], "draft": bool(fact.get("isDraft")), "merged": False,
                      "head": fact.get("headRefName"), "url": fact.get("url")}
        if unit["pr"]["draft"] and plan_text:
            pr_facts, decision = g.pr_facts(fact["number"])
            if decision is not None:
                return None, decision
            unit["pr"]["phases"] = _phase_facts(plan_text, (pr_facts or {}).get("body"))
    elif fact and fact.get("merged"):
        unit["pr"] = {"number": fact["number"], "draft": False, "merged": True,
                      "head": fact.get("headRefName"), "url": fact.get("url")}
    return unit, None


def _story_set(envelope):
    """The epic's stories through `epic-story-hierarchy.md`'s two-tier read (native first, the
    legacy `## Stories` checklist second, unioned) — the same shape as the evaluator's
    ``_story_set_facts``, through `story_set`; no stories at all reports no source."""
    read, _decision = story_set.read(envelope.get("sub_issues"), _text(envelope, "issue_body"))
    stories = story_set.closed_view(read)
    source = story_set.source(read["native"], story_set.filed(read["checklist_raw"]))
    return stories, source


def _refusal(g, envelope, issue_type):
    """A target the coordinator never drives: a question, a slice, an in-scope-blocked open
    question. Returns ``(refusal_or_none, parent_facts_or_none, decision)``."""
    number = int(envelope["number"])
    if issue_type == "question":
        return {"reason": REFUSAL_QUESTION, "issue": number,
                "context": {"title": envelope.get("title")}}, None, None
    parent_facts = None
    if issue_type != "epic":
        parent_facts, decision = g.parent_facts(number, issue_type, envelope.get("parent"))
        if decision is not None:
            return None, None, decision
        if parent_facts and parent_facts.get("parent_kind") == branching.PARENT_KIND_NON_EPIC:
            return {"reason": REFUSAL_SLICE, "issue": number,
                    "context": {"parent": parent_facts.get("parent_epic")}}, parent_facts, None
    refusal, decision = _oq_refusal(g, envelope)
    return refusal, parent_facts, decision


def _oq_refusal(g, envelope):
    entries, blocked, decision = g.open_questions(envelope)
    if decision is not None:
        return None, decision
    if not blocked:
        return None, None
    blocking = [e for e in entries if e.get("status") == "blocked"]
    return {"reason": REFUSAL_OQ_BLOCKED, "issue": int(envelope["number"]),
            "context": {"blocking": [{"oq_id": e.get("oq_id"), "question": e.get("question"),
                                      "gates": e.get("gates"), "source": e.get("source")}
                                     for e in blocking]}}, None


def gather_state(g, issue_number, story=None):
    """Everything :func:`derive_stage` reads, plus the reporting facts around it. Returns
    ``(state, decision)``."""
    root = g.root
    attention = []
    envelope, decision = g.issue(issue_number)
    if decision is not None:
        return None, decision
    issue_type = _issue_type(envelope)
    branches = workspace.list_work_branches(root)
    state = {
        "root_path": root,
        "target": {"number": int(envelope["number"]), "title": envelope.get("title"),
                   "state": envelope.get("state"), "type": issue_type, "labels": _labels(envelope)},
        "attention": attention,
        "refusal": None,
        "epic": None,
        "unit": None,
        "focus_unit": None,
        "story_units": None,
        "parent_epic": None,
    }
    refusal, parent_facts, decision = _refusal(g, envelope, issue_type)
    if decision is not None:
        return None, decision
    if refusal is not None:
        state["refusal"] = refusal
        return state, None

    if issue_type != "epic":
        unit, decision = build_unit(g, envelope, branches)
        if decision is not None:
            return None, decision
        state["unit"] = unit
        state["parent_epic"] = _parent_epic_facts(root, parent_facts, branches)
        _unit_attention(unit, attention)
        return state, None

    number = state["target"]["number"]
    stories, source = _story_set(envelope)
    for entry in stories:
        entry["worktree"] = _worktree_for(root, entry["number"], branches)
    worktree = _worktree_for(root, number, branches, epic=True)
    epic_branch = worktree["branch"] if worktree else None
    if epic_branch is None:
        facts, decision = branching.discover_epic_branch(root, number, envelope.get("title") or "")
        if decision is not None:
            return None, decision
        epic_branch = facts.get("branch")
    log, log_decision = delivery_log.collect(load_thread(envelope))
    if log_decision is not None:
        attention.append("the epic's delivery log has a duplicated entry (%s) — shipped stories read "
                         "from the rest" % log_decision.get("summary"))
    epic = {
        "number": number,
        "closed": (envelope.get("state") or "").upper() == "CLOSED",
        "plan_present": bool(envelope.get("marker_comment_present")),
        "plan_path": envelope.get("marker_comment_path"),
        "branch": epic_branch,
        "worktree": worktree,
        "behind": _behind(worktree) if worktree else False,
        "stories": stories,
        "stories_source": source,
        "shipped": [entry["story"] for entry in (log or {}).get("entries") or []],
        "integration": None,
        "candidates": [],
    }
    state["epic"] = epic
    numbers = {s["number"] for s in stories}
    open_stories = [s for s in stories if not s["closed"]]
    in_flight = [s for s in open_stories if s.get("worktree")]
    focus_number = None
    if story is not None:
        if int(story) not in numbers:
            attention.append("#%s is not one of epic #%s's stories — --story ignored" % (story, number))
        elif any(s["number"] == int(story) and s["closed"] for s in stories):
            attention.append("story #%s is closed — --story ignored" % story)
        else:
            focus_number = int(story)
    if focus_number is None and len(in_flight) == 1:
        focus_number = in_flight[0]["number"]
    elif focus_number is None and len(in_flight) > 1:
        attention.append("%d stories have an open worktree (%s) — pass --story to choose which one "
                         "the coordinator drives" % (len(in_flight), ", ".join("#%s" % s["number"] for s in in_flight)))
    epic["candidates"] = [{"number": s["number"], "title": s["title"], "in_flight": bool(s.get("worktree"))}
                          for s in open_stories]
    # Every open story is read the way the focus story always was, so each gets its own stage
    # (derive_story_stages). The focus story's decision, and AUTH_REQUIRED from any story, still
    # stop the run; any other sibling decision gates only that sibling's entry, so a sibling's
    # duplicated plan marker never turns --story <focus> into a card. A hard `gh` failure on any
    # story still ends the prep, as it always did for the focus story (a network or `gh` fault
    # the focus read would hit too).
    story_units = {}
    for entry in open_stories:
        n = entry["number"]
        record = {"unit": None, "refusal": None, "decision": None}
        story_envelope, decision = g.issue(n)
        if decision is None:
            record["refusal"], decision = _oq_refusal(g, story_envelope)
        if decision is None and record["refusal"] is None:
            record["unit"], decision = build_unit(g, story_envelope, branches)
        if decision is not None:
            if n == focus_number or decision.get("code") == AUTH_REQUIRED:
                return None, decision
            record["decision"] = decision
            attention.append("story #%s could not be read (%s: %s) — its entry is gated on that decision"
                             % (n, decision.get("code"), decision.get("summary")))
        story_units[n] = record
    state["story_units"] = story_units
    if focus_number is not None and open_stories:
        record = story_units[focus_number]
        if record["refusal"] is not None:
            state["refusal"] = record["refusal"]
            return state, None
        unit = record["unit"]
        state["focus_unit"] = unit
        _unit_attention(unit, attention)
        pending = [st for st in stories if st["closed"] and st.get("worktree")]
        if pending:
            attention.append("merged story worktree(s) still open (%s) — close them when you're ready; "
                             "the run carries on with #%s" % (", ".join(st["worktree"]["branch"] for st in pending),
                                                             focus_number))
    if not open_stories:
        # The integration PR matters only once every story has closed — read it then, not on every turn.
        integration, decision = build_unit(g, envelope, branches)
        if decision is not None:
            return None, decision
        epic["integration"] = {"number": (integration.get("pr") or {}).get("number"),
                               "draft": (integration.get("pr") or {}).get("draft"),
                               "gate": integration.get("gate")}
    return state, None


def _parent_epic_facts(root, parent_facts, branches):
    """A story's parent epic: its number, integration branch, worktree (if open) and whether that
    worktree is behind — the folder its plan grounds in."""
    if not parent_facts or not parent_facts.get("parent_epic"):
        return None
    parent = parent_facts["parent_epic"]
    if parent_facts.get("parent_kind") != branching.PARENT_KIND_EPIC:
        return None
    branch = ((parent_facts.get("branch_facts") or {}).get("branch"))
    worktree = None
    if branch and branch in branches:
        worktree = {"branch": branch, "path": str(workspace._work_path(root, branch))}
    return {"number": parent.get("number"), "title": parent.get("title"), "state": parent.get("state"),
            "branch": branch, "worktree": worktree, "behind": _behind(worktree) if worktree else False}


def _unit_attention(unit, attention):
    pr = unit.get("pr") or {}
    if pr.get("merged") and not unit.get("closed"):
        attention.append("PR #%s merged but #%s is still open — close the issue (the evaluator "
                         "normally does)" % (pr["number"], unit["number"]))


def _invoked_from(root):
    path = workspace.invoking_checkout(root)
    worktrees_dir = (Path(root) / ".worktrees").resolve()
    resolved = Path(path).resolve()
    inside = worktrees_dir in resolved.parents
    try:
        branch = workspace._current_branch(path)
    except RuntimeError:
        branch = None
    return {"path": str(resolved), "branch": branch, "is_root": str(resolved) == str(Path(root).resolve()),
            "inside_work_worktree": inside}


def _root_facts(root):
    """The main checkout's state. ``at_default_tip`` is tri-state: ``None`` when the remote tip
    can't be read (offline, no auth) — unknown, never reported as "behind"."""
    status, _notices, _decision = workspace._build_root_status(root)
    if status["branch"] != status["default_branch"]:
        at_tip = False
    else:
        tip = _remote_tip(root, status["default_branch"])
        at_tip = None if tip is None else tip == status["sha"]
    return {"path": root, "branch": status["branch"], "default_branch": status["default_branch"],
            "sha": status["sha"], "dirty": status["dirty"], "at_default_tip": at_tip}


def _sync_target(state):
    epic = state.get("epic") or {}
    if epic.get("worktree"):
        return epic["worktree"]
    return (state.get("parent_epic") or {}).get("worktree")


# ---------------------------------------------------------------------------
# The prep.
# ---------------------------------------------------------------------------


def build(issue_number, repo, root=".", scratch_dir=None, story=None, open_issue=None,
          close_branch=None, sync=False, cwd=None):
    """Run at most one action, then derive the stage. Returns ``(payload, notices, decision)`` —
    never emits; a core's decision is returned unchanged."""
    root = str(workspace._resolve_main_root(root))
    if scratch_dir is None:
        scratch_dir = "/tmp/gh-coordinator-%s" % issue_number
    Path(scratch_dir).mkdir(parents=True, exist_ok=True)
    g = _Gatherer(repo, root, scratch_dir, cwd)

    action = None
    if open_issue is not None:
        payload, notices, decision = prep_workspace_open.build_open(
            str(open_issue), repo, root=root, scratch_dir=str(Path(scratch_dir) / ("open-%s" % open_issue)), cwd=cwd,
        )
        if decision is not None:
            return None, notices, decision
        g.notices.extend(n for n in notices or [] if n not in g.notices)
        action = {"op": "open", "issue": int(open_issue), "receipt": payload,
                  "setup_failed": prep_workspace_open.setup_failed(payload)}
    elif close_branch is not None:
        payload, notices, decision = prep_workspace_close.build_close(
            close_branch, repo, root=root, scratch_dir=str(Path(scratch_dir) / "close"), cwd=cwd,
        )
        if decision is not None:
            return None, notices, decision
        g.notices.extend(n for n in notices or [] if n not in g.notices)
        action = {"op": "close", "branch": close_branch, "receipt": payload, "setup_failed": False}
    elif sync:
        state, decision = gather_state(g, issue_number, story=story)
        if decision is not None:
            return None, g.notices, decision
        target = _sync_target(state)
        if target is None:
            return None, g.notices, _no_sync_target(issue_number)
        payload, notices, decision = workspace._build_sync_work(root, target["branch"])
        if decision is not None:
            return None, notices, decision
        action = {"op": "sync", "branch": target["branch"], "receipt": payload,
                  "setup_failed": not payload["setup"]["succeeded"]}

    state, decision = gather_state(g, issue_number, story=story)
    if decision is not None:
        return None, g.notices, decision
    derived = derive_stage(state)
    story_stages = derive_story_stages(state)
    for entry in (state.get("epic") or {}).get("stories") or []:
        entry.update(story_stages.get(entry["number"]) or {})
    nxt = derived["next"]
    root_facts = _root_facts(root)
    invoked_from = _invoked_from(root)
    attention = state["attention"]
    if invoked_from["inside_work_worktree"]:
        attention.insert(0, "the coordinator is running inside the work worktree %s (%s) — every stage "
                         "runs from here except closing that worktree, which must run from the project "
                         "root %s" % (invoked_from["path"], invoked_from["branch"], root))
    if (nxt.get("kind") == NEXT_SESSION and nxt.get("folder") == root
            and nxt.get("skill") in ("planner", "slicer") and root_facts["at_default_tip"] is False):
        attention.append("the project root is on %s at %s, not the tip of origin/%s — a %s session "
                         "started there may refuse the checkout; update it first (the coordinator never "
                         "moves your checkout)" % (root_facts["branch"], root_facts["sha"][:7],
                                                   root_facts["default_branch"], nxt["skill"]))
    if action and action["setup_failed"]:
        attention.append("the %s ran but its worktree setup hook failed — the worktree is not ready"
                         % action["op"])

    target = state["target"]
    payload = {
        "repo": repo,
        "scratch": scratch_dir,
        "root": root_facts,
        "invoked_from": invoked_from,
        "target": target,
        "vector": {"type": target["type"]},
        "suggested_playbook": PLAYBOOK_EPIC if target["type"] == "epic" else PLAYBOOK_SINGLE,
        "stage": derived["stage"],
        "next": nxt,
        "focus": derived["focus"],
        "refusal": state.get("refusal"),
        "unit": state.get("focus_unit") or state.get("unit"),
        "epic": state.get("epic"),
        "parent_epic": state.get("parent_epic"),
        "routes": routes_for(state, derived["focus"]),
        "attention": attention,
    }
    gate = (payload["unit"] or {}).get("gate") or ((state.get("epic") or {}).get("integration") or {}).get("gate")
    if derived["stage"] == STAGE_GATED:
        payload["gate"] = gate
    if action is not None:
        payload["action"] = action
    return payload, g.notices, None


def _no_sync_target(issue_number):
    from pipelib.decisions import AMBIGUOUS, needs_decision

    return needs_decision(
        AMBIGUOUS,
        summary="#%s has no epic worktree open here — nothing to sync" % issue_number,
        context={"issue": issue_number},
        options=["open the epic's worktree first, then re-run"],
    )


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("issue", help="issue number")
    parser.add_argument("repo", help="owner/repo")
    parser.add_argument("--root", default=".", help="any checkout of the repo (normalized to the main checkout)")
    parser.add_argument("--scratch-dir", dest="scratch_dir", default=None)
    parser.add_argument("--story", type=int, default=None, help="the epic story to focus on")
    parser.add_argument("--cwd", default=None, help="explicit cwd for the gh calls (test injection)")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--open", dest="open_issue", type=int, default=None, metavar="N")
    actions.add_argument("--close", dest="close_branch", default=None, metavar="BRANCH")
    actions.add_argument("--sync", action="store_true")
    args = parser.parse_args(argv)
    if not args.issue.isdigit():
        parser.error("issue must be a number")

    payload, notices, decision = build(
        args.issue, args.repo, root=args.root, scratch_dir=args.scratch_dir, story=args.story,
        open_issue=args.open_issue, close_branch=args.close_branch, sync=args.sync, cwd=args.cwd,
    )
    if decision is not None:
        emit_needs_decision(decision, notices=notices)
        return EXIT_OK
    emit_ok(payload=payload, notices=notices)
    return 1 if (payload.get("action") or {}).get("setup_failed") else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
