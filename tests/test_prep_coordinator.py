"""Unit tests for scripts/prep_coordinator.py — the coordinator's one prep (#83).

Two layers. The stage table is `prep_coordinator.derive_stage`, a pure function over gathered
state, so `StageTableTests` pins every row across epic / story / standalone / refused targets in
process, with no fixtures. The end-to-end tests drive the prep as a real subprocess — `gh` through
the offline shim, `git` through a real temp origin+clone — for what only a run can show: the one
envelope, the read-only default, the action flags composing the workspace tools' own cores (a core
decision passed through unchanged, a clean action that raises no card), the epic worktree's
behind-and-sync round trip, and the "running inside a work worktree" warning.
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "prep_coordinator.py"
OPEN_SCRIPT = SCRIPTS_DIR / "prep_workspace_open.py"

sys.path.insert(0, str(SCRIPTS_DIR))

import prep_coordinator as pc  # noqa: E402
from tests.support import envelope_asserts, gitsandbox, shimenv  # noqa: E402

ROOT = "/repo"
EPIC_WT = {"branch": "epic/150-inbox", "path": "/repo/.worktrees/epic/150-inbox"}


def _wt(branch):
    return {"branch": branch, "path": "/repo/.worktrees/%s" % branch}


def _unit(number=100, **kw):
    unit = {"number": number, "closed": False, "plan_present": True, "worktree": None, "pr": None, "gate": None}
    unit.update(kw)
    return unit


def _standalone(unit, type_="standard", parent=None, refusal=None):
    return {"root_path": ROOT, "target": {"number": unit["number"], "type": type_},
            "refusal": refusal, "epic": None, "unit": unit, "parent_epic": parent}


def _epic(**kw):
    epic = {"number": 150, "closed": False, "plan_present": True, "worktree": EPIC_WT, "behind": False,
            "stories": [{"number": 151, "closed": True, "worktree": None},
                        {"number": 152, "closed": False, "worktree": None}],
            "integration": {"number": None, "draft": None, "gate": None}}
    epic.update(kw)
    return {"root_path": ROOT, "target": {"number": 150, "type": "epic"}, "refusal": None,
            "epic": epic, "unit": None, "parent_epic": None}


class StageTableTests(unittest.TestCase):
    seen = set()

    def _derive(self, state, stage, kind=None, **expect):
        derived = pc.derive_stage(state)
        self.assertEqual(derived["stage"], stage, derived)
        StageTableTests.seen.add(stage)
        if kind is not None:
            self.assertEqual(derived["next"]["kind"], kind)
        for key, value in expect.items():
            self.assertEqual(derived["next"].get(key), value, (key, derived))
        return derived

    # -- standalone ----------------------------------------------------------------------------

    def test_refused_target(self):
        state = _standalone(_unit(), refusal={"reason": pc.REFUSAL_QUESTION})
        self._derive(state, pc.STAGE_REFUSED, pc.NEXT_NONE)

    def test_no_plan_plans_in_the_project_root(self):
        self._derive(_standalone(_unit(plan_present=False)), pc.STAGE_NEEDS_PLAN, pc.NEXT_SESSION,
                     command="/github-pipeline:planner #100", folder=ROOT)

    def test_planned_without_a_worktree_opens_one(self):
        self._derive(_standalone(_unit()), pc.STAGE_NEEDS_WORKSPACE, pc.NEXT_OPEN, issue=100)

    def test_worktree_and_no_pr_is_resolving_in_the_worktree(self):
        wt = _wt("100-fix")
        self._derive(_standalone(_unit(worktree=wt)), pc.STAGE_RESOLVING, pc.NEXT_SESSION,
                     command="/github-pipeline:resolver #100", folder=wt["path"])

    def test_a_draft_pr_is_a_resolver_re_entry(self):
        """A checkpoint pause, a soft-reject, or an unfinished phase all leave a draft: re-enter."""
        unit = _unit(worktree=_wt("100-fix"), pr={"number": 7, "draft": True, "merged": False})
        self._derive(_standalone(unit), pc.STAGE_RESOLVING, command="/github-pipeline:resolver #100")

    def test_a_ready_pr_is_the_evaluators_and_names_the_pr(self):
        wt = _wt("100-fix")
        unit = _unit(worktree=wt, pr={"number": 7, "draft": False, "merged": False})
        self._derive(_standalone(unit), pc.STAGE_PR_READY, pc.NEXT_SESSION,
                     command="/github-pipeline:evaluator #7", folder=wt["path"])

    def test_a_ready_pr_whose_base_moved_goes_back_to_the_resolver(self):
        """A parallel story merged into the epic branch first: the PR's gate ran on a stale base."""
        wt = _wt("100-fix")
        unit = _unit(worktree=wt, pr={"number": 7, "draft": False, "merged": False, "base_moved": True})
        self._derive(_standalone(unit), pc.STAGE_RESOLVING, pc.NEXT_SESSION,
                     command="/github-pipeline:resolver #100", folder=wt["path"], base_moved=True)

    def test_a_ready_pr_on_an_unmoved_or_unknown_base_is_the_evaluators(self):
        for moved in (False, None):
            unit = _unit(worktree=_wt("100-fix"), pr={"number": 7, "draft": False, "merged": False,
                                                      "base_moved": moved})
            derived = self._derive(_standalone(unit), pc.STAGE_PR_READY, command="/github-pipeline:evaluator #7")
            self.assertNotIn("base_moved", derived["next"])

    def test_closed_with_a_worktree_closes_it(self):
        unit = _unit(closed=True, worktree=_wt("100-fix"))
        self._derive(_standalone(unit), pc.STAGE_MERGED_NEEDS_CLOSE, pc.NEXT_CLOSE, branch="100-fix",
                     command="/github-pipeline:workspace-close 100-fix")

    def test_merged_pr_on_an_open_issue_still_releases_the_worktree(self):
        unit = _unit(worktree=_wt("100-fix"), pr={"number": 7, "draft": False, "merged": True})
        self._derive(_standalone(unit), pc.STAGE_MERGED_NEEDS_CLOSE, pc.NEXT_CLOSE)

    def test_merged_pr_with_the_worktree_released_is_terminal_not_a_re_open(self):
        """Merged work never gets a fresh worktree: the open issue rides attention instead."""
        unit = _unit(worktree=None, pr={"number": 7, "draft": False, "merged": True})
        self._derive(_standalone(unit), pc.STAGE_TERMINAL, pc.NEXT_NONE)

    def test_closed_and_released_is_terminal(self):
        self._derive(_standalone(_unit(closed=True)), pc.STAGE_TERMINAL, pc.NEXT_NONE)

    def test_someone_elses_pr_gates(self):
        unit = _unit(gate={"reason": "open-pr-other-active"})
        self._derive(_standalone(unit), pc.STAGE_GATED, pc.NEXT_NONE)

    # -- a story invoked directly --------------------------------------------------------------

    def test_a_story_whose_epic_has_no_worktree_opens_the_epic_first(self):
        parent = {"number": 150, "branch": "epic/150-inbox", "worktree": None, "behind": False}
        state = _standalone(_unit(152, plan_present=False), type_="story", parent=parent)
        self._derive(state, pc.STAGE_NEEDS_WORKSPACE, pc.NEXT_OPEN, issue=150)

    def test_a_story_plans_in_its_epic_worktree_syncing_first_when_behind(self):
        parent = {"number": 150, "branch": "epic/150-inbox", "worktree": EPIC_WT, "behind": True}
        state = _standalone(_unit(152, plan_present=False), type_="story", parent=parent)
        self._derive(state, pc.STAGE_NEEDS_PLAN, folder=EPIC_WT["path"], sync_first=True)
        self.assertEqual(pc.routes_for(state, 152)["planner"], EPIC_WT["path"])

    def test_a_merged_story_is_terminal_for_a_direct_run(self):
        parent = {"number": 150, "branch": "epic/150-inbox", "worktree": EPIC_WT, "behind": False}
        self._derive(_standalone(_unit(152, closed=True), type_="story", parent=parent), pc.STAGE_TERMINAL)

    # -- epic ----------------------------------------------------------------------------------

    def test_epic_without_stories_slices_first(self):
        self._derive(_epic(stories=[], plan_present=False), pc.STAGE_NEEDS_SLICING, pc.NEXT_SESSION,
                     command="/github-pipeline:slicer #150", folder=ROOT)

    def test_epic_without_a_plan_plans_in_the_root(self):
        self._derive(_epic(plan_present=False), pc.STAGE_NEEDS_PLAN,
                     command="/github-pipeline:planner #150", folder=ROOT)

    def test_planned_epic_without_a_worktree_opens_it(self):
        self._derive(_epic(worktree=None), pc.STAGE_NEEDS_WORKSPACE, pc.NEXT_OPEN, issue=150)

    def test_open_stories_put_the_epic_at_stories_with_no_focus(self):
        """No script-side pick: every story's entry carries its own `next`."""
        state = _epic()
        derived = self._derive(state, pc.STAGE_STORIES, pc.NEXT_STORIES)
        self.assertIsNone(derived["focus"])
        routes = pc.routes_for(state, None)
        self.assertEqual(routes["planner"], EPIC_WT["path"])
        self.assertIsNone(routes["resolver"])
        self.assertIsNone(routes["evaluator"])

    def test_a_closed_story_still_holding_a_worktree_keeps_the_epic_at_stories(self):
        """Its close comes from its own entry, so the integration route waits for the release."""
        stories = [{"number": 151, "closed": True, "worktree": _wt("151-sender")},
                   {"number": 152, "closed": True, "worktree": None}]
        derived = self._derive(_epic(stories=stories), pc.STAGE_STORIES, pc.NEXT_STORIES)
        self.assertIsNone(derived["focus"])

    def test_every_story_closed_and_released_reaches_the_integration(self):
        stories = [{"number": 151, "closed": True, "worktree": None},
                   {"number": 152, "closed": True, "worktree": None}]
        self._derive(_epic(stories=stories), pc.STAGE_EPIC_INTEGRATION, command="/github-pipeline:resolver #150")

    def test_every_story_closed_and_a_draft_integration_pr_is_the_epic_route(self):
        """The integration PR is a draft while stories land, so draft here is not 'resolving' a
        story — it is the resolver's epic route, in the epic worktree."""
        stories = [{"number": 151, "closed": True, "worktree": None}]
        state = _epic(stories=stories, integration={"number": 170, "draft": True, "gate": None})
        self._derive(state, pc.STAGE_EPIC_INTEGRATION, pc.NEXT_SESSION,
                     command="/github-pipeline:resolver #150", folder=EPIC_WT["path"])

    def test_a_ready_integration_pr_is_the_evaluators(self):
        stories = [{"number": 151, "closed": True, "worktree": None}]
        state = _epic(stories=stories, integration={"number": 170, "draft": False, "gate": None})
        self._derive(state, pc.STAGE_PR_READY, command="/github-pipeline:evaluator #170", folder=EPIC_WT["path"])

    def test_a_foreign_integration_pr_gates(self):
        stories = [{"number": 151, "closed": True, "worktree": None}]
        state = _epic(stories=stories, integration={"number": 170, "draft": False, "gate": {"reason": "x"}})
        self._derive(state, pc.STAGE_GATED)

    def test_a_closed_epic_closes_its_worktree_then_ends(self):
        self._derive(_epic(closed=True), pc.STAGE_MERGED_NEEDS_CLOSE, branch=EPIC_WT["branch"])
        self._derive(_epic(closed=True, worktree=None), pc.STAGE_TERMINAL)

    @classmethod
    def tearDownClass(cls):
        missing = set(pc.STAGES) - cls.seen
        if missing:
            raise AssertionError("stage-table rows with no test: %s" % sorted(missing))


