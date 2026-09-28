"use strict";

const TOKEN = new URLSearchParams(location.search).get("token") || "";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const FLOWS = {
  new: [["project", "Project"], ["footage", "Footage"], ["cameras", "Cameras"], ["format", "Format"], ["review", "Review"]],
  add: [["footage", "Footage"], ["cameras", "Cameras"], ["review", "Review"]],
};

const state = {
  info: null,
  current: null,          // project open in Resolve
  mode: "new",            // "new" project or "add" footage to the open project
  view: "home",
  sources: [],
  scan: null,
  choices: {},            // scan group id -> {include, profile}
  extra: [],              // cameras without footage
  transfer: "move",
  format: null,           // {aspect, width, height, fps, deliveries, custom}
};

async function api(path, body) {
  const opts = { headers: { "X-Davigen-Token": TOKEN } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ------------------------------------------------------------------ navigation
function show(view) {
  state.view = view;
  $$("section.view").forEach((s) => (s.hidden = s.dataset.view !== view));
  const flow = FLOWS[state.mode];
  const index = flow.findIndex(([v]) => v === view);
  $("#steps").hidden = index < 0;
  $("#steps").innerHTML = flow.map(([v, label], i) =>
    `<span class="${i === index ? "active" : i < index ? "done" : ""}">${esc(label)}</span>`).join("");
  ({ home: loadHome, cameras: renderGroups, format: renderFormat, review: renderReview }[view] || (() => {}))();
  window.scrollTo(0, 0);
}
function next() {
  const flow = FLOWS[state.mode];
  const i = flow.findIndex(([v]) => v === state.view);
  show(flow[i + 1][0]);
}
function back() {
  const flow = FLOWS[state.mode];
  const i = flow.findIndex(([v]) => v === state.view);
  show(i > 0 ? flow[i - 1][0] : "home");
}
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-back]")) back();
});
$("#brand").addEventListener("click", (e) => {
  e.preventDefault();
  if (!running) { reset(); show("home"); }
});

function reset() {
  Object.assign(state, { sources: [], scan: null, choices: {}, extra: [], format: null,
    transfer: state.info ? state.info.transfer : "move" });
  $("#name").value = "";
  $("#name-hint").textContent = "";
  $("#scan-result").innerHTML = "";
  $("#scan-text").textContent = "";
  $("#scan-bar").hidden = true;
  renderSources();
}

// ------------------------------------------------------------------------ home
async function loadHome() {
  renderBanners();
  renderSettings();
  // one after the other: both ask Resolve, and Resolve answers one request at a time
  try {
    state.current = await api("/api/current");
    renderCurrent(state.current);
  } catch (err) {
    $("#current").innerHTML = `<p class="muted">Couldn't read the open project: ${esc(err.message)}</p>`;
  }
  try {
    renderProjects((await api("/api/projects")).projects);
  } catch (err) {
    $("#projects").innerHTML = `<p class="muted empty">${esc(err.message)}</p>`;
  }
}

function renderBanners() {
  const out = [];
  for (const r of state.info.recovery || []) {
    const bad = r.problems.length > 0;
    out.push(`<div class="banner ${bad ? "bad" : ""}">
      <b>An unfinished footage transfer was undone.</b>
      ${r.restored} of ${r.files} files are back in their original folders${r.removed ? `, ${r.removed} partial copies removed` : ""}.
      ${bad ? `<br>Please check: ${r.problems.map(esc).join("; ")}` : ""}
      <button class="link" data-dismiss>Dismiss</button></div>`);
  }
  if (state.info.settings.online_sources === null) {
    out.push(`<div class="banner">
      <b>Allow online sources?</b> Some log profiles (e.g. DJI D-Log M) have no transform in Resolve and no published
      formula. davigen can fetch the manufacturer's official LUT from their download page, and load the camera catalog
      (Wikidata, photos from Wikimedia Commons). Nothing else is sent anywhere.
      <div class="row gap"><button class="secondary" data-online="1">Allow</button>
        <button class="ghost" data-online="0">Not now</button></div></div>`);
  }
  $("#banners").innerHTML = out.join("");
}
$("#banners").addEventListener("click", async (e) => {
  if (e.target.closest("[data-dismiss]")) {
    state.info.recovery = [];
    renderBanners();
  }
  const b = e.target.closest("[data-online]");
  if (b) await setOnline(b.dataset.online === "1");
});

async function setOnline(on) {
  state.info.settings = await api("/api/settings", { online_sources: on });
  renderBanners();
  renderSettings();
  if (on && state.info.catalog.needs_refresh) refreshCatalog();
}

function fmtFormat(f) {
  if (!f || !f.width) return "";
  return `${f.width}×${f.height} · ${f.aspect === "custom" ? "custom" : f.aspect} · ${fpsLabel(f.fps)} fps`;
}
function fpsLabel(f) { return Number.isInteger(Number(f)) ? String(Number(f)) : Number(f).toFixed(3).replace(/0+$/, ""); }

