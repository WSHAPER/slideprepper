# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Custom theme CSS: the static lint, the theme-name normalization, the
fail-soft DAV read, the probe deck, the --theme-set plumbing and the
probe-deck gatekeeper with its digest cache.

Hermetic: the gatekeeper's measurement is faked at the facade's
``measure_deck_overflow`` seam and marp at the runner seam. The one real
render check (a stock-geometry theme passes, an enlarged-type theme is
rejected) is battery-marked: it needs marp-cli and headless Chromium."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from marp_slides import deck_pipeline
from marp_slides.custom_theme import (
    CUSTOM_THEME_NAME,
    MAX_CUSTOM_THEME_BYTES,
    PROBE_DECK,
    PROBE_SLIDE_PATTERNS,
    CustomTheme,
    CustomThemeError,
    parse_custom_theme,
    probe_blocking,
    probe_rejection,
    read_custom_theme,
    write_custom_theme_css,
)
from marp_slides.deck_pipeline import (
    _OverflowMeasurementError,
    gate_custom_theme,
    marp_html_gate,
    render_deck_html,
    render_deck_pdf,
    validate_deck,
)
from marp_slides.layout import SlideOverflow
from marp_slides.theming import ThemingValues

PATH = "SlidePrepper/custom-theme.css"

#: A theme that recolors and re-fonts the default theme: stock geometry.
GOOD_CSS = (
    b'/* @theme corporate */\n@import "default";\n'
    b"section { background: #102030; color: #eeeeee; font-family: serif; }\n"
    b"h1, h2 { color: #f0a020; }\n"
)

#: A theme that enlarges the section type: normal content cannot fit.
BAD_CSS = b'/* @theme huge */\n@import "default";\nsection { font-size: 36px; }\n'


def _theme(css: bytes = GOOD_CSS, path: str = PATH) -> CustomTheme:
    return parse_custom_theme(css, path)


def _report(
    slide: int, overflow_y: int, *, line: int = 44, overflow_x: int = 0
) -> SlideOverflow:
    return SlideOverflow(
        slide=slide,
        theme=CUSTOM_THEME_NAME,
        box_px=(1280, 720),
        safe_px=(1123, 563),
        content_h_px=563 + overflow_y,
        overflow_y_px=overflow_y,
        fill_ratio=(563 + overflow_y) / 563,
        font_px=29,
        line_px=line,
        first_offender=("table", "Phase Owner Outcome"),
        overflow_x_px=overflow_x,
    )


@pytest.fixture(autouse=True)
def _empty_verdict_cache():
    deck_pipeline._CUSTOM_THEME_VERDICTS.clear()
    yield
    deck_pipeline._CUSTOM_THEME_VERDICTS.clear()


# --- the probe deck ------------------------------------------------------------------


def test_probe_deck_is_a_valid_custom_theme_deck() -> None:
    """The probe passes the same strict validator every deck faces, under
    the custom theme name, with one content pattern label per slide."""
    validated = validate_deck(PROBE_DECK, 1_000_000, 60)
    assert validated.frontmatter["theme"] == CUSTOM_THEME_NAME
    assert len(validated.deck.slides) == len(PROBE_SLIDE_PATTERNS) == 6


PROBE_DECK_SHA256 = "bf76a47afe3ddd45a5f480b967aef9d64990b764b35066f450b3f66a9c8a22bc"


def test_probe_deck_is_pinned() -> None:
    """The probe is what every custom theme is held to: it was calibrated
    in the marpteam/marp-cli 4.5.1 image (0.78-0.96 fill per slide under
    the default theme), so any edit must re-calibrate and re-pin."""
    digest = hashlib.sha256(PROBE_DECK.encode("utf-8")).hexdigest()
    assert digest == PROBE_DECK_SHA256


def test_probe_deck_prose_paragraphs_are_single_source_lines() -> None:
    """marp-core renders soft line breaks as <br>: a wrapped prose
    paragraph would change the probe geometry, so each stays one line."""
    prose = [line for line in PROBE_DECK.splitlines() if line.startswith("This ")]
    assert len(prose) == 1
    assert len(prose[0]) > 300


# --- the static lint -----------------------------------------------------------------


def test_good_theme_parses_with_digest_and_path() -> None:
    theme = _theme()
    assert theme.digest == hashlib.sha256(GOOD_CSS).hexdigest()
    assert theme.path == PATH
    assert "section { background: #102030" in theme.css


