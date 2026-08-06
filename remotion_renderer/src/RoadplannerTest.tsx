/**
 * The deliberately banal test composition.
 *
 * Success for the spike is "reproducible and controllable", not "pretty".
 * There are no real trip photos, no map, no Gemini text and no network
 * access here on purpose: anything richer would make a failed render
 * ambiguous between "the subprocess route does not work" and "the content
 * pipeline has a bug".
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
  subtitle: string;
};

const Camper: React.FC<{ x: number }> = ({ x }) => (
  <svg
    width={220}
    height={120}
    viewBox="0 0 220 120"
    style={{ position: "absolute", left: x, bottom: 120 }}
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

export const RoadplannerTest: React.FC<RoadplannerTestProps> = ({
  title,
  subtitle,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, width } = useVideoConfig();
  // Deterministic: the same frame always produces the same picture, which
  // is what makes a CI render comparable at all.
  const x = interpolate(frame, [0, durationInFrames - 1], [-240, width + 40]);

  return (
    <AbsoluteFill style={{ backgroundColor: "#0f2e3d" }}>
      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "flex-start",
          paddingTop: 120,
          fontFamily: "sans-serif",
          color: "#faf6ef",
        }}
      >
        <div style={{ fontSize: 64, fontWeight: 700, letterSpacing: -1 }}>
          {title}
        </div>
        <div style={{ fontSize: 30, marginTop: 18, color: "#9fb8bd" }}>
          {subtitle}
        </div>
      </AbsoluteFill>
      <AbsoluteFill>
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 118,
            height: 6,
            backgroundColor: "#1c5d6b",
          }}
        />
        <Camper x={x} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
