# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Custom Marp theme CSS from the task owner's Files, and its probe deck.

A power user can bring their own Marp theme: a CSS file at a conventional
path in their Files (default ``SlidePrepper/custom-theme.css``,
admin-adjustable through ``SLIDEPREPPER_CUSTOM_THEME_PATH``), applied when
a task asks for the ``custom`` theme. Unlike the design tokens (which only
recolor the generated ``nextcloud`` theme and keep its geometry
template-fixed), a custom theme CAN change slide geometry: font sizes,
padding, line height. The per-slide overflow guarantee therefore needs a
gatekeeper before the theme is ever used, and this module owns its two
static inputs:

- :data:`PROBE_DECK`, a deliberately dense deck at the limits the
  authoring prompts teach (a custom theme gets the default theme's
  density teaching, so it must fit what that teaching produces): long
  single-line bullets, a nested list under a subheading, a full code
  block, a table with full-width rows, long inline-code runs and a
  blockquote, and a slide of running prose at maximum character density.
  The gate itself (render + Chromium measurement + verdict cache) lives in
  :mod:`marp_slides.deck_pipeline`, the facade allowed to drive marp.
- :func:`parse_custom_theme`, the deterministic static lint that runs
  BEFORE any rendering. The CSS is rendered server-side by marp-cli and
  the bundled Chromium (the gate, the measurements, the PDF), so every
  reference that could make the ExApp container fetch something is
  rejected: a ``url(...)`` must be a ``data:`` URI, ``@import`` may only
  name a theme bundled with Marp, and ``image-set()`` (whose bare strings
  are URLs) is refused outright. The checks run on a normalized copy
  (comments dropped, CSS escapes decoded, lowercased) so an escaped
  ``\\75 rl(`` cannot slip past. A ``</style`` sequence is refused on the
  raw text, because marp embeds the theme in a ``<style>`` element of the
  standalone HTML deliverable and the HTML parser ends that element there
  regardless of CSS quoting or comments.

The theme name is normalized, not trusted: every ``@theme <name>``
metadata token is rewritten to ``@theme custom`` (and one is prepended
when the file has none), so a deck's ``theme: custom`` frontmatter always
resolves to exactly this CSS through ``--theme-set``, whatever the file
called itself.

The DAV read (:func:`read_custom_theme`) mirrors the design tokens read:
it runs under the task's ambient impersonation, a missing file is None
(the caller falls back to the default theme with a WARNING), and an
oversize node is refused before the download. A file that fails the lint
raises :class:`CustomThemeError` with the precise reason; the worker logs
it as a WARNING and falls back to the default theme, exactly like a probe
gate rejection. A generation task can also name a theme file PER DECK
through the optional ``custom_theme_file`` input (a picked file id, read
by :func:`read_custom_theme_by_id` through the same lint): the picked
file overrides the conventional path for that one task, and an unusable
pick falls back to the conventional chain rather than failing the deck.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any

from .layout import SlideOverflow
from .theming import ThemingValues
from .validator import DeckError

LOGGER = logging.getLogger("marp_slides.custom_theme")

#: The frontmatter theme name a custom theme deck carries, and the name
#: the normalized CSS registers under ``--theme-set``.
CUSTOM_THEME_NAME = "custom"

#: Knob values that disable custom theme discovery instance-wide (the same
#: off switch the design tokens path honors). Case-insensitive.
DISABLED_CUSTOM_THEME_PATHS = ("-", "off")

#: Raw-byte cap for the theme file. The CSS rides inside every standalone
#: HTML deliverable, so embedded fonts or images as data URIs must stay
#: bounded; a real theme without embedded assets is a few kilobytes.
MAX_CUSTOM_THEME_BYTES = 1024 * 1024

#: The bundled marp-core themes a custom theme may ``@import`` (marp
#: resolves these by name, no fetch involved).
IMPORTABLE_THEMES = ("default", "gaia", "uncover")

#: The workdir file name the normalized CSS is written to.
_CSS_FILE_NAME = f"{CUSTOM_THEME_NAME}-theme.css"

#: Offending snippets in rejection messages are clipped to this length.
_SNIPPET_CHARS = 60

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_HEX_ESCAPE_RE = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?")
_CHAR_ESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)
_URL_RE = re.compile(r"url\(\s*([\"']?)\s*([^\"')\s]*)")
_IMPORT_RE = re.compile(r"@import\b([^;]*);?")
_ALLOWED_IMPORT_RE = re.compile(
    r"\s*([\"'])(" + "|".join(IMPORTABLE_THEMES) + r")\1\s*",
)
_THEME_META_RE = re.compile(r"(@theme\s+)[^\s*]+")
_MAX_CODEPOINT = 0x10FFFF

