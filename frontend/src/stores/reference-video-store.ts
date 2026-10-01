// frontend/src/stores/reference-video-store.ts
import { create } from "zustand";
import { API } from "@/api";
import { errMsg } from "@/utils/async";
import type {
  DubLinePatch,
  H3PromptSections,
  H3PromptStatus,
  ReferenceUnitCapabilityMap,
  ReferenceVideoUnit,
  TransitionType,
} from "@/types";

interface AddUnitPayload {
  prompt: string;
  duration_seconds?: number;
  transition_to_next?: TransitionType;
  note?: string | null;
}

interface PatchUnitPayload {
  prompt?: string;
  duration_seconds?: number;
  transition_to_next?: TransitionType;
  note?: string | null;
  dub_lines?: DubLinePatch[];
}

/** Cache key isolating units per (project, episode) — switching projects with
 * the same episode number must not surface the previous project's units. */
export function referenceVideoCacheKey(projectName: string, episode: number): string {
  return `${projectName}::${episode}`;
}

interface ReferenceVideoStore {
  /** Keyed by `${projectName}::${episode}`. */
  unitsByEpisode: Record<string, ReferenceVideoUnit[]>;
  /**
   * 同 key 下逐单元的服务端定桶结论（桶、档位、引用分裂），随单元列表一起到达，写入口的
   * 响应按单元合并——正文改了引用，桶与档位当场随响应更新，画布不自判。
   */
  unitCapabilitiesByEpisode: Record<string, ReferenceUnitCapabilityMap>;
  /**
   * 同 key 下逐单元的 H3 提示词状态。只有 H3 提示词档的项目才有这一项，缺席即该项目不使用 H3 提示词。
   */
  h3PromptStatusByEpisode: Record<string, Record<string, H3PromptStatus>>;
  selectedUnitId: string | null;
  /**
   * 同 key 下被勾选的 unit id（批量生成、配音等批量动作的作用对象）。空即未启用勾选，
   * 批量动作回落到各自的默认口径。随单元列表加载与删除同步剔除已不存在的 id。
   */
  checkedUnitIdsByEpisode: Record<string, string[]>;
  loading: boolean;
  error: string | null;

  loadUnits: (projectName: string, episode: number) => Promise<void>;
  addUnit: (projectName: string, episode: number, payload: AddUnitPayload) => Promise<ReferenceVideoUnit>;
  patchUnit: (projectName: string, episode: number, unitId: string, patch: PatchUnitPayload) => Promise<ReferenceVideoUnit>;
  deleteUnit: (projectName: string, episode: number, unitId: string) => Promise<void>;
  reorderUnits: (projectName: string, episode: number, unitIds: string[]) => Promise<void>;
  select: (unitId: string | null) => void;
  toggleChecked: (projectName: string, episode: number, unitId: string) => void;
  /** 整体替换该剧集的勾选集合；传空数组即清空。 */
  setChecked: (projectName: string, episode: number, unitIds: string[]) => void;
  /** 手写 H3 六段载荷；校验不过时抛出后端给出的原因。 */
  saveH3Prompt: (projectName: string, episode: number, unitId: string, sections: H3PromptSections) => Promise<void>;
  /**
   * 重新生成 H3 提示词（单元正文不动）：入队文本任务后轮询单元列表，直到被点名的单元都回到 current
   * 或超时。unitIds 缺省即本集全部单元。
   */
  regenerateH3Prompts: (projectName: string, episode: number, unitIds?: string[]) => Promise<void>;
}

/** 重新生成 H3 提示词后轮询单元列表的间隔与上限：文本模型一次调用通常在数十秒内。 */
const H3_POLL_INTERVAL_MS = 3000;
const H3_POLL_MAX_ATTEMPTS = 60;

// 每个 cache key 上最新一次 loadUnits 的序号：`loadUnits` 有多个入口（画布 effect 随
// 项目/剧集/任务完成失效重跑、单元写入后重拉、手动重试），同一 key 上可能有多个请求并发
// 在途。没有这道校验时，先发后到的旧响应会盖掉新响应里已经生成的成片，界面停在「无成片」
// 直到下一次失效。响应落定时序号已被更新的请求接管即丢弃，只让最新一次写回。
const _loadSeqByKey = new Map<string, number>();
let _loadSeq = 0;

