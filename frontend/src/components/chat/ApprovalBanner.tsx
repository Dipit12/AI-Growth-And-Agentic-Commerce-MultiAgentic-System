export default function ApprovalBanner({ approvalId }: { approvalId: string }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-500" />
      Waiting for merchant approval (id: <code className="text-xs">{approvalId.slice(0, 8)}</code>)…
    </div>
  );
}
