"""离线验证多选导出：用桩对象驱动真实的 CatiaService.get_glb。

场景 = 用户真实遇到的那次：选中①线束第 3 根分支 ②卡扣 clipA.2，
两者属于不同 CATPart，期望一次请求全部返回、且互不误裁。
用 _tmp_branch/ 下预导出的真实 STEP，不需要 CATIA。
"""

import base64
import json
import shutil
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.services.catia.service import CatiaError, CatiaService  # noqa: E402

WORK = ROOT / "_tmp_branch"
HARNESS_STEP = WORK / "harness_full.stp"
CLIP_STEP = WORK / "clipA_full.stp"
OUT = ROOT / "_diag_multi.txt"

HARNESS_NAME = "多分支1.1"
CLIP_NAME = "clipA.2"

# 用户真实点出来的拾取点（3D 视图，总成坐标）
PICK_BRANCH3 = (-93.946, 313.594, 101.738)
PICK_CLIP = (-36.293, 233.326, 52.824)

L: list[str] = []


def log(msg: str = "") -> None:
    L.append(str(msg))
    print(msg)


def read_glb_json(data: bytes) -> dict:
    magic, _v, _l = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF", f"not a glb: {magic!r}"
    off = 12
    while off < len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        chunk = data[off : off + clen]
        off += clen
        if ctype == 0x4E4F534A:
            return json.loads(chunk.decode("utf-8"))
    raise RuntimeError("no JSON chunk")


def node_stats(data: bytes) -> list[tuple[str, int]]:
    """返回 [(节点名, 顶点数), ...]。"""
    j = read_glb_json(data)
    accessors = j.get("accessors", [])
    stats: list[tuple[str, int]] = []
    for node in j.get("nodes", []):
        count = 0
        mesh_index = node.get("mesh")
        if mesh_index is not None:
            for prim in j["meshes"][mesh_index].get("primitives", []):
                pos = prim.get("attributes", {}).get("POSITION")
                if pos is not None:
                    count += accessors[pos].get("count", 0)
        stats.append((str(node.get("name")), count))
    return stats


# ---------------- 桩对象 ----------------

class FakeExportDoc:
    """假装是 CATPart 文档：ExportData 时把预导出的 STEP 拷到目标路径。"""

    def __init__(self, name: str, step_path: Path) -> None:
        self.Name = name
        self.FullName = f"D:/电缆数模案例/{name}"
        self._step = step_path

    def ExportData(self, path: str, fmt: str) -> None:
        shutil.copyfile(self._step, path)


class FakeItem:
    def __init__(self, leaf: str, type_: str, ref_name: str, ref_doc, pick=None) -> None:
        self.LeafProduct = SimpleNamespace(Name=leaf)
        self.Type = type_
        self.Value = SimpleNamespace(
            Name=ref_name,
            ReferenceProduct=SimpleNamespace(Parent=ref_doc),
        )
        self.Reference = SimpleNamespace(Name=ref_name, DisplayName=ref_name)
        self._pick = pick


def build_service(items: list[FakeItem]) -> CatiaService:
    selection = SimpleNamespace(Count2=len(items), Item2=lambda i: items[i - 1])
    document = SimpleNamespace(Name="cable example.CATProduct", Selection=selection)
    app = SimpleNamespace(ActiveDocument=document)

    svc = CatiaService()
    svc._connect = lambda: app
    # 桩对象没有 COM 坐标，直接给一个假的拾取点以驱动"分支裁剪开关"逻辑
    svc._pick_point = staticmethod(lambda item: getattr(item, "_pick", None))
    return svc


