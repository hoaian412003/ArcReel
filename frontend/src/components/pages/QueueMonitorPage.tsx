import { useCallback, useMemo, useRef, type KeyboardEvent } from "react";
import { useLocation, useSearch } from "wouter";
import { useTranslation } from "react-i18next";
import { ChevronLeft } from "lucide-react";
import { QueueLimitsTab } from "@/components/queue/QueueLimitsTab";
import { QueueOverviewTab } from "@/components/queue/QueueOverviewTab";
import { QueueTasksTab } from "@/components/queue/QueueTasksTab";
import { ICON_BTN_FILLED_CLS, ambientGlowStyle } from "@/components/ui/darkroom-tokens";
import { ROUTE_APP_PROJECTS } from "@/app-routes";

const TABS = ["overview", "tasks", "limits"] as const;
type QueueTab = (typeof TABS)[number];

const HEADER_GLOW_STYLE = ambientGlowStyle({ at: "30% 0%", intensity: 0.08 });

export function QueueMonitorPage() {
  const { t } = useTranslation("queue");
  const [location, navigate] = useLocation();
  const search = useSearch();

  const activeTab = useMemo((): QueueTab => {
    const tab = new URLSearchParams(search).get("tab");
    return (TABS as readonly string[]).includes(tab ?? "") ? (tab as QueueTab) : "overview";
  }, [search]);

  const setActiveTab = useCallback(
    (next: QueueTab) => {
      const params = new URLSearchParams(search);
      if (next === "overview") params.delete("tab");
      else params.set("tab", next);
      const qs = params.toString();
      navigate(qs ? `${location}?${qs}` : location, { replace: true });
    },
    [navigate, location, search],
  );

  const tabRefs = useRef<Map<QueueTab, HTMLButtonElement>>(new Map());
  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: QueueTab) => {
    const idx = TABS.indexOf(current);
    let next: QueueTab | null = null;
    if (event.key === "ArrowRight") next = TABS[(idx + 1) % TABS.length];
    else if (event.key === "ArrowLeft") next = TABS[(idx - 1 + TABS.length) % TABS.length];
    else if (event.key === "Home") next = TABS[0];
    else if (event.key === "End") next = TABS[TABS.length - 1];
    if (!next) return;
    event.preventDefault();
    setActiveTab(next);
    const target = next;
    requestAnimationFrame(() => tabRefs.current.get(target)?.focus());
  };

  return (
    <div className="flex min-h-screen flex-col bg-bg text-text">
      <header className="relative border-b border-hairline" style={HEADER_GLOW_STYLE}>
        <div className="mx-auto flex max-w-6xl items-start gap-3 px-6 pb-4 pt-6">
          <button
            type="button"
            onClick={() => navigate(ROUTE_APP_PROJECTS)}
            aria-label={t("back")}
            title={t("back")}
            className={`mt-1 ${ICON_BTN_FILLED_CLS}`}
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <div>
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-text-4">
              admin · queue
            </div>
            <h1 className="font-editorial mt-0.5 text-[34px] leading-[1.05] tracking-tight text-text">{t("title")}</h1>
            <p className="mt-1.5 text-[13px] text-text-3">{t("subtitle")}</p>
            <p className="mt-1 text-[11.5px] text-text-4">{t("auto_refresh")}</p>
          </div>
        </div>
        <div
          role="tablist"
          aria-orientation="horizontal"
          aria-label={t("tabs_label")}
          className="mx-auto flex max-w-6xl items-center gap-2 overflow-x-auto px-6 pb-3"
        >
          {TABS.map((tab) => {
            const active = activeTab === tab;
            const cls = active
              ? "border-accent/45 bg-accent-dim text-text shadow-[inset_0_1px_0_oklch(1_0_0_/_0.05),0_0_22px_-10px_var(--color-accent-glow)]"
              : "border-hairline-soft bg-bg-grad-a/40 text-text-2 hover:border-hairline hover:text-text";
            return (
              <button
                key={tab}
                ref={(el) => {
                  if (el) tabRefs.current.set(tab, el);
                  else tabRefs.current.delete(tab);
                }}
                type="button"
                role="tab"
                id={`queue-tab-${tab}`}
                aria-selected={active}
                aria-controls="queue-panel"
                tabIndex={active ? 0 : -1}
                onClick={() => setActiveTab(tab)}
                onKeyDown={(e) => handleKeyDown(e, tab)}
                className={`shrink-0 rounded-[8px] border px-3.5 py-2 text-[12.5px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${cls}`}
              >
                {t(`tab.${tab}`)}
              </button>
            );
          })}
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        <div role="tabpanel" id="queue-panel" aria-labelledby={`queue-tab-${activeTab}`} tabIndex={0} className="focus-visible:outline-none">
          {activeTab === "overview" && <QueueOverviewTab />}
          {activeTab === "tasks" && <QueueTasksTab />}
          {activeTab === "limits" && <QueueLimitsTab />}
        </div>
      </main>
    </div>
  );
}
