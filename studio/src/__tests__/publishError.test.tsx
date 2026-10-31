import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/types";
import { PublishErrorModal } from "../components/PublishError";
import { LocaleProvider } from "../lib/i18n";

const renderModal = (error: ApiError) => {
  const onFix = vi.fn();
  const onClose = vi.fn();
  render(
    <LocaleProvider locale="da">
      <PublishErrorModal error={error} onFix={onFix} onClose={onClose} />
    </LocaleProvider>,
  );
  return { onFix, onClose };
};

afterEach(cleanup);

describe("PublishErrorModal", () => {
  it("hands a failed build's log to the chat", () => {
    const { onFix, onClose } = renderModal(
      new ApiError(422, "build_failed", "Appen kunne ikke bygges.", { log: "src/App.tsx(3,1): error TS2304" }),
    );
    expect(screen.getByText(/TS2304/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Ret fejlen/ }));
    expect(onFix).toHaveBeenCalledWith(expect.stringContaining("error TS2304"));
    expect(onClose).toHaveBeenCalled();
  });

  it("lists locked files and offers no AI fix for them", () => {
    renderModal(new ApiError(422, "locked_files_changed", "Ændrer platformens filer", { paths: ["web/package.json"] }));
    expect(screen.getByText("web/package.json")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Ret fejlen/ })).toBeNull();
  });

  it("lists what the pre-publish check found and drafts a fix for the AI", () => {
    const findings = [{ rule: "fast-poll", title: "Appen spørger serveren for tit", path: "web/src/App.tsx", line: 4 }];
    const { onFix } = renderModal(new ApiError(0, "check_failed", "AI'en kunne ikke rette alt.", { findings }));
    expect(screen.getByText("Appen spørger serveren for tit")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Ret fejlen/ }));
    expect(onFix).toHaveBeenCalledWith(expect.stringContaining("`web/src/App.tsx:4` (fast-poll)"));
  });

  it("offers no AI fix when the check was stopped", () => {
    renderModal(new ApiError(0, "check_stopped", "Udgivelsen blev afbrudt."));
    expect(screen.getByText("Udgivelsen blev afbrudt.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Ret fejlen/ })).toBeNull();
  });
});
