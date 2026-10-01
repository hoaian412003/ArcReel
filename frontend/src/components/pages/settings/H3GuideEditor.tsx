import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
import type { H3GuidePreset } from "@/types";

/** 与后端 `lib.prompts.h3_guide_preset.MAX_CUSTOM_GUIDE_CHARS` 同值。 */
export const H3_GUIDE_CUSTOM_MAX_CHARS = 4000;

interface H3GuideEditorProps {
  id: string;
  preset: H3GuidePreset;
  /** 本层的自定义正文；空串 = 本层没有自定义。 */
  value: string;
  onChange: (next: string) => void;
  /** 本层没有自定义时实际生效的上层正文（全局自定义）；缺省即所选预设的内置指南。 */
  inheritedText?: string;
}

/**
 * H3 写作指南正文编辑器：未自定义时展示实际生效的指南，改动即成为本层的自定义，
 * 与生效文本相同的内容不存为自定义；「恢复」清除本层自定义。
 */
export function H3GuideEditor({ id, preset, value, onChange, inheritedText }: H3GuideEditorProps) {
  const { t } = useTranslation("dashboard");
  const [builtIn, setBuiltIn] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    API.getPromptPartial(`shared/h3_guide/${preset}`, { signal: controller.signal })
      .then((entry) => {
        if (controller.signal.aborted) return;
        setBuiltIn(entry.source.trim());
        setLoadFailed(false);
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadFailed(true);
      });
    return () => controller.abort();
  }, [preset]);

  const fallback = inheritedText?.trim() || builtIn;
  const shown = value || fallback;
  const tooLong = shown.length > H3_GUIDE_CUSTOM_MAX_CHARS;

  return (
    <div>
      <textarea
        id={id}
        value={shown}
        rows={6}
        spellCheck={false}
        onChange={(e) => {
          const next = e.target.value;
          onChange(next.trim() === fallback.trim() ? "" : next);
        }}
        className="w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 font-mono text-[12px] leading-relaxed text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
      <div className="mt-1.5 flex items-center justify-between gap-3">
        <p className="text-[11px] leading-relaxed text-text-4" role={tooLong ? "alert" : undefined}>
          {tooLong
            ? t("h3_guide_custom_too_long", { max: H3_GUIDE_CUSTOM_MAX_CHARS })
            : loadFailed
              ? t("h3_guide_custom_load_failed")
              : value
                ? t("h3_guide_custom_active")
                : t("h3_guide_custom_default")}
        </p>
        {value && (
          <button type="button" onClick={() => onChange("")} className={GHOST_BTN_LG_CLS}>
            {t("h3_guide_custom_reset")}
          </button>
        )}
      </div>
    </div>
  );
}