def _record(unit=None, refusal=None, decision=None):
    return {"unit": unit, "refusal": refusal, "decision": decision}


class StoryStageTests(unittest.TestCase):
    """`derive_story_stages`: every epic story's own stage, the answer a direct run on that story
    would give (`derive_under_parent` with the epic facts as parent)."""

    DECISION = {"code": "MARKER_AMBIGUOUS", "summary": "two plan markers", "context": {}, "options": []}

    def _stages(self, stories, records, **epic_kw):
        epic_kw.setdefault("branch", EPIC_WT["branch"])  # gather_state's epic facts always name it
        state = _epic(stories=stories, **epic_kw)
        state["story_units"] = records
        return state, pc.derive_story_stages(state)

    def _assert(self, facts, stage, kind, **expect):
        self.assertEqual(facts["stage"], stage, facts)
        self.assertEqual(facts["next"]["kind"], kind, facts)
        for key, value in expect.items():
            self.assertEqual(facts["next"].get(key), value, (key, facts))

    def test_closed_stories_close_their_worktree_or_are_terminal(self):
        stories = [{"number": 151, "closed": True, "worktree": _wt("151-sender")},
                   {"number": 152, "closed": True, "worktree": None}]
        _state, stages = self._stages(stories, {})
        self._assert(stages[151], pc.STAGE_MERGED_NEEDS_CLOSE, pc.NEXT_CLOSE, branch="151-sender")
        self._assert(stages[152], pc.STAGE_TERMINAL, pc.NEXT_NONE)

    def test_open_stories_each_get_their_own_stage(self):
        wt4, wt5, wt6 = _wt("154-archive"), _wt("155-snooze"), _wt("156-mute")
        units = {
            152: _unit(152, plan_present=False),
            153: _unit(153),
            154: _unit(154, worktree=wt4),
            155: _unit(155, worktree=wt5, pr={"number": 165, "draft": True, "merged": False}),
            156: _unit(156, worktree=wt6, pr={"number": 166, "draft": False, "merged": False}),
            157: _unit(157, pr={"number": 167, "draft": False, "merged": True}),
        }
        stories = [{"number": n, "closed": False, "worktree": u["worktree"]} for n, u in units.items()]
        _state, stages = self._stages(stories, {n: _record(u) for n, u in units.items()}, behind=True)
        self._assert(stages[152], pc.STAGE_NEEDS_PLAN, pc.NEXT_SESSION, command="/github-pipeline:planner #152",
                     folder=EPIC_WT["path"], sync_first=True)
        self._assert(stages[153], pc.STAGE_NEEDS_WORKSPACE, pc.NEXT_OPEN, issue=153)
        self._assert(stages[154], pc.STAGE_RESOLVING, pc.NEXT_SESSION, command="/github-pipeline:resolver #154",
                     folder=wt4["path"])
        self._assert(stages[155], pc.STAGE_RESOLVING, pc.NEXT_SESSION, command="/github-pipeline:resolver #155",
                     folder=wt5["path"])
        self._assert(stages[156], pc.STAGE_PR_READY, pc.NEXT_SESSION, command="/github-pipeline:evaluator #166",
                     folder=wt6["path"])
        self._assert(stages[157], pc.STAGE_TERMINAL, pc.NEXT_NONE)
        for facts in stages.values():
            self.assertNotIn("gate", facts)
            self.assertNotIn("refusal", facts)

    def test_a_gated_story_carries_the_units_gate(self):
        gate = {"reason": "open-pr-other-active", "header": "PR", "options": [], "prior_pr": {"number": 9}}
        unit = _unit(152, gate=gate)
        _state, stages = self._stages([{"number": 152, "closed": False, "worktree": None}], {152: _record(unit)})
        self._assert(stages[152], pc.STAGE_GATED, pc.NEXT_NONE)
        self.assertEqual(stages[152]["gate"], gate)

    def test_a_refused_story_carries_its_refusal(self):
        refusal = {"reason": pc.REFUSAL_OQ_BLOCKED, "issue": 152, "context": {"blocking": []}}
        _state, stages = self._stages([{"number": 152, "closed": False, "worktree": None}],
                                      {152: _record(refusal=refusal)})
        self._assert(stages[152], pc.STAGE_REFUSED, pc.NEXT_NONE)
        self.assertEqual(stages[152]["refusal"], refusal)

    def test_an_unreadable_story_is_gated_on_its_decision(self):
        _state, stages = self._stages([{"number": 155, "closed": False, "worktree": None}],
                                      {155: _record(decision=self.DECISION)})
        self._assert(stages[155], pc.STAGE_GATED, pc.NEXT_NONE)
        self.assertEqual(stages[155]["gate"], {"reason": "MARKER_AMBIGUOUS", "decision": self.DECISION})

    def test_an_epic_branch_without_a_worktree_opens_the_epic_for_an_unplanned_story(self):
        stories = [{"number": 152, "closed": False, "worktree": None}]
        _state, stages = self._stages(stories, {152: _record(_unit(152, plan_present=False))},
                                      worktree=None, branch="epic/150-inbox")
        self._assert(stages[152], pc.STAGE_NEEDS_WORKSPACE, pc.NEXT_OPEN, issue=150)

    def test_no_epic_branch_plans_an_unplanned_story_in_the_root(self):
        stories = [{"number": 152, "closed": False, "worktree": None}]
        _state, stages = self._stages(stories, {152: _record(_unit(152, plan_present=False))},
                                      worktree=None, branch=None)
        self._assert(stages[152], pc.STAGE_NEEDS_PLAN, pc.NEXT_SESSION, folder=ROOT)

    def test_a_non_epic_target_has_no_story_stages(self):
        self.assertEqual(pc.derive_story_stages(_standalone(_unit())), {})

    def _mixed_epic(self):
        wt = _wt("153-tags")
        units = {152: _unit(152, plan_present=False), 153: _unit(153, worktree=wt),
                 154: _unit(154, worktree=_wt("154-a"), pr={"number": 164, "draft": False, "merged": False}),
                 160: _unit(160, worktree=_wt("160-b"), pr={"number": 170, "draft": False, "merged": False,
                                                            "base_moved": True}),
                 156: _unit(156, pr={"number": 166, "draft": False, "merged": True}),
                 157: _unit(157, gate={"reason": "open-pr-other-stale"})}
        records = {n: _record(u) for n, u in units.items()}
        records[158] = _record(refusal={"reason": pc.REFUSAL_OQ_BLOCKED, "issue": 158, "context": {}})
        records[159] = _record(decision=self.DECISION)
        stories = ([{"number": 151, "closed": True, "worktree": _wt("151-sender")},
                    {"number": 155, "closed": True, "worktree": None}]
                   + [{"number": n, "closed": False, "worktree": (r["unit"] or {}).get("worktree")}
                      for n, r in records.items()])
        return stories, records

    def test_each_entry_equals_a_direct_run_on_that_story(self):
        """The "same derivation" clause, at the table: given the same unit and the epic facts as
        parent, an entry is what `derive_stage` says for a direct-story target — so the entry's
        closed / refused / decision branching never forks from the shared derivation. (Whether the
        gathered unit matches a direct run's gather is `EpicInFlightTests`' and the reads' concern.) The unreadable sibling is the one
        intended divergence, so it is left out. A closed story is compared with no refusal: its
        entry is never gathered, so no open-question gate is read for it (the plan's choice — a
        remote read that cannot change a closed story's stage of release-or-done)."""
        stories, records = self._mixed_epic()
        for behind in (False, True):
            state, stages = self._stages(stories, records, behind=behind)
            parent = {k: state["epic"][k] for k in ("number", "branch", "worktree", "behind")}
            for story in stories:
                number = story["number"]
                record = records.get(number) or {}
                if record.get("decision") is not None:
                    continue
                unit = record.get("unit") or {"number": number, "closed": True, "worktree": story["worktree"]}
                direct = _standalone(unit, type_="story", parent=parent, refusal=record.get("refusal"))
                expected = pc.derive_stage(direct)
                self.assertEqual((stages[number]["stage"], stages[number]["next"]),
                                 (expected["stage"], expected["next"]), number)

    def test_each_entry_carries_its_pr_or_none(self):
        """#116's board keys: an open story read with a PR carries `{number, url, draft}` from its
        unit (the `_mixed_epic` units carry no url); every other entry carries `None`."""
        stories, records = self._mixed_epic()
        _state, stages = self._stages(stories, records)
        self.assertEqual({n: f["pr"] for n, f in stages.items()},
                         {151: None, 155: None, 152: None, 153: None, 157: None, 158: None, 159: None,
                          154: {"number": 164, "url": None, "draft": False},
                          160: {"number": 170, "url": None, "draft": False},
                          156: {"number": 166, "url": None, "draft": False}})

    def test_a_base_moved_entry_is_a_resolver_re_entry(self):
        stories, records = self._mixed_epic()
        _state, stages = self._stages(stories, records)
        self._assert(stages[160], pc.STAGE_RESOLVING, pc.NEXT_SESSION, command="/github-pipeline:resolver #160",
                     base_moved=True)
        self._assert(stages[154], pc.STAGE_PR_READY, pc.NEXT_SESSION, command="/github-pipeline:evaluator #164")


