// Body-region imagery cropped from the local anatomy reference (floated, no bg).
// Coaches browse by body area, not tiny muscles.
export const BODY_REGIONS = ["Back", "Core", "Shoulders", "Chest", "Arms", "Legs"];

const BASE_IMAGE = "/body-regions/base.png";
const REGION_IMAGES: Record<string, string> = {
  Back: "/body-regions/back.png",
  Core: "/body-regions/core.png",
  Shoulders: "/body-regions/shoulders.png",
  Chest: "/body-regions/chest.png",
  Arms: "/body-regions/arms.png",
  Legs: "/body-regions/legs.png",
};

// clickable hotspot zones over the FRONT base figure (viewBox 0 0 197 540).
// Back is selectable via its pill only (it's a back muscle).
const ZONES: { region: string; cx: number; cy: number; rx: number; ry: number }[] = [
  { region: "Shoulders", cx: 98, cy: 112, rx: 62, ry: 16 },
  { region: "Chest", cx: 98, cy: 153, rx: 46, ry: 25 },
  { region: "Core", cx: 98, cy: 228, rx: 34, ry: 44 },
  { region: "Arms", cx: 36, cy: 220, rx: 16, ry: 82 },
  { region: "Arms", cx: 160, cy: 220, rx: 16, ry: 82 },
  { region: "Legs", cx: 80, cy: 412, rx: 24, ry: 116 },
  { region: "Legs", cx: 116, cy: 412, rx: 24, ry: 116 },
];

export function regionForExercise(e: any): string {
  const explicit = e.region || "";
  if (REGION_IMAGES[explicit]) return explicit;
  const text = `${e.pattern || ""} ${e.name || ""}`.toLowerCase();
  if (text.includes("cardio") || text.includes("sled") || text.includes("jump") || text.includes("plyometric")) return "Legs";
  if (text.includes("upper pull") || text.includes("row") || text.includes("pulldown")) return "Back";
  if (text.includes("core") || text.includes("breathing") || text.includes("pallof") || text.includes("carry")) return "Core";
  if (text.includes("upper push - vertical") || text.includes("shoulder") || text.includes("overhead")) return "Shoulders";
  if (text.includes("upper push - horizontal") || text.includes("push-up") || text.includes("bench")) return "Chest";
  if (text.includes("arms") || text.includes("tricep") || text.includes("bicep") || text.includes("curl")) return "Arms";
  return "Legs";
}

export function BodyThumb({ region }: { region?: string }) {
  const r = REGION_IMAGES[region || ""] ? region! : "Legs";
  return <img className="bthumb" src={REGION_IMAGES[r]} alt="" aria-hidden="true" />;
}

/** The filter map: a clickable body. Hover/click a muscle (or a pill) — the
 *  figure shows that muscle highlighted and filters the library. */
export function BodyHeatMap({
  active, hover, onHover, onSelect,
}: {
  active: string | null;
  hover: string | null;
  onHover: (region: string | null) => void;
  onSelect: (region: string) => void;
}) {
  const sel = hover || active;
  const src = sel && REGION_IMAGES[sel] ? REGION_IMAGES[sel] : BASE_IMAGE;
  return (
    <div className="bodymap">
      <svg viewBox="0 0 197 540" className="bodymap-svg" aria-label="filter by body area">
        <image href={src} x="0" y="0" width="197" height="540" preserveAspectRatio="xMidYMid meet" />
        {ZONES.map((z, i) => (
          <ellipse
            key={i}
            className={"bodymap-zone" + (sel === z.region ? " on" : "")}
            cx={z.cx} cy={z.cy} rx={z.rx} ry={z.ry}
            onMouseEnter={() => onHover(z.region)}
            onMouseLeave={() => onHover(null)}
            onClick={() => onSelect(z.region)}
          >
            <title>{z.region}</title>
          </ellipse>
        ))}
      </svg>
      <span className="bodymap-hint">{sel || "Tap a muscle, or a pill"}</span>
    </div>
  );
}
