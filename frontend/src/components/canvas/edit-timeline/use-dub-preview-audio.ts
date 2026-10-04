import { useCallback, useEffect, useRef, type RefObject } from "react";

/** 当前预览片段里的一段配音（时刻都是源视频时间）。 */
export interface PreviewDubLine {
  key: string;
  sourceStart: number;
  sourceEnd: number;
  /** 配音音频文件里要从哪一秒开始播。 */
  fileOffset: number;
}

interface Options {
  /** 配音开关；关闭时一律不出声。 */
  enabled: boolean;
  /** 开启配音时原视频的音量（0–1）。 */
  originalVolume: number;
  /**
   * 是否压低原声。与「当前片段有没有配音」无关：成片渲染时只要整条时间线里有配音，所有片段的原声
   * 都压低，预览必须一致，否则没有台词的片段会比有配音的片段响一倍。
   */
  lowerOriginal: boolean;
  /** 预览正在播放；用来开关逐帧对齐。 */
  playing: boolean;
}

/** 配音与视频时钟允许的最大偏差（秒），超出才拉回。 */
const MAX_DRIFT_SECONDS = 0.15;

/**
 * 预览里的配音：跟着当前片段的视频时钟播放（播放、暂停、拖动、倍速都跟随视频），
 * 开启且当前片段有配音时把原声压到 ``originalVolume``。返回给每个 `<audio>` 用的登记函数。
 */
export function useDubPreviewAudio(
  videoRef: RefObject<HTMLVideoElement | null>,
  lines: readonly PreviewDubLine[],
  { enabled, originalVolume, playing, lowerOriginal }: Options,
): (key: string, element: HTMLAudioElement | null) => void {
  const audios = useRef(new Map<string, HTMLAudioElement>());
  const active = enabled && lines.length > 0;

  const register = useCallback((key: string, element: HTMLAudioElement | null) => {
    if (element) {
      audios.current.set(key, element);
    } else {
      audios.current.get(key)?.pause();
      audios.current.delete(key);
    }
  }, []);

  const synchronize = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const running = active && !video.paused && !video.ended;
    for (const line of lines) {
      const audio = audios.current.get(line.key);
      if (!audio) continue;
      const inside = video.currentTime >= line.sourceStart && video.currentTime < line.sourceEnd;
      if (!running || !inside) {
        if (!audio.paused) audio.pause();
        continue;
      }
      const wanted = line.fileOffset + (video.currentTime - line.sourceStart);
      audio.playbackRate = video.playbackRate;
      if (Math.abs(audio.currentTime - wanted) > MAX_DRIFT_SECONDS) audio.currentTime = wanted;
      if (audio.paused) void audio.play().catch(() => undefined);
    }
  }, [active, lines, videoRef]);

  // 原声音量：配音开着且整条时间线里有配音时，每个片段都压低；否则恢复
  const lower = enabled && lowerOriginal;
  useEffect(() => {
    const video = videoRef.current;
    if (video) video.volume = lower ? originalVolume : 1;
  }, [lower, originalVolume, videoRef, lines]);

  // 播放期间逐帧对齐（timeupdate 只有约 4Hz，配音的起止会被越过）
  useEffect(() => {
    if (!playing) {
      synchronize();
      return;
    }
    let frame = 0;
    const tick = () => {
      synchronize();
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, synchronize]);

  // 拖动、暂停、换速时立刻对齐一次
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const events = ["seeked", "pause", "ratechange"] as const;
    for (const event of events) video.addEventListener(event, synchronize);
    return () => {
      for (const event of events) video.removeEventListener(event, synchronize);
    };
  }, [synchronize, videoRef, lines]);

  return register;
}
