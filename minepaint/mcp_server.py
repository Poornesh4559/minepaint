"""minepaint FastMCP server.

Exposes the world as MCP tools over stdio (default) or HTTP (via web_server).
Mutations autosave to data/world.json and notify registered listeners
(the web server uses that to push live WS updates).

Run directly:  python -m minepaint.mcp_server   (or via any MCP client)
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

from minepaint.core import PALETTE_DOC, World, WorldError

SERVER_NAME = "minepaint"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_WORLD_PATH = DATA_DIR / "world.json"

mcp = FastMCP(SERVER_NAME)

# --- world singleton + persistence -------------------------------------------
world = World()
_world_path = DEFAULT_WORLD_PATH
_mutation_lock = threading.Lock()
_mutation_listeners: List[Callable[[], None]] = []


def set_world_path(path: Path) -> None:
    global _world_path
    _world_path = Path(path)


def load_world_from_disk() -> None:
    if _world_path.exists():
        with open(_world_path) as f:
            world.from_json(json.load(f))


def add_mutation_listener(fn: Callable[[], None]) -> None:
    """Register a callback invoked (synchronously) after every mutation."""
    _mutation_listeners.append(fn)


def autosave() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _mutation_lock:
        with open(_world_path, "w") as f:
            json.dump(world.to_json(), f)
    return str(_world_path)


def _after_mutation() -> str:
    path = autosave()
    for fn in _mutation_listeners:
        fn()
    return path


def _summary() -> Dict[str, Any]:
    return {
        "size": list(world.size),
        "layers": [
            {"id": l.id, "name": l.name, "visible": l.visible, "order": l.order}
            for l in world.layers_sorted()
        ],
        "block_count": world.block_count,
        "entity_count": len(world.entities),
    }


def _range_doc() -> str:
    w, h, d = world.size
    return f"x in [0,{w - 1}], y in [0,{h - 1}] (y = up, ground y=0), z in [0,{d - 1}]"


load_world_from_disk()


# ------------------------------------------------------------------ tools ----
@mcp.tool
def world_info() -> Dict[str, Any]:
    """Summary of the canvas: world size, layers, block count, entity count.

    Cheap -- call this before get_state to see if the canvas changed.

    Example: world_info() -> {"size": [64,32,64], "layers": [...], "block_count": 0, "entity_count": 0}
    """
    return _summary()


@mcp.tool
def get_state() -> Dict[str, Any]:
    """Full world JSON: {size, layers, blocks, entities}.

    Blocks are [x, y, z, type, layer_id, entity_id]. Use this to read the
    whole canvas before drawing. Coordinate convention: y = up, ground y=0.

    Example: get_state() -> {"size":[64,32,64],"layers":[{"id":"house","name":"house","visible":True,"order":0}],"blocks":[[0,0,0,"grass","house",None]],"entities":[]}
    """
    return world.to_json()


@mcp.tool
def place_block(
    x: int,
    y: int,
    z: int,
    block_type: str,
    layer_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Place a single block at (x, y, z); overwrites anything already there.

    block_type must be a valid palette type (see world_info/docstring list).
    If layer_id is None the block is put on the lowest-order layer.
    If entity_id is given, the block joins that entity.

    Example: place_block(5, 0, 5, "grass", "house") -> {"placed":1,"at":[5,0,5],"type":"grass"}
    Valid palette types: """ + "; ".join(f"{k} ({v})" for k, v in sorted(PALETTE_DOC.items()))
    lid = layer_id if layer_id is not None else (sorted(world.layers, key=lambda i: world.layers[i].order) or [None])[0]
    if lid is None:
        raise WorldError(
            "No layer exists. Call create_layer(name) first, or pass layer_id."
        )
    world.place_block(x, y, z, block_type, lid, entity_id)
    _after_mutation()
    return {"placed": 1, "at": [x, y, z], "type": block_type, "layer_id": lid, "entity_id": entity_id}


@mcp.tool
def delete_block(
    x: int, y: int, z: int, layer_id: Optional[str] = None
) -> Dict[str, Any]:
    """Remove the block at (x, y, z) if present. No-op on empty air.

    Example: delete_block(5, 0, 5) -> {"deleted":1,"at":[5,0,5]}
    """
    removed = world.delete_block(x, y, z)
    _after_mutation()
    return {"deleted": 1 if removed else 0, "at": [x, y, z]}


