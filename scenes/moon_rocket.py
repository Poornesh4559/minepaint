"""Full moon with craters + a small rocket on top. Night scene, floating in the void."""
import math
import random

from minepaint import mcp_server as ms
from minepaint.shapes import voxelize_primitives

w = ms.world
w.reset()
lid = w.create_layer("space")

cx, cy, cz, R = 48, 12, 48, 28.0
w.create_entity("moon")

# ---- moon body: big sphere ------------------------------------------------
prims = [{"shape": "sphere", "center": [cx, cy, cz], "r": R, "m": "snow"}]
voxelize_primitives(w, prims, lid, "moon")

# ---- craters: dark disks + raised rims on the surface ---------------------
rng = random.Random(42)
crater_prims = []
n_craters = 16
for i in range(n_craters):
    # random direction on the upper hemisphere (so they're visible)
    theta = rng.uniform(0, 2 * math.pi)
    phi = rng.uniform(0, math.pi * 0.48)
    sx, sy, sz = (math.sin(phi) * math.cos(theta), math.cos(phi),
                  math.sin(phi) * math.sin(theta))
    cr = rng.uniform(2.0, 6.0)
    px, py, pz = cx + sx * R, cy + sy * R, cz + sz * R
    # tangent plane basis
    up = (0.0, 1.0, 0.0)
    tx = (up[1] * sz - up[2] * sy, up[2] * sx - up[0] * sz, up[0] * sy - up[1] * sx)
    n = math.sqrt(sum(v * v for v in tx)) or 1.0
    tx = [v / n for v in tx]
    ty = [sy * tz - sz * ty_v for ty_v, tz in zip((tx[1] * sz - tx[2] * sy,
                                                   tx[2] * sx - tx[0] * sz,
                                                   tx[0] * sy - tx[1] * sx), (0, 0, 0))]
    # simpler: build the crater as a ring of dark blocks laid on the surface
    ring = []
    for k in range(24):
        a = 2 * math.pi * k / 24
        rr = cr
        ox = sx * math.cos(a) + (tx[0] if tx else 1) * math.sin(a)
        oy = sy * math.cos(a)
        oz = sz * math.cos(a) + (tx[2] if tx else 0) * math.sin(a)
        nx, ny, nz = px + ox * rr, py + oy * rr, pz + oz * rr
        ring.append((round(nx), round(ny), round(nz)))
    for (rx_, ry_, rz_) in ring:
        if 0 <= rx_ < 96 and -32 <= ry_ <= 63 and 0 <= rz_ < 96:
            crater_prims.append({"shape": "box", "center": [rx_, ry_, rz_],
                                 "r": [0.6, 0.6, 0.6], "m": "bedrock"})
    # raised rim: lighter ring just outside
    for k in range(24):
        a = 2 * math.pi * k / 24
        rr = cr + 1.2
        ox = sx * math.cos(a) + (tx[0] if tx else 1) * math.sin(a)
        oy = sy * math.cos(a)
        oz = sz * math.cos(a) + (tx[2] if tx else 0) * math.sin(a)
        nx, ny, nz = px + ox * rr, py + oy * rr, pz + oz * rr
        crater_prims.append({"shape": "box", "center": [nx, ny, nz],
                             "r": [0.5, 0.8, 0.5], "m": "cobblestone"})
w.create_entity("craters")
voxelize_primitives(w, crater_prims, lid, "craters")

# ---- rocket on the moon's north pole --------------------------------------
rx, ry, rz = cx, cy + R, cz   # top of the moon
w.create_entity("rocket")
rocket = [
    {"shape": "cylinder", "from": [rx, ry + 2, rz], "to": [rx, ry + 14, rz],
     "r": 2.2, "r2": 2.2, "m": "snow"},
    {"shape": "cylinder", "from": [rx, ry + 14, rz], "to": [rx, ry + 18, rz],
     "r": 2.2, "r2": 0.6, "m": "snow"},                      # nose cone
    {"shape": "sphere", "center": [rx, ry + 18.5, rz], "r": 0.6, "m": "gold_ore"},
    {"shape": "sphere", "center": [rx, ry + 7, rz], "r": 0.9, "m": "glass"},  # window
    {"shape": "box", "center": [rx - 2.4, ry + 2.5, rz], "r": [0.5, 1.8, 0.4], "m": "brick"},
    {"shape": "box", "center": [rx + 2.4, ry + 2.5, rz], "r": [0.5, 1.8, 0.4], "m": "brick"},
    {"shape": "box", "center": [rx, ry + 2.5, rz - 2.4], "r": [0.4, 1.8, 0.5], "m": "brick"},
    {"shape": "box", "center": [rx, ry + 2.5, rz + 2.4], "r": [0.4, 1.8, 0.5], "m": "brick"},
    # launch flame
    {"shape": "cylinder", "from": [rx, ry, rz], "to": [rx, ry - 4, rz],
     "r": 1.4, "r2": 0.6, "m": "lava"},
]
voxelize_primitives(w, rocket, lid, "rocket")

ms._after_mutation()
print(f"world: {w.block_count} blocks | entities: {sorted(e.id for e in w.entities.values())}")
