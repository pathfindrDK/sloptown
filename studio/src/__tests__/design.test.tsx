import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { MOCK_DESIGN } from "../api/mockDesign";
import { ApiError, type Project } from "../api/types";
import { SettingsPanel } from "../editor/SettingsPanel";
import { LocaleProvider } from "../lib/i18n";
import { ShellProvider, standaloneShell } from "../lib/shell";
import { StartChooser } from "../pages/StartChooser";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
beforeEach(() => {
  vi.spyOn(api, "design").mockResolvedValue(MOCK_DESIGN);
});

const project = (over: Partial<Project> = {}): Project => ({
  id: "p1",
  title: "Quiz",
  kind: "webapp",
  slug: "quiz-1",
  ownerSub: "s-t",
  myRole: "owner",
  status: "ready",
  previewUrl: "/",
  appUrl: null,
  publishedSha: null,
  public: false,
  description: "",
  style: "miira",
  palette: "hav",
  createdAt: "",
  updatedAt: "",
  ...over,
});

describe("the design catalog", () => {
  it("in mock mode is the same as template-design/catalog.json", () => {
    // Tests run in studio/ with the whole repo checked out (scripts/test-all.sh).
    const repo = JSON.parse(readFileSync(resolve(process.cwd(), "../template-design/catalog.json"), "utf8"));
    expect(MOCK_DESIGN).toEqual(repo);
  });
});

describe("starting a Vibe", () => {
  const renderStart = () => {
    const onCreated = vi.fn();
    render(
      <LocaleProvider locale="da">
        <StartChooser pid="p1" onCreated={onCreated} />
      </LocaleProvider>,
    );
    return onCreated;
  };

  it("asks for a design system and palette after 'En app'", async () => {
    const create = vi.spyOn(api, "createProject").mockResolvedValue(project({ style: "bulma", palette: "skov" }));
    const onCreated = renderStart();
    fireEvent.click(screen.getByRole("button", { name: /En app/ }));
    fireEvent.click(await screen.findByRole("radio", { name: /Bulma/ }));
    fireEvent.click(screen.getByRole("radio", { name: /Skov/ }));
    expect(screen.getByRole("radio", { name: /Bulma/ }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(create).toHaveBeenCalledWith("p1", "webapp", undefined, { style: "bulma", palette: "skov" });
  });

  it("names the app and shows the choices in the preview before starting", async () => {
    const create = vi.spyOn(api, "createProject").mockResolvedValue(project());
    renderStart();
    fireEvent.click(screen.getByRole("button", { name: /En app/ }));
    const preview = () => screen.getByRole("img", { name: /^Forhåndsvisning/ });
    await screen.findByRole("radio", { name: /Bulma/ });
    expect(preview().getAttribute("aria-label")).toBe("Forhåndsvisning af Ny Vibe i Miira med farverne Overrask mig");
    expect(screen.getByText("Farverne vælges tilfældigt")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Hvad skal appen hedde?"), { target: { value: "  Vikingequiz " } });
    fireEvent.click(screen.getByRole("radio", { name: /Material/ }));
    fireEvent.click(screen.getByRole("radio", { name: /Solnedgang/ }));
    expect(preview().getAttribute("aria-label")).toBe("Forhåndsvisning af Vikingequiz i Material med farverne Solnedgang");
    expect(preview().getAttribute("data-style")).toBe("materialize");
    expect(preview().textContent).toContain("Vikingequiz");
    // The palette's own colours, light first, dark on request.
    expect(preview().style.getPropertyValue("--pv-primary")).toBe(MOCK_DESIGN.palettes[3].light.primary);
    fireEvent.click(screen.getByRole("button", { name: "Vis i mørk tilstand" }));
    expect(preview().style.getPropertyValue("--pv-primary")).toBe(MOCK_DESIGN.palettes[3].dark.primary);

    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("p1", "webapp", "Vikingequiz", { style: "materialize", palette: "solnedgang" }),
    );
  });

  it("leaves the palette to the control plane with 'Overrask mig', and 'Fra bunden' skips the design", async () => {
    const create = vi.spyOn(api, "createProject").mockResolvedValue(project());
    renderStart();
    fireEvent.click(screen.getByRole("button", { name: /En app/ }));
    expect((await screen.findByRole("radio", { name: /Overrask mig/ })).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("p1", "webapp", undefined, { style: "miira", palette: undefined }));

    cleanup();
    renderStart();
    fireEvent.click(screen.getByRole("button", { name: /Fra bunden/ }));
    await waitFor(() => expect(create).toHaveBeenLastCalledWith("p1", "scratch", undefined, undefined));
  });
});

describe("design in Indstillinger", () => {
  const renderSettings = (p: Project) => {
    const onChange = vi.fn();
    const onAskAi = vi.fn();
    render(
      <LocaleProvider locale="da">
        <ShellProvider shell={standaloneShell(() => {})}>
          <SettingsPanel project={p} onChange={onChange} onAskAi={onAskAi} />
        </ShellProvider>
      </LocaleProvider>,
    );
    return { onChange, onAskAi };
  };

  it("changes the palette at once", async () => {
    const update = vi.spyOn(api, "updateProject").mockResolvedValue(project({ palette: "skov" }));
    const { onChange, onAskAi } = renderSettings(project());
    fireEvent.click(await screen.findByRole("button", { name: "Farver: Hav" }));
    fireEvent.click(screen.getByRole("option", { name: "Skov" }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(update).toHaveBeenCalledWith("p1", { palette: "skov" });
    expect(onAskAi).not.toHaveBeenCalled();
  });

  it("confirms a new design system, then hands the teacher a prompt for the AI", async () => {
    const update = vi.spyOn(api, "updateProject").mockResolvedValue(project({ style: "tailwind" }));
    const { onAskAi } = renderSettings(project());
    fireEvent.click(await screen.findByRole("button", { name: "Designsystem: Miira" }));
    fireEvent.click(screen.getByRole("option", { name: "Tailwind" }));
    expect(update).not.toHaveBeenCalled();
    expect(screen.getByText(/passer til Tailwind/)).toBeTruthy();
    // The dialog previews the Vibe in the new system with its current palette.
    expect(screen.getByRole("img", { name: "Forhåndsvisning af Quiz i Tailwind med farverne Hav" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Skift og spørg AI'en" }));
    await waitFor(() => expect(onAskAi).toHaveBeenCalledWith(expect.stringContaining("designsystem til Tailwind")));
    expect(update).toHaveBeenCalledWith("p1", { style: "tailwind" });
  });

  it("says so when the AI is busy, and doesn't ask the AI", async () => {
    vi.spyOn(api, "updateProject").mockRejectedValue(new ApiError(409, "turn_running", "busy"));
    const { onAskAi } = renderSettings(project());
    fireEvent.click(await screen.findByRole("button", { name: "Designsystem: Miira" }));
    fireEvent.click(screen.getByRole("option", { name: "Bulma" }));
    fireEvent.click(screen.getByRole("button", { name: "Skift og spørg AI'en" }));
    expect(await screen.findByText(/Vent til AI'en er færdig/)).toBeTruthy();
    expect(onAskAi).not.toHaveBeenCalled();
  });

  it("shows an older Vibe's palette as Miira's default, and a scratch project has no design", async () => {
    renderSettings(project({ style: null, palette: null }));
    expect(await screen.findByRole("button", { name: "Farver: Miira (standard)" })).toBeTruthy();
    cleanup();
    renderSettings(project({ kind: "scratch", style: null, palette: null }));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText("Designsystem")).toBeNull();
  });
});
