// Browser test of the studio inside the REAL Miira (miira3), against the running stacks.
//
//   E2E_MIIRA_ORIGIN=http://miira.localhost:8090 tests/e2e/browser.sh
//
// Needs SlopTown up with deploy/dev-up.sh and SLOPTOWN_MIIRA_ORIGIN pointing at a miira3
// whose API has VIBE_STUDIO_ORIGIN set (docs/miira-integration.md, "Running against miira3").
// Uses miira3's seeded accounts (dev-login): Lea Lærer (lærer), Erik Elev (elev), the
// platform admin. Checks, in Miira's own UI:
//   1. "Opret ny" offers Vibe to a lærer and not to an elev
//   2. /drive/new?type=vibe (the studio's "Ny Vibe") creates a Vibe and opens it
//   3. the studio boots inside Miira's iframe: drive:ready -> launch token -> drive:init,
//      and the app preview renders two frames deep
//   4. the studio's Del opens Miira's share dialog over the iframe, and a second Vibe
//      started "fra bunden" has no preview or publish but downloads as a .zip
//   5. the standalone studio signs a signed-out user in through Miira's OIDC authorize,
//      which sends them through Miira's login and resumes afterwards
//   6. a klasse share lets an elev open the Vibe inside Miira as a viewer
//   7. System -> Vibe shows the rollout and reaches SlopTown's admin API
// Fails on uncaught page errors and 5xx from either side. Screenshots go to $OUT.
const playwright = require("playwright");
// E2E_BROWSER=webkit|firefox: Safari and Firefox partition or block third-party
// cookies harder than Chrome, which is what the studio-in-Miira iframe meets.
const ENGINE = process.env.E2E_BROWSER || "chromium";

const MIIRA = (process.env.E2E_MIIRA_ORIGIN || "http://miira.localhost:8090").replace(/\/$/, "");
const STUDIO = process.env.E2E_STUDIO_ORIGIN || "http://studio.sloptown.localhost:8080";
const OUT = process.env.OUT || "/out";
const step = (msg) => console.log(`- ${msg}`);

// Closing a context aborts the drive's in-flight requests (event stream, polls),
// which the drive reports as page errors; those are the harness, not the product.
async function closeQuietly(context) {
  for (const p of context.pages()) p.removeAllListeners("pageerror");
  await context.close();
}

function watch(page, errors) {
  skipTours(page);
  page.on("pageerror", (e) => errors.push(`pageerror on ${page.url().slice(0, 80)}: ${e.message}\n${(e.stack || "").slice(0, 600)}`));
  page.on("response", (r) => {
    if (r.status() >= 500) errors.push(`HTTP ${r.status()} ${r.url()}`);
  });
}

async function devLogin(context, email) {
  const r = await context.request.post(`${MIIRA}/api/auth/dev-login`, { data: { email } });
  if (!r.ok()) throw new Error(`dev-login ${email}: ${r.status()}`);
}

// Miira greets a fresh session with product tours that sit over everything and
// arrive a moment after load; "Spring over" jumps to the AI guidelines, which have
// to be accepted. Get through whichever step is in the way.
function skipTours(page) {
  const tour = page.locator(".onboarding-backdrop");
  page.addLocatorHandler(
    tour,
    async () => {
      const accept = tour.getByRole("button", { name: "Accepter" });
      if (await accept.isVisible()) await accept.click();
      else await tour.getByRole("button", { name: "Spring over" }).click();
    },
    { noWaitAfter: true },
  );
}

async function createMenuHasVibe(page) {
  await page.getByRole("button", { name: "Redaktion" }).first().click();
  await page.getByRole("button", { name: /Opret ny/ }).first().click();
  const vibe = page.getByRole("dialog").getByText("Vibe", { exact: true });
  const seen = await vibe.first().isVisible({ timeout: 5000 }).catch(() => false);
  await page.keyboard.press("Escape");
  return seen;
}