class BuildUnitBaseMovedTests(unittest.TestCase):
    """`build_unit`'s `pr.base_moved`: only an open, ready PR with a worktree gets it; an unreadable
    tip is `None` (unknown), and an unanswerable probe counts as moved."""

    def _unit(self, tip, contains=None, draft=False, worktree=True):
        fact = {"number": 160, "isDraft": draft, "headRefName": "152-date", "url": "u"}

        class G:
            root = ROOT

            def current_user(self):
                return "alice", None
        patches = [
            mock.patch.object(pc.branching, "partition_prs_for_issue", return_value=([fact], [])),
            mock.patch.object(pc.branching, "classify_prior_pr_row", return_value=("open-pr-yours", fact)),
            mock.patch.object(pc, "load_thread", return_value=[]),
            mock.patch.object(pc, "_worktree_for", return_value=_wt("152-date") if worktree else None),
            mock.patch.object(pc.workspace, "contains_commit", return_value=contains),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        unit, decision = pc.build_unit(G(), {"number": 152, "state": "OPEN"}, [], base_tip=tip)
        self.assertIsNone(decision)
        return unit["pr"]

    def test_an_unreadable_tip_is_unknown_not_moved(self):
        self.assertIsNone(self._unit(None)["base_moved"])

    def test_the_probe_decides_moved(self):
        self.assertIs(self._unit("abc", contains=True)["base_moved"], False)
        self.assertIs(self._unit("abc", contains=False)["base_moved"], True)
        self.assertIs(self._unit("abc", contains=None)["base_moved"], True, "an unanswerable probe counts as moved")

    def test_a_draft_or_a_pr_without_a_worktree_has_no_base_moved(self):
        self.assertNotIn("base_moved", self._unit("abc", contains=False, worktree=False))
        self.assertNotIn("base_moved", self._unit("abc", contains=False, draft=True))


class _StubGatherer:
    def __init__(self, decisions):
        self.root = ROOT
        self.decisions = decisions

    def _memo(self, _key, fetch):
        return fetch()

    def issue(self, number):
        number = int(number)
        if number in self.decisions:
            return None, self.decisions[number]
        labels = [{"name": "epic"}] if number == 150 else [{"name": "story"}]
        return {"number": number, "title": "#%s" % number, "state": "OPEN", "labels": labels}, None


class GatherStoryUnitsTests(unittest.TestCase):
    """`gather_state`'s per-story read: there is no focus story, so only `AUTH_REQUIRED` stops the
    run; any other decision gates only its story's entry, and a refusal refuses only its entry. The
    reads are stubbed; the policy is what is under test."""

    STORIES = [{"number": 151, "title": "a", "closed": True, "worktree": None},
               {"number": 152, "title": "b", "closed": False, "worktree": None},
               {"number": 153, "title": "c", "closed": False, "worktree": None}]

    def _gather(self, decisions=None, refused=(), units=None):
        refusals = {n: {"reason": pc.REFUSAL_OQ_BLOCKED, "issue": n, "context": {}} for n in refused}
        units = units or {}
        patches = [
            mock.patch.object(pc.workspace, "list_work_branches", return_value=["epic/150-inbox"]),
            mock.patch.object(pc, "_behind", return_value=False),
            mock.patch.object(pc, "_story_set", return_value=([dict(s) for s in self.STORIES], "native")),
            mock.patch.object(pc, "load_thread", return_value=[]),
            mock.patch.object(pc, "_body_facts", return_value={}),
            mock.patch.object(pc, "_refusal", return_value=(None, None, None)),
            mock.patch.object(pc, "_oq_refusal", side_effect=lambda g, env: (refusals.get(int(env["number"])), None)),
            mock.patch.object(pc, "build_unit", side_effect=lambda g, env, branches, base_tip=None: (
                units.get(int(env["number"])) or _unit(int(env["number"])), None)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return pc.gather_state(_StubGatherer(decisions or {}), 150)

    def test_a_storys_decision_gates_only_its_entry(self):
        decision = {"code": "MARKER_AMBIGUOUS", "summary": "dup", "context": {}, "options": []}
        state, err = self._gather(decisions={152: decision})
        self.assertIsNone(err)
        self.assertEqual(state["story_units"][152]["decision"], decision)
        self.assertEqual(state["story_units"][153]["unit"]["number"], 153)
        self.assertTrue(any(line.startswith("story #152 could not be read (MARKER_AMBIGUOUS: dup)")
                            for line in state["attention"]), state["attention"])

    def test_only_an_auth_failure_stops_the_run(self):
        auth = {"code": "AUTH_REQUIRED", "summary": "gh auth", "context": {}, "options": []}
        self.assertEqual(self._gather(decisions={153: auth}), (None, auth))

    def test_a_refusal_refuses_only_its_entry(self):
        state, _err = self._gather(refused=(152,))
        self.assertIsNone(state["refusal"])
        self.assertEqual(state["story_units"][152]["refusal"]["issue"], 152)
        self.assertEqual(pc.derive_story_stages(state)[152]["stage"], pc.STAGE_REFUSED)

    def test_an_open_story_whose_pr_merged_says_so_with_no_focus(self):
        merged = _unit(153, pr={"number": 163, "draft": False, "merged": True})
        state, _err = self._gather(units={153: merged})
        self.assertIn("PR #163 merged but #153 is still open — close the issue (the evaluator normally does)",
                      state["attention"])

    def test_closed_stories_are_never_read(self):
        state, _err = self._gather()
        self.assertEqual(sorted(state["story_units"]), [152, 153])

    def test_no_epic_plan_reads_no_gates(self):
        state, _err = self._gather()
        self.assertIsNone(state["epic"]["concurrency"])
        self.assertEqual(state["story_concurrency"][152],
                         {"set": None, "start": None, "build": None, "merge": None})


class ConcurrencyFactsTests(unittest.TestCase):
    """`concurrency_facts`: the reader's values passed through, each edge's `merged` from the
    delivery log, and `None` gates wherever there is nothing to read — never empty lists."""

    BREAKDOWN = "## Story breakdown\n- #201 a\n- #202 b\n- #203 c\n"
    DECLARED = (BREAKDOWN + "\n## Integration strategy\n\n### Dependency graph\n\n"
                "| Story | Waits on | Kind | Gates | Condition |\n|---|---|---|---|---|\n"
                "| #201 | — | — | — | — |\n"
                "| #202 | #201 | file overlap `scripts/x.py` | build | #202 edits the layout |\n"
                "| #203 | #201 | contract `parser core` | start | — |\n"
                "| #203 | #202 | merge order | merge | — |\n"
                "\n### Concurrent sets\n\n- **Wave 1: #201 and #202.** First.\n- **Wave 2: #203.** Then.\n")

    def _facts(self, plan, numbers=(201, 202, 203), shipped=()):
        reads = []
        real = pc.concurrency.read

        def spy(text):
            reads.append(real(text))
            return reads[-1]
        with mock.patch.object(pc.concurrency, "read", side_effect=spy):
            facts = pc.concurrency_facts(plan, list(numbers), list(shipped))
        return facts, (reads[0] if reads else None)

    def test_a_declared_plan_passes_the_readers_values_through(self):
        (epic_level, per_story, attention), read = self._facts(self.DECLARED)
        self.assertEqual(set(epic_level), {"source", "fallback_reason", "sets", "malformed"})
        self.assertEqual(epic_level["source"], "declared")
        self.assertIsNone(epic_level["fallback_reason"])
        self.assertIs(epic_level["sets"], read["sets"])
        self.assertIs(epic_level["malformed"], read["malformed"])
        self.assertEqual(attention, [])
        self.assertEqual(per_story[201], {"set": "Wave 1", "start": [], "build": [], "merge": []})
        self.assertEqual(per_story[202]["set"], "Wave 1")
        self.assertEqual(per_story[202]["build"], [{"waits_on": 201, "kind": "file overlap", "contracts": [],
                                                    "surface": "scripts/x.py", "condition": "#202 edits the layout",
                                                    "merged": False}])
        self.assertEqual(per_story[203]["start"], [{"waits_on": 201, "kind": "contract", "contracts": ["parser core"],
                                                    "surface": None, "condition": None, "merged": False}])
        self.assertEqual([e["waits_on"] for e in per_story[203]["merge"]], [202])

    def test_an_edge_is_merged_once_its_story_is_in_the_delivery_log(self):
        (_level, per_story, _attention), _read = self._facts(self.DECLARED, shipped=[201])
        self.assertTrue(per_story[202]["build"][0]["merged"])
        self.assertTrue(per_story[203]["start"][0]["merged"])
        self.assertFalse(per_story[203]["merge"][0]["merged"])

    def test_a_malformed_plan_falls_back_serially_with_its_reasons_unchanged(self):
        plan = self.DECLARED.replace("| build |", "| sometime |")
        (epic_level, per_story, _attention), read = self._facts(plan)
        self.assertEqual((epic_level["source"], epic_level["fallback_reason"]), ("serial-fallback", "malformed"))
        self.assertIs(epic_level["malformed"], read["malformed"])
        self.assertTrue(epic_level["malformed"])
        self.assertEqual(per_story[201]["start"], [])
        self.assertEqual([(e["waits_on"], e["kind"]) for e in per_story[202]["start"]], [(201, "order")])
        self.assertEqual([(e["waits_on"], e["kind"]) for e in per_story[203]["start"]], [(202, "order")])
        self.assertIsNone(per_story[202]["set"])

    def test_a_waited_on_story_outside_the_story_set_is_unknown(self):
        (_level, per_story, attention), _read = self._facts(self.DECLARED, numbers=(202, 203))
        self.assertIsNone(per_story[202]["build"][0]["merged"])
        self.assertIsNone(per_story[203]["start"][0]["merged"])
        # One line per waiting story, naming its gate — two stories wait on #201 here.
        self.assertEqual(sorted(line for line in attention if "#201" in line),
                         ["story #202's `build` gate waits on #201, which is not one of the epic's stories — "
                          "that edge's merged state is unknown; settling its condition as not holding releases "
                          "it: #202 edits the layout",
                          "story #203's `start` gate waits on #201, which is not one of the epic's stories — "
                          "that edge's merged state is unknown"])

    def test_a_story_the_plan_does_not_list_has_no_gates(self):
        (_level, per_story, attention), _read = self._facts(self.DECLARED, numbers=(201, 202, 203, 209))
        self.assertEqual(per_story[209], {"set": None, "start": None, "build": None, "merge": None})
        self.assertTrue(any("#209" in line and "## Story breakdown" in line for line in attention), attention)

    def test_a_waited_on_story_closed_without_a_log_entry_is_said_once_per_open_waiter(self):
        """Closed as deferred: no merge is coming, so the held edge is named instead of stalling silently."""
        level, per_story, attention = pc.concurrency_facts(self.DECLARED, [201, 202, 203], [], closed=[201])
        self.assertFalse(per_story[202]["build"][0]["merged"])
        self.assertEqual(sorted(line for line in attention if "#201" in line),
                         ["story #202's `build` gate waits on #201, which is closed but not shipped in the "
                          "delivery log — the edge holds until the log records it or the epic plan is revised; "
                          "settling its condition as not holding releases it: #202 edits the layout",
                          "story #203's `start` gate waits on #201, which is closed but not shipped in the "
                          "delivery log — the edge holds until the log records it or the epic plan is revised"])
        _level, _per, attention = pc.concurrency_facts(self.DECLARED, [201, 202, 203], [201], closed=[201])
        self.assertEqual(attention, [], "a shipped story's edges are merged, never held")
        _level, _per, attention = pc.concurrency_facts(self.DECLARED, [202, 203], [], closed=[202, 203])
        self.assertEqual(attention, [], "a closed waiter needs no start, so its edges say nothing")

    def test_a_closed_unlisted_story_gets_no_gates_and_no_attention(self):
        _level, per_story, attention = pc.concurrency_facts(self.DECLARED, [201, 202, 203, 209], [], closed=[209])
        self.assertEqual(per_story[209], {"set": None, "start": None, "build": None, "merge": None})
        self.assertFalse(any("#209" in line for line in attention), attention)

    def test_no_epic_plan_has_no_concurrency_and_no_gates(self):
        (epic_level, per_story, attention), read = self._facts(None)
        self.assertIsNone(read)
        self.assertIsNone(epic_level)
        self.assertEqual(attention, [])
        for number in (201, 202, 203):
            self.assertEqual(per_story[number], {"set": None, "start": None, "build": None, "merge": None})


class GathererMemoTests(unittest.TestCase):
    def test_remote_reads_are_made_once_and_decisions_are_never_cached(self):
        g = pc._Gatherer("octo/widgets", ROOT, tempfile.mkdtemp(), None)
        calls = []
        self.assertEqual(g._memo("k", lambda: calls.append(1) or ("v", None)), ("v", None))
        self.assertEqual(g._memo("k", lambda: calls.append(1) or ("w", None)), ("v", None))
        self.assertEqual(len(calls), 1)
        decision = {"code": "AUTH_REQUIRED"}
        g._memo("d", lambda: calls.append(1) or (None, decision))
        g._memo("d", lambda: calls.append(1) or (None, decision))
        self.assertEqual(len(calls), 3, "a decision is re-fetched, never replayed from the cache")

    def test_a_re_read_issue_decision_keeps_its_notices_once(self):
        """`--sync` gathers twice and an issue's decision is never cached, so a degraded sibling is
        read twice — its notices must not be reported twice."""
        g = pc._Gatherer("octo/widgets", ROOT, tempfile.mkdtemp(), None)
        decision = {"code": "MARKER_AMBIGUOUS", "summary": "dup", "context": {}, "options": []}
        envelope = {"status": "needs_decision", "decision": decision, "notices": [{"code": "DEPS_UNSUPPORTED"}]}
        with mock.patch.object(pc.gh_gather, "run", return_value=(0, envelope)) as run:
            self.assertEqual(g.issue(155), (None, decision))
            self.assertEqual(g.issue(155), (None, decision))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(g.notices, [{"code": "DEPS_UNSUPPORTED"}])


class PhaseFactTests(unittest.TestCase):
    PLAN = "## Phases\n" + "".join(
        "%d. **Phase %d — %s**\n"
        "   - kind: code-shipping\n"
        "   - ships: PR commits to the issue branch\n"
        "   - closes-dod: (none)\n"
        "   - deliverable: %s\n"
        "   - depends-on: (none)\n%s" % (n, n, title, title, checkpoint)
        for n, title, checkpoint in ((1, "parser", "   - checkpoint: pause\n"),
                                     (2, "store", "   - checkpoint: continue\n"),
                                     (3, "ui", ""))
    )

    def test_shipped_and_next_phase_come_from_the_tracker(self):
        body = "## Phase tracker\n- [x] Phase 1 — Parser\n- [ ] Phase 2 — Store\n- [ ] Phase 3 — UI\n"
        facts = pc._phase_facts(self.PLAN, body)
        self.assertEqual(facts["total"], 3)
        self.assertEqual(facts["shipped"], 1)
        self.assertEqual(facts["next_phase"], 2)
        self.assertEqual(facts["next_checkpoint"], "continue")

    def test_an_unparseable_plan_degrades_to_none(self):
        self.assertIsNone(pc._phase_facts("no phases here", ""))


HOOKS_DIR = REPO_ROOT / "hooks"
FIXTURE_PREFIX = "export default "


def _board_fixture(name):
    """A `hooks/fixtures/*.ts` file is `export default ` plus one JSON object: the keys the board
    mod reads (#116)."""
    text = (HOOKS_DIR / "fixtures" / name).read_text(encoding="utf-8")
    assert text.startswith(FIXTURE_PREFIX), name
    return json.loads(text[len(FIXTURE_PREFIX):])


def _json_kind(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return {str: "string", list: "array", dict: "object", type(None): "null"}[type(value)]


def assert_shape_subset(case, fixture, live, path="$"):
    """Every key path in `fixture` exists in `live` with the same JSON type (a fixture `null` matches
    any type). `epic.stories[]` entries pair by `number`; every other list pairs element by element.
    A prep rename or reshape of a key the board reads fails here instead of blanking the view."""
    if fixture is None:
        return
    case.assertEqual(_json_kind(fixture), _json_kind(live), "%s: fixture %s, live %s"
                     % (path, _json_kind(fixture), _json_kind(live)))
    if isinstance(fixture, dict):
        for key, value in fixture.items():
            case.assertIn(key, live, "%s.%s is missing from the live envelope" % (path, key))
            assert_shape_subset(case, value, live[key], "%s.%s" % (path, key))
    elif isinstance(fixture, list):
        if path == "$.epic.stories":
            by_number = {entry["number"]: entry for entry in live}
            for entry in fixture:
                case.assertIn(entry["number"], by_number, "%s: no live story #%s" % (path, entry["number"]))
                assert_shape_subset(case, entry, by_number[entry["number"]], "%s[#%s]" % (path, entry["number"]))
        else:
            case.assertGreaterEqual(len(live), len(fixture), "%s: live list is shorter" % path)
            for index, entry in enumerate(fixture):
                assert_shape_subset(case, entry, live[index], "%s[%d]" % (path, index))


def _git(args, cwd):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, encoding="utf-8", check=False)
    if result.returncode != 0:
        raise RuntimeError("git %s (cwd=%s) failed: %s" % (" ".join(args), cwd, result.stderr))
    return result.stdout.strip()


def _one_envelope(stdout_text):
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError("expected exactly one envelope line, got %d: %r" % (len(lines), lines))
    return json.loads(lines[0])


class CoordinatorSandboxTestCase(unittest.TestCase):
    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = Path(self.clone.path)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scratch = tmp.name

    def _run(self, args, fixture_case, script=SCRIPT, cwd=None):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case=fixture_case)
        return subprocess.run([sys.executable, str(script)] + args, env=env, cwd=cwd,
                              capture_output=True, encoding="utf-8", check=False)

    def _envelope(self, issue, fixture_case, extra=(), expect_rc=0, cwd=None, script=SCRIPT):
        result = self._run([str(issue), "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch]
                           + list(extra), fixture_case, script=script, cwd=cwd)
        self.assertEqual(result.returncode, expect_rc, "stderr: %s" % result.stderr)
        envelope = _one_envelope(result.stdout)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        return envelope

    def _add_worktree(self, branch, push=False):
        path = self.root / ".worktrees" / branch
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "-b", branch, str(path), "origin/main"], self.root)
        if push:
            _git(["push", "-u", "origin", branch], path)
        return path.resolve()


class ScriptTests(unittest.TestCase):
    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_two_action_flags_are_a_usage_error(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "100", "octo/widgets", "--open", "100", "--sync"],
                                capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 2)

    def test_story_focus_is_retired_and_a_usage_error(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "150", "octo/widgets", "--story", "152"],
                                capture_output=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 2)


