import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { EpisodeSourceReview } from "./EpisodeSourceReview";
import type { EpisodeMeta } from "@/types";

function makeEpisode(overrides: Partial<EpisodeMeta> = {}): EpisodeMeta {
  return {
    episode: 1,
    title: "第一章：初遇",
    script_file: "scripts/episode_1.json",
    source_range: { source_file: "source/episode_1.txt", start: 100, end: 340 },
    outline: { story_beats: ["主角登场", "遭遇冲突"] },
    hook: "反派现身",
    ...overrides,
  };
}

describe("EpisodeSourceReview", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders header meta, guide beats/hook, and the loaded source text", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("这是本集源文……");

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    expect(screen.getByText("第一章：初遇")).toBeInTheDocument();
    expect(screen.getByText("脚本未生成")).toBeInTheDocument();
    expect(screen.getByText("episode_1.txt")).toBeInTheDocument();
    expect(screen.getByText("100–340")).toBeInTheDocument();
    expect(screen.getByText("约 240 字")).toBeInTheDocument();
    expect(screen.getByText("主角登场")).toBeInTheDocument();
    expect(screen.getByText("遭遇冲突")).toBeInTheDocument();
    expect(screen.getByText("反派现身")).toBeInTheDocument();

    expect(screen.getByText("正在加载源文切片…")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("这是本集源文……")).toBeInTheDocument();
    });
    expect(API.getSourceContent).toHaveBeenCalledWith("demo", "episode_1.txt");
  });

  it("shows a not-found message when the source slice fetch fails", async () => {
    vi.spyOn(API, "getSourceContent").mockRejectedValue(new Error("404"));

    render(
      <EpisodeSourceReview
        projectName="demo"
        episode={2}
        episodes={[makeEpisode({ episode: 2, outline: undefined, hook: undefined, source_range: undefined })]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("未找到本集源文切片（source/episode_2.txt）")).toBeInTheDocument();
    });
  });

  it("does not render the guide section when there are no beats or hook", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");

    render(
      <EpisodeSourceReview
        projectName="demo"
        episode={3}
        episodes={[makeEpisode({ episode: 3, outline: undefined, hook: undefined })]}
      />,
    );

    expect(screen.queryByText("本集导览")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("text")).toBeInTheDocument();
    });
  });

  it("collapses and re-expands the guide section", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);

    expect(screen.getByText("主角登场")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /本集导览/ }));
    expect(screen.queryByText("主角登场")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /本集导览/ }));
    expect(screen.getByText("主角登场")).toBeInTheDocument();
  });

  it("resets the collapsed guide section when switching to a different episode", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("text");
    const episodes = [makeEpisode(), makeEpisode({ episode: 2, outline: { story_beats: ["新的一集"] } })];

    const { rerender } = render(
      <EpisodeSourceReview projectName="demo" episode={1} episodes={episodes} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /本集导览/ }));
    expect(screen.queryByText("主角登场")).not.toBeInTheDocument();

    rerender(<EpisodeSourceReview projectName="demo" episode={2} episodes={episodes} />);

    expect(screen.getByText("新的一集")).toBeInTheDocument();
  });

  it("switches the body between the source slice and the screenplay", async () => {
    vi.spyOn(API, "getSourceContent").mockResolvedValue("这是本集源文……");
    const getScreenplay = vi.spyOn(API, "getScreenplay").mockResolvedValue({
      episode: 1,
      status: "pending_review",
      fingerprint: "fp",
      title: "初遇",
      total_seconds: 12,
      scenes: [{ heading: "内景 · 茶馆 · 夜", summary: "主角登场", estimated_seconds: 12, text: "他推门而入。" }],
    });

    render(<EpisodeSourceReview projectName="demo" episode={1} episodes={[makeEpisode()]} />);
    await waitFor(() => expect(screen.getByText("这是本集源文……")).toBeInTheDocument());
    expect(getScreenplay).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "剧本" }));
    await waitFor(() => expect(screen.getByDisplayValue("内景 · 茶馆 · 夜")).toBeInTheDocument());
    expect(getScreenplay).toHaveBeenCalledWith("demo", 1, expect.anything());
    expect(screen.queryByText("这是本集源文……")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "原文" }));
    expect(screen.getByText("这是本集源文……")).toBeInTheDocument();
  });
});
