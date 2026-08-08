/**
 * The cast: the camper, and later the people and the dog.
 *
 * Everything the film draws as a *character* comes from here, behind one
 * shape of interface, so that swapping a drawn stand-in for a real
 * illustration later is a change of asset and not a change of the map.
 *
 * How the camper is meant to end up
 * --------------------------------
 *
 * Roadplanner already holds a photograph of the actual vehicle in the
 * crew section. The intended path is: derive one illustration from that
 * photograph, have somebody confirm it, and store it as a Roadplanner
 * asset that every render then reuses. Generating a picture per render
 * would be slow, expensive and - worse - would make two renders of the
 * same trip different films.
 *
 * Until that illustration exists, this draws a stylised high-roof van.
 * It is deliberately *ours-shaped* rather than a stock vehicle: a
 * generic asset hard-wired into the composition is exactly what the
 * brief rules out as a permanent answer, and a drawing we own can be
 * replaced without anybody's licence being involved.
 *
 * The interface, so the swap is cheap
 * -----------------------------------
 *
 * A character is a component that takes a screen position, a heading and
 * a size. Whether it paints paths or an `<Img>` is its own business. When
 * a confirmed illustration arrives it becomes a second implementation
 * behind the same props, and `CHARACTERS` gains an entry - the map, the
 * crew scene and the outro do not change at all.
 */
import React from "react";
import { Img } from "remotion";

export type CharacterProps = {
  x: number;
  y: number;
  /** Map bearing in degrees; only vehicles use it. */
  heading?: number;
  scale?: number;
};

/**
 * The camper.
 *
 * Drawn from the side and never rotated to follow the road: a side view
 * turned to face north points at the sky, which reads as a van standing
 * on its bumper. It faces the way it is travelling - mirrored when that
 * is westward - and only leans with the gradient of the route.
 */
export const Camper: React.FC<CharacterProps> = ({ x, y, heading = 0, scale = 1 }) => {
  // Mercator y grows downward and the bearing was computed in map space,
  // so the screen angle is the negative of it.
  const screen = -heading;
  const westward = Math.abs(screen) > 90;
  const slope = westward ? (screen > 0 ? 180 - screen : -180 - screen) : screen;
  const lean = Math.max(-14, Math.min(14, slope));
  return (
    <g transform={`translate(${x} ${y}) rotate(${lean}) scale(${scale})`}>
      <ellipse cx="0" cy="10" rx="21" ry="4" fill="rgba(0,0,0,0.38)" />
      <g transform={`scale(${westward ? -1 : 1} 1)`}>
        {/* The high roof, which is the silhouette people recognise. */}
        <path
          d="M-19 -6 L-19 -13 Q-19 -16 -16 -16 L2 -16 Q5 -16 5 -13 L5 -6 Z"
          fill="#eef1f5"
        />
        <rect x="-19" y="-6" width="24" height="10" rx="2" fill="#f6f8fa" />
        {/* Bonnet and windscreen, raked forward. */}
        <path d="M5 -6 L13 -1 L17 3 L17 4 L5 4 Z" fill="#f6f8fa" />
        <path d="M5.8 -5 L12 -0.5 L5.8 -0.5 Z" fill="#7f97ad" />
        <rect x="-16" y="-13.5" width="7" height="5" rx="1" fill="#7f97ad" />
        <rect x="-7.5" y="-13.5" width="7" height="5" rx="1" fill="#7f97ad" />
        {/* A warm stripe, so it is not a white box at small sizes. */}
        <rect x="-19" y="-2" width="24" height="2.4" fill="#e8823f" />
        <circle cx="-12" cy="5" r="3.8" fill="#1b2330" />
        <circle cx="-12" cy="5" r="1.5" fill="#63707f" />
        <circle cx="11" cy="5" r="3.8" fill="#1b2330" />
        <circle cx="11" cy="5" r="1.5" fill="#63707f" />
      </g>
    </g>
  );
};

/**
 * A crew member, drawn from the portrait Roadplanner already stores.
 *
 * The portrait arrives in the render package as a local file - never as
 * a URL. The crew portrait route is guarded only by an unguessable
 * filename, which is a bearer secret rather than a session, and such a
 * link has no business travelling into a render package that is written
 * to a shared folder.
 */
export const CrewPortrait: React.FC<{
  path: string;
  size: number;
  ring?: string;
}> = ({ path, size, ring = "#e8823f" }) => (
  <div
    style={{
      width: size,
      height: size,
      borderRadius: "50%",
      overflow: "hidden",
      border: `${Math.max(2, Math.round(size * 0.03))}px solid ${ring}`,
      boxShadow: "0 12px 30px rgba(0,0,0,0.45)",
      backgroundColor: "#1b2330",
      flex: "0 0 auto",
    }}
  >
    <Img
      src={`/${path}`}
      style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
    />
  </div>
);

/**
 * The camper as a portrait-sized badge, for the crew line-up.
 *
 * Same circle as a person, so the vehicle reads as one of the crew
 * rather than as a diagram beside them.
 */
export const CamperBadge: React.FC<{ size: number }> = ({ size }) => (
  <div
    style={{
      width: size,
      height: size,
      borderRadius: "50%",
      border: `${Math.max(2, Math.round(size * 0.03))}px solid #5f7fc4`,
      boxShadow: "0 12px 30px rgba(0,0,0,0.45)",
      background: "linear-gradient(160deg, #2a3646, #161d29)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flex: "0 0 auto",
    }}
  >
    <svg width={size * 0.72} height={size * 0.72} viewBox="-24 -20 48 34">
      <Camper x={0} y={0} heading={0} scale={1.05} />
    </svg>
  </div>
);
