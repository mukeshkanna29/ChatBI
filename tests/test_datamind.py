"""Tests for ChatBI.

Three layers:
  * pure logic  - type coercion, LLM response parsing, the chart engine, exports
  * connectors  - request shape / response parsing / fallbacks, against mocks
  * the app     - the real Streamlit UI driven headlessly via AppTest

Run with pytest:

    pip install pytest
    pytest tests/ -v

Or without pytest:

    python tests/test_datamind.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import types

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")

PAYLOAD = {
    "charts": [
        {
            "type": "bar", "x": "Category", "y": "Revenue", "agg": "sum",
            "title": "Revenue by category", "reason": "shows the mix",
        }
    ],
    "kpis": [{"column": "Revenue", "agg": "sum", "title": "Total revenue"}],
    "insights": ["Electronics leads."],
}
BODY = json.dumps(PAYLOAD)


def _overlap(a, b):
    """True when two grid rectangles intersect."""
    return (a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"]
            and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"])


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return app.sample_dataset("Retail sales")


# ---------------------------------------------------------------------------
# Helpers and data handling
# ---------------------------------------------------------------------------


def test_sample_datasets_load():
    for name in app.SAMPLE_DATASETS:
        frame = app.sample_dataset(name)
        assert len(frame) > 0
        assert app.numeric_cols(frame) and app.datetime_cols(frame)


def test_type_coercion_is_conservative():
    messy = pd.DataFrame(
        {
            "Date": ["2024-01-05", "2024-02-11", "2024-03-02"],
            "Revenue": ["$1,200.50", "$980.00", "$3,410.25"],
            "ZipCode": ["02134", "10001", "94107"],
            "Region": ["North", "South", "East"],
        }
    )
    coerced = app.coerce_types(messy)
    assert app.is_datetime(coerced["Date"])
    assert app.is_numeric(coerced["Revenue"])
    # Leading-zero identifiers must not become numbers.
    assert not app.is_numeric(coerced["ZipCode"])
    assert not app.is_numeric(coerced["Region"])


def test_profile_lists_every_column(df):
    profile = app.profile_dataframe(df)
    for column in df.columns:
        assert column in profile


@pytest.mark.parametrize(
    "raw",
    [
        '{"charts": [], "kpis": [], "insights": []}',
        'Sure!\n```json\n{"charts": [], "kpis": [], "insights": ["a"]}\n```\nHope that helps.',
        'Here you go: {"charts": [{"title": "a } b"}], "kpis": [], "insights": []} trailing',
    ],
)
def test_extract_json_survives_prose_and_fences(raw):
    assert isinstance(app.extract_json(raw), dict)


def test_extract_json_rejects_garbage():
    with pytest.raises(app.LLMError):
        app.extract_json("no json here")


# The unterminated case below is the literal shape meta/llama-3.3-70b-instruct
# returned against live NIM: a complete answer that never emitted the final "}".
UNTERMINATED = (
    '{"charts": [\n'
    '{"type": "bar", "x": "Product", "y": "Profit", "agg": "sum", "title": "Profit by '
    'Product", "reason": "Shows which products drive the most profit"},\n'
    '{"type": "line", "x": "Date", "y": "Revenue", "agg": "sum", "title": "Revenue Trend", '
    '"reason": "Trend over time"}\n],\n'
    '"kpis": [{"column": "Profit", "agg": "sum", "title": "Total Profit"}],\n'
    '"insights": ["Top products can be identified", "Regional preferences vary"]'
)


@pytest.mark.parametrize(
    "label,raw,expected_charts",
    [
        ("missing final brace", UNTERMINATED, 2),
        (
            "cut mid-object",
            '{"charts": [{"type": "bar", "x": "A", "y": "B", "agg": "sum", "title": "T", '
            '"reason": "r"}, {"type": "line", "x": "Da',
            1,
        ),
        ("cut mid-string", '{"charts": [], "kpis": [], "insights": ["a partial senten', 0),
        (
            "trailing comma then cut",
            '{"charts": [{"type":"bar","x":"A","y":"B","agg":"sum","title":"T","reason":"r"}],',
            1,
        ),
        (
            "cut in a nested value",
            '{"charts": [{"type":"bar","x":"A","y":"B","agg":"sum","title":"T","reason":"r"}], '
            '"kpis": [{"column": "P", "agg": "su',
            1,
        ),
    ],
)
def test_extract_json_repairs_truncated_output(label, raw, expected_charts):
    assert len(app.extract_json(raw)["charts"]) == expected_charts


def test_repaired_json_still_produces_working_charts(df):
    """The repair must yield configs that validate and render, not just parse."""
    result = app.validate_suggestions(app.extract_json(UNTERMINATED), df)
    assert len(result["charts"]) == 2
    assert all(app.build_figure(df, chart) is not None for chart in result["charts"])


@pytest.mark.parametrize("raw", ["[1, 2, 3]", '"just a string"', "42"])
def test_extract_json_rejects_non_objects(raw):
    """A bare array must not reach the validator, which expects a mapping."""
    with pytest.raises(app.LLMError):
        app.extract_json(raw)


def test_extract_json_finds_an_object_inside_an_array():
    assert app.extract_json('[{"charts": [], "kpis": [], "insights": []}]')["charts"] == []


def test_validate_suggestions_repairs_model_output(df):
    raw = {
        "charts": [
            # lowercase names should resolve to the real columns
            {"type": "bar", "x": "category", "y": "revenue", "agg": "sum", "title": "R", "reason": ""},
            {"type": "line", "x": "Date", "y": "Profit", "agg": "sum", "title": "T", "reason": ""},
            # exact duplicate of the first
            {"type": "bar", "x": "Category", "y": "Revenue", "agg": "sum", "title": "D", "reason": ""},
            # hallucinated column - must be dropped
            {"type": "pie", "x": "NoSuchColumn", "y": "Revenue", "agg": "sum", "title": "X", "reason": ""},
            # nonsense type and aggregation - must be repaired
            {"type": "weird", "x": "Region", "y": "Region", "agg": "bogus", "title": "Y", "reason": ""},
        ],
        "kpis": [
            {"column": "Revenue", "agg": "sum", "title": "Total"},
            {"column": "ghost", "agg": "sum", "title": "nope"},
        ],
        "insights": ["one", "two"],
    }
    result = app.validate_suggestions(raw, df)

    assert result["charts"][0]["x"] == "Category"
    assert all(chart["x"] in df.columns for chart in result["charts"])
    assert all(chart["type"] in app.CHART_TYPES for chart in result["charts"])
    assert all(chart["agg"] in app.AGGREGATIONS for chart in result["charts"])
    assert len(result["charts"]) == 3  # duplicate and hallucination removed
    assert len(result["kpis"]) == 1
    assert result["insights"] == ["one", "two"]


# ---------------------------------------------------------------------------
# Heading cleanup - "neat headings" on charts, KPIs and sheet names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("revenue by region", "Revenue by Region"),
        ("REVENUE BY REGION", "Revenue by Region"),
        ("Revenue by Region", "Revenue by Region"),          # already clean - untouched
        ("revenue_by_region", "Revenue by Region"),
        ("monthly profit trend.", "Monthly Profit Trend"),    # trailing punctuation dropped
        ("  top 10 products by profit  ", "Top 10 Products by Profit"),
        ("q3 revenue vs q2", "Q3 Revenue vs Q2"),
    ],
)
def test_clean_heading_normalises_sloppy_titles(raw, expected):
    assert app.clean_heading(raw) == expected


def test_clean_heading_leaves_acronyms_alone():
    # A single all-caps word is assumed to be an intentional acronym, not shouting.
    assert app.clean_heading("USD") == "USD"


def test_clean_heading_falls_back_when_empty():
    assert app.clean_heading("", "Sheet 1") == "Sheet 1"
    assert app.clean_heading(None, "Sheet 1") == "Sheet 1"
    assert app.clean_heading("   ", "Sheet 1") == "Sheet 1"
    assert app.clean_heading("") == ""


def test_chart_titles_come_back_clean(df):
    raw = {
        "charts": [
            {"type": "bar", "x": "Category", "y": "Revenue", "agg": "sum",
             "title": "revenue by category", "reason": ""},
        ],
        "kpis": [{"column": "Revenue", "agg": "sum", "title": "TOTAL REVENUE"}],
        "insights": [],
    }
    result = app.validate_suggestions(raw, df)
    assert result["charts"][0]["title"] == "Revenue by Category"
    assert result["kpis"][0]["title"] == "Total Revenue"


def test_chart_title_falls_back_to_a_generated_one_when_blank(df):
    raw = {"charts": [{"type": "bar", "x": "Category", "y": "Revenue", "agg": "sum",
                       "title": "   ", "reason": ""}],
           "kpis": [], "insights": []}
    result = app.validate_suggestions(raw, df)
    assert result["charts"][0]["title"] == app.default_title("bar", "Category", "Revenue", "sum")


def test_kpi_title_falls_back_when_blank(df):
    raw = {"charts": [], "kpis": [{"column": "Revenue", "agg": "sum", "title": ""}],
           "insights": []}
    result = app.validate_suggestions(raw, df)
    assert result["kpis"][0]["title"] == "Sum of Revenue"


def test_sheet_names_come_back_clean(df):
    raw = {"pages": [{"name": "regional performance", "layout": "Overview",
                      "kpis": [{"column": "Revenue", "agg": "sum", "title": "Total"}],
                      "charts": [{"type": "bar", "x": "Region", "y": "Revenue", "agg": "sum",
                                  "title": "T", "reason": "r"}]}],
           "insights": []}
    result = app.validate_report(raw, df)
    assert result["pages"][0]["name"] == "Regional Performance"


def test_blank_sheet_name_gets_a_numbered_fallback(df):
    raw = {"pages": [{"name": "  ", "layout": "Overview",
                      "kpis": [], "charts": [{"type": "bar", "x": "Region", "y": "Revenue",
                                              "agg": "sum", "title": "T", "reason": "r"}]}],
           "insights": []}
    result = app.validate_report(raw, df)
    assert result["pages"][0]["name"] == "Sheet 1"


# ---------------------------------------------------------------------------
# The senior-analyst persona and sheet-completeness prompt requirements
# ---------------------------------------------------------------------------


def test_prompts_establish_a_senior_analyst_persona():
    for prompt in (app.SYSTEM_PROMPT, app.REPORT_SYSTEM_PROMPT):
        assert "ten years" in prompt
        assert "Business Intelligence" in prompt or "BI" in prompt


def test_report_prompt_asks_for_four_to_five_charts_per_sheet():
    assert "4 to 5" in app.REPORT_SYSTEM_PROMPT


def test_report_prompt_asks_for_topical_sheet_names():
    prompt = app.REPORT_SYSTEM_PROMPT.lower()
    assert "topic" in prompt
    assert "sheet 2" in prompt  # named as the placeholder example to avoid


def test_report_schema_bounds_charts_and_kpis_per_sheet():
    page_schema = app.REPORT_SCHEMA["properties"]["pages"]["items"]
    charts = page_schema["properties"]["charts"]
    kpis = page_schema["properties"]["kpis"]
    assert charts["minItems"] <= 4 <= charts["maxItems"]
    assert charts["minItems"] <= 5 <= charts["maxItems"]
    assert kpis["minItems"] <= 2 and kpis["maxItems"] >= 4


def test_suggestion_schema_bounds_charts_and_kpis():
    charts = app.SUGGESTION_SCHEMA["properties"]["charts"]
    kpis = app.SUGGESTION_SCHEMA["properties"]["kpis"]
    assert charts["minItems"] <= 4 <= charts["maxItems"]
    assert charts["minItems"] <= 5 <= charts["maxItems"]
    assert kpis["minItems"] <= 2 and kpis["maxItems"] >= 4


def test_report_schema_still_carries_the_sheets_marker_the_prompt_test_relies_on():
    # test_report_schema_is_sent_to_claude asserts "SHEETS" appears in the system
    # prompt handed to Claude - pin the literal here too so a future prompt edit
    # that silently drops it fails loudly in more than one place.
    assert "SHEETS" in app.REPORT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Chart engine
# ---------------------------------------------------------------------------

CHART_SPECS = [
    ("bar", "Category", "Revenue", "sum", None),
    ("bar", "Category", "Revenue", "mean", "Channel"),
    ("line", "Date", "Revenue", "sum", None),
    ("area", "Date", "Profit", "sum", "Region"),
    ("scatter", "Revenue", "Profit", "sum", "Category"),
    ("pie", "Region", "Revenue", "sum", None),
    ("donut", "Channel", "Quantity", "sum", None),
    ("histogram", "Revenue", None, "count", None),
    ("box", "Category", "Profit", "sum", None),
    ("treemap", "Product", "Revenue", "sum", "Category"),
    ("heatmap", "Category", "Revenue", "sum", "Region"),
    ("heatmap", None, None, "sum", None),          # correlation-matrix fallback
    ("bar", "Region", None, "count", None),        # count with no measure
    ("bar", "Region", "Category", "nunique", None),
]


def _config(ctype, x, y, agg, color_by, **overrides):
    config = {
        "id": "t", "kind": "chart", "type": ctype, "x": x, "y": y, "agg": agg,
        "color_by": color_by, "grain": "month", "sort": "value_desc", "top_n": 5,
        "color": "#4C78A8", "size": "medium", "show_legend": True,
        "title": app.default_title(ctype, x, y, agg),
    }
    config.update(overrides)
    return config


@pytest.mark.parametrize("ctype,x,y,agg,color_by", CHART_SPECS)
def test_every_chart_type_renders(df, ctype, x, y, agg, color_by):
    assert app.build_figure(df, _config(ctype, x, y, agg, color_by)) is not None


@pytest.mark.parametrize("palette", list(app.PALETTES))
def test_every_palette_renders(df, palette):
    theme = app.chart_theme(palette=palette)
    config = _config("bar", "Category", "Revenue", "sum", "Region")
    config.pop("color")  # let the palette drive the colour
    figure = app.build_figure(df, config, theme)
    assert figure is not None


@pytest.mark.parametrize("template", app.CHART_TEMPLATES)
def test_every_chart_template_applies(df, template):
    theme = app.chart_theme(template=template)
    figure = app.build_figure(df, _config("bar", "Category", "Revenue", "sum", None), theme)
    assert figure.layout.template is not None


def test_theme_falls_back_on_unknown_names():
    theme = app.chart_theme(palette="nope", template="nope", scale="nope")
    assert theme["palette"] == app.DEFAULT_PALETTE
    assert theme["template"] == "plotly_white"
    assert theme["scale"] == "Blues"


def test_heatmap_uses_the_theme_scale(df):
    theme = app.chart_theme(scale="Viridis")
    config = _config("heatmap", "Category", "Revenue", "sum", "Region")
    assert app.build_figure(df, config, theme) is not None


def test_chart_colour_overrides_the_palette(df):
    """An explicitly picked colour must win over the palette."""
    config = _config("bar", "Category", "Revenue", "sum", None, color="#123456")
    figure = app.build_figure(df, config, app.chart_theme(palette="Vibrant"))
    assert "#123456" in str(figure.data[0].marker.color)


def test_suggestions_use_the_active_palette(df):
    raw = {"charts": [{"type": "bar", "x": "Category", "y": "Revenue", "agg": "sum",
                       "title": "T", "reason": "r"}],
           "kpis": [{"column": "Revenue", "agg": "sum", "title": "K"}], "insights": []}
    result = app.validate_suggestions(raw, df, palette=app.PALETTES["Ocean"])
    assert result["charts"][0]["color"] == app.PALETTES["Ocean"][0]
    assert result["kpis"][0]["color"] == app.PALETTES["Ocean"][0]


def test_palette_swatch_is_html():
    swatch = app.palette_swatch(app.PALETTES["Vibrant"])
    assert swatch.startswith("<div") and app.PALETTES["Vibrant"][0] in swatch


def test_parse_theme_file_reads_power_bi_export():
    raw = json.dumps({"name": "Sunset", "dataColors": ["#FF6B35", "#004E89", "#1A659E"]}).encode()
    name, colors = app.parse_theme_file(raw)
    assert name == "Sunset"
    assert colors == ["#FF6B35", "#004E89", "#1A659E"]


def test_parse_theme_file_reads_our_own_export():
    raw = json.dumps({"name": "Mine", "colors": ["#111111", "#222222"]}).encode()
    name, colors = app.parse_theme_file(raw)
    assert name == "Mine"
    assert colors == ["#111111", "#222222"]


def test_parse_theme_file_falls_back_to_default_name():
    raw = json.dumps({"colors": ["#111111", "#222222"]}).encode()
    name, _ = app.parse_theme_file(raw, fallback_name="Imported")
    assert name == "Imported"


def test_parse_theme_file_drops_invalid_hex_entries():
    raw = json.dumps({"colors": ["#111111", "not-a-colour", "#222222"]}).encode()
    _, colors = app.parse_theme_file(raw)
    assert colors == ["#111111", "#222222"]


@pytest.mark.parametrize("bad", [b"not json", b"[1, 2, 3]",
                                  json.dumps({"colors": ["#111111"]}).encode(),
                                  json.dumps({"colors": "nope"}).encode(),
                                  json.dumps({}).encode()])
def test_parse_theme_file_rejects_unusable_input(bad):
    with pytest.raises(app.ThemeImportError):
        app.parse_theme_file(bad)


def test_unique_palette_name_avoids_collisions():
    existing = {"Sunset": [], "Sunset (2)": []}
    assert app.unique_palette_name("Fresh", existing) == "Fresh"
    assert app.unique_palette_name("Sunset", existing) == "Sunset (3)"
    assert app.unique_palette_name("", existing) == "Imported"


def test_all_palettes_merges_builtins_and_imports(ws):
    ws["dm_custom_palettes"] = {"My theme": ["#111111", "#222222"]}
    merged = app.all_palettes()
    assert merged["Default"] == app.PALETTES["Default"]
    assert merged["My theme"] == ["#111111", "#222222"]


def test_chart_theme_resolves_an_imported_palette(ws):
    ws["dm_custom_palettes"] = {"My theme": ["#111111", "#222222"]}
    theme = app.chart_theme(palette="My theme")
    assert theme["palette"] == ["#111111", "#222222"]


@pytest.mark.parametrize("grain", app.TIME_GRAINS)
def test_time_grains(df, grain):
    frame, measure = app.prepare_frame(
        df, _config("line", "Date", "Revenue", "sum", None, grain=grain, sort="none", top_n=0)
    )
    assert measure == "Revenue"
    assert not frame.empty


def test_top_n_limits_rows(df):
    frame, _ = app.prepare_frame(df, _config("bar", "Product", "Revenue", "sum", None, top_n=3))
    assert len(frame) == 3


def test_empty_frame_renders_placeholder(df):
    assert app.build_figure(df.head(0), _config("bar", "Category", "Revenue", "sum", None))


@pytest.mark.parametrize("agg", app.AGGREGATIONS)
def test_kpi_aggregations(df, agg):
    assert app.compute_kpi(df, {"column": "Revenue", "agg": agg}) is not None


def test_kpi_on_missing_column_returns_none(df):
    assert app.compute_kpi(df, {"column": "nope", "agg": "sum"}) is None


def test_allowed_aggregations_hides_nonsense(df):
    assert app.allowed_aggregations(df["Revenue"]) == app.AGGREGATIONS
    assert app.allowed_aggregations(df["Region"]) == ["count", "nunique"]


def test_humanize():
    assert app.humanize(1500000) == "1.50M"
    assert app.humanize(1234.5) == "1.23K"
    assert app.humanize(600) == "600"          # whole numbers keep no decimals
    assert app.humanize(12.75) == "12.75"
    assert app.humanize(None) == "-"


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def elements():
    return [_config(*spec) for spec in CHART_SPECS[:5]] + [
        {"id": "k1", "kind": "kpi", "column": "Revenue", "agg": "sum",
         "title": "Total Revenue", "size": "small"},
        {"id": "t1", "kind": "text", "content": "## Notes", "size": "large"},
    ]


def test_summary_frame_is_arrow_serialisable(df):
    pa = pytest.importorskip("pyarrow")
    summary = app.summary_frame(df)
    # Streamlit serialises through Arrow; mixed-type columns break it.
    assert pa.Table.from_pandas(summary).num_rows == len(df.columns)


def test_csv_exports(df, elements):
    assert app.export_csv(df).startswith(b"Date,Region")
    sectioned = app.export_csv_sections(df, elements).decode("utf-8")
    assert "# RAW DATA" in sectioned
    assert "# SUMMARY STATISTICS" in sectioned
    assert "# CHART 1" in sectioned
    assert "# KPIS" in sectioned


def test_excel_export_has_a_sheet_per_chart(df, elements):
    book = pd.ExcelFile(io.BytesIO(app.export_excel(df, elements)))
    assert {"Raw Data", "Summary", "KPIs"} <= set(book.sheet_names)
    assert len(book.sheet_names) == 3 + 5


def test_excel_export_handles_timezone_aware_dates(df):
    tz_aware = df.copy()
    tz_aware["Date"] = tz_aware["Date"].dt.tz_localize("UTC")
    assert app.export_excel(tz_aware, [])


def test_excel_sheet_selection(df, elements):
    """Only the requested sheets are written."""
    book = pd.ExcelFile(io.BytesIO(app.export_excel(df, elements, sheets=["Summary statistics"])))
    assert book.sheet_names == ["Summary"]

    book = pd.ExcelFile(io.BytesIO(app.export_excel(df, elements, sheets=["Raw data", "KPIs"])))
    assert set(book.sheet_names) == {"Raw Data", "KPIs"}


def test_excel_never_writes_an_empty_workbook(df):
    """openpyxl cannot save a book with no sheets, so one must always survive."""
    book = pd.ExcelFile(io.BytesIO(app.export_excel(df, [], sheets=[])))
    assert book.sheet_names == ["Raw Data"]
    # "KPIs" was asked for but there are no KPI elements to write.
    book = pd.ExcelFile(io.BytesIO(app.export_excel(df, [], sheets=["KPIs"])))
    assert book.sheet_names == ["Raw Data"]


def test_excel_config_sheet_describes_the_dashboard(df, elements):
    data = app.export_excel(df, elements, sheets=["Dashboard config"])
    config = pd.read_excel(io.BytesIO(data), sheet_name="Config")
    assert len(config) == len(elements)
    assert {"Element", "Title", "Chart type"} <= set(config.columns)


def test_excel_pivot_sheet(df, elements):
    pivot = {"index": "Category", "columns": "Region", "values": "Revenue", "agg": "sum"}
    data = app.export_excel(df, elements, sheets=["Pivot table"], pivot=pivot)
    sheet = pd.read_excel(io.BytesIO(data), sheet_name="Pivot")
    assert "TOTAL" in sheet["Category"].astype(str).tolist()
    assert "TOTAL" in sheet.columns


def test_excel_bad_pivot_does_not_sink_the_workbook(df, elements):
    bad = {"index": "NoSuchColumn", "columns": None, "values": "Revenue", "agg": "sum"}
    book = pd.ExcelFile(
        io.BytesIO(app.export_excel(df, elements, sheets=["Raw data", "Pivot table"], pivot=bad))
    )
    assert book.sheet_names == ["Raw Data"]


@pytest.mark.parametrize("theme", list(app.EXCEL_THEMES))
def test_every_excel_theme_writes_a_readable_workbook(df, elements, theme):
    data = app.export_excel(df, elements, theme=theme, sheets=["Raw data"])
    assert len(pd.read_excel(io.BytesIO(data), sheet_name="Raw Data")) == len(df)


def test_pivot_frame_totals(df):
    pivot = app.pivot_frame(df, "Category", "Region", "Revenue", "sum")
    assert "TOTAL" in pivot["Category"].astype(str).tolist()
    assert "TOTAL" in pivot.columns
    # A pivot with no column split still works and adds a row total only.
    flat = app.pivot_frame(df, "Category", None, "Revenue", "sum")
    assert "TOTAL" in flat["Category"].astype(str).tolist()


def test_html_export_is_self_contained(df, elements):
    import re

    html = app.export_html(df, elements, "Test").decode("utf-8")
    assert "Total Revenue" in html
    assert "plotly" in html.lower()
    # The file must open offline, so no tag may fetch anything over the network.
    # (Checked on tags rather than raw text: the embedded plotly bundle contains
    # URL strings of its own for map tiles, which these charts never request.)
    external = re.findall(
        r"""<(?:script|link|img|iframe)[^>]*(?:src|href)\s*=\s*["']([^"']+)["']""", html, re.I
    )
    assert external == []


def test_html_export_whole_report_mirrors_each_sheet(df, elements):
    """"Whole report" scope tags every element with its sheet; the HTML should keep
    each sheet's own KPIs and charts together - one <section> per sheet with its own
    heading - instead of pooling everything from every sheet into one KPI row and
    one flat chart grid."""
    import re

    sheet_one = [dict(e, page="Overview") for e in elements]
    # elements[:2] are chart configs only; include the KPI ("k1") too so both
    # sheets get their own kpi-row.
    kpi = next(e for e in elements if e.get("kind") == "kpi")
    sheet_two = [dict(e, page="Detail") for e in elements[:2] + [kpi]]
    html = app.export_html(df, sheet_one + sheet_two, "Whole Report").decode("utf-8")
    assert html.count('<section class="sheet">') == 2
    assert html.count("<h2>Overview</h2>") == 1
    assert html.count("<h2>Detail</h2>") == 1
    # Each sheet keeps its own KPI row rather than pooling every sheet's KPIs.
    assert html.count('class="kpi-row"') == 2
    # Exercises the multi-sheet branch for real; a second sheet's worth of content
    # must produce more bytes than either sheet alone (mirrors the PDF export test).
    solo = app.export_html(df, sheet_one, "Solo").decode("utf-8")
    assert len(html) > len(solo)
    # No tag may fetch anything over the network, same guarantee as the single-sheet case.
    external = re.findall(
        r"""<(?:script|link|img|iframe)[^>]*(?:src|href)\s*=\s*["']([^"']+)["']""", html, re.I
    )
    assert external == []


def test_html_export_single_sheet_has_no_heading(df, elements):
    """A single sheet must render exactly as before: no <section>/<h2> wrapper."""
    tagged = [dict(e, page="Overview") for e in elements]
    html = app.export_html(df, tagged, "Test").decode("utf-8")
    assert "<section" not in html
    assert "<h2>" not in html
    assert "Total Revenue" in html


def test_pdf_export(df, elements):
    data = app.export_pdf(df, elements, "Test Dashboard")
    assert data[:4] == b"%PDF"


def test_group_elements_by_sheet_buckets_and_preserves_order():
    elements = [
        {"id": "a", "page": "Overview"},
        {"id": "b", "page": "Detail"},
        {"id": "c", "page": "Overview"},  # not contiguous - still lands with "a"
        {"id": "d"},                      # no page tag at all
    ]
    grouped = app.group_elements_by_sheet(elements)
    assert list(grouped) == ["Overview", "Detail", ""]
    assert [e["id"] for e in grouped["Overview"]] == ["a", "c"]
    assert [e["id"] for e in grouped["Detail"]] == ["b"]
    assert [e["id"] for e in grouped[""]] == ["d"]


def test_pdf_export_whole_report_mirrors_each_sheet(df, elements):
    """"Whole report" scope tags every element with its sheet; the PDF should keep
    each sheet's own KPIs and charts together, the way `export_pdf`'s docstring
    promises, instead of pooling everything from every sheet into one list."""
    sheet_one = [dict(e, page="Overview") for e in elements]
    sheet_two = [dict(e, page="Detail") for e in elements[:2]]
    data = app.export_pdf(df, sheet_one + sheet_two, "Whole Report")
    assert data[:4] == b"%PDF"
    # Exercises the multi-sheet branch for real; a single sheet's worth of
    # content plus a second sheet's must produce more bytes than either alone.
    solo = app.export_pdf(df, sheet_one, "Solo")
    assert len(data) > len(solo)


def test_report_json_round_trip(df, elements):
    pages = [
        {"id": "p1", "name": "Overview", "elements": elements},
        {"id": "p2", "name": "Detail", "elements": elements[:2]},
    ]
    name, restored = app.dashboard_from_json(app.dashboard_to_json("My Report", pages))
    assert name == "My Report"
    assert [p["name"] for p in restored] == ["Overview", "Detail"]
    assert len(restored[0]["elements"]) == len(elements)
    assert app.build_figure(df, restored[0]["elements"][0]) is not None
    # Every element comes back with a grid position.
    assert all(
        set(e["layout"]) == {"x", "y", "w", "h"}
        for page in restored for e in page["elements"]
    )


def test_v1_dashboard_json_still_loads(df, elements):
    """Reports saved before pages existed must not break."""
    legacy = json.dumps({"dashboard": {"name": "Old", "elements": elements}}).encode()
    name, pages = app.dashboard_from_json(legacy)
    assert name == "Old"
    assert len(pages) == 1
    assert len(pages[0]["elements"]) == len(elements)
    assert app.build_figure(df, pages[0]["elements"][0]) is not None


def test_dashboard_json_rejects_rubbish():
    with pytest.raises(ValueError):
        app.dashboard_from_json(json.dumps({"dashboard": {"name": "x"}}).encode())


# ---------------------------------------------------------------------------
# LLM connectors (mocked transport)
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_calls(monkeypatch):
    calls: list[dict] = []

    def fake_post(url, headers, payload):
        calls.append({"url": url, "headers": headers, "payload": payload})
        if "groq" in url or "nvidia" in url:
            return {"choices": [{"message": {"content": BODY}}]}
        if "googleapis" in url:
            return {"candidates": [{"content": {"parts": [{"text": BODY}]}}]}
        if "/api/chat" in url:
            return {"message": {"content": BODY}}
        raise AssertionError("unexpected url " + url)

    monkeypatch.setattr(app, "_http_post", fake_post)
    return calls


@pytest.mark.parametrize(
    "provider,model,expected_url",
    [
        ("Groq", "llama-3.3-70b-versatile", "api.groq.com/openai/v1/chat/completions"),
        ("Nvidia", "meta/llama-3.3-70b-instruct", "integrate.api.nvidia.com/v1/chat/completions"),
        ("Gemini", "gemini-2.5-flash", "generativelanguage.googleapis.com"),
        ("Ollama (local)", "mistral", "11434/api/chat"),
    ],
)
def test_http_providers(captured_calls, provider, model, expected_url):
    text = app.call_llm(provider, model, "test-key", "SYSTEM", "USER", "http://localhost:11434")
    call = captured_calls[-1]
    body = json.dumps(call["payload"])
    assert expected_url in call["url"]
    assert json.loads(text) == PAYLOAD
    assert "SYSTEM" in body and "USER" in body
    # Gemini names the model in the URL path; the others put it in the body.
    assert model in body or model in call["url"]


def test_groq_sends_bearer_token_and_json_mode(captured_calls):
    app.call_llm("Groq", "m", "sk-test", "S", "U", "")
    call = captured_calls[-1]
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["payload"]["response_format"] == {"type": "json_object"}


def test_gemini_keeps_the_key_out_of_the_url(captured_calls):
    app.call_llm("Gemini", "m", "sk-test", "S", "U", "")
    call = captured_calls[-1]
    assert call["headers"]["x-goog-api-key"] == "sk-test"
    assert "sk-test" not in call["url"]


def test_connection_check_reports_success(monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: "ready")
    ok, message = app.check_connection("Groq", "llama-3.3-70b-versatile", "sk-test")
    assert ok
    assert "Groq" in message and "llama-3.3-70b-versatile" in message


def test_connection_check_reports_a_missing_key():
    ok, message = app.check_connection("Groq", "some-model", "")
    assert not ok and "API key" in message


def test_connection_check_reports_no_model_chosen():
    ok, message = app.check_connection("Groq", "Custom...", "sk-test")
    assert not ok and "model" in message.lower()


def test_connection_check_surfaces_a_provider_error(monkeypatch):
    def boom(*args, **kwargs):
        raise app.LLMError("invalid api key")

    monkeypatch.setattr(app, "call_llm", boom)
    ok, message = app.check_connection("Groq", "m", "bad-key")
    assert not ok and message == "invalid api key"


def test_connection_check_never_raises(monkeypatch):
    """Even an unexpected fault has to come back as a message, not a crash."""
    def explode(*args, **kwargs):
        raise ValueError("something odd")

    monkeypatch.setattr(app, "call_llm", explode)
    ok, message = app.check_connection("Groq", "m", "k")
    assert not ok and "something odd" in message


def test_connection_check_flags_an_empty_reply(monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: "   ")
    ok, message = app.check_connection("Groq", "m", "k")
    assert not ok and "nothing back" in message


def test_connection_check_needs_no_key_for_ollama(monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: "ready")
    ok, _ = app.check_connection("Ollama (local)", "mistral", "")
    assert ok


def test_missing_api_key_is_reported():
    with pytest.raises(app.LLMError):
        app.call_llm("Groq", "m", "", "S", "U", "")


def test_nvidia_uses_json_mode(captured_calls):
    """NIM supports response_format on the shipped defaults - use it."""
    app.call_llm("Nvidia", "meta/llama-3.3-70b-instruct", "k", "S", "U", "")
    assert captured_calls[-1]["payload"]["response_format"] == {"type": "json_object"}


def test_json_mode_is_dropped_only_when_the_model_rejects_it(monkeypatch):
    """A 4xx means retry plainly; a timeout must not burn a second full timeout."""
    attempts: list[dict] = []

    def rejects_json_mode(url, headers, payload):
        attempts.append(payload)
        if "response_format" in payload:
            raise app.ProviderRejectedParam("Provider returned HTTP 400: unsupported parameter")
        return {"choices": [{"message": {"content": BODY}}]}

    monkeypatch.setattr(app, "_http_post", rejects_json_mode)
    assert json.loads(app.call_llm("Nvidia", "m", "k", "S", "U", "")) == PAYLOAD
    assert len(attempts) == 2
    assert "response_format" not in attempts[1]

    timeouts: list[dict] = []

    def times_out(url, headers, payload):
        timeouts.append(payload)
        raise app.LLMError("The provider did not respond within 180s.")

    monkeypatch.setattr(app, "_http_post", times_out)
    with pytest.raises(app.LLMError, match="did not respond"):
        app.call_llm("Nvidia", "m", "k", "S", "U", "")
    assert len(timeouts) == 1  # not retried


def test_timeouts_and_4xx_are_distinguished(monkeypatch):
    import httpx

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("read timed out")

    monkeypatch.setattr(app.httpx, "post", timeout)
    with pytest.raises(app.LLMError, match="did not respond"):
        app._http_post("https://example.test", {}, {})

    class _Resp:
        status_code = 400
        text = "bad request"

    monkeypatch.setattr(app.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(app.ProviderRejectedParam):
        app._http_post("https://example.test", {}, {})


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


def _fake_anthropic(behaviour):
    """Build a stand-in `anthropic` module. behaviour(kwargs, is_beta) -> response."""
    recorded: list[dict] = []

    class Messages:
        def __init__(self, beta):
            self.beta = beta

        def create(self, **kwargs):
            recorded.append({"beta": self.beta, "kwargs": kwargs})
            return behaviour(kwargs, self.beta)

    class Client:
        def __init__(self, api_key=None):
            self.messages = Messages(False)
            self.beta = types.SimpleNamespace(messages=Messages(True))

    module = types.ModuleType("anthropic")
    module.Anthropic = Client
    return module, recorded


class _BadRequest(Exception):
    status_code = 400


class _AuthError(Exception):
    status_code = 401


def test_claude_uses_the_richest_request_shape(monkeypatch):
    module, recorded = _fake_anthropic(lambda kwargs, beta: _Response(BODY))
    monkeypatch.setitem(sys.modules, "anthropic", module)

    assert json.loads(app._claude_chat("k", "claude-opus-5", "S", "U")) == PAYLOAD
    assert len(recorded) == 1
    assert recorded[0]["beta"] is True
    assert recorded[0]["kwargs"]["fallbacks"] == "default"
    assert recorded[0]["kwargs"]["output_config"]["format"]["type"] == "json_schema"
    assert recorded[0]["kwargs"]["model"] == "claude-opus-5"


def test_claude_degrades_on_an_older_sdk(monkeypatch):
    def old_sdk(kwargs, beta):
        if beta or "output_config" in kwargs:
            raise TypeError("unexpected keyword argument 'output_config'")
        return _Response(BODY)

    module, recorded = _fake_anthropic(old_sdk)
    monkeypatch.setitem(sys.modules, "anthropic", module)

    assert json.loads(app._claude_chat("k", "claude-opus-5", "S", "U")) == PAYLOAD
    assert len(recorded) == 3
    assert "output_config" not in recorded[-1]["kwargs"]


def test_claude_degrades_when_the_beta_is_unavailable(monkeypatch):
    def no_beta(kwargs, beta):
        if beta:
            raise _BadRequest("unsupported beta: server-side-fallback")
        return _Response(BODY)

    module, _ = _fake_anthropic(no_beta)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    assert json.loads(app._claude_chat("k", "claude-opus-5", "S", "U")) == PAYLOAD


def test_claude_surfaces_real_errors(monkeypatch):
    def auth_failure(kwargs, beta):
        raise _AuthError("invalid x-api-key")

    module, _ = _fake_anthropic(auth_failure)
    monkeypatch.setitem(sys.modules, "anthropic", module)
    with pytest.raises(app.LLMError, match="invalid x-api-key"):
        app._claude_chat("bad", "claude-opus-5", "S", "U")


def test_claude_reports_a_refusal(monkeypatch):
    module, _ = _fake_anthropic(lambda kwargs, beta: _Response("", "refusal"))
    monkeypatch.setitem(sys.modules, "anthropic", module)
    with pytest.raises(app.LLMError, match="declined"):
        app._claude_chat("k", "claude-opus-5", "S", "U")


def test_claude_reports_a_missing_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(app.LLMError, match="pip install anthropic"):
        app._claude_chat("k", "claude-opus-5", "S", "U")


def test_suggest_dashboard_end_to_end(df, monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: "```json\n" + BODY + "\n```")
    result = app.suggest_dashboard("Groq", "m", "k", df, "what sells best?", "")
    assert len(result["charts"]) == 1
    assert app.build_figure(df, result["charts"][0]) is not None
    assert app.compute_kpi(df, result["kpis"][0]) > 0


REPORT_BODY = json.dumps({
    "pages": [
        {
            "name": "Sheet 1", "layout": "Overview",
            "kpis": [{"column": "Revenue", "agg": "sum", "title": "Total Revenue"},
                     {"column": "Profit", "agg": "sum", "title": "Total Profit"}],
            "charts": [{"type": "line", "x": "Date", "y": "Revenue", "agg": "sum",
                        "title": "Monthly revenue", "reason": "trend"}],
        },
        {
            "name": "By region", "layout": "Column",
            "kpis": [],
            "charts": [{"type": "bar", "x": "Region", "y": "Revenue", "agg": "sum",
                        "title": "Revenue by region", "reason": "mix"},
                       {"type": "bar", "x": "Category", "y": "Profit", "agg": "sum",
                        "title": "Profit by category", "reason": "mix"}],
        },
    ],
    "insights": ["Electronics leads."],
})


def test_report_prompt_builds_several_sheets(df, monkeypatch):
    """One prompt, many sheets - the 'sheet 1 ... sheet 2 ...' request."""
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: REPORT_BODY)
    result = app.suggest_report("Groq", "m", "k", df, "sheet 1 ... sheet 2 ...", "",
                                dataset="Retail sales")

    pages = result["pages"]
    assert [p["name"] for p in pages] == ["Sheet 1", "By region"]
    assert [p["layout"] for p in pages] == ["Overview", "Column"]
    assert len(pages[0]["elements"]) == 3      # 2 KPIs + 1 chart
    assert len(pages[1]["elements"]) == 2
    assert result["insights"] == ["Electronics leads."]

    for page in pages:
        for element in page["elements"]:
            assert element["dataset"] == "Retail sales"
            assert set(element["layout"]) == {"x", "y", "w", "h"}
            if element["kind"] == "chart":
                assert app.build_figure(df, element) is not None
        # Each sheet is laid out without overlaps.
        layouts = [e["layout"] for e in page["elements"]]
        for i, first in enumerate(layouts):
            for second in layouts[i + 1:]:
                assert not _overlap(first, second)


def test_report_sheets_honour_their_layout(df, monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: REPORT_BODY)
    pages = app.suggest_report("Groq", "m", "k", df, "q", "")["pages"]
    # "By region" asked for Column, so its charts run full width.
    assert all(e["layout"]["w"] == 12 for e in pages[1]["elements"])


def test_report_drops_sheets_that_reference_nothing_real(df, monkeypatch):
    body = json.dumps({
        "pages": [
            {"name": "Good", "layout": "Overview", "kpis": [],
             "charts": [{"type": "bar", "x": "Region", "y": "Revenue", "agg": "sum",
                         "title": "T", "reason": "r"}]},
            {"name": "Bad", "layout": "Overview", "kpis": [],
             "charts": [{"type": "bar", "x": "Nope", "y": "AlsoNope", "agg": "sum",
                         "title": "T", "reason": "r"}]},
        ],
        "insights": [],
    })
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: body)
    pages = app.suggest_report("Groq", "m", "k", df, "q", "")["pages"]
    assert [p["name"] for p in pages] == ["Good"]


def test_report_repairs_a_bad_layout_name(df, monkeypatch):
    body = json.dumps({"pages": [{"name": "S", "layout": "MadeUp", "kpis": [],
                                  "charts": [{"type": "bar", "x": "Region", "y": "Revenue",
                                              "agg": "sum", "title": "T", "reason": "r"}]}],
                       "insights": []})
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: body)
    assert app.suggest_report("Groq", "m", "k", df, "q", "")["pages"][0]["layout"] == "Overview"


def test_report_names_unnamed_sheets(df, monkeypatch):
    body = json.dumps({"pages": [{"name": "", "layout": "Overview", "kpis": [],
                                  "charts": [{"type": "bar", "x": "Region", "y": "Revenue",
                                              "agg": "sum", "title": "T", "reason": "r"}]}],
                       "insights": []})
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: body)
    assert app.suggest_report("Groq", "m", "k", df, "q", "")["pages"][0]["name"] == "Sheet 1"


def test_report_schema_is_sent_to_claude(monkeypatch, df):
    """Claude gets the multi-sheet schema, not the single-sheet one."""
    seen = {}

    def capture(provider, model, api_key, system, user, host, schema=None, max_tokens=2000):
        seen["schema"] = schema
        seen["system"] = system
        seen["max_tokens"] = max_tokens
        return REPORT_BODY

    monkeypatch.setattr(app, "call_llm", capture)
    app.suggest_report("Claude", "claude-opus-5", "k", df, "q", "")
    assert seen["schema"] is app.REPORT_SCHEMA
    assert "SHEETS" in seen["system"]
    # A multi-sheet report needs far more room than a single-sheet suggestion.
    assert seen["max_tokens"] >= 4000


def test_suggest_dashboard_rejects_prose(df, monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: "I cannot help with that.")
    with pytest.raises(app.LLMError):
        app.suggest_dashboard("Groq", "m", "k", df, "q", "")


# ---------------------------------------------------------------------------
# The workspace shell
#
# The UI is a JavaScript component now, so it is not driven through AppTest.
# Every intent it can raise lands in handle_workspace_action, which is ordinary
# Python and is exercised directly here; the shell's own drag, resize, selection
# and rendering are covered by browser checks. The drawers (data, export) are
# still Streamlit and are driven through AppTest at the end of this file.
# ---------------------------------------------------------------------------


class _SessionState(dict):
    """Stand-in for st.session_state, which needs a live Streamlit runtime."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self[key] = value


