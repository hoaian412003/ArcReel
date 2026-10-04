import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useReferenceVideoStore } from "@/stores/reference-video-store";
import type { ReferenceVideoUnit } from "@/types";
import { UnitDirectionSection } from "./UnitDirectionSection";

function mkUnit(overrides: Partial<ReferenceVideoUnit> = {}): ReferenceVideoUnit {
  return {
    unit_id: "E1U02",
    text: "@[张三] 推门",
    duration_seconds: 10,
    transition_to_next: "cut",
    note: null,
    ...overrides,
  };
}

describe("UnitDirectionSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useReferenceVideoStore.setState({ unitsByEpisode: {}, unitCapabilitiesByEpisode: {} });
  });

  it("shows the empty hint and keeps Save disabled until something is written", () => {
    render(<UnitDirectionSection projectName="p" episode={1} unit={mkUnit()} />);
    expect(screen.getByText(/No director.s notes yet|还没有导演说明|Chưa có ghi chú/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save notes|保存说明|Lưu ghi chú/ })).toBeDisabled();
  });

  it("saves only the director fields, dropping blank rows", async () => {
    const patch = vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({
      unit: mkUnit({ brief: "他想回家，危险逼近。", must_not_show: "撞击本身" }),
      unit_capability: {} as never,
    });
    render(<UnitDirectionSection projectName="p" episode={1} unit={mkUnit()} />);

    fireEvent.change(screen.getByPlaceholderText(/What happens|发生什么|Chuyện gì/), {
      target: { value: "他想回家，危险逼近。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add beat|添加节点|Thêm nhịp/ }));
    fireEvent.click(screen.getByRole("button", { name: /Add performance note|添加表演说明|Thêm ghi chú diễn xuất/ }));
    const [mustNotShow] = screen.getAllByRole("textbox").filter((el) => (el as HTMLInputElement).type === "text").slice(-4);
    fireEvent.change(mustNotShow, { target: { value: "撞击本身" } });
    fireEvent.click(screen.getByRole("button", { name: /Save notes|保存说明|Lưu ghi chú/ }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    const [, , unitId, body] = patch.mock.calls[0];
    expect(unitId).toBe("E1U02");
    expect(body).toMatchObject({ brief: "他想回家，危险逼近。", must_not_show: "撞击本身", beats: [], performance: [] });
    expect(body).not.toHaveProperty("prompt");
  });

  it("is read-only while the unit is locked", () => {
    render(<UnitDirectionSection projectName="p" episode={1} unit={mkUnit({ brief: "x" })} locked />);
    expect(screen.getByPlaceholderText(/What happens|发生什么|Chuyện gì/)).toBeDisabled();
  });
});
