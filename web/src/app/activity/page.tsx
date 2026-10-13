"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card, Page, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";

export default function ActivityPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  useEffect(() => {
    api<AuditEvent[]>("/api/events?limit=300").then(setEvents).catch(() => setEvents([]));
  }, []);
  return (
    <Page title="Audit log" subtitle="Every import, enrichment, edit, waiver, approval, export and push, with who did it.">
      <Card pad={false}>
        <table className="w-full text-[12px]">
          <thead className="text-left text-[10px] tracking-wider text-muted uppercase">
            <tr>
              <th className="px-4 py-2">When (UTC)</th>
              <th>Who</th>
              <th>Action</th>
              <th>Product</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id} className="border-t border-line align-top">
                <td className="mono px-4 py-1.5 text-muted">{e.at.replace("T", " ").slice(0, 19)}</td>
                <td>{e.actor}</td>
                <td>
                  <Pill tone={e.action === "approve" || e.action === "bulk_approve" ? "pine" : e.action === "edit" ? "amber" : e.action === "push" ? "plum" : "gray"}>{e.action}</Pill>
                </td>
                <td className="mono">{e.product_id ? <Link href={`/products/${e.product_id}`} className="text-pine underline">{e.product_id}</Link> : ""}</td>
                <td className="mono max-w-[620px] truncate py-1.5 text-muted" title={JSON.stringify(e.detail)}>
                  {JSON.stringify(e.detail)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </Page>
  );
}
