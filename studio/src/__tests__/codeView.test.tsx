import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { CodeView } from "../editor/CodeView";
import { LocaleProvider } from "../lib/i18n";

beforeEach(() => {
  // jsdom has no ResizeObserver; the toolbar's segmented control uses it.
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const show = (path = "README.md") =>
  render(
    <LocaleProvider locale="da">
      <CodeView projectId="p1" path={path} compareSha={null} highlight={false} reloadSignal={0} />
    </LocaleProvider>,
  );

describe("CodeView when a file can't be opened", () => {
  it("a missing file tells the teacher how to carry on, not just that something went wrong", async () => {
    vi.spyOn(api, "file").mockRejectedValue(new ApiError(404, "not_found", "Ikke fundet"));
    show();
    expect(await screen.findByText("Filen findes ikke (længere)")).toBeTruthy();
    expect(screen.getByText(/skriv i chatten, hvad du vil lave/)).toBeTruthy();
    expect(screen.queryByText("Noget gik galt")).toBeNull();
    expect(screen.queryByText("Prøv igen")).toBeNull();
  });

  it("any other failure can be retried", async () => {
    const file = vi
      .spyOn(api, "file")
      .mockRejectedValueOnce(new ApiError(503, "upstream_unreachable", "Vibe svarer ikke"))
      .mockResolvedValue({ path: "README.md", content: "# Hej", locked: false, language: "markdown" } as never);
    show();
    expect(await screen.findByText("Filen kunne ikke åbnes lige nu")).toBeTruthy();
    fireEvent.click(screen.getByText("Prøv igen"));
    await waitFor(() => expect(screen.queryByText("Filen kunne ikke åbnes lige nu")).toBeNull());
    expect(file).toHaveBeenCalledTimes(2);
  });
});
