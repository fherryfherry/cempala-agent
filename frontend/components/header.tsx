"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { listWorkspaces } from "@/lib/api";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function Header() {
  const params = useParams<{ key?: string }>();
  const activeKey = params?.key;
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: listWorkspaces,
    enabled: !!activeKey,
  });

  return (
    <header className="border-b border-black/[.08] dark:border-white/[.145]">
      <div className="mx-auto flex h-14 max-w-5xl items-center gap-4 px-6">
        <Link href="/" className="font-semibold tracking-tight">
          Multi-Agent Portal
        </Link>

        {activeKey && (
          <Select
            value={activeKey}
            onValueChange={(value) => {
              window.location.href = `/w/${value}/agents`;
            }}
          >
            <SelectTrigger size="sm">
              <SelectValue placeholder={activeKey} />
            </SelectTrigger>
            <SelectContent>
              {(workspaces.data ?? []).map((ws) => (
                <SelectItem key={ws.id} value={ws.key}>
                  {ws.name} ({ws.key})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
    </header>
  );
}
