"""Unit tests for scripts/pipelib/ — the architecture.md §3 envelope + shared primitives (S3).

Covers every pipelib function (decisions, hashing, spill, envelope, process, hooks), the
architecture.md §3 <-> pipelib decision-code drift check, threshold precedence (new/legacy/
default), the subprocess runner's construction-time guarantees (refuses str, refuses non-git/gh,
never shell=True, UTF-8 pinned), the hook executor's separateness, the AUTH_REQUIRED fixture case
(via the offline gh shim), the tests/support/envelope_asserts.py conformance helpers (both their
positive and negative behavior), and the toy script's end-to-end envelope emission.

Run via `python3 tests/run.py` (wires `scripts/` and the repo root onto sys.path and the
gh-shim-first PATH before this file is imported — see tests/run.py and tests/README.md).
"""

import ast
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipelib import decisions, envelope, hashing, hooks, limits, process, spill
from tests.support import envelope_asserts, shimenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE_MD = REPO_ROOT / "docs" / "architecture.md"
TOY_SCRIPT = REPO_ROOT / "scripts" / "pipelib_envelope_fixture_demo.py"


def _read_architecture_md():
    with open(ARCHITECTURE_MD, "r", encoding="utf-8") as fh:
        return fh.read()


def _shell_kwarg_bool_literals_in_module(module):
    """Parse ``module``'s real source with :mod:`ast` and return the list of literal boolean
    values passed as a ``shell=`` keyword argument to any call in the module — e.g. a module
    whose only real code is ``subprocess.run(..., shell=False, ...)`` returns ``[False]``.

    This inspects actual syntax, not text, so it is immune to a docstring or comment merely
    *mentioning* ``shell=True``/``shell=False`` in prose (as this module's own docstrings do, to
    explain the guarantee) — a naive substring search over ``inspect.getsource`` would false-positive
    on that prose, which is exactly the bug this helper exists to avoid.
    """
    tree = ast.parse(inspect.getsource(module))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant):
                    found.append(kw.value.value)
    return found


def _parse_decision_codes_from_architecture_md():
    """Extract the closed decision-code set out of docs/architecture.md §3 itself, by parsing
    every list-item-leading, backtick-quoted ALL_CAPS_WITH_UNDERSCORES token in the "Closed
    decision-code set" bullet sub-list — bounded precisely between that bullet's own bold lead-in
    and the next top-level bullet ("**Notices**"), NOT the whole §3 section (which also contains
    unrelated ALL-CAPS-ish tokens, e.g. the `GH_PIPELINE_INLINE_THRESHOLD_BYTES` /
    `GH_OPS_INLINE_THRESHOLD_BYTES` env var names in the later "Spill routing" bullet — those must
    not be mistaken for decision codes). This is the drift-check's ground truth: it reads the doc,
    not a hand-copied list in this test file, so a doc edit that adds/removes/renames a code
    without a matching pipelib.decisions change fails this test rather than passing silently.
    """
    text = _read_architecture_md()
    start_marker = "- **Closed decision-code set**"
    end_marker = "- **Notices**"
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    section = text[start:end]
    # A code always appears as a list item leading with a backtick-quoted ALL-CAPS token
    # (`  - \`CODE\` ...` or, for the combined ROOT_* line, `\`CODE\` / \`CODE\` / \`CODE\``) —
    # anchoring on "leading `- `" (or a following " / `") excludes any other backtick-quoted
    # prose token (script filenames, doc names) that happens to appear later on the same line,
    # since those never themselves start a line with `- \`` or follow " / `".
    leading = set(re.findall(r"^\s*-\s*`([A-Z][A-Z0-9_]*)`", section, flags=re.MULTILINE))
    combined = set(re.findall(r"/\s*`([A-Z][A-Z0-9_]*)`", section))
    return leading | combined


