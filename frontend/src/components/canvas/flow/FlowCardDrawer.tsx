import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Check, RotateCcw, X } from "lucide-react";
import type { FlowCard, FlowColumn } from "@/types/flow";
import type { Screenplay } from "@/types/screenplay";
import { ScriptReviewGate } from "@/components/canvas/timeline/ScriptReviewGate";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { ICON_BTN_FILLED_CLS } from "@/components/ui/darkroom-tokens";
import { DirectionReviewPanel } from "./DirectionReviewPanel";
import { ScreenplayReviewPanel } from "./ScreenplayReviewPanel";

interface FlowCardDrawerProps {
  projectName: string;
  card: FlowCard;
  column: FlowColumn;
  contentMode: string | undefined;
  busy: boolean;
  reviewVersion: number;
  onApprove: () => void;
  onRedo: () => void;
  onClose: () => void;
  /** 剧本审阅面板每次拿到最新剧本时回报，供「通过」带上审阅过的指纹。 */
  onScreenplayLoaded?: (screenplay: Screenplay) => void;
}

/** 卡片详情抽屉：人工列展示待审内容与「通过」按钮（分镜导演审阅另有「请 Agent 重做」）（拖拽之外的等价入口），其余列只给状态行。 */
export function FlowCardDrawer({
  projectName,
  card,
  column,
  contentMode,
  busy,
  reviewVersion,
  onApprove,
  onRedo,
  onClose,
  onScreenplayLoaded,
}: FlowCardDrawerProps) {
  const { t } = useTranslation("dashboard");
  const closeRef = useRef<HTMLButtonElement>(null);
  const human = column.kind === "human";

  useEffect(() => {
    closeRef.current?.focus();
  }, [card.episode]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const columnTitle = t(`flow_col_${card.column}`);
  const title = card.title || t("flow_card_untitled");

  return (
    <aside
      aria-label={`${t("flow_card_label", { episode: card.episode })} ${title}`}
      className="absolute inset-y-0 right-0 z-20 flex w-full max-w-[540px] flex-col border-l border-hairline shadow-2xl arc-slide-in"
      style={{ background: "var(--color-bg)" }}
    >
      <header className="flex items-start gap-3 border-b border-hairline px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[14px] font-medium text-text">
            <span className="mr-2 font-mono text-[12px] text-text-3">
              {t("flow_card_label", { episode: card.episode })}
            </span>
            {title}
          </h2>
          <p className="mt-0.5 text-[11.5px] text-text-3">{columnTitle}</p>
        </div>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          className={ICON_BTN_FILLED_CLS}
          aria-label={t("flow_drawer_close")}
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {card.column === "script_plan_review" && card.next_action === "review_screenplay" ? (
          <ScreenplayReviewPanel
            projectName={projectName}
            episode={card.episode}
            version={reviewVersion}
            onLoaded={onScreenplayLoaded}
          />
        ) : card.column === "script_plan_review" ? (
          contentMode === "drama" || contentMode === "narration" ? (
            <ScriptReviewGate key={card.episode} projectName={projectName} episode={card.episode} contentMode={contentMode} />
          ) : (
            <p className="text-[12.5px] text-text-3">{t("flow_script_plan_unsupported")}</p>
          )
        ) : card.column === "direction_review" ? (
          <DirectionReviewPanel projectName={projectName} episode={card.episode} version={reviewVersion} />
        ) : (
          <div className="flex flex-col gap-2">
            <p className="text-[12.5px] leading-relaxed text-text-2">
              {t("flow_drawer_status_only", { column: columnTitle })}
            </p>
            {card.next_action && (
              <p className="text-[12.5px] text-text">{t("flow_next_action", { action: card.next_action })}</p>
            )}
          </div>
        )}
      </div>

      {human && (
        <footer className="flex flex-wrap justify-end gap-2 border-t border-hairline px-4 py-3">
          {card.column === "direction_review" && (
            <SecondaryButton onClick={onRedo} disabled={busy} leadingIcon={<RotateCcw className="h-3.5 w-3.5" />}>
              {t("flow_redo")}
            </SecondaryButton>
          )}
          <PrimaryButton onClick={onApprove} disabled={busy} leadingIcon={<Check className="h-3.5 w-3.5" />}>
            {busy ? t("flow_approving") : t("flow_approve")}
          </PrimaryButton>
        </footer>
      )}
    </aside>
  );
}
