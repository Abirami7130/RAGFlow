import { getSession, refreshSession, setSession } from "./api";

describe("api client", () => {
  afterEach(() => {
    setSession(null);
    vi.unstubAllGlobals();
  });

  it("parses the error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: "conflict", message: "Taken.", details: {} } }), {
          status: 409,
        }),
      ),
    );
    const { api, ApiError } = await import("./api");
    await expect(api("/api/v1/auth/register", { method: "POST", body: "{}" })).rejects.toMatchObject({
      status: 409,
      code: "conflict",
      message: "Taken.",
    });
    await expect(api("/api/v1/auth/register", { method: "POST", body: "{}" })).rejects.toBeInstanceOf(ApiError);
  });

  it("replaces the session when a refresh succeeds", async () => {
    setSession({ accessToken: "old-access", refreshToken: "old-refresh" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ access_token: "new-access", refresh_token: "new-refresh", expires_in: 900 }), {
          status: 200,
        }),
      ),
    );
    await expect(refreshSession()).resolves.toBe(true);
    expect(getSession()).toEqual({ accessToken: "new-access", refreshToken: "new-refresh" });
  });

  it("clears the session when a refresh is rejected", async () => {
    setSession({ accessToken: "old-access", refreshToken: "old-refresh" });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    await expect(refreshSession()).resolves.toBe(false);
    expect(getSession()).toBeNull();
  });
});
