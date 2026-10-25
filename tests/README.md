# Offline test harness

This is the [architecture.md §10](../docs/architecture.md#10-testing-architecture) harness: a
stdlib-only `unittest` suite that runs every `scripts/*.py` decision path with **no network
access and no live GitHub repo**. It is a separate thing from `tests/SANDBOX.md`, which documents
the live sandbox repo used for parity/smoke runs — offline tests never touch that repo.

Read this file if you are: adding a new fixture case, adding a new `test_*.py`, or running the
suite on Linux. You should not need to read `tests/run.py` or `tests/shim/gh` source for any of
those three tasks.

## Running the suite

```bash
python3 tests/run.py        # quiet: dots + summary
python3 tests/run.py -v     # verbose: one line per test
python3 tests/run.py -j 4   # 4 worker processes (also -j4, --jobs 4, --jobs=4)
python3 tests/run.py -j 1   # serial: the plain unittest TextTestRunner run
```

The suite runs in parallel by default: `-j N` / `--jobs N` sets the worker count, which defaults to
`os.cpu_count()`. The unit of work is one TestCase class, run whole in one worker process
(`tests/support/parallel.py`), so class-scoped state (`setUpClass`, class attributes a
`tearDownClass` reads) never splits across processes. The report has the serial runner's shape:
dots (or one line per test with `-v`), every failure and error with its traceback, `Ran N tests`,
then `OK` or `FAILED (...)`. `-j 1` takes the exact serial code path and never loads the parallel
module, so it is the way to reproduce a run without concurrency.

Because tests run concurrently, no two tests may share a fixed filesystem path. A test that does not
assert a prep's default scratch dir passes `--scratch-dir` from a tempdir (the sandbox base classes'
`self.scratch`); a test that does assert `/tmp/gh-<skill>-<N>/` uses a (skill, key) pair no other
class uses.

Exit code is `0` on success, non-zero if any test failed or errored — this is what CI and the
plan's global DoD gate (`python3 tests/run.py` green from S2 onward) checks. CI is
`.github/workflows/ci.yml`: it runs the suite on every PR and push to `main`, on Linux (Python 3.9,
the floor, and current) and macOS (current).