CONFIG = {"provider": "Groq", "model": "m", "api_key": "", "ollama_host": ""}


@pytest.fixture
def ws(monkeypatch, df):
    """A workspace with one dataset loaded, ready to receive actions."""
    monkeypatch.setattr(app.st, "session_state", _SessionState())
    app.init_state()
    app.add_dataset("Retail sales", df, "test")
    return app.st.session_state


def act(action, config=CONFIG):
    return app.handle_workspace_action(action, config)


def page_elements():
    """The visuals on the sheet currently in view."""
    return app.active_page()["elements"]


# ---- placing visuals ------------------------------------------------------


def test_clicking_a_visual_places_it(ws):
    assert act({"type": "add", "chart": "bar", "fields": ["Category", "Revenue"]})
    element = page_elements()[0]
    assert element["type"] == "bar"
    assert element["x"] == "Category" and element["y"] == "Revenue"
    assert element["agg"] == "sum"
    assert set(element["layout"]) == {"x", "y", "w", "h"}


def test_a_visual_can_be_placed_with_no_fields_chosen(ws, df):
    """Clicking a visual with nothing selected still has to produce a real chart."""
    assert act({"type": "add", "chart": "bar", "fields": []})
    element = page_elements()[0]
    assert element["x"] in df.columns
    assert app.build_figure(df, element) is not None


