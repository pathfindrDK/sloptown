import { afterEach, describe, expect, it, vi } from "vitest";
import { AI_MAX_SIDE, imageForAi } from "../lib/imageForAi";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function bitmap(width: number, height: number) {
  return { width, height, close: vi.fn() } as unknown as ImageBitmap;
}

describe("the AI's copy of an attached image", () => {
  it("sends a small image as it is", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue(bitmap(800, 600)));
    const f = new File(["\x89PNG"], "skitse.png", { type: "image/png" });
    const out = await imageForAi(f);
    expect(out.mime).toBe("image/png");
    expect(out.dataUrl).toMatch(/^data:image\/png;base64,/);
  });

  it("scales a large design mockup down to a JPEG", async () => {
    const bmp = bitmap(4000, 2000);
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue(bmp));
    const ctx = { fillRect: vi.fn(), drawImage: vi.fn(), fillStyle: "" };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
    const toDataURL = vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/jpeg;base64,AAAA");
    const out = await imageForAi(new File(["x"], "design.png", { type: "image/png" }));
    expect(out).toEqual({ mime: "image/jpeg", dataUrl: "data:image/jpeg;base64,AAAA" });
    expect(ctx.drawImage).toHaveBeenCalledWith(bmp, 0, 0, AI_MAX_SIDE, AI_MAX_SIDE / 2);
    expect(toDataURL).toHaveBeenCalledWith("image/jpeg", 0.85);
    expect(bmp.close).toHaveBeenCalled();
  });

  it("falls back to the original when the browser can't decode or draw it", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn().mockRejectedValue(new Error("decode")));
    const out = await imageForAi(new File(["x"], "a.webp", { type: "image/webp" }));
    expect(out.mime).toBe("image/webp");
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue(bitmap(5000, 5000)));
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    expect((await imageForAi(new File(["x"], "b.png", { type: "image/png" }))).mime).toBe("image/png");
  });
});
