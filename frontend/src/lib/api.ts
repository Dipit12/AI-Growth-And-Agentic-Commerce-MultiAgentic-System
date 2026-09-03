// Shared API client for the backend. Every UI surface (chat widget, merchant console, audit
// viewer) goes through these functions rather than calling fetch() directly.

export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export interface CartItem {
  sku: string;
  name: string;
  qty: number;
  unit_price_paise: number;
  category: string;
}

export interface Cart {
  items: CartItem[];
}

export interface ProductResult {
  sku: string;
  name: string;
  price_paise: number;
  category: string;
  stock: number;
  reason?: string;
  score?: number;
}

export interface ReasonTrace {
  summary: string;
  rules_matched: string[];
  session_spend_before: number;
  session_spend_after: number;
  approval_source: "auto" | "human" | "denied";
  idempotency_key: string;
}

export interface ChatResponse {
  session_id: string;
  trace_id: string;
  response: string;
  cart: Cart;
  pending_approval_id: string | null;
  reason_trace: ReasonTrace | null;
  discovery_results: ProductResult[];
  recommendations: ProductResult[];
}

export interface AuditEvent {
  id: string;
  trace_id: string;
  session_id: string;
  timestamp: string;
  actor: string;
  event_type: string;
  payload: Record<string, unknown>;
  reason: string | null;
}

export interface MerchantConfig {
  merchant_id: string;
  per_session_cap_paise: number;
  per_transaction_cap_paise: number;
  allowed_payment_methods: string[];
  blocked_categories: string[];
  auto_approve_threshold_paise: number;
}

export interface PendingApproval {
  id: string;
  trace_id: string;
  session_id: string;
  action_type: string;
  payload: Record<string, unknown>;
  status: "pending" | "approved" | "denied" | "timed_out";
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${body}`);
  }
  return response.json() as Promise<T>;
}

export function sendChatMessage(message: string, sessionId: string | null): Promise<ChatResponse> {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ message, session_id: sessionId }),
  });
}

export function getAuditTrail(traceId: string, limit = 100, offset = 0): Promise<AuditEvent[]> {
  return request<AuditEvent[]>(`/audit/${traceId}?limit=${limit}&offset=${offset}`);
}

export function getMerchantConfig(merchantId: string, merchantKey: string): Promise<MerchantConfig> {
  return request<MerchantConfig>(`/merchant/${merchantId}/config`, {
    headers: { "X-Merchant-Key": merchantKey },
  });
}

export function updateMerchantConfig(
  merchantId: string,
  merchantKey: string,
  update: Partial<Omit<MerchantConfig, "merchant_id">>,
): Promise<MerchantConfig> {
  return request<MerchantConfig>(`/merchant/${merchantId}/config`, {
    method: "PUT",
    headers: { "X-Merchant-Key": merchantKey },
    body: JSON.stringify(update),
  });
}

export function listApprovals(
  merchantId: string,
  merchantKey: string,
  status?: string,
): Promise<PendingApproval[]> {
  const query = status ? `?status=${status}` : "";
  return request<PendingApproval[]>(`/merchant/${merchantId}/approvals${query}`, {
    headers: { "X-Merchant-Key": merchantKey },
  });
}

export function resolveApproval(
  approvalId: string,
  merchantKey: string,
  approve: boolean,
): Promise<{ resolved: boolean }> {
  return request<{ resolved: boolean }>(`/merchant/approvals/${approvalId}/${approve ? "approve" : "deny"}`, {
    method: "POST",
    headers: { "X-Merchant-Key": merchantKey },
  });
}

export function connectMerchantWs(merchantId: string, onMessage: (data: Record<string, unknown>) => void): WebSocket {
  const wsUrl = API_URL.replace(/^http/, "ws");
  const socket = new WebSocket(`${wsUrl}/ws/merchant/${merchantId}`);
  socket.onmessage = (event) => {
    try {
      onMessage(JSON.parse(event.data));
    } catch {
      // ignore malformed frames
    }
  };
  return socket;
}
