// The copy of an attached image that goes to the AI, as a data URL. OpenCode resends the
// whole conversation (images included) on every model call and the egress proxy refuses a
// request over 8 MB, so a large photo or screenshot is scaled down to AI_MAX_SIDE and
// re-encoded as JPEG; a small one goes as it is. The public library gets the original.

export const AI_MAX_SIDE = 1600;
export const AI_MAX_BYTES = 1024 * 1024;

export function readDataUrl(f: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(f);
  });
}

export async function imageForAi(f: File): Promise<{ mime: string; dataUrl: string }> {
  const original = async () => ({ mime: f.type, dataUrl: await readDataUrl(f) });
  if (typeof createImageBitmap !== "function") return original();
  let bmp: ImageBitmap;
  try {
    bmp = await createImageBitmap(f);
  } catch {
    return original(); // the browser can't decode it; the control plane checks the type
  }
  try {
    const scale = Math.min(1, AI_MAX_SIDE / Math.max(bmp.width, bmp.height));
    if (scale === 1 && f.size <= AI_MAX_BYTES) return original();
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) return original();
    ctx.fillStyle = "#fff"; // JPEG has no transparency: a transparent PNG goes on white
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
    return dataUrl.startsWith("data:image/jpeg;base64,") ? { mime: "image/jpeg", dataUrl } : original();
  } finally {
    bmp.close();
  }
}