function renderCurrent(c) {
  const el = $("#current");
  if (!c.name) {
    el.innerHTML = '<p class="muted">No project is open in Resolve.</p>';
    return;
  }
  if (!c.managed) {
    el.innerHTML = `<div class="current-head"><div><h3>${esc(c.name)}</h3>
      <p class="muted">Not a davigen project – nothing to maintain here.</p></div></div>`;
    return;
  }
  const groups = c.groups.map((g) => {
    const missing = !g.input_lut && g.source;
    const status = g.input_lut ? '<span class="dot ok"></span>Input transform set'
      : '<span class="dot bad"></span>Input transform missing';
    let fix = "";
    if (missing) {
      fix = g.source.needs_online
        ? '<button class="link" data-allow-online>Allow online sources</button>'
        : `<button class="link" data-vendor="${esc(g.name)}">Choose LUT file…</button>`;
    }
    return `<tr><td class="mono">${esc(g.name)}</td><td>${esc(g.camera || "")}</td><td>${esc(g.profile || "")}</td>
      <td>${status}${missing ? `<small>${esc(g.source.detail)}</small>` : ""}</td><td>${fix}</td></tr>`;
  }).join("");
  el.innerHTML = `
    <div class="current-head">
      <div><h3>${esc(c.name)}</h3>
        <p class="muted">${esc([fmtFormat(c.format), c.created ? `created ${c.created.slice(0, 10)}` : ""].filter(Boolean).join(" · "))}</p></div>
      <button class="link" data-reveal="${esc(c.folder)}">Show in Finder</button>
    </div>
    <dl class="stats">
      <div><dt>Clips</dt><dd>${c.clips}</dd></div>
      <div><dt>Timelines</dt><dd>${c.timelines}</dd></div>
      <div><dt>Color groups</dt><dd>${c.groups.length}</dd></div>
    </dl>
    ${c.groups.length ? `<table class="groups"><tbody>${groups}</tbody></table>` : ""}
    <div class="row gap wrap">
      <button class="secondary" id="m-add">Add footage</button>
      <button class="secondary" data-flow="assign" title="Put every clip in its camera group and give new clips the node structure">Assign groups &amp; nodes</button>
      <button class="secondary" data-flow="color" title="Rebuild input/output transforms of all groups">Refresh color</button>
      <button class="secondary" data-flow="queue" title="Add all deliveries to the render queue">Queue renders</button>
    </div>
    <div class="basic">
      <div class="basic-text"><b>Basic correction</b>
        <small>Measures every clip on the current timeline and fills the nodes 01–04 (exposure, white balance,
        contrast, saturation) in a new grade version <span class="mono">DAVIGEN_AUTO</span>. Your own grade stays
        as it is – switch versions on the Color page for a before/after. Unsure clips get a marker.</small></div>
      <div class="row gap wrap">
        <label class="check"><input type="checkbox" id="b-dry"> Dry run</label>
        <label class="check" title="Overwrite existing DAVIGEN_AUTO versions (changes made inside them are lost)"><input type="checkbox" id="b-re"> Recompute all</label>
        <button class="primary" id="m-basic">Basic correction</button>
        <button class="link" id="m-basic-report">Last report</button>
      </div>
    </div>`;
}

function startBasic(options = {}) {
  if (options.recompute && !confirm("Recompute all: existing DAVIGEN_AUTO versions are overwritten, including anything you changed inside them. Continue?")) return;
  basicRun = true;
  runFlow("/api/basic", options.dry_run ? "Basic correction · dry run" : "Basic correction", options);
}
let basicRun = false;

const MAINTENANCE = {
  assign: ["/api/assign", "Assigning groups"],
  color: ["/api/color", "Refreshing color"],
  queue: ["/api/queue", "Queueing renders"],
};
$("#current").addEventListener("click", async (e) => {
  const flow = e.target.closest("[data-flow]");
  if (flow) return runFlow(...MAINTENANCE[flow.dataset.flow]);
  if (e.target.closest("#m-basic")) return startBasic({ dry_run: $("#b-dry").checked, recompute: $("#b-re").checked });
  if (e.target.closest("#m-basic-report")) return showBasicReport();
  if (e.target.closest("#m-add")) {
    state.mode = "add";
    reset();
    $("#footage-title").textContent = `Add footage to ${state.current.name}`;
    return show("footage");
  }
  const vendor = e.target.closest("[data-vendor]");
  if (vendor) {
    const r = await api("/api/vendor-lut", { group: vendor.dataset.vendor });
    if (!r.ok) return notify(r.error);
    if (confirm(`${r.file} saved. Refresh color now?`)) runFlow(...MAINTENANCE.color);
  }
  if (e.target.closest("[data-allow-online]")) { await setOnline(true); loadHome(); }
});

function renderProjects(list) {
  $("#projects").innerHTML = list.length ? list.map((p) => `
    <div class="item">
      <div><b>${esc(p.name)}</b>${p.open ? ' <span class="tag">open</span>' : ""}
        <small class="muted">${esc([fmtFormat(p.format), p.groups && p.groups.length ? `${p.groups.length} groups` : "", (p.created || "").slice(0, 10)].filter(Boolean).join(" · "))}</small>
        <small class="mono path">${esc(p.folder)}</small></div>
      <div class="row gap">
        ${p.open ? "" : `<button class="link" data-open="${esc(p.folder)}">Open in Resolve</button>`}
        <button class="link" data-reveal="${esc(p.folder)}">Show in Finder</button>
      </div>
    </div>`).join("") : '<p class="muted empty">Projects you create with davigen show up here.</p>';
}
document.addEventListener("click", async (e) => {
  const reveal = e.target.closest("[data-reveal]");
  if (reveal) {
    const r = await api("/api/reveal", { path: reveal.dataset.reveal });
    if (!r.ok) notify(r.error);
  }
  const open = e.target.closest("[data-open]");
  if (open) {
    open.disabled = true;
    open.textContent = "Opening…";
    const r = await api("/api/open-project", { folder: open.dataset.open });
    if (!r.ok) notify(r.error);
    loadHome();
  }
});

