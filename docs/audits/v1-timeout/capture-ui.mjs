/**
 * Screenshot what the product actually tells a user about the reproduction
 * closure, and print the headline text it rendered.
 *
 *   node docs/audits/v1-timeout/capture-ui.mjs <label>
 *
 * Expects the reproduction API on 8010 (start-api.sh) and the web app on 5174.
 */
import { fileURLToPath } from 'node:url';

import { chromium } from '@playwright/test';

const label = process.argv[2] ?? 'capture';
const base = process.env.NZCL_BASE_URL ?? 'http://127.0.0.1:5174';
const url = `${base}/?link=EB2&snapshot=v1-timeout-repro&metric=distance` +
  `&vehicle=car&scope=physical&direction=forward`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(url);
await page.waitForSelector('.insp-head h2', { timeout: 60_000 });
await page.waitForSelector('.headline, .hero', { timeout: 60_000 });
await page.waitForTimeout(2500);

const panel = page.locator('.inspector, aside, .insp').first();
const dir = fileURLToPath(new URL('.', import.meta.url));
await page.screenshot({ path: `${dir}/ui-${label}-full.png`, fullPage: false });
await panel.screenshot({ path: `${dir}/ui-${label}-panel.png` }).catch(() => {});

const text = await page.locator('.headline, .hero').first().innerText();
const status = await page.locator('.status-pill').first().innerText();
console.log(`--- ${label} ---`);
console.log('status pill :', JSON.stringify(status));
console.log('headline    :', JSON.stringify(text));
await browser.close();
