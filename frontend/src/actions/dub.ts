/**
 * 配音相关动作。
 *
 * 「创建配音」只负责把选中的视频交给服务端自动配音流水线（缺台词先写、从成片检测开始时间、合成音频，
 * 同时最多 4 个视频在跑）；每一步的串行与重试都在服务端，前端不再自己轮询串联。
 */
import { enqueueAutoDub } from "@/actions/generation";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import type { DubTimingResult } from "@/types";

export type CreateVoiceoverOutcome = "queued" | "nothing_to_do";

export async function createVoiceover(
  projectName: string,
  episode: number,
  unitIds: string[],
): Promise<CreateVoiceoverOutcome> {
  if (unitIds.length === 0) return "nothing_to_do";
  const { unitIds: queued } = await enqueueAutoDub(projectName, episode, { unit_ids: unitIds });
  return queued.length > 0 ? "queued" : "nothing_to_do";
}

/** 汇总一次手动检测开始时间的结局并提示；返回检测到开始时间的台词总数。 */
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