class DecisionsModuleTests(unittest.TestCase):
    """pipelib.decisions: the closed decision-code constants + needs_decision()."""

    def test_decision_codes_are_exactly_the_closed_set(self):
        expected = {
            "AUTH_REQUIRED",
            "EMPTY_BODY_FILE",
            "BODY_TOO_LONG",
            "MARKER_AMBIGUOUS",
            "TARGET_IS_PR",
            "DOD_MALFORMED",
            "PHASES_MALFORMED",
            "BRANCH_IN_USE",
            "WORKSPACE_MISMATCH",
            "PLAN_MISSING",
            "THREAD_SUPERSEDED_PLAN",
            "AMBIGUOUS",
            "BLOCKED_ON_USER",
            "TARGET_IS_SLICE",
        }
        self.assertEqual(decisions.DECISION_CODES, frozenset(expected))
        self.assertEqual(len(decisions.DECISION_CODES), 14)

    def test_drift_check_doc_codes_equal_lib_codes(self):
        """The load-bearing drift-check: docs/architecture.md §3's decision-code list, parsed
        directly out of the doc, must equal pipelib.decisions.DECISION_CODES exactly. Adding a
        code to one without the other fails this test (S3 DoD item 6).
        """
        doc_codes = _parse_decision_codes_from_architecture_md()
        self.assertEqual(
            doc_codes,
            set(decisions.DECISION_CODES),
            "docs/architecture.md §3 decision codes and pipelib.decisions.DECISION_CODES have "
            "drifted apart — doc-only: %r, lib-only: %r"
            % (doc_codes - set(decisions.DECISION_CODES), set(decisions.DECISION_CODES) - doc_codes),
        )

    def test_each_named_constant_matches_its_own_string_value(self):
        # Every constant's Python name must equal its own string value (a paraphrase would be a
        # contract break) — checked mechanically rather than by hand-copying 13 assertions.
        for name in sorted(decisions.DECISION_CODES):
            self.assertEqual(getattr(decisions, name), name)

    def test_needs_decision_builds_the_architecture_shape(self):
        payload = decisions.needs_decision(
            decisions.AMBIGUOUS,
            summary="two epic branches match",
            context={"candidates": ["epic/1-a", "epic/1-b"]},
            options=["use epic/1-a", "use epic/1-b"],
        )
        self.assertEqual(
            payload,
            {
                "code": "AMBIGUOUS",
                "summary": "two epic branches match",
                "context": {"candidates": ["epic/1-a", "epic/1-b"]},
                "options": ["use epic/1-a", "use epic/1-b"],
            },
        )

    def test_needs_decision_defaults_context_and_options_to_empty(self):
        payload = decisions.needs_decision(decisions.BLOCKED_ON_USER, summary="stuck")
        self.assertEqual(payload["context"], {})
        self.assertEqual(payload["options"], [])

    def test_needs_decision_rejects_a_code_outside_the_closed_set(self):
        with self.assertRaises(ValueError):
            decisions.needs_decision("NOT_A_REAL_CODE", summary="x")

    def test_needs_decision_rejects_empty_summary(self):
        with self.assertRaises(ValueError):
            decisions.needs_decision(decisions.AMBIGUOUS, summary="")
        with self.assertRaises(ValueError):
            decisions.needs_decision(decisions.AMBIGUOUS, summary=None)

    def test_deps_unsupported_is_not_in_the_closed_decision_set(self):
        # DEPS_UNSUPPORTED is a *notice* (architecture.md §3), never a needs_decision code.
        self.assertEqual(decisions.DEPS_UNSUPPORTED, "DEPS_UNSUPPORTED")
        self.assertNotIn(decisions.DEPS_UNSUPPORTED, decisions.DECISION_CODES)


