
(() => {
  const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36";
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
    over(navigator.userAgentData, 'brands', [{brand: 'Chromium', version: '151'}, {brand: 'Google Chrome', version: '151'}, {brand: 'Not?A_Brand', version: '24'}]);
    over(navigator.userAgentData, 'mobile', false);
    over(navigator.userAgentData, 'platform', 'Windows');
    try {
      navigator.userAgentData.getHighEntropyValues = async (hints) => ({
        architecture: 'x86', bitness: '64', model: '', mobile: false,
        platform: 'Windows', platformVersion: '15.0.0',
        uaFullVersion: '151.0.7922.34',
        fullVersionList: [{brand: 'Chromium', version: '151.0.7922.34'}, {brand: 'Google Chrome', version: '151.0.7922.34'}, {brand: 'Not?A_Brand', version: '24.0.0.0'}],
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

window.__shieldLive = true;
