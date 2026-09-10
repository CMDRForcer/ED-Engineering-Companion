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
    message, previous_message="", incubation_teardown_recent=False
):
    folded = str(message or "").casefold()
    if any(
        fragment in folded for fragment in BENIGN_QT_MESSAGE_FRAGMENTS
    ):
        return True
    if INCUBATION_TEARDOWN_FRAGMENT in folded:
        return True
    # Qt emits this immediately after the teardown message when a lazy page is
    # destroyed while its ListView is still incubating. On its own it remains
    # actionable and must reach diagnostics and the QML smoke test.
    return (
        INCUBATION_DELEGATE_FRAGMENT in folded
        and (
            incubation_teardown_recent
            or INCUBATION_TEARDOWN_FRAGMENT
            in str(previous_message or "").casefold()
        )
    )


def filtered_log_lines(lines):
    result = []
    previous = ""
    for line in lines or []:
        value = str(line)
        if not is_benign_qt_message(value, previous):
            result.append(value)
        previous = value
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
