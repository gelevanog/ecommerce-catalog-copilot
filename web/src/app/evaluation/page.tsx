"use client";

import { useEffect, useState } from "react";

import { Card, Page, Pill, Stat } from "@/components/ui";
import { api } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Summary = Record<string, any>;

const pct = (v: unknown) => (typeof v === "number" ? `${v.toFixed(1)}%` : "–");

export default function EvaluationPage() {
  const [s, setS] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Summary>("/api/evaluation").then(setS).catch((e: Error) => setError(e.message));
  }, []);
  if (!s) return <Page title="Evaluation">{error ? <div className="text-sm text-muted">{error}</div> : "Loading…"}</Page>;
  const main = s.main ?? {};
  const attrs = main.attributes ?? {};
  const listings = main.listings ?? {};
  const claims = listings.claims ?? {};
  const imp = main.import ?? {};
  const tp = main.throughput ?? {};
  const alt = s.vision_alt?.attributes ?? {};
  const offline = s.offline?.attributes ?? {};
  const judge = s.judge ?? {};
  const back = s.backtranslation?.by_language ?? {};
  const cost = s.cost_per_1000_products ?? {};

  return (
    <Page
      wide
      title="Evaluation"
      subtitle={
        <>
          Measured on {s.measured_on} on the {imp.products_matched} synthetic products in <span className="mono">data/demo</span> with free OpenRouter models
          ({main.provider?.vision?.replace("openrouter/", "")} for photos, {main.provider?.text?.replace("openrouter/", "")} for copy and translation).
          Rendered from <span className="mono">results/summary.json</span>.
        </>
      }
    >
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label="Color from photo" value={pct(attrs.color?.image_accuracy)} tone="pine" hint={`lenient ${pct(attrs.color?.image_accuracy_lenient)}`} />
        <Stat label="Pack count from photo" value={pct(attrs.count?.image_accuracy)} tone="pine" />
        <Stat label="Feed errors caught" value={`${attrs.conflicts?.caught ?? "–"} / ${attrs.conflicts?.planted_feed_errors ?? "–"}`} hint={`${attrs.conflicts?.false_alarms ?? "–"} false alarms`} tone="amber" />
        <Stat label="Category exact" value={pct(listings.category?.exact_pct)} hint={`same branch ${pct(listings.category?.same_branch_pct)}`} tone="sky" />
        <Stat label="Fields with unsupported claims" value={pct(claims.hallucination_rate_pct)} hint={`first drafts: ${claims.products_with_unsupported_claim_first_draft ?? "–"} of ${listings.listings ?? "–"} products`} tone="rose" />
        <Stat label="SEO lengths OK" value={pct(listings.seo_length?.all_three?.pct)} hint="title, meta title, meta description" tone="plum" />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Card title="Attributes: photo vs feed vs decision" pad={false}>
          <table className="w-full text-[12px]">
            <thead className="text-left text-[10px] tracking-wider text-muted uppercase">
              <tr>
                <th className="px-4 py-2">Attribute</th>
                <th>Photo ({main.provider?.vision?.split("/").pop()})</th>
                <th>Photo (alt model)</th>
                <th>Photo (offline)</th>
                <th>Feed coverage / accuracy</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {["color", "pattern", "count", "material", "style"].map((a) => (
                <tr key={a} className="border-t border-line">
                  <td className="px-4 py-1.5 font-medium">{a}</td>
                  <td>{pct(attrs[a]?.image_accuracy)}</td>
                  <td>{pct(alt[a]?.image_accuracy)}</td>
                  <td>{pct(offline[a]?.image_accuracy)}</td>
                  <td>
                    {pct(attrs[a]?.feed_coverage)} / {pct(attrs[a]?.feed_accuracy)}
                  </td>
                  <td>{pct(attrs[a]?.merged_accuracy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Claims and the LLM judge">
          <div className="space-y-1.5 text-[13px]">
            <div>
              First drafts with an unsupported or banned claim: <b>{claims.products_with_unsupported_claim_first_draft}</b> of {listings.listings} products ({claims.unsupported_claims_first_draft} claims)
            </div>
            <div>
              After one corrective round: <b>{claims.products_with_unsupported_claim_final}</b> products, {pct(claims.hallucination_rate_pct)} of {claims.english_fields} English fields
            </div>
            <div className="text-ink-soft">
              Judge ({judge.judge_model?.replace("openrouter/", "")}) on {judge.answered} sampled listings: agrees with the checker on {judge.products_agree}, flags something
              the checker passed on {judge.products_judge_only}, misses what the checker caught on {judge.products_checker_only}.
            </div>
            <div className="flex flex-wrap gap-1 pt-1">
              {Object.entries(claims.by_code_final ?? {}).map(([k, v]) => (
                <Pill key={k} tone="rose">
                  {k}: {String(v)}
                </Pill>
              ))}
            </div>
          </div>
        </Card>
        <Card title="Translations" pad={false}>
          <table className="w-full text-[12px]">
            <thead className="text-left text-[10px] tracking-wider text-muted uppercase">
              <tr>
                <th className="px-4 py-2">Lang</th>
                <th>Right language</th>
                <th>Numbers kept</th>
                <th>Glossary</th>
                <th>Brands kept</th>
                <th>Length OK</th>
                <th>Back-translation chrF</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(main.translations ?? {}).map(([lang, t]: [string, any]) => (
                <tr key={lang} className="border-t border-line">
                  <td className="px-4 py-1.5 font-medium">{lang.toUpperCase()}</td>
                  <td>{pct(t.language_ok_pct)}</td>
                  <td>{pct(t.numbers_kept_pct)}</td>
                  <td>{pct(t.glossary_pct)}</td>
                  <td>{pct(t.brand_kept_pct)}</td>
                  <td>{pct(t.length_ok_pct)}</td>
                  <td>{back[lang] ? `${back[lang].chrf_mean} (n=${back[lang].n})` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Import, throughput and cost">
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-[12px]">
            <div>Column mapping</div>
            <div>
              {imp.column_mapping?.correct} / {imp.column_mapping?.total}
            </div>
            <div>Duplicate rows merged</div>
            <div>
              {imp.duplicates?.found} / {imp.duplicates?.expected}
            </div>
            <div>Planted variant problems flagged</div>
            <div>
              {imp.variant_problems?.flagged} / {imp.variant_problems?.planted} ({imp.variant_problems?.false_flags} false)
            </div>
            <div>Model calls per product</div>
            <div>{tp.calls_per_product}</div>
            <div>Seconds per product (p50, one worker)</div>
            <div>{tp.seconds_per_product_p50}</div>
            <div>Products per hour ({tp.workers} workers)</div>
            <div>{tp.products_per_hour}</div>
            <div>Cost per 1,000 products, free models</div>
            <div>$0</div>
            {Object.entries(cost)
              .filter(([k]) => k.includes("/"))
              .map(([k, v]) => (
                <div key={k} className="contents">
                  <div className="text-muted">… at {k} list prices</div>
                  <div>${String(v)}</div>
                </div>
              ))}
          </div>
        </Card>
      </div>
    </Page>
  );
}
