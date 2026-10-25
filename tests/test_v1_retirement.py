"""S20 DoD box 1, as a committed test: v1 is gone and nothing still points at it.

The plan's box-1 grep runs the retired-name literals — the seven v1 skill directory names, the
executor sub-agent, the hook-runner script — over `skills/ agents/ scripts/ tests/ README.md
.claude-plugin/ CLAUDE.md`. Those literals live in `tests/support/retired_tokens.py`
(`RETIRED_NAME_PATTERNS`), which is why this file never spells one: the token table is the single
place they are allowed to appear, so the scan below can hold every *other* file to zero.

`docs/specs/**` and `docs/implementation.md` are exempt as the historical record; `agents/` no
longer exists (its absence is itself asserted below). The scan here is that grep plus the precise
exemptions recorded in `tests/support/retired_tokens.py` — each licensing one *known* frozen string
in one file, never a token generally, so a fresh stale reference in an exempted file still fails.

The scan also holds the plugin's pre-5.0.0 namespace on each live skill (`RETIRED_NAMESPACE_TOKENS`)
to zero over the same paths, so a `github-pipeline:<skill>` invocation missed by the `ctrl-freaq`
rename fails here.

Also asserted: no `*.sh` remains under `scripts/`, no v1 skill directory remains under `skills/`,
and the exemption list is live (every entry still matches something — a stale exemption is a hole).
"""

import re
import unittest
from pathlib import Path

from tests.support.retired_tokens import (
    EXECUTOR_AGENT,
    EXEMPTIONS,
    LIVE_SKILL_NAMES,
    RETIRED_NAME_PATTERNS,
    RETIRED_NAMESPACE_TOKENS,
    RETIRED_SHELL_SCRIPTS,
    RETIRED_SKILL_DIRS,
    line_is_exempt,
)

_V1_PLANNER = RETIRED_SKILL_DIRS[2]  # the v1 planner dir name, composed — never spelled here
_ISSUE_PREFIX = RETIRED_NAME_PATTERNS[0]

REPO_ROOT = Path(__file__).resolve().parent.parent

# The DoD's scanned set. `agents/` is listed for fidelity and skipped when absent.
SCANNED = ("skills", "agents", "scripts", "tests", "README.md", ".claude-plugin", "CLAUDE.md")

_SKIP_DIRS = {"__pycache__", ".git"}
# Binary-ish / generated files never carry prose references.
_SKIP_SUFFIXES = {".pyc", ".pyo"}


def _iter_scanned_files():
    for entry in SCANNED:
        path = REPO_ROOT / entry
        if not path.exists():
            continue
        if path.is_file():
            yield path
            continue
        for child in sorted(path.rglob("*")):
            if not child.is_file():
                continue
            if any(part in _SKIP_DIRS for part in child.parts):
                continue
            if child.suffix in _SKIP_SUFFIXES:
                continue
            yield child


def _scan():
    """Every non-exempt (rel_path, line_no, token, line) hit across the scanned paths."""
    hits = []
    for path in _iter_scanned_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - no binary fixtures today
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for token in RETIRED_NAME_PATTERNS + RETIRED_NAMESPACE_TOKENS:
                if token in line and not line_is_exempt(rel, token, line):
                    hits.append((rel, i, token, line.strip()))
    return hits


