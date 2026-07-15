import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  subtitle?: string;
  children: ReactNode;
  /** Dim contents while a refetch is in flight (no skeleton flash). */
  dimmed?: boolean;
  className?: string;
}

/** Standard dashboard card: chart surface, hairline ring, quiet header. */
export function Card({ title, subtitle, children, dimmed, className }: CardProps) {
  return (
    <section
      className={`rounded-lg border border-hairline bg-surface p-4 ${className ?? ""}`}
    >
      {title && (
        <header className="mb-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {subtitle && <p className="mt-0.5 text-xs text-ink-2">{subtitle}</p>}
        </header>
      )}
      <div className={dimmed ? "opacity-60 transition-opacity" : "transition-opacity"}>
        {children}
      </div>
    </section>
  );
}
