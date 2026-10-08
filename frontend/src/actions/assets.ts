/**
 * 资产分析相关动作。
 *
 * 上传分集源文与手动重试都会让服务端入队 `text_asset_analysis` 任务；组件经这里调用，
 * 统一提示入队结果（执行器离线时源文已落盘，只提示稍后重试）。
 */
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { AssetAnalysisQueued, EpisodeSourceUploadResult, NovelSplitOptions, NovelSplitResult } from "@/types";

/** 上传（或替换）一集源文，刷新项目并提示分析是否已入队。 */
export async function uploadEpisodeSource(
  projectName: string,
  episode: number,
  input: { file?: File; content?: string; title?: string },
): Promise<EpisodeSourceUploadResult> {
  const result = await API.uploadEpisodeSource(projectName, episode, input);
  const key =
    result.analysis.status === "queued"
      ? "dashboard:episode_source_uploaded_toast"
      : "dashboard:episode_source_uploaded_offline_toast";
  useAppStore.getState().pushToast(i18n.t(key, { episode: result.episode }), "success");
  await useProjectsStore.getState().refreshProject(projectName);
  return result;
}

/** 手动重新入队资产分析，并提示结果。 */
export async function enqueueAssetAnalysis(projectName: string): Promise<AssetAnalysisQueued> {
  const result = await API.analyzeAssets(projectName);
  const queued = result.status === "queued";
  useAppStore
    .getState()
    .pushToast(
      i18n.t(queued ? "dashboard:asset_analysis_queued_toast" : "dashboard:asset_analysis_worker_offline_toast"),
      queued ? "success" : "error",
    );
  return result;
}

/** 删除一集（含其剧本与产物）：调用方须先取得用户的明确确认；成功后刷新项目并提示。 */
export async function deleteEpisode(projectName: string, episode: number): Promise<void> {
  await API.deleteEpisode(projectName, episode, { confirmProducts: true });
  useAppStore.getState().pushToast(i18n.t("dashboard:episode_delete_done_toast", { episode }), "success");
  await useProjectsStore.getState().refreshProject(projectName);
}

/** 按章节标题把整本小说切成分集并落盘，刷新项目并提示分析是否已入队。 */
export async function splitNovelIntoEpisodes(
  projectName: string,
  options: NovelSplitOptions,
): Promise<NovelSplitResult> {
  const result = await API.splitNovel(projectName, options);
  const key =
    result.analysis?.status === "worker_offline"
      ? "dashboard:novel_split_done_offline_toast"
      : "dashboard:novel_split_done_toast";
  useAppStore.getState().pushToast(i18n.t(key, { count: result.episodes.length }), "success");
  await useProjectsStore.getState().refreshProject(projectName);
  return result;
}