@pytest.mark.parametrize("chart", [c for c in app.CHART_TYPES])
def test_every_visual_in_the_gallery_can_be_placed(ws, df, chart):
    assert act({"type": "add", "chart": chart, "fields": []})
    element = page_elements()[0]
    assert app.build_figure(df, element) is not None


def test_field_order_does_not_matter(ws):
    """Picking the measure first must not put it on the axis."""
    act({"type": "add", "chart": "bar", "fields": ["Revenue", "Category"]})
    element = page_elements()[0]
    assert element["x"] == "Category" and element["y"] == "Revenue"


def test_scatter_uses_two_numeric_fields(ws):
    act({"type": "add", "chart": "scatter", "fields": ["Revenue", "Profit"]})
    element = page_elements()[0]
    assert element["x"] == "Revenue" and element["y"] == "Profit"


def test_line_defaults_to_a_date_axis(ws):
    act({"type": "add", "chart": "line", "fields": []})
    element = page_elements()[0]
    assert element["x"] == "Date"
    assert element["grain"] == "month"


def test_kpi_and_text_can_be_placed(ws, df):
    act({"type": "add", "chart": "kpi", "fields": ["Revenue"]})
    act({"type": "add", "chart": "text", "fields": []})
    kinds = [e["kind"] for e in page_elements()]
    assert kinds == ["kpi", "text"]
    assert app.compute_kpi(df, page_elements()[0]) > 0


