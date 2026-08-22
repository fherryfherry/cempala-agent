export function Header() {
  return (
    <header className="border-b border-black/[.08] dark:border-white/[.145]">
      <div className="mx-auto flex h-14 max-w-5xl items-center px-6">
        <span className="font-semibold tracking-tight">Multi-Agent Portal</span>
        {/* Workspace switcher lands here in MAP-013/014. */}
      </div>
    </header>
  );
}
