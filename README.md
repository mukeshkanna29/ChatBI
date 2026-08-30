# ChatBI

**Lightweight agentic BI.** Point it at data, ask a question in plain English, and get a
dashboard you can then edit by hand — titles, chart types, colours, sizes, filters — and
export to PDF, Excel, CSV or a standalone interactive HTML file.

Works with any LLM (Groq, Gemini, Nvidia, Claude, or a local Ollama model), reads from five
kinds of data source, loads as many of them as you like, and spreads a report across as many
sheets as you need — with charts you drag and resize on a real canvas.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open <http://localhost:8501> and click **Retail sales** to try it with no API key at all.

---

## Why this exists

| Pain point | Typical tool | ChatBI |
|---|---|---|
| Cost | Power BI / Tableau per-seat licensing | Free, open source, self-hosted |
| Technical barrier | Metabase needs SQL | Ask in English, or use the dropdown builder |
| Single vendor LLM | OpenAI only | Groq, Gemini, Nvidia, Claude, Ollama |
| Setup complexity | Multi-service, database required | One file, one `pip install` |
| Data silos | File upload only | Files, Google Sheets, APIs, SQL databases |
| Privacy | Cloud only | Runs locally; Ollama keeps data on your machine |

---

## Features

### The workspace

ChatBI opens as an application, not a form: a dark app bar with the report name and the
Ask box, a **Data** pane listing datasets and their fields (typed `#` number, `D` date,
`A` text), a **Visuals** gallery of twelve chart types, the canvas in the middle, a
**Format** pane on the right for whatever is selected, and sheet tabs along the bottom.

The whole surface is a self-contained JavaScript component
(`components/workspace/`) — no npm build, no CDN, Plotly's browser bundle vendored from the
installed package on first run. Streamlit sits behind it as the data and AI layer; every
click in the shell becomes one intent that Python applies (`handle_workspace_action`), so the
logic stays where it is tested. A left menu (☰, top left) carries everything a browser cannot
do alone: data sources, the AI provider and key with a **Check connection** button, sheet
themes, chart appearance, filters and export.

**Point and click** — select a field or two in the Data pane, click a visual from the twelve
in the gallery, and it lands on the first free spot on the canvas. Pick nothing and it chooses
sensible fields itself: a date axis for a line, a category for a bar, two numerics for a
scatter. Click any placed visual to fill the **Format** pane on the right — title, axes,
aggregation, colour, exact width and height — no separate "edit mode" to turn on first.

**Ask for the whole report** — the other way in is one box, and it acts like a senior BI
developer sketching out a deck: it designs the sheets, gives each one a real topic instead of
"Sheet 2", and fills each with 4 to 5 charts that look at that topic from different angles,
under clean Title Case headings. Describe the sheets yourself and it builds exactly those:

> *Sheet 1: total revenue and profit KPIs with a monthly revenue trend. Sheet 2: revenue by
> region and by category. Sheet 3: top products.*

Say nothing about sheets and it designs three to five on its own — an executive summary, then
one sheet per dimension this dataset actually supports (region, product, channel, time,
quality...). **Recommend for me** does exactly this, ignoring whatever is typed in the box, for
when you just want ChatBI to decide the structure. **Add to this sheet** does the same
generation for the sheet you are on instead, adding every KPI and chart it comes back with
straight to the canvas (there's no per-suggestion preview step — re-run it, or delete a visual
you don't want, if the result isn't right). Whatever titles or sheet names come back are
cleaned up automatically — sloppy `snake_case` or ALL CAPS becomes `Title Case`; a blank title
falls back to a generated one rather than shipping empty. While any of this is running, the
canvas dims under a spinner and the three buttons disable — a free-tier model can take up to a
couple of minutes, so this is there to make clear the app is working, not stuck.

