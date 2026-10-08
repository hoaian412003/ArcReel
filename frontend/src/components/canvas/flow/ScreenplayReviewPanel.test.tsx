import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API, ApiRequestError } from "@/api";
import type { Screenplay } from "@/types/screenplay";
import { ScreenplayReviewPanel } from "./ScreenplayReviewPanel";

function makeScreenplay(overrides: Partial<Screenplay> = {}): Screenplay {
  return {
    episode: 1,
    status: "pending_review",
    fingerprint: "fp1",
    title: "边关来信",
    logline: "一封信带来谜团",
    total_seconds: 20,
    scene_count: 2,
    scene_seconds: [12, 8],
    scenes: [
      { heading: "内景 · 府门 · 日", summary: "老管家送信", estimated_seconds: 12, text: "老管家递上书信。" },
      { heading: "外景 · 官道 · 夜", summary: "夜行", estimated_seconds: 8, text: "他再也没有回来。" },
    ],
    markdown: "# 边关来信",
    approved_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.spyOn(API, "getScreenplay").mockResolvedValue(makeScreenplay());
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function renderPanel(onLoaded?: (s: Screenplay) => void) {
  render(<ScreenplayReviewPanel projectName="demo" episode={1} version={0} onLoaded={onLoaded} />);
  await screen.findByTestId("screenplay-scene-1");
}

describe("ScreenplayReviewPanel", () => {
  it("展示标题、看点、逐场内容与本集预计总时长，并把审阅过的指纹回报给外层", async () => {
    const onLoaded = vi.fn();
    await renderPanel(onLoaded);

    expect(screen.getByText("边关来信")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "编剧报告" })).toBeInTheDocument();
    expect(screen.getByText("一封信带来谜团")).toBeInTheDocument();
    expect(screen.getByTestId("screenplay-total")).toHaveTextContent("20 秒");
    expect(screen.getByTestId("screenplay-total")).toHaveTextContent("2 场");
    expect(screen.getByTestId("screenplay-status")).toHaveTextContent("待审阅");
    expect(within(screen.getByTestId("screenplay-scene-2")).getByDisplayValue("外景 · 官道 · 夜")).toBeInTheDocument();
    expect(onLoaded).toHaveBeenCalledWith(expect.objectContaining({ fingerprint: "fp1" }));
  });

  it("在报告末尾通过当前审阅版本，并把最新状态回报给宿主", async () => {
    const approved = makeScreenplay({ status: "approved", approved_at: "2026-10-05T00:00:00Z" });
    const approve = vi.spyOn(API, "approveScreenplay").mockResolvedValue(approved);
    const onApproved = vi.fn();
    render(
      <ScreenplayReviewPanel
        projectName="demo"
        episode={1}
        version={0}
        onApproved={onApproved}
      />,
    );
    await screen.findByTestId("screenplay-scene-1");

    fireEvent.click(screen.getByRole("button", { name: "通过剧本" }));

    await waitFor(() => expect(approve).toHaveBeenCalledWith("demo", 1, "fp1"));
    await waitFor(() => expect(onApproved).toHaveBeenCalledWith(approved));
    expect(screen.getByRole("button", { name: "已通过" })).toBeDisabled();
  });

  it("尚未编写时给出提示，加载失败时给出错误与重试", async () => {
    vi.spyOn(API, "getScreenplay").mockResolvedValueOnce(
      makeScreenplay({ status: "none", fingerprint: null, scenes: [], total_seconds: 0 }),
    );
    const { unmount } = render(<ScreenplayReviewPanel projectName="demo" episode={1} version={0} />);
    expect(await screen.findByText(/还没有剧本/)).toBeInTheDocument();
    unmount();

    vi.spyOn(API, "getScreenplay").mockRejectedValueOnce(new Error("boom"));
    render(<ScreenplayReviewPanel projectName="demo" episode={1} version={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("只有改动过的场才能保存，保存只提交变动字段并带上基准指纹", async () => {
    const patch = vi.spyOn(API, "patchScreenplay").mockResolvedValue(
      makeScreenplay({
        fingerprint: "fp2",
        total_seconds: 30,
        scenes: [
          { heading: "内景 · 府门 · 日", summary: "老管家送信", estimated_seconds: 12, text: "老管家递上书信。" },
          { heading: "外景 · 官道 · 夜", summary: "夜行", estimated_seconds: 18, text: "他再也没有回来。" },
        ],
      }),
    );
    await renderPanel();
    const scene = screen.getByTestId("screenplay-scene-2");
    const save = within(scene).getByRole("button", { name: "保存本场" });
    expect(save).toBeDisabled();

    fireEvent.change(within(scene).getByLabelText("预计时长（秒）"), { target: { value: "18" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);

    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("demo", 1, "fp1", [
        { op: "update", index: 1, fields: { estimated_seconds: 18 } },
      ]),
    );
    await waitFor(() => expect(screen.getByTestId("screenplay-total")).toHaveTextContent("30 秒"));
  });

  it("预计时长不是合法整数时不能保存并给出提示", async () => {
    const patch = vi.spyOn(API, "patchScreenplay");
    await renderPanel();
    const scene = screen.getByTestId("screenplay-scene-1");

    fireEvent.change(within(scene).getByLabelText("预计时长（秒）"), { target: { value: "0" } });

    expect(within(scene).getByRole("alert")).toHaveTextContent("1 到 600");
    expect(within(scene).getByRole("button", { name: "保存本场" })).toBeDisabled();
    expect(patch).not.toHaveBeenCalled();
  });

  it("基准指纹过期（409）时提示并重新读取最新剧本", async () => {
    vi.spyOn(API, "patchScreenplay").mockRejectedValueOnce(new ApiRequestError("conflict", undefined, 409));
    await renderPanel();
    const scene = screen.getByTestId("screenplay-scene-1");

    fireEvent.change(within(scene).getByLabelText("正文"), { target: { value: "改过的正文" } });
    fireEvent.click(within(scene).getByRole("button", { name: "保存本场" }));

    await waitFor(() => expect(API.getScreenplay).toHaveBeenCalledTimes(2));
  });

  it("有未保存修改时锁住增删场次，避免序号错位", async () => {
    await renderPanel();
    const scene = screen.getByTestId("screenplay-scene-1");

    fireEvent.change(within(scene).getByLabelText("正文"), { target: { value: "改过的正文" } });

    expect(screen.getByRole("button", { name: "添加一场" })).toBeDisabled();
    expect(within(scene).getByRole("button", { name: "删除第 1 场" })).toBeDisabled();
  });

  it("删除一场与添加一场分别提交 delete / insert 操作", async () => {
    const patch = vi.spyOn(API, "patchScreenplay").mockResolvedValue(makeScreenplay({ fingerprint: "fp2" }));
    await renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "删除第 2 场" }));
    await waitFor(() =>
      expect(patch).toHaveBeenLastCalledWith("demo", 1, "fp1", [{ op: "delete", index: 1 }]),
    );

    fireEvent.click(screen.getByRole("button", { name: "添加一场" }));
    await waitFor(() =>
      expect(patch).toHaveBeenLastCalledWith(
        "demo",
        1,
        "fp2",
        [expect.objectContaining({ op: "insert", index: 2 })],
      ),
    );
  });

  it("只剩一场时不能再删除", async () => {
    vi.spyOn(API, "getScreenplay").mockResolvedValue(
      makeScreenplay({
        scenes: [{ heading: "内景 · 府门 · 日", summary: "老管家送信", estimated_seconds: 12, text: "老管家递上书信。" }],
      }),
    );
    await renderPanel();

    expect(screen.getByRole("button", { name: "删除第 1 场" })).toBeDisabled();
  });
});
