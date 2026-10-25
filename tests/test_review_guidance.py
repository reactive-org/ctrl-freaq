"""Unit tests for scripts/review_guidance.py — the consuming repo's owner-written
``<!-- issue-resolver-review-guidance -->`` block (#99).

File I/O only — no `gh`, no `git`, no shim — so every test drives the pure functions in-process against
real temp files. What this suite protects:

  - **Flag neutralisation.** A bare reviewer flag in the owner's prose would make the resolver's own
    review post inline comments or apply its findings; ``neutralise`` is the only thing between the
    owner's text and the reviewer's argument parser, so each token shape is pinned, with the near-miss
    tokens (``--fixture``, ``--commentary``, an already-spanned flag) as positive controls.
  - **Absent ≡ empty.** The reviewer gets nothing from an empty block, so ``read`` reports it as
    absent, the same as no block and as a malformed one.
  - **Exact-key ``listed``.** A key is listed only as its own code span, never as a prefix of a
    longer key — otherwise setup would skip proposing a class the block never named.
"""

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

import review_guidance  # noqa: E402  (import after sys.path setup, by necessity)

ABSENT = {"present": False, "source": None, "text": None, "neutralised": [], "chars": 0}


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _block(interior):
    return "<!-- issue-resolver-review-guidance -->\n%s<!-- /issue-resolver-review-guidance -->\n" % interior


class NeutraliseTests(unittest.TestCase):
    def test_wraps_bare_flags_and_flag_assignments(self):
        text, wrapped = review_guidance.neutralise("Never --fix here; cap at --max-findings=3 please.")
        self.assertEqual(text, "Never `--fix` here; cap at `--max-findings=3` please.")
        self.assertEqual(wrapped, ["--fix", "--max-findings=3"])

    def test_wraps_a_flag_standing_between_spaces_inside_a_longer_code_span(self):
        # The parser reads whitespace-delimited tokens, so a flag inside a multi-word span is still
        # read; only a span attached to the token itself hides it.
        text, wrapped = review_guidance.neutralise("Run `review --comment now` locally.")
        self.assertEqual(text, "Run `review `--comment` now` locally.")
        self.assertEqual(wrapped, ["--comment"])

    def test_leaves_spanned_flags_and_near_miss_tokens_alone(self):
        source = "Write `--post` as a span; --fixture and --commentary are not flags."
        text, wrapped = review_guidance.neutralise(source)
        self.assertEqual(text, source)
        self.assertEqual(wrapped, [])

    def test_every_reviewer_flag_is_wrapped_and_whitespace_is_preserved(self):
        source = "\n".join(review_guidance.REVIEWER_FLAGS) + "\n\t--fix  "
        text, wrapped = review_guidance.neutralise(source)
        self.assertEqual(wrapped, list(review_guidance.REVIEWER_FLAGS) + ["--fix"])
        self.assertEqual(text, "\n".join("`%s`" % f for f in review_guidance.REVIEWER_FLAGS) + "\n\t`--fix`  ")


class ReadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_block_reads_absent(self):
        _write(self.root / "COMMANDS.md", "# Commands\n")
        self.assertEqual(review_guidance.read(str(self.root)), ABSENT)

    def test_empty_interior_reads_absent(self):
        _write(self.root / "COMMANDS.md", _block(""))
        self.assertEqual(review_guidance.read(str(self.root)), ABSENT)
        _write(self.root / "COMMANDS.md", _block("\n  \n"))
        self.assertEqual(review_guidance.read(str(self.root)), ABSENT)

    def test_duplicate_or_unterminated_block_reads_absent(self):
        _write(self.root / "COMMANDS.md", _block("- one\n") + _block("- two\n"))
        self.assertEqual(review_guidance.read(str(self.root)), ABSENT)
        _write(self.root / "COMMANDS.md", "<!-- issue-resolver-review-guidance -->\n- open\n")
        self.assertEqual(review_guidance.read(str(self.root)), ABSENT)

    def test_well_formed_block_in_either_candidate_file(self):
        interior = "## Review guidance\n**Skip:** `vendor/`; never --fix generated code.\n"
        for name in ("COMMANDS.md", "CLAUDE.md"):
            with self.subTest(file=name):
                for other in ("COMMANDS.md", "CLAUDE.md"):
                    (self.root / other).unlink(missing_ok=True)
                _write(self.root / name, "# Intro\n" + _block(interior))
                facts = review_guidance.read(str(self.root))
                expected_text = "## Review guidance\n**Skip:** `vendor/`; never `--fix` generated code."
                self.assertEqual(facts, {
                    "present": True,
                    "source": str(self.root / name),
                    "text": expected_text,
                    "neutralised": ["--fix"],
                    "chars": len(expected_text),
                })

    def test_malformed_block_in_one_file_falls_through_to_the_other(self):
        _write(self.root / "COMMANDS.md", "<!-- issue-resolver-review-guidance -->\n- open\n")
        _write(self.root / "CLAUDE.md", _block("- from claude\n"))
        facts = review_guidance.read(str(self.root))
        self.assertTrue(facts["present"])
        self.assertEqual(facts["source"], str(self.root / "CLAUDE.md"))
        self.assertEqual(facts["text"], "- from claude")


class ListedTests(unittest.TestCase):
    def test_only_exact_code_spans_count(self):
        text = "- watch for it — recurring: `stale-cache` · 3 findings\n- `nil-guard-missing` too\nbare race word"
        keys = ["stale-cache", "stale", "nil-guard", "nil-guard-missing", "race"]
        self.assertEqual(review_guidance.listed(text, keys), {"stale-cache", "nil-guard-missing"})

    def test_no_text_lists_nothing(self):
        self.assertEqual(review_guidance.listed(None, ["a"]), set())
        self.assertEqual(review_guidance.listed("", ["a"]), set())


if __name__ == "__main__":
    unittest.main()
