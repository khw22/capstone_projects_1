// 중앙 서버 대시보드 - 라이브 백엔드 연동

const API_BASE = (window.API_BASE || "http://localhost:8000").replace(/\/+\$/, "");

let clearTimestamp = 0; 
let allFetchedEvents = []; 

let isAlarmAcknowledged = false; // 관리자가 경보를 껐는지 여부
let isFireCurrentlyActive = false; // 현재 불이 나고 있는 상태인지

async function apiGet(path) {
    const res = await fetch(`${API_BASE}${path}`, {
        headers: { "Accept": "application/json" },
    });
    let body = null;
    try { body = await res.json(); } catch (_) {}
    if (!res.ok || (body && body.ok === false)) {
        const code = body && body.error ? body.error.code : `http_${res.status}`;
        const msg = body && body.error ? body.error.message : res.statusText;
        throw new Error(`[${code}] ${msg}`);
    }
    return body;
}

function setBody(tableId, html) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (tbody) tbody.innerHTML = html;
}

function colCount(tableId) {
    return document.querySelectorAll(`#${tableId} thead th`).length || 1;
}

function fullWidthRow(tableId, text, cls) {
    return `<tr><td class="${cls}" colspan="${colCount(tableId)}">${text}</td></tr>`;
}

function show(v) { return (v === null || v === undefined || v === "") ? "—" : v; }

function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
           `${p(d.getHours())}:${p(d.getMinutes())}`;
}

function statusBadge(status) {
    if (status === "ok") return `<span class="badge ok">정상</span>`;
    if (!status) return `<span class="badge warn">대기</span>`;
    return `<span class="badge bad">${status}</span>`;
}

function setApiStatus(state, text) {
    const wrap = document.getElementById("api-status");
    const label = document.getElementById("api-status-text");
    if (wrap) wrap.className = `api-status ${state}`;
    if (label) label.textContent = text;
}

function isOnline(dev) { return dev.status === "ok"; }

function renderCctvTable(devices) {
    if (!devices.length) {
        setBody("cctv-table", fullWidthRow("cctv-table", "등록된 장치가 없습니다.", "muted"));
        return;
    }
    setBody("cctv-table", devices.map((d) => `
        <tr>
            <td>${show(d.device_id)}</td>
            <td>${show(d.location)}</td>
            <td>${statusBadge(d.status)}</td>
            <td>${show(d.fps)}</td>
            <td>${show(d.model_version)}</td>
            <td>${fmtTime(d.last_seen)}</td>
        </tr>
    `).join(""));
}

function renderStatCards(devices) {
    const total = devices.length;
    const online = devices.filter(isOnline).length;
    const offline = total - online;

    const set = (id, value, cls) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = value;
        if (cls) el.className = `value ${cls}`;
    };
    set("stat-online", `${online} / ${total}`);
    set("stat-streaming", online, "ok");
    set("stat-offline", offline, offline ? "warn" : "ok");
    set("stat-server", "정상", "ok");

    const versions = devices.map((d) => d.model_version).filter(Boolean);
    const mv = document.getElementById("setting-model-version");
    if (mv) mv.textContent = versions.length ? versions[versions.length - 1] : "미보고";
}

function renderRealtimeUnavailable(message) {
    setBody("cctv-table", fullWidthRow("cctv-table", message, "error"));
    ["stat-online", "stat-streaming", "stat-offline"].forEach((id) => {
        const el = document.getElementById(id);
        if (el) { el.textContent = "—"; el.className = "value"; }
    });
    const sv = document.getElementById("stat-server");
    if (sv) { sv.textContent = "오프라인"; sv.className = "value bad"; }
}

async function loadRealtime() {
    const data = await apiGet("/api/devices");
    const devices = (data && data.devices) || [];
    renderCctvTable(devices);
    renderStatCards(devices);
    return devices;
}

const SEVERITY_LABEL = { info: "정보", warning: "주의", critical: "위험" };

