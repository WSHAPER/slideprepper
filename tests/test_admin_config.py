# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin appconfig knobs: precedence over env and defaults, fail-soft
parsing, and the env-only boundary (secrets and infra-coupled variables
are never surfaced)."""

from __future__ import annotations

import logging

import pytest

from marp_slides.settings import (
    ADMIN_CONFIG_KEYS,
    ADMIN_CONFIG_KNOBS,
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

#: An env override per knob: proves the ADMIN value wins over env, and the
#: env value over the built-in default (each distinct from both).
_ENV_LEVEL = {
    "MARP_SLIDES_TOTAL_LLM_TIMEOUT": "600",
    "MARP_SLIDES_MAX_SLIDES": "10",
    "MARP_SLIDES_MAX_PROMPT_CHARS": "100",
    "MARP_SLIDES_DRAFTS_FOLDER": "Env Drafts",
    "MARP_SLIDES_RETRY_LLM": "false",
    "MARP_SLIDES_OVERFLOW_CHECK": "false",
    "MARP_SLIDES_LOG_EXCERPT": "false",
    "MARP_SLIDES_DRAFTS_ENABLED": "false",
}

_ADMIN_LEVEL = {
    "MARP_SLIDES_TOTAL_LLM_TIMEOUT": "1200",
    "MARP_SLIDES_MAX_SLIDES": "20",
    "MARP_SLIDES_MAX_PROMPT_CHARS": "200",
    "MARP_SLIDES_DRAFTS_FOLDER": "Admin Drafts",
    "MARP_SLIDES_RETRY_LLM": "true",
    "MARP_SLIDES_OVERFLOW_CHECK": "true",
    "MARP_SLIDES_LOG_EXCERPT": "true",
    "MARP_SLIDES_DRAFTS_ENABLED": "true",
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
    base = Settings.from_env({"MARP_SLIDES_MAX_SLIDES": "10"})
    assert apply_admin_config(base, {}).max_slides == 10
    assert apply_admin_config(base, {"MARP_SLIDES_MAX_SLIDES": ""}).max_slides == 10
    # a whitespace-only string knob is unset, like the env parser treats it
    assert (
        apply_admin_config(
            Settings.from_env({"MARP_SLIDES_DRAFTS_FOLDER": "Env Drafts"}),
            {"MARP_SLIDES_DRAFTS_FOLDER": "   "},
        ).drafts_folder
        == "Env Drafts"
    )


def test_invalid_int_fails_soft_to_env_with_warning(caplog) -> None:
    base = Settings.from_env({"MARP_SLIDES_MAX_SLIDES": "10"})
    with caplog.at_level(logging.WARNING, logger="marp_slides.settings"):
        layered = apply_admin_config(base, {"MARP_SLIDES_MAX_SLIDES": "many"})
    assert layered.max_slides == 10
    assert "MARP_SLIDES_MAX_SLIDES" in caplog.text
    assert "many" in caplog.text


def test_invalid_bool_fails_soft_to_env_with_warning(caplog) -> None:
    """A bool typo must not silently disable a feature (the env parser
    accepts any string; the admin source is stricter by design)."""
    base = Settings.from_env({"MARP_SLIDES_RETRY_LLM": "true"})
    with caplog.at_level(logging.WARNING, logger="marp_slides.settings"):
        layered = apply_admin_config(base, {"MARP_SLIDES_RETRY_LLM": "ture"})
    assert layered.retry_llm is True
    assert "MARP_SLIDES_RETRY_LLM" in caplog.text
    assert "ture" in caplog.text


@pytest.mark.parametrize("token", ["1", "true", "yes", "on"])
def test_admin_bool_truthy_tokens(token: str) -> None:
    assert apply_admin_config(Settings(), {"MARP_SLIDES_DRAFTS_ENABLED": token})


@pytest.mark.parametrize("token", ["0", "false", "no", "off", "FALSE", " Off "])
def test_admin_bool_falsy_tokens(token: str) -> None:
    assert not apply_admin_config(
        Settings(), {"MARP_SLIDES_DRAFTS_ENABLED": token}
    ).drafts_enabled


def test_surfaced_keys_are_exactly_the_operational_knob_set() -> None:
    """The surfaced set is pinned: the eight operational knobs, nothing
    more. A knob joining or leaving the surface must be a deliberate
    change to this list."""
    assert ADMIN_CONFIG_KEYS == (
        "MARP_SLIDES_TOTAL_LLM_TIMEOUT",
        "MARP_SLIDES_MAX_SLIDES",
        "MARP_SLIDES_MAX_PROMPT_CHARS",
        "MARP_SLIDES_DRAFTS_FOLDER",
        "MARP_SLIDES_RETRY_LLM",
        "MARP_SLIDES_OVERFLOW_CHECK",
        "MARP_SLIDES_LOG_EXCERPT",
        "MARP_SLIDES_DRAFTS_ENABLED",
    )


def test_env_only_variables_are_not_surfaced() -> None:
    """Secrets, AppAPI wiring, infra-coupled and internal pipeline
    variables are unreachable through the admin config surface."""
    assert not set(ADMIN_CONFIG_KEYS) & set(ENV_ONLY_CONFIG_KEYS)
    for secret in (
        "APP_SECRET",
        "NEXTCLOUD_URL",
        "MARP_SLIDES_MARP_BIN",
        "MARP_SLIDES_POLL_INTERVAL",
        "MARP_SLIDES_LLM_TIMEOUT",
        "MARP_SLIDES_MARP_TIMEOUT",
        "MARP_SLIDES_MAX_DECK_BYTES",
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
    assert all(knob.key.startswith("MARP_SLIDES_") for knob in ADMIN_CONFIG_KNOBS)
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
            "MARP_SLIDES_POLL_INTERVAL": "2.5",
            "MARP_SLIDES_MARP_TIMEOUT": "60",
            "MARP_SLIDES_LLM_TIMEOUT": "120",
            "MARP_SLIDES_MAX_DECK_BYTES": "2048",
            "MARP_SLIDES_MARP_BIN": "/usr/local/bin/marp",
        }
    )
    layered = apply_admin_config(base, _ADMIN_LEVEL)
    assert layered.poll_interval_s == 2.5
    assert layered.marp_timeout_s == 60
    assert layered.llm_timeout_s == 120
    assert layered.max_deck_bytes == 2048
    assert layered.marp_bin == "/usr/local/bin/marp"
