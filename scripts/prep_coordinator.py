#!/usr/bin/env python3
"""prep_coordinator.py — where an issue stands in the pipeline, as one fact (#83; the
``coordinator`` standalone tool's prep, ``skills/coordinator/SKILL.md``).

"Facts by script, meaning by model", applied to orchestration. The coordinator never decides
from a session's note where an issue stands: every coordinator turn begins with this one call,
which derives the issue's ``stage`` from GitHub state plus the local work-worktree list and names
the next step — the command, and the absolute folder it runs in. The model writes the stage
brief, relays progress and renders cards; it never re-derives the stage.

Read-only by default. At most one **action** flag, passed when the coordinator runs that routine
step (an open or a sync on the operator's go-ahead naming it, a close once no stage runs there),
runs the matching workspace core in-process and then re-derives:

    --open <N>         prep_workspace_open.build_open     (the workspace-open tool's own core)
    --close <branch>   prep_workspace_close.build_close   (the workspace-close tool's own core)
    --sync             workspace._build_sync_work          (the epic worktree, fast-forward only)

A decision a core raises is emitted **unchanged**, so its card renders here exactly as in the
owning tool. State derivation never calls those cores: they act immediately with no dry-run, so
an action that ran during a read would be an open or close the coordinator never announced.

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
the story's own-branch facts through the same function a direct run on the story uses, and its
``set`` and ``start`` / ``build`` / ``merge`` gates from the epic plan's concurrency declaration
(:func:`concurrency_facts`). There is no single focus: an epic in its story phase is at
``stories``, and the coordinator acts on each entry.

Three key groups exist for the coordinator's board view (#116), which reads them and nothing
reads back: ``target`` carries the target's issue body (always spilled — ``body_bytes`` /
``body_mode`` / ``body_path`` — plus ``body_sha256``); every ``epic.stories`` entry carries its
``pr`` (``number`` / ``url`` / ``draft``, or ``None``); and (#156) every ``epic.stories`` entry and
``unit`` carry ``history``, the epic ``integration_history`` — timed points from one ``gh api
graphql`` read per run (:func:`_history_facts`, built by ``board_history``). Every envelope this
prep emits is also written atomically to ``<scratch>/envelope.json``, the board's feed — a cache of the last envelope, never a
source of truth; stdout stays the contract.

Usage::

    prep_coordinator.py <issue> <owner/repo> [--root PATH] [--scratch-dir DIR]
                        [--open N | --close BRANCH | --sync]

Exit codes (architecture.md §3): 0 with the envelope present (``ok`` or ``needs_decision``); 1
after an action whose worktree setup hook failed (the ok envelope still reports it, as the
workspace tools do); 2 on a usage error; any other non-zero is a hard `gh`/`git` failure.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import board_history  # noqa: E402  (the board's `history` fact, #156)
import branching  # noqa: E402
import concurrency  # noqa: E402
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
from pipelib import hashing, spill  # noqa: E402
from pipelib.decisions import AUTH_REQUIRED  # noqa: E402
from pipelib.envelope import (  # noqa: E402
    EXIT_OK, build_needs_decision, build_ok, emit, write_envelope,
)
from pipelib.thread import load_thread  # noqa: E402

RESEARCH_MARKER = "<!-- issue-research:v1 -->"

# The closed stage set. The router names every one of these (the routing test pins it against this
# tuple), and the board mod's rung legend reads them (a test pins its keys against this tuple too), so
# a new stage is a contract change in both places at once.
STAGE_REFUSED = "refused"
STAGE_GATED = "gated"
STAGE_NEEDS_SLICING = "needs-slicing"
STAGE_NEEDS_PLAN = "needs-plan"
STAGE_NEEDS_WORKSPACE = "needs-workspace"
STAGE_RESOLVING = "resolving"
STAGE_PR_READY = "pr-ready"
STAGE_MERGED_NEEDS_CLOSE = "merged-needs-close"
STAGE_STORIES = "stories"
STAGE_EPIC_INTEGRATION = "epic-integration"
STAGE_TERMINAL = "terminal"
STAGES = (
    STAGE_REFUSED, STAGE_GATED, STAGE_NEEDS_SLICING, STAGE_NEEDS_PLAN, STAGE_NEEDS_WORKSPACE,
    STAGE_RESOLVING, STAGE_PR_READY, STAGE_MERGED_NEEDS_CLOSE, STAGE_STORIES,
    STAGE_EPIC_INTEGRATION, STAGE_TERMINAL,
)

# Why a target is refused — facts the router renders as a card (the slicer's refusal pattern), not
# §3 decision codes: nothing here is a mechanical blocker a re-run resolves.
REFUSAL_QUESTION = "question"
REFUSAL_SLICE = "slice"
REFUSAL_OQ_BLOCKED = "oq-blocked"
REFUSALS = (REFUSAL_QUESTION, REFUSAL_SLICE, REFUSAL_OQ_BLOCKED)

# What the next step is: start a stage session, run a routine workspace step, act on every epic
# story's own entry, or nothing.
NEXT_SESSION = "session"
NEXT_OPEN = "open"
NEXT_CLOSE = "close"
NEXT_STORIES = "stories"
NEXT_NONE = "none"

PLAYBOOK_EPIC = "epic.md"
PLAYBOOK_SINGLE = "single.md"

# The board feed (#116): the last envelope this prep emitted, in its scratch dir.
FEED_FILE = "envelope.json"
NOTICE_BOARD_FEED_UNWRITTEN = "BOARD_FEED_UNWRITTEN"
# The board history read (#156): a failed read nulls every `history`; a connection past its page
# draws its first page. Notices, never decisions — the coordinator itself reads none of this.
NOTICE_BOARD_HISTORY_UNAVAILABLE = "BOARD_HISTORY_UNAVAILABLE"
NOTICE_BOARD_HISTORY_TRUNCATED = "BOARD_HISTORY_TRUNCATED"


class _DiscardStream:
    def write(self, _data):
        return 0

    def flush(self):
        return None


def default_scratch(issue):
    """The scratch dir a run without ``--scratch-dir`` uses — one place, so ``build`` and the
    feed file in ``main`` cannot drift apart."""
    return "/tmp/gh-coordinator-%s" % issue


def _command(skill, arg):
    return "/ctrl-freaq:%s %s" % (skill, arg)


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
        if pr.get("base_moved") is True:
            # A parallel story merged first: the gate the PR passed ran on a base it no longer has.
            return STAGE_RESOLVING, _session("resolver", "#%s" % unit["number"], worktree["path"],
                                             base_moved=True)
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


def _story_pr(unit):
    """An open story's PR for the board view: ``{number, url, draft}`` from its unit, else
    ``None``. Whether it merged is not repeated here: an open story's merged PR reads from its
    ``stage``, and whether a closed story merged stays ``epic.shipped``'s fact."""
    pr = (unit or {}).get("pr")
    if not pr:
        return None
    return {"number": pr["number"], "url": pr.get("url"), "draft": bool(pr.get("draft"))}


