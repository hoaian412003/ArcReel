import { useCallback, useEffect, useState } from "react";
import { Bot, CheckCircle2, ExternalLink, Loader2, LogIn, LogOut, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ClaudeAPI, type ClaudeAuthStatus, type ClaudeLoginStart } from "@/api/claude";
import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_LG_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { errMsg } from "@/utils/async";

export function ClaudeAuthSection() {
  const { t } = useTranslation("dashboard");
  const [status, setStatus] = useState<ClaudeAuthStatus | null>(null);
  const [login, setLogin] = useState<ClaudeLoginStart | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await ClaudeAPI.authStatus();
    setStatus(next);
    if (next.logged_in) {
      setLogin(null);
      setCode("");
    }
  }, []);

  useEffect(() => {
    // Initial status comes from the host CLI and is written back asynchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh().catch((reason) => setError(errMsg(reason)));
  }, [refresh]);

  useEffect(() => {
    if (!login) return;
    const timer = globalThis.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 2500);
    return () => globalThis.clearInterval(timer);
  }, [login, refresh]);

  const openAuthorization = (authorizationUrl: string) => {
    globalThis.open(authorizationUrl, "claude-login", "popup,width=720,height=820,noopener,noreferrer");
  };

  const startLogin = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await ClaudeAPI.startLogin();
      setLogin(next);
      openAuthorization(next.authorization_url);
    } catch (reason) {
      setError(errMsg(reason));
    } finally {
      setBusy(false);
    }
  };

  const completeLogin = async () => {
    if (!login || !code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const next = await ClaudeAPI.completeLogin(login.login_id, code.trim());
      setStatus(next);
      setLogin(null);
      setCode("");
    } catch (reason) {
      setError(errMsg(reason));
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await ClaudeAPI.logout());
      setLogin(null);
    } catch (reason) {
      setError(errMsg(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-5">
      <div className="rounded-[12px] border border-hairline p-6" style={CARD_STYLE}>
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 gap-4">
            <div className="grid h-11 w-11 shrink-0 place-items-center rounded-[10px] border border-accent/30 bg-accent-dim text-accent-2">
              <Bot className="h-5 w-5" aria-hidden />
            </div>
            <div className="min-w-0">
              <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
                Claude Code
              </div>
              <h2 className="mt-1 text-[17px] font-semibold text-text">{t("claude_auth_title")}</h2>
              <p className="mt-1 max-w-xl text-[12.5px] leading-relaxed text-text-3">
                {t("claude_auth_description")}
              </p>
            </div>
          </div>

          {!status ? (
            <Loader2 className="h-4 w-4 animate-spin text-text-3" aria-label={t("claude_auth_loading")} />
          ) : status.logged_in ? (
            <button type="button" disabled={busy} onClick={() => void logout()} className={GHOST_BTN_LG_CLS}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
              {t("claude_auth_logout")}
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => void startLogin()}
              className={ACCENT_BTN_CLS}
              style={ACCENT_BUTTON_STYLE}
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
              {t("claude_auth_login")}
            </button>
          )}
        </div>

        {status?.logged_in ? (
          <div className="mt-5 flex items-start gap-3 rounded-[9px] border border-emerald-400/25 bg-emerald-400/10 px-4 py-3">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" aria-hidden />
            <div className="min-w-0 text-[12.5px]">
              <div className="font-semibold text-emerald-200">{t("claude_auth_connected")}</div>
              <div className="mt-0.5 truncate text-text-3">
                {[status.email, status.organization, status.subscription_type].filter(Boolean).join(" · ")}
              </div>
            </div>
          </div>
        ) : null}

        {login ? (
          <div className="mt-5 rounded-[10px] border border-accent/25 bg-accent-dim/40 p-4">
            <div className="flex items-start gap-2.5">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent-2" aria-hidden />
              <div className="text-[12.5px] leading-relaxed text-text-2">
                <div className="font-semibold text-text">{t("claude_auth_finish_title")}</div>
                <p className="mt-1 text-text-3">{t("claude_auth_finish_description")}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => openAuthorization(login.authorization_url)}
              className={`${GHOST_BTN_LG_CLS} mt-3`}
            >
              <ExternalLink className="h-4 w-4" />
              {t("claude_auth_reopen")}
            </button>
            <div className="mt-4 flex flex-col gap-2 sm:flex-row">
              <input
                value={code}
                onChange={(event) => setCode(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void completeLogin();
                }}
                placeholder={t("claude_auth_code_placeholder")}
                aria-label={t("claude_auth_code_label")}
                className={INPUT_CLS}
                autoComplete="one-time-code"
              />
              <button
                type="button"
                disabled={busy || !code.trim()}
                onClick={() => void completeLogin()}
                className={ACCENT_BTN_CLS}
                style={ACCENT_BUTTON_STYLE}
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {t("claude_auth_confirm")}
              </button>
            </div>
          </div>
        ) : null}

        {error ? <p role="alert" className="mt-4 text-[12px] text-warm-bright">{error}</p> : null}
      </div>

      <p className="px-1 text-[11.5px] leading-relaxed text-text-4">{t("claude_auth_storage_hint")}</p>
    </section>
  );
}
