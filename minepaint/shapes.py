"""Parametric 3D object builders + SDF primitive voxelizer.

The LLM describes shapes (named objects or primitive compositions); this
module does the geometry and writes blocks into the world. Every object
lands as an entity so copy_entity / move_entity keep working.

Primitives (all positions in world block coords, voxel centers at +0.5):
- cylinder: {"shape":"cylinder","from":[x,y,z],"to":[x,y,z],"r":R,"r2":R2,"m":...}
             r = radius at `from`, r2 = radius at `to` (r2=0 => cone)
- sphere:   {"shape":"sphere","center":[...],"r":R,"m":...}
- ellipsoid:{"shape":"ellipsoid","center":[...],"r":[rx,ry,rz],"rot":[yaw,pitch,roll],"m":...}
- box:      {"shape":"box","center":[...],"r":[hx,hy,hz],"rot":[...],"m":...}
- torus:    {"shape":"torus","center":[...],"R":major,"r":minor,"m":...} (flat in XZ)

Solid interiors are fully filled. Cells outside the world are skipped
(objects may hang off the edge). Total blocks per call is capped.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Tuple

from minepaint.core import W, D, Y_MIN, Y_MAX

MAX_BLOCKS_PER_CALL = 250_000


# ------------------------------------------------------------ rotation math
def _rot_matrix(rot: List[float]) -> List[List[float]]:
    """Rotation matrix R = Ry(yaw) @ Rx(pitch) @ Rz(roll), degrees."""
    yaw, pitch, roll = (math.radians(v) for v in (rot or (0.0, 0.0, 0.0)))
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    return [
        [cy * cr + sy * sp * sr, -cy * sr + sy * sp * cr, sy * cp],
        [cp * sr, cp * cr, -sp],
        [-sy * cr + cy * sp * sr, sy * sr + cy * sp * cr, cy * cp],
    ]


def _local_half_extents(prim: Dict[str, Any]) -> List[float]:
    kind = prim["shape"]
    if kind == "sphere":
        r = prim["r"]
        return [r, r, r]
    if kind in ("ellipsoid", "box"):
        r = prim["r"]
        rot = prim.get("rot")
        if not rot:
            return [r[0], r[1], r[2]]
        R = _rot_matrix(rot)
        return [
            abs(R[0][0]) * r[0] + abs(R[1][0]) * r[1] + abs(R[2][0]) * r[2],
            abs(R[0][1]) * r[0] + abs(R[1][1]) * r[1] + abs(R[2][1]) * r[2],
            abs(R[0][2]) * r[0] + abs(R[1][2]) * r[1] + abs(R[2][2]) * r[2],
        ]
    if kind == "cylinder":
        rmax = max(prim.get("r", 1.0), prim.get("r2", prim.get("r", 1.0)))
        return [rmax, rmax, rmax]
    if kind == "torus":
        R, r = prim["R"], prim["r"]
        return [R + r, r, R + r]
    raise ValueError(f"unknown shape {kind!r}")


def _inside(x: int, y: int, z: int, prim: Dict[str, Any]) -> bool:
    px, py, pz = x + 0.5, y + 0.5, z + 0.5
    c = prim.get("center", (0.0, 0.0, 0.0))
    px -= c[0]
    py -= c[1]
    pz -= c[2]
    kind = prim["shape"]

    if kind == "cylinder":
        f = prim["from"]
        d = (prim["to"][0] - f[0], prim["to"][1] - f[1], prim["to"][2] - f[2])
        L2 = d[0] * d[0] + d[1] * d[1] + d[2] * d[2]
        if L2 <= 0:
            return False
        qx, qy, qz = px - (f[0] - c[0]), py - (f[1] - c[1]), pz - (f[2] - c[2])
        t = (qx * d[0] + qy * d[1] + qz * d[2]) / L2
        if t < 0.0 or t > 1.0:
            return False
        r1 = prim.get("r", 1.0)
        rr = r1 + (prim.get("r2", r1) - r1) * t
        radial2 = qx * qx + qy * qy + qz * qz - t * t * L2
        return radial2 <= rr * rr

    if kind == "sphere":
        r = prim["r"]
        return px * px + py * py + pz * pz <= r * r

    if kind == "torus":
        R, r = prim["R"], prim["r"]
        d = math.sqrt(px * px + pz * pz)
        return (d - R) * (d - R) + py * py <= r * r

    # ellipsoid / box: rotate into local space
    rot = prim.get("rot")
    if rot:
        R = _rot_matrix(rot)
        # p_local = R^T p
        lx = R[0][0] * px + R[1][0] * py + R[2][0] * pz
        ly = R[0][1] * px + R[1][1] * py + R[2][1] * pz
        lz = R[0][2] * px + R[1][2] * py + R[2][2] * pz
        px, py, pz = lx, ly, lz
    if kind == "ellipsoid":
        rx, ry, rz = prim["r"]
        return (px / rx) ** 2 + (py / ry) ** 2 + (pz / rz) ** 2 <= 1.0
    if kind == "box":
        hx, hy, hz = prim["r"]
        return abs(px) <= hx and abs(py) <= hy and abs(pz) <= hz
    raise ValueError(f"unknown shape {kind!r}")


def _voxelize_prim(world, prim: Dict[str, Any], layer: str, eid: str) -> int:
    c = prim.get("center", (0.0, 0.0, 0.0))
    ext = _local_half_extents(prim)
    if prim["shape"] == "cylinder":
        f, t = prim["from"], prim["to"]
        rmax = max(prim.get("r", 1.0), prim.get("r2", prim.get("r", 1.0)))
        x0 = math.floor(min(f[0], t[0]) - rmax)
        x1 = math.ceil(max(f[0], t[0]) + rmax)
        y0 = math.floor(min(f[1], t[1]) - rmax)
        y1 = math.ceil(max(f[1], t[1]) + rmax)
        z0 = math.floor(min(f[2], t[2]) - rmax)
        z1 = math.ceil(max(f[2], t[2]) + rmax)
    else:
        x0 = math.floor(c[0] - ext[0])
        x1 = math.ceil(c[0] + ext[0])
        y0 = math.floor(c[1] - ext[1])
        y1 = math.ceil(c[1] + ext[1])
        z0 = math.floor(c[2] - ext[2])
        z1 = math.ceil(c[2] + ext[2])

    x0, x1 = max(0, x0), min(W - 1, x1)
    z0, z1 = max(0, z0), min(D - 1, z1)
    y0, y1 = max(Y_MIN, y0), min(Y_MAX, y1)
    # Pre-check the iteration volume BEFORE placing anything: an oversized
    # primitive previously destroyed existing terrain block-by-block and only
    # raised mid-way, so a failed call left the overwritten cells gone.
    vol = (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)
    if vol > MAX_BLOCKS_PER_CALL:
        raise ValueError(
            f"object exceeds {MAX_BLOCKS_PER_CALL} blocks (bbox volume {vol}); reduce sizes"
        )
    mat = prim.get("m", "stone")
    count = 0
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            for z in range(z0, z1 + 1):
                if _inside(x, y, z, prim):
                    world.place_block(x, y, z, mat, layer, eid)
                    count += 1
                    if count > MAX_BLOCKS_PER_CALL:
                        raise ValueError(
                            f"object exceeds {MAX_BLOCKS_PER_CALL} blocks; reduce sizes"
                        )
    return count


def _validate_prim(prim: Dict[str, Any]) -> None:
    """Check required keys with a clear error (LLMs need actionable messages)."""
    if not isinstance(prim, dict):
        raise ValueError(f"each primitive must be an object, got {type(prim).__name__}")
    kind = prim.get("shape")
    if kind not in ("cylinder", "sphere", "ellipsoid", "box", "torus"):
        raise ValueError(
            f"unknown shape {kind!r}; use cylinder|sphere|ellipsoid|box|torus"
        )
    if kind == "cylinder":
        for k in ("from", "to", "r"):
            if k not in prim:
                raise ValueError(
                    f"cylinder primitive is missing '{k}' — need from, to and r "
                    f"(r2 optional). Got keys: {sorted(prim)}"
                )
    elif kind == "sphere":
        for k in ("center", "r"):
            if k not in prim:
                raise ValueError(
                    f"sphere primitive is missing '{k}' — need center:[x,y,z] "
                    f"and r. Got keys: {sorted(prim)}"
                )
    elif kind in ("ellipsoid", "box"):
        for k in ("center", "r"):
            if k not in prim:
                raise ValueError(
                    f"{kind} primitive is missing '{k}' — need center:[x,y,z] "
                    f"and r:[rx,ry,rz]. Got keys: {sorted(prim)}"
                )
    elif kind == "torus":
        for k in ("center", "R", "r"):
            if k not in prim:
                raise ValueError(
                    f"torus primitive is missing '{k}' — need center, R (major) "
                    f"and r (minor). Got keys: {sorted(prim)}"
                )

    # Radii must be positive: zero/negative radii divide by zero in the SDF
    # math (ZeroDivisionError used to escape and orphan the entity).
    if kind == "sphere":
        if _nonpositive(prim.get("r")):
            raise ValueError("sphere r must be a number > 0")
    elif kind in ("ellipsoid", "box"):
        r = prim.get("r")
        if not (isinstance(r, (list, tuple)) and len(r) == 3) or any(_nonpositive(v) for v in r):
            raise ValueError(f"{kind} r must be [rx,ry,rz] with all values > 0")
    elif kind == "torus":
        if _nonpositive(prim.get("R")) or _nonpositive(prim.get("r")):
            raise ValueError("torus R and r must be numbers > 0")
    elif kind == "cylinder":
        if _nonpositive(prim.get("r")):
            raise ValueError("cylinder r must be a number > 0")


def _nonpositive(v: Any) -> bool:
    try:
        return float(v) <= 0.0
    except (TypeError, ValueError):
        return True


def voxelize_primitives(world, primitives: List[Dict[str, Any]], layer: str,
                        eid: str) -> int:
    """Voxelize a composition of primitives (later ones overwrite earlier)."""
    placed = 0
    for i, prim in enumerate(primitives):
        _validate_prim(prim)
        try:
            placed += _voxelize_prim(world, prim, layer, eid)
        except (KeyError, TypeError, ValueError, ArithmeticError) as e:
            raise ValueError(
                f"bad primitive #{i + 1} ({prim.get('shape')}): {e}"
            )
    return placed


# ---------------------------------------------------------- object library
def _named_giant_tree(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    h = float(p.get("height", 38))
    tr = float(p.get("trunk_r", 5))
    roots = int(p.get("roots", 6))
    branches = int(p.get("branches", 8))
    cr = float(p.get("canopy_r", 12))
    tm = p.get("trunk_m", "oak_log")
    cm = p.get("canopy_m", "oak_leaves")
    prims = [
        # tapered trunk
        {"shape": "cylinder", "from": [x, y, z], "to": [x, y + h * 0.62, z],
         "r": tr, "r2": tr * 0.35, "m": tm},
    ]
    rng = random.Random(int(x) * 31 + int(z) * 17 + int(h))
    # buttress roots flaring out from the base
    for i in range(roots):
        ang = 2 * math.pi * i / roots + rng.uniform(-0.25, 0.25)
        ox, oz = math.cos(ang), math.sin(ang)
        prims.append({
            "shape": "cylinder",
            "from": [x + ox * tr * 0.3, y, z + oz * tr * 0.3],
            "to": [x + ox * tr * 2.1, y - 2, z + oz * tr * 2.1],
            "r": tr * 0.75, "r2": 0.6, "m": tm,
        })
    # branches sweeping up-and-out
    for i in range(branches):
        t = i / max(branches - 1, 1)
        ang = 2 * math.pi * (i / branches) + rng.uniform(-0.4, 0.4)
        bx, bz = math.cos(ang), math.sin(ang)
        bh = y + h * (0.55 + 0.3 * t)
        blen = h * (0.28 - 0.1 * t)
        prims.append({
            "shape": "cylinder",
            "from": [x + bx * tr * 0.6, bh, z + bz * tr * 0.6],
            "to": [x + bx * (tr * 0.6 + blen), bh + h * 0.14, z + bz * (tr * 0.6 + blen)],
            "r": 2.2 - t * 1.2, "r2": 0.6, "m": tm,
        })
    # layered canopy: big center + drooping sides
    cy = y + h * 0.86
    prims.append({
        "shape": "ellipsoid", "center": [x, cy, z],
        "r": [cr, cr * 0.55, cr], "m": cm,
    })
    for i in range(5):
        ang = 2 * math.pi * i / 5 + rng.uniform(-0.3, 0.3)
        rr = cr * rng.uniform(0.45, 0.62)
        prims.append({
            "shape": "ellipsoid",
            "center": [x + math.cos(ang) * cr * 0.72, cy - cr * 0.28,
                       z + math.sin(ang) * cr * 0.72],
            "r": [rr, rr * 0.7, rr], "m": cm,
        })
    return prims


def _named_rock(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    r = float(p.get("r", 3))
    m = p.get("m", "stone")
    rng = random.Random(int(x) * 13 + int(z) * 7)
    prims = []
    for i in range(rng.randint(3, 5)):
        jx = rng.uniform(-r * 0.4, r * 0.4)
        jz = rng.uniform(-r * 0.4, r * 0.4)
        jy = rng.uniform(-r * 0.25, r * 0.25)
        rr = r * rng.uniform(0.55, 0.85)
        prims.append({
            "shape": "ellipsoid",
            "center": [x + jx, y + jy, z + jz],
            "r": [rr, rr * rng.uniform(0.6, 0.9), rr * rng.uniform(0.7, 1.0)],
            "m": m,
        })
    return prims


def _named_arch(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    width = float(p.get("width", 8))
    height = float(p.get("height", 6))
    thick = float(p.get("thick", 2))
    m = p.get("m", "stone")
    hw = width / 2
    prims = [
        {"shape": "box", "center": [x - hw, y + height / 2, z], "r": [thick / 2, height / 2, thick / 2], "m": m},
        {"shape": "box", "center": [x + hw, y + height / 2, z], "r": [thick / 2, height / 2, thick / 2], "m": m},
        {"shape": "box", "center": [x, y + height, z], "r": [hw + thick / 2, thick / 2, thick / 2], "m": m},
    ]
    return prims


def _named_boat(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    ln = float(p.get("length", 12))
    wd = float(p.get("width", 4))
    ht = float(p.get("height", 3))
    m = p.get("m", "oak_planks")
    prims = [
        {"shape": "ellipsoid", "center": [x, y + 1, z], "r": [ln / 2, ht / 2, wd / 2], "m": m},
        {"shape": "cylinder", "from": [x, y + 1, z], "to": [x, y + 1 + ht * 2.5, z], "r": 0.5, "r2": 0.4, "m": "oak_log"},
        {"shape": "box", "center": [x, y + 1 + ht * 2.5, z], "r": [2.2, 0.15, 1.2], "m": "sand"},
    ]
    return prims


def _named_tower(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    h = float(p.get("height", 18))
    r = float(p.get("r", 4))
    m = p.get("m", "cobblestone")
    prims = [
        {"shape": "cylinder", "from": [x, y, z], "to": [x, y + h, z], "r": r, "r2": r * 0.9, "m": m},
    ]
    for i in range(8):
        ang = 2 * math.pi * i / 8
        prims.append({
            "shape": "box",
            "center": [x + math.cos(ang) * (r + 0.4), y + h + 0.4, z + math.sin(ang) * (r + 0.4)],
            "r": [0.9, 0.8, 0.9], "m": m,
        })
    return prims


def _named_mushroom(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    sh = float(p.get("stem_h", 4))
    cr = float(p.get("cap_r", 5))
    prims = [
        {"shape": "cylinder", "from": [x, y, z], "to": [x, y + sh, z], "r": 1.2, "r2": 1.6, "m": "oak_log"},
        {"shape": "ellipsoid", "center": [x, y + sh + cr * 0.45, z], "r": [cr, cr * 0.75, cr], "m": "brick"},
    ]
    return prims


def _named_bridge(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    ln = float(p.get("length", 14))
    wd = float(p.get("width", 4))
    m = p.get("m", "oak_planks")
    hl = ln / 2
    prims = [
        {"shape": "box", "center": [x, y, z], "r": [hl, 0.6, wd / 2], "m": m},
        {"shape": "box", "center": [x, y + 1.1, z - wd / 2], "r": [hl, 0.5, 0.3], "m": m},
        {"shape": "box", "center": [x, y + 1.1, z + wd / 2], "r": [hl, 0.5, 0.3], "m": m},
    ]
    return prims


def _named_cloud(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    rx = float(p.get("rx", 10))
    ry = float(p.get("ry", 3))
    rng = random.Random(int(x) * 3 + int(z))
    prims = []
    for i in range(6):
        ang = rng.uniform(0, 2 * math.pi)
        dist = rng.uniform(0, rx * 0.6)
        rr = rx * rng.uniform(0.3, 0.5)
        prims.append({
            "shape": "ellipsoid",
            "center": [x + math.cos(ang) * dist, y + rng.uniform(-ry * 0.3, ry * 0.4),
                       z + math.sin(ang) * dist],
            "r": [rr, rr * rng.uniform(0.5, 0.75), rr * rng.uniform(0.6, 0.85)],
            "m": "snow",
        })
    return prims


def _named_fountain(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    r = float(p.get("r", 4))
    prims = [
        {"shape": "cylinder", "from": [x, y, z], "to": [x, y + 1.5, z], "r": r, "r2": r * 0.9, "m": "stone"},
        {"shape": "cylinder", "from": [x, y + 1.5, z], "to": [x, y + 2.5, z], "r": 0.6, "m": "stone"},
        {"shape": "ellipsoid", "center": [x, y + 3.6, z], "r": [r * 0.85, 1.1, r * 0.85], "m": "water"},
    ]
    return prims


def _named_spike(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    h = float(p.get("height", 24))
    r = float(p.get("r", 5))
    m = p.get("m", "stone")
    return [{
        "shape": "cylinder", "from": [x, y, z], "to": [x, y + h, z],
        "r": r, "r2": 0.0, "m": m,
    }]


def _named_ball(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    x, y, z = p["position"]
    return [{"shape": "sphere", "center": [x, y, z], "r": p.get("r", 4), "m": p.get("m", "stone")}]


def _named_duck(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Giant rubber-duck toy, facing +x. Yellow body (gold_ore), brick beak."""
    x, y, z = p["position"]
    s = float(p.get("scale", 1.0))
    yellow = "gold_ore"
    prims = [
        # body (floats — center sits near the waterline)
        {"shape": "ellipsoid", "center": [x, y + 10 * s, z],
         "r": [16 * s, 11 * s, 12 * s], "m": yellow},
        # tail flick
        {"shape": "cylinder", "from": [x - 13 * s, y + 8 * s, z],
         "to": [x - 19 * s, y + 14 * s, z], "r": 3.5 * s, "r2": 0.5 * s, "m": yellow},
        # head
        {"shape": "sphere", "center": [x + 10 * s, y + 20 * s, z + 4 * s],
         "r": 7 * s, "m": yellow},
        # beak
        {"shape": "box", "center": [x + 17 * s, y + 19 * s, z + 4 * s],
         "r": [3.5 * s, 1.6 * s, 1.6 * s], "m": "brick"},
        # eye
        {"shape": "sphere", "center": [x + 12.5 * s, y + 22.5 * s, z + 6.8 * s],
         "r": 1.3 * s, "m": "bedrock"},
    ]
    return prims


NAMED_OBJECTS: Dict[str, Any] = {
    "giant_tree": _named_giant_tree,
    "rock": _named_rock,
    "arch": _named_arch,
    "boat": _named_boat,
    "tower": _named_tower,
    "mushroom": _named_mushroom,
    "bridge": _named_bridge,
    "cloud": _named_cloud,
    "fountain": _named_fountain,
    "spike": _named_spike,
    "sphere": _named_ball,
    "duck": _named_duck,
}


def build_named_object(world, kind: str, params: Dict[str, Any], layer: str,
                       eid: str) -> int:
    """Build a named parametric object. Returns blocks placed."""
    builder = NAMED_OBJECTS.get(kind)
    if builder is None:
        raise ValueError(
            f"unknown object kind {kind!r}; available: {sorted(NAMED_OBJECTS)}"
        )
    position = params.get("position")
    if not position or len(position) != 3:
        raise ValueError("params must include position: [x, y, z]")
    prims = builder(params)
    return voxelize_primitives(world, prims, layer, eid)
