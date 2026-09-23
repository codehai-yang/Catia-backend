"""STEP -> glTF/GLB 转换工具。

关键点：
1. 每个零件写成**独立的 XDE 标签**，以调用方传入的名称（CATIA 零件实例名，如
   ``Bracket.1``）作为 glTF 节点名（``node.name``），前端按节点名高亮。
2. 支持**按分支裁剪**：线束类零件的多个分支几何都装在同一个 CATPart 里，
   整体导出会得到「整根线束」。调用方给出分支序号（或 3D 拾取点）后，这里只把
   选中的那个实体写进 GLB。

注意：旧实现把所有零件合并进一个 ``TopoDS_Compound`` 再整体 AddShape，
结果 GLB 里只有一个节点且没有名字，前端无法定位到单个零件，故不再合并。
"""

from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex, BRepBuilderAPI_Transform
from OCC.Core.BRepExtrema import BRepExtrema_DistShapeShape
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepTools import breptools
from OCC.Core.gp import gp_Pnt, gp_Trsf
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Message import Message_ProgressRange
from OCC.Core.RWGltf import RWGltf_CafWriter
from OCC.Core.RWMesh import RWMesh_NameFormat
from OCC.Core.TCollection import TCollection_AsciiString
from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopAbs import TopAbs_ShapeEnum
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Shape, topods
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Extend.DataExchange import read_step_file

PickPoint = tuple[float, float, float]


@dataclass
class StepEntry:
    """一个待写入 GLB 的 STEP 零件。

    branches / pick_points 用来把「一个 CATPart 里的多个分支」裁剪成选中的那部分：
    线束的每个分支在 STEP 里是一个独立实体，实体序号与 CATIA 树里的分支序号一致。
    """

    step_path: str
    matrix: list[list[float]] | None = None
    name: str | None = None
    branches: list[int] = field(default_factory=list)
    pick_points: list[PickPoint] = field(default_factory=list)


def _matrix_to_trsf(m: list[list[float]]) -> gp_Trsf:
    """把 4x4 行主序变换矩阵转成 gp_Trsf（忽略缩放）。"""
    trsf = gp_Trsf()
    trsf.SetValues(
        m[0][0], m[0][1], m[0][2], m[0][3],
        m[1][0], m[1][1], m[1][2], m[1][3],
        m[2][0], m[2][1], m[2][2], m[2][3],
    )
    return trsf


def _as_entry(entry) -> StepEntry:
    """兼容旧的 (path, matrix[, name]) 元组写法，同时支持 StepEntry 与更长的元组。"""
    if isinstance(entry, StepEntry):
        return entry
    step_path = entry[0]
    matrix = entry[1] if len(entry) > 1 else None
    name = entry[2] if len(entry) > 2 else None
    branches = entry[3] if len(entry) > 3 else None
    pick_points = entry[4] if len(entry) > 4 else None
    return StepEntry(
        step_path=str(step_path),
        matrix=matrix,
        name=name,
        branches=list(branches or []),
        pick_points=[tuple(p) for p in (pick_points or [])],
    )


def _iter_top_solids(shape: TopoDS_Shape) -> list[TopoDS_Shape]:
    """拆出 shape 里所有顶层实体。

    线束 CATPart 的每个分支是一个实体，导出 STEP 后仍按原顺序保留，
    所以「第 N 个实体」就是「第 N 个分支」。
    """
    solids: list[TopoDS_Shape] = []
    explorer = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        try:
            solids.append(topods.Solid(explorer.Current()))
        except Exception:  # noqa: BLE001
            solids.append(explorer.Current())
        explorer.Next()
    return solids


def _distance_to_point(shape: TopoDS_Shape, point: PickPoint) -> float:
    """实体到拾取点的最短距离（点在实体表面上时约等于 0）。"""
    try:
        vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex()
        dist = BRepExtrema_DistShapeShape(shape, vertex)
        dist.Perform()
        if dist.IsDone():
            return float(dist.Value())
    except Exception as exc:  # noqa: BLE001
        logger.warning("distance calc failed for {}: {}", point, exc)
    return float("inf")


def _merge_solids(solids: list[TopoDS_Shape]) -> TopoDS_Shape:
    if len(solids) == 1:
        return solids[0]
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for solid in solids:
        builder.Add(comp, solid)
    return comp


def select_branch_geometry(
    shape: TopoDS_Shape,
    branches: list[int] | None = None,
    pick_points: list[PickPoint] | None = None,
) -> tuple[TopoDS_Shape, list[int]]:
    """从「一整根线束」里挑出选中的分支实体。

    优先用 3D 拾取点做几何判定（点到实体距离最小者胜出，最可靠）；
    没有拾取点时才退回到分支序号。返回 (裁剪后的 shape, 实际选中的分支序号)。
    """
    branches = list(branches or [])
    pick_points = list(pick_points or [])
    if not branches and not pick_points:
        return shape, []

    solids = _iter_top_solids(shape)
    if not solids:
        return shape, []

    chosen: set[int] = set()
    if pick_points:
        for point in pick_points:
            best_index, best_dist = -1, float("inf")
            for i, solid in enumerate(solids):
                d = _distance_to_point(solid, point)
                if d < best_dist:
                    best_index, best_dist = i, d
            if best_index >= 0:
                chosen.add(best_index)
    if not chosen:
        for b in branches:
            if 1 <= b <= len(solids):
                chosen.add(b - 1)

    if not chosen:
        logger.warning(
            "branch filter matched nothing (branches={}, solids={}); keeping whole part",
            branches,
            len(solids),
        )
        return shape, []

    ordered = sorted(chosen)
    return _merge_solids([solids[i] for i in ordered]), [i + 1 for i in ordered]


