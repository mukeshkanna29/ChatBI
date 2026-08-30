# ChatBI — Complete Project Reference

Everything about this project in one place: what it is, how it's built, every tool
and number that went into it, and a rehearsed answer to the questions people
actually ask in a demo. Read top to bottom once before presenting; use the FAQ
section as a quick lookup during Q&A.

---

## 1. The 30-second pitch

> ChatBI is an open-source BI tool that opens like a real desktop application —
> app bar, data panel, chart gallery, canvas, format panel, sheet tabs — not a
> web form. You load data, either build charts by clicking, or describe a whole
> report in plain English and watch it get built: multiple sheets, each with a
> real topic, 4–5 charts, KPI tiles, styled and arranged automatically. Drag
> anything to move it, resize it, export to PDF, Excel, CSV or a single
> self-contained HTML file. It runs on one machine, works with five different
> AI providers (or none at all), and there's no per-seat licence.

## 2. Why it exists

| Pain point | Typical tool | ChatBI |
|---|---|---|
| Cost | Power BI / Tableau — per-seat licensing | Free, open source, self-hosted |
| Technical barrier | Metabase-style tools need SQL | Ask in plain English, or point-and-click |
| Locked to one AI vendor | OpenAI-only tools | Groq, Gemini, Nvidia, Claude, or fully local Ollama |
| Setup complexity | Multi-service stacks, a database to run | One Python file, one `pip install` |
| Data silos | File upload only | Files, Google Sheets, any HTTP API, SQL databases |
| Privacy | Cloud-only | Runs on your machine; Ollama keeps even the AI part local |

**Origin.** This started as a single-file Streamlit dashboard app, following a
short written spec (`DATAMIND_PROJECT.md`) for "Auto mode" (AI suggests charts)
and "Manual mode" (you build them). It went through several full redesigns in
the same session: a Streamlit-only UI → a drag/resize canvas as a custom
component → a complete rewrite of the interface as a hand-built JavaScript
application shell, because Streamlit's stock widgets read as a form, not
software. What's described below is the current, final state.

---

## 3. Tech stack — every tool and why

| Layer | Tool | Version | Why this one |
|---|---|---|---|
| Language | Python | 3.12 | Project standard; matches every library below |
| App framework | **Streamlit** | `>=1.49,<2` | Handles the web server, session state and file upload for free — pinned above 1.49 because that's when `width="stretch"` replaced the deprecated `use_container_width` |
| UI shell | **Vanilla JavaScript** (no framework) | — | Streamlit's own widgets can't do drag/resize/free layout; a hand-written component avoids an npm build entirely — see §5 |
| Charts | **Plotly.js** (via `plotly` Python package) | `>=5.20,<7` | Interactive by default (zoom, pan, hover) and the same figure JSON works in Python (server) and JavaScript (browser) without a second charting library |
| Data | **pandas** | `>=2.1` | Universal — every loader, filter and export runs through a DataFrame |
| Numeric | **NumPy** | `>=1.26` | Backs pandas; used directly in the demo-data generators |
| HTTP | **httpx** | `>=0.27` | Every non-Claude provider is called over plain HTTP with this — no per-provider SDK to install |
| AI SDK | **anthropic** | `>=1.0` | Official SDK, used only for Claude — every other provider talks HTTP directly |
| Excel export | **openpyxl** | `>=3.1` | Multi-sheet `.xlsx` with real formatting (fills, borders, frozen panes) |
| PDF export | **reportlab** | `>=4.0` | Report-style, page-by-page PDF layout |
| Chart → image | **kaleido** | `==0.2.1` | Converts Plotly figures to PNG for the PDF; pinned to the version that bundles its own Chromium, so no separate browser install is needed |
| Secrets | **python-dotenv** | `>=1.0` | Reads a local `.env` file for API keys, so they're never hardcoded |
| Testing | **pytest** | — | 229 tests — see §9 |

**Nothing else is required.** No database, no Docker (though a `Dockerfile` is
provided), no Node.js, no build step of any kind — the JavaScript is used
exactly as written, straight from disk.

---

## 4. Repository layout

```
BI/
├── app.py                        3,831 lines — the entire backend
├── components/workspace/
│   ├── index.html                   346 lines — the shell's HTML/CSS
│   ├── workspace.js                  637 lines — the shell's behaviour
│   └── plotly.min.js               (vendored automatically on first run)
├── tests/test_datamind.py         1,707 lines — 229 tests
├── requirements.txt
├── make_sample_files.py           five small practice datasets
├── make_demo_dataset.py           one richer dataset with planted signals
├── README.md                      user-facing feature documentation
├── RUN_IN_VSCODE.md               VS Code setup steps
├── PROJECT_REFERENCE.md           this file
├── Dockerfile, render.yaml        optional deployment
└── .env.example, .streamlit/      config templates
```

