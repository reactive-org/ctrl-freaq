#!/usr/bin/env python3
"""plan_parts.py — one implementation plan, three comments (``skills/_shared/plan-parts.md``).

A plan is authored and read as ONE document, but posted as three comments, because one comment is
capped at :data:`pipelib.limits.BODY_CHAR_LIMIT` characters and a real plan outgrows it:

- the **main** comment (``<!-- implementation-plan:v1 -->``) — every section not listed below: the
  decisions, the phases or story sections, the risks and open questions, the footer;
- the **build** part — ``## Changes (file-level)``, ``## Data model / schema impact``,
  ``## Test plan``, ``## Coverage gap``;
- the **references** part — ``## Doc grounding``, ``## External sources consulted``.

The main comment keeps every heading in its schema position; a moved section's body there is one
stub bullet linking to its part. So the split is mechanical both ways and the model never sees it:
the planner stages one body and :func:`split` cuts it; every reader gets :func:`assemble`'s output.

Two surfaces:

- **The read model** (import-only, pure — never prints, exits or raises): :func:`find_parts` locates
  the part comments in an already-fetched thread (trusted authors only, ``pipelib.trust``; a duplicate
  is ``MARKER_AMBIGUOUS``) and
  :func:`assemble` puts the sections back. ``gh_gather.py`` applies them to the plan marker lookup,
  and ``prep_evaluator.py`` to its own thread scan, so every prep stages the whole plan.
- **``split``** (CLI): run by the planner on every staging. Writes the three bodies and reports each
  one's size against the cap — the plan's size fact, per comment. ``ok`` always; an over-cap comment
  is the planner's to fix (the ``PHASES_MALFORMED`` posture), and ``gh_persist.py``'s own size gate
  still refuses it.

Usage:
    plan_parts.py split <plan.md> --out-dir <dir> [--url build=<url>] [--url references=<url>]
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipelib.decisions import MARKER_AMBIGUOUS, needs_decision  # noqa: E402
from pipelib.envelope import EXIT_OK, EXIT_USAGE_ERROR, emit_ok  # noqa: E402
from pipelib.limits import body_size  # noqa: E402
from pipelib.trust import partition_trusted  # noqa: E402

MAIN_MARKER = "<!-- implementation-plan:v1 -->"

# A separate marker FAMILY, as the shipped-phase records' is: `<!-- implementation-plan-part:` never
# starts with `<!-- implementation-plan:v1 -->`, so every plan lookup (`startswith`) stays blind to
# the parts and a part can never make the plan itself MARKER_AMBIGUOUS.
PART_PREFIX = "<!-- implementation-plan-part:v1:"
PART_SUFFIX = " -->"

# slug -> (label, the section names it carries). Order is the order parts are posted and listed.
PARTS = (
    ("build", ("Changes (file-level)", "Data model / schema impact", "Test plan", "Coverage gap")),
    ("references", ("Doc grounding", "External sources consulted")),
)
SLUGS = tuple(slug for slug, _ in PARTS)

_PART_RE = re.compile(re.escape(PART_PREFIX) + r"([a-z]+)" + re.escape(PART_SUFFIX))
_HEADING_RE = re.compile(r"^##(?!#)\s*(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_FOOTER_RE = re.compile(r"^_Authored by ")
# The stub bullet `stub_line` writes, linked or not — how a reader tells a moved section from content.
_STUB_RE = re.compile(r"^- In this plan's \[?([a-z]+) part(?:\]\([^)]*\))?\.$")
# A schema heading may carry a trailing `(…)` annotation (`## Phases (multi-phase only)` in a draft).
_ANNOTATION_RE = re.compile(r"\s*\(.*\)\s*$")


def part_marker(slug):
    """The first line of the ``slug`` part comment."""
    return "%s%s%s" % (PART_PREFIX, slug, PART_SUFFIX)


def _section_slug(name):
    """The part a section name belongs to, or ``None`` for the main comment."""
    names = {name.lower(), _ANNOTATION_RE.sub("", name).lower()}
    for slug, sections in PARTS:
        if any(section.lower() in names for section in sections):
            return slug
    return None


def _lf(text):
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _strip_blank_tail(lines):
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _parse(text):
    """``(head, sections, footer)``: the lines before the first ``##`` heading, ``[(name, heading
    line, body lines)]`` in order, and the footer lines (the last top-level ``_Authored by`` line to
    the end). A ``##`` line inside a code fence is body, not a heading."""
    lines = _lf(text).split("\n")
    footer_at = None
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence and _FOOTER_RE.match(line):
            footer_at = index
    body_lines = lines if footer_at is None else lines[:footer_at]
    footer = [] if footer_at is None else _strip_blank_tail(lines[footer_at:])

    head, sections = [], []
    current = None
    in_fence = False
    for line in body_lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING_RE.match(line)
        if heading:
            current = [heading.group(1), line, []]
            sections.append(current)
        elif current is None:
            head.append(line)
        else:
            current[2].append(line)
    return (
        _strip_blank_tail(head),
        [(name, heading, _strip_blank_tail(body)) for name, heading, body in sections],
        footer,
    )


