---
id: reference_video/keyframe_sheet
category: video
title: 起始帧联合图
description: 把同一场景里若干个「站位 + 起始景别」画进一张联合图的提示词；生成后按格切开，每格作为视频单元的首帧参考。
stage: keyframe_sheet
invoked_by:
  kind: generation_task
  name: keyframe_sheet
applies_to:
  generation_mode:
  - reference_video
slots:
  reference_images: 参考图声明，每行一张，按随请求发出的参考图序位编号为「Image N」
  rows: 联合图行数
  cols: 联合图列数
  cell_count: 画格总数
  grid_aspect_ratio: 整张联合图的比例
  panel_aspect_ratio: 单个画格的比例
  scene_name: 发生地的场景名
  characters: 出镜角色列表（name、face、body）；face / body 是该角色脸部特写与全身三视图的图号
  cells: 画格列表（number、row、col、people、staging、framing）；people 已写好各人物对应的图号
  placeholders: 空占位格列表（number、row、col）
  style: 项目画风
  style_description: 项目风格描述
protected: false
---
Reference images, in the order they are attached:
{{ reference_images }}

Create ONE composite image: a strict {{ rows }}×{{ cols }} grid with exactly {{ cell_count }} panels of identical size. Every panel is a different camera shot of the same moment in the same scene, “{{ scene_name }}”, with the same people standing or sitting in the same places.

LAYOUT
- Exactly {{ rows }} rows and {{ cols }} columns, {{ cell_count }} panels, read left to right, top to bottom.
- Whole image aspect ratio {{ grid_aspect_ratio }}; every panel is {{ panel_aspect_ratio }} and all panels are exactly the same size.
- Separate the panels with thin white lines. Do not merge, skip or shift panels.
- Keep the same setting, lighting and colour grade in every panel.

IDENTITY (highest priority)
{{ partial("reference_video/keyframe_sheet/lists/characters") }}
- Every person must be recognisably the same person as in their reference images: copy the exact face shape, eyes, nose, mouth, skin tone, hairstyle and body build. Do not beautify, slim down, lighten or darken anyone, and do not invent a new face.
- The same person must look identical in every panel: same face, skin tone, hair and clothes.
- Show only the people listed for each panel. Do not add anyone else.

PANELS
{{ partial("reference_video/keyframe_sheet/lists/cells") }}

STYLE
{{ partial("shared/media_style") }}

Identity fidelity overrides the style above: if it implies beautifying, smoothing or retouching faces, ignore that for faces and keep the natural faces from the reference images.

Avoid: faces, skin tones or hairstyles that differ from the references; the same person looking different between panels; extra people; text, captions, signs with writing, watermarks, logos; sub-panels inside a panel.
