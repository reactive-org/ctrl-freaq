"""Validator 8 (CLAUDE.md "Validators"), as a committed test: the pipeline never resolves or unresolves a
review thread (#145, `skills/_shared/review-threads.md` §11).

Why it is a test and not a convention: the reader counts this account's own resolution of a thread as a
human's, because the pipeline never resolves one. A single prompt or script that did would let the
pipeline clear its own approval gate. So every file under `skills/` and `scripts/` is scanned, every line
and not only code fences: a docstring or contract example that quotes the mutation is already one
copy-paste from a call. The mutation names are spelled only here, under `tests/`.

Also pinned: the contract's state table names exactly `review_threads.THREAD_STATES`, in order.
"""

import re
import unittest
from pathlib import Path

import review_threads

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("skills", "scripts")
_SKIP_DIRS = {"__pycache__", ".git"}
_SKIP_SUFFIXES = {".pyc", ".pyo"}

# Case-insensitive, so it also matches the unresolve mutation, which embeds it.
FORBIDDEN = re.compile(r"resolveReviewThread", re.IGNORECASE)

CONTRACT = REPO_ROOT / "skills" / "_shared" / "review-threads.md"


def _iter_files(root=REPO_ROOT):
    for entry in SCANNED:
        for path in sorted((root / entry).rglob("*")):
            if not path.is_file() or path.suffix in _SKIP_SUFFIXES:
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            yield path


def scan(root=REPO_ROOT):
    """``(offenders, visited)``: every ``path:line`` carrying the forbidden token, and how many files of
    each scanned tree were read."""
    offenders, visited = [], {entry: 0 for entry in SCANNED}
    for path in _iter_files(root):
        visited[path.relative_to(root).parts[0]] += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append("%s:%d %s" % (path.relative_to(root).as_posix(), number, line.strip()))
    return offenders, visited


class NeverResolveTests(unittest.TestCase):
    def test_no_thread_resolve_or_unresolve_call_under_skills_or_scripts(self):
        offenders, _ = scan()
        self.assertEqual(
            offenders, [],
            "the pipeline never resolves or unresolves a review thread (skills/_shared/review-threads.md "
            "§11); describe the rule in words:\n" + "\n".join(offenders),
        )

    def test_the_scan_reads_both_trees(self):
        # Anti-vacuity: a scan that visited nothing would pass the test above on any tree.
        _, visited = scan()
        for entry in SCANNED:
            self.assertGreater(visited[entry], 0, entry)
        self.assertGreater(visited["scripts"], 10)

    def test_a_planted_violation_fails_the_scan(self):
        import shutil
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-never-resolve-"))
        self.addCleanup(shutil.rmtree, str(root), True)
        (root / "skills" / "x").mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "skills" / "x" / "SKILL.md").write_text("Resolve nothing.\n", encoding="utf-8")
        (root / "scripts" / "clean.py").write_text("print('ok')\n", encoding="utf-8")
        self.assertEqual(scan(root)[0], [])
        (root / "scripts" / "bad.py").write_text(
            'MUTATION = "mutation { unresolveReviewThread(input: {threadId: $t}) { thread { id } } }"\n',
            encoding="utf-8",
        )
        (root / "skills" / "x" / "doc.md").write_text("call `resolveReviewThread` after\n", encoding="utf-8")
        offenders, visited = scan(root)
        self.assertEqual([o.split(" ")[0] for o in offenders], ["skills/x/doc.md:1", "scripts/bad.py:1"])
        self.assertEqual(visited, {"skills": 2, "scripts": 2})


class StateTableTests(unittest.TestCase):
    def test_the_contract_state_table_is_thread_states(self):
        text = CONTRACT.read_text(encoding="utf-8")
        section = text.split("## 4. States", 1)[1].split("\n## ", 1)[0]
        rows = re.findall(r"^\| `([a-z-]+)` \|", section, re.MULTILINE)
        self.assertEqual(tuple(rows), review_threads.THREAD_STATES)

    def test_the_contract_carries_the_marker(self):
        self.assertIn(review_threads.MARKER, CONTRACT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
