# AVS: Agent View and Send

Lets an AI agent running on your PC **show you things on your Android phone** (a Markdown report, a to-do list, an image, a PDF, a page served on the PC),
**send you files**, **receive files back**, and **read your comments** on what it showed, with no VPN account and no router port-forwarding.

- **View** tab: whatever the agent last showed. Markdown renders as a small e-reader with a Contents drawer, and follows the agent's edits live.
- **Files** tab: files from the agent (an `.apk` gets an Install button) and a place to send files back.
- **Comments**: every rendered Markdown page has a comment box, and every checklist item has its own. The agent reads them with `avs comments`.

```
Android app (WebView shell) ──► home Wi-Fi: http://PC:8787
                            └─► anywhere: Cloudflare quick tunnel (cloudflared, no account)
                                the tunnel address is posted to a secret ntfy topic that the phone reads
PC:  avs.service (FastAPI)  +  `avs` command for the agent  +  ~/AVS/{outbox,inbox,view}
```

## Requirements
- A Linux PC with Python 3.10+. Developed on Arch; nothing is distribution-specific, but only Linux has been tried.
- An Android phone (8.0+). To build the app: a JDK (17+) and the Android SDK command-line tools (build-tools 35, platform 35, under `~/Android/sdk` or `$ANDROID_HOME`). There is no prebuilt APK.
- Optional: [Hugo](https://gohugo.io/) (rendering Markdown), [cloudflared](https://github.com/cloudflare/cloudflared) (reaching the PC away from home),
  `qrencode` (a QR code for pairing), `notify-send` (a desktop note when the phone sends something).

## Install
On the PC:
```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
ln -s "$PWD/avs.sh" ~/.local/bin/avs                                  # the command the agent runs
mkdir -p ~/.config/systemd/user && sed "s|@DIR@|$PWD|g" contrib/avs.service > ~/.config/systemd/user/avs.service
systemctl --user daemon-reload && systemctl --user enable --now avs
avs pair                               # prints the pairing QR and avs://pair?… link (it is the key to your files; keep it private)
```
For the home-Wi-Fi path allow the port once, for example `sudo ufw allow from 192.168.0.0/16 to any port 8787 proto tcp` (the tunnel does not need it).

On the phone: build and install the app with `android/build.sh` (plain SDK tools, no Gradle; it signs with a throwaway debug key it creates on first run),
then `adb install -r android/build/avs.apk`. Open it and paste the `avs://pair?…` link once (or open that link on the phone). Share any file to **AVS** from another
app to send it to the PC.

## Using it
These are the commands an agent runs. They use the folder `~/AVS` directly, so they need no network.
```
avs send FILE...            # the phone can download it from Files
avs show http://localhost:8090   # a page served on this PC; AVS passes it through, so it works from anywhere (use relative links)
avs show page.html | image.png | report.pdf | folder-with-index.html
avs show notes.md report.md      # Markdown: rendered with Hugo; pages add up behind a Contents drawer (--reset starts over, --once: no live updates)
avs show tool.md --api 8123      # a hybrid page: its own <script src="tool.js"> may call the live server on PC port 8123 (AVS.get(8123, "path"))
echo '# Note' | avs show --title Note
echo '<h1>hi</h1>' | avs html --title Test
avs inbox [--new] [--take DIR]   # files the phone sent
avs wait [--timeout S]      # blocks until the phone sends a file or writes a comment; run it in the background to be told
avs comments [--new] [--page TEXT] [--json]   # what the person wrote in the comment boxes (--new: not reported before)
avs clean [--days N] [--dry-run]   # delete outbox files older than N days (default: outbox_keep_days in the config)
avs clear | status
```

### Showing Markdown
`avs show` works out what it is given (a web address, Markdown, or any other file or folder; `avs view` is the same command). For Markdown it renders with Hugo into a
small static site laid out like an e-reader: a hamburger **Contents** drawer listing every page, the current position, and prev/next chapter links. The built page is
static, so it reaches the phone the same way a file does. Nothing is copied or edited: Hugo mounts each Markdown file, and only the image files that page names, from
where they live (it cannot see anything else in those folders), and a small watcher rebuilds when a source changes, so the phone follows your edits and keeps its
place; it stops by itself when something else is shown.

For a page that *does* something (buttons, live data) run a real server and `avs show http://localhost:PORT` instead, or make the page a **hybrid**: write the Markdown
with a plain `<script src="tool.js">` (and `<link href="look.css">`) next to it, run the live server on a port, and `avs show tool.md --api PORT`. Those script and style
files are mounted in place like images (only the ones the page names; a script's own imports are not followed). Each page has `AVS.url(port, path)` and
`AVS.get(port, path)`, which find the pass-through address and return the parsed JSON or text. The page may read only the port it was given with `--api` (the server
sends the CORS header for that port and nothing else; `--reset` forgets it). The pass-through is GET only, so an action that changes something should take a
one-time code.

### Comments
Every page `avs show` renders from Markdown gets a comment box at the end, and every checklist item (`- [ ]`) gets its own under it, so a to-do list is just a Markdown
file with checkboxes. The person types and taps Save comment; the app (which holds the login) stores it in `~/AVS/comments.json` with the page's title and source file,
shows it again on the next visit, and a PC notification appears. Read them with `avs comments --new`, or let `avs wait` wake you for them like a file. An empty comment
removes it; an item is matched by its bold title, so renaming an item starts it fresh. The boxes show only inside the app (a page opened in a plain browser tab has
nowhere to send them) and not on pages shown with `avs show FILE.html` or a web address.

### Housekeeping
Opt-in: set `"outbox_keep_days": 7` in `~/.config/avs/config.json` and the server removes outbox files older than that every hour (0, the default, keeps everything;
the inbox is never touched). In the app's Files tab an `.apk` in "From your agent" has an **Install** button: the app downloads it and opens the system installer (the
first time, Android asks you to allow AVS to install apps).

## Safety
AVS gives a paired phone access to a folder on your PC and lets an agent put pages in front of you, so read this before you use it.

- **The token is the key.** Everything needs the access token (192-bit, generated on first run, in `~/.config/avs/config.json`, mode 600). The pairing link and QR
  code contain it: treat them like a password. The tunnel address is public but useless without the token.
- **Who can connect.** Direct connections are limited to this PC and private home-network addresses (`allow` in the config). Away from home, traffic arrives through
  the tunnel from this machine, so the token is the only guard there.
- **Encryption: not end to end, and not on the home network.** The access token is a password, not an encryption key; it does not encrypt anything. On the home
  network the phone talks to the PC over plain `http://`, which is **not encrypted**: anyone on the same Wi-Fi who can capture traffic can read it, including the token.
  Away from home the address is `https://…trycloudflare.com`, so the phone-to-Cloudflare leg is TLS, but Cloudflare ends the encryption and relays to your PC, so
  Cloudflare can see what passes through, token included. For real end-to-end encryption put the PC and phone on something like WireGuard or Tailscale and serve HTTPS
  yourself; AVS does not do that.
- **Tunnel only (opt-in).** If you would rather never use plain http, set `"tunnel_only": true` in `~/.config/avs/config.json`, restart the service and run `avs pair`
  again. The PC then listens on itself only (nothing on the home network can connect), the pairing link carries no home-network address, and the phone always uses the
  https tunnel. It needs cloudflared and `"relay": true`, is slower, and trusts Cloudflare as above. `avs status` says which mode is on. The default is unchanged.
- **Only show pages you trust.** A page the agent shows runs in a sandboxed iframe with no cookies and no access to the app's API, but its address contains the token
  (that is how its relative links work). A malicious page's script could use that address to make GET requests through the pass-through to any port on this PC except
  AVS's own, and to read the ones you gave it with `--api`. Treat what you `avs show` as code you are running.
- **The pass-through is GET only,** and `--api PORT` opens cross-origin reads for that one port only. Anything that changes state should be guarded by a one-time code.
- **Notifications** go through [ntfy.sh](https://ntfy.sh) (or your own UnifiedPush distributor) on a secret, per-install topic; they carry a title and a few words,
  not file contents. The tunnel address is also posted to that topic.
- **Not audited.** This is a personal tool shared as is. It has not had a security review. Run it on a machine and network where that is acceptable.

## Tests
`.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest tests` (the Markdown tests are skipped if Hugo is not installed).

## License
MIT, see [LICENSE](LICENSE).
