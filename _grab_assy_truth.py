"""金标准：让 CATIA 自己把整个装配导出为 STEP（位置由 CATIA 烘好），
再拿里面的卡扣实体质心，与「轴当列 / 轴当行」两种填法算出的世界质心对比。

这是完全独立于我们矩阵推导的第三方真值，能钉死倾斜方向。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_diag_assy_truth.txt"
L: list[str] = []


def log(s: str = "") -> None:
    L.append(str(s))
    print(s)


# ---------- 1) 用 CATIA 导出整个装配 ----------
assy_step = ROOT / "_tmp_branch" / "assy_full.stp"
try:
    import pythoncom
    from win32com.client import GetActiveObject

    pythoncom.CoInitialize()
    app = GetActiveObject("CATIA.Application")
    doc = app.ActiveDocument
    log(f"active document: {doc.Name}")
    if assy_step.exists():
        assy_step.unlink()
    doc.ExportData(str(assy_step), "stp")
    log(f"exported: {assy_step.exists()}  {assy_step.stat().st_size // 1024 if assy_step.exists() else 0} KB")
except Exception as exc:  # noqa: BLE001
    log(f"CATIA export failed: {type(exc).__name__}: {exc}")

# ---------- 2) 解析装配 STEP，找卡扣实体 ----------
from OCC.Core.BRepGProp import brepgprop  # noqa: E402
from OCC.Core.GProp import GProp_GProps  # noqa: E402
from OCC.Core.TopAbs import TopAbs_ShapeEnum  # noqa: E402
from OCC.Core.TopExp import TopExp_Explorer  # noqa: E402
from OCC.Core.TopoDS import topods  # noqa: E402
from OCC.Extend.DataExchange import read_step_file  # noqa: E402

# clipA.2 的局部原点（CATIA 里读到的世界坐标）与该零件的局部质心
ORIGIN_CLIP2 = (-38.191, 230.451, 60.354)
LOCAL_CENTER = (0.0, 0.0, -1.4531)

if assy_step.exists():
    log("")
    log(f"=== 解析装配 STEP: {assy_step.name} ===")
    shape = read_step_file(str(assy_step))
    exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    solids = []
    while exp.More():
        solids.append(topods.Solid(exp.Current()))
        exp.Next()
    log(f"装配里实体总数: {len(solids)}")

    cands = []
    for i, s in enumerate(solids):
        props = GProp_GProps()
        try:
            brepgprop.VolumeProperties(s, props)
        except Exception:  # noqa: BLE001
            continue
        c = props.CentreOfMass()
        vol = props.Mass()
        d = (
            (c.X() - ORIGIN_CLIP2[0]) ** 2
            + (c.Y() - ORIGIN_CLIP2[1]) ** 2
            + (c.Z() - ORIGIN_CLIP2[2]) ** 2
        ) ** 0.5
        cands.append((d, i, c.X(), c.Y(), c.Z(), vol))

    cands.sort()
    log("")
    log("离 clipA.2 原点最近的 6 个实体（按质心距离）:")
    for d, i, x, y, z, vol in cands[:6]:
        log(f"  #{i:<4} 质心=({x:10.4f},{y:10.4f},{z:10.4f})  距原点={d:8.4f}  体积={vol:.2f}")

    # 取最近的那个当作 clipA.2 的 Shaft
    d0, i0, tx, ty, tz, _ = cands[0]
    log("")
    log(f"=== 真值（CATIA 烘出的卡扣世界质心）: ({tx:.4f}, {ty:.4f}, {tz:.4f}) ===")

    # ---------- 3) 与两种填法对比 ----------
    C_CLIP = [
        1.000, 0.001, -0.009,
        0.001, 0.966, 0.258,
        0.009, -0.258, 0.966,
        -38.191, 230.451, 60.354,
    ]

    def m_cols(c):
        return [
            [c[0], c[3], c[6], c[9]],
            [c[1], c[4], c[7], c[10]],
            [c[2], c[5], c[8], c[11]],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def m_rows(c):
        return [
            [c[0], c[1], c[2], c[9]],
            [c[3], c[4], c[5], c[10]],
            [c[6], c[7], c[8], c[11]],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def apply(m, p):
        return (
            sum(m[0][k] * (p[k] if k < 3 else 1.0) for k in range(4)),
            sum(m[1][k] * (p[k] if k < 3 else 1.0) for k in range(4)),
            sum(m[2][k] * (p[k] if k < 3 else 1.0) for k in range(4)),
        )

    log("")
    log("=== 对比：局部质心经两种填法变换后的世界质心 vs CATIA 真值 ===")
    for label, m in (("修复后(轴当列)", m_cols(C_CLIP)), ("修复前(轴当行)", m_rows(C_CLIP))):
        w = apply(m, LOCAL_CENTER)
        err = ((w[0] - tx) ** 2 + (w[1] - ty) ** 2 + (w[2] - tz) ** 2) ** 0.5
        log(f"  {label}: ({w[0]:10.4f},{w[1]:10.4f},{w[2]:10.4f})   误差 = {err:.4f} mm")
else:
    log("")
    log("装配 STEP 不可用，跳过金标准对比")

OUT.write_text("\n".join(L), encoding="utf-8")
print("written", OUT)
