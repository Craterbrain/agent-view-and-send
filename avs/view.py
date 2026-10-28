"""`avs show` of Markdown: turn Markdown into a small, nice-looking site and show it in the View tab.

The agent writes Markdown anyway, so this renders it with Hugo (one static-site binary, builds in a blink, no
node_modules) instead of hand-writing HTML pages. Pages accumulate: `avs show a.md` then `avs show b.md` leaves both
up, behind a Contents drawer; `avs show --reset` starts a fresh set.

Nothing is copied or edited. Hugo mounts each Markdown file, and only the image files that page refers to, straight from
where they live (`files = [...]` in its config makes it see exactly those and nothing else in the folder); the title,
order and address of each page come from the site config, not from the file. A small watcher then rebuilds when a
source file changes, so the phone follows edits as they are made, and the page keeps its scroll position. It stops
by itself as soon as something else is shown. `--once` skips the watcher.

This is only for *showing* Markdown. A page that does something (buttons that run a command, live-updating data) still
needs a real server you point `avs show http://localhost:PORT` at, or a hybrid page (`--api PORT`) -- a static build can't call back to the PC.
"""
import fcntl
import json
import os
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from . import core

# A self-contained Hugo site: project-level layouts (no theme), everything inlined so a built page needs no extra files.
CONFIG = """baseURL = '/'
title = 'AVS'
languageCode = 'en'
relativeURLs = true
uglyURLs = true
disableKinds = ['taxonomy', 'term', 'RSS', 'sitemap', 'robotsTXT', '404', 'section']
[markup.goldmark.renderer]
  unsafe = true
[markup.highlight]
  codeFences = false
"""

