import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, ApiError } from "../api";

describe("api client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("GET requests parse JSON response", async () => {
    const mockData = [{ id: "1", name: "Test" }];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await api.get("/projects");
    expect(result).toEqual(mockData);
    expect(fetch).toHaveBeenCalledWith("/api/projects", expect.objectContaining({
      headers: expect.objectContaining({ "Content-Type": "application/json" }),
    }));
  });

  it("POST sends JSON body", async () => {
    const body = { name: "New Project" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "2", ...body }), { status: 201 }),
    );

    await api.post("/projects", body);
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(body),
      }),
    );
  });

  it("PATCH sends JSON body", async () => {
    const body = { name: "Updated" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "1", ...body }), { status: 200 }),
    );

    await api.patch("/projects/1", body);
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    );
  });

  it("DELETE makes delete request", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await api.delete("/projects/1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("throws ApiError on non-OK response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("Not Found", { status: 404 }),
    );

    await expect(api.get("/missing")).rejects.toThrow(ApiError);
    try {
      await api.get("/missing");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(404);
    }
  });

  it("returns undefined for 204 responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    const result = await api.delete("/projects/1");
    expect(result).toBeUndefined();
  });

  it("throws ApiError with timeout message on AbortError", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new DOMException("The operation was aborted.", "AbortError"),
    );

    await expect(api.get("/projects")).rejects.toThrow(ApiError);
    try {
      await api.get("/projects");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(0);
      expect((e as ApiError).message).toContain("timed out");
    }
  });

  it("throws ApiError with network message on fetch failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new TypeError("Failed to fetch"),
    );

    await expect(api.get("/projects")).rejects.toThrow(ApiError);
    try {
      await api.get("/projects");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(0);
      expect((e as ApiError).message).toContain("Network error");
    }
  });
});
