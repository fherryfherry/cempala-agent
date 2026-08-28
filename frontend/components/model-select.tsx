import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDefaultModel, type ToolKind } from "@/lib/api";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const TOOL_KINDS: { value: ToolKind; enabled: boolean }[] = [
  { value: "opencode", enabled: true },
  { value: "claude", enabled: true },
  { value: "agy", enabled: true },
  { value: "codex", enabled: true },
];

/** Provider prefixes each tool supports (from `opencode models`). null = all
 * providers; only relevant to `opencode`, the one tool with a dynamic listing. */
const TOOL_MODEL_PROVIDERS: Record<ToolKind, string[] | null> = {
  opencode: null,
  claude: [],
  agy: [],
  codex: [],
};

/** `claude`/`agy`/`codex` have no `opencode models`-style listing command — their
 * `--model` flags take a fixed set of aliases instead of a `provider/model` string. */
const CLAUDE_MODEL_ALIASES = ["sonnet", "opus", "fable"];
const CODEX_MODEL_ALIASES = ["gpt-5.1-codex", "gpt-5.1-codex-mini"];
const AGY_MODEL_ALIASES = [
  "gemini-3.7-flash-high",
  "gemini-3.7-flash-medium",
  "gemini-3.7-flash-low",
  "gemini-3.6-flash-high",
  "gemini-3.6-flash-medium",
  "gemini-3.6-flash-low",
  "gemini-3.5-flash-high",
  "gemini-3.5-flash-medium",
  "gemini-3.5-flash-low",
  "gemini-3.1-pro-high",
  "gemini-3.1-pro-low",
  "claude-sonnet-4-6",
  "claude-opus-4-6-thinking",
  "gpt-oss-120b-medium",
];

const STATIC_ALIAS_MODELS: Partial<Record<ToolKind, string[]>> = {
  claude: CLAUDE_MODEL_ALIASES,
  codex: CODEX_MODEL_ALIASES,
  agy: AGY_MODEL_ALIASES,
};

export function modelsForTool(toolKind: ToolKind, models: string[]): string[] {
  const aliases = STATIC_ALIAS_MODELS[toolKind];
  if (aliases) return aliases;
  const providers = TOOL_MODEL_PROVIDERS[toolKind];
  if (!providers) return models;
  return models.filter((m) => providers.some((p) => m.startsWith(`${p}/`)));
}

/** Model selector filtered by the chosen tool. Falls back to free-text when the
 * model list is unavailable or the current value is not in the tool's list. */
export function ModelSelect({
  toolKind,
  model,
  onModelChange,
  models,
  isLoading,
  isError,
  errorMessage,
}: {
  toolKind: ToolKind;
  model: string;
  onModelChange: (v: string) => void;
  models: string[] | undefined;
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
}) {
  const available = modelsForTool(toolKind, models ?? []);

  // Tools with their own static alias list don't depend on the opencode `/api/models`
  // fetch — a failure/loading state there is irrelevant to them, skip straight to the picker.
  const hasStaticAliases = Boolean(STATIC_ALIAS_MODELS[toolKind]);

  // Only opencode has a host-level default (its opencode.json "model" key) — the
  // other tools use a fixed alias list unrelated to that config file.
  const defaultModel = useQuery({
    queryKey: ["default-model"],
    queryFn: getDefaultModel,
    retry: false,
    enabled: toolKind === "opencode",
  });

  // Auto-pick a model whenever there's no selection yet — e.g. right after switching
  // tools, so the form isn't left with an empty required field. Prefers the host's own
  // opencode default over whatever sorts first in the list.
  useEffect(() => {
    if (model.trim() !== "" || available.length === 0) return;
    const hostDefault = toolKind === "opencode" ? defaultModel.data?.model : null;
    const picked = hostDefault && available.includes(hostDefault) ? hostDefault : available[0];
    onModelChange(picked);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolKind, available.join("|"), defaultModel.data?.model]);

  if (!hasStaticAliases && isError) {
    return (
      <>
        <Input
          placeholder="provider/model"
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
          required
        />
        <p className="text-xs text-red-600">{errorMessage}</p>
      </>
    );
  }

  if (!hasStaticAliases && isLoading) {
    return (
      <Select value={model} onValueChange={(v) => onModelChange(v ?? "")}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder="Loading…" />
        </SelectTrigger>
        <SelectContent className="max-w-[min(32rem,90vw)]">
          {available.map((m) => (
            <SelectItem key={m} value={m}>
              {m}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  if (available.length === 0) {
    return <p className="text-xs text-zinc-500">No models available for this tool.</p>;
  }

  if (model.trim() !== "" && !available.includes(model)) {
    return (
      <Input
        placeholder="provider/model"
        value={model}
        onChange={(e) => onModelChange(e.target.value)}
        required
      />
    );
  }

  return (
    <Select value={model} onValueChange={(v) => onModelChange(v ?? "")}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder="Select a model" />
      </SelectTrigger>
      <SelectContent className="max-w-[min(32rem,90vw)]">
        {available.map((m) => (
          <SelectItem key={m} value={m}>
            {m}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
