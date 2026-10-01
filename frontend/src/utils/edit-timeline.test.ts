import { describe, expect, it } from "vitest";
import type { EditClip, EditUnit } from "@/types/edit-timeline";
import {
  addUnitClip,
  clipLength,
  clipStarts,
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
