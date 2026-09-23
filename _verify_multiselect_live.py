"""验证多选（多根分支 / 分支+卡扣）是否能全部导出。

需要 CATIA 里已经选好东西，属于「实时会话验证」：
    1. 在 3D 视图 Ctrl 多选两根分支（或 一根分支 + 一个卡扣）
    2. 运行：F:\\office\\conda\\envs\\catia310\\python.exe _verify_multiselect_live.py
    3. 看 _verify_multiselect_live.txt

预期：
  - parts 条数 == 选择里能识别的单元数（每根分支各一项，节点名形如 多分支1.1#3）
  - GLB 里的节点名与 parts[].id 严格一致
  - branches 非空且与 parts 中 branch 非空的项对应
"""

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pythoncom  # noqa: E402

pythoncom.CoInitialize()

from win32com.client import GetActiveObject  # noqa: E402

from app.services.catia.service import CatiaService  # noqa: E402

OUT = Path(__file__).with_suffix(".txt")
L: list[str] = []


def log(s: str = "") -> None:
    L.append(str(s))


def safe(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return f"<err {type(exc).__name__}: {exc}>"


def glb_nodes(data: bytes):
    """读 GLB 的 JSON chunk，返回 (节点名列表, mesh 数)。"""
    if data[:4] != b"glTF":
        return None, 0
    off = 12
    while off + 8 <= len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        if ctype == 0x4E4F534A:  # JSON
            doc = json.loads(data[off : off + clen].decode("utf-8"))
            return [n.get("name") for n in doc.get("nodes", [])], len(doc.get("meshes", []))
        off += clen
    return None, 0


def main() -> None:
    svc = CatiaService()
    app = GetActiveObject("CATIA.Application")
    doc = app.ActiveDocument
    sel = doc.Selection
    count = safe(lambda: sel.Count2)
    log(f"document = {safe(lambda: doc.Name)}")
    log(f"Count2   = {count}")

    if not isinstance(count, int) or count < 1:
        log("")
        log(">> 当前没有选中项。请先在 CATIA 里多选，再重跑本脚本。")
        OUT.write_text("\n".join(L), encoding="utf-8")
        return

    idxs = list(range(1, count + 1))
    log("")
    log("=== 1) 逐项原始信息 ===")
    for i in idxs:
        item = sel.Item2(i)
        log(f"  item {i}: Type={safe(lambda it=item: it.Type)}"
            f"  leaf={safe(lambda it=item: it.LeafProduct.Name)}")
        log(f"           name={str(safe(lambda it=item: it.Value.Name))[:90]}")
        log(f"           branch={safe(lambda it=item: svc._branch_index(it))}"
            f"  pick={safe(lambda it=item: svc._pick_point(it))}")

    log("")
    log("=== 2) _branch_selection 汇总（逐项取，坐标应各不相同）===")
    log(f"  {safe(lambda: svc._branch_selection(sel, idxs))}")

    log("")
    log("=== 3) 真实 get_glb()（默认导出全部选中项）===")
    result = safe(lambda: svc.get_glb())
    if isinstance(result, str):
        log(f"  FAILED: {result}")
    else:
        data, name, parts, branches = result
        log(f"  filename = {name}")
        log(f"  branches = {branches}")
        log(f"  parts    = {json.dumps(parts, ensure_ascii=False)}")
        log(f"  size     = {len(data)} bytes")
        nodes, meshes = glb_nodes(data)
        log(f"  nodes    = {nodes}")
        log(f"  meshes   = {meshes}")
        if nodes is not None:
            ids = [p["id"] for p in parts]
            log(f"  parts[].id == nodes ? {sorted(ids) == sorted(n for n in nodes if n)}")
        out_glb = Path(__file__).with_suffix(".glb")
        out_glb.write_bytes(data)
        log(f"  saved -> {out_glb}")

    OUT.write_text("\n".join(L), encoding="utf-8")
    for line in L:
        print(line)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log(f"FATAL {type(exc).__name__}: {exc}")
        OUT.write_text("\n".join(L), encoding="utf-8")
        print("\n".join(L))
