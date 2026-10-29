import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { LocaleProvider } from "../lib/i18n";
import { ProjectList } from "../pages/ProjectList";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the project list", () => {
  it("loads /api/me and /api/projects once, not on every render", async () => {
    // A new `t` per render once re-ran the load effect in a loop and tripped
    // the edge's per-IP rate limit, leaving teachers with a white page.
    const me = vi.spyOn(api, "me").mockResolvedValue({
      user: { sub: "s-t", name: "Lærer", email: "", role: "laerer", orgId: "o", isSystemAdmin: false },
      termsAcceptedAt: "2026-01-01",
    });
    const list = vi.spyOn(api, "listProjects").mockResolvedValue([]);
    render(
      <LocaleProvider locale="da">
        <MemoryRouter>
          <ProjectList />
        </MemoryRouter>
      </LocaleProvider>,
    );
    await waitFor(() => expect(list).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(me).toHaveBeenCalledTimes(1);
    expect(list).toHaveBeenCalledTimes(1);
  });
});
