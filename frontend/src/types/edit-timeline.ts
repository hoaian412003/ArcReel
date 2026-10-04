export type EditTransition = "cut" | "fade" | "dissolve" | "wipe";

export interface EditClip {
  clip_id: string;
  unit_id: string;
  in_s: number;
  out_s: number | null;
  trimmed_against_version: number | null;
  transition_to_next: EditTransition;
}

export interface EditRevision {
  revision: number;
  parent_revision: number | null;
  author: string;
  summary: string;
  created_at: string;
  clips: EditClip[];
  /** 成片是否混入越南语配音（原声压低、配音叠加）；旧修订没有这个字段，按关闭处理。 */
  voice_over?: boolean;
}

export interface EditTimeline {
  timeline_id: string;
  episode: number;
  name: string;
  revisions: EditRevision[];
}

export interface EditTimelineSummary {
  timeline_id: string;
  name: string;
  latest_revision: number;
  clip_count: number;
  updated_at: string;
}

/** 单元里一条已合成的越南语配音；时间相对该单元视频的起点，与片段入出点同一时间轴。 */
export interface EditDubLine {
  index: number;
  text_vi: string;
  start_s: number;
  duration_s: number;
  audio_url: string;
}

export interface EditUnit {
  unit_id: string;
  video_url: string | null;
  version: number;
  duration_s: number | null;
  dub_lines?: EditDubLine[];
}

export interface EditTimelineDiagnostics {
  deleted_unit_clips: string[];
  unused_unit_ids: string[];
  stale_trim_clips: string[];
  missing_video_clips: string[];
}

export interface EditFinalCut {
  url: string;
  stale: boolean;
  revision: number | null;
}

export interface EditRenderState {
  status: "running" | "done" | "failed";
  revision?: number;
  error_key?: string;
  error_params?: Record<string, unknown>;
}

export interface EditTimelineDetail {
  timeline: EditTimeline;
  diagnostics: EditTimelineDiagnostics;
  units: EditUnit[];
  transitions: EditTransition[];
  final_cut: EditFinalCut | null;
  render: EditRenderState | null;
  /** 项目里有已合成的配音，时间线才有配音开关。 */
  voice_over_available?: boolean;
  /** 混入配音时原声的音量（0–1）。 */
  original_volume?: number;
}
