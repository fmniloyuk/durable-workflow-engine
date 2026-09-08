'use client';

import { useEffect, useMemo, useState } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

type WorkflowSummary = { id:string; name:string; state:string; created_at:string; updated_at:string };
type Task = { id:string; task_key:string; kind:string; queue:string; state:string; dependencies:string[]; attempt:number; max_attempts:number; lease_owner:string|null; error:string|null };
type Event = { id:number; task_id:string|null; event_type:string; details:Record<string, unknown>; created_at:string };
type WorkflowDetail = WorkflowSummary & { tasks:Task[]; timeline:Event[] };
type Worker = { id:string; queues:string[]; concurrency:number; state:string; heartbeat_at:string };

type Point = { x:number; y:number; task:Task };

function Badge({ state }: { state:string }) {
  return <span className={`badge ${state}`}>{state.replaceAll('_', ' ')}</span>;
}

function WorkflowGraph({ tasks }: { tasks:Task[] }) {
  const { points, width, height } = useMemo(() => {
    const byKey = new Map(tasks.map((task) => [task.task_key, task]));
    const depths = new Map<string, number>();
    const depthOf = (key:string, seen = new Set<string>()):number => {
      if (depths.has(key)) return depths.get(key)!;
      if (seen.has(key)) return 0;
      seen.add(key);
      const task = byKey.get(key);
      const depth = !task || task.dependencies.length === 0 ? 0 : 1 + Math.max(...task.dependencies.map((dep) => depthOf(dep, new Set(seen))));
      depths.set(key, depth);
      return depth;
    };
    tasks.forEach((task) => depthOf(task.task_key));
    const layers = new Map<number, Task[]>();
    tasks.forEach((task) => {
      const depth = depths.get(task.task_key) || 0;
      layers.set(depth, [...(layers.get(depth) || []), task]);
    });
    const pts:Point[] = [];
    layers.forEach((layerTasks, depth) => layerTasks.forEach((task, index) => pts.push({ x:40 + depth * 220, y:35 + index * 90, task })));
    const maxDepth = Math.max(0, ...Array.from(layers.keys()));
    const maxRows = Math.max(1, ...Array.from(layers.values()).map((items) => items.length));
    return { points:pts, width:Math.max(720, 80 + (maxDepth + 1) * 220), height:Math.max(260, 70 + maxRows * 90) };
  }, [tasks]);
  const pointByKey = new Map(points.map((point) => [point.task.task_key, point]));
  return (
    <div className="graphWrap" aria-label="Workflow dependency graph">
      <svg className="graph" viewBox={`0 0 ${width} ${height}`} role="img">
        {points.flatMap((point) => point.task.dependencies.map((dep) => {
          const from = pointByKey.get(dep);
          return from ? <line key={`${dep}-${point.task.task_key}`} x1={from.x + 160} y1={from.y + 26} x2={point.x} y2={point.y + 26} /> : null;
        }))}
        {points.map(({ x, y, task }) => <g key={task.id} transform={`translate(${x} ${y})`}><rect width="160" height="54"/><text x="10" y="22">{task.task_key}</text><text className="state" x="10" y="41">{task.state} · attempt {task.attempt}</text></g>)}
      </svg>
    </div>
  );
}

