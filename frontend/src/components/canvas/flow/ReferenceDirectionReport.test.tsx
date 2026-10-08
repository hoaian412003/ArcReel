import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import type { DirectionReview } from "@/types/flow";
import { DirectionReviewPanel } from "./DirectionReviewPanel";

const REFERENCE_REVIEW: DirectionReview = {
  episode: 1,
  status: "pending_review",
  fingerprint: "fp",
  decided_at: null,
  note: null,
  report: {
    route: "reference",
    episode_intent: "归来",
    tone: "克制",
    arc: [{ beat: "开场", units: ["1"] }],
    rows: [
      {
        id: "E1U01",
        description: "@[阿离]{我回来了。}",
        duration_seconds: 8,
        unit_type: "character_intro",
        brief: "阿离回到山门，既想念又怕被认出。",
        shots: [
          {
            at_seconds: 0,
            framing: "全景，阿离居中",
            camera: { type: "static_shot", amplitude: "medium", speed: "normal" },
            action: "推开山门",
            why_cut: "",
          },
          {
            at_seconds: 4,
            framing: "近景",
            camera: { type: "push_in", amplitude: "small", speed: "slow" },
            action: "低声开口",
            why_cut: "看清表情",
          },
        ],
        performance: [{ character: "阿离", note: "迟疑", voice: "清亮偏低" }],
        lines: [{ speaker: "阿离", text: "我回来了。", shot: 2, delivery: "轻声" }],
        sounds: "门轴吱呀",
        music: "",
        must_not_show: "",
        end_frame: "阿离停在门槛内",
        continuity_in: "山门外",
        continuity_out: "山门内",
      },
    ],
    camera_distribution: { static_shot: 1, push_in: 1 },
    warnings: [],
  },
};

beforeEach(() => {
  vi.spyOn(API, "getDirectionReview").mockResolvedValue(REFERENCE_REVIEW);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DirectionReviewPanel · 参考生视频", () => {
  it("按单元展示镜头表、表演与声音、台词落位、配乐与运镜分布", async () => {
    render(<DirectionReviewPanel projectName="demo" episode={1} version={0} />);

    const unit = await screen.findByTestId("flow-dr-unit-E1U01");
    expect(within(unit).getByText("阿离回到山门，既想念又怕被认出。")).toBeInTheDocument();
    expect(within(unit).getByText("push_in (small, slow)")).toBeInTheDocument();
    expect(within(unit).getByText("static_shot")).toBeInTheDocument();
    expect(within(unit).getByText(/切镜：看清表情/)).toBeInTheDocument();
    expect(within(unit).getByText(/声音: 清亮偏低/)).toBeInTheDocument();
    expect(within(unit).getByText("第 2 镜")).toBeInTheDocument();
    expect(within(unit).getByText(/「我回来了。」/)).toBeInTheDocument();
    expect(within(unit).getByText("无配乐")).toBeInTheDocument();
    expect(screen.getByText("运镜分布")).toBeInTheDocument();
    expect(screen.queryByText("机位分布")).not.toBeInTheDocument();
  });

  it("导演写的是自由文本时只展示这段文字，不再拆成镜头表与表演", async () => {
    const row = {
      ...REFERENCE_REVIEW.report!.rows[0],
      direction: "第 0 秒全景，阿离推开山门。\n第 4 秒切近景，她低声说「我回来了。」",
    };
    vi.spyOn(API, "getDirectionReview").mockResolvedValue({
      ...REFERENCE_REVIEW,
      report: { ...REFERENCE_REVIEW.report!, rows: [row] } as never,
    });
    render(<DirectionReviewPanel projectName="demo" episode={1} version={0} />);

    const unit = await screen.findByTestId("flow-dr-unit-E1U01");
    expect(within(unit).getByText(/第 0 秒全景，阿离推开山门。/)).toBeInTheDocument();
    expect(within(unit).queryByText("push_in (small, slow)")).not.toBeInTheDocument();
    expect(within(unit).queryByText("无配乐")).not.toBeInTheDocument();
  });
});
