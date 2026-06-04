import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";

const COLORS = ["#7c2f3d", "#b07a3c", "#5f7a52"];
const tip = { background: "#ffffff", border: "1px solid rgba(20,22,26,0.12)", color: "#16161a", borderRadius: 8 };

export function ChartView({ spec }: { spec: any }) {
  if (!spec || !spec.series?.length) return <div className="muted">No data.</div>;
  const ys: string[] = spec.y || [];
  return (
    <div className="chart">
      <div className="chart-title">{spec.title}</div>
      <ResponsiveContainer width="100%" height={170}>
        {spec.type === "bar" ? (
          <BarChart data={spec.series}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e3e0d8" />
            <XAxis dataKey={spec.x} stroke="#9a948a" fontSize={10} />
            <YAxis stroke="#9a948a" fontSize={10} />
            <Tooltip contentStyle={tip} />
            {ys.map((y, i) => (
              <Bar key={y} dataKey={y} fill={COLORS[i % COLORS.length]} radius={[3, 3, 0, 0]} />
            ))}
          </BarChart>
        ) : (
          <LineChart data={spec.series}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e3e0d8" />
            <XAxis dataKey={spec.x} stroke="#9a948a" fontSize={10} />
            <YAxis stroke="#9a948a" fontSize={10} />
            <Tooltip contentStyle={tip} />
            {ys.map((y, i) => (
              <Line key={y} dataKey={y} stroke={COLORS[i % COLORS.length]} dot={false} strokeWidth={2} />
            ))}
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
