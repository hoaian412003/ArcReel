/** 节点画布的后端契约类型。 */

export type CanvasNodeType = "text" | "image" | "image_generate" | "video_generate" | "audio_generate" | "group" | "markdown";
/** 由项目数据推导的只读节点：资产输出（分组框一律是 group）。 */
export type CanvasDerivedType = "asset";
/** 运行产出的媒体节点。 */
export type CanvasMediaNodeType = "video" | "audio";
export type CanvasAnyNodeType = CanvasNodeType | CanvasDerivedType | CanvasMediaNodeType;
export type CanvasHandleType = "text" | "image" | "video" | "audio" | "asset" | "doc";
export type CanvasNodeStatus = "idle" | "queued" | "running" | "done" | "failed";

export interface CanvasNodeData {
  /** text 节点的正文。 */
  text?: string;
  /** image 节点的项目相对路径；占位中为 null。 */
  path?: string | null;
  label?: string;
  /** image_generate 节点的画幅。 */
  aspect_ratio?: string;
  /** video_generate 节点的视频时长（秒）。 */
  duration_seconds?: number | null;
  /** audio_generate 节点的音色名；缺省用项目旁白音色。 */
  voice?: string | null;
  /** image_generate 节点指定的图像模型，形如 `供应商/模型`；缺省用项目默认。 */
  model?: string | null;
  /** 以下字段只由服务端写入。 */
  status?: CanvasNodeStatus;
  task_id?: string;
  error?: string | null;
  /** 运行提交 / 最近一次状态变化 / 开始运行 / 结束的时间（ISO，UTC）。 */
  submitted_at?: string;
  status_at?: string;
  started_at?: string;
  finished_at?: string;
  version?: number;
  /** 由某次运行自动挂出的输出节点。 */
  generated?: boolean;
  prompt?: string;
  /** 参考说明：这张图被别的节点当参考图时，随图一起交给供应商（如「This is his face…」）。资产图有默认值。 */
  note?: string;
  /** 流水线节点：所在集 / 单元 / 步骤（总览据此跳转，运行据此走已有生成链路）；compact 表示显示成紧凑卡片。 */
  ep?: number;
  unit?: string;
  step?: string;
  sheet?: string;
  cell?: string;
  compact?: boolean;
  /** 单元文本 / 导演说明节点：剧本里当前的值，与节点上的文本不同就可以「存回剧本」。 */
  script_value?: string;
  /** H3 提示词节点：current / stale / missing。 */
  h3_status?: string;
  /** 文档节点：文件的修改时间戳，变了就重读预览。 */
  file_rev?: number | null;
  /** 剧本节点：none / pending_review / approved / corrupt，及待审阅版本的指纹。 */
  screenplay_status?: "none" | "pending_review" | "approved" | "corrupt";
  screenplay_fingerprint?: string | null;
  /** markdown 文档节点：项目里的文件与标题。 */
  file?: string;
  title?: string;
  /** 分组节点：它是第几集的流程。 */
  episode?: number;
  /** 刚放进画布的资产分组：网页按节点实际尺寸排一次版后清除。 */
  autolayout?: boolean;
  /** group 节点：分组框的大小（标题用 label）。 */
  width?: number;
  height?: number;
  /** 读时算出：这张图的输入已不同于生成它时的输入。 */
  stale?: boolean;
  /** asset 节点（服务端按所在资产分组算出）：资产名与类型；分组节点上 `asset` 是它对应的项目条目类型，标题在 `label`。 */
  name?: string;
  asset?: "character" | "scene" | "prop" | "product";
  /** asset 节点：衍生名（如「幼年」）；有衍生名的资产节点不发布成项目的资产图。 */
  derivative?: string;
  [key: string]: unknown;
}

export interface CanvasNode {
  id: string;
  type: CanvasAnyNodeType;
  position: { x: number; y: number };
  data: CanvasNodeData;
  /** 所在分组；位置相对分组。 */
  parentId?: string;
  width?: number;
  height?: number;
}

export interface CanvasEdge {
  id: string;
  source: string;
  sourceHandle: string;
  target: string;
  targetHandle: string;
}

export interface CanvasTemplateSummary {
  id: string;
  name: string;
  node_count: number;
}

export interface CanvasBranchStep {
  id: string;
  type: string;
  reason: "missing" | "stale" | "target";
}

export interface CanvasGraph {
  revision: number;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  templates: CanvasTemplateSummary[];
}

export interface CanvasRunResult {
  success: boolean;
  task_id: string;
  task_ids: string[];
  text_node_id: string;
  image_node_id: string;
}

export type FlowStepState = "done" | "stale" | "empty";

export interface CanvasFlowUnit {
  unit_id: string;
  steps: Record<string, { state: FlowStepState; node_id: string | null }>;
}

export interface CanvasFlowEpisode {
  episode: number;
  title: string;
  source: boolean;
  screenplay: "none" | "pending_review" | "approved" | "corrupt";
  plan: boolean;
  group_id: string | null;
  units: CanvasFlowUnit[];
}
