# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Design tokens v1: a small JSON file in the task owner's Files that
re-brands the generated ``nextcloud`` theme.

The org-branding scenario: an organization wants its decks in ITS
colors and font without the instance admin repainting the whole
Nextcloud. The owner drops a tokens file at a conventional path
(default ``SlidePrepper/design-tokens.json``, admin-adjustable through
``SLIDEPREPPER_DESIGN_TOKENS_PATH``), and every deck that owner
generates (authoring task, chat tool schedule and re-render alike: they
all resolve the branded theme through the same
:func:`marp_slides.task_worker._resolve_theming_values` seam) carries
the token branding INSTEAD of the instance theming: the file replaces
instance-derived branding, it does not merge with it.

v1 is deliberately shallow: two colors and one font family, exactly the
branding surface the generated theme CSS parameterizes today (see
:func:`marp_slides.theming.generate_theme_css`). No raw CSS, no sizes,
no spacing: slide geometry stays template-fixed so the per-slide
overflow guarantee survives custom branding. A token font resolves BY
FAMILY NAME through CSS with the bundled Inter as the fallback; no font
file is fetched (a v1 limitation: a font the render environment does
not have installed silently falls through to Inter).

Fail-soft by contract, because a branding file must never fail a deck:

- a MISSING file is exactly today's behavior: the instance theming (or
  its stock defaults) applies, one DEBUG log line;
- a MALFORMED file (not JSON, not an object, unknown keys, invalid
  values, oversize) is ignored the same way with one WARNING naming the
  file and the precise parse error, and the instance theming applies;
- a DAV read error (no impersonation, unreachable namespace) degrades
  like a missing file: DEBUG, never a task failure.

The schema validates STRICTLY (:func:`parse_design_tokens`): unknown
keys are a file error, so a typo'd key cannot silently no-op. The same
strictness is the injection guard: a font family name may only carry
characters that are inert both inside the CSS string it is interpolated
into (no quotes, no backslash) and inside a ``<style>`` element of the
HTML deliverable (no angle brackets), so a tokens file can smuggle
neither CSS nor HTML.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any

from .theming import THEMING_DEFAULTS, ThemingValues

LOGGER = logging.getLogger("marp_slides.design_tokens")

#: The token keys of schema v1, derived from what the theme CSS
#: parameterizes: the slide background color, its paired text color and
#: the slide font family. Nothing else is steerable in v1.
TOKEN_KEYS = ("color", "color_text", "font_family")

#: Raw-byte cap for the tokens file. Three keys never need more than a
#: few hundred bytes; anything past this cap is a wrong file (or a
#: hostile one) and is rejected as malformed rather than parsed.
MAX_TOKENS_BYTES = 64 * 1024

#: Strict color format: ``#RRGGBB`` or the ``#RGB`` short form, both
#: case-insensitive (the same shapes :func:`marp_slides.theming._rgb`
#: widens). Everything else ('red', 'rgb(...)', a bare int) is a file
#: error.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

#: ``#RGB`` short-form digit count (the other length the color parser
#: accepts besides the 6-digit ``#RRGGBB``).
_SHORT_HEX_DIGITS = 3

#: The control-character bounds a font family name rejects: C0 controls
#: (below 32) and DEL (127).
_CONTROL_CHAR_MAX = 32
_DEL_CHAR = 127

#: Font family name cap: real family names are short ("Atkinson
#: Hyperlegible", "Noto Sans"); the cap keeps a pathological blob from
#: riding the theme CSS.
_MAX_FONT_FAMILY_CHARS = 128

#: Characters a font family name must never carry. Double quote and
#: backslash break out of the quoted CSS string the name is interpolated
#: into; angle brackets would close the ``<style>`` element of the
#: standalone HTML deliverable (HTML parsing inside <style> does not
#: respect CSS string quoting); braces and semicolon are inert there
#: but belong to no family name, so they are rejected on the same
#: whitelist discipline. Control characters are rejected outright.
_FORBIDDEN_FONT_CHARS = set('"\\`<>{};')


class DesignTokensError(ValueError):
    """A design tokens file failed strict schema validation; the message
    is precise (it names the offending key/value) and safe to log."""


@dataclass(frozen=True)
class DesignTokens:
    """The validated v1 tokens: the branding values a tokens file
    contributes. Every field is always set: an absent key in the file
    falls back to the stock theming default (never the instance
    theming: the file replaces instance-derived branding, so a partial
    file is partial STOCK branding, not a mix of two sources)."""

    color: str
    color_text: str
    font_family: str

    def overlay(self, base: ThemingValues) -> ThemingValues:
        """Apply the tokens OVER the instance-derived values: the token
        branding wins, and every instance-specific field the CSS does
        not consume is cleared rather than leaked (an instance
        background photo behind org colors, or an instance slogan in an
        org deck, would be a branding bug if a future consumer ever
        surfaced them). The unused element-color fields go token-coherent
        for the same reason. Returns a new values object; ``base`` is
        untouched."""
        return replace(
            base,
            color=self.color,
            color_text=self.color_text,
            color_element_bright=self.color,
            color_element_dark=self.color,
            background="",  # file branding carries no background image
            background_text=self.color_text,
            slogan="",
            name="",
            cache_buster="0",  # nothing left to cache-bust
            font_family=self.font_family,
        )


