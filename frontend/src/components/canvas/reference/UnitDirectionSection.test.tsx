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
    fireEvent.click(screen.getByRole("button", { name: /Add shot|添加镜头|Thêm shot/ }));
    fireEvent.click(screen.getByRole("button", { name: /Add performance note|添加表演说明|Thêm ghi chú diễn xuất/ }));
    fireEvent.click(screen.getByRole("button", { name: /Add line|添加台词|Thêm thoại/ }));
    fireEvent.change(screen.getByLabelText(/Must NOT show|必须按住|KHÔNG được hiện/), { target: { value: "撞击本身" } });
    fireEvent.click(screen.getByRole("button", { name: /Save notes|保存说明|Lưu ghi chú/ }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    const [, , unitId, body] = patch.mock.calls[0];
    expect(unitId).toBe("E1U02");
    expect(body).toMatchObject({ brief: "他想回家，危险逼近。", must_not_show: "撞击本身", shots: [], lines: [], performance: [] });
    expect(body).not.toHaveProperty("prompt");
  });

  it("saves shots, voice and line placement as edited", async () => {
    const patch = vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({
      unit: mkUnit(),
      unit_capability: {} as never,
    });
    render(<UnitDirectionSection projectName="p" episode={1} unit={mkUnit()} />);

    fireEvent.click(screen.getByRole("button", { name: /Add shot|添加镜头|Thêm shot/ }));
    fireEvent.change(screen.getByLabelText(/^(Shot 1 camera move|镜头 1 运镜|Shot 1 chuyển động máy)$/), {
      target: { value: "push_in" },
    });
    fireEvent.change(screen.getByLabelText(/^(Shot 1 action|镜头 1 动作|Shot 1 hành động)$/), {
      target: { value: "张三推门而入" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add performance note|添加表演说明|Thêm ghi chú diễn xuất/ }));
    fireEvent.change(screen.getByLabelText(/^(Performance note 1|表演说明 1|Ghi chú diễn xuất 1)$/), {
      target: { value: "气喘吁吁" },
    });
    fireEvent.change(screen.getByLabelText(/^(Voice for performance note 1|表演说明 1 的声音|Giọng của mô tả diễn xuất 1)$/), {
      target: { value: "低沉沙哑" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add line|添加台词|Thêm thoại/ }));
    fireEvent.change(screen.getByLabelText(/^(Line 1 \(verbatim|台词 1（|Thoại 1 \()/), { target: { value: "我回来了" } });
    fireEvent.click(screen.getByRole("button", { name: /Save notes|保存说明|Lưu ghi chú/ }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    const body = patch.mock.calls[0][3];
    expect(body.shots).toEqual([expect.objectContaining({ at_seconds: 0, action: "张三推门而入", camera: expect.objectContaining({ type: "push_in" }) })]);
    expect(body.performance).toEqual([expect.objectContaining({ note: "气喘吁吁", voice: "低沉沙哑" })]);
    expect(body.lines).toEqual([expect.objectContaining({ text: "我回来了" })]);
  });

  it("keeps the beats editor for units that only have legacy beats", () => {
    render(
      <UnitDirectionSection
        projectName="p"
        episode={1}
        unit={mkUnit({ beats: [{ at_pct: 0, action: "推门" }] })}
      />,
    );
    expect(screen.getByRole("button", { name: /Add beat|添加节点|Thêm nhịp/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Add shot|添加镜头|Thêm shot/ })).not.toBeInTheDocument();
  });

  it("is read-only while the unit is locked", () => {
    render(<UnitDirectionSection projectName="p" episode={1} unit={mkUnit({ brief: "x" })} locked />);
    expect(screen.getByPlaceholderText(/What happens|发生什么|Chuyện gì/)).toBeDisabled();
  });

  it("edits a free-text direction as one block and saves only that field", async () => {
    const patch = vi.spyOn(API, "patchReferenceVideoUnit").mockResolvedValue({
      unit: mkUnit({ direction: "改过的导演说明" }),
      unit_capability: {} as never,
    });
    render(<UnitDirectionSection projectName="p" episode={1} unit={mkUnit({ direction: "第 0 秒全景，推门。" })} />);

    const box = screen.getByRole("textbox");
    expect(box).toHaveValue("第 0 秒全景，推门。");
    expect(screen.queryByRole("button", { name: /Add shot|添加镜头|Thêm shot/ })).not.toBeInTheDocument();
    fireEvent.change(box, { target: { value: "改过的导演说明" } });
    fireEvent.click(screen.getByRole("button", { name: /Save notes|保存说明|Lưu ghi chú/ }));

    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][3]).toEqual({ direction: "改过的导演说明" });
  });
});
