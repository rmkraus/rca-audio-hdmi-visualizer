const DISPLAY_LEN = 32;
const STEP_MS = 28;
const TILE_STAGGER_MS = 18;
const TEXT_CHARS = Array.from(" ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-/:.'&,()[]!?");
const TIME_CHARS = Array.from(" 0123456789:/");

const rows = {
  track: { el: document.getElementById("line-track"), tiles: [], tokens: [], charset: TEXT_CHARS },
  track2: { el: document.getElementById("line-track-2"), tiles: [], tokens: [], charset: TEXT_CHARS },
  artist: { el: document.getElementById("line-artist"), tiles: [], tokens: [], charset: TEXT_CHARS },
  record: { el: document.getElementById("line-record"), tiles: [], tokens: [], charset: TEXT_CHARS },
  progress: { el: document.getElementById("line-progress") },
  time: { el: document.getElementById("line-time"), tiles: [], tokens: [], charset: TIME_CHARS },
};

const runtimeConfig = {
  recognitionMinRms: null,
};

let lastState = { status: "waiting" };

function tokenize(text) {
  return Array.from(String(text || "").replaceAll("️", ""));
}

function normalizeText(text, len = DISPLAY_LEN, charset = TEXT_CHARS) {
  const tokens = tokenize(text).map(ch => charset.includes(ch) ? ch : (charset.includes(" ") ? " " : "0"));
  while (tokens.length < len) tokens.push(charset.includes(" ") ? " " : "0");
  return tokens.slice(0, len);
}

function makeTile(ch, rowName = "", charsetName = "text") {
  const tile = document.createElement("div");
  tile.className = "tile";
  if (rowName) tile.dataset.row = rowName;
  tile.dataset.charset = charsetName;
  tile.dataset.char = ch;
  tile.dataset.busy = "0";
  tile.innerHTML = `
    <div class="half top"><div class="char"></div></div>
    <div class="half bottom"><div class="char"></div></div>
    <div class="seam"></div>
    <div class="hinge left"></div>
    <div class="hinge right"></div>
  `;
  setTileChar(tile, ch);
  return tile;
}

function setTileChar(tile, ch) {
  tile.querySelector(".top .char").textContent = ch;
  tile.querySelector(".bottom .char").textContent = ch;
  tile.dataset.char = ch;
}

function pulseTile(tile) {
  tile.classList.remove("animating");
  void tile.offsetWidth;
  tile.classList.add("animating");
  setTimeout(() => tile.classList.remove("animating"), 130);
}

function randomChar(tile) {
  const chars = tile.dataset.charset === "time" ? TIME_CHARS : TEXT_CHARS;
  return chars[Math.floor(Math.random() * chars.length)];
}

function animateTileTo(tile, targetChar) {
  tile.dataset.target = targetChar;
  const current = tile.dataset.char || " ";
  if (current === targetChar) return;
  if (tile.dataset.busy === "1") return;
  tile.dataset.busy = "1";

  function runCycle() {
    const finalTarget = tile.dataset.target || " ";
    const currentChar = tile.dataset.char || " ";
    if (currentChar === finalTarget) {
      tile.dataset.busy = "0";
      return;
    }

    const steps = Math.min(8, 2 + Math.floor(Math.random() * 5));
    let step = 0;
    function tick() {
      step += 1;
      const latestTarget = tile.dataset.target || " ";
      setTileChar(tile, step >= steps ? latestTarget : randomChar(tile));
      pulseTile(tile);
      if (step < steps) {
        setTimeout(tick, STEP_MS);
      } else if ((tile.dataset.char || " ") !== (tile.dataset.target || " ")) {
        setTimeout(runCycle, STEP_MS);
      } else {
        tile.dataset.busy = "0";
      }
    }
    tick();
  }

  runCycle();
}

function buildRow(row, len = DISPLAY_LEN, charsetName = "text") {
  row.el.innerHTML = "";
  row.tiles = [];
  for (let i = 0; i < len; i++) {
    const tile = makeTile(charsetName === "text" ? " " : "0", row.el.id || "", charsetName);
    row.el.appendChild(tile);
    row.tiles.push(tile);
  }
}


function setRow(row, text, len = DISPLAY_LEN, options = {}) {
  const next = normalizeText(text, len, row.charset || TEXT_CHARS);
  const old = row.tokens || [];
  row.tokens = next;
  next.forEach((ch, i) => {
    if (!row.tiles[i]) return;
    if (old[i] !== ch || row.tiles[i].dataset.target !== ch) {
      if (options.instant) {
        row.tiles[i].dataset.target = ch;
        row.tiles[i].dataset.busy = "0";
        setTileChar(row.tiles[i], ch);
      } else {
        setTimeout(() => animateTileTo(row.tiles[i], ch), i * TILE_STAGGER_MS);
      }
    }
  });
}

function setWrappedText(firstRow, secondRow, text) {
  const tokens = tokenize(text);
  setRow(firstRow, tokens.slice(0, DISPLAY_LEN).join(""), DISPLAY_LEN);
  setRow(secondRow, tokens.slice(DISPLAY_LEN, DISPLAY_LEN * 2).join(""), DISPLAY_LEN);
}

