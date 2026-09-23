"""端到端验证：steps_to_gltf 是否把实例名写进 GLB 的独立节点。"""
import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Extend.DataExchange import write_step_file

from app.utils.step_to_gltf import steps_to_gltf


def identity():
    return [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]


def translated(x, y, z):
    m = identity()
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def read_glb(path):
    data = Path(path).read_bytes()
    jlen, _ = struct.unpack("<II", data[12:20])
    return json.loads(data[20 : 20 + jlen].decode("utf-8"))


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    s1, s2, s3 = tmp / "a.stp", tmp / "b.stp", tmp / "c.stp"
    write_step_file(BRepPrimAPI_MakeBox(1.0, 1.0, 1.0).Shape(), str(s1))
    write_step_file(BRepPrimAPI_MakeBox(2.0, 1.0, 1.0).Shape(), str(s2))
    write_step_file(BRepPrimAPI_MakeBox(1.0, 3.0, 1.0).Shape(), str(s3))

    out = tmp / "out.glb"
    entries = [
        (str(s1), identity(), "Bracket.1"),
        (str(s2), translated(10.0, 0.0, 0.0), "Bracket.2"),
        (str(s3), translated(0.0, 20.0, 0.0), "Clip.1"),
    ]
    steps_to_gltf(entries, str(out))

    gltf = read_glb(out)
    nodes = gltf.get("nodes", [])
    print("node count:", len(nodes))
    for n in nodes:
        print("  name=", n.get("name"), "| mesh=", n.get("mesh"), "| translation=", n.get("translation"))
    print("meshes:", len(gltf.get("meshes", [])))
    print("scene roots:", gltf.get("scenes"))

    # 兼容旧的两元组调用
    out2 = tmp / "out2.glb"
    steps_to_gltf([(str(s1), identity()), (str(s2), None)], str(out2))
    g2 = read_glb(out2)
    print("legacy 2-tuple names:", [n.get("name") for n in g2.get("nodes", [])])
