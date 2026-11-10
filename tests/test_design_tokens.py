# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Design tokens v1: the strict schema (accept/reject/unknown keys), the
fail-soft owner-Files discovery (present/absent/malformed), the overlay
that replaces instance branding, and the guarantee that token values
steer the generated CSS while the template-fixed geometry stays
untouched."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from marp_slides.design_tokens import (
    DISABLED_TOKENS_PATHS,
    MAX_TOKENS_BYTES,
    TOKEN_KEYS,
    DesignTokensError,
    parse_design_tokens,
    read_design_tokens,
)
from marp_slides.settings import Settings
from marp_slides.theming import (
    WCAG_LARGE_TEXT_CONTRAST_FLOOR,
    ThemingValues,
    contrast_ratio,
    generate_theme_css,
)

#: A full valid v1 file, the shape the README documents.
FULL_FILE = {
    "color": "#3a1b5d",
    "color_text": "#f5f0e8",
    "font_family": "IBM Plex Sans",
}


#: An instance-branded base for the overlay tests: every field set to
#: something the tokens must replace or clear.
def _instance_values() -> ThemingValues:
    return ThemingValues.from_capabilities(
        {
            "color": "#0082c9",
            "color-text": "#ffffff",
            "color-element-bright": "#0073bc",
            "color-element-dark": "#004a70",
            "background": "http://nextcloud.local:8350/apps/theming/img/x.webp",
            "background-text": "#ffffff",
            "slogan": "a safe home for all your data",
            "name": "Nextcloud",
            "cacheBuster": "1d9e0e2e",
        }
    )


# --- schema: accept ---------------------------------------------------------------


def test_full_file_parses() -> None:
    tokens = parse_design_tokens(dict(FULL_FILE))
    assert tokens.color == "#3a1b5d"
    assert tokens.color_text == "#f5f0e8"
    assert tokens.font_family == "IBM Plex Sans"


def test_short_hex_widens_and_case_normalizes() -> None:
    tokens = parse_design_tokens({"color": "#1AF", "color_text": "#FfFfFf"})
    assert tokens.color == "#11aaff"
    assert tokens.color_text == "#ffffff"


def test_partial_file_uses_stock_defaults_not_instance_values() -> None:
    """An absent key never pulls the instance theming in: the file
    replaces instance-derived branding, so the absent color keeps the
    stock default, and the absent text color is derived from the
    effective color (never the instance value, never a blind stock
    white)."""
    tokens = parse_design_tokens({"color": "#123456"})
    assert tokens.color == "#123456"
    assert tokens.color_text == "#ffffff"  # derived: #123456 is dark
    assert tokens.font_family == ""  # the stock Inter-first stack


# --- the absent text color is derived, and bad pairs are visible -------------------


@pytest.mark.parametrize(
    ("color", "expected_text"),
    [
        ("#f5f5f5", "#000000"),  # light brand color: dark text, not stock white
        ("#ffffff", "#000000"),
        ("#1a1a2e", "#ffffff"),  # dark brand color: white text
        ("#3a1b5d", "#ffffff"),
    ],
)
def test_absent_color_text_is_derived_from_luminance(
    color: str, expected_text: str
) -> None:
    """{"color": "#f5f5f5"} is the obvious first file a user writes; the
    stock white pairing would render white on near-white (a deck that
    passes every gate and ships unreadable), so the absent text color is
    derived from the color's luminance exactly like the instance theming
    derives it."""
    tokens = parse_design_tokens({"color": color})
    assert tokens.color_text == expected_text


def test_absent_color_and_absent_color_text_keep_the_stock_pair() -> None:
    """Both keys absent: the stock pair, which the derivation reproduces
    (the stock blue is dark, so the derived text is the stock white)."""
    tokens = parse_design_tokens({})
    assert tokens.color == "#00679e"
    assert tokens.color_text == "#ffffff"


