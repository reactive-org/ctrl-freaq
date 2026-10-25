"""Unit tests for scripts/blast_radius.py — the resolver's review level for one loop pass (#97). A real
temp git origin+clone (tests/support/gitsandbox); no `gh` shim, the script spawns only `git`.

Coverage: the declared level and its `high` default (absent, `low`/`max`, duplicated lines); `--final`
taking the highest declared level, an undeclared phase counting as `high`, a plan without `## Phases`
expecting `[1]`; the raise (a sensitive match, an unparsed declaration line, an out-of-plan file), one
step and capped at `xhigh`; planned spans (a `## Test plan`-only file, a `dir/` span); `--since`
excluding earlier phases and catching a declaration edit inside the phase; the CLI's exit 2; the look's
55-minute window (phase 2); and the cross-consumer pin — prep_evaluator's merge-gate read and `build_assess` agree on one branch.
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


class LookWindowTests(unittest.TestCase):
    """The look's clock (#97 phase 2): a 55-minute window from the card's first render, the clock
    injected through `now`; a missing or unreadable start file is outside it."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def test_the_window_is_3300_seconds_inclusive(self):
        self.assertEqual(blast_radius.LOOK_WINDOW_SECONDS, 3300)
        self.assertEqual(blast_radius.look_start(self.scratch, now=1000), {"started_at": 1000})
        self.assertEqual(
            blast_radius.look_check(self.scratch, now=4300),
            {"started_at": 1000, "elapsed_seconds": 3300, "within_window": True},
        )
        self.assertFalse(blast_radius.look_check(self.scratch, now=4301)["within_window"])

    def test_no_start_file_is_outside_the_window(self):
        self.assertEqual(
            blast_radius.look_check(self.scratch, now=1),
            {"started_at": None, "elapsed_seconds": None, "within_window": False},
        )

    def test_an_unreadable_start_file_is_outside_the_window(self):
        (self.scratch / blast_radius.LOOK_STARTED_FILE).write_text("not a time\n", encoding="utf-8")
        self.assertFalse(blast_radius.look_check(self.scratch, now=1)["within_window"])

    def test_cli_round_trip(self):
        for action, key in (("start", "started_at"), ("check", "within_window")):
            result = subprocess.run(
                [str(SCRIPT), "look", action, "--scratch", str(self.scratch)], capture_output=True, encoding="utf-8"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            envelope = json.loads(result.stdout)
            self.assertEqual(envelope["status"], "ok")
            self.assertIn(key, envelope)
        self.assertTrue(envelope["within_window"])


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


if __name__ == "__main__":
    unittest.main()
