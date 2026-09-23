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
- **`/getglb` 默认导出「全部选中项」**（`index` 不传 / 传 0），可**跨零件**——线束分支 + 卡扣
  会合成到同一个 GLB；传 `index >= 1` 时只导第 index 个选中项（兼容旧调用）。
  前端目前**不传 index**，所以这条默认值就是它的实际行为。
- service 侧按 `LeafProduct.Name` **分组**（保持选择顺序），每组只吃自己的分支过滤，
  各组的 STEP 条目汇总后**一次** `steps_to_gltf` 写出，避免跨零件误裁。

## 裁剪切入口（容易踩的坑）
- **只有识别到电气分支特征**（`_BRANCH_NAME_PATTERN` 命中 `EhiBundleSegmentRib|ElecCurve|GSMCircle`）
  才把 `branches`/`pick_points` 传给导出层；**仅有点击点、没有分支号时一律丢弃**。
- 否则普通零件（卡扣、支架…）上的一次面点击会被当成"分支配对"，把零件裁成
  「离点击点最近的那个实体」，并把节点名变成 `clipA.2#1`、多出一个假的 `branch: 1`。
  卡扣恰好只有 1 个实体时结果看似正确，但节点名已经错了。

## CATIA Position 语义（关键！曾让零件「歪」）
- `Position.GetComponents()` 返回 12 个分量：**前 9 个依次是 x 轴、y 轴、z 轴的分量，
  后 3 个是原点坐标**。轴向量构成旋转矩阵的**列**（`R·e_x = x轴`），
  **必须按「列」填进变换矩阵**（见 `service._components_to_matrix`）。
- 一旦按「行」填 = 对旋转部分做**转置** → 零件绕轴**反向**倾斜。
  症状非常典型：**位移完全正确、只是姿态反了**（用户描述为"零件歪了"）。
- **为什么难发现**：绕某个轴 ±θ 的两版矩阵，其**轴对齐包围盒完全相同**（对称），
  所以靠 AABB / 顶点数 / 位移对比都查不出来。必须用**质心**或**点到表面距离**。
- **定位方法（可复用）**：拿 CATIA 里在零件表面拾取到的点（绝对坐标），
  用两种矩阵的逆变换回零件局部坐标，看哪个落在 `STEP 几何表面`（距离≈0）。
  实测：按列 0.015mm（正确）vs 按行 1.545mm。
- **金标准判据**：把整个装配 `doc.ExportData(assy.stp, "stp")` 导出（CATIA 自己烘好位置），
  用 `BRepGProp.VolumeProperties` 算每个实体质心 → 与我们的矩阵算出的世界质心比。
  实测卡扣：按列误差 **0.0003mm**，按行 0.7504mm。
  （脚本 `_grab_assy_truth.py`；它能顺带自证——装配里 `clipA.1` 位姿为单位阵，
  其实体质心恰为 `(0,0,-1.4531)`，正是零件局部质心。）
- 同一函数也被 `/listposition` 的 `globalRotation` 使用，修一处两边都正确。

## 变换链现状（已知限制）
- `_collect_part_transforms()` 从**选中节点自己**起算（父矩阵 = 单位阵），**不含祖先位姿**。
  （注意：上面修的「轴按列填」是**本级 own 矩阵**的正确性，与这条无关。）
- 本例（cable example）实测 `Clips ASSY` / `Harness ASSY` / 根节点位移全为 0，
  所以真实全局 == 本级 own，误差 0，跨零件合并不会错位。
- 若将来遇到**带位移的子装配**，跨零件（或单零件）导出都会错位。修法：从
  `ProductDocument(doc).product` 往下走、累积祖先矩阵（`list_position()` 已有同样的遍历可参考）。

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
