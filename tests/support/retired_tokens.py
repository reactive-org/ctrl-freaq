"""The v1 surfaces retired at S20, and the exemptions that survive them.

This module is the **single home for the retired token literals**. Every other file under the
S20 DoD's scanned paths (`skills/`, `scripts/`, `tests/`, `README.md`, `.claude-plugin/`,
`CLAUDE.md`) is held to zero hits for them by `tests/test_v1_retirement.py`, so the literals live
here once — imported by the per-skill routing gates and by the retirement scan itself — rather than
being retyped in a dozen regexes. `docs/specs/**` and `docs/implementation.md` are exempt as the
historical record (S20 DoD box 1).

Three token families:

1. `RETIRED_NAME_PATTERNS` — the S20 DoD box-1 grep set, verbatim: the seven v1 skill directory
   names, the `github-ops` executor sub-agent, and the `worktree-hooks` runner script. A hit is a
   stale reference to something that no longer exists on disk.
2. `FORBIDDEN_CONTRACT_TOKENS` — the per-skill prompt gate every cutover step (S7/S10/S13/S15–S19)
   applied to its own `skills/<name>/` tree: the executor name, the v1 skill-invocation namespace
   (`github-pipeline:github-*`), the `GATHER_*` / `PERSIST_*` op names, and the resolver-local
   `§P` IDs. All four retired with v1; this regex keeps them from creeping back.
3. `RETIRED_NAMESPACE_TOKENS` — the plugin's pre-5.0.0 namespace on each live skill
   (`github-pipeline:<live skill>`), scanned over the same path set so a missed invocation string
   after the 5.0.0 rename to `ctrl-freaq` fails the suite. `rename_namespace` maps a frozen capture
   (which keeps the old namespace) onto the live one for byte comparisons.

**Exemptions** are (path, token, required-line-fragment) triples, so an exemption licenses one
*known* string in one file — never the token generally. Two classes:

- **Frozen artifact provenance** ([prd.md §7](../../docs/prd.md)). A persisted artifact's footer
  names the v1 skill that authored it (`_Authored by \\`github-issue-planner\\`…`, `_Cached by
  \\`github-pr-evaluator\\`…`). These are byte-compatibility contract tokens a cross-consuming v1
  reader matches verbatim — the S7 adjudication (a) recorded in `docs/specs/baseline.md` §5.3 — so
  the skill rename deliberately does **not** propagate into the artifact bytes. The exemption covers
  the rendering file *and* the test that pins those bytes.
- **Consuming-repo doc filename.** `docs/open-questions.md` is a *sandbox repo's* open-question
  register, not the retired `open-questions` skill. It is the false-positive the DoD's bare
  `open-questions` grep would otherwise trip on.
"""

import re

# ---------------------------------------------------------------------------------------------
# 1. The S20 DoD box-1 grep set
# ---------------------------------------------------------------------------------------------

RETIRED_SKILL_DIRS = (
    "github-issue-drafter",
    "github-issue-researcher",
    "github-issue-planner",
    "github-issue-resolver",
    "github-pr-evaluator",
    "github-pipeline-setup",
    "open-questions",
)

# The v1 executor sub-agent and the v1 hook-runner script.
EXECUTOR_AGENT = "github-ops"
HOOK_RUNNER = "worktree-hooks"

# The literals the DoD's grep uses (`github-issue-` collapses the four `github-issue-*` dirs).
RETIRED_NAME_PATTERNS = (
    "github-issue-",
    "github-pr-evaluator",
    "github-pipeline-setup",
    "open-questions",
    EXECUTOR_AGENT,
    HOOK_RUNNER,
)


def retired_name_hits(text):
    """The retired v1 names appearing anywhere in `text`. Exemption-free: the per-skill prompt
    gates use it over trees that carry no frozen provenance string."""
    return sorted({token for token in RETIRED_NAME_PATTERNS if token in text})


# The five v1 shell scripts; `scripts/*.sh` must be empty after S20.
RETIRED_SHELL_SCRIPTS = (
    "gh-gather.sh",
    "gh-pr-gather.sh",
    "gh-persist.sh",
    "config-block.sh",
    "worktree-hooks.sh",
)

# ---------------------------------------------------------------------------------------------
# 2. The per-skill prompt gate (the regex every cutover step used over its own skills/<name>/)
# ---------------------------------------------------------------------------------------------

FORBIDDEN_CONTRACT_TOKENS = re.compile(
    r"\bgithub-ops\b|github-pipeline:github-|\bGATHER_[A-Z]+\b|\bPERSIST_[A-Z]+\b|§P[0-9]"
)

# The sixteen live skill directory names (`skills/_shared` is not a skill); a test pins the set
# against the directory listing so a seventeenth skill cannot escape the scan.
LIVE_SKILL_NAMES = (
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
    "review",
    "review-report",
    "setup",
    "slicer",
    "workspace-close",
    "workspace-open",
)

# The plugin's pre-5.0.0 invocation namespace on a live skill: a stale reference after the rename.
RETIRED_NAMESPACE_TOKENS = tuple("github-pipeline:" + name for name in LIVE_SKILL_NAMES)

OLD_NAMESPACE = "github-pipeline:"
NEW_NAMESPACE = "ctrl-freaq:"


