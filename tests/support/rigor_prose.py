"""Prose checks for the rigor step keys (#198), shared by the per-skill routing tests.

The plan's rule: playbooks read step values (`facts.rigor.steps.<key>`, or "the pass's `steps.<key>`" inside a
resolver pass), never the level's name. So every key a prompt names must be a `rigor.STEPS` key, and the level
reaches a prompt only as the argument a script call takes (`--rigor`, or `parse.py rigor-line --pass`).
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import rigor  # noqa: E402

STEP_KEY_RE = re.compile(r"\bsteps\.([a-z_]+)")
LEVEL_FACT = "facts.rigor.level"
# A `rigor:*` label name; a backticked `light`, which is never a review level, so in a prompt it can only be the rigor
# level; or any level token in a condition's shape (`under `medium``, `level is `full``, `` `light` rigor``). A bare
# `medium` is also a `code-review` level and `full` names a step value's column (`the `full` value`), so neither
# alone is a level condition.
LEVEL_NAME_RE = re.compile(
    r"rigor:(?:full|medium|light)\b|`light`"
    r"|\b(?:under|level is|rigor is|rigor level|rigor)\s+`(?:full|medium|light)`"
    r"|`(?:full|medium|light)`\s+(?:rigor|level)\b",
    re.IGNORECASE,
)


def markdown_files(*dirs):
    for d in dirs:
        yield from sorted(Path(d).rglob("*.md"))


def unknown_step_keys(paths):
    """Each `(path, key)` a prompt names that `rigor.STEPS` lacks."""
    known = set(rigor.STEPS["full"])
    return [(str(p), key) for p in paths for key in STEP_KEY_RE.findall(p.read_text(encoding="utf-8"))
            if key not in known]


def level_conditions(paths):
    """Each `(path, line)` that names a rigor level instead of a step value."""
    found = []
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            if LEVEL_NAME_RE.search(line) or (LEVEL_FACT in line and not re.search(r"--(?:rigor|pass) <facts\.rigor\.level>", line)):
                found.append((str(p), line.strip()))
    return found
