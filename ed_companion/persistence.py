"""Collision-safe atomic writes for local EDEC persistence files."""

import os
import re
import tempfile
import threading
import time
from pathlib import Path


_WRITE_LOCK = threading.RLock()
STALE_ATOMIC_TEMP_SECONDS = 60 * 60
_ATOMIC_TEMP_NAME = re.compile(
    r"^\..+\.(?:json|txt|log)\.[a-z0-9_]{8}\.tmp$"
)


def cleanup_stale_atomic_temps(
    directory, *, stale_after=STALE_ATOMIC_TEMP_SECONDS, now=None
):
    """Remove only old sibling temps created by :func:`atomic_write`."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    cutoff = float(time.time() if now is None else now) - max(
        0.0, float(stale_after)
    )
    removed = []
    try:
        candidates = list(directory.iterdir())
    except OSError:
        return removed
    for candidate in candidates:
        if not candidate.is_file() or not _ATOMIC_TEMP_NAME.fullmatch(candidate.name):
            continue
        try:
            if candidate.stat().st_mtime > cutoff:
                continue
            candidate.unlink()
            removed.append(candidate.name)
        except OSError:
            continue
    return removed


def atomic_write(path, text, encoding="utf-8"):
    """Flush one unique sibling temp file and atomically replace its target."""
    path = Path(path)
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding=encoding) as handle:
                descriptor = None
                handle.write(str(text))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
