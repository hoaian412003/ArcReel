import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { CopyButton } from "@/components/ui/CopyButton";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import type { KeyframeSheetRequest } from "@/types/keyframe";
import { errMsg } from "@/utils/async";

interface Props {
  projectName: string;
  episode: number;
  sheetId: string;
  onClose: () => void;
}

type Loaded =
  | { key: string; request: KeyframeSheetRequest; error: null }
  | { key: string; request: null; error: string };

/** 一张联合图下次生成会发出的提示词与按序编号的参考图，供生成前核对。 */
export function KeyframeSheetRequestDialog({ projectName, episode, sheetId, onClose }: Props) {
  const { t } = useTranslation("dashboard");
  const key = `${projectName}:${episode}:${sheetId}`;
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    API.getKeyframeSheetRequest(projectName, episode, sheetId, { signal: controller.signal })
      .then((request) => {
        if (!controller.signal.aborted) setLoaded({ key, request, error: null });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setLoaded({ key, request: null, error: errMsg(err) });
      });
    return () => controller.abort();
  }, [projectName, episode, sheetId, key]);

  const current = loaded?.key === key ? loaded : null;
  const request = current?.request ?? null;
  const titleId = `keyframe-request-title-${episode}-${sheetId}`;

  return (
    <GlassModal
      open
      onClose={onClose}
      labelledBy={titleId}
      widthClassName="w-full max-w-3xl"
      panelClassName="max-h-[85vh] overflow-y-auto rounded-2xl"
    >
      <div className="flex flex-col gap-4 p-5" data-testid="keyframe-request-dialog">
        <header className="flex items-center gap-2">
          <h2 id={titleId} className="flex-1 text-[15px] font-medium text-text">
            {t("keyframe_request_title", { sheet: sheetId })}
          </h2>
          <ModalCloseButton onClick={onClose} />
        </header>
        {current == null && <p className="text-[12.5px] text-text-4">{t("keyframe_request_loading")}</p>}
        {current?.error != null && (
          <p role="alert" className="font-mono text-[11.5px] text-amber-300">
            {t("keyframe_request_failed", { message: current.error })}
          </p>
        )}
        {request?.problem != null && (
          <p role="alert" className="text-[12.5px] text-amber-300">
            {t("keyframe_request_blocked", { name: request.problem.name })}
          </p>
        )}
        {request != null && request.problem === null && (
          <>
            <section className="flex flex-col gap-2">
              <h3 className="text-[12.5px] font-medium text-text-2">
                {t("keyframe_request_references", { count: request.references.length })}
              </h3>
              <ol className="grid grid-cols-2 gap-3 sm:grid-cols-3" data-testid="keyframe-request-references">
                {request.references.map((reference, index) => (
                  <li key={`${index}-${reference.label}`} className="flex flex-col gap-1.5">
                    <div className="flex aspect-square items-center justify-center overflow-hidden rounded-[6px] bg-black/40">
                      {reference.path ? (
                        <img
                          src={API.getFileUrl(projectName, reference.path)}
                          alt={reference.label}
                          className="h-full w-full object-contain"
                          loading="lazy"
                        />
                      ) : (
                        <span className="px-2 text-center text-[11px] text-text-4">{t("keyframe_request_outside")}</span>
                      )}
                    </div>
                    <span className="text-[11.5px] leading-snug text-text-2">{reference.label}</span>
                  </li>
                ))}
              </ol>
            </section>
            <section className="flex flex-col gap-2">
              <div className="flex items-center gap-2">
                <h3 className="flex-1 text-[12.5px] font-medium text-text-2">
                  {t("keyframe_request_prompt", { ratio: request.aspect_ratio })}
                </h3>
                <CopyButton text={request.prompt} label={t("keyframe_request_copy")} />
              </div>
              <pre
                className="max-h-[40vh] overflow-auto whitespace-pre-wrap rounded-[8px] border border-hairline bg-bg-grad-a/55 p-3 font-mono text-[11.5px] leading-relaxed text-text-2"
                data-testid="keyframe-request-prompt"
              >
                {request.prompt}
              </pre>
            </section>
          </>
        )}
      </div>
    </GlassModal>
  );
}
