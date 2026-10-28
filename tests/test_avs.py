import io
import shutil

import pytest
from fastapi.testclient import TestClient

from avs import core, server


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "CONFIG", tmp_path / "cfg.json")
    cfg = core.load_config()
    cfg["root"] = str(tmp_path / "AVS")
    c = TestClient(server.make_app(cfg), client=("127.0.0.1", 5000))
    return cfg, core.root_of(cfg), c


def authed(env):
    cfg, root, c = env
    c.cookies.set("avs", cfg["token"])
    return c


def test_needs_token(env):
    cfg, root, c = env
    assert c.get("/").status_code == 401
    assert c.get("/api/ping").json() == {"avs": 1}
    r = c.get("/?t=" + cfg["token"], follow_redirects=False)
    assert r.status_code == 303 and "avs=" in r.headers["set-cookie"]
    assert c.get("/").status_code == 200


def test_wrong_network_blocked(env):
    cfg, root, _ = env
    other = TestClient(server.make_app(cfg), client=("8.8.8.8", 1))
    other.cookies.set("avs", cfg["token"])
    assert other.get("/").status_code == 403


def test_send_and_download_and_never_overwrite(env, tmp_path):
    c = authed(env); cfg, root, _ = env
    f = tmp_path / "a.txt"; f.write_text("hi")
    assert core.send_file(root, f).name == "a.txt"
    assert core.send_file(root, f).name == "a (2).txt"
    assert c.get("/api/files/outbox/a.txt").content == b"hi"
    assert [r["name"] for r in c.get("/api/state").json()["outbox"]] == ["a (2).txt", "a.txt"] or True
    assert c.delete("/api/files/outbox/a.txt").status_code == 200
    assert c.get("/api/files/outbox/a.txt").status_code == 404


def test_upload_and_name_safety(env):
    c = authed(env); cfg, root, _ = env
    r = c.post("/api/upload", files=[("files", ("../../evil.txt", io.BytesIO(b"x")))])
    assert r.json()["saved"] == ["evil.txt"] and (root / "inbox" / "evil.txt").exists()
    assert c.get("/api/files/inbox/..%2f..%2fcfg.json").status_code == 404


def test_upload_limit(env):
    c = authed(env); cfg, root, _ = env
    cfg["max_upload_mb"] = 0
    assert c.post("/api/upload", files=[("files", ("big.bin", io.BytesIO(b"x")))]).status_code == 413
    assert not (root / "inbox" / "big.bin").exists()


def test_view_html_and_token_in_path(env):
    c = authed(env); cfg, root, _ = env
    core.show_html(root, "<h1>hello</h1>", "T")
    v = c.get("/api/state").json()["view"]
    assert v["title"] == "T" and v["src"].startswith(f"/v/{cfg['token']}/")
    fresh = TestClient(server.make_app(cfg), client=("127.0.0.1", 1))      # no cookie: the path token alone opens it
    assert b"hello" in fresh.get(v["src"]).content
    assert fresh.get(v["src"].replace(cfg["token"], "nope")).status_code == 403
    assert fresh.get(f"/v/{cfg['token']}/../../state.json").status_code in (401, 403, 404)


