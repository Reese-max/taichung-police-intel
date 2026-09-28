import "./globals.css";
import "./v2.css";

import V2DailyDashboard from "../components/V2DailyDashboard.js";


export const metadata = {
  title: "GovIntel AI｜跨機關公共事件整合、異動辨識與交班支援平台",
  description: "整合官方公開來源，辨識公共事件異動、保留證據與缺口，支援警政政策判讀及交班查證。",
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
