# SPDX-FileCopyrightText: 2026 WSHAPER <42714629+WSHAPER@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings for the marp_slides ExApp: env-driven values, admin appconfig
overrides (see apply_admin_config) and immutable contract constants."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

APP_ID = "slideprepper"
# keep in sync with appinfo/info.xml (pinned by tests/test_versions.py)
APP_VERSION = "0.1.1"

TASK_TYPE_ID = "slideprepper:slide_deck_generation"
PROVIDER_ID = "slideprepper-deck_renderer"
PROVIDER_NAME = "Marp Slide Deck Renderer"
EXPECTED_RUNTIME_S = 120

#: The deterministic re-render task type: the SAME strict validation and
#: render pipeline as the authoring task, minus the LLM call. Input is a
#: complete Marp Markdown source (typically the retained
#: ``slide_deck_source`` after edits), so the expected runtime is the render
#: cost only, no authoring round.
RE_RENDER_TASK_TYPE_ID = "slideprepper:re_render"
RE_RENDER_PROVIDER_ID = "slideprepper:re_renderer"
RE_RENDER_PROVIDER_NAME = "Marp Slide Deck Re-renderer"
RE_RENDER_EXPECTED_RUNTIME_S = 10

#: Accepted ``output_format`` values since v0.1.0: ``pptx`` (native
#: editable build via python-pptx; the re-render task's default), ``html``
#: (a single standalone HTML file rendered by marp-cli: Node only, no
#: browser involved) and ``pdf`` (a PDF document rendered by marp-cli
#: through the bundled Chromium: the one output format that uses the
#: browser shipped in the base image). ``all`` generates every format as
#: separate files; it is the generation task's default (see
#: :data:`GENERATION_OUTPUT_FORMAT_DEFAULT`). marp-cli cannot emit ODP, so
#: it is not offered.
OUTPUT_FORMAT_VALUES = ("pptx", "html", "pdf", "all")

#: Display names for the ENUM slot values (one per OUTPUT_FORMAT_VALUES entry).
OUTPUT_FORMAT_LABELS = {
    "pptx": "PowerPoint presentation (pptx)",
    "html": "Standalone HTML presentation (html)",
    "pdf": "PDF document (pdf)",
    "all": "All formats (pptx + html + pdf)",
}

#: Description for the ``output_format`` input slot (states the allowed values).
OUTPUT_FORMAT_DESCRIPTION = (
    "Output file format for the deck: 'pptx' (editable PowerPoint; the "
    "re-render default), 'html' (a single standalone HTML file), 'pdf' (a "
    "PDF document), or 'all' (generates all three formats as separate "
    "files; the generation default). ODP cannot be produced because the "
    "Marp renderer has no ODP output."
)

#: Default ``output_format`` of the GENERATION task type
#: (``slideprepper:slide_deck_generation``) and its worker fallback: ``all``
#: hands the user every deliverable (pptx, html and pdf plus the retained
#: Markdown source) unless they deliberately narrow the selection. The
#: re-render task keeps ``pptx`` (a single-deliverable refresh flow).
GENERATION_OUTPUT_FORMAT_DEFAULT = "all"

#: Input key of the nested ``core:text2text`` task (verified in
#: server/lib/public/TaskProcessing/TaskTypes/TextToText.php).
TEXT2TEXT_INPUT_KEY = "input"
TEXT2TEXT_OUTPUT_KEY = "output"
TEXT2TEXT_TASK_TYPE_ID = "core:text2text"

DEFAULT_APP_PORT = 9090
DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_MARP_TIMEOUT_S = 120
DEFAULT_LLM_TIMEOUT_S = 300
#: Total cap across every inner-LLM round of the two-phase pipeline
#: (the outline plus each slide, first attempts and retries alike).
#: Measured on a local qwen3.8-9b: ~70 s for the outline plus 40-70 s
#: per slide. A hosted research/shared-quota provider (shared-quota)
#: runs 2-6x slower per round with occasional multi-minute stalls, and
#: the 900 s cap calibrated on the local model killed benign 6-8 slide
#: decks mid-authoring (measured live: 6 of 8 benign decks); 1800 s
#: keeps the local case comfortable and lets hosted providers finish
#: with retry headroom.
DEFAULT_TOTAL_LLM_TIMEOUT_S = 1800
DEFAULT_MAX_PROMPT_CHARS = 8192
DEFAULT_MAX_DECK_BYTES = 1024 * 1024
DEFAULT_MAX_SLIDES = 60
DEFAULT_MARP_BIN = "marp"
#: One self-correcting inner-LLM retry when the deck fails extraction or
#: strict validation (sampling variance); gate/build/prompt failures are
#: never retried.
DEFAULT_RETRY_LLM = True

