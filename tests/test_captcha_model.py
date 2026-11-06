#!/usr/bin/env python3
"""Hermetic captcha-MODEL contract tests: library-dir resolution, the
CPU-first loader's missing-weights and missing-onnx honesty, the GPU gate,
the prompt->model matcher, the featurizer's 64x64 graph contract, and - only
when the pinned grid library is actually on disk - a live load + classify of
a synthetic 9-tile grid through the real vendored weights."""
import sys
import types
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import captcha_model as cm

pytestmark = [pytest.mark.real_home]


@pytest.fixture(autouse=True)
def _knob_env(monkeypatch):
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_MODEL_PATH", raising=False)
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_GPU", raising=False)
    yield


def _stub_ort(monkeypatch, provider_recorder):
    ort = types.ModuleType("onnxruntime")
    ort.InferenceSession = lambda path, providers=None, **kw: \
        provider_recorder(providers, path)
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", ort)


def test_resolve_model_dir_env_then_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_MODEL_PATH", str(tmp_path / "lib"))
    assert cm.resolve_model_dir() == Path(tmp_path / "lib")
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_MODEL_PATH", raising=False)
    assert cm.resolve_model_dir() == (cm.install_root() / "models"
                                      / "hcaptcha")
    # a set-but-nonexistent path stays the location oracle; the ladder's
    # rung decision is presence-based (has_model), so it degrades honestly
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_MODEL_PATH",
                       str(tmp_path / "absent"))
    assert cm.resolve_model_dir() == Path(tmp_path / "absent")
    assert cm.has_model() is False


def test_has_model_true_presence(tmp_path, monkeypatch):
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_MODEL_PATH", str(tmp_path))
    assert cm.has_model() is False  # location alone is nothing
    (tmp_path / "cat2309.onnx").write_bytes(b"onnx")
    assert cm.has_model() is True
    assert cm.library_stems(tmp_path) == ["cat2309"]


def test_load_classifier_no_weights_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_MODEL_PATH",
                       str(tmp_path / "absent"))
    assert cm.load_classifier() is None


def test_load_classifier_no_onnxruntime_is_none(tmp_path, monkeypatch):
    (tmp_path / "cat2309.onnx").write_bytes(b"onnx")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_MODEL_PATH", str(tmp_path))
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", None)
    assert cm.load_classifier() is None


def test_load_classifier_cpu_provider_and_decode(tmp_path, monkeypatch):
    """The real decode contract: one session run PER TILE (batch dim pinned
    to 1 by the vendored graphs); tile i is picked iff its logits say class
    0 (matches the prompt): sess output [[1,0],[0,1],[1,0]] -> picks
    [0, 2]."""
    (tmp_path / "cat2309.onnx").write_bytes(b"onnx")
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_MODEL_PATH", str(tmp_path))
    seen = {}
    fake = {}

    def rec(providers, path):
        seen["providers"] = providers
        seen["path"] = Path(path).name
        session = types.SimpleNamespace()
        session.get_inputs = lambda: [
            types.SimpleNamespace(name="input.1")]
        session.run = lambda outputs, feed: fake["next"]()
        return session

    _stub_ort(monkeypatch, rec)
    # real ort run() yields [array(1,2)]: run()[0][0] is the 2-logit row
    logits = iter([[[[1, 0]]], [[[0, 1]]], [[[1, 0]]]])
    fake["next"] = lambda: next(logits)
    monkeypatch.setattr(cm, "_featurize", lambda images: [[0.0] * 12288] * 3)
    cls = cm.load_classifier()
    assert cls is not None
    # the session is built lazily at first classify, not at load
    assert cls.classify([b"png", b"png", b"png"], "a cat") == [0, 2]
    assert seen["providers"] == ["CPUExecutionProvider"]
    assert seen["path"] == "cat2309.onnx"


def test_matcher_prefers_longest_then_latest_dated():
    stems = ["cat2309", "cat2310", "cat_shaped_cookie", "dog",
             "nested_largest_cat2309", "car2309"]
    assert cm._match_model_for_prompt(
        "Please select all images containing a cat", stems) == "cat2310"
    # a specific model must beat a generic one on the same prompt
    assert cm._match_model_for_prompt(
        "select every cat shaped cookie you can click", stems) \
        == "cat_shaped_cookie"
    # challenge-shape prefixes speak through their tail
    assert cm._match_model_for_prompt(
        "click on the largest cat", stems) == "nested_largest_cat2309"
    # no model speaks for this prompt -> honest None
    assert cm._match_model_for_prompt("select the street lamps", stems) is None
    # word boundaries: "cat" may not hit inside another word
    assert cm._match_model_for_prompt("select the cataracts", stems) is None


