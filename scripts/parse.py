#!/usr/bin/env python3
"""parse.py — the single parser for the shared GitHub body grammars (architecture.md §2, §3;
docs/implementation.md S5), ending the per-skill prompt-side parsing v1 did inline in each
`SKILL.md`. This is a from-scratch v2 script: there is no v1 `parse.py` to port from — v1 skills
parsed these grammars as an implicit reading step inside the prompt itself, which is exactly what
this script exists to replace with a deterministic, testable implementation.

File I/O only. No `gh`, no `git`, no network, no subprocess of any kind — every subcommand reads
one already-materialized file path from local disk and emits one architecture.md §3 envelope on
stdout. Callers (prep scripts, playbooks) are responsible for getting the body onto disk first
(a `gh_gather.py` result's `*_path`, or any other already-staged file); this module never fetches
anything itself.

Subcommands, each `<subcommand> <body-file>` except `security-coverage`:

    parse.py dod <body-file> [--render]
        Parses the `## Definition of done` section's top-level checkbox bullets per
        `skills/_shared/dod-annotations.md`'s closed set of five annotation forms (plus the
        "no annotation" bare bullet). `ok` payload: `{"dod": [{"index", "text", "checked",
        "annotation"}, ...]}`, 1-based `index`, `annotation` either `null` (bare bullet) or an
        object with a `form` key naming the closed-set form
        (`closed-by-phase-commit` | `closed-by-phase-operator` | `closed-by-commit` |
        `resolver-claimed-evaluator-rejected` | `previously-claimed-on-closed-pr`) plus that
        form's own fields (`phase`, `sha`, `date`, `reason`, `pr` as applicable — see
        `_ANNOTATION_FORMS` below for the exact field set per form). No `## Definition of done`
        section in the body is not an error — `ok` with `dod: []` (dod-annotations.md's "Edge
        cases"). The payload also carries `present` (whether the section exists, which `dod: []`
        alone cannot say) and `other_checklists` (`[{"heading", "count"}]` — checkbox lists under
        any other `##` heading, `## Stories` excepted; see `dod_summary`): the drafter's
        post-staging check that its checklist landed under the one heading every reader parses.

        An **unknown annotation** (the trailing `(...)` doesn't start with one of the three
        recognized prefix words) or **two annotations stacked** on one bullet (a second
        annotation-shaped parenthetical immediately precedes the one already peeled off the tail)
        returns `DOD_MALFORMED` (`needs_decision`), never a crash, never a best-effort guess at
        what the author meant.

        `--render`: instead of the `dod` payload, re-emits the **exact parsed bullets** as
        Markdown text under `{"rendered": "...bullet lines, joined by \\n, with a single trailing
        newline..."}` — see the docstring of `render_dod_bullets` for the byte-identical
        round-trip guarantee this mode exists to prove. `--render` still parses first: a malformed
        body still returns `DOD_MALFORMED` under `--render`, it does not silently render a
        best-effort guess.

    parse.py oq-links <body-file>
        Parses the `## Open questions` section (marked by the `<!-- open-question-links:v1 -->`
        first line) per `skills/_shared/open-question-links.md`. `ok` payload: `{"open_questions":
        [{"oq_id", "source", "gates", "disposition", "question", "audience", "default",
        "retires_when"}, ...]}`. `disposition` is one of the closed set of exactly three values
        (`scoped-out` | `in-scope (blocked)` | `provisional-default`); `default`/`retires_when`
        are `null` except on `provisional-default` entries (the only disposition that carries
        them). No `## Open questions` section (or a section without the marker) is not an error
        — `ok` with `open_questions: []` (open-question-links.md's Format section: "Omit the
        whole section when no OQ gates the issue").

    parse.py phases <body-file> [--issue-body <issue-body-file>]
        Parses a plan's `## Phases` section per
        `skills/planner/references/plan-schema.md`'s numbered-list-with-structured-
        keys grammar. `ok` payload: `{"phases": [{"number", "title", "kind", "ships",
        "closes_dod", "deliverable", "depends_on", "sub_issue", "checkpoint"}, ...]}`. `kind` is one of the
        closed set of exactly three values (`code-shipping` | `operator` | `decision-only`);
        `closes_dod` is either the literal string `"(none)"` or a list of 1-based ints;
        `depends_on` is either the literal string `"(none)"` or a list of ints (phase numbers);
        `sub_issue` is tri-state — `None` when the optional `sub-issue:` line is absent
        (**unmapped**), the literal string `"(none)"` for an explicit **substrate** claim, or an
        int (the one sub-issue this phase serves). `checkpoint` is `None` when the optional
        `checkpoint:` line is absent (the resolver reads that as `pause`), else `"pause"` or
        `"continue"` — whether the resolver may continue to the next phase in the same session
        once this one ships. No `## Phases` section is
        `ok` with `phases: []` — a plan legitimately omits `## Phases` for single-phase issues and
        epics (plan-schema.md: "multi-phase issues only — omit for single-phase; epics use the
        dedicated `## Story breakdown` / `## Integration strategy` sections"), so an absent
        section is not itself malformed; only a **present-but-broken** section is.

        The `ok` payload also carries `size` — `pipelib.limits.body_size` of the whole body
        (`chars`, `limit_chars`, `headroom_chars`, `over_limit`). It is the one non-grammar fact
        here, carried because this is the call the planner already runs after every staging, fresh
        draft included: a separate size call is one the session forgets, and then hand-counts.
        Size is never a decision — an over-cap body is still `ok`, and fixing it is the planner's.

        `--issue-body`: the issue the plan is for. The payload then also carries `dod_coverage` —
        `dod_coverage(phases, dod_summary(issue body))`, `null` under the single-phase fallback —
        so a `closes-dod` index pointing past the DoD, or at a DoD that does not exist, is caught
        by the same call that proves the grammar, before the plan reviewer runs. Findings, never a
        decision, exactly like `size`.

        A malformed `## Phases` section (a numbered entry missing a required structured key, an
        entry whose `kind:` value is outside the closed set, non-sequential/duplicate phase
        numbers, a `closes-dod`/`depends-on` value that isn't `(none)` and doesn't parse as the
        documented reference-list shape, a `sub-issue:` value that is neither `(none)` nor
        exactly one `#<N>`, or a `checkpoint:` value that is neither `pause` nor `continue`)
        returns `PHASES_MALFORMED`.

    parse.py security-coverage <changed-files> <findings>
        Not a contract parser: an always-`ok` post-staging check with no decision code
        (`skills/_shared/security-review.md` "The procedure" step 1). `<changed-files>` is the staged
        `git diff --name-only` list, one repo-relative path per line; `<findings>` is the security
        pass's whole reply, whose `## Findings` ends with a `Covered:` block of `- <path>` lines. `ok`
        payload: `{"covered", "uncovered", "extra"}` — the block's paths, the listed paths absent from
        it, and the covered paths absent from the list, each in source order, compared as exact strings.
        A reply with no `Covered:` block covers nothing.

Envelope conformance (architecture.md §3) on every output: exactly one JSON object on stdout,
`status` is `ok` or `needs_decision`, `notices: []` always present. Exit 0 on both `ok` and
`needs_decision` (a decision is a valid outcome, not a process failure); exit 2 is reserved for a
genuine usage error (missing/unreadable file, unknown subcommand) — no envelope in that case, per
the exit-code contract every other v2 script also honors.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib.decisions import DOD_MALFORMED, PHASES_MALFORMED, needs_decision  # noqa: E402
from pipelib.envelope import (  # noqa: E402
    EXIT_OK,
    EXIT_USAGE_ERROR,
    emit_needs_decision,
    emit_ok,
)
from pipelib.limits import body_size  # noqa: E402

# ============================================================================================
# Shared file-reading helper
# ============================================================================================


def _read_text_or_die(path_str):
    """Read `path_str` as UTF-8 text (architecture.md §1's encoding pin). Exits 2 with a faithful
    stderr message on a missing/unreadable file or a decode failure — a usage error, not a
    `needs_decision` (the caller gave a bad path; there is no operator decision to surface for
    "the file isn't there").
    """
    path = Path(path_str)
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except OSError as exc:
        sys.stderr.write("PARSE_FILE_UNREADABLE: %s: %s\n" % (path_str, exc))
        sys.exit(EXIT_USAGE_ERROR)
    except UnicodeDecodeError as exc:
        sys.stderr.write("PARSE_FILE_NOT_UTF8: %s: %s\n" % (path_str, exc))
        sys.exit(EXIT_USAGE_ERROR)


def _find_section(lines, heading_pattern):
    """Locate a `## <heading>` section (case-insensitive on the heading text, matching
    dod-annotations.md's "Section finder": "case-insensitive on the section title"). Returns
    `(start_index, end_index)` — a 0-based half-open slice `lines[start_index:end_index]` of the
    section's body lines (the heading line itself excluded) — or `None` if no heading matches.
    Reads "subsequent lines until the next `##` heading or EOF", exactly as dod-annotations.md
    specifies (used identically for oq-links' `## Open questions` and phases' `## Phases`, since
    all three shared contracts describe the same section-finder shape).
    """
    heading_re = re.compile(r"^##\s+" + heading_pattern + r"\s*(?:\(.*\))?\s*$", re.IGNORECASE)
    next_heading_re = re.compile(r"^##(?!#)")
    start = None
    for i, line in enumerate(lines):
        if heading_re.match(line):
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if next_heading_re.match(lines[j]):
            end = j
            break
    return start, end


# ============================================================================================
# `dod` subcommand — skills/_shared/dod-annotations.md
# ============================================================================================

# dod-annotations.md's own "Recognition regex (informal)", ported verbatim to Python syntax. Group
# 1: leading whitespace. Group 2: checkbox state char (` `/`x`/`X`). Group 3: bullet text (non-
# greedy up to an optional trailing annotation). Group 4: the annotation's prefix word. Group 5:
# everything after the prefix word, up to the closing paren.
_DOD_BULLET_RE = re.compile(
    r"^( *)-[ ]+\[([ xX])\][ ]+(.+?)(?:[ ]+\((closed by|resolver claimed phase|previously claimed by) (.+)\))?$"
)

# `_DOD_BULLET_RE`'s prefix alternation is intentionally narrow (the exact three closed-set prefix
# words) so a well-formed non-annotation trailing parenthetical in ordinary bullet prose — e.g.
# `- [ ] Support CSV export (stretch goal)` — is correctly left alone as plain text, never flagged.
# But that narrowness means a genuine **near-miss** annotation attempt (a prefix word typo, an
# attribution phrase using the wrong verb) fails to match the alternation at all and gets silently
# absorbed into the bullet's `text` group instead of being recognized as an attempted-but-broken
# annotation. This second regex is the "does this trailing parenthetical look like an annotation
# attempt regardless of its exact prefix wording" heuristic: it fires on the same attribution
# sub-vocabulary the three real forms are built from (`phase <N>`, a 7-char hex commit sha,
# `operator action <date>`, or `evaluator rejected`) — content ordinary free-text bullets don't
# happen to contain — so a plain stretch-goal/scope-note parenthetical never matches this pattern
# and only a genuine mis-worded attribution attempt does.
_ANNOTATION_LOOKALIKE_RE = re.compile(
    r"\((?:[^()]*\bphase \d+\b[^()]*|[^()]*\b[0-9a-f]{7}\b[^()]*|[^()]*\boperator action\b[^()]*"
    r"|[^()]*\bevaluator rejected\b[^()]*)\)\s*$"
)

# A top-level bullet only — dod-annotations.md's "Indexing and parsing rules": "Sub-bullets
# (indented with two or more leading spaces beneath a top-level bullet) are detail, not DoD
# items." A top-level bullet's own leading-whitespace group (captured group 1 above) must
# therefore be empty or a single space — never two or more — for the line to count at all.
_TOP_LEVEL_INDENT_RE = re.compile(r"^ {0,1}$")

# Per-form tail parsers. Each dispatches on the exact prefix word `_DOD_BULLET_RE` captured, then
# further parses that prefix's own tail shape (dod-annotations.md's "Then dispatch on the prefix
# word" list). Every regex is anchored full-match (`fullmatch`) so a tail that merely *starts with*
# the right shape but has trailing garbage is rejected, not silently truncated.
_TAIL_CLOSED_BY_PHASE_RE = re.compile(
    r"^phase (\d+), commit ([0-9a-f]{7})$"
)
_TAIL_CLOSED_BY_PHASE_OPERATOR_RE = re.compile(
    r"^phase (\d+), operator action (\d{4}-\d{2}-\d{2})$"
)
_TAIL_CLOSED_BY_COMMIT_RE = re.compile(r"^commit ([0-9a-f]{7})$")
_TAIL_RESOLVER_REJECTED_RE = re.compile(
    r"^(\d+), commit ([0-9a-f]{7}); evaluator rejected: (.+)$"
)
# The rejection reason itself must contain no literal parenthesis — dod-annotations.md's "Edge
# cases": "the evaluator-rejection reason should sanitize any `(` / `)` to `[` / `]` to keep the
# parser unambiguous." A reason containing a literal `(`/`)` is therefore never a well-formed
# free-text rationale; it is either an unsanitized author mistake or (the adversarial case this
# guards) a **second annotation stacked** inside what the outer bullet regex's greedy tail-capture
# folded into this one's free-text reason field (dod-annotations.md's "never carries two
# annotations stacked" invariant) — either way, `DOD_MALFORMED`, never silently accepted as
# reason text.
_REASON_CONTAINS_PAREN_RE = re.compile(r"[()]")
_TAIL_PREVIOUSLY_CLAIMED_RE = re.compile(
    r"^phase (\d+), commit ([0-9a-f]{7}) on closed PR #(\d+)$"
)

# Which prefix words pair with which tail regex + resulting form name + field extractor. Order
# matters only in that "closed by" fans out to three sub-shapes tried in this order (phase+commit,
# phase+operator-date, bare commit) — the first that fullmatches wins; if none do, it's an unknown
# annotation shape under a recognized prefix word, which is still `DOD_MALFORMED` (a prefix word
# alone does not certify the whole annotation parses).
def _parse_closed_by_tail(tail):
    m = _TAIL_CLOSED_BY_PHASE_RE.fullmatch(tail)
    if m:
        return {
            "form": "closed-by-phase-commit",
            "phase": int(m.group(1)),
            "sha": m.group(2),
        }
    m = _TAIL_CLOSED_BY_PHASE_OPERATOR_RE.fullmatch(tail)
    if m:
        return {
            "form": "closed-by-phase-operator",
            "phase": int(m.group(1)),
            "date": m.group(2),
        }
    m = _TAIL_CLOSED_BY_COMMIT_RE.fullmatch(tail)
    if m:
        return {"form": "closed-by-commit", "sha": m.group(1)}
    return None


def _parse_resolver_claimed_tail(tail):
    m = _TAIL_RESOLVER_REJECTED_RE.fullmatch(tail)
    if not m:
        return None
    reason = m.group(3)
    if _REASON_CONTAINS_PAREN_RE.search(reason):
        return None
    return {
        "form": "resolver-claimed-evaluator-rejected",
        "phase": int(m.group(1)),
        "sha": m.group(2),
        "reason": reason,
    }


def _parse_previously_claimed_tail(tail):
    m = _TAIL_PREVIOUSLY_CLAIMED_RE.fullmatch(tail)
    if not m:
        return None
    return {
        "form": "previously-claimed-on-closed-pr",
        "phase": int(m.group(1)),
        "sha": m.group(2),
        "pr": int(m.group(3)),
    }


_PREFIX_TAIL_PARSERS = {
    "closed by": _parse_closed_by_tail,
    "resolver claimed phase": _parse_resolver_claimed_tail,
    "previously claimed by": _parse_previously_claimed_tail,
}

# Every annotation form's own render template, keyed by `form` — the exact inverse of the tail
# parsers above, used by `--render` to reconstruct the trailing `(...)` byte-for-byte from the
# parsed fields (never from the original raw line, so a render bug can't hide behind an
# accidental pass-through of unparsed text).
_FORM_RENDERERS = {
    "closed-by-phase-commit": lambda a: "closed by phase %d, commit %s" % (a["phase"], a["sha"]),
    "closed-by-phase-operator": lambda a: "closed by phase %d, operator action %s"
    % (a["phase"], a["date"]),
    "closed-by-commit": lambda a: "closed by commit %s" % a["sha"],
    "resolver-claimed-evaluator-rejected": lambda a: (
        "resolver claimed phase %d, commit %s; evaluator rejected: %s"
        % (a["phase"], a["sha"], a["reason"])
    ),
    "previously-claimed-on-closed-pr": lambda a: (
        "previously claimed by phase %d, commit %s on closed PR #%d"
        % (a["phase"], a["sha"], a["pr"])
    ),
}

# Note on detecting two annotations stacked on one bullet (dod-annotations.md's "A bullet never
# carries two annotations stacked" invariant): `_DOD_BULLET_RE`'s `text` group is non-greedy but
# its `tail` group is greedy and anchored to the line's end, so the regex always prefers capturing
# *any* trailing `(closed by|resolver claimed phase|previously claimed by) ...)` suffix as the
# annotation rather than leaving it inside `text` — meaning `text` can never itself retain a
# second, later, closed-set-prefixed annotation to detect. A stacked second annotation instead
# always ends up folded into the *first* annotation's own `tail` capture. Detection therefore
# happens in two other places instead, each matching how that folding actually manifests: (1) the
# `_ANNOTATION_LOOKALIKE_RE` check below, when `prefix_word` came back `None` because the folded
# tail doesn't start with a recognized prefix at all; (2) each tail sub-shape regex is `fullmatch`-
# anchored, so a stacked annotation folded into a *recognized* prefix's tail simply fails to
# fullmatch that prefix's known field shapes (extra trailing content after the expected fields) —
# except `resolver claimed phase`'s free-text `reason` field, which would otherwise swallow a
# stacked annotation as ordinary reason text; `_REASON_CONTAINS_PAREN_RE` (below) closes that gap.


class _DodMalformed(Exception):
    """Internal signal: a DoD bullet's annotation is outside the closed set (unknown prefix word,
    a recognized prefix word whose tail doesn't match any of its sub-shapes, or two annotations
    stacked on one bullet). Carries a human-readable `reason` for the `needs_decision` summary.
    Never escapes this module.
    """

    def __init__(self, reason, line_number, raw_line):
        super().__init__(reason)
        self.reason = reason
        self.line_number = line_number
        self.raw_line = raw_line


# The one heading pattern both DoD entry points feed to `_find_section`. A single constant, not
# two spelled literals: `has_dod_section` (the create-vs-append fact) and `parse_dod_bullets`
# (the bullets) must never disagree on what counts as the section — a one-sided tolerance edit
# would let a caller append a SECOND `## Definition of done` onto a body that already has one.
_DOD_HEADING_PATTERN = r"Definition of done"


def has_dod_section(body_text):
    """Whether the body carries a `## Definition of done` section at all — distinct from
    `parse_dod_bullets` returning `[]`, which conflates "no section" with "a section holding no
    top-level checkbox bullets". A caller that must decide whether to *create* the section (the
    requirements-gatherer's DoD write) keys off this; a caller that only projects onto existing
    bullets never needs the distinction (dod-annotations.md's "Skip projection").
    """
    return _find_section(body_text.splitlines(), _DOD_HEADING_PATTERN) is not None


# Any `##` heading (never `###`), its text captured. The same `^##(?!#)` boundary `_find_section`
# ends a section on, read in the other direction: "which sections exist" instead of "where is this
# one" — so a section's extent here and there can never disagree.
_ANY_SECTION_HEADING_RE = re.compile(r"^##(?!#)\s*(.*?)\s*$")

# Checklists that are NOT a misplaced DoD, so `dod_summary` never reports them. `## Stories` is the
# legacy epic hierarchy checklist (`skills/_shared/epic-story-hierarchy.md`): an epic filed before
# the native sub-issue relation carries it, often with no DoD, and it has its own reader — flagging
# it would put a "rename your checklist" prompt on every legacy epic.
_NON_DOD_CHECKLIST_HEADING_RE = re.compile(r"^Stories\s*(?:\(.*\))?$", re.IGNORECASE)


def _count_top_level_checkboxes(lines, start, end):
    """Count `lines[start:end]`'s top-level checkbox bullets — the lines `parse_dod_bullets` would
    index — WITHOUT parsing annotations, so the count is known even when an annotation is malformed
    (`parse_dod_bullets` raises at the first one)."""
    count = 0
    for line in lines[start:end]:
        match = _DOD_BULLET_RE.match(line)
        if match and _TOP_LEVEL_INDENT_RE.match(match.group(1)):
            count += 1
    return count


# A CommonMark fence opener/closer: three or more backticks or tildes, up to three spaces of indent.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _blank_fenced_lines(lines):
    """`lines` with every fenced-code line (fence markers included) replaced by `""`. A fence closes
    on a marker of the same character at least as long as its opener; an unclosed fence runs to EOF."""
    out = []
    opener = None
    for line in lines:
        match = _FENCE_RE.match(line)
        if opener is None:
            if match:
                opener = match.group(1)
                out.append("")
            else:
                out.append(line)
            continue
        out.append("")
        if match and match.group(1)[0] == opener[0] and len(match.group(1)) >= len(opener):
            opener = None
    return out


def dod_summary(body_text):
    """The DoD's SHAPE, as a fact: ``{"present", "count", "other_checklists"}``. Never raises.

    - ``present`` — `has_dod_section`.
    - ``count`` — the DoD's top-level checkbox bullets, i.e. the 1-based index space every
      ``closes-dod`` value refers to (0 when the section is absent).
    - ``other_checklists`` — ``[{"heading", "count"}]`` for every OTHER `##` section holding at least
      one top-level checkbox bullet (`## Stories` excepted — `_NON_DOD_CHECKLIST_HEADING_RE`), in body order.

    Why ``other_checklists`` exists: the DoD heading is the contract (`dod-annotations.md`'s
    "Section finder"), and a body whose criteria sit under `## Acceptance criteria` parses as "no
    DoD". Before this fact, the planner indexed those bullets by eye, the plan reviewer accepted it,
    and the resolver — handed `closes-dod` indexes and `dod: []` — ticked the other heading's
    bullets, which no parser downstream reads. Naming the heading is what lets each stage say so.
    """
    lines = body_text.splitlines()
    dod_section = _find_section(lines, _DOD_HEADING_PATTERN)
    # The DoD count keeps `parse_dod_bullets`' exact reading (no fence awareness), so it is always the
    # index space `closes-dod` refers to.
    count = _count_top_level_checkboxes(lines, *dod_section) if dod_section else 0

    # Other checklists are read OUTSIDE fenced code only: an issue that quotes a markdown sample
    # (```` ```markdown ```` holding `## Acceptance criteria` + `- [ ] x`) must not report a phantom
    # checklist — the drafter's check would fail on it and the planner would offer to rename example
    # text. Fenced lines are blanked, so they are neither headings nor checkboxes here.
    visible = _blank_fenced_lines(lines)
    headings = []
    for index, line in enumerate(visible):
        match = _ANY_SECTION_HEADING_RE.match(line)
        if match:
            headings.append((index, match.group(1)))
    other_checklists = []
    for position, (heading_index, heading_text) in enumerate(headings):
        start = heading_index + 1
        if dod_section is not None and start == dod_section[0]:
            continue
        if _NON_DOD_CHECKLIST_HEADING_RE.match(heading_text):
            continue
        end = headings[position + 1][0] if position + 1 < len(headings) else len(visible)
        section_count = _count_top_level_checkboxes(visible, start, end)
        if section_count:
            other_checklists.append({"heading": heading_text, "count": section_count})

    return {"present": dod_section is not None, "count": count, "other_checklists": other_checklists}


def misplaced_dod_note(summary):
    """The one wording every prep uses for "this body's checklist is not under the DoD heading" —
    `None` unless the DoD is absent AND another checklist exists (`summary` is `dod_summary`'s).
    Each prep appends its own consequence; the fact itself is stated identically everywhere."""
    if summary["present"] or not summary["other_checklists"]:
        return None
    where = ", ".join(
        "`## %s` (%d bullet%s)" % (c["heading"], c["count"], "" if c["count"] == 1 else "s")
        for c in summary["other_checklists"]
    )
    return "issue body has no `## Definition of done` — its checklist sits under %s" % where


def dod_coverage(phases, summary):
    """How a plan's ``closes-dod`` claims line up with the issue's DoD (``summary`` is
    `dod_summary`'s). Returns ``None`` when the resolver's single-phase fallback applies — no phases,
    or one phase claiming ``(none)`` (`skills/resolver/references/dod-projection-rule.md`,
    "Single-phase fallback") — because no index is claimed there at all.

    Otherwise ``{"present", "dod_count", "other_checklists", "max_claimed", "out_of_range",
    "unclaimed", "claimed_more_than_once", "drift"}``:

    - ``out_of_range`` — claimed indexes outside ``1..dod_count`` (every claim, when the DoD is
      absent);
    - ``unclaimed`` — DoD indexes no phase claims (the plan reviewer's exact-coverage BLOCKER);
    - ``claimed_more_than_once`` — indexes two or more phases claim (the reviewer judges these);
    - ``drift`` — `dod-annotations.md`'s index-stability invariant exactly as written: an index is
      out of range, or the DoD's bullet count differs from the plan's highest claimed index. A gap
      below the maximum is ``unclaimed`` but NOT drift — that is a planning defect the reviewer owns,
      and widening the resolver's block to it would change a contract the planner's revise relies on.
      A multi-phase plan claiming NOTHING (every phase ``(none)``) against a DoD that has bullets IS
      drift, deliberately: its highest claimed index is 0, so no tick would ever land, and the plan
      breaks the exact-coverage rule — only the planner can fix it. Against an absent DoD (the
      DoD-heading card's "plan without a DoD" arm) both sides are 0 and nothing drifts.

    Always data, never a decision: the planner fixes findings in its own staged plan, the resolver
    re-routes on ``drift``.
    """
    if not phases or (len(phases) == 1 and phases[0]["closes_dod"] == "(none)"):
        return None
    dod_count = summary["count"]
    claims = {}
    for phase in phases:
        if phase["closes_dod"] == "(none)":
            continue
        for index in phase["closes_dod"]:
            claims.setdefault(index, []).append(phase["number"])
    claimed = sorted(claims)
    max_claimed = claimed[-1] if claimed else 0
    out_of_range = [i for i in claimed if i < 1 or i > dod_count]
    return {
        "present": summary["present"],
        "dod_count": dod_count,
        "other_checklists": summary["other_checklists"],
        "max_claimed": max_claimed,
        "out_of_range": out_of_range,
        "unclaimed": [i for i in range(1, dod_count + 1) if i not in claims],
        "claimed_more_than_once": [i for i in claimed if len(claims[i]) > 1],
        "drift": bool(out_of_range) or dod_count != max_claimed,
    }


def dod_malformed_decision(exc, **context):
    """Build the `DOD_MALFORMED` needs_decision payload for a `_DodMalformed` raised by
    `parse_dod_bullets` — the single home for the operator-facing repair guidance and the
    decision's context schema, which were previously restated per caller (`run_dod` here, plus
    the DoD-composing preps). `context` carries any caller-specific keys (e.g. `issue=`,
    `body_file=`) merged alongside the exception's own `line_number`/`raw_line`."""
    return needs_decision(
        DOD_MALFORMED,
        summary=exc.reason,
        context={
            "line_number": exc.line_number,
            "raw_line": exc.raw_line,
            **context,
        },
        options=[
            "fix the bullet's annotation by hand to match one of the closed-set forms in "
            "skills/_shared/dod-annotations.md, then re-run"
        ],
    )


def parse_dod_bullets(body_text):
    """Parse the `## Definition of done` section's top-level checkbox bullets. Returns a list of
    `{"index", "text", "checked", "annotation"}` dicts (1-based `index`, `annotation` a dict or
    `None`). Raises `_DodMalformed` on the first unrecognized or stacked annotation found — the
    caller turns that into a `DOD_MALFORMED` needs_decision envelope.

    No `## Definition of done` section: returns `[]` (dod-annotations.md's "Edge cases"). A caller
    that must tell "absent" from "empty", or see a checklist under another heading, reads
    `dod_summary`.
    """
    lines = body_text.splitlines()
    section = _find_section(lines, _DOD_HEADING_PATTERN)
    if section is None:
        return []
    start, end = section

    bullets = []
    index = 0
    for line_number in range(start, end):
        raw_line = lines[line_number]
        match = _DOD_BULLET_RE.match(raw_line)
        if not match:
            continue  # not a checkbox-bullet line at all (blank line, prose, etc.) — not our concern
        indent, check_char, text, prefix_word, tail = match.groups()
        if not _TOP_LEVEL_INDENT_RE.match(indent):
            continue  # a sub-bullet (indent >= 2) — detail, not a DoD item (dod-annotations.md)

        index += 1
        checked = check_char in ("x", "X")

        annotation = None
        if prefix_word is None:
            # `_DOD_BULLET_RE`'s prefix alternation didn't match anything, so the whole trailing
            # parenthetical (if any) was absorbed into `text`. Before accepting this as a bare,
            # unannotated bullet, check whether `text` nonetheless ends with an annotation
            # *lookalike* — an attribution attempt using the wrong prefix word (a near-miss, not
            # ordinary bullet prose that happens to end in unrelated parentheses).
            lookalike_match = _ANNOTATION_LOOKALIKE_RE.search(text)
            if lookalike_match:
                raise _DodMalformed(
                    "bullet %d has a trailing parenthetical that looks like an attribution "
                    "annotation attempt but does not use one of the closed-set prefix words "
                    "(closed by / resolver claimed phase / previously claimed by): %r"
                    % (index, lookalike_match.group(0)),
                    line_number + 1,
                    raw_line,
                )
        else:
            # A recognized prefix word matched; parse its own tail sub-shape. A stacked second
            # annotation folded into this tail (see the note above `_ANNOTATION_LOOKALIKE_RE`)
            # surfaces as either a fullmatch failure against every known field shape for this
            # prefix, or — for `resolver claimed phase`'s free-text reason field specifically — a
            # literal paren inside the reason (`_REASON_CONTAINS_PAREN_RE`, checked inside
            # `_parse_resolver_claimed_tail`). Both return `None` here, handled identically below.
            tail_parser = _PREFIX_TAIL_PARSERS[prefix_word]
            annotation = tail_parser(tail)
            if annotation is None:
                raise _DodMalformed(
                    "bullet %d's %r annotation does not match any recognized tail shape: %r"
                    % (index, prefix_word, tail),
                    line_number + 1,
                    raw_line,
                )

        bullets.append(
            {"index": index, "text": text, "checked": checked, "annotation": annotation}
        )
    return bullets


def render_dod_bullets(bullets, indent=""):
    """Re-emit `bullets` (as returned by `parse_dod_bullets`) as Markdown checkbox-bullet lines,
    joined by `\\n` with one trailing `\\n` — the exact inverse of `parse_dod_bullets` given the
    same leading `indent` the bullets were parsed at (an empty string for the always-top-level
    case `parse_dod_bullets` restricts itself to).

    This is the round-trip fidelity guarantee the `dod --render` DoD box exercises: parsing a
    body's DoD section and rendering the result must reproduce the **section's bullet lines**
    byte-for-byte (the test compares this against the original bullet-line text extracted from
    the same fixture, not the whole file — headings/blank-lines/prose around the bullets are not
    part of what `parse_dod_bullets` captures, so they are correctly not part of what this
    reproduces). A lossy parse (a field silently dropped or reformatted) fails this by
    construction, since the render path only ever has the parsed fields to work from — it never
    holds onto or falls back to the original raw line.
    """
    out = []
    for bullet in bullets:
        check_char = "x" if bullet["checked"] else " "
        line = "%s- [%s] %s" % (indent, check_char, bullet["text"])
        annotation = bullet["annotation"]
        if annotation is not None:
            renderer = _FORM_RENDERERS[annotation["form"]]
            line += " (%s)" % renderer(annotation)
        out.append(line)
    return "\n".join(out) + ("\n" if out else "")


def run_dod(args):
    body_text = _read_text_or_die(args.body_file)
    try:
        bullets = parse_dod_bullets(body_text)
    except _DodMalformed as exc:
        emit_needs_decision(dod_malformed_decision(exc, body_file=args.body_file))
        sys.exit(EXIT_OK)

    if args.render:
        emit_ok(payload={"rendered": render_dod_bullets(bullets)})
    else:
        summary = dod_summary(body_text)
        emit_ok(
            payload={
                "dod": bullets,
                "present": summary["present"],
                "other_checklists": summary["other_checklists"],
            }
        )
    sys.exit(EXIT_OK)


# ============================================================================================
# `oq-links` subcommand — skills/_shared/open-question-links.md
# ============================================================================================

_OQ_LINKS_MARKER = "<!-- open-question-links:v1 -->"

# The closed disposition set (open-question-links.md §Dispositions) — exactly three values, never
# extended ad hoc.
_OQ_DISPOSITIONS = frozenset({"scoped-out", "in-scope (blocked)", "provisional-default"})

# One OQ entry starts with a `- OQ: ...` line and may continue on subsequent lines that begin
# (after their own leading whitespace) with an em dash `—` — open-question-links.md's worked
# example: continuation lines are `  — disposition: ... — question: ...` etc., soft-wrapped
# beneath the `- OQ:` line they belong to. `—` is the literal em dash (not a hyphen) the
# shared contract itself uses as its field separator throughout the schema block.
_OQ_ENTRY_START_RE = re.compile(r"^- OQ: `([^`]+)` \(([^)]+)\) — gates: (.+)$")
_OQ_CONTINUATION_RE = re.compile(r"^\s+— ")

# Field-extraction regexes, applied to the *joined* (single-line) form of one entry's continuation
# text (see `_join_entry_lines`). Each is independent of the others' presence/absence/order in the
# source, since the schema's own field order is fixed by the template
# (disposition, question, audience, [default, retires-when]) but this parser does not assume a
# reader relies on that fixed order surviving a hand-edit.
# `(?=\s|$)` rather than `\b` for the field terminator: `\b` requires a word/non-word transition,
# which never occurs after a value ending in `)` (a non-word char) followed by a space (also
# non-word) — exactly the shape of `in-scope (blocked)` and `(not filed)`, both real closed-set
# values. A lookahead for "whitespace or end of string" is what "the field's value ends here" is
# actually supposed to mean.
_OQ_DISPOSITION_FIELD_RE = re.compile(
    r"— disposition: (scoped-out|in-scope \(blocked\)|provisional-default)(?=\s|$)"
)
_OQ_QUESTION_FIELD_RE = re.compile(r"— question: (#\d+|\(not filed\))(?=\s|$)")
_OQ_AUDIENCE_FIELD_RE = re.compile(r"— audience: ([^—]+?)(?:\s*—|\s*$)")
_OQ_DEFAULT_FIELD_RE = re.compile(r"— default: ([^—]+?)(?:\s*—|\s*$)")
_OQ_RETIRES_WHEN_FIELD_RE = re.compile(r"— retires-when: ([^—]+?)\s*$")


class _OqLinksMalformed(Exception):
    """Internal signal: an `## Open questions` entry doesn't conform to the closed-set schema
    (unrecognized disposition, a `provisional-default` entry missing its required `default:`/
    `retires-when:` fields, or a `- OQ:` line whose required leading fields don't parse). Distinct
    from `DOD_MALFORMED`/`PHASES_MALFORMED` — the shared contract does not name a dedicated
    decision code for this grammar (open-question-links.md predates architecture.md §3's code
    registry finalization; only `dod`/`phases` are named there), so this raises `AMBIGUOUS` (the
    §3 closed set's residual-ambiguity code), never invents an unregistered new code.
    """

    def __init__(self, reason, line_number, raw_line):
        super().__init__(reason)
        self.reason = reason
        self.line_number = line_number
        self.raw_line = raw_line


def _split_oq_entries(section_lines):
    """Group `section_lines` (already past the `<!-- open-question-links:v1 -->` marker line)
    into one raw-line-list per `- OQ:`-started entry, folding each entry's continuation lines in.
    Non-bullet lines (blank lines between entries, stray prose) are skipped rather than treated as
    a parse error — the marker's presence is what makes this section machine-readable at all; the
    entries within it are the only structured content.
    """
    entries = []
    current = None
    for raw_line in section_lines:
        if _OQ_ENTRY_START_RE.match(raw_line):
            if current is not None:
                entries.append(current)
            current = [raw_line]
        elif current is not None and _OQ_CONTINUATION_RE.match(raw_line):
            current.append(raw_line)
        elif raw_line.strip() == "":
            continue
        else:
            # A non-blank line that is neither a new entry nor a continuation of the current one —
            # if an entry is open, treat it as the end of that entry's continuation run (stray
            # prose after the block); the entry itself is still parsed from what was accumulated.
            if current is not None:
                entries.append(current)
                current = None
    if current is not None:
        entries.append(current)
    return entries


def _parse_one_oq_entry(entry_lines, entry_line_number):
    """Parse one entry's raw lines (the `- OQ:` line plus its continuation lines) into the
    `{"oq_id", "source", "gates", "disposition", "question", "audience", "default",
    "retires_when"}` dict. Raises `_OqLinksMalformed` when a required field is absent or a value
    falls outside its closed set.
    """
    head_match = _OQ_ENTRY_START_RE.match(entry_lines[0])
    if not head_match:  # pragma: no cover — caller only invokes this on lines that already matched
        raise _OqLinksMalformed(
            "entry does not start with the required '- OQ: `<id>` (<source>) — gates: ...' shape",
            entry_line_number,
            entry_lines[0],
        )
    oq_id, source, gates = head_match.groups()

    # The continuation lines carry the rest of the fields, soft-wrapped; joining them with a
    # single space lets the field regexes above match regardless of exactly which physical line a
    # field landed on (open-question-links.md's schema does not pin field-to-line assignment,
    # only field order within the logical entry).
    joined_tail = " ".join(line.strip() for line in entry_lines[1:])
    full_entry_text = entry_lines[0] + (" " + joined_tail if joined_tail else "")

    disposition_match = _OQ_DISPOSITION_FIELD_RE.search(full_entry_text)
    if not disposition_match:
        raise _OqLinksMalformed(
            "OQ `%s` entry is missing a recognized 'disposition:' field (must be one of %s)"
            % (oq_id, sorted(_OQ_DISPOSITIONS)),
            entry_line_number,
            entry_lines[0],
        )
    disposition = disposition_match.group(1)

    question_match = _OQ_QUESTION_FIELD_RE.search(full_entry_text)
    if not question_match:
        raise _OqLinksMalformed(
            "OQ `%s` entry is missing a recognized 'question:' field (must be '#<N>' or "
            "'(not filed)')" % oq_id,
            entry_line_number,
            entry_lines[0],
        )
    question = question_match.group(1)

    audience_match = _OQ_AUDIENCE_FIELD_RE.search(full_entry_text)
    audience = audience_match.group(1).strip() if audience_match else None
    if audience is None:
        raise _OqLinksMalformed(
            "OQ `%s` entry is missing the required 'audience:' field" % oq_id,
            entry_line_number,
            entry_lines[0],
        )

    default_match = _OQ_DEFAULT_FIELD_RE.search(full_entry_text)
    retires_when_match = _OQ_RETIRES_WHEN_FIELD_RE.search(full_entry_text)
    default_value = default_match.group(1).strip() if default_match else None
    retires_when_value = retires_when_match.group(1).strip() if retires_when_match else None

    if disposition == "provisional-default":
        # open-question-links.md §Dispositions: provisional-default "entry MUST carry `default:`
        # ... and `retires-when:`" — required, not optional, for exactly this one disposition.
        if default_value is None or retires_when_value is None:
            raise _OqLinksMalformed(
                "OQ `%s` has disposition 'provisional-default' but is missing 'default:' and/or "
                "'retires-when:' (both are required for this disposition)" % oq_id,
                entry_line_number,
                entry_lines[0],
            )
    else:
        # Present on a non-provisional-default entry would be a hand-edit error the shared
        # contract doesn't sanction (those two fields are documented as "[provisional-default
        # only]") — refuse rather than silently accept a field that shouldn't be there.
        if default_value is not None or retires_when_value is not None:
            raise _OqLinksMalformed(
                "OQ `%s` has disposition '%s' but carries 'default:'/'retires-when:' fields, "
                "which are provisional-default-only" % (oq_id, disposition),
                entry_line_number,
                entry_lines[0],
            )

    return {
        "oq_id": oq_id,
        "source": source,
        "gates": gates,
        "disposition": disposition,
        "question": question,
        "audience": audience,
        "default": default_value,
        "retires_when": retires_when_value,
    }


def parse_oq_links(body_text):
    """Parse the `## Open questions` section's `<!-- open-question-links:v1 -->` entries. Returns
    a list of per-entry dicts (see `_parse_one_oq_entry`). Raises `_OqLinksMalformed` on the first
    entry that doesn't conform.

    No `## Open questions` section, or a section present without the `<!-- open-question-links:v1
    -->` marker as its first line: returns `[]` — open-question-links.md's Format section: "Omit
    the whole section when no OQ gates the issue," and readers "locate the registry with a
    `startswith` match on that line," so a section lacking the marker is not this registry at all
    (it's not this parser's job to guess at an unmarked section's meaning).
    """
    lines = body_text.splitlines()
    section = _find_section(lines, r"Open questions")
    if section is None:
        return []
    start, end = section
    section_lines = lines[start:end]

    # Skip leading blank lines before checking for the marker — a body author may leave one blank
    # line between the heading and the marker; the marker still must be the section's first
    # non-blank content to count as "present" (mirrors the marker-comment convention used
    # elsewhere in this repo, where the marker is the first line of *content*, not necessarily the
    # literal first line of a comment that might carry leading whitespace).
    first_content_index = None
    for i, line in enumerate(section_lines):
        if line.strip() != "":
            first_content_index = i
            break
    if first_content_index is None or section_lines[first_content_index].strip() != _OQ_LINKS_MARKER:
        return []

    entries_lines = _split_oq_entries(section_lines[first_content_index + 1 :])
    results = []
    for entry_lines in entries_lines:
        entry_line_number = start + first_content_index + 1  # approximate, refined below
        # Recompute the precise 1-based source line number of this entry's head line for the
        # decision context (best-effort line attribution for the operator, not load-bearing for
        # correctness — the parsed fields are what matters).
        try:
            entry_line_number = lines.index(entry_lines[0], start) + 1
        except ValueError:  # pragma: no cover — entry_lines[0] always came from `lines` itself
            pass
        results.append(_parse_one_oq_entry(entry_lines, entry_line_number))
    return results


def run_oq_links(args):
    body_text = _read_text_or_die(args.body_file)
    try:
        entries = parse_oq_links(body_text)
    except _OqLinksMalformed as exc:
        from pipelib.decisions import AMBIGUOUS  # local import: keep the module-level import list

        # focused on the two decision codes this file canonically emits (DOD_MALFORMED,
        # PHASES_MALFORMED per architecture.md §3); AMBIGUOUS is pipelib's general residual code,
        # used here only on this one non-canonical-emitter path.
        decision = needs_decision(
            AMBIGUOUS,
            summary=exc.reason,
            context={
                "body_file": args.body_file,
                "line_number": exc.line_number,
                "raw_line": exc.raw_line,
            },
            options=[
                "fix the entry by hand to match the schema in "
                "skills/_shared/open-question-links.md, then re-run"
            ],
        )
        emit_needs_decision(decision)
        sys.exit(EXIT_OK)

    emit_ok(payload={"open_questions": entries})
    sys.exit(EXIT_OK)


# ============================================================================================
# `phases` subcommand — skills/planner/references/plan-schema.md `## Phases`
# ============================================================================================

# The closed `kind:` set (plan-schema.md: "kind: code-shipping | operator | decision-only").
_PHASE_KINDS = frozenset({"code-shipping", "operator", "decision-only"})

# `N. **Phase N — <title>**` — plan-schema.md's worked template line 1 of each numbered entry. The
# two `N`s (the list ordinal and the "Phase N" label) are captured separately so a mismatch
# between them (a renumbering slip) is a detectable malformation rather than silently trusting
# whichever one happens to be read.
_PHASE_HEAD_RE = re.compile(r"^(\d+)\.\s+\*\*Phase (\d+)\s*—\s*(.+?)\*\*\s*$")

# Structured key lines beneath a phase head, each `   - key: value` (plan-schema.md's template
# indents these one level under the numbered head). `re.DOTALL` is not used — value is always the
# rest of the line.
_PHASE_KEY_RE = re.compile(
    r"^\s*-\s+(kind|ships|closes-dod|deliverable|depends-on|sub-issue|checkpoint):\s*(.*)$"
)

# `sub-issue` and `checkpoint` are deliberately absent: each is recognized-but-**not-required**,
# which is the whole backward-compatibility mechanism. This grammar is bidirectionally closed (an
# unrecognized line raises, and so does a missing required key), so making either key required
# would turn every plan authored before it existed into `PHASES_MALFORMED` — and the planner's revise mode, the one path
# that could repair such a plan, parses exactly those bodies.
_REQUIRED_PHASE_KEYS = frozenset({"kind", "ships", "closes-dod", "deliverable", "depends-on"})

# `closes-dod`/`depends-on` values: either the literal `(none)` or a comma-separated reference
# list. `closes-dod` references are bare 1-based ints (dod-annotations.md's indexing: "The first
# top-level bullet is index 1"); `depends-on` references are bare ints (earlier phase numbers,
# plan-schema.md: "depends-on: <earlier phase numbers, or `(none)` for the head phase>").
_REF_LIST_ITEM_RE = re.compile(r"^\d+$")

# `sub-issue:` values: either the literal `(none)` or exactly ONE `#<N>` issue reference. The `#`
# is required because everywhere else in plan-schema.md an *issue* is written `#<N>` while a bare
# int means a DoD index or a phase number — one spelling per meaning keeps `sub-issue: 3` from
# reading as "phase 3". Single-valued by contract: the phase→sub-issue map is N:1 (several phases
# may serve one sub-issue; a phase serves at most one), so a comma-separated list is malformed
# rather than a shorthand to be silently narrowed.
_PHASE_SUB_ISSUE_RE = re.compile(r"^#(\d+)$")

# `checkpoint:` values (plan-schema.md, "The `checkpoint:` phase key"): whether the resolver asks the
# operator before continuing to the next phase in the same session. An absent line is `None`, which
# the resolver reads as `pause` — the behaviour every plan authored before the key had.
_PHASE_CHECKPOINT_VALUES = frozenset({"pause", "continue"})


class _PhasesMalformed(Exception):
    """Internal signal: the `## Phases` section is present but does not parse — a phase entry
    missing a required key, a `kind:` value outside the closed set, non-sequential/duplicate phase
    numbers, a `closes-dod`/`depends-on` value that is neither `(none)` nor a well-formed
    reference list, a `sub-issue:` value that is neither `(none)` nor a single `#<N>`, a
    `checkpoint:` value that is neither `pause` nor `continue`, or a numbered head whose ordinal
    and "Phase N" label disagree. Never escapes this module.
    """

    def __init__(self, reason, line_number, raw_line):
        super().__init__(reason)
        self.reason = reason
        self.line_number = line_number
        self.raw_line = raw_line


def _parse_ref_list_field(value, field_name, phase_number, line_number, raw_line):
    """Parse a `closes-dod`/`depends-on` field value: either the literal `(none)` (returned
    verbatim as the string, per this module's documented `"(none)"`-or-list-of-ints payload
    shape) or a comma-separated list of bare non-negative ints. Raises `_PhasesMalformed` on
    anything else (empty value, non-numeric item, stray punctuation).
    """
    value = value.strip()
    if value == "(none)":
        return "(none)"
    if value == "":
        raise _PhasesMalformed(
            "phase %d's '%s:' field is empty (must be '(none)' or a comma-separated list of "
            "numbers)" % (phase_number, field_name),
            line_number,
            raw_line,
        )
    items = [item.strip() for item in value.split(",")]
    parsed = []
    for item in items:
        if not _REF_LIST_ITEM_RE.match(item):
            raise _PhasesMalformed(
                "phase %d's '%s:' field has a non-numeric reference %r (must be '(none)' or a "
                "comma-separated list of numbers)" % (phase_number, field_name, item),
                line_number,
                raw_line,
            )
        parsed.append(int(item))
    return parsed


def _parse_sub_issue_field(value, phase_number, line_number, raw_line):
    """Parse a `sub-issue:` field value: either the literal `(none)` (returned verbatim as the
    string, matching `_parse_ref_list_field`'s payload convention — an explicit *substrate* claim,
    groundwork no single sub-issue can demonstrate) or a single `#<N>` reference returned as an
    int. Raises `_PhasesMalformed` on an empty value, a bare int, a comma-separated list, `#0`, or
    any other shape.
    """
    value = value.strip()
    if value == "(none)":
        return "(none)"
    match = _PHASE_SUB_ISSUE_RE.match(value)
    if match is None:
        raise _PhasesMalformed(
            "phase %d's 'sub-issue:' field is %r (must be '(none)' or exactly one '#<N>' "
            "sub-issue reference — the phase-to-sub-issue map is N:1, so a list is not legal)"
            % (phase_number, value),
            line_number,
            raw_line,
        )
    number = int(match.group(1))
    if number < 1:
        raise _PhasesMalformed(
            "phase %d's 'sub-issue:' field references '#%d' (issue numbers start at 1)"
            % (phase_number, number),
            line_number,
            raw_line,
        )
    return number


def _parse_checkpoint_field(value, phase_number, line_number, raw_line):
    """Parse a `checkpoint:` field value: exactly `pause` or `continue`, returned verbatim. Raises
    `_PhasesMalformed` on an empty value or any other word — a typo must not read as either answer,
    and `continue` on a phase the operator meant to review is the costly misreading.
    """
    value = value.strip()
    if value not in _PHASE_CHECKPOINT_VALUES:
        raise _PhasesMalformed(
            "phase %d's 'checkpoint:' field is %r (must be 'pause' or 'continue')"
            % (phase_number, value),
            line_number,
            raw_line,
        )
    return value


def parse_phases(body_text):
    """Parse the `## Phases` section into a list of `{"number", "title", "kind", "ships",
    "closes_dod", "deliverable", "depends_on", "sub_issue", "checkpoint"}` dicts, in the order they appear in
    the section (plan-schema.md's numbered list is itself the intended sequencing, mirrored here as
    list order — no independent sort is applied). Raises `_PhasesMalformed` on any parse failure.

    `sub_issue` is tri-state, and the three values are distinguishable on purpose — the planner's
    plan-versus-live diff and reviewer Dimension 7 both read them:

      * `None`     — the `sub-issue:` line is absent: **unmapped** (a plan authored before the key
                     existed, or a mapping never made). An absent key is NOT `(none)`.
      * `"(none)"` — `sub-issue: (none)`: **substrate**, explicitly claimed.
      * `int`      — `sub-issue: #214`: this phase serves that sub-issue.

    `checkpoint` is `None` when the `checkpoint:` line is absent (read as `pause` by the resolver),
    else `"pause"` or `"continue"`.

    No `## Phases` section: returns `[]` — this is a legitimate shape (single-phase issues and
    epics omit it entirely, plan-schema.md), never `PHASES_MALFORMED` on its own; only a
    **present-but-broken** section raises.
    """
    lines = body_text.splitlines()
    section = _find_section(lines, r"Phases")
    if section is None:
        return []
    start, end = section

    # Group each phase head line with the structured-key lines that follow it, up to the next
    # phase head or the section end.
    head_indices = [i for i in range(start, end) if _PHASE_HEAD_RE.match(lines[i])]
    if not head_indices:
        # A `## Phases` heading with no parseable numbered entry beneath it at all is itself
        # malformed — an empty section would have been omitted entirely per the schema's own
        # "omit for single-phase" rule, so a *present* heading implies at least one entry is
        # expected.
        raise _PhasesMalformed(
            "'## Phases' section is present but contains no parseable numbered phase entry",
            start + 1,
            lines[start] if start < len(lines) else "",
        )

    phases = []
    seen_numbers = []
    for idx, head_index in enumerate(head_indices):
        head_match = _PHASE_HEAD_RE.match(lines[head_index])
        ordinal, label_number, title = head_match.groups()
        if ordinal != label_number:
            raise _PhasesMalformed(
                "phase entry's list ordinal (%s) does not match its 'Phase %s' label"
                % (ordinal, label_number),
                head_index + 1,
                lines[head_index],
            )
        phase_number = int(ordinal)

        body_end = head_indices[idx + 1] if idx + 1 < len(head_indices) else end
        found_keys = {}
        for line_index in range(head_index + 1, body_end):
            raw_line = lines[line_index]
            if raw_line.strip() == "":
                continue
            key_match = _PHASE_KEY_RE.match(raw_line)
            if not key_match:
                raise _PhasesMalformed(
                    "phase %d has an unrecognized line where a structured '- key: value' entry "
                    "was expected: %r" % (phase_number, raw_line),
                    line_index + 1,
                    raw_line,
                )
            key, value = key_match.groups()
            if key in found_keys:
                raise _PhasesMalformed(
                    "phase %d's '%s:' key is given more than once" % (phase_number, key),
                    line_index + 1,
                    raw_line,
                )
            found_keys[key] = (value.strip(), line_index + 1, raw_line)

        missing = _REQUIRED_PHASE_KEYS - found_keys.keys()
        if missing:
            raise _PhasesMalformed(
                "phase %d is missing required key(s): %s" % (phase_number, ", ".join(sorted(missing))),
                head_index + 1,
                lines[head_index],
            )

        kind_value, kind_line, kind_raw = found_keys["kind"]
        if kind_value not in _PHASE_KINDS:
            raise _PhasesMalformed(
                "phase %d's 'kind:' value %r is not one of the closed set %s"
                % (phase_number, kind_value, sorted(_PHASE_KINDS)),
                kind_line,
                kind_raw,
            )

        ships_value, _, _ = found_keys["ships"]
        if ships_value == "":
            raise _PhasesMalformed(
                "phase %d's 'ships:' field is empty" % phase_number,
                found_keys["ships"][1],
                found_keys["ships"][2],
            )

        deliverable_value, _, _ = found_keys["deliverable"]
        if deliverable_value == "":
            raise _PhasesMalformed(
                "phase %d's 'deliverable:' field is empty" % phase_number,
                found_keys["deliverable"][1],
                found_keys["deliverable"][2],
            )

        closes_dod_raw, closes_dod_line, closes_dod_line_text = found_keys["closes-dod"]
        closes_dod = _parse_ref_list_field(
            closes_dod_raw, "closes-dod", phase_number, closes_dod_line, closes_dod_line_text
        )

        depends_on_raw, depends_on_line, depends_on_line_text = found_keys["depends-on"]
        depends_on = _parse_ref_list_field(
            depends_on_raw, "depends-on", phase_number, depends_on_line, depends_on_line_text
        )

        sub_issue = None
        if "sub-issue" in found_keys:
            sub_issue_raw, sub_issue_line, sub_issue_line_text = found_keys["sub-issue"]
            sub_issue = _parse_sub_issue_field(
                sub_issue_raw, phase_number, sub_issue_line, sub_issue_line_text
            )

        checkpoint = None
        if "checkpoint" in found_keys:
            checkpoint_raw, checkpoint_line, checkpoint_line_text = found_keys["checkpoint"]
            checkpoint = _parse_checkpoint_field(
                checkpoint_raw, phase_number, checkpoint_line, checkpoint_line_text
            )

        if phase_number in seen_numbers:
            raise _PhasesMalformed(
                "phase number %d is used more than once" % phase_number,
                head_index + 1,
                lines[head_index],
            )
        if seen_numbers and phase_number != seen_numbers[-1] + 1:
            raise _PhasesMalformed(
                "phase numbers are not sequential: phase %d follows phase %d"
                % (phase_number, seen_numbers[-1]),
                head_index + 1,
                lines[head_index],
            )
        seen_numbers.append(phase_number)

        phases.append(
            {
                "number": phase_number,
                "title": title.strip(),
                "kind": kind_value,
                "ships": ships_value,
                "closes_dod": closes_dod,
                "deliverable": deliverable_value,
                "depends_on": depends_on,
                # Always present, `None` when the line is absent, so the dict's key set stays
                # stable across the pre- and post-contract grammars.
                "sub_issue": sub_issue,
                # Same always-present convention: `None` (absent — the resolver pauses) or the
                # literal `"pause"` / `"continue"`.
                "checkpoint": checkpoint,
            }
        )

    return phases


# ============================================================================================
# `## Review level` + planned paths — import-only readers for scripts/blast_radius.py (#97)
# ============================================================================================
#
# The plan's `## Review level` section (skills/planner/references/plan-schema.md "The `## Review
# level` section") carries one line per phase: `- phase <N> · <medium|high|xhigh> — <why>`. Never a
# decision: a line that does not parse, or a phase named twice, is a finding the resolver reads as
# `high` (stricter) and the planner fixes like `dod_coverage` — the safe value is known, so a decision
# code would be a contract change for nothing.

REVIEW_LEVELS = ("medium", "high", "xhigh")
_REVIEW_LEVEL_LINE_RE = re.compile(r"^-\s+phase\s+(\d+)\s+·\s+(medium|high|xhigh)\s+—\s+(\S.*)$")
_REVIEW_LEVEL_ITEM_RE = re.compile(r"^([-*+]|\d+[.)])\s")
_REVIEW_LEVEL_PHASE_RE = re.compile(r"\bphase\s+(\d+)\b", re.IGNORECASE)
# A whitespace-free backticked span — a literal path or a `dir/` prefix in a plan's file lists.
_PLANNED_SPAN_RE = re.compile(r"`([^`\s]+)`")
# A span carrying a glob metacharacter names nothing (operator decision 2026-10-25, "Literal paths
# only"): `*.py` or `*` would cover whole extensions or the tree and silence the out-of-plan raise.
_PLANNED_GLOB_CHARS = "*?["
# A trailing `:<line>` / `:<line>-<line>` — the plan's `path:NN` citation form names the file itself.
_PLANNED_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")


def parse_review_levels(plan_text):
    """The plan's `## Review level` lines → `{"present", "levels": {<int>: {"level", "why"}},
    "unparsed": [line], "duplicated": [<int>]}`. A list item that fails the grammar (a `low` or `max`
    level included) is `unparsed`, and a phase it names gets no level even beside a valid line; both lines of a phase named twice are dropped from `levels` and the
    phase is `duplicated`. Never raises."""
    lines = (plan_text or "").splitlines()
    section = _find_section(lines, r"Review level")
    result = {"present": section is not None, "levels": {}, "unparsed": [], "duplicated": []}
    if section is None:
        return result
    seen, spoiled = {}, set()
    for raw in _blank_fenced_lines(lines[section[0]:section[1]]):
        line = raw.strip()
        found = _REVIEW_LEVEL_LINE_RE.match(line)
        if found:
            seen.setdefault(int(found.group(1)), []).append(
                {"level": found.group(2), "why": found.group(3).strip()}
            )
        elif _REVIEW_LEVEL_ITEM_RE.match(line):
            result["unparsed"].append(line)
            named = _REVIEW_LEVEL_PHASE_RE.search(line)
            if named:
                # An unreadable line naming a phase spoils that phase's valid line too: the stricter
                # default, never the laxer of two lines the planner disagreed between.
                spoiled.add(int(named.group(1)))
    for number in sorted(seen):
        if len(seen[number]) > 1:
            result["duplicated"].append(number)
        elif number not in spoiled:
            result["levels"][number] = seen[number][0]
    return result


def planned_paths(plan_text):
    """The whitespace-free backticked spans under `## Changes (file-level)` and `## Test plan`, in
    order, de-duplicated, a trailing `:<line>[-<line>]` citation suffix dropped — the paths the plan
    names, matched by blast_radius.py with the sensitive-path declaration's grammar (exact, or a `dir/`
    subtree). A span containing `*`, `?` or `[` is not a planned path. Never raises."""
    lines = (plan_text or "").splitlines()
    spans = []
    for heading in (r"Changes", r"Test plan"):
        section = _find_section(lines, heading)
        if section is None:
            continue
        for line in lines[section[0]:section[1]]:
            for span in _PLANNED_SPAN_RE.findall(line):
                span = _PLANNED_LINE_SUFFIX_RE.sub("", span)
                if span and span not in spans and not any(c in span for c in _PLANNED_GLOB_CHARS):
                    spans.append(span)
    return spans


def review_level_phases(phases):
    """The phases a `## Review level` section must cover: the `## Phases` numbers (`parse_phases`
    output), or `[1]` for a plan with none — one line for a single-phase plan."""
    return [phase["number"] for phase in phases] or [1]


def review_level_coverage(phases, levels):
    """`{present, missing, extra, unparsed, duplicated}` for `parse_review_levels`' result against the
    plan's phases (`parse_phases` output), over `review_level_phases`. Informational, like `size` —
    never a decision."""
    expected = review_level_phases(phases)
    named = set(levels["levels"]) | set(levels["duplicated"])
    return {
        "present": levels["present"],
        "missing": [number for number in expected if number not in named],
        "extra": sorted(number for number in named if number not in expected),
        "unparsed": list(levels["unparsed"]),
        "duplicated": list(levels["duplicated"]),
    }


def run_phases(args):
    body_text = _read_text_or_die(args.body_file)
    try:
        phases = parse_phases(body_text)
    except _PhasesMalformed as exc:
        decision = needs_decision(
            PHASES_MALFORMED,
            summary=exc.reason,
            context={
                "body_file": args.body_file,
                "line_number": exc.line_number,
                "raw_line": exc.raw_line,
            },
            options=[
                "fix the '## Phases' section by hand to match the structured-key grammar in "
                "skills/planner/references/plan-schema.md, then re-run"
            ],
        )
        emit_needs_decision(decision)
        sys.exit(EXIT_OK)

    payload = {
        "phases": phases,
        "size": body_size(body_text),
        "review_levels": review_level_coverage(phases, parse_review_levels(body_text)),
    }
    if args.issue_body is not None:
        payload["dod_coverage"] = dod_coverage(
            phases, dod_summary(_read_text_or_die(args.issue_body))
        )
    emit_ok(payload=payload)
    sys.exit(EXIT_OK)


# ============================================================================================
# `## Phase tracker` parsing (PR body) — import-only, NO subcommand and NO decision code
# ============================================================================================
#
# The tracker is a *rendering the resolver rebuilds* on every push, not a contract another skill
# authors: the plan's `## Phases` owns which rows exist, the tracker owns which are ticked. So an
# unparseable row is a row to rewrite, never a run to fail — there is deliberately no
# `PHASES_MALFORMED` analogue here and no CLI subcommand (nothing in a prompt invokes it; both
# consumers are preps). Do not "unify" this with `parse_phases`: the asymmetry is the design.
#
# Two preps need it — `prep_planner` (revise mode: what shipped, for SOFT/HARD classification) and
# `prep_resolver` (continue mode: the row set to reconcile against the plan) — which is why it lives
# here rather than in either one.

# `## Phase tracker` bullet grammar (docs/specs/examples/phase-tracker.md, the frozen worked
# instance): `- [x] Phase N — <title> (commit <sha>)` / `- [ ] Phase N — <title>` (unshipped) /
# `- [x] Phase N — <title> (operator action <ISO-date>)` for an operator/decision-only tick. `\d+`
# only, matching `_PHASE_HEAD_RE`'s integer-only phase labels — a `Phase 5c` row does not parse, by
# construction.
#
# The trailing annotation is stripped from `title` and captured separately, and the alternation is
# restricted to the CLOSED set (`commit …` / `operator …`) on purpose: a greedy "any parenthetical"
# rule would eat a title that legitimately ends in one ("the flag (behind a feature gate)"), while
# not stripping at all leaves an operator row's date inside `title` — where it compares unequal to
# the plan's title on every single run and reads as drift that is not there.
# The separator is what distinguishes the two row kinds, so it is matched precisely: an em dash (with
# optional spaces) or a SPACED hyphen introduces a main row's title, while a bare hyphen bound tight to
# a word makes a sub-row label.
_PHASE_TRACKER_ANNOTATION = (
    r"(?:\s*\((?:commit\s+([0-9a-f]{7,40})|(operator[^()]*))\))?$"
)
_PHASE_TRACKER_ROW_RE = re.compile(
    r"^-\s*\[( |x|X)\]\s*Phase\s+(\d+)(?:\s*—\s*|\s+-\s+)(.+?)" + _PHASE_TRACKER_ANNOTATION
)

# `- [x] Phase <N>-<label> (…)` — a **sub-row**, bound to phase `<N>` rather than being a second row
# *of* it (`skills/resolver/references/dod-projection-rule.md` Example C:
# `- [x] Phase 2-measurement (operator phase 2, applied 2026-06-04)`, alonga `Phase 2 — harness` row of
# its own). `Phase 2-measurement` means "the measurement sub-phase attached to phase 2", NOT "phase 2,
# titled measurement" — reading it as the latter makes one plan phase look like two competing tracker
# rows, which is a tick state nothing can reconcile. The plan's `## Phases` has no counterpart for the
# label (integers only — `plan-schema.md`, "Phase numbering"), so a sub-row is carried through
# reconciliation untouched rather than matched against a phase title.
_PHASE_TRACKER_SUBROW_RE = re.compile(
    r"^-\s*\[( |x|X)\]\s*Phase\s+(\d+)-(\S[^(]*?)" + _PHASE_TRACKER_ANNOTATION
)

# A line that *presents* as a checklist row. Used to tell "this row did not parse" (evidence that the
# tracker's tick state is unknown) from "this line is prose inside the section" (harmless).
_PHASE_TRACKER_ROWLIKE_RE = re.compile(r"^-\s*\[")


def scan_phase_tracker(pr_body_text):
    """Scan a PR body's `## Phase tracker` section. Returns
    `{"present", "rows", "unparsed"}`:

      - `present` — the **section exists**, independent of whether any row parsed. A caller must not
        infer this from `rows`: a section whose rows are all malformed (a `Phase 5c` row from a
        pre-#47 plan, a hand-edit) yields rows `[]` while the section, and its unknown tick state,
        very much exist.
      - `rows` — `{"checked", "phase", "title", "sub_label", "commit_sha", "annotation"}` dicts in
        source order. `sub_label` is `None` on a main row and the label on a `Phase <N>-<label>`
        sub-row, which is bound to phase `<N>` rather than being a second row of it.
        `commit_sha` is set only for the `(commit <sha>)` form; `annotation` carries an
        `operator …` annotation verbatim so a rebuild can preserve it (it is the only record that an
        operator phase landed — `skills/resolver/references/dod-projection-rule.md`).
      - `unparsed` — row-LIKE lines that did not parse, verbatim. Non-empty means the tick state
        cannot be trusted; prose lines in the section are not collected.

    Never raises: the tracker is a rendering the resolver rebuilds, so a bad row is a row to rewrite.
    """
    lines = (pr_body_text or "").splitlines()
    found = _find_section(lines, r"Phase tracker")
    if found is None:
        return {"present": False, "rows": [], "unparsed": []}
    start, end = found

    rows, unparsed = [], []
    for raw_line in lines[start:end]:
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = _PHASE_TRACKER_ROW_RE.match(stripped)
        sub_match = None if match else _PHASE_TRACKER_SUBROW_RE.match(stripped)
        if match or sub_match:
            hit = match or sub_match
            rows.append(
                {
                    "checked": hit.group(1) in ("x", "X"),
                    "phase": int(hit.group(2)),
                    "title": hit.group(3).strip(),
                    # None on a main row; the label on a sub-row bound to `phase`.
                    "sub_label": None if match else hit.group(3).strip(),
                    "commit_sha": hit.group(4),
                    "annotation": hit.group(5),
                }
            )
        elif _PHASE_TRACKER_ROWLIKE_RE.match(stripped):
            unparsed.append(stripped)
    return {"present": True, "rows": rows, "unparsed": unparsed}


def parse_phase_tracker(pr_body_text):
    """The rows alone — `prep_planner`'s revise-mode view, which needs what shipped and nothing
    about section presence. `scan_phase_tracker` is the full surface.
    """
    return scan_phase_tracker(pr_body_text)["rows"]


def normalize_tracker_title(title):
    """Fold a tracker row / plan phase title for comparison: whitespace-collapsed and case-folded.
    A retitle that is only a re-wrap or a capitalization change is not drift, so the tracker diff
    must not report it — see `prep_resolver.build_tracker_diff`.
    """
    return " ".join((title or "").split()).casefold()


# ============================================================================================
# `## Polish` ledger parsing (PR body) — import-only, NO subcommand and NO decision code
# ============================================================================================
#
# The ledger (skills/_shared/polish-ledger.md) is, like the phase tracker, a rendering the pipeline
# itself rebuilds: the resolver appends `open` / `applied` entries at each phase's push and the
# evaluator rewrites dispositions. An unparseable line is a line to rewrite, never a run to fail —
# so this follows `scan_phase_tracker`'s shape (never raises, `unparsed` collects row-like lines)
# rather than `parse_oq_links`'s decision-raising one. Three preps read it: `prep_resolver`
# (continue mode — evaluator-directed `apply` items and the revision-run reasons), `prep_evaluator`
# (the adjudication input) and `prep_planner` (revise mode — the `apply` items a re-plan absorbs).

# The closed disposition set (polish-ledger.md "Dispositions"). `applied` is the only form that
# carries a parenthetical, `(commit <sha>)`, the resolver's record of the fix.
POLISH_DISPOSITIONS = frozenset({"open", "apply", "file", "drop", "applied"})

# `- P<phase>.<seq> · <disposition> — <item> — <anchor> [— <note>]`. The middle dot and the em
# dashes are the field separators; `<item>` must not itself contain ` — ` (the contract says so), so
# a split on that separator is exact. `<disposition>` is matched as a word and validated against
# the closed set afterwards, so an off-vocabulary word lands in `unparsed` rather than silently
# widening the set.
_POLISH_ROW_RE = re.compile(
    r"^-\s*P(\d+)\.(\d+)\s*·\s*([a-z]+)(?:\s*\(commit\s+([0-9a-f]{7,40})\))?\s*—\s*(.+)$"
)
_POLISH_ROWLIKE_RE = re.compile(r"^-\s*P\d")
_POLISH_FIELD_SEP = " — "


def scan_polish(pr_body_text):
    """Scan a PR body's `## Polish` ledger. Returns `{"present", "entries", "unparsed"}`:

      - `present` — the section exists (independent of whether any entry parsed).
      - `entries` — `{"id", "phase", "seq", "disposition", "commit_sha", "item", "anchor", "note"}`
        dicts in source order. `id` is `P<phase>.<seq>`, the stable handle both skills cite.
        `commit_sha` is set only on `applied (commit <sha>)`; `note` is `None` when absent.
      - `unparsed` — ledger-like lines (`- P<digit>…`) that did not parse, verbatim: a missing
        anchor field, an off-vocabulary disposition, a `(commit …)` on anything but `applied`.

    Never raises.
    """
    lines = (pr_body_text or "").splitlines()
    found = _find_section(lines, r"Polish")
    if found is None:
        return absent_polish()
    start, end = found

    entries, unparsed = [], []
    for raw_line in lines[start:end]:
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = _POLISH_ROW_RE.match(stripped)
        fields = match.group(5).split(_POLISH_FIELD_SEP) if match else []
        disposition = match.group(3) if match else None
        commit_sha = match.group(4) if match else None
        valid = (
            match is not None
            and disposition in POLISH_DISPOSITIONS
            and (commit_sha is None or disposition == "applied")
            and len(fields) >= 2
            and all(part.strip() for part in fields[:2])
        )
        if valid:
            note = _POLISH_FIELD_SEP.join(fields[2:]).strip() or None
            entries.append(
                {
                    "id": "P%s.%s" % (match.group(1), match.group(2)),
                    "phase": int(match.group(1)),
                    "seq": int(match.group(2)),
                    "disposition": disposition,
                    "commit_sha": commit_sha,
                    "item": fields[0].strip(),
                    "anchor": fields[1].strip(),
                    "note": note,
                }
            )
        elif _POLISH_ROWLIKE_RE.match(stripped):
            unparsed.append(stripped)
    return {"present": True, "entries": entries, "unparsed": unparsed}


def absent_polish():
    """The polish fact when there is no PR body to scan. A factory (fresh dicts per call) for the
    same aliasing reason as `prep_resolver._absent_tracker`."""
    return {"present": False, "entries": [], "unparsed": []}


# ============================================================================================
# `## Security review` record (PR body) — import-only scanner, NO decision code; plus the
# `security-coverage` subcommand (an always-`ok` post-staging check)
# ============================================================================================
#
# skills/_shared/security-review.md: the security pass's record, one `Reviewed:` line (the full HEAD
# SHA the latest pass was dispatched at) plus one line per finding. Like the polish ledger it is a
# rendering the resolver and evaluator rewrite, so it never raises: a `SEC-`-led line that does not
# parse lands in `unparsed`. Read by `prep_evaluator` (the S4.5 gate and its `covers_head` skip),
# `prep_resolver` (finalisation's security pass) and `review_report` (the per-repo tally).
#
# A consuming repo's own PR template can carry a section under the same heading (a checklist headed
# `## Security review (…)`, say). The record is therefore the FIRST section under the tolerant heading
# that has the record's shape (`_security_section_is_record`: a `- Reviewed:` line or a `SEC-`-led line);
# every other such heading is the repo's and is reported in `other_sections`, never read or rewritten.
# `_find_section` is deliberately not narrowed for this: its parenthetical tolerance is what
# `planned_paths` and the other section readers rely on.

# The closed disposition set (security-review.md "Dispositions"). `fixed` always carries
# `(commit <sha>)`; `dismissed` is the operator's answer, so its note must lead `operator:`.
SECURITY_DISPOSITIONS = ("open", "fixed", "dismissed")

# `- SEC-<n> · <disposition> — <claim> — <anchor> [— <note>]`, split on ` — ` as `## Polish` is.
_SECURITY_ROW_RE = re.compile(
    r"^-\s*SEC-(\d+)\s*·\s*([a-z]+)(?:\s*\(commit\s+([0-9a-f]{7,40})\))?\s*—\s*(.+)$"
)
_SECURITY_ROWLIKE_RE = re.compile(r"^(?:[-*+]|\d+[.)])?\s*SEC-", re.IGNORECASE)
_SECURITY_REVIEWED_RE = re.compile(r"^-\s*Reviewed:(.*)$")
_SECURITY_FIELD_SEP = " — "
_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _security_section_is_record(section_lines):
    """Whether a `## Security review` section has the record's shape: any stripped line is a
    `- Reviewed:` line or a `SEC-`-led line. The one test the scanner (which section is the record) and
    `review_report`'s tally (record vs `other_sections`) share, so the two can never drift."""
    return any(
        _SECURITY_REVIEWED_RE.match(line.strip()) or _SECURITY_ROWLIKE_RE.match(line.strip())
        for line in section_lines
    )


def _security_sections(lines):
    """Every `## Security review` section, as `(heading_index, body_lines)` in source order: the same
    tolerant heading `_find_section` matches (case-insensitive, an optional trailing parenthetical),
    each section running to the next `##` heading or EOF. Fence-blind, as `_find_section` and every
    other section reader are."""
    heading_re = re.compile(r"^##\s+Security review\s*(?:\(.*\))?\s*$", re.IGNORECASE)
    next_heading_re = re.compile(r"^##(?!#)")
    sections = []
    for i, line in enumerate(lines):
        if not heading_re.match(line):
            continue
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if next_heading_re.match(lines[j]):
                end = j
                break
        sections.append((i, lines[i + 1:end]))
    return sections


def scan_security(pr_body_text):
    """Scan a PR body's `## Security review` record. Returns
    `{"present", "reviewed_sha", "entries", "unparsed", "record", "other_sections"}`:

      - `present` — the record exists: some section under the tolerant `Security review` heading has
        the record's shape (`_security_section_is_record`). The first such section is the record.
      - `record` — `{"heading", "line"}` (the verbatim heading line, its 1-based line number) for the
        record, `None` exactly when `present` is false. The writer rewrites only this section.
      - `other_sections` — `[{"heading", "line"}]`, every other section under that heading, in source
        order: a consuming repo's own section (a template checklist), or a second record-shaped section
        only a hand edit can make. Never read and never rewritten.
      - `reviewed_sha` — the last `Reviewed:` value when it is exactly 40 lowercase hex characters,
        else `None` (a short or malformed SHA never claims a head was reviewed).
      - `entries` — `{"id", "seq", "disposition", "commit_sha", "claim", "anchor", "note"}` dicts in
        source order. `commit_sha` is set only on `fixed`; `note` is `None` when absent.
      - `unparsed` — finding lines led by `SEC-` (any case, any or no bullet) that did not parse,
        verbatim: an off-vocabulary disposition, a missing anchor, a `fixed` without `(commit …)` or a
        `(commit …)` on anything else, a `dismissed` whose note does not lead `operator:` — and every
        line after the first that carries an already-used id, so a `dismissed` or `fixed` duplicate
        never shadows an `open` one for a reader that keys entries by id.

    Never raises.
    """
    lines = (pr_body_text or "").splitlines()
    record, other_sections = None, []
    for heading_index, body_lines in _security_sections(lines):
        located = {"heading": lines[heading_index], "line": heading_index + 1}
        if record is None and _security_section_is_record(body_lines):
            record, record_lines = located, body_lines
        else:
            other_sections.append(located)
    if record is None:
        absent = absent_security()
        absent["other_sections"] = other_sections
        return absent

    reviewed_sha, entries, unparsed, seen_ids = None, [], [], set()
    for raw_line in record_lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        reviewed = _SECURITY_REVIEWED_RE.match(stripped)
        if reviewed:
            # The last `Reviewed:` line wins, and a malformed one resets it: a stale valid SHA left
            # above a hand-edited line must never claim the head was reviewed. It never goes to
            # `unparsed`, which holds findings (an unparsed line counts as an open one): a bad SHA
            # only costs the evaluator's skip, so the pass reruns and rewrites it.
            value = reviewed.group(1).strip()
            reviewed_sha = value if _FULL_SHA_RE.fullmatch(value) else None
            continue
        match = _SECURITY_ROW_RE.match(stripped)
        fields = match.group(4).split(_SECURITY_FIELD_SEP) if match else []
        disposition = match.group(2) if match else None
        commit_sha = match.group(3) if match else None
        note = _SECURITY_FIELD_SEP.join(fields[2:]).strip() or None
        valid = (
            match is not None
            and disposition in SECURITY_DISPOSITIONS
            and (commit_sha is not None) == (disposition == "fixed")
            and len(fields) >= 2
            and all(part.strip() for part in fields[:2])
            and (disposition != "dismissed" or (note or "").startswith("operator:"))
        )
        if valid and int(match.group(1)) in seen_ids:
            unparsed.append(stripped)
        elif valid:
            seen_ids.add(int(match.group(1)))
            entries.append(
                {
                    "id": "SEC-%s" % match.group(1),
                    "seq": int(match.group(1)),
                    "disposition": disposition,
                    "commit_sha": commit_sha,
                    "claim": fields[0].strip(),
                    "anchor": fields[1].strip(),
                    "note": note,
                }
            )
        elif _SECURITY_ROWLIKE_RE.match(stripped):
            unparsed.append(stripped)
    return {
        "present": True,
        "reviewed_sha": reviewed_sha,
        "entries": entries,
        "unparsed": unparsed,
        "record": record,
        "other_sections": other_sections,
    }


def absent_security():
    """The security fact when there is no PR body, or no record, to scan. A factory (fresh dicts per
    call), as `absent_polish` is."""
    return {
        "present": False,
        "reviewed_sha": None,
        "entries": [],
        "unparsed": [],
        "record": None,
        "other_sections": [],
    }


# `security-coverage <changed-files> <findings>` — security-review.md "The procedure" step 1. The pass
# must read every changed file and list what it read in a `Covered:` block at the end of `## Findings`;
# this compares that list with the staged `git diff --name-only` list as exact repo-relative strings, so
# the model never does the set difference. Always `ok`, no decision code: a gap is the caller's to act on
# (one re-dispatch, then "the pass did not complete").
_COVERED_HEADER_RE = re.compile(r"^(\*\*)?Covered:(\*\*)?\s*$")
_COVERED_ITEM_RE = re.compile(r"^-\s+(.+?)\s*$")


def _covered_paths(findings_text):
    """The `Covered:` block's `- <path>` lines, in source order. The block starts at a line that is
    exactly `Covered:` (bolded or not) and ends at the first line that is neither blank nor a `- ` item;
    a reply with no block covers nothing. The only normalisation is cosmetic: surrounding backticks and
    a leading `./` are dropped, so a formatting slip never reads as a skipped file."""
    lines = (findings_text or "").splitlines()
    for i, line in enumerate(lines):
        if not _COVERED_HEADER_RE.match(line.strip()):
            continue
        paths = []
        for item in lines[i + 1:]:
            stripped = item.strip()
            if not stripped:
                continue
            match = _COVERED_ITEM_RE.match(stripped)
            if not match:
                break
            path = match.group(1).strip("`")
            paths.append(path[2:] if path.startswith("./") else path)
        return paths
    return []


def security_coverage(changed_text, findings_text):
    """`{"covered", "uncovered", "extra"}`: `covered` the `Covered:` block's paths, `uncovered` the listed
    changed paths absent from it, `extra` the covered paths absent from the list — each in source order.
    `changed_text` is one repo-relative path per line (`git diff --name-only`); blank lines are skipped."""
    changed = [line.strip() for line in (changed_text or "").splitlines() if line.strip()]
    covered = _covered_paths(findings_text)
    covered_set, changed_set = set(covered), set(changed)
    return {
        "covered": covered,
        "uncovered": [path for path in changed if path not in covered_set],
        "extra": [path for path in covered if path not in changed_set],
    }


def run_security_coverage(args):
    changed_text = _read_text_or_die(args.changed_files)
    findings_text = _read_text_or_die(args.findings)
    emit_ok(payload=security_coverage(changed_text, findings_text))
    sys.exit(EXIT_OK)


# ============================================================================================
# `## Follow-ups` section (a PR body) — import-only, NO subcommand and NO decision code
# ============================================================================================
#
# skills/_shared/follow-ups-section.md: `- Filed: <ref> — <title>[ · covers <ledger ids>]` per filed
# issue, `- Note: <text>` per procedural note. Read by `prep_evaluator`'s epic residue (the deferrals
# an epic's merged story PRs left). Lenient, never raises: a body written before the contract (4.37.0)
# has free-form bullets, so any other bullet's issue references are read too — except a `Note:` line
# (and its indented continuation) and lines under a legacy *Procedural notes* label, whose issue
# mentions are context, never deferrals.
#
# `#N` counts only when not preceded by a word character or `/`, so `owner/repo#5` (another repo) and
# `abc#5` are not this repo's #5. A full URL counts only when it names `repo`; with `repo` None, bare
# `#N` references alone are read.
_FOLLOW_UP_HASH_RE = re.compile(r"(?<![\w/])#(\d+)\b")
_FOLLOW_UPS_HEADING = r"Follow-?ups?(?:\s+filed)?"
_FOLLOW_UP_FILED_RE = re.compile(r"^-\s*Filed:\s*(.*)$", re.IGNORECASE)
_FOLLOW_UP_NOTE_RE = re.compile(r"^-\s*Note:", re.IGNORECASE)
_FOLLOW_UP_COVERS_RE = re.compile(r"·\s*covers\s+(P\d+\.\d+(?:\s*,\s*P\d+\.\d+)*)", re.IGNORECASE)
_LEDGER_ID_RE = re.compile(r"P\d+\.\d+")


def _follow_up_label(line):
    """A legacy sub-label line (`*Filed*`, `**Procedural notes:**`, …) reduced to its words, else None."""
    words = re.sub(r"[-*_:\s]+", " ", line).strip().lower()
    return words if words in ("filed", "procedural notes", "procedural note") else None


def _issue_refs(text, url_re):
    hits = []
    if url_re is not None:
        hits += [(m.start(), int(m.group(1))) for m in url_re.finditer(text)]
        text = url_re.sub(lambda m: " " * len(m.group(0)), text)
    hits += [(m.start(), int(m.group(1))) for m in _FOLLOW_UP_HASH_RE.finditer(text)]
    return [number for _pos, number in sorted(hits)]


def scan_follow_ups(pr_body_text, repo=None):
    """A PR body's `## Follow-ups` section. Returns `{"present", "issues", "covers"}`:

      - `present` — the section exists.
      - `issues` — the filed issue numbers, in source order, de-duplicated: a `Filed:` line's own
        reference (a mention in its title is not a second filing), and every reference on a legacy
        free-form bullet.
      - `covers` — `{issue: [ledger ids]}` from each `Filed:` line's ` · covers` segment.

    Never raises."""
    lines = (pr_body_text or "").splitlines()
    found = _find_section(lines, _FOLLOW_UPS_HEADING)
    if found is None:
        return {"present": False, "issues": [], "covers": {}}
    start, end = found
    url_re = None
    if repo:
        url_re = re.compile(
            r"https?://github\.com/" + re.escape(repo) + r"/issues/(\d+)\b", re.IGNORECASE
        )
    issues, covers = [], {}
    in_notes = False  # under a legacy *Procedural notes* label
    in_note = False  # inside a `- Note:` bullet (its indented continuation lines too)
    for raw in lines[start:end]:
        if not raw.strip():
            continue
        stripped = raw.strip()
        label = _follow_up_label(stripped)
        if label is not None:
            in_notes = label != "filed"
            in_note = False
            continue
        if raw[:1].isspace() and in_note:
            continue
        in_note = bool(_FOLLOW_UP_NOTE_RE.match(stripped))
        if in_note or in_notes:
            continue
        filed = _FOLLOW_UP_FILED_RE.match(stripped)
        if filed:
            refs = _issue_refs(filed.group(1), url_re)[:1]
            if refs:
                cover = _FOLLOW_UP_COVERS_RE.search(filed.group(1))
                if cover:
                    ids = covers.setdefault(refs[0], [])
                    ids += [i for i in _LEDGER_ID_RE.findall(cover.group(1)) if i not in ids]
        else:
            refs = _issue_refs(stripped, url_re)
        for number in refs:
            if number not in issues:
                issues.append(number)
    return {"present": True, "issues": issues, "covers": covers}


# ============================================================================================
# The review-loop record (a PR's loop comment) — import-only, NO subcommand and NO decision code
# ============================================================================================
#
# skills/_shared/review-loop-record.md: the resolver's loop comment opens with the marker, then one
# `- R<phase>.<seq> · …` line per classified finding and one `Review runs:` line per loop pass. Read
# by `scripts/review_report.py` (the `review-report` tool, and `prep_setup.py --repo` through it). A loop
# comment written before the marker is read leniently as `legacy`: only the shapes the resolver's
# step 8 already fixed (`Settled (not addressed):` entries, `Defect fixes:` lines) and the phase and
# round headings, never its prose items — their parentheticals were never pinned. Never raises: a
# record-shaped line that fails the grammar lands in `unparsed`.
REVIEW_LOOP_MARKER = "<!-- review-loop:v1 -->"

# The closed sets, in review-loop-record.md's table order (the contract test pins both).
REVIEW_BUCKETS = (
    "addressable",
    "pre-existing",
    "no-shown-cost",
    "explicitly-deferred",
    "decision-required",
    "grounding-violation",
    "plan-settled",
    "refuted",
    "deferred-by-plan",
)
REVIEW_PROVENANCES = ("introduced", "adjacent", "pre-existing")
REVIEW_TIERS = ("defect", "polish")
# `ledger` stands for the `ledger P<phase>.<seq>` form, whose id rides in `ledger_id`.
REVIEW_ACTIONS = ("fixed", "ledger", "settled", "deferred", "decision")
REVIEW_SOURCES = ("review", "cold-read", "evaluator", "human")
REVIEW_CHECKS = ("upheld", "overturned")
REVIEW_EXITS = ("settled", "accepted", "re-plan", "restructure", "abort")

_REVIEW_FINDING_RE = re.compile(r"^-\s*R(\d+)\.(\d+)\s*·\s*(.*)$")
# Finding-shaped: a bullet led by `R<digit>`, so a near-miss is reported rather than skipped.
_REVIEW_FINDING_LIKE_RE = re.compile(r"^[-*+]\s*R\d")
_REVIEW_FIELD_SEP = "·"
_REVIEW_ITEM_SEP = " — "
_REVIEW_LEDGER_ACTION_RE = re.compile(r"^ledger (P(\d+)\.(\d+))$")
_REVIEW_CLASS_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){0,4}$")
# A site is one file path: no whitespace and no `:` — never `path:line` (the ledger's durable-anchor
# rule) and never `path:symbol` (a `Defect fixes:` site), which would split one file's counts.
_REVIEW_SITE_RE = re.compile(r"^[^\s:]+$")
_REVIEW_RUNS_RE = re.compile(r"^Review runs:\s*phase\s+(\d+)\s*·\s*(\d+)\s*·\s*([a-z-]+)\s*$")
_REVIEW_RUNS_LIKE_RE = re.compile(r"^Review runs:", re.IGNORECASE)
# `## Review loop — phase <N>` is the pinned heading; `## Phase <N> review loop` is a shape some
# pre-contract comments used, read for legacy round counts only.
_REVIEW_PHASE_HEADING_RE = re.compile(
    r"^##(?!#)\s*(?:Review loop\s*[—–-]+\s*phase\s+(\d+)|Phase\s+(\d+)\s+review loop)\b", re.IGNORECASE
)
_REVIEW_ROUND_HEADING_RE = re.compile(r"^###(?!#)\s*Round\b", re.IGNORECASE)
# Pre-contract comments also opened a round with a bold `**Round <k>**` line; read on legacy only, where
# the heading was never pinned (the record format's round is always `### Round <k>`).
_REVIEW_LEGACY_ROUND_RE = re.compile(r"^\*\*Round\s+\d+\b", re.IGNORECASE)
_REVIEW_SETTLED_HEAD = "Settled (not addressed):"
_REVIEW_SETTLED_ENTRY_RE = re.compile(r"^-\s*([a-z-]+)\s*—\s*(.+)$")
# The block's three lead words (review-fix-round.md step 8): any other bullet is prose that follows it.
_REVIEW_SETTLED_BUCKETS = ("plan-settled", "deferred-by-plan", "refuted")
_REVIEW_SETTLED_CHECK_RE = re.compile(r"—\s*check:\s*upheld\b")
_REVIEW_SETTLED_REPEATS_RE = re.compile(r"\(×\s*(\d+)[^)]*\)\s*$")
_REVIEW_DEFECT_FIXES_RE = re.compile(r"^Defect fixes:\s*phase\s+(\d+)\s*@\s*([0-9a-f]{7,40})\s*[—–-]+\s*(.*)$")
_REVIEW_LEGACY_TRIGGERS = (_REVIEW_SETTLED_HEAD, "Defect fixes: phase", "Cold read: phase")


def _parse_review_finding(stripped):
    """One finding line → its dict, or None when it fails the grammar."""
    match = _REVIEW_FINDING_RE.match(stripped)
    if match is None:
        return None
    head, sep, item = match.group(3).partition(_REVIEW_ITEM_SEP)
    item = item.strip()
    if not sep or not item:
        return None
    fields = [part.strip() for part in head.split(_REVIEW_FIELD_SEP)]
    if len(fields) not in (7, 8):
        return None
    bucket, provenance, tier, action, source, class_field, site_field = fields[:7]
    check_field = fields[7] if len(fields) == 8 else None
    ledger = _REVIEW_LEDGER_ACTION_RE.match(action)
    if ledger:
        action, ledger_id = "ledger", ledger.group(1)
    else:
        ledger_id = None
    if not class_field.startswith("class:") or not site_field.startswith("site:"):
        return None
    class_key = class_field[len("class:"):].strip()
    site = site_field[len("site:"):].strip()
    check = None
    if check_field is not None:
        if not check_field.startswith("check:"):
            return None
        check = check_field[len("check:"):].strip()
    valid = (
        bucket in REVIEW_BUCKETS
        and provenance in REVIEW_PROVENANCES
        and tier in REVIEW_TIERS
        and action in REVIEW_ACTIONS
        and source in REVIEW_SOURCES
        and _REVIEW_CLASS_KEY_RE.match(class_key)
        and (site == "(none)" or _REVIEW_SITE_RE.match(site))
        and (check is None or check in REVIEW_CHECKS)
    )
    if not valid:
        return None
    return {
        "id": "R%s.%s" % (match.group(1), match.group(2)),
        "phase": int(match.group(1)),
        "seq": int(match.group(2)),
        "bucket": bucket,
        "provenance": provenance,
        "tier": tier,
        "action": action,
        "ledger_id": ledger_id,
        "source": source,
        "class_key": class_key,
        "site": None if site == "(none)" else site,
        "check": check,
        "item": item,
        "line": stripped,
    }


def _parse_settled_entry(stripped):
    match = _REVIEW_SETTLED_ENTRY_RE.match(stripped)
    if match is None or match.group(1) not in _REVIEW_SETTLED_BUCKETS:
        return None
    repeats = _REVIEW_SETTLED_REPEATS_RE.search(stripped)
    return {
        "bucket": match.group(1),
        "item": match.group(2).split(_REVIEW_ITEM_SEP)[0].strip(),
        "check": "upheld" if _REVIEW_SETTLED_CHECK_RE.search(stripped) else None,
        "repeats": int(repeats.group(1)) if repeats else 1,
    }


def absent_review_loop():
    """The scan of a comment that is not a loop comment. A factory, as `absent_polish` is."""
    return {
        "format": None,
        "phase_headings": [],
        "findings": [],
        "runs": [],
        "settled": [],
        "defect_fixes": [],
        "unparsed": [],
    }


def scan_review_loop(comment_text):
    """Scan one PR comment for the review-loop record. Returns
    `{"format", "phase_headings", "findings", "runs", "settled", "defect_fixes", "unparsed"}`:

      - `format` — `"record"` when the first non-blank line is `REVIEW_LOOP_MARKER`; `"legacy"` when
        the comment carries a phase heading or a `Settled (not addressed):` / `Defect fixes: phase` /
        `Cold read: phase` line; else `None`, and every list is empty.
      - `phase_headings` — `{"phase", "rounds"}` per phase heading in order, `rounds` counting the
        `### Round` headings under it (and, on a legacy comment, `**Round <k>**` lines); rounds before
        the first heading open a `phase: None` entry.
      - `findings` — record only: one dict per valid finding line (`id`, `phase`, `seq`, `bucket`,
        `provenance`, `tier`, `action`, `ledger_id`, `source`, `class_key`, `site`, `check`,
        `item`, and `line`, the line verbatim). `site` is `None` for `site: (none)`.
      - `runs` — record only: `{"phase", "count", "exit"}` per `Review runs:` line.
      - `settled` — `{"bucket", "item", "check", "repeats"}` per `Settled (not addressed):` entry.
      - `defect_fixes` — `{"phase", "sha", "sites"}` per `Defect fixes:` line, `sites` verbatim.
      - `unparsed` — record only: finding-shaped or `Review runs:`-led lines that fail the grammar,
        and a finding line repeating an id already seen. `Security review:` and `Finalisation:`
        summary lines are neither.

    Never raises.
    """
    lines = [line.strip() for line in (comment_text or "").splitlines()]
    first = next((line for line in lines if line), None)
    if first == REVIEW_LOOP_MARKER:
        fmt = "record"
    elif any(
        _REVIEW_PHASE_HEADING_RE.match(line) or line.startswith(_REVIEW_LEGACY_TRIGGERS) for line in lines
    ):
        fmt = "legacy"
    else:
        return absent_review_loop()

    scan = absent_review_loop()
    scan["format"] = fmt
    seen_ids, in_settled = set(), False
    for line in lines:
        if not line:
            # The block is one run of lines; prose after a blank line is never one of its entries.
            in_settled = False
            continue
        heading = _REVIEW_PHASE_HEADING_RE.match(line)
        if heading:
            in_settled = False
            scan["phase_headings"].append({"phase": int(heading.group(1) or heading.group(2)), "rounds": 0})
            continue
        if _REVIEW_ROUND_HEADING_RE.match(line) or (fmt == "legacy" and _REVIEW_LEGACY_ROUND_RE.match(line)):
            in_settled = False
            if not scan["phase_headings"]:
                scan["phase_headings"].append({"phase": None, "rounds": 0})
            scan["phase_headings"][-1]["rounds"] += 1
            continue
        if line == _REVIEW_SETTLED_HEAD:
            in_settled = True
            continue
        if in_settled:
            entry = _parse_settled_entry(line) if line.startswith("-") else None
            if entry is not None:
                scan["settled"].append(entry)
                continue
            in_settled = False
        fixes = _REVIEW_DEFECT_FIXES_RE.match(line)
        if fixes:
            scan["defect_fixes"].append(
                {
                    "phase": int(fixes.group(1)),
                    "sha": fixes.group(2),
                    "sites": [site.strip() for site in fixes.group(3).split(";") if site.strip()],
                }
            )
            continue
        if fmt != "record":
            continue
        runs = _REVIEW_RUNS_RE.match(line)
        if runs and runs.group(3) in REVIEW_EXITS:
            scan["runs"].append({"phase": int(runs.group(1)), "count": int(runs.group(2)), "exit": runs.group(3)})
        elif _REVIEW_RUNS_LIKE_RE.match(line):
            scan["unparsed"].append(line)
        elif _REVIEW_FINDING_LIKE_RE.match(line):
            finding = _parse_review_finding(line)
            if finding is None or finding["id"] in seen_ids:
                scan["unparsed"].append(line)
            else:
                seen_ids.add(finding["id"])
                scan["findings"].append(finding)
    return scan


# ============================================================================================
# `## Stories` checklist parsing (legacy epic body) — import-only, NO subcommand and NO decision code
# ============================================================================================
#
# The `checklist` tier of `skills/_shared/epic-story-hierarchy.md`'s two-tier read: epics filed
# before the native parent/sub-issue relation carry their story set only as `- [ ] #NN — <title>`
# bullets, and with no backfill path they do so for life. Best-effort and non-raising: a line that
# matches neither bullet shape is prose, not a story entry. One copy, shared by the planner and the
# slicer (who read the story set) and `branching.search_parent_epic` (who reads it from the story's
# side — membership in this checklist, never a mention anywhere else in the epic body, is the legacy
# parent edge).
#
# A filed bullet is `#NN`, then either nothing or a separator (`—`, `--`, `-`) and a title: the
# checklist is hand-maintained, so a ticked `- [x] #151` is still story #151. `#NN` followed straight
# by prose (`- [ ] #3 lanes of work`, `- [ ] #151abc`) is NOT a filed story — reading it as one would
# send the planner and slicer to fetch an issue nobody filed (a fatal `gh issue view` on a missing
# number) — so it falls through to a placeholder. The heading is the one `dod_summary` already
# excepts: `## Stories` or `## Stories (…)`, so a hand-annotated heading keeps its story set.
_STORY_FILED_RE = re.compile(r"^-\s*\[( |x|X)\]\s*#(\d+)\s*(?:(?:—|--|-)\s*(.*))?$")
_STORY_PLAIN_RE = re.compile(r"^-\s*\[( |x|X)\]\s*(.+)$")
_LEVEL2_HEADING_RE = re.compile(r"^##(?!#)")


def _is_stories_heading(line):
    match = _ANY_SECTION_HEADING_RE.match(line)
    return bool(match) and bool(_NON_DOD_CHECKLIST_HEADING_RE.match(match.group(1)))


def parse_stories_section(issue_body):
    """An epic body's `## Stories` checklist as `[{"number", "title", "checked"}]`, in source order.
    `number` is `None` for a placeholder bullet (a story not yet filed). No section returns `[]` —
    a fresh epic (native children only) or a non-epic body both legitimately lack one."""
    lines = (issue_body or "").splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if _is_stories_heading(line)), None)
    if start is None:
        return []
    end = next((j for j in range(start, len(lines)) if _LEVEL2_HEADING_RE.match(lines[j])), len(lines))

    entries = []
    for raw_line in lines[start:end]:
        stripped = raw_line.strip()
        filed_match = _STORY_FILED_RE.match(stripped)
        if filed_match:
            entries.append(
                {
                    "number": int(filed_match.group(2)),
                    "title": (filed_match.group(3) or "").strip(),
                    "checked": filed_match.group(1) in ("x", "X"),
                }
            )
            continue
        plain_match = _STORY_PLAIN_RE.match(stripped)
        if plain_match:
            entries.append(
                {
                    "number": None,
                    "title": plain_match.group(2).strip(),
                    "checked": plain_match.group(1) in ("x", "X"),
                }
            )
    return entries


def stories_checklist_lists(issue_body, story_number):
    """Whether `issue_body`'s `## Stories` checklist carries a filed bullet for `story_number` — the
    legacy epic↔story edge. A `#<N>` anywhere else in the body (prose, another section's checklist,
    a placeholder bullet that merely mentions it) is a reference, not membership."""
    number = int(story_number)
    return any(entry["number"] == number for entry in parse_stories_section(issue_body))


# ============================================================================================
# Dispatch
# ============================================================================================


def build_parser():
    parser = argparse.ArgumentParser(
        prog="parse.py",
        description="One parser for the shared GitHub body grammars (architecture.md §2, §3).",
    )
    sub = parser.add_subparsers(dest="subcommand")

    p_dod = sub.add_parser("dod")
    p_dod.add_argument("body_file")
    p_dod.add_argument("--render", action="store_true")

    p_oq = sub.add_parser("oq-links")
    p_oq.add_argument("body_file")

    p_phases = sub.add_parser("phases")
    p_phases.add_argument("body_file")
    p_phases.add_argument("--issue-body", default=None)

    p_coverage = sub.add_parser("security-coverage")
    p_coverage.add_argument("changed_files")
    p_coverage.add_argument("findings")

    return parser


def main(argv):
    parser = build_parser()
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand is None:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)

    if args.subcommand == "dod":
        run_dod(args)
    elif args.subcommand == "oq-links":
        run_oq_links(args)
    elif args.subcommand == "phases":
        run_phases(args)
    elif args.subcommand == "security-coverage":
        run_security_coverage(args)
    else:  # pragma: no cover — argparse's own subparser choices already exclude this
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)


if __name__ == "__main__":
    main(sys.argv[1:])
