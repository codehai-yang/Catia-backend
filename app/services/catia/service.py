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
from pycatia.in_interfaces.selected_element import SelectedElement
from app.utils.step_to_gltf import step_to_gltf, steps_to_gltf

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

    # 获取glTF文件：导出当前选中项中的第 index 个（默认第 1 个）的数模，而非其父级文档。
    # 返回 (glb 字节, 文件名, 零件清单)；零件清单里每个零件都带唯一标识（= GLB 节点名），
    # 前端据此在数模上按实例名做高亮。
    def get_glb(self, index: int = 1) -> tuple[bytes, str, list[dict[str, str]]]:
        app = self._connect()
        document = app.ActiveDocument
        if document is None:
            raise CatiaError(StatusCode.VALIDATION_ERROR, "No active CATIA document")

        selection = document.Selection
        count = selection.Count2
        if count < 1:
            raise CatiaError(
                StatusCode.VALIDATION_ERROR, "请先在 CATIA 中选中至少一个零件/分支"
            )
        if index < 1 or index > count:
            raise CatiaError(
                StatusCode.VALIDATION_ERROR,
                f"选中项索引 {index} 需在 1..{count} 之间",
            )

        selected = selection.Item2(index)
        instance_name: str = selected.LeafProduct.Name or "selected"
        val = selected.Value

        # 首选：递归收集选中 Product 子树下所有引用 CATPart 的叶子实例，逐个导出其
        # 引用文档的 STEP，再按各自实例位姿合成一个 GLB。这样既不会带上整个线束文档，
        # 也能正确处理「卡扣装配」这类子装配（其自身无实体，实体都在叶子 CATPart 里）。
        leaf_product: Any = None
        try:
            leaf_product = Product(selected.LeafProduct)
            instances = self._collect_part_transforms(leaf_product, _identity_matrix())
        except Exception as exc:
            logger.info("index={} failed to collect part instances: {}", index, exc)
            instances = []
        if instances:
            logger.info("index={} exporting {} leaf part instance(s)", index, len(instances))
            return self._export_instances_to_glb(instances, instance_name)

        # 回退：独立零件实例（有独立 .CATPart 引用文档）直接导出该引用文档，只会包含这一个零件
        try:
            ref_doc = val.ReferenceProduct.Parent
        except Exception:
            ref_doc = None
        if ref_doc is not None and self._is_part_document(ref_doc):
            logger.info(
                "index={} fallback export ref doc Name={} FullName={}",
                index,
                getattr(ref_doc, "Name", None),
                getattr(ref_doc, "FullName", None),
            )
            return self._export_reference_to_glb(ref_doc, instance_name, leaf_product)

        raise CatiaError(StatusCode.VALIDATION_ERROR, f"无法导出选中对象 {instance_name}")

    # 独立零件实例：直接导出其引用文档
    def _export_reference_to_glb(
        self, ref_doc: Any, name: str, product: Any = None
    ) -> tuple[bytes, str, list[dict[str, str]]]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            step_path = Path(tmp_dir) / f"{name}.stp"
            glb_path = Path(tmp_dir) / f"{name}.glb"
            ref_doc.ExportData(str(step_path), "stp")
            # name 即零件实例名，写进 glTF 节点名供前端高亮
            step_to_gltf(str(step_path), str(glb_path), name=name)
            parts = [
                {
                    "id": name,
                    "instanceName": name,
                    "partNumber": self._part_number(product),
                }
            ]
            return glb_path.read_bytes(), name, parts

    @staticmethod
    def _instance_name(product: Any, index: int) -> str:
        """取零件实例名（如 Bracket.1）。取不到时退回 part_<index> 兜底。"""
        for getter in (lambda: product.name, lambda: product.com_object.Name):
            try:
                value = str(getter()).strip()
            except Exception:
                value = ""
            if value:
                return value
        return f"part_{index}"

    @staticmethod
    def _part_number(product: Any) -> str:
        """取引用零件号（PartNumber）。取不到返回空串。"""
        if product is None:
            return ""
        try:
            return str(product.reference_product.part_number or "").strip()
        except Exception:
            return ""

    # 递归收集 Product 子树下所有引用 CATPart 的叶子实例及其全局位姿矩阵
    def _collect_part_transforms(
        self, product: Any, parent_matrix: list[list[float]]
    ) -> list[tuple[list[list[float]], Any]]:
        try:
            own = _components_to_matrix(product.position.get_components())
        except Exception:
            own = _identity_matrix()
        global_matrix = _matmul(parent_matrix, own)
        try:
            children = product.get_children()
        except Exception:
            children = []
        if not children:
            return [(global_matrix, product)] if self._is_part_instance(product) else []
        result: list[tuple[list[list[float]], Any]] = []
        for child in children:
            result.extend(self._collect_part_transforms(child, global_matrix))
        return result

    @staticmethod
    def _is_part_instance(product: Any) -> bool:
        # 实例引用的是 CATPart 文档（叶子零件），而非 CATProduct 子装配
        try:
            return bool(product.is_catpart())
        except Exception:
            return False

    @staticmethod
    def _is_part_document(doc: Any) -> bool:
        try:
            return str(doc.Name).lower().endswith(".catpart")
        except Exception:
            return False

    # 逐个导出叶子零件引用文档的 STEP，按各自实例位姿合成为一个 GLB。
    # 每个零件在 GLB 里是独立节点，节点名 = CATIA 实例名，前端据此高亮。
    def _export_instances_to_glb(
        self, instances: list[tuple[list[list[float]], Any]], name: str
    ) -> tuple[bytes, str, list[dict[str, str]]]:
        with tempfile.TemporaryDirectory() as tmp_dir:
            entries: list[tuple[str, list[list[float]], str]] = []
            parts: list[dict[str, str]] = []
            for i, (matrix, inst) in enumerate(instances):
                ref_doc = inst.com_object.ReferenceProduct.Parent
                step_path = Path(tmp_dir) / f"part_{i}.stp"
                ref_doc.ExportData(str(step_path), "stp")
                instance_name = self._instance_name(inst, i)
                entries.append((str(step_path), matrix, instance_name))
                parts.append(
                    {
                        "id": instance_name,
                        "instanceName": instance_name,
                        "partNumber": self._part_number(inst),
                    }
                )
            glb_path = Path(tmp_dir) / f"{name}.glb"
            steps_to_gltf(entries, str(glb_path))
            return glb_path.read_bytes(), name, parts

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

    # 获取当前在 CATIA 中选中的零件实例名称及其总长度
    def get_selected_instances(self) -> dict[str, Any]:
        app = self._connect()
        document = app.ActiveDocument
        if document is None:
            raise CatiaError(StatusCode.VALIDATION_ERROR, "No active CATIA document")

        selection = document.Selection
        spa = SPAWorkbench(document)

        # 根总成实例名称（活动文档为装配体时取根 Product 名，否则回退到文档名）
        try:
            assembly_name = ProductDocument(document).product.name
        except Exception:
            assembly_name = document.Name

        # 同名实例只保留一条，长度累加得到总长度
        lengths: dict[str, float | None] = {}
        order: list[str] = []
        for i in range(1, selection.Count2 + 1):
            try:
                selected = SelectedElement(selection.Item2(i))
                name = selected.leaf_product.name
            except Exception as exc:
                logger.warning("failed to read selection {}: {}", i, exc)
                continue
            if not name or name == "InvalidLeafProduct":
                continue
            if name not in lengths:
                lengths[name] = None
                order.append(name)
            try:
                length = float(spa.get_measurable(selected.reference).length)
                lengths[name] = (lengths[name] or 0.0) + length
            except Exception as exc:
                logger.warning("failed to measure length for {}: {}", name, exc)

        results: list[dict[str, Any]] = []
        for name in order:
            length = lengths[name]
            results.append({
                "name": name,
                "length": round(length, 3) if length is not None else None,
            })
        # 总成实例总长度 = 所有选中零件长度之和（测不出的项不计入）
        total_length = sum(l for l in lengths.values() if l is not None)
        return {
            "assemblyName": assembly_name,
            "totalLength": round(total_length, 3),
            "parts": results,
        }

    
