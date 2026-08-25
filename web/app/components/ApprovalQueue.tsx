"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import {
  Button,
  InlineNotification,
  Tag,
  TextArea,
  TextInput,
} from "@carbon/react";
import type { ApprovalRequest } from "@/lib/api";
import { stamp, tierTagType } from "@/lib/format";
import { decideApproval } from "../actions";

/**
 * Tier two actions waiting on a person.
 *
 * The payload shown is the exact request that would be dispatched, not a
 * summary of it. Approving a description of an email is not approving the email.
 */
export function ApprovalQueue({ approvals }: { approvals: ApprovalRequest[] }) {
  if (approvals.length === 0) {
    return (
      <div className="panel">
        <p className="muted" style={{ margin: 0 }}>
          Nothing is waiting for approval. Actions that leave the hospital appear
          here before they are sent, never after.
        </p>
      </div>
    );
  }
  return (
    <>
      {approvals.map((approval) => (
        <ApprovalCard key={approval.id} approval={approval} />
      ))}
    </>
  );
}

function ApprovalCard({ approval }: { approval: ApprovalRequest }) {
  const [decidedBy, setDecidedBy] = useState("");
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(
    null,
  );
  const [pending, startTransition] = useTransition();

  const ready = decidedBy.trim().length > 0 && reason.trim().length > 0;

  const decide = (approved: boolean) =>
    startTransition(async () => {
      setResult(await decideApproval(approval.id, approved, decidedBy, reason));
    });

  return (
    <div className="panel">
      <div className="page-header" style={{ marginBottom: "1rem" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>
            <span className="numeric">{approval.id}</span>{" "}
            <span className="muted">
              {approval.agent.replace(/_/g, " ")} wants to{" "}
              {approval.action.replace(/_/g, " ")}
            </span>
          </h2>
          <p className="page-subtitle numeric">
            obligation{" "}
            <Link href={`/obligations/${approval.obligation_id}`}>
              {approval.obligation_id}
            </Link>{" "}
            · requested {stamp(approval.requested_at)} · policy{" "}
            {approval.policy_hash}
          </p>
        </div>
        <Tag type={tierTagType(approval.risk_tier)}>{approval.risk_tier}</Tag>
      </div>

      <h3 className="panel-title">Exactly what will be sent</h3>
      <pre className="payload">{approval.rendered_payload}</pre>

      <div style={{ marginTop: "1.25rem", display: "grid", gap: "0.75rem" }}>
        <TextInput
          id={`by-${approval.id}`}
          labelText="Decided by"
          placeholder="Your name, recorded in the ledger"
          size="sm"
          value={decidedBy}
          onChange={(event) => setDecidedBy(event.target.value)}
        />
        <TextArea
          id={`reason-${approval.id}`}
          labelText="Reason"
          placeholder="Why this is or is not acceptable to send"
          rows={2}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <Button
            kind="primary"
            size="md"
            disabled={!ready || pending}
            onClick={() => decide(true)}
          >
            {pending ? "Recording..." : "Approve and release"}
          </Button>
          <Button
            kind="danger--tertiary"
            size="md"
            disabled={!ready || pending}
            onClick={() => decide(false)}
          >
            Deny
          </Button>
        </div>
        {!ready && (
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            Both fields are required. A decision with no name and no reason is an
            audit trail that says only that somebody clicked.
          </p>
        )}
        {result && (
          <InlineNotification
            kind={result.ok ? "success" : "error"}
            lowContrast
            hideCloseButton
            title={result.ok ? "Recorded" : "Not recorded"}
            subtitle={result.message}
            style={{ maxWidth: "100%" }}
          />
        )}
      </div>
    </div>
  );
}
