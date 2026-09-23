import math
import re
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
from app.utils.step_to_gltf import ExportedNode, steps_to_gltf

# 线束分支在 CATIA 里的特征名，形如 EhiBundleSegmentRib.2（树里显示为 肋.2 / 分支.2），
# 其电气路径是 ElecCurve.2 / GSMCircle.2。数字即分支序号，与导出 STEP 后的实体顺序一致。
_BRANCH_NAME_PATTERN = re.compile(r"(?:EhiBundleSegmentRib|ElecCurve|GSMCircle)\.(\d+)")

# 分支**中心线**（电气路径）的内部特征名，形如 ElecCurve.2；规格树里显示为 柔性曲线.2。
# 量分支长度只能量它：用户点分支的**表皮**（肋实体表面）时 CATIAMeasurable.Length 会直接
# 报「方法 Length 失败」，点中心线才量得出曲线长度。
# 实测（D:\电缆数模案例）分支 1~4 的中心线长 = 175.0 / 245.4 / 250.4 / 279.1 mm，
# 与同一根分支实体包围盒最长边 171.3 / 232.0 / 193.2 / 257.6 mm 吻合，**单位就是 mm**。
# ⚠️ 别拿 GSMCircle.N（圆.N）当中心线：它是截面圆，Length 是周长（40.84 = 2π×6.5）。
_CENTERLINE_NAME_TEMPLATE = "ElecCurve.{branch}"


def _branches_of(nodes: list[ExportedNode]) -> list[int]:
    """从导出节点清单里取出**实际**导出的分支序号（按分支拆节点时）。"""
    return [node.branch for node in nodes if node.branch is not None]

