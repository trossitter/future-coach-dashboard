import { useEffect, useState } from "react";
import { getJSON, postJSON } from "../api";
import { BodyHeatMap, BODY_REGIONS, BodyThumb, regionForExercise } from "./BodyThumb";

const REGIONS = BODY_REGIONS;

/** LOCAL EXPERIMENT — coach library, browsed TARGET-FIRST. A coach thinks "chest
 *  today", not "find the name starting with F", so the entry point is the body
 *  map / region chips; cards have a media slot + the cue on hover. */
export function CoachLibrary({ onClose }: { onClose: () => void }) {
  const [exercises, setExercises] = useState<any[]>([]);
  const [region, setRegion] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [name, setName] = useState("");

  async function load() {
    const d = await getJSON("/coach/library");
    setExercises(d.exercises || []);
  }
  useEffect(() => { load(); }, []);

  async function add() {
    if (!name.trim()) return;
    await postJSON("/coach/library", { name });
    setName(""); load();
  }

  const shown = region ? exercises.filter((e) => regionForExercise(e) === region) : exercises;
  const active = (r: string) => region === r || hover === r;
  const selectRegion = (r: string) => setRegion(region === r ? null : r);

  return (
    <div className="lib-overlay" onClick={onClose}>
      <div className="lib-panel wide" onClick={(e) => e.stopPropagation()}>
        <div className="lib-head">
          <h2>Sam's library</h2>
          <button className="link" onClick={onClose}>Close</button>
        </div>

        <div className="lib-browse">
          <BodyHeatMap active={region} hover={hover} onHover={setHover} onSelect={selectRegion} />

          <div className="lib-right">
            <div className="region-chips">
              <button className={"chip" + (!region ? " on" : "")} onClick={() => setRegion(null)}>All</button>
              {REGIONS.map((r) => (
                <button key={r} className={"chip" + (active(r) ? " on" : "")}
                  onMouseEnter={() => setHover(r)} onMouseLeave={() => setHover(null)}
                  onClick={() => selectRegion(r)}>{r}</button>
              ))}
            </div>
            <div className="lib-grid">
              {shown.length === 0 && <div className="muted">Nothing in {region} yet.</div>}
              {shown.map((e) => (
                <div key={e.id} className="lib-card" title={e.notes || e.name}>
                  <div className="lib-thumb"><BodyThumb region={regionForExercise(e)} /></div>
                  <div className="lib-card-body">
                    <div className="lib-ex-name">{e.name}</div>
                    <div className="lib-meta">
                      {[regionForExercise(e), e.sets && e.reps ? `${e.sets} × ${e.reps}` : e.reps]
                        .filter(Boolean).join(" · ")}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="lib-add">
          <input placeholder="Add an exercise…" value={name}
            onChange={(e) => setName(e.target.value)} />
          <button onClick={add} disabled={!name.trim()}>Add</button>
        </div>
      </div>
    </div>
  );
}
