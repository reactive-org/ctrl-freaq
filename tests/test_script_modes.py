"""Regression pin: every ``scripts/*.py`` CLI entry point is executable on disk.

Repo-wide, not setup-specific — lives at the top level of ``tests/`` (a neighbor of
``tests/test_run_py.py``, which is likewise about how the scripts get invoked rather than about one
skill's behavior) rather than under ``tests/test_setup_routing.py``, since the defect this pins is not
particular to the setup cutover.

**The Div-1 defect (docs/specs/parity/setup.md, S17 live parity, both legs).** `${CLAUDE_PLUGIN_ROOT}`
invocations always call a script by its **bare path** (``${CLAUDE_PLUGIN_ROOT}/scripts/prep_setup.py
...``), never via ``python3 <script>``. A script with a shebang line but the executable bit unset
exits **126** (`Permission denied`) the moment the OS tries to exec it directly — a hard failure with
no Python traceback, since the interpreter never even starts. This is the **third recurrence** of the
same class of defect (the "S21-era chmod lesson"): `scripts/prep_setup.py` and `scripts/workspace.py`
shipped 0644 out of S17's implementor pass, and the orchestrator's own sweep caught a third,
`scripts/parse.py`, already 0644 before this step touched it. Both S17 live-parity legs hit the 126 and
self-recovered by falling back to an explicit `python3 <script>` invocation — an honest, undesirable
recovery this test exists to make impossible to reintroduce silently. Fixed by ``chmod +x`` on all
three (docs/specs/parity/setup.md's Div-1 annotation records the resolution); this test is the
mechanical guard so a future new script — or a future artifact-export step that silently drops the
executable bit (e.g. a naive ``git archive``/zip/tarball round-trip, or a fresh ``git clone`` on a
filesystem/umask that doesn't preserve it) — fails the offline suite instead of failing live at
`${CLAUDE_PLUGIN_ROOT}` dispatch time.

**Scope of the pin.** Only a script that is BOTH (a) shebang-led (``#!...``, so it is meant to be
exec'd directly, not merely imported) AND (b) carries a top-level ``def main(`` CLI entry point (so it
is a real command, not a library module ``pipelib/*.py`` composes in-process) is required to be
executable. `scripts/oq_tracker.py` has a shebang but no ``def main(``/``__main__`` guard — a
composed-in-process helper module, not a directly-dispatched CLI — so it is outside this pin's scope
(it happens to already be executable; not asserted here, since it's not part of the closed-set
contract this test protects).
"""

import os
import stat
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _has_shebang(path):
    with open(path, "r", encoding="utf-8") as fh:
        first_line = fh.readline()
    return first_line.startswith("#!")


def _has_main_entry(path):
    text = path.read_text(encoding="utf-8")
    # top-level `def main(` — a real CLI entry point, not a helper of the same name nested elsewhere.
    for line in text.splitlines():
        if line.startswith("def main("):
            return True
    return False


def _dispatched_cli_scripts():
    """Every `scripts/*.py` (top level only — not `pipelib/`) that is shebang-led AND carries a
    top-level `def main(` — the closed set this pin covers."""
    scripts = []
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        if _has_shebang(path) and _has_main_entry(path):
            scripts.append(path)
    return scripts


class DispatchedCliScriptsAreExecutableTests(unittest.TestCase):
    """Div-1 regression pin (docs/specs/parity/setup.md): a shebang-led, `def main(`-carrying
    `scripts/*.py` must have the executable bit set, or a bare `${CLAUDE_PLUGIN_ROOT}/scripts/<x>.py`
    dispatch exits 126 with no Python traceback."""

    def test_at_least_the_known_cli_scripts_are_covered(self):
        # Sanity check on the discovery itself — if this drops to near-zero, the shebang/main()
        # detection broke, and the rest of this test class would vacuously pass.
        scripts = _dispatched_cli_scripts()
        names = {p.name for p in scripts}
        for expected in (
            "prep_setup.py",
            "workspace.py",
            "parse.py",
            "config_block.py",
            "gh_gather.py",
            "gh_persist.py",
            "gh_pr_gather.py",
            "prep_coordinator.py",
            "prep_drafter.py",
            "prep_evaluator.py",
            "prep_planner.py",
            "prep_requirements_gatherer.py",
            "prep_researcher.py",
            "prep_resolver.py",
            "prep_review_report.py",
            "prep_slicer.py",
            "prep_workspace_close.py",
            "prep_workspace_open.py",
        ):
            self.assertIn(expected, names, "discovery missed a known dispatched CLI script")

    def test_every_dispatched_cli_script_is_executable(self):
        scripts = _dispatched_cli_scripts()
        self.assertGreater(len(scripts), 0, "no shebang+main() scripts discovered under scripts/")
        non_executable = [
            str(p.relative_to(REPO_ROOT))
            for p in scripts
            if not os.access(str(p), os.X_OK)
        ]
        self.assertEqual(
            non_executable,
            [],
            "the following scripts/*.py are shebang-led with a def main( CLI entry point but are NOT "
            "executable — a bare ${CLAUDE_PLUGIN_ROOT}/scripts/<x>.py dispatch would exit 126: %r "
            "(fix: chmod +x <path>)" % non_executable,
        )

    def test_executable_bit_is_at_least_owner_exec(self):
        # Belt-and-braces over os.access (which honors the *effective* uid/gid of the test runner):
        # assert the literal mode bit too, so a script executable only for the CI/test-runner's own
        # user but not recorded in the file's mode (unlikely, but a distinct failure mode) is caught.
        for path in _dispatched_cli_scripts():
            mode = path.stat().st_mode
            self.assertTrue(
                mode & stat.S_IXUSR,
                "%s has no owner-execute bit set (mode %o)" % (path.relative_to(REPO_ROOT), mode),
            )


if __name__ == "__main__":
    unittest.main()