CSS = """:root{color-scheme:light dark;
 --bg:#faf7f0;--fg:#2b2620;--mut:#7c7468;--line:#e7e0d2;--card:#f0e9da;--acc:#9a5a30;--code:#efe8d8;--bar:rgba(250,247,240,.9)}
@media(prefers-color-scheme:dark){:root{
 --bg:#15151a;--fg:#dad6cd;--mut:#8e897f;--line:#2c2c33;--card:#20202a;--acc:#d79b6e;--code:#1e1e28;--bar:rgba(21,21,26,.9)}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font:18px/1.72 Georgia,"Iowan Old Style",Palatino,"Times New Roman",serif;overflow-wrap:break-word}
.sans{font-family:-apple-system,system-ui,"Segoe UI",sans-serif}
.embedded .bar{display:none}
.progress{position:fixed;top:0;left:0;height:3px;width:0;background:var(--acc);z-index:30}
@supports (animation-timeline:scroll()){.progress{animation:grow linear;animation-timeline:scroll(root block)}@keyframes grow{from{width:0}to{width:100%}}}
.bar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:10px;height:50px;padding:0 12px;background:var(--bar);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.bar-title{flex:1;min-width:0;font:600 15px/1.2 -apple-system,system-ui,sans-serif;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-pos{font:13px/1 -apple-system,system-ui,sans-serif;color:var(--mut);white-space:nowrap;min-width:34px;text-align:right}
.burger{display:flex;flex-direction:column;justify-content:center;gap:4px;width:34px;height:34px;padding:8px;border-radius:8px;cursor:pointer;flex:0 0 auto}
.burger span{display:block;height:2px;background:var(--fg);border-radius:2px}.burger:active{background:var(--card)}
.nav-toggle{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}
.scrim{position:fixed;inset:0;z-index:40;background:rgba(0,0,0,.45);opacity:0;visibility:hidden;transition:opacity .22s}
.drawer{position:fixed;top:0;left:0;z-index:41;display:flex;flex-direction:column;width:min(82vw,330px);height:100%;background:var(--bg);border-right:1px solid var(--line);box-shadow:0 0 40px rgba(0,0,0,.2);transform:translateX(-100%);transition:transform .24s cubic-bezier(.4,0,.2,1)}
.nav-toggle:checked~.scrim{opacity:1;visibility:visible}
.nav-toggle:checked~.drawer{transform:none}
.drawer-head{padding:18px 18px 10px;font:600 13px/1 -apple-system,system-ui,sans-serif;letter-spacing:.09em;text-transform:uppercase;color:var(--mut)}
.drawer nav{overflow-y:auto;padding:0 10px 22px}
.drawer nav a{display:block;padding:11px 12px;border-radius:9px;color:var(--fg);text-decoration:none;font:15px/1.35 -apple-system,system-ui,sans-serif}
.drawer nav a.on{background:var(--acc);color:#fff}.drawer nav a:not(.on):active{background:var(--card)}
main{max-width:34rem;margin:0 auto;padding:30px 22px 10px}main>:first-child{margin-top:0}
h1,h2,h3,h4{line-height:1.3;margin:1.7em 0 .5em;font-family:Georgia,Palatino,serif}h1{font-size:1.7em;margin-top:.15em}h2{font-size:1.36em}h3{font-size:1.12em}
a{color:var(--acc)}
p,ul,ol,blockquote,table,pre{margin:0 0 1.1em}ul,ol{padding-left:1.3em}li{margin:.3em 0}
blockquote{margin-left:0;padding:.2em 1.1em;border-left:3px solid var(--acc);color:var(--mut);font-style:italic}
code{font:.84em ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);padding:.12em .36em;border-radius:5px}
pre{background:var(--code);padding:14px 16px;border-radius:10px;overflow-x:auto}pre code{background:none;padding:0;line-height:1.5}
table{border-collapse:collapse;width:100%;font:.92em/1.5 -apple-system,system-ui,sans-serif;display:block;overflow-x:auto}th,td{border:1px solid var(--line);padding:7px 11px;text-align:left}th{background:var(--card)}
img{max-width:100%;height:auto;border-radius:8px}hr{border:0;border-top:1px solid var(--line);margin:2em 0}
.cbox{display:none;margin:.5em 0 0;font-family:-apple-system,system-ui,sans-serif}.cbox-on .cbox{display:block}
.cbox textarea{display:block;width:100%;resize:vertical;border:1px solid var(--line);background:var(--bg);color:var(--fg);border-radius:9px;padding:7px 10px;font:14px/1.4 -apple-system,system-ui,sans-serif}
.cbox textarea:focus{outline:2px solid var(--acc);outline-offset:-1px}.cbox button{margin-top:6px;border:0;border-radius:8px;background:var(--acc);color:#fff;padding:7px 14px;font:600 13px -apple-system,system-ui,sans-serif}
.cbox button:disabled{opacity:.5}.cbox small{margin-left:8px;color:var(--mut);font-size:12px}.cbox.has textarea{border-color:var(--acc)}
.cbox-page{max-width:34rem;margin:1.6em auto 0;padding:0 22px}.cbox-page .cbox{margin:0}.cbox-page h4{margin:0 0 .4em;font:600 12px -apple-system,system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--mut)}
.chapnav{display:flex;gap:12px;max-width:34rem;margin:0 auto;padding:12px 22px 60px}
.chapnav a{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;text-decoration:none;color:var(--fg);font-family:-apple-system,system-ui,sans-serif}
.chapnav a:active{background:var(--card)}.chapnav a span{font-size:12px;color:var(--mut)}
.chapnav a b{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chapnav a.next{text-align:right;align-items:flex-end}.chapnav .spacer{flex:1}
ul.toc{list-style:none;padding:0;font-family:-apple-system,system-ui,sans-serif}ul.toc li{margin:.55em 0}ul.toc a{font-weight:600}
"""

