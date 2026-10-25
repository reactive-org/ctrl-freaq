"""Unit tests for scripts/parse.py — the S5 shared-body-grammar parser (architecture.md §2, §3;
docs/implementation.md S5).

This is a **from-scratch v2 script** — there is no v1 `parse.py` to port from; v1 skills parsed
the `## Definition of done` / `## Open questions` / `## Phases` grammars inline in each
`SKILL.md`'s prose. This suite validates the Python re-implementation directly against the
`_shared` contract files it is authored from (`skills/_shared/dod-annotations.md`,
`skills/_shared/open-question-links.md`,
`skills/planner/references/plan-schema.md`), using fixtures lifted from those
contracts' own worked examples (`docs/specs/examples/dod-annotations.md`,
`docs/specs/examples/open-question-links.md`) plus adversarial mutations authored for this suite.

`parse.py` is file I/O only (no `gh`, no network, no shim needed — like `config_block.py`'s S21
port), so every test here drives either the module's own functions in-process or the real
`python3 scripts/parse.py ...` subprocess against real fixture files on disk.

Two complementary strategies, matching `tests/test_config_block.py`'s own two-tier pattern:

  - **In-process unit tests** (`*ModuleTests`) call `parse`'s own `parse_dod_bullets` /
    `parse_oq_links` / `parse_phases` / `render_dod_bullets` functions directly — fast, and pins
    the exact field-extraction behavior against fixtures and hand-constructed edge cases.
  - **Subprocess/CLI tests** (`*CliTests`) invoke `python3 scripts/parse.py ...` as a real
    subprocess against the fixture files — the end-to-end path a caller (a prep script, a
    playbook) actually uses, proving the exit-code contract and the envelope shape via
    `tests/support/envelope_asserts`.

Fixtures live under `tests/fixtures/parse_dod/`, `tests/fixtures/parse_oq_links/`,
`tests/fixtures/parse_phases/` — one file per annotation form / disposition / phase-kind
combination (happy path) plus one file per adversarial mutation (malformed path), each fixture's
own docstring-equivalent comment block explaining which shared-contract example it lifts from or
which invariant its mutation targets.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
PARSE_PY = SCRIPTS_DIR / "parse.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DOD_FIXTURES_DIR = FIXTURES_DIR / "parse_dod"
OQ_FIXTURES_DIR = FIXTURES_DIR / "parse_oq_links"
PHASES_FIXTURES_DIR = FIXTURES_DIR / "parse_phases"

sys.path.insert(0, str(SCRIPTS_DIR))

import parse  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR  # noqa: E402
from pipelib.limits import BODY_CHAR_LIMIT, body_size  # noqa: E402
from tests.support import envelope_asserts  # noqa: E402


def _run_cli(args):
    """Invoke ``parse.py`` as a real subprocess. Returns ``(returncode, stdout_text,
    stderr_text)``. UTF-8 decoding is explicit, matching architecture.md §1's encoding pin on the
    test side too (`tests/test_config_block.py`'s own `_run_cli` convention).
    """
    completed = subprocess.run(
        [sys.executable, str(PARSE_PY)] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return (
        completed.returncode,
        completed.stdout.decode("utf-8"),
        completed.stderr.decode("utf-8"),
    )


def _parse_one_envelope(stdout_text):
    """Parse exactly one JSON object from ``stdout_text`` — architecture.md §3's "exactly one
    JSON envelope on stdout" contract (identical helper to `tests/test_config_block.py`'s own).
    """
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(
            "expected exactly one non-blank stdout line (the envelope), got %d: %r"
            % (len(lines), lines)
        )
    return json.loads(lines[0])


def _fixture_text(directory, name):
    with open(directory / name, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _extract_original_top_level_dod_lines(body_text):
    """Pull just the original top-level DoD checkbox-bullet lines out of a fixture body's ``##
    Definition of done`` section — the exact substring `render_dod_bullets` is meant to reproduce
    byte-for-byte, independent of whatever prose/heading/sub-bullet lines surround them in the
    fixture (those are not part of what `parse_dod_bullets` captures, so are correctly excluded
    from the round-trip comparison rather than causing a spurious mismatch).
    """
    lines = body_text.splitlines()
    section = parse._find_section(lines, r"Definition of done")
    assert section is not None, "fixture must have a '## Definition of done' section"
    start, end = section
    out = []
    for i in range(start, end):
        match = parse._DOD_BULLET_RE.match(lines[i])
        if match and parse._TOP_LEVEL_INDENT_RE.match(match.group(1)):
            out.append(lines[i])
    return "\n".join(out) + ("\n" if out else "")


# ============================================================================================
# `dod` — in-process module tests
# ============================================================================================


class ParseDodModuleTests(unittest.TestCase):
    """One test per closed-set annotation form (dod-annotations.md's "Annotation shapes (closed
    set)" table, lifted verbatim into `docs/specs/examples/dod-annotations.md`), each asserting
    the parsed fields AND the byte-identical round-trip through `render_dod_bullets` — this dual
    assertion per fixture is what proves S5's first DoD box ("`dod` parses every annotation form
    in the shared contract (one fixture per form); parse -> re-render round-trips
    byte-identically").
    """

    def _assert_round_trips(self, fixture_name):
        text = _fixture_text(DOD_FIXTURES_DIR, fixture_name)
        bullets = parse.parse_dod_bullets(text)
        rendered = parse.render_dod_bullets(bullets)
        original = _extract_original_top_level_dod_lines(text)
        self.assertEqual(
            rendered,
            original,
            "round-trip mismatch for %s:\n  original: %r\n  rendered: %r"
            % (fixture_name, original, rendered),
        )
        return bullets

    def test_bare_no_annotation_form(self):
        bullets = self._assert_round_trips("bare_no_annotation.md")
        self.assertEqual(len(bullets), 2)
        self.assertEqual(bullets[0]["index"], 1)
        self.assertFalse(bullets[0]["checked"])
        self.assertIsNone(bullets[0]["annotation"])
        self.assertEqual(bullets[0]["text"], "Clicking the save button persists the draft")

    def test_closed_by_phase_commit_form(self):
        bullets = self._assert_round_trips("closed_by_phase_commit.md")
        annotated = bullets[0]
        self.assertTrue(annotated["checked"])
        self.assertEqual(
            annotated["annotation"],
            {"form": "closed-by-phase-commit", "phase": 1, "sha": "abc1234"},
        )

    def test_closed_by_phase_operator_form(self):
        bullets = self._assert_round_trips("closed_by_phase_operator.md")
        annotated = bullets[0]
        self.assertTrue(annotated["checked"])
        self.assertEqual(
            annotated["annotation"],
            {"form": "closed-by-phase-operator", "phase": 2, "date": "2026-06-30"},
        )

    def test_closed_by_commit_single_phase_fallback_form(self):
        bullets = self._assert_round_trips("closed_by_commit_single_phase.md")
        annotated = bullets[0]
        self.assertTrue(annotated["checked"])
        self.assertEqual(annotated["annotation"], {"form": "closed-by-commit", "sha": "9f8e7d6"})

    def test_resolver_claimed_evaluator_rejected_form(self):
        bullets = self._assert_round_trips("resolver_claimed_evaluator_rejected.md")
        rejected = bullets[0]
        self.assertFalse(rejected["checked"])  # sticky veto: un-ticked
        self.assertEqual(
            rejected["annotation"],
            {
                "form": "resolver-claimed-evaluator-rejected",
                "phase": 3,
                "sha": "1a2b3c4",
                "reason": "streaming path still buffers the whole file in memory",
            },
        )

    def test_previously_claimed_on_closed_pr_form(self):
        bullets = self._assert_round_trips("previously_claimed_on_closed_pr.md")
        predecessor = bullets[0]
        self.assertFalse(predecessor["checked"])  # un-ticked predecessor annotation
        self.assertEqual(
            predecessor["annotation"],
            {
                "form": "previously-claimed-on-closed-pr",
                "phase": 2,
                "sha": "4d5e6f7",
                "pr": 612,
            },
        )

    def test_mixed_realistic_body_excludes_sub_bullet_and_indexes_top_level_only(self):
        bullets = self._assert_round_trips("mixed_realistic.md")
        # 3 top-level DoD bullets; the indented sub-bullet detail line and the unrelated
        # '## Out of scope' section's own bullet must not be counted (dod-annotations.md's
        # "Top-level bullets only" + "Section finder" rules).
        self.assertEqual([b["index"] for b in bullets], [1, 2, 3])
        self.assertEqual(bullets[1]["text"], "Search results are keyboard-navigable")

    def test_no_definition_of_done_section_returns_empty_list_silently(self):
        text = "## Summary\nJust a summary, no DoD section at all.\n"
        self.assertEqual(parse.parse_dod_bullets(text), [])

    def test_free_text_trailing_parenthetical_is_not_mistaken_for_an_annotation(self):
        # Negative control for the `_ANNOTATION_LOOKALIKE_RE` heuristic: an ordinary bullet whose
        # free text happens to end in an unrelated parenthetical (a scope/stretch-goal note) must
        # parse as a bare, unannotated bullet — never DOD_MALFORMED. False positives here would
        # block ordinary authoring on every rewrite pass.
        text = "## Definition of done\n- [ ] Support CSV export (stretch goal)\n"
        bullets = parse.parse_dod_bullets(text)
        self.assertEqual(len(bullets), 1)
        self.assertIsNone(bullets[0]["annotation"])
        self.assertEqual(bullets[0]["text"], "Support CSV export (stretch goal)")


class ParseDodMalformedModuleTests(unittest.TestCase):
    """S5 DoD box 2: 'Unknown or stacked annotations return DOD_MALFORMED (never a crash or a
    guess).' One test per distinct way that can happen, each an adversarial mutation authored for
    this suite (not present verbatim in the shared contract, which only documents the well-formed
    shapes).
    """

    def test_unknown_annotation_prefix_raises(self):
        text = _fixture_text(DOD_FIXTURES_DIR, "malformed_unknown_prefix.md")
        with self.assertRaises(parse._DodMalformed) as ctx:
            parse.parse_dod_bullets(text)
        self.assertIn("fixed by", ctx.exception.reason)

    def test_stacked_annotations_both_closed_by_raises(self):
        text = _fixture_text(DOD_FIXTURES_DIR, "malformed_stacked_annotations.md")
        with self.assertRaises(parse._DodMalformed):
            parse.parse_dod_bullets(text)

    def test_recognized_prefix_with_unparseable_tail_raises(self):
        text = _fixture_text(DOD_FIXTURES_DIR, "malformed_bad_tail_shape.md")
        with self.assertRaises(parse._DodMalformed):
            parse.parse_dod_bullets(text)

    def test_stacked_annotation_smuggled_inside_rejection_reason_raises(self):
        # The evaluator-rejection form's `reason` field is free text, so a stacked second
        # annotation folded into it (via the outer regex's greedy tail capture) could otherwise
        # silently pass through as "reason text" rather than being caught — this is the one
        # sub-shape needing its own dedicated stacking guard (`_REASON_CONTAINS_PAREN_RE`).
        text = _fixture_text(DOD_FIXTURES_DIR, "malformed_stacked_rejection_then_closed.md")
        with self.assertRaises(parse._DodMalformed):
            parse.parse_dod_bullets(text)

    def test_never_crashes_with_an_unhandled_exception_on_malformed_input(self):
        # "Never a crash" — assert the only exception type that can escape is the module's own
        # typed `_DodMalformed`, not e.g. an IndexError/AttributeError from a regex assumption
        # that didn't hold.
        for fixture_name in (
            "malformed_unknown_prefix.md",
            "malformed_stacked_annotations.md",
            "malformed_bad_tail_shape.md",
            "malformed_stacked_rejection_then_closed.md",
        ):
            text = _fixture_text(DOD_FIXTURES_DIR, fixture_name)
            try:
                parse.parse_dod_bullets(text)
                self.fail("%s: expected _DodMalformed, got a clean parse" % fixture_name)
            except parse._DodMalformed:
                pass  # exactly the expected typed signal


# ============================================================================================
# `dod` — CLI tests (envelope conformance, exit codes, --render)
# ============================================================================================


class ParseDodCliTests(unittest.TestCase):
    def test_dod_happy_path_envelope_conformance(self):
        rc, out, err = _run_cli(["dod", str(DOD_FIXTURES_DIR / "closed_by_phase_commit.md")])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        envelope_asserts.assert_exit_code_contract(rc, envelope_present=True)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(len(envelope["dod"]), 2)
        self.assertEqual(envelope["dod"][0]["annotation"]["form"], "closed-by-phase-commit")

    def test_dod_render_mode_byte_identical_round_trip_via_cli(self):
        fixture_path = DOD_FIXTURES_DIR / "mixed_realistic.md"
        rc, out, err = _run_cli(["dod", str(fixture_path), "--render"])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        original = _extract_original_top_level_dod_lines(
            _fixture_text(DOD_FIXTURES_DIR, "mixed_realistic.md")
        )
        self.assertEqual(envelope["rendered"], original)

    def test_dod_render_on_malformed_body_still_returns_dod_malformed_not_a_guess(self):
        # "--render still parses first: a malformed body still returns DOD_MALFORMED under
        # --render, it does not silently render a best-effort guess" (module docstring).
        rc, out, err = _run_cli(
            ["dod", str(DOD_FIXTURES_DIR / "malformed_unknown_prefix.md"), "--render"]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "DOD_MALFORMED")
        self.assertNotIn("rendered", envelope)

    def test_dod_malformed_emits_dod_malformed_decision_with_evidence(self):
        rc, out, err = _run_cli(["dod", str(DOD_FIXTURES_DIR / "malformed_bad_tail_shape.md")])
        self.assertEqual(rc, EXIT_OK)  # needs_decision is exit 0 (architecture.md §3)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        envelope_asserts.assert_decision_payload_shape(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "DOD_MALFORMED")
        self.assertIn("body_file", envelope["decision"]["context"])
        self.assertIn("line_number", envelope["decision"]["context"])
        self.assertIn("raw_line", envelope["decision"]["context"])

    def test_dod_no_section_ok_empty_list_via_cli(self):
        # `no_section_single_phase.md` (a `parse_phases` fixture) carries no `## Definition of
        # done` heading at all — genuinely exercises the "section absent" path, distinct from the
        # `parse_oq_links/no_section.md` fixture, which is about the `## Open questions` section
        # and in fact carries its own `## Definition of done` section (used by the `oq-links`
        # tests, not this one).
        rc, out, err = _run_cli(
            ["dod", str(PHASES_FIXTURES_DIR / "no_section_single_phase.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["dod"], [])

    def test_dod_nonexistent_file_exits_usage_error_no_envelope(self):
        rc, out, err = _run_cli(["dod", "/nonexistent/path/does-not-exist.md"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")
        self.assertIn("PARSE_FILE_UNREADABLE", err)
        envelope_asserts.assert_exit_code_contract(rc, envelope_present=False)


# ============================================================================================
# `oq-links` — in-process module tests
# ============================================================================================


class ParseOqLinksModuleTests(unittest.TestCase):
    """S5 DoD box 3: 'oq-links parses the shared contract's examples including all three
    dispositions; a body without the section returns ok with an empty list.'
    """

    def test_all_three_dispositions_parse_from_the_shared_worked_example(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "all_three_dispositions.md")
        entries = parse.parse_oq_links(text)
        self.assertEqual(len(entries), 3)
        dispositions = [e["disposition"] for e in entries]
        self.assertEqual(dispositions, ["scoped-out", "in-scope (blocked)", "provisional-default"])

    def test_scoped_out_entry_fields(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "all_three_dispositions.md")
        entry = parse.parse_oq_links(text)[0]
        self.assertEqual(entry["oq_id"], "PRD-OQ-05")
        self.assertEqual(entry["source"], "docs/prd.md §12 Open questions")
        self.assertEqual(entry["gates"], "which payment methods ship at launch")
        self.assertEqual(entry["question"], "#211")
        self.assertEqual(entry["audience"], "audience:business, audience:developer")
        self.assertIsNone(entry["default"])
        self.assertIsNone(entry["retires_when"])

    def test_in_scope_blocked_entry_fields(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "all_three_dispositions.md")
        entry = parse.parse_oq_links(text)[1]
        self.assertEqual(entry["oq_id"], "OQ-08")
        self.assertEqual(entry["disposition"], "in-scope (blocked)")
        self.assertEqual(entry["question"], "#212")
        self.assertEqual(entry["audience"], "audience:clinical")

    def test_provisional_default_entry_carries_default_and_retires_when(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "all_three_dispositions.md")
        entry = parse.parse_oq_links(text)[2]
        self.assertEqual(entry["oq_id"], "DESIGN-Q-3")
        self.assertEqual(entry["disposition"], "provisional-default")
        self.assertEqual(entry["default"], "newest-first")
        self.assertEqual(entry["retires_when"], "#213 answered")

    def test_not_filed_question_value_parses(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "not_filed_question.md")
        entries = parse.parse_oq_links(text)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["question"], "(not filed)")
        self.assertEqual(entries[0]["audience"], "(unknown)")

    def test_no_open_questions_section_returns_empty_list(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "no_section.md")
        self.assertEqual(parse.parse_oq_links(text), [])

    def test_section_present_without_marker_returns_empty_list_not_malformed(self):
        # open-question-links.md: readers locate the registry via a `startswith` match on the
        # marker line; a section lacking it is not this registry at all — degrade to empty, never
        # raise (a heading name collision with an unrelated "Open questions" prose section must
        # not become an operator-blocking decision).
        text = _fixture_text(OQ_FIXTURES_DIR, "malformed_missing_marker.md")
        self.assertEqual(parse.parse_oq_links(text), [])

    def test_bare_body_with_no_sections_at_all_returns_empty_list(self):
        self.assertEqual(parse.parse_oq_links("no headings here at all\n"), [])


class ParseOqLinksMalformedModuleTests(unittest.TestCase):
    def test_unknown_disposition_raises(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "malformed_unknown_disposition.md")
        with self.assertRaises(parse._OqLinksMalformed) as ctx:
            parse.parse_oq_links(text)
        self.assertIn("disposition", ctx.exception.reason)

    def test_provisional_default_missing_required_fields_raises(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "malformed_provisional_missing_default.md")
        with self.assertRaises(parse._OqLinksMalformed) as ctx:
            parse.parse_oq_links(text)
        self.assertIn("provisional-default", ctx.exception.reason)

    def test_missing_required_question_field_raises(self):
        text = _fixture_text(OQ_FIXTURES_DIR, "malformed_missing_question_field.md")
        with self.assertRaises(parse._OqLinksMalformed):
            parse.parse_oq_links(text)

    def test_default_field_on_non_provisional_disposition_raises(self):
        # open-question-links.md: "default:"/"retires-when:" are "[provisional-default only]" —
        # present on a scoped-out/in-scope-blocked entry is itself a schema violation, not a
        # harmless extra field to silently accept.
        text = (
            "## Open questions\n"
            "<!-- open-question-links:v1 -->\n"
            "- OQ: `OQ-99` (docs/prd.md §1) — gates: something\n"
            "  — disposition: scoped-out — question: #400 — audience: audience:business\n"
            "  — default: something — retires-when: #400 answered\n"
        )
        with self.assertRaises(parse._OqLinksMalformed) as ctx:
            parse.parse_oq_links(text)
        self.assertIn("scoped-out", ctx.exception.reason)


# ============================================================================================
# `oq-links` — CLI tests
# ============================================================================================


class ParseOqLinksCliTests(unittest.TestCase):
    def test_oq_links_happy_path_envelope_conformance(self):
        rc, out, err = _run_cli(
            ["oq-links", str(OQ_FIXTURES_DIR / "all_three_dispositions.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(len(envelope["open_questions"]), 3)

    def test_oq_links_no_section_ok_empty_list_via_cli(self):
        rc, out, err = _run_cli(["oq-links", str(OQ_FIXTURES_DIR / "no_section.md")])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["open_questions"], [])

    def test_oq_links_malformed_emits_needs_decision(self):
        rc, out, err = _run_cli(
            ["oq-links", str(OQ_FIXTURES_DIR / "malformed_unknown_disposition.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        envelope_asserts.assert_decision_payload_shape(envelope)
        self.assertEqual(envelope["status"], "needs_decision")


# ============================================================================================
# `phases` — in-process module tests
# ============================================================================================


class ParsePhasesModuleTests(unittest.TestCase):
    """S5 DoD box 4: 'phases parses well-formed lists and returns PHASES_MALFORMED on the
    malformed fixtures.'
    """

    def test_well_formed_four_phase_plan_all_three_kinds(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_multi_phase.md")
        phases = parse.parse_phases(text)
        self.assertEqual([p["number"] for p in phases], [1, 2, 3, 4])
        self.assertEqual(
            [p["kind"] for p in phases],
            ["code-shipping", "code-shipping", "operator", "decision-only"],
        )

    def test_closes_dod_none_parses_as_literal_string(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_multi_phase.md")
        phases = parse.parse_phases(text)
        self.assertEqual(phases[0]["closes_dod"], "(none)")
        self.assertEqual(phases[0]["depends_on"], "(none)")

    def test_closes_dod_multi_value_list_parses_as_ints(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_multi_phase.md")
        phases = parse.parse_phases(text)
        self.assertEqual(phases[1]["closes_dod"], [1, 2])
        self.assertEqual(phases[1]["depends_on"], [1])

    def test_titles_and_ships_and_deliverable_fields(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_multi_phase.md")
        phases = parse.parse_phases(text)
        self.assertEqual(phases[0]["title"], "substrate")
        self.assertEqual(phases[0]["ships"], "PR commits to the issue branch")
        self.assertEqual(
            phases[2]["deliverable"],
            "the operator's recorded go/no-go on the harness's first measurement run",
        )

    def test_no_phases_section_returns_empty_list_not_malformed(self):
        # plan-schema.md: single-phase issues and epics correctly omit '## Phases' entirely — an
        # absent section is a legitimate shape, never PHASES_MALFORMED on its own.
        text = _fixture_text(PHASES_FIXTURES_DIR, "no_section_single_phase.md")
        self.assertEqual(parse.parse_phases(text), [])

    def test_bare_body_with_no_sections_at_all_returns_empty_list(self):
        self.assertEqual(parse.parse_phases("no headings here at all\n"), [])


class ParsePhasesSubIssueModuleTests(unittest.TestCase):
    """#18: the `sub-issue:` key records which deliverable slice a phase serves. Recognized but
    **not required**, so a plan authored before the key existed still parses.
    """

    def test_sub_issue_tri_state_across_the_three_shapes(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_sub_issue_mapped.md")
        phases = parse.parse_phases(text)
        self.assertEqual(phases[0]["sub_issue"], "(none)")  # explicit substrate claim
        self.assertEqual(phases[1]["sub_issue"], 214)
        self.assertEqual(phases[3]["sub_issue"], 216)

    def test_two_phases_may_serve_the_same_sub_issue(self):
        # N:1 is the locked cardinality: several phases may serve one sub-issue. The parser must
        # not import `closes-dod`'s exactly-once rule — it is the reviewer that judges coverage.
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_sub_issue_mapped.md")
        phases = parse.parse_phases(text)
        self.assertEqual([phases[1]["sub_issue"], phases[2]["sub_issue"]], [214, 214])

    def test_pre_contract_five_key_plan_parses_with_sub_issue_none(self):
        # The whole backward-compatibility mechanism: a plan authored before the key existed has no
        # `sub-issue:` line, and `None` (unmapped) is distinguishable from `"(none)"` (substrate).
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_multi_phase.md")
        phases = parse.parse_phases(text)
        self.assertEqual([p["sub_issue"] for p in phases], [None, None, None, None])

    def test_sub_issue_key_is_always_present_in_the_payload(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_multi_phase.md")
        for phase in parse.parse_phases(text):
            self.assertIn("sub_issue", phase)

    def test_bare_int_raises(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_sub_issue_bare_int.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("sub-issue", ctx.exception.reason)

    def test_comma_separated_list_raises_rather_than_narrowing(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_sub_issue_multi_value.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("N:1", ctx.exception.reason)

    def test_issue_zero_raises(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_sub_issue_zero.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("start at 1", ctx.exception.reason)

    def test_empty_value_raises(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_sub_issue_empty.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("sub-issue", ctx.exception.reason)

    def test_duplicate_sub_issue_key_in_one_phase_raises(self):
        text = (
            "## Phases\n"
            "1. **Phase 1 — a**\n"
            "   - kind: code-shipping\n"
            "   - ships: PR commits to the issue branch\n"
            "   - closes-dod: (none)\n"
            "   - deliverable: x\n"
            "   - depends-on: (none)\n"
            "   - sub-issue: #214\n"
            "   - sub-issue: #216\n"
        )
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("more than once", ctx.exception.reason)


class ParsePhasesCheckpointModuleTests(unittest.TestCase):
    """The `checkpoint:` key records whether the resolver may continue to the next phase in the same
    session once this one ships. Recognized but **not required**: an absent line is `None`, which
    the resolver reads as `pause` — the behaviour every plan authored before the key had.
    """

    def test_three_states_across_the_fixture(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_checkpoint.md")
        phases = parse.parse_phases(text)
        self.assertEqual(
            [p["checkpoint"] for p in phases], ["continue", "pause", None, None]
        )

    def test_pre_contract_plan_parses_with_checkpoint_none(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "well_formed_multi_phase.md")
        phases = parse.parse_phases(text)
        self.assertEqual([p["checkpoint"] for p in phases], [None, None, None, None])

    def test_checkpoint_key_is_always_present_in_the_payload(self):
        for fixture in ("well_formed_multi_phase.md", "well_formed_sub_issue_mapped.md"):
            for phase in parse.parse_phases(_fixture_text(PHASES_FIXTURES_DIR, fixture)):
                self.assertIn("checkpoint", phase)

    def test_bad_value_raises_rather_than_defaulting(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_checkpoint_bad_value.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("checkpoint", ctx.exception.reason)
        self.assertIn("'pause' or 'continue'", ctx.exception.reason)

    def test_empty_value_raises(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_checkpoint_empty.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("checkpoint", ctx.exception.reason)

    def test_values_are_case_sensitive(self):
        text = (
            "## Phases\n"
            "1. **Phase 1 — a**\n"
            "   - kind: code-shipping\n"
            "   - ships: PR commits to the issue branch\n"
            "   - closes-dod: (none)\n"
            "   - deliverable: x\n"
            "   - depends-on: (none)\n"
            "   - checkpoint: Continue\n"
        )
        with self.assertRaises(parse._PhasesMalformed):
            parse.parse_phases(text)

    def test_duplicate_checkpoint_key_in_one_phase_raises(self):
        text = (
            "## Phases\n"
            "1. **Phase 1 — a**\n"
            "   - kind: code-shipping\n"
            "   - ships: PR commits to the issue branch\n"
            "   - closes-dod: (none)\n"
            "   - deliverable: x\n"
            "   - depends-on: (none)\n"
            "   - checkpoint: continue\n"
            "   - checkpoint: pause\n"
        )
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("more than once", ctx.exception.reason)

    def test_checkpoint_and_sub_issue_coexist_in_either_order(self):
        text = (
            "## Phases\n"
            "1. **Phase 1 — a**\n"
            "   - kind: code-shipping\n"
            "   - ships: PR commits to the issue branch\n"
            "   - closes-dod: (none)\n"
            "   - deliverable: x\n"
            "   - depends-on: (none)\n"
            "   - checkpoint: continue\n"
            "   - sub-issue: #214\n"
        )
        phase = parse.parse_phases(text)[0]
        self.assertEqual((phase["checkpoint"], phase["sub_issue"]), ("continue", 214))


class ParsePhasesMalformedModuleTests(unittest.TestCase):
    def test_missing_required_key_raises(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_missing_key.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("deliverable", ctx.exception.reason)

    def test_kind_outside_closed_set_raises(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_bad_kind.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("kind", ctx.exception.reason)

    def test_nonsequential_phase_numbers_raise(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_nonsequential_numbers.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("sequential", ctx.exception.reason)

    def test_ordinal_label_mismatch_raises(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_ordinal_label_mismatch.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("label", ctx.exception.reason)

    def test_bad_closes_dod_value_raises(self):
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_bad_closes_dod_value.md")
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("closes-dod", ctx.exception.reason)

    def test_free_form_sequencing_prose_raises(self):
        # The named regression (architecture.md/resolver.md, issue #640): free-form '## Sequencing'
        # prose instead of the fixed structured-key grammar gave the resolver no way to recognize
        # more phases were due. A '## Phases' heading over unstructured prose must raise, not
        # silently return an empty/partial phase list.
        text = _fixture_text(PHASES_FIXTURES_DIR, "malformed_free_form_sequencing.md")
        with self.assertRaises(parse._PhasesMalformed):
            parse.parse_phases(text)

    def test_duplicate_phase_number_raises(self):
        text = (
            "## Phases\n"
            "1. **Phase 1 — a**\n"
            "   - kind: code-shipping\n"
            "   - ships: PR commits to the issue branch\n"
            "   - closes-dod: (none)\n"
            "   - deliverable: x\n"
            "   - depends-on: (none)\n"
            "1. **Phase 1 — b**\n"
            "   - kind: code-shipping\n"
            "   - ships: PR commits to the issue branch\n"
            "   - closes-dod: (none)\n"
            "   - deliverable: y\n"
            "   - depends-on: 1\n"
        )
        with self.assertRaises(parse._PhasesMalformed) as ctx:
            parse.parse_phases(text)
        self.assertIn("more than once", ctx.exception.reason)

    def test_never_crashes_with_an_unhandled_exception_on_malformed_input(self):
        for fixture_name in (
            "malformed_missing_key.md",
            "malformed_bad_kind.md",
            "malformed_nonsequential_numbers.md",
            "malformed_ordinal_label_mismatch.md",
            "malformed_bad_closes_dod_value.md",
            "malformed_free_form_sequencing.md",
            "malformed_sub_issue_bare_int.md",
            "malformed_sub_issue_multi_value.md",
            "malformed_sub_issue_zero.md",
            "malformed_sub_issue_empty.md",
            "malformed_checkpoint_bad_value.md",
            "malformed_checkpoint_empty.md",
        ):
            text = _fixture_text(PHASES_FIXTURES_DIR, fixture_name)
            try:
                parse.parse_phases(text)
                self.fail("%s: expected _PhasesMalformed, got a clean parse" % fixture_name)
            except parse._PhasesMalformed:
                pass


# ============================================================================================
# `phases` — CLI tests
# ============================================================================================


class PhaseTrackerParseTests(unittest.TestCase):
    """#46: `parse_phase_tracker` is import-only — no subcommand, no decision code.

    The tracker is a rendering the resolver rebuilds every push (the plan's `## Phases` owns which
    rows exist, the tracker owns which are ticked), so an unparseable row is a row to rewrite, never
    a run to fail. That asymmetry against `parse_phases` is deliberate; these tests pin it so a later
    editor does not "unify" the two.
    """

    def test_ticked_row_yields_phase_title_and_commit(self):
        rows = parse.parse_phase_tracker(
            "## Phase tracker\n- [x] Phase 1 — the writer (commit abc1234)\n"
        )
        self.assertEqual(
            rows,
            [
                {
                    "checked": True,
                    "phase": 1,
                    "title": "the writer",
                    "sub_label": None,
                    "commit_sha": "abc1234",
                    "annotation": None,
                }
            ],
        )

    def test_unticked_row_has_no_commit(self):
        rows = parse.parse_phase_tracker("## Phase tracker\n- [ ] Phase 2 — the flag\n")
        self.assertEqual(rows[0]["checked"], False)
        self.assertIsNone(rows[0]["commit_sha"])

    def test_operator_row_keeps_its_date_out_of_the_title(self):
        # The date must NOT land in `title`: the tracker diff compares titles, so an operator row
        # would read as drift on every run and the "adopt the plan's title" rebuild would erase the
        # ISO date — the only record that the operator phase landed.
        rows = parse.parse_phase_tracker(
            "## Phase tracker\n- [x] Phase 3 — the measurement (operator action 2026-06-04)\n"
        )
        self.assertEqual(rows[0]["checked"], True)
        self.assertIsNone(rows[0]["commit_sha"])
        self.assertEqual(rows[0]["title"], "the measurement")
        self.assertEqual(rows[0]["annotation"], "operator action 2026-06-04")

    def test_a_title_that_legitimately_ends_in_parens_is_preserved(self):
        # The annotation alternation is restricted to the closed set for this reason: a greedy
        # "any parenthetical" rule would silently eat part of a real title.
        rows = parse.parse_phase_tracker(
            "## Phase tracker\n- [ ] Phase 2 — the flag (behind a feature gate)\n"
        )
        self.assertEqual(rows[0]["title"], "the flag (behind a feature gate)")
        self.assertIsNone(rows[0]["annotation"])

    def test_section_presence_is_independent_of_whether_rows_parsed(self):
        # A section whose rows are all malformed has zero rows AND an unknown tick state. A caller
        # inferring presence from `rows` would skip reconciling exactly the tracker that needs it.
        scan = parse.scan_phase_tracker("## Phase tracker\n- [x] Phase 5c — nope (commit abc1234)\n")
        self.assertTrue(scan["present"])
        self.assertEqual(scan["rows"], [])
        self.assertEqual(scan["unparsed"], ["- [x] Phase 5c — nope (commit abc1234)"])

    def test_prose_inside_the_section_is_not_evidence_of_an_unreadable_row(self):
        scan = parse.scan_phase_tracker(
            "## Phase tracker\nPhases ship one per session.\n- [x] Phase 1 — a (commit abc1234)\n"
        )
        self.assertEqual(scan["unparsed"], [])
        self.assertEqual([row["phase"] for row in scan["rows"]], [1])

    def test_a_sub_row_is_bound_to_its_phase_not_a_second_row_of_it(self):
        # #51: `dod-projection-rule.md` Example C's own row. `Phase 2-measurement` means the
        # measurement sub-phase attached to phase 2 — reading it as "phase 2, titled measurement" made
        # one plan phase look like two competing tracker rows.
        rows = parse.parse_phase_tracker(
            "## Phase tracker\n- [x] Phase 2-measurement (operator phase 2, applied 2026-06-04)\n"
        )
        self.assertEqual(rows[0]["phase"], 2)
        self.assertEqual(rows[0]["sub_label"], "measurement")
        self.assertEqual(rows[0]["annotation"], "operator phase 2, applied 2026-06-04")

    def test_a_main_row_is_not_mistaken_for_a_sub_row(self):
        # The separator is the discriminator: an em dash or a spaced hyphen introduces a title.
        for line, title in (
            ("- [x] Phase 2 — harness (commit abc1234)", "harness"),
            ("- [x] Phase 2 - harness (commit abc1234)", "harness"),
        ):
            rows = parse.parse_phase_tracker("## Phase tracker\n%s\n" % line)
            self.assertIsNone(rows[0]["sub_label"], line)
            self.assertEqual(rows[0]["title"], title, line)

    def test_the_frozen_worked_instance_parses_as_documented(self):
        # docs/specs/examples/phase-tracker.md verbatim — three main rows plus one sub-row.
        rows = parse.parse_phase_tracker(
            "## Phase tracker\n"
            "- [x] Phase 1 — substrate (commit abc1234)\n"
            "- [ ] Phase 2 — harness\n"
            "- [ ] Phase 2-measurement (operator)\n"
            "- [ ] Phase 3 — decision write-up\n"
        )
        self.assertEqual([row["phase"] for row in rows], [1, 2, 2, 3])
        self.assertEqual([row["sub_label"] for row in rows], [None, None, "measurement", None])

    def test_absent_section_is_not_present(self):
        self.assertEqual(
            parse.scan_phase_tracker("no tracker"), {"present": False, "rows": [], "unparsed": []}
        )

    def test_absent_section_returns_empty(self):
        self.assertEqual(parse.parse_phase_tracker("no tracker here"), [])
        self.assertEqual(parse.parse_phase_tracker(""), [])
        self.assertEqual(parse.parse_phase_tracker(None), [])

    def test_section_ends_at_the_next_heading(self):
        rows = parse.parse_phase_tracker(
            "## Phase tracker\n- [x] Phase 1 — a (commit abc1234)\n\n## Follow-ups\n"
            "- [x] Phase 9 — not a tracker row\n"
        )
        self.assertEqual([r["phase"] for r in rows], [1])

    def test_letter_suffixed_row_does_not_parse(self):
        # `\d+` only, matching `_PHASE_HEAD_RE`. A `Phase 5c` row is simply not a row — which is why
        # the tracker cannot represent the shape #46 forbids either.
        scan = parse.scan_phase_tracker("## Phase tracker\n- [x] Phase 5c — nope\n")
        self.assertEqual(scan["rows"], [])
        self.assertEqual(len(scan["unparsed"]), 1)

    def test_it_never_raises_on_garbage(self):
        for body in ("## Phase tracker\nfree-form prose, no rows at all\n", "## Phase tracker\n- [x]\n"):
            self.assertEqual(parse.parse_phase_tracker(body), [])

    def test_normalize_title_folds_whitespace_and_case(self):
        self.assertEqual(
            parse.normalize_tracker_title("End-to-end   Proof"),
            parse.normalize_tracker_title("end-to-end proof"),
        )

    def test_prep_planner_uses_the_shared_parser_not_a_copy(self):
        # Pins the consolidation: two preps consume this, so a third copy must not drift into being.
        import prep_planner

        self.assertIs(prep_planner._parse_phase_tracker, parse.parse_phase_tracker)


class StoriesSectionParseTests(unittest.TestCase):
    """The legacy `## Stories` checklist — one copy since 4.33.1 (the planner and slicer each carried
    one), read for the story set and, from the story's side, for the legacy parent edge."""

    def test_filed_and_placeholder_entries(self):
        body = (
            "## Stories\n"
            "- [x] #151 — Story A\n"
            "- [ ] #152 — Story B\n"
            "- [ ] Story C placeholder (not filed)\n"
        )
        self.assertEqual(
            parse.parse_stories_section(body),
            [
                {"number": 151, "title": "Story A", "checked": True},
                {"number": 152, "title": "Story B", "checked": False},
                {"number": None, "title": "Story C placeholder (not filed)", "checked": False},
            ],
        )

    def test_absent_section(self):
        self.assertEqual(parse.parse_stories_section("## Goal\n- [ ] not stories\n"), [])
        self.assertEqual(parse.parse_stories_section(None), [])

    def test_stops_at_the_next_level_two_heading_only(self):
        body = (
            "## Stories\n- [ ] #151 — A\n### Wave 2\n- [ ] #152 — B\n\n"
            "## Definition of done\n- [ ] #999 — not a story\n"
        )
        self.assertEqual([e["number"] for e in parse.parse_stories_section(body)], [151, 152])

    def test_hand_maintained_bullet_shapes_still_read_as_filed(self):
        # `#NN` alone, or a separator (`—`, `--`, `-`) and a title: a ticked `- [x] #151` with no
        # title is still story #151, not a placeholder named "#151".
        body = "## Stories\n- [x] #151\n- [ ] #152 -- B\n- [ ] #153-C\n"
        self.assertEqual(
            parse.parse_stories_section(body),
            [
                {"number": 151, "title": "", "checked": True},
                {"number": 152, "title": "B", "checked": False},
                {"number": 153, "title": "C", "checked": False},
            ],
        )

    def test_hash_number_followed_by_prose_is_a_placeholder_not_a_story(self):
        # Reading `#3 lanes` as story #3 would send the planner and slicer to fetch an issue nobody
        # filed — a fatal `gh issue view` on a missing number.
        body = "## Stories\n- [ ] #3 lanes of work\n- [ ] #154abc\n"
        self.assertEqual(
            parse.parse_stories_section(body),
            [
                {"number": None, "title": "#3 lanes of work", "checked": False},
                {"number": None, "title": "#154abc", "checked": False},
            ],
        )

    def test_annotated_stories_heading_is_the_same_section(self):
        # The heading `dod_summary` already excepts: `## Stories` or `## Stories (…)`.
        for heading in ("## Stories", "## stories", "## Stories (wave 1)", "##Stories"):
            self.assertEqual(
                [e["number"] for e in parse.parse_stories_section(heading + "\n- [ ] #7 — x\n")],
                [7],
                heading,
            )
        self.assertEqual(parse.parse_stories_section("## User stories\n- [ ] #7 — x\n"), [])

    def test_stories_checklist_lists_is_membership_not_mention(self):
        listed = "## Stories\n- [ ] #12 — the story\n"
        self.assertTrue(parse.stories_checklist_lists(listed, 12))
        self.assertTrue(parse.stories_checklist_lists(listed, "12"))
        for body in (
            "Related: #12, unlike the old plan.",                        # prose
            "## Related\n- [ ] #12 — tracked elsewhere\n",               # another section
            "## Stories\nSee #12 for context.\n- [ ] #13 — other\n",    # prose inside Stories
            "## Stories\n- [ ] see #12 later\n",                        # placeholder mentioning it
            "## Stories\n- [ ] #120 — x\n- [ ] #1 — y\n",               # neighbouring numbers
            "",
        ):
            self.assertFalse(parse.stories_checklist_lists(body, 12), body)


