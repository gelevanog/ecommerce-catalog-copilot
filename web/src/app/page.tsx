"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button, Page, Pill, STATUS_LABEL, StatusPill, Swatch } from "@/components/ui";
import { api, imageUrl, post } from "@/lib/api";
import type { Card, ProductsResponse } from "@/lib/types";

const TABS = ["all", "needs_review", "ready", "blocked", "approved", "exported", "imported"] as const;

export default function CatalogPage() {
  const [data, setData] = useState<ProductsResponse | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]>("all");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<ProductsResponse>("/api/products")
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const cards = useMemo(() => {
    let list = data?.products ?? [];
    if (tab !== "all") list = list.filter((c) => c.status === tab);
    if (q) {
      const needle = q.toLowerCase();
      list = list.filter((c) => `${c.title} ${c.title_raw} ${c.name} ${c.product_type}`.toLowerCase().includes(needle));
    }
    return list;
  }, [data, tab, q]);

  const counts = data?.counts ?? {};
  const ready = counts.ready ?? 0;

  async function bulkApprove() {
    setBusy("approve");
    try {
      const r = await post<{ approved: string[]; skipped: unknown[] }>("/api/approve/bulk", { batch_id: data?.batch_id, actor: "reviewer" });
      setMessage(`Approved ${r.approved.length} ready products; ${r.skipped.length} need a person first.`);
      load();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function enrich(ids: string[] | null) {
    setBusy("enrich");
    try {
      const job = await post<{ job_id: string; products: number }>("/api/enrich", { product_ids: ids, batch_id: data?.batch_id });
      setMessage(`Enriching ${job.products} products…`);
      const poll = async () => {
        const j = await api<{ status: string; done: number; total: number }>(`/api/jobs/${job.job_id}`);
        setMessage(`Enriching: ${j.done} of ${j.total}`);
        if (j.status === "running") {
          setTimeout(poll, 1500);
        } else {
          setMessage(j.status === "done" ? `Enriched ${j.total} products.` : `Enrichment ${j.status}.`);
          setBusy("");
          setSelected(new Set());
          load();
        }
      };
      setTimeout(poll, 800);
    } catch (e) {
      setMessage((e as Error).message);
      setBusy("");
    }
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <Page
      wide
      title="Catalog"
      subtitle={
        <>
          {data?.total ?? 0} products from two supplier feeds. Every listing is checked against the feed and the photo; a person approves it before
          it can be exported.
        </>
      }
      actions={
        <>
          <Button onClick={() => enrich(selected.size ? [...selected] : null)} disabled={!!busy}>
            {selected.size ? `Enrich ${selected.size} selected` : "Enrich all"}
          </Button>
          <Button kind="primary" onClick={bulkApprove} disabled={!!busy || ready === 0} title="Approves only products with no open errors and no feed/photo conflicts">
            Approve {ready} ready
          </Button>
          <Button href="/export">Export & push</Button>
        </>
      }
    >
      {error ? <div className="mb-4 rounded-lg border border-rose/30 bg-rose-soft px-4 py-3 text-sm text-rose">API not reachable: {error}</div> : null}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-6">
        {(["needs_review", "ready", "blocked", "approved", "exported", "imported"] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setTab(tab === s ? "all" : s)}
            className={`rounded-xl border px-4 py-2.5 text-left transition ${tab === s ? "border-pine bg-pine-soft" : "border-line bg-card hover:border-stone-300"}`}
          >
            <div className="text-[11px] font-semibold tracking-wider text-muted uppercase">{STATUS_LABEL[s]}</div>
            <div className="mt-0.5 text-[22px] font-semibold tracking-tight">{counts[s] ?? 0}</div>
          </button>
        ))}
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search titles, supplier titles, types"
          className="w-80 rounded-lg border border-line bg-white px-3 py-1.5 text-[13px] outline-none focus:border-pine"
        />
        <div className="flex flex-wrap gap-1">
          {TABS.map((t) => (
            <button key={t} type="button" onClick={() => setTab(t)} className={`rounded-md px-2.5 py-1 text-[12px] ${tab === t ? "bg-ink text-white" : "text-ink-soft hover:bg-stone-200/60"}`}>
              {t === "all" ? "All" : STATUS_LABEL[t]}
            </button>
          ))}
        </div>
        {selected.size ? (
          <button type="button" className="text-[12px] text-muted underline" onClick={() => setSelected(new Set())}>
            clear selection ({selected.size})
          </button>
        ) : null}
        {message ? <span className="ml-auto text-[12px] text-ink-soft">{message}</span> : null}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {cards.map((c) => (
          <ProductCard key={c.id} card={c} selected={selected.has(c.id)} onToggle={() => toggle(c.id)} />
        ))}
      </div>
      {data && cards.length === 0 ? <div className="py-16 text-center text-sm text-muted">No products here.</div> : null}
    </Page>
  );
}

