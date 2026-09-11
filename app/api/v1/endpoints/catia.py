from pathlib import Path

from fastapi import APIRouter
from fastapi.params import Body
from fastapi.responses import Response
from pycatia.mec_mod_interfaces import body
from app.schemas.catia import PartItem
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

#获取glTF/GLB文件
@router.post("/getglb")
def get_gltf(fullName: str = Body(...)) -> Response:
    data = catia_service.get_glb(fullName)
    return Response(
        content=data,
        media_type="model/gltf-binary",
        headers={"Content-Disposition": f'attachment; filename="{Path(fullName).stem}.glb"'},
    )

#获取零件位置
@router.post("/getposition")
def get_position(fullName: str = Body(...)) -> Response:
    return ApiResponse(data=catia_service.list_position(fullName))