import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ContentBlock } from "@/types";

import { TaskProgressBlock } from "./TaskProgressBlock";

const notification = (task_status: string): ContentBlock => ({
  type: "task_progress",
  status: "task_notification",
  description: "Author prompts",
  summary: "details",
  task_status,
});

describe("TaskProgressBlock", () => {
  it("marks a stopped background task as interrupted rather than merely ended", () => {
    render(<TaskProgressBlock block={notification("stopped")} />);
    expect(screen.getByText(/子智能体被中断/)).toBeInTheDocument();
  });

  it("keeps the completed label for finished tasks", () => {
    render(<TaskProgressBlock block={notification("completed")} />);
    expect(screen.queryByText(/子智能体被中断/)).not.toBeInTheDocument();
  });
});
