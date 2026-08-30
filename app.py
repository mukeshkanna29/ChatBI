"""
ChatBI - Lightweight Agentic BI Tool
====================================

A single-file, open-source BI tool that pairs AI-generated chart suggestions with
a manual chart builder and a Power BI-style dashboard editor.

Run it with:

    streamlit run app.py

Everything lives in this file on purpose: it is easy to read end to end, easy to
deploy (Streamlit Cloud / Render / Docker), and easy to fork.

Layout of this file
-------------------
1.  Constants & provider registry
2.  Small helpers (JSON parsing, data profiling, type coercion)
3.  LLM connectors (Groq, Gemini, Nvidia, Claude, Ollama)
4.  Data loading (upload, Google Sheets, URL/API, SQL database, sample data)
5.  Filters
6.  Chart engine (aggregation + Plotly figure construction)
7.  Exports (CSV, Excel, PDF, PNG, HTML, dashboard JSON)
8.  Canvas component, then the UI (data shelf, Ask bar, sheets, editors)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import uuid
import warnings
from typing import Any

import httpx
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

warnings.filterwarnings("ignore", category=UserWarning, module="pandas")

# Optional: pick up API keys from a local .env file. Absent python-dotenv, keys can
# still come from real environment variables, .streamlit/secrets.toml, or the sidebar.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

APP_NAME = "ChatBI"
APP_TAGLINE = "Agentic BI - AI suggests, you control"

# --------------------------------------------------------------------------------------
# 1. Constants & provider registry
# --------------------------------------------------------------------------------------

# Each provider exposes the same contract: given a data profile and a user prompt,
# return a JSON object of chart/KPI suggestions. `models` lists sensible defaults;
# users can type any model id the provider supports.
PROVIDERS: dict[str, dict[str, Any]] = {
    "Groq": {
        "key_env": "GROQ_API_KEY",
        "needs_key": True,
        "models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-120b",
        ],
        "models_url": "https://api.groq.com/openai/v1/models",
        "note": "Fastest free tier. Key: https://console.groq.com/keys",
    },
    "Gemini": {
        "key_env": "GEMINI_API_KEY",
        "needs_key": True,
        "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        "note": "Generous free tier. Key: https://aistudio.google.com/apikey",
    },
    "Nvidia": {
        "key_env": "NVIDIA_API_KEY",
        "needs_key": True,
        "models": [
            "openai/gpt-oss-120b",
            "mistralai/mistral-large-2-instruct",
            "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        ],
        # NIM retires models regularly, so the dropdown is refreshed from the live
        # catalogue whenever a key is present; these are only the fallback.
        "models_url": "https://integrate.api.nvidia.com/v1/models",
        "note": "NIM endpoints. Key: https://build.nvidia.com",
    },
    "Claude": {
        "key_env": "ANTHROPIC_API_KEY",
        "needs_key": True,
        "models": [
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5",
            "claude-opus-4-8",
        ],
        "note": "Highest-quality suggestions. Key: https://console.anthropic.com",
    },
    "Ollama (local)": {
        "key_env": None,
        "needs_key": False,
        "models": ["mistral", "llama3.1", "qwen2.5", "phi4"],
        "note": "Runs locally, no key, fully private. Needs `ollama serve`.",
    },
}

CHART_TYPES = [
    "bar",
    "line",
    "area",
    "scatter",
    "pie",
    "donut",
    "histogram",
    "box",
    "heatmap",
    "treemap",
]

# Chart types that aggregate y over x. The rest plot raw rows.
AGGREGATING_TYPES = {"bar", "line", "area", "pie", "donut", "treemap", "heatmap"}
RAW_TYPES = {"scatter", "histogram", "box"}

AGGREGATIONS = ["sum", "mean", "median", "count", "min", "max", "nunique"]

TIME_GRAINS = ["none", "day", "week", "month", "quarter", "year"]

SIZES = {
    "small": {"width": 4, "height": 300},
    "medium": {"width": 6, "height": 400},
    "large": {"width": 12, "height": 520},
}

DEFAULT_PALETTE = [
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#B279A2", "#EECA3B", "#FF9DA6", "#9D755D", "#BAB0AC",
]

# Categorical palettes for charts. "Colourblind safe" is the Okabe-Ito set.
PALETTES: dict[str, list[str]] = {
    "Default": DEFAULT_PALETTE,
    "Vibrant": ["#E63946", "#F77F00", "#FCBF49", "#06D6A0", "#118AB2",
                "#7209B7", "#F72585", "#4CC9F0", "#2A9D8F", "#8D99AE"],
    "Ocean": ["#03045E", "#0077B6", "#00B4D8", "#90E0EF", "#48CAE4",
              "#023E8A", "#0096C7", "#ADE8F4", "#CAF0F8", "#012A4A"],
    "Sunset": ["#F94144", "#F3722C", "#F8961E", "#F9C74F", "#90BE6D",
               "#43AA8B", "#577590", "#C1121F", "#FDC500", "#8E7DBE"],
    "Forest": ["#1B4332", "#2D6A4F", "#40916C", "#52B788", "#74C69D",
               "#95D5B2", "#B7E4C7", "#081C15", "#D8F3DC", "#387C6D"],
    "Berry": ["#590D22", "#A4133C", "#C9184A", "#FF4D6D", "#FF758F",
              "#FF8FA3", "#FFB3C1", "#800F2F", "#FFCCD5", "#6A040F"],
    "Colourblind safe": ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9",
                         "#D55E00", "#F0E442", "#000000", "#8C8C8C", "#7570B3"],
    "Monochrome blue": ["#08306B", "#08519C", "#2171B5", "#4292C6", "#6BAED6",
                        "#9ECAE1", "#C6DBEF", "#DEEBF7", "#F7FBFF", "#123F73"],
    # Built from the shell's own design tokens (the app bar's slate, the accent
    # blue) so a report styled with it looks like an extension of the app itself.
    "Application": ["#2F6FED", "#0B3D91", "#1E2430", "#5B8DEF", "#7EA6FF",
                    "#334155", "#93B4F0", "#0F172A", "#8AA8D6", "#475569"],
}

# Plotly templates - the overall look of the plotting surface.
CHART_TEMPLATES = ["plotly_white", "simple_white", "presentation", "ggplot2",
                   "seaborn", "plotly", "plotly_dark"]

# Continuous scales, used by heatmaps.
COLOR_SCALES = ["Blues", "Viridis", "Cividis", "Teal", "Oranges", "Purples", "RdBu", "Greens"]

CHART_THEME_DEFAULT: dict[str, Any] = {
    "palette": DEFAULT_PALETTE,
    "template": "plotly_white",
    "scale": "Blues",
}


_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class ThemeImportError(ValueError):
    """Raised when an uploaded theme file isn't usable."""


def parse_theme_file(data: bytes, fallback_name: str = "Imported") -> tuple[str, list[str]]:
    """Pull a name and a colour list out of an uploaded theme file.

    Accepts our own export format and real Power BI theme files interchangeably -
    both are just JSON with a colour list under a well-known key. Power BI's key is
    "dataColors"; ours is "colors". Everything else in a Power BI theme file (fonts,
    background, visual styles) is ignored - only the colour list is adopted.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ThemeImportError("That file isn't valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ThemeImportError("Expected a JSON object with a colour list, not a {}.".format(
            type(payload).__name__))

    colors = payload.get("dataColors") or payload.get("colors")
    if not isinstance(colors, list) or len(colors) < 2:
        raise ThemeImportError(
            'No usable colour list found. Expected a "dataColors" array (the format '
            'Power BI exports) or a "colors" array, with at least two hex colours.'
        )
    cleaned = [str(c).strip() for c in colors if _HEX_COLOR_RE.match(str(c).strip())]
    if len(cleaned) < 2:
        raise ThemeImportError(
            "Found a colour list, but fewer than two entries were valid hex colours "
            '(like "#2F6FED").'
        )

    name = str(payload.get("name") or "").strip() or fallback_name
    return name[:40], cleaned


def unique_palette_name(name: str, existing: dict[str, list[str]]) -> str:
    """Keep an imported palette's name from silently overwriting another one."""
    base = (name or "Imported").strip() or "Imported"
    if base not in existing:
        return base
    for suffix in range(2, 100):
        candidate = "{} ({})".format(base, suffix)
        if candidate not in existing:
            return candidate
    return base + " " + new_id("")


def all_palettes() -> dict[str, list[str]]:
    """Built-in palettes plus anything imported this session, built-ins first."""
    return {**PALETTES, **st.session_state.get("dm_custom_palettes", {})}


def chart_theme(palette: str = "Default", template: str = "plotly_white",
                scale: str = "Blues") -> dict[str, Any]:
    """Resolve theme names into the concrete values build_figure needs."""
    return {
        "palette": all_palettes().get(palette, DEFAULT_PALETTE),
        "template": template if template in CHART_TEMPLATES else "plotly_white",
        "scale": scale if scale in COLOR_SCALES else "Blues",
    }


# Workbook formatting presets for the Excel export.
EXCEL_THEMES: dict[str, dict[str, str]] = {
    "Corporate blue": {"header": "1F4E79", "header_text": "FFFFFF", "band": "EAF1F8",
                       "border": "B0BEC5", "accent": "2E75B6"},
    "Slate": {"header": "334155", "header_text": "FFFFFF", "band": "F1F5F9",
              "border": "CBD5E1", "accent": "64748B"},
    "Forest": {"header": "1B4332", "header_text": "FFFFFF", "band": "E8F5EE",
               "border": "B7E4C7", "accent": "40916C"},
    "Plum": {"header": "5B2333", "header_text": "FFFFFF", "band": "F7E8EC",
             "border": "E0B1BB", "accent": "A4133C"},
    "Amber": {"header": "7C4700", "header_text": "FFFFFF", "band": "FFF4E0",
              "border": "F3D5A5", "accent": "C77700"},
    "Minimal": {"header": "FFFFFF", "header_text": "000000", "band": "FFFFFF",
                "border": "D0D0D0", "accent": "444444"},
}

# Sheets the Excel export can contain. The user chooses which to include.
EXCEL_SHEETS = [
    "Raw data",
    "Summary statistics",
    "KPIs",
    "Chart data (one sheet each)",
    "Pivot table",
    "Dashboard config",
]
DEFAULT_EXCEL_SHEETS = ["Raw data", "Summary statistics", "KPIs", "Chart data (one sheet each)"]

MAX_UPLOAD_MB = 500

