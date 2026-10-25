"""concurrency.py — the one reader of an epic plan's concurrency declaration
(``skills/planner/references/plan-schema.md``, "The concurrency declaration").

**Why this module exists.** An epic plan's ``## Integration strategy`` says which story waits on which,
and for what, under ``### Dependency graph``, and names the waves its stories run in under
``### Concurrent sets``. The coordinator follows that declaration instead of judging concurrency from
prose — facts by script, meaning by model — so the grammar needs one reader that every consumer
shares. ``prep_coordinator.py`` composes it (#115); nothing here knows about the coordinator.
``### File overlap`` is read by the plan reviewer and the just-in-time story route, never here: every
gate comes from the graph, and a collision the overlap read finds must already be a graph edge.

**Strict, with a safe worst case.** Anything off-grammar, and any set that contradicts the graph, is
reported in ``malformed`` (a closed reason set, :data:`REASONS`) and the result falls back to the serial
order of ``## Story breakdown`` — each story waiting to start on its predecessor, which is the
coordinator's behaviour before this declaration existed ("Stories are serial"). An epic plan written
before the grammar gets the same fallback. A reader never repairs a row or guesses from prose, and a
fallback never returns an empty ``start`` list, which would make every story startable at once. No
decision code is added: the serial order is a safe answer, and a new code is an architecture.md §3
contract change.

``Condition`` text is returned verbatim and never evaluated — settling a conditional edge is the
consumer's.

:func:`read` is a pure, non-emitting core (architecture.md §2's pure-core pattern): it never prints,
exits or raises. There is no CLI surface (no shebang, no ``main()``): the only callers are prep scripts,
exactly like ``story_set.py``.
"""

import re

KINDS = ("contract", "file overlap", "order", "merge order")
GATES = ("start", "build", "merge")
LEGAL_GATES = {
    "contract": ("start",),
    "file overlap": ("start", "build"),
    "order": ("start", "build"),
    "merge order": ("merge",),
}
SOURCE_DECLARED = "declared"
SOURCE_SERIAL = "serial-fallback"
FALLBACK_ABSENT = "absent"
FALLBACK_MALFORMED = "malformed"

REASON_SECTION = "section"
REASON_HEADER = "header"
REASON_CELLS = "cells"
REASON_STORY = "story"
REASON_WAITS_ON = "waits-on"
REASON_KIND = "kind"
REASON_GATES = "gates"
REASON_KIND_GATES = "kind-gates"
REASON_UNLISTED_STORY = "unlisted-story"
REASON_NO_ROW = "no-row"
REASON_SET = "set"
REASON_SET_ORDER = "set-order"
REASON_CYCLE = "cycle"
REASONS = (
    REASON_SECTION,
    REASON_HEADER,
    REASON_CELLS,
    REASON_STORY,
    REASON_WAITS_ON,
    REASON_KIND,
    REASON_GATES,
    REASON_KIND_GATES,
    REASON_UNLISTED_STORY,
    REASON_NO_ROW,
    REASON_SET,
    REASON_SET_ORDER,
    REASON_CYCLE,
)

SECTION_GRAPH = "Dependency graph"
SECTION_SETS = "Concurrent sets"
SECTION_BREAKDOWN = "Story breakdown"

HEADER = ("Story", "Waits on", "Kind", "Gates", "Condition")
NONE_CELL = "—"

_BREAKDOWN_BULLET = re.compile(r"^- #(\d+)\b")
_STORY_CELL = re.compile(r"^#(\d+)$")
_WAITS_ON_CELL = re.compile(r"^#\d+(?:, #\d+)*$")
_ISSUE_REF = re.compile(r"#(\d+)")
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")
_CONTRACT_KIND = re.compile(r"^contract (`[^`]+`(?:, `[^`]+`)*)$")
_BACKTICKED = re.compile(r"`([^`]+)`")
_FILE_OVERLAP_KIND = re.compile(r"^file overlap `([^`]+)`$")
_SET_BULLET = re.compile(r"^- \*\*(?P<name>[^:*]+): (?P<stories>#\d+(?:(?:, | and )#\d+)*)\.\*\*(?:\s.*)?$")


