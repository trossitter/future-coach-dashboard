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
  const [prompt, setPrompt] = useState(
    "build lower-body strength without aggravating my knee",
  );
  const [time, setTime] = useState(45);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [trace, setTrace] = useState<any[]>([]);
  const [narration, setNarration] = useState("");
  const [show, setShow] = useState<string | null>(null);

  async function run() {
    setLoading(true); setResult(null); setNarration(""); setTrace([]);
    await postSSE("/generate/stream",
      { member_id: memberId, prompt, time_minutes: time },
      (ev, data) => {
        if (ev === "result") { setResult(data.result); setTrace(data.trace); }
        else if (ev === "narration") setNarration((n) => n + data);
      });
    setLoading(false);
  }

  return (
    <div className="panel">
      <h2>Workout Generator</h2>
      <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} />
      <div className="row">
        <label>Time
          <input type="number" value={time} min={15} max={90}
            onChange={(e) => setTime(+e.target.value)} /> min
        </label>
        <button onClick={run} disabled={loading || !memberId}>
          {loading ? "Generating…" : "Generate"}
        </button>
      </div>

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
