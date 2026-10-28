"""delivery_log.py — the epic delivery log's read model (``skills/_shared/epic-delivery-log.md``).

One deterministic mechanism, one home: given an epic issue's already-fetched comment thread, hand
back what each shipped story delivered — resolved across the two tiers, ordered by merge order, and
labelled with which tier answered.

**Why this module exists.** The log has two real consumers with different needs — ``prep_evaluator``
(the writer's prep: *where is this story already recorded, and under which id?*) and
``prep_planner`` (the reader's prep, at two call sites: *what has every predecessor delivered?*). A
per-prep local helper would put the tier resolution and the ``log_source`` classification in three
places, and the one that disagreed would be the one nobody read until a plan grounded on a stale
contract. That is the same two-places-disagree failure the hardcoded doc-path lists were retired
for. This is the ``oq_tracker.py`` / ``doc_catalogue.py`` promotion pattern: one literal algorithm
serving one shared contract named by real consumers, not similarly-shaped copies.

**Why the read is a thread scan and never a marker gather.** ``gh_gather``'s ``marker_prefix`` is a
single-match lookup — more than one hit is ``MARKER_AMBIGUOUS`` by construction — so it cannot
express "collect every per-story entry". It also could not serve the writer: the evaluator's epic
facts are built from the PR's base ref alone (no story number in hand, and a PR's closing-issue set
may be empty or plural), and a per-story lookup would blind it to the legacy tier, letting a
re-record write a second, divergent record for one story. The thread is already in the gather's
envelope, fully paginated, so scanning it costs **zero** extra ``gh`` calls for either consumer.

Every function is a pure, non-emitting core (architecture.md §2's pure-core pattern, the S8 lock):
never prints, never exits, never raises on malformed input. A prep composes these in-process and
merges the returned notices/decision into its own envelope. There is no CLI surface (no shebang, no
``main()``): the only callers are prep scripts, exactly like ``branching.py``.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib.decisions import MARKER_AMBIGUOUS, needs_decision  # noqa: E402
from pipelib.trust import is_trusted_comment  # noqa: E402

# The legacy tier: ONE comment holding every entry. Read forever, never written again, no backfill
# (skills/_shared/epic-delivery-log.md). Kept as a first-class token, not a historical footnote —
# every epic whose log predates per-story comments lives here permanently.
MARKER_V1 = "<!-- epic-delivery-log:v1 -->"

# The per-story tier. `<N>` is the story's issue number, which is what preserves the
# one-marker-one-comment invariant: two comments bearing the SAME `:story:<N>` marker are a genuine
# duplicate, while a shared marker across entries would make "N entries" and "one entry duplicated
# N times" indistinguishable. `:story:15 -->` is not a prefix of `:story:150 -->` — the trailing
# ` -->` terminates the match — so no story's marker can collide with another's.
MARKER_V2_PREFIX = "<!-- epic-delivery-log:v2:story:"
MARKER_V2_SUFFIX = " -->"

# What both tiers' markers start with — how a reader names the family in a `MARKER_UNTRUSTED` notice.
MARKER_FAMILY = "<!-- epic-delivery-log:"

SOURCE_ENTRIES = "entries"
SOURCE_LEGACY = "legacy"
SOURCE_MIXED = "mixed"

# The entry line, identical in both tiers so one parser serves both and a tier migration never
# becomes a parser migration (the contract states this deliberately).
_ENTRY_LINE_RE = re.compile(r"^-\s+#(\d+)\s+—\s+delivered:", re.MULTILINE)

_MARKER_V2_RE = re.compile(
    re.escape(MARKER_V2_PREFIX) + r"(\d+)" + re.escape(MARKER_V2_SUFFIX)
)


def entry_marker(story_number):
    """The per-story marker line for ``story_number``. The single place this string is built."""
    return "%s%s%s" % (MARKER_V2_PREFIX, story_number, MARKER_V2_SUFFIX)


def story_number_for(comment):
    """The story number a per-story entry comment names, or ``None`` if it is not one.

    Matches the marker on the body's FIRST line only: the contract puts it there, and a marker
    appearing later in a body is quoted text, not a record.
    """
    body = (comment.get("body") or "") if comment else ""
    first_line = body.split("\n", 1)[0]
    match = _MARKER_V2_RE.fullmatch(first_line.strip())
    return int(match.group(1)) if match else None


def comment_rest_id(comment):
    """A thread-located comment's REST **numeric** id (``databaseId``).

    The normalized thread's ``id`` is the GraphQL node id, which every REST comment endpoint
    (``--delete-marker-id``, ``edit-comment``) 404s on — #34, where a node id reached the delete
    path, the delete silently failed, and the log accumulated duplicates. Same rule and rationale as
    ``prep_planner._marker_comment_id``; stated here too because this module's callers must not have
    to know which of them normalized the comment.
    """
    if comment is None:
        return None
    return comment.get("databaseId", comment.get("id"))


def _legacy_story_numbers(body):
    """The story numbers a legacy body's entry lines name, in body order."""
    return [int(number) for number in _ENTRY_LINE_RE.findall(body or "")]


