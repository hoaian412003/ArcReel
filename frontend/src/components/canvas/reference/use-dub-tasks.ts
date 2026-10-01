import { useMemo } from "react";
import { isActiveStatus, useTasksStore } from "@/stores/tasks-store";

/** 配音相关的任务类型：撰写越南语台词（文本任务）与合成配音音频。 */
const DUB_TASK_TYPES = new Set(["dub_tts", "text_reference_dub_lines"]);

export interface DubTaskState {
  /** 本项目有配音任务排队或运行中。 */
  active: boolean;
  /** 任务集合与状态的指纹；变化即说明有任务落定，可据此重新拉取台词与时间线。 */
  version: string;
}

export function useDubTasks(projectName: string): DubTaskState {
  const tasks = useTasksStore((s) => s.tasks);
  return useMemo(() => {
    const dubTasks = tasks.filter((task) => task.project_name === projectName && DUB_TASK_TYPES.has(task.task_type));
    return {
      active: dubTasks.some((task) => isActiveStatus(task.status)),
      version: dubTasks.map((task) => `${task.task_id}:${task.status}`).join("|"),
    };
  }, [tasks, projectName]);
}
