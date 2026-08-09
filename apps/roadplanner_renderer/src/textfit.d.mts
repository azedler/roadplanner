/** Types for `textfit.mjs`. Must be `.d.mts` for an explicit `.mjs` import. */

export const FRAME_WIDTH: number;
export const FRAME_HEIGHT: number;
export const SAFE_BOTTOM: number;
export const SAFE_SIDE: number;
export const SAFE_TOP: number;
export const MAX_TEXT_WIDTH: number;

export interface FitBox {
  width?: number;
  maxLines?: number;
  maxFontSize?: number;
  minFontSize?: number;
  weight?: number;
  lineHeight?: number;
  maxHeight?: number | null;
}

export interface FittedText {
  fontSize: number;
  lines: number;
  /** False means: do not draw this here. It is not a rendering failure. */
  fits: boolean;
  text: string;
}

export function charsPerLine(fontSize: number, width: number, weight?: number): number;
export function lineCount(
  text: string,
  fontSize: number,
  width: number,
  weight?: number,
): number;
export function fitText(text: string, options?: FitBox): FittedText;
export function captionBox(): Required<Pick<FitBox, "width" | "maxLines" | "maxFontSize" | "minFontSize" | "lineHeight" | "maxHeight">>;
