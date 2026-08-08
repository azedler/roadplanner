export type Frame = { width: number; height: number };
export type Camera = { x: number; y: number; zoom: number };

export const CAPTION_STRIP: number;

export function cameraFor(
  frame: Frame,
  points: [number, number][],
  options?: { fill?: number; maxZoom?: number; bottom?: number },
): Camera;

export function blendCamera(from: Camera, to: Camera, t: number): Camera;

export function onScreen(frame: Frame, camera: Camera, point: [number, number]): [number, number];

export function cameraTransform(frame: Frame, camera: Camera): string;