def test_theme_name_is_rewritten_to_custom() -> None:
    css = _theme().css
    assert "@theme custom" in css
    assert "corporate" not in css


def test_theme_name_is_prepended_when_absent() -> None:
    css = _theme(b"section { color: red; }\n").css
    assert css.startswith("/* @theme custom */\n")
    assert css.endswith("section { color: red; }\n")


def test_bom_is_dropped() -> None:
    css = _theme(b"\xef\xbb\xbfsection { color: red; }").css
    assert "\ufeff" not in css
    assert css == "/* @theme custom */\nsection { color: red; }"


@pytest.mark.parametrize(
    "css",
    [
        b'@import "default";',
        b"@import 'gaia';",
        b'@import "uncover" ;',
        b"section { background: url(data:image/png;base64,AAAA); }",
        b"section { background: url( 'data:image/svg+xml,%3Csvg%3E' ); }",
        b"/* url(https://example.com) inside a comment is inert */ section {}",
        b'section::before { content: "\\201C"; }',
    ],
)
def test_lint_accepts_render_local_css(css: bytes) -> None:
    assert _theme(css).digest


@pytest.mark.parametrize(
    ("css", "reason"),
    [
        (b"section { background: url(https://example.com/x.png); }", "external"),
        (b"section { background: url('http://10.0.0.1/'); }", "external"),
        (b'section { background: url("//evil.example/x"); }', "external"),
        (b"section { background: url(file:///etc/passwd); }", "external"),
        (b"section { background: URL(https://example.com); }", "external"),
        (b"section { background: \\75 rl(http://10.0.0.1/); }", "external"),
        (b"section { background: u\\rl(http://10.0.0.1/); }", "external"),
        (b"@font-face { src: url(https://fonts.example/a.woff2); }", "external"),
        (b'@import url("https://example.com/a.css");', "external"),
        (b'@import "https://example.com/a.css";', "@import"),
        (b'@import "nextcloud";', "@import"),
        (b'@import "custom";', "@import"),
        (b"@\\69mport 'https://example.com/a.css';", "@import"),
        (b'section { background: image-set("a.png" 1x); }', "image-set"),
        (b'section { background: -webkit-image-set("a.png" 1x); }', "image-set"),
        (b"section::after { content: '</style><script>x</script>'; }", "</style"),
        (b"/* </STYLE> in a comment still ends the element */", "</style"),
        (b"", "empty"),
        (b"  \n", "empty"),
        (b"\xff\xfe not utf-8", "UTF-8"),
    ],
)
def test_lint_rejects_with_the_reason(css: bytes, reason: str) -> None:
    with pytest.raises(CustomThemeError, match=reason) as excinfo:
        parse_custom_theme(css, PATH)
    assert f"'{PATH}'" in str(excinfo.value)


def test_lint_rejects_oversize_bytes() -> None:
    with pytest.raises(CustomThemeError, match="bytes and the cap"):
        parse_custom_theme(b" " * (MAX_CUSTOM_THEME_BYTES + 1), PATH)


def test_rejection_is_a_deck_error() -> None:
    """The worker reports DeckErrors to the task owner verbatim."""
    assert issubclass(CustomThemeError, deck_pipeline.DeckError)


def test_write_custom_theme_css(tmp_path: Path) -> None:
    theme = _theme()
    path = write_custom_theme_css(theme, tmp_path)
    assert path == tmp_path / "custom-theme.css"
    assert path.read_text(encoding="utf-8") == theme.css


# --- discovery: the DAV read -------------------------------------------------------


class _FakeFiles:
    """by_path/download over path-keyed blobs with node size metadata and
    failure injection (the design tokens fake's surface)."""

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
            is_dir=False, file_id=path, info=SimpleNamespace(content_length=size)
        )

    def download(self, node) -> bytes:
        self.downloaded.append(node.file_id)
        if self._download_error is not None:
            raise self._download_error
        return self._blobs[node.file_id]


def _nc(files: object) -> SimpleNamespace:
    return SimpleNamespace(files=files)


def test_read_returns_the_linted_theme() -> None:
    assert read_custom_theme(_nc(_FakeFiles({PATH: GOOD_CSS})), PATH) == _theme()


def test_read_missing_file_is_none() -> None:
    assert read_custom_theme(_nc(_FakeFiles()), PATH) is None


