"""The AVS web app (FastAPI). It is reached over the VPN; it never serves the home LAN or the internet (see core.allowed).

  /                 the app (View, Files)                    needs the access link once (sets a cookie)
  /api/state        what is on screen + both file lists
  /api/files/...    download, upload, delete
  /v/<token>/...    the pages the agent shows. The token is in the path because the page is sandboxed in an iframe
                    (no cookies, no way to call the API) and its own relative links must keep working.
"""
import hmac
import json
import mimetypes
import re
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from . import codex, core, view

CORS = {"Access-Control-Allow-Origin": "*"}

STATIC = Path(__file__).parent / "static"
COOKIE = "avs"
OC_PREFIX = "/oc"


def opencode_html(text: str) -> str:
    """Keep the app's root-relative assets on AVS rather than escaping the proxy mount, and load the event-stream shim before the app."""
    text = re.sub(r'(\b(?:src|href|action|poster)=["\'])/(?!/)', lambda m: m.group(1) + OC_PREFIX + "/", text)
    shim = '<script src="/static/oc-stream.js"></script>'
    return re.sub(r'<head[^>]*>', lambda m: m.group(0) + shim, text, count=1) if shim not in text else text


def opencode_javascript(text: str) -> str:
    """OpenCode's production bundle assumes an origin root; adapt its server, router and Vite preload base."""
    text = re.sub(r'(\w+=\(\)=>location\.hostname\.includes\("opencode\.ai"\)\?"http://localhost:4096":location\.origin)',
                  lambda m: m.group(1) + f'+"{OC_PREFIX}"', text)
    text = re.sub(r'f\((\w+),\{get component\(\)\{return (\w+)\.router\?\?(\w+)\}',
                  lambda m: 'f(' + m[1] + ',{base:"' + OC_PREFIX + '",get component(){return ' + m[2] + '.router??' + m[3] + '}', text)
    text = re.sub(r'(\w+)=function\((\w+)\)\{return"/"\+\2\}',
                  lambda m: m[1] + '=function(' + m[2] + '){return"' + OC_PREFIX + '/"+' + m[2] + '}', text)
    # Asset paths written as literals (web workers, the icon sprite) do not go through the patched base above
    return re.sub(r'(["\'`])/assets/', lambda m: m.group(1) + OC_PREFIX + "/assets/", text)


