"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CalendarIcon } from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
  listAgents,
  listSprints,
  listTickets,
  listWorkspaces,
  updateSprint,
  type Agent,
  type Sprint,
  type Ticket,
  type TicketStatus,
} from "@/lib/api";
import { STATUS_BLOCK_COLOR } from "@/lib/ticket-style";
import { AgentAvatar } from "@/components/agent-avatar";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

const UNIT_LABEL: Record<string, string> = { hour: "jam", day: "hari" };
/** Column header prefix per time_unit, used only in the "belum terjadwal" fallback
 * section below: day → H1, H2… ; hour → J1, J2… */
const COLUMN_PREFIX: Record<string, string> = { hour: "J", day: "H" };

/** Px per calendar day on the scheduled Timeline's date axis, and px per duration
 * unit in the unscheduled fallback section (must match the grid-line spacing in
 * globals.css: .gantt-grid-lines). */
const PX_PER_DAY = 48;
/** Minimum visible width for tickets without (or with tiny) estimates. */
const MIN_BLOCK_PX = 72;
const MIN_SPRINT_BAR_PX = 120;
/** Left column min width; it grows with content so keys/titles never truncate. */
const LEFT_COL_MIN_PX = 420;
const RANGE_PADDING_DAYS = 3;
const FALLBACK_PAST_DAYS = 7;
const FALLBACK_FUTURE_DAYS = 21;

function blockWidth(duration: number | null): number {
  if (duration == null) return MIN_BLOCK_PX;
  return Math.max(MIN_BLOCK_PX, Math.round(duration * PX_PER_DAY));
}

/** Grid units a ticket occupies; tickets without an estimate take one unit. */
function unitDuration(duration: number | null): number {
  return duration == null ? 1 : Math.max(0, duration);
}

/** Parse a plain "YYYY-MM-DD" (as stored/sent) into a local Date at midnight. */
function parseDateOnly(value: string | null | undefined): Date | null {
  if (!value) return null;
  const [y, m, d] = value.split("-").map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}

function formatDateOnly(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function addDays(d: Date, n: number): Date {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + n);
  return copy;
}

function daysBetween(a: Date, b: Date): number {
  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  return Math.round((b.getTime() - a.getTime()) / MS_PER_DAY);
}

/** "Today" as seen in the workspace's own timezone, not the browser's. */
function todayInTimezone(timeZone: string): Date {
  try {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone }).format(new Date());
    return parseDateOnly(parts) ?? new Date();
  } catch {
    return new Date();
  }
}

interface PlacedTicket {
  ticket: Ticket;
  left: number;
}

interface EpicGroup {
  epic: Ticket;
  children: PlacedTicket[];
}

/** Group a sprint's tickets into epic/standalone rows, packing them back-to-back
 * starting at `originPx`. Returns the px position right after the last ticket,
 * so callers can tell whether ticket content overflows a sprint's calendar bar. */
function layoutSprintTickets(
  sprintTickets: Ticket[],
  originPx: number,
): { epics: EpicGroup[]; standalone: PlacedTicket[]; endPx: number } {
  const sorted = [...sprintTickets].sort(
    (a, b) => a.created_at.localeCompare(b.created_at) || a.key.localeCompare(b.key),
  );
  const ids = new Set(sorted.map((t) => t.id));
  let cursorUnits = 0;
  const placed = sorted.map((ticket) => {
    const left = originPx + cursorUnits * PX_PER_DAY;
    cursorUnits += unitDuration(ticket.duration_estimate);
    return { ticket, left };
  });
  const epics = placed
    .filter((p) => p.ticket.parent_id && ids.has(p.ticket.parent_id))
    .reduce((acc, p) => {
      const epicId = p.ticket.parent_id as string;
      let group = acc.find((g) => g.epic.id === epicId);
      if (!group) {
        const epic = placed.find((x) => x.ticket.id === epicId)?.ticket;
        if (!epic) return acc;
        group = { epic, children: [] };
        acc.push(group);
      }
      group.children.push(p);
      return acc;
    }, [] as EpicGroup[]);
  const epicIds = new Set(epics.map((g) => g.epic.id));
  const childIds = new Set(epics.flatMap((g) => g.children.map((c) => c.ticket.id)));
  const standalone = placed.filter(
    (p) => !epicIds.has(p.ticket.id) && !childIds.has(p.ticket.id),
  );
  return { epics, standalone, endPx: originPx + cursorUnits * PX_PER_DAY };
}

