"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

// Compatibility entry only. replace avoids leaving a redirect entry in history;
// Next's router and Link preserve the configured basePath on static deployments.
export default function AskCompatibilityPage() {
  const router = useRouter();
  useEffect(() => { router.replace("/research/"); }, [router]);
  return <main className="govintel-sources" aria-labelledby="ask-redirect-title">
    <h1 id="ask-redirect-title">公開研究入口已整合</h1>
    <p role="status">正在前往公開資料研究…</p>
    <p><Link href="/research/" replace>前往公開資料研究</Link></p>
  </main>;
}