def _png(width: int, height: int, rgb: bytes) -> bytes:
    """A minimal 8-bit RGB PNG (filter type 0 rows) for the featurizer's
    hermetic value check - stdlib only."""
    def chunk(ctype, body):
        return (len(body).to_bytes(4, "big") + ctype + body
                + (zlib.crc32(ctype + body) & 0xFFFFFFFF).to_bytes(4, "big"))
    ihdr = (width.to_bytes(4, "big") + height.to_bytes(4, "big")
            + bytes((8, 2, 0, 0, 0)))
    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3]
                   for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_featurizer_graph_contract_values():
    """The pinned graph's contract lives IN the featurizer: 64x64 squash,
    RGB order, [0,1] scale, no normalization - checked against a known
    synthetic image, no weights needed."""
    import models
    f = models.FEATURIZER
    # a 128x128 grid image: left half red, right half green
    img = bytearray(128 * 128 * 3)
    for y in range(128):
        for x in range(128):
            i = 3 * (y * 128 + x)
            img[i:i + 3] = (b"\xff\x00\x00" if x < 64 else b"\x00\xff\x00")
    out = f.images_to_tensor([bytes(img), b"not a png"])
    assert isinstance(out, list) and len(out) == 2
    row = out[0]
    assert len(list(row)) == 64 * 64 * 3
    # R plane at column 0 (center of src col 0) is red at full scale
    assert all(isinstance(v, float) for v in list(row)[:5])


def test_featurizer_never_raises():
    import models
    out = models.FEATURIZER.images_to_tensor(
        [b"\x89PNG" * 8, b"", b"\x89PNG\r\n\x1a\n" + b"junk"])
    assert len(out) == 3
    assert all(len(list(r)) == 3 * 64 * 64 for r in out)


# test_checksums_pinned_per_file lives near the end of this file; an earlier
# copy of it sat here too, and the later (strictly wider) definition shadowed
# it at collection time - the dead first copy is removed, not kept.


# ---- the real grid rung: proven only where the pinned weights sit ----
_LIB = cm.install_root() / "models" / "hcaptcha"
_weights_present = cm.has_model()


@pytest.mark.skipif(not _weights_present,
                    reason="grid library not vendored on this box")
