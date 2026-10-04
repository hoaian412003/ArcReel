---
id: text/reference_video_script_plan
category: text
title: 参考生视频 · 单元拆分
description: 把源文拆分为参考生视频单元表，每个单元含时长、原文锚与引用语法正文。
stage: script_plan
invoked_by:
  kind: agent_tool
  name: generate_script_plan
applies_to:
  content_mode: [drama, narration]
  generation_mode: [reference_video]
output_schema: lib.script.script_models:ReferenceScriptPlanContentFlatDraft
slots:
  target_language: 输出语言
  project_overview: 项目概述，键齐全的对象 {synopsis, genre, theme, world_setting}；缺值传 null
  assets: 资产外观，对象 {characters, scenes, props}，每项是 [{name, appearance}] 列表（尖括号已中和）
  character_names: 角色候选引用名列表（含 本体/衍生）
  scene_names: 场景候选引用名列表
  prop_names: 道具候选引用名列表
  novel_text: 逐字源文
  episode_outline: 本集大纲，键齐全的对象 {title, story_beats, hook, next_episode_teaser}；无有效内容时传 null
  next_episode_outline: 下集大纲，形状同上；无有效内容时传 null
  episode: 当前集号
  durations: 排序去重后的合法秒数档位，已格式化为文本
  duration_tiers: 「参考图↔时长」两套档位，对象 {with_references, without_references, default_with_references_only, default_without_references_only}，档位已格式化为文本，后两项表示默认秒数只落在其中一套档位内；两套相同或型号未声明时传 null
  default_duration: 默认秒数偏好；未设置时传 null
  speech_rate: 口播语速（阅读单位 / 秒），已格式化为文本
  speech_unit: 阅读单位量词（字 / 词）
  max_duration: 单次生成的时长上限（秒）
  max_reference_images: 单个视频单元的参考图上限；不限时传 null
  episode_target_duration: 单集目标秒数；未设置时传 null
  dub: 越南语配音要求，对象 {speech_rate, narrator_gender, characters}（characters 为 [{name, gender}]，gender 未登记时为 null）；项目未启用配音时传 null
  screenplay: 已确认剧本 {title, logline, scenes[{heading, summary, estimated_seconds, text}]}；没有已确认剧本时传 null
  instructions: 附加指令正文；缺值传 null
protected: false
---
# 角色与任务

你是一位视频单元架构师，本任务是把源文拆分为适配多模态参考生视频模型的 video_unit 表（script_plan 脚本规划）。
每个 video_unit 对应**一次视频生成调用**，正文是一段连续的画面描述，一次生成完整覆盖它。
本阶段定的是**结构与内容契约**：unit 边界、时长（时长即计费单位）、台词落位、核心资产指认——用户会逐 unit 审阅确认这份契约。
导演说明（镜头、表演、声音）由内容确认之后的导演步骤写，视觉编排由提示词编写阶段完成，本阶段都不写。

**输出语言**：所有字符串值必须使用 {{ target_language }}；JSON 键名保持英文。
例外（逐字保留、不翻译）：`@[名称]` 中的资产名须逐字等于下方候选表中的登记名；`source_text` 须逐字复制小说原文。
**结构约束**：字段 / 枚举 / 必填项由 response_schema 强制；本提示只解释**如何写好每个字段的内容**。

# 上下文

{{ partial("shared/overview_block") }}

{{ partial("shared/lists/asset_appearance_blocks") }}

## 小说原文

<novel>
{{ novel_text }}
</novel>
{% if screenplay %}

{{ partial("shared/lists/screenplay_block") }}
{{ partial("shared/screenplay/reference_video") }}
{{ partial("shared/screenplay/common_rules") }}
{% endif %}

{% if episode_outline %}
<episode_outline>
{% if episode_outline.title %}
标题：{{ episode_outline.title }}
{% endif %}
{{ partial("shared/lists/episode_outline_lines", outline=episode_outline) }}
</episode_outline>

{% endif %}
{% if next_episode_outline %}
<next_episode_outline>
{% if next_episode_outline.title %}
标题：{{ next_episode_outline.title }}
{% endif %}
{{ partial("shared/lists/episode_outline_lines", outline=next_episode_outline) }}
</next_episode_outline>

{% endif %}
# 拆分规则

当前正在生成第 {{ episode }} 集。请覆盖全部源文情节，按叙事顺序逐 unit 产出。

- **unit 边界**：每个 unit 对应一个连贯的视频生成片段——同一时间、同一地点、主体动作连续；
  时间 / 空间 / 情节重大切换点开新 unit。
- **source_text**：该 unit 所依据的小说原文片段，**逐字复制**（可截断首尾，但中间不得删字、改写、翻译或概括）。
  它是追溯锚，用于把生成结果对回原文；不逐字复制会被机械校验拒绝。
