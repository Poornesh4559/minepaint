# minepaint 🎨⛏️

A Minecraft-styled **paint tool for AI assistants**. An LLM drives a 3D voxel
world through MCP tools — place blocks, fill cuboids, build one object as an
*entity* and copy/move it around, paint on separate *layers* — while a Three.js
viewer shows the world live in 3D and can export any camera angle as a JPG.

```
LLM (Hermes / OpenCode / Claude) → MCP tools → world state → WebSocket → Three.js viewer
```

## Quick start

```bash
# web viewer + live WS + HTTP MCP  →  http://localhost:8766
.venv/bin/uvicorn minepaint.web_server:app --host 0.0.0.0 --port 8766

# MCP server over stdio (for client configs) — same world state
.venv/bin/python -m minepaint.mcp_server
```

## MCP client config

```json
// OpenCode ~/.config/opencode/opencode.json
{ "model": "...", "mcp": { "minepaint": {
    "type": "local",
    "command": ["/home/ubuntu/minepaint/.venv/bin/python", "-m", "minepaint.mcp_server"],
    "cwd": "/home/ubuntu/minepaint", "enabled": true } } }

// Claude Desktop: same command/args; or HTTP: url http://localhost:8766/mcp
```

## Tools (18)

| Tool | What it does |
|---|---|
| `world_info` / `get_state` | canvas summary / full world JSON (read-back before drawing) |
| `place_block` / `delete_block` | one block |
| `fill_cuboid` | fill an inclusive 3D box |
| `create_layer` / `list_layers` / `set_layer_visible` / `delete_layer` | layered canvas stack |
| `create_entity` / `list_entities` / `get_entity` | named block groups (the copy/paste unit) |
| `copy_entity` | **hero**: duplicate an entity at an offset, N copies |
| `move_entity` / `delete_entity` | relocate / remove an entity |
| `save_world` / `load_world` / `reset_world` | JSON persistence (`data/world.json`, autosaved) |

Palette is fixed (18 block types) — the LLM cannot invent block types.

## Example LLM prompt

> Build a house with a lava moat and 3 trees around it. Make the house a
> 7x3x7 cuboid of brick on a grass floor, put the trees in a "nature" layer as
> an entity, then copy_entity the tree twice.

## Architecture

- `minepaint/core.py` — pure world model (grid, layers, entities, palette)
- `minepaint/mcp_server.py` — FastMCP tools over stdio; autosaves after each mutation
- `minepaint/web_server.py` — FastAPI: serves viewer, `/ws` live push, `/mcp` HTTP transport
- `web/viewer.html` — single-file Three.js (CDN) viewer: orbit cam, camera presets
  (Isometric/Top/Front/Side), layer visibility, palette legend, **Export JPG**
  (supersampled render of the current angle)

## Test

```bash
.venv/bin/python tests/test_client.py   # 26 checks: layers, entities, copy/move, bounds
```
