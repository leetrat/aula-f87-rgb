"""XDG profile locations and one-time migration from the old directory."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping, Optional

APP_DIR_NAME = "aula-f87-rgb"
MIGRATION_MARKER = ".legacy-profiles-migrated"


def xdg_config_home(
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Return the XDG configuration root, ignoring relative values."""

    env = os.environ if environ is None else environ
    configured = env.get("XDG_CONFIG_HOME", "")
    if configured:
        path = Path(configured).expanduser()
        if path.is_absolute():
            return path
    base_home = Path(home).expanduser() if home is not None else Path.home()
    return base_home / ".config"


def default_profile_dir(
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Return the profile directory, honouring the profile-dir override."""

    env = os.environ if environ is None else environ
    override = env.get("AULA_F87_PROFILE_DIR")
    if override:
        return Path(override).expanduser()
    return xdg_config_home(env, home) / APP_DIR_NAME


def legacy_profile_dir(home: Optional[Path] = None) -> Path:
    """Return the pre-XDG profile directory."""

    base_home = Path(home).expanduser() if home is not None else Path.home()
    return base_home / "aula-f87" / "profiles"


def migrate_legacy_profiles(target: Path, legacy: Path) -> list[Path]:
    """Copy legacy profiles into *target* once, without deleting the originals.

    A marker in the target directory makes this a one-time operation.  Existing
    files in the target are never overwritten, and failures to copy individual
    profiles do not stop the remaining ones or remove the legacy data.
    """

    target = Path(target)
    legacy = Path(legacy)
    if target == legacy:
        return []

    marker = target / MIGRATION_MARKER
    if marker.exists():
        return []

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return []

    copied: list[Path] = []
    try:
        sources = sorted(legacy.glob("*.json"))
    except OSError:
        sources = []

    for source in sources:
        destination = target / source.name
        if destination.exists():
            continue
        try:
            shutil.copy2(source, destination)
        except OSError:
            continue
        copied.append(destination)

    try:
        marker.touch()
    except OSError:
        pass
    return copied