$("#new-project").addEventListener("click", () => {
  state.mode = "new";
  reset();
  $("#footage-title").textContent = "Footage";
  show("project");
  $("#name").focus();
});

// -------------------------------------------------------------------- settings
function renderSettings() {
  const i = state.info;
  $("#online").checked = !!i.settings.online_sources;
  $("#default-root").textContent = i.default_root;
  renderCatalog(i.catalog);
}
$("#online").addEventListener("change", (e) => setOnline(e.target.checked));
$("#change-root").addEventListener("click", async () => {
  const r = await api("/api/pick-folder", { prompt: "Default location for new projects" });
  if (!r.path) return;
  state.info.settings = await api("/api/settings", { default_root: r.path });
  state.info.default_root = r.path;
  renderSettings();
});

function renderCatalog(c) {
  const st = c.state || {};
  let text;
  if (st.running) text = `Updating – ${st.what || ""} ${st.total ? `${st.done}/${st.total}` : ""}`;
  else if (st.error) text = `Update failed: ${st.error}`;
  else if (!c.count) text = "Not loaded yet";
  else text = `${c.count} models · ${c.photos} photos · ${c.updated}`;
  $("#catalog-text").textContent = text;
  $("#catalog-refresh").hidden = !!st.running;
}
async function refreshCatalog() {
  const r = await api("/api/catalog/refresh", {});
  if (!r.ok) return notify(r.error);
  pollCatalog();
}
async function pollCatalog() {
  const c = await api("/api/catalog/status");
  state.info.catalog = c;
  renderCatalog(c);
  if (c.state.running) setTimeout(pollCatalog, 1500);
}
$("#catalog-refresh").addEventListener("click", refreshCatalog);

// --------------------------------------------------------------------- project
let validateTimer;
$("#name").addEventListener("input", () => {
  clearTimeout(validateTimer);
  validateTimer = setTimeout(validateName, 150);
  updateRootPreview();
});
async function validateName() {
  const name = $("#name").value.trim();
  const hint = $("#name-hint");
  hint.classList.remove("bad");
  if (!name) { hint.textContent = ""; return; }
  const r = await api("/api/validate", { name });
  hint.classList.toggle("bad", r.problems.length > 0);
  hint.textContent = r.problems.join(" · ");
}
function projectName() { return normalize($("#name").value); }
function normalize(s) {
  const map = { "Ä": "AE", "Ö": "OE", "Ü": "UE", "ä": "AE", "ö": "OE", "ü": "UE", "ß": "SS" };
  return s.replace(/[ÄÖÜäöüß]/g, (c) => map[c]).normalize("NFKD").replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_+/g, "_").toUpperCase();
}
function projectFolder() { return `${$("#root").value.replace(/\/$/, "")}/${projectName()}`; }
function updateRootPreview() {
  $("#root-preview").textContent = projectName() ? projectFolder() + "/" : "";
}
$("#root").addEventListener("input", updateRootPreview);
$("#pick-root").addEventListener("click", async () => {
  const r = await api("/api/pick-folder", { prompt: "Where should the project folder go?" });
  if (r.path) { $("#root").value = r.path; updateRootPreview(); }
});
$("#name").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#project-next").click(); });
$("#project-next").addEventListener("click", () => {
  if (!projectName()) { $("#name-hint").textContent = "Name missing"; $("#name-hint").classList.add("bad"); return; }
  next();
});

