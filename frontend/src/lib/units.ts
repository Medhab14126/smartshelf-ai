// Mirrors backend/app/services/units.py. Quantities count packs; a pack size says what one pack holds.
export const PACK_UNITS = [
  { value: "g", label: "g (grams)" },
  { value: "kg", label: "kg (kilograms)" },
  { value: "ml", label: "ml (millilitres)" },
  { value: "ltr", label: "ltr (litres)" },
] as const;

const ALIASES: Record<string, string> = {
  g: "g", gm: "g", gms: "g", gr: "g", gram: "g", grams: "g",
  kg: "kg", kgs: "kg", kilo: "kg", kilos: "kg",
  ml: "ml", mls: "ml",
  l: "ltr", lt: "ltr", ltr: "ltr", ltrs: "ltr", litre: "ltr", litres: "ltr", liter: "ltr", liters: "ltr",
};

/** Read "500 ml", "1L" or "200 g" from label text; null when no size is printed. */
export function parsePackSize(text: string | null | undefined): { size: string; unit: string } | null {
  const matches = [...(text ?? "").matchAll(/(\d+(?:[.,]\d+)?)\s*([a-z]+)\b/gi)];
  for (const match of matches.reverse()) {
    const unit = ALIASES[match[2].toLowerCase()];
    if (unit) return { size: match[1].replace(",", "."), unit };
  }
  return null;
}

/** "500 ml" from a stored pack size, or "" when unknown. */
export function formatPack(size: string | number | null | undefined, unit: string | null | undefined): string {
  if (size === null || size === undefined || size === "" || !unit) return "";
  return `${Number(size).toLocaleString(undefined, { maximumFractionDigits: 3 })} ${unit}`;
}
