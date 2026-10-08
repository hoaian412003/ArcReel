import { useCallback } from "react";
import { Redirect, useParams } from "wouter";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Bot } from "lucide-react";
import { ClaudePanel } from "@/components/claude/ClaudePanel";
import { NodeCanvasPage } from "@/components/canvas/node-canvas/NodeCanvasPage";
import { TaskFailureListener } from "@/components/layout/TaskFailureListener";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { useProjectLifecycle } from "@/hooks/useProjectLifecycle";
import { useTaskRefresh } from "@/hooks/useTaskRefresh";
import { isDemoProject } from "@/onboarding/demo-project";
import { ROUTE_APP_PROJECTS } from "@/app-routes";
import { useClaudeStore } from "@/stores/claude-store";
import { useProjectsStore } from "@/stores/projects-store";

/**
 * 创作画布：独立的整页（在新标签页里打开），没有顶栏与侧栏——整个窗口都是画布。
 * 只保留画布干活所需的后台：项目数据、任务与项目事件流、Agent 面板。
 */
export function CanvasPage() {
  const { projectName = "" } = useParams<{ projectName: string }>();
  const { t } = useTranslation("dashboard");
  const demo = isDemoProject(projectName);
  useProjectLifecycle(demo ? null : projectName);

  const loaded = useProjectsStore((s) => s.currentProjectName === projectName && !s.projectDetailLoading && s.currentProjectData !== null);
  const sseProject = demo ? null : projectName;
  useTaskRefresh(sseProject, !demo);
  useProjectEventsSSE(sseProject);

  const claudeOpen = useClaudeStore((s) => s.openProject === projectName);
  const openClaude = useClaudeStore((s) => s.open);
  const closeClaude = useCallback(() => useClaudeStore.getState().close(), []);

  // 演示项目在后端不存在，没有可用的画布。
  if (demo) return <Redirect to={`${ROUTE_APP_PROJECTS}/${projectName}`} />;

  return (
    <div className="flex h-screen w-screen overflow-hidden" style={{ color: "var(--color-text)" }}>
      <TaskFailureListener projectName={sseProject} />
      <main className="relative min-w-0 flex-1">
        {loaded ? (
          <NodeCanvasPage key={projectName} projectName={projectName} />
        ) : (
          <div role="status" className="flex h-full items-center justify-center gap-2 text-sm text-text-3">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {t("node_canvas_loading")}
          </div>
        )}
      </main>
      {claudeOpen ? <ClaudePanel key={projectName} projectName={projectName} onClose={closeClaude} /> : null}
      {!claudeOpen ? (
        <button
          type="button"
          onClick={() => openClaude(projectName)}
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
