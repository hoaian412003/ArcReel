import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { createVoiceover } from "@/actions/dub";
import { useAppStore } from "@/stores/app-store";
import { referenceVideoCacheKey, useReferenceVideoStore } from "@/stores/reference-video-store";
import type { DubLine, ReferenceVideoUnit } from "@/types";

const LINE: DubLine = {
  index: 0,
  speaker: null,
  gender: "male",
  source_text: "你好",
  text_vi: "Xin chào",
  start_seconds: null,
};

function unit(id: string, dubLines?: DubLine[]): ReferenceVideoUnit {
  return { unit_id: id, text: "{你好}", duration_seconds: 4, transition_to_next: "cut", note: null, dub_lines: dubLines };
}

function seed(units: ReferenceVideoUnit[]) {
  useReferenceVideoStore.setState({ unitsByEpisode: { [referenceVideoCacheKey("proj", 1)]: units } });
}

describe("createVoiceover", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.setState({ toast: null });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("goes straight to synthesis when every unit already has lines", async () => {
    seed([unit("E1U1", [LINE]), unit("E1U2", [LINE])]);
    const lines = vi.spyOn(API, "generateDubLines");
    const synth = vi
      .spyOn(API, "synthesizeDub")
      .mockResolvedValue({ success: true, task_ids: ["t1", "t2"], unit_ids: ["E1U1", "E1U2"] });

    const outcome = await createVoiceover("proj", 1, ["E1U1", "E1U2"]);

    expect(outcome).toBe("queued");
    expect(lines).not.toHaveBeenCalled();
    expect(synth).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1", "E1U2"] });
  });

  it("writes the missing lines first, waits for them, then synthesizes only ready units", async () => {
    seed([unit("E1U1", [LINE]), unit("E1U2")]);
    const lines = vi
      .spyOn(API, "generateDubLines")
      .mockResolvedValue({ task_id: "t", task_ids: ["t"], deduped: false, unit_ids: ["E1U2"] });
    const list = vi.spyOn(API, "listReferenceVideoUnits");
    list.mockResolvedValueOnce({ units: [unit("E1U1", [LINE]), unit("E1U2")], unit_capabilities: {} });
    list.mockResolvedValueOnce({ units: [unit("E1U1", [LINE]), unit("E1U2", [LINE])], unit_capabilities: {} });
    const synth = vi
      .spyOn(API, "synthesizeDub")
      .mockResolvedValue({ success: true, task_ids: ["t1", "t2"], unit_ids: ["E1U1", "E1U2"] });

    const pending = createVoiceover("proj", 1, ["E1U1", "E1U2"]);
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    const outcome = await pending;

    expect(outcome).toBe("queued");
    expect(lines).toHaveBeenCalledWith("proj", 1, ["E1U2"]);
    expect(list).toHaveBeenCalledTimes(2);
    expect(synth).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1", "E1U2"] });
  });

  it("gives up with a message when the lines never arrive", async () => {
    seed([unit("E1U1")]);
    vi.spyOn(API, "generateDubLines").mockResolvedValue({
      task_id: "t",
      task_ids: ["t"],
      deduped: false,
      unit_ids: ["E1U1"],
    });
    vi.spyOn(API, "listReferenceVideoUnits").mockResolvedValue({ units: [unit("E1U1")], unit_capabilities: {} });
    const synth = vi.spyOn(API, "synthesizeDub");

    const pending = createVoiceover("proj", 1, ["E1U1"]);
    await vi.advanceTimersByTimeAsync(3000 * 101);
    const outcome = await pending;

    expect(outcome).toBe("lines_timeout");
    expect(synth).not.toHaveBeenCalled();
    expect(useAppStore.getState().toast?.tone ?? useAppStore.getState().toast).toBeTruthy();
  });

  it("does nothing for an empty selection", async () => {
    const synth = vi.spyOn(API, "synthesizeDub");

    expect(await createVoiceover("proj", 1, [])).toBe("nothing_to_do");
    expect(synth).not.toHaveBeenCalled();
  });
});
