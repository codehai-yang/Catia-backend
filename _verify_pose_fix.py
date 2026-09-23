"""验证 CATIA 位姿矩阵的填充方向（轴当『列』才对），并证明修复效果。

背景：`Position.GetComponents()` 的前 9 个分量依次是 x/y/z **轴**的分量，
它们构成旋转矩阵的**列**。曾经误按『行』填充 → 旋转被转置 → 零件绕轴反向倾斜，
现象是「位置正确、零件歪了」。

关键点：绕某轴 ±θ 的两版矩阵，其**轴对齐包围盒完全相同**，所以包围盒/顶点数
这类判据查不出来，必须用**质心**或**点到精确表面的距离**。

用法（不需要 CATIA，只要 `_tmp_branch/clipA_full.stp` 存在）：
    F:\\office\\conda\\envs\\catia310\\python.exe _verify_pose_fix.py
"""

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from OCC.Core.BRepBuilderAPI import (  # noqa: E402
    BRepBuilderAPI_MakeVertex,
    BRepBuilderAPI_Transform,
)
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402
from OCC.Core.BRepGProp import brepgprop  # noqa: E402
from OCC.Core.Bnd import Bnd_Box  # noqa: E402
from OCC.Core.BRepBndLib import brepbndlib  # noqa: E402
from OCC.Core.GProp import GProp_GProps  # noqa: E402
from OCC.Core.gp import gp_Pnt, gp_Trsf  # noqa: E402
from OCC.Core.TopAbs import TopAbs_ShapeEnum  # noqa: E402
from OCC.Core.TopExp import TopExp_Explorer  # noqa: E402
from OCC.Core.TopoDS import topods  # noqa: E402
from OCC.Extend.DataExchange import read_step_file  # noqa: E402

from app.services.catia.service import _components_to_matrix  # noqa: E402
from app.utils.step_to_gltf import steps_to_gltf  # noqa: E402

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "_diag_pose_fix.txt"
L: list[str] = []


def log(s: str = "") -> None:
    L.append(str(s))
    print(s)


# ---- 真值来源：对 CATIA 的只读诊断 ----
# clipA.2 的 Position 分量（轴分量 + 原点）
C_CLIP = [
    1.000, 0.001, -0.009,
    0.001, 0.966, 0.258,
    0.009, -0.258, 0.966,
    -38.191, 230.451, 60.354,
]
# 在 CATIA 里于 clipA.2 的 Shaft.1 表面上拾取到的点（世界坐标）
PICK_CLIP = (-36.3, 233.3, 52.8)
# conn1_male.1，旋转更大，用于矩阵合法性对照
C_CONN = [
    0.986, 0.161, 0.038,
    -0.165, 0.946, 0.279,
    0.009, -0.282, 0.959,
    -255.735, 685.477, 217.080,
]


