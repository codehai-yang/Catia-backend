# 项目长期记忆 — CatiaProject

## 运行环境
- 跑后端 / 导出逻辑必须用 `F:\office\conda\envs\catia310\python.exe`：
  OCC(pythonocc)、pycatia、win32com 都在这个 conda 环境里。项目根的 `.venv` **没有** OCC。
- 入口：`app/main.py`，`uvicorn app.main:app`；接口前缀 `settings.api_prefix + /v1`。

## glTF/GLB 导出约定（重要）
- **`/getglb` 返回 JSON**（`ApiResponse[GlbPayload]`），data =
  `{filename, parts:[{id,instanceName,branch,partNumber}], branches:[int], glb: base64}`。
  前端先拿 `parts[].id` 拿到唯一标识，再 base64 解码 `data.glb` 加载模型。**不再是二进制流**。
- GLB 里**每个可高亮单元必须是独立的 glTF 节点**，`node.name` 即 `parts[].id`，必须一致。
  - 普通零件：`node.name` = CATIA 零件实例名（`Product.Name`，形如 `Bracket.1`），`branch=None`。
  - 线束分支：**每根分支单独一个节点**，`node.name` = `父实例名#分支号`（如 `多分支1.1#4`），
    `branch` = 4，`parts` 里**每根分支各占一项**（不是只给一项 + 一个 branches 数组）。
- 该标识与 `/getselected` 返回的 `parts[].name` **同源**（普通零件都来自 `Product.Name`）。
- **不要**把多个零件/多个分支合并进一个 `TopoDS_Compound` 再整体 `AddShape`——那样 GLB 只有一个节点、
  没有名字，前端无法定位单个零件/分支。
- **分支裁剪**由 `step_to_gltf.split_branch_geometry()` 完成，每根分支各一个 `_add_named_shape`；
  导出函数返回 `list[ExportedNode]`，service 用 `_nodes_to_parts()` 把它转成 `parts`，
  用 `_branches_of()` 得出**实际**导出的分支号。旧的 `select_branch_geometry()`（合并版）保留兼容。
- 命名规则集中在 `step_to_gltf.branch_node_name()` + `BRANCH_SEP = "#"`，要换格式只改这一处。

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

## 线束模型结构（案例：D:\电缆数模案例\cable example.CATProduct）
```
cable example.CATProduct
  ├─ Clips ASSY (clipA.1/.2/.3)      clipA.CATPart
  ├─ Connectors ASSY (conn1_female.1 / conn1_male.1)
  ├─ Grommets ASSY / Eyelets ASSY
  └─ Harness ASSY-project cable example
        └─ 几何束1.1                  (GeometricalBundle1.CATProduct)
             └─ 多分支1.1             (MultiBranchable1.CATPart)
                  ├─ Body: 零件几何体, 几何体.1~4   (各含 1 个实体：肋.1~4)
                  ├─ HybridBody: 电气线路几何体.1~4 (各含 柔性曲线.N + 圆.N)
                  └─ HybridBody: 几何图形集.1        (35 个构造元素：平面/点/项目/曲面.1~3)
```
- 一个「多分支」线束的**所有分支几何都在同一个 CATPart 里**：4 个分支 = 4 个
  `几何体.N`（实体肋）+ 4 个 `电气线路几何体.N`（电气路径）。
- 用户在规格树里点的 `分支.1 / 分支.2 / 分支.3 ...` 是**电气工作台（ELW）特征**。

## CATIA 自动化限制（踩坑，重要）
- **`分支.N` 未暴露给自动化**：无论树里点 `分支.2` 还是 Search `Name=分支*,all`（能匹配到 4 个），
  `SelectedElement.Value` / `.Reference` 一律解析回 Product `多分支1.1`，**无法区分是第几个分支**。
  → 想"按 CATIA 选中项只导出那一个分支"，在树选中这条路上做不到。