@pytest.mark.parametrize("path", ["", "  ", "-", "off", "OFF"])
def test_read_disabled_path_makes_no_dav_call(path: str) -> None:
    files = _FakeFiles({PATH: GOOD_CSS})
    assert read_custom_theme(_nc(files), path) is None
    assert files.looked_up == []


def test_read_lookup_error_is_none() -> None:
    files = _FakeFiles(lookup_error=RuntimeError("no namespace"))
    assert read_custom_theme(_nc(files), PATH) is None


def test_read_download_error_is_none_with_warning(caplog) -> None:
    files = _FakeFiles({PATH: GOOD_CSS}, download_error=RuntimeError("503"))
    with caplog.at_level(logging.WARNING, logger="marp_slides.custom_theme"):
        assert read_custom_theme(_nc(files), PATH) is None
    assert "could not be read" in caplog.text


def test_read_without_files_api_is_none() -> None:
    assert read_custom_theme(SimpleNamespace(files=None), PATH) is None


def test_read_refuses_an_oversize_node_before_the_download() -> None:
    files = _FakeFiles(
        {PATH: GOOD_CSS}, reported_sizes={PATH: MAX_CUSTOM_THEME_BYTES + 1}
    )
    with pytest.raises(CustomThemeError, match="bytes and the cap"):
        read_custom_theme(_nc(files), PATH)
    assert files.downloaded == []


def test_read_bad_file_raises_rather_than_falling_back() -> None:
    files = _FakeFiles({PATH: b"section { background: url(http://x/); }"})
    with pytest.raises(CustomThemeError, match="external"):
        read_custom_theme(_nc(files), PATH)


# --- the --theme-set plumbing ------------------------------------------------------


class _FakeRunner:
    def __init__(self) -> None:
        self.argvs: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs):
        self.argvs.append(list(argv))
        Path(argv[argv.index("-o") + 1]).write_bytes(b"<html></html>")
        return subprocess.CompletedProcess(argv, 0, b"", b"")


def _deck(theme: str) -> str:
    return f"---\nmarp: true\ntheme: {theme}\n---\n\n# T\n"


@pytest.mark.parametrize("convert", [marp_html_gate, render_deck_html, render_deck_pdf])
def test_custom_deck_registers_the_custom_css(convert, tmp_path: Path) -> None:
    """Every conversion path (gate, HTML, PDF) registers the linted CSS
    as a file via --theme-set, exactly like the branded theme."""
    runner = _FakeRunner()
    theme = _theme()
    convert(_deck("custom"), tmp_path, runner=runner, theming_values=theme)
    (argv,) = runner.argvs
    css_path = Path(argv[argv.index("--theme-set") + 1])
    assert css_path == tmp_path / "custom-theme.css"
    assert css_path.read_text(encoding="utf-8") == theme.css
    assert "--no-html" in argv


def test_custom_theme_on_another_deck_theme_registers_nothing(tmp_path: Path) -> None:
    runner = _FakeRunner()
    marp_html_gate(_deck("default"), tmp_path, runner=runner, theming_values=_theme())
    assert "--theme-set" not in runner.argvs[0]


def test_branded_values_on_a_custom_deck_register_nothing(tmp_path: Path) -> None:
    runner = _FakeRunner()
    values = ThemingValues.from_capabilities({})
    marp_html_gate(_deck("custom"), tmp_path, runner=runner, theming_values=values)
    assert "--theme-set" not in runner.argvs[0]


# --- the gatekeeper ------------------------------------------------------------------


class _FakeMeasure:
    """Stands in for measure_deck_overflow at the facade seam."""

    def __init__(self, reports=(), error: Exception | None = None) -> None:
        self.reports = tuple(reports)
        self.error = error
        self.calls: list[tuple[str, object]] = []

    def __call__(self, markdown: str, workdir: Path, *, theming_values=None):
        self.calls.append((markdown, theming_values))
        if self.error is not None:
            raise self.error
        return self.reports


def test_gate_measures_the_probe_deck_under_the_theme(monkeypatch) -> None:
    measure = _FakeMeasure()
    monkeypatch.setattr(deck_pipeline, "measure_deck_overflow", measure)
    theme = _theme()
    assert gate_custom_theme(theme) is None
    assert measure.calls == [(PROBE_DECK, theme)]


