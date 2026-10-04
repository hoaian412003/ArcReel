/** 编剧剧本（screenplay）的后端契约类型：按场次组织的单集剧本与审阅状态。 */

export type ScreenplayStatus = "none" | "pending_review" | "approved";

export interface ScreenplayScene {
  heading: string;
  summary: string;
  estimated_seconds: number;
  text: string;
}

export interface Screenplay {
  episode: number;
  status: ScreenplayStatus;
  /** 当前内容的指纹；修改 / 通过时带回，内容已变则被拒。尚未编写时为 null。 */
  fingerprint: string | null;
  title?: string;
  logline?: string;
  /** 本集预计总时长（秒）：各场预计时长相加，由服务端计算。 */
  total_seconds: number;
  scene_count?: number;
  scene_seconds?: number[];
  scenes: ScreenplayScene[];
  /** 面向审阅者的可读版本。 */
  markdown?: string;
  approved_at?: string | null;
}

export type ScreenplayOperation =
  | { op: "update"; index: number; fields: Partial<ScreenplayScene> }
  | { op: "insert"; index: number; scene: ScreenplayScene }
  | { op: "delete"; index: number }
  | { op: "set_meta"; fields: { title?: string; logline?: string } };