#: The probe's running-prose paragraphs, each ONE markdown source line:
#: marp-core renders soft line breaks as hard ``<br>`` breaks, so wrapping
#: them in the source would change the probe's geometry.
_PROSE_ONE = (
    "This slide carries plain paragraph text at the density the authoring "
    "rules allow. It is written as running prose instead of bullets, so "
    "every wrapped line counts against the safe area directly. A theme "
    "with larger type, wider letter spacing or heavier padding turns these "
    "lines into more lines than the slide can hold, and the measured "
    "overflow names this paragraph as the first offender."
)
_PROSE_TWO = (
    "The second paragraph adds the remaining budget. It keeps the total "
    "close to nine rendered lines at the default metrics, which is the "
    "ceiling the density teaching promises the authoring model."
)

#: The probe deck: six slides at the limits the default density teaching
#: allows (about nine single lines of about 70 characters per slide; see
#: :data:`marp_slides.outline.THEME_DENSITY`). Measured in the
#: marpteam/marp-cli 4.5.1 image under the default theme at 0.78-0.96 of
#: the 563 px safe area per slide, so a theme that only recolors or
#: re-fonts the default passes, while one that enlarges the type, the
#: padding or the line height pushes at least one slide more than one
#: line past its box. Headers and footers are deliberately absent: they
#: sit inside the slide padding and the measurement would flag them on a
#: stock theme too. Pinned by tests/test_custom_theme.py: an edit here
#: changes what every custom theme is held to and must be deliberate.
PROBE_DECK = f"""---
marp: true
theme: {CUSTOM_THEME_NAME}
paginate: true
---

# Dense bullets at the teaching limit

- Every bullet here is written to the full single-line budget of the deck
- Authors are taught about seventy characters per line and four to six
- A theme that widens the type or the padding wraps these into two lines
- Wrapped bullets cost a second line each and push the slide past its box
- The probe holds the line count at the limit the density teaching sets
- Bold **emphasis**, *italics* and `inline code` ride the same line budget

---

## Nested lists with a subheading

### Planning the rollout in three phases

- Phase one prepares the pilot group and the support channel
  - Nested items indent one level and keep the same line budget
  - A second nested item checks the indentation geometry again
- Phase two widens the audience to every department on the list
  1. Numbered children use the ordered list marker geometry
  2. A second numbered child closes the nested ordered list

---

## A code block at the teaching limit

```python
def measure_deck(markdown: str, workdir: Path) -> list[Report]:
    \"\"\"Render the deck, measure every slide, report overflow.\"\"\"
    html = render_html(markdown, workdir, strict=True)
    page = inject_measurement_script(html, workdir)
    dump = run_headless_chromium(page, budget_ms=2500)
    reports = parse_marker_payload(dump)
    return [r for r in reports if r.overflow_y_px > r.line_px]
```

The code block carries seven lines of about sixty-eight characters.

---

## A table with full-width rows

| Phase | Owner | Outcome |
| ----- | ----- | ------- |
| Pilot | Platform team | Support channel and the feedback loop in place |
| Rollout | Department leads | Every team migrated with a named contact |
| Review | Steering group | Metrics compared against the pilot baseline |
| Handover | Operations | Runbooks published and the on-call rotation set |

---

## Long inline code runs

Configure `SLIDEPREPPER_OVERFLOW_CHECK=true` and `SLIDEPREPPER_MAX_SLIDES=60` together.

- Paths like `SlidePrepper/design-tokens.json` and `SlidePrepper/custom-theme.css`
- A call such as `measure_deck_overflow(markdown, workdir, theming_values=values)`
- Mixed runs: `marp --theme-set custom.css --no-html` then `chromium --headless`

> A quote line closes the slide and checks the blockquote geometry.

---

## Maximum character density

{_PROSE_ONE}

{_PROSE_TWO}
"""