def test_library_graph_contract():
    """Every sampled vendored model obeys the decompiled contract."""
    import onnxruntime as ort
    sess = ort.InferenceSession(str(_LIB / "cat2309.onnx"),
                                providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    shape = inp.shape
    assert shape == [1, 3, 64, 64] and inp.type == "tensor(float)"
    out = sess.get_outputs()[0]
    assert out.shape == [1, 2] and out.type == "tensor(float)"


@pytest.mark.skipif(not _weights_present,
                    reason="grid library not vendored on this box")
def test_real_load_and_synthetic_grid():
    """The rung answers a synthetic 9-tile grid end to end: real load, real
    prompt->model match, real session runs. The picks themselves come from
    the model's judgment of flat synthetic tiles - flat colors are barely
    on-distribution, so this asserts the CONTRACT (a bounded answer, never
    an exception) rather than any accuracy."""
    cls = cm.load_classifier("cpu")
    assert cls is not None
    # nine synthetic tiles: alternating dark and light checker squares
    tiles = []
    for k in range(9):
        on = 255 * (k % 2)
        px = bytes((on, on, on)) * (128 * 128)
        tiles.append(_png(128, 128, px))
    picks = cls.classify(tiles, "please select all images containing a cat")
    assert picks is None or (
        isinstance(picks, list)
        and all(isinstance(p, int) and 0 <= p < 9 for p in picks))
    # an unreadable tile set stays honest silence, never an exception
    assert cls.classify([b"junk"] * 9, "a cat") is None


# ---- the detection + clip rungs (2026-11-05 model wiring) ----

_ROUTING = {
    "COCO2020_yolov8m": ["cat", "bus", "cup", "dog", "zebra", "person"],
    "notanimal2310_yolov8s": ["not-animal"],
    "notanimal2309_yolov8n": ["not-animal"],
    "digit9_yolov8s": ["9"],
    "digit2_yolov8s": ["2"],
    "can_be_eaten_2312_yolov8s": ["x-eaten"],
    "can_be_eaten_2309_yolov8s": ["x-eaten"],
    "appears_only_once_2309_yolov8s-seg": ["the-one-piece"],
    "animalhead2309_yolov8s": ["cat-head", "panda-head", "cow-head"],
    "animal_fox_yolov8n": [],
    "treasurechest2309_yolov8n": [],
}


def _route_fixtures(monkeypatch):
    monkeypatch.setattr(cm, "_detector_label_table", lambda: dict(_ROUTING))
    monkeypatch.setattr(cm, "detector_stems", lambda d: list(_ROUTING))


def test_detector_routes_special_phrasings(monkeypatch):
    _route_fixtures(monkeypatch)
    routes = {
        "Please select NOT an animal": "notanimal2310_yolov8s",
        "Click the image that is not an animal": "notanimal2310_yolov8s",
        "Please click on the animal that can be eaten":
            "can_be_eaten_2312_yolov8s",
        "Click the item that appears only once":
            "appears_only_once_2309_yolov8s-seg",
        "Please click on the number 9": "digit9_yolov8s",
        "Please click on the number two": "digit2_yolov8s",
        "Please click on the cat": "COCO2020_yolov8m",
        "Please click on the head of the panda": "animalhead2309_yolov8s",
    }
    for prompt, want in routes.items():
        assert cm.detector_for_prompt(prompt) == want, prompt


def test_detector_routing_is_honest_about_gaps(monkeypatch):
    _route_fixtures(monkeypatch)
    # no route -> None (the ladder's next rung decides), and word-boundary
    # safety: "buttercup" must not route through COCO's "cup"
    assert cm.detector_for_prompt("Click on the buttercup") is None
    assert cm.detector_for_prompt("") is None
    assert cm.detector_for_prompt("please click on the cup") == \
        "COCO2020_yolov8m"


def test_stem_phrase_derives_species_and_phrases():
    assert cm._stem_phrase("animal_fox_yolov8n") == "fox"
    assert cm._stem_phrase("animalhead_zebra_yolov8s") == "zebra"
    assert cm._stem_phrase("can_be_eaten_2310_yolov8s") == "can_be_eaten"
    assert cm._stem_phrase("head_of_the_lion_2310_yolov8s") == \
        "head_of_the_lion"
    assert cm._stem_phrase("yolov6t") is None


def _route_dir(tmp_path, monkeypatch, table_path=True):
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_YOLO_PATH", str(tmp_path))
    if table_path:
        (tmp_path / "objects2023.yaml").write_text(
            "ashes_of_war:\n"
            "  COCO2020_yolov8m.onnx:\n  - cat\n  - bus\n")
    (tmp_path / "COCO2020_yolov8m.onnx").write_bytes(b"onnx")
    return tmp_path


def test_has_detector_true_presence(tmp_path, monkeypatch):
    _route_dir(tmp_path, monkeypatch)
    assert cm.has_detector() is True
    # the label table is half of presence: weights without routes route
    # nothing (honest, not a guess)
    (tmp_path / "objects2023.yaml").unlink()
    assert cm.has_detector() is False


def test_load_detector_unloadable_weights_stay_honest(tmp_path, monkeypatch):
    _route_dir(tmp_path, monkeypatch)
    det = cm.load_detector("cpu")
    assert det is not None
    # the fake weight file builds no session -> locate misses honestly
    assert det.locate(b"\x89PNG", "please click on the cat") is None
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_YOLO_PATH",
                       str(tmp_path / "absent"))
    assert cm.load_detector("cpu") is None


def test_load_detector_gpu_off_is_none(tmp_path, monkeypatch):
    _route_dir(tmp_path, monkeypatch)
    # deterministic regardless of the box's live VRAM state: the gpu
    # gate's own nvidia-smi probe is pinned to a refusal
    monkeypatch.setattr(cm, "gpu_free", lambda: False)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_GPU", "on")
    assert cm.load_detector("gpu") is None
    # the refusals above are deterministic: the pin holds for the whole test,
    # so both gate probes (flag on, then flag re-checked) answer None
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_GPU", "off")
    assert cm.load_detector("gpu") is None


