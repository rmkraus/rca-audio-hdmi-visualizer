const DISPLAY_LEN = 32;
const PROGRESS_LEN = 16;
const STEP_MS = 28;
const TILE_STAGGER_MS = 18;
const CHARS = Array.from(" ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-/:.'&,()[]!?").concat([
  "🎵", "👤", "💿", "🟩", "⬜"
]);

const rows = {
  track: { el: document.getElementById("line-track"), tiles: [], tokens: [] },
  artist: { el: document.getElementById("line-artist"), tiles: [], tokens: [] },
  record: { el: document.getElementById("line-record"), tiles: [], tokens: [] },
  progress: { el: document.getElementById("line-progress"), tiles: [], tokens: [] },
};

function tokenize(text) {
  return Array.from(String(text || "").replaceAll("️", ""));
}

function normalizeText(text, len = DISPLAY_LEN) {
  const tokens = tokenize(text).map(ch => CHARS.includes(ch) ? ch : " ");
  while (tokens.length < len) tokens.push(" ");
  return tokens.slice(0, len);
}

function makeTile(ch) {
  const tile = document.createElement("div");
  tile.className = "tile";
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

function randomChar() {
  return CHARS[Math.floor(Math.random() * CHARS.length)];
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
      setTileChar(tile, step >= steps ? latestTarget : randomChar());
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

function buildRow(row, len = DISPLAY_LEN) {
  row.el.innerHTML = "";
  row.tiles = [];
  for (let i = 0; i < len; i++) {
    const tile = makeTile(" ");
    row.el.appendChild(tile);
    row.tiles.push(tile);
  }
}

function setRow(row, text, len = DISPLAY_LEN) {
  const next = normalizeText(text, len);
  const old = row.tokens || [];
  row.tokens = next;
  next.forEach((ch, i) => {
    if (!row.tiles[i]) return;
    if (old[i] !== ch || row.tiles[i].dataset.target !== ch) {
      setTimeout(() => animateTileTo(row.tiles[i], ch), i * TILE_STAGGER_MS);
    }
  });
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

function statusParts(data) {
  const parts = [];
  if (data.playback_status === "playing") parts.push("Playing");
  else if (data.playback_status === "stopped" || data.status === "stopped") parts.push("Stopped");
  if (data.listening || data.status === "listening") parts.push("Listening");
  if (data.backing_off || data.status === "backing_off") parts.push("Backing Off");
  if (data.ratelimit || data.status === "ratelimit") parts.push("RATELIMIT");
  return parts.length ? parts.join(" + ") : (data.status || "Waiting");
}

function updateStats(data) {
  const total = Number(data.shazam_request_count || 0);
  const rpm = Number(data.shazam_requests_per_min || 0);
  document.getElementById("stats").textContent =
    `Status: ${statusParts(data)} | Shazam Requests: ${total} reqs, ${rpm.toFixed(1)} reqs/m`;
}

function updateDisplay(data) {
  const good = data.status === "recognized";
  setRow(rows.track, good ? `🎵 ${data.title || ""}` : "");
  setRow(rows.artist, good ? `👤 ${data.artist || ""}` : "");
  setRow(rows.record, good ? `💿 ${data.album || ""}` : "");

  const p = progress(data);
  if (!good || !p) {
    setRow(rows.progress, "", PROGRESS_LEN);
  } else {
    const filled = Math.max(0, Math.min(PROGRESS_LEN, Math.round(p.ratio * PROGRESS_LEN)));
    setRow(rows.progress, "🟩".repeat(filled) + "⬜".repeat(PROGRESS_LEN - filled), PROGRESS_LEN);
  }
  updateStats(data);
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
  buildRow(rows.artist);
  buildRow(rows.record);
  buildRow(rows.progress, PROGRESS_LEN);
  updateDisplay({
    status: "recognized",
    playback_status: "playing",
    title: "How Lucky",
    artist: "John Prine",
    album: "Pink Cadillac",
    track_duration_ms: 219000,
    progress_start_seconds: 112,
    recognized_at: new Date().toISOString(),
    shazam_request_count: 0,
    shazam_requests_per_min: 0,
  });
  fetchState();
  setInterval(fetchState, 1000);
}

document.addEventListener("DOMContentLoaded", init);