def main() -> None:
    assert HARNESS_STEP.exists() and CLIP_STEP.exists(), "缺少 _tmp_branch 下的 STEP"

    harness_ref = FakeExportDoc("MultiBranchable1.CATPart", HARNESS_STEP)
    clip_ref = FakeExportDoc("clipA.CATPart", CLIP_STEP)

    items = [
        FakeItem(
            HARNESS_NAME,
            "Face",
            "Selection_RSur:(Face:(Brp:(EhiBundleSegmentRib.3;0:(Brp:(ElecCurve.3;2);"
            "Brp:(GSMCircle.3;(Brp:(GSMPlane.24)))));None:();Cf11:()));"
            "EhiBundleSegmentRib.3_ResultOUT",
            harness_ref,
            PICK_BRANCH3,
        ),
        FakeItem(
            CLIP_NAME,
            "PlanarFace",
            "Selection_RSur:(Face:(Brp:(Shaft.1;0:(Brp:(Sketch.1;1)));None:();Cf11:()));"
            "Shaft.1_ResultOUT",
            clip_ref,
            PICK_CLIP,
        ),
    ]

    log("=== 基准：单独导出，拿到各自的期望值 ===")
    from app.utils.step_to_gltf import step_to_gltf

    tmp = ROOT / "_tmp_branch" / "_ref_branch.glb"
    step_to_gltf(str(HARNESS_STEP), str(tmp), name=HARNESS_NAME, branches=[3])
    expect_harness = node_stats(tmp.read_bytes())
    log(f"  线束第3根分支: {expect_harness}")

    tmp2 = ROOT / "_tmp_branch" / "_ref_clip.glb"
    step_to_gltf(str(CLIP_STEP), str(tmp2), name=CLIP_NAME)
    expect_clip = node_stats(tmp2.read_bytes())
    log(f"  整个卡扣    : {expect_clip}")

    log("")
    log("=== 1) get_glb() 默认 index=0 -> 导出全部选中项（跨零件）===")
    svc = build_service(items)
    data, filename, parts, branches = svc.get_glb()
    stats = node_stats(data)
    log(f"  filename : {filename}")
    log(f"  parts    : {json.dumps(parts, ensure_ascii=False)}")
    log(f"  branches : {branches}")
    log(f"  GLB 节点 : {stats}")
    log(f"  GLB 大小 : {len(data)} 字节")

    log("")
    log("=== 2) 断言 ===")
    ids = [p["id"] for p in parts]
    ok_parts = len(parts) == 2 and ids == [f"{HARNESS_NAME}#3", CLIP_NAME]
    log(f"  parts == ['多分支1.1#3','clipA.2']  : {ok_parts}  ({ids})")
    log(f"  GLB 节点名与 parts[].id 完全一致    : {[n for n, _ in stats] == ids}")
    log(f"  branches == [3]                    : {branches == [3]}")
    log(f"  卡扣未被裁成 'clipA.2#N'（未被误裁） : {ok_parts}")
    log(f"  线束分支顶点数与单导一致             : {stats[0][1] == expect_harness[0][1]}")
    log(f"  卡扣顶点数与单导一致（=整零件）      : {stats[1][1] == expect_clip[0][1]}")
    log(f"  base64 往返一致                     : {base64.b64decode(base64.b64encode(data)) == data}")

    log("")
    log("=== 3) 兼容性：传 index 时仍是「只导第 index 个」 ===")
    for idx in (1, 2):
        _, fn, p, b = build_service(items).get_glb(index=idx)
        log(f"  index={idx}: filename={fn} parts={json.dumps(p, ensure_ascii=False)} branches={b}")

    log("")
    log("=== 4) 边界 ===")
    try:
        build_service(items).get_glb(index=3)
        log("  index=3 未报错  <-- 不符合预期")
    except CatiaError as exc:
        log(f"  index=3 -> CatiaError: {exc}")
    try:
        build_service([]).get_glb()
        log("  空选择未报错  <-- 不符合预期")
    except CatiaError as exc:
        log(f"  空选择 -> CatiaError: {exc}")

    log("")
    log("=== 5) 只选卡扣时，不应被拾取点裁成单个实体 ===")
    _, fn5, p5, b5 = build_service([items[1]]).get_glb()
    log(f"  filename={fn5} parts={json.dumps(p5, ensure_ascii=False)} branches={b5}")

    log("")
    log("=== 6) 只选线束分支时，仍然是分支节点 ===")
    _, fn6, p6, b6 = build_service([items[0]]).get_glb()
    log(f"  filename={fn6} parts={json.dumps(p6, ensure_ascii=False)} branches={b6}")

    log("")
    log("=== 7) 裁剪切入口：没识别到分支特征时，拾取点不会被传给导出层 ===")
    from OCC.Extend.DataExchange import read_step_file

    from app.utils.step_to_gltf import _iter_top_solids

    log(f"  clipA   顶层实体数 = {len(_iter_top_solids(read_step_file(str(CLIP_STEP))))}")
    log(f"  harness 顶层实体数 = {len(_iter_top_solids(read_step_file(str(HARNESS_STEP))))}")

    import app.services.catia.service as svc_mod

    captured: list = []
    original = svc_mod.steps_to_gltf

    def spy(entries, out_file, *args, **kwargs):
        captured.append(list(entries))
        return original(entries, out_file, *args, **kwargs)

    svc_mod.steps_to_gltf = spy
    try:
        build_service(items).get_glb()
        build_service([items[1]]).get_glb()
    finally:
        svc_mod.steps_to_gltf = original

    for run, entries in enumerate(captured):
        for entry in entries:
            log(
                f"  run{run}: instance={entry[2]!r} branches={entry[3]} "
                f"pick_points={[tuple(round(v, 1) for v in p) for p in entry[4]]}"
            )
    clip_entries = [e for run in captured for e in run if e[2] == CLIP_NAME]
    log(
        "  卡扣条目的 branches 与 pick_points 都为空: "
        f"{all(not e[3] and not e[4] for e in clip_entries)}"
    )

    for stale in (tmp, tmp2):
        if stale.exists():
            stale.unlink()

    OUT.write_text("\n".join(L), encoding="utf-8")
    print("written", OUT)


if __name__ == "__main__":
    main()
