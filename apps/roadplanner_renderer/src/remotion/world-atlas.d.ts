/**
 * The world map data, declared rather than typed.
 *
 * `world-atlas` ships a 740 KB TopoJSON file. Turning `resolveJsonModule`
 * on would make the type checker read and infer a type for every one of
 * its coordinates on every run, for a value that is handed straight to
 * `topojson.feature()` and never accessed by field. The bundler still
 * inlines the real data; only the checker is told to stop looking.
 */
declare module "world-atlas/countries-50m.json" {
  import type { Topology } from "topojson-specification";

  const topology: Topology;
  export default topology;
}
