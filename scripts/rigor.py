"""rigor.py — the rigor level a pipeline session runs at (#198): ``full``, ``medium`` or ``light``, and the
step values that level sets.

**Why this module exists.** Four preps report the level — ``prep_planner``, ``prep_resolver``,
``prep_evaluator`` and ``prep_coordinator`` — and a direct run, a coordinator run and a re-run must resolve
the same level from the same labels and the same block. So the resolution is one read, composed in-process
by every caller: the ``story_set.py`` / ``sensitive_paths.py`` promotion pattern. There is no CLI surface (no
shebang, no ``main()``): every caller is a prep, exactly like ``branching.py``.

Three inputs:

- **The block** — ``<!-- ctrl-freaq-rigor -->`` in the consuming repo's config file, discovered like every
  gate block (``config_block.read_block_anywhere``, architecture.md §6 rule 1: the working tree of the
  checkout the prep runs in). ``- default: <level>`` and ``- floor: <level>``, one key per line, blank lines
  allowed. No block, or no default, means ``full``; no floor means the floor is the default; a default below
  the floor resolves to the floor (``RIGOR_DEFAULT_BELOW_FLOOR``). Any other non-blank line, a repeated key,
  an unknown value, a structurally broken block or an unreadable candidate makes the whole block malformed:
  ``full``, with ``RIGOR_BLOCK_MALFORMED``. Rejecting the whole block, where the merge policy skips a bad
  line, is deliberate: a typo must never lower rigor.
- **The labels** — the ``rigor:*`` labels on the target issue and on its ancestors up to the first epic (the
  direct parent, and the grandparent only when the parent is not an epic), read in one ``gh api graphql``
  call per issue (:func:`read_labels`). The strictest permitted label wins (``full`` > ``medium`` >
  ``light``); with none, the block's default applies. A ``rigor:*`` label outside the set is dropped
  (``RIGOR_LABEL_UNKNOWN``), and so is one below the floor (``RIGOR_LABEL_BELOW_FLOOR``). A label set that
  could not be read is ``full`` (``RIGOR_LABELS_UNREAD``): a missing read never lowers rigor.
- **The route** — an epic-integration target is always ``full``.

Every degradation is a notice, never a decision code (architecture.md §3): each has a known strict value,
``full``. The step values are :data:`STEPS`, one row per level; playbooks read ``facts.rigor.steps.<key>``,
never the level name (CLAUDE.md "Parameterize before you playbook").

Spawns only ``gh``, through :mod:`pipelib.process`.
"""

import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import branching  # noqa: E402
import config_block  # noqa: E402
import parse  # noqa: E402
from pipelib import process  # noqa: E402

MARKER = "ctrl-freaq-rigor"
# The closed set, strictest first — `parse.RIGOR_LEVELS`, the `Rigor:` line's grammar, so the two never drift.
LEVELS = parse.RIGOR_LEVELS
_RANK = {"light": 0, "medium": 1, "full": 2}
LABEL_PREFIX = "rigor:"

RIGOR_BLOCK_MALFORMED = "RIGOR_BLOCK_MALFORMED"
RIGOR_DEFAULT_BELOW_FLOOR = "RIGOR_DEFAULT_BELOW_FLOOR"
RIGOR_LABEL_UNKNOWN = "RIGOR_LABEL_UNKNOWN"
RIGOR_LABEL_BELOW_FLOOR = "RIGOR_LABEL_BELOW_FLOOR"
RIGOR_LABELS_UNREAD = "RIGOR_LABELS_UNREAD"

# `chosen_by`, the closed set: which input set the level.
CHOSEN_BY = ("label:issue", "label:ancestor", "default", "fallback", "epic-integration")

# The six review-thread kinds, `review_threads.FLAG_KINDS`' set (the routing test pins the two equal).
_ALL_THREAD_SOURCES = ("security", "sensitive", "judgment", "out-of-plan", "routine", "plan")