COMMENT_JS = r"""var cp=d.page,cs={},boxes=[];
function norm(s){return String(s).replace(/`/g,'').replace(/\s+/g,' ').trim()}
function mkbox(item,ph){var w=document.createElement('div'),t=document.createElement('textarea'),b=document.createElement('button'),s=document.createElement('small');
w.className='cbox';t.rows=1;t.placeholder=ph;b.type='button';b.textContent='Save comment';b.hidden=true;w.appendChild(t);w.appendChild(b);w.appendChild(s);
var r={item:item,t:t,b:b,s:s,w:w};boxes.push(r);
t.addEventListener('focus',function(){if(t.rows<3)t.rows=3});
t.addEventListener('input',function(){b.hidden=t.value===((cs[item]||{}).text||'');s.textContent=''});
b.addEventListener('click',function(){b.disabled=true;s.textContent='Saving...';parent.postMessage({avs:'comment',page:cp,src:d.file,title:d.title,item:item,text:t.value},'*')});return r}
function itemKey(li){var e=li.querySelector(':scope > strong, :scope > p > strong');if(e)return norm(e.textContent);
var c=li.cloneNode(true);c.querySelectorAll('ul,ol').forEach(function(x){x.remove()});return norm(c.textContent).slice(0,80)}
function fill(){boxes.forEach(function(r){var v=cs[r.item];r.t.value=v?v.text:'';r.w.classList.toggle('has',!!v);r.b.hidden=true})}
if(cp){
document.querySelectorAll('main li > input[type=checkbox], main li > p > input[type=checkbox]').forEach(function(cb){var li=cb.closest('li'),k=itemKey(li);if(!k)return;
var r=mkbox(k,'Comment on this item'),nested=li.querySelector(':scope > ul, :scope > ol');if(nested)li.insertBefore(r.w,nested);else li.appendChild(r.w)});
var pg=document.createElement('section'),h=document.createElement('h4'),pr=mkbox('','Comment on this page');pg.className='cbox-page';h.textContent='Comment on this page';pg.appendChild(h);pg.appendChild(pr.w);
var mn=document.querySelector('main');mn.parentNode.insertBefore(pg,mn.nextSibling);
addEventListener('message',function(e){var m=e.data;if(e.source!==parent||!m)return;
if(m.avs==='comments'){cs=m.data||{};fill();document.documentElement.classList.add('cbox-on')}
if(m.avs==='commented'){var r=boxes.filter(function(x){return x.item===m.item})[0];if(!r)return;r.b.disabled=false;
if(m.ok){if(r.t.value.trim())cs[m.item]={text:r.t.value};else delete cs[m.item];r.b.hidden=true;r.w.classList.toggle('has',!!cs[m.item]);r.s.textContent=cs[m.item]?'Saved':'Removed'}else r.s.textContent='Could not save: '+(m.error||'try again')}});
parent.postMessage({avs:'comments?',page:cp},'*')}
"""

# baseof carries the e-reader chrome: progress line, top bar with hamburger + position, a pure-CSS Contents drawer,
# and prev/next chapter links. $idx is the current page's place in the by-weight order (-1 on the cover/home page).
BASEOF = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ with .Title }}{{ . }}{{ else }}{{ .Site.Title }}{{ end }}</title>
<style>__CSS__</style>
<script>window.AVS={url:function(port,path){var m=/^[/]v[/]([^/]+)[/]/.exec(location.pathname),p=String(path||"").replace(/^[/]/,"");return(m?"/p/"+m[1]+"/"+port+"/":"http://127.0.0.1:"+port+"/")+p},get:function(port,path){return fetch(AVS.url(port,path)).then(function(r){if(!r.ok)throw new Error(r.status+" "+r.statusText);return(r.headers.get("content-type")||"").indexOf("json")>=0?r.json():r.text()})}};</script>
</head>
{{ $all := .Site.RegularPages.ByWeight }}
{{ $cur := .RelPermalink }}
{{ $idx := -1 }}
{{ range $i, $p := $all }}{{ if eq $p.RelPermalink $cur }}{{ $idx = $i }}{{ end }}{{ end }}
{{ $many := gt (len $all) 1 }}
<body data-page="{{ if .IsHome }}{{ else }}{{ with .Params.src }}{{ . }}{{ else }}view:{{ .Title }}{{ end }}{{ end }}" data-file="{{ .Params.src }}" data-title="{{ .Title }}" data-pos="{{ if ge $idx 0 }}{{ add $idx 1 }}{{ end }}" data-count="{{ len $all }}" data-many="{{ if $many }}1{{ end }}">
<div class="progress"></div>
{{ if $many }}<input type="checkbox" id="nav-toggle" class="nav-toggle" aria-hidden="true">{{ end }}
<header class="bar">
  {{ if $many }}<label class="burger" for="nav-toggle" aria-label="Contents"><span></span><span></span><span></span></label>{{ end }}
  <div class="bar-title">{{ with .Title }}{{ . }}{{ else }}{{ .Site.Title }}{{ end }}</div>
  <div class="bar-pos">{{ if ge $idx 0 }}{{ add $idx 1 }} / {{ len $all }}{{ end }}</div>