def test_explicit_low_contrast_pair_warns_but_is_honored(caplog) -> None:
    """An explicit pair below the WCAG large-text floor is a deliberate
    branding choice: honored (fail-soft), but one WARNING names the file
    and the measured contrast, because no gate catches readability."""
    blob = json.dumps({"color": "#f5f5f5", "color_text": "#ffffff"}).encode("utf-8")
    files = _FakeFiles({PATH: blob})
    with caplog.at_level(logging.WARNING, logger="marp_slides.design_tokens"):
        tokens = read_design_tokens(_nc(files), PATH)
    assert tokens is not None
    assert tokens.color == "#f5f5f5"  # honored, not rejected
    assert tokens.color_text == "#ffffff"
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "hard to read" in m and PATH in m and "contrast" in m for m in messages
    ), messages


def test_derived_pairing_always_clears_the_floor() -> None:
    """The derivation picks whichever of black/white contrasts more, so
    no absent-color_text file can produce an unreadable pair (the
    minimum possible contrast of that pick is ~4.5:1, past the 3:1
    floor): white-on-near-white needs an EXPLICIT color_text."""
    for color in ("#ffffff", "#f5f5f5", "#c0c0c0", "#808080", "#404040", "#000000"):
        tokens = parse_design_tokens({"color": color})
        assert tokens.color_text in ("#000000", "#ffffff")
        assert contrast_ratio(tokens.color, tokens.color_text) >= (
            WCAG_LARGE_TEXT_CONTRAST_FLOOR
        ), color


def test_readable_pair_does_not_warn(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="marp_slides.design_tokens"):
        tokens = read_design_tokens(
            _nc(_FakeFiles({PATH: json.dumps(FULL_FILE).encode("utf-8")})), PATH
        )
    assert tokens is not None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" #1aF ", "#11aaff"),  # padded, short form, mixed case
        ("#abc\n", "#aabbcc"),  # trailing newline
        ("\t#ABCDEF  ", "#abcdef"),
    ],
)
def test_whitespace_padded_colors_normalize(raw: str, expected: str) -> None:
    tokens = parse_design_tokens({"color": raw})
    assert tokens.color == expected


@pytest.mark.parametrize("name", ["Atkinson Hyperlegible", "Noto Sans", "Boss's New"])
def test_font_family_accepts_real_world_names(name: str) -> None:
    """Spaces, periods, apostrophes and mixed case are the alphabet real
    family names live in; the guard only rejects what could inject."""
    assert parse_design_tokens({"font_family": name}).font_family == name


# --- schema: reject ----------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        [],  # not an object
        "a string",
        42,
        {**FULL_FILE, "primary": "#000000"},  # unknown key
        {**FULL_FILE, "colour": "#000000"},  # the typo the strictness names
    ],
)
def test_non_object_and_unknown_keys_are_file_errors(document: object) -> None:
    with pytest.raises(DesignTokensError):
        parse_design_tokens(document)


def test_unknown_keys_are_listed_in_the_message() -> None:
    with pytest.raises(DesignTokensError, match=r"primary.*secondary"):
        parse_design_tokens({**FULL_FILE, "primary": 1, "secondary": 2})


@pytest.mark.parametrize(
    "value", ["red", "#12345", "#1234567", "rgb(1, 2, 3)", "", 123]
)
def test_invalid_colors_are_file_errors(value: object) -> None:
    with pytest.raises(DesignTokensError, match="'color'"):
        parse_design_tokens({"color": value})


@pytest.mark.parametrize(
    "value",
    [
        "",  # empty
        "   ",  # whitespace only
        '"Quoted"',  # breaks the CSS string
        "back\\slash",  # the CSS escape
        "</style>",  # breaks out of the HTML <style> element
        "a{b}",  # braces
        "a;b",  # semicolon
        "line\nbreak",  # control character
        "x" * 129,  # past the cap
        42,  # not a string
    ],
)
def test_injection_shaped_font_names_are_file_errors(value: object) -> None:
    with pytest.raises(DesignTokensError, match="font_family"):
        parse_design_tokens({"font_family": value})


