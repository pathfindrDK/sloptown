// Browser test of the real studio inside fake Miira's iframe, against the running dev stack.
//
//   tests/e2e/browser.sh     (runs this in the Playwright container on the host network)
//
// Signs in to fake Miira as a teacher (u-jonas, so the terms modal shows on first
// use), creates a Vibe through the /drive/new deep link, and checks inside the
// embedded studio: drive:init/launch-token handshake, terms modal, editor chrome,
// the preview iframe served by the vibe-dev container through the edge, the file
// tree, and a read-only code tab. Screenshots go to $OUT (default /out).
// Fails on any uncaught page error in the studio frame.
const { chromium } = require("playwright");

// E2E_DOMAIN=miira-apps.dk: the public test deployment, behind the password gate.
const DOMAIN = process.env.E2E_DOMAIN || "";
const MIIRA = DOMAIN ? `https://miira.${DOMAIN}` : "http://miira.sloptown.localhost:8080";
const OUT = process.env.OUT || "/out";
const step = (msg) => console.log(`- ${msg}`);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: "da-DK" });
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    // "Failed to load resource" also fires for expected 4xx (e.g. 403 terms_required);
    // server failures are caught by the response handler below instead.
    if (m.type() === "error" && /studio\./.test(m.location().url || "") && !/Failed to load resource/.test(m.text()))
      errors.push(`console: ${m.text()}`);
  });
  const failed = [];
  page.on("requestfailed", (r) => failed.push(`${r.failure()?.errorText} ${r.url().slice(0, 120)}`));
  page.on("response", (r) => {
    if (r.status() >= 500 && /sloptown\.localhost|miira-apps\.dk/.test(r.url())) errors.push(`HTTP ${r.status()} ${r.url()}`);
  });
  try {
    if (DOMAIN) {
      await page.goto(`${MIIRA}/`); // -> gate login page on the root domain
      await page.locator("input[name=password]").fill(process.env.E2E_GATE_PASSWORD || "");
      await page.locator("button").click();
      await page.waitForURL(`${MIIRA}/`);
      step("passed the password gate");
    }
    await page.goto(`${MIIRA}/`);
    await page.locator("form", { hasText: "Jonas Holm" }).locator("button").click();
    await page.waitForURL(`${MIIRA}/`);
    step("signed in to fake Miira as Jonas Holm");

    await page.goto(`${MIIRA}/drive/new?type=vibe`);
    await page.waitForURL(/\/files\/[0-9a-f]+$/);
    const fileId = page.url().split("/").pop();
    step(`created Vibe ${fileId} via /drive/new`);

    const studio = page.frameLocator("#vibe");
    const terms = studio.getByRole("button", { name: /accepter/i });
    const editor = studio.getByText("Forhåndsvisning").first();
    const webapp = studio.getByRole("button", { name: /En app/ });
    // Terms only on a user's first visit; the chooser comes first once they are accepted.
    await terms.or(webapp).or(editor).first().waitFor({ timeout: 90000 });
    step("drive:init handshake done, launch token exchanged");
    if (await terms.isVisible()) {
      await page.screenshot({ path: `${OUT}/browser-terms.png` });
      await terms.click();
      step("accepted the terms of use in the embedded studio");
    }

    // A new Vibe asks what to build before anything is created.
    await webapp.or(editor).first().waitFor({ timeout: 30000 });
    if (await webapp.isVisible()) {
      await page.screenshot({ path: `${OUT}/browser-start.png` });
      await webapp.click();
      step("chose \"En app\" in the start chooser");
    }
    await editor.waitFor({ timeout: 90000 });
    step("editor loaded inside the iframe");

    const preview = studio.frameLocator("iframe[src*='preview-'], iframe[src*='/_vibe/session']").first();
    await preview.locator("#root *").first().waitFor({ timeout: 90000 });
    step("preview iframe rendered the template app from vibe-dev");

    await studio.getByRole("tab", { name: "Filer" }).or(studio.getByText("Filer", { exact: true })).first().click();
    await studio.getByText("App.tsx", { exact: true }).first().click();
    await studio.locator(".cm-editor").first().waitFor({ timeout: 30000 });
    step("opened web/src/App.tsx in a read-only code tab");

    await page.screenshot({ path: `${OUT}/browser-editor.png` });
    if (errors.length) throw new Error(`errors in the studio frame:\n${errors.join("\n")}`);
    console.log("browser test: ok");
  } catch (e) {
    await page.screenshot({ path: `${OUT}/browser-failure.png` }).catch(() => {});
    if (failed.length) console.error(`  failed requests:\n    ${failed.join("\n    ")}`);
    for (const f of page.frames()) {
      const text = await f.evaluate(() => document.body?.innerText?.slice(0, 160) ?? "").catch((e) => `<${e.message}>`);
      console.error(`  frame ${f.url()}\n    ${JSON.stringify(text)}`);
    }
    console.error(`browser test FAILED: ${e.message}`);
    if (errors.length) console.error(errors.join("\n"));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
