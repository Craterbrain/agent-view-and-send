"""Shared bits of AVS: config, the three folders, the "what is on screen" state, safe file names.

Everything is plain files under the AVS folder (default ~/AVS), so the agent-side command (`avs`) needs no network:
  outbox/  files the agent sends to the person        (the person downloads them)
  inbox/   files the person sends to the agent        (the agent reads them)
  view/    pages the agent shows in the View tab
  state.json  what the View tab shows right now
"""
import ipaddress
import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

CONFIG = Path(os.environ.get("AVS_CONFIG", Path.home() / ".config" / "avs" / "config.json"))
DEFAULTS = {
    "port": 8787,
    "root": str(Path.home() / "AVS"),
    "lan_host": "",                  # address the phone tries first on the home network; empty = this machine's LAN address
    "relay": True,                   # also reach the PC from anywhere: a Cloudflare quick tunnel (no account), found again through ntfy
    "ntfy": "https://ntfy.sh",       # where the tunnel address is posted (to a secret topic only the paired phone knows)
    # Who may connect directly: this machine and the home network. Tunnel traffic arrives from this machine, so the access token is the guard.
    "allow": ["127.0.0.0/8", "::1/128", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12", "fe80::/10", "fd00::/8"],
    "max_upload_mb": 2048,
    "outbox_keep_days": 0,           # 0 = never delete anything; N = the server removes outbox files older than N days (also `avs clean`)
}
BOXES = ("outbox", "inbox")


def load_config(create: bool = True) -> dict:
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    full = {**DEFAULTS, **cfg}
    if not (full.get("token") and full.get("topic")):
        full["token"] = full.get("token") or secrets.token_urlsafe(24)
        full["topic"] = full.get("topic") or "avs-" + secrets.token_urlsafe(18)
        if create:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps({**cfg, "token": full["token"], "topic": full["topic"]}, indent=1))
            CONFIG.chmod(0o600)
    return full


def root_of(cfg: dict) -> Path:
    r = Path(cfg["root"]).expanduser()
    for d in (*BOXES, "view"):
        (r / d).mkdir(parents=True, exist_ok=True)
    return r


def allowed(ip: str, cfg: dict) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
        if getattr(a, "ipv4_mapped", None):
            a = a.ipv4_mapped
        return any(a in ipaddress.ip_network(n, strict=False) for n in cfg["allow"])
    except ValueError:
        return False


def safe_name(name: str) -> str:
    """A plain file name: no folders, no leading dot, no odd characters."""
    n = os.path.basename((name or "").replace("\\", "/")).strip()
    n = re.sub(r"[^\w.\- ()\[\]+,&'’]", "_", n).lstrip(".").strip()
    return n[:180] or "file"


def unique(folder: Path, name: str) -> Path:
    """folder/name, or folder/name (2).ext if that exists. Nothing is ever overwritten."""
    p = folder / safe_name(name)
    stem, suf, i = p.stem, p.suffix, 2
    while p.exists():
        p = folder / f"{stem} ({i}){suf}"
        i += 1
    return p


def listing(root: Path, box: str) -> list[dict]:
    rows = []
    for f in (root / box).iterdir():
        if f.is_file() and not f.name.startswith("."):
            st = f.stat()
            created = getattr(st, "st_birthtime", None) or st.st_ctime
            rows.append({"name": f.name, "size": st.st_size, "time": st.st_mtime, "created": created})
    return sorted(rows, key=lambda r: -r["time"])


def inside(base: Path, rel: str) -> Path | None:
    """base/rel if it stays under base and exists, else None (blocks ../ tricks and symlinks that leave base)."""
    try:
        p = (base / rel).resolve()
        p.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return p if p.exists() else None


# ---- what the View tab shows ----------------------------------------------------------------------------------------------

def get_state(root: Path) -> dict:
    try:
        return json.loads((root / "state.json").read_text())
    except (OSError, ValueError):
        return {"kind": "none", "rev": 0}


def set_state(root: Path, **view) -> dict:
    st = {**view, "rev": time.time_ns(), "at": time.strftime("%H:%M:%S")}
    tmp = root / "state.json.tmp"
    tmp.write_text(json.dumps(st))
    tmp.replace(root / "state.json")
    return st


