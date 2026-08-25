import { notFound } from "next/navigation";
import { getAudit, getTrust } from "@/lib/api";
import { ObligationDetail } from "@/app/components/ObligationDetail";
import { TrustStrip } from "@/app/components/TrustStrip";

export const dynamic = "force-dynamic";

// params is a promise in this version of Next and has to be awaited.
export default async function ObligationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let chain;
  try {
    chain = await getAudit(id);
  } catch {
    notFound();
  }
  const trust = await getTrust();

  return (
    <div className="page">
      <TrustStrip trust={trust} />
      <ObligationDetail chain={chain} />
    </div>
  );
}
