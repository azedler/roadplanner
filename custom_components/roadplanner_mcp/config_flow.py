"""Config and options flows for Roadplanner."""

from __future__ import annotations

import secrets
from pathlib import PurePosixPath
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    CONFIG_ENTRY_VERSION,
    CONF_ARCHIVE_PATH,
    CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY,
    CONF_ASSISTANT_AUTONOMY_LEVEL,
    CONF_ASSISTANT_COPILOT_AUTO_BRIEFING,
    CONF_ASSISTANT_COPILOT_ENABLED,
    CONF_ASSISTANT_DEBUG_ENABLED,
    CONF_ASSISTANT_ENABLE_RESEARCH,
    CONF_ASSISTANT_MAX_HISTORY,
    CONF_ASSISTANT_MAX_QUEUE,
    CONF_ASSISTANT_MIN_REQUEST_INTERVAL,
    CONF_ASSISTANT_PROVIDER,
    CONF_ASSISTANT_REQUEST_TIMEOUT,
    CONF_ASSISTANT_RETRY_ATTEMPTS,
    CONF_AUTO_APPLY_CHANGESETS,
    CONF_AUTO_SCAN_HANDOFFS,
    CONF_BACKUP_COUNT,
    CONF_BACKUP_PATH,
    CONF_ENABLE_HANDOFF_WEBHOOK,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_FALLBACK_MODEL,
    CONF_GEMINI_MODEL,
    CONF_GEOCODING_ENABLED,
    CONF_GEOCODING_URL,
    CONF_ROUTING_ENABLED,
    CONF_ROUTING_PROVIDER,
    CONF_ROUTING_URL,
    CONF_ROUTING_PROFILE,
    CONF_ROUTING_REQUEST_TIMEOUT,
    CONF_ROUTING_MIN_REQUEST_INTERVAL,
    CONF_DOCUMENT_MAX_UPLOAD_MB,
    CONF_DOCUMENT_ANALYSIS_ENABLED,
    CONF_DEFAULT_CURRENCY,
    CONF_HANDOFF_PATH,
    CONF_NON_ADMIN_ROLE,
    CONF_REFRESH_INTERVAL,
    CONF_ROADBOOK_PATH,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_TOKEN,
    DEFAULT_ARCHIVE_PATH,
    DEFAULT_ALLOW_DESTRUCTIVE_AUTO_APPLY,
    ASSISTANT_AUTONOMY_LEVELS,
    DEFAULT_ASSISTANT_AUTONOMY_LEVEL,
    DEFAULT_ASSISTANT_COPILOT_AUTO_BRIEFING,
    DEFAULT_ASSISTANT_COPILOT_ENABLED,
    DEFAULT_ASSISTANT_DEBUG_ENABLED,
    DEFAULT_ASSISTANT_ENABLE_RESEARCH,
    DEFAULT_ASSISTANT_MAX_HISTORY,
    DEFAULT_ASSISTANT_MAX_QUEUE,
    DEFAULT_ASSISTANT_MIN_REQUEST_INTERVAL,
    DEFAULT_ASSISTANT_PROVIDER,
    DEFAULT_ASSISTANT_REQUEST_TIMEOUT,
    DEFAULT_ASSISTANT_RETRY_ATTEMPTS,
    DEFAULT_AUTO_APPLY_CHANGESETS,
    DEFAULT_AUTO_SCAN_HANDOFFS,
    DEFAULT_BACKUP_COUNT,
    DEFAULT_BACKUP_PATH,
    DEFAULT_ENABLE_HANDOFF_WEBHOOK,
    DEFAULT_GEMINI_API_KEY,
    DEFAULT_GEMINI_FALLBACK_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEOCODING_ENABLED,
    DEFAULT_GEOCODING_URL,
    DEFAULT_ROUTING_ENABLED,
    DEFAULT_ROUTING_PROVIDER,
    DEFAULT_ROUTING_URL,
    DEFAULT_ROUTING_PROFILE,
    DEFAULT_ROUTING_REQUEST_TIMEOUT,
    DEFAULT_ROUTING_MIN_REQUEST_INTERVAL,
    DEFAULT_DOCUMENT_MAX_UPLOAD_MB,
    DEFAULT_DOCUMENT_ANALYSIS_ENABLED,
    DEFAULT_DEFAULT_CURRENCY,
    DEFAULT_HANDOFF_PATH,
    DEFAULT_NON_ADMIN_ROLE,
    DEFAULT_REFRESH_INTERVAL,
    DEFAULT_ROADBOOK_PATH,
    DOMAIN,
    MAX_ASSISTANT_MAX_HISTORY,
    MAX_ASSISTANT_MAX_QUEUE,
    MAX_ASSISTANT_MIN_REQUEST_INTERVAL,
    MAX_ASSISTANT_REQUEST_TIMEOUT,
    MAX_ASSISTANT_RETRY_ATTEMPTS,
    MAX_REFRESH_INTERVAL,
    MIN_DOCUMENT_MAX_UPLOAD_MB,
    MAX_DOCUMENT_MAX_UPLOAD_MB,
    MIN_ROUTING_REQUEST_TIMEOUT,
    MAX_ROUTING_REQUEST_TIMEOUT,
    MIN_ROUTING_MIN_REQUEST_INTERVAL,
    MAX_ROUTING_MIN_REQUEST_INTERVAL,
    MIN_ASSISTANT_MAX_HISTORY,
    MIN_ASSISTANT_MAX_QUEUE,
    MIN_ASSISTANT_MIN_REQUEST_INTERVAL,
    MIN_ASSISTANT_REQUEST_TIMEOUT,
    MIN_ASSISTANT_RETRY_ATTEMPTS,
    MIN_REFRESH_INTERVAL,
    NAME,
    NON_ADMIN_ROLES,
)
from .geocoding import GeocodingUrlValidationError, normalize_geocoding_url
from .routing import (
    RoutingUrlValidationError,
    normalize_routing_profile,
    normalize_routing_url,
)
from .path_utils import (
    PathValidationError,
    normalize_config_relative_path,
    normalize_paths,
)


