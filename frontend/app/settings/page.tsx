"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  getHealth,
  getModels,
  getOrchestratorModel,
  setOrchestratorModel,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export default function GlobalSettingsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const mcp = health.data?.mcp;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <OrchestratorModelCard />

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

function OrchestratorModelCard() {
  const queryClient = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: getModels, retry: false });
  const current = useQuery({
    queryKey: ["orchestrator-model"],
    queryFn: getOrchestratorModel,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: (model: string | null) => setOrchestratorModel(model),
    onSuccess: (updated) => {
      queryClient.setQueryData(["orchestrator-model"], updated);
      toast.success("AI default model saved");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to save model");
    },
  });

  const value = current.data?.model ?? "";
  const options = models.data ?? [];
  const modelsFailed = models.isError || options.length === 0;

  const [manual, setManual] = useState(value);
  const manualDirty = manual.trim() !== value;

  const handleSelect = (v: string | null) => {
    if (v === null || v === "") mutation.mutate(null);
    else mutation.mutate(v);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>AI Orchestrator (default model)</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {current.isLoading ? (
          <p className="text-zinc-500">Loading…</p>
        ) : (
          <>
            {modelsFailed && options.length === 0 ? (
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium">Default model</label>
                <Input
                  placeholder="provider/model (contoh: ollama/qwen3-coder)"
                  value={manual}
                  onChange={(e) => setManual(e.target.value)}
                />
                <p className="text-xs text-zinc-500">
                  `opencode models` tidak dapat dimuat — ketik nama model secara manual.
                </p>
                <div>
                  <Button
                    disabled={mutation.isPending || !manual.trim()}
                    onClick={() => mutation.mutate(manual.trim() || null)}
                  >
                    {mutation.isPending ? "Saving…" : "Save model"}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium">Default model</label>
                <Select value={value ?? ""} onValueChange={handleSelect}>
                  <SelectTrigger className="w-72">
                    <SelectValue placeholder="Pilih model" />
                  </SelectTrigger>
                  <SelectContent>
                    {options.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <p className="text-xs text-zinc-500">
              Model default untuk semua agent (PM, Engineer, dll) yang tidak punya
              model sendiri. Kredensial tetap dari <code>opencode auth login</code>.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
