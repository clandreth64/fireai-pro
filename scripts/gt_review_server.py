"""Local human review tool for the real-drawing corpus (review schema human_review/3).

Start it with ONE command from the repository folder (see docs/HUMAN_VALIDATION_GUIDE.md):
    Windows:      powershell -ExecutionPolicy Bypass -File scripts\\review.ps1
    macOS/Linux:  sh scripts/review.sh
then open http://127.0.0.1:8765

The reviewer answers questions a fire-protection professional can answer by looking at the
drawing: a few FACTS (units, drawing type, number and type of views) and STATEMENTS about FireAI's
interpretation (rooms recognized, labels, boundaries, walls, openings, ...), each CONFIRMED /
CORRECTED / NOT_EVALUATED. Problems can be pointed at directly on the drawing canvas (click a
FireAI item and say what is wrong, or click where something is missing). No CAD counting or
coordinates are ever asked for.

Reviews are written to a separate file (Claude's draft is never modified): public drawings ->
tests/real_drawings/human_reviews/, private drawings -> tests/real_drawings_local/human_reviews/
(git-ignored). Local use only; the reviewer name is NOT an authenticated identity.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "real_drawings"))
sys.path.insert(0, str(ROOT / "scripts"))
import fireai_values as FV  # noqa: E402
import gt as G  # noqa: E402
from gt_review_summary import build_summary, to_markdown  # noqa: E402

H = html.escape
CSS = """body{font:14px system-ui,sans-serif;margin:16px;max-width:1500px}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ccc;padding:6px 8px;vertical-align:top}th{background:#f3f3f3;text-align:left}
.imgs{display:flex;gap:1%}.imgs figure{margin:0;width:49.5%}.imgs img{width:100%;border:1px solid #999}
.PENDING_HUMAN_VERIFICATION{color:#b26a00}.HUMAN_VERIFIED{color:#1b7f1b}.INVALIDATED{color:#b00020}
.PARTIALLY_HUMAN_REVIEWED{color:#0b5cad}pre{white-space:pre-wrap;margin:0;font-size:12px}small{color:#555}
.banner{background:#fdecea;padding:8px;border:1px solid #f5c2c0}.note{background:#eef6ff;padding:8px;border:1px solid #bcd}
#cv{width:100%;height:620px;border:1px solid #999;background:#fff;cursor:crosshair}.fai{cursor:pointer}
.tool button{margin-right:6px}.tool button.on{background:#0b5cad;color:#fff}#flaglist li{margin:2px 0}
textarea,input[type=text]{width:95%}.stmt{font-weight:600}"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<html><head><meta charset='utf-8'><title>{H(title)}</title><style>{CSS}</style></head>"
                        f"<body>{body}</body></html>")


def _show(v) -> str:
    if v in (None, {}, []):
        return "<i>—</i>"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items())
    return H(str(v))


def _fact_input(q: str, prev) -> str:
    spec, n = G.FACTS[q], f"{q}__value"
    if spec["kind"] == "choice":
        opts = "".join(f"<option {'selected' if prev == c else ''}>{c}</option>" for c in spec["choices"])
        return f"<select name='{n}'><option value=''>(choose)</option>{opts}</select>"
    if spec["kind"] == "int":
        return f"<input type='number' min='0' style='width:6em' name='{n}' value='{'' if prev is None else prev}'>"
    if spec["kind"] == "counts":
        prev = prev or {}
        return " ".join(f"<label>{t.replace('_', ' ').lower()} <input type='number' min='0' style='width:4em' "
                        f"name='{n}__{t}' value='{prev.get(t, '')}'></label>" for t in spec["keys"])
    return f"<input type='text' name='{n}' value='{H(prev or '')}'>"


def _raw_fact(q: str, form):
    n = f"{q}__value"
    if G.FACTS[q]["kind"] == "counts":
        return {t: form.get(f"{n}__{t}") or "" for t in G.FACTS[q]["keys"]}
    return form.get(n)


JS = r"""
const C = JSON.parse(document.getElementById('canvasdata').textContent);
let flags = JSON.parse(document.getElementById('flagsdata').textContent);
const svg = document.getElementById('cv'), NS = 'http://www.w3.org/2000/svg';
const [x0, y0, x1, y1] = C.bounds, pad = Math.max(x1 - x0, y1 - y0) * 0.03;
let vb = [x0 - pad, -(y1 + pad), (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad];
const setVB = () => svg.setAttribute('viewBox', vb.join(' '));
setVB();
const g = document.createElementNS(NS, 'g'); g.setAttribute('transform', 'scale(1,-1)'); svg.appendChild(g);
const sw = Math.max(vb[2], vb[3]) / 900;
function poly(pts, attrs) {
  const e = document.createElementNS(NS, 'polyline');
  e.setAttribute('points', pts.map(p => p[0] + ',' + p[1]).join(' '));
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  g.appendChild(e); return e;
}
for (const l of C.lines) poly(l, {fill: 'none', stroke: '#c8c8c8', 'stroke-width': sw});
const COL = {room: '#2e8b57', wall: '#1f4e9c', door: '#d62728', window: '#00a0b0', stair: '#9467bd', column: '#2ca02c',
             existing_fire_protection: '#ff7f0e', area: '#6a3d9a', depiction: '#b8860b'};
const byUid = {};
for (const it of C.items) {
  const col = COL[it.category] || '#8c564b', room = it.category === 'room' || it.category === 'area';
  const pts = it.closed ? it.points.concat([it.points[0]]) : it.points;
  const e = poly(pts, {fill: room ? col : 'none', 'fill-opacity': room ? 0.15 : 0, stroke: col,
                       'stroke-width': sw * (room ? 2 : 1.5), class: 'fai',
                       'stroke-dasharray': it.flagged_by_fireai ? (sw * 6) + ',' + (sw * 4) : ''});
  const t = document.createElementNS(NS, 'title');
  t.textContent = it.category + (it.label ? ' "' + it.label + '"' : '') + (it.subtype ? ' (' + it.subtype + ')' : '') +
                  (it.contains && it.contains.length > 1 ? ' — contains named spaces: ' + it.contains.join(', ') +
                   ' (their boundaries are unresolved)' : '') +
                  (it.flagged_by_fireai ? ' — FireAI flagged for review' : ' — FireAI presented as confident');
  e.appendChild(t); e.dataset.uid = it.uid; byUid[it.uid] = it;
}
for (const t of C.texts) {
  const e = document.createElementNS(NS, 'text');
  e.setAttribute('x', t.xy[0]); e.setAttribute('y', -t.xy[1]); e.setAttribute('transform', 'scale(1,-1)');
  e.setAttribute('font-size', Math.max(t.h, sw * 8)); e.setAttribute('fill', '#555'); e.textContent = t.t;
  e.style.pointerEvents = 'none'; g.appendChild(e);
}
const marks = document.createElementNS(NS, 'g'); g.appendChild(marks);
let mode = 'item';
document.querySelectorAll('.tool button[data-mode]').forEach(b => b.onclick = () => {
  mode = b.dataset.mode; document.querySelectorAll('.tool button[data-mode]').forEach(x => x.classList.toggle('on', x === b));
});
const ITEM_KINDS = ['wrong_room', 'not_a_room', 'merged_spaces', 'wrong_label', 'wrong_boundary', 'misclassified', 'should_be_excluded'];
const MISSING_KINDS = ['missing_room', 'missing_wall', 'missing_door_or_opening', 'missing_other'];
const LABELS = {wrong_room: 'wrong room', not_a_room: 'not a room/space', merged_spaces: 'several rooms merged into one',
  wrong_label: 'wrong room label', wrong_boundary: 'boundary does not match the drawing', misclassified: 'wrong kind of object',
  should_be_excluded: 'should not be part of the building model', missing_room: 'a room/space is missing here',
  missing_wall: 'a wall is missing here', missing_door_or_opening: 'a door / opening is missing here', missing_other: 'something else is missing here'};
function toModel(ev) {
  const p = svg.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY;
  const q = p.matrixTransform(g.getScreenCTM().inverse()); return [q.x, q.y];
}
const pop = document.getElementById('pop');
function ask(kinds, ctx) {
  document.getElementById('popctx').textContent = ctx.text;
  const sel = document.getElementById('popkind'); sel.innerHTML = '';
  for (const k of kinds) { const o = document.createElement('option'); o.value = k; o.textContent = LABELS[k]; sel.appendChild(o); }
  document.getElementById('popnote').value = ''; pop.style.display = 'block'; pop.ctx = ctx;
}
document.getElementById('popadd').onclick = () => {
  const c = pop.ctx, f = {flag: document.getElementById('popkind').value, note: document.getElementById('popnote').value || null};
  if (c.uid) f.target_uid = c.uid; if (c.xy) f.point_local = c.xy;
  flags.push(f); pop.style.display = 'none'; render();
};
document.getElementById('popcancel').onclick = () => pop.style.display = 'none';
let drag = null;
svg.addEventListener('mousedown', ev => drag = {x: ev.clientX, y: ev.clientY, vb: vb.slice(), moved: false});
svg.addEventListener('mousemove', ev => {
  if (!drag) return; const k = vb[2] / svg.clientWidth, dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  vb = [drag.vb[0] - dx * k, drag.vb[1] - dy * k, vb[2], vb[3]]; setVB();
});
svg.addEventListener('mouseup', ev => {
  const d = drag; drag = null; if (!d || d.moved) return;
  const tgt = ev.target.closest ? ev.target.closest('.fai') : null;
  if (mode === 'item' && tgt) {
    const it = byUid[tgt.dataset.uid];
    ask(ITEM_KINDS, {uid: it.uid, text: 'FireAI ' + it.category + (it.label ? ' "' + it.label + '"' : '')});
  } else if (mode === 'missing') {
    const xy = toModel(ev); ask(MISSING_KINDS, {xy: xy, text: 'at the clicked location'});
  }
});
svg.addEventListener('wheel', ev => {
  ev.preventDefault(); const [mx, my] = toModel(ev), f = ev.deltaY > 0 ? 1.2 : 1 / 1.2;
  const cx = mx, cy = -my;
  vb = [cx - (cx - vb[0]) * f, cy - (cy - vb[1]) * f, vb[2] * f, vb[3] * f]; setVB();
}, {passive: false});
function render() {
  marks.innerHTML = ''; const ul = document.getElementById('flaglist'); ul.innerHTML = '';
  flags.forEach((f, i) => {
    let where = '';
    if (f.target_uid && byUid[f.target_uid]) {
      const it = byUid[f.target_uid]; where = 'FireAI ' + it.category + (it.label ? ' "' + it.label + '"' : '');
      const pts = it.points, cx = pts.reduce((a, p) => a + p[0], 0) / pts.length, cy = pts.reduce((a, p) => a + p[1], 0) / pts.length;
      mark(cx, cy, i + 1);
    } else if (f.point_local) { where = 'marked location'; mark(f.point_local[0], f.point_local[1], i + 1); }
    const li = document.createElement('li');
    li.textContent = (i + 1) + '. ' + LABELS[f.flag] + ' — ' + where + (f.note ? ' — "' + f.note + '"' : '') + ' ';
    const b = document.createElement('button'); b.type = 'button'; b.textContent = 'remove';
    b.onclick = () => { flags.splice(i, 1); render(); }; li.appendChild(b); ul.appendChild(li);
  });
  document.getElementById('visual_flags').value = JSON.stringify(flags);
}
function mark(x, y, n) {
  const c = document.createElementNS(NS, 'circle'); c.setAttribute('cx', x); c.setAttribute('cy', y);
  c.setAttribute('r', sw * 9); c.setAttribute('fill', '#b00020'); c.setAttribute('fill-opacity', 0.8); marks.appendChild(c);
  const t = document.createElementNS(NS, 'text'); t.setAttribute('x', x); t.setAttribute('y', -y); t.setAttribute('transform', 'scale(1,-1)');
  t.setAttribute('font-size', sw * 12); t.setAttribute('fill', '#fff'); t.setAttribute('text-anchor', 'middle');
  t.setAttribute('dominant-baseline', 'central'); t.textContent = n; t.style.pointerEvents = 'none'; marks.appendChild(t);
}
document.getElementById('fit').onclick = () => { vb = [x0 - pad, -(y1 + pad), (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad]; setVB(); };
document.getElementById('hidef').onchange = e => document.querySelectorAll('.fireai').forEach(x => x.style.display = e.target.checked ? 'none' : '');
render();
"""


def create_review_app(gt_dir: Path = G.GT_DIR, public_dir: Path = G.PUBLIC_REVIEWS,
                      private_dir: Path = G.PRIVATE_REVIEWS, outputs: Path = FV.OUTPUTS,
                      corpus_lookup=G.corpus_entry) -> FastAPI:
    app = FastAPI(title="FireAI human review (local)")

    def ctx(gid: str):
        try:
            rec = G.load_record(gid, gt_dir)
            entry = corpus_lookup(gid)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "unknown drawing") from None
        path = G.review_path(gid, entry["private"], public_dir, private_dir)
        review = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        ed, mp = FV.preferred_model(gid, outputs)
        return rec, entry, path, review, ed, mp

    def latest_png(gid: str, name: str) -> Path | None:
        hits = sorted(outputs.glob(f"*/{gid}_*/{name}"), key=lambda p: p.stat().st_mtime, reverse=True)
        return hits[0] if hits else None

    @app.get("/", response_class=HTMLResponse)
    def index():
        rows = []
        for gid in G.list_ids(gt_dir):
            rec, entry, _p, review, _ed, mp = ctx(gid)
            eff = G.effective(rec, review, entry["sha256"], FV.model_sha(mp) if mp else None)
            done = len(G.QUESTIONS) - len(eff["not_reviewed"])
            stale = " (FireAI changed since your evaluation — please re-check)" if eff.get("stale_evaluations") else ""
            rows.append(f"<tr><td><a href='/review/{gid}'>{gid}</a></td><td>{H(entry['description'])}</td>"
                        f"<td>{'private — stays on this computer' if entry['private'] else 'public'}</td>"
                        f"<td class='{eff['status']}'>{eff['status']}{stale}</td><td>{done} / {len(G.QUESTIONS)}</td></tr>")
        return _page("FireAI review", "<h1>FireAI human review</h1><p>Look at each drawing, then answer plain "
                     "questions about it and about FireAI's interpretation. Claude's records are drafts, not truth. "
                     "<a href='/summary'>Summary and metrics</a></p><table><tr><th>Drawing</th><th>Description</th>"
                     "<th>Source</th><th>Status</th><th>Questions answered</th></tr>" + "".join(rows) + "</table>")

    @app.get("/summary", response_class=HTMLResponse)
    def summary():
        md = to_markdown(build_summary(gt_dir, public_dir, private_dir, outputs, corpus_lookup))
        return _page("Review summary", f"<p><a href='/'>&larr; all drawings</a></p><pre>{H(md)}</pre>")

    @app.get("/img/{gid}/{kind}")
    def img(gid: str, kind: str):
        if not G.ID_RE.match(gid) or kind not in ("source", "overlay"):
            raise HTTPException(404)
        p = latest_png(gid, f"{kind}.png")
        if p is None:
            raise HTTPException(404, "no local rendering; run scripts/validate_corpus.py first")
        return FileResponse(p, media_type="image/png")

    @app.get("/canvas/{gid}.json")
    def canvas_json(gid: str):
        *_, mp = ctx(gid)
        if mp is None:
            raise HTTPException(404, "no local FireAI output")
        return JSONResponse(FV.canvas(FV.load_model_dict(mp)))

    def render(gid: str, errors: list[str] | None = None, posted: dict | None = None,
               posted_flags: list | None = None) -> HTMLResponse:
        rec, entry, _path, review, ed, mp = ctx(gid)
        cur_sha = FV.model_sha(mp) if mp else None
        eff = G.effective(rec, review, entry["sha256"], cur_sha)
        m = FV.load_model_dict(mp) if mp else None
        mv = FV.machine_facts(m) if m else {}
        cv = FV.canvas(m) if m else {"bounds": [0, 0, 1, 1], "lines": [], "texts": [], "items": [], "regions": []}
        em = FV.evaluated_model_ref(mp, outputs) if mp else None
        keep_evals = review and not eff.get("stale_evaluations") and eff["status"] != "INVALIDATED"
        prev = posted if posted is not None else {
            q: it for q, it in ((review or {}).get("items") or {}).items()
            if eff["status"] != "INVALIDATED" and (q in G.FACTS or keep_evals)}
        flags = posted_flags if posted_flags is not None else ((review or {}).get("visual_flags") or [] if keep_evals else [])
        fact_rows = []
        for q, spec in G.FACTS.items():
            p = prev.get(q, {})
            proposed, src = (mv.get(q), "FireAI") if mv.get(q) is not None else (G.draft_fact(rec, q), "Claude draft")
            radios = "".join(
                f"<label><input type='radio' name='{q}__decision' value='{s}' {'checked' if p.get('human_decision') == s else ''}"
                f"{' disabled' if s == 'CONFIRMED' and proposed is None else ''}> "
                f"{ {'CONFIRMED': 'Correct', 'CORRECTED': 'Wrong — correct value:', 'NOT_EVALUATED': 'Skip'}[s]}</label> "
                for s in G.STATUSES)
            bases = "".join(f"<option {'selected' if p.get('basis') == b else ''}>{b}</option>" for b in G.BASES)
            fact_rows.append(
                f"<tr><td class='stmt'>{H(spec['question'])}</td><td>{_show(proposed)}<br><small>shown value from {src}"
                f"</small><br><small>Claude draft (reference): {_show(G.draft_values(rec).get(q))}</small></td>"
                f"<td>{radios}<br>{_fact_input(q, p.get('human_corrected_value'))}<br><select name='{q}__basis'>"
                f"<option value=''>(how do you know?)</option>{bases}</select></td></tr>")
        eval_rows = []
        for q, stmt in G.EVALUATIONS.items():
            p = prev.get(q, {})
            c = p.get("human_corrected_value") or {}
            radios = "".join(
                f"<label><input type='radio' name='{q}__decision' value='{s}' {'checked' if p.get('human_decision') == s else ''}> "
                f"{ {'CONFIRMED': 'Yes', 'CORRECTED': 'No', 'NOT_EVALUATED': 'Skip / cannot judge'}[s]}</label> "
                for s in G.STATUSES)
            sev = "".join(f"<option {'selected' if c.get('severity') == s else ''}>{s}</option>" for s in G.SEVERITIES)
            eval_rows.append(
                f"<tr><td class='stmt'>{H(stmt)}</td><td>{radios}</td><td><small>If No:</small> <select name='{q}__severity'>"
                f"<option value=''>(how serious?)</option>{sev}</select><br><input type='text' name='{q}__description' "
                f"placeholder='what is wrong (a few words)' value='{H(c.get('description') or '')}'><br>"
                f"<input type='text' name='{q}__question' placeholder='open question (optional)' "
                f"value='{H(p.get('open_question') or '')}'></td></tr>")
        err = ("<div class='banner'><b>Not saved:</b><ul>" + "".join(f"<li>{H(e)}</li>" for e in errors) + "</ul></div>"
               if errors else "")
        stale = ("<div class='note'>FireAI's output for this drawing has changed since your last evaluation, so your "
                 "earlier answers about FireAI are kept on file but need a fresh look. Your drawing facts still stand.</div>"
                 if eff.get("stale_evaluations") else "")
        where = ("<b>Private drawing:</b> your review stays on this computer." if entry["private"]
                 else "Public drawing: this review file may be committed.")
        body = f"""<p><a href='/'>&larr; all drawings</a> · <a href='/summary'>summary</a></p>
<h1>{gid} <span class='{eff['status']}'>{eff['status']}</span></h1><p>{H(entry['description'])} — {where}</p>{err}{stale}
<div class='note'><b>What to do:</b> look at the source drawing. Answer the facts, then say whether each statement about
FireAI's interpretation is right. If something is wrong, you can click it on the drawing below and say what is wrong,
or click where something is missing. Skip anything you can't judge. Nothing needs CAD knowledge.</div>
<p><label><input type='checkbox' id='hidef'> Hide FireAI's interpretation while I judge the facts</label></p>
<div class='imgs'><figure><figcaption><b>SOURCE DRAWING</b> (from the file)</figcaption>
<a href='/img/{gid}/source' target='_blank'><img src='/img/{gid}/source' alt='source'></a></figure>
<figure class='fireai'><figcaption><b>FIREAI INTERPRETATION</b> (machine output — not truth)</figcaption>
<a href='/img/{gid}/overlay' target='_blank'><img src='/img/{gid}/overlay' alt='FireAI overlay'></a></figure></div>
<h2 class='fireai'>Point at problems on the drawing</h2>
<div class='fireai'><div class='tool'><button type='button' data-mode='item' class='on'>Flag a FireAI item</button>
<button type='button' data-mode='missing'>Mark something missing</button> <button type='button' id='fit'>Fit</button>
<small>Scroll to zoom, drag to pan. Grey = drawing; green = FireAI rooms (physical regions); blue = walls; red = doors; teal = windows; gold = door/window content
seen in a section/elevation/detail (not a plan door);
dashed = FireAI already flagged it as uncertain. Hover an item to see what FireAI thinks it is.</small></div>
<svg id='cv' xmlns='http://www.w3.org/2000/svg'></svg>
<div id='pop' style='display:none' class='note'><b>What is wrong with <span id='popctx'></span>?</b>
<select id='popkind'></select> <input type='text' id='popnote' placeholder='note (optional)' style='width:30%'>
<button type='button' id='popadd'>Add</button> <button type='button' id='popcancel'>Cancel</button></div>
<ul id='flaglist'></ul>{'<small>(the drawing is large: only part of the linework is shown)</small>' if cv.get('truncated') else ''}</div>
<form method='post' action='/review/{gid}'>
<input type='hidden' name='visual_flags' id='visual_flags' value='[]'>
<input type='hidden' name='evaluated_model' value='{H(json.dumps(em))}'>
<p>Your name <input type='text' name='reviewer' required style='width:20em' value='{H((review or {}).get('reviewer', ''))}'>
<small>(recorded as an unauthenticated name)</small></p>
<h2>1. Facts about the drawing</h2><table><tr><th>Question</th><th>Shown value</th><th>Your answer</th></tr>{''.join(fact_rows)}</table>
<h2 class='fireai'>2. Is FireAI's interpretation right?</h2><table class='fireai'><tr><th>Statement</th><th>Your answer</th>
<th>If not</th></tr>{''.join(eval_rows)}</table>
<p>Anything else<br><textarea name='overall_note' rows='3'>{H((review or {}).get('note') or '')}</textarea></p>
<button type='submit'>Save review</button> <small>You can save part-way and come back later.</small></form>
<script type='application/json' id='canvasdata'>{json.dumps(cv).replace('</', '<\\/')}</script>
<script type='application/json' id='flagsdata'>{json.dumps(flags).replace('</', '<\\/')}</script>
<script>{JS}</script>"""
        return _page(f"{gid} review", body)

    @app.get("/review/{gid}", response_class=HTMLResponse)
    def review_page(gid: str):
        return render(gid)

    @app.post("/review/{gid}")
    async def save(gid: str, request: Request):
        rec, entry, path, review, _ed, mp = ctx(gid)
        form = await request.form()
        errors: list[str] = []
        m = FV.load_model_dict(mp) if mp else None
        mv = FV.machine_facts(m) if m else {}
        em = FV.evaluated_model_ref(mp, outputs) if mp else None
        try:
            posted_em = json.loads(form.get("evaluated_model") or "null")
        except ValueError:
            posted_em = None
        if em and posted_em and posted_em.get("model_sha256") != em["model_sha256"]:
            errors.append("FireAI's output changed while you were reviewing — reload the page and check again")
        decisions = {}
        for q in G.QUESTIONS:
            st = form.get(f"{q}__decision")
            if not st:
                continue
            d = {"human_decision": st, "reason": None,
                 "open_question": (form.get(f"{q}__question") or "").strip() or None, "human_corrected_value": None}
            if q in G.FACTS:
                d["basis"] = form.get(f"{q}__basis") or None
                if st == "CONFIRMED":
                    if mv.get(q) is not None:
                        d["confirmed_value"], d["confirmed_value_source"] = mv[q], "fireai"
                    elif G.draft_fact(rec, q) is not None:
                        d["confirmed_value"], d["confirmed_value_source"] = G.draft_fact(rec, q), "claude_draft"
            if st == "CORRECTED":
                raw = (_raw_fact(q, form) if q in G.FACTS else
                       {"severity": form.get(f"{q}__severity"), "description": form.get(f"{q}__description")})
                try:
                    d["human_corrected_value"] = G.parse_value(q, raw)
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append(f"{q}: {exc}")
            decisions[q] = d
        try:
            flags = json.loads(form.get("visual_flags") or "[]")
        except ValueError:
            flags, errors = [], errors + ["visual flags could not be read"]
        items = {i["uid"]: i for i in (FV.canvas(m)["items"] if m else [])}
        t = (m or {}).get("transform") or {}
        clean = []
        for f in flags if isinstance(flags, list) else []:
            f = {k: f.get(k) for k in ("flag", "target_uid", "point_local", "note")}
            it = items.get(f.get("target_uid"))
            if f.get("target_uid") and it is None:
                errors.append("a visual flag refers to an item that is not in this FireAI output")
                continue
            if it:   # recorded from FireAI's own data, never from the browser
                f.update({"target_category": it["category"], "target_label": it["label"], "target_id": it["id"],
                          "target_confident": it["confident"], "target_flagged_by_fireai": it["flagged_by_fireai"]})
            if f.get("point_local") and t.get("scale"):
                x, y = f["point_local"]
                f["point_src"] = [x / t["scale"] + t["origin"][0], y / t["scale"] + t["origin"][1]]
            clean.append(f)
        rv = G.make_review(rec, entry["sha256"], form.get("reviewer") or "", decisions,
                           (form.get("overall_note") or "").strip() or None, previous=review,
                           evaluated_model=em, visual_flags=clean)
        errors += G.validate_review(rv, rec, entry["sha256"])
        if errors:
            resp = render(gid, errors, posted=decisions, posted_flags=clean)
            resp.status_code = 422
            return resp
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rv, indent=2), encoding="utf-8")
        return RedirectResponse(f"/review/{gid}", status_code=303)

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    import uvicorn
    print(f"FireAI human review: open http://127.0.0.1:{a.port}  (Ctrl+C to stop)")
    uvicorn.run(create_review_app(), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
