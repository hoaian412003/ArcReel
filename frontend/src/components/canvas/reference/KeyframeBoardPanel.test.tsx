import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import type { KeyframeBoard, KeyframeBoardApplicable, KeyframeCell, KeyframeSheet } from "@/types/keyframe";
import type { TaskItem } from "@/types/task";
import { KeyframeBoardPanel } from "./KeyframeBoardPanel";

const enqueueKeyframeSheet = vi.hoisted(() => vi.fn());
vi.mock("@/actions/generation", () => ({ enqueueKeyframeSheet }));

function cell(overrides: Partial<KeyframeCell> = {}): KeyframeCell {
  return {
    id: "K01",
    label: "Vương 近景",
    scene: "车内",
    staging: "Vương 在左，Thăng 在右靠窗",
    framing: "近景，Vương 凑向右侧",
    characters: ["Vương"],
    unit_ids: ["E1U10", "E1U11"],
    status: "draft",
    image: "keyframes/episode_1/K01.png",
    stale: false,
    ...overrides,
  };
}

function sheet(overrides: Partial<KeyframeSheet> = {}, cells: KeyframeCell[] = [cell()]): KeyframeSheet {
  return { id: "S1", scene: "车内", image: "keyframes/episode_1/S1.png", problem: null, cells, ...overrides };
}

function board(overrides: Partial<KeyframeBoardApplicable> = {}): KeyframeBoardApplicable {
  return {
    applicable: true,
    episode: 1,
    has_board: true,
    plan_stale: false,
    units: [
      { unit_id: "E1U10", text: "Vương 嘴角带笑地凑向 Thăng", cell_id: "K01" },
      { unit_id: "E1U12", text: "Thăng 盯着 Vương", cell_id: null },
    ],
    sheets: [sheet()],
    ...overrides,
  };
}

function runningTask(resourceId: string): TaskItem {
  return {
    task_id: "t1",
    project_name: "demo",
    task_type: "keyframe_sheet",
    media_type: "image",
    resource_id: resourceId,
    resource_type: null,
    script_file: null,
    payload: {},
    status: "running",
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-10-07T00:00:00Z",
    started_at: "2026-10-07T00:00:01Z",
    finished_at: null,
    updated_at: "2026-10-07T00:00:02Z",
  };
}

function mockBoard(value: KeyframeBoard) {
  return vi.spyOn(API, "getKeyframeBoard").mockResolvedValue(value);
}

