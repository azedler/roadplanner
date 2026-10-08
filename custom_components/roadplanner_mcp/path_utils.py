"""Safe path handling for Roadplanner configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath


class PathValidationError(ValueError):
    """Raised when a configured path escapes the Home Assistant config dir."""


def normalize_config_relative_path(
    config_dir: str | Path,
    value: str,
    *,
    disallow_www: bool = False,
) -> str:
    """Return a normalized POSIX path relative to the HA config directory."""
    if not isinstance(value, str) or not value.strip():
        raise PathValidationError("Pfad darf nicht leer sein")

    root = Path(config_dir).expanduser().resolve()
    candidate = Path(value.strip()).expanduser()
    absolute = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = absolute.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as err:
        raise PathValidationError(
            "Pfad muss innerhalb des Home-Assistant-Konfigurationsverzeichnisses "
            "liegen"
        ) from err

    if not relative.parts or relative == Path("."):
        raise PathValidationError(
            "Pfad darf nicht das Konfigurationsverzeichnis selbst sein"
        )
    if disallow_www and relative.parts[0].casefold() == "www":
        raise PathValidationError(
            "Private Roadplanner-Daten dürfen nicht im öffentlich erreichbaren "
            "www-Ordner liegen"
        )
    return relative.as_posix()


def resolve_config_path(config_dir: str | Path, relative_path: str) -> Path:
    """Resolve a previously validated config-relative path."""
    root = Path(config_dir).expanduser().resolve()
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as err:
        raise PathValidationError(
            "Konfigurierter Pfad liegt außerhalb von /config"
        ) from err
    return candidate


def normalize_paths(
    config_dir: str | Path,
    roadbook_value: str,
    backup_value: str,
    handoff_value: str,
) -> tuple[str, str, str]:
    """Validate canonical, backup, and handoff directories."""
    roadbook = normalize_config_relative_path(config_dir, roadbook_value)
    backup = normalize_config_relative_path(
        config_dir,
        backup_value,
        disallow_www=True,
    )
    handoff = normalize_config_relative_path(
        config_dir,
        handoff_value,
        disallow_www=True,
    )

    roadbook_parts = PurePosixPath(roadbook)
    backup_parts = PurePosixPath(backup)
    handoff_parts = PurePosixPath(handoff)
    if backup_parts == roadbook_parts or handoff_parts == roadbook_parts:
        raise PathValidationError("Private Verzeichnisse müssen getrennt sein")
    if backup_parts == handoff_parts:
        raise PathValidationError(
            "Sicherungs- und Übergabeverzeichnis müssen getrennt sein"
        )
    if backup_parts in handoff_parts.parents or handoff_parts in backup_parts.parents:
        raise PathValidationError(
            "Sicherungs- und Übergabeverzeichnis dürfen nicht ineinander liegen"
        )
    if backup_parts in roadbook_parts.parents or roadbook_parts in backup_parts.parents:
        raise PathValidationError(
            "Roadbook und Sicherungsverzeichnis dürfen nicht ineinander liegen"
        )
    if handoff_parts in roadbook_parts.parents or roadbook_parts in handoff_parts.parents:
        raise PathValidationError(
            "Roadbook und Übergabeverzeichnis dürfen nicht ineinander liegen"
        )
    return roadbook, backup, handoff


def media_library_roots(media_dirs: Mapping[str, str] | None) -> tuple[Path, ...]:
    """The resolved media directories a library is allowed to live in.

    Home Assistant hands these out as `hass.config.media_dirs` - by
    default `{"local": "/media"}`, but a user can name several. Asking
    Home Assistant rather than hard-coding "/media" is the point: a path
    is accepted because it is a configured media directory, not because
    it starts with the right six characters.
    """
    if not media_dirs:
        return ()
    roots: list[Path] = []
    for value in media_dirs.values():
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            resolved = Path(value.strip()).expanduser().resolve(strict=False)
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def normalize_library_path(
    config_dir: str | Path,
    value: str,
    *,
    media_dirs: Mapping[str, str] | None = None,
) -> str:
    """Normalize the generated-exports library path.

    Unlike every other Roadplanner directory, this one may sit OUTSIDE
    the config directory - in one of Home Assistant's media directories.
    The reason is the backup: `/config` goes into every Home Assistant
    backup and from there into OneDrive, and a few finished films are
    gigabytes of data that is already on disk, already regenerable, and
    has no business being copied to the cloud every night. `/media` is
    not backed up, which is exactly the property we want here.

    Two shapes come back, and the shape is the answer to "where does
    this live":

    - a **config-relative** posix path, as before, when the value is
      inside the config directory, and
    - an **absolute** posix path when it is inside a media directory.

    Anything else is rejected, so this stays a choice between two known
    roots and never becomes "any path on the host".
    """
    if not isinstance(value, str) or not value.strip():
        raise PathValidationError("Pfad darf nicht leer sein")

    root = Path(config_dir).expanduser().resolve()
    candidate = Path(value.strip()).expanduser()
    absolute = candidate if candidate.is_absolute() else root / candidate
    resolved = absolute.resolve(strict=False)

    try:
        relative = resolved.relative_to(root)
    except ValueError:
        relative = None

    if relative is not None:
        if not relative.parts or relative == Path("."):
            raise PathValidationError(
                "Pfad darf nicht das Konfigurationsverzeichnis selbst sein"
            )
        if relative.parts[0].casefold() == "www":
            raise PathValidationError(
                "Private Roadplanner-Daten dürfen nicht im öffentlich "
                "erreichbaren www-Ordner liegen"
            )
        return relative.as_posix()

    for media_root in media_library_roots(media_dirs):
        try:
            inside = resolved.relative_to(media_root)
        except ValueError:
            continue
        if not inside.parts or inside == Path("."):
            raise PathValidationError(
                "Pfad darf nicht das Medienverzeichnis selbst sein"
            )
        return resolved.as_posix()

    raise PathValidationError(
        "Pfad muss im Home-Assistant-Konfigurationsverzeichnis oder in einem "
        "Medienverzeichnis (z. B. /media) liegen"
    )


def resolve_library_path(
    config_dir: str | Path,
    stored_path: str,
    *,
    media_dirs: Mapping[str, str] | None = None,
) -> Path:
    """Resolve a stored library path to an absolute directory.

    Re-validates rather than trusting the stored string: options live in
    an editable storage file, and a media directory can be dropped from
    the configuration between two restarts. The one normalizer decides
    in both cases, so there is no second copy of "where may this live".
    """
    normalized = normalize_library_path(
        config_dir,
        stored_path,
        media_dirs=media_dirs,
    )
    if PurePosixPath(normalized).is_absolute():
        return Path(normalized)
    root = Path(config_dir).expanduser().resolve()
    return (root / normalized).resolve(strict=False)