class CatiaError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _components_to_matrix(components: Any) -> list[list[float]]:
    """把 Position.GetComponents 的 12 个分量转成 4x4 行主序变换矩阵。

    CATIA 文档：前 9 个分量**依次是 x 轴、y 轴、z 轴的分量**，最后 3 个是原点坐标。
    轴向量构成旋转矩阵的**列**（R·e_x = x轴），所以这里按列填充。

    ⚠️ 别改成按行填：那等于对旋转部分做转置，零件会绕轴**反向**倾斜
    （现象是"位置正确但零件歪了"）。曾用几何实测验证过——把 CATIA 里的拾取点
    逆变换回零件局部坐标，按列填时点到零件表面距离 0.015mm，按行填时 1.545mm。
    """
    return [
        [components[0], components[3], components[6], components[9]],
        [components[1], components[4], components[7], components[10]],
        [components[2], components[5], components[8], components[11]],
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

    # 获取glTF文件：导出当前选中项。
    # **默认（index <= 0）导出全部选中项**，可以跨零件——例如线束的一根分支 + 一个卡扣
    # 会一起合成到同一个 GLB；传 index >= 1 时只导出第 index 个选中项（兼容旧调用）。
    # 返回 (glb 字节, 文件名, 节点清单, 实际导出的分支序号)；节点清单里每一项都带唯一标识
    # （= GLB 节点名），线束选多根分支时每根分支各占一项（如 多分支1.1#1、多分支1.1#4），
    # 前端据此在数模上按实例名做高亮。
    def get_glb(
        self, index: int = 0
    ) -> tuple[bytes, str, list[dict[str, Any]], list[int]]:
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
        if index > count:
            raise CatiaError(
                StatusCode.VALIDATION_ERROR,
                f"选中项索引 {index} 需在 0..{count} 之间（0 = 全部）",
            )

        indices = range(1, count + 1) if index <= 0 else (index,)

        # 按叶子零件分组（保持选择顺序）。每个零件只吃自己的分支过滤，避免互相误裁：
        # 卡扣上的那次面点击不能拿去裁线束，反之亦然。
        # 这里只记索引、不长期持有 SelectedElement 对象——见 _pick_point 的说明。
        groups: dict[str, list[int]] = {}
        for i in indices:
            try:
                item = selection.Item2(i)
                leaf_name = str(item.LeafProduct.Name) or "selected"
            except Exception as exc:
                logger.info("selection item {} unusable: {}", i, exc)
                continue
            groups.setdefault(leaf_name, []).append(i)

        if not groups:
            raise CatiaError(StatusCode.VALIDATION_ERROR, "选中的对象无法导出")

        with tempfile.TemporaryDirectory() as tmp_dir:
            entries: list[tuple[str, list[list[float]] | None, str, list[int], list]] = []
            products: dict[str, Any] = {}
            for seq, (leaf_name, idxs) in enumerate(groups.items()):
                group_entries, group_products = self._items_to_entries(
                    tmp_dir, selection, idxs, seq
                )
                if not group_entries:
                    continue
                entries.extend(group_entries)
                products.update(group_products)
                logger.info(
                    "group {} -> {} entry(ies) {}",
                    leaf_name,
                    len(group_entries),
                    [entry[2] for entry in group_entries],
                )

            if not entries:
                raise CatiaError(
                    StatusCode.VALIDATION_ERROR, "选中的对象无法导出（未收集到任何零件）"
                )

            filename = next(iter(groups))
            glb_path = Path(tmp_dir) / f"{filename}.glb"
            nodes = steps_to_gltf(entries, str(glb_path))
            parts = self._nodes_to_parts(nodes, products)
            return glb_path.read_bytes(), filename, parts, _branches_of(nodes)

    @staticmethod
    def _branch_index(item: Any) -> int | None:
        """从 3D 拾取到的面/边引用名里解析分支序号。

        在 3D 视图里点到某个分支的表面时，选中项的引用名形如::

            Selection_RSur:(Face:(Brp:(EhiBundleSegmentRib.2;0:(Brp:(ElecCurve.2;1);...
                                                              Brp:(GSMCircle.2;...)))))

        其中的 2 就是分支序号，与导出 STEP 后第 2 个实体对应。
        纯树选中（不点几何）拿不到这个信息，此时返回 None。

        查询顺序：``Value.Name``（真实 3D 拾取时带的就是这个）→ ``Reference.Name`` →
        ``Reference.DisplayName``（用 CreateReferenceFromBRepName 还原出来的引用，
        名字会被写成 CATIAReference17 这种自动名，只有 DisplayName 还留着
        ``FSur:(Face:(Brp:(EhiBundleSegmentRib.2;...`` 这串面包屑）。
        """
        for getter in (
            lambda: item.Value.Name,
            lambda: item.Reference.Name,
            lambda: item.Reference.DisplayName,
        ):
            try:
                text = str(getter())
            except Exception:
                continue
            match = _BRANCH_NAME_PATTERN.search(text or "")
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _pick_point(item: Any) -> tuple[float, float, float] | None:
        """取 3D 视图内的拾取点（总成坐标）。非几何拾取时返回 None。

        ⚠️ 调用方必须传入「刚从 ``selection.Item2(i)`` 取出来的」对象：
        坐标读取依赖 CATIA 当前的拾取状态，如果先批量取出多个 SelectedElement
        再逐个读坐标，它们会**统一返回最后一项的坐标**（实测：选两根分支拿到两个
        完全相同的点），进而让多选退化只导出一根分支。
        """
        try:
            coords = SelectedElement(item).get_coordinates()
            point = (float(coords[0]), float(coords[1]), float(coords[2]))
        except Exception:
            return None
        # 非几何选择的兜底值是 (0, 0, 0)，不能当成有效拾取点
        if point == (0.0, 0.0, 0.0):
            return None
        try:
            if str(item.Type) == "Product":
                return None
        except Exception:
            pass
        return point

    def _branch_selection(
        self, selection: Any, indexes: list[int]
    ) -> tuple[list[int], list[tuple[float, float, float]]]:
        """收集**同一个叶子零件**下这些选中项里的分支序号与 3D 拾取点。

        支持用户一次选中多个断开的分支（多选），返回它们的并集。

        注意：这里必须**逐个** ``selection.Item2(i)`` 后立即读取，不能先批量取好
        item 列表再遍历（原因见 :meth:`_pick_point`）。
        """
        branches: list[int] = []
        points: list[tuple[float, float, float]] = []
        for i in indexes:
            try:
                item = selection.Item2(i)
            except Exception as exc:
                logger.info("selection item {} unusable: {}", i, exc)
                continue
            index = self._branch_index(item)
            if index is not None and index not in branches:
                branches.append(index)
            # 重新取一次再**立刻**读坐标，保证取到的是这一项的拾取点
            try:
                item = selection.Item2(i)
            except Exception:
                continue
            point = self._pick_point(item)
            if point is not None and point not in points:
                points.append(point)
        return branches, points

    # 把导出层的节点清单转成接口返回的零件清单：**每根分支一项**，
    # id/instanceName 就是该分支在 GLB 里的节点名（如 多分支1.1#4）。
    def _nodes_to_parts(
        self, nodes: list[ExportedNode], products: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": node.name,
                "instanceName": node.name,
                "branch": node.branch,
                "partNumber": self._part_number(products.get(node.instance_name)),
            }
            for node in nodes
        ]

    # 把一个叶子零件（或子装配）的选中项转成待导出的 STEP 条目。
    # 首选递归收集其子树下所有引用 CATPart 的叶子实例——这样「卡扣装配」这类自身无实体的
    # 子装配也能正确导出；退化时才直接导出该零件自己的引用文档。
    # 只接收选中项的**索引**，需要时现取现用（避免持有过期的 SelectedElement）。
    # 返回 (entries, products)；entries 元素 = (step_path, matrix|None, 实例名, 分支号, 拾取点)
    def _items_to_entries(
        self, tmp_dir: str | Path, selection: Any, indexes: list[int], seq: int
    ) -> tuple[
        list[tuple[str, list[list[float]] | None, str, list[int], list]], dict[str, Any]
    ]:
        try:
            first = selection.Item2(indexes[0])
        except Exception as exc:
            logger.info("selection item {} unusable: {}", indexes[0], exc)
            return [], {}
        leaf_name = str(first.LeafProduct.Name) or "selected"
        branches, pick_points = self._branch_selection(selection, indexes)
        # 只有识别到电气分支特征时才按分支裁剪：普通零件（卡扣、支架…）上的一次面点击
        # 不该把这个零件裁成「离点击点最近的那个实体」。
        if pick_points and not branches:
            logger.info("{}: 有几何拾取但未识别到分支特征，按整零件导出", leaf_name)
            pick_points = []
        if branches:
            logger.info(
                "{}: branch filter branches={} pick_points={}",
                leaf_name,
                branches,
                pick_points,
            )

        leaf_product: Any = None
        instances: list[tuple[list[list[float]], Any]] = []
        try:
            leaf_product = Product(first.LeafProduct)
            instances = self._collect_part_transforms(leaf_product, _identity_matrix())
        except Exception as exc:
            logger.info("{} failed to collect part instances: {}", leaf_name, exc)

        if not instances:
            # 回退：独立零件实例（有独立 .CATPart 引用文档）直接导出该引用文档，
            # 只会包含这一个零件
            try:
                ref_doc = first.Value.ReferenceProduct.Parent
            except Exception:
                ref_doc = None
            if ref_doc is None or not self._is_part_document(ref_doc):
                logger.warning("{} 无法导出，已跳过", leaf_name)
                return [], {}
            step_path = Path(tmp_dir) / f"part_{seq}.stp"
            ref_doc.ExportData(str(step_path), "stp")
            logger.info(
                "{}: fallback export ref doc Name={}",
                leaf_name,
                getattr(ref_doc, "Name", None),
            )
            return [(str(step_path), None, leaf_name, branches, pick_points)], {
                leaf_name: leaf_product
            }

        entries: list[tuple[str, list[list[float]] | None, str, list[int], list]] = []
        products: dict[str, Any] = {}
        for k, (matrix, inst) in enumerate(instances):
            ref_doc = inst.com_object.ReferenceProduct.Parent
            step_path = Path(tmp_dir) / f"part_{seq}_{k}.stp"
            ref_doc.ExportData(str(step_path), "stp")
            instance_name = self._instance_name(inst, k)
            # 分支过滤只作用于被选中的那个实例，避免误裁同一子树里的其它实例
            is_target = instance_name == leaf_name
            entries.append(
                (
                    str(step_path),
                    matrix,
                    instance_name,
                    branches if is_target else [],
                    pick_points if is_target else [],
                )
            )
            products[instance_name] = inst
        return entries, products

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

        # 同名实例只保留一条；长度按「分支中心线」汇总，非分支零件仍按选中对象直接量。
        seen: set[str] = set()
        order: list[str] = []
        branch_ids: dict[str, list[int]] = {}   # 叶子零件名 -> 选中项里识别到的分支号（去重、保序）
        direct: dict[str, list[float]] = {}     # 叶子零件名 -> 直接量出来的长度（非分支 / 兜底）
        part_ctx: dict[str, tuple[Any, Any] | None] = {}  # 叶子零件名 -> (part, spa)

        for i in range(1, selection.Count2 + 1):
            try:
                # 逐个取、立刻用：Item2 的取值顺序会影响后面的拾取状态（见 _pick_point）
                item = selection.Item2(i)
                selected = SelectedElement(item)
                name = selected.leaf_product.name
            except Exception as exc:
                logger.warning("failed to read selection {}: {}", i, exc)
                continue
            if not name or name == "InvalidLeafProduct":
                continue
            if name not in seen:
                seen.add(name)
                order.append(name)

            # 点分支（表皮或中心线）时能解析出分支号，长度一律改用该分支的中心线来量，
            # 这样用户点表皮、点中心线拿到的长度一致（原因见 _CENTERLINE_NAME_TEMPLATE）。
            branch = self._branch_index(item)
            if branch is not None:
                branch_ids.setdefault(name, [])
                if branch not in branch_ids[name]:
                    branch_ids[name].append(branch)
                if name not in part_ctx:
                    part_ctx[name] = self._part_measure_context(item, name)
                continue

            # 非分支（卡扣、接头…）：沿用原行为，直接量选中的那个对象
            try:
                length = float(spa.get_measurable(selected.reference).length)
                direct.setdefault(name, []).append(length)
            except Exception as exc:
                logger.warning("failed to measure length for {}: {}", name, exc)

        results: list[dict[str, Any]] = []
        for name in order:
            length = self._total_length(name, branch_ids, direct, part_ctx)
            results.append({
                "name": name,
                "length": round(length, 3) if length is not None else None,
            })
        # 总成实例总长度 = 所有选中零件长度之和（测不出的项不计入）
        total_length = sum(r["length"] for r in results if r["length"] is not None)
        return {
            "assemblyName": assembly_name,
            "totalLength": round(total_length, 3),
            "parts": results,
        }

    # 汇总一个叶子零件的长度：
    # 优先用「识别到的分支号 -> 该分支中心线长度」之和；分支量不到时退回直接量的结果。
    def _total_length(
        self,
        name: str,
        branch_ids: dict[str, list[int]],
        direct: dict[str, list[float]],
        part_ctx: dict[str, tuple[Any, Any] | None],
    ) -> float | None:
        branches = branch_ids.get(name) or []
        context = part_ctx.get(name)
        if branches and context:
            part, part_spa = context
            measured = [
                length
                for length in (
                    self._measure_centerline(part, part_spa, branch)
                    for branch in branches
                )
                if length is not None
            ]
            if measured:
                return sum(measured)
            logger.info("{}: 分支中心线量不到长度，退回直接测量", name)
        values = direct.get(name) or []
        return sum(values) if values else None

    # 解析叶子零件所属的 CATPart 文档，返回 (part, spa) 供量中心线用；取不到返回 None。
    # 必须传入刚取出来的选中项对象（同 _pick_point 的注意事项）。
    def _part_measure_context(self, item: Any, name: str) -> tuple[Any, Any] | None:
        try:
            ref_doc = item.LeafProduct.ReferenceProduct.Parent
        except Exception as exc:
            logger.warning("{}: 取引用零件文档失败: {}", name, exc)
            return None
        if not self._is_part_document(ref_doc):
            return None
        try:
            return PartDocument(ref_doc).part, SPAWorkbench(ref_doc)
        except Exception as exc:
            logger.warning("{}: 打开零件文档失败: {}", name, exc)
            return None

    # 量某个分支的中心线（电气路径）长度。找不到或量不出返回 None。
    @staticmethod
    def _measure_centerline(part: Any, part_spa: Any, branch: int) -> float | None:
        try:
            curve = part.find_object_by_name(
                _CENTERLINE_NAME_TEMPLATE.format(branch=branch)
            )
        except Exception:
            logger.warning("分支 {} 找不到中心线，长度返回 null", branch)
            return None
        try:
            reference = part.create_reference_from_object(curve)
            return float(part_spa.get_measurable(reference).length)
        except Exception as exc:
            logger.warning("分支 {} 中心线长度测量失败: {}", branch, exc)
            return None

    
