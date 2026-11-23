// AVS Codex tab: a phone-sized client for the Codex CLI on the PC. AVS runs `codex app-server` and relays it over one WebSocket (/codex-ws),
// which the Cloudflare tunnel passes through at once. Same look as the OpenCode chat (chat.css); no socket is kept while the app is hidden.
(function () {
"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = s => window.AVSChat._md(s);
const SEND = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
const STOP = '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>';
const store = { get(k) { try { return JSON.parse(localStorage.getItem("avs.codex." + k)); } catch (e) { return null; } },
                set(k, v) { try { localStorage.setItem("avs.codex." + k, JSON.stringify(v)); } catch (e) {} } };

const X = {
  started: false, dir: "", ws: null, tries: 0, calls: new Map(), n: 1, sessions: [], id: store.get("thread") || "", turns: [], items: new Map(),
  running: "", busy: new Set(), asks: new Map(), models: [], model: store.get("model") || "", err: "", search: "", open: new Set(), limits: null, tokens: null, skills: [], files: [], slash: null, onTitle: null,
};

// ---------- connection ----------
function connect() {
  if (X.ws || document.hidden) return;
  const ws = X.ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/codex-ws");
  ws.onopen = () => { X.tries = 0; conn(""); init(); };
  ws.onmessage = e => { let m; try { m = JSON.parse(e.data); } catch (x) { return; } incoming(m); };
  ws.onclose = () => {
    if (X.ws !== ws) return;
    X.ws = null;
    for (const c of X.calls.values()) c.reject(new Error("Lost the connection to your PC"));
    X.calls.clear();
    if (!document.hidden && X.started) { conn("Reconnecting…"); setTimeout(connect, Math.min(1000 * 2 ** X.tries++, 15000)); }
  };
}
function rpc(method, params) {
  return new Promise((resolve, reject) => {
    if (!X.ws || X.ws.readyState !== 1) return reject(new Error("Not connected to your PC"));
    const id = X.n++;
    X.calls.set(id, { resolve, reject });
    X.ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}
function conn(t) { const c = $("xconn"); c.textContent = t; c.hidden = !t; }
function toast(msg) { const t = $("xtoast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 2800); }
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { if (X.ws) { const w = X.ws; X.ws = null; w.close(); } } else if (X.started) connect();
});

// ---------- events ----------
function incoming(m) {
  if (m.id !== undefined && !m.method && !m.ask) {
    const c = X.calls.get(m.id);
    if (c) { X.calls.delete(m.id); m.error ? c.reject(new Error(m.error.message || "Failed")) : c.resolve(m.result); }
    return;
  }
  if (m.ask !== undefined) { X.asks.set(m.ask, m); drawAsks(); return; }
  const p = m.params || {};
  switch (m.method) {
    case "serverRequest/resolved": X.asks.delete(p.requestId); drawAsks(); break;
    case "avs/stopped": X.running = ""; X.asks.clear(); drawAsks(); drawMsgs(); drawComposer(); break;
    case "account/rateLimits/updated": X.limits = p.rateLimits; drawLimit(); break;
    case "thread/tokenUsage/updated": if (p.threadId === X.id) X.tokens = p.tokenUsage; break;
    case "thread/started": case "thread/name/updated": case "thread/archived": loadList(); break;
    case "thread/status/changed":
      p.status && p.status.type === "active" ? X.busy.add(p.threadId) : X.busy.delete(p.threadId);
      drawList(); break;
    case "turn/started": if (p.threadId === X.id) { upsertTurn(p.turn); X.running = p.turn.id; drawMsgs(); drawComposer(); } break;
    case "turn/completed":
      if (p.threadId !== X.id) break;
      upsertTurn(p.turn); X.running = "";
      if (p.turn.status === "failed" && p.turn.error) X.err = p.turn.error.message || "The turn failed";
      drawMsgs(); drawComposer(); loadList(); break;
    case "item/started": case "item/completed":
      if (p.threadId === X.id) { putItem(p.turnId, p.item); drawMsgs(); } break;
    case "item/agentMessage/delta": if (p.threadId === X.id) { const it = X.items.get(p.itemId); if (it) it.text = (it.text || "") + p.delta; else putItem(p.turnId, { type: "agentMessage", id: p.itemId, text: p.delta }); drawMsgs(); } break;
    case "item/commandExecution/outputDelta": { const it = X.items.get(p.itemId); if (it) { it.aggregatedOutput = (it.aggregatedOutput || "") + p.delta; if (X.open.has(it.id)) drawMsgs(); } break; }
    case "error": if (p.threadId === undefined || p.threadId === X.id) { X.err = (p.error && p.error.message) || p.message || "Codex reported an error"; drawMsgs(); } break;
  }
}
function upsertTurn(t) {
  const i = X.turns.findIndex(x => x.id === t.id);
  if (i < 0) X.turns.push({ ...t, items: [] });            // a copy: putItem fills its items, so reusing t.items would add each one twice
  else X.turns[i] = { ...X.turns[i], ...t, items: X.turns[i].items };
  for (const it of t.items || []) putItem(t.id, it);
}
function putItem(turnId, item) {
  let t = X.turns.find(x => x.id === turnId);
  if (!t) { t = { id: turnId, items: [] }; X.turns.push(t); }
  if (item.type === "userMessage" && !String(item.id).startsWith("local-")) {       // the real message replaces the one shown while sending
    X.turns = X.turns.filter(x => x.id !== "local"); for (const k of [...X.items.keys()]) if (k.startsWith("local-")) X.items.delete(k);
    t = X.turns.find(x => x.id === turnId) || t;
  }
  const old = X.items.get(item.id);
  if (old) { Object.assign(old, item); return; }
  X.items.set(item.id, item);
  t.items.push(item);
}

// ---------- threads ----------
async function init() {
  loadList(); loadSkills();
  rpc("model/list", {}).then(r => { X.models = (r.data || []).filter(m => !m.hidden); drawComposer(); }).catch(() => {});
  rpc("account/rateLimits/read", {}).then(r => { X.limits = (r && (r.rateLimits || r)) || null; drawLimit(); }).catch(() => {});
  if (X.id) openThread(X.id, true);
}
async function loadList() {
  try { X.sessions = (await rpc("thread/list", { limit: 60 })).data || []; drawList(); drawTitle(); } catch (e) { $("xlist").innerHTML = `<div class="cstat">${esc(e.message)}</div>`; }
}
async function openThread(id, quiet) {
  X.id = id; store.set("thread", id); X.tokens = null; X.turns = []; X.items.clear(); X.err = ""; X.running = ""; drawMsgs(); drawTitle();
  try {
    const [r] = await Promise.all([rpc("thread/read", { threadId: id, includeTurns: true }), rpc("thread/resume", { threadId: id, excludeTurns: true }).catch(() => {})]);
    if (X.id !== id) return;
    X.turns = []; X.items.clear();
    for (const t of (r.thread && r.thread.turns) || []) upsertTurn(t);
    const live = X.turns.find(t => t.status === "inProgress"); X.running = live ? live.id : "";
  } catch (e) { if (!quiet) toast(e.message); X.err = e.message; }
  drawMsgs(); drawComposer(); drawTitle(); scrollEnd(true);
}
function newChat() { X.tokens = null; X.id = ""; store.set("thread", ""); X.turns = []; X.items.clear(); X.err = ""; X.files = []; X.running = ""; menu(false); drawMsgs(); drawComposer(); drawTitle(); $("xinput").focus(); }
function sess(id) { return X.sessions.find(s => s.id === id); }
function title() { const s = sess(X.id); return (s && (s.name || s.preview)) || "New chat"; }
function drawTitle() { $("xttl").textContent = title(); if (X.onTitle) X.onTitle(); }

// ---------- sending ----------
async function send() {
  const box = $("xinput"), text = box.value.trim();
  if (!text && !X.files.length) return;
  const cmd = slashCommand(text);
  if (cmd) { box.value = ""; autosize(); return cmd.skill ? sendSkill(cmd.skill, cmd.args) : runBuiltin(cmd.builtin, cmd.args); }
  if (X.running && !X.files.length) { return steer(text); }
  const files = X.files; let input = [{ type: "text", text, text_elements: [] }], shown = [{ type: "text", text }];
  box.value = ""; X.files = []; autosize(); X.err = "";
  try {
    if (files.length) {                         // pictures go to Codex as images; other files are saved on the PC and named in the message
      const other = files.filter(f => !/^image\//.test(f.mime));
      let note = "";
      if (other.length) {
        const fd = new FormData(); for (const f of other) fd.append("files", f.blob, f.filename);
        const r = await fetch("/api/upload", { method: "POST", body: fd, credentials: "same-origin" });
        if (!r.ok) throw new Error("Couldn’t upload: " + (await r.text()).slice(0, 120));
        note = "\n\n[Attached files: " + (await r.json()).paths.join(", ") + "]";
      }
      const imgs = files.filter(f => /^image\//.test(f.mime)).map(f => ({ type: "image", url: f.url }));
      input = [{ type: "text", text: (text + note).trim(), text_elements: [] }, ...imgs]; shown = [...imgs, { type: "text", text: (text + note).trim() }];
    }
    if (X.running) { await rpc("turn/steer", { threadId: X.id, expectedTurnId: X.running, input }); drawComposer(); return; }
    if (!X.id) {
      const r = await rpc("thread/start", { approvalPolicy: "never", sandbox: "danger-full-access", ...(X.model ? { model: X.model } : {}) });
      X.id = r.thread.id; store.set("thread", X.id); loadList();
    }
    putItem("local", { type: "userMessage", id: "local-" + Date.now(), content: shown });
    drawMsgs(); scrollEnd(true);
    await rpc("turn/start", { threadId: X.id, approvalPolicy: "never", sandboxPolicy: { type: "dangerFullAccess" }, input, ...(X.model ? { model: X.model } : {}) });
  } catch (e) { box.value = text; X.files = files; autosize(); X.err = e.message; drawMsgs(); }
  drawComposer();
}
function attach(list) {
  for (const f of list) {
    if (f.size > 25e6) { toast(f.name + " is too large (25 MB at most)"); continue; }
    const r = new FileReader(), name = f.name && f.name !== "image.png" ? f.name : (f.type.startsWith("image/") ? "pasted-" + Date.now() + "." + (f.type.split("/")[1] || "png") : "file");
    r.onload = () => { X.files.push({ filename: name, mime: f.type || "application/octet-stream", url: r.result, blob: f }); drawComposer(); };
    r.readAsDataURL(f);
  }
}
async function steer(text) {
  try { await rpc("turn/steer", { threadId: X.id, expectedTurnId: X.running, input: [{ type: "text", text, text_elements: [] }] }); $("xinput").value = ""; autosize(); }
  catch (e) { toast(e.message); }
}
async function stop() { if (X.running) rpc("turn/interrupt", { threadId: X.id, turnId: X.running }).catch(e => toast(e.message)); }
function autosize() { const t = $("xinput"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, window.innerHeight * .38) + "px"; drawComposer(); drawSlash(); }

// ---------- slash commands ----------
// Typing "/" at the start of the box opens a menu that narrows as you type. Built-ins run here; Codex skills (/name args) start a turn
// with that skill attached; anything else is an ordinary message.
const BUILTIN = [
  { name: "new", description: "Start a new chat", run: () => newChat() },
  { name: "model", description: "Choose the model", run: () => modelSheet() },
  { name: "compact", description: "Summarize the chat to free up context", chat: true, run: compact },
  { name: "review", description: "Review uncommitted changes (or: /review what to look for)", chat: true, args: true, run: review },
  { name: "undo", description: "Drop the last message and reply from the chat history (files are not reverted)", chat: true, run: undo },
  { name: "fork", description: "Copy this chat into a new one", chat: true, run: fork },
  { name: "rename", description: "Rename this chat", chat: true, args: true, run: rename },
  { name: "stop", description: "Stop the current reply", chat: true, run: () => X.running ? stop() : toast("Nothing is running") },
  { name: "usage", description: "Plan limits, context and tokens", run: () => usageSheet() },
  { name: "reload", description: "Pick up new or changed skills", run: () => loadSkills(true) },
];
async function loadSkills(force) {
  try {
    const r = await rpc("skills/list", { ...(X.dir ? { cwds: [X.dir] } : {}), forceReload: !!force });
    const seen = new Set();
    X.skills = (r.data || []).flatMap(e => e.skills || []).filter(k => k.enabled !== false && !seen.has(k.name) && seen.add(k.name));
    if (force) toast(`Reloaded: ${X.skills.length} skills`);
  } catch (e) { if (force) toast("Couldn’t reload: " + e.message); }
}
function commands() {
  return BUILTIN.map(c => ({ ...c, kind: "" })).concat(X.skills.filter(k => !BUILTIN.some(b => b.name === k.name))
    .map(k => ({ name: k.name, description: k.shortDescription || k.description || "", kind: "skill", args: true, skill: k })));
}
function slashQuery() { const m = $("xinput").value.match(/^\/(\S*)$/); return m ? m[1].toLowerCase() : null; }
function slashMatches(q) {
  const all = commands(), pre = all.filter(c => c.name.toLowerCase().startsWith(q)),
    mid = all.filter(c => !c.name.toLowerCase().startsWith(q) && (c.name.toLowerCase().includes(q) || (q.length > 2 && c.description.toLowerCase().includes(q))));
  return pre.concat(mid).slice(0, 40);
}
function drawSlash() {
  const q = slashQuery(), box = $("xmenu");
  const list = q === null ? [] : slashMatches(q);
  if (!list.length) { box.hidden = true; X.slash = null; return; }
  X.slash = { list, sel: Math.min(X.slash ? X.slash.sel : 0, list.length - 1) };
  box.innerHTML = list.map((c, i) => `<button data-slash="${i}" class="${i === X.slash.sel ? "on" : ""}"><b>/${esc(c.name)}</b>${c.kind ? `<i>${c.kind}</i>` : ""}<span>${esc(c.description.split("\n")[0].slice(0, 120))}</span></button>`).join("");
  box.hidden = false;
  const on = box.querySelector(".on"); if (on) on.scrollIntoView({ block: "nearest" });
}
function pickSlash(i) {
  const c = X.slash && X.slash.list[i]; if (!c) return;
  if (c.args) { $("xinput").value = "/" + c.name + " "; autosize(); $("xinput").focus(); return; }     // these take arguments: let them be typed
  $("xinput").value = ""; autosize(); runBuiltin(c, "");
}
function slashCommand(text) {
  const m = text.match(/^\/(\S+)(?:\s+([\s\S]*))?$/); if (!m) return null;
  const args = (m[2] || "").trim(), b = BUILTIN.find(c => c.name === m[1]); if (b) return { builtin: b, args };
  const k = X.skills.find(x => x.name === m[1]); return k ? { skill: k, args } : null;
}
function runBuiltin(c, args) {
  if (c.chat && !X.id) { toast("Start a chat first"); return; }
  c.run(args);
}
async function startTurn(input) {
  if (!X.id) { const r = await rpc("thread/start", { approvalPolicy: "never", sandbox: "danger-full-access", ...(X.model ? { model: X.model } : {}) }); X.id = r.thread.id; store.set("thread", X.id); loadList(); }
  await rpc("turn/start", { threadId: X.id, approvalPolicy: "never", sandboxPolicy: { type: "dangerFullAccess" }, input, ...(X.model ? { model: X.model } : {}) });
}
async function sendSkill(skill, args) {
  if (X.running) { toast("Wait for the reply, or /stop it"); return; }
  const text = "$" + skill.name + (args ? " " + args : "");
  X.err = "";
  try {
    putItem("local", { type: "userMessage", id: "local-" + Date.now(), content: [{ type: "text", text: "/" + skill.name + (args ? " " + args : "") }] }); drawMsgs(); scrollEnd(true);
    await startTurn([{ type: "text", text, text_elements: [] }, { type: "skill", name: skill.name, path: skill.path }]);
  } catch (e) { X.err = e.message; drawMsgs(); }
}
async function compact() {
  try { toast("Compacting…"); await rpc("thread/compact/start", { threadId: X.id }); } catch (e) { toast("Couldn’t compact: " + e.message); }
}
async function review(args) {
  if (X.running) { toast("Wait for the reply, or /stop it"); return; }
  X.err = "";
  try { await rpc("review/start", { threadId: X.id, target: args ? { type: "custom", instructions: args } : { type: "uncommittedChanges" }, delivery: "inline" }); }
  catch (e) { X.err = e.message; drawMsgs(); }
}
async function undo() {
  if (X.running) { toast("Stop the reply first"); return; }
  const turns = X.turns.filter(t => t.id !== "local"), last = turns[turns.length - 1];
  if (!last) { toast("Nothing to undo"); return; }
  const u = (last.items || []).find(i => i.type === "userMessage");
  try { await rpc("thread/revert", { threadId: X.id, beforeTurnId: last.id }); await openThread(X.id, true); if (u) { $("xinput").value = userText(u); autosize(); } }
  catch (e) { toast("Couldn’t undo: " + e.message); }
}
async function fork() {
  try { const r = await rpc("thread/fork", { threadId: X.id }); await loadList(); await openThread(r.thread.id); toast("Forked into a new chat"); }
  catch (e) { toast("Couldn’t fork: " + e.message); }
}
async function rename(args) {
  const go = async name => { name = String(name || "").trim(); if (!name) return; try { await rpc("thread/name/set", { threadId: X.id, name }); sheet(false); loadList(); } catch (e) { toast("Couldn’t rename: " + e.message); } };
  if (args) return go(args);
  sheet(true, `<div class="grab"></div><h4>Rename chat</h4><input id="xname" value="${esc(title())}"><button class="go" id="xnamego">Rename</button>`);
  $("xnamego").onclick = () => go($("xname").value); $("xname").onkeydown = e => { if (e.key === "Enter") go($("xname").value); }; $("xname").select();
}

// ---------- drawing ----------
function userText(it) { return (it.content || []).filter(c => c.type === "text").map(c => c.text).join("\n"); }
function stepOf(it) {
  const open = X.open.has(it.id), ok = it.status === "completed" || it.status === "success", bad = it.status === "failed" || it.status === "declined";
  let lbl, det = "";
  if (it.type === "commandExecution") {
    lbl = `<b>${it.status === "inProgress" ? "Running" : "Ran"}</b> ${esc(String(it.command || "").replace(/^\S*\/(?:ba|z)?sh -lc '?/, "").replace(/'$/, "").slice(0, 120))}`;
    det = `<pre>$ ${esc(it.command)}</pre>${it.aggregatedOutput ? `<pre>${esc(it.aggregatedOutput.slice(-6000))}</pre>` : ""}${it.exitCode ? `<pre>exit ${it.exitCode}</pre>` : ""}`;
  } else if (it.type === "fileChange") {
    const ch = it.changes || [];
    lbl = `<b>Edited</b> ${ch.length === 1 ? esc(ch[0].path.split("/").pop()) : ch.length + " files"}`;
    det = ch.map(c => `<pre>${esc(c.path)}\n${esc(c.diff || "").split("\n").map(l => l[0] === "+" ? `<span class="add">${l}</span>` : l[0] === "-" ? `<span class="del">${l}</span>` : l).join("\n")}</pre>`).join("");
  } else if (it.type === "mcpToolCall") {
    lbl = `<b>${esc(it.server)}</b> ${esc(it.tool)}`; det = `<pre>${esc(JSON.stringify(it.arguments, null, 1).slice(0, 2000))}</pre>`;
  } else return "";
  return `<div><button class="step${bad ? " err" : ""}" data-t="${esc(it.id)}"><span class="ic">${it.status === "inProgress" ? '<i class="spin"></i>' : bad ? "✕" : "✓"}</span><span class="lbl">${lbl}</span></button>${open ? `<div class="sdet">${det}</div>` : ""}</div>`;
}
function drawMsgs() {
  const box = $("xmsgs"), items = X.turns.flatMap(t => t.items || []);
  $("xempty").hidden = !!items.length;
  let h = "", steps = "";
  const flush = () => { if (steps) { h += `<div class="steps">${steps}</div>`; steps = ""; } };
  for (const it of items) {
    if (it.type === "userMessage") { flush(); h += `<div class="cu"><div class="bub">${(it.content || []).filter(c => c.type === "image" && /^(data:image|https?:)/.test(c.url || "")).map(c => `<img src="${esc(c.url)}" alt="">`).join("")}${esc(userText(it))}</div></div>`; }
    else if (it.type === "agentMessage") { flush(); h += `<div class="ca md">${md(it.text || "")}</div>`; }
    else if (it.type === "plan") { flush(); h += `<div class="ca md">${md(it.text || "")}</div>`; }
    else steps += stepOf(it);
  }
  flush();
  if (X.running) h += '<span class="spark">&#10043;</span>';
  if (X.err) h += `<div class="cerr">${esc(X.err)}</div>`;
  const keep = $("xscroll"), near = keep.scrollHeight - keep.scrollTop - keep.clientHeight < 120;
  box.innerHTML = h;
  if (near) scrollEnd();
}
function scrollEnd(force) { const s = $("xscroll"); requestAnimationFrame(() => s.scrollTop = s.scrollHeight); }
function drawComposer() {
  const has = !!$("xinput").value.trim() || X.files.length, run = !!X.running, b = $("xsend");
  $("xfiles").innerHTML = X.files.map((f, i) => `<span class="chip">${/^image\//.test(f.mime) ? `<img src="${esc(f.url)}" alt="">` : "📎"} ${esc(f.filename)}<button data-unfile="${i}" aria-label="Remove">×</button></span>`).join("");
  b.innerHTML = run && !has ? STOP : SEND; b.disabled = !run && !has; b.dataset.stop = run && !has ? "1" : "";
  const m = X.models.find(x => x.id === X.model || x.model === X.model) || X.models.find(x => x.isDefault);
  $("xmodel").textContent = m ? m.displayName : "Model"; $("xmodel").hidden = !X.models.length;
}
function drawList() {
  const q = X.search.toLowerCase(), now = Date.now() / 1000, groups = [["Today", 86400], ["Previous 7 days", 7 * 86400], ["Older", 1e12]];
  const list = X.sessions.filter(s => !q || (s.name || s.preview || "").toLowerCase().includes(q)).sort((a, b) => b.updatedAt - a.updatedAt);
  let h = "", last = -1;
  for (const s of list) {
    const g = groups.findIndex(([, age]) => now - s.updatedAt < age);
    if (g !== last) { h += `<h5>${groups[g][0]}</h5>`; last = g; }
    h += `<button class="citem${s.id === X.id ? " on" : ""}" data-id="${esc(s.id)}"><span>${esc(s.name || s.preview || "New chat")}</span>${X.busy.has(s.id) ? '<i class="spin busy"></i>' : ""}</button>`;
  }
  $("xlist").innerHTML = h || '<div class="cstat">No chats yet</div>';
}
function drawAsks() {
  let h = "";
  for (const a of X.asks.values()) {
    const p = a.params || {}, id = a.ask;
    if (a.method === "item/commandExecution/requestApproval") {
      h += `<div class="card"><h4>Run a command?</h4>${p.reason ? esc(p.reason) : ""}<code>${esc(p.command || "")}</code><div class="btns"><button data-a="${id}" data-r='"decline"'>Decline</button><button data-a="${id}" data-r='"acceptForSession"'>Always (this chat)</button><button class="pri" data-a="${id}" data-r='"accept"'>Allow</button></div></div>`;
    } else if (a.method === "item/fileChange/requestApproval") {
      h += `<div class="card"><h4>Edit files?</h4>${esc(p.reason || "Codex wants to change files.")}${p.grantRoot ? `<code>${esc(p.grantRoot)}</code>` : ""}<div class="btns"><button data-a="${id}" data-r='"decline"'>Decline</button><button data-a="${id}" data-r='"acceptForSession"'>Always (this chat)</button><button class="pri" data-a="${id}" data-r='"accept"'>Allow</button></div></div>`;
    } else if (a.method === "item/permissions/requestApproval") {
      h += `<div class="card"><h4>More access?</h4>${esc(p.reason || "Codex asks for extra permissions.")}<code>${esc(JSON.stringify(p.permissions || {}, null, 1))}</code><div class="btns"><button data-p="${id}" data-g="0">Decline</button><button class="pri" data-p="${id}" data-g="1">Allow this turn</button></div></div>`;
    } else if (a.method === "item/tool/requestUserInput") {
      h += `<div class="card" data-q="${id}">${(p.questions || []).map(q => `<h4>${esc(q.header || "Question")}</h4>${esc(q.question)}${(q.options || []).map(o => `<button class="opt" data-qi="${esc(q.id)}" data-v="${esc(o.label)}">${esc(o.label)}<small>${esc(o.description || "")}</small></button>`).join("")}<input data-qt="${esc(q.id)}" placeholder="${q.options ? "Or type an answer" : "Your answer"}"${q.isSecret ? ' type="password"' : ""}>`).join("")}<div class="btns"><button class="pri" data-qs="${id}">Send</button></div></div>`;
    } else {
      h += `<div class="card"><h4>Codex needs something AVS can't show</h4>${esc(a.method)}<div class="btns"><button data-a="${id}" data-r='"decline"'>Decline</button></div></div>`;
    }
  }
  $("xpend").innerHTML = h;
}
function answer(id, result) { X.ws && X.ws.send(JSON.stringify({ answer: id, result })); X.asks.delete(id); drawAsks(); }

// ---------- drawer, sheet ----------
function menu(on) { const o = $("codex"); on = on === undefined ? !o.classList.contains("drawer") : on; o.classList.toggle("drawer", on); $("xdrawer").setAttribute("aria-hidden", !on); if (on) loadList(); }
function sheet(on, html) { const o = $("codex"); o.classList.toggle("sheet", !!on); $("xsheet").setAttribute("aria-hidden", !on); if (html !== undefined) $("xsheet").innerHTML = html; }
function modelSheet() {
  sheet(true, `<div class="grab"></div><h4>Model</h4><div class="opts">${X.models.map(m => `<button data-m="${esc(m.id)}" class="${m.id === X.model || (!X.model && m.isDefault) ? "on" : ""}"><span>${esc(m.displayName)}</span><small>${esc(m.description || "").slice(0, 40)}</small></button>`).join("")}</div>`);
}
const num = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n || 0);
const span = m => !m ? "" : m % 10080 === 0 ? (m / 10080 === 1 ? "Weekly" : m / 10080 + "-week") : m % 1440 === 0 ? m / 1440 + "-day" : m % 60 === 0 ? m / 60 + "-hour" : m + "-minute";
function when(t) { return t ? new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" }) : ""; }
function windows() { const l = X.limits || {}; return [l.primary, l.secondary].filter(Boolean); }
function reached() { const l = X.limits || {}; return l.rateLimitReachedType || (windows().some(w => w.usedPercent >= 100) ? "limit" : ""); }
function drawLimit() {                           // a warning above the composer once a limit is nearly or fully used
  const top = windows().sort((a, b) => b.usedPercent - a.usedPercent)[0], el = $("xlimit");
  if (!top || top.usedPercent < 90) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<b>${reached() ? "Usage limit reached" : "Usage " + Math.round(top.usedPercent) + "% used"}</b>${top.resetsAt ? " · resets " + when(top.resetsAt) : ""}`;
}
function drawUsage() {
  const l = X.limits || {}, ws = windows(), t = X.tokens;
  const bar = w => `<div class="urow"><span>${esc(span(w.windowDurationMins) || "Limit")}</span><b>${Math.round(w.usedPercent)}% used${w.resetsAt ? " · resets " + when(w.resetsAt) : ""}</b></div><div class="ubar"><i style="width:${Math.min(100, w.usedPercent)}%"></i></div>`;
  let h = '<div class="grab"></div><h4>Usage</h4><div class="usage">';
  if (reached()) h += `<div class="ulimit"><b>Limit reached</b>${l.rateLimitReachedType ? " (" + esc(String(l.rateLimitReachedType).replace(/_/g, " ")) + ")" : ""}<div>Codex will not run until the window resets.</div></div>`;
  h += `<h5>Plan limits${l.planType ? " · " + esc(l.planType) : ""}</h5>` + (ws.length ? ws.map(bar).join("") : '<div class="urow"><span>No limit data yet</span></div>');
  if (l.credits && l.credits.balance != null) h += `<div class="urow"><span>Credits</span><b>${esc(l.credits.balance)}</b></div>`;
  if (t) {
    const ctx = t.modelContextWindow ? Math.min(100, Math.round(t.last.totalTokens / t.modelContextWindow * 100)) : null;
    h += "<h5>This chat</h5>";
    if (ctx !== null) h += `<div class="urow"><span>Context</span><b>${ctx}% · ${num(t.last.totalTokens)} / ${num(t.modelContextWindow)}</b></div><div class="ubar"><i style="width:${ctx}%"></i></div>`;
    h += `<div class="urow"><span>Input</span><b>${num(t.total.inputTokens)}${t.total.cachedInputTokens ? " (" + num(t.total.cachedInputTokens) + " cached)" : ""}</b></div>`
      + `<div class="urow"><span>Output</span><b>${num(t.total.outputTokens)}${t.total.reasoningOutputTokens ? " (" + num(t.total.reasoningOutputTokens) + " reasoning)" : ""}</b></div>`
      + `<div class="urow"><span>Total</span><b>${num(t.total.totalTokens)}</b></div>`;
  }
  sheet(true, h + "</div>");
}
function usageSheet() {
  drawUsage();
  rpc("account/rateLimits/read", {}).then(r => { X.limits = (r && (r.rateLimits || r)) || X.limits; drawLimit(); if ($("xsheet").querySelector(".usage")) drawUsage(); }).catch(() => {});
}

// ---------- wiring ----------
function wire() {
  $("xburger").onclick = () => menu();
  $("xshade").onclick = () => { menu(false); sheet(false); };
  $("xnew").onclick = newChat; $("xnewb").onclick = newChat;
  $("xusage").onclick = usageSheet;
  $("xmodel").onclick = modelSheet;
  $("xsearch").oninput = e => { X.search = e.target.value; drawList(); };
  $("xsend").onclick = () => $("xsend").dataset.stop ? stop() : send();
  $("xinput").oninput = autosize;
  $("xplus").onclick = () => $("xpick").click();
  $("xpick").onchange = e => { attach([...e.target.files]); e.target.value = ""; };
  $("xfiles").onclick = e => { const b = e.target.closest("[data-unfile]"); if (b) { X.files.splice(+b.dataset.unfile, 1); drawComposer(); } };
  $("xinput").addEventListener("paste", e => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); attach(fs); } });
  const xb = $("xbox");
  xb.addEventListener("dragover", e => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); xb.classList.add("drop"); } });
  xb.addEventListener("dragleave", () => xb.classList.remove("drop"));
  xb.addEventListener("drop", e => { if (e.dataTransfer?.files.length) { e.preventDefault(); xb.classList.remove("drop"); attach([...e.dataTransfer.files]); } });
  $("xinput").onfocus = () => document.body.classList.add("typing");
  // Tapping Send used to blur the box first: the keyboard closed, the nav bar came back and moved the button before the click landed.
  // Keep focus on the box while Send is pressed, and (like the Chat tab) give a real blur a moment so the layout does not jump mid-tap.
  $("xsend").addEventListener("pointerdown", e => e.preventDefault());
  $("xsend").addEventListener("mousedown", e => e.preventDefault());
  $("xinput").onblur = () => setTimeout(() => { if (document.activeElement !== $("xinput")) document.body.classList.remove("typing"); }, 150);
  $("xinput").onkeydown = e => {
    if (X.slash && !$("xmenu").hidden) {                       // a hardware keyboard can walk the command menu
      const n = X.slash.list.length;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); X.slash.sel = (X.slash.sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; drawSlash(); return; }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); pickSlash(X.slash.sel); drawSlash(); return; }
      if (e.key === "Escape") { e.preventDefault(); $("xmenu").hidden = true; X.slash = null; return; }
    }
    if (e.key === "Enter" && !e.shiftKey && e.ctrlKey) { e.preventDefault(); send(); }
  };
  $("xmenu").onclick = e => { const b = e.target.closest("[data-slash]"); if (b) { pickSlash(+b.dataset.slash); drawSlash(); } };
  $("xlist").onclick = e => { const b = e.target.closest(".citem"); if (b) { menu(false); openThread(b.dataset.id); } };
  $("xsheet").onclick = e => { const b = e.target.closest("[data-m]"); if (b) { X.model = b.dataset.m; store.set("model", X.model); sheet(false); drawComposer(); } };
  $("xmsgs").onclick = e => {
    const t = e.target.closest("[data-t]"); if (t) { const id = t.dataset.t; X.open.has(id) ? X.open.delete(id) : X.open.add(id); drawMsgs(); return; }
    const c = e.target.closest("[data-copy]"); if (c) { const code = c.closest(".code").querySelector("code").textContent; navigator.clipboard.writeText(code).then(() => toast("Copied")).catch(() => {}); }
  };
  $("xpend").onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.a) answer(+b.dataset.a, { decision: JSON.parse(b.dataset.r) });
    else if (b.dataset.p) { const a = X.asks.get(+b.dataset.p); answer(+b.dataset.p, b.dataset.g === "1" ? { permissions: a.params.permissions || {}, scope: "turn" } : { permissions: {}, scope: "turn" }); }
    else if (b.dataset.qi) { b.parentNode.querySelectorAll(`[data-qi="${b.dataset.qi}"]`).forEach(o => o.classList.toggle("on", o === b)); }
    else if (b.dataset.qs) {
      const card = b.closest("[data-q]"), answers = {};
      for (const inp of card.querySelectorAll("[data-qt]")) {
        const qid = inp.dataset.qt, pick = card.querySelector(`[data-qi="${qid}"].on`);
        const v = inp.value.trim() || (pick && pick.dataset.v) || ""; if (v) answers[qid] = { answers: [v] };
      }
      answer(+b.dataset.qs, { answers });
    }
  };
}

function start(s) {
  const has = !!(s && s.codex);
  $("xnone").hidden = has; $("xmain").hidden = !has;
  if (!has || X.started) return;
  X.started = true; X.dir = s.codex_directory || "";
  $("xdir").textContent = X.dir || "Chats on your PC";
  wire(); drawComposer(); connect();
}
window.AVSCodex = {
  start, menu: () => menu(), newChat, usage: usageSheet, title,
  onTitle(fn) { X.onTitle = fn; },
  show() { if (X.started) { connect(); scrollEnd(true); } },
  hide() { menu(false); sheet(false); document.body.classList.remove("typing"); },
  back() { const o = $("codex"); if (o.classList.contains("sheet")) { sheet(false); return true; } if (o.classList.contains("drawer")) { menu(false); return true; } return false; },
};
})();
