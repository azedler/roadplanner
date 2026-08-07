/**
 * The deliberately banal test composition.
 *
 * Success for this spike is "reproducible and measurable", not "pretty".
 * No maps, no photos, no AI, no network, no fonts to resolve: anything
 * richer would make a failed render ambiguous between "Remotion cannot run
 * in this container" and "the content has a bug". Everything here is drawn
 * from primitives that ship with the browser.
 *
 * The animation is a pure function of the frame number, so the same frame
 * always produces the same picture. That is what makes a warm render
 * comparable to a cold one, and a CI render comparable to a device render.
 */
import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export type RoadplannerTestProps = {
  title: string;
};

const Camper: React.FC<{ x: number }> = ({ x }) => (
  <svg
    width={220}
    height={120}
    viewBox="0 0 220 120"
    style={{ position: "absolute", left: x, bottom: 130 }}
  >
    <rect x="10" y="20" width="150" height="60" rx="10" fill="#e07a3f" />
    <rect x="160" y="42" width="46" height="38" rx="8" fill="#e07a3f" />
    <rect x="168" y="50" width="28" height="20" rx="4" fill="#faf6ef" />
    <rect x="26" y="34" width="52" height="30" rx="4" fill="#faf6ef" />
    <circle cx="56" cy="88" r="16" fill="#20242a" />
    <circle cx="56" cy="88" r="6" fill="#9fb8bd" />
    <circle cx="176" cy="88" r="16" fill="#20242a" />
    <circle cx="176" cy="88" r="6" fill="#9fb8bd" />
  </svg>
);

export const RoadplannerTest: React.FC<RoadplannerTestProps> = ({ title }) => {
  const frame = useCurrentFrame();
  const { durationInFrames, width } = useVideoConfig();
  // Starts fully off-screen left, ends fully off-screen right, so the first
  // and last frame are unambiguous to look at.
  const x = interpolate(frame, [0, durationInFrames - 1], [-240, width + 40]);

  return (
    <AbsoluteFill style={{ backgroundColor: "#000000" }}>
      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "flex-start",
          paddingTop: 140,
          fontFamily: "sans-serif",
          color: "#ffffff",
        }}
      >
        <div style={{ fontSize: 64, fontWeight: 700, letterSpacing: -1 }}>
          {title}
        </div>
      </AbsoluteFill>
      <AbsoluteFill>
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 128,
            height: 4,
            backgroundColor: "#333333",
          }}
        />
        <Camper x={x} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
