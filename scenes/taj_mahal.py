"""Taj Mahal v2 — full-canvas, with real dome profile, tiered minarets, detail."""
from minepaint import mcp_server as ms
from minepaint.core import W, D, Y_MAX
from minepaint.shapes import voxelize_primitives

w = ms.world
w.reset()
layer = w.create_layer("terrain")
G = 10
for x in range(W):
    for z in range(D):
        w.place_block(x, G, z, "grass", layer)
        w.place_block(x, G - 1, z, "dirt", layer)
        w.place_block(x, G - 2, z, "stone", layer)

base = G + 1  # platform top
marble, gold = "snow", "gold_ore"
lid = w.create_layer("monument")

# ---- platform with border + south steps ----------------------------------
P0, P1 = 12, 83
for x in range(P0, P1 + 1):
    for z in range(P0, P1 + 1):
        for y in (G + 1, G + 2):
            w.place_block(x, y, z, "cobblestone", lid)
        w.place_block(x, G + 3, z, marble, lid)
for x in range(P0, P1 + 1):  # border ring
    for z in (P0, P1):
        w.place_block(x, G + 4, z, "sand", lid)
    for z in range(P0, P1 + 1):
        w.place_block(P0, G + 4, z, "sand", lid)
        w.place_block(P1, G + 4, z, "sand", lid)
for s in range(1, 4):  # descending steps on the south side
    z0 = P1 + 1 + (s - 1) * 2
    for x in range(P0 + 2, P1 - 1):
        for z in range(z0, z0 + 2):
            w.place_block(x, G - s + 4, z, "cobblestone", lid)

# ---- pool + channel with fountains ---------------------------------------
def water(x1, x2, z1, z2, y):
    for x in range(x1, x2 + 1):
        for z in range(z1, z2 + 1):
            w.place_block(x, y, z, "water", lid)

water(24, 71, 55, 63, base + 1)
for x in (24, 71):
    for z in range(55, 64):
        w.place_block(x, base + 1, z, marble, lid)
for z in (55, 63):
    for x in range(24, 72):
        w.place_block(x, base + 1, z, marble, lid)
water(47, 49, 64, 83, base + 1)
water(47, 49, 84, 94, G + 1)
fountain_prims = []
for fz in (58, 72, 86):
    fountain_prims += [
        {"shape": "cylinder", "from": [48, base + 1 if fz > 64 else base + 1, fz],
         "to": [48, base + 2, fz], "r": 0.7, "m": marble},
        {"shape": "sphere", "center": [48, base + 3, fz], "r": 0.9, "m": "water"},
    ]

# ---- onion dome helper (flared bulb -> point) -----------------------------
def onion(cx, cy, cz, R, H, m):
    prof = [(0.0, R * 1.05), (0.26, R * 1.38), (0.52, R * 1.18), (0.72, R * 0.75),
            (0.88, R * 0.38), (1.0, R * 0.14)]
    return [{"shape": "cylinder", "from": [cx, cy + t0 * H, cz], "to": [cx, cy + t1 * H, cz],
             "r": r0, "r2": r1, "m": m} for (t0, r0), (t1, r1) in zip(prof, prof[1:])]

# ---- tomb + dome + chhatris ----------------------------------------------
cx, cz = 48, 48
w.create_entity("tomb")
tomb = [
    {"shape": "box", "center": [cx, base + 7, cz], "r": [22, 7, 18], "m": marble},
    # gold inlay band around the walls
    {"shape": "box", "center": [cx, base + 4, cz], "r": [22.2, 0.4, 18.2], "m": gold},
    {"shape": "box", "center": [cx, base + 11, cz], "r": [22.2, 0.4, 18.2], "m": gold},
    # corner pilasters
    *[{"shape": "box", "center": [cx + sx * 21, base + 7, cz + sz * 17], "r": [1.2, 7, 1.2], "m": gold}
      for sx in (-1, 1) for sz in (-1, 1)],
    # iwan on the south face
    {"shape": "box", "center": [cx, base + 9, cz + 17], "r": [5, 9, 1], "m": marble},
    {"shape": "box", "center": [cx, base + 4, cz + 17.5], "r": [4, 4, 0.6], "m": "glass"},
    {"shape": "box", "center": [cx, base + 13.5, cz + 17], "r": [5.5, 0.5, 0.8], "m": gold},
]
# chhatris: small drum + mini onion + finial
for sx in (-17, 17):
    for sz in (-13, 13):
        chx, chz = cx + sx, cz + sz
        tomb += [
            {"shape": "cylinder", "from": [chx, base + 3, chz], "to": [chx, base + 9, chz],
             "r": 2.6, "r2": 2.4, "m": marble},
            {"shape": "box", "center": [chx, base + 4.5, chz], "r": [2.8, 0.4, 2.8], "m": gold},
        ]
        tomb += onion(chx, base + 9, chz, 2.8, 6.5, marble)
        tomb += [{"shape": "cylinder", "from": [chx, base + 15.5, chz], "to": [chx, base + 17, chz],
                  "r": 0.15, "r2": 0.08, "m": gold}]
