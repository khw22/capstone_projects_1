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
    const state = { type: "", status: "", sort: "new", period: "", auto: 0, updated: "", limit: PAGE };
    const FETCH_LIMIT = 1000;                              // 서버가 허용하는 최대값(장치당)
    let timer = null, refreshing = false;
    let lastRows = [];

    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const apiBase = () => (window.API_BASE || "http://localhost:8000").replace(/\/+$/, "");
    const isAuto = (e) => AUTO_TYPES.indexOf(e.event_type) >= 0;
    const labelOf = (e) => (reviews[e.device_id] || {})[e.related_detection_id] || "";
    const areaOf = (e) => { const m = /크기\s*(\d+)x(\d+)px/.exec(e.message || ""); return m ? Number(m[1]) * Number(m[2]) : 0; };

    // snapshot_ref 가 없어도 message 의 snap=... 토큰으로 찾는다.
    function snapshotId(e) {
        if (e.snapshot_ref) return String(e.snapshot_ref);
        const m = /snap=(snap_[0-9a-zA-Z]+)/.exec(e.message || "");
        return m ? m[1] : null;
    }
    const detailText = (e) => String(e.message || "").replace(/\s*\|\s*snap=\S+/, "").replace(/\s*\|\s*/g, " · ");

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
        </div>
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;font-size:13px;">
            <button onclick="__buttRefresh()" style="padding:5px 12px;cursor:pointer;">↻ 새로고침</button>
            <span>자동 갱신</span>${sel("auto", [["0", "끔"], ["30", "30초"], ["60", "60초"]])}
            <span style="color:#6b7a90;">마지막 갱신 ${esc(state.updated || "-")} · 불러온 이벤트는 장치당 최대 ${FETCH_LIMIT}건</span>
        </div>`;
    }

    function visibleRows() {
        let rows = lastRows.filter((e) => {
            if (state.type === "auto" && !isAuto(e)) return false;
            if (state.type === "candidate" && isAuto(e)) return false;
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
                <td>${thumb}</td>
                <td>${reviewCell(e)}</td>
            </tr>`;
        }).join("") + (rows.length > shown.length
            ? `<tr><td colspan="5" style="text-align:center;"><button onclick="__buttMore()" style="padding:6px 14px;cursor:pointer;">더 보기 (남은 ${rows.length - shown.length}건)</button></td></tr>`
            : "");
    }


    // ---------- 오염 감지 지수(시범) ----------
    // 최근 N시간에 '사람이 확인한' 꽁초/쓰레기 감지 건수에 가중치를 곱해 더한 값. 실제 오염 수준을 잰 값이 아니다.
    const POLLUTION = { hours: 24, weight: { cigarette_butt: 1, other_litter: 3 }, warn: 1, high: 5 };
    let deviceInfo = [], deviceInfoAt = 0;

    async function loadDeviceInfo(force) {
        if (!force && deviceInfo.length && Date.now() - deviceInfoAt < 30000) return;
        try {
            const r = await fetch(`${apiBase()}/api/devices`);
            if (r.ok) { deviceInfo = (await r.json()).devices || []; deviceInfoAt = Date.now(); }
        } catch (_) { /* 장치 정보를 못 받으면 이벤트가 있는 장치만 표시 */ }
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
        if (sec && sec.querySelector) {                      // 안내 문구를 시범 지수 설명으로 바꾼다
            const badge = sec.querySelector("h2 .badge");
            if (badge) { badge.textContent = "시범"; }
            const note = sec.querySelector(".note");
            if (note) note.textContent = `※ 최근 ${POLLUTION.hours}시간에 사람이 확인한 꽁초(${POLLUTION.weight.cigarette_butt}점)와 쓰레기(${POLLUTION.weight.other_litter}점)의 감지 건수를 합한 '오염 감지 지수'입니다. 실제 오염 수준을 잰 값이 아니라 감지 건수 기준이며, 치워진 쓰레기는 반영하지 않습니다. 점수 가중치와 등급 기준(주의 ${POLLUTION.warn}점, 높음 ${POLLUTION.high}점 이상)은 임시 값입니다.`;
        }
        const tr = document.querySelector("#pollution-table thead tr");
        if (tr && tr.children && tr.children.length === 5) tr.insertAdjacentHTML("beforeend", "<th>검수 대기</th>");

        const since = Date.now() - POLLUTION.hours * 3600 * 1000;
        const ids = deviceInfo.map((d) => d.device_id);
        lastRows.forEach((e) => { if (e.device_id && ids.indexOf(e.device_id) < 0) ids.push(e.device_id); });
        if (!ids.length) {
            tbody.innerHTML = `<tr><td class="muted" colspan="6">장치가 없습니다.</td></tr>`;
            return;
        }
        const time = (t) => (typeof fmtTime === "function" ? fmtTime(t) : t);
        const now = new Date().toISOString();
        tbody.innerHTML = ids.map((dev) => {
            const info = deviceInfo.find((d) => d.device_id === dev) || {};
            const recent = lastRows.filter((e) => e.device_id === dev && new Date(e.timestamp).getTime() >= since);
            const nButt = recent.filter((e) => labelOf(e) === "cigarette_butt").length;
            const nLit = recent.filter((e) => labelOf(e) === "other_litter").length;
            const pending = recent.filter((e) => !labelOf(e)).length;
            const score = nButt * POLLUTION.weight.cigarette_butt + nLit * POLLUTION.weight.other_litter;
            const [g, color] = grade(score);
            return `<tr>
                <td>${esc(dev)}</td>
                <td>${esc(info.location || "-")}</td>
                <td>${score}점 <small style="color:#6b7a90;">(꽁초 ${nButt}, 쓰레기 ${nLit})</small></td>
                <td><span style="background:${color};color:#fff;border-radius:10px;padding:2px 10px;font-size:12px;">${g}</span></td>
                <td>${esc(time(now))}</td>
                <td>${pending}건</td>
            </tr>`;
        }).join("");
    }

    // 서버에서 후보·자동 감지 이벤트와 검수 결과를 다시 불러온다(장치당 최대 1000건, 기간 선택 시 since 적용)
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
        if (key === "auto") {                                // 자동 갱신: 기본은 끔(팀이 자동 새로고침을 일부러 뺐음)
            state.auto = Number(value) || 0;
            if (timer) { clearInterval(timer); timer = null; }
            if (state.auto > 0) timer = setInterval(refreshNow, state.auto * 1000);
            drawTools();
            return;
        }
        state[key] = value;
        state.limit = PAGE;
        if (key === "period") { refreshNow(); return; }       // 기간은 서버에서 다시 불러온다
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
            } catch (_) { /* 서버가 구버전이면 검수 없이 목록만 표시 */ }
        }
    }

    window.renderButtCandidates = function (events) {
        lastRows = (events || []).filter((e) => ALL_TYPES.indexOf(e.event_type) >= 0);
        drawTools();
        drawTable();
        drawPollution();
        refreshNow();                                         // script.js 는 장치당 100건만 주므로 1000건까지 다시 불러온다
    };

    // 라벨 선택 -> 서버에 저장 (이벤트 위임: 표가 다시 그려져도 동작)
    document.addEventListener("change", async (ev) => {
        const sel = ev.target;
        if (!sel || !sel.classList || !sel.classList.contains("butt-review")) return;
        const dev = sel.dataset.dev, cid = sel.dataset.cid, label = sel.value;
        const note = sel.parentElement.querySelector(".butt-saved");
        try {
            const r = await fetch(`${apiBase()}/api/devices/${encodeURIComponent(dev)}/reviews/${encodeURIComponent(cid)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ label }),
            });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            (reviews[dev] = reviews[dev] || {})[cid] = label;
            if (note) note.textContent = "저장됨";
            drawTools();                                  // 표는 다시 그리지 않아 '저장됨' 표시가 유지된다
            drawPollution();
        } catch (err) {
            if (note) { note.style.color = "#c0392b"; note.textContent = "저장 실패"; }
            alert("검수 결과 저장 실패: " + err.message);
        }
    });
})();
