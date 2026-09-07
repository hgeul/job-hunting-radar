# -*- coding: utf-8 -*-
"""HTML 대시보드. **자체 완결형**(인라인 CSS/JS, 외부 요청 0)이라야 한다.

개인 매칭 결과라 로컬 파일로만 만든다. 외부 전송·CDN 참조를 넣지 않는다.
"""

import datetime as dt
import json
import os

from radar.models import deadline_info
from radar.settings import HERE, source_badge


_DASH_TEMPLATE = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PERSON__ · 채용 매칭 대시보드</title>
<style>
  :root{color-scheme:light}
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,'Segoe UI',Roboto,'Malgun Gothic',sans-serif;background:#f4f5f7;color:#1c2733}
  .wrap{max-width:1100px;margin:0 auto;padding:18px}
  h1{font-size:20px;margin:0 0 4px}
  .meta{color:#5b6b7b;font-size:13px;margin-bottom:12px}
  .ctrl{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
  .ctrl input{flex:1;min-width:160px;padding:8px 10px;border:1px solid #cdd6df;border-radius:8px;font-size:14px}
  .ctrl button{padding:8px 12px;border:1px solid #cdd6df;background:#fff;border-radius:8px;cursor:pointer;font-size:13px}
  .ctrl button.on{background:#1565c0;color:#fff;border-color:#1565c0}
  .tblwrap{overflow-x:auto;background:#fff;border:1px solid #e2e8ef;border-radius:10px}
  table{width:100%;border-collapse:collapse;font-size:13px;min-width:720px}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid #eef2f6;vertical-align:top}
  th{background:#fafbfc;color:#5b6b7b;font-weight:600;white-space:nowrap;position:sticky;top:0}
  tr.hot td{background:#fff8e6}
  tr.exp td{opacity:.5}
  .sc{font-weight:700;font-size:15px}
  .dl{white-space:nowrap;font-weight:600}
  .dl.urg{color:#c62828}
  .dl.roll{color:#2e7d32}
  .dl.unk{color:#98a5b3;font-weight:400}
  a.job{color:#1565c0;text-decoration:none;font-weight:600}
  a.job:hover{text-decoration:underline}
  .ol{color:#6b7887;font-size:12px;margin-top:3px;max-width:420px}
  .src{font-size:11px;color:#8a97a6;margin-left:6px;white-space:nowrap}
  .vd{white-space:nowrap;font-size:12px;color:#43536a}
  .foot{color:#98a5b3;font-size:12px;margin-top:12px;text-align:center}
  .empty{padding:30px;text-align:center;color:#98a5b3}
</style>
<div class="wrap">
  <h1>🎯 __PERSON__ · 채용 매칭 대시보드</h1>
  <div class="meta">생성 __GEN__ · 총 <b>__TOTAL__</b>건 · 🔥 __HOT__건 · ⏰ 마감임박 __URGENT__건 · 알림문턱 __NOTIFY__점</div>
  <div class="ctrl">
    <input id="q" placeholder="회사·공고 검색…">
    <button id="bScore" class="on">점수순</button>
    <button id="bDl">마감임박순</button>
  </div>
  <div class="tblwrap">
    <table>
      <thead><tr>
        <th>점수</th><th>마감</th><th>판정</th><th>회사</th><th>공고</th><th>경력</th><th>지역</th><th>등록</th>
      </tr></thead>
      <tbody id="tb"></tbody>
    </table>
  </div>
  <p class="foot">job-hunting-radar · 로컬 생성 · 외부 전송 없음</p>
</div>
<script>
const DATA = __DATA__;
let mode = 'score';
const q = document.getElementById('q');
function cell(txt){const td=document.createElement('td');td.textContent=txt;return td;}
function render(){
  const term=(q.value||'').trim().toLowerCase();
  let rows=DATA.filter(r=>!term||(r.company+' '+r.title).toLowerCase().includes(term));
  rows.sort((a,b)=> mode==='dl' ? (a.dsort-b.dsort)||(b.score-a.score) : (b.score-a.score)||(a.dsort-b.dsort));
  const tb=document.getElementById('tb');tb.innerHTML='';
  if(!rows.length){const tr=document.createElement('tr');const td=document.createElement('td');td.colSpan=8;td.className='empty';td.textContent='조건에 맞는 공고가 없어요.';tr.appendChild(td);tb.appendChild(tr);return;}
  for(const r of rows){
    const tr=document.createElement('tr');
    if(r.expired)tr.className='exp';else if(r.hot)tr.className='hot';
    const sc=cell('');sc.innerHTML='<span class="sc">'+r.score+'</span>'+(r.hot?' 🔥':'');tr.appendChild(sc);
    const dl=document.createElement('td');const sp=document.createElement('span');
    sp.className='dl '+(r.urgent?'urg':(r.dstate==='rolling'?'roll':(r.dstate==='unknown'?'unk':'')));
    sp.textContent=(r.urgent?'⏰ ':'')+r.deadline;dl.appendChild(sp);tr.appendChild(dl);
    const vd=cell(r.verdict);vd.className='vd';tr.appendChild(vd);
    tr.appendChild(cell(r.company));
    const jc=document.createElement('td');const a=document.createElement('a');
    a.className='job';a.href=r.url;a.target='_blank';a.rel='noopener';a.textContent=r.title;jc.appendChild(a);
    if(r.src){const s=document.createElement('span');s.className='src';s.textContent=r.src;jc.appendChild(s);}
    if(r.one_liner){const d=document.createElement('div');d.className='ol';d.textContent=r.one_liner;jc.appendChild(d);}
    tr.appendChild(jc);
    tr.appendChild(cell(r.career));
    tr.appendChild(cell(r.region));
    tr.appendChild(cell(r.date));
    tb.appendChild(tr);
  }
}
document.getElementById('bScore').onclick=function(){mode='score';this.classList.add('on');document.getElementById('bDl').classList.remove('on');render();};
document.getElementById('bDl').onclick=function(){mode='dl';this.classList.add('on');document.getElementById('bScore').classList.remove('on');render();};
q.oninput=render;
render();
</script>
"""


def _badge(card):
    """대시보드 공고명 옆 배지. target이면 tier를 앞에 붙인다."""
    tg = card.get("target")
    bits = []
    if tg:
        bits.append(f"🎯{tg.get('tier', '?')}")
    sb = source_badge(card.get("sources"))
    if sb:
        bits.append(sb)
    return " ".join(bits)


def write_dashboard(cfg, seen):
    """seen의 매칭 카드들을 모아 자체 완결형 HTML 대시보드 생성(외부 의존 0).

    matches_dir/index.html 로 저장(매 실행 덮어씀). 카드는 notified 시 seen에 축적되므로
    여러 날치 매칭이 마감일·점수 기준으로 한눈에 정렬·검색된다. 카드 없으면 None.
    """
    import html as _html  # noqa: WPS433
    out_dir = os.path.join(HERE, cfg["output"]["matches_dir"])
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "index.html")
    keep = cfg["output"].get("seen_retention_days", 90)
    cutoff = (dt.date.today() - dt.timedelta(days=keep)).isoformat()
    notify = cfg["scoring"]["notify_threshold"]

    rows = []
    for rec in seen.values():
        c = rec.get("card")
        if not c or rec.get("first_seen", "9999") < cutoff:
            continue
        dlabel, ddays, dstate = deadline_info(c.get("deadline"))
        if dstate == "date":
            dsort = ddays if ddays is not None else 99999
        elif dstate == "rolling":
            dsort = 100000
        else:
            dsort = 100001
        rows.append({
            "score": c.get("score", 0),
            # 결격 제외 공고는 강조하지 않는다(노트·텔레그램과 같은 기준).
            "hot": (c.get("score", 0) >= notify
                    and c.get("recommendation") != "SKIP"),
            # scored 는 Phase 6 부터, llm 은 그 이전 카드가 쓰던 키다. 둘 다 본다
            # (state 는 seen_retention_days 만큼 옛 카드를 들고 있다).
            "verdict": c.get("verdict") or (
                "-" if (c.get("scored") or c.get("llm")) else "⚙️ 근거 미확보"),
            "deadline": dlabel, "dstate": dstate, "dsort": dsort,
            "urgent": dstate == "date" and ddays is not None and 0 <= ddays <= 3,
            "expired": dstate == "date" and ddays is not None and ddays < 0,
            "company": c.get("company", "?"), "title": c.get("title", "?"),
            "url": c.get("url", "#"), "career": c.get("career", "-"),
            "region": c.get("region", "-"), "one_liner": c.get("one_liner") or "",
            "src": _badge(c),
            "date": c.get("date", ""),
        })
    if not rows:
        return None
    rows.sort(key=lambda r: r["score"], reverse=True)
    hot = sum(1 for r in rows if r["hot"])
    urgent = sum(1 for r in rows if r["urgent"])
    data_json = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")

    doc = _DASH_TEMPLATE
    for k, v in {
        "__PERSON__": _html.escape(cfg.get("name", "") or "전체"),
        "__GEN__": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "__TOTAL__": str(len(rows)), "__HOT__": str(hot),
        "__URGENT__": str(urgent), "__NOTIFY__": str(notify),
        "__DATA__": data_json,
    }.items():
        doc = doc.replace(k, v)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    return path
