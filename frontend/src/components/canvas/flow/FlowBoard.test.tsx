import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, ApiRequestError } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { DirectionReview, FlowBoard as FlowBoardData } from "@/types/flow";
import type { Screenplay } from "@/types/screenplay";
import { FlowBoard } from "./FlowBoard";

vi.mock("@/components/canvas/timeline/ScriptReviewGate", () => ({
  ScriptReviewGate: ({ episode }: { episode: number }) => <div data-testid="script-review-gate">gate-{episode}</div>,
}));

const COLUMN_IDS = [
  ["script_plan", "agent"],
  ["script_plan_review", "human"],
  ["direction", "agent"],
  ["direction_review", "human"],
  ["prompts", "agent"],
  ["assets", "agent"],
  ["video", "agent"],
  ["done", "agent"],
] as const;

function makeBoard(): FlowBoardData {
  return {
    columns: COLUMN_IDS.map(([id, kind]) => ({ id, kind })),
    cards: [
      { episode: 1, title: "雨夜", column: "script_plan_review", next_action: "review", blocked: false },
      { episode: 2, title: "归来", column: "direction_review", next_action: "review", blocked: true },
      { episode: 3, title: "重逢", column: "video", next_action: "generate", blocked: false },
      { episode: 4, title: "初遇", column: "script_plan_review", next_action: "review_screenplay", blocked: false },
    ],
  };
}

const REVIEW: DirectionReview = {
  episode: 2,
  status: "pending_review",
  fingerprint: "fp",
  decided_at: null,
  note: null,
  report: {
    episode_intent: "建立压迫感",
    tone: "冷峻",
    arc: [{ beat: "开场", units: ["U1"] }],
    rows: [
      {
        id: "U1",
        description: "阿离立于屋檐下",
        camera: { angle: "低角度", shot_size: "中景", movement: "推", intent: "低角度：体现气势" },
        performance: [{ character: "阿离", note: "克制" }],
        continuity_in: "雨夜",
        continuity_out: "入屋",
      },
    ],
    angle_distribution: { 低角度: 1 },
    warnings: [{ code: "static", entry_ids: ["U1"], message: "镜头过于静态" }],
  },
};

const SCREENPLAY: Screenplay = {
  episode: 4,
  status: "pending_review",
  fingerprint: "sp-fp",
  title: "初遇",
  logline: "两人在雨中相遇",
  total_seconds: 12,
  scenes: [{ heading: "外景 · 街头 · 雨夜", summary: "相遇", estimated_seconds: 12, text: "雨落在伞面上。" }],
};

const dataTransfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" };

function drag(cardEl: HTMLElement, columnId: string) {
  fireEvent.dragStart(cardEl, { dataTransfer });
  const column = screen.getByTestId(`flow-column-${columnId}`);
  fireEvent.dragOver(column, { dataTransfer });
  fireEvent.drop(column, { dataTransfer });
}