async function loadEvents(devices) {
    if (!devices.length) {
        setBody("events-table", fullWidthRow("events-table", "이벤트가 없습니다.", "muted"));
        setBody("fire-origin-table", fullWidthRow("fire-origin-table", "장치가 없습니다.", "muted"));
        return;
    }
    
    const results = await Promise.allSettled(
        devices.map((d) =>
            apiGet(`/api/devices/${encodeURIComponent(d.device_id)}/events?limit=100`)
                .then((r) => ((r && r.events) || []).map((e) => ({ ...e, device_id: d.device_id })))
        )
    );

    const events = [];
    let anyFailed = false;
    for (const r of results) {
        if (r.status === "fulfilled") events.push(...r.value);
        else anyFailed = true;
    }
    events.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)));

    allFetchedEvents = events;

    const displayEvents = events.filter(e => new Date(e.timestamp).getTime() >= clearTimestamp);

    if (!displayEvents.length) {
        const msg = anyFailed ? "일부 장치의 이벤트를 불러오지 못했습니다." : "현재 표시할 이벤트가 없습니다.";
        setBody("events-table", fullWidthRow("events-table", msg, anyFailed ? "error" : "muted"));
        setBody("fire-origin-table", fullWidthRow("fire-origin-table", "현재 감지된 화재가 없습니다 (안전 상태).", "ok"));
        return;
    }

    // 💡 데이터 렌더링 분리 (실시간 추적 vs 일반 이벤트 로그)
    const fireEvents = displayEvents.filter(e => e.event_type === "fire_tracking");
    
    // 🔴 [추가] 파이썬에서 보낸 OCR 데이터만 따로 뽑아냅니다.
    const ocrEvents = displayEvents.filter(e => e.event_type === "ocr_reading"); 
    
    // 🔴 [수정] 일반 로그에서는 fire_tracking과 ocr_reading을 둘 다 빼줍니다.
    const logEvents = displayEvents.filter(e => e.event_type !== "fire_tracking" && e.event_type !== "ocr_reading");


    // 1. 하단 이벤트 기록 렌더링 (logEvents 전용)
    if (logEvents.length === 0) {
        setBody("events-table", fullWidthRow("events-table", "이벤트가 없습니다.", "muted"));
    } else {
        setBody("events-table", logEvents.map((e) => {
            const sev = SEVERITY_LABEL[e.severity] || e.severity || "";
            const type = e.event_type ? `${e.event_type}${sev ? ` (\${sev})` : ""}` : show(sev);
            return `
            <tr>
                <td>${fmtTime(e.timestamp)}</td>
                <td>${show(e.device_id)}</td>
                <td>${type}</td>
                <td>${show(e.message)}</td>
            </tr>`;
        }).join(""));
    }

    // 2. 상단 화재 추적 현황 렌더링 (fireEvents 전용)
    const thSection = document.getElementById("th");
    const btnAlarmOff = document.getElementById("btn-alarm-off");

    if (fireEvents.length > 0) {
        // 💡 불이 새로 났을 때 (기존에 불이 없었는데 새로 생김)
        if (!isFireCurrentlyActive) {
            isFireCurrentlyActive = true;
            isAlarmAcknowledged = false; // 새 화재이므로 경보 해제 상태 초기화
        }

        // 💡 관리자가 아직 경보를 끄지 않았다면 빨간색 점멸 켜기!
        if (!isAlarmAcknowledged) {
            thSection.classList.add("alarm-active");
            btnAlarmOff.style.display = "block"; // 끄기 버튼 보이기
        }

        setBody("fire-origin-table", fireEvents.map((e) => {
            const sev = SEVERITY_LABEL[e.severity] || e.severity || "";
            return `
            <tr>
                <td>${show(e.device_id)}</td>
                <td>${fmtTime(e.timestamp)}</td>
                <td style="font-weight: bold; color: #ff6b6b;">${show(e.message)}</td>
                <td><span class="badge bad">${sev}</span></td>
            </tr>`;
        }).join(""));
    } else {
        // 💡 불이 꺼졌을 때 (안전 상태)
        isFireCurrentlyActive = false;
        isAlarmAcknowledged = false;
        
        thSection.classList.remove("alarm-active"); // 점멸 끄기
        btnAlarmOff.style.display = "none";         // 버튼 숨기기
        
        setBody("fire-origin-table", fullWidthRow("fire-origin-table", "현재 감지된 화재가 없습니다 (안전 상태).", "ok"));
    }

    // 3. OCR 판독 결과 렌더링 (최신순 5개까지만 화면에 표시)
    if (ocrEvents.length > 0) {
        // 가장 최근 데이터 5개만 자르기
        const recentOcr = ocrEvents.slice(0, 5); 
        setBody("ocr-table", recentOcr.map((e) => `
            <tr>
                <td>${show(e.device_id)}</td>
                <td>${fmtTime(e.timestamp)}</td>
                <td style="font-weight: bold; font-size: 16px; color: #2ecc71;">${show(e.message)}</td>
            </tr>
        `).join(""));
    } else {
        const ocrTableBody = document.querySelector("#ocr-table tbody");
        if(ocrTableBody) {
            setBody("ocr-table", fullWidthRow("ocr-table", "아직 측정된 수치가 없습니다.", "muted"));
        }
    }
}

