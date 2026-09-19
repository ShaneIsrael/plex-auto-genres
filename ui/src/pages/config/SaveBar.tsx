import { Check, Loader2, Save, Undo2 } from "lucide-react";
import type { ConfigDocument, ValidationIssue } from "../../api/types";
import { describeLoc } from "./editor";

export type ValidationStatus = "idle" | "validating" | "valid" | "invalid";

export function SaveBar({
  doc,
  status,
  errors,
  saving,
  onSave,
  onDiscard,
}: {
  doc: ConfigDocument;
  status: ValidationStatus;
  errors: ValidationIssue[];
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
}) {
  const first = errors[0];
  return (
    <div className="savebar" role="region" aria-label="Unsaved changes">
      <div className="savebar__status" aria-live="polite">
        {status === "validating" || saving ? (
          <>
            <Loader2 size={14} className="spin" aria-hidden="true" />
            <span className="muted">{saving ? "Saving…" : "Checking…"}</span>
          </>
        ) : status === "invalid" && first ? (
          <>
            <span className="tone-fail">{errors.length} problem{errors.length > 1 ? "s" : ""}</span>
            <span className="savebar__problem" title={`${describeLoc(first.loc, doc)}: ${first.msg}`}>
              {describeLoc(first.loc, doc)}: {first.msg}
            </span>
          </>
        ) : (
          <>
            <Check size={14} className="tone-ok" aria-hidden="true" />
            <span>Unsaved changes</span>
            <span className="faint">valid</span>
          </>
        )}
      </div>
      <div className="savebar__actions">
        <button type="button" className="button button--ghost" onClick={onDiscard} disabled={saving}>
          <Undo2 size={14} aria-hidden="true" /> Discard
        </button>
        <button type="button" className="button" onClick={onSave} disabled={saving || status !== "valid"}>
          <Save size={14} aria-hidden="true" /> Save
        </button>
      </div>
    </div>
  );
}