#: Deck failures log a 240-char excerpt of the extracted markdown (WARNING,
#: single line) for docker-logs diagnosability. The excerpt is prompt-derived
#: deck content: privacy-sensitive deployments can suppress it with
#: ``SLIDEPREPPER_LOG_EXCERPT=false``; the error line itself always stays
DEFAULT_LOG_EXCERPT = True

#: Early slide-fit gates: every authored slide is measured against its
#: theme box (an overflowing slide fails the per-slide retry with the
#: measured overflow message as feedback), and one batch measurement
#: backs the render phase as a non-retryable deck-level gate. Measurement
#: is best-effort: an infrastructure outage (Chromium missing, render
#: crash) logs and skips the check, never blocks deck delivery. Disable
#: both gates with ``SLIDEPREPPER_OVERFLOW_CHECK=false``.
DEFAULT_OVERFLOW_CHECK = True

#: Draft retention: failed attempts keep their
#: authored slides as an atomic checkpoint on the ExApp volume so a
#: follow-up task with draft_id resumes instead of regenerating.
DEFAULT_DRAFTS_ENABLED = True
DEFAULT_DRAFTS_FOLDER = ""
DEFAULT_DRAFTS_MAX_KEPT = 20

#: Files-by-default delivery: every generated deck
#: format plus the source markdown is written into this folder in the
#: task owner's Files (DAV impersonation, fail-soft). Empty disables.
DEFAULT_DELIVERIES_FOLDER = "SlidePrepper"

#: Owner-relative path of the design tokens file that re-brands the
#: generated ``nextcloud`` theme (see the README's "Design tokens").
#: It rides the delivered-files folder convention so the file lives
#: beside the decks it brands. A missing file is exactly the
#: instance-theming behavior; deleting (or renaming) the file is the
#: off switch.
DEFAULT_DESIGN_TOKENS_PATH = "SlidePrepper/design-tokens.json"

#: How long a read-through of the admin appconfig stays fresh: a value an
#: admin saves (declarative settings UI or occ) applies to tasks claimed
#: at least this long after the save, without a restart.
ADMIN_CONFIG_REFRESH_TTL_S = 30.0

_ADMIN_BOOL_TRUE = ("1", "true", "yes", "on")
_ADMIN_BOOL_FALSE = ("0", "false", "no", "off")

LOGGER = logging.getLogger("marp_slides.settings")

PPTX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

#: The retained deck source (final validated Marp Markdown) is uploaded with
#: this content type.
MARKDOWN_MIME_TYPE = "text/markdown"

#: MIME type uploaded for ``output_format=html`` results.
HTML_MIME_TYPE = "text/html"

#: MIME type uploaded for ``output_format=pdf`` results.
PDF_MIME_TYPE = "application/pdf"


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid value for {name}: {raw!r} (expected a number)"
        ) from exc


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid value for {name}: {raw!r} (expected an integer)"
        ) from exc