class ReadOnlyDerivationTests(CoordinatorSandboxTestCase):
    def test_a_fresh_issue_needs_a_plan_and_nothing_is_created(self):
        envelope = self._envelope(100, "prep_workspace_open_fresh")
        self.assertEqual(envelope["stage"], pc.STAGE_NEEDS_PLAN)
        self.assertEqual(envelope["next"]["command"], "/github-pipeline:planner #100")
        self.assertEqual(envelope["next"]["folder"], str(self.root.resolve()))
        self.assertEqual(envelope["suggested_playbook"], "single.md")
        self.assertTrue(envelope["root"]["at_default_tip"])
        self.assertFalse((self.root / ".worktrees").exists(), "a read never opens a worktree")
        self.assertNotIn("action", envelope)

    def test_the_target_body_is_always_spilled_with_its_sha(self):
        """#116's orientation input: the body is a path even this small (`force_path`), never a
        bare inline `body`, and its sha is of exactly the file's bytes."""
        body = json.loads((REPO_ROOT / "tests" / "fixtures" / "prep_workspace_open_fresh" / "issue_view.json")
                          .read_text(encoding="utf-8"))["body"].encode("utf-8")
        self.assertLess(len(body), 25600)
        target = self._envelope(100, "prep_workspace_open_fresh")["target"]
        self.assertEqual(target["body_mode"], "path")
        self.assertNotIn("body", target)
        self.assertEqual(Path(target["body_path"]).read_bytes(), body)
        self.assertEqual(target["body_bytes"], len(body))
        self.assertEqual(target["body_sha256"], hashlib.sha256(body).hexdigest())

    def test_the_body_keys_ride_the_refused_and_epic_paths_too(self):
        """`gather_state` adds them before the refusal return and the epic branch, so every path's
        `target` carries them."""
        for issue, case in ((120, "prep_coordinator_question"), (150, "prep_coordinator_epic_unsliced")):
            target = self._envelope(issue, case)["target"]
            body = Path(target["body_path"]).read_bytes()
            self.assertEqual((target["body_mode"], target["body_bytes"]), ("path", len(body)), case)
            self.assertEqual(target["body_sha256"], hashlib.sha256(body).hexdigest(), case)

    def test_the_board_issue_fixture_is_a_shape_subset_of_a_live_prep(self):
        """#116's key pin, single-issue side: `hooks/fixtures/prep-issue.ts` against the run it was
        trimmed from."""
        self._add_worktree("100-fix-the-widget")
        live = self._envelope(100, "prep_coordinator_draft_phases")
        fixture = _board_fixture("prep-issue.ts")
        self.assertIsNotNone(fixture["unit"]["pr"]["phases"], "the fixture pins the phase keys")
        assert_shape_subset(self, fixture, live)

    def test_a_paused_multi_phase_draft_is_a_resolver_re_entry_with_its_phase_position(self):
        """The checkpoint-pause re-entry: a draft PR whose tracker has phase 1 of 3 ticked."""
        wt = self._add_worktree("100-fix-the-widget")
        envelope = self._envelope(100, "prep_coordinator_draft_phases")
        self.assertEqual(envelope["stage"], pc.STAGE_RESOLVING)
        self.assertEqual(envelope["next"]["command"], "/github-pipeline:resolver #100")
        self.assertEqual(envelope["next"]["folder"], str(wt))
        self.assertEqual(envelope["unit"]["pr"]["number"], 58)
        phases = envelope["unit"]["pr"]["phases"]
        self.assertEqual((phases["total"], phases["shipped"], phases["next_phase"]), (3, 1, 2))

    def test_an_unreachable_remote_leaves_the_root_tip_unknown_and_raises_no_warning(self):
        _git(["remote", "set-url", "origin", str(self.root / "no-such-remote")], self.root)
        envelope = self._envelope(100, "prep_workspace_open_fresh")
        self.assertEqual(envelope["stage"], pc.STAGE_NEEDS_PLAN)
        self.assertIsNone(envelope["root"]["at_default_tip"])
        self.assertFalse(any("not the tip" in line for line in envelope["attention"]), envelope["attention"])

    def test_a_question_is_refused(self):
        envelope = self._envelope(120, "prep_coordinator_question")
        self.assertEqual(envelope["stage"], pc.STAGE_REFUSED)
        self.assertEqual(envelope["refusal"]["reason"], pc.REFUSAL_QUESTION)

    def test_a_slice_is_refused_naming_its_parent(self):
        """A non-epic's sub-issue ships as a phase on its parent's branch — the coordinator offers
        the parent instead (the same classification workspace-open refuses on)."""
        envelope = self._envelope(266, "prep_workspace_open_slice")
        self.assertEqual(envelope["stage"], pc.STAGE_REFUSED)
        self.assertEqual(envelope["refusal"]["reason"], pc.REFUSAL_SLICE)
        self.assertEqual(envelope["refusal"]["context"]["parent"]["number"], 103)

    def test_an_in_scope_blocked_open_question_is_refused_naming_it(self):
        """The resolver's own gate, composed: blocked iff `in-scope (blocked)` and the tracker is
        unresolved."""
        envelope = self._envelope(100, "prep_resolver_oq_blocked_open")
        self.assertEqual(envelope["stage"], pc.STAGE_REFUSED)
        self.assertEqual(envelope["refusal"]["reason"], pc.REFUSAL_OQ_BLOCKED)
        blocking = envelope["refusal"]["context"]["blocking"]
        self.assertEqual([(b["oq_id"], b["question"]) for b in blocking], [("OQ-01", "#201")])
        self.assertEqual(blocking[0]["gates"], "default sort order")

    def test_an_unsliced_epic_slices_first(self):
        envelope = self._envelope(150, "prep_coordinator_epic_unsliced")
        self.assertEqual(envelope["stage"], pc.STAGE_NEEDS_SLICING)
        self.assertEqual(envelope["next"]["command"], "/github-pipeline:slicer #150")
        self.assertEqual(envelope["suggested_playbook"], "epic.md")


