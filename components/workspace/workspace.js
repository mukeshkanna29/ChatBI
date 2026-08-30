/* ChatBI workspace shell.
 *
 * Owns the whole application surface - app bar, data pane, visual gallery, canvas,
 * format pane and sheet tabs - and talks to Python over the Streamlit component
 * protocol. All state lives in Python; this file renders it and posts back intents,
 * so business logic stays where it is tested.
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- transport
  function post(type, payload) {
    window.parent.postMessage(
      Object.assign({ isStreamlitMessage: true, type: type }, payload || {}), "*");
  }
  function setFrameHeight(h) { post("streamlit:setFrameHeight", { height: h }); }

  var seq = 0;
  function send(action) {
    // `seq` guarantees a distinct value each time, so Streamlit never dedupes two
    // identical intents (deleting twice, clicking the same visual twice...).
    post("streamlit:setComponentValue",
         { value: Object.assign({ seq: ++seq }, action), dataType: "json" });
  }

  // ---------------------------------------------------------------- state
  var S = {
    items: [], datasets: [], fields: [], pages: [], layouts: [],
    themes: [], activeTheme: "", prompts: [], loading: false,
    selected: null, activeDataset: "", activePage: "", reportName: "",
    pageLayout: "Custom", canAsk: false, columns: 12, row: 40, sig: null,
    picked: []          // field names selected in the Data pane
  };
  var GAP = 8, cellW = 100;

  var el = {
    canvas: document.getElementById("canvas"),
    stage: document.getElementById("stage"),
    blank: document.getElementById("blank"),
    datasets: document.getElementById("datasets"),
    fields: document.getElementById("fields"),
    gallery: document.getElementById("gallery"),
    galleryHint: document.getElementById("galleryHint"),
    format: document.getElementById("format"),
    tabs: document.getElementById("tabs"),
    rname: document.getElementById("rname"),
    ask: document.getElementById("ask"),
    askgo: document.getElementById("askgo"),
    askadd: document.getElementById("askadd"),
    askrec: document.getElementById("askrec"),
    aiMark: document.querySelector(".ai-mark"),
    busy: document.getElementById("busy"),
    busyMsg: document.getElementById("busyMsg"),
    theme: document.getElementById("themeSel"),
    blankGo: document.getElementById("blankGo"),
    blankChips: document.getElementById("blankChips"),
    blankTitle: document.getElementById("blankTitle"),
    blankBody: document.getElementById("blankBody"),
  };

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g,
      function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; });
  }

  // ---------------------------------------------------------------- geometry
  function measure() {
    var w = el.canvas.clientWidth || el.stage.clientWidth || 1100;
    cellW = (w < 80 ? 1100 : w) / S.columns;
    el.canvas.style.setProperty("--cw", cellW + "px");
    el.canvas.style.setProperty("--ch", S.row + "px");
  }
  function place(node, L) {
    node.style.left = (L.x * cellW + GAP / 2) + "px";
    node.style.top = (L.y * S.row + GAP / 2) + "px";
    node.style.width = (L.w * cellW - GAP) + "px";
    node.style.height = (L.h * S.row - GAP) + "px";
  }
  function canvasHeight() {
    var rows = 0;
    S.items.forEach(function (i) { rows = Math.max(rows, i.layout.y + i.layout.h); });
    return Math.max(rows + 1, 8) * S.row;
  }
  function relayout() {
    measure();
    S.items.forEach(function (i) {
      if (i._node) place(i._node, i.layout);
      if (i._plot && window.Plotly) {
        try { Plotly.Plots.resize(i._plot); } catch (e) { /* not drawn yet */ }
      }
    });
    el.canvas.style.height = canvasHeight() + "px";
  }

  // ---------------------------------------------------------------- drag & resize
  function beginDrag(item, ev) {
    if (ev.button !== 0) return;
    ev.preventDefault();
    selectItem(item.id);
    var node = item._node, o = { x: ev.clientX, y: ev.clientY };
    var start = { x: item.layout.x, y: item.layout.y };
    node.classList.add("drag");
    function move(e) {
      item.layout.x = clamp(start.x + Math.round((e.clientX - o.x) / cellW),
                            0, S.columns - item.layout.w);
      item.layout.y = Math.max(0, start.y + Math.round((e.clientY - o.y) / S.row));
      place(node, item.layout);
      el.canvas.style.height = canvasHeight() + "px";
    }
    function up() {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      node.classList.remove("drag");
      relayout();
      sendLayout();
    }
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }

  function beginResize(item, ev) {
    if (ev.button !== 0) return;
    ev.preventDefault(); ev.stopPropagation();
    selectItem(item.id);
    var node = item._node, o = { x: ev.clientX, y: ev.clientY };
    var start = { w: item.layout.w, h: item.layout.h };
    node.classList.add("size");
    function move(e) {
      item.layout.w = clamp(start.w + Math.round((e.clientX - o.x) / cellW),
                            2, S.columns - item.layout.x);
      item.layout.h = Math.max(3, start.h + Math.round((e.clientY - o.y) / S.row));
      place(node, item.layout);
      el.canvas.style.height = canvasHeight() + "px";
    }
    function up() {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      node.classList.remove("size");
      relayout();
      sendLayout();
      drawFormat();
    }
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }

  function sendLayout() {
    send({ type: "layout", layout: S.items.map(function (i) {
      return { id: i.id, x: i.layout.x, y: i.layout.y, w: i.layout.w, h: i.layout.h };
    }) });
  }

  // ---------------------------------------------------------------- canvas
  function tileBody(item) {
    var body = document.createElement("div");
    body.className = "tbody";
    if (item.kind === "kpi") {
      body.innerHTML =
        '<div class="kpi"><div class="rule"></div><div class="lab"></div>' +
        '<div class="val"></div><div class="sub"></div></div>';
      body.querySelector(".rule").style.background = item.color || "#2f6fed";
      body.querySelector(".lab").textContent = item.title || "";
      body.querySelector(".val").textContent = item.value || "-";
      body.querySelector(".sub").textContent = item.subtitle || "";
    } else if (item.kind === "text") {
      var t = document.createElement("div");
      t.className = "txt"; t.textContent = item.content || "";
      body.appendChild(t);
    } else {
      var p = document.createElement("div");
      p.className = "plot"; body.appendChild(p); item._plot = p;
    }
    return body;
  }

  function drawPlot(item) {
    if (!item._plot || !item.figure || !window.Plotly) return;
    try {
      Plotly.newPlot(item._plot, item.figure.data || [], item.figure.layout || {}, {
        responsive: true,
        displaylogo: false,
        displayModeBar: "hover",          // zoom, pan, reset, download PNG
        scrollZoom: true,
        doubleClick: "reset",
        modeBarButtonsToRemove: ["select2d", "lasso2d", "toggleSpikelines"],
        toImageButtonOptions: { format: "png", scale: 2 }
      });
    } catch (err) {
      item._plot.innerHTML = '<div class="bad"></div>';
      item._plot.querySelector(".bad").textContent = "Could not draw this chart: " + err;
    }
  }

  function drawCanvas() {
    el.canvas.innerHTML = "";
    el.canvas.classList.add("grid");
    measure();
    el.blank.style.display = S.items.length ? "none" : "grid";

    S.items.forEach(function (item) {
      var node = document.createElement("div");
      node.className = "tile" + (item.id === S.selected ? " sel" : "");
      node.dataset.id = item.id;
      node.addEventListener("mousedown", function () { selectItem(item.id); });

      var head = document.createElement("div");
      head.className = "thead";
      head.innerHTML = '<span class="t"></span><span class="x">' +
        '<button class="iconbtn" data-a="duplicate" title="Duplicate">&#10697;</button>' +
        '<button class="iconbtn del" data-a="delete" title="Delete">&#10005;</button></span>';
      head.querySelector(".t").textContent = item.title || item.kind;
      head.addEventListener("mousedown", function (e) {
        if (e.target.closest(".iconbtn")) return;
        beginDrag(item, e);
      });
      head.querySelectorAll(".iconbtn").forEach(function (b) {
        b.addEventListener("click", function (e) {
          e.stopPropagation();
          send({ type: b.dataset.a, id: item.id });
        });
      });
      node.appendChild(head);
      node.appendChild(tileBody(item));

      var grip = document.createElement("div");
      grip.className = "grip";
      grip.addEventListener("mousedown", function (e) { beginResize(item, e); });
      node.appendChild(grip);

      item._node = node;
      place(node, item.layout);
      el.canvas.appendChild(node);
    });

    S.items.forEach(drawPlot);
    el.canvas.style.height = canvasHeight() + "px";
  }

  function selectItem(id) {
    if (S.selected === id) return;
    S.selected = id;
    S.items.forEach(function (i) {
      if (i._node) i._node.classList.toggle("sel", i.id === id);
    });
    drawFormat();
  }

  // ---------------------------------------------------------------- left pane
  function drawDatasets() {
    el.datasets.innerHTML = "";
    if (!S.datasets.length) {
      el.datasets.innerHTML = '<div class="hint" style="padding:2px 4px 8px">' +
        'No data yet. Click <b>Data</b> above to add a file, a link or a database.</div>';
      return;
    }
    S.datasets.forEach(function (d) {
      var row = document.createElement("div");
      row.className = "ds" + (d.name === S.activeDataset ? " on" : "");
      row.innerHTML = '<span></span><span class="meta"></span>';
      row.children[0].textContent = d.name;
      row.children[1].textContent = d.rows.toLocaleString();
      row.addEventListener("click", function () {
        send({ type: "dataset", name: d.name });
      });
      el.datasets.appendChild(row);
    });
  }

  function drawFields() {
    el.fields.innerHTML = "";
    S.fields.forEach(function (f) {
      var row = document.createElement("div");
      var on = S.picked.indexOf(f.name) >= 0;
      row.className = "field" + (on ? " sel" : "");
      var g = f.kind === "number" ? "g-num" : f.kind === "date" ? "g-date" : "g-txt";
      var letter = f.kind === "number" ? "#" : f.kind === "date" ? "D" : "A";
      row.innerHTML = '<span class="glyph ' + g + '">' + letter + "</span><span></span>";
      row.children[1].textContent = f.name;
      row.title = f.name + " (" + f.kind + ")";
      row.addEventListener("click", function () {
        var at = S.picked.indexOf(f.name);
        if (at >= 0) S.picked.splice(at, 1);
        else { S.picked.push(f.name); if (S.picked.length > 2) S.picked.shift(); }
        drawFields(); drawGalleryHint();
      });
      el.fields.appendChild(row);
    });
  }

  var VISUALS = [
    ["bar", "Bar", '<rect x="3" y="9" width="3.4" height="9"/><rect x="8.3" y="5" width="3.4" height="13"/><rect x="13.6" y="11" width="3.4" height="7"/>'],
    ["line", "Line", '<polyline points="3,15 8,9 12,12 18,4" fill="none" stroke="currentColor" stroke-width="2"/>'],
    ["area", "Area", '<path d="M3 16 L8 9 L12 12 L18 5 L18 18 L3 18 Z"/>'],
    ["pie", "Pie", '<circle cx="10.5" cy="10.5" r="7.5"/><path d="M10.5 10.5 L10.5 3 A7.5 7.5 0 0 1 18 10.5 Z" fill="#fff"/>'],
    ["donut", "Donut", '<circle cx="10.5" cy="10.5" r="7.5"/><circle cx="10.5" cy="10.5" r="3.6" fill="#fff"/>'],
    ["scatter", "Scatter", '<circle cx="5.5" cy="14" r="1.8"/><circle cx="10" cy="8" r="1.8"/><circle cx="14.5" cy="12" r="1.8"/><circle cx="16.5" cy="5.5" r="1.8"/>'],
    ["histogram", "Histogram", '<rect x="3" y="12" width="3" height="6"/><rect x="7" y="7" width="3" height="11"/><rect x="11" y="4" width="3" height="14"/><rect x="15" y="10" width="3" height="8"/>'],
    ["box", "Box", '<rect x="6" y="7" width="9" height="7"/><line x1="10.5" y1="3" x2="10.5" y2="7" stroke="currentColor" stroke-width="1.6"/><line x1="10.5" y1="14" x2="10.5" y2="18" stroke="currentColor" stroke-width="1.6"/>'],
    ["heatmap", "Heatmap", '<rect x="3" y="3" width="6.5" height="6.5"/><rect x="11" y="3" width="6.5" height="6.5" opacity=".45"/><rect x="3" y="11" width="6.5" height="6.5" opacity=".45"/><rect x="11" y="11" width="6.5" height="6.5"/>'],
    ["treemap", "Treemap", '<rect x="3" y="3" width="9" height="14"/><rect x="13" y="3" width="5" height="6.5" opacity=".5"/><rect x="13" y="10.5" width="5" height="6.5" opacity=".5"/>'],
    ["kpi", "KPI card", '<rect x="3" y="6" width="15" height="9" rx="1.5" opacity=".25"/><rect x="5.5" y="9" width="7" height="3"/>'],
    ["text", "Text", '<rect x="3" y="5" width="15" height="1.8"/><rect x="3" y="9" width="15" height="1.8" opacity=".6"/><rect x="3" y="13" width="9" height="1.8" opacity=".4"/>'],
  ];

  function drawGallery() {
    el.gallery.innerHTML = "";
    VISUALS.forEach(function (v) {
      var b = document.createElement("button");
      b.className = "viz"; b.title = v[1];
      b.innerHTML = '<svg viewBox="0 0 21 21" fill="currentColor" style="color:#475467">' +
                    v[2] + "</svg>";
      b.addEventListener("click", function () {
        send({ type: "add", chart: v[0], fields: S.picked.slice() });
        S.picked = []; drawFields(); drawGalleryHint();
      });
      el.gallery.appendChild(b);
    });
  }

  function drawGalleryHint() {
    el.galleryHint.textContent = S.picked.length
      ? "Using " + S.picked.join(" + ") + " - click a visual to place it."
      : "Pick one or two fields above, then click a visual. Or just click one and let "
        + "ChatBI choose the fields.";
  }

  // ---------------------------------------------------------------- format pane
  function field(label, controlHTML) {
    return '<div class="fld"><label>' + esc(label) + "</label>" + controlHTML + "</div>";
  }
  function options(list, chosen) {
    return list.map(function (o) {
      return '<option value="' + esc(o) + '"' + (o === chosen ? " selected" : "") + ">" +
             esc(o) + "</option>";
    }).join("");
  }

  function drawFormat() {
    var item = S.items.filter(function (i) { return i.id === S.selected; })[0];
    if (!item) {
      el.format.innerHTML = '<div class="empty-fmt">Select a visual on the canvas to ' +
        "change its title, fields, colour and size.</div>";
      return;
    }
    var cols = S.fields.map(function (f) { return f.name; });
    var nums = S.fields.filter(function (f) { return f.kind === "number"; })
                       .map(function (f) { return f.name; });
    var html = "";
    html += field("Title", '<input type="text" id="f_title" value="' +
                  esc(item.title || "") + '">');

    if (item.kind === "chart") {
      html += field("Visual", '<select id="f_type">' +
        options(VISUALS.filter(function (v) { return v[0] !== "kpi" && v[0] !== "text"; })
                       .map(function (v) { return v[0]; }), item.type) + "</select>");
      html += field("X axis", '<select id="f_x">' + options(cols, item.x) + "</select>");
      html += field("Y axis", '<select id="f_y">' +
        options(["(none)"].concat(cols), item.y || "(none)") + "</select>");
      html += '<div class="fld"><div class="row2">' +
        '<div><label>Summarise</label><select id="f_agg">' +
          options(item.aggOptions || ["sum"], item.agg) + "</select></div>" +
        '<div><label>Split by</label><select id="f_by">' +
          options(["(none)"].concat(cols), item.color_by || "(none)") + "</select></div>" +
        "</div></div>";
      html += '<div class="fld"><div class="row2">' +
        '<div><label>Sort</label><select id="f_sort">' +
          options(["none", "value_desc", "value_asc", "x_asc", "x_desc"], item.sort) +
          "</select></div>" +
        '<div><label>Top N</label><input type="number" id="f_top" min="0" max="200" value="' +
          (item.top_n || 0) + '"></div></div></div>';
      html += field("Colour", '<input type="color" id="f_color" value="' +
                    esc(item.color || "#2f6fed") + '">');
    } else if (item.kind === "kpi") {
      html += field("Measure", '<select id="f_col">' +
                    options(cols, item.column) + "</select>");
      html += field("Summarise", '<select id="f_agg">' +
                    options(item.aggOptions || ["sum"], item.agg) + "</select>");
      html += field("Accent", '<input type="color" id="f_color" value="' +
                    esc(item.color || "#2f6fed") + '">');
    } else {
      html += field("Text", '<input type="text" id="f_content" value="' +
                    esc(item.content || "") + '">');
    }

    html += '<div class="fld"><div class="row2">' +
      '<div><label>Width</label><input type="number" id="f_w" min="1" max="12" value="' +
        item.layout.w + '"></div>' +
      '<div><label>Height</label><input type="number" id="f_h" min="3" max="60" value="' +
        item.layout.h + '"></div></div></div>';

    el.format.innerHTML = html;

    function on(id, ev, fn) {
      var node = document.getElementById(id);
      if (node) node.addEventListener(ev, fn);
    }
    function patch(key, value) { send({ type: "update", id: item.id, patch: patch1(key, value) }); }
    function patch1(k, v) { var o = {}; o[k] = v; return o; }
    function nullable(v) { return v === "(none)" ? null : v; }

    on("f_title", "change", function (e) { patch("title", e.target.value); });
    on("f_type", "change", function (e) { patch("type", e.target.value); });
    on("f_x", "change", function (e) { patch("x", e.target.value); });
    on("f_y", "change", function (e) { patch("y", nullable(e.target.value)); });
    on("f_agg", "change", function (e) { patch("agg", e.target.value); });
    on("f_by", "change", function (e) { patch("color_by", nullable(e.target.value)); });
    on("f_sort", "change", function (e) { patch("sort", e.target.value); });
    on("f_top", "change", function (e) { patch("top_n", parseInt(e.target.value || 0, 10)); });
    on("f_color", "change", function (e) { patch("color", e.target.value); });
    on("f_col", "change", function (e) { patch("column", e.target.value); });
    on("f_content", "change", function (e) { patch("content", e.target.value); });
    on("f_w", "change", function (e) {
      send({ type: "resize", id: item.id, w: parseInt(e.target.value, 10), h: item.layout.h });
    });
    on("f_h", "change", function (e) {
      send({ type: "resize", id: item.id, w: item.layout.w, h: parseInt(e.target.value, 10) });
    });
    void nums;
  }

  // ---------------------------------------------------------------- sheet tabs
  function drawTabs() {
    el.tabs.innerHTML = "";
    S.pages.forEach(function (p) {
      var t = document.createElement("button");
      t.className = "tab" + (p.id === S.activePage ? " on" : "");
      t.innerHTML = '<span></span><span class="k" title="Delete sheet">&#10005;</span>';
      t.children[0].textContent = p.name;
      t.addEventListener("click", function (e) {
        if (e.target.classList.contains("k")) {
          if (S.pages.length > 1) send({ type: "page", action: "delete", id: p.id });
          return;
        }
        if (p.id === S.activePage) {
          var name = window.prompt("Rename this sheet", p.name);
          if (name && name !== p.name) send({ type: "page", action: "rename", id: p.id, name: name });
          return;
        }
        send({ type: "page", action: "select", id: p.id });
      });
      el.tabs.appendChild(t);
    });
    var add = document.createElement("button");
    add.className = "tabadd"; add.textContent = "+"; add.title = "New sheet";
    add.addEventListener("click", function () { send({ type: "page", action: "add" }); });
    el.tabs.appendChild(add);

    var spacer = document.createElement("div");
    spacer.className = "spacer";
    el.tabs.appendChild(spacer);

    var lay = document.createElement("div");
    lay.className = "lay";
    lay.innerHTML = '<span style="color:#98a2b3;font-size:11.5px">Layout</span>' +
      '<select id="pagelayout">' + options(S.layouts, S.pageLayout) + "</select>";
    el.tabs.appendChild(lay);
    lay.querySelector("select").addEventListener("change", function (e) {
      send({ type: "pagelayout", layout: e.target.value });
    });
  }

  function drawBlank() {
    if (!el.blankGo) return;
    el.blankGo.disabled = !S.canAsk;
    if (S.canAsk) {
      el.blankTitle.textContent = "Let ChatBI build this for you";
      el.blankBody.textContent = "It reads your columns and lays out the sheets - KPIs, "
        + "charts and arrangement - in one go.";
    } else {
      el.blankTitle.textContent = "Add an AI key to have this built for you";
      el.blankBody.textContent = "Open the menu on the left, paste a key under AI model, "
        + "and ChatBI will design the sheets from your columns.";
    }
    el.blankChips.innerHTML = "";
    if (!S.canAsk) return;
    S.prompts.forEach(function (text) {
      var chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = text;
      chip.title = text;
      chip.addEventListener("click", function () {
        el.ask.value = text;         // show what was asked, then run it
        runAsk("build", text);
      });
      el.blankChips.appendChild(chip);
    });
  }

  function drawThemes() {
    if (!el.theme) return;
    el.theme.innerHTML = '<option value="">Custom</option>' + S.themes.map(function (t) {
      return '<option value="' + t.name + '"' +
             (t.name === S.activeTheme ? " selected" : "") + ">" + t.name + "</option>";
    }).join("");
    var current = S.themes.filter(function (t) { return t.name === S.activeTheme; })[0];
    el.theme.title = current
      ? current.name + " - " + current.note
      : "Pick a theme to restyle and re-arrange this sheet";
  }

  // ---------------------------------------------------------------- busy state
  // A Build/Add here/Recommend call round-trips through Python and can take
  // anywhere from a few seconds to a couple of minutes on a free-tier model.
  // Without this, the canvas just sits there and looks frozen or broken.
  function setLoading(kind) {
    S.loading = !!kind;
    if (el.askgo) el.askgo.disabled = S.loading || !S.canAsk;
    if (el.askadd) el.askadd.disabled = S.loading || !S.canAsk;
    if (el.askrec) el.askrec.disabled = S.loading || !S.canAsk;
    if (el.ask) el.ask.disabled = S.loading;
    var wrap = document.querySelector(".ask-wrap");
    if (wrap) wrap.classList.toggle("loading", S.loading);
    if (el.aiMark) el.aiMark.classList.toggle("spin", S.loading);
    if (el.busy) el.busy.classList.toggle("on", S.loading);
    if (el.busyMsg) {
      el.busyMsg.textContent = kind === "addhere"
        ? "Adding to this sheet…"
        : "Building your report…";
    }
  }

  function runAsk(kind, promptText) {
    if (S.loading || !S.canAsk) return;
    setLoading(kind);
    send({ type: kind, prompt: promptText });
  }

  // ---------------------------------------------------------------- app bar
  el.rname.addEventListener("change", function () {
    send({ type: "rename_report", name: el.rname.value });
  });
  function ask(kind) { runAsk(kind, el.ask.value.trim()); }
  el.askgo.addEventListener("click", function () { ask("build"); });
  el.askadd.addEventListener("click", function () { ask("addhere"); });
  if (el.askrec) {
    // Deliberately ignores whatever is typed above - the point is that ChatBI
    // decides the structure itself, independent of any prompt the user wrote.
    el.askrec.addEventListener("click", function () { runAsk("build", ""); });
  }
  el.ask.addEventListener("keydown", function (e) {
    // Enter adds a line (prompts are multi-line); Ctrl or Cmd + Enter builds.
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ask("build"); }
  });
  if (el.blankGo) {
    el.blankGo.addEventListener("click", function () {
      runAsk("build", el.ask.value.trim());
    });
  }
  if (el.theme) {
    el.theme.addEventListener("change", function (e) {
      if (e.target.value) send({ type: "theme", name: e.target.value });
    });
  }

  // ---------------------------------------------------------------- render
  function render(args) {
    S.items = (args.items || []).map(function (it, i) {
      var g = it.layout || {};
      function n(v, d) { return (typeof v === "number" && isFinite(v)) ? v : d; }
      return Object.assign({}, it, {
        layout: { x: n(g.x, 0), y: n(g.y, i * 6), w: n(g.w, 6), h: n(g.h, 6) }
      });
    });
    S.datasets = args.datasets || [];
    S.fields = args.fields || [];
    S.pages = args.pages || [];
    S.layouts = args.layouts || [];
    S.themes = args.themes || [];
    S.activeTheme = args.activeTheme || "";
    S.prompts = args.prompts || [];
    S.activeDataset = args.activeDataset || "";
    S.activePage = args.activePage || "";
    S.pageLayout = args.pageLayout || "Custom";
    S.columns = args.columns || 12;
    S.row = args.row_height || 40;
    S.canAsk = !!args.canAsk;
    if (document.activeElement !== el.rname) el.rname.value = args.reportName || "";
    if (S.selected && !S.items.some(function (i) { return i.id === S.selected; })) {
      S.selected = null;
    }
    el.askgo.disabled = !S.canAsk;
    el.askadd.disabled = !S.canAsk;
    if (el.askrec) el.askrec.disabled = !S.canAsk;
    el.ask.placeholder = S.canAsk
      ? "Describe the report you want, sheet by sheet:\n"
        + "Sheet 1: revenue and profit KPIs with a monthly trend.\n"
        + "Sheet 2: sales by region and by category."
      : "Open the menu (top left) and add an AI key under AI model to describe reports\n"
        + "in words. You can still build visuals by hand from the panel on the left.";

    drawDatasets(); drawFields(); drawGallery(); drawGalleryHint();
    drawThemes(); drawCanvas(); drawBlank(); drawFormat(); drawTabs();
  }

  function onMessage(event) {
    if (!event.data || event.data.type !== "streamlit:render") return;
    setLoading(false);                   // a real render means Python finished the last action,
                                          // even if it added nothing (e.g. "Add here" with no
                                          // new suggestions) and the content hash didn't change
    var args = event.data.args || {};
    if (args.sig === S.sig) return;      // Streamlit resends args on every rerun
    S.sig = args.sig;
    render(args);
  }

  window.addEventListener("message", onMessage);
  window.addEventListener("resize", relayout);
  if (window.ResizeObserver) {
    var last = 0;
    new ResizeObserver(function () {
      var w = el.canvas.clientWidth;
      if (w && Math.abs(w - last) > 1) { last = w; relayout(); }
    }).observe(el.canvas);
  }

  // Test hook - lets the geometry and interactions be driven from outside.
  window.__workspace = {
    state: function () {
      return {
        items: S.items.map(function (i) {
          return { id: i.id, kind: i.kind, title: i.title, layout: i.layout };
        }),
        selected: S.selected, pages: S.pages, activePage: S.activePage,
        datasets: S.datasets, fields: S.fields.length, picked: S.picked, cellW: cellW
      };
    },
    select: selectItem,
    pick: function (name) {
      S.picked.push(name); if (S.picked.length > 2) S.picked.shift();
      drawFields(); drawGalleryHint();
    }
  };

  post("streamlit:componentReady", { apiVersion: 1 });
  setFrameHeight(document.documentElement.clientHeight || 820);
})();
