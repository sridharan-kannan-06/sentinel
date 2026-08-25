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
      <HeaderName prefix="Sentinel">Continuity Board</HeaderName>
      <HeaderNavigation aria-label="Sentinel">
        <HeaderMenuItem href="/">Board</HeaderMenuItem>
        <HeaderMenuItem href="/approvals">Approvals</HeaderMenuItem>
      </HeaderNavigation>
    </Header>
  );
}
