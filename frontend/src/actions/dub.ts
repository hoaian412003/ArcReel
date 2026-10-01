/**
 * 「创建配音」的完整流程：缺台词的单元先撰写越南语台词，台词就绪后再合成音频。
 *
 * 撰写台词是异步文本任务，合成必须等台词落盘，所以这里轮询单元列表直到被点名的单元都带上
 * 台词（或超时）；已有台词的单元直接进入合成。
 */
import { API } from "@/api";
import { enqueueDubLines, enqueueDubSynthesis } from "@/actions/generation";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { referenceVideoCacheKey, useReferenceVideoStore } from "@/stores/reference-video-store";
import { errMsg } from "@/utils/async";
import type { DubTimingResult } from "@/types";

const LINES_POLL_INTERVAL_MS = 3000;
const LINES_POLL_MAX_ATTEMPTS = 100;

export type CreateVoiceoverOutcome = "queued" | "lines_timeout" | "nothing_to_do";

function unitsMissingLines(projectName: string, episode: number, unitIds: string[]): string[] {
  const units = useReferenceVideoStore.getState().unitsByEpisode[referenceVideoCacheKey(projectName, episode)] ?? [];
  const wanted = new Set(unitIds);
  return units
    .filter((unit) => wanted.has(unit.unit_id) && (unit.dub_lines?.length ?? 0) === 0)
    .map((unit) => unit.unit_id);
}

/** 汇总一次检测的结局并提示；返回检测到开始时间的台词总数。 */
export function notifyDubTiming(results: readonly DubTimingResult[]): number {
  const detected = results.reduce((sum, r) => sum + r.detected, 0);
  const total = results.reduce((sum, r) => sum + r.total, 0);
  const { pushToast } = useAppStore.getState();
  if (detected > 0) {
    pushToast(i18n.t("dashboard:dub_detect_done_toast", { detected, total }), "success");
  } else if (results.length > 0 && results.every((r) => r.status === "no_video")) {
    pushToast(i18n.t("dashboard:dub_detect_no_video_toast"), "info");
  } else {
    pushToast(i18n.t("dashboard:dub_detect_none_toast"), "info");
  }
  return detected;
}

/** 有成片、且还有台词没检测过开始时间的单元。 */
function unitsNeedingDetection(projectName: string, episode: number, unitIds: string[]): string[] {
  const units = useReferenceVideoStore.getState().unitsByEpisode[referenceVideoCacheKey(projectName, episode)] ?? [];
  const wanted = new Set(unitIds);
  return units
    .filter(
      (unit) =>
        wanted.has(unit.unit_id) &&
        Boolean(unit.generated_assets?.video_clip) &&
        (unit.dub_lines ?? []).some((line) => line.detected_start_seconds == null),
    )
    .map((unit) => unit.unit_id);
}

export async function createVoiceover(
  projectName: string,
  episode: number,
  unitIds: string[],
): Promise<CreateVoiceoverOutcome> {
  if (unitIds.length === 0) return "nothing_to_do";

  const missing = unitsMissingLines(projectName, episode, unitIds);
  if (missing.length > 0) {
    await enqueueDubLines(projectName, episode, missing);
    for (let attempt = 0; attempt < LINES_POLL_MAX_ATTEMPTS; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, LINES_POLL_INTERVAL_MS));
      await useReferenceVideoStore.getState().loadUnits(projectName, episode);
      if (unitsMissingLines(projectName, episode, missing).length === 0) break;
      if (attempt === LINES_POLL_MAX_ATTEMPTS - 1) {
        useAppStore.getState().pushToast(i18n.t("dashboard:dub_lines_timeout"), "error");
        return "lines_timeout";
      }
    }
  }

  // 撰写后仍没有台词的单元（模型未返回、校验未过）不进合成，由服务端按「无台词」拒绝整批前先剔除。
  const ready = unitIds.filter((id) => !unitsMissingLines(projectName, episode, [id]).length);
  if (ready.length === 0) return "nothing_to_do";

  // 台词就绪后先从成片读出原台词的开始时间；识别不可用或失败不挡住配音，开始时间退回估算
  const toDetect = unitsNeedingDetection(projectName, episode, ready);
  if (toDetect.length > 0) {
    try {
      notifyDubTiming((await API.detectDubTiming(projectName, episode, toDetect)).results);
      await useReferenceVideoStore.getState().loadUnits(projectName, episode);
    } catch (e: unknown) {
      useAppStore.getState().pushToast(i18n.t("dashboard:dub_detect_skipped_toast", { error: errMsg(e) }), "info");
    }
  }
  await enqueueDubSynthesis(projectName, episode, { unit_ids: ready });
  return "queued";
}
