import type { Trust } from "@/lib/api";

/**
 * The configuration in force, on every page.
 *
 * These hashes come from the same loaders the enforcing code uses, so what is on
 * screen is what decided. The Evidence Gate state is shown loudly when it is off
 * because a board that looks normal while the gate is disabled would be actively
 * misleading.
 */
export function TrustStrip({ trust }: { trust: Trust }) {
  const gateOff = !trust.evidence.gate_enabled;
  return (
    <div
      className="trust-strip"
      style={
        gateOff
          ? { borderColor: "var(--cds-support-error)", borderWidth: "2px" }
          : undefined
      }
    >
      <div className="trust-item">
        <span className="trust-label">Policy</span>
        <span className="trust-value">
          v{trust.policy.version} · {trust.policy.hash}
        </span>
      </div>
      <div className="trust-item">
        <span className="trust-label">Evidence rules</span>
        <span className="trust-value">{trust.evidence.hash}</span>
      </div>
      <div className="trust-item">
        <span className="trust-label">Evidence gate</span>
        <span
          className="trust-value"
          style={{
            color: gateOff
              ? "var(--cds-text-error)"
              : "var(--cds-support-success)",
          }}
        >
          {gateOff ? "OFF — closing without proof" : "ON"}
        </span>
      </div>
      <div className="trust-item">
        <span className="trust-label">Escalation</span>
        <span className="trust-value">
          {trust.escalation.hash} · max{" "}
          {trust.escalation.max_notifications_per_recipient_per_24h}/24h
        </span>
      </div>
      <div className="trust-item">
        <span className="trust-label">Model</span>
        <span className="trust-value">
          {trust.model.name} @ {trust.model.location} · {trust.model.role}
        </span>
      </div>
      <div className="trust-item">
        <span className="trust-label">Build</span>
        <span className="trust-value">{trust.git_sha}</span>
      </div>
    </div>
  );
}
