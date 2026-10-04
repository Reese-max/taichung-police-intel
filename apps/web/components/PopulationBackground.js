"use client";

import { useEffect, useState } from "react";
import { validatePopulationSnapshot } from "../lib/population-background.js";
import { safeHttpsUrl } from "../lib/public-query.js";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

export default function PopulationBackground() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [code, setCode] = useState("66000060");
  useEffect(() => {
    let cancelled = false;
    fetch(`${BASE_PATH}/data/population-112Y12M.json`, { cache: "no-store", signal: AbortSignal.timeout(12000) })
      .then((response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
      .then(validatePopulationSnapshot).then((value) => { if (!cancelled) setData(value); })
      .catch((reason) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; };
  }, []);
  const row = data?.districts.find((item) => item.district_code === code);
  return <section className="pq-population" aria-labelledby="pq-population-title">
    <h2 id="pq-population-title">D1 · 行政區人口／戶數背景查詢</h2>
    <p><strong>固定期別：民國 112 年 12 月（2023-12）。</strong>已核對歷史背景樣本；來源尚未升格為正式定期來源。</p>
    <p>不是目前人口、現場人潮、活動出席人數或受影響人口；不能推論機關管轄、可用警力或現場狀態。</p>
    {error ? <p role="alert">UNKNOWN · {error}</p> : !data ? <p role="status">正在核對固定期別資料……</p> : <>
      <div className="pq-field"><label htmlFor="pq-population-district">臺中市行政區（29 區）</label><select id="pq-population-district" value={code} onChange={(event) => setCode(event.target.value)}>{data.districts.map((item) => <option key={item.district_code} value={item.district_code}>{item.district_name} · {item.district_code}</option>)}</select></div>
      {row && <dl className="pq-event-facts"><div><dt>行政區</dt><dd>{row.district_name}</dd></div><div><dt>行政區代碼</dt><dd>{row.district_code}</dd></div><div><dt>統計期別</dt><dd>112 年 12 月（2023-12）</dd></div><div><dt>人口</dt><dd>{row.population.toLocaleString("zh-TW")} 人</dd></div><div><dt>戶數</dt><dd>{row.households.toLocaleString("zh-TW")} 戶</dd></div></dl>}
      <p>{data.license.attribution}</p><p>{data.license.transformation_notice}</p>
      <p><a href={safeHttpsUrl(data.source_url)} target="_blank" rel="noreferrer">官方固定期別查詢頁</a> · <a href={safeHttpsUrl(data.license.terms_url)} target="_blank" rel="noreferrer">政府資料開放授權條款</a></p>
      <details className="pq-receipt"><summary>固定期別來源與資料收據</summary><p>來源：{data.source_id} · {data.authority}</p><p>期別代碼：{data.period}</p><p>原始 CSV：{data.source_receipt.csv_filename}</p><p>原始 CSV SHA256：{data.source_receipt.raw_csv_sha256}</p><p>取得核對時間：{data.observed_at}</p><p>資料截止為 2023-12；取得時間不代表人口資料更新到今天。</p><a href={`${BASE_PATH}/data/population-112Y12M.json`}>下載已核對的公開資料與收據</a></details>
    </>}
  </section>;
}
