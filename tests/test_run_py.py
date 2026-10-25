"""Self-tests for tests/run.py itself: discovery, exit-code propagation, PATH self-check.

A deliberately-failing test cannot live in this repo's real tests/test_*.py tree (run.py would
then always exit non-zero). Instead these tests build a *throwaway nested copy* of the harness
layout — its own ``tests/`` package (named exactly ``tests``, since run.py's own
``from tests.support import shimenv`` requires that literal package name) inside a fresh temp
parent directory, with its own single test_*.py — and run that nested harness's run.py in a
subprocess, asserting on its exit code and output. This proves failure propagation without
poisoning this suite's own green run.
"""

import io
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from unittest import mock

from tests.support import parallel

_THIS_DIR = Path(__file__).resolve().parent
RUN_PY_NAME = "run.py"


class DiscoveryAndSuccessTests(unittest.TestCase):
    def test_run_py_exits_zero_on_a_passing_nested_suite(self):
        with _NestedHarness(
            {
                "test_passing.py": (
                    "import unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_ok(self):\n"
                    "        self.assertEqual(1, 1)\n"
                )
            }
        ) as nested:
            for args in ([], ["-j", "1"]):
                with self.subTest(jobs=args or "default"):
                    result = nested.run(extra_args=args)
                    self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_run_py_discovers_multiple_test_files(self):
        with _NestedHarness(
            {
                "test_a.py": (
                    "import unittest\n"
                    "class A(unittest.TestCase):\n"
                    "    def test_a(self):\n"
                    "        print('RAN_A')\n"
                ),
                "test_b.py": (
                    "import unittest\n"
                    "class B(unittest.TestCase):\n"
                    "    def test_b(self):\n"
                    "        print('RAN_B')\n"
                ),
            }
        ) as nested:
            result = nested.run(extra_args=["-v"])
            combined = result.stdout + result.stderr
            self.assertIn("RAN_A", combined)
            self.assertIn("RAN_B", combined)
            self.assertEqual(result.returncode, 0)


class FailurePropagationTests(unittest.TestCase):
    """Each case runs both branches: the default (parallel) run and `-j 1`, the serial
    `TextTestRunner` path."""

    def test_run_py_exits_nonzero_on_a_failing_nested_test(self):
        with _NestedHarness(
            {
                "test_failing.py": (
                    "import unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_deliberately_fails(self):\n"
                    "        self.assertEqual(1, 2, 'deliberate failure for run.py self-test')\n"
                )
            }
        ) as nested:
            for args in ([], ["-j", "1"]):
                with self.subTest(jobs=args or "default"):
                    result = nested.run(extra_args=args)
                    combined = result.stdout + result.stderr
                    self.assertNotEqual(result.returncode, 0, msg=combined)
                    self.assertIn("FAIL: test_deliberately_fails", combined)

    def test_run_py_exits_nonzero_on_a_nested_error_not_just_assertion_failure(self):
        with _NestedHarness(
            {
                "test_erroring.py": (
                    "import unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_raises(self):\n"
                    "        raise RuntimeError('deliberate error for run.py self-test')\n"
                )
            }
        ) as nested:
            for args in ([], ["-j", "1"]):
                with self.subTest(jobs=args or "default"):
                    result = nested.run(extra_args=args)
                    combined = result.stdout + result.stderr
                    self.assertNotEqual(result.returncode, 0, msg=combined)
                    self.assertIn("ERROR: test_raises", combined)
                    self.assertIn("RuntimeError: deliberate error for run.py self-test", combined)

    def test_one_failure_among_passes_still_fails_the_whole_run(self):
        with _NestedHarness(
            {
                "test_mixed.py": (
                    "import unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_pass_one(self):\n"
                    "        self.assertTrue(True)\n"
                    "    def test_pass_two(self):\n"
                    "        self.assertTrue(True)\n"
                    "    def test_the_one_that_fails(self):\n"
                    "        self.fail('deliberate')\n"
                )
            }
        ) as nested:
            for args in ([], ["-j", "1"]):
                with self.subTest(jobs=args or "default"):
                    result = nested.run(extra_args=args)
                    self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)


