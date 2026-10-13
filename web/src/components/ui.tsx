import type { ReactNode } from "react";

export function Page({ title, subtitle, actions, children, wide = false }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`mx-auto px-7 py-6 ${wide ? "max-w-[1640px]" : "max-w-[1320px]"}`}>
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[25px] font-semibold tracking-tight text-ink">{title}</h1>
          {subtitle ? <div className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

export function Card({ title, aside, children, className = "", pad = true }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`rounded-xl border border-line bg-card shadow-[0_1px_2px_rgba(23,32,28,0.04)] ${className}`}>
      {title ? (
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-[12px] font-semibold tracking-wider text-ink-soft uppercase">{title}</h2>
          {aside}
        </div>
      ) : null}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

const TONES = {
  pine: "bg-pine-soft text-pine",
  sky: "bg-sky-soft text-sky",
  amber: "bg-amber-soft text-amber",
  rose: "bg-rose-soft text-rose",
  plum: "bg-plum-soft text-plum",
  gray: "bg-stone-100 text-stone-600",
} as const;
export type Tone = keyof typeof TONES;

export function Pill({ tone = "gray", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export const STATUS_LABEL: Record<string, string> = {
  imported: "Imported",
  needs_review: "Needs review",
  ready: "Ready",
  approved: "Approved",
  exported: "Exported",
  blocked: "Blocked",
  enriched: "Enriched",
};
const STATUS_TONE: Record<string, Tone> = { imported: "gray", needs_review: "amber", ready: "sky", approved: "pine", exported: "plum", blocked: "rose" };

export function StatusPill({ status }: { status: string }) {
  return <Pill tone={STATUS_TONE[status] ?? "gray"}>{STATUS_LABEL[status] ?? status}</Pill>;
}

export function Button({
  children,
  onClick,
  kind = "secondary",
  disabled = false,
  title,
  href,
}: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "secondary" | "ghost" | "danger";
  disabled?: boolean;
  title?: string;
  href?: string;
}) {
  const styles = {
    primary: "bg-pine text-white hover:bg-[#185a44] border-pine",
    secondary: "bg-white text-ink hover:bg-stone-50 border-line",
    ghost: "bg-transparent text-ink-soft hover:bg-stone-100 border-transparent",
    danger: "bg-white text-rose hover:bg-rose-soft border-line",
  }[kind];
  const cls = `inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] font-medium transition disabled:cursor-not-allowed disabled:opacity-45 ${styles}`;
  if (href) {
    return (
      <a href={href} className={cls} title={title}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" className={cls} onClick={onClick} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  const color = tone ? { pine: "text-pine", sky: "text-sky", amber: "text-amber", rose: "text-rose", plum: "text-plum", gray: "text-ink" }[tone] : "text-ink";
  return (
    <div className="rounded-xl border border-line bg-card px-4 py-3">
      <div className="text-[11px] font-semibold tracking-wider text-muted uppercase">{label}</div>
      <div className={`mt-1 text-[22px] font-semibold tracking-tight ${color}`}>{value}</div>
      {hint ? <div className="mt-0.5 text-[12px] text-muted">{hint}</div> : null}
    </div>
  );
}

export const SWATCH: Record<string, string> = {
  black: "#202024",
  white: "#f2f1ec",
  gray: "#86898e",
  navy: "#1e2c5c",
  blue: "#2c60c4",
  "light blue": "#92bee4",
  teal: "#128084",
  green: "#2e8448",
  olive: "#707434",
  red: "#cc2a2a",
  burgundy: "#761a2e",
  pink: "#ec98b2",
  orange: "#ec7a2a",
  yellow: "#f2ce36",
  mustard: "#caa028",
  beige: "#decaa8",
  brown: "#7a5234",
  purple: "#703e98",
};

export function Swatch({ color, size = 12 }: { color: string; size?: number }) {
  return <span className="inline-block rounded-full border border-black/15 align-middle" style={{ width: size, height: size, background: SWATCH[color] ?? "#ccc" }} />;
}