def parse_design_tokens(document: object) -> DesignTokens:
    """Strictly validate a parsed tokens document and build the
    :class:`DesignTokens`.

    Raises :class:`DesignTokensError` for everything outside the v1
    schema: a non-object root, unknown keys (listed in the message, so a
    typo names itself), malformed colors, or a font family name that is
    empty, oversize, or carries characters forbidden by the injection
    guard. Colors normalize to the lowercase 6-digit form (the short
    ``#RGB`` form widens) so the generated CSS carries one shape.
    """
    if not isinstance(document, dict):
        raise DesignTokensError(
            f"the design tokens file must contain a JSON object, got "
            f"{type(document).__name__}"
        )
    unknown = sorted(str(key) for key in document if key not in TOKEN_KEYS)
    if unknown:
        raise DesignTokensError(
            f"unknown design token key(s) {', '.join(unknown)}; the v1 "
            f"schema accepts exactly {', '.join(TOKEN_KEYS)}"
        )
    color = _parse_color(document.get("color"), "color", THEMING_DEFAULTS["color"])
    color_text = _parse_color(
        document.get("color_text"), "color_text", THEMING_DEFAULTS["color-text"]
    )
    font_family = _parse_font_family(document.get("font_family"))
    return DesignTokens(color=color, color_text=color_text, font_family=font_family)


def _parse_color(value: object, key: str, default: str) -> str:
    """One token color: ``#RRGGBB``/``#RGB``, normalized to lowercase
    6-digit; absent keeps the stock default; anything else is a file
    error naming the key and the value."""
    if value is None:
        return default
    if not isinstance(value, str) or _HEX_COLOR_RE.match(value.strip()) is None:
        raise DesignTokensError(
            f"the design token {key!r} must be a '#RRGGBB' (or '#RGB') "
            f"hex color string, got {value!r}"
        )
    digits = value.strip().removeprefix("#").lower()
    if len(digits) == _SHORT_HEX_DIGITS:
        digits = "".join(ch * 2 for ch in digits)
    return f"#{digits}"


def _parse_font_family(value: object) -> str:
    """The token font family: a non-empty name (after stripping) of at
    most :data:`_MAX_FONT_FAMILY_CHARS` characters, none of them in the
    injection-guard set and none a control character; absent keeps the
    stock Inter-first stack (represented as the empty string)."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DesignTokensError(
            f"the design token 'font_family' must be a font family name "
            f"string, got {value!r}"
        )
    name = value.strip()
    if not name or len(name) > _MAX_FONT_FAMILY_CHARS:
        raise DesignTokensError(
            f"the design token 'font_family' must be a font family name "
            f"of 1 to {_MAX_FONT_FAMILY_CHARS} characters"
        )
    for char in name:
        if (
            char in _FORBIDDEN_FONT_CHARS
            or ord(char) < _CONTROL_CHAR_MAX
            or ord(char) == _DEL_CHAR
        ):
            raise DesignTokensError(
                f"the design token 'font_family' {name!r} carries a "
                f"character family names cannot have ({char!r}); quotes, "
                "backslashes, angle brackets, braces and semicolons are "
                "rejected on purpose"
            )
    return name


def read_design_tokens(nc: Any, path: str) -> DesignTokens | None:
    """Read the task owner's tokens file over DAV and validate it;
    fail-soft by the module contract (None on any problem, never a
    raise).

    Runs under the TASK'S AMBIENT DAV IMPERSONATION: ``process_task``
    stamps the task owner on the session before the pipeline starts and
    nothing inside a task body re-points it, so a plain ``by_path``
    lookup resolves inside the owner's Files without threading the user
    id through every measurement helper. A session without
    impersonation (or a DAV outage) fails like a missing file: DEBUG,
    instance theming, deck proceeds.

    The read is deliberately UNCACHED: the theming seam re-resolves per
    measurement (~a dozen lookups per task), which costs milliseconds
    of DAV against a minutes-long task and buys exact freshness: an
    owner's file edit applies to the very next measurement, and a
    deleted file falls back with no TTL window to wait out.
    """
    if not path.strip():
        LOGGER.debug("no design tokens path configured; instance theming applies")
        return None
    files = getattr(nc, "files", None)
    by_path = getattr(files, "by_path", None)
    download = getattr(files, "download", None)
    if by_path is None or download is None:
        LOGGER.debug("no Files API available; design tokens do not apply")
        return None
    try:
        node = by_path(path)
    except Exception as exc:  # noqa: BLE001 - an unreadable namespace is an absent file
        LOGGER.debug(
            "design tokens lookup at '%s' failed (%s); instance theming applies",
            path,
            exc,
        )
        return None
    if node is None or getattr(node, "is_dir", False):
        LOGGER.debug("no design tokens file at '%s'; instance theming applies", path)
        return None
    try:
        data = download(node)
    except Exception as exc:  # noqa: BLE001 - a branding file must never fail a task
        LOGGER.warning(
            "the design tokens file '%s' could not be read (%s); the "
            "instance theming applies",
            path,
            exc,
        )
        return None
    return _tokens_from_bytes(data, path)


def _tokens_from_bytes(data: bytes, path: str) -> DesignTokens | None:
    """Decode and strictly validate the downloaded tokens bytes; the
    malformed paths (oversize, not UTF-8/JSON, schema violation) each
    log one WARNING naming the file and the precise reason and return
    None so the caller's fail-soft contract holds."""
    if len(data) > MAX_TOKENS_BYTES:
        LOGGER.warning(
            "the design tokens file '%s' exceeds the %d-byte cap and is "
            "ignored; the instance theming applies",
            path,
            MAX_TOKENS_BYTES,
        )
        return None
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.warning(
            "the design tokens file '%s' is not valid JSON and is ignored "
            "(%s); the instance theming applies",
            path,
            exc,
        )
        return None
    try:
        return parse_design_tokens(document)
    except DesignTokensError as exc:
        LOGGER.warning(
            "the design tokens file '%s' is ignored (%s); the instance theming applies",
            path,
            exc,
        )
        return None