Two files carry the entire product: **`app.py`** (backend — data, AI, exports,
state) and **`components/workspace/`** (frontend — everything you see and
click). There is no database and no build artifact folder.

---

## 5. Architecture — how the two halves talk

This is the one idea worth understanding cold, because it explains almost
every "why is it built that way" question.

```
┌─────────────────────────────────────────────────────────────┐
│  BROWSER                                                     │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  components/workspace/  (plain HTML + JS, one iframe)│    │
│  │  Draws the app bar, panes, canvas, tabs.             │    │
│  │  Owns dragging, resizing, selection — all of it      │    │
│  │  happens locally in the browser, no round trip.      │    │
│  └───────────────────┬───────────────────────────────────┘   │
│                       │ one JSON "intent" per user action    │
│                       │ e.g. {"type":"add","chart":"bar",…}  │
│                       ▼                                       │
├─────────────────────────────────────────────────────────────┤
│  PYTHON  (app.py, running on the server / your machine)      │
│  handle_workspace_action(intent) → mutates st.session_state  │
│  → Streamlit reruns app.py top to bottom                     │
│  → workspace_payload() rebuilds the whole UI state as JSON   │
│  → sent back down to the iframe, which redraws               │
└─────────────────────────────────────────────────────────────┘
```

- **Streamlit component protocol.** `components/workspace/` is declared via
  `streamlit.components.v1.declare_component(...)` and mounted in an iframe.
  It's not a Streamlit widget — it's a self-contained web page that happens to
  be embedded inside one, communicating by `postMessage`.
- **One function is the entire API surface.** Every click, drag, resize, sheet
  switch, theme pick and AI prompt becomes a small JSON object
  (`{"type": "...", ...}`) sent to `handle_workspace_action()` in `app.py`.
  That function is ~14 `if kind == "...":` branches — `add`, `update`,
  `resize`, `delete`, `duplicate`, `layout`, `dataset`, `page`, `pagelayout`,
  `theme`, `rename_report`, `build`, `addhere`. This is *why* the whole app is
  unit-testable without a browser: every one of those branches is called
  directly in tests.
- **Dragging and resizing never leave the browser mid-gesture.** The JS moves
  the tile in real time using plain mouse events; only the *final* position is
  sent to Python, on mouse-up. That's why moving things feels instant despite
  the round-trip architecture everywhere else.
- **Python owns all state.** `st.session_state` holds every dataset, every
  page, every chart config. The JS side has no persistent memory of its own —
  on every rerun it receives the complete current state and redraws from
  scratch. This is deliberate: it means the JS can never drift out of sync
  with what Python thinks is true.
- **The left menu is normal Streamlit**, not part of the custom component —
  data sources, the AI key, filters, appearance, export. Only the
  canvas/gallery/format-pane experience needed to escape Streamlit's default
  widgets; forms and settings didn't.

---

## 6. Data pipeline

```
file / URL / Sheets / SQL  →  load_*()  →  coerce_types()  →  DataFrame in
session_state  →  apply_saved_filters()  →  chart engine  →  Plotly figure
```

- **Five sources**, all live in the left menu at once (no mode switch):
  file upload (CSV, TSV, Excel, JSON, Parquet — up to 500 MB each, several
  files at once), Google Sheets (public share link), any HTTP URL/API
  returning CSV/TSV/JSON/Parquet, a SQL database (any SQLAlchemy connection
  string), and three built-in sample datasets.
- **No cap on how many datasets can be loaded** — past 8 (`DATASET_ADVISORY`)
  it just notes they're all sitting in memory at once.
- **`coerce_types()`** is the cleaning step every loader runs through: text
  that looks like money (`"$1,200.50"`) becomes a float, text that looks like
  a date becomes a real datetime — conservatively, so an ID column like a
  postcode (`"02134"`) is never mistaken for a number.
- **Filters are per-dataset, not per-sheet.** Narrowing one dataset affects
  every chart built from it across every sheet, including ones you're not
  looking at; other loaded datasets are untouched.

---

## 7. The AI layer

### 7.1 Providers

