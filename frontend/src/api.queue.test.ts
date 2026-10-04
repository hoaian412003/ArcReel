import { afterEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";

function stubFetch(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: "OK",
    headers: new Headers(),
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("API queue monitor", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("builds the task list query from non-empty filters only", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await API.listQueueTasks({ status: "running", provider_id: "", q: "a b", limit: 25, offset: 0 });
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toBe("/api/v1/admin/queue/tasks?status=running&q=a+b&limit=25&offset=0");
  });

  it("encodes the task id in detail and cancel paths and posts without body for cancel", async () => {
    const fetchMock = stubFetch({});
    await API.getQueueTask("t/1 x");
    await API.cancelQueueTask("t/1 x");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/queue/tasks/t%2F1%20x");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/admin/queue/tasks/t%2F1%20x/cancel");
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST" });
  });

  it("sends the reason when marking a task failed", async () => {
    const fetchMock = stubFetch({});
    await API.markQueueTaskFailed("t1", "stuck");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/queue/tasks/t1/mark-failed");
    expect(JSON.parse(init.body as string)).toEqual({ reason: "stuck" });
  });

  it("PUTs limit overrides wrapped in an overrides object, preserving explicit nulls", async () => {
    const fetchMock = stubFetch({});
    await API.updateQueueLimits({ image: 4, video: null });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/queue/limits");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ overrides: { image: 4, video: null } });
  });

  it("requests audit entries with the action filter", async () => {
    const fetchMock = stubFetch({ items: [], total: 0 });
    await API.listQueueAudit({ action: "cancel", limit: 10 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/queue/audit?action=cancel&limit=10");
  });
});
