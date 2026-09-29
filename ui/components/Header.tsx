"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserSwitcher } from "./UserSwitcher";
import { HealthIndicator } from "./HealthIndicator";

const LINKS = [
  { href: "/", label: "Chat" },
  { href: "/presenter", label: "Presenter" },
  { href: "/compare", label: "Compare" },
  { href: "/audit", label: "Audit" },
  { href: "/admin", label: "Admin" },
];

/** Pages that pick identities per pane; the app-wide "Acting as" switcher does not apply there. */
const PER_PANE_IDENTITY = ["/presenter", "/compare"];

export function Header() {
  const pathname = usePathname();
  const perPane = PER_PANE_IDENTITY.some((p) => pathname.startsWith(p));
  return (
    <header className="app-header">
      <div className={`app-header-inner${perPane ? " per-pane" : ""}`}>
        <div className="brand">
          <Link href="/" className="brand-name">
            Internal Brain
          </Link>
          <span className="brand-tagline">Company-wide answers, scoped to your permissions, fully auditable.</span>
        </div>
        <nav className="nav" aria-label="Main">
          {LINKS.map((l) => {
            const active = l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
            return (
              <Link key={l.href} href={l.href} className={active ? "active" : undefined} aria-current={active ? "page" : undefined}>
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="header-right">
          {perPane ? <span className="switcher-label">identity chosen per pane</span> : <UserSwitcher />}
          <HealthIndicator />
        </div>
      </div>
    </header>
  );
}
