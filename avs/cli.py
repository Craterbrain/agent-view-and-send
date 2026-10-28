"""`avs`: what the agent runs. It works on the AVS folder directly, so it needs no network and no server round trip.

  avs send FILE...          (notifies the phone) put files where the person can download them (--move to move instead of copy)
  avs outbox                list files in the outbox
  avs show TARGET...        show something in the View tab; it works out what it was given:
                              a web address, or a localhost one (passed through; use relative links)
                              Markdown files, or a folder of them: rendered with Hugo into an e-reader (Contents drawer); pages add up,
                                --reset starts over, --once stops it following edits live, --api PORT lets the page's own
                                <script src> call the live server on that PC port: AVS.get(PORT, "path"); no target reads Markdown from stdin
                              any other file (.html .png .pdf .txt ...) or a folder with index.html: shown as it is
                            (--title T names it; `avs view` is the same command)
  avs html [--title T]      show HTML read from standard input
  avs inbox [--new] [--take DIR]   list what the person sent (--new: only files not reported before); --take copies into DIR
  avs wait [--timeout S]    wait until the person sends a file or writes a comment, print it, exit (run it in the background to be told)
  avs comments [--new] [--page TEXT] [--json]   the comments the person wrote on pages (one box per Markdown page, one per checklist item); --new: only those not reported before
  avs clean [--days N] [--dry-run]   delete outbox files older than N days (default: outbox_keep_days from the config; nothing if 0)
  avs clear                 empty the View tab
  avs pair                  show the pairing QR (scan it in the Android app) and the link
  avs status | serve
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import core


def fmt_comment(r: dict) -> str:
    """One comment as the agent reads it: where it was written, what it is on, and the text."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["time"]))
    return f"comment on {r['title'] or r['page']}" + (f", item \"{r['item']}\"" if r["item"] else " (the page)") + f"  ({r['src'] or r['page']}, {when})\n    " + r["text"].replace("\n", "\n    ")


