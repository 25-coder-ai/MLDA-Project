/* ==========================================================================
   TRUST-CODE console — single-file SPA (no build step)
   ========================================================================== */
(() => {
  "use strict";

  // ------------------------------------------------------------------ utils
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const fmtPct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
  const fmtNum = (v, d = 0) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
  };

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
        else if (k === "html") el.innerHTML = v;
        else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
        else if (k === "dataset") Object.assign(el.dataset, v);
        else if (k === "value") el.value = v;
        else if (k === "checked") el.checked = !!v;
        else el.setAttribute(k, v === true ? "" : v);
      }
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const I = {
    check: '<path d="M20 6 9 17l-5-5"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    alert: '<path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4m0-4h.01"/>',
    chev: '<path d="m6 9 6 6 6-6"/>',
    play: '<path d="M6 4l14 8-14 8z"/>',
    download: '<path d="M12 3v12m0 0-4-4m4 4 4-4M4 21h16"/>',
    copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M3 6h18M8 6V4h8v2m-9 0 1 14h8l1-14"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8m0-5v5h-5"/>',
    shield: '<path d="M12 2 4 5v6c0 5 3.4 9.3 8 11 4.6-1.7 8-6 8-11V5z"/>',
    upload: '<path d="M12 21V9m0 0-4 4m4-4 4 4M4 3h16"/>',
    eye: '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
    code: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
    link: '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1m1 7a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    graph: '<circle cx="5" cy="6" r="3"/><circle cx="19" cy="6" r="3"/><circle cx="12" cy="18" r="3"/><path d="M7.5 7.5 10.5 16M16.5 7.5 13.5 16M8 6h8"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    sparkle: '<path d="M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  };
  const icon = (name, cls = "") => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 24 24"); if (cls) s.setAttribute("class", cls); s.innerHTML = I[name] || ""; return s; };

  // ------------------------------------------------------------------ state
  const DEFAULT_OPTIONS = { run_tests: true, run_mutation: true, run_behavior_probe: true, run_proofs: true, max_mutants: 10 };
  const S = {
    health: null,
    scenarios: [],
    scenarioId: null,
    ws: { files: {}, activeFile: null, requirements: "", review: "", patches: [], verifier: [], options: { ...DEFAULT_OPTIONS } },
    wbTab: "files",
    parsed: null,
    report: null,
    reportTab: "claims",
    fmFilter: null,
    explorer: { file: null, line: null },
    graph: { showTests: false, showImports: true, zoom: 1, tx: 0, ty: 0 },
    running: false,
  };

  // ------------------------------------------------------------------ api
  async function api(path, opts = {}) {
    const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: { "Content-Type": "application/json" } };
    if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
    const res = await fetch(path, init);
    const text = await res.text();
    let data = text;
    try { data = JSON.parse(text); } catch { /* plain text */ }
    if (!res.ok) {
      let msg = typeof data === "object" && data ? data.detail : text;
      if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
      throw new Error(msg || `HTTP ${res.status}`);
    }
    return data;
  }

  // ------------------------------------------------------------------ toasts & modal
  function toast(msg, kind = "info", ms = 3200) {
    const t = h("div", { class: `toast ${kind}` }, h("span", { class: "tdot" }), h("div", null, msg));
    $("#toasts").append(t);
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 260); }, ms);
  }
  function openModal(title, body) {
    $("#modalTitle").textContent = title;
    const mb = $("#modalBody");
    mb.innerHTML = "";
    mb.append(body);
    $("#modal").classList.remove("hidden");
  }
  function closeModal() { $("#modal").classList.add("hidden"); }
  $("#modalClose").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  async function copyText(text, label = "Copied to clipboard") {
    try { await navigator.clipboard.writeText(text); toast(label, "good"); }
    catch {
      const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }); ta.value = text; document.body.append(ta); ta.select();
      try { document.execCommand("copy"); toast(label, "good"); } catch { toast("Copy failed", "bad"); }
      ta.remove();
    }
  }
  function download(name, content, type) {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const a = h("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ------------------------------------------------------------------ theme
  function applyTheme(t) { document.documentElement.dataset.theme = t; store.set("tc.theme", t); }
  applyTheme(store.get("tc.theme", window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  $("#themeBtn").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

  // ------------------------------------------------------------------ history
  function saveHistory(report) {
    const list = store.get("tc.history", []);
    const entry = { id: report.report_id, created: report.created_at, headline: report.summary.headline, trust: report.summary.trust_index, scenario: S.scenarioId, report };
    let next = [entry, ...list.filter((x) => x.id !== report.report_id)].slice(0, 6);
    while (next.length && !store.set("tc.history", next)) next = next.slice(0, -1);
    renderHistory();
  }
  function renderHistory() {
    const menu = $("#historyMenu");
    menu.innerHTML = "";
    const list = store.get("tc.history", []);
    if (!list.length) { menu.append(h("div", { class: "dd-empty" }, "No runs yet — verify something first.")); return; }
    menu.append(h("div", { class: "sub-title", style: { margin: "8px 10px" } }, "Recent runs"));
    for (const e of list) {
      menu.append(h("div", { class: "dd-item", onclick: () => { S.report = e.report; S.fmFilter = null; $("#historyDropdown").classList.remove("open"); location.hash = "#/report"; render(); toast(`Opened ${e.id}`); } },
        scoreRing(e.trust, 36),
        h("div", { class: "grow" }, h("div", { class: "t" }, e.scenario ? titleOf(e.scenario) : e.id), h("div", { class: "faint", style: { fontSize: "11.5px" } }, `${new Date(e.created * 1000).toLocaleString()} · ${e.id}`))));
    }
    menu.append(h("div", { class: "dd-item", style: { justifyContent: "center", color: "var(--bad)" }, onclick: () => { store.set("tc.history", []); renderHistory(); toast("History cleared"); } }, "Clear history"));
  }
  $("#historyBtn").addEventListener("click", (e) => { e.stopPropagation(); $("#historyDropdown").classList.toggle("open"); });
  document.addEventListener("click", (e) => { if (!e.target.closest("#historyDropdown")) $("#historyDropdown").classList.remove("open"); });
  const titleOf = (id) => (S.scenarios.find((s) => s.id === id) || {}).title || id;

  // ------------------------------------------------------------------ health
  async function loadHealth() {
    const pill = $("#statusPill");
    try {
      S.health = await api("/api/v1/health");
      pill.classList.add("ok"); pill.classList.remove("down");
      const c = S.health.capabilities;
      $("#statusText").textContent = `${c.sandbox} · ${c.sast.join(" + ")}`;
    } catch {
      pill.classList.add("down");
      $("#statusText").textContent = "engine offline";
    }
  }
  $("#statusPill").addEventListener("click", () => {
    if (!S.health) { loadHealth(); return; }
    const c = S.health.capabilities;
    const rows = [
      ["Engine", c.engine], ["Sandbox backend", c.sandbox], ["Python parser", c.parsers.python], ["Polyglot parser", c.parsers.polyglot],
      ["Static analysers", c.sast.join(", ")], ["Claim NLP", c.nlp],
      ["Weights", `base ${c.weights.base} · +${c.weights.supported} supported · −${c.weights.unsupported} unsupported · −${c.weights.contradicted} contradicted · −${c.weights.hallucinated} hallucinated`],
      ["Verdict bands", `TRUSTED ≥ ${c.thresholds.trusted} · UNCERTAIN ≥ ${c.thresholds.uncertain} · else REJECTED`],
    ];
    const tools = Object.entries(c.tools).map(([k, v]) => h("span", { class: `chip ${v ? "good" : ""}` }, v ? icon("check") : icon("x"), k));
    openModal("Engine capabilities", h("div", { class: "stack" },
      h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("tbody", null, rows.map(([k, v]) => h("tr", null, h("td", { class: "muted", style: { width: "180px" } }, k), h("td", null, v)))))),
      h("div", { class: "sub-title" }, "External tools detected"), h("div", { class: "row wrap" }, tools),
      h("p", { class: "muted" }, "Missing tools are optional: the built-in deterministic SAST, AST resolver, sandbox harness and proof engine always run. Install Semgrep, Trivy or Docker (gVisor) to add more heterogeneous evidence.")));
  });

  // ------------------------------------------------------------------ router
  const ROUTES = ["workbench", "report", "evidence", "prove", "architecture"];
  const route = () => { const r = (location.hash.replace(/^#\/?/, "") || "workbench").split("?")[0]; return ROUTES.includes(r) ? r : "workbench"; };
  window.addEventListener("hashchange", render);
  function render() {
    const r = route();
    $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === r));
    const app = $("#app");
    app.innerHTML = "";
    const view = { workbench: viewWorkbench, report: viewReport, evidence: viewEvidence, prove: viewProve, architecture: viewArchitecture }[r]();
    view.classList.add("view");
    app.append(view);
  }

  // ------------------------------------------------------------------ syntax highlighting
  const PY_KW = new Set("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield".split(" "));
  const PY_BI = new Set("print len range int str float dict list set tuple sorted sum min max abs round isinstance open super enumerate zip map filter any all repr type ValueError TypeError KeyError IndexError Exception ZeroDivisionError AttributeError self cls".split(" "));
  function highlight(line) {
    const re = /(#.*$)|([rbfuRBFU]{0,2}"""(?:[^"\\]|\\.|"(?!""))*(?:"""|$)|[rbfuRBFU]{0,2}'''(?:[^'\\]|\\.|'(?!''))*(?:'''|$)|[rbfuRBFU]{0,2}"(?:\\.|[^"\\])*"?|[rbfuRBFU]{0,2}'(?:\\.|[^'\\])*'?)|(\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)|([A-Za-z_]\w*)/g;
    let out = "", last = 0, m, prev = "";
    while ((m = re.exec(line))) {
      out += esc(line.slice(last, m.index));
      const t = m[0];
      if (m[1]) out += `<span class="tk-com">${esc(t)}</span>`;
      else if (m[2]) out += `<span class="tk-str">${esc(t)}</span>`;
      else if (m[3]) out += `<span class="tk-num">${esc(t)}</span>`;
      else if (PY_KW.has(t)) out += `<span class="tk-kw">${t}</span>`;
      else if (prev === "def" || prev === "class" || line[re.lastIndex] === "(") out += `<span class="tk-fn">${t}</span>`;
      else if (PY_BI.has(t)) out += `<span class="tk-bi">${t}</span>`;
      else out += esc(t);
      if (m[4]) prev = t;
      last = re.lastIndex;
    }
    return out + esc(line.slice(last));
  }
  function renderDiff(text) {
    const box = h("div", { class: "diff" });
    for (const line of String(text || "").replace(/\n$/, "").split("\n")) {
      let cls = "";
      if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ")) cls = "meta";
      else if (line.startsWith("@@")) cls = "hunk";
      else if (line.startsWith("+")) cls = "add";
      else if (line.startsWith("-")) cls = "del";
      box.append(h("div", { class: cls }, line || " "));
    }
    return box;
  }

  // ------------------------------------------------------------------ small components
  function scoreColor(v) { return v >= 80 ? "var(--good)" : v >= 50 ? "var(--warn)" : "var(--bad)"; }
  function scoreRing(v, size = 44) {
    const r = (size - 6) / 2, c = 2 * Math.PI * r, off = c * (1 - clamp(v, 0, 100) / 100);
    const wrap = h("div", { class: "score-ring", style: { width: `${size}px`, height: `${size}px` } });
    wrap.innerHTML = `<svg viewBox="0 0 ${size} ${size}" style="width:${size}px;height:${size}px"><circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke="var(--border)"/><circle cx="${size / 2}" cy="${size / 2}" r="${r}" stroke="${scoreColor(v)}" stroke-dasharray="${c}" stroke-dashoffset="${c}" data-off="${off}" style="transition:stroke-dashoffset .9s cubic-bezier(.2,.8,.2,1)"/></svg><b style="font-size:${size < 40 ? 10.5 : 12.5}px">${Math.round(v)}</b>`;
    requestAnimationFrame(() => requestAnimationFrame(() => { const c2 = wrap.querySelectorAll("circle")[1]; if (c2) c2.style.strokeDashoffset = c2.dataset.off; }));
    return wrap;
  }
  function gauge(v) {
    const g = h("div", { class: "gauge" });
    const L = Math.PI * 100;
    g.innerHTML = `<svg viewBox="0 0 240 150"><defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stop-color="var(--bad)"/><stop offset=".5" stop-color="var(--warn)"/><stop offset="1" stop-color="var(--good)"/></linearGradient></defs>
      <path d="M20 130 A100 100 0 0 1 220 130" stroke="var(--panel-2)" stroke-linecap="round"/>
      <path d="M20 130 A100 100 0 0 1 220 130" stroke="url(#gg)" stroke-linecap="round" stroke-dasharray="${L}" stroke-dashoffset="${L}" id="gaugeArc" style="transition:stroke-dashoffset 1.2s cubic-bezier(.2,.8,.2,1)"/></svg>
      <div class="val"><b id="gaugeNum">0</b><span>Trust index</span></div>`;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const arc = g.querySelector("#gaugeArc"); if (arc) arc.style.strokeDashoffset = L * (1 - clamp(v, 0, 100) / 100);
      const num = g.querySelector("#gaugeNum"); const t0 = performance.now();
      const step = (t) => { const k = Math.min(1, (t - t0) / 1100); num.textContent = Math.round(v * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    }));
    return g;
  }
  const badge = (txt, cls) => h("span", { class: `badge ${cls || txt}` }, String(txt).replace(/_/g, " "));
  function explainList(items) {
    const ul = h("ul", { class: "explain" });
    for (const e of items || []) {
      const ch = e.trim()[0];
      const [cls, ic] = ch === "✓" ? ["ok", "✓"] : ch === "✗" ? ["no", "✕"] : ch === "△" ? ["meh", "!"] : ["nfo", "i"];
      ul.append(h("li", { class: cls }, h("span", { class: "ic" }, ic), h("span", { html: esc(e.replace(/^[✓✗△•]\s*/, "")).replace(/`([^`]+)`/g, "<code>$1</code>") })));
    }
    return ul;
  }
  function sectionTitle(text, extra) { return h("div", { class: "section-title" }, h("h2", null, text), h("div", { class: "line" }), extra || null); }
  function makeEditor(value, onInput, opts = {}) {
    const wrap = h("div", { class: `editor ${opts.tall ? "tall" : ""}`, style: opts.height ? { minHeight: opts.height } : null });
    const gutter = h("div", { class: "gutter" });
    const ta = h("textarea", { spellcheck: "false", placeholder: opts.placeholder || "", "aria-label": opts.label || "editor" });
    ta.value = value || "";
    const sync = () => { const n = ta.value.split("\n").length; gutter.textContent = Array.from({ length: n }, (_, i) => i + 1).join("\n"); gutter.scrollTop = ta.scrollTop; };
    ta.addEventListener("input", () => { sync(); onInput && onInput(ta.value); });
    ta.addEventListener("scroll", () => { gutter.scrollTop = ta.scrollTop; });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Tab") { e.preventDefault(); const s = ta.selectionStart; ta.setRangeText("    ", s, ta.selectionEnd, "end"); ta.dispatchEvent(new Event("input")); }
    });
    sync();
    wrap.append(gutter, ta);
    return wrap;
  }
  function empty(title, text, action) {
    return h("div", { class: "card empty" }, icon("shield"), h("h3", null, title), h("p", null, text), action || null);
  }

  // ==================================================================== WORKBENCH
  async function loadScenarios() {
    try { S.scenarios = await api("/api/v1/scenarios"); } catch (e) { toast(`Could not load scenarios: ${e.message}`, "bad"); }
  }
  async function selectScenario(id, silent) {
    try {
      const s = await api(`/api/v1/scenarios/${id}`);
      S.scenarioId = id;
      S.ws.files = { ...s.files };
      S.ws.activeFile = Object.keys(s.files).find((f) => !/__init__|test/.test(f) && /\.py$/.test(f)) || Object.keys(s.files)[0] || null;
      S.ws.requirements = (s.requirements[0] || {}).content || "";
      S.ws.review = s.review_text || "";
      S.ws.patches = (s.patches || []).map((p) => ({ fixes_tests: [], requirement_ids: [], original_code_snippet: "", ...p, _open: false, _preview: true }));
      S.ws.verifier = (s.verifier || []).map((v) => ({ counterfactual_verdict: null, ...v }));
      S.parsed = null;
      if (!silent) toast(`Loaded scenario: ${s.title}`, "good");
      if (route() === "workbench") render();
    } catch (e) { toast(e.message, "bad"); }
  }

  function viewWorkbench() {
    const root = h("div");
    root.append(h("div", { class: "page-head" },
      h("div", null, h("div", { class: "eyebrow" }, "Verification workbench"), h("h1", null, "Put AI code review on trial"),
        h("p", null, "Load a scenario or your own code, paste what the AI said and the patch it wrote, then let TRUST-CODE judge every word against deterministic evidence — AST, static analysis, sandboxed execution, executable specs and bounded proofs.")),
    ));

    const wb = h("div", { class: "wb" });
    // --- sidebar
    const side = h("div", { class: "card" },
      h("div", { class: "card-head" }, icon("sparkle"), h("h3", null, "Scenarios"), h("span", { class: "sub" }, `${S.scenarios.length} built-in`)));
    const list = h("div", { class: "scenario-list" });
    S.scenarios.forEach((s, i) => {
      list.append(h("div", { class: `scenario ${S.scenarioId === s.id ? "active" : ""}`, tabindex: "0", onclick: () => selectScenario(s.id), onkeydown: (e) => { if (e.key === "Enter") selectScenario(s.id); } },
        h("h4", null, h("span", { class: "num" }, String(i + 1).padStart(2, "0")), s.title),
        h("p", null, s.tagline),
        h("div", { class: "meta" },
          s.claims ? h("span", { class: "chip" }, `${s.claims} claims`) : null,
          s.patches ? h("span", { class: "chip" }, `${s.patches} patches`) : null,
          s.judged ? h("span", { class: "chip accent" }, "LLM judge") : null)));
    });
    side.append(list);
    const upload = h("input", { type: "file", multiple: true, class: "hidden", onchange: (e) => importFiles(e.target.files, false) });
    const uploadDir = h("input", { type: "file", multiple: true, webkitdirectory: true, class: "hidden", onchange: (e) => importFiles(e.target.files, true) });
    side.append(h("div", { class: "card-pad", style: { borderTop: "1px solid var(--border)" } },
      h("div", { class: "sub-title", style: { marginTop: 0 } }, "Your own code"),
      h("div", { class: "row wrap" },
        h("button", { class: "btn sm", onclick: () => uploadDir.click() }, icon("folder"), "Folder"),
        h("button", { class: "btn sm", onclick: () => upload.click() }, icon("upload"), "Files"),
        h("button", { class: "btn sm ghost", onclick: blankWorkspace }, icon("plus"), "Blank")),
      upload, uploadDir));
    wb.append(side);

    // --- main editor card
    const main = h("div");
    const card = h("div", { class: "card" });
    const tabs = [
      ["files", "Source files", Object.keys(S.ws.files).length],
      ["requirements", "Requirements", (S.ws.requirements.match(/^\s*#{0,6}\s*REQ-\d+/gim) || []).length],
      ["review", "AI review", (S.ws.review.match(/^\s*(?:[-*]\s*)?\[?[A-Z]{0,3}\d+\]?.*\.\w+[:#]/gm) || []).length],
      ["patches", "AI patches", S.ws.patches.length],
      ["judge", "LLM judge", S.ws.verifier.length],
      ["options", "Options", null],
    ];
    card.append(h("div", { class: "tabs", role: "tablist" }, tabs.map(([k, label, n]) => h("button", { class: `tab ${S.wbTab === k ? "active" : ""}`, role: "tab", onclick: () => { S.wbTab = k; render(); } }, label, n !== null ? h("span", { class: "count" }, n) : null))));
    const body = h("div", { class: "card-pad" });
    ({ files: wbFiles, requirements: wbRequirements, review: wbReview, patches: wbPatches, judge: wbJudge, options: wbOptions }[S.wbTab])(body);
    card.append(body);
    main.append(card);

    const nFiles = Object.keys(S.ws.files).length;
    main.append(h("div", { class: "action-bar" },
      h("div", null, h("div", { style: { fontWeight: 700 } }, S.scenarioId ? titleOf(S.scenarioId) : "Custom workspace"),
        h("div", { class: "muted", style: { fontSize: "12.5px" } }, `${nFiles} files · ${S.ws.patches.length} patches · ${S.ws.verifier.length} judge verdicts`)),
      h("div", { class: "spacer" }),
      S.report ? h("button", { class: "btn", onclick: () => (location.hash = "#/report") }, icon("eye"), "Last report") : null,
      h("button", { class: "btn primary big", id: "runBtn", disabled: S.running || !nFiles, onclick: runVerification }, icon("play"), "Run verification", h("span", { class: "kbd" }, "Ctrl ↵"))));
    wb.append(main);
    root.append(wb);
    return root;
  }

  function blankWorkspace() {
    S.scenarioId = null;
    S.ws = { files: { "app/main.py": "def add(a, b):\n    return a + b\n", "tests/test_main.py": "from app.main import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n" }, activeFile: "app/main.py", requirements: "# Requirements\n\n## REQ-001 · Addition\nadd must be commutative.\n- symbols: app.main.add\n- check: add(2, 3) == add(3, 2)\n- prove: add(a, b) => result == add(b, a)\n- domain: a = range(-20, 21); b = range(-20, 21)\n", review: "[C1] app/main.py:1-2 (Bug) `add` is recursive and never returns.\n", patches: [], verifier: [], options: { ...DEFAULT_OPTIONS } };
    S.parsed = null; S.wbTab = "files";
    render();
    toast("Blank workspace ready — edit the files or import your own.");
  }

  async function importFiles(fileList, isDir) {
    const ok = /\.(py|js|jsx|mjs|ts|tsx|java|go|md|txt|toml|cfg|ini|json|ya?ml)$/i;
    const files = {};
    let reqs = "";
    let skipped = 0;
    for (const f of Array.from(fileList)) {
      let path = (f.webkitRelativePath || f.name).replace(/\\/g, "/");
      if (isDir && path.includes("/")) path = path.split("/").slice(1).join("/");
      if (/(^|\/)(node_modules|\.git|__pycache__|\.venv|venv|dist|build)\//.test(path) || !ok.test(path) || f.size > 400000) { skipped++; continue; }
      const text = await f.text();
      if (/(requirement|spec|prd|srs)[^/]*\.(md|txt)$/i.test(path)) reqs += (reqs ? "\n\n" : "") + text;
      else files[path] = text;
    }
    if (!Object.keys(files).length) { toast("No supported source files found.", "warn"); return; }
    S.scenarioId = null;
    S.ws.files = files;
    S.ws.activeFile = Object.keys(files)[0];
    if (reqs) S.ws.requirements = reqs;
    S.ws.review = ""; S.ws.patches = []; S.ws.verifier = []; S.parsed = null;
    render();
    toast(`Imported ${Object.keys(files).length} files${skipped ? ` (${skipped} skipped)` : ""}${reqs ? " + requirements" : ""}.`, "good");
  }

  function wbFiles(body) {
    const names = Object.keys(S.ws.files).sort();
    if (!S.ws.activeFile || !(S.ws.activeFile in S.ws.files)) S.ws.activeFile = names[0] || null;
    const pane = h("div", { class: "files-pane" });
    const list = h("div", { class: "file-list" });
    for (const n of names) {
      list.append(h("div", { class: `file-item ${n === S.ws.activeFile ? "active" : ""}`, title: n, onclick: () => { S.ws.activeFile = n; render(); } },
        icon("file"), h("span", { style: { overflow: "hidden", textOverflow: "ellipsis" } }, n),
        h("span", { class: "x", title: "Delete file", onclick: (e) => { e.stopPropagation(); delete S.ws.files[n]; toast(`Deleted ${n}`); render(); } }, icon("trash"))));
    }
    list.append(h("button", { class: "btn sm ghost", style: { marginTop: "6px" }, onclick: () => promptNewFile() }, icon("plus"), "New file"));
    pane.append(list);
    if (S.ws.activeFile) {
      const f = S.ws.activeFile;
      pane.append(h("div", null,
        h("div", { class: "row", style: { marginBottom: "8px" } }, h("span", { class: "chip mono" }, f), h("span", { class: "spacer" }), h("span", { class: "faint", style: { fontSize: "12px" } }, `${S.ws.files[f].split("\n").length} lines`)),
        makeEditor(S.ws.files[f], (v) => { S.ws.files[f] = v; }, { tall: true, label: f })));
    } else pane.append(empty("No files", "Import a folder or create a file to begin."));
    body.append(pane);
  }
  function promptNewFile() {
    const inp = h("input", { class: "input mono", placeholder: "pkg/module.py", value: "" });
    const go = () => {
      const p = inp.value.trim().replace(/\\/g, "/").replace(/^\.?\//, "");
      if (!p || p.split("/").includes("..")) { toast("Enter a valid relative path", "warn"); return; }
      if (S.ws.files[p] !== undefined) { toast("File already exists", "warn"); return; }
      S.ws.files[p] = ""; S.ws.activeFile = p; closeModal(); render();
    };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    openModal("New file", h("div", { class: "stack" }, h("div", { class: "field" }, h("label", null, "Relative path"), inp), h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "btn primary", onclick: go }, "Create"))));
    setTimeout(() => inp.focus(), 50);
  }

  function wbRequirements(body) {
    body.append(h("div", { class: "row wrap", style: { marginBottom: "10px" } },
      h("p", { class: "muted", style: { margin: 0, flex: 1 } }, "Requirements become deterministic oracles. Under a ", h("code", null, "## REQ-###"), " heading add ", h("code", null, "symbols:"), ", ", h("code", null, "setup:"), ", ", h("code", null, "check:"), " (assertion), ", h("code", null, "forbid:"), " (static rule id) and ", h("code", null, "prove:"), " + ", h("code", null, "domain:"), " (bounded proof)."),
      h("button", { class: "btn sm", onclick: () => { S.ws.requirements += `\n\n## REQ-${String(100 + Math.floor(Math.random() * 800))} · New requirement\nDescribe the behaviour in prose.\n- symbols: package.module.function\n- check: function(1, 2) == 3\n- prove: function(a, b) => result >= 0\n- domain: a = range(0, 50); b = range(0, 50)\n`; render(); } }, icon("plus"), "Template")));
    body.append(makeEditor(S.ws.requirements, (v) => { S.ws.requirements = v; }, { tall: true, label: "requirements" }));
  }

  function wbReview(body) {
    body.append(h("div", { class: "row wrap", style: { marginBottom: "10px" } },
      h("p", { class: "muted", style: { margin: 0, flex: 1 } }, "Paste the AI reviewer's comments. One claim per line: ", h("code", null, "[C1] path/file.py:12-18 (Security) text…"), ". Continuation lines are appended to the previous claim."),
      h("button", { class: "btn sm", onclick: parsePreview }, icon("eye"), "Preview claims")));
    body.append(makeEditor(S.ws.review, (v) => { S.ws.review = v; S.parsed = null; }, { height: "300px", label: "review", placeholder: "[C1] shop/db.py:20-23 (Security) `find_user` builds SQL with an f-string…" }));
    const prev = h("div", { class: "claim-preview", id: "claimPreview" });
    body.append(prev);
    if (S.parsed) fillPreview(prev);
  }
  async function parsePreview() {
    try {
      S.parsed = await api("/api/v1/claims/parse", { body: { review_text: S.ws.review } });
      const prev = $("#claimPreview"); if (prev) fillPreview(prev);
      toast(`Parsed ${S.parsed.claims.length} claim(s)`, "good");
    } catch (e) { toast(e.message, "bad"); }
  }
  function fillPreview(prev) {
    prev.innerHTML = "";
    if (!S.parsed.claims.length) { prev.append(h("div", { class: "muted" }, "No claims found.")); return; }
    prev.append(h("div", { class: "sub-title" }, "Deconstructed claims (Step 3.1)"));
    for (const c of S.parsed.claims) {
      const ent = S.parsed.entities[c.claim_id] || {};
      prev.append(h("div", { class: "cp" },
        h("div", { class: "row wrap" }, h("span", { class: "item-id" }, c.claim_id), h("span", { class: "chip mono" }, `${c.target_file || "(inferred)"}:${c.target_line_start}-${c.target_line_end}`), h("span", { class: "chip accent" }, c.claimed_defect_category),
          (ent.tokens || []).map((t) => h("span", { class: "chip mono info" }, t)), (ent.parameters || []).map((t) => h("span", { class: "chip mono warn" }, `param ${t}`)), (ent.requirement_ids || []).map((t) => h("span", { class: "chip" }, t))),
        h("div", { class: "muted", style: { marginTop: "6px", fontSize: "12.5px" } }, "Counterfactual probe for your LLM judge: ", h("i", null, S.parsed.counterfactuals[c.claim_id]),
          h("button", { class: "btn sm ghost", style: { marginLeft: "6px" }, onclick: () => copyText(S.parsed.counterfactuals[c.claim_id], "Counterfactual copied") }, icon("copy")))));
    }
  }

  function newPatch() {
    const target = Object.keys(S.ws.files).find((f) => !/test/.test(f)) || "file.py";
    const first = (S.ws.files[target] || "").split("\n").slice(0, 3);
    S.ws.patches.push({ patch_id: `P-${Math.random().toString(16).slice(2, 6).toUpperCase()}`, target_file: target, original_code_snippet: "", intended_fix_description: "", fixes_tests: [], requirement_ids: [],
      suggested_diff: `--- a/${target}\n+++ b/${target}\n@@ -1,${first.length} +1,${first.length} @@\n${first.map((l) => " " + l).join("\n")}\n`, _open: true, _preview: false });
    render();
  }
  function wbPatches(body) {
    body.append(h("div", { class: "row", style: { marginBottom: "12px" } },
      h("p", { class: "muted", style: { margin: 0, flex: 1 } }, "Each AI patch is applied in an ephemeral sandbox, re-tested against the sealed baseline, re-scanned, probed for behavioural drift and mutation-tested."),
      h("button", { class: "btn sm", onclick: newPatch }, icon("plus"), "Add patch")));
    if (!S.ws.patches.length) { body.append(h("div", { class: "empty" }, icon("code"), h("h3", null, "No patches"), h("p", null, "Add a unified diff produced by your AI assistant."))); return; }
    const fileOpts = Object.keys(S.ws.files).sort();
    S.ws.patches.forEach((p, i) => {
      const card = h("div", { class: "patch-card" });
      card.append(h("div", { class: "ph", onclick: () => { p._open = !p._open; render(); } },
        h("span", { class: "item-id" }, p.patch_id), h("div", { style: { flex: 1, minWidth: 0 } }, h("div", { style: { fontWeight: 600 } }, p.intended_fix_description || "Untitled patch"), h("div", { class: "faint mono", style: { fontSize: "11.5px" } }, p.target_file)),
        h("button", { class: "btn sm ghost danger", onclick: (e) => { e.stopPropagation(); S.ws.patches.splice(i, 1); toast(`Removed ${p.patch_id}`); render(); } }, icon("trash")),
        icon("chev", "chev")));
      if (p._open) {
        const pb = h("div", { class: "pb stack" });
        const sel = h("select", { class: "input", onchange: (e) => { p.target_file = e.target.value; } }, fileOpts.map((f) => h("option", { value: f, selected: f === p.target_file ? "selected" : null }, f)));
        pb.append(h("div", { class: "grid cols-2" },
          h("div", { class: "field" }, h("label", null, "Intended fix (the AI's description)"), h("input", { class: "input", value: p.intended_fix_description, oninput: (e) => { p.intended_fix_description = e.target.value; } })),
          h("div", { class: "field" }, h("label", null, "Target file"), sel),
          h("div", { class: "field" }, h("label", null, "Tests it claims to fix (comma separated)"), h("input", { class: "input mono", value: (p.fixes_tests || []).join(", "), oninput: (e) => { p.fixes_tests = e.target.value.split(",").map((s) => s.trim()).filter(Boolean); } })),
          h("div", { class: "field" }, h("label", null, "Requirements it claims to satisfy"), h("input", { class: "input mono", value: (p.requirement_ids || []).join(", "), oninput: (e) => { p.requirement_ids = e.target.value.split(",").map((s) => s.trim()).filter(Boolean); } }))));
        pb.append(h("div", { class: "field" }, h("label", null, "Original code the AI quoted (optional — grounding check)"), h("input", { class: "input mono", value: p.original_code_snippet || "", oninput: (e) => { p.original_code_snippet = e.target.value; } })));
        pb.append(h("div", { class: "row" }, h("label", { class: "muted", style: { fontSize: "12px", fontWeight: 600 } }, "Unified diff"), h("span", { class: "spacer" }),
          h("div", { class: "seg" }, h("button", { class: p._preview ? "active" : "", onclick: () => { p._preview = true; render(); } }, "Preview"), h("button", { class: p._preview ? "" : "active", onclick: () => { p._preview = false; render(); } }, "Edit"))));
        pb.append(p._preview ? renderDiff(p.suggested_diff) : makeEditor(p.suggested_diff, (v) => { p.suggested_diff = v; }, { height: "260px", label: "diff" }));
        card.append(pb);
      }
      body.append(card);
    });
  }

  function wbJudge(body) {
    const ids = [...new Set((S.ws.review.match(/\[([A-Za-z]{0,4}-?\d+)\]/g) || []).map((m) => m.slice(1, -1)))];
    body.append(h("p", { class: "muted", style: { marginTop: 0 } }, "Optional: paste the verdicts an LLM-as-a-judge gave on the same claims. TRUST-CODE measures whether it repeats the generator's mistakes (correlated error) and whether it also agrees with the engine-generated negation of each claim (confirmation bias)."));
    const dl = h("datalist", { id: "claimIds" }, ids.map((i) => h("option", { value: i })));
    body.append(dl);
    body.append(h("div", { class: "judge-row faint", style: { fontSize: "11.5px", fontWeight: 700 } }, h("span", null, "Claim"), h("span", null, "Verdict"), h("span", null, "On negated claim"), h("span", null, "Rationale"), h("span")));
    S.ws.verifier.forEach((v, i) => {
      const verdictSel = (val, key, allowNone) => h("select", { class: "input", onchange: (e) => { v[key] = e.target.value || null; } },
        (allowNone ? [["", "— not asked —"]] : []).concat([["confirmed", "confirmed"], ["rejected", "rejected"], ["uncertain", "uncertain"]]).map(([k, l]) => h("option", { value: k, selected: (val || "") === k ? "selected" : null }, l)));
      body.append(h("div", { class: "judge-row" },
        h("input", { class: "input mono", list: "claimIds", value: v.claim_id, oninput: (e) => { v.claim_id = e.target.value.trim(); } }),
        verdictSel(v.verdict, "verdict", false), verdictSel(v.counterfactual_verdict, "counterfactual_verdict", true),
        h("input", { class: "input", value: v.rationale || "", placeholder: "Judge's rationale", oninput: (e) => { v.rationale = e.target.value; } }),
        h("button", { class: "icon-btn", title: "Remove", onclick: () => { S.ws.verifier.splice(i, 1); render(); } }, icon("trash"))));
    });
    body.append(h("div", { class: "row", style: { marginTop: "12px" } },
      h("button", { class: "btn sm", onclick: () => { S.ws.verifier.push({ claim_id: ids[S.ws.verifier.length] || "C1", verdict: "confirmed", counterfactual_verdict: null, rationale: "" }); render(); } }, icon("plus"), "Add verdict"),
      ids.length ? h("button", { class: "btn sm ghost", onclick: () => { S.ws.verifier = ids.map((id) => ({ claim_id: id, verdict: "confirmed", counterfactual_verdict: "confirmed", rationale: "Agreed with the reviewer." })); render(); toast("Simulated a rubber-stamping judge for every claim"); } }, icon("zap"), "Simulate a rubber-stamp judge") : null));
  }

  function wbOptions(body) {
    const o = S.ws.options;
    const sw = (key, label, desc) => h("div", { class: "row", style: { padding: "10px 0", borderBottom: "1px solid var(--border)" } },
      h("div", { style: { flex: 1 } }, h("div", { style: { fontWeight: 600 } }, label), h("div", { class: "muted", style: { fontSize: "12.5px" } }, desc)),
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: o[key], onchange: (e) => { o[key] = e.target.checked; } }), h("span", { class: "track" })));
    body.append(sw("run_tests", "Sandboxed test execution", "Baseline + post-patch suites with per-test line tracing (zero-regression gate)."));
    body.append(sw("run_mutation", "Mutation witness", "Mutate the patched lines and check whether any test notices."));
    body.append(sw("run_behavior_probe", "Differential behaviour probing", "Synthesise inputs and diff pre/post behaviour to surface novel bugs."));
    body.append(sw("run_proofs", "Bounded proofs", "Discharge prove: obligations exhaustively over their finite domains."));
    const val = h("b", null, String(o.max_mutants));
    body.append(h("div", { style: { padding: "12px 0" } }, h("div", { class: "row" }, h("div", { style: { fontWeight: 600, flex: 1 } }, "Max mutants per patch"), val),
      h("input", { type: "range", min: "2", max: "20", value: String(o.max_mutants), oninput: (e) => { o.max_mutants = +e.target.value; val.textContent = e.target.value; } })));
  }

  // ------------------------------------------------------------------ run
  function buildPayload() {
    return {
      workspace: { files: S.ws.files },
      requirements: S.ws.requirements.trim() ? [{ name: "REQUIREMENTS.md", content: S.ws.requirements }] : [],
      review_text: S.ws.review,
      patches: S.ws.patches.map(({ _open, _preview, ...p }) => p),
      verifier: S.ws.verifier.filter((v) => v.claim_id),
      options: S.ws.options,
    };
  }
  async function runVerification() {
    if (S.running) return;
    if (!Object.keys(S.ws.files).length) { toast("Add some source files first", "warn"); return; }
    S.running = true;
    const ov = $("#runOverlay");
    const stages = (S.health && S.health.stages) || [];
    $("#runStages").innerHTML = "";
    stages.forEach((s) => $("#runStages").append(h("li", { dataset: { key: s.key } }, h("span", { class: "node" }, icon("check")), s.label)));
    $("#runBar").style.width = "0%"; $("#runPct").textContent = "0%"; $("#runLog").textContent = ""; $("#runSub").textContent = "Gathering ground truth before a single claim is read.";
    ov.classList.remove("hidden");
    try {
      const { job_id } = await api("/api/v1/jobs", { body: buildPayload() });
      let job;
      for (;;) {
        await sleep(600);
        job = await api(`/api/v1/jobs/${job_id}?include_report=false`);
        $("#runBar").style.width = `${job.progress}%`; $("#runPct").textContent = `${job.progress}%`;
        $("#runSub").textContent = job.message || "working…";
        for (const st of job.stages) { const li = $(`#runStages li[data-key="${st.key}"]`); if (li) li.className = st.status; }
        $("#runLog").textContent = job.log.map((l) => `[${String(l.t.toFixed(1)).padStart(5)}s] ${l.stage.padEnd(9)} ${l.message}`).join("\n");
        $("#runLog").scrollTop = 1e9;
        if (job.status === "done" || job.status === "error") break;
      }
      if (job.status === "error") throw new Error(job.error || "verification failed");
      const full = await api(`/api/v1/jobs/${job_id}`);
      S.report = full.report; S.fmFilter = null; S.reportTab = S.report.claims.length ? "claims" : "patches"; S.explorer = { file: null, line: null };
      saveHistory(S.report);
      $("#runBar").style.width = "100%"; $("#runPct").textContent = "100%";
      await sleep(350);
      ov.classList.add("hidden");
      location.hash = "#/report";
      render();
      toast(`Verification complete in ${(S.report.duration_ms / 1000).toFixed(1)}s`, "good");
    } catch (e) {
      ov.classList.add("hidden");
      toast(`Verification failed: ${e.message}`, "bad", 6000);
    } finally {
      S.running = false;
      const b = $("#runBtn"); if (b) b.disabled = false;
    }
  }
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && route() === "workbench") { e.preventDefault(); runVerification(); }
    if (e.key === "Escape") { closeModal(); $("#historyDropdown").classList.remove("open"); }
  });

  // ==================================================================== REPORT
  function noReport() {
    return empty("No report yet", "Run a verification from the workbench — or try the full audit scenario.",
      h("div", { class: "row", style: { justifyContent: "center", marginTop: "14px" } },
        h("button", { class: "btn", onclick: () => (location.hash = "#/workbench") }, "Open workbench"),
        h("button", { class: "btn primary", onclick: async () => { await selectScenario("full-audit", true); location.hash = "#/workbench"; runVerification(); } }, icon("play"), "Run full audit")));
  }
  function viewReport() {
    const R = S.report;
    if (!R) return noReport();
    const root = h("div");
    const s = R.summary;
    root.append(h("div", { class: "page-head" },
      h("div", null, h("div", { class: "eyebrow" }, `Report ${R.report_id}`), h("h1", null, "Trust verdict"),
        h("p", null, `${R.engine} · ${new Date(R.created_at * 1000).toLocaleString()} · ${(R.duration_ms / 1000).toFixed(1)}s · ${R.workspace.files.length} files / ${R.workspace.loc} LOC · sandbox ${R.capabilities.sandbox}`)),
      h("div", { class: "spacer" }),
      h("div", { class: "row wrap" },
        h("button", { class: "btn", onclick: () => download(`${R.report_id}.json`, JSON.stringify(R, null, 2), "application/json") }, icon("download"), "JSON"),
        h("button", { class: "btn", onclick: exportMarkdown }, icon("download"), "Markdown"),
        h("button", { class: "btn", onclick: () => copyText(summaryText(R), "Summary copied") }, icon("copy"), "Copy summary"),
        h("button", { class: "btn primary", onclick: () => { location.hash = "#/workbench"; setTimeout(runVerification, 50); } }, icon("refresh"), "Re-run"))));

    const hero = h("div", { class: "card hero" }, gauge(s.trust_index),
      h("div", null, h("div", { class: "headline" }, s.headline),
        h("div", { class: "kpis" },
          kpi("Trusted", s.trusted, "var(--good)"), kpi("Uncertain", s.uncertain, "var(--warn)"), kpi("Rejected", s.rejected, "var(--bad)"), kpi("Hard gates", s.hard_gates, "var(--accent)")),
        R.warnings.length ? h("div", { class: "row wrap", style: { marginTop: "12px" } }, R.warnings.slice(0, 3).map((w) => h("span", { class: "chip warn", title: w }, icon("alert"), w.length > 70 ? w.slice(0, 70) + "…" : w))) : null,
        h("div", { class: "row wrap", style: { marginTop: "12px" } },
          h("span", { class: `chip ${R.ledger.intact ? "good" : "bad"}` }, icon("lock"), `Ledger ${R.ledger.intact ? "intact" : "broken"} · seal ${String(R.ledger.seal_hash || "").slice(0, 10)}…`),
          h("span", { class: "chip" }, `SAST: ${R.capabilities.sast.join(" + ")}`),
          h("span", { class: "chip" }, `Tests: ${R.baseline_tests.framework}`),
          h("span", { class: "chip" }, `Retrieval: ${R.capabilities.retrieval}`))));
    root.append(hero);

    root.append(sectionTitle("Failure-mode detection", S.fmFilter ? h("button", { class: "btn sm", onclick: () => { S.fmFilter = null; render(); } }, icon("x"), "Clear filter") : h("span", { class: "faint", style: { fontSize: "12px" } }, "click a tile to filter")));
    const fm = h("div", { class: "fm-grid" });
    for (const row of R.failure_modes) {
      fm.append(h("div", { class: `fm ${row.detected ? "on" : ""} ${S.fmFilter === row.key ? "sel" : ""}`, title: row.refs.join(", "), onclick: () => { S.fmFilter = S.fmFilter === row.key ? null : row.key; if (S.fmFilter) { const hasC = row.refs.some((r) => r.startsWith("claim:")); const hasP = row.refs.some((r) => r.startsWith("patch:")); if (hasC && !hasP) S.reportTab = "claims"; else if (hasP && !hasC) S.reportTab = "patches"; } render(); } },
        h("div", { class: "t" }, row.label), h("div", { class: "d" }, row.description), h("span", { class: "n" }, row.detected)));
    }
    root.append(fm);

    const tabs = [["claims", "Review claims", R.claims.length], ["patches", "AI patches", R.patches.length], ["judge", "LLM judge audit", R.verifier_audit ? R.verifier_audit.metrics.judged : 0], ["requirements", "Requirements", R.requirements.length], ["explorer", "Code explorer", R.workspace.files.length], ["graph", "Call graph", R.call_graph.nodes.length]];
    const card = h("div", { class: "card", style: { marginTop: "22px" } });
    card.append(h("div", { class: "tabs" }, tabs.map(([k, l, n]) => h("button", { class: `tab ${S.reportTab === k ? "active" : ""}`, onclick: () => { S.reportTab = k; render(); } }, l, h("span", { class: "count" }, n)))));
    const body = h("div", { class: "card-pad" });
    ({ claims: repClaims, patches: repPatches, judge: repJudge, requirements: repRequirements, explorer: repExplorer, graph: repGraph }[S.reportTab])(body, R);
    card.append(body);
    root.append(card);
    return root;
  }
  function kpi(label, v, color) { return h("div", { class: "kpi" }, h("div", { class: "k" }, h("span", { class: "dotc", style: { background: color } }), label), h("div", { class: "v" }, v)); }
  function summaryText(R) {
    const lines = [`TRUST-CODE ${R.report_id} — trust index ${R.summary.trust_index.toFixed(1)}/100`, R.summary.headline, ""];
    for (const c of R.claims) lines.push(`${c.score.verdict.padEnd(9)} ${String(Math.round(c.score.final)).padStart(3)}  ${c.claim.claim_id}  ${c.state}  ${c.claim.claim_text.slice(0, 90)}`);
    for (const p of R.patches) lines.push(`${p.score.verdict.padEnd(9)} ${String(Math.round(p.score.final)).padStart(3)}  ${p.patch.patch_id}  ${p.score.gates.filter((g) => g.triggered).map((g) => g.gate).join(", ") || "no gates"}`);
    return lines.join("\n");
  }
  async function exportMarkdown() {
    try { const md = await api("/api/v1/report/markdown", { body: S.report }); download(`${S.report.report_id}.md`, md, "text/markdown"); toast("Markdown report downloaded", "good"); }
    catch (e) { toast(e.message, "bad"); }
  }
  const matchesFilter = (ref, modes) => !S.fmFilter || (modes || []).includes(S.fmFilter);

  // ---------------------------------------------------------------- claims
  function repClaims(body, R) {
    if (!R.claims.length) { body.append(h("div", { class: "empty" }, icon("info"), h("h3", null, "No review claims in this run"), h("p", null, "Paste AI review comments in the workbench to verify them."))); return; }
    const counts = {};
    R.claims.forEach((c) => (counts[c.state] = (counts[c.state] || 0) + 1));
    body.append(h("div", { class: "row wrap", style: { marginBottom: "14px" } }, ["SUPPORTED", "UNSUPPORTED", "CONTRADICTED", "HALLUCINATED"].map((st) => h("span", { class: `badge ${st}` }, `${counts[st] || 0} ${st}`)),
      h("span", { class: "spacer" }), h("button", { class: "btn sm ghost", onclick: () => $$(".item", body).forEach((i) => i.classList.add("open")) }, "Expand all"), h("button", { class: "btn sm ghost", onclick: () => $$(".item", body).forEach((i) => i.classList.remove("open")) }, "Collapse all")));
    for (const c of R.claims) body.append(claimItem(c, R));
    if (S.openAll) { $$(".item", body).slice(0, 2).forEach((i) => i.classList.add("open")); }
  }
  function claimItem(c, R) {
    const item = h("div", { class: `item ${matchesFilter(null, c.failure_modes) ? "" : "dim"}` });
    const cl = c.claim;
    item.append(h("div", { class: "item-head", onclick: () => item.classList.toggle("open") },
      h("span", { class: "item-id" }, cl.claim_id),
      h("div", { class: "item-title" }, h("div", { class: "txt", title: cl.claim_text }, cl.claim_text), h("div", { class: "loc" }, `${cl.target_file || "?"}:${cl.target_line_start}-${cl.target_line_end} · ${cl.claimed_defect_category}`)),
      h("div", { class: "scorebox" }, badge(c.state), badge(c.score.verdict), scoreRing(c.score.final), icon("chev", "chev"))));
    const b = h("div", { class: "item-body" });
    b.append(h("div", { class: "row wrap" },
      c.symbols.map((s) => h("span", { class: `chip mono ${s.status === "hallucinated" ? "bad" : s.status === "resolved_elsewhere" ? "warn" : s.status === "resolved" ? "good" : "info"}`, title: `${s.status}${s.note ? " — " + s.note : ""}${s.suggestions.length ? " · did you mean " + s.suggestions.join(", ") : ""}` }, s.role === "parameter" ? "param " : "", s.token, s.status === "hallucinated" ? " ✕" : "")),
      c.evidence_kinds.map((k) => h("span", { class: "chip accent" }, k)),
      h("span", { class: "spacer" }),
      cl.target_file && R.files[cl.target_file] !== undefined ? h("button", { class: "btn sm", onclick: () => { S.explorer = { file: cl.target_file, line: cl.target_line_start }; S.reportTab = "explorer"; render(); } }, icon("code"), "Show in code") : null));
    b.append(h("div", { class: "sub-title" }, "Why this verdict"), explainList(c.explanations));
    const grid = h("div", { class: "grid cols-2", style: { marginTop: "6px" } });
    grid.append(h("div", null, h("div", { class: "sub-title" }, "Trust score (Step 5.1)"), waterfall(c.score)));
    const right = h("div");
    if (c.predicates.length) {
      right.append(h("div", { class: "sub-title" }, "Semantic predicates"), h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Predicate", "Subject", "Claim", "Fact", "Outcome"].map((t) => h("th", null, t)))),
        h("tbody", null, c.predicates.map((p) => h("tr", { title: p.evidence }, h("td", null, p.label), h("td", { class: "mono" }, p.subject || "—"), h("td", null, p.asserted === null ? "—" : String(p.asserted)), h("td", null, p.actual === null ? "?" : String(p.actual)),
          h("td", null, h("span", { class: `chip ${p.outcome === "corroborated" ? "good" : p.outcome === "refuted" ? "bad" : "warn"}` }, p.outcome.replace("_", " ")))))))));
    }
    if (c.static_evidence.length) right.append(h("div", { class: "sub-title" }, "Static evidence (±5 lines)"), findingsTable(c.static_evidence));
    if (c.requirement_links.length) right.append(h("div", { class: "sub-title" }, "Requirement links"), h("div", { class: "row wrap" }, c.requirement_links.map((l) => h("span", { class: `chip ${l.status === "PASS" ? "good" : l.status === "FAIL" ? "bad" : "warn"}`, title: `${l.title} · similarity ${l.similarity}` }, `${l.req_id} ${l.status}${l.explicit ? " (cited)" : ""}`))));
    if (c.proofs.length) right.append(h("div", { class: "sub-title" }, "Proof certificates"), h("div", { class: "stack" }, c.proofs.map(certCard)));
    if (!right.childNodes.length) right.append(h("div", { class: "sub-title" }, "Evidence"), h("div", { class: "muted" }, "No predicate, finding, requirement or proof speaks to this claim."));
    grid.append(right);
    b.append(grid);
    b.append(h("div", { class: "sub-title" }, "Counterfactual probe"), h("div", { class: "row", style: { padding: "10px 12px", border: "1px dashed var(--border-2)", borderRadius: "10px" } },
      h("i", { style: { flex: 1 } }, c.counterfactual_probe), h("button", { class: "btn sm", onclick: () => copyText(c.counterfactual_probe, "Counterfactual copied") }, icon("copy"), "Copy")));
    if (c.verifier) {
      const v = c.verifier;
      b.append(h("div", { class: "sub-title" }, "LLM judge on this claim"), h("div", { class: "row wrap" },
        h("span", { class: `chip ${v.correct === false ? "bad" : v.correct ? "good" : "warn"}` }, `judge said ${v.verdict}${v.counterfactual_verdict ? ` · negation: ${v.counterfactual_verdict}` : ""}`),
        h("span", { class: "chip" }, `echo ${Math.round(v.echo_ratio * 100)}%`), h("span", { class: "chip" }, `${v.evidence_anchors} evidence anchors`),
        v.flags.map((f) => h("span", { class: "chip bad", title: f.detail }, icon("alert"), f.label))));
    }
    item.append(b);
    return item;
  }
  function waterfall(score) {
    const box = h("div", { class: "waterfall" });
    const row = (label, val, left, width, color, txt) => h("div", { class: "wf-row" }, h("span", { class: "muted" }, label), h("div", { class: "wf-track" }, h("div", { class: "wf-bar", style: { left: `${left}%`, width: `${width}%`, background: color } })), h("b", { style: { textAlign: "right", fontVariantNumeric: "tabular-nums" } }, txt ?? val));
    let cur = score.base;
    box.append(row("Base S₀", score.base, 0, score.base, "var(--info)", fmtNum(score.base)));
    for (const c of score.components) {
      if (!c.count) continue;
      const next = cur + c.delta;
      const lo = clamp(Math.min(cur, next), 0, 100), hi = clamp(Math.max(cur, next), 0, 100);
      box.append(row(`${c.key} ×${c.count}`, c.delta, lo, Math.max(1.5, hi - lo), c.delta >= 0 ? "var(--good)" : "var(--bad)", `${c.delta >= 0 ? "+" : ""}${fmtNum(c.delta)}`));
      cur = next;
    }
    box.append(row("Final", score.final, 0, score.final, scoreColor(score.final), fmtNum(score.final)));
    const gates = score.gates.filter((g) => g.gate);
    if (gates.length) box.append(h("div", { class: "row wrap", style: { marginTop: "8px" } }, gates.map((g) => h("span", { class: `gate ${g.triggered ? "on" : "off"}`, title: g.reason }, g.triggered ? icon("x") : icon("check"), g.gate))));
    box.append(h("div", { class: "faint mono", style: { fontSize: "11px", marginTop: "6px" } }, `raw ${fmtNum(score.raw, 1)} → ${score.gate_triggered ? "hard gate ⇒ 0" : "clamp[0,100]"} → ${score.verdict}`));
    return box;
  }
  function findingsTable(list) {
    return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["Tool", "Rule", "Sev", "CWE", "Location", "Message"].map((t) => h("th", null, t)))),
      h("tbody", null, list.map((f) => h("tr", null, h("td", null, h("span", { class: "chip" }, f.tool)), h("td", { class: "mono" }, f.rule_id),
        h("td", null, h("span", { class: `chip ${f.severity === "HIGH" ? "bad" : f.severity === "MEDIUM" ? "warn" : "info"}` }, f.severity)), h("td", { class: "mono" }, f.cwe || "—"),
        h("td", { class: "mono" }, `${f.file}:${f.line}`), h("td", null, f.message))))));
  }
  function certCard(c) {
    return h("div", { class: `cert ${c.status}` },
      h("div", { class: "row wrap" }, badge(c.status), c.req_id ? h("span", { class: "chip" }, c.req_id) : null, h("span", { class: "chip mono" }, c.function), h("span", { class: "spacer" }), h("span", { class: "chip" }, `${(c.cases_checked || 0).toLocaleString()} / ${(c.domain_size || 0).toLocaleString()} cases${c.exhaustive ? " · exhaustive" : ""}`)),
      h("div", { class: "mono", style: { margin: "10px 0 4px", fontSize: "12.5px" } }, c.property),
      h("div", { class: "muted mono", style: { fontSize: "11.5px" } }, `domain: ${c.domain}${c.precondition ? ` · requires: ${c.precondition}` : ""}`),
      c.counterexample ? h("div", { style: { marginTop: "8px" } }, h("span", { class: "chip bad" }, "counterexample"), " ", h("code", null, JSON.stringify(c.counterexample.inputs || c.counterexample)), c.counterexample.result !== undefined ? h("span", { class: "muted" }, ` → ${c.counterexample.result}`) : null, c.counterexample.raised ? h("span", { class: "muted" }, ` → ${c.counterexample.raised}`) : null) : null,
      h("p", { class: "muted", style: { fontSize: "12.5px", margin: "8px 0 6px" } }, c.disclaimer || c.detail),
      h("div", { class: "hash" }, `certificate sha256 ${c.certificate_hash}`));
  }

  // ---------------------------------------------------------------- patches
  function repPatches(body, R) {
    if (!R.patches.length) { body.append(h("div", { class: "empty" }, icon("info"), h("h3", null, "No patches in this run"), h("p", null, "Add AI-generated diffs in the workbench to validate them."))); return; }
    for (const p of R.patches) body.append(patchItem(p, R));
    if (S.openAll) { const first = $(".item", body); if (first) first.classList.add("open"); }
  }
  function patchItem(p, R) {
    const item = h("div", { class: `item ${matchesFilter(null, p.failure_modes) ? "" : "dim"}` });
    const trig = p.score.gates.filter((g) => g.triggered);
    item.append(h("div", { class: "item-head", onclick: () => item.classList.toggle("open") },
      h("span", { class: "item-id" }, p.patch.patch_id),
      h("div", { class: "item-title" }, h("div", { class: "txt" }, p.patch.intended_fix_description || "AI patch"), h("div", { class: "loc" }, `${p.patch.target_file} · ${p.application.applied ? `${Object.values(p.application.changed_lines).flat().length} lines added` : "not applied"}`)),
      h("div", { class: "scorebox" }, trig.slice(0, 2).map((g) => h("span", { class: "gate on", title: g.reason }, icon("x"), g.gate)), badge(p.score.verdict), scoreRing(p.score.final), icon("chev", "chev"))));
    const b = h("div", { class: "item-body" });
    const views = [["summary", "Summary"], ["diff", "Diff"], ["tests", `Tests${p.tests.regressions.length ? " ⚠" : ""}`], ["reqs", "Requirements"], ["adequacy", "Test adequacy"], ["behavior", `Behaviour${p.behavior.novel_bug_count ? " ⚠" : ""}`], ["security", `Security Δ${p.static_new.some((f) => f.security) ? " ⚠" : ""}`], ["blast", "Blast radius"]];
    let cur = "summary";
    const seg = h("div", { class: "seg", style: { flexWrap: "wrap" } });
    const pane = h("div", { style: { marginTop: "14px" } });
    const show = (k) => { cur = k; $$("button", seg).forEach((x) => x.classList.toggle("active", x.dataset.k === k)); pane.innerHTML = ""; pane.append(patchPane(k, p, R)); };
    views.forEach(([k, l]) => seg.append(h("button", { dataset: { k }, onclick: () => show(k) }, l)));
    b.append(seg, pane);
    item.append(b);
    show(cur);
    return item;
  }
  function patchPane(k, p, R) {
    const box = h("div");
    if (k === "summary") {
      box.append(h("div", { class: "grid cols-2" },
        h("div", null, h("div", { class: "sub-title", style: { marginTop: 0 } }, "Why this verdict"), explainList(p.explanations)),
        h("div", null, h("div", { class: "sub-title", style: { marginTop: 0 } }, "Trust score & hard gates (Steps 5.1–5.2)"), waterfall(p.score),
          h("div", { class: "sub-title" }, "Application (Step 4.2)"), h("div", { class: "row wrap" },
            h("span", { class: `chip ${p.application.applied ? "good" : "bad"}` }, p.application.applied ? "applied" : "rejected"),
            h("span", { class: "chip" }, p.application.method),
            p.application.git_check ? h("span", { class: `chip ${p.application.git_check === "ok" ? "good" : "warn"}`, title: p.application.git_check }, `git apply --check: ${p.application.git_check === "ok" ? "ok" : "rejects"}`) : null,
            p.application.snippet_found === false ? h("span", { class: "chip bad" }, "quoted snippet not found") : p.application.snippet_found ? h("span", { class: "chip good" }, "quoted snippet grounded") : null,
            p.syntax_ok ? null : h("span", { class: "chip bad" }, "syntax error")),
          p.application.hunks.length ? h("div", { class: "tbl-wrap", style: { marginTop: "8px" } }, h("table", { class: "tbl" }, h("tbody", null, p.application.hunks.map((hk) => h("tr", null, h("td", { class: "mono" }, hk.header), h("td", null, h("span", { class: `chip ${hk.status === "applied" ? "good" : hk.status === "offset" ? "warn" : "bad"}` }, hk.status)), h("td", { class: "muted" }, hk.note)))))) : null,
          p.introduced_symbols.length ? h("div", null, h("div", { class: "sub-title" }, "Symbols the fix invented"), h("div", { class: "row wrap" }, p.introduced_symbols.map((s) => h("span", { class: "chip bad mono", title: s.detail }, `${s.name} (line ${s.line})`)))) : null)));
    } else if (k === "diff") {
      box.append(renderDiff(p.patch.suggested_diff));
    } else if (k === "tests") {
      const t = p.tests;
      if (!t.ran) { box.append(h("div", { class: "muted" }, "Tests were not executed for this patch.")); return box; }
      box.append(h("div", { class: "row wrap", style: { marginBottom: "10px" } },
        h("span", { class: "chip" }, `baseline ${t.baseline.passed}/${t.baseline.total} passing`), h("span", { class: "chip" }, `post ${t.post.passed}/${t.post.total} passing`),
        h("span", { class: `chip ${t.regressions.length ? "bad" : "good"}` }, `${t.regressions.length} regressions`), h("span", { class: `chip ${t.fixed.length ? "good" : ""}` }, `${t.fixed.length} fixed`),
        t.targets_requested.length ? h("span", { class: `chip ${t.targets_unsatisfied.length ? "bad" : "good"}` }, `targets ${t.targets_satisfied.length}/${t.targets_requested.length}`) : null));
      const order = { regression: 0, removed: 1, new_fail: 2, fixed: 3, stable_fail: 4, new_pass: 5, stable_pass: 6, skipped: 7 };
      box.append(h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Test", "Baseline", "After patch", "Transition"].map((x) => h("th", null, x)))),
        h("tbody", null, [...t.matrix].sort((a, b2) => order[a.transition] - order[b2.transition]).map((m) => h("tr", { class: m.transition === "regression" ? "hi" : "", title: m.message },
          h("td", { class: "mono" }, m.test_id), h("td", null, outcomeChip(m.before)), h("td", null, outcomeChip(m.after)),
          h("td", null, h("span", { class: `chip ${["regression", "removed", "new_fail"].includes(m.transition) ? "bad" : m.transition === "fixed" ? "good" : m.transition === "stable_fail" ? "warn" : ""}` }, m.transition.replace("_", " ")))))))));
    } else if (k === "reqs") {
      if (!p.requirements.length) { box.append(h("div", { class: "muted" }, "No executable requirements.")); return box; }
      box.append(h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Requirement", "Linked", "Before", "After", "Transition", "Failing clauses"].map((x) => h("th", null, x)))),
        h("tbody", null, p.requirements.map((r) => h("tr", { class: r.linked ? "hi" : "" }, h("td", null, h("b", null, r.req_id), " ", h("span", { class: "muted" }, r.title)), h("td", null, r.linked ? "yes" : ""), h("td", null, badge(r.before)), h("td", null, badge(r.after)),
          h("td", null, h("span", { class: `chip ${r.transition === "regression" || r.transition === "still_failing" ? "bad" : r.transition === "fixed" ? "good" : ""}` }, r.transition.replace("_", " "))),
          h("td", { class: "muted", style: { fontSize: "12px" } }, r.details.join(" · ") || "—")))))));
      if (p.proofs.length) box.append(h("div", { class: "sub-title" }, "Proof certificates after the patch"), h("div", { class: "grid cols-2" }, p.proofs.map(certCard)));
    } else if (k === "adequacy") {
      const a = p.adequacy;
      if (a.verdict === "not_applicable") { box.append(h("div", { class: "muted" }, a.note || "Not applicable.")); return box; }
      box.append(h("div", { class: "grid cols-3" },
        h("div", { class: "kpi meter" }, h("div", { class: "k" }, "Changed-line coverage"), h("div", { class: "mv" }, fmtPct(a.coverage_ratio)), h("div", { class: "cov" }, h("div", { style: { width: `${(a.coverage_ratio || 0) * 100}%` } }))),
        h("div", { class: "kpi meter" }, h("div", { class: "k" }, "Mutation score"), h("div", { class: "mv" }, fmtPct(a.mutation_score)), h("div", { class: "cov" }, h("div", { style: { width: `${(a.mutation_score || 0) * 100}%` } }))),
        h("div", { class: "kpi meter" }, h("div", { class: "k" }, "Verdict"), h("div", { class: "mv", style: { color: a.verdict === "adequate" ? "var(--good)" : "var(--bad)" } }, a.verdict), h("div", { class: "muted", style: { fontSize: "12px" } }, `${a.covering_tests.length} covering test(s)`))));
      box.append(h("p", { class: "muted" }, a.note));
      box.append(h("div", { class: "row wrap" }, h("span", { class: "faint", style: { fontSize: "12px" } }, "Changed executable lines:"), a.changed_executable_lines.map((l) => h("span", { class: `chip mono ${a.covered_lines.includes(l) ? "good" : "bad"}` }, `L${l}`))));
      if (a.mutants.length) box.append(h("div", { class: "sub-title" }, "Mutation witness"), h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Mutant", "Line", "Operator", "Mutation", "Result", "Killed by"].map((x) => h("th", null, x)))),
        h("tbody", null, a.mutants.map((m) => h("tr", null, h("td", { class: "mono" }, m.mutant_id), h("td", { class: "mono" }, m.line), h("td", null, m.operator), h("td", { class: "mono" }, `${m.original}  →  ${m.mutated}`), h("td", null, badge(m.status)), h("td", { class: "mono muted", style: { fontSize: "11.5px" } }, m.killed_by.map((x) => x.split("::").pop()).join(", ") || "—")))))));
    } else if (k === "behavior") {
      const bp = p.behavior;
      if (!bp.functions.length) { box.append(h("div", { class: "muted" }, "No module-level functions changed — nothing to probe.")); return box; }
      box.append(h("div", { class: "row wrap", style: { marginBottom: "10px" } }, h("span", { class: `chip ${bp.novel_bug_count ? "bad" : "good"}` }, `${bp.novel_bug_count} unexplained divergence(s)`), h("span", { class: "chip" }, `${bp.intended_changes} intended change(s)`)));
      for (const f of bp.functions) {
        box.append(h("div", { class: "sub-title" }, `${f.qualified_name} — ${f.cases_tried} synthesised inputs`));
        if (f.skipped_reason) { box.append(h("div", { class: "muted" }, `skipped: ${f.skipped_reason}`)); continue; }
        if (!f.divergences.length) { box.append(h("div", { class: "chip good" }, "identical behaviour on every probed input")); continue; }
        const rank = { new_crash: 0, new_hang: 0, silent_value_change: 1, exception_changed: 2, validation_added: 3, crash_fixed: 4 };
        box.append(h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
          h("thead", null, h("tr", null, ["Input", "Before", "After", "Kind"].map((x) => h("th", null, x)))),
          h("tbody", null, [...f.divergences].sort((a, b2) => rank[a.kind] - rank[b2.kind]).map((d) => h("tr", { class: rank[d.kind] <= 2 ? "hi" : "" }, h("td", { class: "mono" }, d.input), h("td", { class: "mono" }, d.before), h("td", { class: "mono" }, d.after),
            h("td", null, h("span", { class: `chip ${rank[d.kind] <= 2 ? "bad" : "good"}` }, d.kind.replace(/_/g, " ")))))))));
      }
    } else if (k === "security") {
      box.append(h("div", { class: "sub-title", style: { marginTop: 0 } }, `New findings (Δ = post − pre): ${p.static_new.length}`));
      box.append(p.static_new.length ? findingsTable(p.static_new) : h("div", { class: "chip good" }, "no new findings"));
      box.append(h("div", { class: "sub-title" }, `Resolved findings: ${p.static_resolved.length}`));
      box.append(p.static_resolved.length ? findingsTable(p.static_resolved) : h("div", { class: "muted" }, "none"));
    } else if (k === "blast") {
      const br = p.blast_radius;
      box.append(h("div", { class: "row wrap" }, h("span", { class: "faint" }, "Changed:"), br.changed_functions.map((f) => h("span", { class: "chip mono bad" }, f))));
      box.append(br.impacted.length ? h("div", { class: "tbl-wrap", style: { marginTop: "10px" } }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["Impacted caller", "Call distance", "Covered by tests"].map((x) => h("th", null, x)))),
        h("tbody", null, br.impacted.map((i) => h("tr", null, h("td", { class: "mono" }, i.qualified_name), h("td", null, i.distance), h("td", null, h("span", { class: `chip ${i.covered_by_tests ? "good" : "bad"}` }, i.covered_by_tests ? "yes" : "no"))))))) : h("p", { class: "muted" }, "No callers are affected."));
      box.append(h("button", { class: "btn sm", style: { marginTop: "10px" }, onclick: () => { S.reportTab = "graph"; S.graph.focusPatch = p.patch.patch_id; render(); } }, icon("graph"), "Show on call graph"));
    }
    return box;
  }
  const outcomeChip = (o) => h("span", { class: `chip ${o === "passed" ? "good" : o === "failed" || o === "error" ? "bad" : o === "missing" ? "warn" : ""}` }, o);

  // ---------------------------------------------------------------- judge audit
  function repJudge(body, R) {
    const A = R.verifier_audit;
    if (!A) { body.append(h("div", { class: "empty" }, icon("info"), h("h3", null, "No LLM-judge transcript"), h("p", null, "Add verdicts in the workbench's “LLM judge” tab (or load the verifier scenarios) to audit an LLM-as-a-judge for correlated errors and confirmation bias."))); return; }
    const m = A.metrics;
    body.append(h("div", { class: "card-pad", style: { borderRadius: "12px", background: m.independence_index !== null && m.independence_index < .5 ? "var(--bad-bg)" : "var(--good-bg)", marginBottom: "14px" } }, h("b", null, A.conclusion)));
    body.append(h("div", { class: "grid cols-4" },
      kpiMeter("Judge accuracy", fmtPct(m.accuracy), "vs deterministic ground truth"),
      kpiMeter("Same-mistake rate", fmtPct(m.same_mistake_rate), "generator errors the judge confirmed"),
      kpiMeter("Independence index", m.independence_index === null ? "—" : m.independence_index.toFixed(2), `1 − φ (φ = ${m.error_correlation_phi ?? "—"})`),
      kpiMeter("Polarity-insensitive", fmtPct(m.polarity_insensitive_rate), "also confirmed the negated claim")));
    const c = m.confusion;
    body.append(h("div", { class: "grid cols-2", style: { marginTop: "16px" } },
      h("div", null, h("div", { class: "sub-title" }, "Judge vs ground truth"), h("div", { class: "confusion" },
        h("div", { class: "h" }), h("div", { class: "h" }, "Claim true (SUPPORTED)"), h("div", { class: "h" }, "Claim false (HALLUC./CONTRA.)"),
        h("div", { class: "h" }, "Judge confirmed"), h("div", { class: "cgood" }, c.confirmed_true || 0), h("div", { class: "cbad" }, c.confirmed_false || 0),
        h("div", { class: "h" }, "Judge rejected"), h("div", { class: "cbad" }, c.rejected_true || 0), h("div", { class: "cgood" }, c.rejected_false || 0)),
        h("p", { class: "muted", style: { fontSize: "12.5px" } }, `Confirm rate ${fmtPct(m.confirm_rate)} vs true-claim rate ${fmtPct(m.true_support_rate)} → agreement inflation ${fmtPct(m.agreement_inflation)}. ${c.confirmed_unverifiable || 0} unverifiable claim(s) were confirmed anyway.`)),
      h("div", null, h("div", { class: "sub-title" }, "Why TRUST-CODE is independent"), h("ul", { class: "explain" }, A.engine_heterogeneity.map((x) => h("li", { class: "ok" }, h("span", { class: "ic" }, "✓"), x))))));
    body.append(h("div", { class: "sub-title" }, "Per-claim audit"), h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["Claim", "Ground truth", "Judge", "Negation", "Echo", "Flags", "Rationale"].map((x) => h("th", null, x)))),
      h("tbody", null, A.entries.map((e) => h("tr", { class: e.correct === false ? "hi" : "" }, h("td", { class: "mono" }, e.claim_id), h("td", null, badge(e.ground_truth)), h("td", null, e.verdict), h("td", null, e.counterfactual_verdict || "—"),
        h("td", null, `${Math.round(e.echo_ratio * 100)}%`), h("td", null, h("div", { class: "row wrap" }, e.flags.map((f) => h("span", { class: "chip bad", title: f.detail }, f.label)))), h("td", { class: "muted", style: { fontSize: "12px", maxWidth: "320px" } }, e.rationale)))))));
  }
  const kpiMeter = (label, v, sub) => h("div", { class: "kpi meter" }, h("div", { class: "k" }, label), h("div", { class: "mv" }, v), h("div", { class: "muted", style: { fontSize: "11.5px" } }, sub));

  // ---------------------------------------------------------------- requirements
  function repRequirements(body, R) {
    if (!R.requirements.length) { body.append(h("div", { class: "empty" }, icon("info"), h("h3", null, "No requirements"), h("p", null, "Add a REQUIREMENTS.md with REQ-### sections to enable compliance checks."))); return; }
    for (const r of R.requirements) {
      const item = h("div", { class: "item" });
      item.append(h("div", { class: "item-head", onclick: () => item.classList.toggle("open") }, h("span", { class: "item-id" }, r.req_id),
        h("div", { class: "item-title" }, h("div", { class: "txt" }, r.title), h("div", { class: "loc" }, r.symbols.join(", ") || "no symbols")),
        h("div", { class: "scorebox" }, h("span", { class: "chip" }, `${r.results.filter((x) => x.status === "pass").length}/${r.results.length} clauses`), badge(r.status), icon("chev", "chev"))));
      item.append(h("div", { class: "item-body" }, h("p", { style: { marginTop: 0 } }, r.text),
        r.results.length ? h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("thead", null, h("tr", null, ["Kind", "Clause", "Result", "Detail"].map((x) => h("th", null, x)))),
          h("tbody", null, r.results.map((x) => h("tr", null, h("td", null, h("span", { class: "chip" }, x.kind)), h("td", { class: "mono" }, x.expr), h("td", null, h("span", { class: `chip ${x.status === "pass" ? "good" : x.status === "skipped" ? "warn" : "bad"}` }, x.status)), h("td", { class: "muted", style: { fontSize: "12px" } }, x.detail)))))) : h("div", { class: "muted" }, "Prose only — no executable clauses."),
        r.proofs.length ? h("div", null, h("div", { class: "sub-title" }, "Proof certificates"), h("div", { class: "grid cols-2" }, r.proofs.map(certCard))) : null));
      body.append(item);
    }
  }

  // ---------------------------------------------------------------- explorer
  function markersFor(R, file) {
    const marks = {};
    const add = (line, kind, text) => { (marks[line] = marks[line] || { kind, items: [] }).items.push(text); if (kind === "bad") marks[line].kind = "bad"; };
    for (const f of R.static_findings) if (f.file === file) add(f.line, f.security && f.severity !== "LOW" ? "bad" : "warn", `${f.tool} ${f.rule_id}: ${f.message}`);
    for (const c of R.claims) if (c.claim.target_file === file) for (let l = c.claim.target_line_start; l <= Math.min(c.claim.target_line_end, c.claim.target_line_start + 40); l++) add(l, "info", `${c.claim.claim_id} (${c.state}): ${c.claim.claim_text}`);
    return marks;
  }
  function repExplorer(body, R) {
    const files = R.workspace.files.filter((f) => R.files[f] !== undefined);
    if (!S.explorer.file || !files.includes(S.explorer.file)) S.explorer.file = files.find((f) => /\.py$/.test(f) && !/test/.test(f)) || files[0];
    const file = S.explorer.file;
    const wrap = h("div", { class: "explorer" });
    const left = h("div", { class: "stack" });
    left.append(h("select", { class: "input mono", onchange: (e) => { S.explorer = { file: e.target.value, line: null }; render(); } }, files.map((f) => h("option", { value: f, selected: f === file ? "selected" : null }, f))));
    const tree = h("div", { class: "tree" });
    const build = (nodes, parent) => {
      for (const n of nodes) {
        const lbl = [h("span", { class: `kind ${n.kind}` }, n.kind.replace("class_attribute", "attr").slice(0, 6)), h("span", null, n.name), h("span", { class: "faint" }, `:${n.line}`)];
        const go = (e) => { e.preventDefault(); e.stopPropagation(); S.explorer.line = n.line; scrollToLine(n.line); };
        if (n.children && n.children.length) {
          const d = h("details", { open: n.kind === "class" || n.kind === "function" ? true : null });
          d.append(h("summary", { onclick: (e) => { if (e.target.closest("span")) go(e); }, title: n.signature || n.qualified }, lbl));
          build(n.children, d);
          parent.append(d);
        } else parent.append(h("div", { class: "leaf", title: n.signature || n.detail || n.qualified, onclick: go }, lbl));
      }
    };
    build((R.symbol_tree[file] || []), tree);
    if (!tree.childNodes.length) tree.append(h("div", { class: "muted", style: { padding: "8px" } }, "No symbols (non-code file)."));
    left.append(h("div", { class: "sub-title", style: { margin: "4px 0 0" } }, "AST explorer"), tree);
    left.append(h("div", { class: "row wrap", style: { fontSize: "11.5px" } }, h("span", { class: "chip" }, h("span", { class: "dotc", style: { background: "var(--bad)" } }), "security finding"), h("span", { class: "chip" }, h("span", { class: "dotc", style: { background: "var(--warn)" } }), "other finding"), h("span", { class: "chip" }, h("span", { class: "dotc", style: { background: "var(--info)" } }), "AI claim range")));
    wrap.append(left);
    const marks = markersFor(R, file);
    const tbl = h("table");
    (R.files[file] || "").split("\n").forEach((line, i) => {
      const ln = i + 1, m = marks[ln];
      const tr = h("tr", { id: `L${ln}`, class: S.explorer.line === ln ? "hl" : "", title: m ? m.items.join("\n") : null });
      const td = h("td", { class: "ln" }, m ? h("span", { class: `mark ${m.kind}` }) : null, String(ln));
      tr.append(td, h("td", { html: /\.py$/.test(file) ? highlight(line) || " " : esc(line) || " " }));
      tbl.append(tr);
    });
    const view = h("div", { class: "code-view", id: "codeView" }, tbl);
    wrap.append(h("div", null, h("div", { class: "row", style: { marginBottom: "8px" } }, h("span", { class: "chip mono" }, file), h("span", { class: "spacer" }), h("span", { class: "faint", style: { fontSize: "12px" } }, `${Object.keys(marks).length} annotated lines`)), view));
    body.append(wrap);
    if (S.explorer.line) setTimeout(() => scrollToLine(S.explorer.line), 60);
  }
  function scrollToLine(ln) {
    const row = document.getElementById(`L${ln}`); const view = $("#codeView");
    if (!row || !view) return;
    $$("tr.hl", view).forEach((r) => r.classList.remove("hl"));
    row.classList.add("hl", "flash");
    view.scrollTo({ top: row.offsetTop - view.clientHeight / 3, behavior: "smooth" });
    setTimeout(() => row.classList.remove("flash"), 1600);
  }

  // ---------------------------------------------------------------- call graph
  function repGraph(body, R) {
    const G = R.call_graph;
    const hot = new Map();
    const focus = R.patches.filter((p) => !S.graph.focusPatch || p.patch.patch_id === S.graph.focusPatch);
    for (const p of focus) { p.blast_radius.changed_functions.forEach((f) => hot.set(f, "changed")); p.blast_radius.impacted.forEach((i) => { if (!hot.has(i.qualified_name)) hot.set(i.qualified_name, "impacted"); }); }
    const claimSubjects = new Set();
    for (const c of R.claims) for (const s of c.symbols) for (const m of s.matches) if (["function", "method", "class"].includes(m.kind)) claimSubjects.add(m.qualified_name);
    let nodes = G.nodes.filter((n) => S.graph.showTests || !(n.id.startsWith("tests") || /(^|\.)test_/.test(n.id)));
    const ids = new Set(nodes.map((n) => n.id));
    const edges = G.edges.filter((e) => ids.has(e.source) && ids.has(e.target) && (S.graph.showImports || e.kind === "call"));
    body.append(h("div", { class: "row wrap", style: { marginBottom: "10px" } },
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.graph.showTests, onchange: (e) => { S.graph.showTests = e.target.checked; render(); } }), h("span", { class: "track" }), "Test functions"),
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: S.graph.showImports, onchange: (e) => { S.graph.showImports = e.target.checked; render(); } }), h("span", { class: "track" }), "Import edges"),
      R.patches.length ? h("select", { class: "input", style: { width: "auto" }, onchange: (e) => { S.graph.focusPatch = e.target.value || null; render(); } }, h("option", { value: "" }, "Blast radius: all patches"), R.patches.map((p) => h("option", { value: p.patch.patch_id, selected: S.graph.focusPatch === p.patch.patch_id ? "selected" : null }, `Blast radius: ${p.patch.patch_id}`))) : null,
      h("span", { class: "spacer" }), h("span", { class: "faint", style: { fontSize: "12px" } }, `${nodes.length} nodes · ${edges.length} edges · drag to pan, scroll to zoom, click a node to open its code`)));
    const W = 1000, H = 560;
    const pos = forceLayout(nodes, edges, W, H);
    const color = (n) => hot.get(n.id) === "changed" ? "var(--bad)" : hot.get(n.id) === "impacted" ? "var(--warn)" : claimSubjects.has(n.id) ? "var(--accent)" : n.kind === "module" ? "var(--accent-2)" : n.kind === "class" ? "var(--tok-kw)" : "var(--info)";
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const defs = document.createElementNS(svgNS, "defs");
    defs.innerHTML = `<marker id="arr" viewBox="0 0 10 10" refX="16" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="var(--border-2)" stroke="none"/></marker><marker id="arrh" viewBox="0 0 10 10" refX="16" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="var(--bad)" stroke="none"/></marker>`;
    svg.append(defs);
    const g = document.createElementNS(svgNS, "g");
    svg.append(g);
    for (const e of edges) {
      const a = pos.get(e.source), b = pos.get(e.target); if (!a || !b) continue;
      const line = document.createElementNS(svgNS, "line");
      const isHot = hot.has(e.target) && hot.has(e.source);
      line.setAttribute("class", `gedge ${e.kind} ${isHot ? "hot" : ""}`);
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y); line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      line.setAttribute("marker-end", `url(#${isHot ? "arrh" : "arr"})`);
      g.append(line);
    }
    for (const n of nodes) {
      const p = pos.get(n.id);
      const grp = document.createElementNS(svgNS, "g");
      grp.setAttribute("class", "gnode");
      grp.setAttribute("transform", `translate(${p.x},${p.y})`);
      const r = n.kind === "module" ? 9 : hot.has(n.id) ? 8 : 6;
      grp.innerHTML = `<circle r="${r}" fill="${color(n)}"></circle><text x="${r + 4}" y="4">${esc(n.label)}</text><title>${esc(n.id)}${n.file ? `\n${n.file}:${n.line}` : ""}${hot.has(n.id) ? `\n${hot.get(n.id)} by patch` : ""}</title>`;
      grp.addEventListener("click", (ev) => { ev.stopPropagation(); if (n.file && R.files[n.file] !== undefined) { S.explorer = { file: n.file, line: n.line }; S.reportTab = "explorer"; render(); } });
      g.append(grp);
    }
    const st = S.graph;
    const apply = () => g.setAttribute("transform", `translate(${st.tx},${st.ty}) scale(${st.zoom})`);
    apply();
    let drag = null;
    svg.addEventListener("mousedown", (e) => { drag = { x: e.clientX, y: e.clientY, tx: st.tx, ty: st.ty }; });
    if (st._mm) window.removeEventListener("mousemove", st._mm);
    if (st._mu) window.removeEventListener("mouseup", st._mu);
    st._mm = (e) => { if (!drag || !svg.isConnected) return; const k = W / svg.clientWidth; st.tx = drag.tx + (e.clientX - drag.x) * k; st.ty = drag.ty + (e.clientY - drag.y) * k; apply(); };
    st._mu = () => { drag = null; };
    window.addEventListener("mousemove", st._mm);
    window.addEventListener("mouseup", st._mu);
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect(); const k = W / rect.width;
      const mx = (e.clientX - rect.left) * k, my = (e.clientY - rect.top) * k;
      const nz = clamp(st.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.3, 4);
      st.tx = mx - ((mx - st.tx) * nz) / st.zoom; st.ty = my - ((my - st.ty) * nz) / st.zoom; st.zoom = nz; apply();
    }, { passive: false });
    const legend = h("div", { class: "graph-legend" }, [["var(--accent-2)", "module"], ["var(--info)", "function"], ["var(--accent)", "cited by a claim"], ["var(--bad)", "changed by patch"], ["var(--warn)", "impacted caller"]].map(([c, l]) => h("span", { class: "row", style: { gap: "5px" } }, h("span", { class: "dotc", style: { background: c } }), l)));
    const tools = h("div", { class: "graph-tools" },
      h("button", { class: "icon-btn", title: "Zoom in", onclick: () => { st.zoom = clamp(st.zoom * 1.2, .3, 4); apply(); } }, icon("plus")),
      h("button", { class: "icon-btn", title: "Reset view", onclick: () => { st.zoom = 1; st.tx = 0; st.ty = 0; apply(); } }, icon("refresh")));
    body.append(h("div", { class: "graph-wrap" }, svg, legend, tools));
  }
  function forceLayout(nodes, edges, W, H) {
    const pos = new Map();
    const groups = [...new Set(nodes.map((n) => n.id.split(".").slice(0, 2).join(".")))];
    nodes.forEach((n, i) => {
      const gi = groups.indexOf(n.id.split(".").slice(0, 2).join("."));
      const ang = (gi / Math.max(1, groups.length)) * Math.PI * 2;
      const jitter = ((i * 9301 + 49297) % 233280) / 233280;
      pos.set(n.id, { x: W / 2 + Math.cos(ang) * 260 + Math.cos(jitter * 6.28) * 60, y: H / 2 + Math.sin(ang) * 180 + Math.sin(jitter * 6.28) * 60, vx: 0, vy: 0, g: gi });
    });
    const arr = [...pos.values()];
    const links = edges.map((e) => [pos.get(e.source), pos.get(e.target), e.kind]).filter(([a, b]) => a && b && a !== b);
    for (let it = 0; it < 320; it++) {
      const alpha = 1 - it / 320;
      for (let i = 0; i < arr.length; i++) for (let j = i + 1; j < arr.length; j++) {
        const a = arr[i], b = arr[j]; let dx = a.x - b.x, dy = a.y - b.y; let d2 = dx * dx + dy * dy || 0.01;
        const f = (2600 / d2) * alpha; const d = Math.sqrt(d2); dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      }
      for (const [a, b, kind] of links) {
        const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1; const target = kind === "import" ? 170 : 95;
        const f = ((d - target) / d) * 0.04 * alpha; a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
      }
      for (const p of arr) { p.vx += (W / 2 - p.x) * 0.004 * alpha; p.vy += (H / 2 - p.y) * 0.006 * alpha; p.x += clamp(p.vx, -20, 20); p.y += clamp(p.vy, -20, 20); p.vx *= 0.6; p.vy *= 0.6; p.x = clamp(p.x, 30, W - 110); p.y = clamp(p.y, 24, H - 24); }
    }
    return pos;
  }

  // ==================================================================== EVIDENCE
  function viewEvidence() {
    const R = S.report;
    if (!R) return noReport();
    const root = h("div");
    root.append(h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Ground truth"), h("h1", null, "Claim-blind evidence ledger"),
      h("p", null, "Every artifact below was hashed into a SHA-256 chain and sealed before any AI claim was parsed. Verify the chain in your browser — or tamper with it and watch it break."))));
    const L = R.ledger;
    const status = h("span", { class: "verify-state" }, "not verified yet");
    const tbody = h("tbody");
    const rows = L.entries.map((e) => {
      const tr = h("tr", { class: `ledger-row ${e.kind === "SEAL" ? "seal" : ""}` },
        h("td", { class: "mono" }, e.seq), h("td", null, h("span", { class: `chip ${e.stage === "seal" ? "accent" : e.stage === "verify" ? "info" : ""}` }, e.stage)), h("td", { class: "mono" }, e.kind), h("td", { class: "muted" }, e.source), h("td", null, e.summary),
        h("td", { class: "hash", title: e.hash }, `${e.hash.slice(0, 14)}…`), h("td", { class: "vcell" }, "—"));
      tbody.append(tr);
      return tr;
    });
    const verify = async (entries, label) => {
      const enc = new TextEncoder();
      let prev = "0".repeat(64), okAll = true, brokeAt = null;
      for (let i = 0; i < entries.length; i++) {
        const e = entries[i];
        const buf = await crypto.subtle.digest("SHA-256", enc.encode(`${e.seq}|${e.stage}|${e.kind}|${e.source}|${e.digest}|${e.prev_hash}`));
        const hex = [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
        const ok = hex === e.hash && e.prev_hash === prev && e.seq === i;
        const cell = $(".vcell", rows[i]);
        cell.innerHTML = ""; cell.append(h("span", { class: `chip ${ok && okAll ? "good" : "bad"}` }, ok && okAll ? "✓ linked" : "✕ broken"));
        if (!ok && okAll) { okAll = false; brokeAt = i; }
        prev = e.hash;
        await sleep(25);
      }
      status.className = `verify-state ${okAll ? "ok" : "bad"}`;
      status.textContent = okAll ? `✓ ${entries.length} entries verified — chain intact (${label})` : `✕ chain broken at entry #${brokeAt} (${label})`;
      toast(okAll ? "Ledger chain verified" : `Tampering detected at entry #${brokeAt}`, okAll ? "good" : "bad");
    };
    const canVerify = !!(window.crypto && crypto.subtle);
    root.append(h("div", { class: "card" },
      h("div", { class: "card-head" }, icon("lock"), h("h3", null, "Hash-chained ledger"), h("span", { class: "sub" }, `seal #${L.seal_seq} · head ${L.head_hash.slice(0, 12)}…`), h("span", { class: "spacer" }), status,
        h("button", { class: "btn sm", disabled: !canVerify, title: canVerify ? "" : "WebCrypto requires a secure context (localhost or https)", onclick: () => verify(L.entries, "original") }, icon("check"), "Verify chain"),
        h("button", { class: "btn sm danger", disabled: !canVerify, onclick: () => { const t = JSON.parse(JSON.stringify(L.entries)); const k = Math.max(1, (L.seal_seq || 2) - 2); t[k].digest = t[k].digest.replace(/^./, (c) => (c === "a" ? "b" : "a")); toast(`Simulating tampering with entry #${k}'s evidence…`, "warn"); verify(t, `entry #${k} tampered`); } }, icon("zap"), "Simulate tampering")),
      h("div", { class: "tbl-wrap", style: { border: 0, borderRadius: 0, maxHeight: "none" } }, h("table", { class: "tbl" }, h("thead", null, h("tr", null, ["#", "Stage", "Kind", "Source", "Summary", "Hash", "Check"].map((x) => h("th", null, x)))), tbody)),
      h("div", { class: "card-pad faint mono", style: { fontSize: "11.5px", borderTop: "1px solid var(--border)" } }, `hash_i = ${L.formula}`)));

    const tools = [...new Set(R.static_findings.map((f) => f.tool))];
    let toolFilter = "all";
    const ftbl = h("div");
    const drawF = () => { ftbl.innerHTML = ""; const list = R.static_findings.filter((f) => toolFilter === "all" || f.tool === toolFilter); ftbl.append(list.length ? findingsTable(list) : h("div", { class: "muted", style: { padding: "12px" } }, "No findings.")); };
    const seg = h("div", { class: "seg" }, ["all", ...tools].map((t) => h("button", { class: t === "all" ? "active" : "", onclick: (e) => { toolFilter = t; $$("button", seg).forEach((b) => b.classList.remove("active")); e.target.classList.add("active"); drawF(); } }, t)));
    drawF();
    root.append(sectionTitle(`Static analysis — ${R.static_findings.length} findings`, seg), ftbl);

    const T = R.baseline_tests;
    root.append(sectionTitle(`Baseline tests — ${T.framework}`, h("span", { class: "chip" }, `${(T.tests.filter((t) => t.outcome === "passed")).length}/${T.tests.length} passing`)));
    root.append(T.tests.length ? h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["Test", "Outcome", "Duration", "Lines executed (traced)"].map((x) => h("th", null, x)))),
      h("tbody", null, T.tests.map((t) => h("tr", { title: t.message }, h("td", { class: "mono" }, t.test_id), h("td", null, outcomeChip(t.outcome)), h("td", { class: "mono" }, `${(t.duration * 1000).toFixed(1)} ms`),
        h("td", { class: "muted mono", style: { fontSize: "11.5px" } }, Object.entries(t.lines).filter(([f]) => !/test/.test(f)).map(([f, ls]) => `${f}: ${ls.length}`).join(" · ") || "—")))))) : h("div", { class: "muted" }, T.error || "No tests were run."));

    const ts = R.capabilities.tool_status || {};
    root.append(sectionTitle("Tool status"), h("div", { class: "row wrap" }, Object.entries(ts).map(([k, v]) => h("span", { class: `chip ${v === "ok" ? "good" : v === "unavailable" ? "" : "bad"}` }, `${k}: ${v}`))));
    return root;
  }

  // ==================================================================== PROVE
  const PRESETS = [
    { name: "Volume pricing never gets cheaper", function: "shop.pricing.bulk_price", call: "bulk_price(unit, qty + 1) >= bulk_price(unit, qty)", post: "", domain: "unit = [1, 2, 5, 10, 25]; qty = range(0, 200)" },
    { name: "Discount stays within [50%, 100%] of price", function: "shop.pricing.apply_discount", call: "apply_discount(price, pct)", post: "price * 0.5 <= result <= price", domain: "price = range(0, 501, 5); pct = range(-20, 151, 5)" },
    { name: "Bill shares reconcile to the cent", function: "shop.pricing.split_bill", call: "split_bill(amount, people)", post: "abs(result * people - amount) <= 0.01 * people", domain: "amount = range(0, 1000, 7); people = range(1, 13)" },
    { name: "Reservations never go negative", function: "shop.inventory.reserve", call: 'reserve({"A": stock}, "A", qty)', post: "result >= 0 and result == stock - qty", domain: "stock = range(0, 30); qty = range(-10, 40)", allow: "ValueError" },
    { name: "Prove a hallucinated function", function: "shop.pricing.round_currency", call: "round_currency(x)", post: "result >= 0", domain: "x = range(0, 10)" },
  ];
  const P = { function: PRESETS[0].function, call: PRESETS[0].call, post: PRESETS[0].post, domain: PRESETS[0].domain, pre: "", allow: "", setup: "", cert: null, busy: false };
  function viewProve() {
    const root = h("div");
    root.append(h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Proof console"), h("h1", null, "“Prove this code is correct”"),
      h("p", null, "An AI saying code is correct is a claim, not a proof. State the property; TRUST-CODE discharges it in the sandbox — exhaustively over a finite domain (bounded proof) or by deterministic sampling — and returns a hashed certificate or a concrete counterexample."))));
    const funcs = [];
    const walk = (nodes) => nodes.forEach((n) => { if (n.kind === "function" || n.kind === "method") funcs.push(n.qualified); walk(n.children || []); });
    if (S.report) Object.values(S.report.symbol_tree).forEach(walk);
    const grid = h("div", { class: "grid cols-2" });
    const f = (label, key, mono = true, ph = "") => h("div", { class: "field" }, h("label", null, label), h("input", { class: `input ${mono ? "mono" : ""}`, value: P[key], placeholder: ph, list: key === "function" ? "fnList" : null, oninput: (e) => { P[key] = e.target.value; } }));
    const form = h("div", { class: "card card-pad stack" },
      h("div", { class: "row wrap" }, h("span", { class: "sub-title", style: { margin: 0 } }, "Presets"), PRESETS.map((p) => h("button", { class: "chip clickable", onclick: () => { Object.assign(P, { function: p.function, call: p.call, post: p.post, domain: p.domain, allow: p.allow || "", pre: "", setup: "" }); render(); } }, p.name))),
      h("datalist", { id: "fnList" }, funcs.map((q) => h("option", { value: q }))),
      f("Function under proof (qualified name)", "function"),
      f("Call or property", "call", true, "f(x, y)   or   f(x + 1) >= f(x)"),
      f("Post-condition over `result` (leave empty if the call is itself a property)", "post", true, "0 <= result <= x"),
      f("Finite input domain", "domain", true, "x = range(0, 100); y = [1, 2, 3]"),
      h("div", { class: "grid cols-2" }, f("Precondition (optional)", "pre", true, "y != 0"), f("Allowed exceptions (optional)", "allow", true, "ValueError")),
      f("Setup code (optional)", "setup", true, "from pkg import db; conn = db.connect()"),
      h("div", { class: "row" }, h("span", { class: "muted", style: { fontSize: "12.5px", flex: 1 } }, `Runs against the ${Object.keys(S.ws.files).length} files currently in the workbench.`),
        h("button", { class: "btn primary", disabled: P.busy, onclick: runProof }, icon("target"), P.busy ? "Proving…" : "Attempt proof")));
    grid.append(form);
    const out = h("div", { class: "stack" });
    if (P.busy) out.append(h("div", { class: "card card-pad" }, h("div", { class: "skeleton", style: { height: "22px", width: "40%" } }), h("div", { class: "skeleton", style: { height: "14px", marginTop: "14px" } }), h("div", { class: "skeleton", style: { height: "14px", marginTop: "10px", width: "70%" } })));
    else if (P.cert) out.append(P.cert.error ? h("div", { class: "card card-pad" }, h("div", { class: "row" }, badge("REJECTED"), h("b", null, "Proof request refused")), h("p", { class: "muted" }, P.cert.error)) : certCard(P.cert));
    else out.append(h("div", { class: "card empty" }, icon("target"), h("h3", null, "No certificate yet"), h("p", null, "Pick a preset or state your own property.")));
    out.append(h("div", { class: "card card-pad" }, h("div", { class: "sub-title", style: { marginTop: 0 } }, "What the statuses mean"),
      explainList(["✓ PROVED_BOUNDED — holds for every input in the declared finite domain (exhaustive). Nothing is claimed beyond it.", "✗ REFUTED — a concrete, reproducible counterexample exists; the correctness claim is false.", "△ INCONCLUSIVE — the domain was too large to exhaust; sampled inputs passed, but that is not a proof.", "• A proof about a function that does not exist is refused — hallucinations cannot be proved."])));
    grid.append(out);
    root.append(grid);
    return root;
  }
  async function runProof() {
    if (!Object.keys(S.ws.files).length) { toast("Load a scenario or files in the workbench first", "warn"); return; }
    P.busy = true; P.cert = null; render();
    try {
      P.cert = await api("/api/v1/prove", { body: { workspace: { files: S.ws.files }, function: P.function.trim(), call: P.call.trim(), post: P.post.trim(), domain: P.domain.trim(), pre: P.pre.trim() || null, allow: P.allow.split(",").map((s) => s.trim()).filter(Boolean), setup: P.setup.trim() || null } });
      toast(`Certificate: ${P.cert.status.replace("_", " ")}`, P.cert.status === "PROVED_BOUNDED" ? "good" : P.cert.status === "REFUTED" ? "bad" : "warn");
    } catch (e) { P.cert = { error: e.message }; toast(e.message, "bad", 5000); }
    finally { P.busy = false; if (route() === "prove") render(); }
  }

  // ==================================================================== ARCHITECTURE
  function viewArchitecture() {
    const root = h("div");
    root.append(h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "How it works"), h("h1", null, "A verifier that never asks another LLM"),
      h("p", null, "LLM-as-a-judge inherits the generator's failure modes. TRUST-CODE replaces it with heterogeneous, deterministic evidence and a mathematically defined trust score — following the TRUST-CODE SOP end to end."))));
    const layers = [
      ["01", "Input layer", ["Git / folder / inline code", "PRD / SRS with executable clauses", "AI review prose & unified diffs", "LLM-judge transcript (optional)"]],
      ["02", "Evidence gathering", ["AST symbol tables (ast / Tree-sitter)", "trustcode-sast + Bandit + Semgrep", "Sandboxed baseline tests + line tracing", "Requirement checks & bounded proofs"]],
      ["03", "Seal", ["SHA-256 hash chain of all evidence", "Sealed before any claim is parsed", "Browser-verifiable, tamper-evident"]],
      ["04", "Verification", ["Claims: symbols → predicates → evidence", "Patches: apply → test → Δ-scan → probe → mutate", "Judge: correlated-error & bias audit"]],
      ["05", "Decision & output", ["S = clamp(0,100, 50 + 25Cs − 15Cu − 35Cc − 50Ch)", "Hard gates: regression, new CWE, hallucination", "TRUSTED ≥ 80 · UNCERTAIN ≥ 50 · REJECTED"]],
    ];
    root.append(h("div", { class: "pipeline" }, layers.map(([n, t, items]) => h("div", { class: "layer" }, h("div", { class: "n" }, n), h("h4", null, t), h("ul", null, items.map((x) => h("li", null, x)))))));
    const novel = [
      ["Claim-blind sealed evidence ledger", "Ground truth is hash-chained and sealed before the AI's claims are read, proving evidence was not selected to fit the claims it judges."],
      ["Typed semantic-predicate compilation", "Review prose is compiled into polarity-aware boolean predicates (initialised-before-use, handles-exceptions, parameterised-SQL, …) decided on AST facts — catching real-symbol, false-statement hallucinations."],
      ["Zero-tolerance symbol & API grounding", "Every cited file, line range, symbol, parameter, requirement ID and standard-library attribute (by introspection) must resolve, with nearest-neighbour correction."],
      ["Executable specification oracles", "Requirements carry check / forbid / prove clauses, so compliance claims — and patches — are decided by execution, not by reading."],
      ["Counterfactual polarity probing of LLM judges", "The engine negates each claim; a judge that confirms both a claim and its negation is measured as confirmation-biased, and φ-correlation with generator errors yields an independence index."],
      ["Mutation witness on patched lines", "Mutations confined to the diff prove whether the test suite can even notice the fix — turning “tests pass” into a measurable claim."],
      ["Differential behavioural probing", "Inputs synthesised from the function body are run pre- and post-patch; unexplained divergences surface novel bugs no test or reviewer mentioned."],
      ["Scope-honest bounded proof certificates", "Correctness claims become proof obligations; certificates state exactly what was proved, and universal claims backed by bounded proofs are capped at UNCERTAIN."],
    ];
    root.append(sectionTitle("Novel mechanisms"), h("div", { class: "grid cols-4" }, novel.map(([t, d], i) => h("div", { class: "novel" }, h("div", { class: "ix" }, i + 1), h("h4", null, t), h("p", null, d)))));
    const sop = [
      ["Module 1 · Ingestion", "src/ingestion/git_loader.py, ast_parser.py, spec_indexer.py, src/models/ingestion.py"],
      ["Module 2 · Evidence", "src/evidence/static_analyzer.py, rules.py, test_runner.py, evidence_store.py, ledger.py"],
      ["Module 3 · Claims", "src/verification/claim_extractor.py, symbol_resolver.py, call_graph.py, semantic_checker.py, claim_verifier.py"],
      ["Module 4 · Patches", "src/verification/patch_validator.py, diff_utils.py, name_resolution.py, mutation.py, behavior_probe.py, src/sandbox/"],
      ["Module 5 · Scoring", "src/scoring/weights.py, score_engine.py"],
      ["Extensions", "prover.py (bounded proofs), requirement_verifier.py, verifier_audit.py (LLM-judge audit)"],
      ["Delivery", "src/main.py (FastAPI), src/web (this console), src/ui/dashboard.py (Streamlit), src/cli.py (CI)"],
    ];
    root.append(sectionTitle("SOP traceability"), h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("tbody", null, sop.map(([a, b]) => h("tr", null, h("td", { style: { width: "220px", fontWeight: 700 } }, a), h("td", { class: "mono muted" }, b)))))));
    root.append(h("div", { class: "row", style: { marginTop: "18px" } }, h("a", { class: "btn", href: "/docs", target: "_blank" }, icon("link"), "OpenAPI docs"), h("button", { class: "btn primary", onclick: async () => { await selectScenario("full-audit", true); location.hash = "#/workbench"; } }, icon("play"), "Try the full audit")));
    return root;
  }

  // ==================================================================== boot
  (async function boot() {
    renderHistory();
    render();
    await Promise.all([loadHealth(), loadScenarios()]);
    if (S.scenarios.length) await selectScenario(S.scenarios[0].id, true);
    const last = store.get("tc.history", [])[0];
    if (last && !S.report) S.report = last.report;
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    if (params.get("job")) {
      try {
        const j = await api(`/api/v1/jobs/${params.get("job")}`);
        if (j.report) { S.report = j.report; S.reportTab = params.get("tab") || (S.report.claims.length ? "claims" : "patches"); if (params.get("open")) S.openAll = true; }
      } catch (e) { toast(`Job not found: ${e.message}`, "warn"); }
    }
    const auto = params.get("run") || params.get("scenario");
    if (auto && S.scenarios.some((x) => x.id === auto)) {
      await selectScenario(auto, true);
      if (params.get("tab")) S.reportTab = params.get("tab");
      if (params.get("run")) { const tab = params.get("tab"); await runVerification(); if (tab) { S.reportTab = tab; } if (params.get("then")) location.hash = `#/${params.get("then")}`; render(); return; }
    }
    render();
  })();
})();
