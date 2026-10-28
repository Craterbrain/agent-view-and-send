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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import core

CORS = {"Access-Control-Allow-Origin": "*"}

STATIC = Path(__file__).parent / "static"
COOKIE = "avs"


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
                             "background_color": "#101418", "theme_color": "#101418",
                             "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]},
                            media_type="application/manifest+json")

    @app.get("/static/{name}")
    def static(name: str):
        p = core.inside(STATIC, name)
        if not p or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/api/state")
    def state():
        st = core.get_state(root)
        view = dict(st)
        if st.get("kind") == "file":
            view["src"] = f"/v/{token}/{st['path']}"
        elif st.get("kind") == "proxy":
            view["src"] = f"/p/{token}/{st['port']}/{st.get('rest', '')}"
        return {"view": view, "outbox": core.listing(root, "outbox"), "inbox": core.listing(root, "inbox")}

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
        saved = []
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
        import shutil, subprocess
        if shutil.which("notify-send"):                              # a desktop note on the PC too
            subprocess.Popen(["notify-send", "-a", "AVS", "File from your phone", ", ".join(saved)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"saved": saved}

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
        return FileResponse(p, headers=CORS)             # a framed page has no origin of its own: its module scripts and fetches need this

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

    def sweep():                                    # opt-in: outbox_keep_days > 0 in the config; re-read each time so a change applies at once
        while True:
            days = core.load_config(create=False)["outbox_keep_days"]
            try:
                core.clean_outbox(core.root_of(cfg), days)
            except OSError:
                pass
            time.sleep(3600)
    threading.Thread(target=sweep, daemon=True).start()
    if cfg["relay"]:
        threading.Thread(target=relay.run_forever, args=(cfg, threading.Event()), daemon=True).start()
    uvicorn.run(make_app(cfg), host="127.0.0.1" if cfg.get("tunnel_only") else "0.0.0.0", port=cfg["port"], log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
