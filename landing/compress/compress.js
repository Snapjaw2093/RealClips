// RealClips video compressor.
//
// Everything here runs in the visitor's browser. We use ffmpeg.wasm: the same
// FFmpeg program from the Python pipeline, packaged to run on a web page.
// The video is never uploaded anywhere.
//
// Flow: pick a file -> choose options -> compress in a background worker ->
// preview + download.

import { FFmpeg } from "/vendor/ffmpeg/index.js";
import { toBlobURL } from "/vendor/util/index.js";

// ---------------------------------------------------------------------------
// SETTINGS - easy to change
// ---------------------------------------------------------------------------
const TARGET_MB = 30;          // the "Fit under" size
const SIZE_SAFETY = 0.92;      // aim a little under the target (sizes are approximate)
const AUDIO_KBPS = 96;         // audio quality
const MAX_SHORT_SIDE = 720;    // output is capped at 720p (on the shorter side)
const MIN_VIDEO_KBPS = 250;    // below this the picture would look terrible
// The heavy part of FFmpeg (a ~30 MB engine) is fetched from a CDN the first
// time it's needed. The small wrapper files live in /vendor on this site.
const CORE_BASE = "https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.6/dist/esm";
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const steps = ["step-pick", "step-options", "step-work", "step-done", "step-error"];

let file = null;          // the File the visitor picked
let duration = null;      // its length in seconds (null if the browser can't tell)
let ffmpeg = null;        // the FFmpeg instance (created on first use)
let previewUrl = null;    // blob URLs we should free later
let downloadUrl = null;
let cancelled = false;
const logs = [];         // the most recent FFmpeg log lines (for length probing + error details)

// ---- tiny helpers ---------------------------------------------------------

/** Show one step of the tool and hide the others. */
function show(stepId) {
  for (const id of steps) $(id).hidden = id !== stepId;
}