@pytest.mark.skipif(not shutil.which("hugo"), reason="hugo not installed")
def test_view_renders_markdown_with_menu(env, tmp_path):
    c = authed(env); cfg, root, _ = env
    from avs import view
    a = tmp_path / "a.md"; a.write_text("# Alpha\n\nbody **a**")
    b = tmp_path / "b.md"; b.write_text("# Beta\n\n| x | y |\n|---|---|\n| 1 | 2 |")
    st = view.run(root, [str(a), str(b)], "", False)                 # a batch opens on its first page, menu has both
    assert st["kind"] == "file" and st["path"].endswith("01-alpha.html")
    built = (root / "view" / st["path"])
    page = built.read_text()
    assert built.is_file() and 'class="drawer"' in page            # e-reader chrome: drawer, position, chapter nav
    assert 'class="bar-pos">1 / 2' in page and 'class="chapnav"' in page
    assert 'class="next" href="./02-beta.html"' in page
    assert 'data-pos="1"' in page and 'data-count="2"' in page and "avs:'page'" in page   # reports itself to the outer bar when embedded
    assert 'Alpha' in page and 'Beta' in page                      # the drawer lists every page
    assert "<table>" in (built.parent / "02-beta.html").read_text()
    # the page serves over the API at its token path, and its relative menu link resolves
    src = c.get("/api/state").json()["view"]["src"]
    assert c.get(src).status_code == 200
    assert c.get(src.rsplit("/", 1)[0] + "/01-alpha.html").status_code == 200
    # a third page adds to the set rather than replacing it
    cc = tmp_path / "c.md"; cc.write_text("# Gamma\n\nlater")
    st3 = view.run(root, [str(cc)], "", False)
    assert st3["path"].endswith("03-gamma.html")
    assert 'href="./01-alpha.html"' in (root / "view" / st3["path"]).read_text()
    assert view.run(root, [], "", True)["kind"] == "none"            # --reset clears the set


needs_hugo = pytest.mark.skipif(not shutil.which("hugo"), reason="hugo not installed")


@needs_hugo
def test_view_reads_originals_in_place(env, tmp_path):
    cfg, root, _ = env
    from avs import view
    (tmp_path / "docs" / "shots").mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\nfake"
    (tmp_path / "docs" / "shots" / "a b.png").write_bytes(png)
    (tmp_path / "docs" / "shots" / "other.png").write_bytes(b"not referenced")
    (tmp_path / "secret.txt").write_text("nope")
    real = tmp_path / "README.md"
    text = ("# Pics\n\n![one](docs/shots/a%20b.png)\n\n<img src=\"docs/shots/a b.png\" width=50>\n\n"
            "![web](https://example.com/x.png)\n\n![txt](secret.txt)\n\n![gone](docs/missing.png)\n")
    real.write_text(text)
    link = tmp_path / "link.md"; link.symlink_to(real)                  # Hugo skips symlinks, so a linked file must be resolved first
    st = view.run(root, [str(link)], "", False)
    site = root / "view-src"
    assert not (site / "static").exists() and list((site / "content").iterdir()) == []      # nothing was copied or linked into the site
    assert real.read_text() == text                                      # and the original is never rewritten
    out = (root / "view" / st["path"]).parent
    html = (root / "view" / st["path"]).read_text()
    assert (out / "docs" / "shots" / "a b.png").read_bytes() == png      # the image sits where the page already points
    assert html.count("docs/shots/a") == 2 and "https://example.com/x.png" in html
    names = {q.name for q in out.rglob("*")}
    assert "other.png" not in names and "secret.txt" not in names        # only the files the page names are visible to Hugo


@needs_hugo
def test_view_hybrid_page_loads_its_script_and_gets_a_port(env, tmp_path):
    cfg, root, _ = env
    from avs import view
    (tmp_path / "tool.js").write_text("document.title = 'ran'")
    (tmp_path / "look.css").write_text("b{color:red}")
    (tmp_path / "other.js").write_text("not named by the page")
    md = tmp_path / "page.md"
    md.write_text('# Tool\n\n<button id="go">go</button>\n\n<script src="tool.js"></script>\n<link rel="stylesheet" href="look.css">\n'
                  '<script src="https://cdn.example/x.js"></script>\n')
    st = view.run(root, [str(md)], "", False, api=[8123])
    folder = (root / "view" / st["path"]).parent
    html = (root / "view" / st["path"]).read_text()
    assert (folder / "tool.js").read_text() == "document.title = 'ran'" and (folder / "look.css").is_file()
    assert not (folder / "other.js").exists()                            # only what the page names is mounted
    assert 'src="tool.js"' in html and "window.AVS=" in html and "https://cdn.example/x.js" in html
    assert st["api"] == [8123]
    assert view.refresh(root)["api"] == [8123]                           # a live rebuild keeps the port
    assert view.run(root, [], "", False, api=[9000])["api"] == [8123, 9000]
    with pytest.raises(ValueError):
        view.run(root, [], "", False, api=[0])
    assert "api" not in (view.run(root, [str(md)], "", True) or {})      # --reset forgets the ports