def make_app(cfg: dict | None = None) -> FastAPI:
    cfg = cfg or core.load_config()
    root = core.root_of(cfg)
    app = FastAPI(title="AVS", docs_url=None, redoc_url=None, openapi_url=None)
    token = cfg["token"]

    def ok(given: str | None) -> bool:
        return bool(given) and hmac.compare_digest(given, token)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        ip = request.client.host if request.client else ""
        if not core.allowed(ip, cfg):
            return Response("Not reachable from here. Pair the AVS app first.", status_code=403)
        path = request.url.path
        if path.startswith(("/v/", "/p/")):                                  # the token is the second path part
            if not ok(path.split("/")[2] if path.count("/") >= 2 else ""):
                return Response("Forbidden", status_code=403)
        elif path not in ("/manifest.webmanifest", "/api/ping") and not path.startswith("/static/"):
            if ok(request.query_params.get("t")):                   # the access link: remember it, then drop it from the address
                r = RedirectResponse(path, status_code=303)
                r.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
                return r
            if not ok(request.cookies.get(COOKIE)):
                return Response("Open the access link from the agent (avs link).", status_code=401)
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.get("/")
    def index():
        return HTMLResponse((STATIC / "index.html").read_text())

    @app.get("/api/ping")
    def ping():                                     # lets the phone tell "this is my PC" from any other web server
        return {"avs": 1}

    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse({"name": "Agent View and Send", "short_name": "AVS", "start_url": "/", "display": "standalone",
                             "background_color": "#15171a", "theme_color": "#15171a",
                             "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]},
                            media_type="application/manifest+json")

    @app.get("/static/{name}")
    def static(name: str):
        p = core.inside(STATIC, name)
        if not p or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    def opencode_target():
        from urllib.parse import unquote, urlsplit, urlunsplit
        raw = cfg.get("opencode_url", "")
        if not raw:
            return None
        u = urlsplit(raw)
        if u.scheme not in ("http", "https") or not u.hostname:
            return None
        base = urlunsplit((u.scheme, u.netloc.rsplit("@", 1)[-1], "", "", ""))
        auth = (unquote(u.username or "opencode"), unquote(u.password or "")) if u.password else None
        return base, auth, u.path

    bridge = {}

    def codex_directory() -> str:
        return cfg.get("codex_directory") or str(Path.home())

    @app.websocket("/codex-ws")
    async def codex_socket(websocket: WebSocket):
        """The Codex tab's one connection: JSON requests in (answered by id), Codex's notifications and questions out."""
        import asyncio
        import contextlib
        if not websocket.client or not core.allowed(websocket.client.host, cfg) or not ok(websocket.cookies.get(COOKIE)):
            await websocket.close(code=4401)
            return
        binary = codex.find(cfg)
        if not binary:
            await websocket.close(code=4404)
            return
        if "b" not in bridge:
            bridge["b"] = codex.Bridge(binary, codex_directory())
        b = bridge["b"]
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        b.listeners.add(queue)
        for ask in b.pending():
            queue.put_nowait(ask)

        async def to_phone():
            while True:
                await websocket.send_json(await queue.get())

        async def run(msg):
            try:
                params = msg.get("params") or {}
                if msg["method"] == "thread/start":
                    params.setdefault("cwd", codex_directory())
                    params.setdefault("serviceName", "avs")
                reply = {"id": msg["id"], "result": await b.call(msg["method"], params)}
            except codex.RpcError as e:
                reply = {"id": msg["id"], "error": {"message": str(e)}}
            except Exception as e:
                reply = {"id": msg["id"], "error": {"message": f"Could not reach Codex: {e}"}}
            with contextlib.suppress(Exception):
                await websocket.send_json(reply)

        sender = asyncio.create_task(to_phone())
        jobs: set = set()
        try:
            while True:
                msg = await websocket.receive_json()
                if not isinstance(msg, dict):
                    continue
                if "answer" in msg and isinstance(msg.get("result"), dict):
                    await b.answer(msg["answer"], msg["result"])
                elif isinstance(msg.get("method"), str) and "id" in msg:
                    job = asyncio.create_task(run(msg))
                    jobs.add(job)
                    job.add_done_callback(jobs.discard)
        except Exception:
            pass
        finally:
            b.listeners.discard(queue)
            for t in (sender, *jobs):
                t.cancel()
            with contextlib.suppress(RuntimeError):
                await websocket.close()

    @app.get("/api/state")
    def state():
        st = core.get_state(root)
        view = dict(st)
        if st.get("kind") == "file":
            view["src"] = f"/v/{token}/{st['path']}"
        elif st.get("kind") == "proxy":
            view["src"] = f"/p/{token}/{st['port']}/{st.get('rest', '')}"
        target = opencode_target()
        return {"view": view, "outbox": core.listing(root, "outbox"), "inbox": core.listing(root, "inbox"),
                "opencode_url": OC_PREFIX + target[2] if target else "", "opencode_directory": cfg.get("opencode_directory", "") if target else "",
                "codex": bool(codex.find(cfg)), "codex_directory": codex_directory()}

    def box_file(box: str, name: str) -> Path:
        if box not in core.BOXES:
            raise HTTPException(404)
        p = core.inside(root / box, core.safe_name(name))
        if not p or not p.is_file():
            raise HTTPException(404, "No such file")
        return p

    @app.get("/api/files/{box}/{name}")
    def download(box: str, name: str):
        p = box_file(box, name)
        return FileResponse(p, filename=p.name, media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream")

    @app.delete("/api/files/{box}/{name}")
    def remove(box: str, name: str):
        box_file(box, name).unlink()
        return {"ok": True}

    @app.api_route(OC_PREFIX, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    @app.api_route(OC_PREFIX + "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    async def opencode_proxy(request: Request, path: str = ""):
        """The whole OpenCode app, not just its session API, travels through AVS's pairing cookie and tunnel."""
        import base64
        import httpx
        target = opencode_target()
        if not target:
            raise HTTPException(503, "OpenCode is not configured")
        base, auth, _ = target
        hop = {"host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "cookie", "authorization", "origin", "referer"}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in hop}
        headers["Origin"] = base
        if auth:
            headers["Authorization"] = "Basic " + base64.b64encode((auth[0] + ":" + auth[1]).encode()).decode()
        params = [(k, v) for k, v in request.query_params.multi_items() if k != "t"]
        client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
        try:
            up = await client.send(client.build_request(request.method, base + "/" + path, params=params, headers=headers,
                                                        content=request.stream()), stream=True)
        except httpx.HTTPError as e:
            await client.aclose()
            raise HTTPException(502, f"Could not reach OpenCode: {e}")

        content_type = up.headers.get("content-type", "")
        rewrite = "html" in content_type or "javascript" in content_type or "css" in content_type or path.endswith((".html", ".js", ".css"))
        drop = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
        if rewrite:
            drop |= {"content-length", "content-encoding", "etag", "content-md5"}
        out_headers = {k: v for k, v in up.headers.items() if k.lower() not in drop}
        location = up.headers.get("location", "")
        if location.startswith("/") and not location.startswith("//"):
            out_headers["location"] = OC_PREFIX + location
        if rewrite:
            raw = await up.aread()
            await up.aclose(); await client.aclose()
            text = raw.decode("utf-8", "replace")
            if "html" in content_type or path.endswith(".html"):
                text = opencode_html(text)
            elif "css" in content_type or path.endswith(".css"):
                text = re.sub(r'(url\(["\']?)/(?!/)', lambda m: m.group(1) + OC_PREFIX + "/", text)
            else:
                text = opencode_javascript(text)
            return Response(text.encode(), status_code=up.status_code, headers=out_headers)

        async def body():
            try:
                async for chunk in up.aiter_raw():
                    yield chunk
            finally:
                await up.aclose()
                await client.aclose()
        return StreamingResponse(body(), status_code=up.status_code, headers=out_headers)

    @app.websocket("/oc-stream/{path:path}")
    async def opencode_stream(websocket: WebSocket, path: str):
        """One of OpenCode's event streams (/event, /global/event, ...) carried over a WebSocket. Cloudflare's quick tunnel holds a streamed
        HTTP response back until tens of KB have built up, so OpenCode's small live events never reached the phone away from home;
        WebSocket frames pass through at once. Each message is a piece of the upstream text/event-stream, unchanged."""
        import asyncio
        import contextlib
        import httpx
        if not websocket.client or not core.allowed(websocket.client.host, cfg) or not ok(websocket.cookies.get(COOKIE)):
            await websocket.close(code=4401)
            return
        target = opencode_target()
        if not target or not path.endswith("event"):                    # event streams only; everything else uses the HTTP proxy
            await websocket.close(code=4404)
            return
        base, auth, _ = target
        params = [(k, v) for k, v in websocket.query_params.multi_items() if k != "t"]
        headers = {"Accept": "text/event-stream", "Accept-Encoding": "identity"}
        directory = websocket.query_params.get("directory") or cfg.get("opencode_directory")
        if directory:
            headers["x-opencode-directory"] = directory
        await websocket.accept()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None), auth=auth) as client:
                async with client.stream("GET", base + "/" + path, params=params, headers=headers) as up:
                    if up.status_code != 200:
                        await websocket.close(code=4502, reason=f"OpenCode answered {up.status_code}")
                        return

                    async def pump():
                        async for chunk in up.aiter_text():
                            await websocket.send_text(chunk)

                    async def watch():
                        while (await websocket.receive())["type"] != "websocket.disconnect":
                            pass

                    tasks = [asyncio.create_task(pump()), asyncio.create_task(watch())]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
        except httpx.HTTPError:
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1011, reason="Could not reach OpenCode")
            return
        with contextlib.suppress(RuntimeError):
            await websocket.close()

    @app.websocket(OC_PREFIX + "/{path:path}")
    async def opencode_websocket(websocket: WebSocket, path: str):
        """HTTP middleware does not guard upgrades, so check the same AVS cookie and peer network here."""
        import asyncio
        import base64
        import contextlib
        from urllib.parse import urlencode
        import websockets
        if not websocket.client or not core.allowed(websocket.client.host, cfg) or not ok(websocket.cookies.get(COOKIE)):
            await websocket.close(code=4401)
            return
        target = opencode_target()
        if not target:
            await websocket.close(code=4503)
            return
        base, auth, _ = target
        query = urlencode([(k, v) for k, v in websocket.query_params.multi_items() if k != "t"])
        uri = ("wss:" if base.startswith("https:") else "ws:") + base.split(":", 1)[1] + "/" + path + (("?" + query) if query else "")
        headers = {"Authorization": "Basic " + base64.b64encode((auth[0] + ":" + auth[1]).encode()).decode()} if auth else {}
        await websocket.accept()
        try:
            async with websockets.connect(uri, origin=base, additional_headers=headers, proxy=None, max_size=None) as upstream:
                async def downstream():
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)

                async def upstream_messages():
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("text") is not None:
                            await upstream.send(message["text"])
                        elif message.get("bytes") is not None:
                            await upstream.send(message["bytes"])

                tasks = [asyncio.create_task(downstream()), asyncio.create_task(upstream_messages())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1011)
            raise
        with contextlib.suppress(RuntimeError):
            await websocket.close()

    @app.post("/api/push")
    async def push(request: Request):
        """The phone says where its push distributor wants alerts ("" = back to the ntfy topic)."""
        try:
            url = str((await request.json()).get("endpoint", ""))
        except ValueError:
            raise HTTPException(400, "Expected JSON")
        if url and (not re.match(r"^https?://[^\s]+$", url) or len(url) > 1000):
            raise HTTPException(400, "Not a web address")
        core.set_push_endpoint(url)
        return {"ok": True}

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...)):
        limit = cfg["max_upload_mb"] * 1024 * 1024
        saved, paths = [], []
        for f in files:
            dest = core.unique(root / "inbox", f.filename or "file")
            size = 0
            try:
                with open(dest, "wb") as out:
                    while chunk := await f.read(1 << 20):
                        size += len(chunk)
                        if size > limit:
                            raise HTTPException(413, f"{f.filename} is over the {cfg['max_upload_mb']} MB limit")
                        out.write(chunk)
            except BaseException:
                dest.unlink(missing_ok=True)
                raise
            saved.append(dest.name)
            paths.append(str(dest))
        import shutil, subprocess
        if shutil.which("notify-send"):                              # a desktop note on the PC too
            subprocess.Popen(["notify-send", "-a", "AVS", "File from your phone", ", ".join(saved)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"saved": saved, "paths": paths}

    @app.get("/api/comments")
    def comments_get(page: str = ""):
        return core.comments_for(root, page)

    @app.put("/api/comments")
    async def comments_put(request: Request):
        try:
            b = await request.json()
            page, item, text = b["page"], b.get("item", ""), b.get("text", "")
            title, src = str(b.get("title", ""))[:300], str(b.get("src", ""))[:1000]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "A comment needs a page and text.")
        if not all(isinstance(x, str) for x in (page, item, text)) or not 0 < len(page) <= 1000 or len(item) > 400 or len(text) > 20000:
            raise HTTPException(400, "That comment is too long or malformed.")
        core.comment_set(root, page, item, text, title, src)
        import shutil, subprocess
        if text.strip() and shutil.which("notify-send"):                    # a desktop note on the PC too
            subprocess.Popen(["notify-send", "-a", "AVS", "Comment from your phone", f"{title or page}: {text[:120]}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True}

    @app.get("/v/{tok}/{path:path}")
    def view_file(tok: str, path: str):
        p = core.inside(root / "view", path)
        if not p or not p.is_file():
            raise HTTPException(404)
        if p.suffix == ".html":                          # opening a page counts as using it, so a Markdown build stays while it is read
            view.touch(root, path.partition("/")[0])
        return FileResponse(p, headers=CORS)            # a framed page has no origin of its own: its module scripts and fetches need this

    @app.get("/p/{tok}/{port}/{path:path}")
    async def passthrough(tok: str, port: int, path: str, request: Request):
        """A page or dashboard running on this PC (e.g. http://localhost:8090), fetched here and handed on. Pages should use relative links."""
        import httpx
        from fastapi.responses import StreamingResponse
        if port == cfg["port"] or not 1 <= port <= 65535:
            raise HTTPException(400, "That port cannot be shown.")
        client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))
        fwd = {k: v for k, v in request.headers.items() if k.lower() in ("range", "if-range", "accept", "accept-language")}
        try:
            up = await client.send(client.build_request("GET", f"http://127.0.0.1:{port}/{path}", params=dict(request.query_params), headers=fwd), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            raise HTTPException(502, f"Nothing is answering on port {port} of the PC.")
        keep = {k: v for k, v in up.headers.items() if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges", "last-modified")}
        st = core.get_state(root)                        # a page may read (GET) only the port it was given with `avs view --api`, or its own server
        if port in (st.get("api") or []) or (st.get("kind") == "proxy" and st.get("port") == port):
            keep.update(CORS)

        if up.headers.get("content-type", "").startswith("text/html") and up.status_code == 200:      # make "/a/x" links point back through here
            import re
            raw = await up.aread()
            await up.aclose(); await client.aclose()
            pre = f"/p/{tok}/{port}/"
            text = re.sub(r'(\b(?:src|href|action|poster)=["\'])/(?!/)', lambda m: m.group(1) + pre, raw.decode("utf-8", "replace"))
            return Response(text.encode(), media_type="text/html; charset=utf-8", headers={k: v for k, v in keep.items() if k in CORS})

        async def body():
            try:
                async for chunk in up.aiter_raw():
                    yield chunk
            finally:
                await up.aclose()
                await client.aclose()
        return StreamingResponse(body(), status_code=up.status_code, headers=keep)

    return app


def main():
    import threading

    import uvicorn
    from . import relay
    cfg = core.load_config()
    if cfg.get("tunnel_only") and not cfg["relay"]:
        raise SystemExit('avs: "tunnel_only" needs "relay": true, or nothing could reach this PC.')

    def sweep():                                    # the config is re-read each time so a change applies at once
        n = 0
        while True:
            now = core.load_config(create=False)
            try:
                view.scrap_idle(core.root_of(cfg), now["view_keep_hours"])          # every 5 minutes: Markdown builds idle past view_keep_hours
                if n % 12 == 0:                                                     # hourly, opt-in: outbox files older than outbox_keep_days
                    core.clean_outbox(core.root_of(cfg), now["outbox_keep_days"])
            except OSError:
                pass
            n += 1
            time.sleep(300)
    threading.Thread(target=sweep, daemon=True).start()
    if cfg["relay"]:
        threading.Thread(target=relay.run_forever, args=(cfg, threading.Event()), daemon=True).start()
    uvicorn.run(make_app(cfg), host="127.0.0.1" if cfg.get("tunnel_only") else "0.0.0.0", port=cfg["port"], log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
