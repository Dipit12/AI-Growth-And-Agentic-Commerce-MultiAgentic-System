import { useCallback, useState } from "react";

import CartSidebar from "../components/chat/CartSidebar";
import type { DisplayMessage } from "../components/chat/MessageList";
import MessageList from "../components/chat/MessageList";
import MessageInput from "../components/chat/MessageInput";
import { sendChatMessage, type Cart } from "../lib/api";

const SESSION_STORAGE_KEY = "razorpay-buildathon-session-id";

export default function ChatRoute() {
  const [sessionId, setSessionId] = useState<string | null>(() =>
    typeof window !== "undefined" ? window.localStorage.getItem(SESSION_STORAGE_KEY) : null,
  );
  const [messages, setMessages] = useState<DisplayMessage[]>([
    {
      role: "assistant",
      content: "Hi! I can help you find products, manage your cart, and check out. What are you looking for?",
    },
  ]);
  const [cart, setCart] = useState<Cart>({ items: [] });
  const [loading, setLoading] = useState(false);

  const handleSend = useCallback(
    async (message: string) => {
      setMessages((prev) => [...prev, { role: "user", content: message }]);
      setLoading(true);
      try {
        const result = await sendChatMessage(message, sessionId);
        if (!sessionId) {
          setSessionId(result.session_id);
          window.localStorage.setItem(SESSION_STORAGE_KEY, result.session_id);
        }
        setCart(result.cart);
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: result.response,
            traceId: result.trace_id,
            reasonTrace: result.reason_trace,
            products: [...result.discovery_results, ...result.recommendations],
            pendingApprovalId: result.pending_approval_id,
          },
        ]);
      } catch (error) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `Something went wrong: ${(error as Error).message}` },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [sessionId],
  );

  const handleAddToCart = useCallback(
    (sku: string) => {
      void handleSend(`add ${sku} to my cart`);
    },
    [handleSend],
  );

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <MessageList messages={messages} loading={loading} onAddToCart={handleAddToCart} />
        <MessageInput onSend={handleSend} disabled={loading} />
      </div>
      <CartSidebar cart={cart} />
    </div>
  );
}
