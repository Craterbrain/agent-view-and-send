// AVS chat: a phone-sized client for the OpenCode server on the PC, laid out like the Claude app: a drawer of chats, plain replies with
// tool steps folded away, a rounded composer with model and agent pickers, and cards for permission requests and questions.
// Everything goes through AVS: requests to /oc/... (the authenticated proxy) and live events over the /oc-stream/ WebSocket, which
// the Cloudflare tunnel passes through at once (it holds back streamed HTTP responses). No socket is kept while the app is hidden.
(function () {
"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = { get(k) { try { return JSON.parse(localStorage.getItem("avs.chat." + k)); } catch (e) { return null; } },
                set(k, v) { try { localStorage.setItem("avs.chat." + k, JSON.stringify(v)); } catch (e) {} } };
const SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
const STOP = '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>';

const C = {
  started: false, dir: "", url: "", sessions: [], id: "", msgs: new Map(), order: [], status: {}, perms: new Map(), questions: new Map(),
  open: new Set(), files: [], commands: [], slash: null, agents: [], agent: store.get("agent") || "", providers: [], model: store.get("model"), ws: null, wsTry: 0,
  dirty: false, full: false, pending: null, err: "", connected: false, search: "", onTitle: null,
};

// ---------- requests ----------
function sess(id) { return C.sessions.find(s => s.id === id); }
function curDir() { const s = sess(C.id); return (s && s.directory) || C.dir; }
async function api(path, opt = {}) {
  const u = new URL("/oc" + path, location.href), dir = opt.dir || curDir();
  if (dir && !u.searchParams.has("directory")) u.searchParams.set("directory", dir);
  const init = { method: opt.method || "GET", cache: "no-store", headers: {} };
  if (opt.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opt.body); }
  const r = await fetch(u, init);
  const t = await r.text();
  if (!r.ok) { let m = t; try { const j = JSON.parse(t); m = j.data?.message || j.message || j.error || t; } catch (e) {} throw new Error(String(m || r.status).slice(0, 240)); }
  try { return t ? JSON.parse(t) : null; } catch (e) { return t; }
}
function toast(msg) { const t = $("ctoast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 2600); }
async function copy(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) { const a = document.createElement("textarea"); a.value = text; a.style.position = "fixed"; a.style.opacity = "0"; document.body.append(a); a.select(); try { document.execCommand("copy"); } catch (x) {} a.remove(); }
  toast("Copied");
}

// ---------- Markdown (the subset models write: fences, headings, lists, quotes, tables, emphasis, links) ----------
function inl(s) {
  const codes = [];
  s = s.replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (_, b, c) => { codes.push(c.trim() ? c.replace(/^ | $/g, "") : c); return "\u0000" + (codes.length - 1) + "\u0000"; });
  s = esc(s)
    .replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, (m, alt, u) => /^https?:/i.test(u) ? `<a href="${u}" target="_blank" rel="noopener">${alt || "image"}</a>` : alt)
    .replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (m, t, u) => /^(https?:|mailto:)/i.test(u) ? `<a href="${u}" target="_blank" rel="noopener">${t}</a>` : t)
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?'"])/g, (m, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener">${u}</a>`)
    .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>").replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, "$1<strong>$2</strong>")
    .replace(/(^|[^*\w])\*(?=[^\s*])([^*]*?[^\s*])\*(?![*\w])/g, "$1<em>$2</em>").replace(/(^|[^\w])_(?=[^\s_])([^_]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>")
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
}
const LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
function isBlock(l) { return /^\s*(```|~~~)/.test(l) || /^#{1,6}\s/.test(l) || /^\s*>/.test(l) || LIST.test(l) || /^\s*([-*_])(\s*\1){2,}\s*$/.test(l); }
function cells(l) { return l.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map(c => c.trim()); }
function md(src) {
  const L = String(src || "").replace(/\r\n?/g, "\n").split("\n"), out = [];
  let i = 0;
  while (i < L.length) {
    const l = L[i];
    const f = l.match(/^\s*(```+|~~~+)\s*([^\s`]*)/);
    if (f) {
      const code = []; i++;
      while (i < L.length && !L[i].trim().startsWith(f[1])) code.push(L[i++]);
      i++;
      out.push(`<div class="code"><div class="codeh"><span>${esc(f[2] || "code")}</span><button data-copy>Copy</button></div><pre><code>${esc(code.join("\n"))}</code></pre></div>`);
      continue;
    }
    if (!l.trim()) { i++; continue; }
    const h = l.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (h) { const n = Math.min(h[1].length + 1, 6); out.push(`<h${n}>${inl(h[2])}</h${n}>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
    if (/^\s*>/.test(l)) { const q = []; while (i < L.length && /^\s*>/.test(L[i])) q.push(L[i++].replace(/^\s*>\s?/, "")); out.push(`<blockquote>${md(q.join("\n"))}</blockquote>`); continue; }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < L.length && /^\s*\|?\s*:?-{2,}/.test(L[i + 1])) {
      const head = cells(l); i += 2; const rows = [];
      while (i < L.length && /^\s*\|.*\|\s*$/.test(L[i])) rows.push(cells(L[i++]));
      out.push(`<table><thead><tr>${head.map(c => `<th>${inl(c)}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${inl(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    const m = l.match(LIST);
    if (m) {
      const base = m[1].length, ordered = /\d/.test(m[2]), items = [];
      while (i < L.length) {
        const x = L[i], mm = x.match(LIST);
        const same = mm && mm[1].length <= base + 1 && /\d/.test(mm[2]) === ordered;      // a sibling item; a different kind starts a new list
        if (same) { items.push([mm[3]]); i++; continue; }
        if (mm && mm[1].length <= base + 1) break;
        if (!x.trim()) { const n = L[i + 1], nm = n !== undefined && n.match(LIST);
          if (n !== undefined && ((/^\s{2,}\S/.test(n) && !(nm && nm[1].length <= base + 1)) || (nm && nm[1].length <= base + 1 && /\d/.test(nm[2]) === ordered))) { items.at(-1).push(""); i++; continue; }
          break; }
        if (/^\s+\S/.test(x)) { items.at(-1).push(x.replace(new RegExp("^\\s{0," + (base + 4) + "}"), "")); i++; continue; }
        if (!isBlock(x)) { items.at(-1).push(x); i++; continue; }
        break;
      }
      const start = ordered ? parseInt(m[2], 10) : 1;
      const lis = items.map(([first, ...rest]) => {
        const t = first.match(/^\[([ xX])\]\s+(.*)/);
        const head = t ? `${t[1] === " " ? "☐" : "☑"} ${inl(t[2])}` : inl(first);
        const more = rest.join("\n").trim() ? md(rest.join("\n")) : "";
        return `<li${t ? ' class="task"' : ""}>${head}${more}</li>`;
      }).join("");
      out.push(ordered ? `<ol${start !== 1 ? ` start="${start}"` : ""}>${lis}</ol>` : `<ul>${lis}</ul>`);
      continue;
    }
    const p = [l]; i++;
    while (i < L.length && L[i].trim() && !isBlock(L[i]) && !/^\s*\|.*\|\s*$/.test(L[i])) p.push(L[i++]);
    out.push(`<p>${inl(p.join("\n")).replace(/\n/g, "<br>")}</p>`);
  }
  return out.join("");
}

// ---------- messages ----------
function upsertInfo(info) {
  let m = C.msgs.get(info.id);
  if (!m) { m = { info, parts: [] }; C.msgs.set(info.id, m); C.order.push(info.id); C.order.sort(); }
  else m.info = info;
  return m;
}
function upsertPart(part) {
  let m = C.msgs.get(part.messageID);
  if (!m) m = upsertInfo({ id: part.messageID, sessionID: part.sessionID, role: "assistant", time: { created: Date.now() } });
  const i = m.parts.findIndex(p => p.id === part.id);
  if (i < 0) { m.parts.push(part); m.parts.sort((a, b) => a.id < b.id ? -1 : 1); } else m.parts[i] = part;
}
async function loadMessages(id) {
  try {
    const list = await api(`/session/${id}/message`);
    if (id !== C.id) return;
    C.msgs = new Map(); C.order = [];
    for (const { info, parts } of list || []) { const m = upsertInfo(info); m.parts = parts || []; }
    C.err = ""; render(true); scrollEnd();
  } catch (e) {
    if (id !== C.id) return;
    if (/not.?found|404/i.test(e.message)) { select("", false); return; }      // a chat deleted elsewhere: start a new one
    C.err = "Couldn’t load this chat: " + e.message; render(true);
  }
}
const VERB = { bash: "Ran", read: "Read", edit: "Edited", write: "Wrote", glob: "Found files", grep: "Searched", list: "Listed", webfetch: "Fetched", websearch: "Searched the web",
  codesearch: "Searched code", task: "Ran agent", skill: "Used skill", patch: "Patched", multiedit: "Edited", lsp: "Checked", todoread: "Read to-dos" };
function short(p) { const s = String(p || ""); return C.dir && s.startsWith(C.dir + "/") ? s.slice(C.dir.length + 1) : s.replace(/^\/home\/[^/]+\//, "~/"); }
function stepLabel(p) {
  if (p.type === "reasoning") return `<b>Thinking</b>`;
  const st = p.state || {}, i = st.input || {};
  const what = i.description || i.command || (i.filePath && short(i.filePath)) || i.pattern || i.url || i.query || i.name || st.title || "";
  return `<b>${esc(VERB[p.tool] || p.tool)}</b> ${esc(String(what).split("\n")[0].slice(0, 140))}`;
}
function stepIcon(p) {
  if (p.type === "reasoning") return p.time && p.time.end ? "✻" : '<span class="spin"></span>';
  const s = (p.state || {}).status;
  return s === "error" ? "✕" : s === "completed" ? "✓" : '<span class="spin"></span>';
}
function cut(s, n = 6000) { s = String(s ?? ""); return s.length > n ? s.slice(0, n) + `\n… ${s.length - n} more characters` : s; }
function diffHtml(d) { return cut(d, 12000).split("\n").map(l => `<span class="${l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : ""}">${esc(l)}</span>`).join("\n"); }
function stepDetail(p) {
  if (p.type === "reasoning") return `<div class="think">${esc(p.text)}</div>`;
  const st = p.state || {}, i = st.input || {}, meta = st.metadata || {};
  const parts = [];
  if (p.tool === "bash") parts.push(`<pre>$ ${esc(i.command || "")}</pre>`);
  else if ((p.tool === "edit" || p.tool === "write" || p.tool === "multiedit" || p.tool === "patch") && (meta.diff || i.content)) parts.push(`<pre>${meta.diff ? diffHtml(meta.diff) : esc(cut(i.content))}</pre>`);
  else if (Object.keys(i).length) parts.push(`<pre>${esc(cut(JSON.stringify(i, null, 2), 3000))}</pre>`);
  const outp = st.status === "error" ? st.error : (p.tool === "edit" && meta.diff ? "" : st.output);
  if (outp) parts.push(`<pre>${esc(cut(outp))}</pre>`);
  return parts.length ? `<div class="sdet">${parts.join("")}</div>` : "";
}
function stepRow(p) {
  const open = C.open.has(p.id), err = (p.state || {}).status === "error";
  return `<button class="step${err ? " err" : ""}" data-step="${esc(p.id)}"><span class="ic">${stepIcon(p)}</span><span class="lbl">${stepLabel(p)}</span></button>${open ? stepDetail(p) : ""}`;
}
function stepsHtml(run) {
  if (run.length === 1) return `<div class="steps">${stepRow(run[0])}</div>`;
  const key = "g" + run[0].id, open = C.open.has(key), last = run[run.length - 1];
  const live = run.some(p => p.type === "reasoning" ? !(p.time && p.time.end) : !["completed", "error"].includes((p.state || {}).status));
  const tools = run.filter(p => p.type === "tool").length;
  const head = live ? stepLabel(last) : `<b>${tools ? tools + (tools === 1 ? " step" : " steps") : "Thought"}</b>${run.some(p => (p.state || {}).status === "error") ? " · some failed" : ""}`;
  return `<div class="steps"><button class="stepsh" data-step="${esc(key)}"><span class="ic">${live ? '<span class="spin"></span>' : open ? "▾" : "▸"}</span><span class="lbl">${head}</span></button>${open ? `<div class="stepsin">${run.map(stepRow).join("")}</div>` : ""}</div>`;
}
function todoHtml(p) {
  const todos = ((p.state || {}).input || {}).todos || [];
  if (!todos.length) return "";
  return `<div class="todo">${todos.map(t => `<div class="${t.status === "completed" ? "done" : t.status === "in_progress" ? "now" : ""}"><span>${t.status === "completed" ? "☑" : t.status === "in_progress" ? "▸" : "☐"}</span><span>${esc(t.content)}</span></div>`).join("")}</div>`;
}
function errText(e) { return e && (e.data && e.data.message || e.message || e.name) || ""; }
function modelName(providerID, modelID) {
  const p = C.providers.find(x => x.id === providerID), m = p && p.models && p.models[modelID];
  return (m && m.name) || modelID || "";
}
function attHtml(parts) {
  const files = parts.filter(p => p.type === "file");
  if (!files.length) return "";
  return `<div class="att">${files.map(f => /^image\//.test(f.mime) && /^data:|^https?:/.test(f.url) ? `<img src="${esc(f.url)}" alt="${esc(f.filename || "image")}">` : `<span class="chip">📎 ${esc(f.filename || short((f.source || {}).path) || f.mime)}</span>`).join("")}</div>`;
}
function commandOf(text) {                          // which command template a user message was expanded from, and the arguments typed after it
  const t = text.trim();
  for (const c of C.commands) {
    const tpl = String(c.template || "").trim(), head = tpl.split("$ARGUMENTS")[0].trim().slice(0, 120);
    if (head.length < 24 || !t.startsWith(head)) continue;
    return { name: c.name, args: !tpl.includes("$ARGUMENTS") && t.startsWith(tpl) ? t.slice(tpl.length).trim().slice(0, 300) : "" };
  }
  return null;
}
function userHtml(m) {
  const text = m.parts.filter(p => p.type === "text" && !p.synthetic && !p.ignored).map(p => p.text).join("\n\n");
  if (!text && !m.parts.some(p => p.type === "file")) return "";
  const cmd = text && commandOf(text);
  if (cmd && !C.open.has("u" + m.info.id))         // a /command: OpenCode stores the expanded template, show what was typed (tap for all of it)
    return `<div class="cu" data-msg="${esc(m.info.id)}"><div>${attHtml(m.parts)}<button class="bub cmdb" data-step="u${esc(m.info.id)}"><b>/${esc(cmd.name)}</b>${cmd.args ? " " + esc(cmd.args) : ""}</button></div></div>`;
  return `<div class="cu" data-msg="${esc(m.info.id)}"><div>${attHtml(m.parts)}${text ? `<div class="bub"${cmd ? ` data-step="u${esc(m.info.id)}"` : ""}>${esc(text)}</div>` : ""}</div></div>`;
}
function turnHtml(msgs, isLast, busy) {
  const out = [];
  let run = [];
  const flush = () => { if (run.length) out.push(stepsHtml(run)); run = []; };
  let text = "", last = null;
  for (const m of msgs) {
    last = m;
    for (const p of m.parts) {
      if (p.type === "reasoning") { if (p.text && p.text.trim()) run.push(p); continue; }
      if (p.type === "tool") {
        if (p.tool === "todowrite") { flush(); out.push(todoHtml(p)); continue; }
        if (p.tool === "question" && (p.state || {}).status !== "completed" && (p.state || {}).status !== "error") continue;
        run.push(p); continue;
      }
      if (p.type === "text" && !p.synthetic && !p.ignored && p.text) { flush(); out.push(`<div class="md">${md(p.text)}</div>`); text += (text ? "\n\n" : "") + p.text; continue; }
      if (p.type === "file") { flush(); out.push(attHtml([p])); continue; }
      if (p.type === "compaction") { flush(); out.push(`<div class="cdiv">Earlier messages summarized</div>`); continue; }
      if (p.type === "agent" || p.type === "subtask") { run.push({ ...p, type: "tool", tool: "task", state: { status: "completed", input: { description: p.description || p.name || p.agent } } }); continue; }
    }
    const e = m.info.error;
    if (e) { flush(); out.push(e.name === "MessageAbortedError" ? `<div class="cnote">Stopped</div>` : `<div class="cerr">${esc(errText(e))}</div>`); }
  }
  flush();
  if (isLast && busy) {
    const st = C.status[C.id];
    if (st && st.type === "retry") out.push(`<div class="cretry">Retrying${st.attempt ? ` (attempt ${st.attempt})` : ""}: ${esc(st.message || "")}</div>`);
    out.push(`<div class="spark" aria-label="Working">✻</div>`);
  } else if (text && last) {
    const mi = last.info;
    out.push(`<div class="cacts"><button data-copyturn>Copy</button>${isLast ? `<button data-retry>Retry</button>` : ""}<small>${esc(modelName(mi.providerID, mi.modelID))}</small></div>`);
  }
  return out.join("");
}
function busy() { const s = C.status[C.id]; return !!s && s.type !== "idle"; }
let raf = 0;
function render(now) { if (now) { cancelAnimationFrame(raf); raf = 0; draw(); return; } if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); }
function draw() {
  const sc = $("cscroll"), near = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 120;
  const msgs = shown();                              // without what /undo reverted
  const html = [];
  let turn = [], turnUser = null;
  const b = busy(), turns = [];
  for (const m of msgs) {
    if (m.info.role === "user") { if (turnUser || turn.length) turns.push([turnUser, turn]); turnUser = m; turn = []; }
    else turn.push(m);
  }
  if (turnUser || turn.length) turns.push([turnUser, turn]);
  turns.forEach(([u, t], k) => {
    const isLast = k === turns.length - 1;
    if (u) html.push(userHtml(u));
    if (t.length || (isLast && b && !C.pending)) html.push(`<div class="ca" data-turn="${k}">${turnHtml(t, isLast && !C.pending, b)}</div>`);
  });
  if (C.pending) {
    html.push(`<div class="cu pending"><div>${attHtml(C.pending.parts)}${C.pending.text ? `<div class="bub">${esc(C.pending.text)}</div>` : ""}</div></div><div class="ca"><div class="spark">✻</div></div>`);
  }
  if (C.err) html.push(`<div class="cerr">${esc(C.err)}</div>`);
  const empty = !html.length;
  $("cempty").hidden = !empty;
  $("cmsgs").innerHTML = html.join("");
  $("cmsgs").dataset.turns = turns.length;
  if (near) sc.scrollTop = sc.scrollHeight;
  drawPending(); drawComposer(); onScroll();
}
function scrollEnd() { const sc = $("cscroll"); sc.scrollTop = sc.scrollHeight; onScroll(); }
function onScroll() { const sc = $("cscroll"); $("cdown").hidden = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 200; }

// ---------- permissions and questions ----------
function drawPending() {
  const out = [];
  for (const p of C.perms.values()) {
    if (p.sessionID !== C.id) continue;
    const pats = (p.patterns || []).filter(Boolean);
    const what = p.metadata && (p.metadata.command || p.metadata.filepath || p.metadata.filePath) || pats.join("\n");
    out.push(`<div class="card"><h4>Allow ${esc(p.permission)}?</h4>${what ? `<code>${esc(cut(what, 1500))}</code>` : ""}<div class="btns"><button data-perm="${esc(p.id)}" data-r="reject">Deny</button>${(p.always || []).length ? `<button data-perm="${esc(p.id)}" data-r="always">Always allow</button>` : ""}<button class="pri" data-perm="${esc(p.id)}" data-r="once">Allow once</button></div></div>`);
  }
  for (const q of C.questions.values()) {
    if (q.sessionID !== C.id) continue;
    q.pick = q.pick || q.questions.map(() => []);
    out.push(`<div class="card" data-q="${esc(q.id)}">${q.questions.map((x, n) => `<h4>${esc(x.header || "Question")}</h4><div>${esc(x.question)}</div>${(x.options || []).map(o => `<button class="opt${q.pick[n].includes(o.label) ? " on" : ""}" data-qn="${n}" data-ql="${esc(o.label)}">${esc(o.label)}${o.description ? `<small>${esc(o.description)}</small>` : ""}</button>`).join("")}${x.custom !== false ? `<input data-qc="${n}" placeholder="Something else…">` : ""}`).join("")}<div class="btns"><button data-qskip="${esc(q.id)}">Skip</button><button class="pri" data-qsend="${esc(q.id)}">Send</button></div></div>`);
  }
  $("cpend").innerHTML = out.join("");
}
async function loadPending() {
  try { for (const p of await api("/permission") || []) C.perms.set(p.id, p); } catch (e) {}
  try { for (const q of await api("/question") || []) C.questions.set(q.id, q); } catch (e) {}
  drawPending();
}

// ---------- composer ----------
function agentList() { return C.agents.filter(a => a.mode !== "subagent" && !a.hidden); }
function currentModel() {
  if (C.model) return C.model;
  const users = C.order.map(id => C.msgs.get(id)).filter(m => m && m.info.sessionID === C.id && m.info.role === "user" && m.info.model);
  return users.length ? users[users.length - 1].info.model : null;
}
function drawComposer() {
  const m = currentModel(), ag = C.agent || (agentList()[0] || {}).name || "build";
  $("cmodel").textContent = (m ? modelName(m.providerID, m.modelID) : "Default model") + " ▾";
  $("cagent").textContent = ag.charAt(0).toUpperCase() + ag.slice(1);
  $("cagent").hidden = agentList().length < 2;
  const has = $("cinput").value.trim() || C.files.length, stop = busy() && !has;
  $("csend").innerHTML = stop ? STOP : SEND;
  $("csend").setAttribute("aria-label", stop ? "Stop" : "Send");
  $("csend").disabled = !stop && !has;
  $("cfiles").innerHTML = C.files.map((f, i) => `<span class="chip">${/^image\//.test(f.mime) ? `<img src="${esc(f.url)}" alt="">` : "📎"} ${esc(f.filename)}<button data-unfile="${i}" aria-label="Remove">×</button></span>`).join("");
  const comp = $("ccomp"); $("opencode").style.setProperty("--compH", comp.offsetHeight + "px");
}
function grow() { const t = $("cinput"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, innerHeight * .38) + "px"; drawComposer(); }
async function send() {
  if (busy() && !$("cinput").value.trim() && !C.files.length) { try { await api(`/session/${C.id}/abort`, { method: "POST", body: {} }); } catch (e) { toast("Couldn’t stop: " + e.message); } return; }
  const text = $("cinput").value.trim(), files = C.files.slice();
  if (!text && !files.length) return;
  const cmd = slashCommand(text);
  if (cmd && cmd.builtin) { $("cinput").value = ""; grow(); runBuiltin(cmd.builtin); return; }
  const parts = [];
  if (text) parts.push({ type: "text", text });
  for (const f of files) parts.push({ type: "file", mime: f.mime, url: f.url, filename: f.filename });
  C.pending = { text, parts: files.map(f => ({ type: "file", ...f })) };
  $("cinput").value = ""; C.files = []; grow(); render(true); scrollEnd();
  try {
    if (!C.id) { const s = await api("/session", { method: "POST", body: {}, dir: C.dir }); addSession(s); select(s.id, true); C.pending = { text, parts: files.map(f => ({ type: "file", ...f })) }; render(true); }
    const m = currentModel();
    C.status[C.id] = { type: "busy" };
    if (cmd) {                                       // an OpenCode command or skill: OpenCode expands its template and runs it
      const body = { command: cmd.command, arguments: cmd.arguments, parts: parts.filter(p => p.type === "file") };
      if (m) body.model = m.providerID + "/" + m.modelID;
      if (C.agent) body.agent = C.agent;
      api(`/session/${C.id}/command`, { method: "POST", body }).catch(e => { C.pending = null; C.status[C.id] = { type: "idle" }; toast("Command failed: " + e.message); render(true); });
      return;
    }
    const body = { parts };
    if (m) body.model = { providerID: m.providerID, modelID: m.modelID };
    if (C.agent) body.agent = C.agent;
    await api(`/session/${C.id}/prompt_async`, { method: "POST", body });
  } catch (e) {
    C.pending = null; C.status[C.id] = { type: "idle" };
    $("cinput").value = text; C.files = files; grow();
    toast("Couldn’t send: " + e.message); render(true);
  }
}
function attach(list) {
  for (const f of list) {
    if (f.size > 25e6) { toast(f.name + " is too large (25 MB at most)"); continue; }
    const r = new FileReader();
    r.onload = () => { C.files.push({ filename: f.name, mime: f.type || "application/octet-stream", url: r.result }); drawComposer(); };
    r.readAsDataURL(f);
  }
}

// ---------- sessions and the drawer ----------
function addSession(s) {
  if (!s || !s.id || s.parentID) return;
  const i = C.sessions.findIndex(x => x.id === s.id);
  if (i < 0) C.sessions.push(s); else C.sessions[i] = s;
  C.sessions.sort((a, b) => ((b.time || {}).updated || 0) - ((a.time || {}).updated || 0));
}
function title() { const s = sess(C.id); return s ? (s.title && !/^New session - \d{4}-/.test(s.title) ? s.title : "New chat") : "New chat"; }
function titleChanged() { $("cttl").textContent = title(); $("cinput").placeholder = C.id ? "Reply to OpenCode…" : "Ask OpenCode anything…"; if (C.onTitle) C.onTitle(); }
async function loadSessions() {
  try { const list = await api("/session?roots=true&limit=200", { dir: C.dir }); C.sessions = []; for (const s of list || []) addSession(s); titleChanged(); drawList(); }
  catch (e) { $("clist").innerHTML = `<div class="cstat">Couldn’t load chats.<br>${esc(e.message)}</div>`; }
}
function day(ms) { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); }
function drawList() {
  const q = C.search.trim().toLowerCase();
  const list = C.sessions.filter(s => !q || (s.title || "").toLowerCase().includes(q));
  if (!list.length) { $("clist").innerHTML = `<div class="cstat">${q ? "No chats match." : "No chats yet."}</div>`; return; }
  const today = day(Date.now()), groups = [];
  for (const s of list) {
    const t = (s.time || {}).updated || 0, d = (today - day(t)) / 864e5;
    const g = d < 1 ? "Today" : d < 2 ? "Yesterday" : d < 7 ? "Previous 7 days" : d < 30 ? "Previous 30 days" : new Date(t).toLocaleDateString([], { month: "long", year: "numeric" });
    if (!groups.length || groups[groups.length - 1][0] !== g) groups.push([g, []]);
    groups[groups.length - 1][1].push(s);
  }
  $("clist").innerHTML = groups.map(([g, ss]) => `<h5>${esc(g)}</h5>` + ss.map(s => {
    const st = C.status[s.id], t = s.title && !/^New session - \d{4}-/.test(s.title) ? s.title : "New chat";
    return `<button class="citem${s.id === C.id ? " on" : ""}" data-sid="${esc(s.id)}"><span>${esc(t)}</span>${st && st.type !== "idle" ? '<span class="busy spin"></span>' : ""}</button>`;
  }).join("")).join("");
}
function menu(open) {
  const o = $("opencode");
  if (open === undefined) open = !o.classList.contains("drawer");
  sheet(false);
  o.classList.toggle("drawer", open);
  $("cdrawer").setAttribute("aria-hidden", !open);
  if (open) { $("csearch").value = C.search = ""; drawList(); loadSessions(); }
}
function select(id, keepDraft) {
  if (id === C.id && C.msgs.size) { menu(false); return; }
  C.id = id || ""; C.msgs = new Map(); C.order = []; C.pending = null; C.err = ""; C.open = new Set();
  store.set("session", C.id);
  if (!keepDraft) { C.files = []; }
  titleChanged(); menu(false); render(true);
  if (C.id) loadMessages(C.id);
}
function newChat() { select("", false); setTimeout(() => $("cinput").focus(), 50); }

// ---------- bottom sheet ----------
function sheet(html) {
  const o = $("opencode");
  if (html === false) { o.classList.remove("sheet"); $("csheet").setAttribute("aria-hidden", "true"); return; }
  $("csheet").innerHTML = `<div class="grab"></div>${html}`;
  o.classList.add("sheet"); $("csheet").setAttribute("aria-hidden", "false");
}
function modelSheet() {
  const cur = currentModel(), q = (sheet.q || "").toLowerCase();
  const groups = C.providers.map(p => [p, Object.entries(p.models || {}).filter(([id, m]) => !q || (m.name || id).toLowerCase().includes(q) || p.name.toLowerCase().includes(q))
    .sort((a, b) => (a[1].name || a[0]).localeCompare(b[1].name || b[0]))]).filter(g => g[1].length);
  const body = groups.map(([p, ms]) => `<h5>${esc(p.name || p.id)}</h5>` + ms.map(([id, m]) => `<button data-model="${esc(p.id)}\u0001${esc(id)}" class="${cur && cur.providerID === p.id && cur.modelID === id ? "on" : ""}"><span>${esc(m.name || id)}</span></button>`).join("")).join("");
  return `<h4>Model</h4><input id="cmq" placeholder="Search models" value="${esc(sheet.q || "")}"><div class="opts"><button data-model="" class="${!C.model ? "on" : ""}"><span>Default <small>(the chat’s last model, else OpenCode’s)</small></span></button>${body || `<div class="cstat">No models.</div>`}</div>`;
}
function sessionSheet(id) {
  const s = sess(id); if (!s) return;
  sheet(`<h4>${esc(s.title || "Chat")}</h4><div class="opts"><button data-sact="rename" data-sid2="${esc(id)}">Rename</button><button data-sact="web" data-sid2="${esc(id)}">Open in OpenCode web</button><button class="danger" data-sact="delete" data-sid2="${esc(id)}">Delete</button></div>`);
}
async function sessionAction(act, id) {
  const s = sess(id); if (!s) return;
  sheet(false);
  if (act === "rename") {
    const t = prompt("Rename chat", s.title || ""); if (t === null || !t.trim()) return;
    try { addSession(await api(`/session/${id}`, { method: "PATCH", body: { title: t.trim() }, dir: s.directory })); titleChanged(); drawList(); } catch (e) { toast("Couldn’t rename: " + e.message); }
  } else if (act === "delete") {
    if (!confirm(`Delete “${s.title || "this chat"}”? This can’t be undone.`)) return;
    try { await api(`/session/${id}`, { method: "DELETE", dir: s.directory }); C.sessions = C.sessions.filter(x => x.id !== id); if (id === C.id) select("", false); drawList(); } catch (e) { toast("Couldn’t delete: " + e.message); }
  } else if (act === "web") openFull(s);
}
function b64url(s) { let bin = ""; for (const b of new TextEncoder().encode(s)) bin += String.fromCharCode(b); return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
function openFull(s) {
  menu(false); sheet(false);
  const f = $("cframe"), src = s ? `/oc/${b64url(s.directory || C.dir)}/session/${s.id}` : "/oc/";
  if (f.dataset.src !== src) { f.dataset.src = src; f.src = src; }
  $("cfull").hidden = false; C.full = true;
}

// ---------- usage ----------
// OpenCode reports no plan limits, so this adds up what it records per reply (tokens, cost) and shows the last limit notice it got.
function tokens(t) { if (!t) return 0; const c = t.cache || {}; return t.total || (t.input || 0) + (t.output || 0) + (t.reasoning || 0) + (c.read || 0) + (c.write || 0); }
const num = n => n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k" : String(Math.round(n));
const money = c => !c ? "$0" : "$" + (c < 0.01 ? c.toFixed(4) : c.toFixed(2));
function ctxLimit(providerID, modelID) { const p = C.providers.find(x => x.id === providerID), m = p && p.models && p.models[modelID]; return (m && m.limit && m.limit.context) || 0; }
function resetAt(text, at) {
  const m = String(text || "").match(/resets? in ((?:\d+\s*(?:days?|hours?|minutes?|mins?|seconds?|secs?)[\s,]*(?:and\s*)?)+)/i);
  if (!m) return 0;
  let ms = 0;
  for (const [, n, u] of m[1].matchAll(/(\d+)\s*(day|hour|min|sec)/gi)) ms += +n * { day: 864e5, hour: 36e5, min: 6e4, sec: 1e3 }[u.toLowerCase()];
  return at + ms;
}
const LIMIT = /limit|quota|rate.?limit|insufficient|balance|too many requests/i;
function when(ms) { const d = new Date(ms), today = day(Date.now()) === day(ms); return (today ? "" : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " ") + d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
async function usage() {
  sheet(`<h4>Usage</h4><div class="opts usage"><div class="cstat">Adding up…</div></div>`);
  const now = Date.now(), since = now - 7 * 864e5, W = { h5: now - 5 * 36e5, today: day(now), d7: since };
  let all = [];
  try { all = (await api("/session?limit=500", { dir: C.dir }) || []).filter(s => ((s.time || {}).updated || 0) >= since); } catch (e) {}
  const lists = await Promise.all(all.map(s => api(`/session/${s.id}/message`, { dir: s.directory }).catch(() => [])));
  const sum = { h5: { n: 0, t: 0, c: 0 }, today: { n: 0, t: 0, c: 0 }, d7: { n: 0, t: 0, c: 0 } }, models = {};
  let notice = null;
  for (const list of lists) for (const { info } of list || []) {
    if (info.role !== "assistant") continue;
    const at = (info.time || {}).created || 0, t = tokens(info.tokens), c = info.cost || 0;
    for (const k in W) if (at >= W[k]) { sum[k].n++; sum[k].t += t; sum[k].c += c; }
    if (at >= since) { const key = modelName(info.providerID, info.modelID) || info.modelID || "?"; const m = models[key] = models[key] || { t: 0, c: 0, n: 0 }; m.t += t; m.c += c; m.n++; }
    const text = errText(info.error);
    if (text && LIMIT.test(text) && (!notice || at > notice.at)) notice = { at: (info.time || {}).completed || at, text, model: modelName(info.providerID, info.modelID) };
  }
  for (const [sid, st] of Object.entries(C.status)) if (st && st.type === "retry" && LIMIT.test(st.message || "")) notice = { at: now, text: st.message, model: "", live: true };
  // this chat, from what is loaded
  const mine = C.order.map(id => C.msgs.get(id)).filter(m => m && m.info.sessionID === C.id && m.info.role === "assistant");
  const tot = { i: 0, o: 0, r: 0, cr: 0, c: 0 };
  for (const { info } of mine) { const k = info.tokens || {}; tot.i += k.input || 0; tot.o += k.output || 0; tot.r += k.reasoning || 0; tot.cr += (k.cache || {}).read || 0; tot.c += info.cost || 0; }
  const lastTok = [...mine].reverse().find(m => tokens(m.info.tokens) > 0);
  const out = [];
  if (notice) {
    const reset = resetAt(notice.text, notice.at), past = reset && reset < now;
    out.push(`<div class="ulimit"><b>${notice.live ? "Waiting on a limit now" : past ? "Last limit notice" : "Limit reached"}</b>${notice.model ? ` · ${esc(notice.model)}` : ""} · ${esc(when(notice.at))}<div>${esc(cut(notice.text, 300))}</div>${reset ? `<div class="ureset">${past ? "Reset" : "Resets around"} ${esc(when(reset))}</div>` : ""}</div>`);
  }
  if (C.id && mine.length) {
    out.push(`<h5>This chat</h5>`);
    if (lastTok) {
      const used = tokens(lastTok.info.tokens), lim = ctxLimit(lastTok.info.providerID, lastTok.info.modelID), pct = lim ? Math.min(100, used / lim * 100) : 0;
      out.push(`<div class="urow"><span>Context</span><b>${num(used)}${lim ? ` of ${num(lim)} · ${pct.toFixed(0)}%` : ""}</b></div>${lim ? `<div class="ubar"><i style="width:${pct.toFixed(1)}%"></i></div>` : ""}`);
    }
    out.push(`<div class="urow"><span>Input · cached</span><b>${num(tot.i)} · ${num(tot.cr)}</b></div><div class="urow"><span>Output · reasoning</span><b>${num(tot.o)} · ${num(tot.r)}</b></div><div class="urow"><span>Cost</span><b>${money(tot.c)}</b></div>`);
  }
  out.push(`<h5>All chats</h5><table class="utab"><tr><th></th><th>5 hours</th><th>Today</th><th>7 days</th></tr>
    <tr><td>Replies</td>${["h5", "today", "d7"].map(k => `<td>${sum[k].n}</td>`).join("")}</tr>
    <tr><td>Tokens</td>${["h5", "today", "d7"].map(k => `<td>${num(sum[k].t)}</td>`).join("")}</tr>
    <tr><td>Cost</td>${["h5", "today", "d7"].map(k => `<td>${money(sum[k].c)}</td>`).join("")}</tr></table>`);
  const top = Object.entries(models).sort((a, b) => b[1].t - a[1].t).slice(0, 8);
  if (top.length) out.push(`<h5>By model, 7 days</h5>` + top.map(([k, m]) => `<div class="urow"><span>${esc(k)}</span><b>${num(m.t)} · ${money(m.c)}</b></div>`).join(""));
  out.push(`<p class="cnote">Added up from OpenCode’s records on the PC (${all.length} chat${all.length === 1 ? "" : "s"} active in the last 7 days, subagents included). OpenCode doesn’t report plan limits${notice ? ", so the notice above is the last one a reply ran into" : "; when a reply runs into one, it shows here"}.</p>`);
  const box = $("csheet").querySelector(".usage");
  if (box) box.innerHTML = out.join("");
}

// ---------- slash commands ----------
// Typing "/" at the start of the box opens a menu that narrows as you type. Built-ins are done here, as OpenCode's own app does;
// the rest (OpenCode commands such as /review, and skills) are run by OpenCode through POST /session/:id/command.
const BUILTIN = [
  { name: "new", description: "Start a new chat", run: () => newChat() },
  { name: "model", description: "Choose the model", run: () => { sheet.q = ""; sheet(modelSheet()); } },
  { name: "agent", description: "Switch agent (Build / Plan)", run: () => $("cagent").click() },
  { name: "compact", description: "Summarize the chat to free up context", chat: true, run: compact },
  { name: "undo", description: "Undo the last message and its changes", chat: true, run: undo },
  { name: "redo", description: "Bring back what was undone", chat: true, run: redo },
  { name: "fork", description: "Copy this chat into a new one", chat: true, run: fork },
  { name: "rename", description: "Rename this chat", chat: true, run: () => sessionAction("rename", C.id) },
  { name: "usage", description: "Tokens, cost and limit notices", run: () => usage() },
  { name: "reload", description: "Pick up new or changed skills, commands and config", run: reload },
];
function commands() {
  return BUILTIN.map(c => ({ ...c, kind: "" })).concat(C.commands.filter(c => !BUILTIN.some(b => b.name === c.name))
    .map(c => ({ name: c.name, description: c.description || "", kind: c.source === "skill" ? "skill" : "command", args: true })));
}
function loadCommands() { return api("/command", { dir: C.dir }).then(c => { C.commands = c || []; render(); }).catch(() => {}); }
// OpenCode reads skills and commands once per directory; a new skill shows only after its instance is reloaded, which ends running replies
async function reload() {
  const running = Object.entries(C.status).filter(([, st]) => st && st.type !== "idle");
  if (running.length) { toast("A chat is still working; reload when it’s done"); return; }
  try { await api("/instance/dispose", { method: "POST", body: {}, dir: C.dir }); await loadCommands(); toast(`Reloaded: ${C.commands.filter(c => c.source === "skill").length} skills, ${C.commands.filter(c => c.source !== "skill").length} commands`); }
  catch (e) { toast("Couldn’t reload: " + e.message); }
}
function slashQuery() { const v = $("cinput").value, m = v.match(/^\/(\S*)$/); return m ? m[1].toLowerCase() : null; }
function slashMatches(q) {
  const all = commands(), pre = all.filter(c => c.name.toLowerCase().startsWith(q)), mid = all.filter(c => !c.name.toLowerCase().startsWith(q) && (c.name.toLowerCase().includes(q) || (q.length > 2 && c.description.toLowerCase().includes(q))));
  return pre.concat(mid).slice(0, 40);
}
function drawSlash() {
  const q = slashQuery(), box = $("cmenu");
  if (q === null) { box.hidden = true; C.slash = null; return; }
  const list = slashMatches(q);
  if (!list.length) { box.hidden = true; C.slash = null; return; }
  C.slash = { list, sel: Math.min(C.slash ? C.slash.sel : 0, list.length - 1) };
  box.innerHTML = list.map((c, i) => `<button data-slash="${i}" class="${i === C.slash.sel ? "on" : ""}"><b>/${esc(c.name)}</b>${c.kind ? `<i>${c.kind}</i>` : ""}<span>${esc(c.description.split("\n")[0].slice(0, 120))}</span></button>`).join("");
  box.hidden = false;
  const on = box.querySelector(".on"); if (on) on.scrollIntoView({ block: "nearest" });
}
function pickSlash(i) {
  const c = C.slash && C.slash.list[i]; if (!c) return;
  if (c.args) { $("cinput").value = "/" + c.name + " "; grow(); $("cinput").focus(); return; }       // OpenCode commands take arguments: let them be typed
  $("cinput").value = ""; grow(); runBuiltin(c);
}
function runBuiltin(c) {
  if (c.chat && !C.id) { toast("Start a chat first"); return; }
  c.run();
}
function lastModel() {
  const m = currentModel(); if (m) return m;
  const a = [...C.order].reverse().map(id => C.msgs.get(id)).find(x => x && x.info.sessionID === C.id && x.info.role === "assistant" && x.info.providerID);
  return a ? { providerID: a.info.providerID, modelID: a.info.modelID } : null;
}
async function compact() {
  const m = lastModel(); if (!m) { toast("Choose a model first"); return; }
  try { C.status[C.id] = { type: "busy" }; render(); await api(`/session/${C.id}/summarize`, { method: "POST", body: { providerID: m.providerID, modelID: m.modelID } }); }
  catch (e) { C.status[C.id] = { type: "idle" }; toast("Couldn’t compact: " + e.message); render(); }
}
function shown() { const s = sess(C.id), cut = s && s.revert && s.revert.messageID; return C.order.map(id => C.msgs.get(id)).filter(m => m && m.info.sessionID === C.id && (!cut || m.info.id < cut)); }
async function undo() {
  if (busy()) { toast("Stop the reply first"); return; }
  const u = shown().filter(m => m.info.role === "user").pop(); if (!u) { toast("Nothing to undo"); return; }
  try {
    addSession(await api(`/session/${C.id}/revert`, { method: "POST", body: { messageID: u.info.id } }));
    $("cinput").value = u.parts.filter(p => p.type === "text" && !p.synthetic).map(p => p.text).join("\n\n"); grow(); render(true);
  } catch (e) { toast("Couldn’t undo: " + e.message); }
}
async function redo() {
  const s = sess(C.id); if (!s || !s.revert) { toast("Nothing to redo"); return; }
  try { addSession(await api(`/session/${C.id}/unrevert`, { method: "POST", body: {} })); render(true); } catch (e) { toast("Couldn’t redo: " + e.message); }
}
async function fork() {
  try { const s = await api(`/session/${C.id}/fork`, { method: "POST", body: {} }); addSession(s); select(s.id); toast("Forked into a new chat"); } catch (e) { toast("Couldn’t fork: " + e.message); }
}
// "/name args": a built-in runs here, an OpenCode command or skill is sent to OpenCode; anything else is an ordinary message
function slashCommand(text) {
  const m = text.match(/^\/(\S+)(?:\s+([\s\S]*))?$/); if (!m) return null;
  const b = BUILTIN.find(c => c.name === m[1]); if (b) return { builtin: b };
  const c = C.commands.find(x => x.name === m[1]); return c ? { command: c.name, arguments: (m[2] || "").trim() } : null;
}

// ---------- live events ----------
function onEvent(ev) {
  const t = ev.type, p = ev.properties || {};
  const sid = p.sessionID || (p.info && p.info.sessionID) || (p.part && p.part.sessionID);
  switch (t) {
    case "session.created": case "session.updated":
      if (p.info) { addSession(p.info); if (p.info.id === C.id) { titleChanged(); render(); } if ($("opencode").classList.contains("drawer")) drawList(); }
      return;
    case "session.deleted":
      if (p.info) { C.sessions = C.sessions.filter(s => s.id !== p.info.id); drawList(); }
      return;
    case "session.status": C.status[sid] = p.status; break;
    case "session.idle": C.status[sid] = { type: "idle" }; break;
    case "session.error":
      if (sid === C.id && p.error && p.error.name !== "MessageAbortedError" && !C.order.some(id => (C.msgs.get(id) || {}).info?.error)) toast(errText(p.error));
      break;
    case "message.updated":
      if (p.info && p.info.sessionID === C.id) { upsertInfo(p.info); if (p.info.role === "user") C.pending = null; }
      break;
    case "message.removed":
      if (sid === C.id && C.msgs.delete(p.messageID)) C.order = C.order.filter(id => id !== p.messageID);
      break;
    case "message.part.updated":
      if (p.part && p.part.sessionID === C.id) upsertPart(p.part);
      break;
    case "message.part.delta": {
      if (sid !== C.id) return;
      const m = C.msgs.get(p.messageID), part = m && m.parts.find(x => x.id === p.partID);
      if (part) part[p.field] = (part[p.field] || "") + p.delta;
      break;
    }
    case "message.part.removed": {
      const m = C.msgs.get(p.messageID); if (m) m.parts = m.parts.filter(x => x.id !== p.partID);
      break;
    }
    case "permission.asked": case "permission.v2.asked": C.perms.set(p.id, p); if (p.sessionID !== C.id) toast("A chat is waiting for your permission"); break;
    case "permission.replied": case "permission.v2.replied": C.perms.delete(p.requestID || p.permissionID || p.id); break;
    case "question.asked": case "question.v2.asked": C.questions.set(p.id, p); break;
    case "question.replied": case "question.rejected": case "question.v2.replied": case "question.v2.rejected": C.questions.delete(p.requestID || p.id); break;
    default: return;
  }
  if (sid && sid !== C.id && (t === "session.status" || t === "session.idle")) { if ($("opencode").classList.contains("drawer")) drawList(); return; }
  render();
}
function connect() {
  if (C.ws || document.hidden || !C.started) return;
  const ws = new WebSocket((location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + "/oc-stream/global/event");
  C.ws = ws;
  let buf = "";
  ws.onopen = () => {
    C.wsTry = 0; C.connected = true; $("cconn").hidden = true;
    if (C.id) loadMessages(C.id);
    api("/session/status").then(s => { if (s) { C.status = s; render(); } }).catch(() => {});
    loadPending();
  };
  ws.onmessage = e => {
    buf += e.data.replace(/\r\n?/g, "\n");
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      const data = block.split("\n").filter(l => l.startsWith("data:")).map(l => l.slice(5).replace(/^ /, "")).join("\n");
      if (!data) continue;
      try { const m = JSON.parse(data); onEvent(m.payload || m); } catch (x) {}
    }
  };
  ws.onclose = () => {
    if (C.ws !== ws) return;
    C.ws = null; C.connected = false;
    if (document.hidden) return;
    $("cconn").hidden = false; $("cconn").textContent = "Reconnecting to OpenCode…";
    setTimeout(connect, Math.min(20000, 800 * 2 ** C.wsTry++));
  };
}
function disconnect() { const ws = C.ws; C.ws = null; if (ws) try { ws.close(); } catch (e) {} }

// ---------- wiring ----------
function wire() {
  $("cburger").onclick = () => menu();
  $("cnewtop").onclick = newChat;
  $("cusage").onclick = usage;
  $("cttl").onclick = () => { if (C.id) sessionSheet(C.id); };
  $("cnew").onclick = newChat;
  $("cshade").onclick = () => { menu(false); sheet(false); };
  $("csearch").oninput = e => { C.search = e.target.value; drawList(); };
  $("cfullweb").onclick = () => openFull(sess(C.id));
  $("cfullclose").onclick = () => { $("cfull").hidden = true; C.full = false; };
  $("cdown").onclick = () => $("cscroll").scrollTo({ top: $("cscroll").scrollHeight, behavior: "smooth" });
  $("cscroll").addEventListener("scroll", onScroll, { passive: true });
  const inp = $("cinput");
  inp.addEventListener("input", grow);
  inp.addEventListener("input", () => { if ($("cinput").value === "/") loadCommands().then(drawSlash); drawSlash(); });   // fresh list each time the menu opens
  inp.addEventListener("keydown", e => {
    if (C.slash && !$("cmenu").hidden) {               // a hardware keyboard can walk the command menu
      const n = C.slash.list.length;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); C.slash.sel = (C.slash.sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; drawSlash(); return; }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); pickSlash(C.slash.sel); drawSlash(); return; }
      if (e.key === "Escape") { e.preventDefault(); $("cmenu").hidden = true; C.slash = null; return; }
    }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
  });
  $("cmenu").addEventListener("pointerdown", e => e.preventDefault());       // keep the keyboard up while tapping a command
  $("cmenu").onclick = e => { const b = e.target.closest("[data-slash]"); if (b) { pickSlash(+b.dataset.slash); drawSlash(); } };
  inp.addEventListener("focus", () => document.body.classList.add("typing"));
  inp.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== inp) document.body.classList.remove("typing"); }, 150));
  $("csend").onclick = send;
  $("cplus").onclick = () => $("cpick").click();
  $("cpick").onchange = e => { attach([...e.target.files]); e.target.value = ""; };
  $("cagent").onclick = () => { const l = agentList(); if (l.length < 2) return; const i = l.findIndex(a => a.name === (C.agent || l[0].name)); C.agent = l[(i + 1) % l.length].name; store.set("agent", C.agent); drawComposer(); toast((C.agent === "plan" ? "Plan: reads and plans, no edits" : C.agent.charAt(0).toUpperCase() + C.agent.slice(1) + " agent")); };
  $("cmodel").onclick = () => { sheet.q = ""; sheet(modelSheet()); };
  // long-press a chat in the drawer for rename / delete
  let lp = null, lpFired = false;
  $("clist").addEventListener("pointerdown", e => { const b = e.target.closest("[data-sid]"); lpFired = false; if (!b) return; lp = setTimeout(() => { lpFired = true; sessionSheet(b.dataset.sid); }, 520); });
  ["pointerup", "pointercancel", "pointerleave"].forEach(n => $("clist").addEventListener(n, () => clearTimeout(lp)));
  $("clist").addEventListener("scroll", () => clearTimeout(lp), { passive: true });
  $("clist").addEventListener("contextmenu", e => e.preventDefault());
  $("clist").onclick = e => { const b = e.target.closest("[data-sid]"); if (b && !lpFired) select(b.dataset.sid); };
  $("opencode").addEventListener("click", async e => {
    const t = e.target;
    const step = t.closest("[data-step]");
    if (step) { const k = step.dataset.step; C.open.has(k) ? C.open.delete(k) : C.open.add(k); render(true); return; }
    if (t.closest("[data-copy]")) { copy(t.closest(".code").querySelector("code").textContent); return; }
    if (t.closest("[data-copyturn]")) { const ca = t.closest(".ca"); copy([...ca.querySelectorAll(".md")].map(x => x.innerText).join("\n\n")); return; }
    if (t.closest("[data-retry]")) {
      const users = C.order.map(id => C.msgs.get(id)).filter(m => m && m.info.sessionID === C.id && m.info.role === "user");
      const u = users[users.length - 1]; if (!u) return;
      if (busy()) return;
      $("cinput").value = u.parts.filter(p => p.type === "text" && !p.synthetic).map(p => p.text).join("\n\n");     // send the last message again
      C.files = u.parts.filter(p => p.type === "file" && /^data:/.test(p.url)).map(p => ({ filename: p.filename || "file", mime: p.mime, url: p.url }));
      grow(); send(); return;
    }
    const un = t.closest("[data-unfile]"); if (un) { C.files.splice(+un.dataset.unfile, 1); drawComposer(); return; }
    const pm = t.closest("[data-perm]");
    if (pm) { const id = pm.dataset.perm; try { await api(`/permission/${id}/reply`, { method: "POST", body: { reply: pm.dataset.r } }); C.perms.delete(id); drawPending(); } catch (x) { toast("Couldn’t answer: " + x.message); } return; }
    const opt = t.closest("[data-ql]");
    if (opt) { const q = C.questions.get(opt.closest("[data-q]").dataset.q), n = +opt.dataset.qn, l = opt.dataset.ql, info = q.questions[n];
      q.pick[n] = info.multiple ? (q.pick[n].includes(l) ? q.pick[n].filter(x => x !== l) : [...q.pick[n], l]) : [l]; drawPending(); return; }
    const qs = t.closest("[data-qsend]"), qk = t.closest("[data-qskip]");
    if (qs || qk) {
      const id = (qs || qk).dataset.qsend || (qk || {}).dataset?.qskip, q = C.questions.get(id), card = (qs || qk).closest("[data-q]");
      try {
        if (qk) await api(`/question/${id}/reject`, { method: "POST", body: {} });
        else { const answers = q.questions.map((x, n) => { const c = card.querySelector(`[data-qc="${n}"]`); const v = c && c.value.trim(); return v ? [...q.pick[n], v] : q.pick[n]; });
          await api(`/question/${id}/reply`, { method: "POST", body: { answers } }); }
        C.questions.delete(id); drawPending();
      } catch (x) { toast("Couldn’t answer: " + x.message); }
      return;
    }
    const md_ = t.closest("[data-model]");
    if (md_) { const v = md_.dataset.model; C.model = v ? { providerID: v.split("\u0001")[0], modelID: v.split("\u0001")[1] } : null; store.set("model", C.model); sheet(false); drawComposer(); return; }
    const sa = t.closest("[data-sact]"); if (sa) { sessionAction(sa.dataset.sact, sa.dataset.sid2); return; }
  });
  $("opencode").addEventListener("input", e => {          // model search: refresh the list only, so the field keeps focus and the keyboard
    if (e.target.id !== "cmq") return;
    sheet.q = e.target.value;
    const tmp = document.createElement("div"); tmp.innerHTML = modelSheet();
    $("csheet").querySelector(".opts").innerHTML = tmp.querySelector(".opts").innerHTML;
  });
  document.addEventListener("visibilitychange", () => { if (document.hidden) disconnect(); else if (C.started) connect(); });
  new ResizeObserver(() => drawComposer()).observe($("ccomp"));
}

