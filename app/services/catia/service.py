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
from app.utils.step_to_gltf import step_to_gltf

class CatiaError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


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
