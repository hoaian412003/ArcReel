import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { API } from "@/api";
import { UnitDialogueCard } from "./UnitDialogueCard";

describe("UnitDialogueCard", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists dialogue and voiceover lines parsed from the saved text", async () => {
    const spy = vi.spyOn(API, "previewReferenceScript").mockResolvedValue({
      utterances: [
        { index: 1, kind: "dialogue", speaker: "Lan", text: "Hello" },
        { index: 2, kind: "voiceover", speaker: null, text: "Night falls" },
      ],
      warnings: [{ key: "w", message: "Lan has no reference audio" }],
    });
    render(<UnitDialogueCard projectName="p" episode={1} text="saved" assets="" />);
    expect(await screen.findByText("Hello")).toBeInTheDocument();
    expect(screen.getByText("Night falls")).toBeInTheDocument();
    expect(screen.getByText("Lan has no reference audio")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith("p", 1, "saved", expect.anything());
  });

  it("shows an empty hint and lets the user jump back to the editor", async () => {
    vi.spyOn(API, "previewReferenceScript").mockResolvedValue({ utterances: [], warnings: [] });
    const onEdit = vi.fn();
    render(<UnitDialogueCard projectName="p" episode={1} text="t" assets="" onEdit={onEdit} />);
    expect(await screen.findByText(/unit_dialogue_card_empty|No dialogue|没有台词/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(onEdit).toHaveBeenCalledOnce();
  });
});
