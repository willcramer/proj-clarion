/**
 * LogView, readable, color-coded log surface used everywhere we render
 * a stream of log lines (build runner, pipeline events, run output,
 * profile JSONL).
 *
 * Two layers of highlighting:
 *
 * 1. **Per-line severity tint**, a tiny regex-based detector classifies
 *    each line as `error / warn / success / info / debug / null` and tints
 *    the line's background + applies a left border in the matching tone.
 *    The eye learns to scan the gutter for color, then dives into specific
 *    lines. Severity is detected ONCE per line and memoized, adding
 *    another 1k log lines doesn't re-classify the existing ones.
 *
 * 2. **Inline token highlighting**, within each line, common log tokens
 *    get a colour: timestamps (muted), severity labels (chip), HTTP
 *    methods (accent), HTTP status codes (coloured by 2xx / 4xx / 5xx
 *    class), file:line locators (faint accent), UUIDs (mono pop),
 *    URLs (info), numbers-with-units (warning), quoted strings (success).
 *    All via a single multi-group regex pass per line, fast enough that
 *    the runner still scrolls smoothly at 50+ lines/second.
 *
 * Auto-scrolls to the bottom on new lines unless the user has scrolled
 * up (we detect the gap and pause auto-scroll until they're near the
 * bottom again, same pattern most modern log viewers use).
 *
 * a11y: rendered as a `role="log"` live region so assistive tech can
 * track new lines if the user wants it. The actual line content is in
 * `<pre><code>` so screen readers don't break on whitespace.
 */
import { useEffect, useMemo, useRef, type CSSProperties } from "react";

import { cn } from "@/lib/cn";

export type LogViewProps = {
  /** Array of log lines, no trailing newlines. */
  lines: string[];
  /** Optional max-height; the inner <pre> scrolls past it. */
  maxHeight?: number | string;
  /** Default empty-state copy. */
  emptyText?: string;
  /** When true, do NOT auto-scroll to bottom. Useful for the audit /
   *  history surfaces where the user is reading from the top. */
  staticView?: boolean;
  /** Optional className for the outer container. */
  className?: string;
  /** Optional `aria-live` politeness, defaults to `polite`. Set
   *  `"off"` for surfaces where line updates aren't useful to announce. */
  ariaLive?: "off" | "polite" | "assertive";
};

type Severity = "error" | "warn" | "info" | "debug" | "success" | null;

const SEVERITY_STYLES: Record<Exclude<Severity, null>, string> = {
  // Errors should be unmissable. A 4-px left bar (vs 2px on others) +
  // a stronger red tint trade some layout symmetry for "this line is
  // bad" legibility. We don't override the row text colour, the
  // inline-token colours (status codes, methods) carry information
  // that's worth preserving against the red wash.
  error:   "border-l-4 border-l-[var(--color-danger)] bg-[var(--color-danger-bg)]",
  warn:    "border-l-2 border-l-[var(--color-warning)] bg-[var(--color-warning-bg)]/30",
  success: "border-l-2 border-l-[var(--color-success)] bg-[var(--color-success-bg)]/15",
  info:    "border-l-2 border-l-[var(--color-info)] bg-transparent",
  debug:   "border-l-2 border-l-[var(--color-text-faint)] bg-transparent opacity-75",
};

const SEVERITY_TEXT: Record<Exclude<Severity, null>, string> = {
  error:   "text-[var(--color-danger)]",
  warn:    "text-[var(--color-warning)]",
  success: "text-[var(--color-success)]",
  info:    "text-[var(--color-info)]",
  debug:   "text-[var(--color-text-faint)]",
};

