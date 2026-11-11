# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin appconfig knobs: precedence over env and defaults, fail-soft
parsing, and the env-only boundary (secrets and infra-coupled variables
are never surfaced)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from marp_slides.settings import (
    ADMIN_CONFIG_KEYS,
    ADMIN_CONFIG_KNOBS,
    ADMIN_CONFIG_REFRESH_TTL_S,
    DEFAULT_CUSTOM_THEME_PATH,
    DEFAULT_DESIGN_TOKENS_PATH,
    DEFAULT_DRAFTS_FOLDER,
    DEFAULT_LOG_EXCERPT,
    DEFAULT_MAX_PROMPT_CHARS,
    DEFAULT_MAX_SLIDES,
    DEFAULT_OVERFLOW_CHECK,
    DEFAULT_RETRY_LLM,
    DEFAULT_TOTAL_LLM_TIMEOUT_S,
    ENV_ONLY_CONFIG_KEYS,
    Settings,
    apply_admin_config,
)
from marp_slides.task_worker import AdminConfigCache

#: An env override per knob: proves the ADMIN value wins over env, and the
#: env value over the built-in default (each distinct from both).
_ENV_LEVEL = {
    "SLIDEPREPPER_TOTAL_LLM_TIMEOUT": "600",
    "SLIDEPREPPER_MAX_SLIDES": "10",
    "SLIDEPREPPER_MAX_PROMPT_CHARS": "100",
    "SLIDEPREPPER_DRAFTS_FOLDER": "Env Drafts",
    "SLIDEPREPPER_RETRY_LLM": "false",
    "SLIDEPREPPER_OVERFLOW_CHECK": "false",
    "SLIDEPREPPER_LOG_EXCERPT": "false",
    "SLIDEPREPPER_DRAFTS_ENABLED": "false",
    "SLIDEPREPPER_DESIGN_TOKENS_PATH": "Env Tokens/tokens.json",
    "SLIDEPREPPER_CUSTOM_THEME_PATH": "Env Theme/theme.css",
}

_ADMIN_LEVEL = {
    "SLIDEPREPPER_TOTAL_LLM_TIMEOUT": "1200",
    "SLIDEPREPPER_MAX_SLIDES": "20",
    "SLIDEPREPPER_MAX_PROMPT_CHARS": "200",
    "SLIDEPREPPER_DRAFTS_FOLDER": "Admin Drafts",
    "SLIDEPREPPER_RETRY_LLM": "true",
    "SLIDEPREPPER_OVERFLOW_CHECK": "true",
    "SLIDEPREPPER_LOG_EXCERPT": "true",
    "SLIDEPREPPER_DRAFTS_ENABLED": "true",
    "SLIDEPREPPER_DESIGN_TOKENS_PATH": "Admin Tokens/tokens.json",
    "SLIDEPREPPER_CUSTOM_THEME_PATH": "Admin Theme/theme.css",
}

#: The expected effective value per knob per precedence level (attribute
#: name -> (default, env, admin)).
_EXPECTED = {
    "total_llm_timeout_s": (
        DEFAULT_TOTAL_LLM_TIMEOUT_S,
        600,
        1200,
    ),
    "max_slides": (DEFAULT_MAX_SLIDES, 10, 20),
    "max_prompt_chars": (DEFAULT_MAX_PROMPT_CHARS, 100, 200),
    "drafts_folder": (DEFAULT_DRAFTS_FOLDER, "Env Drafts", "Admin Drafts"),
    "retry_llm": (DEFAULT_RETRY_LLM, False, True),
    "overflow_check": (DEFAULT_OVERFLOW_CHECK, False, True),
    "log_excerpt": (DEFAULT_LOG_EXCERPT, False, True),
    "drafts_enabled": (True, False, True),
    "design_tokens_path": (
        DEFAULT_DESIGN_TOKENS_PATH,
        "Env Tokens/tokens.json",
        "Admin Tokens/tokens.json",
    ),
    "custom_theme_path": (
        DEFAULT_CUSTOM_THEME_PATH,
        "Env Theme/theme.css",
        "Admin Theme/theme.css",
    ),
}


