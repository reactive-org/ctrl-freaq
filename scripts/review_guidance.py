"""review_guidance.py — the consuming repo's ``<!-- issue-resolver-review-guidance -->`` block (#99).

The block is short, owner-written prose telling the resolver's reviewer what failure looks like in this
repo, which paths to skip, how many nits to report and what evidence a finding needs. It is
**user-owned**, like ``<!-- claude-code-stack-profile -->``: its one reader passes the text to the
reviewer verbatim and parses no structure, so there is no canonical form for ``setup`` to reconcile it
to — setup seeds it when absent and re-ingests the owner's text as the base on a re-run.

One read, two consumers: ``prep_resolver.py`` composes :func:`read` into ``facts.config.review_guidance``
(the S5.1 reviewer ``<context>``), and ``prep_setup.py`` composes :func:`listed` to mark which recurring
class keys the block already names. The block is read from the working tree of the checkout the command
runs in, as every config block is (architecture.md §6 rule 1).

**Why the flags are neutralised here, not by the model.** ``code-review`` reads ``--comment``, ``--fix``,
``--post``, ``--no-post`` and ``--max-findings`` wherever they stand as whitespace-delimited tokens, so a
bare one in the owner's prose would make the loop's own review post inline PR comments or apply its own
findings. S5.1's "no bare flag token" rule governs the text the model writes; the owner's text is not
the model's, so the script wraps each such token in a code span before the text reaches ``<context>``.

Import-only, stdlib-only, pure: no CLI, no ``gh`` call, never raises on a malformed block (a malformed
block in one candidate file falls through to the other, as ``config_block.read_block_anywhere`` does).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config_block  # noqa: E402  (import after sys.path setup, by necessity; in-process composition)

MARKER = "issue-resolver-review-guidance"
REVIEWER_FLAGS = ("--comment", "--fix", "--post", "--no-post", "--max-findings")

_WHITESPACE_RE = re.compile(r"(\s+)")


def _is_flag_token(token):
    if "`" in token:
        return False
    return any(token == flag or token.startswith(flag + "=") for flag in REVIEWER_FLAGS)


def neutralise(text):
    """Wrap every whitespace-delimited reviewer-flag token (``--fix``, ``--max-findings=3``) in a code
    span; a token already carrying an attached backtick is left alone, and whitespace is preserved
    byte for byte. Returns ``(text, wrapped)`` — ``wrapped`` lists the original tokens, in order."""
    wrapped = []
    parts = _WHITESPACE_RE.split(text)
    for index, part in enumerate(parts):
        if index % 2 == 0 and part and _is_flag_token(part):
            wrapped.append(part)
            parts[index] = "`%s`" % part
    return "".join(parts), wrapped


def read(root):
    """The block as ``{"present", "source", "text", "neutralised", "chars"}``. ``source`` is the absolute
    path of the file the block was read from; ``text`` is the interior, neutralised, joined with ``\\n``.
    An absent block, or one whose interior is empty (``read_block_anywhere`` reports that as present with
    no lines), reads as not present."""
    present, lines, source = config_block.read_block_anywhere(root, MARKER)
    if not present or not "\n".join(lines).strip():
        return {"present": False, "source": None, "text": None, "neutralised": [], "chars": 0}
    text, wrapped = neutralise("\n".join(lines))
    return {"present": True, "source": source, "text": text, "neutralised": wrapped, "chars": len(text)}


def listed(text, class_keys):
    """The class keys ``text`` already names as the exact code span `` `<key>` `` — never a prefix of
    a longer key, never a bare word."""
    if not text:
        return set()
    return {key for key in class_keys if "`%s`" % key in text}