#: What each probe slide exercises, in deck order: rejection messages name
#: the slide AND its content pattern so the theme author knows which rule
#: of their CSS to loosen.
PROBE_SLIDE_PATTERNS = (
    "long single-line bullets",
    "a nested list under a subheading",
    "a full code block",
    "a table with full-width rows",
    "long inline-code runs and a blockquote",
    "running prose at maximum character density",
)


class CustomThemeError(DeckError):
    """A custom theme file was refused (static lint or the probe-deck
    geometry gate). The message names the file and the precise reason and
    is safe to surface to the task owner."""


@dataclass(frozen=True)
class CustomTheme:
    """A custom theme that passed the static lint: the normalized CSS
    (registered as ``@theme custom``) and the SHA-256 of the file's raw
    bytes, the gatekeeper's cache key. ``path`` is the owner-relative
    path it was read from, for messages and logs only."""

    css: str
    digest: str
    path: str


#: What rides the marp conversion's theme slot: the generated branded
#: theme's values, or a custom theme's CSS. Each applies only to a deck
#: whose frontmatter theme names it (``nextcloud`` / ``custom``).
ThemeSource = ThemingValues | CustomTheme


def parse_custom_theme(data: bytes, path: str) -> CustomTheme:
    """Lint and normalize a custom theme file's raw bytes into a
    :class:`CustomTheme`, or raise :class:`CustomThemeError` naming the
    file and the first violation. Deterministic and render-free: this runs
    before the probe deck ever touches marp."""
    if len(data) > MAX_CUSTOM_THEME_BYTES:
        raise _oversize(path, len(data))
    try:
        css = data.decode("utf-8").removeprefix("\ufeff")
    except UnicodeDecodeError as exc:
        raise _rejected(path, f"it is not UTF-8 text ({exc.reason})") from exc
    if not css.strip():
        raise _rejected(path, "it is empty")
    if "</style" in css.lower():
        raise _rejected(
            path,
            "it contains a '</style' sequence, which would end the theme's "
            "<style> element in the HTML deliverable",
        )
    _lint_references(_normalized(css), path)
    return CustomTheme(
        css=_normalize_theme_name(css),
        digest=hashlib.sha256(data).hexdigest(),
        path=path,
    )


def write_custom_theme_css(theme: CustomTheme, workdir: str | Path) -> Path:
    """Write the normalized custom theme CSS into the render workdir and
    return its path (for marp-cli's ``--theme-set``), the same file-on-disk
    mechanism the generated ``nextcloud`` theme uses. No CSS is ever
    inlined into a deliverable by this app: marp embeds the theme."""
    css_path = Path(workdir) / _CSS_FILE_NAME
    css_path.write_text(theme.css, encoding="utf-8")
    return css_path


def read_custom_theme(nc: Any, path: str) -> CustomTheme | None:
    """Read and lint the task owner's custom theme file over DAV.

    Returns None when there is no usable file (no path, the off switch, no
    Files API, a lookup failure, no node, a directory, or a failed
    download): the caller falls back to the default theme. Returns the
    linted :class:`CustomTheme` when the file passes
    :func:`parse_custom_theme`, and raises :class:`CustomThemeError`
    (naming the file and the reason) when it does not, so the caller can
    report exactly why the file was not used.

    Runs under the task's ambient DAV impersonation, exactly like the
    design tokens read. An oversize node (by its reported size) is refused
    BEFORE the download, because ``download`` buffers the whole file into
    worker memory; the post-download length check in
    :func:`parse_custom_theme` is the backstop for a missing or lying size
    report."""
    normalized = path.strip()
    if not normalized or normalized.lower() in DISABLED_CUSTOM_THEME_PATHS:
        LOGGER.debug("custom theme discovery is disabled ('%s')", path)
        return None
    files = getattr(nc, "files", None)
    by_path = getattr(files, "by_path", None)
    download = getattr(files, "download", None)
    if by_path is None or download is None:
        LOGGER.debug("no Files API available; no custom theme")
        return None
    try:
        node = by_path(normalized)
    except Exception as exc:  # noqa: BLE001 - an unreadable namespace is an absent file
        LOGGER.debug("custom theme lookup at '%s' failed (%s)", normalized, exc)
        return None
    if node is None or getattr(node, "is_dir", False):
        LOGGER.debug("no custom theme file at '%s'", normalized)
        return None
    data = _download_theme_node(download, node, normalized)
    return None if data is None else parse_custom_theme(data, normalized)


