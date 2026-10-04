import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClaudeAPI, type ClaudeAuthStatus } from "@/api/claude";
import { ClaudeAuthSection } from "./ClaudeAuthSection";

const loggedOut: ClaudeAuthStatus = {
  logged_in: false,
  login_pending: false,
  auth_method: null,
  api_provider: null,
  email: null,
  organization: null,
  subscription_type: null,
};

describe("ClaudeAuthSection", () => {
  beforeEach(() => {
    vi.spyOn(ClaudeAPI, "authStatus").mockResolvedValue(loggedOut);
  });

  it("opens Claude OAuth and accepts the returned code", async () => {
    const open = vi.spyOn(globalThis, "open").mockImplementation(() => null);
    vi.spyOn(ClaudeAPI, "startLogin").mockResolvedValue({
      login_id: "login-1",
      authorization_url: "https://claude.com/cai/oauth/authorize?state=test",
    });
    vi.spyOn(ClaudeAPI, "completeLogin").mockResolvedValue({
      ...loggedOut,
      logged_in: true,
      auth_method: "claude.ai",
      api_provider: "firstParty",
      email: "person@example.com",
      organization: "Example",
      subscription_type: "pro",
    });

    render(<ClaudeAuthSection />);
    fireEvent.click(await screen.findByRole("button", { name: "使用 Claude 登录" }));

    await waitFor(() => {
      expect(open).toHaveBeenCalledWith(
        "https://claude.com/cai/oauth/authorize?state=test",
        "claude-login",
        expect.stringContaining("popup"),
      );
    });
    fireEvent.change(screen.getByLabelText("Claude 授权码"), { target: { value: "one-time-code" } });
    fireEvent.click(screen.getByRole("button", { name: "确认登录" }));

    expect(await screen.findByText("已连接 Claude")).toBeInTheDocument();
    expect(ClaudeAPI.completeLogin).toHaveBeenCalledWith("login-1", "one-time-code");
  });
});
