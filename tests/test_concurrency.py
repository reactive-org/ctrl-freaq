"""Unit tests for scripts/concurrency.py — the reader of an epic plan's concurrency declaration
(``skills/planner/references/plan-schema.md``, "The concurrency declaration").

The module is **pure** — a plan body in, a value out — so every test drives it in-process with no
`gh`, no shim and no sandbox. ``prep_coordinator.py`` composes it (#115); that suite pins the
projection.

What this suite exists to protect:

  - **The declared read.** The schema's own worked example returns every gate, edge field and set it
    declares, so the grammar the planner writes is the grammar the reader reads.
  - **The serial fallback, on every failure.** An absent or malformed declaration returns each story
    waiting to start on its predecessor — never an empty ``start`` list, which would make every story
    startable at once. Every per-reason test asserts the fallback, not just the reason.
  - **One cause, one entry.** Each closed ``malformed`` reason is anchored where the plan pins it, and
    a structural failure suppresses the checks that would only echo it.
  - **#108's hand-written declaration reads as ``header``**, which is why #108's plan is migrated by a
    planner revise rather than read through a legacy tier.
"""

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "concurrency"
SCHEMA = REPO_ROOT / "skills" / "planner" / "references" / "plan-schema.md"

sys.path.insert(0, str(SCRIPTS_DIR))

import concurrency  # noqa: E402

HEADER = "| Story | Waits on | Kind | Gates | Condition |"

BREAKDOWN = ["#1", "#2", "#3"]
ROWS = [
    "| #1 | — | — | — | — |",
    "| #2 | — | — | — | — |",
    "| #3 | #1, #2 | contract `core` | start | — |",
]
SETS = [
    "- **Wave 1: #1 and #2.** Both at once.",
    "- **Wave 2: #3.** After wave 1.",
]


def plan(breakdown=BREAKDOWN, rows=ROWS, sets=SETS, header=HEADER, graph=True, overlap=False, extra=()):
    lines = ["<!-- implementation-plan:v1 -->", "", "## Story breakdown"]
    lines += ["- %s \"Story %s\" — scope" % (story, story[1:]) for story in breakdown]
    lines += ["", "## Integration strategy", "", "Prose about convergence.", ""]
    if graph:
        lines += ["### Dependency graph", "", header, "|---|---|---|---|---|"] + list(rows) + [""]
    if overlap:
        lines += [
            "### File overlap",
            "",
            "| Surface | Stories | Resolution |",
            "|---|---|---|",
            "| `docs/prd.md` | #3 | routed to the finalization story |",
            "",
        ]
    if sets is not None:
        lines += ["### Concurrent sets", ""] + list(sets) + [""]
    lines += list(extra)
    lines += ["## Risks & watchpoints", "- (none)"]
    return "\n".join(lines) + "\n"


def entry(body, raw, reason, section="Dependency graph"):
    line = body.splitlines().index(raw) + 1
    return {"section": section, "line": line, "raw": raw, "reason": reason}


def serial_numbers(result):
    return [(story["number"], [edge["waits_on"] for edge in story["start"]]) for story in result["stories"]]


class DeclaredReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = SCHEMA.read_text(encoding="utf-8").splitlines()
        start = next(i for i, line in enumerate(text) if line.startswith("### The concurrency declaration"))
        open_fence = next(i for i in range(start, len(text)) if text[i].startswith("```"))
        close_fence = next(i for i in range(open_fence + 1, len(text)) if text[i].startswith("```"))
        cls.result = concurrency.read("\n".join(text[open_fence + 1 : close_fence]))
        cls.by_number = {story["number"]: story for story in cls.result["stories"]}

    def test_the_schema_example_is_declared_and_clean(self):
        self.assertEqual(self.result["source"], concurrency.SOURCE_DECLARED)
        self.assertIsNone(self.result["fallback_reason"])
        self.assertEqual(self.result["malformed"], [])
        self.assertEqual([story["number"] for story in self.result["stories"]], [201, 202, 203, 204])

    def test_the_root_story_has_no_gates(self):
        root = self.by_number[201]
        self.assertEqual((root["start"], root["build"], root["merge"]), ([], [], []))

    def test_a_multi_story_waits_on_is_one_edge_per_story(self):
        self.assertEqual([edge["waits_on"] for edge in self.by_number[204]["start"]], [201, 202, 203])
        self.assertTrue(all(edge["kind"] == "order" for edge in self.by_number[204]["start"]))

    def test_a_two_name_contract_edge_carries_both_names(self):
        (edge,) = self.by_number[203]["start"]
        self.assertEqual(edge["kind"], "contract")
        self.assertEqual(edge["contracts"], ["parser core", "parser facts"])
        self.assertIsNone(edge["surface"])

    def test_a_conditional_file_overlap_edge_lands_under_build(self):
        story = self.by_number[202]
        self.assertEqual(story["start"], [])
        self.assertEqual(
            story["build"],
            [
                {
                    "waits_on": 201,
                    "kind": "file overlap",
                    "contracts": [],
                    "surface": "scripts/prep_example.py",
                    "condition": "#202's findings call for a layout change",
                }
            ],
        )

    def test_a_merge_order_edge_lands_under_merge(self):
        (edge,) = self.by_number[203]["merge"]
        self.assertEqual((edge["waits_on"], edge["kind"], edge["condition"]), (202, "merge order", None))

    def test_each_story_carries_its_set(self):
        self.assertEqual(
            [(story["number"], story["set"]) for story in self.result["stories"]],
            [(201, "Wave 1"), (202, "Wave 1"), (203, "Wave 2"), (204, "Wave 3")],
        )
        self.assertEqual(
            self.result["sets"],
            [
                {"name": "Wave 1", "stories": [201, 202]},
                {"name": "Wave 2", "stories": [203]},
                {"name": "Wave 3", "stories": [204]},
            ],
        )

    def test_the_result_round_trips_through_json(self):
        self.assertEqual(json.loads(json.dumps(self.result)), self.result)


