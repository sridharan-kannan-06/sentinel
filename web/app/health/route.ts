import { NextResponse } from "next/server";

// Named /health rather than /healthz: Google's edge intercepts /healthz on
// run.app hostnames and answers it before the request reaches the container.
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({
    service: "sentinel-web",
    engine_configured: Boolean(process.env.ENGINE_BASE_URL),
    reid_configured: Boolean(process.env.REID_BASE_URL),
  });
}
