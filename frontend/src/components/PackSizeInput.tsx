import { Input, Select } from "@/components/ui/input";
import { PACK_UNITS } from "@/lib/units";

export type PackSize = { size: string; unit: string };

export const emptyPack: PackSize = { size: "", unit: "" };

/** Both parts or neither: the backend rejects a size without a unit. */
export const packInvalid = (pack: PackSize) => (pack.size.trim() !== "") !== (pack.unit !== "") || (pack.size !== "" && !(Number(pack.size) > 0));

export const packPayload = (pack: PackSize) =>
  pack.size.trim() && pack.unit ? { pack_size: Number(pack.size), pack_unit: pack.unit } : { pack_size: null, pack_unit: null };

/** What one pack holds: a value box plus g/kg/ml/ltr. The quantity field counts packs. */
export function PackSizeInput({ id, value, onChange }: { id: string; value: PackSize; onChange: (value: PackSize) => void }) {
  const invalid = packInvalid(value);
  return (
    <div>
      <div className="grid grid-cols-[1fr_1.4fr] gap-2">
        <Input
          id={id}
          type="number"
          inputMode="decimal"
          step="any"
          min="0"
          placeholder="e.g. 500"
          value={value.size}
          aria-invalid={invalid}
          onChange={(e) => onChange({ ...value, size: e.target.value })}
        />
        <Select aria-label="Pack size unit" value={value.unit} aria-invalid={invalid} onChange={(e) => onChange({ ...value, unit: e.target.value })}>
          <option value="">Unit</option>
          {PACK_UNITS.map((unit) => (
            <option key={unit.value} value={unit.value}>
              {unit.label}
            </option>
          ))}
        </Select>
      </div>
      {invalid && <p className="mt-1 text-xs text-destructive">Enter both the value and the unit, or leave both empty.</p>}
    </div>
  );
}
