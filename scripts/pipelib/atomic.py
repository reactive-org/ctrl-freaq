"""Atomic text-file writes (#183): the one temp-file-then-rename sequence the scripts share.

:func:`write_text` writes to a ``tempfile.mkstemp`` file in the target's directory and
``os.replace``s it onto the target, so a concurrent reader sees the whole previous file or the whole
new one, never part of either; the file keeps mkstemp's ``0600`` mode. Its callers are
:func:`pipelib.envelope.write_envelope` and ``board_history.save_cache``; each serializes its own
text and passes its own temp-file prefix.
"""

import os
import tempfile


def write_text(path, text, prefix):
    """Write ``text`` to ``path`` (a ``str`` or path-like) as UTF-8 bytes, atomically.

    The temp file is named ``<prefix><random>.tmp`` in ``path``'s directory. The write is binary, so no
    platform newline translation touches ``text``. Raises ``OSError`` (or whatever else the write
    raised) to the caller unwrapped, after removing the temp file.
    """
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
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