def show_url(root: Path, url: str, title: str = "") -> dict:
    if not re.match(r"^https?://", url):
        raise ValueError("A web address must start with http:// or https://")
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.hostname in ("localhost", "127.0.0.1", "::1"):        # a page served on this PC: AVS passes it through, so it works from anywhere
        port = u.port or (443 if u.scheme == "https" else 80)
        rest = (u.path.lstrip("/") + ("?" + u.query if u.query else ""))
        return set_state(root, kind="proxy", port=port, rest=rest, title=title or url)
    return set_state(root, kind="url", url=url, title=title or url)


def show_path(root: Path, src: Path, title: str = "") -> dict:
    """Copy a page (an .html file, an image, a PDF, text, or a folder with index.html) into view/ and show it."""
    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise ValueError(f"{src} does not exist.")
    slot = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    dest = root / "view" / slot
    if src.is_dir():
        if not (src / "index.html").is_file():
            raise ValueError("A folder needs an index.html in it.")
        shutil.copytree(src, dest)
        entry = "index.html"
    else:
        dest.mkdir(parents=True)
        shutil.copy2(src, dest / safe_name(src.name))
        entry = safe_name(src.name)
    return set_state(root, kind="file", path=f"{slot}/{entry}", title=title or src.name)


def show_html(root: Path, html: str, title: str = "") -> dict:
    slot = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    (root / "view" / slot).mkdir(parents=True)
    (root / "view" / slot / "index.html").write_text(html)
    return set_state(root, kind="file", path=f"{slot}/index.html", title=title or "Page from the agent")


def send_file(root: Path, src: Path, move: bool = False) -> Path:
    src = Path(src).expanduser().resolve()
    if not src.is_file():
        raise ValueError(f"{src} is not a file (zip a folder first).")
    dest = unique(root / "outbox", src.name)
    (shutil.move if move else shutil.copy2)(src, dest)
    return dest


def clean_outbox(root: Path, days: float, dry_run: bool = False) -> list[str]:
    """Removes outbox files last changed more than `days` days ago and returns their names. days <= 0 removes nothing."""
    if days <= 0:
        return []
    cutoff = time.time() - days * 86400
    gone = []
    for r in listing(root, "outbox"):
        if r["time"] < cutoff:
            if not dry_run:
                (root / "outbox" / r["name"]).unlink(missing_ok=True)
            gone.append(r["name"])
    return gone


# ---- pairing --------------------------------------------------------------------------------------------------------------

def lan_address() -> str:
    """This machine's address on the home network (the one the default route uses)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))                  # nothing is sent; this only picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def pairing_link(cfg: dict) -> str:
    """What the QR code holds. The Android app reads it once and keeps it."""
    from urllib.parse import urlencode
    lan = f"http://{cfg['lan_host'] or lan_address()}:{cfg['port']}"
    q = {"lan": lan, "token": cfg["token"]}
    if cfg["relay"]:
        q.update(topic=cfg["topic"], ntfy=cfg["ntfy"])
    return "avs://pair?" + urlencode(q)


def set_push_endpoint(url: str) -> None:
    """Remember where the phone's UnifiedPush distributor (the ntfy app) wants alerts sent; "" forgets it (alerts go back to the ntfy topic)."""
    try:
        cur = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cur = {}
    if url:
        cur["push_endpoint"] = url
    else:
        cur.pop("push_endpoint", None)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cur, indent=1))
    CONFIG.chmod(0o600)


def notify(cfg: dict, title: str, text: str) -> bool:
    """Tell the phone something happened (a file arrived, a page changed). Through UnifiedPush when the phone has given its push address (the ntfy app
    holds the connection, AVS holds none), else as a message on the secret ntfy topic that the app listens to. Never raises: a missing network only means no notification."""
    endpoint = cfg.get("push_endpoint")
    if endpoint:
        import urllib.request
        try:
            req = urllib.request.Request(endpoint, data=json.dumps({"title": title[:80], "text": text[:300]}).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "User-Agent": "avs"})
            with urllib.request.urlopen(req, timeout=6) as r:
                return 200 <= r.status < 300
        except Exception:
            return False
    if not cfg.get("relay"):
        return False
    from . import relay
    return relay.publish(cfg["ntfy"], cfg["topic"], text[:300], title=title[:80], tags="avs-event", timeout=6)


