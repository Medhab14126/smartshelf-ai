import { CalendarDays } from "lucide-react";
import * as React from "react";

import { Input } from "@/components/ui/input";
import { cn, formatDate, isIsoDate, parseDayFirstDate } from "@/lib/utils";

const toText = (value: string) => (isIsoDate(value) ? formatDate(value) : value);

/**
 * Date field that always reads and writes dd/mm/yyyy.
 *
 * The native `type="date"` input follows the OS locale (mm/dd/yyyy on many machines), which is easy to misread for
 * expiry dates. `value` and `onChange` use ISO `yyyy-mm-dd`; text that isn't a valid date is passed through unchanged
 * so callers can block saving with `isIsoDate()` instead of silently dropping what was typed.
 */
export function DateInput({
  id,
  value,
  onChange,
  disabled,
  className,
  "aria-invalid": ariaInvalid,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  "aria-invalid"?: boolean;
}) {
  const [text, setText] = React.useState(() => toText(value));
  const [touched, setTouched] = React.useState(false);
  const picker = React.useRef<HTMLInputElement>(null);

  // Follow changes made outside this field (form reset, choosing a suggested reading).
  React.useEffect(() => {
    setText((current) => ((parseDayFirstDate(current) ?? current) === value ? current : toText(value)));
  }, [value]);

  const invalid = text.trim() !== "" && parseDayFirstDate(text) === null;
  const update = (next: string) => {
    setText(next);
    onChange(next.trim() === "" ? "" : (parseDayFirstDate(next) ?? next));
  };

  return (
    <div className={cn("relative", className)}>
      <Input
        id={id}
        value={text}
        placeholder="dd/mm/yyyy"
        inputMode="numeric"
        autoComplete="off"
        disabled={disabled}
        aria-invalid={ariaInvalid || (touched && invalid)}
        aria-describedby={touched && invalid ? `${id}-format` : undefined}
        onChange={(e) => update(e.target.value)}
        onBlur={() => {
          setTouched(true);
          const iso = parseDayFirstDate(text);
          if (iso) setText(formatDate(iso)); // normalise "1/2/26" to "01/02/2026"
        }}
        className="pr-10"
      />
      <button
        type="button"
        disabled={disabled}
        aria-label="Choose from calendar"
        onClick={() => picker.current?.showPicker?.()}
        className="absolute right-1 top-1 flex size-8 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none"
      >
        <CalendarDays className="size-4" aria-hidden />
      </button>
      {/* Hidden native input only supplies the calendar popup; its value is ISO regardless of locale. */}
      <input
        ref={picker}
        type="date"
        tabIndex={-1}
        aria-hidden
        className="pointer-events-none absolute bottom-0 right-0 size-px opacity-0"
        value={isIsoDate(value) ? value : ""}
        onChange={(e) => {
          setTouched(false);
          update(e.target.value ? formatDate(e.target.value) : "");
        }}
      />
      {touched && invalid && (
        <p id={`${id}-format`} className="mt-1 text-xs text-destructive">
          Enter the date as dd/mm/yyyy
        </p>
      )}
    </div>
  );
}