class HashingModuleTests(unittest.TestCase):
    """pipelib.hashing: sha256_hex / sha256_hex_file."""

    def test_sha256_hex_matches_known_vector(self):
        # sha256("") is a well-known constant digest.
        self.assertEqual(
            hashing.sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_sha256_hex_matches_hashlib_directly(self):
        import hashlib

        data = b"the quick brown fox jumps over the lazy dog"
        self.assertEqual(hashing.sha256_hex(data), hashlib.sha256(data).hexdigest())

    def test_sha256_hex_rejects_non_bytes(self):
        with self.assertRaises(TypeError):
            hashing.sha256_hex("not bytes, a str")

    def test_sha256_hex_file_matches_sha256_hex_of_same_bytes(self):
        data = b"file-based hashing must match in-memory hashing\n" * 100
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.bin"
            path.write_bytes(data)
            self.assertEqual(hashing.sha256_hex_file(path), hashing.sha256_hex(data))

    def test_sha256_hex_file_accepts_str_path_too(self):
        data = b"str-path variant"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample2.bin"
            path.write_bytes(data)
            self.assertEqual(hashing.sha256_hex_file(str(path)), hashing.sha256_hex(data))


class SpillThresholdPrecedenceTests(unittest.TestCase):
    """pipelib.spill.resolve_threshold: new var > legacy var > default (S3 DoD item 5)."""

    def test_default_when_neither_var_set(self):
        self.assertEqual(spill.resolve_threshold(env={}), 25600)
        self.assertEqual(spill.resolve_threshold(env={}), spill.DEFAULT_THRESHOLD_BYTES)

    def test_legacy_var_honored_when_new_var_absent(self):
        env = {spill.LEGACY_THRESHOLD_ENV_VAR: "1000"}
        self.assertEqual(spill.resolve_threshold(env=env), 1000)

    def test_new_var_takes_precedence_over_legacy_var(self):
        env = {
            spill.THRESHOLD_ENV_VAR: "500",
            spill.LEGACY_THRESHOLD_ENV_VAR: "999999",
        }
        self.assertEqual(spill.resolve_threshold(env=env), 500)

    def test_new_var_alone_is_honored(self):
        env = {spill.THRESHOLD_ENV_VAR: "42"}
        self.assertEqual(spill.resolve_threshold(env=env), 42)

    def test_unparsable_new_var_falls_back_to_legacy(self):
        env = {
            spill.THRESHOLD_ENV_VAR: "not-an-int",
            spill.LEGACY_THRESHOLD_ENV_VAR: "777",
        }
        self.assertEqual(spill.resolve_threshold(env=env), 777)

    def test_unparsable_new_and_legacy_falls_back_to_default(self):
        env = {
            spill.THRESHOLD_ENV_VAR: "garbage",
            spill.LEGACY_THRESHOLD_ENV_VAR: "also-garbage",
        }
        self.assertEqual(spill.resolve_threshold(env=env), spill.DEFAULT_THRESHOLD_BYTES)

    def test_resolve_threshold_defaults_to_os_environ_when_env_is_none(self):
        # Sanity check that omitting `env=` reads the real process environment rather than
        # crashing or silently returning the default regardless of what's actually set.
        old = os.environ.get(spill.THRESHOLD_ENV_VAR)
        try:
            os.environ[spill.THRESHOLD_ENV_VAR] = "12345"
            self.assertEqual(spill.resolve_threshold(), 12345)
        finally:
            if old is None:
                os.environ.pop(spill.THRESHOLD_ENV_VAR, None)
            else:
                os.environ[spill.THRESHOLD_ENV_VAR] = old


class SpillBytesTests(unittest.TestCase):
    """pipelib.spill.spill_bytes: inline vs path decision + field production."""

    def test_small_section_is_inline_with_bare_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            fields = spill.spill_bytes(b"short text", "body", tmp, env={})
            self.assertEqual(fields["body_mode"], "inline")
            self.assertEqual(fields["body_bytes"], len(b"short text"))
            self.assertEqual(fields["body"], "short text")
            self.assertNotIn("body_path", fields)

    def test_large_section_spills_to_path_and_writes_the_file(self):
        big = b"x" * 100
        with tempfile.TemporaryDirectory() as tmp:
            env = {spill.THRESHOLD_ENV_VAR: "50"}
            fields = spill.spill_bytes(big, "thread", tmp, env=env)
            self.assertEqual(fields["thread_mode"], "path")
            self.assertEqual(fields["thread_bytes"], 100)
            self.assertIn("thread_path", fields)
            self.assertNotIn("thread", fields)
            written = Path(fields["thread_path"]).read_bytes()
            self.assertEqual(written, big)

    def test_exactly_at_threshold_is_inline_not_path(self):
        # architecture.md §3: "inline when <= threshold" — an exact boundary case.
        data = b"y" * 50
        with tempfile.TemporaryDirectory() as tmp:
            env = {spill.THRESHOLD_ENV_VAR: "50"}
            fields = spill.spill_bytes(data, "boundary", tmp, env=env)
            self.assertEqual(fields["boundary_mode"], "inline")

    def test_one_byte_over_threshold_spills(self):
        data = b"y" * 51
        with tempfile.TemporaryDirectory() as tmp:
            env = {spill.THRESHOLD_ENV_VAR: "50"}
            fields = spill.spill_bytes(data, "boundary", tmp, env=env)
            self.assertEqual(fields["boundary_mode"], "path")

    def test_force_path_spills_a_tiny_section_regardless_of_size(self):
        # architecture.md §3: "Diffs and line-comment sets are always path."
        with tempfile.TemporaryDirectory() as tmp:
            fields = spill.spill_bytes(b"tiny", "diff", tmp, env={}, force_path=True)
            self.assertEqual(fields["diff_mode"], "path")
            self.assertIn("diff_path", fields)

    def test_custom_filename_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            fields = spill.spill_bytes(
                b"x" * 100, "diff", tmp, env={spill.THRESHOLD_ENV_VAR: "1"}, filename="diff.patch"
            )
            self.assertTrue(fields["diff_path"].endswith("diff.patch"))

    def test_creates_scratch_dir_if_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "does" / "not" / "exist" / "yet"
            fields = spill.spill_bytes(
                b"x" * 10, "sect", nested, env={spill.THRESHOLD_ENV_VAR: "1"}
            )
            self.assertTrue(Path(fields["sect_path"]).is_file())

    def test_rejects_non_bytes_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TypeError):
                spill.spill_bytes("a str, not bytes", "body", tmp)

    def test_rejects_empty_section_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                spill.spill_bytes(b"data", "", tmp)


class EnvelopeBuildOkTests(unittest.TestCase):
    """pipelib.envelope.build_ok / emit_ok."""

    def test_build_ok_default_shape(self):
        env = envelope.build_ok()
        self.assertEqual(env, {"status": "ok", "notices": []})

    def test_build_ok_merges_payload_at_top_level(self):
        env = envelope.build_ok(payload={"repo": "o/n", "scratch": "/tmp/x"})
        self.assertEqual(env["status"], "ok")
        self.assertEqual(env["repo"], "o/n")
        self.assertEqual(env["scratch"], "/tmp/x")
        self.assertEqual(env["notices"], [])

    def test_build_ok_carries_notices(self):
        env = envelope.build_ok(notices=["DEPS_UNSUPPORTED"])
        self.assertEqual(env["notices"], ["DEPS_UNSUPPORTED"])

    def test_build_ok_rejects_payload_shadowing_status(self):
        with self.assertRaises(ValueError):
            envelope.build_ok(payload={"status": "sneaky"})

    def test_build_ok_rejects_payload_shadowing_notices(self):
        with self.assertRaises(ValueError):
            envelope.build_ok(payload={"notices": ["sneaky"]})

    def test_emit_ok_writes_one_json_line_and_returns_the_built_dict(self):
        buf = io.StringIO()
        returned = envelope.emit_ok(payload={"x": 1}, stream=buf)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed, returned)
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["x"], 1)


