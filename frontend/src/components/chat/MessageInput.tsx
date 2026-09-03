import { useState } from "react";
import type { FormEvent } from "react";

export default function MessageInput({
  onSend,
  disabled,
}: {
  onSend: (message: string) => void;
  disabled: boolean;
}) {
  const [value, setValue] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2 border-t border-slate-200 bg-white p-3">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Ask for a product, manage your cart, or checkout…"
        className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
        disabled={disabled}
      />
      <button
        type="submit"
        disabled={disabled}
        className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        Send
      </button>
    </form>
  );
}