def test_precedence_matrix_per_knob() -> None:
    """appconfig value > env value > built-in default, for every knob and
    every level (no knob may be pinned to one level by a typo in the
    spec)."""
    from_env_only = Settings.from_env(_ENV_LEVEL)
    assert from_env_only.drafts_enabled is False  # env parse really applied
    for knob in ADMIN_CONFIG_KNOBS:
        default_v, env_v, admin_v = _EXPECTED[knob.attribute]
        # default level: no env, no admin
        assert getattr(Settings.from_env({}), knob.attribute) == default_v, knob.key
        # env level: env set, no admin
        assert getattr(from_env_only, knob.attribute) == env_v, knob.key
        # admin level: env set, admin set (admin wins)
        layered = apply_admin_config(from_env_only, {knob.key: _ADMIN_LEVEL[knob.key]})
        assert getattr(layered, knob.attribute) == admin_v, knob.key


def test_admin_value_of_every_knob_wins_in_one_batch() -> None:
    layered = apply_admin_config(Settings.from_env(_ENV_LEVEL), _ADMIN_LEVEL)
    for knob in ADMIN_CONFIG_KNOBS:
        _default_v, _env_v, admin_v = _EXPECTED[knob.attribute]
        assert getattr(layered, knob.attribute) == admin_v, knob.key


def test_unset_or_empty_admin_key_keeps_env_value() -> None:
    base = Settings.from_env({"SLIDEPREPPER_MAX_SLIDES": "10"})
    assert apply_admin_config(base, {}).max_slides == 10
    assert apply_admin_config(base, {"SLIDEPREPPER_MAX_SLIDES": ""}).max_slides == 10
    # a whitespace-only string knob is unset, like the env parser treats it
    assert (
        apply_admin_config(
            Settings.from_env({"SLIDEPREPPER_DRAFTS_FOLDER": "Env Drafts"}),
            {"SLIDEPREPPER_DRAFTS_FOLDER": "   "},
        ).drafts_folder
        == "Env Drafts"
    )


def test_invalid_int_fails_soft_to_env_with_warning(caplog) -> None:
    base = Settings.from_env({"SLIDEPREPPER_MAX_SLIDES": "10"})
    with caplog.at_level(logging.WARNING, logger="marp_slides.settings"):
        layered = apply_admin_config(base, {"SLIDEPREPPER_MAX_SLIDES": "many"})
    assert layered.max_slides == 10
    assert "SLIDEPREPPER_MAX_SLIDES" in caplog.text
    assert "many" in caplog.text


def test_invalid_bool_fails_soft_to_env_with_warning(caplog) -> None:
    """A bool typo must not silently disable a feature (the env parser
    accepts any string; the admin source is stricter by design)."""
    base = Settings.from_env({"SLIDEPREPPER_RETRY_LLM": "true"})
    with caplog.at_level(logging.WARNING, logger="marp_slides.settings"):
        layered = apply_admin_config(base, {"SLIDEPREPPER_RETRY_LLM": "ture"})
    assert layered.retry_llm is True
    assert "SLIDEPREPPER_RETRY_LLM" in caplog.text
    assert "ture" in caplog.text


@pytest.mark.parametrize("token", ["1", "true", "yes", "on"])
def test_admin_bool_truthy_tokens(token: str) -> None:
    assert apply_admin_config(Settings(), {"SLIDEPREPPER_DRAFTS_ENABLED": token})


@pytest.mark.parametrize("token", ["0", "false", "no", "off", "FALSE", " Off "])
def test_admin_bool_falsy_tokens(token: str) -> None:
    assert not apply_admin_config(
        Settings(), {"SLIDEPREPPER_DRAFTS_ENABLED": token}
    ).drafts_enabled


