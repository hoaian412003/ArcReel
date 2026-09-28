import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { AddCredentialModal } from "@/components/agent/AddCredentialModal";
import { CredentialList } from "@/components/agent/CredentialList";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { SectionShell } from "@/components/ui/SectionShell";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import type {
  AgentCredential,
  CreateAgentCredentialRequest,
  LocalLoginStatus,
  PresetProvider,
  TestConnectionResponse,
  UpdateAgentCredentialRequest,
} from "@/types/agent-credential";
import { errMsg, voidCall } from "@/utils/async";

export function CredentialsSection() {
  const { t } = useTranslation("dashboard");

  const [credentials, setCredentials] = useState<AgentCredential[]>([]);
  const [presets, setPresets] = useState<PresetProvider[]>([]);
  const [customSentinelId, setCustomSentinelId] = useState("__custom__");
  const [localLoginSentinelId, setLocalLoginSentinelId] = useState("__local_login__");
  const [localLoginStatus, setLocalLoginStatus] = useState<LocalLoginStatus | null>(null);
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [busyCredId, setBusyCredId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null);
  const [testedCredId, setTestedCredId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deletingCred, setDeletingCred] = useState(false);
  const [editingCred, setEditingCred] = useState<AgentCredential | null>(null);

  const loadCreds = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([
        API.listAgentCredentials(),
        API.listAgentPresetProviders(),
      ]);
      setCredentials(c.credentials);
      setPresets(p.providers);
      setCustomSentinelId(p.custom_sentinel_id);
      setLocalLoginSentinelId(p.local_login_sentinel_id);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  }, []);

  useEffect(() => {
    // mount 时异步拉取凭证后再 setState，属于受控的初始化加载。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadCreds();
  }, [loadCreds]);

  const credentialsRef = useRef<AgentCredential[]>([]);
  useEffect(() => {
    credentialsRef.current = credentials;
  }, [credentials]);

  const handleCreate = useCallback(
    async (req: CreateAgentCredentialRequest) => {
      await API.createAgentCredential(req);
      await loadCreds();
      voidCall(useConfigStatusStore.getState().refresh());
      useAppStore.getState().pushToast(t("agent_config_saved"), "success");
    },
    [loadCreds, t],
  );

  const handleUpdate = useCallback(
    async (req: CreateAgentCredentialRequest) => {
      if (editingCred == null) return;
      const patch: UpdateAgentCredentialRequest = {
        display_name: req.display_name,
        base_url: req.base_url,
        model: req.model,
        haiku_model: req.haiku_model,
        sonnet_model: req.sonnet_model,
        opus_model: req.opus_model,
        subagent_model: req.subagent_model,
      };
      if (req.api_key) patch.api_key = req.api_key;
      await API.updateAgentCredential(editingCred.id, patch);
      setEditingCred(null);
      await loadCreds();
      voidCall(useConfigStatusStore.getState().refresh());
      useAppStore.getState().pushToast(t("agent_config_saved"), "success");
    },
    [editingCred, loadCreds, t],
  );

  const handleActivate = useCallback(
    async (id: number) => {
      setBusyCredId(id);
      try {
        await API.activateAgentCredential(id);
        await loadCreds();
        const c = credentialsRef.current.find((x) => x.id === id);
        voidCall(useConfigStatusStore.getState().refresh());
        useAppStore
          .getState()
          .pushToast(
            t("cred_activated_toast", { name: c?.display_name ?? "" }),
            "success",
          );
      } catch (err) {
        useAppStore.getState().pushToast(errMsg(err), "error");
      } finally {
        setBusyCredId(null);
      }
    },
    [loadCreds, t],
  );

  const hasLocalLogin = credentials.some((c) => c.preset_id === localLoginSentinelId);

  const refreshLocalLoginStatus = useCallback(async () => {
    try {
      setLocalLoginStatus(await API.getLocalLoginStatus());
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  }, []);

  useEffect(() => {
    if (!hasLocalLogin) return;
    // 存在本机登录凭证时异步拉取一次登录态，属于受控的初始化加载。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshLocalLoginStatus();
  }, [hasLocalLogin, refreshLocalLoginStatus]);

  const handleUseLocalLogin = useCallback(async () => {
    await handleCreate({ preset_id: localLoginSentinelId });
  }, [handleCreate, localLoginSentinelId]);

  const handleTest = useCallback(async (id: number) => {
    if (credentialsRef.current.find((x) => x.id === id)?.preset_id === localLoginSentinelId) {
      setBusyCredId(id);
      try {
        await refreshLocalLoginStatus();
      } finally {
        setBusyCredId(null);
      }
      return;
    }
    setBusyCredId(id);
    setTestResult(null);
    setTestedCredId(id);
    try {
      const res = await API.testAgentCredential(id);
      setTestResult(res);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setBusyCredId(null);
    }
  }, [localLoginSentinelId, refreshLocalLoginStatus]);

  const confirmDelete = useCallback(async () => {
    if (confirmDeleteId == null) return;
    setDeletingCred(true);
    try {
      await API.deleteAgentCredential(confirmDeleteId);
      await loadCreds();
      setConfirmDeleteId(null);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setDeletingCred(false);
    }
  }, [confirmDeleteId, loadCreds]);

  return (
    <>
      <SectionShell
        kicker="Credentials"
        title={t("agent_credentials")}
        description={t("anthropic_key_required_desc")}
        trailing={
          <div className="flex items-center gap-1">
            {!hasLocalLogin && (
              <button
                type="button"
                onClick={() => void handleUseLocalLogin().catch((err) =>
                  useAppStore.getState().pushToast(errMsg(err), "error"),
                )}
                className={GHOST_BTN_CLS}
              >
                {t("cred_local_login_use")}
              </button>
            )}
            <button
              type="button"
              onClick={() => setAddModalOpen(true)}
              className={GHOST_BTN_CLS}
            >
              + {t("add_credential")}
            </button>
          </div>
        }
      >
        <CredentialList
          credentials={credentials}
          busyId={busyCredId}
          testedId={testedCredId}
          testResult={testResult}
          localLoginPresetId={localLoginSentinelId}
          localLoginStatus={localLoginStatus}
          onActivate={(id) => void handleActivate(id)}
          onTest={(id) => void handleTest(id)}
          onEdit={setEditingCred}
          onDelete={setConfirmDeleteId}
        />
      </SectionShell>

      <AddCredentialModal
        open={addModalOpen}
        presets={presets}
        customSentinelId={customSentinelId}
        onSubmit={handleCreate}
        onClose={() => setAddModalOpen(false)}
      />

      <AddCredentialModal
        key={editingCred?.id ?? "edit-empty"}
        open={editingCred !== null}
        mode="edit"
        presets={presets}
        customSentinelId={customSentinelId}
        initial={
          editingCred
            ? {
                preset_id: editingCred.preset_id,
                display_name: editingCred.display_name,
                base_url: editingCred.base_url,
                model: editingCred.model ?? undefined,
                haiku_model: editingCred.haiku_model ?? undefined,
                sonnet_model: editingCred.sonnet_model ?? undefined,
                opus_model: editingCred.opus_model ?? undefined,
                subagent_model: editingCred.subagent_model ?? undefined,
              }
            : undefined
        }
        onSubmit={handleUpdate}
        onClose={() => setEditingCred(null)}
      />

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title={t("cred_delete_confirm_title")}
        description={t("cred_delete_confirm")}
        confirmLabel={t("common:delete")}
        cancelLabel={t("common:cancel")}
        tone="danger"
        loading={deletingCred}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </>
  );
}
