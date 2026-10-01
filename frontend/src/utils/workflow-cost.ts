import type { CostBreakdown, CostByType } from "@/types";
import { costEntries, formatCurrencyAmount, totalBreakdown } from "@/utils/cost-format";

/** 制作步骤里会产生 AI 调用费用的那几步，顺序即制作顺序。 */
export const COST_STEP_IDS = ["asset_sheets", "storyboard", "narration_delivery", "video"] as const;
export type CostStepId = (typeof COST_STEP_IDS)[number];

/** 费用桶 → 制作步骤：资产图按类型分桶，都归到「资产图」一步。 */
const BUCKETS_BY_STEP: Record<CostStepId, readonly (keyof CostByType)[]> = {
  asset_sheets: ["characters", "scenes", "props", "products"],
  storyboard: ["image"],
  narration_delivery: ["audio"],
  video: ["video"],
};

export interface WorkflowStepCost {
  id: CostStepId;
  estimate: CostBreakdown;
  actual: CostBreakdown;
}

function pick(byType: CostByType, buckets: readonly (keyof CostByType)[]): CostBreakdown {
  const subset: CostByType = {};
  for (const bucket of buckets) subset[bucket] = byType[bucket];
  return totalBreakdown(subset);
}

/** 把项目级合计按制作步骤重排；「未归属费用」不属于任何一步，由调用方单独展示。 */
export function workflowStepCosts(totals: {
  estimate: CostByType;
  actual: CostByType;
}): WorkflowStepCost[] {
  return COST_STEP_IDS.map((id) => ({
    id,
    estimate: pick(totals.estimate, BUCKETS_BY_STEP[id]),
    actual: pick(totals.actual, BUCKETS_BY_STEP[id]),
  }));
}

export function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : String(n);
}

/** 文本步骤常是几分钱以下的小额，两位小数会显示成 $0.00；小额多给几位。 */
export function formatStepCost(breakdown: CostBreakdown | undefined): string {
  const entries = costEntries(breakdown);
  if (entries.length === 0) return "\u2014";
  return entries
    .map(([cur, amt]) =>
      formatCurrencyAmount(cur, amt, amt < 0.01 ? { maximumFractionDigits: 4 } : {}),
    )
    .join(" + ");
}
