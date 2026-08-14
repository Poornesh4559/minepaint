"""minepaint core world model.

Pure, deterministic, no I/O. A Minecraft-styled 3D voxel world:
- fixed grid of WORLD_SIZE = 64 (x) x 32 (y, up) x 64 (z)
- Blocks: one per cell, tagged with a layer and optionally an entity
- Layers: a named, ordered stack (the paint-canvas stack), can be hidden
- Entities: a named group of blocks -- the copy/paste unit

Everything that can go wrong raises a subclass of WorldError with a clear,
LLM-friendly message.

Thread-safety: the web server serializes/reads the world on the event loop
while FastMCP runs tool functions on worker threads, so every public method
that touches the mutable dicts takes the same re-entrant lock. Snapshotting
(to_json / blocks) is consistent under the lock.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Footprint 96x96, vertical range -32..63 (96 tall): terrain extends BELOW
# y=0 (seabed / bedrock), like Minecraft's negative-depth world.
WORLD_SIZE = (96, 96, 96)
W, H, D = WORLD_SIZE
Y_MIN = -32
Y_MAX = Y_MIN + H - 1

# Safety cap: total blocks a single copy_entity call may create. Matches the
# shape voxelizer's MAX_BLOCKS_PER_CALL so an LLM can't OOM the server by
# copying a giant entity 1000x.
MAX_COPY_BLOCKS = 250_000


class WorldError(Exception):
    """Base class for all minepaint world errors."""


class OutOfBoundsError(WorldError):
    """A coordinate fell outside the world grid."""


class UnknownBlockTypeError(WorldError):
    """block_type not in the fixed palette."""


class UnknownLayerError(WorldError):
    """layer_id does not exist."""


class UnknownEntityError(WorldError):
    """entity_id does not exist."""


# FIXED palette. The LLM may only use these block types.
# Short descriptions are in PALETTE_DOC so tools can expose them.
PALETTE = {
    "grass": "#5B9E3A",
    "dirt": "#8B5A2B",
    "stone": "#7D7D7D",
    "cobblestone": "#6E6E6E",
    "oak_log": "#6B4423",
    "oak_planks": "#A9824B",
    "oak_leaves": "#3A7D28",
    "sand": "#E8D48B",
    "water": "#3B6FD4",
    "glass": "#BFE0FF",
    "brick": "#9C4A3C",
    "snow": "#F0F0F0",
    "lava": "#E8651A",
    "bedrock": "#3A3A3A",
    "gold_ore": "#E0B72C",
    "diamond_ore": "#5FDBD0",
    "netherrack": "#7A2226",
    "cactus": "#2E8B3D",
}

PALETTE_DOC = {
    "grass": "green top block, good for ground",
    "dirt": "brown soil, use under grass or for paths",
    "stone": "plain gray rock",
    "cobblestone": "rough gray stone, good for walls/floors",
    "oak_log": "brown wood trunk, good for tree trunks",
    "oak_planks": "light wood planks, good for floors and roofs",
    "oak_leaves": "green foliage, good for tree canopies",
    "sand": "pale yellow, good for beaches/deserts",
    "water": "translucent blue, good for moats/ponds",
    "glass": "translucent pale blue, good for windows",
    "brick": "red-brown brick, good for houses",
    "snow": "white, good for snowy ground",
    "lava": "translucent orange, good for hazards/moats",
    "bedrock": "dark gray unbreakable base",
    "gold_ore": "stone with yellow flecks, decoration",
    "diamond_ore": "stone with cyan flecks, decoration",
    "netherrack": "dark red, nether ground",
    "cactus": "green spiky plant",
}


def _bounds_error(x: int, y: int, z: int) -> OutOfBoundsError:
    return OutOfBoundsError(
        f"({x},{y},{z}) is out of bounds. Valid range: "
        f"x in [0,{W - 1}], y in [{Y_MIN},{Y_MAX}] (y = up; ground near y=0; "
        f"seabed/bedrock below), z in [0,{D - 1}]."
    )


def check_bounds(x: int, y: int, z: int) -> None:
    if not (0 <= x < W and Y_MIN <= y <= Y_MAX and 0 <= z < D):
        raise _bounds_error(x, y, z)


def _check_bounds_int(v: Any, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise WorldError(f"{name} must be an integer, got {v!r}")
    return v


@dataclass
class Block:
    x: int
    y: int
    z: int
    type: str
    layer_id: str
    entity_id: Optional[str] = None

    def key(self) -> Tuple[int, int, int]:
        return (self.x, self.y, self.z)


@dataclass
class Layer:
    id: str
    name: str
    visible: bool = True
    order: int = 0


@dataclass
class Entity:
    id: str
    name: str


# Vanilla-Minecraft block names LLMs commonly invent; mapped to the closest
# palette equivalent so a stray 'iron_block' builds instead of failing.
BLOCK_ALIASES = {
    "iron_block": "stone",
    "iron": "stone",
    "smooth_stone": "stone",
    "stone_bricks": "cobblestone",
    "stonebrick": "cobblestone",
    "cobbled_deepslate": "cobblestone",
    "deepslate": "stone",
    "planks": "oak_planks",
    "oak_wood": "oak_log",
    "wood": "oak_planks",
    "white_wool": "snow",
    "black_wool": "stone",
    "gray_wool": "stone",
    "grey_wool": "stone",
    "orange_wool": "brick",
    "yellow_wool": "sand",
    "red_wool": "brick",
    "blue_wool": "water",
    "green_wool": "grass",
    "brown_wool": "dirt",
    "wool": "oak_planks",
    "concrete": "stone",
    "white_concrete": "snow",
    "sandstone": "sand",
    "gold_block": "gold_ore",
    "diamond_block": "diamond_ore",
    "glass_pane": "glass",
    "ice": "glass",
    "obsidian": "bedrock",
    "redstone_block": "netherrack",
    "nether_bricks": "netherrack",
    "gravel": "stone",
    "clay": "dirt",
}


def resolve_block_type(block_type: str) -> str:
    """Return the palette block for a user/LLM-supplied name, applying
    vanilla-Minecraft aliases. Unknown names raise UnknownBlockTypeError."""
    if block_type in PALETTE:
        return block_type
    alias = BLOCK_ALIASES.get(block_type)
    if alias is not None:
        return alias
    raise UnknownBlockTypeError(
        f"Unknown block type {block_type!r}. Allowed types "
        f"(with descriptions): {PALETTE_DOC}."
    )


class World:
    """96 x 96 x 96 voxel grid (y from -32 to 63) with layers and entities."""

    def __init__(self) -> None:
        self.size: Tuple[int, int, int] = WORLD_SIZE
        self._blocks: Dict[Tuple[int, int, int], Block] = {}
        self.layers: Dict[str, Layer] = {}
        self.entities: Dict[str, Entity] = {}
        self._next_layer_order = 0
        # Re-entrant so nested public calls (e.g. copy_entity -> entity_blocks)
        # work while the outer call holds the lock.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ ids
    def _unique_layer_id(self, name: str) -> str:
        base, n = name, 2
        while base in self.layers:
            base = f"{name}_{n}"
            n += 1
        return base

    def _unique_entity_id(self, name: str) -> str:
        base, n = name, 2
        while base in self.entities:
            base = f"{name}_{n}"
            n += 1
        return base

    # ------------------------------------------------------------ validation
    def _require_layer(self, layer_id: str) -> Layer:
        layer = self.layers.get(layer_id)
        if layer is None:
            raise UnknownLayerError(
                f"Layer {layer_id!r} does not exist. "
                f"Existing layers: {sorted(self.layers) or 'none (create one first with create_layer)'}."
            )
        return layer

    def _require_entity(self, entity_id: str) -> Entity:
        entity = self.entities.get(entity_id)
        if entity is None:
            raise UnknownEntityError(
                f"Entity {entity_id!r} does not exist. "
                f"Existing entities: {sorted(self.entities) or 'none (create one first with create_entity)'}."
            )
        return entity

    def _require_type(self, block_type: str) -> str:
        return resolve_block_type(block_type)

    def _validate_place(
        self, x: int, y: int, z: int, block_type: str, layer_id: str,
        entity_id: Optional[str] = None,
    ) -> str:
        x, y, z = _check_bounds_int(x, "x"), _check_bounds_int(y, "y"), _check_bounds_int(z, "z")
        check_bounds(x, y, z)
        self._require_type(block_type)
        self._require_layer(layer_id)
        if entity_id is not None:
            self._require_entity(entity_id)
        return self._require_type(block_type)

    # --------------------------------------------------------------- blocks
    def block_at(self, x: int, y: int, z: int) -> Optional[Block]:
        with self._lock:
            return self._blocks.get((x, y, z))

    def place_block(
        self, x: int, y: int, z: int, block_type: str, layer_id: str,
        entity_id: Optional[str] = None,
    ) -> Block:
        """Place one block, overwriting anything already at that cell."""
        block_type = self._validate_place(x, y, z, block_type, layer_id, entity_id)
        block = Block(x=x, y=y, z=z, type=block_type, layer_id=layer_id, entity_id=entity_id)
        with self._lock:
            self._blocks[block.key()] = block
        return block

    def delete_block(self, x: int, y: int, z: int) -> Optional[Block]:
        x, y, z = _check_bounds_int(x, "x"), _check_bounds_int(y, "y"), _check_bounds_int(z, "z")
        check_bounds(x, y, z)
        with self._lock:
            return self._blocks.pop((x, y, z), None)

    def fill_cuboid(
        self, x1: int, y1: int, z1: int, x2: int, y2: int, z2: int,
        block_type: str, layer_id: str, entity_id: Optional[str] = None,
    ) -> int:
        """Fill the inclusive cuboid (x1..x2, y1..y2, z1..z2). Returns # blocks placed."""
        for v, name in ((x1, "x1"), (y1, "y1"), (z1, "z1"), (x2, "x2"), (y2, "y2"), (z2, "z2")):
            _check_bounds_int(v, name)
        lo_x, hi_x = sorted((x1, x2))
        lo_y, hi_y = sorted((y1, y2))
        lo_z, hi_z = sorted((z1, z2))
        for x in (lo_x, hi_x):
            if not 0 <= x < W:
                raise _bounds_error(x, lo_y, lo_z)
        for y in (lo_y, hi_y):
            if not Y_MIN <= y <= Y_MAX:
                raise _bounds_error(lo_x, y, lo_z)
        for z in (lo_z, hi_z):
            if not 0 <= z < D:
                raise _bounds_error(lo_x, lo_y, z)
        block_type = self._require_type(block_type)
        self._require_layer(layer_id)
        if entity_id is not None:
            self._require_entity(entity_id)
        count = 0
        with self._lock:
            for x in range(lo_x, hi_x + 1):
                for y in range(lo_y, hi_y + 1):
                    for z in range(lo_z, hi_z + 1):
                        self._blocks[(x, y, z)] = Block(x, y, z, block_type, layer_id, entity_id)
                        count += 1
        return count

    def blocks_in_layer(self, layer_id: str) -> List[Block]:
        with self._lock:
            return [b for b in self._blocks.values() if b.layer_id == layer_id]

    def entity_blocks(self, entity_id: str) -> List[Block]:
        self._require_entity(entity_id)
        with self._lock:
            return [b for b in self._blocks.values() if b.entity_id == entity_id]

    def blocks_by_type(self, block_type: str) -> List[Block]:
        with self._lock:
            return [b for b in self._blocks.values() if b.type == block_type]

    @property
    def blocks(self) -> List[Block]:
        with self._lock:
            return sorted(self._blocks.values(), key=lambda b: (b.x, b.y, b.z))

    @property
    def block_count(self) -> int:
        return len(self._blocks)

    # --------------------------------------------------------------- layers
    def create_layer(self, name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise WorldError("Layer name must be a non-empty string.")
        with self._lock:
            lid = self._unique_layer_id(name)
            self.layers[lid] = Layer(id=lid, name=name, visible=True, order=self._next_layer_order)
            self._next_layer_order += 1
        return lid

    def delete_layer(self, layer_id: str) -> int:
        self._require_layer(layer_id)
        with self._lock:
            removed = len(self.blocks_in_layer(layer_id))
            self._blocks = {k: b for k, b in self._blocks.items() if b.layer_id != layer_id}
            del self.layers[layer_id]
        return removed

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        with self._lock:
            self._require_layer(layer_id).visible = bool(visible)

    def layers_sorted(self) -> List[Layer]:
        with self._lock:
            return sorted(self.layers.values(), key=lambda l: (l.order, l.id))

    # ------------------------------------------------------------- entities
    def create_entity(self, name: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise WorldError("Entity name must be a non-empty string.")
        with self._lock:
            eid = self._unique_entity_id(name)
            self.entities[eid] = Entity(id=eid, name=name)
        return eid

    def copy_entity(
        self, entity_id: str, dx: int, dy: int, dz: int, copies: int = 1,
        layer_id: Optional[str] = None,
    ) -> List[str]:
        """Duplicate an entity's blocks, offset per copy. Hero feature."""
        entity = self._require_entity(entity_id)
        for v, name in ((dx, "dx"), (dy, "dy"), (dz, "dz")):
            _check_bounds_int(v, name)
        if not isinstance(copies, int) or isinstance(copies, bool) or copies < 1:
            raise WorldError("copies must be an integer >= 1.")
        if copies > 1000:
            raise WorldError("copies must be <= 1000.")
        target_layer = layer_id if layer_id is not None else None
        if layer_id is not None:
            self._require_layer(layer_id)
        with self._lock:
            source = self.entity_blocks(entity_id)
            if not source:
                raise WorldError(f"Entity {entity_id!r} has no blocks to copy.")
            total_blocks = len(source) * copies
            if total_blocks > MAX_COPY_BLOCKS:
                raise WorldError(
                    f"copy would create {total_blocks} blocks (entity {entity_id!r} has "
                    f"{len(source)} blocks x {copies} copies); max {MAX_COPY_BLOCKS} per "
                    f"call — copy in smaller batches."
                )
            # validate every target cell BEFORE mutating (atomicity), and reject
            # offsets that land on the source or an earlier copy (that would
            # silently steal blocks between entities).
            used: set = set()
            for b in source:
                used.add(b.key())
            targets: List[List[Tuple[int, int, int]]] = []
            for i in range(1, copies + 1):
                cell_list = []
                for b in source:
                    nx, ny, nz = b.x + dx * i, b.y + dy * i, b.z + dz * i
                    check_bounds(nx, ny, nz)
                    if (nx, ny, nz) in used:
                        raise WorldError(
                            f"copy offset ({dx},{dy},{dz}) overlaps the source entity's own "
                            f"blocks or an earlier copy — nothing copied. Choose a larger "
                            f"offset so copies don't collide."
                        )
                    cell_list.append((nx, ny, nz))
                used.update(cell_list)
                targets.append(cell_list)
            new_ids: List[str] = []
            for i, cell_list in enumerate(targets, start=1):
                nid = self._unique_entity_id(f"{entity.name}_copy{i}")
                new_entity = Entity(id=nid, name=f"{entity.name}_copy{i}")
                self.entities[nid] = new_entity
                for b, (nx, ny, nz) in zip(source, cell_list):
                    self._blocks[(nx, ny, nz)] = Block(
                        nx, ny, nz, b.type, b.layer_id if target_layer is None else target_layer, nid
                    )
                new_ids.append(nid)
            return new_ids

    def move_entity(self, entity_id: str, dx: int, dy: int, dz: int) -> int:
        entity = self._require_entity(entity_id)
        for v, name in ((dx, "dx"), (dy, "dy"), (dz, "dz")):
            _check_bounds_int(v, name)
        with self._lock:
            source = self.entity_blocks(entity_id)
            if not source:
                return 0
            moved: List[Tuple[Block, Tuple[int, int, int]]] = []
            conflicts = 0
            for b in source:
                nx, ny, nz = b.x + dx, b.y + dy, b.z + dz
                check_bounds(nx, ny, nz)
                occupant = self._blocks.get((nx, ny, nz))
                if occupant is not None and occupant is not b:
                    conflicts += 1
                moved.append((b, (nx, ny, nz)))
            if conflicts:
                raise WorldError(
                    f"move would overwrite {conflicts} existing block(s) — nothing moved. "
                    f"Use a larger offset, or delete/move the blocking blocks first."
                )
            for b, (nx, ny, nz) in moved:
                del self._blocks[b.key()]
                b.x, b.y, b.z = nx, ny, nz
                self._blocks[(nx, ny, nz)] = b
            return len(moved)

    def delete_entity(self, entity_id: str) -> int:
        self._require_entity(entity_id)
        with self._lock:
            removed = len(self.entity_blocks(entity_id))
            self._blocks = {k: b for k, b in self._blocks.items() if b.entity_id != entity_id}
            del self.entities[entity_id]
        return removed

    # ------------------------------------------------------------- resetting
    def reset(self) -> None:
        with self._lock:
            self._blocks.clear()
            self.layers.clear()
            self.entities.clear()
            self._next_layer_order = 0

    # ------------------------------------------------------------ serializing
    def to_json(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "size": list(self.size),
                "y_min": Y_MIN,
                "layers": [
                    {"id": l.id, "name": l.name, "visible": l.visible, "order": l.order}
                    for l in self.layers_sorted()
                ],
                "blocks": [
                    [b.x, b.y, b.z, b.type, b.layer_id, b.entity_id] for b in self.blocks
                ],
                "entities": [{"id": e.id, "name": e.name} for e in self.entities.values()],
            }

    def from_json(self, data: Dict[str, Any]) -> None:
        """Populate THIS world from a to_json() dict (in place).

        In-place so callers holding a reference to the singleton (e.g. the
        web server's imported `world`) see the loaded state.
        """
        if data.get("size") is not None and list(data["size"]) != list(WORLD_SIZE):
            raise WorldError(
                f"World file has size {data['size']} but this build only supports {WORLD_SIZE}."
            )
        with self._lock:
            self._blocks.clear()
            self.layers.clear()
            self.entities.clear()
            self._next_layer_order = 0
            for l in data.get("layers", []):
                self.layers[l["id"]] = Layer(
                    id=l["id"], name=l.get("name", l["id"]),
                    visible=bool(l.get("visible", True)), order=int(l.get("order", 0)),
                )
                self._next_layer_order = max(self._next_layer_order, int(l.get("order", 0)) + 1)
            for e in data.get("entities", []):
                self.entities[e["id"]] = Entity(id=e["id"], name=e.get("name", e["id"]))
            for row in data.get("blocks", []):
                x, y, z, block_type, layer_id = row[0], row[1], row[2], row[3], row[4]
                entity_id = row[5] if len(row) > 5 else None
                self._validate_place(x, y, z, block_type, layer_id, entity_id)
                self._blocks[(x, y, z)] = Block(x, y, z, block_type, layer_id, entity_id)
