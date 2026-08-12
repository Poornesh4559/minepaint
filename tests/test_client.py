"""minepaint end-to-end test client.

Connects to minepaint/mcp_server.py over stdio, builds a house + tree scene,
copies/moves the tree, then verifies everything via get_state.

Run from repo root:  .venv/bin/python .work/test_client.py
Prints PASS/FAIL per check; exit code non-zero on any failure.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(REPO, ".venv", "bin", "python")
SERVER = os.path.join(REPO, "minepaint", "mcp_server.py")

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" -- {detail}" if detail else ""))
    if not cond:
        failures.append(name)


async def call(session: ClientSession, tool: str, **kwargs):
    res = await session.call_tool(tool, arguments=kwargs or None)
    if res.isError:
        raise RuntimeError(f"tool {tool} error: {res.content}")
    data = res.content[0].text
    try:
        return json.loads(data)
    except Exception:
        return data


def find_block(state, x, y, z):
    for b in state["blocks"]:
        if b[0] == x and b[1] == y and b[2] == z:
            return b
    return None


async def main() -> int:
    params = StdioServerParameters(
        command=PYTHON, args=[SERVER], cwd=REPO,
        env={"PYTHONPATH": REPO, "PYTHONUNBUFFERED": "1"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # deterministic start
            await call(session, "reset_world")

            # ---- layers ----
            house = await call(session, "create_layer", name="house")
            check("create_layer(house) returns id", house == "house", str(house))
            nature = await call(session, "create_layer", name="nature")
            check("create_layer(nature) returns id", nature == "nature", str(nature))

            # ---- house in layer 'house' ----
            # floor + walls + flat roof, all inclusive fill_cuboid
            floor = await call(session, "fill_cuboid", x1=0, y1=0, z1=0, x2=6, y2=0, z2=6,
                               block_type="oak_planks", layer_id=house)
            check("house floor = 49 blocks", floor.get("placed") == 49, str(floor))
            walls = await call(session, "fill_cuboid", x1=0, y1=1, z1=0, x2=6, y2=3, z2=6,
                               block_type="brick", layer_id=house)
            check("house walls = 147 blocks", walls.get("placed") == 147, str(walls))
            roof = await call(session, "fill_cuboid", x1=0, y1=4, z1=0, x2=6, y2=4, z2=6,
                              block_type="oak_planks", layer_id=house)
            check("house roof = 49 blocks", roof.get("placed") == 49, str(roof))
            # 49 + 147 + 49 = 245 house blocks

            # ---- tree as entity 'tree' in layer 'nature' ----
            tree = await call(session, "create_entity", name="tree")
            check("create_entity(tree) returns id", tree == "tree", str(tree))
            for y in range(3):  # trunk at (10, y, 10)
                await call(session, "place_block", x=10, y=y, z=10,
                           block_type="oak_log", layer_id=nature, entity_id=tree)
            leaves = await call(session, "fill_cuboid", x1=9, y1=3, z1=9, x2=11, y2=4, z2=11,
                                block_type="oak_leaves", layer_id=nature, entity_id=tree)
            check("tree leaves = 18 blocks", leaves.get("placed") == 18, str(leaves))
            # tree = 3 trunk + 18 leaves = 21 blocks

            tree_blocks = await call(session, "get_entity", entity_id=tree)
            check("get_entity(tree) = 21 blocks", len(tree_blocks["blocks"]) == 21, str(len(tree_blocks["blocks"])))

            # ---- hero feature: copy + move ----
            copy = await call(session, "copy_entity", entity_id=tree, dx=10, dy=0, dz=0)
            check("copy_entity(tree, 10,0,0) creates tree_copy1",
                  copy.get("new_entity_ids") == ["tree_copy1"], str(copy))
            check("copy reports 21 new blocks", copy.get("new_blocks") == 21, str(copy))

            moved = await call(session, "move_entity", entity_id=tree, dx=0, dy=0, dz=5)
            check("move_entity(tree, 0,0,5) moves 21 blocks", moved.get("blocks") == 21, str(moved))

            # ---- verify full state ----
            state = await call(session, "get_state")
            blocks = state["blocks"]
            n = len(blocks)
            check("total block count = 287 (245 house + 21 tree + 21 copy)",
                  n == 287, str(n))

            entities = {e["id"] for e in state["entities"]}
            check("entity ids = {tree, tree_copy1}", entities == {"tree", "tree_copy1"}, str(entities))

            layers = {l["id"] for l in state["layers"]}
            check("layer ids = {house, nature}", layers == {"house", "nature"}, str(layers))

            # house corner blocks
            b = find_block(state, 0, 0, 0)
            check("(0,0,0) = oak_planks on house", b is not None and b[3] == "oak_planks" and b[4] == house, str(b))
            b = find_block(state, 0, 1, 0)
            check("(0,1,0) = brick on house", b is not None and b[3] == "brick" and b[4] == house, str(b))
            b = find_block(state, 6, 4, 6)
            check("(6,4,6) = oak_planks roof on house", b is not None and b[3] == "oak_planks" and b[4] == house, str(b))

            # original tree, moved +5 in z: trunk base now (10,0,15)
            b = find_block(state, 10, 0, 15)
            check("moved tree trunk base at (10,0,15) = oak_log",
                  b is not None and b[3] == "oak_log" and b[4] == nature and b[5] == tree, str(b))
            b = find_block(state, 10, 0, 10)
            check("original trunk spot (10,0,10) is now empty", b is None)

            # copy tree at +10 x: trunk base now (20,0,10)
            b = find_block(state, 20, 0, 10)
            check("copied tree trunk base at (20,0,10) = oak_log",
                  b is not None and b[3] == "oak_log" and b[4] == nature and b[5] == "tree_copy1", str(b))
            b = find_block(state, 20, 1, 10)
            check("copied tree trunk middle at (20,1,10)", b is not None and b[3] == "oak_log", str(b))
            b = find_block(state, 20, 2, 10)
            check("copied tree trunk top at (20,2,10)", b is not None and b[3] == "oak_log", str(b))

            # copy leaves present
            b = find_block(state, 21, 3, 11)
            check("copied tree leaf at (21,3,11)", b is not None and b[3] == "oak_leaves", str(b))

            # moved-tree leaves present around (10,3,15)
            b = find_block(state, 9, 3, 14)
            check("moved tree leaf at (9,3,14)", b is not None and b[3] == "oak_leaves", str(b))

            # entity membership counts via get_entity
            e1 = await call(session, "get_entity", entity_id="tree_copy1")
            check("get_entity(tree_copy1) = 21 blocks", len(e1["blocks"]) == 21, str(len(e1["blocks"])))
            coords = {(b[0], b[1], b[2]) for b in e1["blocks"]}
            check("tree_copy1 contains (20,0,10)", (20, 0, 10) in coords, str((20, 0, 10) in coords))

            # bounds safety: copy that would go out of range must error
            try:
                await call(session, "copy_entity", entity_id=tree, dx=200, dy=0, dz=0)
                check("out-of-bounds copy_entity raises error", False, "no error raised")
            except RuntimeError as ex:
                check("out-of-bounds copy_entity raises error", "out of bounds" in str(ex).lower(), str(ex)[:80])

            # terrain generation: dramatic snowy mountains + ocean
            terr = await call(session, "generate_terrain", seed=5, sea_level=12,
                              snowline=26, mountain_amp=44.0)
            check("generate_terrain returns by_type summary",
                  "by_type" in terr and terr["blocks"] > 30000, str(terr.get("blocks")))
            bt = terr.get("by_type", {})
            check("terrain has snow caps", bt.get("snow", 0) > 500, f"snow={bt.get('snow')}")
            check("terrain has water (ocean/river)", bt.get("water", 0) > 2000, f"water={bt.get('water')}")
            check("terrain has bedrock crust", bt.get("bedrock", 0) > 5000, f"bedrock={bt.get('bedrock')}")
            check("terrain height range includes negatives (seabed)",
                  terr.get("peak_height", 0) > 0, str(terr.get("peak_height")))
            st = await call(session, "get_state")
            y_vals = [b[1] for b in st["blocks"]]
            check("terrain extends below y=0", min(y_vals) < 0, f"min_y={min(y_vals)}")
            check("terrain reaches snowline heights", max(y_vals) > 30, f"max_y={max(y_vals)}")

            # parametric objects + primitive voxelizer
            obj = await call(session, "build_object", kind="giant_tree",
                             params={"position": [48, 10, 48], "height": 30,
                                     "trunk_r": 4, "canopy_r": 9})
            check("build_object giant_tree places blocks",
                  isinstance(obj.get("blocks"), int) and obj["blocks"] > 4000,
                  str(obj.get("blocks")))
            check("giant_tree becomes an entity", bool(obj.get("entity")), str(obj.get("entity")))
            shp = await call(session, "build_shape", primitives=[
                {"shape": "cylinder", "from": [10, 5, 10], "to": [10, 12, 10],
                 "r": 1, "m": "oak_log"},
                {"shape": "sphere", "center": [10, 15, 10], "r": 3, "m": "oak_leaves"}])
            check("build_shape composition places blocks",
                  isinstance(shp.get("blocks"), int) and shp["blocks"] > 100,
                  str(shp.get("blocks")))
            check("build_shape creates entity", bool(shp.get("entity")), str(shp.get("entity")))
            ents = await call(session, "list_entities")
            check("object entities listed", len(ents) >= 2, str(len(ents)))

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