def _render(head, sections, footer):
    blocks = ["\n".join(head)] if head else []
    blocks += ["\n".join([heading] + body) for _, heading, body in sections]
    if footer:
        blocks.append("\n".join(footer))
    return "\n\n".join(blocks) + "\n"


def stub_line(slug, url=None):
    """The one bullet that stands in for a moved section in the main comment."""
    if url:
        return "- In this plan's [%s part](%s)." % (slug, url)
    return "- In this plan's %s part." % slug


def _is_stub(body):
    """Whether a section body is just the stub that stands in for a moved section."""
    lines = [line for line in body if line.strip()]
    return len(lines) == 1 and _STUB_RE.match(lines[0].strip()) is not None


def has_stubs(main_text):
    """Whether a main comment carries any moved section — i.e. was written as parts."""
    return any(_is_stub(body) for _, _, body in _parse(main_text)[1])


def split(text, urls=None):
    """Cut a whole plan into ``{"main": text, "build": text, "references": text}``.

    ``urls`` maps a slug to its posted comment URL, for the main comment's stub links; a slug without
    one gets an unlinked stub (a fresh plan's parts are posted before their URLs exist, then the main
    comment is cut again with them). A part with no sections still gets a body, so an existing part
    comment is overwritten rather than left stale.
    """
    urls = urls or {}
    head, sections, footer = _parse(text)
    main_sections = []
    part_sections = {slug: [] for slug in SLUGS}
    for name, heading, body in sections:
        slug = _section_slug(name)
        if slug is None:
            main_sections.append((name, heading, body))
        else:
            part_sections[slug].append((name, heading, body))
            main_sections.append((name, heading, [stub_line(slug, urls.get(slug))]))
    out = {"main": _render(head, main_sections, footer)}
    for slug, names in PARTS:
        part_head = [
            part_marker(slug),
            "**Implementation plan — %s part** (%s). Part of the implementation plan on this issue: "
            "the decisions are in the main plan comment. Written with it by the planner; re-run the "
            "planner to revise — do not hand-edit." % (slug, ", ".join("`## %s`" % n for n in names)),
        ]
        if not part_sections[slug]:
            part_head += ["", "_This plan has no sections in this part._"]
        out[slug] = _render(part_head, part_sections[slug], [])
    return out


def assemble(main_text, part_texts):
    """Put each part's sections back into the main comment, in the main comment's heading order.

    ``part_texts`` maps a slug to its comment body (a missing slug is a plan written before parts
    existed: its sections are still in the main comment, untouched). Returns ``(text, report)``;
    ``report`` lists ``missing`` (a stub in main with no part section to replace it — the part lacks
    it, or the part comment is gone; the stub is kept and reported, never read silently as content) and
    ``unplaced`` (a part section with no heading in main — appended after main's last section).
    """
    head, sections, footer = _parse(main_text)
    from_parts = {}
    order = []
    for slug in SLUGS:
        if part_texts.get(slug) is None:
            continue
        for name, heading, body in _parse(part_texts[slug])[1]:
            key = name.lower()
            if key not in from_parts:
                order.append(key)
            from_parts[key] = (name, heading, body)
    placed = set()
    out_sections = []
    missing = []
    for name, heading, body in sections:
        key = name.lower()
        if key in from_parts:
            out_sections.append(from_parts[key])
            placed.add(key)
        else:
            # A stub with nothing to replace it: the part lacks the section, or the part comment
            # itself is gone. Either way a reader would take the stub for the section's content.
            if _section_slug(name) is not None and _is_stub(body):
                missing.append(name)
            out_sections.append((name, heading, body))
    unplaced = [from_parts[key][0] for key in order if key not in placed]
    out_sections += [from_parts[key] for key in order if key not in placed]
    return _render(head, out_sections, footer), {"missing": missing, "unplaced": unplaced}


def comment_rest_id(comment):
    """A comment's REST numeric id — a normalized thread comment's ``databaseId``, a raw REST
    comment's ``id`` (#34: the id space every REST comment endpoint addresses)."""
    return comment.get("databaseId", comment.get("id"))