def _add_named_shape(
    shape_tool,
    shape,
    name: str | None,
    index: int,
    lin_def: float,
    ang_def: float,
):
    """网格化一个 shape，作为独立节点加入 XDE 文档，并把 name 写进节点名。"""
    breptools.Clean(shape)
    BRepMesh_IncrementalMesh(shape, lin_def, False, ang_def, True).Perform()

    label = shape_tool.AddShape(shape, False)
    node_name = str(name).strip() if name else ""
    if not node_name:
        node_name = f"part_{index}"
    try:
        # 注意：这里必须传 Python str（SWIG 的 typemap 只接受 str），
        # 传 TCollection_ExtendedString 会报参数类型错误。
        TDataStd_Name.Set(label, node_name)
    except Exception as exc:
        logger.warning("failed to set glTF node name {!r}: {}", node_name, exc)
    return label


def _write_document(doc, out_file: str) -> None:
    info = TColStd_IndexedDataMapOfStringString()
    info.Add(
        TCollection_AsciiString("Generator"),
        TCollection_AsciiString("step_to_gltf.py"),
    )

    is_binary = Path(out_file).suffix.lower() == ".glb"
    writer = RWGltf_CafWriter(str(out_file), is_binary)
    # 让 glTF 节点名取自 XDE 标签名（即零件实例名），前端据此做高亮
    writer.SetNodeNameFormat(RWMesh_NameFormat.RWMesh_NameFormat_InstanceOrProduct)

    result = writer.Perform(doc, info, Message_ProgressRange())
    if result != IFSelect_RetDone:
        raise RuntimeError(f"glTF 转换失败: {out_file}")


def _load_entry_shape(entry: StepEntry) -> TopoDS_Shape:
    shape = read_step_file(str(entry.step_path))
    if entry.matrix is not None:
        # 先把实例位姿烘进几何，再做分支判定，这样拾取点(总成坐标)才能直接比较
        shape = BRepBuilderAPI_Transform(shape, _matrix_to_trsf(entry.matrix), True).Shape()
    return shape


def step_to_gltf(
    in_file: str,
    out_file: str,
    lin_def: float = 0.1,
    ang_def: float = 0.5,
    name: str | None = None,
    branches: list[int] | None = None,
    pick_points: list[PickPoint] | None = None,
):
    """把单个 STEP 文件转换为 glTF/GLB。

    参数:
        in_file: 输入 STEP 文件路径
        out_file: 输出 glTF/GLB 文件路径
        lin_def: 线性偏差 (越小网格越精细，文件越大)
        ang_def: 角度偏差 (弧度，越小网格越平滑)
        name: 写入 glTF 节点名的名称（通常是 CATIA 零件实例名）
        branches: 只要 STEP 里的第 N 个分支实体（1 起）；为空表示整个零件
        pick_points: 3D 拾取点（总成坐标），比 branches 更可靠，优先使用
    """
    doc = TDocStd_Document("doc")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    entry = StepEntry(step_path=str(in_file), name=name, branches=list(branches or []),
                      pick_points=[tuple(p) for p in (pick_points or [])])
    shape = _load_entry_shape(entry)
    shape, kept = select_branch_geometry(shape, entry.branches, entry.pick_points)
    if kept:
        logger.info("step_to_gltf kept branch(es) {} of {}", kept, in_file)
    _add_named_shape(shape_tool, shape, name, 0, lin_def, ang_def)

    _write_document(doc, out_file)


def steps_to_gltf(
    entries,
    out_file: str,
    lin_def: float = 0.1,
    ang_def: float = 0.5,
    branches: list[int] | None = None,
    pick_points: list[PickPoint] | None = None,
):
    """读取多个 STEP 文件，各自应用变换矩阵后合并写成一个 glTF/GLB。

    每个零件写成**一个独立节点**，节点名 = 该零件的实例名，供前端高亮；
    若该零件内部含多个分支且给出 branches/pick_points，则只写入选中的分支。

    参数:
        entries: [(step_path, matrix_or_None, name_or_None[, branches[, pick_points]]), ...]
                 也可以是 StepEntry；matrix 为 4x4 行主序（局部->全局）
        out_file: 输出 glTF/GLB 路径
        lin_def / ang_def: 网格精度
        branches / pick_points: 函数级默认值，用于没有单独指定裁剪信息的 entry
    """
    doc = TDocStd_Document("doc")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    default_branches = list(branches or [])
    default_points = [tuple(p) for p in (pick_points or [])]

    for index, raw in enumerate(entries):
        entry = _as_entry(raw)
        if not entry.branches:
            entry.branches = list(default_branches)
        if not entry.pick_points:
            entry.pick_points = list(default_points)

        shape = _load_entry_shape(entry)
        shape, kept = select_branch_geometry(shape, entry.branches, entry.pick_points)
        if kept:
            logger.info(
                "steps_to_gltf entry {} kept branch(es) {} (requested branches={})",
                index, kept, entry.branches,
            )

        _add_named_shape(shape_tool, shape, entry.name, index, lin_def, ang_def)

    _write_document(doc, out_file)
