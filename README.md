# minepaint 🎨⛏️

A Minecraft-styled **paint tool for AI assistants**. An LLM drives a 3D voxel
world through MCP tools — place blocks, fill cuboids, build one object as an
*entity* and copy/move it around, paint on separate *layers*, or generate
full terrain (mountains, rivers, oceans, snow caps). A Three.js viewer shows
the world live in 3D with sunset/day lighting and can export any camera angle
as a JPG. The viewer has a **prompt box** — type a scene, and the LLM plans
and paints it for you.

```
LLM (Hermes / OpenCode / viewer prompt box) → MCP tools → world state
        → WebSocket → Three.js viewer → Export JPG
```

## Quick start

```bash
# web viewer + live WS + HTTP MCP  →  http://localhost:8766
.venv/bin/uvicorn minepaint.web_server:app --host 127.0.0.1 --port 8766

# MCP server over stdio (for client configs) — same world state
.venv/bin/python -m minepaint.mcp_server
```

**Auth**: the viewer requires a token. It's auto-generated on first run and
appended to `~/.hermes/.env` as `MINEPAINT_TOKEN`. First page load asks for it
and remembers it (localStorage). All WS/API calls require it.

**Prompt box**: bottom-left in the viewer. The prompt runs through the
opencode CLI (`opencode-go/deepseek-v4-flash` by default; override with the
`MINEPAINT_LLM_MODEL` env var). The LLM returns a JSON plan of tool calls,
executed in-process; the world updates live.

## MCP client config

```json
// OpenCode ~/.config/opencode/opencode.json
{ "model": "...", "mcp": { "minepaint": {
    "type": "local",
    "command": ["/home/ubuntu/minepaint/.venv/bin/python", "-m", "minepaint.mcp_server"],
    "cwd": "/home/ubuntu/minepaint", "enabled": true } } }

// Claude Desktop: same command/args; or HTTP: url http://127.0.0.1:8766/mcp
```

## Tools (19)

| Tool | What it does |
|---|---|
| `world_info` / `get_state` | canvas summary / full world JSON (read-back) |
| `generate_terrain` | **terrain**: ridged mountains, snow caps, rocky slopes, beaches, oceans, meandering river. Tuned recipe: `seed=<n>, sea_level=12, snowline=26, mountain_amp=44` |
| `place_block` / `delete_block` | one block |
| `fill_cuboid` | fill an inclusive 3D box |
| `create_layer` / `list_layers` / `set_layer_visible` / `delete_layer` | layered canvas stack |
| `create_entity` / `list_entities` / `get_entity` | named block groups (the copy/paste unit) |
| `copy_entity` | **hero**: duplicate an entity at an offset, N copies |
| `move_entity` / `delete_entity` | relocate / remove an entity |
| `save_world` / `load_world` / `reset_world` | JSON persistence (`data/world.json`, autosaved) |

World: **96 × 96 footprint, y from −32 (seabed/bedrock) to +63**. y = up.
Palette is fixed (18 block types) — the LLM cannot invent block types.

## Viewer

- **Day/night cycle** — the sun rises in the east (+x), arcs overhead, sets in
  the west (−x); the moon follows the same axis opposite phase. Time slider
  (0–24h), auto-cycle button (▶ Cycle, one full day per 5 min), starfield at
  night, dynamic sky/lighting by sun elevation. URL params: `?time=18`, `?cycle=1`
- Orbit camera + presets (Isometric / Top / Front / Side) + cinematic URL
  params: `?camx=&camy=&camz=&tgtx=&tgty=&tgtz=` and `?ui=0` (hide HUD)
- **🎲 Random** button — generates a fresh random landscape (random seed +
  varied sea_level/snowline/mountain_amp via `POST /api/random_landscape`)
- Layer visibility toggles, palette legend, per-block color jitter (pseudo-texture)
- **Export JPG**: supersampled render of the current camera angle
- **Prompt box**: LLM paints your scene (opencode CLI backend)

## Test

```bash
.venv/bin/python tests/test_client.py   # 33 checks: layers, entities, copy/move,
                                        # bounds, terrain (snow/ocean/seabed)
```
