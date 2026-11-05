// butt_candidates.js - '지속 변화 후보(꽁초·쓰레기)' 섹션: 요약 카드 + 필터/정렬 + 목록 + 패널 썸네일 + 검수(라벨 저장)
// script.js 의 loadEvents() 가 window.renderButtCandidates(events) 를 호출한다.
// 표시 대상: persistent_change_candidate(후보 추출) 와 cigarette_butt_detected / litter_detected(자동 감지).
// '오염도 현황' 섹션(#pollution)에는 '오염 감지 지수(시범)'를 장치별로 채운다(서버 수정 없음, 화면에서 계산).
// index.html 을 더 고칠 필요 없음: 요약/필터 영역(#butt-tools)은 표(#butt-table) 위에 이 스크립트가 만든다.
(function () {
    const CAND_TYPE = "persistent_change_candidate";
    const AUTO_TYPES = ["cigarette_butt_detected", "litter_detected"];
    const ALL_TYPES = [CAND_TYPE].concat(AUTO_TYPES);
    const LABELS = [
        ["cigarette_butt", "꽁초"],
        ["other_litter", "쓰레기(꽁초 제외)"],
        ["not_litter", "쓰레기 아님"],
        ["uncertain", "불확실"],
    ];
    const PAGE = 50;
    const reviews = {};                                   // reviews[device_id][candidate_id] = label
    const state = { type: "", status: "", sort: "new", minArea: "0", period: "", auto: 0, updated: "", limit: PAGE, heatDev: "" };
    const FETCH_LIMIT = 1000;                              // 서버가 허용하는 최대값(장치당)
    let timer = null, refreshing = false;
    let lastRows = [];

    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const apiBase = () => (window.API_BASE || "http://localhost:8000").replace(/\/+$/, "");
    const isAuto = (e) => AUTO_TYPES.indexOf(e.event_type) >= 0;
    const labelOf = (e) => (reviews[e.device_id] || {})[e.related_detection_id] || "";
    const areaOf = (e) => { const m = /크기\s*(\d+)x(\d+)px/.exec(e.message || ""); return m ? Number(m[1]) * Number(m[2]) : 0; };
    const isLocalServer = (id) => {
        if (!id) return true;
        const s = String(id).toLowerCase();
        return s.includes("local ai server") || s.includes("local-ai-server");
    };

    // snapshot_ref 가 없어도 message 의 snap=... 토큰으로 찾는다.
    function snapshotId(e) {
        if (e.snapshot_ref) return String(e.snapshot_ref);
        const m = /snap=(snap_[0-9a-zA-Z]+)/.exec(e.message || "");
        return m ? m[1] : null;
    }
    // make_scene.py 로 만든 '첫 변화 전후 장면' 사진이 있으면 message 의 scene=... 토큰으로 찾는다.
    function sceneId(e) {
        const m = /scene=(snap_[0-9a-zA-Z]+)/.exec(e.message || "");
        return m ? m[1] : null;
    }
    const detailText = (e) => String(e.message || "").replace(/\s*\|\s*snap=\S+/, "").replace(/\s*\|\s*scene=\S+/, "").replace(/\s*\|\s*/g, " · ");

    // ---------- 요약 카드 + 필터 ----------
    function ensureTools() {
        let el = document.getElementById("butt-tools");
        if (el) return el;
        const table = document.getElementById("butt-table");
        if (!table || !table.parentNode) return null;
        el = document.createElement("div");
        el.id = "butt-tools";
        table.parentNode.insertBefore(el, table);
        return el;
    }

    function drawTools() {
        const el = ensureTools();
        const count = document.getElementById("butt-count");
        const reviewed = lastRows.filter((e) => labelOf(e)).length;
        if (count) count.textContent = `${lastRows.length}건 · 검수 ${reviewed}건`;
        if (!el) return;
        const n = (lab) => lastRows.filter((e) => labelOf(e) === lab).length;
        const today = new Date().toDateString();
        const todayN = lastRows.filter((e) => new Date(e.timestamp).toDateString() === today).length;
        const card = (label, v, color) => `<div style="background:#f8fafd;border:1px solid #dbe4f0;border-radius:6px;padding:8px 14px;min-width:88px;">
            <div style="font-size:12px;color:#6b7a90;">${label}</div>
            <div style="font-size:20px;font-weight:bold;color:${color || "#1f2a3a"};">${v}</div></div>`;
        const sel = (key, opts) => `<select onchange="__buttSet('${key}', this.value)">` +
            opts.map(([v, t]) => `<option value="${v}" ${String(state[key]) === String(v) ? "selected" : ""}>${t}</option>`).join("") + "</select>";
        el.innerHTML = `
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;">
            ${card("전체", lastRows.length)}${card("오늘 감지", todayN)}${card("검수 대기", lastRows.length - reviewed, "#d9822b")}
            ${card("꽁초", n("cigarette_butt"), "#2f6f62")}${card("쓰레기", n("other_litter"), "#2f6f62")}
            ${card("쓰레기 아님", n("not_litter"))}${card("불확실", n("uncertain"))}
        </div>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;font-size:13px;">
            <span>종류</span>${sel("type", [["", "전체"], ["auto", "자동 감지"], ["candidate", "후보 추출"]])}
            <span>검수</span>${sel("status", [["", "전체"], ["none", "미검수"]].concat(LABELS))}
            <span>정렬</span>${sel("sort", [["new", "최신순"], ["old", "오래된순"], ["large", "큰 것 먼저"]])}
            <span>기간</span>${sel("period", [["", "전체"], ["24h", "최근 24시간"], ["7d", "최근 7일"]])}
            <span>최소 면적(후보)</span>${sel("minArea", [["0", "제한 없음"], ["300", "300px²"], ["500", "500px²"], ["800", "800px²"]])}
            <span style="color:#6b7a90;">표시 ${visibleRows().length}건 / 전체 ${lastRows.length}건</span>
        </div>
        <div style="font-size:12px;color:#6b7a90;margin-bottom:10px;">
            ※ '큰 것 먼저' 정렬과 '최소 면적'은 크기만 보는 단순 규칙입니다(학습 모델 점수가 아닙니다). 화면의 크기는 가로×세로 상자 값이라 평가 때 쓴 면적과 같지 않고, 기준값은 임시입니다. 작은 쓰레기를 놓칠 수 있으니 필요하면 '제한 없음'으로 확인하세요.
        </div>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;font-size:13px;">
            <button onclick="__buttRefresh()" style="padding:5px 12px;cursor:pointer;">↻ 새로고침</button>
            <span>자동 갱신</span>${sel("auto", [["0", "끔"], ["30", "30초"], ["60", "60초"]])}
            <span style="color:#6b7a90;">마지막 갱신 ${esc(state.updated || "-")} · 불러온 이벤트는 장치당 최대 ${FETCH_LIMIT}건</span>
        </div>`;
        drawViz();
    }

    function visibleRows() {
        let rows = lastRows.filter((e) => {
            if (state.type === "auto" && !isAuto(e)) return false;
            if (state.type === "candidate" && isAuto(e)) return false;
            const minA = Number(state.minArea) || 0;           // 후보에만 적용. 크기를 못 읽은 항목(0)은 숨기지 않는다
            if (minA > 0 && !isAuto(e)) { const a = areaOf(e); if (a > 0 && a < minA) return false; }
            if (state.status === "none") return !labelOf(e);
            if (state.status) return labelOf(e) === state.status;
            return true;
        });
        const ts = (e) => String(e.timestamp || "");
        if (state.sort === "old") rows.sort((a, b) => (ts(a) < ts(b) ? -1 : 1));
        else if (state.sort === "large") rows.sort((a, b) => areaOf(b) - areaOf(a) || (ts(a) < ts(b) ? 1 : -1));
        else rows.sort((a, b) => (ts(a) < ts(b) ? 1 : -1));
        return rows;
    }

    function reviewCell(e) {
        const cid = e.related_detection_id;
        if (!cid) return "—";
        const cur = labelOf(e);
        const opts = [`<option value="" disabled ${cur ? "" : "selected"}>미검수</option>`]
            .concat(LABELS.map(([v, t]) => `<option value="${v}" ${cur === v ? "selected" : ""}>${t}</option>`));
        return `<select class="butt-review" data-dev="${esc(e.device_id)}" data-cid="${esc(cid)}">${opts.join("")}</select>
                <span class="butt-saved" style="margin-left:6px;font-size:12px;color:#1f8a4c;"></span>`;
    }

    function sceneLink(e) {
        const sid = sceneId(e);
        if (!sid) return "";
        const url = `${apiBase()}/api/snapshots/${encodeURIComponent(sid)}`;
        return `<a href="${esc(url)}" target="_blank" rel="noopener" style="display:inline-block;margin-top:4px;font-size:12px;">▶ 첫 변화 전후 장면 보기</a>`;
    }

    function drawTable() {
        const tbody = document.querySelector("#butt-table tbody");
        if (!tbody) return;
        const rows = visibleRows();
        if (!rows.length) {
            const msg = lastRows.length ? "조건에 맞는 후보가 없습니다." : "수신된 지속 변화 후보가 없습니다.";
            tbody.innerHTML = `<tr><td class="muted" colspan="5">${msg}</td></tr>`;
            return;
        }
        const time = (t) => (typeof fmtTime === "function" ? fmtTime(t) : t);
        const shown = rows.slice(0, state.limit);
        tbody.innerHTML = shown.map((e) => {
            const id = snapshotId(e);
            const url = id ? `${apiBase()}/api/snapshots/${encodeURIComponent(id)}` : null;
            const thumb = url
                ? `<a href="${esc(url)}" target="_blank" rel="noopener"><img src="${esc(url)}" alt="검수 패널" loading="lazy" style="width:220px;border:1px solid #c8d4e6;border-radius:4px;display:block;"></a>`
                : "—";
            return `<tr>
                <td>${esc(time(e.timestamp))}</td>
                <td>${esc(e.device_id)}</td>
                <td>${esc(detailText(e))}</td>
                <td>${thumb}${sceneLink(e)}</td>
                <td>${reviewCell(e)}</td>
            </tr>`;
        }).join("") + (rows.length > shown.length
            ? `<tr><td colspan="5" style="text-align:center;"><button onclick="__buttMore()" style="padding:6px 14px;cursor:pointer;">더 보기 (남은 ${rows.length - shown.length}건)</button></td></tr>`
            : "");
    }


    // ---------- 오염 감지 지수(시범) ----------
    const POLLUTION = { hours: 24, weight: { cigarette_butt: 1, other_litter: 3 }, warn: 1, high: 5 };
    let deviceInfo = [], deviceInfoAt = 0;

    async function loadDeviceInfo(force) {
        if (!force && deviceInfo.length && Date.now() - deviceInfoAt < 30000) return;
        try {
            const r = await fetch(`${apiBase()}/api/devices`);
            if (r.ok) { deviceInfo = (await r.json()).devices || []; deviceInfoAt = Date.now(); }
        } catch (_) { }
    }

    const FRAME = { w: 1920, h: 1080, gx: 16, gy: 9, fine: 10 };
    function boxOf(e) {
        const m = /위치\s*(-?\d+)\s*,\s*(-?\d+)/.exec(e.message || "");
        const s = /크기\s*(\d+)x(\d+)px/.exec(e.message || "");
        return (m && s) ? { x: Number(m[1]), y: Number(m[2]), w: Number(s[1]), h: Number(s[2]) } : null;
    }
    function coverageOf(events) {
        const fw = Math.ceil(FRAME.w / FRAME.fine), fh = Math.ceil(FRAME.h / FRAME.fine);
        const fine = new Uint8Array(fw * fh), grid = new Uint8Array(FRAME.gx * FRAME.gy);
        const cw = FRAME.w / FRAME.gx, ch = FRAME.h / FRAME.gy;
        let used = 0;
        events.forEach((e) => {
            const b = boxOf(e);
            if (!b) return;
            const x0 = Math.max(0, b.x), y0 = Math.max(0, b.y), x1 = Math.min(FRAME.w, b.x + b.w), y1 = Math.min(FRAME.h, b.y + b.h);
            if (x1 <= x0 || y1 <= y0) return;
            used++;
            for (let j = Math.floor(y0 / FRAME.fine); j <= Math.floor((y1 - 1) / FRAME.fine); j++)
                for (let i = Math.floor(x0 / FRAME.fine); i <= Math.floor((x1 - 1) / FRAME.fine); i++) fine[j * fw + i] = 1;
            for (let j = Math.floor(y0 / ch); j <= Math.floor((y1 - 1) / ch); j++)
                for (let i = Math.floor(x0 / cw); i <= Math.floor((x1 - 1) / cw); i++) grid[j * FRAME.gx + i] = 1;
        });
        const sum = (a) => a.reduce((s, v) => s + v, 0);
        return { used: used, cover: sum(fine) / fine.length, grid: sum(grid) / grid.length };
    }

    function grade(score) {
        if (score >= POLLUTION.high) return ["높음", "#d9534f"];
        if (score >= POLLUTION.warn) return ["주의", "#e8892b"];
        return ["양호", "#2f6f62"];
    }

    function drawPollution() {
        const sec = document.getElementById("pollution");
        const tbody = document.querySelector("#pollution-table tbody");
        if (!tbody) return;
        if (sec && sec.querySelector) {
            const badge = sec.querySelector("h2 .badge");
            if (badge) { badge.textContent = "시범"; }
            const note = sec.querySelector(".note");
            if (note) note.textContent = `※ 최근 ${POLLUTION.hours}시간에 사람이 확인한 꽁초(${POLLUTION.weight.cigarette_butt}점)와 쓰레기(${POLLUTION.weight.other_litter}점)의 감지 건수를 합한 '오염 감지 지수'입니다. 실제 오염 수준을 잰 값이 아니라 감지 건수 기준이며, 치워진 쓰레기는 반영하지 않습니다. 점수 가중치와 등급 기준(주의 ${POLLUTION.warn}점, 높음 ${POLLUTION.high}점 이상)은 임시 값입니다. 오른쪽 '확정 면적 비율'과 '격자 점유'는 사람이 확정한 후보 상자가 화면(1920x1080 가정)을 덮는 비율과 16x9 격자 중 점유한 칸의 비율이며, 등급에는 아직 쓰지 않는 참고 값입니다. 장치 하나가 여러 영상·카메라를 받으면 겹쳐서 계산되니 카메라 하나 기준으로 해석하세요.`;
        }
        const tr = document.querySelector("#pollution-table thead tr");
        if (tr && tr.insertAdjacentHTML) {
            const txt = tr.textContent || "";
            let add = "";
            if (txt.indexOf("검수 대기") < 0) add += "<th>검수 대기</th>";
            if (txt.indexOf("확정 면적") < 0) add += "<th>확정 면적 비율</th><th>격자 점유</th>";
            if (add) tr.insertAdjacentHTML("beforeend", add);
        }

        const since = Date.now() - POLLUTION.hours * 3600 * 1000;
        
        // 💡 local-ai-server 제외 필터링 적용
        let ids = deviceInfo.map((d) => d.device_id).filter(id => !isLocalServer(id));
        lastRows.forEach((e) => { 
            if (e.device_id && ids.indexOf(e.device_id) < 0 && !isLocalServer(e.device_id)) { 
                ids.push(e.device_id); 
            } 
        });

        if (!ids.length) {
            tbody.innerHTML = `<tr><td class="muted" colspan="8">장치가 없습니다.</td></tr>`;
            return;
        }
        const time = (t) => (typeof fmtTime === "function" ? fmtTime(t) : t);
        
        tbody.innerHTML = ids.map((dev) => {
            const info = deviceInfo.find((d) => d.device_id === dev) || {};
            
            const devEvents = lastRows.filter((e) => e.device_id === dev);
            let lastUpdated = "—";
            if (devEvents.length > 0) {
                const latestEvent = devEvents.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))[0];
                lastUpdated = latestEvent.timestamp;
            }
            
            const recent = devEvents.filter((e) => new Date(e.timestamp).getTime() >= since);
            const nButt = recent.filter((e) => labelOf(e) === "cigarette_butt").length;
            const nLit = recent.filter((e) => labelOf(e) === "other_litter").length;
            const pending = recent.filter((e) => !labelOf(e)).length;
            const score = nButt * POLLUTION.weight.cigarette_butt + nLit * POLLUTION.weight.other_litter;
            const [g, color] = grade(score);
            const cv = coverageOf(recent.filter((e) => ["cigarette_butt", "other_litter"].indexOf(labelOf(e)) >= 0));
            const cvText = (nButt + nLit) === 0 ? "0%" : (cv.used ? `${(cv.cover * 100).toFixed(2)}%` : "—");
            const gridText = (nButt + nLit) === 0 ? "0%" : (cv.used ? `${Math.round(cv.grid * 100)}%` : "—");
            
            return `<tr>
                <td>${esc(dev)}</td>
                <td>${esc(info.location || "-")}</td>
                <td>${score}점 <small style="color:#6b7a90;">(꽁초 ${nButt}, 쓰레기 ${nLit})</small></td>
                <td><span style="background:${color};color:#fff;border-radius:10px;padding:2px 10px;font-size:12px;">${g}</span></td>
                <td>${lastUpdated === "—" ? "—" : esc(time(lastUpdated))}</td> 
                <td>${pending}건</td>
                <td>${cvText} <small style="color:#6b7a90;">(상자 ${cv.used}개)</small></td>
                <td>${gridText}</td>
            </tr>`;
        }).join("");
    }

    // ---------- 처리 흐름 띠 + 격자 히트맵 ----------
    const VIZ_CSS = `
    #butt-viz{background:#0e1a2b;color:#e6edf7;border-radius:10px;padding:18px 20px 16px;margin:10px 0 14px;font-variant-numeric:tabular-nums;}
    #butt-viz h3{margin:0;font-size:15px;font-weight:700;color:#f3f7fc;}
    #butt-viz .bv-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:12px;}
    #butt-viz .bv-sub{font-size:12px;color:#8fa4c2;}
    #butt-viz .bv-flow{display:grid;grid-template-columns:repeat(4,1fr);gap:0;}
    #butt-viz .bv-st{position:relative;padding:2px 22px 4px 14px;border-left:2px solid #2a3f5f;}
    #butt-viz .bv-st:not(:last-child)::after{content:"\\203A";position:absolute;right:4px;top:6px;color:#4b6488;font-size:24px;line-height:1;}
    #butt-viz .bv-n{font-size:34px;font-weight:700;line-height:1.1;color:#f3f7fc;}
    #butt-viz .bv-n small{font-size:13px;font-weight:500;color:#8fa4c2;margin-left:3px;}
    #butt-viz .bv-l{font-size:13px;color:#c5d3e8;margin:2px 0 8px;}
    #butt-viz .bv-bar{height:4px;background:#1b2d47;border-radius:2px;overflow:hidden;}
    #butt-viz .bv-bar i{display:block;height:100%;border-radius:2px;}
    #butt-viz .bv-r{font-size:12px;color:#8fa4c2;margin-top:6px;min-height:16px;}
    #butt-viz .bv-chip{display:inline-block;border-radius:10px;padding:1px 10px;font-size:12px;font-weight:700;color:#fff;vertical-align:middle;}
    #butt-viz hr{border:0;border-top:1px solid #22354f;margin:16px 0 14px;}
    #butt-viz .bv-heat{display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:20px;align-items:start;}
    #butt-viz svg{width:100%;height:auto;display:block;border-radius:6px;background:#0a1422;}
    #butt-viz select{background:#16263d;color:#e6edf7;border:1px solid #2a3f5f;border-radius:5px;padding:3px 6px;font-size:13px;}
    #butt-viz .bv-stat{padding:9px 0;border-bottom:1px solid #1d2f49;}
    #butt-viz .bv-stat:last-of-type{border-bottom:0;}
    #butt-viz .bv-stat b{display:block;font-size:22px;color:#f3f7fc;line-height:1.2;}
    #butt-viz .bv-stat span{font-size:12px;color:#8fa4c2;}
    #butt-viz .bv-legend{display:flex;gap:12px;align-items:center;font-size:12px;color:#8fa4c2;margin-top:8px;flex-wrap:wrap;}
    #butt-viz .bv-legend i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:4px;vertical-align:-1px;}
    #butt-viz .bv-foot{font-size:11.5px;color:#7f94b3;margin-top:12px;line-height:1.55;}
    @media (max-width:760px){#butt-viz .bv-flow{grid-template-columns:repeat(2,1fr);row-gap:14px;}#butt-viz .bv-heat{grid-template-columns:1fr;}}
    `;

    function ensureViz() {
        const tools = ensureTools();
        if (!tools || !tools.parentNode) return null;
        if (!document.getElementById("butt-viz-style")) {
            const st = document.createElement("style");
            st.id = "butt-viz-style";
            st.textContent = VIZ_CSS;
            document.head.appendChild(st);
        }
        let el = document.getElementById("butt-viz");
        if (!el) {
            el = document.createElement("div");
            el.id = "butt-viz";
            tools.parentNode.insertBefore(el, tools);
        }
        return el;
    }

    function drawViz() {
        const el = ensureViz();
        if (!el) return;
        const CONF = ["cigarette_butt", "other_litter"];
        const isConf = (e) => CONF.indexOf(labelOf(e)) >= 0;
        const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);

        const cands = lastRows.filter((e) => !isAuto(e));
        const nAuto = lastRows.length - cands.length;
        const rev = cands.filter((e) => labelOf(e));
        const conf = cands.filter(isConf);
        const since = Date.now() - POLLUTION.hours * 3600 * 1000;
        const ids = deviceInfo.map((d) => d.device_id).filter(id => !isLocalServer(id));
        lastRows.forEach((e) => { if (e.device_id && ids.indexOf(e.device_id) < 0 && !isLocalServer(e.device_id)) ids.push(e.device_id); });
        const recentOf = (dev) => lastRows.filter((e) => e.device_id === dev && new Date(e.timestamp).getTime() >= since);
        const scoreOf = (dev) => recentOf(dev).reduce((s, e) => s + (POLLUTION.weight[labelOf(e)] || 0), 0);
        const total = ids.reduce((s, d) => s + scoreOf(d), 0);
        const top = ids.reduce((m, d) => Math.max(m, scoreOf(d)), 0);
        const [topG, topC] = grade(top);
        const stage = (n, label, w, color, note) => `<div class="bv-st"><div class="bv-n">${n}<small>건</small></div>
            <div class="bv-l">${label}</div><div class="bv-bar"><i style="width:${w}%;background:${color};"></i></div><div class="bv-r">${note}</div></div>`;
        const flow = `
        <div class="bv-head"><h3>무단투기 후보 처리 흐름</h3>
            <span class="bv-sub">불러온 후보 ${cands.length}건 기준${nAuto ? ` · 자동 감지 ${nAuto}건은 따로` : ""}</span></div>
        <div class="bv-flow">
            ${stage(cands.length, "후보 추출", 100, "#5b8def", "영상에서 자동으로 뽑은 목록")}
            ${stage(rev.length, "사람 검수", pct(rev.length, cands.length), "#e8b23b", `검수율 ${pct(rev.length, cands.length)}%`)}
            ${stage(conf.length, "꽁초·쓰레기 확정", pct(conf.length, cands.length), "#3fb8a0", rev.length ? `검수한 것 중 ${pct(conf.length, rev.length)}%` : "검수하면 집계됩니다")}
            <div class="bv-st"><div class="bv-n">${total}<small>점</small></div>
                <div class="bv-l">오염 감지 지수(시범)</div>
                <div><span class="bv-chip" style="background:${topC};">${topG}</span></div>
                <div class="bv-r">최근 ${POLLUTION.hours}시간 · 장치 중 가장 높은 등급</div></div>
        </div>`;

        let dev = state.heatDev;
        if (ids.indexOf(dev) < 0) {
            dev = ids.find((d) => recentOf(d).some(isConf)) || ids[0] || "";
        }
        const recent = recentOf(dev).filter(isConf);
        const gx = FRAME.gx, gy = FRAME.gy, cw = FRAME.w / gx, ch = FRAME.h / gy;
        const cnt = new Array(gx * gy).fill(0);
        const boxes = [];
        recent.forEach((e) => {
            const b = boxOf(e);
            if (!b) return;
            const x0 = Math.max(0, b.x), y0 = Math.max(0, b.y), x1 = Math.min(FRAME.w, b.x + b.w), y1 = Math.min(FRAME.h, b.y + b.h);
            if (x1 <= x0 || y1 <= y0) return;
            boxes.push([x0, y0, x1 - x0, y1 - y0]);
            for (let j = Math.floor(y0 / ch); j <= Math.floor((y1 - 1) / ch); j++)
                for (let i = Math.floor(x0 / cw); i <= Math.floor((x1 - 1) / cw); i++) cnt[j * gx + i]++;
        });
        const cellColor = (c) => (c >= 3 ? "#e5603d" : c === 2 ? "#e8b23b" : "#3fb8a0");
        let cells = "";
        for (let j = 0; j < gy; j++) for (let i = 0; i < gx; i++) {
            const c = cnt[j * gx + i];
            cells += `<rect x="${i * cw}" y="${j * ch}" width="${cw}" height="${ch}" fill="${c ? cellColor(c) : "none"}" fill-opacity="${c ? 0.82 : 0}" stroke="#1d2f49" stroke-width="2">` +
                `<title>${i + 1}열 ${j + 1}행 · 확정 상자 ${c}개</title></rect>` +
                (c ? `<text x="${i * cw + cw / 2}" y="${j * ch + ch / 2 + 15}" text-anchor="middle" font-size="46" font-weight="700" fill="#0e1a2b">${c}</text>` : "");
        }
        const boxSvg = boxes.map((b) => `<rect x="${b[0]}" y="${b[1]}" width="${b[2]}" height="${b[3]}" fill="rgba(255,255,255,0.10)" stroke="#ffffff" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join("");
        const cov = coverageOf(recent);
        const occ = cnt.filter((c) => c > 0).length;
        const score = scoreOf(dev);
        const [g, gc] = grade(score);
        const opts = ids.map((d) => {
            const info = deviceInfo.find((x) => x.device_id === d) || {};
            return `<option value="${esc(d)}" ${d === dev ? "selected" : ""}>${esc(d)}${info.location ? " · " + esc(info.location) : ""}</option>`;
        }).join("");
        const empty = recent.length === 0
            ? `<text x="${FRAME.w / 2}" y="${FRAME.h / 2}" text-anchor="middle" font-size="44" fill="#6f86a8">확정된 후보가 없습니다. 아래 표에서 후보를 꽁초/쓰레기로 확정하면 여기에 쌓입니다.</text>` : "";
        const heat = `
        <hr>
        <div class="bv-head"><h3>화면 격자 히트맵</h3>
            <select onchange="__buttSet('heatDev', this.value)" aria-label="장치 선택">${opts || "<option>장치 없음</option>"}</select>
            <span class="bv-sub">최근 ${POLLUTION.hours}시간 · 사람이 확정한 상자만 · 칸의 숫자는 겹친 상자 수</span></div>
        <div class="bv-heat">
            <div>
                <svg viewBox="0 0 ${FRAME.w} ${FRAME.h}" role="img" aria-label="확정된 후보 상자가 16x9 격자의 어느 칸에 쌓였는지 보여 주는 그림">${cells}${boxSvg}${empty}</svg>
                <div class="bv-legend"><span><i style="background:#3fb8a0;"></i>1개</span><span><i style="background:#e8b23b;"></i>2개</span><span><i style="background:#e5603d;"></i>3개 이상</span><span><i style="border:1px solid #fff;"></i>확정 상자</span></div>
            </div>
            <div>
                <div class="bv-stat"><b>${recent.length ? cov.used ? (cov.cover * 100).toFixed(2) + "%" : "—" : "0%"}</b><span>확정 면적 비율</span></div>
                <div class="bv-stat"><b>${occ} / ${gx * gy}칸</b><span>격자 점유 ${recent.length ? (cov.used ? Math.round(cov.grid * 100) + "%" : "—") : "0%"}</span></div>
                <div class="bv-stat"><b>${score}점 <span class="bv-chip" style="background:${gc};">${g}</span></b><span>오염 감지 지수(시범)</span></div>
                <div class="bv-stat"><b>${cov.used} / ${recent.length}개</b><span>위치를 읽은 상자 / 확정 건수</span></div>
            </div>
        </div>
        <div class="bv-foot">시범 지수이며 실제 오염 수준을 잰 값이 아닙니다. 사람이 확정한 건수만 반영하고, 치워진 쓰레기는 반영하지 않습니다. 화면 크기는 1920x1080으로 가정했고, 영상 한 편이 아니라 최근 ${POLLUTION.hours}시간 누적입니다. 장치끼리 오염 순위를 매기는 용도가 아닙니다.</div>`;
        el.innerHTML = flow + heat;
    }

    async function refreshNow() {
        if (refreshing) return;
        refreshing = true;
        state.updated = "갱신 중…";
        drawTools();
        let ok = false;
        try {
            await loadDeviceInfo(true);
            const ids = deviceInfo.map((d) => d.device_id);
            lastRows.forEach((e) => { if (e.device_id && ids.indexOf(e.device_id) < 0) ids.push(e.device_id); });
            const hours = state.period === "24h" ? 24 : state.period === "7d" ? 168 : 0;
            const since = hours ? new Date(Date.now() - hours * 3600 * 1000).toISOString().replace(/\.\d+Z$/, "Z") : "";
            const results = await Promise.allSettled(ids.map((dev) =>
                fetch(`${apiBase()}/api/devices/${encodeURIComponent(dev)}/events?limit=${FETCH_LIMIT}` +
                      (since ? `&since=${encodeURIComponent(since)}` : ""))
                    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
                    .then((b) => (b.events || []).map((e) => Object.assign({}, e, { device_id: dev })))));
            const all = [];
            results.forEach((r) => { if (r.status === "fulfilled") { ok = true; all.push.apply(all, r.value); } });
            if (ok) {
                lastRows = all.filter((e) => ALL_TYPES.indexOf(e.event_type) >= 0);
                await fetchReviews(Array.from(new Set(lastRows.map((e) => e.device_id))));
            }
            state.updated = ok ? new Date().toLocaleTimeString("ko-KR") : "갱신 실패(서버 확인)";
        } catch (_) {
            state.updated = "갱신 실패(서버 확인)";
        }
        refreshing = false;
        state.limit = PAGE;
        drawTools();
        drawTable();
        drawPollution();
    }

    window.__buttRefresh = refreshNow;

    window.__buttSet = function (key, value) {
        if (key === "auto") {
            state.auto = Number(value) || 0;
            if (timer) { clearInterval(timer); timer = null; }
            if (state.auto > 0) timer = setInterval(refreshNow, state.auto * 1000);
            drawTools();
            return;
        }
        state[key] = value;
        state.limit = PAGE;
        if (key === "period") { refreshNow(); return; }
        drawTools();
        drawTable();
    };
    window.__buttMore = function () {
        state.limit += PAGE;
        drawTable();
    };

    async function fetchReviews(devices) {
        for (const dev of devices) {
            try {
                const r = await fetch(`${apiBase()}/api/devices/${encodeURIComponent(dev)}/reviews`);
                if (!r.ok) continue;
                const body = await r.json();
                reviews[dev] = {};
                (body.reviews || []).forEach((x) => { reviews[dev][x.candidate_id] = x.label; });
            } catch (_) { }
        }
    }

    window.renderButtCandidates = function (events) {
        lastRows = (events || []).filter((e) => ALL_TYPES.indexOf(e.event_type) >= 0);
        drawTools();
        drawTable();
        drawPollution();
        refreshNow();
    };

    document.addEventListener("change", async (ev) => {
        const sel = ev.target;
        if (!sel || !sel.classList || !sel.classList.contains("butt-review")) return;
        const dev = sel.dataset.dev, cid = sel.dataset.cid, label = sel.value;
        const note = sel.parentElement.querySelector(".butt-saved");
        
        const oldLabel = (reviews[dev] || {})[cid] || "";
        const oldScore = POLLUTION.weight[oldLabel] || 0;
        const newScore = POLLUTION.weight[label] || 0;
        const scoreDiff = newScore - oldScore;

        try {
            const r = await fetch(`${apiBase()}/api/devices/${encodeURIComponent(dev)}/reviews/${encodeURIComponent(cid)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ label }),
            });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            (reviews[dev] = reviews[dev] || {})[cid] = label;
            if (note) note.textContent = "저장됨";
            drawTools();
            drawPollution();

            if (scoreDiff !== 0) {
                const event = lastRows.find((e) => e.related_detection_id === cid) || {};
                fetch(`${apiBase()}/api/stats/pollution`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        device_id: dev,
                        timestamp: event.timestamp || new Date().toISOString(),
                        score_diff: scoreDiff
                    })
                });
            }
        } catch (err) {
            if (note) { note.style.color = "#c0392b"; note.textContent = "저장 실패"; }
            alert("검수 결과 저장 실패: " + err.message);
        }
    });

    // ==========================================
    // 💡 2. 새 창(새 탭)에서 stats.html을 띄우는 전용 코드
    // ==========================================
    const btnShowStats = document.getElementById("btn-show-pollution-stats");
    if (btnShowStats) {
        btnShowStats.addEventListener("click", () => {
            let ids = deviceInfo.map(d => d.device_id).filter(id => !isLocalServer(id));
            lastRows.forEach((e) => { 
                if (e.device_id && ids.indexOf(e.device_id) < 0 && !isLocalServer(e.device_id)) {
                    ids.push(e.device_id); 
                }
            });

            const idsParam = encodeURIComponent(ids.join(","));
            window.open(`stats.html?devices=${idsParam}`, "_blank");
        });
    }
})();