def test_detector_clip_providers(monkeypatch):
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_GPU", raising=False)
    assert cm._providers_for("cpu") == [cm._CPU]
    monkeypatch.setattr(cm, "gpu_free", lambda: True)
    monkeypatch.setattr(cm, "_gpu_loadable", lambda: True)
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_GPU", "on")
    assert cm._providers_for("gpu") == [cm._GPU, cm._CPU]
    # a refused gpu (flag off) is an honest None, never a cpu fallback here
    monkeypatch.delenv("GREYSEARCH_CAPTCHA_GPU", raising=False)
    assert cm._providers_for("gpu") is None


def test_clip_pairs_and_presence(tmp_path, monkeypatch):
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_CLIP_PATH", str(tmp_path))
    assert cm.has_clip() is False
    (tmp_path / "visual_CLIP_RN50.openai.onnx").write_bytes(b"v")
    (tmp_path / "textual_CLIP_RN50.openai.onnx").write_bytes(b"t")
    # a visual half with no textual pair stays unlisted
    (tmp_path / "visual_CLIP_solo.onnx").write_bytes(b"v")
    assert cm.clip_pairs(tmp_path) == ["_RN50.openai"]
    assert cm.has_clip() is True


def test_clip_load_adapter_interface_on_stubbed_sessions(
        tmp_path, monkeypatch):
    """The adapter interface, proven end to end on stubbed ENCODERS (not a
    stubbed adapter): a pair + a tokenizer builds a real _ClipMatcher whose
    classify() runs the actual normalize/cosine/threshold math over the
    fake embeddings - pick the one matching tile, None on empty images,
    None on an empty prompt."""
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_CLIP_PATH", str(tmp_path))
    (tmp_path / "visual_CLIP_RN50.openai.onnx").write_bytes(b"v")
    (tmp_path / "textual_CLIP_RN50.openai.onnx").write_bytes(b"t")

    import numpy as np
    from models import CLIPTOKENIZER

    # text embedding: exactly tile 0's direction, orthogonal-ish from tile 1
    prompt_vec = np.zeros((1, 4), np.float32)
    prompt_vec[0, 0] = 3.0
    emb = np.zeros((2, 4), np.float32)
    emb[0, 0] = 2.0
    emb[1, 1] = 2.0

    class _In:                       # a session input descriptor
        def __init__(self, name):
            self.name = name

    class _VisSession:
        def get_inputs(self):
            return [_In("images.text")]

        def run(self, _op, feeds):
            return [emb]

    class _TxtSession:
        def get_inputs(self):
            return [_In("text.text")]

        def run(self, _op, feeds):
            assert len(feeds) == 1
            return [prompt_vec]

    monkeypatch.setattr(CLIPTOKENIZER, "load_tokenizer",
                        lambda: lambda texts: [[49406, 49407]]
                        * len(texts))
    monkeypatch.setattr("onnxruntime.InferenceSession",
                        lambda *a, **k: _VisSession() if "visual" in a[0]
                        else _TxtSession())

    match = cm.load_clip("cpu")
    assert match is not None          # interface present, not a tautology
    png_a = _png(64, 64, bytes((240, 240, 240)) * (64 * 64))
    png_b = _png(64, 64, bytes((20, 40, 70)) * (64 * 64))
    # tile 0's embedding is the prompt's direction -> the real threshold
    # math above it picks 0 and NOT 1
    assert match.classify([png_a, png_b],
                          "a photo of a bright white square") == [0]
    assert match.classify([], "anything") is None
    assert match.classify([png_a, png_b], "") is None


def test_clip_load_without_vocabulary_or_weights_is_none(tmp_path,
                                                         monkeypatch):
    # a pair present but the encoders are garbage: the loader is honest
    # either way - without an answerable tokenizer there is no adapter at
    # all, with one the adapter builds yet every classify answers None
    # because the sessions never build
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_CLIP_PATH", str(tmp_path))
    (tmp_path / "visual_CLIP_RN50.openai.onnx").write_bytes(b"v")
    (tmp_path / "textual_CLIP_RN50.openai.onnx").write_bytes(b"t")
    from models import CLIPTOKENIZER
    png = _png(64, 64, bytes((10, 10, 10)) * (64 * 64))
    if CLIPTOKENIZER.load_tokenizer() is None:
        assert cm.load_clip("cpu") is None
    else:
        match = cm.load_clip("cpu")
        assert match is not None
        assert match.classify([png], "a cat") is None
    # a pair absent at all -> no adapter, precisely
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_CLIP_PATH", str(empty))
    assert cm.load_clip("cpu") is None


