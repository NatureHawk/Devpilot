import type { ReactNode } from "react";

/** Label + explanation on the left, control on the right. */
export function SettingsRow({
  label,
  description,
  control,
}: {
  label: string;
  description: ReactNode;
  control?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="max-w-xl min-w-0">
        <h3 className="text-ink text-sm font-medium">{label}</h3>
        <p className="text-ink-muted mt-1 text-xs leading-relaxed">{description}</p>
      </div>
      {control ? <div className="shrink-0">{control}</div> : null}
    </div>
  );
}

/** Right-aligned status text for a setting that has no interactive control. */
export function SettingsStatus({ children }: { children: ReactNode }) {
  return <span className="text-ink-faint text-xs whitespace-nowrap">{children}</span>;
}
