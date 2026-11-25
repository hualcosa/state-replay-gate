// Captures review screenshots of the built page with a locally installed Chrome via playwright-core.
// Usage: pnpm --dir web build && pnpm --dir web run screenshots
// Also fails if any request leaves the preview origin or the console reports an error.
import { mkdirSync } from "node:fs";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { preview } from "vite";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const out = resolve(root, "screenshots");
mkdirSync(out, { recursive: true });

const chromePath =
  process.env.CHROME_PATH ||
  ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"].find(existsSync);
if (!chromePath) throw new Error("Set CHROME_PATH to a Chrome/Chromium executable.");

const server = await preview({ root, preview: { port: 4179, strictPort: true, host: "127.0.0.1" } });
const origin = "http://127.0.0.1:4179";
const browser = await chromium.launch({ executablePath: chromePath, headless: true });
const problems = [];

async function open(viewport, reducedMotion, forceFixture = false) {
  // With data/run.json blocked the browser logs one expected 404 before the fixture fallback.
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: viewport.width < 600 ? 1 : 2, reducedMotion, colorScheme: "dark" });
  const page = await ctx.newPage();
  if (forceFixture) await page.route("**/data/run.json", (r) => r.fulfill({ status: 404, body: "" }));
  page.on("request", (r) => {
    if (!r.url().startsWith(origin) && !r.url().startsWith("data:")) problems.push(`external request: ${r.url()}`);
  });
  page.on("console", (m) => m.type() === "error" && !(forceFixture && m.text().includes("404")) && problems.push(`console error: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  await page.goto(origin + "/", { waitUntil: "networkidle" });
  await page.waitForSelector("[data-testid=hero]");
  await page.evaluate(() => document.fonts.ready);
  // Element screenshots would otherwise catch the sticky bar.
  await page.addStyleTag({ content: ".topbar{position:static!important}" });
  return { ctx, page };
}

async function scrollThrough(page) {
  const h = await page.evaluate(() => document.body.scrollHeight);
  for (let y = 0; y < h; y += 500) {
    await page.evaluate((v) => window.scrollTo(0, v), y);
    await page.waitForTimeout(60);
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(1300);
}

// 1. Desktop, static (reduced motion): the finished story.
{
  const { ctx, page } = await open({ width: 1360, height: 900 }, "reduce");
  await page.screenshot({ path: `${out}/01-top.png` });
  await page.locator("#hero").screenshot({ path: `${out}/02-hero-final.png` });
  await scrollThrough(page);
  await page.locator("#headline").screenshot({ path: `${out}/03-headline.png` });
  await page.locator("#scoreboard").screenshot({ path: `${out}/04-scoreboard.png` });
  await page.locator("#scenarios").locator("summary").first().click();
  await page.locator("#scenarios").screenshot({ path: `${out}/05-scenarios.png` });
  await page.locator("#honest").screenshot({ path: `${out}/06-honest.png` });
  await ctx.close();
}
// 2. Desktop with motion: hero mid-animation (T1 just arrived, arm lanes landing).
{
  const { ctx, page } = await open({ width: 1360, height: 1000 }, "no-preference");
  await page.locator("#hero").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => document.querySelector("#hero")?.getAttribute("data-stage") === "4", null, { timeout: 15000 });
  await page.waitForTimeout(1400);
  await page.locator("#hero").screenshot({ path: `${out}/07-hero-playing.png` });
  await ctx.close();
}
// 2b. Fixture fallback with its badge (data/run.json blocked).
{
  const { ctx, page } = await open({ width: 1360, height: 760 }, "reduce", true);
  await page.screenshot({ path: `${out}/09-fixture-badge.png` });
  await ctx.close();
}
// 3. Phone width.
{
  const { ctx, page } = await open({ width: 390, height: 844 }, "reduce");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (overflow > 0) problems.push(`horizontal page overflow at 390px: ${overflow}px`);
  await scrollThrough(page);
  const after = await page.evaluate(() => [document.documentElement.scrollWidth, window.devicePixelRatio, innerWidth]);
  console.log("phone scrollWidth/dpr/innerWidth", after.join(" "));
  await page.screenshot({ path: `${out}/08-phone-full.png`, fullPage: true });
  await ctx.close();
}

await browser.close();
await new Promise((r) => server.httpServer.close(r));
if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log("screenshots written to", out, "; no external requests, no console errors");