class PathSelfCheckTests(unittest.TestCase):
    def test_run_py_fails_fast_if_gh_would_not_resolve_to_shim(self):
        # Simplest reliable offline way to simulate "shim missing/broken": strip the execute bit
        # from the nested copy's own shim binary so PATH lookup skips it as non-executable, and
        # confirm run.py's startup self-check catches that before running any test.
        with _NestedHarness(
            {
                "test_passing.py": (
                    "import unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_ok(self):\n"
                    "        pass\n"
                )
            }
        ) as nested:
            shim_gh = nested.tests_dir / "shim" / "gh"
            shim_gh.chmod(0o644)
            result = nested.run()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FATAL", result.stdout + result.stderr)

    def test_parallel_run_fails_fast_before_any_worker_or_test(self):
        """The shim check is the parent's, before discovery and before any pool exists."""
        with _NestedHarness(
            {
                "test_passing.py": (
                    "import unittest\n"
                    "class T(unittest.TestCase):\n"
                    "    def test_ok(self):\n"
                    "        pass\n"
                )
            }
        ) as nested:
            (nested.tests_dir / "shim" / "gh").chmod(0o644)
            result = nested.run(extra_args=["-j", "2"])
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1, msg=combined)
            self.assertIn("FATAL", combined)
            self.assertNotIn("Ran", combined)


_PASSING_CLASS = (
    "import unittest\n"
    "class Passing(unittest.TestCase):\n"
    "    def test_passes(self):\n"
    "        self.assertTrue(True)\n"
)
_FAILING_CLASS = (
    "import unittest\n"
    "class Failing(unittest.TestCase):\n"
    "    def test_deliberately_fails(self):\n"
    "        self.assertEqual(1, 2, 'deliberate parallel failure')\n"
)


