"""Drift-check validator for v2 judgment sub-agent prompts (docs/implementation.md S11).

One typed-exception vocabulary spans scripts *and* judgment sub-agents: architecture.md §3's
**closed decision-code set** (which supersedes v1's `skills/_shared/subagent-decision-signal.md`).
This validator is the committed guard that keeps every v2 sub-agent prompt on that one vocabulary —
and it **binds later prompt authors** (S13 planner, S15 drafter, S16 researcher, S18 question pair,
S19 doc-reviewer): their prompts are auto-discovered the moment their v2 skill dir lands, so a new
prompt that names an off-§3 decision code, or that re-cites the retired signal doc, fails this test.

Three assertions, over every discovered v2 sub-agent prompt:

1. **Codes ⊆ §3.** The typed decision codes a prompt names as returnable (via the shared
   `## Exception` / `code:` protocol, plus any decision-code-shaped backticked token) must all be
   members of the §3 closed set. A prompt that returns no §3 code (a reviewer that returns findings,
   the test-selection sub-agent's `COMMAND:`/`RATIONALE:`, the cold-read audit's severity-labelled
   findings) is vacuously conformant.
2. **No retired-doc citation.** No v2 prompt cites `subagent-decision-signal` — §3 supersedes it.
3. **No ref-arithmetic input.** No v2 prompt runs ref arithmetic (`git show <ref>:<path>` /
   `git grep <ref>`) inside a code fence — sub-agents read prep-staged paths / read workspaces, never
   a ref (architecture.md §6/§8). Reuses S10's fence-scoped ref-arithmetic patterns
   (`test_resolver_routing.py::ContractTokenGateTests`), so prose that *describes* the banned form
   (an audit note explaining what v1's ref reads were replaced with) doesn't false-positive.

Discovery keys on the v2 router+playbook anatomy (architecture.md §9): a skill dir carrying a
`playbooks/` subdirectory. While v1 and v2 coexisted this also excluded every frozen v1 skill dir
(none had `playbooks/`), so their still-v1 prompts could legitimately cite the retired doc until
their own cutover step. S20 removed the last of them, so the glob now discovers exactly the nine v2
skills' prompts — and keeps discovering any prompt a future skill adds.

The §3 code set is parsed from `docs/architecture.md` (anchored on the `## §3` heading, backticked
codes lifted from the closed-set list) — resilient to prose edits, strict on the set.
"""

import re
import unittest
from pathlib import Path

from tests.support.retired_tokens import RETIRED_SKILL_DIRS

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"

# A decision code is an ALL-CAPS token: [A-Z] then [A-Z0-9_], length >= 3 (AMBIGUOUS, DOD_MALFORMED).
_CODE_TOKEN = r"[A-Z][A-Z0-9_]{2,}"


# ---------------------------------------------------------------------------
# Parse architecture.md §3's closed decision-code set
# ---------------------------------------------------------------------------


def _section3_text():
    """The §3 section body of docs/architecture.md, from its `## §3` heading to the next `## §`."""
    lines = ARCHITECTURE.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## §3"):
            start = i
            break
    if start is None:  # pragma: no cover - guarded by test_section3_parse_is_sane
        raise AssertionError("docs/architecture.md has no `## §3` heading")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## §"):
            end = j
            break
    return "\n".join(lines[start:end])


def parse_section3_codes():
    """Return the closed decision-code set from §3: the backticked ALL-CAPS codes in the list that
    runs from `Closed decision-code set` to `Adding a code is a contract change` (so §3's `Notices`
    example `DEPS_UNSUPPORTED`, which sits *after* that sentence, is correctly excluded — it is a
    non-blocking notice, not a decision code)."""
    section = _section3_text()
    lo = section.find("Closed decision-code set")
    hi = section.find("Adding a code is a contract change")
    if lo == -1 or hi == -1 or hi <= lo:  # pragma: no cover - guarded by the sanity test
        raise AssertionError("could not locate §3's closed decision-code list boundaries")
    region = section[lo:hi]
    return set(re.findall(r"`(" + _CODE_TOKEN + r")`", region))