def test_placed_visuals_never_overlap(ws):
    for chart in ("bar", "kpi", "line", "kpi", "text", "pie"):
        act({"type": "add", "chart": chart, "fields": []})
    layouts = [e["layout"] for e in page_elements()]
    for index, first in enumerate(layouts):
        for second in layouts[index + 1:]:
            assert not _overlap(first, second)


def test_unknown_fields_are_ignored(ws, df):
    act({"type": "add", "chart": "bar", "fields": ["NotAColumn", "Revenue"]})
    element = page_elements()[0]
    assert element["x"] in df.columns and element["y"] in df.columns


# ---- editing --------------------------------------------------------------


def test_format_pane_edits_apply(ws):
    act({"type": "add", "chart": "bar", "fields": ["Category", "Revenue"]})
    element_id = page_elements()[0]["id"]
    assert act({"type": "update", "id": element_id,
                "patch": {"title": "Renamed", "type": "line", "color": "#ff0000"}})
    element = page_elements()[0]
    assert element["title"] == "Renamed"
    assert element["type"] == "line"
    assert element["color"] == "#ff0000"


def test_editing_an_unknown_element_is_a_no_op(ws):
    act({"type": "add", "chart": "bar", "fields": []})
    assert act({"type": "update", "id": "ghost", "patch": {"title": "x"}}) is False