# central dome: drum + big onion + finial
w.create_entity("dome")
dome = [
    {"shape": "cylinder", "from": [cx, base + 13, cz], "to": [cx, base + 17, cz],
     "r": 14, "r2": 13.5, "m": marble},
    {"shape": "box", "center": [cx, base + 16, cz], "r": [14.4, 0.5, 14.4], "m": gold},
]
dome += onion(cx, base + 17, cz, 13, 26, marble)   # 26-tall onion
dome += [
    {"shape": "sphere", "center": [cx, base + 44, cz], "r": 1.2, "m": gold},
    {"shape": "cylinder", "from": [cx, base + 45, cz], "to": [cx, base + 49.5, cz],
     "r": 0.18, "r2": 0.08, "m": gold},
]

# ---- tiered minarets with balconies + chhatri tops ------------------------
def minaret(mx, mz):
    return [
        {"shape": "cylinder", "from": [mx, base, mz], "to": [mx, base + 8, mz],
         "r": 2.4, "r2": 2.0, "m": marble},
        {"shape": "cylinder", "from": [mx, base + 8, mz], "to": [mx, base + 21, mz],
         "r": 1.5, "r2": 1.3, "m": marble},
        {"shape": "cylinder", "from": [mx, base + 21, mz], "to": [mx, base + 22.5, mz],
         "r": 2.9, "r2": 2.5, "m": marble},                      # balcony 1
        {"shape": "cylinder", "from": [mx, base + 22.5, mz], "to": [mx, base + 34, mz],
         "r": 1.3, "r2": 1.1, "m": marble},
        {"shape": "cylinder", "from": [mx, base + 34, mz], "to": [mx, base + 35.5, mz],
         "r": 2.7, "r2": 2.3, "m": marble},                      # balcony 2
        {"shape": "cylinder", "from": [mx, base + 35.5, mz], "to": [mx, base + 41, mz],
         "r": 1.1, "r2": 1.0, "m": marble},
        {"shape": "cylinder", "from": [mx, base + 41, mz], "to": [mx, base + 42.5, mz],
         "r": 2.3, "r2": 2.0, "m": marble},                      # top gallery
    ] + onion(mx, base + 42.5, mz, 2.3, 6, marble) + [
        {"shape": "cylinder", "from": [mx, base + 48.5, mz], "to": [mx, base + 49.5, mz],
         "r": 0.12, "m": gold},
    ]

for sx in (-36, 36):
    for sz in (-36, 36):
        w.create_entity(f"minaret_{sx}_{sz}")
        dome += minaret(cx + sx, cz + sz)

# ---- south gate with domes -----------------------------------------------
w.create_entity("gate")
gate = [
    {"shape": "box", "center": [cx - 9, base + 3, 92], "r": [3.5, 3, 1.2], "m": marble},
    {"shape": "box", "center": [cx + 9, base + 3, 92], "r": [3.5, 3, 1.2], "m": marble},
    {"shape": "box", "center": [cx, base + 6.5, 92], "r": [12, 0.8, 1.2], "m": marble},
    {"shape": "box", "center": [cx, base + 5, 92], "r": [12, 0.4, 1.3], "m": gold},
]
for gx in (-9, 0, 9):
    gate += onion(cx + gx, base + 7, 92, 2.2, 4.5, marble)
    gate += [{"shape": "cylinder", "from": [cx + gx, base + 11.5, 92], "to": [cx + gx, base + 12.5, 92],
              "r": 0.12, "m": gold}]

voxelize_primitives(w, tomb, lid, "tomb")
voxelize_primitives(w, dome, lid, "dome")
voxelize_primitives(w, gate, lid, "gate")
w.create_entity("fountains")
voxelize_primitives(w, fountain_prims, lid, "fountains")

# ---- formal cypress rows + flower beds -----------------------------------
det = w.create_layer("garden")
from minepaint.terrain import _pine
rows = [(6, 20), (6, 76), (90, 20), (90, 76), (20, 6), (76, 6), (20, 90), (76, 90)]
n = 0
for (rx, rz) in rows:
    for off in (-14, 0, 14):
        x, z = rx + off, rz if rx < 50 else rz + off
        if 0 <= x < W and 0 <= z < D and not (P0 <= x <= P1 and P0 <= z <= P1):
            n += 1
            w.create_entity(f"cypress_{n}")
            _pine(w, x, G + 1, z, det, f"cypress_{n}")
for qx, qz in ((20, 20), (76, 20), (20, 76), (76, 76)):
    for dx in (0, 1):
        for dz in (0, 1):
            w.place_block(qx + dx, G + 1, qz + dz, "gold_ore", det)
            w.place_block(qx + dx + 3, G + 1, qz + dz, "brick", det)
            w.place_block(qx + dx, G + 1, qz + dz + 3, "sand", det)

ms._after_mutation()
print(f"world: {w.block_count} blocks | entities: {len(w.entities)}")
print("dome top y =", base + 49.5)