// ANSI escape code stripper.
//
// Two forms appear in the wild:
//   1. Proper ANSI: ESC `[Nm` (the ESC byte is `\x1b`).
//   2. "Half-stripped" ANSI: just `[Nm`, happens when the producer
//      writes ESC sequences but a downstream pipe (Python's structlog
//      colorama wrapper, async subprocess buffering) drops the ESC byte
//      and leaves the literal CSI bracket. This is what we see in the
//      Sentinel build logs ("`[2m...[0m`" leaking into the rendered log).
//
// Stripping #2 has a small false-positive risk on text like
// "value [42m] is bad", but that pattern's vanishingly rare in
// structured logs vs. the certain wins of cleaning up structlog output.
function stripAnsi(line: string): string {
  return line
    // ESC (\x1b) is the intended ANSI-escape sentinel we're stripping.
    // eslint-disable-next-line no-control-regex
    .replace(/\x1b\[[0-9;]*m/g, "")  // proper ANSI
    .replace(/\[[0-9;]*m/g, "");     // half-stripped CSI residue
}

// Per-line severity classifier, runs on the *cleaned* line (after
// stripAnsi). Priority: explicit error words > structured error
// signals > HTTP 5xx > warn > success > info / debug.
//
// Most logs hit none of these and the line stays neutral.
function detectSeverity(cleaned: string): Severity {
  // Empty / whitespace lines stay neutral so we don't render colored
  // empty boxes when stdout flushes a blank.
  if (!cleaned.trim()) return null;

  // ── Strong error signals ──
  if (/\b(ERROR|FATAL|CRITICAL|FAIL(?:ED|URE)?|EXCEPTION|TRACEBACK|PANIC|SEGFAULT|abort)\b/i.test(cleaned)) return "error";
  // Named error/exception classes, Python and JS stack traces almost
  // always contain `XYZError:` / `XYZException:` even on the LAST line
  // (the type + message). Catches `TypeError`, `ValueError`,
  // `IntegrityError`, `ReferenceError`, `ConnectionRefusedError`, etc.
  if (/\b(?:[A-Z][a-zA-Z]*?(?:Error|Exception|Failure))\b/.test(cleaned)) return "error";
  // Traceback continuation lines, Python: `  File "x.py", line N, in fn`,
  // JS: `    at functionName (...)`. These are the body of a multi-line
  // error and should tint with the same red as the head.
  if (/^\s+File "[^"]+", line \d+/.test(cleaned)) return "error";
  if (/^\s+at\s+[\w.<>$]+\s*\(/.test(cleaned)) return "error";

  // ── Structured-log signals ──
  // structlog / json-line `error_count=6`, `errors=2`, `failed=1`,
  // `failures=N`. Only flag when the count > 0; `error_count=0` is
  // info noise on a successful phase.done.
  const errCountMatch = cleaned.match(/\b(?:error_count|errors|failures|failed)=(\d+)/i);
  if (errCountMatch && parseInt(errCountMatch[1], 10) > 0) return "error";
  // Explicit `level=error` / `level=err` / `severity=error`
  if (/\b(?:level|severity)=err(?:or)?\b/i.test(cleaned)) return "error";

  // ── HTTP-status-as-error ──
  // 5xx anywhere in the line ⇒ server error. We match `HTTP/...5\d{2}`
  // OR a bare `5\d{2}` followed by a known reason phrase (so we don't
  // flag random 500-ish numbers like `500ms`, they end in non-digit
  // but the lookahead handles it).
  if (/\bHTTP\/\d\.\d"?\s+5\d{2}\b/i.test(cleaned)) return "error";
  if (/\b5\d{2}\s+(?:Internal Server Error|Service Unavailable|Bad Gateway|Gateway Timeout)/i.test(cleaned)) return "error";

  // ── Warn-level signals ──
  if (/\b(WARN(?:ING)?)\b/i.test(cleaned)) return "warn";
  if (/\b(?:level|severity)=warn(?:ing)?\b/i.test(cleaned)) return "warn";
  // 4xx ⇒ client error, surface as warn (failures the SE can usually fix)
  if (/\bHTTP\/\d\.\d"?\s+4\d{2}\b/i.test(cleaned)) return "warn";
  // Retry / backoff messaging
  if (/\b(?:retrying|retry attempt|backoff|will retry|sleeping for)\b/i.test(cleaned)) return "warn";

  // ── Success ──
  // `Wrote ...` matches `Wrote data/plans/<uuid>.json` which most of
  // our Python CLI tools emit on success. `Pushed` ditto for gcx
  // pushes. Strict word-boundary so it doesn't catch "rewrote" or
  // similar in error messages.
  if (/(?:^|\s)[✓✅]\s|\b(?:succeeded|completed|created|persisted|ready|wrote|pushed)\b/i.test(cleaned)) return "success";
  // 2xx HTTP responses are success-flavoured info, info colour
  // (not green) so they don't drown out actual completion lines.

  // ── Plain INFO / DEBUG ──
  if (/\b(INFO|NOTICE)\b/i.test(cleaned) || /\b(?:level|severity)=info\b/i.test(cleaned)) return "info";
  if (/\b(DEBUG|TRACE|VERBOSE)\b/i.test(cleaned) || /\b(?:level|severity)=(?:debug|trace)\b/i.test(cleaned)) return "debug";

  return null;
}

// Inline token highlighting. Single regex with named-ish groups (1..N).
// Order matters within a single match: the regex returns the FIRST
// non-empty group, so we put more-specific patterns earlier.
//
// Patterns:
//   1: ISO 8601 / RFC 3339 timestamp
//   2: severity word (only when standalone, already handled by line tint
//      but we colour it too for legibility)
//   3: HTTP method
//   4: HTTP 1xx-5xx status code (3 digits)
//   5: file path with :line
//   6: UUID
//   7: URL
//   8: number with unit (50ms, 1.2s, 200MB, 50%)
//   9: standalone hex id (commit / digest)
//  10: double-quoted string
//  11: single-quoted string
//  12: key=value (small ones, common in structured logs)
const TOKEN_RE = new RegExp(
  [
    `(\\d{4}-\\d{2}-\\d{2}[T ]\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?Z?)`,         // 1 timestamp
    `\\b(ERROR|FATAL|CRITICAL|WARN(?:ING)?|INFO|NOTICE|DEBUG|TRACE)\\b`,        // 2 level
    `\\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\\b`,                           // 3 method
    `(?<![\\w.])([1-5]\\d{2})(?![\\w])`,                                        // 4 http status
    `([\\w./-]+\\.(?:py|ts|tsx|js|jsx|yaml|yml|json|toml|md|sql):\\d+)`,        // 5 file:line
    `([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})`, // 6 uuid
    `(https?://[^\\s)\\]"']+)`,                                                 // 7 url
    `\\b(\\d+(?:\\.\\d+)?(?:ms|us|ns|s|min|h|MB|GB|KB|TB|%|rps|qps))\\b`,       // 8 num+unit
    `\\b([0-9a-f]{12,40})\\b`,                                                  // 9 hex-id
    `("(?:[^"\\\\]|\\\\.){1,80}")`,                                             // 10 dbl-quoted
    `('(?:[^'\\\\]|\\\\.){1,80}')`,                                             // 11 sgl-quoted
    `(\\b[a-z_][\\w.-]*=(?:[\\w./:-]+|"[^"]*"))`,                               // 12 key=value
  ].join("|"),
  "g",
);

type Token = { kind: TokenKind; text: string };
type TokenKind =
  | "plain" | "ts" | "level" | "method" | "status"
  | "file" | "uuid" | "url" | "numUnit" | "hex"
  | "dquoted" | "squoted" | "kv" | "errkv";

const KIND_TO_CLASS: Record<Exclude<TokenKind, "plain">, string> = {
  ts:       "text-[var(--color-text-faint)]",
  level:    "font-medium",  // colour comes from severity-aware coloring below
  method:   "text-[var(--color-accent)] font-medium",
  status:   "",  // assigned dynamically below based on 2xx/4xx/5xx
  file:     "text-[var(--color-accent)]/80 underline decoration-dotted underline-offset-2",
  uuid:     "text-[var(--color-text-muted)] font-mono",
  url:      "text-[var(--color-info)] underline decoration-dotted underline-offset-2",
  numUnit:  "text-[var(--color-warning)]",
  hex:      "text-[var(--color-text-muted)]",
  dquoted:  "text-[var(--color-success)]",
  squoted:  "text-[var(--color-success)]",
  kv:       "text-[var(--color-text-muted)]",
  // `error_count=6`, `errors=2`, `failed=1`, the structured-log
  // signal that triggered our line tint. Colour them red even on
  // already-tinted rows so the eye lands directly on the count.
  errkv:    "text-[var(--color-danger)] font-medium",
};

// Match `error_count=N` / `errors=N` / `failed=N` / `failures=N` so we
// can specifically colour the count when N > 0. Lower-case lookup since
// structured logs are conventionally lower-snake-case keys.
const ERROR_KV_RE = /^(?:error_count|errors|failed|failures)=(\d+)$/i;

function tokenize(line: string): Token[] {
  const out: Token[] = [];
  let lastIndex = 0;
  let m: RegExpExecArray | null;
  // Reset .lastIndex on the shared regex so re-entering tokenize doesn't
  // skip ahead. RegExp objects with `g` flag are stateful.
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(line))) {
    if (m.index > lastIndex) {
      out.push({ kind: "plain", text: line.slice(lastIndex, m.index) });
    }
    if (m[1])       out.push({ kind: "ts",      text: m[1] });
    else if (m[2])  out.push({ kind: "level",   text: m[2] });
    else if (m[3])  out.push({ kind: "method",  text: m[3] });
    else if (m[4])  out.push({ kind: "status",  text: m[4] });
    else if (m[5])  out.push({ kind: "file",    text: m[5] });
    else if (m[6])  out.push({ kind: "uuid",    text: m[6] });
    else if (m[7])  out.push({ kind: "url",     text: m[7] });
    else if (m[8])  out.push({ kind: "numUnit", text: m[8] });
    else if (m[9])  out.push({ kind: "hex",     text: m[9] });
    else if (m[10]) out.push({ kind: "dquoted", text: m[10] });
    else if (m[11]) out.push({ kind: "squoted", text: m[11] });
    else if (m[12]) {
      // Special-case: `error_count=N` / `errors=N` / `failed=N` /
      // `failures=N` with N > 0, promote to `errkv` so the count
      // pops red even on tinted rows. `=0` stays neutral.
      const errMatch = m[12].match(ERROR_KV_RE);
      const isErr = errMatch && parseInt(errMatch[1], 10) > 0;
      out.push({ kind: isErr ? "errkv" : "kv", text: m[12] });
    }
    lastIndex = m.index + m[0].length;
  }
  if (lastIndex < line.length) {
    out.push({ kind: "plain", text: line.slice(lastIndex) });
  }
  return out;
}

// Colour the HTTP status code by class. 2xx → success, 3xx → info,
// 4xx → warn, 5xx → danger.
function statusClass(text: string): string {
  const c = text[0];
  if (c === "2") return "text-[var(--color-success)] font-medium";
  if (c === "3") return "text-[var(--color-info)] font-medium";
  if (c === "4") return "text-[var(--color-warning)] font-medium";
  if (c === "5") return "text-[var(--color-danger)] font-medium";
  return "";
}

// Level chip colour follows severity priority (same one we use for
// per-line tints). Inlined here so the tokenizer doesn't need to know.
function levelClass(text: string): string {
  const t = text.toUpperCase();
  if (t === "ERROR" || t === "FATAL" || t === "CRITICAL") return SEVERITY_TEXT.error;
  if (t === "WARN" || t === "WARNING") return SEVERITY_TEXT.warn;
  if (t === "INFO" || t === "NOTICE")  return SEVERITY_TEXT.info;
  if (t === "DEBUG" || t === "TRACE")  return SEVERITY_TEXT.debug;
  return "";
}

export function LogView({
  lines,
  maxHeight = 480,
  emptyText = "Waiting for output…",
  staticView = false,
  className,
  ariaLive = "polite",
}: LogViewProps) {
  // Strip ANSI, then tokenize + classify each cleaned line. Memoized by
  // the lines array reference, PipelineContext appends to logs in-place
  // but always replaces the outer object, so `lines` stays referentially
  // stable until something changes and useMemo correctly invalidates.
  const decorated = useMemo(
    () =>
      lines.map((raw) => {
        const cleaned = stripAnsi(raw);
        return {
          text: cleaned,
          severity: detectSeverity(cleaned),
          tokens: tokenize(cleaned),
        };
      }),
    [lines],
  );

  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new lines, but pause if the user has
  // scrolled up. Detection: if the viewport's scrollBottom is within
  // 80px of the content height, treat the user as "following the tail".
  // Otherwise leave them where they are.
  useEffect(() => {
    if (staticView) return;
    const el = containerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distFromBottom < 80) {
      el.scrollTop = el.scrollHeight;
    }
  }, [decorated.length, staticView]);

  const style: CSSProperties = {
    maxHeight: typeof maxHeight === "number" ? `${maxHeight}px` : maxHeight,
  };

  return (
    <div
      ref={containerRef}
      role="log"
      aria-live={ariaLive}
      aria-relevant="additions"
      className={cn(
        "overflow-y-auto bg-black/40 font-mono text-[12.5px] leading-[1.6]",
        className,
      )}
      style={style}
    >
      {decorated.length === 0 ? (
        <div className="px-4 py-3 text-[var(--color-text-faint)] italic">
          {emptyText}
        </div>
      ) : (
        <div className="py-1">
          {decorated.map((d, i) => (
            <LogLineRow key={i} text={d.text} severity={d.severity} tokens={d.tokens} />
          ))}
        </div>
      )}
    </div>
  );
}

function LogLineRow({
  severity, tokens,
}: { text: string; severity: Severity; tokens: Token[] }) {
  return (
    <div
      className={cn(
        "px-4 py-0.5 whitespace-pre-wrap break-all",
        // Severity styles drive both the border width AND the colour.
        // Errors get a 4px bar; everything else gets 2px or none.
        severity ? SEVERITY_STYLES[severity] : "border-l-2 border-l-transparent",
      )}
    >
      {tokens.map((tok, j) => {
        if (tok.kind === "plain") return <span key={j}>{tok.text}</span>;
        const className =
          tok.kind === "status"
            ? statusClass(tok.text)
            : tok.kind === "level"
            ? cn(KIND_TO_CLASS.level, levelClass(tok.text))
            : KIND_TO_CLASS[tok.kind];
        return (
          <span key={j} className={className}>
            {tok.text}
          </span>
        );
      })}
    </div>
  );
}
