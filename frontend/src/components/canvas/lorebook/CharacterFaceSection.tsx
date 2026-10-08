import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, ScanFace } from "lucide-react";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { CharacterDerivativeSheet } from "./CharacterDerivativeSheet";
import { useCharacterDerivativeSheets } from "./useCharacterDerivativeSheets";
import type { CharacterDerivative } from "@/types";

/** 脸部特写登记为角色的一条衍生，名字固定，脚本中写作 `@[角色/Face]`。 */
export const FACE_DERIVATIVE_NAME = "Face";

const FACE_DESCRIPTION =
  "Tight front-facing close-up of the face and head only, neutral expression, even soft lighting, plain background. Identity, hair and facial features exactly as the base look.";

interface CharacterFaceSectionProps {
  projectName: string;
  characterName: string;
  derivatives: Record<string, CharacterDerivative>;
  ownerHasSheet: boolean;
  busy?: boolean;
  readOnly?: boolean;
  onReload?: () => Promise<unknown> | void;
}

/**
 * 角色卡上的脸部特写一格：未登记时给出一键新增入口，已登记则复用衍生资产图的生成、
 * 编辑与版本回退。脸图是对本体资产图的一次编辑，因此没有本体图时不放行生成。
 */
export function CharacterFaceSection({
  projectName,
  characterName,
  derivatives,
  ownerHasSheet,
  busy = false,
  readOnly = false,
  onReload,
}: CharacterFaceSectionProps) {
  const { t } = useTranslation("assets");
  const [pending, setPending] = useState(false);
  const registered = FACE_DERIVATIVE_NAME in derivatives;
  const { statuses, refresh } = useCharacterDerivativeSheets(projectName, characterName, registered);

  if (!registered && readOnly) return null;

  const handleAdd = async () => {
    if (busy || pending) return;
    if (rejectIfAssetBusy("character", projectName, characterName, t, "assets:derivative_busy_hint")) return;
    setPending(true);
    try {
      await API.addCharacterDerivative(projectName, characterName, FACE_DERIVATIVE_NAME, FACE_DESCRIPTION);
      await onReload?.();
      refresh();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="min-w-0">
      <p className="font-mono text-[10px] uppercase tracking-[0.18em]" style={{ color: "var(--color-text-4)" }}>
        FACE
      </p>
      {registered ? (
        <CharacterDerivativeSheet
          projectName={projectName}
          characterName={characterName}
          derivativeName={FACE_DERIVATIVE_NAME}
          status={statuses[FACE_DERIVATIVE_NAME]}
          ownerHasSheet={ownerHasSheet}
          ratio="1:1"
          busy={busy || pending || readOnly}
          onRestore={async () => {
            await onReload?.();
            refresh();
          }}
        />
      ) : (
        <button
          type="button"
          onClick={() => void handleAdd()}
          disabled={busy || pending || !ownerHasSheet}
          className="focus-ring mt-1.5 flex aspect-square w-full flex-col items-center justify-center gap-1.5 rounded-lg px-2 text-center text-[12px] transition-colors hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
          style={{ border: "1px dashed var(--color-hairline)", color: "var(--color-text-3)" }}
        >
          {pending ? <Loader2 className="h-5 w-5 animate-spin" /> : <ScanFace className="h-5 w-5" />}
          {ownerHasSheet ? t("assets:face_add") : t("assets:derivative_owner_sheet_required")}
        </button>
      )}
    </div>
  );
}