function setProgressLine(row, ratio) {
  if (!row || !row.el) return;
  const progress = clamp01(ratio);
  row.el.style.setProperty("--progress", `${(progress * 100).toFixed(2)}%`);
  row.el.dataset.complete = progress >= 0.995 ? "true" : "false";
}

function clamp01(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}


function parseDate(text) {
  if (!text) return null;
  const d = new Date(text);
  return isNaN(d.getTime()) ? null : d;
}

function progress(data) {
  if (data.status !== "recognized" || !data.track_duration_ms || data.progress_start_seconds == null) return null;
  const recognized = parseDate(data.recognized_at);
  const elapsed = recognized ? Math.max(0, (Date.now() - recognized.getTime()) / 1000) : 0;
  const total = data.track_duration_ms / 1000;
  const current = Math.min(total, Math.max(0, Number(data.progress_start_seconds) + elapsed));
  return { current, total, ratio: total > 0 ? current / total : 0 };
}

function timeParts(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const mm = Math.floor(seconds / 60);
  const ss = seconds % 60;
  return {
    mm: String(mm).padStart(2, "0"),
    ss: String(ss).padStart(2, "0"),
  };
}

function setTimeRow(p) {
  if (!p) {
    setRow(rows.time, "", DISPLAY_LEN, { instant: true });
    rows.time.el.dataset.active = "0";
    return;
  }
  const current = timeParts(p.current);
  const total = timeParts(p.total);
  setRow(rows.time, `${current.mm}:${current.ss} / ${total.mm}:${total.ss}`, DISPLAY_LEN, { instant: true });
  rows.time.el.dataset.active = "1";
}

function statusParts(data) {
  const parts = [];
  if (data.playback_status === "playing") parts.push("Playing");
  else if (data.playback_status === "stopped" || data.status === "stopped") parts.push("Stopped");
  if (data.listening || data.status === "listening") parts.push("Listening");
  if (data.backing_off || data.status === "backing_off") parts.push("Backing Off");
  if (data.ratelimit || data.status === "ratelimit") parts.push("RATELIMIT");
  return parts.length ? parts.join(" + ") : (data.status || "Waiting");
}

function numericLabel(value, digits = 0) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "--";
  return n.toFixed(digits);
}

function updateStats(data) {
  const total = Number(data.shazam_request_count || 0);
  const rpm = Number(data.shazam_requests_per_min || 0);
  const rms = numericLabel(data.rms, 1);
  const threshold = numericLabel(runtimeConfig.recognitionMinRms, 1);
  document.getElementById("stats").textContent =
    `Status: ${statusParts(data)} | RMS: ${rms} | Silence Threshold: ${threshold} | Shazam Requests: ${total} reqs, ${rpm.toFixed(1)} reqs/m`;
}

function updateDisplay(data) {
  lastState = data;
  const good = data.status === "recognized";
  document.body.dataset.playback = data.playback_status === "stopped" || data.status === "stopped" ? "stopped" : "playing";
  document.body.dataset.listening = data.listening || data.status === "listening" ? "true" : "false";
  setWrappedText(rows.track, rows.track2, good ? `${data.title || ""}` : "");
  setRow(rows.artist, good ? `${data.artist || ""}` : "");
  setRow(rows.record, good ? `${data.album || ""}` : "");
  refreshTimerAndProgress();
  updateStats(data);
}

function refreshTimerAndProgress() {
  const good = lastState.status === "recognized";
  const p = progress(lastState);
  setTimeRow(good ? p : null);
  setProgressLine(rows.progress, good && p ? p.ratio : 0);
}


async function fetchConfig() {
  try {
    const res = await fetch(`/api/config?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    runtimeConfig.recognitionMinRms = Number(data.recognition_min_rms);
  } catch (err) {
    runtimeConfig.recognitionMinRms = null;
  }
}

async function fetchState() {
  try {
    const res = await fetch(`/api/now-playing?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    updateDisplay(await res.json());
  } catch (err) {
    updateStats({ status: "error", message: String(err), shazam_request_count: 0, shazam_requests_per_min: 0 });
  }
}

function init() {
  buildRow(rows.track);
  buildRow(rows.track2);
  buildRow(rows.artist);
  buildRow(rows.record);
  buildRow(rows.time, DISPLAY_LEN, "time");
  updateDisplay({
    status: "recognized",
    playback_status: "playing",
    title: "Please Don't Bury Me (2020 Remaster)",
    artist: "John Prine",
    album: "Pink Cadillac",
    track_duration_ms: 219000,
    progress_start_seconds: 112,
    recognized_at: new Date().toISOString(),
    shazam_request_count: 0,
    shazam_requests_per_min: 0,
  });
  fetchConfig().finally(fetchState);
  setInterval(fetchState, 2000);
  setInterval(refreshTimerAndProgress, 1000);
  setInterval(fetchConfig, 30000);
}

document.addEventListener("DOMContentLoaded", init);