def read_custom_theme_by_id(nc: Any, file_id: int) -> CustomTheme | None:
    """Read and lint a PICKED custom theme file by its Nextcloud file id
    (the generation task's optional ``custom_theme_file`` input): the
    same caps, the same download seam and the same static lint as
    :func:`read_custom_theme`, differing only in how the node is found
    (``by_id`` for an id the server already resolved and access-checked
    at schedule time, not ``by_path`` under the ambient impersonation).

    The return contract mirrors the by-path read exactly: None when
    there is no usable file (an unresolvable id, a folder, or a failed
    download, each with one WARNING naming the pick, because a picked
    file is EXPECTED to exist), and :class:`CustomThemeError` naming the
    picked file and the reason when the content fails the lint. The
    returned theme's ``path`` field carries the picked file's label (its
    Files path, its name, or a file-id fallback) for messages and
    logs."""
    files = getattr(nc, "files", None)
    by_id = getattr(files, "by_id", None)
    download = getattr(files, "download", None)
    if by_id is None or download is None:
        LOGGER.debug("no Files API available; the picked custom theme is skipped")
        return None
    try:
        node = by_id(file_id)
    except Exception as exc:  # noqa: BLE001 - a theme file must never crash a task
        LOGGER.warning(
            "the picked custom theme file (file id %s) could not be looked up (%s)",
            file_id,
            exc,
        )
        return None
    label = _picked_file_label(node, file_id)
    if node is None or getattr(node, "is_dir", False):
        LOGGER.warning(
            "the picked custom theme file (file id %s) is not a readable file",
            file_id,
        )
        return None
    data = _download_theme_node(download, node, label)
    return None if data is None else parse_custom_theme(data, label)


def _picked_file_label(node: Any, file_id: int) -> str:
    """The label naming a picked file in messages: its Files path when
    the node exposes one, its name otherwise, a file-id fallback when
    even those are absent."""
    user_path = str(getattr(node, "user_path", "") or "").strip().strip("/")
    if user_path:
        return user_path
    name = str(getattr(node, "name", "") or "").strip()
    return name or f"file id {file_id}"


def _download_theme_node(download: Any, node: Any, label: str) -> bytes | None:
    """The shared read tail of both theme readers: the pre-download size
    cap (an oversize node raises, naming the file, exactly like the
    by-path read) and the download itself (None with one WARNING on a
    failed read)."""
    reported = getattr(getattr(node, "info", None), "content_length", None)
    if isinstance(reported, int) and reported > MAX_CUSTOM_THEME_BYTES:
        raise _oversize(label, reported)
    try:
        return bytes(download(node))
    except Exception as exc:  # noqa: BLE001 - a theme file must never crash a task
        LOGGER.warning("the custom theme file '%s' could not be read (%s)", label, exc)
        return None


def probe_blocking(reports: Sequence[SlideOverflow]) -> tuple[SlideOverflow, ...]:
    """The probe slides that fail the gate: overflow beyond ONE rendered
    line, the same one-line grace the per-slide gate and the deck
    backstop apply vertically (sub-line overflow is invisible). Unlike
    those gates the probe also blocks on the horizontal axis with the same
    grace: an authored deck can be re-asked to shorten a wide line, but a
    theme that clips stock-width content sideways is the theme's fault."""
    return tuple(
        r for r in reports if r.overflow_y_px > r.line_px or r.overflow_x_px > r.line_px
    )


