# CAPTCHA BENCH - acceptance and benchmark sheet (2026-11-05)

The local captcha ladder shipped green. The full regression suite passes at
the head this sheet was written against: first written at **269 passed**
(the task-12 wiring commit, the grid library), then re-proven at **293
passed** after the 2026-11-05 model-wiring pass added the detector, CLIP,
and no-weights provider strategies (`python3 -m pytest tests/ -q`, system
Python, HEAD = the llm-rung-coverage commit 975befe). Every
behavior the suite can prove without touching the live web is proven by that
run; the four real-network checks at the bottom of this sheet are the owner's
manual live probe, per the standing live-probe convention: the sheet states
which rows are proven and which need your hand, and nothing here claims a
live-web result it does not have.

**Where to start:** read "What the suite already proves," then run the
Live-probe checklist below - it is four one-sentence checks in a real
browser, and reCAPTCHA (item 1) is the one that exercises the whole ladder.

## What the suite already proves

The hermetic suite covers the ladder end to end with fake frames, so none of
this needs the network: provider detection for all three providers, both
rung orderings and all three knob filters, the round cap stopping after
GREYSEARCH_CAPTCHA_MAX_ROUNDS, an honest skip record when the ladder
exhausts, the kill switch returning an un-engaged record with no model call,
the Turnstile behavioral rung clicking a checkbox and honest-absent when
there is none, the corrupt-model-path fall-through returning None instead of
raising, the LLM wall-clock timeout parsing, the wait knobs' two-phase
ordering and junk-value fallbacks, and the ledger's captcha_skip /
captcha_solved notes keeping their exact shape. The 2026-11-05 wiring pass
extended the same hermetic cover to everything in the coverage map below:
the extended provider scan (arkose/funcaptcha frames, recaptcha-v3 badges,
text-captcha pairs), the two-mode capture (`pick` vs `points`), the
normalized point dispatch, the detector and CLIP fall-throughs inside the
ladder, the transcription contract, and the honest-None degradation
records. 293/293, zero failures -
including, because the pinned weight libraries sit on this box, the
real-weights tests: every grid graph's decompiled tensor contract
([1,3,64,64] -> [1,2]) with a live load + 9-tile classify through the
actual session, the detector routing table and locate contract on the
staged YOLO graphs, and the CLIP pair's classify contract on the staged
ViT-B-32 weights.

## The knobs

Defaults below are read straight from the code, with the file and line each
default lives on. The first six belong to the solver; the last two belong to
the browser_session wait contract that wraps every solve attempt, and the
seventh (the older single-length pin) overrides both of those when set.

| Knob | Default | Meaning |
|---|---|---|
| `GREYSEARCH_CAPTCHA_SOLVER` | on (off/0/false disables) | The kill switch: off means detect and skip honestly, no model is ever consulted (captcha_solve.py:40-52). |
| `GREYSEARCH_CAPTCHA_RUNGS` | `llm,cpu` (comma list) | The rung order the ladder tries per round; unknown names drop, gpu also needs its flag on, cpu also needs weights present (captcha_solve.py:42-78). |
| `GREYSEARCH_CAPTCHA_GPU` | off | Turns the gpu rung on, and only then if nvidia-smi says under half the VRAM is in use - the gpu-natural-mode rule is never overridden (captcha_model.py:40-75). |
| `GREYSEARCH_CAPTCHA_MAX_ROUNDS` | 3 (clamped 1-10) | Round cap per challenged page: capture, run the ladder, click; exhausting it is an honest skip record (captcha_solve.py:45-46, 81-86). |
| `GREYSEARCH_CAPTCHA_LLM_TIMEOUT_S` | 20.0 | The llm rung's wall-clock ceiling in seconds; a non-numeric or non-positive value falls back to 20 rather than raising (captcha_solve.py:94-113). |
| `GREYSEARCH_CAPTCHA_MODEL_PATH` | `models/hcaptcha/` in the tree | Overrides where the ONNX loader looks for the per-model grid LIBRARY (a directory of .onnx classifiers, no longer one file); a path whose directory holds no models degrades the cpu rung out of the ladder (captcha_model.py, resolve_model_dir / has_model). |
| `GREYSEARCH_CAPTCHA_WAIT_MIN_S` | 30.0 s | The WAIT contract's first phase after detection, and the bounded re-wait that governs whether a solve claim is handed back (browser_session.py:218, 281). |
| `GREYSEARCH_CAPTCHA_WAIT_MAX_S` | 60.0 s (never below min) | The WAIT contract's second phase; junk or negative values fall back to their defaults (browser_session.py:219, 283-285). |

