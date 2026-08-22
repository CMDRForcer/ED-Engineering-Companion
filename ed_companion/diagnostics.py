"""Small, deterministic filters for user-facing diagnostics."""

from ed_companion.persistence import atomic_write


BENIGN_QT_MESSAGE_FRAGMENTS = (
    "retrying to obtain clipboard",
    "qxgivsyncservice not destroyed in time",
    "qeventdispatcherwin32::wakeup: failed to post a message",
)


def is_benign_qt_message(message):
    folded = str(message or "").casefold()
    return any(
        fragment in folded for fragment in BENIGN_QT_MESSAGE_FRAGMENTS
    )


def filtered_log_lines(lines):
    return [
        str(line) for line in (lines or [])
        if not is_benign_qt_message(line)
    ]


def clean_diagnostic_log(path):
    """Remove known noise already stored by older releases."""
    try:
        original = path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return False
    cleaned = filtered_log_lines(original)
    if cleaned == original:
        return False
    try:
        atomic_write(
            path,
            "\n".join(cleaned) + ("\n" if cleaned else ""),
        )
    except OSError:
        return False
    return True
