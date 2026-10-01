/** 模版的触发方；`name` 是工具名、用户操作名或任务类型，取值由后端封闭声明。 */
export interface PromptTemplateTrigger {
  kind: "agent_tool" | "user_action" | "generation_task";
  name: string;
}

/** 提示词模版元数据（frontmatter），列表与详情共用。 */
export interface PromptTemplateMeta {
  id: string;
  category: string;
  title: string;
  description: string;
  /** 所属制作步骤。 */
  stage: string;
  invoked_by: PromptTemplateTrigger;
  /** 轴名 → 适用的轴值；变体族按这里的轴值展开。 */
  applies_to: Record<string, string[]>;
  /** 槽位名 → 说明。 */
  slots: Record<string, string>;
  protected: boolean;
  /** `module:Class`，无结构化输出时为 null。 */
  output_schema: string | null;
}

export interface PromptTemplateListResponse {
  templates: PromptTemplateMeta[];
}

export interface PromptTemplatePartial {
  name: string;
  source: string;
  /** 锁定：不提供编辑入口。 */
  protected: boolean;
  /** 引用它的模版 id，按注册顺序。 */
  referenced_by: string[];
}

/** JSON schema 节点，只声明设置页展开输出结构时读取的字段。 */
export interface JsonSchemaNode {
  type?: string;
  description?: string;
  enum?: unknown[];
  properties?: Record<string, JsonSchemaNode>;
  items?: JsonSchemaNode;
  anyOf?: JsonSchemaNode[];
  $ref?: string;
  $defs?: Record<string, JsonSchemaNode>;
}

export interface PromptTemplateDetail {
  template: PromptTemplateMeta;
  source: string;
  /** 按首次引用顺序；变体族展开为全部轴值。 */
  partials: PromptTemplatePartial[];
  output_schema?: JsonSchemaNode;
}

/** H3 提示词编写模版的全局替换状态；与后端 `H3AuthoringOverrideResponse` 对应。 */
export interface H3AuthoringOverride {
  /** 内置模版正文：编辑起点与「恢复默认」的内容。 */
  builtin: string;
  /** 全局替换正文；没有时为 null。 */
  override: string | null;
  max_chars: number;
}

export interface H3AuthoringPreview {
  /** 用示例数据渲染出的完整提示词。 */
  prompt: string;
}

/** 内置模版 id；与后端 `lib.prompts.h3_authoring_template.TEMPLATE_ID` 同值。 */
export const H3_AUTHORING_TEMPLATE_ID = "text/reference_video_prompt_authoring_h3_ref2va";
