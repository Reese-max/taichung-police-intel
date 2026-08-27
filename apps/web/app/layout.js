import "./globals.css";
import "./v2.css";

import V2DailyDashboard from "../components/V2DailyDashboard.js";


export const metadata = {
  title: "臺中警政每日情資｜公開政策與議會監測",
  description: "以官方公開來源整理本期真正新增、修正、狀態與時程變更，協助警政政策及議會工作快速判讀與查證。",
};

export const viewport = {
  themeColor: "#0f1923",
};

export default function RootLayout({ children }) {
  return (
    <html lang="zh-Hant-TW">
      <body>
        <V2DailyDashboard />
        <details className="legacy-system-details">
          <summary>資料來源、歷史監測介面與影音證據</summary>
          <div className="legacy-system-content">{children}</div>
        </details>
      </body>
    </html>
  );
}