| Provider | Needs a key | Default model | Note |
|---|---|---|---|
| **Groq** | Yes | `llama-3.3-70b-versatile` | Fastest free tier |
| **Gemini** | Yes | `gemini-2.5-flash` | Generous free tier |
| **Nvidia** (NIM) | Yes | `openai/gpt-oss-120b` | Wide model catalogue |
| **Claude** | Yes | `claude-opus-5` | Highest suggestion quality; only provider using an official SDK (`anthropic`) rather than raw HTTP |
| **Ollama** | No | `mistral` | Runs on your machine — nothing leaves it |

The model dropdown is **fetched live** from the provider whenever a key is
present (`fetch_models()`), because providers retire models without warning —
this was hit for real during development (a configured Nvidia default model
was retired mid-project) and is why the list refreshes instead of staying
hardcoded.

**Check connection** (left menu, under AI model): sends the smallest possible
request — "reply with the word ready" — and reports success (with the model
name and response time) or exactly what's wrong, before you rely on it for a
real report.

### 7.2 Two ways to use it

- **`suggest_dashboard()`** — "Add here": suggests 4–5 charts and 2–4 KPIs for
  the *current* sheet only and adds all of them straight to the canvas.
  Existing sheets are untouched; there's no per-suggestion preview/accept
  step — undo an unwanted visual by deleting it, or re-run the prompt.
- **`suggest_report()`** — "Build": designs the *entire* report from one
  prompt — multiple sheets, each with a name, a layout, KPIs and charts.
  Replaces whatever's currently on the report. "Recommend for me" calls this
  with an empty prompt, which is what makes `suggest_report()` fall back to
  designing the sheet structure itself instead of following instructions.

### 7.3 The prompt and its persona

Both prompts open with the same instruction: *"You are a senior Business
Intelligence developer and data analyst with more than ten years of
experience."* The report prompt specifically requires:

- A real **topic** per sheet name ("Regional Performance"), never a
  placeholder like "Sheet 2".
- **Exactly 4 to 5 charts** per sheet, each looking at the topic from a
  genuinely different angle — not the same two columns cut five ways.
- **Title Case headings** on every chart and KPI.
- If the user names sheets explicitly ("Sheet 1: ... Sheet 2: ..."), those
  exact sheets are built with that exact content — the model follows
  instructions literally when given them.

### 7.4 Why the output can be trusted enough to render automatically

Nothing the model returns is drawn until `validate_suggestions()` /
`validate_report()` check it against the real DataFrame:

- **Invented columns are dropped**, not rendered as a broken chart.
- **Near-miss names are matched** — `"revenue"` resolves to the real column
  `Revenue`.
- **Impossible aggregations are repaired** — summing a text column silently
  becomes a count instead.
- **Duplicate charts are removed.**
- **Truncated JSON is repaired.** Smaller/free models regularly return an
  answer that gets cut off mid-object; `extract_json()` closes the dangling
  structure rather than discarding the whole response — verified live against
  an actual truncated response from a real model during development.
- **`clean_heading()`** normalises sloppy titles the model still returns
  (`ALL CAPS`, `snake_case`) into Title Case; a blank title falls back to a
  generated one (`"Sum of Revenue by Region"`) rather than shipping empty.

**What isn't validated:** the prose insights/commentary. Chart configuration
is checked against real columns; the sentences describing what a chart shows
are not fact-checked against the data. Be upfront about this if asked — see
the FAQ.

### 7.5 What data actually leaves your machine

Only a **profile** of the dataset — column names, types, min/max, a few
sample rows — plus your prompt. Not the full table. Choosing Ollama means even
that profile never leaves the machine.

---

## 8. The canvas & visual system

- **A 12-column grid.** Every chart, KPI card and text block has `{x, y, w, h}`
  in grid units. `next_free_slot()` places new items in the first gap that
  doesn't overlap anything already there — tested explicitly for mixed
  chart/KPI sizes so a small KPI card never lands under a wide chart.
- **Twelve visuals in the gallery**: Bar, Line, Area, Pie, Donut, Scatter,
  Histogram, Box, Heatmap, Treemap, KPI card, Text block.
- **Click-to-build**: select up to two fields (typed `#` number, `D` date,
  `A` text), click a visual — it's placed with sensible defaults chosen for
  you (a date axis for a line chart, two numerics for a scatter, etc. — see
  `_new_chart_from_fields()`). Click a visual with nothing selected and it
  still picks reasonable fields itself.
- **The Format pane** (right side) edits whatever's selected: title, type, X,
  Y, aggregation (`sum / mean / median / count / min / max / nunique` — only
  the ones that make sense for the column are offered), colour, split-by,
  sort, Top N, exact width/height.