def test_font_family_at_the_cap_is_accepted() -> None:
    name = "x" * 128
    assert parse_design_tokens({"font_family": name}).font_family == name


# --- overlay: the file replaces instance branding ----------------------------------


def test_overlay_replaces_branding_and_clears_instance_fields() -> None:
    values = parse_design_tokens(dict(FULL_FILE)).overlay(_instance_values())
    assert values.color == "#3a1b5d"
    assert values.color_text == "#f5f0e8"
    assert values.font_family == "IBM Plex Sans"
    # the instance's own branding must not leak into a token-branded deck
    assert values.background == ""  # no instance photo behind org colors
    assert values.background_text == "#f5f0e8"
    assert values.slogan == ""
    assert values.name == ""
    assert values.cache_buster == "0"
    assert values.color_element_bright == "#3a1b5d"
    assert values.color_element_dark == "#3a1b5d"


def test_overlay_leaves_base_untouched() -> None:
    base = _instance_values()
    parse_design_tokens(dict(FULL_FILE)).overlay(base)
    assert base.color == "#0082c9"
    assert base.font_family == ""


# --- the generated CSS: token values steer branding, never geometry -----------------


def test_css_reflects_token_values() -> None:
    values = parse_design_tokens(dict(FULL_FILE)).overlay(_instance_values())
    css = generate_theme_css(values)
    assert "background-color: #3a1b5d;" in css
    assert "color: #f5f0e8;" in css
    assert 'font-family: "IBM Plex Sans", "Inter", -apple-system,' in css
    # the bundled Inter stays embedded: it IS the fallback for the token font
    assert css.count("@font-face {") == 2
    # the instance photo did not survive the overlay
    assert "background-image" not in css


def test_css_changes_only_branding_lines_not_geometry_rules() -> None:
    """Tokens recolor and refont, nothing else: diffing the
    token-branded CSS against the instance-branded one line by line,
    every differing line must be a branding line (color, rgba tint or
    font-family). This is the invariance that matters, not the absence
    of geometry strings: a legitimate future geometry rule may appear in
    BOTH themes, and this check still holds."""
    base = generate_theme_css(_instance_values())
    token = generate_theme_css(
        parse_design_tokens(dict(FULL_FILE)).overlay(_instance_values())
    )
    base_lines, token_lines = base.splitlines(), token.splitlines()
    assert len(base_lines) == len(token_lines)
    branding = ("color", "rgba(", "font-family")
    differing = [
        (b, t)
        for b, t in zip(base_lines, token_lines)
        if b != t and not any(part in b or part in t for part in branding)
    ]
    assert differing == [], differing
    # and something did change (the diff is real, not vacuous)
    assert any(b != t for b, t in zip(base_lines, token_lines))


# --- injection: an accepted hostile name cannot restructure the CSS ---------------


def test_hostile_but_accepted_font_name_stays_an_inert_string() -> None:
    """A name that passes the guard but LOOKS like CSS (comment markers,
    an @theme directive for another theme) must land as one inert quoted
    string: it appears exactly once (the font-family line), the theme
    header stays `@theme nextcloud`, and no `</style` can exist because
    angle brackets reject at parse time. The name cannot re-declare the
    theme, open a comment, or break out of the HTML style element."""
    hostile = "x */ /* @theme gaia */ y"
    tokens = parse_design_tokens({"font_family": hostile})
    css = generate_theme_css(tokens.overlay(_instance_values()))
    assert css.count(f'"{hostile}"') == 1  # exactly once, quoted
    # marp registers the theme from the HEADER comment: it still
    # declares OUR theme, and the "@theme gaia" inside the name exists
    # only within that one quoted string
    assert css.splitlines()[0] == "/*! @theme nextcloud */"
    assert css.count("@theme gaia") == 1
    assert "@theme gaia" in f'"{hostile}"'
    assert "</style" not in css  # no HTML breakout surface at all
    # and the structure is untouched: diffing against a benign font name,
    # only the font-family line differs
    benign = generate_theme_css(
        parse_design_tokens({"font_family": "Safe Name"}).overlay(_instance_values())
    )
    hostile_lines, benign_lines = css.splitlines(), benign.splitlines()
    assert len(hostile_lines) == len(benign_lines)
    differing = [
        (h, b)
        for h, b in zip(hostile_lines, benign_lines)
        if h != b and "font-family" not in h
    ]
    assert differing == [], differing


