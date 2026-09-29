import type { CSSProperties } from "react";
import type { LucideIcon } from "lucide-react";

/** The system's one stroke weight, from the design bundle. */
const STROKE = 1.75;

export function Icon({
  as: Glyph,
  size = 16,
  style,
}: {
  as: LucideIcon;
  size?: number;
  style?: CSSProperties;
}) {
  return (
    <Glyph
      size={size}
      strokeWidth={STROKE}
      aria-hidden="true"
      style={{ flex: "0 0 auto", ...style }}
    />
  );
}
