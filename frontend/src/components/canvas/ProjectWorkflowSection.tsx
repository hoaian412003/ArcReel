/**
 * 项目概览里的「制作流程」区块：把权威工作流计划的每一步与该步的参考费用并排陈述。
 *
 * 步骤与进度只投影后端计划（`POST /workflow-plan`），不在界面推断；费用取自费用 store 已
 * 加载的估算——图像/视频/配音按媒体类型归到对应步骤，文本步骤按提示词规模估算 token 与费用。
 */

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { enqueueAssetAnalysis } from "@/actions/assets";
import { API } from "@/api";
import { STEP_RAILS } from "@/components/workflow/state-language";
import { nextStepForAction } from "@/components/workflow/problem-views";
import { useAppStore } from "@/stores/app-store";
import { useCostStore } from "@/stores/cost-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { CostBreakdown, TextStepCost } from "@/types";
import type { WorkflowPlan, WorkflowStepState } from "@/types/workflow";
import { costEntries, totalBreakdown } from "@/utils/cost-format";
import {
  formatStepCost,
  formatTokens,
  workflowStepCosts,
  type CostStepId,
} from "@/utils/workflow-cost";

/** 逐集取计划的集数上限；超过则只取当前集的计划，避免一次发出海量请求。 */
const MAX_PER_EPISODE_PLANS = 30;

const CARD_BG =
  "linear-gradient(180deg, oklch(0.22 0.012 265 / 0.55), oklch(0.19 0.010 265 / 0.40))";
const CARD_SHADOW =
  "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 8px 24px -10px oklch(0 0 0 / 0.5)";

function mergeCost(...parts: (CostBreakdown | undefined)[]): CostBreakdown {
  const out: CostBreakdown = {};
  for (const part of parts) {
    for (const [cur, amt] of Object.entries(part ?? {})) out[cur] = (out[cur] ?? 0) + amt;
  }
  return out;
}

interface StepProgress {
  id: string;
  state: WorkflowStepState;
  completed: number;
  total: number;
}

/**
 * 把逐集计划折成项目级的一行：全部集同态就照实陈述；否则报「已完成 n / N」，
 * 状态取最靠后的那一集——一步「Done」只在每一集都完成时才成立。
 */
function foldSteps(plans: WorkflowPlan[]): StepProgress[] {
  const [first] = plans;
  if (!first) return [];
  return first.steps.map((step) => {
    const states = plans.map((plan) => plan.steps.find((s) => s.id === step.id)?.state ?? step.state);
    const completed = states.filter((state) => state === "completed").length;
    const uniform = states.every((state) => state === states[0]);
    const state = uniform
      ? states[0]
      : (states.find((state) => state === "blocked") ??
        states.find((state) => state === "active") ??
        states.find((state) => state === "ready") ??
        states.find((state) => state !== "completed" && state !== "skipped") ??
        "pending");
    return { id: step.id, state, completed, total: states.length };
  });
}

