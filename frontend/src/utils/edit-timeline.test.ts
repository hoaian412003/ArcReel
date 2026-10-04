import { describe, expect, it } from "vitest";
import type { EditClip, EditUnit } from "@/types/edit-timeline";
import {
  addUnitClip,
  clipLength,
  clipStarts,
  dubBlocks,
  locateTime,
  effectiveRange,
  isTrimStale,
  moveClip,
  splitClip,
  updateClip,
} from "./edit-timeline";

const unit: EditUnit = { unit_id: "U1", video_url: "/v.mp4", version: 2, duration_s: 8 };
const clip = (over: Partial<EditClip> = {}): EditClip => ({
  clip_id: "a",
  unit_id: "U1",
  in_s: 0,
  out_s: null,
  trimmed_against_version: null,
  transition_to_next: "cut",
  ...over,
});

describe("edit-timeline utils", () => {
  it("uses the whole unit when the trim was cut against another version", () => {
    const stale = clip({ in_s: 1, out_s: 3, trimmed_against_version: 1 });
    expect(isTrimStale(stale, unit)).toBe(true);
    expect(effectiveRange(stale, unit)).toEqual([0, 8]);
    expect(clipLength(clip({ in_s: 1, out_s: 3, trimmed_against_version: 2 }), unit)).toBe(2);
  });

  it("records the current version when trimming and clears it for a whole clip", () => {
    const trimmed = updateClip([clip()], "a", { in_s: 1, out_s: 4 }, unit);
    expect(trimmed[0].trimmed_against_version).toBe(2);
    const whole = updateClip(trimmed, "a", { in_s: 0, out_s: null }, unit);
    expect(whole[0].trimmed_against_version).toBeNull();
  });

  it("splits a clip and keeps the transition on the second half", () => {
    const parts = splitClip([clip({ transition_to_next: "fade" })], "a", 3, unit);
    expect(parts).toHaveLength(2);
    expect(parts[0]).toMatchObject({ in_s: 0, out_s: 3, transition_to_next: "cut" });
    expect(parts[1]).toMatchObject({ in_s: 3, out_s: 8, transition_to_next: "fade" });
    expect(parts[0].clip_id).not.toBe(parts[1].clip_id);
    expect(splitClip([clip()], "a", 0.05, unit)).toHaveLength(1);
  });

  it("moves clips within bounds and appends whole-unit clips", () => {
    const list = [clip({ clip_id: "a" }), clip({ clip_id: "b" })];
    expect(moveClip(list, "a", 1).map((c) => c.clip_id)).toEqual(["b", "a"]);
    expect(moveClip(list, "a", -1)).toBe(list);
    const added = addUnitClip([], "U9");
    expect(added[0]).toMatchObject({ unit_id: "U9", in_s: 0, out_s: null });
  });

  it("maps timeline time onto clips across boundaries and clamps to the ends", () => {
    const units = new Map<string, EditUnit>([["U1", unit]]);
    const clips = [clip({ clip_id: "a", out_s: 3 }), clip({ clip_id: "b" })];
    expect(clipStarts(clips, units)).toEqual([0, 3]);
    expect(locateTime(clips, units, 1)).toEqual({ index: 0, offset: 1 });
    expect(locateTime(clips, units, 3)).toEqual({ index: 1, offset: 0 });
    expect(locateTime(clips, units, 99)).toEqual({ index: 1, offset: 8 });
    expect(locateTime([], units, 5)).toEqual({ index: 0, offset: 0 });
  });
});

describe("dubBlocks", () => {
  const dubbed: EditUnit = {
    ...unit,
    dub_lines: [
      { index: 0, text_vi: "Xin chào", start_s: 1, duration_s: 1.5, audio_url: "/a0.wav" },
      { index: 1, text_vi: "Tạm biệt", start_s: 6, duration_s: 1, audio_url: "/a1.wav" },
    ],
  };
  const units = new Map([["U1", dubbed]]);

  it("places every line of a whole-unit clip at its own time", () => {
    const clips = [clip()];

    const blocks = dubBlocks(clips, units, clipStarts(clips, units));

    expect(blocks.map((b) => [b.index, b.start, b.duration, b.offsetInClip])).toEqual([
      [0, 1, 1.5, 1],
      [1, 6, 1, 6],
    ]);
  });

  it("shifts lines by where the clip starts on the timeline and by its in-point", () => {
    // 第二个片段取单元 5–8 秒，排在一个 4 秒的片段之后
    const clips = [clip({ clip_id: "a", in_s: 0, out_s: 4, trimmed_against_version: 2 }), clip({ clip_id: "b", in_s: 5, out_s: 8, trimmed_against_version: 2 })];

    const blocks = dubBlocks(clips, units, clipStarts(clips, units));

    const second = blocks.find((b) => b.clipId === "b");
    expect(second).toMatchObject({ index: 1, start: 4 + 1, offsetInClip: 1, sourceStart: 6, sourceEnd: 7 });
  });

  it("keeps only the part of a line that lies inside the trimmed range", () => {
    // 单元 1–2.5 秒的台词，片段只用 2.0–8 秒：只剩后 0.5 秒，音频要从文件的 1.0 秒处开始播
    const clips = [clip({ in_s: 2, out_s: 8, trimmed_against_version: 2 })];

    const [block] = dubBlocks(clips, units, clipStarts(clips, units));

    expect(block).toMatchObject({ index: 0, start: 0, duration: 0.5, sourceStart: 2, sourceEnd: 2.5, fileOffset: 1 });
  });

  it("drops lines outside the clip or reduced to a sliver", () => {
    const clips = [clip({ in_s: 2.46, out_s: 5.9, trimmed_against_version: 2 })];

    expect(dubBlocks(clips, units, clipStarts(clips, units))).toEqual([]);
  });

  it("ignores units without voice-over", () => {
    const plain = new Map([["U1", unit]]);
    const clips = [clip()];

    expect(dubBlocks(clips, plain, clipStarts(clips, plain))).toEqual([]);
  });

  it("uses the whole unit when the trim is stale, like the renderer", () => {
    const clips = [clip({ in_s: 6.5, out_s: 7, trimmed_against_version: 1 })];

    const blocks = dubBlocks(clips, units, clipStarts(clips, units));

    expect(blocks.map((b) => b.index)).toEqual([0, 1]);
  });
});