def rename_namespace(text):
    """A frozen capture that predates the 5.0.0 rename, read through the rename."""
    return text.replace(OLD_NAMESPACE, NEW_NAMESPACE)


# The v1 skill-invocation namespace, for tests that assert one specific stale command is gone.
V1_INVOCATION_PREFIX = "/github-pipeline:github-"

# ---------------------------------------------------------------------------------------------
# 3. Exemptions — (path, token, required fragment on the line)
# ---------------------------------------------------------------------------------------------

_FROZEN = "frozen prd.md §7 artifact provenance string (S7 adjudication (a))"
_SANDBOX_DOC = "a consuming repo's open-question register filename, not the retired skill"

EXEMPTIONS = (
    # --- frozen artifact renderings -----------------------------------------------------------
    (
        "skills/evaluator/references/health-cache-comment.md",
        "github-pr-evaluator",
        "Cached by `github-pr-evaluator`",
        _FROZEN,
    ),
    (
        "skills/evaluator/references/review-comment.md",
        "github-pr-evaluator",
        "Recorded by `github-pr-evaluator`",
        _FROZEN,
    ),
    (
        "skills/planner/references/plan-schema.md",
        "github-issue-",
        "_Authored by `github-issue-planner` and verified in",
        _FROZEN,
    ),
    (
        "skills/planner/playbooks/plan-spine.md",
        "github-issue-",
        "github-issue-planner\\`; re-run that skill to revise.",
        _FROZEN,
    ),
    (
        "skills/planner/references/plan-schema.md",
        "open-questions",
        "a doc open-questions register entry: that goes in `## Open questions`",
        _FROZEN + " — inside the byte-pinned plan-schema block (test_planner_routing.py"
        "::PlanSchemaByteCompatTests)",
    ),
    (
        "skills/researcher/references/dossier-schema.md",
        "github-issue-",
        "`github-issue-planner` consumes it and owns the design decisions.",
        _FROZEN,
    ),
    (
        "skills/researcher/references/dossier-schema.md",
        "github-issue-",
        "_Authored by `github-issue-researcher`. Re-run that skill to refresh",
        _FROZEN,
    ),
    # --- the tests that pin those frozen bytes -------------------------------------------------
    (
        "tests/test_planner_routing.py",
        "github-issue-",
        "_Authored by `github-issue-planner` and verified in",
        _FROZEN + " (pinned by this assertion)",
    ),
    (
        "tests/test_planner_routing.py",
        "github-issue-",
        "`github-issue-planner`; re-run that skill to revise.",
        _FROZEN + " (pinned by this assertion)",
    ),
    (
        "tests/test_researcher_routing.py",
        "github-issue-",
        'self.assertIn("Authored by `github-issue-researcher`", block)',
        _FROZEN + " (pinned by this assertion)",
    ),
    (
        "tests/test_researcher_routing.py",
        "github-issue-",
        'self.assertIn("`github-issue-planner` consumes it", block)',
        _FROZEN + " (pinned by this assertion)",
    ),
    # --- fixture copies of the frozen `github-pipeline-config` header body ----------------------
    (
        "tests/fixtures/config_block_canonical_blocks/github_pipeline_config_header_body.md",
        "github-pipeline-setup",
        "Re-run `github-pipeline-setup` to reconcile them (idempotent).",
        _FROZEN + " (fixture copy)",
    ),
    (
        "tests/fixtures/config_block_roundtrip/commands_before.md",
        "github-pipeline-setup",
        "Re-run `github-pipeline-setup` to reconcile them (idempotent).",
        _FROZEN + " (fixture copy)",
    ),
    (
        "tests/fixtures/config_block_roundtrip/commands_after_upsert_new_block.md",
        "github-pipeline-setup",
        "Re-run `github-pipeline-setup` to reconcile them (idempotent).",
        _FROZEN + " (fixture copy)",
    ),
    (
        "tests/fixtures/config_block_roundtrip/commands_after_replace_existing_block.md",
        "github-pipeline-setup",
        "Re-run `github-pipeline-setup` to reconcile them (idempotent).",
        _FROZEN + " (fixture copy)",
    ),
    (
        "tests/fixtures/config_block_roundtrip/commands_after_remove_middle_block.md",
        "github-pipeline-setup",
        "Re-run `github-pipeline-setup` to reconcile them (idempotent).",
        _FROZEN + " (fixture copy)",
    ),
    # --- a consuming repo's own doc filename ---------------------------------------------------
    ("tests/SANDBOX.md", "open-questions", "docs/open-questions.md", _SANDBOX_DOC),
    ("tests/test_prep_drafter.py", "open-questions", "docs/open-questions.md", _SANDBOX_DOC),
    # --- this file ------------------------------------------------------------------------------
    (
        "tests/support/retired_tokens.py",
        None,  # every token: this module *is* the table
        None,
        "the retirement token table itself — the one place the literals are allowed to live",
    ),
)


def line_is_exempt(rel_path, token, line):
    """True when `token`'s appearance in `line` of `rel_path` is on the exemption list."""
    for path, exempt_token, fragment, _why in EXEMPTIONS:
        if path != rel_path:
            continue
        if exempt_token is None:
            return True
        if exempt_token != token:
            continue
        if fragment is None or fragment in line:
            return True
    return False