def derive_story_stages(state):
    """Every epic story's own ``{stage, next, pr}``, through :func:`derive_under_parent` with the
    epic facts as its parent — plus ``refusal`` when refused and ``gate`` when gated. ``pr`` is
    :func:`_story_pr` for an open story read with a PR, else ``None`` (a closed, gated-on-a-decision
    or refused story, or one without a PR). ``{}`` for a non-epic target.

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
                                  "gate": {"reason": decision.get("code"), "decision": decision},
                                  "pr": None}
                continue
            if record.get("refusal") is not None:
                stages[number] = {"stage": STAGE_REFUSED, "next": {"kind": NEXT_NONE},
                                  "refusal": record["refusal"], "pr": None}
                continue
            unit = record["unit"]
        stage, nxt = derive_under_parent(unit, epic, root)
        facts = {"stage": stage, "next": nxt, "pr": _story_pr(unit)}
        if stage == STAGE_GATED:
            facts["gate"] = unit.get("gate")
        stages[number] = facts
    return stages


def derive_stage(state):
    """The whole stage table over the gathered ``state`` (see :func:`gather_state`). Returns
    ``{"stage", "next", "focus"}`` — ``focus`` is the issue number the stage is about: the target,
    or ``None`` at ``stories``, where every epic story's entry carries its own stage."""
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

    # The story phase: no story is picked here. Each `epic.stories[]` entry carries its own `next`
    # (a close, an open, a start), and the coordinator reads them against their gates — a top-level
    # stage naming one story would be the script-side pick the epic plan's concurrency forbids.
    if any(not story.get("closed") or story.get("worktree") for story in stories):
        return {"stage": STAGE_STORIES, "next": {"kind": NEXT_STORIES}, "focus": None}

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