class BoardFeedTests(CoordinatorSandboxTestCase):
    """#116's feed: every envelope prep emits is also `<scratch>/envelope.json`, byte for byte;
    a run with no envelope leaves no file; a failed write is a notice, never a failure."""

    def _feed(self):
        return Path(self.scratch) / pc.FEED_FILE

    def test_the_feed_file_equals_stdout(self):
        result = self._run(["100", "octo/widgets", "--root", str(self.root), "--scratch-dir", self.scratch],
                           "prep_workspace_open_fresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._feed().read_text(encoding="utf-8"), result.stdout)

    def test_a_decision_envelope_is_written_too(self):
        envelope = self._envelope(100, "prep_workspace_open_fresh", extra=["--sync"])
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "AMBIGUOUS")
        self.assertEqual(json.loads(self._feed().read_text(encoding="utf-8")), envelope)

    def test_a_run_with_no_envelope_leaves_no_file(self):
        self._feed().write_text('{"status": "ok"}\n', encoding="utf-8")
        with mock.patch.object(pc, "build", side_effect=RuntimeError("gh failed")):
            with self.assertRaises(RuntimeError):
                pc.main(["100", "octo/widgets", "--scratch-dir", self.scratch])
        self.assertFalse(self._feed().exists())

    def test_an_unwritten_feed_is_a_notice_and_a_stderr_line(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pc, "build", return_value=({"stage": pc.STAGE_TERMINAL}, [], None)), \
                mock.patch.object(pc, "write_envelope", side_effect=OSError("disk full")), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            rc = pc.main(["100", "octo/widgets", "--scratch-dir", self.scratch])
        self.assertEqual(rc, 0)
        self.assertIn("BOARD_FEED_UNWRITTEN", _one_envelope(out.getvalue())["notices"])
        self.assertIn("prep_coordinator: board feed not written: disk full", err.getvalue())


