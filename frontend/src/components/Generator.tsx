import { useState } from "react";
import { postSSE, postJSON } from "../api";
import { GraphEvidence } from "./GraphEvidence";
import { BodyThumb, regionForExercise } from "./BodyThumb";

type SectionKey = "warmup" | "main" | "cooldown";

// defaults applied to a pool item promoted into a prescription — section-aware
// so an added warmup reads as a warmup, a cooldown as a hold, etc.
const ADD_DEFAULTS: Record<SectionKey, { sets: number; reps: string; rest_seconds: number }> = {
  warmup: { sets: 1, reps: "8-10 reps", rest_seconds: 20 },
  cooldown: { sets: 1, reps: "30-45s hold", rest_seconds: 15 },
  main: { sets: 3, reps: "8-12 reps", rest_seconds: 90 },
};

function Section({
  title, section, items, onSave, saved,
  editable, pool, onDelete, onReorder, onAdd, onNote,
}: {
  title: string;
  section: SectionKey;
  items: any[];
  onSave?: (p: any) => void;
  saved?: string[];
  // editing wiring — when present the section renders coach controls
  editable?: boolean;
  pool?: any[];            // safe_pool items not already in the plan
  onDelete?: (section: SectionKey, id: string) => void;
  onReorder?: (section: SectionKey, fromId: string, toId: string) => void;
  onAdd?: (section: SectionKey, poolItem: any) => void;
  onNote?: (section: SectionKey, id: string, text: string) => void;
}) {
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  if (!items?.length && !editable) return null;
  return (
    <div className="wsection">
      <div className="wsection-title">{title}</div>
      {items.map((p) => (
        <div
          key={p.id}
          className={
            "prescription" +
            (editable ? " editable" : "") +
            (overId === p.id && dragId !== p.id ? " drop-target" : "") +
            (dragId === p.id ? " dragging" : "")
          }
          draggable={editable || undefined}
          onDragStart={editable ? () => setDragId(p.id) : undefined}
          onDragOver={editable ? (e) => { e.preventDefault(); setOverId(p.id); } : undefined}
          onDragLeave={editable ? () => setOverId((o) => (o === p.id ? null : o)) : undefined}
          onDrop={editable ? (e) => {
            e.preventDefault();
            if (dragId && dragId !== p.id) onReorder?.(section, dragId, p.id);
            setDragId(null); setOverId(null);
          } : undefined}
          onDragEnd={editable ? () => { setDragId(null); setOverId(null); } : undefined}
        >
          <div className="ex-line">
            {editable && <span className="drag-handle" aria-hidden title="Drag to reorder">⠿</span>}
            <div className="ex-body">
              <span className="ex-name">{p.name}</span>
              <span className="ex-rx">{p.sets} × {p.reps} · rest {p.rest_seconds}s</span>
            </div>
            {editable && (
              <button
                className="row-del"
                aria-label={`Remove ${p.name}`}
                title="Remove from plan"
                onClick={() => onDelete?.(section, p.id)}
              >×</button>
            )}
          </div>
          {editable && (
            typeof p.note === "string"
              ? <input className="ex-note" placeholder="Cue for the member — e.g. “pull the floor apart”"
                  value={p.note} onChange={(e) => onNote?.(section, p.id, e.target.value)} />
              : <button className="link ex-note-add" onClick={() => onNote?.(section, p.id, "")}>+ note</button>
          )}
          {onSave && (saved?.includes(p.id)
            ? <span className="lib-save saved">✓ in library</span>
            : <button className="link lib-save" onClick={() => onSave(p)}>+ library</button>)}
        </div>
      ))}
      {editable && (
        <div className="add-wrap">
          {!picking ? (
            <button className="link add-toggle" onClick={() => setPicking(true)}>+ add exercise</button>
          ) : (
            <div className="add-picker">
              <div className="add-picker-head">
                <span className="muted">Safe pool only</span>
                <button className="row-del" aria-label="Close picker" onClick={() => setPicking(false)}>×</button>
              </div>
              {pool && pool.length > 0 ? (
                <div className="add-list">
                  {pool.map((it) => (
                    <button
                      key={it.id}
                      className="add-item"
                      onClick={() => { onAdd?.(section, it); setPicking(false); }}
                    >
                      <span className="add-thumb"><BodyThumb region={regionForExercise(it)} /></span>
                      <span className="add-item-body">
                        <span className="ex-name">{it.name}</span>
                        {it.pattern && <span className="ex-rx">{it.pattern}</span>}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="muted add-empty">Every safe exercise is already in the plan.</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function Generator({ memberId, memberName, injuries, equipment }: any) {
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
  // ad-hoc, this-session equipment constraints (coach toggles a member's gear
  // off → exclude; clarify loop resolves unrecognised gear → extra)
  const [excludeEquip, setExcludeEquip] = useState<string[]>([]);
  const [extraEquip, setExtraEquip] = useState<string[]>([]);
  const [sent, setSent] = useState(false);   // plan delivered to the member's app
  const [savedIds, setSavedIds] = useState<string[]>([]);   // saved to coach library
  // local, transient coach edits to the generated plan (reorder/delete/add).
  // null ⇒ no edits yet, so the displayed plan is the freshly generated one.
  // Reset to null on every new generation so a regenerate discards manual edits.
  const [editedPlan, setEditedPlan] = useState<any>(null);
  // signature of the inputs that produced the shown plan. Lets us flag "unsaved
  // changes" so edits stage quietly instead of auto-regenerating on every
  // keystroke / time tick / equipment toggle.
  const [lastSig, setLastSig] = useState("");
  const sig = (p = prompt, t = time, aj = avoidJoints, ij = ignoreJoints,
               ex = excludeEquip, xt = extraEquip) =>
    JSON.stringify([p, t, [...aj].sort(), [...ij].sort(),
                    [...ex].sort(), [...xt].sort()]);
  const dirty = !!result && sig() !== lastSig;

  async function run(
    avoid = avoidJoints, ignore = ignoreJoints,
    exclude = excludeEquip, extra = extraEquip,
  ) {
    setLoading(true); setResult(null); setNarration(""); setTrace([]); setClarify(null); setSent(false);
    await postSSE("/generate/stream",
      { member_id: memberId, prompt, time_minutes: time,
        avoid_joints: avoid, ignore_joints: ignore,
        exclude_equipment: exclude, extra_equipment: extra },
      (ev, data) => {
        if (ev === "result") {
          if (data.result.clarification) setClarify(data.result.clarification);
          else {
            setResult(data.result);
            setEditedPlan(null);   // discard any manual edits from the prior plan
            setLastSig(sig(prompt, time, avoid, ignore, exclude, extra));
          }
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

  // coach confirms (or skips) a piece of equipment the system didn't recognise.
  // "use it" threads the term into extra_equipment; "skip" just re-runs.
  function resolveEquip(name: string, use: boolean) {
    const nextExtra = use ? [...extraEquip, name] : extraEquip;
    setExtraEquip(nextExtra);
    run(avoidJoints, ignoreJoints, excludeEquip, nextExtra);
  }

  // coach toggles one of the member's own equipment chips off/on for this
  // session. Staged only — it does NOT regenerate. The coach commits this (with
  // any other tweaks) via the primary button, so chip clicks never surprise-run.
  function toggleEquip(name: string) {
    setExcludeEquip((cur) =>
      cur.includes(name) ? cur.filter((e) => e !== name) : [...cur, name]);
  }

  // the plan the coach actually sees and acts on: their local edits if any,
  // otherwise the freshly generated plan straight from the backend.
  const displayedPlan = editedPlan ?? result?.plan;

  // ids already placed anywhere in the displayed plan — used to dedupe the
  // add-picker so the same exercise can't be added twice.
  const planIds: Set<string> = new Set(
    displayedPlan
      ? (["warmup", "main", "cooldown"] as const)
          .flatMap((s) => (displayedPlan[s] || []).map((p: any) => p.id))
      : []
  );

  // additions are constrained to safe_pool, minus anything already in the plan.
  // This is the ONLY source the picker draws from, so a coach can never insert
  // a contraindicated exercise — there is no free-text add path.
  const addablePool: any[] = (result?.safe_pool || []).filter((it: any) => !planIds.has(it.id));

  // produce a fresh, mutable copy of the current displayed plan to edit into.
  const cloneDisplayed = () => ({
    warmup: [...(displayedPlan?.warmup || [])],
    main: [...(displayedPlan?.main || [])],
    cooldown: [...(displayedPlan?.cooldown || [])],
  });

  function deleteExercise(section: SectionKey, id: string) {
    const next = cloneDisplayed();
    next[section] = next[section].filter((p: any) => p.id !== id);
    setEditedPlan(next);
  }

  function reorderExercise(section: SectionKey, fromId: string, toId: string) {
    const next = cloneDisplayed();
    const list = next[section];
    const from = list.findIndex((p: any) => p.id === fromId);
    const to = list.findIndex((p: any) => p.id === toId);
    if (from < 0 || to < 0 || from === to) return;
    const [moved] = list.splice(from, 1);
    list.splice(to, 0, moved);
    setEditedPlan(next);
  }

  function addExercise(section: SectionKey, poolItem: any) {
    // guard: only ever append something from safe_pool that isn't already placed.
    if (planIds.has(poolItem.id)) return;
    if (!(result?.safe_pool || []).some((it: any) => it.id === poolItem.id)) return;
    const d = ADD_DEFAULTS[section];
    const next = cloneDisplayed();
    next[section] = [...next[section], { id: poolItem.id, name: poolItem.name, ...d }];
    setEditedPlan(next);
  }

  // a per-exercise coaching cue, added while customizing the generated plan — a
  // cue is a per-session thing ("rep this out, buddy"), not a library property.
  // It rides along to the member with the plan.
  function setNote(section: SectionKey, id: string, text: string) {
    const next = cloneDisplayed();
    next[section] = next[section].map((p: any) => (p.id === id ? { ...p, note: text } : p));
    setEditedPlan(next);
  }

  // on-platform handoff: deliver the plan to the member's app/record rather than
  // exporting or printing it off-platform. Sends the DISPLAYED (edited) plan —
  // including any coach cues — so the member gets exactly what was customized.
  async function deliver() {
    const all = (["warmup", "main", "cooldown"] as const)
      .flatMap((s) => displayedPlan[s] || []);
    const ids = all.map((p: any) => p.id);
    const notes = all.filter((p: any) => p.note).map((p: any) => ({ name: p.name, note: p.note }));
    await postJSON(`/members/${memberId}/deliver`, { exercise_ids: ids, notes, summary: prompt });
    setSent(true);
  }

  // save a prescribed exercise into the coach's own library (local experiment)
  async function saveToLibrary(p: any) {
    await postJSON("/coach/library", { name: p.name, reps: `${p.sets} × ${p.reps}` });
    setSavedIds((s) => [...s, p.id]);
  }

  return (
    <div className="panel">
      <h2>Workout Generator</h2>
      <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} maxLength={600}
        placeholder={`Describe the session for ${memberName || "this member"} — e.g. "full-body, pec isolation, 45 min" or "lower body, easy on the knee"`} />
      <div className="row">
        <label>Time
          <input type="text" inputMode="numeric" value={time} min={15} max={90}
            onChange={(e) => setTime(e.target.value === "" ? 0
              : parseInt(e.target.value.replace(/\D/g, ""), 10) || 0)} /> min
        </label>
        <button className={"gen-btn" + (dirty ? " dirty" : "")}
          onClick={() => run()} disabled={loading || !memberId}>
          {loading ? "Generating…" : !result ? "Generate" : dirty ? "Update plan" : "Regenerate"}
        </button>
      </div>
      {result && dirty && !loading && (
        <div className="muted pending-note">Unsaved changes — “Update plan” to apply.</div>
      )}

      {equipment?.length > 0 && (
        <div className="equip-chips">
          <span className="equip-label">Equipment on hand</span>
          <div className="equip-row">
            {equipment.map((e: string) => {
              const off = excludeEquip.includes(e);
              return (
                <button
                  key={e}
                  className={"chip equip-chip" + (off ? " off" : "")}
                  disabled={loading || !memberId}
                  onClick={() => toggleEquip(e)}
                  title={off ? "Click to use this session" : "Click to skip this session"}
                >
                  {e}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {clarify && clarify.kind === "equipment" && (
        <div className="clarify">
          <div className="clarify-tag">Before I build this — one check</div>
          {clarify.questions.map((q: string, i: number) => (
            <div key={i} className="clarify-q">
              <span>{q}</span>
              {clarify.equipment[i] && (
                <div className="clarify-actions">
                  <button className="chip" onClick={() => resolveEquip(clarify.equipment[i], true)}>
                    Yes, {clarify.equipment[i]} has it — use it
                  </button>
                  <button className="chip ghost" onClick={() => resolveEquip(clarify.equipment[i], false)}>
                    No, skip it
                  </button>
                </div>
              )}
            </div>
          ))}
          <div className="muted">
            That's gear the graph doesn't recognise — so it asks instead of guessing.
            Confirm it and I'll allow exercises that need it.
          </div>
        </div>
      )}

      {clarify && clarify.kind !== "equipment" && (
        <div className="clarify">
          <div className="clarify-tag">
            {clarify.scope ? "What should this session focus on?" : "Before I build this — one check"}
          </div>
          {clarify.questions.map((q: string, i: number) => (
            <div key={i} className="clarify-q">
              <span>{q}</span>
              {clarify.joints[i] && (
                <div className="clarify-actions">
                  <button className="chip" onClick={() => resolve(clarify.joints[i], true)}>
                    Yes, avoid the {clarify.joints[i]}
                  </button>
                  <button className="chip ghost" onClick={() => resolve(clarify.joints[i], false)}>
                    No, it's fine
                  </button>
                </div>
              )}
            </div>
          ))}
          <div className="muted">
            {clarify.scope
              ? "That prompt didn't read as a training request, so I'd rather ask than invent one — edit it above and generate again."
              : "The graph found a constraint it can't confirm on file — so it asks instead of guessing. Your answer filters the exercise pool deterministically."}
          </div>
        </div>
      )}

      {(avoidJoints.length > 0 || excludeEquip.length > 0) && (
        <div className="muted constraint-note">
          This session:
          {avoidJoints.length > 0 ? ` avoiding ${avoidJoints.join(", ")}` : ""}
          {avoidJoints.length > 0 && excludeEquip.length > 0 ? " ·" : ""}
          {excludeEquip.length > 0 ? ` skipping ${excludeEquip.join(", ")}` : ""}
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
          <div className="muted edit-hint">Customize this plan — drag to reorder, × to remove, add only from the safe pool.</div>
          <div className="workout">
            {(["warmup", "main", "cooldown"] as const).map((s) => (
              <Section
                key={s}
                title={s[0].toUpperCase() + s.slice(1)}
                section={s}
                items={displayedPlan[s]}
                onSave={saveToLibrary}
                saved={savedIds}
                editable
                pool={addablePool}
                onDelete={deleteExercise}
                onReorder={reorderExercise}
                onAdd={addExercise}
                onNote={setNote}
              />
            ))}
          </div>

          <div className="deliver-row">
            {sent ? (
              <span className="sent-note">Sent to {memberName || "the member"} — it's in their plan ✓</span>
            ) : (
              <button className="gen-btn" onClick={deliver} disabled={loading}>
                Send to {memberName || "member"}
              </button>
            )}
          </div>

          <div className="evidence-row">
            <button className="link" onClick={() => setShow(show === "prov" ? null : "prov")}>
              Why these? (provenance)
            </button>
            <button className="link" onClick={() => setShow(show === "filt" ? null : "filt")}>
              Filtered out ({result.filtered_summary?.total ?? result.filtered_out.length})
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
            <>
              <div className="muted filt-summary">
                {result.filtered_summary?.unsafe ?? 0} unsafe · {result.filtered_summary?.equipment ?? 0} need equipment
                {(result.filtered_summary?.total ?? 0) > result.filtered_out.length
                  ? ` — showing ${result.filtered_out.length} of ${result.filtered_summary.total}` : ""}
              </div>
              <div className="detail">
                {result.filtered_out.map((f: any) => (
                  <div key={f.id} className="prov">
                    <b className="unsafe">✗ {f.name}</b>
                    <div className="muted">reasons: {f.reasons.map((r: any) =>
                      r.via?.length ? `${r.type} (${r.via.join(", ")})` : r.type).join("; ")}</div>
                    {f.alternatives?.length > 0 &&
                      <div className="muted">try instead: {f.alternatives.join(", ")}</div>}
                  </div>
                ))}
              </div>
            </>
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