function fmtSize(bytes) {
  const mb = bytes / (1024 * 1024);
  return mb >= 1000 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(mb < 10 ? 1 : 0)} MB`;
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function setProgress(fraction, title, detail) {
  const pct = Math.max(0, Math.min(1, fraction || 0)) * 100;
  $("bar").setAttribute("aria-valuenow", pct.toFixed(0));
  $("bar-fill").style.width = `${pct}%`;
  if (title) $("work-title").textContent = title;
  if (detail !== undefined) $("work-detail").textContent = detail;
}

function showError(message) {
  $("error-text").textContent = message;
  show("step-error");
}

/** Ask the browser how long the video is (so we can pick a bitrate). */
function readDuration(f) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(f);
    const v = document.createElement("video");
    v.preload = "metadata";
    v.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(isFinite(v.duration) ? v.duration : null); };
    v.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
    v.src = url;
  });
}

// ---- step 1: choosing a file ---------------------------------------------

async function chooseFile(f) {
  if (!f) return;
  const looksLikeVideo = f.type.startsWith("video/") || /\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(f.name);
  if (!looksLikeVideo) {
    showError("That doesn't look like a video file. Try an MP4 or MOV.");
    return;
  }
  file = f;
  duration = await readDuration(f);
  if (!duration) duration = await probeDuration();

  $("file-name").textContent = f.name;
  $("file-info").textContent =
    fmtSize(f.size) + (duration ? ` · ${fmtTime(duration)} long` : " · length unknown");

  // The "fit under 30 MB" goal needs the length to do its math.
  const sizeOption = $("goal").querySelector('option[value="size30"]');
  sizeOption.disabled = !duration;
  if (!duration) $("goal").value = "small";
  updateHint();
  show("step-options");
}

/** Explain what the current options will do. */
function updateHint() {
  const limit = Number($("length").value);
  const len = duration ? (limit ? Math.min(limit, duration) : duration) : null;
  let text = "";
  if ($("goal").value === "size30" && len) {
    const kbps = videoKbpsFor(len);
    text = kbps < MIN_VIDEO_KBPS
      ? `${fmtTime(len)} is too long to fit ${TARGET_MB} MB with good quality. Choose a shorter length.`
      : file.size <= TARGET_MB * 1024 * 1024 && !limit
        ? `This video is already under ${TARGET_MB} MB. It will still be made smaller.`
        : `Picks a quality that lands near ${TARGET_MB} MB for ${fmtTime(len)} of video.`;
  } else if ($("goal").value === "small") {
    text = "Caps the video at 720p with good quality. Size depends on how long and detailed it is."
      + ($("speed").value === "ultrafast" ? " Faster mode makes noticeably bigger files here; choose Better quality for the smallest result." : "");
  } else if (!duration) {
    text = "This browser couldn't read the video's length, so \"Fit under\" isn't available.";
  }
  $("goal-hint").textContent = text;
}

/** Video bitrate (kbps) that makes `seconds` of video + audio fit the target size. */
function videoKbpsFor(seconds) {
  const totalBits = TARGET_MB * 1024 * 1024 * 8 * SIZE_SAFETY;
  const audioBits = AUDIO_KBPS * 1000 * seconds;
  return Math.floor((totalBits - audioBits) / seconds / 1000);
}

/** The bitrate we will actually use. A short video can have room for MORE than
 *  it started with, and asking for that would make the file bigger, so we never
 *  go above ~70% of the original's average bitrate. */
function chosenKbps(seconds) {
  const fit = videoKbpsFor(seconds);
  if (!duration) return fit;
  const sourceKbps = (file.size * 8) / 1000 / duration;
  return Math.max(MIN_VIDEO_KBPS, Math.min(fit, Math.floor(sourceKbps * 0.7)));
}

// ---- loading FFmpeg -------------------------------------------------------

async function getFFmpeg() {
  if (ffmpeg) return ffmpeg;
  const instance = new FFmpeg();
  instance.on("log", ({ message }) => { logs.push(message); if (logs.length > 60) logs.shift(); });
  setProgress(0, "Loading compressor...", "One-time download (about 30 MB)");

  // toBlobURL downloads a file (reporting progress) and turns it into a local
  // blob: URL, which is what lets the worker use files from another website.
  const onDownload = ({ received, total }) => {
    if (total) setProgress(received / total, undefined, `Downloading engine: ${fmtSize(received)} of ${fmtSize(total)}`);
  };
  const coreURL = await toBlobURL(`${CORE_BASE}/ffmpeg-core.js`, "text/javascript");
  const wasmURL = await toBlobURL(`${CORE_BASE}/ffmpeg-core.wasm`, "application/wasm", true, onDownload);
  await instance.load({ coreURL, wasmURL });
  ffmpeg = instance;
  return ffmpeg;
}

// ---- reading the video through FFmpeg ------------------------------------

/** "Mount" the file so FFmpeg can read it straight from disk instead of
 *  copying the whole video into memory (important for big files). */
async function mountInput(ff) {
  const ext = (file.name.match(/\.[a-z0-9]+$/i) || [".mp4"])[0].toLowerCase();
  const safeFile = new File([file], `input${ext}`, { type: file.type });
  await ff.createDir("/in");
  await ff.mount("WORKERFS", { files: [safeFile] }, "/in");
  return `/in/input${ext}`;
}

async function unmountInput(ff) {
  try { await ff.unmount("/in"); await ff.deleteDir("/in"); } catch { /* already gone */ }
}

/** Some formats (like iPhone HEVC video in Chrome) can't be read by the browser
 *  itself, so we ask FFmpeg for the length instead. */
async function probeDuration() {
  show("step-work");
  setProgress(0, "Reading video...", "");
  let ff = null;
  try {
    ff = await getFFmpeg();
    setProgress(1, "Reading video...", "");
    const path = await mountInput(ff);
    logs.length = 0;
    await ff.exec(["-i", path]); // no output file: FFmpeg prints the video's info and stops
    await unmountInput(ff);
    const m = /Duration: (\d+):(\d+):(\d+(?:\.\d+)?)/.exec(logs.join("\n"));
    return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) : null;
  } catch {
    if (ff) await unmountInput(ff);
    return null;
  }
}

// ---- step 3: compressing --------------------------------------------------

/** Build the FFmpeg command line for the chosen options. */
function buildArgs(inputPath, outputPath, lengthSeconds) {
  const limit = Number($("length").value);

  // Scale so the SHORTER side is at most 720 px (never enlarge). "-2" means
  // "pick the other side to keep the shape, as an even number".
  const scale =
    `scale='if(gt(iw,ih),-2,min(${MAX_SHORT_SIDE},iw))':'if(gt(iw,ih),min(${MAX_SHORT_SIDE},ih),-2)'`;

  const args = ["-i", inputPath];
  if (limit) args.push("-t", String(limit)); // keep only the first N seconds
  args.push("-vf", scale, "-c:v", "libx264", "-preset", $("speed").value, "-pix_fmt", "yuv420p");

  if ($("goal").value === "size30") {
    const k = chosenKbps(lengthSeconds);
    // A target bitrate steers the file size; maxrate/bufsize stop big spikes.
    args.push("-b:v", `${k}k`, "-maxrate", `${Math.round(k * 1.5)}k`, "-bufsize", `${k * 3}k`);
  } else {
    args.push("-crf", "28"); // constant quality: lower number = better quality, bigger file
  }

  args.push("-c:a", "aac", "-b:a", `${AUDIO_KBPS}k`, "-ac", "2", "-movflags", "+faststart", outputPath);
  return args;
}

async function compress() {
  cancelled = false;
  const limit = Number($("length").value);
  const lengthSeconds = duration ? (limit ? Math.min(limit, duration) : duration) : null;

  if ($("goal").value === "size30" && !lengthSeconds) $("goal").value = "small"; // needs the length
  if ($("goal").value === "size30" && videoKbpsFor(lengthSeconds) < MIN_VIDEO_KBPS) {
    showError(`That video is too long to fit ${TARGET_MB} MB with decent quality. Pick "First 5 minutes" or a smaller goal.`);
    return;
  }

  show("step-work");
  const startedAt = Date.now();
  const timer = setInterval(() => { $("work-time").textContent = fmtTime((Date.now() - startedAt) / 1000); }, 500);

  try {
    const ff = await getFFmpeg();
    setProgress(0, "Compressing...", "Starting");

    // FFmpeg prints lines like "time=00:01:23.45". We read that to know how
    // far along it is.
    const onLog = ({ message }) => {
      const m = /time=(\d+):(\d+):(\d+(?:\.\d+)?)/.exec(message);
      if (m && lengthSeconds) {
        const t = Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]);
        setProgress(t / lengthSeconds, undefined, `${fmtTime(t)} of ${fmtTime(lengthSeconds)} processed`);
      }
    };
    ff.on("log", onLog);

    const inputPath = await mountInput(ff);

    const code = await ff.exec(buildArgs(inputPath, "out.mp4", lengthSeconds));
    ff.off("log", onLog);
    if (cancelled) return;
    if (code !== 0) {
      const detail = logs.slice(-3).join(" ").trim().slice(0, 160);
      throw new Error("FFmpeg could not process this video. It may use a format this browser tool can't read." + (detail ? ` (${detail})` : ""));
    }

    const data = await ff.readFile("out.mp4");
    await ff.deleteFile("out.mp4");
    await unmountInput(ff);
    showResult(data);
  } catch (err) {
    if (cancelled) return;
    ffmpeg = null; // start fresh next time, in case the worker is in a bad state
    console.error(err);
    showError(
      /memory|abort|out of bounds|unreachable/i.test(String(err?.message))
        ? "The browser ran out of memory. Try \"First 5 minutes\", a smaller goal, or a different browser."
        : String(err?.message || err)
    );
  } finally {
    clearInterval(timer);
  }
}

function cancel() {
  cancelled = true;
  if (ffmpeg) { ffmpeg.terminate(); ffmpeg = null; } // stops the work immediately
  show("step-options");
}

// ---- step 4: the result ---------------------------------------------------

function showResult(data) {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  const blob = new Blob([data.buffer], { type: "video/mp4" });
  previewUrl = downloadUrl = URL.createObjectURL(blob);

  $("size-before").textContent = fmtSize(file.size);
  $("size-after").textContent = fmtSize(blob.size);
  const pct = Math.round((1 - blob.size / file.size) * 100);
  const overTarget = $("goal").value === "size30" && blob.size > TARGET_MB * 1024 * 1024;
  $("saved").textContent = overTarget
    ? `That came out a little over ${TARGET_MB} MB. Try "First 5 minutes", or run it again with a shorter length.`
    : pct > 0 ? `${pct}% smaller.` : "This video was already small, so there's not much to save.";

  $("preview").src = previewUrl;
  const base = file.name.replace(/\.[^.]+$/, "");
  $("download").href = downloadUrl;
  $("download").setAttribute("download", `${base}-small.mp4`);
  show("step-done");
}

function reset() {
  file = null; duration = null;
  $("file").value = "";
  $("preview").removeAttribute("src");
  show("step-pick");
}

// ---- wiring ---------------------------------------------------------------

if (!window.WebAssembly || !window.Worker) {
  $("drop").hidden = true;
  $("unsupported").hidden = false;
} else {
  $("file").addEventListener("change", (e) => chooseFile(e.target.files[0]));
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => chooseFile(e.dataTransfer.files[0]));

  $("goal").addEventListener("change", updateHint);
  $("length").addEventListener("change", updateHint);
  $("speed").addEventListener("change", updateHint);
  $("go").addEventListener("click", compress);
  $("cancel").addEventListener("click", cancel);
  $("change").addEventListener("click", reset);
  $("again").addEventListener("click", reset);
  $("retry").addEventListener("click", () => (file ? show("step-options") : reset()));
}
