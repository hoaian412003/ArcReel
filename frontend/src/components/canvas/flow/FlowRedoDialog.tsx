import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { GlassModal } from "@/components/ui/GlassModal";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";

/** 「请 Agent 重做」的备注弹窗；备注可留空。 */
export function FlowRedoDialog({
  open,
  episode,
  loading,
  onSubmit,
  onCancel,
}: {
  open: boolean;
  episode: number;
  loading: boolean;
  onSubmit: (note: string) => void | Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const titleId = useId();
  const noteId = useId();
  const [note, setNote] = useState("");

  return (
    <GlassModal open={open} onClose={loading ? () => {} : onCancel} labelledBy={titleId}>
      <form
        className="flex flex-col gap-3 px-6 pb-6 pt-5"
        onSubmit={(e) => {
          e.preventDefault();
          void onSubmit(note.trim());
        }}
      >
        <h2 id={titleId} className="display-serif text-[17px] font-semibold tracking-tight text-text">
          {t("dashboard:flow_redo_title", { episode })}
        </h2>
        <p className="text-[12.5px] leading-relaxed text-text-3">{t("dashboard:flow_redo_desc")}</p>
        <label htmlFor={noteId} className="text-[12px] text-text-2">
          {t("dashboard:flow_redo_note_label")}
        </label>
        <textarea
          id={noteId}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder={t("dashboard:flow_redo_note_placeholder")}
          rows={4}
          disabled={loading}
          className={INPUT_CLS}
        />
        <div className="mt-1 flex justify-end gap-2">
          <SecondaryButton onClick={onCancel} disabled={loading}>
            {t("common:cancel")}
          </SecondaryButton>
          <PrimaryButton type="submit" disabled={loading}>
            {loading ? t("dashboard:flow_redo_sending") : t("dashboard:flow_redo_submit")}
          </PrimaryButton>
        </div>
      </form>
    </GlassModal>
  );
}
