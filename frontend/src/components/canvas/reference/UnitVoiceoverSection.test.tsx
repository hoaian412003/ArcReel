import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { API } from "@/api";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { UnitVoiceoverSection } from "./UnitVoiceoverSection";
import type { UnitDub } from "@/types";

function dub(overrides: Partial<UnitDub> = {}): UnitDub {
  return {
    unit_id: "E1U1",
    duration_seconds: 8,
    overflow_seconds: 0,
    video_path: "reference_videos/E1U1.mp4",
    original_volume: 0.5,
    lines: [
      {
        index: 0,
        speaker: "张三",
        gender: "male",
        text_vi: "Xin chào",
        source_text: "你好",
        start_seconds: 0,
        start_override: null,
        start_source: "detected",
        delayed_seconds: 0,
        duration_seconds: 1.5,
        estimated: false,
        audio_path: "audio/a0.wav",
        stale: false,
      },
      {
        index: 1,
        speaker: null,
        gender: "female",
        text_vi: "Tạm biệt",
        source_text: "再见",
        start_seconds: 1.7,
        start_override: null,
        start_source: "estimated",
        delayed_seconds: 0,
        duration_seconds: 1,
        estimated: true,
        audio_path: null,
        stale: true,
      },
    ],
    ...overrides,
  };
}

describe("UnitVoiceoverSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useTasksStore.setState({ tasks: [] });
    useReferenceVideoStore.setState({ unitsByEpisode: {} });
  });

  const mount = () =>
    render(<UnitVoiceoverSection projectName="proj" episode={1} unitId="E1U1" refreshKey="k" />);

  it("lists start time, gender and Vietnamese text per line, and flags stale or ungenerated lines", async () => {
    vi.spyOn(API, "getUnitDub").mockResolvedValue(dub());
    mount();

    expect(await screen.findByDisplayValue("Xin chào")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Tạm biệt")).toBeInTheDocument();
    expect(screen.getByLabelText(/Start time of line 2|第 2 行开始时间/)).toHaveValue(1.7);
    expect(screen.getByLabelText(/Gender of line 2|第 2 行性别/)).toHaveValue("female");
    expect(screen.getByText(/Source changed|原文已改/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Play line|试听第/ })).toHaveLength(1);
  });

  it("saves edited text, gender and start time as one full replacement", async () => {
    vi.spyOn(API, "getUnitDub").mockResolvedValue(dub());
    const patch = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { unit_id: "E1U1" } as never, unit_capability: {} as never });
    mount();

    fireEvent.change(await screen.findByLabelText(/Voice-over text of line 1|第 1 行配音台词/), {
      target: { value: "Chào bạn" },
    });
    fireEvent.change(screen.getByLabelText(/Gender of line 1|第 1 行性别/), { target: { value: "female" } });
    fireEvent.change(screen.getByLabelText(/Start time of line 2|第 2 行开始时间/), { target: { value: "3.5" } });
    fireEvent.click(screen.getByRole("button", { name: /Save lines|保存台词/ }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith("proj", 1, "E1U1", {
      dub_lines: [
        { index: 0, speaker: "张三", gender: "female", text_vi: "Chào bạn", start_seconds: null },
        { index: 1, speaker: null, gender: "female", text_vi: "Tạm biệt", start_seconds: 3.5 },
      ],
    });
  });

  it("clearing the start time removes the override", async () => {
    const withOverride = dub();
    withOverride.lines[1] = { ...withOverride.lines[1], start_override: 4, start_seconds: 4 };
    vi.spyOn(API, "getUnitDub").mockResolvedValue(withOverride);
    const patch = vi
      .spyOn(API, "patchReferenceVideoUnit")
      .mockResolvedValue({ unit: { unit_id: "E1U1" } as never, unit_capability: {} as never });
    mount();

    fireEvent.change(await screen.findByLabelText(/Start time of line 2|第 2 行开始时间/), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Save lines|保存台词/ }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0][3].dub_lines?.[1].start_seconds).toBeNull();
  });

  it("labels where each start time came from", async () => {
    vi.spyOn(API, "getUnitDub").mockResolvedValue(dub());
    mount();

    await screen.findByDisplayValue("Xin chào");
    expect(screen.getByText(/^(from video|成片检测)$/)).toBeInTheDocument();
    expect(screen.getByText(/^(estimated|估算)$/)).toBeInTheDocument();
  });

  it("detects start times from the video for this unit and refreshes the table", async () => {
    const getDub = vi.spyOn(API, "getUnitDub").mockResolvedValueOnce(dub());
    const detect = vi.spyOn(API, "detectDubTiming").mockResolvedValue({
      results: [{ unit_id: "E1U1", status: "detected", detected: 2, total: 2 }],
    });
    const after = dub();
    after.lines[1] = { ...after.lines[1], start_seconds: 5.2, start_source: "detected" };
    getDub.mockResolvedValue(after);
    mount();

    fireEvent.click(await screen.findByRole("button", { name: /Detect start times|检测开始时间/ }));

    await waitFor(() => expect(detect).toHaveBeenCalledWith("proj", 1, ["E1U1"]));
    await waitFor(() => expect(screen.getByLabelText(/Start time of line 2|第 2 行开始时间/)).toHaveValue(5.2));
  });

  it("offers detection only when the unit has a video", async () => {
    vi.spyOn(API, "getUnitDub").mockResolvedValue(dub({ video_path: null }));
    mount();

    await screen.findByDisplayValue("Xin chào");
    expect(screen.queryByRole("button", { name: /Detect start times|检测开始时间/ })).not.toBeInTheDocument();
  });

  it("says which line is pushed late by the one before it, and by how much", async () => {
    const late = dub();
    late.lines[1] = { ...late.lines[1], start_source: "detected", delayed_seconds: 1.04 };
    vi.spyOn(API, "getUnitDub").mockResolvedValue(late);
    mount();

    await screen.findByDisplayValue("Tạm biệt");
    expect(screen.getByText(/1\.0 s later than in the video: line 1|比成片晚 1\.0 秒：第 1 行/)).toBeInTheDocument();
  });

  it("stays quiet about tiny delays", async () => {
    const barely = dub();
    barely.lines[1] = { ...barely.lines[1], delayed_seconds: 0.1 };
    vi.spyOn(API, "getUnitDub").mockResolvedValue(barely);
    mount();

    await screen.findByDisplayValue("Tạm biệt");
    expect(screen.queryByText(/later than in the video|比成片晚/)).not.toBeInTheDocument();
  });

  it("warns when the voice-over runs past the unit", async () => {
    vi.spyOn(API, "getUnitDub").mockResolvedValue(dub({ overflow_seconds: 2.34 }));
    mount();

    expect(await screen.findByText(/2\.3/)).toBeInTheDocument();
  });

  it("shows an empty hint when the unit has no lines yet", async () => {
    vi.spyOn(API, "getUnitDub").mockResolvedValue(dub({ lines: [] }));
    mount();

    expect(await screen.findByText(/No Vietnamese lines yet|还没有越南语台词/)).toBeInTheDocument();
  });
});
