"""config_flow.py: Config flow for DAB Pumps integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult

from homeassistant.core import callback
from homeassistant.components.diagnostics.util import async_redact_data
from homeassistant.helpers.selector import selector


from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LANGUAGE,
)

from pydabpumps import (
    DabPumpsInstall,
    DabPumpsUserRole,
    DabPumpsError,
    DabPumpsAuthError,
) 

from .const import (
    DIAGNOSTICS_REDACT,
    DOMAIN,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_LANGUAGE,
    CONF_INSTALL_ID,
    CONF_INSTALL_NAME,
    MSG_LANGUAGE,
    LANGUAGE_MAP,
    LANGUAGE_AUTO,
    LANGUAGE_AUTO_FALLBACK,
    LANGUAGE_TEXT_AUTO,
    LANGUAGE_TEXT_FALLBACK,
)
from .coordinator import (
    DabPumpsCoordinatorFactory,
    DabPumpsCoordinator,
)

_LOGGER = logging.getLogger(__name__)

# internal consts only used in config flow
CONF_ROLE_MENU = "role_menu"
ROLE_MENU_KEEP = "keep_role"
ROLE_MENU_UPDATE = "update_role"

# Error keys; must exist under config.error in strings.json / translations
ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_NO_INSTALLATIONS = "no_installations"
ERROR_ROLE_UPDATE_FAILED = "role_update_failed"
ERROR_UNKNOWN = "unknown"


class ConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""
    
    VERSION = 1
    
    def __init__(self):
        """Initialize config flow."""
        self._username: str = None
        self._password: str = None
        self._install_map: dict[str,DabPumpsInstall] = {}
        self._install_id: str = None
        self._install_name: str = None
        self._errors: dict[str,str] = {}
        self._coordinator: DabPumpsCoordinator = None

        # Assign the HA configured log level of this module to the v module
        log_level: int = _LOGGER.getEffectiveLevel()
        logging.getLogger("pydabpumps").setLevel(log_level)

        _LOGGER.info(f"Logging at {logging.getLevelName(log_level)}")
    
    
    async def async_try_connection(self):
        """Test the username and password by connecting to the DConnect website"""
        _LOGGER.info("Trying connection...")
        
        self._errors = {}
        self._coordinator = DabPumpsCoordinatorFactory.create_temp(self.hass, self._username, self._password)
        try:
            # Call the DabPumpsApi with the detect_device method
            self._install_map = await self._coordinator.async_config_flow_data()
            
            if self._install_map:
                _LOGGER.info("Successfully connected!")
                _LOGGER.debug(f"install_map: {self._install_map}")
                self._errors = {}
                return True
            else:
                self._errors["base"] = ERROR_NO_INSTALLATIONS
        
        # DabPumpsAuthError derives from DabPumpsError, so it must be tested first
        except DabPumpsAuthError:
            self._errors[CONF_PASSWORD] = ERROR_INVALID_AUTH
        except DabPumpsError:
            self._errors["base"] = ERROR_CANNOT_CONNECT
        except Exception:
            _LOGGER.exception("Unexpected exception while testing connection")
            self._errors["base"] = ERROR_UNKNOWN
        
        return False
    

    async def async_try_update_role(self, role_old: DabPumpsUserRole, role_new: DabPumpsUserRole):
        """Try to update the user role to Installer/Professional"""

        _LOGGER.info(f"Trying update of user role from {role_old} to {role_new}...")
        
        self._errors = {}
        try:
            # Call the DabPumpsApi with the update_role
            role_rsp = await self._coordinator.async_config_change_role(self._install_id, role_old, role_new)
            
            if role_rsp is not None:
                _LOGGER.info("Successfully updated role!")
                self._errors = {}
                return True
            else:
                self._errors[CONF_ROLE_MENU] = ERROR_ROLE_UPDATE_FAILED
        
        except DabPumpsAuthError:
            self._errors[CONF_ROLE_MENU] = ERROR_INVALID_AUTH
        except DabPumpsError:
            self._errors[CONF_ROLE_MENU] = ERROR_CANNOT_CONNECT
        except Exception:
            _LOGGER.exception("Unexpected exception while updating role")
            self._errors[CONF_ROLE_MENU] = ERROR_UNKNOWN
        
        return False

    
    # This is step 1 for the user/pass function.
    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        
        if user_input is not None:
            _LOGGER.debug(f"Step user - handle input {async_redact_data(user_input, DIAGNOSTICS_REDACT)}")
            
            self._username = user_input.get(CONF_USERNAME, '')
            self._password = user_input.get(CONF_PASSWORD, '')
            
            # test the username+password and retrieve installations available to this user
            await self.async_try_connection()
            
            if not self._errors:
                # go to the second step to choose which installation to use
                return await self.async_step_install()
        
        # Show the form with the username+password and optionally a list of installations
        _LOGGER.debug(f"Step user - show form")
        
        return self.async_show_form(
            step_id = "user", 
            data_schema = vol.Schema({
                vol.Required(CONF_USERNAME, description={"suggested_value": self._username or DEFAULT_USERNAME}): str,
                vol.Required(CONF_PASSWORD, description={"suggested_value": self._password or DEFAULT_PASSWORD}): str,
            }),
            errors = self._errors
        )
    
    
    async def async_step_install(self, user_input=None) -> ConfigFlowResult:
        """Second step im config flow to choose which installation to use"""
        
        # if there is only one installation found, then automatically select it and skip display of form
        if self._install_map and len(self._install_map)==1:
            _LOGGER.debug(f"Step install - auto select the only installation available")
            user_input = {
                CONF_INSTALL_NAME: next( (install.name for install in self._install_map.values()), None)
            }
        
        if user_input is not None:
            _LOGGER.debug(f"Step install - handle input {user_input}")
            
            self._install_name = user_input.get(CONF_INSTALL_NAME, None)
            self._install_id = next( (install.id for install in self._install_map.values() if install.name == self._install_name), None)

            # Do we have everything we need?
            if not self._errors and self._install_id and self._install_name:
                # Abort now, before offering a role change on an installation that is already configured
                await self.async_set_unique_id(self._install_id)
                self._abort_if_unique_id_configured()

                # go to the second step to check the user role in this install
                return await self.async_step_role()

        # Show a form with the list of installations
        _LOGGER.debug(f"Step install - show form")        

        return self.async_show_form(
            step_id = "install", 
            data_schema = vol.Schema({
                vol.Required(CONF_INSTALL_NAME): selector({
                   "select": {
                      "options": [ install.name for install in self._install_map.values() ]
                   }
                })
            }),
            errors = self._errors
        )
    
    
    async def async_step_role(self, user_input=None) -> ConfigFlowResult:
        """Third step in config flow to check user role"""
        
        role_old = next( (install.role for install in self._install_map.values() if install.id == self._install_id), DabPumpsUserRole.CUSTOMER_FREE)
        _LOGGER.debug(f"Step role - detected current role {role_old}")

        if role_old not in [DabPumpsUserRole.CUSTOMER, DabPumpsUserRole.CUSTOMER_FREE]:
            _LOGGER.debug(f"Step role - auto keep current role {role_old}")
            user_input = {
                CONF_ROLE_MENU: ROLE_MENU_KEEP
            }
        
        if user_input is not None:
            _LOGGER.debug(f"Step role - handle input {user_input}")
            
            chosen = user_input.get(CONF_ROLE_MENU, None)
            if chosen == ROLE_MENU_UPDATE:
                # Try and update role from customer to installer (regardless of subscription or free). 
                # Ignore if this fails
                role_old = DabPumpsUserRole.CUSTOMER
                role_new = DabPumpsUserRole.INSTALLER
                await self.async_try_update_role(role_old, role_new)
            # ROLE_MENU_KEEP (or an auto-kept role): nothing to do

            # Finish the config flow
            return await self.async_step_finish()

        # Show a form with role choices
        _LOGGER.debug(f"Step role - build schema")
        schema = vol.Schema({
            vol.Required(CONF_ROLE_MENU): selector({
                "select": { 
                    "options": [ROLE_MENU_UPDATE, ROLE_MENU_KEEP],
                    "mode": "list",
                    "translation_key": CONF_ROLE_MENU
                }
            })
        })

        _LOGGER.debug(f"Step role - show form")
        return self.async_show_form(
            step_id = "role", 
            data_schema = schema,
            errors = self._errors,
            last_step = False,
        )
    
    
    async def _async_persist_login(self) -> None:
        """
        Write the freshly obtained login and refresh token into the persisted cache,
        so the next startup can skip the password login.
        """
        self._coordinator._install_id = self._install_id
        self._coordinator._install_name = self._install_name
        await self._coordinator.async_on_unload()


    async def async_step_finish(self, user_input=None) -> ConfigFlowResult:
        """Configuration has finished"""

        await self._async_persist_login()

        # unique_id was already set and checked in async_step_install
        return self.async_create_entry(
            title = self._install_name, 
            data = {
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_INSTALL_ID: self._install_id,
                CONF_INSTALL_NAME: self._install_name,
            },
            options = {
                CONF_LANGUAGE: DEFAULT_LANGUAGE,
            }
        )


    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle re-authentication after the coordinator raised ConfigEntryAuthFailed."""
        self._username = entry_data.get(CONF_USERNAME, '')
        self._install_id = entry_data.get(CONF_INSTALL_ID)
        self._install_name = entry_data.get(CONF_INSTALL_NAME)
        return await self.async_step_reauth_confirm()


    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new password for the existing account and re-validate it."""
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            self._password = user_input.get(CONF_PASSWORD, '')

            await self.async_try_connection()

            if not self._errors and self._install_id not in self._install_map:
                # Credentials are valid but no longer give access to this installation
                self._errors["base"] = ERROR_NO_INSTALLATIONS

            if not self._errors:
                # Overwrite the cached refresh token with the one from the fresh password login;
                # otherwise the next startup restores the token that caused the reauth.
                await self._async_persist_login()

                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates = {
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                    },
                )

        return self.async_show_form(
            step_id = "reauth_confirm",
            description_placeholders = {
                CONF_USERNAME: self._username,
                CONF_INSTALL_NAME: self._install_name or "",
            },
            data_schema = vol.Schema({
                vol.Required(CONF_PASSWORD): str,
            }),
            errors = self._errors,
        )
    

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self) -> None:
        """Initialize options flow."""
        super().__init__()

        self._language_code = None
        self._language_name = None
        self._errors = {}
        self._language_map: dict[str, str] = {}


    def _get_language_auto_text(self):
        system_language_code = DabPumpsCoordinator.system_language(self.hass)

        if system_language_code in LANGUAGE_MAP:
            system_language_name = LANGUAGE_MAP[system_language_code]
            return LANGUAGE_TEXT_AUTO.format(system_language_name)
        else:
            fallback_language_name = LANGUAGE_MAP[LANGUAGE_AUTO_FALLBACK]
            return LANGUAGE_TEXT_FALLBACK.format(fallback_language_name)


    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""

        # Build a per-flow copy; the module-level LANGUAGE_MAP must not be mutated
        # (self.hass is only available once the flow runs, not in __init__)
        if not self._language_map:
            self._language_map = dict(LANGUAGE_MAP)
            self._language_map[LANGUAGE_AUTO] = self._get_language_auto_text()

        if user_input is not None:
            _LOGGER.debug(f"Options flow handle user input")
            self._errors = {}

            self._language_name = user_input.get(MSG_LANGUAGE, None)
            self._language_code = next( (code for code,name in self._language_map.items() if name == self._language_name), None)

            # Do we have everything we need?
            if not self._errors and self._language_code:

                # The options flow manager stores the returned data as the entry options
                return self.async_create_entry(data = { CONF_LANGUAGE: self._language_code })

            _LOGGER.error(f"Error: {self._errors}")
        
        else:
            self._language_code = self.config_entry.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
            self._language_name = next( (name for code,name in self._language_map.items() if code == self._language_code), LANGUAGE_MAP[DEFAULT_LANGUAGE])

        # Show the form with the options
        _LOGGER.debug(f"Options flow show user input form")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(MSG_LANGUAGE, default=self._language_name): selector({
                   "select": {
                      "options": [ name for name in self._language_map.values() ]
                   }
                })
            }),
            errors = self._errors
        )
 