class RetiredNameScanTests(unittest.TestCase):
    def test_zero_retired_v1_name_hits_in_the_scanned_paths(self):
        hits = _scan()
        self.assertEqual(
            hits,
            [],
            "stale v1 reference(s):\n"
            + "\n".join("  %s:%d [%s] %s" % h for h in hits),
        )

    def test_the_scan_actually_reads_the_tree(self):
        # Anti-vacuity: the walk must reach real prompt/script/test files, or the assertion above
        # would pass on an empty file list.
        scanned = {p.relative_to(REPO_ROOT).as_posix() for p in _iter_scanned_files()}
        for expected in ("CLAUDE.md", "README.md", "skills/resolver/SKILL.md", "scripts/parse.py"):
            self.assertIn(expected, scanned, "the retirement scan never reached %r" % expected)
        self.assertGreater(len(scanned), 100, "scan set implausibly small: %d files" % len(scanned))

    def test_a_planted_violation_would_be_caught(self):
        # The predicate, exercised on synthetic text composed from the token table (never written
        # to disk, never spelled literally here).
        self.assertFalse(
            line_is_exempt(
                "skills/resolver/SKILL.md", EXECUTOR_AGENT, "delegate to `%s`" % EXECUTOR_AGENT
            ),
            "a fresh executor-agent reference must not be exempt",
        )
        self.assertFalse(
            line_is_exempt(
                "skills/planner/references/plan-schema.md",
                _ISSUE_PREFIX,
                "run the `%s` skill first" % _V1_PLANNER,
            ),
            "an exempted file's *other* v1 mentions must still be caught",
        )
        self.assertTrue(
            line_is_exempt(
                "skills/planner/references/plan-schema.md",
                _ISSUE_PREFIX,
                "_Authored by `%s` and verified in <N> review pass(es)." % _V1_PLANNER,
            ),
            "the frozen artifact footer must stay exempt",
        )
        namespace_token = RETIRED_NAMESPACE_TOKENS[0]
        self.assertFalse(
            line_is_exempt(
                "skills/resolver/SKILL.md", namespace_token, "/%s #1" % namespace_token
            ),
            "a stale pre-5.0.0 invocation must not be exempt",
        )

    def test_live_skill_names_match_the_skill_directories(self):
        on_disk = {
            p.name for p in (REPO_ROOT / "skills").iterdir() if p.is_dir() and p.name != "_shared"
        }
        self.assertEqual(set(LIVE_SKILL_NAMES), on_disk)


class ExemptionHygieneTests(unittest.TestCase):
    """Every exemption must still be earned — a stale one is an unguarded hole."""

    def test_every_exemption_still_matches_its_file(self):
        for path, token, fragment, why in EXEMPTIONS:
            target = REPO_ROOT / path
            self.assertTrue(target.is_file(), "exemption names a missing file: %s (%s)" % (path, why))
            if fragment is None:
                continue
            text = target.read_text(encoding="utf-8")
            self.assertTrue(
                fragment in text,
                "stale exemption: %s no longer contains %r (%s)" % (path, fragment, why),
            )
            self.assertIn(token, fragment, "exemption fragment must contain its own token: %r" % (fragment,))


class RemovedSurfaceTests(unittest.TestCase):
    """Every check here reads a *directory listing* rather than stat-ing each path individually.
    That is deliberate: on a bind-mounted working tree (the documented Linux container leg in
    `tests/README.md` runs the host repo through a shared mount) a freshly deleted path can still
    answer a per-path `stat` from the mount's cache for a short window, while a `readdir` of its
    parent is always current. Listing keeps the assertion about the tree, not about mount timing."""

    @staticmethod
    def _names(rel):
        target = REPO_ROOT / rel
        return set(p.name for p in target.iterdir()) if target.is_dir() else set()

    def test_no_shell_scripts_remain_under_scripts(self):
        found = sorted(p.name for p in (REPO_ROOT / "scripts").glob("*.sh"))
        self.assertEqual(found, [], "v1 shell scripts still present: %r" % found)

    def test_each_named_v1_script_is_gone(self):
        present = self._names("scripts")
        still_there = sorted(n for n in RETIRED_SHELL_SCRIPTS if n in present)
        self.assertEqual(still_there, [], "v1 script(s) still present: %r" % still_there)

    def test_the_executor_agent_directory_is_gone(self):
        self.assertNotIn(
            "agents",
            self._names("."),
            "agents/ still exists — the v1 executor sub-agent retired at S20",
        )

    def test_no_v1_skill_directory_remains(self):
        present = self._names("skills")
        still_there = sorted(n for n in RETIRED_SKILL_DIRS if n in present)
        self.assertEqual(
            still_there, [], "v1 skill director(ies) still present: %r" % still_there
        )

    def test_only_the_fifteen_v2_skill_directories_remain(self):
        found = sorted(p.name for p in (REPO_ROOT / "skills").iterdir() if p.is_dir())
        self.assertEqual(
            found,
            sorted(
                [
                    "_shared",
                    "coordinator",
                    "doc-reviewer",
                    "drafter",
                    "evaluator",
                    "planner",
                    "question-resolver",
                    "question-sweep",
                    "requirements-gatherer",
                    "researcher",
                    "resolver",
                    "review-report",
                    "setup",
                    "slicer",
                    "workspace-close",
                    "workspace-open",
                ]
            ),
            "skills/ must hold exactly _shared + the fifteen prd.md §2 skills (v3 added "
            "workspace-open/workspace-close; #17 added slicer; 3.5.0 added "
            "requirements-gatherer; 4.39.0 added coordinator; #98 added review-report)",
        )

    def test_the_retired_shared_contracts_are_gone(self):
        self.assertNotIn(
            "subagent-decision-signal.md",
            self._names("skills/_shared"),
            "subagent-decision-signal.md is superseded by architecture.md §3's closed code set",
        )

    def test_worktree_lifecycle_keeps_the_external_block_format_only(self):
        # The mechanics half folded into workspace.py (architecture.md §6/§11); the consuming-repo
        # block format stays (prd.md §7).
        text = (REPO_ROOT / "skills" / "_shared" / "worktree-lifecycle.md").read_text(encoding="utf-8")
        for marker in ("<!-- worktree-setup -->", "<!-- /worktree-setup -->",
                       "<!-- worktree-teardown -->", "<!-- /worktree-teardown -->"):
            self.assertIn(marker, text, "the external block format must survive: %r" % marker)
        for mechanic in ("git worktree add", "git worktree list", "exit code", "--reused"):
            self.assertNotIn(
                mechanic,
                text,
                "worktree-lifecycle.md still restates runner mechanics (%r) — workspace.py owns them" % mechanic,
            )


