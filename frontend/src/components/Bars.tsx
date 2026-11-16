type Item = { label: string; count: number };

export function Bars({ items, empty }: { items: Item[]; empty: string }) {
  const max = Math.max(1, ...items.map((item) => item.count));
  if (items.length === 0) {
    return <p className="text-sm text-ink/60">{empty}</p>;
  }
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.label} className="grid grid-cols-[7rem_1fr_2rem] items-center gap-3 text-sm">
          <span className="truncate" title={item.label}>
            {item.label}
          </span>
          <div className="h-2.5 rounded-full bg-line">
            <div
              className="h-2.5 rounded-full bg-pine"
              style={{ width: `${Math.max(4, (item.count / max) * 100)}%` }}
            />
          </div>
          <span className="text-right tabular-nums">{item.count}</span>
        </div>
      ))}
    </div>
  );
}