def _no_gates():
    return {"set": None, "start": None, "build": None, "merge": None}


def concurrency_facts(plan_text, story_numbers, shipped, closed=()):
    """The epic plan's concurrency declaration, as ``(epic_level, per_story, attention)``.

    ``epic_level`` is the reader's own ``source`` / ``fallback_reason`` / ``sets`` / ``malformed``
    values, never reshaped or filtered (a serial fallback is never swallowed), or ``None`` with no
    epic plan. ``per_story`` maps each story number to ``{set, start, build, merge}``: the reader's
    edges, each plus ``merged`` — ``True`` when its ``waits_on`` story is in ``shipped`` (the
    delivery log, the evaluator's record of the merge into the epic branch; a story closed as
    deferred never merged; one whose entry is duplicated is left out of ``shipped``, so it reads
    ``False`` and fails safe behind the log's own attention line), ``False`` when that story is in the
    story set without an entry, ``None``
    when it is not one of the epic's stories. A story the plan does not list, and every story when
    there is no plan, gets ``None`` for its set and all three gates — never empty lists, which would
    read as "waits on nothing". Attention lines are for open stories only (a ``closed`` one needs no
    start, so a line about its gates is noise): one per unlisted story, and one per edge that will
    hold with no merge coming — its ``waits_on`` story outside the story set, or closed but not
    shipped in the log (closed as deferred, or its entry duplicated), which holds the waiter until
    the log records it or the epic plan is revised; a conditional edge's line also names its
    condition, which the coordinator may settle as not holding. ``Condition`` text is
    passed through, never evaluated: there is no startable verdict here, and the coordinator judges
    what to offer from the edges."""
    if plan_text is None:
        return None, {number: _no_gates() for number in story_numbers}, []
    read = concurrency.read(plan_text)
    epic_level = {key: read[key] for key in ("source", "fallback_reason", "sets", "malformed")}
    members = set(story_numbers)
    shipped = set(shipped or [])
    closed = set(closed or [])
    listed = {entry["number"]: entry for entry in read["stories"]}
    per_story = {}
    attention = []
    unknown = set()
    for number in story_numbers:
        entry = listed.get(number)
        if entry is None:
            per_story[number] = _no_gates()
            if number in closed:
                continue
            attention.append("story #%s is not in the epic plan's `## Story breakdown` — it has no gates and "
                             "is offered no start until the epic plan is revised" % number)
            continue
        facts = {"set": entry["set"]}
        for gate in concurrency.GATES:
            edges = []
            for edge in entry[gate]:
                waits_on = edge["waits_on"]
                merged = True if waits_on in shipped else (False if waits_on in members else None)
                stuck = merged is None or (merged is False and waits_on in closed)
                if stuck and number not in closed and (number, waits_on) not in unknown:
                    unknown.add((number, waits_on))
                    if merged is None:
                        why = "which is not one of the epic's stories — that edge's merged state is unknown"
                    else:
                        why = ("which is closed but not shipped in the delivery log — the edge holds until the "
                               "log records it or the epic plan is revised")
                    if edge["condition"]:
                        # A conditional edge is also released once the coordinator settles its condition
                        # as not holding (epic.md §2) — the line must not say the plan is the only way out.
                        why += "; settling its condition as not holding releases it: %s" % edge["condition"]
                    attention.append("story #%s's `%s` gate waits on #%s, %s" % (number, gate, waits_on, why))
                edges.append(dict(edge, merged=merged))
            facts[gate] = edges
        per_story[number] = facts
    return epic_level, per_story, attention


