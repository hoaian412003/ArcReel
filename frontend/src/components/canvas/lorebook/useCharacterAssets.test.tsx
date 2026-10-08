import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { selectActiveResourceIds, useTasksStore } from "@/stores/tasks-store";
import type { ProjectData } from "@/types";
import { useCharacterAssets } from "./useCharacterAssets";

const project = { title: "Demo", characters: { Hero: { description: "d" } } } as unknown as ProjectData;

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useTasksStore.setState(useTasksStore.getInitialState(), true);
  useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: project, currentScripts: {} });
  vi.spyOn(API, "getProject").mockResolvedValue({ project, scripts: {} });
});

afterEach(() => vi.restoreAllMocks());

describe("useCharacterAssets", () => {
  it("saves description, reference image and audio in order, then refreshes the project", async () => {
    vi.spyOn(API, "updateCharacter").mockResolvedValue({ success: true });
    const upload = vi.spyOn(API, "uploadFile").mockResolvedValue({ success: true, path: "x", url: "y" });
    const { result } = renderHook(() => useCharacterAssets("demo"));

    await act(() =>
      result.current.save("Hero", {
        description: "new desc",
        voiceStyle: "new voice",
        referenceFile: new File(["a"], "a.png"),
        audioFile: new File(["b"], "b.wav"),
      }),
    );

    expect(API.updateCharacter).toHaveBeenCalledWith("demo", "Hero", { description: "new desc", voice_style: "new voice" });
    expect(upload).toHaveBeenNthCalledWith(1, "demo", "character_ref", expect.any(File), "Hero");
    expect(upload).toHaveBeenNthCalledWith(2, "demo", "character_audio_ref", expect.any(File), "Hero");
    expect(API.getProject).toHaveBeenCalled();
    expect(useAppStore.getState().toast?.tone).toBe("success");
  });

  it("still refreshes the project when a later upload step fails, and reports the error", async () => {
    vi.spyOn(API, "updateCharacter").mockResolvedValue({ success: true });
    vi.spyOn(API, "uploadFile").mockRejectedValue(new Error("audio_duration_out_of_range"));
    const { result } = renderHook(() => useCharacterAssets("demo"));

    await act(() =>
      result.current.save("Hero", { description: "d", voiceStyle: "v", audioFile: new File(["b"], "b.wav") }),
    );

    expect(API.getProject).toHaveBeenCalled();
    expect(useAppStore.getState().toast?.tone).toBe("error");
  });

  it("submits generation, marks the character busy right away and toasts on failure", async () => {
    const generate = vi
      .spyOn(API, "generateCharacter")
      .mockResolvedValueOnce({ success: true, task_id: "t-1", deduped: false, message: "已提交" });
    const { result } = renderHook(() => useCharacterAssets("demo"));

    act(() => result.current.generate("Hero"));

    await waitFor(() => {
      const { tasks, optimisticActive } = useTasksStore.getState();
      expect(selectActiveResourceIds(tasks, "character", "demo", optimisticActive).has("Hero")).toBe(true);
    });
    expect(generate).toHaveBeenCalledWith("demo", "Hero");
    expect(result.current.generating.has("Hero")).toBe(true);

    generate.mockRejectedValueOnce(new Error("character generate failed"));
    act(() => result.current.generate("Hero"));
    await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("error"));
  });

  it("add reports a failure and rethrows so the dialog stays open", async () => {
    vi.spyOn(API, "addCharacter").mockRejectedValue(new Error("duplicate"));
    const { result } = renderHook(() => useCharacterAssets("demo"));

    await expect(act(() => result.current.add("Hero", "d", "v"))).rejects.toThrow("duplicate");

    expect(useAppStore.getState().toast?.tone).toBe("error");
  });

  it("does nothing without a project", async () => {
    const update = vi.spyOn(API, "updateCharacter");
    const { result } = renderHook(() => useCharacterAssets(null));
    await act(() => result.current.save("Hero", { description: "d", voiceStyle: "v" }));
    expect(update).not.toHaveBeenCalled();
  });
});