class AbsentFallbackTests(unittest.TestCase):
    def test_a_prose_only_strategy_falls_back_to_serial(self):
        result = concurrency.read(plan(graph=False, sets=None))
        self.assertEqual((result["source"], result["fallback_reason"]), (concurrency.SOURCE_SERIAL, "absent"))
        self.assertEqual(serial_numbers(result), [(1, []), (2, [1]), (3, [2])])
        for story in result["stories"][1:]:
            (edge,) = story["start"]
            self.assertEqual(
                edge, {"waits_on": edge["waits_on"], "kind": "order", "contracts": [], "surface": None, "condition": None}
            )
        self.assertTrue(all(story["set"] is None for story in result["stories"]))
        self.assertEqual((result["sets"], result["malformed"]), ([], []))

    def test_empty_and_breakdown_less_input_is_absent_and_never_raises(self):
        for body in ("", "## Integration strategy\n\n### Dependency graph\n\n" + HEADER + "\n", None):
            result = concurrency.read(body)
            self.assertEqual(
                result,
                {"source": "serial-fallback", "fallback_reason": "absent", "stories": [], "sets": [], "malformed": []},
            )

    def test_overlap_and_free_subsections_do_not_change_the_result(self):
        extra = ["### Merging inside a wave", "", "- **Not a set: #9.** Free prose.", "| #9 | x |", ""]
        self.assertEqual(concurrency.read(plan(overlap=True, extra=extra)), concurrency.read(plan()))

    def test_and_reads_like_a_comma_in_a_set_bullet(self):
        comma = concurrency.read(plan(sets=["- **Wave 1: #1, #2.** Both.", SETS[1]]))
        self.assertEqual(comma, concurrency.read(plan()))
        self.assertEqual(comma["sets"][0]["stories"], [1, 2])


