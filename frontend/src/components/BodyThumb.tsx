// Coach-library body map: a realistic neutral avatar with deterministic region
// overlays. The graph/data decides "Chest" or "Back"; the UI owns the picture.
export const BODY_REGIONS = [
  "Shoulders", "Chest", "Back", "Arms", "Core", "Legs", "Glutes", "Cardio", "Mobility",
];

const REGION_PATHS: Record<string, string[]> = {
  Shoulders: [
    "M21 67 C27 55 42 51 54 58 C50 67 38 73 24 74 Z",
    "M72 58 C84 51 99 55 105 67 L102 74 C88 73 76 67 72 58 Z",
  ],
  Chest: [
    "M35 77 C45 68 56 69 63 79 L62 96 C50 96 39 89 34 82 Z",
    "M64 79 C71 69 83 68 95 77 L97 82 C91 89 78 96 65 96 Z",
  ],
  Back: [
    "M37 72 C45 63 56 62 63 74 L63 116 C52 113 42 101 36 85 Z",
    "M64 74 C71 62 82 63 91 72 L95 85 C88 101 77 113 64 116 Z",
  ],
  Arms: [
    "M22 73 C32 78 34 96 31 117 C28 139 27 153 21 161 C16 155 18 135 20 116 C22 96 17 81 22 73 Z",
    "M104 73 C99 81 104 96 106 116 C108 135 110 155 105 161 C99 153 98 139 95 117 C92 96 94 78 104 73 Z",
  ],
  Core: [
    "M48 96 C56 101 72 101 80 96 C82 115 80 136 70 150 L63 157 L56 150 C46 136 44 115 48 96 Z",
  ],
  Legs: [
    "M48 151 C57 158 60 181 58 211 C57 237 53 256 46 258 C40 249 42 219 43 198 C44 176 41 158 48 151 Z",
    "M78 151 C87 158 84 176 85 198 C86 219 88 249 80 258 C73 256 69 237 68 211 C66 181 69 158 78 151 Z",
  ],
  Glutes: [
    "M47 143 C55 150 72 150 81 143 L78 160 C69 168 57 168 48 160 Z",
  ],
  Cardio: [
    "M33 72 C47 58 80 58 94 72 C103 91 99 133 84 159 C74 176 53 176 42 159 C27 133 24 91 33 72 Z",
  ],
  Mobility: [
    "M31 74 C42 59 84 59 96 74 C105 95 99 135 82 164 C89 185 89 235 80 258 C72 254 68 229 67 204 C65 181 62 181 60 204 C58 229 54 254 46 258 C37 235 37 185 44 164 C27 135 22 95 31 74 Z",
  ],
};

const MANY = ["Shoulders", "Chest", "Back", "Arms", "Core", "Legs", "Glutes"];

export function regionForExercise(e: any): string {
  const explicit = e.region || "";
  if (REGION_PATHS[explicit]) return explicit;
  const text = `${e.pattern || ""} ${e.name || ""}`.toLowerCase();
  if (text.includes("cardio") || text.includes("sled") || text.includes("jump") || text.includes("plyometric")) {
    return "Cardio";
  }
  if (text.includes("mobility") || text.includes("regen") || text.includes("stretch") || text.includes("breathing")) {
    return "Mobility";
  }
  if (text.includes("upper pull") || text.includes("row") || text.includes("pulldown")) return "Back";
  if (text.includes("upper push - vertical") || text.includes("shoulder") || text.includes("overhead")) return "Shoulders";
  if (text.includes("upper push - horizontal") || text.includes("push-up") || text.includes("bench")) return "Chest";
  if (text.includes("arms") || text.includes("tricep") || text.includes("bicep")) return "Arms";
  if (text.includes("core")) return "Core";
  if (text.includes("hip lift") || text.includes("glute") || text.includes("rdl")) return "Glutes";
  if (text.includes("lower") || text.includes("lunge") || text.includes("squat")) return "Legs";
  return "Mobility";
}

function pathsFor(region?: string): string[] {
  if (region === "Full body") return MANY.flatMap((r) => REGION_PATHS[r] || []);
  return region ? REGION_PATHS[region] || [] : [];
}

function AvatarBase({ className = "body-avatar" }: { className?: string }) {
  return (
    <image
      className={className}
      href="/coach-avatar-front.png"
      x="0"
      y="0"
      width="126"
      height="268"
      preserveAspectRatio="xMidYMid meet"
    />
  );
}

export function BodyThumb({ region }: { region?: string }) {
  return (
    <svg className="bthumb" viewBox="0 0 126 268" aria-hidden>
      <AvatarBase />
      <g className="bthumb-hl">
        {pathsFor(region).map((d, i) => <path key={i} d={d} />)}
      </g>
    </svg>
  );
}

export function BodyHeatMap({
  active,
  hover,
  onHover,
  onSelect,
}: {
  active: string | null;
  hover: string | null;
  onHover: (region: string | null) => void;
  onSelect: (region: string) => void;
}) {
  return (
    <svg className="bodymap" viewBox="0 0 126 268" aria-label="filter by body area">
      <AvatarBase />
      {BODY_REGIONS.map((r) => (
        <g
          key={r}
          className={"body-region" + (active === r || hover === r ? " hot" : "")}
          onMouseEnter={() => onHover(r)}
          onMouseLeave={() => onHover(null)}
          onClick={() => onSelect(r)}
        >
          <title>{r}</title>
          {pathsFor(r).map((d, i) => <path key={i} d={d} />)}
        </g>
      ))}
    </svg>
  );
}
