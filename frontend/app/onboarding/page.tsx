"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  createWorkspace,
  getDefaultModel,
  getModels,
  type ToolKind,
} from "@/lib/api";
import { AGENT_TEMPLATES, suggestSlotNames, type TemplateSlot } from "@/lib/agent-templates";
import { ChatBotMessage } from "@/components/onboarding/chat-bot-message";
import { ChatOptions } from "@/components/onboarding/chat-options";
import { BuildingScreen } from "@/components/onboarding/building-screen";
import { PmHandoff } from "@/components/onboarding/pm-handoff";
import { FolderBrowser } from "@/components/onboarding/folder-browser";
import { TOOL_KINDS, modelsForTool } from "@/components/model-select";
import { Button } from "@/components/ui/button";

type Step =
  | "intro"
  | "name"
  | "key_confirm"
  | "key_edit"
  | "repo_choice"
  | "repo_edit"
  | "repo_clone"
  | "workspace_error"
  | "build_what"
  | "squad_choice"
  | "squad_manual"
  | "tool_pick"
  | "building"
  | "pm_chat";

export interface Turn {
  bot: string;
  user: string;
}

function suggestKey(name: string): string {
  const letters = name.replace(/[^a-zA-Z]/g, "").toUpperCase();
  const padded = letters.length < 2 ? (letters + "WS").slice(0, 2) : letters;
  return padded.slice(0, 5);
}

