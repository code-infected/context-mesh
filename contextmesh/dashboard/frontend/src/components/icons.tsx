/** Minimal inline SVG icon set (stroke = currentColor, 16px default). */

interface IconProps {
  size?: number;
  className?: string;
}

function base(size: number | undefined, className: string | undefined) {
  return {
    width: size ?? 16,
    height: size ?? 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className,
    "aria-hidden": true,
  };
}

export function GridIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}

export function BarsIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <line x1="4" y1="20" x2="4" y2="10" />
      <line x1="10" y1="20" x2="10" y2="4" />
      <line x1="16" y1="20" x2="16" y2="14" />
      <line x1="22" y1="20" x2="22" y2="8" />
    </svg>
  );
}

export function AlertTriangleIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

export function HistoryIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 7 12 12 15.5 14" />
    </svg>
  );
}

export function SunIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <circle cx="12" cy="12" r="4" />
      <line x1="12" y1="2" x2="12" y2="4" />
      <line x1="12" y1="20" x2="12" y2="22" />
      <line x1="4.93" y1="4.93" x2="6.34" y2="6.34" />
      <line x1="17.66" y1="17.66" x2="19.07" y2="19.07" />
      <line x1="2" y1="12" x2="4" y2="12" />
      <line x1="20" y1="12" x2="22" y2="12" />
      <line x1="4.93" y1="19.07" x2="6.34" y2="17.66" />
      <line x1="17.66" y1="6.34" x2="19.07" y2="4.93" />
    </svg>
  );
}

export function MoonIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

export function CheckIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export function MinusCircleIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <circle cx="12" cy="12" r="9" />
      <line x1="8" y1="12" x2="16" y2="12" />
    </svg>
  );
}

export function ArrowUpIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <line x1="12" y1="19" x2="12" y2="5" />
      <polyline points="5 12 12 5 19 12" />
    </svg>
  );
}

export function ArrowDownIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <polyline points="19 12 12 19 5 12" />
    </svg>
  );
}

export function ChevronRightIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

export function ZapIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  );
}

export function CopyIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

export function InboxIcon({ size, className }: IconProps) {
  return (
    <svg {...base(size, className)}>
      <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </svg>
  );
}