class MalformedReasonTests(unittest.TestCase):
    """One minimal plan per closed reason: exactly the one expected entry, and the serial fallback."""

    def assert_malformed(self, body, expected):
        result = concurrency.read(body)
        self.assertEqual(result["malformed"], expected)
        self.assertEqual((result["source"], result["fallback_reason"]), (concurrency.SOURCE_SERIAL, "malformed"))
        self.assertEqual(serial_numbers(result), [(1, []), (2, [1]), (3, [2])])
        self.assertEqual(result["sets"], [])
        for reason in (e["reason"] for e in expected):
            self.assertIn(reason, concurrency.REASONS)

    def test_section_graph_without_sets(self):
        body = plan(sets=None)
        self.assert_malformed(body, [entry(body, "### Dependency graph", "section")])

    def test_section_sets_without_graph(self):
        body = plan(graph=False)
        self.assert_malformed(body, [entry(body, "### Concurrent sets", "section", section="Concurrent sets")])

    def test_header_is_reported_alone(self):
        header = "| Story | Waits on | Kind |"
        body = plan(header=header, rows=["| #3 | #1 | contract `core`; gates the start |"])
        self.assert_malformed(body, [entry(body, header, "header")])

    def test_cells(self):
        raw = "| #3 | #1, #2 | contract `core` | start |"
        body = plan(rows=ROWS[:2] + [raw])
        self.assert_malformed(body, [entry(body, raw, "cells")])

    def test_story(self):
        raw = "| 3 | #1, #2 | contract `core` | start | — |"
        body = plan(rows=ROWS[:2] + [raw])
        self.assert_malformed(body, [entry(body, raw, "story")])

    def test_waits_on_self(self):
        raw = "| #3 | #3 | order | start | — |"
        body = plan(rows=ROWS + [raw])
        self.assert_malformed(body, [entry(body, raw, "waits-on")])

    def test_waits_on_off_grammar(self):
        raw = "| #3 | #1 and #2 | contract `core` | start | — |"
        body = plan(rows=ROWS[:2] + [raw])
        self.assert_malformed(body, [entry(body, raw, "waits-on")])

    def test_waits_on_partial_root_row(self):
        raw = "| #2 | — | order | start | — |"
        body = plan(rows=[ROWS[0], raw, ROWS[2]])
        self.assert_malformed(body, [entry(body, raw, "waits-on")])

    def test_kind(self):
        raw = "| #3 | #1, #2 | file overlap on `x` | start | — |"
        body = plan(rows=ROWS[:2] + [raw])
        self.assert_malformed(body, [entry(body, raw, "kind")])

    def test_gates(self):
        raw = "| #3 | #1, #2 | contract `core` | the start | — |"
        body = plan(rows=ROWS[:2] + [raw])
        self.assert_malformed(body, [entry(body, raw, "gates")])

    def test_kind_gates_contract_build(self):
        raw = "| #3 | #1, #2 | contract `core` | build | — |"
        body = plan(rows=ROWS[:2] + [raw])
        self.assert_malformed(body, [entry(body, raw, "kind-gates")])

    def test_the_legal_pairs_are_the_schema_table(self):
        self.assertEqual(
            concurrency.LEGAL_GATES,
            {
                "contract": ("start",),
                "file overlap": ("start", "build"),
                "order": ("start", "build"),
                "merge order": ("merge",),
            },
        )

    def test_kind_gates_file_overlap_merge(self):
        raw = "| #3 | #1, #2 | file overlap `x` | merge | — |"
        body = plan(rows=ROWS[:2] + [raw])
        self.assert_malformed(body, [entry(body, raw, "kind-gates")])

    def test_unlisted_story(self):
        raw = "| #3 | #9 | order | start | — |"
        body = plan(rows=ROWS + [raw])
        self.assert_malformed(body, [entry(body, raw, "unlisted-story")])

    def test_no_row(self):
        body = plan(rows=ROWS[:2] + ["| #3 | #1 | contract `core` | start | — |"], breakdown=BREAKDOWN + ["#4"],
                    sets=[SETS[0], "- **Wave 2: #3 and #4.** After wave 1."])
        result = concurrency.read(body)
        self.assertEqual(result["malformed"], [entry(body, '- #4 "Story 4" — scope', "no-row", section="Story breakdown")])
        self.assertEqual(result["source"], concurrency.SOURCE_SERIAL)
        self.assertEqual(serial_numbers(result), [(1, []), (2, [1]), (3, [2]), (4, [3])])

    def test_set_story_in_two_sets(self):
        raw = "- **Wave 2: #2 and #3.** After wave 1."
        body = plan(sets=[SETS[0], raw])
        self.assert_malformed(body, [entry(body, raw, "set", section="Concurrent sets")])

    def test_set_story_in_no_set(self):
        body = plan(sets=[SETS[0]], rows=ROWS[:2] + ["| #3 | — | — | — | — |"])
        self.assert_malformed(body, [entry(body, '- #3 "Story 3" — scope', "set", section="Story breakdown")])

    def test_set_order_start_within_one_set(self):
        raw = "| #2 | #1 | order | start | — |"
        body = plan(rows=[ROWS[0], raw, ROWS[2]])
        self.assert_malformed(body, [entry(body, raw, "set-order")])

    def test_set_order_build_on_a_later_set(self):
        raw = "| #1 | #3 | order | build | — |"
        body = plan(rows=[raw, ROWS[1], "| #3 | #2 | contract `core` | start | — |"])
        self.assert_malformed(body, [entry(body, raw, "set-order")])

    def test_cycle_inside_one_set_reports_alone(self):
        first = "| #1 | #2 | order | build | — |"
        body = plan(rows=[first, "| #2 | #1 | merge order | merge | — |", ROWS[2]])
        self.assert_malformed(body, [entry(body, first, "cycle")])


class Epic108FixtureTests(unittest.TestCase):
    def test_the_hand_written_declaration_reads_as_header_and_serial(self):
        body = (FIXTURES / "epic-108-plan.md").read_text(encoding="utf-8")
        result = concurrency.read(body)
        self.assertEqual([e["reason"] for e in result["malformed"]], ["header"])
        self.assertEqual(result["malformed"][0]["raw"], "| Story | Waits on | Kind |")
        self.assertEqual(result["source"], concurrency.SOURCE_SERIAL)
        self.assertEqual(
            serial_numbers(result),
            [(111, []), (112, [111]), (113, [112]), (114, [113]), (115, [114]), (116, [115]), (117, [116])],
        )


if __name__ == "__main__":
    unittest.main()
