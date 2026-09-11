import math
import tempfile
import threading
from pathlib import Path
from typing import Any
from loguru import logger
import pythoncom
from app.core.status_code import StatusCode
from win32com.client import GetActiveObject
from pycatia.mec_mod_interfaces.part_document import PartDocument
from pycatia.space_analyses_interfaces.spa_workbench import SPAWorkbench
from pycatia.product_structure_interfaces.product import Product
from pycatia.product_structure_interfaces.product_document import ProductDocument
from app.utils.step_to_gltf import step_to_gltf

class CatiaError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _components_to_matrix(components: Any) -> list[list[float]]:
    """把 Position.GetComponents 的 12 个分量转成 4x4 行主序变换矩阵。"""
    return [
        [components[0], components[1], components[2], components[9]],
        [components[3], components[4], components[5], components[10]],
        [components[6], components[7], components[8], components[11]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _identity_matrix() -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
        for i in range(4)
    ]


def _fmt_xyz(x: float, y: float, z: float) -> str:
    return f"{x:.6f}, {y:.6f}, {z:.6f}"


def _matrix_to_quaternion(m: list[list[float]]) -> tuple[float, float, float, float]:
    """把 3x3 旋转矩阵转为四元数 (x, y, z, w)。"""
    m00, m01, m02 = m[0][0], m[0][1], m[0][2]
    m10, m11, m12 = m[1][0], m[1][1], m[1][2]
    m20, m21, m22 = m[2][0], m[2][1], m[2][2]

    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    return (x, y, z, w)


def _fmt_rotation(m: list[list[float]]) -> str:
    x, y, z, w = _matrix_to_quaternion(m)
    return f"{x:.6f}, {y:.6f}, {z:.6f}, {w:.6f}"


class CatiaService:

    _PART_SUFFIX = ".catpart"

    def __init__(self) -> None:
        # 每个线程缓存各自的 COM 代理，避免跨线程共享 STA 对象
        self._local = threading.local()

    def _connect(self) -> Any:
        # COM 调用必须在使用的线程里先 CoInitialize；FastAPI 用线程池执行同步接口，
        # 不同请求可能落在不同线程，所以每次都要保证当前线程已初始化。
        pythoncom.CoInitialize()

        app = getattr(self._local, "app", None)
        if app is not None:
            return app

        try:
            app = GetActiveObject("CATIA.Application")
        except Exception as exc:
            raise CatiaError(
                StatusCode.CATIA_UNAVAILABLE, f"CATIA is not running: {exc}"
            ) from exc
        self._local.app = app
        logger.info("Connected to CATIA")
        return app

    #查询所有零件
    def list_parts(self) -> list[dict[str, Any]]:
        app = self._connect()
        # CATIA COM API 对象模型
        documents = app.Documents

        parts: list[dict[str, Any]] = []

        # catia com 集合索引从 1 开始
        for i in range(1, documents.Count + 1):
            doc = documents.Item(i)
            name: str = doc.Name
            if not name.lower().endswith(self._PART_SUFFIX):
                continue

            item: dict[str, Any] = {
                "name": name,
                "full_name": doc.FullName,
                "path": doc.Path,
            }
            parts.append(item)
        return parts

    #查询零件信息
    def select_part(self, part_name: str) -> list[dict[str, Any]]:

        app = self._connect()
        documents = app.Documents
        parts: list[dict[str, Any]] = []
        item: dict[str,Any] = {}
        for i in range(1, documents.Count + 1):
            doc = documents.Item(i)
            name: str = doc.Name
            if name.lower() == part_name.lower():
                item["name"] = name
                item["full_name"] = doc.FullName
                item["path"] = doc.Path
                for field in ("Saved", "ReadOnly"):
                    try:
                        item[field.lower()] = bool(getattr(doc, field))
                    except Exception:
                        item[field.lower()] = False
                #零件号与密度等
                try:
                    part_doc = PartDocument(doc)
                    part = part_doc.part
                    item["part_number"] = part_doc.product.part_number
                    item["density"] = part.density

                    spa = SPAWorkbench(doc)
                    reference = part.create_reference_from_object(part.main_body)
                    volume = spa.get_measurable(reference).volume
                    item["volume"] = volume
                    if item["density"] and volume:
                        item["mass"] = item["density"] * volume
                except Exception as exc:
                    logger.warning("failed to read part properties for {}: {}", name, exc)
                parts.append(item)
                return parts
        return []

    # 获取glTF文件
    def get_glb(self, fullName: str) -> bytes:
        app = self._connect()
        documents = app.Documents
        # 按 fullName 定位已打开的文档
        doc = None
        for i in range(1, documents.Count + 1):
            candidate = documents.Item(i)
            if candidate.FullName == fullName:
                doc = candidate
                break
        if doc is None:
            raise CatiaError(
                StatusCode.VALIDATION_ERROR, f"Document not open: {fullName}"
            )

        part_doc = PartDocument(doc)

        # 导出 STEP 转成 GLB，都放在临时目录，返回后自动清理
        with tempfile.TemporaryDirectory() as tmp_dir:
            stem = Path(fullName).stem
            step_path = Path(tmp_dir) / f"{stem}.stp"
            glb_path = Path(tmp_dir) / f"{stem}.glb"
            part_doc.export_data(step_path, "stp", overwrite=True)
            step_to_gltf(str(step_path), str(glb_path))
            return glb_path.read_bytes()

    # 获取零件位置（递归装配体结构树，返回每个节点的局部/全局位置与旋转）
    def list_position(self, fullName: str) -> list[dict[str, Any]]:
        app = self._connect()
        documents = app.Documents

        doc = None
        for i in range(1, documents.Count + 1):
            candidate = documents.Item(i)
            if candidate.FullName == fullName:
                doc = candidate
                break
        if doc is None:
            raise CatiaError(
                StatusCode.VALIDATION_ERROR, f"Document not open: {fullName}"
            )

        root = ProductDocument(doc).product
        results: list[dict[str, Any]] = []

        def walk(product: Product, parent_matrix: list[list[float]], parent_name: str) -> None:
            components = product.position.get_components()
            global_matrix = _matmul(parent_matrix, _components_to_matrix(components))

            results.append({
                "name": product.name,
                "localPosition": _fmt_xyz(components[9], components[10], components[11]),
                "globalPosition": _fmt_xyz(global_matrix[0][3], global_matrix[1][3], global_matrix[2][3]),
                "globalRotation": _fmt_rotation(global_matrix),
                "parentName": parent_name,
            })

            for child in product.get_children():
                walk(child, global_matrix, product.name)

        # 从根下第一层开始，根（用户传入的装配体本身）不作为条目返回
        for child in root.get_children():
            walk(child, _identity_matrix(), root.name)

        return results