class PolishLedgerParseTests(unittest.TestCase):
    """`scan_polish` — the `## Polish` ledger (skills/_shared/polish-ledger.md). A rendering the
    resolver and evaluator rebuild, so it follows the phase tracker's posture: never raises, and an
    unparseable line lands in `unparsed` to be rewritten, never a decision."""

    CONTRACT = REPO_ROOT / "skills" / "_shared" / "polish-ledger.md"

    def _contract_example(self):
        text = self.CONTRACT.read_text(encoding="utf-8")
        start = text.index("```\n## Polish\n") + len("```\n")
        return text[start:text.index("```", start)]

    def test_the_contract_worked_example_parses_whole(self):
        scan = parse.scan_polish(self._contract_example())
        self.assertTrue(scan["present"])
        self.assertEqual(scan["unparsed"], [])
        self.assertEqual(
            [(e["id"], e["disposition"]) for e in scan["entries"]],
            [("P1.1", "open"), ("P1.2", "file"), ("P2.1", "applied"), ("P2.2", "drop"), ("P2.3", "apply")],
        )
        applied = scan["entries"][2]
        self.assertEqual(applied["commit_sha"], "3f9c2ab")
        self.assertEqual(applied["anchor"], "lib/client.rb / retry_after")
        self.assertEqual(applied["note"], "evaluator: misleading on a public API")
        self.assertIsNone(scan["entries"][0]["note"])

    def test_every_contract_disposition_is_in_the_closed_set(self):
        self.assertEqual(parse.POLISH_DISPOSITIONS, {"open", "apply", "file", "drop", "applied"})
        table = self.CONTRACT.read_text(encoding="utf-8")
        for word in parse.POLISH_DISPOSITIONS:
            self.assertIn("| `%s" % word, table)

    def test_malformed_lines_are_unparsed_not_raised(self):
        body = (
            "## Polish\n"
            "- P1.1 · maybe — off-vocabulary disposition — a.py / f\n"
            "- P1.2 · open — missing its anchor field\n"
            "- P1.3 · open (commit abc1234) — a commit on a non-applied entry — a.py / f\n"
            "- P1.4 · open — fine — a.py / f\n"
            "prose inside the section is ignored\n"
        )
        scan = parse.scan_polish(body)
        self.assertEqual([e["id"] for e in scan["entries"]], ["P1.4"])
        self.assertEqual(len(scan["unparsed"]), 3)

    def test_absent_section_is_not_present(self):
        self.assertEqual(parse.scan_polish("no ledger"), parse.absent_polish())
        self.assertEqual(parse.scan_polish(None), {"present": False, "entries": [], "unparsed": []})

    def test_the_section_ends_at_the_next_heading(self):
        scan = parse.scan_polish("## Polish\n- P1.1 · open — a — b.py / c\n## Follow-ups\n- P1.2 · open — x — y / z\n")
        self.assertEqual([e["id"] for e in scan["entries"]], ["P1.1"])