// ---------- entry points for index.html ----------
async function start(state) {
  if (C.started || !state || !state.opencode_url) return;
  C.started = true; C.dir = state.opencode_directory || ""; C.url = state.opencode_url;
  $("cnone").hidden = true; $("cmain").hidden = false;
  wire();
  const fromUrl = (C.url.match(/\/session\/(ses_[A-Za-z0-9]+)/) || [])[1];
  C.id = store.get("session") ?? fromUrl ?? "";
  titleChanged(); render(true); drawComposer();
  connect();
  loadSessions();
  loadCommands();
  api("/agent", { dir: C.dir }).then(a => { C.agents = a || []; if (C.agent && !agentList().some(x => x.name === C.agent)) C.agent = ""; drawComposer(); }).catch(() => {});
  api("/config/providers", { dir: C.dir }).then(p => { C.providers = (p && p.providers) || []; render(); }).catch(() => {});
  if (C.id) loadMessages(C.id);
}
window.AVSChat = {
  start,
  menu: () => menu(),
  newChat,
  usage,
  title,
  onTitle(fn) { C.onTitle = fn; },
  hide() { menu(false); sheet(false); document.body.classList.remove("typing"); },
  back() {                                     // Android back: close the topmost thing; false when there was nothing to close
    const o = $("opencode");
    if (o.classList.contains("sheet")) { sheet(false); return true; }
    if (o.classList.contains("drawer")) { menu(false); return true; }
    if (C.full) { $("cfull").hidden = true; C.full = false; return true; }
    return false;
  },
  _md: md,
};
})();
