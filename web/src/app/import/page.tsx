"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, Card, Page, Pill } from "@/components/ui";
import { API, post } from "@/lib/api";
import type { Preview } from "@/lib/types";

const TARGETS = [
  "title", "sku", "gtin", "vendor", "color", "material", "size", "pack", "dimensions", "length", "width", "height",
  "weight", "capacity", "price", "inventory", "image", "category", "notes", "features", "care",
];

export default function ImportPage() {
  const router = useRouter();
  const [feeds, setFeeds] = useState<File[]>([]);
  const [images, setImages] = useState<File[]>([]);
  const [previews, setPreviews] = useState<Record<string, Preview>>({});
  const [overrides, setOverrides] = useState<Record<string, Record<string, string | null>>>({});
  const [vendors, setVendors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function pick(files: FileList | null) {
    const list = Array.from(files ?? []);
    setFeeds(list);
    const next: Record<string, Preview> = {};
    for (const f of list) {
      const form = new FormData();
      form.append("file", f);
      const response = await fetch(`${API}/api/import/preview`, { method: "POST", body: form });
      if (response.ok) next[f.name] = (await response.json()) as Preview;
    }
    setPreviews(next);
  }

  async function submit() {
    setBusy(true);
    const form = new FormData();
    feeds.forEach((f) => form.append("feeds", f));
    images.forEach((f) => form.append("images", f));
    form.append("vendors", JSON.stringify(vendors));
    form.append("overrides", JSON.stringify(overrides));
    form.append("name", feeds.map((f) => f.name).join(", "));
    try {
      const response = await fetch(`${API}/api/import`, { method: "POST", body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? response.statusText);
      setMessage(`Imported ${body.products} products (${body.duplicates_merged} duplicate rows merged).`);
      router.push("/");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function demo() {
    setBusy(true);
    const r = await post<{ products: number }>("/api/import/demo");
    setMessage(`Imported the demo feeds: ${r.products} products.`);
    setBusy(false);
    router.push("/");
  }

  return (
    <Page
      title="Import a supplier feed"
      subtitle="CSV or XLSX, any column names. Shelfwise finds the header row, proposes a mapping onto the store's fields (by name, else by the values), and you confirm it before anything is imported."
      actions={<Button onClick={demo} disabled={busy}>Load the demo feeds</Button>}
    >
      <div className="grid grid-cols-2 gap-4">
        <Card title="1 · Feeds">
          <input type="file" multiple accept=".csv,.xlsx,.tsv,.txt" onChange={(e) => pick(e.target.files)} className="text-[13px]" />
        </Card>
        <Card title="2 · Product photos (optional)">
          <input type="file" multiple accept="image/*" onChange={(e) => setImages(Array.from(e.target.files ?? []))} className="text-[13px]" />
          <div className="mt-1 text-[12px] text-muted">{images.length} images selected; file names must match the feed&apos;s image column.</div>
        </Card>
      </div>
      {Object.values(previews).map((p) => (
        <Card key={p.file} title={`3 · Column mapping: ${p.file}`} className="mt-4" aside={<span className="text-[12px] text-muted">header on row {p.header_row} · {p.rows} rows</span>}>
          <div className="mb-3 flex items-center gap-2 text-[13px]">
            <span className="text-muted">Brand for this feed (if it has no brand column):</span>
            <input className="rounded-md border border-line px-2 py-1" value={vendors[p.file] ?? ""} onChange={(e) => setVendors({ ...vendors, [p.file]: e.target.value })} placeholder="e.g. Northbay" />
          </div>
          <table className="w-full text-[12px]">
            <thead className="text-left text-[10px] tracking-wider text-muted uppercase">
              <tr>
                <th className="py-1">Supplier column</th>
                <th>Samples</th>
                <th>Store field</th>
                <th>How</th>
              </tr>
            </thead>
            <tbody>
              {p.mapping.map((m) => {
                const current = overrides[p.file]?.[m.column] !== undefined ? overrides[p.file][m.column] : m.target;
                return (
                  <tr key={m.column} className="border-t border-line">
                    <td className="mono py-1.5">{m.column}</td>
                    <td className="max-w-[360px] truncate text-muted">{m.samples.join(" · ")}</td>
                    <td>
                      <select
                        className="rounded border border-line bg-white px-1.5 py-0.5"
                        value={current ?? ""}
                        onChange={(e) => setOverrides({ ...overrides, [p.file]: { ...(overrides[p.file] ?? {}), [m.column]: e.target.value || null } })}
                      >
                        <option value="">(ignore)</option>
                        {TARGETS.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <Pill tone={m.method === "synonym" ? "pine" : m.method === "values" ? "sky" : "gray"}>{m.method}</Pill>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      ))}
      <div className="mt-4 flex items-center gap-3">
        <Button kind="primary" onClick={submit} disabled={busy || feeds.length === 0}>
          Import {feeds.length || ""} feed{feeds.length === 1 ? "" : "s"}
        </Button>
        <span className="text-[13px] text-ink-soft">{message}</span>
      </div>
    </Page>
  );
}