- **Five automatic page layouts** (a sheet keeps re-arranging itself under a
  layout until you drag something by hand, which switches it to Custom):

  | Layout | Arrangement |
  |---|---|
  | Overview | KPI banner across the top, charts two per row |
  | Spotlight | One full-width hero chart, the rest small beneath |
  | Sidebar | Tall main chart left, everything else stacked right |
  | Column | One full-width element per row |
  | Compact | Dense tiling — three charts to a row |

- **Six sheet themes** (`SHEET_THEMES`), each bundling a layout + palette +
  Plotly template + heatmap scale in one click: **Executive** (Overview /
  Default palette), **Focus** (Spotlight / Ocean), **Analyst** (Compact /
  Colourblind-safe), **Story** (Column / Sunset), **Midnight** (Sidebar /
  Vibrant, dark plots), **Application** (Overview / the app's own dark-shell
  colours, `plotly_dark`) — a report themed with it looks like an extension
  of ChatBI itself rather than a chart pasted into it.
- **Nine built-in chart palettes** (Default, Vibrant, Ocean, Sunset, Forest,
  Berry, Colourblind safe, Monochrome blue, Application) and **seven Plotly
  templates** (including `plotly_dark`) selectable independently of a theme,
  under Appearance in the left menu.
- **Theme import/export** (`parse_theme_file`, `all_palettes`, under
  Appearance → "Import or export a theme"): upload a `.json` file and it adds
  a new palette for this session — accepts a real Power BI theme export (its
  `dataColors` list) or ChatBI's own `{"colors": [...]}` format
  interchangeably. Hex entries are validated and non-colour values dropped;
  fewer than two valid colours raises `ThemeImportError` with a specific
  message rather than silently doing nothing. A name collision with an
  existing palette gets `" (2)"`, `" (3)"`, etc. appended
  (`unique_palette_name`) instead of overwriting it. Imported palettes live
  only in `st.session_state.dm_custom_palettes` — they don't survive a page
  refresh, and there's a matching "Export current theme" download button to
  save the active palette back out in the same JSON shape. `chart_theme()`
  resolves palette names through `all_palettes()` (built-ins plus imports),
  so an imported palette works everywhere a built-in one does.

---

## 9. Testing

**229 tests**, `pytest tests/ -v`, three layers:

1. **Pure logic** — type coercion, JSON extraction/repair (including the real
   truncated-response case above), the chart engine across every type,
   palette and template, every export format, heading cleanup, the five
   layouts' placement math.
2. **Connectors** — request shape, response parsing, JSON-mode negotiation,
   timeout-vs-4xx handling, Claude's SDK fallback chain — all against mocked
   HTTP, no live network calls in the test suite.
3. **The workspace** — every one of the ~14 intents in
   `handle_workspace_action()` is called directly and its effect on
   `st.session_state` asserted; the left-menu widgets (data sources, filters,
   every export format) are driven through Streamlit's `AppTest` harness.

