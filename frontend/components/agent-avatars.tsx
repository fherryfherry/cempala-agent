"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { listRuns, type Agent } from "@/lib/api";
import { cn } from "@/lib/utils";
import { AgentAvatar } from "@/components/agent-avatar";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";

// Avatar cluster settings: size of one avatar, horizontal overlap between
// consecutive avatars, how many recent-active avatars are shown before the gray
// "+N" overflow chip, and the rotation interval (the front agent cycles to the back).
const AVATAR_SIZE = 30;
const AVATAR_OVERLAP = 14;
const MAX_VISIBLE = 3;
const ROTATE_MS = 2500;

function AgentTooltip({ agent, children }: { agent: Agent; children: React.ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent positionerClassName="z-[100]">
        <span className="whitespace-nowrap">
          {agent.name} ({agent.role}): {agent.status}
        </span>
      </TooltipContent>
    </Tooltip>
  );
}

/** Stacked round avatars (2-letter initials) of the workspace's agents, ordered by
 * last run start (most recent first) and rotating smoothly. Status shows as a
 * colored ring around each avatar; hover pauses the rotation. */
export function AgentAvatars({
  agents,
  workspaceId,
  workspaceKey,
}: {
  agents: Agent[];
  workspaceId: string;
  workspaceKey: string;
}) {
  const runs = useQuery({
    queryKey: ["runs", workspaceId],
    queryFn: () => listRuns(workspaceId),
    enabled: !!workspaceId,
  });

  // Sort: agents with a run first (by latest started_at desc), the rest fall back
  // to their creation time so the order stays deterministic.
  const ordered = useMemo(() => {
    if (!runs.data) return agents;
    const lastStart = new Map<string, string>();
    for (const r of runs.data) {
      if (r.started_at) {
        const prev = lastStart.get(r.agent_id);
        if (!prev || r.started_at > prev) lastStart.set(r.agent_id, r.started_at);
      }
    }
    return [...agents].sort((a, b) => {
      const sa = lastStart.get(a.id) ?? a.created_at;
      const sb = lastStart.get(b.id) ?? b.created_at;
      if (sa !== sb) return sa > sb ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [agents, runs.data]);

  // Rotation index. Reset to 0 whenever the ordering changes (new run started) so
  // the freshly active agent jumps to the front.
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const orderKey = ordered.map((a) => a.id).join(",");
  const prevKey = useRef(orderKey);
  useEffect(() => {
    if (prevKey.current !== orderKey) {
      prevKey.current = orderKey;
      setIndex(0);
    }
  }, [orderKey]);

  // Only non-idle agents rotate (the ones actually doing something / errored, plus
  // the latest active at the front); idle agents stay parked in their slots so the
  // cluster doesn't cycle through the whole roster pointlessly.
  const activeAgents = ordered.filter((a) => a.status !== "idle");
  const rotatedOrder =
    activeAgents.length > 0
      ? [...activeAgents.slice(index), ...activeAgents.slice(0, index), ...ordered.filter((a) => a.status === "idle")]
      : ordered;

  useEffect(() => {
    if (paused || activeAgents.length < 2) return;
    const timer = setInterval(() => setIndex((i) => (i + 1) % activeAgents.length), ROTATE_MS);
    return () => clearInterval(timer);
  }, [paused, activeAgents.length]);

  if (ordered.length === 0) return null;

  const visible = rotatedOrder.slice(0, MAX_VISIBLE);
  const hidden = ordered.length - visible.length;
  const slot = AVATAR_SIZE - AVATAR_OVERLAP;
  // Chip sits exactly where the next avatar would be in the stack (same overlap),
  // so the cluster stays compact. It renders above the avatars to stay clickable.
  const chipLeft = visible.length * slot;
  const containerWidth = hidden > 0 ? chipLeft + AVATAR_SIZE : (visible.length - 1) * slot + AVATAR_SIZE;

  return (
    <div
      className="relative"
      style={{ width: containerWidth, height: AVATAR_SIZE }}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {visible.map((a, i) => {
        const agent = rotatedOrder[i];
        return (
          <AgentTooltip key={agent.id} agent={agent}>
            <span
              className={cn(
                "absolute top-0 flex cursor-pointer items-center justify-center rounded-full bg-background shadow-sm select-none",
                agent.status === "working" && "animate-pulse",
                agent.status === "disabled" && "opacity-60",
                "hover:scale-110",
              )}
              style={{
                left: i * slot,
                width: AVATAR_SIZE,
                height: AVATAR_SIZE,
                zIndex: visible.length - i,
                transition: "left 500ms ease-in-out, scale 200ms ease-in-out",
              }}
            >
              <AgentAvatar
                name={agent.name}
                template={agent.avatar_template}
                color={agent.avatar_color}
                size={AVATAR_SIZE}
              />
            </span>
          </AgentTooltip>
        );
      })}
      {hidden > 0 && (
        <Link
          href={`/w/${workspaceKey}/agents`}
          title={`${ordered.length} agents — open Agents`}
          className="absolute top-0 flex cursor-pointer items-center justify-center rounded-full bg-zinc-900 text-[10px] font-semibold text-zinc-100 shadow-sm dark:bg-zinc-100 dark:text-zinc-900"
          style={{
            left: chipLeft,
            width: AVATAR_SIZE,
            height: AVATAR_SIZE,
            zIndex: 0,
            paddingLeft: AVATAR_OVERLAP / 2 + 4,
          }}
        >
          +{ordered.length}
        </Link>
      )}
    </div>
  );
}

