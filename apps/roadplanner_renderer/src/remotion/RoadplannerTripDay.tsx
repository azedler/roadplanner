/**
 * One real trip day, as a short video.
 *
 * Four kinds of scene and nothing else: a title card, the day's facts, the
 * photos, a closing card. No map, no music, no narration, no generated
 * text — those are all deliberately out of scope, and each one added here
 * would make a failed render ambiguous between "the data path is broken"
 * and "the new thing is broken".
 *
 * The length follows the content: a day with three photos is shorter than
 * one with five. `calculateMetadata` on the composition is what makes that
 * work, and it is the single place the arithmetic lives — the renderer
 * reads the result back rather than computing it a second time.
 *
 * Two rules the content follows, both learned from the PDF:
 *
 * - **nothing is invented to fill a gap.** A day without a summary shows
 *   no summary; a day without a distance shows no distance. A placeholder
 *   is worse than a blank, because it reads as data.
 * - **every animation is a pure function of the frame number**, so the
 *   same package always renders to the same video and a warm render is
 *   comparable to a cold one.
 */
import React from "react";
import {
  AbsoluteFill,
  Img,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export const FPS = 30;
export const TITLE_FRAMES = 60;
export const FACTS_FRAMES = 75;
export const PHOTO_FRAMES = 51;
export const CLOSING_FRAMES = 54;

export type TripDayStop = {
  name: string;
  time: string;
};

export type TripDayFacts = {
  dayId: string;
  date: string;
  title: string;
  summary: string;
  number: number | null;
  count: number | null;
  distanceKm: number | null;
  durationMinutes: number | null;
};

export type RoadplannerTripDayProps = {
  tripTitle: string;
  day: TripDayFacts;
  stops: TripDayStop[];
  photos: string[];
};

export const tripDayDurationInFrames = (photoCount: number): number =>
  TITLE_FRAMES + FACTS_FRAMES + PHOTO_FRAMES * Math.max(1, photoCount) + CLOSING_FRAMES;

const INK = "#f5f7fa";
const MUTED = "#9fb3c8";
const BACKDROP = "#101725";
const ACCENT = "#e07a3f";

const base: React.CSSProperties = {
  fontFamily: "sans-serif",
  color: INK,
  backgroundColor: BACKDROP,
};

/** Fade in over the first third of a second, hold, fade out at the end. */
const useSceneOpacity = (length: number): number => {
  const frame = useCurrentFrame();
  return Math.min(
    interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" }),
    interpolate(frame, [length - 10, length], [1, 0], { extrapolateLeft: "clamp" }),
  );
};

const formatDuration = (minutes: number): string => {
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
};

const TitleCard: React.FC<{ props: RoadplannerTripDayProps }> = ({ props }) => {
  const opacity = useSceneOpacity(TITLE_FRAMES);
  const { day, tripTitle } = props;
  const counter =
    day.number && day.count ? `Tag ${day.number} von ${day.count}` : "";
  return (
    <AbsoluteFill
      style={{ ...base, justifyContent: "center", padding: 110, opacity }}
    >
      {tripTitle ? (
        <div style={{ fontSize: 34, color: MUTED, letterSpacing: 3 }}>
          {tripTitle.toUpperCase()}
        </div>
      ) : null}
      <div style={{ height: 6, width: 140, backgroundColor: ACCENT, margin: "28px 0" }} />
      <div style={{ fontSize: 68, fontWeight: 700, lineHeight: 1.1 }}>
        {day.title || "Reisetag"}
      </div>
      <div style={{ fontSize: 32, color: MUTED, marginTop: 24 }}>
        {[day.date, counter].filter(Boolean).join("  ·  ")}
      </div>
    </AbsoluteFill>
  );
};

const FactsCard: React.FC<{ props: RoadplannerTripDayProps }> = ({ props }) => {
  const opacity = useSceneOpacity(FACTS_FRAMES);
  const { day, stops } = props;
  const numbers = [
    day.distanceKm ? `${Math.round(day.distanceKm)} km` : "",
    day.durationMinutes ? formatDuration(day.durationMinutes) : "",
    stops.length ? `${stops.length} Stopps` : "",
  ].filter(Boolean);
  // Six fit legibly at this size; the rest are counted rather than
  // squeezed in, which is what the PDF got wrong with overlapping labels.
  const shown = stops.slice(0, 6);
  const hidden = stops.length - shown.length;
  return (
    <AbsoluteFill style={{ ...base, padding: 96, opacity }}>
      <div style={{ fontSize: 28, color: MUTED, letterSpacing: 3 }}>
        {(day.date || "REISETAG").toUpperCase()}
      </div>
      {numbers.length ? (
        <div style={{ fontSize: 44, marginTop: 18, color: ACCENT }}>
          {numbers.join("   ·   ")}
        </div>
      ) : null}
      {day.summary ? (
        <div style={{ fontSize: 30, lineHeight: 1.45, marginTop: 26, maxWidth: 1050 }}>
          {day.summary}
        </div>
      ) : null}
      <div style={{ marginTop: 30 }}>
        {shown.map((stop, index) => (
          <div
            key={`${stop.name}-${index}`}
            style={{ fontSize: 30, color: MUTED, marginBottom: 10 }}
          >
            <span style={{ color: ACCENT, marginRight: 16 }}>›</span>
            {stop.name}
            {stop.time ? (
              <span style={{ marginLeft: 14, fontSize: 24 }}>{stop.time}</span>
            ) : null}
          </div>
        ))}
        {hidden > 0 ? (
          <div style={{ fontSize: 24, color: MUTED }}>und {hidden} weitere</div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

const PhotoScene: React.FC<{ source: string; caption: string }> = ({
  source,
  caption,
}) => {
  const frame = useCurrentFrame();
  const opacity = useSceneOpacity(PHOTO_FRAMES);
  // A slow push in. Small on purpose: enough to stop the frame looking
  // frozen, not enough to soften the picture.
  const scale = interpolate(frame, [0, PHOTO_FRAMES], [1.0, 1.06]);
  return (
    <AbsoluteFill style={{ backgroundColor: "#000000", opacity }}>
      <AbsoluteFill style={{ transform: `scale(${scale})` }}>
        <Img
          src={source}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      </AbsoluteFill>
      {caption ? (
        <AbsoluteFill style={{ justifyContent: "flex-end" }}>
          <div
            style={{
              // A readable strip rather than a box: the caption has to work
              // over a bright sky and over a dark forest.
              background: "linear-gradient(transparent, rgba(0,0,0,0.72))",
              padding: "90px 72px 46px",
              fontFamily: "sans-serif",
              color: INK,
              fontSize: 32,
            }}
          >
            {caption}
          </div>
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};

const ClosingCard: React.FC<{ props: RoadplannerTripDayProps }> = ({ props }) => {
  const opacity = useSceneOpacity(CLOSING_FRAMES);
  return (
    <AbsoluteFill
      style={{ ...base, alignItems: "center", justifyContent: "center", opacity }}
    >
      <div style={{ fontSize: 30, color: MUTED, letterSpacing: 4 }}>ROADPLANNER</div>
      <div style={{ height: 6, width: 110, backgroundColor: ACCENT, margin: "26px 0" }} />
      <div style={{ fontSize: 44, fontWeight: 600, textAlign: "center", maxWidth: 1000 }}>
        {props.day.title || props.tripTitle || "Reisetag"}
      </div>
      <div style={{ fontSize: 28, color: MUTED, marginTop: 20 }}>
        {props.photos.length === 1
          ? "1 Foto"
          : `${props.photos.length} Fotos`}
      </div>
    </AbsoluteFill>
  );
};

export const RoadplannerTripDay: React.FC<RoadplannerTripDayProps> = (props) => {
  const { fps } = useVideoConfig();
  const caption = [props.day.date, props.day.title].filter(Boolean).join("  ·  ");
  let cursor = 0;
  const scenes: React.ReactNode[] = [];

  scenes.push(
    <Sequence key="title" from={cursor} durationInFrames={TITLE_FRAMES}>
      <TitleCard props={props} />
    </Sequence>,
  );
  cursor += TITLE_FRAMES;

  scenes.push(
    <Sequence key="facts" from={cursor} durationInFrames={FACTS_FRAMES}>
      <FactsCard props={props} />
    </Sequence>,
  );
  cursor += FACTS_FRAMES;

  props.photos.forEach((source, index) => {
    scenes.push(
      <Sequence key={`photo-${index}`} from={cursor} durationInFrames={PHOTO_FRAMES}>
        <PhotoScene source={source} caption={index === 0 ? caption : ""} />
      </Sequence>,
    );
    cursor += PHOTO_FRAMES;
  });

  scenes.push(
    <Sequence key="closing" from={cursor} durationInFrames={CLOSING_FRAMES}>
      <ClosingCard props={props} />
    </Sequence>,
  );

  // `fps` is read so a composition registered at another frame rate fails
  // loudly here rather than producing a video of the wrong length.
  if (fps !== FPS) {
    throw new Error(`Die Komposition erwartet ${FPS} fps, bekommen hat sie ${fps}.`);
  }

  return <AbsoluteFill style={{ backgroundColor: BACKDROP }}>{scenes}</AbsoluteFill>;
};
