#!/usr/bin/env python3
"""fp_shield.py - default fingerprint shield for greysearch slot browsers.

Owner directive 2026-11-23: every slot browser presents as a normal Windows
Chrome on integrated graphics with Windows fonts; battery is not exposed; the
UA switcher is on by default. No extension can ride the headless CDP lane, and
patchright drops Context.add_init_script on connect_over_cdp-attached contexts
(verified 2026-11-23: a no-op init script never ran), so the shield is applied
per page through raw CDP: Page.addScriptToEvaluateOnNewDocument (navigator,
screen, battery, userAgentData, WebGL vendor/renderer) plus
Network.setUserAgentOverride so the sec-ch-ua client-hint headers agree with
the navigator surface. Fonts come from the OS (mscorefonts + carlito).
"""
from __future__ import annotations

WINDOWS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/156.0.0.0 Safari/537.36")

UA_METADATA = {
    "brands": [
        {"brand": "Chromium", "version": "156"},
        {"brand": "Google Chrome", "version": "156"},
        {"brand": "Not?A_Brand", "version": "24"},
    ],
    "fullVersion": "156.0.8078.79",
    "fullVersionList": [
        {"brand": "Chromium", "version": "156.0.8078.79"},
        {"brand": "Google Chrome", "version": "156.0.8078.79"},
        {"brand": "Not?A_Brand", "version": "24.0.0.0"},
    ],
    "platform": "Windows",
    "platformVersion": "15.0.0",
    "architecture": "x86",
    "model": "",
    "mobile": False,
    "bitness": "64",
}

SHIELD_JS = r"""
(() => {
  const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36";
  const over = (o, k, v) => { try { Object.defineProperty(o, k, {get: () => v, configurable: true}); } catch (e) {} };
  const nOver = (k, v) => {
    over(navigator, k, v);
    try { const p = Object.getPrototypeOf(navigator); if (p && p !== Object.prototype) over(p, k, v); } catch (e) {}
  };
  nOver('userAgent', UA);
  nOver('appVersion', UA.slice(UA.indexOf('Mozilla/5.0 ') + 12));
  nOver('platform', 'Win32');
  nOver('oscpu', undefined);
  nOver('vendor', 'Google Inc.');
  nOver('hardwareConcurrency', 8);
  nOver('deviceMemory', 8);
  nOver('maxTouchPoints', 0);
  over(screen, 'availWidth', screen.width);
  over(screen, 'availHeight', screen.height - 40);
  try {
    delete navigator.getBattery;
    Object.defineProperty(navigator, 'getBattery', {get: () => undefined, configurable: true});
    const np = Object.getPrototypeOf(navigator);
    if (np && np !== Object.prototype && 'getBattery' in np) Object.defineProperty(np, 'getBattery', {get: () => undefined, configurable: true});
  } catch (e) {}
  if (navigator.userAgentData) {
    over(navigator.userAgentData, 'brands', [{brand: 'Chromium', version: '156'}, {brand: 'Google Chrome', version: '156'}, {brand: 'Not?A_Brand', version: '24'}]);
    over(navigator.userAgentData, 'mobile', false);
    over(navigator.userAgentData, 'platform', 'Windows');
    try {
      navigator.userAgentData.getHighEntropyValues = async (hints) => ({
        architecture: 'x86', bitness: '64', model: '', mobile: false,
        platform: 'Windows', platformVersion: '15.0.0',
        uaFullVersion: '156.0.8078.79',
        fullVersionList: [{brand: 'Chromium', version: '156.0.8078.79'}, {brand: 'Google Chrome', version: '156.0.8078.79'}, {brand: 'Not?A_Brand', version: '24.0.0.0'}],
      });
    } catch (e) {}
  }
  const vendor = 'Intel Inc.';
  const renderer = 'Intel(R) Iris(R) Xe Graphics';
  const patchGL = (proto) => {
    if (!proto || !proto.getParameter) return;
    const gp = proto.getParameter;
    proto.getParameter = function (p) {
      if (p === 37445) return vendor;
      if (p === 37446) return renderer;
      return gp.call(this, p);
    };
  };
  try {
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (t, ...a) {
      const c = orig.call(this, t, ...a);
      if (c && (t === 'webgl' || t === 'experimental-webgl' || t === 'webgl2')) patchGL(Object.getPrototypeOf(c));
      return c;
    };
  } catch (e) {}
})();
"""


def shield_page(page) -> None:
    """Register the shield on ONE page via raw CDP so every navigation in
    that page's lifetime runs it, and align the client-hint headers.

    Works on connect_over_cdp-attached contexts (Context.add_init_script
    does not run there). Never raises: a shield miss must not break a fetch.
    """
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send("Page.addScriptToEvaluateOnNewDocument",
                 {"source": SHIELD_JS, "worldName": ""})
    except Exception:
        pass
    # Verified 2026-11-23: on connect_over_cdp-attached contexts patchright
    # drops BOTH Context.add_init_script AND addScriptToEvaluateOnNewDocument
    # (even with runImmediately:true - an identifier comes back but the script
    # never executes). The header override below still works, but the JS half
    # must be driven per navigation: re-run the shield on the main frame each
    # time the document changes. Runs before onload; a document-start read can
    # still race it, which is strictly better than no JS shield at all.
    def _run_shield(obj):
        # patchright emits domcontentloaded with the PAGE object, not a frame
        try:
            f = obj if getattr(obj, "parent_frame", "missing") == None else obj.main_frame
            f.evaluate(SHIELD_JS)
        except Exception:
            pass
    try:
        page.on("domcontentloaded", _run_shield)
        if page.main_frame and page.main_frame.url:
            _run_shield(page.main_frame)
    except Exception:
        pass
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send("Network.setUserAgentOverride", {
            "userAgent": WINDOWS_UA,
            "acceptLanguage": "en-US,en;q=0.9",
            "platform": "Win32",
            "userAgentMetadata": UA_METADATA,
        })
    except Exception:
        pass


def apply_page(page) -> None:
    """Legacy alias - shield_page now does both jobs on one call."""
    shield_page(page)


def install_on_context(context) -> None:
    """Best-effort context-level install for direct-handle launches, where
    Context.add_init_script DOES run. Attached contexts need shield_page."""
    try:
        context.add_init_script(SHIELD_JS)
    except Exception:
        pass