function ProductCard({ card, selected, onToggle }: { card: Card; selected: boolean; onToggle: () => void }) {
  const color = typeof card.attributes.color === "string" ? card.attributes.color : null;
  const pattern = typeof card.attributes.pattern === "string" ? card.attributes.pattern : null;
  const count = typeof card.attributes.count === "number" ? card.attributes.count : null;
  return (
    <div className={`group relative overflow-hidden rounded-xl border bg-card transition ${selected ? "border-pine ring-2 ring-pine/30" : "border-line hover:border-stone-300"}`}>
      <button
        type="button"
        onClick={onToggle}
        aria-label="select"
        className={`absolute top-2 left-2 z-10 grid h-5 w-5 place-items-center rounded border text-[11px] ${selected ? "border-pine bg-pine text-white" : "border-stone-300 bg-white/90 text-transparent group-hover:text-stone-400"}`}
      >
        ✓
      </button>
      <div className="absolute top-2 right-2 z-10">
        <StatusPill status={card.status} />
      </div>
      <Link href={`/products/${card.id}`} className="block">
        <div className="aspect-square w-full overflow-hidden bg-stone-100">
          {card.image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={imageUrl(card.image)} alt={card.name} className="h-full w-full object-cover transition group-hover:scale-[1.02]" loading="lazy" />
          ) : null}
        </div>
        <div className="space-y-1.5 p-3">
          <div className="line-clamp-2 min-h-[36px] text-[13px] leading-snug font-medium text-ink">{card.title || card.name}</div>
          <div className="truncate text-[11px] text-muted" title={card.title_raw}>
            feed: <span className="mono">{card.title_raw}</span>
          </div>
          <div className="flex flex-wrap items-center gap-1">
            {color ? (
              <Pill>
                <Swatch color={color} size={9} />
                {color}
              </Pill>
            ) : null}
            {pattern && pattern !== "solid" ? <Pill>{pattern}</Pill> : null}
            {count && count > 1 ? <Pill>×{count}</Pill> : null}
            {card.variants > 1 ? <Pill tone="sky">{card.variants} variants</Pill> : null}
          </div>
          <div className="flex flex-wrap items-center gap-1 pt-0.5">
            {card.errors ? <Pill tone="rose">{card.errors} error{card.errors > 1 ? "s" : ""}</Pill> : null}
            {card.conflicts.map((a) => (
              <Pill key={a} tone="amber" title="The feed and the photo disagree">
                {a}: feed ≠ photo
              </Pill>
            ))}
            {card.warnings && !card.errors ? <Pill tone="gray">{card.warnings} warning{card.warnings > 1 ? "s" : ""}</Pill> : null}
            {card.languages.length ? <span className="ml-auto text-[10px] text-muted">{card.languages.map((l) => l.toUpperCase()).join(" ")}</span> : null}
          </div>
        </div>
      </Link>
    </div>
  );
}