def test_surfaced_keys_are_exactly_the_operational_knob_set() -> None:
    """The surfaced set is pinned: the operational knobs below, nothing
    more. A knob joining or leaving the surface must be a deliberate
    change to this list."""
    assert ADMIN_CONFIG_KEYS == (
        "SLIDEPREPPER_TOTAL_LLM_TIMEOUT",
        "SLIDEPREPPER_MAX_SLIDES",
        "SLIDEPREPPER_MAX_PROMPT_CHARS",
        "SLIDEPREPPER_DRAFTS_FOLDER",
        "SLIDEPREPPER_RETRY_LLM",
        "SLIDEPREPPER_OVERFLOW_CHECK",
        "SLIDEPREPPER_LOG_EXCERPT",
        "SLIDEPREPPER_DRAFTS_ENABLED",
        "SLIDEPREPPER_DESIGN_TOKENS_PATH",
        "SLIDEPREPPER_CUSTOM_THEME_PATH",
    )


def test_env_only_variables_are_not_surfaced() -> None:
    """Secrets, AppAPI wiring, infra-coupled and internal pipeline
    variables are unreachable through the admin config surface."""
    assert not set(ADMIN_CONFIG_KEYS) & set(ENV_ONLY_CONFIG_KEYS)
    for secret in (
        "APP_SECRET",
        "NEXTCLOUD_URL",
        "SLIDEPREPPER_MARP_BIN",
        "SLIDEPREPPER_POLL_INTERVAL",
        "SLIDEPREPPER_LLM_TIMEOUT",
        "SLIDEPREPPER_MARP_TIMEOUT",
        "SLIDEPREPPER_MAX_DECK_BYTES",
    ):
        assert secret in ENV_ONLY_CONFIG_KEYS
        assert secret not in ADMIN_CONFIG_KEYS
    # the env-only MARP knobs map to Settings attributes the admin surface
    # cannot reach: no knob overrides them
    env_only_attributes = {
        "marp_bin",
        "poll_interval_s",
        "llm_timeout_s",
        "marp_timeout_s",
        "max_deck_bytes",
    }
    knob_attributes = {knob.attribute for knob in ADMIN_CONFIG_KNOBS}
    assert not knob_attributes & env_only_attributes
    # layering a whole admin batch never perturbs them either (proved for
    # values in test_layering_preserves_env_only_settings)


def test_knob_spec_is_well_formed() -> None:
    attributes = {knob.attribute for knob in ADMIN_CONFIG_KNOBS}
    keys = [knob.key for knob in ADMIN_CONFIG_KNOBS]
    assert len(keys) == len(set(keys))  # keys are the appconfig keys: unique
    assert all(knob.key.startswith("SLIDEPREPPER_") for knob in ADMIN_CONFIG_KNOBS)
    assert all(knob.kind in ("int", "bool", "str") for knob in ADMIN_CONFIG_KNOBS)
    # every attribute exists on Settings and carries a metadata-bearing
    # label/description for the admin form
    for knob in ADMIN_CONFIG_KNOBS:
        assert hasattr(Settings(), knob.attribute)
        assert knob.label and "|" not in knob.label
        assert knob.description and "Default:" in knob.description
    assert len(attributes) == len(keys)  # one attribute per key


def test_layering_preserves_env_only_settings() -> None:
    """Applying admin values must never touch the env-only knobs."""
    base = Settings.from_env(
        {
            "SLIDEPREPPER_POLL_INTERVAL": "2.5",
            "SLIDEPREPPER_MARP_TIMEOUT": "60",
            "SLIDEPREPPER_LLM_TIMEOUT": "120",
            "SLIDEPREPPER_MAX_DECK_BYTES": "2048",
            "SLIDEPREPPER_MARP_BIN": "/usr/local/bin/marp",
        }
    )
    layered = apply_admin_config(base, _ADMIN_LEVEL)
    assert layered.poll_interval_s == 2.5
    assert layered.marp_timeout_s == 60
    assert layered.llm_timeout_s == 120
    assert layered.max_deck_bytes == 2048
    assert layered.marp_bin == "/usr/local/bin/marp"


