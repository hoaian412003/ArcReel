import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, Save, X } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import { errMsg } from "@/utils/async";
import {
  REFERENCE_UNIT_TYPES,
  type DirectorBeat,
  type DirectorPerformance,
  type ReferenceUnitType,
  type ReferenceVideoUnit,
  type UnitDirectionPatch,
} from "@/types";

export interface UnitDirectionSectionProps {
  projectName: string;
  episode: number;
  unit: ReferenceVideoUnit;
  /** 单元正被生成任务占用时只读。 */
  locked?: boolean;
}

interface DirectionDraft {
  unit_type: ReferenceUnitType | "";
  brief: string;
  beats: DirectorBeat[];
  performance: DirectorPerformance[];
  must_not_show: string;
  end_frame: string;
  continuity_in: string;
  continuity_out: string;
}

const FIELD_CLASS =
  "focus-ring w-full rounded-md border border-[var(--color-hairline)] bg-[oklch(0.22_0.011_265_/_0.5)] px-2 py-1.5 text-[12px] text-[var(--color-text)] placeholder:text-[var(--color-text-4)]";
const LABEL_CLASS = "font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-4)]";

function draftFromUnit(unit: ReferenceVideoUnit): DirectionDraft {
  return {
    unit_type: unit.unit_type ?? "",
    brief: unit.brief ?? "",
    beats: (unit.beats ?? []).map((beat) => ({ ...beat })),
    performance: (unit.performance ?? []).map((item) => ({ ...item })),
    must_not_show: unit.must_not_show ?? "",
    end_frame: unit.end_frame ?? "",
    continuity_in: unit.continuity_in ?? "",
    continuity_out: unit.continuity_out ?? "",
  };
}

/** 草稿里空白的节点 / 表演行不上传：用户加了行又没填，等于没有。 */
function cleaned(draft: DirectionDraft): DirectionDraft {
  return {
    ...draft,
    beats: draft.beats.filter((beat) => beat.action.trim()),
    performance: draft.performance.filter((item) => item.note.trim()),
  };
}

function toPatch(draft: DirectionDraft): UnitDirectionPatch {
  const next = cleaned(draft);
  return {
    ...(next.unit_type ? { unit_type: next.unit_type } : {}),
    brief: next.brief,
    beats: next.beats,
    performance: next.performance,
    must_not_show: next.must_not_show,
    end_frame: next.end_frame,
    continuity_in: next.continuity_in,
    continuity_out: next.continuity_out,
  };
}

/**
 * 单元的导演说明：这个单元为什么存在、角色怎么演、必须按住什么、最后一帧是什么。
 *
 * 说明由脚本规划产出，创作者在这里随意改写；保存只改导演说明，不动正文与 H3 提示词——
 * 要让改动进入画面，保存后再「重新生成 H3 提示词」。
 */