SECTION3_CODES = parse_section3_codes()


# ---------------------------------------------------------------------------
# Discover v2 sub-agent prompt files
# ---------------------------------------------------------------------------


def discover_v2_prompt_files():
    """Every v2 judgment sub-agent prompt. A v2 skill dir is one carrying a `playbooks/` subdir
    (architecture.md §9 router+playbook anatomy — no v1 dir has it). Within each, the prompt files
    are `references/*-prompt*.md` and `references/*-sub-agent*.md` (the two S10 naming forms). Written
    as a glob so S13/S15/S16/S18/S19 prompts are picked up automatically when their skill lands."""
    files = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not (skill_dir / "playbooks").is_dir():
            continue
        refs = skill_dir / "references"
        if not refs.is_dir():
            continue
        for pattern in ("*-prompt*.md", "*-sub-agent*.md"):
            files.extend(sorted(refs.glob(pattern)))
    return sorted(set(files))


V2_PROMPT_FILES = discover_v2_prompt_files()


# ---------------------------------------------------------------------------
# Extract the decision codes a prompt names as returnable
# ---------------------------------------------------------------------------


def extract_prompt_codes(text):
    """The typed decision codes a prompt declares as returnable. Union of two surfaces:

    (a) the `code:` field of the shared `## Exception` protocol (the exact surface the caller's main
        loop parses) — matches both `code: A | B | C` and a JSON `"code": "A"`, splitting the
        alternation. This is the authoritative returnable-code declaration.
    (b) decision-code-*shaped* backticked tokens (a multi-part `` `FOO_BAR` `` ALL-CAPS with an
        underscore, or a bare backticked token that is itself a §3 code) — defense in depth for a
        code named only in a `Raise:` bullet or closed-set prose.

    Non-code ALL-CAPS prose never matches: severities (`BLOCKER`/`SUGGESTION`/`NIT`) are bold, not
    backticked; the test-selection `COMMAND:`/`RATIONALE:` headers are not backticked; the resolver
    review loop's guard-rail card kinds are lowercase (`deadlock`, `architectural`, …) — a deliberately
    *separate* rich-card protocol, not a §3 decision code.
    """
    codes = set()
    for line in text.splitlines():
        m = re.match(r'\s*"?code"?\s*:\s*(.+)$', line)
        if m:
            codes.update(re.findall(_CODE_TOKEN, m.group(1)))
    for tok in re.findall(r"`(" + _CODE_TOKEN + r")`", text):
        if "_" in tok or tok in SECTION3_CODES:
            codes.add(tok)
    return codes


# ---------------------------------------------------------------------------
# Fence tracking (S10 precedent: distinguish a real command from prose about one)
# ---------------------------------------------------------------------------


def _fence_stripped_lines(path):
    in_fence = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            yield i, line, in_fence
            continue
        yield i, line, in_fence


class Section3ParseTests(unittest.TestCase):
    """Guard the §3 parser itself, so the code-subset assertions below are anchored to a real set and
    a silent empty parse can never make them pass vacuously."""

    def test_section3_parse_is_sane(self):
        self.assertGreaterEqual(
            len(SECTION3_CODES), 10, "expected §3 to define >= 10 decision codes, got %r" % (SECTION3_CODES,)
        )
        # Anchor tokens that must be present (the sub-agent-raised codes + a few script-emitted ones).
        for anchor in (
            "AUTH_REQUIRED",
            "PHASES_MALFORMED",
            "PLAN_MISSING",
            "THREAD_SUPERSEDED_PLAN",
            "AMBIGUOUS",
            "BLOCKED_ON_USER",
        ):
            self.assertIn(anchor, SECTION3_CODES, "§3 parse dropped the anchor code %r" % anchor)
        # The §3 `Notices` example must NOT leak into the decision-code set.
        self.assertNotIn("DEPS_UNSUPPORTED", SECTION3_CODES, "a notice leaked into the code set")


