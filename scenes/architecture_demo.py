"""Architecture proof: Roman colosseum + Chinese mansion via build_shape primitives."""
from minepaint import mcp_server as ms
from minepaint.terrain import generate
from minepaint.shapes import voxelize_primitives

w = ms.world
generate(w, seed=23, style="rolling_hills")

def surface(x, z):
    return max((b.y for b in w._blocks.values() if b.x == x and b.z == z), default=0)

lid = w.create_layer("buildings")
stone = "cobblestone"

# ---- Roman colosseum: 3 stacked hollow tiers (tori) + sand arena ---------
cx, cz = 26, 26
g = surface(cx, cz) + 1
w.create_entity("colosseum")
prims = [
    {"shape": "torus", "center": [cx, g + 5, cz], "R": 14, "r": 5, "m": stone},
    {"shape": "torus", "center": [cx, g + 11, cz], "R": 11, "r": 4, "m": stone},
    {"shape": "torus", "center": [cx, g + 16, cz], "R": 8.5, "r": 3, "m": stone},
    {"shape": "cylinder", "from": [cx, g, cz], "to": [cx, g + 1, cz], "r": 8.5, "m": "sand"},
]
# grand entrance: two pillars + lintel
prims += [
    {"shape": "box", "center": [cx - 4, g + 4, cz + 14], "r": [1, 4, 1.2], "m": stone},
    {"shape": "box", "center": [cx + 4, g + 4, cz + 14], "r": [1, 4, 1.2], "m": stone},
    {"shape": "box", "center": [cx, g + 8.5, cz + 14], "r": [4.5, 0.8, 1.2], "m": stone},
]
voxelize_primitives(w, prims, lid, "colosseum")

# ---- Chinese mansion: platform, main hall with pagoda roof, courtyard -----
mx, mz = 66, 66
mg = surface(mx, mz) + 1
w.create_entity("mansion")
m_prims = [
    # platform
    {"shape": "box", "center": [mx, mg + 0.75, mz], "r": [8, 0.75, 6], "m": "stone"},
    # main hall
    {"shape": "box", "center": [mx, mg + 3.5, mz], "r": [4, 2, 3], "m": "brick"},
    # pagoda roof (wide cone-ish) + ridge ball
    {"shape": "cylinder", "from": [mx, mg + 5.5, mz], "to": [mx, mg + 8.5, mz],
     "r": 6, "r2": 0.4, "m": "brick"},
    {"shape": "sphere", "center": [mx, mg + 9, mz], "r": 0.8, "m": "gold_ore"},
    # side halls + small roofs
    {"shape": "box", "center": [mx - 6, mg + 2.5, mz], "r": [1.6, 2, 3], "m": "brick"},
    {"shape": "cylinder", "from": [mx - 6, mg + 4.5, mz], "to": [mx - 6, mg + 6.5, mz],
     "r": 3, "r2": 0.3, "m": "brick"},
    {"shape": "box", "center": [mx + 6, mg + 2.5, mz], "r": [1.6, 2, 3], "m": "brick"},
    {"shape": "cylinder", "from": [mx + 6, mg + 4.5, mz], "to": [mx + 6, mg + 6.5, mz],
     "r": 3, "r2": 0.3, "m": "brick"},
    # courtyard walls with a gate gap in front
    {"shape": "box", "center": [mx, mg + 1.5, mz - 7], "r": [7.5, 1.5, 0.6], "m": "brick"},
    {"shape": "box", "center": [mx - 6.5, mg + 1.5, mz], "r": [0.6, 1.5, 6.4], "m": "brick"},
    {"shape": "box", "center": [mx + 6.5, mg + 1.5, mz], "r": [0.6, 1.5, 6.4], "m": "brick"},
    {"shape": "box", "center": [mx - 3, mg + 1.5, mz + 7], "r": [3.4, 1.5, 0.6], "m": "brick"},
    {"shape": "box", "center": [mx + 3, mg + 1.5, mz + 7], "r": [3.4, 1.5, 0.6], "m": "brick"},
]
voxelize_primitives(w, m_prims, lid, "mansion")

ms._after_mutation()
print(f"world: {w.block_count} blocks | entities: {sorted(e.id for e in w.entities.values())}")
