import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API, ApiRequestError } from "@/api";
import type { EditTimelineDetail } from "@/types/edit-timeline";
import { EditTimelineCanvas } from "./EditTimelineCanvas";

function detail(revision = 1, overrides: Partial<EditTimelineDetail> = {}): EditTimelineDetail {
  const clip = (id: string, unit: string) => ({
    clip_id: id,
    unit_id: unit,
    in_s: 0,
    out_s: null,
    trimmed_against_version: null,
    transition_to_next: "cut" as const,
  });
  return {
    timeline: {
      timeline_id: "t1",
      episode: 1,
      name: "Cut 1",
      revisions: Array.from({ length: revision }, (_, i) => ({
        revision: i + 1,
        parent_revision: i === 0 ? null : i,
        author: "user",
        summary: "edit",
        created_at: "x",
        clips: [clip("c1", "U1")],
      })),
    },
    diagnostics: {
      deleted_unit_clips: [],
      unused_unit_ids: ["U2"],
      stale_trim_clips: [],
      missing_video_clips: [],
    },
    units: [
      { unit_id: "U1", video_url: "/u1.mp4", version: 1, duration_s: 6 },
      { unit_id: "U2", video_url: "/u2.mp4", version: 1, duration_s: 4 },
    ],
    transitions: ["cut", "fade", "dissolve", "wipe"],
    final_cut: null,
    render: null,
    ...overrides,
  };
}

function renderCanvas() {
  const { hook } = memoryLocation({ path: "/episodes/1/edit" });
  return render(
    <Router hook={hook}>
      <EditTimelineCanvas projectName="demo" episode={1} />
    </Router>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(API, "listEditTimelines").mockResolvedValue({
    timelines: [
      { timeline_id: "t1", name: "Cut 1", latest_revision: 1, clip_count: 1, updated_at: "x" },
    ],
  });
  vi.spyOn(API, "getEditTimeline").mockResolvedValue(detail());
});

