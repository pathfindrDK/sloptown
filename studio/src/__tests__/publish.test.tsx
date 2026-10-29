import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../api/types";
import { TopBar } from "../components/TopBar";
import { LocaleProvider } from "../lib/i18n";
import { publishState } from "../lib/publish";

afterEach(cleanup);

describe("publishState", () => {
  it("is publish before the first publish, update with newer changes, published when current", () => {
    expect(publishState("b", null)).toBe("publish");
    expect(publishState(null, null)).toBe("publish");
    expect(publishState("b", "a")).toBe("update");
    expect(publishState("a", "a")).toBe("published");
  });
});

describe("the top bar's publish button", () => {
  const project = (publishedSha: string | null) =>
    ({ id: "p1", title: "Quiz", kind: "webapp", publishedSha, appUrl: publishedSha ? "https://quiz.example/" : null }) as Project;
  const show = (publishedSha: string | null, headSha = "b", publishing = false) => {
    const onPublish = vi.fn();
    const onUnpublish = vi.fn();
    render(
      <LocaleProvider locale="da">
        <TopBar
          project={project(publishedSha)}
          budget={null}
          headSha={headSha}
          canEdit
          publishing={publishing}
          onBack={() => {}}
          onRename={() => {}}
          onShare={() => {}}
          onPublish={onPublish}
          onUnpublish={onUnpublish}
          onDownload={() => {}}
        />
      </LocaleProvider>,
    );
    return { onPublish, onUnpublish };
  };

  it("says Udgiv and offers no unpublish before the first publish", () => {
    const { onPublish } = show(null);
    fireEvent.click(screen.getByRole("button", { name: /Udgiv/ }));
    expect(onPublish).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Flere valg for udgivelsen" })).toBeNull();
  });

  it("says Opdater with the update hint when there are newer changes", () => {
    const { onPublish } = show("a");
    const button = screen.getByRole("button", { name: /Opdater/ });
    expect(button.getAttribute("title")).toBe("Opdater udgivelse med seneste ændringer");
    fireEvent.click(button);
    expect(onPublish).toHaveBeenCalled();
  });

  it("says Opdaterer … while updating", () => {
    show("a", "b", true);
    expect((screen.getByRole("button", { name: /Opdaterer/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("is a disabled Udgivet when current, and the menu offers Stop udgivelse", () => {
    const { onUnpublish } = show("b");
    expect((screen.getByRole("button", { name: /Udgivet/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Flere valg for udgivelsen" }));
    fireEvent.click(screen.getByText("Stop udgivelse"));
    expect(onUnpublish).toHaveBeenCalled();
  });
});