/** Grid units a sprint's bar (and the unscheduled-section cursor) advances: the
 * sprint's own duration when set, otherwise the sum of its tickets' durations. */
function sprintExtent(sprint: Sprint, epics: EpicGroup[], standalone: PlacedTicket[]): number {
  if (sprint.duration_estimate != null) return Math.max(1, sprint.duration_estimate);
  const all = [...standalone, ...epics.flatMap((g) => g.children)];
  return Math.max(
    1,
    all.reduce((sum, { ticket }) => sum + unitDuration(ticket.duration_estimate), 0),
  );
}

interface UnscheduledRow {
  sprint: Sprint;
  epics: EpicGroup[];
  standalone: PlacedTicket[];
  extent: number;
  sprintStart: number;
}

/** Serial back-to-back placement for sprints with no date at all: sprints ordered
 * by index, each sprint's blocks continuing where the previous one ended. This is
 * the pre-calendar layout, kept only as a fallback for not-yet-scheduled sprints
 * so they still render (never fabricating a fake date for them). */
function layoutUnscheduledRows(sprints: Sprint[], allTickets: Ticket[]): UnscheduledRow[] {
  let globalCursorUnits = 0;
  return sprints.map((sprint) => {
    const sprintTickets = allTickets.filter((t) => t.sprint_id === sprint.id);
    const originPx = globalCursorUnits * PX_PER_DAY;
    const { epics, standalone } = layoutSprintTickets(sprintTickets, originPx);
    const extent = sprintExtent(sprint, epics, standalone);
    const sprintStart = globalCursorUnits;
    globalCursorUnits = Math.max(globalCursorUnits, sprintStart + extent);
    return { sprint, epics, standalone, extent, sprintStart };
  });
}

interface ScheduledRow {
  sprint: Sprint;
  epics: EpicGroup[];
  standalone: PlacedTicket[];
  barLeft: number;
  barWidth: number;
  /** Only one of start_date/end_date is set — bar is a stub, not a real range. */
  incomplete: boolean;
}

/** Calendar-anchored placement for sprints with at least one date set. Sprints
 * can have gaps or overlap between each other now — nothing forces them
 * contiguous the way the old serial cursor did. */
function layoutScheduledRows(
  sprints: Sprint[],
  allTickets: Ticket[],
  dateToX: (d: Date) => number,
): { rows: ScheduledRow[]; maxRightEdge: number } {
  let maxRightEdge = 0;
  const rows = sprints.map((sprint) => {
    const start = parseDateOnly(sprint.start_date);
    const end = parseDateOnly(sprint.end_date);
    let barLeft: number;
    let barWidth: number;
    let incomplete = false;
    if (start && end) {
      barLeft = dateToX(start);
      barWidth = Math.max(MIN_SPRINT_BAR_PX, dateToX(addDays(end, 1)) - barLeft);
    } else if (start) {
      barLeft = dateToX(start);
      barWidth = MIN_SPRINT_BAR_PX;
      incomplete = true;
    } else {
      const rightEdge = dateToX(addDays(end as Date, 1));
      barWidth = MIN_SPRINT_BAR_PX;
      barLeft = Math.max(0, rightEdge - barWidth);
      incomplete = true;
    }
    const sprintTickets = allTickets.filter((t) => t.sprint_id === sprint.id);
    const { epics, standalone, endPx } = layoutSprintTickets(sprintTickets, barLeft);
    maxRightEdge = Math.max(maxRightEdge, barLeft + barWidth, endPx);
    return { sprint, epics, standalone, barLeft, barWidth, incomplete };
  });
  return { rows, maxRightEdge };
}

