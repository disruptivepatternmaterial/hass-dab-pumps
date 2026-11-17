"""Provides diagnostics for custom component."""

import logging

from dataclasses import fields, is_dataclass
from datetime import datetime
from multidict import MultiDict, MultiDictProxy
from types import MappingProxyType, NoneType
from typing import Any, Mapping

from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from pydabpumps import (
    to_dict,
)

from .const import (
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    DIAGNOSTICS_REDACT,
)
from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator,
)


_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(hass: HomeAssistant, config_entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    install_id = config_entry.data[CONF_INSTALL_ID]
    install_name = config_entry.data[CONF_INSTALL_NAME]
    _LOGGER.info(f"Retrieve diagnostics for install {install_name} ({install_id})")
    
    coordinator: DabPumpsCoordinator = DabPumpsCoordinatorFactory.create(hass, config_entry)
    diagnostics = {
        "config": {
            "data": config_entry.data,
            "options": config_entry.options,
        },
        "coordinator": await coordinator.async_get_diagnostics(),
        "api": await coordinator._api.async_get_diagnostics(), 
    }

    # Convert contents to only contain standard structures: int, float, str, list, dict, ...
    diagnostics_dict = to_dict(diagnostics)

    # Hide passwords etc.
    return async_redact_data(diagnostics_dict, DIAGNOSTICS_REDACT)