# --- discovery: fail-soft DAV read ---------------------------------------------------


class _FakeFiles:
    """The DAV surface read_design_tokens touches: by_path/download over
    path-keyed blobs, with realistic node metadata (``info.content_length``,
    the size the pre-download cap check reads) and failure injection."""

    def __init__(
        self,
        blobs: dict[str, bytes] | None = None,
        *,
        lookup_error: Exception | None = None,
        download_error: Exception | None = None,
        reported_sizes: dict[str, int] | None = None,
    ) -> None:
        self._blobs = dict(blobs or {})
        self._reported_sizes = dict(reported_sizes or {})
        self._lookup_error = lookup_error
        self._download_error = download_error
        self.looked_up: list[str] = []
        self.downloaded: list[str] = []

    def by_path(self, path: str):
        self.looked_up.append(path)
        if self._lookup_error is not None:
            raise self._lookup_error
        if path not in self._blobs:
            return None
        size = self._reported_sizes.get(path, len(self._blobs[path]))
        return SimpleNamespace(
            is_dir=False,
            file_id=path,
            info=SimpleNamespace(content_length=size),
        )

    def download(self, node) -> bytes:
        self.downloaded.append(node.file_id)
        if self._download_error is not None:
            raise self._download_error
        return self._blobs[node.file_id]


def _nc(files: object) -> SimpleNamespace:
    return SimpleNamespace(files=files)


PATH = "SlidePrepper/design-tokens.json"


def test_read_returns_validated_tokens() -> None:
    files = _FakeFiles({PATH: json.dumps(FULL_FILE).encode("utf-8")})
    tokens = read_design_tokens(_nc(files), PATH)
    assert tokens == parse_design_tokens(dict(FULL_FILE))