def probe_rejection(path: str, blocking: Sequence[SlideOverflow]) -> CustomThemeError:
    """The gate's rejection for a theme whose probe deck overflows: names
    every blocking probe slide with the content pattern it exercises, the
    measured overflow and the first overflowing element, and says what
    to change in the CSS."""
    listed = "\n".join(_probe_line(report) for report in blocking)
    return _rejected(
        path,
        "normal content does not fit its slides. Under this theme the probe "
        "deck (content at the density the authoring rules teach for the "
        f"default theme) overflows:\n{listed}\nReduce the theme's font-size, "
        "padding or line-height until this content fits",
    )


def _probe_line(report: SlideOverflow) -> str:
    lines = max(1, ceil(report.overflow_y_px / max(1, report.line_px)))
    pattern = (
        PROBE_SLIDE_PATTERNS[report.slide - 1]
        if 1 <= report.slide <= len(PROBE_SLIDE_PATTERNS)
        else "probe content"
    )
    line = (
        f"- probe slide {report.slide} ({pattern}): {report.content_h_px} px "
        f"of content in a {report.safe_px[1]} px safe area, "
        f"{report.overflow_y_px} px over (about {lines} lines, "
        f"{report.fill_ratio:.2f}x full)"
    )
    if report.overflow_x_px > 0:
        line += f", and {report.overflow_x_px} px wider than the safe area"
    if report.first_offender is not None:
        tag, text = report.first_offender
        line += f"; the first overflowing element is a <{tag}> '{_snippet(text)}'"
    return line


def _rejected(path: str, reason: str) -> CustomThemeError:
    return CustomThemeError(
        f"The custom theme file '{path}' was rejected: {reason}. Fix the file "
        "or choose another theme."
    )


def _oversize(path: str, size: int) -> CustomThemeError:
    return _rejected(
        path, f"it is {size} bytes and the cap is {MAX_CUSTOM_THEME_BYTES}"
    )


def _decode_escape(match: re.Match[str]) -> str:
    codepoint = int(match.group(1), 16)
    if codepoint == 0 or codepoint > _MAX_CODEPOINT:
        return "\ufffd"
    return chr(codepoint)


def _normalized(css: str) -> str:
    """The lint view of the CSS: comments dropped (CSS comments do not
    nest, so the non-greedy match is exact), escapes decoded the way the
    CSS tokenizer decodes them (hex escapes with their optional trailing
    whitespace, then single-character escapes), lowercased."""
    text = _COMMENT_RE.sub(" ", css)
    text = _HEX_ESCAPE_RE.sub(_decode_escape, text)
    text = _CHAR_ESCAPE_RE.sub(lambda m: m.group(1), text)
    return text.lower()


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > _SNIPPET_CHARS:
        return f"{text[: _SNIPPET_CHARS - 3]}..."
    return text


def _lint_references(normalized: str, path: str) -> None:
    """Refuse every construct that makes the server-side renderer fetch:
    non-data ``url()`` targets, ``@import`` of anything but a bundled
    theme name, and ``image-set()``."""
    for match in _URL_RE.finditer(normalized):
        if not match.group(2).startswith("data:"):
            raise _rejected(
                path,
                f"'{_snippet(match.group(0))}' references an external resource; "
                "only data: URIs are allowed in url()",
            )
    for match in _IMPORT_RE.finditer(normalized):
        if not _ALLOWED_IMPORT_RE.fullmatch(match.group(1)):
            raise _rejected(
                path,
                f"'{_snippet(match.group(0))}' is not allowed; @import may only "
                f"name a theme bundled with Marp ({', '.join(IMPORTABLE_THEMES)}), "
                'for example @import "default";',
            )
    if "image-set(" in normalized:
        raise _rejected(
            path,
            "image-set() is not allowed (its entries are fetched as URLs); "
            "use a single url(data:...) instead",
        )


def _normalize_theme_name(css: str) -> str:
    """Register the CSS as ``@theme custom``: rewrite every ``@theme``
    metadata token, or prepend one when the file declares none."""
    rewritten, count = _THEME_META_RE.subn(rf"\g<1>{CUSTOM_THEME_NAME}", css)
    if count:
        return rewritten
    return f"/* @theme {CUSTOM_THEME_NAME} */\n{css}"
