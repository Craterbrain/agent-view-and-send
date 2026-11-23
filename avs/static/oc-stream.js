// AVS: runs first in OpenCode's own web app (served under /oc/), before the app's code.
//  1. The app calls parts of its server from the site root ("/global/health", "/api/session/...") whatever its configured base, and
//     "/api/..." is also AVS's own space. Every same-origin request (fetch, XHR, WebSocket, EventSource) is moved under /oc/.
//  2. It reads its live events with fetch() on /event and /global/event. Through Cloudflare's quick tunnel a streamed response is held
//     back until tens of KB have built up, so replies never appeared away from home. Those requests go to AVS's /oc-stream/ WebSocket,
//     which carries the same text/event-stream unchanged, and the app gets an ordinary streaming Response. If the socket cannot open,
//     the real fetch is used.
(function () {
  const realFetch = window.fetch.bind(window), RealWS = window.WebSocket, RealES = window.EventSource, xhrOpen = XMLHttpRequest.prototype.open;
  const EVENTS = /^\/oc(\/.*)?\/event$/;
  const OURS = /^\/oc(\/|$)|^\/oc-stream\//;
  function place(raw) {                              // an absolute URL, moved under /oc/ when it is ours and not there yet
    const u = new URL(String(raw), location.href);
    if (u.host === location.host && !OURS.test(u.pathname)) u.pathname = "/oc" + u.pathname;
    return u;
  }
  function stream(u, signal) {                       // u: a URL under /oc/ ending in /event; resolves to a Response, or null
    const ws = new RealWS((location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + "/oc-stream" + u.pathname.slice(3) + u.search);
    return new Promise(resolve => {
      let ctl, opened = false;
      const body = new ReadableStream({ start(c) { ctl = c; }, cancel() { try { ws.close(); } catch (e) {} } });
      const enc = new TextEncoder();
      ws.onopen = () => { opened = true; resolve(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })); };
      ws.onmessage = e => { try { ctl.enqueue(enc.encode(e.data)); } catch (x) {} };
      ws.onclose = () => { if (!opened) return resolve(null); try { ctl.close(); } catch (x) {} };
      ws.onerror = () => { if (!opened) resolve(null); };
      if (signal) signal.addEventListener("abort", () => { try { ws.close(); } catch (e) {} try { ctl.error(new DOMException("Aborted", "AbortError")); } catch (x) {} });
    });
  }
  window.fetch = async function (input, init) {
    const req = input instanceof Request ? input : null;
    let u;
    try { u = place(req ? req.url : input); } catch (e) { return realFetch(input, init); }
    const method = ((init && init.method) || (req && req.method) || "GET").toUpperCase();
    if (method === "GET" && u.host === location.host && EVENTS.test(u.pathname)) {
      const dir = new Headers((init && init.headers) || (req && req.headers) || {}).get("x-opencode-directory");   // a socket carries no headers
      if (dir && !u.searchParams.has("directory")) u.searchParams.set("directory", decodeURIComponent(dir));
      const r = await stream(u, (init && init.signal) || (req && req.signal));
      if (r) return r;
    }
    return realFetch(req ? new Request(u.href, req) : u.href, init);
  };
  XMLHttpRequest.prototype.open = function (method, url, ...rest) { return xhrOpen.call(this, method, place(url).href, ...rest); };
  function WS(url, protocols) { const u = place(url); return protocols === undefined ? new RealWS(u.href) : new RealWS(u.href, protocols); }
  WS.prototype = RealWS.prototype;
  for (const k of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) WS[k] = RealWS[k];
  window.WebSocket = WS;
  if (RealES) {
    function ES(url, opts) { return new RealES(place(url).href, opts); }
    ES.prototype = RealES.prototype;
    for (const k of ["CONNECTING", "OPEN", "CLOSED"]) ES[k] = RealES[k];
    window.EventSource = ES;
  }
})();
