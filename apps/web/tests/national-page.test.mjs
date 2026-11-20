import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = await readFile(new URL("../app/national/page.js", import.meta.url), "utf8");
const css = await readFile(new URL("../app/national/national.css", import.meta.url), "utf8");

test("national page keeps accessible responsive search and source links", () => {
  assert.match(page, /aria-pressed=/);
  assert.match(page, /role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(page, /開啟官方原頁（另開視窗）/);
  assert.match(page, /results\.filters\.q/);
  assert.match(page, /national-search-conditions/);
  assert.match(page, /了解更多/);
  assert.match(page, /只提供來源線索/);
  assert.match(page, /全國資料查詢服務尚未設定/);
  assert.match(page, /aria-label=\{`開啟官方原頁（另開視窗）/);
  assert.match(page, /官方連結無法驗證/);
  assert.match(page, /maxLength=\{1000\}/);
  assert.match(page, /max=\{draft\.to \|\| undefined\}/);
  assert.match(page, /min=\{draft\.from \|\| undefined\}/);
  assert.doesNotMatch(page + css, /linear-gradient|emoji/);
  assert.match(css, /@media \(min-width: 768px\)/);
  assert.match(css, /prefers-reduced-motion: reduce/);
});

test("accent is limited to search action and focus visibility", () => {
  const selectors = css.split("}").filter(rule => rule.includes("--accent")).map(rule => rule.slice(0, rule.indexOf("{")).trim());
  assert.deepEqual(selectors, [
    ".national-page .national-search",
    ".national-page :focus-visible",
  ]);
});

test("desktop hides the mobile type select and date disclosure", () => {
  const desktop = css.match(/@media \(min-width: 768px\) \{([\s\S]*?)\n\}/)?.[1] || "";
  assert.match(desktop, /\.national-kind-select select[^}]*display:\s*none/);
  assert.match(desktop, /\.national-date-toggle\s*\{\s*display:\s*none/);
});

test("clear appears once per viewport with mobile and desktop placement", () => {
  assert.equal((page.match(/>清除<\/button>/g) || []).length, 2);
  assert.match(page, /className="national-clear"[^>]*>清除<\/button>/);
  assert.match(page, /className="national-clear-mobile"[^>]*>清除<\/button>/);
  assert.match(css, /\.national-page \.national-clear\s*\{\s*display:\s*none/);
  assert.match(css, /\.national-secondary-fields > \.national-clear-mobile\s*\{\s*display:\s*none/);
  assert.match(css, /\.national-secondary-fields\s*\{[^}]*gap:\s*16px/);
  assert.match(css, /@media \(min-width: 768px\)[\s\S]*?\.national-page \.national-clear\s*\{\s*display:\s*inline-flex/);
});
