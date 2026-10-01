import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { H3AuthoringGlobalEditor, H3AuthoringProjectEditor } from "./H3TemplateEditor";

const BUILTIN = "# 内置模版\n{{ style }}";

function mockOverride(override: string | null) {
  return vi.spyOn(API, "getH3AuthoringOverride").mockResolvedValue({ builtin: BUILTIN, override, max_chars: 100 });
}

describe("H3AuthoringGlobalEditor", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the built-in template and saves an edit as the global override", async () => {
    mockOverride(null);
    const save = vi.spyOn(API, "saveH3AuthoringOverride").mockResolvedValue({ prompt: "ok" });
    render(<H3AuthoringGlobalEditor />);
    const box = (await screen.findByRole("textbox")) as HTMLTextAreaElement;
    expect(box.value).toBe(BUILTIN);

    fireEvent.change(box, { target: { value: "# 我的模版\n{{ style }}" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(save).toHaveBeenCalledWith("# 我的模版\n{{ style }}"));
  });

  it("shows the server's validation message when saving fails", async () => {
    mockOverride(null);
    vi.spyOn(API, "saveH3AuthoringOverride").mockRejectedValue(new Error("缺少锁定片段"));
    render(<H3AuthoringGlobalEditor />);
    const box = await screen.findByRole("textbox");
    fireEvent.change(box, { target: { value: "bad" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText("缺少锁定片段")).toBeInTheDocument();
  });

  it("restores the default by deleting the stored override", async () => {
    mockOverride("# 旧的自定义");
    const del = vi.spyOn(API, "deleteH3AuthoringOverride").mockResolvedValue();
    render(<H3AuthoringGlobalEditor />);
    expect(((await screen.findByRole("textbox")) as HTMLTextAreaElement).value).toBe("# 旧的自定义");
    fireEvent.click(screen.getByRole("button", { name: "恢复默认" }));
    await waitFor(() => expect(del).toHaveBeenCalled());
    await waitFor(() => expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(BUILTIN));
  });

  it("previews with sample data and drops the preview once the text changes", async () => {
    mockOverride(null);
    vi.spyOn(API, "previewH3AuthoringOverride").mockResolvedValue({ prompt: "渲染结果" });
    render(<H3AuthoringGlobalEditor />);
    const box = await screen.findByRole("textbox");
    fireEvent.click(screen.getByRole("button", { name: "用示例数据预览" }));
    expect(await screen.findByText("渲染结果")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "# changed" } });
    expect(screen.queryByText("渲染结果")).not.toBeInTheDocument();
  });
});

describe("H3AuthoringProjectEditor", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the inherited global override and only stores text that differs from it", async () => {
    mockOverride("# 全局版本");
    const onChange = vi.fn();
    render(<H3AuthoringProjectEditor value="" onChange={onChange} />);
    const box = (await screen.findByRole("textbox")) as HTMLTextAreaElement;
    expect(box.value).toBe("# 全局版本");

    fireEvent.change(box, { target: { value: "# 项目版本" } });
    expect(onChange).toHaveBeenLastCalledWith("# 项目版本");
    fireEvent.change(box, { target: { value: "# 全局版本\n" } });
    expect(onChange).toHaveBeenLastCalledWith("");
  });
});
