import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";

/**
 * Lightweight modal for destructive actions. Click outside or Cancel
 * dismisses; Confirm fires `onConfirm(extras)` and the parent decides
 * what to do next.
 *
 * `extras` is the optional opt-in toggles (e.g. "Also clean up Cloud").
 * The parent pulls them out of the callback arg to drive whatever
 * downstream call needs them.
 */
export interface ExtraToggle {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  defaultChecked?: boolean;
}

export function ConfirmDialog({
  open, title, body, confirmLabel = "Delete", danger = true,
  extras = [],
  onConfirm, onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  extras?: ExtraToggle[];
  onConfirm: (toggles: Record<string, boolean>) => void;
  onCancel: () => void;
}) {
  // Local state for the optional toggles. Reset to defaults whenever
  // the dialog opens.
  const [toggles, setToggles] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!open) return;
    setToggles(Object.fromEntries(extras.map((e) => [e.id, !!e.defaultChecked])));
  }, [open, extras]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div onClick={(e) => e.stopPropagation()}>
        <Card className="w-[520px] max-w-[90vw] p-5">
          <div className="flex items-start gap-3 mb-4">
            {danger && <AlertTriangle className="text-[var(--color-warning)] shrink-0 mt-0.5" size={20} />}
            <div>
              <div className="font-medium">{title}</div>
              <div className="text-sm text-[var(--color-text-muted)] mt-2">{body}</div>
            </div>
          </div>

          {extras.length > 0 && (
            <div className="mb-4 space-y-2 pl-7">
              {extras.map((e) => (
                <label key={e.id} className="flex items-start gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={!!toggles[e.id]}
                    onChange={(ev) => setToggles((t) => ({ ...t, [e.id]: ev.target.checked }))}
                    className="mt-0.5 accent-[var(--color-accent)]"
                  />
                  <div>
                    <div>{e.label}</div>
                    {e.hint && <div className="text-xs text-[var(--color-text-faint)] mt-0.5">{e.hint}</div>}
                  </div>
                </label>
              ))}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={onCancel}>Cancel</Button>
            <Button variant={danger ? "danger" : "primary"} onClick={() => onConfirm(toggles)}>
              {confirmLabel}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
