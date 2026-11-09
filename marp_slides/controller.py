# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FastAPI application: lifecycle, auth middleware and TaskProcessing registration."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from nc_py_api import NextcloudApp
from nc_py_api.ex_app import AppAPIAuthMiddleware, set_handlers
from nc_py_api.ex_app.ui.settings import (
    SettingsField,
    SettingsFieldType,
    SettingsForm,
)

from .settings import (
    ADMIN_CONFIG_KNOBS,
    ADMIN_CONFIG_REFRESH_TTL_S,
    PROVIDER_ID,
    RE_RENDER_PROVIDER_ID,
    Settings,
)
from .task_definition import (
    PROVIDER,
    RE_RENDER_PROVIDER,
    RE_RENDER_TASK_TYPE,
    TASK_TYPE,
)
from .task_worker import (
    APP_ENABLED,
    THREAD_STOP_EVENT,
    start_bg_thread,
    stop_bg_thread,
    trigger_handler,
)

LOGGER = logging.getLogger("marp_slides.controller")

ADMIN_SETTINGS_FORM_ID = "marp_slides_settings"

#: The declarative settings section AppAPI ships for ExApps (registered by
#: app_api as "ExApps Settings"; see its lib/Settings/DeclarativeSettings).
ADMIN_SETTINGS_SECTION_ID = "declarative_settings"

_FIELD_TYPE_BY_KIND = {
    "int": SettingsFieldType.NUMBER,
    "bool": SettingsFieldType.CHECKBOX,
    "str": SettingsFieldType.TEXT,
}


def admin_settings_form() -> SettingsForm:
    """The admin-editable form for the operational knobs (AppAPI
    declarative settings): rendered under Settings -> Administration, its
    field ids ARE the oc_appconfig_ex keys the worker reads per task.
    Field defaults show the BUILT-IN defaults: an environment override
    stays in force until an admin saves a value here, and the form cannot
    see the container environment."""
    return SettingsForm(
        id=ADMIN_SETTINGS_FORM_ID,
        section_type="admin",
        section_id=ADMIN_SETTINGS_SECTION_ID,
        title="Marp Slides",
        description=(
            "Operational knobs. A value saved here overrides the container "
            "environment variable of the same name and applies to new "
            f"tasks within {ADMIN_CONFIG_REFRESH_TTL_S:.0f} seconds; an "
            "empty field keeps the environment value. The defaults shown "
            "are the built-in defaults, not the current environment "
            "overrides."
        ),
        fields=[
            SettingsField(
                id=knob.key,
                title=knob.label,
                description=knob.description,
                type=_FIELD_TYPE_BY_KIND[knob.kind],
                default=getattr(Settings(), knob.attribute),
            )
            for knob in ADMIN_CONFIG_KNOBS
        ],
    )


def enabled_handler(enabled: bool, nc: NextcloudApp) -> str:
    try:
        if enabled:
            nc.providers.task_processing.register(PROVIDER, TASK_TYPE)
            nc.providers.task_processing.register(
                RE_RENDER_PROVIDER, RE_RENDER_TASK_TYPE
            )
            # the admin settings form rides enablement: re-registering on
            # every enable refreshes the schema after upgrades (idempotent)
            nc.ui.settings.register_form(admin_settings_form())
            APP_ENABLED.set()
            if THREAD_STOP_EVENT.is_set():
                # If the threads were previously stopped, they are started again,
                # otherwise the lifecycle handler has already started them.
                start_bg_thread(Settings.from_env())
        else:
            APP_ENABLED.clear()
            nc.providers.task_processing.unregister(PROVIDER_ID)
            nc.providers.task_processing.unregister(RE_RENDER_PROVIDER_ID)
            stop_bg_thread()
    except Exception as exc:
        LOGGER.exception("Error in enabled handler")
        return f"Error in enabled handler: {exc}"

    LOGGER.info("App %s", "enabled" if enabled else "disabled")
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    set_handlers(app, enabled_handler, trigger_handler=trigger_handler)
    settings = Settings.from_env()
    try:
        nc = NextcloudApp()
        nc_app_enabled = nc.enabled_state
    except ValueError as exc:
        # Missing NEXTCLOUD_URL/APP_ID/APP_SECRET env: the heartbeat still
        # serves; the worker will retry client creation in its loop.
        LOGGER.warning("Nextcloud client not configured yet: %s", exc)
        nc_app_enabled = False
    if nc_app_enabled:
        APP_ENABLED.set()
    start_bg_thread(settings)
    LOGGER.info("App enable state at startup: %s", nc_app_enabled)
    yield
    stop_bg_thread()


app = FastAPI(lifespan=lifespan)
app.add_middleware(AppAPIAuthMiddleware)


@app.get("/")
def _(request: Request) -> str:
    """Server check."""
    return f"Hello, {request.scope.get('username', 'anon')}!"
