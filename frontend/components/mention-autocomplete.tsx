"use client";

import { useMemo, useState } from "react";

export interface MentionOption {
  id: string;
  label: string;
  sublabel?: string;
  /** Group header shown above the option (e.g. "Agents", "Artifacts", "Tickets"). */
  group: string;
  /** The text inserted into the composer (without the leading @). */
  insert: string;
}

/** How many options per group are shown before the "+N lainnya" expand row. */
const GROUP_LIMIT = 3;

/**
 * @-mention autocomplete for textareas. Shows a dropdown above the caret when the
 * user types `@` followed by characters; click or Tab inserts the highlighted
 * option, ArrowUp/ArrowDown move the selection, Esc closes.
 *
 * Options are grouped by `option.group` (grouped headers, 3 items per group, a
 * "+N lainnya" row expands the group). The component is controlled: `value`/
 * `onChange` own the textarea state, and `onInsert(insert)` is called with the
 * chosen option's `insert` text — the parent replaces the `@query` token and
 * refocuses.
 */
export function MentionAutocomplete({
  value,
  onChange,
  options,
  textareaRef,
  onInsert,
  placeholder,
  disabled,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  options: MentionOption[];
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  onInsert: (insert: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}) {
  const [query, setQuery] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(0);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const groups = useMemo(() => {
    if (query === null) return [];
    const q = query.toLowerCase();
    const byGroup = new Map<string, MentionOption[]>();
    for (const o of options) {
      // Substring match anywhere in the label, not just prefix.
      if (q && !o.label.toLowerCase().includes(q)) continue;
      const list = byGroup.get(o.group);
      if (list) list.push(o);
      else byGroup.set(o.group, [o]);
    }
    return Array.from(byGroup.entries()).map(([name, items]) => ({ name, items }));
  }, [options, query]);

  // Flat list of currently visible items (grouped, limited unless expanded) —
  // keyboard navigation and Tab/Enter operate on this.
  const visibleItems = useMemo(() => {
    const flat: MentionOption[] = [];
    for (const g of groups) {
      const items = expanded.has(g.name) ? g.items : g.items.slice(0, GROUP_LIMIT);
      flat.push(...items);
    }
    return flat;
  }, [groups, expanded]);

  const hiddenCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const g of groups) {
      if (!expanded.has(g.name) && g.items.length > GROUP_LIMIT) {
        counts.set(g.name, g.items.length - GROUP_LIMIT);
      }
    }
    return counts;
  }, [groups, expanded]);

  function handleChange(v: string) {
    onChange(v);
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
    const cursor = el?.selectionStart ?? v.length;
    const upToCursor = v.slice(0, cursor);
    // `@` alone (empty query) opens the dropdown immediately; `@abc` filters.
    const match = upToCursor.match(/@([a-zA-Z0-9][a-zA-Z0-9-]*)?$/);
    if (match) {
      setQuery(match[1] ?? "");
      setHighlight(0);
      setExpanded(new Set());
      setOpen(true);
    } else {
      setQuery(null);
      setOpen(false);
    }
  }

  function insert(option: MentionOption) {
    onInsert(option.insert);
    setQuery(null);
    setOpen(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      if (open && visibleItems.length > 0) {
        // Dropdown open: Enter picks the highlighted mention.
        e.preventDefault();
        insert(visibleItems[highlight]);
        return;
      }
      // Dropdown closed: Enter sends the message (Shift+Enter = newline).
      e.preventDefault();
      e.currentTarget.form?.requestSubmit();
      return;
    }
    if (!open || visibleItems.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (h + 1) % visibleItems.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (h - 1 + visibleItems.length) % visibleItems.length);
    } else if (e.key === "Tab") {
      e.preventDefault();
      insert(visibleItems[highlight]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      setQuery(null);
    }
  }

  function expandGroup(name: string) {
    setExpanded((prev) => new Set(prev).add(name));
  }

  return (
    <div className="relative min-w-0 flex-1">
      <textarea
        ref={textareaRef}
        value={value}
        rows={1}
        onChange={(e) => handleChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        className={`w-full ${className ?? ""}`}
      />
      {open && visibleItems.length > 0 && (
        <div className="absolute bottom-full left-0 z-20 mb-1 max-h-72 w-72 overflow-y-auto rounded-md border border-zinc-200 bg-white py-1 shadow-md dark:border-zinc-800 dark:bg-zinc-900">
          {groups.map((g) => {
            const items = expanded.has(g.name) ? g.items : g.items.slice(0, GROUP_LIMIT);
            const hidden = hiddenCounts.get(g.name) ?? 0;
            return (
              <div key={g.name}>
                <p className="px-3 pt-1.5 pb-0.5 text-[10px] font-semibold tracking-wide text-zinc-400 uppercase">
                  {g.name}
                </p>
                {items.map((o) => {
                  const flatIndex = visibleItems.indexOf(o);
                  return (
                    <button
                      key={o.id}
                      type="button"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        insert(o);
                      }}
                      onMouseEnter={() => setHighlight(flatIndex)}
                      className={`block w-full px-3 py-1.5 text-left text-sm ${
                        flatIndex === highlight
                          ? "bg-zinc-100 dark:bg-zinc-800"
                          : "hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
                      }`}
                    >
                      <span className="font-medium">{o.label}</span>
                      {o.sublabel && (
                        <span className="ml-1.5 text-xs text-zinc-400">{o.sublabel}</span>
                      )}
                    </button>
                  );
                })}
                {hidden > 0 && (
                  <button
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      expandGroup(g.name);
                    }}
                    className="block w-full px-3 py-1 text-left text-xs text-zinc-500 hover:bg-zinc-50 hover:text-zinc-700 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-300"
                  >
                    +{hidden} lainnya…
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
