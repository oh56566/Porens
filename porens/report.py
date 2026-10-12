"""Write recovery manifests (JSON + CSV) describing what was carved.

A forensic recovery is only useful if you can say *where each file came
from* and *how confident the reconstruction is*. The manifest records the
source offset, size, confidence, truncation flag, hash, and any EXIF
timestamp/camera/GPS for every recovered file.
"""

from __future__ import annotations

import csv
import html
import json
import os
from typing import Iterable

from .recover import Recovered

_IMAGE_KINDS = {"jpeg", "png", "gif", "webp", "tiff"}


def _row(rec: Recovered) -> dict:
    return {
        "index": rec.index,
        "file": os.path.basename(rec.out_path),
        "source": rec.source,
        "original_name": rec.original_name,
        "fs_path": rec.fs_path,
        "kind": rec.kind,
        "ext": rec.ext,
        "source_offset": rec.source_offset,
        "size": rec.size,
        "confidence": round(rec.confidence, 3),
        "truncated": rec.truncated,
        "verified": rec.verified,
        "sha256": rec.sha256,
        "exif_timestamp": rec.exif.best_timestamp,
        "camera_make": rec.exif.make,
        "camera_model": rec.exif.model,
        "gps_lat": rec.exif.gps_lat,
        "gps_lon": rec.exif.gps_lon,
        "notes": "; ".join(rec.notes),
    }


def write_manifest(records: Iterable[Recovered], out_dir: str) -> dict:
    """Write ``manifest.json`` and ``manifest.csv`` into ``out_dir``.

    Returns summary statistics (also useful for the CLI's closing line).
    """

    rows = [_row(r) for r in records]
    json_path = os.path.join(out_dir, "manifest.json")
    csv_path = os.path.join(out_dir, "manifest.csv")

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)

    if rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    by_kind: dict[str, int] = {}
    clean = partial = 0
    total_bytes = 0
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        total_bytes += r["size"]
        if r["truncated"]:
            partial += 1
        else:
            clean += 1
    return {
        "count": len(rows),
        "by_kind": by_kind,
        "clean": clean,
        "truncated": partial,
        "total_bytes": total_bytes,
        "manifest_json": json_path,
        "manifest_csv": csv_path if rows else None,
    }


def _conf_class(c: float) -> str:
    return "hi" if c >= 0.85 else ("mid" if c >= 0.6 else "lo")


_MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif",
         "webp": "image/webp", "tif": "image/tiff", "mp4": "video/mp4",
         "mov": "video/quicktime", "3gp": "video/3gpp"}
# Only inline files up to this size when embedding, to keep the page sane.
EMBED_MAX = 8 * 1024 * 1024


