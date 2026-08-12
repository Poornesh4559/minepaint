"""Procedural terrain generation (pure Python, no numpy).

Generates a heightmap with ridged mountain ranges, carves a meandering
river valley, fills oceans, applies a snowline, and writes blocks into a
World. Deterministic per seed.
"""

from __future__ import annotations

import math
import random
from typing import Dict, Optional, Tuple

from minepaint.core import W, H, D, Y_MIN, Y_MAX, PALETTE

SEA_LEVEL_DEFAULT = 7
SNOWLINE_DEFAULT = 38


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
    # end on whichever edge is nearest to (sx, sz) -> walk to that edge
    edges = [
        (sx, 0), (sx, D - 1), (0, sz), (W - 1, sz),
    ]
    end = min(edges, key=lambda e: abs(e[0] - sx) + abs(e[1] - sz))
    return (sx, sz), end, rng.randint(0, 10**9)


def generate(world, seed: int = 1, *, sea_level: int = SEA_LEVEL_DEFAULT,
             snowline: int = SNOWLINE_DEFAULT, mountain_amp: float = 34.0,
             river: bool = True, rockline: Optional[int] = None) -> Dict[str, object]:
    """Replace the world with generated terrain. Returns a summary dict.

    Writes into `world` in place (terrain layer named "terrain"). The caller
    (MCP tool) is responsible for autosave/notify.

    Surface rules: sand at/under sea level, grass in the lowlands, bare stone
    slopes above the rockline (snowline - 6), snow caps (2 blocks thick)
    above the snowline. Terrain has a thin crust; deeper is air.
    """
    if rockline is None:
        rockline = snowline - 6
    # clamp (not reject) out-of-range params — LLMs pick weird values; a
    # clamped landscape beats a refused one
    sea_level = min(40, max(Y_MIN, int(sea_level)))
    snowline = min(55, max(Y_MIN, int(snowline)))
    mountain_amp = min(80.0, max(10.0, float(mountain_amp)))
    rockline = min(snowline - 1, max(Y_MIN, int(rockline)))

    # --- heightmap ---------------------------------------------------------
    heights: Dict[Tuple[int, int], float] = {}
    peak = float("-inf")
    for x in range(W):
        for z in range(D):
            base = fbm(x / 18.0, z / 18.0, seed, octaves=3)          # [-1,1]
            ridge = ridged(x / 9.0 + 500.0, z / 9.0 + 500.0, seed + 99)
            e = 6.0 + base * 7.0 + ridge * mountain_amp
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
            # meander perpendicular to the path
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
    for x in range(W):
        for z in range(D):
            h = int(round(heights[(x, z)]))
            # clamp to the world box (extreme amp can push past the ceiling)
            h = min(max(h, Y_MIN + 1), Y_MAX)
            under_water = h < sea_level
            bottom = max(Y_MIN, h - CRUST)
            # column fill (crust only)
            for y in range(bottom, h + 1):
                if y == bottom:
                    bt = "bedrock"
                elif y == h:
                    if h >= snowline:
                        bt = "snow"
                    elif h >= rockline:
                        bt = "stone"  # bare rocky slopes
                    elif under_water or h <= sea_level + 1:
                        bt = "sand"
                    else:
                        bt = "grass"
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
            # water fill
            if under_water:
                for y in range(h + 1, sea_level + 1):
                    world.place_block(x, y, z, "water", layer)
                    placed += 1
                    counts["water"] = counts.get("water", 0) + 1

    return {
        "terrain": "generated",
        "seed": seed,
        "blocks": placed,
        "peak_height": peak,
        "sea_level": sea_level,
        "snowline": snowline,
        "river": river_path,
        "by_type": counts,
    }
