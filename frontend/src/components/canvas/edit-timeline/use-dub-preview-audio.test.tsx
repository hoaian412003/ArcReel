import { renderHook } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDubPreviewAudio, type PreviewDubLine } from "./use-dub-preview-audio";

const LINES: PreviewDubLine[] = [
  { key: "a", sourceStart: 1, sourceEnd: 3, fileOffset: 0 },
  { key: "b", sourceStart: 5, sourceEnd: 6, fileOffset: 0.4 },
];

function makeVideo(currentTime: number, paused = false): HTMLVideoElement {
  const video = document.createElement("video");
  Object.defineProperty(video, "currentTime", { configurable: true, writable: true, value: currentTime });
  Object.defineProperty(video, "paused", { configurable: true, writable: true, value: paused });
  Object.defineProperty(video, "ended", { configurable: true, value: false });
  return video;
}

function makeAudio(): HTMLAudioElement {
  const audio = document.createElement("audio");
  Object.defineProperty(audio, "paused", { configurable: true, writable: true, value: true });
  Object.defineProperty(audio, "currentTime", { configurable: true, writable: true, value: 0 });
  vi.spyOn(audio, "play").mockImplementation(() => {
    Object.defineProperty(audio, "paused", { configurable: true, writable: true, value: false });
    return Promise.resolve();
  });
  vi.spyOn(audio, "pause").mockImplementation(() => {
    Object.defineProperty(audio, "paused", { configurable: true, writable: true, value: true });
  });
  return audio;
}

describe("useDubPreviewAudio", () => {
  beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function setup(
    video: HTMLVideoElement,
    options: { enabled: boolean; playing: boolean; lowerOriginal?: boolean },
    lines = LINES,
  ) {
    const ref = createRef<HTMLVideoElement>() as { current: HTMLVideoElement | null };
    ref.current = video;
    const hook = renderHook(
      (props: { enabled: boolean; playing: boolean; lowerOriginal?: boolean }) =>
        useDubPreviewAudio(ref, lines, { lowerOriginal: true, ...props, originalVolume: 0.5 }),
      { initialProps: options },
    );
    const a = makeAudio();
    const b = makeAudio();
    hook.result.current("a", a);
    hook.result.current("b", b);
    return { ...hook, a, b, video };
  }

  it("lowers the original to the given volume while there is voice-over to play, and restores it when off", () => {
    const video = makeVideo(0, true);
    const { rerender } = setup(video, { enabled: true, playing: false });
    expect(video.volume).toBe(0.5);

    rerender({ enabled: false, playing: false });
    expect(video.volume).toBe(1);
  });

  it("lowers the original even on a clip that has no voice-over lines, so every clip is as loud as in the final cut", () => {
    const video = makeVideo(0, true);
    setup(video, { enabled: true, playing: false, lowerOriginal: true }, []);

    expect(video.volume).toBe(0.5);
  });

  it("leaves the original alone when there is no voice-over anywhere in the timeline", () => {
    const video = makeVideo(0, true);
    setup(video, { enabled: true, playing: false, lowerOriginal: false }, []);

    expect(video.volume).toBe(1);
  });

  it("plays the line the video is inside of, from the matching spot in the audio file", () => {
    const video = makeVideo(2, false);
    const { a, b } = setup(video, { enabled: true, playing: true });

    video.dispatchEvent(new Event("seeked"));

    expect(a.play).toHaveBeenCalled();
    expect(a.currentTime).toBeCloseTo(1); // 2 - 1 = 播到第 1 秒
    expect(b.play).not.toHaveBeenCalled();
  });

  it("starts a line partway through its file when the clip's in-point cut its head off", () => {
    const video = makeVideo(5.2, false);
    const { b } = setup(video, { enabled: true, playing: true });

    video.dispatchEvent(new Event("seeked"));

    expect(b.currentTime).toBeCloseTo(0.4 + 0.2);
  });

  it("stops everything when the video pauses or leaves the line", () => {
    const video = makeVideo(2, false);
    const { a } = setup(video, { enabled: true, playing: true });
    video.dispatchEvent(new Event("seeked"));
    expect(a.paused).toBe(false);

    (video as unknown as { paused: boolean }).paused = true;
    video.dispatchEvent(new Event("pause"));
    expect(a.pause).toHaveBeenCalled();

    (video as unknown as { paused: boolean }).paused = false;
    video.currentTime = 4;
    video.dispatchEvent(new Event("seeked"));
    expect(a.paused).toBe(true);
  });

  it("stays silent when the switch is off", () => {
    const video = makeVideo(2, false);
    const { a } = setup(video, { enabled: false, playing: true });

    video.dispatchEvent(new Event("seeked"));

    expect(a.play).not.toHaveBeenCalled();
  });

  it("pauses a line's audio when its element goes away (the clip changed)", () => {
    const video = makeVideo(2, false);
    const { a, result } = setup(video, { enabled: true, playing: true });
    video.dispatchEvent(new Event("seeked"));

    result.current("a", null);

    expect(a.pause).toHaveBeenCalled();
  });
});
