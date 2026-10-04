import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MigrationRepairBanner } from "./MigrationRepairBanner";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData, ProjectStatus } from "@/types/project";

const HEALTHY: ProjectStatus = {
  phase: "production",
  phase_progress: 0.5,
  needs_repair: false,
  repair_reason: null,
  assets: {
    character: { total: 1, available: 1, stale: 0 },
    scene: { total: 1, available: 1, stale: 0 },
    prop: { total: 0, available: 0, stale: 0 },
  },
  episodes_summary: { total: 1, scripted: 1, in_production: 1, completed: 0 },
};

function setProjectStatus(status: ProjectStatus) {
  useProjectsStore.setState({
    currentProjectData: { name: "demo", status } as unknown as ProjectData,
  });
}

describe("MigrationRepairBanner", () => {
  beforeEach(() => {
    useProjectsStore.setState({ currentProjectData: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("stays out of the way while the project is healthy", () => {
    setProjectStatus(HEALTHY);
    render(<MigrationRepairBanner />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the raw failure reason when the project needs repair", () => {
    setProjectStatus({
      ...HEALTHY,
      needs_repair: true,
      repair_reason: "episode script scripts/episode_1.json item 2 has no identity",
    });
    render(<MigrationRepairBanner />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(
      screen.getByText("episode script scripts/episode_1.json item 2 has no identity"),
    ).toBeInTheDocument();
  });
});