# --- read-through cache (the runtime refresh seam) -------------------------


class _FakeAppConfigExAPI:
    """The oc_appconfig_ex surface of the nc client: mutable values (what
    an admin's save changed), a read counter, an optional read error."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})
        self.reads: list[list[str]] = []
        self.error: Exception | None = None

    def get_values(self, keys):
        self.reads.append(list(keys))
        if self.error is not None:
            raise self.error
        return [
            SimpleNamespace(key=key, value=self.values[key])
            for key in keys
            if key in self.values
        ]


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_cache_reads_through_and_observes_change_after_ttl() -> None:
    """Refresh semantics: one batched read serves the TTL window; a value
    an admin saves is observed by a later task once the TTL expires (the
    documented worst-case latency for a knob change)."""
    appconfig = _FakeAppConfigExAPI({"SLIDEPREPPER_MAX_SLIDES": "2"})
    clock = _FakeClock()
    cache = AdminConfigCache(
        Settings.from_env({"SLIDEPREPPER_MAX_SLIDES": "10"}),
        ttl_s=ADMIN_CONFIG_REFRESH_TTL_S,
        clock=clock,
    )
    nc = SimpleNamespace(appconfig_ex=appconfig)
    assert cache.settings_for(nc).max_slides == 2  # admin beats env
    appconfig.values["SLIDEPREPPER_MAX_SLIDES"] = "3"
    clock.now = 10.0
    assert cache.settings_for(nc).max_slides == 2  # still fresh: no read
    assert len(appconfig.reads) == 1
    clock.now = ADMIN_CONFIG_REFRESH_TTL_S + 0.1
    assert cache.settings_for(nc).max_slides == 3  # changed value observed
    assert len(appconfig.reads) == 2


def test_cache_batches_every_knob_key_in_one_read() -> None:
    appconfig = _FakeAppConfigExAPI({})
    clock = _FakeClock()
    cache = AdminConfigCache(Settings(), clock=clock)
    cache.settings_for(SimpleNamespace(appconfig_ex=appconfig))
    assert appconfig.reads == [list(ADMIN_CONFIG_KEYS)]


def test_cache_without_values_returns_env_settings() -> None:
    base = Settings.from_env({"SLIDEPREPPER_TOTAL_LLM_TIMEOUT": "600"})
    cache = AdminConfigCache(base, clock=_FakeClock())
    assert cache.settings_for(SimpleNamespace(appconfig_ex=_FakeAppConfigExAPI())) == (
        base
    )


def test_cache_read_failure_negative_caches_and_keeps_env(caplog) -> None:
    """An unreachable AppAPI (or a missing capability) keeps the
    environment settings and does not hammer the server: the failed read
    is cached for the same TTL."""
    from nc_py_api import NextcloudException

    appconfig = _FakeAppConfigExAPI()
    appconfig.error = NextcloudException(997, "AppAPI unreachable")
    clock = _FakeClock()
    base = Settings.from_env({"SLIDEPREPPER_MAX_SLIDES": "10"})
    cache = AdminConfigCache(base, ttl_s=30.0, clock=clock)
    nc = SimpleNamespace(appconfig_ex=appconfig)
    with caplog.at_level(logging.WARNING, logger="marp_slides.task_worker"):
        first = cache.settings_for(nc)
        clock.now = 10.0
        second = cache.settings_for(nc)
    assert first == base == second
    assert len(appconfig.reads) == 1  # the failure was negative-cached
    assert "AppAPI unreachable" in caplog.text


def test_cache_recovers_after_a_failed_read() -> None:
    from nc_py_api import NextcloudException

    appconfig = _FakeAppConfigExAPI({"SLIDEPREPPER_MAX_SLIDES": "2"})
    error = NextcloudException(997, "AppAPI unreachable")
    appconfig.error = error
    clock = _FakeClock()
    cache = AdminConfigCache(Settings(), ttl_s=30.0, clock=clock)
    nc = SimpleNamespace(appconfig_ex=appconfig)
    assert cache.settings_for(nc).max_slides == Settings().max_slides
    appconfig.error = None
    clock.now = 30.1
    assert cache.settings_for(nc).max_slides == 2


# --- declarative admin form (the AppAPI admin surface) ----------------------


def test_admin_form_covers_exactly_the_knob_set() -> None:
    """The form's field ids ARE the oc_appconfig_ex keys the worker reads
    (the declarative save stores by field id), in spec order, with the
    built-in defaults as the UI fallback."""
    from marp_slides.controller import admin_settings_form

    form = admin_settings_form()
    assert form.id == "operational_settings"
    assert form.section_type == "admin"
    assert form.section_id == "declarative_settings"
    assert form.title == "SlidePrepper"
    assert f"within {ADMIN_CONFIG_REFRESH_TTL_S:.0f} seconds" in form.description
    assert "environment variable of the same name" in form.description
    assert [f.id for f in form.fields] == list(ADMIN_CONFIG_KEYS)


def test_admin_form_field_types_and_defaults_match_the_knobs() -> None:
    from nc_py_api.ex_app.ui.settings import SettingsFieldType

    from marp_slides.controller import admin_settings_form

    defaults = Settings()  # built-in defaults, the single source
    form = admin_settings_form()
    for field, knob in zip(form.fields, ADMIN_CONFIG_KNOBS, strict=True):
        assert field.title == knob.label
        assert field.description == knob.description
        expected_type = {"int": "number", "bool": "radio", "str": "text"}[knob.kind]
        assert field.type == SettingsFieldType(expected_type)
        if knob.kind == "bool":
            # yes/no radio carrying the built-in default as "1"/"0"
            assert field.default == ("1" if getattr(defaults, knob.attribute) else "0")
        else:
            assert field.default == getattr(defaults, knob.attribute)


def test_admin_form_id_survives_the_settings_ui_prefix_strip() -> None:
    """Regression (verified live against Nextcloud 35 + app_api 35): the
    settings UI submits form.id.replace(appId + '_', '') as the formId,
    and app_api's SetValueListener drops the save silently when that
    stripped id matches no registered form. A form id containing the app
    prefix therefore makes every real-UI save a 200-and-forget no-op, so
    the registered id must be invariant under the strip."""
    from marp_slides.controller import ADMIN_SETTINGS_FORM_ID, admin_settings_form
    from marp_slides.settings import APP_ID

    form = admin_settings_form()
    assert form.id == ADMIN_SETTINGS_FORM_ID
    assert ADMIN_SETTINGS_FORM_ID.replace(f"{APP_ID}_", "") == ADMIN_SETTINGS_FORM_ID


def test_admin_form_bools_are_radios_with_storable_values() -> None:
    """Regression (verified live): an unchecked declarative checkbox saves
    JSON false, app_api stores that as the empty string, and an empty
    value means 'keep the environment value' in the precedence design,
    so a checkbox knob could never be turned off through the UI. Boolean
    knobs must therefore be Yes/No radios whose submitted strings ("1"/
    "0") store verbatim and parse through the strict bool tokens."""
    from marp_slides.controller import admin_settings_form
    from marp_slides.settings import _parse_admin_bool

    form = admin_settings_form()
    bool_fields = [
        f
        for f, knob in zip(form.fields, ADMIN_CONFIG_KNOBS, strict=True)
        if knob.kind == "bool"
    ]
    assert bool_fields  # the four toggles
    for field in bool_fields:
        assert isinstance(field.options, dict)
        assert field.options == {"Yes": "1", "No": "0"}
        for value in field.options.values():
            assert value != ""
            assert _parse_admin_bool(value) is not None
        assert field.default in field.options.values()


def test_admin_form_field_ids_never_include_env_only_variables() -> None:
    from marp_slides.controller import admin_settings_form

    field_ids = [f.id for f in admin_settings_form().fields]
    assert not set(field_ids) & set(ENV_ONLY_CONFIG_KEYS)


def test_enabled_handler_registers_providers_and_the_admin_form() -> None:
    """Enablement registers the task providers AND the declarative
    settings form (the canonical AppAPI pattern: the form rides the
    enabled event; re-registration is idempotent); disablement removes
    the providers but never the admin-set config values."""
    from marp_slides.controller import ADMIN_SETTINGS_FORM_ID, enabled_handler
    from marp_slides.task_worker import THREAD_STOP_EVENT

    class _FakeTaskProcessing:
        def __init__(self) -> None:
            self.registered: list[tuple[object, object]] = []
            self.unregistered: list[str] = []

        def register(self, provider, task_type):
            self.registered.append((provider, task_type))

        def unregister(self, provider_id):
            self.unregistered.append(provider_id)

    class _FakeSettingsUI:
        def __init__(self) -> None:
            self.forms: list[object] = []

        def register_form(self, form):
            self.forms.append(form)

    task_processing = _FakeTaskProcessing()
    settings_ui = _FakeSettingsUI()
    nc = SimpleNamespace(
        providers=SimpleNamespace(task_processing=task_processing),
        ui=SimpleNamespace(settings=settings_ui),
    )
    try:
        assert enabled_handler(True, nc) == ""
        assert len(task_processing.registered) == 2
        assert len(settings_ui.forms) == 1
        assert settings_ui.forms[0].id == ADMIN_SETTINGS_FORM_ID
        assert enabled_handler(False, nc) == ""
        assert task_processing.unregistered == [
            "slideprepper-deck_renderer",
            "slideprepper:re_renderer",
        ]
        assert len(settings_ui.forms) == 1  # the form is not unregistered
    finally:
        # disablement stops the worker thread: undo the global state the
        # handler touched so other tests see the module as imported
        THREAD_STOP_EVENT.clear()


# --- deployment manifest documentation --------------------------------------


def test_info_xml_documents_the_knob_set_and_its_labels() -> None:
    """info.xml stays accurate now that env is the fallback: every
    surfaced knob is declared as an env variable (AppAPI only passes
    declared names into the container), its display-name matches the
    admin form label verbatim, and its description states the fallback
    and the admin precedence."""
    import xml.etree.ElementTree as ET
    from pathlib import Path

    tree = ET.parse(Path(__file__).resolve().parent.parent / "appinfo" / "info.xml")
    variables = tree.getroot().find("external-app").find("environment-variables")
    declared = {
        v.findtext("name"): (v.findtext("display-name"), v.findtext("description"))
        for v in variables.findall("variable")
    }
    assert set(ADMIN_CONFIG_KEYS) <= set(declared)
    knobs = {knob.key: knob for knob in ADMIN_CONFIG_KNOBS}
    for key, knob in knobs.items():
        display_name, description = declared[key]
        assert display_name == knob.label, key
        assert description.startswith("Optional. Environment fallback"), key
        assert "ExApps Settings" in description, key
    # the env-only MARP knobs stay documented as non-admin-manageable
    for env_only in (
        "SLIDEPREPPER_POLL_INTERVAL",
        "SLIDEPREPPER_MARP_TIMEOUT",
        "SLIDEPREPPER_LLM_TIMEOUT",
    ):
        assert env_only in declared
        assert "not admin-manageable" in declared[env_only][1].lower(), env_only
    # no undeclared strays: every declared SLIDEPREPPER_ variable is
    # classified exactly once (surfaced or documented env-only)
    declared_marp = {name for name in declared if name.startswith("SLIDEPREPPER_")}
    assert declared_marp == set(ADMIN_CONFIG_KEYS) | {
        "SLIDEPREPPER_POLL_INTERVAL",
        "SLIDEPREPPER_MARP_TIMEOUT",
        "SLIDEPREPPER_LLM_TIMEOUT",
    }
