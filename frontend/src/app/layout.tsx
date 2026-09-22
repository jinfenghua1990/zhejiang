import type { Metadata } from "next";
import "./globals.css";
import AuthShell from "@/components/auth-shell";
import GlobalResizableTables from "@/components/global-resizable-tables";

export const metadata: Metadata = {
  title: "电商经营数据平台",
  description: "连接吉客云 + 1688 + 浙江农信的经营数据平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: `(function(){try{var m=localStorage.getItem("app-theme-mode");if(m!=="system"&&m!=="light"&&m!=="dark"){var l=localStorage.getItem("app-theme");m=l==="light"||l==="dark"?l:"system"}var r=m==="system"?(window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):m;var e=document.documentElement;e.classList.toggle("dark",r==="dark");e.dataset.themeMode=m;e.dataset.theme=r;e.style.colorScheme=r;}catch(e){}})();` }} />
      </head>
      <body>
        <GlobalResizableTables />
        <AuthShell>{children}</AuthShell>
      </body>
    </html>
  );
}
