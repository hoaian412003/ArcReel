import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { referenceVideoCacheKey, useReferenceVideoStore } from "@/stores/reference-video-store";
import { useTasksStore } from "@/stores/tasks-store";
import { CreateVoiceoverButton } from "./CreateVoiceoverButton";
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

function unit(id: string): ReferenceVideoUnit {
  return {
    unit_id: id,
    text: "{你好}",
    duration_seconds: 4,
    transition_to_next: "cut",
    note: null,
    dub_lines: [{ index: 0, speaker: null, gender: "male", source_text: "你好", text_vi: "Xin chào", start_seconds: null }],
  };
}

describe("CreateVoiceoverButton", () => {
  beforeEach(() => {
    useTasksStore.setState({ tasks: [] });
    useReferenceVideoStore.setState({
      unitsByEpisode: { [referenceVideoCacheKey("proj", 1)]: [unit("E1U1"), unit("E1U2"), unit("E1U3")] },
      checkedUnitIdsByEpisode: {},
    });
  });

  afterEach(() => vi.restoreAllMocks());

  const mount = () => render(<CreateVoiceoverButton projectName="proj" episode={1} fallbackUnitId="E1U1" />);

  it("turns dubbing on first when the project has it off", async () => {
    useProjectsStore.setState({ currentProjectName: "proj", currentProjectData: BASE_PROJECT });
    const update = vi.spyOn(API, "updateProject").mockResolvedValue({ success: true, project: BASE_PROJECT });
    vi.spyOn(useProjectsStore.getState(), "refreshProject").mockResolvedValue("ok" as never);
    const synth = vi.spyOn(API, "synthesizeDub");
    mount();

    fireEvent.click(screen.getByRole("button", { name: /Enable Vietnamese voice-over|开启越南语配音/ }));

    await waitFor(() => expect(update).toHaveBeenCalledWith("proj", { dub_target_language: "vi" }));
    expect(synth).not.toHaveBeenCalled();
  });

  it("creates the voice-over for the current unit when nothing is checked", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: { ...BASE_PROJECT, dub_target_language: "vi" },
    });
    const synth = vi
      .spyOn(API, "synthesizeDub")
      .mockResolvedValue({ success: true, task_ids: ["t"], unit_ids: ["E1U1"] });
    mount();

    fireEvent.click(screen.getByRole("button", { name: /^(Create voice-over|创建配音)$/ }));

    await waitFor(() => expect(synth).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U1"] }));
  });

  it("creates the voice-over for every checked unit and shows the count", async () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: { ...BASE_PROJECT, dub_target_language: "vi" },
    });
    useReferenceVideoStore.getState().setChecked("proj", 1, ["E1U2", "E1U3"]);
    const synth = vi
      .spyOn(API, "synthesizeDub")
      .mockResolvedValue({ success: true, task_ids: ["a", "b"], unit_ids: ["E1U2", "E1U3"] });
    mount();

    fireEvent.click(screen.getByRole("button", { name: /Create voice-over \(2\)|创建配音（2）/ }));

    await waitFor(() => expect(synth).toHaveBeenCalledWith("proj", 1, { unit_ids: ["E1U2", "E1U3"] }));
  });

  it("is disabled while a dub task is running", () => {
    useProjectsStore.setState({
      currentProjectName: "proj",
      currentProjectData: { ...BASE_PROJECT, dub_target_language: "vi" },
    });
    useTasksStore.setState({
      tasks: [{ task_id: "t", project_name: "proj", task_type: "dub_tts", status: "running" }] as never,
    });
    mount();

    expect(screen.getByRole("button", { name: /^(Create voice-over|创建配音)$/ })).toBeDisabled();
  });
});
