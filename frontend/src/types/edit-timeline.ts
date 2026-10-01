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

export interface EditUnit {
  unit_id: string;
  video_url: string | null;
  version: number;
  duration_s: number | null;
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
}
