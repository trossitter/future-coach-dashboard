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

export function Copilot({ memberId }: any) {
  const [messages, setMessages] = useState<any[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [chart, setChart] = useState<any>(null);

  async function ask(q: string) {
    if (!q.trim() || !memberId) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "coach", text: q },
                            { role: "copilot", text: "", intent: "" }]);
    await postSSE("/copilot", { member_id: memberId, question: q }, (ev, data) => {
      if (ev === "context") {
        setMessages((m) => { const c = [...m]; c[c.length - 1].intent = data.result.intent; return c; });
      } else if (ev === "answer") {
        setMessages((m) => { const c = [...m]; c[c.length - 1].text += data; return c; });
      }
    });
    setBusy(false);
  }

  async function showChart(kind: string) {
    setChart(await getJSON(`/members/${memberId}/charts/${kind}`));
  }

  return (
    <div className="panel">
      <h2>AI Copilot</h2>

      <div className="quick">
        {QUICK.map((q) => (
          <button key={q} className="chip" onClick={() => ask(q)} disabled={busy}>{q}</button>
        ))}
      </div>

      <div className="thread">
        {messages.map((m, i) => (
          <div key={i} className={"msg " + m.role}>
            {m.role === "copilot" && m.intent && <span className="intent">{m.intent}</span>}
            <span className="msg-text">{m.text || (busy && i === messages.length - 1 ? "…" : "")}</span>
          </div>
        ))}
      </div>

      <div className="row">
        <input value={input} placeholder="Ask about this member…"
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
