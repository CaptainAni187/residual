import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Residual",
  description:
    "A reconciliation engine for payment settlements. It works out where the difference between money captured and money banked went, and shows the query behind every rupee.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500&display=swap"
        />
      </head>
      <body>
        <div className="mx-auto max-w-5xl px-5">
          <header className="masthead flex flex-wrap items-end gap-x-7 gap-y-2 pt-8 pb-2">
            <span className="brand">Residual</span>
            <span className="sans text-[12px]" style={{ color: "var(--ink-2)" }}>
              reconciliation engine for payment settlements
            </span>
            <nav className="ml-auto flex gap-5">
              <Link className="navlink" href="/">Workspace</Link>
              <Link className="navlink" href="/engine">Engine</Link>
              <a
                className="navlink"
                href="https://github.com/CaptainAni187/residual"
                target="_blank"
                rel="noreferrer"
              >
                Source
              </a>
            </nav>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
