"""Generate reports in Markdown, JSON, CSV, and interactive HTML formats.

Output files:
- report_complete.csv   — hotels with full static data
- report_problems.csv   — hotels with issues (boolean columns per field)
- report_summary.csv    — totals
- report.json           — full structured data
- report.md             — human-readable Markdown
- report.html           — interactive HTML dashboard with filters & charts
"""

from __future__ import annotations

import csv
import html as html_lib
import json
import re
from pathlib import Path
from typing import List

from src.config import REPORTS_DIR
from src.utils import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Hotel URL builder (duplicated from config to keep report_writer standalone)
# ---------------------------------------------------------------------------

_BASE_URL = "https://hgwcore.azurewebsites.net/AbroadStaticData/Hotel"


def _hotel_link(hotel_id: str) -> str:
    return f"{_BASE_URL}?hotelId={hotel_id}&hotelIdType=Sabre&vendor=SabreLDS"


# ---------------------------------------------------------------------------
# Data structures expected from runner
# ---------------------------------------------------------------------------

class CompleteHotel:
    def __init__(self, hotel_id: str, name: str, stars: str, images_count: int):
        self.hotel_id = hotel_id
        self.name = name
        self.stars = stars
        self.images_count = images_count

    def to_dict(self) -> dict:
        return {
            "hotel_id": self.hotel_id,
            "name": self.name,
            "stars": self.stars,
            "images_count": self.images_count,
        }


class IncompleteHotel:
    def __init__(
        self,
        hotel_id: str,
        status: str,
        missing_fields: List[str],
        name: str = "",
        codes: str = "",
        destination_en: str = "",
        token_url: str = "",
        explanation: str = "",
    ):
        self.hotel_id = hotel_id
        self.status = status  # NOT_FOUND | INCOMPLETE
        self.missing_fields = missing_fields
        self.name = name
        self.codes = codes
        self.destination_en = destination_en
        self.token_url = token_url
        self.explanation = explanation

    def to_dict(self) -> dict:
        return {
            "hotel_id": self.hotel_id,
            "status": self.status,
            "missing_fields": self.missing_fields,
            "name": self.name,
            "codes": self.codes,
            "destination_en": self.destination_en,
            "token_url": self.token_url,
            "explanation": self.explanation,
        }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class Summary:
    def __init__(self, total: int, complete: int, incomplete: int, not_found: int):
        self.total = total
        self.complete = complete
        self.incomplete = incomplete
        self.not_found = not_found

    def to_dict(self) -> dict:
        return {
            "total_checked": self.total,
            "complete": self.complete,
            "incomplete": self.incomplete,
            "not_found": self.not_found,
        }


# ---------------------------------------------------------------------------
# Writer (entry point)
# ---------------------------------------------------------------------------

