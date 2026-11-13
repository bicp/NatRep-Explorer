/* NatRep Explorer – front end (vanilla JS, Chart.js, Leaflet) */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const pct = (v, d = 1) => (v == null || isNaN(v) ? "–" : (v * 100).toFixed(d) + "%");
  const int = (v) => (v == null || isNaN(v) ? "–" : Math.round(v).toLocaleString("en-GB"));
  const DEFAULT_COUNTRY = "Australia";  // opened when the site is visited without a page in the URL
  const AGES = ["18-24", "25-34", "35-44", "45-54", "55+"];
  // Age splits, computed from single-year ages (UN World Population Prospects 2024, table age_single).
  // bands: [from, to] inclusive, to = null means "and over"; an optional third item is the label.
  const AGE_SPLITS = [
    { id: "consumer", name: "Standard consumer (ESOMAR / census)", use: "General B2C surveys, brand tracking and studies requiring national census weighting.",
      bands: [[18, 24], [25, 34], [35, 44], [45, 54], [55, 64], [65, null]] },
    { id: "decade", name: "Decade-based", use: "Broad life-stage analysis (20s vs. 30s vs. 40s) when strict census alignment isn't needed.",
      bands: [[18, 29], [30, 39], [40, 49], [50, 59], [60, 69], [70, null]] },
    { id: "young", name: "Granular / younger skew", use: "Tech, social media, gaming and youth-focused products, where life changes rapidly between 25 and 34.",
      bands: [[18, 24], [25, 29], [30, 34], [35, 44], [45, 54], [55, null]] },
    { id: "senior", name: "Senior / healthcare", use: "Pharma, retirement planning and accessibility studies, where splitting 65+ reveals major differences in health and mobility.",
      bands: [[18, 34], [35, 49], [50, 64], [65, 74], [75, 84], [85, null]] },
    { id: "b2b", name: "B2B / professional", use: "Enterprise software and workplace studies, where career seniority matters more than exact age.",
      bands: [[18, 24, "Under 25"], [25, 34], [35, 44], [45, 54], [55, null]] },
    { id: "b2b-compact", name: "B2B / professional (compact)", use: "Enterprise software and workplace studies with fewer, broader seniority groups.",
      bands: [[18, 24, "Under 25"], [25, 34], [35, 44], [45, null]] },
  ];
  const bandLabel = ([lo, hi, label]) => label || (hi == null ? `${lo}+` : `${lo}–${hi}`);
  const currentSplit = () => AGE_SPLITS.find((s) => s.id === store.get("agesplit", "consumer")) || AGE_SPLITS[0];
  // shares of all adults (male + female = 1) per band of a split
  function splitShares(single, split) {
    const tot = single.reduce((s, r) => s + r.male + r.female, 0) || 1;
    return split.bands.map((b) => {
      const [lo, hi] = b, inBand = single.filter((r) => r.age >= lo && (hi == null || r.age <= hi));
      return { label: bandLabel(b), male: inBand.reduce((s, r) => s + r.male, 0) / tot, female: inBand.reduce((s, r) => s + r.female, 0) / tot };
    });
  }
  const splitOptions = (sel) => AGE_SPLITS.map((s) => `<option value="${s.id}"${s.id === sel.id ? " selected" : ""}>${esc(s.name)}</option>`).join("");
  const store = { get(k, d) { try { const v = localStorage.getItem("natrep:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
                  set(k, v) { try { localStorage.setItem("natrep:" + k, JSON.stringify(v)); } catch {} } };
  const loading = (msg) => `<p class="loading" role="status"><span class="spinner" aria-hidden="true"></span>${esc(msg)}</p>`;
  const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2200); };

  let COUNTRIES = [], META = {};
  const charts = {};

  // ------------------------------------------------------------------ data (SQLite in the browser)
  // scripts/build_db.py publishes the database as chunks in db/; sql.js-httpvfs reads only the pages each query needs
  // with HTTP range requests. These functions return the same shapes the old FastAPI endpoints did.
  const MAX_RADIUS_KM = 500, MAX_ROWS = 50000;
  const abs = (p) => new URL(p, location.href).href;
  let dbp;
  function database() {
    return (dbp ||= (async () => {
      const r = await fetch("db/config.json", { cache: "no-cache" });
      if (!r.ok) throw new Error("database not found – build it with scripts/build_db.py (see README)");
      const config = await r.json();
      config.urlPrefix = abs("db/" + config.urlPrefix);
      return createDbWorker([{ from: "inline", config }], abs("vendor/sql-httpvfs/sqlite.worker.js"), abs("vendor/sql-httpvfs/sql-wasm.wasm"));
    })());
  }
  const rows = async (sql, args = []) => (await database()).db.query(sql, args);
  const parse = (v, d) => (v == null ? d : JSON.parse(v));
  const COLS = "country, iso2, zip, place, admin1, admin2, admin3, lat, lon";

  async function getMeta() {
    const m = Object.fromEntries((await rows("SELECT key, value FROM meta")).map((r) => [r.key, r.value]));
    m.hhi_columns = parse(m.hhi_columns, []);
    m.zip_lens = parse(m.zip_lens, []);
    m.zip_rows = +m.zip_rows;
    m.cells_per_deg = +m.cells_per_deg;
    m.zip_alpha_max = m.zip_alpha_max == null ? Infinity : +m.zip_alpha_max;
    m.countries = (await rows("SELECT count(*) n FROM countries"))[0].n;
    return m;
  }

  async function getCountries() {
    const out = await rows("SELECT name, iso2, zip_records, internet, has_coords, notes, layout FROM countries ORDER BY name");
    const ag = await rows("SELECT country, gender, age, share FROM age_gender");
    const ed = await rows("SELECT country, level, share FROM education WHERE classification LIKE 'ISCED%' ORDER BY ord");
    const hhi = Object.fromEntries((await rows("SELECT country, data_json FROM refs WHERE kind='hhi'")).map((r) => [r.country, JSON.parse(r.data_json)]));
    for (const c of out) {
      const mine = ag.filter((a) => a.country === c.name);
      c.male = mine.filter((a) => a.gender === "Male").reduce((s, a) => s + (a.share || 0), 0);
      c.age = {};
      for (const a of mine) c.age[a.age] = (c.age[a.age] || 0) + (a.share || 0);
      c.tertiary = ed.find((x) => x.country === c.name && x.level.toLowerCase().startsWith("tertiary"))?.share ?? null;
      const h = hhi[c.name] || {};
      c.hhi_median_eur = h["Median EUR Est"] ?? null;
      c.hhi_confidence = h.Confidence ?? null;
    }
    return out;
  }

  async function getCountry(name) {
    const [c] = await rows("SELECT * FROM countries WHERE name = ?", [name]);
    if (!c) throw new Error("Unknown country");
    c.warnings = JSON.parse(c.warnings || "[]");
    c.home_note = c.notes;
    c.age_gender = await rows("SELECT gender, age, share FROM age_gender WHERE country=? ORDER BY gender DESC, age", [name]);
    c.age_single = await rows("SELECT age, male, female FROM age_single WHERE country=? ORDER BY age", [name]).catch(() => []);
    c.education = await rows("SELECT classification, level, share FROM education WHERE country=? ORDER BY ord", [name]);
    c.income = await rows("SELECT band, lower, upper, share, currency, year, definition, source, confidence, base FROM income WHERE country=? ORDER BY ord", [name]);
    c.notes = (await rows("SELECT text FROM notes WHERE country=? ORDER BY ord", [name])).map((r) => r.text);
    c.extras = (await rows("SELECT title, source_sheet, rows_json FROM extras WHERE country=? ORDER BY ord", [name]))
      .map((r) => ({ title: r.title, sheet: r.source_sheet, rows: JSON.parse(r.rows_json) }));
    const ref = Object.fromEntries((await rows("SELECT kind, data_json FROM refs WHERE country=?", [name])).map((r) => [r.kind, JSON.parse(r.data_json)]));
    c.hhi = ref.hhi ?? null;
    c.sources = ref.sources ?? null;
    c.regions = await rows("SELECT value admin1, n FROM regions WHERE country=? AND level='admin1' ORDER BY value COLLATE BINARY", [name]);
    return c;
  }

  const ftsQuery = (q) => (q.match(/[\p{L}\p{N}_]+/gu) || []).map((t) => `"${t}"*`).join(" ");

  async function search(q, country, limit = 60) {
    q = q.trim();
    const cw = country ? " AND country = ?" : "", ca = country ? [country] : [];
    // 1) zip codes starting with the query, shortest first: one index range per zip length
    const zq = q.replace(/\s+/g, "").replace(/[%_]/g, "");
    const results = [];
    // no zip starts with more than zip_alpha_max non-digits, so a word like "Berlin" can't be a zip prefix
    const maybeZip = zq && zq.match(/^\D*/)[0].length <= META.zip_alpha_max;
    if (maybeZip) for (const len of META.zip_lens.filter((l) => l >= zq.length)) {
      results.push(...await rows(`SELECT id, ${COLS} FROM zips WHERE zip_len = ? AND zip_norm LIKE ?${cw} ORDER BY zip_norm LIMIT ?`,
        [len, zq + "%", ...ca, limit - results.length]));
      if (results.length >= limit) break;
    }
    // 2) cities / places: exact name first, then FTS prefix matches by number of zips (places are stored in that order)
    const ft = ftsQuery(q);
    let places = [];
    if (ft) {
      const P = "p.id, p.country, p.iso2, p.place, p.admin1, p.admin2, p.zips, p.lat, p.lon, p.first_zip";
      places = await rows(`SELECT ${P} FROM places p WHERE p.place = ?${country ? " AND p.country = ?" : ""} ORDER BY p.id LIMIT 15`, [q, ...ca]);
      const match = `place : (${ft})` + (country ? ` AND country : "${country.replace(/"/g, '""')}"` : "");
      const more = await rows(`SELECT ${P} FROM places_fts f JOIN places p ON p.id = f.rowid WHERE places_fts MATCH ?${country ? " AND p.country = ?" : ""}
                               ORDER BY f.rowid LIMIT ?`, [match, ...ca, 15 + places.length]);
      const seen = new Set(places.map((p) => p.id));
      for (const p of more) if (!seen.has(p.id) && places.length < 15) { places.push(p); seen.add(p.id); }
    }
    // 3) state / region and province / county names
    const regions = [];
    if (q.length >= 2) for (const level of ["admin1", "admin2"])
      regions.push(...await rows(`SELECT country, level, value, n FROM regions WHERE level = ? AND value LIKE ?${cw} ORDER BY n DESC LIMIT 8`, [level, q + "%", ...ca]));
    return { results, places, regions: regions.slice(0, 12) };
  }

  function haversine(lat1, lon1, lat2, lon2) {
    const rad = Math.PI / 180, p1 = lat1 * rad, p2 = lat2 * rad, dp = p2 - p1, dl = (lon2 - lon1) * rad;
    const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * 6371.0088 * Math.asin(Math.sqrt(a));
  }

  async function radius(lat, lon, km, country) {
    if (!(km > 0 && km <= MAX_RADIUS_KM)) throw new Error(`Radius must be between 0 and ${MAX_RADIUS_KM} km`);
    const dlat = km / 110.574, dlon = km / (111.320 * Math.max(Math.cos(lat * Math.PI / 180), 0.01));
    // zips are stored by grid cell (row * cols + col); each grid row of the bounding box is one contiguous cell range
    const C = META.cells_per_deg, nrows = 180 * C, ncols = 360 * C;
    const cell = (v, off, n) => Math.min(Math.max(Math.floor((v + off) * C), 0), n - 1);
    const r0 = cell(lat - dlat, 90, nrows), r1 = cell(lat + dlat, 90, nrows), c0 = cell(lon - dlon, 180, ncols), c1 = cell(lon + dlon, 180, ncols);
    const ranges = [], args = [];
    for (let r = r0; r <= r1; r++) { ranges.push("cell BETWEEN ? AND ?"); args.push(r * ncols + c0, r * ncols + c1); }
    args.push(lat - dlat, lat + dlat, lon - dlon, lon + dlon, ...(country ? [country] : []));
    // "+country" keeps SQLite on the cell index instead of scanning the whole country
    const cand = await rows(`SELECT ${COLS} FROM zips WHERE (${ranges.join(" OR ")})
                             AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?${country ? " AND +country = ?" : ""}`, args);
    const byZip = new Map();
    for (const r of cand) {
      const d = haversine(lat, lon, r.lat, r.lon);
      if (d > km) continue;
      const key = r.country + "\u0000" + r.zip, cur = byZip.get(key);
      if (!cur) { r.distance_km = Math.round(d * 100) / 100; r.places = r.place ? [r.place] : []; byZip.set(key, r); continue; }
      if (r.place && !cur.places.includes(r.place)) cur.places.push(r.place);
      if (d < cur.distance_km) { for (const k of ["place", "admin1", "admin2", "admin3", "lat", "lon"]) cur[k] = r[k]; cur.distance_km = Math.round(d * 100) / 100; }
    }
    const out = [...byZip.values()].sort((a, b) => a.distance_km - b.distance_km);
    const results = out.slice(0, MAX_ROWS);
    return { center: { lat, lon }, km, count: results.length, truncated: out.length > MAX_ROWS, results };
  }

  async function area({ country, level = "admin1", value = "", within }) {
    if (!["admin1", "admin2", "admin3", "place"].includes(level)) throw new Error("bad level");
    const nested = within && level !== "admin1";
    const results = await rows(`SELECT country, iso2, zip, group_concat(DISTINCT place) places, min(place) place, admin1, admin2, admin3,
                                avg(lat) lat, avg(lon) lon FROM zips WHERE country=? AND ${level}=?${nested ? " AND admin1=?" : ""}
                                GROUP BY zip ORDER BY zip LIMIT ?`, [country, value, ...(nested ? [within] : []), MAX_ROWS]);
    for (const r of results) r.places = (r.places || "").split(",");
    return { country, level, value, count: results.length, results };
  }

  function download(text, filename) {
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv" })), download: filename });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  const safeName = (s, n) => s.replace(/[^\p{L}\p{N}_-]+/gu, "_").slice(0, n);
  function zipsCsv(res, filename) {
    const cell = (v) => { v = String(v ?? ""); return /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
    const cols = ["country", "zip", "place", "admin1", "admin2", "admin3", "lat", "lon", "distance_km", "places"];
    const lines = [["Country", "Zip code", "Place", "State / Region", "Province / County", "Community", "Latitude", "Longitude", "Distance (km)", "All places in zip"]];
    for (const r of res) lines.push(cols.map((c) => (c === "places" ? (r.places || []).join("; ") : r[c])));
    download(lines.map((l) => l.map(cell).join(",")).join("\r\n") + "\r\n", filename);
  }
  function chart(id, cfg) {
    if (charts[id]) charts[id].destroy();
    const el = document.getElementById(id);
    if (!el) return;
    const grid = css("--grid"), ink = css("--text-2");
    Chart.defaults.font.family = css("--font");
    Chart.defaults.color = ink;
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: {} } } }, cfg.options || {});
    const sc = cfg.options.scales || {};
    for (const k of Object.keys(sc)) {
      sc[k].grid = Object.assign({ color: grid, drawTicks: false }, sc[k].grid || {});
      sc[k].border = { display: false };
    }
    charts[id] = new Chart(el, cfg);
  }
  const bar = (color) => ({ backgroundColor: color, borderRadius: 4, borderSkipped: "start", maxBarThickness: 28, borderColor: css("--surface"), borderWidth: { left: 0, right: 0, top: 0, bottom: 0 } });
  const pctAxis = (axis = "y") => ({ [axis]: { beginAtZero: true, ticks: { callback: (v) => v + "%" } } });
  const pctTip = { callbacks: { label: (c) => ` ${c.dataset.label ? c.dataset.label + ": " : ""}${c.parsed[c.chart.options.indexAxis === "y" ? "x" : "y"]?.toFixed(1)}%` } };
  const legend = (items) => `<div class="legend">${items.map(([l, c]) => `<span><i style="background:${c}"></i>${esc(l)}</span>`).join("")}</div>`;

  // ------------------------------------------------------------------ routing
  function route() {
    const [, view = "country", arg] = location.hash.split("/");
    const v = ["country", "compare", "locations"].includes(view) ? view : "country";
    $$(".view").forEach((el) => el.classList.toggle("active", el.id === "view-" + v));
    $$(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.view === v));
    if (v === "country") showCountry(decodeURIComponent(arg || store.get("country", DEFAULT_COUNTRY)));
    if (v === "compare") renderCompare();
    if (v === "locations") { initMap(); setTimeout(() => map.invalidateSize(), 50); }
  }
  window.addEventListener("hashchange", route);

  // ------------------------------------------------------------------ country view
  function renderCountryList() {
    const f = $("#country-filter").value.toLowerCase();
    const cur = store.get("country", "");
    $("#country-list").innerHTML = COUNTRIES.filter((c) => c.name.toLowerCase().includes(f) || (c.iso2 || "").toLowerCase() === f)
      .map((c) => `<li><a href="#/country/${encodeURIComponent(c.name)}" class="${c.name === cur ? "active" : ""}"><span>${esc(c.name)}</span><span class="iso">${esc(c.iso2)}</span></a></li>`).join("");
  }

  function sampleN() { return Math.max(1, +$("#sample-n")?.value || store.get("n", 1000)); }

  async function showCountry(name) {
    store.set("country", name);
    renderCountryList();
    const box = $("#country-content");
    let d;
    try { d = await getCountry(name); }
    catch (e) { box.innerHTML = `<p class="empty">Couldn't load ${esc(name)}: ${esc(e.message)}</p>`; return; }
    const N = store.get("n", 1000);
    const male = d.age_gender.filter((a) => a.gender === "Male"), female = d.age_gender.filter((a) => a.gender === "Female");
    const mShare = male.reduce((s, a) => s + (a.share || 0), 0);
    const tert = d.education.find((e) => /^ISCED/.test(e.classification) && /^tertiary/i.test(e.level));
    const zipInfo = d.zip_sheet ? `${int(d.zip_records)} zip records` : "No zip codes in file";

    box.innerHTML = `
      <div class="head">
        <div><h1>${esc(d.name)}</h1>
          <div class="sub"><span class="tag">${esc(d.iso2 || "")}</span><span class="tag">Layout ${esc(d.layout)}</span><span class="tag">${zipInfo}</span>${d.region_tab ? `<span class="tag">Extra tab: ${esc(d.region_tab)}</span>` : ""}</div>
        </div>
        <div style="display:flex;gap:10px;align-items:center">
          <label class="sample">Sample size <input id="sample-n" type="number" min="1" step="50" value="${N}"></label>
          <a class="btn ghost" href="#/compare" id="add-compare">Compare…</a>
        </div>
      </div>
      ${d.home_note ? `<div class="note">${esc(d.home_note)}</div>` : ""}
      ${d.warnings.length ? `<div class="note">Data check: ${esc(d.warnings.join("; "))}</div>` : ""}
      <div class="kpis">
        <div class="kpi"><div class="label">Internet penetration</div><div class="value">${pct(d.internet)}</div><div class="foot">${esc(d.internet_src || "")}</div></div>
        <div class="kpi"><div class="label">Male / Female (18+)</div><div class="value">${pct(mShare)} / ${pct(1 - mShare)}</div><div class="foot">UN WPP 2024</div></div>
        <div class="kpi"><div class="label">Aged 55+</div><div class="value">${pct(male.concat(female).filter((a) => a.age === "55+").reduce((s, a) => s + a.share, 0))}</div><div class="foot">share of adults</div></div>
        <div class="kpi"><div class="label">Tertiary educated</div><div class="value">${pct(tert?.share)}</div><div class="foot">ISCED, adults 25–64</div></div>
        <div class="kpi"><div class="label">Median HH income (EUR est.)</div><div class="value">${d.hhi?.["Median EUR Est"] ? "€" + int(+d.hhi["Median EUR Est"]) : "–"}</div><div class="foot">${d.hhi?.Confidence ? "Confidence: " + esc(d.hhi.Confidence) : "HHI Comparable"}</div></div>
      </div>
      <div class="grid">
        <div class="card"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="7" r="4"/><path d="M3 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2M16 3.1a4 4 0 0 1 0 7.8M21 21v-2a4 4 0 0 0-3-3.9"/></svg>Gender × age</h2><small>adults 18+</small></div>
          ${legend([["Male", css("--s1")], ["Female", css("--s2")]])}
          <div class="chart"><canvas id="c-age"></canvas></div>
          <div id="t-age"></div></div>
        <div class="card"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M7 16v-3M11 16V8M15 16v-6M19 16V5"/></svg>Age splits</h2><small>adults 18+</small></div>
          <div class="split-pick"><select id="age-split" aria-label="Age split">${splitOptions(currentSplit())}</select><button class="btn ghost" id="split-csv">Download CSV</button></div>
          <p class="small" id="split-use"></p>
          ${legend([["Male", css("--s1")], ["Female", css("--s2")]])}
          <div class="chart"><canvas id="c-split"></canvas></div>
          <div id="t-split"></div>
          <p class="small muted">Source: ${esc(META.age_source || "UN World Population Prospects 2024")}.</p></div>
        <div class="card"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 10 12 5 2 10l10 5 10-5Z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>Education</h2></div><div id="edu"></div></div>
        <div class="card"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2"/></svg>Household income (local)</h2></div><div id="inc"></div></div>
        <div class="card"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 10h12M4 14h9M19 6a7.7 7.7 0 0 0-5.2-2A7.9 7.9 0 0 0 6 12a7.9 7.9 0 0 0 7.8 8 7.7 7.7 0 0 0 5.2-2"/></svg>Household income – comparable EUR bands</h2><small>HHI Comparable tab</small></div><div id="hhi"></div></div>
        <div class="card full"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg>Zip codes</h2><small>${zipInfo}</small></div><div id="zipcard"></div></div>
        ${d.extras.length ? `<div class="card full"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/></svg>Additional country-specific tables</h2><small>kept from the original sheets</small></div><div id="extras"></div></div>` : ""}
        <div class="card full"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15Z"/></svg>Sources &amp; notes</h2></div><div id="sources"></div></div>
      </div>`;

    $("#sample-n").addEventListener("input", () => { store.set("n", sampleN()); fillCountryTables(d); });
    $("#age-split").addEventListener("change", (e) => { store.set("agesplit", e.target.value); renderSplit(d); });
    $("#split-csv").addEventListener("click", () => splitCsv(d));
    $("#add-compare").addEventListener("click", () => { const l = store.get("compare", []); if (!l.includes(d.name)) { l.push(d.name); store.set("compare", l.slice(-6)); } });

    chart("c-age", { type: "bar", data: { labels: AGES, datasets: [
      { label: "Male", data: AGES.map((a) => (male.find((m) => m.age === a)?.share || 0) * 100), ...bar(css("--s1")) },
      { label: "Female", data: AGES.map((a) => (female.find((m) => m.age === a)?.share || 0) * 100), ...bar(css("--s2")) }] },
      options: { scales: { x: { grid: { display: false } }, ...pctAxis() }, plugins: { legend: { display: false }, tooltip: pctTip } } });

    // zip card
    const zc = $("#zipcard");
    if (!d.zip_sheet) zc.innerHTML = `<p class="muted">No zip code tab is available for ${esc(d.name)} in the workbook.</p>`;
    else {
      zc.innerHTML = `${d.has_coords ? "" : `<div class="note">This country's zip data has no coordinates, so radius search isn't available. You can still search and export by region or place.</div>`}
        <p class="small">Click a region to list and export all its zip codes, or <a href="#/locations" id="go-loc">search a city or zip code in ${esc(d.name)}</a>.</p>
        <div class="regions">${d.regions.map((r) => `<button data-v="${esc(r.admin1)}">${esc(r.admin1)} <span class="muted">${int(r.n)}</span></button>`).join("") || '<span class="muted">No region breakdown in the source data.</span>'}</div>`;
      $("#go-loc").addEventListener("click", () => { $("#loc-country").value = d.name; });
      $$(".regions button", zc).forEach((b) => b.addEventListener("click", () => { location.hash = "#/locations"; $("#loc-country").value = d.name; loadArea({ country: d.name, level: "admin1", value: b.dataset.v }); }));
    }

    // extras
    if (d.extras.length) $("#extras").innerHTML = d.extras.map((x) => `<div class="extra">${x.title ? `<h3>${esc(x.title)}</h3>` : ""}${gridTable(x.rows)}${x.sheet !== d.natrep_sheet ? `<div class="small muted">from tab “${esc(x.sheet)}”</div>` : ""}</div>`).join("");

    // sources
    const src = d.sources || {};
    $("#sources").innerHTML = `
      ${d.notes.length ? `<ul class="notes">${d.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      ${Object.keys(src).length ? `<details><summary>Full source reference (Sources tab)</summary><dl class="kv">${Object.entries(src).filter(([k]) => k !== "COUNTRY").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${linkify(v)}</dd>`).join("")}</dl></details>` : ""}`;

    fillCountryTables(d);
  }

  function linkify(t) { return esc(t).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>'); }

  function renderSplit(d) {
    const split = currentSplit(), N = sampleN();
    $("#split-use").textContent = "Best used for: " + split.use;
    if (!d.age_single.length) { $("#t-split").innerHTML = '<p class="muted">No single-year age data in this database – rebuild it with scripts/build_db.py.</p>'; return; }
    const sh = splitShares(d.age_single, split);
    chart("c-split", { type: "bar", data: { labels: sh.map((r) => r.label), datasets: [
      { label: "Male", data: sh.map((r) => r.male * 100), ...bar(css("--s1")) },
      { label: "Female", data: sh.map((r) => r.female * 100), ...bar(css("--s2")) }] },
      options: { scales: { x: { grid: { display: false } }, ...pctAxis() }, plugins: { legend: { display: false }, tooltip: pctTip } } });
    let tm = 0, tf = 0;
    const row = (label, x, y, cls = "") => `<tr class="${cls}"><td>${esc(label)}</td><td class="num">${pct(x, 2)}</td><td class="num">${int(x * N)}</td><td class="num">${pct(y, 2)}</td><td class="num">${int(y * N)}</td><td class="num">${pct(x + y, 2)}</td><td class="num">${int((x + y) * N)}</td></tr>`;
    $("#t-split").innerHTML = `<table><thead><tr><th>Age</th><th class="num">Male %</th><th class="num">Male n</th><th class="num">Female %</th><th class="num">Female n</th><th class="num">Total %</th><th class="num">Total n</th></tr></thead><tbody>
      ${sh.map((r) => { tm += r.male; tf += r.female; return row(r.label, r.male, r.female); }).join("")}
      ${row("Total", tm, tf, "total")}</tbody></table>`;
  }

  function splitCsv(d) {
    const split = currentSplit(), N = sampleN();
    if (!d.age_single.length) return;
    const lines = [["Country", "Age split", "Age", "Male %", "Male n", "Female %", "Female n", "Total %", "Total n"]];
    for (const r of splitShares(d.age_single, split))
      lines.push([d.name, split.name, r.label, (r.male * 100).toFixed(2), Math.round(r.male * N), (r.female * 100).toFixed(2), Math.round(r.female * N), ((r.male + r.female) * 100).toFixed(2), Math.round((r.male + r.female) * N)]);
    lines.push([], [`Source: ${META.age_source || "UN World Population Prospects 2024"}`], [`Sample size: ${N}`]);
    const cell = (v) => { v = String(v ?? ""); return /[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
    download(lines.map((l) => l.map(cell).join(",")).join("\r\n") + "\r\n", `natrep_age_${d.iso2 || safeName(d.name, 20)}_${split.id}.csv`);
  }

  function fillCountryTables(d) {
    const N = sampleN();
    renderSplit(d);
    // gender × age table
    const m = (g, a) => d.age_gender.find((x) => x.gender === g && x.age === a)?.share || 0;
    let tm = 0, tf = 0;
    $("#t-age").innerHTML = `<table><thead><tr><th>Age</th><th class="num">Male %</th><th class="num">Male n</th><th class="num">Female %</th><th class="num">Female n</th><th class="num">Total %</th><th class="num">Total n</th></tr></thead><tbody>
      ${AGES.map((a) => { const x = m("Male", a), y = m("Female", a); tm += x; tf += y;
        return `<tr><td>${a}</td><td class="num">${pct(x, 2)}</td><td class="num">${int(x * N)}</td><td class="num">${pct(y, 2)}</td><td class="num">${int(y * N)}</td><td class="num">${pct(x + y, 2)}</td><td class="num">${int((x + y) * N)}</td></tr>`; }).join("")}
      <tr class="total"><td>Total</td><td class="num">${pct(tm, 2)}</td><td class="num">${int(tm * N)}</td><td class="num">${pct(tf, 2)}</td><td class="num">${int(tf * N)}</td><td class="num">${pct(tm + tf, 2)}</td><td class="num">${int((tm + tf) * N)}</td></tr></tbody></table>`;

    // education
    const groups = {};
    d.education.forEach((e) => (groups[e.classification] ||= []).push(e));
    $("#edu").innerHTML = Object.entries(groups).map(([cls, rows], i) => `
      <h3>${esc(cls)}</h3>
      <div class="chart" style="height:${40 + rows.length * 34}px"><canvas id="c-edu-${i}"></canvas></div>
      <table><thead><tr><th>Level</th><th class="num">%</th><th class="num">n</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${esc(r.level)}</td><td class="num">${pct(r.share)}</td><td class="num">${r.share == null ? "–" : int(r.share * N)}</td></tr>`).join("")}</tbody></table>`).join("") || '<p class="muted">No education data.</p>';
    Object.values(groups).forEach((rows, i) => chart(`c-edu-${i}`, { type: "bar",
      data: { labels: rows.map((r) => r.level.length > 34 ? r.level.slice(0, 32) + "…" : r.level), datasets: [{ data: rows.map((r) => r.share == null ? null : r.share * 100), ...bar(css("--s1")) }] },
      options: { indexAxis: "y", scales: { ...pctAxis("x"), y: { grid: { display: false } } }, plugins: { legend: { display: false }, tooltip: pctTip } } }));

    // income
    const inc = d.income, has = inc.some((r) => r.share != null);
    const i0 = inc[0] || {};
    $("#inc").innerHTML = !inc.length ? '<p class="muted">No income table.</p>' : `
      <p class="small">${esc(i0.definition)} · ${esc(i0.currency)} · ${esc(i0.year)}${i0.confidence ? " · Confidence: " + esc(i0.confidence) : ""}</p>
      ${has ? `<div class="chart" style="height:${40 + inc.length * 34}px"><canvas id="c-inc"></canvas></div>` : '<div class="note">No % distribution by band is published – reference figures only.</div>'}
      <table><thead><tr><th>Band</th>${has ? '<th class="num">%</th><th class="num">n</th>' : ""}</tr></thead><tbody>
      ${inc.map((r) => `<tr><td>${esc(r.band)}</td>${has ? `<td class="num">${pct(r.share)}</td><td class="num">${r.share == null ? "–" : int(r.share * N)}</td>` : ""}</tr>`).join("")}</tbody></table>
      <p class="small muted">Source: ${esc(i0.source)}${i0.base && i0.base !== "—" ? " · Base: " + esc(i0.base) : ""}</p>`;
    if (has) chart("c-inc", { type: "bar", data: { labels: inc.map((r) => r.band.replace(/ \(.*\)$/, "")), datasets: [{ data: inc.map((r) => r.share * 100), ...bar(css("--s1")) }] },
      options: { indexAxis: "y", scales: { ...pctAxis("x"), y: { grid: { display: false } } }, plugins: { legend: { display: false }, tooltip: pctTip } } });

    // HHI comparable
    const h = d.hhi;
    if (!h) $("#hhi").innerHTML = '<p class="muted">Not in the HHI Comparable tab.</p>';
    else {
      const bands = Object.keys(h).filter((k) => k.endsWith(" %"));
      $("#hhi").innerHTML = `<p class="small">Median ≈ €${int(+h["Median EUR Est"])} · Confidence: ${esc(h.Confidence)} · ${esc(h["Primary Source Route"] || "")}</p>
        <div class="chart"><canvas id="c-hhi"></canvas></div>
        <table><thead><tr><th>Band</th><th class="num">%</th><th class="num">Households</th><th class="num">n</th></tr></thead><tbody>
        ${bands.map((b) => `<tr><td>${esc(b.replace(" %", ""))}</td><td class="num">${pct(+h[b])}</td><td class="num">${int(+h[b.replace(" %", " HH")])}</td><td class="num">${int(+h[b] * N)}</td></tr>`).join("")}</tbody></table>`;
      chart("c-hhi", { type: "bar", data: { labels: bands.map((b) => b.replace(" %", "")), datasets: [{ data: bands.map((b) => +h[b] * 100), ...bar(css("--s1")) }] },
        options: { scales: { x: { grid: { display: false } }, ...pctAxis() }, plugins: { legend: { display: false }, tooltip: pctTip } } });
    }
  }

  function gridTable(rows) {
    if (!rows.length) return "";
    const isNum = (v) => typeof v === "number";
    const header = rows.length > 1 && rows[0].every((v) => !isNum(v)) ? rows[0] : null;
    const body = header ? rows.slice(1) : rows;
    const ncol = Math.max(...rows.map((r) => r.length));
    const pctCol = [...Array(ncol)].map((_, j) => { const nums = body.map((r) => r[j]).filter(isNum); return nums.length && nums.every((v) => v >= 0 && v <= 1) && nums.some((v) => !Number.isInteger(v)); });
    const fmt = (v, j) => !isNum(v) ? esc(v) : pctCol[j] ? pct(v, 1) : Number.isInteger(v) ? v.toLocaleString("en-GB") : (+v.toFixed(4)).toLocaleString("en-GB");
    const shown = body.slice(0, 300);
    return `<table>${header ? `<thead><tr>${header.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>` : ""}<tbody>
      ${shown.map((r) => `<tr>${r.map((v, j) => `<td class="${isNum(v) ? "num" : ""}">${fmt(v, j)}</td>`).join("")}</tr>`).join("")}</tbody></table>
      ${body.length > shown.length ? `<div class="small muted">Showing ${shown.length} of ${body.length} rows – see the workbook for the rest.</div>` : ""}`;
  }

  // ------------------------------------------------------------------ compare view
  const SLOTS = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"];
  const cache = {};
  const detail = async (n) => (cache[n] ||= getCountry(n));
  function slotColors() {
    // colour follows the country, not its position: keep a stable slot per country
    const list = store.get("compare", []), slots = store.get("slots", {});
    const used = new Set(list.map((n) => slots[n]).filter((s) => s != null));
    list.forEach((n) => { if (slots[n] == null || ![...Array(6).keys()].includes(slots[n])) { const free = [...Array(6).keys()].find((i) => !used.has(i)); slots[n] = free; used.add(free); } });
    Object.keys(slots).forEach((n) => { if (!list.includes(n)) delete slots[n]; });
    store.set("slots", slots);
    return Object.fromEntries(list.map((n) => [n, css(SLOTS[slots[n]])]));
  }

  const eduKey = (lvl) => /^below/i.test(lvl) ? "Below upper secondary" : /^tertiary/i.test(lvl) ? "Tertiary" : "Upper sec. / post-sec.";
  const EDU = ["Below upper secondary", "Upper sec. / post-sec.", "Tertiary"];
  let cmpRows = [];

  async function renderCompare() {
    let list = store.get("compare", null);
    if (!list) { list = ["Netherlands", "Germany", "United Kingdom"]; store.set("compare", list); }
    const colors = slotColors();
    $("#compare-n").value = store.get("n", 1000);
    $("#compare-add").innerHTML = `<option value="">+ Add country…</option>` + COUNTRIES.filter((c) => !list.includes(c.name)).map((c) => `<option>${esc(c.name)}</option>`).join("");
    $("#compare-add").disabled = list.length >= 6;
    $("#compare-chips").innerHTML = list.map((n) => `<span class="chip"><i style="background:${colors[n]}"></i>${esc(n)}<button data-n="${esc(n)}" aria-label="Remove ${esc(n)}">×</button></span>`).join("");
    $$("#compare-chips button").forEach((b) => b.addEventListener("click", () => { store.set("compare", store.get("compare", []).filter((x) => x !== b.dataset.n)); renderCompare(); }));
    const body = $("#compare-body");
    if (!list.length) { body.innerHTML = '<p class="empty">Add up to six countries to compare.</p>'; return; }
    const ds = await Promise.all(list.map(detail));
    const N = Math.max(1, +$("#compare-n").value || 1000);
    const lg = legend(list.map((n) => [n, colors[n]]));
    const split = currentSplit();
    body.innerHTML = `
      <div class="grid">
        <div class="card"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M8 17V9M13 17V5M18 17v-6"/></svg>Age distribution (adults 18+)</h2><div class="split-pick"><select id="k-split" aria-label="Age split">${splitOptions(split)}</select></div>${lg}<div class="chart"><canvas id="k-age"></canvas></div><p class="small muted">${esc(split.name)} · ${esc(META.age_source || "UN World Population Prospects 2024")}</p></div>
        <div class="card"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 10 12 5 2 10l10 5 10-5Z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg>Education (ISCED, adults 25–64)</h2>${lg}<div class="chart"><canvas id="k-edu"></canvas></div></div>
        <div class="card"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 10h12M4 14h9M19 6a7.7 7.7 0 0 0-5.2-2A7.9 7.9 0 0 0 6 12a7.9 7.9 0 0 0 7.8 8 7.7 7.7 0 0 0 5.2-2"/></svg>Household income – comparable EUR bands</h2>${lg}<div class="chart"><canvas id="k-hhi"></canvas></div></div>
        <div class="card"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.6a10 10 0 0 1 14 0M8.5 16.1a5 5 0 0 1 7 0M2 8.8a15 15 0 0 1 20 0"/><circle cx="12" cy="20" r="1"/></svg>Internet penetration</h2><div class="chart"><canvas id="k-int"></canvas></div></div>
        <div class="card full"><div class="card-head"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v18M3 6h18M3 18h18"/></svg>Side by side</h2><small>% (n at sample size ${int(N)})</small></div><div class="table-wrap" id="k-table"></div></div>
        <div class="card full"><h2><svg class="ico" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2"/></svg>Local household income bands</h2><div class="grid" id="k-inc"></div></div>
      </div>`;
    $("#k-split").addEventListener("change", (e) => { store.set("agesplit", e.target.value); renderCompare(); });
    const AGE_LABELS = split.bands.map(bandLabel);
    const splitOf = new Map(ds.map((d) => [d.name, d.age_single.length ? splitShares(d.age_single, split) : null]));
    const ageShare = (d, a) => { const r = splitOf.get(d.name)?.find((x) => x.label === a); return r ? r.male + r.female : null; };
    const edu = (d, k) => { const r = d.education.filter((e) => /^ISCED/.test(e.classification) && eduKey(e.level) === k); return r.length && r.some((x) => x.share != null) ? r.reduce((s, x) => s + (x.share || 0), 0) : null; };
    const hhiBands = (META.hhi_columns || []).filter((c) => c.endsWith(" %"));
    const grouped = (labels, fn) => ({ labels, datasets: ds.map((d) => ({ label: d.name, data: labels.map((l) => { const v = fn(d, l); return v == null ? null : v * 100; }), ...bar(colors[d.name]) })) });
    const opts = { scales: { x: { grid: { display: false } }, ...pctAxis() }, plugins: { legend: { display: false }, tooltip: pctTip } };
    chart("k-age", { type: "bar", data: grouped(AGE_LABELS, ageShare), options: opts });
    chart("k-edu", { type: "bar", data: grouped(EDU, edu), options: opts });
    chart("k-hhi", { type: "bar", data: grouped(hhiBands.map((b) => b.replace(" %", "")), (d, l) => d.hhi ? +d.hhi[l + " %"] : null), options: opts });
    chart("k-int", { type: "bar", data: { labels: ds.map((d) => d.name), datasets: [{ data: ds.map((d) => d.internet * 100), backgroundColor: ds.map((d) => colors[d.name]), borderRadius: 4, maxBarThickness: 28 }] },
      options: { indexAxis: "y", scales: { x: { beginAtZero: true, max: 100, ticks: { callback: (v) => v + "%" } }, y: { grid: { display: false } } }, plugins: { legend: { display: false }, tooltip: pctTip } } });

    // table
    const male = (d) => d.age_gender.filter((x) => x.gender === "Male").reduce((s, x) => s + x.share, 0);
    cmpRows = [
      ["Internet penetration", (d) => d.internet, true],
      ["Male", male, true], ["Female", (d) => 1 - male(d), true],
      ...AGE_LABELS.map((a) => ["Age " + a, (d) => ageShare(d, a), true]),
      ...EDU.map((k) => ["Education: " + k, (d) => edu(d, k), true]),
      ...hhiBands.map((b) => ["HHI " + b.replace(" %", ""), (d) => (d.hhi ? +d.hhi[b] : null), true]),
      ["Median HH income (EUR est.)", (d) => (d.hhi ? +d.hhi["Median EUR Est"] : null), false, (v) => "€" + int(v)],
      ["HHI confidence", (d) => d.hhi?.Confidence ?? null, false, (v) => esc(v)],
      ["Zip records", (d) => d.zip_records, false, int],
      ["Radius search available", (d) => (d.has_coords ? "Yes" : "No"), false, (v) => v],
    ];
    $("#k-table").innerHTML = `<table class="cmp"><thead><tr><th>Metric</th>${ds.map((d) => `<th class="num">${esc(d.name)}</th>`).join("")}</tr></thead><tbody>
      ${cmpRows.map(([label, fn, isPct, f]) => { const vals = ds.map(fn); const nums = vals.filter((v) => typeof v === "number");
        const max = nums.length > 1 ? Math.max(...nums) : null;
        return `<tr><td>${esc(label)}</td>${vals.map((v) => `<td class="num ${isPct && v === max ? "best" : ""}">${v == null ? "–" : isPct ? `${pct(v)} <span class="muted">(${int(v * N)})</span>` : f(v)}</td>`).join("")}</tr>`; }).join("")}</tbody></table>`;
    $("#k-inc").innerHTML = ds.map((d) => `<div><h3><i style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${colors[d.name]}"></i> ${esc(d.name)} <span class="muted">· ${esc(d.income[0]?.currency || "")} ${esc(d.income[0]?.year || "")}</span></h3>
      <table><tbody>${d.income.map((r) => `<tr><td>${esc(r.band)}</td><td class="num">${r.share == null ? "–" : pct(r.share)}</td></tr>`).join("")}</tbody></table>
      <div class="small muted">${esc(d.income[0]?.definition || "")}</div></div>`).join("");
    window._cmp = { ds, N };
  }

  function downloadCompareCsv() {
    const { ds, N } = window._cmp || {};
    if (!ds) return;
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = [["Metric", ...ds.flatMap((d) => [d.name + " %", d.name + " n"])].map(q).join(",")];
    cmpRows.forEach(([label, fn, isPct]) => lines.push([label, ...ds.flatMap((d) => { const v = fn(d); return isPct ? [v == null ? "" : (v * 100).toFixed(2), v == null ? "" : Math.round(v * N)] : [v, ""]; })].map(q).join(",")));
    download(lines.join("\n"), `natrep_compare_${ds.map((d) => d.iso2).join("-")}.csv`);
  }

  // ------------------------------------------------------------------ locations view
  let map, layer, centerMarker, circle;
  const state = { center: null, last: null };
  function initMap() {
    if (map) return;
    map = L.map("map", { preferCanvas: true, worldCopyJump: true }).setView([50, 10], 4);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors" }).addTo(map);
    layer = L.layerGroup().addTo(map);
    map.on("click", (e) => setCenter({ lat: e.latlng.lat, lon: e.latlng.lng, label: `Map point ${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)}`, country: null }));
  }

  function setCenter(c) {
    initMap();
    state.center = c;
    $("#center-label").textContent = c.label;
    $("#center-label").title = c.label;
    $("#same-country").disabled = !c.country;
    $("#run-radius").disabled = c.lat == null;
    if (centerMarker) centerMarker.remove();
    if (c.lat != null) {
      centerMarker = L.marker([c.lat, c.lon], { icon: L.divIcon({ className: "pin", html: '<svg width="30" height="30" viewBox="0 0 24 24" fill="' + css("--pin") + '" stroke="#fff" stroke-width="1.5"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3" fill="#fff"/></svg>', iconSize: [30, 30], iconAnchor: [15, 29] }) }).addTo(map).bindTooltip(c.label);
      map.setView([c.lat, c.lon], Math.max(map.getZoom(), 10));
      runRadius();
    } else {
      $("#loc-summary").innerHTML = `<div class="note">No coordinates for this location in the source data, so a radius search isn't possible. Use “all zips in place/region” instead.</div>`;
    }
  }

  async function runRadius() {
    const c = state.center; if (!c || c.lat == null) return;
    const km = +$("#km").value;
    const country = $("#same-country").checked && c.country ? c.country : null;
    $("#loc-summary").innerHTML = loading("Finding zip codes…");
    let d;
    try { d = await radius(c.lat, c.lon, km, country); }
    catch (e) { $("#loc-summary").innerHTML = `<div class="note">${esc(e.message)}</div>`; return; }
    if (circle) circle.remove();
    circle = L.circle([c.lat, c.lon], { radius: km * 1000, color: css("--s1"), weight: 2, fillOpacity: 0.06 }).addTo(map);
    map.fitBounds(circle.getBounds(), { padding: [20, 20] });
    const csvName = `zips_${safeName(c.label.split(",")[0], 40)}_${km}km.csv`;
    showResults(d.results, `${int(d.count)} zip codes within ${km} km`, `of ${c.label}${country ? " · " + country + " only" : ""}${d.truncated ? " · list truncated" : ""}`, csvName, true);
  }

  async function loadArea({ country, level, value, within }) {
    initMap();
    $("#loc-summary").innerHTML = loading("Loading zip codes…");
    const d = await area({ country, level, value, within });
    if (circle) { circle.remove(); circle = null; }
    if (centerMarker) { centerMarker.remove(); centerMarker = null; }
    state.center = null; $("#run-radius").disabled = true;
    $("#center-label").textContent = `${value}${within ? ", " + within : ""} (${country})`;
    showResults(d.results, `${int(d.count)} zip codes`, `in ${value}${within ? ", " + within : ""}, ${country}`, `zips_${safeName(`${country}_${value}`, 50)}.csv`, false);
    const pts = d.results.filter((r) => r.lat != null).map((r) => [r.lat, r.lon]);
    if (pts.length) map.fitBounds(pts, { padding: [20, 20], maxZoom: 13 });
  }

  function showResults(res, headline, sub, csvName, withDist) {
    state.last = res;
    layer.clearLayers();
    const MAXM = 5000, color = css("--s2"), ring = css("--surface");
    res.slice(0, MAXM).forEach((r) => { if (r.lat != null) L.circleMarker([r.lat, r.lon], { radius: 4, color: ring, weight: 1, fillColor: color, fillOpacity: 0.9 })
      .bindTooltip(`<b>${esc(r.zip)}</b> ${esc(r.place || "")}<br>${esc(r.admin1 || "")}${r.distance_km != null ? ` · ${r.distance_km} km` : ""}`).addTo(layer); });
    const MAXT = 2000;
    $("#loc-summary").innerHTML = `<div><div class="count">${headline}</div><div class="small">${esc(sub)}${res.length > MAXM ? ` · map shows nearest ${int(MAXM)}` : ""}</div></div>
      <span style="flex:1"></span>
      <button class="btn ghost sm" id="copy-zips" ${res.length ? "" : "disabled"}>Copy zip list</button>
      <a class="btn sm" href="#" id="csv-zips" ${res.length ? "" : 'style="pointer-events:none;opacity:.45"'}>Download CSV</a>`;
    $("#csv-zips").addEventListener("click", (e) => { e.preventDefault(); zipsCsv(res, csvName); });
    $("#copy-zips").addEventListener("click", async () => {
      const txt = [...new Set(res.map((r) => r.zip))].join("\n");
      try { await navigator.clipboard.writeText(txt); toast(`Copied ${int(res.length)} zip codes`); } catch { toast("Copy failed – use Download CSV"); }
    });
    $("#loc-table").innerHTML = res.length ? `<table><thead><tr><th>Zip</th><th>Place</th><th>Region</th><th>Province / county</th>${withDist ? '<th class="num">km</th>' : ""}<th>Country</th></tr></thead><tbody>
      ${res.slice(0, MAXT).map((r) => `<tr><td><b>${esc(r.zip)}</b></td><td>${esc(r.place)}${r.places && r.places.length > 1 ? ` <span class="muted">+${r.places.length - 1}</span>` : ""}</td><td>${esc(r.admin1)}</td><td>${esc(r.admin2)}</td>${withDist ? `<td class="num">${r.distance_km}</td>` : ""}<td>${esc(r.iso2)}</td></tr>`).join("")}
      </tbody></table>${res.length > MAXT ? `<p class="small muted" style="padding:8px">Showing the first ${int(MAXT)} – download the CSV for all ${int(res.length)}.</p>` : ""}` : '<p class="empty">No zip codes found.</p>';
  }

  let searchTimer, searchSeq = 0;
  async function doSearch() {
    const q = $("#loc-q").value.trim(), country = $("#loc-country").value;
    const box = $("#loc-results");
    if (q.length < 2) { box.innerHTML = '<p class="hint">Type at least two characters.</p>'; return; }
    const seq = ++searchSeq;
    box.innerHTML = loading("Searching…");
    let d;
    try { d = await search(q, country, 40); }
    catch (e) { if (seq === searchSeq) box.innerHTML = `<p class="hint">Search failed: ${esc(e.message)}</p>`; return; }
    if (seq !== searchSeq) return;
    const zipHits = d.results.filter((r) => r.zip.replace(/\s/g, "").toLowerCase().startsWith(q.replace(/\s/g, "").toLowerCase()));
    let html = "";
    if (d.places.length) html += `<h3>Cities &amp; places</h3>` + d.places.map((p, i) => `<button class="hit" data-k="p${i}"><b>${esc(p.place)}</b><span class="s">${esc([p.admin1, p.country].filter(Boolean).join(", "))} · ${p.lat == null ? "no coordinates" : "radius from centre"}</span></button>${p.zips > 1 ? `<button class="hit sub" data-k="a${i}"><span class="s">↳ List all ${int(p.zips)} zip codes of ${esc(p.place)}</span></button>` : ""}`).join("");
    if (d.regions.length) html += `<h3>Regions</h3>` + d.regions.map((r, i) => `<button class="hit" data-k="r${i}"><b>${esc(r.value)}</b><span class="s">${r.level === "admin1" ? "State / region" : "Province / county"} · ${esc(r.country)} · ${int(r.n)} records</span></button>`).join("");
    if (zipHits.length) html += `<h3>Zip codes</h3>` + zipHits.map((r, i) => `<button class="hit" data-k="z${i}"><b>${esc(r.zip)}</b> ${esc(r.place)}<span class="s">${esc([r.admin1, r.country].filter(Boolean).join(", "))}</span></button>`).join("");
    box.innerHTML = html || '<p class="hint">No matches.</p>';
    $$(".hit", box).forEach((b) => b.addEventListener("click", () => {
      $$(".hit", box).forEach((x) => x.classList.remove("active")); b.classList.add("active");
      const k = b.dataset.k, i = +k.slice(1);
      if (k[0] === "p") { const p = d.places[i];
        if (p.lat == null) loadArea({ country: p.country, level: "place", value: p.place, within: p.admin1 });
        else setCenter({ lat: p.lat, lon: p.lon, label: `${p.place}, ${[p.admin1, p.country].filter(Boolean).join(", ")}`, country: p.country });
      }
      if (k[0] === "a") { const p = d.places[i]; loadArea({ country: p.country, level: "place", value: p.place, within: p.admin1 }); }
      if (k[0] === "r") { const r = d.regions[i]; loadArea({ country: r.country, level: r.level, value: r.value }); }
      if (k[0] === "z") { const r = zipHits[i]; setCenter({ lat: r.lat, lon: r.lon, label: `${r.zip} ${r.place}, ${r.country}`, country: r.country }); }
    }));
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    $("#meta").textContent = "Loading database…";
    META = await getMeta();
    COUNTRIES = await getCountries();
    $("#meta").innerHTML = `${int(META.countries)} countries · ${int(META.zip_rows)} zip records<br>Data: ${esc(META.source_file)} · loaded ${esc(META.built)}`;
    $("#loc-country").innerHTML += COUNTRIES.filter((c) => c.zip_records).map((c) => `<option>${esc(c.name)}</option>`).join("");
    $("#country-filter").addEventListener("input", renderCountryList);
    $("#compare-add").addEventListener("change", (e) => { if (!e.target.value) return; const l = store.get("compare", []); l.push(e.target.value); store.set("compare", l.slice(0, 6)); renderCompare(); });
    $("#compare-n").addEventListener("change", () => { store.set("n", +$("#compare-n").value || 1000); renderCompare(); });
    $("#compare-csv").addEventListener("click", downloadCompareCsv);
    $("#loc-q").addEventListener("input", () => {
      clearTimeout(searchTimer);
      if ($("#loc-q").value.trim().length >= 2) $("#loc-results").innerHTML = loading("Searching…");  // feedback during the debounce too
      searchTimer = setTimeout(doSearch, 250);
    });
    $("#loc-country").addEventListener("change", doSearch);
    const syncKm = (v) => { $("#km").value = v; $("#km-range").value = Math.min(100, v); };
    $("#km-range").addEventListener("input", (e) => syncKm(e.target.value));
    $("#km-range").addEventListener("change", runRadius);
    $("#km").addEventListener("change", (e) => { syncKm(e.target.value); runRadius(); });
    $("#same-country").addEventListener("change", runRadius);
    $("#run-radius").addEventListener("click", runRadius);
    const SUN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
    const MOON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';
    const isDark = () => (document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark";
    const paintToggle = () => { const b = $("#theme-toggle"); b.innerHTML = isDark() ? SUN : MOON; b.title = isDark() ? "Switch to light mode" : "Switch to dark mode"; };
    const rerender = () => { paintToggle(); if (circle) circle.setStyle({ color: css("--s1") }); route(); };
    $("#theme-toggle").addEventListener("click", () => { const t = isDark() ? "light" : "dark"; document.documentElement.dataset.theme = t; store.set("theme", t); rerender(); });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!store.get("theme", null)) rerender(); });
    paintToggle();
    if (!location.hash) location.hash = "#/country/" + encodeURIComponent(DEFAULT_COUNTRY);
    route();
  }
  boot().catch((e) => { document.body.insertAdjacentHTML("beforeend", `<div class="note" style="margin:20px">Couldn't start: ${esc(e.message)}</div>`); });
})();
