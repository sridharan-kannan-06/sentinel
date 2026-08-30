"use client";

import {
  Header,
  HeaderName,
  HeaderNavigation,
  HeaderMenuItem,
} from "@carbon/react";

export function AppHeader() {
  return (
    <Header aria-label="Sentinel">
      {/* The suffix is dropped on narrow screens rather than truncated to
          "Continuity Boar". The page heading underneath already says it. */}
      <HeaderName prefix="Sentinel">
        <span className="header-suffix">Continuity Board</span>
      </HeaderName>
      <HeaderNavigation aria-label="Sentinel">
        <HeaderMenuItem href="/">Board</HeaderMenuItem>
        <HeaderMenuItem href="/approvals">Approvals</HeaderMenuItem>
      </HeaderNavigation>
    </Header>
  );
}
