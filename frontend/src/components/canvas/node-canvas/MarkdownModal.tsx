import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { API } from "@/api";
import { MarkdownText } from "./MarkdownText";
import { GlassModal } from "@/components/ui/GlassModal";
import { ICON_BTN_CLS } from "@/components/ui/darkroom-tokens";

/** 文档节点的全文：只读，来源文本与分集计划等。 */
export function MarkdownModal({
  projectName,
  file,
  title,
  onClose,
}: {
  projectName: string;
  file: string;
  title: string;
  onClose: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const [content, setContent] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    API.getCanvasMarkdown(projectName, file)
      .then((res) => {
        if (!controller.signal.aborted) setContent(res.content);
      })
      .catch(() => {
        if (!controller.signal.aborted) setContent("");
      });
    return () => controller.abort();
  }, [file, projectName]);
  return (
    <GlassModal open onClose={onClose} ariaLabel={title} widthClassName="w-full max-w-3xl" panelClassName="max-h-[85vh] flex flex-col">
      <div className="flex items-center gap-2 border-b border-hairline px-5 py-3">
        <h2 className="flex-1 truncate text-[14px] font-semibold text-text">{title}</h2>
        <span className="truncate text-[11px] text-text-4">{file}</span>
        <button type="button" className={ICON_BTN_CLS} onClick={onClose} aria-label={t("node_canvas_close")}>
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
        <MarkdownText
          className="text-[13px]"
          source={content === null ? t("node_canvas_loading") : content.trim() || t("node_canvas_markdown_empty")}
        />
      </div>
    </GlassModal>
  );
}