def routes_for(state, focus):
    """Skill -> the absolute folder its session runs in, for the issue the stage is about — so a
    relayed handoff's command (a re-route the GitHub state cannot show) maps to a folder by script,
    not prose. ``None`` where that skill has no folder yet (no worktree open). At ``stories``
    (``focus`` ``None``) the planner is the epic worktree's and the resolver / evaluator have no
    single folder: a story's is its own entry's ``worktree.path``."""
    root = state["root_path"]
    epic = state.get("epic")
    parent = state.get("parent_epic") or {}
    epic_worktree = (epic or {}).get("worktree") or parent.get("worktree")
    plan_folder = root
    if epic is None:
        if epic_worktree:
            plan_folder = epic_worktree["path"]
        work_folder = ((state.get("unit") or {}).get("worktree") or {}).get("path")
    elif focus is None:
        if epic_worktree:
            plan_folder = epic_worktree["path"]
        work_folder = None
    else:
        work_folder = (epic.get("worktree") or {}).get("path")
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


def build_unit(g, envelope, branches, base_tip=None):
    """One issue's own-branch facts: plan, worktree, prior-PR row, PR state. Returns
    ``(unit, decision)``.

    ``base_tip`` is the remote tip of the epic branch the issue's PR bases on (``None`` for a
    standalone issue, or when it can't be read). An open, ready PR with a worktree gets
    ``pr.base_moved``: ``True`` when the worktree's HEAD lacks that tip — a parallel story merged
    first, so the gate the PR passed ran on a stale base — ``None`` when the tip is unknown. An
    unanswerable probe counts as moved: worktrees share one object store, so a tip that isn't local
    is a tip nobody here has merged."""
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
        elif not unit["pr"]["draft"] and unit["worktree"]:
            unit["pr"]["base_moved"] = (None if base_tip is None else
                                        workspace.contains_commit(unit["worktree"]["path"], base_tip) is not True)
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


def _epic_tip(g, worktree, branch):
    """The epic branch's remote tip: the one ``_behind`` already read for its worktree, else one
    ``ls-remote`` per run. ``None`` when there is no branch or the remote can't be read."""
    if worktree is not None:
        return worktree.get("remote_tip")
    if not branch:
        return None
    return g._memo(("tip", branch), lambda: (_remote_tip(g.root, branch), None))[0]


def gather_state(g, issue_number):
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
        "story_units": None,
        "story_concurrency": None,
        "parent_epic": None,
    }
    state["target"].update(_body_facts(g, envelope))
    refusal, parent_facts, decision = _refusal(g, envelope, issue_type)
    if decision is not None:
        return None, decision
    if refusal is not None:
        state["refusal"] = refusal
        return state, None

    if issue_type != "epic":
        # The parent epic first, so a story's ready PR is checked against the epic tip it bases on.
        parent = _parent_epic_facts(root, parent_facts, branches)
        tip = _epic_tip(g, parent["worktree"], parent["branch"]) if parent else None
        unit, decision = build_unit(g, envelope, branches, base_tip=tip)
        if decision is not None:
            return None, decision
        state["unit"] = unit
        state["parent_epic"] = parent
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
        "concurrency": None,
    }
    state["epic"] = epic
    epic["concurrency"], state["story_concurrency"], lines = concurrency_facts(
        _plan_text(envelope), [s["number"] for s in stories], epic["shipped"],
        closed=[s["number"] for s in stories if s["closed"]])
    attention.extend(lines)
    tip = _epic_tip(g, worktree, epic_branch)
    open_stories = [s for s in stories if not s["closed"]]
    # Every open story is read for its own stage (derive_story_stages); there is no focus story.
    # Only AUTH_REQUIRED stops the run — any other decision gates only that story's entry, and a
    # refusal refuses only its entry. A hard `gh` failure on any story still ends the prep (a
    # network or `gh` fault every other read would hit too).
    story_units = {}
    for entry in open_stories:
        n = entry["number"]
        record = {"unit": None, "refusal": None, "decision": None}
        story_envelope, decision = g.issue(n)
        if decision is None:
            record["refusal"], decision = _oq_refusal(g, story_envelope)
        if decision is None and record["refusal"] is None:
            record["unit"], decision = build_unit(g, story_envelope, branches, base_tip=tip)
        if decision is not None:
            if decision.get("code") == AUTH_REQUIRED:
                return None, decision
            record["decision"] = decision
            attention.append("story #%s could not be read (%s: %s) — its entry is gated on that decision"
                             % (n, decision.get("code"), decision.get("summary")))
        elif record["unit"] is not None:
            _unit_attention(record["unit"], attention)
        story_units[n] = record
    state["story_units"] = story_units
    if not open_stories:
        # The integration PR matters only once every story has closed — read it then, not on every turn.
        integration, decision = build_unit(g, envelope, branches)
        if decision is not None:
            return None, decision
        epic["integration"] = {"number": (integration.get("pr") or {}).get("number"),
                               "draft": (integration.get("pr") or {}).get("draft"),
                               "gate": integration.get("gate")}
    return state, None


