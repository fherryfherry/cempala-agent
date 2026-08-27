"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronDownIcon, PlusIcon } from "lucide-react";
import type { Workspace } from "@/lib/api";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function WorkspaceSwitcher({
  workspaces,
  activeKey,
}: {
  workspaces: Workspace[];
  activeKey: string;
}) {
  const [open, setOpen] = useState(false);
  const active = workspaces.find((ws) => ws.key === activeKey);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-sm font-medium hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <span className="w-40 truncate">
              {active ? `${active.name} (${active.key})` : activeKey}
            </span>
            <ChevronDownIcon className="size-3.5 text-zinc-400" />
          </button>
        }
      />
      <PopoverContent align="start" className="w-64 p-1">
        <div className="flex flex-col">
          {workspaces.map((ws) => (
            <Link
              key={ws.id}
              href={`/w/${ws.key}/chat`}
              onClick={() => setOpen(false)}
              className={`flex items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                ws.key === activeKey ? "bg-zinc-100 font-medium dark:bg-zinc-800" : ""
              }`}
            >
              <span>{ws.name}</span>
              <span className="shrink-0 text-xs text-zinc-400">{ws.key}</span>
            </Link>
          ))}
        </div>
        <div className="mt-1 border-t border-black/5 pt-1 dark:border-white/5">
          <Link
            href="/onboarding"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-zinc-500 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800"
          >
            <PlusIcon className="size-3.5" />
            New workspace
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}