class SecurityRecordParseTests(unittest.TestCase):
    """`scan_security` — the `## Security review` record (skills/_shared/security-review.md). Like the
    polish ledger it never raises; a malformed line lands in `unparsed`. Its `reviewed_sha` decides the
    evaluator's skip, so only a full 40-hex SHA counts."""

    CONTRACT = REPO_ROOT / "skills" / "_shared" / "security-review.md"
    HEAD = "0123456789abcdef0123456789abcdef01234567"

    def _contract_example(self):
        text = self.CONTRACT.read_text(encoding="utf-8")
        start = text.index("```\n## Security review\n") + len("```\n")
        return text[start:text.index("```", start)]

    def test_the_contract_worked_example_parses_whole(self):
        scan = parse.scan_security(self._contract_example())
        self.assertTrue(scan["present"])
        self.assertEqual(scan["unparsed"], [])
        self.assertIsNotNone(scan["reviewed_sha"])
        self.assertEqual(
            [(e["id"], e["disposition"]) for e in scan["entries"]],
            [("SEC-1", "fixed"), ("SEC-2", "dismissed"), ("SEC-3", "open")],
        )
        self.assertIsNotNone(scan["entries"][0]["commit_sha"])
        self.assertTrue(scan["entries"][1]["note"].startswith("operator:"))

    def test_each_disposition_parses(self):
        body = (
            "## Security review\n- Reviewed: %s\n"
            "- SEC-1 · open — a query built from input — src/q.py / build — still to fix\n"
            "- SEC-2 · fixed (commit abc1234) — a token in the log — lib/log.rb / Log#write\n"
            "- SEC-3 · dismissed — path joined from input — app/files.py / open_upload — operator: admin-only route\n"
            "- SEC-4 · open — a secret in source — config/keys.swift / Keys\n" % self.HEAD
        )
        scan = parse.scan_security(body)
        self.assertEqual(scan["reviewed_sha"], self.HEAD)
        self.assertEqual(scan["unparsed"], [])
        entries = scan["entries"]
        self.assertEqual([e["disposition"] for e in entries], ["open", "fixed", "dismissed", "open"])
        self.assertEqual(entries[1]["commit_sha"], "abc1234")
        self.assertIsNone(entries[0]["commit_sha"])
        self.assertEqual(entries[0]["note"], "still to fix")
        self.assertIsNone(entries[3]["note"])
        self.assertEqual(entries[2]["anchor"], "app/files.py / open_upload")
        self.assertEqual((entries[3]["id"], entries[3]["seq"]), ("SEC-4", 4))
        self.assertEqual(parse.SECURITY_DISPOSITIONS, ("open", "fixed", "dismissed"))

    def test_malformed_lines_are_unparsed_not_raised(self):
        body = (
            "## Security review\n"
            "- SEC-1 · open (commit abc1234) — a commit on open — a.py / f\n"
            "- SEC-2 · dismissed (commit abc1234) — a commit on dismissed — a.py / f — operator: x\n"
            "- SEC-3 · fixed — fixed with no commit — a.py / f\n"
            "- SEC-4 · ignored — off-vocabulary disposition — a.py / f\n"
            "- SEC-5 · open — missing its anchor field\n"
            "- SEC-6 · dismissed — no operator reason — a.py / f — looked fine\n"
            "- SEC-7 · open — fine — a.py / f\n"
            "prose inside the section is ignored\n"
        )
        scan = parse.scan_security(body)
        self.assertEqual([e["id"] for e in scan["entries"]], ["SEC-7"])
        self.assertEqual(len(scan["unparsed"]), 6)

    def test_only_a_full_sha_is_a_reviewed_head(self):
        for value in ("abc1234", self.HEAD.upper(), self.HEAD + "0", "", self.HEAD + " (phase 2)"):
            scan = parse.scan_security("## Security review\n- Reviewed: %s\n" % value)
            self.assertTrue(scan["present"])
            self.assertIsNone(scan["reviewed_sha"], value)
            # `unparsed` holds findings, and each one blocks approval; a bad SHA is not a finding.
            self.assertEqual(scan["unparsed"], [], value)

    def test_a_malformed_later_reviewed_line_resets_a_stale_valid_one(self):
        scan = parse.scan_security(
            "## Security review\n- Reviewed: %s\n- Reviewed: %s (phase 2)\n" % (self.HEAD, self.HEAD)
        )
        self.assertIsNone(scan["reviewed_sha"])

    def test_a_duplicated_id_never_shadows_an_open_entry(self):
        # Operator-raised (review round 3 re-examined): keyed by id, a later `dismissed` line hid an
        # `open` one with the same id. A repeat of an id lands in `unparsed`, which counts as open.
        for first, second in (("open", "dismissed"), ("dismissed", "open"), ("open", "fixed (commit abc1234)")):
            note = " — operator: x" if "dismissed" in (first, second) else ""
            body = (
                "## Security review\n"
                "- SEC-2 · %s — token in log — lib/log.rb / Log#write%s\n"
                "- SEC-2 · %s — token in log — lib/log.rb / Log#write%s\n"
                % (first, note if first == "dismissed" else "", second, note if second == "dismissed" else "")
            )
            scan = parse.scan_security(body)
            self.assertEqual([e["disposition"] for e in scan["entries"]], [first.split()[0]], body)
            self.assertEqual(len(scan["unparsed"]), 1, body)
            blocking = any(e["disposition"] == "open" for e in scan["entries"]) or scan["unparsed"]
            self.assertTrue(blocking, body)

    def test_any_sec_led_line_that_does_not_parse_is_unparsed(self):
        for line in ("* SEC-3 · open — c — a.py / f", "SEC-3 · open — c — a.py / f",
                     "- sec-3 · open — c — a.py / f", "1. SEC-3 · open — c — a.py / f"):
            scan = parse.scan_security("## Security review\n%s\n" % line)
            self.assertEqual(scan["entries"], [], line)
            self.assertEqual(scan["unparsed"], [line], line)

    def test_absent_section_is_not_present(self):
        self.assertEqual(parse.scan_security("no record"), parse.absent_security())
        self.assertEqual(
            parse.scan_security(None),
            {"present": False, "reviewed_sha": None, "entries": [], "unparsed": [], "record": None,
             "other_sections": []},
        )

    def test_the_section_ends_at_the_next_heading(self):
        scan = parse.scan_security(
            "## Security review\n- SEC-1 · open — a — b.py / c\n## Polish\n- SEC-2 · open — x — y / z\n"
        )
        self.assertEqual([e["id"] for e in scan["entries"]], ["SEC-1"])

    def test_garbage_never_raises(self):
        for body in ("## Security review\n- SEC-", "## Security review\n- SEC-x · · — —\n\x00", "\n" * 3, "##"):
            parse.scan_security(body)

    # Epic DoD 15: a consuming repo's own PR template can carry a section under the same heading. The
    # record is the first section with the record's shape; every other one is `other_sections`.
    LOOKALIKE = "## Security review (`ES-9`)\n\n- [x] **Not triggered** — docs only.\n- [ ] **Reviewed by:** _name_\n"

    def _record(self):
        return "## Security review\n- Reviewed: %s\n- SEC-1 · open — a — b.py / c\n" % self.HEAD

    def test_a_lookalike_above_the_record_is_left_out(self):
        body = "Fixes #1\n\n" + self.LOOKALIKE + "\n" + self._record()
        scan = parse.scan_security(body)
        lines = body.splitlines()
        self.assertTrue(scan["present"])
        self.assertEqual(scan["reviewed_sha"], self.HEAD)
        self.assertEqual([e["id"] for e in scan["entries"]], ["SEC-1"])
        self.assertEqual(scan["unparsed"], [])
        self.assertEqual(scan["record"], {"heading": "## Security review", "line": lines.index("## Security review") + 1})
        self.assertEqual(scan["other_sections"], [{"heading": "## Security review (`ES-9`)", "line": 3}])

    def test_a_lookalike_below_the_record_reads_the_same_record(self):
        body = self._record() + "\n" + self.LOOKALIKE
        scan = parse.scan_security(body)
        self.assertEqual(scan["reviewed_sha"], self.HEAD)
        self.assertEqual([e["id"] for e in scan["entries"]], ["SEC-1"])
        self.assertEqual(scan["record"], {"heading": "## Security review", "line": 1})
        self.assertEqual(scan["other_sections"], [{"heading": "## Security review (`ES-9`)", "line": 5}])

    def test_a_lookalike_alone_is_no_record(self):
        scan = parse.scan_security("Fixes #1\n\n" + self.LOOKALIKE)
        self.assertFalse(scan["present"])
        self.assertIsNone(scan["record"])
        self.assertIsNone(scan["reviewed_sha"])
        self.assertEqual(scan["entries"], [])
        self.assertEqual(scan["other_sections"], [{"heading": "## Security review (`ES-9`)", "line": 3}])

    def test_an_exact_heading_without_the_record_shape_is_no_record(self):
        scan = parse.scan_security("## Security review\n\nNothing to report here.\n")
        self.assertFalse(scan["present"])
        self.assertIsNone(scan["record"])
        self.assertEqual(scan["other_sections"], [{"heading": "## Security review", "line": 1}])

    def test_a_malformed_reviewed_line_alone_is_the_record(self):
        scan = parse.scan_security("## Security review\n- Reviewed: abc123\n")
        self.assertTrue(scan["present"])
        self.assertIsNone(scan["reviewed_sha"])
        self.assertEqual(scan["record"], {"heading": "## Security review", "line": 1})
        self.assertEqual(scan["other_sections"], [])

    def test_a_second_record_shaped_section_is_never_merged(self):
        body = self._record() + "## Security review\n- SEC-2 · open — x — y.py / z\n"
        scan = parse.scan_security(body)
        self.assertEqual([e["id"] for e in scan["entries"]], ["SEC-1"])
        self.assertEqual(scan["other_sections"], [{"heading": "## Security review", "line": 4}])

    def test_the_shared_record_predicate(self):
        self.assertTrue(parse._security_section_is_record(["  - Reviewed: x"]))
        self.assertTrue(parse._security_section_is_record(["", "1. SEC-1 garbage"]))
        self.assertFalse(parse._security_section_is_record(["- [x] **Not triggered**", "Reviewed by: me"]))
        self.assertFalse(parse._security_section_is_record([]))
        # The boundary against template prose: a `Reviewed:` line counts only as a `- ` bullet.
        self.assertFalse(parse._security_section_is_record(["Reviewed: me"]))
        self.assertFalse(parse._security_section_is_record(["- Reviewed by: me"]))

    def test_find_section_keeps_its_parenthetical_tolerance(self):
        # The watchpoint: #97's `planned_paths` reads `## Changes (file-level)` through this tolerance,
        # so the DoD 15 fix stays inside `scan_security` and never narrows the shared finder.
        self.assertEqual(parse._find_section(["## Changes (file-level)", "- `a.py`"], r"Changes"), (1, 2))
        self.assertEqual(parse._find_section(["## Security review (ES-9)", "x"], r"Security review"), (1, 2))


