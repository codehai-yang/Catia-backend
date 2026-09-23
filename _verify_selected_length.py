"""验证 /getselected 的长度字段：点分支表皮 / 点中心线都能拿到分支长度。

背景：原来直接量「用户选中的那个对象」的 Measurable.Length，用户点分支**表皮**（肋实体）时
直接报「方法 Length 失败」，只有点中心线（柔性曲线）才量得出来。现在改成按分支号去零件里
找中心线（ElecCurve.N）再量，两种选法结果一致。

运行：
    F:\\office\\conda\\envs\\catia310\\python.exe _verify_selected_length.py
结果写到 _verify_selected_length.txt。

预期（cable example，分支 1~4 中心线长）：
    175.002 / 245.359 / 250.354 / 279.096 mm，四根合计 949.811
并且：如果运行前在 CATIA 里选中了分支（表皮或中心线都行），
最后的「真实 get_selected_instances()」应当给出对应的 length，而不是 null。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pythoncom  # noqa: E402
from win32com.client import GetActiveObject  # noqa: E402

from app.services.catia.service import CatiaService  # noqa: E402

EXPECT = {1: 175.002, 2: 245.359, 3: 250.354, 4: 279.096}
OUT = Path(__file__).with_suffix(".txt")
L: list[str] = []


def log(*p) -> None:
    L.append(" ".join(str(x) for x in p))


def safe(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return f"<err {type(exc).__name__}: {exc}>"


def main() -> None:
    svc = CatiaService()
    app = GetActiveObject("CATIA.Application")

    # 1) 找到线束零件的 CATPart 文档（直接按文档找，不依赖装配树）
    from pycatia.mec_mod_interfaces.part_document import PartDocument
    from pycatia.space_analyses_interfaces.spa_workbench import SPAWorkbench

    docs = app.Documents
    part_doc = None
    for i in range(1, docs.Count + 1):
        d = docs.Item(i)
        if str(safe(lambda dd=d: dd.Name)).lower() == "multibranchable1.catpart":
            part_doc = d
    if part_doc is None:
        log("!! 没找到 MultiBranchable1.CATPart，请先在 CATIA 打开电缆案例")
        OUT.write_text("\n".join(L), encoding="utf-8")
        return

    part = PartDocument(part_doc).part
    part_spa = SPAWorkbench(part_doc)

    log("=== 1) 逐分支量中心线（新逻辑核心）===")
    ok = True
    for branch, expect in EXPECT.items():
        got = svc._measure_centerline(part, part_spa, branch)
        match = got is not None and abs(got - expect) < 0.01
        ok = ok and match
        log(f"  分支 {branch}: got={got}  expect≈{expect}  {'OK' if match else 'MISMATCH'}")
    log(f"  -> {'全部通过' if ok else '有偏差，请看上面'}")

    log("")
    log("=== 2) _total_length 汇总（分支中心线优先 + 直接量兜底）===")
    ctx = {"多分支1.1": (part, part_spa)}
    cases = [
        ("四根分支求和", "多分支1.1", {"多分支1.1": [1, 2, 3, 4]}, {}, ctx, 949.811),
        ("两根分支", "多分支1.1", {"多分支1.1": [2, 3]}, {}, ctx, 495.712),
        ("重复分支去重后仍为单根", "多分支1.1", {"多分支1.1": [2]}, {}, ctx, 245.359),
        ("无分支->用直接量结果", "clipA.3", {}, {"clipA.3": [20.0, 30.0]}, {}, 50.0),
        ("无分支且量不到->null", "clipA.3", {}, {}, {}, None),
        ("分支量不到->退回直接量", "多分支1.1", {"多分支1.1": [99]},
         {"多分支1.1": [12.5]}, ctx, 12.5),
    ]
    for title, name, bids, direct, ctx_map, expect in cases:
        got = svc._total_length(name, bids, direct, ctx_map)
        if expect is None:
            match = got is None
        else:
            match = got is not None and abs(got - expect) < 0.01
        log(f"  {title}: got={got}  {'OK' if match else 'MISMATCH (expect ' + str(expect) + ')'}")

    log("")
    log("=== 3) 真实会话：当前选中项 -> get_selected_instances() ===")
    doc = app.ActiveDocument
    sel = doc.Selection
    count = safe(lambda: sel.Count2)
    log(f"  active document = {safe(lambda: doc.Name)}   Count2 = {count}")
    if isinstance(count, int) and count > 0:
        for i in range(1, count + 1):
            item = sel.Item2(i)
            log(f"  item {i}: Type={safe(lambda it=item: it.Type)}"
                f" leaf={safe(lambda it=item: it.LeafProduct.Name)}"
                f" branch={safe(lambda it=item: svc._branch_index(it))}")
    else:
        log("  (没有选中项：请先在 CATIA 里点一根分支的表皮/中心线，再重跑本脚本)")
    log(f"  response = {json.dumps(safe(lambda: svc.get_selected_instances()), ensure_ascii=False)}")

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
