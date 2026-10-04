import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "wouter";
import { AlertTriangle, ArrowLeft, ChevronLeft, ChevronRight, Pause, Play, Scissors, Trash2 } from "lucide-react";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { useDubPreviewAudio } from "./use-dub-preview-audio";
import {
  addUnitClip,
  clipLength,
  clipStarts,
  dubBlocks,
  effectiveRange,
  isTrimStale,
  locateTime,
  moveClip,
  removeClip,
  splitClip,
  totalLength,
  updateClip,
} from "@/utils/edit-timeline";
import type {
  EditClip,
  EditTimelineDetail,
  EditTimelineSummary,
  EditTransition,
  EditUnit,
} from "@/types/edit-timeline";

interface EditTimelineCanvasProps {
  projectName: string;
  episode: number;
}

const TRANSITIONS: EditTransition[] = ["cut", "fade", "dissolve", "wipe"];
const MIN_CLIP_PX = 56;
const PX_PER_SECOND = 24;
/** 配音块在主轨底部留出的一条通道高度（像素）。 */
const DUB_LANE_PX = 18;

function formatClock(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

/** 集的剪辑时间线编辑器：主轨片段排布/裁切/转场、连续预览、修订保存与成片渲染。 */
export function EditTimelineCanvas({ projectName, episode }: EditTimelineCanvasProps) {
  const { t } = useTranslation("edit_timeline");
  const pushToast = useAppStore((s) => s.pushToast);

  const [timelines, setTimelines] = useState<EditTimelineSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<EditTimelineDetail | null>(null);
  const [draft, setDraft] = useState<EditClip[]>([]);
  const [draftVoiceOver, setDraftVoiceOver] = useState(false);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [playIndex, setPlayIndex] = useState(0);
  const [globalTime, setGlobalTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const seekOffset = useRef(0);
  const keepPlaying = useRef(false);

  const units = useMemo(() => new Map((detail?.units ?? []).map((u) => [u.unit_id, u])), [detail]);
  const latestClips = detail?.timeline.revisions.at(-1)?.clips;
  const latestRevision = detail?.timeline.revisions.at(-1)?.revision ?? 0;
  const latestVoiceOver = detail?.timeline.revisions.at(-1)?.voice_over ?? false;
  const voiceOverAvailable = detail?.voice_over_available ?? false;
  const originalVolume = detail?.original_volume ?? 0.3;
  const dirty =
    latestClips !== undefined &&
    (JSON.stringify(latestClips) !== JSON.stringify(draft) || latestVoiceOver !== draftVoiceOver);
  const starts = useMemo(() => clipStarts(draft, units), [draft, units]);
  const dub = useMemo(() => dubBlocks(draft, units, starts), [draft, units, starts]);
  const total = useMemo(() => totalLength(draft, units), [draft, units]);
  const selectedClip = draft.find((c) => c.clip_id === selectedClipId) ?? null;
  const selectedUnit = selectedClip ? units.get(selectedClip.unit_id) : undefined;

  const applyDetail = useCallback((next: EditTimelineDetail) => {
    setDetail(next);
    setDraft(next.timeline.revisions.at(-1)?.clips ?? []);
    setDraftVoiceOver(next.timeline.revisions.at(-1)?.voice_over ?? false);
    setSelectedId(next.timeline.timeline_id);
  }, []);

  const refreshList = useCallback(async () => {
    const { timelines: list } = await API.listEditTimelines(projectName, episode);
    setTimelines(list);
    return list;
  }, [projectName, episode]);

  const loadTimeline = useCallback(
    async (id: string) => {
      applyDetail(await API.getEditTimeline(projectName, episode, id));
      setSelectedClipId(null);
      setPlayIndex(0);
    },
    [projectName, episode, applyDetail],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await refreshList();
        if (!cancelled && list.length > 0) await loadTimeline(list[0].timeline_id);
      } catch (e) {
        if (!cancelled) pushToast(errMsg(e, t("load_failed")), "error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshList, loadTimeline, pushToast, t]);

  // ---- 渲染状态轮询 ----
  const renderStatus = detail?.render?.status;
  useEffect(() => {
    if (renderStatus !== "running" || !selectedId) return;
    const timer = setInterval(async () => {
      try {
        const res = await API.getEditTimelineRender(projectName, episode, selectedId);
        setDetail((d) => (d ? { ...d, render: res.render, final_cut: res.final_cut } : d));
      } catch {
        /* 下个周期重试 */
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [renderStatus, selectedId, projectName, episode]);

  // ---- 预览：按片段顺序连续播放 ----
  const playClip = draft[playIndex];
  const playUnit = playClip ? units.get(playClip.unit_id) : undefined;
  const nextClip = draft[playIndex + 1];
  const nextUnit = nextClip ? units.get(nextClip.unit_id) : undefined;
  const playRange = playClip ? effectiveRange(playClip, playUnit) : null;

  // 预览里的配音：只放当前片段的，跟着视频时钟走；开着配音时原声压低
  const clipDub = useMemo(() => dub.filter((block) => block.clipId === playClip?.clip_id), [dub, playClip?.clip_id]);
  const registerDubAudio = useDubPreviewAudio(videoRef, clipDub, {
    enabled: draftVoiceOver,
    originalVolume,
    playing,
    // 成片里只要有配音，所有片段的原声都压低；预览与它一致
    lowerOriginal: dub.length > 0,
  });

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !playRange) return;
    const target = playRange[0] + seekOffset.current;
    seekOffset.current = 0;
    pendingSeek.current = target;
    if (video.readyState >= 1) {
      video.currentTime = target;
      pendingSeek.current = null;
      if (keepPlaying.current) void video.play().catch(() => undefined);
    }
  }, [playIndex, playClip?.clip_id, playUnit?.video_url, playRange?.[0]]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleLoadedMetadata = () => {
    const video = videoRef.current;
    if (!video || pendingSeek.current === null) return;
    video.currentTime = pendingSeek.current;
    pendingSeek.current = null;
    if (keepPlaying.current) void video.play().catch(() => undefined);
  };

  /** 当前片段播完：接着播下一个；已是最后一个则停在末尾。 */
  const advance = () => {
    if (playIndex + 1 < draft.length) {
      keepPlaying.current = true;
      seekOffset.current = 0;
      setGlobalTime(starts[playIndex + 1]);
      setPlayIndex(playIndex + 1);
    } else {
      videoRef.current?.pause();
      keepPlaying.current = false;
      setPlaying(false);
      setGlobalTime(total);
    }
  };

  const reachedOut = (video: HTMLVideoElement) => !!playRange && video.currentTime >= playRange[1] - 0.03;

  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video || !playRange) return;
    if (!video.paused && reachedOut(video)) {
      advance();
      return;
    }
    setGlobalTime(starts[playIndex] + Math.max(0, video.currentTime - playRange[0]));
  };

  // timeupdate 只有约 4Hz，出点会被越过；播放期间逐帧检查，保证在出点处无缝切到下一个片段。
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => {
      const video = videoRef.current;
      if (video && !video.paused && !video.ended && reachedOut(video)) {
        advance();
        return;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  });

  /** 跳到整条时间线上的某个时刻（可跨片段）。 */
  const seekGlobal = (time: number) => {
    const { index, offset } = locateTime(draft, units, time);
    setGlobalTime(starts[index] + offset);
    const video = videoRef.current;
    const range = draft[index] ? effectiveRange(draft[index], units.get(draft[index].unit_id)) : null;
    if (index === playIndex && video && range && video.readyState >= 1) {
      video.currentTime = range[0] + offset;
      return;
    }
    keepPlaying.current = video ? !video.paused : false;
    seekOffset.current = offset;
    setPlayIndex(index);
  };

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      if (globalTime >= total - 0.05) seekGlobal(0);
      keepPlaying.current = true;
      void video.play().catch(() => undefined);
    } else {
      keepPlaying.current = false;
      video.pause();
    }
  };

  const selectClip = (clip: EditClip) => {
    setSelectedClipId(clip.clip_id);
    seekGlobal(starts[draft.findIndex((c) => c.clip_id === clip.clip_id)] ?? 0);
  };

  // ---- 编辑动作 ----
  const guarded = async (action: () => Promise<void>) => {
    try {
      await action();
    } catch (e) {
      if (e instanceof ApiRequestError && e.status === 409 && selectedId) {
        pushToast(t("conflict"), "error");
        await loadTimeline(selectedId).catch(() => undefined);
      } else {
        pushToast(errMsg(e), "error");
      }
    }
  };

  const createTimeline = (source: "mechanical" | "copy") =>
    guarded(async () => {
      const name =
        source === "copy" && detail
          ? t("copy_suffix", { name: detail.timeline.name })
          : t("default_name", { n: timelines.length + 1 });
      const created = await API.createEditTimeline(projectName, episode, {
        name,
        source,
        copy_from: source === "copy" ? (selectedId ?? undefined) : undefined,
      });
      applyDetail(created);
      setSelectedClipId(null);
      setPlayIndex(0);
      await refreshList();
    });

  const save = () =>
    guarded(async () => {
      if (!selectedId) return;
      setSaving(true);
      try {
        const next = await API.updateEditTimeline(projectName, episode, selectedId, {
          parent_revision: latestRevision,
          clips: draft,
          summary: "edit",
          voice_over: draftVoiceOver,
        });
        applyDetail(next);
        pushToast(t("saved", { revision: next.timeline.revisions.at(-1)?.revision }), "success");
        await refreshList();
      } finally {
        setSaving(false);
      }
    });

  const rollback = (toRevision: number) =>
    guarded(async () => {
      if (!selectedId) return;
      applyDetail(
        await API.rollbackEditTimeline(projectName, episode, selectedId, {
          to_revision: toRevision,
          parent_revision: latestRevision,
        }),
      );
      await refreshList();
    });

  const rename = () =>
    guarded(async () => {
      if (!detail || !selectedId) return;
      const name = window.prompt(t("rename_prompt"), detail.timeline.name)?.trim();
      if (!name || name === detail.timeline.name) return;
      const next = await API.renameEditTimeline(projectName, episode, selectedId, name);
      setDetail((d) => (d ? { ...d, timeline: next.timeline } : d));
      await refreshList();
    });

  const remove = () =>
    guarded(async () => {
      if (!detail || !selectedId) return;
      if (!window.confirm(t("delete_confirm", { name: detail.timeline.name }))) return;
      await API.deleteEditTimeline(projectName, episode, selectedId);
      const list = await refreshList();
      if (list.length > 0) await loadTimeline(list[0].timeline_id);
      else {
        setDetail(null);
        setDraft([]);
        setSelectedId(null);
      }
    });

  const render = () =>
    guarded(async () => {
      if (!selectedId) return;
      if (dirty) {
        pushToast(t("render_needs_save"), "error");
        return;
      }
      const state = await API.renderEditTimeline(projectName, episode, selectedId);
      setDetail((d) => (d ? { ...d, render: state } : d));
    });

  const edit = (next: EditClip[]) => setDraft(next);

  // ---- 视图 ----
  if (loading) return <div className="p-6 text-sm text-gray-400">…</div>;

  const header = (
    <div
      className="flex flex-wrap items-center gap-3 px-6 py-3"
      style={{ borderBottom: "1px solid var(--color-hairline)" }}
    >
      <Link
        href={`/episodes/${episode}`}
        className="inline-flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200"
      >
        <ArrowLeft className="h-4 w-4" />
        {t("back")}
      </Link>
      <h2 className="text-base font-semibold text-gray-100">{t("title", { episode })}</h2>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {timelines.length > 0 && (
          <select
            aria-label={t("select_timeline")}
            className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100"
            value={selectedId ?? ""}
            onChange={(e) => void guarded(() => loadTimeline(e.target.value))}
          >
            {timelines.map((tl) => (
              <option key={tl.timeline_id} value={tl.timeline_id}>
                {tl.name} · {t("timeline_summary", { count: tl.clip_count })}
              </option>
            ))}
          </select>
        )}
        <button className="btn-ghost text-sm" onClick={() => void createTimeline("mechanical")}>
          {t("new_timeline")}
        </button>
        {detail && (
          <>
            <button className="btn-ghost text-sm" onClick={() => void createTimeline("copy")}>
              {t("duplicate")}
            </button>
            <button className="btn-ghost text-sm" onClick={() => void rename()}>
              {t("rename")}
            </button>
            <button className="btn-ghost text-sm text-red-400" onClick={() => void remove()}>
              {t("delete")}
            </button>
          </>
        )}
      </div>
    </div>
  );

  if (!detail) {
    return (
      <div className="flex h-full flex-col">
        {header}
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-gray-400">
          <p>{t("no_timelines")}</p>
          <button className="btn-primary" onClick={() => void createTimeline("mechanical")}>
            {t("create_from_script")}
          </button>
        </div>
      </div>
    );
  }

  const diag = detail.diagnostics;
  const warnings: string[] = [];
  if (diag.deleted_unit_clips.length) warnings.push(t("warn_deleted", { count: diag.deleted_unit_clips.length }));
  if (diag.stale_trim_clips.length) warnings.push(t("warn_stale_trim", { count: diag.stale_trim_clips.length }));
  if (diag.missing_video_clips.length)
    warnings.push(t("warn_missing_video", { count: diag.missing_video_clips.length }));

  const clipWidth = (clip: EditClip) => Math.max(MIN_CLIP_PX, clipLength(clip, units.get(clip.unit_id)) * PX_PER_SECOND);
  let playheadX: number | null = null;
  if (draft.length > 0 && playIndex < draft.length) {
    let left = 8; // 主轨容器内边距
    for (let i = 0; i < playIndex; i++) left += clipWidth(draft[i]) + 4; // 4 = 片段间距
    const length = clipLength(draft[playIndex], units.get(draft[playIndex].unit_id));
    const fraction = length > 0 ? Math.min(1, Math.max(0, (globalTime - starts[playIndex]) / length)) : 0;
    playheadX = left + fraction * clipWidth(draft[playIndex]);
  }

  const usedIds = new Set(draft.map((c) => c.unit_id));
  const unusedUnits = detail.units.filter((u) => !usedIds.has(u.unit_id));
  const render_ = detail.render;
  const finalCut = detail.final_cut;

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {header}
      {warnings.length > 0 && (
        <div className="space-y-1 px-6 pt-3">
          {warnings.map((w) => (
            <div key={w} className="flex items-center gap-2 text-xs text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {w}
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-6 px-6 py-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* 预览 */}
        <section>
          <h3 className="mb-2 text-sm font-medium text-gray-300">{t("preview")}</h3>
          <div className="aspect-video overflow-hidden rounded bg-black">
            {playUnit?.video_url ? (
              // eslint-disable-next-line jsx-a11y/media-has-caption -- 预览片段素材，字幕在成片阶段生成
              <video
                ref={videoRef}
                key={playUnit.video_url}
                src={playUnit.video_url}
                playsInline
                className="h-full w-full"
                onLoadedMetadata={handleLoadedMetadata}
                onTimeUpdate={handleTimeUpdate}
                onPlay={() => {
                  keepPlaying.current = true;
                  setPlaying(true);
                }}
                onPause={() => {
                  // 素材播到文件末尾时浏览器先触发 pause 再触发 ended：此时不是用户暂停，
                  // 不清除续播标记，交给 onEnded 接着播下一个片段。
                  if (videoRef.current?.ended) return;
                  keepPlaying.current = false;
                  setPlaying(false);
                }}
                onEnded={() => {
                  if (keepPlaying.current) advance();
                  else setPlaying(false);
                }}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-gray-500">{t("no_clips")}</div>
            )}
          </div>
          <div className="mt-2 flex items-center gap-3">
            <button
              className="btn-ghost inline-flex items-center"
              aria-label={playing ? t("pause") : t("play")}
              disabled={draft.length === 0}
              onClick={togglePlay}
            >
              {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            </button>
            <input
              type="range"
              aria-label={t("scrub")}
              className="min-w-0 flex-1"
              min={0}
              max={Math.max(total, 0.1)}
              step={0.05}
              value={Math.min(globalTime, total)}
              disabled={draft.length === 0}
              onChange={(e) => seekGlobal(Number.parseFloat(e.target.value))}
            />
            <span className="num shrink-0 text-xs text-gray-400">
              {formatClock(globalTime)} / {formatClock(total)}
            </span>
          </div>
          {/* 预载下一个片段的素材，减少切换时的停顿 */}
          {nextUnit?.video_url && nextUnit.video_url !== playUnit?.video_url && (
            <video src={nextUnit.video_url} preload="auto" muted hidden aria-hidden="true" />
          )}
          {draftVoiceOver &&
            clipDub.map((block) => (
              // eslint-disable-next-line jsx-a11y/media-has-caption -- 配音音轨无画面，台词见主轨下方的配音通道
              <audio
                key={block.key}
                ref={(el) => registerDubAudio(block.key, el)}
                src={block.audioUrl}
                preload="auto"
                hidden
              />
            ))}
          <p className="mt-1 text-xs text-gray-500">{t("preview_note")}</p>
        </section>

        {/* 检查器 */}
        <section>
          <h3 className="mb-2 text-sm font-medium text-gray-300">{t("inspector")}</h3>
          {selectedClip ? (
            <ClipInspector
              clip={selectedClip}
              unit={selectedUnit}
              onChange={(patch) => edit(updateClip(draft, selectedClip.clip_id, patch, selectedUnit))}
              onSplit={() => {
                const at = videoRef.current?.currentTime ?? 0;
                edit(splitClip(draft, selectedClip.clip_id, at, selectedUnit));
              }}
              onMove={(offset) => edit(moveClip(draft, selectedClip.clip_id, offset))}
              onRemove={() => {
                edit(removeClip(draft, selectedClip.clip_id));
                setSelectedClipId(null);
              }}
              t={t}
            />
          ) : (
            <p className="text-sm text-gray-500">{t("select_clip")}</p>
          )}
        </section>
      </div>

      {/* 主轨 */}
      <section className="px-6 pb-4">
        <div className="mb-2 flex items-center gap-3">
          <h3 className="text-sm font-medium text-gray-300">{t("track")}</h3>
          <span className="num text-xs text-gray-500">{totalLength(draft, units).toFixed(1)}s</span>
          {dirty && <span className="text-xs text-amber-400">{t("unsaved")}</span>}
          {voiceOverAvailable && (
            <label
              className="inline-flex items-center gap-1.5 text-xs text-gray-300"
              title={t("voice_over_hint", { percent: Math.round(originalVolume * 100) })}
            >
              <input
                type="checkbox"
                checked={draftVoiceOver}
                onChange={(e) => setDraftVoiceOver(e.target.checked)}
                className="h-3.5 w-3.5 accent-teal-400"
              />
              {t("voice_over")}
            </label>
          )}
          <div className="ml-auto flex gap-2">
            <button
              className="btn-ghost text-sm"
              disabled={!dirty}
              onClick={() => {
                if (latestClips) setDraft(latestClips);
                setDraftVoiceOver(latestVoiceOver);
              }}
            >
              {t("revert")}
            </button>
            <button className="btn-primary text-sm" disabled={!dirty || saving} onClick={() => void save()}>
              {t("save")}
            </button>
          </div>
        </div>
        <div
          className="relative flex min-h-[84px] items-stretch gap-1 overflow-x-auto rounded border border-gray-800 bg-gray-900/60 p-2"
          style={voiceOverAvailable ? { paddingBottom: 8 + DUB_LANE_PX } : undefined}
        >
          {draft.length === 0 && <p className="self-center px-2 text-sm text-gray-500">{t("no_clips")}</p>}
          {playheadX !== null && (
            <div
              aria-hidden="true"
              className="pointer-events-none absolute bottom-0 top-0 z-10 w-px bg-red-400"
              style={{ left: playheadX }}
            />
          )}
          {voiceOverAvailable &&
            dub.map((block) => {
              const index = draft.findIndex((c) => c.clip_id === block.clipId);
              let left = 8;
              for (let i = 0; i < index; i++) left += clipWidth(draft[i]) + 4;
              const clipLen = clipLength(draft[index], units.get(draft[index].unit_id));
              const width = clipWidth(draft[index]);
              const scale = clipLen > 0 ? width / clipLen : 0;
              return (
                <div
                  key={block.key}
                  data-testid="dub-block"
                  title={block.text}
                  aria-label={t("voice_over_line", { unit: block.unitId, text: block.text })}
                  className={`pointer-events-auto absolute truncate rounded-sm px-1 text-[10px] leading-[14px] ${
                    draftVoiceOver ? "bg-teal-500/70 text-teal-50" : "bg-gray-600/50 text-gray-300"
                  }`}
                  style={{
                    left: left + block.offsetInClip * scale,
                    width: Math.max(4, block.duration * scale),
                    bottom: 6,
                    height: 14,
                  }}
                >
                  {block.text}
                </div>
              );
            })}
          {draft.map((clip) => {
            const unit = units.get(clip.unit_id);
            const width = clipWidth(clip);
            const flags = [
              !unit && t("clip_missing"),
              unit && !unit.video_url && t("clip_missing"),
              isTrimStale(clip, unit) && t("clip_stale"),
            ].filter(Boolean);
            const selected = clip.clip_id === selectedClipId;
            return (
              <button
                key={clip.clip_id}
                aria-pressed={selected}
                onClick={() => selectClip(clip)}
                style={{ width, flexShrink: 0 }}
                className={`flex flex-col justify-between rounded border px-2 py-1 text-left text-xs ${
                  selected ? "border-indigo-400 bg-indigo-500/20" : "border-gray-700 bg-gray-800 hover:bg-gray-700"
                }`}
              >
                <span className="truncate font-medium text-gray-100">{clip.unit_id}</span>
                <span className="num text-gray-400">{clipLength(clip, unit).toFixed(1)}s</span>
                {clip.transition_to_next !== "cut" && (
                  <span className="text-[10px] text-indigo-300">→ {t(`transition_${clip.transition_to_next}`)}</span>
                )}
                {flags.length > 0 && <span className="text-[10px] text-amber-400">{flags.join(" · ")}</span>}
              </button>
            );
          })}
        </div>
      </section>

      {/* 未使用单元 */}
      {unusedUnits.length > 0 && (
        <section className="px-6 pb-4">
          <h3 className="mb-2 text-sm font-medium text-gray-300">{t("unused_units")}</h3>
          <div className="flex flex-wrap gap-2">
            {unusedUnits.map((u) => (
              <button
                key={u.unit_id}
                className="btn-ghost text-xs"
                disabled={!u.video_url}
                onClick={() => edit(addUnitClip(draft, u.unit_id))}
              >
                {t("add_unit")} {u.unit_id}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* 成片 + 修订历史 */}
      <div className="grid gap-6 px-6 pb-8 lg:grid-cols-2">
        <section>
          <div className="mb-2 flex items-center gap-3">
            <h3 className="text-sm font-medium text-gray-300">{t("final_cut")}</h3>
            <button
              className="btn-primary text-sm"
              disabled={render_?.status === "running" || draft.length === 0}
              onClick={() => void render()}
            >
              {render_?.status === "running" ? t("rendering") : t("render")}
            </button>
          </div>
          {render_?.status === "failed" && (
            <p className="mb-2 text-xs text-red-400">
              {t("render_failed", { reason: render_.error_key ?? "" })}
            </p>
          )}
          {finalCut && (
            <>
              {/* eslint-disable-next-line jsx-a11y/media-has-caption -- 渲染后的成片预览，字幕在成片阶段生成 */}
              <video src={finalCut.url} controls playsInline className="aspect-video w-full rounded bg-black" />
              <p className={`mt-1 text-xs ${finalCut.stale ? "text-amber-400" : "text-gray-500"}`}>
                {finalCut.stale ? t("final_cut_stale") : t("final_cut_current", { revision: finalCut.revision })}
              </p>
            </>
          )}
        </section>

        <section>
          <h3 className="mb-2 text-sm font-medium text-gray-300">{t("history")}</h3>
          <ul className="space-y-1">
            {[...detail.timeline.revisions].reverse().map((rev) => (
              <li key={rev.revision} className="flex items-center gap-2 text-xs text-gray-400">
                <span className="truncate">
                  {t("revision_item", { revision: rev.revision, author: rev.author, summary: rev.summary })}
                </span>
                {rev.revision !== latestRevision && (
                  <button
                    className="ml-auto shrink-0 text-indigo-300 hover:underline"
                    onClick={() => void rollback(rev.revision)}
                  >
                    {t("rollback")}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

interface ClipInspectorProps {
  clip: EditClip;
  unit: EditUnit | undefined;
  onChange: (patch: Partial<Pick<EditClip, "in_s" | "out_s" | "transition_to_next">>) => void;
  onSplit: () => void;
  onMove: (offset: -1 | 1) => void;
  onRemove: () => void;
  t: (key: string, options?: Record<string, unknown>) => string;
}

function ClipInspector({ clip, unit, onChange, onSplit, onMove, onRemove, t }: ClipInspectorProps) {
  const [start, end] = effectiveRange(clip, unit);
  const parse = (value: string) => {
    const n = Number.parseFloat(value);
    return Number.isFinite(n) ? Math.max(0, n) : null;
  };
  return (
    <div className="space-y-3 text-sm">
      <div className="font-medium text-gray-100">{clip.unit_id}</div>
      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs text-gray-400">
          {t("in_point")}
          <input
            type="number"
            min={0}
            step={0.1}
            value={start}
            onChange={(e) => {
              const v = parse(e.target.value);
              if (v !== null && v < end) onChange({ in_s: v, out_s: clip.out_s ?? end });
            }}
            className="mt-1 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-gray-100"
          />
        </label>
        <label className="text-xs text-gray-400">
          {t("out_point")}
          <input
            type="number"
            min={0}
            step={0.1}
            value={end}
            onChange={(e) => {
              const v = parse(e.target.value);
              if (v !== null && v > start) onChange({ in_s: start, out_s: v });
            }}
            className="mt-1 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-gray-100"
          />
        </label>
      </div>
      <label className="block text-xs text-gray-400">
        {t("transition")}
        <select
          value={clip.transition_to_next}
          onChange={(e) => onChange({ transition_to_next: e.target.value as EditTransition })}
          className="mt-1 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-gray-100"
        >
          {TRANSITIONS.map((name) => (
            <option key={name} value={name}>
              {t(`transition_${name}`)}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-wrap gap-2">
        <button className="btn-ghost inline-flex items-center gap-1 text-xs" onClick={onSplit}>
          <Scissors className="h-3.5 w-3.5" />
          {t("split")}
        </button>
        <button className="btn-ghost inline-flex items-center gap-1 text-xs" onClick={() => onMove(-1)}>
          <ChevronLeft className="h-3.5 w-3.5" />
          {t("move_left")}
        </button>
        <button className="btn-ghost inline-flex items-center gap-1 text-xs" onClick={() => onMove(1)}>
          {t("move_right")}
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <button className="btn-ghost inline-flex items-center gap-1 text-xs text-red-400" onClick={onRemove}>
          <Trash2 className="h-3.5 w-3.5" />
          {t("remove")}
        </button>
      </div>
    </div>
  );
}
