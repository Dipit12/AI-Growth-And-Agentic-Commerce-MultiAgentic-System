import type { ProductResult } from "../../lib/api";

function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
}

export default function ProductCard({
  product,
  onAdd,
}: {
  product: ProductResult;
  onAdd: (sku: string) => void;
}) {
  return (
    <div className="flex w-56 flex-shrink-0 flex-col rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <div className="text-sm font-medium text-slate-900">{product.name}</div>
      <div className="mt-1 text-xs uppercase tracking-wide text-slate-400">{product.category}</div>
      <div className="mt-2 text-base font-semibold text-brand-700">{formatPaise(product.price_paise)}</div>
      {product.reason ? <p className="mt-1 text-xs italic text-slate-500">{product.reason}</p> : null}
      <button
        onClick={() => onAdd(product.sku)}
        className="mt-3 rounded-md bg-brand-600 px-2 py-1.5 text-xs font-medium text-white hover:bg-brand-700"
      >
        Add to cart
      </button>
    </div>
  );
}
