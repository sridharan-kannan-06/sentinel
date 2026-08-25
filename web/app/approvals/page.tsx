import { getApprovals, getTrust } from "@/lib/api";
import { ApprovalQueue } from "@/app/components/ApprovalQueue";
import { TrustStrip } from "@/app/components/TrustStrip";

export const dynamic = "force-dynamic";

export default async function ApprovalsPage() {
  const [queue, trust] = await Promise.all([getApprovals(), getTrust()]);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Approval queue</h1>
          <p className="page-subtitle">
            {queue.count} tier two{" "}
            {queue.count === 1 ? "action" : "actions"} waiting on a person.
            Nothing here has been sent.
          </p>
        </div>
      </div>
      <TrustStrip trust={trust} />
      <ApprovalQueue approvals={queue.approvals} />
    </div>
  );
}
