"""Procedural terrain generation (pure Python, no numpy).

Generates a heightmap with ridged mountain ranges, carves a meandering
river valley, fills oceans, applies a snowline, and writes blocks into a
World. Deterministic per seed.

Styles give genuinely different landscapes (desert dunes, flat-topped
mesas, volcanic wastes with lava lakes, tropical islands...) so the LLM can
paint varied scenes. Terrain has a thin crust; deeper is air.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

from minepaint.core import W, H, D, Y_MIN, Y_MAX, PALETTE, Block

SEA_LEVEL_DEFAULT = 7
SNOWLINE_DEFAULT = 38

# ------------------------------------------------------------ style presets
# surface modes:
#   climatic -> sand near water, grass lowlands, rocky slopes, snow caps
#   sand     -> desert dunes (sand everywhere, stone outcrops high up)
#   mesa     -> stepped plateaus with flat sand tops and rock cliffs
#   nether   -> netherrack everywhere, lava instead of water
STYLES = {
    "snowy_mountains": {"sea_level": 12, "snowline": 26, "mountain_amp": 44.0, "surface": "climatic"},
    "rolling_hills":   {"sea_level": 8,  "snowline": 99, "mountain_amp": 16.0, "surface": "climatic"},
    "river_valley":    {"sea_level": 10, "snowline": 32, "mountain_amp": 28.0, "surface": "climatic"},
    "islands":         {"sea_level": 16, "snowline": 28, "mountain_amp": 28.0, "surface": "climatic"},
    "desert":          {"sea_level": 5,  "snowline": 99, "mountain_amp": 22.0, "surface": "sand"},
    "mesa":            {"sea_level": 6,  "snowline": 99, "mountain_amp": 26.0, "surface": "mesa"},
    "volcanic":        {"sea_level": 8,  "snowline": 99, "mountain_amp": 34.0, "surface": "nether"},
    "tropical":        {"sea_level": 14, "snowline": 99, "mountain_amp": 26.0, "surface": "climatic"},
}


# ---------------------------------------------------------------- value noise
def _hash(x: int, y: int, seed: int) -> float:
    """Deterministic 32-bit-ish hash of (x, y, seed) -> [0, 1)."""
    h = (x * 374761393 + y * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return h / 0xFFFFFFFF


def _value_noise(x: float, y: float, seed: int) -> float:
    xi, yi = math.floor(x), math.floor(y)
    xf, yf = x - xi, y - yi
    u = xf * xf * (3 - 2 * xf)
    v = yf * yf * (3 - 2 * yf)
    a = _hash(xi, yi, seed)
    b = _hash(xi + 1, yi, seed)
    c = _hash(xi, yi + 1, seed)
    d = _hash(xi + 1, yi + 1, seed)
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v


def fbm(x: float, y: float, seed: int, octaves: int = 3, lacunarity: float = 2.0,
        gain: float = 0.5) -> float:
    """Fractional Brownian motion, roughly in [-1, 1]."""
    amp, freq, total, norm = 1.0, 1.0, 0.0, 0.0
    for _ in range(octaves):
        total += amp * _value_noise(x * freq, y * freq, seed)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


def ridged(x: float, y: float, seed: int, octaves: int = 3) -> float:
    """Ridged noise (sharp mountain crests), in [0, 1]."""
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for _ in range(octaves):
        total += amp * (1.0 - abs(_value_noise(x * freq, y * freq, seed)))
        norm += amp
        amp *= 0.5
        freq *= 2.0
    r = total / norm
    return r * r * r  # sharpen the crests


# ------------------------------------------------------------------ terrain
def _river_path(seed: int) -> Tuple[Tuple[int, int], Tuple[int, int], int]:
    """Pick a (start, end, wobble_seed) for the river: from high ground to an edge."""
    rng = random.Random(seed + 7)
    sx = rng.randint(W // 3, 2 * W // 3)
    sz = rng.randint(D // 3, 2 * D // 3)
    edges = [
        (sx, 0), (sx, D - 1), (0, sz), (W - 1, sz),
    ]
    end = min(edges, key=lambda e: abs(e[0] - sx) + abs(e[1] - sz))
    return (sx, sz), end, rng.randint(0, 10**9)


def _surface_block(mode: str, h: int, sea_level: int, snowline: int,
                   rockline: int, under_water: bool) -> str:
    """Pick the surface block for a column top, per style surface mode."""
    if mode == "sand":          # desert dunes; stone outcrops very high up
        return "stone" if h >= 34 else "sand"
    if mode == "mesa":          # flat sand tops, rock cliff sides, sand floor
        if h >= 26:
            return "sand"
        if h >= 12:
            return "stone"
        return "sand"
    if mode == "nether":        # volcanic wasteland
        return "netherrack"
    # climatic
    if h >= snowline:
        return "snow"
    if h >= rockline:
        return "stone"
    if under_water or h <= sea_level + 1:
        return "sand"
    return "grass"


def generate(world, seed: int = 1, *, style: str = "snowy_mountains",
             sea_level: Optional[int] = None, snowline: Optional[int] = None,
             mountain_amp: Optional[float] = None, river: bool = True,
             rockline: Optional[int] = None) -> Dict[str, object]:
    """Replace the world with generated terrain. Returns a summary dict.

    style picks a preset (STYLES dict); any explicitly-passed param overrides
    that preset. Params are clamped (not rejected) — a clamped landscape
    beats a refused one. Writes into `world` in place (layer "terrain").
    """
    preset = STYLES.get(style, STYLES["snowy_mountains"])
    surface_mode = preset["surface"]
    sea_level = preset["sea_level"] if sea_level is None else int(sea_level)
    snowline = preset["snowline"] if snowline is None else int(snowline)
    mountain_amp = preset["mountain_amp"] if mountain_amp is None else float(mountain_amp)

    sea_level = min(40, max(Y_MIN, sea_level))
    snowline = min(55, max(Y_MIN, snowline))
    mountain_amp = min(80.0, max(10.0, mountain_amp))
    if rockline is None:
        rockline = snowline - 6
    rockline = min(snowline - 1, max(Y_MIN, int(rockline)))

    mesa = surface_mode == "mesa"
    dune_base = 8 if surface_mode == "sand" else 6

    # --- heightmap ---------------------------------------------------------
    heights: Dict[Tuple[int, int], float] = {}
    peak = float("-inf")
    for x in range(W):
        for z in range(D):
            base = fbm(x / 18.0, z / 18.0, seed, octaves=3)          # [-1,1]
            ridge = ridged(x / 9.0 + 500.0, z / 9.0 + 500.0, seed + 99)
            e = dune_base + base * 7.0 + ridge * mountain_amp
            if mesa:
                e = round(e / 8.0) * 8.0          # stepped plateau tops
            heights[(x, z)] = e
            peak = max(peak, e)

    # --- river: carve a valley from high ground to an edge -----------------
    river_path: Tuple[int, int] | None = None
    if river:
        (sx, sz), (ex, ez), wob = _river_path(seed)
        river_path = (sx, sz)
        steps = max(abs(ex - sx), abs(ez - sz)) or 1
        for i in range(steps + 1):
            t = i / steps
            bx, bz = sx + (ex - sx) * t, sz + (ez - sz) * t
            off = (fbm(t * 3.0, wob, wob, octaves=2) - 0.5) * 2.0
            px, pz = int(bx + off * 5.0), int(bz - off * 5.0)
            for dx in range(-2, 3):
                for dz in range(-2, 3):
                    dist = dx * dx + dz * dz
                    cx, cz = px + dx, pz + dz
                    if 0 <= cx < W and 0 <= cz < D:
                        cur = heights[(cx, cz)]
                        if dist <= 1:
                            target = sea_level - 2 + _hash(cx, cz, wob) * 2.0
                        else:
                            target = cur - 3.0  # soft valley shoulders
                        heights[(cx, cz)] = min(cur, target)

    # --- write blocks ------------------------------------------------------
    world.reset()
    layer = world.create_layer("terrain")
    placed = 0
    counts: Dict[str, int] = {}
    CRUST = 8  # solid blocks under the surface; deeper is air (invisible)
    liquid = "lava" if surface_mode == "nether" else "water"
    for x in range(W):
        for z in range(D):
            h = int(round(heights[(x, z)]))
            # clamp to the world box (extreme amp can push past the ceiling)
            h = min(max(h, Y_MIN + 1), Y_MAX)
            under_water = h < sea_level
            bottom = max(Y_MIN, h - CRUST)
            for y in range(bottom, h + 1):
                if y == bottom:
                    bt = "bedrock"
                elif y == h:
                    bt = _surface_block(surface_mode, h, sea_level, snowline,
                                        rockline, under_water)
                elif surface_mode == "nether":
                    bt = "netherrack"
                elif h >= snowline and y >= h - 1:
                    bt = "snow"  # 2-block snow caps
                elif h >= rockline and y >= h - 2:
                    bt = "stone"  # rock under the cap, no dirt band
                elif y >= h - 2:
                    bt = "dirt"
                else:
                    bt = "stone"
                world.place_block(x, y, z, bt, layer)
                placed += 1
                counts[bt] = counts.get(bt, 0) + 1
            if under_water:
                for y in range(h + 1, sea_level + 1):
                    world.place_block(x, y, z, liquid, layer)
                    placed += 1
                    counts[liquid] = counts.get(liquid, 0) + 1

    return {
        "terrain": "generated",
        "style": style,
        "seed": seed,
        "blocks": placed,
        "peak_height": peak,
        "sea_level": sea_level,
        "snowline": snowline,
        "mountain_amp": mountain_amp,
        "river": river_path,
        "by_type": counts,
    }


# ------------------------------------------------- surface helpers (O(1) via cache)
def _column_cache(world) -> Dict[Tuple[int, int], Block]:
    """Map (x, z) -> topmost block at that column."""
    cols: Dict[Tuple[int, int], Block] = {}
    for b in world._blocks.values():
        cur = cols.get((b.x, b.z))
        if cur is None or b.y > cur.y:
            cols[(b.x, b.z)] = b
    return cols


def _build_road(world, x1: int, z1: int, x2: int, z2: int, width: int = 2,
                block_type: str = "dirt", layer_id: Optional[str] = None) -> int:
    """Lay a height-following path from (x1,z1) to (x2,z2). Returns # placed."""
    cols = _column_cache(world)
    steps = max(abs(x2 - x1), abs(z2 - z1)) or 1
    placed = 0
    for i in range(steps + 1):
        t = i / steps
        cx = round(x1 + (x2 - x1) * t)
        cz = round(z1 + (z2 - z1) * t)
        for dw in range(-(width // 2), (width // 2) + 1):
            if abs(x2 - x1) >= abs(z2 - z1):
                px, pz = cx, cz + dw
            else:
                px, pz = cx + dw, cz
            if not (0 <= px < W and 0 <= pz < D):
                continue
            top = cols.get((px, pz))
            if top is None:
                continue
            if top.type in ("water", "lava"):
                continue  # don't pave over liquid
            for y in (top.y, top.y - 1):
                if Y_MIN <= y <= Y_MAX:
                    world.place_block(px, y, pz, block_type, layer_id)
                    placed += 1
    return placed


def _scatter(world, block_type: str, count: int, x1: int, z1: int, x2: int, z2: int,
             min_spacing: int, layer_id: Optional[str], allow_surfaces: set) -> int:
    cols = _column_cache(world)
    rng = random.Random(block_type)
    spots: List[Tuple[int, int, int]] = []
    x1, x2 = sorted((max(0, x1), min(W - 1, x2)))
    z1, z2 = sorted((max(0, z1), min(D - 1, z2)))
    attempts = 0
    while len(spots) < count and attempts < count * 40:
        attempts += 1
        x, z = rng.randint(x1, x2), rng.randint(z1, z2)
        top = cols.get((x, z))
        if top is None or top.type not in allow_surfaces:
            continue
        if top.y < 4 or top.y > Y_MAX - 8:
            continue
        if all((x - sx) ** 2 + (z - sz) ** 2 >= min_spacing ** 2 for sx, sy, sz in spots):
            spots.append((x, top.y, z))
    for x, y, z in spots:
        world.place_block(x, y, z, block_type, layer_id)
    return len(spots)


def _pine(world, x: int, y: int, z: int, layer: str, eid: str) -> int:
    for i in range(3):
        world.place_block(x, y + i, z, "oak_log", layer, eid)
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            world.place_block(x + dx, y + 3, z + dz, "oak_leaves", layer, eid)
            if abs(dx) + abs(dz) <= 1:
                world.place_block(x + dx, y + 4, z + dz, "oak_leaves", layer, eid)
    world.place_block(x, y + 5, z, "oak_leaves", layer, eid)
    return 3 + 9 + 5 + 1


def _oak(world, x: int, y: int, z: int, layer: str, eid: str) -> int:
    n = 0
    for i in range(4):
        world.place_block(x, y + i, z, "oak_log", layer, eid)
        n += 1
    for dy in range(3, 6):
        r = 1 if dy < 5 else 0
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if dx == 0 and dz == 0 and dy < 5:
                    continue
                world.place_block(x + dx, y + dy, z + dz, "oak_leaves", layer, eid)
                n += 1
    world.place_block(x, y + 5, z, "oak_leaves", layer, eid)
    n += 1
    return n


def _scatter_trees(world, tree_type: str, count: int, x1: int, z1: int, x2: int, z2: int,
                   layer_id: str) -> Tuple[int, List[str]]:
    cols = _column_cache(world)
    rng = random.Random(tree_type)
    spots: List[Tuple[int, int, int]] = []
    x1, x2 = sorted((max(0, x1), min(W - 1, x2)))
    z1, z2 = sorted((max(0, z1), min(D - 1, z2)))
    attempts = 0
    while len(spots) < count and attempts < count * 40:
        attempts += 1
        x, z = rng.randint(x1, x2), rng.randint(z1, z2)
        top = cols.get((x, z))
        if top is None or top.type not in ("grass", "dirt", "sand"):
            continue
        if top.y < 4 or top.y > Y_MAX - 8:
            continue
        if all((x - sx) ** 2 + (z - sz) ** 2 >= 36 for sx, sy, sz in spots):
            spots.append((x, top.y, z))
    total = 0
    ids: List[str] = []
    for i, (x, y, z) in enumerate(spots):
        eid = world.create_entity(f"{tree_type}_{i + 1}")
        ids.append(eid)
        if tree_type == "oak":
            total += _oak(world, x, y + 1, z, layer_id, eid)
        else:
            total += _pine(world, x, y + 1, z, layer_id, eid)
    return total, ids
