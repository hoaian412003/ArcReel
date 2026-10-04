import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { referenceVideoCacheKey, useReferenceVideoStore } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { CreateVoiceoverButton, type CreateVoiceoverButtonProps } from "./CreateVoiceoverButton";
import type { ProjectData, ReferenceVideoUnit } from "@/types";

const BASE_PROJECT: ProjectData = {
  title: "p",
  content_mode: "narration",
  style: "",
  episodes: [],
  characters: {},
  scenes: {},
  props: {},
};
const DUB_PROJECT: ProjectData = { ...BASE_PROJECT, dub_target_language: "vi" };

function unit(id: string, hasVideo: boolean): ReferenceVideoUnit {
  return {
    unit_id: id,
    text: "{你好}",
    duration_seconds: 4,
    transition_to_next: "cut",
    note: null,
    ...(hasVideo
      ? { generated_assets: { video_clip: `reference_videos/${id}.mp4` } as ReferenceVideoUnit["generated_assets"] }
      : {}),
  };
}

const queued = (ids: string[]) => ({ success: true, task_ids: ids.map((id) => `t-${id}`), unit_ids: ids, skipped: [] });

describe("CreateVoiceoverButton", () => {
  beforeEach(() => {
    useTasksStore.setState({ tasks: [] });
    useReferenceVideoStore.setState({
      unitsByEpisode: {
        [referenceVideoCacheKey("proj", 1)]: [unit("E1U1", true), unit("E1U2", true), unit("E1U3", false)],
      },
      checkedUnitIdsByEpisode: {},
    });
    useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: DUB_PROJECT });
  });

  afterEach(() => vi.restoreAllMocks());

  const mount = (props: Partial<CreateVoiceoverButtonProps> = {}) =>
    render(<CreateVoiceoverButton projectName="proj" episode={1} fallbackUnitId="E1U1" {...props} />);

  describe("turning voice-over on", () => {
    it("enables it first when the project has it off, and does not queue anything yet", async () => {
      useProjectsStore.setState({ currentProjectData: BASE_PROJECT });
      const update = vi.spyOn(API, "updateProject").mockResolvedValue({ success: true, project: BASE_PROJECT });
      vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("ok" as never);
      const auto = vi.spyOn(API, "autoDubVoiceover");
      mount();

      fireEvent.click(screen.getByRole("button", { name: /Enable Vietnamese voice-over|开启越南语配音/ }));

      await waitFor(() => expect(update).toHaveBeenCalledWith("proj", { dub_target_language: "vi" }));
      expect(auto).not.toHaveBeenCalled();
    });
  });

  describe("in the dialogue pane (one video)", () => {
    it("runs the pipeline for the current video when nothing is checked", async () => {
      const auto = vi.spyOn(API, "autoDubVoiceover").mockResolvedValue(queued(["E1U1"]));
      mount();

      fireEvent.click(screen.getByRole("button", { name: /^(Create voice-over|创建配音)$/ }));

      await waitFor(() => expect(auto).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1"] }));
    });

    it("runs it for every checked video and shows the count", async () => {
      useReferenceVideoStore.getState().setChecked("proj", 1, ["E1U2", "E1U3"]);
      const auto = vi.spyOn(API, "autoDubVoiceover").mockResolvedValue(queued(["E1U2"]));
      mount();

      fireEvent.click(screen.getByRole("button", { name: /Create voice-over \(2\)|创建配音（2）/ }));

      await waitFor(() => expect(auto).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U2", "E1U3"] }));
    });
  });

  describe("in the canvas header (the whole episode)", () => {
    it("covers every video that exists when nothing is checked, and skips the ones without a video", async () => {
      const auto = vi.spyOn(API, "autoDubVoiceover").mockResolvedValue(queued(["E1U1", "E1U2"]));
      mount({ scope: "episode", fallbackUnitId: undefined });

      fireEvent.click(screen.getByRole("button", { name: /Auto voice-over \(2\)|自动配音（2）/ }));

      await waitFor(() => expect(auto).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1", "E1U2"] }));
    });

    it("covers only the checked videos when some are checked", async () => {
      useReferenceVideoStore.getState().setChecked("proj", 1, ["E1U2"]);
      const auto = vi.spyOn(API, "autoDubVoiceover").mockResolvedValue(queued(["E1U2"]));
      mount({ scope: "episode", fallbackUnitId: undefined });

      fireEvent.click(screen.getByRole("button", { name: /Auto voice-over \(1\)|自动配音（1）/ }));

      await waitFor(() => expect(auto).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U2"] }));
    });

    it("is disabled, with an explanation, when no video exists yet", () => {
      useReferenceVideoStore.setState({
        unitsByEpisode: { [referenceVideoCacheKey("proj", 1)]: [unit("E1U3", false)] },
      });
      mount({ scope: "episode", fallbackUnitId: undefined });

      const button = screen.getByRole("button", { name: /Auto voice-over|自动配音/ });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("title", expect.stringMatching(/no videos|没有可配音/));
    });
  });

  it.each(["dub_auto", "dub_tts", "text_reference_dub_lines"])("is disabled while a %s task is running", (taskType) => {
    useTasksStore.setState({
      tasks: [{ task_id: "t", project_name: "proj", task_type: taskType, status: "running" }] as never,
    });
    mount();

    expect(screen.getByRole("button", { name: /^(Create voice-over|创建配音)$/ })).toBeDisabled();
  });

  it("shows the server's refusal instead of failing silently", async () => {
    vi.spyOn(API, "autoDubVoiceover").mockRejectedValue(new Error("VieNeu is off"));
    mount();

    fireEvent.click(screen.getByRole("button", { name: /^(Create voice-over|创建配音)$/ }));

    await waitFor(() => expect(screen.getByRole("button", { name: /^(Create voice-over|创建配音)$/ })).toBeEnabled());
  });
});