const SQUAD_SLOTS = AGENT_TEMPLATES[0].slots;

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("intro");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [workspaceKey, setWorkspaceKey] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [description, setDescription] = useState("");
  const [selectedSlots, setSelectedSlots] = useState<TemplateSlot[]>(SQUAD_SLOTS);
  const [toolKind, setToolKind] = useState<ToolKind>("opencode");
  const [model, setModel] = useState("");
  const [creating, setCreating] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [pm, setPm] = useState<{ id: string; name: string } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const models = useQuery({ queryKey: ["models"], queryFn: getModels, retry: false });
  const defaultModel = useQuery({
    queryKey: ["default-model"],
    queryFn: getDefaultModel,
    retry: false,
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, step, creating]);

  const pushTurn = (bot: string, user: string) => setTurns((t) => [...t, { bot, user }]);

  async function attemptCreateWorkspace(
    finalKey: string,
    finalRepoPath: string,
    cloneUrl?: string,
  ) {
    setCreating(true);
    try {
      const ws = await createWorkspace({
        name,
        key: finalKey,
        repo_path: finalRepoPath,
        clone_url: cloneUrl,
      });
      setWorkspaceId(ws.id);
      setWorkspaceKey(ws.key);
      setStep("build_what");
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Terjadi kesalahan tak terduga.";
      setErrorMessage(message);
      setStep("workspace_error");
    } finally {
      setCreating(false);
    }
  }

  if (step === "pm_chat" && workspaceId && pm) {
    return (
      <PmHandoff
        workspaceId={workspaceId}
        workspaceKey={workspaceKey}
        turns={turns}
        description={description}
      />
    );
  }

  return (
    <div
      className="mx-auto flex h-[calc(100dvh-3.5rem)] w-full max-w-2xl flex-col justify-end gap-4 overflow-y-auto px-6 py-10"
      style={{
        WebkitMaskImage: "linear-gradient(to bottom, transparent 0, black 3rem)",
        maskImage: "linear-gradient(to bottom, transparent 0, black 3rem)",
      }}
    >
      {turns.map((t, i) => (
        <div key={i} className="flex flex-col gap-2">
          <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-5 py-3.5 text-lg">
            {t.bot}
          </div>
          <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-5 py-3.5 text-lg text-primary-foreground">
            {t.user}
          </div>
        </div>
      ))}

      {step === "intro" && (
        <Step
          text="Halo! Selamat datang 👋 Perkenalkan, saya CEMPALA Bot. Sebelum mulai, kita perlu bikin Workspace dulu — Workspace itu ruang kerja tempat tim AI agent-mu (PM, Engineer, QA, dst) mengerjakan tiket-tiket project ini secara otomatis, terhubung langsung ke repo project aslimu. Yuk kita siapkan bareng-bareng."
          key="intro"
        >
          <ChatOptions
            options={[{ label: "Mulai", value: "start" }]}
            onAnswer={() => {
              pushTurn(
                "Halo! Selamat datang 👋 Perkenalkan, saya CEMPALA Bot. Sebelum mulai, kita perlu bikin Workspace dulu — Workspace itu ruang kerja tempat tim AI agent-mu (PM, Engineer, QA, dst) mengerjakan tiket-tiket project ini secara otomatis, terhubung langsung ke repo project aslimu. Yuk kita siapkan bareng-bareng.",
                "Mulai",
              );
              setStep("name");
            }}
          />
        </Step>
      )}

      {step === "name" && (
        <Step
          text="Sip, ayo kita mulai! Pertama, kasih nama buat workspace ini — biasanya nama project atau tim kamu. Nama apa?"
          key="name"
        >
          <ChatOptions
            options={[]}
            freeTextPlaceholder="Nama workspace…"
            onAnswer={(v) => {
              pushTurn(
                "Sip, ayo kita mulai! Pertama, kasih nama buat workspace ini — biasanya nama project atau tim kamu. Nama apa?",
                v,
              );
              setName(v);
              setKey(suggestKey(v));
              setStep("key_confirm");
            }}
          />
        </Step>
      )}

      {step === "key_confirm" && (
        <Step
          text={`Nama yang bagus, "${name}"! Tiap workspace juga punya "key" — kode singkat 2-5 huruf buat penomoran tiket (misalnya ${key}-1, ${key}-2). Saya sarankan pakai key ${key}, gimana?`}
          key="key_confirm"
        >
          <ChatOptions
            options={[
              { label: "Pakai ini", value: "yes" },
              { label: "Ubah", value: "edit" },
            ]}
            onAnswer={(v) => {
              pushTurn(
                `Nama yang bagus, "${name}"! Tiap workspace juga punya "key" — kode singkat 2-5 huruf buat penomoran tiket (misalnya ${key}-1, ${key}-2). Saya sarankan pakai key ${key}, gimana?`,
                v === "yes" ? "Pakai ini" : "Ubah",
              );
              if (v === "yes") setStep("repo_choice");
              else setStep("key_edit");
            }}
          />
        </Step>
      )}

      {step === "key_edit" && (
        <Step text="Oke, key-nya mau apa? (2-5 huruf, misalnya MAP)" key="key_edit">
          <ChatOptions
            options={[]}
            freeTextPlaceholder="MAP"
            minLength={2}
            onAnswer={(v) => {
              const sanitized = v.replace(/[^a-zA-Z]/g, "").toUpperCase().slice(0, 5);
              pushTurn("Oke, key-nya mau apa? (2-5 huruf, misalnya MAP)", sanitized);
              setKey(sanitized);
              setStep("repo_choice");
            }}
          />
        </Step>
      )}

      {step === "repo_choice" && (
        <Step
          text="Mantap, key-nya dicatat! Sekarang soal repo — folder kerja nyata tempat agent-agent nanti baca dan nulis kode. Reponya mau di mana?"
          key="repo_choice"
        >
          <ChatOptions
            options={[
              { label: "Buat baru otomatis", value: "auto" },
              { label: "Browse folder", value: "browse" },
              { label: "Clone dari Git", value: "clone" },
              { label: "Saya punya path sendiri", value: "custom" },
            ]}
            onAnswer={(v) => {
              pushTurn(
                "Mantap, key-nya dicatat! Sekarang soal repo — folder kerja nyata tempat agent-agent nanti baca dan nulis kode. Reponya mau di mana?",
                v === "auto"
                  ? "Buat baru otomatis"
                  : v === "browse"
                    ? "Browse folder"
                    : v === "clone"
                      ? "Clone dari Git"
                      : "Saya punya path sendiri",
              );
              if (v === "auto") {
                void attemptCreateWorkspace(key, name);
              } else if (v === "browse") {
                setBrowsing(true);
              } else if (v === "clone") {
                setStep("repo_clone");
              } else {
                setStep("repo_edit");
              }
            }}
          />
        </Step>
      )}

      {browsing && (
        <FolderBrowser
          onClose={() => setBrowsing(false)}
          onSelect={(selectedPath) => {
            setBrowsing(false);
            pushTurn("Browse folder", selectedPath);
            void attemptCreateWorkspace(key, selectedPath);
          }}
        />
      )}

      {step === "repo_edit" && (
        <Step
          text="Oke, kasih tau absolute path ke folder repo-nya (misalnya /Users/kamu/projects/nama-project)"
          key="repo_edit"
        >
          <ChatOptions
            options={[]}
            freeTextPlaceholder="/absolute/path/to/repo"
            onAnswer={(v) => {
              pushTurn(
                "Oke, kasih tau absolute path ke folder repo-nya (misalnya /Users/kamu/projects/nama-project)",
                v,
              );
              void attemptCreateWorkspace(key, v);
            }}
          />
        </Step>
      )}

      {step === "repo_clone" && (
        <Step
          text="Oke, kasih tau URL git repo-nya (HTTPS atau SSH) — nanti di-clone otomatis ke workspaces/<nama>."
          key="repo_clone"
        >
          <ChatOptions
            options={[]}
            freeTextPlaceholder="https://github.com/org/repo.git"
            onAnswer={(v) => {
              pushTurn(
                "Oke, kasih tau URL git repo-nya (HTTPS atau SSH) — nanti di-clone otomatis ke workspaces/<nama>.",
                v,
              );
              void attemptCreateWorkspace(key, name, v);
            }}
          />
        </Step>
      )}

      {step === "workspace_error" && (
        <Step text={`Hmm, gagal: ${errorMessage}. Coba lagi?`} key="workspace_error">
          <ChatOptions
            options={[
              { label: "Ubah key", value: "key" },
              { label: "Ubah repo", value: "repo" },
            ]}
            onAnswer={(v) => {
              pushTurn(`Hmm, gagal: ${errorMessage}. Coba lagi?`, v === "key" ? "Ubah key" : "Ubah repo");
              setStep(v === "key" ? "key_edit" : "repo_choice");
            }}
          />
        </Step>
      )}

      {creating && <p className="text-sm text-zinc-500">Membuat workspace…</p>}

      {step === "build_what" && workspaceId && (
        <Step
          text="Mantap, workspace-nya sudah jadi 🎉 Sekarang ceritain singkat, project ini mau dibuat apa? Ini bakal jadi konteks buat semua agent, dan saya langsung buatkan tiket pertamanya."
          key="build_what"
        >
          <ChatOptions
            options={[]}
            minLength={10}
            freeTextPlaceholder="Ceritakan singkat project ini mau dibuat apa…"
            onAnswer={(v) => {
              pushTurn(
                "Mantap, workspace-nya sudah jadi 🎉 Sekarang ceritain singkat, project ini mau dibuat apa? Ini bakal jadi konteks buat semua agent, dan saya langsung buatkan tiket pertamanya.",
                v,
              );
              setDescription(v);
              setStep("squad_choice");
            }}
          />
        </Step>
      )}

      {step === "squad_choice" && (
        <Step
          text="Keren, ide bagus itu! Terakhir soal tim — workspace ini butuh agent (AI worker) yang bakal ngerjain tiket-tiketnya. Mau pakai tim lengkap atau pilih sendiri?"
          key="squad_choice"
        >
          <ChatOptions
            options={[
              { label: "Pakai full squad", value: "full" },
              { label: "Pilih manual", value: "manual" },
            ]}
            onAnswer={(v) => {
              pushTurn(
                "Keren, ide bagus itu! Terakhir soal tim — workspace ini butuh agent (AI worker) yang bakal ngerjain tiket-tiketnya. Mau pakai tim lengkap atau pilih sendiri?",
                v === "full" ? "Pakai full squad" : "Pilih manual",
              );
              if (v === "full") {
                setSelectedSlots(SQUAD_SLOTS);
                setStep("tool_pick");
              } else {
                setStep("squad_manual");
              }
            }}
          />
        </Step>
      )}

      {step === "squad_manual" && (
        <SquadPicker
          onDone={(slots) => {
            pushTurn(
              "Oke, pilih role yang mau diaktifkan dari tim standar:",
              slots.map((s) => s.label).join(", ") || "(tidak ada)",
            );
            setSelectedSlots(slots);
            setStep("tool_pick");
          }}
        />
      )}

      {step === "tool_pick" && (
        <Step
          text="Sip, tim sudah dipilih! Terakhir, mau pakai tool apa buat jalanin agent-agentnya?"
          key="tool_pick"
        >
          <div className="flex flex-wrap gap-2">
            {TOOL_KINDS.map((t) => {
              const opencodeUnavailable =
                t.value === "opencode" && (models.isLoading || models.isError);
              const disabled = !t.enabled || opencodeUnavailable;
              return (
                <button
                  key={t.value}
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    const available = modelsForTool(t.value, models.data ?? []);
                    // Prefer the host's own `opencode` CLI default (its opencode.json
                    // "model" key) over just picking whatever sorts first — only
                    // meaningful for opencode itself, since the other tools use a
                    // fixed alias list unrelated to that config file.
                    const hostDefault =
                      t.value === "opencode" ? defaultModel.data?.model : null;
                    const picked =
                      hostDefault && available.includes(hostDefault)
                        ? hostDefault
                        : (available[0] ?? "");
                    setToolKind(t.value);
                    setModel(picked);
                    pushTurn(
                      "Sip, tim sudah dipilih! Terakhir, mau pakai tool apa buat jalanin agent-agentnya?",
                      t.value,
                    );
                    setStep("building");
                  }}
                  className="rounded-full border border-border px-4 py-2 text-lg text-zinc-500 transition-colors hover:border-primary hover:bg-accent/50 disabled:pointer-events-none disabled:opacity-40"
                >
                  {t.value}
                  {!t.enabled ? " (coming soon)" : ""}
                </button>
              );
            })}
          </div>
          {models.isError && (
            <p className="text-sm text-red-600">
              Model opencode gagal dimuat — coba tool lain, atau jalankan{" "}
              <code className="font-mono">opencode auth login</code> lalu ulangi.
            </p>
          )}
        </Step>
      )}

      {step === "building" && workspaceId && (
        <BuildingScreen
          workspaceId={workspaceId}
          description={description}
          slots={selectedSlots}
          names={suggestSlotNames(selectedSlots, [])}
          toolKind={toolKind}
          model={model}
          onDone={(pmAgent) => {
            if (!pmAgent) {
              toast.error("Agent PM gagal dibuat — lengkapi manual dari halaman Agents.");
              router.push(`/w/${workspaceKey}/dashboard`);
              return;
            }
            setPm(pmAgent);
            setStep("pm_chat");
          }}
        />
      )}

      <div ref={bottomRef} />
    </div>
  );
}

