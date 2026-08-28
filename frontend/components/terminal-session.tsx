"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { terminalWebSocketUrl } from "@/lib/api";

/** Keeps one PTY/xterm session alive for the lifetime of the workspace layout —
 * mounted here (not in the /terminal page itself) so navigating to another page
 * and back doesn't tear down and respawn the shell. Rendered as a fixed overlay
 * that's shoved off-screen (never unmounted) when the owner isn't on the Terminal
 * page, so xterm keeps its DOM/measurements and the WebSocket stays open.
 */
export function TerminalSession({
  workspaceId,
  workspaceKey,
}: {
  workspaceId: string | undefined;
  workspaceKey: string | undefined;
}) {
  const pathname = usePathname();
  const isVisible = !!workspaceKey && pathname === `/w/${workspaceKey}/terminal`;

  const hostRef = useRef<HTMLDivElement>(null);
  const startedRef = useRef(false);
  const termRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);

  // Starts the session exactly once, the first time the owner opens the Terminal
  // page for this workspace. Guarded by startedRef (not by effect deps) so this
  // body never re-runs on later isVisible flips — only the real unmount effect
  // below tears anything down.
  useEffect(() => {
    if (!isVisible || !workspaceId || startedRef.current || !hostRef.current) return;
    startedRef.current = true;

    const term = new Terminal({ convertEol: true, cursorBlink: true });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(hostRef.current);
    fitAddon.fit();
    term.focus();
    termRef.current = term;
    fitAddonRef.current = fitAddon;

    const ws = new WebSocket(terminalWebSocketUrl(workspaceId));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      fitAddon.fit();
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };
    ws.onmessage = (evt) => {
      term.write(new Uint8Array(evt.data as ArrayBuffer));
    };

    const encoder = new TextEncoder();
    term.onData((data) => {
      // Must be a BINARY frame — the backend treats text frames as JSON control
      // messages only (resize).
      if (ws.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
    });

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    });
    resizeObserver.observe(hostRef.current);
    resizeObserverRef.current = resizeObserver;
  }, [isVisible, workspaceId]);

  // Re-fit and refocus whenever the owner comes back to the Terminal page —
  // dimensions can be stale after sitting shoved off-screen.
  useEffect(() => {
    if (!isVisible || !termRef.current || !fitAddonRef.current) return;
    fitAddonRef.current.fit();
    termRef.current.focus();
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({ type: "resize", cols: termRef.current.cols, rows: termRef.current.rows }),
      );
    }
  }, [isVisible]);

  // Teardown — fires on real unmount (leaving the workspace) AND on React 19 dev
  // Strict Mode's synthetic mount→cleanup→mount pass (which always runs this once
  // right after the very first mount, to catch missing-cleanup bugs). Resetting
  // startedRef/the instance refs here means that synthetic pass correctly leads
  // to a fresh session on the immediate remount, instead of leaving startedRef
  // permanently "true" with no live session behind it (dead terminal, and each
  // dev Fast Refresh repeating this cycle was hammering the backend).
  useEffect(() => {
    return () => {
      resizeObserverRef.current?.disconnect();
      wsRef.current?.close();
      termRef.current?.dispose();
      resizeObserverRef.current = null;
      wsRef.current = null;
      termRef.current = null;
      fitAddonRef.current = null;
      startedRef.current = false;
    };
  }, []);

  return (
    <div
      style={{
        position: "fixed",
        top: "3.5rem",
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 10,
        // visibility (not moving the box off-screen): the box must keep its real
        // on-screen geometry at all times, or xterm/FitAddon measure it as 0-size
        // and — worse — a fixed box with both top and bottom set still covers the
        // full viewport even when "pushed" way above it, since bottom stays
        // pinned to 0 (that bug is what caused the black full-page overlay).
        visibility: isVisible ? "visible" : "hidden",
        pointerEvents: isVisible ? "auto" : "none",
      }}
      className="flex flex-col gap-2 bg-background p-4"
    >
      <h1 className="text-2xl font-semibold tracking-tight">Terminal</h1>
      <div
        ref={hostRef}
        onClick={() => termRef.current?.focus()}
        className="min-h-0 flex-1 rounded-md bg-black p-2"
      />
    </div>
  );
}
