"""Content tests for docs/guides/ui-design.md and its row in docs/guides/README.md (#207).

The guide is shipped inside the plugin and read by doc-reviewer as the rubric for a consuming repo's
ui-design doc, so what it says about the pipeline must match the pipeline: two readers (the planner and
the designer), the design record as a `## UI decisions` citation, the `<!-- ui-prototype -->` block and the
`experience` pause, and the doc's propose-only role — no stage edits it on an issue's PR.
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUIDES = REPO_ROOT / "docs" / "guides"


class UiDesignGuideTests(unittest.TestCase):
    def setUp(self):
        self.guide = " ".join((GUIDES / "ui-design.md").read_text(encoding="utf-8").split())
        readme = (GUIDES / "README.md").read_text(encoding="utf-8")
        rows = [line for line in readme.splitlines() if line.startswith("| `docs/ui-design.md` |")]
        self.assertEqual(len(rows), 1)
        self.row = rows[0]

    def test_the_resolver_never_reads_claim_is_retired(self):
        self.assertNotIn("resolver never reads", self.guide)

    def test_guide_names_the_epic_s_pieces(self):
        for phrase in ("`designer`", "<!-- ux-design:v1 -->", "design record", "<!-- ui-prototype -->",
                       "`experience` pause", "UX conventions", "### Designing before building",
                       "[precedent: design record `<state-id>`]", "`- Design target:` line",
                       "**`U-ux-conventions` UX conventions** *(optional"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.guide)

    def test_guide_states_the_propose_only_role(self):
        self.assertIn("**The doc's role in the build is propose-only.** No stage edits `ui-design.md` on an "
                      "issue's PR as a side effect of building it; a convention stated in a session reaches the doc only through a "
                      "follow-up issue's own PR.",
                      self.guide)
        self.assertIn("never on the issue's PR", self.guide)

    def test_readme_row_matches(self):
        for phrase in ("UX conventions", "`designer`", "design record", "<!-- ui-prototype -->", "`experience` pause",
                       "Propose-only: no stage edits it on an issue's PR as a side effect of building it"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.row)


if __name__ == "__main__":
    unittest.main()
