import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { beforeEach, describe, expect, it } from "vitest";
import { useAppStore } from "@/stores/app-store";
import type { ReferenceUnitCapability } from "@/types";
import { ReferenceSplitAlert } from "./ReferenceSplitAlert";

function capability(overrides: Partial<ReferenceUnitCapability> = {}): ReferenceUnitCapability {
  return {
    unit_id: "E1U1",
    declared_capability: "r2v",
    hydrated_capability: "r2v",
    declared_references: [],
    unavailable_references: [],
    unregistered_references: [],
    allowed_durations: null,
    excluded_durations: null,
    ...overrides,
  } as ReferenceUnitCapability;
}

describe("ReferenceSplitAlert", () => {
  beforeEach(() => {
    useAppStore.setState({ scrollTarget: null });
  });

  it("links a derivative reference without an image to its owner's asset card", async () => {
    const { hook } = memoryLocation({ path: "/episodes/1", record: true });
    render(
      <Router hook={hook}>
        <ReferenceSplitAlert
          className=""
          capability={capability({
            unavailable_references: [
              { type: "character", name: "Trần Hán Thăng/35 tuổi trước khi trùng sinh" },
              { type: "scene", name: "Đại điện" },
            ],
          })}
        />
      </Router>,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Trần Hán Thăng/35 tuổi trước khi trùng sinh" }),
    );
    expect(useAppStore.getState().scrollTarget).toMatchObject({
      type: "character",
      id: "Trần Hán Thăng",
      route: "/characters",
    });

    await userEvent.click(screen.getByRole("button", { name: "Đại điện" }));
    expect(useAppStore.getState().scrollTarget).toMatchObject({
      type: "scene",
      id: "Đại điện",
      route: "/scenes",
    });
  });
});
