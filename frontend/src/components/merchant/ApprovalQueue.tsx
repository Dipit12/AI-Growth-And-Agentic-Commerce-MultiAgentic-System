import type { PendingApproval } from "../../lib/api";

function formatPaise(value: unknown): string {
  const paise = typeof value === "number" ? value : 0;
  return `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
}

const STATUS_STYLES: Record<PendingApproval["status"], string> = {
  pending: "bg-amber-100 text-amber-700",
  approved: "bg-emerald-100 text-emerald-700",
  denied: "bg-red-100 text-red-700",
  timed_out: "bg-slate-200 text-slate-600",
};

export default function ApprovalQueue({
  approvals,
  onResolve,
  resolvingId,
}: {
  approvals: PendingApproval[];
  onResolve: (id: string, approve: boolean) => void;
  resolvingId: string | null;
}) {
  if (approvals.length === 0) {
    return <p className="text-sm text-slate-400">No approvals to show.</p>;
  }

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-3 py-2">Trace</th>
            <th className="px-3 py-2">Action</th>
            <th className="px-3 py-2">Amount</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Created</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {approvals.map((approval) => (
            <tr key={approval.id}>
              <td className="px-3 py-2">
                <a href={`/audit/${approval.trace_id}`} className="text-brand-600 hover:underline">
                  {approval.trace_id.slice(0, 8)}
                </a>
              </td>
              <td className="px-3 py-2">{approval.action_type}</td>
              <td className="px-3 py-2">{formatPaise(approval.payload?.amount_paise)}</td>
              <td className="px-3 py-2">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[approval.status]}`}>
                  {approval.status}
                </span>
              </td>
              <td className="px-3 py-2 text-xs text-slate-500">{new Date(approval.created_at).toLocaleTimeString()}</td>
              <td className="px-3 py-2 text-right">
                {approval.status === "pending" ? (
                  <div className="flex justify-end gap-2">
                    <button
                      onClick={() => onResolve(approval.id, true)}
                      disabled={resolvingId === approval.id}
                      className="rounded-md bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => onResolve(approval.id, false)}
                      disabled={resolvingId === approval.id}
                      className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
                    >
                      Deny
                    </button>
                  </div>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
