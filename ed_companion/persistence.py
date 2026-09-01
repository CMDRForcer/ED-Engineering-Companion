"""Collision-safe atomic writes for local EDEC persistence files."""

import os
import json
import re
import shutil
import tempfile
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


_WRITE_LOCK = threading.RLock()
_CORRUPT_JSON: dict[str, dict[str, str]] = {}
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
        if str(path.resolve()) in _CORRUPT_JSON:
            return False
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
    return True


def load_json_file(path, default, encoding="utf-8-sig"):
    """Load persistent JSON without converting corruption into writable state."""
    path = Path(path)
    resolved = str(path.resolve())
    try:
        text = path.read_text(encoding=encoding)
    except FileNotFoundError:
        with _WRITE_LOCK:
            _CORRUPT_JSON.pop(resolved, None)
        return deepcopy(default)
    except OSError as exc:
        return _protect_corrupt_json(path, default, type(exc).__name__)
    try:
        loaded = json.loads(text)
    except (ValueError, TypeError) as exc:
        return _protect_corrupt_json(path, default, type(exc).__name__)
    if default is not None and not isinstance(loaded, type(default)):
        return _protect_corrupt_json(path, default, "unexpected JSON root type")
    with _WRITE_LOCK:
        _CORRUPT_JSON.pop(resolved, None)
    return loaded


def _protect_corrupt_json(path, default, reason):
    path = Path(path)
    resolved = str(path.resolve())
    with _WRITE_LOCK:
        existing = _CORRUPT_JSON.get(resolved)
        if existing:
            return deepcopy(default)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            shutil.copy2(path, backup)
            backup_name = backup.name
        except OSError:
            backup_name = "backup unavailable"
        _CORRUPT_JSON[resolved] = {
            "path": resolved,
            "file": path.name,
            "backup": backup_name,
            "reason": str(reason),
        }
    return deepcopy(default)


def persistence_issues(directory=None):
    """Return privacy-safe corruption diagnostics, optionally below a root."""
    root = Path(directory).resolve() if directory is not None else None
    with _WRITE_LOCK:
        rows = list(_CORRUPT_JSON.values())
    if root is not None:
        rows = [
            row for row in rows
            if Path(row["path"]).is_relative_to(root)
        ]
    return [
        f"Persistent JSON unreadable: {row['file']} ({row['reason']}); "
        f"recovery copy: {row['backup']}; automatic writes blocked"
        for row in rows
    ]


def clear_persistence_errors():
    """Test/support hook; valid reloads normally clear individual entries."""
    with _WRITE_LOCK:
        _CORRUPT_JSON.clear()
