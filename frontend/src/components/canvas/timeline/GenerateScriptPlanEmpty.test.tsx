import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GenerateScriptPlanEmpty } from "./GenerateScriptPlanEmpty";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";

function task(overrides: Partial<TaskItem>): TaskItem {
  return {
    task_id: "t1",
    project_name: "p",
    task_type: "text_drama_script_plan",
    media_type: "text",
    resource_id: "episode-1",
    status: "running",
    ...overrides,
  } as TaskItem;
}

describe("GenerateScriptPlanEmpty", () => {
  beforeEach(() => {
    useTasksStore.setState({ tasks: [] });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("queues generation for the episode and toasts success", async () => {
    const spy = vi.spyOn(API, "generateScriptPlan").mockResolvedValue({ task_id: "t1", deduped: false });
    const toast = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<GenerateScriptPlanEmpty projectName="p" episode={2} />);

    fireEvent.click(screen.getByRole("button", { name: /generate script plan|生成脚本规划/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("p", 2));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.any(String), "success"));
  });

  it("disables the button while an active script plan task exists for the episode", () => {
    useTasksStore.setState({ tasks: [task({})] });
    render(<GenerateScriptPlanEmpty projectName="p" episode={1} />);

    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("ignores finished tasks and other episodes", () => {
    useTasksStore.setState({
      tasks: [task({ status: "failed" }), task({ task_id: "t2", resource_id: "episode-9" })],
    });
    render(<GenerateScriptPlanEmpty projectName="p" episode={1} />);

    expect(screen.getByRole("button")).toBeEnabled();
  });

  it("surfaces the server error as a toast and re-enables the button", async () => {
    vi.spyOn(API, "generateScriptPlan").mockRejectedValue(new Error("already exists"));
    const toast = vi.spyOn(useAppStore.getState(), "pushToast");
    render(<GenerateScriptPlanEmpty projectName="p" episode={1} />);

    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => expect(toast).toHaveBeenCalledWith("already exists", "error"));
    expect(screen.getByRole("button")).toBeEnabled();
  });
});
