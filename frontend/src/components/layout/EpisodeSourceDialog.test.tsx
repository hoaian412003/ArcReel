import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeMeta, EpisodeSourceUploadResult } from "@/types";
import { EpisodeSourceDialog } from "./EpisodeSourceDialog";

function episode(num: number, title = `第${num}集`): EpisodeMeta {
  return { episode: num, title, script_file: `scripts/episode_${num}.json` };
}

function uploadResult(overrides: Partial<EpisodeSourceUploadResult> = {}): EpisodeSourceUploadResult {
  return {
    episode: 3,
    title: "",
    created: true,
    stale: false,
    characters: 4,
    analysis: { status: "queued", task_id: "t1" },
    ...overrides,
  };
}

function renderDialog(props: Partial<Parameters<typeof EpisodeSourceDialog>[0]> = {}) {
  const onClose = vi.fn();
  const onUploaded = vi.fn();
  render(
    <EpisodeSourceDialog
      open
      projectName="demo"
      episodes={[episode(1), episode(2)]}
      onClose={onClose}
      onUploaded={onUploaded}
      {...props}
    />,
  );
  return { onClose, onUploaded };
}

describe("EpisodeSourceDialog", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState({ toast: null });
  });

  it("prefills the next episode number and uploads pasted text with the title", async () => {
    const upload = vi.spyOn(API, "uploadEpisodeSource").mockResolvedValue(uploadResult());
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue(undefined as never);
    const { onClose, onUploaded } = renderDialog();
    const user = userEvent.setup();

    expect(screen.getByLabelText("集号")).toHaveValue(3);
    await user.type(screen.getByLabelText("标题（可选）"), "城门");
    await user.type(screen.getByLabelText("或直接粘贴文本"), "李恒与少女相遇。");
    await user.click(screen.getByRole("button", { name: "上传并分析" }));

    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(3));
    expect(upload).toHaveBeenCalledWith("demo", 3, { content: "李恒与少女相遇。", title: "城门" });
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps submit disabled until there is a source, and warns when the episode already exists", async () => {
    renderDialog({ initialEpisode: 2 });
    const user = userEvent.setup();

    expect(screen.getByRole("button", { name: "上传并分析" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent("第 2 集已有源文");
    expect(screen.getByLabelText("标题（可选）")).toHaveValue("第2集");

    await user.type(screen.getByLabelText("或直接粘贴文本"), "新内容");
    expect(screen.getByRole("button", { name: "上传并分析" })).toBeEnabled();
  });

  it("shows the server error and stays open when the upload fails", async () => {
    vi.spyOn(API, "uploadEpisodeSource").mockRejectedValue(new Error("源文不能为空"));
    const { onClose } = renderDialog();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("或直接粘贴文本"), "正文");
    await user.click(screen.getByRole("button", { name: "上传并分析" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("源文不能为空");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("tells the user to run the analysis later when the worker is offline", async () => {
    vi.spyOn(API, "uploadEpisodeSource").mockResolvedValue(
      uploadResult({ analysis: { status: "worker_offline", task_id: null } }),
    );
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue(undefined as never);
    renderDialog();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("或直接粘贴文本"), "正文");
    await user.click(screen.getByRole("button", { name: "上传并分析" }));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toContain("后台执行器离线"));
  });
});