class EnvelopeBuildNeedsDecisionTests(unittest.TestCase):
    """pipelib.envelope.build_needs_decision / emit_needs_decision."""

    def test_build_needs_decision_shape(self):
        decision = decisions.needs_decision(
            decisions.PLAN_MISSING, summary="no plan comment found", options=["run planner"]
        )
        env = envelope.build_needs_decision(decision)
        self.assertEqual(
            env,
            {
                "status": "needs_decision",
                "decision": {
                    "code": "PLAN_MISSING",
                    "summary": "no plan comment found",
                    "context": {},
                    "options": ["run planner"],
                },
                "notices": [],
            },
        )

    def test_build_needs_decision_rejects_non_dict(self):
        with self.assertRaises(TypeError):
            envelope.build_needs_decision("not a dict")

    def test_build_needs_decision_rejects_code_outside_closed_set(self):
        with self.assertRaises(ValueError):
            envelope.build_needs_decision(
                {"code": "MADE_UP", "summary": "x", "context": {}, "options": []}
            )

    def test_build_needs_decision_rejects_missing_required_key(self):
        for missing in ("summary", "context", "options"):
            payload = {"code": "AMBIGUOUS", "summary": "s", "context": {}, "options": []}
            del payload[missing]
            with self.assertRaises(ValueError):
                envelope.build_needs_decision(payload)

    def test_emit_needs_decision_writes_one_json_line(self):
        buf = io.StringIO()
        decision = decisions.needs_decision(decisions.BRANCH_IN_USE, summary="branch busy")
        returned = envelope.emit_needs_decision(decision, stream=buf)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed, returned)
        self.assertEqual(parsed["status"], "needs_decision")
        self.assertEqual(parsed["decision"]["code"], "BRANCH_IN_USE")


class EnvelopeEmitLowLevelTests(unittest.TestCase):
    """pipelib.envelope.emit: the single serialization point + exit-code constants."""

    def test_exit_code_constants_match_architecture_md_section_3(self):
        self.assertEqual(envelope.EXIT_OK, 0)
        self.assertEqual(envelope.EXIT_USAGE_ERROR, 2)

    def test_emit_rejects_invalid_status(self):
        buf = io.StringIO()
        with self.assertRaises(ValueError):
            envelope.emit({"status": "not-a-real-status"}, stream=buf)
        # Nothing must have been written on the rejected path.
        self.assertEqual(buf.getvalue(), "")

    def test_emit_writes_exactly_one_line_ending_in_newline(self):
        buf = io.StringIO()
        envelope.emit({"status": "ok", "notices": []}, stream=buf)
        content = buf.getvalue()
        self.assertEqual(content.count("\n"), 1)
        self.assertTrue(content.endswith("\n"))

    def test_emit_to_a_real_buffer_stream_pins_utf8(self):
        # A stream exposing `.buffer` (like sys.stdout in a real process) exercises the
        # buffer.write(...encode("utf-8")) branch, not the plain-text-stream branch.
        class _FakeBufferedStream:
            def __init__(self):
                self.buffer = _FakeBuffer()

        class _FakeBuffer:
            def __init__(self):
                self.written = b""

            def write(self, data):
                self.written += data

            def flush(self):
                pass

        fake = _FakeBufferedStream()
        envelope.emit({"status": "ok", "notices": [], "note": "café"}, stream=fake)
        decoded = fake.buffer.written.decode("utf-8")
        parsed = json.loads(decoded)
        self.assertEqual(parsed["note"], "café")