# One row per level. `full` is today's behaviour, value for value: every playbook rule names its `full`
# value in place, so a `full` run is byte-identical to a run before #198.
STEPS = {
    "full": {
        "plan_review_passes": 3,
        "plan_review_blockers_only": False,
        "review_rounds": None,
        "unplanned_level": "high",
        "fixed_level": None,
        "judgment": True,
        "stall_card": True,
        "polish_in_loop": "fix",
        "polish_card": "resolver+backstop",
        "cold_read": "every-phase-loop",
        "security_round": "loop",
        "dismissal_check": True,
        "defect_injection": True,
        "fix_design": "on",
        "out_of_plan_triage": True,
        "thread_sources": list(_ALL_THREAD_SOURCES),
        "pause_signals": "all",
    },
    "medium": {
        "plan_review_passes": 2,
        "plan_review_blockers_only": False,
        "review_rounds": 4,
        "unplanned_level": "medium",
        "fixed_level": None,
        "judgment": False,
        "stall_card": False,
        "polish_in_loop": "list",
        "polish_card": "resolver+open-or-unfixed-apply",
        "cold_read": "every-phase-once",
        "security_round": "once-pass-level",
        "dismissal_check": True,
        "defect_injection": True,
        "fix_design": "precheck-only",
        "out_of_plan_triage": True,
        "thread_sources": ["security", "out-of-plan", "routine", "plan"],
        "pause_signals": "all-but-judgment",
    },
    "light": {
        "plan_review_passes": 1,
        "plan_review_blockers_only": True,
        "review_rounds": 2,
        "unplanned_level": "low",
        "fixed_level": "low",
        "judgment": False,
        "stall_card": False,
        "polish_in_loop": "list",
        "polish_card": "none",
        "cold_read": "final-once",
        "security_round": "once-low",
        "dismissal_check": False,
        "defect_injection": False,
        "fix_design": "precheck-only",
        "out_of_plan_triage": False,
        "thread_sources": ["security"],
        "pause_signals": "checkpoint-and-warning",
    },
}

_BLOCK_LINE_RE = re.compile(r"^\s*-\s*([A-Za-z_-]+)\s*:\s*(\S+)\s*$")
_BLOCK_KEYS = ("default", "floor")


def stricter(a, b):
    """The stricter of two levels."""
    return a if _RANK[a] >= _RANK[b] else b


def lowest(levels):
    """The least strict of ``levels`` (a non-empty iterable of levels)."""
    return min(levels, key=lambda level: _RANK[level])


def below(level, floor):
    """Whether ``level`` is less strict than ``floor``."""
    return _RANK[level] < _RANK[floor]


def parse_block(interior_lines):
    """``(default, floor, problem)`` from a well-formed block's interior lines: ``default`` and ``floor``
    as written (``None`` when absent), ``problem`` a one-line reason the block is malformed, else ``None``.
    Never adjusts a value: :func:`resolve` applies the defaults and the floor."""
    values = {}
    for raw in interior_lines:
        line = raw.strip()
        if not line:
            continue
        found = _BLOCK_LINE_RE.match(line)
        if not found or found.group(1) not in _BLOCK_KEYS:
            return None, None, "an unreadable line: %r" % line
        key, value = found.group(1), found.group(2)
        if key in values:
            return None, None, "`%s` is given twice" % key
        if value not in LEVELS:
            return None, None, "`%s: %s` is not a level (%s)" % (key, value, ", ".join(LEVELS))
        values[key] = value
    return values.get("default"), values.get("floor"), None


def _resolve_path(root, path):
    return Path(path) if Path(path).is_absolute() else Path(root) / path


def _stray_delimiter(root):
    """The first candidate file holding a line that is exactly this block's open or close delimiter, or
    ``None``. Read only when no well-formed block was found, so a broken block reads as malformed rather
    than absent. A prose mention of the marker inside a longer line never counts."""
    open_marker, close_marker = "<!-- %s -->" % MARKER, "<!-- /%s -->" % MARKER

    def reader(path):
        resolved = _resolve_path(root, path)
        return resolved.read_text(encoding="utf-8") if resolved.is_file() else None

    for candidate in config_block.candidate_paths_with(reader):
        text = reader(candidate)
        if text and any(line.strip() in (open_marker, close_marker) for line in text.splitlines()):
            return str(_resolve_path(root, candidate))
    return None


