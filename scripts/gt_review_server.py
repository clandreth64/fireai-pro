"""Local human ground-truth review tool for the real-drawing corpus.

Start it with ONE command from the repository folder (see docs/HUMAN_VALIDATION_GUIDE.md):
    Windows:      powershell -ExecutionPolicy Bypass -File scripts\\review.ps1
    macOS/Linux:  sh scripts/review.sh
then open http://127.0.0.1:8765

For each drawing and category the reviewer sees, side by side:
    SOURCE DRAWING (rendered from the file)  |  FIREAI INTERPRETATION (overlay)
    CLAUDE DRAFT (not truth) | FIREAI VALUE (machine, not truth) | YOUR DECISION
and records CONFIRMED / CORRECTED (typed value) / NOT_EVALUATED with the basis, an optional
reason and an optional open question. Inputs are never pre-filled from FireAI or from the draft.

Reviews are written to a separate file (the draft record is never modified):
public drawings -> tests/real_drawings/human_reviews/, private drawings ->
tests/real_drawings_local/human_reviews/ (git-ignored). Local use only: binds to 127.0.0.1 by
default; no authentication — the reviewer name is NOT an authenticated identity.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "real_drawings"))
sys.path.insert(0, str(ROOT / "scripts"))
import fireai_values as FV  # noqa: E402
import gt as G  # noqa: E402
from gt_review_summary import build_summary, to_markdown  # noqa: E402

CSS = """body{font:14px system-ui,sans-serif;margin:16px;max-width:1500px}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ccc;padding:5px 7px;vertical-align:top}th{background:#f3f3f3;text-align:left}
.draft{background:#fff8e1}.machine{background:#eef3ff}.decision{background:#f6fff4;min-width:330px}
.imgs{display:flex;gap:1%}.imgs figure{margin:0;width:49.5%}.imgs img{width:100%;border:1px solid #999}
.PENDING_HUMAN_VERIFICATION{color:#b26a00}.HUMAN_VERIFIED{color:#1b7f1b}.INVALIDATED{color:#b00020}
.PARTIALLY_HUMAN_REVIEWED{color:#0b5cad}pre{white-space:pre-wrap;margin:0;font-size:12px}
small{color:#555}.err{color:#b00020}.hidden{display:none}textarea,input[type=text]{width:95%}
.banner{background:#fdecea;padding:8px;border:1px solid #f5c2c0}"""
H = html.escape


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<html><head><meta charset='utf-8'><title>{H(title)}</title><style>{CSS}</style></head>"
                        f"<body>{body}</body></html>")


def _pretty(v) -> str:
    return "<i>(none)</i>" if v in (None, {}, []) else f"<pre>{H(json.dumps(v, indent=1, default=str))}</pre>"


def _value_input(cat: str, prev) -> str:
    """Typed input for a CORRECTED value, pre-filled only from this reviewer's previous decision."""
    spec = G.CATEGORY_SPECS[cat]
    k, n = spec["kind"], f"{cat}__value"
    if k == "choice":
        opts = "".join(f"<option {'selected' if prev == c else ''}>{c}</option>" for c in spec["choices"])
        return f"<select name='{n}'><option value=''>(choose)</option>{opts}</select>"
    if k == "int":
        return f"<input type='number' min='0' name='{n}' value='{H(str(prev)) if prev is not None else ''}'>"
    if k == "counts":
        prev = prev or {}
        return "<br>".join(f"<label>{t} <input type='number' min='0' style='width:5em' name='{n}__{t}' "
                           f"value='{prev.get(t, '')}'></label>" for t in spec["keys"])
    if k == "extents":
        prev = prev or {}
        return (f"width ft <input type='number' step='any' style='width:7em' name='{n}__width_ft' value='{prev.get('width_ft', '')}'>"
                f" height ft <input type='number' step='any' style='width:7em' name='{n}__height_ft' value='{prev.get('height_ft', '')}'>")
    if k == "text_count":
        prev = prev or {}
        c = prev.get("count")
        return (f"count <input type='number' min='0' style='width:6em' name='{n}__count' value='{'' if c is None else c}'>"
                f"<br><input type='text' name='{n}__description' placeholder='description' "
                f"value='{H(prev.get('description') or '')}'>")
    text = ""
    if prev is not None:
        text = {"list": lambda v: "\n".join(v), "name_numbers": lambda v: "\n".join(f"{r['name']} = {r['value']}" for r in v),
                "key_values": lambda v: "\n".join(f"{a}: {b}" for a, b in v.items()),
                "polygons": lambda v: json.dumps(v, indent=1)}.get(k, str)(prev)
    return f"<textarea name='{n}' rows='3'>{H(text)}</textarea>"


def _raw_value(cat: str, form) -> object:
    k, n = G.CATEGORY_SPECS[cat]["kind"], f"{cat}__value"
    if k == "counts":
        return {t: form.get(f"{n}__{t}") or "" for t in G.CATEGORY_SPECS[cat]["keys"]}
    if k == "extents":
        return {"width_ft": form.get(f"{n}__width_ft"), "height_ft": form.get(f"{n}__height_ft")}
    if k == "text_count":
        return {"count": form.get(f"{n}__count"), "description": form.get(f"{n}__description")}
    return form.get(n)


def create_review_app(gt_dir: Path = G.GT_DIR, public_dir: Path = G.PUBLIC_REVIEWS,
                      private_dir: Path = G.PRIVATE_REVIEWS, outputs: Path = FV.OUTPUTS,
                      corpus_lookup=G.corpus_entry) -> FastAPI:
    app = FastAPI(title="FireAI ground-truth review (local)")

    def ctx(gid: str):
        try:
            rec = G.load_record(gid, gt_dir)
            entry = corpus_lookup(gid)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "unknown drawing") from None
        path = G.review_path(gid, entry["private"], public_dir, private_dir)
        review = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return rec, entry, path, review

    def machine(gid: str) -> tuple[str | None, dict]:
        models = FV.latest_models(gid, outputs)
        for ed in ("dwg", "dxf"):
            if ed in models:
                return ed, FV.machine_values(FV.load_model_dict(models[ed]))
        return None, {}

    def latest_png(gid: str, name: str) -> Path | None:
        hits = sorted(outputs.glob(f"*/{gid}_*/{name}"), key=lambda p: p.stat().st_mtime, reverse=True)
        return hits[0] if hits else None

    @app.get("/", response_class=HTMLResponse)
    def index():
        rows = []
        for gid in G.list_ids(gt_dir):
            rec, entry, _p, review = ctx(gid)
            eff = G.effective(rec, review, entry["sha256"])
            done = len(G.CATEGORIES) - len(eff["not_reviewed"])
            rows.append(f"<tr><td><a href='/review/{gid}'>{gid}</a></td><td>{H(entry['description'])}</td>"
                        f"<td>{'private — review stays on this computer' if entry['private'] else 'public'}</td>"
                        f"<td class='{eff['status']}'>{eff['status']}</td><td>{done} / {len(G.CATEGORIES)}</td></tr>")
        return _page("Ground-truth review", "<h1>FireAI ground-truth review</h1>"
                     "<p>Claude's records are <b>drafts</b>, not truth. Only your decisions create ground truth. "
                     "<a href='/summary'>Review summary</a></p><table><tr><th>Drawing</th><th>Description</th>"
                     "<th>Source</th><th>Status</th><th>Categories reviewed</th></tr>" + "".join(rows) + "</table>")

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

    def render(gid: str, errors: list[str] | None = None, posted: dict | None = None) -> HTMLResponse:
        rec, entry, _path, review = ctx(gid)
        eff = G.effective(rec, review, entry["sha256"])
        drafts = G.draft_values(rec)
        prev = posted if posted is not None else (
            (review or {}).get("items", {}) if eff["status"] != "INVALIDATED" else {})
        ed, mv = machine(gid)
        rows = []
        for cat in G.CATEGORIES:
            spec, p, d = G.CATEGORY_SPECS[cat], prev.get(cat, {}), drafts.get(cat)
            radios = "".join(
                f"<label><input type='radio' name='{cat}__decision' value='{s}' "
                f"{'checked' if p.get('human_decision') == s else ''}{' disabled' if s == 'CONFIRMED' and not d else ''}>"
                f" {s}</label> " for s in G.STATUSES)
            bases = "".join(f"<option {'selected' if p.get('basis') == b else ''}>{b}</option>" for b in G.BASES)
            rows.append(
                f"<tr><th>{cat}<br><small>{H(spec['help'])}</small></th><td class='draft'>{_pretty(d)}</td>"
                f"<td class='machine fireai'>{_pretty(mv.get(cat)) if ed else '<i>(no local FireAI run)</i>'}</td>"
                f"<td class='decision'>{radios}<br><select name='{cat}__basis'><option value=''>(basis of your decision)"
                f"</option>{bases}</select><br><small>Correct value (only if CORRECTED):</small><br>"
                f"{_value_input(cat, p.get('human_corrected_value'))}<br>"
                f"<input type='text' name='{cat}__reason' placeholder='reason (optional)' value='{H(p.get('reason') or '')}'><br>"
                f"<input type='text' name='{cat}__question' placeholder='open question (optional)' "
                f"value='{H(p.get('open_question') or '')}'></td></tr>")
        err = ("<div class='banner'><b>Not saved:</b><ul>" + "".join(f"<li>{H(e)}</li>" for e in errors) + "</ul></div>"
               if errors else "")
        where = ("<b>Private drawing:</b> saved on this computer only (git-ignored)." if entry["private"]
                 else "Public drawing: this review file may be committed.")
        body = f"""<p><a href='/'>&larr; all drawings</a> · <a href='/summary'>summary</a></p>
<h1>{gid} <span class='{eff['status']}'>{eff['status']}</span></h1><p>{H(entry['description'])}</p>
<p>{where} {H(eff.get('reason', ''))}</p>{err}
<label><input type='checkbox' id='hidef' onchange="document.querySelectorAll('.fireai').forEach(e=>e.classList.toggle('hidden',this.checked))">
Hide FireAI's interpretation (to judge the drawing without it)</label>
<div class='imgs'><figure><figcaption><b>SOURCE DRAWING</b> (rendered from the file)</figcaption>
<a href='/img/{gid}/source' target='_blank'><img src='/img/{gid}/source' alt='source rendering'></a></figure>
<figure class='fireai'><figcaption><b>FIREAI INTERPRETATION</b> (machine output — not truth)</figcaption>
<a href='/img/{gid}/overlay' target='_blank'><img src='/img/{gid}/overlay' alt='FireAI overlay'></a></figure></div>
<form method='post' action='/review/{gid}'>
<p>Reviewer name <input type='text' name='reviewer' required style='width:20em'
 value='{H((review or {}).get('reviewer', ''))}'> <small>(recorded as an unauthenticated name)</small></p>
<table><tr><th>Category</th><th class='draft'>CLAUDE DRAFT (not truth)</th>
<th class='machine fireai'>FIREAI VALUE ({ed or '—'}; machine, not truth)</th><th class='decision'>YOUR DECISION</th></tr>
{''.join(rows)}</table><p>Overall note<br><textarea name='overall_note' rows='3'>{H((review or {}).get('note') or '')}</textarea></p>
<button type='submit'>Save review</button> <small>You can save partial reviews and come back later.</small></form>"""
        return _page(f"{gid} review", body)

    @app.get("/review/{gid}", response_class=HTMLResponse)
    def review_page(gid: str):
        return render(gid)

    @app.post("/review/{gid}")
    async def save(gid: str, request: Request):
        rec, entry, path, review = ctx(gid)
        form = await request.form()
        decisions, errors = {}, []
        for cat in G.CATEGORIES:
            st = form.get(f"{cat}__decision")
            if not st:
                continue
            d = {"human_decision": st, "basis": form.get(f"{cat}__basis") or None,
                 "reason": (form.get(f"{cat}__reason") or "").strip() or None,
                 "open_question": (form.get(f"{cat}__question") or "").strip() or None,
                 "human_corrected_value": None}
            if st == "CORRECTED":
                try:
                    d["human_corrected_value"] = G.parse_value(cat, _raw_value(cat, form))
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append(f"{cat}: {exc}")
            decisions[cat] = d
        rv = G.make_review(rec, entry["sha256"], form.get("reviewer") or "", decisions,
                           (form.get("overall_note") or "").strip() or None, previous=review)
        errors += G.validate_review(rv, rec, entry["sha256"])
        if errors:
            resp = render(gid, errors, posted=decisions)
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
    print(f"FireAI ground-truth review: open http://127.0.0.1:{a.port}  (Ctrl+C to stop)")
    uvicorn.run(create_review_app(), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
