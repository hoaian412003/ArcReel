import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { QueueOverview, QueueTaskDetail, QueueTaskRow } from "@/types/queue-monitor";
import { QueueMonitorPage } from "./QueueMonitorPage";

const OVERVIEW: QueueOverview = {
  worker: { online: true, owner_id: "worker-1", lease_until: "2026-01-01T00:00:00Z", mode: "embedded" },
  counts: { queued: 3, running: 2, succeeded_24h: 10, failed_24h: 1, cancelled_24h: 0 },
  lanes: [{ provider_id: "runninghub", media_type: "video", limit: 4, running: 2, queued: 3 }],
  oldest_queued_age_seconds: 185,
  cost_24h: { totals: [{ currency: "USD", amount: 1.5 }], provider_coins: 42 },
};

function task(overrides: Partial<QueueTaskRow> = {}): QueueTaskRow {
  return {
    task_id: "task-1",
    project_name: "demo",
    task_type: "video",
    media_type: "video",
    status: "queued",
    provider_id: "runninghub",
    provider_job_id: null,
    progress: null,
    last_synced_at: null,
    queued_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    error_message: null,
    estimated_cost_amount: 0.5,
    estimated_currency: "USD",
    cost_amount: null,
    currency: null,
    provider_coins: null,
    user_id: "u1",
    source: "webui",
    ...overrides,
  };
}

function detail(row: QueueTaskRow): QueueTaskDetail {
  return {
    task: {
      ...row,
      input_digest: "abc123",
      input: { prompt: "fly over the city", duration: 6 },
      output: { path: "videos/final.mp4" },
    },
    events: [],
    api_calls: [],
  };
}

function renderPage(path = "/app/queue") {
  const location = memoryLocation({ path, record: true });
  return render(
    <Router hook={location.hook} searchHook={location.searchHook}>
      <QueueMonitorPage />
    </Router>,
  );
}

describe("QueueMonitorPage", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.spyOn(API, "getQueueOverview").mockResolvedValue(OVERVIEW);
    vi.spyOn(API, "listQueueTasks").mockResolvedValue({ items: [task()], total: 1 });
    vi.spyOn(API, "getQueueTask").mockResolvedValue(detail(task()));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows worker state, counts, lane usage, oldest age and cost on the overview", async () => {
    renderPage();
    expect(await screen.findByText("在线")).toBeInTheDocument();
    expect(screen.getByText("3m 05s")).toBeInTheDocument();
    expect(screen.getByText("1.5 USD")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "runninghub video 车道占用" })).toHaveAttribute("aria-valuenow", "50");
  });

  it("shows an error panel with retry when the first load fails", async () => {
    vi.spyOn(API, "getQueueOverview").mockRejectedValueOnce(new Error("nope")).mockResolvedValue(OVERVIEW);
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("nope");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("在线")).toBeInTheDocument();
  });

  it("applies the status filter to the task query", async () => {
    renderPage("/app/queue?tab=tasks");
    await screen.findByText("task-1");
    fireEvent.change(screen.getByLabelText("状态"), { target: { value: "failed" } });
    await waitFor(() =>
      expect(API.listQueueTasks).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "failed", offset: 0 }),
        expect.anything(),
      ),
    );
  });

  it("removes audit navigation and shows task input and output in the drawer", async () => {
    renderPage("/app/queue?tab=tasks");
    expect(screen.queryByRole("tab", { name: "审计" })).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "打开任务 task-1" }));
    const drawer = await screen.findByRole("dialog", { name: "任务详情" });
    expect(within(drawer).getByText("输入")).toBeInTheDocument();
    expect(within(drawer).getByText(/fly over the city/)).toBeInTheDocument();
    expect(within(drawer).getByText("输出")).toBeInTheDocument();
    expect(within(drawer).getByText(/videos\/final\.mp4/)).toBeInTheDocument();
  });

  it("cancels a queued task from the drawer only after confirmation", async () => {
    const cancel = vi.spyOn(API, "cancelQueueTask").mockResolvedValue(task({ status: "cancelled" }));
    renderPage("/app/queue?tab=tasks");
    fireEvent.click(await screen.findByRole("button", { name: "打开任务 task-1" }));
    const drawer = await screen.findByRole("dialog", { name: "任务详情" });
    expect(within(drawer).queryByRole("button", { name: "标记为失败" })).not.toBeInTheDocument();

    fireEvent.click(await within(drawer).findByRole("button", { name: "取消任务" }));
    expect(cancel).not.toHaveBeenCalled();
    const confirm = await screen.findByRole("dialog", { name: "取消该任务？" });
    fireEvent.click(within(confirm).getByRole("button", { name: "取消任务" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith("task-1"));
  });

  it("requires a reason before marking a running task failed", async () => {
    vi.spyOn(API, "listQueueTasks").mockResolvedValue({ items: [task({ status: "running" })], total: 1 });
    vi.spyOn(API, "getQueueTask").mockResolvedValue(detail(task({ status: "running" })));
    const markFailed = vi.spyOn(API, "markQueueTaskFailed").mockResolvedValue(task({ status: "failed" }));
    renderPage("/app/queue?tab=tasks");
    fireEvent.click(await screen.findByRole("button", { name: "打开任务 task-1" }));
    const drawer = await screen.findByRole("dialog", { name: "任务详情" });
    fireEvent.click(await within(drawer).findByRole("button", { name: "标记为失败" }));

    const confirm = await screen.findByRole("dialog", { name: "将该任务标记为失败？" });
    const submit = within(confirm).getByRole("button", { name: "标记失败" });
    expect(submit).toBeDisabled();
    fireEvent.change(within(confirm).getByLabelText("原因"), { target: { value: " stuck " } });
    fireEvent.click(submit);
    await waitFor(() => expect(markFailed).toHaveBeenCalledWith("task-1", "stuck"));
  });

  it("saves limit overrides, sending null for blank fields", async () => {
    const limits = {
      defaults: { image: 4, video: 2, audio: 4 },
      overrides: { image: null, video: null, audio: null },
      effective: [],
    };
    vi.spyOn(API, "getQueueLimits").mockResolvedValue(limits);
    const update = vi.spyOn(API, "updateQueueLimits").mockResolvedValue({
      ...limits,
      overrides: { image: null, video: 6, audio: null },
    });
    renderPage("/app/queue?tab=limits");
    const video = await screen.findByLabelText("视频");
    const save = screen.getByRole("button", { name: "保存上限" });
    expect(save).toBeDisabled();
    fireEvent.change(video, { target: { value: "x" } });
    expect(save).toBeDisabled();
    fireEvent.change(video, { target: { value: "6" } });
    fireEvent.click(save);
    await waitFor(() => expect(update).toHaveBeenCalledWith({ image: null, video: 6, audio: null }));
  });
});