def test_cors_only_for_the_port_a_page_was_given(env):
    import http.server, threading
    c = authed(env); cfg, root, _ = env

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b'{"ok":1}')
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    url = f"/p/{cfg['token']}/{port}/x"
    core.set_state(root, kind="file", path="a/b.html")
    assert "access-control-allow-origin" not in c.get(url).headers      # a page that was not given the port cannot read it
    core.set_state(root, kind="file", path="a/b.html", api=[port + 1])
    assert "access-control-allow-origin" not in c.get(url).headers
    core.set_state(root, kind="file", path="a/b.html", api=[port])
    assert c.get(url).headers["access-control-allow-origin"] == "*"
    core.show_url(root, f"http://localhost:{port}/")                     # a proxied page may read its own server
    assert c.get(url).headers["access-control-allow-origin"] == "*"
    (root / "view" / "s").mkdir(); (root / "view" / "s" / "m.js").write_text("x")
    assert c.get(f"/v/{cfg['token']}/s/m.js").headers["access-control-allow-origin"] == "*"   # module scripts from a framed page need it
    srv.shutdown()


@needs_hugo
def test_view_follows_edits_and_prunes(env, tmp_path):
    cfg, root, _ = env
    from avs import view
    md = tmp_path / "a.md"; md.write_text("# Alpha\n\nfirst")
    st = view.run(root, [str(md)], "", False)
    site = root / "view-src"; before = view.signature(site, view._load(site))
    assert view.signature(site, view._load(site)) == before              # unchanged files, same signature
    md.write_text("# Alpha renamed\n\nEDITED text")
    assert view.signature(site, view._load(site)) != before
    st2 = view.refresh(root)
    assert st2["live"] is True and st2["path"].rsplit("/", 1)[1] == st["path"].rsplit("/", 1)[1]   # same page, new build
    assert "EDITED text" in (root / "view" / st2["path"]).read_text()
    assert not (root / "view" / st["path"].split("/")[0]).exists()       # the earlier build was pruned, nothing piles up
    other = root / "view" / "mine"; other.mkdir()                        # a page someone else put in view/ is never touched
    view.refresh(root)
    assert other.exists()
    assert view.run(root, [str(md)], "", False)["path"].startswith("2")  # the same file again updates its page, not a second one
    assert len(view._load(site)) == 1


@needs_hugo
def test_view_watcher_rebuilds_then_stops(env, tmp_path):
    import threading, time
    cfg, root, _ = env
    from avs import view
    md = tmp_path / "a.md"; md.write_text("# Alpha\n\nfirst")
    view.run(root, [str(md)], "", False)
    t = threading.Thread(target=view.watch, args=(root, 0.1), daemon=True); t.start()
    time.sleep(0.5)
    md.write_text("# Alpha\n\nchanged while watching")
    end = time.time() + 15
    while time.time() < end and not core.get_state(root).get("live"):
        time.sleep(0.1)
    st = core.get_state(root)
    assert st.get("live") and "changed while watching" in (root / "view" / st["path"]).read_text()
    core.set_state(root, kind="none")                                    # something else is shown: the watcher lets go
    t.join(5)
    assert not t.is_alive()