class EnvelopeWriteTests(unittest.TestCase):
    """pipelib.envelope.write_envelope: a file copy of exactly the line `emit` writes, written
    atomically (mkstemp + os.replace) with mkstemp's 0600 mode (#116's board feed)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "envelope.json"

    def test_the_file_holds_exactly_the_emitted_line(self):
        env = envelope.build_ok(payload={"target": {"title": "Widgets — café ✓"}})
        buf = io.StringIO()
        envelope.emit(env, stream=buf)
        envelope.write_envelope(env, str(self.path))
        self.assertEqual(self.path.read_bytes(), buf.getvalue().encode("utf-8"))

    def test_the_file_is_owner_only_and_no_temp_file_is_left(self):
        envelope.write_envelope(envelope.build_ok(), str(self.path))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["envelope.json"])

    def test_a_second_write_replaces_the_first(self):
        envelope.write_envelope(envelope.build_ok(payload={"n": 1}), str(self.path))
        envelope.write_envelope(envelope.build_ok(payload={"n": 2}), str(self.path))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["n"], 2)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["envelope.json"])

    def test_an_invalid_status_writes_nothing(self):
        with self.assertRaises(ValueError):
            envelope.write_envelope({"status": "not-a-real-status"}, str(self.path))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_an_unwritable_directory_raises_oserror(self):
        with self.assertRaises(OSError):
            envelope.write_envelope(envelope.build_ok(), str(self.dir / "missing" / "envelope.json"))


class ProcessRunnerConstructionGuaranteesTests(unittest.TestCase):
    """pipelib.process.run: refuses string commands and non-git/gh binaries by construction,
    never shell=True, pins UTF-8 (S3 DoD item 2).
    """

    def test_refuses_a_string_command(self):
        with self.assertRaises(TypeError):
            process.run("git status")

    def test_refuses_a_string_command_even_if_it_looks_like_one_safe_binary(self):
        with self.assertRaises(TypeError):
            process.run("gh auth status")

    def test_refuses_empty_argv(self):
        with self.assertRaises(TypeError):
            process.run([])

    def test_refuses_non_git_gh_binary(self):
        with self.assertRaises(ValueError):
            process.run(["rm", "-rf", "/"])

    def test_refuses_non_git_gh_binary_even_with_a_relative_or_absolute_path(self):
        with self.assertRaises(ValueError):
            process.run(["/bin/sh", "-c", "echo hi"])

    def test_allows_git(self):
        result = process.run(["git", "--version"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("git version", result.stdout)

    def test_allowed_binaries_is_exactly_git_and_gh(self):
        self.assertEqual(process.ALLOWED_BINARIES, frozenset({"git", "gh"}))

    def test_source_never_passes_shell_true_for_the_general_runner(self):
        # Static proof, not just behavioral: parse the module's real AST (not a substring search
        # over its source text, which would false-positive on the module's own docstring prose
        # explaining this very guarantee) and confirm every actual `shell=` keyword argument in
        # the module is literally False — there is no call site that could ever pass True.
        shell_values = _shell_kwarg_bool_literals_in_module(process)
        self.assertEqual(shell_values, [False], "process.py must pass shell=False, never True")

    def test_run_pins_utf8_encoding_on_subprocess_run_call(self):
        source = inspect.getsource(process.run)
        self.assertIn('encoding="utf-8"', source)

    def test_result_stdout_and_stderr_are_str_not_bytes(self):
        result = process.run(["git", "--version"])
        self.assertIsInstance(result.stdout, str)
        self.assertIsInstance(result.stderr, str)

    def test_check_true_raises_on_nonzero_non_auth_failure(self):
        with self.assertRaises(subprocess.CalledProcessError):
            process.run(["git", "this-is-not-a-real-git-subcommand"], check=True)

    def test_command_result_repr_does_not_crash(self):
        result = process.run(["git", "--version"])
        self.assertIn("CommandResult", repr(result))


class ProcessAuthRequiredClassificationTests(unittest.TestCase):
    """AUTH_REQUIRED classification: exit-code-4 signature (primary) and stderr-marker fallback
    (secondary), both unit-tested directly against the pure classification function, plus the
    full fixture-backed subprocess path (S3 DoD item 3).
    """

    def test_gh_exit_code_4_is_classified_as_auth_failure(self):
        self.assertTrue(process._is_gh_auth_failure("gh", 4, ""))

    def test_git_exit_code_4_is_not_classified_as_auth_failure(self):
        # The exit-code-4 signature is gh-specific; git returning 4 for some unrelated reason
        # must not be misclassified.
        self.assertFalse(process._is_gh_auth_failure("git", 4, ""))

    def test_gh_zero_exit_is_never_auth_failure_regardless_of_stderr_text(self):
        self.assertFalse(process._is_gh_auth_failure("gh", 0, "gh auth login"))

    def test_gh_nonzero_nonfour_with_stderr_marker_is_classified_via_fallback(self):
        self.assertTrue(
            process._is_gh_auth_failure("gh", 1, "gh: Bad credentials (HTTP 401)")
        )

    def test_gh_nonzero_nonfour_without_any_marker_is_not_auth_failure(self):
        self.assertFalse(process._is_gh_auth_failure("gh", 1, "some unrelated network error"))

    def test_stderr_marker_match_is_case_insensitive(self):
        self.assertTrue(process._is_gh_auth_failure("gh", 1, "GH AUTH LOGIN required"))

    def test_gh_auth_required_exit_code_constant_is_4(self):
        # gh's own documented exit-code contract (`gh help exit-codes`): 4 == "requires
        # authentication." Pinned as a named constant rather than a magic number at each use site.
        self.assertEqual(process.GH_AUTH_REQUIRED_EXIT_CODE, 4)

    def test_auth_required_fixture_end_to_end_via_shim(self):
        """The DoD's headline fixture case: under the offline shim, a `gh auth status` call that
        fails with gh's real auth-failure exit code (4) must come back from
        pipelib.process.run(...) with .auth_required == True — proving the whole path (shim
        fixture -> real subprocess -> pipelib classification) works, not just the pure function
        in isolation.
        """
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="pipelib_gh_auth_required")
        result = process.run(["gh", "auth", "status"], env=env)
        self.assertEqual(result.returncode, 4)
        self.assertTrue(result.auth_required)

    def test_successful_gh_auth_fixture_is_not_classified_as_auth_required(self):
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="pipelib_gh_auth_ok")
        result = process.run(["gh", "auth", "status"], env=env)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.auth_required)

    def test_check_true_does_not_raise_on_an_auth_required_failure(self):
        # An AUTH_REQUIRED outcome is decision-worthy, not exception-worthy — check=True must not
        # raise for it even though the exit code is nonzero, so callers branch on
        # result.auth_required instead of needing a try/except.
        env = shimenv.intercepted_env(base_env=os.environ, fixture_case="pipelib_gh_auth_required")
        result = process.run(["gh", "auth", "status"], env=env, check=True)
        self.assertTrue(result.auth_required)


class HookExecutorIsSeparateTests(unittest.TestCase):
    """pipelib.hooks: the §1 carve-out lives in its own, explicitly-named module/function,
    separate from the general runner, and is the only place shell=True appears (S3 DoD item 2).
    """

    def test_hooks_module_is_separate_from_process_module(self):
        self.assertNotEqual(hooks.__name__, process.__name__)
        self.assertFalse(hasattr(process, "run_repo_owned_hook_command"))
        self.assertFalse(hasattr(hooks, "ALLOWED_BINARIES"))

    def test_hook_executor_function_name_reads_as_the_carveout(self):
        # Not just "a function exists" — its name itself must self-identify as the carve-out
        # (architecture.md §1: "a separate, explicitly-named entry point").
        self.assertTrue(hasattr(hooks, "run_repo_owned_hook_command"))
        self.assertIn("hook", hooks.run_repo_owned_hook_command.__name__)

    def test_only_hooks_module_uses_shell_true(self):
        # AST-based (not substring) for the same reason as
        # ProcessRunnerConstructionGuaranteesTests.test_source_never_passes_shell_true_for_the_general_runner:
        # a docstring mentioning `shell=True` in prose must not be mistaken for a real call site.
        hooks_shell_values = _shell_kwarg_bool_literals_in_module(hooks)
        process_shell_values = _shell_kwarg_bool_literals_in_module(process)
        self.assertEqual(hooks_shell_values, [True], "hooks.py's one call site must pass shell=True")
        self.assertEqual(process_shell_values, [False], "process.py must never pass shell=True")

    def test_hook_executor_runs_a_shell_string_and_respects_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.txt"
            result = hooks.run_repo_owned_hook_command("pwd > marker.txt && echo done", cwd=tmp)
            self.assertEqual(result.returncode, 0)
            self.assertIn("done", result.stdout)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), os.path.realpath(tmp))

    def test_hook_executor_supports_shell_syntax_the_general_runner_cannot(self):
        # Pipes/&&/redirects are exactly what the argument-list runner structurally cannot do —
        # this is *why* the carve-out exists, not an incidental feature.
        with tempfile.TemporaryDirectory() as tmp:
            result = hooks.run_repo_owned_hook_command("echo one && echo two | cat", cwd=tmp)
            self.assertEqual(result.returncode, 0)
            self.assertIn("one", result.stdout)
            self.assertIn("two", result.stdout)

    def test_hook_executor_rejects_a_list_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TypeError):
                hooks.run_repo_owned_hook_command(["echo", "hi"], cwd=tmp)

    def test_hook_executor_rejects_empty_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                hooks.run_repo_owned_hook_command("   ", cwd=tmp)

    def test_hook_executor_requires_cwd(self):
        with self.assertRaises(ValueError):
            hooks.run_repo_owned_hook_command("echo hi", cwd=None)

    def test_hook_executor_does_not_raise_on_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = hooks.run_repo_owned_hook_command("exit 7", cwd=tmp)
            self.assertEqual(result.returncode, 7)

    def test_hook_result_repr_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = hooks.run_repo_owned_hook_command("true", cwd=tmp)
            self.assertIn("HookResult", repr(result))

    def test_only_workspace_py_is_documented_as_the_hooks_importer(self):
        # A cheap textual guard (not a runtime constraint pipelib can enforce, since Python has no
        # private-import mechanism): pipelib.hooks' own module docstring names workspace.py as the
        # intended sole importer, so a reviewer grepping the docstring sees the constraint stated,
        # matching CLAUDE.md's convention of naming the "why" alongside the rule.
        self.assertIn("workspace.py", hooks.__doc__)


class EnvelopeAssertsPositiveTests(unittest.TestCase):
    """tests/support/envelope_asserts.py: the conformance helpers, exercised against
    KNOWN-CONFORMANT envelopes (must not raise) — S3 DoD item 4, first half.
    """

    def test_assert_valid_status_accepts_ok(self):
        envelope_asserts.assert_valid_status({"status": "ok", "notices": []})

    def test_assert_valid_status_accepts_needs_decision(self):
        envelope_asserts.assert_valid_status(
            {
                "status": "needs_decision",
                "decision": {"code": "AMBIGUOUS", "summary": "s", "context": {}, "options": []},
                "notices": [],
            }
        )

    def test_assert_decision_payload_shape_accepts_conformant_needs_decision(self):
        env = envelope.build_needs_decision(
            decisions.needs_decision(decisions.PLAN_MISSING, summary="no plan")
        )
        envelope_asserts.assert_decision_payload_shape(env)

    def test_assert_decision_payload_shape_is_a_noop_for_ok_status(self):
        envelope_asserts.assert_decision_payload_shape({"status": "ok", "notices": []})

    def test_assert_spill_sections_well_formed_accepts_inline_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            fields = spill.spill_bytes(b"hi", "body", tmp, env={})
            env = {"status": "ok", "notices": []}
            env.update(fields)
            envelope_asserts.assert_spill_sections_well_formed(env, ["body"])

    def test_assert_spill_sections_well_formed_accepts_path_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            fields = spill.spill_bytes(b"hi", "diff", tmp, env={}, force_path=True)
            env = {"status": "ok", "notices": []}
            env.update(fields)
            envelope_asserts.assert_spill_sections_well_formed(env, ["diff"])

    def test_assert_exit_code_contract_accepts_zero_with_envelope(self):
        envelope_asserts.assert_exit_code_contract(0, envelope_present=True)

    def test_assert_exit_code_contract_accepts_two_without_envelope(self):
        envelope_asserts.assert_exit_code_contract(2, envelope_present=False)

    def test_assert_exit_code_contract_allows_other_nonzero_either_way(self):
        # architecture.md §3: any other non-zero is a hard failure; envelope not guaranteed
        # either way, so neither True nor False should raise here.
        envelope_asserts.assert_exit_code_contract(1, envelope_present=False)
        envelope_asserts.assert_exit_code_contract(1, envelope_present=True)

    def test_assert_write_receipt_shape_accepts_conformant_receipt(self):
        digest = hashing.sha256_hex(b"body text")
        envelope_asserts.assert_write_receipt_shape(
            {"body_bytes": len(b"body text"), "body_sha256": digest}
        )

    def test_assert_notices_shape_accepts_empty_and_nonempty_list(self):
        envelope_asserts.assert_notices_shape({"notices": []})
        envelope_asserts.assert_notices_shape({"notices": ["DEPS_UNSUPPORTED"]})

    def test_assert_full_envelope_conformance_accepts_a_real_ok_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            fields = spill.spill_bytes(b"content", "issue_body", tmp, env={})
            env = envelope.build_ok(payload=fields)
            envelope_asserts.assert_full_envelope_conformance(env, spill_section_names=["issue_body"])


class EnvelopeAssertsNegativeTests(unittest.TestCase):
    """tests/support/envelope_asserts.py: the conformance helpers correctly REJECT malformed
    envelopes (must raise AssertionError) — S3 DoD item 4, second half. An assertion helper that
    can't fail is worthless as a conformance gate for later suites.
    """

    def test_assert_valid_status_rejects_unknown_status(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_valid_status({"status": "sideways"})

    def test_assert_valid_status_rejects_missing_status(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_valid_status({})

    def test_assert_valid_status_rejects_non_dict(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_valid_status("not a dict")

    def test_assert_decision_payload_shape_rejects_code_outside_closed_set(self):
        env = {
            "status": "needs_decision",
            "decision": {"code": "MADE_UP", "summary": "s", "context": {}, "options": []},
        }
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_decision_payload_shape(env)

    def test_assert_decision_payload_shape_rejects_missing_summary(self):
        env = {
            "status": "needs_decision",
            "decision": {"code": "AMBIGUOUS", "context": {}, "options": []},
        }
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_decision_payload_shape(env)

    def test_assert_decision_payload_shape_rejects_wrong_typed_context(self):
        env = {
            "status": "needs_decision",
            "decision": {"code": "AMBIGUOUS", "summary": "s", "context": ["not", "a", "dict"], "options": []},
        }
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_decision_payload_shape(env)

    def test_assert_spill_sections_well_formed_rejects_path_mode_missing_path_field(self):
        env = {"body_mode": "path", "body_bytes": 100}
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_spill_sections_well_formed(env, ["body"])

    def test_assert_spill_sections_well_formed_rejects_inline_mode_with_stray_path_field(self):
        env = {"body_mode": "inline", "body_bytes": 2, "body": "hi", "body_path": "/tmp/oops"}
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_spill_sections_well_formed(env, ["body"])

    def test_assert_spill_sections_well_formed_rejects_inline_mode_missing_bare_field(self):
        env = {"body_mode": "inline", "body_bytes": 2}
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_spill_sections_well_formed(env, ["body"])

    def test_assert_spill_sections_well_formed_rejects_path_mode_with_bare_field_present(self):
        env = {
            "body_mode": "path",
            "body_bytes": 2,
            "body_path": "/tmp/x",
            "body": "should not be here",
        }
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_spill_sections_well_formed(env, ["body"])

    def test_assert_spill_sections_well_formed_rejects_invalid_mode_value(self):
        env = {"body_mode": "sideways", "body_bytes": 2}
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_spill_sections_well_formed(env, ["body"])

    def test_assert_spill_sections_well_formed_rejects_negative_bytes(self):
        env = {"body_mode": "inline", "body_bytes": -1, "body": "x"}
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_spill_sections_well_formed(env, ["body"])

    def test_assert_exit_code_contract_rejects_zero_without_envelope(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_exit_code_contract(0, envelope_present=False)

    def test_assert_exit_code_contract_rejects_two_with_envelope(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_exit_code_contract(2, envelope_present=True)

    def test_assert_write_receipt_shape_rejects_non_hex_sha(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_write_receipt_shape({"body_bytes": 5, "body_sha256": "not-hex!!"})

    def test_assert_write_receipt_shape_rejects_wrong_length_sha(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_write_receipt_shape({"body_bytes": 5, "body_sha256": "abc123"})

    def test_assert_write_receipt_shape_rejects_uppercase_sha(self):
        digest_upper = hashing.sha256_hex(b"x").upper()
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_write_receipt_shape({"body_bytes": 1, "body_sha256": digest_upper})

    def test_assert_write_receipt_shape_rejects_negative_body_bytes(self):
        digest = hashing.sha256_hex(b"x")
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_write_receipt_shape({"body_bytes": -1, "body_sha256": digest})

    def test_assert_notices_shape_rejects_non_list(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_notices_shape({"notices": "not-a-list"})

    def test_assert_notices_shape_rejects_missing_key(self):
        with self.assertRaises(AssertionError):
            envelope_asserts.assert_notices_shape({})

    def test_failure_message_includes_caller_supplied_msg_prefix(self):
        try:
            envelope_asserts.assert_valid_status({"status": "bogus"}, msg="my-script fixture")
            self.fail("expected AssertionError")
        except AssertionError as exc:
            self.assertIn("my-script fixture", str(exc))


class ToyScriptEndToEndTests(unittest.TestCase):
    """The toy script (scripts/pipelib_envelope_fixture_demo.py) run as a real subprocess, its
    output validated with the conformance helpers — proving pipelib + the helpers work together
    end to end, not just unit-by-unit (S3 DoD item 4).
    """

    def _run_toy(self, args, env=None):
        return subprocess.run(
            [sys.executable, str(TOY_SCRIPT)] + args,
            env=env if env is not None else dict(os.environ),
            capture_output=True,
            encoding="utf-8",
            check=False,
        )

    def test_toy_script_exists_and_is_executable(self):
        self.assertTrue(TOY_SCRIPT.is_file())
        self.assertTrue(os.access(TOY_SCRIPT, os.X_OK))

    def test_toy_script_ok_mode_emits_conformant_envelope_with_a_spilled_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_toy(["--mode", "ok", "--scratch", tmp])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(len(lines), 1, "must emit exactly one JSON line")
            env = json.loads(lines[0])
            envelope_asserts.assert_full_envelope_conformance(
                env, spill_section_names=["toy_section", "toy_inline_section"]
            )
            envelope_asserts.assert_exit_code_contract(result.returncode, envelope_present=True)
            self.assertEqual(env["toy_section_mode"], "path")
            self.assertEqual(env["toy_inline_section_mode"], "inline")
            self.assertTrue(Path(env["toy_section_path"]).is_file())

    def test_toy_script_needs_decision_mode_emits_conformant_envelope(self):
        result = self._run_toy(["--mode", "needs-decision"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = json.loads(result.stdout.splitlines()[0])
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "needs_decision")
        self.assertEqual(env["decision"]["code"], "AMBIGUOUS")

    def test_toy_script_gh_auth_check_mode_surfaces_auth_required_under_the_fixture(self):
        env_vars = shimenv.intercepted_env(
            base_env=os.environ, fixture_case="pipelib_gh_auth_required"
        )
        result = self._run_toy(["--mode", "gh-auth-check"], env=env_vars)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = json.loads(result.stdout.splitlines()[0])
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "needs_decision")
        self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")

    def test_toy_script_gh_auth_check_mode_emits_ok_when_gh_succeeds(self):
        env_vars = shimenv.intercepted_env(base_env=os.environ, fixture_case="pipelib_gh_auth_ok")
        result = self._run_toy(["--mode", "gh-auth-check"], env=env_vars)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = json.loads(result.stdout.splitlines()[0])
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "ok")

    def test_toy_script_ok_mode_requires_scratch_arg(self):
        result = self._run_toy(["--mode", "ok"])
        self.assertNotEqual(result.returncode, 0)


class BodySizeTests(unittest.TestCase):
    """`limits.body_size` — the one size shape `parse.py phases` and `plan_shipped.py check` both
    emit, so two readers of one staged body cannot disagree with each other or with the write gate."""

    def test_a_body_at_the_limit_still_writes(self):
        # `>`, matching gh_persist's gate: a body exactly AT the limit posts.
        self.assertEqual(
            limits.body_size("x" * limits.BODY_CHAR_LIMIT),
            {
                "chars": limits.BODY_CHAR_LIMIT,
                "limit_chars": limits.BODY_CHAR_LIMIT,
                "headroom_chars": 0,
                "over_limit": False,
            },
        )

    def test_one_character_over_is_over(self):
        size = limits.body_size("x" * (limits.BODY_CHAR_LIMIT + 1))
        self.assertTrue(size["over_limit"])
        self.assertEqual(size["headroom_chars"], -1)

    def test_characters_not_bytes(self):
        # Two UTF-8 bytes each: a byte count would put this body at twice the limit.
        size = limits.body_size("é" * limits.BODY_CHAR_LIMIT)
        self.assertEqual(size["chars"], limits.BODY_CHAR_LIMIT)
        self.assertFalse(size["over_limit"])

    def test_a_crlf_is_two_characters(self):
        # Counted as given — the callers read untranslated, as the gate does.
        self.assertEqual(limits.body_size("a\r\nb")["chars"], 4)


class PipelibIsSkillAgnosticTests(unittest.TestCase):
    """architecture.md §2: pipelib must never contain skill-specific logic. A cheap grep-style
    guard against the obvious regression (a future edit hard-coding issue/PR/plan concepts here).
    """

    def test_no_skill_specific_vocabulary_in_pipelib_source(self):
        # These tokens would indicate actual pipeline-skill logic/data (an issue number field, a
        # PR object, an epic-branch computation, a worktree path variable) creeping into pipelib —
        # as opposed to a decision code's *documentation* naming which script canonically emits it
        # (architecture.md §3's own "Meaning + canonical emitter per code" convention, which this
        # step's brief explicitly asks pipelib.decisions to carry, e.g. "DOD_MALFORMED ... a DoD
        # bullet ... parse.py dod" — a reference to the concept, not DoD-parsing logic itself).
        pipelib_dir = REPO_ROOT / "scripts" / "pipelib"
        banned = ("issue_number", "pull_request", "epic_branch", "worktree_path")
        for py_file in sorted(pipelib_dir.glob("*.py")):
            source = py_file.read_text(encoding="utf-8")
            for token in banned:
                self.assertNotIn(
                    token,
                    source,
                    "%s contains skill-specific token %r (architecture.md §2)" % (py_file, token),
                )


class ModuleFilenamesUseUnderscoresTests(unittest.TestCase):
    """architecture.md §1: "Module filenames use underscores so the test suite can import them.\""""

    def test_all_pipelib_modules_use_underscore_filenames(self):
        pipelib_dir = REPO_ROOT / "scripts" / "pipelib"
        for py_file in pipelib_dir.glob("*.py"):
            self.assertNotIn("-", py_file.name, "%s must not contain a hyphen" % py_file)


if __name__ == "__main__":
    unittest.main()
