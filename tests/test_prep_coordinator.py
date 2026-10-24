"""Unit tests for scripts/prep_coordinator.py — the coordinator's one prep (#83).

Two layers. The stage table is `prep_coordinator.derive_stage`, a pure function over gathered
state, so `StageTableTests` pins every row across epic / story / standalone / refused targets in
process, with no fixtures. The end-to-end tests drive the prep as a real subprocess — `gh` through
the offline shim, `git` through a real temp origin+clone — for what only a run can show: the one
envelope, the read-only default, the action flags composing the workspace tools' own cores (a core
decision passed through unchanged, a clean action that raises no card), the epic worktree's
behind-and-sync round trip, and the "running inside a work worktree" warning.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
            "refusal": refusal, "epic": None, "unit": unit, "focus_unit": None, "parent_epic": parent}


def _epic(focus=None, **kw):
    epic = {"number": 150, "closed": False, "plan_present": True, "worktree": EPIC_WT, "behind": False,
            "stories": [{"number": 151, "closed": True, "worktree": None},
                        {"number": 152, "closed": False, "worktree": None}],
            "integration": {"number": None, "draft": None, "gate": None}}
    epic.update(kw)
    return {"root_path": ROOT, "target": {"number": 150, "type": "epic"}, "refusal": None,
            "epic": epic, "unit": None, "focus_unit": focus, "parent_epic": None}


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

    def test_a_merged_storys_worktree_is_released_before_the_next_story(self):
        stories = [{"number": 151, "closed": True, "worktree": _wt("151-sender")},
                   {"number": 152, "closed": False, "worktree": None}]
        derived = self._derive(_epic(stories=stories), pc.STAGE_MERGED_NEEDS_CLOSE, pc.NEXT_CLOSE,
                               branch="151-sender")
        self.assertEqual(derived["focus"], 151)

    def test_a_declined_story_close_never_blocks_the_story_in_focus(self):
        """The operator kept #151's worktree: the run carries on with the story in focus."""
        stories = [{"number": 151, "closed": True, "worktree": _wt("151-sender")},
                   {"number": 152, "closed": False, "worktree": _wt("152-date")}]
        focus = _unit(152, worktree=_wt("152-date"))
        derived = self._derive(_epic(stories=stories, focus=focus), pc.STAGE_RESOLVING,
                               command="/github-pipeline:resolver #152")
        self.assertEqual(derived["focus"], 152)

    def test_a_focus_story_whose_pr_merged_returns_to_the_pick(self):
        focus = _unit(152, worktree=None, pr={"number": 160, "draft": False, "merged": True})
        self._derive(_epic(focus=focus), pc.STAGE_NEXT_STORY, pc.NEXT_PICK)

    def test_no_story_in_flight_asks_for_the_next_story(self):
        self._derive(_epic(), pc.STAGE_NEXT_STORY, pc.NEXT_PICK)

    def test_the_focus_story_plans_in_the_epic_worktree(self):
        derived = self._derive(_epic(focus=_unit(152, plan_present=False), behind=True), pc.STAGE_NEEDS_PLAN,
                               command="/github-pipeline:planner #152", folder=EPIC_WT["path"], sync_first=True)
        self.assertEqual(derived["focus"], 152)

    def test_the_focus_story_opens_resolves_and_evaluates_in_its_own_worktree(self):
        self._derive(_epic(focus=_unit(152)), pc.STAGE_NEEDS_WORKSPACE, issue=152)
        wt = _wt("152-date")
        self._derive(_epic(focus=_unit(152, worktree=wt, pr={"number": 160, "draft": True})),
                     pc.STAGE_RESOLVING, folder=wt["path"])
        state = _epic(focus=_unit(152, worktree=wt, pr={"number": 160, "draft": False}))
        self._derive(state, pc.STAGE_PR_READY, command="/github-pipeline:evaluator #160")
        self.assertEqual(pc.routes_for(state, 152)["resolver"], wt["path"])
        self.assertEqual(pc.routes_for(state, 152)["planner"], EPIC_WT["path"])

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
        wt3, wt4, wt5 = _wt("153-tags"), _wt("154-archive"), _wt("155-snooze")
        units = {
            152: _unit(152, plan_present=False),
            153: _unit(153),
            154: _unit(154, worktree=wt4),
            155: _unit(155, worktree=wt5, pr={"number": 165, "draft": True, "merged": False}),
            156: _unit(156, worktree=wt3, pr={"number": 166, "draft": False, "merged": False}),
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
                     folder=wt3["path"])
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
        """The "same derivation" clause: an entry is what `derive_stage` says for a direct-story
        target with the same unit and the epic facts as parent. The unreadable sibling is the one
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

    def test_the_focus_storys_entry_equals_the_top_level_stage(self):
        stories, records = self._mixed_epic()
        foci = [None] + [r["unit"] for r in records.values() if r["unit"] is not None]
        checked = 0
        for focus in foci:
            state, stages = self._stages(stories, records, focus=focus)
            derived = pc.derive_stage(state)
            if derived["focus"] is None:
                continue
            checked += 1
            self.assertEqual((stages[derived["focus"]]["stage"], stages[derived["focus"]]["next"]),
                             (derived["stage"], derived["next"]), derived)
        self.assertGreaterEqual(checked, 4)


class _StubGatherer:
    def __init__(self, decisions):
        self.root = ROOT
        self.decisions = decisions

    def issue(self, number):
        number = int(number)
        if number in self.decisions:
            return None, self.decisions[number]
        labels = [{"name": "epic"}] if number == 150 else [{"name": "story"}]
        return {"number": number, "title": "#%s" % number, "state": "OPEN", "labels": labels}, None


class GatherStoryUnitsTests(unittest.TestCase):
    """`gather_state`'s per-story read: which decision or refusal stops the run, and which only
    gates its own story's entry. The reads are stubbed; the policy is what is under test."""

    STORIES = [{"number": 151, "title": "a", "closed": True, "worktree": None},
               {"number": 152, "title": "b", "closed": False, "worktree": None},
               {"number": 153, "title": "c", "closed": False, "worktree": None}]

    def _gather(self, story=None, decisions=None, refused=()):
        from unittest import mock

        refusals = {n: {"reason": pc.REFUSAL_OQ_BLOCKED, "issue": n, "context": {}} for n in refused}
        patches = [
            mock.patch.object(pc.workspace, "list_work_branches", return_value=["epic/150-inbox"]),
            mock.patch.object(pc, "_behind", return_value=False),
            mock.patch.object(pc, "_story_set", return_value=([dict(s) for s in self.STORIES], "native")),
            mock.patch.object(pc, "load_thread", return_value=[]),
            mock.patch.object(pc, "_refusal", return_value=(None, None, None)),
            mock.patch.object(pc, "_oq_refusal", side_effect=lambda g, env: (refusals.get(int(env["number"])), None)),
            mock.patch.object(pc, "build_unit", side_effect=lambda g, env, branches: (_unit(int(env["number"])), None)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return pc.gather_state(_StubGatherer(decisions or {}), 150, story=story)

    def test_a_siblings_decision_gates_only_its_entry(self):
        decision = {"code": "MARKER_AMBIGUOUS", "summary": "dup", "context": {}, "options": []}
        state, err = self._gather(story=153, decisions={152: decision})
        self.assertIsNone(err)
        self.assertEqual(state["story_units"][152]["decision"], decision)
        self.assertEqual(state["focus_unit"]["number"], 153)
        self.assertTrue(any(line.startswith("story #152 could not be read (MARKER_AMBIGUOUS: dup)")
                            for line in state["attention"]), state["attention"])

    def test_the_focus_storys_decision_and_any_auth_failure_stop_the_run(self):
        marker = {"code": "MARKER_AMBIGUOUS", "summary": "dup", "context": {}, "options": []}
        auth = {"code": "AUTH_REQUIRED", "summary": "gh auth", "context": {}, "options": []}
        self.assertEqual(self._gather(story=152, decisions={152: marker}), (None, marker))
        self.assertEqual(self._gather(story=153, decisions={152: auth}), (None, auth))

    def test_only_the_focus_storys_refusal_refuses_the_run(self):
        state, _err = self._gather(story=153, refused=(152,))
        self.assertIsNone(state["refusal"])
        self.assertEqual(state["focus_unit"]["number"], 153)
        self.assertEqual(state["story_units"][152]["refusal"]["issue"], 152)
        state, _err = self._gather(story=152, refused=(152,))
        self.assertEqual(state["refusal"]["issue"], 152)
        self.assertEqual(pc.derive_stage(state)["stage"], pc.STAGE_REFUSED)
        self.assertEqual(pc.derive_story_stages(state)[152]["stage"], pc.STAGE_REFUSED)

    def test_closed_stories_are_never_read(self):
        state, _err = self._gather()
        self.assertEqual(sorted(state["story_units"]), [152, 153])


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

    def test_the_next_storys_plan_names_the_sync_and_sync_brings_the_worktree_to_the_tip(self):
        envelope = self._envelope(150, "prep_coordinator_epic_story", extra=["--story", "152"])
        self.assertEqual(envelope["stage"], pc.STAGE_NEEDS_PLAN)
        self.assertEqual(envelope["focus"], 152)
        self.assertEqual(envelope["next"]["folder"], str(self.epic_wt))
        self.assertTrue(envelope["next"]["sync_first"])
        self.assertTrue(envelope["epic"]["behind"])

        synced = self._envelope(150, "prep_coordinator_epic_story", extra=["--story", "152", "--sync"])
        self.assertEqual(synced["action"]["op"], "sync")
        self.assertTrue(synced["action"]["receipt"]["sync"]["fast_forwarded"])
        self.assertEqual(_git(["rev-parse", "HEAD"], self.epic_wt), self.tip)
        self.assertFalse(synced["next"]["sync_first"])

        stories = {s["number"]: s for s in envelope["epic"]["stories"]}
        self.assertEqual(stories[152]["stage"], pc.STAGE_NEEDS_PLAN)
        self.assertTrue(stories[152]["next"]["sync_first"])
        self.assertEqual(stories[151]["stage"], pc.STAGE_TERMINAL)
        synced_stories = {s["number"]: s for s in synced["epic"]["stories"]}
        self.assertFalse(synced_stories[152]["next"]["sync_first"], "merged from the --sync path's final gather")

    def test_without_story_focus_the_next_story_is_a_pick(self):
        envelope = self._envelope(150, "prep_coordinator_epic_story")
        self.assertEqual(envelope["stage"], pc.STAGE_NEXT_STORY)
        self.assertEqual([c["number"] for c in envelope["epic"]["candidates"]], [152])

    def test_running_inside_the_epic_worktree_is_supported_and_said_up_front(self):
        envelope = self._envelope(150, "prep_coordinator_epic_story", extra=["--story", "152"], cwd=self.epic_wt)
        self.assertEqual(envelope["stage"], pc.STAGE_NEEDS_PLAN)
        self.assertTrue(envelope["invoked_from"]["inside_work_worktree"])
        self.assertIn("running inside the work worktree", envelope["attention"][0])


class EpicInFlightTests(CoordinatorSandboxTestCase):
    """Epic #150 with five stories at different stages: #151 closed with its worktree still open,
    #152 a ready PR, #153 resolving, #154 unplanned, #155 unreadable (two plan markers)."""

    FIXTURE = "prep_coordinator_epic_in_flight"

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

    def test_without_story_every_story_reports_its_own_stage(self):
        envelope = self._envelope(150, self.FIXTURE)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["stage"], pc.STAGE_MERGED_NEEDS_CLOSE)
        self.assertEqual(envelope["focus"], 151)
        attention = envelope["attention"]
        self.assertTrue(any("pass --story" in line and "#152, #153" in line for line in attention), attention)
        self.assertTrue(any(line.startswith("story #155 could not be read (MARKER_AMBIGUOUS") for line in attention),
                        attention)
        self.assertFalse(any("merged but" in line for line in attention), attention)
        self.assertEqual(envelope["epic"]["candidates"],
                         [{"number": 152, "title": "Sort by date", "in_flight": True},
                          {"number": 153, "title": "Tag filters", "in_flight": True},
                          {"number": 154, "title": "Archive view", "in_flight": False},
                          {"number": 155, "title": "Snooze", "in_flight": False}])
        self._assert_entries(envelope)

    def test_a_focused_story_matches_its_own_entry(self):
        envelope = self._envelope(150, self.FIXTURE, extra=["--story", "153"])
        self.assertEqual(envelope["stage"], pc.STAGE_RESOLVING)
        self.assertEqual(envelope["focus"], 153)
        self.assertEqual(envelope["unit"]["number"], 153)
        stories = self._assert_entries(envelope)
        self.assertEqual((stories[153]["stage"], stories[153]["next"]), (envelope["stage"], envelope["next"]))

    def test_the_focus_storys_own_decision_still_raises_the_card(self):
        envelope = self._envelope(150, self.FIXTURE, extra=["--story", "155"])
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
