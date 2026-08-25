import { getBoard, getTrust } from "@/lib/api";
import { BoardTable } from "./components/BoardTable";
import { TrustStrip } from "./components/TrustStrip";
import { stamp } from "@/lib/format";

// Live operational state. Rendering this at build time would show a board that
// was true once.
export const dynamic = "force-dynamic";

export default async function BoardPage() {
  const [board, trust] = await Promise.all([getBoard(), getTrust()]);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Continuity Board</h1>
          <p className="page-subtitle">
            {board.count} open {board.count === 1 ? "obligation" : "obligations"},
            ordered by risk of breach. Read at {stamp(board.generated_at)}.
          </p>
        </div>
      </div>

      <TrustStrip trust={trust} />
      <BoardTable rows={board.obligations} />

      <p className="muted" style={{ marginTop: "1.5rem", fontSize: 12 }}>
        Every obligation here carries its own timer. A reconciliation sweep runs
        every fifteen minutes and re-arms any checkpoint whose timer was lost,
        because the obligation is the source of truth and the timer is not.
      </p>
    </div>
  );
}