function Step({ text, children }: { text: string; children: React.ReactNode }) {
  const [typed, setTyped] = useState(false);
  return (
    <div className="flex flex-col gap-3">
      <ChatBotMessage text={text} onDone={() => setTyped(true)} />
      {typed && children}
    </div>
  );
}

function SquadPicker({ onDone }: { onDone: (slots: TemplateSlot[]) => void }) {
  const [picked, setPicked] = useState<Set<string>>(new Set(SQUAD_SLOTS.map((s) => s.role)));
  const [typed, setTyped] = useState(false);

  const toggle = (role: string) => {
    if (role === "pm") return; // PM is mandatory — always drives the workspace, can't be dropped.
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      return next;
    });
  };

  return (
    <div className="flex flex-col gap-3">
      <ChatBotMessage
        text="Pilih role yang mau diaktifkan (PM wajib, jadi selalu aktif):"
        onDone={() => setTyped(true)}
      />
      {typed && (
        <>
          <div className="flex flex-wrap gap-2">
            {SQUAD_SLOTS.map((slot) => {
              const mandatory = slot.role === "pm";
              return (
                <button
                  key={slot.role}
                  type="button"
                  disabled={mandatory}
                  onClick={() => toggle(slot.role)}
                  className={`rounded-full border px-4 py-2 text-lg transition-colors ${
                    picked.has(slot.role)
                      ? "border-primary bg-accent/50"
                      : "border-border text-zinc-500"
                  } ${mandatory ? "cursor-not-allowed opacity-80" : ""}`}
                >
                  {slot.label}
                  {mandatory ? " (wajib)" : ""}
                </button>
              );
            })}
          </div>
          <Button
            size="lg"
            className="w-fit text-lg"
            onClick={() => onDone(SQUAD_SLOTS.filter((s) => picked.has(s.role)))}
          >
            Lanjut
          </Button>
        </>
      )}
    </div>
  );
}
