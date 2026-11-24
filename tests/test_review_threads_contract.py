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
import sys
import unittest
from pathlib import Path

# Run alone (`python3 -m unittest tests.<module>`, as CLAUDE.md's validators do), scripts/ is not on the path:
# tests/run.py adds it for the suite, so add it here too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import review_threads  # noqa: E402 (import after sys.path setup, by necessity)

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


class DesignSummaryContractTests(unittest.TestCase):
    """#241: the contract's design summary — its stamp field, its clearance with no `adopted` condition, a hunk
    exemption that is its alone, its closings order and its retirements — names what `review_threads.py` does."""

    def setUp(self):
        self.flat = " ".join(CONTRACT.read_text(encoding="utf-8").split())
        self.check = " ".join((REPO_ROOT / "skills" / "_shared" / "design-check.md").read_text(encoding="utf-8").split())

    def _section(self, start, end):
        head = self.flat.index(start)
        return self.flat[head:self.flat.index(end, head)]

    def test_the_stamp_carries_summary_after_target_and_before_phase(self):
        self.assertIn("[ · group sensitive\\|design][ · advisory][ · target <12 hex>][ · summary <12 hex>][ · phase <N>]",
                      self.flat)
        self.assertEqual(review_threads.GROUPS, ("sensitive", "design"))

    def test_an_accepted_summary_clears_whatever_adopted_reads(self):
        states = self._section("## 4. States", "## 5. Trust")
        self.assertIn("a design summary clears whatever `adopted` reads (#241)", states)
        self.assertIn("an `accepted` summary clears every difference it lists whatever `adopted` reads", states)
        self.assertIn("the gate reads `design.adopted` and `design.summary`, never a reply's text", states)

    def test_the_hunk_exemption_is_the_summarys_alone(self):
        hunk = self._section("## 6. Hunk unchanged", "## 7. Flags")
        self.assertIn("**A design summary** (#241, a trusted root whose `summary` is read, §2) runs no check at all",
                      hunk)
        self.assertIn("The exemption is the summary's alone: every other thread, a design group and a single-state "
                      "design thread included, keeps the hunk-unchanged rule.", hunk)

    def test_the_key_row_names_no_path_and_the_retirements_are_listed(self):
        flags = self._section("## 7. Flags", "## 8.")
        self.assertIn("| `design-departure:<id>` |", flags)
        self.assertIn("It names no path, as a security key names none", flags)
        self.assertEqual(review_threads.flag_key_parts("design-departure:D1"), ("design-departure", None))
        for reason in review_threads.DESIGN_RETIREMENTS:
            self.assertIn("`%s`" % reason, flags, reason)
        self.assertIn("Its `full` value holds all seven kinds", flags)

    def test_the_closing_order_follows_closing_kind(self):
        template = self._section("### Thread template", "`decision` (#169)")
        order = [template.index(s) for s in ("any `security:` key →", "a design summary (every key",
                                             "any `design:` key, a design group's included →", "a sensitive group →",
                                             "a batch →", "otherwise →")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(review_threads.closing_kind(["security:SEC-1", "design-departure:D1"]), "security")
        self.assertEqual(review_threads.closing_kind(["design-departure:D1", "design-departure:U2"]), "design-summary")
        self.assertEqual(review_threads.closing_kind(["design:empty:a.py", "design:error:a.py"], group="design"),
                         "design")

    def test_design_check_states_the_departures_section_and_its_sub_steps(self):
        for token in ("## The departures section", "- **Writer.** The resolver only",
                      "A difference the build did not intend is a fix, never a departure.",
                      "3.1. **Stage the explanations** (#241)", "3.2. **Classify** (#241)",
                      "4. **Name the sites.**", "6. **Upload the captures** (#237)",
                      "`DESIGN_CLASSIFICATION_UNREAD`", "`DESIGN_DEPARTURES_UNTRUSTED`",
                      "with no `adopted` condition (`threads[].design.summary`)"):
            self.assertIn(token, self.check, token)


if __name__ == "__main__":
    unittest.main()