def write_reports(
    complete: List[CompleteHotel],
    incomplete: List[IncompleteHotel],
    summary: Summary,
) -> None:
    """Write all report files."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(complete, incomplete, summary)
    _write_md(complete, incomplete, summary)
    _write_csv_complete(complete)
    _write_csv_problems(incomplete)
    _write_csv_summary(summary)
    _write_html(complete, incomplete, summary)
    log.info("Reports saved to %s", REPORTS_DIR)


# ---------------------------------------------------------------------------
# Missing-field helpers for boolean columns
# ---------------------------------------------------------------------------

def _has_missing(fields: List[str], keyword: str) -> bool:
    for f in fields:
        if keyword.lower() in f.lower():
            return True
    return False


def _missing_name(fields: List[str]) -> bool:
    return "Name" in fields


def _missing_stars(fields: List[str]) -> bool:
    return _has_missing(fields, "Stars")


def _missing_images(fields: List[str]) -> bool:
    for f in fields:
        if f.startswith("Images"):
            return True
    return False


def _missing_image_alt(fields: List[str]) -> bool:
    return any(re.match(r"Image\[\d+\]\.Alt", f) for f in fields)


def _missing_image_name(fields: List[str]) -> bool:
    return any(re.match(r"Image\[\d+\]\.Name", f) for f in fields)


def _missing_image_url(fields: List[str]) -> bool:
    return any(re.match(r"Image\[\d+\]\.Url", f) for f in fields)


def _yn(val: bool) -> str:
    return "YES" if val else "NO"


# ---------------------------------------------------------------------------
# CSV — Complete hotels
# ---------------------------------------------------------------------------

def _write_csv_complete(complete: List[CompleteHotel]) -> None:
    path = REPORTS_DIR / "report_complete.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["HOTELID", "Name", "Stars", "Images_Count", "Link"])
        for h in complete:
            writer.writerow([h.hotel_id, h.name, h.stars, h.images_count, _hotel_link(h.hotel_id)])


# ---------------------------------------------------------------------------
# CSV — Problem hotels
# ---------------------------------------------------------------------------

def _write_csv_problems(incomplete: List[IncompleteHotel]) -> None:
    path = REPORTS_DIR / "report_problems.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "HOTELID", "Name", "Status",
            "Missing_Name", "Missing_Stars", "Missing_Images",
            "Missing_Image_Alt", "Missing_Image_Name", "Missing_Image_Url",
            "Missing_Fields_Raw",
            "Destination_Code", "Destination_EN",
            "Token_URL", "Explanation", "Link",
        ])
        for h in incomplete:
            mf = h.missing_fields
            writer.writerow([
                h.hotel_id, h.name, h.status,
                _yn(_missing_name(mf)), _yn(_missing_stars(mf)), _yn(_missing_images(mf)),
                _yn(_missing_image_alt(mf)), _yn(_missing_image_name(mf)), _yn(_missing_image_url(mf)),
                "; ".join(mf),
                h.codes, h.destination_en, h.token_url, h.explanation,
                _hotel_link(h.hotel_id),
            ])


# ---------------------------------------------------------------------------
# CSV — Summary
# ---------------------------------------------------------------------------

def _write_csv_summary(summary: Summary) -> None:
    path = REPORTS_DIR / "report_summary.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Metric", "Count"])
        writer.writerow(["Total checked", summary.total])
        writer.writerow(["Complete", summary.complete])
        writer.writerow(["Incomplete", summary.incomplete])
        writer.writerow(["Not found", summary.not_found])


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _write_md(
    complete: List[CompleteHotel],
    incomplete: List[IncompleteHotel],
    summary: Summary,
) -> None:
    lines: List[str] = []
    lines.append("# SabreLDS Hotel Static Data Report\n")

    lines.append("## 1. Hotels with complete static data\n")
    if complete:
        lines.append("| HOTELID | Name | Stars | Images | Link |")
        lines.append("|---------|------|-------|--------|------|")
        for h in complete:
            link = _hotel_link(h.hotel_id)
            lines.append(f"| {h.hotel_id} | {h.name} | {h.stars} | {h.images_count} | [Open]({link}) |")
    else:
        lines.append("_No hotels with complete data._")
    lines.append("")

    lines.append("## 2. Hotels without complete static data or not found\n")
    if incomplete:
        lines.append("| HOTELID | Status | Name | Missing Fields | Codes | Destination_EN | Link |")
        lines.append("|---------|--------|------|----------------|-------|----------------|------|")
        for h in incomplete:
            missing = "; ".join(h.missing_fields)
            link = _hotel_link(h.hotel_id)
            lines.append(
                f"| {h.hotel_id} | {h.status} | {h.name} | {missing} "
                f"| {h.codes} | {h.destination_en} | [Open]({link}) |"
            )
    else:
        lines.append("_All hotels have complete data._")
    lines.append("")

    lines.append("## Summary\n")
    lines.append(f"- **Total checked**: {summary.total}")
    lines.append(f"- **Complete**: {summary.complete}")
    lines.append(f"- **Incomplete**: {summary.incomplete}")
    lines.append(f"- **Not found**: {summary.not_found}")
    lines.append("")

    (REPORTS_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _write_json(
    complete: List[CompleteHotel],
    incomplete: List[IncompleteHotel],
    summary: Summary,
) -> None:
    data = {
        "complete_hotels": [h.to_dict() for h in complete],
        "incomplete_hotels": [h.to_dict() for h in incomplete],
        "summary": summary.to_dict(),
    }
    (REPORTS_DIR / "report.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# HTML — Interactive dashboard
# ---------------------------------------------------------------------------

def _write_html(
    complete: List[CompleteHotel],
    incomplete: List[IncompleteHotel],
    summary: Summary,
) -> None:
    """Generate a self-contained interactive HTML report.

    Features:
    - Summary cards with distribution chart
    - Two tabs: Complete / Problems
    - Column sorting (click header)
    - Global search
    - Per-column dropdown filters for Status and Missing_* fields
    - Clickable hotel links
    - Export filtered view to CSV
    """

    # --- Build JS data arrays ---
    complete_js = json.dumps([
        {
            "id": h.hotel_id,
            "name": h.name,
            "stars": h.stars,
            "imgs": h.images_count,
            "link": _hotel_link(h.hotel_id),
        }
        for h in complete
    ], ensure_ascii=False)

    problems_js = json.dumps([
        {
            "id": h.hotel_id,
            "name": h.name,
            "status": h.status,
            "mName": _yn(_missing_name(h.missing_fields)),
            "mStars": _yn(_missing_stars(h.missing_fields)),
            "mImages": _yn(_missing_images(h.missing_fields)),
            "mImgAlt": _yn(_missing_image_alt(h.missing_fields)),
            "mImgName": _yn(_missing_image_name(h.missing_fields)),
            "mImgUrl": _yn(_missing_image_url(h.missing_fields)),
            "raw": "; ".join(h.missing_fields),
            "codes": h.codes,
            "destEn": h.destination_en,
            "link": _hotel_link(h.hotel_id),
        }
        for h in incomplete
    ], ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SabreLDS Hotel Static Data Report</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#f0f2f5; color:#1a1a2e; }}
.header {{ background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%); color:#fff; padding:24px 32px; }}
.header h1 {{ font-size:22px; font-weight:600; }}
.header .subtitle {{ color:#a0aec0; margin-top:4px; font-size:13px; }}

/* Summary cards */
.cards {{ display:flex; gap:16px; padding:20px 32px; flex-wrap:wrap; }}
.card {{ background:#fff; border-radius:10px; padding:20px 24px; min-width:160px; flex:1;
         box-shadow:0 1px 3px rgba(0,0,0,.08); border-left:4px solid #4361ee; }}
.card.green {{ border-left-color:#06d6a0; }}
.card.orange {{ border-left-color:#f77f00; }}
.card.red {{ border-left-color:#ef476f; }}
.card .label {{ font-size:12px; color:#666; text-transform:uppercase; letter-spacing:.5px; }}
.card .value {{ font-size:28px; font-weight:700; margin-top:4px; }}
.card .pct {{ font-size:13px; color:#888; margin-top:2px; }}

/* Chart bar */
.chart-bar {{ display:flex; height:32px; border-radius:6px; overflow:hidden; margin:0 32px 8px; }}
.chart-bar .seg {{ display:flex; align-items:center; justify-content:center; color:#fff; font-size:11px; font-weight:600; }}
.chart-bar .seg.green {{ background:#06d6a0; }}
.chart-bar .seg.orange {{ background:#f77f00; }}
.chart-bar .seg.red {{ background:#ef476f; }}
.legend {{ display:flex; gap:16px; padding:0 32px 16px; font-size:12px; color:#555; }}
.legend span::before {{ content:''; display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; vertical-align:middle; }}
.legend .lg::before {{ background:#06d6a0; }}
.legend .lo::before {{ background:#f77f00; }}
.legend .lr::before {{ background:#ef476f; }}

/* Tabs */
.tabs {{ display:flex; gap:0; padding:0 32px; }}
.tab {{ padding:10px 24px; cursor:pointer; font-size:14px; font-weight:500; color:#666;
        border-bottom:3px solid transparent; transition:all .2s; }}
.tab:hover {{ color:#1a1a2e; }}
.tab.active {{ color:#4361ee; border-bottom-color:#4361ee; }}
.tab .badge {{ background:#e2e8f0; color:#555; border-radius:10px; padding:2px 8px; font-size:11px; margin-left:6px; }}
.tab.active .badge {{ background:#4361ee; color:#fff; }}

/* Controls */
.controls {{ padding:12px 32px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
.controls input[type=text] {{ padding:8px 12px; border:1px solid #d1d5db; border-radius:6px; font-size:13px; width:260px; }}
.controls select {{ padding:8px 10px; border:1px solid #d1d5db; border-radius:6px; font-size:13px; background:#fff; }}
.controls button {{ padding:8px 16px; border:none; border-radius:6px; font-size:13px; cursor:pointer;
                    background:#4361ee; color:#fff; font-weight:500; }}
.controls button:hover {{ background:#3a56d4; }}
.controls button.secondary {{ background:#e2e8f0; color:#555; }}
.controls button.secondary:hover {{ background:#cbd5e1; }}
.controls .count {{ font-size:13px; color:#888; margin-left:auto; }}

/* Table */
.table-wrap {{ padding:0 32px 32px; overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; background:#fff; border-radius:8px; overflow:hidden;
         box-shadow:0 1px 3px rgba(0,0,0,.08); }}
th {{ background:#f8fafc; padding:10px 12px; text-align:left; font-weight:600; color:#475569; cursor:pointer;
     user-select:none; white-space:nowrap; border-bottom:2px solid #e2e8f0; position:sticky; top:0; }}
th:hover {{ background:#eef1f6; }}
th .arrow {{ font-size:10px; margin-left:4px; color:#94a3b8; }}
td {{ padding:8px 12px; border-bottom:1px solid #f1f5f9; vertical-align:top; }}
tr:hover td {{ background:#f8fafc; }}
a.link {{ color:#4361ee; text-decoration:none; font-weight:500; }}
a.link:hover {{ text-decoration:underline; }}
.badge-yes {{ background:#fee2e2; color:#dc2626; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }}
.badge-no {{ background:#dcfce7; color:#16a34a; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }}
.badge-status {{ padding:3px 10px; border-radius:4px; font-size:11px; font-weight:600; }}
.badge-status.nf {{ background:#fee2e2; color:#dc2626; }}
.badge-status.inc {{ background:#fef3c7; color:#d97706; }}
.raw-field {{ max-width:280px; white-space:normal; font-size:11px; color:#666; }}
.hidden {{ display:none; }}
</style>
</head>
<body>

<div class="header">
  <h1>SabreLDS Hotel Static Data Report</h1>
  <div class="subtitle">AbroadStaticData / Hotel — Vendor: SabreLDS</div>
</div>

<!-- Summary cards -->
<div class="cards">
  <div class="card">
    <div class="label">Total Checked</div>
    <div class="value">{summary.total:,}</div>
  </div>
  <div class="card green">
    <div class="label">Complete</div>
    <div class="value">{summary.complete:,}</div>
    <div class="pct">{summary.complete/summary.total*100:.1f}%</div>
  </div>
  <div class="card orange">
    <div class="label">Incomplete</div>
    <div class="value">{summary.incomplete:,}</div>
    <div class="pct">{summary.incomplete/summary.total*100:.1f}%</div>
  </div>
  <div class="card red">
    <div class="label">Not Found</div>
    <div class="value">{summary.not_found:,}</div>
    <div class="pct">{summary.not_found/summary.total*100:.1f}%</div>
  </div>
</div>

<div class="chart-bar">
  <div class="seg green" style="width:{summary.complete/summary.total*100:.1f}%">{summary.complete/summary.total*100:.1f}%</div>
  <div class="seg orange" style="width:{summary.incomplete/summary.total*100:.1f}%">{summary.incomplete/summary.total*100:.1f}%</div>
  <div class="seg red" style="width:{summary.not_found/summary.total*100:.1f}%">{summary.not_found/summary.total*100:.1f}%</div>
</div>
<div class="legend">
  <span class="lg">Complete</span>
  <span class="lo">Incomplete</span>
  <span class="lr">Not Found</span>
</div>

<!-- Tabs -->
<div class="tabs">
  <div class="tab active" data-tab="problems">Problems <span class="badge">{len(incomplete):,}</span></div>
  <div class="tab" data-tab="complete">Complete <span class="badge">{len(complete):,}</span></div>
</div>

<!-- Problems tab -->
<div id="tab-problems">
  <div class="controls">
    <input type="text" id="searchProblems" placeholder="Search by Hotel ID or Name...">
    <select id="filterStatus"><option value="">All Statuses</option><option value="NOT_FOUND">NOT_FOUND</option><option value="INCOMPLETE">INCOMPLETE</option></select>
    <select id="filterField">
      <option value="">All Missing Fields</option>
      <option value="mStars">Missing Stars</option>
      <option value="mImages">Missing Images</option>
      <option value="mImgAlt">Missing Image Alt</option>
      <option value="mImgName">Missing Image Name</option>
      <option value="mImgUrl">Missing Image Url</option>
      <option value="mName">Missing Name</option>
    </select>
    <button class="secondary" onclick="resetFilters()">Reset</button>
    <button onclick="exportCSV('problems')">Export CSV</button>
    <div class="count" id="countProblems"></div>
  </div>
  <div class="table-wrap">
    <table id="tblProblems">
      <thead><tr>
        <th>#</th>
        <th data-col="id">HOTELID <span class="arrow">&#9650;</span></th>
        <th data-col="name">Name <span class="arrow"></span></th>
        <th data-col="status">Status <span class="arrow"></span></th>
        <th data-col="mStars">Stars <span class="arrow"></span></th>
        <th data-col="mImages">Images <span class="arrow"></span></th>
        <th data-col="mImgAlt">Img Alt <span class="arrow"></span></th>
        <th data-col="mImgName">Img Name <span class="arrow"></span></th>
        <th data-col="mImgUrl">Img Url <span class="arrow"></span></th>
        <th data-col="mName">Name? <span class="arrow"></span></th>
        <th data-col="codes">Code <span class="arrow"></span></th>
        <th data-col="destEn">Destination <span class="arrow"></span></th>
        <th>Details</th>
        <th>Link</th>
      </tr></thead>
      <tbody id="bodyProblems"></tbody>
    </table>
  </div>
</div>

<!-- Complete tab -->
<div id="tab-complete" class="hidden">
  <div class="controls">
    <input type="text" id="searchComplete" placeholder="Search by Hotel ID or Name...">
    <button onclick="exportCSV('complete')">Export CSV</button>
    <div class="count" id="countComplete"></div>
  </div>
  <div class="table-wrap">
    <table id="tblComplete">
      <thead><tr>
        <th>#</th>
        <th data-col="id">HOTELID <span class="arrow">&#9650;</span></th>
        <th data-col="name">Name <span class="arrow"></span></th>
        <th data-col="stars">Stars <span class="arrow"></span></th>
        <th data-col="imgs">Images <span class="arrow"></span></th>
        <th>Link</th>
      </tr></thead>
      <tbody id="bodyComplete"></tbody>
    </table>
  </div>
</div>

<script>
// --- Data ---
const DATA_COMPLETE = {complete_js};
const DATA_PROBLEMS = {problems_js};

// --- State ---
let sortCol = 'id', sortAsc = true, activeTab = 'problems';

// --- Tabs ---
document.querySelectorAll('.tab').forEach(t => {{
  t.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    activeTab = t.dataset.tab;
    document.getElementById('tab-problems').classList.toggle('hidden', activeTab !== 'problems');
    document.getElementById('tab-complete').classList.toggle('hidden', activeTab !== 'complete');
  }});
}});

// --- Badge helpers ---
function ynBadge(v) {{
  return v === 'YES' ? '<span class="badge-yes">YES</span>' : '<span class="badge-no">NO</span>';
}}
function statusBadge(s) {{
  const cls = s === 'NOT_FOUND' ? 'nf' : 'inc';
  return `<span class="badge-status ${{cls}}">${{s}}</span>`;
}}
function esc(s) {{ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }}

// --- Render problems ---
function renderProblems() {{
  const search = document.getElementById('searchProblems').value.toLowerCase();
  const fStatus = document.getElementById('filterStatus').value;
  const fField = document.getElementById('filterField').value;

  let filtered = DATA_PROBLEMS.filter(r => {{
    if (search && !r.id.toLowerCase().includes(search) && !r.name.toLowerCase().includes(search)) return false;
    if (fStatus && r.status !== fStatus) return false;
    if (fField && r[fField] !== 'YES') return false;
    return true;
  }});

  filtered.sort((a,b) => {{
    let va = a[sortCol] ?? '', vb = b[sortCol] ?? '';
    if (typeof va === 'number') return sortAsc ? va-vb : vb-va;
    va = String(va).toLowerCase(); vb = String(vb).toLowerCase();
    return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
  }});

  const tbody = document.getElementById('bodyProblems');
  tbody.innerHTML = filtered.map((r,i) => `<tr>
    <td style="color:#94a3b8;font-size:11px">${{i+1}}</td>
    <td><b>${{esc(r.id)}}</b></td>
    <td>${{esc(r.name)}}</td>
    <td>${{statusBadge(r.status)}}</td>
    <td>${{ynBadge(r.mStars)}}</td>
    <td>${{ynBadge(r.mImages)}}</td>
    <td>${{ynBadge(r.mImgAlt)}}</td>
    <td>${{ynBadge(r.mImgName)}}</td>
    <td>${{ynBadge(r.mImgUrl)}}</td>
    <td>${{ynBadge(r.mName)}}</td>
    <td>${{esc(r.codes)}}</td>
    <td>${{esc(r.destEn)}}</td>
    <td class="raw-field">${{esc(r.raw)}}</td>
    <td><a class="link" href="${{r.link}}" target="_blank">Open</a></td>
  </tr>`).join('');

  document.getElementById('countProblems').textContent = `Showing ${{filtered.length}} of ${{DATA_PROBLEMS.length}}`;
  window._filteredProblems = filtered;
}}

// --- Render complete ---
function renderComplete() {{
  const search = document.getElementById('searchComplete').value.toLowerCase();

  let filtered = DATA_COMPLETE.filter(r => {{
    if (search && !r.id.toLowerCase().includes(search) && !r.name.toLowerCase().includes(search)) return false;
    return true;
  }});

  filtered.sort((a,b) => {{
    let va = a[sortCol] ?? '', vb = b[sortCol] ?? '';
    if (typeof va === 'number') return sortAsc ? va-vb : vb-va;
    va = String(va).toLowerCase(); vb = String(vb).toLowerCase();
    return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
  }});

  const tbody = document.getElementById('bodyComplete');
  tbody.innerHTML = filtered.map((r,i) => `<tr>
    <td style="color:#94a3b8;font-size:11px">${{i+1}}</td>
    <td><b>${{esc(r.id)}}</b></td>
    <td>${{esc(r.name)}}</td>
    <td>${{esc(r.stars)}}</td>
    <td>${{r.imgs}}</td>
    <td><a class="link" href="${{r.link}}" target="_blank">Open</a></td>
  </tr>`).join('');

  document.getElementById('countComplete').textContent = `Showing ${{filtered.length}} of ${{DATA_COMPLETE.length}}`;
  window._filteredComplete = filtered;
}}

// --- Sorting ---
document.querySelectorAll('th[data-col]').forEach(th => {{
  th.addEventListener('click', () => {{
    const col = th.dataset.col;
    if (sortCol === col) sortAsc = !sortAsc; else {{ sortCol = col; sortAsc = true; }}
    // Update arrows
    th.closest('thead').querySelectorAll('.arrow').forEach(a => a.textContent = '');
    th.querySelector('.arrow').textContent = sortAsc ? '\\u25B2' : '\\u25BC';
    if (activeTab === 'problems') renderProblems(); else renderComplete();
  }});
}});

// --- Filters ---
document.getElementById('searchProblems').addEventListener('input', renderProblems);
document.getElementById('filterStatus').addEventListener('change', renderProblems);
document.getElementById('filterField').addEventListener('change', renderProblems);
document.getElementById('searchComplete').addEventListener('input', renderComplete);

function resetFilters() {{
  document.getElementById('searchProblems').value = '';
  document.getElementById('filterStatus').value = '';
  document.getElementById('filterField').value = '';
  renderProblems();
}}

// --- Export CSV ---
function exportCSV(type) {{
  let csv, filename;
  if (type === 'problems') {{
    const rows = window._filteredProblems || DATA_PROBLEMS;
    csv = 'HOTELID,Name,Status,Missing_Stars,Missing_Images,Missing_ImgAlt,Missing_ImgName,Missing_ImgUrl,Missing_Name,Code,Destination,Details,Link\\n';
    rows.forEach(r => {{
      csv += [r.id,`"${{r.name}}"`,r.status,r.mStars,r.mImages,r.mImgAlt,r.mImgName,r.mImgUrl,r.mName,r.codes,`"${{r.destEn}}"`,`"${{r.raw}}"`,r.link].join(',') + '\\n';
    }});
    filename = 'problems_filtered.csv';
  }} else {{
    const rows = window._filteredComplete || DATA_COMPLETE;
    csv = 'HOTELID,Name,Stars,Images,Link\\n';
    rows.forEach(r => {{
      csv += [r.id,`"${{r.name}}"`,`"${{r.stars}}"`,r.imgs,r.link].join(',') + '\\n';
    }});
    filename = 'complete_filtered.csv';
  }}
  const blob = new Blob(['\\uFEFF'+csv], {{type:'text/csv;charset=utf-8;'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}}

// --- Init ---
renderProblems();
renderComplete();
</script>
</body>
</html>"""

    (REPORTS_DIR / "report.html").write_text(html_content, encoding="utf-8")
