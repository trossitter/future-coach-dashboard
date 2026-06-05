import { useEffect, useMemo, useRef, useState } from "react";
// @ts-ignore - react-force-graph-2d ships no types
import ForceGraph2D from "react-force-graph-2d";

const FG: any = ForceGraph2D;

const color = (g: string) =>
  g === "member" ? "#16161a" :
  g === "injury" ? "#b07a3c" :
  g === "safe" ? "#5f7a52" : "#b8634a";

/** Shows WHY the plan is safe: member → injury → contraindicated (red) vs the
 *  chosen safe exercises (green). The evidence behind the safety filter. */
export function GraphEvidence({ memberName, injuries, plan, filtered }: any) {
  // Fill the panel width responsively (was a fixed 430 that left a grey gap),
  // and refit after the simulation settles so nodes never drift off the canvas.
  const wrapRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = useMemo(() => {
    const nodes: any[] = [{ id: "member", label: memberName || "Member", group: "member" }];
    const links: any[] = [];
    (injuries || []).forEach((inj: string) => {
      nodes.push({ id: "inj:" + inj, label: inj, group: "injury" });
      links.push({ source: "member", target: "inj:" + inj });
    });
    const planEx = [
      ...(plan?.warmup || []), ...(plan?.main || []), ...(plan?.cooldown || []),
    ];
    planEx.slice(0, 8).forEach((p: any) => {
      nodes.push({ id: "ex:" + p.id, label: p.name, group: "safe" });
      links.push({ source: "member", target: "ex:" + p.id });
    });
    const injTarget = injuries?.[0] ? "inj:" + injuries[0] : "member";
    (filtered || []).slice(0, 6).forEach((f: any) => {
      nodes.push({ id: "flt:" + f.id, label: f.name, group: "unsafe" });
      links.push({ source: injTarget, target: "flt:" + f.id, kind: "contra" });
    });
    return { nodes, links };
  }, [memberName, injuries, plan, filtered]);

  return (
    <div className="graph-evidence" ref={wrapRef}>
      <FG
        ref={fgRef}
        graphData={data}
        width={width}
        height={320}
        cooldownTicks={80}
        onEngineStop={() => fgRef.current?.zoomToFit(400, 36)}
        backgroundColor="#eceef1"
        nodeRelSize={5}
        linkColor={(l: any) => (l.kind === "contra" ? "#b8634a" : "#d8d3c8")}
        linkWidth={(l: any) => (l.kind === "contra" ? 1.5 : 1)}
        nodeCanvasObject={(node: any, ctx: any, scale: number) => {
          ctx.fillStyle = color(node.group);
          ctx.beginPath();
          ctx.arc(node.x, node.y, node.group === "member" ? 6 : 4, 0, 2 * Math.PI);
          ctx.fill();
          if (scale > 1.1 || node.group === "member" || node.group === "injury") {
            ctx.font = `${11 / scale}px sans-serif`;
            ctx.fillStyle = "#3a3a3a";
            ctx.fillText(node.label, node.x + 7, node.y + 3);
          }
        }}
      />
      <div className="legend">
        <span className="dot" style={{ background: color("member") }} /> member
        <span className="dot" style={{ background: color("injury") }} /> injury
        <span className="dot" style={{ background: color("safe") }} /> chosen (safe)
        <span className="dot" style={{ background: color("unsafe") }} /> filtered (contraindicated)
      </div>
    </div>
  );
}
