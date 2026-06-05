import { useState } from "react";
import { postSSE } from "../api";
import { GraphEvidence } from "./GraphEvidence";

function Section({ title, items }: { title: string; items: any[] }) {
  if (!items?.length) return null;
  return (
    <div className="wsection">
      <div className="wsection-title">{title}</div>
      {items.map((p) => (
        <div key={p.id} className="prescription">
          <span className="ex-name">{p.name}</span>
          <span className="ex-rx">{p.sets} × {p.reps} · rest {p.rest_seconds}s</span>
        </div>
      ))}
    </div>
  );
}

export function Generator({ memberId, memberName, injuries, equipment }: any) {
  const [prompt, setPrompt] = useState("");
  const [time, setTime] = useState(45);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [trace, setTrace] = useState<any[]>([]);
  const [narration, setNarration] = useState("");
  const [show, setShow] = useState<string | null>(null);
  // ad-hoc, this-session joint constraints resolved via the clarify loop
  const [clarify, setClarify] = useState<any>(null);
  const [avoidJoints, setAvoidJoints] = useState<string[]>([]);
  const [ignoreJoints, setIgnoreJoints] = useState<string[]>([]);
  // ad-hoc, this-session equipment constraints (coach toggles a member's gear
  // off → exclude; clarify loop resolves unrecognised gear → extra)
  const [excludeEquip, setExcludeEquip] = useState<string[]>([]);
  const [extraEquip, setExtraEquip] = useState<string[]>([]);
  // signature of the inputs that produced the shown plan. Lets us flag "unsaved
  // changes" so edits stage quietly instead of auto-regenerating on every
  // keystroke / time tick / equipment toggle.
  const [lastSig, setLastSig] = useState("");
  const sig = (p = prompt, t = time, aj = avoidJoints, ij = ignoreJoints,
               ex = excludeEquip, xt = extraEquip) =>
    JSON.stringify([p, t, [...aj].sort(), [...ij].sort(),
                    [...ex].sort(), [...xt].sort()]);
  const dirty = !!result && sig() !== lastSig;

  async function run(
    avoid = avoidJoints, ignore = ignoreJoints,
    exclude = excludeEquip, extra = extraEquip,
  ) {
    setLoading(true); setResult(null); setNarration(""); setTrace([]); setClarify(null);
    await postSSE("/generate/stream",
      { member_id: memberId, prompt, time_minutes: time,
        avoid_joints: avoid, ignore_joints: ignore,
        exclude_equipment: exclude, extra_equipment: extra },
      (ev, data) => {
        if (ev === "result") {
          if (data.result.clarification) setClarify(data.result.clarification);
          else {
            setResult(data.result);
            setLastSig(sig(prompt, time, avoid, ignore, exclude, extra));
          }
          setTrace(data.trace);
        } else if (ev === "narration") setNarration((n) => n + data);
      });
    setLoading(false);
  }

  // coach answers one clarification → record the constraint and re-generate;
  // any remaining unrecognised joints get asked on the next pass.
  function resolve(joint: string, avoid: boolean) {
    const nextAvoid = avoid ? [...avoidJoints, joint] : avoidJoints;
    const nextIgnore = avoid ? ignoreJoints : [...ignoreJoints, joint];
    setAvoidJoints(nextAvoid); setIgnoreJoints(nextIgnore);
    run(nextAvoid, nextIgnore);
  }

  // coach confirms (or skips) a piece of equipment the system didn't recognise.
  // "use it" threads the term into extra_equipment; "skip" just re-runs.
  function resolveEquip(name: string, use: boolean) {
    const nextExtra = use ? [...extraEquip, name] : extraEquip;
    setExtraEquip(nextExtra);
    run(avoidJoints, ignoreJoints, excludeEquip, nextExtra);
  }

  // coach toggles one of the member's own equipment chips off/on for this
  // session. Staged only — it does NOT regenerate. The coach commits this (with
  // any other tweaks) via the primary button, so chip clicks never surprise-run.
  function toggleEquip(name: string) {
    setExcludeEquip((cur) =>
      cur.includes(name) ? cur.filter((e) => e !== name) : [...cur, name]);
  }

  return (
    <div className="panel">
      <h2>Workout Generator</h2>
      <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} maxLength={600}
        placeholder={`Describe the session for ${memberName || "this member"} — e.g. "full-body, pec isolation, 45 min" or "lower body, easy on the knee"`} />
      <div className="row">
        <label>Time
          <input type="number" value={time} min={15} max={90}
            onChange={(e) => setTime(+e.target.value)} /> min
        </label>
        <button className={"gen-btn" + (dirty ? " dirty" : "")}
          onClick={() => run()} disabled={loading || !memberId}>
          {loading ? "Generating…" : !result ? "Generate" : dirty ? "Update plan" : "Regenerate"}
        </button>
      </div>
      {result && dirty && !loading && (
        <div className="muted pending-note">Unsaved changes — “Update plan” to apply.</div>
      )}

      {equipment?.length > 0 && (
        <div className="equip-chips">
          <span className="equip-label">Equipment on hand</span>
          <div className="equip-row">
            {equipment.map((e: string) => {
              const off = excludeEquip.includes(e);
              return (
                <button
                  key={e}
                  className={"chip equip-chip" + (off ? " off" : "")}
                  disabled={loading || !memberId}
                  onClick={() => toggleEquip(e)}
                  title={off ? "Click to use this session" : "Click to skip this session"}
                >
                  {e}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {clarify && clarify.kind === "equipment" && (
        <div className="clarify">
          <div className="clarify-tag">Before I build this — one check</div>
          {clarify.questions.map((q: string, i: number) => (
            <div key={i} className="clarify-q">
              <span>{q}</span>
              {clarify.equipment[i] && (
                <div className="clarify-actions">
                  <button className="chip" onClick={() => resolveEquip(clarify.equipment[i], true)}>
                    Yes, {clarify.equipment[i]} has it — use it
                  </button>
                  <button className="chip ghost" onClick={() => resolveEquip(clarify.equipment[i], false)}>
                    No, skip it
                  </button>
                </div>
              )}
            </div>
          ))}
          <div className="muted">
            That's gear the graph doesn't recognise — so it asks instead of guessing.
            Confirm it and I'll allow exercises that need it.
          </div>
        </div>
      )}

      {clarify && clarify.kind !== "equipment" && (
        <div className="clarify">
          <div className="clarify-tag">
            {clarify.scope ? "What should this session focus on?" : "Before I build this — one check"}
          </div>
          {clarify.questions.map((q: string, i: number) => (
            <div key={i} className="clarify-q">
              <span>{q}</span>
              {clarify.joints[i] && (
                <div className="clarify-actions">
                  <button className="chip" onClick={() => resolve(clarify.joints[i], true)}>
                    Yes, avoid the {clarify.joints[i]}
                  </button>
                  <button className="chip ghost" onClick={() => resolve(clarify.joints[i], false)}>
                    No, it's fine
                  </button>
                </div>
              )}
            </div>
          ))}
          <div className="muted">
            {clarify.scope
              ? "That prompt didn't read as a training request, so I'd rather ask than invent one — edit it above and generate again."
              : "The graph found a constraint it can't confirm on file — so it asks instead of guessing. Your answer filters the exercise pool deterministically."}
          </div>
        </div>
      )}

      {(avoidJoints.length > 0 || excludeEquip.length > 0) && (
        <div className="muted constraint-note">
          This session:
          {avoidJoints.length > 0 ? ` avoiding ${avoidJoints.join(", ")}` : ""}
          {avoidJoints.length > 0 && excludeEquip.length > 0 ? " ·" : ""}
          {excludeEquip.length > 0 ? ` skipping ${excludeEquip.join(", ")}` : ""}
        </div>
      )}

      {trace.length > 0 && (
        <div className="trace">
          {trace.filter((e) => e.kind === "agent").map((e, i) => (
            <span key={i} className="trace-step">{e.name} <em>{e.ms}ms</em></span>
          ))}
          <span className={"badge " + (result?.degraded ? "warn" : "ok")}>
            {result?.degraded ? "no-LLM (deterministic)" : "safety ✓ ids⊆safe"}
          </span>
        </div>
      )}

      {narration && <div className="narration">{narration}</div>}

      {result && (
        <>
          <div className="workout">
            <Section title="Warmup" items={result.plan.warmup} />
            <Section title="Main" items={result.plan.main} />
            <Section title="Cooldown" items={result.plan.cooldown} />
          </div>

          <div className="evidence-row">
            <button className="link" onClick={() => setShow(show === "prov" ? null : "prov")}>
              Why these? (provenance)
            </button>
            <button className="link" onClick={() => setShow(show === "filt" ? null : "filt")}>
              Filtered for safety ({result.filtered_out.length})
            </button>
            <button className="link" onClick={() => setShow(show === "graph" ? null : "graph")}>
              Graph evidence
            </button>
          </div>

          {show === "prov" && (
            <div className="detail">
              {result.provenance.map((p: any) => (
                <div key={p.exercise_id} className="prov">
                  <b>{p.name}</b>
                  <div className="muted">chosen: {p.chosen_because.join("; ")}</div>
                  <div className="muted">safe: {p.safe_because.join("; ")}</div>
                </div>
              ))}
            </div>
          )}
          {show === "filt" && (
            <div className="detail">
              {result.filtered_out.map((f: any) => (
                <div key={f.id} className="prov">
                  <b className="unsafe">✗ {f.name}</b>
                  <div className="muted">reasons: {f.reasons.map((r: any) =>
                    r.via?.length ? `${r.type} (${r.via.join(", ")})` : r.type).join("; ")}</div>
                  {f.alternatives?.length > 0 &&
                    <div className="muted">try instead: {f.alternatives.join(", ")}</div>}
                </div>
              ))}
            </div>
          )}
          {show === "graph" && (
            <GraphEvidence memberName={memberName} injuries={injuries}
              plan={result.plan} filtered={result.filtered_out} />
          )}
        </>
      )}
    </div>
  );
}
