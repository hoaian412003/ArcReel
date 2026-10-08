import { TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useProjectsStore } from "@/stores/projects-store";
import { AskAgentButton } from "@/components/claude/AskAgentButton";

/**
 * 工作台顶部的一条阻断说明：本项目的数据升级没跑完，生成入口全部关闭。
 *
 * 已有的产物照常可看，所以这里不遮挡界面、也不做成对话框——它是持续存在的状态，
 * 不是一次性通知，形态取参考生视频那条 warm 提示条。只展示失败原因，不提供界面内的修复入口。
 */
export function MigrationRepairBanner() {
  const { t } = useTranslation("dashboard");
  const status = useProjectsStore((state) => state.currentProjectData?.status);
  const projectName = useProjectsStore((state) => state.currentProjectName);
  const needsRepair = status?.needs_repair === true;
  const reason = status?.repair_reason ?? null;

  if (!needsRepair) return null;

  return (
    <div
      role="alert"
      className="flex shrink-0 items-start gap-2.5 border-b px-5 py-2.5"
      style={{ borderColor: "var(--color-warm-ring)", background: "var(--color-warm-soft)" }}
    >
      <TriangleAlert
        className="mt-0.5 h-4 w-4 shrink-0"
        style={{ color: "var(--color-warm)" }}
        aria-hidden="true"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="m-0 text-[12px] font-semibold leading-[1.55] text-[var(--color-text)]">
          {t("migration_repair_title")}
        </p>
        <p className="m-0 text-[12px] leading-[1.55] text-[var(--color-text-2)]">
          {t("migration_repair_body")}
        </p>
        {projectName ? <AskAgentButton projectName={projectName} action={{ action: "repair_project" }} /> : null}
        {reason ? (
          <p className="m-0 break-words font-mono text-[11.5px] leading-[1.5] text-[var(--color-text-3)]">
            {reason}
          </p>
        ) : null}
      </div>
    </div>
  );
}
