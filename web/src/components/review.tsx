"use client";

import { useState } from "react";

import { Pill, Swatch } from "@/components/ui";
import type { Decision, Fact, Issue } from "@/lib/types";

/** Word-level diff (LCS) between two texts, shown inline: removed words struck through, added ones highlighted. */
export function WordDiff({ before, after }: { before: string; after: string }) {
  const a = before.split(/(\s+)/);
  const b = after.split(/(\s+)/);
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { t: "same" | "add" | "del"; s: string }[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ t: "same", s: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ t: "del", s: a[i++] });
    else out.push({ t: "add", s: b[j++] });
  }
  while (i < n) out.push({ t: "del", s: a[i++] });
  while (j < m) out.push({ t: "add", s: b[j++] });
  return (
    <span className="leading-relaxed">
      {out.map((p, k) =>
        p.t === "same" ? (
          <span key={k}>{p.s}</span>
        ) : p.t === "add" ? (
          <ins key={k} className="rounded bg-pine-soft px-0.5 text-pine no-underline">
            {p.s}
          </ins>
        ) : (
          <del key={k} className="rounded bg-rose-soft px-0.5 text-rose/80">
            {p.s}
          </del>
        ),
      )}
    </span>
  );
}

const SOURCE_TONE: Record<string, "pine" | "sky" | "plum" | "amber" | "gray"> = { feed: "sky", image: "plum", editor: "pine", rule: "gray" };

export function EvidenceChips({ ids, facts, decisions }: { ids: string[]; facts: Fact[]; decisions: Decision[] }) {
  if (!ids.length) return <span className="text-[11px] text-muted">no facts stated</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {ids.map((id) => (
        <EvidenceChip key={id} id={id} facts={facts} decisions={decisions} />
      ))}
    </span>
  );
}

function EvidenceChip({ id, facts, decisions }: { id: string; facts: Fact[]; decisions: Decision[] }) {
  const [open, setOpen] = useState(false);
  let label = id;
  let tone: "pine" | "sky" | "plum" | "amber" | "gray" = "gray";
  let rows: { source: string; ref: string; quote: string }[] = [];
  let swatch: string | null = null;
  if (id.startsWith("attr:")) {
    const d = decisions.find((x) => x.attribute === id.slice(5));
    if (d) {
      label = `${d.attribute}: ${String(d.value)}`;
      tone = d.status === "agree" ? "pine" : d.status === "image_only" ? "plum" : d.status === "conflict" ? "amber" : "sky";
      rows = d.evidence;
      if (d.attribute === "color") swatch = String(d.value);
    }
  } else {
    const f = facts.find((x) => x.id === id);
    if (f) {
      label = `${f.key === "feature" || f.key === "care" ? "" : `${f.key}: `}${f.display}`;
      tone = SOURCE_TONE[f.evidence[0]?.source ?? "feed"] ?? "sky";
      rows = f.evidence;
    }
  }
  return (
    <span className="relative" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <Pill tone={tone}>
        {swatch ? <Swatch color={swatch} size={8} /> : null}
        <span className="max-w-[210px] truncate">{label}</span>
      </Pill>
      {open && rows.length ? (
        <span className="chip-pop block rounded-lg border border-line bg-white p-2.5 text-[11px] shadow-lg">
          {rows.map((r, k) => (
            <span key={k} className="block py-0.5">
              <span className={`mr-1 font-semibold uppercase ${r.source === "image" ? "text-plum" : r.source === "feed" ? "text-sky" : "text-pine"}`}>{r.source}</span>
              <span className="mono text-ink-soft">{r.ref}</span>
              {r.quote ? <span className="mt-0.5 block text-ink">“{r.quote}”</span> : null}
            </span>
          ))}
        </span>
      ) : null}
    </span>
  );
}

export function Counter({ n, min, max }: { n: number; min: number; max: number }) {
  const tone = n > max ? "text-rose" : n < min ? "text-amber" : "text-pine";
  return (
    <span className={`mono text-[11px] ${tone}`}>
      {n}/{max}
    </span>
  );
}

export function IssueLine({ issue, onWaive }: { issue: Issue; onWaive?: () => void }) {
  const tone = issue.severity === "error" ? "border-rose/40 bg-rose-soft/60" : issue.severity === "warning" ? "border-amber/30 bg-amber-soft/60" : "border-line bg-stone-50";
  return (
    <div className={`rounded-lg border px-2.5 py-1.5 text-[12px] ${tone} ${issue.waived ? "opacity-55" : ""}`}>
      <div className="flex min-w-0 items-center gap-1.5">
        <span className={`shrink-0 font-semibold ${issue.severity === "error" ? "text-rose" : issue.severity === "warning" ? "text-amber" : "text-muted"}`}>{issue.code}</span>
        {issue.field ? <span className="mono min-w-0 truncate text-[11px] text-muted" title={issue.field}>{issue.field}</span> : null}
        {onWaive && !issue.waived ? (
          <button type="button" onClick={onWaive} className="ml-auto shrink-0 text-[11px] text-muted underline hover:text-ink">
            accept
          </button>
        ) : null}
      </div>
      <div className="text-ink-soft">{issue.message}</div>
      {issue.waived ? <div className="text-[11px] text-muted">accepted by {issue.waived}</div> : null}
    </div>
  );
}
