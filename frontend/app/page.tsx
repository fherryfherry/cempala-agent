"use client";

import { useQuery } from "@tanstack/react-query";
import { getHealth } from "@/lib/api";

export default function Home() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-4 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Backend status</h1>

      {isLoading && <p className="text-zinc-500">Checking backend…</p>}

      {isError && (
        <p className="text-red-600">
          Could not reach backend: {error instanceof Error ? error.message : "unknown error"}
        </p>
      )}

      {data && (
        <dl className="grid max-w-sm grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <dt className="text-zinc-500">Backend</dt>
          <dd className="font-medium">{data.status}</dd>
          <dt className="text-zinc-500">opencode</dt>
          <dd className="font-medium">{data.opencode ?? "not found"}</dd>
        </dl>
      )}
    </div>
  );
}
