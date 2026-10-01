import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { referenceVideoCacheKey, useReferenceVideoStore } from "@/stores/reference-video-store";
import type { H3PromptStatus, ReferenceVideoUnit } from "@/types";
import { H3PromptControls, H3RegenerateAllButton } from "./H3PromptControls";

vi.mock("@/api", () => ({
  API: {
    patchReferenceUnitH3Prompt: vi.fn(),
    regenerateH3Prompts: vi.fn(),
    listReferenceVideoUnits: vi.fn(),
  },
}));

const KEY = referenceVideoCacheKey("demo", 1);

function unit(overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: "E1U1",
    text: "@[张三] 站在 @[酒馆]",
    duration_seconds: 6,
    transition_to_next: "cut",
    note: null,
    ...overrides,
  };
}

function seedStatus(status: H3PromptStatus | null) {
  useReferenceVideoStore.setState({
    h3PromptStatusByEpisode: status ? { [KEY]: { E1U1: status } } : {},
  });
}

describe("H3PromptControls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedStatus(null);
  });

  it("项目不使用 H3 提示词档时不渲染任何入口", () => {
    const { container } = render(<H3PromptControls unit={unit()} projectName="demo" episode={1} />);
    expect(container).toBeEmptyDOMElement();
  });

  it.each([
    ["current", "最新"],
    ["stale", "已过期"],
    ["missing", "未生成"],
  ] as const)("展示服务端给出的状态：%s", (status, label) => {
    seedStatus(status);
    render(<H3PromptControls unit={unit()} projectName="demo" episode={1} />);
    expect(screen.getByText(new RegExp(label))).toBeInTheDocument();
  });

  it("重新生成只点名这一个单元", async () => {
    seedStatus("stale");
    vi.mocked(API.regenerateH3Prompts).mockResolvedValue({ task_id: "t1", deduped: false, unit_ids: ["E1U1"] });
    vi.mocked(API.listReferenceVideoUnits).mockResolvedValue({
      units: [unit()],
      unit_capabilities: {},
      h3_prompt_status: { E1U1: "current" },
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<H3PromptControls unit={unit()} projectName="demo" episode={1} />);
      fireEvent.click(screen.getByRole("button", { name: "重新生成 H3 提示词" }));
      await waitFor(() => expect(API.regenerateH3Prompts).toHaveBeenCalledWith("demo", 1, ["E1U1"]));
      await vi.advanceTimersByTimeAsync(3100);
      await waitFor(() => expect(useReferenceVideoStore.getState().h3PromptStatusByEpisode[KEY]?.E1U1).toBe("current"));
    } finally {
      vi.useRealTimers();
    }
  });

  it("手写保存把六段整体交给后端，校验不过时展示原因并保持打开", async () => {
    seedStatus("missing");
    vi.mocked(API.patchReferenceUnitH3Prompt).mockRejectedValueOnce(new Error("unit E1U1 的 detailed_description 没有分镜标记"));
    render(<H3PromptControls unit={unit()} projectName="demo" episode={1} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑 H3 提示词" }));
    const dialog = screen.getByRole("dialog");
    for (const textarea of within(dialog).getAllByRole("textbox")) {
      fireEvent.change(textarea, { target: { value: "text" } });
    }
    fireEvent.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("没有分镜标记");
    expect(API.patchReferenceUnitH3Prompt).toHaveBeenCalledWith(
      "demo",
      1,
      "E1U1",
      expect.objectContaining({ subject_definitions: "text", non_diegetic_music: "text" }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("六段没写全时不能保存", () => {
    seedStatus("missing");
    render(<H3PromptControls unit={unit()} projectName="demo" episode={1} />);
    fireEvent.click(screen.getByRole("button", { name: "编辑 H3 提示词" }));
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "保存" })).toBeDisabled();
  });
});

describe("H3RegenerateAllButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    seedStatus(null);
  });

  it("没有状态表（非 H3 提示词档）时不渲染", () => {
    const { container } = render(<H3RegenerateAllButton projectName="demo" episode={1} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("不点名单元：重新生成本集全部", async () => {
    seedStatus("stale");
    vi.mocked(API.regenerateH3Prompts).mockResolvedValue({ task_id: "t1", deduped: false, unit_ids: ["E1U1"] });
    vi.mocked(API.listReferenceVideoUnits).mockResolvedValue({
      units: [unit()],
      unit_capabilities: {},
      h3_prompt_status: { E1U1: "current" },
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<H3RegenerateAllButton projectName="demo" episode={1} />);
      fireEvent.click(screen.getByRole("button", { name: "重新生成全部 H3 提示词" }));
      await waitFor(() => expect(API.regenerateH3Prompts).toHaveBeenCalledWith("demo", 1, undefined));
      await vi.advanceTimersByTimeAsync(3100);
    } finally {
      vi.useRealTimers();
    }
  });
});
