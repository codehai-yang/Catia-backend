"""离线验证：按分支裁剪后写出的 GLB 是否只含选中的分支。

用 CATIA 真实导出的 STEP + 真实 3D 拾取点，不依赖 CATIA。
"""
import json
import struct
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.utils.step_to_gltf import step_to_gltf, steps_to_gltf

TMP = Path(__file__).resolve().parent / "_tmp_branch"
STEP = TMP / "baseline_full.stp"
OUT = Path(__file__).resolve().parent / "_diag13.txt"

PICK_RIB2 = (-14.775392532348633, 74.62451171875, 12.684941291809082)
PICK_RIB1 = (-13.19568157196045, -53.01512145996094, 8.706997871398926)

L: list[str] = []


def log(*a):
    L.append(" ".join(str(x) for x in a))


def read_glb_json(path: Path) -> dict:
    raw = path.read_bytes()
    magic, version, _ = struct.unpack("<4sII", raw[:12])
    assert magic == b"glTF", magic
    offset = 12
    chunk_len, chunk_type = struct.unpack("<I4s", raw[offset : offset + 8])
    assert chunk_type == b"JSON", chunk_type
    return json.loads(raw[offset + 8 : offset + 8 + chunk_len].decode("utf-8"))


def node_report(path: Path) -> tuple[int, list[str], int, int]:
    doc = read_glb_json(path)
    nodes = doc.get("nodes", [])
    names = [n.get("name", "<unnamed>") for n in nodes]
    meshes = doc.get("meshes", [])
    vertex_total = 0
    for m in meshes:
        for prim in m.get("primitives", []):
            acc = prim.get("attributes", {}).get("POSITION")
            if acc is None:
                continue
            vertex_total += doc["accessors"][acc].get("count", 0)
    return len(nodes), names, len(meshes), vertex_total


def run(tag: str, out_name: str, **kwargs):
    out = TMP / out_name
    step_to_gltf(str(STEP), str(out), name="多分支1.1", **kwargs)
    n_nodes, names, n_meshes, verts = node_report(out)
    size = out.stat().st_size
    log(f"[{tag}]")
    log(f"    glb size      = {size} bytes")
    log(f"    node count    = {n_nodes}  names={names}")
    log(f"    mesh count    = {n_meshes}  vertices={verts}")
    return verts


log("=== 单个 STEP -> GLB 分支裁剪 ===")
v_all = run("no filter (整根线束)", "v_all.glb")
v_b2 = run("branches=[2]", "v_b2.glb", branches=[2])
v_b1 = run("branches=[1]", "v_b1.glb", branches=[1])
v_pick2 = run("pick_points=[Rib.2]", "v_pick2.glb", pick_points=[PICK_RIB2])
v_pick12 = run("pick_points=[Rib.2, Rib.1]", "v_pick12.glb", pick_points=[PICK_RIB2, PICK_RIB1])

log("")
log("=== 多步 steps_to_gltf（带实例矩阵，模拟真实调用）===")
identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
out = TMP / "v_multi.glb"
steps_to_gltf(
    [(str(STEP), identity, "多分支1.1")],
    str(out),
    pick_points=[PICK_RIB2],
)
n_nodes, names, n_meshes, verts = node_report(out)
log(f"[steps_to_gltf pick Rib.2] nodes={n_nodes} names={names} meshes={n_meshes} verts={verts}")

out2 = TMP / "v_multi_legacy.glb"
steps_to_gltf([(str(STEP), identity, "多分支1.1")], str(out2))
n_nodes, names, n_meshes, verts = node_report(out2)
log(f"[steps_to_gltf 旧二元组兼容] nodes={n_nodes} names={names} meshes={n_meshes} verts={verts}")

log("")
log("=== 判定 ===")
log(f"整根线束 vertices = {v_all}")
log(f"仅分支2  vertices = {v_b2}")
log(f"仅分支1  vertices = {v_b1}")
log(f"分支1+2  vertices = {v_pick12}")
log(f"分支1+2 是否明显小于整根: {v_pick12 < v_all}")
log(f"单分支2 是否约等于 1/4 整根: {v_b2 < v_all * 0.6}")
log(f"分支1 与 分支2 顶点数是否不同(说明确实是不同实体): {v_b1 != v_b2}")

OUT.write_text("\n".join(L), encoding="utf-8")
print("written", OUT)
