"""端到端验证：模拟「用户在 3D 里点分支表皮」这条路径，验证 /getselected 的长度字段。

真实拾取没法脚本化，但选中项的引用名（Selection_RSur:(Face:(Brp:(EhiBundleSegmentRib.2;...)))）
是持久化的 BRep 名，可以用 CreateReferenceFromBRepName 还原成引用再塞进选择集，
从而在不人工点击的情况下跑通：
    选择项(分支表面) -> _branch_index 得分支号 -> _part_measure_context 取零件
    -> _measure_centerline 量中心线 -> get_selected_instances() 返回 length

运行：F:\\office\\conda\\envs\\catia310\\python.exe _verify_branch_pick.py
（会先清空 CATIA 当前选择，跑完也清空）结果见 _verify_branch_pick.txt。

预期：分支 1 -> 175.002，分支 2 -> 245.359（单位 mm）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pythoncom  # noqa: E402
from win32com.client import GetActiveObject  # noqa: E402

from app.services.catia.service import CatiaService  # noqa: E402

# (期望分支号, 分支表面的 BRep 名, 期望长度 mm)
# BRep 名取自真实会话日志里用户点分支表面时 Value.Name 的面包屑部分。
CASES = [
    (
        1,
        "Face:(Brp:(EhiBundleSegmentRib.1;0:(Brp:(ElecCurve.1;2);"
        "Brp:(GSMCircle.1;(Brp:(GSMPlane.5)))));None:();Cf11:())"
        ";EhiBundleSegmentRib.1_ResultOUT;Z0;G3563",
        175.002,
    ),
    (
        2,
        "Face:(Brp:(EhiBundleSegmentRib.2;0:(Brp:(ElecCurve.2;1);"
        "Brp:(GSMCircle.2;(Brp:(GSMPlane.13)))));None:();Cf11:())"
        ";EhiBundleSegmentRib.2_ResultOUT;Z0;G3563",
        245.359,
    ),
]

OUT = Path(__file__).with_suffix(".txt")
L: list[str] = []


def log(*p) -> None:
    L.append(" ".join(str(x) for x in p))


def safe(fn, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        if default is not None:
            return default
        return f"<err {type(exc).__name__}: {exc}>"


def main() -> None:
    svc = CatiaService()
    app = GetActiveObject("CATIA.Application")
    doc = app.ActiveDocument
    sel = doc.Selection
    log(f"active document = {safe(lambda: doc.Name)}")

    from pycatia.in_interfaces.selection import Selection as PSelection
    from pycatia.mec_mod_interfaces.part_document import PartDocument
    from pycatia.product_structure_interfaces.product_document import ProductDocument

    part_doc = None
    docs = app.Documents
    for i in range(1, docs.Count + 1):
        d = docs.Item(i)
        if str(safe(lambda dd=d: dd.Name)).lower() == "multibranchable1.catpart":
            part_doc = d
    if part_doc is None:
        log("!! 没找到 MultiBranchable1.CATPart")
        OUT.write_text("\n".join(L), encoding="utf-8")
        return
    part = PartDocument(part_doc).part

    sel.Clear()
    all_ok = True
    for want_branch, label, want_length in CASES:
        log("")
        log(f"--- 模拟点第 {want_branch} 根分支的表皮 ---")
        try:
            ref = part.create_reference_from_b_rep_name(label, part)
        except Exception as exc:  # noqa: BLE001
            log(f"  create_reference_from_b_rep_name 失败: {exc}")
            all_ok = False
            continue

        sel.Clear()
        try:
            PSelection(sel).add(ref)  # 必须走 pycatia 包装（内部取 .com_object）
        except Exception as exc:  # noqa: BLE001
            log(f"  Selection.Add 失败: {exc}")
            all_ok = False
            continue

        item = sel.Item2(1)
        branch = svc._branch_index(item)
        log(f"  Count2={safe(lambda: sel.Count2)}  Value.Name={safe(lambda: item.Value.Name)}")
        log(f"  解析出的分支号 = {branch}（期望 {want_branch}）")

        response = safe(lambda: svc.get_selected_instances())
        log(f"  /getselected 响应 = {json.dumps(response, ensure_ascii=False)}")
        got = None
        if isinstance(response, dict) and response.get("parts"):
            got = response["parts"][0].get("length")

        ok = (
            branch == want_branch
            and got is not None
            and abs(got - want_length) < 0.01
        )
        all_ok = all_ok and ok
        log(f"  -> {'OK' if ok else 'FAIL'}  length={got}（期望 ≈{want_length}）")

    # 附带回归：_branch_index 加了 DisplayName 兜底后，/getglb 的分支裁剪必须不受影响
    log("")
    log("--- 附带回归：/getglb 仍按分支裁剪（第 2 根分支）---")
    try:
        ref = part.create_reference_from_b_rep_name(CASES[1][1], part)
        sel.Clear()
        PSelection(sel).add(ref)
        data, filename, parts, branches = svc.get_glb()
        log(f"  parts={json.dumps(parts, ensure_ascii=False)}  branches={branches}")
        log(f"  glb size = {len(data)} bytes  filename = {filename}")
        ok = branches == [2] and [p["id"] for p in parts] == ["多分支1.1#2"]
        all_ok = all_ok and ok
        log(f"  -> {'OK' if ok else 'FAIL'}")
    except Exception as exc:  # noqa: BLE001
        all_ok = False
        log(f"  FAIL: {type(exc).__name__}: {exc}")

    sel.Clear()
    log("")
    log(f"=== 结论：{'全部通过' if all_ok else '有失败项'}；选择集已清空 ===")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback

        log("FATAL", type(exc).__name__, exc)
        log(traceback.format_exc())
        OUT.write_text("\n".join(L), encoding="utf-8")
