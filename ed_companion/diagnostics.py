"""Small, deterministic filters for user-facing diagnostics."""

from ed_companion.persistence import atomic_write


BENIGN_QT_MESSAGE_FRAGMENTS = (
    "retrying to obtain clipboard",
    "qxgivsyncservice not destroyed in time",
    "qeventdispatcherwin32::wakeup: failed to post a message",
)
INCUBATION_TEARDOWN_FRAGMENT = "object or context destroyed during incubation"
INCUBATION_DELEGATE_FRAGMENT = "qml component: cannot create delegate"


def is_benign_qt_message(
    message, previous_message="", incubation_teardown_recent=False,
    next_message="",
):
    folded = str(message or "").casefold()
    if any(
        fragment in folded for fragment in BENIGN_QT_MESSAGE_FRAGMENTS
    ):
        return True
    if INCUBATION_TEARDOWN_FRAGMENT in folded:
        return True
    if INCUBATION_DELEGATE_FRAGMENT not in folded:
        return False
    # Qt pairs a delegate-creation failure with the teardown message for
    # one single benign event - a lazily-unloaded page's ListView aborting
    # an in-flight delegate incubation - but does not guarantee which of
    # the two it emits first; observed order is the delegate failure
    # first, immediately followed by the teardown message, as often as
    # the reverse. On its own (paired with neither), it remains
    # actionable and must reach diagnostics and the QML smoke test.
    return (
        incubation_teardown_recent
        or INCUBATION_TEARDOWN_FRAGMENT in str(previous_message or "").casefold()
        or INCUBATION_TEARDOWN_FRAGMENT in str(next_message or "").casefold()
    )


def filtered_log_lines(lines):
    lines = [str(line) for line in lines or []]
    result = []
    for index, value in enumerate(lines):
        previous = lines[index - 1] if index > 0 else ""
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if not is_benign_qt_message(value, previous, next_message=next_line):
            result.append(value)
    return result


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
