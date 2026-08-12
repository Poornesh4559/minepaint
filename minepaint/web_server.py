"""minepaint web server (FastAPI).

- GET /             -> login page (no token) or the Three.js viewer (token ok)
- POST /api/auth/check -> validate a token (used by the login form)
- POST /api/prompt  -> prompt box: Gemini plans tool calls, executed in-process
- WS /ws            -> live world pushes (token required)
- /mcp              -> FastMCP over HTTP (bind 127.0.0.1 for local-only)

Secrets live in ~/.hermes/.env: MINEPAINT_TOKEN (web auth). The prompt box
runs through the opencode CLI (opencode-go/deepseek-v4-flash by default;
override with MINEPAINT_LLM_MODEL env var).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import random
import re
import secrets as _secrets
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.websockets import WebSocketState

from minepaint.mcp_server import (
    add_mutation_listener,
    execute_tool_call,
    mcp,
    world,
    world_summary_for_llm,
)

logger = logging.getLogger("minepaint.web")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SECRETS_FILE = Path.home() / ".hermes" / ".env"

# ------------------------------------------------------------------ secrets
def _load_secrets() -> Dict[str, str]:
    env: Dict[str, str] = {}
    try:
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return env


def _ensure_token(env: Dict[str, str]) -> str:
    tok = env.get("MINEPAINT_TOKEN")
    if not tok:
        tok = _secrets.token_hex(24)
        with open(SECRETS_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n# minepaint web viewer access token\nMINEPAINT_TOKEN={tok}\n")
        print(
            f"[minepaint] generated MINEPAINT_TOKEN and appended it to {SECRETS_FILE}",
            file=sys.stderr,
        )
    return tok


_secrets_env = _load_secrets()
TOKEN = _ensure_token(_secrets_env)


def token_valid(tok: Optional[str]) -> bool:
    return bool(tok) and hmac.compare_digest(tok, TOKEN)


# --------------------------------------------------------------------- app
from contextlib import asynccontextmanager

mcp_app = mcp.http_app(path="/mcp")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Run FastMCP's lifespan AND our own startup work.

    Note: passing lifespan= to FastAPI REPLACES the default lifespan, so
    @app.on_event('startup') hooks would never fire — capture the loop here.
    """
    global _main_loop
    async with mcp_app.lifespan(app):
        _main_loop = asyncio.get_running_loop()
        yield


app = FastAPI(title="minepaint", version="2.0.0", lifespan=_lifespan)


