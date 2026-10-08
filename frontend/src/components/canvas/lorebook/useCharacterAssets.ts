import { useCallback, useMemo } from "react";
import { API } from "@/api";
import { enqueueCharacter, enqueueCharacterDerivative, enqueueProduct, enqueueProp, enqueueScene } from "@/actions/generation";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import { buildEntityRevisionKey } from "@/utils/project-changes";

export interface CharacterSavePayload {
  description: string;
  voiceStyle: string;
  referenceFile?: File | null;
  audioFile?: File | null;
}

/** 与角色同构、在画布上也按「描述 → 图片供应商 → 资产图」分组的其余资产类型。 */
export type OtherAssetKind = "scene" | "prop" | "product";

export interface CharacterAssets {
  generating: Set<string>;
  save: (name: string, payload: CharacterSavePayload) => Promise<void>;
  /** 入队失败已转 toast，不会抛出。 */
  generate: (name: string) => void;
  /** 失败时 toast 后继续抛出，供新增对话框据此保持打开。 */
  add: (name: string, description: string, voiceStyle: string, referenceFile?: File | null) => Promise<void>;
  /** 只改描述（画布上描述文本节点的编辑）；失败 toast，不抛出。 */
  saveDescription: (name: string, description: string) => Promise<void>;
  /** 上传 / 替换参考图（画布上参考图节点）；失败 toast，不抛出。 */
  uploadReference: (name: string, file: File) => Promise<void>;
  /** 场景 / 道具 / 商品：正在生成资产图的名字集合。 */
  generatingAssets: Record<OtherAssetKind, Set<string>>;
  saveAssetDescription: (kind: OtherAssetKind, name: string, description: string) => Promise<void>;
  generateAsset: (kind: OtherAssetKind, name: string) => void;
  /** 正在生成中的衍生，键为 `角色/衍生`。 */
  generatingDerivatives: Set<string>;
  generateDerivative: (name: string, derivative: string) => void;
  saveDerivativeDescription: (name: string, derivative: string, description: string) => Promise<void>;
  /** 失败时 toast 后抛出，供新增表单据此保持打开。 */
  addDerivative: (name: string, derivative: string, description: string) => Promise<void>;
  deleteDerivative: (name: string, derivative: string) => Promise<void>;
  /** 版本恢复 / 外部变更后重新拉取项目。 */
  refresh: () => Promise<void>;
}

/**
 * 角色资产的写操作与占用态：新增、保存（描述 / 声音 / 参考图 / 参考音频）、生成资产图。
 * 画布上的角色节点与演示项目的角色页共用同一套。保存是多步写入，任一步失败都要刷新项目，
 * 让已持久化的部分反映到界面。
 */
