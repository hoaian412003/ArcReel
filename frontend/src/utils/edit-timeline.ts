import type { EditClip, EditUnit } from "@/types/edit-timeline";

/** 素材时长缺失时的兜底（秒）：只影响界面上的片段宽度估算，渲染以实测时长为准。 */
const FALLBACK_DURATION_S = 5;

export function newClipId(): string {
  return Math.random().toString(36).slice(2, 14);
}

/** 入出点针对的视频版本已不是 current 时，渲染忽略裁切、整段使用。 */
export function isTrimStale(clip: EditClip, unit: EditUnit | undefined): boolean {
  return (
    clip.trimmed_against_version !== null && unit !== undefined && clip.trimmed_against_version !== unit.version
  );
}

/** 片段实际使用的源素材区间（秒）。 */
export function effectiveRange(clip: EditClip, unit: EditUnit | undefined): [number, number] {
  const total = unit?.duration_s ?? FALLBACK_DURATION_S;
  if (isTrimStale(clip, unit)) return [0, total];
  return [clip.in_s, clip.out_s ?? total];
}

export function clipLength(clip: EditClip, unit: EditUnit | undefined): number {
  const [start, end] = effectiveRange(clip, unit);
  return Math.max(0, end - start);
}

export function totalLength(clips: EditClip[], units: Map<string, EditUnit>): number {
  return clips.reduce((sum, clip) => sum + clipLength(clip, units.get(clip.unit_id)), 0);
}

export function addUnitClip(clips: EditClip[], unitId: string): EditClip[] {
  return [
    ...clips,
    {
      clip_id: newClipId(),
      unit_id: unitId,
      in_s: 0,
      out_s: null,
      trimmed_against_version: null,
      transition_to_next: "cut",
    },
  ];
}

export function removeClip(clips: EditClip[], clipId: string): EditClip[] {
  return clips.filter((clip) => clip.clip_id !== clipId);
}

export function moveClip(clips: EditClip[], clipId: string, offset: -1 | 1): EditClip[] {
  const from = clips.findIndex((clip) => clip.clip_id === clipId);
  const to = from + offset;
  if (from < 0 || to < 0 || to >= clips.length) return clips;
  const next = [...clips];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

export function updateClip(
  clips: EditClip[],
  clipId: string,
  patch: Partial<Pick<EditClip, "in_s" | "out_s" | "transition_to_next">>,
  unit: EditUnit | undefined,
): EditClip[] {
  return clips.map((clip) => {
    if (clip.clip_id !== clipId) return clip;
    const next = { ...clip, ...patch };
    // 改动入出点即以当前版本为裁切依据；恢复为整段时清除依据。
    if ("in_s" in patch || "out_s" in patch) {
      const whole = next.in_s === 0 && next.out_s === null;
      next.trimmed_against_version = whole ? null : (unit?.version ?? null);
    }
    return next;
  });
}

/** 在源素材时间 ``at`` 处把片段拆成两段；``at`` 不在片段区间内时原样返回。 */
export function splitClip(
  clips: EditClip[],
  clipId: string,
  at: number,
  unit: EditUnit | undefined,
): EditClip[] {
  const index = clips.findIndex((clip) => clip.clip_id === clipId);
  if (index < 0) return clips;
  const clip = clips[index];
  const [start, end] = effectiveRange(clip, unit);
  if (at <= start + 0.1 || at >= end - 0.1) return clips;
  const version = unit?.version ?? null;
  const first: EditClip = {
    ...clip,
    in_s: start,
    out_s: at,
    trimmed_against_version: version,
    transition_to_next: "cut",
  };
  const second: EditClip = {
    ...clip,
    clip_id: newClipId(),
    in_s: at,
    out_s: end,
    trimmed_against_version: version,
  };
  return [...clips.slice(0, index), first, second, ...clips.slice(index + 1)];
}

/** 每个片段在整条时间线上的起始时间（秒）。 */
export function clipStarts(clips: EditClip[], units: Map<string, EditUnit>): number[] {
  const starts: number[] = [];
  let cursor = 0;
  for (const clip of clips) {
    starts.push(cursor);
    cursor += clipLength(clip, units.get(clip.unit_id));
  }
  return starts;
}

/** 把整条时间线上的时间定位到 ``(片段下标, 片段内偏移)``；越界时夹到首尾。 */
export function locateTime(
  clips: EditClip[],
  units: Map<string, EditUnit>,
  time: number,
): { index: number; offset: number } {
  if (clips.length === 0) return { index: 0, offset: 0 };
  const starts = clipStarts(clips, units);
  const t = Math.max(0, time);
  let index = starts.length - 1;
  for (let i = 0; i < starts.length; i++) {
    if (t < starts[i] + clipLength(clips[i], units.get(clips[i].unit_id))) {
      index = i;
      break;
    }
  }
  const length = clipLength(clips[index], units.get(clips[index].unit_id));
  return { index, offset: Math.min(Math.max(0, t - starts[index]), length) };
}

/** 时间线上的一段配音：位置按预览用的顺序时间轴（转场重叠只在成片里出现）。 */
export interface DubBlock {
  key: string;
  clipId: string;
  unitId: string;
  index: number;
  text: string;
  audioUrl: string;
  /** 在整条时间线上的开始时刻与时长（秒）。 */
  start: number;
  duration: number;
  /** 在片段内的开始时刻（相对片段起点，秒）。 */
  offsetInClip: number;
  /** 落在片段区间内的这一截，在源视频里的起止时刻（秒）。 */
  sourceStart: number;
  sourceEnd: number;
  /** 配音音频文件里要从哪一秒开始播（配音开始早于片段入点时 > 0）。 */
  fileOffset: number;
}

/** 配音落在片段入出点之内的部分太短就不放了，与渲染一致。 */
const MIN_DUB_BLOCK_S = 0.05;

/** 把每个片段区间内的配音换算成时间线上的块；跨过入点 / 出点的只留区间内的那一截。 */
export function dubBlocks(clips: EditClip[], units: Map<string, EditUnit>, starts: number[]): DubBlock[] {
  const blocks: DubBlock[] = [];
  clips.forEach((clip, i) => {
    const unit = units.get(clip.unit_id);
    if (!unit?.dub_lines?.length) return;
    const [rangeStart, rangeEnd] = effectiveRange(clip, unit);
    for (const line of unit.dub_lines) {
      const begin = Math.max(line.start_s, rangeStart);
      const end = Math.min(line.start_s + line.duration_s, rangeEnd);
      if (end - begin <= MIN_DUB_BLOCK_S) continue;
      blocks.push({
        key: `${clip.clip_id}:${line.index}`,
        clipId: clip.clip_id,
        unitId: clip.unit_id,
        index: line.index,
        text: line.text_vi,
        audioUrl: line.audio_url,
        start: starts[i] + (begin - rangeStart),
        duration: end - begin,
        offsetInClip: begin - rangeStart,
        sourceStart: begin,
        sourceEnd: end,
        fileOffset: begin - line.start_s,
      });
    }
  });
  return blocks;
}