// --------------------------------------------------------------------- footage
function renderSources() {
  $("#sources").innerHTML = state.sources.length
    ? state.sources.map((p, i) => `<div class="item"><span class="mono">${esc(p)}</span>
        <button class="link" data-remove="${i}">Remove</button></div>`).join("")
    : '<p class="muted empty">No folder added yet.</p>';
  $("#scan").disabled = state.sources.length === 0;
  $$("#transfer input").forEach((r) => (r.checked = r.value === state.transfer));
  $("#footage-next").disabled = !(state.scan && state.scan.groups.length);
}
$("#sources").addEventListener("click", (e) => {
  const b = e.target.closest("[data-remove]");
  if (!b) return;
  state.sources.splice(Number(b.dataset.remove), 1);
  renderSources();
});
$("#add-source").addEventListener("click", async () => {
  const r = await api("/api/pick-folder", { prompt: "Choose a footage folder or card" });
  if (r.path && !state.sources.includes(r.path)) { state.sources.push(r.path); renderSources(); }
});
$("#transfer").addEventListener("change", (e) => { state.transfer = e.target.value; renderSpace(); });
$("#scan").addEventListener("click", async () => {
  $("#scan").disabled = true;
  $("#scan-bar").hidden = false;
  $("#scan-result").innerHTML = "";
  await api("/api/scan", { paths: state.sources });
  pollScan();
});
async function pollScan() {
  const s = await api("/api/scan");
  $("#scan-bar i").style.width = (s.total ? Math.round((s.done / s.total) * 100) : 0) + "%";
  $("#scan-text").textContent = s.total ? `Reading ${s.done} of ${s.total} files…` : "Looking for video files…";
  if (s.running) return setTimeout(pollScan, 300);
  $("#scan-bar").hidden = true;
  state.scan = s;
  state.choices = {};
  s.groups.forEach((g) => (state.choices[g.id] = { include: true, profile: g.profile }));
  state.format = null;                 // re-suggest from the new footage
  renderSources();
  renderScanResult();
}
function renderScanResult() {
  const s = state.scan;
  if (!s.groups.length) {
    $("#scan-text").textContent = "";
    $("#scan-result").innerHTML = '<p class="muted">No video files found.</p>';
    return;
  }
  const cams = new Set(s.groups.map((g) => g.camera_key)).size;
  $("#scan-text").textContent = `${s.clip_count} clips · ${cams} ${cams === 1 ? "camera" : "cameras"} · ${fmtBytes(s.size || 0)}`;
  const rows = s.groups.map((g) => `<div class="item"><span><b>${esc(g.camera_name)}</b>
    <small class="muted">${g.count} clips · ${esc(g.profile_label)}</small></span>
    <span class="conf ${g.confidence}">${confidenceLabel(g.confidence)}</span></div>`).join("");
  const errs = s.errors.length ? `<p class="hint bad">${s.errors.length} files couldn't be read: ${s.errors.slice(0, 3).map((e) => esc(e.name)).join(", ")}${s.errors.length > 3 ? "…" : ""}</p>` : "";
  $("#scan-result").innerHTML = `<div class="panel list">${rows}</div>${errs}`;
  renderSpace();
}
function renderSpace() {
  const s = state.scan;
  $("#space-hint").textContent = !s || !s.size ? "" : {
    move: `${fmtBytes(s.size)} will be moved. On the same drive this needs no extra space.`,
    copy: `${fmtBytes(s.size)} will be copied – that much free space is needed on the project drive.`,
    leave: "",
  }[state.transfer];
}
function confidenceLabel(c) {
  return { metadata: "from metadata", inferred: "inferred", guess: "guessed – please check" }[c] || c;
}
function fmtBytes(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(i > 2 ? 1 : 0)} ${units[i]}`;
}
$("#footage-next").addEventListener("click", next);
$("#skip-footage").addEventListener("click", () => {
  state.scan = null; state.choices = {};
  next();
});

// --------------------------------------------------------------------- cameras
function initials(name) {
  return name.replace(/^(Panasonic|DJI|Sony|Canon|Nikon|Fujifilm|Apple|GoPro|Insta360)\s+/i, "").replace(/LUMIX\s*/i, "").slice(0, 6);
}
function avatar(name, thumb) {
  return thumb
    ? `<div class="avatar"><img src="/catalog/thumbs/${esc(thumb)}" alt="" loading="lazy"></div>`
    : `<div class="avatar text">${esc(initials(name))}</div>`;
}
function shortOf(id) { return (state.info.shorts && state.info.shorts[id]) || id; }
function profileLabel(id) { return (state.info.profiles.find((p) => p.id === id) || { label: id }).label; }
function fmtDur(s) { const m = Math.floor(s / 60); return m ? `${m} min` : `${s} s`; }

function renderGroups() {
  const groups = state.scan ? state.scan.groups : [];
  const cards = groups.map((g) => {
    const ch = state.choices[g.id];
    const opts = g.profiles_available.map((p) => `<option value="${p.id}" ${p.id === ch.profile ? "selected" : ""}>${esc(p.label)}</option>`).join("");
    const meta = [`${g.count} clips`, fmtDur(g.duration), g.resolutions.join(", "), g.fps.map((f) => f + " fps").join(", "),
      g.bit_depth.map((b) => b + "-bit").join("/"), g.lenses.join(", ")].filter(Boolean);
    return `
    <div class="card ${ch.include ? "" : "off"}" data-id="${esc(g.id)}">
      ${avatar(g.camera_name, g.thumb)}
      <div class="card-body">
        <div class="card-title"><b>${esc(g.camera_name)}</b> <span class="conf ${g.confidence}">${confidenceLabel(g.confidence)}</span></div>
        <div class="meta">${meta.map(esc).join(" · ")}</div>
        <div class="row gap"><select data-profile>${opts}</select><span class="mono muted" data-gname>${esc(g.group_name)}</span></div>
        <div class="source" data-source></div>
      </div>
      <label class="include"><input type="checkbox" data-include ${ch.include ? "checked" : ""}> Use</label>
    </div>`;
  });
  const extras = state.extra.map((x, i) => {
    const cam = state.info.cameras.find((c) => c.key === x.camera_key);
    const ids = (x.profiles && x.profiles.length ? x.profiles : cam ? cam.profiles : state.info.profiles.map((p) => p.id));
    const opts = ids.map((pid) => `<option value="${pid}" ${pid === x.profile ? "selected" : ""}>${esc(profileLabel(pid))}</option>`).join("");
    return `
    <div class="card" data-extra="${i}">
      ${avatar(x.camera_name, x.thumb)}
      <div class="card-body">
        <div class="card-title"><b>${esc(x.camera_name)}</b> <span class="conf guess">no footage yet</span></div>
        <div class="meta">The group is prepared now – add clips later with “Add footage”.</div>
        <div class="row gap"><select data-extra-profile>${opts}</select></div>
        <div class="source" data-source></div>
      </div>
      <button class="link" data-extra-remove="${i}">Remove</button>
    </div>`;
  });
  $("#groups").innerHTML = cards.concat(extras).join("") || '<p class="muted">No footage scanned. Add the cameras you will use below, or continue with an empty project.</p>';
  groups.forEach((g) => updateSource(g.id));
  state.extra.forEach((x, i) => updateExtraSource(i));
}
async function sourceFor(profile, key, name) {
  return api("/api/source?" + new URLSearchParams({ profile, camera_key: key, camera_name: name }));
}
function renderSource(el, src) {
  if (!el || !src || !src.kind) return;
  el.className = `source ${src.kind === "missing" ? "bad" : src.needs_online ? "warn" : ""}`;
  el.innerHTML = `Input: <b>${esc(src.label)}</b> – ${esc(src.detail)}` +
    (src.needs_online ? ' <button class="link" data-allow-online>Allow online sources</button>' : "");
}
async function updateSource(id) {
  const g = state.scan.groups.find((x) => x.id === id);
  const card = $(`.card[data-id="${CSS.escape(id)}"]`);
  renderSource(card && $("[data-source]", card), await sourceFor(state.choices[id].profile, g.camera_key, g.camera_name));
}
async function updateExtraSource(i) {
  const x = state.extra[i];
  const card = $(`.card[data-extra="${i}"]`);
  renderSource(card && $("[data-source]", card), await sourceFor(x.profile, x.camera_key, x.camera_name));
}
$("#groups").addEventListener("change", (e) => {
  const card = e.target.closest(".card");
  if (!card) return;
  if (card.dataset.id) {
    const ch = state.choices[card.dataset.id];
    if (e.target.matches("[data-include]")) { ch.include = e.target.checked; card.classList.toggle("off", !ch.include); }
    if (e.target.matches("[data-profile]")) {
      ch.profile = e.target.value;
      const g = state.scan.groups.find((x) => x.id === card.dataset.id);
      $("[data-gname]", card).textContent = `G_${g.camera_key}_${shortOf(ch.profile)}`;
      updateSource(card.dataset.id);
    }
  } else if (card.dataset.extra !== undefined && e.target.matches("[data-extra-profile]")) {
    state.extra[Number(card.dataset.extra)].profile = e.target.value;
    updateExtraSource(Number(card.dataset.extra));
  }
});
$("#groups").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-extra-remove]");
  if (b) { state.extra.splice(Number(b.dataset.extraRemove), 1); renderGroups(); }
  if (e.target.closest("[data-allow-online]")) { await setOnline(true); renderGroups(); }
});

let searchTimer, lastHits = [];
$("#cam-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchCameras, 150);
});
async function searchCameras() {
  const q = $("#cam-search").value.trim();
  if (!q) { $("#cam-results").innerHTML = ""; return; }
  lastHits = (await api("/api/catalog?" + new URLSearchParams({ q }))).results;
  const brands = state.info.brands.map((b) => `<option value="${b}">${b}</option>`).join("");
  $("#cam-results").innerHTML = lastHits.map((c, i) => `
    <button class="catcard" data-hit="${i}">
      ${avatar(c.name, c.thumb)}
      <span><b>${esc(c.name)}</b><small>${esc(c.brand)}${c.year ? " · " + esc(c.year) : ""}</small></span>
    </button>`).join("") + `
    <div class="custom">
      <span class="muted">Not listed? Add <b>${esc(q)}</b> as your own camera:</span>
      <select id="custom-brand"><option value="">Brand…</option>${brands}</select>
      <button class="secondary" id="custom-add">Add</button>
    </div>
    ${lastHits.some((c) => c.thumb) ? '<p class="credit">Photos: Wikimedia Commons · Data: Wikidata</p>' : ""}`;
}
async function addExtra(cam, persist) {
  if (persist) {
    const r = await api("/api/cameras", { name: cam.name, brand: cam.brand, profiles: cam.profiles, thumb: cam.thumb });
    if (r.ok) { cam = r.camera; state.info.cameras.unshift({ ...cam, user: true }); }
  }
  state.extra.push({ camera_key: cam.key, camera_name: cam.name, profile: cam.profiles[0], profiles: cam.profiles, thumb: cam.thumb });
  $("#cam-search").value = "";
  $("#cam-results").innerHTML = "";
  renderGroups();
}
$("#cam-results").addEventListener("click", (e) => {
  const b = e.target.closest("[data-hit]");
  if (b) {
    const hit = lastHits[Number(b.dataset.hit)];
    addExtra(hit, !hit.known);   // unknown catalog models are remembered so their clips are recognised later
    return;
  }
  if (e.target.closest("#custom-add")) {
    addExtra({ name: $("#cam-search").value.trim(), brand: $("#custom-brand").value, profiles: [], thumb: "" }, true);
  }
});
$("#cameras-next").addEventListener("click", next);

// ---------------------------------------------------------------------- format
function defaultFormat() {
  const f = state.info.format;
  return { ...f.default, deliveries: [...f.default.deliveries], custom: false };
}
function aspectInfo(id) { return state.info.format.aspects.find((a) => a.id === id); }

function renderFormat() {
  if (!state.format) state.format = defaultFormat();
  const f = state.format;
  const info = state.info.format;
  const s = state.scan && state.scan.suggest;
  const same = s && s.width === f.width && s.height === f.height && Number(s.fps) === Number(f.fps);
  $("#suggest").innerHTML = s ? `<div class="banner subtle">Your footage: <b>${esc(s.source)}</b>${s.clamped ? " (Resolve Free: max. UHD)" : ""}.
    ${same ? "The master matches it." : '<button class="link" id="use-suggest">Use it as master</button>'}</div>` : "";

  $("#aspects").innerHTML = info.aspects.map((a) =>
    `<button data-aspect="${esc(a.id)}" class="${a.id === f.aspect && !f.custom ? "active" : ""}" title="${esc(a.label)}">${esc(a.id)}</button>`).join("") +
    `<button data-aspect="custom" class="${f.custom ? "active" : ""}">Custom</button>`;

  const presets = f.custom ? [] : (aspectInfo(f.aspect) || { presets: [] }).presets;
  const known = presets.some((p) => p.width === f.width && p.height === f.height);
  $("#resolution").innerHTML = presets.map((p) =>
    `<option value="${p.width}x${p.height}" ${p.width === f.width && p.height === f.height ? "selected" : ""}>${p.width} × ${p.height}</option>`).join("") +
    `<option value="custom" ${f.custom || !known ? "selected" : ""}>Custom…</option>`;
  $("#custom-size").hidden = !(f.custom || !known);
  $("#cw").value = f.width;
  $("#ch").value = f.height;

  $("#fps").innerHTML = info.fps.map((x) => `<option value="${x}" ${Number(x) === Number(f.fps) ? "selected" : ""}>${fpsLabel(x)} fps</option>`).join("");

  $("#deliveries").innerHTML = info.deliveries.map((d) => `
    <label class="option"><input type="checkbox" value="${esc(d.id)}" ${f.deliveries.includes(d.id) ? "checked" : ""}>
      <span><b>${esc(d.label)}</b></span></label>`).join("");
  renderFpsWarnings();
  updatePreview();
}
function setSize(w, h) {
  Object.assign(state.format, { width: w, height: h });
}
$("#suggest").addEventListener("click", (e) => {
  if (!e.target.closest("#use-suggest")) return;
  const s = state.scan.suggest;
  Object.assign(state.format, { width: s.width, height: s.height, fps: s.fps, aspect: s.aspect, custom: s.aspect === "custom" });
  renderFormat();
});
$("#aspects").addEventListener("click", (e) => {
  const b = e.target.closest("[data-aspect]");
  if (!b) return;
  const f = state.format;
  if (b.dataset.aspect === "custom") {
    Object.assign(f, { custom: true, aspect: "custom" });
  } else {
    const p = aspectInfo(b.dataset.aspect).presets[0];
    Object.assign(f, { custom: false, aspect: b.dataset.aspect, width: p.width, height: p.height });
  }
  renderFormat();
});
$("#resolution").addEventListener("change", (e) => {
  if (e.target.value === "custom") {
    $("#custom-size").hidden = false;
    $("#cw").focus();
    return;
  }
  const [w, h] = e.target.value.split("x").map(Number);
  setSize(w, h);
  $("#custom-size").hidden = true;
  updatePreview();
});
for (const id of ["#cw", "#ch"]) {
  $(id).addEventListener("change", () => {
    const w = Math.max(16, Math.round(Number($("#cw").value) / 2) * 2);
    const h = Math.max(16, Math.round(Number($("#ch").value) / 2) * 2);
    setSize(w, h);
    if (!state.format.custom) {
      const a = state.info.format.aspects.find((x) => {
        const p = x.presets[0];
        return p && Math.abs(p.width / p.height - w / h) < 0.01 * (w / h);
      });
      if (!a || a.id !== state.format.aspect) { state.format.custom = true; state.format.aspect = "custom"; }
    }
    renderFormat();
  });
}
$("#fps").addEventListener("change", (e) => { state.format.fps = Number(e.target.value); renderFpsWarnings(); updatePreview(); });
$("#deliveries").addEventListener("change", () => {
  state.format.deliveries = $$("#deliveries input:checked").map((i) => i.value);
  updatePreview();
});

function renderFpsWarnings() {
  const fps = Number(state.format.fps);
  const lines = [];
  for (const g of selectedGroups()) {
    for (const v of g.fps_values || []) {
      if (Math.abs(v - fps) < 0.01) continue;
      const ratio = v / fps;
      const rounded = Math.round(ratio);
      const text = rounded >= 2 && Math.abs(ratio - rounded) < 0.01
        ? `plays at normal speed, or as ${rounded}× slow motion when you conform it (Clip Attributes → Frame rate).`
        : `doesn't divide evenly into ${fpsLabel(fps)} fps – motion can stutter. Consider a matching project frame rate.`;
      lines.push(`<li><b>${esc(g.camera_name)}</b> shot ${fpsLabel(v)} fps: ${text}</li>`);
    }
  }
  $("#fps-warnings").innerHTML = lines.length ? `<ul class="notes">${lines.join("")}</ul>` : "";
}