def _src_for(rec: Recovered, fname: str, embed: bool) -> str:
    if not embed or rec.size > EMBED_MAX:
        return fname
    try:
        import base64

        with open(rec.out_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        mime = _MIME.get(rec.ext, "application/octet-stream")
        return f"data:{mime};base64,{b64}"
    except OSError:
        return fname


def _card(rec: Recovered, embed: bool = False) -> str:
    fname = html.escape(os.path.basename(rec.out_path))
    is_image = rec.kind in _IMAGE_KINDS
    is_video = rec.kind == "isobmff" and rec.ext in ("mp4", "mov", "3gp")
    src = html.escape(_src_for(rec, os.path.basename(rec.out_path), embed))
    if is_image:
        media = f'<img loading="lazy" src="{src}" alt="{fname}">'
    elif is_video:
        media = f'<video controls preload="metadata" src="{src}"></video>'
    else:
        media = f'<div class="noprev">{html.escape(rec.ext.upper())}</div>'

    title = html.escape(rec.original_name or fname)
    meta_rows = []
    if rec.fs_path:
        meta_rows.append(("path", html.escape(rec.fs_path)))
    if rec.exif.best_timestamp:
        meta_rows.append(("taken", html.escape(rec.exif.best_timestamp)))
    if rec.exif.model:
        cam = f"{rec.exif.make or ''} {rec.exif.model}".strip()
        meta_rows.append(("camera", html.escape(cam)))
    meta_rows.append(("size", f"{rec.size:,} B"))
    meta_rows.append(("source", html.escape(rec.source)))
    gps = ""
    if rec.exif.gps_lat is not None and rec.exif.gps_lon is not None:
        lat, lon = rec.exif.gps_lat, rec.exif.gps_lon
        url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}"
        gps = f'<a class="gps" href="{html.escape(url)}" target="_blank" rel="noopener">📍 {lat:.5f}, {lon:.5f}</a>'

    meta_html = "".join(
        f'<div class="row"><span>{k}</span><b>{v}</b></div>' for k, v in meta_rows
    )
    cc = _conf_class(rec.confidence)
    trunc = '<span class="badge warn">partial</span>' if rec.truncated else ""
    verified = ""
    if rec.verified is True:
        verified = '<span class="badge hi">decoded ✓</span>'
    elif rec.verified is False:
        verified = '<span class="badge warn">decode ✗</span>'
    data_attrs = (
        f'data-kind="{rec.kind}" data-source="{rec.source}" '
        f'data-conf="{rec.confidence}" data-size="{rec.size}" '
        f'data-ts="{html.escape(rec.exif.best_timestamp or "")}"'
    )
    return f"""<figure class="card" {data_attrs}>
  <div class="media">{media}</div>
  <figcaption>
    <div class="name" title="{title}">{title}</div>
    <div class="badges">
      <span class="badge {cc}">conf {rec.confidence:.2f}</span>
      <span class="badge src">{html.escape(rec.source)}</span>{verified}{trunc}
    </div>
    <div class="meta">{meta_html}</div>
    {gps}
  </figcaption>
</figure>"""


