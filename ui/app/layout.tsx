import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import "./workspace.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "Porter Forces AI | Board Decision Intelligence",
    template: "%s | Porter Forces AI",
  },
  description:
    "Evidence-led competitive intelligence and board decision support for financial-services leaders.",
  applicationName: "Porter Forces AI",
  keywords: [
    "Porter Five Forces",
    "board decision support",
    "financial services",
    "competitive intelligence",
    "AI strategy",
  ],
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
