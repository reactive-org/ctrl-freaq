"""Unit tests for scripts/config_block.py — the S21 Python port of v1's
``scripts/config-block.sh`` (architecture.md §3, §6, §7; docs/implementation.md S21).

This port is **file I/O only** — no `gh`, no network, no shim needed (unlike gh_gather.py /
gh_pr_gather.py / gh_persist.py) — so every test here drives the script as a real subprocess
against real temp files, the same way `tests/README.md`'s "Adding a fixture case" section
describes for a shim-backed script, minus the shim: `GH_SHIM_FIXTURES`/`intercepted_env` play no
role here because this module never calls `shutil.which("gh")` or spawns anything.

Two complementary strategies:

  - **In-process unit tests** (`ConfigBlockModuleTests`) call `config_block`'s own functions
    directly (`_scan_marker`, `_render_upsert`, `_render_remove`, `run_read`/`run_list` via
    captured stdout) — fast, and pins the exact line-index arithmetic against hand-traced expected
    output.
  - **Subprocess/CLI tests** (`ConfigBlockCliTests`) invoke `python3 scripts/config_block.py ...`
    as a real subprocess against real temp files — the end-to-end path a caller (`workspace.py`,
    `skills/setup/`) actually uses, proving the exit-code contract, the envelope shape via
    `tests/support/envelope_asserts`, and (the critical DoD row) **byte-identical round-trips**
    against fixtures lifted from `docs/specs/examples/config-blocks.md` /
    `skills/setup/references/block-authoring.md`'s canonical block forms.

Every exit code / decision / edge case in this file was cross-checked directly against the real
`scripts/config-block.sh` (v1, frozen reference) at authoring time — not inferred from prose —
using throwaway scratch files; see the module docstring of `scripts/config_block.py` for the
resulting behavior contract this suite pins.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
CONFIG_BLOCK_PY = SCRIPTS_DIR / "config_block.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
ROUNDTRIP_DIR = FIXTURES_DIR / "config_block_roundtrip"
CANONICAL_BLOCKS_DIR = FIXTURES_DIR / "config_block_canonical_blocks"

sys.path.insert(0, str(SCRIPTS_DIR))

import config_block  # noqa: E402  (import after sys.path setup, by necessity)
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR  # noqa: E402
from tests.support import envelope_asserts  # noqa: E402


def _run_cli(args, cwd=None):
    """Invoke ``config_block.py`` as a real subprocess. Returns
    ``(returncode, stdout_text, stderr_text)``. UTF-8 decoding is explicit (not relying on the
    platform default) to match architecture.md §1's "encoding=utf-8 pinned on all text I/O" even
    on the *test* side.
    """
    completed = subprocess.run(
        [sys.executable, str(CONFIG_BLOCK_PY)] + args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ),
    )
    return (
        completed.returncode,
        completed.stdout.decode("utf-8"),
        completed.stderr.decode("utf-8"),
    )


def _parse_one_envelope(stdout_text):
    """Parse exactly one JSON object from ``stdout_text`` — architecture.md §3's "exactly one
    JSON envelope on stdout" contract. Raises (fails the test) if it isn't exactly one line of
    valid JSON, mirroring the "envelope_present" boolean `assert_exit_code_contract` expects.
    """
    lines = [line for line in stdout_text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AssertionError(
            "expected exactly one non-blank stdout line (the envelope), got %d: %r"
            % (len(lines), lines)
        )
    return json.loads(lines[0])


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class ConfigBlockModuleTests(unittest.TestCase):
    """Direct, in-process tests of config_block's internal scan/render helpers — fast, and pins
    the line-index arithmetic ported from v1's `AWK_SCAN`/`run_upsert`/`run_remove` awk sources.
    """

    def test_scan_marker_absent_returns_zero_counts(self):
        oc, cc, oi, ci = config_block._scan_marker(["a", "b"], "missing")
        self.assertEqual((oc, cc, oi, ci), (0, 0, 0, 0))

    def test_scan_marker_well_formed_returns_one_based_indices(self):
        lines = ["pre", "<!-- m -->", "interior", "<!-- /m -->", "post"]
        oc, cc, oi, ci = config_block._scan_marker(lines, "m")
        self.assertEqual((oc, cc, oi, ci), (1, 1, 2, 4))

    def test_scan_marker_tolerates_surrounding_whitespace(self):
        lines = ["  <!-- m -->  ", "x", "\t<!-- /m -->\t"]
        oc, cc, oi, ci = config_block._scan_marker(lines, "m")
        self.assertEqual((oc, cc, oi, ci), (1, 1, 1, 3))

    def test_scan_marker_duplicate_open_counts_both(self):
        lines = ["<!-- m -->", "a", "<!-- /m -->", "<!-- m -->", "b", "<!-- /m -->"]
        oc, cc, oi, ci = config_block._scan_marker(lines, "m")
        self.assertEqual(oc, 2)
        self.assertEqual(cc, 2)

    def test_scan_marker_unterminated_has_zero_close_index(self):
        lines = ["<!-- m -->", "a"]
        oc, cc, oi, ci = config_block._scan_marker(lines, "m")
        self.assertEqual((oc, cc, oi, ci), (1, 0, 1, 0))

    def test_valid_marker_name_regex_accepts_allowed_charset(self):
        for name in ("issue-resolver-fast-checks", "weird:name_ok-1", "A1", "a:b_c-d"):
            self.assertRegex(name, config_block._VALID_MARKER_NAME_RE)

    def test_valid_marker_name_regex_rejects_disallowed_chars(self):
        for name in ("bad name!", "has/slash", "has.dot", ""):
            self.assertNotRegex(name, config_block._VALID_MARKER_NAME_RE)

    def test_render_upsert_replaces_existing_block_in_place(self):
        lines = ["pre", "<!-- m -->", "old interior", "<!-- /m -->", "post"]
        result = config_block._render_upsert(lines, "m", ["new", "body"], prepend=False)
        self.assertEqual(result, ["pre", "<!-- m -->", "new", "body", "<!-- /m -->", "post"])

    def test_render_upsert_appends_new_block_with_blank_line_separator(self):
        lines = ["existing content"]
        result = config_block._render_upsert(lines, "m", ["body"], prepend=False)
        self.assertEqual(result, ["existing content", "", "<!-- m -->", "body", "<!-- /m -->"])

    def test_render_upsert_appends_new_block_no_blank_line_when_file_empty(self):
        result = config_block._render_upsert([], "m", ["body"], prepend=False)
        self.assertEqual(result, ["<!-- m -->", "body", "<!-- /m -->"])

    def test_render_upsert_appends_no_extra_blank_when_last_line_already_blank(self):
        lines = ["existing content", ""]
        result = config_block._render_upsert(lines, "m", ["body"], prepend=False)
        self.assertEqual(result, ["existing content", "", "<!-- m -->", "body", "<!-- /m -->"])

    def test_render_upsert_prepend_new_block_with_blank_line_separator(self):
        lines = ["existing content"]
        result = config_block._render_upsert(lines, "m", ["body"], prepend=True)
        self.assertEqual(result, ["<!-- m -->", "body", "<!-- /m -->", "", "existing content"])

    def test_render_upsert_prepend_no_extra_blank_when_file_empty(self):
        result = config_block._render_upsert([], "m", ["body"], prepend=True)
        self.assertEqual(result, ["<!-- m -->", "body", "<!-- /m -->"])

    def test_render_upsert_prepend_replaces_in_place_when_block_already_exists(self):
        # Second --prepend on an existing block is a position-unchanged replace, not a re-prepend
        # (module docstring's placement-shape 1 takes priority over the prepend flag).
        lines = ["<!-- m -->", "old", "<!-- /m -->", "", "existing content"]
        result = config_block._render_upsert(lines, "m", ["new"], prepend=True)
        self.assertEqual(result, ["<!-- m -->", "new", "<!-- /m -->", "", "existing content"])

    def test_render_upsert_raises_ambiguous_on_duplicate(self):
        lines = ["<!-- m -->", "a", "<!-- /m -->", "<!-- m -->", "b", "<!-- /m -->"]
        with self.assertRaises(config_block._MarkerAmbiguous):
            config_block._render_upsert(lines, "m", ["body"], prepend=False)

    def test_render_upsert_raises_unterminated_on_open_only(self):
        lines = ["<!-- m -->", "a"]
        with self.assertRaises(config_block._MarkerUnterminated):
            config_block._render_upsert(lines, "m", ["body"], prepend=False)

    def test_render_remove_drops_block_and_preceding_blank_line(self):
        lines = ["pre", "", "<!-- m -->", "interior", "<!-- /m -->", "post"]
        new_lines, changed = config_block._render_remove(lines, "m")
        self.assertEqual(new_lines, ["pre", "post"])
        self.assertTrue(changed)

    def test_render_remove_no_blank_line_above_to_drop(self):
        lines = ["<!-- m -->", "interior", "<!-- /m -->", "post"]
        new_lines, changed = config_block._render_remove(lines, "m")
        self.assertEqual(new_lines, ["post"])
        self.assertTrue(changed)

    def test_render_remove_absent_block_is_unchanged_noop(self):
        lines = ["just", "some", "text"]
        new_lines, changed = config_block._render_remove(lines, "missing")
        self.assertEqual(new_lines, lines)
        self.assertFalse(changed)

    def test_render_remove_raises_ambiguous_on_duplicate(self):
        lines = ["<!-- m -->", "a", "<!-- /m -->", "<!-- m -->", "b", "<!-- /m -->"]
        with self.assertRaises(config_block._MarkerAmbiguous):
            config_block._render_remove(lines, "m")

    def test_render_remove_raises_unterminated_on_open_only(self):
        lines = ["<!-- m -->", "a"]
        with self.assertRaises(config_block._MarkerUnterminated):
            config_block._render_remove(lines, "m")

    def test_serialize_round_trips_through_read_lines(self):
        text = "a\nb\nc\n"
        lines = text.splitlines()
        self.assertEqual(config_block._serialize(lines), text)

    def test_serialize_empty_list_is_empty_string(self):
        self.assertEqual(config_block._serialize([]), "")


class ConfigBlockCliReadTests(unittest.TestCase):
    """`read` subcommand: raw text on success, exit-code contract on every failure shape."""

    def setUp(self):
        self.tmp = self.enterContext(_TempDir())

    def test_read_nonexistent_file_exits_3_matching_absent_block(self):
        # Verified directly against v1: a nonexistent file behaves exactly like "block absent,"
        # not a distinct error (module docstring's "Exit codes" note; docs/specs/setup.md's
        # explicit callout that neither SKILL.md nor block-authoring.md states this for `read`
        # — this port's own source-of-truth verification, not a guess).
        rc, out, err = _run_cli(["read", str(self.tmp / "nope.md"), "anything"])
        self.assertEqual(rc, 3)
        self.assertEqual(out, "")

    def test_read_absent_block_in_existing_file_exits_3(self):
        f = self.tmp / "f.md"
        _write(f, "hello\nworld\n")
        rc, out, err = _run_cli(["read", str(f), "missing"])
        self.assertEqual(rc, 3)
        self.assertEqual(out, "")

    def test_read_well_formed_block_prints_interior_and_exits_0(self):
        f = self.tmp / "f.md"
        _write(f, "pre\n<!-- m -->\nline one\nline two\n<!-- /m -->\npost\n")
        rc, out, err = _run_cli(["read", str(f), "m"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "line one\nline two\n")

    def test_read_tolerates_delimiter_whitespace(self):
        f = self.tmp / "f.md"
        _write(f, "  <!-- ws -->  \ncontent\n\t<!-- /ws -->\t\n")
        rc, out, err = _run_cli(["read", str(f), "ws"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "content\n")

    def test_read_duplicate_marker_exits_4(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\na\n<!-- /m -->\nmid\n<!-- m -->\nb\n<!-- /m -->\n")
        rc, out, err = _run_cli(["read", str(f), "m"])
        self.assertEqual(rc, 4)

    def test_read_unterminated_marker_exits_5(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\na\n")
        rc, out, err = _run_cli(["read", str(f), "m"])
        self.assertEqual(rc, 5)

    def test_read_empty_interior_prints_nothing_but_exits_0(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\n<!-- /m -->\n")
        rc, out, err = _run_cli(["read", str(f), "m"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_read_invalid_marker_name_exits_2_with_message(self):
        f = self.tmp / "f.md"
        _write(f, "content\n")
        rc, out, err = _run_cli(["read", str(f), "bad name!"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertIn("INVALID_MARKER_NAME", err)


class ConfigBlockCliListTests(unittest.TestCase):
    """`list` subcommand: `<status> <name>` inventory, always exit 0."""

    def setUp(self):
        self.tmp = self.enterContext(_TempDir())

    def test_list_nonexistent_file_exits_0_with_empty_output(self):
        # docs/specs/setup.md: "a nonexistent <file> exits 0 with empty stdout — 'no file' is
        # treated as 'no blocks,' not an error."
        rc, out, err = _run_cli(["list", str(self.tmp / "nope.md")])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_list_empty_file_exits_0_with_empty_output(self):
        f = self.tmp / "f.md"
        _write(f, "")
        rc, out, err = _run_cli(["list", str(f)])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_list_well_formed_block_reports_ok(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\nx\n<!-- /m -->\n")
        rc, out, err = _run_cli(["list", str(f)])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "ok m\n")

    def test_list_unterminated_block_reports_open(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\nx\n")
        rc, out, err = _run_cli(["list", str(f)])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "open m\n")

    def test_list_duplicate_open_reports_dup(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\na\n<!-- /m -->\n<!-- m -->\nb\n<!-- /m -->\n")
        rc, out, err = _run_cli(["list", str(f)])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "dup m\n")

    def test_list_multiple_blocks_is_deterministic_across_runs(self):
        # v1's own order is `awk` associative-array hash order (verified directly against the
        # real .sh: NOT file order, NOT alphabetical) — an implementation artifact, not a
        # documented contract. This port instead guarantees first-appearance order, and this test
        # pins that it is the *same* order on every run (config_block.py's own docstring:
        # "Marker discovery order").
        f = self.tmp / "f.md"
        _write(
            f,
            "<!-- alpha -->\nA\n<!-- /alpha -->\n\n"
            "<!-- beta -->\nB\n<!-- /beta -->\n\n"
            "<!-- gamma -->\nG\n<!-- /gamma -->\n",
        )
        results = set()
        for _ in range(5):
            rc, out, err = _run_cli(["list", str(f)])
            self.assertEqual(rc, 0)
            results.add(out)
        self.assertEqual(results, {"ok alpha\nok beta\nok gamma\n"})


class ConfigBlockCliUpsertTests(unittest.TestCase):
    """`upsert` subcommand: envelope conformance, idempotency (mtime-preserving no-op),
    MARKER_AMBIGUOUS surfacing, dry-run, and the four placement shapes end to end.
    """

    def setUp(self):
        self.tmp = self.enterContext(_TempDir())

    def _stage_body(self, text, name="body.md"):
        body_path = self.tmp / name
        _write(body_path, text)
        return body_path

    def test_upsert_happy_path_envelope_conformance(self):
        f = self.tmp / "f.md"
        _write(f, "preamble\n")
        body = self._stage_body("line one\nline two\n")
        rc, out, err = _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        envelope_asserts.assert_write_receipt_shape(envelope)
        envelope_asserts.assert_exit_code_contract(rc, envelope_present=True)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(envelope["op"], "upsert")
        self.assertEqual(envelope["marker"], "m")
        self.assertTrue(envelope["changed"])
        self.assertFalse(envelope["dry_run"])

    def test_upsert_body_sha256_matches_real_sha256sum(self):
        import hashlib

        f = self.tmp / "f.md"
        body_text = "line one\nline two\n"
        body = self._stage_body(body_text)
        rc, out, err = _run_cli(["upsert", str(f), "m", str(body)])
        envelope = _parse_one_envelope(out)
        expected = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        self.assertEqual(envelope["body_sha256"], expected)
        self.assertEqual(envelope["body_bytes"], len(body_text.encode("utf-8")))

    def test_upsert_creates_new_block_when_absent(self):
        f = self.tmp / "f.md"
        _write(f, "preamble\n")
        body = self._stage_body("line one\nline two\n")
        _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(f.read_text(encoding="utf-8"), "preamble\n\n<!-- m -->\nline one\nline two\n<!-- /m -->\n")

    def test_upsert_creates_file_from_scratch_when_absent(self):
        f = self.tmp / "new.md"
        self.assertFalse(f.exists())
        body = self._stage_body("hello\n")
        rc, out, err = _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        self.assertTrue(envelope["changed"])
        self.assertEqual(f.read_text(encoding="utf-8"), "<!-- m -->\nhello\n<!-- /m -->\n")

    def test_upsert_reupsert_same_body_is_noop_and_leaves_mtime_untouched(self):
        f = self.tmp / "f.md"
        _write(f, "preamble\n")
        body = self._stage_body("same body\n")
        _run_cli(["upsert", str(f), "m", str(body)])
        mtime_before = f.stat().st_mtime_ns

        rc, out, err = _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        self.assertFalse(envelope["changed"])
        mtime_after = f.stat().st_mtime_ns
        self.assertEqual(
            mtime_before,
            mtime_after,
            "a changed:false upsert must not touch the file on disk at all (not even mtime) — "
            "matching v1's finish_write cmp-then-mv guard",
        )

    def test_upsert_dry_run_reports_changed_true_but_writes_nothing(self):
        f = self.tmp / "new.md"
        body = self._stage_body("hello\n")
        rc, out, err = _run_cli(["upsert", str(f), "m", str(body), "--dry-run"])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        self.assertTrue(envelope["changed"])
        self.assertTrue(envelope["dry_run"])
        self.assertFalse(f.exists(), "--dry-run must not create/modify the target file")

    def test_upsert_prepend_places_new_block_at_top(self):
        f = self.tmp / "f.md"
        _write(f, "existing top content\n")
        body = self._stage_body("prepended\n")
        _run_cli(["upsert", str(f), "m", str(body), "--prepend"])
        self.assertEqual(
            f.read_text(encoding="utf-8"),
            "<!-- m -->\nprepended\n<!-- /m -->\n\nexisting top content\n",
        )

    def test_upsert_prepend_on_existing_block_replaces_in_place_not_reprepended(self):
        f = self.tmp / "f.md"
        body1 = self._stage_body("first\n", name="body1.md")
        _run_cli(["upsert", str(f), "m", str(body1), "--prepend"])
        _write(f, f.read_text(encoding="utf-8") + "\nmore content after\n")
        body2 = self._stage_body("second\n", name="body2.md")
        _run_cli(["upsert", str(f), "m", str(body2), "--prepend"])
        content = f.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("<!-- m -->\nsecond\n<!-- /m -->\n"))
        self.assertIn("more content after", content)

    def test_upsert_replaces_existing_block_interior_leaving_rest_untouched(self):
        f = self.tmp / "f.md"
        _write(f, "before\n<!-- m -->\nold\n<!-- /m -->\nafter\n")
        body = self._stage_body("new\n")
        _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(f.read_text(encoding="utf-8"), "before\n<!-- m -->\nnew\n<!-- /m -->\nafter\n")

    def test_upsert_duplicate_marker_surfaces_marker_ambiguous_needs_decision(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\na\n<!-- /m -->\n<!-- m -->\nb\n<!-- /m -->\n")
        body = self._stage_body("new\n")
        before = _read_bytes(f)
        rc, out, err = _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(rc, EXIT_OK, "needs_decision is a valid outcome, exit 0 (architecture.md §3)")
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        self.assertEqual(_read_bytes(f), before, "a refused write must leave the file untouched")

    def test_upsert_unterminated_marker_exits_5_and_leaves_file_untouched(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\na\n")
        before = _read_bytes(f)
        body = self._stage_body("new\n")
        rc, out, err = _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(rc, 5)
        self.assertEqual(_read_bytes(f), before)

    def test_upsert_missing_body_file_exits_2(self):
        f = self.tmp / "f.md"
        _write(f, "content\n")
        rc, out, err = _run_cli(["upsert", str(f), "m", str(self.tmp / "no-such-body.md")])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertIn("MISSING_BODY_FILE", err)

    def test_upsert_invalid_marker_name_exits_2(self):
        f = self.tmp / "f.md"
        _write(f, "content\n")
        body = self._stage_body("x\n")
        rc, out, err = _run_cli(["upsert", str(f), "bad name!", str(body)])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertIn("INVALID_MARKER_NAME", err)

    def test_upsert_allows_empty_body_file(self):
        # config-block.sh does no GitHub/git I/O and has no empty-body gate (that's gh_persist.py's
        # job, architecture.md §7/§12) — verified directly against v1: an empty staged body is
        # accepted and produces an empty interior, not a rejection.
        f = self.tmp / "f.md"
        _write(f, "pre\n")
        body = self._stage_body("")
        rc, out, err = _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        self.assertEqual(envelope["body_bytes"], 0)
        self.assertEqual(
            envelope["body_sha256"],
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(f.read_text(encoding="utf-8"), "pre\n\n<!-- m -->\n<!-- /m -->\n")

    def test_upsert_canonicalizes_body_with_no_trailing_newline(self):
        # v1's emit_body() streams the body file via `getline`, which always re-adds one newline
        # per line regardless of the source file's own trailing-newline state — canonicalizing so
        # the append and replace paths always produce identical interior bytes.
        f = self.tmp / "f.md"
        body = self._stage_body("no trailing newline")
        _run_cli(["upsert", str(f), "m", str(body)])
        self.assertEqual(f.read_text(encoding="utf-8"), "<!-- m -->\nno trailing newline\n<!-- /m -->\n")


class ConfigBlockCliRemoveTests(unittest.TestCase):
    """`remove` subcommand: envelope conformance, no-op semantics (absent file / absent block),
    MARKER_AMBIGUOUS surfacing, dry-run.
    """

    def setUp(self):
        self.tmp = self.enterContext(_TempDir())

    def test_remove_nonexistent_file_is_noop_changed_false(self):
        f = self.tmp / "nope.md"
        rc, out, err = _run_cli(["remove", str(f), "m"])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "ok")
        self.assertFalse(envelope["changed"])
        self.assertFalse(f.exists(), "remove on an absent file must not invent one")

    def test_remove_absent_block_in_existing_file_is_noop_changed_false(self):
        f = self.tmp / "f.md"
        _write(f, "just some text\n")
        before = _read_bytes(f)
        rc, out, err = _run_cli(["remove", str(f), "missing"])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        self.assertFalse(envelope["changed"])
        self.assertEqual(_read_bytes(f), before)

    def test_remove_dry_run_reports_no_write(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\nx\n<!-- /m -->\n")
        before = _read_bytes(f)
        rc, out, err = _run_cli(["remove", str(f), "m", "--dry-run"])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        self.assertTrue(envelope["changed"])
        self.assertTrue(envelope["dry_run"])
        self.assertEqual(_read_bytes(f), before, "--dry-run must not modify the file")

    def test_remove_well_formed_block_drops_block_and_preceding_blank_line(self):
        f = self.tmp / "f.md"
        _write(f, "pre\n\n<!-- m -->\nx\n<!-- /m -->\npost\n")
        rc, out, err = _run_cli(["remove", str(f), "m"])
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(f.read_text(encoding="utf-8"), "pre\npost\n")

    def test_remove_duplicate_marker_surfaces_marker_ambiguous_needs_decision(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\na\n<!-- /m -->\n<!-- m -->\nb\n<!-- /m -->\n")
        before = _read_bytes(f)
        rc, out, err = _run_cli(["remove", str(f), "m"])
        self.assertEqual(rc, EXIT_OK)
        envelope = _parse_one_envelope(out)
        envelope_asserts.assert_full_envelope_conformance(envelope)
        self.assertEqual(envelope["status"], "needs_decision")
        self.assertEqual(envelope["decision"]["code"], "MARKER_AMBIGUOUS")
        self.assertEqual(_read_bytes(f), before)

    def test_remove_unterminated_marker_exits_5(self):
        f = self.tmp / "f.md"
        _write(f, "<!-- m -->\na\n")
        rc, out, err = _run_cli(["remove", str(f), "m"])
        self.assertEqual(rc, 5)

    def test_remove_invalid_marker_name_exits_2(self):
        f = self.tmp / "f.md"
        _write(f, "content\n")
        rc, out, err = _run_cli(["remove", str(f), "bad name!"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertIn("INVALID_MARKER_NAME", err)


class ConfigBlockCliUsageTests(unittest.TestCase):
    """Bare usage-error paths: no subcommand, unknown subcommand — exit 2, no envelope."""

    def test_no_args_exits_2_with_no_envelope(self):
        rc, out, err = _run_cli([])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")

    def test_unknown_subcommand_exits_2_with_no_envelope(self):
        rc, out, err = _run_cli(["bogus"])
        self.assertEqual(rc, EXIT_USAGE_ERROR)
        self.assertEqual(out, "")


class ConfigBlockRoundTripFixtureTests(unittest.TestCase):
    """**The critical DoD row**: byte-identical round-trips against the v1 canonical block forms,
    fixtures lifted verbatim from docs/specs/examples/config-blocks.md /
    skills/setup/references/block-authoring.md. Every "after" fixture file in
    tests/fixtures/config_block_roundtrip/ was cross-generated and diffed against the real v1
    `scripts/config-block.sh` at authoring time (not hand-derived) — see the implementor report.

    Assertions compare **bytes**, not parsed/semantic structure, per the DoD's own wording:
    "assert byte equality, not semantic."
    """

    def setUp(self):
        self.tmp = self.enterContext(_TempDir())

    def _copy_fixture_into_tmp(self, fixture_name, dest_name="commands.md"):
        src = ROUNDTRIP_DIR / fixture_name
        dest = self.tmp / dest_name
        dest.write_bytes(src.read_bytes())
        return dest

    def test_read_returns_exact_interior_bytes_of_a_canonical_block(self):
        f = self._copy_fixture_into_tmp("commands_before.md")
        rc, out, err = _run_cli(["read", str(f), "pr-evaluator-merge-policy"])
        self.assertEqual(rc, 0)
        expected = (CANONICAL_BLOCKS_DIR / "pr_evaluator_merge_policy_body.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(out, expected)

    def test_upsert_then_read_returns_exactly_what_was_written(self):
        f = self._copy_fixture_into_tmp("commands_before.md")
        body = CANONICAL_BLOCKS_DIR / "issue_resolver_fast_checks_filled_body.md"
        rc, _, _ = _run_cli(["upsert", str(f), "pr-evaluator-static-checks", str(body)])
        self.assertEqual(rc, 0)
        rc, out, _ = _run_cli(["read", str(f), "pr-evaluator-static-checks"])
        self.assertEqual(rc, 0)
        self.assertEqual(out, body.read_text(encoding="utf-8"))

    def test_upsert_new_block_into_multiblock_file_leaves_rest_byte_identical(self):
        f = self._copy_fixture_into_tmp("commands_before.md")
        body = CANONICAL_BLOCKS_DIR / "issue_resolver_fast_checks_filled_body.md"
        rc, out, err = _run_cli(["upsert", str(f), "pr-evaluator-static-checks", str(body)])
        self.assertEqual(rc, 0)
        expected_bytes = (ROUNDTRIP_DIR / "commands_after_upsert_new_block.md").read_bytes()
        self.assertEqual(
            f.read_bytes(),
            expected_bytes,
            "upsert of a NEW block into a file already holding other canonical blocks must "
            "leave every pre-existing byte untouched",
        )

    def test_remove_middle_block_leaves_rest_byte_identical(self):
        f = self._copy_fixture_into_tmp("commands_before.md")
        rc, out, err = _run_cli(["remove", str(f), "issue-resolver-canonical-suite"])
        self.assertEqual(rc, 0)
        expected_bytes = (ROUNDTRIP_DIR / "commands_after_remove_middle_block.md").read_bytes()
        self.assertEqual(
            f.read_bytes(),
            expected_bytes,
            "removing one block from a multi-block file must leave every other block and its "
            "surrounding bytes untouched",
        )

    def test_upsert_replacing_existing_block_leaves_rest_byte_identical(self):
        f = self._copy_fixture_into_tmp("commands_before.md")
        new_body = ROUNDTRIP_DIR / "new_merge_policy_body.md"
        rc, out, err = _run_cli(["upsert", str(f), "pr-evaluator-merge-policy", str(new_body)])
        self.assertEqual(rc, 0)
        expected_bytes = (ROUNDTRIP_DIR / "commands_after_replace_existing_block.md").read_bytes()
        self.assertEqual(
            f.read_bytes(),
            expected_bytes,
            "replacing an existing block's interior must leave every other block byte-identical",
        )

    def test_full_roundtrip_upsert_then_remove_restores_original_bytes(self):
        # upsert a new canonical block, then remove it — the file must return to exactly its
        # original bytes (the "one blank line above" removal exactly undoes the "one blank line
        # before a new block" insertion, per both v1 and this port's shared placement logic).
        f = self._copy_fixture_into_tmp("commands_before.md")
        original_bytes = f.read_bytes()
        body = CANONICAL_BLOCKS_DIR / "issue_resolver_fast_checks_filled_body.md"
        _run_cli(["upsert", str(f), "pr-evaluator-static-checks", str(body)])
        self.assertNotEqual(f.read_bytes(), original_bytes)
        rc, out, err = _run_cli(["remove", str(f), "pr-evaluator-static-checks"])
        self.assertEqual(rc, 0)
        self.assertEqual(f.read_bytes(), original_bytes)

    def test_reupsert_canonical_block_with_same_body_is_byte_level_noop(self):
        # Idempotency against a real canonical multi-block file, not just a synthetic one-block
        # fixture — the whole point of upsert per config-block.sh's own module docstring.
        f = self._copy_fixture_into_tmp("commands_before.md")
        original_bytes = f.read_bytes()
        same_body_text = (CANONICAL_BLOCKS_DIR / "pr_evaluator_merge_policy_body.md").read_text(
            encoding="utf-8"
        )
        same_body_path = self.tmp / "same_body.md"
        same_body_path.write_text(same_body_text, encoding="utf-8")
        rc, out, err = _run_cli(["upsert", str(f), "pr-evaluator-merge-policy", str(same_body_path)])
        self.assertEqual(rc, 0)
        envelope = _parse_one_envelope(out)
        self.assertFalse(envelope["changed"])
        self.assertEqual(f.read_bytes(), original_bytes)

    def test_upsert_unfilled_placeholder_template_into_fresh_file_matches_the_fenced_schema(self):
        # docs/specs/examples/config-blocks.md's `issue-resolver-fast-checks` entry quotes the
        # *unfilled* schema template ("- `<command>` — <description>"), distinct from
        # `issue_resolver_fast_checks_filled_body.md`'s worked-example fill-in used by the other
        # round-trip tests above — this pins that the byte-identical guarantee holds for the bare
        # schema shape too, not only for a filled-in instance of it.
        f = self.tmp / "commands.md"
        body = CANONICAL_BLOCKS_DIR / "issue_resolver_fast_checks_body.md"
        rc, out, err = _run_cli(["upsert", str(f), "issue-resolver-fast-checks", str(body)])
        self.assertEqual(rc, 0)
        self.assertEqual(
            f.read_bytes(),
            b"<!-- issue-resolver-fast-checks -->\n- `<command>` \xe2\x80\x94 <description>\n"
            b"<!-- /issue-resolver-fast-checks -->\n",
        )
        # read it back and diff directly against the fixture body file, byte for byte.
        rc, out, err = _run_cli(["read", str(f), "issue-resolver-fast-checks"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.encode("utf-8"), body.read_bytes())


class ConfigBlockCliUpsertMalformedTargetTests(unittest.TestCase):
    """`upsert` against a target file whose *existing* content for the marker is itself malformed
    (duplicate/unterminated) — the write-side counterpart of the `read`-side tests above, proving
    upsert refuses rather than guesses through pre-existing malformed content too.
    """

    def setUp(self):
        self.tmp = self.enterContext(_TempDir())

    def test_upsert_marker_name_with_allowed_special_chars(self):
        f = self.tmp / "f.md"
        _write(f, "pre\n")
        body = self.tmp / "body.md"
        _write(body, "body content\n")
        rc, out, err = _run_cli(["upsert", str(f), "weird:name_ok-1", str(body)])
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("<!-- weird:name_ok-1 -->", f.read_text(encoding="utf-8"))
        self.assertIn("<!-- /weird:name_ok-1 -->", f.read_text(encoding="utf-8"))


class _TempDir:
    """Minimal context manager wrapping tempfile.TemporaryDirectory, used via
    ``self.enterContext`` so cleanup happens automatically regardless of test outcome (Python
    3.11+'s ``TestCase.enterContext`` is not assumed available on the architecture.md §1 floor of
    3.9, hence this hand-rolled shim rather than relying on it directly).
    """

    def __init__(self):
        import tempfile

        self._tempdir = tempfile.TemporaryDirectory()

    def __enter__(self):
        return Path(self._tempdir.name).resolve()

    def __exit__(self, *exc_info):
        self._tempdir.cleanup()
        return False


class ReadBlockWithTests(unittest.TestCase):
    """`read_block_with` (#96) is `read_block_anywhere`'s discovery over an injected reader: fed the
    same files through a dict-backed reader it answers identically, its `source` the discovery path
    that resolves to `read_block_anywhere`'s absolute one."""

    BLOCK = "<!-- demo -->\n- `a`\n<!-- /demo -->\n"

    def _both(self, files, marker="demo"):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            on_disk = config_block.read_block_anywhere(str(root), marker)
            via_reader = config_block.read_block_with(files.get, marker)
            resolved = str(root / via_reader[2]) if via_reader[0] else None
            return on_disk, via_reader, resolved

    def _assert_parity(self, files, expected_source):
        on_disk, via_reader, resolved = self._both(files)
        self.assertEqual(on_disk[:2], via_reader[:2])
        self.assertEqual(via_reader[2], expected_source)
        self.assertEqual(on_disk[2], resolved)

    def test_commands_md_wins_over_claude_md(self):
        self._assert_parity({"COMMANDS.md": self.BLOCK, "CLAUDE.md": self.BLOCK.replace("`a`", "`b`")}, "COMMANDS.md")

    def test_malformed_first_candidate_falls_through(self):
        self._assert_parity({"COMMANDS.md": self.BLOCK + self.BLOCK, "CLAUDE.md": self.BLOCK}, "CLAUDE.md")

    def test_one_level_include_is_discovered(self):
        self._assert_parity({"CLAUDE.md": "See @docs/config.md\n", "docs/config.md": self.BLOCK}, "docs/config.md")

    def test_absent_block(self):
        self._assert_parity({"CLAUDE.md": "# nothing\n"}, None)

    def test_candidate_paths_with_lists_includes_after_the_candidates(self):
        files = {"CLAUDE.md": "See @docs/a.md and @/abs/b.md\n"}
        self.assertEqual(
            config_block.candidate_paths_with(files.get),
            ["COMMANDS.md", "CLAUDE.md", "docs/a.md", "/abs/b.md"],
        )


# `unittest.TestCase.enterContext` was added in Python 3.11; this suite targets the architecture.md
# §1 floor of 3.9, so provide the same ergonomics via a tiny polyfill method rather than requiring
# 3.11 — every TestCase above calls `self.enterContext(_TempDir())` expecting this method to exist.
def _enter_context(self, cm):
    value = cm.__enter__()
    self.addCleanup(cm.__exit__, None, None, None)
    return value


if not hasattr(unittest.TestCase, "enterContext"):
    unittest.TestCase.enterContext = _enter_context


if __name__ == "__main__":
    unittest.main()