def read_block(root):
    """``(block, notices)`` — the block at ``root``'s working tree. ``block`` is ``None`` when absent, else
    ``{"path", "default", "floor", "malformed"}``: ``path`` the host file's absolute path (``None`` when an
    unreadable candidate stopped the read), ``default`` / ``floor`` as written (``None`` when absent, and
    both ``None`` on a malformed block), ``malformed`` whether :func:`resolve` must treat it as unreadable."""
    try:
        present, interior, source = config_block.read_block_anywhere(root, MARKER)
        stray = None if present else _stray_delimiter(root)
    except (OSError, UnicodeDecodeError) as exc:
        return ({"path": None, "default": None, "floor": None, "malformed": True},
                ["%s: a candidate config file could not be read (%s); the level is `full`"
                 % (RIGOR_BLOCK_MALFORMED, exc)])
    if not present:
        if stray is None:
            return None, []
        return ({"path": stray, "default": None, "floor": None, "malformed": True},
                ["%s: the `<!-- %s -->` block in %s is not well-formed (a delimiter is missing, repeated or "
                 "out of order); the level is `full`" % (RIGOR_BLOCK_MALFORMED, MARKER, stray)])
    default, floor, problem = parse_block(interior)
    if problem is not None:
        return ({"path": source, "default": None, "floor": None, "malformed": True},
                ["%s: the `<!-- %s -->` block in %s has %s; the level is `full`"
                 % (RIGOR_BLOCK_MALFORMED, MARKER, source, problem)])
    return {"path": source, "default": default, "floor": floor, "malformed": False}, []


def _rigor_labels(labels):
    return [label.strip() for label in labels or [] if (label or "").strip().lower().startswith(LABEL_PREFIX)]


def resolve(block, label_sets, epic_integration=False):
    """``(rigor, notices)`` — the pure core. ``block`` is :func:`read_block`'s ``block`` (``None`` when
    absent); ``label_sets`` is ``{"issues": [[label]], "ancestors": [[label]]}``, or ``None`` when a label
    read failed. ``rigor`` is ``{"level", "source": {"block": {"present", "path", "default", "floor"},
    "labels", "chosen_by"}, "steps"}``: ``source.block`` carries the effective default and floor (``full``
    when absent), ``source.labels`` is ``label_sets`` as given (``None`` when unread), and ``steps`` is a
    copy of the level's :data:`STEPS` row.

    An issue label wins a tie with an ancestor's (``label:issue``). Failures resolve upward: a malformed
    block, an unread label set and an epic-integration target are each ``full``."""
    notices = []
    malformed = bool(block and block.get("malformed"))
    usable = block if block is not None and not malformed else None
    default = (usable or {}).get("default") or "full"
    floor = (usable or {}).get("floor") or default
    if below(default, floor):
        notices.append("%s: the block's default `%s` is below its floor `%s`; the default is `%s`"
                       % (RIGOR_DEFAULT_BELOW_FLOOR, default, floor, floor))
        default = floor
    block_fact = {
        "present": usable is not None,
        "path": (block or {}).get("path"),
        "default": default,
        "floor": floor,
    }

    def done(level, chosen_by):
        return {
            "level": level,
            "source": {"block": block_fact, "labels": label_sets, "chosen_by": chosen_by},
            "steps": copy.deepcopy(STEPS[level]),
        }, notices

    if epic_integration:
        return done("full", "epic-integration")
    if label_sets is None:
        notices.append("%s: the issue's or an ancestor's labels could not be read; the level is `full`"
                       % RIGOR_LABELS_UNREAD)
        return done("full", "fallback")
    best = {}
    for origin, key in (("issue", "issues"), ("ancestor", "ancestors")):
        for labels in label_sets.get(key) or []:
            for label in _rigor_labels(labels):
                level = label.lower()[len(LABEL_PREFIX):]
                if level not in LEVELS:
                    notices.append("%s: `%s` is not a rigor level (%s); ignored"
                                   % (RIGOR_LABEL_UNKNOWN, label, ", ".join(LEVELS)))
                    continue
                if below(level, floor):
                    notices.append("%s: `%s` is below the floor `%s`; ignored"
                                   % (RIGOR_LABEL_BELOW_FLOOR, label, floor))
                    continue
                if origin not in best or _RANK[level] > _RANK[best[origin]]:
                    best[origin] = level
    if best:
        issue, ancestor = best.get("issue"), best.get("ancestor")
        if issue is not None and (ancestor is None or _RANK[issue] >= _RANK[ancestor]):
            return done(issue, "label:issue")
        return done(ancestor, "label:ancestor")
    return done(default, "fallback" if malformed else "default")


