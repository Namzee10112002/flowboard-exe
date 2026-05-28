from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx
import websockets

from flowboard.db import get_session
from flowboard.db.models import FlowAccount

logger = logging.getLogger(__name__)

FLOW_URL = "https://labs.google/fx/tools/flow"
FLOW_API_PREFIX = "https://aisandbox-pa.googleapis.com/"
TRPC_PREFIX = "https://labs.google/fx/api/trpc/"
SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
DEFAULT_CDP_PORT_BASE = 9400


class FlowBrowserError(RuntimeError):
    """Raised when the managed Chrome profile cannot be controlled."""


def cdp_port_for_account(account_id: int) -> int:
    if account_id <= 0:
        raise FlowBrowserError("account_id_must_be_positive")
    base = int(os.getenv("FLOWBOARD_CDP_PORT_BASE", str(DEFAULT_CDP_PORT_BASE)))
    port = base + account_id
    if port > 65535:
        raise FlowBrowserError("cdp_port_out_of_range")
    return port


def _is_flow_url(url: str | None) -> bool:
    if not isinstance(url, str):
        return False
    return (
        url.startswith("https://labs.google/fx/tools/flow")
        or (url.startswith("https://labs.google/fx/") and "/tools/flow" in url)
    )


def _extract_bearer(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _extract_bearer_from_headers(headers: Any) -> str | None:
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if str(key).lower() == "authorization":
            return _extract_bearer(value)
    return None


def _clone_body_with_captcha(body: Any, captcha_token: str | None) -> Any:
    if not captcha_token or body is None:
        return body
    try:
        final_body = json.loads(json.dumps(body))
    except Exception:
        return body
    ctx = final_body.get("clientContext") if isinstance(final_body, dict) else None
    recaptcha = ctx.get("recaptchaContext") if isinstance(ctx, dict) else None
    if isinstance(recaptcha, dict):
        recaptcha["token"] = captcha_token
    requests = final_body.get("requests") if isinstance(final_body, dict) else None
    if isinstance(requests, list):
        for req in requests:
            req_ctx = req.get("clientContext") if isinstance(req, dict) else None
            req_recaptcha = req_ctx.get("recaptchaContext") if isinstance(req_ctx, dict) else None
            if isinstance(req_recaptcha, dict):
                req_recaptcha["token"] = captcha_token
    return final_body


AUTH_HOOK_JS = r"""
(() => {
  if (window.__flowboardCdpHookInstalled) return true;
  window.__flowboardCdpHookInstalled = true;

  const emitBearer = (value, source) => {
    const match = /^Bearer\s+(.+)/i.exec(String(value || ''));
    const token = match && match[1] ? match[1].trim() : '';
    if (!token || typeof window.flowboardBearerBridge !== 'function') return;
    try {
      window.flowboardBearerBridge(JSON.stringify({
        token,
        source,
        href: location.href,
        ts: Date.now(),
      }));
    } catch {}
  };

  const inspectHeaders = (headers, source) => {
    if (!headers) return;
    try {
      if (typeof Headers !== 'undefined' && headers instanceof Headers) {
        emitBearer(headers.get('authorization'), source);
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
        if (String(key).toLowerCase() === 'authorization') emitBearer(value, source);
      }
    }
  };

  const originalFetch = window.fetch;
  if (typeof originalFetch === 'function') {
    window.fetch = function flowboardCdpFetch(input, init) {
      try {
        inspectHeaders(init && init.headers, 'fetch.init');
        if (typeof Request !== 'undefined' && input instanceof Request) {
          inspectHeaders(input.headers, 'fetch.request');
        }
      } catch {}
      return originalFetch.apply(this, arguments);
    };
  }

  const xhrProto = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  if (xhrProto && xhrProto.setRequestHeader) {
    const originalSetRequestHeader = xhrProto.setRequestHeader;
    xhrProto.setRequestHeader = function flowboardCdpSetRequestHeader(name, value) {
      try {
        if (String(name || '').toLowerCase() === 'authorization') emitBearer(value, 'xhr');
      } catch {}
      return originalSetRequestHeader.apply(this, arguments);
    };
  }
  return true;
})()
"""


@dataclass
class CdpPageSession:
    ws_url: str
    on_token: Optional[Callable[[str, str], None]] = None
    _ws: Any = None
    _next_id: int = 1
    _pending: dict[int, asyncio.Future] = field(default_factory=dict)
    _reader_task: Optional[asyncio.Task] = None

    async def __aenter__(self) -> "CdpPageSession":
        self._ws = await websockets.connect(self.ws_url, max_size=64 * 1024 * 1024)
        self._reader_task = asyncio.create_task(self._reader(), name="flow-cdp-reader")
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._ws is not None:
            await self._ws.close()
        if self._reader_task:
            await asyncio.gather(self._reader_task, return_exceptions=True)

    async def send(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if self._ws is None:
            raise FlowBrowserError("cdp_not_connected")
        msg_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        await self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(msg_id, None)
            raise FlowBrowserError(f"cdp_timeout:{method}") from exc
        if isinstance(resp, dict) and resp.get("error"):
            message = resp.get("error", {}).get("message") if isinstance(resp.get("error"), dict) else resp["error"]
            raise FlowBrowserError(f"cdp_error:{method}:{message}")
        return resp if isinstance(resp, dict) else {}

    async def _reader(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_id = data.get("id")
            if isinstance(msg_id, int):
                fut = self._pending.pop(msg_id, None)
                if fut is not None and not fut.done():
                    fut.set_result(data)
                continue
            self._handle_event(data)

    def _handle_event(self, data: dict[str, Any]) -> None:
        method = data.get("method")
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        token: str | None = None
        source = str(method or "cdp")
        if method == "Runtime.bindingCalled" and params.get("name") == "flowboardBearerBridge":
            payload = params.get("payload")
            try:
                parsed = json.loads(payload) if isinstance(payload, str) else {}
            except json.JSONDecodeError:
                parsed = {}
            token = parsed.get("token") if isinstance(parsed, dict) else None
            source = parsed.get("source") if isinstance(parsed, dict) else source
        elif method in ("Network.requestWillBeSent", "Network.requestWillBeSentExtraInfo"):
            headers = params.get("headers")
            if not isinstance(headers, dict):
                req = params.get("request") if isinstance(params.get("request"), dict) else {}
                headers = req.get("headers") if isinstance(req.get("headers"), dict) else {}
            token = _extract_bearer_from_headers(headers)
        if token and self.on_token:
            self.on_token(token, source)


class FlowBrowserBridge:
    async def wait_until_ready(self, account_id: int, timeout: float = 5.0) -> bool:
        port = cdp_port_for_account(account_id)
        deadline = time.monotonic() + timeout
        url = f"http://127.0.0.1:{port}/json/version"
        async with httpx.AsyncClient(timeout=1.0) as client:
            while time.monotonic() < deadline:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
        return False

    async def capture_flow_token(self, account_id: int, timeout: float = 20.0) -> str | None:
        target = await self._ensure_flow_target(account_id)
        ws_url = target.get("webSocketDebuggerUrl")
        if not isinstance(ws_url, str) or not ws_url:
            raise FlowBrowserError("cdp_target_has_no_websocket")

        captured: list[str] = []

        def on_token(token: str, source: str) -> None:
            if token:
                logger.info("flow token captured via cdp:%s (len=%d)", source, len(token))
                captured.append(token)

        async with CdpPageSession(ws_url, on_token=on_token) as cdp:
            await self._prepare_page(cdp)
            await self._install_auth_hook(cdp)
            await self._poke_flow_page(cdp)
            token = await self._wait_for_token(captured, timeout=min(timeout, 3.0))
            if token:
                return token

            await cdp.send("Page.reload", {"ignoreCache": True}, timeout=5.0)
            return await self._wait_for_token(captured, timeout=max(1.0, timeout - 3.0))

    async def api_request(
        self,
        account_id: int,
        *,
        url: str,
        method: str = "POST",
        headers: Optional[dict[str, Any]] = None,
        body: Any = None,
        captcha_action: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        if not url.startswith(FLOW_API_PREFIX):
            return {"status": 400, "error": "INVALID_URL"}
        token = await self._token_for_account(account_id)
        if not token:
            try:
                token = await self.capture_flow_token(account_id, timeout=15.0)
            except FlowBrowserError as exc:
                return {"status": 503, "error": str(exc)}
        if not token:
            return {"status": 503, "error": "NO_FLOW_KEY"}

        target = await self._ensure_flow_target(account_id)
        ws_url = target.get("webSocketDebuggerUrl")
        if not isinstance(ws_url, str) or not ws_url:
            return {"status": 503, "error": "cdp_target_has_no_websocket"}

        captured: list[str] = []

        async with CdpPageSession(ws_url, on_token=lambda t, _s: captured.append(t)) as cdp:
            await self._prepare_page(cdp)
            await self._install_auth_hook(cdp)
            captcha_token = None
            if captcha_action:
                captcha_token = await self._solve_captcha(cdp, captcha_action)
                if not captcha_token:
                    return {"status": 403, "error": "CAPTCHA_FAILED"}

            final_body = _clone_body_with_captcha(body, captcha_token)
            fetch_headers = {**(headers or {}), "authorization": f"Bearer {token}"}
            payload = {
                "url": url,
                "method": method or "POST",
                "headers": fetch_headers,
                "body": final_body,
            }
            resp = await self._page_fetch(cdp, payload, timeout=timeout or 180.0)
            if captured:
                await self._remember_token(account_id, captured[-1])
            return resp

    async def trpc_request(
        self,
        account_id: int,
        *,
        url: str,
        method: str = "POST",
        headers: Optional[dict[str, Any]] = None,
        body: Any = None,
        timeout: Optional[float] = 30.0,
    ) -> dict[str, Any]:
        if not url.startswith(TRPC_PREFIX):
            return {"error": "INVALID_TRPC_URL"}
        token = await self._token_for_account(account_id)
        if not token:
            try:
                token = await self.capture_flow_token(account_id, timeout=15.0)
            except FlowBrowserError as exc:
                return {"error": str(exc)}

        target = await self._ensure_flow_target(account_id)
        ws_url = target.get("webSocketDebuggerUrl")
        if not isinstance(ws_url, str) or not ws_url:
            return {"error": "cdp_target_has_no_websocket"}

        fetch_headers = {"Content-Type": "application/json", **(headers or {})}
        if token:
            fetch_headers["authorization"] = f"Bearer {token}"
        payload = {
            "url": url,
            "method": method or "POST",
            "headers": fetch_headers,
            "body": body,
        }
        async with CdpPageSession(ws_url) as cdp:
            await self._prepare_page(cdp)
            await self._install_auth_hook(cdp)
            return await self._page_fetch(cdp, payload, timeout=timeout or 30.0)

    async def fetch_user_info(self, token: str) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    async def _ensure_flow_target(self, account_id: int) -> dict[str, Any]:
        port = cdp_port_for_account(account_id)
        base = f"http://127.0.0.1:{port}"
        if not await self.wait_until_ready(account_id, timeout=0.5):
            try:
                from flowboard.services.chrome_profile import launch_flow_account_profile

                launch_flow_account_profile(account_id)
            except Exception as exc:  # noqa: BLE001
                raise FlowBrowserError(f"cdp_debugger_not_ready:{exc}") from exc
            if not await self.wait_until_ready(account_id, timeout=8.0):
                raise FlowBrowserError("cdp_debugger_not_ready")

        async with httpx.AsyncClient(timeout=4.0) as client:
            try:
                targets = (await client.get(f"{base}/json/list")).json()
            except Exception as exc:
                raise FlowBrowserError("cdp_debugger_not_ready") from exc
            if isinstance(targets, list):
                for target in targets:
                    if (
                        isinstance(target, dict)
                        and target.get("type") == "page"
                        and _is_flow_url(target.get("url"))
                    ):
                        return target
            quoted = urllib.parse.quote(FLOW_URL, safe=":/?=&")
            for method in ("PUT", "GET"):
                try:
                    resp = await client.request(method, f"{base}/json/new?{quoted}")
                    if resp.status_code < 400:
                        data = resp.json()
                        if isinstance(data, dict):
                            return data
                except Exception:
                    continue
        raise FlowBrowserError("cdp_cannot_open_flow_tab")

    async def _prepare_page(self, cdp: CdpPageSession) -> None:
        for method, params in (
            ("Page.enable", {}),
            ("Runtime.enable", {}),
            ("Network.enable", {}),
        ):
            try:
                await cdp.send(method, params, timeout=5.0)
            except FlowBrowserError:
                logger.debug("cdp prepare ignored failure for %s", method, exc_info=True)
        try:
            await cdp.send("Runtime.addBinding", {"name": "flowboardBearerBridge"}, timeout=5.0)
        except FlowBrowserError:
            logger.debug("cdp binding may already exist", exc_info=True)

    async def _install_auth_hook(self, cdp: CdpPageSession) -> None:
        try:
            await cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": AUTH_HOOK_JS}, timeout=5.0)
        except FlowBrowserError:
            logger.debug("cdp add init script failed", exc_info=True)
        await self._runtime_eval(cdp, AUTH_HOOK_JS, timeout=5.0)

    async def _poke_flow_page(self, cdp: CdpPageSession) -> None:
        expr = """
        (async () => {
          try { await fetch('/fx/tools/flow', { credentials: 'include' }); } catch {}
          return true;
        })()
        """
        try:
            await self._runtime_eval(cdp, expr, timeout=5.0)
        except FlowBrowserError:
            logger.debug("cdp token poke failed", exc_info=True)

    async def _solve_captcha(self, cdp: CdpPageSession, action: str) -> str | None:
        token = await self._solve_captcha_once(cdp, action)
        if token:
            return token
        try:
            await cdp.send("Page.reload", {"ignoreCache": True}, timeout=5.0)
            await asyncio.sleep(3.0)
            await self._install_auth_hook(cdp)
        except FlowBrowserError:
            return None
        return await self._solve_captcha_once(cdp, action)

    async def _solve_captcha_once(self, cdp: CdpPageSession, action: str) -> str | None:
        payload = json.dumps({"siteKey": SITE_KEY, "action": action}, ensure_ascii=False)
        expr = f"""
        (async () => {{
          const args = {payload};
          const deadline = Date.now() + 10000;
          while (!(window.grecaptcha && window.grecaptcha.enterprise && window.grecaptcha.enterprise.execute)) {{
            if (Date.now() > deadline) throw new Error('grecaptcha not available');
            await new Promise((resolve) => setTimeout(resolve, 200));
          }}
          return await window.grecaptcha.enterprise.execute(args.siteKey, {{ action: args.action }});
        }})()
        """
        try:
            value = await self._runtime_eval(cdp, expr, timeout=15.0)
        except FlowBrowserError:
            return None
        return value if isinstance(value, str) and value else None

    async def _page_fetch(
        self,
        cdp: CdpPageSession,
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        args = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        expr = f"""
        (async () => {{
          const args = {args};
          const resp = await fetch(args.url, {{
            method: args.method || 'POST',
            headers: args.headers || {{}},
            credentials: 'include',
            body: (args.method || 'POST').toUpperCase() === 'GET'
              ? undefined
              : JSON.stringify(args.body),
          }});
          const text = await resp.text();
          let data;
          try {{ data = JSON.parse(text); }} catch {{ data = text; }}
          return {{ status: resp.status, data }};
        }})()
        """
        try:
            value = await self._runtime_eval(cdp, expr, timeout=timeout)
        except FlowBrowserError as exc:
            return {"status": 500, "error": str(exc)}
        return value if isinstance(value, dict) else {"status": 500, "error": "INVALID_CDP_FETCH_RESULT"}

    async def _runtime_eval(self, cdp: CdpPageSession, expression: str, timeout: float = 30.0) -> Any:
        resp = await cdp.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        result = resp.get("result") if isinstance(resp.get("result"), dict) else {}
        if result.get("exceptionDetails"):
            text = result.get("exceptionDetails", {}).get("text") or "runtime_exception"
            raise FlowBrowserError(str(text))
        inner = result.get("result") if isinstance(result.get("result"), dict) else {}
        return inner.get("value")

    async def _wait_for_token(self, captured: list[str], timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if captured:
                return captured[-1]
            await asyncio.sleep(0.2)
        return captured[-1] if captured else None

    async def _token_for_account(self, account_id: int) -> str | None:
        from flowboard.services.flow_client import flow_client

        ctx = flow_client.get_account_context(account_id)
        token = ctx.get("flow_key")
        if isinstance(token, str) and token:
            return token
        with get_session() as s:
            row = s.get(FlowAccount, account_id)
            if row is not None and isinstance(row.credential, str) and row.credential.strip():
                return row.credential.strip()
        return None

    async def _remember_token(self, account_id: int, token: str) -> None:
        if not token:
            return
        from flowboard.services.flow_client import flow_client

        flow_client.set_account_context(account_id, flow_key=token)


flow_browser = FlowBrowserBridge()
