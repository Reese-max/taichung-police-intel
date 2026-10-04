import "./globals.css";
import "./v2.css";
import "./govintel.css";

import GovIntelFrame from "../components/GovIntelFrame.js";


export const metadata = {
  title: "GovIntel AI－公共資訊查詢與個人化追蹤平台",
  description: "查詢官方公開資訊，保存自己選擇的追蹤條件，核對公告變動、原文與資料涵蓋限制。",
};

export const viewport = {
  themeColor: "#0f1923",
};

export default function RootLayout({ children }) {
  return (
    <html lang="zh-Hant-TW">
      <body>
        <GovIntelFrame>{children}</GovIntelFrame>
      </body>
    </html>
  );
}
