"use client";

import { useQuery } from "@tanstack/react-query";
import { getHealth } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function GlobalSettingsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const mcp = health.data?.mcp;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <Card>
        <CardHeader>
          <CardTitle>MCP</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          {health.isLoading ? (
            <p className="text-zinc-500">Checking…</p>
          ) : health.isError ? (
            <p className="text-red-600">Backend unreachable.</p>
          ) : (
            <>
              <p>
                Status:{" "}
                <span className={mcp?.enabled ? "text-green-600" : "text-zinc-500"}>
                  {mcp?.enabled ? "enabled" : "disabled"}
                </span>
              </p>
              <p className="text-zinc-500">
                API base: <code className="font-mono">{mcp?.api_base}</code>
              </p>
              <p className="text-xs text-zinc-500">
                Every agent run gets a fresh, local MCP server (stdio, no network exposure)
                proxying these tools to the ticket API — this is global for the whole portal,
                not configurable per workspace.
              </p>
              {mcp?.enabled && mcp.tools.length > 0 && (
                <ul className="flex flex-col gap-2 border-t border-black/[.08] pt-3 dark:border-white/[.145]">
                  {mcp.tools.map((tool) => (
                    <li key={tool.name}>
                      <span className="font-mono text-xs">{tool.name}</span>
                      <p className="text-xs text-zinc-500">{tool.description}</p>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