def _sections(lines, level):
    """``{heading text: (heading index, [body indexes])}`` for every heading of ``level`` (``"## "``
    or ``"### "``), each body running to the next heading of that level or higher. ``lines`` is a list
    of ``(index, text)``. A repeated heading keeps its first occurrence."""
    stops = ("## ",) if level == "## " else ("## ", "### ")
    found = {}
    current = None
    for index, text in lines:
        if any(text.startswith(stop) for stop in stops):
            current = None
            if text.startswith(level):
                name = text[len(level):].strip()
                if name not in found:
                    found[name] = (index, [])
                    current = name
            continue
        if current is not None:
            found[current][1].append(index)
    return found


def _cells(text):
    return [cell.strip() for cell in text.strip().strip("|").split("|")]


def _entry(section, index, lines, reason):
    return {"section": section, "line": index + 1, "raw": lines[index], "reason": reason}


def _edge(waits_on, kind, contracts=(), surface=None, condition=None):
    return {
        "waits_on": waits_on,
        "kind": kind,
        "contracts": list(contracts),
        "surface": surface,
        "condition": condition,
    }


def _parse_kind(cell):
    """``(kind, contracts, surface)`` for a ``Kind`` cell, or ``None`` when it is off-grammar. A
    trailing `` — <why>`` is prose for humans and dropped."""
    head = cell.split(" " + NONE_CELL + " ", 1)[0].strip()
    if head in ("order", "merge order"):
        return head, [], None
    match = _CONTRACT_KIND.match(head)
    if match:
        return "contract", _BACKTICKED.findall(match.group(1)), None
    match = _FILE_OVERLAP_KIND.match(head)
    if match:
        return "file overlap", [], match.group(1)
    return None


def _read_graph(heading, body, lines, listed, malformed):
    """The well-formed graph rows as ``[(index, story, [waits_on], gate, (kind, contracts, surface),
    condition)]`` plus the set of stories that have a row, or ``None`` for that set when the presence
    check cannot be computed (a header failure, or a row whose story could not be read at all). A
    subsection with no table at all is a ``header`` failure at its heading: read as an empty graph, it
    would leave every story ungated."""
    rows = []
    present = set()
    table = [index for index in body if lines[index].lstrip().startswith("|")]
    if not table:
        malformed.append(_entry(SECTION_GRAPH, heading, lines, REASON_HEADER))
        return rows, None
    header = table[0]
    if tuple(_cells(lines[header])) != HEADER:
        malformed.append(_entry(SECTION_GRAPH, header, lines, REASON_HEADER))
        return rows, None
    presence_known = True
    for index in table[1:]:
        cells = _cells(lines[index])
        if all(_SEPARATOR_CELL.match(cell) for cell in cells):
            continue
        if len(cells) != len(HEADER):
            malformed.append(_entry(SECTION_GRAPH, index, lines, REASON_CELLS))
            lenient = _ISSUE_REF.search(cells[0]) if cells else None
            if lenient:
                present.add(int(lenient.group(1)))
            else:
                presence_known = False
            continue
        story_cell, waits_cell, kind_cell, gates_cell, condition_cell = cells
        reasons = []
        story_match = _STORY_CELL.match(story_cell)
        if story_match:
            story = int(story_match.group(1))
            present.add(story)
        else:
            story = None
            reasons.append(REASON_STORY)
            lenient = _ISSUE_REF.search(story_cell)
            if lenient:
                present.add(int(lenient.group(1)))
            else:
                presence_known = False
        if waits_cell == NONE_CELL:
            if (kind_cell, gates_cell, condition_cell) != (NONE_CELL, NONE_CELL, NONE_CELL):
                reasons.append(REASON_WAITS_ON)
            for reason in reasons:
                malformed.append(_entry(SECTION_GRAPH, index, lines, reason))
            if story is not None and not reasons and story not in listed:
                malformed.append(_entry(SECTION_GRAPH, index, lines, REASON_UNLISTED_STORY))
            continue
        waits_on = []
        if _WAITS_ON_CELL.match(waits_cell):
            waits_on = [int(n) for n in _ISSUE_REF.findall(waits_cell)]
            if (story is not None and story in waits_on) or len(set(waits_on)) != len(waits_on):
                reasons.append(REASON_WAITS_ON)
        else:
            reasons.append(REASON_WAITS_ON)
        kind = _parse_kind(kind_cell)
        if kind is None:
            reasons.append(REASON_KIND)
        gate = gates_cell if gates_cell in GATES else None
        if gate is None:
            reasons.append(REASON_GATES)
        if kind is not None and gate is not None and gate not in LEGAL_GATES[kind[0]]:
            reasons.append(REASON_KIND_GATES)
        for reason in reasons:
            malformed.append(_entry(SECTION_GRAPH, index, lines, reason))
        if reasons:
            continue
        if any(n not in listed for n in [story] + waits_on):
            malformed.append(_entry(SECTION_GRAPH, index, lines, REASON_UNLISTED_STORY))
            continue
        condition = None if condition_cell == NONE_CELL else condition_cell
        rows.append((index, story, waits_on, gate, kind, condition))
    return rows, (present if presence_known else None)