**Drag-and-drop canvas** — dashboards render on a real 12-column canvas, and **Move & resize**
is on by default: drag any element by its header bar to move it, pull the bottom-right corner
to resize. Both snap to the grid and save immediately. Turn the toggle off to lock the layout.
For exact placement, every element's property panel also has numeric width, height, column and
row boxes. New elements find the first free slot rather than landing on top of what is already
there. It is a self-contained Streamlit component with no npm build and no CDN — Plotly's own
browser bundle is vendored from the installed package on first run, so the canvas works
offline. Switch **Canvas** off for a plain stacked layout.

**Five page layouts** — each page can be arranged automatically by picking a layout beside its
name:

| Layout | Arrangement |
|---|---|
| Overview | KPI banner across the top, then charts two to a row |
| Spotlight | One full-width hero chart, the rest small beneath it |
| Sidebar | A tall main chart on the left, everything else stacked on the right |
| Column | One full-width element per row, for reading top to bottom |
| Compact | Dense tiling — three charts to a row, six KPIs |

A page on a layout keeps itself arranged, so newly added charts drop into place. Moving or
resizing anything by hand switches that page to **Custom** and keeps your positions; pick a
layout again at any time to re-flow it.

**Report pages** — a report holds as many pages as you like, switched by tabs above the
canvas, each with its own elements. Add, rename, duplicate and delete pages; export the
current page or the whole report.

**As many datasets as you like** — load several sources together and mix them in one report;
there is no cap. Each element remembers which dataset it reads from, and filters are held per
dataset, so narrowing one leaves the others untouched. Dropping a multi-sheet Excel workbook
can import every worksheet as its own dataset in one go.

**Charts** — bar, line, area, scatter, pie, donut, histogram, box, heatmap and treemap, plus
KPI cards and markdown text blocks. All Plotly, so hover, zoom and pan come for free.

**Colour** — nine built-in chart palettes (Default, Vibrant, Ocean, Sunset, Forest, Berry,
Colourblind safe, Monochrome blue, Application) picked from **Appearance** in the left menu,
with a live swatch preview. The same panel can import a theme file — a real Power BI theme
export (its `dataColors` list) or ChatBI's own `colors` format both work — which adds it as a
new palette choice for this session, or export the current palette back out the same way. The
choice drives every chart, every KPI accent bar and every export, including AI-suggested
charts. Seven Plotly templates set the plotting surface (`plotly_dark` for dark charts), and a
separate continuous scale applies to heatmaps. Individual charts and KPI cards can still
override the palette with their own colour picker.

**Filters** — pick any columns to filter on and get the right control automatically: a date
range for dates, a slider for numbers, a multiselect for categories. Filters are kept per
dataset and apply to every chart, KPI and export drawing on that dataset — across all pages,
including ones not currently on screen.

**Data sources** — all five sit together under **Data** in the left menu; there is no mode
to switch between them, and nothing has to be unloaded first.

| Source | Notes |
|---|---|
| File upload | Drop several files at once. CSV, TSV, Excel (`.xlsx`/`.xls`/`.xlsm`, optionally one dataset per worksheet), JSON, Parquet |
| Google Sheets | Paste the share URL; needs "Anyone with the link can view" |
| URL / API | CSV, TSV, JSON or Parquet; format auto-detected, nested JSON flattened |
| SQL database | Any SQLAlchemy URL — PostgreSQL, MySQL, SQLite, Snowflake, Databricks |
| Sample data | Three synthetic datasets so you can demo with zero setup |

Want files to practise with?

```bash
python make_sample_files.py      # five small datasets across different domains
python make_demo_dataset.py      # one richer dataset with real signals planted in it -
                                  # seasonality, an underperforming region, a category
                                  # that gets returned, discounting that erodes margin -
                                  # so Ask produces genuine findings instead of noise
```

Both write to a **ChatBI samples** folder on your Desktop, with money as `$1,200.50` and
dates as text, so you can watch the import clean them up.

Text columns holding dates (`2024-03-01`) or money (`$1,200.50`) are converted automatically,
while ID-like columns such as postcodes are deliberately left as text.

**Exports**

