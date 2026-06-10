"""Config flow for Vialis Linky integration."""
from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    BASE_URL,
    CLIENT_ID,
    CODE_VERIFIER,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    REDIRECT_URI,
)


async def _validate_credentials(username: str, password: str) -> bool:
    """Perform the 3-step PKCE auth flow to validate credentials."""
    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(CODE_VERIFIER.encode()).digest()
        )
        .decode()
        .rstrip("=")
    )

    # Dedicated session with real cookie jar (needed for cookieOauth across steps)
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as session:
        # Step 1: authenticate with username/password
        try:
            resp1 = await session.post(
                f"{BASE_URL}/auth/externe/authentification",
                data={"username": username, "password": password, "client_id": CLIENT_ID},
            )
            body1 = await resp1.json(content_type=None)
            if body1.get("code") != "0":
                return False
        except Exception:
            return False

        # Step 2: get authorization code
        try:
            resp2 = await session.get(
                f"{BASE_URL}/auth/authorize-internet",
                params={
                    "redirect_uri": REDIRECT_URI,
                    "response_type": "code",
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                    "client_id": CLIENT_ID,
                },
                allow_redirects=False,
            )
            location = resp2.headers.get("Location", "")
            codes = parse_qs(urlparse(location).query).get("code")
            if not codes:
                return False
            auth_code = codes[0]
        except Exception:
            return False

        # Step 3: exchange code for token
        try:
            resp3 = await session.post(
                f"{BASE_URL}/auth/tokenUtilisateurInternet",
                data={
                    "client_id": CLIENT_ID,
                    "code": auth_code,
                    "redirect_uri": REDIRECT_URI,
                    "grant_type": "authorization_code",
                    "code_verifier": CODE_VERIFIER,
                },
            )
            payload = await resp3.json(content_type=None)
            return bool(payload.get("access_token"))
        except Exception:
            return False


_STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): selector.selector({"text": {"type": "email"}}),
        vol.Required(CONF_PASSWORD): selector.selector({"text": {"type": "password"}}),
    }
)


class VialisConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Vialis Linky."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            valid = await _validate_credentials(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if valid:
                return self.async_create_entry(
                    title="Vialis Linky",
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
            errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=_STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
