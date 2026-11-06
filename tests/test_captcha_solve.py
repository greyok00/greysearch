#!/usr/bin/env python3
"""Hermetic captcha-SOLVE contract tests: knob resolution, the engine
ladder, the LLM rung's gateway protocol, capture/click scripting, the
round manager, and honest records. No network: the gateway POST is a
module-level function every test monkeypatches."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import captcha_solve as cs

pytestmark = [pytest.mark.real_home]


@pytest.fixture(autouse=True)
def _knob_env(monkeypatch):
    for k in ("GREYSEARCH_CAPTCHA_SOLVER", "GREYSEARCH_CAPTCHA_RUNGS",
              "GREYSEARCH_CAPTCHA_MAX_ROUNDS", "GREYSEARCH_CAPTCHA_MODEL_PATH",
              "GREYSEARCH_CAPTCHA_GPU", "GREYSEARCH_CAPTCHA_LLM_TIMEOUT_S",
              "GREYSEARCH_LLM_URL"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_solver_enabled_default_on_and_off():
    assert cs.solver_enabled() is True
    for v in ("off", "0", "false", "OFF"):
        os.environ["GREYSEARCH_CAPTCHA_SOLVER"] = v
        assert cs.solver_enabled() is False
    os.environ["GREYSEARCH_CAPTCHA_SOLVER"] = "on"
    assert cs.solver_enabled() is True


def test_engine_rungs_defaults_and_explicit_order():
    assert cs.engine_rungs(has_model=False) == ["llm"]
    assert cs.engine_rungs(has_model=True) == ["llm", "cpu"]
    os.environ["GREYSEARCH_CAPTCHA_RUNGS"] = "cpu,llm"
    assert cs.engine_rungs(has_model=True) == ["cpu", "llm"]
    os.environ["GREYSEARCH_CAPTCHA_RUNGS"] = "bogus,cpu"
    assert cs.engine_rungs(has_model=True) == ["cpu"]
    os.environ["GREYSEARCH_CAPTCHA_GPU"] = "on"
    os.environ["GREYSEARCH_CAPTCHA_RUNGS"] = "gpu"
    assert cs.engine_rungs(has_model=True) == ["gpu"]
    # gpu joins the ladder only when GREYSEARCH_CAPTCHA_GPU=on
    del os.environ["GREYSEARCH_CAPTCHA_RUNGS"]
    assert cs.engine_rungs(has_model=True) == ["llm", "cpu", "gpu"]


def test_max_rounds():
    assert cs.max_rounds() == 3
    os.environ["GREYSEARCH_CAPTCHA_MAX_ROUNDS"] = "1"
    assert cs.max_rounds() == 1


import json as _json


def _capture(monkeypatch):
    calls = {}

    def fake_post(payload, timeout_s):
        calls["payload"] = payload
        calls["timeout"] = timeout_s
        return calls.get("reply")

    monkeypatch.setattr(cs, "_gateway_post", fake_post)
    return calls


def test_attempt_llm_happy_path(monkeypatch):
    calls = _capture(monkeypatch)
    calls["reply"] = {
        "choices": [{"message": {"content": _json.dumps({"picks": [1, 3]})}}]
    }
    picks = cs.attempt_llm([b"\x89PNG" * 10, b"\x89PNG" * 11, b"\x89PNG" * 12,
                            b"\x89PNG" * 13],
                           "Select all buses", timeout_s=4.0)
    assert picks == [1, 3]
    pl = calls["payload"]
    assert pl["model"] == cs.LLM_MODEL
    assert pl["max_tokens"] <= 256
    body = pl["messages"][0]["content"]
    assert isinstance(body, list) and pl["messages"][0]["role"] == "user"
    assert body[0]["text"].startswith("SELECT")
    assert body[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_attempt_llm_timeout_and_malformed_return_none(monkeypatch):
    _capture(monkeypatch)                      # fake returns None always
    assert cs.attempt_llm([b"x"], "p", timeout_s=1.0) is None
    monkeypatch.setattr(cs, "_gateway_post",
                        lambda p, t: {"choices": [{"message":
                                                   {"content": "gorp"}}]})
    assert cs.attempt_llm([b"x"], "p", timeout_s=1.0) is None
    assert cs.attempt_llm([], "", timeout_s=1.0) is None


def test_llm_timeout_knob(monkeypatch):
    os.environ["GREYSEARCH_CAPTCHA_LLM_TIMEOUT_S"] = "7"
    assert cs.llm_timeout() == 7.0
    del os.environ["GREYSEARCH_CAPTCHA_LLM_TIMEOUT_S"]
    assert cs.llm_timeout() == 20.0


class _FakeFrame:
    def __init__(self, tiles, prompt_text=""):
        self.tiles = tiles
        self.prompt_text = prompt_text
        self.clicked = []

    def locator(self, sel):
        return self

    @property
    def first(self):
        # Playwright locators expose .first as a narrowed sub-locator; the
        # fake narrows to itself so `.locator(sel).first.inner_text()`
        # resolves against the same prompt text (brief fix 2026-11-05: the
        # brief's fake omitted this, which made the pinned hcaptcha
        # .prompt-text contract unreachable — capture_grid's
        # `loc(...).first` chain always AttributeError'd and the prompt
        # fell through to page.inner_text).
        return self

    def all(self):
        return self.tiles

    def nth(self, i):
        return self.tiles[i]

    def click(self, timeout=None):
        self.clicked.append(1)

    def inner_text(self, timeout=None):
        return self.prompt_text


class _FakeTile:
    def __init__(self, png=b"png-bytes", url=""):
        self.png = png
        self.url = url
        self.clicked = False

    def screenshot(self, timeout=None):
        return self.png

    def click(self, timeout=None):
        self.clicked = True


class _FakePage3:
    """Page faking the provider scan JS and challenge-frame access."""

    def __init__(self, providers, tiles, prompt="Select all buses",
                 frame_prompt=""):
        self._providers = providers
        self.tiles = tiles
        self.prompt = prompt
        self.frame_prompt = frame_prompt

    def evaluate(self, js, arg=None):
        if "greysearch-providers" in js:
            return list(self._providers)
        return None

    def url(self):
        return "https://example.three/page"

    def inner_text(self, timeout=None):
        return self.prompt

    def frame_locator(self, sel):
        return _FakeFrame(self.tiles, self.frame_prompt)


_DETECTOR_JS = """
() => {
  const re = /(recaptcha|hcaptcha|turnstile)/i;
  const out = new Set();
  for (const f of Array.from(document.querySelectorAll('iframe'))) {
    const m = String(f.src||'').match(re) || String(f.title||'').match(re);
    if (m) out.add(m[1].toLowerCase());
  }
  const t = (((document.body||{})).innerText||'').slice(0, 2000);
  if (re.test(t)) { const m = t.match(re); if (m) out.add(m[1].toLowerCase()); }
  return ['greysearch-providers'].concat(Array.from(out));
}
"""


def test_detect_provider():
    pg = _FakePage3(["recaptcha"], [])
    assert cs.detect_provider(pg) == "recaptcha"
    pg = _FakePage3([], [])
    assert cs.detect_provider(pg) is None


def test_capture_grid_returns_prompt_and_pngs():
    tiles = [_FakeTile(png=b"a"), _FakeTile(png=b"b"), _FakeTile(png=b"c")]
    pg = _FakePage3(["recaptcha"], tiles)
    cap = cs.capture_grid(pg, "recaptcha")
    assert cap == {"mode": "pick", "prompt": "Select all buses",
                   "images": [b"a", b"b", b"c"]}
    # hcaptcha prompt lives in .prompt-text of the challenge frame
    pg2 = _FakePage3(["hcaptcha"], tiles, prompt="ignored",
                     frame_prompt="Pick one")
    cap2 = cs.capture_grid(pg2, "hcaptcha")
    assert cap2["prompt"] == "Pick one"
    assert cap2["mode"] == "pick"
    assert cap2["images"] == [b"a", b"b", b"c"]


def test_capture_single_element_is_points_mode():
    # A ONE-element tile capture is the coordinate-challenge shape: the
    # capture reports mode "points" (answers are click coordinates, not
    # tile toggles).
    pg = _FakePage3(["hcaptcha"], [_FakeTile(png=b"big")],
                    frame_prompt="Click the panda")
    cap = cs.capture_grid(pg, "hcaptcha")
    assert cap["mode"] == "points"
    assert cap["images"] == [b"big"] and cap["prompt"] == "Click the panda"


def test_dispatch_clicks_and_bounds():
    tiles = [_FakeTile(), _FakeTile(), _FakeTile()]
    pg = _FakePage3(["recaptcha"], tiles)
    assert cs.dispatch_clicks(pg, "recaptcha", [0, 2]) is True
    assert tiles[0].clicked and tiles[2].clicked and not tiles[1].clicked
    assert cs.dispatch_clicks(pg, "recaptcha", [9]) is False   # out of range
    assert cs.dispatch_clicks(pg, "recaptcha", []) is False    # nothing to do


def test_dispatch_clicks_partial_fail_returns_false():
    # All-or-nothing contract: a pick that cannot land (9 is out of range)
    # makes the whole dispatch False even though tile 0 clicked.
    tiles = [_FakeTile(), _FakeTile(), _FakeTile()]
    pg = _FakePage3(["recaptcha"], tiles)
    assert cs.dispatch_clicks(pg, "recaptcha", [0, 9]) is False
    assert tiles[0].clicked and not tiles[1].clicked and not tiles[2].clicked


def test_capture_grid_keeps_tile_slots_aligned():
    # An oversized tile keeps its (empty) slot so the images list stays
    # aligned with DOM order; only the decode is skipped, never the slot.
    big = b"x" * 300_000
    tiles = [_FakeTile(png=big), _FakeTile(png=b"b")]
    pg = _FakePage3(["recaptcha"], tiles)
    cap = cs.capture_grid(pg, "recaptcha")
    assert cap["images"] == [b"", b"b"]
    assert cap["prompt"] == "Select all buses"


def test_solve_challenge_llm_solves_first_round(monkeypatch):
    pg = _FakePage3(["recaptcha"], [_FakeTile(), _FakeTile()])
    monkeypatch.setattr(cs, "attempt_llm",
                        lambda i, p, t: [0] if len(i) == 2 else [1])
    monkeypatch.setattr(cs, "engine_rungs", lambda m: ["llm"])
    rec = cs.solve_challenge(pg, "recaptcha")
    assert rec["engaged"] is True and rec["solved"] is True
    assert rec["provider"] == "recaptcha" and rec["rung"] == "llm"
    assert rec["rounds"] == 1 and not rec.get("error")


def test_solve_challenge_ladder_falls_through(monkeypatch):
    import types as _types
    import captcha_model as _cm2
    pg = _FakePage3(["recaptcha"], [_FakeTile(), _FakeTile(), _FakeTile()])
    fake = _types.SimpleNamespace(classify=lambda imgs, p: [0])
    monkeypatch.setattr(cs, "attempt_llm", lambda i, p, t: None)
    monkeypatch.setattr(cs, "engine_rungs", lambda m: ["llm", "cpu"])
    monkeypatch.setattr(_cm2, "has_cpu_runway", lambda: True)
    monkeypatch.setattr(_cm2, "load_classifier", lambda d="cpu": fake)
    monkeypatch.setattr(_cm2, "load_clip", lambda d="cpu": None)
    rec = cs.solve_challenge(pg, "recaptcha")
    assert rec["solved"] is True and rec["rung"] == "cpu"
    assert rec["latency_ms"].get("llm") is not None


def test_solve_challenge_cap_exhausted_honest_skip(monkeypatch):
    os.environ["GREYSEARCH_CAPTCHA_MAX_ROUNDS"] = "1"
    pg = _FakePage3(["recaptcha"], [_FakeTile(), _FakeTile()])
    monkeypatch.setattr(cs, "attempt_llm", lambda i, p, t: [0])
    monkeypatch.setattr(cs, "engine_rungs", lambda m: ["llm"])
    # dispatch_clicks reports False: clicks never land -> rounds burn out
    monkeypatch.setattr(cs, "dispatch_clicks", lambda p, pr, k: False)
    rec = cs.solve_challenge(pg, "recaptcha")
    assert rec["engaged"] is True and rec["solved"] is False
    assert rec["rounds"] == 1
    assert rec.get("error", "").startswith(("cap", "rungs", "challenge"))


def test_solve_challenge_disabled_is_unengaged(monkeypatch):
    os.environ["GREYSEARCH_CAPTCHA_SOLVER"] = "off"
    pg = _FakePage3(["recaptcha"], [_FakeTile()])
    rec = cs.solve_challenge(pg, "recaptcha")
    assert rec == {"engaged": False, "provider": "recaptcha",
                   "rungs": [], "rung": None, "rounds": 0,
                   "solved": False, "latency_ms": {}, "error": None}


class _TsFrame:
    def __init__(self, has_box=True):
        self.has_box = has_box
        self.clicked = False

    def locator(self, sel):
        return self

    @property
    def count(self):
        return 1 if self.has_box else 0

    def click(self, timeout=None):
        self.clicked = True


class _TsPage:
    def __init__(self, has_box=True):
        self.frame = _TsFrame(has_box)

    def frame_locator(self, sel):
        if "challenges.cloudflare.com" not in sel and "turnstile" not in sel:
            raise KeyError(sel)
        return self.frame


def test_solve_turnstile_clicks_or_reports_cleanly():
    pg = _TsPage(True)
    rec = cs.solve_turnstile(pg)
    assert rec["provider"] == "turnstile" and rec["solved"] is True
    assert rec["rung"] == "behavioral" and rec["rungs"] == ["behavioral"]
    pg = _TsPage(False)
    rec = cs.solve_turnstile(pg)
    assert rec["solved"] is False
    assert rec.get("error", "").startswith(("no checkbox", "cap"))


def test_repo_free_of_nopecha_bytes():
    """Acceptance gate: the git-tracked tree carries no nopecha byte
    outside tests/ and the spec/plan docs (which document the removal)."""
    files = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.split()
    hits = []
    for rel in files:
        if rel.startswith("tests/") or rel.startswith("docs/superpowers/"):
            # tests/ and the spec/plan docs legitimately name the removed
            # asset; they document the removal and are exempt from scanning.
            continue
        if "nopecha" in rel:
            hits.append(rel)  # a deleted-in-name-only path still tracked
            continue
        p = REPO_ROOT / rel
        try:
            if p.is_file() and "nopecha" in p.read_text(
                    errors="ignore").lower():
                hits.append(rel)
        except Exception:
            pass
    assert not hits, f"nopecha still referenced: {hits[:10]}"


def test_lane_never_loads_nopecha():
    """Acceptance gate (2026-11-05 owner correction): a captcha-solving
    browser extension stays on the box for the owner's other browsers, but
    the LANE is disabled for it - the launcher, every slot/profile seeding
    path, the health checker, the wait contract and the solver carry no
    extension reference, no Chromium extension load path, and no key seed.
    These exact files are scanned WITHOUT the tests/docs exemptions the
    tree-wide byte gate allows."""
    lane_files = ["install.sh", "patchright_headless.py", "doctor.py",
                  "browser_session.py", "searx_fallback.py",
                  "captcha_solve.py", "captcha_model.py"]
    for rel in lane_files:
        text = (REPO_ROOT / rel).read_text(errors="ignore").lower()
        assert "nopecha" not in text, f"{rel} still references the extension"
        assert "GREYSEARCH_NOPECHA" not in text.upper(), rel
        # no lane file wires a Chromium extension into a browser at all:
        # neither the argv flag patchright would honor nor a loader helper
        assert "--load-extension" not in text, f"{rel} loads an extension"
        assert "load_extension" not in text, f"{rel} loads an extension"
    # and no extension path is seeded into any lane profile layout, on disk
    # as well as in git (conf/ is ignored, so tracking alone cannot prove it)
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.splitlines()
    seeded = [rel for rel in tracked
              if rel.startswith(("assets/nopecha", "conf/"))]
    assert not seeded, f"extension assets or key seed still tracked: {seeded}"
    conf_dir = REPO_ROOT / "conf"
    if conf_dir.is_dir():
        leaked = [p.name for p in conf_dir.rglob("*")
                  if "nopecha" in p.read_text(errors="ignore").lower()]
        assert not leaked, f"key material in conf/: {leaked[:5]}"


# ---- the points half of the solver: one-image challenges answered with
# normalized click coordinates (2026-11-05 model wiring) ----

class _Mouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((x, y))


class _BoxTile(_FakeTile):
    """A tile faking Playwright's bounding_box; clicks land at
    x + frac * width."""

    def bounding_box(self):
        return {"x": 40, "y": 60, "width": 400, "height": 300}


class _PointsPage(_FakePage3):
    def __init__(self, providers, tiles, prompt="Select all buses",
                 frame_prompt=""):
        super().__init__(providers, tiles, prompt, frame_prompt)
        self.mouse = _Mouse()


def test_attempt_llm_points_happy_path(monkeypatch):
    calls = _capture(monkeypatch)
    calls["reply"] = {"choices": [{"message": {
        "content": 'noise gorp {"points":[[0.5,0.5],[0.75,0.25],'
                   '[1.4,0.2],[0.3]]} tail'}}]}
    points = cs.attempt_llm_points(b"\x89PNG", "Click on the cat",
                                   timeout_s=4.0)
    # out-of-range coordinates drop; malformed rows drop; two remain
    assert points == [(0.5, 0.5), (0.75, 0.25)]
    payload = calls["payload"]
    assert payload["messages"][0]["content"][0]["text"].startswith(
        "You are looking at ONE image.")
    assert payload["think"] is False
    monkeypatch.setattr(cs, "_gateway_post",
                        lambda p, t: {"choices": [{"message":
                                                   {"content": "gorp"}}]})
    assert cs.attempt_llm_points(b"x", "p", timeout_s=1.0) is None
    assert cs.attempt_llm_points(b"", "p", timeout_s=1.0) is None


def test_dispatch_points_clicks_normalized():
    tile = _BoxTile()
    pg = _PointsPage(["hcaptcha"], [tile])
    assert cs.dispatch_points(pg, "hcaptcha", [(0.5, 0.5), (0.25, 0.1)]) \
        is True
    assert pg.mouse.clicks == [(240.0, 210.0), (140.0, 90.0)]
    # out-of-bounds coordinates are skipped, making dispatch False
    pg.mouse.clicks = []
    assert cs.dispatch_points(pg, "hcaptcha", [(1.5, 0.5)]) is False
    assert pg.mouse.clicks == []
    assert cs.dispatch_points(pg, "hcaptcha", []) is False


def test_dispatch_points_is_atomic_before_the_first_click():
    """Fix-round finding: valid points must never land while an invalid
    sibling flips the dispatch to False - validation happens for the
    WHOLE set before the first mouse press."""
    tile = _BoxTile()
    pg = _PointsPage(["hcaptcha"], [tile])
    # a valid point first, an out-of-range point second: NOTHING clicks
    pg.mouse.clicks = []
    assert cs.dispatch_points(pg, "hcaptcha",
                              [(0.5, 0.5), (1.5, 0.5)]) is False
    assert pg.mouse.clicks == []
    # a malformed point does the same
    pg.mouse.clicks = []
    assert cs.dispatch_points(pg, "hcaptcha",
                              [(0.5, 0.5), (0.3,)]) is False
    assert pg.mouse.clicks == []
    assert cs.dispatch_points(pg, "hcaptcha",
                              [(0.5, 0.5), "nonsense"]) is False
    assert pg.mouse.clicks == []
    # a valid set that clicks half-way then fails still stops before any
    # submit (the failure surfaces, not a silent half-solve)
    class _BrokenAfterOne:
        def __init__(self):
            self.clicks = []

        def click(self, x, y):
            self.clicks.append((x, y))
            if len(self.clicks) == 2:
                raise RuntimeError("mouse died")

    pg.mouse = _BrokenAfterOne()
    assert cs.dispatch_points(pg, "hcaptcha",
                              [(0.5, 0.5), (0.25, 0.1), (0.1, 0.1)]) is False
    assert len(pg.mouse.clicks) == 2


def test_solve_points_ladder_uses_detector(monkeypatch):
    import types as _types
    import captcha_model as _cm2
    pg = _PointsPage(["hcaptcha"], [_BoxTile()],
                     frame_prompt="Click the panda")
    detector = _types.SimpleNamespace(locate=lambda img, p: [(0.5, 0.5)])
    monkeypatch.setattr(cs, "attempt_llm", lambda i, p, t: None)
    monkeypatch.setattr(cs, "attempt_llm_points", lambda i, p, t: None)
    monkeypatch.setattr(cs, "engine_rungs", lambda m: ["llm", "cpu"])
    monkeypatch.setattr(_cm2, "has_cpu_runway", lambda: True)
    monkeypatch.setattr(_cm2, "load_classifier", lambda d="cpu": None)
    monkeypatch.setattr(_cm2, "load_clip", lambda d="cpu": None)
    monkeypatch.setattr(_cm2, "load_detector", lambda d="cpu": detector)
    rec = cs.solve_challenge(pg, "hcaptcha")
    assert rec["solved"] is True and rec["rung"] == "cpu"
    assert pg.mouse.clicks == [(240.0, 210.0)]


# ---- the no-weights provider strategies (2026-11-05 phase 3) ----

def test_detect_provider_new_markers():
    assert cs.detect_provider(_FakePage3(["recaptcha"], [])) == "recaptcha"
    assert cs.detect_provider(_FakePage3(["arkose"], [])) == "arkose"
    assert cs.detect_provider(_FakePage3(["recaptcha-v3"], [])) \
        == "recaptcha-v3"
    assert cs.detect_provider(_FakePage3(["text"], [])) == "text"
    assert cs.detect_provider(_FakePage3([], [])) is None


def test_attempt_llm_text_transcribes(monkeypatch):
    calls = _capture(monkeypatch)
    calls["reply"] = {"choices": [{"message": {"content": "  'Fx91A' \n"}}]}
    answer = cs.attempt_llm_text(b"\x89PNG", timeout_s=4.0)
    assert answer == "Fx91A"
    txt = calls["payload"]["messages"][0]["content"][0]["text"]
    assert txt.startswith("Transcribe EXACTLY")
    # a reply with no alphanumerics is an honest miss, never a guess
    calls["reply"] = {"choices": [{"message": {"content": "???"}}]}
    assert cs.attempt_llm_text(b"\x89PNG", timeout_s=4.0) is None
    calls["reply"] = None
    assert cs.attempt_llm_text(b"x", timeout_s=1.0) is None


def test_solve_v3_records_without_fabricating():
    rec = cs.solve_v3_report()
    assert rec["provider"] == "recaptcha-v3"
    assert rec["engaged"] is True and rec["solved"] is False
    assert rec["rung"] is None
    assert "detect-and-record" in rec["error"]


class _TextLocator:
    def __init__(self, page, sel):
        self._page = page
        self._sel = sel

    @property
    def first(self):
        return self

    def screenshot(self, timeout=None):
        return self._page.shot

    def fill(self, value, timeout=None):
        self._page.filled = value

    def press(self, key, timeout=None):
        self._page.pressed = key

    def click(self, timeout=None):
        self._page.submitted = True


class _TextPage:
    def __init__(self, marked=True, shot=b"png", scan=True):
        self._marked = marked
        self.shot = shot
        self.filled = None
        self.pressed = None
        self.submitted = False
        self._scan = scan

    def evaluate(self, js, arg=None):
        if "greysearch-captcha-img" in js or "marked" not in js:
            return "marked" if self._marked else None
        return None

    def locator(self, sel):
        return _TextLocator(self, sel)


def test_solve_text_transcribes_and_types(monkeypatch):
    calls = _capture(monkeypatch)
    calls["reply"] = {"choices": [{"message": {"content": "Fx91A\n"}}]}
    pg = _TextPage()
    rec = cs.solve_text(pg)
    assert rec["engaged"] is True and rec["solved"] is True
    assert rec["provider"] == "text" and rec["rung"] == "llm"
    assert pg.filled == "Fx91A" and pg.pressed == "Enter"
    # every miss path is honest
    rec2 = cs.solve_text(_TextPage(marked=False))
    assert rec2["solved"] is False and rec2["error"] == \
        "text: no named captcha image + input pair found"
    rec3 = cs.solve_text(_TextPage(shot=b""))
    assert rec3["solved"] is False and "unreadable" in rec3["error"]
    calls["reply"] = None
    rec4 = cs.solve_text(_TextPage())
    assert rec4["solved"] is False and rec4["rung"] is None
    assert "transcription" in rec4["error"]


def test_solve_text_disabled_is_unengaged():
    os.environ["GREYSEARCH_CAPTCHA_SOLVER"] = "off"
    rec = cs.solve_text(_TextPage())
    assert rec["engaged"] is False and rec["solved"] is False


class _ArkoseImg:
    def __init__(self, png=b"a"):
        self.png = png
        self.clicked = False

    def screenshot(self, timeout=None):
        return self.png

    def click(self, timeout=None):
        self.clicked = True


class _ArkosePage(_FakePage3):
    pass


def test_solve_arkose_match_variant_llm_picks(monkeypatch):
    calls = _capture(monkeypatch)
    calls["reply"] = {"choices": [{"message": {"content":
                                               '{"picks":[0,2]}'}}]}
    tiles = [_ArkoseImg(), _ArkoseImg(), _ArkoseImg()]
    pg = _ArkosePage(["arkose"], tiles, "Pick the matching pair")
    rec = cs.solve_challenge(pg, "arkose")
    assert rec["provider"] == "arkose"
    assert rec["solved"] is True and rec["rung"] == "llm"
    assert tiles[0].clicked and tiles[2].clicked and not tiles[1].clicked


class _FakePage4NoFrame(_FakePage3):
    # a page whose challenge frame cannot be located at all
    def __init__(self):
        super().__init__(["arkose"], [])

    def frame_locator(self, sel):
        raise RuntimeError("no such frame")


def test_solve_arkose_rotation_and_misses_stay_honest(monkeypatch):
    # rotation shape: ONE image element -> mode "points" -> the llm rung
    # is refused (no verifiable click semantics) -> detect-and-record
    calls = _capture(monkeypatch)
    calls["reply"] = {"choices": [{"message": {"content": '{"picks":[0]}'}}]}
    pg = _ArkosePage(["arkose"], [_ArkoseImg()], "Make it upright")
    rec = cs.solve_challenge(pg, "arkose")
    assert rec["solved"] is False and rec["rung"] is None
    assert "detect" in rec["error"] and "record" in rec["error"]
    # undecidable llm answer: detected and recorded, never faked
    calls["reply"] = None
    tiles = [_ArkoseImg(), _ArkoseImg(), _ArkoseImg()]
    rec3 = cs.solve_challenge(_ArkosePage(["arkose"], tiles, "Match"), "arkose")
    assert rec3["solved"] is False
    assert "detected and recorded" in (rec3["error"] or "")
    # frame unreadable: honest miss
    rec4 = cs.solve_challenge(_FakePage4NoFrame(), "arkose")
    assert rec4["solved"] is False and "frame" in rec4["error"]


def test_solve_challenge_no_llm_degrades_honestly(monkeypatch):
    import captcha_model as _cm2
    # gateway down and no vendored model: the ladder ends in an honest
    # record, never a fabricated token or a fake solve
    pg = _FakePage3(["recaptcha"], [_FakeTile(), _FakeTile()])
    monkeypatch.setattr(cs, "attempt_llm", lambda i, p, t: None)
    monkeypatch.setattr(cs, "engine_rungs", lambda m: ["llm", "cpu"])
    monkeypatch.setattr(_cm2, "has_cpu_runway", lambda: True)
    monkeypatch.setattr(_cm2, "load_classifier", lambda d="cpu": None)
    monkeypatch.setattr(_cm2, "load_clip", lambda d="cpu": None)
    rec = cs.solve_challenge(pg, "recaptcha")
    assert rec["engaged"] is True and rec["solved"] is False
    assert rec["rung"] is None
    assert rec["error"] == "cap: rounds exhausted without a landed solve"