def _at(obj, path):
    for part in path.split("."):
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


class BoardReadKeysTests(CoordinatorSandboxTestCase):
    """#116: the keys the board mod reads that `hooks/fixtures/*.ts` can carry only as `null` or not
    at all (the fixtures' source runs have no such state), each pinned non-null where prep emits it —
    a live run, or the builder that writes it. The fixture shape pin cannot see beneath a `null`."""

    def _assert_kinds(self, envelope, kinds, label):
        for path, kind in kinds.items():
            self.assertEqual(_json_kind(_at(envelope, path)), kind, "%s: %s" % (label, path))

    def test_a_gated_and_a_refused_target_carry_their_reasons(self):
        gated = self._envelope(100, "prep_workspace_open_gated")
        self._assert_kinds(gated, {"gate.reason": "string", "unit.gate.reason": "string"}, "gated")
        refused = self._envelope(120, "prep_coordinator_question")
        self._assert_kinds(refused, {"refusal.reason": "string"}, "refused")

class BoardBuilderKeysTests(unittest.TestCase):
    """The pure half of `BoardReadKeysTests`: the same pins read from the builders, no sandbox."""

    _assert_kinds = BoardReadKeysTests._assert_kinds

    def test_the_builders_the_board_reads_from(self):
        self._assert_kinds({"next": pc._open(150)}, {"next.kind": "string", "next.issue": "number"}, "_open")
        with mock.patch.object(pc, "_behind", return_value=False):
            parent = pc._parent_epic_facts(ROOT, {"parent_epic": {"number": 150, "title": "t", "state": "OPEN"},
                                                  "parent_kind": pc.branching.PARENT_KIND_EPIC,
                                                  "branch_facts": {"branch": "epic/150-inbox"}},
                                           ["epic/150-inbox"])
        self._assert_kinds({"parent_epic": parent}, {"parent_epic.worktree.path": "string"}, "parent_epic")
        plan = ConcurrencyFactsTests.DECLARED.replace("| build |", "| sometime |")
        level, _per_story, _attention = pc.concurrency_facts(plan, [201, 202, 203], [])
        self._assert_kinds({"c": level}, {"c.fallback_reason": "string", "c.malformed.0.reason": "string"},
                           "concurrency")
        self.assertIn("line", level["malformed"][0])

    def test_an_epic_whose_stories_all_closed_carries_its_integration_keys(self):
        stories = [{"number": 151, "title": "a", "closed": True, "worktree": None}]
        unit = _unit(150, pr={"number": 170, "draft": True, "merged": False},
                     gate={"reason": "open-pr-other-stale"})
        patches = [
            mock.patch.object(pc.workspace, "list_work_branches", return_value=["epic/150-inbox"]),
            mock.patch.object(pc, "_behind", return_value=False),
            mock.patch.object(pc, "_story_set", return_value=(stories, "native")),
            mock.patch.object(pc, "load_thread", return_value=[]),
            mock.patch.object(pc, "_body_facts", return_value={}),
            mock.patch.object(pc, "_refusal", return_value=(None, None, None)),
            mock.patch.object(pc, "build_unit", return_value=(unit, None)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        state, _err = pc.gather_state(_StubGatherer({}), 150)
        self._assert_kinds(state, {"epic.integration.number": "number", "epic.integration.draft": "boolean",
                                   "epic.integration.gate.reason": "string"}, "integration")


class BoardModReadOnlyTests(unittest.TestCase):
    """#116's DoD 2, offline: the board mod's non-test modules call no `$` namespace that could
    write, spawn, reach the network, prompt, or act on a session or agent (a plain text scan)."""

    BANNED = ("$.process", "$.http", "$.fs.write", "$.prompt", "$.session", "$.agent", "$.tool.")
    # The allowlist the module comment states: every `$.<noun>[.<method>]` spelling is one of these.
    ALLOWED = ("$.ui.", "$.store.", "$.fs.read", "$.model.complete", "$.command.")

    def test_no_mod_module_calls_a_writing_namespace(self):
        modules = [p for p in sorted(HOOKS_DIR.rglob("*.ts")) if not p.name.endswith(".test.ts")
                   and "fixtures" not in p.parts]
        self.assertTrue(modules, "the scan must see the mod's modules")
        for module in modules:
            source = module.read_text(encoding="utf-8")
            for token in self.BANNED:
                self.assertNotIn(token, source, "%s calls %s" % (module.relative_to(REPO_ROOT), token))
            for spelled in re.findall(r"\$\.[A-Za-z_]+(?:\.[A-Za-z_]+)?", source):
                self.assertTrue(spelled.startswith(self.ALLOWED) or spelled + "." in self.ALLOWED,
                                "%s calls %s, outside the allowed set" % (module.relative_to(REPO_ROOT), spelled))
            # The scan reads `$.<noun>` spellings, so `$` and its nouns are never aliased or indexed.
            self.assertIsNone(re.search(r"=\s*\$(?:\.[A-Za-z_]+)?\s*(?:[;\n)]|$)|\$\s*\[", source, re.M),
                              "%s aliases or indexes `$`" % module.relative_to(REPO_ROOT))


class ActionTests(CoordinatorSandboxTestCase):
    def test_open_runs_the_workspace_open_core_then_re_derives(self):
        envelope = self._envelope(100, "prep_workspace_open_fresh", extra=["--open", "100"])
        self.assertEqual(envelope["action"]["op"], "open")
        wt = Path(envelope["action"]["receipt"]["workspace"]["path"])
        self.assertTrue(wt.is_dir())
        self.assertEqual(envelope["routes"]["resolver"], str(wt))
        self.assertEqual(envelope["stage"], pc.STAGE_NEEDS_PLAN, "plan-before-open: the plan is still next")

    def test_an_open_core_decision_is_passed_through_unchanged(self):
        """The card the coordinator renders is byte-for-byte the workspace-open tool's own."""
        tool = self._envelope(100, "prep_workspace_open_multi_linked", script=OPEN_SCRIPT)
        coordinator = self._envelope(100, "prep_workspace_open_multi_linked", extra=["--open", "100"])
        self.assertEqual(tool["status"], "needs_decision")
        self.assertEqual(coordinator["decision"], tool["decision"])
        self.assertEqual(coordinator["notices"], tool["notices"])

    def test_close_releases_a_merged_issues_worktree(self):
        wt = self._add_worktree("100-fix-the-widget")
        before = self._envelope(100, "prep_coordinator_closed")
        self.assertEqual(before["stage"], pc.STAGE_MERGED_NEEDS_CLOSE)
        self.assertEqual(before["next"]["branch"], "100-fix-the-widget")
        self.assertTrue(wt.is_dir(), "the read did not close anything")
        after = self._envelope(100, "prep_coordinator_closed", extra=["--close", "100-fix-the-widget"])
        self.assertEqual(after["action"]["op"], "close")
        self.assertTrue(after["action"]["receipt"]["removed"])
        self.assertFalse(wt.exists())
        self.assertEqual(after["stage"], pc.STAGE_TERMINAL)

    def test_closing_the_worktree_the_coordinator_sits_in_is_the_tools_own_card(self):
        wt = self._add_worktree("100-fix-the-widget")
        envelope = self._envelope(100, "prep_coordinator_closed", extra=["--close", "100-fix-the-widget"], cwd=wt)
        self.assertEqual(envelope["decision"]["code"], "WORKSPACE_MISMATCH")
        self.assertEqual(envelope["decision"]["context"]["reason"], "cwd_inside_target")
        self.assertTrue(wt.is_dir())


class EpicSyncTests(CoordinatorSandboxTestCase):
    def setUp(self):
        super().setUp()
        self.epic_wt = self._add_worktree("epic/150-inbox", push=True)
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        _git(["fetch", "origin", "epic/150-inbox"], other.path)
        _git(["checkout", "-b", "epic/150-inbox", "origin/epic/150-inbox"], other.path)
        (Path(other.path) / "story-151.txt").write_text("merged\n", encoding="utf-8")
        _git(["add", "story-151.txt"], other.path)
        _git(["commit", "-m", "story 151 squash-merged"], other.path)
        _git(["push", "origin", "epic/150-inbox"], other.path)
        self.tip = _git(["rev-parse", "HEAD"], other.path)

    def _stories(self, envelope):
        return {s["number"]: s for s in envelope["epic"]["stories"]}

    def test_the_next_storys_plan_names_the_sync_and_sync_brings_the_worktree_to_the_tip(self):
        envelope = self._envelope(150, "prep_coordinator_epic_story")
        self.assertEqual(envelope["stage"], pc.STAGE_STORIES)
        self.assertTrue(envelope["epic"]["behind"])
        stories = self._stories(envelope)
        self.assertEqual(stories[152]["stage"], pc.STAGE_NEEDS_PLAN)
        self.assertEqual(stories[152]["next"]["folder"], str(self.epic_wt))
        self.assertTrue(stories[152]["next"]["sync_first"])
        self.assertEqual(stories[151]["stage"], pc.STAGE_TERMINAL)

        synced = self._envelope(150, "prep_coordinator_epic_story", extra=["--sync"])
        self.assertEqual(synced["action"]["op"], "sync")
        self.assertTrue(synced["action"]["receipt"]["sync"]["fast_forwarded"])
        self.assertEqual(_git(["rev-parse", "HEAD"], self.epic_wt), self.tip)
        self.assertFalse(self._stories(synced)[152]["next"]["sync_first"], "merged from the --sync path's final gather")

    def test_the_story_phase_is_stories_with_no_pick(self):
        envelope = self._envelope(150, "prep_coordinator_epic_story")
        self.assertEqual(envelope["next"], {"kind": pc.NEXT_STORIES})
        self.assertIsNone(envelope["focus"])
        self.assertIsNone(envelope["unit"])
        self.assertNotIn("candidates", envelope["epic"])

    def test_running_inside_the_epic_worktree_is_supported_and_said_up_front(self):
        envelope = self._envelope(150, "prep_coordinator_epic_story", cwd=self.epic_wt)
        self.assertEqual(self._stories(envelope)[152]["stage"], pc.STAGE_NEEDS_PLAN)
        self.assertTrue(envelope["invoked_from"]["inside_work_worktree"])
        self.assertIn("running inside the work worktree", envelope["attention"][0])


class _EpicStoriesCase(CoordinatorSandboxTestCase):
    """Epic #150 with five stories at different stages: #151 closed with its worktree still open,
    #152 a ready PR, #153 resolving, #154 unplanned, #155 unreadable (two plan markers)."""

    def setUp(self):
        super().setUp()
        self.epic_wt = self._add_worktree("epic/150-inbox")
        self.wts = {n: self._add_worktree(b) for n, b in ((151, "151-sender"), (152, "152-date"), (153, "153-tags"))}

    def _stories(self, envelope):
        return {s["number"]: s for s in envelope["epic"]["stories"]}

    def _assert_entries(self, envelope):
        stories = self._stories(envelope)
        self.assertEqual({n: s["stage"] for n, s in stories.items()},
                         {151: pc.STAGE_MERGED_NEEDS_CLOSE, 152: pc.STAGE_PR_READY, 153: pc.STAGE_RESOLVING,
                          154: pc.STAGE_NEEDS_PLAN, 155: pc.STAGE_GATED})
        self.assertEqual(stories[151]["next"]["branch"], "151-sender")
        self.assertEqual(stories[152]["next"]["command"], "/github-pipeline:evaluator #160")
        self.assertEqual(stories[152]["next"]["folder"], str(self.wts[152]))
        self.assertEqual(stories[153]["next"]["command"], "/github-pipeline:resolver #153")
        self.assertEqual(stories[153]["next"]["folder"], str(self.wts[153]))
        self.assertEqual(stories[154]["next"]["command"], "/github-pipeline:planner #154")
        self.assertEqual(stories[154]["next"]["folder"], str(self.epic_wt))
        self.assertEqual(stories[155]["next"], {"kind": pc.NEXT_NONE})
        self.assertEqual(stories[155]["gate"]["reason"], "MARKER_AMBIGUOUS")
        self.assertEqual(stories[155]["gate"]["decision"]["code"], "MARKER_AMBIGUOUS")
        return stories


class EpicInFlightTests(_EpicStoriesCase):
    """The five stories under a plan with a `## Story breakdown` and no declaration."""

    FIXTURE = "prep_coordinator_epic_in_flight"

    def test_every_story_reports_its_own_stage_and_serial_gates(self):
        envelope = self._envelope(150, self.FIXTURE)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["stage"], pc.STAGE_STORIES)
        self.assertEqual(envelope["next"], {"kind": pc.NEXT_STORIES})
        self.assertIsNone(envelope["focus"])
        self.assertIsNone(envelope["unit"])
        self.assertNotIn("candidates", envelope["epic"])
        attention = envelope["attention"]
        self.assertFalse(any("--story" in line for line in attention), attention)
        self.assertTrue(any(line.startswith("story #155 could not be read (MARKER_AMBIGUOUS") for line in attention),
                        attention)
        self.assertFalse(any("merged but" in line for line in attention), attention)
        # A plan with a `## Story breakdown` and no declaration: the reader's serial fallback.
        self.assertEqual(envelope["epic"]["concurrency"],
                         {"source": "serial-fallback", "fallback_reason": "absent", "sets": [], "malformed": []})
        stories = self._assert_entries(envelope)
        self.assertEqual(stories[151]["start"], [])
        for previous, number in ((151, 152), (152, 153), (153, 154), (154, 155)):
            self.assertIsNone(stories[number]["set"])
            self.assertEqual(stories[number]["start"],
                             [{"waits_on": previous, "kind": "order", "contracts": [], "surface": None,
                               "condition": None, "merged": False}], number)
            self.assertEqual((stories[number]["build"], stories[number]["merge"]), ([], []))


class EpicConcurrencyTests(_EpicStoriesCase):
    """The epic's convergence test: the same five stories with a declared concurrency plan and
    #151 in the delivery log — #111's per-story stages unchanged beside #115's gates."""

    FIXTURE = "prep_coordinator_epic_concurrency"

    def test_the_declared_gates_ride_each_entry(self):
        envelope = self._envelope(150, self.FIXTURE)
        self.assertEqual(envelope["stage"], pc.STAGE_STORIES)
        self.assertEqual(envelope["epic"]["shipped"], [151])
        self.assertEqual(envelope["epic"]["concurrency"],
                         {"source": "declared", "fallback_reason": None, "malformed": [],
                          "sets": [{"name": "Wave 1", "stories": [151]},
                                   {"name": "Wave 2", "stories": [152, 153]},
                                   {"name": "Wave 3", "stories": [154, 155]}]})
        stories = self._assert_entries(envelope)
        self.assertEqual({n: s["set"] for n, s in stories.items()},
                         {151: "Wave 1", 152: "Wave 2", 153: "Wave 2", 154: "Wave 3", 155: "Wave 3"})
        self.assertEqual((stories[151]["start"], stories[151]["build"], stories[151]["merge"]), ([], [], []))
        self.assertEqual(stories[152]["start"], [{"waits_on": 151, "kind": "contract", "contracts": ["sender sort"],
                                                  "surface": None, "condition": None, "merged": True}])
        self.assertEqual(stories[153]["build"], [{"waits_on": 151, "kind": "file overlap", "contracts": [],
                                                  "surface": "src/inbox", "condition": "#153 changes the inbox list",
                                                  "merged": True}])
        self.assertEqual(stories[154]["start"], [{"waits_on": 152, "kind": "order", "contracts": [],
                                                  "surface": None, "condition": None, "merged": False}])
        self.assertEqual([(e["waits_on"], e["kind"], e["merged"]) for e in stories[155]["merge"]],
                         [(152, "merge order", False)])
        self.assertEqual([(e["waits_on"], e["merged"]) for e in stories[155]["build"]], [(153, False)])
        self.assertFalse(any("Story breakdown" in line or "merged state is unknown" in line
                             for line in envelope["attention"]), envelope["attention"])

    def test_the_board_epic_fixture_is_a_shape_subset_of_a_live_prep(self):
        """#116's key pin, epic side: `hooks/fixtures/prep-epic.ts` against this case's live run, and
        the manifest shape that keeps a pre-mod host loading the plugin."""
        live = self._envelope(150, self.FIXTURE)
        fixture = _board_fixture("prep-epic.ts")
        stories = {s["number"]: s for s in fixture["epic"]["stories"]}
        self.assertIsNotNone(stories[152]["pr"], "pins pr.number / pr.url")
        self.assertIn("reason", stories[155]["gate"], "pins gate.reason")
        for gate in ("start", "build", "merge"):
            self.assertTrue(any(s[gate] for s in stories.values()), "pins %s edges" % gate)
        assert_shape_subset(self, fixture, live)
        manifest = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["hooks"], {})
        self.assertEqual(manifest["modules"], ["./pipeline-board.ts"])
        self.assertTrue((HOOKS_DIR / "pipeline-board.ts").is_file())


