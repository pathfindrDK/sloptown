import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { QueueItem, Thread, Upload } from "../api/types";
import { ChatPanel } from "../editor/ChatPanel";
import { QueuedMessages } from "../editor/QueuedMessages";
import { LocaleProvider } from "../lib/i18n";
import { initialProjectState } from "../lib/projectState";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const thread: Thread = { id: "t1", projectId: "p1", title: "Escape room", ownerSub: "s", createdAt: "", archived: false, status: "idle" };
const upload: Upload = {
  id: "abcdef012345", name: "Professor.zip", kind: "zip", dir: ".uploads/abcdef012345", bytes: 9, fileCount: 6, files: [],
};

function renderChat() {
  const state = { ...initialProjectState, threads: [thread], messages: { t1: [] } };
  render(
    <LocaleProvider locale="da">
      <ChatPanel
        projectId="p1"
        state={state}
        dispatch={() => {}}
        activeThreadId="t1"
        setActiveThreadId={() => {}}
        draft={null}
        clearDraft={() => {}}
        blocked={false}
        onBlocked={() => {}}
      />
    </LocaleProvider>,
  );
  return document.querySelector<HTMLInputElement>('input[type="file"]')!;
}

describe("attaching files in the chat", () => {
  it("uploads a zip, waits for it, and sends its id with the message", async () => {
    let finish!: (u: Upload) => void;
    const up = vi.spyOn(api, "upload").mockReturnValue(new Promise((r) => (finish = r)));
    const send = vi.spyOn(api, "send").mockResolvedValue({ turnId: "x", queued: false });
    const picker = renderChat();
    expect(picker.accept).toContain(".zip");
    const zip = new File(["PK"], "Professor.zip", { type: "application/zip" });
    fireEvent.change(picker, { target: { files: [zip] } });
    expect(up).toHaveBeenCalledWith("p1", zip);
    expect(await screen.findByText("Sender …")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText(/Beskriv/), { target: { value: "Byg en app ud fra min zip" } });
    const sendBtn = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
    expect(sendBtn.disabled).toBe(true);
    finish(upload);
    expect(await screen.findByText("6 filer")).toBeTruthy();
    expect(sendBtn.disabled).toBe(false);
    fireEvent.click(sendBtn);
    await waitFor(() => expect(send).toHaveBeenCalledWith("t1", "Byg en app ud fra min zip", undefined, ["abcdef012345"]));
    await waitFor(() => expect(screen.queryByText("Professor.zip")).toBeNull());
  });

  it("refuses a file over 25 MB and shows a failed upload's message", async () => {
    vi.spyOn(api, "upload").mockRejectedValue(new Error("Zip-filen kunne ikke åbnes. Prøv at pakke den igen."));
    const picker = renderChat();
    const big = new File(["x"], "stor.zip");
    Object.defineProperty(big, "size", { value: 26 * 1024 * 1024 });
    fireEvent.change(picker, { target: { files: [big] } });
    expect(await screen.findByText(/højst fylde 25 MB/)).toBeTruthy();
    expect(api.upload).not.toHaveBeenCalled();
    fireEvent.change(picker, { target: { files: [new File(["x"], "ødelagt.zip")] } });
    expect(await screen.findByText(/kunne ikke åbnes/)).toBeTruthy();
    expect(screen.queryByText("ødelagt.zip")).toBeNull();
  });

  it("shows attached files on a waiting message", () => {
    const m: QueueItem = { id: "q", threadId: "t1", text: "Brug zip'en", imageCount: 0, uploadNames: ["Professor.zip"], authorSub: "s", queuedAt: null };
    render(
      <LocaleProvider locale="da">
        <QueuedMessages messages={[m]} paused={false} busy={false} onSendNow={() => {}} onEdit={() => {}} onDelete={() => {}} />
      </LocaleProvider>,
    );
    expect(screen.getByText("Professor.zip")).toBeTruthy();
  });
});
