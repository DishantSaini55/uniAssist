import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './styles.css';

// Same-origin proxy: Vite forwards /api to FastAPI (see vite.config.js).
// In dev/Docker the Vite proxy serves /api; on Vercel set VITE_API_URL to the backend's public URL.
const API = (import.meta.env.VITE_API_URL || '/api').replace(/\/$/, '');

const prompts = [
  'What is the minimum attendance required to appear for end-semester exams?',
  'How do I apply for the supplementary exam?',
  'Am I eligible to appear in the exam for Data Structures?',
  'Am I eligible for the supplementary exam in Mathematics?',
  'I failed Data Structures. If I pass the supplementary, will I be eligible for placement?',
  'What is the scholarship for studying in Antarctica?',
];
const TYPE_LABEL = {
  retrieved_fact: 'Retrieved fact', calculated: 'Calculated by tool', not_found: 'Not found',
  clarification_needed: 'Clarification needed', refused: 'Refused', conflict_flagged: 'Conflict flagged',
};

const Panel = ({title, children, open = false}) =>
  <details className="panel" open={open}><summary>{title}<span>⌄</span></summary><div className="panel-body">{children}</div></details>;
const Json = ({data}) => { try { return <pre>{JSON.stringify(data ?? {}, null, 2)}</pre>; } catch { return <pre>Could not render.</pre>; } };

class ErrorBoundary extends React.Component {
  constructor(props) { super(props); this.state = {error: null}; }
  static getDerivedStateFromError(error) { return {error}; }
  render() {
    return this.state.error
      ? <main style={{padding: 40, color: '#e9eef8', background: '#07111f', minHeight: '100vh'}}><h2>Display error</h2><p>The backend is unaffected. Refresh to retry.</p><pre>{this.state.error.message}</pre></main>
      : this.props.children;
  }
}

function Citations({items}) {
  return <table className="cites"><thead><tr><th>Document</th><th>Section</th><th>Page</th><th>Version</th><th>Effective</th></tr></thead>
    <tbody>{items.map((c, i) => <tr key={i}><td title={c.title}>{c.doc_id}</td><td>{c.section ?? '-'}</td><td>{c.page ?? '-'}</td><td>{c.version ?? '-'}</td><td>{c.effective_from ?? '-'}</td></tr>)}</tbody></table>;
}

function AssistantMessage({data, onAudit, audit}) {
  const d = data && typeof data === 'object' ? data : {answer: 'Unexpected response.'};
  const cites = Array.isArray(d.citations) ? d.citations : [];
  return <div className="message assistant">
    <span>uniAssist {d.answer_type && <em className={'tag ' + d.answer_type}>{TYPE_LABEL[d.answer_type] || d.answer_type}</em>}</span>
    <p style={{whiteSpace: 'pre-wrap'}}>{typeof d.answer === 'string' ? d.answer : 'No answer.'}</p>
    {d.explanation && <p className="explain">{d.explanation}</p>}
    {d.assumptions?.length > 0 && <ul className="assume">{d.assumptions.map((a, i) => <li key={i}>Assumption: {a}</li>)}</ul>}
    <div className="grid">
      {cites.length > 0 && <Panel title={`Citations (${cites.length})`} open><Citations items={cites}/></Panel>}
      {d.applied_rules?.length > 0 && <Panel title="Applied rules"><Json data={d.applied_rules}/></Panel>}
      {d.tools_invoked?.length > 0 && <Panel title={`Tools invoked (${d.tools_invoked.length})`}><Json data={d.tools_invoked}/></Panel>}
      {d.conflicts_detected?.length > 0 && <Panel title="Conflicts detected" open><Json data={d.conflicts_detected}/></Panel>}
    </div>
    {d.trace_id && <button className="link" onClick={() => onAudit(d.trace_id)}>trace {d.trace_id} · view audit record</button>}
    {audit && <Panel title="Audit record" open><Json data={audit}/></Panel>}
  </div>;
}

function Ingest({onDone}) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  async function submit(e) {
    e.preventDefault();
    const f = new FormData(e.target);
    const file = f.get('file');
    if (!file || !file.size) { setMsg({error: 'Choose a file.'}); return; }
    const meta = {};
    ['doc_id', 'title', 'issuer', 'authority_level', 'doc_type', 'version', 'effective_from', 'effective_to', 'supersedes', 'scope_programmes', 'scope_batches', 'provenance']
      .forEach(k => { const v = (f.get(k) || '').toString().trim(); if (v) meta[k] = v; });
    const body = new FormData(); body.append('file', file); body.append('metadata', JSON.stringify(meta));
    setBusy(true); setMsg(null);
    try {
      const r = await fetch(`${API}/ingest`, {method: 'POST', body});
      const j = await r.json(); setMsg(r.ok ? j : {error: JSON.stringify(j.detail ?? j)}); if (r.ok) onDone();
    } catch (err) { setMsg({error: String(err)}); } finally { setBusy(false); }
  }
  return <form className="ingest" onSubmit={submit}>
    <input type="file" name="file" accept=".pdf,.txt,.md"/>
    <input name="doc_id" placeholder="doc_id (e.g. ACAD-2026-09)" required/>
    <input name="title" placeholder="title" required/>
    <input name="issuer" placeholder="issuer" required/>
    <div className="row">
      <select name="authority_level" defaultValue="2">{[1, 2, 3, 4, 5].map(n => <option key={n}>{n}</option>)}</select>
      <select name="doc_type" defaultValue="circular">{['regulation', 'circular', 'notice', 'faq', 'handbook', 'unofficial'].map(t => <option key={t}>{t}</option>)}</select>
    </div>
    <input name="version" placeholder="version" defaultValue="1.0"/>
    <label>effective_from<input type="date" name="effective_from" required/></label>
    <input name="supersedes" placeholder="supersedes (DOC-ID#7.2;...)"/>
    <input name="scope_programmes" placeholder="scope_programmes" defaultValue="ALL"/>
    <input name="scope_batches" placeholder="scope_batches" defaultValue="ALL"/>
    <button disabled={busy}>{busy ? 'Ingesting…' : 'Ingest document'}</button>
    {msg && <pre className={msg.error ? 'err' : 'ok'}>{JSON.stringify(msg, null, 1)}</pre>}
  </form>;
}