def _body_facts(g, envelope):
    """The target's issue body for the board view's orientation paragraph: always spilled under
    ``<scratch>/target`` (the coordinator re-runs prep every turn and never reads the body, so it
    never sits inline in its context), plus ``body_sha256`` of the same bytes."""
    data = _text(envelope, "issue_body").encode("utf-8")
    facts = spill.spill_bytes(data, "body", str(Path(g.scratch_dir) / "target"), force_path=True)
    facts["body_sha256"] = hashing.sha256_hex(data)
    return facts


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


def _history_facts(g, state, root_facts):
    """The board's ``history`` (#156), set in place: on every ``epic.stories[]`` entry plus
    ``epic.integration_history``, or on ``unit`` — from **one** ``gh api graphql`` read
    (:mod:`board_history`), memoized so ``--sync`` reads once. A refused target carries none.

    The plan-posted time of an issue this run gathered comes from ``gh_gather``'s
    ``marker_comment_created_at``; one it did not (a closed story — :func:`gather_state` reads open
    stories only — or a story whose read raised a decision) from its comments in the same query.
    No ``data.repository`` makes every ``history`` and ``integration_history`` ``None`` with
    ``BOARD_HISTORY_UNAVAILABLE``; a connection past its page adds ``BOARD_HISTORY_TRUNCATED``. It
    never raises and never returns a decision: the board is a view, and must not break the run."""
    if state.get("refusal"):
        return
    epic = state.get("epic")
    unit = state.get("unit")
    if epic is None and unit is None:
        return
    gathered = g._issues

    def plan_at(number, node):
        envelope = gathered.get(number)
        if envelope is not None:
            return envelope.get("marker_comment_created_at") if envelope.get("marker_comment_present") else None
        return board_history.plan_created_at(((node or {}).get("comments") or {}).get("nodes"))

    def null_all():
        if epic is not None:
            for entry in epic.get("stories") or []:
                entry["history"] = None
            epic["integration_history"] = None
        else:
            unit["history"] = None
        if NOTICE_BOARD_HISTORY_UNAVAILABLE not in g.notices:
            g.notices.append(NOTICE_BOARD_HISTORY_UNAVAILABLE)

    try:
        if epic is not None:
            stories = epic.get("stories") or []
            numbers = [entry["number"] for entry in stories]
            branch = epic.get("branch")
            if not numbers and not branch:  # an unsliced epic: nothing to ask for
                epic["integration_history"] = []
                return
            query = board_history.build_query(
                g.repo, numbers, comment_issues=[n for n in numbers if n not in gathered],
                story_base=branch, integration_head=branch,
                default_branch=root_facts.get("default_branch") if branch else None)
        else:
            pr_number = (unit.get("pr") or {}).get("number")
            query = board_history.build_query(g.repo, [unit["number"]],
                                              pr_numbers=[pr_number] if pr_number else [])
        repository = g._memo(("history", query),
                             lambda: (board_history.read(g.repo, query, cwd=g.cwd), None))[0]
        if repository is None:
            null_all()
            return
        if board_history.truncated(repository) and NOTICE_BOARD_HISTORY_TRUNCATED not in g.notices:
            g.notices.append(NOTICE_BOARD_HISTORY_TRUNCATED)

        if epic is None:
            node = repository.get("i%d" % unit["number"])
            pr = repository.get("p%d" % pr_number) if pr_number else None
            unit["history"] = (board_history.points(node, pr if isinstance(pr, dict) else None,
                                                    plan_at(unit["number"], node))
                               if isinstance(node, dict) else [])
            return

        prs = (repository.get("stories") or {}).get("nodes") or []
        story_prs = {n: board_history.story_pr(prs, n) for n in numbers}
        for entry in stories:
            number = entry["number"]
            node = repository.get("i%d" % number)
            if not isinstance(node, dict):
                entry["history"] = []
                continue
            waited = [(story_prs.get(edge.get("waits_on")) or {}).get("mergedAt")
                      for edge in entry.get("start") or []]
            entry["history"] = board_history.points(node, story_prs[number], plan_at(number, node), waited)
        epic["integration_history"] = board_history.integration_points(
            board_history.integration_pr((repository.get("integration") or {}).get("nodes")))
    except Exception as exc:  # the board must never break the coordinator's run
        sys.stderr.write("prep_coordinator: board history not built: %s\n" % exc)
        null_all()


