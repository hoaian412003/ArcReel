/** Flow 看板与分镜导演报告审阅的后端契约类型。 */

export type FlowColumnKind = "agent" | "human";

export interface FlowColumn {
  id: string;
  kind: FlowColumnKind;
}

export interface FlowCard {
  episode: number;
  title: string;
  column: string;
  next_action: string;
  blocked: boolean;
}

export interface FlowBoard {
  columns: FlowColumn[];
  cards: FlowCard[];
}

export type DirectionReviewStatus =
  | "not_applicable"
  | "no_direction"
  | "pending_review"
  | "approved"
  | "changes_requested";

export interface DirectionReportRow {
  id: string;
  description: string;
  camera: {
    angle: string;
    shot_size: string;
    movement: string;
    intent: string;
  };
  performance: { character: string; note: string }[];
  continuity_in: string;
  continuity_out: string;
}

export interface DirectionReportWarning {
  code: string;
  entry_ids: string[];
  message: string;
}

export interface DirectionReport {
  route?: "storyboard";
  episode_intent: string;
  tone: string;
  arc: { beat: string; units: string[] | string }[];
  rows: DirectionReportRow[];
  angle_distribution: Record<string, number>;
  warnings: DirectionReportWarning[];
}

/** 参考生视频单元的一个镜头：切入秒数、取景、运镜（类型 + 幅度 + 速度）、动作与切镜理由。 */
export interface ReferenceDirectionShot {
  at_seconds: number;
  framing: string;
  camera: { type: string; amplitude: string; speed: string };
  action: string;
  why_cut: string;
}

export interface ReferenceDirectionRow {
  id: string;
  description: string;
  duration_seconds: number;
  unit_type: string;
  brief: string;
  shots: ReferenceDirectionShot[];
  performance: { character: string; note: string; voice: string }[];
  lines: { speaker: string; text: string; shot: number; delivery: string }[];
  sounds: string;
  music: string;
  must_not_show: string;
  end_frame: string;
  continuity_in: string;
  continuity_out: string;
}

/** 参考生视频路线的导演报告：逐单元的镜头表、表演与声音、台词落位，以及运镜分布。 */
export interface ReferenceDirectionReport {
  route: "reference";
  episode_intent: string;
  tone: string;
  arc: { beat: string; units: string[] | string }[];
  rows: ReferenceDirectionRow[];
  camera_distribution: Record<string, number>;
  warnings: DirectionReportWarning[];
}

export interface DirectionReview {
  episode: number;
  status: DirectionReviewStatus;
  fingerprint: string | null;
  decided_at: string | null;
  note: string | null;
  report: DirectionReport | ReferenceDirectionReport | null;
}

export interface DirectionReviewDecision {
  decision: "approve" | "changes" | "redo";
  note?: string;
  entry_ids?: string[];
}
