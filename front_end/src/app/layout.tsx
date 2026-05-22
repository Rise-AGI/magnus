// front_end/src/app/layout.tsx
import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { AuthProvider } from "@/context/auth-context";
import { LanguageProvider } from "@/context/language-context";
import { CLUSTER_CONFIG } from "@/lib/config";

// Inter Variable, latin subset。字体二进制提交在 src/app/fonts/，
// 取自 fontsource 镜像（与 Google Fonts CDN 同 OFL 二进制），避免 build 时
// 对 fonts.googleapis.com 的外网依赖。
const inter = localFont({
  src: [
    {
      path: "./fonts/Inter-Variable-Latin.woff2",
      style: "normal",
      weight: "100 900",
    },
    {
      path: "./fonts/Inter-Variable-Latin-Italic.woff2",
      style: "italic",
      weight: "100 900",
    },
  ],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Magnus Platform",
  description: `${CLUSTER_CONFIG.name} Infrastructure`,
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={inter.className}>
        <AuthProvider>
          <LanguageProvider>
            {children}
          </LanguageProvider>
        </AuthProvider>
      </body>
    </html>
  );
}