The pre-existing `GREYSEARCH_CAPTCHA_WAIT_S` (default 45.0 s,
browser_session.py:217) still pins both wait phases to its one value when
set, so the old single-length contract and the two-phase one stay one
machinery.

The 2026-11-05 wiring pass added seven more knobs, same rules (junk falls
back, never raises):

| Knob | Default | Meaning |
|---|---|---|
| `GREYSEARCH_CAPTCHA_YOLO_PATH` | `models/yolo/` in the tree | Detector weight directory override (captcha_model.py:262). |
| `GREYSEARCH_CAPTCHA_YOLO_CONF` | 0.5 | A box must score at least this to count as a locate (captcha_model.py:255, 440). |
| `GREYSEARCH_CAPTCHA_YOLO_MAX_POINTS` | 8 | Ceiling on clicked points per round from the detector (captcha_model.py:450). |
| `GREYSEARCH_CAPTCHA_CLIP_PATH` | `models/clip/` in the tree | CLIP visual/textual pair directory override (captcha_model.py:658). |
| `GREYSEARCH_CAPTCHA_CLIP_FAMILY` | `_ViT-B-32.openai` | Which visual_CLIP/textual_CLIP pair answers first; the loader falls through the other families (captcha_model.py:649, 689). |
| `GREYSEARCH_CAPTCHA_CLIP_MIN_SIM` | 0.24 | The absolute cosine floor a tile must clear on top of the margin rule (captcha_model.py:653, 721). |
| `GREYSEARCH_CAPTCHA_VENDOR` | `grid` (install.sh) | Vendor scope: default fetches/verifies the grid library only; `all` also fetches the yolo/clip pins. A vendor run NEVER deletes staged weight files (install.sh:117). |

## Per-challenge rung latency

The ladder's `llm ms` column is intentionally blank: probing it means calling
the resident model gateway, which is the owner's manual probe per the
live-probe convention, so those cells wait for your run and say
owner-live-probe. The `cpu ms` column is a real offline measurement from this
box (command and raw numbers quoted under it).

| Challenge | Rung ladder actually run | llm ms | cpu ms | Proven by |
|---|---|---|---|---|
| reCAPTCHA wall (3x3 grid) | llm, then cpu, up to 3 rounds | owner-live-probe | ~631 ms (9 tiles, median of 5) | hermetic suite + this box's timed run |
| hCaptcha wall (task grid) | llm, then cpu, up to 3 rounds | owner-live-probe | ~75 ms (1 tile); a 12-tile grid costs ~840 ms per round | hermetic suite + this box's timed run |
| Turnstile widget | behavioral only (checkbox click, no model) | n/a | n/a | hermetic suite (solve_turnstile tests) |

The `cpu ms` above is `classify()` end to end on real challenge-view tile
bytes - 3x3 tiles sliced from hcaptcha-challenger's own published grid
screenshot (the upstream test asset at
tests/challenge_view/image_label_binary/1.png, ~180x180 px a tile, PNG) -
through the vendored loader, featurizer included, because that is the cost
the ladder's `latency_ms[rung]` actually records. The same run answers the
grid: prompt "please select all images containing a car" through
`classify()` returned picks [2, 4, 7] against the screenshot's true tile
contents (cars at 2, 4, 6, 7 - three of four), "select the microwave"
returned [3] exactly, "dolphin" returned [1] of the screenshot's two
dolphin tiles (margins sit under 1 logit for the second - these vintage
models are conservative on 2024-style GAN-overlaid tiles; see the honest
story below).

