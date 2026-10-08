---
id: text/asset_analysis
category: text
title: 资产分析
description: 从新上传的分集源文提取角色、场景、道具三类资产定义（外观描述与声音风格），已登记的资产不重复提取。
stage: asset_analysis
invoked_by:
  kind: user_action
  name: asset_analysis
applies_to:
  source_kind: [novel, screenplay]
output_schema: lib.project.asset_analysis:AssetAnalysisDraft
slots:
  source_kind: 源文类型轴值（novel / screenplay），用于解析命名变体片段
  target_language: 输出语言，取项目源语言，缺省为中文
  synopsis: 故事概述；无时传 null
  genre: 题材；无时传 null
  existing_characters: 已登记的角色名列表；无时传空列表
  existing_scenes: 已登记的场景名列表；无时传空列表
  existing_props: 已登记的道具名列表；无时传空列表
  source_content: 待分析的分集源文，每集以「第 N 集」标题行分隔
protected: false
---
{{ variant("text/asset_analysis/task", source_kind) }}

**输出语言**：所有字符串值使用 {{ target_language }}；JSON 键名保持英文。

# 项目信息
{% if synopsis %}
- 故事概述：{{ synopsis }}
{% endif %}
{% if genre %}
- 题材：{{ genre }}
{% endif %}
{% if not synopsis and not genre %}
- 无
{% endif %}

# 已登记的资产（不要重复提取）
- 角色：{% if existing_characters %}{{ existing_characters | join("、") }}{% else %}无{% endif %}

- 场景：{% if existing_scenes %}{{ existing_scenes | join("、") }}{% else %}无{% endif %}

- 道具：{% if existing_props %}{{ existing_props | join("、") }}{% else %}无{% endif %}


# 提取规则
- 只提取视觉信息：description 只写外貌、服装、标志物、色彩关键词；不写性格、关系、剧情。
- 角色 voice_style 记录声音与语气风格（如「温柔但有威严」）。
- 场景 description 只描述环境本身：空间结构、氛围、光线、色调；不写场景中的人物，也不写这里发生过的情节。
- 道具 description 写外观细节、材质、尺寸参考、色彩特征。
- 源文没有明确视觉描写的资产，description 写空字符串，不猜测、不补写。
- 严格增量：已登记的资产一律不要出现在输出中，即使本批源文给了更多细节。
- 同一角色在本体之外的另一套外观，满足「跨场景持续、能由图像表现」时登记为该角色的衍生：衍生名用简短状态名，description 只写相对本体的变化，用图像编辑指令口吻（如「换上黑色重甲，其余外观保持不变」）。单个镜头内的表情、姿势、临时状态不算衍生。
- 没有可提取内容的类别输出空列表。

# 源文
{{ source_content }}
