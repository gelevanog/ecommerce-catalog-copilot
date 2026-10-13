"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { Counter, EvidenceChips, IssueLine, WordDiff } from "@/components/review";
import { Button, Card, Pill, StatusPill, Swatch } from "@/components/ui";
import { api, imageUrl, patch, post } from "@/lib/api";
import type { Decision, Issue, Listing, ProductDetail, ProductRecord, StoreConfigInfo, Translated } from "@/lib/types";

type Lang = "en" | string;

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [config, setConfig] = useState<StoreConfigInfo | null>(null);
  const [lang, setLang] = useState<Lang>("en");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<ProductDetail>(`/api/products/${id}`).then(setDetail).catch((e: Error) => setError(e.message));
  }, [id]);
  useEffect(() => {
    load();
    api<StoreConfigInfo>("/api/config").then(setConfig).catch(() => null);
  }, [load]);

  const record = detail?.record;

  async function act(fn: () => Promise<ProductRecord | unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!record) {
    return <div className="p-8 text-sm text-muted">{error || "Loading…"}</div>;
  }

  const listing = record.listing;
  const openErrors = record.issues.filter((i) => i.severity === "error" && !i.waived);
  const conflicts = record.decisions.filter((d) => d.status === "conflict");
  const canApprove = !!listing && openErrors.length === 0 && conflicts.length === 0 && record.status !== "approved" && record.status !== "exported";
  const attrs = record.image_attributes[0];

  return (
    <div className="mx-auto max-w-[1640px] px-6 py-5">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Link href="/" className="text-[13px] text-muted hover:text-ink">
          ← Catalog
        </Link>
        <h1 className="text-[22px] font-semibold tracking-tight">{listing?.title || detail.name}</h1>
        <StatusPill status={record.status} />
        <div className="ml-auto flex items-center gap-2">
          {error ? <span className="max-w-[420px] truncate text-[12px] text-rose" title={error}>{error}</span> : null}
          <Button onClick={() => act(() => post(`/api/products/${id}/enrich`))} disabled={busy}>
            Re-enrich
          </Button>
          <Button
            kind="primary"
            disabled={!canApprove || busy}
            onClick={() => act(() => post(`/api/products/${id}/approve`, { actor: "reviewer" }))}
            title={canApprove ? "Approve every field" : openErrors.length ? `${openErrors.length} open error(s)` : conflicts.length ? "Resolve the feed/photo conflicts first" : ""}
          >
            {record.status === "approved" || record.status === "exported" ? "Approved ✓" : "Approve listing"}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-[300px_minmax(0,1fr)_330px] gap-4">
        {/* left: photo and attributes */}
        <div className="space-y-4">
          <Card pad={false}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={imageUrl(record.product.images[0])} alt="" className="aspect-square w-full rounded-t-xl object-cover" />
            <div className="space-y-1.5 px-4 py-3 text-[12px]">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-ink-soft">Seen in the photo</span>
                <span className="mono text-[10px] text-muted" title={attrs?.model}>
                  {attrs?.model?.split("/").slice(-1)[0]}
                </span>
              </div>
              {attrs?.error ? <div className="text-rose">{attrs.error}</div> : null}
              <div className="flex flex-wrap gap-1">
                {attrs?.color ? (
                  <Pill tone="plum">
                    <Swatch color={attrs.color} size={8} /> {attrs.color}
                  </Pill>
                ) : null}
                {attrs?.secondary_color ? (
                  <Pill tone="plum">
                    <Swatch color={attrs.secondary_color} size={8} /> {attrs.secondary_color}
                  </Pill>
                ) : null}
                {attrs?.pattern ? <Pill tone="plum">{attrs.pattern}</Pill> : null}
                {attrs?.material ? <Pill tone="plum">{attrs.material}?</Pill> : null}
                {attrs?.count ? <Pill tone="plum">× {attrs.count}</Pill> : null}
                {attrs?.style ? <Pill tone="plum">{attrs.style}</Pill> : null}
              </div>
              <div className="text-[11px] text-muted">The vision model never sees the feed, so agreement is real evidence.</div>
            </div>
          </Card>
          <Card title="Feed vs photo" pad={false}>
            <table className="w-full text-[12px]">
              <thead className="text-left text-[10px] tracking-wider text-muted uppercase">
                <tr>
                  <th className="px-3 py-1.5 font-semibold">Attr.</th>
                  <th className="px-1 py-1.5 font-semibold">Feed</th>
                  <th className="px-1 py-1.5 font-semibold">Photo</th>
                  <th className="px-3 py-1.5 font-semibold">Decision</th>
                </tr>
              </thead>
              <tbody>
                {record.decisions.map((d) => (
                  <DecisionRow key={d.attribute} d={d} onPick={(v) => act(() => post(`/api/products/${id}/attributes`, { attribute: d.attribute, value: v }))} />
                ))}
              </tbody>
            </table>
          </Card>
          <Card title={`Variants (${record.product.variants.length})`} pad={false}>
            <div className="divide-y divide-line text-[12px]">
              {record.product.variants.map((v) => (
                <div key={v.sku} className="flex items-center gap-2 px-3 py-1.5">
                  <span className="mono text-[11px] text-ink-soft">{v.sku}</span>
                  <span>{Object.values(v.options).join(" / ") || "one size"}</span>
                  <span className="ml-auto font-medium">{v.price != null ? `$${v.price.toFixed(2)}` : "-"}</span>
                  <span className="mono text-[10px] text-muted" title={`from “${v.source_price}”`}>
                    r{v.source.row}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>

        {/* middle: the listing */}
        <div className="min-w-0 space-y-4">
          {listing ? (
            <ListingEditor record={record} listing={listing} lang={lang} setLang={setLang} config={config} onSave={(field, value) => act(() => patch(`/api/products/${id}/fields`, { field, value }))} onRevert={(field) => act(() => post(`/api/products/${id}/revert`, { field }))} busy={busy} />
          ) : (
            <Card>
              <div className="py-10 text-center text-sm text-muted">
                Not enriched yet.{" "}
                <button type="button" className="text-pine underline" onClick={() => act(() => post(`/api/products/${id}/enrich`))}>
                  Enrich now
                </button>
              </div>
            </Card>
          )}
          <Card title="Supplier data (before)">
            <div className="space-y-2 text-[12px]">
              <div>
                <span className="text-muted">Title in the feed: </span>
                <span className="mono">{record.product.title_raw}</span>
                <span className="ml-2 text-muted">({record.product.supplier_file})</span>
              </div>
              {record.product.notes_raw ? (
                <div>
                  <span className="text-muted">Supplier notes: </span>
                  {record.product.notes_raw}
                </div>
              ) : null}
              {record.product.merged_duplicates.length ? (
                <div className="text-muted">Merged duplicate rows: {record.product.merged_duplicates.map((m) => `${m.file} row ${m.row}`).join(", ")}</div>
              ) : null}
            </div>
          </Card>
        </div>

        {/* right: checks and evidence */}
        <div className="space-y-4">
          <Card title="Checks" aside={<span className="text-[11px] text-muted">{openErrors.length} errors · {record.issues.filter((i) => i.severity === "warning").length} warnings</span>}>
            <div className="space-y-1.5">
              {openErrors.length === 0 && conflicts.length === 0 ? (
                <div className="rounded-lg border border-pine/30 bg-pine-soft px-3 py-2 text-[12px] text-pine">
                  No open errors. Every stated spec traces to the feed or the photo.
                </div>
              ) : null}
              {sortIssues(record.issues)
                .filter((i) => i.severity !== "info" || i.code === "supplier_claim_ignored")
                .slice(0, 14)
                .map((i, k) => (
                  <IssueLine
                    key={`${i.code}-${i.field}-${k}`}
                    issue={i}
                    onWaive={
                      i.code.startsWith("unsupported_") || i.code === "banned_claim" || i.severity === "info"
                        ? undefined
                        : () => {
                            const reason = window.prompt(`Accept “${i.code}” on ${i.field || "product"}? Reason:`);
                            if (reason) act(() => post(`/api/products/${id}/waive`, { code: i.code, field: i.field, reason }));
                          }
                    }
                  />
                ))}
            </div>
          </Card>
          <Card title="Facts and evidence">
            <div className="space-y-1.5 text-[12px]">
              {record.product.facts.map((f) => (
                <div key={f.id} className="flex gap-2">
                  <span className="mono w-7 shrink-0 text-[11px] text-muted">{f.id}</span>
                  <div className="min-w-0">
                    <div>
                      <span className="text-muted">{f.key}:</span> {f.display}
                    </div>
                    <div className="mono truncate text-[10px] text-muted" title={f.evidence.map((e) => e.ref).join("; ")}>
                      {f.evidence[0]?.ref}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
          <Card title="Activity">
            <div className="space-y-1 text-[11px]">
              {detail.events.slice(0, 8).map((e) => (
                <div key={e.id} className="flex gap-2">
                  <span className="mono text-muted">{e.at.slice(11, 16)}</span>
                  <span className="font-medium">{e.action}</span>
                  <span className="truncate text-muted">{e.actor}</span>
                </div>
              ))}
              <div className="pt-1 text-muted">
                {record.stats.calls} model calls · {(record.stats.input_tokens + record.stats.output_tokens).toLocaleString()} tokens · {record.stats.seconds.toFixed(0)} s
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function sortIssues(issues: Issue[]): Issue[] {
  const rank = { error: 0, warning: 1, info: 2 } as const;
  return [...issues].sort((a, b) => rank[a.severity] - rank[b.severity] || Number(!!a.waived) - Number(!!b.waived));
}

function show(v: unknown): string {
  return v === null || v === undefined ? "–" : String(v);
}

function DecisionRow({ d, onPick }: { d: Decision; onPick: (value: unknown) => void }) {
  const tone = { agree: "pine", conflict: "amber", feed_only: "sky", image_only: "plum", missing: "gray" } as const;
  const label = { agree: "agree", conflict: "conflict", feed_only: "feed only", image_only: "photo only", missing: "missing" }[d.status];
  return (
    <tr className={`border-t border-line align-top ${d.status === "conflict" ? "bg-amber-soft/50" : ""}`}>
      <td className="px-3 py-1.5 font-medium">{d.attribute}</td>
      <td className="px-1 py-1.5">
        {d.attribute === "color" && d.feed_value ? <Swatch color={String(d.feed_value)} size={8} /> : null} {show(d.feed_value)}
      </td>
      <td className="px-1 py-1.5">
        {d.attribute === "color" && d.image_value ? <Swatch color={String(d.image_value)} size={8} /> : null} {show(d.image_value)}
      </td>
      <td className="px-3 py-1.5">
        <Pill tone={tone[d.status]} title={d.note}>
          {label}
        </Pill>
        {d.status === "conflict" ? (
          <div className="mt-1 flex gap-1">
            <button type="button" className="rounded border border-line bg-white px-1.5 text-[10px] hover:bg-stone-50" onClick={() => onPick(d.feed_value)}>
              use feed
            </button>
            <button type="button" className="rounded border border-line bg-white px-1.5 text-[10px] hover:bg-stone-50" onClick={() => onPick(d.image_value)}>
              use photo
            </button>
          </div>
        ) : null}
      </td>
    </tr>
  );
}

type FieldDef = { key: keyof Translated | "tags" | "alt_texts"; label: string; multiline?: boolean; limit?: [number, number]; list?: boolean; html?: boolean };

function ListingEditor({
  record,
  listing,
  lang,
  setLang,
  config,
  onSave,
  onRevert,
  busy,
}: {
  record: ProductRecord;
  listing: Listing;
  lang: Lang;
  setLang: (l: Lang) => void;
  config: StoreConfigInfo | null;
  onSave: (field: string, value: unknown) => void;
  onRevert: (field: string) => void;
  busy: boolean;
}) {
  const limits = config?.limits ?? {};
  const pair = (k: string, d: [number, number]): [number, number] => {
    const v = limits[k];
    return Array.isArray(v) && v.length >= 2 ? [v[0], v[1]] : d;
  };
  const fields: FieldDef[] = [
    { key: "title", label: "Title", limit: pair("title", [20, 70]) },
    { key: "body_html", label: "Description", multiline: true, html: true },
    { key: "bullets", label: "Bullet points", list: true },
    { key: "meta_title", label: "Meta title", limit: pair("meta_title", [25, 60]) },
    { key: "meta_description", label: "Meta description", multiline: true, limit: pair("meta_description", [70, 160]) },
    ...(lang === "en" ? ([{ key: "tags", label: "Tags", list: true }, { key: "alt_texts", label: "Image alt text", list: true }] as FieldDef[]) : []),
  ];
  const source: Translated & { tags?: string[]; alt_texts?: string[] } = lang === "en" ? listing : listing.translations[lang] ?? { title: "", body_html: "", bullets: [], meta_title: "", meta_description: "" };
  const generated = lang === "en" ? record.generated : record.generated?.translations[lang];
  const languages = ["en", ...Object.keys(listing.translations).sort()];
  const langIssues = (lang === "en" ? [] : record.issues.filter((i) => i.field.startsWith(`translations.${lang}`))) as Issue[];

  return (
    <Card
      title="Listing"
      aside={
        <div className="flex gap-1">
          {languages.map((l) => (
            <button key={l} type="button" onClick={() => setLang(l)} className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${lang === l ? "bg-ink text-white" : "text-ink-soft hover:bg-stone-100"}`}>
              {l.toUpperCase()}
            </button>
          ))}
        </div>
      }
    >
      <div className="space-y-4">
        {lang === "en" ? (
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <Pill tone="sky">{listing.product_type}</Pill>
            <span className="text-muted">Google category</span>
            <span className="mono text-[11px]">{listing.category_id}</span>
            <span className="text-ink-soft">{listing.category_path}</span>
          </div>
        ) : (
          <div className="space-y-1">
            {langIssues.length === 0 ? (
              <div className="text-[12px] text-pine">Language, numbers, brand names and glossary terms check out.</div>
            ) : (
              langIssues.map((i, k) => <IssueLine key={k} issue={i} />)
            )}
          </div>
        )}
        {fields.map((f) => {
          const fieldKey = lang === "en" ? f.key : `translations.${lang}.${f.key}`;
          const value = (source as Record<string, unknown>)[f.key];
          const before = generated ? (generated as Record<string, unknown>)[f.key] : undefined;
          const issues = record.issues.filter((i) => i.field === fieldKey || i.field.startsWith(`${fieldKey}[`));
          const provenanceIds = lang === "en" ? Object.entries(record.provenance).filter(([k]) => k === f.key || k.startsWith(`${f.key}[`)).flatMap(([, v]) => v) : [];
          return (
            <FieldBlock
              key={`${lang}-${f.key}`}
              def={f}
              fieldKey={fieldKey}
              value={value}
              before={before}
              edited={record.review[fieldKey]?.state === "edited"}
              approved={record.review[fieldKey]?.state === "approved"}
              issues={issues}
              evidence={lang === "en" ? <EvidenceChips ids={[...new Set(provenanceIds)]} facts={record.product.facts} decisions={record.decisions} /> : null}
              onSave={onSave}
              onRevert={onRevert}
              busy={busy}
            />
          );
        })}
      </div>
    </Card>
  );
}

function FieldBlock({
  def,
  fieldKey,
  value,
  before,
  edited,
  approved,
  issues,
  evidence,
  onSave,
  onRevert,
  busy,
}: {
  def: FieldDef;
  fieldKey: string;
  value: unknown;
  before: unknown;
  edited: boolean;
  approved: boolean;
  issues: Issue[];
  evidence: React.ReactNode;
  onSave: (field: string, value: unknown) => void;
  onRevert: (field: string) => void;
  busy: boolean;
}) {
  const text = Array.isArray(value) ? (value as string[]).join("\n") : String(value ?? "");
  const beforeText = Array.isArray(before) ? (before as string[]).join("\n") : String(before ?? "");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const errors = issues.filter((i) => i.severity === "error" && !i.waived);
  const hasDiff = edited && beforeText && beforeText !== text;
  const plainLength = useMemo(() => (def.html ? text.replace(/<[^>]+>/g, " ").split(/\s+/).filter(Boolean).length : text.length), [def.html, text]);

  return (
    <div className={`rounded-lg border px-3 py-2.5 ${errors.length ? "border-rose/40 bg-rose-soft/30" : "border-line"}`}>
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-[11px] font-semibold tracking-wider text-muted uppercase">{def.label}</span>
        {def.limit ? <Counter n={text.length} min={def.limit[0]} max={def.limit[1]} /> : null}
        {def.html ? <span className="mono text-[11px] text-muted">{plainLength} words</span> : null}
        {edited ? <Pill tone="amber">edited</Pill> : approved ? <Pill tone="pine">approved</Pill> : null}
        <span className="ml-auto flex gap-2 text-[11px]">
          {edited ? (
            <button type="button" className="text-muted underline hover:text-ink" onClick={() => onRevert(fieldKey)} disabled={busy}>
              revert
            </button>
          ) : null}
          {!editing ? (
            <button
              type="button"
              className="text-pine underline"
              onClick={() => {
                setDraft(text);
                setEditing(true);
              }}
            >
              edit
            </button>
          ) : null}
        </span>
      </div>
      {editing ? (
        <div className="space-y-2">
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={def.multiline || def.list ? 5 : 2} className="mono w-full rounded-md border border-line p-2 text-[12px] outline-none focus:border-pine" />
          <div className="flex gap-2">
            <Button
              kind="primary"
              disabled={busy}
              onClick={() => {
                onSave(fieldKey, def.list ? draft.split("\n").filter((x) => x.trim()) : draft);
                setEditing(false);
              }}
            >
              Save and re-check
            </Button>
            <Button kind="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : hasDiff ? (
        <div className="text-[13px]">
          <WordDiff before={beforeText} after={text} />
        </div>
      ) : def.html ? (
        <div className="prose-listing text-[13px] leading-relaxed text-ink" dangerouslySetInnerHTML={{ __html: sanitize(text) }} />
      ) : def.list ? (
        def.key === "tags" ? (
          <div className="flex flex-wrap gap-1">
            {(value as string[]).map((t) => (
              <Pill key={t}>{t}</Pill>
            ))}
          </div>
        ) : (
          <ul className="list-disc space-y-0.5 pl-5 text-[13px]">
            {(value as string[]).map((b, k) => (
              <li key={k} className={issues.some((i) => i.field === `${fieldKey}[${k}]` && i.severity === "error") ? "text-rose" : ""}>
                {b}
              </li>
            ))}
          </ul>
        )
      ) : (
        <div className="text-[13px] text-ink">{text}</div>
      )}
      {errors.length ? (
        <div className="mt-2 space-y-1">
          {errors.map((i, k) => (
            <div key={k} className="text-[12px] text-rose">
              ✕ {i.message}
            </div>
          ))}
        </div>
      ) : null}
      {evidence ? <div className="mt-2 flex items-start gap-2 text-[11px] text-muted">{evidence}</div> : null}
    </div>
  );
}

/** The API only stores HTML that passed the allowed-tags check; strip anything else again before rendering. */
function sanitize(html: string): string {
  return html.replace(/<(?!\/?(p|ul|ol|li|strong|em|b|i|br)\b)[^>]*>/gi, "").replace(/\son\w+="[^"]*"/gi, "");
}
