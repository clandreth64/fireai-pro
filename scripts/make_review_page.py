"""Build a LOCAL review page comparing SOURCE vs FIREAI INTERPRETATION vs GROUND TRUTH.

Usage: python scripts/make_review_page.py <run label>
Writes tests/real_drawings_outputs_local/<label>/review.html (git-ignored; contains drawing
content). Each drawing section shows source.png and overlay.png side by side, the
human-review ground truth record, review triggers, and an element table; clicking a row
shows the element's rules, evidence, source handles/layers/blocks and coordinates.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = ROOT / "tests" / "real_drawings_outputs_local"
GT_DIR = ROOT / "tests" / "real_drawings" / "ground_truth"


def _elements(model: dict) -> list[dict]:
    ents = {e["id"]: e for e in model.get("entities", [])}
    rows = []
    for el in model.get("elements", []):
        if el["category"] == "text_annotation":
            continue
        src = [ents[i] for i in el["source_entity_ids"] if i in ents][:6]
        rows.append({
            "id": el["id"], "uid": el.get("uid"), "category": el["category"], "subtype": el.get("subtype"),
            "label": el.get("label"), "confidence": el["confidence"], "verify": el["requires_verification"],
            "rules": el.get("rules", []), "evidence": el.get("evidence", []),
            "review": (el.get("provenance") or {}).get("review", {}).get("status"),
            "region": el.get("properties", {}).get("view_region"),
            "sources": [{"id": s["id"], "type": s["type"], "layer": s["layer"], "handle": s.get("handle"),
                         "path": "/".join(s.get("handle_path") or []), "block_path": s.get("block_path"),
                         "src": ((s.get("source") or {}).get("points") or [(s.get("source") or {}).get("insert")])[:2],
                         "local": ((s.get("normalized") or {}).get("points") or [(s.get("normalized") or {}).get("insert")])[:2]}
                        for s in src],
        })
    return rows


def main():
    label = sys.argv[1]
    run = OUT_ROOT / label
    parts = [f"<h1>FireAI review — run {html.escape(label)}</h1>",
             "<p>Local only. Left: drawing as rendered from file. Right: FireAI interpretation. "
             "Below: ground truth (AI-assisted, pending human confirmation). Click an element row for details.</p>"]
    data = {}
    for d in sorted(p for p in run.iterdir() if p.is_dir()):
        mfile, rfile = d / "building_model.json", d / "understanding_report.json"
        rid = d.name.split("_")[0] + "_" + d.name.split("_")[1]
        gt = json.loads((GT_DIR / f"{rid}.json").read_text()) if (GT_DIR / f"{rid}.json").exists() else {}
        rep = json.loads(rfile.read_text()) if rfile.exists() else {}
        from fireai.schema import read_model_dict   # restores defaults omitted from persisted entities
        rows = _elements(read_model_dict(mfile)) if mfile.exists() else []
        data[d.name] = rows
        trig = ", ".join(t["code"] for t in rep.get("review_triggers", [])) or "none"
        fail = rep.get("failure") or {}
        imgs = "".join(f'<figure><img src="{d.name}/{f}"><figcaption>{f}</figcaption></figure>'
                       for f in ("source.png", "overlay.png") if (d / f).exists())
        parts.append(f"""<section><h2>{html.escape(d.name)} — {html.escape(rep.get('processing_status', '?'))}
{html.escape(fail.get('code') or '')}</h2><div class=grid>{imgs}</div>
<p><b>Review triggers:</b> {html.escape(trig)}</p>
<details><summary>Ground truth</summary><pre>{html.escape(json.dumps(gt, indent=1))}</pre></details>
<input placeholder="filter elements…" oninput="filt('{d.name}', this.value)">
<table id="t_{d.name}"><tr><th>id</th><th>category</th><th>label</th><th>conf</th><th>verify</th><th>region</th><th>rules</th></tr>
{''.join(f"<tr onclick=\"show('{d.name}','{r['id']}')\"><td>{r['id']}</td><td>{html.escape(str(r['category']))}"
         f"{' / ' + html.escape(r['subtype']) if r['subtype'] else ''}</td><td>{html.escape(str(r['label'] or ''))}</td>"
         f"<td>{r['confidence']}</td><td>{'YES' if r['verify'] else ''}</td><td>{r['region'] or ''}</td>"
         f"<td>{html.escape(','.join(r['rules']))}</td></tr>" for r in rows[:1500])}</table>
<pre id="d_{d.name}" class=detail></pre></section>""")
    page = f"""<!DOCTYPE html><html><head><meta charset=utf-8><title>FireAI review {html.escape(label)}</title>
<style>body{{font:13px system-ui;margin:16px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
img{{width:100%;border:1px solid #ccc}}table{{border-collapse:collapse;font-size:12px}}td,th{{border-bottom:1px solid #eee;
padding:2px 6px;text-align:left}}tr:hover{{background:#eef}}.detail{{background:#f6f6f6;padding:8px;white-space:pre-wrap}}
section{{border-top:3px solid #333;margin-top:24px}}</style></head><body>{''.join(parts)}
<script>const D={json.dumps(data)};
function show(k,id){{const r=D[k].find(x=>x.id===id);document.getElementById('d_'+k).textContent=JSON.stringify(r,null,1);}}
function filt(k,q){{q=q.toLowerCase();for(const tr of document.getElementById('t_'+k).rows){{if(tr.rowIndex===0)continue;
tr.style.display=tr.textContent.toLowerCase().includes(q)?'':'none';}}}}</script></body></html>"""
    (run / "review.html").write_text(page, encoding="utf-8")
    print(f"wrote {run / 'review.html'}")


if __name__ == "__main__":
    main()
