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
    // cds--g100 is the class Carbon's own token block is written under. The
    // :root block in globals.scss covers the page either way; this keeps any
    // Carbon component that reads the class from falling back to the light theme.
    <html lang="en" className="cds--g100">
      <body>
        <AppHeader />
        <main>{children}</main>
      </body>
    </html>
  );
}