# ---- noticing uploads ---------------------------------------------------------------------------------------------------------

def unseen(root: Path) -> list[dict]:
    """Inbox files the agent has not been told about yet (oldest first)."""
    try:
        seen = set(json.loads((root / ".seen.json").read_text()))
    except (OSError, ValueError):
        seen = set()
    return sorted((r for r in listing(root, "inbox") if r["name"] not in seen), key=lambda r: r["time"])


def mark_seen(root: Path, names: list[str]) -> None:
    try:
        seen = set(json.loads((root / ".seen.json").read_text()))
    except (OSError, ValueError):
        seen = set()
    (root / ".seen.json").write_text(json.dumps(sorted(seen | set(names))))


def wait_for_upload(root: Path, timeout: float = 0, poll: float = 1.0) -> list[dict]:
    """Block until a new file is in the inbox (or the timeout passes; 0 = wait forever). Returns the new files and marks them seen."""
    end = time.time() + timeout if timeout else None
    while True:
        new = [r for r in unseen(root) if not r["name"].endswith(".part")]
        if new:
            time.sleep(0.5)                                   # let a file that was still being written finish
            new = unseen(root)
            mark_seen(root, [r["name"] for r in new])
            return new
        if end and time.time() > end:
            return []
        time.sleep(poll)


# ---- comments the person writes on pages ----------------------------------------------------------------------------------------
# comments.json: {page: {"title": ..., "src": the Markdown file it came from, "items": {item: {"text": ..., "time": ...}}}}. An item is "" for the
# comment on the whole page, or the name of a checklist item. Saving an empty text removes the comment.

_comment_lock = threading.Lock()


def comments_load(root: Path) -> dict:
    try:
        return json.loads((root / "comments.json").read_text())
    except (OSError, ValueError):
        return {}


def comment_set(root: Path, page: str, item: str, text: str, title: str = "", src: str = "") -> None:
    with _comment_lock:
        data = comments_load(root)
        pg = data.setdefault(page, {"title": title, "src": src, "items": {}})
        pg["title"], pg["src"] = title or pg.get("title", ""), src or pg.get("src", "")
        if text.strip():
            pg["items"][item] = {"text": text, "time": time.time()}
        else:
            pg["items"].pop(item, None)
        if not pg["items"]:
            data.pop(page)
        tmp = root / "comments.json.tmp"
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        tmp.replace(root / "comments.json")


def comments_for(root: Path, page: str) -> dict:
    return comments_load(root).get(page, {}).get("items", {})


def comment_rows(root: Path) -> list[dict]:
    """Every comment as a flat row, oldest first."""
    rows = [{"page": page, "title": pg.get("title", ""), "src": pg.get("src", ""), "item": item, "text": v["text"], "time": v["time"]}
            for page, pg in comments_load(root).items() for item, v in pg.get("items", {}).items()]
    return sorted(rows, key=lambda r: r["time"])


def _seen_comments(root: Path) -> dict:
    try:
        return json.loads((root / ".seen-comments.json").read_text())
    except (OSError, ValueError):
        return {}


def comments_unseen(root: Path) -> list[dict]:
    """Comments written or changed since the agent last looked."""
    seen = _seen_comments(root)
    return [r for r in comment_rows(root) if seen.get(r["page"] + "\x1f" + r["item"]) != r["time"]]


def comments_mark_seen(root: Path, rows: list[dict]) -> None:
    seen = _seen_comments(root)
    seen.update({r["page"] + "\x1f" + r["item"]: r["time"] for r in rows})
    (root / ".seen-comments.json").write_text(json.dumps(seen))


def wait_for_event(root: Path, timeout: float = 0, poll: float = 1.0) -> tuple[list[dict], list[dict]]:
    """Block until the person sends a file or writes a comment (or the timeout passes; 0 = wait forever). Returns (new files, new comments), both marked seen."""
    end = time.time() + timeout if timeout else None
    while True:
        if [r for r in unseen(root) if not r["name"].endswith(".part")] or comments_unseen(root):
            time.sleep(0.5)                                   # let a file that was still being written finish
            files, notes = unseen(root), comments_unseen(root)
            mark_seen(root, [r["name"] for r in files])
            comments_mark_seen(root, notes)
            return files, notes
        if end and time.time() > end:
            return [], []
        time.sleep(poll)
