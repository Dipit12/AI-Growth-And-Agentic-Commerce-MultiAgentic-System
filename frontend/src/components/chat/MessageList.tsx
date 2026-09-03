import { useEffect, useRef } from "react";

import type { ProductResult, ReasonTrace } from "../../lib/api";
import ApprovalBanner from "./ApprovalBanner";
import ProductCard from "./ProductCard";
import ReasonPill from "./ReasonPill";

export interface DisplayMessage {
  role: "user" | "assistant";
  content: string;
  traceId?: string;
  reasonTrace?: ReasonTrace | null;
  products?: ProductResult[];
  pendingApprovalId?: string | null;
}

export default function MessageList({
  messages,
  loading,
  onAddToCart,
}: {
  messages: DisplayMessage[];
  loading: boolean;
  onAddToCart: (sku: string) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  return (
    <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
      {messages.map((message, index) => (
        <div key={index} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
          <div className={`max-w-lg ${message.role === "user" ? "text-right" : "text-left"}`}>
            <div
              className={`inline-block rounded-2xl px-4 py-2 text-sm ${
                message.role === "user" ? "bg-brand-600 text-white" : "bg-white text-slate-800 shadow-sm"
              }`}
            >
              {message.content}
            </div>

            {message.role === "assistant" && message.reasonTrace ? (
              <ReasonPill trace={message.reasonTrace} />
            ) : null}

            {message.role === "assistant" && message.pendingApprovalId ? (
              <div className="mt-2">
                <ApprovalBanner approvalId={message.pendingApprovalId} />
              </div>
            ) : null}

            {message.role === "assistant" && message.products && message.products.length > 0 ? (
              <div className="mt-2 flex gap-3 overflow-x-auto pb-1">
                {message.products.map((product) => (
                  <ProductCard key={product.sku} product={product} onAdd={onAddToCart} />
                ))}
              </div>
            ) : null}

            {message.role === "assistant" && message.traceId ? (
              <a
                href={`/audit/${message.traceId}`}
                className="mt-1 block text-[11px] text-slate-400 hover:text-brand-600 hover:underline"
              >
                trace: {message.traceId.slice(0, 8)}
              </a>
            ) : null}
          </div>
        </div>
      ))}

      {loading ? (
        <div className="flex justify-start">
          <div className="inline-block animate-pulse rounded-2xl bg-white px-4 py-2 text-sm text-slate-400 shadow-sm">
            thinking…
          </div>
        </div>
      ) : null}

      <div ref={bottomRef} />
    </div>
  );
}
