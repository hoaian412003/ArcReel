import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Sparkles } from "lucide-react";
import { API } from "@/api";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import { voidPromise } from "@/utils/async";

const SCRIPT_PLAN_TASK_TYPES = new Set([
  "text_drama_script_plan",
  "text_narration_script_plan",
  "text_reference_script_plan",
]);

interface GenerateScriptPlanEmptyProps {
  projectName: string;
  episode: number;
}

/**
 * 该集尚无脚本规划时的空态：一键入队脚本规划生成。进行中状态取自任务队列（含 Agent 提交的同集任务），
 * 产出落盘后审阅面板经 script_plan 修订号刷新，本空态随之被内容取代。
 */
export function GenerateScriptPlanEmpty({ projectName, episode }: GenerateScriptPlanEmptyProps) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const [submitting, setSubmitting] = useState(false);
  const taskActive = useTasksStore((s) =>
    s.tasks.some(
      (task) =>
        task.project_name === projectName &&
        task.resource_id === `episode-${episode}` &&
        SCRIPT_PLAN_TASK_TYPES.has(task.task_type) &&
        (task.status === "queued" || task.status === "running"),
    ),
  );
  const generating = submitting || taskActive;

  const handleGenerate = async () => {
    setSubmitting(true);
    try {
      await API.generateScriptPlan(projectName, episode);
      pushToast(t("dashboard:script_plan_generate_queued"), "success");
    } catch (err) {
      pushToast(err instanceof Error ? err.message : t("dashboard:script_plan_generate_failed"), "error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
      <p className="text-text-4">{t("dashboard:no_script_plan_content")}</p>
      <PrimaryButton
        onClick={voidPromise(handleGenerate)}
        disabled={generating}
        leadingIcon={
          generating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
          ) : (
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
          )
        }
      >
        {generating ? t("dashboard:script_plan_generating") : t("dashboard:script_plan_generate")}
      </PrimaryButton>
    </div>
  );
}