class PromptDiscoveryTests(unittest.TestCase):
    def test_discovery_finds_the_landed_v2_prompts_and_excludes_v1(self):
        found = {p.relative_to(SKILLS_DIR).as_posix() for p in V2_PROMPT_FILES}
        # The S10 resolver prompts must all be discovered. The review fix round is deliberately
        # absent: since 4.11.0 it runs in the main conversation, so it is a procedure reference, not
        # a sub-agent prompt, and its file name matches neither discovery glob.
        for expected in (
            "resolver/references/state-distiller-prompt.md",
            "resolver/references/issue-audit-prompt.md",
            "resolver/references/cold-read-audit-prompt.md",
            "resolver/references/fix-design-prompt.md",
            "resolver/references/dismissal-check-prompt.md",
            "resolver/references/blast-radius-judgment-prompt.md",
            "resolver/references/test-selection-sub-agent.md",
        ):
            self.assertIn(expected, found, "discovery must include %r" % expected)
        self.assertNotIn(
            "resolver/references/review-fix-round.md",
            found,
            "the main-loop fix-round procedure must not be discovered as a sub-agent prompt",
        )
        # The S18 question-status reader converged onto the §3 vocabulary and must be discovered
        # at its v2 path.
        self.assertIn(
            "question-sweep/references/question-status-reader-prompt.md",
            found,
            "the question-status reader must be discovered at its v2 path",
        )
        # The #16 cut reviewer carries the relocated ordering + sizing judgment for BOTH altitudes;
        # discovery is what subjects it to the §3-code and ref-arithmetic gates below, so a rename
        # that dropped it out of the glob must fail here rather than silently ungate it.
        self.assertIn(
            "slicer/references/cut-reviewer-prompt.md",
            found,
            "the slicer's cut reviewer must be discovered",
        )
        # #95's security review, shared by the resolver's finalisation and the evaluator's S4.5.
        self.assertIn(
            "evaluator/references/security-review-prompt.md",
            found,
            "the security review prompt must be discovered",
        )
        # No retired v1 skill dir may reappear as a discovery source (S20 removed them all).
        for p in found:
            self.assertFalse(
                p.split("/")[0] in RETIRED_SKILL_DIRS,
                "a retired v1 skill dir was discovered as v2: %r" % p,
            )


class PromptCodeConformanceTests(unittest.TestCase):
    """Assertion 1: every v2 prompt's returnable decision codes ⊆ §3's closed set."""

    def test_every_v2_prompt_names_only_section3_codes(self):
        self.assertTrue(V2_PROMPT_FILES, "no v2 sub-agent prompt files discovered — glob is broken")
        for path in V2_PROMPT_FILES:
            codes = extract_prompt_codes(path.read_text(encoding="utf-8"))
            stray = codes - SECTION3_CODES
            self.assertEqual(
                stray,
                set(),
                "%s names decision code(s) outside architecture.md §3's closed set: %r "
                "(add the code to §3 + the router rule, or converge to an existing §3 name)"
                % (path.relative_to(REPO_ROOT), sorted(stray)),
            )

    def test_state_distiller_codes_are_exactly_its_three_section3_codes(self):
        # A live-fixture anchor: the one discovered prompt that DOES return §3 codes must extract to
        # exactly the three the distiller raises, all §3 members — so extract_prompt_codes provably
        # lifts real codes (a parser that silently returned {} would make the subset check vacuous).
        distiller = SKILLS_DIR / "resolver" / "references" / "state-distiller-prompt.md"
        codes = extract_prompt_codes(distiller.read_text(encoding="utf-8"))
        self.assertEqual(
            codes,
            {"THREAD_SUPERSEDED_PLAN", "PHASES_MALFORMED", "AMBIGUOUS"},
            "distiller returnable codes drifted: %r" % (sorted(codes),),
        )