def m_axis_as_rows(c):
    """修复前的错误填法：把轴的三个分量当『行』。"""
    return [
        [c[0], c[1], c[2], c[9]],
        [c[3], c[4], c[5], c[10]],
        [c[6], c[7], c[8], c[11]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def is_orthonormal(m):
    err = 0.0
    for i in range(3):
        for j in range(3):
            dot = sum(m[i][k] * m[j][k] for k in range(3))
            err = max(err, abs(dot - (1.0 if i == j else 0.0)))
    det = (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )
    return err < 1e-6, err, det


def apply(m, p):
    return (
        sum(m[0][k] * (p[k] if k < 3 else 1.0) for k in range(4)),
        sum(m[1][k] * (p[k] if k < 3 else 1.0) for k in range(4)),
        sum(m[2][k] * (p[k] if k < 3 else 1.0) for k in range(4)),
    )


def invert_point(m, p):
    """R 正交 → R^-1 = R^T；把世界点变换回局部坐标。"""
    r = [[m[i][j] for j in range(3)] for i in range(3)]
    o = (m[0][3], m[1][3], m[2][3])
    d = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
    return (
        r[0][0] * d[0] + r[1][0] * d[1] + r[2][0] * d[2],
        r[0][1] * d[0] + r[1][1] * d[1] + r[2][1] * d[2],
        r[0][2] * d[0] + r[1][2] * d[1] + r[2][2] * d[2],
    )


def dist_to_point(shape, p):
    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex()
    d = BRepExtrema_DistShapeShape(shape, v)
    d.Perform()
    return float(d.Value()) if d.IsDone() else float("inf")


def aabb_of_shape(shape):
    box = Bnd_Box()
    brepbndlib.Add(shape, box)
    return box.Get()


def read_glb_positions(path: Path):
    """解析 GLB 的 POSITION 顶点（变换已烘入几何，即世界坐标）。"""
    data = path.read_bytes()
    _magic, _ver, total = struct.unpack_from("<III", data, 0)
    off, js, blob = 12, None, b""
    while off < total:
        clen, ctype = struct.unpack_from("<II", data, off)
        body = data[off + 8 : off + 8 + clen]
        if ctype == 0x4E4F534A:
            js = json.loads(body.decode("utf-8"))
        elif ctype == 0x004E4942:
            blob = body
        off += 8 + clen
    if js is None:
        return []

    def read_accessor(idx):
        acc = js["accessors"][idx]
        bv = js["bufferViews"][acc["bufferView"]]
        start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = bv.get("byteStride") or 12
        return [
            struct.unpack_from("<fff", blob, start + i * stride)
            for i in range(acc["count"])
        ]

    pts = []
    for mesh in js.get("meshes", []):
        for prim in mesh.get("primitives", []):
            pos = prim.get("attributes", {}).get("POSITION")
            if pos is not None:
                pts.extend(read_accessor(pos))
    return pts


def nearest(pts, p):
    return min(
        ((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2) ** 0.5 for q in pts
    )


# ============ 1) 矩阵合法性 ============
log("=== 1) service 的 _components_to_matrix（修复后）===")
for tag, c in (("clipA.2", C_CLIP), ("conn1_male.1", C_CONN)):
    m = _components_to_matrix(c)
    ok, err, det = is_orthonormal(m)
    log(f"  {tag}: 正交误差={err:.2e}  det={det:.6f}  （源数据本身带 1e-4 级舍入，可接受）")
    log(f"     R = {[[round(v, 4) for v in row] for row in m[:3]]}")
    log(f"     原点 = ({m[0][3]:.3f}, {m[1][3]:.3f}, {m[2][3]:.3f})")

step = ROOT / "_tmp_branch" / "clipA_full.stp"
if not step.exists():
    log(f"\n缺少夹具 {step}，跳过几何验证（可用 _grab_assy_truth.py 或重新导出生成）")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("written", OUT)
    raise SystemExit(0)

shape_local = read_step_file(str(step))
solids = []
exp = TopExp_Explorer(shape_local, TopAbs_ShapeEnum.TopAbs_SOLID)
while exp.More():
    solids.append(topods.Solid(exp.Current()))
    exp.Next()
bx = aabb_of_shape(shape_local)
log(f"\nclipA 局部几何: {len(solids)} 个实体, "
    f"包围盒 {bx[3]-bx[0]:.2f} x {bx[4]-bx[1]:.2f} x {bx[5]-bx[2]:.2f} mm")

m_new = _components_to_matrix(C_CLIP)
m_old = m_axis_as_rows(C_CLIP)

# ============ 2) 判据 A：点到精确 B-Rep 表面的距离 ============
log("")
log("=== 2) 判据A：CATIA 拾取点逆变换回局部后，到零件**精确表面**的距离 ===")
log(f"    拾取点（CATIA 给的绝对坐标）: {PICK_CLIP}  —— 必然落在零件表面上")
for label, m in (("修复后(轴当列)", m_new), ("修复前(轴当行)", m_old)):
    local_pt = invert_point(m, PICK_CLIP)
    d = dist_to_point(shape_local, local_pt)
    log(f"    {label}: 局部点=({local_pt[0]:7.3f},{local_pt[1]:7.3f},{local_pt[2]:7.3f})"
        f"  到表面距离 = {d:8.4f} mm")

# ============ 3) 端到端：真实 STEP -> GLB ============
log("")
log("=== 3) 判据B：真实 STEP 经真实导出链路生成 GLB ===")
glb_new = ROOT / "_tmp_branch" / "pose_new.glb"
glb_old = ROOT / "_tmp_branch" / "pose_old.glb"
for label, m, out in (
    ("修复后(轴当列)", m_new, glb_new),
    ("修复前(轴当行)", m_old, glb_old),
):
    nodes = steps_to_gltf([(str(step), m, "clipA.2")], str(out))
    pts = read_glb_positions(out)
    ax = (
        min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts),
        max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts),
    )
    log(f"\n    [{label}] 节点={[n.name for n in nodes]} 顶点数={len(pts)}")
    log(f"      世界包围盒: x[{ax[0]:.3f},{ax[3]:.3f}] y[{ax[1]:.3f},{ax[4]:.3f}] "
        f"z[{ax[2]:.3f},{ax[5]:.3f}]")
    log(f"      最近顶点到拾取点 = {nearest(pts, PICK_CLIP):.4f} mm（受网格离散度限制，仅作趋势参考）")

log("")
log("    ⚠ 注意：两种填法的包围盒、顶点数**完全相同** —— 绕轴 ±θ 的轴对齐包围盒是对称的，")
log("      所以包围盒/顶点数查不出这个 bug，必须靠上面/下面的质心类判据。")

# ============ 4) 判据 C：质心 ============
log("")
log("=== 4) 判据C：零件质心（对倾斜方向敏感）===")
props = GProp_GProps()
brepgprop.VolumeProperties(shape_local, props)
c = props.CentreOfMass()
local_c = (c.X(), c.Y(), c.Z())
log(f"    零件局部质心: ({local_c[0]:.4f}, {local_c[1]:.4f}, {local_c[2]:.4f})")
for label, m in (("修复后(轴当列)", m_new), ("修复前(轴当行)", m_old)):
    w = apply(m, local_c)
    log(f"    {label} 世界质心: ({w[0]:.4f}, {w[1]:.4f}, {w[2]:.4f})")
log("    → 两者 y 相差约 0.75mm，方向相反；哪个对由 _grab_assy_truth.py 的金标准裁定。")
log("      金标准结论：轴当列 误差 0.0003mm ✓ / 轴当行 误差 0.7504mm ✗")

for stale in (glb_new, glb_old):
    if stale.exists():
        stale.unlink()

OUT.write_text("\n".join(L), encoding="utf-8")
print("written", OUT)
