"""Engineer portrait path helpers (UI assets only)."""

from pathlib import Path


def engineer_portrait_slug(name: str) -> str:
    aliases = {
        "palin": "professor_palin",
        "professor palin": "professor_palin",
        "bris dekker": "colonel_bris_dekker",
        "colonel bris dekker": "colonel_bris_dekker",
        "sarge": "the_sarge",
        "the sarge": "the_sarge",
        "dweller": "the_dweller",
        "the dweller": "the_dweller",
    }
    key = str(name or "").strip().casefold()
    if key in aliases:
        return aliases[key]
    return (
        key.replace(".", "")
        .replace("'", "")
        .replace("-", "_")
        .replace(" ", "_")
    )


def engineer_portrait_url(app_root: Path, name: str) -> str:
    path = app_root / "assets" / "engineers" / f"{engineer_portrait_slug(name)}.jpg"
    if path.is_file():
        return path.resolve().as_uri()
    return ""
