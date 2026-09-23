"""验证：多个带名字的零件能否在 GLB 里生成独立且已命名的节点。"""
import json
import struct
from pathlib import Path

from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.BRepTools import breptools
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Message import Message_ProgressRange
from OCC.Core.RWGltf import RWGltf_CafWriter
from OCC.Core.RWMesh import RWMesh_NameFormat
from OCC.Core.TColStd import TColStd_IndexedDataMapOfStringString
from OCC.Core.TDataStd import TDataStd_Name
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool


def build_glb(out_file, names, name_format):
    doc = TDocStd_Document("d")
    tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    for nm in names:
        shp = BRepPrimAPI_MakeBox(1.0, 1.0, 1.0).Shape()
        breptools.Clean(shp)
        BRepMesh_IncrementalMesh(shp, 0.1, False, 0.5, True).Perform()
        label = tool.AddShape(shp, False)
        TDataStd_Name.Set(label, nm)

    writer = RWGltf_CafWriter(out_file, True)
    writer.SetNodeNameFormat(name_format)
    res = writer.Perform(doc, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange())
    assert res == IFSelect_RetDone, "write failed"


def read_glb_nodes(path):
    data = Path(path).read_bytes()
    jlen, _ = struct.unpack("<II", data[12:20])
    return json.loads(data[20 : 20 + jlen].decode("utf-8"))


names = ["Bracket.1", "Bracket.2", "Clip.1"]

for fmt_name in ["Product", "InstanceOrProduct", "ProductAndInstance"]:
    out = rf"e:\office\CatiaProject\_occ_fmt_{fmt_name}.glb"
    build_glb(out, names, getattr(RWMesh_NameFormat, f"RWMesh_NameFormat_{fmt_name}"))
    g = read_glb_nodes(out)
    nodes = g.get("nodes", [])
    print(f"[{fmt_name}] nodes={len(nodes)} names={[n.get('name') for n in nodes]} meshes={len(g.get('meshes', []))}")
