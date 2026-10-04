import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { validatePopulationSnapshot } from "../lib/population-background.js";

const data = JSON.parse(await readFile(new URL("../public/data/population-112Y12M.json", import.meta.url), "utf8"));
test("D1 fixed historical sample preserves official 112Y12M counts and district codes", async () => {
  await validatePopulationSnapshot(data);
  assert.equal(data.districts.length, 29);
  const xitun = data.districts.find((row) => row.district_code === "66000060");
  assert.deepEqual([xitun.population, xitun.households], [235441, 94971]);
  assert.equal(data.totals.population, 2845909);
  assert.equal(data.production_active, false);
});
test("D1 wrong period and count mutations are refused instead of replacing fixed historical data", async () => {
  const wrongPeriod = structuredClone(data); wrongPeriod.period = "114Y12M";
  await assert.rejects(validatePopulationSnapshot(wrongPeriod), /固定期別/);
  const changed = structuredClone(data); changed.districts[0].population += 1;
  await assert.rejects(validatePopulationSnapshot(changed), /hash/);
  const promoted = structuredClone(data); promoted.production_active = true;
  await assert.rejects(validatePopulationSnapshot(promoted), /來源收據/);
});