describe("EditTimelineCanvas", () => {
  it("offers to create a timeline from script order when the episode has none", async () => {
    vi.spyOn(API, "listEditTimelines").mockResolvedValue({ timelines: [] });
    const create = vi.spyOn(API, "createEditTimeline").mockResolvedValue(detail());
    renderCanvas();

    fireEvent.click(await screen.findByRole("button", { name: /create from script order|按脚本顺序创建/i }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("demo", 1, expect.objectContaining({ source: "mechanical" })),
    );
  });

  it("saves an added unit as a new revision against the latest parent revision", async () => {
    const update = vi.spyOn(API, "updateEditTimeline").mockResolvedValue(detail(2));
    renderCanvas();

    fireEvent.click(await screen.findByRole("button", { name: /U2/ }));
    fireEvent.click(screen.getByRole("button", { name: /save revision|保存修订/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    const [, , id, payload] = update.mock.calls[0];
    expect(id).toBe("t1");
    expect(payload.parent_revision).toBe(1);
    expect(payload.clips.map((c) => c.unit_id)).toEqual(["U1", "U2"]);
  });

  it("reloads the latest revision when the server reports a revision conflict", async () => {
    vi.spyOn(API, "updateEditTimeline").mockRejectedValue(new ApiRequestError("conflict", undefined, 409));
    const get = vi.mocked(API.getEditTimeline);
    renderCanvas();

    fireEvent.click(await screen.findByRole("button", { name: /U2/ }));
    fireEvent.click(screen.getByRole("button", { name: /save revision|保存修订/i }));

    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  });

  it("exposes one scrubber spanning the whole timeline and follows added clips", async () => {
    renderCanvas();

    const scrubber = (await screen.findByRole("slider")) as HTMLInputElement;
    expect(scrubber.max).toBe("6");

    fireEvent.click(screen.getByRole("button", { name: /U2/ }));
    expect((screen.getByRole("slider") as HTMLInputElement).max).toBe("10");
  });

  it("keeps playing into the next clip when a clip runs to the end of its file", async () => {
    const { container } = renderCanvas();
    fireEvent.click(await screen.findByRole("button", { name: /U2/ }));

    const video = container.querySelector("video[src='/u1.mp4']:not([hidden])") as HTMLVideoElement;
    expect(video).not.toBeNull();
    // 浏览器在文件末尾先触发 pause 再触发 ended。
    Object.defineProperty(video, "ended", { value: true, configurable: true });
    fireEvent.play(video);
    fireEvent.pause(video);
    fireEvent.ended(video);

    await waitFor(() =>
      expect(container.querySelector("video[src='/u2.mp4']:not([hidden])")).not.toBeNull(),
    );
  });

  describe("voice-over", () => {
    function withDub(voiceOver: boolean): EditTimelineDetail {
      const base = detail();
      return {
        ...base,
        voice_over_available: true,
        original_volume: 0.5,
        timeline: {
          ...base.timeline,
          revisions: base.timeline.revisions.map((r) => ({ ...r, voice_over: voiceOver })),
        },
        units: [
          {
            ...base.units[0],
            dub_lines: [{ index: 0, text_vi: "Xin chào", start_s: 1, duration_s: 2, audio_url: "/a0.wav" }],
          },
          base.units[1],
        ],
      };
    }

    it("has no switch and no voice-over lane when the project has no voice-over audio", async () => {
      renderCanvas();

      await screen.findByRole("button", { name: /U2/ });
      expect(screen.queryByRole("checkbox", { name: /voice-over|配音/i })).not.toBeInTheDocument();
      expect(screen.queryByTestId("dub-block")).not.toBeInTheDocument();
    });

    it("shows the switch and a block for each voice-over line on the track", async () => {
      vi.mocked(API.getEditTimeline).mockResolvedValue(withDub(true));
      renderCanvas();

      expect(await screen.findByRole("checkbox", { name: /voice-over|配音/i })).toBeChecked();
      const blocks = screen.getAllByTestId("dub-block");
      expect(blocks).toHaveLength(1);
      expect(blocks[0]).toHaveAttribute("title", "Xin chào");
    });

    it("plays the preview's original sound at half volume while the voice-over is on", async () => {
      vi.mocked(API.getEditTimeline).mockResolvedValue(withDub(true));
      const { container } = renderCanvas();

      await screen.findByRole("checkbox", { name: /voice-over|配音/i });

      const video = container.querySelector("video[src='/u1.mp4']:not([hidden])") as HTMLVideoElement;
      await waitFor(() => expect(video.volume).toBe(0.5));
      expect(container.querySelectorAll("audio[src='/a0.wav']")).toHaveLength(1);
    });

    it("lowers the original on a clip that has no voice-over lines too, like the final cut does", async () => {
      const base = withDub(true);
      const silentFirst = {
        ...base,
        timeline: {
          ...base.timeline,
          revisions: base.timeline.revisions.map((r) => ({
            ...r,
            clips: [
              { clip_id: "c0", unit_id: "U2", in_s: 0, out_s: null, trimmed_against_version: null, transition_to_next: "cut" as const },
              ...r.clips,
            ],
          })),
        },
      };
      vi.mocked(API.getEditTimeline).mockResolvedValue(silentFirst);
      const { container } = renderCanvas();
      await screen.findByRole("checkbox", { name: /voice-over|配音/i });

      // 第一个片段是没有配音台词的 U2
      const video = container.querySelector("video[src='/u2.mp4']:not([hidden])") as HTMLVideoElement;
      expect(video).not.toBeNull();
      await waitFor(() => expect(video.volume).toBe(0.5));
    });

    it("keeps the original at full volume and plays no voice-over audio when switched off", async () => {
      vi.mocked(API.getEditTimeline).mockResolvedValue(withDub(true));
      const { container } = renderCanvas();
      const toggle = await screen.findByRole("checkbox", { name: /voice-over|配音/i });

      fireEvent.click(toggle);

      const video = container.querySelector("video[src='/u1.mp4']:not([hidden])") as HTMLVideoElement;
      await waitFor(() => expect(video.volume).toBe(1));
      expect(container.querySelectorAll("audio[src='/a0.wav']")).toHaveLength(0);
    });

    it("treats the switch as an unsaved change and saves it with the revision", async () => {
      vi.mocked(API.getEditTimeline).mockResolvedValue(withDub(true));
      const update = vi.spyOn(API, "updateEditTimeline").mockResolvedValue(withDub(false));
      renderCanvas();
      const toggle = await screen.findByRole("checkbox", { name: /voice-over|配音/i });
      const save = screen.getByRole("button", { name: /save revision|保存修订/i });
      expect(save).toBeDisabled();

      fireEvent.click(toggle);
      expect(save).toBeEnabled();
      fireEvent.click(save);

      await waitFor(() => expect(update).toHaveBeenCalled());
      expect(update.mock.calls[0][3]).toMatchObject({ voice_over: false, parent_revision: 1 });
    });

    it("discarding changes puts the switch back where it was saved", async () => {
      vi.mocked(API.getEditTimeline).mockResolvedValue(withDub(true));
      renderCanvas();
      const toggle = await screen.findByRole("checkbox", { name: /voice-over|配音/i });

      fireEvent.click(toggle);
      expect(toggle).not.toBeChecked();
      fireEvent.click(screen.getByRole("button", { name: /discard changes|放弃改动/i }));

      expect(toggle).toBeChecked();
    });
  });
});