function renderUnbackedSections() {
    setBody("pollution-table", fullWidthRow("pollution-table", "AI 오염도(쓰레기/꽁초) 탐지 모델 적용 대기 중입니다.", "muted"));
    setBody("fire-origin-table", fullWidthRow("fire-origin-table", "현재 감지된 화재가 없습니다 (안전 상태).", "ok")); 
}

document.addEventListener("DOMContentLoaded", async () => {
    const apiBaseEl = document.getElementById("setting-api-base");
    if (apiBaseEl) apiBaseEl.textContent = API_BASE;

    renderUnbackedSections();

    // 영구 삭제 기능
    const btnClear = document.getElementById("btn-clear");
    if (btnClear) {
        btnClear.addEventListener("click", async () => {
            if (!confirm("모든 이벤트 기록을 영구적으로 삭제하시겠습니까?\n데이터베이스에서 완전히 지워지며 되돌릴 수 없습니다.")) {
                return;
            }
            try {
                const data = await loadRealtime();
                for (const d of data) {
                    await fetch(`${API_BASE}/api/devices/${encodeURIComponent(d.device_id)}/events`, {
                        method: "DELETE"
                    });
                }
                alert("영구 삭제 완료!");
                allFetchedEvents = []; 
                setBody("events-table", fullWidthRow("events-table", "이벤트가 없습니다.", "muted"));
                setBody("fire-origin-table", fullWidthRow("fire-origin-table", "현재 감지된 화재가 없습니다 (안전 상태).", "ok"));
            } catch (err) {
                alert("삭제 중 서버 연결 오류: " + err.message);
            }
        });
    }

    // 🚨 여기에 경보 점멸 끄기 버튼 클릭 이벤트 추가!
    const btnAlarmOff = document.getElementById("btn-alarm-off");
    if (btnAlarmOff) {
        btnAlarmOff.addEventListener("click", () => {
            isAlarmAcknowledged = true; // 관리자가 인지함
            
            // 시각적 알람(점멸) 제거 및 버튼 숨기기
            document.getElementById("th").classList.remove("alarm-active");
            btnAlarmOff.style.display = "none";
        });
    }

    // 🚨 [추가] 영상 원격 제어 (빨간 테두리 끄기) 명령 전송 기능
    const btnRemoteAlarmOff = document.getElementById("btn-remote-alarm-off");
    if (btnRemoteAlarmOff) {
        btnRemoteAlarmOff.addEventListener("click", async () => {
            try {
                const iso = new Date().toISOString();
                const data = await loadRealtime();
                for (const d of data) {
                    await fetch(`${API_BASE}/api/devices/${encodeURIComponent(d.device_id)}/events`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            timestamp: iso,
                            event_type: "명령",
                            severity: "info",
                            message: "MANUAL_ALARM_OFF"
                        })
                    });
                }
                alert("명령 전송: CCTV 영상 화면의 빨간 테두리 알람을 강제 해제합니다.");
            } catch (err) {
                alert("명령 전송 실패: " + err.message);
            }
        });
    }

    // 엑셀 저장 기능 (💡 실시간 좌표 도배 로그는 빼고, 깨끗한 기록만 저장되도록 필터링 적용!)
    const btnExport = document.getElementById("btn-export");
    if (btnExport) {
        btnExport.addEventListener("click", () => {
            // fire_tracking을 제외한 진짜 이벤트 기록만 다운로드
            const exportData = allFetchedEvents.filter(e => e.event_type !== "fire_tracking");
            
            if (!exportData.length) return alert("저장할 이벤트 로그가 없습니다.");
            
            let csv = "발생일시,장치ID,이벤트유형,위험도,상세내용\n";
            exportData.forEach(e => {
                const time = fmtTime(e.timestamp);
                const dev = e.device_id || "";
                const type = e.event_type || "";
                const sev = SEVERITY_LABEL[e.severity] || e.severity || "";
                const msg = (e.message || "").replace(/,/g, " "); // CSV 호환을 위해 쉼표 제거
                csv += `${time},${dev},${type},${sev},${msg}\n`;
            });

            const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `관제기록_${new Date().getTime()}.csv`;
            a.click();
        });
    }

    const refreshData = async () => {
        try {
            const devices = await loadRealtime();
            setApiStatus("online", `API 연결됨 · 장치 ${devices.length}대`);
            await loadEvents(devices);
        } catch (err) {
            setApiStatus("offline", "API 서버에 연결할 수 없습니다");
            renderRealtimeUnavailable(`서버에 연결할 수 없습니다: ${err.message}`);
        }
    };

    // 최초 1회 로드 (브라우저 새로고침[F5]을 누를 때마다 갱신됩니다)
    await refreshData();
});