def _read_sets(body, lines, listed, malformed):
    """The set bullets as ``[{"name", "stories", "line"}]`` plus every story some bullet names,
    leniently read off an off-grammar bullet too, so one bad bullet reports once."""
    sets = []
    seen = {}
    named = set()
    for index in body:
        text = lines[index]
        if not text.startswith("- "):
            continue
        match = _SET_BULLET.match(text)
        if not match:
            malformed.append(_entry(SECTION_SETS, index, lines, REASON_SET))
            named.update(int(n) for n in _ISSUE_REF.findall(text))
            continue
        stories = [int(n) for n in _ISSUE_REF.findall(match.group("stories"))]
        named.update(stories)
        if any(n not in listed for n in stories):
            malformed.append(_entry(SECTION_SETS, index, lines, REASON_UNLISTED_STORY))
            continue
        if any(n in seen or stories.count(n) > 1 for n in stories):
            malformed.append(_entry(SECTION_SETS, index, lines, REASON_SET))
            continue
        position = len(sets)
        for n in stories:
            seen[n] = position
        sets.append({"name": match.group("name").strip(), "stories": stories, "line": index})
    return sets, seen, named


def _reaches(edges, start, goal):
    stack, visited = [start], set()
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in visited:
            continue
        visited.add(node)
        stack.extend(edges.get(node, ()))
    return False


def _serial(breakdown):
    stories = []
    previous = None
    for number, _ in breakdown:
        start = [] if previous is None else [_edge(previous, "order")]
        stories.append({"number": number, "set": None, "start": start, "build": [], "merge": []})
        previous = number
    return stories


def _result(source, reason, stories, sets, malformed):
    return {
        "source": source,
        "fallback_reason": reason,
        "stories": stories,
        "sets": sets,
        "malformed": sorted(malformed, key=lambda entry: entry["line"]),
    }


