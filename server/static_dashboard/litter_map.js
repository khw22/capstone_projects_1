/* litter_map.js — 환경지기 지도 애드온 (무단투기 후보/검수/장면)
 *
 * map.html 을 고치지 않고, </body> 바로 앞에 아래 한 줄만 추가해서 쓴다.
 *     <script src="litter_map.js"></script>
 * map.html 의 전역 변수(cameras, labels)만 읽는다. 화재 코드와 같은 요소(#toast-container, 사이렌)는 건드리지 않는다.
 *
 * 하는 일
 *  1) CCTV 라벨 아래에 "🗑 N점" 배지(사람이 확정한 꽁초 1점·쓰레기 3점, 최근 24시간, 시범 지수)
 *  2) 큰 후보(상자 가로x세로 >= MIN_LIGHT_AREA px)가 검수를 기다리면 CCTV 라벨이 주황색으로 깜빡인다('검수 필요 후보'이지 투기 확정이 아님)
 *  3) 새로 '쓰레기/꽁초'로 확정되거나 큰 후보가 새로 생기면 화면 아래 가운데에 알림(소리 없음)
 *  4) 배지를 누르면 장면(GIF/사진) 창이 열리고, 거기서 꽁초/쓰레기/쓰레기 아님/불확실을 바로 저장한다(대시보드와 같은 검수 API)
 *     '작은 후보도 보기'를 켜면 불빛이 켜지지 않는 작은 후보(꽁초일 수 있음)도 같은 창에서 검수할 수 있다.
 *  5) 만안구 경계 파일이 있으면 지도를 만안구로 제한하고 바깥을 흐리게 덮는다(파일이 없으면 지도는 그대로).
 * 한계: 투기하는 순간을 찾은 것이 아니라 변화가 처음 보인 시각 전후를 자동으로 고른 장면이다.
 *       얼굴이 보일 수 있으니 외부 공개 전에는 make_scene.py --mosaic 을 쓴다.
 *       점수는 실제 오염 수준이 아니라 사람이 확정한 건수 기반 시범 지수다.
 */