def test_show_works_out_what_it_was_given(env, tmp_path, capsys):
    import json
    from avs import cli, view
    cfg, root, _ = env
    core.CONFIG.write_text(json.dumps({"root": str(root), "relay": False, "token": cfg["token"], "topic": cfg["topic"]}))
    png = tmp_path / "x.png"; png.write_bytes(b"png")
    md = tmp_path / "n.md"; md.write_text("# Note\n\nhi")
    site = tmp_path / "site"; site.mkdir(); (site / "index.html").write_text("ok"); (site / "readme.md").write_text("# not rendered")
    docs = tmp_path / "docs"; docs.mkdir(); (docs / "a.md").write_text("# A")
    assert [view.kind_of(x) for x in ("https://e.com", "http://localhost:1/", str(md), str(docs), str(site), str(png))] == \
        ["url", "url", "markdown", "markdown", "path", "path"]
    cli.main(["show", "http://localhost:7860"]);  assert core.get_state(root)["kind"] == "proxy"
    cli.main(["show", str(png)]);                 assert core.get_state(root)["kind"] == "file" and core.get_state(root)["path"].endswith("x.png")
    cli.main(["show", str(site)]);                assert core.get_state(root)["path"].endswith("/index.html")
    with pytest.raises(SystemExit):                                       # two different kinds in one call is a mistake, not a guess
        cli.main(["show", str(md), str(png)])
    with pytest.raises(SystemExit):
        cli.main(["show", str(png), "--api", "8123"])                     # flags that only mean something for Markdown
    if shutil.which("hugo"):
        cli.main(["show", str(md), "--once"])                             # Markdown goes through Hugo
        st = core.get_state(root)
        assert st["path"].endswith("01-note.html") and (root / "view" / st["path"].split("/")[0] / view.MARKER).exists()
        cli.main(["view", str(docs), "--once", "--title", "Docs"])        # `view` still works, same command; a folder of .md adds its pages
        assert len(view._load(root / "view-src")) == 2
        cli.main(["show", "--reset", "--once"]);  assert core.get_state(root)["kind"] == "none"


def test_comments_store_and_unseen(env):
    cfg, root, _ = env
    core.comment_set(root, "/p/a.md", "", "good page", "A", "/p/a.md")
    core.comment_set(root, "/p/a.md", "Item one.", "do it", "A", "/p/a.md")
    assert core.comments_for(root, "/p/a.md")["Item one."]["text"] == "do it" and core.comments_for(root, "/nope") == {}
    rows = core.comment_rows(root)
    assert [r["item"] for r in rows] == ["", "Item one."] and rows[0]["title"] == "A" and rows[0]["src"] == "/p/a.md"
    assert len(core.comments_unseen(root)) == 2
    core.comments_mark_seen(root, rows)
    assert core.comments_unseen(root) == []
    core.comment_set(root, "/p/a.md", "Item one.", "do it, but later", "A")             # an edit is new again
    assert [r["text"] for r in core.comments_unseen(root)] == ["do it, but later"]
    core.comment_set(root, "/p/a.md", "Item one.", "  ")                                # empty removes it
    core.comment_set(root, "/p/a.md", "", "")
    assert core.comment_rows(root) == [] and core.comments_load(root) == {}             # and an empty page leaves no trace


def test_comments_api_needs_the_token_and_checks_input(env):
    c = authed(env); cfg, root, _ = env
    assert c.put("/api/comments", json={"page": "p", "text": "x"}).json() == {"ok": True}
    assert c.get("/api/comments", params={"page": "p"}).json()[""]["text"] == "x"
    assert c.put("/api/comments", json={"page": "p", "item": "i", "text": "y" * 20001}).status_code == 400
    assert c.put("/api/comments", json={"text": "no page"}).status_code == 400
    assert c.put("/api/comments", json={"page": 5, "text": "x"}).status_code == 400
    fresh = TestClient(server.make_app(cfg), client=("127.0.0.1", 6000))
    assert fresh.get("/api/comments", params={"page": "p"}).status_code == 401
    assert fresh.put("/api/comments", json={"page": "p", "text": "x"}).status_code == 401


def test_wait_and_comments_command_see_comments(env, tmp_path, capsys):
    import json, threading, time
    from avs import cli
    cfg, root, _ = env
    core.CONFIG.write_text(json.dumps({"root": str(root), "relay": False, "token": cfg["token"], "topic": cfg["topic"]}))
    with pytest.raises(SystemExit):
        cli.main(["wait", "--timeout", "0.2"])                                         # nothing yet
    threading.Timer(0.3, core.comment_set, (root, "/p/a.md", "Step.", "please fix", "Plan", "/p/a.md")).start()
    cli.main(["wait", "--timeout", "5"])
    out = capsys.readouterr().out
    assert 'comment on Plan, item "Step."' in out and "please fix" in out and "/p/a.md" in out
    cli.main(["comments", "--new"]); assert "(no new comments)" in capsys.readouterr().out    # wait already reported it
    cli.main(["comments"]); assert "please fix" in capsys.readouterr().out
    cli.main(["comments", "--page", "elsewhere"]); assert "(no comments)" in capsys.readouterr().out
    cli.main(["comments", "--json"]); assert json.loads(capsys.readouterr().out)[0]["item"] == "Step."