def _sync_target(state):
    epic = state.get("epic") or {}
    if epic.get("worktree"):
        return epic["worktree"]
    return (state.get("parent_epic") or {}).get("worktree")


# ---------------------------------------------------------------------------
# The prep.
# ---------------------------------------------------------------------------


def build(issue_number, repo, root=".", scratch_dir=None, open_issue=None,
          close_branch=None, sync=False, cwd=None):
    """Run at most one action, then derive the stage. Returns ``(payload, notices, decision)`` —
    never emits; a core's decision is returned unchanged."""
    root = str(workspace._resolve_main_root(root))
    if scratch_dir is None:
        scratch_dir = default_scratch(issue_number)
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
        state, decision = gather_state(g, issue_number)
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

    state, decision = gather_state(g, issue_number)
    if decision is not None:
        return None, g.notices, decision
    derived = derive_stage(state)
    story_stages = derive_story_stages(state)
    story_concurrency = state.get("story_concurrency") or {}
    for entry in (state.get("epic") or {}).get("stories") or []:
        entry.update(story_stages.get(entry["number"]) or {})
        entry.update(story_concurrency.get(entry["number"]) or _no_gates())
    nxt = derived["next"]
    root_facts = _root_facts(root)
    _history_facts(g, state, root_facts)
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
        "unit": state.get("unit"),
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
    parser.add_argument("--cwd", default=None, help="explicit cwd for the gh calls (test injection)")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--open", dest="open_issue", type=int, default=None, metavar="N")
    actions.add_argument("--close", dest="close_branch", default=None, metavar="BRANCH")
    actions.add_argument("--sync", action="store_true")
    args = parser.parse_args(argv)
    if not args.issue.isdigit():
        parser.error("issue must be a number")

    # The board feed: unlinked before `build`, so a hard failure inside it leaves no file for the
    # board to read as this run's facts (it keeps showing the last run it read). Best effort, as a cache: a usage error
    # exits before this, an unlink that fails is ignored, and the file is written just before stdout.
    feed = Path(args.scratch_dir or default_scratch(args.issue)) / FEED_FILE
    try:
        feed.unlink(missing_ok=True)
    except OSError:
        pass
    payload, notices, decision = build(
        args.issue, args.repo, root=args.root, scratch_dir=args.scratch_dir,
        open_issue=args.open_issue, close_branch=args.close_branch, sync=args.sync, cwd=args.cwd,
    )
    if decision is not None:
        envelope = build_needs_decision(decision, notices=notices)
    else:
        envelope = build_ok(payload=payload, notices=notices)
    try:
        write_envelope(envelope, str(feed))
    except OSError as exc:
        envelope["notices"].append(NOTICE_BOARD_FEED_UNWRITTEN)
        sys.stderr.write("prep_coordinator: board feed not written: %s\n" % exc)
    emit(envelope)
    if decision is not None:
        return EXIT_OK
    return 1 if (payload.get("action") or {}).get("setup_failed") else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
