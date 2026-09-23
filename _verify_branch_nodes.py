"""验证：线束选多根分支时，GLB 里每根分支一个独立命名节点，接口 parts 每根一项。

用真实的线束 STEP（_tmp_branch/baseline_full.stp）离线跑，不依赖 CATIA。
"""

import base64
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.utils.step_to_gltf import (  # noqa: E402
    ExportedNode,
    branch_node_name,
    split_branch_geometry,
    step_to_gltf,
    steps_to_gltf,
)

STEP = ROOT / "_tmp_branch" / "baseline_full.stp"
WORK = ROOT / "_tmp_branch"
OUT = ROOT / "_diag_nodes.txt"

# 上一轮用户在 3D 视图里真实点出来的两个拾取点（总成坐标）
PICK_RIB2 = (-14.78, 74.62, 12.68)
PICK_RIB1 = (-13.20, -53.02, 8.71)

L: list[str] = []


def log(msg: str = "") -> None:
    L.append(str(msg))
    print(msg)


def read_glb_json(path: Path) -> dict:
    data = path.read_bytes()
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF", f"not a glb: {magic!r}"
    off = 12
    while off < len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        chunk = data[off : off + clen]
        off += clen
        if ctype == 0x4E4F534A:  # 'JSON'
            return json.loads(chunk.decode("utf-8"))
    raise RuntimeError("no JSON chunk")


def glb_summary(path: Path) -> tuple[list[str], int, int]:
    """返回 (节点名列表, 节点数, 顶点数)。"""
    j = read_glb_json(path)
    names = [n.get("name") for n in j.get("nodes", [])]
    accessors = j.get("accessors", [])
    total = 0
    for mesh in j.get("meshes", []):
        for prim in mesh.get("primitives", []):
            pos = prim.get("attributes", {}).get("POSITION")
            if pos is not None:
                total += accessors[pos].get("count", 0)
    return names, len(names), total


def main() -> None:
    assert STEP.exists(), f"缺少测试用 STEP: {STEP}"

    log("=== 1) branches=[1, 4] -> 每根分支一个独立命名节点 ===")
    glb = WORK / "branch_1_4.glb"
    nodes = step_to_gltf(str(STEP), str(glb), name="多分支1.1", branches=[1, 4])
    names, n_nodes, verts = glb_summary(glb)
    log(f"  返回节点    : {[(n.name, n.branch) for n in nodes]}")
    log(f"  glTF 节点名 : {names}")
    log(f"  节点数/顶点 : {n_nodes} / {verts}")

    log("")
    log("=== 2) 拾取点判定（1 和 2 号分支）走同一条路 ===")
    glb2 = WORK / "branch_pick.glb"
    nodes2 = step_to_gltf(
        str(STEP), str(glb2), name="多分支1.1", pick_points=[PICK_RIB1, PICK_RIB2]
    )
    names2, n2, v2 = glb_summary(glb2)
    log(f"  返回节点    : {[(n.name, n.branch) for n in nodes2]}")
    log(f"  glTF 节点名 : {names2}")
    log(f"  节点数/顶点 : {n2} / {v2}")

    log("")
    log("=== 3) 不裁剪 -> 仍是单个节点（节点名 = 实例名）===")
    glb3 = WORK / "whole.glb"
    nodes3 = step_to_gltf(str(STEP), str(glb3), name="多分支1.1")
    names3, n3, v3 = glb_summary(glb3)
    log(f"  返回节点    : {[(n.name, n.branch) for n in nodes3]}")
    log(f"  glTF 节点名 : {names3}")
    log(f"  节点数/顶点 : {n3} / {v3}")

    log("")
    log("=== 4) 旧的二元组调用仍然兼容 ===")
    glb4 = WORK / "legacy.glb"
    legacy_nodes = steps_to_gltf(
        [(str(STEP) + "", None), (str(STEP), None)], str(glb4)
    )
    names4, n4, v4 = glb_summary(glb4)
    log(f"  返回节点    : {[(n.name, n.branch) for n in legacy_nodes]}")
    log(f"  glTF 节点名 : {names4}")
    log(f"  节点数/顶点 : {n4} / {v4}")

    log("")
    log("=== 5) split_branch_geometry 直接给分支序号 ===")
    from OCC.Extend.DataExchange import read_step_file

    shapes = split_branch_geometry(read_step_file(str(STEP)), [1, 4])
    log(f"  命中分支    : {[b for _, b in shapes]}")
    log(f"  每个分支是否独立 solid: {[s.ShapeType() for s, _ in shapes]}")
    log(f"  空 branches -> {split_branch_geometry(read_step_file(str(STEP)), [])}")
    log(f"  越界 branches=[9] -> {split_branch_geometry(read_step_file(str(STEP)), [9])}")

    log("")
    log("=== 6) service: 节点清单 -> parts（每根分支一项）===")
    from app.services.catia.service import CatiaService, _branches_of

    svc = CatiaService()
    fake_nodes = [
        ExportedNode(name=branch_node_name("多分支1.1", 1), instance_name="多分支1.1", branch=1),
        ExportedNode(name=branch_node_name("多分支1.1", 4), instance_name="多分支1.1", branch=4),
    ]
    parts = svc._nodes_to_parts(fake_nodes, {"多分支1.1": None})
    log(f"  parts       : {json.dumps(parts, ensure_ascii=False)}")
    log(f"  branches    : {_branches_of(fake_nodes)}")
    log(f"  parts 条数 == branches 条数: {len(parts) == len(_branches_of(fake_nodes))}")

    log("")
    log("=== 7) /getglb 端点契约（假数据）===")
    from app.api.v1.endpoints import catia as ep

    FAKE_GLB = b"\x00\x01FAKE-BRANCH-GLB"
    ep.catia_service.get_glb = lambda index=1: (FAKE_GLB, "多分支1.1", parts, [1, 4])
    resp = ep.get_gltf(index=1)
    body = resp.model_dump()
    log(f"  data keys   : {list(body['data'].keys())}")
    log(f"  parts       : {json.dumps(body['data']['parts'], ensure_ascii=False)}")
    log(f"  branches    : {body['data']['branches']}")
    decoded = base64.b64decode(body["data"]["glb"])
    log(f"  base64 往返 : {decoded == FAKE_GLB}")

    log("")
    log("=== 8) openapi 里 GlbNode 的字段 ===")
    from app.main import app as fastapi_app

    spec = fastapi_app.openapi()
    props = spec["components"]["schemas"]["GlbNode"]["properties"]
    log(f"  GlbNode 字段: {list(props.keys())}")
    log(f"  含 branch   : {'branch' in props}")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print("written", OUT)


if __name__ == "__main__":
    main()