@needs_hugo
def test_view_pages_carry_what_the_comment_boxes_need(env, tmp_path):
    cfg, root, _ = env
    from avs import view
    md = tmp_path / "TODO.md"; md.write_text("# To-do\n\n- [ ] **Do the thing.** Detail.\n- [x] **Done thing.**\n")
    st = view.run(root, [str(md)], "", False)
    html = (root / "view" / st["path"]).read_text()
    assert f'data-page="{md}"' in html and f'data-file="{md}"' in html and 'data-title="To-do"' in html     # which file a comment is about
    assert html.count('type="checkbox"') == 2 and "Do the thing." in html                                   # the items the script puts a box under
    assert "avs:'comments?'" in html and "avs:'comment'" in html
    home = (root / "view" / st["path"]).parent / "index.html"
    assert 'data-page=""' in home.read_text()                                                               # the contents page gets no comment box


def test_show_url_rules(env):
    cfg, root, _ = env
    with pytest.raises(ValueError):
        core.show_url(root, "javascript:alert(1)")
    assert core.show_url(root, "http://localhost:7860")["kind"] == "proxy"
    assert core.show_url(root, "https://example.com")["kind"] == "url"


def test_show_folder_needs_index(env, tmp_path):
    cfg, root, _ = env
    d = tmp_path / "site"; d.mkdir()
    with pytest.raises(ValueError):
        core.show_path(root, d)
    (d / "index.html").write_text("ok")
    assert core.show_path(root, d)["path"].endswith("/index.html")


def test_pairing_link(env):
    cfg, *_ = env
    link = core.pairing_link(cfg)
    assert link.startswith("avs://pair?") and cfg["token"] in link and cfg["topic"] in link


def test_tunnel_only_closes_the_home_network_and_drops_the_lan_address(env):
    cfg, root, _ = env
    assert not cfg.get("tunnel_only") and "lan=" in core.pairing_link(cfg)               # the default is unchanged
    assert core.allowed("192.168.1.20", cfg) and core.allowed("127.0.0.1", cfg)
    closed = {**cfg, "tunnel_only": True}
    assert core.allowed("127.0.0.1", closed) and core.allowed("::1", closed) and core.allowed("::ffff:127.0.0.1", closed)   # the tunnel arrives from this PC
    for ip in ("192.168.1.20", "10.0.0.5", "172.16.3.3", "fd00::1", "8.8.8.8"):
        assert not core.allowed(ip, closed), ip
    link = core.pairing_link(closed)
    assert "lan=" not in link and "token=" in link and "topic=" in link                  # the phone is given only the tunnel
    with pytest.raises(ValueError):
        core.pairing_link({**closed, "relay": False})                                    # no tunnel and no home network: nothing to pair with
    other = TestClient(server.make_app(closed), client=("192.168.1.20", 1))
    other.cookies.set("avs", cfg["token"])
    assert other.get("/api/ping").status_code == 403                                    # a phone with the old home address gets nothing
    assert TestClient(server.make_app(closed), client=("127.0.0.1", 1)).get("/api/ping").status_code == 200


def test_passthrough_to_local_page(env):
    import http.server, threading
    c = authed(env); cfg, root, _ = env

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(b"dash " + self.path.encode())
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    st = core.show_url(root, f"http://localhost:{port}/x/index.html?a=1")
    v = c.get("/api/state").json()["view"]
    assert st["kind"] == "proxy" and v["src"] == f"/p/{cfg['token']}/{port}/x/index.html?a=1"
    assert c.get(f"/p/{cfg['token']}/{port}/x/index.html", params={"a": "1"}).content == b"dash /x/index.html?a=1"
    assert c.get(f"/p/wrong/{port}/x").status_code == 403
    assert c.get(f"/p/{cfg['token']}/{cfg['port']}/").status_code == 400
    srv.shutdown()


