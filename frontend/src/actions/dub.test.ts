import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { createVoiceover, notifyDubTiming } from "@/actions/dub";
import { useAppStore } from "@/stores/app-store";

function lastToast(): string {
  return JSON.stringify(useAppStore.getState().toast ?? "");
}

describe("createVoiceover", () => {
  beforeEach(() => {
    useAppStore.setState({ toast: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("hands the chosen videos to the server pipeline in one request", async () => {
    const auto = vi.spyOn(API, "autoDubVoiceover").mockResolvedValue({
      success: true,
      task_ids: ["a", "b"],
      unit_ids: ["E1U1", "E1U2"],
      skipped: [],
    });

    const outcome = await createVoiceover("proj", 1, ["E1U1", "E1U2"]);

    expect(outcome).toBe("queued");
    expect(auto).toHaveBeenCalledTimes(1);
    expect(auto).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1", "E1U2"] });
    expect(lastToast()).toMatch(/2/);
  });

  it("says which videos were skipped because they have no video yet", async () => {
    vi.spyOn(API, "autoDubVoiceover").mockResolvedValue({
      success: true,
      task_ids: ["a"],
      unit_ids: ["E1U1"],
      skipped: [{ unit_id: "E1U2", reason: "no_video" }],
    });

    expect(await createVoiceover("proj", 1, ["E1U1", "E1U2"])).toBe("queued");
    expect(lastToast()).toMatch(/1/);
  });

  it("reports nothing to do when the server queued no video", async () => {
    vi.spyOn(API, "autoDubVoiceover").mockResolvedValue({ success: true, task_ids: [], unit_ids: [], skipped: [] });

    expect(await createVoiceover("proj", 1, ["E1U1"])).toBe("nothing_to_do");
  });

  it("does not call the server for an empty selection", async () => {
    const auto = vi.spyOn(API, "autoDubVoiceover");

    expect(await createVoiceover("proj", 1, [])).toBe("nothing_to_do");
    expect(auto).not.toHaveBeenCalled();
  });

  it("lets a server refusal reach the caller", async () => {
    vi.spyOn(API, "autoDubVoiceover").mockRejectedValue(new Error("VieNeu is off"));

    await expect(createVoiceover("proj", 1, ["E1U1"])).rejects.toThrow("VieNeu is off");
  });
});

describe("notifyDubTiming", () => {
  beforeEach(() => {
    useAppStore.setState({ toast: null });
  });

  it("counts the lines whose start time was found", () => {
    const detected = notifyDubTiming([
      { unit_id: "E1U1", status: "detected", detected: 2, total: 3 },
      { unit_id: "E1U2", status: "no_match", detected: 0, total: 2 },
    ]);

    expect(detected).toBe(2);
    expect(lastToast()).toMatch(/2/);
  });

  it("tells apart having no video from hearing nothing usable", () => {
    expect(notifyDubTiming([{ unit_id: "E1U1", status: "no_video", detected: 0, total: 2 }])).toBe(0);
    const noVideo = lastToast();
    useAppStore.setState({ toast: null });
    expect(notifyDubTiming([{ unit_id: "E1U1", status: "no_speech", detected: 0, total: 2 }])).toBe(0);
    expect(lastToast()).not.toBe(noVideo);
  });
});
