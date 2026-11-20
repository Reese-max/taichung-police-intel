"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import V2DailyDashboard from "./V2DailyDashboard.js";

const destinations = [
  ["/", "概覽"],
  ["/public-query/", "公開查詢"],
  ["/national/", "全國資料"],
  ["/research/", "公開研究"],
  ["/tracking/", "我的追蹤"],
  ["/live/", "會議導航"],
  ["/sources/", "來源狀態"],
];

export default function GovIntelFrame({ children }) {
  const pathname = usePathname();
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
  const route = pathname?.startsWith(`${basePath}/`)
    ? pathname.slice(basePath.length)
    : pathname;
  const home = route === "/" || route === "";
  const normalized = route?.replace(/\/$/, "") || "/";
  return (
    <>
      <a className="govintel-skip" href="#govintel-content">跳至主要內容</a>
      <header className={`govintel-header${normalized === "/national" ? " national-header" : ""}`}>
        <Link href="/" className="govintel-brand">
          <strong>GovIntel AI</strong>
          <span>公共資訊查詢與個人化追蹤</span>
        </Link>
        <nav aria-label="主要導覽">
          {destinations.map(([href, label]) => (
            <Link key={href} href={href} aria-current={normalized === (href.replace(/\/$/, "") || "/") ? "page" : undefined}>
              {label}
            </Link>
          ))}
        </nav>
      </header>
      <div id="govintel-content" tabIndex={-1}>
        {home ? (
          <>
            <section className="govintel-intro" aria-labelledby="govintel-intro-title">
              <h1 id="govintel-intro-title">全國資料，一次查找</h1>
              <p>一次查找 5 個地方議會、立法院與政府採購資料，再回到官方來源核對原文與時間。追蹤條件保存在這台瀏覽器，重新開站後可看更新。</p>
              <div><Link href="/national/">搜尋全國資料</Link><Link href="/tracking/">管理我的追蹤</Link></div>
            </section>
            <V2DailyDashboard />
            <details className="legacy-system-details">
              <summary>歷史議會原型、資料來源與影音證據</summary>
              <div className="legacy-system-content">{children}</div>
            </details>
          </>
        ) : children}
      </div>
    </>
  );
}
