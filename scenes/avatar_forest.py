"""Avatar-style demo scene: rolling hills + giant home trees + pine forest + cloud."""
from minepaint import mcp_server as ms
from minepaint.terrain import generate, _scatter_trees
from minepaint.shapes import build_named_object

w = ms.world
generate(w, seed=23, style="rolling_hills")

def surface(x, z):
    return max((b.y for b in w._blocks.values() if b.x == x and b.z == z), default=0)

lid = w.create_layer("objects")
g = surface(48, 48) + 1
w.create_entity("home_tree")
build_named_object(w, "giant_tree",
                   {"position": [48, g, 48], "height": 38, "trunk_r": 5,
                    "roots": 6, "branches": 8, "canopy_r": 12}, lid, "home_tree")
# two more home trees via copy_entity (hero feature)
w.copy_entity("home_tree", 32, 0, 20)
w.copy_entity("home_tree", -26, 0, 17)
# pines scattered around the valley
det = w.create_layer("details")
_scatter_trees(w, "pine", 14, 4, 4, 91, 91, det)
# a cloud drifting above
w.create_entity("cloud_1")
build_named_object(w, "cloud", {"position": [48, g + 50, 48], "rx": 11}, det, "cloud_1")

ms._after_mutation()
print(f"world: {w.block_count} blocks | layers: {[l.name for l in w.layers_sorted()]} | entities: {sorted(e.id for e in w.entities.values())}")