// ==========================================
// 🌤️ 날씨 및 온습도 검색 & 현재 위치 연동
// ==========================================

// 1. 위도/경도를 받아 날씨를 업데이트하는 함수 (Open-Meteo API)
async function updateWeather(lat, lon, locationName) {
    try {
        const res = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,relative_humidity_2m,weather_code`);
        const data = await res.json();
        
        const temp = data.current.temperature_2m;
        const hum = data.current.relative_humidity_2m;
        const code = data.current.weather_code;
        
        // WMO 날씨 코드에 따른 간단한 이모티콘 매핑
        let icon = "☀️";
        if (code >= 1 && code <= 3) icon = "⛅";
        else if (code >= 45 && code <= 48) icon = "🌫️";
        else if (code >= 51 && code <= 67) icon = "🌧️";
        else if (code >= 71 && code <= 77) icon = "❄️";
        else if (code >= 80 && code <= 82) icon = "☔";
        else if (code >= 95) icon = "⛈️";
        
        document.getElementById('w-location').textContent = locationName;
        document.getElementById('w-temp').textContent = `${temp}°C`;
        document.getElementById('w-hum').textContent = `💧${hum}%`;
        document.getElementById('w-icon').textContent = icon;
        
    } catch (e) {
        console.error("날씨 정보를 불러오는데 실패했습니다.", e);
    }
}

// 2. 검색어를 통해 특정 지역의 위도/경도를 찾는 함수
async function searchLocation(query) {
    try {
        const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}`);
        const data = await res.json();
        
        if (data.length > 0) {
            updateWeather(data[0].lat, data[0].lon, query);
        } else {
            alert("검색하신 지역을 찾을 수 없습니다.");
        }
    } catch (e) {
        console.error("지역 검색 실패", e);
        alert("지역 검색 중 오류가 발생했습니다.");
    }
}

// 💡 3. 접속한 사용자의 현재 위치를 찾아 날씨를 띄우는 함수
function loadCurrentLocationWeather() {
    if (navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(async (position) => {
            const lat = position.coords.latitude;
            const lon = position.coords.longitude;
            
            try {
                // 현재 좌표를 지역명(동/구/시)으로 변환
                const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}`);
                const data = await res.json();
                
                // 주소 정보 중 가장 적절한 지역명 추출
                let locName = "현재 위치";
                if (data.address) {
                    locName = data.address.city || data.address.town || data.address.borough || data.address.suburb || "현재 위치";
                }
                
                updateWeather(lat, lon, locName);
            } catch (e) {
                updateWeather(lat, lon, "현재 위치");
            }
        }, (error) => {
            console.warn("위치 권한 거부 또는 오류 발생. 기본 위치(인천)로 표시합니다.");
            // 위치 권한을 거부할 경우 기본값 (인천광역시 좌표)
            updateWeather(37.4563, 126.7052, "인천광역시");
        });
    } else {
        console.warn("브라우저가 위치 정보를 지원하지 않습니다.");
        updateWeather(37.4563, 126.7052, "인천광역시");
    }
}

// 4. 이벤트 리스너 등록
document.addEventListener("DOMContentLoaded", () => {
    const btnSearch = document.getElementById("btn-weather-search");
    const inputSearch = document.getElementById("weather-input");

    if (btnSearch && inputSearch) {
        btnSearch.addEventListener("click", () => {
            const query = inputSearch.value.trim();
            if (query) searchLocation(query);
        });

        inputSearch.addEventListener("keypress", (e) => {
            if (e.key === "Enter") {
                const query = inputSearch.value.trim();
                if (query) searchLocation(query);
            }
        });
    }

    // 💡 초기 로딩 시 무조건 '현재 위치' 기반 날씨 표출 실행
    loadCurrentLocationWeather();
});