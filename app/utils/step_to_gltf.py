import sys
from pathlib import Path
from OCC.Extend.DataExchange import read_step_file
from OCC.Core.BRepTools import breptools
from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
from OCC.Core.TCollection import TCollection_AsciiString
from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
from OCC.Core.RWGltf import RWGltf_CafWriter
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Message import Message_ProgressRange
from OCC.Core.gp import gp_Trsf
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.TopoDS import TopoDS_Compound

def step_to_gltf(in_file: str, out_file: str, lin_def: float = 0.1, ang_def: float = 0.5):
    """
    将 STEP 文件转换为 glTF/GLB

    参数:
        in_file: 输入 STEP 文件路径
        out_file: 输出 glTF/GLB 文件路径
        lin_def: 线性偏差 (越小网格越精细，文件越大)
        ang_def: 角度偏差 (弧度，越小网格越平滑)
    """
    # 1. 加载 STEP 文件
    print(f"正在读取: {in_file}")
    shape = read_step_file(in_file)

    # 2. 清理已有网格，然后重新网格化
    print("正在生成网格...")
    breptools.Clean(shape)
    BRepMesh_IncrementalMesh(shape, lin_def, False, ang_def, True).Perform()

    # 3. 将形状放入 XDE 文档 (用于写入 glTF)
    doc = TDocStd_Document("doc")
    XCAFDoc_DocumentTool.ShapeTool(doc.Main()).AddShape(shape)

    # 4. 添加元数据 (可选)
    info = TColStd_IndexedDataMapOfStringString()
    info.Add(TCollection_AsciiString("Generator"),
             TCollection_AsciiString("step_to_gltf.py"))

    # 5. 写入 glTF/GLB 文件
    print(f"正在导出: {out_file}")
    # 根据输出文件后缀判断是否为二进制 GLB
    is_binary = Path(out_file).suffix.lower() == ".glb"
    writer = RWGltf_CafWriter(out_file, is_binary)
    result = writer.Perform(doc, info, Message_ProgressRange())

    if result == IFSelect_RetDone:
        print("转换成功！")
    else:
        print("转换失败！")
        sys.exit(1)


def _matrix_to_trsf(m):
    """把 4x4 行主序变换矩阵转成 gp_Trsf（忽略缩放）。"""
    trsf = gp_Trsf()
    trsf.SetValues(
        m[0][0], m[0][1], m[0][2], m[0][3],
        m[1][0], m[1][1], m[1][2], m[1][3],
        m[2][0], m[2][1], m[2][2], m[2][3],
    )
    return trsf


def steps_to_gltf(entries, out_file, lin_def: float = 0.1, ang_def: float = 0.5):
    """
    读取多个 STEP 文件，各自应用变换矩阵后合并写成一个 glTF/GLB。

    参数:
        entries: [(step_path, matrix_or_None), ...]，matrix 为 4x4 行主序（局部->全局）
        out_file: 输出 glTF/GLB 路径
        lin_def: 线性偏差
        ang_def: 角度偏差
    """
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)

    for step_path, matrix in entries:
        shape = read_step_file(str(step_path))
        if matrix is not None:
            trsf = _matrix_to_trsf(matrix)
            shape = BRepBuilderAPI_Transform(shape, trsf, True).Shape()
        builder.Add(compound, shape)

    breptools.Clean(compound)
    BRepMesh_IncrementalMesh(compound, lin_def, False, ang_def, True).Perform()

    doc = TDocStd_Document("doc")
    XCAFDoc_DocumentTool.ShapeTool(doc.Main()).AddShape(compound)

    info = TColStd_IndexedDataMapOfStringString()
    info.Add(TCollection_AsciiString("Generator"),
             TCollection_AsciiString("step_to_gltf.py"))

    is_binary = Path(out_file).suffix.lower() == ".glb"
    writer = RWGltf_CafWriter(str(out_file), is_binary)
    result = writer.Perform(doc, info, Message_ProgressRange())

    if result != IFSelect_RetDone:
        raise RuntimeError(f"glTF 转换失败: {out_file}")
