---
name: canvas-workflow
description: 在项目的节点画布上搭建、连线并运行创作链路（提示词 → 图片 → 视频 / 语音）。当用户说"在画布上"、"@EP source"、"帮我搭一条链"、"用画布生成"，或通过画布输入框向你下达指令时使用。
user-invocable: false
---

# 节点画布工作流

画布是用户正在眼前看着的创作图，你的每一次改动都会实时出现。目标是把「提示词 → 图片 → 视频 / 语音」摆成清楚的一条链，并替用户运行。

## 开始前

1. 先调用 `get_canvas`，读清现有节点、连线与已存的模板；不要凭空新建重复的节点。
2. 画布上一切都是原语节点（text / image / image_generate / video_generate / audio_generate / video / audio / asset / markdown）加分组（group）；没有"角色""场景"这类特殊节点。角色、场景、道具、商品各是一个分组：文本 → 图片供应商 → 图片 → 资产节点（asset）。
3. 资产节点是发布键：它的输入图成为项目资产图（`delete_asset` 可删项目资产）；删除分组只从画布移除，项目资产保留。提示词里写 `@[名称]` 会自动带上同名资产的图，无需连线。项目里有、画布上没有的资产用 `canvas_import_assets` 摆上来；用一张现成图新建资产：`canvas_create_asset_group`（返回各子节点 id，描述要写）→ `canvas_set_node_image`（`file` 写项目内图片路径，`asset_node_id` 填返回的资产节点 id）；一集的制作流程用 `canvas_import_episode`（单元脚本 → 导演 → 关键帧 → H3 → 视频 → 配音）。
4. 用户在画布上选中了节点（或用 Add to Context 加入上下文的 @节点 id）时，指令里会带上节点 id：围绕它工作，不要动无关的节点。

集分组顶部有三个文档节点，用 `out → in` 连成一条线：EP source → Screenplay → Script plan（文档口只在文档节点之间连）。

**用户 @ 了 EP source 节点**（引用的节点 `data.step` 是 `source`，`data.ep` 是集号 N），并要求写剧本或只是引用它让你往下做：
1. 用 `write-screenplay` 子智能体为第 N 集写剧本（它读源文、用 `submit_screenplay` 提交）。本集已有剧本且没被明确要求重写时，先回报，不覆盖。
2. 提交成功后调用 `canvas_import_episode`（集号 N）：集分组或 Screenplay 节点还没有就补上，并把 EP source → Screenplay → Script plan 连好；已有的不动。
3. 回复剧本摘要，并告诉用户去 Screenplay 节点审阅：审阅通过只由用户点「通过审阅」，你不替他审。

**用户 @ 了集分组**（引用的节点是 `group` 且 `data.episode` 是集号 N）并让你往下做：这是「继续第 N 集」，按 video-workflow 的流程走：
1. `get_workflow_plan`（目标集 N）读 `next_action`，按它 dispatch 对应子智能体（剧本规划 `split-reference-video-units`、导演 `direct-episode`、提示词 `create-episode-script`、关键帧 `plan-keyframes` 等），每步返回后刷新计划继续。
2. 遇到要用户决定的动作（剧本审阅、规划确认、导演审阅等）就停下，告诉用户去哪个节点 / 页面决定；会计费的生成（图 / 视频 / 配音）先说明再等用户同意，不擅自提交。
3. 集分组会自动跟上项目：新出现的单元行（脚本 → 导演 → 关键帧 → H3 → 视频 → 配音）与关键帧板自动补到画布上，节点内容随项目文件自动刷新，不必每步都手动导入。只有用户把节点删了又想要回来、或分组不在画布上时才调用 `canvas_import_episode`（集号 N）。用户已删掉的单元不会被自动加回。
4. 参考生视频 + H3 的集在提示词写完后要处理起始帧：规划用 `plan-keyframes`；出图用 `generate_keyframe_sheet`（付费，先说明并得到同意）；格子的审核只由用户做。

用户也可以直接点 Screenplay 节点上的「让 Agent 写剧本」，等同于第一个流程。

## 搭链的规则

- 节点有类型：`text`（提示词 / 备注）、`image_generate`（图片供应商）、`video_generate`（视频供应商：提示词 + 可选起始帧图 → 视频）、`audio_generate`（语音供应商：文本 → 语音），后三类可运行。视频 / 语音 / 图片节点由运行产出，不能 `canvas_add_node` 新增；项目里已有的图片文件用 `canvas_set_node_image` 放上画布；归类用 `canvas_group` / `canvas_ungroup`（只动画布，不改项目数据）。
- 句柄只在同数据类型之间连：text→`prompt`（text）、图片→图片供应商的 `image1`（image）、图片→视频供应商的 `frame`（起始帧）、text→语音供应商的 `text`。连错会被拒绝，按返回的问题改正。
- 把 `image_generate` 的输出句柄 `image` 直接连到另一个 `image_generate` 的 `image1`（或 `video_generate` 的 `frame`），下游运行时会取上游**最近一次**产出的图，重跑上游后无需重连。
- 视频和语音都比图片贵且慢：运行前确认 `duration_seconds` 是所用视频模型支持的时长；视频进行中服务被重启会让该节点失败，需要重新运行。
- 放置新节点前看现有节点坐标：节点宽约 280，同列纵向间隔取 ≥ 300，沿输入→输出从左到右排；不要叠在一起。
- 参考图在请求里按**从上到下的摆放顺序**编号成「图1」「图2」……运行时服务端会自动附上编号说明；提示词里要指认某张参考图时就写「图N」。

## 运行

- `canvas_run_node` 只提交任务并立刻返回任务 id，**不等结果**。运行后用 `get_canvas` 查看进度：输出节点的 `status` 是 `queued` / `running` / `done` / `failed`，失败时节点的 `error` 写明原因。
- 有上下游依赖时按先上游后下游的顺序运行，并确认上游已 `done` 再跑下游；上游没有产物时下游拿不到参考图。
- 图片节点带 `stale: true` 说明它生成之后输入已经变了，询问用户是否需要重跑，不要擅自批量重跑（每次运行都计费）。

## 模板

用户反复要同一套结构时，用 `canvas_save_template` 把选中的文本 / 供应商节点存成模板，之后用 `canvas_use_template` 在新位置展开。产出的图片和上传的图片不会进模板。

## 不要做

- 不要为了"整理"而删除用户的节点或连线；正在排队或运行的节点删不掉，也不要尝试绕过。
- 不要在没有被要求时指定 `model`：节点不填就用项目默认的图像模型。
- 不要用画布工具替代项目的审阅流程：剧本、导演的审核仍按各自的工作流进行。
- 改动项目数据（资产、脚本）前先说明；会计费的运行（图片 / 视频 / 语音）只在用户要求时执行。你的改动会被存成快照，用户可在界面回滚。
