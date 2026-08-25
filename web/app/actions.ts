"use server";

import { revalidatePath } from "next/cache";

const ENGINE = (process.env.ENGINE_BASE_URL ?? "").replace(/\/$/, "");

export type DecisionResult = { ok: boolean; message: string };

/**
 * Approve or deny one parked tier two action.
 *
 * A reason is required by the engine and the form requires it too, so a decision
 * without one cannot be made by accident from either side. The engine is the
 * authority: this only reports what it said.
 */
export async function decideApproval(
  approvalId: string,
  approved: boolean,
  decidedBy: string,
  reason: string,
): Promise<DecisionResult> {
  if (!reason.trim()) {
    return { ok: false, message: "A reason is required to record a decision." };
  }
  if (!decidedBy.trim()) {
    return { ok: false, message: "Record who is making this decision." };
  }

  const path = approved ? "approve" : "deny";
  const response = await fetch(`${ENGINE}/approvals/${approvalId}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decided_by: decidedBy, reason }),
    cache: "no-store",
  });

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : `The engine returned ${response.status}.`;
    return { ok: false, message: detail };
  }

  revalidatePath("/approvals");
  revalidatePath(`/obligations/${body.obligation_id ?? ""}`);
  return {
    ok: true,
    message: `${approvalId} ${approved ? "approved" : "denied"} and recorded in the ledger.`,
  };
}

/**
 * Ask the re-identification service for the name behind a token.
 *
 * This is the only place in the board that can produce a name, it runs on the
 * server, and the reid service records every call with the caller and a reason
 * before it answers.
 */
export async function reidentify(
  token: string,
  reason: string,
): Promise<{ ok: boolean; identity?: string; message: string }> {
  const reidUrl = (process.env.REID_BASE_URL ?? "").replace(/\/$/, "");
  if (!reidUrl) {
    return { ok: false, message: "Re-identification is not configured." };
  }
  if (!reason.trim()) {
    return {
      ok: false,
      message: "A reason is required. Every re-identification is logged with one.",
    };
  }

  // reid is closed to the internet, so this call carries an identity token
  // minted for the board's own service account from the metadata server.
  let authorization = "";
  try {
    const tokenResponse = await fetch(
      `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${encodeURIComponent(reidUrl)}`,
      { headers: { "Metadata-Flavor": "Google" }, cache: "no-store" },
    );
    if (tokenResponse.ok) {
      authorization = `Bearer ${await tokenResponse.text()}`;
    }
  } catch {
    // Running outside Cloud Run. The call below will be refused, which is the
    // correct outcome rather than a silent fallback to an unauthenticated read.
  }

  const response = await fetch(`${reidUrl}/reidentify`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(authorization ? { Authorization: authorization } : {}),
      "X-Sentinel-Operator": "continuity-board",
    },
    body: JSON.stringify({ token, reason }),
    cache: "no-store",
  });

  if (!response.ok) {
    return {
      ok: false,
      message:
        response.status === 403 || response.status === 401
          ? "Refused. The board is not authorised to reverse this token."
          : `Re-identification failed with ${response.status}.`,
    };
  }
  const body = await response.json();
  return {
    ok: true,
    identity: body.identity,
    message: "This read has been recorded in the re-identification access log.",
  };
}
