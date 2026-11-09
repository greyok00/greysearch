// Runs the compatibility model off the main thread. Everything expensive lives
// here, the model download, the WASM session, and token generation, so the
// page stays scrollable and the streamed text can actually be painted. The ONNX
// runtime's own `wasm.proxy` is not an option: with the vendored build it fails
// to start with "worker not ready" in cross-browser testing.
import { pipeline, TextStreamer, env } from './transformers.min.js';

let generator = null;
let ready = null;
const cancelled = new Set();
// Guarantees the pipeline sees one generate call at a time (see generate()).
let runChain = Promise.resolve();

// SmolLM2 ships a ChatML template; the tokenizer's own template keeps the
// prompt identical to what the model was tuned on. The literal fallback is for
// a tokenizer that carries no template at all.
function chatPrompt(tokenizer, messages) {
  try {
    const text = tokenizer?.apply_chat_template?.(messages, {
      tokenize: false, add_generation_prompt: true
    });
    if (typeof text === 'string' && text) return text;
  } catch { /* fall through to the literal template */ }
  return messages.map(m => `<|im_start|>${m.role}\n${m.content}<|im_end|>\n`).join('')
    + '<|im_start|>assistant\n';
}

// transformers.js reports per-file events; the widget wants one number. The
// weights file is the only large one, so track the largest progress seen.
function progressReporter() {
  let best = 0;
  return (event) => {
    if (!event) return;
    if (event.status === 'progress' && Number.isFinite(event.progress)) {
      best = Math.max(best, Math.min(100, event.progress));
      self.postMessage({ type: 'progress', progress: best / 100, text: 'Downloading compatibility model' });
    } else if (event.status === 'done') {
      if (/\.onnx/.test(String(event.file || ''))) {
        self.postMessage({ type: 'progress', progress: 1, text: 'Compatibility model ready' });
      }
    }
  };
}

async function init({ model, dtype, mirror, localModelPath }) {
  // A self-hosted mirror is laid out as <mirror>/<org>/<model>/<file>; the
  // default Hugging Face template would insert /resolve/<revision>/ instead.
  if (mirror && /^https?:/i.test(mirror)) {
    env.remoteHost = mirror.endsWith('/') ? mirror : `${mirror}/`;
    env.remotePathTemplate = '{model}/';
  }
  // Fully local mode: a relative model id (org/name) resolves against
  // env.localModelPath, so nothing is ever fetched from huggingface.co.
  if (localModelPath) env.allowLocalModels = true, env.localModelPath = localModelPath, env.allowRemoteModels = false;
  // The ONNX runtime defaults to a jsdelivr URL; a worker that fetched it there
  // put a third-party CDN back in the load path and could not run offline.
  if (env.backends?.onnx?.wasm) {
    env.backends.onnx.wasm.numThreads = 1;
    const wasmBase = new URL('./', import.meta.url);
    if (wasmBase.protocol === 'http:' || wasmBase.protocol === 'https:') {
      env.backends.onnx.wasm.wasmPaths = wasmBase.href;
    }
  }
  generator = await pipeline('text-generation', model, {
    dtype,
    device: 'wasm',
    progress_callback: progressReporter()
  });
  self.postMessage({ type: 'ready' });
}

self.onmessage = async (event) => {
  const message = event.data || {};
  if (message.type === 'init') {
    if (!ready) {
      ready = init(message).catch((error) => {
        self.postMessage({ type: 'error', message: String(error?.message || error) });
      });
    }
    return;
  }

  if (message.type === 'cancel') {
    // Generation cannot be stopped inside the runtime; the tokens are dropped
    // here and the run is allowed to finish in the background.
    cancelled.add(message.id);
    return;
  }

  if (message.type !== 'generate') return;
  // One run at a time: transformers.js pipelines are not reentrant, so two
  // concurrent generates would interleave one KV cache and tag each other's
  // tokens. The widget asks one question at a time today; a cancel-then-retry
  // or a second host would otherwise reach this.
  runChain = runChain.then(() => generate(message)).catch(() => {});
};

async function generate(message) {
  const { id, messages, maxTokens, temperature } = message;
  const live = () => !cancelled.has(id);
  try {
    await ready;
    if (!generator) throw new Error('model not loaded');
    const prompt = chatPrompt(generator.tokenizer, messages || []);
    const streamer = new TextStreamer(generator.tokenizer, {
      skip_prompt: true,
      skip_special_tokens: true,
      callback_function: (text) => {
        if (text && live()) self.postMessage({ type: 'token', id, text });
      }
    });
    await generator(prompt, {
      max_new_tokens: maxTokens,
      do_sample: Number(temperature) > 0,
      temperature: Number(temperature) > 0 ? Number(temperature) : 1,
      // A 135M model re-states the same clause until the budget runs out;
      // banning a repeated 3-gram turned loops into clean answers.
      no_repeat_ngram_size: 3,
      streamer,
      return_full_text: false
    });
    self.postMessage({ type: 'done', id });
  } catch (error) {
    self.postMessage({ type: 'generate-error', id, message: String(error?.message || error) });
  } finally {
    cancelled.delete(id);
  }
}
