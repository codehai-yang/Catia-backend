"""验证分支识别与 /getglb 新契约。

用真实采样到的引用名验证 _branch_index；用假的 selection 验证多选并集；
再检查 FastAPI 的 openapi schema 与端点返回结构。不依赖 CATIA / httpx。
"""
import base64
import py_compile
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT = Path(__file__).resolve().parent / "_diag14.txt"
L: list[str] = []


def log(*a):
    L.append(" ".join(str(x) for x in a))


FILES = [
    r"app\utils\step_to_gltf.py",
    r"app\services\catia\service.py",
    r"app\api\v1\endpoints\catia.py",
    r"app\schemas\catia.py",
]
for f in FILES:
    py_compile.compile(f, doraise=True)
log(f"COMPILE OK: {len(FILES)} files")

from app.services.catia.service import CatiaService  # noqa: E402

REAL_RIB2 = (
    "Selection_RSur:(Face:(Brp:(EhiBundleSegmentRib.2;0:(Brp:(ElecCurve.2;1);"
    "Brp:(GSMCircle.2;(Brp:(GSMPlane.13)))));None:();Cf11:());"
    "EhiBundleSegmentRib.2_ResultOUT;Z0;G3563)"
)
REAL_RIB1 = (
    "Selection_RSur:(Face:(Brp:(EhiBundleSegmentRib.1;0:(Brp:(ElecCurve.1;2);"
    "Brp:(GSMCircle.1;(Brp:(GSMPlane.5)))));None:();Cf11:());"
    "EhiBundleSegmentRib.1_ResultOUT;Z0;G3563)"
)
PRODUCT_REF = "cable example/Harness ASSY-project cable example/几何束1.1/多分支1.1/"


class FakeRef:
    def __init__(self, name):
        self.Name = name


class FakeValue:
    def __init__(self, name):
        self.Name = name


class FakeLeaf:
    def __init__(self, name):
        self.Name = name


class FakeItem:
    def __init__(self, value_name, ref_name, leaf_name, typ="Face", coords=None):
        self.Value = FakeValue(value_name)
        self.Reference = FakeRef(ref_name)
        self.LeafProduct = FakeLeaf(leaf_name)
        self.Type = typ
        self._coords = coords

    def get_coordinates(self):
        if self._coords is None:
            raise RuntimeError("no coordinates")
        return self._coords


class FakeSelection:
    def __init__(self, items):
        self._items = items
        self.Count2 = len(items)

    def Item2(self, i):
        return self._items[i - 1]


log("")
log("=== 1) 分支序号解析（真实拾取到的引用名）===")
for tag, name in (("Rib.2", REAL_RIB2), ("Rib.1", REAL_RIB1), ("Product", PRODUCT_REF)):
    item = FakeItem(name, name, "多分支1.1")
    idx = CatiaService._branch_index(item)
    log(f"  {tag:8s} -> branch_index = {idx}")

log("")
log("=== 2) 多选（两个断开的分支）并集 ===")
sel = FakeSelection(
    [
        FakeItem(REAL_RIB2, REAL_RIB2, "多分支1.1"),
        FakeItem(REAL_RIB1, REAL_RIB1, "多分支1.1"),
        FakeItem(PRODUCT_REF, PRODUCT_REF, "多分支1.1", typ="Product"),
        FakeItem(REAL_RIB2, REAL_RIB2, "别的零件.1"),
    ]
)
svc = CatiaService()
branches, points = svc._branch_selection(sel, sel.Count2, "多分支1.1")
log(f"  branches    = {branches}   (期望 [2, 1]，且不混入别的零件的分支)")
log(f"  pick_points = {points}   (期望空：假对象取不到坐标)")

log("")
log("=== 3) 单选一个分支 ===")
sel1 = FakeSelection([FakeItem(REAL_RIB2, REAL_RIB2, "多分支1.1")])
b1, _ = svc._branch_selection(sel1, 1, "多分支1.1")
log(f"  branches = {b1}   (期望 [2])")

log("")
log("=== 4) 树里选中（拿不到分支信息）===")
sel_tree = FakeSelection([FakeItem(PRODUCT_REF, PRODUCT_REF, "多分支1.1", typ="Product")])
bt, _ = svc._branch_selection(sel_tree, 1, "多分支1.1")
log(f"  branches = {bt}   (期望 [] -> 回退成导出整个零件)")

# ---------- 5) 端点契约 ----------
log("")
log("=== 5) /getglb 端点契约 ===")
from app.api.v1.endpoints import catia as ep  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

spec = fastapi_app.openapi()
route = spec["paths"]["/api/v1/catia/getglb"]["post"]
log(f"  200 schema = {route['responses']['200']['content']['application/json']['schema']}")
payload_schema = spec["components"]["schemas"]["GlbPayload"]["properties"]
log(f"  GlbPayload keys = {list(payload_schema.keys())}")
log(f"  branches 字段已声明 = {'branches' in payload_schema}")

FAKE_GLB = b"\x00\x01FAKE-BRANCH-GLB"
ep.catia_service.get_glb = lambda index=1: (
    FAKE_GLB,
    "多分支1.1",
    [{"id": "多分支1.1", "instanceName": "多分支1.1", "partNumber": "多分支1"}],
    [2, 1],
)


def dump(resp):
    return resp.model_dump() if hasattr(resp, "model_dump") else resp


body = dump(ep.get_gltf(index=1))
log(f"  top keys  = {list(body.keys())}")
log(f"  data keys = {list(body['data'].keys())}")
log(f"  parts     = {body['data']['parts']}")
log(f"  branches  = {body['data']['branches']}")
decoded = base64.b64decode(body["data"]["glb"])
log(f"  glb bytes = {decoded!r}")
log(f"  roundtrip ok = {decoded == FAKE_GLB}")

log("")
log("=== 6) 未裁剪场景（branches 为空）===")
ep.catia_service.get_glb = lambda index=1: (
    FAKE_GLB,
    "Clip.1",
    [{"id": "Clip.1", "instanceName": "Clip.1", "partNumber": "P-9"}],
    [],
)
b2 = dump(ep.get_gltf(index=1))
log(f"  filename = {b2['data']['filename']}")
log(f"  parts    = {b2['data']['parts']}")
log(f"  branches = {b2['data']['branches']}   (空 = 导出整个零件)")

OUT.write_text("\n".join(L), encoding="utf-8")
print("written", OUT)