| Format | Contents |
|---|---|
| PDF | Title page, then one section per sheet - each with its own heading, its own KPI table and its own rendered charts, mirroring the report's actual sheets |
| Excel `.xlsx` | A configurable multi-sheet workbook — see below |
| CSV (raw) | The filtered dataset |
| CSV (sectioned) | Raw data + summary statistics + each chart's aggregated data in one file |
| Interactive HTML | A self-contained dashboard file — fully interactive, opens offline, no server |
| Report JSON | Every page, element and grid position; re-upload it later to restore the report |

### The Excel workbook

Choosing **Excel** reveals a workbook options panel. Pick any combination of sheets:

| Sheet | Contents |
|---|---|
| Raw data | The filtered dataset |
| Summary statistics | Per column: dtype, nulls, unique, min/max/mean/std, date range |
| KPIs | Every KPI card with its column, aggregation and computed value |
| Chart data | One sheet per chart, holding exactly the aggregated numbers it plots |
| Pivot table | A cross-tab you configure inline — rows, columns, values, aggregation — with row and column totals |
| Dashboard config | Every element's page, dataset, type, axes, aggregation and size, so a colleague can see how the report was built |

Six formatting themes — Corporate blue, Slate, Forest, Plum, Amber and Minimal — set the
header fill, row banding and borders. Every sheet gets a styled header row, frozen panes,
autofilter, number formats and auto-width columns.

---

## Choosing a model

| Provider | Default model | Key |
|---|---|---|
| **Groq** | `llama-3.3-70b-versatile` | <https://console.groq.com/keys> — fastest free tier |
| **Gemini** | `gemini-2.5-flash` | <https://aistudio.google.com/apikey> |
| **Nvidia** | `openai/gpt-oss-120b` | <https://build.nvidia.com> |
| **Claude** | `claude-opus-5` | <https://console.anthropic.com> — best suggestion quality |
| **Ollama** | `mistral` | No key. `ollama serve` + `ollama pull mistral` |

The Model dropdown is filled from the provider's own catalogue whenever a key is present, so
retired models disappear instead of failing at request time — providers do drop models without
warning. Pick **Custom...** to type any id yourself; the list above is only the offline default.

Groq, Gemini and Nvidia are called over plain HTTP, so they need no extra SDK. Claude uses the
official `anthropic` package, requests a strict JSON schema for its response, and asks the API
for server-side refusal fallbacks — falling back automatically to a simpler request shape if
your installed SDK version does not support those options.

Whatever the provider returns is validated against your actual schema before anything is
drawn: invented columns are dropped, near-miss column names (`revenue` → `Revenue`) are
resolved, impossible aggregations are repaired, and duplicates are removed.

Model output is also repaired before validation. Smaller models regularly wrap JSON in prose
or a ``` fence, and some finish a complete answer without emitting the closing `}` — observed
live from `meta/llama-3.3-70b-instruct`. ChatBI unwraps fences, finds the object inside
surrounding text, and closes an unterminated one; if generation was cut mid-value it falls
back to the last element that completed, rather than failing the whole request.

### Where API keys come from

Checked in order: `.streamlit/secrets.toml` → environment variable (including a local `.env`)
→ whatever you type in the left menu. Keys typed there live in the browser session only;
ChatBI never writes them to disk.

Under **AI model** in the left menu, **Check connection** sends the smallest possible request
— "reply with the word ready" — and reports back in plain language: success with the model
name and response time, or exactly what went wrong (no key yet, model retired, provider
timed out). Use it before relying on a provider for a real report, not after.

```bash
cp .env.example .env            # then fill in the providers you use
# or, for Streamlit Cloud:
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

---

## Deploying

**Streamlit Cloud** — push to GitHub, point <https://share.streamlit.io> at the repo with
`app.py` as the entry point, and paste your keys into the Secrets box. Free tier, ~60 seconds.

**Render** — the included `render.yaml` is a Blueprint: New → Blueprint → select the repo, then
set your keys in the dashboard.

**Docker**

```bash
docker build -t chatbi .
docker run -p 8501:8501 -e GROQ_API_KEY=gsk_xxx chatbi
```

