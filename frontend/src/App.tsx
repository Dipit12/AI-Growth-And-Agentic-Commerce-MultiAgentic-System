import { NavLink, Route, Routes } from "react-router-dom";

import AuditRoute from "./routes/Audit";
import ChatRoute from "./routes/Chat";
import MerchantRoute from "./routes/Merchant";

function NavTab({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
          isActive ? "bg-brand-600 text-white" : "text-slate-600 hover:bg-slate-200"
        }`
      }
    >
      {label}
    </NavLink>
  );
}

export default function App() {
  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <div className="flex items-center gap-2">
          <span className="text-lg font-semibold">Agentic Commerce</span>
          <span className="rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-700">
            Razorpay Buildathon
          </span>
        </div>
        <nav className="flex gap-2">
          <NavTab to="/" label="Shopper Chat" />
          <NavTab to="/merchant" label="Merchant Console" />
          <NavTab to="/audit" label="Audit Viewer" />
        </nav>
      </header>
      <main className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<ChatRoute />} />
          <Route path="/merchant" element={<MerchantRoute />} />
          <Route path="/audit" element={<AuditRoute />} />
          <Route path="/audit/:traceId" element={<AuditRoute />} />
        </Routes>
      </main>
    </div>
  );
}
