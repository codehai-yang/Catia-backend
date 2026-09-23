"""STEP -> glTF/GLB 转换工具。

关键点：导出时把**每个零件作为独立的 XDE 标签**写入，并以调用方传入的名称
（CATIA 的零件实例名，如 ``Bracket.1``）作为 glTF 节点名（``node.name``）。
前端加载 GLB 后遍历场景，按节点名即可把选中的实例高亮出来。

注意：旧实现把所有零件合并进一个 ``TopoDS_Compound`` 再整体 AddShape，
结果 GLB 里只有一个节点且没有名字，前端无法定位到单个零件，故这里不再合并。
"""

from pathlib import Path

from loguru import logger
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepTools import breptools
from OCC.Core.gp import gp_Trsf
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Message import Message_ProgressRange
from OCC.Core.RWGltf import RWGltf_CafWriter
from OCC.Core.RWMesh import RWMesh_NameFormat
from OCC.Core.TCollection import TCollection_AsciiString
from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Extend.DataExchange import read_step_file


def _matrix_to_trsf(m: list[list[float]]) -> gp_Trsf:
    """把 4x4 行主序变换矩阵转成 gp_Trsf（忽略缩放）。"""
    trsf = gp_Trsf()
    trsf.SetValues(
        m[0][0], m[0][1], m[0][2], m[0][3],
        m[1][0], m[1][1], m[1][2], m[1][3],
        m[2][0], m[2][1], m[2][2], m[2][3],
    )
    return trsf


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


def step_to_gltf(
    in_file: str,
    out_file: str,
    lin_def: float = 0.1,
    ang_def: float = 0.5,
    name: str | None = None,
):
    """把单个 STEP 文件转换为 glTF/GLB。

    参数:
        in_file: 输入 STEP 文件路径
        out_file: 输出 glTF/GLB 文件路径
        lin_def: 线性偏差 (越小网格越精细，文件越大)
        ang_def: 角度偏差 (弧度，越小网格越平滑)
        name: 写入 glTF 节点名的名称（通常是 CATIA 零件实例名）
    """
    doc = TDocStd_Document("doc")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    shape = read_step_file(in_file)
    _add_named_shape(shape_tool, shape, name, 0, lin_def, ang_def)

    _write_document(doc, out_file)


def steps_to_gltf(
    entries,
    out_file: str,
    lin_def: float = 0.1,
    ang_def: float = 0.5,
):
    """读取多个 STEP 文件，各自应用变换矩阵后合并写成一个 glTF/GLB。

    每个零件写成**一个独立节点**，节点名 = 该零件的实例名，供前端高亮。

    参数:
        entries: [(step_path, matrix_or_None, name_or_None), ...]
                 matrix 为 4x4 行主序（局部->全局）；name 为零件实例名（可省略）
        out_file: 输出 glTF/GLB 路径
        lin_def: 线性偏差
        ang_def: 角度偏差
    """
    doc = TDocStd_Document("doc")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())

    for index, entry in enumerate(entries):
        step_path, matrix = entry[0], entry[1]
        name = entry[2] if len(entry) > 2 else None

        shape = read_step_file(str(step_path))
        if matrix is not None:
            trsf = _matrix_to_trsf(matrix)
            shape = BRepBuilderAPI_Transform(shape, trsf, True).Shape()

        _add_named_shape(shape_tool, shape, name, index, lin_def, ang_def)

    _write_document(doc, out_file)
