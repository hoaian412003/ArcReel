import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";
import { API } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { ACCENT_BTN_SM_CLS, GHOST_BTN_CLS, ICON_BTN_CLS } from "@/components/ui/darkroom-tokens";
import type { CanvasFlowEpisode, FlowStepState } from "@/types/canvas";

const REST_LIMIT = 30;
const STEPS = ["script", "direction", "keyframe", "h3", "video", "voice"] as const;
const TONE: Record<FlowStepState, string> = {
  done: "border-emerald-400/50 bg-emerald-400/15 text-emerald-200",
  stale: "border-amber-400/50 bg-amber-400/15 text-amber-200",
  empty: "border-dashed border-hairline text-text-4",
};

/** 流程总览：每一集各步骤的状态；点一个格子，画布飞到对应的节点。 */
export function FlowModal({
  projectName,
  onClose,
  onJump,
  onImported,
}: {
  projectName: string;
  onClose: () => void;
  /** 跳到画布上的某个节点（分组或单元里的节点）。 */
  onJump: (nodeId: string) => void;
  /** 一集被摆上画布之后，让画布重读。 */
  onImported: () => Promise<void>;
}) {
  const { t } = useTranslation("dashboard");
  const [episodes, setEpisodes] = useState<CanvasFlowEpisode[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [reloadKey, setReloadKey] = useState(0);
  const [showAll, setShowAll] = useState(false);
  // 已经有单元脚本或已在画布上的集排在前面；其余（只有来源、还没规划）收起来，需要时再展开前几集。
  const worked = (episodes ?? []).filter((ep) => ep.units.length > 0 || ep.group_id);
  const rest = (episodes ?? []).filter((ep) => ep.units.length === 0 && !ep.group_id);
  useEffect(() => {
    const controller = new AbortController();
    API.getCanvasFlow(projectName)
      .then((res) => {
        if (!controller.signal.aborted) setEpisodes(res.episodes);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err));
      });
    return () => controller.abort();
  }, [projectName, reloadKey]);

  const importEpisode = async (episode: number) => {
    setBusy(episode);
    try {
      await API.importCanvasEpisode(projectName, episode);
      await onImported();
      setReloadKey((key) => key + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <GlassModal open onClose={onClose} ariaLabel={t("node_canvas_flow_title")} widthClassName="w-full max-w-4xl" panelClassName="max-h-[85vh] flex flex-col">
      <div className="flex items-center gap-2 border-b border-hairline px-5 py-3">
        <h2 className="flex-1 text-[14px] font-semibold text-text">{t("node_canvas_flow_title")}</h2>
        <button type="button" className={ICON_BTN_CLS} onClick={onClose} aria-label={t("node_canvas_close")}>
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
        {error ? <p className="mb-3 text-[12px] text-red-300">{error}</p> : null}
        {episodes === null ? (
          <p className="flex items-center gap-2 text-[12.5px] text-text-3">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {t("node_canvas_loading")}
          </p>
        ) : episodes.length === 0 ? (
          <p className="text-[12.5px] text-text-3">{t("node_canvas_flow_empty")}</p>
        ) : (
          [...worked, ...(showAll ? rest.slice(0, REST_LIMIT) : [])].map((ep) => (
            <section key={ep.episode} className="mb-6">
              <div className="mb-2 flex items-center gap-3">
                <h3 className="text-[13px] font-semibold text-text">
                  {t("node_canvas_flow_episode", { episode: ep.episode })} {ep.title}
                </h3>
                <span className="text-[11px] text-text-4">
                  {ep.source ? t("node_canvas_flow_has_source") : t("node_canvas_flow_no_source")} ·{" "}
                  {ep.plan ? t("node_canvas_flow_has_plan") : t("node_canvas_flow_no_plan")}
                </span>
                {ep.group_id ? (
                  <button type="button" className={`ml-auto ${GHOST_BTN_CLS}`} onClick={() => onJump(ep.group_id as string)}>
                    {t("node_canvas_flow_show")}
                  </button>
                ) : (
                  <button type="button" className={`ml-auto ${ACCENT_BTN_SM_CLS}`} disabled={busy !== null} onClick={() => void importEpisode(ep.episode)}>
                    {busy === ep.episode ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
                    {t("node_canvas_flow_import")}
                  </button>
                )}
              </div>
              {ep.units.length === 0 ? (
                <p className="text-[12px] text-text-4">{t("node_canvas_flow_no_units")}</p>
              ) : (
                <div className="grid gap-1.5 text-[11.5px]" style={{ gridTemplateColumns: `70px repeat(${STEPS.length}, minmax(0, 1fr))` }}>
                  <span />
                  {STEPS.map((step) => (
                    <span key={step} className="px-1 text-text-3">
                      {t(`node_canvas_flow_step_${step}`)}
                    </span>
                  ))}
                  {ep.units.map((unit) => (
                    <FlowRow key={unit.unit_id} unit={unit} onJump={onJump} />
                  ))}
                </div>
              )}
            </section>
          ))
        )}
        {rest.length > 0 ? (
          <div className="mt-2 flex items-center gap-3 text-[12px] text-text-3">
            <span>{t("node_canvas_flow_rest", { count: rest.length })}</span>
            <button type="button" className={GHOST_BTN_CLS} onClick={() => setShowAll((on) => !on)}>
              {showAll ? t("node_canvas_flow_rest_hide") : t("node_canvas_flow_rest_show", { count: Math.min(REST_LIMIT, rest.length) })}
            </button>
          </div>
        ) : null}
      </div>
    </GlassModal>
  );
}

function FlowRow({ unit, onJump }: { unit: CanvasFlowEpisode["units"][number]; onJump: (nodeId: string) => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <>
      <span className="flex items-center text-text-3">{unit.unit_id}</span>
      {STEPS.map((step) => {
        const cell = unit.steps[step];
        const state = cell?.state ?? "empty";
        const nodeId = cell?.node_id ?? null;
        return (
          <button
            key={step}
            type="button"
            disabled={!nodeId}
            title={nodeId ? t("node_canvas_flow_jump") : t("node_canvas_flow_not_on_canvas")}
            className={`rounded-[7px] border px-2 py-1.5 text-left transition-colors ${TONE[state]} ${nodeId ? "hover:brightness-125" : "cursor-default"}`}
            onClick={() => nodeId && onJump(nodeId)}
          >
            {t(`node_canvas_flow_state_${state}`)}
          </button>
        );
      })}
    </>
  );
}
