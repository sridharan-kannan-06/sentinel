"use client";

import Link from "next/link";
import { Tag } from "@carbon/react";
import type { BoardRow } from "@/lib/api";
import { statusTagType, tierTagType, elapsedSince } from "@/lib/format";
import { Countdown } from "./Countdown";

/**
 * Open obligations, most urgent first.
 *
 * Ordered by risk of breach rather than by deadline, which the engine computes
 * from status and from how much of each obligation's own window has been used.
 * A four hour obligation with ten minutes left outranks a two week one due
 * tomorrow, and sorting by deadline would put them the other way round.
 *
 * Written as a plain table rather than Carbon's DataTable because every column
 * here is either a live countdown or a link, and the sorting is already decided
 * by the engine.
 */
export function BoardTable({ rows }: { rows: BoardRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="panel">
        <p className="muted" style={{ margin: 0 }}>
          No open obligations. Nothing is currently owed.
        </p>
      </div>
    );
  }

  return (
    <div className="scroll-x">
      <table className="cds--data-table cds--data-table--sm">
        <thead>
          <tr>
            <th>Subject</th>
            <th>Type</th>
            <th>Owner</th>
            <th>Time to breach</th>
            <th>Open for</th>
            <th>Status</th>
            <th>Tier</th>
            <th>Root blocker</th>
            <th>Next wake</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                <Link href={`/obligations/${row.id}`} className="token">
                  {row.subject_token}
                </Link>
                <div className="muted numeric" style={{ fontSize: 11 }}>
                  {row.id}
                </div>
              </td>
              <td>{row.type.replace(/_/g, " ")}</td>
              <td>
                <div>{row.owner_role.replace(/_/g, " ")}</div>
                <div className="muted token" style={{ fontSize: 11 }}>
                  {row.owner_id}
                </div>
              </td>
              <td>
                <Countdown target={row.deadline} />
              </td>
              <td className="numeric muted">{elapsedSince(row.opened_at)}</td>
              <td>
                <Tag type={statusTagType(row.status)} size="sm">
                  {row.status}
                </Tag>
              </td>
              <td>
                <Tag type={tierTagType(row.risk_tier)} size="sm">
                  {row.risk_tier}
                </Tag>
              </td>
              <td>
                {row.root_blocker ? (
                  <Link href={`/obligations/${row.root_blocker}`} className="token">
                    {row.root_blocker}
                  </Link>
                ) : (
                  <span className="muted">none</span>
                )}
              </td>
              <td>
                {row.next_checkpoint ? (
                  <>
                    <Countdown target={row.next_checkpoint} />
                    <div className="muted" style={{ fontSize: 11 }}>
                      {row.next_checkpoint_kind}
                    </div>
                  </>
                ) : (
                  <span className="muted">none scheduled</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
