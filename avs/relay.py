"""Reach the PC from anywhere with nothing to set up: a Cloudflare quick tunnel (cloudflared, no account) gives a public https address
that changes each time it starts, and this posts the current address to a secret ntfy topic. The paired phone knows the topic (it is in
the pairing QR) and reads the newest message to find the PC again. The access token is still required for everything."""
import re
import shutil
import subprocess
import threading
import time
import urllib.request

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def publish(ntfy: str, topic: str, url: str, title: str = "AVS address", tags: str = "link", timeout: int = 15) -> bool:
    req = urllib.request.Request(f"{ntfy.rstrip('/')}/{topic}", data=url.encode(), method="POST",
                                 headers={"Title": title, "Cache": "yes", "Tags": tags})
    try:
        urllib.request.urlopen(req, timeout=timeout).read()
        return True
    except OSError:
        return False


def latest(ntfy: str, topic: str) -> str:
    """The newest address posted to the topic ('' if none): what the phone does."""
    import json
    try:
        with urllib.request.urlopen(f"{ntfy.rstrip('/')}/{topic}/json?poll=1&since=12h", timeout=15) as r:
            msgs = [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]
    except (OSError, ValueError):
        return ""
    found = [m["message"] for m in msgs if m.get("event") == "message" and URL_RE.fullmatch(m.get("message", ""))]
    return found[-1] if found else ""


def run_forever(cfg: dict, stop: threading.Event) -> None:
    """Keep a tunnel to the local server alive; post its address whenever it changes and every 30 minutes (ntfy keeps messages ~12 h)."""
    exe = shutil.which("cloudflared")
    if not exe:
        print("avs: cloudflared is not installed, so only the home network works.", flush=True)
        return
    while not stop.is_set():
        p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{cfg['port']}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        url, last = "", 0.0

        def pump():
            nonlocal url
            for line in p.stderr:
                m = URL_RE.search(line)
                if m:
                    url = m.group(0)
        threading.Thread(target=pump, daemon=True).start()
        while p.poll() is None and not stop.is_set():
            if url and time.time() - last > 1800:
                if publish(cfg["ntfy"], cfg["topic"], url):
                    last = time.time()
                else:
                    last = time.time() - 1700            # try again in ~100 s
            stop.wait(5)
        p.terminate()
        stop.wait(10)
