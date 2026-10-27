"""Unit tests for scripts/blast_radius.py — the resolver's review level for one loop pass (#97). A real
temp git origin+clone (tests/support/gitsandbox); no `gh` shim, the script spawns only `git`.

Coverage: the declared level and its `high` default (absent, `low`/`max`, duplicated lines); `--final`
taking the highest declared level, an undeclared phase counting as `high`, a plan without `## Phases`
expecting `[1]`; the raise (a sensitive match, an unparsed declaration line, an out-of-plan file), one
step and capped at `xhigh`; planned spans (a `## Test plan`-only file, a `dir/` span); `--since`
excluding earlier phases and catching a declaration edit inside the phase; the CLI's exit 2; the retired look
clock (#155); the epic-integration pass's delivered set (#149); and the cross-consumer pin — prep_evaluator's merge-gate read and `build_assess` agree on one branch.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "blast_radius.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import blast_radius  # noqa: E402
import parse  # noqa: E402
import prep_evaluator  # noqa: E402
from tests.support import gitsandbox  # noqa: E402


def _git(args, cwd):
    result = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), result.stderr))
    return result.stdout.strip()


def _block(*entries):
    return "<!-- pr-evaluator-sensitive-paths -->\n%s<!-- /pr-evaluator-sensitive-paths -->\n" % "".join(
        line + "\n" for line in entries
    )


def _plan(levels=None, phases=2, changes=("`src/`",), tests=("`tests/test_x.py`",)):
    parts = ["<!-- implementation-plan:v1 -->", "", "## Changes (file-level)"]
    parts += ["- %s — a change" % span for span in changes]
    parts += ["", "## Test plan"] + ["- Unit: %s" % span for span in tests]
    if phases:
        parts += ["", "## Phases"]
        for number in range(1, phases + 1):
            parts += [
                "%d. **Phase %d — step %d**" % (number, number, number),
                "   - kind: code-shipping",
                "   - ships: PR commits to the issue branch",
                "   - closes-dod: %d" % number,
                "   - deliverable: step %d" % number,
                "   - depends-on: %s" % ("(none)" if number == 1 else number - 1),
            ]
    if levels is not None:
        parts += ["", "## Review level"] + list(levels)
    return "\n".join(parts) + "\n"


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.origin = gitsandbox.mk_origin()
        self.addCleanup(self.origin.cleanup)
        self.clone = gitsandbox.mk_clone(self.origin)
        self.addCleanup(self.clone.cleanup)
        self.root = self.clone.path
        self.write("CLAUDE.md", _block("- `infra/`"))
        self.write("README.md", "seed\n")
        self.commit("seed")
        _git(["push", "-q", "origin", "HEAD:main"], self.root)
        _git(["checkout", "-q", "-b", "feature"], self.root)
        self.base = _git(["rev-parse", "HEAD"], self.root)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message="change"):
        _git(["add", "-A"], self.root)
        _git(["commit", "-q", "-m", message], self.root)
        return _git(["rev-parse", "HEAD"], self.root)

    def assess(self, plan, phase=1, final=False, **span):
        span = span or {"base_ref": "main"}
        payload, notices, decision = blast_radius.build_assess(self.root, plan, phase, final, **span)
        self.assertIsNone(decision)
        return payload, notices


class StepUpTests(unittest.TestCase):
    def test_one_step_capped_at_xhigh(self):
        self.assertEqual(
            [blast_radius.step_up(level) for level in ("medium", "high", "xhigh")], ["high", "xhigh", "xhigh"]
        )


class DeclaredLevelTests(SandboxCase):
    def setUp(self):
        super().setUp()
        self.write("src/a.py", "a\n")
        self.commit()

    def test_the_phase_declared_level_is_used(self):
        payload, notices = self.assess(_plan(["- phase 1 · medium — plumbing", "- phase 2 · xhigh — a seam"]))
        self.assertEqual((payload["declared_level"], payload["declared_source"]), ("medium", "plan"))
        self.assertEqual((payload["level"], payload["raised"]), ("medium", False))
        self.assertEqual(payload["why"], "plan declares `medium` for phase 1")
        self.assertEqual(notices, [])

    def test_no_review_level_section_is_high_by_default(self):
        payload, _ = self.assess(_plan())
        self.assertEqual((payload["declared_level"], payload["declared_source"]), ("high", "default"))
        self.assertEqual(payload["level"], "high")

    def test_low_max_and_duplicated_lines_are_high_with_a_notice(self):
        for lines in (
            ["- phase 1 · low — tiny"],
            ["- phase 1 · max — scary"],
            ["- phase 1 · medium — a", "- phase 1 · medium — b"],
        ):
            payload, notices = self.assess(_plan(lines))
            self.assertEqual((payload["declared_level"], payload["declared_source"]), ("high", "default"), lines)
            self.assertEqual(len(notices), 1, lines)

    def test_final_takes_the_highest_declared_level(self):
        payload, _ = self.assess(
            _plan(["- phase 1 · medium — a", "- phase 2 · xhigh — b"]), phase=2, final=True
        )
        self.assertEqual((payload["declared_level"], payload["declared_source"]), ("xhigh", "plan"))
        self.assertTrue(payload["why"].startswith("final pass: highest declared level `xhigh`"))

    def test_final_counts_an_undeclared_phase_as_high(self):
        payload, _ = self.assess(_plan(["- phase 2 · medium — b"]), phase=2, final=True)
        self.assertEqual((payload["declared_level"], payload["declared_source"]), ("high", "default"))

    def test_final_on_a_malformed_phases_section_counts_every_phase_as_high(self):
        broken = _plan(["- phase 1 · medium — a"], phases=0) + "\n## Phases\n1. **Phase 1 — x**\n   - closes-dod: x\n"
        payload, notices = self.assess(broken, final=True)
        self.assertEqual(payload["declared_level"], "high")
        self.assertEqual(len(notices), 1)
        payload, _ = self.assess(broken.replace("medium — a", "xhigh — a"), final=True)
        self.assertEqual(payload["declared_level"], "xhigh")

    def test_final_on_a_plan_without_phases_expects_phase_1(self):
        payload, _ = self.assess(_plan(["- phase 1 · medium — one change"], phases=0), final=True)
        self.assertEqual((payload["declared_level"], payload["declared_source"]), ("medium", "plan"))


class RaiseTests(SandboxCase):
    LEVELS = ["- phase 1 · medium — a", "- phase 2 · xhigh — b"]

    def test_a_sensitive_match_raises_one_step(self):
        self.write("src/infra.py", "x\n")
        self.write("infra/main.tf", "x\n")
        self.commit()
        payload, _ = self.assess(_plan(self.LEVELS, changes=("`src/`", "`infra/`")))
        self.assertEqual(payload["sensitive"]["matches"], [{"pattern": "infra/", "path": "infra/main.tf"}])
        self.assertEqual(payload["out_of_plan"], [])
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))
        self.assertIn("sensitive path `infra/` matched `infra/main.tf`", payload["why"])

    def test_an_unparsed_declaration_line_raises(self):
        self.write("CLAUDE.md", _block("- `infra/`", "- infra/typo"))
        self.commit()
        _git(["push", "-q", "origin", "HEAD:main"], self.root)
        self.write("src/a.py", "a\n")
        self.commit()
        payload, _ = self.assess(_plan(self.LEVELS))
        self.assertIn("unreadable declaration", payload["sensitive"]["error"])
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))

    def test_an_out_of_plan_file_raises(self):
        self.write("src/a.py", "a\n")
        self.write("docs/b.md", "b\n")
        self.commit()
        payload, _ = self.assess(_plan(self.LEVELS))
        self.assertEqual(payload["out_of_plan"], ["docs/b.md"])
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))
        self.assertIn("outside the plan: `docs/b.md`", payload["why"])

    def test_a_file_named_only_under_test_plan_does_not_raise(self):
        self.write("tests/test_x.py", "t\n")
        self.commit()
        payload, _ = self.assess(_plan(self.LEVELS))
        self.assertEqual((payload["out_of_plan"], payload["raised"]), ([], False))

    def test_a_dir_span_covers_its_subtree(self):
        self.write("src/deep/er/a.py", "a\n")
        self.commit()
        payload, _ = self.assess(_plan(self.LEVELS))
        self.assertEqual(payload["out_of_plan"], [])

    def test_a_plan_with_no_spans_makes_every_path_out_of_plan(self):
        self.write("src/a.py", "a\n")
        self.commit()
        payload, _ = self.assess(_plan(self.LEVELS, changes=(), tests=()))
        self.assertEqual(payload["out_of_plan"], ["src/a.py"])

    def test_a_glob_span_names_nothing(self):
        self.write("src/a.py", "a\n")
        self.commit()
        for span in ("`*.py`", "`*`"):
            payload, _ = self.assess(_plan(self.LEVELS, changes=(span,), tests=()))
            self.assertEqual((payload["out_of_plan"], payload["raised"]), (["src/a.py"], True), span)

    def test_no_plan_is_high_and_only_a_sensitive_path_raises(self):
        self.write("docs/b.md", "b\n")
        self.commit()
        for final in (False, True):
            payload, notices = self.assess(None, final=final)
            self.assertEqual(
                (payload["declared_level"], payload["out_of_plan"], payload["raised"], payload["level"]),
                ("high", [], False, "high"),
            )
            self.assertTrue(payload["why"].startswith("no plan: `high` by default"))
            self.assertEqual(len(notices), 1)
        self.write("infra/x.tf", "x\n")
        self.commit()
        payload, _ = self.assess(None)
        self.assertEqual((payload["raised"], payload["level"]), (True, "xhigh"))

    def test_shipped_records_join_the_plan_on_the_cli(self):
        self.write("src/a.py", "a\n")
        self.write("old/b.py", "b\n")
        self.commit()
        plan = self.root / "plan.md"
        plan.write_text(_plan(["- phase 1 · medium — a"]), encoding="utf-8")
        record = self.root / "record.md"
        record.write_text("## Changes (file-level)\n- `old/b.py` — shipped earlier\n", encoding="utf-8")
        args = [str(SCRIPT), "assess", str(self.root), "--plan", str(plan), "--plan", str(record), "--phase", "1",
                "--base-ref", "main"]
        envelope = json.loads(subprocess.run(args, capture_output=True, encoding="utf-8").stdout)
        self.assertEqual(envelope["out_of_plan"], [])
        envelope = json.loads(subprocess.run(args[:5] + args[7:], capture_output=True, encoding="utf-8").stdout)
        self.assertIn("old/b.py", envelope["out_of_plan"])

    def test_xhigh_stays_xhigh(self):
        self.write("docs/b.md", "b\n")
        self.commit()
        payload, _ = self.assess(_plan(self.LEVELS), phase=2)
        self.assertEqual((payload["declared_level"], payload["raised"], payload["level"]), ("xhigh", True, "xhigh"))


class JudgmentTests(SandboxCase):
    """The blast-radius judgment (#128): a fourth signal that only raises, joining the fact signals' OR
    so the level still moves one step in total; the fact signals stay the floor."""

    LEVELS = ["- phase 1 · medium — a", "- phase 2 · xhigh — b"]
    RAISE = "## Judgment\nverdict: raise\n- contract · src/a.py:1 — the envelope a later phase reads changes\n"
    HOLD = "## Judgment\nverdict: hold\n"

    def judge(self, judgment, phase=1, changes=("`src/`",)):
        payload, notices, decision = blast_radius.build_assess(
            self.root, _plan(self.LEVELS, changes=changes), phase, False, base_ref="main", judgment=judgment
        )
        self.assertIsNone(decision)
        return payload, notices

    def test_no_judgment_is_null_and_changes_nothing(self):
        self.write("src/a.py", "a\n")
        self.commit()
        payload, notices = self.judge(None)
        self.assertIsNone(payload["judgment"])
        self.assertEqual((payload["raised"], payload["level"], notices), (False, "medium", []))

    def test_a_hold_with_no_fact_signal_keeps_the_declared_level(self):
        self.write("src/a.py", "a\n")
        self.commit()
        payload, notices = self.judge(self.HOLD)
        self.assertEqual((payload["raised"], payload["level"], notices), (False, "medium", []))
        self.assertEqual(
            payload["judgment"], {"verdict": "hold", "signals": [], "unparsed": [], "judged_raise": False}
        )

    def test_a_raise_with_no_fact_signal_raises_one_step(self):
        self.write("src/a.py", "a\n")
        self.commit()
        payload, _ = self.judge(self.RAISE)
        self.assertEqual((payload["out_of_plan"], payload["sensitive"]["matches"]), ([], []))
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))
        self.assertTrue(payload["judgment"]["judged_raise"])

    def test_a_raise_beside_a_fact_signal_is_still_one_step(self):
        self.write("src/a.py", "a\n")
        self.write("docs/b.md", "b\n")
        self.commit()
        payload, _ = self.judge(self.RAISE)
        self.assertEqual(payload["out_of_plan"], ["docs/b.md"])
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))

    def test_a_hold_never_removes_a_fact_signal(self):
        self.write("docs/b.md", "b\n")
        self.commit()
        payload, _ = self.judge(self.HOLD)
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))
        self.write("infra/main.tf", "x\n")
        self.commit()
        payload, _ = self.judge(self.HOLD, changes=("`src/`", "`docs/`", "`infra/`"))
        self.assertEqual(payload["out_of_plan"], [])
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))

    def test_xhigh_plus_a_raise_stays_xhigh(self):
        self.write("src/a.py", "a\n")
        self.commit()
        payload, _ = self.judge(self.RAISE, phase=2)
        self.assertEqual((payload["declared_level"], payload["raised"], payload["level"]), ("xhigh", True, "xhigh"))

    def test_an_unread_verdict_raises_with_a_notice(self):
        self.write("src/a.py", "a\n")
        self.commit()
        for text in ("", "## Judgment\ncode: AMBIGUOUS\nno diff\n", "verdict: hold\n"):
            payload, notices = self.judge(text)
            self.assertEqual((payload["raised"], payload["level"]), (True, "high"), text)
            self.assertIsNone(payload["judgment"]["verdict"], text)
            self.assertTrue(payload["judgment"]["judged_raise"], text)
            self.assertEqual(len(notices), 1, text)
            self.assertIn("judgment not read (counts as a raise)", payload["why"], text)

    def test_a_judgment_on_a_no_plan_run_is_named_in_its_notice(self):
        self.write("src/a.py", "a\n")
        self.commit()
        payload, notices, _ = blast_radius.build_assess(self.root, None, 1, False, base_ref="main", judgment=self.RAISE)
        self.assertEqual((payload["raised"], payload["level"]), (True, "xhigh"))
        self.assertIn("only a sensitive path or the judgment can raise it", notices[0])

    def test_an_ambiguous_code_beside_a_verdict_is_named_in_the_notice(self):
        self.write("src/a.py", "a\n")
        self.commit()
        _, notices = self.judge(self.HOLD + "code: AMBIGUOUS\n")
        self.assertIn("or a `code: AMBIGUOUS` line in it", notices[0])

    def test_why_names_parsed_tokens_never_the_evidence(self):
        self.write("src/a.py", "a\n")
        self.commit()
        payload, _ = self.judge(self.RAISE + "- seam · src/b.py:4 — a second seam\n")
        self.assertIn("raised to `high`: judged blast radius: contract at `src/a.py:1` (+1 more)", payload["why"])
        self.assertNotIn("envelope a later phase reads", payload["why"])
        self.assertEqual(payload["judgment"]["signals"][0]["evidence"], "the envelope a later phase reads changes")


class DeliveredTests(SandboxCase):
    """#149: on the epic-integration pass the files the PRs merged into the epic branch changed count as
    planned, so an epic plan written at coordination altitude (no story's files, no `## Review level`)
    does not raise every pass. A file no merged PR changed still raises; the other signals are untouched."""

    EPIC_PLAN = "<!-- implementation-plan:v1 -->\n\n## Approach\nCoordinate the stories.\n"
    FILES = ("src/a.py", "src/b.py", "docs/c.md")

    def deliver(self, *extra):
        for path in self.FILES + extra:
            self.write(path, path + "\n")
        self.commit()

    def integration(self, delivered, plan=None, judgment=None):
        payload, notices, decision = blast_radius.build_assess(
            self.root, self.EPIC_PLAN if plan is None else plan, 1, True, base_ref="main",
            judgment=judgment, delivered=delivered,
        )
        self.assertIsNone(decision)
        return payload, notices

    def test_an_integration_range_of_delivered_files_reviews_at_the_declared_level(self):
        self.deliver()
        payload, _ = self.integration(list(self.FILES))
        self.assertEqual(payload["out_of_plan"], [])
        self.assertIs(payload["raised"], False)
        self.assertEqual((payload["level"], payload["declared_source"]), ("high", "default"))
        self.assertEqual(sorted(payload["delivered"]["covered"]), sorted(self.FILES))
        self.assertEqual(payload["delivered"]["count"], 3)
        # The same range without the delivered set is the bug: every file out of plan, `xhigh`.
        payload, _ = self.integration(None)
        self.assertEqual((sorted(payload["out_of_plan"]), payload["level"]), (sorted(self.FILES), "xhigh"))

    def test_a_file_no_merged_pr_changed_still_raises(self):
        self.deliver("direct/edit.py")
        payload, _ = self.integration(list(self.FILES))
        self.assertEqual(payload["out_of_plan"], ["direct/edit.py"])
        self.assertEqual((payload["raised"], payload["level"]), (True, "xhigh"))
        self.assertIn("outside the plan: `direct/edit.py`", payload["why"])

    def test_delivered_does_not_mask_a_sensitive_match(self):
        self.deliver("infra/main.tf")
        payload, _ = self.integration(list(self.FILES) + ["infra/main.tf"])
        self.assertEqual(payload["out_of_plan"], [])
        self.assertEqual(payload["sensitive"]["matches"], [{"pattern": "infra/", "path": "infra/main.tf"}])
        self.assertEqual((payload["raised"], payload["level"]), (True, "xhigh"))

    def test_delivered_does_not_mask_a_judgment_raise(self):
        self.deliver()
        payload, _ = self.integration(
            list(self.FILES), judgment="## Judgment\nverdict: raise\n- contract · src/a.py:1 — e\n"
        )
        self.assertEqual(payload["out_of_plan"], [])
        self.assertEqual((payload["raised"], payload["level"]), (True, "xhigh"))

    def test_delivered_membership_is_exact(self):
        self.write("src/a.py", "a\n")
        self.commit()
        payload, _ = self.integration(["src/*.py", "src/", "src/a.p"])
        self.assertEqual(payload["out_of_plan"], ["src/a.py"])
        self.assertEqual(payload["delivered"]["covered"], [])

    def test_a_planned_span_is_not_reported_as_covered(self):
        self.deliver()
        payload, _ = self.integration(list(self.FILES), plan=_plan(changes=("`src/`",), tests=()))
        self.assertEqual(payload["delivered"]["covered"], ["docs/c.md"])

    def test_no_delivered_keeps_the_payload_null(self):
        self.write("src/a.py", "a\n")
        self.write("docs/b.md", "b\n")
        self.commit()
        payload, notices = self.assess(_plan(RaiseTests.LEVELS))
        self.assertIsNone(payload["delivered"])
        self.assertEqual(payload["out_of_plan"], ["docs/b.md"])
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))

    def test_delivered_is_ignored_on_a_no_plan_run(self):
        self.write("docs/b.md", "b\n")
        self.commit()
        payload, _, _ = blast_radius.build_assess(self.root, None, 1, True, base_ref="main", delivered=["x"])
        self.assertEqual((payload["out_of_plan"], payload["raised"]), ([], False))


class SinceRangeTests(SandboxCase):
    def test_since_excludes_files_changed_before_the_phase_base(self):
        self.write("docs/earlier.md", "an earlier phase, out of plan\n")
        phase_base = self.commit("phase 1")
        self.write("src/a.py", "a\n")
        self.commit("phase 2")
        payload, _ = self.assess(_plan(["- phase 2 · medium — b"]), phase=2, since=phase_base)
        self.assertEqual(payload["changed_paths"], ["src/a.py"])
        self.assertEqual(payload["range"], {"kind": "since", "ref": phase_base, "base": phase_base})
        self.assertFalse(payload["raised"])

    def test_a_declaration_edit_inside_the_phase_is_a_match(self):
        self.write("src/a.py", "a\n")
        phase_base = self.commit("phase 1")
        self.write("CLAUDE.md", _block("- `infra/`", "- `secrets/`"))
        self.commit("phase 2")
        payload, _ = self.assess(
            _plan(["- phase 2 · medium — b"], changes=("`src/`", "`CLAUDE.md`")), phase=2, since=phase_base
        )
        self.assertEqual(
            payload["sensitive"]["matches"],
            [{"pattern": "<!-- pr-evaluator-sensitive-paths -->", "path": "CLAUDE.md"}],
        )
        self.assertEqual((payload["raised"], payload["level"]), (True, "high"))


class CrossConsumerTests(SandboxCase):
    """The epic's cross-consumer pin: one declaration, one PR branch — the merge gate's read and the
    resolver's final-pass read report the same `matches`, `base` and `error`."""

    def check_agreement(self):
        gate = prep_evaluator._sensitive_paths_fact(self.root, "main")
        payload, _ = self.assess(_plan(), phase=2, final=True, base_ref="main")
        resolver = payload["sensitive"]
        self.assertEqual(
            (gate["matches"], gate["base"], gate["error"]),
            (resolver["matches"], payload["range"]["base"], resolver["error"]),
        )
        return gate, payload

    def test_a_match_agrees(self):
        self.write("infra/main.tf", "x\n")
        self.write("src/a.py", "a\n")
        self.commit()
        gate, payload = self.check_agreement()
        self.assertTrue(gate["matches"])
        self.assertTrue(gate["forced_ask"])

    def test_an_unparsed_declaration_line_agrees(self):
        self.write("CLAUDE.md", _block("- `infra/`", "- infra/typo"))
        self.commit()
        gate, payload = self.check_agreement()
        self.assertIn("unreadable declaration", gate["error"])
        self.assertTrue(payload["raised"])


class PlannedSpansTests(SandboxCase):
    """`planned_spans` (#163): the span list the out-of-plan set was computed from, for `review_threads.py`'s
    thread-side narrowing. Reporting it changes nothing else."""

    def test_planned_spans_is_the_union_over_every_plan_and_none_without_one(self):
        self.write("src/a.py", "a\n")
        self.write("lib/guard.rb", "g\n")
        self.commit()
        plan = _plan(changes=("`src/`", "`guard.rb`"))
        record = _plan(changes=("`src/`", "`lib/x.rb`"), tests=())
        payload, _, _ = blast_radius.build_assess(self.root, plan, 1, False, base_ref="main", records=[record])
        expected = list(parse.planned_paths(plan))
        expected += [span for span in parse.planned_paths(record) if span not in expected]
        self.assertEqual(payload["planned_spans"], expected)
        self.assertEqual(payload["planned_spans"], ["src/", "guard.rb", "tests/test_x.py", "lib/x.rb"])
        # A basename span still matches nothing here: the raise is unchanged.
        self.assertEqual((payload["out_of_plan"], payload["raised"]), (["lib/guard.rb"], True))
        no_plan, _ = self.assess(None)
        self.assertIsNone(no_plan["planned_spans"])

    def test_the_raise_is_unchanged_by_the_new_key(self):
        self.write("src/a.py", "a\n")
        self.write("docs/b.md", "b\n")
        self.commit()
        payload, _ = self.assess(_plan(RaiseTests.LEVELS))
        self.assertEqual((payload["out_of_plan"], payload["raised"], payload["level"]), (["docs/b.md"], True, "high"))
        self.assertEqual(set(payload) - {"planned_spans"}, {
            "phase", "final", "declared_level", "declared_source", "range", "changed_paths", "sensitive",
            "out_of_plan", "delivered", "judgment", "raised", "level", "why"})


class LookClockRetiredTests(unittest.TestCase):
    """#155 retired the look's 55-minute clock: the context-usage rule (`phase_review.py`) decides
    whether a session continues past a review, so `look` is no subcommand and its constants are gone."""

    def test_look_is_no_longer_a_subcommand(self):
        for action in ("start", "check"):
            result = subprocess.run(
                [str(SCRIPT), "look", action, "--scratch", tempfile.gettempdir()], capture_output=True, encoding="utf-8"
            )
            self.assertEqual(result.returncode, 2, action)
            self.assertEqual(result.stdout, "")

    def test_the_clock_is_gone(self):
        for name in ("LOOK_WINDOW_SECONDS", "LOOK_STARTED_FILE", "look_start", "look_check"):
            self.assertFalse(hasattr(blast_radius, name), name)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([str(SCRIPT)] + list(args), capture_output=True, encoding="utf-8")

    def test_neither_or_both_range_flags_is_exit_2(self):
        plan = REPO_ROOT / "README.md"
        for flags in ([], ["--base-ref", "main", "--since", "HEAD"]):
            result = self.run_cli("assess", str(REPO_ROOT), "--plan", str(plan), "--phase", "1", *flags)
            self.assertEqual(result.returncode, 2, flags)

    def test_an_unreadable_plan_is_exit_2(self):
        result = self.run_cli("assess", str(REPO_ROOT), "--plan", "/nonexistent/plan.md", "--phase", "1", "--since", "HEAD")
        self.assertEqual(result.returncode, 2)
        self.assertIn("BLAST_RADIUS_PLAN_UNREADABLE", result.stderr)

    def test_an_unreadable_judgment_is_exit_2(self):
        result = self.run_cli(
            "assess", str(REPO_ROOT), "--plan", str(REPO_ROOT / "README.md"), "--judgment", "/nonexistent/j.md",
            "--phase", "1", "--since", "HEAD",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("BLAST_RADIUS_JUDGMENT_UNREADABLE:", result.stderr)

    def test_an_empty_judgment_path_is_exit_2(self):
        result = self.run_cli(
            "assess", str(REPO_ROOT), "--plan", str(REPO_ROOT / "README.md"), "--judgment", "",
            "--phase", "1", "--since", "HEAD",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("BLAST_RADIUS_JUDGMENT_UNREADABLE:", result.stderr)

    def test_an_unreadable_delivered_file_is_exit_2(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        cases = {"missing": None, "invalid": "{not json", "non-list": '{"paths": "src/a.py"}',
                 "non-string": '{"paths": [1]}', "array": '["src/a.py"]'}
        for name, text in cases.items():
            path = tmp / ("%s.json" % name)
            if text is not None:
                path.write_text(text, encoding="utf-8")
            result = self.run_cli(
                "assess", str(REPO_ROOT), "--plan", str(REPO_ROOT / "README.md"), "--delivered", str(path),
                "--phase", "1", "--since", "HEAD",
            )
            self.assertEqual(result.returncode, 2, name)
            self.assertIn("BLAST_RADIUS_DELIVERED_UNREADABLE:", result.stderr, name)


class CliEnvelopeTests(SandboxCase):
    def test_one_ok_envelope(self):
        plan = self.root / "plan.md"
        plan.write_text(_plan(["- phase 1 · medium — a"]), encoding="utf-8")
        result = subprocess.run(
            [str(SCRIPT), "assess", str(self.root), "--plan", str(plan), "--phase", "1", "--since", "HEAD"],
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual((envelope["status"], envelope["level"], envelope["notices"]), ("ok", "medium", []))
        self.assertIsNone(envelope["judgment"])

    def test_one_ok_envelope_with_a_judgment(self):
        plan = self.root / "plan.md"
        plan.write_text(_plan(["- phase 1 · medium — a"]), encoding="utf-8")
        verdict = self.root / "judgment.md"
        verdict.write_text("## Judgment\nverdict: raise\n- seam · src/a.py:1 — e\n", encoding="utf-8")
        result = subprocess.run(
            [str(SCRIPT), "assess", str(self.root), "--plan", str(plan), "--judgment", str(verdict), "--phase", "1",
             "--since", "HEAD"],
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual((envelope["status"], envelope["level"], envelope["raised"]), ("ok", "high", True))
        self.assertEqual(envelope["judgment"]["verdict"], "raise")

    def test_delivered_round_trips_on_the_cli(self):
        self.write("src/a.py", "a\n")
        self.write("docs/b.md", "b\n")
        self.commit()
        plan = self.root / "plan.md"
        plan.write_text("<!-- implementation-plan:v1 -->\n", encoding="utf-8")
        delivered = self.root / "epic-delivered.json"
        delivered.write_text(json.dumps({"prs": [], "paths": ["src/a.py", "docs/b.md"], "unreadable": []}),
                             encoding="utf-8")
        result = subprocess.run(
            [str(SCRIPT), "assess", str(self.root), "--plan", str(plan), "--delivered", str(delivered),
             "--phase", "1", "--final", "--base-ref", "main"],
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["delivered"], {"count": 2, "covered": ["docs/b.md", "src/a.py"]})
        self.assertEqual((envelope["out_of_plan"], envelope["level"]), ([], "high"))


if __name__ == "__main__":
    unittest.main()
