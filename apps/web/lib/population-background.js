import { safeHttpsUrl, sha256Json } from "./public-query.js";

export const POPULATION_SAMPLE_SHA256 = "4f47d4c209d390ada155f35df317a03ea31852795551212f183c886341271ecd";
export const POPULATION_CSV_SHA256 = "95500e06310098d4c194a26e4aeba00e7142d5c9e441a62fe18f71250d65496b";

export async function validatePopulationSnapshot(data) {
  if (data?.schema_version !== 1 || data.period !== "112Y12M" || data.period_label !== "2023 年 12 月"
      || data.status !== "VERIFIED_HISTORICAL_BACKGROUND_SAMPLE" || data.production_active !== false
      || data.source_receipt?.raw_csv_sha256 !== POPULATION_CSV_SHA256 || !safeHttpsUrl(data.source_url)
      || !Array.isArray(data.districts) || data.districts.length !== 29) throw new Error("D1 固定期別或來源收據無法驗證；未顯示人口資料");
  if (await sha256Json(data) !== POPULATION_SAMPLE_SHA256) throw new Error("D1 歷史資料 hash 不符，已拒絕使用");
  const ids = new Set();
  for (const row of data.districts) {
    if (!/^66000\d{3}$/.test(row.district_code) || ids.has(row.district_code)
        || ![row.population, row.households, row.male_population, row.female_population].every((count) => Number.isSafeInteger(count) && count >= 0)
        || row.population !== row.male_population + row.female_population) throw new Error("D1 行政區代碼或人戶數無法驗證");
    ids.add(row.district_code);
  }
  for (const key of ["population", "households", "male_population", "female_population"]) {
    if (data.districts.reduce((sum, row) => sum + row[key], 0) !== data.totals[key]) throw new Error("D1 人戶數合計無法驗證");
  }
  return data;
}
