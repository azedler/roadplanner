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

/**
 * The real vehicle's colours, read off photographs of it.
 *
 * Blue-grey metallic with a near-black moulded roof. That contrast is
 * most of what makes the van recognisable from across a field, and it is
 * worth more than the pale bodywork the first drawing invented.
 *
 * It costs something, and the keyline pays for it. A dark van on a dark
 * map is a smudge - the land is #33404f and the body is not far off it.
 * So the silhouette carries a thin pale outline that does not scale with
 * the zoom, the same device a paper map uses to lift a symbol off its
 * background. The van stays legible at forty pixels without being
 * repainted a colour it is not.
 */
const BODY = "#55677d";
const SKIRT = "#47576b";
const ROOF = "#22282f";
const GLASS = "#8ea4b8";
const ROOF_GLASS = "#5d6f83";
const KEYLINE = "#d7e2ee";
const HALO_W = 2.6;

// The silhouette, written once. The halo underneath and the bodywork on
// top have to be the same shapes or the outline would sit crooked.
const BODY_D = "M-19 -8.6 L6 -8.6 L6 3.4 Q6 4.6 4.8 4.6 L-17.8 4.6 Q-19 4.6 -19 3.4 Z";
const NOSE_D = "M6 -8.6 L11.4 -6.2 Q15.6 -4.4 17.2 -0.6 L18.2 2.2 Q18.5 3.2 17.4 3.4 L6 3.4 Z";
const ROOF_D =
  "M6.6 -8.6 Q5.6 -13.6 2.6 -16.4 Q0.8 -18 -2.6 -18 L-14.4 -17.2 Q-17.4 -17 -17.4 -14.6 L-17.4 -8.6 Z";


/**
 * The confirmed illustrations this film may use, if any.
 *
 * A context rather than a prop threaded through the map, because the
 * question "is there an approved picture of our camper" is the same
 * everywhere and belongs to the film, not to any one scene. Empty is the
 * normal state: without an asset every character falls back to what is
 * drawn here, which is a worse picture and a complete film.
 */
export type CharacterAsset = { kind: string; variant: string; path: string };

export const CharacterAssetContext = React.createContext<CharacterAsset[]>([]);

export const useCharacterAsset = (kind: string, variant: string): string => {
  const assets = React.useContext(CharacterAssetContext);
  const found = assets.find((asset) => asset.kind === kind && asset.variant === variant);
  if (found) return found.path;
  // The side elevation stands in for the map view and the other way
  // round: one approved picture is better than one approved picture and
  // one drawing that do not look like the same vehicle.
  const other = assets.find((asset) => asset.kind === kind);
  return other ? other.path : "";
};