@mcp.tool
def fill_cuboid(
    x1: int, y1: int, z1: int, x2: int, y2: int, z2: int,
    block_type: str, layer_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fill the inclusive cuboid from (x1,y1,z1) to (x2,y2,z2) with block_type.

    Corners may be given in any order. Every cell inside is set (overwrites).
    Bounds-checked: the whole cuboid must fit in the world or an error lists
    the valid range.

    Example: fill_cuboid(0, 0, 0, 3, 0, 3, "grass", "house") fills a 4x4 floor
    with grass and returns {"placed":16,"cuboid":[[0,0,0],[3,0,3]],"type":"grass"}.
    """
    lid = layer_id if layer_id is not None else (sorted(world.layers, key=lambda i: world.layers[i].order) or [None])[0]
    if lid is None:
        raise WorldError(
            "No layer exists. Call create_layer(name) first, or pass layer_id."
        )
    count = world.fill_cuboid(x1, y1, z1, x2, y2, z2, block_type, lid, entity_id)
    _after_mutation()
    return {
        "placed": count,
        "cuboid": [[x1, y1, z1], [x2, y2, z2]],
        "type": block_type,
        "layer_id": lid,
    }


@mcp.tool
def create_layer(name: str) -> str:
    """Create a new (visible) layer and return its id. Layer = paint-canvas stack.

    Blocks always belong to exactly one layer; hide/show layers to isolate
    scene parts.

    Example: create_layer("house") -> "house"
    """
    lid = world.create_layer(name)
    _after_mutation()
    return lid


@mcp.tool
def list_layers() -> List[Dict[str, Any]]:
    """All layers with id, name, visible, order (stack order, top first).

    Example: list_layers() -> [{"id":"house","name":"house","visible":True,"order":0}]
    """
    return [
        {"id": l.id, "name": l.name, "visible": l.visible, "order": l.order}
        for l in reversed(world.layers_sorted())
    ]


@mcp.tool
def set_layer_visible(layer_id: str, visible: bool) -> Dict[str, Any]:
    """Hide or show a layer. Hidden layers still exist, just not displayed.

    Example: set_layer_visible("nature", False) -> {"layer_id":"nature","visible":False}
    """
    world.set_layer_visible(layer_id, visible)
    _after_mutation()
    return {"layer_id": layer_id, "visible": bool(visible)}


@mcp.tool
def delete_layer(layer_id: str) -> Dict[str, Any]:
    """Delete a layer AND all blocks on it.

    Example: delete_layer("scrap") -> {"layer_id":"scrap","blocks_removed":12}
    """
    removed = world.delete_layer(layer_id)
    _after_mutation()
    return {"layer_id": layer_id, "blocks_removed": removed}


@mcp.tool
def create_entity(name: str) -> str:
    """Create an empty entity (named group of blocks) and return its id.

    Blocks placed with entity_id=... land in it. Entities are the copy/paste
    unit: build one object, then copy_entity/move_entity it around.

    Example: create_entity("tree") -> "tree"
    """
    eid = world.create_entity(name)
    _after_mutation()
    return eid


@mcp.tool
def list_entities() -> List[Dict[str, Any]]:
    """All entities with id, name and block count.

    Example: list_entities() -> [{"id":"tree","name":"tree","blocks":12}]
    """
    return [
        {"id": e.id, "name": e.name, "blocks": len(world.entity_blocks(e.id))}
        for e in world.entities.values()
    ]


@mcp.tool
def get_entity(entity_id: str) -> Dict[str, Any]:
    """All blocks belonging to an entity, plus its name.

    Example: get_entity("tree") -> {"id":"tree","name":"tree","blocks":[[x,y,z,type,layer_id,entity_id],...]}
    """
    entity = world._require_entity(entity_id)
    return {
        "id": entity.id,
        "name": entity.name,
        "blocks": [
            [b.x, b.y, b.z, b.type, b.layer_id, b.entity_id]
            for b in world.entity_blocks(entity_id)
        ],
    }


@mcp.tool
def copy_entity(
    entity_id: str, dx: int, dy: int, dz: int, copies: int = 1,
    layer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Copy an entity 'copies' times, each offset by (dx, dy, dz) from the source.

    Copy i is offset by (dx*i, dy*i, dz*i). New entity ids are like
    "<name>_copy1", "<name>_copy2", ... Every block is bounds-checked; if any
    lands outside the world an error lists the valid range and nothing changes.

    Example: copy_entity("tree", 10, 0, 0, copies=2) -> {"copied":"tree","new_entity_ids":["tree_copy1","tree_copy2"],"new_blocks":12}
    """
    new_ids = world.copy_entity(entity_id, dx, dy, dz, copies, layer_id)
    _after_mutation()
    total = sum(len(world.entity_blocks(i)) for i in new_ids)
    return {
        "copied": entity_id,
        "new_entity_ids": new_ids,
        "new_blocks": total,
    }


@mcp.tool
def move_entity(entity_id: str, dx: int, dy: int, dz: int) -> Dict[str, Any]:
    """Move all of an entity's blocks by (dx, dy, dz).

    Every block is bounds-checked; if any would leave the world an error lists
    the valid range and nothing moves.

    Example: move_entity("tree", 0, 0, 5) -> {"moved":"tree","blocks":12,"offset":[0,0,5]}
    """
    moved = world.move_entity(entity_id, dx, dy, dz)
    _after_mutation()
    return {"moved": entity_id, "blocks": moved, "offset": [dx, dy, dz]}


@mcp.tool
def delete_entity(entity_id: str) -> Dict[str, Any]:
    """Delete an entity AND all blocks tagged with it.

    Example: delete_entity("tree_copy1") -> {"entity_id":"tree_copy1","blocks_removed":12}
    """
    removed = world.delete_entity(entity_id)
    _after_mutation()
    return {"entity_id": entity_id, "blocks_removed": removed}


@mcp.tool
def save_world(path: Optional[str] = None) -> Dict[str, Any]:
    """Save the world to JSON. Default data/world.json.

    Example: save_world() -> {"saved_to":"data/world.json","blocks":123,"layers":2,"entities":4}
    """
    if path is not None:
        set_world_path(path)
    saved = autosave()
    return {
        "saved_to": saved,
        "blocks": world.block_count,
        "layers": len(world.layers),
        "entities": len(world.entities),
    }


@mcp.tool
def load_world(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the world from JSON. Default data/world.json.

    Example: load_world() -> {"loaded_from":"data/world.json","blocks":123,"layers":2,"entities":4}
    """
    if path is not None:
        set_world_path(path)
    load_world_from_disk()
    return {
        "loaded_from": str(_world_path),
        "blocks": world.block_count,
        "layers": len(world.layers),
        "entities": len(world.entities),
    }


@mcp.tool
def reset_world() -> Dict[str, Any]:
    """Wipe the world to a fresh empty 64x32x64 canvas (no layers/entities).

    Example: reset_world() -> {"reset":True,"size":[64,32,64]}
    """
    world.reset()
    _after_mutation()
    return {"reset": True, "size": list(world.size)}


if __name__ == "__main__":
    mcp.run()