/**
 * 作废该 cache key 上所有在途加载。
 *
 * 写入口（增删改排序）落定后调用：迟到的 GET 读的是写入之前的列表，直接写回会把用户刚做的
 * 编辑、增删或排序在界面上暂时撤销，直到下一次失效才复原。
 *
 * 一并结算 `loading`：被作废的那次加载会在响应落定时直接丢弃，不再自己复位，而作废它的是
 * 写入口、不是另一次加载，没有接管方来收尾——不在这里落定，画布会一直停在加载态。
 */
function invalidateInFlightLoads(cacheKey: string, set: (patch: { loading: false }) => void): void {
  _loadSeqByKey.set(cacheKey, ++_loadSeq);
  set({ loading: false });
}

export const useReferenceVideoStore = create<ReferenceVideoStore>((set, get) => ({
  unitsByEpisode: {},
  unitCapabilitiesByEpisode: {},
  h3PromptStatusByEpisode: {},
  selectedUnitId: null,
  checkedUnitIdsByEpisode: {},
  loading: false,
  error: null,

  loadUnits: async (projectName, episode) => {
    const cacheKey = referenceVideoCacheKey(projectName, episode);
    const seq = ++_loadSeq;
    _loadSeqByKey.set(cacheKey, seq);
    set({ loading: true, error: null });
    try {
      const { units, unit_capabilities, h3_prompt_status } = await API.listReferenceVideoUnits(projectName, episode);
      if (_loadSeqByKey.get(cacheKey) !== seq) return;
      set((s) => {
        const { [cacheKey]: _previous, ...otherStatuses } = s.h3PromptStatusByEpisode;
        return {
          unitsByEpisode: { ...s.unitsByEpisode, [cacheKey]: units },
          unitCapabilitiesByEpisode: { ...s.unitCapabilitiesByEpisode, [cacheKey]: unit_capabilities },
          h3PromptStatusByEpisode: h3_prompt_status
            ? { ...otherStatuses, [cacheKey]: h3_prompt_status }
            : otherStatuses,
          checkedUnitIdsByEpisode: pruneChecked(s.checkedUnitIdsByEpisode, cacheKey, units),
          loading: false,
        };
      });
    } catch (e) {
      if (_loadSeqByKey.get(cacheKey) !== seq) return;
      set({ loading: false, error: errMsg(e) });
    }
  },

  addUnit: async (projectName, episode, payload) => {
    const { unit, unit_capability } = await API.addReferenceVideoUnit(projectName, episode, payload);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: [...list, unit] },
        unitCapabilitiesByEpisode: {
          ...s.unitCapabilitiesByEpisode,
          [key]: { ...s.unitCapabilitiesByEpisode[key], [unit.unit_id]: unit_capability },
        },
        selectedUnitId: unit.unit_id,
      };
    });
    refreshH3PromptStatuses(projectName, episode);
    return unit;
  },

  patchUnit: async (projectName, episode, unitId, patch) => {
    const { unit, unit_capability } = await API.patchReferenceVideoUnit(projectName, episode, unitId, patch);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      return {
        unitsByEpisode: {
          ...s.unitsByEpisode,
          [key]: list.map((u) => (u.unit_id === unitId ? unit : u)),
        },
        unitCapabilitiesByEpisode: {
          ...s.unitCapabilitiesByEpisode,
          [key]: { ...s.unitCapabilitiesByEpisode[key], [unitId]: unit_capability },
        },
      };
    });
    refreshH3PromptStatuses(projectName, episode);
    return unit;
  },

  deleteUnit: async (projectName, episode, unitId) => {
    await API.deleteReferenceVideoUnit(projectName, episode, unitId);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const list = s.unitsByEpisode[key] ?? [];
      const { [unitId]: _removed, ...remaining } = s.unitCapabilitiesByEpisode[key] ?? {};
      return {
        unitsByEpisode: { ...s.unitsByEpisode, [key]: list.filter((u) => u.unit_id !== unitId) },
        unitCapabilitiesByEpisode: { ...s.unitCapabilitiesByEpisode, [key]: remaining },
        checkedUnitIdsByEpisode: pruneChecked(
          s.checkedUnitIdsByEpisode,
          key,
          list.filter((u) => u.unit_id !== unitId),
        ),
        selectedUnitId: s.selectedUnitId === unitId ? null : s.selectedUnitId,
      };
    });
  },

  reorderUnits: async (projectName, episode, unitIds) => {
    const { units } = await API.reorderReferenceVideoUnits(projectName, episode, unitIds);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => ({
      unitsByEpisode: { ...s.unitsByEpisode, [referenceVideoCacheKey(projectName, episode)]: units },
    }));
  },

  select: (unitId) => set({ selectedUnitId: unitId }),

  toggleChecked: (projectName, episode, unitId) =>
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      const current = s.checkedUnitIdsByEpisode[key] ?? [];
      const next = current.includes(unitId) ? current.filter((id) => id !== unitId) : [...current, unitId];
      return { checkedUnitIdsByEpisode: { ...s.checkedUnitIdsByEpisode, [key]: next } };
    }),

  setChecked: (projectName, episode, unitIds) =>
    set((s) => ({
      checkedUnitIdsByEpisode: {
        ...s.checkedUnitIdsByEpisode,
        [referenceVideoCacheKey(projectName, episode)]: Array.from(new Set(unitIds)),
      },
    })),

  saveH3Prompt: async (projectName, episode, unitId, sections) => {
    const { unit, h3_prompt_status } = await API.patchReferenceUnitH3Prompt(projectName, episode, unitId, sections);
    invalidateInFlightLoads(referenceVideoCacheKey(projectName, episode), set);
    set((s) => {
      const key = referenceVideoCacheKey(projectName, episode);
      return {
        unitsByEpisode: {
          ...s.unitsByEpisode,
          [key]: (s.unitsByEpisode[key] ?? []).map((u) => (u.unit_id === unitId ? unit : u)),
        },
        h3PromptStatusByEpisode: {
          ...s.h3PromptStatusByEpisode,
          [key]: { ...s.h3PromptStatusByEpisode[key], [unitId]: h3_prompt_status },
        },
      };
    });
  },

  regenerateH3Prompts: async (projectName, episode, unitIds) => {
    const { unit_ids } = await API.regenerateH3Prompts(projectName, episode, unitIds);
    const key = referenceVideoCacheKey(projectName, episode);
    for (let attempt = 0; attempt < H3_POLL_MAX_ATTEMPTS; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, H3_POLL_INTERVAL_MS));
      await get().loadUnits(projectName, episode);
      const statuses = get().h3PromptStatusByEpisode[key] ?? {};
      if (unit_ids.every((id) => statuses[id] === "current")) return;
    }
  },
}));

/** 勾选集合只保留仍存在的 unit；无变化时返回原对象，避免无谓的订阅刷新。 */
function pruneChecked(
  checked: Record<string, string[]>,
  key: string,
  units: ReferenceVideoUnit[],
): Record<string, string[]> {
  const current = checked[key];
  if (!current || current.length === 0) return checked;
  const alive = new Set(units.map((u) => u.unit_id));
  const next = current.filter((id) => alive.has(id));
  return next.length === current.length ? checked : { ...checked, [key]: next };
}

/**
 * 单元正文的增改会让 H3 提示词过期或缺席：该项目使用 H3 提示词档时（状态表存在）重拉一次列表，
 * 状态以服务端的指纹比对为准，前端不复算。
 */
function refreshH3PromptStatuses(projectName: string, episode: number): void {
  const store = useReferenceVideoStore.getState();
  if (store.h3PromptStatusByEpisode[referenceVideoCacheKey(projectName, episode)]) {
    void store.loadUnits(projectName, episode);
  }
}
