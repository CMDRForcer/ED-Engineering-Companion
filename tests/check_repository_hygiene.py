from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BLOCKED_NAMES = {
    "cargo.json",
    "community_upload_queue.json",
    "eddn_config.json",
    "eddn_journal_cursor.json",
    "inara_config.json",
    "inara_journal_cache.json",
    "inara_receipts.json",
    "journal_path.txt",
    "market.json",
    "modulesinfo.json",
    "navroute.json",
    "outfitting.json",
    "phase14.log",
    "phase14_graphics.json",
    "session_history.json",
    "shipyard.json",
    "status.json",
}
BLOCKED_SUFFIXES = {".bak", ".crash", ".key", ".log", ".p12", ".pem", ".pfx", ".pyc", ".tmp", ".zip"}
SECRET_PATTERNS = {
    "absolute Windows user path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+", re.IGNORECASE),
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub personal token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
TEXT_SUFFIXES = {"", ".bat", ".json", ".md", ".py", ".qml", ".spec", ".txt", ".yml", ".yaml"}


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    return [Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value]


def main():
    failures = []
    for relative in tracked_files():
        folded_parts = [part.casefold() for part in relative.parts]
        name = relative.name.casefold()
        if any(part == "__pycache__" or part.startswith("profile-") for part in folded_parts):
            failures.append(f"runtime directory tracked: {relative.as_posix()}")
        if name in BLOCKED_NAMES or relative.suffix.casefold() in BLOCKED_SUFFIXES:
            failures.append(f"runtime/generated file tracked: {relative.as_posix()}")
        path = ROOT / relative
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{label}: {relative.as_posix()}")
    if failures:
        print("Repository hygiene check failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("Repository hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
