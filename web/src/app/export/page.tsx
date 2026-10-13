"use client";

import { useCallback, useEffect, useState } from "react";

import { Button, Card, Page, Pill, Stat } from "@/components/ui";
import { API, api, post } from "@/lib/api";

type PreviewResponse = { approved: number; rows: number; csv_head: string; translation_rows: number };
type PushResponse = { target: string; ok: number; failed: number; results: { handle: string; ok: boolean; remote_id: string; variants: number; translations: number; errors: string[] }[] };

export default function ExportPage() {
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [push, setPush] = useState<PushResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<PreviewResponse>("/api/export/preview").then(setPreview).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function run(target: string) {
    setBusy(true);
    setError("");
    try {
      setPush(await post<PushResponse>(`/api/push/${target}`));
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page title="Export and push" subtitle="Only approved products leave Shelfwise. The stores below are local mocks of the Shopify Admin GraphQL API and the WooCommerce REST API: no real store, no account.">
      <div className="mb-4 grid grid-cols-3 gap-3">
        <Stat label="Approved products" value={preview?.approved ?? "–"} tone="pine" />
        <Stat label="Shopify CSV rows" value={preview?.rows ?? "–"} hint="one row per variant" />
        <Stat label="Translation rows" value={preview?.translation_rows ?? "–"} hint="DE · ES · FR" />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Card title="Shopify CSV">
          <p className="mb-3 text-[13px] text-ink-soft">Admin › Products › Import format: handle, title, HTML body, vendor, type, tags, variants with SKU, grams, price and barcode, image with alt text, SEO title and description, Google product category.</p>
          <div className="flex gap-2">
            <Button kind="primary" href={`${API}/api/export/shopify.csv`}>
              Download products CSV
            </Button>
            <Button href={`${API}/api/export/translations.csv`}>Translations CSV</Button>
          </div>
          {preview?.csv_head ? <pre className="mono mt-3 max-h-48 overflow-auto rounded-lg bg-stone-50 p-2 text-[10px] leading-relaxed text-ink-soft">{preview.csv_head}</pre> : null}
        </Card>
        <Card title="Push to a store (mock)">
          <p className="mb-3 text-[13px] text-ink-soft">
            Shopify: <span className="mono">productSet</span> then <span className="mono">translationsRegister</span> with content digests. WooCommerce: a variable product and a variations batch.
          </p>
          <div className="flex gap-2">
            <Button kind="primary" onClick={() => run("shopify")} disabled={busy || !preview?.approved}>
              Push to Shopify mock
            </Button>
            <Button onClick={() => run("woocommerce")} disabled={busy || !preview?.approved}>
              Push to WooCommerce mock
            </Button>
          </div>
          {error ? <div className="mt-2 text-[12px] text-rose">{error}</div> : null}
          {push ? (
            <div className="mt-3 space-y-1 text-[12px]">
              <div>
                {push.target}: <Pill tone="pine">{push.ok} ok</Pill> {push.failed ? <Pill tone="rose">{push.failed} failed</Pill> : null}
              </div>
              <div className="max-h-56 overflow-auto rounded-lg border border-line">
                {push.results.map((r) => (
                  <div key={r.handle} className="flex gap-2 border-b border-line px-2 py-1 last:border-0">
                    <span className={r.ok ? "text-pine" : "text-rose"}>{r.ok ? "✓" : "✕"}</span>
                    <span className="mono truncate">{r.handle}</span>
                    <span className="ml-auto text-muted">{r.remote_id}</span>
                    <span className="text-muted">{r.variants} var · {r.translations} tr</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </Card>
      </div>
    </Page>
  );
}
