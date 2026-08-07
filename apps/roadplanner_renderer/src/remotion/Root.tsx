/**
 * The composition registry.
 *
 * Exactly one composition exists, and its id is the only value the job
 * protocol accepts. A whitelist rather than a free string, because the name
 * reaches a rendering engine.
 */
import React from "react";
import { Composition } from "remotion";

import { RoadplannerTest } from "./RoadplannerTest";

export const COMPOSITION_ID = "roadplanner-remotion-test";

export const RemotionRoot: React.FC = () => (
  <Composition
    id={COMPOSITION_ID}
    component={RoadplannerTest}
    durationInFrames={150}
    fps={30}
    width={1280}
    height={720}
    defaultProps={{ title: "Roadplanner Remotion Test" }}
  />
);
