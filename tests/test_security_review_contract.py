"""#95: the security review's shared contract (skills/_shared/security-review.md) and its sub-agent
prompt (skills/evaluator/references/security-review-prompt.md).

The record is script-parsed (`parse.scan_security`), so its disposition table must name exactly the
scanner's closed set. The prompt must stay stack-agnostic (CLAUDE.md "Validators" 5) and carry its inputs
and output grammar. And DoD 3 — a dismissal reaches the operator, a finding is never silently dropped — is
prose, so its load-bearing sentences are pinned here.
"""

import re
import unittest
from pathlib import Path

from scripts import parse

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = REPO_ROOT / "skills" / "_shared" / "security-review.md"
PROMPT = REPO_ROOT / "skills" / "evaluator" / "references" / "security-review-prompt.md"

# CLAUDE.md "Validators" 5, and the stacks a labelled example may name.
_STACK_RE = re.compile(r"swift|xcode|xcb\.sh|foodjournal|rails|rspec|pytest", re.IGNORECASE)
# Names only: no stack whose name is also a common English word ("go", "rust"), which would count as a stack.
_STACK_NAMES = ("python", "rails", "ruby", "swift", "node", "java", "kotlin", "php", "javascript", "typescript")


def _flat(path):
    return " ".join(path.read_text(encoding="utf-8").split())


class SecurityContractTests(unittest.TestCase):
    def test_the_disposition_table_names_exactly_the_scanners_set(self):
        text = CONTRACT.read_text(encoding="utf-8")
        table = text[text.index("## Dispositions (closed set)"):text.index("## The procedure")]
        rows = re.findall(r"^\| `([a-z]+)", table, re.MULTILINE)
        self.assertEqual(tuple(rows), parse.SECURITY_DISPOSITIONS)

    def test_a_dismissal_is_the_operators_answer_on_a_consent_card(self):
        flat = _flat(CONTRACT)
        self.assertIn('`header: "Security"`', flat)
        self.assertIn("**only as the operator's answer**", flat)
        self.assertIn("the note led by `operator: <reason>`", flat)
        self.assertIn('"Consent vs judgment"', flat)

    def test_nothing_leaves_the_record_silently(self):
        flat = _flat(CONTRACT)
        self.assertIn("`premise false: <evidence>`. It **still goes on the card**", flat)
        self.assertIn("**An `unparsed` line counts as `open`**", flat)
        self.assertIn("never fixed silently", flat)
        self.assertIn("**never approves while an entry is `open`**", flat)

    def test_a_re_reported_finding_keeps_its_id_and_a_dismissal_is_not_re_asked(self):
        flat = _flat(CONTRACT)
        self.assertIn("Same anchor and same claim → the same finding, which keeps its id", flat)
        self.assertIn("A recorded dismissal is not re-asked", flat)
        self.assertIn("**Matching errs toward asking**", flat)
        self.assertIn("an id is never reused or renumbered", flat)

    def test_only_a_dismiss_proposal_takes_a_question(self):
        # Cold read: steps 4 and 5 disagreed on whether a fix proposal gets a question.
        flat = _flat(CONTRACT)
        self.assertIn("One question per dismiss proposal", flat)
        self.assertIn("so it takes no question", flat)
        self.assertNotIn("One question per finding", flat)
        self.assertIn("a duplicated id must never let a `dismissed` or `fixed` line shadow an `open` one", flat)

    def test_the_one_record_write_is_what_later_restages_start_from(self):
        # Cold read: the S5.5 write and the post-merge restage did not carry S4.5's record.
        flat = _flat(CONTRACT)
        self.assertIn("when it has none, S4.5's write stands alone", flat)
        self.assertIn("a playbook's post-merge restage starts from it", flat)
        evaluator = REPO_ROOT / "skills" / "evaluator"
        self.assertIn("the one S4.5/S5.5 record write", _flat(evaluator / "playbooks" / "standard.md"))
        self.assertIn("and the `## Security review` section when S4.5 ran the pass or changed an entry",
                      _flat(evaluator / "references" / "polish-adjudication.md"))

    def test_the_skip_needs_the_head_and_the_own_account(self):
        flat = _flat(CONTRACT)
        self.assertIn("`Reviewed:` equals the gated head **and** `facts.self_review` is true", flat)
        self.assertIn("Never write it after a fix commit", flat)

    def test_the_record_is_the_first_record_shaped_section_and_a_lookalike_is_never_touched(self):
        # Epic DoD 15: a consuming repo's own section under the heading is never read or rewritten.
        flat = _flat(CONTRACT)
        self.assertIn("The record is the **first** section under that heading", flat)
        self.assertIn("a `- Reviewed:` line or a `SEC-`-led line", flat)
        self.assertIn("`facts.security.other_sections`", flat)
        self.assertIn("They are never read and never rewritten", flat)
        self.assertIn("**Rewrite only the record**, the section `facts.security.record` names", flat)
        self.assertIn("add a new section headed exactly `## Security review`", flat)
        self.assertIn("**Never rename, fill or remove a section `other_sections` lists**", flat)

    def test_the_pass_proves_its_coverage_by_script(self):
        # Epic DoD 6: minimum coverage — a pass that skipped a changed file did not complete.
        flat = _flat(CONTRACT)
        self.assertIn("`<facts.scratch>/security-diff-files.txt`", flat)
        self.assertIn("`<<changed_files_path>>`", flat)
        self.assertIn("scripts/parse.py security-coverage", flat)
        self.assertIn("never by eye", flat)
        self.assertIn("re-dispatch **once**", flat)
        self.assertIn("Paths still uncovered after it mean the pass **did not complete**", flat)
        self.assertIn("Run it only on a `## Findings` reply", flat)
        self.assertIn("one staging repair and one coverage re-dispatch", flat)
        self.assertIn("else at the end of the PR body", flat)
        self.assertIn("`git -c core.quotePath=false diff --name-only origin/<base>...HEAD`", flat)
        self.assertIn("`<facts.scratch>/security-findings-redispatch.md` so the first reply is kept", flat)
        self.assertIn("placed after the last line of the last section", flat)
        self.assertIn("Write a section only when it carries a `Reviewed:` line or at least one entry", flat)
        self.assertIn("missing or empty staged diff or changed-files list) → repair the staging of both files", flat)
        self.assertIn("A lookalike is protected only while it carries neither shape", flat)
        self.assertIn("or changed files still uncovered after the coverage re-dispatch",
                      " ".join((REPO_ROOT / "skills" / "resolver" / "references" / "finalisation.md").read_text(encoding="utf-8").split()))