The canvas's own dragging/resizing/selection is JavaScript and isn't covered
by `pytest` (`AppTest` can't execute component JS) — that was verified
manually in a real browser during development: drag a tile, force a full
server round-trip, confirm the position survives.

Zero `pyflakes` warnings across `app.py` and the test file.

---

## 10. Exports

| Format | Contents |
|---|---|
| **PDF** | Title page, then one section per sheet - its own heading, its own KPI table (only that sheet's KPIs), then its own charts one per page (`group_elements_by_sheet`) |
| **Excel `.xlsx`** | Configurable: Raw data, Summary statistics, KPIs, one sheet per chart's aggregated numbers, an inline-configurable Pivot table with row/column totals, and a Dashboard config sheet — six formatting themes (Corporate blue, Slate, Forest, Plum, Amber, Minimal) |
| **CSV — raw** | Just the filtered data |
| **CSV — sectioned** | Raw data + summary statistics + each chart's own aggregated numbers, one file |
| **Interactive HTML** | A single self-contained file — fully interactive (Plotly is inlined), opens offline, no server needed |
| **Report JSON** | The whole report — every sheet, every chart, every position — reloadable to restore it exactly |

Every export can be scoped to **this sheet** or the **whole report**. At "whole report"
scope, PDF keeps each sheet's KPIs and charts grouped under that sheet's own heading rather
than pooling everything from every sheet into one undifferentiated list - the Interactive
HTML export does not do this yet, it lays every sheet's cards into one flat grid.

---

## 11. Known limits (say these before someone else finds them)

- **Elements can overlap if dragged onto each other by hand** — automatic
  placement never overlaps; manual dragging doesn't stop you from doing it
  yourself.
- **Insights/commentary text isn't fact-checked** against the data (§7.4) —
  chart configuration is validated, prose isn't.
- **No multi-user / auth layer.** It's a single-machine tool, one browser
  session at a time. There's no login, no sharing link, no concurrent-editor
  support.
- **Every interaction round-trips to Python** except dragging/resizing, so the
  app responds in a noticeable beat rather than instantly on things like
  adding a chart or switching sheets — a deliberate trade-off against writing
  a full second copy of the state logic in JavaScript.
- **JavaScript changes need care.** The whole shell is hand-written with no
  build step or bundler to catch syntax errors before they ship — a real bug
  during development (a stray literal newline inside a JS string) crashed the
  entire component silently until the browser console was checked.
- **Free-tier AI providers can be slow or queue requests** under load — the
  app waits up to 180 seconds before giving a clear timeout message rather
  than hanging indefinitely.

---

## 12. FAQ — rehearsed answers for the room

**"How is this different from just using ChatGPT to make a chart?"**
ChatGPT can describe a chart. This *is* the dashboard — an actual interactive
Plotly chart on a real canvas you can drag, resize, restyle and export,
generated from your real data with columns validated against what you
actually uploaded, not invented.

**"What happens if the AI hallucinates a column that doesn't exist?"**
It's dropped before anything renders. Every model response is checked against
the live DataFrame first — see §7.4. This was a deliberate design decision
from the start, not an afterthought.

**"Is my data sent to OpenAI / some cloud company?"**
Only if you pick a cloud provider, and even then only a profile (column
names/types/ranges/a few sample rows) plus your prompt — never the full
table. Pick Ollama and nothing leaves the machine at all, including that
profile.

**"What if the AI service is down or my key doesn't work?"**
Check connection (left menu) verifies this before you rely on it. If a
provider fails mid-report, the failure is caught and shown as a message — the
existing report is left untouched, nothing crashes.

**"Can I trust the written insights it gives me?"**
Be honest here: chart *configuration* is validated against real columns;
the *prose* commentary is not independently fact-checked. Treat insights as a
first draft to verify, not a final answer.

**"Why build the UI in raw JavaScript instead of React/Vue?"**
No build step, no npm dependency tree, no version drift — the whole frontend
is two files that run exactly as written. Streamlit's component protocol only
needs plain HTML/JS/postMessage; a framework would add complexity without
adding a capability this app needs.

**"Does it scale to huge datasets?"**
It's an in-memory pandas tool — comfortable up to the low millions of rows on
a normal laptop; very large data should be pre-aggregated or limited (e.g.
`LIMIT` in a SQL source) before loading.

**"Can multiple people use it at once?"**
Not today — it's single-user, single-machine, no auth. That's a known,
explicit limitation (§11), not a bug.

**"How do I know it's actually working and not just frozen?"**
Build, Add here and Recommend for me all put the canvas behind a dimmed
overlay with a spinner and a status line the moment you click, and disable
the buttons so a second click can't queue a duplicate request. It clears the
instant Python finishes and sends the next render back down — including the
edge case where "Add here" comes back with nothing new to add. A free-tier
model can take up to a couple of minutes, which is exactly why this exists.

**"Can I bring in my own colours, like a Power BI theme?"**
Yes — Appearance → "Import or export a theme" takes a `.json` file and reads
either a real Power BI theme export (its `dataColors` list) or ChatBI's own
`{"colors": [...]}` format. It becomes a new palette for the session, usable
anywhere the built-in ones are; export the current palette back out the same
way to reuse it next time.

**"Do I have to decide how many sheets my report needs?"**
No — leave the prompt box empty and click **Recommend for me** (or just
click Build with nothing typed) and ChatBI designs the structure itself:
typically an executive summary plus one sheet per dimension the data actually
supports. Type your own sheet-by-sheet brief instead and it follows that.

**"What does it cost to run?"**
The app itself: free, self-hosted, no licence. The AI part: free-tier
providers (Groq, Gemini, Nvidia) cover a real demo; Ollama is free and fully
local; Claude is the paid option for the highest-quality suggestions.

**"How long did this take to build, and is it 'finished'?"**
It's a working, tested product (229 automated tests, zero lint warnings) built
iteratively in one continuous session, including two full UI rewrites along
the way based on hands-on feedback. It's demo-ready, not enterprise-hardened —
§11 lists the honest gaps.

**"What's the single most important design decision?"**
The Streamlit-component boundary in §5: Python owns every bit of state, the
browser only ever displays it and reports back what the user just did. That
one rule is why 229 tests can exercise the *entire* interactive app without
ever opening a browser.