def collect(thread_list):
    """Resolve an epic's delivery log from its comment thread.

    Returns ``(log, decision)``. ``decision`` is a ``MARKER_AMBIGUOUS`` payload when one story
    carries more than one per-story comment, or the epic carries more than one legacy comment — both
    are genuine duplicates, and both are the caller's to degrade or forward (the evaluator degrades:
    a merge already judged on its own gates must not stall on its log; the planner forwards, because
    an unknown shape for a predecessor's contract is exactly what it must not ground on).

    **A decision does not empty the log.** The duplicated story is omitted — its shape is precisely
    what is unknown — and every other story still resolves, so a writer recording story B is not
    blocked by a duplicate on story A. Ambiguity in the legacy tier likewise leaves the per-story
    tier readable: the two tiers fail independently.

    ``log`` keys:

    ``present``      -- any record at all exists.
    ``log_source``   -- ``entries`` / ``legacy`` / ``mixed``, or ``None`` when absent.
    ``entries``      -- the unioned records, in merge order. Each carries ``story``, ``tier``, and
                        for a per-story record its ``comment_id`` / ``comment_url``.
    ``entry_count``  -- how many stories are recorded (never double-counting a story in both tiers).
    ``duplicated_stories``
                     -- stories carrying more than one per-story comment, hence omitted above.
    ``legacy``       -- the legacy comment's ``comment_id`` / ``comment_url`` / ``body``, or ``None``.
    ``untrusted``    -- how many log-marked comments (either tier) an untrusted author wrote; skipped
                        before anything above is read (``pipelib.trust``, #179).
    ``text``         -- the whole log as one document, for staging to a single path.
    ``text_chars``   -- ``len(text)``, so a caller can report growth without re-measuring.
    """
    thread_list = thread_list or []

    # Only a trusted author's log comment counts (pipelib.trust, #179): the log-marked comments of
    # both tiers are partitioned first, so a stranger's comment can neither record a story, supply its
    # contract to the planner, make a story ambiguous, nor become the `comment_id` the evaluator's
    # re-record edits. Unmarked comments pass through untouched and are never counted.
    untrusted = 0
    kept = []
    for comment in thread_list:
        if _is_log_comment(comment) and not is_trusted_comment(comment):
            untrusted += 1
            continue
        kept.append(comment)
    thread_list = kept

    # Group by story first, so a duplicate is identified per story rather than aborting the read.
    # "Many comments" is the normal state now, so one story's duplicate must not erase every other
    # story's record — the writer recording story B has to be able to proceed.
    by_story = {}
    for comment in thread_list:
        story = story_number_for(comment)
        if story is None:
            continue
        by_story.setdefault(story, []).append(comment)

    duplicated = {story: cs for story, cs in by_story.items() if len(cs) > 1}
    per_story = {story: cs[0] for story, cs in by_story.items() if len(cs) == 1}

    legacy_matches = [
        comment
        for comment in thread_list
        if comment is not None and (comment.get("body") or "").startswith(MARKER_V1)
    ]

    decision = None
    if duplicated:
        # Name the first duplicated story in thread order — deterministic, and the one the writer
        # reports a recovery for. The resolved half is still returned alongside.
        story = next(s for s in (story_number_for(c) for c in thread_list) if s in duplicated)
        clashing = duplicated[story]
        decision = needs_decision(
            MARKER_AMBIGUOUS,
            summary="%d delivery-log comments name story #%s — expected at most one per story"
            % (len(clashing), story),
            context={
                "marker_prefix": entry_marker(story),
                "story": story,
                "duplicated_stories": sorted(duplicated),
                "comment_ids": [comment_rest_id(c) for c in clashing],
                "comment_urls": [c.get("url") for c in clashing],
            },
            options=[
                "inspect each comment and pick the one to treat as current",
                "delete the stale duplicate comment(s), then re-run",
            ],
        )
    elif len(legacy_matches) > 1:
        decision = needs_decision(
            MARKER_AMBIGUOUS,
            summary="%d comments match the legacy epic-delivery-log marker %r — expected at most one"
            % (len(legacy_matches), MARKER_V1),
            context={
                "marker_prefix": MARKER_V1,
                "comment_ids": [comment_rest_id(c) for c in legacy_matches],
                "comment_urls": [c.get("url") for c in legacy_matches],
            },
            options=[
                "inspect each comment and pick the one to treat as current",
                "delete the stale duplicate comment(s), then re-run",
            ],
        )

    # An ambiguous legacy tier contributes nothing (there is no way to choose between two
    # monoliths), but the per-story half stays readable — the tiers fail independently.
    legacy_comment = legacy_matches[0] if len(legacy_matches) == 1 else None
    legacy_body = (legacy_comment.get("body") or "") if legacy_comment else ""

    # Union by story number, the per-story entry winning where a story appears in both tiers. Never
    # take one tier and drop the other: that silently loses shipped stories, and two shapes for one
    # story would hand Dimension 8 a contradiction (the contract's precedence rule). A story whose
    # per-story tier is ambiguous is omitted from both halves — its shape is exactly what is unknown.
    entries = []
    for story in _legacy_story_numbers(legacy_body):
        if story in per_story or story in duplicated:
            continue
        entries.append({"story": story, "tier": SOURCE_LEGACY})

    # Per-story entries in thread order, which IS merge order — comments are returned in ascending
    # creation order and nothing maintains a position field. An entry edited in place (a re-record)
    # keeps its original position, which is the intent: the position records when the story shipped.
    for comment in thread_list:
        story = story_number_for(comment)
        if story is None or story not in per_story:
            continue
        entries.append(
            {
                "story": story,
                "tier": SOURCE_ENTRIES,
                "comment_id": comment_rest_id(comment),
                "comment_url": comment.get("url"),
                "created_at": comment.get("createdAt"),
            }
        )

    if per_story and legacy_comment is not None:
        log_source = SOURCE_MIXED
    elif per_story:
        log_source = SOURCE_ENTRIES
    elif legacy_comment is not None:
        log_source = SOURCE_LEGACY
    else:
        log_source = None

    text = _render(legacy_body, per_story, duplicated, thread_list)

    log = {
        "present": log_source is not None,
        "log_source": log_source,
        "entries": entries,
        "entry_count": len(entries),
        "duplicated_stories": sorted(duplicated),
        "legacy": (
            {
                "comment_id": comment_rest_id(legacy_comment),
                "comment_url": legacy_comment.get("url"),
                "body": legacy_body,
            }
            if legacy_comment is not None
            else None
        ),
        "untrusted": untrusted,
        "text": text,
        "text_chars": len(text),
    }
    return log, decision


