/**
 * CodeBlock, readable monospace surface for logs, JSON, YAML, and any
 * non-trivial code snippet shown to the SE.
 *
 * Why bespoke (not Prism / Shiki):
 *   The runner shows live-streaming logs that change every second; a
 *   tokenizer-based highlighter would re-tokenize on every paint and
 *   tank perf. Most code in this app is already pretty-printed JSON
 *   from the planner; light-touch CSS highlighting (numbers, keywords,
 *   strings via regex post-process) is enough for readability without
 *   the bundle hit. Drop in Shiki later if we add a real source-code
 *   viewer.
 *
 * Features:
 *   • Copy-to-clipboard button (changes to a checkmark for 1.5s).
 *   • Line numbers (toggleable for very long blocks).
 *   • Soft-wrap toggle, defaults to off so log alignment is preserved,
 *     but a single click wraps long lines without re-rendering. Saved
 *     to localStorage so the SE's preference sticks.
 *   • A11y: code is in a `<pre><code>` so screen readers announce it
 *     correctly; the action buttons sit OUTSIDE the code surface so
 *     they don't get read inline.
 */
import { Check, Copy, ListOrdered, WrapText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/cn";

export type CodeBlockProps = {
  /** The code to display, as a single string with newlines. */
  code: string;
  /** Hint for the user (header label), typically the file extension
   *  or a friendly type name like "JSON" / "Log". Decorative. */
  language?: string;
  /** Show line numbers initially. User can toggle. Default true. */
  showLineNumbers?: boolean;
  /** Soft-wrap initial state. User can toggle. Default false. */
  initialWrap?: boolean;
  /** Optional max-height; scrolls inside the block past this. */
  maxHeight?: number | string;
  /** Optional className passed to the outer container. */
  className?: string;
  /** Hide the toolbar entirely (compact embeds, e.g. inside a tooltip). */
  noToolbar?: boolean;
};

const STORAGE_WRAP_KEY = "clarion.codeblock.wrap";
const STORAGE_NUMS_KEY = "clarion.codeblock.numbers";

export function CodeBlock({
  code,
  language,
  showLineNumbers = true,
  initialWrap = false,
  maxHeight = 480,
  className,
  noToolbar = false,
}: CodeBlockProps) {
  // Persist toggles across sessions, once an SE picks "wrap on" they
  // tend to want it everywhere; localStorage is the right grain.
  const [wrap, setWrap] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_WRAP_KEY);
      return stored === null ? initialWrap : stored === "1";
    } catch {
      return initialWrap;
    }
  });
  const [withNumbers, setWithNumbers] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_NUMS_KEY);
      return stored === null ? showLineNumbers : stored === "1";
    } catch {
      return showLineNumbers;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(STORAGE_WRAP_KEY, wrap ? "1" : "0"); } catch { /* localStorage unavailable (private mode/quota); non-fatal */ }
  }, [wrap]);
  useEffect(() => {
    try { localStorage.setItem(STORAGE_NUMS_KEY, withNumbers ? "1" : "0"); } catch { /* localStorage unavailable (private mode/quota); non-fatal */ }
  }, [withNumbers]);

  const [copied, setCopied] = useState(false);
  const lines = useMemo(() => code.split("\n"), [code]);

  async function copyAll() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Some browsers (e.g. iframe contexts without clipboard perms)
      // refuse navigator.clipboard. Fall through silently, the user
      // can select+copy manually as fallback.
    }
  }

  const lineNumWidth = String(lines.length).length;

  return (
    <div
      className={cn(
        "rounded-lg border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] overflow-hidden",
        className,
      )}
    >
      {!noToolbar && (
        <div className="flex items-center gap-1 px-2 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-canvas-elev2)]/40">
          {language && (
            <span className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)] px-1.5">
              {language}
            </span>
          )}
          <span className="ml-auto flex items-center gap-0.5">
            <ToolbarButton
              label={withNumbers ? "Hide line numbers" : "Show line numbers"}
              active={withNumbers}
              onClick={() => setWithNumbers((v) => !v)}
            >
              <ListOrdered size={12} aria-hidden="true" />
            </ToolbarButton>
            <ToolbarButton
              label={wrap ? "Disable wrap" : "Soft wrap"}
              active={wrap}
              onClick={() => setWrap((v) => !v)}
            >
              <WrapText size={12} aria-hidden="true" />
            </ToolbarButton>
            <ToolbarButton
              label={copied ? "Copied!" : "Copy"}
              onClick={copyAll}
            >
              {copied ? (
                <Check size={12} aria-hidden="true" className="text-[var(--color-success)]" />
              ) : (
                <Copy size={12} aria-hidden="true" />
              )}
            </ToolbarButton>
          </span>
        </div>
      )}
      <pre
        className={cn(
          "font-mono text-[13px] leading-[1.55] overflow-auto m-0 p-0",
        )}
        style={{
          maxHeight: typeof maxHeight === "number" ? `${maxHeight}px` : maxHeight,
        }}
      >
        <code className="block">
          {lines.map((line, i) => (
            <span
              key={i}
              className={cn(
                "flex items-baseline px-3",
                i === 0 && "pt-3",
                i === lines.length - 1 && "pb-3",
                "hover:bg-white/[0.02]",
              )}
            >
              {withNumbers && (
                <span
                  aria-hidden="true"
                  className="select-none mr-3 text-[var(--color-text-faint)] tabular-nums shrink-0"
                  style={{ width: `${lineNumWidth}ch`, textAlign: "right" }}
                >
                  {i + 1}
                </span>
              )}
              <span
                className={cn(
                  "flex-1 min-w-0",
                  wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre",
                )}
              >
                {/* Light-touch highlighting: keywords / strings / numbers
                    in JSON-ish content. Pure CSS via tokenize() below. */}
                <HighlightedLine line={line} />
              </span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

function ToolbarButton({
  label, active, onClick, children,
}: {
  label: string;
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        "p-1.5 rounded transition-colors",
        active
          ? "bg-[var(--color-accent-bg)] text-[var(--color-accent)]"
          : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.04]",
      )}
    >
      {children}
    </button>
  );
}

