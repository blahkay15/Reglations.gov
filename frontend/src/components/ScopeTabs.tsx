export function ScopeTabs({
  scope,
  onChange,
}: {
  scope: "mine" | "team";
  onChange: (scope: "mine" | "team") => void;
}) {
  const tabs: { id: "mine" | "team"; label: string }[] = [
    { id: "mine", label: "My items" },
    { id: "team", label: "Team" },
  ];
  return (
    <div className="mt-5 inline-flex rounded-lg border border-line bg-card p-1 text-sm">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={`rounded-md px-3 py-1.5 ${scope === tab.id ? "bg-pine text-white" : "text-ink/70"}`}
          onClick={() => onChange(tab.id)}
          type="button"
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
