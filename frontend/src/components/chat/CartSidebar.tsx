import type { Cart } from "../../lib/api";

function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
}

export default function CartSidebar({ cart }: { cart: Cart }) {
  const total = cart.items.reduce((sum, item) => sum + item.qty * item.unit_price_paise, 0);

  return (
    <aside className="flex w-72 flex-shrink-0 flex-col border-l border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">Cart</h2>
      {cart.items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">Your cart is empty.</p>
      ) : (
        <ul className="mt-3 flex flex-1 flex-col gap-2 overflow-y-auto">
          {cart.items.map((item) => (
            <li key={item.sku} className="rounded-md border border-slate-100 p-2 text-sm">
              <div className="font-medium text-slate-800">{item.name}</div>
              <div className="flex justify-between text-xs text-slate-500">
                <span>x{item.qty}</span>
                <span>{formatPaise(item.unit_price_paise * item.qty)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex justify-between border-t border-slate-200 pt-3 text-sm font-semibold">
        <span>Total</span>
        <span>{formatPaise(total)}</span>
      </div>
    </aside>
  );
}