class ParallelRunTests(unittest.TestCase):
    """`-j N` (N > 1) runs TestCase classes across spawned workers (`tests/support/parallel.py`)
    and must report like the serial `TextTestRunner`: non-zero exit and every failing test named
    with its traceback."""

    def test_parallel_failure_is_named_with_its_traceback_and_fails_the_run(self):
        with _NestedHarness(
            {"test_pass.py": _PASSING_CLASS, "test_fail.py": _FAILING_CLASS}
        ) as nested:
            result = nested.run(extra_args=["-j", "2"])
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, msg=combined)
            self.assertIn("FAIL: test_deliberately_fails", combined)
            self.assertIn("deliberate parallel failure", combined)
            self.assertIn("Ran 2 tests", combined)
            self.assertIn("FAILED (failures=1)", combined)

    def test_parallel_error_is_named_and_fails_the_run(self):
        with _NestedHarness(
            {
                "test_pass.py": _PASSING_CLASS,
                "test_erroring.py": (
                    "import unittest\n"
                    "class Erroring(unittest.TestCase):\n"
                    "    def test_raises(self):\n"
                    "        raise RuntimeError('deliberate parallel error')\n"
                ),
            }
        ) as nested:
            result = nested.run(extra_args=["-j", "2"])
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, msg=combined)
            self.assertIn("ERROR: test_raises", combined)
            self.assertIn("RuntimeError: deliberate parallel error", combined)

    def test_parallel_import_failure_is_an_error_naming_the_module(self):
        with _NestedHarness(
            {
                "test_pass.py": _PASSING_CLASS,
                "test_broken.py": "import a_module_that_does_not_exist_anywhere\n",
            }
        ) as nested:
            result = nested.run(extra_args=["-j", "2"])
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, msg=combined)
            self.assertIn("test_broken", combined)
            self.assertIn("a_module_that_does_not_exist_anywhere", combined)
            self.assertIn("Ran 2 tests", combined)

    def test_parallel_run_with_only_an_import_failure_creates_no_pool(self):
        """Zero schedulable units: the parent runs the discovery-time failure itself."""
        with _NestedHarness(
            {"test_broken.py": "import a_module_that_does_not_exist_anywhere\n"}
        ) as nested:
            result = nested.run(extra_args=["-j", "2"])
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1, msg=combined)
            self.assertIn("Ran 1 test in", combined)
            self.assertIn("FAILED (errors=1)", combined)
            self.assertNotIn("ProcessPoolExecutor", combined)
            self.assertNotIn("concurrent/futures", combined)

    def test_a_worker_that_dies_fails_the_run_with_an_error(self):
        """A dead worker breaks the pool: every unit that did not finish is an error, exit 1."""
        with _NestedHarness(
            {
                "test_pass.py": _PASSING_CLASS,
                "test_dies.py": (
                    "import os, unittest\n"
                    "class Dies(unittest.TestCase):\n"
                    "    def test_kills_its_worker(self):\n"
                    "        os._exit(3)\n"
                ),
            }
        ) as nested:
            result = nested.run(extra_args=["-j", "2"])
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1, msg=combined)
            self.assertIn("ERROR: tests.test_dies.Dies.test_kills_its_worker", combined)
            self.assertIn("worker process died", combined)
            self.assertIn("Ran 2 tests", combined)

    def test_interrupt_stops_the_run_without_running_queued_units(self):
        """Ctrl-C reaches the whole process group, as from a terminal: the run stops promptly
        instead of draining the queue (6 classes x 20s at -j 2 would take 60s)."""
        files = {
            "test_slow%d.py" % i: (
                "import time, unittest\n"
                "class Slow%d(unittest.TestCase):\n"
                "    def test_sleeps(self):\n"
                "        time.sleep(20)\n" % i
            )
            for i in range(6)
        }
        with _NestedHarness(files) as nested:
            started = time.monotonic()
            proc = nested.popen(extra_args=["-j", "2"])
            time.sleep(3)
            os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                self.fail("the interrupted run did not exit")
            self.assertNotEqual(proc.returncode, 0)
            self.assertLess(time.monotonic() - started, 15)

    def test_parallel_verbose_prints_one_ok_line_per_test(self):
        with _NestedHarness(
            {
                "test_a.py": (
                    "import unittest\n"
                    "class A(unittest.TestCase):\n"
                    "    def test_alpha(self):\n"
                    "        pass\n"
                ),
                "test_b.py": (
                    "import unittest\n"
                    "class B(unittest.TestCase):\n"
                    "    def test_beta(self):\n"
                    "        pass\n"
                ),
            }
        ) as nested:
            result = nested.run(extra_args=["-j", "2", "-v"])
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, msg=combined)
            self.assertRegex(combined, r"test_alpha \(.*\) \.\.\. ok")
            self.assertRegex(combined, r"test_beta \(.*\) \.\.\. ok")
            self.assertIn("Ran 2 tests", combined)

    def test_serial_run_py_does_not_import_the_parallel_module(self):
        """The nested tree has no module that imports `parallel`, so its absence from
        `sys.modules` proves run.py's `-j 1` branch never imports it. (In the real suite,
        `test_run_py.py` imports it at discovery.)"""
        with _NestedHarness(
            {
                "test_serial.py": (
                    "import sys, unittest\n"
                    "class S(unittest.TestCase):\n"
                    "    def test_parallel_not_loaded(self):\n"
                    "        self.assertNotIn('tests.support.parallel', sys.modules)\n"
                )
            }
        ) as nested:
            result = nested.run(extra_args=["-j", "1"])
            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, msg=combined)
            self.assertIn("Ran 1 test", combined)
            self.assertIn("OK", combined)


class _BreaksOnSecondSubmit:
    """A stand-in executor: the first unit's future breaks, the next submit raises, as a real
    ProcessPoolExecutor does once a worker has died while units are still being queued."""

    def __init__(self, *args, **kwargs):
        self.submitted = 0

    def submit(self, fn, *args):
        self.submitted += 1
        if self.submitted > 1:
            raise BrokenProcessPool("a worker died")
        future = Future()
        future.set_exception(BrokenProcessPool("a worker died"))
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        pass


