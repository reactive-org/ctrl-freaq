"""rigor.py — the rigor level a pipeline session runs at (#198): ``full``, ``medium`` or ``light``, and the
step values that level sets.

**Why this module exists.** Four preps report the level — ``prep_planner``, ``prep_resolver``,
``prep_evaluator`` and ``prep_coordinator`` — and a direct run, a coordinator run and a re-run must resolve
the same level from the same labels and the same block content. So the resolution is one read, composed
in-process by every caller: the ``story_set.py`` / ``sensitive_paths.py`` promotion pattern. There is no CLI surface (no
shebang, no ``main()``): every caller is a prep, exactly like ``branching.py``.

Four inputs:

- **The block** — ``<!-- ctrl-freaq-rigor -->`` in the consuming repo's config file, discovered like every
  gate block (``config_block.read_block_anywhere``, architecture.md §6 rule 1: the working tree of the
  checkout the prep runs in). ``- default: <level>`` and ``- floor: <level>``, one key per line, blank lines
  allowed. No block, or no default, means ``full``; no floor means the floor is the default; a default below
  the floor resolves to the floor (``RIGOR_DEFAULT_BELOW_FLOOR``). Any other non-blank line, a repeated key,
  an unknown value, an unreadable candidate, or a block reached only outside the repository (an out-of-root
  or out-and-back include or symlink, which the self-edit comparison in ``sensitive_paths`` cannot see,
  ``sensitive_paths.unreached``) makes the whole block malformed: ``full``, with ``RIGOR_BLOCK_MALFORMED``.
  A structurally broken block (a lone, repeated or out-of-order delimiter line) is malformed only when no
  candidate holds a well-formed one: the first well-formed block found wins, as for every gate block
  (``_stray_delimiter``). Rejecting the whole block, where the merge policy skips a bad line, is
  deliberate: a typo inside the block must never lower rigor.
- **Whether the branch's block is its base's** — compared with the branch's merge base with
  ``origin/<base_ref>`` (``sensitive_paths.rigor_block_changed``), never a phase's ``--since`` base, which
  already holds a block an earlier phase lowered, and with ``origin/<base_ref>``'s tip, which a base that
  changed the block after the branch was cut reads differently. A difference, or a comparison that fails, is
  ``full`` (``RIGOR_BLOCK_CHANGED``): a branch never runs at a level it set itself, nor at an older one its base
  has since replaced.
- **The labels** — the ``rigor:*`` labels on each issue and on its ancestors up to the first epic (the
  direct parent, and the grandparent only when the parent is not an epic, plus a legacy-checklist epic the
  prep found, ``fallback_parent``), read in one ``gh api graphql`` call per issue (:func:`read_labels`). Each
  issue resolves on its own — its strictest permitted label (``full`` > ``medium`` > ``light``), else the
  block's default — and the strictest issue wins, so no issue's label lowers another's. A ``rigor:*`` label
  outside the set is dropped (``RIGOR_LABEL_UNKNOWN``), and so is one below the floor
  (``RIGOR_LABEL_BELOW_FLOOR``). A label set that could not be read is ``full`` (``RIGOR_LABELS_UNREAD``): a
  missing read never lowers rigor. Where an epic's label may not have been read (no sub-issue relation, or a
  walk ending at a parentless story) the read says so (``RIGOR_ANCESTORS_UNSUPPORTED``).
- **The route** — an epic-integration target is always ``full``.

Two checkouts read different block content when the branch changes the block, or when its base changed the
block after the branch was cut; either way every session that judges the branch (planner, resolver,
evaluator) resolves ``full`` from the base comparison. The coordinator reads the project root's working tree
and compares nothing, so its status line — and the floor its first-run ``Rigor`` card offers — can differ from
the level a branch runs: for such a branch, or when the root is not at its base's tip.

Every degradation is a notice, never a decision code (architecture.md §3): each has a known strict value,
``full``. The step values are :data:`STEPS`, one row per level; playbooks read ``facts.rigor.steps.<key>``,
never the level name (CLAUDE.md "Parameterize before you playbook").

Spawns ``gh`` through :mod:`pipelib.process`; the reach check in :mod:`sensitive_paths` reads files only.
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
import sensitive_paths  # noqa: E402  (the in-root reach check the self-edit comparison shares)
from pipelib import process  # noqa: E402

MARKER = "ctrl-freaq-rigor"
# The closed set, strictest first — `parse.RIGOR_LEVELS`, the `Rigor:` line's grammar, so the two never drift.
LEVELS = parse.RIGOR_LEVELS
# Strictness rank, from `LEVELS`' strictest-first order, so the two cannot drift.
_RANK = {level: rank for rank, level in enumerate(reversed(LEVELS))}
LABEL_PREFIX = "rigor:"

RIGOR_BLOCK_MALFORMED = "RIGOR_BLOCK_MALFORMED"
RIGOR_DEFAULT_BELOW_FLOOR = "RIGOR_DEFAULT_BELOW_FLOOR"
RIGOR_LABEL_UNKNOWN = "RIGOR_LABEL_UNKNOWN"
RIGOR_LABEL_BELOW_FLOOR = "RIGOR_LABEL_BELOW_FLOOR"
RIGOR_LABELS_UNREAD = "RIGOR_LABELS_UNREAD"
RIGOR_BLOCK_CHANGED = "RIGOR_BLOCK_CHANGED"
RIGOR_ANCESTORS_UNSUPPORTED = "RIGOR_ANCESTORS_UNSUPPORTED"

# `chosen_by`, the closed set: which input set the level.
CHOSEN_BY = ("label:issue", "label:ancestor", "default", "fallback", "block-changed", "epic-integration")

# The six review-thread kinds, `review_threads.FLAG_KINDS`' set (the routing test pins the two equal).
_ALL_THREAD_SOURCES = ("security", "sensitive", "judgment", "out-of-plan", "routine", "plan")

# One row per level. `full`'s row is today's behaviour, value for value: every playbook rule names its `full`
# value in place, so a `full` run takes the steps it took before #198 (a change to the rigor block is a new
# sensitive match at every level, and each prep makes up to two more `gh` calls per issue — its label read and a
# `fallback_parent` read — plus, given a base, the block comparison's fetch and git reads).
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
        # A block reached only through an out-of-root or out-and-back route (a symlink or an absolute or `..`
        # include) is one the self-edit comparison cannot see (`sensitive_paths`, SEC-1's class), so a PR could
        # lower its own level unseen: it is unreadable, and the level is `full`.
        unreached = sensitive_paths.unreached(root, MARKER) if present else []
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
    if unreached:
        return ({"path": source, "default": None, "floor": None, "malformed": True},
                ["%s: %s; the level is `full`" % (RIGOR_BLOCK_MALFORMED, unreached[0])])
    default, floor, problem = parse_block(interior)
    if problem is not None:
        return ({"path": source, "default": None, "floor": None, "malformed": True},
                ["%s: the `<!-- %s -->` block in %s has %s; the level is `full`"
                 % (RIGOR_BLOCK_MALFORMED, MARKER, source, problem)])
    return {"path": source, "default": default, "floor": floor, "malformed": False}, []


def _rigor_labels(labels):
    return [label.strip() for label in labels or [] if (label or "").strip().lower().startswith(LABEL_PREFIX)]


# `chosen_by` among issues tied at the run's level: an issue's own label, then an ancestor's, then the default
# (a malformed block's `fallback` ranks with the default it stands in for).
_CHOSEN_RANK = {"label:issue": 2, "label:ancestor": 1, "default": 0, "fallback": 0}


def _add(notices, notice):
    if notice not in notices:
        notices.append(notice)


def _issue_level(entry, floor, default_level, default_by, notices):
    """``(level, chosen_by)`` for one issue: the strictest permitted label among its own and its ancestors',
    its own winning a tie, else the default. Unknown and below-floor labels are dropped with a notice."""
    best = {}
    for origin, sets in (("issue", [entry.get("issue") or []]), ("ancestor", entry.get("ancestors") or [])):
        for labels in sets:
            for label in _rigor_labels(labels):
                level = label.lower()[len(LABEL_PREFIX):]
                if level not in LEVELS:
                    _add(notices, "%s: `%s` is not a rigor level (%s); ignored"
                         % (RIGOR_LABEL_UNKNOWN, label, ", ".join(LEVELS)))
                    continue
                if below(level, floor):
                    _add(notices, "%s: `%s` is below the floor `%s`; ignored"
                         % (RIGOR_LABEL_BELOW_FLOOR, label, floor))
                    continue
                if origin not in best or _RANK[level] > _RANK[best[origin]]:
                    best[origin] = level
    issue, ancestor = best.get("issue"), best.get("ancestor")
    if issue is not None and (ancestor is None or _RANK[issue] >= _RANK[ancestor]):
        return issue, "label:issue"
    if ancestor is not None:
        return ancestor, "label:ancestor"
    return default_level, default_by


def resolve(block, label_sets, epic_integration=False, block_changed=False):
    """``(rigor, notices)`` — the pure core. ``block`` is :func:`read_block`'s ``block`` (``None`` when
    absent); ``label_sets`` is a list with one ``{"issue": [label], "ancestors": [[label]]}`` per issue, or
    ``None`` when any one issue's read failed. ``block_changed`` is whether the block is not its base's —
    the branch changes it, or the base changed it after the branch was cut — ``True``, or a one-line reason (a
    failed base read is a change).

    Each issue resolves on its own: the strictest permitted label among its own and its ancestors' (its own
    winning a tie), else the block's default. The run's level is the strictest issue's, and ``chosen_by`` the
    winning issue's — among issues tied at that level, ``label:issue`` beats ``label:ancestor``, which beats
    ``default``. Pooling every issue's labels first would let one issue's label lower another that carries
    none (PR #199's cold read). With no issue only the block applies.

    ``rigor`` is ``{"level", "source": {"block": {"present", "path", "default", "floor", "changed"}, "labels",
    "chosen_by"}, "full_rules", "steps"}``: ``source.block`` carries the effective default and floor (``full``
    when absent); ``source.labels`` is one ``{"labels", "ancestors", "level", "chosen_by"}`` per issue, as given
    (``None`` when unread, or on an epic-integration target, which reads none); ``full_rules`` is ``False``
    (:func:`build_rigor` sets it); ``steps`` is a copy of the level's :data:`STEPS` row.

    Failures resolve upward, checked in this order: an epic-integration target, a changed block, an unread
    label set — each ``full`` — and a malformed block reads as absent (``full``, ``chosen_by: fallback``)."""
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
        "changed": bool(block_changed),
    }
    default_by = "fallback" if malformed else "default"
    per_issue = None
    if label_sets is not None and not epic_integration:
        per_issue = []
        for entry in label_sets:
            level, chosen_by = _issue_level(entry, floor, default, default_by, notices)
            per_issue.append({"labels": list(entry.get("issue") or []),
                              "ancestors": [list(a) for a in entry.get("ancestors") or []],
                              "level": level, "chosen_by": chosen_by})

    def done(level, chosen_by):
        return {
            "level": level,
            "source": {"block": block_fact, "labels": per_issue, "chosen_by": chosen_by},
            "full_rules": False,
            "steps": copy.deepcopy(STEPS[level]),
        }, notices

    if epic_integration:
        return done("full", "epic-integration")
    if block_changed:
        reason = block_changed if isinstance(block_changed, str) else (
            "the branch changes the `<!-- %s -->` block against its base" % MARKER)
        notices.append("%s: %s; the level is `full`" % (RIGOR_BLOCK_CHANGED, reason))
        return done("full", "block-changed")
    if per_issue is None:
        notices.append("%s: the issue's or an ancestor's labels could not be read; the level is `full`"
                       % RIGOR_LABELS_UNREAD)
        return done("full", "fallback")
    if not per_issue:
        return done(default, default_by)
    winner = max(per_issue, key=lambda e: (_RANK[e["level"]], _CHOSEN_RANK[e["chosen_by"]]))
    return done(winner["level"], winner["chosen_by"])


_LABELS = "labels(first: 50) { totalCount nodes { name } }"


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
    """A node's label names, or ``None`` when they cannot be read whole — no list, or more labels than the
    page holds (``totalCount``), since a `rigor:full` past the page must not read as absent."""
    labels = (node or {}).get("labels") or {}
    nodes = labels.get("nodes")
    if not isinstance(nodes, list):
        return None
    total = labels.get("totalCount")
    if isinstance(total, int) and total > len(nodes):
        return None
    return [n["name"] for n in nodes if isinstance(n, dict) and isinstance(n.get("name"), str)]


def _gql_issue(repo, number, subissues_available, cwd):
    """The query's ``issue`` node, or ``None`` on any non-zero exit or a response it cannot read."""
    result = process.run(
        ["gh", "api", "graphql", "-f", "query=" + labels_query(repo, number, subissues_available)], cwd=cwd
    )
    if result.returncode != 0:
        return None
    try:
        issue = (((json.loads(result.stdout or "null") or {}).get("data") or {}).get("repository") or {}).get("issue")
    except (ValueError, AttributeError):
        return None
    return issue if isinstance(issue, dict) else None


def _ancestor(node):
    """``({"number", "labels"}, type)`` for a parent node, or ``None`` when it cannot be read whole."""
    if not isinstance(node, dict) or not isinstance(node.get("number"), int):
        return None
    labels = _names(node)
    if labels is None:
        return None
    return {"number": node["number"], "labels": labels}, branching.detect_type_with_question(labels, node.get("title") or "")


def read_labels(repo, number, subissues_available=True, fallback_parent=None, cwd=None):
    """``{"issue": {"number", "labels"}, "ancestors": [{"number", "labels"}], "epic_unread": bool}`` for one
    issue, or ``None`` when any read failed — any non-zero exit (an auth failure included) or a response it
    cannot read. Deliberately stricter than ``board_history``'s read, which keeps partial data: a missing
    ancestor's labels must not lower rigor.

    The ancestors are the native walk's direct parent, plus the grandparent only when the parent is not an
    epic (``branching.detect_type_with_question`` on its labels and title). ``fallback_parent`` is the epic the
    prep's own hierarchy lookup found — a legacy ``## Stories`` checklist names a story there with no native
    edge: when the native walk reaches no epic and it is not already an ancestor, its own labels are read
    (the parent-less query) and counted as one. ``epic_unread`` is set when the walk ends at a story-typed
    ancestor with no parent and no ``fallback_parent`` was read: that story's epic, if it has one, is linked
    only by a checklist this read did not see."""
    issue = _gql_issue(repo, number, subissues_available, cwd)
    if issue is None:
        return None
    own = _names(issue)
    if own is None:
        return None
    ancestors, reached_epic, ends_at_story = [], False, False
    parent = issue.get("parent") if subissues_available else None
    if parent is not None:
        read = _ancestor(parent)
        if read is None:
            return None
        ancestors.append(read[0])
        reached_epic = read[1] == "epic"
        grand = parent.get("parent")
        if not reached_epic:
            if grand is None:
                ends_at_story = read[1] == "story"
            else:
                grand_read = _ancestor(grand)
                if grand_read is None:
                    return None
                ancestors.append(grand_read[0])
                reached_epic = grand_read[1] == "epic"
    fallback_read = False
    if (fallback_parent is not None and not reached_epic and int(fallback_parent) != int(number)
            and int(fallback_parent) not in [a["number"] for a in ancestors]):
        node = _gql_issue(repo, int(fallback_parent), False, cwd)
        labels = _names(node) if node is not None else None
        if labels is None:
            return None
        ancestors.append({"number": int(fallback_parent), "labels": labels})
        fallback_read = True
    return {"issue": {"number": int(number), "labels": own}, "ancestors": ancestors,
            "epic_unread": ends_at_story and not fallback_read}


def _fallback_for(fallback_parent, number):
    """The ``fallback_parent`` for one issue: a number applies to every issue (a prep reading one), a mapping
    ``{issue: epic}`` only to the issues it names — so an unrelated issue a PR also closes never inherits the
    epic's label (no issue's label lowers another's)."""
    if isinstance(fallback_parent, dict):
        return fallback_parent.get(int(number))
    return fallback_parent


def build_rigor(root, repo, issues, epic_integration=False, subissues_available=True, base_ref=None,
                fallback_parent=None, full_rules=False, cwd=None):
    """``(rigor, notices)`` — ``facts.rigor`` for a prep: the block at ``root``'s working tree, whether it is
    its base's (``sensitive_paths.rigor_block_changed``: compared with the branch's merge base with
    ``origin/<base_ref>`` — never a phase's ``--since`` base, which already holds a block an earlier phase
    lowered — and with ``origin/<base_ref>``'s tip, which a base that changed the block after the branch was
    cut reads differently), and the labels of every issue in ``issues`` with their ancestors. One unread issue makes the whole label set unread (``full``). With no issues only the block
    applies; an epic-integration target reads no labels.

    ``source.labels`` is one ``{"number", "labels", "ancestors": [{"number", "labels"}], "level", "chosen_by"}``
    per issue, ``None`` when unread. A difference or a failed comparison resolves ``full`` (``block-changed``) and
    sets ``source.block.changed``. ``full_rules`` is the caller's fact (the evaluator's ``forced_ask``): when
    true, ``steps`` is ``full``'s row while ``level`` and ``chosen_by`` still report the resolved level.

    ``fallback_parent`` is the epic a prep's own hierarchy lookup found: a number, for every issue, or a mapping
    ``{issue: epic}`` naming only the issues on that epic (the evaluator's story PR, which may close others).

    ``RIGOR_ANCESTORS_UNSUPPORTED`` says when an epic's label may not have counted: a host with no sub-issue
    relation and no ``fallback_parent`` for an issue, or a walk ending at a story with no parent
    (``epic_unread``)."""
    block, notices = read_block(root)
    block_changed = False
    if base_ref:
        changed, error = sensitive_paths.rigor_block_changed(root, base_ref)
        if error is not None:
            block_changed = "the block could not be compared with `origin/%s` (%s)" % (base_ref, error)
        elif changed == "base":
            block_changed = ("`origin/%s` reads another `<!-- %s -->` block than this checkout (the base changed it "
                             "since this checkout's commit: a branch cut before the change, or a checkout behind "
                             "its base)" % (base_ref, MARKER))
        elif changed:
            block_changed = "the branch changes the `<!-- %s -->` block against `origin/%s`" % (MARKER, base_ref)
    reads = []
    if not epic_integration:
        for number in issues or []:
            read = read_labels(repo, number, subissues_available=subissues_available,
                               fallback_parent=_fallback_for(fallback_parent, number), cwd=cwd)
            if read is None:
                reads = None
                break
            reads.append(read)
    label_sets = None if reads is None else [
        {"issue": read["issue"]["labels"], "ancestors": [a["labels"] for a in read["ancestors"]]} for read in reads
    ]
    rigor, resolve_notices = resolve(block, label_sets, epic_integration=epic_integration,
                                     block_changed=block_changed)
    if rigor["source"]["labels"] is not None:
        rigor["source"]["labels"] = [
            {"number": read["issue"]["number"], "labels": read["issue"]["labels"], "ancestors": read["ancestors"],
             "level": entry["level"], "chosen_by": entry["chosen_by"]}
            for read, entry in zip(reads, rigor["source"]["labels"])
        ]
    ancestor_notices = []
    if reads and not epic_integration:
        unsupported = [r["issue"]["number"] for r in reads
                       if _fallback_for(fallback_parent, r["issue"]["number"]) is None]
        if not subissues_available and unsupported:
            ancestor_notices.append(
                "%s: this host has no sub-issue relation, so only the own labels of %s counted; an epic's "
                "`rigor:*` label was not read" % (RIGOR_ANCESTORS_UNSUPPORTED,
                                                   ", ".join("#%d" % n for n in unsupported)))
        for read in reads:
            if read["epic_unread"]:
                ancestor_notices.append(
                    "%s: #%d's parent #%d is a story with no parent on the sub-issue relation; an epic linked only "
                    "by its `## Stories` checklist was not read" % (RIGOR_ANCESTORS_UNSUPPORTED, read["issue"]["number"],
                                                                    read["ancestors"][-1]["number"]))
    if full_rules:
        rigor["full_rules"] = True
        rigor["steps"] = copy.deepcopy(STEPS["full"])
    merged = []
    for notice in notices + resolve_notices + ancestor_notices:
        _add(merged, notice)
    return rigor, merged
