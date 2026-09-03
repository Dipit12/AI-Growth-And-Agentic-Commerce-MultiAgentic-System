import { useCallback, useEffect, useState } from "react";

import ApprovalQueue from "../components/merchant/ApprovalQueue";
import ConfigForm from "../components/merchant/ConfigForm";
import {
  connectMerchantWs,
  getMerchantConfig,
  listApprovals,
  resolveApproval,
  updateMerchantConfig,
  type MerchantConfig,
  type PendingApproval,
} from "../lib/api";

const MERCHANT_ID = "demo";
const KEY_STORAGE = "razorpay-buildathon-merchant-key";

export default function MerchantRoute() {
  const [merchantKey, setMerchantKey] = useState(
    () => window.localStorage.getItem(KEY_STORAGE) || "demo-merchant-key",
  );
  const [config, setConfig] = useState<MerchantConfig | null>(null);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [configResult, approvalsResult] = await Promise.all([
        getMerchantConfig(MERCHANT_ID, merchantKey),
        listApprovals(MERCHANT_ID, merchantKey),
      ]);
      setConfig(configResult);
      setApprovals(approvalsResult);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [merchantKey]);

  useEffect(() => {
    window.localStorage.setItem(KEY_STORAGE, merchantKey);
    void refresh();
  }, [merchantKey, refresh]);

  useEffect(() => {
    const socket = connectMerchantWs(MERCHANT_ID, () => {
      void refresh();
    });
    return () => socket.close();
  }, [refresh]);

  async function handleSaveConfig(update: Partial<Omit<MerchantConfig, "merchant_id">>) {
    setSaving(true);
    try {
      const updated = await updateMerchantConfig(MERCHANT_ID, merchantKey, update);
      setConfig(updated);
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function handleResolve(approvalId: string, approve: boolean) {
    setResolvingId(approvalId);
    try {
      await resolveApproval(approvalId, merchantKey, approve);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setResolvingId(null);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8 overflow-y-auto p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-900">Merchant Console</h1>
        <label className="flex items-center gap-2 text-xs text-slate-500">
          X-Merchant-Key
          <input
            value={merchantKey}
            onChange={(e) => setMerchantKey(e.target.value)}
            className="rounded-md border border-slate-300 px-2 py-1 text-xs"
          />
        </label>
      </div>

      {error ? <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Policy</h2>
        {config ? <ConfigForm config={config} onSave={handleSaveConfig} saving={saving} /> : null}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Pending approvals</h2>
        <ApprovalQueue approvals={approvals} onResolve={handleResolve} resolvingId={resolvingId} />
      </section>
    </div>
  );
}
