/** Types for `maplabels.mjs`. Must be `.d.mts` for an explicit `.mjs` import. */

export interface MapLabelCandidate {
  text: string;
  x: number;
  y: number;
  fontSize: number;
  gap?: number;
  /** Drawn even where it collides: the one place that must be named. */
  keep?: boolean;
  [key: string]: unknown;
}

export interface LabelBox {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface PlacedMapLabel extends MapLabelCandidate {
  side: "left" | "right";
  box: LabelBox;
}

export function estimateWidth(text: string, fontSize: number): number;
export function labelBox(label: MapLabelCandidate, side: "left" | "right"): LabelBox;
export function placeLabels(
  labels: MapLabelCandidate[],
  options?: { width?: number; height?: number; margin?: number },
): PlacedMapLabel[];