const STATUS_LEGEND: { status: TicketStatus; label: string }[] = [
  { status: "todo", label: "Todo" },
  { status: "in_progress", label: "In Progress" },
  { status: "review", label: "Review" },
  { status: "qa", label: "QA" },
  { status: "security", label: "Security" },
  { status: "done", label: "Done" },
  { status: "release", label: "Release" },
  { status: "blocked", label: "Blocked" },
];

function EmptySprintsState({ workspaceKey }: { workspaceKey: string }) {
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-col gap-1.5 px-8 pt-7 opacity-50">
        <div className="flex items-center gap-2.5">
          <div className="h-2 w-24 rounded-full border border-dashed border-zinc-300 dark:border-zinc-600" />
          <div className="h-2 w-[168px] rounded-full bg-zinc-100 dark:bg-zinc-800" />
        </div>
        <div className="flex items-center gap-2.5 pl-5">
          <div className="h-2 w-24 rounded-full border border-dashed border-zinc-200 dark:border-zinc-700" />
          <div className="h-2 w-24 rounded-full bg-zinc-100 dark:bg-zinc-800" />
        </div>
        <div className="flex items-center gap-2.5 pl-5">
          <div className="h-2 w-24 rounded-full border border-dashed border-zinc-200 dark:border-zinc-700" />
          <div className="h-2 w-[232px] rounded-full bg-zinc-100 dark:bg-zinc-800" />
        </div>
      </div>

      <CardContent className="flex flex-col items-center gap-4 py-7 text-center">
        <div className="flex size-14 items-center justify-center rounded-full bg-zinc-100 dark:bg-zinc-800">
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-zinc-500"
          >
            <rect x="3" y="4.5" width="13" height="3" rx="1.5" />
            <rect x="3" y="10.5" width="9" height="3" rx="1.5" />
            <rect x="3" y="16.5" width="18" height="3" rx="1.5" />
          </svg>
        </div>

        <div className="flex max-w-sm flex-col gap-1.5">
          <p className="text-[15px] font-semibold">Belum ada sprint</p>
          <p className="text-[13px] leading-relaxed text-zinc-500">
            Diskusikan dulu rencana kerja dengan PM di Chat. Setelah disepakati, sprint dan
            timeline-nya akan langsung muncul di sini.
          </p>
        </div>

        <Link
          href={`/w/${workspaceKey}/chat`}
          className={buttonVariants({ variant: "outline", size: "sm" })}
        >
          Diskusi dengan PM
          <ArrowRight />
        </Link>
      </CardContent>
    </Card>
  );
}

function StatusLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {STATUS_LEGEND.map(({ status, label }) => (
        <span key={status} className="flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-400">
          <span className={`size-2.5 rounded-sm ${STATUS_BLOCK_COLOR[status]}`} />
          {label}
        </span>
      ))}
    </div>
  );
}

