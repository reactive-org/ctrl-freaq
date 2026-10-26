"""Unit tests for scripts/phase_review.py — the resolver's context-usage rule and its Review card's
links (#155). Pure cores on a temp scratch dir, plus the CLI's envelope and exit 2.

Coverage: the bands at their edges against a 970,000-token limit (window 1,000,000 × 97%); `unknown`
with no auto-compact point; per-pass growth (a phase run twice is two passes), the max-growth projection
and its crossing; no completed pass → no projection; the ledger appending across calls; the phase and
per-file links; the usage errors; the constants.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "phase_review.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import phase_review  # noqa: E402

WINDOW, PCT, LIMIT = 1_000_000, 97, 970_000


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def read(self, phase, point, used, pct=PCT):
        return phase_review.build_context(self.scratch, phase, point, used, WINDOW, pct)

    def test_the_constants(self):
        self.assertEqual((phase_review.FINISH_SHARE, phase_review.WRAP_SHARE), (0.65, 0.90))

    def test_bands_at_their_edges(self):
        for share, band in ((0.6499, "under"), (0.65, "finish"), (0.8999, "finish"), (0.90, "wrap")):
            payload = self.read(1, "round", round(share * LIMIT))
            self.assertEqual(payload["band"], band, share)
            self.assertEqual(payload["limit_tokens"], LIMIT)
        self.assertEqual(payload["thresholds"], {"finish": 0.65, "wrap": 0.90})

    def test_no_auto_compact_point_is_unknown(self):
        payload = self.read(1, "start", 500_000, pct=None)
        self.assertEqual((payload["band"], payload["share"], payload["projected_share"]), ("unknown", None, None))
        self.assertIsNone(payload["limit_tokens"])
        self.assertFalse(payload["projection_crosses_wrap"])

    def test_no_completed_pass_means_no_projection(self):
        self.read(1, "start", 100_000)
        payload = self.read(1, "round", 200_000)
        self.assertEqual(payload["phase_growth"], [])
        self.assertIsNone(payload["projected_share"])
        self.assertFalse(payload["projection_crosses_wrap"])

    def test_growth_and_the_max_growth_projection(self):
        self.read(1, "start", 100_000)
        self.read(1, "end", 300_000)          # phase 1 grew 200,000
        self.read(2, "start", 300_000)
        payload = self.read(2, "end", 400_000)  # phase 2 grew 100,000
        self.assertEqual(payload["phase_growth"], [{"phase": 1, "tokens": 200_000}, {"phase": 2, "tokens": 100_000}])
        self.assertAlmostEqual(payload["projected_share"], (400_000 + 200_000) / LIMIT)
        self.assertFalse(payload["projection_crosses_wrap"])
        crossing = self.read(3, "start", 700_000)
        self.assertAlmostEqual(crossing["projected_share"], 900_000 / LIMIT)
        self.assertTrue(crossing["projection_crosses_wrap"])

    def test_a_phase_run_twice_is_two_passes(self):
        # A changes-required pass of a shipped phase opens with its own `start` after the card's answer.
        self.read(1, "start", 100_000)
        self.read(1, "end", 250_000)
        self.read(1, "answer", 260_000)
        self.read(1, "start", 270_000)
        payload = self.read(1, "end", 300_000)
        self.assertEqual(payload["phase_growth"], [{"phase": 1, "tokens": 150_000}, {"phase": 1, "tokens": 30_000}])

    def test_an_unpaired_end_is_ignored_and_negative_growth_floors_at_zero(self):
        self.read(1, "end", 100_000)
        self.read(2, "start", 500_000)
        payload = self.read(2, "end", 200_000)   # the host compacted mid-pass
        self.assertEqual(payload["phase_growth"], [{"phase": 2, "tokens": 0}])

    def test_the_ledger_appends_across_calls(self):
        self.read(1, "start", 1)
        self.read(1, "round", 2)
        lines = (self.scratch / phase_review.USAGE_FILE).read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["used"] for line in lines], [1, 2])

    def test_invalid_arguments_raise(self):
        for kwargs in ({"window": 0}, {"used": -1}, {"auto_compact_pct": 0}, {"auto_compact_pct": 101}, {"point": "x"}):
            args = dict(scratch=self.scratch, phase=1, point="start", used=1, window=WINDOW, auto_compact_pct=PCT)
            args.update(kwargs)
            with self.assertRaises(ValueError, msg=kwargs):
                phase_review.build_context(**args)


class LinksTests(unittest.TestCase):
    PR = "https://github.com/octo/widgets/pull/7"

    def test_a_phase_scoped_link_per_file(self):
        payload = phase_review.build_links(self.PR, ["b.py", "a/c.md"], entry="a" * 40, head="b" * 40)
        self.assertEqual(payload["phase_url"], self.PR + "/files/aaaaaaa..bbbbbbb")
        self.assertEqual([f["path"] for f in payload["files"]], ["b.py", "a/c.md"])
        self.assertEqual(
            payload["files"][1]["url"],
            self.PR + "/files/aaaaaaa..bbbbbbb#diff-" + hashlib.sha256(b"a/c.md").hexdigest(),
        )

    def test_no_entry_is_the_whole_files_view(self):
        payload = phase_review.build_links(self.PR + "/", ["x"])
        self.assertEqual(payload["phase_url"], self.PR + "/files")
        self.assertTrue(payload["files"][0]["url"].startswith(self.PR + "/files#diff-"))

    def test_zero_paths(self):
        self.assertEqual(phase_review.build_links(self.PR, [])["files"], [])

    def test_entry_and_head_go_together(self):
        for kwargs in ({"entry": "a" * 40}, {"head": "b" * 40}):
            with self.assertRaises(ValueError):
                phase_review.build_links(self.PR, [], **kwargs)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def run_cli(self, *args):
        return subprocess.run([str(SCRIPT)] + list(args), capture_output=True, encoding="utf-8")

    def test_context_round_trip(self):
        result = self.run_cli("context", "--scratch", str(self.scratch), "--phase", "1", "--point", "start",
                              "--used", "485000", "--window", str(WINDOW), "--auto-compact-pct", "97")
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual((envelope["status"], envelope["band"], envelope["share"]), ("ok", "under", 0.5))

    def test_links_round_trip(self):
        result = self.run_cli("links", "--pr-url", LinksTests.PR, "--entry", "a" * 40, "--head", "b" * 40, "x.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["files"][0]["path"], "x.py")

    def test_usage_errors_exit_2(self):
        for args in (
            [],
            ["context", "--scratch", str(self.scratch), "--phase", "1", "--point", "start", "--window", "10"],
            ["context", "--scratch", str(self.scratch), "--phase", "1", "--point", "start", "--used", "1",
             "--window", "0"],
            ["links", "--pr-url", LinksTests.PR, "--entry", "a" * 40],
        ):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertEqual(result.stdout, "", args)


if __name__ == "__main__":
    unittest.main()
