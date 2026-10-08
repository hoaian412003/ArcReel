import { Bot } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ClaudeActionInput } from "@/api/claude";
import { useClaudeStore } from "@/stores/claude-store";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { isDemoProject } from "@/onboarding/demo-project";

export function AskAgentButton({ projectName, action }: { projectName: string; action: ClaudeActionInput }) {
  const { t } = useTranslation("dashboard");
  const ask = useClaudeStore((state) => state.ask);
  const pending = useClaudeStore((state) => state.pending[projectName] ?? false);
  const pushToast = useAppStore((state) => state.pushToast);
  if (isDemoProject(projectName)) return null;
  return (
    <button type="button" disabled={pending} className="mt-2 flex items-center gap-1.5 rounded-md border border-white/15 px-2.5 py-1.5 text-xs hover:bg-white/10 disabled:opacity-50"
      onClick={() => { void ask(projectName, action).catch((error) => pushToast(errMsg(error), "error")); }}>
      <Bot className="h-3.5 w-3.5" aria-hidden />{t("claude_ask")}
    </button>
  );
}
