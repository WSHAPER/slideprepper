# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Theme input: validation, the hard-override check against the deck, and
the authoring prompt's theme instructions (single source:
constructs.MARP_THEMES feeds the validator, the prompt and the ENUM)."""

from __future__ import annotations

import inspect

import pytest

from marp_slides.constructs import (
    ALLOWED_THEMES,
    MARP_THEMES,
    THEME_AUTO,
    THEME_CUSTOM,
    THEME_NEXTCLOUD,
    THEME_VALUES,
)
from marp_slides.deck_pipeline import (
    DECK_SYSTEM_PROMPT,
    DeckError,
    build_llm_prompt,
    validate_theme,
    validate_theme_override,
)

# --- the theme task input ------------------------------------------------------


def test_none_means_nextcloud_default() -> None:
    """The generation task's default theme is the instance branding
    (nextcloud): the task-type registration and this validator fallback
    share the constructs single source."""
    assert validate_theme(None) == THEME_NEXTCLOUD == "nextcloud"


def test_all_values_accepted_and_normalized() -> None:
    for value in THEME_VALUES:
        assert validate_theme(f" {value.upper()} ") == value


def test_garbage_rejected_listing_all_values() -> None:
    with pytest.raises(DeckError, match="theme") as excinfo:
        validate_theme("neon")
    message = str(excinfo.value)
    for value in THEME_VALUES:
        assert f"'{value}'" in message


def test_non_string_rejected() -> None:
    with pytest.raises(DeckError, match="theme"):
        validate_theme(42)


def test_allowed_default_is_the_constructs_constant() -> None:
    """The accepted set has ONE source: the default of ``allowed`` must be
    constructs.THEME_VALUES itself, not a duplicated literal (a second list
    would be free to drift from the ENUM values the provider registers)."""
    default = inspect.signature(validate_theme).parameters["allowed"].default
    assert default is THEME_VALUES


# --- the hard user override vs the deck frontmatter ----------------------------


def test_override_match_passes() -> None:
    validate_theme_override({"theme": "gaia"}, "gaia")


def test_auto_never_constrains_the_deck() -> None:
    validate_theme_override({"theme": "uncover"}, THEME_AUTO)
    validate_theme_override({}, THEME_AUTO)


def test_override_mismatch_is_a_retryable_deck_error() -> None:
    with pytest.raises(DeckError, match="requires theme 'gaia'"):
        validate_theme_override({"theme": "default"}, "gaia")


def test_override_missing_theme_key_rejected_with_remedy() -> None:
    with pytest.raises(DeckError, match=r"is missing.*Write 'theme: uncover'"):
        validate_theme_override({}, "uncover")


# --- the authoring prompt teaches the themes from the constant ------------------


def test_system_prompt_lists_every_theme_with_its_description() -> None:
    for name, description in MARP_THEMES:
        assert f"- {name}: {description}" in DECK_SYSTEM_PROMPT, (
            f"prompt lost theme: {name}"
        )


def test_system_prompt_instructs_the_pick_into_the_frontmatter() -> None:
    assert "Deck themes" in DECK_SYSTEM_PROMPT
    assert "write its exact name into the frontmatter theme key" in DECK_SYSTEM_PROMPT


def test_theme_values_stay_consistent_with_the_theme_list() -> None:
    # the opt-in custom theme is allowed in the frontmatter and the task
    # input, but it is NOT a MARP_THEMES entry ('auto' never offers it)
    marp_names = tuple(name for name, _description in MARP_THEMES)
    assert (*marp_names, THEME_CUSTOM) == ALLOWED_THEMES
    assert THEME_CUSTOM not in marp_names
    assert (THEME_AUTO, *ALLOWED_THEMES) == THEME_VALUES
    # the instance-branded theme is a first-class member: the default task
    # input and the generated theme (theming.THEME_NAME) both name it
    assert THEME_NEXTCLOUD in ALLOWED_THEMES
    from marp_slides.theming import THEME_NAME

    assert THEME_NAME == THEME_NEXTCLOUD


def test_auto_prompt_carries_no_override_clause() -> None:
    assert "requires the" not in build_llm_prompt("a deck about bees")


def test_override_prompt_states_the_requirement_after_the_rules() -> None:
    prompt = build_llm_prompt("a deck about bees", "gaia")
    assert "requires the 'gaia' theme" in prompt
    assert "theme: gaia" in prompt
    # the requirement lands after the theme list it overrides
    assert prompt.index("requires the 'gaia' theme") > prompt.index("Deck themes")


def test_custom_theme_is_an_accepted_input_and_matches_the_module_name() -> None:
    """'custom' is a theme ENUM value; its name is the one the custom
    theme CSS registers under --theme-set (custom_theme.CUSTOM_THEME_NAME)."""
    from marp_slides.custom_theme import CUSTOM_THEME_NAME

    assert validate_theme("custom") == THEME_CUSTOM == CUSTOM_THEME_NAME
    assert THEME_CUSTOM in THEME_VALUES


def test_system_prompt_never_offers_the_custom_theme_to_auto() -> None:
    assert f"- {THEME_CUSTOM}:" not in DECK_SYSTEM_PROMPT
