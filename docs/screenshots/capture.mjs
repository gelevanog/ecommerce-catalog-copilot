// Screenshots of the dashboard with headless Chrome (puppeteer-core), 1600 px wide.
//
//   PUPPETEER_FROM=<dir with node_modules/puppeteer-core> HERO_ID=p_xxx node docs/screenshots/capture.mjs \
//     http://localhost:3000 http://localhost:8000 [shots...]
//
// Shots: hero (review screen of HERO_ID), catalog, translations (the same product, DE tab), import (column mapping of
// the Northbay feed), export (after a push to the Shopify mock), evaluation. Every page shows data from the running
// API, which was seeded with the listings of the real-model evaluation run (README > Screenshots).
import { createRequire } from "module";
import path from "path";

const require = createRequire(process.env.PUPPETEER_FROM ? path.join(process.env.PUPPETEER_FROM, "x.js") : import.meta.url);
const puppeteer = require("puppeteer-core");
const [base, apiBase, ...shots] = process.argv.slice(2);
const out = process.env.OUT_DIR || new URL(".", import.meta.url).pathname;
const repo = new URL("../..", import.meta.url).pathname;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox"] });

async function open(url, height = 1000) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height, deviceScaleFactor: 1 });
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  page.on("console", (msg) => {
    if (msg.type() === "error") console.log("page error:", msg.text());
  });
  await page.goto(url, { waitUntil: "networkidle0", timeout: 120000 });
  await sleep(900);
  return page;
}

async function clickText(page, selector, text) {
  await page.evaluate(
    (sel, t) => {
      const el = [...document.querySelectorAll(sel)].find((b) => b.innerText.trim() === t);
      el?.click();
    },
    selector,
    text,
  );
  await sleep(700);
}

for (const shot of shots) {
  if (shot === "hero" || shot === "translations") {
    const page = await open(`${base}/products/${process.env.HERO_ID}`, shot === "hero" ? 1000 : 1000);
    if (shot === "translations") await clickText(page, "button", process.env.LANG_TAB || "DE");
    if (shot === "hero" && process.env.HOVER_CHIP) {
      const chips = await page.$$("span.relative");
      const idx = Number(process.env.HOVER_CHIP);
      if (chips[idx]) {
        await chips[idx].hover();
        await sleep(500);
      }
    }
    await page.screenshot({ path: `${out}${shot}.png` });
  } else if (shot === "catalog") {
    const page = await open(`${base}/`, 1000);
    await page.screenshot({ path: `${out}catalog.png` });
  } else if (shot === "import") {
    const page = await open(`${base}/import`, 1000);
    const input = await page.$('input[type="file"][accept*=".csv"]');
    await input.uploadFile(path.join(repo, "data/demo/feeds/northbay_feed.csv"));
    await page.waitForFunction(() => document.body.innerText.includes("Column mapping"), { timeout: 30000 });
    await sleep(800);
    await page.screenshot({ path: `${out}import.png` });
  } else if (shot === "export") {
    const page = await open(`${base}/export`, 1000);
    await clickText(page, "button", "Push to Shopify mock");
    await page.waitForFunction(() => document.body.innerText.includes(" ok"), { timeout: 60000 });
    await sleep(800);
    await page.screenshot({ path: `${out}export.png` });
  } else if (shot === "evaluation") {
    const page = await open(`${base}/evaluation`, 1000);
    await page.screenshot({ path: `${out}evaluation.png` });
  }
  console.log(`${shot}.png`);
}
await browser.close();
void apiBase;