# JSON contract the LLM must follow. Also used as a strict schema for Claude.
SUGGESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "charts": {
            "type": "array",
            # The prompt asks for 4-5; the schema floor sits a little below that so a
            # sparse dataset (few usable columns) is never structurally impossible to
            # satisfy, while the ceiling stops a rich dataset from sprawling past what
            # one sheet can hold.
            "minItems": 3,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": CHART_TYPES},
                    "x": {"type": "string"},
                    "y": {"type": "string"},
                    "agg": {"type": "string", "enum": AGGREGATIONS},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["type", "x", "y", "agg", "title", "reason"],
                "additionalProperties": False,
            },
        },
        "kpis": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "column": {"type": "string"},
                    "agg": {"type": "string", "enum": AGGREGATIONS},
                    "title": {"type": "string"},
                },
                "required": ["column", "agg", "title"],
                "additionalProperties": False,
            },
        },
        "insights": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["charts", "kpis", "insights"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a senior Business Intelligence developer and data analyst with
more than ten years of experience building executive dashboards. You are sharp about
which cuts of data are actually worth showing, and you title everything the way a
professional BI tool does - never a raw column name, never "Chart 1".

You are given a profile of a tabular dataset and a question from the user.
Suggest 4 to 5 charts and 2 to 4 KPI metrics that best answer the question, each
looking at it from a genuinely different angle - do not cut the same two columns
five different ways.

Hard rules:
- Use ONLY column names that appear verbatim in the profile. Never invent columns.
- "y" and KPI "column" must be a numeric column, unless "agg" is "count" or "nunique".
- Prefer a date/time column on the x-axis for line and area charts.
- Prefer a low-cardinality categorical column on the x-axis for bar, pie and treemap.
- Do not suggest the same chart twice.
- "title" is a clean, specific, Title Case heading a professional dashboard would show,
  e.g. "Revenue by Region" or "Monthly Profit Trend" - never a bare column name.
- "reason" is one short sentence, written the way a senior analyst would brief a
  director: specific and quantified where the data supports it, not generic.

Respond with ONLY a JSON object, no prose and no markdown fences:
{"charts": [{"type": "...", "x": "...", "y": "...", "agg": "...", "title": "...", "reason": "..."}],
 "kpis": [{"column": "...", "agg": "...", "title": "..."}],
 "insights": ["short observation", "..."]}

Allowed "type": CHART_TYPE_LIST
Allowed "agg": AGG_LIST
""".replace("CHART_TYPE_LIST", ", ".join(CHART_TYPES)).replace("AGG_LIST", ", ".join(AGGREGATIONS))


# Contract for building a whole multi-sheet report from one prompt.
REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "layout": {
                        "type": "string",
                        "enum": ["Overview", "Spotlight", "Sidebar", "Column", "Compact"],
                    },
                    "charts": SUGGESTION_SCHEMA["properties"]["charts"],
                    "kpis": SUGGESTION_SCHEMA["properties"]["kpis"],
                },
                "required": ["name", "layout", "charts", "kpis"],
                "additionalProperties": False,
            },
        },
        "insights": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["pages", "insights"],
    "additionalProperties": False,
}

REPORT_SYSTEM_PROMPT = """You are a senior Business Intelligence developer and data
analyst with more than ten years of experience building executive dashboards for
retail, SaaS and operations teams. You have seen thousands of reports and know which
cuts of data actually earn a director's attention - you do not pad a sheet with filler,
and you do not ship a sheet that feels thin.

You are given a profile of a tabular dataset and a request from the user. Design the
complete SHEETS of the report, the way you would structure a real board-ready BI deck.

Each sheet needs:
- A NAME that is the sheet's topic, written the way a professional report would label
  a tab - e.g. "Regional Performance", "Product Profitability", "Customer Quality" -
  never a placeholder like "Sheet 2" or a bare column name.
- 2 to 4 KPI tiles that headline the sheet's topic in one number each.
- EXACTLY 4 to 5 charts that explore that topic from genuinely different angles (by
  time, by segment, by dimension, as a distribution, as a comparison...). Never cut the
  same two columns five different ways, and never repeat a chart from another sheet.

Follow the user's structure:
- If they describe sheets explicitly ("sheet 1 ... sheet 2 ...", "a sheet for sales and
  one for returns"), create exactly those sheets, with those names, and fill each with
  what they asked for - still aiming for 4 to 5 charts unless they capped it lower.
- If they do not describe sheets, design 3 to 5 that tell a coherent story: an executive
  summary first, then one sheet per dimension that actually matters in this data (time
  trend, region/geography, product/category, channel, customer segment, quality/returns
  - pick the ones this dataset supports, skip the ones it does not).

Hard rules:
- Use ONLY column names that appear verbatim in the profile. Never invent columns.
- "y" and KPI "column" must be a numeric column, unless "agg" is "count" or "nunique".
- Prefer a date/time column on the x-axis for line and area charts.
- Prefer a low-cardinality categorical column on the x-axis for bar, pie and treemap.
- Do not repeat the same chart within a sheet, or across sheets.
- "title" is a clean, specific, Title Case heading a professional dashboard would show,
  e.g. "Revenue by Region" or "Monthly Profit Trend" - never a bare column name, a
  generic label, or trailing punctuation.
- "reason" is one short sentence, written the way a senior analyst would brief a
  director: specific and quantified where the data supports it, not generic.
- "layout" is how to arrange the sheet, one of: Overview (KPI banner then charts two per
  row - the default for most sheets), Spotlight (one big hero chart), Sidebar (tall
  chart left, rest right), Column (one full-width element per row), Compact (dense
  tiling - good for a sheet with 5 charts and no KPIs).

Respond with ONLY a JSON object, no prose and no markdown fences:
{"pages": [{"name": "...", "layout": "Overview",
            "kpis": [{"column": "...", "agg": "...", "title": "..."}],
            "charts": [{"type": "...", "x": "...", "y": "...", "agg": "...",
                        "title": "...", "reason": "..."}]}],
 "insights": ["short observation", "..."]}

Allowed "type": CHART_TYPE_LIST
Allowed "agg": AGG_LIST
""".replace("CHART_TYPE_LIST", ", ".join(CHART_TYPES)).replace("AGG_LIST", ", ".join(AGGREGATIONS))


class LLMError(RuntimeError):
    """Raised when a provider call fails in a way worth showing the user."""


# --------------------------------------------------------------------------------------
# 2. Helpers
# --------------------------------------------------------------------------------------


def new_id(prefix: str = "el") -> str:
    return prefix + "_" + uuid.uuid4().hex[:8]


def _scan_json(text: str, start: int) -> tuple[int | None, list[str], bool]:
    """Walk a JSON value from `start`.

    Returns (index of the closing brace, still-open closers, inside-a-string) - the
    index is None when the text runs out before the value is closed.
    """
    stack: list[str] = []
    in_string = escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]":
            if stack:
                stack.pop()
            if not stack:
                return i, stack, in_string
    return None, stack, in_string


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of an LLM response.

    Models wrap JSON in prose or ```json fences often enough that a bare
    json.loads() is not reliable, so fall back to brace matching - and then to
    repairing an unterminated object, which smaller models produce regularly
    (a complete answer that simply never emits the final `}`).
    """
    if not text or not text.strip():
        raise LLMError("The model returned an empty response.")

    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        # A bare array or scalar is not the contract; fall through to look for an
        # object inside it rather than handing a list to the validator.
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise LLMError("No JSON found in the model response:\n\n" + text[:400])

    end, stack, in_string = _scan_json(cleaned, start)
    if end is not None:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("Model returned malformed JSON: " + str(exc)) from exc

    # Unterminated, so repair it two ways and take whichever parses.
    #
    #   close_open  - append the closers that are still open. Right when the model
    #                 emitted a complete answer and only dropped the final `}`.
    #   trim_back   - drop back to the last element that actually completed, then
    #                 close. Right when generation was cut mid-value.
    #
    # Ending inside a string proves the last value is incomplete, so prefer
    # trim_back in that case and close_open otherwise. Trying both either way
    # means a repair is only rejected when neither shape is valid JSON.
    candidate = cleaned[start:]

    if in_string:
        close_open = candidate + '"' + "".join(reversed(stack))
    else:
        close_open = candidate.rstrip().rstrip(",") + "".join(reversed(stack))

    attempts = [close_open]
    cut = max(candidate.rfind("}"), candidate.rfind("]"))
    if cut > 0:
        trimmed = candidate[: cut + 1]
        _, remaining, _ = _scan_json(trimmed, 0)
        trim_back = trimmed.rstrip().rstrip(",") + "".join(reversed(remaining))
        attempts = [trim_back, close_open] if in_string else [close_open, trim_back]

    for attempt in attempts:
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise LLMError("Model returned truncated JSON:\n\n" + text[:400])


def is_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def is_datetime(series: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(series)


def numeric_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if is_numeric(df[c])]


def datetime_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if is_datetime(df[c])]


def categorical_cols(df: pd.DataFrame, max_unique: int = 200) -> list[str]:
    out = []
    for c in df.columns:
        if is_numeric(df[c]) or is_datetime(df[c]):
            continue
        if df[c].nunique(dropna=True) <= max_unique:
            out.append(c)
    return out


def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Best-effort type detection for CSV/JSON sources, which arrive as strings.

    Deliberately conservative: a column is only converted when nearly every value
    converts cleanly, so IDs and postcodes are left alone.
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    money_re = r"[,$£€₹%\s]"

    for col in df.columns:
        series = df[col]
        # pandas 2 gives text columns `object` dtype; pandas 3 gives `str`. Handle both.
        if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
            continue
        non_null = series.dropna()
        if non_null.empty:
            continue

        text = non_null.astype(str)

        # Currency / thousands separators -> numeric.
        if text.str.contains(money_re, regex=True).mean() > 0.5:
            converted = pd.to_numeric(text.str.replace(money_re, "", regex=True), errors="coerce")
            if converted.notna().mean() > 0.95:
                df[col] = pd.to_numeric(
                    series.astype(str).str.replace(money_re, "", regex=True), errors="coerce"
                )
                continue

        # Date-like strings -> datetime. Skip pure numbers (years, ids).
        if text.str.fullmatch(r"-?\d+(\.\d+)?").mean() > 0.5:
            continue
        try:
            parsed = pd.to_datetime(text.head(500), errors="coerce", format="mixed")
        except (ValueError, TypeError):
            continue
        if parsed.notna().mean() > 0.9:
            try:
                df[col] = pd.to_datetime(series, errors="coerce", format="mixed")
            except (ValueError, TypeError):
                pass

    return df


def profile_dataframe(df: pd.DataFrame, sample_rows: int = 3) -> str:
    """Compact, token-cheap description of the data for the LLM prompt."""
    lines = ["Rows: {:,}".format(len(df)), "Columns: {}".format(len(df.columns)), "", "COLUMNS:"]
    for col in df.columns:
        s = df[col]
        kind = "numeric" if is_numeric(s) else "datetime" if is_datetime(s) else "categorical"
        parts = ["- {} ({}, dtype={})".format(col, kind, s.dtype)]
        parts.append("nulls={}".format(int(s.isna().sum())))
        parts.append("unique={}".format(int(s.nunique(dropna=True))))
        if kind == "numeric" and s.notna().any():
            parts.append("min={:.6g}, max={:.6g}, mean={:.6g}".format(s.min(), s.max(), s.mean()))
        elif kind == "datetime" and s.notna().any():
            parts.append("from={}, to={}".format(s.min(), s.max()))
        else:
            examples = s.dropna().astype(str).unique()[:5]
            if len(examples):
                parts.append("examples=" + ", ".join(repr(e) for e in examples))
        lines.append("  ".join(parts))

    lines.append("")
    lines.append("FIRST {} ROWS (CSV):".format(sample_rows))
    lines.append(df.head(sample_rows).to_csv(index=False).strip())
    return "\n".join(lines)


def humanize(value: float) -> str:
    """Compact number formatting for KPI cards."""
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        return "-"
    magnitude = abs(value)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= limit:
            return "{:,.2f}{}".format(value / limit, suffix)
    if float(value).is_integer():
        return "{:,.0f}".format(value)
    if magnitude >= 1 or value == 0:
        return "{:,.2f}".format(value)
    return "{:,.4g}".format(value)


def allowed_aggregations(series: pd.Series) -> list[str]:
    """sum/mean/median/min/max are meaningless on text and dates - hide them."""
    return AGGREGATIONS if is_numeric(series) else ["count", "nunique"]


# --------------------------------------------------------------------------------------
# 3. LLM connectors
# --------------------------------------------------------------------------------------

# Free-tier endpoints queue requests, so allow generous headroom before giving up.
HTTP_TIMEOUT = 180.0


class ProviderRejectedParam(LLMError):
    """The endpoint refused a request parameter (4xx), so a simpler shape may work.

    Distinguished from a timeout or a network fault, which retrying with fewer
    parameters would not fix - and which would cost a second full timeout.
    """


def _http_post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    except httpx.TimeoutException as exc:
        raise LLMError(
            "The provider did not respond within {:.0f}s. Free tiers queue requests when "
            "busy - try again, or pick a smaller model.".format(HTTP_TIMEOUT)
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMError("Could not reach the provider: " + str(exc)) from exc
    if response.status_code == 410:
        raise LLMError(
            "That model has been retired by the provider and is no longer "
            "available. Pick another one from the Model dropdown. " + response.text[:300]
        )
    if 400 <= response.status_code < 500:
        raise ProviderRejectedParam(
            "Provider returned HTTP {}: {}".format(response.status_code, response.text[:500])
        )
    if response.status_code >= 400:
        raise LLMError(
            "Provider returned HTTP {}: {}".format(response.status_code, response.text[:500])
        )
    try:
        return response.json()
    except ValueError as exc:
        raise LLMError("Provider returned a non-JSON response.") from exc


def _openai_compatible_chat(
    base_url: str, api_key: str, model: str, system: str, user: str, json_mode: bool = True,
    max_tokens: int = 2000,
) -> str:
    """Shared transport for Groq and Nvidia, which both speak the OpenAI chat schema."""
    if not api_key:
        raise LLMError("An API key is required for this provider.")

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    try:
        data = _http_post(base_url.rstrip("/") + "/chat/completions", headers, payload)
    except ProviderRejectedParam:
        if not json_mode:
            raise
        # Not every model on these endpoints supports JSON mode; retry plainly.
        # Only on an explicit rejection - retrying after a timeout would just
        # burn the timeout a second time.
        payload.pop("response_format", None)
        data = _http_post(base_url.rstrip("/") + "/chat/completions", headers, payload)

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("Unexpected response shape: " + json.dumps(data)[:400]) from exc


def _gemini_chat(api_key: str, model: str, system: str, user: str,
                 max_tokens: int = 2000) -> str:
    if not api_key:
        raise LLMError("An API key is required for Gemini.")

    url = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent".format(model)
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    data = _http_post(url, {"x-goog-api-key": api_key, "Content-Type": "application/json"}, payload)
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts)
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("Unexpected Gemini response: " + json.dumps(data)[:400]) from exc


def _looks_like_unsupported_param(exc: Exception) -> bool:
    """True when a request failed because the SDK or API rejected a newer parameter.

    Lets the Claude connector degrade from the richest request shape to the plainest
    one instead of failing outright on an older `anthropic` release.
    """
    if isinstance(exc, TypeError):
        return True
    status = getattr(exc, "status_code", None)
    if status is not None and status != 400:
        return False
    message = str(exc).lower()
    markers = (
        "unexpected keyword",
        "unsupported",
        "unrecognized",
        "unknown field",
        "extra inputs",
        "extra_forbidden",
        "not supported",
        "beta",
        "output_config",
        "fallbacks",
    )
    return any(marker in message for marker in markers)


def _claude_chat(
    api_key: str, model: str, system: str, user: str,
    schema: dict[str, Any] | None = None, max_tokens: int = 4000,
) -> str:
    """Claude via the official Anthropic SDK.

    Tries the richest supported request first (server-side refusal fallbacks plus a
    strict JSON schema) and steps down if the installed SDK or the account does not
    support those parameters.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise LLMError("The `anthropic` package is missing. Run: pip install anthropic") from exc

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    output_config = {
        "effort": "low",
        "format": {"type": "json_schema", "schema": schema or SUGGESTION_SCHEMA},
    }
    attempts: list[tuple[bool, dict[str, Any]]] = [
        (
            True,
            {
                "betas": ["server-side-fallback-2026-07-01"],
                "fallbacks": "default",
                "output_config": output_config,
            },
        ),
        (False, {"output_config": output_config}),
        (False, {}),
    ]

    last_error: Exception | None = None
    for use_beta, extra in attempts:
        endpoint = client.beta.messages if use_beta else client.messages
        try:
            response = endpoint.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                **extra,
            )
        except Exception as exc:  # noqa: BLE001 - inspected below, re-raised if fatal
            if _looks_like_unsupported_param(exc):
                last_error = exc
                continue
            raise LLMError("Claude request failed: " + str(exc)) from exc

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMError("Claude declined to answer this request.")
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise LLMError("Claude returned no text content.")
        return text

    raise LLMError("Claude request failed: " + str(last_error))


def _ollama_chat(host: str, model: str, system: str, user: str,
                 max_tokens: int = 2000) -> str:
    url = host.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_predict": max_tokens},
    }
    try:
        data = _http_post(url, {"Content-Type": "application/json"}, payload)
    except LLMError as exc:
        raise LLMError(
            "{}\n\nIs Ollama running? Start it with `ollama serve` and pull the model "
            "with `ollama pull {}`.".format(exc, model)
        ) from exc
    try:
        return data["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LLMError("Unexpected Ollama response: " + json.dumps(data)[:400]) from exc


def call_llm(
    provider: str, model: str, api_key: str, system: str, user: str, ollama_host: str,
    schema: dict[str, Any] | None = None, max_tokens: int = 2000,
) -> str:
    if provider == "Groq":
        return _openai_compatible_chat(
            "https://api.groq.com/openai/v1", api_key, model, system, user,
            max_tokens=max_tokens,
        )
    if provider == "Nvidia":
        # NIM supports response_format on the models we ship as defaults, and
        # _openai_compatible_chat retries without it for any model that does not.
        return _openai_compatible_chat(
            "https://integrate.api.nvidia.com/v1", api_key, model, system, user,
            max_tokens=max_tokens,
        )
    if provider == "Gemini":
        return _gemini_chat(api_key, model, system, user, max_tokens)
    if provider == "Claude":
        return _claude_chat(api_key, model, system, user, schema, max_tokens)
    if provider.startswith("Ollama"):
        return _ollama_chat(ollama_host, model, system, user, max_tokens)
    raise LLMError("Unknown provider: " + provider)


@st.cache_data(show_spinner=False, ttl=3600, max_entries=8)
def fetch_models(provider: str, api_key: str, ollama_host: str = "") -> list[str]:
    """Ask the provider which models it currently serves.

    Providers retire models without warning - a hardcoded list goes stale and the
    user gets an opaque failure. Returns [] on any problem so the caller can fall
    back to the built-in defaults.
    """
    spec = PROVIDERS.get(provider) or {}
    try:
        if provider.startswith("Ollama"):
            response = httpx.get(
                (ollama_host or "http://localhost:11434").rstrip("/") + "/api/tags", timeout=10.0
            )
            response.raise_for_status()
            return sorted(m["name"] for m in response.json().get("models", []))

        url = spec.get("models_url")
        if not url or not api_key:
            return []
        response = httpx.get(
            url, headers={"Authorization": "Bearer " + api_key}, timeout=15.0
        )
        response.raise_for_status()
        return sorted(m["id"] for m in response.json().get("data", []) if m.get("id"))
    except Exception:  # noqa: BLE001 - listing is a convenience, never a blocker
        return []


def check_connection(
    provider: str, model: str, api_key: str, ollama_host: str = ""
) -> tuple[bool, str]:
    """Send the smallest possible request and report what came back.

    Returns (ok, message). Never raises - a failed check is information, not an
    error, and the message is written for the person reading it.
    """
    spec = PROVIDERS.get(provider) or {}
    if spec.get("needs_key") and not api_key:
        return False, "No API key yet. Paste one above, then check again."
    if not model or model == "Custom...":
        return False, "Pick a model first."

    started = time.time()
    try:
        reply = call_llm(
            provider, model, api_key,
            "Reply with the single word: ready.",
            "Are you there?",
            ollama_host, max_tokens=16,
        )
    except LLMError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - the check must never crash the app
        return False, "{}: {}".format(type(exc).__name__, exc)

    took = time.time() - started
    if not (reply or "").strip():
        return False, "{} answered but sent nothing back.".format(provider)
    return True, "{} answered as {} in {:.1f}s. You are ready to build reports.".format(
        provider, model, took)


def _match_column(name: Any, df: pd.DataFrame) -> str | None:
    """Resolve a model-supplied column name against the real schema.

    Models frequently return a near-miss ("revenue" for "Revenue"), so match
    case-insensitively and ignore separators before giving up.
    """
    if not isinstance(name, str) or not name.strip():
        return None
    if name in df.columns:
        return name
    lowered = {str(c).lower(): c for c in df.columns}
    if name.lower() in lowered:
        return lowered[name.lower()]
    squashed = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in df.columns}
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    return squashed.get(key)


# Small connector words a BI heading keeps lowercase, e.g. "Revenue by Region".
_HEADING_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or",
    "per", "the", "to", "vs", "vs.", "with",
}


def clean_heading(text: Any, fallback: str = "") -> str:
    """Normalise a model-supplied heading into a clean, professional title.

    Collapses whitespace and underscores, drops trailing punctuation, and title-cases
    text that arrived either all-lowercase or ALL CAPS - the two sloppy shapes models
    actually produce when asked for a "clean heading". Anything already mixed-case is
    left untouched, since re-casing it risks mangling an acronym the model got right.
    """
    cleaned = re.sub(r"_+", " ", str(text or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(" .:-–—")
    if not cleaned:
        return fallback

    letters = [c for c in cleaned if c.isalpha()]
    words = cleaned.split(" ")
    is_lower = bool(letters) and all(c.islower() for c in letters)
    is_upper = bool(letters) and len(words) > 1 and all(c.isupper() for c in letters)
    if is_lower or is_upper:
        titled = []
        for index, word in enumerate(words):
            lower = word.lower()
            if index > 0 and lower in _HEADING_SMALL_WORDS:
                titled.append(lower)
            else:
                titled.append(lower[:1].upper() + lower[1:])
        cleaned = " ".join(titled)
    return cleaned


def validate_suggestions(
    raw: dict[str, Any], df: pd.DataFrame, palette: list[str] | None = None
) -> dict[str, Any]:
    """Turn a raw LLM payload into chart configs that are guaranteed to render.

    Anything referencing a column that does not exist is dropped rather than
    rendered as an error card.
    """
    palette = palette or DEFAULT_PALETTE
    charts: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    numerics = set(numeric_cols(df))

    for item in raw.get("charts") or []:
        if not isinstance(item, dict):
            continue
        ctype = str(item.get("type", "bar")).strip().lower()
        if ctype in ("column", "column_chart", "bar_chart"):
            ctype = "bar"
        if ctype not in CHART_TYPES:
            ctype = "bar"

        x = _match_column(item.get("x"), df)
        y = _match_column(item.get("y"), df)
        if x is None:
            continue

        agg = str(item.get("agg", "sum")).strip().lower()
        if agg not in AGGREGATIONS:
            agg = "sum"
        # A non-numeric measure only makes sense for count/nunique.
        if (y is None or y not in numerics) and agg not in ("count", "nunique"):
            if ctype in AGGREGATING_TYPES:
                agg = "count"
            elif y is None:
                continue
        if ctype in RAW_TYPES and ctype != "histogram" and y is None:
            continue

        signature = (ctype, x, y, agg)
        if signature in seen:
            continue
        seen.add(signature)

        title = clean_heading(item.get("title"))
        charts.append(
            {
                "id": new_id("chart"),
                "kind": "chart",
                "type": ctype,
                "x": x,
                "y": y,
                "agg": agg,
                "color_by": None,
                "grain": "month" if is_datetime(df[x]) and ctype in ("line", "area") else "none",
                "sort": "none",
                "top_n": 0,
                "title": title or default_title(ctype, x, y, agg),
                "color": palette[len(charts) % len(palette)],
                "size": "medium",
                "show_legend": True,
                "reason": str(item.get("reason") or "").strip(),
            }
        )

    kpis: list[dict[str, Any]] = []
    for item in raw.get("kpis") or []:
        if not isinstance(item, dict):
            continue
        column = _match_column(item.get("column") or item.get("metric"), df)
        if column is None:
            continue
        agg = str(item.get("agg", "sum")).strip().lower()
        if agg not in AGGREGATIONS:
            agg = "sum"
        if column not in numerics and agg not in ("count", "nunique"):
            agg = "count"
        default_kpi_title = "{} of {}".format(agg.title(), column)
        kpis.append(
            {
                "id": new_id("kpi"),
                "kind": "kpi",
                "column": column,
                "agg": agg,
                "title": clean_heading(item.get("title"), default_kpi_title),
                "size": "small",
                "color": palette[0],
            }
        )

    insights = [str(i) for i in (raw.get("insights") or []) if str(i).strip()][:6]
    return {"charts": charts, "kpis": kpis, "insights": insights}


def suggest_dashboard(
    provider: str, model: str, api_key: str, df: pd.DataFrame, question: str,
    ollama_host: str, palette: list[str] | None = None,
) -> dict[str, Any]:
    """Ask the selected LLM for a dashboard, then validate it against the real schema."""
    user_prompt = "DATA PROFILE\n------------\n{}\n\nUSER QUESTION\n-------------\n{}".format(
        profile_dataframe(df), question.strip() or "Show me the most useful insights in this data."
    )
    text = call_llm(provider, model, api_key, SYSTEM_PROMPT, user_prompt, ollama_host)
    return validate_suggestions(extract_json(text), df, palette)


def validate_report(
    raw: dict[str, Any], df: pd.DataFrame, dataset: str = "",
    palette: list[str] | None = None,
) -> dict[str, Any]:
    """Turn a raw multi-sheet payload into ready-to-render pages.

    Each sheet is validated with the same rules as a single-sheet suggestion, so a
    hallucinated column costs one chart rather than the whole report.
    """
    pages: list[dict[str, Any]] = []
    for entry in raw.get("pages") or []:
        if not isinstance(entry, dict):
            continue
        validated = validate_suggestions(entry, df, palette)
        elements = validated["kpis"] + validated["charts"]
        if not elements:
            continue
        for element in elements:
            element["dataset"] = dataset

        layout = entry.get("layout")
        if layout not in PAGE_LAYOUTS:
            layout = "Overview"
        name = clean_heading(entry.get("name"), "Sheet {}".format(len(pages) + 1))
        page = {
            "id": new_id("page"),
            "name": name[:60],
            "layout": layout,
            "elements": elements,
        }
        apply_page_layout(page["elements"], layout)
        pages.append(page)

    insights = [str(i) for i in (raw.get("insights") or []) if str(i).strip()][:8]
    return {"pages": pages, "insights": insights}


def suggest_report(
    provider: str, model: str, api_key: str, df: pd.DataFrame, question: str,
    ollama_host: str, dataset: str = "", palette: list[str] | None = None,
) -> dict[str, Any]:
    """Ask the model to lay out a whole multi-sheet report from one prompt."""
    user_prompt = "DATA PROFILE\n------------\n{}\n\nUSER REQUEST\n------------\n{}".format(
        profile_dataframe(df),
        question.strip() or "Build a useful report for this data.",
    )
    # A report can run to several sheets, each with KPIs and charts, so it needs
    # far more room than a single-sheet suggestion before the reply is cut off.
    text = call_llm(
        provider, model, api_key, REPORT_SYSTEM_PROMPT, user_prompt, ollama_host,
        schema=REPORT_SCHEMA, max_tokens=6000,
    )
    return validate_report(extract_json(text), df, dataset, palette)


# --------------------------------------------------------------------------------------
# 4. Data loading
# --------------------------------------------------------------------------------------


class DataLoadError(RuntimeError):
    """Raised when a data source cannot be read."""


@st.cache_data(show_spinner=False, max_entries=4)
def excel_sheet_names(data: bytes) -> list[str]:
    with pd.ExcelFile(io.BytesIO(data)) as book:
        return list(book.sheet_names)


@st.cache_data(show_spinner=False, max_entries=4)
def load_uploaded_file(name: str, data: bytes, sheet: str | None = None) -> pd.DataFrame:
    """Parse an uploaded file. Cached on (name, bytes) so reruns do not re-parse."""
    lower = name.lower()
    buffer = io.BytesIO(data)
    try:
        if lower.endswith(".csv"):
            df = pd.read_csv(buffer, sep=None, engine="python")
        elif lower.endswith((".tsv", ".tab")):
            df = pd.read_csv(buffer, sep="\t")
        elif lower.endswith((".xlsx", ".xls", ".xlsm")):
            df = pd.read_excel(buffer, sheet_name=sheet or 0)
        elif lower.endswith(".json"):
            df = _json_to_frame(json.loads(data.decode("utf-8")))
        elif lower.endswith(".parquet"):
            df = pd.read_parquet(buffer)
        else:
            raise DataLoadError("Unsupported file type: " + name)
    except DataLoadError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        raise DataLoadError("Could not parse {}: {}".format(name, exc)) from exc
    return coerce_types(df)


def _json_to_frame(payload: Any) -> pd.DataFrame:
    """Flatten a JSON document into a table.

    Handles the two common API shapes: a bare list of records, or an object with
    the records nested under a key such as "data" / "results" / "items".
    """
    if isinstance(payload, list):
        return pd.json_normalize(payload)
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "records", "rows", "values"):
            if isinstance(payload.get(key), list):
                return pd.json_normalize(payload[key])
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return pd.json_normalize(value)
        return pd.json_normalize(payload)
    raise DataLoadError("JSON payload is not tabular.")


@st.cache_data(show_spinner=False, max_entries=4)
def load_from_url(url: str) -> pd.DataFrame:
    try:
        response = httpx.get(url, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DataLoadError("Could not fetch the URL: " + str(exc)) from exc

    content_type = response.headers.get("content-type", "").lower()
    lowered = url.lower().split("?")[0]
    try:
        if lowered.endswith(".parquet"):
            return coerce_types(pd.read_parquet(io.BytesIO(response.content)))
        if lowered.endswith((".xlsx", ".xls")):
            return coerce_types(pd.read_excel(io.BytesIO(response.content)))
        if "json" in content_type or lowered.endswith(".json"):
            return coerce_types(_json_to_frame(response.json()))
        if lowered.endswith((".tsv", ".tab")):
            return coerce_types(pd.read_csv(io.StringIO(response.text), sep="\t"))
        if "csv" in content_type or lowered.endswith(".csv") or "," in response.text[:2000]:
            return coerce_types(pd.read_csv(io.StringIO(response.text), sep=None, engine="python"))
        return coerce_types(_json_to_frame(response.json()))
    except DataLoadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DataLoadError("Fetched the URL but could not parse it as a table: " + str(exc)) from exc


def google_sheet_csv_url(share_url: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", share_url)
    if not match:
        raise DataLoadError(
            "That does not look like a Google Sheets URL. Expected "
            "https://docs.google.com/spreadsheets/d/SHEET_ID/edit"
        )
    sheet_id = match.group(1)
    gid_match = re.search(r"[#&?]gid=(\d+)", share_url)
    gid = gid_match.group(1) if gid_match else "0"
    return "https://docs.google.com/spreadsheets/d/{}/export?format=csv&gid={}".format(sheet_id, gid)


@st.cache_data(show_spinner=False, max_entries=4)
def load_google_sheet(share_url: str) -> pd.DataFrame:
    csv_url = google_sheet_csv_url(share_url)
    try:
        return coerce_types(pd.read_csv(csv_url))
    except Exception as exc:  # noqa: BLE001
        raise DataLoadError(
            "Could not read the sheet. Make sure link sharing is set to "
            '"Anyone with the link can view". ({})'.format(exc)
        ) from exc


def load_from_database(connection_string: str, query: str) -> pd.DataFrame:
    """Run a query through SQLAlchemy. Not cached: credentials should not be memoised."""
    try:
        from sqlalchemy import create_engine, text
    except ImportError as exc:
        raise DataLoadError("SQLAlchemy is not installed. Run: pip install sqlalchemy") from exc
    try:
        engine = create_engine(connection_string)
        with engine.connect() as connection:
            df = pd.read_sql(text(query), connection)
    except Exception as exc:  # noqa: BLE001
        raise DataLoadError("Database query failed: " + str(exc)) from exc
    return coerce_types(df)


SAMPLE_DATASETS = ["Retail sales", "SaaS subscriptions", "Web traffic"]


@st.cache_data(show_spinner=False)
def sample_dataset(name: str, rows: int = 600) -> pd.DataFrame:
    """Synthetic but realistic demo data - no network, no API key."""
    rng = np.random.default_rng(42)

    if name == "SaaS subscriptions":
        start = pd.Timestamp("2024-01-01")
        signups = start + pd.to_timedelta(rng.integers(0, 540, rows), unit="D")
        plan = rng.choice(["Free", "Starter", "Pro", "Enterprise"], rows, p=[0.4, 0.3, 0.22, 0.08])
        seats = np.where(plan == "Enterprise", rng.integers(25, 400, rows), rng.integers(1, 25, rows))
        price = pd.Series(plan).map({"Free": 0, "Starter": 12, "Pro": 39, "Enterprise": 95}).to_numpy()
        return pd.DataFrame(
            {
                "SignupDate": signups,
                "Plan": plan,
                "Region": rng.choice(["NA", "EMEA", "APAC", "LATAM"], rows, p=[0.4, 0.3, 0.2, 0.1]),
                "Channel": rng.choice(["Organic", "Paid", "Referral", "Partner"], rows),
                "Seats": seats,
                "MRR": np.round(seats * price * rng.uniform(0.85, 1.15, rows), 2),
                "ChurnRisk": np.round(rng.beta(2, 6, rows), 3),
                "SupportTickets": rng.poisson(1.6, rows),
            }
        )

    if name == "Web traffic":
        days = pd.date_range("2024-01-01", periods=rows, freq="h")
        base = 400 + 260 * np.sin(np.arange(rows) / 24 * 2 * np.pi)
        sessions = np.maximum(0, base + rng.normal(0, 70, rows)).astype(int)
        return pd.DataFrame(
            {
                "Timestamp": days,
                "Source": rng.choice(
                    ["Google", "Direct", "Social", "Email", "Referral"], rows, p=[0.45, 0.2, 0.15, 0.1, 0.1]
                ),
                "Device": rng.choice(["Desktop", "Mobile", "Tablet"], rows, p=[0.45, 0.48, 0.07]),
                "Country": rng.choice(["US", "IN", "UK", "DE", "BR", "JP"], rows),
                "Sessions": sessions,
                "BounceRate": np.round(rng.uniform(0.2, 0.75, rows), 3),
                "AvgSessionSec": np.round(rng.gamma(4, 40, rows), 1),
                "Conversions": rng.binomial(sessions, 0.021),
            }
        )

    # Default: retail sales
    dates = pd.date_range("2024-01-01", periods=rows, freq="D")[:rows]
    dates = pd.to_datetime(rng.choice(pd.date_range("2024-01-01", "2025-06-30"), rows))
    category = rng.choice(
        ["Electronics", "Apparel", "Home", "Grocery", "Beauty"], rows, p=[0.25, 0.25, 0.2, 0.2, 0.1]
    )
    unit_price = pd.Series(category).map(
        {"Electronics": 320.0, "Apparel": 55.0, "Home": 90.0, "Grocery": 18.0, "Beauty": 34.0}
    ).to_numpy()
    quantity = rng.integers(1, 14, rows)
    revenue = np.round(unit_price * quantity * rng.uniform(0.8, 1.25, rows), 2)
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(dates),
            "Region": rng.choice(["North", "South", "East", "West"], rows),
            "Category": category,
            "Product": rng.choice(["A-100", "B-220", "C-330", "D-440", "E-550", "F-660"], rows),
            "Channel": rng.choice(["Online", "Retail", "Wholesale"], rows, p=[0.5, 0.35, 0.15]),
            "Quantity": quantity,
            "UnitPrice": unit_price,
            "Revenue": revenue,
            "Profit": np.round(revenue * rng.uniform(0.08, 0.34, rows), 2),
            "Returned": rng.choice([0, 1], rows, p=[0.94, 0.06]),
        }
    )


# --------------------------------------------------------------------------------------
# 5. Filters
# --------------------------------------------------------------------------------------


def filter_key(dataset: str, column: str, kind: str) -> str:
    return "flt_{}_{}_{}".format(kind, dataset, column)


def filter_columns_key(dataset: str) -> str:
    return "dm_filter_cols_" + dataset


def apply_saved_filters(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """Re-apply a dataset's filter selections without rendering any widget.

    Filters live per dataset, so a dashboard mixing several datasets filters each
    one by its own controls - including elements on pages that are not on screen.
    """
    chosen = st.session_state.get(filter_columns_key(dataset)) or []
    if not chosen:
        return df

    mask = pd.Series(True, index=df.index)
    for column in chosen:
        if column not in df.columns:
            continue
        series = df[column]
        if is_datetime(series):
            picked = st.session_state.get(filter_key(dataset, column, "date"))
            if isinstance(picked, (list, tuple)) and len(picked) == 2:
                start = pd.Timestamp(picked[0])
                end = pd.Timestamp(picked[1]) + pd.Timedelta(days=1)
                mask &= series.between(start, end, inclusive="left")
        elif is_numeric(series):
            picked = st.session_state.get(filter_key(dataset, column, "num"))
            if isinstance(picked, (list, tuple)) and len(picked) == 2:
                mask &= series.between(picked[0], picked[1])
        else:
            picked = st.session_state.get(filter_key(dataset, column, "cat"))
            if picked:
                mask &= series.astype(str).isin(picked)
    return df[mask]


def render_filter_controls(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    """Filter widgets for one dataset. The caller supplies the container."""
    if True:
        candidates = datetime_cols(df) + categorical_cols(df, max_unique=60) + numeric_cols(df)
        chosen = st.multiselect(
            "Columns to filter on",
            candidates,
            key=filter_columns_key(dataset),
            help="Filters apply to every chart, KPI and export using this dataset.",
        )
        if not chosen:
            st.caption("No filters applied - the whole dataset is in play.")
            return df

        columns = st.columns(min(3, len(chosen)))
        for index, column in enumerate(chosen):
            series = df[column]
            with columns[index % len(columns)]:
                if is_datetime(series):
                    valid = series.dropna()
                    if valid.empty:
                        continue
                    low, high = valid.min().date(), valid.max().date()
                    st.date_input(
                        column, value=(low, high), min_value=low, max_value=high,
                        key=filter_key(dataset, column, "date"),
                    )
                elif is_numeric(series):
                    valid = series.dropna()
                    if valid.empty:
                        continue
                    low, high = float(valid.min()), float(valid.max())
                    if low == high:
                        st.caption("{}: constant ({})".format(column, low))
                        continue
                    st.slider(
                        column, min_value=low, max_value=high, value=(low, high),
                        key=filter_key(dataset, column, "num"),
                    )
                else:
                    options = sorted(series.dropna().astype(str).unique().tolist())
                    st.multiselect(
                        column, options, key=filter_key(dataset, column, "cat"),
                        placeholder="All values",
                    )

        filtered = apply_saved_filters(df, dataset)
        st.caption(
            "Showing {:,} of {:,} rows ({:.1f}%).".format(
                len(filtered), len(df), 100 * len(filtered) / max(len(df), 1)
            )
        )
        return filtered


# --------------------------------------------------------------------------------------
# 6. Chart engine
# --------------------------------------------------------------------------------------

GRAIN_CODES = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}


def default_title(ctype: str, x: str | None, y: str | None, agg: str) -> str:
    if ctype == "histogram":
        return "Distribution of {}".format(x)
    if ctype == "scatter":
        return "{} vs {}".format(x, y)
    if ctype == "box":
        return "{} spread by {}".format(y, x)
    if ctype == "heatmap":
        return "{} heatmap".format(y or "Correlation")
    measure = "Row count" if agg == "count" or not y else "{} of {}".format(agg.title(), y)
    return "{} by {}".format(measure, x)


def prepare_frame(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    """Aggregate the data for one chart. Returns (frame, measure column name)."""
    x, y = cfg.get("x"), cfg.get("y")
    agg = cfg.get("agg", "sum")
    color_by = cfg.get("color_by") or None
    work = df

    if x and x in work.columns and is_datetime(work[x]):
        grain = cfg.get("grain", "none")
        if grain in GRAIN_CODES:
            work = work.copy()
            work[x] = work[x].dt.to_period(GRAIN_CODES[grain]).dt.to_timestamp()

    group = [c for c in [x, color_by] if c and c in work.columns]
    if not group:
        return work, y or ""
    # Dedupe while preserving order (x and color_by may be the same column).
    group = list(dict.fromkeys(group))

    if agg == "count" or not y or y not in work.columns:
        frame = work.groupby(group, dropna=False).size().reset_index(name="count")
        measure = "count"
    else:
        frame = work.groupby(group, dropna=False)[y].agg(agg).reset_index()
        measure = y

    sort = cfg.get("sort", "none")
    if sort == "value_desc":
        frame = frame.sort_values(measure, ascending=False)
    elif sort == "value_asc":
        frame = frame.sort_values(measure, ascending=True)
    elif sort == "x_asc":
        frame = frame.sort_values(group[0], ascending=True)
    elif sort == "x_desc":
        frame = frame.sort_values(group[0], ascending=False)
    elif x and x in work.columns and is_datetime(work[x]):
        frame = frame.sort_values(group[0])

    top_n = int(cfg.get("top_n") or 0)
    if top_n > 0:
        if sort == "none":
            frame = frame.sort_values(measure, ascending=False)
        frame = frame.head(top_n)

    return frame.reset_index(drop=True), measure


def build_figure(
    df: pd.DataFrame, cfg: dict[str, Any], theme: dict[str, Any] | None = None
) -> go.Figure:
    """Build the Plotly figure for one chart config.

    `theme` supplies the categorical palette, Plotly template and continuous scale;
    a chart's own `color` still wins for single-series charts.
    """
    theme = theme or CHART_THEME_DEFAULT
    ctype = cfg.get("type", "bar")
    x, y = cfg.get("x"), cfg.get("y")
    color_by = cfg.get("color_by") or None
    palette = theme.get("palette") or DEFAULT_PALETTE
    color = cfg.get("color") or palette[0]
    height = SIZES.get(cfg.get("size", "medium"), SIZES["medium"])["height"]
    title = cfg.get("title") or default_title(ctype, x, y, cfg.get("agg", "sum"))
    single = [color]

    if ctype == "heatmap":
        fig = _heatmap_figure(df, cfg, theme)
    elif ctype in RAW_TYPES:
        if ctype == "scatter":
            fig = px.scatter(
                df, x=x, y=y, color=color_by,
                color_discrete_sequence=palette if color_by else single,
                opacity=0.75, hover_data=df.columns[:6].tolist(),
            )
        elif ctype == "histogram":
            fig = px.histogram(
                df, x=x, color=color_by,
                color_discrete_sequence=palette if color_by else single, nbins=40,
            )
        else:  # box
            fig = px.box(
                df, x=x, y=y, color=color_by,
                color_discrete_sequence=palette if color_by else single,
            )
    else:
        frame, measure = prepare_frame(df, cfg)
        if frame.empty:
            fig = go.Figure()
            fig.add_annotation(text="No data after filters", showarrow=False)
        elif ctype == "bar":
            fig = px.bar(
                frame, x=x, y=measure, color=color_by,
                color_discrete_sequence=palette if color_by else single,
                barmode="group",
            )
        elif ctype == "line":
            fig = px.line(
                frame, x=x, y=measure, color=color_by, markers=len(frame) <= 60,
                color_discrete_sequence=palette if color_by else single,
            )
        elif ctype == "area":
            fig = px.area(
                frame, x=x, y=measure, color=color_by,
                color_discrete_sequence=palette if color_by else single,
            )
        elif ctype in ("pie", "donut"):
            fig = px.pie(
                frame, names=x, values=measure, hole=0.5 if ctype == "donut" else 0.0,
                color_discrete_sequence=palette,
            )
            fig.update_traces(textposition="inside", textinfo="percent+label")
        elif ctype == "treemap":
            path = [c for c in [color_by, x] if c]
            fig = px.treemap(
                frame, path=path, values=measure, color_discrete_sequence=palette
            )
        else:
            fig = px.bar(frame, x=x, y=measure, color_discrete_sequence=single)

    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        height=height,
        margin=dict(l=10, r=10, t=48, b=10),
        showlegend=bool(cfg.get("show_legend", True)) and color_by is not None,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        hovermode="closest" if ctype in ("scatter", "pie", "donut", "treemap") else "x unified",
        template=theme.get("template", "plotly_white"),
    )
    return fig


def _heatmap_figure(
    df: pd.DataFrame, cfg: dict[str, Any], theme: dict[str, Any] | None = None
) -> go.Figure:
    """Pivot heatmap (x by color_by), falling back to a numeric correlation matrix."""
    theme = theme or CHART_THEME_DEFAULT
    scale = theme.get("scale", "Blues")
    x, y, color_by = cfg.get("x"), cfg.get("y"), cfg.get("color_by")
    if x and color_by and y and all(c in df.columns for c in (x, color_by, y)):
        agg = cfg.get("agg", "sum")
        pivot = df.pivot_table(
            index=color_by, columns=x, values=y,
            aggfunc="size" if agg == "count" else agg,
        )
        return px.imshow(pivot, aspect="auto", color_continuous_scale=scale, text_auto=".2s")

    numerics = numeric_cols(df)
    if len(numerics) < 2:
        fig = go.Figure()
        fig.add_annotation(
            text="A heatmap needs a category on X, a category in 'Break down by', "
                 "and a numeric measure - or at least two numeric columns.",
            showarrow=False,
        )
        return fig
    return px.imshow(
        df[numerics].corr(numeric_only=True).round(2),
        aspect="auto", color_continuous_scale="RdBu", zmin=-1, zmax=1, text_auto=True,
    )


def pivot_frame(df: pd.DataFrame, index: str, columns: str | None, values: str,
                agg: str = "sum") -> pd.DataFrame:
    """Spreadsheet-style pivot with row and column totals, for the Excel export."""
    if index not in df.columns or values not in df.columns:
        raise DataLoadError("Pivot needs an existing index and value column.")
    pivot = df.pivot_table(
        index=index,
        columns=columns if columns and columns in df.columns else None,
        values=values,
        aggfunc="size" if agg == "count" else agg,
    )
    if isinstance(pivot, pd.Series):
        pivot = pivot.to_frame(name=values)
    pivot = pivot.round(2)
    if not pivot.empty:
        pivot.loc["TOTAL"] = pivot.sum(numeric_only=True)
        if pivot.shape[1] > 1:
            pivot["TOTAL"] = pivot.sum(axis=1, numeric_only=True)
    return pivot.reset_index()


def compute_kpi(df: pd.DataFrame, cfg: dict[str, Any]) -> float | None:
    column, agg = cfg.get("column"), cfg.get("agg", "sum")
    if column not in df.columns:
        return None
    series = df[column]
    try:
        if agg == "count":
            return float(series.notna().sum())
        if agg == "nunique":
            return float(series.nunique(dropna=True))
        if not is_numeric(series):
            return float(series.nunique(dropna=True))
        return float(getattr(series, agg)())
    except (TypeError, ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------------------
# 7. Exports
# --------------------------------------------------------------------------------------

EXCEL_MAX_ROWS = 1_048_575


def _excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """openpyxl cannot write timezone-aware datetimes or exotic objects."""
    out = df.copy()
    for col in out.columns:
        series = out[col]
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            out[col] = series.dt.tz_localize(None)
        elif pd.api.types.is_object_dtype(series):
            non_null = series.dropna()
            if not non_null.empty and not isinstance(non_null.iloc[0], (str, int, float, bool)):
                out[col] = series.astype(str)
    out.columns = [str(c) for c in out.columns]
    return out


def _sheet_name(raw: str, fallback: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", "-", str(raw or fallback)).strip() or fallback
    return cleaned[:31]


def summary_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Per-column statistics.

    The numeric stats stay float-typed (NaN where they do not apply) so that Arrow
    can serialise the table for st.dataframe and Excel receives real numbers; the
    date span goes in its own text column.
    """
    rows = []
    for col in df.columns:
        series = df[col]
        row: dict[str, Any] = {
            "Column": str(col),
            "Data Type": str(series.dtype),
            "Non-Null": int(series.notna().sum()),
            "Nulls": int(series.isna().sum()),
            "Unique": int(series.nunique(dropna=True)),
            "Min": np.nan,
            "Max": np.nan,
            "Mean": np.nan,
            "Std Dev": np.nan,
            "Range": "",
        }
        if is_numeric(series) and series.notna().any():
            row["Min"] = round(float(series.min()), 4)
            row["Max"] = round(float(series.max()), 4)
            row["Mean"] = round(float(series.mean()), 4)
            row["Std Dev"] = round(float(series.std()), 4) if len(series) > 1 else 0.0
        elif is_datetime(series) and series.notna().any():
            row["Range"] = "{} to {}".format(series.min(), series.max())
        rows.append(row)

    frame = pd.DataFrame(rows)
    for column in ("Min", "Max", "Mean", "Std Dev"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["Range"] = frame["Range"].astype(str)
    return frame


def frame_for(
    element: dict[str, Any], df: pd.DataFrame, frames: dict[str, pd.DataFrame] | None
) -> pd.DataFrame:
    """The data one element plots - its own dataset when the report has several."""
    if frames:
        return frames.get(element.get("dataset", ""), df)
    return df


def export_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def export_csv_sections(
    df: pd.DataFrame, elements: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame] | None = None,
) -> bytes:
    """Raw data, summary statistics and every chart's aggregated data in one CSV."""
    out = io.StringIO()
    out.write("# RAW DATA\n")
    out.write(df.to_csv(index=False))
    out.write("\n# SUMMARY STATISTICS\n")
    out.write(summary_frame(df).to_csv(index=False))

    for index, element in enumerate(elements, start=1):
        if element.get("kind") != "chart":
            continue
        try:
            frame, _ = prepare_frame(frame_for(element, df, frames), element)
        except Exception:  # noqa: BLE001 - a broken chart must not break the export
            continue
        out.write("\n# CHART {}: {}\n".format(index, element.get("title", "")))
        out.write(frame.to_csv(index=False))

    kpis = [e for e in elements if e.get("kind") == "kpi"]
    if kpis:
        out.write("\n# KPIS\n")
        out.write("Title,Column,Aggregation,Value\n")
        for kpi in kpis:
            out.write(
                "{},{},{},{}\n".format(
                    kpi.get("title", ""), kpi.get("column", ""), kpi.get("agg", ""),
                    compute_kpi(frame_for(kpi, df, frames), kpi),
                )
            )
    return out.getvalue().encode("utf-8")


def export_excel(
    df: pd.DataFrame,
    elements: list[dict[str, Any]],
    theme: str = "Corporate blue",
    sheets: list[str] | None = None,
    pivot: dict[str, str] | None = None,
    dashboard_name: str = "Dashboard",
    frames: dict[str, pd.DataFrame] | None = None,
) -> bytes:
    """Multi-sheet workbook.

    `sheets` selects which of EXCEL_SHEETS to include, `theme` picks the header and
    banding colours from EXCEL_THEMES, and `pivot` (index/columns/values/agg) drives
    the optional pivot-table sheet.
    """
    try:
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError("openpyxl is not installed. Run: pip install openpyxl") from exc

    wanted = sheets if sheets is not None else DEFAULT_EXCEL_SHEETS
    colours = EXCEL_THEMES.get(theme, EXCEL_THEMES["Corporate blue"])
    minimal = theme == "Minimal"

    header_fill = PatternFill(
        start_color=colours["header"], end_color=colours["header"], fill_type="solid"
    )
    band_fill = PatternFill(start_color=colours["band"], end_color=colours["band"], fill_type="solid")
    header_font = Font(bold=True, color=colours["header_text"], size=11)
    thin = Side(style="thin", color=colours["border"])
    medium = Side(style="medium", color=colours["accent"])

    def style(worksheet, banded: bool = True) -> None:
        """Header row, freeze panes, autofilter, banded rows and auto-width columns."""
        max_row, max_col = worksheet.max_row, worksheet.max_column
        for cell in worksheet[1]:
            if not minimal:
                cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(bottom=medium)

        worksheet.freeze_panes = "A2"
        if max_row > 1 and max_col >= 1:
            worksheet.auto_filter.ref = "A1:{}{}".format(get_column_letter(max_col), max_row)

        for row_index in range(2, max_row + 1):
            for cell in worksheet[row_index]:
                cell.border = Border(bottom=thin)
                if banded and not minimal and row_index % 2 == 0:
                    cell.fill = band_fill
                if isinstance(cell.value, float):
                    cell.number_format = "#,##0.00"
                elif isinstance(cell.value, int) and not isinstance(cell.value, bool):
                    cell.number_format = "#,##0"

        for column_cells in worksheet.columns:
            longest = max(
                (len(str(cell.value)) for cell in column_cells[:200] if cell.value is not None),
                default=8,
            )
            letter = get_column_letter(column_cells[0].column)
            worksheet.column_dimensions[letter].width = min(max(longest + 2, 10), 45)

    output = io.BytesIO()
    written = 0
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        used: set[str] = set()

        def add(name: str, frame: pd.DataFrame, banded: bool = True) -> None:
            nonlocal written
            safe = _sheet_name(name, "Sheet{}".format(len(used) + 1))
            suffix = 1
            while safe in used:
                suffix += 1
                safe = _sheet_name("{}-{}".format(safe[:28], suffix), "Sheet{}".format(suffix))
            used.add(safe)
            _excel_safe(frame).to_excel(writer, sheet_name=safe, index=False)
            style(writer.sheets[safe], banded)
            written += 1

        if "Raw data" in wanted:
            add("Raw Data", df.head(EXCEL_MAX_ROWS))

        if "Summary statistics" in wanted:
            add("Summary", summary_frame(df))

        kpis = [e for e in elements if e.get("kind") == "kpi"]
        if "KPIs" in wanted and kpis:
            add("KPIs", pd.DataFrame(
                [
                    {
                        "Title": k.get("title", ""),
                        "Column": k.get("column", ""),
                        "Aggregation": k.get("agg", ""),
                        "Value": compute_kpi(frame_for(k, df, frames), k),
                    }
                    for k in kpis
                ]
            ))

        if "Pivot table" in wanted and pivot:
            try:
                add("Pivot", pivot_frame(
                    df, pivot.get("index"), pivot.get("columns"),
                    pivot.get("values"), pivot.get("agg", "sum"),
                ))
            except Exception:  # noqa: BLE001 - a bad pivot must not sink the workbook
                pass

        if "Chart data (one sheet each)" in wanted:
            for index, element in enumerate(elements, start=1):
                if element.get("kind") != "chart":
                    continue
                try:
                    frame, _ = prepare_frame(frame_for(element, df, frames), element)
                except Exception:  # noqa: BLE001
                    continue
                add("{}. {}".format(index, element.get("title", "Chart")), frame)

        if "Dashboard config" in wanted:
            add("Config", pd.DataFrame(
                [
                    {
                        "Element": e.get("kind", "chart"),
                        "Page": e.get("page", ""),
                        "Dataset": e.get("dataset", ""),
                        "Title": e.get("title", ""),
                        "Chart type": e.get("type", ""),
                        "X": e.get("x", ""),
                        "Y / column": e.get("y") or e.get("column", ""),
                        "Aggregation": e.get("agg", ""),
                        "Break down by": e.get("color_by", ""),
                        "Size": e.get("size", ""),
                    }
                    for e in elements
                ] or [{"Element": "(dashboard is empty)"}]
            ), banded=False)

        if not written:
            # ExcelWriter cannot save a workbook with no sheets.
            add("Raw Data", df.head(EXCEL_MAX_ROWS))

    output.seek(0)
    return output.getvalue()


def figure_to_png(fig: go.Figure, scale: float = 2.0) -> bytes:
    try:
        return fig.to_image(format="png", scale=scale, width=1100, height=620)
    except Exception as exc:  # noqa: BLE001 - kaleido is an optional extra
        raise RuntimeError(
            "PNG and PDF export need the kaleido rendering engine.\n\n"
            "Install the self-contained build with:  pip install kaleido==0.2.1\n"
            "(kaleido>=1.0 also works, but needs a local Chrome - run `plotly_get_chrome`.)\n\n"
            "Underlying error: {}".format(exc)
        ) from exc


def group_elements_by_sheet(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Bucket elements by their "page" tag, preserving first-seen sheet order.

    Doesn't assume the caller kept each sheet's elements contiguous - used by
    exports (PDF) that need to render "whole report" scope one sheet at a time,
    the same grouping the app itself shows on screen.
    """
    sheets: dict[str, list[dict[str, Any]]] = {}
    for element in elements:
        sheets.setdefault(str(element.get("page") or ""), []).append(element)
    return sheets


def export_pdf(
    df: pd.DataFrame, elements: list[dict[str, Any]], dashboard_name: str,
    theme: dict[str, Any] | None = None, frames: dict[str, pd.DataFrame] | None = None,
) -> bytes:
    """Dashboard report: title page, then one section per sheet, mirroring the canvas.

    Each sheet gets its own heading, its own KPI table (only its KPIs) and its own
    charts - the same grouping the app shows on screen - rather than pooling every
    sheet's KPIs and charts into one undifferentiated list.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Image as RLImage,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise RuntimeError("reportlab is not installed. Run: pip install reportlab") from exc

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(letter),
        leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36,
        title=dashboard_name,
    )
    styles = getSampleStyleSheet()
    story: list[Any] = [
        Paragraph(dashboard_name, styles["Title"]),
        Paragraph(
            "Generated by {} - {:,} rows after filters".format(APP_NAME, len(df)),
            styles["Normal"],
        ),
        Spacer(1, 14),
    ]

    def kpi_table(kpis: list[dict[str, Any]]) -> Table:
        table_data = [["Metric", "Value"]]
        for kpi in kpis:
            value = compute_kpi(frame_for(kpi, df, frames), kpi)
            table_data.append([kpi.get("title", ""), humanize(value) if value is not None else "-"])
        table = Table(table_data, colWidths=[4 * inch, 2.4 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B0BEC5")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return table

    sheets = group_elements_by_sheet(elements)
    multi_sheet = len(sheets) > 1

    any_content = False
    for sheet_index, (sheet_name, sheet_elements) in enumerate(sheets.items()):
        kpis = [e for e in sheet_elements if e.get("kind") == "kpi"]
        charts = [e for e in sheet_elements if e.get("kind") == "chart"]
        if not kpis and not charts:
            continue
        any_content = True

        if multi_sheet:
            if sheet_index:
                story.append(PageBreak())
            story.append(Paragraph(sheet_name or "Sheet", styles["Heading1"]))
            story.append(Spacer(1, 8))

        if kpis:
            story.extend([kpi_table(kpis), Spacer(1, 14)])

        for position, element in enumerate(charts):
            if position or kpis or multi_sheet:
                story.append(PageBreak())
            fig = build_figure(frame_for(element, df, frames), element, theme)
            png = figure_to_png(fig)
            story.append(Paragraph(element.get("title", "Chart"), styles["Heading2"]))
            story.append(Spacer(1, 6))
            story.append(RLImage(io.BytesIO(png), width=9.4 * inch, height=5.3 * inch))
            if element.get("reason"):
                story.append(Spacer(1, 6))
                story.append(Paragraph(str(element["reason"]), styles["Italic"]))

    if not any_content:
        story.append(Paragraph("This dashboard has no elements yet.", styles["Normal"]))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def export_html(
    df: pd.DataFrame, elements: list[dict[str, Any]], dashboard_name: str,
    theme: dict[str, Any] | None = None, frames: dict[str, pd.DataFrame] | None = None,
) -> bytes:
    """Self-contained interactive dashboard - opens in any browser, no server needed.

    Each sheet gets its own heading, its own KPI row and its own chart grid - the
    same grouping export_pdf uses - rather than pooling every sheet's KPIs and
    charts into one undifferentiated list. A single sheet renders exactly as
    before, with no heading.
    """
    sheets = group_elements_by_sheet(elements)
    multi_sheet = len(sheets) > 1
    first = True

    def render_sheet(sheet_elements: list[dict[str, Any]]) -> str:
        nonlocal first
        blocks: list[str] = []
        kpis = [e for e in sheet_elements if e.get("kind") == "kpi"]
        if kpis:
            cards = []
            for kpi in kpis:
                value = compute_kpi(frame_for(kpi, df, frames), kpi)
                cards.append(
                    '<div class="kpi"><div class="kpi-label">{}</div>'
                    '<div class="kpi-value">{}</div></div>'.format(
                        st_escape(kpi.get("title", "")), humanize(value) if value is not None else "-"
                    )
                )
            blocks.append('<div class="kpi-row">' + "".join(cards) + "</div>")

        for element in sheet_elements:
            if element.get("kind") == "text":
                blocks.append('<div class="text-block">{}</div>'.format(st_escape(element.get("content", ""))))
                continue
            if element.get("kind") != "chart":
                continue
            fig = build_figure(frame_for(element, df, frames), element, theme)
            width = SIZES.get(element.get("size", "medium"), SIZES["medium"])["width"]
            html = fig.to_html(
                full_html=False,
                include_plotlyjs=True if first else False,
                default_height=SIZES.get(element.get("size", "medium"), SIZES["medium"])["height"],
            )
            first = False
            blocks.append('<div class="card" style="--span:{}">{}</div>'.format(width, html))
        return "".join(blocks)

    if multi_sheet:
        sections = []
        for sheet_name, sheet_elements in sheets.items():
            content = render_sheet(sheet_elements)
            if not content:
                continue
            sections.append(
                '<section class="sheet"><h2>{}</h2><div class="grid">{}</div></section>'.format(
                    st_escape(sheet_name or "Sheet"), content
                )
            )
        body = "".join(sections)
    else:
        only_sheet = next(iter(sheets.values()), [])
        body = '<div class="grid">{}</div>'.format(render_sheet(only_sheet))

    document = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 24px; background: #f4f6f8; color: #1a1a1a; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color: #667085; font-size: 13px; margin-bottom: 20px; }}
  .sheet {{ margin-bottom: 28px; }}
  .sheet h2 {{ font-size: 17px; margin: 0 0 12px; color: #1a1a1a; }}
  .grid {{ display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; }}
  .card {{ grid-column: span var(--span, 6); background: #fff; border: 1px solid #e4e7ec;
          border-radius: 10px; padding: 10px; overflow-x: auto; }}
  .text-block {{ grid-column: span 12; background: #fff; border: 1px solid #e4e7ec;
                border-radius: 10px; padding: 16px; white-space: pre-wrap; }}
  .kpi-row {{ grid-column: span 12; display: flex; flex-wrap: wrap; gap: 16px; }}
  .kpi {{ flex: 1 1 180px; background: #fff; border: 1px solid #e4e7ec; border-radius: 10px;
         padding: 16px; }}
  .kpi-label {{ color: #667085; font-size: 13px; }}
  .kpi-value {{ font-size: 26px; font-weight: 600; margin-top: 4px; }}
  @media (max-width: 820px) {{ .card {{ grid-column: span 12; }} }}
</style></head>
<body>
<h1>{title}</h1>
<div class="meta">Generated by {app} &middot; {rows:,} rows</div>
{body}
</body></html>""".format(
        title=st_escape(dashboard_name), app=APP_NAME, rows=len(df), body=body
    )
    return document.encode("utf-8")


def st_escape(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def dashboard_to_json(name: str, pages: list[dict[str, Any]]) -> bytes:
    """Serialise a whole report: every page, its elements and their grid positions."""
    payload = {
        "dashboard": {
            "id": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "dashboard",
            "name": name,
            "layout": "canvas",
            "version": 2,
            "pages": [
                {
                    "id": page.get("id", new_id("page")),
                    "name": page.get("name", "Page"),
                    "layout": page.get("layout", CUSTOM_LAYOUT),
                    "elements": page.get("elements", []),
                }
                for page in pages
            ],
        }
    }
    return json.dumps(payload, indent=2, default=str).encode("utf-8")


def _normalise_element(
    element: dict[str, Any], placed: list[dict[str, int]]
) -> dict[str, Any]:
    element.setdefault("kind", "chart")
    element.setdefault("id", new_id(element["kind"]))
    element.setdefault("size", "medium")
    element.setdefault("dataset", "")
    if not isinstance(element.get("layout"), dict):
        width, height = element_span(element)
        element["layout"] = next_free_slot(placed, width, height)
    placed.append(element["layout"])
    return element


def dashboard_from_json(data: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Load a report. Accepts both the v2 page format and v1 flat element lists."""
    payload = json.loads(data.decode("utf-8"))
    dashboard = payload.get("dashboard", payload)

    raw_pages = dashboard.get("pages")
    if not isinstance(raw_pages, list):
        # v1 files held a single flat list of elements.
        flat = dashboard.get("elements") or dashboard.get("charts")
        if not isinstance(flat, list):
            raise ValueError("The file does not contain dashboard pages or elements.")
        raw_pages = [{"name": "Page 1", "elements": flat}]

    pages = []
    for raw in raw_pages:
        if not isinstance(raw, dict):
            continue
        placed: list[dict[str, int]] = []
        elements = [
            _normalise_element(element, placed)
            for element in (raw.get("elements") or [])
            if isinstance(element, dict)
        ]
        layout = raw.get("layout")
        pages.append({
            "id": raw.get("id") or new_id("page"),
            "name": str(raw.get("name") or "Page {}".format(len(pages) + 1)),
            "layout": layout if layout in LAYOUT_CHOICES else CUSTOM_LAYOUT,
            "elements": elements,
        })
    if not pages:
        pages = [new_page()]
    return str(dashboard.get("name", "Dashboard")), pages


# --------------------------------------------------------------------------------------
# 8. Drag-and-drop canvas (custom Streamlit component)
# --------------------------------------------------------------------------------------

COMPONENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "components", "dashboard_canvas")
CANVAS_COLUMNS = 12
CANVAS_ROW_PX = 40


def _canvas_component():
    """Declare the canvas component, vendoring plotly.js into it on first use.

    Plotly ships its own browser bundle, so writing that file next to index.html
    keeps the canvas working offline without committing a copy to the repo.
    """
    import streamlit.components.v1 as components

    os.makedirs(COMPONENT_DIR, exist_ok=True)
    bundle = os.path.join(COMPONENT_DIR, "plotly.min.js")
    if not os.path.exists(bundle) or os.path.getsize(bundle) < 100_000:
        from plotly.offline import get_plotlyjs

        with open(bundle, "w", encoding="utf-8") as handle:
            handle.write(get_plotlyjs())
    return components.declare_component("datamind_canvas", path=COMPONENT_DIR)


def element_span(element: dict[str, Any]) -> tuple[int, int]:
    """Grid width and height for an element, from its size preset and kind."""
    width = SIZES.get(element.get("size", "medium"), SIZES["medium"])["width"]
    kind = element.get("kind", "chart")
    height = 4 if kind == "kpi" else 3 if kind == "text" else 9
    return width, height


def next_free_slot(
    taken: list[dict[str, int]], width: int, height: int, columns: int = CANVAS_COLUMNS
) -> dict[str, int]:
    """First grid position of this size that overlaps nothing already placed.

    Scans left to right, top to bottom, so new elements fill gaps before starting
    a new row - the alternative (offsetting by index) collides as soon as a page
    mixes wide charts with small KPI cards.
    """
    width = max(1, min(width, columns))

    def clashes(x: int, y: int) -> bool:
        for other in taken:
            if (
                x < other["x"] + other["w"]
                and other["x"] < x + width
                and y < other["y"] + other["h"]
                and other["y"] < y + height
            ):
                return True
        return False

    for row in range(500):
        for column in range(columns - width + 1):
            if not clashes(column, row):
                return {"x": column, "y": row, "w": width, "h": height}
    return {"x": 0, "y": 500, "w": width, "h": height}


# Five selectable page layouts. Each says how wide and tall to make an element of
# each kind, and in what order to pack them; `hero` singles out the first chart.
# Packing runs through next_free_slot, so rows wrap on their own.
PAGE_LAYOUTS: dict[str, dict[str, Any]] = {
    "Overview": {
        "description": "KPI banner across the top, then charts two to a row.",
        "order": ["kpi", "hero", "chart", "text"],
        "kpi": (3, 4), "hero": None, "chart": (6, 9), "text": (12, 3),
    },
    "Spotlight": {
        "description": "One full-width hero chart, the rest small beneath it.",
        "order": ["kpi", "hero", "chart", "text"],
        "kpi": (3, 4), "hero": (12, 12), "chart": (4, 7), "text": (12, 3),
    },
    "Sidebar": {
        "description": "A tall main chart on the left, everything else stacked right.",
        "order": ["hero", "kpi", "chart", "text"],
        "kpi": (4, 4), "hero": (8, 14), "chart": (4, 7), "text": (12, 3),
    },
    "Column": {
        "description": "One full-width element per row - good for reading top to bottom.",
        "order": ["kpi", "hero", "chart", "text"],
        "kpi": (4, 4), "hero": None, "chart": (12, 8), "text": (12, 3),
    },
    "Compact": {
        "description": "Dense tiling - three charts to a row, six KPIs.",
        "order": ["kpi", "hero", "chart", "text"],
        "kpi": (2, 3), "hero": None, "chart": (4, 7), "text": (4, 3),
    },
}

SHEET_THEMES: dict[str, dict[str, str]] = {
    "Executive": {"layout": "Overview", "palette": "Default",
                  "template": "plotly_white", "scale": "Blues",
                  "note": "KPI banner, charts two per row"},
    "Focus":     {"layout": "Spotlight", "palette": "Ocean",
                  "template": "simple_white", "scale": "Teal",
                  "note": "One hero chart, the rest beneath"},
    "Analyst":   {"layout": "Compact", "palette": "Colourblind safe",
                  "template": "plotly_white", "scale": "Cividis",
                  "note": "Dense tiling, accessible colours"},
    "Story":     {"layout": "Column", "palette": "Sunset",
                  "template": "presentation", "scale": "Oranges",
                  "note": "Full width, top to bottom"},
    "Midnight":  {"layout": "Sidebar", "palette": "Vibrant",
                  "template": "plotly_dark", "scale": "Viridis",
                  "note": "Dark plots, tall chart on the left"},
    "Application": {"layout": "Overview", "palette": "Application",
                    "template": "plotly_dark", "scale": "Blues",
                    "note": "Matches ChatBI's own dark shell"},
}

# "Custom" means the page keeps whatever positions the user dragged it into.
CUSTOM_LAYOUT = "Custom"
LAYOUT_CHOICES = [CUSTOM_LAYOUT] + list(PAGE_LAYOUTS)


def _size_preset_for_width(width: int) -> str:
    """Nearest size preset, so the stacked fallback view matches the canvas."""
    best = "medium"
    for name, spec in SIZES.items():
        if abs(spec["width"] - width) < abs(SIZES[best]["width"] - width):
            best = name
    return best


def apply_page_layout(elements: list[dict[str, Any]], layout: str) -> bool:
    """Re-arrange a page's elements into one of the named layouts.

    Returns False for an unknown layout or "Custom", which leave positions alone.
    """
    spec = PAGE_LAYOUTS.get(layout)
    if not spec:
        return False

    buckets: dict[str, list[dict[str, Any]]] = {"kpi": [], "chart": [], "text": []}
    for element in elements:
        kind = element.get("kind", "chart")
        buckets.get(kind, buckets["chart"]).append(element)

    hero_spec = spec.get("hero")
    hero = buckets["chart"][:1] if hero_spec else []
    rest = buckets["chart"][1:] if hero_spec else buckets["chart"]

    placed: list[dict[str, int]] = []

    def put(element: dict[str, Any], size: tuple[int, int]) -> None:
        width, height = size
        slot = next_free_slot(placed, width, height)
        element["layout"] = slot
        element["size"] = _size_preset_for_width(slot["w"])
        placed.append(slot)

    groups = {"kpi": (buckets["kpi"], spec["kpi"]),
              "hero": (hero, hero_spec or spec["chart"]),
              "chart": (rest, spec["chart"]),
              "text": (buckets["text"], spec["text"])}
    for name in spec["order"]:
        group, size = groups[name]
        for element in group:
            put(element, size)
    return True


def place_element(existing: list[dict[str, Any]], element: dict[str, Any]) -> dict[str, int]:
    """Position one element so it does not sit on top of the ones already there."""
    taken = [
        e["layout"] for e in existing
        if isinstance(e.get("layout"), dict) and e is not element
    ]
    width, height = element_span(element)
    return next_free_slot(taken, width, height)


def canvas_items(
    elements: list[dict[str, Any]], frames: dict[str, pd.DataFrame], theme: dict[str, Any]
) -> list[dict[str, Any]]:
    """Serialise dashboard elements into the payload the canvas renders."""
    items = []
    placed: list[dict[str, int]] = [
        e["layout"] for e in elements if isinstance(e.get("layout"), dict)
    ]
    for element in elements:
        layout = element.get("layout")
        if not isinstance(layout, dict):
            width, height = element_span(element)
            layout = next_free_slot(placed, width, height)
            placed.append(layout)
        item: dict[str, Any] = {
            "id": element["id"],
            "kind": element.get("kind", "chart"),
            "title": element.get("title", ""),
            "layout": layout,
            "color": element.get("color", DEFAULT_PALETTE[0]),
        }
        df = frames.get(element.get("dataset", ""))
        if element.get("kind") == "text":
            item["content"] = element.get("content", "")
        elif element.get("kind") == "kpi":
            value = compute_kpi(df, element) if df is not None else None
            item["value"] = humanize(value) if value is not None else "-"
            item["subtitle"] = "{} of {}".format(
                element.get("agg", "sum"), element.get("column", "?")
            )
        else:
            if df is None:
                item["kind"] = "text"
                item["content"] = "Dataset '{}' is not loaded.".format(element.get("dataset", ""))
            else:
                try:
                    figure = build_figure(df, element, theme)
                    figure.update_layout(margin=dict(l=8, r=8, t=8, b=8), title=None,
                                         autosize=True, height=None)
                    item["figure"] = json.loads(figure.to_json())
                except Exception as exc:  # noqa: BLE001 - one bad chart must not blank the page
                    item["kind"] = "text"
                    item["content"] = "Could not draw '{}': {}".format(item["title"], exc)
        items.append(item)
    return items


def mark_custom_layout(page: dict[str, Any]) -> None:
    """A hand-placed page stops being auto-arranged."""
    page["layout"] = CUSTOM_LAYOUT


def apply_canvas_layout(page: dict[str, Any], layout: list[dict[str, Any]]) -> bool:
    """Write positions from the canvas back onto the page's elements."""
    by_id = {str(entry.get("id")): entry for entry in layout if isinstance(entry, dict)}
    changed = False
    for element in page.get("elements", []):
        entry = by_id.get(element.get("id"))
        if not entry:
            continue
        new = {
            "x": int(entry.get("x", 0)),
            "y": int(entry.get("y", 0)),
            "w": int(entry.get("w", 6)),
            "h": int(entry.get("h", 6)),
        }
        if element.get("layout") != new:
            element["layout"] = new
            element["size"] = _size_preset_for_width(new["w"])
            changed = True
    if changed:
        mark_custom_layout(page)
    return changed


# --------------------------------------------------------------------------------------
# 9. UI
# --------------------------------------------------------------------------------------

DATA_SOURCES = ["Sample data", "Upload file", "Google Sheets", "URL / API", "SQL database"]
# No hard cap on how many datasets can be loaded. This is only the point at which
# the app starts warning that everything lives in memory at once.
DATASET_ADVISORY = 8


WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "components", "workspace")
WORKSPACE_HEIGHT = 860


@st.cache_resource(show_spinner=False)
def _workspace_component():
    """The full application shell. Vendors plotly.js beside it on first use."""
    import streamlit.components.v1 as components

    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    bundle = os.path.join(WORKSPACE_DIR, "plotly.min.js")
    if not os.path.exists(bundle) or os.path.getsize(bundle) < 100_000:
        from plotly.offline import get_plotlyjs

        with open(bundle, "w", encoding="utf-8") as handle:
            handle.write(get_plotlyjs())
    return components.declare_component("datamind_workspace", path=WORKSPACE_DIR)


def field_list(df: pd.DataFrame) -> list[dict[str, str]]:
    """Column names with a coarse type, for the Data pane."""
    out = []
    for column in df.columns:
        kind = ("number" if is_numeric(df[column])
                else "date" if is_datetime(df[column]) else "text")
        out.append({"name": str(column), "kind": kind})
    return out


def suggested_prompts(df: pd.DataFrame | None) -> list[str]:
    """Ready-made prompts phrased around this dataset's own columns.

    The point of the app is that the model does the work, so the empty canvas
    offers real prompts rather than an empty box and a blinking cursor.
    """
    if df is None or df.empty:
        return []
    numerics = numeric_cols(df)
    dates = datetime_cols(df)
    categories = categorical_cols(df, max_unique=40)
    out: list[str] = []

    measure = numerics[0] if numerics else None
    second = numerics[1] if len(numerics) > 1 else None

    if measure and dates:
        out.append("Sheet 1: {} KPIs with a monthly trend. Sheet 2: {} by {}.".format(
            measure, measure, categories[0] if categories else "category"))
    if measure and categories:
        out.append("Which {} has the highest {}? One sheet per breakdown.".format(
            categories[0].lower(), measure.lower()))
    if measure and second:
        out.append("Compare {} against {} and show what drives the difference.".format(
            measure.lower(), second.lower()))
    if dates:
        out.append("Show how everything has changed over {}.".format(dates[0].lower()))
    out.append("Build me a complete overview of this data.")
    return out[:4]


def workspace_payload(
    frames: dict[str, pd.DataFrame], can_ask: bool
) -> dict[str, Any]:
    """Everything the shell needs to draw itself."""
    page = active_page()
    active = active_dataset()
    df = frames.get(active)

    items = canvas_items(page["elements"], frames, current_chart_theme())
    # The format pane needs to know which aggregations make sense per element.
    by_id = {element["id"]: element for element in page["elements"]}
    for item in items:
        element = by_id.get(item["id"], {})
        source = frames.get(element.get("dataset", ""))
        item.update({
            "type": element.get("type"),
            "x": element.get("x"),
            "y": element.get("y"),
            "agg": element.get("agg"),
            "color_by": element.get("color_by"),
            "sort": element.get("sort", "none"),
            "top_n": element.get("top_n", 0),
            "column": element.get("column"),
        })
        if source is not None:
            measure = element.get("y") if item.get("kind") == "chart" else element.get("column")
            if measure in source.columns:
                item["aggOptions"] = allowed_aggregations(source[measure])
            else:
                item["aggOptions"] = ["count", "nunique"]

    return {
        "items": items,
        "datasets": [
            {"name": name, "rows": len(entry["df"]), "cols": len(entry["df"].columns)}
            for name, entry in st.session_state.dm_datasets.items()
        ],
        "fields": field_list(df) if df is not None else [],
        "prompts": suggested_prompts(df),
        "pages": [{"id": p["id"], "name": p["name"]} for p in st.session_state.dm_pages],
        "layouts": LAYOUT_CHOICES,
        "themes": [{"name": n, "note": v["note"]} for n, v in SHEET_THEMES.items()],
        "activeTheme": st.session_state.get("dm_theme_name", ""),
        "activeDataset": active,
        "activePage": page["id"],
        "pageLayout": page.get("layout", CUSTOM_LAYOUT),
        "reportName": st.session_state.dm_dash_name,
        "canAsk": can_ask,
        "columns": CANVAS_COLUMNS,
        "row_height": CANVAS_ROW_PX,
    }


def _new_chart_from_fields(
    chart: str, fields: list[str], df: pd.DataFrame
) -> dict[str, Any]:
    """Turn 'this visual, these fields' into a full chart config.

    Whatever the user picked, this fills the gaps sensibly: a category or date for
    the axis, a numeric column for the measure, counting when there is no measure.
    """
    palette = current_chart_theme()["palette"]
    chosen = [f for f in fields if f in df.columns]
    numerics = [f for f in chosen if is_numeric(df[f])]
    others = [f for f in chosen if f not in numerics]

    dates = datetime_cols(df)
    categories = categorical_cols(df, max_unique=60)
    all_numeric = numeric_cols(df)

    if chart in ("line", "area"):
        default_x = (dates or categories or list(df.columns))[0]
    else:
        default_x = (categories or dates or list(df.columns))[0]

    x = others[0] if others else (default_x if not numerics or chart not in RAW_TYPES
                                  else numerics[0])
    y = numerics[0] if numerics else (all_numeric[0] if all_numeric else None)
    if chart == "scatter":
        x = numerics[0] if numerics else (all_numeric[0] if all_numeric else x)
        y = (numerics[1] if len(numerics) > 1
             else next((c for c in all_numeric if c != x), None))
    if chart == "histogram":
        y = None

    agg = "sum" if y is not None else "count"
    if y is not None and not is_numeric(df[y]):
        agg = "count"

    return {
        "id": new_id("chart"),
        "kind": "chart",
        "type": chart,
        "x": x,
        "y": y,
        "agg": agg,
        "color_by": None,
        "grain": "month" if x in dates and chart in ("line", "area") else "none",
        "sort": "none",
        "top_n": 0,
        "color": palette[0],
        "size": "medium",
        "show_legend": True,
        "title": default_title(chart, x, y, agg),
        "reason": "",
    }


def handle_workspace_action(action: dict[str, Any], config: dict[str, Any]) -> bool:
    """Apply one intent from the shell. Returns True when the app should rerun."""
    kind = action.get("type")
    page = active_page()

    if kind == "layout":
        return apply_canvas_layout(page, action.get("layout") or [])

    if kind == "resize":
        for element in page["elements"]:
            if element.get("id") == action.get("id"):
                layout = dict(element.get("layout") or place_element([], element))
                layout["w"] = max(1, min(int(action.get("w", layout["w"])), CANVAS_COLUMNS))
                layout["h"] = max(3, int(action.get("h", layout["h"])))
                layout["x"] = max(0, min(layout["x"], CANVAS_COLUMNS - layout["w"]))
                element["layout"] = layout
                element["size"] = _size_preset_for_width(layout["w"])
                mark_custom_layout(page)
                return True
        return False

    if kind in ("delete", "duplicate"):
        index = find_index(action.get("id", ""))
        if index < 0:
            return False
        elements = list(page["elements"])
        if kind == "delete":
            elements.pop(index)
        else:
            copy = dict(elements[index])
            copy["id"] = new_id(copy.get("kind", "chart"))
            copy["title"] = str(copy.get("title", "")) + " (copy)"
            copy["layout"] = place_element(elements, copy)
            elements.insert(index + 1, copy)
        page["elements"] = elements
        return True

    if kind == "update":
        patch = action.get("patch") or {}
        for element in page["elements"]:
            if element.get("id") == action.get("id"):
                element.update(patch)
                return True
        return False

    if kind == "add":
        df = get_dataset(active_dataset())
        if df is None:
            return False
        chart = action.get("chart", "bar")
        fields = [f for f in (action.get("fields") or []) if f in df.columns]
        palette = current_chart_theme()["palette"]
        if chart == "text":
            add_element({"id": new_id("text"), "kind": "text", "size": "large",
                         "content": "Double-click to edit this note."})
        elif chart == "kpi":
            numerics = numeric_cols(df)
            column = next((f for f in fields if is_numeric(df[f])),
                          numerics[0] if numerics else list(df.columns)[0])
            agg = "sum" if is_numeric(df[column]) else "count"
            add_element({"id": new_id("kpi"), "kind": "kpi", "column": column, "agg": agg,
                         "title": "{} of {}".format(agg.title(), column),
                         "size": "small", "color": palette[0]})
        else:
            add_element(_new_chart_from_fields(chart, fields, df))
        return True

    if kind == "dataset":
        name = action.get("name", "")
        if name in st.session_state.dm_datasets:
            _set_active_dataset(name)
            return True
        return False

    if kind == "theme":
        spec = SHEET_THEMES.get(action.get("name", ""))
        if not spec:
            return False
        # Colour applies to the whole report; the arrangement is per sheet.
        st.session_state["dm_palette"] = spec["palette"]
        st.session_state["dm_template"] = spec["template"]
        st.session_state["dm_scale"] = spec["scale"]
        st.session_state["dm_theme_name"] = action["name"]
        page["layout"] = spec["layout"]
        apply_page_layout(page["elements"], spec["layout"])
        # Recolour anything still carrying the old palette's colours.
        palette = PALETTES[spec["palette"]]
        for index, element in enumerate(page["elements"]):
            element["color"] = palette[index % len(palette)]
        return True

    if kind == "pagelayout":
        layout = action.get("layout", CUSTOM_LAYOUT)
        if layout in LAYOUT_CHOICES:
            page["layout"] = layout
            apply_page_layout(page["elements"], layout)
            return True
        return False

    if kind == "rename_report":
        st.session_state.dm_dash_name = str(action.get("name") or "Report")[:80]
        return True

    if kind == "page":
        return _handle_page_action(action)

    if kind in ("build", "addhere"):
        return _handle_ask(action, config)

    return False


def _handle_page_action(action: dict[str, Any]) -> bool:
    what = action.get("action")
    pages = st.session_state.dm_pages

    if what == "add":
        page = new_page(unique_page_name("Sheet {}".format(len(pages) + 1)))
        st.session_state.dm_pages = pages + [page]
        st.session_state.dm_active_page = page["id"]
        return True
    if what == "select":
        if any(p["id"] == action.get("id") for p in pages):
            st.session_state.dm_active_page = action["id"]
            return True
        return False
    if what == "delete" and len(pages) > 1:
        st.session_state.dm_pages = [p for p in pages if p["id"] != action.get("id")]
        if st.session_state.dm_active_page == action.get("id"):
            st.session_state.dm_active_page = st.session_state.dm_pages[0]["id"]
        return True
    if what == "rename":
        for page in pages:
            if page["id"] == action.get("id"):
                page["name"] = unique_page_name(str(action.get("name") or page["name"])[:60])
                return True
    return False


def _handle_ask(action: dict[str, Any], config: dict[str, Any]) -> bool:
    """Run the prompt, either building whole sheets or adding to the current one."""
    name = active_dataset()
    df = get_dataset(name)
    if df is None:
        return False
    frame = apply_saved_filters(df, name)
    prompt = str(action.get("prompt") or "").strip()

    try:
        if action["type"] == "build":
            result = suggest_report(
                config["provider"], config["model"], config["api_key"], frame, prompt,
                config["ollama_host"], dataset=name,
                palette=current_chart_theme()["palette"],
            )
            if not result["pages"]:
                st.session_state.dm_flash = (
                    "error", "Nothing in that request matched this data. Try naming the "
                             "columns you care about.")
                return True
            st.session_state.dm_pages = result["pages"]
            st.session_state.dm_active_page = result["pages"][0]["id"]
            st.session_state.dm_last_insights = result["insights"]
            st.session_state.dm_flash = ("success", "Built {} sheet{}: {}".format(
                len(result["pages"]), "" if len(result["pages"]) == 1 else "s",
                ", ".join(p["name"] for p in result["pages"])))
        else:
            result = suggest_dashboard(
                config["provider"], config["model"], config["api_key"], frame, prompt,
                config["ollama_host"], palette=current_chart_theme()["palette"],
            )
            added = 0
            for element in result["kpis"] + result["charts"]:
                copy = dict(element)
                copy["id"] = new_id(copy.get("kind", "chart"))
                copy.pop("layout", None)
                add_element(copy)
                added += 1
            st.session_state.dm_last_insights = result["insights"]
            if added:
                st.session_state.dm_flash = ("success", "Added {} visual{} to this sheet.".format(
                    added, "" if added == 1 else "s"))
            else:
                st.session_state.dm_flash = (
                    "error", "Nothing in that request matched this data.")
    except LLMError as exc:
        st.session_state.dm_flash = ("error", str(exc))
    except Exception as exc:  # noqa: BLE001
        st.session_state.dm_flash = ("error", "Unexpected error: {}".format(exc))
    return True


def get_secret(name: str | None) -> str:
    """Read an API key from st.secrets, then the environment. Never from disk directly."""
    if not name:
        return ""
    try:
        value = st.secrets.get(name)  # raises if no secrets.toml exists
        if value:
            return str(value)
    except Exception:  # noqa: BLE001 - absence of secrets.toml is normal
        pass
    return os.getenv(name, "")


def palette_swatch(colours: list[str], size: int = 16) -> str:
    """A row of colour chips, so the palette choice is visible before applying it."""
    chips = "".join(
        '<span style="display:inline-block;width:{s}px;height:{s}px;background:{c};'
        'border-radius:3px;margin-right:3px;"></span>'.format(s=size, c=colour)
        for colour in colours[:10]
    )
    return '<div style="margin:-6px 0 10px 0;">' + chips + "</div>"


def current_chart_theme() -> dict[str, Any]:
    """The palette/template/scale currently chosen in the sidebar."""
    return chart_theme(
        st.session_state.get("dm_palette", "Default"),
        st.session_state.get("dm_template", "plotly_white"),
        st.session_state.get("dm_scale", "Blues"),
    )


def new_page(name: str = "Page 1") -> dict[str, Any]:
    return {"id": new_id("page"), "name": name, "elements": [], "layout": "Overview"}


def init_state() -> None:
    defaults = {
        # name -> {"df": DataFrame, "source": str}. As many as you like.
        "dm_datasets": {},
        "dm_active_dataset": "",
        # Report pages, Power BI style. Each holds its own elements.
        "dm_pages": [new_page()],
        "dm_active_page": "",
        "dm_dash_name": "My Report",
        "dm_suggestions": None,
        "dm_export_bytes": None,
        "dm_export_meta": None,
        "dm_last_insights": [],
        "dm_last_seq": None,    # de-duplicates repeated actions from the shell
        "dm_flash": None,
        "dm_entered": False,  # entered the workspace before loading any data
        "dm_conn_check": None,  # result of the last "Check connection"
        "dm_custom_palettes": {},  # name -> [hex, ...], imported this session
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if not st.session_state.dm_pages:
        st.session_state.dm_pages = [new_page()]
    if st.session_state.dm_active_page not in {p["id"] for p in st.session_state.dm_pages}:
        st.session_state.dm_active_page = st.session_state.dm_pages[0]["id"]


# ---- datasets -------------------------------------------------------------------------


def dataset_names() -> list[str]:
    return list(st.session_state.dm_datasets)


def active_dataset() -> str:
    """The dataset the builders and filters currently work against."""
    names = dataset_names()
    if not names:
        return ""
    if st.session_state.dm_active_dataset not in names:
        st.session_state.dm_active_dataset = names[0]
    return st.session_state.dm_active_dataset


def get_dataset(name: str) -> pd.DataFrame | None:
    entry = st.session_state.dm_datasets.get(name)
    return entry["df"] if entry else None


def unique_dataset_name(name: str) -> str:
    """Keep dataset names unique so elements can reference them reliably."""
    base = (str(name).strip() or "dataset")[:40]
    existing = set(st.session_state.dm_datasets)
    if base not in existing:
        return base
    for suffix in range(2, 100):
        candidate = "{} ({})".format(base, suffix)
        if candidate not in existing:
            return candidate
    return new_id("dataset")


def add_dataset(name: str, df: pd.DataFrame, source: str) -> str:
    """Register a loaded frame. Returns the (possibly de-duplicated) name."""
    final = unique_dataset_name(name)
    st.session_state.dm_datasets[final] = {"df": df, "source": source}
    _set_active_dataset(final)
    st.session_state.dm_suggestions = None
    return final


def _set_active_dataset(name: str) -> None:
    st.session_state.dm_active_dataset = name


def remove_dataset(name: str) -> None:
    st.session_state.dm_datasets.pop(name, None)
    remaining = dataset_names()
    if st.session_state.dm_active_dataset == name:
        _set_active_dataset(remaining[0] if remaining else "")


def filtered_frames() -> dict[str, pd.DataFrame]:
    """Every loaded dataset with its own filters applied, keyed by name."""
    return {
        name: apply_saved_filters(entry["df"], name)
        for name, entry in st.session_state.dm_datasets.items()
    }


# ---- pages ----------------------------------------------------------------------------


def active_page() -> dict[str, Any]:
    pages = st.session_state.dm_pages
    for page in pages:
        if page["id"] == st.session_state.dm_active_page:
            return page
    st.session_state.dm_active_page = pages[0]["id"]
    return pages[0]


def unique_page_name(name: str) -> str:
    existing = {p["name"] for p in st.session_state.dm_pages}
    if name not in existing:
        return name
    for suffix in range(2, 100):
        candidate = "{} ({})".format(name, suffix)
        if candidate not in existing:
            return candidate
    return new_id("page")


def add_element(element: dict[str, Any]) -> None:
    """Append to the current page, binding the element to the active dataset."""
    page = active_page()
    element.setdefault("dataset", active_dataset())
    element["layout"] = element.get("layout") or place_element(page["elements"], element)
    page["elements"] = page["elements"] + [element]


def find_index(element_id: str) -> int:
    for index, element in enumerate(active_page()["elements"]):
        if element.get("id") == element_id:
            return index
    return -1


def safe_index(options: list[Any], value: Any, fallback: int = 0) -> int:
    try:
        return options.index(value)
    except (ValueError, AttributeError):
        return fallback


def render_model_settings() -> dict[str, Any]:
    """Provider, key and model. Rendered inside the left menu."""
    provider = st.selectbox("Provider", list(PROVIDERS), key="dm_provider")
    spec = PROVIDERS[provider]

    api_key = ""
    ollama_host = "http://localhost:11434"
    if spec["needs_key"]:
        stored = get_secret(spec["key_env"])
        api_key = st.text_input(
            "API key", value=stored, type="password", key="dm_key_" + provider,
            help="Kept in this browser session only - never written to disk.",
        )
        if stored and api_key == stored:
            st.caption("Loaded from {}.".format(spec["key_env"]))
    else:
        ollama_host = st.text_input("Ollama host", value=ollama_host, key="dm_ollama_host")

    live = fetch_models(provider, api_key, ollama_host)
    model_options = (live or spec["models"]) + ["Custom..."]
    picked = st.selectbox(
        "Model", model_options,
        key="dm_model_pick_{}_{}".format(provider, "live" if live else "default"),
    )
    model = picked
    if picked == "Custom...":
        model = st.text_input(
            "Custom model id", value=spec["models"][0], key="dm_model_custom_" + provider
        )
    if live:
        st.caption("{} models available from {}.".format(len(live), provider))
    st.caption(spec["note"])

    # Prove the key and model actually work, rather than finding out mid-report.
    if st.button("Check connection", width="stretch", key="dm_check_conn"):
        with st.spinner("Contacting {}...".format(provider)):
            st.session_state.dm_conn_check = check_connection(
                provider, model, api_key, ollama_host)

    checked = st.session_state.get("dm_conn_check")
    if checked:
        ok, message = checked
        (st.success if ok else st.error)(message)

    return {"provider": provider, "model": model, "api_key": api_key,
            "ollama_host": ollama_host}


def render_appearance_settings() -> None:
    palettes = all_palettes()
    options = list(palettes)
    current = st.session_state.get("dm_palette", "Default")
    palette_name = st.selectbox(
        "Chart palette", options, index=safe_index(options, current), key="dm_palette"
    )
    st.markdown(palette_swatch(palettes[palette_name]), unsafe_allow_html=True)
    st.selectbox(
        "Chart style", CHART_TEMPLATES, key="dm_template",
        help="Try plotly_dark for a dark plotting surface.",
    )
    st.selectbox("Heatmap scale", COLOR_SCALES, key="dm_scale")

    with st.expander("Import or export a theme", expanded=False):
        st.caption(
            "Bring in colours from a real Power BI theme file (its “dataColors” "
            "list), or any JSON file with a “colors” array of hex codes."
        )
        uploaded = st.file_uploader(
            "Theme file (.json)", type=["json"], key="dm_theme_upload"
        )
        if uploaded is not None and st.button(
            "Add this palette", key="dm_theme_import_btn", width="stretch"
        ):
            try:
                name, colors = parse_theme_file(
                    uploaded.getvalue(), fallback_name=uploaded.name.rsplit(".", 1)[0]
                )
                final_name = unique_palette_name(name, all_palettes())
                st.session_state.dm_custom_palettes[final_name] = colors
                st.session_state.dm_palette = final_name
                st.success('Imported "{}" ({} colours) and selected it.'.format(
                    final_name, len(colors)))
                st.rerun()
            except ThemeImportError as exc:
                st.error(str(exc))

        custom = st.session_state.get("dm_custom_palettes") or {}
        if custom:
            st.caption("Imported this session: " + ", ".join(custom))

        export_payload = json.dumps(
            {
                "name": palette_name,
                "colors": palettes[palette_name],
                "dataColors": palettes[palette_name],
                "template": st.session_state.get("dm_template", "plotly_white"),
                "scale": st.session_state.get("dm_scale", "Blues"),
            },
            indent=2,
        )
        st.download_button(
            "Export current theme (.json)",
            data=export_payload,
            file_name=re.sub(r"[^A-Za-z0-9]+", "_", palette_name).strip("_").lower()
                     + "_theme.json",
            mime="application/json",
            width="stretch",
            key="dm_theme_export_btn",
        )


def read_model_config() -> dict[str, Any]:
    """The model settings as they currently stand, without drawing any widget.

    The shell renders on every run but the settings widgets only exist while the
    left menu is open, so the values are read back from session state.
    """
    provider = st.session_state.get("dm_provider") or list(PROVIDERS)[0]
    spec = PROVIDERS.get(provider, PROVIDERS[list(PROVIDERS)[0]])

    api_key = ""
    if spec["needs_key"]:
        api_key = st.session_state.get("dm_key_" + provider) or get_secret(spec["key_env"])
    host = st.session_state.get("dm_ollama_host") or "http://localhost:11434"

    model = st.session_state.get("dm_model_pick_{}_live".format(provider))         or st.session_state.get("dm_model_pick_{}_default".format(provider))         or spec["models"][0]
    if model == "Custom...":
        model = st.session_state.get("dm_model_custom_" + provider) or spec["models"][0]

    return {"provider": provider, "model": model, "api_key": api_key, "ollama_host": host}


# --------------------------------------------------------------------------------------
# Data panel - every source available at once, as many datasets as you like
# --------------------------------------------------------------------------------------


def _try_add(name: str, loader, source: str, errors: list[str]) -> bool:
    """Run a loader, register the result, and collect any failure message."""
    try:
        frame = loader()
    except (DataLoadError, ValueError, TypeError) as exc:
        errors.append("{}: {}".format(name, exc))
        return False
    except Exception as exc:  # noqa: BLE001 - loaders touch the network and disk
        errors.append("{}: {}".format(name, exc))
        return False
    if frame is None or frame.empty:
        errors.append("{}: no rows found.".format(name))
        return False
    try:
        add_dataset(name, frame, source)
    except DataLoadError as exc:
        errors.append(str(exc))
        return False
    return True


def render_data_panel() -> None:
    """Load data from any source at any time - no mode switch required."""
    loaded = len(st.session_state.dm_datasets)
    with st.expander(
        "Data  -  {} loaded".format(loaded) if loaded else "Data  -  add your first dataset",
        expanded=loaded == 0,
    ):
        if loaded >= DATASET_ADVISORY:
            st.caption(
                "{} datasets are loaded and all of them sit in memory. If things slow "
                "down, remove the ones you are finished with.".format(loaded)
            )

        errors: list[str] = []
        tabs = st.tabs(
            ["Upload files", "Sample data", "URL / API", "Google Sheets", "SQL database"]
        )

        with tabs[0]:
            uploads = st.file_uploader(
                "Drop one or more files here",
                type=["csv", "xlsx", "xls", "xlsm", "json", "tsv", "tab", "parquet"],
                accept_multiple_files=True,
                key="dm_uploader",
                help="CSV, Excel, JSON, TSV or Parquet - up to {} MB each.".format(MAX_UPLOAD_MB),
            )
            has_excel = any(
                u.name.lower().endswith((".xlsx", ".xls", ".xlsm")) for u in uploads or []
            )
            split_sheets = False
            if has_excel:
                split_sheets = st.checkbox(
                    "Import every worksheet as its own dataset",
                    value=True,
                    key="dm_split_sheets",
                    help="Off means only the first worksheet of each workbook is loaded.",
                )
            if uploads and st.button(
                "Add {} file{}".format(len(uploads), "" if len(uploads) == 1 else "s"),
                type="primary", width="stretch", key="dm_add_uploads",
            ):
                for upload in uploads:
                    data = upload.getvalue()
                    stem = upload.name.rsplit(".", 1)[0]
                    is_excel = upload.name.lower().endswith((".xlsx", ".xls", ".xlsm"))
                    if is_excel and split_sheets:
                        try:
                            sheets = excel_sheet_names(data)
                        except Exception as exc:  # noqa: BLE001
                            errors.append("{}: {}".format(upload.name, exc))
                            continue
                        for sheet in sheets:
                            _try_add(
                                "{} - {}".format(stem, sheet),
                                lambda d=data, n=upload.name, sh=sheet: load_uploaded_file(n, d, sh),
                                upload.name, errors,
                            )
                    else:
                        _try_add(
                            stem,
                            lambda d=data, n=upload.name: load_uploaded_file(n, d),
                            upload.name, errors,
                        )
                if not errors:
                    st.rerun()

        with tabs[1]:
            sample = st.selectbox("Dataset", SAMPLE_DATASETS, key="dm_sample_name")
            if st.button("Add sample data", type="primary", width="stretch", key="dm_add_sample"):
                if _try_add(sample, lambda: sample_dataset(sample), "Sample data", errors):
                    st.rerun()

        with tabs[2]:
            url = st.text_input(
                "Data URL", placeholder="https://example.com/data.csv", key="dm_api_url"
            )
            st.caption("CSV, TSV, JSON or Parquet. The format is detected automatically.")
            if url and st.button("Fetch", type="primary", width="stretch", key="dm_add_url"):
                label = url.rsplit("/", 1)[-1].split("?")[0] or "api-data"
                if _try_add(label, lambda: load_from_url(url), url[:60], errors):
                    st.rerun()

        with tabs[3]:
            sheet_url = st.text_input(
                "Share URL",
                placeholder="https://docs.google.com/spreadsheets/d/.../edit",
                key="dm_gsheet_url",
            )
            st.caption('Sharing must be set to "Anyone with the link can view".')
            if sheet_url and st.button(
                "Fetch sheet", type="primary", width="stretch", key="dm_add_gsheet"
            ):
                if _try_add(
                    "Google Sheet", lambda: load_google_sheet(sheet_url), "Google Sheets", errors
                ):
                    st.rerun()

        with tabs[4]:
            connection = st.text_input(
                "Connection string", type="password",
                placeholder="postgresql://user:pass@host:5432/db", key="dm_conn",
            )
            query = st.text_area(
                "SQL query", value="SELECT * FROM my_table LIMIT 1000", key="dm_query", height=90
            )
            st.caption(
                "Needs the matching driver, e.g. psycopg2-binary, pymysql, "
                "snowflake-sqlalchemy or databricks-sql-connector."
            )
            if connection and query and st.button(
                "Run query", type="primary", width="stretch", key="dm_add_sql"
            ):
                if _try_add(
                    "SQL result", lambda: load_from_database(connection, query), "SQL", errors
                ):
                    st.rerun()

        for message in errors:
            st.error(message)


# --------------------------------------------------------------------------------------
# Build modes
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# Element editing
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------------------


def render_export_controls(df: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> None:
    formats = {
        "PDF report": ("pdf", "application/pdf"),
        "Excel workbook (.xlsx)": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "CSV - raw data": ("csv", "text/csv"),
        "CSV - data + summary + chart data": ("csv", "text/csv"),
        "Interactive HTML dashboard": ("html", "text/html"),
        "Report layout (JSON)": ("json", "application/json"),
    }
    name = st.session_state.dm_dash_name

    scope_row = st.columns([2, 2])
    with scope_row[0]:
        scope = st.radio(
            "Include", ["This page", "Whole report"], horizontal=True, key="dm_export_scope"
        )
    page = active_page()
    if scope == "This page":
        elements = [dict(e, page=page["name"]) for e in page["elements"]]
    else:
        elements = [
            dict(element, page=p["name"])
            for p in st.session_state.dm_pages
            for element in p["elements"]
        ]
    with scope_row[1]:
        st.caption("{} element{} selected.".format(
            len(elements), "" if len(elements) == 1 else "s"))

    columns = st.columns([3, 1, 1])
    with columns[0]:
        choice = st.selectbox("Export format", list(formats), key="dm_export_format")
    with columns[1]:
        generate = st.button("Generate", width="stretch")

    # Excel gets its own options: which sheets, and how the workbook is styled.
    sheets = DEFAULT_EXCEL_SHEETS
    workbook_theme = "Corporate blue"
    pivot: dict[str, str] | None = None
    if choice.startswith("Excel"):
        with st.container(border=True):
            st.markdown("**Workbook options**")
            option_columns = st.columns([3, 2])
            with option_columns[0]:
                sheets = st.multiselect(
                    "Sheets to include", EXCEL_SHEETS, default=DEFAULT_EXCEL_SHEETS,
                    key="dm_excel_sheets",
                )
            with option_columns[1]:
                workbook_theme = st.selectbox(
                    "Formatting theme", list(EXCEL_THEMES), key="dm_excel_theme"
                )
                swatch = EXCEL_THEMES[workbook_theme]
                st.markdown(
                    palette_swatch(["#" + swatch["header"], "#" + swatch["accent"],
                                    "#" + swatch["band"]], size=18),
                    unsafe_allow_html=True,
                )

            if "Pivot table" in sheets:
                st.markdown("**Pivot table**")
                pivot_columns = st.columns(4)
                all_columns = list(df.columns)
                numerics = numeric_cols(df) or all_columns
                with pivot_columns[0]:
                    p_index = st.selectbox("Rows", all_columns, key="dm_pivot_index")
                with pivot_columns[1]:
                    p_cols = st.selectbox(
                        "Columns", ["(none)"] + all_columns, key="dm_pivot_columns"
                    )
                with pivot_columns[2]:
                    p_values = st.selectbox("Values", numerics, key="dm_pivot_values")
                with pivot_columns[3]:
                    p_agg = st.selectbox(
                        "Aggregation", allowed_aggregations(df[p_values]), key="dm_pivot_agg"
                    )
                pivot = {
                    "index": p_index,
                    "columns": None if p_cols == "(none)" else p_cols,
                    "values": p_values,
                    "agg": p_agg,
                }

    if generate:
        extension, mime = formats[choice]
        slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower() or "dashboard"
        theme = current_chart_theme()
        try:
            if choice == "PDF report":
                payload = export_pdf(df, elements, name, theme, frames)
            elif choice.startswith("Excel"):
                payload = export_excel(
                    df, elements, workbook_theme, sheets, pivot, name, frames
                )
            elif choice == "CSV - raw data":
                payload = export_csv(df)
            elif choice.startswith("CSV - data"):
                payload = export_csv_sections(df, elements, frames)
            elif choice.startswith("Interactive"):
                payload = export_html(df, elements, name, theme, frames)
            else:
                pages = (
                    [page] if scope == "This page" else st.session_state.dm_pages
                )
                payload = dashboard_to_json(name, pages)
            st.session_state.dm_export_bytes = payload
            st.session_state.dm_export_meta = ("{}.{}".format(slug, extension), mime, choice)
        except Exception as exc:  # noqa: BLE001 - optional deps report their own fix
            st.session_state.dm_export_bytes = None
            st.session_state.dm_export_meta = None
            st.error(str(exc))

    with columns[2]:
        if st.session_state.dm_export_bytes and st.session_state.dm_export_meta:
            filename, mime, _ = st.session_state.dm_export_meta
            st.download_button(
                "Download",
                data=st.session_state.dm_export_bytes,
                file_name=filename,
                mime=mime,
                width="stretch",
                type="primary",
            )
    if st.session_state.dm_export_meta:
        st.caption(
            "Ready: {} ({:,} KB)".format(
                st.session_state.dm_export_meta[0],
                max(1, len(st.session_state.dm_export_bytes) // 1024),
            )
        )


# --------------------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------------------


WELCOME_CSS = """
<style>
  /* The start screen is a normal page, not the full-bleed shell: it needs its own
     rules, because the shell's zero-padding would clip the content off the left. */
  #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
  [data-testid="stStatusWidget"], header {display: none !important;}
  section[data-testid="stSidebar"] {display: none !important;}
  .stApp {background: #f4f6fa;}
  .block-container {
    max-width: 1000px !important;
    padding: 0 28px 72px !important;
    margin: 0 auto !important;
  }

  .dm-bar {
    background: #1e2430; margin: 0 -28px 0; padding: 0 24px; height: 46px;
    display: flex; align-items: center; gap: 9px; color: #eef1f6;
    font-size: 13.5px; font-weight: 600; letter-spacing: -.01em;
  }
  .dm-bar .dot {width: 9px; height: 9px; border-radius: 2px; background: #2f6fed;}
  .dm-bar .muted {color: #8d97ab; font-weight: 400;}

  .dm-hero {padding: 52px 0 6px;}
  .dm-hero h1 {
    font-size: 34px; line-height: 1.18; font-weight: 700; letter-spacing: -.028em;
    color: #101828; margin: 0 0 12px; max-width: 20ch;
  }
  .dm-hero p {
    font-size: 16.5px; color: #5b6676; margin: 0; max-width: 56ch; line-height: 1.55;
  }

  .dm-rule {height: 1px; background: #dde3ed; margin: 34px 0 26px;}
  .dm-eyebrow {
    font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
    color: #8792a5; margin-bottom: 14px;
  }

  .dm-step {display: flex; gap: 11px; align-items: flex-start;}
  .dm-step .num {
    flex: 0 0 auto; width: 23px; height: 23px; border-radius: 50%;
    background: #e9f0fe; color: #1a4fbf; font-size: 12px; font-weight: 700;
    display: grid; place-items: center; margin-top: 1px;
  }
  .dm-step .txt b {display: block; font-size: 14.5px; color: #101828; margin-bottom: 2px;}
  .dm-step .txt span {font-size: 13.5px; color: #5b6676; line-height: 1.5;}

  /* Sample cards: the primary action, so they get the weight. Streamlit gives its
     bordered container no testid of its own, so match the one holding our card. */
  [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .dm-card-title) {
    background: #fff; border-radius: 11px !important; padding: 15px 16px 13px !important;
    box-shadow: 0 1px 2px rgba(16,24,40,.05); border-color: #dde3ed !important;
  }
  .dm-card-title {font-size: 15px; font-weight: 640; color: #101828; margin-bottom: 2px;}
  .dm-card-note {font-size: 12.5px; color: #667085; line-height: 1.45; min-height: 34px;}
  .dm-card-meta {
    font-size: 11px; color: #8792a5; letter-spacing: .02em; margin-bottom: 10px;
    font-variant-numeric: tabular-nums;
  }

  .stButton button {
    border-radius: 7px !important; font-weight: 550 !important; font-size: 13.5px !important;
    border-color: #c6cfdd !important;
  }
  .stButton button[kind="primary"] {
    background: #2f6fed !important; border-color: #2f6fed !important;
  }
  .stButton button[kind="primary"]:hover {background: #2560d8 !important;}

  .dm-foot {
    margin-top: 30px; padding-top: 18px; border-top: 1px solid #dde3ed;
    font-size: 12.5px; color: #8792a5;
  }

  @media (prefers-color-scheme: dark) {
    .stApp {background: #12161f;}
    .dm-hero h1 {color: #e9edf5;}
    .dm-hero p, .dm-step .txt span {color: #95a0b3;}
    .dm-step .txt b, .dm-card-title {color: #e9edf5;}
    .dm-rule, .dm-foot {border-color: #2c3444;}
    .dm-rule {background: #2c3444;}
    .dm-step .num {background: #1b2740; color: #a8c2ff;}
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .dm-card-title)
      {background: #1a1f2b; border-color: #2c3444 !important;}
    .dm-card-note {color: #95a0b3;}
  }
</style>
"""

SAMPLE_BLURBS = {
    "Retail sales": ("Orders across regions, categories and channels, with revenue, "
                     "profit and returns.", "600 rows · 10 columns"),
    "SaaS subscriptions": ("Sign-ups by plan and region, with seats, MRR, churn risk "
                           "and support load.", "600 rows · 8 columns"),
    "Web traffic": ("Hourly sessions by source, device and country, with bounce rate "
                    "and conversions.", "600 rows · 8 columns"),
}


def render_welcome() -> None:
    """First run: what the app does, and the two quickest ways to start."""
    st.markdown(
        '<div class="dm-bar"><span class="dot"></span>ChatBI'
        '<span class="muted">&nbsp;· agentic BI</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="dm-hero">'
        "<h1>Turn a spreadsheet into a dashboard by describing it.</h1>"
        "<p>Load your data, say what you want to see in plain English, and drag the "
        "result into shape. No SQL, no chart-building knowledge.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="dm-rule"></div>', unsafe_allow_html=True)
    steps = st.columns(3, gap="large")
    copy = [
        ("Add data", "A CSV or Excel file, a link, a database — or one of the samples "
                     "below. Load as many as you like."),
        ("Ask", "“Sheet 1: revenue KPIs and a monthly trend. Sheet 2: sales by region.” "
                "It builds exactly those sheets."),
        ("Arrange", "Drag visuals around, resize them, then export to PDF, Excel or a "
                    "shareable HTML file."),
    ]
    for column, (index, (title, body)) in zip(steps, enumerate(copy, start=1)):
        with column:
            st.markdown(
                '<div class="dm-step"><div class="num">{}</div><div class="txt">'
                "<b>{}</b><span>{}</span></div></div>".format(index, title, body),
                unsafe_allow_html=True,
            )

    st.markdown('<div class="dm-rule"></div>', unsafe_allow_html=True)
    st.markdown('<div class="dm-eyebrow">Start with sample data</div>',
                unsafe_allow_html=True)

    cards = st.columns(3, gap="medium")
    for column, name in zip(cards, SAMPLE_DATASETS):
        blurb, meta = SAMPLE_BLURBS.get(name, ("", ""))
        with column:
            with st.container(border=True):
                st.markdown('<div class="dm-card-title">{}</div>'
                            '<div class="dm-card-note">{}</div>'
                            '<div class="dm-card-meta">{}</div>'.format(name, blurb, meta),
                            unsafe_allow_html=True)
                if st.button("Open", key="quick_" + name, width="stretch",
                             type="primary"):
                    errors: list[str] = []
                    _try_add(name, lambda n=name: sample_dataset(n), "Sample data", errors)
                    for message in errors:
                        st.error(message)
                    if not errors:
                        st.rerun()

    st.markdown('<div class="dm-rule"></div>', unsafe_allow_html=True)
    own = st.columns([1.1, 3])
    with own[0]:
        if st.button("Add your own data", width="stretch", key="dm_bring_own"):
            # Straight into the workspace; open the left menu (☰, top left) for sources.
            st.session_state.dm_entered = True
            st.rerun()
    with own[1]:
        st.caption("Files, Google Sheets, an API or a database — all in one panel.")

    st.markdown(
        '<div class="dm-foot">Runs on your machine. Works with Groq, Gemini, Nvidia, '
        "Claude or a local Ollama model — the AI is optional, everything else works "
        "without a key.</div>",
        unsafe_allow_html=True,
    )


SHELL_CSS = """
<style>
  /* The shell owns the window; only the left menu sits outside it. The sidebar
     opens expanded (see initial_sidebar_state in main()), so the header carries
     nothing this app needs - hide it outright rather than float a control over it. */
  #MainMenu, footer, header, [data-testid="stToolbar"], [data-testid="stDecoration"],
  [data-testid="stStatusWidget"] {display: none !important;}
  [data-testid="stSidebarCollapseButton"] button {color: #dbe1ec !important;}
  .block-container {padding: 0 !important; max-width: 100% !important;}
  [data-testid="stAppViewContainer"] > .main {padding: 0 !important;}
  iframe[title="datamind_workspace"] {display: block; border: 0;}
  .stApp {background: #f2f4f7;}

  /* ---- the left menu ------------------------------------------------------ */
  section[data-testid="stSidebar"] {
    background: #1e2430 !important;
    border-right: 1px solid #11151d;
    width: 340px !important;
    transform: none !important;
    margin-left: 0 !important;
    visibility: visible !important;
  }
  section[data-testid="stSidebar"] .block-container,
  section[data-testid="stSidebar"] > div {padding-top: 14px !important;}
  section[data-testid="stSidebar"] * {color: #dbe1ec;}
  section[data-testid="stSidebar"] h1,
  section[data-testid="stSidebar"] h2,
  section[data-testid="stSidebar"] h3 {color: #fff !important;}
  section[data-testid="stSidebar"] [data-testid="stExpander"] {
    background: #262d3b; border: 1px solid #333b4c; border-radius: 9px;
  }
  section[data-testid="stSidebar"] [data-testid="stExpander"] summary {
    font-size: 13px; font-weight: 600;
  }
  section[data-testid="stSidebar"] input,
  section[data-testid="stSidebar"] textarea,
  section[data-testid="stSidebar"] [data-baseweb="select"] > div {
    background: #171c26 !important; border-color: #39404f !important;
    color: #e7ecf5 !important;
  }
  section[data-testid="stSidebar"] .stButton button {
    background: #2f6fed; border: 1px solid #2f6fed; color: #fff;
    border-radius: 7px; font-weight: 550;
  }
  section[data-testid="stSidebar"] .stButton button:hover {background: #2560d8;}
  section[data-testid="stSidebar"] .stButton button[kind="secondary"] {
    background: transparent; border-color: #3d4557; color: #ccd3e0;
  }
  section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
    background: #171c26; border-color: #39404f;
  }
  /* The sidebar toggle: the "menu button" on the left. */
  [data-testid="stSidebarCollapsedControl"] button,
  [data-testid="stSidebarCollapseButton"] button {color: #dbe1ec !important;}

  .menu-h {
    font-size: 10.5px; font-weight: 700; letter-spacing: .11em; text-transform: uppercase;
    color: #7f8ca3 !important; margin: 16px 0 6px;
  }
  .menu-brand {
    display: flex; align-items: center; gap: 8px; font-weight: 650; font-size: 14px;
    padding-bottom: 10px; border-bottom: 1px solid #333b4c; margin-bottom: 4px;
  }
  .menu-brand .dot {width: 9px; height: 9px; border-radius: 2px; background: #2f6fed;}
  .theme-note {font-size: 11.5px; color: #8d97ab !important; line-height: 1.45;}
</style>
"""


def render_workspace_shell(config: dict[str, Any], frames: dict[str, pd.DataFrame]):
    """Draw the app shell and hand back whatever the user did in it."""
    component = _workspace_component()
    spec = PROVIDERS[config["provider"]]
    can_ask = bool(config["api_key"]) or not spec["needs_key"]
    payload = workspace_payload(frames, can_ask)
    payload["sig"] = hashlib.md5(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return component(height=WORKSPACE_HEIGHT, key="dm_workspace", default=None, **payload)


def render_menu(config: dict[str, Any], frames: dict[str, pd.DataFrame]) -> None:
    """The left menu: data, model, look and export. Opened by the ☰ button."""
    with st.sidebar:
        st.markdown(
            '<div class="menu-brand"><span class="dot"></span>ChatBI</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="menu-h">Data</div>', unsafe_allow_html=True)
        render_data_panel()

        names = dataset_names()
        if names:
            active = active_dataset()
            for name in names:
                entry = st.session_state.dm_datasets[name]
                row = st.columns([3, 1])
                row[0].caption("{}{}  ·  {:,} rows".format(
                    name, "  (in use)" if name == active else "", len(entry["df"])))
                if row[1].button("Remove", key="rm_ds_" + name, width="stretch"):
                    remove_dataset(name)
                    st.rerun()

            st.markdown('<div class="menu-h">Filters</div>', unsafe_allow_html=True)
            with st.expander("Filter {}".format(active_dataset()), expanded=False):
                render_filter_controls(get_dataset(active_dataset()), active_dataset())

        st.markdown('<div class="menu-h">AI model</div>', unsafe_allow_html=True)
        with st.expander("Provider and key", expanded=not config.get("api_key")):
            render_model_settings()

        st.markdown('<div class="menu-h">Sheet theme</div>', unsafe_allow_html=True)
        render_theme_menu()

        st.markdown('<div class="menu-h">Appearance</div>', unsafe_allow_html=True)
        with st.expander("Colours and chart style", expanded=False):
            render_appearance_settings()

        if names:
            st.markdown('<div class="menu-h">Export</div>', unsafe_allow_html=True)
            with st.expander("Export and save", expanded=False):
                name = active_dataset()
                render_export_controls(apply_saved_filters(get_dataset(name), name), frames)
                st.markdown("---")
                uploaded = st.file_uploader(
                    "Load a saved report (.json)", type=["json"], key="dm_layout_upload"
                )
                if uploaded is not None and st.button("Apply saved report", width="stretch"):
                    try:
                        title, pages = dashboard_from_json(uploaded.getvalue())
                        st.session_state.dm_dash_name = title
                        st.session_state.dm_pages = pages
                        st.session_state.dm_active_page = pages[0]["id"]
                        st.success("Loaded {} sheet(s).".format(len(pages)))
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error("Could not read that report file: {}".format(exc))


def render_theme_menu() -> None:
    """Pick a theme: it recolours the report and re-flows the current sheet."""
    current = st.session_state.get("dm_theme_name", "")
    options = ["Custom"] + list(SHEET_THEMES)
    picked = st.selectbox(
        "Theme", options, index=safe_index(options, current or "Custom"),
        key="dm_theme_pick",
        help="Sets the palette, the plot style and how this sheet is arranged.",
    )
    if picked != "Custom":
        st.markdown('<div class="theme-note">{}</div>'.format(SHEET_THEMES[picked]["note"]),
                    unsafe_allow_html=True)
        st.markdown(palette_swatch(PALETTES[SHEET_THEMES[picked]["palette"]], size=14),
                    unsafe_allow_html=True)
    if st.button("Apply theme", width="stretch", key="dm_apply_theme",
                 disabled=picked == "Custom"):
        handle_workspace_action({"type": "theme", "name": picked}, read_model_config())
        st.rerun()


def main() -> None:
    st.set_page_config(
        page_title=APP_NAME, page_icon="📊", layout="wide",
        # Expanded by default: real BI tools (Power BI, Tableau) keep the left panel
        # visible rather than behind a toggle. It also sidesteps a real fragility -
        # the collapsed-state "open sidebar" control's DOM shape varies across
        # Streamlit builds and was once silently absent, making the whole menu
        # (data, keys, export) unreachable with no error anywhere. Expanded needs no
        # such control: it renders unconditionally, and Streamlit's own collapse
        # arrow (on the sidebar's edge) still lets a user hide it for more canvas.
        initial_sidebar_state="expanded",
    )
    init_state()
    config = read_model_config()

    # First run shows the intro; "Add your own data" enters the workspace without it.
    # Each screen brings its own stylesheet - the shell's full-bleed rules would clip
    # the intro's content off the left edge.
    if not dataset_names() and not st.session_state.get("dm_entered"):
        st.markdown(WELCOME_CSS, unsafe_allow_html=True)
        render_welcome()
        return

    st.markdown(SHELL_CSS, unsafe_allow_html=True)

    frames = filtered_frames()

    flash = st.session_state.pop("dm_flash", None)
    if flash:
        (st.success if flash[0] == "success" else st.error)(flash[1])

    action = render_workspace_shell(config, frames)
    if isinstance(action, dict) and action.get("seq") != st.session_state.get("dm_last_seq"):
        st.session_state.dm_last_seq = action.get("seq")
        if handle_workspace_action(action, config):
            st.rerun()

    # The menu renders after the shell, so an action that writes a widget's state
    # (a theme changing the palette) reruns before that widget is instantiated.
    render_menu(config, frames)


if __name__ == "__main__":
    main()
