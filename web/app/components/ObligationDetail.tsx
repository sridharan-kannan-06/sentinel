"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import {
  Button,
  InlineNotification,
  Tag,
  TextInput,
} from "@carbon/react";
import type { AuditChain } from "@/lib/api";
import { actionTone, stamp, statusTagType, tierTagType } from "@/lib/format";
import { Countdown } from "./Countdown";
import { reidentify } from "../actions";

function toneColour(action: string): string | undefined {
  const tone = actionTone(action);
  if (tone === "bad") return "var(--cds-text-error)";
  if (tone === "good") return "var(--cds-support-success)";
  return undefined;
}

export function ObligationDetail({ chain }: { chain: AuditChain }) {
  const [showEverything, setShowEverything] = useState(false);
  const { obligation, why_stuck: why } = chain;

  const nextPending = obligation.checkpoints?.find((c) => !c.fired) ?? null;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">
            <span className="token">{obligation.subject_token}</span>{" "}
            <span className="muted" style={{ fontWeight: 400 }}>
              {obligation.type.replace(/_/g, " ")}
            </span>
          </h1>
          <p className="page-subtitle numeric">
            {obligation.id} · version {obligation.version} · opened{" "}
            {stamp(obligation.created_at)}
          </p>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <Tag type={statusTagType(obligation.status)}>{obligation.status}</Tag>
          <Tag type={tierTagType(obligation.risk_tier)}>{obligation.risk_tier}</Tag>
          <Button
            kind={showEverything ? "secondary" : "primary"}
            size="md"
            onClick={() => setShowEverything((v) => !v)}
          >
            {showEverything
              ? "Hide the full chain"
              : "Show everything the agent did and why"}
          </Button>
        </div>
      </div>

      <div className="panel">
        <h2 className="panel-title">Why is this stuck</h2>
        <p className={why.blocked ? "answer" : "answer clear"}>{why.answer}</p>
        {why.chain.length > 1 && (
          <p className="muted" style={{ marginTop: "0.75rem", fontSize: 12 }}>
            Dependency path:{" "}
            {[obligation.id, ...why.chain].map((id, index) => (
              <span key={id}>
                {index > 0 && " → "}
                <Link href={`/obligations/${id}`} className="token">
                  {id}
                </Link>
              </span>
            ))}
          </p>
        )}
      </div>

      <div className="detail-grid">
        <div>
          <div className="panel">
            <h2 className="panel-title">Timeline</h2>
            <ul className="timeline">
              {(showEverything ? chain.history : chain.history.slice(-12)).map(
                (entry, index) => (
                  <li className="timeline-row" key={`${entry.at}-${index}`}>
                    <span className="timeline-when">{stamp(entry.at)}</span>
                    <span className="timeline-actor">{entry.actor}</span>
                    <span className="timeline-what">
                      <strong style={{ color: toneColour(entry.action) }}>
                        {entry.action}
                      </strong>
                      <br />
                      {entry.reason}
                      {showEverything && (entry.policy_decision_id || entry.trace_id) && (
                        <span
                          className="muted numeric"
                          style={{ display: "block", fontSize: 11, marginTop: 2 }}
                        >
                          {entry.policy_decision_id
                            ? `policy ${entry.policy_decision_id} `
                            : ""}
                          {entry.evidence_ref ? `evidence ${entry.evidence_ref} ` : ""}
                          {entry.trace_id ? `trace ${entry.trace_id}` : ""}
                        </span>
                      )}
                    </span>
                  </li>
                ),
              )}
            </ul>
            {!showEverything && chain.history.length > 12 && (
              <p className="muted" style={{ fontSize: 12, marginTop: "0.75rem" }}>
                Showing the last 12 of {chain.history.length} entries.
              </p>
            )}
          </div>

          {chain.evidence_checks.length > 0 && (
            <div className="panel">
              <h2 className="panel-title">Evidence checks</h2>
              {chain.evidence_checks.map((check, index) => (
                <div className="check-row" key={index}>
                  <Tag type={check.outcome === "passed" ? "green" : "red"} size="sm">
                    {check.outcome}
                  </Tag>
                  <span>{check.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <div className="panel">
            <h2 className="panel-title">Next wake-up</h2>
            {nextPending ? (
              <>
                <div style={{ fontSize: 28 }}>
                  <Countdown target={nextPending.at} />
                </div>
                <p className="muted numeric" style={{ margin: "0.25rem 0 0", fontSize: 12 }}>
                  {nextPending.kind} at {stamp(nextPending.at)}
                </p>
              </>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                No checkpoint pending.
              </p>
            )}
            <hr
              style={{
                border: 0,
                borderTop: "1px solid var(--cds-border-subtle-01)",
                margin: "1rem 0",
              }}
            />
            <div className="trust-item">
              <span className="trust-label">Deadline</span>
              <span className="trust-value">
                <Countdown target={obligation.deadline} /> ·{" "}
                {stamp(obligation.deadline)}
              </span>
            </div>
          </div>

          <div className="panel">
            <h2 className="panel-title">What closes this</h2>
            <p style={{ margin: 0 }}>{obligation.required_evidence}</p>
            <p className="muted" style={{ fontSize: 12, marginTop: "0.75rem" }}>
              The model may propose closure. Only an authoritative external fact
              recorded against this obligation can close it.
            </p>
          </div>

          <div className="panel">
            <h2 className="panel-title">What the model saw</h2>
            <p className="token" style={{ margin: 0 }}>
              {obligation.subject_token} · {obligation.owner_id}
            </p>
            <p className="muted" style={{ fontSize: 12, marginTop: "0.5rem" }}>
              Tokens, not names. Identifiers are replaced at the trust boundary
              before anything reaches the model, and the same person maps to the
              same token across weeks.
            </p>
            <Reidentify token={obligation.subject_token} />
          </div>

          <div className="panel">
            <h2 className="panel-title">Policy decisions</h2>
            {chain.policy_decisions.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                None recorded yet.
              </p>
            ) : (
              chain.policy_decisions.map((decision, index) => (
                <div className="check-row" key={index}>
                  <span className="numeric muted" style={{ fontSize: 11 }}>
                    {decision.policy_decision_id}
                  </span>
                  <span>
                    <strong style={{ color: toneColour(decision.action) }}>
                      {decision.action}
                    </strong>
                    <br />
                    <span className="muted">{decision.reason}</span>
                  </span>
                </div>
              ))
            )}
          </div>

          <div className="panel">
            <h2 className="panel-title">Counts</h2>
            {Object.entries(chain.counts).map(([label, value]) => (
              <div
                key={label}
                style={{ display: "flex", justifyContent: "space-between" }}
              >
                <span className="muted">{label.replace(/_/g, " ")}</span>
                <span className="numeric">{value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

/**
 * Reverse one token, with a reason, through the re-identification service.
 *
 * The reason is not decoration. reid records who asked, for which token, and why
 * before it answers, so a reason left blank is a log entry that explains nothing.
 */
function Reidentify({ token }: { token: string }) {
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<{
    ok: boolean;
    identity?: string;
    message: string;
  } | null>(null);
  const [pending, startTransition] = useTransition();

  return (
    <div style={{ marginTop: "1rem" }}>
      <TextInput
        id={`reid-${token}`}
        labelText="Reason for re-identification"
        placeholder="Why does this need a name attached?"
        size="sm"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      />
      <Button
        kind="tertiary"
        size="sm"
        disabled={pending || !reason.trim()}
        style={{ marginTop: "0.5rem" }}
        onClick={() =>
          startTransition(async () => {
            setResult(await reidentify(token, reason));
          })
        }
      >
        {pending ? "Resolving..." : `Reveal who ${token} is`}
      </Button>
      {result && (
        <InlineNotification
          kind={result.ok ? "info" : "error"}
          lowContrast
          hideCloseButton
          title={result.ok ? (result.identity ?? "resolved") : "Refused"}
          subtitle={result.message}
          style={{ marginTop: "0.75rem", maxWidth: "100%" }}
        />
      )}
    </div>
  );
}
