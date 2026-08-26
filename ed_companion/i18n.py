"""Small, deterministic translation catalog for the QML interface."""

import json
from pathlib import Path


SUPPORTED_LANGUAGES = ("en", "de", "es", "fr")
DEFAULT_LANGUAGE = "en"


class TranslationCatalog:
    """Load flat JSON catalogs and always fall back to English, then source text."""

    def __init__(self, directory: Path):
        self._catalogs = {}
        for language in SUPPORTED_LANGUAGES:
            path = Path(directory) / f"{language}.json"
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                value = {}
            self._catalogs[language] = {
                str(key): str(text)
                for key, text in value.items()
                if isinstance(key, str) and isinstance(text, str)
            } if isinstance(value, dict) else {}

    def translate(self, language: str, key: str, fallback: str = "") -> str:
        language = str(language or "").casefold()
        key = str(key or "")
        local = self._catalogs.get(language, {})
        english = self._catalogs.get(DEFAULT_LANGUAGE, {})
        return local.get(key) or english.get(key) or str(fallback or key)
