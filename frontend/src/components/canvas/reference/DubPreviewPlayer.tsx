import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export interface DubPreviewClip {
  /** 稳定键：unit 内第几条配音。 */
  index: number;
  url: string;
  startSeconds: number;
  durationSeconds: number;
  /** 字幕文本（越南语配音台词）。 */
  text: string;
}

export interface DubPreviewPlayerProps {
  videoUrl: string;
  posterUrl?: string;
  clips: readonly DubPreviewClip[];
  /** 原视频音量（0–1）；配音以满音量叠加其上。 */
  originalVolume?: number;
  className?: string;
}

/** 配音与视频时钟允许的最大偏差（秒），超出才拉回。 */
const MAX_DRIFT_SECONDS = 0.15;

/**
 * 越南语配音预览：原视频压低音量播放，每条配音按开始时间叠加，并显示当前配音台词。
 * 配音音轨由视频时钟驱动（播放、暂停、拖动、倍速都跟随视频），不自己维护时间线。
 */
export function DubPreviewPlayer({
  videoUrl,
  posterUrl,
  clips,
  originalVolume = 0.5,
  className,
}: DubPreviewPlayerProps) {
  const { t } = useTranslation("dashboard");
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRefs = useRef(new Map<number, HTMLAudioElement>());
  const frameRef = useRef<number | null>(null);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const synchronize = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const playing = !video.paused && !video.ended;
    let active: number | null = null;
    for (const clip of clips) {
      const audio = audioRefs.current.get(clip.index);
      if (!audio) continue;
      const offset = video.currentTime - clip.startSeconds;
      const inside = offset >= 0 && offset < clip.durationSeconds;
      if (inside) active ??= clip.index;
      if (!inside || !playing) {
        if (!audio.paused) audio.pause();
        continue;
      }
      audio.playbackRate = video.playbackRate;
      if (Math.abs(audio.currentTime - offset) > MAX_DRIFT_SECONDS) audio.currentTime = offset;
      if (audio.paused) void audio.play().catch(() => undefined);
    }
    // 暂停时也要按当前位置显示台词
    if (!playing) {
      active = null;
      for (const clip of clips) {
        const offset = video.currentTime - clip.startSeconds;
        if (offset >= 0 && offset < clip.durationSeconds) {
          active = clip.index;
          break;
        }
      }
    }
    setActiveIndex((prev) => (prev === active ? prev : active));
  }, [clips]);

  const stopLoop = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
  }, []);

  const startLoop = useCallback(() => {
    stopLoop();
    const tick = () => {
      synchronize();
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
  }, [stopLoop, synchronize]);

  useEffect(() => stopLoop, [stopLoop]);

  // 配音清单或原音量变化后立即对齐一次
  useEffect(() => {
    const video = videoRef.current;
    if (video) video.volume = originalVolume;
    synchronize();
  }, [originalVolume, synchronize]);

  const active = clips.find((clip) => clip.index === activeIndex) ?? null;
  const percent = Math.round(originalVolume * 100);

  return (
    <div className={className}>
      <div className="relative overflow-hidden rounded-lg border border-[var(--color-hairline-soft)] bg-black">
        {/* 越南语台词由下方 role=status 的字幕层随播放位置显示，配音音轨本身无画面 */}
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video
          ref={videoRef}
          src={videoUrl}
          poster={posterUrl}
          controls
          playsInline
          preload="metadata"
          aria-label={t("dub_preview_video_label")}
          className="block w-full"
          onLoadedMetadata={(e) => {
            e.currentTarget.volume = originalVolume;
          }}
          onPlay={startLoop}
          onPause={() => {
            stopLoop();
            synchronize();
          }}
          onEnded={() => {
            stopLoop();
            synchronize();
          }}
          onSeeked={synchronize}
          onRateChange={synchronize}
        />
        {active && (
          <p
            role="status"
            aria-live="polite"
            className="pointer-events-none absolute inset-x-3 bottom-12 m-0 rounded-md bg-black/70 px-3 py-1.5 text-center text-[13px] leading-snug text-white"
          >
            {active.text}
          </p>
        )}
      </div>
      {clips.map((clip) => (
        // eslint-disable-next-line jsx-a11y/media-has-caption -- 配音音轨无画面，台词见视频上的字幕层
        <audio
          key={`${clip.index}:${clip.url}`}
          ref={(el) => {
            if (el) audioRefs.current.set(clip.index, el);
            else audioRefs.current.delete(clip.index);
          }}
          src={clip.url}
          preload="auto"
        />
      ))}
      <p className="m-0 mt-1.5 text-[11px] text-[var(--color-text-4)]">
        {t("dub_preview_caption", { percent })}
      </p>
    </div>
  );
}
