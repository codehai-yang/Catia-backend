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