def find_parts(comments):
    """The part comments in a thread: ``(parts, decision, untrusted)``. ``parts`` maps a slug to
    ``{"id", "url", "body", "updated_at"}``; ``decision`` is ``MARKER_AMBIGUOUS`` when a slug matches
    more than one comment (that slug is omitted — which copy is current is exactly what is unknown). A
    part comment is never edited by hand, so a duplicate means a write went wrong.

    Only a trusted author's part counts (``pipelib.trust``, #179): a part comment whose author is not
    ``OWNER`` / ``MEMBER`` / ``COLLABORATOR`` is skipped before the duplicate count, so it can neither
    stand in for a section nor make a slug ambiguous. ``untrusted`` is how many were skipped, every
    known slug together; a comment naming an unknown slug is not a part and is never counted."""
    hits = {}
    for comment in comments or []:
        body = _lf((comment or {}).get("body"))
        first = body.split("\n", 1)[0].strip()
        match = _PART_RE.fullmatch(first)
        if match and match.group(1) in SLUGS:
            hits.setdefault(match.group(1), []).append(comment)
    untrusted = 0
    for slug in list(hits):
        hits[slug], skipped = partition_trusted(hits[slug])
        untrusted += skipped
    parts = {}
    decision = None
    for slug in SLUGS:
        found = hits.get(slug, [])
        if len(found) == 1:
            comment = found[0]
            parts[slug] = {
                "id": comment_rest_id(comment),
                "url": comment.get("url") or comment.get("html_url"),
                "body": comment.get("body") or "",
                "updated_at": comment.get("updated_at") or comment.get("updatedAt"),
            }
        elif len(found) > 1 and decision is None:
            decision = needs_decision(
                MARKER_AMBIGUOUS,
                summary="%d comments carry the plan's %s part marker — expected at most one"
                % (len(found), slug),
                context={
                    "marker_prefix": part_marker(slug),
                    "comment_ids": [comment_rest_id(c) for c in found],
                    "comment_urls": [c.get("url") or c.get("html_url") for c in found],
                },
                options=[
                    "inspect each comment and pick the one to treat as current",
                    "delete the stale duplicate comment(s), then re-run",
                ],
            )
    return parts, decision, untrusted


def assemble_from_thread(main_body, comments):
    """The whole plan from its main comment and the thread holding its parts.

    Returns ``(text, parts, notes, decision, untrusted_parts)``: ``parts`` is ``[{"slug", "id",
    "url"}]`` for every trusted part found (what a revise edits in place), ``notes`` is the
    human-readable form of :func:`assemble`'s report, ``decision`` is :func:`find_parts`'s, and
    ``untrusted_parts`` its count of skipped untrusted part comments — reported whether or not a main
    comment exists, because orphaned parts are read without one. ``comments`` is the whole thread;
    the part filter, the author check included, is applied here."""
    found, decision, untrusted = find_parts(comments)
    if not main_body or (not found and not has_stubs(main_body)):
        # A plan written before parts existed (or a lone main comment): hand it over byte-for-byte.
        parts = [{"slug": slug, "id": found[slug]["id"], "url": found[slug]["url"]} for slug in SLUGS if slug in found]
        return main_body, parts, [], decision, untrusted
    text, report = assemble(main_body, {slug: part["body"] for slug, part in found.items()})
    notes = []
    if report["missing"]:
        notes.append(
            "the plan's main comment points at a part for %s, but no part comment carries it — the "
            "section's content is unavailable; "
            "re-run the planner to repair the plan (skills/_shared/plan-parts.md)"
            % ", ".join("`## %s`" % name for name in report["missing"])
        )
    if report["unplaced"]:
        notes.append(
            "a plan part carries %s, which the main comment has no heading for — read at the end of "
            "the plan" % ", ".join("`## %s`" % name for name in report["unplaced"])
        )
    parts = [
        {"slug": slug, "id": found[slug]["id"], "url": found[slug]["url"]} for slug in SLUGS if slug in found
    ]
    return text, parts, notes, decision, untrusted


def _read_or_die(path_str):
    try:
        with open(path_str, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write("plan_parts: cannot read %s: %s\n" % (path_str, exc))
        sys.exit(EXIT_USAGE_ERROR)


def main(argv):
    parser = argparse.ArgumentParser(prog="plan_parts.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="subcommand")
    p_split = sub.add_parser("split")
    p_split.add_argument("plan")
    p_split.add_argument("--out-dir", required=True)
    p_split.add_argument("--url", action="append", default=[], help="<slug>=<posted part comment URL>")
    if not argv:
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    args = parser.parse_args(argv)
    if args.subcommand != "split":
        parser.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE_ERROR)
    urls = {}
    for item in args.url:
        slug, sep, url = item.partition("=")
        if not sep or slug not in SLUGS or not url:
            sys.stderr.write("plan_parts: --url expects <slug>=<url> with slug in %s, got %r\n" % (SLUGS, item))
            sys.exit(EXIT_USAGE_ERROR)
        urls[slug] = url

    bodies = split(_read_or_die(args.plan), urls)
    out_dir = Path(args.out_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write("plan_parts: cannot create %s: %s\n" % (out_dir, exc))
        sys.exit(1)
    comments = []
    for name in ("main",) + SLUGS:
        path = out_dir / ("plan-%s.md" % name)
        path.write_text(bodies[name], encoding="utf-8")
        comments.append({"name": name, "path": str(path), "size": body_size(bodies[name])})
    emit_ok(
        payload={
            "comments": comments,
            "over_limit": [c["name"] for c in comments if c["size"]["over_limit"]],
            "unlinked": [slug for slug in SLUGS if slug not in urls],
        }
    )
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main(sys.argv[1:])