def read(plan_body):
    """An epic plan's concurrency declaration as gates per story.

    Returns ``{"source", "fallback_reason", "stories", "sets", "malformed"}``:

    - ``source`` — :data:`SOURCE_DECLARED`, or :data:`SOURCE_SERIAL` with ``fallback_reason``
      ``"absent"`` (neither ``### Dependency graph`` nor ``### Concurrent sets`` under
      ``## Integration strategy``, or no ``## Story breakdown`` stories) or ``"malformed"``.
    - ``stories`` — ``## Story breakdown`` order (a repeated ``- #<N>`` bullet keeps its first place;
      other lines there are not stories), each ``{"number", "set", "start", "build",
      "merge"}``; a gate list holds ``{"waits_on", "kind", "contracts", "surface", "condition"}``
      edges in row order. On a fallback each story waits to start on its predecessor (kind
      ``order``) and ``set`` is ``None``.
    - ``sets`` — ``[{"name", "stories"}]`` in declared order, ``[]`` on a fallback.
    - ``malformed`` — ``[{"section", "line", "raw", "reason"}]``, 1-based ``line``, plan-line order,
      ``reason`` from :data:`REASONS`; kept on a fallback so the consumer can say why.
    """
    if not isinstance(plan_body, str):
        plan_body = ""
    lines = plan_body.splitlines()
    numbered = list(enumerate(lines))
    top = _sections(numbered, "## ")

    breakdown = []
    listed = set()
    if SECTION_BREAKDOWN in top:
        for index in top[SECTION_BREAKDOWN][1]:
            match = _BREAKDOWN_BULLET.match(lines[index])
            if match and int(match.group(1)) not in listed:
                listed.add(int(match.group(1)))
                breakdown.append((int(match.group(1)), index))

    strategy = top.get("Integration strategy")
    subsections = _sections([(i, lines[i]) for i in strategy[1]], "### ") if strategy else {}
    graph = subsections.get(SECTION_GRAPH)
    sets_section = subsections.get(SECTION_SETS)

    if not breakdown or (graph is None and sets_section is None):
        return _result(SOURCE_SERIAL, FALLBACK_ABSENT, _serial(breakdown), [], [])

    malformed = []
    if graph is None or sets_section is None:
        present_name, present = (SECTION_SETS, sets_section) if graph is None else (SECTION_GRAPH, graph)
        malformed.append(_entry(present_name, present[0], lines, REASON_SECTION))

    rows, present = [], None
    if graph is not None:
        rows, present = _read_graph(graph[0], graph[1], lines, listed, malformed)
    sets, set_of, named = [], {}, None
    if sets_section is not None:
        sets, set_of, named = _read_sets(sets_section[1], lines, listed, malformed)

    for number, index in breakdown:
        if present is not None and number not in present:
            malformed.append(_entry(SECTION_BREAKDOWN, index, lines, REASON_NO_ROW))
        if named is not None and number not in named:
            malformed.append(_entry(SECTION_BREAKDOWN, index, lines, REASON_SET))

    if not malformed:
        edges = {}
        for _, story, waits_on, _, _, _ in rows:
            edges.setdefault(story, []).extend(waits_on)
        cycle_row = None
        for index, story, waits_on, gate, _, _ in rows:
            for target in waits_on:
                later = set_of[target] > set_of[story]
                same = set_of[target] == set_of[story]
                if later or (same and gate == "start"):
                    malformed.append(_entry(SECTION_GRAPH, index, lines, REASON_SET_ORDER))
                    break
            if cycle_row is None and any(_reaches(edges, target, story) for target in waits_on):
                cycle_row = index
        if cycle_row is not None:
            malformed.append(_entry(SECTION_GRAPH, cycle_row, lines, REASON_CYCLE))

    if malformed:
        return _result(SOURCE_SERIAL, FALLBACK_MALFORMED, _serial(breakdown), [], malformed)

    by_story = {
        number: {"number": number, "set": None, "start": [], "build": [], "merge": []}
        for number, _ in breakdown
    }
    for entry in sets:
        for number in entry["stories"]:
            by_story[number]["set"] = entry["name"]
    for _, story, waits_on, gate, kind, condition in rows:
        name, contracts, surface = kind
        for target in waits_on:
            by_story[story][gate].append(_edge(target, name, contracts, surface, condition))
    return _result(
        SOURCE_DECLARED,
        None,
        [by_story[number] for number, _ in breakdown],
        [{"name": entry["name"], "stories": entry["stories"]} for entry in sets],
        [],
    )