class PromptCitationTests(unittest.TestCase):
    """Assertion 2: no v2 prompt cites the retired signal doc."""

    def test_no_v2_prompt_cites_the_retired_signal_doc(self):
        for path in V2_PROMPT_FILES:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "subagent-decision-signal",
                text,
                "%s still cites the retired subagent-decision-signal doc — cite architecture.md §3 "
                "(the one vocabulary that supersedes it)" % path.relative_to(REPO_ROOT),
            )


class PromptRefInputTests(unittest.TestCase):
    """Assertion 3: no v2 prompt runs ref arithmetic in a code fence (paths in, never refs — §6/§8).
    Reuses the S10 fence-scoped patterns; a prose note describing the banned v1 form is allowed."""

    _REF_ARITH = re.compile(r"\bgit\s+show\s+\S+:\S|\bgit\s+grep\b[^\n]*\s+[a-z]+/[^\s-]")

    def test_no_ref_arithmetic_in_any_v2_prompt_code_fence(self):
        for path in V2_PROMPT_FILES:
            for i, line, in_fence in _fence_stripped_lines(path):
                if not in_fence:
                    continue
                hit = self._REF_ARITH.search(line)
                self.assertIsNone(
                    hit,
                    "ref-arithmetic in a code fence at %s:%d — %r (sub-agents read prep-staged "
                    "paths / read workspaces, never a ref)"
                    % (path.relative_to(REPO_ROOT), i, hit.group(0) if hit else None),
                )


class DeliberateViolationTraceTests(unittest.TestCase):
    """Proof the validator CATCHES a violation: run the extraction/citation logic against synthetic
    prompt text that breaks each rule, and assert the check would flag it. (String-level, so it never
    writes a bad file to disk — it exercises the exact predicates the file-level tests use.)"""

    def test_off_section3_code_would_be_caught(self):
        bad = "## Exception\ncode: THREAD_SUPERSEDED_PLAN | FABRICATED_CODE\n"
        stray = extract_prompt_codes(bad) - SECTION3_CODES
        self.assertEqual(stray, {"FABRICATED_CODE"}, "extractor must surface an off-§3 code")

    def test_backticked_off_section3_code_would_be_caught(self):
        bad = "Raise the `MADE_UP_SIGNAL` exception when the thread is empty.\n"
        self.assertIn("MADE_UP_SIGNAL", extract_prompt_codes(bad) - SECTION3_CODES)

    def test_retired_doc_citation_would_be_caught(self):
        bad = "see [`../../_shared/subagent-decision-signal.md`](../../_shared/subagent-decision-signal.md)"
        self.assertIn("subagent-decision-signal", bad)

    def test_ref_arithmetic_would_be_caught(self):
        self.assertRegex("git show origin/main:docs/prd.md", PromptRefInputTests._REF_ARITH)
        self.assertRegex("git grep pattern epic/12-foo/src", PromptRefInputTests._REF_ARITH)

    def test_severity_and_section_headers_do_not_false_positive(self):
        # The tokens most likely to be mistaken for codes in real prompts must extract to nothing.
        benign = (
            "- Severity: **BLOCKER** | **SUGGESTION** | **NIT**\n"
            "COMMAND:\n(none)\nRATIONALE:\nDiff touches only docs.\n"
            '"kind": "deadlock" | "architectural" | "verification_failure"\n'
        )
        self.assertEqual(extract_prompt_codes(benign), set(), "benign prompt prose must yield no codes")



class BlastRadiusJudgmentDispatchTests(unittest.TestCase):
    """#145: the evaluator's S4.6 dispatches the resolver's blast-radius judgment prompt too, over the PR's
    cumulative diff, so its cited sites become review threads."""

    def test_the_prompt_names_the_evaluator_dispatch_site(self):
        text = " ".join((SKILLS_DIR / "resolver" / "references" / "blast-radius-judgment-prompt.md")
                        .read_text(encoding="utf-8").split())
        self.assertIn("The evaluator's S4.6 dispatches it too", text)
        self.assertIn("with `<<final>>` true over the PR's cumulative diff", text)


if __name__ == "__main__":
    unittest.main()