def test_read_missing_file_is_debug_not_warning(caplog) -> None:
    """The common case (no tokens file) must not spam the log: one DEBUG
    line, exactly today's theming behavior."""
    files = _FakeFiles({})
    with caplog.at_level(logging.DEBUG, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None
    messages = [r.getMessage() for r in caplog.records]
    assert any("no design tokens file" in m and PATH in m for m in messages)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_read_directory_node_is_treated_as_absent(caplog) -> None:
    files = _FakeFiles({PATH: b"{}"})
    files.by_path = lambda path: SimpleNamespace(is_dir=True)  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None


def test_read_malformed_json_warns_naming_file_and_error(caplog) -> None:
    files = _FakeFiles({PATH: b"{not json"})
    with caplog.at_level(logging.WARNING, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None
    messages = [r.getMessage() for r in caplog.records]
    assert any("not valid JSON" in m and PATH in m for m in messages), messages


def test_read_schema_violation_warns_naming_file_and_error(caplog) -> None:
    files = _FakeFiles({PATH: json.dumps({"color": "red"}).encode("utf-8")})
    with caplog.at_level(logging.WARNING, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None
    messages = [r.getMessage() for r in caplog.records]
    assert any("ignored" in m and PATH in m and "'color'" in m for m in messages), (
        messages
    )


def test_read_oversize_reported_size_is_refused_before_download(caplog) -> None:
    """The memory guard: the byte cap is enforced on the size the DAV
    node REPORTS, before download() buffers anything, because the worker
    process is shared by every user of the instance. A 2 GiB file at the
    tokens path must cost one PROPFIND plus one WARNING, not 2 GiB of
    resident memory."""
    files = _FakeFiles(
        {PATH: b"x" * (MAX_TOKENS_BYTES + 1)},
        reported_sizes={PATH: MAX_TOKENS_BYTES + 1},
    )
    with caplog.at_level(logging.WARNING, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None
    assert files.downloaded == []  # never fetched
    assert any("the cap is" in r.getMessage() for r in caplog.records)


def test_read_oversize_bytes_hit_the_post_download_backstop(caplog) -> None:
    """A size report that lies (or is absent) still cannot smuggle an
    oversize file through: the downloaded length check is the backstop."""
    files = _FakeFiles({PATH: b"x" * (MAX_TOKENS_BYTES + 1)})
    files._reported_sizes[PATH] = 16  # a lying size report
    with caplog.at_level(logging.WARNING, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None
    assert files.downloaded == [PATH]  # the backstop runs after the fetch
    assert any("the cap is" in r.getMessage() for r in caplog.records)


def test_read_file_of_exactly_the_cap_is_accepted() -> None:
    """The cap is inclusive: a file of exactly MAX_TOKENS_BYTES parses
    (padding whitespace is legal JSON) and is honored."""
    body = b'{"color": "#3a1b5d"}'
    padded = body + b" " * (MAX_TOKENS_BYTES - len(body))
    assert len(padded) == MAX_TOKENS_BYTES
    files = _FakeFiles({PATH: padded})
    tokens = read_design_tokens(_nc(files), PATH)
    assert tokens is not None
    assert tokens.color == "#3a1b5d"


def test_read_file_one_byte_over_the_cap_is_refused(caplog) -> None:
    files = _FakeFiles(
        {PATH: b" " * (MAX_TOKENS_BYTES + 1)},
        reported_sizes={PATH: MAX_TOKENS_BYTES + 1},
    )
    with caplog.at_level(logging.WARNING, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None
    assert files.downloaded == []


def test_read_dav_lookup_error_degrades_to_absent(caplog) -> None:
    """An unreachable namespace (or a session without impersonation)
    must degrade exactly like a missing file: DEBUG, never a raise."""
    files = _FakeFiles({}, lookup_error=RuntimeError("401"))
    with caplog.at_level(logging.DEBUG, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_read_download_error_warns_and_never_raises(caplog) -> None:
    files = _FakeFiles({PATH: b"{}"}, download_error=RuntimeError("DAV down"))
    with caplog.at_level(logging.WARNING, logger="marp_slides.design_tokens"):
        assert read_design_tokens(_nc(files), PATH) is None
    assert any("could not be read" in r.getMessage() for r in caplog.records)


def test_read_without_files_api_is_absent() -> None:
    assert read_design_tokens(SimpleNamespace(files=None), PATH) is None
    assert read_design_tokens(SimpleNamespace(), PATH) is None


def test_read_empty_path_is_a_no_op() -> None:
    files = _FakeFiles({PATH: json.dumps(FULL_FILE).encode("utf-8")})
    assert read_design_tokens(_nc(files), "  ") is None
    assert files.looked_up == []


@pytest.mark.parametrize("sentinel", [*DISABLED_TOKENS_PATHS, "OFF", " Off "])
def test_disabled_sentinels_skip_dav_entirely(sentinel: str) -> None:
    """The instance-wide off switch: '-' or 'off' (case-insensitive)
    means discovery disabled, no by_path at all — a lever for
    organizations whose decks must always carry the instance theming,
    independent of any user's file."""
    files = _FakeFiles({PATH: json.dumps(FULL_FILE).encode("utf-8")})
    assert read_design_tokens(_nc(files), sentinel) is None
    assert files.looked_up == []


def test_disabled_sentinel_reaches_the_read_through_settings() -> None:
    """The knob's '-'/'off' value travels through Settings verbatim (the
    str knobs keep admin text as-is) and lands in the read as disabled."""
    assert Settings(design_tokens_path="-").design_tokens_path == "-"
    assert read_design_tokens(SimpleNamespace(files=None), "-") is None


def test_token_keys_are_exactly_the_v1_schema() -> None:
    """The v1 surface is pinned: two colors and one font, the branding
    axes the generated CSS parameterizes. Growing the schema must be a
    deliberate change to this tuple."""
    assert TOKEN_KEYS == ("color", "color_text", "font_family")
