import { useEffect, useState } from "react";
import { getJSON } from "./api";
import { Generator } from "./components/Generator";
import { Copilot } from "./components/Copilot";
import "./index.css";

function Stat({ k, v, sub }: { k: string; v: any; sub?: any }) {
  return (
    <div className="stat">
      <div className="stat-k">{k}</div>
      <div className="stat-v">{v ?? "—"}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export default function App() {
  const [members, setMembers] = useState<any[]>([]);
  const [memberId, setMemberId] = useState("");
  const [ctx, setCtx] = useState<any>(null);
  const [imgOk, setImgOk] = useState(true);

  useEffect(() => {
    getJSON("/members").then((d) => {
      setMembers(d.members || []);
      if (d.members?.length) setMemberId(d.members[0].id);
    });
  }, []);
  useEffect(() => {
    setCtx(null);
    if (memberId) getJSON(`/members/${memberId}/longitudinal`).then(setCtx);
  }, [memberId]);

  const member = members.find((m) => m.id === memberId);
  const injuries: string[] = (member?.injuries || []).filter(Boolean);

  return (
    <div className="page">
      <header className="topbar">
        <span className="wordmark">FUTURE</span>
        <nav className="nav">
          <span className="nav-label">Coach dashboard</span>
          <select value={memberId} onChange={(e) => setMemberId(e.target.value)}>
            {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </nav>
      </header>

      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow"><span className="num">01</span> — Member</div>
          <h1 className="display">{member?.name || "—"}</h1>
          <div className="rule" />
          <div className="stats">
            <Stat k="Journey" v={ctx?.journey_stage} />
            <Stat k="Adherence"
              v={ctx?.adherence?.latest_pct != null ? ctx.adherence.latest_pct + "%" : "—"}
              sub={ctx?.adherence?.trend} />
            <Stat k="Injuries" v={injuries.length ? injuries.join(", ") : "none"} />
            {ctx?.oura && <Stat k="Sleep score" v={ctx.oura.avg_sleep_score} sub="Oura avg" />}
          </div>
        </div>

        <aside className="hero-aside">
          <div className="eyebrow"><span className="num">·</span> Brief</div>
          <p className="lede">
            {ctx?.churn_level ? `Churn risk ${ctx.churn_level}. ` : ""}
            {ctx?.oura ? "Recovery tracked via Oura. " : ""}
            Recommendations are driven by the knowledge graph — safe, explainable,
            and grounded in this member's own data.
          </p>
        </aside>

        <figure className="figure" aria-hidden="true">
          {imgOk
            ? <img src="/athlete.jpg" alt="" onError={() => setImgOk(false)} />
            : <div className="figure-fallback" />}
        </figure>
      </section>

      <section className="surface gen">
        <div className="eyebrow"><span className="num">02</span> — Generate</div>
        <Generator memberId={memberId} memberName={member?.name} injuries={injuries} />
      </section>

      <section className="surface cop">
        <div className="eyebrow"><span className="num">03</span> — Copilot</div>
        <Copilot memberId={memberId} />
      </section>

      <footer className="foot">
        Synthetic data only · knowledge-graph-backed · {members.length} members
      </footer>
    </div>
  );
}