beforeEach(() => {
  useProjectsStore.setState({ currentProjectData: { content_mode: "drama" } as never });
  useAssistantStore.getState().setInput("");
  vi.spyOn(API, "getFlowBoard").mockResolvedValue(makeBoard());
  vi.spyOn(API, "getDirectionReview").mockResolvedValue(REVIEW);
  vi.spyOn(API, "getScreenplay").mockResolvedValue(SCREENPLAY);
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function renderBoard() {
  render(<FlowBoard projectName="demo" />);
  await screen.findByTestId("flow-card-1");
}

describe("FlowBoard", () => {
  it("按列渲染卡片，列头带数量，人工列有标记，阻塞卡片有提示", async () => {
    await renderBoard();
    expect(screen.getAllByRole("region")).toHaveLength(8);
    expect(screen.getByTestId("flow-column-script_plan_review")).toContainElement(screen.getByTestId("flow-card-1"));
    expect(screen.getByTestId("flow-column-direction_review")).toContainElement(screen.getByTestId("flow-card-2"));
    expect(screen.getByTestId("flow-column-video")).toContainElement(screen.getByTestId("flow-card-3"));
    expect(screen.getByTestId("flow-column-script_plan_review")).toHaveAttribute("data-kind", "human");
    expect(screen.getByTestId("flow-column-direction")).toHaveAttribute("data-kind", "agent");
    expect(screen.getByTestId("flow-card-2")).toHaveTextContent("被阻塞");
    expect(screen.getByTestId("flow-card-1")).toHaveAttribute("draggable", "true");
    expect(screen.getByTestId("flow-card-3")).toHaveAttribute("draggable", "false");
  });

  it("加载失败给出错误与重试，空项目给出空态", async () => {
    vi.spyOn(API, "getFlowBoard").mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce({
      columns: makeBoard().columns,
      cards: [],
    });
    render(<FlowBoard projectName="demo" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText(/还没有分集/)).toBeInTheDocument();
  });

  it("拖到下一列：direction_review 通过", async () => {
    const decide = vi.spyOn(API, "decideDirectionReview").mockResolvedValue({ ...REVIEW, status: "approved" });
    await renderBoard();
    drag(screen.getByTestId("flow-card-2"), "prompts");
    await waitFor(() => expect(decide).toHaveBeenCalledWith("demo", 2, { decision: "approve" }));
    await waitFor(() => expect(API.getFlowBoard).toHaveBeenCalledTimes(2));
  });

  it("拖到下一列：script_plan_review 确认，遇到 409 走覆盖确认", async () => {
    const overwrite = { revision: "rev1", entries: [], storyboard_count: 0, video_count: 0 };
    const confirm = vi
      .spyOn(API, "confirmScriptReview")
      .mockRejectedValueOnce(new ApiRequestError("exists", { script_overwrite: overwrite }, 409))
      .mockResolvedValueOnce({} as never);
    await renderBoard();
    drag(screen.getByTestId("flow-card-1"), "direction");
    await waitFor(() => expect(confirm).toHaveBeenCalledWith("demo", 1, { overwriteRevision: undefined }));
    fireEvent.click(await screen.findByRole("button", { name: /确认覆盖|覆盖/ }));
    await waitFor(() => expect(confirm).toHaveBeenLastCalledWith("demo", 1, { overwriteRevision: "rev1" }));
  });

  it("非法落点被拒绝并提示，不调用接口", async () => {
    const decide = vi.spyOn(API, "decideDirectionReview");
    const confirm = vi.spyOn(API, "confirmScriptReview");
    const toast = vi.spyOn(useAppStore.getState(), "pushToast");
    await renderBoard();
    drag(screen.getByTestId("flow-card-1"), "video");
    drag(screen.getByTestId("flow-card-3"), "done");
    expect(decide).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    expect(toast).toHaveBeenCalledTimes(2);
    expect(toast).toHaveBeenNthCalledWith(1, expect.stringContaining("下一列"), "warning");
    expect(toast).toHaveBeenNthCalledWith(2, expect.stringContaining("审阅列"), "warning");
  });

  it("失败时提示错误并保留卡片位置", async () => {
    vi.spyOn(API, "decideDirectionReview").mockRejectedValue(new Error("nope"));
    const toast = vi.spyOn(useAppStore.getState(), "pushToast");
    await renderBoard();
    drag(screen.getByTestId("flow-card-2"), "prompts");
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining("nope"), "error"));
    expect(screen.getByTestId("flow-column-direction_review")).toContainElement(screen.getByTestId("flow-card-2"));
  });

  it("拖回上一列：弹出备注框，记录 redo，预填助手输入并打开助手", async () => {
    const decide = vi.spyOn(API, "decideDirectionReview").mockResolvedValue({ ...REVIEW, status: "changes_requested" });
    await renderBoard();
    drag(screen.getByTestId("flow-card-2"), "direction");
    const note = await screen.findByLabelText(/备注/);
    fireEvent.change(note, { target: { value: "开场太静" } });
    fireEvent.click(screen.getByRole("button", { name: "发起重做" }));
    await waitFor(() => expect(decide).toHaveBeenCalledWith("demo", 2, { decision: "redo", note: "开场太静" }));
    await waitFor(() => expect(useAssistantStore.getState().input).toContain("开场太静"));
    expect(useAssistantStore.getState().input).toContain("2");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });

  it("剧本规划的重做不调用决定接口，只预填对话", async () => {
    const decide = vi.spyOn(API, "decideDirectionReview");
    await renderBoard();
    drag(screen.getByTestId("flow-card-1"), "script_plan");
    fireEvent.click(await screen.findByRole("button", { name: "发起重做" }));
    await waitFor(() => expect(useAssistantStore.getState().input).toContain("剧本规划"));
    expect(decide).not.toHaveBeenCalled();
  });

  it("剧本规划卡片的抽屉复用审阅面板，并提供通过按钮", async () => {
    const confirm = vi.spyOn(API, "confirmScriptReview").mockResolvedValue({} as never);
    await renderBoard();
    fireEvent.click(within1("flow-card-1"));
    expect(await screen.findByTestId("script-review-gate")).toHaveTextContent("gate-1");
    fireEvent.click(screen.getByRole("button", { name: "通过" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith("demo", 1, { overwriteRevision: undefined }));
  });

  it("导演报告抽屉展示意图、镜头意图、表演、衔接、机位分布与警告，按钮可请 Agent 重做", async () => {
    await renderBoard();
    fireEvent.click(within1("flow-card-2"));
    expect(await screen.findByText("建立压迫感")).toBeInTheDocument();
    expect(screen.getByText("冷峻")).toBeInTheDocument();
    expect(screen.getByText("低角度：体现气势")).toBeInTheDocument();
    expect(screen.getByText("克制")).toBeInTheDocument();
    expect(screen.getByText("入屋")).toBeInTheDocument();
    expect(screen.getByText("镜头过于静态")).toBeInTheDocument();
    expect(screen.getByText("机位分布")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "请 Agent 重做" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("agent 列卡片的抽屉只有状态行，没有审阅按钮；Esc 关闭", async () => {
    await renderBoard();
    fireEvent.click(within1("flow-card-3"));
    expect(await screen.findByText(/由 Agent 处理|暂无需审阅/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "通过" })).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(screen.queryByText(/暂无需审阅/)).not.toBeInTheDocument();
  });
});

describe("FlowBoard · 剧本审阅", () => {
  it("剧本卡片的抽屉展示可编辑剧本，「通过」带上审阅过的那一版指纹", async () => {
    const approve = vi.spyOn(API, "approveScreenplay").mockResolvedValue({ ...SCREENPLAY, status: "approved" });
    await renderBoard();
    fireEvent.click(within1("flow-card-4"));
    expect(await screen.findByTestId("screenplay-scene-1")).toBeInTheDocument();
    expect(screen.queryByTestId("script-review-gate")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "通过" }));

    await waitFor(() => expect(approve).toHaveBeenCalledWith("demo", 4, "sp-fp"));
  });

  it("没打开抽屉直接拖到下一列：按当前剧本的指纹通过", async () => {
    const approve = vi.spyOn(API, "approveScreenplay").mockResolvedValue({ ...SCREENPLAY, status: "approved" });
    const confirm = vi.spyOn(API, "confirmScriptReview");
    await renderBoard();

    drag(screen.getByTestId("flow-card-4"), "direction");

    await waitFor(() => expect(approve).toHaveBeenCalledWith("demo", 4, "sp-fp"));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("重写剧本不调用决定接口，只预填对话", async () => {
    const approve = vi.spyOn(API, "approveScreenplay");
    await renderBoard();
    drag(screen.getByTestId("flow-card-4"), "script_plan");
    fireEvent.click(await screen.findByRole("button", { name: "发起重做" }));
    await waitFor(() => expect(useAssistantStore.getState().input).toContain("重写第 4 集的剧本"));
    expect(approve).not.toHaveBeenCalled();
  });
});

function within1(testId: string): HTMLElement {
  return screen.getByTestId(testId).querySelector("button") as HTMLElement;
}