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

export function Generator({ memberId, memberName, injuries }: any) {
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

  async function run(avoid = avoidJoints, ignore = ignoreJoints) {
    setLoading(true); setResult(null); setNarration(""); setTrace([]); setClarify(null);
    await postSSE("/generate/stream",
      { member_id: memberId, prompt, time_minutes: time,
        avoid_joints: avoid, ignore_joints: ignore },
      (ev, data) => {
        if (ev === "result") {
          if (data.result.clarification) setClarify(data.result.clarification);
          else setResult(data.result);
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
        <button onClick={() => run()} disabled={loading || !memberId}>
          {loading ? "Generating…" : "Generate"}
        </button>
      </div>

      {clarify && (
        <div className="clarify">
          <div className="clarify-tag">Before I build this — one check</div>
          {clarify.questions.map((q: string, i: number) => (
            <div key={i} className="clarify-q">
              <span>{q}</span>
              <div className="clarify-actions">
                <button className="chip" onClick={() => resolve(clarify.joints[i], true)}>
                  Yes, avoid the {clarify.joints[i]}
                </button>
                <button className="chip ghost" onClick={() => resolve(clarify.joints[i], false)}>
                  No, it's fine
                </button>
              </div>
            </div>
          ))}
          <div className="muted">
            The graph found a constraint it can't confirm on file — so it asks
            instead of guessing. Your answer filters the exercise pool deterministically.
          </div>
        </div>
      )}

      {avoidJoints.length > 0 && (result || clarify) && (
        <div className="muted constraint-note">
          Avoiding this session: {avoidJoints.join(", ")} (coach-confirmed)
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
                  <div className="muted">reasons: {f.reasons.map((r: any) => r.type).join(", ")}</div>
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
