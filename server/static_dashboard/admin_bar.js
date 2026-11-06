/* admin_bar.js — 대시보드(index.html)와 지도(map.html) 공통 메뉴 + 로그아웃
 * 각 페이지의 </body> 바로 앞에 한 줄: <script src="admin_bar.js"></script>
 * - index.html: 위쪽 메뉴(.topbar .nav)에 '지도 관제'와 로그아웃을 덧붙인다.
 * - map.html: 왼쪽 위에 작은 메뉴 막대를 띄운다.
 * - 서버 로그인 보호가 꺼져 있으면 "로그인 보호 꺼짐"이라고 정직하게 표시한다.
 */
(function () {
    "use strict";
    function el(tag, css, html) { var e = document.createElement(tag); if (css) e.style.cssText = css; if (html != null) e.innerHTML = html; return e; }
    function logout() {
        fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }).finally(function () { location.href = "/login.html"; });
    }
    var embedded = false;
    try { embedded = window.top !== window.self; } catch (e) { embedded = true; }
    function goLogin() {
        var next = "/login.html?next=" + encodeURIComponent((embedded ? "/control.html" : location.pathname));
        try { (embedded ? window.top : window).location.href = next; } catch (e) { location.href = next; }
    }
    // 분할 화면(control.html) 안의 대시보드: 위쪽 머리글(날씨·메뉴)은 숨기고, 지도에서 CCTV를 고르면 그 장치 줄로 이동·강조한다
    function embedDashboard() {
        var st = document.createElement("style");
        st.textContent = ".topbar,.bottombar{display:none!important}.content{padding-top:12px!important}" +
            ".litter-hl{outline:3px solid #f5a35c!important;outline-offset:-2px;background:rgba(245,163,92,.18)!important;transition:background .3s}";
        document.head.appendChild(st);
        window.addEventListener("message", function (ev) {
            if (ev.origin !== location.origin || !ev.data || ev.data.type !== "litter-focus") return;
            var id = String(ev.data.id || "");
            var rows = document.querySelectorAll("#pollution-table tbody tr, #cctv-table tbody tr");
            var first = null;
            rows.forEach(function (tr) {
                var c = tr.cells && tr.cells[0];
                if (c && c.textContent.trim() === id) { tr.classList.add("litter-hl"); first = first || tr; setTimeout(function () { tr.classList.remove("litter-hl"); }, 3500); }
            });
            var target = first || document.getElementById("pollution");
            if (target && target.scrollIntoView) target.scrollIntoView({ behavior: "smooth", block: "center" });
        });
    }
    function build(info) {
        if (embedded) { if (/index\.html$|\/$/.test(location.pathname)) embedDashboard(); return; }   // 위쪽 공통 헤더는 control.html 이 하나만 둔다
        var onMap = /map\.html$/.test(location.pathname);
        var nav = document.querySelector(".topbar .nav");
        var user = info.auth_enabled ? ("👤 " + (info.user || "관리자")) : "⚠ 로그인 보호 꺼짐";
        if (nav) {                                           // index.html
            var a = el("a", "", "🗺 지도 관제"); a.href = "map.html"; nav.appendChild(a);
            var cc = el("a", "", "🖥 통합 화면"); cc.href = "control.html"; nav.appendChild(cc);
            var s = el("span", "margin-left:14px;font-size:12px;color:" + (info.auth_enabled ? "#9fc3ff" : "#ffcf7a"), user);
            nav.appendChild(s);
            if (info.auth_enabled) { var b = el("a", "cursor:pointer;margin-left:10px", "로그아웃"); b.addEventListener("click", logout); nav.appendChild(b); }
            return;
        }
        var bar = el("div", "position:fixed;top:12px;left:12px;z-index:70;display:flex;gap:6px;align-items:center;background:rgba(15,27,42,.92);color:#fff;padding:6px 10px;border-radius:999px;font:12px 'Malgun Gothic',sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.35)");
        var h = el("a", "color:#cfe3ff;text-decoration:none;font-weight:bold", "🏠 대시보드"); h.href = "index.html";
        var m = el("a", "color:#fff;text-decoration:none;font-weight:bold;padding:2px 8px;border-radius:999px;background:#2f6f62", "🗺 지도 관제"); m.href = "map.html";
        var u = el("span", "color:" + (info.auth_enabled ? "#9fc3ff" : "#ffcf7a"), user);
        var cc2 = el("a", "color:#cfe3ff;text-decoration:none;font-weight:bold", "🖥 통합 화면"); cc2.href = "control.html";
        bar.appendChild(h); bar.appendChild(m); bar.appendChild(cc2); bar.appendChild(u);
        if (info.auth_enabled) { var o = el("button", "border:0;border-radius:999px;padding:3px 9px;background:#c0392b;color:#fff;font-weight:bold;cursor:pointer", "로그아웃"); o.addEventListener("click", logout); bar.appendChild(o); }
        bar.id = "admin-bar";
        document.body.appendChild(bar);
    }
    // 통합 화면(control.html)의 안쪽 칸(iframe)이거나 스스로 메뉴를 가진 페이지면 공통 메뉴를 만들지 않는다.
    if (window.self !== window.top || document.documentElement.getAttribute("data-no-adminbar") != null) return;
    fetch("/api/auth/me", { credentials: "same-origin" })
        .then(function (r) {
            if (r.status === 401) { goLogin(); return null; }
            return r.ok ? r.json() : { auth_enabled: false };
        })
        .then(function (info) { if (info) build(info); })
        .catch(function () { /* 서버 구버전이면 아무것도 하지 않는다 */ });
})();
