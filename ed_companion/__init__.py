"""Reusable application core for ED Engineering Companion (EDEC)."""

import os

# Release/build identity shown by the app and sent to optional integrations.
APP_VERSION = "21.198"

# Build pipelines may explicitly mark non-release artifacts. Installed builds
# and release tags use the stable default.
BUILD_CHANNEL = str(os.environ.get("EDEC_BUILD_CHANNEL") or "release").strip().casefold()


def is_development_build(channel=BUILD_CHANNEL):
    """Return INARA's development marker for the centralized build channel."""
    return str(channel or "release").strip().casefold() != "release"

# Python package metadata follows its own semantic-version line.
__version__ = "11.1.0"