_LABELS = "labels(first: 50) { nodes { name } }"


def labels_query(repo, number, subissues_available=True):
    """The one GraphQL query :func:`read_labels` runs — the issue's labels, then its parent's and its
    grandparent's with their titles (the parent's type decides whether the grandparent counts). On a host
    with no sub-issues the ``parent`` field is omitted."""
    owner, _, name = repo.partition("/")
    parent = ""
    if subissues_available:
        parent = " parent { number title %s parent { number title %s } }" % (_LABELS, _LABELS)
    return "query { repository(owner: %s, name: %s) { issue(number: %d) { %s%s } } }" % (
        json.dumps(owner), json.dumps(name), int(number), _LABELS, parent
    )


def _names(node):
    nodes = ((node or {}).get("labels") or {}).get("nodes")
    if not isinstance(nodes, list):
        return None
    return [n["name"] for n in nodes if isinstance(n, dict) and isinstance(n.get("name"), str)]


def read_labels(repo, number, subissues_available=True, cwd=None):
    """``{"issue": {"number", "labels"}, "ancestors": [{"number", "labels"}]}`` for one issue, or ``None``
    when the read failed — any non-zero exit (an auth failure included) or a response it cannot read. The
    ancestors are the direct parent, plus the grandparent only when the parent is not an epic
    (``branching.detect_type_with_question`` on its labels and title). Deliberately stricter than
    ``board_history``'s read, which keeps partial data: a missing ancestor's labels must not lower rigor."""
    result = process.run(
        ["gh", "api", "graphql", "-f", "query=" + labels_query(repo, number, subissues_available)], cwd=cwd
    )
    if result.returncode != 0:
        return None
    try:
        issue = (((json.loads(result.stdout or "null") or {}).get("data") or {}).get("repository") or {}).get("issue")
    except (ValueError, AttributeError):
        return None
    if not isinstance(issue, dict):
        return None
    own = _names(issue)
    if own is None:
        return None
    ancestors = []
    parent = issue.get("parent") if subissues_available else None
    if parent is not None:
        if not isinstance(parent, dict) or not isinstance(parent.get("number"), int):
            return None
        parent_labels = _names(parent)
        if parent_labels is None:
            return None
        ancestors.append({"number": parent["number"], "labels": parent_labels})
        grand = parent.get("parent")
        if grand is not None and branching.detect_type_with_question(parent_labels, parent.get("title") or "") != "epic":
            if not isinstance(grand, dict) or not isinstance(grand.get("number"), int):
                return None
            grand_labels = _names(grand)
            if grand_labels is None:
                return None
            ancestors.append({"number": grand["number"], "labels": grand_labels})
    return {"issue": {"number": int(number), "labels": own}, "ancestors": ancestors}


def build_rigor(root, repo, issues, epic_integration=False, subissues_available=True, cwd=None):
    """``(rigor, notices)`` — ``facts.rigor`` for a prep: the block at ``root``'s working tree, and the
    labels of every issue in ``issues`` (with their ancestors). One unread issue makes the whole label set
    unread (``full``). ``source.labels`` names each label set's issue: ``{"issues": [{"number", "labels"}],
    "ancestors": [{"number", "labels"}]}``, ``None`` when unread. With no issues only the block applies. An
    epic-integration target reads no labels: it is ``full`` whatever they say."""
    block, notices = read_block(root)
    reads = []
    if not epic_integration:
        for number in issues or []:
            read = read_labels(repo, number, subissues_available=subissues_available, cwd=cwd)
            if read is None:
                reads = None
                break
            reads.append(read)
    label_sets = None if reads is None else {
        "issues": [read["issue"]["labels"] for read in reads],
        "ancestors": [a["labels"] for read in reads for a in read["ancestors"]],
    }
    rigor, resolve_notices = resolve(block, label_sets, epic_integration=epic_integration)
    rigor["source"]["labels"] = None if reads is None else {
        "issues": [read["issue"] for read in reads],
        "ancestors": [a for read in reads for a in read["ancestors"]],
    }
    return rigor, notices + resolve_notices