(function () {
    "use strict";
    var VERSION = "5";   // 화면 구석에 작게 보인다: 새 파일이 적용됐는지 확인용
    var POLL_MS = 6000;
    var TOAST_MS = 12000;
    var W_BUTT = 1, W_LITTER = 3, WARN = 1, HIGH = 5;
    var TYPES = ["persistent_change_candidate", "cigarette_butt_detected", "litter_detected"];
    var LABEL_TEXT = { cigarette_butt: "꽁초", other_litter: "쓰레기(꽁초 제외)", not_litter: "쓰레기 아님", uncertain: "불확실" };
    var MIN_LIGHT_AREA = 500;   // 이 값 이상인 후보만 불빛. 같은 20영상으로 정한 임시 기준(새 영상 미검증), 상자(가로x세로) 기준이라 평가에 쓴 마스크 면적보다 후하다
    var BOUNDARY_FILES = ["skorea_municipalities_geo_simple.json", "manan.geojson", "skorea_municipalities_geo.json"];

    var known = {};          // known[dev][cid] = true  (이미 알림을 낸/기준선으로 잡은 확정 건)
    var baselineDone = {};   // baselineDone[dev] = true (첫 조회는 알림 없이 기준선만 잡는다)
    var rowsByDev = {};      // rowsByDev[dev] = [확정 건 목록]
    var candsByDev = {};     // candsByDev[dev] = [검수 안 된 후보 목록(큰 것 먼저)]
    var bigKnown = {};       // 알림을 이미 낸 큰 후보
    var queue = [], qi = 0, showSmall = false, camsByDev = {};
    var badges = {};

    var embedded = false;
    try { embedded = window.top !== window.self; } catch (e) { embedded = true; }
    // 분할 화면(control.html)이면 지도에서 고른 CCTV 를 옆 대시보드에 알린다
    function notifyParent(id) {
        try { if (embedded) window.parent.postMessage({ type: "litter-focus", id: id }, location.origin); } catch (e) { /* 무시 */ }
    }
    // 대시보드 표에서 CCTV 줄을 누르면(control.html 이 전달) 지도가 그 CCTV 위치로 부드럽게 이동하고 라벨이 잠깐 강조된다
    window.addEventListener("message", function (ev) {
        if (ev.origin !== location.origin || !ev.data || ev.data.type !== "litter-pan") return;
        try {
            var id = String(ev.data.id || "");
            var list = (typeof cameras !== "undefined" && cameras) ? cameras : [];
            var cam = list.filter(function (c) { return c.id === id; })[0];
            if (!cam || typeof map === "undefined" || !map) return;
            map.panTo(new kakao.maps.LatLng(cam.lat, cam.lng));
            // 화재/오염 영상 창이 이미 열려 있으면 새로 고른 CCTV 의 영상으로 바꿔 준다(닫혀 있으면 열지 않는다)
            var vm = document.getElementById("videoModal");
            var vopen = vm && getComputedStyle(vm).display !== "none";
            if (window.console) console.log("[litter_map] 대시보드에서 " + id + " 선택 (영상 창 " + (vopen ? "열림 -> 전환" : "닫힘") + ")");
            var lm = document.getElementById("litterModal");
            var lopen = lm && getComputedStyle(lm).display !== "none";
            if (vopen && typeof openCamera === "function") { quiet = true; try { openCamera(cam); } finally { quiet = false; } }
            else if (lopen) { openCam(id); }                       // 장면 탭이 열려 있었다면 새로 고른 CCTV 의 장면 탭으로 바꾼다
            var el = (typeof labels !== "undefined") ? labels[id] : null;
            if (el) { el.style.outline = "3px solid #f5a35c"; setTimeout(function () { el.style.outline = ""; }, 2500); }
        } catch (e) { console.warn("[litter_map] 지도 이동 실패", e); }
    });
    function apiBase() { return String(window.API_BASE || "").replace(/\/+$/, ""); }
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function tok(msg, name) {
        var m = new RegExp(name + "=(snap_[0-9a-zA-Z]+)").exec(msg || "");
        return m ? m[1] : "";
    }
    function snapUrl(id) { return apiBase() + "/api/snapshots/" + encodeURIComponent(id); }
    function sizeOf(msg) {
        var m = /크기\s*(\d+)\s*x\s*(\d+)\s*px/.exec(msg || "");
        return m ? m[1] + "x" + m[2] + "px" : "";
    }
    function areaOfMsg(msg) {
        var m = /크기\s*(\d+)\s*x\s*(\d+)\s*px/.exec(msg || "");
        return m ? Number(m[1]) * Number(m[2]) : 0;
    }
    function detail(msg) {
        return String(msg || "").replace(/\s*\|\s*snap=\S+/, "").replace(/\s*\|\s*scene=\S+/, "").replace(/\s*\|\s*/g, " · ");
    }
    function fmt(ts) {
        var d = new Date(ts);
        if (isNaN(d.getTime())) return String(ts || "");
        return d.toLocaleString("ko-KR", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    }
    function grade(score) {
        return score >= HIGH ? { t: "높음", c: "#c0392b" } : score >= WARN ? { t: "주의", c: "#e67e22" } : { t: "양호", c: "#2f6f62" };
    }

    // ---------- 스타일/요소 ----------
    function injectUi() {
        if (document.getElementById("litter-panel")) return;
        var st = document.createElement("style");
        st.textContent =
            ".litter-badge{display:block;margin-top:4px;padding:2px 6px;border-radius:4px;font:bold 11px 'Malgun Gothic',sans-serif;color:#fff;cursor:pointer;background:#2f6f62}" +
            "#litter-panel{position:fixed;top:20px;right:20px;width:300px;max-height:45vh;background:rgba(255,255,255,.96);border:2px solid #2f6f62;border-radius:10px;z-index:60;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 6px 15px rgba(0,0,0,.3);font-family:'Malgun Gothic',sans-serif}" +
            "#litter-panel .lp-h{background:#2f6f62;color:#fff;padding:9px 12px;font-size:14px;font-weight:bold;display:flex;justify-content:space-between;align-items:center}" +
            "#litter-panel .lp-h small{font-weight:normal;font-size:11px;opacity:.85}" +
            "#litter-list{padding:8px;overflow-y:auto;display:flex;flex-direction:column;gap:6px}" +
            ".litter-item{display:flex;gap:8px;align-items:center;padding:6px;border-left:4px solid #2f6f62;background:#eef5f3;border-radius:4px;cursor:pointer;font-size:12px;color:#243;}" +
            ".litter-item:hover{background:#dcebe7}" +
            ".litter-item img{width:64px;height:40px;object-fit:cover;border-radius:3px;background:#222;flex-shrink:0}" +
            ".litter-item b{display:block;font-size:12px}" +
            ".litter-empty{color:#667;font-size:12px;padding:6px}" +
            "#litter-toasts{position:fixed;bottom:30px;left:50%;transform:translateX(-50%);z-index:210;display:flex;flex-direction:column;gap:8px}" +
            ".litter-toast{background:#e67e22;color:#fff;padding:12px 16px;border-radius:8px;min-width:320px;max-width:420px;font:bold 14px 'Malgun Gothic',sans-serif;box-shadow:0 6px 18px rgba(0,0,0,.35);display:flex;gap:12px;justify-content:space-between;align-items:center}" +
            ".litter-toast span.s{display:block;font-weight:normal;font-size:12px;margin-top:3px;color:#fff3e0}" +
            ".litter-toast button{background:rgba(0,0,0,.25);color:#fff;border:0;border-radius:5px;padding:6px 10px;font-weight:bold;cursor:pointer;margin-left:4px}" +
            "#litterModal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:220;justify-content:center;align-items:center}" +
            "#litterModal .lm{background:#fff;border-radius:8px;padding:16px;width:760px;max-width:92%;max-height:92vh;overflow:auto;font-family:'Malgun Gothic',sans-serif;box-shadow:0 4px 15px rgba(0,0,0,.4)}" +
            "#litterModal img.big{width:100%;max-height:60vh;object-fit:contain;background:#000;border-radius:4px}" +
            "#litterModal .note{font-size:12px;color:#555;margin:8px 0}" +
            "#litterModal button{padding:8px 18px;border:0;border-radius:5px;background:#333;color:#fff;font-weight:bold;cursor:pointer}" +
            "#litterModal .lm-rv{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}" +
            "#litterModal .lm-rv button{background:#2f6f62}#litterModal .lm-rv button.na{background:#7f8c8d}#litterModal .lm-rv button.on{outline:3px solid #f1c40f}" +
            "#litterModal .lm-nav{display:flex;gap:10px;align-items:center;font-size:13px;margin:8px 0}" +
            ".lm-tabs{display:flex;gap:6px;align-items:center;margin:0 0 10px;font-family:'Malgun Gothic',sans-serif}" +
            ".lm-tabs button{padding:6px 14px;border:0;border-radius:999px;background:#e3e8ef;color:#333;font-weight:bold;cursor:pointer;font-size:13px}" +
            ".lm-tabs button.on{background:#2f6f62;color:#fff}" +
            "#litterModal .lm-tabs button{background:#e3e8ef;color:#333;padding:6px 14px}#litterModal .lm-tabs button.on{background:#2f6f62;color:#fff}" +
            ".lm-tabs .tabnote{margin-left:auto;font-size:11px;color:#777;font-weight:normal}" +
            "#litterModal .lm-nav button{padding:5px 12px;background:#555}" +
            ".cam-label.litter-alert{border-color:#fff;animation:litterFlash 1s infinite}" +
            "@keyframes litterFlash{0%,100%{background:#e67e22;box-shadow:0 0 6px rgba(230,126,34,.6)}50%{background:#f5a35c;box-shadow:0 0 18px rgba(230,126,34,1)}}";
        document.head.appendChild(st);

        var panel = document.createElement("div");
        panel.id = "litter-panel";
        panel.innerHTML = '<div class="lp-h"><span>🗑 무단투기 확정 현황</span><small>시범 지수</small></div><div id="litter-list"></div>';
        document.body.appendChild(panel);

        var strip = document.createElement("div");
        strip.id = "litter-strip";
        strip.style.cssText = "position:fixed;top:" + (embedded ? 12 : 56) + "px;left:12px;z-index:60;display:flex;gap:10px;align-items:center;background:rgba(15,27,42,.92);color:#fff;padding:7px 14px;border-radius:999px;font:bold 12px 'Malgun Gothic',sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.35)";
        document.body.appendChild(strip);

        var tc = document.createElement("div");
        tc.id = "litter-toasts";
        document.body.appendChild(tc);

        var modal = document.createElement("div");
        modal.id = "litterModal";
        modal.innerHTML = '<div class="lm">' + tabsHtml("scene") + '<h3 id="lm-title" style="margin:0 0 8px"></h3>' +
            '<img class="big" id="lm-img" alt="장면 로딩 중">' +
            '<div class="lm-nav"><button id="lm-prev">◀ 이전</button><span id="lm-pos"></span><button id="lm-next">다음 ▶</button>' +
            '<label style="margin-left:auto;font-size:12px"><input type="checkbox" id="lm-small"> 작은 후보도 보기(꽁초일 수 있음)</label></div>' +
            '<div class="note" id="lm-info"></div>' +
            '<div class="lm-rv" id="lm-rv"></div><div class="note" id="lm-save"></div>' +
            '<div class="note">※ 변화가 처음 보인 시각 전후를 자동으로 고른 장면이며, 투기하는 순간을 확정한 것이 아닙니다. 실시간 영상이 아니고, 시연 중에는 CCTV에 배정한 영상(시연용 배정)일 수 있습니다. 얼굴이 보일 수 있어 외부 공개 전에는 모자이크가 필요합니다.</div>' +
            '<button id="lm-close">닫기</button></div>';
        modal.addEventListener("click", function (ev) { if (ev.target === modal) closeLitter(); });
        document.body.appendChild(modal);
        document.getElementById("lm-close").addEventListener("click", closeLitter);
        bindTabs(modal);
        document.getElementById("lm-prev").addEventListener("click", function () { go(-1); });
        document.getElementById("lm-next").addEventListener("click", function () { go(1); });
        document.getElementById("lm-small").addEventListener("change", function (ev) { showSmall = ev.target.checked; rebuildQueue(); });
    }
    // ---------- 영상 | 무단투기 장면 탭 (화재 영상 창과 장면 창을 한 창처럼 오간다) ----------
    var lastCamId = "";
    function tabsHtml(active) {
        return '<div class="lm-tabs"><button type="button" data-tab="video"' + (active === "video" ? ' class="on"' : "") + '>📹 영상</button>' +
            '<button type="button" data-tab="scene"' + (active === "scene" ? ' class="on"' : "") + '>🗑 무단투기 장면</button>' +
            '<span class="tabnote">' + (active === "scene" ? "후보 장면은 실시간 영상이 아닙니다" : "영상이 안 나오면 장면 탭을 보세요") + '</span></div>';
    }
    function camObj(id) {
        var list = (typeof cameras !== "undefined" && cameras) ? cameras : [];
        return list.filter(function (c) { return c.id === id; })[0];
    }
    function bigCount(id) {
        var n = (rowsByDev[id] || []).length;
        (candsByDev[id] || []).forEach(function (r) { if (r.area >= MIN_LIGHT_AREA) n++; });
        return n;
    }
    var skipAuto = false;
    function bindTabs(root) {
        Array.prototype.forEach.call(root.querySelectorAll(".lm-tabs button"), function (b) {
            b.addEventListener("click", function () {
                var id = lastCamId || curDev;
                if (!id) return;
                if (b.getAttribute("data-tab") === "scene") {
                    if (typeof closeModal === "function") closeModal();
                    openCam(id);
                } else {
                    closeLitter();
                    var cam = camObj(id);
                    if (cam && typeof openCamera === "function") { skipAuto = true; openCamera(cam); }
                }
            });
        });
    }
    var quiet = false;                       // 대시보드 표에서 고른 것이면 대시보드를 다시 움직이지 않는다
    function hookOpenCamera() {
        if (window.__lmHooked) return;
        if (typeof window.openCamera !== "function") { setTimeout(hookOpenCamera, 500); return; }
        var orig = window.openCamera;
        window.openCamera = function (cam) {
            var auto = !skipAuto; skipAuto = false;
            lastCamId = cam.id;
            closeLitter();
            orig(cam);
            if (!quiet) notifyParent(cam.id);                  // 지도에서 CCTV 를 고르면 대시보드가 그 장치의 오염도로 이동
            var vm = document.getElementById("videoModal");
            if (vm && !vm.querySelector(".lm-tabs")) {
                var h = document.getElementById("modalTitle");
                var wrap = document.createElement("div");
                wrap.innerHTML = tabsHtml("video");
                if (h && h.parentNode) h.parentNode.insertBefore(wrap.firstChild, h);
                bindTabs(vm);
            }
            var v = document.getElementById("videoStream");
            if (v) {
                v.onerror = function () {                      // 영상이 안 나오는데 볼 후보 장면이 있으면 장면 탭으로 넘어간다
                    var open = vm && vm.style.display !== "none" && getComputedStyle(vm).display !== "none";
                    if (!auto || !open || !v.getAttribute("src") || bigCount(cam.id) === 0) return;
                    if (typeof closeModal === "function") closeModal();
                    openCam(cam.id);
                };
            }
        };
        window.__lmHooked = true;
    }
    function closeLitter() {
        var m = document.getElementById("litterModal");
        if (m) m.style.display = "none";
        var i = document.getElementById("lm-img");
        if (i) i.removeAttribute("src");
    }
    var curDev = "";
    function rebuildQueue(keepCid) {
        var all = (candsByDev[curDev] || []).slice();
        // 큰 후보 먼저(큰 것 -> 작은 것), 작은 후보는 '작은 후보도 보기'를 켠 경우에만
        queue = all.filter(function (r) { return showSmall || r.area >= MIN_LIGHT_AREA; });
        (rowsByDev[curDev] || []).forEach(function (r) { if (r._view) queue.unshift(r); });
        qi = 0;
        if (keepCid) queue.forEach(function (r, k) { if (r.cid === keepCid) qi = k; });
        drawModal();
    }
    function go(d) {
        if (!queue.length) return;
        qi = (qi + d + queue.length) % queue.length;
        drawModal();
    }
    function drawModal() {
        var m = document.getElementById("litterModal");
        if (!m) return;
        var row = queue[qi];
        var cam = camsByDev[curDev] || {};
        document.getElementById("lm-small").checked = showSmall;
        document.getElementById("lm-pos").textContent = queue.length ? (qi + 1) + " / " + queue.length : "0 / 0";
        document.getElementById("lm-save").textContent = "";
        var img = document.getElementById("lm-img"), rv = document.getElementById("lm-rv");
        rv.innerHTML = "";
        if (!row) {
            document.getElementById("lm-title").textContent = "🗑 " + curDev + (cam.name ? " (" + cam.name + ")" : "");
            img.removeAttribute("src"); img.style.display = "none";
            document.getElementById("lm-info").textContent = showSmall ? "검수 대기 중인 후보가 없습니다." : "검수 대기 중인 큰 후보가 없습니다. 작은 후보는 위 체크박스를 켜면 볼 수 있습니다.";
            m.style.display = "flex";
            return;
        }
        img.style.display = "";
        var sid = row.scene || row.snap;
        document.getElementById("lm-title").textContent = "🗑 " + row.dev + (row.name ? " (" + row.name + ")" : "") + " · " + (row.label ? LABEL_TEXT[row.label] || row.label : "검수 대기 후보");
        if (sid) img.src = snapUrl(sid); else img.removeAttribute("src");
        document.getElementById("lm-info").textContent =
            fmt(row.ts) + " · " + (row.size ? "크기 " + row.size + " · " : "") + (row.area && row.area < MIN_LIGHT_AREA ? "작은 후보(불빛 기준 미만) · " : "") +
            (row.scene ? "첫 변화 전후 장면" : (row.snap ? "후보 패널 사진(장면 사진 없음)" : "사진 없음")) +
            (row.label && (row.by || row.at) ? " · 검수: " + (row.by || "기록 없음") + (row.at ? " " + fmt(row.at) : "") : "");
        [["cigarette_butt", "꽁초"], ["other_litter", "쓰레기(꽁초 제외)"], ["not_litter", "쓰레기 아님"], ["uncertain", "불확실"]].forEach(function (x) {
            var b = document.createElement("button");
            b.textContent = x[1];
            if (x[0] === "not_litter" || x[0] === "uncertain") b.className = "na";
            if (row.label === x[0]) b.className += " on";
            b.addEventListener("click", function () { saveReview(row, x[0]); });
            rv.appendChild(b);
        });
        m.style.display = "flex";
    }
    function saveReview(row, label) {
        var note = document.getElementById("lm-save");
        note.style.color = "#555"; note.textContent = "저장 중…";
        fetch(apiBase() + "/api/devices/" + encodeURIComponent(row.dev) + "/reviews/" + encodeURIComponent(row.cid), {
            method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: label })
        }).then(function (r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            note.style.color = "#2f6f62"; note.textContent = "저장됨: " + (LABEL_TEXT[label] || label);
            row.label = label;
            var cam = camsByDev[row.dev];
            return cam ? pollCam(cam).then(function () { drawList(); rebuildQueue(); document.getElementById("lm-save").textContent = "저장됨: " + (LABEL_TEXT[label] || label); }) : null;
        }).catch(function (err) { note.style.color = "#c0392b"; note.textContent = "저장 실패: " + err.message; });
    }
    function openLitter(row, viewOnly) {
        curDev = row.dev; lastCamId = row.dev;
        if (row.label) {            // 이미 검수한 항목을 목록에서 눌러 연 경우: 그 항목을 맨 앞에 두고 다시 검수할 수 있다
            var rows = rowsByDev[curDev] || [];
            rows.forEach(function (r) { r._view = (r === row); });
        }
        rebuildQueue();
        if (row.label) { rows = rowsByDev[curDev] || []; rows.forEach(function (r) { delete r._view; }); }
    }
    function openCam(devId) {
        curDev = devId; lastCamId = devId;
        (rowsByDev[devId] || []).forEach(function (r) { delete r._view; });
        rebuildQueue();
    }
    window.__litterOpen = openLitter;

    // ---------- 알림 ----------
    function toast(row, score, pendingN) {
        var c = document.getElementById("litter-toasts");
        if (!c) return;
        var g = grade(score);
        var el = document.createElement("div");
        el.className = "litter-toast";
        el.innerHTML = (pendingN ?
            '<div>🗑 [' + esc(row.dev) + '] ' + esc(row.name ? row.name + " 구역 " : "") + '큰 후보 ' + pendingN + '건 검수 필요' +
            '<span class="s">쓰레기 투기가 확정된 것이 아니라 사람이 확인해야 하는 후보입니다</span></div>' :
            '<div>🗑 [' + esc(row.dev) + '] ' + esc(row.name ? row.name + " 구역 " : "") + esc(LABEL_TEXT[row.label] || "쓰레기") + ' 확정' +
            '<span class="s">현재 ' + score + '점 · ' + g.t + ' (사람이 확정한 건수 기준, 시범)</span></div>') +
            '<div><button data-a="v">장면 보기</button><button data-a="x">닫기</button></div>';
        el.querySelector('[data-a="v"]').addEventListener("click", function () { if (pendingN) openCam(row.dev); else openLitter(row); el.remove(); });
        el.querySelector('[data-a="x"]').addEventListener("click", function () { el.remove(); });
        c.appendChild(el);
        setTimeout(function () { if (el.parentNode) el.remove(); }, TOAST_MS);
    }

    // ---------- 그리기 ----------
    function scoreOf(rows) {
        var s = 0;
        rows.forEach(function (r) { s += r.label === "cigarette_butt" ? W_BUTT : W_LITTER; });
        return s;
    }
    function drawBadge(cam, pending) {
        var el = (typeof labels !== "undefined") ? labels[cam.id] : null;
        if (!el) return;
        var b = badges[cam.id];
        if (!b) {
            b = document.createElement("span");
            b.className = "litter-badge";
            el.addEventListener("click", function () { notifyParent(cam.id); });     // 라벨을 눌러도 옆 대시보드가 그 장치로 이동
            b.addEventListener("click", function (ev) {
                ev.stopPropagation();                      // 화재 영상 창(openCamera)이 같이 열리지 않게 한다
                notifyParent(cam.id);
                openCam(cam.id);
            });
            el.appendChild(b);
            badges[cam.id] = b;
        }
        var rows = rowsByDev[cam.id] || [];
        var score = scoreOf(rows), g = grade(score);
        b.style.background = g.c;
        var big = (candsByDev[cam.id] || []).filter(function (r) { return r.area >= MIN_LIGHT_AREA; }).length;
        b.textContent = "🗑 " + score + "점 " + g.t + (big ? " · 검수 필요 " + big : "") + (pending - big > 0 ? " · 작은 후보 " + (pending - big) : "");
        el.classList.toggle("litter-alert", big > 0);
        b.title = "최근 24시간 사람이 확정한 꽁초(1점)·쓰레기(3점) 합. 시범 지수이며 실제 오염 수준이 아닙니다.";
    }
    function drawList() {
        var box = document.getElementById("litter-list");
        if (!box) return;
        var all = [];
        Object.keys(rowsByDev).forEach(function (d) { all = all.concat(rowsByDev[d]); });
        all.sort(function (a, b) { return a.ts < b.ts ? 1 : -1; });
        if (!all.length) { box.innerHTML = '<div class="litter-empty">최근 24시간 확정된 쓰레기·꽁초가 없습니다.</div>'; return; }
        box.innerHTML = "";
        all.slice(0, 20).forEach(function (r) {
            var it = document.createElement("div");
            it.className = "litter-item";
            var sid = r.scene || r.snap;
            it.innerHTML = (sid ? '<img src="' + esc(snapUrl(sid)) + '" alt="">' : "") +
                '<div><b>' + esc(r.dev) + ' · ' + esc(LABEL_TEXT[r.label] || "확정") + '</b>' + esc(fmt(r.ts)) + (r.size ? " · " + esc(r.size) : "") + '</div>';
            it.addEventListener("click", function () { openLitter(r); });
            box.appendChild(it);
        });
    }

    // 지도 위 상태 요약 띠: 한눈에 보는 숫자(화재 표시는 map.html 이 라벨에 붙이는 'fire' 클래스를 센다)
    function drawStrip() {
        var el = document.getElementById("litter-strip");
        if (!el) return;
        var cams = (typeof cameras !== "undefined" && cameras) ? cameras : [];
        var fire = document.querySelectorAll(".cam-label.fire").length;
        var need = 0, score = 0;
        cams.forEach(function (c) {
            need += (candsByDev[c.id] || []).filter(function (r) { return r.area >= MIN_LIGHT_AREA; }).length;
            score += scoreOf(rowsByDev[c.id] || []);
        });
        var chip = function (txt, bg) { return '<span style="background:' + bg + ';padding:2px 9px;border-radius:999px">' + txt + '</span>'; };
        el.innerHTML = chip("📹 CCTV " + cams.length + "대", "#34495e") +
            chip("🔥 화재 " + fire, fire ? "#c0392b" : "#2f6f62") +
            chip("🗑 검수 필요 " + need, need ? "#e67e22" : "#2f6f62") +
            chip("확정 점수 합 " + score + "점", "#34495e") +
            '<span style="font-size:10px;opacity:.5;margin-left:6px">v' + VERSION + '</span>';
    }

    // ---------- 조회 ----------
    function pollCam(cam) {
        var since = new Date(Date.now() - 24 * 3600 * 1000).toISOString().replace(/\.\d+Z$/, "Z");
        var dev = encodeURIComponent(cam.id);
        return Promise.all([
            fetch(apiBase() + "/api/devices/" + dev + "/events?limit=1000&since=" + encodeURIComponent(since)).then(function (r) { return r.ok ? r.json() : { events: [] }; }),
            fetch(apiBase() + "/api/devices/" + dev + "/reviews").then(function (r) { return r.ok ? r.json() : { reviews: [] }; })
        ]).then(function (res) {
            var rev = {}, revInfo = {};
            (res[1].reviews || []).forEach(function (x) { rev[x.candidate_id] = x.label; revInfo[x.candidate_id] = { by: x.reviewer || "", at: x.updated_at || "" }; });
            var rows = [], pending = 0, cands = [];
            (res[0].events || []).forEach(function (e) {
                if (TYPES.indexOf(e.event_type) < 0) return;
                var cid = e.related_detection_id;
                var lab = rev[cid];
                if (lab === "cigarette_butt" || lab === "other_litter") {
                    rows.push({ dev: cam.id, name: cam.name || "", cid: cid, label: lab, ts: e.timestamp, by: (revInfo[cid] || {}).by, at: (revInfo[cid] || {}).at,
                        snap: e.snapshot_ref ? String(e.snapshot_ref) : tok(e.message, "snap"),
                        scene: tok(e.message, "scene"), size: sizeOf(e.message), text: detail(e.message) });
                } else if (!lab && e.event_type === "persistent_change_candidate") {
                    pending++;
                    cands.push({ dev: cam.id, name: cam.name || "", cid: cid, label: "", ts: e.timestamp,
                        snap: e.snapshot_ref ? String(e.snapshot_ref) : tok(e.message, "snap"),
                        scene: tok(e.message, "scene"), size: sizeOf(e.message), area: areaOfMsg(e.message), text: detail(e.message) });
                }
            });
            rows.sort(function (a, b) { return a.ts < b.ts ? 1 : -1; });
            rowsByDev[cam.id] = rows;
            camsByDev[cam.id] = cam;
            cands.sort(function (a, b) { return b.area - a.area || (a.ts < b.ts ? 1 : -1); });   // 큰 후보 먼저
            candsByDev[cam.id] = cands;
            // 큰 후보가 새로 생기면 알림(첫 조회는 기준선만). 한 번에 한 건으로 묶는다.
            bigKnown[cam.id] = bigKnown[cam.id] || {};
            var newBig = 0, newRow = null;
            cands.forEach(function (r) {
                if (r.area >= MIN_LIGHT_AREA && !bigKnown[cam.id][r.cid]) { bigKnown[cam.id][r.cid] = true; newBig++; newRow = newRow || r; }
            });
            if (newBig && baselineDone[cam.id]) toast(newRow, scoreOf(rows), cands.filter(function (r) { return r.area >= MIN_LIGHT_AREA; }).length);
            known[cam.id] = known[cam.id] || {};
            var score = scoreOf(rows);
            rows.forEach(function (r) {
                if (!known[cam.id][r.cid]) {
                    known[cam.id][r.cid] = true;
                    if (baselineDone[cam.id]) toast(r, score);      // 첫 조회는 기준선만, 이후 새로 확정된 것만 알림
                }
            });
            baselineDone[cam.id] = true;
            drawBadge(cam, pending);
        }).catch(function (e) { if (window.console) console.warn("[litter_map] 조회 실패(다음 주기에 다시)", e); });
    }
    function tick() {
        var cams = (typeof cameras !== "undefined" && cameras) ? cameras : [];
        Promise.all(cams.map(pollCam)).then(function () { drawList(); drawStrip(); });
    }

    // ---------- 만안구로 지도 제한 ----------
    // 경계 파일(GeoJSON)을 static_dashboard 에 두면 그 안쪽만 보이게 바깥을 흐리게 덮고, 지도를 그 범위 밖으로 끌고 갈 수 없게 한다.
    // 파일이 없거나 형식이 다르면 아무것도 바꾸지 않는다. 카카오 지도에서의 동작은 실제 지도에서 확인이 필요하다(개발 환경에서는 가짜 지도로만 시험함).
    function applyBoundary(gj) {
        var feats = gj && gj.type === "FeatureCollection" ? gj.features : (gj ? [gj] : []);
        var f = feats.filter(function (x) {
            return /만안|manan/i.test(JSON.stringify((x && x.properties) || {}));   // 속성 이름이 한글이든 영어든(name, name_eng, SIG_ENG_NM …) 상관없이 찾는다
        })[0] || (feats.length === 1 ? feats[0] : null);
        if (!f || !f.geometry) {
            if (window.console && feats.length) console.warn("[litter_map] 만안구를 찾지 못했습니다. 첫 항목 속성:", JSON.stringify(feats[0].properties || {}), "· 전체 " + feats.length + "개");
            return false;
        }
        var polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.type === "MultiPolygon" ? f.geometry.coordinates : null;
        if (!polys) return false;
        var minLat = 90, maxLat = -90, minLng = 180, maxLng = -180, holes = [];
        polys.forEach(function (poly) {
            holes.push(poly[0].map(function (c) {
                minLng = Math.min(minLng, c[0]); maxLng = Math.max(maxLng, c[0]);
                minLat = Math.min(minLat, c[1]); maxLat = Math.max(maxLat, c[1]);
                return new kakao.maps.LatLng(c[1], c[0]);
            }));
        });
        var outer = [new kakao.maps.LatLng(minLat - 1, minLng - 1), new kakao.maps.LatLng(minLat - 1, maxLng + 1),
                     new kakao.maps.LatLng(maxLat + 1, maxLng + 1), new kakao.maps.LatLng(maxLat + 1, minLng - 1)];
        var mask = new kakao.maps.Polygon({ path: [outer].concat(holes), strokeWeight: 3, strokeColor: "#2f6f62", strokeOpacity: 0.95,
                                 fillColor: "#ffffff", fillOpacity: 0.8, zIndex: 1 });
        mask.setMap(map);
        if (window.console) console.log("[litter_map] 만안구 경계 적용: 구멍 " + holes.length + "개, 범위 위도 " + minLat.toFixed(4) + "~" + maxLat.toFixed(4) + ", 경도 " + minLng.toFixed(4) + "~" + maxLng.toFixed(4));
        try {
            var c0 = map.getCenter(), lv0 = map.getLevel();
            map.setBounds(new kakao.maps.LatLngBounds(new kakao.maps.LatLng(minLat, minLng), new kakao.maps.LatLng(maxLat, maxLng)));
            var maxLv = map.getLevel();
            if (map.setMaxLevel) map.setMaxLevel(maxLv);           // 이 이상 축소 금지
            map.setLevel(Math.min(lv0, maxLv));
            map.setCenter(c0);                                      // 원래 보던 위치(안양대)는 유지
            kakao.maps.event.addListener(map, "dragend", function () {
                var c = map.getCenter(), la = c.getLat(), ln = c.getLng();
                var nla = Math.min(Math.max(la, minLat), maxLat), nln = Math.min(Math.max(ln, minLng), maxLng);
                if (nla !== la || nln !== ln) map.panTo(new kakao.maps.LatLng(nla, nln));   // 범위 밖이면 가장자리로 되돌림
            });
        } catch (e) { if (window.console) console.warn("[litter_map] 범위 제한 일부 실패", e); }
        return true;
    }
    function cropToBoundary() {
        if (typeof map === "undefined" || typeof kakao === "undefined") return;
        var i = 0;
        (function next() {
            if (i >= BOUNDARY_FILES.length) { if (window.console) console.warn("[litter_map] 만안구 경계 파일이 없어 지도를 그대로 둡니다."); return; }
            fetch(apiBase() + "/" + BOUNDARY_FILES[i++])
                .then(function (r) { if (!r.ok) throw new Error("no file"); return r.json(); })
                .then(function (gj) { if (!applyBoundary(gj)) next(); })
                .catch(function (e) { if (window.console) console.warn("[litter_map] 경계 처리 중 오류(" + BOUNDARY_FILES[i - 1] + ")", e); next(); });
        })();
    }

    function start() {
        hookOpenCamera();
        injectUi();
        cropToBoundary();
        drawList();
        tick();
        setInterval(tick, POLL_MS);
        setInterval(drawStrip, 2000);       // 화재 표시는 map.html 이 2초마다 바꾸므로 같은 주기로 다시 센다
    }
    // map.html 이 카메라 목록(/api/cameras)을 읽어 올 때까지 잠깐 기다린 뒤 시작한다.
    var tries = 0;
    (function wait() {
        var ready = (typeof cameras !== "undefined" && cameras && cameras.length && typeof labels !== "undefined" && Object.keys(labels).length);
        if (ready || tries++ > 40) { start(); return; }
        setTimeout(wait, 500);
    })();
})();
