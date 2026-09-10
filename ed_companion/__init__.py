"""Reusable application core for ED Engineering Companion (EDEC)."""

import os
import sys

# Release/build identity shown by the app and sent to optional integrations.
APP_VERSION = "1.1.1"


def default_build_channel(env_value=None, *, frozen=None):
    """Resolve the build channel.

    An explicit ``EDEC_BUILD_CHANNEL`` always wins. A packaged (frozen) build
    is a release. A bare source checkout is a development build, so integrations
    are not told that ad-hoc runs are the released ``APP_VERSION``.
    """
    if env_value:
        return str(env_value).strip().casefold()
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    return "release" if frozen else "development"


BUILD_CHANNEL = default_build_channel(os.environ.get("EDEC_BUILD_CHANNEL"))


def is_development_build(channel=BUILD_CHANNEL):
    """Return INARA's development marker for the centralized build channel."""
    return str(channel or "release").strip().casefold() != "release"

# Python package metadata follows its own semantic-version line.
__version__ = "11.1.0"
