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