class ParallelReportingUnitTests(unittest.TestCase):
    """In-process cases for paths a nested run cannot reproduce on demand."""

    def test_a_pool_that_breaks_while_units_are_queued_reports_every_unit(self):
        # Local classes, so discovery never collects them as tests of this module.
        class _Passing(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass

        class _Other(unittest.TestCase):
            def test_three(self):
                pass

        suite = unittest.TestSuite(
            [_Passing("test_one"), _Passing("test_two"), _Other("test_three")]
        )
        stream = io.StringIO()
        with mock.patch.object(parallel, "ProcessPoolExecutor", _BreaksOnSecondSubmit):
            successful = parallel.run_parallel(suite, jobs=2, verbosity=1, stream=stream)
        report = stream.getvalue()
        self.assertFalse(successful)
        self.assertEqual(report.count("worker process died"), 3, msg=report)
        self.assertIn("Ran 3 tests", report)
        self.assertIn("FAILED (errors=3)", report)

    def test_failure_blocks_follow_discovery_order_not_completion_order(self):
        reporter = parallel._Reporter(
            io.StringIO(), verbosity=0, position={"m.C.test_a": 0, "m.C.test_b": 1}
        )
        for test_id in ("m.C.test_b (i=1)", "m.C.test_a"):
            reporter.add(
                [
                    {
                        "test_id": test_id,
                        "description": test_id,
                        "outcome": parallel.FAIL,
                        "detail": "boom",
                        "duration": 0.0,
                    }
                ],
                1,
            )
        reporter.finish(0.0, pool_broken=False)
        report = reporter.stream.getvalue()
        self.assertLess(
            report.index("FAIL: m.C.test_a\n"), report.index("FAIL: m.C.test_b (i=1)"), msg=report
        )


class JobsArgumentTests(unittest.TestCase):
    def test_non_positive_or_non_integer_jobs_is_a_usage_error(self):
        with _NestedHarness({"test_pass.py": _PASSING_CLASS}) as nested:
            for value in ("0", "x"):
                result = nested.run(extra_args=["-j", value])
                self.assertEqual(result.returncode, 2, msg=value + result.stdout + result.stderr)
                self.assertNotIn("Ran", result.stdout + result.stderr)

    def test_every_jobs_spelling_is_accepted(self):
        with _NestedHarness({"test_pass.py": _PASSING_CLASS}) as nested:
            for args in (["-j2"], ["--jobs", "2"], ["--jobs=2"]):
                result = nested.run(extra_args=args)
                self.assertEqual(result.returncode, 0, msg=repr(args) + result.stderr)


class _NestedHarness:
    """Builds `<tmp>/tests/{run.py,shim/,support/,__init__.py,<given test files>}` and runs the
    nested run.py as `python3 tests/run.py` with cwd=<tmp>, so the nested process's own
    `sys.path.insert(0, REPO_ROOT)` + `from tests.support import shimenv` resolve against the
    nested tree, completely isolated from this real suite.
    """

    def __init__(self, test_files):
        self._test_files = test_files
        self._parent = None
        self.tests_dir = None

    def __enter__(self):
        self._parent = Path(tempfile.mkdtemp(prefix="gh-pipeline-nested-harness-")).resolve()
        self.tests_dir = self._parent / "tests"
        self.tests_dir.mkdir()

        shutil.copy2(_THIS_DIR / RUN_PY_NAME, self.tests_dir / RUN_PY_NAME)
        shutil.copytree(_THIS_DIR / "shim", self.tests_dir / "shim")
        shutil.copytree(_THIS_DIR / "support", self.tests_dir / "support")
        shutil.copy2(_THIS_DIR / "__init__.py", self.tests_dir / "__init__.py")

        for name, source in self._test_files.items():
            (self.tests_dir / name).write_text(source, encoding="utf-8")

        return self

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(self._parent, ignore_errors=True)
        return False

    def popen(self, extra_args=None):
        """The nested run in its own process group, so a test can signal it and its workers."""
        args = ["python3", str(self.tests_dir / RUN_PY_NAME)] + (extra_args or [])
        return subprocess.Popen(
            args,
            cwd=str(self._parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def run(self, extra_args=None):
        args = ["python3", str(self.tests_dir / RUN_PY_NAME)] + (extra_args or [])
        return subprocess.run(
            args,
            cwd=str(self._parent),
            capture_output=True,
            encoding="utf-8",
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
