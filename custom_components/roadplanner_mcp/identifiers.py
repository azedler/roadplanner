"""ID generation and identifier validation for Roadplanner's canonical store."""

from __future__ import annotations

import json
import re
from typing import Any
import uuid

from .json_io import ValidationError

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _stable_id(prefix: str, value: Any, index: int = 0) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    name = f"roadplanner:{prefix}:{index}:{material}"
    return f"{prefix}-{uuid.uuid5(uuid.NAMESPACE_URL, name).hex[:12]}"


def validate_identifier(value: Any, field_name: str) -> str:
    """Validate IDs and slugs used in filenames."""
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            f"'{field_name}' darf nur Buchstaben, Zahlen, '_' und '-' enthalten "
            "und muss 1 bis 128 Zeichen lang sein"
        )
    return value.strip()