Raw: the benchmark loaded `captcha_model.load_classifier("cpu")` and ran
`classify()` over the 9 sliced tile PNGs 5 times: 719.1 / 578.2 / 622.2 /
631.4 / 1,099.6 ms (median 631.4); a single tile ran 74.9 ms. Splitting the
cost apart: featurizing 9 tiles took ~523 ms (the pure-Python featurizer -
no numpy, by design; the numpy reshape path measured slower at ~522 ms
because translating 110 k Python floats into arrays eats the win), and the
9 ONNX session runs took ~110 ms, so the per-tile cost is roughly 80 percent
featurizer, 20 percent graph. The practical read: a full 3x3 grid round
costs the cpu rung about 0.07 s per tile end to end (~0.6 s per round), and
that cost is paid once per round, capped by
`GREYSEARCH_CAPTCHA_MAX_ROUNDS`. Prompt matching scans the 487-file library
in ~5 ms per call and is cached per model after first use.

### The cpu column's honest story

The vendored weights (models/SOURCE, models/CHECKSUMS) are now the REAL
solver: hcaptcha-challenger's per-prompt binary tile classifiers (GPL-3.0,
downloaded to this box's disk only, never redistributed), decompiled with
the `onnx` protobuf reader and wired fresh - no upstream source copied.
Decompiled contract, identical for every model sampled: input
float32[1,3,64,64] NCHW RGB, output float32[1,2] raw logits, class 0 (the
argmax-positive side) = the tile matches the challenge prompt. Preprocessing
is NOT in the graph (the ops are Conv/Relu/Add/Gemm only), so the pipeline
was derived and validated against upstream's own ground-truth tile set
(image_label_binary/off_road_vehicle: 7 positives, 33 negatives, human-labeled
by their filenames): decode, squash both axes to 64x64, RGB, /255, nothing
further - 40/40 correct sideings under this recipe, while mean/std- and
plus/minus-0.5-normalized variants both flip real negatives. Two disclosed
limits, stated because the graph cannot prove them away:

1. 144-pixel WATERMARK-style tiles: upstream's consumer runs a
   colored non-local-means denoise before the resize when a tile's height is
   exactly 144 - a step invisible to the graph and not replicable in the
   featurizer's stdlib-only decoder, so such tiles are fed un-denoised and
   may score noisier than upstream would. The lane's patchright PNG captures
   are unaffected unless a challenge view itself serves that height.
2. Vintage vs today's challenges: the library's models were trained on
   2023-era hcaptcha styles. On the 2024-style GAN-overlaid test grid above
   they still answer with real signal, but the second dolphin tile missed by
   under 1 logit, and challenges with no per-tile visual class at all -
   "select all numbers smaller than 45", relative comparisons - have no
   library match and answer None honestly; upstream's modern line routes
   those through its LLM lane, which is exactly what this ladder's llm rung
   is. Accuracy on live grids is the owner's live-probe row; the suite and
   the ground-truth set prove the wiring, not that number.

## Coverage map - every challenge type, rung by rung (2026-11-05 wiring pass)

This is the map the wiring pass was judged against: each challenge family the
lane can meet, the rung that answers it, and what happens when that rung has
nothing (the honest-None rule - a miss is recorded, never faked). The
"proven by" column names the hermetic test or the measured probe behind the
row.

| Challenge family | Rung that answers | What happens | Proven by |
|---|---|---|---|
| hcaptcha / reCAPTCHA image grid (tile pick) | llm, then cpu: grid classifier, then CLIP matcher | tile screenshots captured (`capture_grid`, mode `pick`), llm asked for picks; else the per-prompt binary classifier; else CLIP cosine-threshold picks (sims at or above `mean + 0.5*std`, absolute floor 0.24) | hermetic suite (ladder fall-through, cap-exhausted) + real-weights tests |
| coordinate challenge (`points` mode: ONE image element) | llm, then cpu: YOLO detector | normalized click points from the llm or the routed detector, clicked all-or-nothing inside the element's bounding box (`dispatch_points`) | hermetic suite (points capture, dispatch, detector ladder) + real-weights locate probe |
| Turnstile widget | behavioral only | checkbox click or honest no-clickbox; no model consulted | hermetic suite (solve_turnstile) |
| Arkose / FunCaptcha, match-tile shape | llm only | same frame capture as a grid; picks clicked; a useless or absent llm answer is honest detect-and-record | hermetic suite (arkose match variant, miss honesty) |
| Arkose / FunCaptcha, rotation shape (`points`) | refused by design | click semantics cannot be verified without its per-type DOM knowledge, so the llm rung is not trusted to click - detected and recorded | hermetic suite (rotation variant) |
| reCAPTCHA v3 | none (by design) | score-based widget, no visual challenge exists - detect-and-record; the page's token flow governs | hermetic suite (v3 record shape) |
| legacy text captcha (image + code input) | llm only | transcribe (alphanumeric-cleaned; empty or >24 chars is an honest miss), type, submit; successful typing is the only `solved` | hermetic suite (transcribe contract, both miss paths) |
| prompt with NO routed model at all | ladder reports honestly | rungs drop out of the ladder rather than pretending; exhaust = honest skip record | hermetic suite (no-llm degradation, unrouted stems) |

