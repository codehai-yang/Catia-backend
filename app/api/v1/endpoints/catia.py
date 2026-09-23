import base64

from fastapi import APIRouter
from fastapi.params import Body
from fastapi.responses import Response
from pycatia.mec_mod_interfaces import body
from app.schemas.catia import (
    GlbNode,
    GlbPayload,
    PartItem,
    SelectedParts,
)
from app.schemas.common import ApiResponse
from app.services.catia.service import CatiaService

router = APIRouter(tags=["catia"])

catia_service = CatiaService()

# 查询所有零件
@router.post("/listparts", response_model=ApiResponse[list[PartItem]], summary="List open CATIA parts")
def list_parts() -> ApiResponse[list[PartItem]]:
    return ApiResponse(data=catia_service.list_parts())

# 查询单个零件
@router.post("/selectpart", response_model=ApiResponse[list[PartItem]])
def select_part(partName: str = Body(...)) -> ApiResponse[list[PartItem]]:
    return ApiResponse(data=catia_service.select_part(partName))

# 获取glTF/GLB文件（当前选中项中的第 index 个，默认第 1 个）
# 返回 JSON：零件清单（含唯一标识 id）+ 模型本体（base64）。前端用 parts[].id
# 与 GLB 里的节点名(node.name) 对齐，即可在数模上按实例名高亮。
@router.post(
    "/getglb",
    response_model=ApiResponse[GlbPayload],
    summary="Export GLB of the selected item, with part instance names",
)
def get_gltf(index: int = Body(1)) -> ApiResponse[GlbPayload]:
    data, name, parts, branches = catia_service.get_glb(index=index)
    return ApiResponse(
        data=GlbPayload(
            filename=f"{name}.glb",
            parts=[GlbNode(**part) for part in parts],
            branches=branches,
            glb=base64.b64encode(data).decode("ascii"),
        )
    )

#获取零件位置
@router.post("/getposition")
def get_position(fullName: str = Body(...)) -> Response:
    return ApiResponse(data=catia_service.list_position(fullName))

# 获取当前选中的零件实例名称及总长度
@router.post("/getselected", response_model=ApiResponse[SelectedParts])
def get_selected() -> ApiResponse[SelectedParts]:
    return ApiResponse(data=catia_service.get_selected_instances())

