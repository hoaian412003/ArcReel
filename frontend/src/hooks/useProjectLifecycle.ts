import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { setApiReadOnly } from "@/api";
import { buildDemoProjectData, buildDemoScripts, isDemoProject } from "@/onboarding/demo-project";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";

/**
 * 项目生命周期：按项目名把项目数据灌进 projects-store，离开时清空。工作台与独立的画布页共用。
 * 真实项目走 `refreshProject`（首屏加载与其余刷新共用取消域）；引导演示项目在磁盘上并不存在，数据直接来自
 * 前端常量，并在这段生命周期内锁死写请求。
 */
export function useProjectLifecycle(projectName: string | null): void {
  const { setCurrentProject, setProjectDetailLoading } = useProjectsStore();
  const { t } = useTranslation("onboarding");
  // 把 t 通过 ref 暴露给首屏加载的 onError 回调，避免切语言触发 t 重建 → effect
  // 依赖跟着重建 → 真实项目整条加载重跑（下方 effect 依赖里刻意不含 t，理由见下）。
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  // 项目生命周期：按项目类型取数据。
  // 依赖里不含 `t` —— 界面语言变化只该重灌演示常量（见下一个 effect），不该让真实项目
  // 整条加载重跑（会先清空 store 闪一次空态）。
  useEffect(() => {
    if (!projectName) return;

    // 引导演示项目在磁盘上并不存在：数据直接来自前端常量，不发请求，并在这段生命周期内
    // 锁死写请求。数据本身由下一个 effect 灌入。
    if (isDemoProject(projectName)) {
      setApiReadOnly(true);
      setProjectDetailLoading(false);
      return () => {
        setApiReadOnly(false);
        setCurrentProject(null, null);
      };
    }

    // 进真实项目一律解锁，不让上一次演示的闸门残留下来
    setApiReadOnly(false);
    setProjectDetailLoading(true);
    // 先落地项目名（数据置空）：refreshProject 的写入门槛要求 currentProjectName 已等于
    // 目标项目，否则响应落地时会被判成「非当前项目」而丢弃（见 projects-store.ts
    // isCurrentProject 的注释）。这一步同时轮换取消域，上一个项目的在途请求随之作废，
    // 首屏加载与 refreshProject 的其余调用方共用同一取消域。
    setCurrentProject(projectName, null);
    void useProjectsStore
      .getState()
      .refreshProject(projectName, {
        onError: (err) =>
          useAppStore.getState().pushToast(tRef.current("dashboard:project_load_failed", { message: errMsg(err) }), "error"),
      })
      .then((result) => {
        // "cancelled" 代表本轮未同步（项目已被切走、取消域已轮换），loading 状态交由
        // 接管的新一轮自行结算，此处不动共享状态。
        if (result === "cancelled") return;
        setProjectDetailLoading(false);
      });

    return () => {
      setCurrentProject(null, null);
    };
  }, [projectName, setCurrentProject, setProjectDetailLoading]);

  // 演示数据随界面语言走：`t` 换身份时重灌一遍常量，只影响演示项目
  useEffect(() => {
    if (!projectName || !isDemoProject(projectName)) return;
    setCurrentProject(projectName, buildDemoProjectData(t), buildDemoScripts(t));
  }, [projectName, setCurrentProject, t]);
}
