import "./globals.css";

export const metadata = {
  title: "議會證據抽屜｜臺中警政公共資訊監測",
  description: "可回到官方影音時間點的議會準備證據介面。",
};

export const viewport = {
  themeColor: "#0f1923",
};

export default function RootLayout({ children }) {
  return (
    <html lang="zh-Hant-TW">
      <body>{children}</body>
    </html>
  );
}