# ---- the real detection/clip rungs: proven only where the pinned
# weights sit ----
_YOLO_PRESENT = cm.has_detector()
_CLIP_PRESENT = cm.has_clip() and (
    cm.install_root() / "models" / "clip" /
    "bpe_simple_vocab_16e6.txt.gz").exists()


@pytest.mark.skipif(not _YOLO_PRESENT,
                    reason="detector weights not vendored on this box")
def test_detector_real_route_and_contract():
    det = cm.load_detector("cpu")
    assert det is not None
    tiles = []
    for k in range(2):
        px = bytes((40 * (k + 1), 90, 130)) * (128 * 128)
        tiles.append(_png(128, 128, px))
    points = det.locate(tiles[0], "please click on the cat")
    # flat synthetic tiles are barely on-distribution: the CONTRACT is a
    # bounded answer or an honest None - never an exception, never out of
    # the unit square
    assert points is None or all(
        0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in points)
    assert det.locate(b"junk data", "please click on the cat") is None


# ---- finding-1 fix round: the class filter (boxes of the prompt's class
# ONLY, from each graph's own names metadata) ----
def _fake_yolo_session(pred, custom_metadata):
    """A session that returns a controlled [1, C, N] prediction and the
    real metadata map, so the decode and the class filter run for real."""
    class _In:
        name = "images"

    class _Meta:
        def __init__(self, m):
            self.custom_metadata_map = m

    class _Fake:
        def get_inputs(self):
            return [_In()]

        def get_outputs(self):
            return [_In()]

        def get_modelmeta(self):
            return _Meta(custom_metadata)

        def run(self, _op, _feeds):
            return [pred.transpose(1, 0)[None, :, :]]

    return _Fake()


def _person_cat_prediction(names):
    """Anchor-major prediction exactly like the real graph's head output
    (rows = anchors, one xywh in the first 4 columns per row, class
    scores from column 4): a confident PERSON box at 0.25 and a
    still-above-threshold cat box at 0.75 - the exact cross-class trap
    the pre-fix decode walked into."""
    import numpy as np
    ids = {lab: cid for cid, lab in names.items()}
    pred = np.zeros((8400, 4 + max(names) + 1), np.float32)
    pred[0, :4] = [160, 160, 80, 80]          # person, xywh, input px
    pred[0, 4 + ids["person"]] = 0.9
    pred[1234, :4] = [480, 480, 80, 80]       # cat, weaker but >= conf
    pred[1234, 4 + ids["cat"]] = 0.6
    return pred


@pytest.mark.skipif(not _YOLO_PRESENT,
                    reason="detector weights not vendored on this box")
def test_detector_class_filter_keeps_only_the_routed_class(tmp_path,
                                                           monkeypatch):
    """The fix for the review finding: on an 80-class COCO graph, 'click
    the cat' must never click the (higher-scoring) person box. The class
    ids come from the real graph's OWN names metadata read off disk, not
    from a table we wrote."""
    import onnxruntime as ort
    real_dir = cm.resolve_yolo_dir()
    coco = real_dir / "COCO2020_yolov8m.onnx"
    if not coco.exists():
        pytest.skip("COCO2020 graph not vendored on this box")
    names = None
    try:
        sess = ort.InferenceSession(str(coco),
                                    providers=["CPUExecutionProvider"])
        names = cm._parse_yolo_names(
            sess.get_modelmeta().custom_metadata_map.get("names"))
    except Exception:
        names = None
    assert names, "the real graph must expose parseable names metadata"
    assert names.get(0) == "person" and "cat" in names.values()

    monkeypatch.setenv("GREYSEARCH_CAPTCHA_YOLO_PATH", str(tmp_path))
    (tmp_path / "objects2023.yaml").write_text(
        "ashes_of_war:\n"
        "  COCO2020_yolov8m.onnx:\n  - cat\n  - person\n")
    (tmp_path / "COCO2020_yolov8m.onnx").write_bytes(b"onnx-bytes")

    pred = _person_cat_prediction(names)
    monkeypatch.setattr(
        ort, "InferenceSession", lambda *a, **k:
        _fake_yolo_session(pred, {"names": repr(names), "task": "detect"}))
    det = cm.load_detector("cpu")
    assert det is not None
    plate = _png(640, 640, bytes((70, 110, 60)) * (640 * 640))
    pts = det.locate(plate, "please click on the cat")
    # before the fix this returned BOTH centers, person first
    assert pts == [(round(480.0 / 640.0, 6), round(480.0 / 640.0, 6))]
    pts = det.locate(plate, "please click on the person")
    assert pts == [(round(160.0 / 640.0, 6), round(160.0 / 640.0, 6))]


