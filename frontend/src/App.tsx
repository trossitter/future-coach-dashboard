import { useEffect, useState } from "react";
import { getJSON } from "./api";
import { Generator } from "./components/Generator";
import { Copilot } from "./components/Copilot";
import { CoachLibrary } from "./components/CoachLibrary";
import { ProtocolsTeaser } from "./components/ProtocolsTeaser";
import "./index.css";

const today = new Date().toLocaleDateString(undefined, {
  weekday: "long", month: "long", day: "numeric",
});

const flagged = (m: any) =>
  m.churn_level === "elevated" || m.adherence_trend === "declining";

function status(m: any): { label: string; tone: string } {
  if (flagged(m)) return { label: "Needs attention", tone: "alert" };
  if (m.journey_stage === "onboarding") return { label: "Onboarding", tone: "new" };
  if (m.journey_stage === "progressing") return { label: "Progressing", tone: "ok" };
  return { label: "On track", tone: "ok" };
}

const trendArrow = (t: string) =>
  t === "declining" ? "↓" : t === "improving" ? "↑" : t === "steady" ? "→" : "";

export default function App() {
  const [roster, setRoster] = useState<any[]>([]);
  const [sel, setSel] = useState<any | null>(null);
  const [imgOk, setImgOk] = useState(true);
  const [showLib, setShowLib] = useState(false);   // coach library (local experiment)
  const [chatOpen, setChatOpen] = useState(false); // floating copilot widget
  const [showProtocols, setShowProtocols] = useState(false); // protocols "coming soon" view

  useEffect(() => { getJSON("/roster").then((d) => setRoster(d.members || [])); }, []);

  const needs = roster.filter(flagged);
  const headline =
    roster.length === 0 ? "Loading…" :
    needs.length === 0 ? "Everyone's on track today." :
    `${needs.length} member${needs.length > 1 ? "s" : ""} need${needs.length > 1 ? "" : "s"} you this morning.`;

  return (
    <>
      {/* the athlete stays fixed in the background; content scrolls over it */}
      <div className="bg-figure" aria-hidden="true">
        {imgOk
          ? <img src="/athlete.jpg" alt="" onError={() => setImgOk(false)} />
          : <div className="figure-fallback" />}
        <div className="bg-scrim" />
      </div>

      <header className="topbar">
        <span className="wordmark">FUTURE</span>
        <nav className="nav">
          <button className="nav-label nav-link" onClick={() => setShowLib(true)}>
            Coach dashboard · Sam ▾
          </button>
        </nav>
      </header>
      {showLib && <CoachLibrary onClose={() => setShowLib(false)} />}

      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow">Today · {today}</div>
          <h1 className="display">{headline}</h1>
          <div className="rule" />
          <p className="lede">
            Their whole picture, in one place. Build safe, personal sessions and
            get grounded answers — fast.
          </p>
          <div className="hero-cta">Select a member to begin</div>
        </div>
      </section>

      {/* opaque sheet scrolls up over the fixed figure */}
      <div className="sheet">
        <div className="sheet-inner">
          <section className="surface">
            <div className="section-label">Members</div>
            <div className="roster">
              {roster.map((m) => (
                <button
                  key={m.id}
                  className={"member-card" + (sel?.id === m.id ? " active" : "")}
                  onClick={() => setSel(m)}
                >
                  <div className="card-top">
                    <span className="card-name">{m.name}</span>
                    <span className={"status " + status(m).tone}>{status(m).label}</span>
                  </div>
                  <div className="card-metrics">
                    <div className="metric">
                      <span className="m-label">Adherence</span>
                      <span className="m-val">
                        {m.adherence_pct != null ? `${m.adherence_pct}% ${trendArrow(m.adherence_trend)}` : "—"}
                      </span>
                    </div>
                    <div className="metric">
                      <span className="m-label">Injuries</span>
                      <span className="m-val">{m.injuries?.length ? m.injuries.join(", ") : "none"}</span>
                    </div>
                    <div className="metric">
                      <span className="m-label">Sleep score</span>
                      <span className="m-val">{m.sleep_score ?? "—"}</span>
                    </div>
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
                <Generator key={sel.id} memberId={sel.id} memberName={sel.name}
                  injuries={(sel.injuries || []).filter(Boolean)}
                  equipment={(sel.equipment || []).filter(Boolean)}
                  dislikes={(sel.dislikes || []).filter(Boolean)} />
              </section>
            </>
          )}

          <footer className="foot">
            Synthetic data only · knowledge-graph-backed · {roster.length} members
          </footer>
          <button className="protocols-hint" onClick={() => setShowProtocols(true)}
            style={{ fontSize: "16px" }}>
            Coming soon…
          </button>
        </div>
      </div>

      {showProtocols && <ProtocolsTeaser onClose={() => setShowProtocols(false)} />}

      {/* floating AI copilot — a chat widget scoped to the selected member */}
      {chatOpen && (
        <div className="copilot-window" role="dialog" aria-label="AI Copilot">
          <div className="copilot-window-head">
            <span className="copilot-title">
              Copilot{sel ? ` · ${sel.name}` : ""}
            </span>
            <button
              className="copilot-x"
              aria-label="Close copilot"
              onClick={() => setChatOpen(false)}
            >
              ×
            </button>
          </div>
          {sel ? (
            <Copilot key={sel.id} memberId={sel.id} compact />
          ) : (
            <div className="copilot-empty">Select a member first.</div>
          )}
        </div>
      )}

      {/* quiet designer signature, bottom-left — kept at the hint's small size */}
      <div
        style={{
          position: "fixed", left: 20, bottom: 14, zIndex: 5,
          fontSize: "12.5px", letterSpacing: "0.08em", fontStyle: "italic",
          color: "var(--sub)", pointerEvents: "none",
        }}
      >
        designed by Thalia
      </div>

      <button
        className={"copilot-launcher" + (chatOpen ? " open" : "")}
        aria-label={chatOpen ? "Close copilot" : "Open copilot"}
        aria-expanded={chatOpen}
        onClick={() => setChatOpen((v) => !v)}
      >
        {chatOpen ? "×" : "💬"}
      </button>
    </>
  );
}
