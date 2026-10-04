import { describe, expect, it, beforeEach } from "vitest";
import {
  useAppStore,
  useProjectsStore,
  useTasksStore,
} from "@/stores";
import type { TaskItem } from "@/types";

function resetAllStores(): void {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  useTasksStore.setState(useTasksStore.getInitialState(), true);
}

function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "task-1",
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: "segment-1",
    resource_type: null,
    script_file: null,
    payload: {},
    status: "queued",
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-02-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-02-01T00:00:00Z",
    ...overrides,
  };
}

describe("stores", () => {
  beforeEach(() => {
    resetAllStores();
  });

  it("updates app store state and counters", () => {
    const app = useAppStore.getState();

    app.setFocusedContext({ type: "character", id: "hero" });
    expect(useAppStore.getState().focusedContext).toEqual({
      type: "character",
      id: "hero",
    });

    app.triggerScrollTo({ type: "segment", id: "S1", route: "/episodes/1", highlight: true });
    expect(useAppStore.getState().scrollTarget).toEqual(
      expect.objectContaining({
        type: "segment",
        id: "S1",
        route: "/episodes/1",
        highlight: true,
        highlight_style: "flash",
      }),
    );
    const requestId = useAppStore.getState().scrollTarget?.request_id;
    expect(requestId).toBeTruthy();
    app.clearScrollTarget(requestId);
    expect(useAppStore.getState().scrollTarget).toBeNull();

    // pushToast 只写 toast，不副作用写入 workspaceNotifications
    app.pushToast("hello");
    expect(useAppStore.getState().toast?.text).toBe("hello");
    expect(useAppStore.getState().toast?.tone).toBe("info");
    expect(useAppStore.getState().workspaceNotifications).toHaveLength(0);
    app.clearToast();
    expect(useAppStore.getState().toast).toBeNull();

    // pushNotification 同时写两者，tone 与 target 正确传递
    app.pushNotification("task failed", "error", {
      target: { type: "segment", id: "S1", route: "/episodes/1" },
    });
    expect(useAppStore.getState().toast).toEqual(
      expect.objectContaining({ text: "task failed", tone: "error" }),
    );
    expect(useAppStore.getState().workspaceNotifications[0]).toEqual(
      expect.objectContaining({
        text: "task failed",
        tone: "error",
        target: expect.objectContaining({ id: "S1" }),
      }),
    );
    app.clearToast();
    useAppStore.setState({ workspaceNotifications: [] });

    app.pushWorkspaceNotification({
      text: "AI 刚更新了角色「hero」，点击查看",
      target: {
        type: "character",
        id: "hero",
        route: "/characters",
      },
    });
    expect(useAppStore.getState().toast).toBeNull();
    const notification = useAppStore.getState().workspaceNotifications[0];
    expect(notification.target?.id).toBe("hero");
    app.markWorkspaceNotificationRead(notification.id);
    expect(useAppStore.getState().workspaceNotifications[0].read).toBe(true);
    app.removeWorkspaceNotification(notification.id);
    expect(
      useAppStore.getState().workspaceNotifications.some((item) => item.id === notification.id)
    ).toBe(false);

    app.setUsagePanelOpen(true);
    expect(useAppStore.getState().usagePanelOpen).toBe(true);

    expect(useAppStore.getState().sourceFilesVersion).toBe(0);
    app.invalidateSourceFiles();
    expect(useAppStore.getState().sourceFilesVersion).toBe(1);

    expect(useAppStore.getState().entityRevisions).toEqual({});
    expect(app.getEntityRevision("segment:S1")).toBe(0);
    app.invalidateEntities(["segment:S1", "character:hero", "segment:S1"]);
    expect(app.getEntityRevision("segment:S1")).toBe(1);
    expect(app.getEntityRevision("character:hero")).toBe(1);
    app.invalidateAllEntities();
    expect(app.getEntityRevision("segment:S1")).toBe(2);
    expect(app.getEntityRevision("clue:missing")).toBe(1);
  });

  it("replaces tasks via setTasks and updates task stats", () => {
    const tasks = useTasksStore.getState();
    const first = makeTask();
    const second = makeTask({
      task_id: "task-2",
      status: "running",
      updated_at: "2026-02-01T00:01:00Z",
    });

    tasks.setTasks([first]);
    expect(useTasksStore.getState().tasks).toHaveLength(1);
    expect(useTasksStore.getState().tasks[0].status).toBe("queued");

    tasks.setTasks([second, first]);
    expect(useTasksStore.getState().tasks).toHaveLength(2);
    expect(useTasksStore.getState().tasks[0].task_id).toBe("task-2");

    tasks.setStats({ queued: 1, running: 1, succeeded: 0, failed: 0, cancelled: 0, total: 2 });
    expect(useTasksStore.getState().stats.total).toBe(2);

    tasks.setConnected(true);
    expect(useTasksStore.getState().connected).toBe(true);
  });

  it("updates projects store fields", () => {
    const projects = useProjectsStore.getState();

    projects.setProjects([{ name: "demo", title: "Demo", style: "Anime", thumbnail: null, status: {} }]);
    expect(useProjectsStore.getState().projects).toHaveLength(1);

    projects.setProjectsLoading(true);
    expect(useProjectsStore.getState().projectsLoading).toBe(true);

    projects.setCurrentProject("demo", {
      title: "Demo",
      content_mode: "narration",
      style: "Anime",
      episodes: [],
      characters: {},
      scenes: {},
      props: {},
    });
    expect(useProjectsStore.getState().currentProjectName).toBe("demo");
    expect(useProjectsStore.getState().currentProjectData?.title).toBe("Demo");

    projects.setProjectDetailLoading(true);
    expect(useProjectsStore.getState().projectDetailLoading).toBe(true);

    projects.setShowCreateModal(true);
    expect(useProjectsStore.getState().showCreateModal).toBe(true);

    projects.setCreatingProject(true);
    expect(useProjectsStore.getState().creatingProject).toBe(true);
  });
});
