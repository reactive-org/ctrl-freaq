"""Unit tests for scripts/gh_persist.py — the ported v1 gh-persist.sh write path (S21), the
single execution path for every issue/PR persist op under the architecture.md §3 envelope.

Covers, per the S21 addendum's DoD rows:

- A conformant envelope for the happy path and every decision/notice each subcommand can emit
  (create, edit-body, link, comment, close, reopen).
- The empty-body gate: an empty or missing staged body file -> EMPTY_BODY_FILE (needs_decision,
  exit 0 — NOT v1's bare exit 2), for every body-bearing subcommand (create/edit-body/comment).
- body_sha256 / body_bytes byte-fidelity receipts on every body-bearing write.
- Native-dependency capability-gating: create retries without deps + DEPS_UNSUPPORTED notice;
  link reports a no-op success + DEPS_UNSUPPORTED notice; a non-deps gh failure surfaces instead
  of being swallowed.
- Post-new-before-delete-old ordering on marker-comment replacement (proved by absence — a failed
  post never reaches a delete call the fixture doesn't provide — and by a successful case).
- close/reopen idempotency (close-on-an-already-closed-issue is a no-op success, matching gh's own
  no-op — no client-side special-casing needed since the shim fixture always returns success).
- --dry-run: prints the would-run command, performs no gh call, no envelope write receipt.
- Usage errors (missing subcommand, link with no relationship flags, comment target/review-action
  mismatches) stay exit 2 with no envelope, per architecture.md §3.

Two fixture strategies, used where each is actually necessary:

1. **Real subprocess + offline shim** (the default, used everywhere the shim's fixture schema can
   express the scenario). Because gh_persist.py's body-bearing subcommands embed a
   **caller-staged file path** in their `gh ... --body-file <path>` argv, and the shim
   (tests/shim/gh, see tests/README.md) matches argv by **exact list equality**, a static
   tests/fixtures/<case>/manifest.json (a fixed path baked in ahead of time) cannot answer for a
   tempfile path that changes every test run. This suite instead builds each fixture's
   manifest.json **dynamically inside a tempfile.TemporaryDirectory()**, in the same directory the
   body is staged to, so the manifest's argv entry references the *exact* path this test run
   actually produces — then points GH_SHIM_FIXTURES at that directory directly (via
   shimenv.intercepted_env's `extra=` kwarg, since shimenv.fixture_case_dir() assumes the static
   tests/fixtures/<name>/ layout a variable-path case cannot use). Subcommands whose argv is fully
   static (close, reopen, most of link) work the same way — one fixture-building helper for the
   whole suite is simpler than mixing static and dynamic set-ups.

2. **In-process scripted `process.run` fake** (`_ScriptedProcessRun`, used only for
   create/link's deps-capability classification). The shim's manifest schema replays **stdout and
   exit_code only** — it has no stderr channel (see tests/shim/gh's own module docstring) — but
   `_is_deps_error`'s classification reads `stderr` TEXT. A real subprocess run through the shim
   therefore cannot exercise "the gh call failed with THIS specific deps-capability stderr
   message," so those tests instead import gh_persist as a module, monkeypatch
   `gh_persist.process.run` with a small fake that returns scripted `CommandResult`s (with
   arbitrary stderr text) and records call order, call the internal `_cmd_create`/`_cmd_link`
   functions directly, and capture their stdout via `contextlib.redirect_stdout`. This is the
   same "test the pure classification/sequencing logic directly" approach test_pipelib.py uses
   for `pipelib.process._is_gh_auth_failure`, applied here because gh_persist's deps-capability
   retry is itself a stderr-driven branch the shim cannot fixture end-to-end.

Run via `python3 tests/run.py` (wires scripts/ and the repo root onto sys.path and the
gh-shim-first PATH before this file is imported).
"""

import contextlib
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pipelib import hashing, process
from tests.support import envelope_asserts, shimenv

# gh_persist is importable because tests/run.py inserts scripts/ onto sys.path before any
# test_*.py is discovered/imported (see tests/README.md) — no per-file sys.path hack needed here,
# matching test_pipelib.py's top-level `from pipelib import ...` style. Needed as a real import
# (not a one-off local import, cf. test_gh_pr_gather.py's PortPreservesV1CliShapeTests) because
# several test classes below repeatedly monkeypatch gh_persist.process.run and call its internal
# _cmd_create/_cmd_link functions directly (see module docstring, strategy 2).
import gh_persist

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gh_persist.py"
_THREAD_COMMENT_IDS = itertools.count(1)


