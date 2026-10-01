import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { useAppStore } from "@/stores/app-store";
import type { ReferenceDeclaredResource, ReferenceUnitCapability } from "@/types";
import { formatNameList } from "@/utils/list-format";
import { assetFocusTarget } from "@/utils/task-target";
import { bucketLabel } from "./unit-tier-problem";

/**
 * 声明引用与可用参考图分裂的结构化提示：逐条点名缺图 / 未登记的引用与桶的改变。服务端已按
 * 可用图定桶，执行侧会以同样的问题阻断；画布与内容确认面板共用这一份，不静默换桶。
 */
// 名字列表交给 Intl 按界面语言排分隔符；占位符先占住每一项，再换回可点击的链接。
const SLOT = "\u0001";
const SLOT_PATTERN = new RegExp(`${SLOT}(\\d+)${SLOT}`);

export function ReferenceSplitAlert({
  capability,
  className,
}: {
  capability: ReferenceUnitCapability;
  className: string;
}) {
  const { t, i18n } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  const triggerScrollTo = useAppStore((s) => s.triggerScrollTo);

  const openAsset = (ref: ReferenceDeclaredResource) => {
    const target = assetFocusTarget(ref.type, ref.name);
    if (!target) return;
    setLocation(target.route);
    triggerScrollTo({
      type: target.type,
      id: target.id,
      route: target.route,
      highlight_style: target.highlight_style ?? "flash",
    });
  };

  const unavailable = capability.unavailable_references;
  const unavailableLine = t("reference_unit_unavailable_references", {
    names: formatNameList(unavailable.map((_, i) => `${SLOT}${i}${SLOT}`), i18n.language),
    interpolation: { escapeValue: false },
  });

  return (
    <div role="alert" data-testid="reference-split-alert" className={className}>
      <p className="font-medium">{t("reference_unit_split_title")}</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-4">
        {unavailable.length > 0 && (
          <li>
            {unavailableLine.split(SLOT_PATTERN).map((part, i) => {
              if (i % 2 === 0) return <Fragment key={i}>{part}</Fragment>;
              const ref = unavailable[Number(part)];
              if (!ref) return null;
              if (!assetFocusTarget(ref.type, ref.name)) {
                return <Fragment key={i}>{ref.name}</Fragment>;
              }
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => openAsset(ref)}
                  className="rounded underline underline-offset-2 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current"
                >
                  {ref.name}
                </button>
              );
            })}
          </li>
        )}
        {capability.unregistered_references.length > 0 && (
          <li>
            {t("reference_unit_unregistered_references", {
              names: formatNameList(capability.unregistered_references, i18n.language),
            })}
          </li>
        )}
        {capability.declared_capability !== capability.hydrated_capability && (
          <li>
            {t("reference_unit_bucket_changed", {
              declared: bucketLabel(t, capability.declared_capability),
              hydrated: bucketLabel(t, capability.hydrated_capability),
            })}
          </li>
        )}
      </ul>
    </div>
  );
}
