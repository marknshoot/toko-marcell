import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { adminRefresh, getAdminSession } from "../api";

function okRefresh(token = "jwt-token") {
  return {
    ok: true,
    status: 200,
    json: async () => ({
      access_token: token,
      token_type: "bearer",
      user: { username: "admin", role: "admin" },
    }),
  };
}

describe("adminRefresh", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls the storefront origin so the refresh cookie is first-party", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okRefresh());
    vi.stubGlobal("fetch", fetchMock);

    await adminRefresh();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/refresh");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
  });

  it("persists the new access token for the tab", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okRefresh("rotated-jwt")));

    await adminRefresh();

    expect(getAdminSession()?.access_token).toBe("rotated-jwt");
    expect(sessionStorage.getItem("toko_admin_access_token")).toBe(
      "rotated-jwt"
    );
  });

  it("sends one request for concurrent callers (rotation is single-use)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okRefresh());
    vi.stubGlobal("fetch", fetchMock);

    const [first, second] = await Promise.all([adminRefresh(), adminRefresh()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(first.access_token).toBe("jwt-token");
    expect(second.access_token).toBe("jwt-token");
  });

  it("does not keep a rejected refresh in flight", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Refresh token required" }),
      })
      .mockResolvedValueOnce(okRefresh());
    vi.stubGlobal("fetch", fetchMock);

    await expect(adminRefresh()).rejects.toMatchObject({ status: 401 });
    await expect(adminRefresh()).resolves.toMatchObject({
      access_token: "jwt-token",
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("serialises refreshes across tabs with the Web Locks API", async () => {
    const order = [];
    const fetchMock = vi.fn(async () => {
      order.push("fetch");
      return okRefresh();
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("navigator", {
      ...globalThis.navigator,
      locks: {
        request: async (name, callback) => {
          order.push(`lock:${name}`);
          return callback();
        },
      },
    });

    await adminRefresh();

    expect(order).toEqual(["lock:toko-admin-refresh", "fetch"]);
  });

  it("clears the tab session when the cookie is rejected", async () => {
    sessionStorage.setItem("toko_admin_access_token", "stale-jwt");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Refresh token required" }),
      })
    );

    await expect(adminRefresh()).rejects.toMatchObject({ status: 401 });
    expect(sessionStorage.getItem("toko_admin_access_token")).toBeNull();
  });
});
