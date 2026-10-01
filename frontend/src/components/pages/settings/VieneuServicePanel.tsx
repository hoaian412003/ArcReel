import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Play, Square } from "lucide-react";
import { API } from "@/api";
import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import type { VieneuSettings, VieneuStatus, VieneuVoices } from "@/types";

const POLL_MS = 2000;
const INSTALL_COMMAND =
  "git clone https://github.com/pnnbao97/VieNeu-TTS third_party/VieNeu-TTS && (cd third_party/VieNeu-TTS && uv sync)";

type Draft = Partial<VieneuSettings>;

/**
 * 本地 VieNeu-TTS 服务的运行状态与设置：启停、检出目录 / 启动命令 / 端口 / 设备等，
 * 以及当前可用的音色数。设置在下次启动服务时生效。
 */
export function VieneuServicePanel() {
  const { t } = useTranslation("dashboard");
  const [status, setStatus] = useState<VieneuStatus | null>(null);
  const [settings, setSettings] = useState<VieneuSettings | null>(null);
  const [voices, setVoices] = useState<VieneuVoices | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState<"start" | "stop" | "save" | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const mounted = useRef(true);

  const refreshStatus = useCallback(async () => {
    try {
      const next = await API.getVieneuStatus();
      if (mounted.current) setStatus(next);
      return next;
    } catch {
      return null;
    }
  }, []);

  const refreshVoices = useCallback(async () => {
    try {
      const next = await API.getVieneuVoices();
      if (mounted.current) setVoices(next);
    } catch {
      // 音色数只是辅助信息，取不到就不显示
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void (async () => {
      try {
        const [s, st] = await Promise.all([API.getVieneuSettings(), API.getVieneuStatus()]);
        if (!mounted.current) return;
        setSettings(s);
        setStatus(st);
        void refreshVoices();
      } catch (e: unknown) {
        if (mounted.current) setLoadError(errMsg(e));
      }
    })();
    return () => {
      mounted.current = false;
    };
  }, [refreshVoices]);

  // 启动中（含另一处触发的启动）持续刷新状态，直到落定
  const state = status?.state;
  useEffect(() => {
    if (state !== "starting") return;
    const timer = setInterval(() => void refreshStatus(), POLL_MS);
    return () => clearInterval(timer);
  }, [state, refreshStatus]);

  const handleStart = async () => {
    setBusy("start");
    // 启动请求会阻塞到服务就绪，期间用轮询把「启动中」与日志展示出来
    const timer = setInterval(() => void refreshStatus(), POLL_MS);
    try {
      const next = await API.startVieneu();
      setStatus(next);
      void refreshVoices();
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("vieneu_start_failed", { error: errMsg(e) }), "error");
      await refreshStatus();
    } finally {
      clearInterval(timer);
      setBusy(null);
    }
  };

  const handleStop = async () => {
    setBusy("stop");
    try {
      setStatus(await API.stopVieneu());
      void refreshVoices();
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("vieneu_stop_failed", { error: errMsg(e) }), "error");
    } finally {
      setBusy(null);
    }
  };

  const handleSave = async () => {
    if (Object.keys(draft).length === 0) return;
    setBusy("save");
    try {
      setSettings(await API.patchVieneuSettings(draft));
      setDraft({});
      useAppStore.getState().pushToast(t("vieneu_saved_toast"), "success");
    } catch (e: unknown) {
      useAppStore.getState().pushToast(t("vieneu_save_failed", { error: errMsg(e) }), "error");
    } finally {
      setBusy(null);
    }
  };

  const edit = (patch: Draft) => setDraft((current) => ({ ...current, ...patch }));

  if (loadError) {
    return (
      <p role="alert" className="rounded-md bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
        {t("vieneu_load_failed", { error: loadError })}
      </p>
    );
  }
  if (!settings || !status) {
    return (
      <div className="flex items-center gap-2 text-text-3">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
        <span className="text-[12px]">{t("vieneu_loading")}</span>
      </div>
    );
  }

  const value = <K extends keyof VieneuSettings>(key: K): VieneuSettings[K] =>
    (draft[key] ?? settings[key]);
  const running = status.state === "ready";
  const starting = status.state === "starting" || busy === "start";
  const dirty = Object.keys(draft).length > 0;
  const genderCounts = voices
    ? {
        male: voices.voices.filter((v) => v.gender === "male").length,
        female: voices.voices.filter((v) => v.gender === "female").length,
      }
    : null;

  return (
    <section aria-label={t("vieneu_panel_title")} className="space-y-4 rounded-[10px] border border-hairline-soft p-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <h4 className="text-[13px] font-semibold text-text">{t("vieneu_panel_title")}</h4>
        <span
          role="status"
          className={`rounded-full border px-2.5 py-0.5 text-[11px] ${
            running
              ? "border-[var(--color-good)] text-[var(--color-good)]"
              : status.state === "error"
                ? "border-[var(--color-warm-ring)] text-[var(--color-warm-bright)]"
                : "border-hairline text-text-3"
          }`}
        >
          {t(`vieneu_state_${starting ? "starting" : status.state}`)}
        </span>
        {running && (
          <span className="text-[11.5px] text-text-3">
            {status.managed ? t("vieneu_managed") : t("vieneu_external")}
            {status.base_url ? ` · ${status.base_url}` : ""}
          </span>
        )}
        <span className="flex-1" />
        {running ? (
          <button
            type="button"
            onClick={() => void handleStop()}
            disabled={busy !== null || !status.managed}
            title={!status.managed ? t("vieneu_external_stop_hint") : undefined}
            className={GHOST_BTN_CLS}
          >
            {busy === "stop" ? (
              <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
            ) : (
              <Square className="h-3.5 w-3.5" aria-hidden />
            )}
            {t("vieneu_stop")}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void handleStart()}
            disabled={busy !== null || starting}
            className={ACCENT_BTN_SM_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {starting ? (
              <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
            ) : (
              <Play className="h-3.5 w-3.5" aria-hidden />
            )}
            {starting ? t("vieneu_starting") : t("vieneu_start")}
          </button>
        )}
      </div>

      {starting && <p className="text-[11.5px] text-text-3">{t("vieneu_first_start_hint")}</p>}

      {status.last_error && status.state === "error" && (
        <div role="alert" className="space-y-2 rounded-md bg-red-500/10 px-3 py-2 text-[12px] text-red-300">
          <p className="break-words">{status.last_error}</p>
          <p className="text-red-200/80">{t("vieneu_install_hint")}</p>
          <code className="block break-all rounded bg-black/30 px-2 py-1 font-mono text-[11px] text-text-2">
            {INSTALL_COMMAND}
          </code>
        </div>
      )}

      {genderCounts && voices && (
        <p className="text-[11.5px] text-text-3">
          {t("vieneu_voices_summary", {
            male: genderCounts.male,
            female: genderCounts.female,
            source: t(`vieneu_voices_source_${voices.source}`),
          })}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex items-center gap-2 text-[12.5px] text-text-2 sm:col-span-2">
          <input
            type="checkbox"
            checked={value("enabled")}
            onChange={(e) => edit({ enabled: e.target.checked })}
            className="h-3.5 w-3.5 accent-[var(--color-accent)]"
          />
          {t("vieneu_field_enabled")}
        </label>

        <div className="sm:col-span-2">
          <FieldLabel htmlFor="vieneu-workdir">{t("vieneu_field_workdir")}</FieldLabel>
          <input
            id="vieneu-workdir"
            value={value("workdir")}
            onChange={(e) => edit({ workdir: e.target.value })}
            className={INPUT_CLS}
            spellCheck={false}
          />
          <p className="mt-1 text-[11px] text-text-4">{t("vieneu_field_workdir_hint")}</p>
        </div>

        <div className="sm:col-span-2">
          <FieldLabel htmlFor="vieneu-command">{t("vieneu_field_command")}</FieldLabel>
          <input
            id="vieneu-command"
            value={value("command")}
            onChange={(e) => edit({ command: e.target.value })}
            placeholder="uv run python -m apps.openai_speech"
            className={`${INPUT_CLS} font-mono`}
            spellCheck={false}
          />
        </div>

        <div>
          <FieldLabel htmlFor="vieneu-port">{t("vieneu_field_port")}</FieldLabel>
          <input
            id="vieneu-port"
            type="number"
            min={1}
            max={65535}
            value={value("port")}
            onChange={(e) => edit({ port: Number(e.target.value) })}
            className={INPUT_CLS}
          />
        </div>

        <div>
          <FieldLabel htmlFor="vieneu-device">{t("vieneu_field_device")}</FieldLabel>
          <select
            id="vieneu-device"
            value={value("device")}
            onChange={(e) => edit({ device: e.target.value as VieneuSettings["device"] })}
            className={INPUT_CLS}
          >
            <option value="auto">{t("vieneu_device_auto")}</option>
            <option value="cpu">CPU</option>
            <option value="cuda">CUDA</option>
          </select>
        </div>

        <div>
          <FieldLabel htmlFor="vieneu-timeout">{t("vieneu_field_startup_timeout")}</FieldLabel>
          <input
            id="vieneu-timeout"
            type="number"
            min={5}
            max={3600}
            value={value("startup_timeout_seconds")}
            onChange={(e) => edit({ startup_timeout_seconds: Number(e.target.value) })}
            className={INPUT_CLS}
          />
        </div>

        <div>
          <FieldLabel htmlFor="vieneu-idle">{t("vieneu_field_idle")}</FieldLabel>
          <input
            id="vieneu-idle"
            type="number"
            min={0}
            max={1440}
            value={value("idle_minutes")}
            onChange={(e) => edit({ idle_minutes: Number(e.target.value) })}
            className={INPUT_CLS}
          />
        </div>

        <label className="flex items-center gap-2 text-[12.5px] text-text-2 sm:col-span-2">
          <input
            type="checkbox"
            checked={value("int8")}
            onChange={(e) => edit({ int8: e.target.checked })}
            className="h-3.5 w-3.5 accent-[var(--color-accent)]"
          />
          {t("vieneu_field_int8")}
        </label>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={!dirty || busy !== null}
          className={ACCENT_BTN_SM_CLS}
          style={ACCENT_BUTTON_STYLE}
        >
          {busy === "save" && <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />}
          {t("vieneu_save")}
        </button>
        <span className="text-[11.5px] text-text-4">{t("vieneu_settings_hint")}</span>
      </div>

      <div>
        <button
          type="button"
          onClick={() => setShowLogs((v) => !v)}
          aria-expanded={showLogs}
          className="text-[11.5px] text-text-3 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {showLogs ? t("vieneu_logs_hide") : t("vieneu_logs_show")}
        </button>
        {showLogs && (
          <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-black/30 p-2 font-mono text-[11px] leading-relaxed text-text-3">
            {status.logs.length > 0 ? status.logs.join("\n") : t("vieneu_logs_empty")}
          </pre>
        )}
      </div>
    </section>
  );
}
