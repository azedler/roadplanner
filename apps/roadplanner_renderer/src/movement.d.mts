/**
 * Types for the movement maths.
 *
 * The module itself is plain JavaScript so the release suite can drive it
 * without a bundler or a browser - the motion has to be testable as
 * motion, frame against frame, and that is not something a TSX file
 * would let a node test do.
 */
export type LonLat = [number, number];
export type Route = { points: LonLat[]; lengths: number[]; total: number };

export function metres(a: LonLat, b: LonLat): number;
export function arcLengths(points: LonLat[]): number[];
export function prepareRoute(points: LonLat[]): Route;
export function pointAtDistance(
  route: Route,
  distance: number,
): { position: LonLat; index: number };
export function bearingWindow(route: Route, frames: number): { behind: number; ahead: number };
export function bearingAtDistance(
  route: Route,
  distance: number,
  window?: { behind?: number; ahead?: number },
): number;
export function routeUpTo(route: Route, distance: number): LonLat[];
export function easeTravel(
  fraction: number,
  ramps?: { rampIn?: number; rampOut?: number },
): number;
export function damped(samples: (frame: number) => number, frame: number, window?: number): number;