def _write_manifest(fixtures_dir, entries):
    with open(Path(fixtures_dir) / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(entries, fh)


def _write_stdout_file(fixtures_dir, filename, content_bytes):
    path = Path(fixtures_dir) / filename
    with open(path, "wb") as fh:
        fh.write(content_bytes)
    return filename


def _run_script(args, fixtures_dir=None, extra_env=None):
    """Run gh_persist.py as a real subprocess under the intercepted PATH.

    ``fixtures_dir`` points GH_SHIM_FIXTURES directly at an arbitrary directory (built by the
    caller with _write_manifest/_write_stdout_file) rather than the static
    tests/fixtures/<name>/ layout, since this script's argv varies per test run (see module
    docstring). Returns the completed subprocess.
    """
    env = shimenv.intercepted_env(base_env=os.environ, fixture_case=None)
    if fixtures_dir is not None:
        env["GH_SHIM_FIXTURES"] = str(fixtures_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def _parse_envelope(result):
    lines = result.stdout.splitlines()
    assert len(lines) == 1, "expected exactly one stdout line, got %r" % (result.stdout,)
    return json.loads(lines[0])


class ScriptExistsTests(unittest.TestCase):
    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertTrue(os.access(SCRIPT, os.X_OK))


class EmptyBodyGateTests(unittest.TestCase):
    """architecture.md §12 / #626-#627: no body-bearing write without a non-empty staged file.
    v1 exited 2 with a bare stderr line; v2 makes EMPTY_BODY_FILE a proper needs_decision envelope
    at exit 0 (its canonical emitter per architecture.md §3), so this suite asserts the v2 shape,
    not the v1 exit code.
    """

    def test_create_with_missing_body_file_returns_empty_body_file_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = str(Path(tmp) / "does-not-exist.md")
            result = _run_script(
                ["create", "o/r", missing_path, "--title", "T"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")
            self.assertIn(missing_path, env["decision"]["summary"])

    def test_create_with_zero_byte_body_file_returns_empty_body_file_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.md"
            empty_path.write_bytes(b"")
            result = _run_script(
                ["create", "o/r", str(empty_path), "--title", "T"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")
            self.assertEqual(env["decision"]["context"]["reason"], "zero bytes")

    def test_edit_body_with_empty_file_returns_empty_body_file_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.md"
            empty_path.write_bytes(b"")
            result = _run_script(["edit-body", "o/r", "42", str(empty_path)], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")

    def test_comment_with_empty_file_returns_empty_body_file_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.md"
            empty_path.write_bytes(b"")
            result = _run_script(
                ["comment", "o/r", "issue", "42", str(empty_path)], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")

    def test_empty_body_gate_never_calls_gh_at_all(self):
        # No manifest.json exists in this tempdir at all -- if the script called `gh` before the
        # gate short-circuited, the shim would MISS (exit 2, no envelope) and this test would fail
        # with a malformed-envelope error instead of a clean assertion, proving the gate runs
        # strictly before any gh invocation.
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.md"
            empty_path.write_bytes(b"")
            result = _run_script(
                ["create", "o/r", str(empty_path), "--title", "T"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["status"], "needs_decision")


class BodyTooLongGateTests(unittest.TestCase):
    """#38: the same gate as EMPTY_BODY_FILE, at the other end. GitHub rejects any body over 65,536
    CHARACTERS at every endpoint (GraphQL addComment and REST PATCH alike), so without this the
    caller gets a raw `gh` error at exit 1 with no envelope — and, for the planner, gets it at the
    persist step after grounding, the gates, drafting and the whole reviewer loop have been spent.
    """

    # Every op that funnels through _verify_body_file. `comment` covers its three targets via the
    # same call site; `close-pr` only gates when --comment-file is supplied.
    BODY_BEARING_OPS = [
        (["create", "o/r", "{body}", "--title", "T"], "create"),
        (["create-pr", "o/r", "{body}", "--title", "T", "--head", "h", "--base", "b"], "create-pr"),
        (["edit-body", "o/r", "42", "{body}"], "edit-body"),
        (["edit-pr-body", "o/r", "42", "{body}"], "edit-pr-body"),
        (["comment", "o/r", "issue", "42", "{body}"], "comment issue"),
        (["comment", "o/r", "pr", "42", "{body}"], "comment pr"),
        (["comment", "o/r", "pr-review", "42", "{body}", "--review-action", "comment"], "pr-review"),
        (["close-pr", "o/r", "42", "--comment-file", "{body}"], "close-pr"),
    ]

    def _over_cap_body(self, tmp, char_count=70000, char="x"):
        path = Path(tmp) / "big.md"
        path.write_text(char * char_count, encoding="utf-8")
        return path

    def test_gate_fires_on_every_body_bearing_op(self):
        for argv_template, label in self.BODY_BEARING_OPS:
            with self.subTest(op=label):
                with tempfile.TemporaryDirectory() as tmp:
                    body = self._over_cap_body(tmp)
                    argv = [a.replace("{body}", str(body)) for a in argv_template]
                    # No manifest.json in this tempdir at all: any `gh` call would MISS the shim
                    # (exit 2, no envelope) and fail the parse below, which is how this proves the
                    # gate runs strictly before the write.
                    result = _run_script(argv, fixtures_dir=tmp)
                    self.assertEqual(result.returncode, 0, msg=result.stderr)
                    env = _parse_envelope(result)
                    envelope_asserts.assert_full_envelope_conformance(env)
                    self.assertEqual(env["status"], "needs_decision")
                    self.assertEqual(env["decision"]["code"], "BODY_TOO_LONG")

    def test_decision_context_carries_actual_limit_and_overage(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = self._over_cap_body(tmp, char_count=70000)
            result = _run_script(["edit-body", "o/r", "42", str(body)], fixtures_dir=tmp)
            ctx = _parse_envelope(result)["decision"]["context"]
            self.assertEqual(ctx["actual_chars"], 70000)
            self.assertEqual(ctx["limit_chars"], 65536)
            self.assertEqual(ctx["overage_chars"], 70000 - 65536)
            self.assertEqual(ctx["actual_bytes"], 70000)  # single-byte fixture: bytes == chars

    def test_a_multibyte_body_is_measured_in_characters_not_bytes(self):
        # THE test that pins character-not-byte semantics. 65,530 two-byte codepoints is ~131 KB —
        # well over the limit if you measure bytes, comfortably under it in the unit GitHub
        # actually enforces. A byte gate would refuse this legal body and could never detect that
        # it had. Must pass the gate and reach `gh`.
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "multibyte.md"
            body.write_text("é" * 65530, encoding="utf-8")
            self.assertGreater(len(body.read_bytes()), 65536)  # over cap in bytes
            stdout_file = _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/42\n")
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--body-file", str(body)]
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": stdout_file}])
            result = _run_script(["edit-body", "o/r", "42", str(body)], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["status"], "ok", "a legal multibyte body was refused as too long")

    def test_body_exactly_at_the_limit_is_accepted(self):
        # The gate is `> limit`, matching the spill threshold's "at threshold is inline" convention.
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "exact.md"
            body.write_text("x" * 65536, encoding="utf-8")
            stdout_file = _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/42\n")
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--body-file", str(body)]
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": stdout_file}])
            result = _run_script(["edit-body", "o/r", "42", str(body)], fixtures_dir=tmp)
            self.assertEqual(_parse_envelope(result)["status"], "ok")

    def test_gate_precedes_the_dry_run_preview(self):
        # --dry-run must not be a way to get a "would_run" receipt for a body that can never land.
        with tempfile.TemporaryDirectory() as tmp:
            body = self._over_cap_body(tmp)
            result = _run_script(["edit-body", "o/r", "42", str(body), "--dry-run"])
            env = _parse_envelope(result)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "BODY_TOO_LONG")
            self.assertNotIn("would_run", env)

    def test_empty_gate_still_wins_over_the_size_gate(self):
        # Ordering: a missing file is EMPTY_BODY_FILE, not a 0-character BODY_TOO_LONG.
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "nope.md")
            result = _run_script(["edit-body", "o/r", "42", missing], fixtures_dir=tmp)
            self.assertEqual(_parse_envelope(result)["decision"]["code"], "EMPTY_BODY_FILE")


class EditCommentTests(unittest.TestCase):
    """#38: in-place marker update. `gh` has no edit-by-id command, so the executor is a REST PATCH;
    `-F body=@<path>` keeps the body crossing as a path (the #626/#627 discipline). Its value over
    delete-and-repost is a stable comment URL and GitHub's own edit history as the supersession
    record — which is what removes the pressure to keep a history layer inside the body."""

    def _fixture(self, tmp, body_path, comment_id="98765"):
        cmd = [
            "api", "--method", "PATCH",
            "repos/o/r/issues/comments/%s" % comment_id,
            "-F", "body=@%s" % body_path,
            "--jq", ".html_url",
        ]
        stdout_file = _write_stdout_file(
            tmp, "url.txt", b"https://github.com/o/r/issues/42#issuecomment-98765\n"
        )
        _write_manifest(tmp, [{"argv": cmd, "stdout_file": stdout_file}])

    def test_edit_comment_happy_path_emits_write_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "plan.md"
            body_bytes = b"<!-- implementation-plan:v1 -->\nrefreshed\n"
            body_path.write_bytes(body_bytes)
            self._fixture(tmp, body_path)
            result = _run_script(
                ["edit-comment", "o/r", "98765", str(body_path)], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertEqual(env["op"], "edit-comment")
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))

    def test_returned_url_is_the_same_comment(self):
        # The whole point: a revise no longer mints a new comment id, so a URL a prior handoff
        # published still resolves and the issue-body pointer needs no refresh.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "plan.md"
            body_path.write_bytes(b"body\n")
            self._fixture(tmp, body_path)
            result = _run_script(
                ["edit-comment", "o/r", "98765", str(body_path)], fixtures_dir=tmp
            )
            self.assertIn("issuecomment-98765", _parse_envelope(result)["url"])

    def test_body_crosses_as_a_path_not_as_text(self):
        # `-F body=@<path>` is what keeps the staged-path discipline; a `-f body=<text>` form would
        # re-serialize the body across argv and reopen the #626/#627 class of race.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "plan.md"
            body_path.write_bytes(b"body\n")
            result = _run_script(
                ["edit-comment", "o/r", "98765", str(body_path), "--dry-run"]
            )
            would_run = _parse_envelope(result)["would_run"]
            self.assertIn("-F body=@%s" % body_path, would_run)
            self.assertIn("--method PATCH", would_run)

    def test_graphql_node_id_is_a_usage_error_before_any_write(self):
        # Same id-space rule as --delete-marker-id: REST takes the numeric id only, so a node id is
        # a guaranteed 404. Rejected up front, with nothing written.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "plan.md"
            body_path.write_bytes(b"body\n")
            result = _run_script(
                ["edit-comment", "o/r", "IC_kwDOabc123", str(body_path)], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("numeric REST comment id", result.stderr)

    def test_over_cap_body_is_gated_here_too(self):
        # REST enforces the same limit as GraphQL, so edit-comment is not an escape hatch for a body
        # that cannot be written — it is an incentive fix, not a capacity fix.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "plan.md"
            body_path.write_text("x" * 70000, encoding="utf-8")
            result = _run_script(
                ["edit-comment", "o/r", "98765", str(body_path)], fixtures_dir=tmp
            )
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "BODY_TOO_LONG")

    def test_empty_body_is_gated_here_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "plan.md"
            body_path.write_bytes(b"")
            result = _run_script(
                ["edit-comment", "o/r", "98765", str(body_path)], fixtures_dir=tmp
            )
            self.assertEqual(_parse_envelope(result)["decision"]["code"], "EMPTY_BODY_FILE")

    def test_auth_required_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "plan.md"
            body_path.write_bytes(b"body\n")
            cmd = [
                "api", "--method", "PATCH", "repos/o/r/issues/comments/98765",
                "-F", "body=@%s" % body_path, "--jq", ".html_url",
            ]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(
                ["edit-comment", "o/r", "98765", str(body_path)], fixtures_dir=tmp
            )
            self.assertEqual(_parse_envelope(result)["decision"]["code"], "AUTH_REQUIRED")


class CreateHappyPathTests(unittest.TestCase):
    def test_create_without_deps_emits_ok_with_write_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_bytes = b"the new issue body\n"
            body_path.write_bytes(body_bytes)

            cmd = ["issue", "create", "--repo", "o/r", "--title", "New issue",
                   "--body-file", str(body_path)]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/101\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt", "exit_code": 0}])

            result = _run_script(
                ["create", "o/r", str(body_path), "--title", "New issue"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["op"], "create")
            self.assertEqual(env["url"], "https://github.com/o/r/issues/101")
            self.assertEqual(env["body_bytes"], len(body_bytes))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))
            self.assertEqual(env["notices"], [])

    def test_create_with_labels_forwards_each_label_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"body")

            cmd = ["issue", "create", "--repo", "o/r", "--title", "T", "--body-file",
                   str(body_path), "--label", "bug", "--label", "urgent"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/5\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])

            result = _run_script(
                ["create", "o/r", str(body_path), "--title", "T", "--label", "bug",
                 "--label", "urgent"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["status"], "ok")

    def test_create_forwards_explicit_cwd_to_gh_call(self):
        # cwd discipline (S21 brief): every process.run call must take an explicit cwd derived
        # from the port's own arguments, never ambient. We can't observe the shim's cwd directly,
        # but we CAN prove --cwd doesn't break the call and that a bogus cwd surfaces as a real
        # failure (i.e. cwd is actually being passed through, not silently ignored).
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"body")
            cmd = ["issue", "create", "--repo", "o/r", "--title", "T", "--body-file",
                   str(body_path)]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/5\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])

            result = _run_script(
                ["--cwd", tmp, "create", "o/r", str(body_path), "--title", "T"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class CreatePrHappyPathTests(unittest.TestCase):
    """gh_persist.py create-pr (added S10, additive per architecture.md §2) — the resolver's PR-open
    write path. Mirrors CreateHappyPathTests' shape: staged-body-path convention, unconditional
    write receipts, --base/--head explicit and required (no ambient-cwd branch inference), --draft
    optional (the v1 resolver's multi-phase fresh-PR-open path, SKILL.md:896)."""

    def test_create_pr_ready_emits_ok_with_write_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "pr-body.md"
            body_bytes = b"## Doc grounding\ncites architecture.md \xc2\xa73\n"
            body_path.write_bytes(body_bytes)

            cmd = ["pr", "create", "--repo", "o/r", "--title", "Add CSV export (#142)",
                   "--body-file", str(body_path), "--base", "main", "--head", "142-add-csv-export"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/pull/287\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt", "exit_code": 0}])

            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "Add CSV export (#142)",
                 "--base", "main", "--head", "142-add-csv-export"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["op"], "create-pr")
            self.assertEqual(env["url"], "https://github.com/o/r/pull/287")
            self.assertEqual(env["body_bytes"], len(body_bytes))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))
            self.assertEqual(env["notices"], [])

    def test_create_pr_draft_forwards_draft_flag(self):
        # v1 parity: the multi-phase fresh-PR-open path passes --draft (SKILL.md:896); the PR
        # stays draft until every planned phase has shipped, then flips via the sanctioned
        # `gh pr ready` toggle (architecture.md §10) -- unaffected by this op.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "pr-body.md"
            body_path.write_bytes(b"multi-phase body")

            cmd = ["pr", "create", "--repo", "o/r", "--title", "feat(llm): #640 spike harness",
                   "--body-file", str(body_path), "--base", "main", "--head", "640-spike",
                   "--draft"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/pull/649\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])

            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "feat(llm): #640 spike harness",
                 "--base", "main", "--head", "640-spike", "--draft"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["url"], "https://github.com/o/r/pull/649")

    def test_create_pr_story_base_is_the_epic_branch(self):
        # A story PR bases on the parent epic's integration branch, not main -- base/head are
        # explicit facts the caller supplies (no ambient-cwd inference, architecture.md §6).
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "pr-body.md"
            body_path.write_bytes(b"story body")

            cmd = ["pr", "create", "--repo", "o/r", "--title", "Add export service (#151)",
                   "--body-file", str(body_path), "--base", "epic/150-chat-session-ux-polish",
                   "--head", "151-add-export-service"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/pull/155\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])

            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "Add export service (#151)",
                 "--base", "epic/150-chat-session-ux-polish", "--head", "151-add-export-service"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["url"], "https://github.com/o/r/pull/155")

    def test_create_pr_dry_run_previews_command_and_makes_no_live_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "pr-body.md"
            body_bytes = b"preview body"
            body_path.write_bytes(body_bytes)

            # No manifest.json / fixtures_dir -- any live gh call would MISS the shim.
            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "T", "--base", "main",
                 "--head", "my-branch", "--dry-run"]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["op"], "create-pr")
            self.assertTrue(env["dry_run"])
            self.assertIn("would_run", env)
            self.assertNotIn("url", env)
            self.assertEqual(env["body_bytes"], len(body_bytes))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))

    def test_create_pr_dry_run_preview_includes_draft_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "pr-body.md"
            body_path.write_bytes(b"content")
            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "T", "--base", "main",
                 "--head", "my-branch", "--draft", "--dry-run"]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertIn("--draft", env["would_run"])

    def test_create_pr_with_missing_body_file_returns_empty_body_file_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = str(Path(tmp) / "does-not-exist.md")
            result = _run_script(
                ["create-pr", "o/r", missing_path, "--title", "T", "--base", "main",
                 "--head", "my-branch"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")

    def test_create_pr_with_zero_byte_body_file_returns_empty_body_file_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.md"
            empty_path.write_bytes(b"")
            result = _run_script(
                ["create-pr", "o/r", str(empty_path), "--title", "T", "--base", "main",
                 "--head", "my-branch"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")
            self.assertEqual(env["decision"]["context"]["reason"], "zero bytes")

    def test_create_pr_empty_body_gate_never_calls_gh_at_all(self):
        # No manifest.json in this tempdir -- a live gh call would MISS the shim if the gate
        # didn't short-circuit first, proving the empty-body gate runs before any `gh` invocation.
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.md"
            empty_path.write_bytes(b"")
            result = _run_script(
                ["create-pr", "o/r", str(empty_path), "--title", "T", "--base", "main",
                 "--head", "my-branch"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["status"], "needs_decision")

    def test_create_pr_without_title_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--base", "main", "--head", "my-branch"]
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_create_pr_without_base_is_a_usage_error(self):
        # --base is required and explicit -- no ambient-cwd / current-branch inference
        # (architecture.md §6 "No ambient cwd").
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "T", "--head", "my-branch"]
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_create_pr_without_head_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "T", "--base", "main"]
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_create_pr_surfaces_auth_required_as_needs_decision(self):
        # architecture.md §3: AUTH_REQUIRED is "detected by the pipelib runner, any script" --
        # gh's own documented exit code 4 (`gh help exit-codes`) is sufficient to exercise the
        # classification path (see AuthRequiredClassificationTests' matching docstring).
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            cmd = ["pr", "create", "--repo", "o/r", "--title", "T", "--body-file",
                   str(body_path), "--base", "main", "--head", "my-branch"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "T", "--base", "main",
                 "--head", "my-branch"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")

    def test_create_pr_hard_gh_failure_surfaces_nonzero_no_envelope(self):
        # A non-auth, non-zero gh failure propagates as this script's own non-zero exit with
        # stderr forwarded verbatim and no envelope guaranteed (architecture.md §3).
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            cmd = ["pr", "create", "--repo", "o/r", "--title", "T", "--body-file",
                   str(body_path), "--base", "main", "--head", "my-branch"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 1}])
            result = _run_script(
                ["create-pr", "o/r", str(body_path), "--title", "T", "--base", "main",
                 "--head", "my-branch"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")

    def test_create_pr_forwards_explicit_cwd_to_gh_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"body")
            cmd = ["pr", "create", "--repo", "o/r", "--title", "T", "--body-file",
                   str(body_path), "--base", "main", "--head", "my-branch"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/pull/5\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])

            result = _run_script(
                ["--cwd", tmp, "create-pr", "o/r", str(body_path), "--title", "T",
                 "--base", "main", "--head", "my-branch"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class IsDepsErrorUnitTests(unittest.TestCase):
    """Direct unit tests of gh_persist._is_deps_error (the pure stderr-classification predicate),
    imported in-process rather than via subprocess -- mirroring how test_pipelib.py's
    ProcessAuthRequiredClassificationTests tests pipelib.process._is_gh_auth_failure directly.
    This is necessary (not just convenient): the offline shim (tests/shim/gh) has no stderr
    channel in its fixture schema (it replays only stdout_file + exit_code, per its own module
    docstring and tests/README.md), so a real deps-capability failure's stderr TEXT cannot be
    round-tripped through a real subprocess under this harness -- the classification predicate
    itself must be exercised directly to prove it matches/rejects the right strings.
    """

    def test_matches_unknown_flag(self):
        self.assertTrue(gh_persist._is_deps_error("unknown flag: --blocked-by"))

    def test_matches_unknown_json_field(self):
        self.assertTrue(gh_persist._is_deps_error("unknown JSON field: \"blockedBy\""))

    def test_matches_issue_dependencies_feature_off_phrasing(self):
        self.assertTrue(
            gh_persist._is_deps_error("issue dependencies are not enabled for this repository")
        )

    def test_matches_hyphenated_issue_dependencies_phrasing(self):
        self.assertTrue(gh_persist._is_deps_error("issue-dependencies feature disabled"))

    def test_matches_issue_dependencies_with_no_separator(self):
        # v1's regex is `issue[ -]?dependenc` — the separator is OPTIONAL (space, hyphen, or
        # NONE), not just the two spellings that happen to look likely. A substring-marker
        # approximation missing this exact edge case would silently under-match relative to the
        # v1 bash behavior this port must preserve exactly.
        self.assertTrue(gh_persist._is_deps_error("issuedependencies not supported on this plan"))

    def test_match_is_case_insensitive(self):
        self.assertTrue(gh_persist._is_deps_error("UNKNOWN FLAG: --blocking"))

    def test_does_not_match_unrelated_validation_failure(self):
        self.assertFalse(gh_persist._is_deps_error("validation failed: label not found"))

    def test_does_not_match_bare_blocking_in_a_relationship_integrity_error(self):
        # Ported verbatim from v1's is_deps_error rationale: bare "blocking"/"blocked-by" appears
        # in unrelated, MUST-surface API errors (e.g. a relationship-integrity conflict) -- a
        # false-positive match here would silently swallow a real error as "unsupported."
        self.assertFalse(gh_persist._is_deps_error("#5 is already blocking this issue"))

    def test_does_not_match_bare_dependency_word(self):
        self.assertFalse(gh_persist._is_deps_error("circular dependency detected"))

    def test_empty_or_none_stderr_does_not_match(self):
        self.assertFalse(gh_persist._is_deps_error(""))
        self.assertFalse(gh_persist._is_deps_error(None))


class _ScriptedProcessRun:
    """A drop-in replacement for pipelib.process.run used to test gh_persist's in-process
    sequencing logic (retry-without-deps ordering, post-then-delete ordering) without a real
    subprocess or the shim -- needed because the offline shim cannot deliver specific stderr TEXT
    to a subprocess (see IsDepsErrorUnitTests' docstring), but the *sequencing* invariants
    (architecture.md §12: retry-without on a deps failure; post-new-before-delete-old) are
    call-ordering properties this fake can prove directly by recording every call it receives.

    Each call pops the next canned pipelib.process.CommandResult off ``results`` (a list, in
    call order) and appends the argv it was invoked with to ``.calls`` for the test to inspect
    afterward. Raises AssertionError (not silently returning None) if called more times than
    ``results`` has entries -- an over-call is a bug in the sequencing under test, not something
    to paper over.
    """

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __call__(self, argv, cwd=None, env=None, input_text=None, check=False):
        self.calls.append(list(argv))
        if not self._results:
            raise AssertionError(
                "gh_persist called process.run more times than this test scripted; "
                "calls so far: %r" % (self.calls,)
            )
        return self._results.pop(0)


class CreateDepsCapabilityGatingTests(unittest.TestCase):
    """architecture.md §12: "Capability-gated degradation (native deps) with notice" —
    create retries WITHOUT native deps on a deps-capability failure and reports DEPS_UNSUPPORTED
    as a notice (work proceeds; not a needs_decision), ported verbatim from v1's cmd_create.
    Exercised in-process (see _ScriptedProcessRun) since the real classification depends on
    stderr text the offline shim cannot deliver through a real subprocess.
    """

    def setUp(self):
        self._original_run = gh_persist.process.run
        self.addCleanup(setattr, gh_persist.process, "run", self._original_run)

    def _run_create_in_process(self, cli_args, scripted_results):
        fake = _ScriptedProcessRun(scripted_results)
        gh_persist.process.run = fake
        buf = io.StringIO()
        parser, _subparsers_by_name = gh_persist._build_parser()
        args = parser.parse_args(cli_args)
        with contextlib.redirect_stdout(buf):
            exit_code = gh_persist._cmd_create(args, parser)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "expected exactly one JSON line, got %r" % (lines,))
        return exit_code, json.loads(lines[0]), fake.calls

    def test_create_with_blocked_by_retries_without_on_deps_unsupported_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"body")

            with_deps_failure = process.CommandResult(
                returncode=1, stdout="", stderr="unknown flag: --blocked-by\n"
            )
            without_deps_success = process.CommandResult(
                returncode=0, stdout="https://github.com/o/r/issues/12\n", stderr=""
            )
            exit_code, env, calls = self._run_create_in_process(
                ["create", "o/r", str(body_path), "--title", "T", "--blocked-by", "9"],
                [with_deps_failure, without_deps_success],
            )
            self.assertEqual(exit_code, 0)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["url"], "https://github.com/o/r/issues/12")
            self.assertIn("DEPS_UNSUPPORTED", env["notices"])
            # Ordering: the WITH-deps attempt (--blocked-by present) must be call #1, the
            # WITHOUT-deps retry (flag absent) call #2 -- never the reverse, and never omitted
            # (a script that skipped straight to "without deps" would silently never learn
            # whether deps actually work on a capable repo).
            self.assertEqual(len(calls), 2)
            self.assertIn("--blocked-by", calls[0])
            self.assertNotIn("--blocked-by", calls[1])

    def test_create_with_blocked_by_surfaces_non_deps_failure_without_retry(self):
        # A non-deps failure (e.g. bad label, auth-unrelated error) must surface, NOT be masked as
        # a deps-capability miss, and must NOT retry (a retry here would risk double-filing on a
        # gh version where the first call's failure wasn't actually atomic).
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"body")
            non_deps_failure = process.CommandResult(
                returncode=1, stdout="", stderr="validation failed: label not found\n"
            )
            fake = _ScriptedProcessRun([non_deps_failure])
            gh_persist.process.run = fake
            buf_out, buf_err = io.StringIO(), io.StringIO()
            parser, _subparsers_by_name = gh_persist._build_parser()
            args = parser.parse_args(
                ["create", "o/r", str(body_path), "--title", "T", "--blocked-by", "9"]
            )
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                exit_code = gh_persist._cmd_create(args, parser)
            self.assertEqual(exit_code, 1)
            self.assertEqual(buf_out.getvalue(), "")
            self.assertIn("validation failed", buf_err.getvalue())
            # No retry attempted -- exactly one process.run call.
            self.assertEqual(len(fake.calls), 1)


