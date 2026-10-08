import type { TaskItem } from "@/types/task";

const POLL_INTERVAL_MS = 2000;
const TIMEOUT_MS = 20 * 60 * 1000;

export interface BranchStep {
  id: string;
  type: string;
  reason: "missing" | "stale" | "target";
}

export class BranchStepFailed extends Error {
  constructor(
    readonly step: BranchStep,
    message: string,
  ) {
    super(message);
    this.name = "BranchStepFailed";
  }
}

export interface BranchDeps {
  /** 运行一步，返回它入队的任务 id。 */
  runStep: (step: BranchStep) => Promise<string[]>;
  getTask: (taskId: string) => Promise<Pick<TaskItem, "status" | "error_message">>;
  sleep?: (ms: number) => Promise<void>;
  onStep?: (step: BranchStep, index: number) => void;
}

const ACTIVE = new Set(["queued", "running"]);

/**
 * 按先上游后下游的顺序逐步运行：每一步的任务都进入终态才跑下一步（下游要用上游刚产出的图）。
 * 任一步失败或被取消即停下，抛出 :class:`BranchStepFailed` 指明是哪一步。
 */
export async function runBranch(steps: readonly BranchStep[], deps: BranchDeps): Promise<void> {
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  for (const [index, step] of steps.entries()) {
    deps.onStep?.(step, index);
    const taskIds = await deps.runStep(step);
    const deadline = Date.now() + TIMEOUT_MS;
    for (const taskId of taskIds) {
      for (;;) {
        const task = await deps.getTask(taskId);
        if (!ACTIVE.has(task.status)) {
          if (task.status !== "succeeded") {
            throw new BranchStepFailed(step, task.error_message ?? task.status);
          }
          break;
        }
        if (Date.now() > deadline) throw new BranchStepFailed(step, "timeout");
        await sleep(POLL_INTERVAL_MS);
      }
    }
  }
}