class BaseMovedTests(CoordinatorSandboxTestCase):
    """DoD 2: a story PR whose base moved because a parallel story merged first is re-gated."""

    def setUp(self):
        super().setUp()
        self.epic_wt = self._add_worktree("epic/150-inbox", push=True)
        self.wts = {n: self._add_worktree(b) for n, b in ((151, "151-sender"), (152, "152-date"), (153, "153-tags"))}
        other = gitsandbox.mk_clone(self.origin)
        self.addCleanup(other.cleanup)
        _git(["fetch", "origin", "epic/150-inbox"], other.path)
        _git(["checkout", "-b", "epic/150-inbox", "origin/epic/150-inbox"], other.path)
        (Path(other.path) / "story-153.txt").write_text("merged\n", encoding="utf-8")
        _git(["add", "story-153.txt"], other.path)
        _git(["commit", "-m", "story 153 squash-merged first"], other.path)
        _git(["push", "origin", "epic/150-inbox"], other.path)

    def _entry(self, number):
        envelope = self._envelope(150, "prep_coordinator_epic_in_flight")
        return {s["number"]: s for s in envelope["epic"]["stories"]}[number]

    def test_a_ready_pr_missing_the_epic_tip_is_re_gated_then_evaluated_once_merged_in(self):
        wt = self.wts[152]
        for fetched in (False, True):  # never fetched (an unanswerable probe), then genuinely behind
            if fetched:
                _git(["fetch", "origin", "epic/150-inbox"], wt)
            entry = self._entry(152)
            self.assertEqual(entry["stage"], pc.STAGE_RESOLVING, fetched)
            self.assertEqual(entry["next"]["command"], "/github-pipeline:resolver #152")
            self.assertEqual(entry["next"]["folder"], str(wt))
            self.assertIs(entry["next"]["base_moved"], True)
        _git(["merge", "--no-edit", "origin/epic/150-inbox"], wt)
        entry = self._entry(152)
        self.assertEqual(entry["stage"], pc.STAGE_PR_READY)
        self.assertEqual(entry["next"]["command"], "/github-pipeline:evaluator #160")
        self.assertNotIn("base_moved", entry["next"])


if __name__ == "__main__":
    unittest.main()
