import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { UserProvider } from "@/lib/user";
import { Header } from "@/components/Header";

export const metadata: Metadata = {
  title: "Internal Brain",
  description: "Company-wide answers, scoped to your permissions, fully auditable.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <UserProvider>
          <Header />
          <main className="page">{children}</main>
        </UserProvider>
      </body>
    </html>
  );
}
