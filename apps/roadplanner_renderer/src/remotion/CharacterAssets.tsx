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
  /** Whether somebody is at the wheel. Off when the vehicle is parked. */
  driver?: boolean;
};

/**
 * The camper: a Nugget Plus, which is a Transit Custom with a fixed
 * high roof on top of it.
 *
 * The first version of this drawing was a tall rectangular box with a
 * flat nose - a Sprinter, and recognisable as somebody else's van. The
 * roof was not the mistake; everything in front of it was. Three shapes
 * carry the identity, and they are the three this spends its lines on:
 *
 * **A low, rounded nose.** The Transit's bonnet is short and drops away
 * from a steeply raked windscreen. A vertical face here is what made the
 * old drawing read as a lorry.
 *
 * **A roof that starts behind the cab.** The high roof is moulded, a
 * little narrower than the body, and it begins with a step and a sloped
 * fairing over the windscreen - not one box running from bumper to
 * bumper.
 *
 * **A long body on a long wheelbase**, with the rear overhang the Plus
 * has for its bathroom.
 *
 * Drawn from the side and never rotated to follow the road: a side view
 * turned to face north points at the sky, which reads as a van standing
 * on its bumper. It faces the way it is travelling - mirrored when that
 * is westward - and only leans with the gradient of the route.
 *
 * The colours are the film's, not the vehicle's - a warm stripe against
 * pale bodywork, so that at forty pixels wide it is not a white smudge
 * on a dark map. When a photograph of the real one is turned into a
 * confirmed asset, that is where the true colour belongs.
 */
export const Camper: React.FC<CharacterProps> = ({ x, y, heading = 0, scale = 1, driver = true }) => {
  // Mercator y grows downward and the bearing was computed in map space,
  // so the screen angle is the negative of it.
  const screen = -heading;
  const westward = Math.abs(screen) > 90;
  const slope = westward ? (screen > 0 ? 180 - screen : -180 - screen) : screen;
  const lean = Math.max(-14, Math.min(14, slope));
  return (
    <g transform={`translate(${x} ${y}) rotate(${lean}) scale(${scale})`}>
      <ellipse cx="0" cy="10" rx="22" ry="4" fill="rgba(0,0,0,0.38)" />
      <g transform={`scale(${westward ? -1 : 1} 1)`}>
        {/* The moulded high roof: inset from the body, rounded at the
            back, and sloping down over the cab rather than ending in a
            wall above the windscreen. */}
        <path
          d="M-17.5 -8.5 L-17.5 -15 Q-17.5 -17.4 -15 -17.4 L2.5 -17.4 Q5 -17.4 5.6 -15.4 L7.4 -8.5 Z"
          fill="#e7ecf2"
        />
        {/* Body sides, low and long. */}
        <path d="M-19 -8.6 L6 -8.6 L6 3.4 Q6 4.6 4.8 4.6 L-17.8 4.6 Q-19 4.6 -19 3.4 Z" fill="#f6f8fa" />
        {/* Bonnet and the raked windscreen - the Transit's face. */}
        <path
          d="M6 -8.6 L11.4 -6.2 Q15.6 -4.4 17.2 -0.6 L18.2 2.2 Q18.5 3.2 17.4 3.4 L6 3.4 Z"
          fill="#f6f8fa"
        />
        <path d="M6.9 -7.2 L11 -5.2 Q13.6 -4 15 -1.8 L6.9 -1.8 Z" fill="#7f97ad" />
        {/* Grille and light, so the front end is not a blank wedge. */}
        <path d="M15.6 -0.9 L18.1 1.4 L18.3 2.4 L15.6 2.4 Z" fill="#c3ccd6" />
        <circle cx="16.4" cy="0.6" r="0.9" fill="#f4e3b8" />
        {/* Side glazing: cab, sliding door, rear. */}
        <rect x="1.4" y="-7.4" width="4.2" height="5" rx="0.8" fill="#7f97ad" />
        <rect x="-6.4" y="-7.4" width="6.6" height="5" rx="0.8" fill="#7f97ad" />
        <rect x="-15.4" y="-7.4" width="7.8" height="5" rx="0.8" fill="#7f97ad" />
        {/* The awning rail along the roofline, and the warm stripe. */}
        <rect x="-17.6" y="-9.4" width="23" height="1" rx="0.4" fill="#c3ccd6" />
        <rect x="-19" y="-0.4" width="25" height="2.2" fill="#e8823f" />
        {/* Wheels on a long wheelbase, with the Plus's rear overhang. */}
        <circle cx="-13.4" cy="5" r="3.9" fill="#1b2330" />
        <circle cx="-13.4" cy="5" r="1.5" fill="#63707f" />
        <circle cx="11.4" cy="5" r="3.9" fill="#1b2330" />
        <circle cx="11.4" cy="5" r="1.5" fill="#63707f" />
        {driver ? <Driver /> : null}
      </g>
    </g>
  );
};

/**
 * Somebody at the wheel, looking out.
 *
 * Deliberately nobody in particular. The camper is about forty pixels
 * wide on the map, and a face at that size is four pixels of skin - a
 * likeness is not something this drawing is capable of, whoever it were
 * meant to be. What it *can* say is that the van is being driven rather
 * than rolling along empty, and that is worth the six shapes.
 *
 * Where a person really is recognisable is the crew scene, at a couple
 * of hundred pixels and from their own photograph.
 */
const Driver: React.FC = () => (
  <g>
    {/* Shoulder, in a colour that separates from the glass behind it -
        without contrast the whole figure disappears into the window. */}
    <path d="M1.9 -2.5 Q2.4 -4.6 4.05 -4.6 Q5.7 -4.6 5.8 -2.5 Z" fill="#2f4054" />
    <circle cx="4.05" cy="-5.55" r="1.35" fill="#d8a883" />
    {/* Hair, so the head is a head rather than a bare dot. */}
    <path d="M2.7 -5.75 Q2.85 -7 4.05 -7 Q5.3 -7 5.4 -5.75 Q4.75 -6.35 4.05 -6.3 Q3.35 -6.25 2.7 -5.75 Z" fill="#4a3428" />
  </g>
);

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
