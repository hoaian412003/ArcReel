/** 节点画布的后端契约类型。 */

export type CanvasNodeType = "text" | "image" | "image_generate" | "h3_writer" | "video_generate" | "audio_generate";
/** 由项目数据推导的只读节点：集分组、视频单元、起始帧联合图、H3 提示词。 */
export type CanvasDerivedType = "episode" | "unit" | "keyframe" | "h3_prompt" | "character";
/** 运行产出的媒体节点。 */
export type CanvasMediaNodeType = "video" | "audio";
export type CanvasAnyNodeType = CanvasNodeType | CanvasDerivedType | CanvasMediaNodeType;
export type CanvasHandleType = "text" | "image" | "unit" | "h3" | "video" | "audio";
export type H3Status = "current" | "stale" | "missing";
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
  version?: number;
  /** 由某次运行自动挂出的输出节点。 */
  generated?: boolean;
  prompt?: string;
  /** 读时算出：这张图的输入已不同于生成它时的输入。 */
  stale?: boolean;
  /** 派生节点：episode / unit / keyframe / h3_prompt / character。 */
  /** 角色分组及其子节点：角色名（内容取自项目里的角色条目）。 */
  name?: string;
  /** 角色分组里的图片节点：reference 参考图（可上传）/ sheet 资产图。 */
  role?: "reference" | "sheet" | "derivative";
  /** 角色衍生节点：衍生名（`name` 仍是角色名）。 */
  derivative?: string;
  episode?: number;
  title?: string;
  unit_count?: number;
  h3?: boolean;
  unit_id?: string;
  h3_status?: H3Status | null;
  sheet_id?: string;
  scene?: string | null;
  problem?: { code: string; name: string } | null;
  cell_count?: number;
  approved?: boolean;
  sections?: Record<string, string> | null;
  [key: string]: unknown;
}

export interface CanvasNode {
  id: string;
  type: CanvasAnyNodeType;
  position: { x: number; y: number };
  data: CanvasNodeData;
  /** 服务端推导的节点：只读，位置相对 parentId（集分组）。 */
  derived?: boolean;
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
  /** 项目数据推导出的连线，不可删除。 */
  virtual?: boolean;
}

export interface CanvasEpisodeOption {
  episode: number;
  title: string;
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
  /** 画布上展示项目节点的集。 */
  view: { episodes: number[] };
  /** 可展示的集（仅参考生视频项目有）。 */
  episodes: CanvasEpisodeOption[];
  templates: CanvasTemplateSummary[];
}

export interface CanvasRunResult {
  success: boolean;
  task_id: string;
  task_ids: string[];
  text_node_id: string;
  image_node_id: string;
}