def _absent_log():
    """The shape a caller gets alongside a decision — no record read, nothing invented."""
    return {
        "present": False,
        "log_source": None,
        "entries": [],
        "entry_count": 0,
        "duplicated_stories": [],
        "legacy": None,
        "untrusted": 0,
        "text": "",
        "text_chars": 0,
    }


def _is_log_comment(comment):
    """Whether ``comment`` carries either tier's marker — the subset the author check applies to."""
    if comment is None:
        return False
    return story_number_for(comment) is not None or (comment.get("body") or "").startswith(MARKER_V1)


def _render(legacy_body, per_story, duplicated, thread_list):
    """The whole log as one document, for staging to the single path the plan reviewer reads.

    The legacy body first (its entries shipped before any per-story comment could exist), then the
    per-story entries in thread order. A story recorded in both tiers appears once, from its
    per-story comment — the same precedence the ``entries`` list applies.
    """
    parts = []
    if legacy_body:
        superseded = set(per_story) | set(duplicated)
        kept = [
            line
            for line in legacy_body.split("\n")
            if not _line_names_superseded_story(line, superseded)
        ]
        parts.append("\n".join(kept).strip())
    for comment in thread_list:
        story = story_number_for(comment)
        if story is None or story not in per_story:
            continue
        parts.append((comment.get("body") or "").strip())
    return "\n\n".join(part for part in parts if part)


def _line_names_superseded_story(line, superseded):
    match = _ENTRY_LINE_RE.match(line)
    return match is not None and int(match.group(1)) in superseded


def record_for_story(log, story_number):
    """Where ``story_number`` is already recorded — the writer's question.

    Returns ``(tier, record)``: ``("entries", {...})`` when the story has its own comment (update it
    in place), ``("legacy", {...})`` when its line lives in the legacy body (update that line and
    repost, never add a second record in the other tier), or ``(None, None)`` when unrecorded.
    """
    if not log or not log.get("present"):
        return None, None
    for entry in log.get("entries") or []:
        if entry.get("story") == story_number and entry.get("tier") == SOURCE_ENTRIES:
            return SOURCE_ENTRIES, entry
    legacy = log.get("legacy")
    if legacy and story_number in _legacy_story_numbers(legacy.get("body")):
        return SOURCE_LEGACY, legacy
    return None, None