class CreateParentSubIssueTests(CreateDepsCapabilityGatingTests):
    """`create --parent` establishes GitHub's native parent/sub-issue relation in the filing
    round-trip (skills/_shared/epic-story-hierarchy.md), capability-gated independently of native
    dependencies. Reuses the in-process scripted-run harness for the same reason: the
    classification reads stderr text the offline shim can't deliver.
    """

    def test_create_with_parent_passes_the_flag_through_on_the_first_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"story body")

            success = process.CommandResult(
                returncode=0, stdout="https://github.com/o/r/issues/96\n", stderr=""
            )
            exit_code, env, calls = self._run_create_in_process(
                ["create", "o/r", str(body_path), "--title", "S", "--parent", "95"],
                [success],
            )
            self.assertEqual(exit_code, 0)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["url"], "https://github.com/o/r/issues/96")
            # One call, relation included, no degradation notice.
            self.assertEqual(len(calls), 1)
            self.assertIn("--parent", calls[0])
            self.assertEqual(calls[0][calls[0].index("--parent") + 1], "95")
            self.assertEqual(env["notices"], [])

    def test_create_retries_without_parent_on_subissues_unsupported(self):
        """The story still files; only the relation is dropped, reported as SUBISSUES_UNSUPPORTED —
        never DEPS_UNSUPPORTED, which would send a reader to the wrong fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"story body")

            with_parent_failure = process.CommandResult(
                returncode=1, stdout="", stderr="unknown flag: --parent\n"
            )
            without_parent_success = process.CommandResult(
                returncode=0, stdout="https://github.com/o/r/issues/96\n", stderr=""
            )
            exit_code, env, calls = self._run_create_in_process(
                ["create", "o/r", str(body_path), "--title", "S", "--parent", "95"],
                [with_parent_failure, without_parent_success],
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["url"], "https://github.com/o/r/issues/96")
            self.assertIn("SUBISSUES_UNSUPPORTED", env["notices"])
            self.assertNotIn("DEPS_UNSUPPORTED", env["notices"])
            self.assertEqual(len(calls), 2)
            self.assertIn("--parent", calls[0])
            self.assertNotIn("--parent", calls[1])

    def test_ladder_keeps_deps_when_only_the_parent_relation_is_unsupported(self):
        """Both relations requested, only sub-issues missing: rung 2 (deps kept, parent dropped)
        succeeds, so the dependency survives and exactly one notice is reported."""
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"story body")

            both_failure = process.CommandResult(
                returncode=1, stdout="", stderr="unknown flag: --parent\n"
            )
            deps_only_success = process.CommandResult(
                returncode=0, stdout="https://github.com/o/r/issues/96\n", stderr=""
            )
            exit_code, env, calls = self._run_create_in_process(
                [
                    "create", "o/r", str(body_path), "--title", "S",
                    "--blocked-by", "9", "--parent", "95",
                ],
                [both_failure, deps_only_success],
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(env["notices"], ["SUBISSUES_UNSUPPORTED"])
            self.assertEqual(len(calls), 2)
            self.assertIn("--blocked-by", calls[1])
            self.assertNotIn("--parent", calls[1])

    def test_ladder_drops_both_relations_and_reports_both_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"story body")

            failure = process.CommandResult(
                returncode=1, stdout="", stderr="unknown flag\n"
            )
            bare_success = process.CommandResult(
                returncode=0, stdout="https://github.com/o/r/issues/96\n", stderr=""
            )
            exit_code, env, calls = self._run_create_in_process(
                [
                    "create", "o/r", str(body_path), "--title", "S",
                    "--blocked-by", "9", "--parent", "95",
                ],
                [failure, failure, failure, bare_success],
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(env["notices"], ["DEPS_UNSUPPORTED", "SUBISSUES_UNSUPPORTED"])
            self.assertEqual(len(calls), 4)
            self.assertNotIn("--blocked-by", calls[3])
            self.assertNotIn("--parent", calls[3])

    def test_non_capability_failure_with_parent_surfaces_without_retry(self):
        """A real error (bad parent number, permissions) must surface, not be masked as a
        capability miss and silently filed without the relation."""
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"story body")

            fake = _ScriptedProcessRun(
                [
                    process.CommandResult(
                        returncode=1,
                        stdout="",
                        stderr="could not resolve to an Issue with the number 99999\n",
                    )
                ]
            )
            gh_persist.process.run = fake
            parser, _ = gh_persist._build_parser()
            args = parser.parse_args(
                ["create", "o/r", str(body_path), "--title", "S", "--parent", "99999"]
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exit_code = gh_persist._cmd_create(args, parser)
            self.assertEqual(exit_code, 1)
            self.assertEqual(buf.getvalue(), "")
            self.assertEqual(len(fake.calls), 1)

    def test_error_merely_mentioning_a_parent_is_not_a_capability_miss(self):
        """A relationship-integrity failure ("the parent issue is closed") must SURFACE. Matching a
        bare "parent" as a capability signature would file the story unparented and report a benign
        SUBISSUES_UNSUPPORTED, so the caller never learns its real parenting attempt failed — the
        same silent-swallow class `_DEPS_ERROR_PATTERN` refuses to match bare "blocking" for.
        """
        self.assertFalse(gh_persist._is_subissues_error("the parent issue is closed"))
        self.assertFalse(gh_persist._is_subissues_error("cannot add a parent to itself"))
        # The genuine capability signatures still classify.
        self.assertTrue(gh_persist._is_subissues_error("unknown flag: --parent"))
        self.assertTrue(gh_persist._is_subissues_error('unknown JSON field: "subIssues"'))
        self.assertTrue(gh_persist._is_subissues_error("sub-issues are not available"))

    def test_parenting_integrity_failure_surfaces_instead_of_degrading(self):
        """The same rule proven through the real create path: one call, hard exit, no envelope."""
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"story body")
            fake = _ScriptedProcessRun(
                [
                    process.CommandResult(
                        returncode=1, stdout="", stderr="the parent issue is closed\n"
                    )
                ]
            )
            gh_persist.process.run = fake
            parser, _ = gh_persist._build_parser()
            args = parser.parse_args(
                ["create", "o/r", str(body_path), "--title", "S", "--parent", "95"]
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exit_code = gh_persist._cmd_create(args, parser)
            self.assertEqual(exit_code, 1)
            self.assertEqual(buf.getvalue(), "")
            self.assertEqual(len(fake.calls), 1)

    def test_dry_run_preview_includes_the_parent_flag_and_never_calls_gh(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"story body")
            fake = _ScriptedProcessRun([])
            gh_persist.process.run = fake
            parser, _ = gh_persist._build_parser()
            args = parser.parse_args(
                ["create", "o/r", str(body_path), "--title", "S", "--parent", "95", "--dry-run"]
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exit_code = gh_persist._cmd_create(args, parser)
            self.assertEqual(exit_code, 0)
            env = json.loads(buf.getvalue().splitlines()[0])
            self.assertIn("--parent 95", env["would_run"])
            self.assertEqual(fake.calls, [])


class LinkTests(unittest.TestCase):
    def test_link_with_no_flags_is_a_usage_error(self):
        result = _run_script(["link", "o/r", "42"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_link_happy_path_emits_ok_with_changed_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--add-blocked-by", "9"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/42\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(
                ["link", "o/r", "42", "--add-blocked-by", "9"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["op"], "link")
            self.assertTrue(env["changed"])
            self.assertFalse(env["deps_unsupported"])
            self.assertEqual(env["notices"], [])

    def test_link_surfaces_non_deps_failure(self):
        # A non-deps gh failure (e.g. "could not resolve to an Issue") has no stderr-text
        # dependency in the assertion -- the shim's exit_code alone drives this branch (any
        # failure _is_deps_error doesn't recognize surfaces as a hard failure), so this stays a
        # real end-to-end subprocess test unlike the two deps-classification tests below.
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--add-blocked-by", "9"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 1}])
            result = _run_script(
                ["link", "o/r", "42", "--add-blocked-by", "9"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")

    def test_link_reports_no_op_success_with_deps_unsupported_notice(self):
        # In-process (see _ScriptedProcessRun / IsDepsErrorUnitTests docstrings): the real
        # deps-unsupported classification depends on stderr TEXT the offline shim's fixture
        # schema cannot deliver through a real subprocess (stdout_file + exit_code only).
        deps_failure = process.CommandResult(
            returncode=1, stdout="", stderr="issue dependencies are not enabled for this repo\n"
        )
        fake = _ScriptedProcessRun([deps_failure])
        original_run = gh_persist.process.run
        gh_persist.process.run = fake
        try:
            buf = io.StringIO()
            parser, subparsers_by_name = gh_persist._build_parser()
            args = parser.parse_args(["link", "o/r", "42", "--add-blocked-by", "9"])
            with contextlib.redirect_stdout(buf):
                exit_code = gh_persist._cmd_link(args, subparsers_by_name["link"])
            env = json.loads(buf.getvalue().splitlines()[0])
        finally:
            gh_persist.process.run = original_run

        self.assertEqual(exit_code, 0)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "ok")
        self.assertFalse(env["changed"])
        self.assertTrue(env["deps_unsupported"])
        self.assertIn("DEPS_UNSUPPORTED", env["notices"])
        self.assertNotIn("url", env)
        # link has no without-deps retry (relationship-only; "without deps" would be an empty
        # edit) -- exactly one process.run call, unlike create's two-call retry sequence.
        self.assertEqual(len(fake.calls), 1)

    def test_link_dry_run_performs_no_write(self):
        result = _run_script(
            ["link", "o/r", "42", "--add-blocked-by", "9", "--dry-run"],
            fixtures_dir=None,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_envelope(result)
        self.assertTrue(env["dry_run"])
        self.assertIn("would_run", env)
        self.assertNotIn("url", env)

    def test_link_forwards_multiple_repeated_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--add-blocked-by", "9",
                   "--remove-blocking", "3", "--add-blocking", "7"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/42\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(
                ["link", "o/r", "42", "--add-blocked-by", "9", "--remove-blocking", "3",
                 "--add-blocking", "7"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class AddParentTests(unittest.TestCase):
    """`add-parent` is the one AFTER-THE-FACT parenting op (#16): it adopts an already-filed issue
    as a parent's sub-issue, which `create --parent` cannot do. Bodyless and one-child-per-call, so
    sequential calls fix the epic's sub-issue panel order. Same attempt-and-classify capability gate
    as `create --parent`, but with `link`'s degradation shape (no lower rung to descend to — the
    relation IS the whole edit), which is why the assertions below mirror LinkTests rather than
    CreateParentSubIssueTests' ladder.
    """

    def test_self_parenting_is_a_usage_error(self):
        result = _run_script(["add-parent", "o/r", "42", "42"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_missing_parent_argument_is_a_usage_error(self):
        result = _run_script(["add-parent", "o/r", "42"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_happy_path_edits_the_child_and_reports_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The child is edited, not the parent: GitHub's relation is set from the child's side,
            # and this argv is the contract a fixture-replaying caller matches exactly.
            cmd = ["issue", "edit", "151", "--repo", "o/r", "--parent", "150"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/151\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(["add-parent", "o/r", "151", "150"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["op"], "add-parent")
            self.assertEqual(env["issue"], "151")
            self.assertEqual(env["parent"], "150")
            self.assertTrue(env["changed"])
            self.assertFalse(env["subissues_unsupported"])
            self.assertEqual(env["notices"], [])
            # Bodyless: no write receipt, unlike every body-bearing op.
            self.assertNotIn("body_sha256", env)

    def test_accepts_a_url_as_the_parent(self):
        # --parent takes a number OR a url (gh resolves it); the script passes it through opaquely,
        # exactly as create --parent does — no client-side number parsing to get wrong.
        with tempfile.TemporaryDirectory() as tmp:
            parent_url = "https://github.com/o/r/issues/150"
            cmd = ["issue", "edit", "151", "--repo", "o/r", "--parent", parent_url]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/151\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(["add-parent", "o/r", "151", parent_url], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(_parse_envelope(result)["parent"], parent_url)

    def test_surfaces_a_non_capability_failure(self):
        # Any failure _is_subissues_error doesn't recognize surfaces hard, so the shim's exit_code
        # alone drives this branch — no stderr text needed, keeping it a real subprocess test.
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "151", "--repo", "o/r", "--parent", "150"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 1}])
            result = _run_script(["add-parent", "o/r", "151", "150"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")

    def test_reports_no_op_success_with_subissues_unsupported_notice(self):
        # In-process (see _ScriptedProcessRun): the capability classification reads stderr TEXT the
        # offline shim cannot deliver through a real subprocess.
        subissues_failure = process.CommandResult(
            returncode=1, stdout="", stderr="unknown flag: --parent\n"
        )
        exit_code, env, calls = self._run_add_parent_in_process(
            ["add-parent", "o/r", "151", "150"], [subissues_failure]
        )
        self.assertEqual(exit_code, 0)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertEqual(env["status"], "ok")
        self.assertFalse(env["changed"])
        self.assertTrue(env["subissues_unsupported"])
        # Exact, not `in`: emitting DEPS_UNSUPPORTED as well would send the reader to the prose
        # dependency-linking fallback instead of the legacy `## Stories` checklist — the confusion the
        # two codes are kept separate to prevent.
        self.assertEqual(env["notices"], ["SUBISSUES_UNSUPPORTED"])
        self.assertNotIn("url", env)
        # No retry: dropping the relation would leave an empty edit, so there is nothing to descend
        # to -- exactly one call, matching link's deps degradation rather than create's ladder.
        self.assertEqual(len(calls), 1)

    def test_parenting_integrity_failure_surfaces_instead_of_degrading(self):
        # The load-bearing half of _is_subissues_error's "err toward SURFACING" rule: an error that
        # merely MENTIONS a parent is a real integrity failure, not a capability miss. Degrading it
        # to a benign notice would tell the caller the adoption was skipped for host reasons when in
        # fact its real parenting attempt failed.
        for stderr_text in (
            # Messages that do NOT name the relation — the easy half.
            "the parent issue is closed\n",
            "cannot add a parent to itself\n",
            "could not resolve to an Issue with the number 99999\n",
            "HTTP 403: You must have write access to the repository\n",
            "API rate limit exceeded\n",
            # The load-bearing half: every one of these NAMES the relation, so the bare-noun
            # alternative in _SUBISSUES_ERROR_PATTERN matches them. They are integrity, quota and
            # permission failures, not a missing capability, and degrading any of them tells the
            # caller the host doesn't serve sub-issues when in fact its real write failed.
            "Unprocessable Entity: Sub-issue is already parented\n",
            "this issue is already a sub-issue of #5\n",
            "You have exceeded the maximum number of sub-issues (100)\n",
            "sub-issues cannot be nested more than 8 levels deep\n",
            "A sub-issue must belong to the same repository as its parent\n",
            "Sub-issue cannot be its own parent\n",
            # gh appends the failing GraphQL path to API errors, and `--parent` is implemented via
            # the addSubIssue mutation — so a plain 403 arrives carrying the relation's name.
            "GraphQL: Resource not accessible by personal access token (addSubIssue)\n",
        ):
            with self.subTest(stderr=stderr_text):
                integrity_failure = process.CommandResult(
                    returncode=1, stdout="", stderr=stderr_text
                )
                fake = _ScriptedProcessRun([integrity_failure])
                original_run = gh_persist.process.run
                gh_persist.process.run = fake
                try:
                    buf_out, buf_err = io.StringIO(), io.StringIO()
                    parser, subparsers_by_name = gh_persist._build_parser()
                    args = parser.parse_args(["add-parent", "o/r", "151", "150"])
                    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                        exit_code = gh_persist._cmd_add_parent(
                            args, subparsers_by_name["add-parent"]
                        )
                finally:
                    gh_persist.process.run = original_run
                self.assertEqual(exit_code, 1)
                self.assertEqual(buf_out.getvalue(), "")
                self.assertIn(stderr_text.strip(), buf_err.getvalue())

    def test_genuine_capability_misses_still_degrade(self):
        """The other side of the deny list: it must not over-reach and turn a real capability miss into
        a hard failure, which would break `add-parent` on every host that genuinely lacks the flag."""
        for stderr_text in (
            "unknown flag: --parent\n",
            'unknown JSON field: "subIssues"\n',
            "sub-issues are not available on this host\n",
            "sub-issue support is disabled for this repository\n",
        ):
            with self.subTest(stderr=stderr_text):
                self.assertTrue(gh_persist._is_subissues_error(stderr_text), stderr_text)

    def test_self_parenting_is_caught_through_every_equivalent_spelling(self):
        """gh accepts a bare number and a URL interchangeably, so a raw string compare let `42` +
        `#42` (or a URL for the same issue) reach gh, leaving gh's own rejection text as the only
        backstop — and that text names the relation, so it used to be degraded."""
        for child, parent in (
            ("42", "#42"),
            ("#42", "42"),
            ("42", " 42 "),
            ("42", "https://github.com/o/r/issues/42"),
            ("https://github.com/o/r/issues/42", "42"),
            ("42", "https://github.com/o/r/issues/42/"),
        ):
            with self.subTest(child=child, parent=parent):
                result = _run_script(["add-parent", "o/r", child, parent])
                self.assertEqual(result.returncode, 2, "%r vs %r must be a usage error" % (child, parent))
                self.assertEqual(result.stdout, "")

    def test_distinct_issues_are_not_confused_by_the_normalizer(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "151", "--repo", "o/r", "--parent", "#150"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/151\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(["add-parent", "o/r", "151", "#150"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_surfaces_auth_required_as_needs_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "151", "--repo", "o/r", "--parent", "150"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(["add-parent", "o/r", "151", "150"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")

    def test_dry_run_previews_the_parent_flag_and_never_calls_gh(self):
        fake = _ScriptedProcessRun([])
        original_run = gh_persist.process.run
        gh_persist.process.run = fake
        try:
            buf = io.StringIO()
            parser, subparsers_by_name = gh_persist._build_parser()
            args = parser.parse_args(["add-parent", "o/r", "151", "150", "--dry-run"])
            with contextlib.redirect_stdout(buf):
                exit_code = gh_persist._cmd_add_parent(args, subparsers_by_name["add-parent"])
            env = json.loads(buf.getvalue().splitlines()[0])
        finally:
            gh_persist.process.run = original_run
        self.assertEqual(exit_code, 0)
        self.assertTrue(env["dry_run"])
        self.assertIn("--parent 150", env["would_run"])
        self.assertNotIn("url", env)
        # No outcome claim on a run that wrote nothing: `cut.md` documents that the caller reads
        # `changed`, so `changed: true` here would record every rehearsed adoption as applied.
        self.assertNotIn("changed", env)
        self.assertNotIn("subissues_unsupported", env)
        self.assertEqual(fake.calls, [])

    def _run_add_parent_in_process(self, cli_args, scripted_results):
        fake = _ScriptedProcessRun(scripted_results)
        original_run = gh_persist.process.run
        gh_persist.process.run = fake
        try:
            buf = io.StringIO()
            parser, subparsers_by_name = gh_persist._build_parser()
            args = parser.parse_args(cli_args)
            with contextlib.redirect_stdout(buf):
                exit_code = gh_persist._cmd_add_parent(args, subparsers_by_name["add-parent"])
            lines = buf.getvalue().splitlines()
            self.assertEqual(len(lines), 1, "expected exactly one JSON line, got %r" % (lines,))
            return exit_code, json.loads(lines[0]), fake.calls
        finally:
            gh_persist.process.run = original_run


class CommentPostThenDeleteOrderingTests(unittest.TestCase):
    """architecture.md §12: "Post-new-before-delete-old on marker replacement" — the new comment
    call must be issued (and succeed) BEFORE the delete call. Verified two ways: (1) the delete
    call is fixtured to only exist as a valid entry, so if the script tried to delete before
    posting, the shim wouldn't have the *post* comment yet -- but since the shim is stateless per
    call, the real proof is (2) an explicit call-order log the shim's argv approach can't natively
    provide, so this test uses a `--log-calls` side-channel file the manifest entries append their
    own argv to via a wrapper... instead, simplest and most direct: assert that when the POST
    fails, the DELETE call is never attempted at all (no manifest entry for it, so if the script's
    code tried to call it, the whole subprocess would MISS() the shim and produce garbage output
    the JSON parse would reject) -- proving the ordering the invariant relies on: a failed post
    never reaches the delete step.
    """

    def test_successful_post_then_delete_marker_both_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"new marker comment body")
            post_cmd = ["issue", "comment", "42", "--repo", "o/r", "--body-file", str(body_path)]
            delete_cmd = ["api", "-X", "DELETE", "repos/o/r/issues/comments/555"]
            _write_stdout_file(tmp, "post_url.txt", b"https://github.com/o/r/issues/42#comment-1\n")
            _write_manifest(
                tmp,
                [
                    {"argv": post_cmd, "stdout_file": "post_url.txt", "exit_code": 0},
                    {"argv": delete_cmd, "exit_code": 0},
                ],
            )
            result = _run_script(
                ["comment", "o/r", "issue", "42", str(body_path), "--delete-marker-id", "555"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertEqual(env["url"], "https://github.com/o/r/issues/42#comment-1")
            self.assertEqual(env["notices"], [])

    def test_a_node_id_delete_marker_is_rejected_up_front_before_any_post(self):
        # #34: the delete path is REST (`repos/{owner}/{repo}/issues/comments/{id}`), so a GraphQL
        # node id is a GUARANTEED 404, not the transient failure the best-effort WARN exists for.
        # Rejecting before the post means the caller gets an actionable usage error with nothing
        # written -- rather than a posted comment plus an undeletable stale duplicate. The manifest
        # holds no entry for the post at all: if validation moved after it, the shim would fail on
        # an unmatched command instead of returning the usage error asserted here.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"new marker comment body")
            _write_manifest(tmp, [])
            result = _run_script(
                [
                    "comment", "o/r", "issue", "42", str(body_path),
                    "--delete-marker-id", "IC_kwDOS85sFs8AAAABPuoijg",
                ],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("numeric REST comment id", result.stderr)

    def test_a_failed_post_never_attempts_the_delete_call(self):
        # No manifest entry exists for the DELETE call at all. If the script's ordering were
        # reversed (delete before post, or delete attempted regardless of post outcome), the
        # subprocess would either issue an un-fixtured call (shim MISS, exit 2, malformed
        # envelope) or -- worse -- silently delete the prior marker before confirming the new one
        # landed. This proves the post-before-delete invariant by absence: the script must return
        # from the post's failure branch before ever reaching the delete code path.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"new marker comment body")
            post_cmd = ["issue", "comment", "42", "--repo", "o/r", "--body-file", str(body_path)]
            # The shim only replays stdout (tests/shim/gh has no stderr channel); the failure
            # branch under test (_cmd_comment's plain "gh call failed -> exit 1") never inspects
            # stderr *content* to decide anything (unlike create/link's deps classification), so
            # an empty-stderr failure fixture exercises the exact same code path a real stderr
            # message would.
            _write_manifest(tmp, [{"argv": post_cmd, "exit_code": 1}])

            result = _run_script(
                ["comment", "o/r", "issue", "42", str(body_path), "--delete-marker-id", "555"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")

    def test_delete_failure_after_successful_post_is_best_effort_notice_not_a_failure(self):
        # The post already landed -- a delete failure must not fail the whole op or lose the
        # write receipt; it surfaces as a notice (v1's WARN line), and the overall status stays ok.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"new marker comment body")
            post_cmd = ["issue", "comment", "42", "--repo", "o/r", "--body-file", str(body_path)]
            delete_cmd = ["api", "-X", "DELETE", "repos/o/r/issues/comments/555"]
            _write_stdout_file(tmp, "post_url.txt", b"https://github.com/o/r/issues/42#comment-2\n")
            # Again, the delete-failure branch only checks delete_result.returncode != 0 -- no
            # stderr-content inspection -- so an empty-stderr failure exercises the same path.
            _write_manifest(
                tmp,
                [
                    {"argv": post_cmd, "stdout_file": "post_url.txt", "exit_code": 0},
                    {"argv": delete_cmd, "exit_code": 1},
                ],
            )
            result = _run_script(
                ["comment", "o/r", "issue", "42", str(body_path), "--delete-marker-id", "555"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "ok")
            self.assertEqual(env["url"], "https://github.com/o/r/issues/42#comment-2")
            self.assertTrue(
                any("could not delete" in n for n in env["notices"]),
                "expected a best-effort delete-failure notice, got %r" % (env["notices"],),
            )


class CommentPostThenDeleteExplicitCallOrderTests(unittest.TestCase):
    """architecture.md §12: "Post-new-before-delete-old on marker replacement" — strengthens
    CommentPostThenDeleteOrderingTests' proof-by-absence with an EXPLICIT positive call-ordering
    assertion: the recorded gh calls list has the new-comment POST at ``calls[0]`` and the DELETE
    at ``calls[1]``, never the reverse. Uses the same in-process _ScriptedProcessRun technique
    CreateDepsCapabilityGatingTests uses to prove create's with-deps-then-without-deps retry
    ordering (calls[0] has --blocked-by, calls[1] doesn't) — applied here to gh_persist's OTHER
    call-ordering invariant. Proof-by-absence (the tests above) shows a failed post never reaches
    the delete step; this test additionally shows that on the SUCCESS path the two calls this
    script issues are recorded in the exact order the invariant requires, not merely "the delete
    fixture happened to exist" -- a call-order log the shim's per-call statelessness can't natively
    provide, which is exactly why _ScriptedProcessRun (recording every call as it happens) is used
    instead of a second real-subprocess assertion.
    """

    def test_post_call_precedes_delete_call_in_recorded_call_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"new marker comment body")

            post_success = process.CommandResult(
                returncode=0,
                stdout="https://github.com/o/r/issues/42#comment-1\n",
                stderr="",
            )
            delete_success = process.CommandResult(returncode=0, stdout="", stderr="")
            fake = _ScriptedProcessRun([post_success, delete_success])
            original_run = gh_persist.process.run
            gh_persist.process.run = fake
            try:
                buf = io.StringIO()
                parser, subparsers_by_name = gh_persist._build_parser()
                args = parser.parse_args(
                    [
                        "comment", "o/r", "issue", "42", str(body_path),
                        "--delete-marker-id", "555",
                    ]
                )
                with contextlib.redirect_stdout(buf):
                    exit_code = gh_persist._cmd_comment(args, subparsers_by_name["comment"])
                env = json.loads(buf.getvalue().splitlines()[0])
            finally:
                gh_persist.process.run = original_run

            self.assertEqual(exit_code, 0)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertEqual(env["url"], "https://github.com/o/r/issues/42#comment-1")

            # The explicit positive ordering assertion: exactly two calls were made, the FIRST
            # (calls[0]) is the new-comment POST (`gh issue comment ...`), the SECOND (calls[1]) is
            # the DELETE of the old marker (`gh api -X DELETE .../comments/<id>`) — post precedes
            # delete, proven by the recorded call log itself, not by the delete fixture's mere
            # presence/absence. Each recorded call is the full argv gh_persist passed to
            # process.run, including the leading "gh" token (unlike the shim's manifest matching,
            # which is keyed on argv with that token already stripped).
            self.assertEqual(len(fake.calls), 2)
            self.assertEqual(fake.calls[0][:3], ["gh", "issue", "comment"])
            self.assertIn("--body-file", fake.calls[0])
            self.assertEqual(fake.calls[1][:4], ["gh", "api", "-X", "DELETE"])
            self.assertIn("repos/o/r/issues/comments/555", fake.calls[1])


class CommentTargetValidationTests(unittest.TestCase):
    def test_review_action_forbidden_for_issue_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"x")
            result = _run_script(
                ["comment", "o/r", "issue", "1", str(body_path), "--review-action", "approve"]
            )
            self.assertEqual(result.returncode, 2)

    def test_review_action_forbidden_for_pr_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"x")
            result = _run_script(
                ["comment", "o/r", "pr", "1", str(body_path), "--review-action", "comment"]
            )
            self.assertEqual(result.returncode, 2)

    def test_review_action_required_for_pr_review_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"x")
            result = _run_script(["comment", "o/r", "pr-review", "1", str(body_path)])
            self.assertEqual(result.returncode, 2)

    def test_pr_review_comment_happy_path_uses_review_action_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"LGTM")
            cmd = ["pr", "review", "9", "--repo", "o/r", "--approve", "--body-file",
                   str(body_path)]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/pull/9#pullrequestreview-1\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(
                ["comment", "o/r", "pr-review", "9", str(body_path), "--review-action", "approve"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)

    def test_pr_target_comment_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"a pr comment")
            cmd = ["pr", "comment", "9", "--repo", "o/r", "--body-file", str(body_path)]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/pull/9#issuecomment-1\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(
                ["comment", "o/r", "pr", "9", str(body_path)], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class CloseReopenIdempotencyTests(unittest.TestCase):
    """architecture.md §7: "close on a closed issue is a no-op"; bodyless, no empty-body gate."""

    def test_close_happy_path_emits_ok_no_write_receipt_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "close", "42", "--repo", "o/r"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            result = _run_script(["close", "o/r", "42"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["op"], "close")
            self.assertTrue(env["closed"])
            self.assertNotIn("body_bytes", env)
            self.assertNotIn("body_sha256", env)

    def test_close_with_reason_forwards_the_reason_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "close", "42", "--repo", "o/r", "--reason", "not planned"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            result = _run_script(
                ["close", "o/r", "42", "--reason", "not planned"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_close_on_an_already_closed_issue_is_still_a_no_op_success(self):
        # gh itself treats `gh issue close` on an already-closed issue as a no-op success (exit 0)
        # -- this script performs no client-side special-casing, so the fixture simply models gh's
        # own no-op behavior and this test proves the script doesn't add an error path on top of
        # it (i.e. it doesn't try to be "smarter" than gh and reject a reentrant close).
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "close", "7", "--repo", "o/r"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            first = _run_script(["close", "o/r", "7"], fixtures_dir=tmp)
            second = _run_script(["close", "o/r", "7"], fixtures_dir=tmp)
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertEqual(_parse_envelope(first)["closed"], True)
            self.assertEqual(_parse_envelope(second)["closed"], True)

    def test_close_dry_run_performs_no_write(self):
        result = _run_script(["close", "o/r", "42", "--dry-run"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_envelope(result)
        self.assertTrue(env["dry_run"])
        self.assertIn("would_run", env)
        self.assertNotIn("closed", env)

    def test_reopen_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "reopen", "42", "--repo", "o/r"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            result = _run_script(["reopen", "o/r", "42"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["op"], "reopen")
            self.assertTrue(env["reopened"])

    def test_reopen_dry_run_performs_no_write(self):
        result = _run_script(["reopen", "o/r", "42", "--dry-run"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_envelope(result)
        self.assertTrue(env["dry_run"])
        self.assertNotIn("reopened", env)


class EditBodyTests(unittest.TestCase):
    def test_edit_body_happy_path_emits_write_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_bytes = b"replacement body text\n"
            body_path.write_bytes(body_bytes)
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--body-file", str(body_path)]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/42\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(["edit-body", "o/r", "42", str(body_path)], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertEqual(env["op"], "edit-body")
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))

    def test_edit_body_dry_run_performs_no_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_bytes = b"content"
            body_path.write_bytes(body_bytes)
            result = _run_script(
                ["edit-body", "o/r", "42", str(body_path), "--dry-run"]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertTrue(env["dry_run"])
            self.assertIn("would_run", env)
            self.assertNotIn("url", env)
            # v1 parity (verified against a live `gh-persist.sh ... --dry-run` run): emit_envelope
            # reports body_bytes/body_sha256 unconditionally, dry-run or not -- it hashes the
            # caller-staged file directly, which needs no live gh call to compute. Only `url` (the
            # thing a real write produces) is genuinely absent in dry-run.
            self.assertEqual(env["body_bytes"], len(body_bytes))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))


class EditPrBodyTests(unittest.TestCase):
    """gh_persist.py edit-pr-body — `gh pr edit --body-file`, the PR-side counterpart to edit-body
    (which is `gh issue edit` and rejects a PR number). Mirrors EditBodyTests exactly; the noun
    assertion below is the whole point of the op existing separately.
    """

    def test_edit_pr_body_happy_path_emits_write_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_bytes = b"refreshed epic integration PR body\n"
            body_path.write_bytes(body_bytes)
            cmd = ["pr", "edit", "300", "--repo", "o/r", "--body-file", str(body_path)]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/pull/300\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt"}])
            result = _run_script(["edit-pr-body", "o/r", "300", str(body_path)], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertEqual(env["op"], "edit-pr-body")
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))

    def test_edit_pr_body_dry_run_previews_gh_pr_edit_not_gh_issue_edit(self):
        # The regression guard for the reason this op exists: `gh issue edit <pr>` errors on a PR
        # number, so a PR-body write routed through edit-body never lands.
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_bytes = b"content"
            body_path.write_bytes(body_bytes)
            result = _run_script(["edit-pr-body", "o/r", "300", str(body_path), "--dry-run"])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertTrue(env["dry_run"])
            self.assertIn("pr edit", env["would_run"])
            self.assertNotIn("issue edit", env["would_run"])
            self.assertNotIn("url", env)
            self.assertEqual(env["body_bytes"], len(body_bytes))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))

    def test_edit_pr_body_with_empty_file_returns_empty_body_file_decision_before_any_gh_call(self):
        # No manifest.json in this tempdir: a gh call before the gate would MISS (shim exit 2, no
        # envelope), so a clean decision envelope proves the gate runs first — same proof shape as
        # EmptyBodyGateTests.
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.md"
            empty_path.write_bytes(b"")
            result = _run_script(["edit-pr-body", "o/r", "300", str(empty_path)], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")


class DryRunNeverInvokesGhTests(unittest.TestCase):
    """--dry-run must perform no live write for every body-bearing subcommand -- proven the same
    way as the empty-body gate: no manifest.json exists, so any real gh call would MISS.
    """

    def test_create_dry_run_with_no_fixtures_present_still_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_bytes = b"content"
            body_path.write_bytes(body_bytes)
            # fixtures_dir intentionally omitted -- GH_SHIM_FIXTURES unset, any gh call MISSes.
            result = _run_script(
                ["create", "o/r", str(body_path), "--title", "T", "--dry-run"]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertTrue(env["dry_run"])
            self.assertIn("would_run", env)
            self.assertNotIn("url", env)
            # v1 parity: body_bytes/body_sha256 are reported even in dry-run (hashed straight from
            # the staged file, no live gh call needed) -- verified against a live v1 dry-run run.
            self.assertEqual(env["body_bytes"], len(body_bytes))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))

    def test_comment_dry_run_with_no_fixtures_present_still_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_bytes = b"content"
            body_path.write_bytes(body_bytes)
            result = _run_script(
                ["comment", "o/r", "issue", "1", str(body_path), "--dry-run"]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertTrue(env["dry_run"])
            self.assertEqual(env["body_bytes"], len(body_bytes))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(body_bytes))


class EditLabelsTests(unittest.TestCase):
    """gh_persist.py edit-labels (added S13, additive per architecture.md §2) — bodyless label
    add/remove (module docstring's edit-labels paragraph). No empty-body gate; at least one of
    --add/--remove required; no client-side idempotency special-casing (the shim fixture models
    gh's own no-op-on-repeat behavior, mirroring CloseReopenIdempotencyTests)."""

    def test_add_single_label_emits_ok_with_added_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--add-label", "planned"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/42\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt", "exit_code": 0}])
            result = _run_script(["edit-labels", "o/r", "42", "--add", "planned"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["op"], "edit-labels")
            self.assertEqual(env["added"], ["planned"])
            self.assertEqual(env["removed"], [])
            self.assertEqual(env["url"], "https://github.com/o/r/issues/42")
            self.assertNotIn("body_bytes", env)
            self.assertNotIn("body_sha256", env)

    def test_remove_single_label_emits_ok_with_removed_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--remove-label", "stale"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            result = _run_script(["edit-labels", "o/r", "42", "--remove", "stale"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["removed"], ["stale"])
            self.assertEqual(env["added"], [])

    def test_add_and_remove_combined_forward_both_flag_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--add-label", "planned",
                   "--add-label", "researched", "--remove-label", "triage"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            result = _run_script(
                ["edit-labels", "o/r", "42", "--add", "planned", "--add", "researched",
                 "--remove", "triage"],
                fixtures_dir=tmp,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["added"], ["planned", "researched"])
            self.assertEqual(env["removed"], ["triage"])

    def test_no_add_or_remove_flags_is_a_usage_error(self):
        result = _run_script(["edit-labels", "o/r", "42"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_add_present_label_is_still_a_no_op_success(self):
        # gh's own semantics: --add-label on an already-present label is idempotent (no error).
        # This script performs no client-side "is it already set" pre-check -- the fixture models
        # gh's own no-op success, and re-running the same call twice proves no special-casing
        # rejects the repeat (mirrors CloseReopenIdempotencyTests's close-on-closed pattern).
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--add-label", "planned"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            first = _run_script(["edit-labels", "o/r", "42", "--add", "planned"], fixtures_dir=tmp)
            second = _run_script(["edit-labels", "o/r", "42", "--add", "planned"], fixtures_dir=tmp)
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertEqual(_parse_envelope(first)["added"], ["planned"])
            self.assertEqual(_parse_envelope(second)["added"], ["planned"])

    def test_remove_absent_label_is_still_a_no_op_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--remove-label", "never-there"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            first = _run_script(["edit-labels", "o/r", "42", "--remove", "never-there"], fixtures_dir=tmp)
            second = _run_script(["edit-labels", "o/r", "42", "--remove", "never-there"], fixtures_dir=tmp)
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertEqual(second.returncode, 0, msg=second.stderr)

    def test_dry_run_previews_command_and_makes_no_live_call(self):
        # No manifest/fixtures_dir -- any live gh call would MISS the shim.
        result = _run_script(["edit-labels", "o/r", "42", "--add", "planned", "--dry-run"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertTrue(env["dry_run"])
        self.assertIn("would_run", env)
        self.assertEqual(env["added"], ["planned"])
        self.assertNotIn("url", env)

    def test_surfaces_auth_required_as_needs_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--add-label", "planned"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(["edit-labels", "o/r", "42", "--add", "planned"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")


class EditTitleTests(unittest.TestCase):
    """gh_persist.py edit-title / edit-pr-title -- the only ops that set the title of an
    ALREADY-FILED issue/PR (module docstring's edit-title paragraph). Bodyless (a title is not a
    body): no empty-body gate, no body_bytes/body_sha256 receipt, mirroring EditLabelsTests rather
    than EditBodyTests. --title is required, so a no-op invocation is a usage error."""

    def test_edit_title_emits_ok_with_title_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--title", "Epic: payments rework"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/issues/42\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt", "exit_code": 0}])
            result = _run_script(
                ["edit-title", "o/r", "42", "--title", "Epic: payments rework"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["op"], "edit-title")
            self.assertEqual(env["issue"], "42")
            self.assertEqual(env["title"], "Epic: payments rework")
            self.assertEqual(env["url"], "https://github.com/o/r/issues/42")
            self.assertNotIn("body_bytes", env)
            self.assertNotIn("body_sha256", env)

    def test_edit_pr_title_uses_the_pr_noun(self):
        # edit-body's reason for the split: `gh issue edit` rejects a PR number, so the noun is in
        # the op name and a mis-targeted call fails loudly instead of mis-writing.
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["pr", "edit", "7", "--repo", "o/r", "--title", "fix: the thing"]
            _write_stdout_file(tmp, "url.txt", b"https://github.com/o/r/pull/7\n")
            _write_manifest(tmp, [{"argv": cmd, "stdout_file": "url.txt", "exit_code": 0}])
            result = _run_script(
                ["edit-pr-title", "o/r", "7", "--title", "fix: the thing"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["op"], "edit-pr-title")
            self.assertEqual(env["pr"], "7")
            self.assertEqual(env["title"], "fix: the thing")
            self.assertEqual(env["url"], "https://github.com/o/r/pull/7")
            self.assertNotIn("body_bytes", env)

    def test_title_with_spaces_and_quotes_crosses_as_one_argv_element(self):
        # Exact-list argv matching in the shim IS the assertion: a shell-interpolated or re-split
        # title would arrive as several elements and MISS.
        tricky = 'Epic: "payments" & billing -- v2'
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--title", tricky]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            result = _run_script(["edit-title", "o/r", "42", "--title", tricky], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(_parse_envelope(result)["title"], tricky)

    def test_missing_title_flag_is_a_usage_error(self):
        for argv in (["edit-title", "o/r", "42"], ["edit-pr-title", "o/r", "7"]):
            with self.subTest(argv=argv):
                result = _run_script(argv)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_dry_run_previews_command_and_makes_no_live_call(self):
        # No manifest/fixtures_dir -- any live gh call would MISS the shim.
        for argv, op in (
            (["edit-title", "o/r", "42", "--title", "T", "--dry-run"], "edit-title"),
            (["edit-pr-title", "o/r", "7", "--title", "T", "--dry-run"], "edit-pr-title"),
        ):
            with self.subTest(op=op):
                result = _run_script(argv)
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                env = _parse_envelope(result)
                envelope_asserts.assert_full_envelope_conformance(env)
                self.assertEqual(env["op"], op)
                self.assertTrue(env["dry_run"])
                self.assertIn("would_run", env)
                self.assertEqual(env["title"], "T")
                self.assertNotIn("url", env)

    def test_surfaces_auth_required_as_needs_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "edit", "42", "--repo", "o/r", "--title", "T"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(["edit-title", "o/r", "42", "--title", "T"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")


class ClosePrTests(unittest.TestCase):
    """gh_persist.py close-pr (added S13, additive per architecture.md §2) — closes a PR with an
    OPTIONAL staged comment (module docstring's close-pr paragraph). The comment crosses the
    prompt boundary as a staged-file path but the gh-argv boundary as text (`gh pr close` has no
    --body-file of its own)."""

    def test_close_without_comment_is_bodyless(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["pr", "close", "287", "--repo", "o/r"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            result = _run_script(["close-pr", "o/r", "287"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["op"], "close-pr")
            self.assertTrue(env["closed"])
            self.assertFalse(env["commented"])
            self.assertNotIn("body_bytes", env)
            self.assertNotIn("body_sha256", env)

    def test_close_with_comment_forwards_staged_text_as_comment_flag(self):
        # The exact byte-faithful supersession marker text a HARD-revise close stages
        # (docs/specs/resolver.md's "Predecessor-PR detection": greps a closed PR body for the
        # literal phrase `Re-plan superseded this PR`).
        comment_text = (
            "Re-plan superseded this PR. See updated plan at "
            "https://github.com/o/r/issues/142#issuecomment-999. A new branch and PR will open "
            "at the next `/ctrl-freaq:resolver #142` run."
        )
        with tempfile.TemporaryDirectory() as tmp:
            comment_path = Path(tmp) / "close-comment.md"
            comment_path.write_text(comment_text, encoding="utf-8")

            cmd = ["pr", "close", "287", "--repo", "o/r", "--comment", comment_text]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 0}])
            result = _run_script(
                ["close-pr", "o/r", "287", "--comment-file", str(comment_path)], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            envelope_asserts.assert_write_receipt_shape(env)
            self.assertTrue(env["closed"])
            self.assertTrue(env["commented"])
            self.assertEqual(env["body_bytes"], len(comment_text.encode("utf-8")))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(comment_text.encode("utf-8")))

    def test_close_with_missing_comment_file_returns_empty_body_file_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = str(Path(tmp) / "does-not-exist.md")
            result = _run_script(
                ["close-pr", "o/r", "287", "--comment-file", missing_path], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")

    def test_close_with_zero_byte_comment_file_returns_empty_body_file_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.md"
            empty_path.write_bytes(b"")
            result = _run_script(
                ["close-pr", "o/r", "287", "--comment-file", str(empty_path)], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "EMPTY_BODY_FILE")
            self.assertEqual(env["decision"]["context"]["reason"], "zero bytes")

    def test_empty_body_gate_never_calls_gh_at_all(self):
        # No manifest/fixtures_dir -- any live gh call would MISS the shim and fail the test.
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = str(Path(tmp) / "does-not-exist.md")
            result = _run_script(["close-pr", "o/r", "287", "--comment-file", missing_path])
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["status"], "needs_decision")

    def test_dry_run_without_comment_previews_bodyless_command(self):
        result = _run_script(["close-pr", "o/r", "287", "--dry-run"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(env)
        self.assertTrue(env["dry_run"])
        self.assertIn("would_run", env)
        self.assertNotIn("closed", env)
        self.assertNotIn("body_bytes", env)

    def test_dry_run_with_comment_previews_command_and_receipt_with_no_live_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            comment_path = Path(tmp) / "close-comment.md"
            comment_bytes = b"Re-plan superseded this PR.\n"
            comment_path.write_bytes(comment_bytes)
            # No manifest/fixtures_dir -- any live gh call would MISS the shim.
            result = _run_script(
                ["close-pr", "o/r", "287", "--comment-file", str(comment_path), "--dry-run"]
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertTrue(env["dry_run"])
            self.assertIn("Re-plan superseded this PR", env["would_run"])
            self.assertEqual(env["body_bytes"], len(comment_bytes))
            self.assertEqual(env["body_sha256"], hashing.sha256_hex(comment_bytes))

    def test_surfaces_auth_required_as_needs_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["pr", "close", "287", "--repo", "o/r"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(["close-pr", "o/r", "287"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")


class UsageErrorTests(unittest.TestCase):
    """architecture.md §3: exit 2, no envelope, for a malformed invocation."""

    def test_no_subcommand_is_a_usage_error(self):
        result = _run_script([])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_unknown_subcommand_is_a_usage_error(self):
        result = _run_script(["frobnicate", "o/r", "1"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_create_without_title_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            result = _run_script(["create", "o/r", str(body_path)])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_comment_invalid_target_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            result = _run_script(["comment", "o/r", "not-a-real-target", "1", str(body_path)])
            self.assertEqual(result.returncode, 2)

    def test_close_invalid_reason_is_a_usage_error(self):
        result = _run_script(["close", "o/r", "1", "--reason", "bogus-reason"])
        self.assertEqual(result.returncode, 2)


class AuthRequiredClassificationTests(unittest.TestCase):
    """architecture.md §3: AUTH_REQUIRED is "detected by the pipelib runner, any script" -- proves
    gh_persist.py surfaces it as a needs_decision rather than a bare hard failure. The shim only
    replays stdout/exit_code (tests/shim/gh has no stderr channel), but that's sufficient here:
    pipelib.process._is_gh_auth_failure's PRIMARY signal is gh's own documented exit code 4 (`gh
    help exit-codes`) -- stderr-text matching is only the secondary fallback for a nonzero,
    non-4 exit -- so a bare exit_code=4 fixture (no stdout/stderr needed) exercises the exact same
    classification path a real auth failure would.
    """

    def test_create_surfaces_auth_required_as_needs_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            cmd = ["issue", "create", "--repo", "o/r", "--title", "T", "--body-file",
                   str(body_path)]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(
                ["create", "o/r", str(body_path), "--title", "T"], fixtures_dir=tmp
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            envelope_asserts.assert_full_envelope_conformance(env)
            self.assertEqual(env["status"], "needs_decision")
            self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")

    def test_edit_pr_body_surfaces_auth_required_as_needs_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body.md"
            body_path.write_bytes(b"content")
            cmd = ["pr", "edit", "300", "--repo", "o/r", "--body-file", str(body_path)]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(["edit-pr-body", "o/r", "300", str(body_path)], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")

    def test_close_surfaces_auth_required_as_needs_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = ["issue", "close", "42", "--repo", "o/r"]
            _write_manifest(tmp, [{"argv": cmd, "exit_code": 4}])
            result = _run_script(["close", "o/r", "42"], fixtures_dir=tmp)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            env = _parse_envelope(result)
            self.assertEqual(env["decision"]["code"], "AUTH_REQUIRED")


class AttachTests(unittest.TestCase):
    """``--attach FILE[#ALT]`` (gh >= 2.99): forwarded verbatim on the body ops and issue/pr comments,
    refused up front where it can't work, never retried without on an old gh, a partial upload is a
    landed write, and a body op reads the rewritten body back as ``posted_body_path``. Shim tests
    prove the argv end-to-end; the stderr-driven branches run in-process (module docstring,
    strategy 2)."""

    def setUp(self):
        self._original_run = gh_persist.process.run
        self.addCleanup(setattr, gh_persist.process, "run", self._original_run)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.body = self.tmp / "pr.md"
        self.body.write_text("body ![after](%s)\n" % (self.tmp / "after.png"), encoding="utf-8")
        self.shot = self.tmp / "after.png"
        self.shot.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    def _in_process(self, argv, results):
        """Run ``main`` in-process against scripted results; ``(exit, envelope|None, stderr, calls)``.
        A usage error is ``SystemExit(2)`` from argparse, reported as exit 2."""
        fake = _ScriptedProcessRun(results)
        gh_persist.process.run = fake
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = gh_persist.main(argv)
            except SystemExit as exc:
                code = exc.code
        lines = out.getvalue().splitlines()
        return code, (json.loads(lines[0]) if lines else None), err.getvalue(), fake.calls

    def _ok(self, stdout):
        return process.CommandResult(returncode=0, stdout=stdout, stderr="")

    # -- forwarding --

    def test_create_pr_forwards_attach_and_reads_the_posted_body_back(self):
        attach = "%s#The after state" % self.shot
        pr_cmd = ["pr", "create", "--repo", "o/r", "--title", "T", "--body-file", str(self.body),
                  "--base", "main", "--head", "h", "--attach", attach]
        view_cmd = ["pr", "view", "https://github.com/o/r/pull/7", "--repo", "o/r", "--json",
                    "body", "--jq", ".body"]
        posted = "body ![after](https://github.com/user-attachments/assets/abc)\n"
        _write_stdout_file(self.tmp, "url.txt", b"https://github.com/o/r/pull/7\n")
        _write_stdout_file(self.tmp, "posted.txt", posted.encode("utf-8") + b"\n")
        _write_manifest(self.tmp, [{"argv": pr_cmd, "stdout_file": "url.txt"},
                                   {"argv": view_cmd, "stdout_file": "posted.txt"}])
        result = _run_script(["create-pr", "o/r", str(self.body), "--title", "T", "--base", "main",
                              "--head", "h", "--attach", attach], fixtures_dir=self.tmp)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(env)
        envelope_asserts.assert_write_receipt_shape(env)
        self.assertEqual(env["attachments"], [attach])
        self.assertEqual(env["notices"], [])
        # The receipt describes the STAGED body; the posted copy is the rewritten one.
        self.assertEqual(env["body_sha256"], hashing.sha256_hex(self.body.read_bytes()))
        self.assertEqual(env["posted_body_path"], str(self.tmp / "pr.posted.md"))
        self.assertEqual(Path(env["posted_body_path"]).read_text(encoding="utf-8"), posted)

    def test_every_attach_op_forwards_each_flag_in_its_dry_run_preview(self):
        second = self.tmp / "before.webp"
        second.write_bytes(b"x")
        attach = ["--attach", str(self.shot), "--attach", str(second)]
        cases = [
            ["create", "o/r", str(self.body), "--title", "T"],
            ["create-pr", "o/r", str(self.body), "--title", "T", "--base", "b", "--head", "h"],
            ["edit-body", "o/r", "5", str(self.body)],
            ["edit-pr-body", "o/r", "6", str(self.body)],
            ["comment", "o/r", "issue", "5", str(self.body)],
            ["comment", "o/r", "pr", "6", str(self.body)],
        ]
        for argv in cases:
            with self.subTest(op=argv[0], target=argv[2] if argv[0] == "comment" else None):
                code, env, err, calls = self._in_process(argv + attach + ["--dry-run"], [])
                self.assertEqual(code, 0, msg=err)
                self.assertEqual(calls, [])
                self.assertIn("--attach %s --attach %s" % (self.shot, second), env["would_run"])
                self.assertEqual(env["attachments"], [str(self.shot), str(second)])

    def test_comment_forwards_attach_without_a_read_back(self):
        code, env, err, calls = self._in_process(
            ["comment", "o/r", "pr", "6", str(self.body), "--attach", str(self.shot)],
            [self._ok("https://github.com/o/r/pull/6#issuecomment-1\n")],
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(len(calls), 1)  # nothing restages a comment, so no `pr view`
        self.assertEqual(calls[0][-2:], ["--attach", str(self.shot)])
        self.assertNotIn("posted_body_path", env)

    def test_without_attach_the_envelope_carries_no_attach_keys(self):
        code, env, err, calls = self._in_process(
            ["edit-pr-body", "o/r", "6", str(self.body)],
            [self._ok("https://github.com/o/r/pull/6\n")],
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("--attach", calls[0])
        for key in ("attachments", "posted_body_path", "attach_stderr"):
            self.assertNotIn(key, env)

    # -- up-front refusals: usage errors, nothing written --

    def _assert_refused(self, argv, fragment):
        code, env, err, calls = self._in_process(argv, [])
        self.assertEqual(code, 2)
        self.assertIsNone(env)
        self.assertEqual(calls, [])
        self.assertIn(fragment, err)

    def test_pr_review_refuses_attach(self):
        self._assert_refused(
            ["comment", "o/r", "pr-review", "6", str(self.body), "--review-action", "approve",
             "--attach", str(self.shot)],
            "--attach is forbidden when target is 'pr-review'",
        )

    def test_missing_empty_and_untyped_files_are_refused(self):
        empty = self.tmp / "empty.png"
        empty.write_bytes(b"")
        untyped = self.tmp / "notes.txt"
        untyped.write_text("x")
        for value, fragment in [
            (str(self.tmp / "nope.png") + "#alt", "file not found"),
            (str(empty), "file is empty"),
            (str(untyped), "file type is not one gh uploads"),
        ]:
            with self.subTest(fragment=fragment):
                self._assert_refused(["edit-pr-body", "o/r", "6", str(self.body), "--attach", value],
                                     fragment)

    def test_a_relative_path_is_refused_even_when_it_resolves_under_cwd(self):
        # gh resolves a relative path against --cwd, the check against the script's own cwd: the two
        # can disagree, and the body's exact-string match needs the absolute path anyway.
        self._assert_refused(
            ["--cwd", str(self.tmp), "edit-pr-body", "o/r", "6", str(self.body),
             "--attach", "after.png"],
            "needs an absolute path",
        )

    def test_a_hash_inside_the_path_is_named_not_reported_missing(self):
        hashed_dir = self.tmp / "run#3"
        hashed_dir.mkdir()
        (hashed_dir / "shot.png").write_bytes(b"x")
        for value in (str(hashed_dir / "shot.png"), str(hashed_dir / "shot.png") + "#Alt"):
            with self.subTest(value=value):
                self._assert_refused(
                    ["edit-pr-body", "o/r", "6", str(self.body), "--attach", value],
                    "path contains '#'",
                )

    def test_alt_text_containing_a_hash_still_attaches(self):
        code, env, err, _calls = self._in_process(
            ["edit-pr-body", "o/r", "6", str(self.body), "--attach", "%s#C# sample" % self.shot,
             "--dry-run"],
            [],
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(env["attachments"], ["%s#C# sample" % self.shot])

    def test_more_than_fifty_attachments_is_refused(self):
        argv = ["create-pr", "o/r", str(self.body), "--title", "T", "--base", "b", "--head", "h"]
        argv += ["--attach", str(self.shot)] * 51
        self._assert_refused(argv, "at most 50")

    def test_size_gate_reserves_room_for_each_attachment(self):
        self.body.write_text("x" * (gh_persist.BODY_CHAR_LIMIT - 100), encoding="utf-8")
        base = ["edit-pr-body", "o/r", "6", str(self.body), "--dry-run"]
        code, env, _err, _calls = self._in_process(base, [])
        self.assertEqual(env["status"], "ok")
        code, env, _err, calls = self._in_process(base + ["--attach", str(self.shot)], [])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertEqual(env["decision"]["code"], "BODY_TOO_LONG")
        context = env["decision"]["context"]
        self.assertEqual(context["attachment_allowance_chars"], gh_persist._ATTACH_CHAR_ALLOWANCE)
        # actual_chars stays the body's own length; only the overage counts the allowance.
        self.assertEqual(context["actual_chars"], gh_persist.BODY_CHAR_LIMIT - 100)
        self.assertEqual(context["overage_chars"], gh_persist._ATTACH_CHAR_ALLOWANCE - 100)

    # -- capability miss: an old gh --

    def test_unknown_attach_flag_fails_without_a_retry(self):
        miss = process.CommandResult(returncode=1, stdout="", stderr="unknown flag: --attach\n")
        code, env, err, calls = self._in_process(
            ["create-pr", "o/r", str(self.body), "--title", "T", "--base", "b", "--head", "h",
             "--attach", str(self.shot)],
            [miss],
        )
        self.assertEqual(code, 1)
        self.assertIsNone(env)
        self.assertEqual(len(calls), 1)  # never re-posted without the flag: refs would dangle
        self.assertTrue(err.startswith("ATTACH_UNSUPPORTED:"), err)

    def test_create_ladder_does_not_read_an_attach_miss_as_a_deps_miss(self):
        miss = process.CommandResult(returncode=1, stdout="", stderr="unknown flag: --attach\n")
        code, _env, err, calls = self._in_process(
            ["create", "o/r", str(self.body), "--title", "T", "--blocked-by", "9",
             "--attach", str(self.shot)],
            [miss],
        )
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(err.startswith("ATTACH_UNSUPPORTED:"), err)

    def test_attach_classifier_names_the_flag(self):
        self.assertTrue(gh_persist._is_attach_error("unknown flag: --attach\n\nUsage: ..."))
        self.assertFalse(gh_persist._is_attach_error("unknown flag: --blocked-by"))
        self.assertFalse(gh_persist._is_attach_error("unknown flag: --attachments"))

    # -- partial upload and read-back --

    def test_partial_upload_is_a_landed_write_with_a_notice(self):
        partial = process.CommandResult(returncode=1, stdout="https://github.com/o/r/pull/6\n",
                                        stderr="failed to upload after.png\n")
        code, env, err, calls = self._in_process(
            ["edit-pr-body", "o/r", "6", str(self.body), "--attach", str(self.shot)],
            [partial, self._ok("posted body\n")],
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(env["status"], "ok")
        self.assertEqual(env["url"], "https://github.com/o/r/pull/6")
        self.assertEqual(env["notices"], ["ATTACH_PARTIAL"])
        self.assertEqual(env["attach_stderr"], "failed to upload after.png\n")
        self.assertEqual([c[1:3] for c in calls], [["pr", "edit"], ["pr", "view"]])

    def test_partial_upload_stops_the_create_ladder(self):
        partial = process.CommandResult(returncode=1, stdout="https://github.com/o/r/issues/12\n",
                                        stderr="unknown flag: --blocked-by\nupload failed\n")
        code, env, err, calls = self._in_process(
            ["create", "o/r", str(self.body), "--title", "T", "--blocked-by", "9",
             "--attach", str(self.shot)],
            [partial, self._ok("posted\n")],
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(env["notices"], ["ATTACH_PARTIAL"])
        self.assertEqual([c[1:3] for c in calls], [["issue", "create"], ["issue", "view"]])

    def test_non_url_stdout_on_a_failed_write_is_a_hard_failure(self):
        # Only a GitHub URL proves the write landed; a stray stdout line on a failure must not turn a
        # real failure into `ok` + ATTACH_PARTIAL.
        failure = process.CommandResult(returncode=1, stdout="warning: something odd\n",
                                        stderr="GraphQL: could not resolve\n")
        code, env, err, calls = self._in_process(
            ["edit-pr-body", "o/r", "6", str(self.body), "--attach", str(self.shot)], [failure]
        )
        self.assertEqual(code, 1)
        self.assertIsNone(env)
        self.assertEqual(len(calls), 1)
        self.assertEqual(err, "GraphQL: could not resolve\n")

    def test_a_failed_read_back_is_a_notice_not_a_failure(self):
        code, env, err, calls = self._in_process(
            ["edit-body", "o/r", "5", str(self.body), "--attach", str(self.shot)],
            [self._ok("https://github.com/o/r/issues/5\n"),
             process.CommandResult(returncode=1, stdout="", stderr="HTTP 502\n")],
        )
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(env["notices"], ["ATTACH_READBACK_UNAVAILABLE"])
        self.assertIsNone(env["posted_body_path"])
        self.assertEqual(calls[1][1:4], ["issue", "view", "https://github.com/o/r/issues/5"])

    def test_a_non_attach_failure_still_surfaces_hard(self):
        failure = process.CommandResult(returncode=1, stdout="", stderr="GraphQL: not found\n")
        code, env, err, calls = self._in_process(
            ["edit-pr-body", "o/r", "6", str(self.body), "--attach", str(self.shot)], [failure]
        )
        self.assertEqual(code, 1)
        self.assertIsNone(env)
        self.assertEqual(err, "GraphQL: not found\n")


class ReviewThreadOpsTests(unittest.TestCase):
    """#145: `review-post` posts one COMMENT review carrying every thread in a spec, `review-reply`
    replies in one thread; both stamp every body themselves (skills/_shared/review-threads.md §2/§10),
    gate it before any `gh` call, and pass it as `-F body=@<stamped path>`."""

    HEAD = "a" * 40

    def setUp(self):
        self._original_run = gh_persist.process.run
        self.addCleanup(setattr, gh_persist.process, "run", self._original_run)
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-review-post-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(str(self.tmp), True))
        (self.tmp / "review.md").write_text("Two threads for you.\n", encoding="utf-8")
        (self.tmp / "f1.md").write_text("Sensitive path.\n", encoding="utf-8")
        (self.tmp / "f2.md").write_text("Out of plan.\n", encoding="utf-8")
        self.spec = self.tmp / "spec.json"
        self._write_spec()

    def _write_spec(self, **overrides):
        spec = {
            "pr": 5, "head": self.HEAD, "review_body": str(self.tmp / "review.md"),
            "threads": [
                {"f": 1, "path": "scripts/x.py", "subject": "line", "start_line": 3, "line": 7,
                 "flags": ["sensitive:scripts/x.py"], "supersedes": None, "body": str(self.tmp / "f1.md")},
                {"f": 2, "path": "docs/a.md", "subject": "file", "start_line": None, "line": None,
                 "flags": ["out-of-plan:docs/a.md"], "supersedes": None, "body": str(self.tmp / "f2.md")},
            ],
        }
        spec.update(overrides)
        self.spec.write_text(json.dumps(spec), encoding="utf-8")

    def _stamped(self, name):
        return str(self.tmp / name) + ".stamped.md"

    def _pending_argv(self):
        return ["api", "graphql", "-f", "query=" + gh_persist._pending_review_query("o/r", "5")]

    def _sequence(self, pending_nodes=()):
        """The manifest entries of a happy post, in call order: argv and canned stdout."""
        thread = lambda tid, url: {"data": {"addPullRequestReviewThread": {"thread": {
            "id": tid, "comments": {"nodes": [{"url": url}]}}}}}
        entries = [(self._pending_argv(), {"data": {"viewer": {"login": "me"}, "repository": {"pullRequest": {
            "id": "PR_1", "reviews": {"nodes": list(pending_nodes)}}}}})]
        for node in pending_nodes:
            if gh_persist._pending_review_foreign(node) is None:
                entries.append((["api", "graphql", "-f", "query=" + gh_persist._DELETE_REVIEW_MUTATION,
                                 "-f", "review=%s" % node["id"]], {"data": {"deletePullRequestReview": {}}}))
        entries += [
            (["api", "graphql", "-f", "query=" + gh_persist._ADD_REVIEW_MUTATION, "-f", "pr=PR_1",
              "-f", "commit=" + self.HEAD, "-F", "body=@" + self._stamped("review.md")],
             {"data": {"addPullRequestReview": {"pullRequestReview": {"id": "PRR_9", "url": "u"}}}}),
            (["api", "graphql", "-f", "query=" + gh_persist._ADD_RANGE_THREAD_MUTATION, "-f", "review=PRR_9",
              "-f", "path=scripts/x.py", "-F", "body=@" + self._stamped("f1.md"), "-F", "startLine=3",
              "-F", "line=7"], thread("PRRT_1", "https://github.com/o/r/pull/5#discussion_r1")),
            (["api", "graphql", "-f", "query=" + gh_persist._ADD_FILE_THREAD_MUTATION, "-f", "review=PRR_9",
              "-f", "path=docs/a.md", "-F", "body=@" + self._stamped("f2.md")],
             thread("PRRT_2", "https://github.com/o/r/pull/5#discussion_r2")),
            (["api", "graphql", "-f", "query=" + gh_persist._SUBMIT_REVIEW_MUTATION, "-f", "review=PRR_9"],
             {"data": {"submitPullRequestReview": {"pullRequestReview": {
                 "id": "PRR_9", "url": "https://github.com/o/r/pull/5#pullrequestreview-9"}}}}),
        ]
        return entries

    def test_review_post_call_sequence_through_the_shim(self):
        fixtures = self.tmp / "fx"
        fixtures.mkdir()
        manifest = []
        for i, (argv, out) in enumerate(self._sequence()):
            manifest.append({"argv": argv, "stdout_file": _write_stdout_file(
                fixtures, "o%d.json" % i, json.dumps(out).encode("utf-8"))})
        _write_manifest(fixtures, manifest)
        result = _run_script(["review-post", "o/r", "5", str(self.spec)], fixtures_dir=fixtures)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_envelope(result)
        envelope_asserts.assert_full_envelope_conformance(env)
        envelope_asserts.assert_write_receipt_shape(env)
        self.assertEqual((env["op"], env["review_id"]), ("review-post", "PRR_9"))
        self.assertEqual(env["url"], "https://github.com/o/r/pull/5#pullrequestreview-9")
        self.assertEqual([(t["f"], t["thread_id"]) for t in env["threads"]], [(1, "PRRT_1"), (2, "PRRT_2")])
        for receipt, name in zip(env["threads"], ("f1.md", "f2.md")):
            self.assertEqual(receipt["body_sha256"], hashing.sha256_hex_file(self._stamped(name)))
        # Every posted body starts with the marker; each root carries its F id in the stamp.
        for name, f in (("f1.md", 1), ("f2.md", 2)):
            text = Path(self._stamped(name)).read_text(encoding="utf-8")
            self.assertTrue(text.startswith(gh_persist.review_threads.MARKER + "\n"))
            self.assertEqual(gh_persist.review_threads.parse_stamp(text)["f"], f)
        self.assertTrue(Path(self._stamped("review.md")).read_text(encoding="utf-8").startswith(
            gh_persist.review_threads.MARKER))

    def _run_in_process(self, cli_args, results):
        fake = _ScriptedProcessRun(results)
        gh_persist.process.run = fake
        out, err = io.StringIO(), io.StringIO()
        parser, by_name = gh_persist._build_parser()
        args = parser.parse_args(cli_args)
        handler = gh_persist._cmd_review_post if args.subcommand == "review-post" else gh_persist._cmd_review_reply
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = handler(args, by_name[args.subcommand])
        return code, out.getvalue(), err.getvalue(), fake.calls

    def _ok(self, payload):
        return process.CommandResult(returncode=0, stdout=json.dumps(payload), stderr="")

    def _stranded(self, extra_comments=(), total=None):
        rt = gh_persist.review_threads
        comments = [{"body": rt.stamp_root(1, self.HEAD, "scripts/x.py", "line", 3, 7, [], "x")}]
        comments += [{"body": b} for b in extra_comments]
        return {"id": "PRR_old", "body": rt.stamp_review(self.HEAD, "old"), "author": {"login": "me"},
                "comments": {"totalCount": total if total is not None else len(comments), "nodes": comments}}

    def _conflicts(self, pending):
        first = self._sequence(pending_nodes=[pending])[0]
        code, out, err, calls = self._run_in_process(["review-post", "o/r", "5", str(self.spec)], [self._ok(first[1])])
        self.assertEqual((code, out, len(calls)), (1, "", 1), msg=err)
        self.assertTrue(err.startswith(gh_persist.REVIEW_PENDING_CONFLICT + ":"), err)
        return err

    def test_a_stranded_review_the_operator_added_comments_to_is_not_deleted(self):
        # A line comment added in GitHub's UI while the review is pending goes INTO that review.
        self.assertIn("did not write", self._conflicts(self._stranded(extra_comments=["my own note"])))

    def test_a_pasted_marker_without_the_review_stamp_is_the_operators(self):
        pending = dict(self._stranded(), body=gh_persist.review_threads.MARKER + "\nmy draft")
        self.assertIn("not the pipeline's", self._conflicts(pending))

    def test_a_pending_review_with_unread_comments_is_not_deleted(self):
        self.assertIn("more comments", self._conflicts(self._stranded(total=150)))

    def test_a_submit_that_landed_despite_a_failed_exit_is_ok(self):
        sequence = self._sequence()
        failed = process.CommandResult(returncode=1, stdout="", stderr="HTTP 502\n")
        landed = self._ok({"data": {"node": {"state": "COMMENTED", "url": "https://x/review9"}}})
        code, out, err, calls = self._run_in_process(
            ["review-post", "o/r", "5", str(self.spec)], [self._ok(o) for _, o in sequence[:-1]] + [failed, landed])
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(json.loads(out)["url"], "https://x/review9")
        self.assertIn("PullRequestReview", calls[-1][4])

    def test_a_failed_submit_still_pending_fails(self):
        sequence = self._sequence()
        failed = process.CommandResult(returncode=1, stdout="", stderr="HTTP 502\n")
        for readback in (self._ok({"data": {"node": {"state": "PENDING", "url": None}}}),
                         process.CommandResult(returncode=1, stdout="", stderr="boom\n")):
            code, out, err, _ = self._run_in_process(
                ["review-post", "o/r", "5", str(self.spec)], [self._ok(o) for _, o in sequence[:-1]] + [failed, readback])
            self.assertEqual((code, out), (1, ""), msg=err)

    def test_a_stranded_pipeline_pending_review_is_deleted_first(self):
        stranded = self._stranded()
        sequence = self._sequence(pending_nodes=[stranded])
        code, out, err, calls = self._run_in_process(
            ["review-post", "o/r", "5", str(self.spec)], [self._ok(o) for _, o in sequence])
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(calls, [["gh"] + argv for argv, _ in sequence])
        self.assertIn("deletePullRequestReview", calls[1][4])

    def test_an_operator_draft_review_is_a_conflict_and_nothing_is_written(self):
        draft = {"id": "PRR_mine", "body": "half-written notes", "author": {"login": "me"}}
        first = self._sequence(pending_nodes=[draft])[0]
        code, out, err, calls = self._run_in_process(["review-post", "o/r", "5", str(self.spec)], [self._ok(first[1])])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith(gh_persist.REVIEW_PENDING_CONFLICT + ":"), err)
        self.assertEqual(len(calls), 1, "only the read may run before the conflict")

    def test_another_accounts_pending_review_is_not_ours_to_touch(self):
        # PENDING reviews are private to their author; one GitHub ever reports for someone else is ignored.
        other = {"id": "PRR_x", "body": "notes", "author": {"login": "mate"}}
        sequence = self._sequence(pending_nodes=[other])
        code, _, err, calls = self._run_in_process(
            ["review-post", "o/r", "5", str(self.spec)], [self._ok(o) for _, o in sequence])
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(len(calls), len(sequence))

    def test_an_empty_or_oversize_thread_body_gates_before_any_gh_call(self):
        (self.tmp / "f2.md").write_text("", encoding="utf-8")
        code, out, _, calls = self._run_in_process(["review-post", "o/r", "5", str(self.spec)], [])
        self.assertEqual((code, calls), (0, []))
        self.assertEqual(json.loads(out)["decision"]["code"], "EMPTY_BODY_FILE")
        (self.tmp / "f2.md").write_text("x" * (gh_persist.BODY_CHAR_LIMIT + 1), encoding="utf-8")
        code, out, _, calls = self._run_in_process(["review-post", "o/r", "5", str(self.spec)], [])
        self.assertEqual((code, calls), (0, []))
        self.assertEqual(json.loads(out)["decision"]["code"], "BODY_TOO_LONG")

    def test_a_stamp_the_grammar_cannot_carry_is_a_usage_error(self):
        self._write_spec(head="nothex")
        result = _run_script(["review-post", "o/r", "5", str(self.spec)])
        self.assertEqual(result.returncode, 2)

    def test_dry_run_previews_only(self):
        result = _run_script(["review-post", "o/r", "5", str(self.spec), "--dry-run"])
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        env = _parse_envelope(result)
        self.assertTrue(env["dry_run"])
        self.assertEqual(len(env["would_run"]), 5)
        self.assertIn("-F body=@" + self._stamped("f1.md"), env["would_run"][2])
        self.assertEqual([t["f"] for t in env["threads"]], [1, 2])

    def test_review_reply_stamps_and_receipts(self):
        body = self.tmp / "reply.md"
        body.write_text("Fixed in the retry helper.\n", encoding="utf-8")
        head = "b" * 40
        argv = ["gh", "api", "graphql", "-f", "query=" + gh_persist._REPLY_MUTATION, "-f", "thread=PRRT_1",
                "-F", "body=@" + str(body) + ".stamped.md"]
        check = ["gh", "api", "graphql", "-f", "query=" + gh_persist._THREAD_QUERY, "-f", "thread=PRRT_1"]
        code, out, err, calls = self._run_in_process(
            ["review-reply", "o/r", "5", "--thread", "PRRT_1", "--head", head, "--f", "1", str(body)],
            [self._thread_node(), self._ok({"data": {"addPullRequestReviewThreadReply": {"comment": {"url": "https://x/r1"}}}})])
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(calls, [check, argv])
        env = json.loads(out)
        self.assertEqual((env["op"], env["url"], env["posted"]), ("review-reply", "https://x/r1", True))
        stamped = Path(str(body) + ".stamped.md").read_text(encoding="utf-8")
        self.assertEqual(gh_persist.review_threads.parse_stamp(stamped),
                         {"kind": "reply", "f": 1, "head": head, "decision": None})
        self.assertEqual(env["body_sha256"], hashing.sha256_hex_file(str(body) + ".stamped.md"))
        self.assertIsNone(env["decision"])

    def _thread_node(self, resolved=False, number=5, repo="o/r", comments=(), viewer="me"):
        node = {"isResolved": resolved, "pullRequest": {"number": number, "repository": {"nameWithOwner": repo}},
                "root": {"nodes": list(comments[:1])}, "recent": {"nodes": list(comments)}}
        return self._ok({"data": {"viewer": {"login": viewer}, "node": node}})

    @staticmethod
    def _tc(body, login="me", association="OWNER"):
        # A thread comment as the pre-read returns it; the URL is unique because the root is de-duplicated by it.
        return {"author": {"login": login}, "authorAssociation": association, "body": body,
                "createdAt": "2026-10-01T00:00:00Z", "url": "https://x/c%d" % next(_THREAD_COMMENT_IDS),
                "state": "SUBMITTED", "originalCommit": None}

    def _thread_with(self, decision=None, flags=("sensitive:a.py",), tail=()):
        rt = gh_persist.review_threads
        comments = [self._tc(rt.stamp_root(1, "a" * 40, "a.py", "line", 1, 2, list(flags), "why"))]
        if decision:
            comments.append(self._tc(rt.stamp_reply("a" * 40, "%s: x" % decision, f=1, decision=decision)))
        return self._thread_node(comments=comments + list(tail))

    def _reply(self, answers, decision=None):
        body = self.tmp / "reply.md"
        body.write_text("looks right\n", encoding="utf-8")
        argv = ["review-reply", "o/r", "5", "--thread", "T", "--head", "b" * 40, "--f", "1", str(body)]
        if decision:
            argv += ["--decision", decision]
        code, out, err, calls = self._run_in_process(argv, answers)
        self.assertEqual(code, 0, msg=err)
        return json.loads(out), calls, body

    def _posted(self):
        return self._ok({"data": {"addPullRequestReviewThreadReply": {"comment": {"url": "https://x/r9"}}}})

    def test_review_reply_decision_stamps_the_field_and_the_visible_word(self):
        for decision, flags in (("accept", ()), ("changes", ()), ("dismiss", ("security:SEC-1",))):
            env, calls, body = self._reply([self._thread_with(flags=flags), self._posted()], decision=decision)
            self.assertEqual((env["posted"], env["decision"], len(calls)), (True, decision, 2))
            stamped = Path(str(body) + ".stamped.md").read_text(encoding="utf-8")
            self.assertEqual(gh_persist.review_threads.parse_stamp(stamped)["decision"], decision)
            self.assertEqual(gh_persist.review_threads.decision_text(stamped), "%s: looks right" % decision)

    def test_review_reply_refuses_a_plain_reply_under_a_standing_accept_or_dismiss(self):
        for decision in ("accept", "dismiss"):
            env, calls, _ = self._reply([self._thread_with(decision=decision, flags=("security:SEC-1",))])
            self.assertEqual((env["posted"], env["refused"], len(calls)), (False, "decided", 1))
        # A standing `changes` decision is answered by the resolver's fix reply.
        env, calls, _ = self._reply([self._thread_with(decision="changes"), self._posted()])
        self.assertEqual((env["posted"], env["refused"]), (True, None))

    def test_review_reply_posts_once_a_later_human_comment_ends_the_decision(self):
        env, calls, _ = self._reply([self._thread_with(decision="accept", tail=[self._tc("one more thing")]),
                                     self._posted()])
        self.assertEqual((env["posted"], len(calls)), (True, 2))
        # Another account's decision stamp carries no decision, so it refuses nothing.
        rt = gh_persist.review_threads
        other = self._tc(rt.stamp_reply("a" * 40, "accept: x", f=1, decision="accept"), login="writer",
                         association="MEMBER")
        env, _, _ = self._reply([self._thread_with(tail=[other]), self._posted()])
        self.assertEqual(env["posted"], True)

    def test_review_reply_never_refuses_a_decision_reply_as_decided(self):
        env, calls, _ = self._reply([self._thread_with(decision="accept"), self._posted()], decision="changes")
        self.assertEqual((env["posted"], env["refused"], env["decision"]), (True, None, "changes"))

    def test_review_reply_refuses_a_dismiss_decision_off_a_security_thread(self):
        env, calls, _ = self._reply([self._thread_with()], decision="dismiss")
        self.assertEqual((env["posted"], env["refused"], len(calls)), (False, "not-security", 1))
        # An operator-rooted thread carries no stamp, so no `security:` key either.
        env, _, _ = self._reply([self._thread_node(comments=[self._tc("is this safe?")])], decision="dismiss")
        self.assertEqual(env["refused"], "not-security")

    def test_review_reply_with_a_failed_pre_read_posts_no_decision(self):
        body = self.tmp / "reply.md"
        body.write_text("x\n", encoding="utf-8")
        code, out, _, calls = self._run_in_process(
            ["review-reply", "o/r", "5", "--thread", "T", "--head", "b" * 40, str(body), "--decision", "accept"],
            [process.CommandResult(returncode=1, stdout="", stderr="boom\n")])
        self.assertEqual((code, out, len(calls)), (1, "", 1))

    def test_review_reply_an_unknown_decision_is_a_usage_error(self):
        body = self.tmp / "reply.md"
        body.write_text("x\n", encoding="utf-8")
        result = _run_script(["review-reply", "o/r", "5", "--thread", "T", "--head", "b" * 40, str(body),
                              "--decision", "approve"])
        self.assertEqual(result.returncode, 2)

    def test_review_reply_refuses_a_resolved_or_foreign_thread(self):
        # A reply would move a resolved thread's reference commit past the human's resolution.
        body = self.tmp / "reply.md"
        body.write_text("Fixed.\n", encoding="utf-8")
        for answer, reason in ((self._thread_node(resolved=True), "resolved"),
                               (self._thread_node(number=6), "other-pr"),
                               (self._thread_node(repo="o/other"), "other-pr"),
                               (self._ok({"data": {"node": None}}), "not-a-thread")):
            code, out, err, calls = self._run_in_process(
                ["review-reply", "o/r", "5", "--thread", "T", "--head", "b" * 40, str(body)], [answer])
            self.assertEqual(code, 0, msg=err)
            env = json.loads(out)
            self.assertEqual((env["posted"], env["refused"], len(calls)), (False, reason, 1))

    def test_review_reply_with_a_failed_thread_read_posts_nothing(self):
        body = self.tmp / "reply.md"
        body.write_text("Fixed.\n", encoding="utf-8")
        code, out, _, calls = self._run_in_process(
            ["review-reply", "o/r", "5", "--thread", "T", "--head", "b" * 40, str(body)],
            [process.CommandResult(returncode=1, stdout="", stderr="boom\n")])
        self.assertEqual((code, out, len(calls)), (1, "", 1))

    def test_review_reply_needs_a_pr_number(self):
        body = self.tmp / "reply.md"
        body.write_text("x\n", encoding="utf-8")
        result = _run_script(["review-reply", "o/r", "five", "--thread", "T", "--head", "b" * 40, str(body)])
        self.assertEqual(result.returncode, 2)

    def test_review_reply_gates_an_empty_body(self):
        body = self.tmp / "reply.md"
        body.write_text("", encoding="utf-8")
        code, out, _, calls = self._run_in_process(
            ["review-reply", "o/r", "5", "--thread", "T", "--head", "b" * 40, str(body)], [])
        self.assertEqual((code, calls), (0, []))
        self.assertEqual(json.loads(out)["decision"]["code"], "EMPTY_BODY_FILE")

    def test_two_threads_naming_one_body_file_is_a_usage_error(self):
        spec = json.loads(self.spec.read_text(encoding="utf-8"))
        spec["threads"][1]["body"] = spec["threads"][0]["body"]
        self.spec.write_text(json.dumps(spec), encoding="utf-8")
        result = _run_script(["review-post", "o/r", "5", str(self.spec)])
        self.assertEqual(result.returncode, 2)
        self.assertIn("one body file", result.stderr)

    def test_a_landed_submit_without_a_url_is_still_ok(self):
        # The review is published once submit exits 0: reporting a failure would make the caller re-post.
        sequence = self._sequence()
        sequence[-1] = (sequence[-1][0], {"data": {"submitPullRequestReview": {"pullRequestReview": None}}})
        code, out, err, _ = self._run_in_process(
            ["review-post", "o/r", "5", str(self.spec)], [self._ok(o) for _, o in sequence])
        self.assertEqual(code, 0, msg=err)
        env = json.loads(out)
        self.assertEqual((env["status"], env["url"], env["review_id"]), ("ok", None, "PRR_9"))

    def test_a_failed_thread_add_stops_before_submit(self):
        # The pending review is left marked, for the next run's stranded-review delete; nothing is
        # published, and the failure is a hard one (no envelope), never an `ok` over a partial review.
        sequence = self._sequence()
        failed = process.CommandResult(returncode=1, stdout="", stderr="GraphQL: line must be part of the diff\n")
        results = [self._ok(o) for _, o in sequence[:3]] + [failed]
        code, out, err, calls = self._run_in_process(["review-post", "o/r", "5", str(self.spec)], results)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("line must be part of the diff", err)
        self.assertEqual(len(calls), 4)
        self.assertFalse(any("submitPullRequestReview" in " ".join(c) for c in calls))

    def test_a_malformed_spec_field_is_a_usage_error_with_nothing_written(self):
        # Never coerced (a null path stamped as "None"), never a traceback (a non-string body).
        for mutate in (
            lambda spec: spec["threads"][0].__setitem__("path", None),
            lambda spec: spec["threads"][0].__setitem__("flags", "abc"),
            lambda spec: spec["threads"][0].__setitem__("supersedes", 3),
            lambda spec: spec["threads"][0].__setitem__("f", True),
            lambda spec: spec["threads"][1].__setitem__("body", 5),
            lambda spec: spec.__setitem__("review_body", 5),
            lambda spec: spec.__setitem__("head", int("1" * 40)),
        ):
            self._write_spec()
            spec = json.loads(self.spec.read_text(encoding="utf-8"))
            mutate(spec)
            self.spec.write_text(json.dumps(spec), encoding="utf-8")
            for stale in self.tmp.glob("*.stamped.md"):
                stale.unlink()
            result = _run_script(["review-post", "o/r", "5", str(self.spec)])
            self.assertEqual(result.returncode, 2, msg=(spec, result.stderr))
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(list(self.tmp.glob("*.stamped.md")), [], spec)

    def test_a_non_utf8_body_is_a_usage_error(self):
        (self.tmp / "review.md").write_bytes(b"\xff\xfe not utf-8")
        result = _run_script(["review-post", "o/r", "5", str(self.spec)])
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_review_reply_with_an_unstampable_f_is_a_usage_error(self):
        body = self.tmp / "reply.md"
        body.write_text("x\n", encoding="utf-8")
        result = _run_script(["review-reply", "o/r", "5", "--thread", "T", "--head", "b" * 40, "--f", "0", str(body)])
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_no_resolve_or_unresolve_op_exists(self):
        _, by_name = gh_persist._build_parser()
        self.assertFalse([name for name in by_name if "resolve" in name])


if __name__ == "__main__":
    unittest.main()
