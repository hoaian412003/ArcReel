import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { API } from "@/api";
import { VieneuServicePanel } from "./VieneuServicePanel";
import type { VieneuSettings, VieneuStatus, VieneuVoices } from "@/types";

const SETTINGS: VieneuSettings = {
  enabled: true,
  command: "",
  workdir: "/repo/third_party/VieNeu-TTS",
  host: "127.0.0.1",
  port: 8000,
  device: "auto",
  int8: false,
  startup_timeout_seconds: 180,
  idle_minutes: 15,
};

const STOPPED: VieneuStatus = {
  state: "stopped",
  pid: null,
  last_error: null,
  base_url: null,
  managed: false,
  logs: [],
};

const READY: VieneuStatus = {
  state: "ready",
  pid: 42,
  last_error: null,
  base_url: "http://127.0.0.1:8000/v1",
  managed: true,
  logs: ["server up"],
};

const VOICES: VieneuVoices = {
  source: "catalog",
  voices: [
    { id: "Mai Anh", label: "Mai Anh", gender: "female", locale: "vi", region: "north" },
    { id: "Hải Đăng", label: "Hải Đăng", gender: "male", locale: "vi", region: "north" },
    { id: "Adam", label: "Adam", gender: "male", locale: "vi", region: "south" },
  ],
};

describe("VieneuServicePanel", () => {
  beforeEach(() => {
    vi.spyOn(API, "getVieneuSettings").mockResolvedValue(SETTINGS);
    vi.spyOn(API, "getVieneuVoices").mockResolvedValue(VOICES);
  });

  afterEach(() => vi.restoreAllMocks());

  it("shows the state, the project-local folder and the male/female voice counts", async () => {
    vi.spyOn(API, "getVieneuStatus").mockResolvedValue(STOPPED);
    render(<VieneuServicePanel />);

    expect(await screen.findByDisplayValue("/repo/third_party/VieNeu-TTS")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Stopped|未运行/);
    expect(await screen.findByText(/2 male and 1 female|2 个男声、1 个女声/)).toBeInTheDocument();
  });

  it("starts the service and reflects the ready state", async () => {
    vi.spyOn(API, "getVieneuStatus").mockResolvedValue(STOPPED);
    const start = vi.spyOn(API, "startVieneu").mockResolvedValue(READY);
    render(<VieneuServicePanel />);

    fireEvent.click(await screen.findByRole("button", { name: /^(Start|启动)$/ }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Managed by ArcReel|由 ArcReel 托管/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Running|运行中/);
    expect(screen.getByRole("button", { name: /^(Stop|停止)$/ })).toBeEnabled();
  });

  it("stops a managed service", async () => {
    vi.spyOn(API, "getVieneuStatus").mockResolvedValue(READY);
    const stop = vi.spyOn(API, "stopVieneu").mockResolvedValue(STOPPED);
    render(<VieneuServicePanel />);

    fireEvent.click(await screen.findByRole("button", { name: /^(Stop|停止)$/ }));

    await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: /^(Start|启动)$/ })).toBeInTheDocument();
  });

  it("does not let ArcReel stop a service it did not start", async () => {
    vi.spyOn(API, "getVieneuStatus").mockResolvedValue({ ...READY, managed: false });
    render(<VieneuServicePanel />);

    expect(await screen.findByRole("button", { name: /^(Stop|停止)$/ })).toBeDisabled();
    expect(screen.getByText(/External service|外部服务/)).toBeInTheDocument();
  });

  it("saves only the changed settings", async () => {
    vi.spyOn(API, "getVieneuStatus").mockResolvedValue(STOPPED);
    const patch = vi.spyOn(API, "patchVieneuSettings").mockResolvedValue({ ...SETTINGS, port: 8123, int8: true });
    render(<VieneuServicePanel />);

    const save = await screen.findByRole("button", { name: /Save settings|保存设置/ });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^(Port|端口)$/), { target: { value: "8123" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /int8/ }));
    fireEvent.click(save);

    await waitFor(() => expect(patch).toHaveBeenCalledWith({ port: 8123, int8: true }));
  });

  it("explains how to install when the service failed to start", async () => {
    vi.spyOn(API, "getVieneuStatus").mockResolvedValue({
      ...STOPPED,
      state: "error",
      last_error: "No such file or directory",
    });
    render(<VieneuServicePanel />);

    expect(await screen.findByText("No such file or directory")).toBeInTheDocument();
    expect(screen.getByText(/git clone https:\/\/github.com\/pnnbao97\/VieNeu-TTS/)).toBeInTheDocument();
  });

  it("shows the logs on demand", async () => {
    vi.spyOn(API, "getVieneuStatus").mockResolvedValue(READY);
    render(<VieneuServicePanel />);

    expect(screen.queryByText("server up")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Show logs|查看日志/ }));
    expect(screen.getByText("server up")).toBeInTheDocument();
  });
});
