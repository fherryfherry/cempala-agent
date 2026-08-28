// The actual terminal UI is rendered by <TerminalSession> in the workspace
// layout (frontend/app/w/[key]/layout.tsx) as a persistent fixed overlay, so the
// PTY/WebSocket session survives navigating away from this route and back. This
// page only needs to exist so the /terminal URL resolves.
export default function TerminalPage() {
  return null;
}
