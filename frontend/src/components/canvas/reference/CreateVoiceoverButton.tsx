import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AudioLines, Loader2 } from "lucide-react";
import { API } from "@/api";
import { createVoiceover } from "@/actions/dub";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { referenceVideoCacheKey, useReferenceVideoStore } from "@/stores/reference-video-store";
import { errMsg } from "@/utils/async";
import { useDubTasks } from "./use-dub-tasks";

export interface CreateVoiceoverButtonProps {
  projectName: string;
  episode: number;
  /**
   * unit：对勾选的视频（无勾选则当前这个视频）自动配音，放在对白面板里；
   * episode：对勾选的视频（无勾选则本集所有已有成片的视频）自动配音，放在画布顶栏。
   */
  scope?: "unit" | "episode";
  /** scope=unit 且没有勾选任何视频时处理的单元（当前选中项）。 */
  fallbackUnitId?: string;
}

const EMPTY: readonly string[] = [];

/**
 * 「创建配音」：交给服务端自动配音流水线——每个视频依次写越南语台词（缺则写）、从成片检测开始时间、
 * 合成音频，同时最多 4 个视频在跑。项目尚未启用越南语配音时，同一个按钮先负责开启，开启后再点一次。
 */
export function CreateVoiceoverButton({
  projectName,
  episode,
  scope = "unit",
  fallbackUnitId,
}: CreateVoiceoverButtonProps) {
  const { t } = useTranslation("dashboard");
  const enabled = useProjectsStore((s) => s.currentProjectData?.dub_target_language === "vi");
  const checked =
    useReferenceVideoStore((s) => s.checkedUnitIdsByEpisode[referenceVideoCacheKey(projectName, episode)]) ??
    EMPTY;
  // 选择器返回 store 里的原数组（引用稳定）；在选择器里 filter/map 会每次渲染都得到新数组，触发无限重渲染
  const units = useReferenceVideoStore((s) => s.unitsByEpisode[referenceVideoCacheKey(projectName, episode)]);
  const unitsWithVideo = useMemo(
    () => (units ?? []).filter((unit) => Boolean(unit.generated_assets?.video_clip)).map((unit) => unit.unit_id),
    [units],
  );
  const dubTasks = useDubTasks(projectName);
  const [working, setWorking] = useState(false);

  const targets =
    checked.length > 0
      ? [...checked]
      : scope === "episode"
        ? unitsWithVideo
        : fallbackUnitId
          ? [fallbackUnitId]
          : [];
  const nothingToVoice = enabled && scope === "episode" && targets.length === 0;
  const busy = working || dubTasks.active;

  const handleClick = async () => {
    if (busy || nothingToVoice) return;
    setWorking(true);
    try {
      if (!enabled) {
        await API.updateProject(projectName, { dub_target_language: "vi" });
        await useProjectsStore.getState().refreshProject(projectName);
        useAppStore.getState().pushToast(t("dub_enabled_toast"), "success");
        return;
      }
      await createVoiceover(projectName, episode, targets);
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("dub_create_failed", { error: errMsg(e) }), "error");
    } finally {
      setWorking(false);
    }
  };

  const label = !enabled
    ? t("dub_enable_voiceover")
    : scope === "episode"
      ? targets.length > 0
        ? t("dub_auto_episode_count", { count: targets.length })
        : t("dub_auto_episode")
      : targets.length > 1
        ? t("dub_create_voiceover_count", { count: targets.length })
        : t("dub_create_voiceover");
  const title = nothingToVoice
    ? t("dub_auto_no_video_hint")
    : scope === "episode" && enabled
      ? t("dub_auto_episode_hint")
      : undefined;

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={busy || nothingToVoice}
      title={title}
      className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-accent-soft)] bg-[oklch(0.24_0.02_290_/_0.45)] px-2 py-1 text-[11.5px] text-[var(--color-text-2)] transition-colors hover:text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-60"
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : (
        <AudioLines className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      <span>{label}</span>
    </button>
  );
}
