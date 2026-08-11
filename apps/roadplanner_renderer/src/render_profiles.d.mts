/** Types for `render_profiles.mjs`. Must be `.d.mts` for an explicit `.mjs` import. */

export const DESIGN_WIDTH: number;
export const DESIGN_HEIGHT: number;
export const FILM_FPS: number;

export interface RenderProfile {
  id: string;
  width: number;
  height: number;
  fps: number;
  crf: number;
  x264Preset: string;
  label: string;
  description: string;
  suffix: string;
  experimental: boolean;
  recommended: boolean;
}

export const RENDER_PROFILES: Record<string, RenderProfile>;
export const DEFAULT_RENDER_PROFILE: string;

export function renderProfile(id: string | null | undefined): RenderProfile;
export function pixelFactor(profile?: RenderProfile | null): number;