- **时长决策序**（自上而下，高优先级是硬边界，低优先级在其内做优化）：
  1. 硬约束：`duration_seconds` 是 unit 时长（一次生成调用一个时长），必须取支持档位（{{ durations }}）中的值。
     叙事需要的时长放不下时，把该 unit 按叙事顺序重拆为多个 unit，**不得违约时长**。
{% if duration_tiers %}
     本型号下该档位还随「有无参考图」分两套，按该 unit **画面描述里有没有 `@` 资产引用**取用（台词记号 `@[角色]{台词}` 的说话人不计入——它不生成参考图，只驱动音色声明）：带 `@` 引用取（{{ duration_tiers.with_references }}），不带取（{{ duration_tiers.without_references }}）。服务端按该 unit 此刻**可用的参考图**定档：引用的资产还没有生成参考图时，该 unit 按不带引用的那套档位执行。两者取其一：要么改取该 unit 引用状态对应档位内的值，要么调整引用——把次要资产融入描述文字、不用 `@` 引用，从而适用不带引用的那套档位。
{% endif %}
  2. 台词下界：先估算该 unit 全部台词与画外音念完约需的秒数（口播语速约 {{ speech_rate }} {{ speech_unit }}/秒），
     取**不低于**这个秒数的档位。这是单向下界——台词永不压进念不完的短档；无台词的 unit 没有此下界。
     台词量超过最长档（{{ max_duration }} 秒）时把该 unit 拆开，不要把台词硬塞进一个 unit。
  3. 默认偏好：{% if default_duration %}unit 默认取 {{ default_duration }} 秒{% if duration_tiers and duration_tiers.default_with_references_only %}（该默认值只落在带 `@` 引用的 unit 的档位内，另一种状态的 unit 按上面的硬约束取值）{% endif %}{% if duration_tiers and duration_tiers.default_without_references_only %}（该默认值只落在不带 `@` 引用的 unit 的档位内，另一种状态的 unit 按上面的硬约束取值）{% endif %}，叙事需要更长时可取更长档（偏好可被内容需要覆盖，硬约束不可）{% else %}按叙事需要从档位中取值，不强制默认值{% endif %}。
  4. 打包效率：在 1-3 之内组织正文内容，{% if episode_target_duration %}按本集目标时长打包——{{ partial("shared/episode_target_duration_rule") }}；单个 unit 在目标之内可长可短，不必贴近单次上限 {{ max_duration }} 秒，也{% else %}使 unit 时长贴近 {{ max_duration }} 秒；{% endif %}不要默认选最短 / 保守值。
{% if max_reference_images %}
- **references 上限**：一个 unit 的**画面描述里** `@` 引用的资产名（去重后）不超过 {{ max_reference_images }} 个（台词记号 `@[角色]{台词}` 的说话人不计入——它不生成参考图）；超出时把次要角色融入背景描述（不用 `@` 引用），不要压缩主体资产。
{% endif %}

# 正文书写语法

{{ partial("shared/writing_syntax") }}

# 本阶段的正文写作指引

- 画面描述聚焦当下瞬间的**可见动作**：谁做了什么、物件互动、环境动态；动词描述物理可观察动作
  （伸手 / 转身 / 推门 / 投向），避免「陷入 / 回忆 / 意识到 / 决定」等内心动词。
- 资产名必须逐字取自下列候选，不要发明候选之外的名称：
  - character: {{ partial("shared/asset_name_candidates", names=character_names) }}
  - scene: {{ partial("shared/asset_name_candidates", names=scene_names) }}
  - prop: {{ partial("shared/asset_name_candidates", names=prop_names) }}
- 原文里的人物对白写成台词记号（`@[角色]{台词}`），旁白 / 心声写成画外音记号（`{台词}`），逐字保留原文措辞；
  台词是内容契约的一部分，prompt_authoring 不会再改动它。
- **说话必有台词**：画面里只要有角色在说话 / 搭话 / 念叨 / 追问（含「说个不停」「念叨」这类原文只概述、没给原话的情形），正文里就必须为他写出具体的台词记号 `@[角色]{台词}`，不得只写「说话」的动作而没有台词。
  原文给了原话就逐字保留；原文只概述说话内容时，依据上下文与该概述补写 1–2 句简短、贴合人物与情境的台词，不新增情节，也不改变这个 unit 想要的效果（例如「对方不答」就不要让对方回话）。
  这是对上面「不杜撰台词」的唯一例外，且仅限已确定有人在说话的画面；整个 unit 没有人说话时不要硬加台词。
- 每个 unit 的正文都要 `@` 引用它发生地的场景资产；地点切换到新 unit 时换成新地点的场景，
  地点不变的连续 unit 逐条重复引用同一个场景（候选表里没有匹配的场景时才用文字写地点）。
- 本阶段不写景别 / 构图 / 运镜（prompt_authoring 补），把叙事内容与动作过程写清楚即可。
{% if dub %}

# 越南语配音台词（dub_lines）

本项目需要把成片配成越南语。每个 unit 在 `dub_lines` 里为正文中的**每一条发声**（台词记号 `@[角色]{台词}` 与画外音记号 `{台词}`，按它们在正文里的出现顺序合并编号，从 0 起）各写一行越南语配音，`dub_lines` 的行数与发声条数严格相等；正文里没有任何发声记号的 unit，`dub_lines` 留空数组。

- `index`：该发声在本 unit 正文中的序号（0 起，台词与画外音合并计序）。
- `speaker`：台词填说话角色名（逐字等于记号里的 `@[角色]` 名称）；画外音填 null。
- `gender`：`male` 或 `female`。台词按下表中该角色的性别取值；表中未登记性别时，依角色称谓、语气与上下文判断；画外音一律取 `{{ dub.narrator_gender }}`。
- `text_vi`：该条发声的越南语配音台词。译自对应记号内的台词，忠实原意，用地道口语，称谓与人称代词（anh / em / chị / tôi / tao / mày / cậu ...）须符合说话人与听者的性别、年龄与关系；不要逐字硬译，不要加原文没有的内容，不要夹带中文或括号注释。
- **篇幅预算**：越南语口播语速约 {{ dub.speech_rate }} 词/秒，一个 unit 里全部 `text_vi` 的总词数不得超过「该 unit 时长（秒）× {{ dub.speech_rate }}」，宁短勿长；译文偏长时精简措辞，而不是改动正文里的台词（正文台词是内容契约，保持不变）。
{% if dub.characters %}

角色性别（用于 `gender`）：
{{ partial("shared/lists/dub_character_genders", characters=dub.characters) }}
{% endif %}
{% endif %}
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
