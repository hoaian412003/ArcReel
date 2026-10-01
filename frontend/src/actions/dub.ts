/**
 * 「创建配音」的完整流程：缺台词的单元先撰写越南语台词，台词就绪后再合成音频。
 *
 * 撰写台词是异步文本任务，合成必须等台词落盘，所以这里轮询单元列表直到被点名的单元都带上
 * 台词（或超时）；已有台词的单元直接进入合成。
 */
import { enqueueDubLines, enqueueDubSynthesis } from "@/actions/generation";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { referenceVideoCacheKey, useReferenceVideoStore } from "@/stores/reference-video-store";

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
  await enqueueDubSynthesis(projectName, episode, { unit_ids: ready });
  return "queued";
}