export function UnitDirectionSection({ projectName, episode, unit, locked = false }: UnitDirectionSectionProps) {
  const { t } = useTranslation("dashboard");
  const patchUnit = useReferenceVideoStore((s) => s.patchUnit);
  const pushToast = useAppStore((s) => s.pushToast);
  const saved = useMemo(() => draftFromUnit(unit), [unit]);
  const [draft, setDraft] = useState<DirectionDraft>(saved);
  const [baseline, setBaseline] = useState(saved);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 后端返回的新值落定（保存成功 / 重新加载）后，以它为新基线；用户正在改的草稿不被覆盖。
  if (baseline !== saved && JSON.stringify(baseline) !== JSON.stringify(saved)) {
    const wasClean = JSON.stringify(cleaned(draft)) === JSON.stringify(cleaned(baseline));
    setBaseline(saved);
    if (wasClean) setDraft(saved);
  }

  const dirty = JSON.stringify(cleaned(draft)) !== JSON.stringify(cleaned(baseline));
  const hasContent = Boolean(
    saved.unit_type || saved.brief || saved.beats.length || saved.performance.length || saved.must_not_show || saved.end_frame,
  );
  const disabled = locked || saving;

  const set = <K extends keyof DirectionDraft>(key: K, value: DirectionDraft[K]) =>
    setDraft((prev) => ({ ...prev, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await patchUnit(projectName, episode, unit.unit_id, toPatch(draft));
      pushToast(t("unit_direction_saved"), "success");
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      aria-label={t("unit_direction_title")}
      className="space-y-3 border-b border-[var(--color-hairline-soft)] px-3.5 py-3"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--color-text-3)]">Director</h3>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={!dirty || disabled}
          className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-[var(--color-hairline)] px-2 py-1 text-[11px] text-[var(--color-text-2)] enabled:hover:text-[var(--color-text)] disabled:opacity-40"
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" /> : <Save className="h-3 w-3" aria-hidden="true" />}
          {t("unit_direction_save")}
        </button>
      </div>

      {!hasContent && !dirty && <p className="text-[11.5px] text-[var(--color-text-4)]">{t("unit_direction_empty")}</p>}

      <label className="block space-y-1">
        <span className={LABEL_CLASS}>{t("unit_direction_type")}</span>
        <select
          value={draft.unit_type}
          disabled={disabled}
          onChange={(e) => set("unit_type", e.target.value as ReferenceUnitType | "")}
          className={FIELD_CLASS}
        >
          <option value="">{t("unit_direction_type_none")}</option>
          {REFERENCE_UNIT_TYPES.map((value) => (
            <option key={value} value={value}>
              {t(`unit_type_${value}`)}
            </option>
          ))}
        </select>
      </label>

      <label className="block space-y-1">
        <span className={LABEL_CLASS}>{t("unit_direction_brief")}</span>
        <textarea
          value={draft.brief}
          disabled={disabled}
          rows={3}
          placeholder={t("unit_direction_brief_placeholder")}
          onChange={(e) => set("brief", e.target.value)}
          className={`${FIELD_CLASS} resize-y`}
        />
      </label>

      <div className="space-y-1.5">
        <span className={LABEL_CLASS}>{t("unit_direction_beats")}</span>
        {draft.beats.map((beat, index) => (
          <div key={index} className="flex items-center gap-1.5">
            <input
              type="number"
              min={0}
              max={100}
              value={beat.at_pct}
              disabled={disabled}
              aria-label={t("unit_direction_beat_pct", { index: index + 1 })}
              onChange={(e) =>
                set(
                  "beats",
                  draft.beats.map((b, i) =>
                    i === index ? { ...b, at_pct: Math.min(100, Math.max(0, Number(e.target.value) || 0)) } : b,
                  ),
                )
              }
              className={`${FIELD_CLASS} w-16 flex-none`}
            />
            <span className="text-[11px] text-[var(--color-text-4)]">%</span>
            <input
              value={beat.action}
              disabled={disabled}
              aria-label={t("unit_direction_beat_action", { index: index + 1 })}
              onChange={(e) =>
                set("beats", draft.beats.map((b, i) => (i === index ? { ...b, action: e.target.value } : b)))
              }
              className={FIELD_CLASS}
            />
            <button
              type="button"
              disabled={disabled}
              aria-label={t("unit_direction_remove_row")}
              onClick={() => set("beats", draft.beats.filter((_, i) => i !== index))}
              className="focus-ring flex-none rounded p-1 text-[var(--color-text-4)] hover:text-[var(--color-text)]"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={disabled}
          onClick={() => set("beats", [...draft.beats, { at_pct: draft.beats.length ? 100 : 0, action: "" }])}
          className="focus-ring inline-flex items-center gap-1 text-[11px] text-[var(--color-text-3)] hover:text-[var(--color-text)]"
        >
          <Plus className="h-3 w-3" aria-hidden="true" />
          {t("unit_direction_add_beat")}
        </button>
      </div>

      <div className="space-y-1.5">
        <span className={LABEL_CLASS}>{t("unit_direction_performance")}</span>
        {draft.performance.map((item, index) => (
          <div key={index} className="flex items-center gap-1.5">
            <input
              value={item.character}
              disabled={disabled}
              placeholder={t("unit_direction_character")}
              aria-label={t("unit_direction_character")}
              onChange={(e) =>
                set("performance", draft.performance.map((p, i) => (i === index ? { ...p, character: e.target.value } : p)))
              }
              className={`${FIELD_CLASS} w-32 flex-none`}
            />
            <input
              value={item.note}
              disabled={disabled}
              placeholder={t("unit_direction_performance_placeholder")}
              aria-label={t("unit_direction_performance_note", { index: index + 1 })}
              onChange={(e) =>
                set("performance", draft.performance.map((p, i) => (i === index ? { ...p, note: e.target.value } : p)))
              }
              className={FIELD_CLASS}
            />
            <button
              type="button"
              disabled={disabled}
              aria-label={t("unit_direction_remove_row")}
              onClick={() => set("performance", draft.performance.filter((_, i) => i !== index))}
              className="focus-ring flex-none rounded p-1 text-[var(--color-text-4)] hover:text-[var(--color-text)]"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        ))}
        <button
          type="button"
          disabled={disabled}
          onClick={() => set("performance", [...draft.performance, { character: "", note: "" }])}
          className="focus-ring inline-flex items-center gap-1 text-[11px] text-[var(--color-text-3)] hover:text-[var(--color-text)]"
        >
          <Plus className="h-3 w-3" aria-hidden="true" />
          {t("unit_direction_add_performance")}
        </button>
      </div>

      {(
        [
          ["must_not_show", "unit_direction_must_not_show"],
          ["end_frame", "unit_direction_end_frame"],
          ["continuity_in", "unit_direction_continuity_in"],
          ["continuity_out", "unit_direction_continuity_out"],
        ] as const
      ).map(([key, label]) => (
        <label key={key} className="block space-y-1">
          <span className={LABEL_CLASS}>{t(label)}</span>
          <input value={draft[key]} disabled={disabled} onChange={(e) => set(key, e.target.value)} className={FIELD_CLASS} />
        </label>
      ))}

      {error && (
        <p role="alert" className="text-[11.5px] text-red-300">
          {error}
        </p>
      )}
      <p className="text-[11px] text-[var(--color-text-4)]">{t("unit_direction_apply_hint")}</p>
    </section>
  );
}
