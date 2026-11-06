"""관리자 로그인 (환경지기 대시보드 보호).

server/api/ 폴더에 두고, main.py 에서 아래 두 줄로 켠다 (app 과 CORS 설정 바로 뒤):
    from . import admin_auth
    admin_auth.install(app, storage.get_api_key)

환경변수 (PowerShell 예: $env:CCTV_ADMIN_PASSWORD="강한비밀번호")
    CCTV_ADMIN_PASSWORD  관리자 비밀번호. 비어 있으면 로그인 보호가 꺼진다(기존 동작 그대로, 시작할 때 경고 출력).
    CCTV_ADMIN_USER      관리자 아이디 (기본 admin)
    CCTV_SESSION_SECRET  세션 서명 비밀값. 없으면 서버를 켤 때마다 새로 만들어 서버를 다시 켜면 모두 로그아웃된다.
    CCTV_SESSION_HOURS   로그인 유지 시간 (기본 8)

보호 범위 (로그인이 켜져 있을 때)
    - /login.html, /api/auth/* 만 로그인 없이 열린다.
    - 기기(edge)가 올리는 POST /api/devices/{id}/(heartbeat|detections|events|snapshots) 는
      X-Device-Api-Key 가 그 기기의 키와 맞으면 통과한다(쿠키 불필요).
    - 그 밖의 모든 주소(대시보드 페이지, 데이터 조회, 영상·사진, 검수 저장, 삭제)는 로그인 쿠키가 필요하다.
    - 페이지 요청이면 /login.html 로 보내고, 데이터 요청이면 401 을 돌려준다.

한계 (정직하게): HTTPS 가 아니라서 같은 망에서는 비밀번호가 평문으로 오갈 수 있다(localhost 시연은 무관).
계정은 하나, 비밀번호 재설정·다중 사용자·2단계 인증은 없다. 로그인 시도는 IP당 5분에 5번까지 허용한다.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Callable, Optional
from urllib.parse import quote

COOKIE = "cctv_admin"
INGEST = re.compile(r"^/api/devices/([^/]+)/(heartbeat|detections|events|snapshots)$")
OPEN_PATHS = {"/login.html", "/favicon.ico", "/api/auth/login", "/api/auth/logout", "/api/auth/me"}
MAX_FAILS, WINDOW_S = 5, 300


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class AdminAuth:
    def __init__(self, get_device_key: Optional[Callable[[str], Optional[str]]] = None):
        self.user = os.getenv("CCTV_ADMIN_USER", "admin")
        self.password = os.getenv("CCTV_ADMIN_PASSWORD", "")
        self.enabled = bool(self.password)
        self.secret = (os.getenv("CCTV_SESSION_SECRET") or "").encode() or secrets.token_bytes(32)
        self.hours = float(os.getenv("CCTV_SESSION_HOURS", "8"))
        self.get_device_key = get_device_key
        self.fails = {}

    # ---- 세션 토큰 (사용자|만료시각 + HMAC 서명) ----
    def make_token(self, user: str) -> str:
        payload = _b64(json.dumps({"u": user, "exp": int(time.time() + self.hours * 3600)}).encode())
        sig = _b64(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())
        return payload + "." + sig

    def read_token(self, token: str) -> Optional[str]:
        try:
            payload, sig = token.split(".", 1)
            good = _b64(hmac.new(self.secret, payload.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, good):
                return None
            data = json.loads(_unb64(payload))
            return data["u"] if data["exp"] > time.time() else None
        except Exception:
            return None

    # ---- 로그인 확인 ----
    def check_login(self, user: str, password: str) -> bool:
        ok_u = hmac.compare_digest(hashlib.sha256(user.encode()).digest(), hashlib.sha256(self.user.encode()).digest())
        ok_p = hmac.compare_digest(hashlib.sha256(password.encode()).digest(), hashlib.sha256(self.password.encode()).digest())
        return ok_u and ok_p

    def locked(self, ip: str) -> bool:
        now = time.time()
        self.fails[ip] = [t for t in self.fails.get(ip, []) if now - t < WINDOW_S]
        return len(self.fails[ip]) >= MAX_FAILS

    def fail(self, ip: str) -> None:
        self.fails.setdefault(ip, []).append(time.time())


def install(app, get_device_key: Optional[Callable[[str], Optional[str]]] = None) -> AdminAuth:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    auth = AdminAuth(get_device_key)
    if not auth.enabled:
        print("[auth] 관리자 로그인 보호가 꺼져 있습니다. 켜려면 CCTV_ADMIN_PASSWORD 환경변수를 지정하고 서버를 다시 켜세요.")
    else:
        print("[auth] 관리자 로그인 보호 켜짐 (아이디: %s, 유지 %.0f시간)" % (auth.user, auth.hours))
        if not os.getenv("CCTV_SESSION_SECRET"):
            print("[auth] CCTV_SESSION_SECRET 이 없어 서버를 다시 켜면 로그인이 풀립니다.")

    def current_user(request: Request) -> Optional[str]:
        tok = request.cookies.get(COOKIE)
        return auth.read_token(tok) if tok else None

    async def login(request: Request):
        ip = request.client.host if request.client else "?"
        if not auth.enabled:
            return JSONResponse({"ok": True, "auth_enabled": False})
        if auth.locked(ip):
            return JSONResponse({"detail": "로그인 시도가 너무 많습니다. 5분 뒤에 다시 시도하세요."}, status_code=429)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not auth.check_login(str(body.get("username", "")), str(body.get("password", ""))):
            auth.fail(ip)
            return JSONResponse({"detail": "아이디 또는 비밀번호가 맞지 않습니다."}, status_code=401)
        resp = JSONResponse({"ok": True, "user": auth.user})
        resp.set_cookie(COOKIE, auth.make_token(auth.user), max_age=int(auth.hours * 3600), httponly=True, samesite="lax", path="/")
        return resp

    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    async def me(request: Request):
        u = current_user(request)
        if auth.enabled and not u:
            return JSONResponse({"auth_enabled": True, "authenticated": False}, status_code=401)
        return {"auth_enabled": auth.enabled, "authenticated": bool(u) or not auth.enabled, "user": u}

    # 정적 파일(app.mount("/"))보다 먼저 등록되어야 하므로 install 은 mount 앞에서 호출한다.
    app.add_api_route("/api/auth/login", login, methods=["POST"])
    app.add_api_route("/api/auth/logout", logout, methods=["POST"])
    app.add_api_route("/api/auth/me", me, methods=["GET"])

    class Gate:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and scope["method"] in ("GET", "HEAD"):
                host = dict((k.decode().lower(), v.decode()) for k, v in scope["headers"]).get("host", "")
                if host.startswith("127.0.0.1"):            # 127.0.0.1 과 localhost 는 쿠키가 따로라서 localhost 로 통일한다
                    qs = scope.get("query_string", b"").decode()
                    loc = "http://localhost" + host[len("127.0.0.1"):] + scope["path"] + ("?" + qs if qs else "")
                    await send({"type": "http.response.start", "status": 302, "headers": [(b"location", loc.encode())]})
                    return await send({"type": "http.response.body", "body": b""})
            if scope["type"] != "http" or not auth.enabled:
                return await self.inner(scope, receive, send)
            path, method = scope["path"], scope["method"]
            host = {k.decode().lower(): v.decode() for k, v in scope["headers"]}.get("host", "")
            if method == "GET" and host.startswith("127.0.0.1"):
                # 쿠키는 주소(호스트)마다 따로라서 127.0.0.1 로 로그인하면 localhost 에서는 로그인이 안 된 것으로 보인다. 항상 localhost 로 통일한다.
                loc = "http://localhost" + (":" + host.split(":", 1)[1] if ":" in host else "") + path
                qs = scope.get("query_string", b"").decode()
                if qs:
                    loc += "?" + qs
                await send({"type": "http.response.start", "status": 302, "headers": [(b"location", loc.encode())]})
                return await send({"type": "http.response.body", "body": b""})
            if path in OPEN_PATHS or method == "OPTIONS":
                return await self.inner(scope, receive, send)
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            # 쿠키
            cookies = {}
            for part in headers.get("cookie", "").split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookies[k] = v
            if cookies.get(COOKIE) and auth.read_token(cookies[COOKIE]):
                return await self.inner(scope, receive, send)
           # 기기가 올리는 데이터: 기기 및 분석 스크립트의 전송 데이터는 인증 없이 통과
            m = INGEST.match(path)
            if m and method == "POST":
                return await self.inner(scope, receive, send)
                if auth.get_device_key:
                    given = headers.get("x-device-api-key", "")
                    expected = auth.get_device_key(m.group(1)) or ""
                    if given and expected and hmac.compare_digest(given.encode(), expected.encode()):
                        return await self.inner(scope, receive, send)
            wants_page = method == "GET" and (path == "/" or path.endswith(".html") or "text/html" in headers.get("accept", ""))
            if wants_page and not path.startswith("/api/"):
                location = "/login.html?next=" + quote(path)
                await send({"type": "http.response.start", "status": 302, "headers": [(b"location", location.encode())]})
                return await send({"type": "http.response.body", "body": b""})
            body = json.dumps({"detail": "로그인이 필요합니다."}, ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

    app.add_middleware(Gate)
    return auth
