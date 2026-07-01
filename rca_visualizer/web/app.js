const DISPLAY_LEN = 32;
const PROGRESS_LEN = DISPLAY_LEN;
const PROGRESS_FILLED = "█";
const PROGRESS_EMPTY = "░";
const STEP_MS = 28;
const TILE_STAGGER_MS = 18;
const TEXT_CHARS = Array.from(" ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-/:.'&,()[]!?█░");
const TIME_CHARS = Array.from(" 0123456789:/");

const rows = {
  track: { el: document.getElementById("line-track"), tiles: [], tokens: [], charset: TEXT_CHARS },
  track2: { el: document.getElementById("line-track-2"), tiles: [], tokens: [], charset: TEXT_CHARS },
  artist: { el: document.getElementById("line-artist"), tiles: [], tokens: [], charset: TEXT_CHARS },
  record: { el: document.getElementById("line-record"), tiles: [], tokens: [], charset: TEXT_CHARS },
  progress: { el: document.getElementById("line-progress"), tiles: [], tokens: [], charset: TEXT_CHARS },
  time: { el: document.getElementById("line-time"), tiles: [], tokens: [], charset: TIME_CHARS },
};

const meters = {
  confidence: {
    needle: document.getElementById("confidence-needle"),
    readout: document.getElementById("confidence-readout"),
    value: 0,
    target: 0,
  },
  input: {
    needle: document.getElementById("input-needle"),
    readout: document.getElementById("input-readout"),
    value: 0,
    target: 0,
  },
};

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
  tile.dataset.progress = ch === PROGRESS_FILLED ? "filled" : (ch === PROGRESS_EMPTY ? "empty" : "");
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

function buildProgressBulbs(row, len = PROGRESS_LEN) {
  row.el.innerHTML = "";
  row.tiles = [];
  for (let i = 0; i < len; i++) {
    const bulb = document.createElement("div");
    bulb.className = "progress-bulb";
    bulb.style.setProperty("--glow", "0");
    row.el.appendChild(bulb);
    row.tiles.push(bulb);
  }
}

function setRow(row, text, len = DISPLAY_LEN) {
  const next = normalizeText(text, len, row.charset || TEXT_CHARS);
  const old = row.tokens || [];
  row.tokens = next;
  next.forEach((ch, i) => {
    if (!row.tiles[i]) return;
    if (old[i] !== ch || row.tiles[i].dataset.target !== ch) {
      setTimeout(() => animateTileTo(row.tiles[i], ch), i * TILE_STAGGER_MS);
    }
  });
}

function setWrappedText(firstRow, secondRow, text) {
  const tokens = tokenize(text);
  setRow(firstRow, tokens.slice(0, DISPLAY_LEN).join(""), DISPLAY_LEN);
  setRow(secondRow, tokens.slice(DISPLAY_LEN, DISPLAY_LEN * 2).join(""), DISPLAY_LEN);
}

function setProgressBulbs(row, ratio) {
  const progress = Math.max(0, Math.min(1, Number(ratio) || 0));
  const scaled = progress * row.tiles.length;
  row.tiles.forEach((bulb, i) => {
    const glow = Math.max(0, Math.min(1, scaled - i));
    bulb.style.setProperty("--glow", glow.toFixed(3));
  });
}

function clamp01(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}

function confidenceRatio(data) {
  const score = Number(data.score || 0);
  if (score > 1) return clamp01(score / 100);
  return clamp01(score);
}

function inputRatio(data) {
  const rms = Math.max(0, Number(data.rms || 0));
  if (!rms) return 0;
  // 16-bit audio RMS runs 0..32768. Use a logarithmic-ish scale so normal
  // line input movement is visible without pinning the meter constantly.
  return clamp01(Math.log10(1 + rms) / Math.log10(1 + 9000));
}

function setMeters(data) {
  meters.confidence.target = data.status === "recognized" ? confidenceRatio(data) : 0;
  meters.input.target = inputRatio(data);
  if (meters.confidence.readout) {
    meters.confidence.readout.textContent = `MATCH ${Math.round(meters.confidence.target * 100)}%`;
  }
  if (meters.input.readout) {
    meters.input.readout.textContent = `RMS ${Math.round(Math.max(0, Number(data.rms || 0)))}`;
  }
}

function needleAngle(value, jitter = 0) {
  return -54 + clamp01(value) * 108 + jitter;
}

function animateMeters() {
  const t = Date.now() / 1000;
  Object.entries(meters).forEach(([name, meter], i) => {
    meter.value += (meter.target - meter.value) * 0.12;
    const activity = Math.max(0.025, meter.value * 0.035);
    const jitter = Math.sin(t * (5.8 + i * 1.7)) * activity * 100 + Math.sin(t * (11.3 + i)) * activity * 38;
    if (meter.needle) {
      meter.needle.style.transform = `rotate(${needleAngle(meter.value, jitter).toFixed(2)}deg)`;
    }
  });
  requestAnimationFrame(animateMeters);
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
    setRow(rows.time, "", DISPLAY_LEN);
    rows.time.el.dataset.active = "0";
    return;
  }
  const current = timeParts(p.current);
  const total = timeParts(p.total);
  setRow(rows.time, `${current.mm}:${current.ss} / ${total.mm}:${total.ss}`, DISPLAY_LEN);
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

function updateStats(data) {
  const total = Number(data.shazam_request_count || 0);
  const rpm = Number(data.shazam_requests_per_min || 0);
  document.getElementById("stats").textContent =
    `Status: ${statusParts(data)} | Shazam Requests: ${total} reqs, ${rpm.toFixed(1)} reqs/m`;
}

function updateDisplay(data) {
  const good = data.status === "recognized";
  document.body.dataset.playback = data.playback_status === "stopped" || data.status === "stopped" ? "stopped" : "playing";
  document.body.dataset.listening = data.listening || data.status === "listening" ? "true" : "false";
  setWrappedText(rows.track, rows.track2, good ? `${data.title || ""}` : "");
  setRow(rows.artist, good ? `${data.artist || ""}` : "");
  setRow(rows.record, good ? `${data.album || ""}` : "");

  const p = progress(data);
  setTimeRow(good ? p : null);
  if (!good || !p) {
    setProgressBulbs(rows.progress, 0);
  } else {
    setProgressBulbs(rows.progress, p.ratio);
  }
  updateStats(data);
  setMeters(data);
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
  buildProgressBulbs(rows.progress, PROGRESS_LEN);
  updateDisplay({
    status: "recognized",
    playback_status: "playing",
    title: "Please Don't Bury Me (2020 Remaster)",
    artist: "John Prine",
    album: "Pink Cadillac",
    score: 0.88,
    rms: 2250,
    track_duration_ms: 219000,
    progress_start_seconds: 112,
    recognized_at: new Date().toISOString(),
    shazam_request_count: 0,
    shazam_requests_per_min: 0,
  });
  animateMeters();
  fetchState();
  setInterval(fetchState, 1000);
}

document.addEventListener("DOMContentLoaded", init);