export default function TimelinePage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;
  const queryClient = useQueryClient();

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const sprints = useQuery({
    queryKey: ["sprints", workspace?.id],
    queryFn: () => listSprints(workspace!.id),
    enabled: !!workspace,
  });

  const tickets = useQuery({
    queryKey: ["tickets", workspace?.id],
    queryFn: () => listTickets(workspace!.id),
    enabled: !!workspace,
  });

  const agents = useQuery({
    queryKey: ["agents", workspace?.id],
    queryFn: () => listAgents(workspace!.id),
    enabled: !!workspace,
  });

  const activateMutation = useMutation({
    mutationFn: (sprintId: string) => updateSprint(sprintId, { status: "active" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sprints", workspace?.id] });
      toast.success("Sprint activated");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to activate sprint");
    },
  });

  const completeMutation = useMutation({
    mutationFn: (sprintId: string) => updateSprint(sprintId, { status: "completed" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sprints", workspace?.id] });
      queryClient.invalidateQueries({ queryKey: ["tickets", workspace?.id] });
      toast.success("Sprint selesai — tiket yang belum selesai dipindah ke sprint lain/backlog.");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to complete sprint");
    },
  });

  const saveDatesMutation = useMutation({
    mutationFn: ({
      sprintId,
      startDate,
      endDate,
    }: {
      sprintId: string;
      startDate: string | null;
      endDate: string | null;
    }) => updateSprint(sprintId, { start_date: startDate, end_date: endDate }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sprints", workspace?.id] });
      toast.success("Tanggal sprint disimpan");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to save sprint dates");
    },
  });

  if (workspaces.isLoading) {
    return <p className="px-6 py-10 text-sm text-zinc-500">Loading workspace…</p>;
  }

  if (!workspace) {
    return (
      <p className="px-6 py-10 text-sm text-red-600">
        Workspace &quot;{workspaceKey}&quot; not found.
      </p>
    );
  }

  const unitLabel = UNIT_LABEL[workspace.time_unit] ?? workspace.time_unit;
  const columnPrefix = COLUMN_PREFIX[workspace.time_unit] ?? "H";
  const orderedSprints = [...(sprints.data ?? [])].sort((a, b) => a.index - b.index);
  const allTickets = tickets.data ?? [];
  const agentOf = (id: string | null) => agents.data?.find((a) => a.id === id);
  const backlogCount = allTickets.filter((t) => !t.sprint_id).length;

  const scheduledSprints = orderedSprints.filter((s) => s.start_date || s.end_date);
  const unscheduledSprints = orderedSprints.filter((s) => !s.start_date && !s.end_date);

  const today = todayInTimezone(workspace.timezone);

  let minDate: Date;
  let maxDate: Date;
  if (scheduledSprints.length === 0) {
    minDate = addDays(today, -FALLBACK_PAST_DAYS);
    maxDate = addDays(today, FALLBACK_FUTURE_DAYS);
  } else {
    const knownDates = scheduledSprints
      .flatMap((s) => [parseDateOnly(s.start_date), parseDateOnly(s.end_date)])
      .filter((d): d is Date => d != null);
    knownDates.push(today);
    const minRaw = new Date(Math.min(...knownDates.map((d) => d.getTime())));
    const maxRaw = new Date(Math.max(...knownDates.map((d) => d.getTime())));
    minDate = addDays(minRaw, -RANGE_PADDING_DAYS);
    maxDate = addDays(maxRaw, RANGE_PADDING_DAYS);
  }
  const dateToX = (d: Date) => daysBetween(minDate, d) * PX_PER_DAY;

  const { rows: scheduledRows, maxRightEdge } = layoutScheduledRows(
    scheduledSprints,
    allTickets,
    dateToX,
  );
  const dateRangeWidth = Math.max(1, daysBetween(minDate, maxDate) + 1) * PX_PER_DAY;
  const scheduledGridWidth = Math.max(dateRangeWidth, maxRightEdge);
  const scheduledGridDays = Math.ceil(scheduledGridWidth / PX_PER_DAY);
  const todayX = dateToX(today);

  const unscheduledRows = layoutUnscheduledRows(unscheduledSprints, allTickets);
  const unscheduledGridUnits = unscheduledRows.reduce(
    (max, r) => Math.max(max, r.sprintStart + r.extent),
    0,
  );
  const unscheduledGridColumns = Math.max(1, Math.ceil(unscheduledGridUnits));
  const unscheduledGridWidth = unscheduledGridColumns * PX_PER_DAY;

  const handleSaveDates = (sprintId: string, startDate: string | null, endDate: string | null) =>
    saveDatesMutation.mutate({ sprintId, startDate, endDate });

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Timeline</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Sprint ditampilkan di sumbu kalender berdasarkan tanggal mulai/selesai; sprint yang
          belum punya tanggal muncul terpisah di bawah. Durasi tiket dalam {unitLabel} (satuan
          workspace, atur di Settings).
        </p>
      </div>

      {orderedSprints.length === 0 ? (
        <EmptySprintsState workspaceKey={workspaceKey} />
      ) : (
        <>
          <StatusLegend />

          {scheduledRows.length > 0 && (
            <Card>
              <CardContent className="overflow-x-auto p-0">
                <div
                  className="grid min-w-max"
                  style={{
                    gridTemplateColumns: `minmax(${LEFT_COL_MIN_PX}px, max-content) ${scheduledGridWidth}px`,
                  }}
                >
                  <div className="sticky left-0 z-20 flex h-9 items-center bg-card px-4 text-xs font-semibold text-zinc-600 dark:text-zinc-300">
                    Sprint / Task
                  </div>
                  <div
                    className="gantt-grid-lines relative"
                    style={{ width: scheduledGridWidth, height: 36 }}
                  >
                    {Array.from({ length: scheduledGridDays }, (_, i) => {
                      const d = addDays(minDate, i);
                      const isMonthStart = d.getDate() === 1;
                      return (
                        <span
                          key={i}
                          className={`absolute top-0 flex h-full w-full flex-col items-center justify-center text-[10px] text-zinc-400 ${
                            isMonthStart ? "border-l border-black/10 dark:border-white/10" : ""
                          }`}
                          style={{ left: i * PX_PER_DAY, width: PX_PER_DAY }}
                        >
                          {isMonthStart && (
                            <span className="text-[9px] font-semibold text-zinc-500">
                              {d.toLocaleDateString("id-ID", { month: "short" })}
                            </span>
                          )}
                          <span>{d.getDate()}</span>
                        </span>
                      );
                    })}
                    <TodayLine x={todayX} label="Hari ini" />
                  </div>

                  {scheduledRows.map(({ sprint, epics, standalone, barLeft, barWidth, incomplete }) => (
                    <Fragment key={sprint.id}>
                      <GanttSprintLeft
                        sprint={sprint}
                        epics={epics}
                        standalone={standalone}
                        unitLabel={unitLabel}
                        onActivate={() => activateMutation.mutate(sprint.id)}
                        activating={activateMutation.isPending}
                        onComplete={() => completeMutation.mutate(sprint.id)}
                        completing={completeMutation.isPending && completeMutation.variables === sprint.id}
                        onSaveDates={(start, end) => handleSaveDates(sprint.id, start, end)}
                        savingDates={saveDatesMutation.isPending}
                      />
                      <div
                        className="gantt-grid-lines relative"
                        style={{ width: scheduledGridWidth, height: 44 }}
                      >
                        <div
                          className={`absolute top-1/2 h-6 -translate-y-1/2 rounded ${
                            incomplete
                              ? "border-2 border-dashed border-zinc-400 bg-zinc-400/20"
                              : "bg-zinc-900/85 dark:bg-zinc-100/90"
                          }`}
                          style={{ left: Math.round(barLeft), width: barWidth }}
                          title={
                            incomplete
                              ? `${sprint.name} — tanggal belum lengkap`
                              : `${sprint.name} (${sprint.start_date} – ${sprint.end_date})`
                          }
                        />
                        <TodayLine x={todayX} />
                      </div>

                      {epics.map(({ epic, children }) => (
                        <Fragment key={epic.id}>
                          <EpicLeftCell ticket={epic} workspaceKey={workspaceKey} />
                          <div className="gantt-grid-lines relative border-b border-black/[.06] dark:border-white/[.06]">
                            <div style={{ width: scheduledGridWidth, height: 36 }} />
                            <TodayLine x={todayX} />
                          </div>
                          {children.map(({ ticket, left }) => (
                            <Fragment key={ticket.id}>
                              <TicketLeftCell
                                ticket={ticket}
                                workspaceKey={workspaceKey}
                                assignee={agentOf(ticket.assignee_id)}
                                indent
                              />
                              <TicketTimelineCell
                                ticket={ticket}
                                left={left}
                                unitLabel={unitLabel}
                                gridWidth={scheduledGridWidth}
                                workspaceKey={workspaceKey}
                                todayX={todayX}
                              />
                            </Fragment>
                          ))}
                        </Fragment>
                      ))}

                      {standalone.map(({ ticket, left }) => (
                        <Fragment key={ticket.id}>
                          <TicketLeftCell
                            ticket={ticket}
                            workspaceKey={workspaceKey}
                            assignee={agentOf(ticket.assignee_id)}
                          />
                          <TicketTimelineCell
                            ticket={ticket}
                            left={left}
                            unitLabel={unitLabel}
                            gridWidth={scheduledGridWidth}
                            workspaceKey={workspaceKey}
                            todayX={todayX}
                          />
                        </Fragment>
                      ))}
                    </Fragment>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {unscheduledRows.length > 0 && (
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-medium text-zinc-500">Sprint belum terjadwal</h2>
              <Card>
                <CardContent className="overflow-x-auto p-0">
                  <div
                    className="grid min-w-max"
                    style={{
                      gridTemplateColumns: `minmax(${LEFT_COL_MIN_PX}px, max-content) ${unscheduledGridWidth}px`,
                    }}
                  >
                    <div className="sticky left-0 z-20 flex h-9 items-center bg-card px-4 text-xs font-semibold text-zinc-600 dark:text-zinc-300">
                      Sprint / Task
                    </div>
                    <div
                      className="gantt-grid-lines relative"
                      style={{ width: unscheduledGridWidth, height: 36 }}
                    >
                      {Array.from({ length: unscheduledGridColumns }, (_, i) => (
                        <span
                          key={i}
                          className="absolute top-0 pt-1.5 text-center text-[10px] text-zinc-400"
                          style={{ left: i * PX_PER_DAY, width: PX_PER_DAY }}
                        >
                          {columnPrefix}
                          {i + 1}
                        </span>
                      ))}
                    </div>

                    {unscheduledRows.map(({ sprint, epics, standalone, extent, sprintStart }) => (
                      <Fragment key={sprint.id}>
                        <GanttSprintLeft
                          sprint={sprint}
                          epics={epics}
                          standalone={standalone}
                          unitLabel={unitLabel}
                          onActivate={() => activateMutation.mutate(sprint.id)}
                          activating={activateMutation.isPending}
                          onComplete={() => completeMutation.mutate(sprint.id)}
                          completing={
                            completeMutation.isPending && completeMutation.variables === sprint.id
                          }
                          onSaveDates={(start, end) => handleSaveDates(sprint.id, start, end)}
                          savingDates={saveDatesMutation.isPending}
                        />
                        <div
                          className="gantt-grid-lines relative"
                          style={{ width: unscheduledGridWidth, height: 44 }}
                        >
                          <div
                            className="absolute top-1/2 h-6 -translate-y-1/2 rounded bg-zinc-900/85 dark:bg-zinc-100/90"
                            style={{
                              left: Math.round(sprintStart * PX_PER_DAY),
                              width: Math.max(MIN_SPRINT_BAR_PX, extent * PX_PER_DAY),
                            }}
                            title={`${sprint.name} — ${extent} ${unitLabel}`}
                          />
                        </div>

                        {epics.map(({ epic, children }) => (
                          <Fragment key={epic.id}>
                            <EpicLeftCell ticket={epic} workspaceKey={workspaceKey} />
                            <div className="gantt-grid-lines relative border-b border-black/[.06] dark:border-white/[.06]">
                              <div style={{ width: unscheduledGridWidth, height: 36 }} />
                            </div>
                            {children.map(({ ticket, left }) => (
                              <Fragment key={ticket.id}>
                                <TicketLeftCell
                                  ticket={ticket}
                                  workspaceKey={workspaceKey}
                                  assignee={agentOf(ticket.assignee_id)}
                                  indent
                                />
                                <TicketTimelineCell
                                  ticket={ticket}
                                  left={left}
                                  unitLabel={unitLabel}
                                  gridWidth={unscheduledGridWidth}
                                  workspaceKey={workspaceKey}
                                />
                              </Fragment>
                            ))}
                          </Fragment>
                        ))}

                        {standalone.map(({ ticket, left }) => (
                          <Fragment key={ticket.id}>
                            <TicketLeftCell
                              ticket={ticket}
                              workspaceKey={workspaceKey}
                              assignee={agentOf(ticket.assignee_id)}
                            />
                            <TicketTimelineCell
                              ticket={ticket}
                              left={left}
                              unitLabel={unitLabel}
                              gridWidth={unscheduledGridWidth}
                              workspaceKey={workspaceKey}
                            />
                          </Fragment>
                        ))}
                      </Fragment>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </div>
          )}
        </>
      )}

      {backlogCount > 0 && (
        <p className="text-xs text-zinc-500">
          {backlogCount} tiket belum masuk sprint (tidak ditampilkan di timeline).
        </p>
      )}
    </div>
  );
}

function TodayLine({ x, label }: { x: number; label?: string }) {
  return (
    <div
      className="pointer-events-none absolute inset-y-0 z-10 w-px bg-red-500/70"
      style={{ left: Math.round(x) }}
    >
      {label && (
        <span className="absolute top-0 left-1 whitespace-nowrap text-[9px] font-semibold text-red-500">
          {label}
        </span>
      )}
    </div>
  );
}

function SprintDatePopover({
  sprint,
  onSave,
  saving,
}: {
  sprint: Sprint;
  onSave: (startDate: string | null, endDate: string | null) => void;
  saving: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState<Date | undefined>(parseDateOnly(sprint.start_date) ?? undefined);
  const [end, setEnd] = useState<Date | undefined>(parseDateOnly(sprint.end_date) ?? undefined);

  return (
    <Popover
      open={open}
      onOpenChange={(next: boolean) => {
        setOpen(next);
        if (next) {
          setStart(parseDateOnly(sprint.start_date) ?? undefined);
          setEnd(parseDateOnly(sprint.end_date) ?? undefined);
        }
      }}
    >
      <PopoverTrigger
        render={
          <Button size="sm" variant="outline">
            <CalendarIcon />
            {sprint.start_date && sprint.end_date
              ? `${sprint.start_date} → ${sprint.end_date}`
              : "Atur tanggal"}
          </Button>
        }
      />
      <PopoverContent className="w-auto p-3">
        <div className="flex flex-col gap-3">
          <div className="flex gap-3">
            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium text-zinc-500">Mulai</span>
              <Calendar
                mode="single"
                selected={start}
                onSelect={setStart}
                defaultMonth={start}
              />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium text-zinc-500">Selesai</span>
              <Calendar mode="single" selected={end} onSelect={setEnd} defaultMonth={end ?? start} />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setStart(undefined);
                setEnd(undefined);
              }}
            >
              Bersihkan
            </Button>
            <Button
              size="sm"
              disabled={saving}
              onClick={() => {
                onSave(start ? formatDateOnly(start) : null, end ? formatDateOnly(end) : null);
                setOpen(false);
              }}
            >
              Simpan
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function CompleteSprintButton({ onComplete, completing }: { onComplete: () => void; completing: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" variant="outline" disabled={completing} />}>
        Complete sprint
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Selesaikan sprint ini?</DialogTitle>
          <DialogDescription>
            Tiket yang belum berstatus done/release akan otomatis dipindahkan ke sprint aktif
            lain, atau sprint planned dengan index terkecil, atau ke backlog kalau tidak ada
            sprint lain. Setiap tiket yang dipindah akan mendapat catatan sistem.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Batal
          </Button>
          <Button
            onClick={() => {
              onComplete();
              setOpen(false);
            }}
          >
            Ya, selesaikan
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function GanttSprintLeft({
  sprint,
  epics,
  standalone,
  unitLabel,
  onActivate,
  activating,
  onComplete,
  completing,
  onSaveDates,
  savingDates,
}: {
  sprint: Sprint;
  epics: EpicGroup[];
  standalone: PlacedTicket[];
  unitLabel: string;
  onActivate: () => void;
  activating: boolean;
  onComplete: () => void;
  completing: boolean;
  onSaveDates: (startDate: string | null, endDate: string | null) => void;
  savingDates: boolean;
}) {
  const totalDuration = [...standalone, ...epics.flatMap((g) => g.children)].reduce(
    (sum, { ticket }) => sum + (ticket.duration_estimate ?? 0),
    0,
  );

  return (
    <div className="sticky left-0 z-10 flex items-center justify-between gap-2 border-b border-black/[.06] bg-card px-4 py-1.5 dark:border-white/[.06]">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold whitespace-nowrap">{sprint.name}</span>
          <Badge variant={sprint.status === "active" ? "default" : "outline"}>
            {sprint.status}
          </Badge>
        </div>
        {sprint.goal && (
          <p className="text-xs whitespace-nowrap text-zinc-500">{sprint.goal}</p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2 text-xs text-zinc-500">
        <span className="whitespace-nowrap">
          Total: {totalDuration} {unitLabel}
        </span>
        <SprintDatePopover sprint={sprint} onSave={onSaveDates} saving={savingDates} />
        {sprint.status !== "active" && sprint.status !== "completed" && (
          <Button size="sm" variant="outline" onClick={onActivate} disabled={activating}>
            Mark active
          </Button>
        )}
        {sprint.status !== "completed" && (
          <CompleteSprintButton onComplete={onComplete} completing={completing} />
        )}
      </div>
    </div>
  );
}

function EpicLeftCell({
  ticket,
  workspaceKey,
}: {
  ticket: Ticket;
  workspaceKey: string;
}) {
  return (
    <Link
      href={`/w/${workspaceKey}/ticket/${ticket.key}`}
      className="sticky left-0 z-10 flex items-center gap-2 border-b border-black/[.06] bg-card px-4 hover:bg-accent/40 dark:border-white/[.06]"
    >
      <span className="rounded bg-violet-100 px-1 py-0.5 text-[9px] font-semibold text-violet-700 dark:bg-violet-900/60 dark:text-violet-300">
        EPIC
      </span>
      <span className="font-mono text-xs whitespace-nowrap text-zinc-500">{ticket.key}</span>
      <span className="text-sm font-semibold whitespace-nowrap">{ticket.title}</span>
    </Link>
  );
}

function TicketLeftCell({
  ticket,
  workspaceKey,
  assignee,
  indent,
}: {
  ticket: Ticket;
  workspaceKey: string;
  assignee?: Agent;
  indent?: boolean;
}) {
  return (
    <Link
      href={`/w/${workspaceKey}/ticket/${ticket.key}`}
      className="sticky left-0 z-10 flex items-center gap-2 border-b border-black/[.06] bg-card px-4 hover:bg-accent/40 dark:border-white/[.06]"
    >
      {indent && <span className="ml-5 border-l-2 border-black/10 pl-2 dark:border-white/10" />}
      <span className="font-mono text-xs whitespace-nowrap text-zinc-500">{ticket.key}</span>
      {assignee && (
        <span title={assignee.name} className="shrink-0">
          <AgentAvatar
            name={assignee.name}
            template={assignee.avatar_template}
            color={assignee.avatar_color}
            size={16}
          />
        </span>
      )}
      <span className="text-xs whitespace-nowrap">{ticket.title}</span>
    </Link>
  );
}

function TicketTimelineCell({
  ticket,
  left,
  unitLabel,
  gridWidth,
  workspaceKey,
  todayX,
}: {
  ticket: Ticket;
  left: number;
  unitLabel: string;
  gridWidth: number;
  workspaceKey: string;
  todayX?: number;
}) {
  const color = STATUS_BLOCK_COLOR[ticket.status];

  return (
    <div className="gantt-grid-lines relative border-b border-black/[.06] dark:border-white/[.06]">
      <div style={{ width: gridWidth, height: 36 }}>
        <Tooltip>
          <TooltipTrigger
            render={
              <Link
                href={`/w/${workspaceKey}/ticket/${ticket.key}`}
                className={`absolute top-1/2 block -translate-y-1/2 rounded px-2 py-1 text-xs text-white hover:opacity-90 ${color}`}
                style={{
                  left: Math.round(left),
                  width: blockWidth(ticket.duration_estimate),
                }}
              >
                <span className="block truncate">
                  {ticket.duration_estimate != null
                    ? `${ticket.duration_estimate} ${unitLabel}`
                    : ticket.key}
                </span>
              </Link>
            }
          />
          <TooltipContent>
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <span className={`size-2 rounded-full ${color}`} />
              <span className="font-mono">{ticket.key}</span> · {ticket.status}
            </div>
            <div className="line-clamp-2 font-normal">{ticket.title}</div>
            <div className="font-normal">
              {ticket.duration_estimate != null
                ? `${ticket.duration_estimate} ${unitLabel}`
                : "Tanpa estimasi"}
            </div>
          </TooltipContent>
        </Tooltip>
        {todayX != null && <TodayLine x={todayX} />}
      </div>
    </div>
  );
}
