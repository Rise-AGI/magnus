// front_end/src/app/layout.tsx
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
// 👇 1. 引入 AuthProvider
import { AuthProvider } from "@/context/auth-context";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Magnus Platform",
  description: "PKU-Plasma Infrastructure",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body className={inter.className}>
        {/* 👇 2. 包裹 AuthProvider，让全局可以使用登录态 */}
        <AuthProvider>
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}