class SecurityPromptTests(unittest.TestCase):
    def test_the_prompt_reads_every_changed_file_and_lists_its_coverage(self):
        text = PROMPT.read_text(encoding="utf-8")
        flat = " ".join(text.split())
        self.assertIn("<<changed_files_path>>", text)
        self.assertIn("Read **every** listed file", flat)
        findings = text[text.index("## Findings\n1."):]
        self.assertIn("\nCovered:\n- <path>\n```", findings)
        self.assertIn("`## Findings` always ends with the `Covered:` block", flat)
        self.assertIn("the diff file or the changed-files list is missing or empty", flat)
        self.assertIn("a binary file, or a generated one (a lockfile, a build artifact) is read in the diff only, and is still listed", flat)

    def test_the_prompt_carries_its_five_inputs_and_findings_grammar(self):
        text = PROMPT.read_text(encoding="utf-8")
        for placeholder in ("<<workspace_path>>", "<<diff_path>>", "<<changed_files_path>>", "<<base_ref>>",
                            "<<catalogue_entries>>"):
            self.assertIn(placeholder, text)
        self.assertIn("## Findings\n1. <path>:<line> — <category> — <high|medium> — <claim> — <exploit path>", text)
        self.assertIn("code: AMBIGUOUS", text)
        # Review round 2: a directory catalogue entry reaches the pass with its member files.
        self.assertIn("read and cite the member, never the directory", " ".join(text.split()))
        self.assertIn("a `kind: dir` entry's `members` listed under it", _flat(CONTRACT))

    def test_every_stack_mention_is_a_labelled_multi_stack_example(self):
        for line in PROMPT.read_text(encoding="utf-8").splitlines():
            if _STACK_RE.search(line):
                named = [n for n in _STACK_NAMES if re.search(r"\b%s\b" % n, line, re.IGNORECASE)]
                self.assertGreaterEqual(len(named), 2, line)

    def test_the_prompt_names_none_of_the_built_ins_stack_specifics(self):
        lower = PROMPT.read_text(encoding="utf-8").lower()
        for token in ("pickle", "flask", "jinja", "escape()"):
            self.assertNotIn(token, lower)


if __name__ == "__main__":
    unittest.main()