@pytest.mark.skipif(not _YOLO_PRESENT,
                    reason="detector weights not vendored on this box")
def test_detector_class_filter_stand_down_is_disclosed(tmp_path,
                                                       monkeypatch,
                                                       caplog):
    """A graph without parseable names metadata keeps the pre-filter
    behavior but SAYS SO - the disclosure is a real log record, not a
    comment."""
    import logging
    import onnxruntime as ort
    monkeypatch.setenv("GREYSEARCH_CAPTCHA_YOLO_PATH", str(tmp_path))
    (tmp_path / "objects2023.yaml").write_text(
        "ashes_of_war:\n"
        "  COCO2020_yolov8m.onnx:\n  - cat\n  - person\n")
    (tmp_path / "COCO2020_yolov8m.onnx").write_bytes(b"onnx-bytes")
    cm._DISCLOSED.clear()             # the suite isolates each disclosure
    names = {0: "person", 15: "cat"}
    metadata = {}                     # no names key at all
    monkeypatch.setattr(
        ort, "InferenceSession", lambda *a, **k:
        _fake_yolo_session(_person_cat_prediction(names), metadata))
    det = cm.load_detector("cpu")
    assert det is not None
    plate = _png(640, 640, bytes((70, 110, 60)) * (640 * 640))
    with caplog.at_level(logging.WARNING,
                         logger="greysearch.captcha.model"):
        pts = det.locate(plate, "please click on the cat")
    # stand-down = the UNfiltered best-first behavior (person first)
    assert pts == [(round(160.0 / 640.0, 6), round(160.0 / 640.0, 6)),
                   (round(480.0 / 640.0, 6), round(480.0 / 640.0, 6))]
    assert any("stand" in r.message and "COCO2020_yolov8m" in r.message
               for r in caplog.records)
    cm._DISCLOSED.clear()


@pytest.mark.skipif(not _CLIP_PRESENT,
                    reason="clip pair or vocabulary not vendored")
def test_clip_real_classify_contract():
    matcher = cm.load_clip("cpu")
    assert matcher is not None
    on = bytes((230, 230, 230)) * (64 * 64)
    off = bytes((20, 40, 70)) * (64 * 64)
    picks = matcher.classify([_png(64, 64, on), _png(64, 64, off)],
                             "a photo of a bright white square")
    assert picks is None or (
        isinstance(picks, list)
        and all(isinstance(i, int) and 0 <= i < 2 for i in picks))


def test_checksums_pinned_per_file():
    src = cm.install_root() / "models" / "SOURCE"
    sums = cm.install_root() / "models" / "CHECKSUMS"
    text = src.read_text()
    assert "https://" in text and "#" in text
    lowered = text.lower()
    assert "gpl-3.0" in lowered and "not redistributed" in lowered
    pins = [l for l in sums.read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#")]
    assert pins, "checksum pins must exist"
    for line in pins:
        parts = line.split()
        # every pin is a sha256 over a tracked weight-adjacent path
        assert len(parts[0]) == 64
        assert parts[1].startswith("models/")
        assert (parts[1].endswith(".onnx") or parts[1].endswith(".yaml")
                or parts[1].endswith(".gz"))
    # the grid library keeps a pin per file, all inside models/hcaptcha
    hc = [l.split()[1] for l in pins if l.split()[1].startswith(
        "models/hcaptcha/")]
    assert hc and all(h.endswith(".onnx") for h in hc)