function App() {
  const [studentId, setStudentId] = useState('S1001');
  const [asOf, setAsOf] = useState('');
  const [query, setQuery] = useState('');
  const [messages, setMessages] = useState([]);
  const [health, setHealth] = useState(null);
  const [sending, setSending] = useState(false);
  const [audits, setAudits] = useState({});
  const [showIngest, setShowIngest] = useState(false);

  const refreshHealth = () => fetch(`${API}/health`).then(r => r.json()).then(setHealth).catch(() => setHealth({status: 'offline'}));
  useEffect(() => { refreshHealth(); }, []);

  async function ask(value = query) {
    const text = value.trim(); if (!text || sending) return;
    setMessages(m => [...m, {role: 'user', text}]); setQuery(''); setSending(true);
    try {
      const headers = {'Content-Type': 'application/json'};
      if (studentId.trim()) headers['X-Student-Id'] = studentId.trim();
      const body = {question: text}; if (asOf) body.as_of_date = asOf;
      const r = await fetch(`${API}/ask`, {method: 'POST', headers, body: JSON.stringify(body)});
      const data = await r.json().catch(() => ({answer: 'Invalid response from the backend.'}));
      if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request rejected');
      setMessages(m => [...m, {role: 'assistant', data}]);
    } catch (e) { setMessages(m => [...m, {role: 'assistant', data: {answer: 'The request failed: ' + e.message, answer_type: 'refused'}}]); }
    finally { setSending(false); }
  }
  async function loadAudit(id) {
    const r = await fetch(`${API}/audit/${id}`);
    if (r.ok) { const j = await r.json(); setAudits(a => ({...a, [id]: j})); }
  }
  const c = health?.components;
  return <main className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="crest">u</div><div><b>uniAssist</b><small>University Student Services</small></div></div>
      <section><p className="eyebrow">LOGGED-IN STUDENT</p>
        <label>X-Student-Id<input value={studentId} onChange={e => setStudentId(e.target.value.toUpperCase())} placeholder="S1001 (blank = general questions)" maxLength="5"/></label>
        <label style={{marginTop: 12}}>As-of date<input type="date" value={asOf} onChange={e => setAsOf(e.target.value)}/></label>
        <p className="hint">Identity is sent only in the request header, never read from the question text. Blank as-of date means today.</p></section>
      <section className="status"><span className={'dot ' + (health?.status === 'ok' ? 'online' : 'offline')}/>
        <div><b>System {health?.status ?? 'checking'}</b><small>{c ? `${c.vector_store?.chunks ?? 0} chunks · ${c.sqlite?.documents ?? 0} docs · LLM ${c.llm?.status}` : 'API'}</small></div></section>
      <section><button className="link" onClick={() => setShowIngest(s => !s)}>{showIngest ? 'Hide' : 'Add a document (POST /ingest)'}</button>
        {showIngest && <Ingest onDone={refreshHealth}/>}</section>
      <footer>uniAssist<br/><small>Cited · Deterministic tools · Auditable</small></footer>
    </aside>
    <section className="workspace">
      <header><div><p className="eyebrow">EVIDENCE-FIRST ASSISTANT</p><h1>Ask about rules, procedures and your own records.</h1></div></header>
      <div className="conversation">
        {messages.length === 0 && <div className="welcome"><div className="spark">✦</div><h2>How can I help today?</h2>
          <p>Policy answers carry a document, section, page, version and effective date. Personal answers are computed by tools from your records.</p>
          <div className="suggestions">{prompts.map(p => <button key={p} onClick={() => ask(p)}>{p}</button>)}</div></div>}
        {messages.map((m, i) => m.role === 'user'
          ? <div key={i} className="message user"><span>You</span><p>{m.text}</p></div>
          : <AssistantMessage key={i} data={m.data} onAudit={loadAudit} audit={audits[m.data?.trace_id] || null}/>)}
        {sending && <div className="typing">Checking authorised sources<span>•••</span></div>}
      </div>
      <form className="composer" onSubmit={e => { e.preventDefault(); ask(); }}>
        <textarea value={query} onChange={e => setQuery(e.target.value)} placeholder="Ask a question…" rows="2"/>
        <button disabled={sending || !query.trim()}>{sending ? 'Working…' : 'Send ↗'}</button></form>
    </section>
  </main>;
}
createRoot(document.getElementById('root')).render(<ErrorBoundary><App/></ErrorBoundary>);