export function useCharacterAssets(projectName: string | null): CharacterAssets {
  const generating = useActiveResourceIds("character", projectName);
  const generatingDerivatives = useActiveResourceIds("character_derivative", projectName);
  const generatingScenes = useActiveResourceIds("scene", projectName);
  const generatingProps = useActiveResourceIds("prop", projectName);
  const generatingProducts = useActiveResourceIds("product", projectName);
  const generatingAssets = useMemo(
    () => ({ scene: generatingScenes, prop: generatingProps, product: generatingProducts }),
    [generatingScenes, generatingProps, generatingProducts],
  );

  const refreshProject = useCallback(
    async (invalidateKeys: string[] = []): Promise<void> => {
      if (!projectName) return;
      await useProjectsStore.getState().refreshProject(projectName, { invalidateKeys });
    },
    [projectName],
  );

  const save = useCallback(
    async (name: string, payload: CharacterSavePayload) => {
      if (!projectName) return;
      const invalidateKeys =
        payload.referenceFile || payload.audioFile ? [buildEntityRevisionKey("character", name)] : [];
      try {
        await API.updateCharacter(projectName, name, {
          description: payload.description,
          voice_style: payload.voiceStyle,
        });
        if (payload.referenceFile) {
          await API.uploadFile(projectName, "character_ref", payload.referenceFile, name);
        }
        if (payload.audioFile) {
          await API.uploadFile(projectName, "character_audio_ref", payload.audioFile, name);
        }
        useAppStore.getState().pushToast(i18n.t("dashboard:character_updated_toast", { name }), "success");
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(i18n.t("dashboard:update_character_failed", { message: errMsg(err) }), "error");
      } finally {
        // 三步写操作顺序执行，任一步失败时前面已持久化的变更（描述 / 参考图）也要反映到本地
        // store，否则用户会误以为整个保存失败而重复提交。
        await refreshProject(invalidateKeys);
      }
    },
    [projectName, refreshProject],
  );

  const generate = useCallback(
    (name: string) => {
      if (!projectName) return;
      enqueueCharacter(projectName, name).catch((err: unknown) => {
        useAppStore.getState().pushToast(i18n.t("dashboard:submit_failed", { message: errMsg(err) }), "error");
      });
    },
    [projectName],
  );

  const add = useCallback(
    async (name: string, description: string, voiceStyle: string, referenceFile?: File | null) => {
      if (!projectName) return;
      try {
        await API.addCharacter(projectName, name, description, voiceStyle);
        if (referenceFile) {
          await API.uploadFile(projectName, "character_ref", referenceFile, name);
        }
        await refreshProject(referenceFile ? [buildEntityRevisionKey("character", name)] : []);
        useAppStore.getState().pushToast(i18n.t("dashboard:character_added_toast", { name }), "success");
      } catch (err) {
        useAppStore.getState().pushToast(i18n.t("dashboard:add_failed", { message: errMsg(err) }), "error");
        throw err;
      }
    },
    [projectName, refreshProject],
  );

  const saveDescription = useCallback(
    async (name: string, description: string) => {
      if (!projectName) return;
      try {
        await API.updateCharacter(projectName, name, { description });
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(i18n.t("dashboard:update_character_failed", { message: errMsg(err) }), "error");
      } finally {
        await refreshProject();
      }
    },
    [projectName, refreshProject],
  );

  const uploadReference = useCallback(
    async (name: string, file: File) => {
      if (!projectName) return;
      try {
        await API.uploadFile(projectName, "character_ref", file, name);
        useAppStore.getState().pushToast(i18n.t("dashboard:character_updated_toast", { name }), "success");
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(i18n.t("dashboard:update_character_failed", { message: errMsg(err) }), "error");
      } finally {
        await refreshProject([buildEntityRevisionKey("character", name)]);
      }
    },
    [projectName, refreshProject],
  );

  const generateDerivative = useCallback(
    (name: string, derivative: string) => {
      if (!projectName) return;
      enqueueCharacterDerivative(projectName, name, derivative).catch((err: unknown) => {
        useAppStore.getState().pushToast(i18n.t("dashboard:submit_failed", { message: errMsg(err) }), "error");
      });
    },
    [projectName],
  );

  const saveDerivativeDescription = useCallback(
    async (name: string, derivative: string, description: string) => {
      if (!projectName) return;
      try {
        await API.updateCharacterDerivative(projectName, name, derivative, description);
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(i18n.t("dashboard:update_character_failed", { message: errMsg(err) }), "error");
      } finally {
        await refreshProject();
      }
    },
    [projectName, refreshProject],
  );

  const addDerivative = useCallback(
    async (name: string, derivative: string, description: string) => {
      if (!projectName) return;
      try {
        await API.addCharacterDerivative(projectName, name, derivative, description);
        await refreshProject();
      } catch (err) {
        useAppStore.getState().pushToast(i18n.t("dashboard:add_failed", { message: errMsg(err) }), "error");
        throw err;
      }
    },
    [projectName, refreshProject],
  );

  const deleteDerivative = useCallback(
    async (name: string, derivative: string) => {
      if (!projectName) return;
      try {
        await API.deleteCharacterDerivative(projectName, name, derivative);
      } catch (err) {
        useAppStore
          .getState()
          .pushToast(i18n.t("dashboard:update_character_failed", { message: errMsg(err) }), "error");
      } finally {
        await refreshProject();
      }
    },
    [projectName, refreshProject],
  );

  const saveAssetDescription = useCallback(
    async (kind: OtherAssetKind, name: string, description: string) => {
      if (!projectName) return;
      try {
        const updates = { description };
        if (kind === "scene") await API.updateProjectScene(projectName, name, updates);
        else if (kind === "prop") await API.updateProjectProp(projectName, name, updates);
        else await API.updateProjectProduct(projectName, name, updates);
      } catch (err) {
        useAppStore.getState().pushToast(i18n.t("dashboard:submit_failed", { message: errMsg(err) }), "error");
      } finally {
        await refreshProject();
      }
    },
    [projectName, refreshProject],
  );

  const generateAsset = useCallback(
    (kind: OtherAssetKind, name: string) => {
      if (!projectName) return;
      const enqueue = { scene: enqueueScene, prop: enqueueProp, product: enqueueProduct }[kind];
      enqueue(projectName, name).catch((err: unknown) => {
        useAppStore.getState().pushToast(i18n.t("dashboard:submit_failed", { message: errMsg(err) }), "error");
      });
    },
    [projectName],
  );

  const refresh = useCallback(() => refreshProject(), [refreshProject]);

  return useMemo(
    () => ({
      generating,
      save,
      generate,
      add,
      saveDescription,
      uploadReference,
      generatingAssets,
      saveAssetDescription,
      generateAsset,
      generatingDerivatives,
      generateDerivative,
      saveDerivativeDescription,
      addDerivative,
      deleteDerivative,
      refresh,
    }),
    [
      generating,
      save,
      generate,
      add,
      saveDescription,
      uploadReference,
      generatingAssets,
      saveAssetDescription,
      generateAsset,
      generatingDerivatives,
      generateDerivative,
      saveDerivativeDescription,
      addDerivative,
      deleteDerivative,
      refresh,
    ],
  );
}
