"""Self-tests for tests/support/gitsandbox.py: origin+clone creation and guaranteed cleanup."""

import os
import subprocess
import unittest

from tests.support import gitsandbox


class MkOriginMkCloneTests(unittest.TestCase):
    def test_origin_and_clone_exist_and_are_related(self):
        origin = gitsandbox.mk_origin()
        self.addCleanup(origin.cleanup)
        self.assertTrue(os.path.isdir(origin.path))
        self.assertEqual(str(origin.path), os.path.realpath(origin.path))

        clone = gitsandbox.mk_clone(origin)
        self.addCleanup(clone.cleanup)
        self.assertTrue(os.path.isdir(clone.path))
        self.assertEqual(str(clone.path), os.path.realpath(clone.path))
        self.assertTrue(os.path.isdir(os.path.join(clone.path, ".git")))

        # The clone's origin remote must actually point back at the bare repo.
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=clone.path,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        self.assertEqual(result.stdout.strip(), str(origin.path))

        # The seed commit must be present and checked out.
        readme = os.path.join(clone.path, "README.md")
        self.assertTrue(os.path.isfile(readme))

    def test_cleanup_removes_both_directories(self):
        origin = gitsandbox.mk_origin()
        clone = gitsandbox.mk_clone(origin)
        origin_path, clone_path = origin.path, clone.path
        self.assertTrue(os.path.isdir(origin_path))
        self.assertTrue(os.path.isdir(clone_path))

        clone.cleanup()
        origin.cleanup()

        self.assertFalse(os.path.exists(clone_path))
        self.assertFalse(os.path.exists(origin_path))

    def test_multiple_clones_of_same_origin_are_independent(self):
        origin = gitsandbox.mk_origin()
        self.addCleanup(origin.cleanup)
        clone_a = gitsandbox.mk_clone(origin)
        self.addCleanup(clone_a.cleanup)
        clone_b = gitsandbox.mk_clone(origin)
        self.addCleanup(clone_b.cleanup)
        self.assertNotEqual(clone_a.path, clone_b.path)



def _git(cwd, *args):
    return subprocess.run(
        ["git"] + list(args), cwd=str(cwd), capture_output=True, encoding="utf-8", check=True
    ).stdout.strip()


class TemplateTests(unittest.TestCase):
    """`mk_origin` / `mk_clone` copy a per-process template; the copies must behave like the
    plain `git init` + `git clone` sandboxes they replace."""

    def _origin(self, **kwargs):
        origin = gitsandbox.mk_origin(**kwargs)
        self.addCleanup(origin.cleanup)
        return origin

    def _clone(self, origin):
        clone = gitsandbox.mk_clone(origin)
        self.addCleanup(clone.cleanup)
        return clone

    def test_two_origins_are_distinct_paths_with_the_same_seed_commit(self):
        first, second = self._origin(), self._origin()
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(str(second.path), os.path.realpath(second.path))
        self.assertEqual(
            _git(first.path, "rev-parse", "main"), _git(second.path, "rev-parse", "main")
        )
        self.assertEqual(first.template_key, "main")

    def test_a_copied_clone_points_at_its_own_origin_and_pushes_there_only(self):
        origin, other = self._origin(), self._origin()
        clone = self._clone(origin)
        self.assertEqual(_git(clone.path, "remote", "get-url", "origin"), str(origin.path))
        self.assertEqual(_git(clone.path, "config", "user.name"), "gh-pipeline sandbox")
        self.assertEqual(_git(clone.path, "status", "--porcelain"), "")

        (clone.path / "change.txt").write_text("x\n", encoding="utf-8")
        _git(clone.path, "add", "change.txt")
        _git(clone.path, "commit", "-m", "change")
        _git(clone.path, "push", "origin", "HEAD:main")
        pushed = _git(clone.path, "rev-parse", "HEAD")

        self.assertEqual(_git(origin.path, "rev-parse", "main"), pushed)
        self.assertNotEqual(_git(other.path, "rev-parse", "main"), pushed)
        template = gitsandbox._TEMPLATES["main"]
        self.assertNotEqual(_git(template.origin_path, "rev-parse", "main"), pushed)

    def test_a_clone_of_a_pushed_origin_sees_the_push(self):
        """The pristine check falls back to a real clone once an origin has moved."""
        origin = self._origin()
        writer = self._clone(origin)
        (writer.path / "pushed.txt").write_text("y\n", encoding="utf-8")
        _git(writer.path, "add", "pushed.txt")
        _git(writer.path, "commit", "-m", "pushed")
        _git(writer.path, "push", "origin", "HEAD:main")

        reader = self._clone(origin)
        self.assertEqual(
            _git(reader.path, "rev-parse", "HEAD"), _git(writer.path, "rev-parse", "HEAD")
        )
        self.assertTrue((reader.path / "pushed.txt").is_file())

    def test_another_initial_branch_gets_its_own_template(self):
        origin = self._origin(initial_branch="trunk")
        self.assertEqual(origin.template_key, "trunk")
        self.assertEqual(_git(origin.path, "symbolic-ref", "HEAD"), "refs/heads/trunk")
        clone = self._clone(origin)
        self.assertEqual(_git(clone.path, "rev-parse", "--abbrev-ref", "HEAD"), "trunk")
        self.assertEqual(_git(clone.path, "remote", "get-url", "origin"), str(origin.path))