// ─── Light-touch tokenization ──────────────────────────────────────
//
// Regex-based, single pass. Catches the high-value cases: strings (key
// + value), numbers, booleans/null. Not a real lexer, fails on weird
// edge cases (JSON5 trailing commas, multi-line strings) but those are
// rare in our use cases (planner-emitted JSON, structured logs).

function HighlightedLine({ line }: { line: string }) {
  const tokens = useMemo(() => tokenize(line), [line]);
  return (
    <>
      {tokens.map((tok, i) =>
        tok.kind === "plain" ? (
          <span key={i}>{tok.text}</span>
        ) : (
          <span key={i} className={KIND_TO_CLASS[tok.kind]}>
            {tok.text}
          </span>
        ),
      )}
    </>
  );
}

type Token = { kind: "plain" | "string" | "number" | "literal" | "key" | "punct"; text: string };

const KIND_TO_CLASS: Record<Exclude<Token["kind"], "plain">, string> = {
  string:  "text-[var(--color-success)]",
  number:  "text-[var(--color-warning)]",
  literal: "text-[var(--color-info)]",
  key:     "text-[var(--color-accent)]",
  punct:   "text-[var(--color-text-faint)]",
};

const TOKEN_RE = new RegExp(
  [
    `("(?:[^"\\\\]|\\\\.)*"\\s*:)`,        // 1: key
    `("(?:[^"\\\\]|\\\\.)*")`,             // 2: string
    `(-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)`, // 3: number
    `\\b(true|false|null)\\b`,              // 4: literal
    `([{}[\\],:])`,                         // 5: punct
  ].join("|"),
  "g",
);

function tokenize(line: string): Token[] {
  const out: Token[] = [];
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOKEN_RE.exec(line))) {
    if (m.index > lastIndex) {
      out.push({ kind: "plain", text: line.slice(lastIndex, m.index) });
    }
    if (m[1])      out.push({ kind: "key",     text: m[1] });
    else if (m[2]) out.push({ kind: "string",  text: m[2] });
    else if (m[3]) out.push({ kind: "number",  text: m[3] });
    else if (m[4]) out.push({ kind: "literal", text: m[4] });
    else if (m[5]) out.push({ kind: "punct",   text: m[5] });
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < line.length) {
    out.push({ kind: "plain", text: line.slice(lastIndex) });
  }
  return out;
}
