import { describe, expect, it } from "vitest";
import { workflowStepCosts } from "@/utils/workflow-cost";

describe("workflowStepCosts", () => {
  it("groups asset buckets into one step and keeps currencies apart", () => {
    const steps = workflowStepCosts({
      estimate: {
        characters: { USD: 1 },
        scenes: { USD: 0.5, CNY: 2 },
        image: { USD: 3 },
        video: { USD: 10 },
        audio: { CNY: 4 },
      },
      actual: { video: { USD: 2.5 }, unassigned: { USD: 9 } },
    });
    const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
    expect(byId.asset_sheets.estimate).toEqual({ USD: 1.5, CNY: 2 });
    expect(byId.storyboard.estimate).toEqual({ USD: 3 });
    expect(byId.narration_delivery.estimate).toEqual({ CNY: 4 });
    expect(byId.video.actual).toEqual({ USD: 2.5 });
    expect(steps.flatMap((s) => Object.values(s.actual)).length).toBe(1);
  });
});