def main(argv=None):
    p = argparse.ArgumentParser(prog="avs", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send"); s.add_argument("files", nargs="+"); s.add_argument("--move", action="store_true")
    sub.add_parser("outbox")
    w = sub.add_parser("show", aliases=["view"]); w.add_argument("targets", nargs="*"); w.add_argument("--title", default="")
    w.add_argument("--reset", action="store_true"); w.add_argument("--once", action="store_true"); w.add_argument("--api", type=int, action="append")
    h = sub.add_parser("html"); h.add_argument("--title", default="")
    i = sub.add_parser("inbox"); i.add_argument("--take"); i.add_argument("--new", action="store_true")
    wt = sub.add_parser("wait"); wt.add_argument("--timeout", type=float, default=0)
    cm = sub.add_parser("comments"); cm.add_argument("--new", action="store_true"); cm.add_argument("--page", default=""); cm.add_argument("--json", action="store_true")
    cl = sub.add_parser("clean"); cl.add_argument("--days", type=float); cl.add_argument("--dry-run", action="store_true")
    for n in ("clear", "pair", "status", "serve"):
        sub.add_parser(n)
    a = p.parse_args(argv)

    cfg = core.load_config()
    root = core.root_of(cfg)
    try:
        if a.cmd == "send":
            for f in a.files:
                name = core.send_file(root, Path(f), a.move).name
                print("sent:", name)
                core.notify(cfg, "File from your agent", name)
        elif a.cmd in ("show", "view"):
            from . import view
            kinds = {view.kind_of(x) for x in a.targets}
            if len(a.targets) > 1 and kinds != {"markdown"}:
                raise ValueError("Give one thing to show (a web address, a file or a folder), or several Markdown files.")
            if kinds <= {"markdown"}:                       # Markdown, or nothing (rebuild the set, --reset, or Markdown on stdin)
                st = view.run(root, a.targets, a.title, a.reset, watch=not a.once, api=a.api)
            elif a.reset or a.api or a.once:
                raise ValueError("--reset, --once and --api only apply to Markdown.")
            elif kinds == {"url"}:
                st = core.show_url(root, a.targets[0], a.title)
            else:
                st = core.show_path(root, Path(a.targets[0]), a.title)
            if st.get("kind") == "none":
                print("cleared")
            else:
                print("shown")
                core.notify(cfg, "New page to look at", st.get("title") or (a.targets[0] if a.targets else "Page from the agent"))
        elif a.cmd == "html":
            st = core.show_html(root, sys.stdin.read(), a.title)
            print("shown")
            core.notify(cfg, "New page to look at", st.get("title") or "Page from the agent")
        elif a.cmd == "outbox":
            rows = core.listing(root, "outbox")
            for r in rows:
                t = time.strftime('%Y-%m-%d %H:%M', time.localtime(r.get('created', r['time'])))
                print(f"{root / 'outbox' / r['name']}  ({r['size']} bytes, {t})")
            if not rows:
                print("(nothing in outbox)")
        elif a.cmd == "inbox":
            rows = core.unseen(root) if a.new else core.listing(root, "inbox")
            core.mark_seen(root, [r["name"] for r in rows])
            dest = Path(a.take).expanduser() if a.take else None
            if dest:
                dest.mkdir(parents=True, exist_ok=True)
            for r in rows:
                if dest:
                    print(shutil.copy2(root / "inbox" / r["name"], core.unique(dest, r["name"])))
                else:
                    t = time.strftime('%Y-%m-%d %H:%M', time.localtime(r.get('created', r['time'])))
                    print(f"{root / 'inbox' / r['name']}  ({r['size']} bytes, {t})")
            if not rows:
                print("(nothing from the phone)")
        elif a.cmd == "wait":
            got, notes = core.wait_for_event(root, a.timeout)
            for r in got:
                print(f"{root / 'inbox' / r['name']}  ({r['size']} bytes)")
            for r in notes:
                print(fmt_comment(r))
            if not got and not notes:
                sys.exit(1)
        elif a.cmd == "comments":
            rows = core.comments_unseen(root) if a.new else core.comment_rows(root)
            rows = [r for r in rows if a.page.lower() in (r["title"] + " " + r["src"] + " " + r["page"]).lower()]
            core.comments_mark_seen(root, rows)
            if a.json:
                print(json.dumps(rows, indent=1, ensure_ascii=False))
            else:
                for r in rows:
                    print(fmt_comment(r))
                if not rows:
                    print("(no new comments)" if a.new else "(no comments)")
        elif a.cmd == "clean":
            days = a.days if a.days is not None else cfg["outbox_keep_days"]
            if days <= 0:
                sys.exit("avs: give --days N, or set outbox_keep_days in the config (0 keeps everything).")
            gone = core.clean_outbox(root, days, a.dry_run)
            for n in gone:
                print(("would remove:" if a.dry_run else "removed:"), n)
            print(f"{len(gone)} file(s) older than {days:g} days" + (" (dry run, nothing deleted)" if a.dry_run else ""))
        elif a.cmd == "clear":
            core.set_state(root, kind="none")
            print("cleared")
        elif a.cmd == "pair":
            link = core.pairing_link(cfg)
            if shutil.which("qrencode"):
                subprocess.run(["qrencode", "-t", "ansiutf8", link])
            print("\nScan this in the AVS app. Link:", link, "\nKeep it private: it is the key to your files.")
        elif a.cmd == "status":
            st = core.get_state(root)
            print(f"folder {root}\nshowing {st.get('title') or 'nothing'}\noutbox {len(core.listing(root, 'outbox'))} files, inbox {len(core.listing(root, 'inbox'))} files")
            print("alerts: " + ("through UnifiedPush (the ntfy app on the phone)" if cfg.get("push_endpoint") else "through the ntfy topic (the app's own listener)"))
        elif a.cmd == "serve":
            from . import server
            server.main()
    except ValueError as e:
        sys.exit(f"avs: {e}")


if __name__ == "__main__":
    main()
