import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { DubPreviewPlayer, type DubPreviewClip } from "./DubPreviewPlayer";

const CLIPS: DubPreviewClip[] = [
  { index: 0, url: "/a0.wav", startSeconds: 1, durationSeconds: 2, text: "Xin chào" },
  { index: 1, url: "/a1.wav", startSeconds: 5, durationSeconds: 1, text: "Tạm biệt" },
];

function setVideoState(video: HTMLVideoElement, state: { currentTime: number; paused: boolean }) {
  Object.defineProperty(video, "currentTime", { configurable: true, writable: true, value: state.currentTime });
  Object.defineProperty(video, "paused", { configurable: true, value: state.paused });
}

describe("DubPreviewPlayer", () => {
  const play = vi.fn().mockResolvedValue(undefined);
  const pause = vi.fn();

  beforeEach(() => {
    play.mockClear();
    pause.mockClear();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(play);
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(pause);
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("sets the original video to the given volume and renders one audio element per clip", () => {
    const { container } = render(<DubPreviewPlayer videoUrl="/v.mp4" clips={CLIPS} originalVolume={0.5} />);
    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video.volume).toBe(0.5);
    expect(container.querySelectorAll("audio")).toHaveLength(2);
    expect(screen.getByText(/50%/)).toBeInTheDocument();
  });

  it("plays the original at 30% unless told otherwise, and the voice-over clips at full volume", () => {
    const { container } = render(<DubPreviewPlayer videoUrl="/v.mp4" clips={CLIPS} />);
    const video = container.querySelector("video") as HTMLVideoElement;
    expect(video.volume).toBe(0.3);
    expect(screen.getByText(/30%/)).toBeInTheDocument();
    for (const audio of Array.from(container.querySelectorAll("audio"))) expect(audio.volume).toBe(1);
  });

  it("plays only the clip covering the current time and shows its subtitle", () => {
    const { container } = render(<DubPreviewPlayer videoUrl="/v.mp4" clips={CLIPS} />);
    const video = container.querySelector("video") as HTMLVideoElement;
    setVideoState(video, { currentTime: 1.5, paused: false });
    act(() => {
      fireEvent.seeked(video);
    });
    expect(play).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("Xin chào");
  });

  it("pauses clip audio and still shows the subtitle at a paused position", () => {
    const { container } = render(<DubPreviewPlayer videoUrl="/v.mp4" clips={CLIPS} />);
    const video = container.querySelector("video") as HTMLVideoElement;
    setVideoState(video, { currentTime: 5.2, paused: true });
    act(() => {
      fireEvent.pause(video);
    });
    expect(play).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Tạm biệt");
  });

  it("shows no subtitle between clips", () => {
    const { container } = render(<DubPreviewPlayer videoUrl="/v.mp4" clips={CLIPS} />);
    const video = container.querySelector("video") as HTMLVideoElement;
    setVideoState(video, { currentTime: 4, paused: true });
    act(() => {
      fireEvent.seeked(video);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
