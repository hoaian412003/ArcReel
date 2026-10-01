import { useState } from "react";
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
  /** 没有勾选任何单元时处理的单元（当前选中项）。 */
  fallbackUnitId: string;
}

const EMPTY: readonly string[] = [];

/**
 * 「创建配音」：对勾选的单元（无勾选则当前单元）撰写越南语台词并合成配音音频。
 * 项目尚未启用越南语配音时，同一个按钮先负责开启，开启后再点一次创建。
 */
export function CreateVoiceoverButton({ projectName, episode, fallbackUnitId }: CreateVoiceoverButtonProps) {
  const { t } = useTranslation("dashboard");
  const enabled = useProjectsStore((s) => s.currentProjectData?.dub_target_language === "vi");
  const checked =
    useReferenceVideoStore((s) => s.checkedUnitIdsByEpisode[referenceVideoCacheKey(projectName, episode)]) ??
    EMPTY;
  const dubTasks = useDubTasks(projectName);
  const [working, setWorking] = useState(false);
  const targets = checked.length > 0 ? [...checked] : [fallbackUnitId];
  const busy = working || dubTasks.active;

  const handleClick = async () => {
    if (busy) return;
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
    : targets.length > 1
      ? t("dub_create_voiceover_count", { count: targets.length })
      : t("dub_create_voiceover");

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={busy}
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