export function ProjectWorkflowSection({
  projectName,
  episodes,
}: {
  projectName: string;
  /** 项目里的集号；为空（尚未规划分集）时按服务端选定的当前集取一份计划。 */
  episodes: readonly number[];
}) {
  const { t } = useTranslation(["dashboard", "workflow"]);
  const { t: tWorkflow } = useTranslation("workflow");
  const [plan, setPlan] = useState<WorkflowPlan | null>(null);
  const [episodePlans, setEpisodePlans] = useState<WorkflowPlan[]>([]);
  const episodeKey = episodes.join(",");
  const snapshotRevision = useProjectsStore((s) => s.projectSnapshotRevisions[projectName] ?? 0);
  const costData = useCostStore((s) => s.costData);
  const [analyzing, setAnalyzing] = useState(false);

  useEffect(() => {
    if (!projectName) return;
    const controller = new AbortController();
    // 每集一份计划要一次请求：集数很多（整本小说导入）时逐集取会一次性发出成百上千个请求，
    // 超过上限改取服务端按项目进度选定的当前集这一份。
    const listed = episodeKey ? episodeKey.split(",").map(Number) : [];
    const targets: (number | null)[] =
      listed.length > 0 && listed.length <= MAX_PER_EPISODE_PLANS ? listed : [null];
    Promise.all(
      targets.map((episode) =>
        API.getWorkflowPlan(projectName, { episode }, { signal: controller.signal }),
      ),
    )
      .then((plans) => {
        if (controller.signal.aborted) return;
        setEpisodePlans(plans);
        // 摘要行的「下一步」取服务端按项目进度选定的当前集。
        return API.getWorkflowPlan(projectName, {}, { signal: controller.signal });
      })
      .then((current) => {
        if (current && !controller.signal.aborted) setPlan(current);
      })
      .catch(() => {
        // 保留上一份计划：刷新失败不该把已有事实清空。
      });
    return () => controller.abort();
  }, [projectName, snapshotRevision, episodeKey]);

  const progress = useMemo(() => foldSteps(episodePlans), [episodePlans]);

  const costByStep = useMemo(() => {
    const byId = new Map<string, { estimate: CostBreakdown; actual?: CostBreakdown; text?: TextStepCost }>();
    if (!costData) return byId;
    for (const step of workflowStepCosts(costData.project_totals)) {
      byId.set(step.id satisfies CostStepId, { estimate: step.estimate, actual: step.actual });
    }
    for (const step of costData.text_steps?.steps ?? []) {
      byId.set(step.id, { estimate: step.cost, text: step });
    }
    return byId;
  }, [costData]);

  if (!plan) return null;

  const analyzeAssets = async () => {
    setAnalyzing(true);
    try {
      await enqueueAssetAnalysis(projectName);
    } catch (err) {
      useAppStore.getState().pushToast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setAnalyzing(false);
    }
  };

  const estimateTotal = mergeCost(
    ...progress.map((step) => costByStep.get(step.id)?.estimate),
  );
  const unassigned = costData?.project_totals.actual.unassigned;
  const actualTotal = totalBreakdown({ ...costData?.project_totals.actual, unassigned: undefined });

  return (
    <section
      className="relative overflow-hidden rounded-2xl p-5"
      style={{
        border: "1px solid var(--color-hairline-soft)",
        background: CARD_BG,
        boxShadow: CARD_SHADOW,
      }}
      data-testid="project-workflow-section"
    >
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className="text-[10.5px] font-bold uppercase"
          style={{ color: "var(--color-text-4)", letterSpacing: "1.0px" }}
        >
          {t("dashboard:workflow_section_title")}
        </span>
        <span className="min-w-0 flex-1 truncate text-[12px]" style={{ color: "var(--color-text-3)" }}>
          {nextStepForAction(tWorkflow, plan.next_action.type)}
        </span>
        {plan.next_action.type === "analyze_assets" ? (
          <button
            type="button"
            onClick={() => void analyzeAssets()}
            disabled={analyzing}
            className="focus-ring shrink-0 rounded px-2 py-0.5 text-[11.5px] disabled:opacity-50"
            style={{ color: "var(--color-accent-2)", border: "1px solid var(--color-accent-soft)" }}
          >
            {t("dashboard:asset_analysis_run")}
          </button>
        ) : null}
      </div>

      <ol className="num m-0 list-none p-0 text-[12px]">
        {progress.map((step) => {
          const rail = STEP_RAILS[step.state];
          const cost = costByStep.get(step.id);
          const skipped = step.state === "skipped";
          const hasActual = costEntries(cost?.actual).length > 0;
          return (
            <li
              key={step.id}
              className="flex items-start justify-between gap-3 py-1"
              data-testid={`workflow-cost-${step.id}`}
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-2">
                  <span
                    aria-hidden
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={
                      rail.mark === "solid" || rail.mark === "pulse"
                        ? { background: rail.tone.color }
                        : { border: `1px solid ${rail.tone.ring}` }
                    }
                  />
                  <span style={{ color: skipped ? "var(--color-text-4)" : "var(--color-text-2)" }}>
                    {t(`workflow:step_${step.id}`, { defaultValue: step.id })}
                  </span>
                  <span className="text-[11px]" style={{ color: rail.tone.color }}>
                    {step.total > 1 && step.completed > 0 && step.completed < step.total
                      ? t("dashboard:workflow_steps_progress", {
                          completed: step.completed,
                          total: step.total,
                        })
                      : t(`workflow:step_state_${step.state}`)}
                  </span>
                </div>
                {cost?.text ? (
                  <div className="pl-4 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
                    {t("dashboard:workflow_cost_tokens", {
                      input: formatTokens(cost.text.input_tokens),
                      output: formatTokens(cost.text.output_tokens),
                    })}
                  </div>
                ) : null}
              </div>
              {skipped ? null : (
                <div className="shrink-0 text-right">
                  <div style={{ color: "var(--color-text-2)" }}>{formatStepCost(cost?.estimate)}</div>
                  {hasActual ? (
                    <div className="text-[10.5px]" style={{ color: "var(--color-good)" }}>
                      {t("dashboard:actual")} {formatStepCost(cost?.actual)}
                    </div>
                  ) : null}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <dl
        className="num mt-2 space-y-1 pt-2 text-[12px]"
        style={{ borderTop: "1px solid var(--color-hairline-soft)" }}
      >
        <div className="flex items-baseline justify-between gap-3">
          <dt style={{ color: "var(--color-text-4)" }}>
            {t("dashboard:estimate")} · {t("dashboard:cost_total_short")}
          </dt>
          <dd className="font-semibold" style={{ color: "oklch(0.85 0.13 75)" }}>
            {formatStepCost(estimateTotal)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt style={{ color: "var(--color-text-4)" }}>
            {t("dashboard:actual")} · {t("dashboard:cost_total_short")}
          </dt>
          <dd className="font-semibold" style={{ color: "var(--color-good)" }}>
            {formatStepCost(actualTotal)}
          </dd>
        </div>
        {costEntries(unassigned).length > 0 ? (
          <div className="flex items-baseline justify-between gap-3">
            <dt style={{ color: "var(--color-text-4)" }}>{t("dashboard:actual_unassigned_history")}</dt>
            <dd style={{ color: "var(--color-text-3)" }}>{formatStepCost(unassigned)}</dd>
          </div>
        ) : null}
      </dl>
      <p className="mt-2 text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
        {t("dashboard:workflow_cost_note")}
      </p>
    </section>
  );
}