def get_effective_options(entry: ConfigEntry) -> dict[str, Any]:
    """Merge immutable secrets and editable options."""
    return {**entry.data, **entry.options}


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Return the shared initial/options schema.

    The API key deliberately has no default so Home Assistant never renders the
    stored secret back into the browser. Leaving it empty keeps the existing key.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_ROADBOOK_PATH,
                default=defaults.get(CONF_ROADBOOK_PATH, DEFAULT_ROADBOOK_PATH),
            ): str,
            vol.Required(
                CONF_BACKUP_PATH,
                default=defaults.get(CONF_BACKUP_PATH, DEFAULT_BACKUP_PATH),
            ): str,
            vol.Required(
                CONF_HANDOFF_PATH,
                default=defaults.get(CONF_HANDOFF_PATH, DEFAULT_HANDOFF_PATH),
            ): str,
            vol.Required(
                CONF_ARCHIVE_PATH,
                default=defaults.get(CONF_ARCHIVE_PATH, DEFAULT_ARCHIVE_PATH),
            ): str,
            vol.Required(
                CONF_BACKUP_COUNT,
                default=defaults.get(CONF_BACKUP_COUNT, DEFAULT_BACKUP_COUNT),
            ): vol.All(int, vol.Range(min=1, max=500)),
            vol.Required(
                CONF_REFRESH_INTERVAL,
                default=defaults.get(
                    CONF_REFRESH_INTERVAL,
                    DEFAULT_REFRESH_INTERVAL,
                ),
            ): vol.All(
                int,
                vol.Range(min=MIN_REFRESH_INTERVAL, max=MAX_REFRESH_INTERVAL),
            ),
            vol.Required(
                CONF_AUTO_SCAN_HANDOFFS,
                default=defaults.get(
                    CONF_AUTO_SCAN_HANDOFFS,
                    DEFAULT_AUTO_SCAN_HANDOFFS,
                ),
            ): bool,
            vol.Required(
                CONF_AUTO_APPLY_CHANGESETS,
                default=defaults.get(
                    CONF_AUTO_APPLY_CHANGESETS,
                    DEFAULT_AUTO_APPLY_CHANGESETS,
                ),
            ): bool,
            vol.Required(
                CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY,
                default=defaults.get(
                    CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY,
                    DEFAULT_ALLOW_DESTRUCTIVE_AUTO_APPLY,
                ),
            ): bool,
            vol.Required(
                CONF_ENABLE_HANDOFF_WEBHOOK,
                default=defaults.get(
                    CONF_ENABLE_HANDOFF_WEBHOOK,
                    DEFAULT_ENABLE_HANDOFF_WEBHOOK,
                ),
            ): bool,
            vol.Required(
                CONF_NON_ADMIN_ROLE,
                default=defaults.get(
                    CONF_NON_ADMIN_ROLE,
                    DEFAULT_NON_ADMIN_ROLE,
                ),
            ): vol.In(NON_ADMIN_ROLES),
            vol.Required(
                CONF_ASSISTANT_PROVIDER,
                default=defaults.get(
                    CONF_ASSISTANT_PROVIDER,
                    DEFAULT_ASSISTANT_PROVIDER,
                ),
            ): vol.In(("gemini",)),
            vol.Optional(CONF_GEMINI_API_KEY): str,
            vol.Required(
                CONF_GEMINI_MODEL,
                default=defaults.get(
                    CONF_GEMINI_MODEL,
                    DEFAULT_GEMINI_MODEL,
                ),
            ): str,
            vol.Required(
                CONF_GEMINI_FALLBACK_MODEL,
                default=defaults.get(
                    CONF_GEMINI_FALLBACK_MODEL,
                    DEFAULT_GEMINI_FALLBACK_MODEL,
                ),
            ): str,
            vol.Required(
                CONF_ASSISTANT_REQUEST_TIMEOUT,
                default=defaults.get(
                    CONF_ASSISTANT_REQUEST_TIMEOUT,
                    DEFAULT_ASSISTANT_REQUEST_TIMEOUT,
                ),
            ): vol.All(
                int,
                vol.Range(
                    min=MIN_ASSISTANT_REQUEST_TIMEOUT,
                    max=MAX_ASSISTANT_REQUEST_TIMEOUT,
                ),
            ),
            vol.Required(
                CONF_ASSISTANT_RETRY_ATTEMPTS,
                default=defaults.get(
                    CONF_ASSISTANT_RETRY_ATTEMPTS,
                    DEFAULT_ASSISTANT_RETRY_ATTEMPTS,
                ),
            ): vol.All(
                int,
                vol.Range(
                    min=MIN_ASSISTANT_RETRY_ATTEMPTS,
                    max=MAX_ASSISTANT_RETRY_ATTEMPTS,
                ),
            ),
            vol.Required(
                CONF_ASSISTANT_MIN_REQUEST_INTERVAL,
                default=defaults.get(
                    CONF_ASSISTANT_MIN_REQUEST_INTERVAL,
                    DEFAULT_ASSISTANT_MIN_REQUEST_INTERVAL,
                ),
            ): vol.All(
                vol.Coerce(float),
                vol.Range(
                    min=MIN_ASSISTANT_MIN_REQUEST_INTERVAL,
                    max=MAX_ASSISTANT_MIN_REQUEST_INTERVAL,
                ),
            ),
            vol.Required(
                CONF_ASSISTANT_MAX_QUEUE,
                default=defaults.get(
                    CONF_ASSISTANT_MAX_QUEUE,
                    DEFAULT_ASSISTANT_MAX_QUEUE,
                ),
            ): vol.All(
                int,
                vol.Range(
                    min=MIN_ASSISTANT_MAX_QUEUE,
                    max=MAX_ASSISTANT_MAX_QUEUE,
                ),
            ),
            vol.Required(
                CONF_ASSISTANT_AUTONOMY_LEVEL,
                default=defaults.get(
                    CONF_ASSISTANT_AUTONOMY_LEVEL,
                    DEFAULT_ASSISTANT_AUTONOMY_LEVEL,
                ),
            ): vol.In(ASSISTANT_AUTONOMY_LEVELS),
            vol.Required(
                CONF_ASSISTANT_COPILOT_ENABLED,
                default=defaults.get(
                    CONF_ASSISTANT_COPILOT_ENABLED,
                    DEFAULT_ASSISTANT_COPILOT_ENABLED,
                ),
            ): bool,
            vol.Required(
                CONF_ASSISTANT_COPILOT_AUTO_BRIEFING,
                default=defaults.get(
                    CONF_ASSISTANT_COPILOT_AUTO_BRIEFING,
                    DEFAULT_ASSISTANT_COPILOT_AUTO_BRIEFING,
                ),
            ): bool,
            vol.Required(
                CONF_ASSISTANT_DEBUG_ENABLED,
                default=defaults.get(
                    CONF_ASSISTANT_DEBUG_ENABLED,
                    DEFAULT_ASSISTANT_DEBUG_ENABLED,
                ),
            ): bool,
            vol.Required(
                CONF_ASSISTANT_ENABLE_RESEARCH,
                default=defaults.get(
                    CONF_ASSISTANT_ENABLE_RESEARCH,
                    DEFAULT_ASSISTANT_ENABLE_RESEARCH,
                ),
            ): bool,
            vol.Required(
                CONF_ASSISTANT_MAX_HISTORY,
                default=defaults.get(
                    CONF_ASSISTANT_MAX_HISTORY,
                    DEFAULT_ASSISTANT_MAX_HISTORY,
                ),
            ): vol.All(
                int,
                vol.Range(
                    min=MIN_ASSISTANT_MAX_HISTORY,
                    max=MAX_ASSISTANT_MAX_HISTORY,
                ),
            ),
            vol.Required(
                CONF_GEOCODING_ENABLED,
                default=defaults.get(
                    CONF_GEOCODING_ENABLED,
                    DEFAULT_GEOCODING_ENABLED,
                ),
            ): bool,
            vol.Required(
                CONF_GEOCODING_URL,
                default=defaults.get(
                    CONF_GEOCODING_URL,
                    DEFAULT_GEOCODING_URL,
                ),
            ): str,
            vol.Required(
                CONF_ROUTING_ENABLED,
                default=defaults.get(
                    CONF_ROUTING_ENABLED,
                    DEFAULT_ROUTING_ENABLED,
                ),
            ): bool,
            vol.Required(
                CONF_ROUTING_PROVIDER,
                default=defaults.get(
                    CONF_ROUTING_PROVIDER,
                    DEFAULT_ROUTING_PROVIDER,
                ),
            ): vol.In(("osrm",)),
            vol.Required(
                CONF_ROUTING_URL,
                default=defaults.get(
                    CONF_ROUTING_URL,
                    DEFAULT_ROUTING_URL,
                ),
            ): str,
            vol.Required(
                CONF_ROUTING_PROFILE,
                default=defaults.get(
                    CONF_ROUTING_PROFILE,
                    DEFAULT_ROUTING_PROFILE,
                ),
            ): str,
            vol.Required(
                CONF_ROUTING_REQUEST_TIMEOUT,
                default=defaults.get(
                    CONF_ROUTING_REQUEST_TIMEOUT,
                    DEFAULT_ROUTING_REQUEST_TIMEOUT,
                ),
            ): vol.All(
                int,
                vol.Range(
                    min=MIN_ROUTING_REQUEST_TIMEOUT,
                    max=MAX_ROUTING_REQUEST_TIMEOUT,
                ),
            ),
            vol.Required(
                CONF_ROUTING_MIN_REQUEST_INTERVAL,
                default=defaults.get(
                    CONF_ROUTING_MIN_REQUEST_INTERVAL,
                    DEFAULT_ROUTING_MIN_REQUEST_INTERVAL,
                ),
            ): vol.All(
                vol.Coerce(float),
                vol.Range(
                    min=MIN_ROUTING_MIN_REQUEST_INTERVAL,
                    max=MAX_ROUTING_MIN_REQUEST_INTERVAL,
                ),
            ),
            vol.Required(
                CONF_DOCUMENT_MAX_UPLOAD_MB,
                default=defaults.get(
                    CONF_DOCUMENT_MAX_UPLOAD_MB,
                    DEFAULT_DOCUMENT_MAX_UPLOAD_MB,
                ),
            ): vol.All(
                int,
                vol.Range(
                    min=MIN_DOCUMENT_MAX_UPLOAD_MB,
                    max=MAX_DOCUMENT_MAX_UPLOAD_MB,
                ),
            ),
            vol.Required(
                CONF_DOCUMENT_ANALYSIS_ENABLED,
                default=defaults.get(
                    CONF_DOCUMENT_ANALYSIS_ENABLED,
                    DEFAULT_DOCUMENT_ANALYSIS_ENABLED,
                ),
            ): bool,
            vol.Required(
                CONF_DEFAULT_CURRENCY,
                default=defaults.get(
                    CONF_DEFAULT_CURRENCY,
                    DEFAULT_DEFAULT_CURRENCY,
                ),
            ): str,
        }
    )


