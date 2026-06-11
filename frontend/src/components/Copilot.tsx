import { useEffect, useState } from "react";
import { postSSE, getJSON, type CopilotRequest } from "../api";
import { ChartView } from "./Charts";

// the live copilot thread is persisted per member so it survives closing the
// widget AND a page reload. Scoped by member id (matching the `key={sel.id}`
// remount) so threads never bleed across members. The coach clears it explicitly.
const threadKey = (id: string) => `copilot:thread:${id}`;
const loadThread = (id: string): any[] => {
  try { return JSON.parse(localStorage.getItem(threadKey(id)) || "[]"); }
  catch { return []; }
};

const QUICK = [
  "Show me the brief",
  "How's adherence trending?",
  "How did they sleep this week?",
  "Are they at risk of churning?",
  "What changed since last week?",
];

const CHARTS = [
  ["adherence", "Adherence"],
  ["sleep", "Sleep"],
  ["weight", "Weight"],
  ["messages", "Messages"],
];

// belt-and-suspenders: the model is told not to emit markdown, but strip it anyway
const sanitize = (t: string) =>
  t.replace(/\*+/g, "").replace(/_{2,}/g, "").replace(/^#+\s*/gm, "").trim();

// render the copilot sentence with clickable [n] citations that point back to the
// numbered facts above — every LLM claim traces to a graph-derived source.
function withCitations(text: string, onCite: (n: number) => void) {
  return sanitize(text).split(/(\[\d+\])/g).map((part, j) => {
    const hit = part.match(/^\[(\d+)\]$/);
    if (hit) {
      const n = Number(hit[1]);
      return (
        <button key={j} className="cite" title="show the source" onClick={() => onCite(n)}>
          [{n}]
        </button>
      );
    }
    return <span key={j}>{part}</span>;
  });
}

export function Copilot({ memberId, compact }: any) {
  // lazy-init from localStorage so a reopened/reloaded widget restores the thread
  const [messages, setMessages] = useState<any[]>(() => loadThread(memberId));
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [chart, setChart] = useState<any>(null);
  const [log, setLog] = useState<any[] | null>(null);   // real past chat thread
  const [cite, setCite] = useState<{ m: number; n: number } | null>(null);  // highlighted source

  // persist the live thread on every change (per member). Empty thread → clear
  // the key so a fresh member doesn't inherit a stale "[]".
  useEffect(() => {
    try {
      if (messages.length) localStorage.setItem(threadKey(memberId), JSON.stringify(messages));
      else localStorage.removeItem(threadKey(memberId));
    } catch { /* storage full / disabled — degrade to in-memory only */ }
  }, [messages, memberId]);

  // explicit reset — the coach wants a fresh thread for this member
  function clearThread() {
    setMessages([]);
    setCite(null);
    try { localStorage.removeItem(threadKey(memberId)); } catch { /* ignore */ }
  }

  async function ask(q: string) {
    if (!q.trim() || !memberId) return;
    setInput("");
    setBusy(true);
    // prior turns → context for follow-ups ("what about her sleep?"); the answer
    // still comes from the freshly-retrieved member slice, not the conversation.
    const history = messages
      .filter((m) => m.text)
      .map((m) => ({ role: m.role, text: m.role === "coach" ? m.text : sanitize(m.text) }));
    setMessages((m) => [...m, { role: "coach", text: q },
                            { role: "copilot", text: "", intent: "", facts: [] }]);
    const body: CopilotRequest = { member_id: memberId, question: q, history };
    await postSSE("/copilot", body, (ev, data) => {
      // pure updaters (no mutation) — mutating shared state doubled under StrictMode
      if (ev === "context") {
        setMessages((m) => m.map((msg, i) =>
          i === m.length - 1
            ? { ...msg, intent: data.result.intent, facts: data.result.facts || [] }
            : msg));
      } else if (ev === "answer") {
        setMessages((m) => m.map((msg, i) =>
          i === m.length - 1 ? { ...msg, text: msg.text + data } : msg));
      }
    });
    setBusy(false);
  }

  async function showChart(kind: string) {
    setChart(await getJSON(`/members/${memberId}/charts/${kind}`));
  }

  async function toggleLog() {
    if (log) { setLog(null); return; }
    const d = await getJSON(`/members/${memberId}/chat`);
    setLog(d.messages || []);
  }

  return (
    <div className={compact ? "copilot-body" : "panel"}>
      <div className="panel-head">
        {!compact && <h2>AI Copilot</h2>}
        <button className="link" onClick={toggleLog}>
          {log ? "Hide chat history" : "Chat history"}
        </button>
        {messages.length > 0 && (
          <button className="link" onClick={clearThread} title="Start a fresh thread">
            Clear
          </button>
        )}
      </div>

      {log && (
        <div className="chatlog">
          {log.length === 0 && <div className="muted">No messages on file.</div>}
          {log.map((m, i) => (
            <div key={i} className={"logmsg " + m.from}>
              <div className="log-meta">{m.from} · {m.ts?.slice(0, 10)}</div>
              {m.text && <div className="log-text">{m.text}</div>}
              {m.has_attachment && m.attachments?.map((cap: string, k: number) => (
                <div className="attachment" key={k}>
                  <span className="att-thumb" aria-hidden>▧</span>
                  <span className="att-cap">{cap}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      <div className="quick">
        {QUICK.map((q) => (
          <button key={q} className="chip" onClick={() => ask(q)} disabled={busy}>{q}</button>
        ))}
      </div>

      <div className="thread">
        {messages.map((m, i) => (
          <div key={i} className={"msg " + m.role}>
            {m.role === "copilot" && m.intent === "clarify" && (
              <span className="intent">Quick check</span>
            )}
            {m.role === "copilot" && m.facts?.length > 0 && (
              <div className="facts">
                {m.facts.map((f: any, k: number) => (
                  <div className={"fact" + (cite?.m === i && cite?.n === k + 1 ? " hot" : "")} key={k}>
                    <span className="f-label"><span className="f-n">[{k + 1}]</span>{f.label}</span>
                    <span className="f-value">{f.value}</span>
                    {f.source && <span className="f-src">{f.source}</span>}
                  </div>
                ))}
              </div>
            )}
            {(m.role === "coach" ? m.text : sanitize(m.text)) && (
              <span className="msg-text">
                {m.role === "coach" ? m.text : withCitations(m.text, (n) => setCite({ m: i, n }))}
              </span>
            )}
            {busy && i === messages.length - 1 && !m.text && !m.facts?.length && (
              <span className="msg-text">…</span>
            )}
          </div>
        ))}
      </div>

      <div className="row">
        <input value={input} placeholder="Ask about this member…" maxLength={500}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask(input)} />
        <button onClick={() => ask(input)} disabled={busy}>Ask</button>
      </div>

      <div className="quick charts-row">
        {CHARTS.map(([k, label]) => (
          <button key={k} className="chip" onClick={() => showChart(k)}>{label}</button>
        ))}
      </div>
      {chart && <ChartView spec={chart} />}
    </div>
  );
}