No setup step is required: no `pip install`, no virtualenv, nothing to vendor. The only
requirement is a Python interpreter (`python3` on `PATH`, ≥ 3.9 — the pinned floor in
[architecture.md §1](../docs/architecture.md#1-system-overview--boundaries)) and a real `git`
binary (used by the git-sandbox helper for workspace/lifecycle tests — see below).

## How `run.py` wires the environment

Before discovering or running anything, `run.py`:

1. Rebuilds this process's own `PATH` as `tests/shim` **then** `tests/support/poison` **then**
   the rest of the inherited `PATH`. Any code under test that shells out to `gh` — directly, or
   indirectly through a subprocess it spawns — finds `tests/shim/gh` first.
2. Asserts `shutil.which("gh")` resolves to exactly `tests/shim/gh`, and refuses to run any test
   (`FATAL`, exit 1) if it doesn't. This is a static self-check, not a per-test one: if PATH
   wiring is ever broken, the whole run fails immediately instead of a handful of tests failing
   confusingly later. Both steps run in the parent process, before any worker starts; spawned
   workers inherit the rewired environment and never rewire `PATH` themselves.
3. Discovers every `tests/test_*.py` via `unittest.TestLoader().discover(...)`
   (pattern `test_*.py`), runs it (serially at `-j 1`, else across the worker pool), then exits
   `0`/`1` — non-zero on any failure, error or unexpected success (`wasSuccessful()`'s rule).

`run.py` never sets `GH_SHIM_FIXTURES` itself — that selects which fixture *case* the shim reads
from, and it's set per-test (see below), not suite-wide, since different tests need different
canned responses.

### Why there's a second thing (`tests/support/poison/gh`) besides the shim

The shim (`tests/shim/gh`) is the thing that actually answers fixture calls. The poison sentinel
(`tests/support/poison/gh`) is a second, independent `gh`-named executable that always fails
loudly with a distinctive marker and exit code. It sits *after* the shim on `PATH` during a
normal run, so it never actually gets reached — its only job is to be what a `gh` lookup finds
**if the shim were ever missing or skipped**, so that failure mode is a loud, obvious crash
instead of silently falling through to a real, network-calling `gh` further down `PATH`.
`tests/test_no_real_gh.py` proves both halves: that the shim wins under the normal wiring, and
that the poison sentinel itself actually fails loudly when it's the thing PATH finds.

Both `intercepted_env()` (shim + poison + inherited PATH) and `poison_only_env()` (poison +
inherited PATH, no shim — used only to test the sentinel in isolation) live in
`tests/support/shimenv.py`; use them from a test rather than hand-rolling `PATH` strings.

## Fixture layout

```
tests/fixtures/<case>/
  manifest.json       # required: list of {"argv": [...], "stdout_file": "...", "exit_code": N}
  <stdout files>       # referenced by stdout_file, any name, arbitrary bytes
```

`manifest.json` is a JSON **list**, not an object — this avoids ever needing to canonicalize an
argv list into a single string dict key. Each entry:

| Field | Required | Meaning |
|---|---|---|
| `argv` | yes | The exact argument list (excluding the `gh` token itself) this entry answers for. Matched by **exact list equality** against what the shim process received — no prefix matching, no glob. |
| `stdout_file` | no | Path **relative to the case directory** whose bytes are written to stdout verbatim (binary-safe: no text re-encoding, so a fixture can hold arbitrary JSON, including a future envelope payload, or diff bytes). Omit for empty stdout. |
| `exit_code` | no | Process exit code to return. Defaults to `0`. |

A case directory can hold as many `manifest.json` entries / stdout files as the scripts under
test need — one case usually corresponds to one script-under-test scenario (e.g. "issue fetch,
happy path" or "auth failure"), not one call.

### Adding a fixture case

1. Decide what real `gh` call(s) the scenario needs and what `gh` would print for each — capture
   real output once if you have a live sandbox handy (`gh <args> > tests/fixtures/<case>/<name>`),
   or hand-author a minimal realistic payload.
2. Create `tests/fixtures/<your-case>/` and drop the stdout file(s) in it.
3. Write `manifest.json` as a list, one entry per distinct argv the scenario calls with. Example:

   ```json
   [
     {
       "argv": ["issue", "view", "42", "--json", "body,title,labels"],
       "stdout_file": "issue_42.json",
       "exit_code": 0
     },
     {
       "argv": ["issue", "comment", "42", "--body-file", "-"],
       "stdout_file": "comment_posted.txt",
       "exit_code": 0
     }
   ]
   ```

4. Point your test at it: `os.environ["GH_SHIM_FIXTURES"] = shimenv.fixture_case_dir("your-case")`
   (or, more commonly, pass `fixture_case="your-case"` to
   `tests.support.shimenv.intercepted_env(...)` and use the returned `env` as the `env=` kwarg
   when invoking the script under test as a subprocess).
5. Run `python3 tests/run.py -v` and confirm your new test passes.

### What a miss looks like

If a script under test calls `gh` with an argv not in the active case's `manifest.json`, the shim
writes a diff to stderr — the argv it received, and every argv key available in that case — and
exits `2`. This is deliberate: an un-fixtured call must fail the test loudly (so a coverage gap
is visible immediately), not hang waiting on nothing, and not silently return empty output that
lets a bug slip past. See `tests/shim/gh`'s module docstring for the exact wire format if you
need it, but the miss message itself (stderr) is self-explanatory — you shouldn't need to.

## Git sandbox (`tests/support/gitsandbox.py`)

Workspace/lifecycle tests that need a real git repository (not a `gh` call) use this instead of
the shim — per [architecture.md §10](../docs/architecture.md#10-testing-architecture), "Git
sandbox: ... no shim needed." It shells out to the real `git` binary (an allowed spawnable) and
never touches the network — everything happens between two local temp directories.

```python
from tests.support import gitsandbox

# unittest.TestCase style — cleanup guaranteed via addCleanup, pass or fail:
origin = gitsandbox.mk_origin()
self.addCleanup(origin.cleanup)
clone = gitsandbox.mk_clone(origin)
self.addCleanup(clone.cleanup)

# or, outside a TestCase, the context-manager form:
with gitsandbox.git_sandbox() as (origin, clone):
    ...
```

`mk_origin()` builds a temp **bare** repo seeded with one commit on `main` (or the branch name you
pass). `mk_clone(origin)` clones it into a fresh temp working directory with a throwaway
`user.name`/`user.email` already configured. Both `.path` attributes are `Path.resolve()`-resolved
— macOS's `/tmp` is a symlink to `/private/tmp`, and comparing a raw `tempfile` path against a
`resolve()`-derived one elsewhere would spuriously mismatch.

## Importing shared helpers from a test

`tests/` is an importable package (`tests/__init__.py` exists; `run.py` inserts the repo root
onto `sys.path` before discovery). Import shared support this way, not via `sys.path` hacks in
each test file:

```python
from tests.support import gitsandbox
from tests.support import shimenv
```

From S3 onward, `scripts/pipelib/`'s envelope-conformance assertion helpers land under
`tests/support/` too and are imported the same way by every later suite (status validity,
decision-payload shape, `*_mode`/`*_path` pairing, exit-code contract) — this package layout is
what makes that possible without each suite re-deriving import paths.

## Running on Linux

The suite must pass on both macOS and Linux
([architecture.md §10](../docs/architecture.md#10-testing-architecture);
[prd.md §9.6](../docs/prd.md#9-engineering-quality-requirements)) — it's stdlib-only Python with
no BSD/GNU-specific shelling, so this should just work, but the dual-platform run is what proves
it rather than assumes it. A stock `python:3` image needs nothing installed beyond what it ships
with (no pip step, no vendored packages) and does need a `git` binary, which that image does not
include by default — install it in the container before running:

```bash
docker run --rm -v "$(pwd)":/repo -w /repo python:3 bash -c "
  apt-get update -qq && apt-get install -y -qq git > /dev/null &&
  python3 tests/run.py
"
```

Any container or host with Python ≥ 3.9 and `git` works equally well — this is one
copy-pasteable example, not the only valid invocation. There is nothing macOS-specific to strip
out first: no `sed`/`awk`/`date`/`stat` BSD-dialect calls, no case-insensitive-filesystem
assumption, no absolute `/opt/homebrew` or `/usr/local` path baked in anywhere in `tests/`.

## What this harness does NOT do

- It does not talk to the network, ever (proven by `tests/test_no_real_gh.py`, not just assumed).
- It does not touch the live sandbox repo — that's `tests/SANDBOX.md`'s job, and it's a
  completely separate, explicitly-authorized flow (parity/smoke runs), never exercised by
  `python3 tests/run.py`.
- It is not a linter for prompt files (`skills/*/SKILL.md`, `agents/*.md`) — those are validated
  by the contract-token census and banned-pattern greps
  ([architecture.md §10](../docs/architecture.md#10-testing-architecture), `CLAUDE.md`'s
  "Editing conventions" section), which have no offline harness because there's no deterministic
  code to run.
