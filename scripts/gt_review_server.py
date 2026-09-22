"""Local human ground-truth review tool for the real-drawing corpus.

    docker run --rm -p 127.0.0.1:8765:8765 -v "<repo>:/app" -w /app fireai:dev \
        python scripts/gt_review_server.py --host 0.0.0.0     # (inside the container)
    then open http://127.0.0.1:8765

For each drawing a person marks every category CONFIRMED (Claude's draft is
right), CORRECTED (enter the right value) or NOT_EVALUATED, with the basis of
their judgement. The form is pre-filled from NOTHING: Claude's draft is shown
as a draft, and FireAI's interpretation is hidden behind a toggle labelled as
machine output — it can never be submitted as ground truth.

Reviews are written to a separate file (never into the draft record):
public drawings -> tests/real_drawings/human_reviews/, private drawings ->
tests/real_drawings_local/human_reviews/ (git-ignored). Local use only: the
server binds to 127.0.0.1 by default and has no authentication.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "real_drawings"))
import gt as G  # noqa: E402

OUTPUTS = ROOT / "tests" / "real_drawings_outputs_local"
CSS = """body{font:14px system-ui,sans-serif;margin:16px;max-width:1400px}table{border-collapse:collapse}
td,th{border:1px solid #ccc;padding:4px 6px;vertical-align:top}th{background:#f3f3f3;text-align:left}
.draft{background:#fff8e1;padding:6px;border:1px dashed #d9a400}.machine{background:#eef;padding:6px}
.imgs img{max-width:48%;border:1px solid #999;margin-right:1%}.PENDING_HUMAN_VERIFICATION{color:#b26a00}
.HUMAN_VERIFIED{color:#1b7f1b}.INVALIDATED{color:#b00020}.PARTIALLY_HUMAN_REVIEWED{color:#0b5cad}
pre{white-space:pre-wrap;margin:0}"""


def create_review_app(gt_dir: Path = G.GT_DIR, public_dir: Path = G.PUBLIC_REVIEWS,
                      private_dir: Path = G.PRIVATE_REVIEWS, outputs: Path = OUTPUTS,
                      corpus_lookup=G.corpus_entry) -> FastAPI:
    app = FastAPI(title="FireAI ground-truth review (local)")

    def ctx(gid: str):
        try:
            rec = G.load_record(gid, gt_dir)
            entry = corpus_lookup(gid)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "unknown drawing") from None
        private = entry["private"]
        path = G.review_path(gid, private, public_dir, private_dir)
        review = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return rec, entry, private, path, review

    def latest(gid: str, name: str) -> Path | None:
        hits = sorted(outputs.glob(f"*/{gid}_*/{name}"), key=lambda p: p.stat().st_mtime, reverse=True)
        return hits[0] if hits else None

    @app.get("/", response_class=HTMLResponse)
    def index():
        rows = []
        for gid in G.list_ids(gt_dir):
            rec, entry, private, _p, review = ctx(gid)
            eff = G.effective(rec, review, entry["sha256"])
            rows.append(f"<tr><td><a href='/review/{gid}'>{gid}</a></td><td>{html.escape(entry.get('description', ''))}"
                        f"</td><td>{'private (review stays local)' if private else 'public'}</td>"
                        f"<td class='{eff['status']}'>{eff['status']}</td></tr>")
        return (f"<html><head><title>GT review</title><style>{CSS}</style></head><body><h1>Ground-truth review</h1>"
                "<p>Claude's records are <b>drafts</b>. Only your review creates ground truth.</p>"
                "<table><tr><th>Drawing</th><th>Description</th><th>Source</th><th>Status</th></tr>"
                + "".join(rows) + "</table></body></html>")

    @app.get("/img/{gid}/{kind}")
    def img(gid: str, kind: str):
        if not G.ID_RE.match(gid) or kind not in ("source", "overlay"):
            raise HTTPException(404)
        p = latest(gid, f"{kind}.png")
        if p is None:
            raise HTTPException(404, "no local rendering; run scripts/validate_corpus.py first")
        return FileResponse(p, media_type="image/png")

    @app.get("/review/{gid}", response_class=HTMLResponse)
    def review_page(gid: str):
        rec, entry, private, path, review = ctx(gid)
        eff = G.effective(rec, review, entry["sha256"])
        drafts = G.draft_values(rec)
        prev = (review or {}).get("items", {}) if eff["status"] != "INVALIDATED" else {}
        rows = []
        for cat in G.CATEGORIES:
            d = drafts.get(cat)
            dtxt = html.escape(json.dumps(d, indent=1)) if d else "<i>(no draft value)</i>"
            p = prev.get(cat, {})
            radios = "".join(
                f"<label><input type='radio' name='{cat}__status' value='{s}' {'checked' if p.get('status') == s else ''}"
                f"{' disabled' if s == 'CONFIRMED' and not d else ''}> {s}</label><br>" for s in G.STATUSES)
            bases = "".join(f"<option {'selected' if p.get('basis') == b else ''}>{b}</option>" for b in G.BASES)
            rows.append(f"<tr><th>{cat}</th><td class='draft'><pre>{dtxt}</pre></td><td>{radios}</td>"
                        f"<td><select name='{cat}__basis'><option value=''>(basis)</option>{bases}</select><br>"
                        f"<textarea name='{cat}__value' rows=2 cols=36 placeholder='correct value (if CORRECTED)'>"
                        f"{html.escape(str(p.get('value') or ''))}</textarea><br>"
                        f"<input name='{cat}__note' size=36 placeholder='note' value='{html.escape(p.get('note') or '')}'>"
                        f"</td></tr>")
        warn = ("<p><b>Private drawing:</b> this review is saved locally only (git-ignored).</p>" if private else
                "<p>Public drawing: this review file may be committed.</p>")
        return f"""<html><head><title>{gid} review</title><style>{CSS}</style></head><body>
<p><a href='/'>&larr; all drawings</a></p><h1>{gid} <span class='{eff['status']}'>{eff['status']}</span></h1>
{warn}<p>{html.escape(eff.get('reason', ''))}</p>
<div class='imgs'><img src='/img/{gid}/source' alt='source rendering (from the drawing file)'>
<details><summary>Show FireAI interpretation (machine output — NOT ground truth; do not copy it)</summary>
<div class='machine'><img src='/img/{gid}/overlay' alt='FireAI overlay'></div></details></div>
<form method='post' action='/review/{gid}'>
<p>Reviewer <input name='reviewer' required value='{html.escape((review or {}).get('reviewer', ''))}'></p>
<table><tr><th>Category</th><th>Claude draft (not truth)</th><th>Your decision</th><th>Basis / value / note</th></tr>
{''.join(rows)}</table><p>Overall note<br><textarea name='overall_note' rows=3 cols=80></textarea></p>
<button type='submit'>Save human review</button></form></body></html>"""

    @app.post("/review/{gid}")
    async def save(gid: str, request: Request, reviewer: str = Form(...)):
        rec, entry, _private, path, _review = ctx(gid)
        form = await request.form()
        items = {}
        for cat in G.CATEGORIES:
            st = form.get(f"{cat}__status")
            if not st:
                continue
            it = {"status": st, "basis": form.get(f"{cat}__basis") or None,
                  "note": (form.get(f"{cat}__note") or "").strip() or None}
            val = (form.get(f"{cat}__value") or "").strip()
            if st == "CORRECTED":
                try:
                    it["value"] = json.loads(val)
                except (ValueError, TypeError):
                    it["value"] = val or None
            items[cat] = it
        rv = G.make_review(rec, entry["sha256"], reviewer, items, (form.get("overall_note") or "").strip() or None)
        errs = G.validate_review(rv, rec, entry["sha256"])
        if errs:
            return HTMLResponse("<h1>Not saved</h1><ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in errs)
                                + f"</ul><p><a href='/review/{gid}'>back</a></p>", status_code=422)
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
    uvicorn.run(create_review_app(), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
