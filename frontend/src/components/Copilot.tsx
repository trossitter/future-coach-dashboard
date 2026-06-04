import { useState } from "react";
import { postSSE, getJSON } from "../api";
import { ChartView } from "./Charts";

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

export function Copilot({ memberId }: any) {
  const [messages, setMessages] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [chart, setChart] = useState<any>(null);
  const [log, setLog] = useState<any[] | null>(null);   // real past chat thread

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
    await postSSE("/copilot", { member_id: memberId, question: q, history }, (ev, data) => {
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
    <div className="panel">
      <div className="panel-head">
        <h2>AI Copilot</h2>
        <button className="link" onClick={toggleLog}>
          {log ? "Hide chat history" : "Chat history"}
        </button>
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
                  <div className="fact" key={k}>
                    <span className="f-label">{f.label}</span>
                    <span className="f-value">{f.value}</span>
                  </div>
                ))}
              </div>
            )}
            {(m.role === "coach" ? m.text : sanitize(m.text)) && (
              <span className="msg-text">{m.role === "coach" ? m.text : sanitize(m.text)}</span>
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