def write_html_gallery(records: Iterable[Recovered], out_dir: str,
                       title: str = "Porens recovery", embed: bool = False) -> str:
    """Write a self-contained ``gallery.html`` next to the recovered files.

    By default cards reference the recovered files by relative name, so open
    the page from inside the output directory. With ``embed=True`` the image
    and video bytes are inlined as data URIs, producing a single portable
    file that renders anywhere (at the cost of size).
    """

    recs = list(records)
    cards = "\n".join(_card(r, embed=embed) for r in recs)
    total_bytes = sum(r.size for r in recs)
    kinds = sorted({r.kind for r in recs})
    sources = sorted({r.source for r in recs})
    filt_kinds = "".join(
        f'<button class="chip" data-f="kind" data-v="{k}">{k}</button>' for k in kinds
    )
    filt_src = "".join(
        f'<button class="chip" data-f="source" data-v="{s}">{s}</button>' for s in sources
    )
    path = os.path.join(out_dir, "gallery.html")
    doc = _HTML_TEMPLATE.format(
        title=html.escape(title),
        count=len(recs),
        total_mb=total_bytes / (1024 * 1024),
        filt_kinds=filt_kinds,
        filt_src=filt_src,
        cards=cards,
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg:#f6f7f9; --fg:#1b1d21; --muted:#6b7280; --card:#ffffff;
    --line:#e5e7eb; --hi:#15803d; --hiBg:#dcfce7; --mid:#b45309; --midBg:#fef3c7;
    --lo:#b91c1c; --loBg:#fee2e2; --src:#334155; --srcBg:#e2e8f0; --accent:#2563eb;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg:#0f1115; --fg:#e6e8eb; --muted:#9aa3af; --card:#171a20;
      --line:#262b33; --hiBg:#0f2e1c; --midBg:#352711; --loBg:#3a1518; --srcBg:#1e2632;
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  header {{ padding:20px 16px; border-bottom:1px solid var(--line);
    position:sticky; top:0; background:var(--bg); z-index:5; }}
  h1 {{ margin:0 0 4px; font-size:18px; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  .controls {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; align-items:center; }}
  .chip, .sortbtn {{ border:1px solid var(--line); background:var(--card); color:var(--fg);
    border-radius:999px; padding:5px 11px; font-size:12px; cursor:pointer; }}
  .chip.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
  .sep {{ width:1px; height:20px; background:var(--line); margin:0 4px; }}
  main {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
    gap:14px; padding:16px; }}
  .card {{ margin:0; background:var(--card); border:1px solid var(--line);
    border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }}
  .media {{ aspect-ratio:4/3; background:#00000010; display:flex;
    align-items:center; justify-content:center; overflow:hidden; }}
  .media img, .media video {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .noprev {{ color:var(--muted); font-weight:600; letter-spacing:.05em; }}
  figcaption {{ padding:10px 12px; display:flex; flex-direction:column; gap:7px; }}
  .name {{ font-weight:600; font-size:13px; white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; }}
  .badges {{ display:flex; gap:6px; flex-wrap:wrap; }}
  .badge {{ font-size:11px; padding:2px 8px; border-radius:999px; font-weight:600; }}
  .badge.hi {{ color:var(--hi); background:var(--hiBg); }}
  .badge.mid {{ color:var(--mid); background:var(--midBg); }}
  .badge.lo {{ color:var(--lo); background:var(--loBg); }}
  .badge.src {{ color:var(--src); background:var(--srcBg); }}
  .badge.warn {{ color:var(--lo); background:var(--loBg); }}
  .meta {{ font-size:12px; color:var(--muted); display:flex; flex-direction:column; gap:2px; }}
  .meta .row {{ display:flex; justify-content:space-between; gap:8px; }}
  .meta .row b {{ color:var(--fg); font-weight:500; text-align:right;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .gps {{ font-size:12px; color:var(--accent); text-decoration:none; }}
  .empty {{ padding:40px 16px; text-align:center; color:var(--muted); }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="sub">{count} files · {total_mb:.1f} MiB · open this page from inside the output folder</div>
  <div class="controls">
    <button class="chip active" data-f="all" data-v="">all</button>
    {filt_kinds}<span class="sep"></span>{filt_src}
    <span class="sep"></span>
    <button class="sortbtn" data-sort="ts">sort: date</button>
    <button class="sortbtn" data-sort="size">size</button>
    <button class="sortbtn" data-sort="conf">confidence</button>
  </div>
</header>
<main id="grid">
{cards}
</main>
<div class="empty" id="empty" style="display:none">No files match this filter.</div>
<script>
  const grid = document.getElementById('grid');
  const cards = [...grid.children];
  const empty = document.getElementById('empty');
  let activeFilter = {{f:'all', v:''}};
  function apply() {{
    let shown = 0;
    for (const c of cards) {{
      const ok = activeFilter.f === 'all' ||
        c.getAttribute('data-' + activeFilter.f) === activeFilter.v;
      c.style.display = ok ? '' : 'none';
      if (ok) shown++;
    }}
    empty.style.display = shown ? 'none' : 'block';
  }}
  document.querySelectorAll('.chip').forEach(ch => ch.addEventListener('click', () => {{
    document.querySelectorAll('.chip').forEach(x => x.classList.remove('active'));
    ch.classList.add('active');
    activeFilter = {{f: ch.dataset.f, v: ch.dataset.v}};
    apply();
  }}));
  document.querySelectorAll('.sortbtn').forEach(b => b.addEventListener('click', () => {{
    const key = b.dataset.sort;
    const val = c => key === 'ts' ? (c.dataset.ts || '') : parseFloat(c.dataset[key] || 0);
    const sorted = [...cards].sort((a, z) => {{
      const av = val(a), zv = val(z);
      return (key === 'ts') ? zv.localeCompare(av) : (zv - av);
    }});
    sorted.forEach(c => grid.appendChild(c));
  }}));
</script>
</body>
</html>
"""