let previewTimer, lastPreview = null;
function updatePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const f = state.format;
    lastPreview = await api("/api/preview", { format: formatBody(f) });
    const hint = $("#edition-hint");
    if (!state.info.studio && !lastPreview.fits_free) {
      const [w, h] = lastPreview.free_size;
      hint.className = "hint warn";
      hint.textContent = `Resolve Free is limited to UHD. The project will be created at ${w} × ${h}; with Studio it can use ${f.width} × ${f.height}.`;
    } else {
      hint.className = "hint";
      hint.textContent = state.info.studio ? "Resolve Studio detected – no resolution limit." : "Resolve Free – timelines up to UHD.";
    }
  }, 120);
}
function formatBody(f) {
  return { width: f.width, height: f.height, fps: f.fps, aspect: f.custom ? "custom" : f.aspect, deliveries: f.deliveries };
}
$("#format-next").addEventListener("click", next);

// ---------------------------------------------------------------------- review
function selectedGroups() {
  return (state.scan ? state.scan.groups : []).filter((g) => state.choices[g.id].include)
    .map((g) => ({ ...g, profile: state.choices[g.id].profile, group_name: `G_${g.camera_key}_${shortOf(state.choices[g.id].profile)}` }));
}
const TRANSFER_TEXT = {
  move: "moved into 01_MEDIA – put back automatically if anything fails",
  copy: "copied into 01_MEDIA and verified – originals untouched",
  leave: "imported from where they are",
};
async function renderReview() {
  const adding = state.mode === "add";
  const name = adding ? state.current.name : projectName();
  const groups = selectedGroups();
  const clips = groups.reduce((n, g) => n + g.count, 0);
  const allGroups = [...new Set(groups.map((g) => g.group_name).concat(state.extra.map((x) => `G_${x.camera_key}_${shortOf(x.profile)}`)))];
  const folder = adding ? state.current.folder : projectFolder();
  $("#review-title").textContent = adding ? `Add footage to ${name}` : "Review";
  $("#create").textContent = adding ? "Add footage" : "Create project";
  $("#create").disabled = !name || (adding && !clips && !state.extra.length);

  let formatBlock = "";
  if (!adding) {
    const f = state.format || (state.format = defaultFormat());
    const p = lastPreview || await api("/api/preview", { format: formatBody(f) });
    const size = !state.info.studio && !p.fits_free ? `${p.free_size[0]} × ${p.free_size[1]} (Free) · ${f.width} × ${f.height} with Studio` : `${f.width} × ${f.height}`;
    formatBlock = `
    <div class="block"><h3>Format</h3><p>${esc(size)} · ${esc(f.custom ? "custom" : f.aspect)} · ${fpsLabel(f.fps)} fps</p></div>
    <div class="block"><h3>Timelines</h3><ul class="mono">${p.timelines.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>
    <div class="block"><h3>Deliveries</h3><ul>${p.deliveries.map((d) => `<li>${esc(d)}</li>`).join("") || "<li>none</li>"}</ul></div>`;
  }
  $("#review").innerHTML = `
    <div class="block"><h3>Project</h3><p><b>${esc(name || "Name missing")}</b></p><p class="mono muted">${esc(folder)}/</p></div>
    <div class="block"><h3>Footage</h3><p>${clips ? `${clips} clips · ${fmtBytes(state.scan.size || 0)} · ${TRANSFER_TEXT[state.transfer]}` : "No footage"}</p>
      ${adding ? '<p class="muted">New clips go into the camera bins and to the end of the assembly timeline.</p>' : ""}</div>
    <div class="block"><h3>Color groups</h3>${allGroups.length ? `<ul class="mono">${allGroups.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : "<p>None</p>"}
      <p class="muted">Pre-clip: camera log → DaVinci Wide Gamut / Intermediate · Post-clip: → Rec.709 Gamma 2.4</p></div>
    ${formatBlock}
    ${adding ? "" : `<div class="block"><h3>Basic correction</h3>
      <label class="check"><input type="checkbox" id="basic-on" ${state.basic ? "checked" : ""}>
        Measure every clip and fill the nodes 01–04 (exposure, white balance, contrast, saturation) in a grade version
        DAVIGEN_AUTO – your first pass, ready on the Color page</label></div>`}`;
  const cb = $("#basic-on");
  if (cb) cb.addEventListener("change", () => (state.basic = cb.checked));
}
$("#create").addEventListener("click", () => {
  const body = {
    project: projectName(),
    root: $("#root").value,
    transfer: state.transfer,
    groups: (state.scan ? state.scan.groups : []).map((g) => ({ id: g.id, ...state.choices[g.id] })),
    extra: state.extra,
    format: state.format ? formatBody(state.format) : undefined,
    basic_correction: state.mode === "new" && !!state.basic,
  };
  if (state.mode === "add") runFlow("/api/add", `Adding footage to ${state.current.name}`, body);
  else runFlow("/api/create", `Creating ${body.project}`, body);
});

// ------------------------------------------------------------------ run a flow
let running = false;
async function runFlow(path, title, body = {}) {
  running = true;
  $("#run-title").textContent = title;
  $("#progress").innerHTML = "";
  $("#error").hidden = true;
  $("#after").hidden = true;
  show("run");
  try {
    const r = await api(path, body);
    if (!r.ok) throw new Error(r.error);
    pollProgress();
  } catch (err) {
    running = false;
    showError(err.message);
    $("#after").hidden = false;
  }
}
async function pollProgress() {
  let p;
  try { p = await api("/api/progress"); } catch { return setTimeout(pollProgress, 1000); }
  $("#progress").innerHTML = p.steps.map((s) => `
    <li class="${s.state}"><span class="icon"></span><div>${esc(s.label)}${s.detail ? `<small>${esc(s.detail)}</small>` : ""}</div></li>`).join("");
  if (!p.done) return setTimeout(pollProgress, 500);
  running = false;
  $("#run-title").textContent = p.error ? "Stopped" : "Done";
  if (p.error) showError(p.error);
  $("#after").hidden = false;
  $("#warnings").innerHTML = p.warnings.length
    ? `<div class="notice"><h3>Notes</h3><ul>${p.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "";
  $("#manual").innerHTML = p.manual.length
    ? `<div class="notice manual"><h3>Left to do in Resolve</h3><ul>${p.manual.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "";
  $("#report").innerHTML = basicRun && p.result && p.result.rows ? reportTable(p.result.rows, p.result.timeline) : "";
  basicRun = false;
}

// ------------------------------------------------------------ basic correction
const num = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
function reportTable(rows, timeline) {
  if (!rows.length) return '<p class="muted">No clips were measured on this timeline.</p>';
  const body = rows.map((r) => {
    const conf = r.confidence === null || r.confidence === undefined ? null : Number(r.confidence);
    const cls = r.skipped && !r.written ? "bad" : conf !== null && conf < 0.6 ? "warn" : "ok";
    const stops = r.stops === null || r.stops === undefined ? "–" : `${r.stops >= 0 ? "+" : ""}${num(r.stops)}`;
    const cct = r.cct_before ? `${Math.round(r.cct_before)} → ${Math.round(r.cct_after)} K` : "–";
    const notes = [...(r.flags || []), r.skipped && !r.written ? r.skipped : ""].filter(Boolean);
    return `<tr data-goto="${esc(r.id)}" title="Show this clip on the Color page">
      <td><span class="dot ${cls}"></span>${esc(r.name)}${r.hero ? ' <span class="tag">hero</span>' : ""}</td>
      <td class="num">${r.scene === null || r.scene === undefined ? "–" : r.scene + 1}</td>
      <td class="num">${conf === null ? "–" : num(conf)}</td>
      <td class="num">${stops}</td><td class="num">${cct}</td>
      <td class="num">${num(r.contrast)}</td><td class="num">${num(r.saturation)}</td>
      <td class="flags">${notes.map(esc).join(", ")}</td></tr>`;
  }).join("");
  return `<div class="report"><h3>Report · ${esc(timeline || "")}</h3>
    <p class="muted">Click a row to see the clip on the Color page. Stops, white balance and contrast are what went into
    DAVIGEN_AUTO; flagged clips also have a marker in Resolve.</p>
    <table class="report-table"><thead><tr><th>Clip</th><th>Scene</th><th>Confidence</th><th>Exposure</th>
    <th>White balance</th><th>Contrast</th><th>Sat</th><th>Notes</th></tr></thead><tbody>${body}</tbody></table></div>`;
}
document.addEventListener("click", async (e) => {
  const row = e.target.closest("[data-goto]");
  if (!row) return;
  const r = await api("/api/basic/goto", { id: row.dataset.goto });
  if (!r.ok) notify("That clip isn't on the current timeline anymore.");
});
async function showBasicReport() {
  const r = await api("/api/basic/report");
  $("#run-title").textContent = "Basic correction";
  $("#progress").innerHTML = "";
  $("#error").hidden = true;
  $("#warnings").innerHTML = $("#manual").innerHTML = "";
  $("#report").innerHTML = r.rows.length
    ? `<p class="muted">Last run ${esc((r.date || "").replace("T", " "))}${r.dry_run ? " · dry run" : ""}</p>` + reportTable(r.rows, r.timeline)
    : `<p class="muted">No Basic correction has run on ${esc(r.timeline || "this timeline")} yet.</p>`;
  $("#after").hidden = false;
  show("run");
}
function showError(msg) {
  $("#error").hidden = false;
  $("#error").textContent = msg;
}
$("#home").addEventListener("click", () => { reset(); state.mode = "new"; show("home"); });

function notify(msg) { alert(msg); }

// ------------------------------------------------------------ connection state
let misses = 0;
function offline(on) {
  $("#offline").hidden = !on;
  $("#app").hidden = on;
  $("#steps").hidden = $("#env").hidden = on || $("#steps").hidden;
}
async function heartbeat() {
  try {
    await api("/api/heartbeat", {});
    misses = 0;
    if (!$("#offline").hidden) location.reload();
  } catch {
    if (++misses >= 2) offline(true);
  }
}
setInterval(heartbeat, 15000);
$("#retry").addEventListener("click", heartbeat);

// ------------------------------------------------------------------------ init
(async function init() {
  try {
    state.info = await api("/api/info");
  } catch (err) {
    offline(true);
    return;
  }
  const i = state.info;
  $("#env").innerHTML = `<span>Resolve ${esc(i.resolve.replace(/^DaVinci Resolve\s*(Studio\s*)?/, ""))}</span><span class="tag">${i.studio ? "Studio" : "Free"}</span>`;
  $("#root").value = i.default_root.replace(/^~/, i.home || "~");
  $("#version-note").textContent = i.version || "";
  state.transfer = i.transfer;
  state.basic = !!i.basic_default;
  reset();
  if (i.catalog.needs_refresh && i.settings.online_sources) refreshCatalog();   // yearly, in the background
  if (location.hash === "#basic") {           // started from 'davigen Basic Correction' in Resolve's menu
    history.replaceState(null, "", location.pathname + location.search);
    return startBasic({});
  }
  show("home");
})();
