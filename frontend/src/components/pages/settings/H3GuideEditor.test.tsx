import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { H3GuideEditor } from "./H3GuideEditor";

const BUILT_IN = "  - 细节要具体\n  - 篇幅随 unit 时长走";

function mockBuiltIn() {
  return vi.spyOn(API, "getPromptPartial").mockResolvedValue({
    name: "shared/h3_guide/standard",
    source: BUILT_IN,
    protected: false,
    referenced_by: [],
  });
}

describe("H3GuideEditor", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the built-in guide for the preset when there is no custom text", async () => {
    const spy = mockBuiltIn();
    render(<H3GuideEditor id="guide" preset="standard" value="" onChange={vi.fn()} />);
    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe(BUILT_IN.trim()));
    expect(spy).toHaveBeenCalledWith("shared/h3_guide/standard", expect.anything());
  });

  it("turns an edit into custom text and ignores edits that equal the effective text", async () => {
    mockBuiltIn();
    const onChange = vi.fn();
    render(<H3GuideEditor id="guide" preset="standard" value="" onChange={onChange} />);
    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe(BUILT_IN.trim()));

    fireEvent.change(box, { target: { value: "  - my own rule" } });
    expect(onChange).toHaveBeenLastCalledWith("  - my own rule");

    fireEvent.change(box, { target: { value: `${BUILT_IN.trim()}\n` } });
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("shows the inherited text and clears the custom text on restore", async () => {
    mockBuiltIn();
    const onChange = vi.fn();
    const { rerender } = render(
      <H3GuideEditor id="guide" preset="standard" value="" onChange={onChange} inheritedText="  - global rule" />,
    );
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("- global rule");

    rerender(<H3GuideEditor id="guide" preset="standard" value="  - project rule" onChange={onChange} inheritedText="  - global rule" />);
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("  - project rule");
    fireEvent.click(screen.getByRole("button"));
    expect(onChange).toHaveBeenLastCalledWith("");
  });

  it("flags text longer than the limit", async () => {
    mockBuiltIn();
    render(<H3GuideEditor id="guide" preset="standard" value={"x".repeat(4001)} onChange={vi.fn()} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