def test_resizing_from_the_format_pane(ws):
    act({"type": "add", "chart": "bar", "fields": []})
    element_id = page_elements()[0]["id"]
    assert act({"type": "resize", "id": element_id, "w": 9, "h": 14})
    assert page_elements()[0]["layout"]["w"] == 9
    assert page_elements()[0]["layout"]["h"] == 14

    # The stacked-view preset follows the width it was given.
    act({"type": "resize", "id": element_id, "w": 12, "h": 14})
    assert page_elements()[0]["size"] == "large"
    act({"type": "resize", "id": element_id, "w": 4, "h": 6})
    assert page_elements()[0]["size"] == "small"


def test_resizing_keeps_the_element_on_the_grid(ws):
    act({"type": "add", "chart": "bar", "fields": []})
    element_id = page_elements()[0]["id"]
    act({"type": "resize", "id": element_id, "w": 99, "h": 1})
    layout = page_elements()[0]["layout"]
    assert layout["w"] == 12 and layout["x"] + layout["w"] <= 12
    assert layout["h"] >= 3


def test_dragging_writes_positions_back(ws):
    act({"type": "add", "chart": "bar", "fields": []})
    element_id = page_elements()[0]["id"]
    assert act({"type": "layout",
                "layout": [{"id": element_id, "x": 4, "y": 6, "w": 5, "h": 8}]})
    assert page_elements()[0]["layout"] == {"x": 4, "y": 6, "w": 5, "h": 8}
    assert app.active_page()["layout"] == app.CUSTOM_LAYOUT


