import { useState } from "react";
import type { FormEvent } from "react";

import type { MerchantConfig } from "../../lib/api";

function paiseToRupees(paise: number): string {
  return (paise / 100).toString();
}

export default function ConfigForm({
  config,
  onSave,
  saving,
}: {
  config: MerchantConfig;
  onSave: (update: Partial<Omit<MerchantConfig, "merchant_id">>) => void;
  saving: boolean;
}) {
  const [perSessionCap, setPerSessionCap] = useState(paiseToRupees(config.per_session_cap_paise));
  const [perTransactionCap, setPerTransactionCap] = useState(paiseToRupees(config.per_transaction_cap_paise));
  const [autoApproveThreshold, setAutoApproveThreshold] = useState(
    paiseToRupees(config.auto_approve_threshold_paise),
  );
  const [allowedMethods, setAllowedMethods] = useState(config.allowed_payment_methods.join(", "));
  const [blockedCategories, setBlockedCategories] = useState(config.blocked_categories.join(", "));

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    onSave({
      per_session_cap_paise: Math.round(parseFloat(perSessionCap || "0") * 100),
      per_transaction_cap_paise: Math.round(parseFloat(perTransactionCap || "0") * 100),
      auto_approve_threshold_paise: Math.round(parseFloat(autoApproveThreshold || "0") * 100),
      allowed_payment_methods: allowedMethods.split(",").map((s) => s.trim()).filter(Boolean),
      blocked_categories: blockedCategories.split(",").map((s) => s.trim()).filter(Boolean),
    });
  }

  const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );

  const inputClass =
    "w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500";

  return (
    <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-4 rounded-lg border border-slate-200 bg-white p-5">
      <Field label="Per-session cap (₹)">
        <input className={inputClass} value={perSessionCap} onChange={(e) => setPerSessionCap(e.target.value)} />
      </Field>
      <Field label="Per-transaction cap (₹)">
        <input
          className={inputClass}
          value={perTransactionCap}
          onChange={(e) => setPerTransactionCap(e.target.value)}
        />
      </Field>
      <Field label="Auto-approve threshold (₹)">
        <input
          className={inputClass}
          value={autoApproveThreshold}
          onChange={(e) => setAutoApproveThreshold(e.target.value)}
        />
      </Field>
      <Field label="Allowed payment methods (comma-separated)">
        <input className={inputClass} value={allowedMethods} onChange={(e) => setAllowedMethods(e.target.value)} />
      </Field>
      <Field label="Blocked categories (comma-separated)">
        <input
          className={inputClass}
          value={blockedCategories}
          onChange={(e) => setBlockedCategories(e.target.value)}
        />
      </Field>
      <div className="col-span-2 flex justify-end">
        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save policy"}
        </button>
      </div>
    </form>
  );
}