### The detector rung's measured story

Twenty-five-plus prompt probes route through the upstream
`objects2023.yaml` table to the right vendored YOLO graph, including the
special phrasings (`not an animal`, `can run`, `the one piece`,
`animal heads`, hexagon/spiral/star-bricks), digit prompts via number words,
and `head of the <animal>` style prompts; species prompts derive from the
model stem itself (`animal_<species>` -> the species). On the box's real
probe assets, `COCO2020_yolov8m` located cat (conf 0.41/0.50) and dog
(0.48/0.48) tiles, and the panda-head graph located a goose-head element at
normalized (0.4452, 0.4282) in ~800 ms - the decode/resize/box/point path
all live. Disclosed gaps: the `onclick_*` and `yolov5/yolov6` stems carry no
routable label phrasing (they stay loadable but unrouted; digit-keypoint
prompts need a pixel-space finetune that was not replicated), and prompts
unroutable from the table fall through to the llm/clip rungs rather than a
wrong model.

### The clip rung's measured story

The vendored ViT-B-32 pair + the MIT BPE tokenizer (validated against its
anchors, SOT 49406 / EOT 49407) classify the pinned 3x3 challenge
screenshot: per-tile argmax 8/9, prompt-sentence threshold picks exact 9/9
- the rung's default family (`GREYSEARCH_CAPTCHA_CLIP_FAMILY`
`_ViT-B-32.openai`). RN50 is weaker on the same tiles (6-7/9) and stays
selectable. Disclosed: the resize is a bilinear near-fit of the reference
bicubic pipeline (numbers above measured under it), and the vendored
ViT-L-14 visual graph is corrupt upstream (fails onnx checker) and drops
out at session build with a cached refusal rather than half-working.

## Owner-run live-probe checklist

**Where to start:** open the slot browser lane on a site that actually
challenges, and take the six checks in order; each is one sentence, and a
failing check is a finding to report against, not a knob to turn up.

1. A real Google reCAPTCHA wall is detected and the full ladder engages
   (`GREYSEARCH_CAPTCHA_RUNGS` order, llm then cpu), with the record carrying
   provider, per-rung latency, rounds, and a solved or honest-exhausted
   boolean - this is the row where the blank `llm ms` column gets filled in.
2. A real Turnstile widget passes behaviorally: the checkbox click lands
   (or honest no-clickbox is recorded), the existing two-phase wait contract
   completes the proof-of-work, and no model is consulted.
3. `GREYSEARCH_CAPTCHA_SOLVER=off` skips honestly: the lane records
   `captcha_skip` in the ledger metadata, no model call happens, and the
   record shape is unchanged from the pre-solver contract.
4. `GREYSEARCH_CAPTCHA_MODEL_PATH` pointed at a directory that holds no
   .onnx models degrades to llm-only without erroring: the cpu rung drops
   out of the ladder, the page flow never raises, and the record still
   closes with its own skip note.
5. A real Arkose / FunCaptcha frame is detected as `arkose`: a match-tile
   round gets an llm answer clicked in, a rotation round records
   detect-and-record honestly, and neither fabricates a solve.
6. A reCAPTCHA v3 badge is detected as `recaptcha-v3` and answered with the
   detect-and-record only, never an invented score; a legacy text captcha
   on the same session transcribes, types, and leaves `solved` to whether
   the typing actually landed.