def test_delete_and_duplicate(ws):
    act({"type": "add", "chart": "bar", "fields": []})
    element_id = page_elements()[0]["id"]

    assert act({"type": "duplicate", "id": element_id})
    assert len(page_elements()) == 2
    assert page_elements()[1]["id"] != element_id
    assert page_elements()[1]["title"].endswith("(copy)")
    assert not _overlap(page_elements()[0]["layout"], page_elements()[1]["layout"])

    assert act({"type": "delete", "id": element_id})
    assert len(page_elements()) == 1
    assert act({"type": "delete", "id": "ghost"}) is False


# ---- sheets ---------------------------------------------------------------


def test_sheet_tabs_add_select_rename_delete(ws):
    first = app.active_page()["id"]
    assert act({"type": "page", "action": "add"})
    pages = ws["dm_pages"]
    assert len(pages) == 2 and ws["dm_active_page"] == pages[1]["id"]

    assert act({"type": "page", "action": "select", "id": first})
    assert ws["dm_active_page"] == first

    assert act({"type": "page", "action": "rename", "id": first, "name": "Overview"})
    assert app.active_page()["name"] == "Overview"

    assert act({"type": "page", "action": "delete", "id": first})
    assert len(ws["dm_pages"]) == 1
    assert ws["dm_active_page"] == ws["dm_pages"][0]["id"]


