import { describe, expect, it, vi } from "vitest";
import { BranchStepFailed, runBranch, type BranchStep } from "./branch-runner";

const steps: BranchStep[] = [
  { id: "up", type: "image_generate", reason: "missing" },
  { id: "down", type: "image_generate", reason: "target" },
];
const noSleep = () => Promise.resolve();

describe("runBranch", () => {
  it("waits for each step's tasks to finish before starting the next", async () => {
    const order: string[] = [];
    const polls: Record<string, string[]> = { t1: ["running", "succeeded"], t2: ["succeeded"] };
    await runBranch(steps, {
      runStep: (step) => {
        order.push(`run:${step.id}`);
        return Promise.resolve([step.id === "up" ? "t1" : "t2"]);
      },
      getTask: (id) => {
        order.push(`poll:${id}`);
        return Promise.resolve({ status: polls[id].shift() as never, error_message: null });
      },
      sleep: noSleep,
    });
    expect(order).toEqual(["run:up", "poll:t1", "poll:t1", "run:down", "poll:t2"]);
  });

  it("stops at the first failed step and names it", async () => {
    const runStep = vi.fn((step: BranchStep) => Promise.resolve([step.id]));
    const failure = runBranch(steps, {
      runStep,
      getTask: () => Promise.resolve({ status: "failed", error_message: "provider down" }),
      sleep: noSleep,
    });
    await expect(failure).rejects.toMatchObject({ name: "BranchStepFailed", message: "provider down", step: steps[0] });
    expect(runStep).toHaveBeenCalledTimes(1);
  });

  it("propagates an enqueue error without polling", async () => {
    const getTask = vi.fn();
    await expect(
      runBranch(steps, { runStep: () => Promise.reject(new Error("no prompt")), getTask, sleep: noSleep }),
    ).rejects.toThrow("no prompt");
    expect(getTask).not.toHaveBeenCalled();
    expect(BranchStepFailed).toBeDefined();
  });
});
