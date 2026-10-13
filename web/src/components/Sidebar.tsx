"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { StoreConfigInfo } from "@/lib/types";

const NAV = [
  { href: "/", label: "Catalog", icon: "▦" },
  { href: "/import", label: "Import feed", icon: "⇪" },
  { href: "/export", label: "Export & push", icon: "⇲" },
  { href: "/evaluation", label: "Evaluation", icon: "◎" },
  { href: "/activity", label: "Audit log", icon: "≡" },
];

export function Sidebar() {
  const path = usePathname();
  const [config, setConfig] = useState<StoreConfigInfo | null>(null);
  useEffect(() => {
    api<StoreConfigInfo>("/api/config").then(setConfig).catch(() => setConfig(null));
  }, []);
  return (
    <aside className="w-[218px] shrink-0 bg-night">
      <div className="sticky top-0 flex h-screen flex-col px-3 py-5 text-[13px] text-white/80">
      <Link href="/" className="mb-6 flex items-center gap-2.5 px-2">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-[#3aa57d] to-[#1f6f54] text-[15px] font-bold text-white">S</span>
        <span>
          <span className="block text-[15px] font-semibold text-white">Shelfwise</span>
          <span className="block text-[11px] text-white/50">catalog copilot</span>
        </span>
      </Link>
      <nav className="flex flex-col gap-0.5">
        {NAV.map((item) => {
          const active = item.href === "/" ? path === "/" || path.startsWith("/products") : path.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 ${active ? "bg-night-soft text-white" : "hover:bg-white/5"}`}>
              <span className="w-4 text-center text-white/60">{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto space-y-2 rounded-lg bg-white/5 p-3 text-[11px] leading-relaxed text-white/60">
        <div className="font-semibold text-white/80">{config?.store ?? "Store"}</div>
        <div>
          Languages: {config?.languages.map((l) => l.toUpperCase()).join(" · ") ?? "-"}
        </div>
        {config?.listing_models?.length ? (
          <div className="break-words">
            Listings by: <span className="text-white/80">{config.listing_models.map((m) => m.split("/").pop()).join(", ")}</span>
          </div>
        ) : null}
        <div className="break-words">
          Re-enrich with: <span className="text-white/80">{config?.models.provider === "fake" ? "offline model" : config?.models.text?.replace("openrouter/", "") ?? "-"}</span>
        </div>
        <div>{config?.models.free_only === "true" ? "Free-only guard on" : "Paid models allowed"}</div>
      </div>
      </div>
    </aside>
  );
}
