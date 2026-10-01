import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2, MessageSquareText, Pencil } from "lucide-react";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import { assetColor } from "./asset-colors";
import type { ScriptPreview } from "@/types";

export interface UnitDialogueCardProps {
  projectName: string;
  episode: number;
  /** 已保存的单元正文——即生成时实际下发的内容，而非编辑器里未保存的草稿。 */
  text: string;
  /**
   * 项目资产表版本：解析结果依赖它（未登记 speaker、角色缺参考音频等提示），
   * 调用侧传稳定引用，变化时重新拉取。
   */
  assets: string | undefined;
  /** 跳回编辑器修改正文；未提供时不显示入口。 */
  onEdit?: () => void;
}

/**
 * 生成前的台词核对卡：把该单元正文解析出的逐条台词与画外音只读地摆在生成按钮上方。
 * 台词由正文机械派生，卡片不提供就地编辑，改动一律回到编辑器。
 */
export function UnitDialogueCard({ projectName, episode, text, assets, onEdit }: UnitDialogueCardProps) {
  const { t } = useTranslation("dashboard");
  const [preview, setPreview] = useState<ScriptPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 结果对应的输入；与当前输入不一致即仍在加载（避免在 effect 里同步 setState）。
  const [settledFor, setSettledFor] = useState<{ text: string; assets: string | undefined } | null>(null);
  const loading = settledFor === null || settledFor.text !== text || settledFor.assets !== assets;

  useEffect(() => {
    const controller = new AbortController();
    API.previewReferenceScript(projectName, episode, text, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        setPreview(result);
        setError(null);
        setSettledFor({ text, assets });
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setPreview(null);
        setError(errMsg(e));
        setSettledFor({ text, assets });
      });
    return () => controller.abort();
  }, [projectName, episode, text, assets]);

  const utterances = preview?.utterances ?? [];
  const warnings = preview?.warnings ?? [];

  return (
    <section
      aria-label={t("unit_dialogue_card_title")}
      aria-busy={loading || undefined}
      className="rounded-lg border border-[var(--color-hairline-soft)] bg-[oklch(0.18_0.010_265_/_0.5)] p-3"
    >
      <div className="mb-2 flex items-center gap-1.5">
        <MessageSquareText className="h-3.5 w-3.5 text-[var(--color-text-3)]" aria-hidden="true" />
        <h3 className="text-xs font-semibold text-[var(--color-text-2)]">{t("unit_dialogue_card_title")}</h3>
        {loading && (
          <span role="status" className="inline-flex items-center">
            <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            <span className="sr-only">{t("script_preview_loading")}</span>
          </span>
        )}
        <span className="flex-1" />
        {onEdit && (
          <button
            type="button"
            onClick={onEdit}
            className="focus-ring inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-[var(--color-text-3)] hover:text-[var(--color-text-2)]"
          >
            <Pencil className="h-3 w-3" aria-hidden="true" />
            {t("unit_dialogue_card_edit")}
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="rounded-md bg-red-500/10 px-2.5 py-1.5 text-[11.5px] text-red-300">
          {t("script_preview_failed", { error })}
        </p>
      )}

      {warnings.length > 0 && (
        <ul
          aria-label={t("script_preview_warnings_label")}
          className="mb-2 flex flex-col gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 p-2"
        >
          {warnings.map((w, i) => (
            <li key={`${w.key}-${i}`} className="flex gap-1.5 text-[11.5px] leading-relaxed text-amber-200">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
              <span>{w.message}</span>
            </li>
          ))}
        </ul>
      )}

      {utterances.length > 0 ? (
        <ul className="flex flex-col gap-1.5 text-[11.5px]">
          {utterances.map((u, i) => {
            const palette = assetColor(u.kind === "dialogue" ? "character" : "unknown");
            return (
              <li key={`${u.index}-${i}`} className="flex items-baseline gap-2">
                <span className="shrink-0 font-mono tabular-nums text-[var(--color-text-4)]">{u.index}</span>
                <span
                  translate="no"
                  className={`shrink-0 rounded-sm px-1 ${palette.textClass} ${palette.bgClass}`}
                >
                  {u.kind === "dialogue" ? u.speaker : t("script_highlight_voiceover")}
                </span>
                <span className="min-w-0 flex-1 break-words text-[var(--color-text-2)]">{u.text}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        !loading &&
        !error && <p className="text-[11.5px] text-[var(--color-text-4)]">{t("unit_dialogue_card_empty")}</p>
      )}
    </section>
  );
}