</header>
{{ if $many }}
<label class="scrim" for="nav-toggle" aria-hidden="true"></label>
<aside class="drawer">
  <div class="drawer-head">Contents</div>
  <nav>{{ range $all }}<a href="{{ .RelPermalink }}"{{ if eq .RelPermalink $cur }} class="on"{{ end }}>{{ .Title }}</a>{{ end }}</nav>
</aside>
{{ end }}
<main>{{ block "main" . }}{{ .Content }}{{ end }}</main>
{{ if and $many (ge $idx 0) }}
<footer class="chapnav">
  {{ if gt $idx 0 }}{{ $p := index $all (sub $idx 1) }}<a class="prev" href="{{ $p.RelPermalink }}"><span>← Previous</span><b>{{ $p.Title }}</b></a>{{ else }}<span class="spacer"></span>{{ end }}
  {{ if lt (add $idx 1) (len $all) }}{{ $n := index $all (add $idx 1) }}<a class="next" href="{{ $n.RelPermalink }}"><span>Next →</span><b>{{ $n.Title }}</b></a>{{ else }}<span class="spacer"></span>{{ end }}
</footer>
{{ end }}
<script>
(function(){if(parent===window)return;document.documentElement.classList.add('embedded');
var d=document.body.dataset,f=location.pathname.split('/').pop(),st;
parent.postMessage({avs:'page',file:f,title:d.title,pos:d.pos,count:d.count,nav:d.many==='1'},'*');
var m=/avsy=(\\d+)/.exec(location.hash);if(m)addEventListener('load',function(){scrollTo({top:+m[1],behavior:'instant'})});
__COMMENTS__
addEventListener('scroll',function(){clearTimeout(st);st=setTimeout(function(){parent.postMessage({avs:'scroll',file:f,y:Math.round(scrollY)},'*')},150)},{passive:true});
addEventListener('message',function(e){if(e.data&&e.data.avs==='toggle'){var t=document.getElementById('nav-toggle');if(t)t.checked=!t.checked}});
})();
</script>
</body>
</html>
""".replace("__CSS__", CSS).replace("__COMMENTS__", COMMENT_JS)

SINGLE = '{{ define "main" }}{{ .Content }}{{ end }}\n'
INDEX = (
    '{{ define "main" }}\n<h1>{{ .Site.Title }}</h1>\n{{ .Content }}\n'
    '<ul class="toc">{{ range .Site.RegularPages.ByWeight }}<li><a href="{{ .RelPermalink }}">{{ .Title }}</a></li>{{ end }}</ul>\n'
    '{{ end }}\n'
)


MARKDOWN_EXT = (".md", ".markdown")


def kind_of(target: str) -> str:
    """What `avs show` should do with one target: "url" (a web address), "markdown" (a Markdown file, or a folder of them with no index.html
    of its own: rendered here) or "path" (any other file, or a folder with an index.html: shown as it is)."""
    if target.startswith(("http://", "https://")):
        return "url"
    p = Path(target).expanduser()
    if p.suffix.lower() in MARKDOWN_EXT:
        return "markdown"
    if p.is_dir() and not (p / "index.html").is_file() and any(p.glob("*.md")):
        return "markdown"
    return "path"


def _piped_stdin() -> str | None:
    """Markdown piped on stdin, or None. Never blocks: only reads when data is actually waiting on a real pipe."""
    import select
    import sys
    try:
        if sys.stdin.isatty() or not select.select([sys.stdin], [], [], 0.0)[0]:
            return None
    except (OSError, ValueError):                    # no real fileno (e.g. under pytest): treat as no input
        return None
    body = sys.stdin.read()
    return body if body.strip() else None


def _hugo() -> str:
    exe = shutil.which("hugo")
    if not exe:
        raise ValueError("Hugo is not installed (sudo pacman -S hugo).")
    return exe


def site_dir(root: Path) -> Path:
    return root / "view-src"


MARKER = ".avs-view"                                # inside a built page folder: `avs view` made it, so it may replace or prune it
ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".bmp", ".ico", ".js", ".mjs", ".css", ".json"}
_REFS = [re.compile(r'!\[[^\]]*\]\((<[^>]+>|[^)\s]+)(?:\s+"[^"]*")?\)'),            # a Markdown image
         re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', re.I),                            # raw HTML in the page: an image, a script, a stylesheet
         re.compile(r'<script\b[^>]*?\bsrc="([^"]+)"', re.I),
         re.compile(r'<link\b[^>]*?\bhref="([^"]+)"', re.I)]


def ensure_scaffold(site: Path) -> None:
    (site / "content").mkdir(parents=True, exist_ok=True)
    d = site / "layouts" / "_default"
    d.mkdir(parents=True, exist_ok=True)
    (d / "baseof.html").write_text(BASEOF)           # rewritten each time so template tweaks always take effect
    (d / "single.html").write_text(SINGLE)
    (site / "layouts" / "index.html").write_text(INDEX)


def _load(site: Path) -> list[dict]:
    try:
        return json.loads((site / "pages.json").read_text())
    except (OSError, ValueError):
        return []


def _save(site: Path, pages: list[dict]) -> None:
    (site / "pages.json").write_text(json.dumps(pages, indent=1))


def _settings(site: Path) -> dict:
    try:
        return json.loads((site / "settings.json").read_text())
    except (OSError, ValueError):
        return {}


def _slug(title: str) -> str:
    s = re.sub(r"[^\w]+", "-", title.lower()).strip("-")
    return s[:40] or "page"


def _title_of(body: str, fallback: str) -> str:
    for line in body.splitlines():
        m = re.match(r"#\s+(.+)", line.strip())
        if m:
            return m.group(1).strip()
        if line.strip():
            break
    return fallback


def _source(site: Path, pg: dict) -> Path:
    return Path(pg["src"]) if pg.get("src") else site / "content" / pg["name"] / "page.md"


def _read(p: Path) -> str:
    try:
        return p.read_text()
    except (OSError, UnicodeDecodeError):
        return ""


def _page_title(site: Path, pg: dict) -> str:
    return pg.get("title") or _title_of(_read(_source(site, pg)), pg.get("stem") or "Page")


def _asset_mounts(pg: dict, text: str) -> list[tuple[str, str, str]]:
    """(folder, where it goes under static/, file name) for each local image, script or stylesheet file the page refers to. A page's address
    is its site root, so a file lands at the same relative path the page already uses and the Markdown needs no change. Web addresses,
    data: images, absolute paths and anything that is not one of those file types are left out (a script's own imports are not followed)."""
    base = os.path.dirname(pg["src"]) if pg.get("src") else (pg.get("cwd") or ".")
    out = []
    for ref in (r for rx in _REFS for r in rx.findall(text)):
        raw = unquote(ref.strip("<>")).split("#")[0].split("?")[0]
        if not raw or raw.startswith("/") or re.match(r"^[a-z][a-z0-9+.-]*:", raw, re.I) or Path(raw).suffix.lower() not in ASSET_EXT:
            continue
        path = os.path.normpath(os.path.join(base, raw))
        real = os.path.realpath(path)
        if os.path.basename(real) != os.path.basename(path) or not os.path.isfile(real):    # Hugo does not follow a link to a file
            continue
        out.append((os.path.dirname(real), posixpath.normpath("/" + posixpath.dirname(raw)).lstrip("/"), os.path.basename(real)))
    return out


def _glob(name: str) -> str:
    return re.sub(r"([*?\[\]{}\\])", r"\\\1", name)


def write_config(site: Path, pages: list[dict]) -> None:
    """hugo.toml: each page's title, order and address, plus the mounts that let Hugo read the originals in place."""
    s = lambda x: json.dumps(x, ensure_ascii=False)          # a JSON string is a valid TOML string
    cascade, mounts, seen = [], [f'[[module.mounts]]\n  source = {s(str(site / "content"))}\n  target = "content"\n'], {}
    for i, pg in enumerate(pages, 1):
        src = _source(site, pg)
        if not src.is_file():                                # gone for the moment (an editor saving), or deleted: leave it out of this build
            continue
        cascade.append(f'[[cascade]]\n  title = {s(_page_title(site, pg))}\n  weight = {i}\n  url = {s("/" + pg["name"] + ".html")}\n'
                       f'  [cascade.params]\n    src = {s(pg.get("src") or "")}\n'
                       f'  [cascade.target]\n    path = {s("/" + pg["name"] + "/**")}\n')
        if pg.get("src"):
            real = src.resolve()
            mounts.append(f'[[module.mounts]]\n  source = {s(str(real.parent))}\n  target = {s("content/" + pg["name"])}\n  files = [{s(_glob(real.name))}]\n')
        for folder, target, name in _asset_mounts(pg, _read(src)):
            if seen.setdefault((target, name), folder) == folder:        # two pages naming different files the same way: the first wins
                mounts.append(f'[[module.mounts]]\n  source = {s(folder)}\n  target = {s(("static/" + target).rstrip("/"))}\n  files = [{s(_glob(name))}]\n')
    (site / "hugo.toml").write_text(CONFIG + "\n" + "\n".join(cascade) + "\n" + "\n".join(dict.fromkeys(mounts)))


def prune(root: Path, keep: str) -> None:
    """Remove the earlier pages `avs view` built (only those it marked), so one set never piles up copies."""
    for d in (root / "view").iterdir():
        if d.name != keep and d.is_dir() and (d / MARKER).exists():
            shutil.rmtree(d, ignore_errors=True)


def build(root: Path, site: Path, entry: str, title: str, live: bool = False) -> dict:
    slot = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    dest = root / "view" / slot
    r = subprocess.run([_hugo(), "--quiet", "-s", str(site), "-d", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError("Hugo could not build the page:\n" + (r.stderr or r.stdout).strip())
    (dest / MARKER).write_text("")
    if not (dest / entry).is_file():                 # a page we meant to open did not appear; fall back to the home list
        entry = "index.html"
    api = _settings(site).get("api") or []
    st = core.set_state(root, kind="file", path=f"{slot}/{entry}", title=title, **({"live": True} if live else {}), **({"api": api} if api else {}))
    prune(root, slot)
    return st


def run(root: Path, files: list[str], title: str, reset: bool, watch: bool = False, api: list[int] | None = None) -> dict:
    """Add the given Markdown (files, a folder of .md, or stdin) to the View set and show it. No input just rebuilds."""
    _hugo()
    site = site_dir(root)
    if reset:
        shutil.rmtree(site / "content", ignore_errors=True)
        (site / "pages.json").unlink(missing_ok=True)
        (site / "settings.json").unlink(missing_ok=True)
    shutil.rmtree(site / "static", ignore_errors=True)         # an earlier version kept copies here; Hugo reads the originals now
    ensure_scaffold(site)
    if api:                                          # the live server(s) on this PC that these pages may call; `avs view --api 8123`
        if not all(isinstance(n, int) and 1 <= n <= 65535 for n in api):
            raise ValueError("--api takes a port number (1-65535).")
        ports = sorted(set(_settings(site).get("api") or []) | set(api))
        (site / "settings.json").write_text(json.dumps({"api": ports}))
    pages = _load(site)

    paths: list[Path] = []
    for f in files:
        p = Path(f).expanduser()
        paths.extend(sorted(p.glob("*.md")) if p.is_dir() else [p])
    touched: list[dict] = []
    for p in paths:
        if not p.is_file():
            raise ValueError(f"{p} does not exist.")
        real = str(p.resolve())
        pg = next((x for x in pages if x.get("src") == real), None)       # the same file again updates its page rather than adding another
        if pg is None:
            pg = {"src": real, "stem": p.stem.replace("_", " ")}
            pg["name"] = f"{len(pages) + 1:02d}-{_slug(_title_of(_read(p), pg['stem']))}"
            pages.append(pg)
        if title and len(paths) == 1:
            pg["title"] = title
        touched.append(pg)
    if not paths:                                    # read piped Markdown, but only if some is actually waiting (never block)
        body = _piped_stdin()
        if body:
            pg = {"stem": "Page", "cwd": str(Path.cwd()), "title": title or None}
            pg["name"] = f"{len(pages) + 1:02d}-{_slug(title or _title_of(body, 'Page'))}"
            (site / "content" / pg["name"]).mkdir(parents=True, exist_ok=True)
            (site / "content" / pg["name"] / "page.md").write_text(body.strip() + "\n")
            pages.append(pg)
            touched.append(pg)

    _save(site, pages)
    if not pages:                                    # nothing to show (e.g. --reset on its own)
        return core.set_state(root, kind="none")
    write_config(site, pages)
    if touched:                                      # open the first page of this batch (e.g. a README)
        st = build(root, site, touched[0]["name"] + ".html", _page_title(site, touched[0]))
    else:                                            # no new input: rebuild and reopen the home list
        st = build(root, site, "index.html", "AVS")
    if watch:
        start_watcher(root)
    return st


# ---- live updates -----------------------------------------------------------------------------------------------------------

def _stat(p: Path):
    try:
        s = p.stat()
        return (s.st_mtime_ns, s.st_size)
    except OSError:
        return None


def signature(site: Path, pages: list[dict]) -> list:
    """What the current page set depends on: each source file and each image it uses, by modification time and size."""
    sig = [_stat(site / "pages.json")]
    for pg in pages:
        src = _source(site, pg)
        sig.append(_stat(src))
        sig.extend(_stat(Path(folder) / name) for folder, _, name in _asset_mounts(pg, _read(src)))
    return sig


def refresh(root: Path) -> dict:
    """Rebuild what is showing from the current sources, staying on the same page and marking it live so the phone keeps its place."""
    site = site_dir(root)
    st = core.get_state(root)
    entry = (st.get("path") or "").rpartition("/")[2] or "index.html"
    ensure_scaffold(site)                            # a newer avs may have a newer page template
    write_config(site, _load(site))
    return build(root, site, entry, st.get("title") or "AVS", live=True)


def watch(root: Path, poll: float = 1.0, rebuild: bool = False) -> None:
    site = site_dir(root)
    lock = open(site / "watch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:                          # one watcher is enough
        return
    mine = [Path(__file__), Path(core.__file__)]
    loaded = [_stat(p) for p in mine]
    last = None
    while True:
        if [_stat(p) for p in mine] != loaded:       # avs itself was changed: start again so the new code is what keeps the page fresh
            os.execv(sys.executable, [sys.executable, "-m", "avs.view", "watch", str(root), "rebuild"])
        st = core.get_state(root)
        slot = (st.get("path") or "").partition("/")[0]
        if st.get("kind") != "file" or not slot or not (root / "view" / slot / MARKER).exists():
            return                                   # something else is on screen now: this page is no longer ours to keep fresh
        pages = _load(site)
        sig = signature(site, pages)
        if rebuild or (last is not None and sig != last):
            rebuild = False
            try:
                refresh(root)
            except ValueError:
                pass                                 # a half-saved file or a Hugo hiccup: keep showing the last good build, try again on the next change
        last = sig
        time.sleep(poll)


def start_watcher(root: Path) -> None:
    subprocess.Popen([sys.executable, "-m", "avs.view", "watch", str(root)], cwd=Path(__file__).resolve().parent.parent,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["watch"] and len(sys.argv) in (3, 4):
        watch(Path(sys.argv[2]), rebuild=sys.argv[3:] == ["rebuild"])