@dataclass(frozen=True)
class Settings:
    """Runtime knobs. NEXTCLOUD_URL/APP_SECRET are read by nc_py_api itself from the
    environment; they are surfaced here only for validation and local-dev convenience."""

    app_port: int = DEFAULT_APP_PORT
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S
    marp_timeout_s: int = DEFAULT_MARP_TIMEOUT_S
    llm_timeout_s: int = DEFAULT_LLM_TIMEOUT_S
    total_llm_timeout_s: int = DEFAULT_TOTAL_LLM_TIMEOUT_S
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS
    max_deck_bytes: int = DEFAULT_MAX_DECK_BYTES
    max_slides: int = DEFAULT_MAX_SLIDES
    marp_bin: str = DEFAULT_MARP_BIN
    retry_llm: bool = DEFAULT_RETRY_LLM
    log_excerpt: bool = DEFAULT_LOG_EXCERPT
    overflow_check: bool = DEFAULT_OVERFLOW_CHECK
    drafts_enabled: bool = DEFAULT_DRAFTS_ENABLED
    drafts_folder: str = DEFAULT_DRAFTS_FOLDER
    drafts_max_kept: int = DEFAULT_DRAFTS_MAX_KEPT
    deliveries_folder: str = DEFAULT_DELIVERIES_FOLDER
    design_tokens_path: str = DEFAULT_DESIGN_TOKENS_PATH

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            app_port=_env_int(env, "APP_PORT", DEFAULT_APP_PORT),
            poll_interval_s=_env_float(
                env, "SLIDEPREPPER_POLL_INTERVAL", DEFAULT_POLL_INTERVAL_S
            ),
            marp_timeout_s=_env_int(
                env, "SLIDEPREPPER_MARP_TIMEOUT", DEFAULT_MARP_TIMEOUT_S
            ),
            llm_timeout_s=_env_int(
                env, "SLIDEPREPPER_LLM_TIMEOUT", DEFAULT_LLM_TIMEOUT_S
            ),
            total_llm_timeout_s=_env_int(
                env, "SLIDEPREPPER_TOTAL_LLM_TIMEOUT", DEFAULT_TOTAL_LLM_TIMEOUT_S
            ),
            max_prompt_chars=_env_int(
                env, "SLIDEPREPPER_MAX_PROMPT_CHARS", DEFAULT_MAX_PROMPT_CHARS
            ),
            max_deck_bytes=_env_int(
                env, "SLIDEPREPPER_MAX_DECK_BYTES", DEFAULT_MAX_DECK_BYTES
            ),
            max_slides=_env_int(env, "SLIDEPREPPER_MAX_SLIDES", DEFAULT_MAX_SLIDES),
            marp_bin=env.get("SLIDEPREPPER_MARP_BIN", DEFAULT_MARP_BIN)
            or DEFAULT_MARP_BIN,
            retry_llm=_env_bool(env, "SLIDEPREPPER_RETRY_LLM", DEFAULT_RETRY_LLM),
            log_excerpt=_env_bool(env, "SLIDEPREPPER_LOG_EXCERPT", DEFAULT_LOG_EXCERPT),
            overflow_check=_env_bool(
                env, "SLIDEPREPPER_OVERFLOW_CHECK", DEFAULT_OVERFLOW_CHECK
            ),
            drafts_enabled=_env_bool(
                env, "SLIDEPREPPER_DRAFTS_ENABLED", DEFAULT_DRAFTS_ENABLED
            ),
            drafts_folder=env.get("SLIDEPREPPER_DRAFTS_FOLDER", "").strip()
            or DEFAULT_DRAFTS_FOLDER,
            drafts_max_kept=_env_int(
                env, "SLIDEPREPPER_DRAFTS_MAX_KEPT", DEFAULT_DRAFTS_MAX_KEPT
            ),
            deliveries_folder=env.get("SLIDEPREPPER_DELIVERIES_FOLDER", "").strip()
            or DEFAULT_DELIVERIES_FOLDER,
            design_tokens_path=env.get("SLIDEPREPPER_DESIGN_TOKENS_PATH", "").strip()
            or DEFAULT_DESIGN_TOKENS_PATH,
        )


@dataclass(frozen=True)
class AdminConfigKnob:
    """One admin-manageable knob: the ``oc_appconfig_ex`` key (the env var
    name), the :class:`Settings` attribute it overrides, its value kind and
    the human metadata the declarative settings form renders."""

    key: str
    attribute: str
    kind: str  # "int" | "bool" | "str"
    label: str
    description: str


#: The operational knobs an instance admin may tune at runtime (see
#: README "Configuration"). Secrets and infra-coupled variables stay
#: environment-only (ENV_ONLY_CONFIG_KEYS); internal per-round timeouts and
#: byte gates stay environment-only too: their admin-facing tuning axis is
#: the surfaced total cap and slide count.
ADMIN_CONFIG_KNOBS: tuple[AdminConfigKnob, ...] = (
    AdminConfigKnob(
        key="SLIDEPREPPER_TOTAL_LLM_TIMEOUT",
        attribute="total_llm_timeout_s",
        kind="int",
        label="Total inner LLM time cap (seconds)",
        description=(
            "Total time budget across BOTH inner LLM attempts (the first "
            "plus the self-correcting retry). Default: 1800."
        ),
    ),
    AdminConfigKnob(
        key="SLIDEPREPPER_MAX_SLIDES",
        attribute="max_slides",
        kind="int",
        label="Maximum slides per deck",
        description=(
            "Upper bound of slides the model may author for one deck; the "
            "per-task request can only lower it. Default: 60."
        ),
    ),
    AdminConfigKnob(
        key="SLIDEPREPPER_MAX_PROMPT_CHARS",
        attribute="max_prompt_chars",
        kind="int",
        label="Maximum user prompt length (characters)",
        description=(
            "Prompts longer than this are rejected before any LLM round. Default: 8192."
        ),
    ),
    AdminConfigKnob(
        key="SLIDEPREPPER_DRAFTS_FOLDER",
        attribute="drafts_folder",
        kind="str",
        label="Files folder for draft checkpoints",
        description=(
            "When set, failed and resumed decks also copy their current "
            "draft markdown into this folder of the requesting user's "
            "Files. Empty keeps drafts on the ExApp volume only. "
            "Default: empty."
        ),
    ),
    AdminConfigKnob(
        key="SLIDEPREPPER_RETRY_LLM",
        attribute="retry_llm",
        kind="bool",
        label="Retry failed LLM rounds once",
        description=(
            "One self-correcting retry when the deck fails extraction or "
            "strict validation; disable to fail at the first attempt. "
            "Default: true."
        ),
    ),
    AdminConfigKnob(
        key="SLIDEPREPPER_OVERFLOW_CHECK",
        attribute="overflow_check",
        kind="bool",
        label="Slide overflow checks",
        description=(
            "Measure every authored slide against its theme box and retry "
            "or shrink overflowing slides. Disable only when the headless "
            "renderer is unavailable on this instance. Default: true."
        ),
    ),
    AdminConfigKnob(
        key="SLIDEPREPPER_LOG_EXCERPT",
        attribute="log_excerpt",
        kind="bool",
        label="Log deck-failure markdown excerpts",
        description=(
            "When a generated deck fails validation, log a 240-character "
            "excerpt of the extracted markdown (WARNING, deck content "
            "only) for diagnosability. Set to false to suppress the "
            "excerpt in privacy-sensitive deployments; the error line "
            "itself is always logged. Default: true."
        ),
    ),
    AdminConfigKnob(
        key="SLIDEPREPPER_DRAFTS_ENABLED",
        attribute="drafts_enabled",
        kind="bool",
        label="Keep draft checkpoints",
        description=(
            "Failed attempts keep their completed slides as a checkpoint "
            "so a follow-up task can resume; disable to always regenerate "
            "from scratch. Default: true."
        ),
    ),
    AdminConfigKnob(
        key="SLIDEPREPPER_DESIGN_TOKENS_PATH",
        attribute="design_tokens_path",
        kind="str",
        label="Design tokens file path",
        description=(
            "Path of the design tokens JSON (relative to the requesting "
            "user's Files root) whose colors and font re-brand the "
            "nextcloud theme for that user's decks; a folder mounted at "
            "the same path for every user (for example a group folder) "
            "can be pointed at here. A missing or malformed file falls "
            "back to the instance theming, never a task failure. Set to "
            "'-' or 'off' to disable design tokens instance-wide. "
            "Default: SlidePrepper/design-tokens.json."
        ),
    ),
)

