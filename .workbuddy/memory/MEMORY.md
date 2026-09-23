# 项目长期记忆 — CatiaProject

## 运行环境
- 跑后端 / 导出逻辑必须用 `F:\office\conda\envs\catia310\python.exe`：
  OCC(pythonocc)、pycatia、win32com 都在这个 conda 环境里。项目根的 `.venv` **没有** OCC。
- 入口：`app/main.py`，`uvicorn app.main:app`；接口前缀 `settings.api_prefix + /v1`。

## glTF/GLB 导出约定（重要）
- **`/getglb` 返回 JSON**（`ApiResponse[GlbPayload]`），data = `{filename, parts:[{id,instanceName,partNumber}], glb: base64}`。
  前端先拿 `parts[].id` 拿到唯一标识，再 base64 解码 `data.glb` 加载模型。**不再是二进制流**。
- GLB 里**每个零件必须是独立的 glTF 节点**，`node.name` = CATIA 零件实例名
  （`Product.Name`，形如 `Bracket.1`）。`parts[].id` 与该节点名一致，前端据此在数模上按实例名高亮。
- 该标识与 `/getselected` 返回的 `parts[].name` **同源**（都来自 `Product.Name`），必须保持一致。
- **不要**把多个零件合并进一个 `TopoDS_Compound` 再整体 `AddShape`——那样 GLB 只有一个节点、
  没有名字，前端无法定位单个零件。

## OCC (pythonocc) 踩坑记录
- `TDataStd_Name.Set(label, name)`：`name` **必须传 Python `str`**。传 `TCollection_ExtendedString`
  会报 `Wrong number or type of arguments for overloaded function 'TDataStd_Name_Set'`
  （SWIG typemap 只接受 Python str）。
- 让节点名真正写进 glTF，需要 `writer.SetNodeNameFormat(RWMesh_NameFormat.RWMesh_NameFormat_InstanceOrProduct)`。
- `XCAFDoc_ShapeTool.AddShape(shape, False)` 每调一次产生一个顶层节点，这正是"每零件一节点"的做法。
- `BRepBuilderAPI_Transform` 会把变换烘进几何，节点上不会有 `translation`，几何位置本身已正确。

## 命名/接口风格
- 接口统一包 `ApiResponse{code,message,data}`；业务异常抛 `CatiaError(code, msg)`。
- 接口尽量套 `ApiResponse` 并声明 `response_model`；只有确实要直接吐文件流的接口才返回 `Response`。
