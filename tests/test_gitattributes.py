"""Unit tests for scripts/gitattributes.py — the single write path for the consuming repo's
`linguist-generated` block in its repo-root `.gitattributes` (#181). Driven as a real subprocess, so
each test exercises the argv-in / envelope-out path setup's flow uses.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gitattributes.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gitattributes  # noqa: E402  (import after sys.path setup, by necessity)

BEGIN = "# ctrl-freaq:linguist-generated:begin"
END = "# ctrl-freaq:linguist-generated:end"


def _run(args):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)] + args, capture_output=True, text=True, encoding="utf-8"
    )
    envelope = json.loads(completed.stdout) if completed.stdout.strip() else None
    return completed.returncode, envelope, completed.stderr


class GitattributesTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="gitattributes-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.file = self.dir / ".gitattributes"
        self.body = self.dir / "body.txt"

    def _body(self, *lines):
        self.body.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return str(self.body)

    def test_constants_are_the_contract_fence(self):
        self.assertEqual((gitattributes.BEGIN, gitattributes.END), (BEGIN, END))

    def test_upsert_creates_the_file(self):
        rc, env, _ = _run(["upsert", str(self.file), self._body("sorbet/rbi/** linguist-generated")])
        self.assertEqual(rc, 0)
        self.assertEqual(env["status"], "ok")
        self.assertTrue(env["changed"])
        self.assertTrue(env["created"])
        self.assertEqual(
            self.file.read_text(encoding="utf-8"),
            "%s\nsorbet/rbi/** linguist-generated\n%s\n" % (BEGIN, END),
        )

    def test_upsert_appends_the_block_after_existing_lines(self):
        self.file.write_bytes(b"*.png binary\n*.sh text eol=lf")  # no trailing newline
        _run(["upsert", str(self.file), self._body("gen/** linguist-generated")])
        self.assertEqual(
            self.file.read_bytes(),
            ("*.png binary\n*.sh text eol=lf\n%s\ngen/** linguist-generated\n%s\n" % (BEGIN, END)).encode(),
        )

    def test_upsert_replaces_only_the_fenced_block(self):
        before = "*.png binary\r\n%s\nold/** linguist-generated\n%s\n# trailing comment\r\n" % (BEGIN, END)
        self.file.write_bytes(before.encode("utf-8"))
        rc, env, _ = _run([
            "upsert", str(self.file),
            self._body("a/** linguist-generated", "", "b/*.ts linguist-generated"),
        ])
        self.assertEqual(rc, 0)
        self.assertTrue(env["changed"])
        self.assertFalse(env["created"])
        self.assertEqual(env["lines"], ["a/** linguist-generated", "b/*.ts linguist-generated"])
        self.assertEqual(
            self.file.read_bytes(),
            ("*.png binary\r\n%s\na/** linguist-generated\nb/*.ts linguist-generated\n%s\n"
             "# trailing comment\r\n" % (BEGIN, END)).encode("utf-8"),
        )

    def test_upsert_of_the_same_lines_is_unchanged(self):
        self.file.write_text("%s\na/** linguist-generated\n%s\n" % (BEGIN, END), encoding="utf-8")
        rc, env, _ = _run(["upsert", str(self.file), self._body("a/** linguist-generated")])
        self.assertEqual(rc, 0)
        self.assertFalse(env["changed"])

    def test_upsert_dry_run_writes_nothing(self):
        rc, env, _ = _run(["upsert", str(self.file), self._body("a/** linguist-generated"), "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertTrue(env["changed"])
        self.assertFalse(self.file.exists())

    def test_upsert_refuses_a_line_that_is_not_linguist_generated(self):
        for bad in ("a/** binary", "a/** linguist-generated=false", "linguist-generated", "a b linguist-generated"):
            rc, env, err = _run(["upsert", str(self.file), self._body("ok/** linguist-generated", bad)])
            self.assertEqual(rc, 2, bad)
            self.assertIsNone(env)
            self.assertIn("INVALID_BODY_LINE", err)
            self.assertFalse(self.file.exists())

    def test_read_of_an_absent_file_and_a_file_without_a_block(self):
        rc, env, _ = _run(["read", str(self.file)])
        self.assertEqual((rc, env["status"], env["present"], env["lines"]), (0, "ok", False, []))
        self.file.write_text("*.png binary\n", encoding="utf-8")
        rc, env, _ = _run(["read", str(self.file)])
        self.assertEqual((env["present"], env["lines"]), (False, []))

    def test_read_reports_the_interior(self):
        self.file.write_text("x\n%s\na/** linguist-generated\n%s\n" % (BEGIN, END), encoding="utf-8")
        rc, env, _ = _run(["read", str(self.file)])
        self.assertEqual((env["present"], env["lines"]), (True, ["a/** linguist-generated"]))

    def test_malformed_fences_are_marker_ambiguous_and_write_nothing(self):
        cases = {
            "two begins": "%s\n%s\n%s\n" % (BEGIN, BEGIN, END),
            "two ends": "%s\n%s\n%s\n" % (BEGIN, END, END),
            "begin, no end": "%s\na/** linguist-generated\n" % BEGIN,
            "end, no begin": "%s\n" % END,
            "end before begin": "%s\n%s\n" % (END, BEGIN),
        }
        for name, text in cases.items():
            self.file.write_text(text, encoding="utf-8")
            for args in (["read", str(self.file)],
                         ["upsert", str(self.file), self._body("a/** linguist-generated")]):
                rc, env, _ = _run(args)
                self.assertEqual(rc, 0, name)
                self.assertEqual(env["status"], "needs_decision", name)
                self.assertEqual(env["decision"]["code"], "MARKER_AMBIGUOUS", name)
            self.assertEqual(self.file.read_text(encoding="utf-8"), text, name)


if __name__ == "__main__":
    unittest.main()