def test_the_last_sheet_cannot_be_deleted(ws):
    only = app.active_page()["id"]
    assert act({"type": "page", "action": "delete", "id": only}) is False
    assert len(ws["dm_pages"]) == 1


def test_visuals_land_on_the_sheet_you_are_looking_at(ws):
    act({"type": "add", "chart": "bar", "fields": []})
    act({"type": "page", "action": "add"})
    act({"type": "add", "chart": "line", "fields": []})
    assert len(ws["dm_pages"][0]["elements"]) == 1
    assert len(ws["dm_pages"][1]["elements"]) == 1
    assert ws["dm_pages"][1]["elements"][0]["type"] == "line"


def test_selecting_a_layout_rearranges_the_sheet(ws):
    for _ in range(3):
        act({"type": "add", "chart": "bar", "fields": []})
    assert act({"type": "pagelayout", "layout": "Column"})
    assert app.active_page()["layout"] == "Column"
    assert all(e["layout"]["w"] == 12 for e in page_elements())

    act({"type": "pagelayout", "layout": "Compact"})
    assert all(e["layout"]["w"] == 4 for e in page_elements())
    assert act({"type": "pagelayout", "layout": "Nonsense"}) is False


def test_renaming_the_report(ws):
    assert act({"type": "rename_report", "name": "Q3 review"})
    assert ws["dm_dash_name"] == "Q3 review"


def test_switching_dataset(ws, monkeypatch):
    app.add_dataset("Web traffic", app.sample_dataset("Web traffic"), "test")
    assert app.active_dataset() == "Web traffic"
    assert act({"type": "dataset", "name": "Retail sales"})
    assert app.active_dataset() == "Retail sales"
    assert act({"type": "dataset", "name": "nope"}) is False


def test_there_are_six_sheet_themes():
    assert len(app.SHEET_THEMES) == 6
    for spec in app.SHEET_THEMES.values():
        assert spec["layout"] in app.PAGE_LAYOUTS
        assert spec["palette"] in app.PALETTES
        assert spec["template"] in app.CHART_TEMPLATES
        assert spec["note"]


def test_application_theme_matches_the_shell():
    spec = app.SHEET_THEMES["Application"]
    assert spec["palette"] == "Application"
    assert app.PALETTES["Application"]


@pytest.mark.parametrize("theme", list(app.SHEET_THEMES))
def test_applying_a_theme_restyles_and_realigns(ws, theme):
    """A theme has to move the plots as well as recolour them."""
    for _ in range(4):
        act({"type": "add", "chart": "bar", "fields": []})
    act({"type": "layout", "layout": [                      # scatter them by hand first
        {"id": e["id"], "x": 0, "y": i * 3, "w": 3, "h": 3}
        for i, e in enumerate(page_elements())]})

    assert act({"type": "theme", "name": theme})
    spec = app.SHEET_THEMES[theme]

    assert ws["dm_palette"] == spec["palette"]
    assert ws["dm_template"] == spec["template"]
    assert ws["dm_theme_name"] == theme
    assert app.active_page()["layout"] == spec["layout"]

    layouts = [e["layout"] for e in page_elements()]
    assert any(l["w"] != 3 for l in layouts)            # re-flowed, not left as dragged
    for index, first in enumerate(layouts):
        for second in layouts[index + 1:]:
            assert not _overlap(first, second)

    palette = app.PALETTES[spec["palette"]]
    assert all(e["color"] in palette for e in page_elements())


def test_an_unknown_theme_is_ignored(ws):
    act({"type": "add", "chart": "bar", "fields": []})
    assert act({"type": "theme", "name": "Nope"}) is False


def test_the_shell_is_given_the_themes(ws):
    payload = app.workspace_payload(app.filtered_frames(), can_ask=True)
    assert [t["name"] for t in payload["themes"]] == list(app.SHEET_THEMES)
    assert all(t["note"] for t in payload["themes"])


def test_starter_prompts_use_the_real_columns(ws, df):
    """The empty sheet offers prompts phrased around this data, not placeholders."""
    prompts = app.suggested_prompts(df)
    assert 1 <= len(prompts) <= 4
    lowered = [c.lower() for c in df.columns]
    # Every prompt that names something must name a column this data actually has.
    specific = [p for p in prompts if any(c in p.lower() for c in lowered)]
    assert len(specific) >= len(prompts) - 1     # the catch-all one names nothing
    assert all(p.strip() and len(p) < 200 for p in prompts)
    assert app.suggested_prompts(None) == []
    assert app.suggested_prompts(df.head(0)) == []


def test_starter_prompts_survive_a_thin_dataset():
    thin = pd.DataFrame({"Name": ["a", "b"], "Note": ["x", "y"]})   # no numbers, no dates
    prompts = app.suggested_prompts(thin)
    assert prompts and all(isinstance(p, str) for p in prompts)


def test_prompts_reach_the_shell(ws):
    payload = app.workspace_payload(app.filtered_frames(), can_ask=True)
    assert payload["prompts"]


def test_an_unknown_action_is_ignored(ws):
    assert act({"type": "nonsense"}) is False


# ---- the Ask bar ----------------------------------------------------------


def test_ask_builds_sheets(ws, monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: REPORT_BODY)
    assert act({"type": "build", "prompt": "sheet 1 ... sheet 2 ..."})

    pages = ws["dm_pages"]
    assert [p["name"] for p in pages] == ["Sheet 1", "By region"]
    assert ws["dm_active_page"] == pages[0]["id"]
    assert ws["dm_flash"][0] == "success"
    assert ws["dm_last_insights"] == ["Electronics leads."]


def test_ask_can_add_to_the_current_sheet(ws, monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: BODY)
    assert act({"type": "addhere", "prompt": "top products"})
    assert len(ws["dm_pages"]) == 1          # no new sheets
    assert len(page_elements()) == 2              # one KPI + one chart
    assert ws["dm_flash"][0] == "success"


def test_ask_surfaces_a_provider_failure(ws, monkeypatch):
    def boom(*args, **kwargs):
        raise app.LLMError("provider exploded")

    monkeypatch.setattr(app, "call_llm", boom)
    assert act({"type": "build", "prompt": "anything"})
    assert ws["dm_flash"] == ("error", "provider exploded")
    assert page_elements() == []                  # the report is left alone


def test_ask_reports_when_nothing_matched(ws, monkeypatch):
    monkeypatch.setattr(app, "call_llm", lambda *a, **k: '{"pages": [], "insights": []}')
    assert act({"type": "build", "prompt": "x"})
    assert ws["dm_flash"][0] == "error"
    assert len(ws["dm_pages"]) == 1


def test_ask_respects_filters(ws, monkeypatch):
    """The model must profile the filtered data, not the whole table."""
    seen = {}

    def capture(provider, model, api_key, system, user, host, schema=None, max_tokens=2000):
        seen["user"] = user
        return REPORT_BODY

    ws[app.filter_columns_key("Retail sales")] = ["Category"]
    ws[app.filter_key("Retail sales", "Category", "cat")] = ["Electronics"]
    monkeypatch.setattr(app, "call_llm", capture)
    act({"type": "build", "prompt": "x"})
    assert "Rows: 600" not in seen["user"]


# ---- what the shell is handed ---------------------------------------------


def test_workspace_payload_shape(ws):
    act({"type": "add", "chart": "bar", "fields": ["Category", "Revenue"]})
    act({"type": "add", "chart": "kpi", "fields": ["Revenue"]})
    payload = app.workspace_payload(app.filtered_frames(), can_ask=True)

    assert [d["name"] for d in payload["datasets"]] == ["Retail sales"]
    assert payload["datasets"][0]["rows"] == 600
    assert payload["activeDataset"] == "Retail sales"
    assert payload["pages"][0]["id"] == app.active_page()["id"]
    assert payload["layouts"] == app.LAYOUT_CHOICES
    assert payload["columns"] == app.CANVAS_COLUMNS
    assert payload["canAsk"] is True

    kinds = {item["kind"] for item in payload["items"]}
    assert kinds == {"chart", "kpi"}
    chart = [i for i in payload["items"] if i["kind"] == "chart"][0]
    assert chart["figure"]["data"]
    assert chart["x"] == "Category" and chart["agg"] == "sum"
    assert "sum" in chart["aggOptions"]
    kpi = [i for i in payload["items"] if i["kind"] == "kpi"][0]
    assert kpi["value"] and kpi["column"] == "Revenue"


def test_payload_lists_fields_with_types(ws):
    payload = app.workspace_payload(app.filtered_frames(), can_ask=False)
    by_name = {f["name"]: f["kind"] for f in payload["fields"]}
    assert by_name["Revenue"] == "number"
    assert by_name["Date"] == "date"
    assert by_name["Category"] == "text"
    assert payload["canAsk"] is False