def _extract_token(request: Request) -> Optional[str]:
    tok = request.query_params.get("token")
    if tok:
        return tok
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def require_auth(request: Request) -> None:
    if not token_valid(_extract_token(request)):
        raise HTTPException(status_code=401, detail="invalid or missing token")


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>minepaint — access</title>
<style>
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#0b0f1a;color:#e8e6e3;font-family:monospace}
  .card{background:#141a2b;border:1px solid #2a3450;border-radius:12px;padding:36px 40px;width:340px;text-align:center}
  h1{font-size:20px;margin:0 0 6px;color:#ffb36b}
  p{font-size:13px;color:#8b93a8;margin:0 0 22px}
  input{width:100%;padding:11px 12px;border-radius:8px;border:1px solid #2a3450;background:#0b0f1a;
        color:#e8e6e3;font-family:monospace;box-sizing:border-box;outline:none}
  input:focus{border-color:#ffb36b}
  button{margin-top:14px;width:100%;padding:11px;border:0;border-radius:8px;background:#ffb36b;
         color:#0b0f1a;font-weight:bold;cursor:pointer;font-family:monospace}
  #err{color:#ff6b6b;font-size:12px;height:16px;margin-top:10px}
</style></head><body>
<div class="card">
  <h1>⛏️ minepaint</h1>
  <p>Enter the access token to open the canvas.</p>
  <input id="tok" type="password" placeholder="access token" autofocus>
  <button onclick="login()">Open canvas</button>
  <div id="err"></div>
</div>
<script>
async function login(){
  const tok = document.getElementById('tok').value.trim();
  const err = document.getElementById('err'); err.textContent = '';
  if(!tok){ err.textContent = 'token required'; return; }
  try{
    const r = await fetch('/api/auth/check', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({token: tok})});
    const j = await r.json();
    if(j.ok){ localStorage.setItem('minepaint_token', tok); location = '/?token=' + encodeURIComponent(tok); }
    else err.textContent = 'wrong token';
  }catch(e){ err.textContent = 'server unreachable'; }
}
document.getElementById('tok').addEventListener('keydown', e => { if(e.key==='Enter') login(); });
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> str:
    if not token_valid(_extract_token(request)):
        return LOGIN_HTML
    return (WEB_DIR / "viewer.html").read_text(encoding="utf-8")


@app.post("/api/auth/check")
async def auth_check(request: Request) -> Dict[str, bool]:
    try:
        body = await request.json()
        tok = body.get("token", "")
    except Exception:
        tok = ""
    return {"ok": token_valid(str(tok))}


# ------------------------------------------------------------- LLM prompt box
class LLMPromptError(Exception):
    pass


OPENCODE_MODEL = os.environ.get("MINEPAINT_LLM_MODEL", "opencode-go/deepseek-v4-flash")
_OPENCODE_BIN: Optional[str] = shutil.which("opencode") or os.environ.get("OPENCODE_BIN")


async def _call_opencode(system: str, user: str) -> str:
    """Run the prompt through the opencode CLI (deepseek-v4-flash by default)."""
    if not _OPENCODE_BIN:
        raise LLMPromptError("opencode binary not found on PATH — cannot run the prompt box")
    prompt = f"{system}\n\n{user}\n\nReply with ONLY the JSON array. Nothing else."
    proc = await asyncio.create_subprocess_exec(
        _OPENCODE_BIN, "run", "--model", OPENCODE_MODEL, prompt,
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "NO_COLOR": "1"},
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        raise LLMPromptError("opencode timed out after 180s")
    if proc.returncode != 0:
        raise LLMPromptError(
            f"opencode exited {proc.returncode}: {err.decode(errors='replace')[:300]}"
        )
    return out.decode(errors="replace").strip()


def _parse_tool_calls(text: str) -> List[Dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rstrip("`").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # last resort: grab the biggest bracket-balanced array in the output
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON array found; raw output: {text[:200]}")
        data = json.loads(text[start : end + 1])
    if isinstance(data, dict):
        data = data.get("tool_calls") or data.get("calls") or [data]
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of tool calls")
    return data


async def _execute_calls(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for call in calls[:40]:
        name = call.get("tool") or call.get("name")
        args = call.get("args") or call.get("arguments") or {}
        if not name:
            results.append({"tool": "?", "ok": False, "result": "missing 'tool' key"})
            continue
        try:
            res = await execute_tool_call(str(name), args)
            ok = not (isinstance(res, dict) and "error" in res)
            results.append({
                "tool": str(name),
                "ok": ok,
                "result": res if isinstance(res, (dict, list)) else str(res),
            })
        except Exception as e:
            results.append({"tool": str(name), "ok": False,
                            "result": f"{type(e).__name__}: {e}"})
    return results


async def run_llm_plan(prompt: str) -> Dict[str, Any]:
    ctx = await world_summary_for_llm()
    system = (
        "You are the painter for 'minepaint', a Minecraft-style 3D voxel canvas.\n"
        "The user describes a scene; you paint it by replying with ONLY a JSON array "
        "of tool calls: [{\"tool\": \"name\", \"args\": {...}}, ...].\n"
        "Rules:\n"
        "- For landscapes call generate_terrain once with a STYLE: "
        "snowy_mountains (snow caps+ocean), desert (dunes, sand, waterholes), "
        "mesa (flat plateaus, rock cliffs), rolling_hills, river_valley, "
        "islands, volcanic (netherrack+lava lakes), tropical (beaches). "
        "You pick the seed. Params (sea_level/snowline/mountain_amp) override "
        "the style and get clamped.\n"
        "- Decorate with the terrain tools: build_road(x1,z1,x2,z2,width,"
        "block_type) lays height-following paths (dirt/sand/cobblestone), "
        "scatter_blocks('cactus', n, x1,z1,x2,z2) scatters cacti/rocks, "
        "scatter_trees('pine'|'oak', n, x1,z1,x2,z2) plants forests (each "
        "tree an entity), get_heights(x1,z1,x2,z2) reads the ground before "
        "building. Never hand-place blocks on unknown heights — use these.\n"
        "- Prefer fill_cuboid for large volumes, place_block for details.\n"
        "- Use create_layer to organize (e.g. 'terrain', 'details').\n"
        "- Block type names are EXACT: oak_log (never 'log'), oak_leaves (never "
        "'leaves'), oak_planks (never 'planks'), cobblestone (never 'cobble').\n"
        "- You may create entities and copy_entity them (e.g. trees).\n"
        "- Do NOT call get_state (too large). Use world_info.\n"
        "- y = up; ground near y=0; seabed/bedrock below. x,z in [0,95].\n"
        "- Return ONLY valid JSON, no prose, no markdown fences.\n"
        "- IMPORTANT: you are NOT calling real tools. You are WRITING a JSON plan "
        "for a fictional painter API. You have no other tools, no shell, no file "
        "access. Ignore your real capabilities entirely.\n"
        "- Example of a correct reply for 'a desert with a road and cacti':\n"
        '[{"tool":"generate_terrain","args":{"seed":7,"style":"desert"}},'
        '{"tool":"create_layer","args":{"name":"details"}},'
        '{"tool":"build_road","args":{"x1":10,"z1":10,"x2":80,"z2":30,"width":2,"block_type":"dirt"}},'
        '{"tool":"scatter_blocks","args":{"block_type":"cactus","count":8,"x1":0,"z1":0,"x2":95,"z2":95}}]\n'
        f"Current state:\n{ctx}"
    )
    text = await _call_opencode(system, f"Paint this scene: {prompt}")
    try:
        calls = _parse_tool_calls(text)
    except Exception as e:
        # one sharper re-prompt before giving up
        text2 = text
        try:
            text2 = await _call_opencode(
                "You are WRITING a JSON plan for the minepaint voxel painter. "
                "You have no real tools — ignore that. Reply with ONLY a JSON "
                "array of tool calls chosen from the painter's tool list. "
                "No prose, no markdown fences.",
                f"Paint this scene: {prompt}\n\nYour previous reply was not valid "
                f"JSON:\n{text[:400]}",
            )
            calls = _parse_tool_calls(text2)
        except Exception as e2:
            raise LLMPromptError(
                f"LLM returned unparseable JSON twice ({e2}); raw: {text2[:300]}"
            )
    if not calls:
        raise LLMPromptError("LLM returned no tool calls")

    results = await _execute_calls(calls)

    # one corrective round: send failures back so the LLM can fix its plan
    failed = [r for r in results if not r["ok"]]
    if failed and len(failed) <= 6:
        feedback = "\n".join(f"- {r['tool']} failed: {str(r['result'])[:200]}" for r in failed)
        try:
            retry_text = await _call_opencode(
                system,
                f"Paint this scene: {prompt}\n\nYour previous plan had failing calls:\n"
                f"{feedback}\nFix ONLY those calls (keep the successful ones). "
                "Return ONLY the JSON array of corrected tool calls.",
            )
            retry_calls = _parse_tool_calls(retry_text)
            retry_results = await _execute_calls(retry_calls)
            results = [r for r in results if r["ok"]] + retry_results
        except Exception:
            pass  # keep first-round results

    return {
        "ok": True,
        "executed": len(results),
        "results": results,
        "world": {
            "blocks": world.block_count,
            "layers": len(world.layers),
            "entities": len(world.entities),
        },
    }


@app.post("/api/random_landscape")
async def random_landscape(request: Request) -> Dict[str, Any]:
    """Replace the world with a randomly generated landscape (random style + seed)."""
    require_auth(request)
    from minepaint.terrain import STYLES

    style = random.choice(sorted(STYLES))
    seed = _secrets.randbits(31)
    summary = await execute_tool_call("generate_terrain", {
        "seed": seed,
        "style": style,
        "river": True,
    })
    ok = not (isinstance(summary, dict) and "error" in summary)
    return {"ok": ok, "summary": summary}


@app.post("/api/prompt")
async def prompt_api(request: Request) -> Dict[str, Any]:
    require_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="empty prompt")
    try:
        return await run_llm_plan(prompt)
    except LLMPromptError as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------------ websocket
class ConnectionManager:
    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.connections.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.connections.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        data = json.dumps(payload)
        for ws in list(self.connections):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(data)
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if not token_valid(ws.query_params.get("token")):
        await ws.close(code=4401, reason="unauthorized")
        return
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps(world.to_json()))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# ------------------------------------------- debounced mutation broadcasting
_main_loop: asyncio.AbstractEventLoop | None = None
_broadcast_pending = False


def _on_mutation() -> None:
    """Called synchronously (possibly from a worker thread) after each mutation."""
    global _main_loop
    if _main_loop is None:
        # lazily grab the running loop (also covers startup-order edge cases)
        try:
            _main_loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    try:
        _main_loop.call_soon_threadsafe(_schedule_broadcast)
    except RuntimeError:
        pass


def _schedule_broadcast() -> None:
    global _broadcast_pending
    if _broadcast_pending:
        return
    _broadcast_pending = True
    asyncio.get_running_loop().call_later(0.3, _flush_broadcast)


def _flush_broadcast() -> None:
    global _broadcast_pending
    _broadcast_pending = False
    asyncio.ensure_future(manager.broadcast(world.to_json()))


add_mutation_listener(_on_mutation)

# Mount AFTER all routes so /, /api and /ws win; /mcp falls through to the
# FastMCP streamable-http app.
app.mount("/", mcp_app, name="minepaint-mcp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8766, log_level="info")