(async () => {
  const browser = await playwright[ENGINE].launch();
  step(`engine: ${ENGINE}`);
  const errors = [];
  try {
    // --- Lea: create, embed, share ---------------------------------------------------
    const lea = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "da-DK" });
    const page = await lea.newPage();
    watch(page, errors);
    await devLogin(lea, "teacher@miira.dk");
    await page.goto(`${MIIRA}/`);
    await page.getByRole("button", { name: "Redaktion" }).first().waitFor({ timeout: 60000 });
    if (!(await createMenuHasVibe(page))) throw new Error("Opret ny has no Vibe tile for a lærer");
    step("Lea Lærer: Opret ny offers Vibe");

    await page.goto(`${MIIRA}/drive/new?type=vibe`);
    await page.waitForURL(/#\/file\/[0-9a-f-]{36}/, { timeout: 30000 });
      const fileId = page.url().match(/#\/file\/([0-9a-f-]{36})/)[1];
    step(`/drive/new?type=vibe created Vibe ${fileId} and opened it`);

    const frame = page.frameLocator('iframe[title^="Vibe"]');
    const terms = frame.getByRole("button", { name: /accepter/i });
    const editor = frame.getByText("Forhåndsvisning").first();
    const webapp = frame.getByRole("button", { name: /En app/ });
    // Terms only on a user's first visit; the chooser comes first once they are accepted.
    await terms.or(webapp).or(editor).first().waitFor({ timeout: 90000 });
    step("studio booted in Miira's iframe (drive:init with launch token)");
    if (await terms.isVisible()) {
      await page.screenshot({ path: `${OUT}/miira3-terms.png` });
      await terms.click();
    }
    await webapp.or(editor).first().waitFor({ timeout: 30000 });
    if (await webapp.isVisible()) {
      await page.screenshot({ path: `${OUT}/miira3-start.png` });
      await webapp.click();
    }
    await editor.waitFor({ timeout: 120000 });
    // Two frames deep: Miira -> studio -> the app's preview host, whose
    // frame-ancestors must allow both.
    const preview = frame.frameLocator("iframe[src*='preview-'], iframe[src*='/_vibe/session']").first();
    await preview.locator("#root *").first().waitFor({ timeout: 90000 });
    await page.screenshot({ path: `${OUT}/miira3-editor.png` });
    step("editor and the app preview render inside Miira");

    await frame.getByRole("button", { name: /^Del/ }).first().click();
    const dialog = page.getByRole("dialog");
    await dialog.first().waitFor({ timeout: 15000 });
    await page.screenshot({ path: `${OUT}/miira3-share.png` });
    step("studio Del -> Miira's share dialog over the iframe");
    await page.keyboard.press("Escape");

    // A second Vibe, "fra bunden": no preview and no publish, files to download.
    await page.goto(`${MIIRA}/drive/new?type=vibe`);
    await page.waitForURL(/#\/file\/[0-9a-f-]{36}/, { timeout: 30000 });
    const scratchFrame = page.frameLocator('iframe[title^="Vibe"]');
    await scratchFrame.getByRole("button", { name: /Fra bunden/ }).click({ timeout: 60000 });
    await scratchFrame.getByRole("button", { name: /Hent filer/ }).waitFor({ timeout: 60000 });
    await scratchFrame.getByText("Mit projekt").first().waitFor({ timeout: 60000 });
    if (await scratchFrame.getByRole("button", { name: /Udgiv/ }).count()) throw new Error("scratch project offers Udgiv");
    if (await scratchFrame.getByText("Forhåndsvisning", { exact: true }).count()) throw new Error("scratch project shows a preview");
    const download = page.waitForEvent("download", { timeout: 30000 });
    await scratchFrame.getByRole("button", { name: /Hent filer/ }).click();
    if (!(await download).suggestedFilename().endsWith(".zip")) throw new Error("download is not a .zip");
    await page.screenshot({ path: `${OUT}/miira3-scratch.png` });
    step("\"Fra bunden\": README open, no preview or Udgiv, Hent filer downloads a .zip");

    // Each step gets a fresh browser context; close the last one first. Over the plain
    // HTTP/1.1 of a dev server its open event streams eat Chrome's six connections per
    // host, and the next page's hundreds of dev modules then never load.
    await closeQuietly(lea);

    // --- signed-out standalone studio -> Miira login -> back -------------------------
    // WebKit drops `Secure` cookies over plain http, even on *.localhost (Chrome and
    // Firefox keep them), so SlopTown's login cookie never sticks there locally; on a
    // real https domain it does. Skipped rather than faked.
    const standalone = !(ENGINE === "webkit" && STUDIO.startsWith("http://"));
    if (!standalone) step("SKIPPED standalone login: WebKit + http (Secure cookies need https)");
    const fresh = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "da-DK" });
    if (standalone) {
      const p2 = await fresh.newPage();
      watch(p2, errors);
      await p2.goto(`${STUDIO}/auth/login`);
      // authorize -> no session -> the drive root, which stashes the request and shows login
      await p2.waitForURL((u) => u.origin === MIIRA && !u.pathname.startsWith("/api/"), {
        timeout: 30000,
        waitUntil: "commit",
      });
      const leaLogin = p2.getByRole("button", { name: /Lea Lærer/ }).first();
      await leaLogin.waitFor({ timeout: 60000 });
      await p2.screenshot({ path: `${OUT}/miira3-login.png` });
      step("standalone studio sent a signed-out user to Miira's login");
      await leaLogin.click();
      await p2.waitForURL((u) => u.href.startsWith(STUDIO), { timeout: 30000 });
      await p2.getByText(/Mine Vibes|Vibes|Ny Vibe/).first().waitFor({ timeout: 30000 });
      await p2.screenshot({ path: `${OUT}/miira3-studio-standalone.png` });
      // From the page: the session cookie is `Secure`, which a browser sends on
      // http://*.localhost but Playwright's request client does not.
      const me = await p2.evaluate(async () => (await fetch("/api/me", { credentials: "include" })).json());
      if (me.user?.name !== "Lea Lærer") throw new Error(`studio session is ${JSON.stringify(me.user)}`);
      step("after Miira's login the authorize request resumed and the studio is signed in as Lea");

    }
    await closeQuietly(fresh);
    const lea2 = await browser.newContext();
    await devLogin(lea2, "teacher@miira.dk");

    // --- Erik: no Vibe tile ----------------------------------------------------------
    const erik = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "da-DK" });
    const p3 = await erik.newPage();
    watch(p3, errors);
    await devLogin(erik, "student@miira.dk");
    const meErik = await (await erik.request.get(`${MIIRA}/api/auth/me`)).json();
    if (meErik.features?.vibe !== false) throw new Error("an elev must not get features.vibe");
    step("Erik Elev: no Vibe creation (features.vibe false)");
    // The Vibe Lea made above, once shared with Erik's klasse, opens for him as a viewer.
    const klasser = await (await lea2.request.get(`${MIIRA}/api/shares/classes`)).json();
    const klasse = klasser.find((k) => k.name === "1. A");
    const shared = await lea2.request.post(`${MIIRA}/api/shares/class`, {
      data: { fileId, classIds: [klasse.id], mode: "consumer" },
    });
    if (shared.status() !== 204) throw new Error(`class share: ${shared.status()}`);
    await p3.goto(`${MIIRA}/#/file/${fileId}`);
    const erikFrame = p3.frameLocator('iframe[title^="Vibe"]');
    await erikFrame.locator("body *").first().waitFor({ timeout: 60000 });
    const launched = await (await erik.request.post(`${MIIRA}/api/vibe/files/${fileId}/launch`)).json();
    if (launched.fileRole !== "viewer") throw new Error(`Erik's role is ${launched.fileRole}`);
    await p3.waitForTimeout(3000);
    await p3.screenshot({ path: `${OUT}/miira3-viewer.png` });
    step("Erik Elev opens the class-shared Vibe inside Miira as a viewer");

    await closeQuietly(erik);

    // --- System -> Vibe --------------------------------------------------------------
    const admin = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "da-DK" });
    const p4 = await admin.newPage();
    watch(p4, errors);
    await devLogin(admin, "admin@miira.dk");
    await p4.goto(`${MIIRA}/#/system/vibe`);
    await p4.getByText("Håndhæv budgetter").waitFor({ timeout: 30000 });
    await p4.getByText("Lea Lærer").first().waitFor({ timeout: 10000 });
    step("System -> Vibe lists Lea on the rollout (Adgang)");
    await p4.screenshot({ path: `${OUT}/miira3-system-vibe.png`, fullPage: true });
    step("System -> Vibe shows SlopTown's budget settings");

    if (errors.length) throw new Error(errors.join("\n"));
    console.log(`browser test against miira3 passed (${ENGINE})`);
  } catch (e) {
    console.error(`FAILED: ${e.message}`);
    for (const p of browser.contexts().flatMap((c) => c.pages())) {
      const text = await p.evaluate(() => document.body.innerText.length).catch(() => -1);
      console.error(`  page ${p.url().slice(0, 100)} (${text} chars of text)`);
      await p.screenshot({ path: `${OUT}/miira3-failure-${Date.now()}.png` }).catch(() => {});
    }
    if (errors.length) console.error(errors.join("\n"));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