#: The oc_appconfig_ex keys read per task (in one batched call).
ADMIN_CONFIG_KEYS: tuple[str, ...] = tuple(knob.key for knob in ADMIN_CONFIG_KNOBS)

#: Deliberately NOT admin-manageable: AppAPI wiring and bind surface are read
#: by nc_py_api itself; the marp binary path, the daemon poll cadence and the
#: per-round subprocess timeout are container/runtime-relative; the inner
#: per-round LLM timeout and the deck byte gate are internal pipeline details
#: whose admin-facing axes are the surfaced total cap and slide count. This
#: tuple exists so tests can pin the boundary (nothing secret or
#: infra-coupled may ever join ADMIN_CONFIG_KNOBS).
ENV_ONLY_CONFIG_KEYS: tuple[str, ...] = (
    "APP_SECRET",
    "APP_ID",
    "APP_PORT",
    "APP_HOST",
    "NEXTCLOUD_URL",
    "SLIDEPREPPER_MARP_BIN",
    "SLIDEPREPPER_POLL_INTERVAL",
    "SLIDEPREPPER_MARP_TIMEOUT",
    "SLIDEPREPPER_LLM_TIMEOUT",
    "SLIDEPREPPER_MAX_DECK_BYTES",
)


def _parse_admin_bool(raw: str) -> bool | None:
    """Strict bool parse for admin-set values (unlike :func:`_env_bool`, an
    unrecognized token is a typo, not a False): True/False or None."""
    token = raw.strip().lower()
    if token in _ADMIN_BOOL_TRUE:
        return True
    if token in _ADMIN_BOOL_FALSE:
        return False
    return None


def apply_admin_config(base: Settings, values: Mapping[str, str]) -> Settings:
    """Layer admin-set appconfig values over ``base`` (the env settings):
    a knob set on the server wins, an unset or empty key keeps the env
    value, and an invalid value fails soft to the env value with a warning
    naming the knob and the bad value."""
    overrides: dict[str, Any] = {}
    for knob in ADMIN_CONFIG_KNOBS:
        raw = values.get(knob.key)
        if raw is None or raw == "":
            continue
        if knob.kind == "int":
            try:
                # reuse the env parser so both sources accept the same shape
                overrides[knob.attribute] = _env_int(values, knob.key, 0)
            except ValueError:
                LOGGER.warning(
                    "Ignoring invalid admin value for %s: %r (expected an "
                    "integer); the environment value stays in force",
                    knob.key,
                    raw,
                )
        elif knob.kind == "bool":
            parsed = _parse_admin_bool(raw)
            if parsed is None:
                LOGGER.warning(
                    "Ignoring invalid admin value for %s: %r (expected a "
                    "boolean); the environment value stays in force",
                    knob.key,
                    raw,
                )
            else:
                overrides[knob.attribute] = parsed
        else:
            stripped = raw.strip()
            if stripped:
                overrides[knob.attribute] = stripped
    return replace(base, **overrides) if overrides else base
