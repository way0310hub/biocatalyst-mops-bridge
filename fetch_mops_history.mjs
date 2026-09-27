#!/usr/bin/env node
/**
 * Query MOPS history through the official rendered page.
 * This is used only for explicit MOPS_HISTORY_CODES backfills.
 */
import { chromium } from "playwright";
import fs from "node:fs";

const codes = (process.env.MOPS_HISTORY_CODES || "")
  .split(",")
  .map((x) => x.trim())
  .filter(Boolean);
const days = Number(process.env.MOPS_HISTORY_DAYS || "365");
const today = new Date();
const start = new Date(today);
start.setDate(start.getDate() - days);

function monthKeys(from, to) {
  const result = [];
  const cursor = new Date(from.getFullYear(), from.getMonth(), 1);
  const end = new Date(to.getFullYear(), to.getMonth(), 1);
  while (cursor <= end) {
    result.push({ year: cursor.getFullYear(), month: cursor.getMonth() + 1 });
    cursor.setMonth(cursor.getMonth() + 1);
  }
  return result;
}

function rocDate(raw) {
  const match = String(raw || "").match(/(\\d{3})[\\/](\\d{1,2})[\\/](\\d{1,2})/);
  if (!match) return "";
  return `${Number(match[1]) + 1911}-${String(Number(match[2])).padStart(2, "0")}-${String(Number(match[3])).padStart(2, "0")}`;
}

function clean(raw) {
  return String(raw || "").replace(/\\s+/g, " ").trim();
}

async function queryMonth(page, code, year, month) {
  await page.goto("https://mops.twse.com.tw/mops/#/web/t05st01", {
    waitUntil: "domcontentloaded",
    timeout: 60000,
  });
  await page.locator("#companyId").fill(code);
  await page.waitForTimeout(400);
  const companyButton = page.locator("button").filter({ hasText: new RegExp(`^\\s*${code}\\s`) }).first();
  if (await companyButton.count()) {
    await companyButton.click();
  }
  await page.locator("#year").fill(String(year - 1911));
  await page.locator("#month").selectOption(String(month));
  await page.locator("#searchBtn").click();
  await page.waitForTimeout(1200);

  return page.locator("#searchBlock table tbody tr").evaluateAll((rows) =>
    rows.map((row) => Array.from(row.querySelectorAll("td")).map((cell) => cell.innerText.trim()))
  );
}

async function main() {
  if (!codes.length) {
    fs.writeFileSync("public/mops_history.json", JSON.stringify({
      ok: true, fetchedAt: new Date().toISOString(), codes: [], notices: [],
      sourceStats: [], status: "未指定歷史公司"
    }, null, 2));
    return;
  }

  fs.mkdirSync("public", { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const notices = [];
  const sourceStats = [];
  const months = monthKeys(start, today);

  try {
    for (const code of codes) {
      let count = 0;
      for (const { year, month } of months) {
        const rows = await queryMonth(page, code, year, month);
        for (const cells of rows) {
          if (cells.length < 5 || !/^\\d{4}$/.test(clean(cells[0]))) continue;
          const date = rocDate(cells[2]);
          if (!date || date < start.toISOString().slice(0, 10) ||
              date > today.toISOString().slice(0, 10)) continue;
          notices.push({
            code: clean(cells[0]).padStart(4, "0"),
            company: clean(cells[1]),
            date,
            time: clean(cells[3]),
            title: clean(cells[4]),
            source: "MOPS 歷史重大訊息（官方網頁查詢）",
            url: `https://mops.twse.com.tw/mops/#/web/t146sb05?companyId=${clean(cells[0]).padStart(4, "0")}`
          });
          count += 1;
        }
      }
      sourceStats.push({ code, notices: count, ok: true });
    }
  } finally {
    await browser.close();
  }

  fs.writeFileSync("public/mops_history.json", JSON.stringify({
    ok: true,
    fetchedAt: new Date().toISOString(),
    from: start.toISOString().slice(0, 10),
    to: today.toISOString().slice(0, 10),
    codes,
    notices,
    sourceStats,
    status: "官方網頁查詢完成"
  }, null, 2));
}

main().catch((error) => {
  fs.mkdirSync("public", { recursive: true });
  fs.writeFileSync("public/mops_history.json", JSON.stringify({
    ok: false, fetchedAt: new Date().toISOString(), codes, notices: [],
    sourceStats: [], status: "官方網頁查詢失敗", error: String(error).slice(0, 300)
  }, null, 2));
  process.exitCode = 1;
});