**Local**

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt
streamlit run app.py
```

---

## Optional dependencies

The core install covers everything except database access. Uncomment what you need in
`requirements.txt`:

| Need | Package |
|---|---|
| Any SQL database | `sqlalchemy` (plus a driver below) |
| PostgreSQL | `psycopg2-binary` |
| MySQL / MariaDB | `pymysql` |
| Parquet files | `pyarrow` |
| Snowflake | `snowflake-sqlalchemy` |
| Databricks | `databricks-sql-connector` |

**PDF and PNG export** use `kaleido` to rasterise charts. The pinned `kaleido==0.2.1` bundles
its own Chromium, so it works on Streamlit Cloud and in Docker with no extra setup. `kaleido>=1.0`
works too but expects a local Chrome — run `plotly_get_chrome` after installing it.

---

## How it fits together

`app.py` is organised in nine labelled sections, top to bottom:

| Section | What lives there |
|---|---|
| 1. Constants | Provider registry, chart types, palettes, sizes, the JSON contract sent to the model |
| 2. Helpers | JSON extraction and repair, type coercion, schema profiling |
| 3. LLM connectors | One function per provider behind a single `call_llm` entry point |
| 4. Data loading | The five sources, with caching keyed on file bytes and URLs |
| 5. Filters | Type-aware filter bar, stored per dataset |
| 6. Chart engine | Aggregation (`prepare_frame`) then figure construction (`build_figure`) |
| 7. Exports | CSV, Excel, PDF, HTML, report JSON |
| 8. Workspace | Grid placement, the payload the shell renders, and `handle_workspace_action` |
| 9. Shell & menu | Mounting the component, the first-run screen, the left menu |

A report is plain data in `st.session_state` — a list of sheets, each a list of element dicts
carrying their own grid position — which is why saving it is just `json.dumps` and loading it
is just `json.loads`. The UI lives in `components/workspace/` (`index.html` for the chrome,
`workspace.js` for the behaviour), implementing the Streamlit component protocol directly in
vanilla JavaScript.

Adding a provider means writing one function that takes `(api_key, model, system, user)` and
returns text, then adding an entry to `PROVIDERS` and a branch in `call_llm`.

---

## Tests

```bash
pip install pytest
pytest tests/ -v
```

229 tests covering three layers:

- **Pure logic** — type coercion, LLM response parsing and truncation repair, the chart engine
  across all ten chart types and every palette/template, and each export format.
- **Connectors** — request shape, response parsing, JSON-mode negotiation, timeout-vs-4xx
  handling, and Claude's request fallback chain, all against mocked transports.
- **The workspace** — every intent the shell can raise (place a visual, edit it in the Format
  pane, resize, drag, delete, duplicate, switch sheet, choose a layout, ask) lands in
  `handle_workspace_action`, which is ordinary Python and is driven directly. The payload the
  shell receives is asserted too.
- **The left menu** — data sources, filters, the connection check and every export format,
  through Streamlit's `AppTest`.

The shell's own rendering, dragging, resizing and selection are verified in a real browser,
which `AppTest` cannot do since it does not execute component JavaScript.

---

## Known limits

- Visuals can overlap if you drag one on top of another; new ones are placed without
  collisions but neighbours are not pushed aside afterwards.
- Every interaction round-trips to the Python process, so the shell responds in a beat rather
  than instantly. Dragging and resizing are smooth because they are handled entirely in the
  browser and only the final position is sent.
- Charts render from an in-memory pandas DataFrame, so very large datasets should be narrowed
  with a `LIMIT` in the SQL source or by pre-aggregating. Five loaded datasets all live in
  memory at once.
- Datasets are held in the browser session, so a refresh means loading them again.
- No authentication or multi-user state — one browser session is one dashboard. Put it behind
  your own auth proxy if you expose it publicly.
- Drill-down, scheduled reports and PowerPoint export are not implemented.

---

## Licence

MIT. Do what you like with it.
