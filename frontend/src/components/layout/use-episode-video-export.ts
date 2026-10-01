import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";

const POLL_INTERVAL_MS = 2000;

function triggerBrowserDownload(url: string, filename: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * 侧栏「导出视频」：取该集第一条剪辑时间线（没有则按机械顺序新建），
 * 成片缺失或过期时先渲染，随后下载 MP4。
 */
export function useEpisodeVideoExport(projectName: string | null) {
  const { t } = useTranslation(["dashboard"]);
  const [exporting, setExporting] = useState<Set<number>>(new Set());

  const setBusy = (episode: number, busy: boolean) =>
    setExporting((prev) => {
      const next = new Set(prev);
      if (busy) next.add(episode);
      else next.delete(episode);
      return next;
    });

  const exportEpisodeVideo = useCallback(
    async (episode: number) => {
      if (!projectName || exporting.has(episode)) return;
      setBusy(episode, true);
      try {
        const { timelines } = await API.listEditTimelines(projectName, episode);
        const timelineId =
          timelines[0]?.timeline_id ??
          (await API.createEditTimeline(projectName, episode, { name: "Default", source: "mechanical" }))
            .timeline.timeline_id;

        let state = await API.getEditTimelineRender(projectName, episode, timelineId);
        if (!state.final_cut || state.final_cut.stale || state.render?.status === "running") {
          if (state.render?.status !== "running") {
            await API.renderEditTimeline(projectName, episode, timelineId);
          }
          for (;;) {
            await sleep(POLL_INTERVAL_MS);
            state = await API.getEditTimelineRender(projectName, episode, timelineId);
            if (state.render?.status === "failed") {
              throw new Error(state.render.error_key ?? "render_failed");
            }
            if (state.render?.status === "done") break;
          }
        }
        if (!state.final_cut) throw new Error("render_failed");
        triggerBrowserDownload(state.final_cut.url, `${projectName}_episode_${episode}.mp4`);
        useAppStore.getState().pushToast(t("dashboard:episode_video_export_started", { episode }), "success");
      } catch (err) {
        useAppStore
          .getState()
          .pushNotification(t("dashboard:episode_video_export_failed", { message: errMsg(err) }), "error");
      } finally {
        setBusy(episode, false);
      }
    },
    [projectName, exporting, t],
  );

  return { exportEpisodeVideo, exporting };
}
