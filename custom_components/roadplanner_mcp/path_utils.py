"""Safe path handling for Roadplanner configuration."""

from __future__ import annotations

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