beforeEach(() => {
  enqueueKeyframeSheet.mockReset();
  useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("KeyframeBoardPanel", () => {
  it("项目不适用时只给一句说明", async () => {
    mockBoard({ applicable: false, episode: 1 });
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    expect(await screen.findByText(/只用于采用 H3/)).toBeInTheDocument();
  });

  it("还没有规划时给出可复制的 Agent 指令", async () => {
    mockBoard(board({ has_board: false, sheets: [] }));
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    expect(await screen.findByText("这一集还没有起始帧规划")).toBeInTheDocument();
    expect(screen.getByText("请为第 1 集规划起始帧")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /复制/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("请为第 1 集规划起始帧"));
  });

  it("加载失败时给出错误与重试", async () => {
    vi.spyOn(API, "getKeyframeBoard").mockRejectedValueOnce(new Error("boom")).mockResolvedValue(board());
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    fireEvent.click(screen.getByRole("button", { name: /重试|Retry/i }));
    expect(await screen.findByTestId("keyframe-board")).toBeInTheDocument();
  });

  it("按联合图展示格子：标签、景别、站位、用于哪些单元与审核状态，图片走项目文件地址", async () => {
    mockBoard(board());
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    const card = await screen.findByTestId("keyframe-cell-K01");
    expect(within(card).getByText("Vương 近景")).toBeInTheDocument();
    expect(within(card).getByText("近景，Vương 凑向右侧")).toBeInTheDocument();
    expect(within(card).getByText("Vương 在左，Thăng 在右靠窗")).toBeInTheDocument();
    expect(within(card).getByText("E1U10 · E1U11")).toBeInTheDocument();
    expect(within(card).getByText("待审核")).toBeInTheDocument();
    expect(within(card).getByRole("img", { name: "Vương 近景" })).toHaveAttribute(
      "src", expect.stringContaining("/files/demo/keyframes/episode_1/K01.png"),
    );
    expect(screen.getByTestId("keyframe-sheet-S1")).toHaveTextContent("车内");
  });

  it("通过一格：调用审核接口并用返回的看板刷新", async () => {
    mockBoard(board());
    const approved = board({ sheets: [sheet({}, [cell({ status: "approved" })])] });
    const review = vi.spyOn(API, "reviewKeyframeCell").mockResolvedValue(approved);
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /^通过$/ }));

    await waitFor(() => expect(review).toHaveBeenCalledWith("demo", 1, "K01", true));
    expect(await screen.findByRole("button", { name: /取消通过/ })).toBeInTheDocument();
    expect(within(screen.getByTestId("keyframe-cell-K01")).getByText("已通过")).toBeInTheDocument();
  });

  it("一格在审核时只有它自己的按钮进入加载态，其他格子与生成按钮不受影响", async () => {
    const two = board({
      sheets: [sheet({}, [cell(), cell({ id: "K02", label: "Thăng 近景", unit_ids: ["E1U12"] })])],
    });
    mockBoard(two);
    let finish: (value: KeyframeBoard) => void = () => undefined;
    vi.spyOn(API, "reviewKeyframeCell").mockReturnValue(new Promise<KeyframeBoard>((resolve) => (finish = resolve)));
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    const first = within(await screen.findByTestId("keyframe-cell-K01")).getByRole("button", { name: /^通过$/ });
    fireEvent.click(first);

    await waitFor(() => expect(first).toBeDisabled());
    expect(within(screen.getByTestId("keyframe-cell-K02")).getByRole("button", { name: /^通过$/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /重新生成联合图/ })).toBeEnabled();
    expect(screen.getAllByRole("combobox").every((select) => !(select as HTMLSelectElement).disabled)).toBe(true);
    finish(two);
  });

  it("全部通过：一次调用批量审核接口，没有可通过的格子时禁用", async () => {
    const draft = board({ sheets: [sheet({}, [cell(), cell({ id: "K02", image: null, status: "pending" })])] });
    mockBoard(draft);
    const approveAll = vi
      .spyOn(API, "approveAllKeyframeCells")
      .mockResolvedValue(board({ sheets: [sheet({}, [cell({ status: "approved" }), cell({ id: "K02", image: null, status: "pending" })])] }));
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /全部通过（1 格）/ }));

    await waitFor(() => expect(approveAll).toHaveBeenCalledWith("demo", 1));
    expect(await screen.findByRole("button", { name: /全部通过（0 格）/ })).toBeDisabled();
  });

  it("生成全部：没有图时直接为每张可生成的联合图入队，素材不齐的跳过", async () => {
    mockBoard(
      board({
        sheets: [
          sheet({ id: "S1", image: null }, [cell({ id: "K01", image: null, status: "pending" })]),
          sheet({ id: "S2", image: null, problem: { code: "keyframe_scene_sheet_missing", name: "车内" } }, [
            cell({ id: "K02", image: null, status: "pending" }),
          ]),
          sheet({ id: "S3", image: null }, [cell({ id: "K03", image: null, status: "pending" })]),
        ],
      }),
    );
    enqueueKeyframeSheet.mockResolvedValue({ taskIds: ["t"], deduped: false });
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /生成全部（2 张）/ }));

    await waitFor(() => expect(enqueueKeyframeSheet).toHaveBeenCalledTimes(2));
    expect(enqueueKeyframeSheet.mock.calls.map((call) => call[2])).toEqual(["S1", "S3"]);
  });

  it("生成全部会覆盖已有图，先确认再入队", async () => {
    mockBoard(board());
    enqueueKeyframeSheet.mockResolvedValue({ taskIds: ["t"], deduped: false });
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /生成全部（1 张）/ }));
    expect(enqueueKeyframeSheet).not.toHaveBeenCalled();

    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /生成全部（1 张）/ }));

    await waitFor(() => expect(enqueueKeyframeSheet).toHaveBeenCalledWith("demo", 1, "S1"));
  });

  it("没有图或素材已变的格子不能通过，并说明原因", async () => {
    mockBoard(
      board({
        sheets: [
          sheet({}, [
            cell({ id: "K01", status: "pending", image: null }),
            cell({ id: "K02", label: "Thăng 近景", unit_ids: ["E1U12"], stale: true }),
          ]),
        ],
      }),
    );
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    const none = await screen.findByTestId("keyframe-cell-K01");
    expect(within(none).getByRole("button", { name: /^通过$/ })).toBeDisabled();
    expect(within(none).getByText("先生成联合图")).toBeInTheDocument();
    const stale = screen.getByTestId("keyframe-cell-K02");
    expect(within(stale).getByRole("button", { name: /^通过$/ })).toBeDisabled();
    expect(within(stale).getByText("素材已变，请重新生成")).toBeInTheDocument();
  });

  it("素材不齐的联合图说明缺哪份，且不能生成", async () => {
    mockBoard(board({ sheets: [sheet({ problem: { code: "keyframe_scene_sheet_missing", name: "车内" } })] }));
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    expect(await screen.findByTestId("keyframe-problem-S1")).toHaveTextContent("缺少场景「车内」的资产图");
    expect(screen.getByRole("button", { name: /重新生成联合图/ })).toBeDisabled();
  });

  it("查看提示词与参考图：按图 N 顺序列出参考图并显示提示词", async () => {
    mockBoard(board());
    const request = vi.spyOn(API, "getKeyframeSheetRequest").mockResolvedValue({
      sheet_id: "S1",
      problem: null,
      prompt: "一张 2×2 联合图",
      aspect_ratio: "16:9",
      references: [
        { label: "图1为场景「车内」的参考", path: "scenes/车内.png" },
        { label: "图2为角色「Vương」的脸部特写参考", path: "characters/derivatives/Vương/Face.png" },
      ],
    });
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /查看提示词与参考图/ }));

    const dialog = await screen.findByTestId("keyframe-request-dialog");
    expect(request).toHaveBeenCalledWith("demo", 1, "S1", expect.anything());
    expect(await within(dialog).findByTestId("keyframe-request-prompt")).toHaveTextContent("一张 2×2 联合图");
    const references = within(within(dialog).getByTestId("keyframe-request-references")).getAllByRole("img");
    expect(references.map((img) => img.getAttribute("alt"))).toEqual([
      "图1为场景「车内」的参考",
      "图2为角色「Vương」的脸部特写参考",
    ]);
  });

  it("素材缺失时查看输入会说明缺哪份而不显示提示词", async () => {
    mockBoard(board());
    vi.spyOn(API, "getKeyframeSheetRequest").mockResolvedValue({
      sheet_id: "S1",
      problem: { code: "keyframe_scene_sheet_missing", name: "车内" },
    });
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /查看提示词与参考图/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("车内");
    expect(screen.queryByTestId("keyframe-request-prompt")).not.toBeInTheDocument();
  });

  it("生成联合图经入队动作层，资源 id 是 episode_N/S1", async () => {
    mockBoard(board({ sheets: [sheet({ image: null }, [cell({ status: "pending", image: null })])] }));
    enqueueKeyframeSheet.mockResolvedValue({ taskIds: ["t1"], deduped: false });
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /^生成联合图$/ }));

    await waitFor(() => expect(enqueueKeyframeSheet).toHaveBeenCalledWith("demo", 1, "S1"));
  });

  it("联合图在生成时禁用按钮，任务结束后重读看板", async () => {
    const load = mockBoard(board());
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);
    await screen.findByTestId("keyframe-board");
    expect(load).toHaveBeenCalledTimes(1);

    act(() => useTasksStore.setState({ tasks: [runningTask("episode_1/S1")] }));
    expect(await screen.findByRole("button", { name: /生成中/ })).toBeDisabled();

    act(() => useTasksStore.setState({ tasks: [] }));
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });

  it("把单元改挂到另一格或取消，调用改挂接口并刷新", async () => {
    mockBoard(board());
    const assign = vi.spyOn(API, "assignKeyframeUnit").mockResolvedValue(board());
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    const select = await screen.findByLabelText("单元 E1U12 使用的起始帧");
    fireEvent.change(select, { target: { value: "K01" } });
    await waitFor(() => expect(assign).toHaveBeenCalledWith("demo", 1, "E1U12", "K01"));

    const assigned = screen.getByLabelText("单元 E1U10 使用的起始帧");
    expect(assigned).toHaveValue("K01");
    fireEvent.change(assigned, { target: { value: "" } });
    await waitFor(() => expect(assign).toHaveBeenCalledWith("demo", 1, "E1U10", null));
  });

  it("规划过期时提示，但格子照常显示", async () => {
    mockBoard(board({ plan_stale: true }));
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    expect(await screen.findByRole("status")).toHaveTextContent("单元内容或导演说明又有改动");
    expect(screen.getByTestId("keyframe-cell-K01")).toBeInTheDocument();
  });

  it("审核失败时弹错误并重读看板", async () => {
    const load = mockBoard(board());
    vi.spyOn(API, "reviewKeyframeCell").mockRejectedValue(new Error("stale"));
    render(<KeyframeBoardPanel projectName="demo" episode={1} />);

    fireEvent.click(await screen.findByRole("button", { name: /^通过$/ }));

    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });
});
