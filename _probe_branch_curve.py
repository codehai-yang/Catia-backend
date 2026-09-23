"""探测线束分支零件内部结构：找出每根分支的「中心线（电气路径）」到底叫什么名字，
以及能不能用 SPAWorkbench 量出它的长度。

为什么需要：/getselected 的长度字段现在是「直接量用户选中的那个对象」，
用户点分支表皮（面/实体）时 CATIAMeasurable.Length 会直接报「方法 Length 失败」，
只有点中心线（曲线）才能量出来。要兼容两种选法，就必须按分支号去零件里
找到那根中心线曲线，所以得先知道它的真实名字。

运行：
    F:\\office\\conda\\envs\\catia310\\python.exe _probe_branch_curve.py
结果写到 _probe_branch_curve.txt（PowerShell 的 stdout 有时会被吞）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pythoncom  # noqa: E402
from win32com.client import Dispatch, GetActiveObject  # noqa: E402

CASE = r"D:\电缆数模案例\cable example.CATProduct"
OUT = Path(__file__).with_suffix(".txt")
L: list[str] = []


def log(*parts) -> None:
    L.append(" ".join(str(p) for p in parts))


def safe(fn, default="<err>"):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return f"{default} {type(exc).__name__}: {exc}"


def dump_module(name: str, com_obj) -> None:
    """给对象挂上 .Name / .Length 之类的探针，用于实测。"""
    log(f"  [module {name}] {com_obj}")


def main() -> None:
    pythoncom.CoInitialize()

    try:
        app = GetActiveObject("CATIA.Application")
        log("attached to running CATIA")
    except Exception:
        app = Dispatch("CATIA.Application")
        log("started a new CATIA")

    app.Visible = True

    docs = app.Documents
    target = None
    for i in range(1, docs.Count + 1):
        d = docs.Item(i)
        log("open doc:", safe(lambda dd=d: dd.Name))
        if str(safe(lambda dd=d: dd.FullName, "")).lower() == CASE.lower():
            target = d
    if target is None:
        log("opening", CASE, "...")
        target = docs.Open(CASE)
        log("opened:", safe(lambda: target.Name))

    from pycatia.mec_mod_interfaces.part_document import PartDocument  # noqa: E402
    from pycatia.product_structure_interfaces.product_document import ProductDocument  # noqa: E402
    from pycatia.space_analyses_interfaces.spa_workbench import SPAWorkbench  # noqa: E402

    root = ProductDocument(target).product
    log("root product =", safe(lambda: root.name))

    leaf = None

    def walk(prod, depth=0):
        nonlocal leaf
        log("  " * depth + "- " + str(safe(lambda: prod.name, "?")))
        if str(safe(lambda: prod.name, "")) == "多分支1.1":
            leaf = prod
        for child in (safe(lambda: prod.get_children(), []) or []):
            if not isinstance(child, str):
                walk(child, depth + 1)

    walk(root)
    log("leaf found =", leaf is not None, safe(lambda: leaf.name) if leaf else "")

    if leaf is None:
        log("!! 没找到 多分支1.1，下面的结构探测跳过")
        OUT.write_text("\n".join(L), encoding="utf-8")
        return

    ref_doc = leaf.com_object.ReferenceProduct.Parent
    log("ref doc =", safe(lambda: ref_doc.Name))

    part_doc = PartDocument(ref_doc)
    part = part_doc.part
    spa = SPAWorkbench(ref_doc)

    log("")
    log("=== A) 实体 Body（零件几何体）===")
    for b in safe(lambda: list(part.bodies), []) or []:
        log("  Body:", safe(lambda bb=b: bb.name))
        for s in safe(lambda bb=b: list(bb.shapes), []) or []:
            log("     shape:", safe(lambda ss=s: ss.name))

    log("")
    log("=== B) HybridBody / HybridShape（电气线路几何体）===")
    for hb in safe(lambda: list(part.hybrid_bodies), []) or []:
        log("  HybridBody:", safe(lambda h=hb: h.name))
        for hs in safe(lambda h=hb: list(h.hybrid_shapes), []) or []:
            name = safe(lambda x=hs: x.name, "?")
            length = safe(lambda x=hs: spa.get_measurable(part.create_reference_from_object(x)).length)
            radius = safe(lambda x=hs: spa.get_measurable(part.create_reference_from_object(x)).radius)
            log(f"     shape name={name!r} length={length} radius={radius}")

    log("")
    log("=== C) find_object_by_name 逐个候选 ===")
    for cand in [
        "ElecCurve.1", "ElecCurve.2", "ElecCurve.3", "ElecCurve.4",
        "柔性曲线.1", "柔性曲线.2",
        "GSMCircle.1", "GSMCircle.2", "圆.1", "圆.2",
        "EhiBundleSegmentRib.1", "EhiBundleSegmentRib.2",
        "肋.1", "肋.2", "几何体.1", "几何体.2",
    ]:
        try:
            obj = part.find_object_by_name(cand)
            name = safe(lambda o=obj: o.name, "?")
            length = safe(lambda o=obj: spa.get_measurable(part.create_reference_from_object(o)).length)
            log(f"  {cand:28s} -> FOUND name={name!r} length={length}")
        except Exception as exc:  # noqa: BLE001
            log(f"  {cand:28s} -> not found ({type(exc).__name__})")

    log("")
    log("=== D) 工作台/单位参考：量一个已知长度（整体导出参考）===")
    log("  (跳过)")

    OUT.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback

        log("FATAL", type(exc).__name__, exc)
        log(traceback.format_exc())
        OUT.write_text("\n".join(L), encoding="utf-8")
