import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ClaudeAPI, type ClaudeMemoryFile, type ProjectClaudeAgent } from "@/api/claude";
import { useAppStore } from "@/stores/app-store";
import { useClaudeStore } from "@/stores/claude-store";
import { downloadBlob } from "@/utils/download";
import { errMsg } from "@/utils/async";

export function ClaudeAgentSettings({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const [agent, setAgent] = useState<ProjectClaudeAgent | null>(null);
  const [files, setFiles] = useState<ClaudeMemoryFile[]>([]);
  const [name, setName] = useState("MEMORY.md");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const pushToast = useAppStore((state) => state.pushToast);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([ClaudeAPI.getAgent(projectName), ClaudeAPI.getMemory(projectName)])
      .then(([status, memory]) => {
        if (cancelled) return;
        setAgent(status);
        setFiles(memory.files);
        setContent(memory.files.find((file) => file.name === "MEMORY.md")?.content ?? "");
      }).catch((error) => { if (!cancelled) pushToast(errMsg(error), "error"); });
    return () => { cancelled = true; };
  }, [projectName, pushToast]);

  const run = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await operation();
      const memory = await ClaudeAPI.getMemory(projectName);
      setFiles(memory.files);
      pushToast(t("claude_memory_saved"), "success");
    } catch (error) { pushToast(errMsg(error), "error"); }
    finally { setBusy(false); }
  };
  const buttonClass = "rounded border border-white/15 px-2 py-1.5 text-xs hover:bg-white/10 disabled:opacity-40";
  return (
    <section className="flex-1 space-y-3 overflow-y-auto p-3 text-xs">
      <h2 className="text-sm font-semibold">{t("claude_project_agent")}</h2>
      {agent ? <>
        <div className="flex flex-wrap gap-2 text-white/60">
          <span>{agent.logged_in ? t("claude_auth_connected") : t("claude_login_required")}</span>
          <span>{t("claude_mcp_ready")}</span>
          <span>{t("claude_skills_count", { count: agent.skills.length })}</span>
        </div>
        <details className="text-white/50"><summary className="cursor-pointer">{t("claude_workspace")}</summary>
          <p className="mt-1 break-all font-mono text-[10px]">{agent.workspace}</p>
          <p className="mt-1 break-all font-mono text-[10px]">{agent.memory_path}</p>
        </details>
      </> : <p>{t("claude_auth_loading")}</p>}
      <label className="block">{t("claude_memory_file")}
        <select className="mt-1 w-full rounded border border-white/15 bg-[#101116] p-2" value={name}
          onChange={(event) => { setName(event.target.value); setContent(files.find((file) => file.name === event.target.value)?.content ?? ""); }}>
          {!files.some((file) => file.name === "MEMORY.md") ? <option value="MEMORY.md">MEMORY.md</option> : null}
          {files.map((file) => <option key={file.name} value={file.name}>{file.name}</option>)}
        </select>
      </label>
      <label className="block">{t("claude_memory")}
        <textarea className="mt-1 w-full rounded border border-white/15 bg-white/5 p-2 font-mono text-xs" rows={12}
          value={content} onChange={(event) => setContent(event.target.value)} />
      </label>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={busy} onClick={() => void run(() => ClaudeAPI.saveMemory(projectName, { name, content }))}>{t("claude_memory_save")}</button>
        <button type="button" className={buttonClass} disabled={busy || !content.trim()} onClick={() => void run(async () => {
          await ClaudeAPI.saveMemory(projectName, { name, content });
          await ClaudeAPI.promoteMemory(projectName, name);
        })}>{t("claude_memory_promote")}</button>
        <button type="button" className={buttonClass} disabled={busy || !content.trim()} onClick={() => void run(() => useClaudeStore.getState().ask(projectName, { action: "remember", note: content }))}>{t("claude_remember")}</button>
        <button type="button" className={buttonClass} disabled={busy} onClick={() => void run(async () => {
          const blob = await ClaudeAPI.exportAgent(projectName);
          downloadBlob(blob, `claude-${projectName}.zip`);
        })}>{t("claude_export")}</button>
      </div>
      <button type="button" className={`${buttonClass} text-amber-300`} disabled={busy} onClick={() => {
        if (!confirmReset) { setConfirmReset(true); return; }
        void run(async () => { await ClaudeAPI.resetMemory(projectName); setContent(""); setName("MEMORY.md"); setConfirmReset(false); });
      }}>{t(confirmReset ? "claude_memory_reset_confirm" : "claude_memory_reset")}</button>
      {confirmReset ? <p className="text-white/50">{t("claude_memory_reset_hint")}</p> : null}
    </section>
  );
}