class GitSandboxContextManagerTests(unittest.TestCase):
    def test_cleans_up_on_normal_exit(self):
        with gitsandbox.git_sandbox() as (origin, clone):
            origin_path, clone_path = origin.path, clone.path
            self.assertTrue(os.path.isdir(origin_path))
            self.assertTrue(os.path.isdir(clone_path))
        self.assertFalse(os.path.exists(origin_path))
        self.assertFalse(os.path.exists(clone_path))

    def test_cleans_up_even_when_body_raises(self):
        captured = {}
        with self.assertRaises(RuntimeError):
            with gitsandbox.git_sandbox() as (origin, clone):
                captured["origin_path"] = origin.path
                captured["clone_path"] = clone.path
                raise RuntimeError("deliberate failure inside the sandbox body")
        self.assertFalse(os.path.exists(captured["origin_path"]))
        self.assertFalse(os.path.exists(captured["clone_path"]))


class AddCleanupPropagatesOnFailureTests(unittest.TestCase):
    """addCleanup-registered sandbox cleanup must run even when the test body itself fails —
    proven here by running a throwaway failing test in a nested subprocess (so this test suite's
    own result isn't polluted by a deliberately-failing case) and then checking the sandbox paths
    it printed no longer exist.
    """

    def test_addcleanup_runs_after_test_failure(self):
        probe_script = (
            "import sys, os, unittest\n"
            "sys.path.insert(0, %r)\n"
            "from tests.support import gitsandbox\n"
            "class Probe(unittest.TestCase):\n"
            "    def test_fails_after_creating_sandbox(self):\n"
            "        origin = gitsandbox.mk_origin()\n"
            "        self.addCleanup(origin.cleanup)\n"
            "        clone = gitsandbox.mk_clone(origin)\n"
            "        self.addCleanup(clone.cleanup)\n"
            "        print('PATHS', origin.path, clone.path)\n"
            "        raise AssertionError('deliberate failure')\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        ) % (gitsandbox.__file__.rsplit(os.sep + "tests" + os.sep, 1)[0],)

        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(probe_script)
            probe_path = fh.name
        self.addCleanup(lambda: os.remove(probe_path))

        result = subprocess.run(
            ["python3", probe_path, "-v"],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, "the deliberately-failing probe must fail")

        combined = result.stdout + result.stderr
        line = next(l for l in combined.splitlines() if l.startswith("PATHS "))
        _, origin_path, clone_path = line.split()
        self.assertFalse(
            os.path.exists(origin_path), "origin dir must be cleaned up despite test failure"
        )
        self.assertFalse(
            os.path.exists(clone_path), "clone dir must be cleaned up despite test failure"
        )


if __name__ == "__main__":
    unittest.main()