class DanglingSkillCitationTests(unittest.TestCase):
    """No `scripts/*.py` docstring or comment cites a **deleted** v1 `SKILL.md` by locator.

    The retirement scan above can't see these: `SKILL.md` is not a retired-*name* token, so a
    provenance note reading `(SKILL.md:645-651)` or `(SKILL.md §P3.1)` survived S20's sweep while
    pointing at a file that no longer exists. The frozen `docs/specs/<skill>.md` specs are where the
    v1 record lives — and they carry those v1 line numbers themselves — so a script cites the spec
    section, and the spec carries the line cite.

    A **located** citation is `SKILL.md` followed by a line number or a `§` anchor. It is allowed in
    exactly two forms:

    1. **Sibling form** — the same line also names a `docs/specs/<name>.md` path, i.e. the frozen
       spec is the primary citation and the v1 locator rides along as secondary provenance.
    2. **Live v2 router** — the citation is written as a `skills/<name>/SKILL.md` path that exists
       on disk (e.g. `prep_planner.py`'s pointer at `skills/planner/SKILL.md` §2's routing table).

    An unlocated mention ("no SKILL.md invokes this"; "v1 skills parsed these inline in each
    SKILL.md") names no file and dangles nothing, so it is out of scope by construction.
    """

    # `SKILL.md:12` / `SKILL.md §5.4` / `` `…/SKILL.md` §2 `` — an optional closing backtick and
    # whitespace may sit between the filename and its locator.
    _LOCATED = re.compile(r"SKILL\.md`?\s*(?::\s*\d|§)")
    _SPEC_SIBLING = re.compile(r"docs/specs/[a-z-]+\.md")
    _V2_ROUTER_PATH = re.compile(r"skills/([a-z-]+)/SKILL\.md")

    @classmethod
    def citation_is_allowed(cls, line):
        """The predicate, on one line of source. Shared with the planted-violation test."""
        if cls._SPEC_SIBLING.search(line):
            return True
        for name in cls._V2_ROUTER_PATH.findall(line):
            if (REPO_ROOT / "skills" / name / "SKILL.md").is_file():
                return True
        return False

    def test_no_dangling_v1_skill_md_citation_in_scripts(self):
        offenders = []
        for path in sorted((REPO_ROOT / "scripts").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not self._LOCATED.search(line):
                    continue
                if self.citation_is_allowed(line):
                    continue
                offenders.append(
                    "  %s:%d %s" % (path.relative_to(REPO_ROOT).as_posix(), i, line.strip())
                )
        self.assertEqual(
            offenders,
            [],
            "dangling v1 SKILL.md citation(s) — retarget to the frozen docs/specs/<skill>.md "
            "section, or name the behavior:\n" + "\n".join(offenders),
        )

    def test_the_scan_reaches_every_script(self):
        # Anti-vacuity: the walk must reach the prep scripts, which are where the citations lived.
        seen = {
            p.name
            for p in (REPO_ROOT / "scripts").rglob("*.py")
            if "__pycache__" not in p.parts
        }
        for expected in ("prep_drafter.py", "prep_resolver.py", "parse.py", "workspace.py"):
            self.assertIn(expected, seen, "the citation scan never reached %r" % expected)

    def test_a_planted_violation_would_be_caught(self):
        bare = "# the prior-PR state table (SKILL.md:645-651), carried as the row mapping."
        pid = "    fallback chain per SKILL.md §P3.1's documented worst-case."
        for line in (bare, pid):
            self.assertRegex(line, self._LOCATED, "the locator pattern must match %r" % line)
            self.assertFalse(
                self.citation_is_allowed(line), "a bare v1 locator must not be allowed: %r" % line
            )
        # Both allowed forms are recognized.
        self.assertTrue(
            self.citation_is_allowed(
                "# PR-type detection (docs/specs/evaluator.md; SKILL.md:254-256)."
            ),
            "the sibling form (spec named on the same line) must be allowed",
        )
        self.assertTrue(
            self.citation_is_allowed("    (`skills/planner/SKILL.md` §2's routing table):"),
            "a live v2 router path must be allowed",
        )
        # A v2-shaped path whose skill dir no longer exists (composed, never spelled — this file is
        # inside the retirement scan's own scope).
        self.assertFalse(
            self.citation_is_allowed("    (`skills/%s/SKILL.md` §2):" % _V1_PLANNER),
            "a v2-shaped path whose file does not exist must NOT be allowed",
        )


class RuntimeDependencyTests(unittest.TestCase):
    """S20 DoD box 3: the runtime-dependency docs name only python3 / git / gh."""

    _DOCS = ("README.md", "CLAUDE.md", "tests/README.md")
    # `jq`/`bash` as a *declared dependency*. Word-boundary'd so `bash`-in-a-URL or the word
    # "jquery" can't false-positive, and so prose about a retired v1 dependency is still caught.
    _DROPPED = re.compile(r"\bjq\b|\bbash\b", re.IGNORECASE)

    def test_no_jq_or_bash_dependency_claim_in_the_dependency_docs(self):
        for rel in self._DOCS:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            in_fence = False
            for i, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    # A shell invocation inside a code fence (`docker run ... bash -c`) is a
                    # command, not a declared dependency.
                    continue
                hit = self._DROPPED.search(line)
                self.assertIsNone(
                    hit,
                    "%s:%d names a dropped runtime dependency (%r): %s"
                    % (rel, i, hit.group(0) if hit else None, line.strip()),
                )

    def test_the_dependency_docs_name_the_three_that_remain(self):
        for rel in ("README.md", "CLAUDE.md"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for dep in ("python3", "git", "gh"):
                self.assertIn(dep, text, "%s must name the %r runtime dependency" % (rel, dep))


class ManifestTests(unittest.TestCase):
    def test_manifests_parse_and_carry_a_v2_or_later_version(self):
        # v1 shipped as 1.x; the rewrite re-baselined at 2.0.0 and the v3 workspace-model
        # inversion bumped the major again. The retirement claim is "never 1.x again", not a
        # forever-pin to one major — and BOTH manifests must carry the same version (the updater
        # compares manifest version strings, so a split would strand consumers).
        import json

        for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
            data = json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict, "%s must be a JSON object" % rel)
        plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        version = plugin.get("version", "")
        self.assertRegex(version, r"^[2-9]\d*\.\d+\.\d+$", "plugin.json version must be >= 2.0.0")
        self.assertEqual(
            marketplace["plugins"][0]["version"], version,
            "both manifests must carry the same version, bumped together",
        )


if __name__ == "__main__":
    unittest.main()