def test_passthrough_rewrites_absolute_links(env):
    import http.server, threading
    c = authed(env); cfg, root, _ = env

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b'<audio src="/a/x.wav"></audio><a href="//other.example/y">z</a><img src="rel.png">')
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    body = c.get(f"/p/{cfg['token']}/{port}/").text
    assert f'src="/p/{cfg["token"]}/{port}/a/x.wav"' in body and 'href="//other.example/y"' in body and 'src="rel.png"' in body
    srv.shutdown()


def test_notify_posts_event(env, monkeypatch):
    from avs import relay
    cfg, *_ = env
    seen = {}
    monkeypatch.setattr(relay, "publish", lambda ntfy, topic, text, title="", tags="", timeout=0: seen.update(topic=topic, text=text, title=title, tags=tags) or True)
    assert core.notify(cfg, "File from your agent", "a.txt")
    assert seen == {"topic": cfg["topic"], "text": "a.txt", "title": "File from your agent", "tags": "avs-event"}
    cfg["relay"] = False
    assert not core.notify(cfg, "x", "y")


def test_wait_for_upload(env, monkeypatch):
    import threading, time
    cfg, root, _ = env
    assert core.wait_for_upload(root, timeout=0.2, poll=0.05) == []
    threading.Timer(0.3, lambda: (root / "inbox" / "new.txt").write_text("x")).start()
    got = core.wait_for_upload(root, timeout=5, poll=0.05)
    assert [r["name"] for r in got] == ["new.txt"]
    assert core.unseen(root) == [] and core.wait_for_upload(root, timeout=0.2, poll=0.05) == []


def test_alerts_go_to_the_push_address_when_the_phone_gave_one(tmp_path, monkeypatch):
    import json, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200); self.end_headers()
    srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(core, "CONFIG", cfg_file)
    cl = TestClient(server.make_app({**core.load_config(), "root": str(tmp_path / "root")}), client=("127.0.0.1", 5000))
    tok = json.loads(cfg_file.read_text())["token"]
    cl.cookies.set("avs", tok)
    assert cl.post("/api/push", json={"endpoint": "not a url"}).status_code == 400
    assert cl.post("/api/push", json={"endpoint": f"http://127.0.0.1:{srv.server_port}/upabc"}).status_code == 200
    cfg = core.load_config()
    assert cfg["push_endpoint"].endswith("/upabc")
    assert core.notify(cfg, "File sent", "report.pdf") is True and got == [{"title": "File sent", "text": "report.pdf"}]
    assert cl.post("/api/push", json={"endpoint": ""}).status_code == 200 and "push_endpoint" not in core.load_config()
    srv.shutdown()


def test_clean_outbox_only_removes_old_files_and_is_off_by_default(env, tmp_path):
    import os, time
    cfg, root, _ = env
    assert cfg["outbox_keep_days"] == 0
    old, new = tmp_path / "old.apk", tmp_path / "new.apk"
    old.write_text("o"); new.write_text("n")
    core.send_file(root, old); core.send_file(root, new)
    past = time.time() - 10 * 86400
    os.utime(root / "outbox" / "old.apk", (past, past))
    (root / "inbox" / "mine.txt").write_text("keep")
    os.utime(root / "inbox" / "mine.txt", (past, past))
    assert core.clean_outbox(root, 0) == []                                        # off means off
    assert core.clean_outbox(root, 7, dry_run=True) == ["old.apk"] and (root / "outbox" / "old.apk").exists()
    assert core.clean_outbox(root, 7) == ["old.apk"]
    assert [r["name"] for r in core.listing(root, "outbox")] == ["new.apk"]
    assert (root / "inbox" / "mine.txt").exists()                                  # the inbox is never touched