- `SelectedElement.Reference.DisplayName` 能给出规格树路径（到 product 级），
  形如 `cable example/Harness ASSY-project cable example/几何束1.1/多分支1.1/`。
- `SelectedElement.LeafProduct` 语义 = 规格树路径上**最深的 Product**（如 `多分支1.1`），
  不是"最深的几何体"，所以选任何分支都返回同一个 Product。
- **`Selection.Copy` + 新建零件 + `PasteSpecial` 不可用**：`PasteSpecial` 报
  "该 CSO 为空"，导出的是空白零件（STEP 仅 ~3126 字节）；HybridBody 的 `Copy` 直接失败。
  且调用 PasteSpecial 会在会话里留下 `ccp_dummy*_Format_*` 内部占位文档。
- 零件若处于可视化模式，`Selection.Search("Name=肋*,all")` 返回 0（特征未加载）。
- **`/getglb` 原来整文档 `ExportData`**，所以选任何分支都导出整个 `MultiBranchable1.CATPart`
  → 已通过下面「分支识别」方案修复。

## 分支识别方案（已实现，重要）
- **树里点 `分支.N`：拿不到分支号**（CATIA 限制，见上）。这是无法绕过的。
- **3D 视图点分支表面：能拿到分支号**。选中项的 `Value.Name` / `Reference.Name` 形如
  `Selection_RSur:(Face:(Brp:(EhiBundleSegmentRib.2;0:(Brp:(ElecCurve.2;1);
  Brp:(GSMCircle.2;...))));...)`，其中的数字即分支号（正则见 service 的 `_BRANCH_NAME_PATTERN`）。
- **3D 拾取点**：`SelectedElement(item).get_coordinates()` 给出总成坐标的拾取点。
  `Type == "Product"` 或坐标为 `(0,0,0)` 时视为无效（非几何选择的兜底值）。
- **STEP 实体顺序 = 分支号**：整体导出的 STEP 里 4 个 `MANIFOLD_SOLID_BREP`
  名为 `几何体.1..4`；用真实拾取点验证，点 `Rib.N` 到第 N 个实体距离**恰好 0.0000**，
  到其它实体 ≥51 → 顺序严格对应，可作为裁剪依据。
- **裁剪优先级**：拾取点几何判定（`BRepExtrema_DistShapeShape`）> 分支序号；两者都没有则保留整零件。
  多选多个分支时，序号/拾取点取并集，**每根命中的分支各写一个独立节点**。
- **可复跑的回归基准**（`_tmp_branch/baseline_full.stp`，同一次导出）：整根 21947 顶点；
  单根 `#1=4305`、`#2=5823`、`#4=6410`；`[1,2]=10128`、`[1,4]=10715`（精确相加，说明分割无漏无重）。
- 注意：`read_step_file_with_names_colors()` 只给到 `多分支1` 一层（XDE 不把 4 个实体当命名节点），
  所以**不要指望从 STEP 拿实体名**，用几何判定。

## 本机工具环境坑（踩坑，重要）
- **PowerShell 工具的 stdout 经常被吞掉**：命令看似成功但没输出。统一「结果写文件 → 用 Read 读」。
- **`fastapi.testclient` 不可用**：缺 `httpx`（本环境没装）。验证接口契约请直接调用端点函数 +
  检查 `app.openapi()`。
- **PowerShell 的 `Add-Type` 被安全策略禁止**（不能编译 .NET 代码）。需要枚举窗口等用 Python + pywin32。
- **Bash 工具在本机坏了**（`ls` / `head` / `dirname` 都 not found），不要用，改用 Glob/Grep/Read。
- CATIA 自动化中途若 `GetActiveObject` 报「操作无法使用」但进程还在 →
  多半是弹了模态对话框。用 pywin32 `EnumWindows` 找 `class=#32770` 窗口，
  `PostMessage(hwnd, WM_CLOSE)` 取消。
