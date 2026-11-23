"""Codex on this PC, for the Chat tab: AVS runs one `codex app-server` (JSON-RPC over stdio) and relays it to the phone over a WebSocket.

The phone may only call the methods in METHODS; approvals Codex asks for are held until a phone answers them (and replayed to a phone
that connects later), so a dropped connection never loses a question.
"""
import asyncio
import contextlib
import json
import shutil

METHODS = {
    "thread/list", "thread/start", "thread/resume", "thread/read", "thread/turns/list", "thread/name/set", "thread/archive",
    "thread/fork", "thread/compact/start", "thread/revert", "turn/start", "turn/steer", "turn/interrupt", "model/list",
    "account/rateLimits/read", "skills/list", "review/start",
}
# What the phone is told about; the rest of app-server's notifications (realtime audio, fs watchers, MCP startup, ...) stay here.
NOTIFY = ("item/", "turn/", "thread/", "serverRequest/resolved", "account/rateLimits/updated", "warning", "error")
# Requests Codex makes of its client: these are questions for the person. Anything else is refused so Codex never waits on us.
ASK = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval",
       "item/tool/requestUserInput", "mcpServer/elicitation/request", "applyPatchApproval", "execCommandApproval"}

# Approvals AVS grants itself instead of asking the phone (the Codex tab runs fully permissive).
AUTO = {"item/commandExecution/requestApproval": lambda p: {"decision": "acceptForSession"},
        "item/fileChange/requestApproval": lambda p: {"decision": "acceptForSession"},
        "item/permissions/requestApproval": lambda p: {"permissions": p.get("permissions") or {}, "scope": "session"},
        "mcpServer/elicitation/request": lambda p: {"action": "accept", "content": {}},
        "applyPatchApproval": lambda p: {"decision": "approved_for_session"},
        "execCommandApproval": lambda p: {"decision": "approved_for_session"}}


class RpcError(Exception):
    def __init__(self, error):
        super().__init__(str(error.get("message") if isinstance(error, dict) else error))
        self.error = error


def find(cfg: dict) -> str | None:
    """The codex binary: `codex` in the config if set, else on PATH."""
    return shutil.which(cfg.get("codex") or "codex")


class Bridge:
    def __init__(self, binary: str, cwd: str | None = None):
        self.binary, self.cwd = binary, cwd
        self.proc = None
        self.next_id = 1
        self.waiting: dict[int, asyncio.Future] = {}
        self.asks: dict = {}                        # id Codex gave -> the request, until a phone answers
        self.listeners: set[asyncio.Queue] = set()
        self.lock = asyncio.Lock()
        self.reader = None

    async def _send(self, message: dict):
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def ensure(self):
        async with self.lock:
            if self.proc and self.proc.returncode is None:
                return
            self.proc = await asyncio.create_subprocess_exec(
                self.binary, "app-server", cwd=self.cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=1 << 26)
            self.waiting.clear()
            self.asks.clear()
            self.reader = asyncio.create_task(self._read(self.proc))
            await self._call("initialize", {"clientInfo": {"name": "avs", "title": "AVS", "version": "1"}})
            await self._send({"method": "initialized"})

    async def _call(self, method: str, params: dict | None):
        number = self.next_id
        self.next_id += 1
        future = self.waiting[number] = asyncio.get_running_loop().create_future()
        await self._send({"id": number, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(future, 60 if method != "thread/compact/start" else 300)
        finally:
            self.waiting.pop(number, None)

    async def call(self, method: str, params: dict | None = None):
        if method not in METHODS:
            raise RpcError(f"{method} is not available")
        await self.ensure()
        return await self._call(method, params)

    async def answer(self, ask_id, result: dict):
        """A phone's answer to something Codex asked; the first answer wins."""
        if ask_id not in self.asks:
            return
        self.asks.pop(ask_id)
        await self._send({"id": ask_id, "result": result})
        self._fan({"method": "serverRequest/resolved", "params": {"requestId": ask_id}})

    def _fan(self, message: dict):
        for q in self.listeners:
            q.put_nowait(message)

    async def _read(self, proc):
        try:
            while line := await proc.stdout.readline():
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if "method" in m and "id" in m:                                   # Codex asks us something
                    if m["method"] in AUTO:
                        await self._send({"id": m["id"], "result": AUTO[m["method"]](m.get("params") or {})})
                    elif m["method"] in ASK:
                        self.asks[m["id"]] = m
                        self._fan({"ask": m["id"], "method": m["method"], "params": m.get("params")})
                    else:
                        await self._send({"id": m["id"], "error": {"code": -32601, "message": "not supported by AVS"}})
                elif "method" in m:
                    if m["method"].startswith(NOTIFY):
                        self._fan({"method": m["method"], "params": m.get("params")})
                elif m.get("id") in self.waiting:
                    f = self.waiting[m["id"]]
                    if not f.done():
                        f.set_exception(RpcError(m["error"])) if "error" in m else f.set_result(m.get("result"))
        finally:
            for f in self.waiting.values():
                if not f.done():
                    f.set_exception(RpcError("Codex stopped"))
            self.asks.clear()
            self._fan({"method": "avs/stopped", "params": {}})

    def pending(self) -> list[dict]:
        return [{"ask": i, "method": m["method"], "params": m.get("params")} for i, m in self.asks.items()]

    async def close(self):
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), 5)
