import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import { site } from "@/lib/site.config";

export const metadata: Metadata = {
  title: `${site.name}: ${site.hero.headline}`,
  description: site.description,
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3100"),
  openGraph: { title: site.name, description: site.description, type: "website" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const api = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
  return (
    <html lang="en">
      <body>
        <a className="skip" href="#main">Skip to content</a>
        {children}
        {api && <Script src={`${api}/api/v1/public/widget.js`} data-api={api} data-title={`Chat with ${site.name}`} strategy="lazyOnload" />}
      </body>
    </html>
  );
}
