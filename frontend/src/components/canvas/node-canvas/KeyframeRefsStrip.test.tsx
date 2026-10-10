import { render, screen, waitFor } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { CanvasActionsContext, type CanvasActions } from "./canvas-context";
import { KeyframeRefsStrip } from "./CanvasNodes";

function renderStrip() {
  const actions = { projectName: "demo" } as CanvasActions;
  return render(
    <ReactFlowProvider>
      <CanvasActionsContext.Provider value={actions}>
        <KeyframeRefsStrip episode={1} sheetId="S1" nodeId="n1" />
      </CanvasActionsContext.Provider>
    </ReactFlowProvider>,
  );
}

describe("KeyframeRefsStrip", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows one thumbnail per reference the sheet will send", async () => {
    vi.spyOn(API, "getKeyframeSheetRequest").mockResolvedValue({
      sheet_id: "S1",
      problem: null,
      prompt: "p",
      aspect_ratio: "16:9",
      references: [
        { label: "Image 1: the scene", path: "scenes/a.png" },
        { label: "Image 2: Hán Thăng, 35 tuổi.", path: "canvas/uploads/b.png" },
      ],
    });

    renderStrip();

    await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
    expect(screen.getByTitle("Image 2: Hán Thăng, 35 tuổi.")).toBeInTheDocument();
  });

  it("names the missing material instead of listing references", async () => {
    vi.spyOn(API, "getKeyframeSheetRequest").mockResolvedValue({
      sheet_id: "S1",
      problem: { code: "keyframe_character_sheet_missing", name: "Hán Thăng" },
    });

    renderStrip();

    expect(await screen.findByText(/Hán Thăng/)).toBeInTheDocument();
    expect(screen.queryAllByRole("img")).toHaveLength(0);
  });
});