class SecurityCoverageTests(unittest.TestCase):
    """`security_coverage` / `parse.py security-coverage` — security-review.md "The procedure" step 1:
    the pass's `Covered:` block against the staged changed-files list, as exact strings. Always `ok`."""

    CHANGED = "scripts/a.py\napp/models/b.rb\n\nSources/C.swift\n"

    def _findings(self, *paths):
        return "## Findings\n1. scripts/a.py:3 — injection — high — c — p\n\nCovered:\n" + "".join(
            "- %s\n" % path for path in paths)

    def test_every_listed_path_covered(self):
        result = parse.security_coverage(self.CHANGED, self._findings("scripts/a.py", "app/models/b.rb", "Sources/C.swift"))
        self.assertEqual(result["uncovered"], [])
        self.assertEqual(result["extra"], [])
        self.assertEqual(result["covered"], ["scripts/a.py", "app/models/b.rb", "Sources/C.swift"])

    def test_a_missing_path_alone_is_uncovered(self):
        result = parse.security_coverage(self.CHANGED, self._findings("scripts/a.py", "Sources/C.swift"))
        self.assertEqual(result["uncovered"], ["app/models/b.rb"])
        self.assertEqual(result["extra"], [])

    def test_a_covered_path_not_in_the_list_is_extra(self):
        result = parse.security_coverage(
            self.CHANGED, self._findings("scripts/a.py", "app/models/b.rb", "Sources/C.swift", "lib/d.go"))
        self.assertEqual(result["uncovered"], [])
        self.assertEqual(result["extra"], ["lib/d.go"])

    def test_no_covered_block_covers_nothing(self):
        result = parse.security_coverage(self.CHANGED, "## Findings\n")
        self.assertEqual(result, {"covered": [], "uncovered": ["scripts/a.py", "app/models/b.rb", "Sources/C.swift"],
                                  "extra": []})

    def test_backticks_a_leading_dot_slash_and_a_bold_header_are_cosmetic(self):
        findings = "## Findings\n\n**Covered:**\n- `scripts/a.py`\n- ./app/models/b.rb\n- Sources/C.swift\n"
        self.assertEqual(parse.security_coverage(self.CHANGED, findings)["uncovered"], [])

    def test_the_block_ends_at_the_first_non_item_line(self):
        findings = self._findings("scripts/a.py") + "Done.\n- app/models/b.rb\n"
        self.assertEqual(parse.security_coverage(self.CHANGED, findings)["covered"], ["scripts/a.py"])

    def test_the_cli_emits_one_ok_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            changed = Path(tmp, "security-diff-files.txt")
            findings = Path(tmp, "security-findings.md")
            changed.write_text(self.CHANGED, encoding="utf-8")
            findings.write_text(self._findings("scripts/a.py"), encoding="utf-8")
            rc, out, _ = _run_cli(["security-coverage", str(changed), str(findings)])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["uncovered"], ["app/models/b.rb", "Sources/C.swift"])

    def test_an_unreadable_file_is_a_usage_error(self):
        rc, out, _ = _run_cli(["security-coverage", "/nonexistent/changed.txt", "/nonexistent/findings.md"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")


class ReviewLoopScanTests(unittest.TestCase):
    """`scan_review_loop` — the resolver's loop comment (skills/_shared/review-loop-record.md): the
    record lines under `<!-- review-loop:v1 -->`, and a pre-contract comment read leniently as
    `legacy`. Never raises; a record-shaped line that fails the grammar lands in `unparsed`."""

    M = parse.REVIEW_LOOP_MARKER

    def _record(self, *lines):
        return "\n".join((self.M, "## Review loop — phase 1", "### Round 1") + lines) + "\n"

    def _line(self, bucket="addressable", provenance="introduced", tier="defect", action="fixed",
              source="review", class_key="stale-read", site="scripts/a.py", check=None, item="it breaks",
              rid="R1.1"):
        line = "- %s · %s · %s · %s · %s · %s · class: %s · site: %s" % (
            rid, bucket, provenance, tier, action, source, class_key, site)
        if check:
            line += " · check: %s" % check
        return line + " — " + item

    def test_every_closed_value_parses_field_for_field(self):
        lines, seq = [], 0
        for bucket in parse.REVIEW_BUCKETS:
            for provenance in parse.REVIEW_PROVENANCES:
                seq += 1
                lines.append(self._line(bucket=bucket, provenance=provenance, rid="R1.%d" % seq))
        cases = [
            dict(tier="polish", action="ledger P1.3"),
            dict(action="settled", check="upheld", source="cold-read"),
            dict(action="deferred", check="overturned", source="evaluator"),
            dict(action="decision", source="human"),
        ]
        for case in cases:
            seq += 1
            lines.append(self._line(rid="R1.%d" % seq, **case))
        scan = parse.scan_review_loop(self._record(*lines))
        self.assertEqual(scan["format"], "record")
        self.assertEqual(scan["unparsed"], [])
        self.assertEqual(len(scan["findings"]), seq)
        self.assertEqual({f["bucket"] for f in scan["findings"]}, set(parse.REVIEW_BUCKETS))
        self.assertEqual({f["provenance"] for f in scan["findings"]}, set(parse.REVIEW_PROVENANCES))
        self.assertEqual({f["source"] for f in scan["findings"]}, set(parse.REVIEW_SOURCES))
        self.assertEqual({f["action"] for f in scan["findings"]}, set(parse.REVIEW_ACTIONS))
        ledger = scan["findings"][-4]
        self.assertEqual((ledger["action"], ledger["ledger_id"], ledger["tier"]), ("ledger", "P1.3", "polish"))
        self.assertEqual([f["check"] for f in scan["findings"][-3:-1]], ["upheld", "overturned"])
        first = scan["findings"][0]
        self.assertEqual(
            first,
            {"id": "R1.1", "phase": 1, "seq": 1, "bucket": "addressable", "provenance": "introduced",
             "tier": "defect", "action": "fixed", "ledger_id": None, "source": "review",
             "class_key": "stale-read", "site": "scripts/a.py", "check": None, "item": "it breaks",
             "line": lines[0]},
        )

    def test_site_none_and_an_item_carrying_separators_stay_whole(self):
        scan = parse.scan_review_loop(self._record(self._line(site="(none)", item="a — b · c: d")))
        self.assertEqual(scan["findings"][0]["site"], None)
        self.assertEqual(scan["findings"][0]["item"], "a — b · c: d")

    def test_a_malformed_line_is_unparsed_and_the_rest_still_reads(self):
        bad = [
            self._line(bucket="wrongish"),
            self._line(provenance="inherited"),
            self._line(tier="nit"),
            self._line(action="ledger P1"),
            self._line(source="bot"),
            self._line(check="maybe"),
            self._line(class_key="Stale-Read"),
            self._line(class_key="scripts/a.py"),
            self._line(class_key="one-two-three-four-five-six"),
            self._line(site="scripts/a.py:12"),
            self._line(site="scripts/a.py:load_rows"),
            "- R1.9 · addressable · introduced · defect · fixed · review · class: x — no site field",
            "- R1.9 · addressable · introduced · defect · fixed · review · class: x · site: a.py",
            "Review runs: phase 1 · three · settled",
            "Review runs: phase 1 · 3 · finished",
        ]
        good = self._line(rid="R1.2")
        scan = parse.scan_review_loop(self._record(*(bad + [good, "Review runs: phase 1 · 4 · accepted"])))
        self.assertEqual(scan["unparsed"], bad)
        self.assertEqual([f["id"] for f in scan["findings"]], ["R1.2"])
        self.assertEqual(scan["runs"], [{"phase": 1, "count": 4, "exit": "accepted"}])

    def test_a_repeated_id_is_unparsed(self):
        repeat = self._line(action="settled", bucket="refuted")
        scan = parse.scan_review_loop(self._record(self._line(), repeat))
        self.assertEqual([f["action"] for f in scan["findings"]], ["fixed"])
        self.assertEqual(scan["unparsed"], [repeat])

    def test_summary_lines_are_neither_findings_nor_unparsed(self):
        scan = parse.scan_review_loop(
            self._record("Security review: 2 findings, 1 fixed", "Finalisation: 3 entries triaged", self._line())
        )
        self.assertEqual(len(scan["findings"]), 1)
        self.assertEqual(scan["unparsed"], [])

    def test_every_exit_parses(self):
        lines = ["Review runs: phase %d · %d · %s" % (i + 1, i + 2, e) for i, e in enumerate(parse.REVIEW_EXITS)]
        scan = parse.scan_review_loop(self._record(*lines))
        self.assertEqual([r["exit"] for r in scan["runs"]], list(parse.REVIEW_EXITS))
        self.assertEqual(scan["runs"][0], {"phase": 1, "count": 2, "exit": "settled"})

    def test_round_headings_count_per_phase_heading(self):
        text = "\n".join([self.M, "### Round 1", "## Review loop — phase 2", "### Round 1", "### Round 2",
                          "## Review loop — phase 3", "### Round 1"])
        scan = parse.scan_review_loop(text)
        self.assertEqual(
            scan["phase_headings"],
            [{"phase": None, "rounds": 1}, {"phase": 2, "rounds": 2}, {"phase": 3, "rounds": 1}],
        )

    def test_the_marker_must_be_the_first_non_blank_line(self):
        scan = parse.scan_review_loop("\n\nquoting:\n%s\n%s\n" % (self.M, self._line()))
        self.assertIsNone(scan["format"])
        self.assertEqual(scan["findings"], [])

    def _legacy(self):
        # The shape a real consuming repo's pre-contract loop comment has.
        return "\n".join([
            "## Review loop — phase 1",
            "",
            "### Round 1 — `code-review` medium over `origin/main...HEAD`",
            "Defect count: 2",
            "- **The cache is read stale** (introduced · defect). Fixed: reordered.",
            "- R1.1 looks like a record line but is prose here",
            "Defect fixes: phase 1 @ 78c5783 — scripts/a.py:load; lib/b.rb:Client#call",
            "### Round 2 — `code-review` medium",
            "Defect count: 1",
            "Defect fixes: phase 1 @ ffb6c9b — scripts/a.py:save",
            "### Round 3",
            "Settled (not addressed):",
            "- refuted — a nil token crashes — evidence: guard at the call site — check: upheld (×2, kept)",
            "- plan-settled — config read at the working tree — cites: ## Architecture decisions \"x\"",
            "- deferred-by-plan — the reader is unwired — phase 2 \"The reader\"",
            "Announced: nothing.",
        ])

    def test_a_legacy_comment_reads_its_fixed_shapes_only(self):
        scan = parse.scan_review_loop(self._legacy())
        self.assertEqual(scan["format"], "legacy")
        self.assertEqual(scan["phase_headings"], [{"phase": 1, "rounds": 3}])
        self.assertEqual(scan["findings"], [])
        self.assertEqual(scan["runs"], [])
        self.assertEqual(scan["unparsed"], [])
        self.assertEqual(
            scan["settled"],
            [
                {"bucket": "refuted", "item": "a nil token crashes", "check": "upheld", "repeats": 2},
                {"bucket": "plan-settled", "item": "config read at the working tree", "check": None, "repeats": 1},
                {"bucket": "deferred-by-plan", "item": "the reader is unwired", "check": None, "repeats": 1},
            ],
        )
        self.assertEqual(
            scan["defect_fixes"],
            [
                {"phase": 1, "sha": "78c5783", "sites": ["scripts/a.py:load", "lib/b.rb:Client#call"]},
                {"phase": 1, "sha": "ffb6c9b", "sites": ["scripts/a.py:save"]},
            ],
        )

    def test_the_settled_block_reads_only_its_own_entries(self):
        # Cold read: a prose bullet after the block, past a blank line or with another lead word, was read
        # as a settled entry with bucket `fixed`.
        for text in (
            "Settled (not addressed):\n- refuted — x — evidence: y\n\n- refuted — prose after a gap",
            "Settled (not addressed):\n- refuted — x — evidence: y\n- fixed — the cache now reads after write",
        ):
            scan = parse.scan_review_loop(text)
            self.assertEqual([e["item"] for e in scan["settled"]], ["x"], text)

    def test_bold_round_lines_count_on_legacy_only(self):
        # The live run: a pre-contract comment opened its rounds with `**Round <k>**` lines.
        text = "## Phase 5 review loop\n**Round 1** (`review` medium) — defect count: 1\n**Round 2** — 0. Settled.\n"
        self.assertEqual(parse.scan_review_loop(text)["phase_headings"], [{"phase": 5, "rounds": 2}])
        record = self.M + "\n## Review loop — phase 1\n**Round 1** — prose\n### Round 1\n"
        self.assertEqual(parse.scan_review_loop(record)["phase_headings"], [{"phase": 1, "rounds": 1}])

    def test_a_heading_only_legacy_comment_counts_its_rounds(self):
        scan = parse.scan_review_loop("## Review loop — phase 2\n\n### Round 1\n- polish fixed\n### Round 2\n")
        self.assertEqual(scan["format"], "legacy")
        self.assertEqual(scan["phase_headings"], [{"phase": 2, "rounds": 2}])

    def test_each_legacy_trigger_alone_marks_a_legacy_comment(self):
        for text in ("Settled (not addressed):\n- refuted — x — evidence: y",
                     "Defect fixes: phase 1 @ abcdef1 — a.py:f", "Cold read: phase 1 @ abcdef1",
                     "## Phase 2 review loop\n### Round 1"):
            self.assertEqual(parse.scan_review_loop(text)["format"], "legacy", text)

    def test_any_other_comment_is_not_a_loop_comment(self):
        for text in ("LGTM", "## Review loop thoughts\n- R1.1 · nope", "", None):
            self.assertEqual(parse.scan_review_loop(text), parse.absent_review_loop())

    def test_garbage_never_raises(self):
        for text in (self.M + "\n- R", self.M + "\n- R1.1 · · · — \x00", self.M + "\nReview runs:",
                     "Settled (not addressed):\n-", "## Review loop — phase 99999999999999999999"):
            parse.scan_review_loop(text)


class ReviewLevelParseTests(unittest.TestCase):
    """`parse_review_levels` / `planned_paths` / `review_level_coverage` (#97) — the plan's
    `## Review level` lines and the paths it names, read by scripts/blast_radius.py. Never raising."""

    def test_grammar_heading_tolerance_and_duplicates(self):
        plan = (
            "## Review level (per phase)\n\n"
            "- phase 1 · medium — plumbing\n"
            "- phase 2 · xhigh — the seam phase 3 builds on\n"
            "- phase 3 · low — not a level the planner may write\n"
            "- phase 4 · high — a\n"
            "- phase 4 · medium — b\n"
            "prose under the heading is not a line\n"
            "\n## Risks\n- phase 5 · high — another section\n"
        )
        levels = parse.parse_review_levels(plan)
        self.assertTrue(levels["present"])
        self.assertEqual(levels["levels"], {
            1: {"level": "medium", "why": "plumbing"},
            2: {"level": "xhigh", "why": "the seam phase 3 builds on"},
        })
        self.assertEqual(levels["unparsed"], ["- phase 3 · low — not a level the planner may write"])
        self.assertEqual(levels["duplicated"], [4])

    def test_an_unparsed_line_spoils_its_phase_valid_line(self):
        levels = parse.parse_review_levels(
            "## Review level\n- phase 1 · medium — a\n- phase 1 · max — b\n- phase 2 · medium — c\n"
        )
        self.assertEqual(sorted(levels["levels"]), [2])
        self.assertEqual(levels["unparsed"], ["- phase 1 · max — b"])

    def test_absent_section(self):
        self.assertEqual(
            parse.parse_review_levels("## Phases\n"),
            {"present": False, "levels": {}, "unparsed": [], "duplicated": []},
        )

    def test_planned_paths_reads_both_sections_and_skips_spans_with_spaces(self):
        plan = (
            "## Approach\n- `elsewhere.py` is not a planned path\n"
            "## Changes (file-level)\n- `scripts/a.py` — `def f(x, y)` changes; `scripts/`\n"
            "## Test plan\n- Unit: `tests/test_a.py`, and `scripts/a.py` again; `tests/test_b.py:1483` and"
            " `tests/test_c.py:10-12` pins\n"
        )
        self.assertEqual(
            parse.planned_paths(plan),
            ["scripts/a.py", "scripts/", "tests/test_a.py", "tests/test_b.py", "tests/test_c.py"],
        )

    def test_planned_paths_skips_glob_spans(self):
        # Operator decision 2026-10-25, "Literal paths only": a glob would silence the out-of-plan raise.
        plan = "## Changes (file-level)\n- `*` `*.py` `scripts/*.py` `a?.py` `pages/[id].tsx` `src/` `b.py`\n"
        self.assertEqual(parse.planned_paths(plan), ["src/", "b.py"])

    def test_review_level_phases_defaults_to_phase_1(self):
        self.assertEqual(parse.review_level_phases([]), [1])
        self.assertEqual(parse.review_level_phases([{"number": 1}, {"number": 3}]), [1, 3])

    def test_coverage_missing_extra_and_single_phase(self):
        phases = [{"number": 1}, {"number": 2}]
        levels = parse.parse_review_levels("## Review level\n- phase 1 · high — a\n- phase 3 · high — c\n")
        self.assertEqual(parse.review_level_coverage(phases, levels), {
            "present": True, "missing": [2], "extra": [3], "unparsed": [], "duplicated": [],
        })
        single = parse.parse_review_levels("## Review level\n- phase 1 · medium — one change\n")
        self.assertEqual(parse.review_level_coverage([], single)["missing"], [])
        self.assertEqual(parse.review_level_coverage([], parse.parse_review_levels(""))["missing"], [1])

    def test_phases_cli_reports_review_levels(self):
        rc, out, err = _run_cli(["phases", str(PHASES_FIXTURES_DIR / "well_formed_multi_phase.md")])
        envelope = _parse_one_envelope(out)
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["review_levels"]["present"])
        self.assertEqual(envelope["review_levels"]["missing"], [1, 2, 3, 4])

    def test_scan_review_loop_skips_the_level_and_checkpoint_lines(self):
        text = "\n".join((
            parse.REVIEW_LOOP_MARKER,
            "## Review loop — phase 1",
            "Review level: phase 1 · high — plan declares `medium` for phase 1; raised to `high`: x",
            "### Round 1",
            "Checkpoint: phase 1 · look — checkpoint `pause`",
            "Review runs: phase 1 · 2 · settled",
        )) + "\n"
        scan = parse.scan_review_loop(text)
        self.assertEqual((scan["findings"], scan["unparsed"]), ([], []))
        self.assertEqual(scan["runs"], [{"phase": 1, "count": 2, "exit": "settled"}])


class FollowUpsScanTests(unittest.TestCase):
    """`scan_follow_ups` — a PR body's `## Follow-ups` section (skills/_shared/follow-ups-section.md),
    the epic residue's source for what each merged story PR deferred. Lenient for pre-contract bodies,
    never raises."""

    CONTRACT = REPO_ROOT / "skills" / "_shared" / "follow-ups-section.md"

    def _contract_example(self):
        text = self.CONTRACT.read_text(encoding="utf-8")
        start = text.index("```\n## Follow-ups\n") + len("```\n")
        return text[start:text.index("```", start)]

    def test_the_contract_worked_example(self):
        scan = parse.scan_follow_ups(self._contract_example(), "octo/widgets")
        # The Note line's #61 is context, never a deferral.
        self.assertEqual(scan["issues"], [301, 302])
        self.assertEqual(scan["covers"], {301: ["P1.2", "P2.1"]})

    def test_a_filed_line_reads_only_its_own_reference(self):
        body = "## Follow-ups\n- Filed: #301 — supersedes the approach in #45 · covers P1.1\n"
        self.assertEqual(parse.scan_follow_ups(body)["issues"], [301])

    def test_legacy_bullets_are_read_leniently_in_source_order(self):
        body = (
            "Fixes #12\n\n"
            "## Follow-ups\n"
            "- https://github.com/octo/widgets/issues/301 — cache invalidation (P1.2)\n"
            "- #302 — retry budget; see also #301\n"
            "- other repo: octo/gadgets#7 and https://github.com/octo/gadgets/issues/8\n"
            "## Polish\n"
            "- P1.1 · open — a — b.py / c — see #999\n"
        )
        scan = parse.scan_follow_ups(body, "octo/widgets")
        self.assertEqual(scan, {"present": True, "issues": [301, 302], "covers": {}})

    def test_notes_and_legacy_procedural_notes_are_skipped(self):
        body = (
            "## Follow-ups\n"
            "*Filed*\n"
            "- #301 — cache\n"
            "- Note: waits on question #61\n"
            "  continued: and #62\n"
            "- #303 — after a note\n"
            "**Procedural notes:**\n"
            "- the batch size waits on #64\n"
        )
        self.assertEqual(parse.scan_follow_ups(body)["issues"], [301, 303])

    def test_without_a_repo_only_bare_references_count(self):
        body = "## Follow-ups\n- https://github.com/octo/widgets/issues/301\n- #302\n"
        self.assertEqual(parse.scan_follow_ups(body)["issues"], [302])

    def test_heading_variants_and_absence(self):
        self.assertEqual(parse.scan_follow_ups("## Follow-ups filed\n- #5\n")["issues"], [5])
        self.assertEqual(parse.scan_follow_ups("## Followups (2)\n- #5\n- #6\n")["issues"], [5, 6])
        absent = {"present": False, "issues": [], "covers": {}}
        self.assertEqual(parse.scan_follow_ups("no section #5"), absent)
        self.assertEqual(parse.scan_follow_ups(None), absent)
        self.assertEqual(parse.scan_follow_ups("## Follow-ups\nnone\n")["issues"], [])


class ParsePhasesCliTests(unittest.TestCase):
    def test_phases_happy_path_envelope_conformance(self):
        rc, out, err = _run_cli(
            ["phases", str(PHASES_FIXTURES_DIR / "well_formed_multi_phase.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(len(envelope["phases"]), 4)

    def test_phases_no_section_ok_empty_list_via_cli(self):
        rc, out, err = _run_cli(
            ["phases", str(PHASES_FIXTURES_DIR / "no_section_single_phase.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["phases"], [])

    def test_phases_reports_the_staged_body_size(self):
        # The planner reads size from the call it already runs after every staging (plan-schema.md
        # "Size."), so a fresh draft never falls back to a hand count.
        fixture = PHASES_FIXTURES_DIR / "well_formed_multi_phase.md"
        rc, out, err = _run_cli(["phases", str(fixture)])
        envelope = _parse_one_envelope(out)
        text = fixture.read_bytes().decode("utf-8")
        self.assertEqual(envelope["size"], body_size(text))
        self.assertFalse(envelope["size"]["over_limit"])

    def test_phases_reports_size_for_a_plan_with_no_phases_section(self):
        # A fresh single-phase draft has no `## Phases` — exactly the path that had no size read.
        rc, out, err = _run_cli(["phases", str(PHASES_FIXTURES_DIR / "no_section_single_phase.md")])
        envelope = _parse_one_envelope(out)
        self.assertEqual(envelope["phases"], [])
        self.assertIn("chars", envelope["size"])

    def test_an_over_cap_body_is_ok_never_a_decision(self):
        # Size is the planner's to fix; `status` stays owned by the grammar.
        body = (PHASES_FIXTURES_DIR / "well_formed_multi_phase.md").read_text(encoding="utf-8")
        body += "\n## Risks & watchpoints\n\n- " + "x" * (BODY_CHAR_LIMIT + 1) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.md"
            path.write_text(body, encoding="utf-8")
            rc, out, err = _run_cli(["phases", str(path)])
        envelope = _parse_one_envelope(out)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(envelope["size"]["over_limit"])
        self.assertEqual(len(envelope["phases"]), 4)

    def test_phases_sub_issue_round_trips_through_the_envelope(self):
        rc, out, err = _run_cli(
            ["phases", str(PHASES_FIXTURES_DIR / "well_formed_sub_issue_mapped.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            [p["sub_issue"] for p in envelope["phases"]], ["(none)", 214, 214, 216]
        )

    def test_phases_checkpoint_round_trips_through_the_envelope(self):
        rc, out, err = _run_cli(
            ["phases", str(PHASES_FIXTURES_DIR / "well_formed_checkpoint.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(
            [p["checkpoint"] for p in envelope["phases"]], ["continue", "pause", None, None]
        )

    def test_phases_malformed_checkpoint_emits_phases_malformed_decision(self):
        rc, out, err = _run_cli(
            ["phases", str(PHASES_FIXTURES_DIR / "malformed_checkpoint_bad_value.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_decision_payload_shape(envelope)
        self.assertEqual(envelope["decision"]["code"], "PHASES_MALFORMED")

    def test_phases_malformed_sub_issue_emits_phases_malformed_decision(self):
        rc, out, err = _run_cli(
            ["phases", str(PHASES_FIXTURES_DIR / "malformed_sub_issue_bare_int.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        envelope_asserts.assert_decision_payload_shape(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "PHASES_MALFORMED")

    def test_letter_suffixed_label_is_malformed_with_locatable_evidence(self):
        # The #185 shape (issue #45): work inserted between a shipped prefix and an unshipped tail
        # by letter-suffixing the label instead of renumbering. `_PHASE_HEAD_RE` does not recognize
        # the head at all, so it reads as a stray line inside the PRIOR phase. The planner's S7 gate
        # tells the model to repair at `context.line_number` / `raw_line`, so both must be populated
        # and must point at the offending head — not merely be present on the envelope.
        rc, out, err = _run_cli(
            ["phases", str(PHASES_FIXTURES_DIR / "malformed_letter_suffixed_label.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        envelope_asserts.assert_decision_payload_shape(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "PHASES_MALFORMED")
        context = envelope["decision"]["context"]
        self.assertIsInstance(context["line_number"], int)
        self.assertIn("Phase 2c", context["raw_line"])
        fixture_lines = (
            PHASES_FIXTURES_DIR / "malformed_letter_suffixed_label.md"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(fixture_lines[context["line_number"] - 1], context["raw_line"])

    def test_phases_malformed_emits_phases_malformed_decision(self):
        rc, out, err = _run_cli(
            ["phases", str(PHASES_FIXTURES_DIR / "malformed_free_form_sequencing.md")]
        )
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        envelope_asserts.assert_decision_payload_shape(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "PHASES_MALFORMED")


class BlastJudgmentScanTests(unittest.TestCase):
    """`scan_blast_judgment` — the blast-radius judgment's verdict (#128,
    skills/resolver/references/blast-radius-judgment-prompt.md). Never raises; a verdict it cannot read is
    `None`, which `blast_radius.build_assess` counts as a raise."""

    def test_hold(self):
        self.assertEqual(
            parse.scan_blast_judgment("## Judgment\nverdict: hold\n"),
            {"verdict": "hold", "signals": [], "unparsed": []},
        )

    def test_raise_with_two_evidence_lines(self):
        scan = parse.scan_blast_judgment(
            "## Judgment\nverdict: raise\n"
            "- contract · scripts/a.py:12 — the envelope key phase 2 reads is renamed\n"
            "- dependents · skills/b.md:3-9 — a later story builds on this seam\n"
        )
        self.assertEqual(scan["verdict"], "raise")
        self.assertEqual(scan["unparsed"], [])
        self.assertEqual(
            scan["signals"],
            [
                {"dimension": "contract", "site": "scripts/a.py:12",
                 "evidence": "the envelope key phase 2 reads is renamed"},
                {"dimension": "dependents", "site": "skills/b.md:3-9",
                 "evidence": "a later story builds on this seam"},
            ],
        )

    def test_a_dimension_reads_in_any_case(self):
        scan = parse.scan_blast_judgment("## Judgment\nverdict: raise\n- Contract · a.py:1 — e\n")
        self.assertEqual((scan["signals"][0]["dimension"], scan["unparsed"]), ("contract", []))

    def test_a_raise_with_no_evidence_line_stays_a_raise(self):
        scan = parse.scan_blast_judgment("## Judgment\nverdict: raise\n")
        self.assertEqual((scan["verdict"], scan["signals"], scan["unparsed"]), ("raise", [], ["verdict: raise"]))

    def test_an_unknown_dimension_or_a_siteless_line_is_unparsed(self):
        for line in ("- volume · a.py:1 — many lines", "- seam · a.py — no line number", "- seam · a.py:1 —"):
            scan = parse.scan_blast_judgment("## Judgment\nverdict: raise\n%s\n" % line)
            self.assertEqual(scan["signals"], [], line)
            self.assertIn(line, scan["unparsed"], line)
            self.assertEqual(scan["verdict"], "raise", line)

    def test_unreadable_verdicts_are_none(self):
        for text in (
            "## Judgment\ncode: AMBIGUOUS\nThe diff file was empty.\n",
            "## Judgment\nverdict: hold\ncode: AMBIGUOUS\n",
            "",
            None,
            "verdict: hold\n",
            "## Judgment\nno verdict here\n",
            "## Judgment\nverdict: lower\n",
            "## Judgment\nverdict: raise\nverdict: hold\n",
        ):
            self.assertIsNone(parse.scan_blast_judgment(text)["verdict"], text)

    def test_conflicting_and_off_vocabulary_verdict_lines_are_unparsed(self):
        self.assertEqual(
            parse.scan_blast_judgment("## Judgment\nverdict: raise\nverdict: hold\n")["unparsed"],
            ["verdict: raise", "verdict: hold"],
        )
        self.assertEqual(parse.scan_blast_judgment("## Judgment\nverdict: lower\n")["unparsed"], ["verdict: lower"])

    def test_a_prose_code_line_is_not_a_code(self):
        scan = parse.scan_blast_judgment("## Judgment\nverdict: hold\nCode: the diff only renames a local.\n")
        self.assertEqual(scan["verdict"], "hold")

    def test_text_outside_the_section_is_ignored(self):
        scan = parse.scan_blast_judgment(
            "verdict: raise\n- seam · x.py:1 — before\n## Judgment\nverdict: hold\n"
            "Some prose.\n## Notes\nverdict: raise\n- seam · y.py:2 — after\n"
        )
        self.assertEqual(scan, {"verdict": "hold", "signals": [], "unparsed": []})

    def test_never_raises(self):
        for text in ("## Judgment\n\x00\n- ·\n", "## judgment\nVERDICT: Raise\n- seam · a:1 — e", "#" * 500):
            parse.scan_blast_judgment(text)
        self.assertEqual(parse.scan_blast_judgment("## judgment\nVERDICT: Raise\n- seam · a:1 — e")["verdict"], "raise")


# ============================================================================================
# DoD shape + closes-dod coverage — the checks that stop a plan indexing a non-DoD checklist
# ============================================================================================


def _phase(number, closes_dod):
    return {"number": number, "closes_dod": closes_dod}


class DodSummaryModuleTests(unittest.TestCase):
    """`dod_summary`: the DoD heading is the contract, so a checklist under any other heading is
    reported by name rather than read as the DoD (the drafter's pre-4.32 feature template wrote
    `## Acceptance criteria`, and every stage downstream then saw "no DoD")."""

    def test_a_dod_body_reports_its_count_and_no_other_checklist(self):
        body = "## Definition of done\n- [ ] a\n- [x] b (closed by commit abc1234)\n  - [ ] sub\n"
        self.assertEqual(
            parse.dod_summary(body), {"present": True, "count": 2, "other_checklists": []}
        )

    def test_acceptance_criteria_is_named_not_adopted(self):
        body = "## User story\nx\n\n## Acceptance criteria\n- [ ] a\n- [ ] b\n\n## Related issues\n"
        self.assertEqual(
            parse.dod_summary(body),
            {
                "present": False,
                "count": 0,
                "other_checklists": [{"heading": "Acceptance criteria", "count": 2}],
            },
        )

    def test_legacy_stories_checklist_is_never_reported(self):
        # Legacy epics carry `## Stories` and often no DoD; flagging it would prompt a rename on
        # every one of them (`_shared/epic-story-hierarchy.md` reads it as the checklist tier).
        body = "## Goal\nx\n\n## Stories\n- [ ] #12 — one\n- [x] #13 — two\n"
        self.assertEqual(parse.dod_summary(body)["other_checklists"], [])

    def test_sub_bullets_and_prose_do_not_make_a_checklist(self):
        body = "## Notes\n- plain bullet\n  - [ ] indented checkbox\n"
        self.assertEqual(parse.dod_summary(body)["other_checklists"], [])

    def test_an_h3_inside_a_section_stays_in_it_as_the_section_finder_reads_it(self):
        # `_find_section` ends a section only at `##`, so the DoD parser would index this bullet
        # too after a rename — the reported count must be the count the rename yields.
        body = "## Acceptance criteria\n- [ ] a\n### Detail\n- [ ] b\n"
        self.assertEqual(
            parse.dod_summary(body)["other_checklists"],
            [{"heading": "Acceptance criteria", "count": 2}],
        )

    def test_a_fenced_markdown_sample_is_not_a_checklist(self):
        body = (
            "## Context\nOur template looks like:\n\n```markdown\n## Acceptance criteria\n"
            "- [ ] a\n```\n\n## Definition of done\n- [ ] real\n"
        )
        summary = parse.dod_summary(body)
        self.assertEqual(summary["other_checklists"], [])
        self.assertEqual(summary["count"], 1)

    def test_a_tilde_fence_and_a_longer_closer_both_close(self):
        body = "~~~\n## Acceptance criteria\n- [ ] a\n~~~~\n\n## Acceptance criteria\n- [ ] b\n"
        self.assertEqual(
            parse.dod_summary(body)["other_checklists"],
            [{"heading": "Acceptance criteria", "count": 1}],
        )

    def test_a_malformed_annotation_is_still_counted(self):
        # The planner's prep stays lenient (a revise repairs annotations), so the count must not
        # depend on the annotation parse that raises.
        body = "## Definition of done\n- [x] a (closed by phase one, commit zzz)\n- [ ] b\n"
        with self.assertRaises(parse._DodMalformed):  # noqa: SLF001
            parse.parse_dod_bullets(body)
        self.assertEqual(parse.dod_summary(body)["count"], 2)

    def test_a_dod_and_another_checklist_both_report(self):
        body = "## Definition of done\n- [ ] a\n\n## Acceptance criteria\n- [ ] b\n"
        summary = parse.dod_summary(body)
        self.assertTrue(summary["present"])
        self.assertEqual(summary["count"], 1)
        self.assertEqual(
            summary["other_checklists"], [{"heading": "Acceptance criteria", "count": 1}]
        )
        self.assertIsNone(parse.misplaced_dod_note(summary))

    def test_the_misplaced_note_names_each_heading(self):
        note = parse.misplaced_dod_note(
            parse.dod_summary("## Acceptance criteria\n- [ ] a\n")
        )
        self.assertIn("`## Acceptance criteria` (1 bullet)", note)


class DodCoverageModuleTests(unittest.TestCase):
    AC_ONLY = parse.dod_summary("## Acceptance criteria\n- [ ] a\n- [ ] b\n")
    THREE = parse.dod_summary("## Definition of done\n- [ ] a\n- [ ] b\n- [ ] c\n")

    def test_single_phase_fallback_claims_nothing(self):
        self.assertIsNone(parse.dod_coverage([], self.THREE))
        self.assertIsNone(parse.dod_coverage([_phase(1, "(none)")], self.THREE))

    def test_exact_coverage_is_clean(self):
        cov = parse.dod_coverage([_phase(1, [1, 2]), _phase(2, [3])], self.THREE)
        self.assertEqual(
            (cov["out_of_range"], cov["unclaimed"], cov["claimed_more_than_once"], cov["drift"]),
            ([], [], [], False),
        )

    def test_claims_against_an_absent_dod_are_out_of_range_and_drift(self):
        # The reported failure: indexes written against `## Acceptance criteria`.
        cov = parse.dod_coverage([_phase(1, [1]), _phase(2, [2])], self.AC_ONLY)
        self.assertFalse(cov["present"])
        self.assertEqual(cov["out_of_range"], [1, 2])
        self.assertTrue(cov["drift"])
        self.assertEqual(cov["other_checklists"], [{"heading": "Acceptance criteria", "count": 2}])

    def test_a_single_claiming_phase_is_still_checked(self):
        cov = parse.dod_coverage([_phase(1, [1, 2])], self.AC_ONLY)
        self.assertTrue(cov["drift"])

    def test_planning_without_a_dod_is_clean(self):
        # The card's "Plan without a DoD" arm: every phase `(none)`, nothing claimed, no drift.
        cov = parse.dod_coverage([_phase(1, "(none)"), _phase(2, "(none)")], self.AC_ONLY)
        self.assertEqual((cov["out_of_range"], cov["drift"]), ([], False))

    def test_a_plan_claiming_nothing_against_a_real_dod_is_drift(self):
        # Deliberate: max claimed 0 vs 3 bullets — no tick would ever land, and only a re-plan fixes it.
        cov = parse.dod_coverage([_phase(1, "(none)"), _phase(2, "(none)")], self.THREE)
        self.assertEqual((cov["max_claimed"], cov["unclaimed"], cov["drift"]), (0, [1, 2, 3], True))

    def test_a_mid_gap_is_unclaimed_but_not_drift(self):
        # Drift is the written invariant (count vs max index); a gap is the reviewer's BLOCKER.
        cov = parse.dod_coverage([_phase(1, [1]), _phase(2, [3])], self.THREE)
        self.assertEqual(cov["unclaimed"], [2])
        self.assertFalse(cov["drift"])

    def test_a_dod_grown_past_the_plan_is_drift(self):
        cov = parse.dod_coverage([_phase(1, [1]), _phase(2, [2])], self.THREE)
        self.assertEqual(cov["unclaimed"], [3])
        self.assertTrue(cov["drift"])

    def test_a_doubly_claimed_index_is_reported(self):
        cov = parse.dod_coverage([_phase(1, [1, 2]), _phase(2, [2, 3])], self.THREE)
        self.assertEqual(cov["claimed_more_than_once"], [2])
        self.assertFalse(cov["drift"])

    def test_index_zero_is_out_of_range(self):
        cov = parse.dod_coverage([_phase(1, [0, 1, 2, 3])], self.THREE)
        self.assertEqual(cov["out_of_range"], [0])
        self.assertTrue(cov["drift"])


class DodShapeCliTests(unittest.TestCase):
    def _write(self, tmp, name, text):
        path = Path(tmp) / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_dod_reports_presence_and_other_checklists(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = self._write(tmp, "issue.md", "## Acceptance criteria\n- [ ] a\n")
            rc, out, err = _run_cli(["dod", body])
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["dod"], [])
        self.assertFalse(envelope["present"])
        self.assertEqual(
            envelope["other_checklists"], [{"heading": "Acceptance criteria", "count": 1}]
        )

    def test_phases_without_issue_body_carries_no_coverage_key(self):
        rc, out, err = _run_cli(["phases", str(PHASES_FIXTURES_DIR / "well_formed_multi_phase.md")])
        self.assertNotIn("dod_coverage", _parse_one_envelope(out))

    def test_phases_with_issue_body_reports_coverage(self):
        # well_formed_multi_phase.md claims 1, 2 (phase 2) and 3 (phase 3).
        with tempfile.TemporaryDirectory() as tmp:
            good = self._write(
                tmp, "good.md", "## Definition of done\n- [ ] a\n- [ ] b\n- [ ] c\n"
            )
            misplaced = self._write(
                tmp, "ac.md", "## Acceptance criteria\n- [ ] a\n- [ ] b\n- [ ] c\n"
            )
            plan = str(PHASES_FIXTURES_DIR / "well_formed_multi_phase.md")
            _, good_out, _ = _run_cli(["phases", plan, "--issue-body", good])
            _, ac_out, _ = _run_cli(["phases", plan, "--issue-body", misplaced])
        good_env, ac_env = _parse_one_envelope(good_out), _parse_one_envelope(ac_out)
        envelope_asserts.assert_full_envelope_conformance(good_env)
        self.assertFalse(good_env["dod_coverage"]["drift"])
        self.assertEqual(ac_env["status"], "ok")
        self.assertTrue(ac_env["dod_coverage"]["drift"])
        self.assertEqual(ac_env["dod_coverage"]["out_of_range"], [1, 2, 3])

    def test_phases_single_phase_plan_reports_null_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = self._write(tmp, "issue.md", "## Definition of done\n- [ ] a\n")
            rc, out, err = _run_cli(
                ["phases", str(PHASES_FIXTURES_DIR / "no_section_single_phase.md"),
                 "--issue-body", body]
            )
        envelope = _parse_one_envelope(out)
        self.assertIn("dod_coverage", envelope)
        self.assertIsNone(envelope["dod_coverage"])


# ============================================================================================
# Cross-cutting: usage errors, subcommand dispatch
# ============================================================================================


class ParseCliUsageTests(unittest.TestCase):
    def test_no_args_exits_usage_error_no_envelope(self):
        rc, out, err = _run_cli([])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")

    def test_unknown_subcommand_exits_usage_error(self):
        rc, out, err = _run_cli(["bogus-subcommand", "x"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