def _normalize_input(
    config_dir: str,
    user_input: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(user_input)
    submitted_key = str(result.get(CONF_GEMINI_API_KEY, "")).strip()
    if submitted_key:
        result[CONF_GEMINI_API_KEY] = submitted_key
    elif current and current.get(CONF_GEMINI_API_KEY):
        result[CONF_GEMINI_API_KEY] = current[CONF_GEMINI_API_KEY]
    else:
        result[CONF_GEMINI_API_KEY] = DEFAULT_GEMINI_API_KEY
    result[CONF_GEMINI_MODEL] = (
        str(result.get(CONF_GEMINI_MODEL) or DEFAULT_GEMINI_MODEL).strip()
        or DEFAULT_GEMINI_MODEL
    )
    result[CONF_GEMINI_FALLBACK_MODEL] = str(
        result.get(CONF_GEMINI_FALLBACK_MODEL, DEFAULT_GEMINI_FALLBACK_MODEL)
        or ""
    ).strip()
    if result[CONF_GEMINI_FALLBACK_MODEL] == result[CONF_GEMINI_MODEL]:
        result[CONF_GEMINI_FALLBACK_MODEL] = ""
    if not result.get(CONF_ASSISTANT_COPILOT_ENABLED):
        result[CONF_ASSISTANT_COPILOT_AUTO_BRIEFING] = False
    result[CONF_GEOCODING_URL] = normalize_geocoding_url(
        result.get(CONF_GEOCODING_URL, DEFAULT_GEOCODING_URL)
    )
    result[CONF_ROUTING_URL] = normalize_routing_url(
        result.get(CONF_ROUTING_URL, DEFAULT_ROUTING_URL)
    )
    result[CONF_ROUTING_PROFILE] = normalize_routing_profile(
        result.get(CONF_ROUTING_PROFILE, DEFAULT_ROUTING_PROFILE)
    )

    roadbook, backup, handoff = normalize_paths(
        config_dir,
        user_input[CONF_ROADBOOK_PATH],
        user_input[CONF_BACKUP_PATH],
        user_input[CONF_HANDOFF_PATH],
    )
    result[CONF_ROADBOOK_PATH] = roadbook
    result[CONF_BACKUP_PATH] = backup
    result[CONF_HANDOFF_PATH] = handoff
    archive = normalize_config_relative_path(
        config_dir,
        user_input[CONF_ARCHIVE_PATH],
        disallow_www=True,
    )
    archive_path = PurePosixPath(archive)
    other_paths = tuple(PurePosixPath(item) for item in (roadbook, backup, handoff))
    if archive_path in other_paths:
        raise PathValidationError("Archivverzeichnis muss getrennt sein")
    if any(archive_path in other.parents or other in archive_path.parents for other in other_paths):
        raise PathValidationError(
            "Archivverzeichnis darf nicht in einem anderen Roadplanner-Verzeichnis liegen"
        )
    result[CONF_ARCHIVE_PATH] = archive
    currency = str(
        result.get(CONF_DEFAULT_CURRENCY) or DEFAULT_DEFAULT_CURRENCY
    ).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("invalid_currency")
    result[CONF_DEFAULT_CURRENCY] = currency
    if (
        result[CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY]
        and not result[CONF_AUTO_APPLY_CHANGESETS]
    ):
        result[CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY] = False
    return result


class RoadplannerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single Roadplanner configuration entry."""

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = _normalize_input(self.hass.config.config_dir, user_input)
            except PathValidationError:
                errors["base"] = "invalid_path"
            except GeocodingUrlValidationError:
                errors["base"] = "invalid_geocoding_url"
            except RoutingUrlValidationError:
                errors["base"] = "invalid_routing_url"
            except ValueError as err:
                if str(err) == "invalid_currency":
                    errors["base"] = "invalid_currency"
                else:
                    raise
            else:
                data[CONF_WEBHOOK_ID] = secrets.token_hex(32)
                data[CONF_WEBHOOK_TOKEN] = secrets.token_urlsafe(48)
                return self.async_create_entry(title=NAME, data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RoadplannerOptionsFlow()


class RoadplannerOptionsFlow(OptionsFlow):
    """Edit paths, assistant, folder automation, and panel access."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = get_effective_options(self.config_entry)
        if user_input is not None:
            try:
                options = _normalize_input(
                    self.hass.config.config_dir,
                    user_input,
                    current=current,
                )
            except PathValidationError:
                errors["base"] = "invalid_path"
            except GeocodingUrlValidationError:
                errors["base"] = "invalid_geocoding_url"
            except RoutingUrlValidationError:
                errors["base"] = "invalid_routing_url"
            except ValueError as err:
                if str(err) == "invalid_currency":
                    errors["base"] = "invalid_currency"
                else:
                    raise
            else:
                return self.async_create_entry(title="", data=options)
        defaults = dict(user_input or current)
        defaults.pop(CONF_GEMINI_API_KEY, None)
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(defaults),
            errors=errors,
        )
