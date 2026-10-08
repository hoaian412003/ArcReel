import { useLocation } from "wouter";
import { GlobalHeader } from "./GlobalHeader";
import { AssetSidebar } from "./AssetSidebar";
import { useTaskRefresh } from "@/hooks/useTaskRefresh";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { TaskFailureListener } from "./TaskFailureListener";
import { MigrationRepairBanner } from "./MigrationRepairBanner";
import { useProjectsStore } from "@/stores/projects-store";
import { DemoReadOnlyBanner } from "@/onboarding/DemoReadOnlyBanner";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { isDemoProject } from "@/onboarding/demo-project";
import { Bot } from "lucide-react";
import { useCallback } from "react";
import { useClaudeStore } from "@/stores/claude-store";
import { ClaudePanel } from "@/components/claude/ClaudePanel";
import { useTranslation } from "react-i18next";

interface StudioLayoutProps {
  children: React.ReactNode;
}

/**
 * 工作台两栏布局壳：顶栏 + （侧栏 / 主区）。
 */
export function StudioLayout({ children }: StudioLayoutProps) {
  const { t } = useTranslation("dashboard");
  const openProject = useClaudeStore((s) => s.openProject);
  const openClaude = useClaudeStore((s) => s.open);
  const closeClaude = useCallback(() => useClaudeStore.getState().close(), []);
  const [, setLocation] = useLocation();
  const currentProjectName = useProjectsStore((s) => s.currentProjectName);
  // 演示项目在后端不存在：任务 / 项目事件流都是真实写路径，演示态下整条都不接
  const demoMode = useDemoWorkbench();

  // demoMode 演示→真实切换时先于 store 变为 false，currentProjectName 单独判一次
  // 兜住这一帧仍读到旧演示项目名的窗口，避免对不存在的演示项目建一次必然失败的 SSE 连接。
  // useTaskRefresh 的 projectName=null 语义是「不按项目过滤」而非「停用」，enabled 必须
  // 同步这一判定，否则该帧会退化成对全局任务的轮询而非真正停用。
  const isEffectivelyDemo = demoMode || isDemoProject(currentProjectName);
  const sseProjectName = isEffectivelyDemo ? null : currentProjectName;
  const claudeOpen = openProject === currentProjectName;
  useTaskRefresh(sseProjectName, !isEffectivelyDemo);
  useProjectEventsSSE(sseProjectName);

  return (
    <div
      className="flex h-screen flex-col"
      style={{ color: "var(--color-text)" }}
    >
      <TaskFailureListener projectName={sseProjectName} />
      <GlobalHeader onNavigateBack={() => setLocation("~/app/projects")} />
      {demoMode ? <DemoReadOnlyBanner /> : null}
      <MigrationRepairBanner />
      <div className="flex flex-1 overflow-hidden">
        <AssetSidebar />
        <main className="flex-1 overflow-hidden">
          {children}
        </main>
        {!isEffectivelyDemo && currentProjectName && claudeOpen ? (
          <ClaudePanel key={currentProjectName} projectName={currentProjectName} onClose={closeClaude} />
        ) : null}
      </div>
      {!isEffectivelyDemo && currentProjectName && !claudeOpen ? (
        <button
          type="button"
          onClick={() => openClaude(currentProjectName)}
          aria-label={t("claude_open")}
          className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full bg-[#d7ff45] px-4 py-2.5 text-sm font-semibold text-black shadow-xl"
        >
          <Bot className="h-4 w-4" aria-hidden />
          {t("claude_title")}
        </button>
      ) : null}
    </div>
  );
}