export type CharacterProps = {
  x: number;
  y: number;
  /** Map bearing in degrees; only vehicles use it. */
  heading?: number;
  scale?: number;
  /** Whether somebody is at the wheel. Off when the vehicle is parked. */
  driver?: boolean;
  /** Which approved illustration to prefer, when one exists. */
  variant?: "map" | "side";
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
export const Camper: React.FC<CharacterProps> = (props) => {
  const asset = useCharacterAsset("vehicle", props.variant ?? "map");
  return asset ? <CamperImage {...props} asset={asset} /> : <CamperDrawing {...props} />;
};

/**
 * The approved illustration, placed exactly where the drawing would be.
 *
 * Mirrored when travelling west and leaning with the gradient, like the
 * drawn one - the asset is generated facing right, and a camper that
 * drove to Norway pointing backwards would be worse than no asset at
 * all. It never rotates to the heading, for the same reason the drawing
 * does not: a side view turned north stands on its bumper.
 */
const CAMPER_ASSET_WIDTH = 46;

const CamperImage: React.FC<CharacterProps & { asset: string }> = ({
  x,
  y,
  heading = 0,
  scale = 1,
  asset,
}) => {
  const screen = -heading;
  const westward = Math.abs(screen) > 90;
  const slope = westward ? (screen > 0 ? 180 - screen : -180 - screen) : screen;
  const lean = Math.max(-14, Math.min(14, slope));
  const width = CAMPER_ASSET_WIDTH * scale;
  return (
    <g transform={`translate(${x} ${y}) rotate(${lean})`}>
      <ellipse cx="0" cy={width * 0.21} rx={width * 0.46} ry={width * 0.085} fill="rgba(0,0,0,0.42)" />
      <g transform={`scale(${westward ? -1 : 1} 1)`}>
        <image
          href={`/${asset}`}
          x={-width / 2}
          y={-width * 0.62}
          width={width}
          height={width}
          preserveAspectRatio="xMidYMax meet"
        />
      </g>
    </g>
  );
};

const CamperDrawing: React.FC<CharacterProps> = ({ x, y, heading = 0, scale = 1, driver = true }) => {
  // Mercator y grows downward and the bearing was computed in map space,
  // so the screen angle is the negative of it.
  const screen = -heading;
  const westward = Math.abs(screen) > 90;
  const slope = westward ? (screen > 0 ? 180 - screen : -180 - screen) : screen;
  const lean = Math.max(-14, Math.min(14, slope));
  return (
    <g transform={`translate(${x} ${y}) rotate(${lean}) scale(${scale})`}>
      <ellipse cx="0" cy="10" rx="22" ry="4" fill="rgba(0,0,0,0.42)" />
      <g transform={`scale(${westward ? -1 : 1} 1)`}>
        {/* The halo, and the whole reason the true colours are usable.
            The same three shapes drawn first in pale, stroked wide: what
            survives once the real bodywork is painted over them is a thin
            outline all the way round. A paper map lifts a dark symbol off
            a dark ground exactly this way, and it costs no accuracy - the
            van keeps the colour it actually is. */}
        <g fill={KEYLINE} stroke={KEYLINE} strokeWidth={HALO_W} strokeLinejoin="round">
          <path d={BODY_D} vectorEffect="non-scaling-stroke" />
          <path d={NOSE_D} vectorEffect="non-scaling-stroke" />
          <path d={ROOF_D} vectorEffect="non-scaling-stroke" />
        </g>
        {/* Body: blue-grey metallic, low and long on the long wheelbase. */}
        <path d={BODY_D} fill={BODY} />
        {/* The Transit face: a short bonnet dropping away from a steeply
            raked screen. A vertical front here reads as a lorry. */}
        <path d={NOSE_D} fill={BODY} />
        {/* The near-black moulded roof, and the reason the van is
            recognisable across a field: it climbs steeply from above the
            screen to a shoulder, then falls away gently to the back. */}
        <path d={ROOF_D} fill={ROOF} />
        {/* The window in the roof panel. */}
        <rect x="-9.6" y="-15.2" width="6.6" height="3" rx="0.7" fill={ROOF_GLASS} />
        {/* Windscreen, cab window, and the long side glazing. */}
        <path d="M6.9 -7.2 L11 -5.2 Q13.6 -4 15 -1.8 L6.9 -1.8 Z" fill={GLASS} />
        <rect x="1.4" y="-7.4" width="4.2" height="5" rx="0.8" fill={GLASS} />
        <rect x="-6.6" y="-7.4" width="6.8" height="5" rx="0.8" fill={GLASS} />
        <rect x="-15.6" y="-7.4" width="8" height="5" rx="0.8" fill={GLASS} />
        {/* Ford's wide grille and a headlight, so the nose is not blank. */}
        <path d="M15.4 -1 L18.1 1.4 L18.3 2.4 L15.4 2.4 Z" fill="#1e242c" />
        <ellipse cx="15.2" cy="-0.9" rx="1.5" ry="0.8" fill="#e8eef5" opacity={0.85} />
        {/* Lower body a shade darker, the way the real one is skirted. */}
        <path d="M-19 1.6 L6 1.6 L6 3.4 Q6 4.6 4.8 4.6 L-17.8 4.6 Q-19 4.6 -19 3.4 Z" fill={SKIRT} />
        {/* Silver alloys. */}
        <circle cx="-13.4" cy="5" r="3.9" fill="#171c23" />
        <circle cx="-13.4" cy="5" r="1.9" fill="#b9c4d0" />
        <circle cx="11.4" cy="5" r="3.9" fill="#171c23" />
        <circle cx="11.4" cy="5" r="1.9" fill="#b9c4d0" />
        {driver ? <Driver /> : null}
      </g>
    </g>
  );
};

/**
 * Somebody at the wheel.
 *
 * Still not a portrait, and it cannot become one: the camper is about
 * forty pixels wide on the map, so a head is three pixels across and a
 * face is one. No amount of care makes a likeness fit there.
 *
 * What does survive at three pixels is a silhouette and two colours. So
 * the figure carries the two things that are true of whoever usually
 * drives this van - hair worn short and light rather than long and dark,
 * and a black jacket - and claims nothing beyond them. That is the
 * honest version of "make it look like her": not a face, but a shape
 * that is no longer somebody else's.
 *
 * Recognising a person is the crew scene's job, at a couple of hundred
 * pixels and from their own photograph.
 */
const Driver: React.FC = () => (
  <g>
    {/* Shoulder in the dark jacket, against the cab glass. */}
    <path d="M1.9 -2.5 Q2.4 -4.6 4.05 -4.6 Q5.7 -4.6 5.8 -2.5 Z" fill="#20262d" />
    <circle cx="4.05" cy="-5.55" r="1.35" fill="#e0b492" />
    {/* Hair: cropped short and light, which is the one feature that
        still reads once everything else has become a single pixel. */}
    <path
      d="M2.72 -5.5 Q2.72 -7.05 4.05 -7.05 Q5.4 -7.05 5.42 -5.55 Q5.1 -6.25 4.35 -6.35 Q3.5 -6.45 2.72 -5.5 Z"
      fill="#b9ad9c"
    />
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
