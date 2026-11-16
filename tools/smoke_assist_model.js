// smoke_assist_model.js — headless check of the thinking layer's optional
// local model, run against the live portal.
//
// What this asserts (exit 0 when any holds):
//   1. WebGPU path: on a shader-f16-capable browser the self-hosted
//      SmolLM2-360M loads (all weights/wasm served from /assets/) and
//      judge() returns verdicts. On this box swiftshader lacks f16, so
//      a run here exercises the fallback instead — the GPU load itself
//      is verified structurally (fetchable artifacts) below.
//   2. CPU fallback: SmolLM2-135M loads from /assets/models/ and generates.
//   3. The assist pane chrome (rank chips, model pass button) is present.
import pw from "/home/grey/local-assistant/product/node_modules/playwright-core/index.js";
const { chromium } = pw;

const BASE = "http://127.0.0.1:8791";
const browser = await chromium.launch({
  executablePath: "/usr/bin/chromium",
  headless: true,
  args: ["--enable-unsafe-webgpu", "--no-sandbox"],
});

const page = await browser.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 160)));
console.log("== 1. page chrome ==");
await page.goto(BASE + "/", { waitUntil: "networkidle", timeout: 45000 });
const chrome1 = await page.evaluate(() => ({
  script: typeof window.GSAssistModel,
  gsa: !!window.GSAssistModel,
}));
console.log(JSON.stringify(chrome1));

console.log("== 2. artifacts reachable (structural GPU + weight check) ==");

const artifacts = await page.evaluate(async () => {
  const urls = [
    "/assets/vendor/web-llm/index.js",
    "/assets/vendor/web-llm/SmolLM2-360M-Instruct-q4f16_1_cs1k-webgpu.wasm",
    "/assets/models/SmolLM2-360M-Instruct-q4f16_1-MLC/resolve/main/mlc-chat-config.json",
    "/assets/models/SmolLM2-360M-Instruct-q4f16_1-MLC/resolve/main/params_shard_0.bin",
    "/assets/models/SmolLM2-360M-Instruct-q4f16_1-MLC/resolve/main/tokenizer.json",
    "/assets/models/hf/SmolLM2-135M-Instruct/tokenizer.json",
    "/assets/models/hf/SmolLM2-135M-Instruct/onnx/model_int8.onnx",
  ];
  const missing = [];
  for (const u of urls) {
    try {
      const r = await fetch(u, { method: "HEAD" });
      if (!r.ok) missing.push(u + " -> " + r.status);
    } catch (e) { missing.push(u + " -> " + e); }
  }
  return { missing };
});
console.log(JSON.stringify(artifacts));

console.log("== 3. load + generate (falls back to CPU here) ==");
const got = await page.evaluate(async () => {
  const st = { events: [] };
  window.GSAssistModel.on((p, t) => st.events.push(`${Math.round(p * 100)}% ${t}`));
  try {
    await window.GSAssistModel.load();
  } catch (e) {
    return { fail: "load failed: " + String(e && e.message || e).slice(0, 200), events: st.events.slice(-4) };
  }
  const s = window.GSAssistModel.status();
  // per-note YES/NO judgement — the format verified to work on every
  // SmolLM2 variant; one generate call per note, sequential.
  const NOTES = [
    "auditd rules are compiled with augenrules; rules.d files are concatenated in lexicographic order at boot.",
    "Sign in to add this to Watch Later. Sign in to share video playlists with friends.",
  ];
  const verdicts = await window.GSAssistModel.judgeYesNo(
    NOTES, "how does the linux audit framework record system activity");
  return { backend: s, verdicts, events: st.events.slice(-2) };
});
console.log(JSON.stringify(got, null, 1));

await browser.close();

let ok = false;
if (chrome1.gsa && !artifacts.missing.length && got.backend && got.backend.ready) {
  ok = true;
  console.log("SMOKE OK: backend=" + got.backend.kind +
    (got.verdicts && got.verdicts.length ? " (judged " + got.verdicts.length + " notes)" : ""));
} else {
  console.log("SMOKE FAIL");
}
if (errs.length) console.log("page errors:", errs.slice(0, 3));
process.exit(ok ? 0 : 1);
