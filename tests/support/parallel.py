"""Parallel execution for ``tests/run.py -j N`` (N > 1).

The scheduling unit is one TestCase class, run whole in one worker: no module uses
``setUpModule``/``setUpClass`` for fixtures, but some classes keep class-scoped state
(``StageTableTests.seen`` + ``tearDownClass``), so a class never splits across processes. Units
are submitted largest-first (by test count) to a ``ProcessPoolExecutor`` pinned to the ``spawn``
start method on every OS, so worker state is the same on macOS and Linux (prd.md §9.6).

The parent has already rewired ``PATH`` and passed the gh-resolves-to-the-shim check before any
pool exists; spawned workers inherit ``os.environ``, so ``_worker_init`` only redoes the
``sys.path`` wiring ``run.py`` does at import time.

Only plain data crosses the process boundary: a worker returns one dict per test (``test_id``,
``description``, ``outcome``, ``detail``, ``duration``) — never a ``TestCase``, ``TestResult`` or
traceback object, none of which pickles reliably. The parent prints a report shaped like
``unittest.TextTestRunner``'s and returns ``wasSuccessful()``'s answer.

This module lives under ``tests/support/`` (not beside ``run.py``) because ``test_run_py.py``'s
nested harness copies only ``run.py``, ``shim/``, ``support/`` and ``__init__.py``, and because
worker functions must be importable by name to be picklable under spawn.
"""

import multiprocessing
import sys
import time
import unittest
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool

OK = "ok"
FAIL = "fail"
ERROR = "error"
SKIP = "skip"
EXPECTED_FAILURE = "expected_failure"
UNEXPECTED_SUCCESS = "unexpected_success"

_SEPARATOR1 = "=" * 70
_SEPARATOR2 = "-" * 70

# (dots-mode char, verbose-mode word) per outcome, as TextTestResult prints them.
_SHORT = {
    OK: ".",
    FAIL: "F",
    ERROR: "E",
    SKIP: "s",
    EXPECTED_FAILURE: "x",
    UNEXPECTED_SUCCESS: "u",
}
_LONG = {
    OK: "ok",
    FAIL: "FAIL",
    ERROR: "ERROR",
    EXPECTED_FAILURE: "expected failure",
    UNEXPECTED_SUCCESS: "unexpected success",
}


def _iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def _is_failed_test(test):
    # unittest.loader._FailedTest (a module that failed to import) and _ErrorHolder-like loader
    # placeholders cannot be re-resolved by name in a worker; the parent runs them itself.
    return type(test).__module__ == "unittest.loader"


def discover_units(suite):
    """Group a discovered suite's test ids by ``"<module>.<Class>"``.

    Returns ``(units, failed_tests)``: ``units`` is a list of ``(class_key, [test_id, ...])`` in
    discovery order, then stably sorted by test count, largest first; ``failed_tests`` holds the
    discovery-time placeholder tests the parent must run in-process.
    """
    by_class = {}
    failed_tests = []
    for test in _iter_tests(suite):
        if _is_failed_test(test):
            failed_tests.append(test)
            continue
        cls = type(test)
        key = "%s.%s" % (cls.__module__, cls.__qualname__)
        by_class.setdefault(key, []).append(test.id())
    units = sorted(by_class.items(), key=lambda unit: len(unit[1]), reverse=True)
    return units, failed_tests


def _worker_init(repo_root, scripts_dir):
    for extra_path in (repo_root, scripts_dir):
        if extra_path not in sys.path:
            sys.path.insert(0, extra_path)


class _RecordingResult(unittest.TestResult):
    """Records one plain dict per test; no unittest object outlives the worker."""

    def __init__(self):
        super().__init__()
        self.records = []
        self._started = {}

    def startTest(self, test):
        super().startTest(test)
        self._started[test.id()] = time.perf_counter()

    def stopTest(self, test):
        # A test whose only failures were subtests is never recorded under its own id.
        self._started.pop(test.id(), None)
        super().stopTest(test)

    def _record(self, test, outcome, detail=""):
        started = self._started.pop(test.id(), None)
        duration = time.perf_counter() - started if started is not None else 0.0
        self.records.append(
            {
                "test_id": test.id(),
                "description": str(test),
                "outcome": outcome,
                "detail": detail,
                "duration": duration,
            }
        )

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, OK)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, FAIL, self.failures[-1][1])

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, ERROR, self.errors[-1][1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, SKIP, reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, EXPECTED_FAILURE)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, UNEXPECTED_SUCCESS)

    def addSubTest(self, test, subtest, err):
        # TestResult.addSubTest appends to failures/errors on a failing subtest; record it as its
        # own entry the way TextTestResult reports it (one block per failing subtest).
        super().addSubTest(test, subtest, err)
        if err is not None:
            if issubclass(err[0], test.failureException):
                self._record(subtest, FAIL, self.failures[-1][1])
            else:
                self._record(subtest, ERROR, self.errors[-1][1])


def run_unit(unit_ids):
    """Run one unit's tests (by id) in this process. Returns ``(records, test_count)``."""
    suite = unittest.defaultTestLoader.loadTestsFromNames(unit_ids)
    result = _RecordingResult()
    suite.run(result)
    return result.records, result.testsRun


