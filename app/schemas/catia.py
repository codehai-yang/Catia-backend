from pydantic import BaseModel

class PartItem(BaseModel):
    name: str
    full_name: str
    path: str = ""
    part_number: str = ""
    saved: bool = False
    read_only: bool = False
    density: float | None = None  # 密度 kg/m³
    volume: float | None = None   # 体积 m³
    mass: float | None = None     # 质量 kg

# 零件位置
class PartPosition(BaseModel):
    name: str
    localPosition: str      #局部坐标，零件相对于它直接父级的位置和姿态
    globalPosition: str     #零件相对于整个装配体的位置
    globalRotation: str     # 零件相对于总成的旋转
    parentName: str = ""    #父级零件名称