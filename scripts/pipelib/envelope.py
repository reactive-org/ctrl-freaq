"""The envelope contract itself (architecture.md §3): build and emit the one-JSON-object-on-stdout
every v2 script produces, plus the exit-code semantics that go with it.

Exit codes (architecture.md §3):
    0   envelope present on stdout; consult ``status``.
    2   usage error — malformed invocation; no envelope is emitted.
    *   (any other non-zero) hard failure (command/network); stderr carries the faithful error;
        no envelope is guaranteed.

``status`` is ``"ok"`` or ``"needs_decision"``. ``needs_decision`` is a *valid outcome*, not an
error — exit code stays 0. Every envelope may carry ``notices: []`` (non-blocking degradations,
e.g. ``DEPS_UNSUPPORTED``) regardless of status.
"""

import json
import os
import sys
import tempfile

from pipelib.decisions import DECISION_CODES

STATUS_OK = "ok"
STATUS_NEEDS_DECISION = "needs_decision"

EXIT_OK = 0
EXIT_USAGE_ERROR = 2

_VALID_STATUSES = frozenset({STATUS_OK, STATUS_NEEDS_DECISION})


def build_ok(payload=None, notices=None):
    """Build an ``ok`` envelope dict: ``{"status": "ok", ...payload, "notices": [...]}``.

    ``payload`` is the script-specific facts/fields merged in at the top level (e.g. a facts
    block, or a write receipt's ``body_bytes``/``body_sha256``) — merged rather than nested, so a
    reader does one ``json.loads`` and finds both ``status`` and the payload fields at the same
    level. ``payload`` must not define ``status`` or ``notices`` itself (those are this function's
    to set) — a collision raises, so a script cannot accidentally shadow the envelope's own keys.
    ``notices`` defaults to an empty list, never omitted, so every envelope has a stable key set.
    """
    payload = dict(payload) if payload is not None else {}
    for reserved in ("status", "notices"):
        if reserved in payload:
            raise ValueError(
                "pipelib.envelope.build_ok: payload must not define reserved key %r itself"
                % reserved
            )
    envelope = {"status": STATUS_OK}
    envelope.update(payload)
    envelope["notices"] = list(notices) if notices is not None else []
    return envelope


def build_needs_decision(decision, notices=None):
    """Build a ``needs_decision`` envelope dict.

    ``decision`` is the payload from :func:`pipelib.decisions.needs_decision` (or an equivalent
    dict with the same four keys) — validated here again defensively: ``decision["code"]`` must be
    in :data:`pipelib.decisions.DECISION_CODES` and ``summary``/``context``/``options`` must be
    present, so a malformed decision object can never reach stdout even if a caller bypassed the
    builder. Returns architecture.md §3's exact shape:
    ``{"status": "needs_decision", "decision": {...}, "notices": [...]}``.
    """
    if not isinstance(decision, dict):
        raise TypeError("pipelib.envelope.build_needs_decision: decision must be a dict")
    code = decision.get("code")
    if code not in DECISION_CODES:
        raise ValueError(
            "pipelib.envelope.build_needs_decision: decision['code'] %r is not in the closed "
            "decision-code set" % (code,)
        )
    for required_key in ("summary", "context", "options"):
        if required_key not in decision:
            raise ValueError(
                "pipelib.envelope.build_needs_decision: decision is missing required key %r"
                % required_key
            )
    envelope = {
        "status": STATUS_NEEDS_DECISION,
        "decision": {
            "code": decision["code"],
            "summary": decision["summary"],
            "context": decision["context"],
            "options": decision["options"],
        },
    }
    envelope["notices"] = list(notices) if notices is not None else []
    return envelope


def emit(envelope, stream=None):
    """Write exactly one JSON object to ``stream`` (defaults to ``sys.stdout``) as a single line.

    This is the sole stdout write — every emitting script calls this (directly, or via
    :func:`emit_ok` / :func:`emit_needs_decision`) rather than hand-rolling ``json.dumps``/``print``,
    and it serializes through :func:`_serialize`, which :func:`write_envelope` shares, so "exactly
    one JSON envelope on stdout" is a property of the library, not a discipline every script must
    remember. Validates ``envelope["status"]`` is one of the two valid values before writing
    anything — an invalid status raises before a single byte reaches the stream, so a caller never
    receives a half-written or malformed envelope. UTF-8 is pinned on the write per
    architecture.md §1 (relevant on any stream that isn't already UTF-8-configured).
    """
    text = _serialize(envelope)
    target = stream if stream is not None else sys.stdout
    if hasattr(target, "buffer"):
        target.buffer.write(text.encode("utf-8"))
        target.buffer.flush()
    else:
        # A stream without a `.buffer` (e.g. a plain io.StringIO in a test) — write the str
        # directly rather than forcing a bytes encode/decode round-trip the caller didn't ask for.
        target.write(text)
        target.flush()


def _serialize(envelope):
    """The one envelope line, newline included, after the status check :func:`emit` documents —
    shared by :func:`emit` and :func:`write_envelope` so a file copy is byte-identical to stdout."""
    status = envelope.get("status")
    if status not in _VALID_STATUSES:
        raise ValueError(
            "pipelib.envelope: envelope['status'] must be one of %r, got %r"
            % (sorted(_VALID_STATUSES), status)
        )
    return json.dumps(envelope, ensure_ascii=False) + "\n"


def write_envelope(envelope, path):
    """Write ``envelope`` to ``path`` as exactly the line :func:`emit` writes, atomically.

    The text goes to a ``tempfile.mkstemp`` file in ``path``'s directory and is ``os.replace``d
    onto ``path``, so a concurrent reader sees the whole previous file or the whole new one, never
    part of either; the file keeps mkstemp's ``0600`` mode. A copy for a reader outside the
    pipeline (the coordinator prep's board feed, #116) — stdout stays the contract. Raises
    ``OSError`` to the caller, and removes the temp file when the write fails.
    """
    text = _serialize(envelope)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".envelope-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def emit_ok(payload=None, notices=None, stream=None):
    """Build (:func:`build_ok`) and :func:`emit` an ``ok`` envelope; returns the built dict.

    Callers that exit the process after emitting use :data:`EXIT_OK` as their exit code — this
    function does not call ``sys.exit`` itself, so a script controls its own process lifetime
    (relevant for scripts that emit then perform further non-stdout work, or for tests that call
    this in-process without wanting the test runner's own process to exit).
    """
    envelope = build_ok(payload=payload, notices=notices)
    emit(envelope, stream=stream)
    return envelope


def emit_needs_decision(decision, notices=None, stream=None):
    """Build (:func:`build_needs_decision`) and :func:`emit` a ``needs_decision`` envelope;
    returns the built dict. See :func:`emit_ok` for the exit-code note — same applies here:
    ``needs_decision`` is exit code 0 (architecture.md §3: "a valid outcome, not an error"), and
    this function does not call ``sys.exit``.
    """
    envelope = build_needs_decision(decision, notices=notices)
    emit(envelope, stream=stream)
    return envelope
