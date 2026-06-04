import { useEffect, useState } from "react";
import { getJSON } from "./api";
import { Generator } from "./components/Generator";
import { Copilot } from "./components/Copilot";
import "./index.css";

const today = new Date().toLocaleDateString(undefined, {
  weekday: "long", month: "long", day: "numeric",
});

const flagged = (m: any) =>
  m.churn_level === "elevated" || m.adherence_trend === "declining";

export default function App() {
  const [roster, setRoster] = useState<any[]>([]);
  const [sel, setSel] = useState<any | null>(null);
  const [imgOk, setImgOk] = useState(true);

  useEffect(() => { getJSON("/roster").then((d) => setRoster(d.members || [])); }, []);

  const needs = roster.filter(flagged);
  const headline =
    roster.length === 0 ? "Loading…" :
    needs.length === 0 ? "Everyone's on track today." :
    `${needs.length} member${needs.length > 1 ? "s" : ""} need${needs.length > 1 ? "" : "s"} you this morning.`;

  return (
    <div className="page">
      <header className="topbar">
        <span className="wordmark">FUTURE</span>
        <nav className="nav"><span className="nav-label">Coach dashboard · Sam</span></nav>
      </header>

      {/* front and center: what the coach needs, not a single member */}
      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow">Today · {today}</div>
          <h1 className="display">{headline}</h1>
          <div className="rule" />
          <p className="lede">
            Recommendations are driven by the knowledge graph — safe, explainable,
            and grounded in each member's own data. Choose a member to begin.
          </p>
        </div>
        <figure className="figure" aria-hidden="true">
          {imgOk
            ? <img src="/athlete.jpg" alt="" onError={() => setImgOk(false)} />
            : <div className="figure-fallback" />}
        </figure>
      </section>

      <section className="surface">
        <div className="section-label">Members</div>
        <div className="roster">
          {roster.map((m) => (
            <button
              key={m.id}
              className={"member-card" + (sel?.id === m.id ? " active" : "") + (flagged(m) ? " flagged" : "")}
              onClick={() => setSel(m)}
            >
              <div className="card-top">
                <span className="card-name">{m.name}</span>
                {flagged(m) && <span className="flag">needs attention</span>}
              </div>
              <div className="card-stats">
                <span>{m.journey_stage || "—"}</span>
                <span>adherence {m.adherence_pct != null ? m.adherence_pct + "%" : "—"}
                  {m.adherence_trend ? ` · ${m.adherence_trend}` : ""}</span>
                {m.churn_level && <span>churn {m.churn_level}</span>}
                {m.injuries?.length > 0 && <span>{m.injuries.join(", ")}</span>}
                {m.sleep_score && <span>sleep {m.sleep_score}</span>}
              </div>
            </button>
          ))}
        </div>
      </section>

      {sel && (
        <>
          <section className="surface">
            <div className="section-label">Working with</div>
            <h2 className="member-headline">{sel.name}</h2>
            <Generator memberId={sel.id} memberName={sel.name}
              injuries={(sel.injuries || []).filter(Boolean)} />
          </section>
          <section className="surface">
            <Copilot memberId={sel.id} />
          </section>
        </>
      )}

      <footer className="foot">
        Synthetic data only · knowledge-graph-backed · {roster.length} members
      </footer>
    </div>
  );
}
