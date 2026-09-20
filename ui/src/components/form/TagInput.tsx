import { X } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent } from "react";

/**
 * Chips plus a text box. Enter, comma or blur adds; Backspace on an empty box
 * removes the last chip. Case-insensitive de-duplication, since every consumer
 * matches genres that way too.
 */
export function TagInput({
  id,
  value,
  onChange,
  placeholder = "add…",
  suggestions,
  describedBy,
}: {
  id: string;
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
  describedBy?: string;
}) {
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();

  const commit = (raw: string) => {
    const parts = raw.split(",").map((p) => p.trim()).filter(Boolean);
    if (!parts.length) return;
    const seen = new Set(value.map((v) => v.toLowerCase()));
    const added = parts.filter((p) => !seen.has(p.toLowerCase()) && (seen.add(p.toLowerCase()), true));
    if (added.length) onChange([...value, ...added]);
    setText("");
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit(text);
    } else if (e.key === "Backspace" && text === "" && value.length) {
      onChange(value.slice(0, -1));
    }
  };

  return (
    <div className="tags" onClick={() => input.current?.focus()}>
      {value.map((tag, i) => (
        <span key={`${tag}-${i}`} className="tags__chip">
          <span>{tag}</span>
          <button
            type="button"
            className="tags__remove"
            aria-label={`Remove ${tag}`}
            onClick={(e) => {
              e.stopPropagation();
              onChange(value.filter((_, j) => j !== i));
            }}
          >
            <X size={12} aria-hidden="true" />
          </button>
        </span>
      ))}
      <input
        ref={input}
        id={id}
        className="tags__input"
        value={text}
        placeholder={value.length ? "" : placeholder}
        list={suggestions ? listId : undefined}
        aria-describedby={describedBy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        onBlur={() => commit(text)}
        autoComplete="off"
      />
      {suggestions && (
        <datalist id={listId}>
          {suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      )}
    </div>
  );
}
