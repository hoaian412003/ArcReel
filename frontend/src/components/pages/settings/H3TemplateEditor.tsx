import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";
import { errMsg } from "@/utils/async";

interface H3TemplateEditorProps {
  id: string;
  /** 编辑区展示的正文。 */
  text: string;
  /** 本层是否已经替换内置模版。 */
  isCustom: boolean;
  onChange: (next: string) => void;
  onRestore: () => void;
  maxChars: number;
  /** 额外的操作按钮（如全局层的「保存」）。 */
  actions?: React.ReactNode;
  error?: string | null;
}

/** H3 提示词编写模版的整份正文编辑器；「预览」用示例数据渲染并校验，不落盘。 */
export function H3TemplateEditor({ id, text, isCustom, onChange, onRestore, maxChars, actions, error }: H3TemplateEditorProps) {
  const { t } = useTranslation("dashboard");
  const [previewing, setPreviewing] = useState(false);
  // 预览记下它对应的正文：正文变了，旧预览不再展示。
  const [previewed, setPreviewed] = useState<{ text: string; result: { prompt: string } | { error: string } } | null>(
    null,
  );
  const preview = previewed?.text === text ? previewed.result : null;

  const runPreview = async () => {
    setPreviewing(true);
    try {
      setPreviewed({ text, result: await API.previewH3AuthoringOverride(text) });
    } catch (err) {
      setPreviewed({ text, result: { error: errMsg(err) } });
    } finally {
      setPreviewing(false);
    }
  };

  const tooLong = text.length > maxChars;

  return (
    <div>
      <textarea
        id={id}
        value={text}
        rows={18}
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 font-mono text-[12px] leading-relaxed text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
      <p className="mt-1.5 text-[11px] leading-relaxed text-text-4" role={tooLong || error ? "alert" : undefined}>
        {tooLong
          ? t("h3_template_too_long", { max: maxChars })
          : (error ?? (isCustom ? t("h3_template_active") : t("h3_template_default")))}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={() => void runPreview()} disabled={previewing || tooLong} className={GHOST_BTN_LG_CLS}>
          {previewing && <Loader2 aria-hidden className="h-3.5 w-3.5 motion-safe:animate-spin" />}
          {t("h3_template_preview")}
        </button>
        {isCustom && (
          <button type="button" onClick={onRestore} className={GHOST_BTN_LG_CLS}>
            {t("h3_template_restore")}
          </button>
        )}
        {actions}
      </div>
      {preview && "error" in preview && (
        <p role="alert" className="mt-3 whitespace-pre-wrap text-[12px] leading-relaxed text-danger">
          {preview.error}
        </p>
      )}
      {preview && "prompt" in preview && (
        <pre className="mt-3 max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 p-3 font-mono text-[11.5px] leading-relaxed text-text-2">
          {preview.prompt}
        </pre>
      )}
    </div>
  );
}

/** 全局层：读取内置正文与全局替换，保存 / 恢复各自直接落库。 */
export function H3AuthoringGlobalEditor() {
  const { t } = useTranslation("dashboard");
  const [builtin, setBuiltin] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [maxChars, setMaxChars] = useState(40000);
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    API.getH3AuthoringOverride({ signal: controller.signal }).then(
      (res) => {
        if (controller.signal.aborted) return;
        setBuiltin(res.builtin);
        setSaved(res.override);
        setMaxChars(res.max_chars);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setError(errMsg(err));
      },
    );
    return () => controller.abort();
  }, []);

  if (builtin === null) {
    return <p className="text-[12.5px] text-text-3">{error ?? t("prompt_templates_loading")}</p>;
  }

  const text = draft ?? saved ?? builtin;
  const dirty = draft !== null && draft !== (saved ?? builtin);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      // 与内置正文相同的内容不存为替换。
      if (text.trim() === builtin.trim()) {
        if (saved !== null) await API.deleteH3AuthoringOverride();
        setSaved(null);
      } else {
        await API.saveH3AuthoringOverride(text);
        setSaved(text);
      }
      setDraft(null);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    setBusy(true);
    setError(null);
    try {
      if (saved !== null) await API.deleteH3AuthoringOverride();
      setSaved(null);
      setDraft(null);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <H3TemplateEditor
      id="h3-template-global"
      text={text}
      isCustom={saved !== null || (draft !== null && draft.trim() !== builtin.trim())}
      onChange={setDraft}
      onRestore={() => void restore()}
      maxChars={maxChars}
      error={error}
      actions={
        <button
          type="button"
          onClick={() => void save()}
          disabled={busy || !dirty || text.length > maxChars}
          className={ACCENT_BTN_CLS}
          style={ACCENT_BUTTON_STYLE}
        >
          {busy && <Loader2 aria-hidden className="h-3.5 w-3.5 motion-safe:animate-spin" />}
          {t("common:save")}
        </button>
      }
    />
  );
}

/** 项目层：展示实际生效的正文（全局替换，否则内置），改动随项目设置页的保存一并提交。 */
export function H3AuthoringProjectEditor({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const [inherited, setInherited] = useState<{ text: string; maxChars: number } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    API.getH3AuthoringOverride({ signal: controller.signal }).then(
      (res) => {
        if (!controller.signal.aborted) setInherited({ text: res.override ?? res.builtin, maxChars: res.max_chars });
      },
      () => undefined,
    );
    return () => controller.abort();
  }, []);

  if (inherited === null) return null;
  return (
    <H3TemplateEditor
      id="h3-template-project"
      text={value || inherited.text}
      isCustom={value !== ""}
      onChange={(next) => onChange(next.trim() === inherited.text.trim() ? "" : next)}
      onRestore={() => onChange("")}
      maxChars={inherited.maxChars}
    />
  );
}