def test_gate_rejects_naming_slide_amount_and_offender(monkeypatch) -> None:
    measure = _FakeMeasure([_report(4, 78)])
    monkeypatch.setattr(deck_pipeline, "measure_deck_overflow", measure)
    with pytest.raises(CustomThemeError) as excinfo:
        gate_custom_theme(_theme(BAD_CSS))
    message = str(excinfo.value)
    assert f"'{PATH}'" in message
    assert "normal content does not fit" in message
    assert "probe slide 4 (a table with full-width rows)" in message
    assert "78 px over (about 2 lines" in message
    assert "<table> 'Phase Owner Outcome'" in message
    assert "font-size, padding or line-height" in message


def test_gate_grants_the_one_line_grace(monkeypatch) -> None:
    """Sub-line overflow is invisible, exactly as in the overflow gates."""
    measure = _FakeMeasure([_report(5, 44), _report(6, 10, overflow_x=44)])
    monkeypatch.setattr(deck_pipeline, "measure_deck_overflow", measure)
    assert gate_custom_theme(_theme()) is None


def test_gate_blocks_horizontal_overflow_past_one_line(monkeypatch) -> None:
    measure = _FakeMeasure([_report(1, 0, overflow_x=45)])
    monkeypatch.setattr(deck_pipeline, "measure_deck_overflow", measure)
    with pytest.raises(CustomThemeError, match="45 px wider than the safe area"):
        gate_custom_theme(_theme())


def test_gate_caches_the_verdict_by_content_digest(monkeypatch) -> None:
    measure = _FakeMeasure()
    monkeypatch.setattr(deck_pipeline, "measure_deck_overflow", measure)
    gate_custom_theme(_theme())
    gate_custom_theme(_theme(path="Elsewhere/theme.css"))  # same bytes
    assert len(measure.calls) == 1
    gate_custom_theme(_theme(GOOD_CSS + b"\n/* edited */\n"))
    assert len(measure.calls) == 2


def test_cached_rejection_names_the_current_path(monkeypatch) -> None:
    measure = _FakeMeasure([_report(1, 200)])
    monkeypatch.setattr(deck_pipeline, "measure_deck_overflow", measure)
    with pytest.raises(CustomThemeError):
        gate_custom_theme(_theme(BAD_CSS))
    with pytest.raises(CustomThemeError, match=r"'Moved/theme\.css'"):
        gate_custom_theme(_theme(BAD_CSS, path="Moved/theme.css"))
    assert len(measure.calls) == 1


def test_gate_infrastructure_failure_propagates_uncached(monkeypatch) -> None:
    measure = _FakeMeasure(error=_OverflowMeasurementError("no chromium"))
    monkeypatch.setattr(deck_pipeline, "measure_deck_overflow", measure)
    for _ in range(2):
        with pytest.raises(_OverflowMeasurementError):
            gate_custom_theme(_theme())
    assert len(measure.calls) == 2
    assert deck_pipeline._CUSTOM_THEME_VERDICTS == {}


def test_gate_cache_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(deck_pipeline, "measure_deck_overflow", _FakeMeasure())
    cap = deck_pipeline._CUSTOM_THEME_VERDICTS_MAX
    for index in range(cap + 5):
        gate_custom_theme(_theme(GOOD_CSS + f"/* {index} */".encode()))
    assert len(deck_pipeline._CUSTOM_THEME_VERDICTS) == cap


def test_probe_blocking_and_rejection_helpers() -> None:
    reports = (_report(1, 44), _report(2, 45), _report(3, 0, overflow_x=45))
    assert [r.slide for r in probe_blocking(reports)] == [2, 3]
    message = str(probe_rejection(PATH, probe_blocking(reports)))
    assert "probe slide 2 (a nested list under a subheading)" in message
    assert "probe slide 3 (a full code block)" in message


# --- the real toolchain (battery lane) -----------------------------------------------


@pytest.mark.battery
def test_real_gate_accepts_stock_geometry_and_rejects_enlarged_type() -> None:
    """Against real marp-cli and headless Chromium: a recolor/re-font of
    the default theme passes, a 36px section font is rejected with the
    probe slides named."""
    gate_custom_theme(_theme(GOOD_CSS))
    with pytest.raises(CustomThemeError, match="probe slide 1"):
        gate_custom_theme(_theme(BAD_CSS))
