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

# 选中的零件实例
class SelectedPart(BaseModel):
    name: str
    length: float | None = None  # 实例总长度（CATIA 文档单位，通常 mm）

# 选中结果：总成实例名称 + 选中的零件列表 + 总长度
class SelectedParts(BaseModel):
    assemblyName: str
    totalLength: float = 0.0  # 总成实例总长度 = 选中零件长度之和
    parts: list[SelectedPart] = []


# GLB 里的一个节点：要么是整个零件，要么是线束零件里的某一根分支
class GlbNode(BaseModel):
    id: str                     # 唯一标识 = GLB 里的节点名(node.name)，前端据此在数模上高亮
    instanceName: str           # 该节点所属实例名，与 id 一致；分支时形如 多分支1.1#4
    branch: int | None = None   # 分支序号（1 起）；None 表示这个节点是整个零件
    partNumber: str = ""        # 引用零件号，取不到时为空

# /getglb 返回体：零件清单 + 模型本体（base64）
class GlbPayload(BaseModel):
    filename: str
    # 每根分支一项；未按分支裁剪时就是零件本身一项
    parts: list[GlbNode] = []
    # 本次实际导出了哪些分支（1 起的分支序号），与 parts 里 branch 非空的项一一对应。
    # 线束这类「多个分支装在同一个 CATPart 里」的零件，后端会按分支裁剪；
    # 为空表示导出的是整个零件。
    branches: list[int] = []
    glb: str                # GLB 文件内容的 base64 编码，前端解码后加载