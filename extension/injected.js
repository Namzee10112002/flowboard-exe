/**
 * Injected into MAIN world on labs.google — has access to window.grecaptcha.
 * Used solely for reCAPTCHA solving. Media URLs come from the generation API
 * response directly (agent extracts fifeUrl from data.media[].image), so no
 * TRPC response interception is needed.
 */
const SITE_KEY = '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV';

function emitBearer(value, source) {
  const match = /^Bearer\s+(.+)/i.exec(String(value || ''));
  const token = match?.[1]?.trim();
  if (!token) return;
  window.dispatchEvent(new CustomEvent('FLOWBOARD_BEARER_TOKEN', {
    detail: { token, source },
  }));
}

function inspectHeaders(headers, source) {
  if (!headers) return;
  try {
    if (typeof Headers !== 'undefined' && headers instanceof Headers) {
      const auth = headers.get('authorization');
      if (auth) emitBearer(auth, source);
      return;
    }
  } catch {}
  if (Array.isArray(headers)) {
    for (const pair of headers) {
      if (Array.isArray(pair) && String(pair[0] || '').toLowerCase() === 'authorization') {
        emitBearer(pair[1], source);
      }
    }
    return;
  }
  if (typeof headers === 'object') {
    for (const [key, value] of Object.entries(headers)) {
      if (key.toLowerCase() === 'authorization') emitBearer(value, source);
    }
  }
}

function installAuthCaptureHooks() {
  if (window.__flowboardAuthHooksInstalled) return;
  window.__flowboardAuthHooksInstalled = true;

  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = function flowboardFetch(input, init) {
      try {
        inspectHeaders(init?.headers, 'fetch.init');
        if (typeof Request !== 'undefined' && input instanceof Request) {
          inspectHeaders(input.headers, 'fetch.request');
        }
      } catch {}
      return originalFetch.apply(this, arguments);
    };
  }

  const xhrProto = window.XMLHttpRequest?.prototype;
  if (xhrProto?.setRequestHeader) {
    const originalSetRequestHeader = xhrProto.setRequestHeader;
    xhrProto.setRequestHeader = function flowboardSetRequestHeader(name, value) {
      try {
        if (String(name || '').toLowerCase() === 'authorization') {
          emitBearer(value, 'xhr');
        }
      } catch {}
      return originalSetRequestHeader.apply(this, arguments);
    };
  }
}

installAuthCaptureHooks();

window.addEventListener('GET_CAPTCHA', async ({ detail }) => {
  const { requestId, pageAction } = detail;
  try {
    await waitForGrecaptcha();
    const token = await window.grecaptcha.enterprise.execute(SITE_KEY, {
      action: pageAction,
    });
    window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
      detail: { requestId, token },
    }));
  } catch (e) {
    window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
      detail: { requestId, error: e.message },
    }));
  }
});

function waitForGrecaptcha(timeout = 10000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      if (window.grecaptcha?.enterprise?.execute) return resolve();
      if (Date.now() - start > timeout) return reject(new Error('grecaptcha not available'));
      setTimeout(check, 200);
    };
    check();
  });
}