def _run_in_process(tests):
    result = _RecordingResult()
    unittest.TestSuite(tests).run(result)
    return result.records, result.testsRun


class _Reporter:
    def __init__(self, stream, verbosity):
        self.stream = stream
        self.verbosity = verbosity
        self.records = []
        self.tests_run = 0

    def add(self, records, test_count):
        self.tests_run += test_count
        for record in records:
            self.records.append(record)
            if self.verbosity > 1:
                if record["outcome"] == SKIP:
                    word = "skipped %r" % (record["detail"],)
                else:
                    word = _LONG[record["outcome"]]
                self.stream.write("%s ... %s\n" % (record["description"], word))
            elif self.verbosity == 1:
                self.stream.write(_SHORT[record["outcome"]])
        self.stream.flush()

    def add_broken(self, unit_ids, message):
        """Every test of a unit that did not finish because the pool broke is an error."""
        self.add(
            [
                {
                    "test_id": test_id,
                    "description": test_id,
                    "outcome": ERROR,
                    "detail": message,
                    "duration": 0.0,
                }
                for test_id in unit_ids
            ],
            len(unit_ids),
        )

    def finish(self, elapsed, pool_broken):
        stream = self.stream
        if self.verbosity == 1:
            stream.write("\n")
        for flavour, outcome in (("ERROR", ERROR), ("FAIL", FAIL)):
            for record in self.records:
                if record["outcome"] != outcome:
                    continue
                stream.write(_SEPARATOR1 + "\n")
                stream.write("%s: %s\n" % (flavour, record["description"]))
                stream.write(_SEPARATOR2 + "\n")
                stream.write("%s\n" % record["detail"])
        stream.write(_SEPARATOR2 + "\n")
        stream.write(
            "Ran %d test%s in %.3fs\n\n"
            % (self.tests_run, "" if self.tests_run == 1 else "s", elapsed)
        )
        counts = {outcome: 0 for outcome in _SHORT}
        for record in self.records:
            counts[record["outcome"]] += 1
        successful = (
            counts[FAIL] == 0
            and counts[ERROR] == 0
            and counts[UNEXPECTED_SUCCESS] == 0
            and not pool_broken
        )
        infos = []
        if not successful:
            if counts[FAIL]:
                infos.append("failures=%d" % counts[FAIL])
            if counts[ERROR]:
                infos.append("errors=%d" % counts[ERROR])
        if counts[SKIP]:
            infos.append("skipped=%d" % counts[SKIP])
        if counts[EXPECTED_FAILURE]:
            infos.append("expected failures=%d" % counts[EXPECTED_FAILURE])
        if counts[UNEXPECTED_SUCCESS]:
            infos.append("unexpected successes=%d" % counts[UNEXPECTED_SUCCESS])
        stream.write("OK" if successful else "FAILED")
        stream.write(" (%s)\n" % ", ".join(infos) if infos else "\n")
        stream.flush()
        return successful


def _stop_workers(pool):
    # Snapshot the workers first: shutdown() drops the executor's reference to them. The process
    # map is private, but present on every version this suite supports (3.9 onward); 3.14's public
    # terminate_workers() does the same once it is the floor.
    processes = list((getattr(pool, "_processes", None) or {}).values())
    for process in processes:
        process.terminate()
    pool.shutdown(wait=False, cancel_futures=True)


def run_parallel(suite, jobs, verbosity, stream=None, repo_root="", scripts_dir=""):
    """Run ``suite`` across ``jobs`` spawned workers; print a TextTestRunner-shaped report.

    Returns the success flag (``wasSuccessful()``'s rule, and False on a broken pool).
    """
    stream = stream if stream is not None else sys.stderr
    reporter = _Reporter(stream, verbosity)
    started = time.perf_counter()
    units, failed_tests = discover_units(suite)
    pool_broken = False

    if failed_tests:
        reporter.add(*_run_in_process(failed_tests))

    if units:
        pending = {}
        pool = ProcessPoolExecutor(
            max_workers=min(jobs, len(units)),
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_worker_init,
            initargs=(repo_root, scripts_dir),
        )
        try:
            for _key, ids in units:
                pending[pool.submit(run_unit, ids)] = ids
            for future in as_completed(pending):
                ids = pending[future]
                try:
                    records, test_count = future.result()
                except BrokenProcessPool as exc:
                    pool_broken = True
                    reporter.add_broken(ids, "worker process died: %r" % (exc,))
                    continue
                except Exception as exc:  # a unit whose result could not come back
                    reporter.add_broken(ids, "unit failed in worker: %r" % (exc,))
                    continue
                reporter.add(records, test_count)
        except KeyboardInterrupt:
            # Stop like a serial run does. The executor's context-manager exit
            # (shutdown(wait=True)) would run every queued unit first, and cancel_futures cannot
            # reach a unit already prefetched into a worker's call queue, so stop the workers too.
            _stop_workers(pool)
            raise
        pool.shutdown(wait=True)

    return reporter.finish(time.perf_counter() - started, pool_broken)