def test_payload_offers_only_sensible_aggregations(ws):
    """A text measure must not offer sum in the format pane."""
    act({"type": "add", "chart": "bar", "fields": ["Region"]})
    element = page_elements()[0]
    element["y"] = "Category"
    payload = app.workspace_payload(app.filtered_frames(), can_ask=True)
    assert payload["items"][0]["aggOptions"] == ["count", "nunique"]


def test_payload_is_json_serialisable(ws):
    for chart in ("bar", "line", "kpi", "text"):
        act({"type": "add", "chart": chart, "fields": []})
    payload = app.workspace_payload(app.filtered_frames(), can_ask=True)
    assert json.loads(json.dumps(payload, default=str))


def test_workspace_component_files_exist():
    app._workspace_component.clear()
    app._workspace_component()
    for name in ("index.html", "workspace.js"):
        assert os.path.isfile(os.path.join(app.WORKSPACE_DIR, name))
    bundle = os.path.join(app.WORKSPACE_DIR, "plotly.min.js")
    assert os.path.getsize(bundle) > 100_000


# ---------------------------------------------------------------------------
# The drawers, which are still Streamlit
# ---------------------------------------------------------------------------


def _by_key(elements_, key):
    for element in elements_:
        if getattr(element, "key", None) == key:
            return element
    raise KeyError("{} not found; have {}".format(
        key, [getattr(e, "key", None) for e in elements_]))


def _labelled(buttons, label):
    return [b for b in buttons if b.label == label]


@pytest.fixture
def booted():
    """The real app, with the sample data loaded, as a first-time user sees it."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    _by_key(at.button, "quick_Retail sales").click()
    at.run()
    assert not at.exception
    return at


def test_first_run_shows_the_three_steps():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Add data" in text and "Ask" in text and "Arrange" in text
    assert any(b.key == "quick_Retail sales" for b in at.button)


def test_the_intro_screen_stays_uncluttered():
    """The upload panel belongs in the workspace, not on the first screen."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    assert not at.exception
    assert not any(u.key == "dm_uploader" for u in at.file_uploader)
    inputs = {t.key for t in at.text_input} | {t.key for t in at.text_area}
    assert not {"dm_api_url", "dm_gsheet_url", "dm_conn", "dm_query"} & inputs
    # Only the samples and one way through to the full data panel.
    assert any(b.key == "quick_Retail sales" for b in at.button)
    assert any(b.key == "dm_bring_own" for b in at.button)


def test_bringing_your_own_data_opens_the_workspace_drawer():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    _by_key(at.button, "dm_bring_own").click()
    at.run()
    assert not at.exception

    # In the workspace with no data yet, the left menu carries every source.
    assert at.session_state["dm_entered"] is True
    assert at.session_state["dm_datasets"] == {}
    assert any(u.key == "dm_uploader" for u in at.file_uploader)
    inputs = {t.key for t in at.text_input} | {t.key for t in at.text_area}
    assert {"dm_api_url", "dm_gsheet_url", "dm_conn", "dm_query"} <= inputs


def test_loading_data_from_the_drawer_with_none_loaded():
    """The empty workspace must still be able to take its first dataset."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    _by_key(at.button, "dm_bring_own").click()
    at.run()
    _by_key(at.button, "dm_add_sample").click()
    at.run()
    assert not at.exception
    assert list(at.session_state["dm_datasets"]) == ["Retail sales"]


def test_menu_is_safe_before_any_data_is_loaded():
    """The empty workspace must still render its menu, minus the data-only parts."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH, default_timeout=120)
    at.run()
    _by_key(at.button, "dm_bring_own").click()
    at.run()
    assert not at.exception
    # Data sources and the model settings are there; export is not offered yet.
    assert any(u.key == "dm_uploader" for u in at.file_uploader)
    assert any(s.key == "dm_provider" for s in at.selectbox)
    assert not any(s.key == "dm_export_format" for s in at.selectbox)


def test_loading_data_puts_you_in_the_workspace(booted):
    at = booted
    assert list(at.session_state["dm_datasets"]) == ["Retail sales"]
    # The shell replaces the setup screen, so the sample buttons are gone.
    assert not any(b.key == "quick_Retail sales" for b in at.button)


def test_menu_offers_every_source(booted):
    at = booted
    assert not at.exception
    assert any(u.key == "dm_uploader" for u in at.file_uploader)
    assert any(s.key == "dm_sample_name" for s in at.selectbox)
    inputs = {t.key for t in at.text_input} | {t.key for t in at.text_area}
    assert {"dm_api_url", "dm_gsheet_url", "dm_conn", "dm_query"} <= inputs
    # Model and appearance settings live here too.
    assert any(s.key == "dm_provider" for s in at.selectbox)
    assert any(s.key == "dm_palette" for s in at.selectbox)


def test_loading_more_datasets_from_the_drawer(booted):
    at = booted
    _by_key(at.selectbox, "dm_sample_name").set_value("Web traffic")
    at.run()
    _by_key(at.button, "dm_add_sample").click()
    at.run()
    assert not at.exception
    assert set(at.session_state["dm_datasets"]) == {"Retail sales", "Web traffic"}
    assert at.session_state["dm_active_dataset"] == "Web traffic"


def test_there_is_no_dataset_limit(booted):
    at = booted
    for index in range(app.DATASET_ADVISORY + 2):
        at.session_state["dm_datasets"]["extra %d" % index] = {
            "df": app.sample_dataset("Retail sales"), "source": "test"
        }
    at.run()
    assert not at.exception
    assert len(at.session_state["dm_datasets"]) > app.DATASET_ADVISORY
    assert any("sit in memory" in c.value for c in at.caption)


def test_removing_a_dataset_returns_to_setup(booted):
    at = booted
    at.session_state["dm_datasets"] = {}
    at.run()
    assert not at.exception
    assert any(b.key == "quick_Retail sales" for b in at.button)


def test_filters_live_in_the_menu(booted):
    at = booted
    _by_key(at.multiselect, app.filter_columns_key("Retail sales")).set_value(["Category"])
    at.run()
    _by_key(at.multiselect,
            app.filter_key("Retail sales", "Category", "cat")).set_value(["Electronics"])
    at.run()
    assert not at.exception
    caption = [c.value for c in at.caption if c.value.startswith("Showing")]
    assert caption and not caption[0].startswith("Showing 600")


@pytest.mark.parametrize(
    "fmt",
    [
        "Excel workbook (.xlsx)",
        "CSV - raw data",
        "CSV - data + summary + chart data",
        "Interactive HTML dashboard",
        "Report layout (JSON)",
        "PDF report",
    ],
)
def test_exports_from_the_menu(booted, fmt):
    at = booted
    _by_key(at.selectbox, "dm_export_format").set_value(fmt)
    at.run()
    _labelled(at.button, "Generate")[0].click()
    at.run()
    assert not [e.value for e in at.error]
    # "Export current theme" is its own always-present download button, separate
    # from the report export this test is driving.
    report_downloads = [d for d in at.download_button if d.key != "dm_theme_export_btn"]
    assert len(report_downloads) == 1
    assert any(c.value.startswith("Ready:") for c in at.caption)


def test_excel_options_appear_only_for_excel(booted):
    at = booted
    _by_key(at.selectbox, "dm_export_format").set_value("CSV - raw data")
    at.run()
    assert not any(m.key == "dm_excel_sheets" for m in at.multiselect)

    _by_key(at.selectbox, "dm_export_format").set_value("Excel workbook (.xlsx)")
    at.run()
    assert _by_key(at.multiselect, "dm_excel_sheets").value == app.DEFAULT_EXCEL_SHEETS
    assert _by_key(at.selectbox, "dm_excel_theme").value == "Corporate blue"


def test_pivot_controls_appear_when_that_sheet_is_selected(booted):
    at = booted
    _by_key(at.selectbox, "dm_export_format").set_value("Excel workbook (.xlsx)")
    at.run()
    assert not any(s.key == "dm_pivot_index" for s in at.selectbox)

    _by_key(at.multiselect, "dm_excel_sheets").set_value(["Raw data", "Pivot table"])
    at.run()
    assert not at.exception
    assert _by_key(at.selectbox, "dm_pivot_index").value in list(
        app.sample_dataset("Retail sales").columns
    )


def test_appearance_settings_change_the_palette(booted):
    at = booted
    _by_key(at.selectbox, "dm_palette").set_value("Ocean")
    at.run()
    assert not at.exception
    assert at.session_state["dm_palette"] == "Ocean"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-header", "-x"]))
