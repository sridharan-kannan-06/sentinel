import type { Metadata } from "next";
import "./globals.scss";
import { AppHeader } from "./components/AppHeader";

export const metadata: Metadata = {
  title: "Sentinel Continuity Board",
  description:
    "Open obligations, ordered by risk of breach. Nothing stays open unnoticed.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" data-carbon-theme="g100">
      <body>
        <AppHeader />
        <main>{children}</main>
      </body>
    </html>
  );
}