export default function Page() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [dead, setDead] = useState<Task[]>([]);
  const [error, setError] = useState<string>('');

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const [wfRes, workerRes, deadRes] = await Promise.all([fetch(`${API}/v1/workflows`), fetch(`${API}/v1/workers`), fetch(`${API}/v1/dead-letter`)]);
        if (!wfRes.ok || !workerRes.ok || !deadRes.ok) throw new Error('API request failed');
        const wf = await wfRes.json() as WorkflowSummary[];
        const workerRows = await workerRes.json() as Worker[];
        const deadRows = await deadRes.json() as Task[];
        if (!active) return;
        setWorkflows(wf); setWorkers(workerRows); setDead(deadRows); setError('');
        if (!selected && wf[0]) setSelected(wf[0].id);
      } catch (e) { if (active) setError(e instanceof Error ? e.message : 'Unknown error'); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch(`${API}/v1/workflows/${selected}`);
        if (!response.ok) throw new Error('Workflow request failed');
        const data = await response.json() as WorkflowDetail;
        if (active) setDetail(data);
      } catch (e) { if (active) setError(e instanceof Error ? e.message : 'Unknown error'); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [selected]);

  const counts = useMemo(() => {
    const map = new Map<string, number>();
    detail?.tasks.forEach((task) => map.set(task.state, (map.get(task.state) || 0) + 1));
    return map;
  }, [detail]);

  return <main>
    <header><div><p className="muted">Distributed systems portfolio</p><h1>Durable Workflow Engine</h1><p className="muted">PostgreSQL truth · Redis Streams transport · lease-based workers</p></div><div>{error ? <span className="error">{error}</span> : <span className="muted">Auto-refreshing</span>}</div></header>
    <div className="grid">
      <aside className="panel"><h2>Workflows</h2><div className="stack">{workflows.map((wf) => <button className="workflowButton" aria-current={selected === wf.id} key={wf.id} onClick={() => setSelected(wf.id)}><div className="row"><strong>{wf.name}</strong><Badge state={wf.state}/></div><small className="muted">{new Date(wf.created_at).toLocaleString()}</small></button>)}{workflows.length === 0 && <p className="muted">No workflows yet.</p>}</div></aside>
      <section className="panel">{detail ? <><div className="row"><div><h2>{detail.name}</h2><p className="muted">{detail.id}</p></div><Badge state={detail.state}/></div><div className="statGrid"><div className="stat">Queued<strong>{counts.get('queued') || 0}</strong></div><div className="stat">Running<strong>{counts.get('running') || 0}</strong></div><div className="stat">Succeeded<strong>{counts.get('succeeded') || 0}</strong></div><div className="stat">Retries<strong>{detail.tasks.reduce((n,t) => n + Math.max(0,t.attempt-1),0)}</strong></div></div><h3>Dependency graph</h3><WorkflowGraph tasks={detail.tasks}/><h3 style={{marginTop:20}}>Tasks</h3><div style={{overflowX:'auto'}}><table className="table"><thead><tr><th>Task</th><th>Queue</th><th>Status</th><th>Attempt</th><th>Lease owner</th><th>Error</th></tr></thead><tbody>{detail.tasks.map((task) => <tr key={task.id}><td><strong>{task.task_key}</strong><br/><span className="muted">{task.kind}</span></td><td>{task.queue}</td><td><Badge state={task.state}/></td><td>{task.attempt}/{task.max_attempts}</td><td>{task.lease_owner || '—'}</td><td className="error">{task.error || '—'}</td></tr>)}</tbody></table></div></> : <p className="muted">Select a workflow.</p>}</section>
    </div>
    <div className="split"><section className="panel"><h2>Worker health</h2><table className="table"><thead><tr><th>Worker</th><th>Queues</th><th>State</th><th>Heartbeat</th></tr></thead><tbody>{workers.map((worker) => <tr key={worker.id}><td>{worker.id}</td><td>{worker.queues.join(', ')}</td><td><Badge state={worker.state}/></td><td>{new Date(worker.heartbeat_at).toLocaleTimeString()}</td></tr>)}</tbody></table></section><section className="panel"><h2>Dead-letter queue</h2>{dead.length === 0 ? <p className="muted">No durable dead-letter tasks.</p> : <ul>{dead.map((task) => <li key={task.id}>{task.task_key}: {task.error}</li>)}</ul>}</section></div>
    {detail && <section className="panel" style={{marginTop:20}}><h2>Execution timeline</h2><ol className="timeline">{detail.timeline.map((event) => <li key={event.id}><div className="row"><strong>{event.event_type}</strong><time className="muted">{new Date(event.created_at).toLocaleTimeString()}</time></div><small className="muted">{JSON.stringify(event.details)}</small></li>)}</ol></section>}
  </main>;
}
