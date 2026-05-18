"""
MuMu onboarding: Super Proxy → OfferUp signup → AnyMessage verification → Chrome.

Uses ADB/UIAutomator helpers from app.py (imported inside workers to avoid cycles).
"""

from __future__ import annotations

import html as html_module
import json
import os
import random
import re
import secrets
import string
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

PROXY_PACKAGE = "com.scheler.superproxy"
OFFERUP_PACKAGE = "com.offerup"
MUMU_STORE_PACKAGES = (
    "com.mumu.store",
    "com.netease.mumu.store",
    "com.mumu.android",
    "com.netease.mumu",
    "com.mumu.launcher",
)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_proxy_apk() -> str:
    try:
        from app import PROXY_APK_PATH

        return PROXY_APK_PATH
    except Exception:
        return os.path.join(_APP_DIR, "Proxy.apk")


def _default_offerup_apks() -> str:
    try:
        from app import OFFERUP_APKS_PATH

        return OFFERUP_APKS_PATH
    except Exception:
        return os.path.join(_APP_DIR, "OfferUp_2026.18.0.apks")

ANYMESSAGE_API_BASE = "https://api.anymessage.shop"
ANYMESSAGE_SITE = "offerup.com"
DEFAULT_PROXY_HOST = "192.168.0.103"
PROXY_PORT_PRESETS = list(range(60000, 60016))
MUMU_VERTICAL_RESOLUTION = "phone.1"

_OFFERUP_CONFIRM_EMAIL_RE = re.compile(
    r"https?://(?:www\.)?offerup\.com/accounts/register/confirm-email\?[^\s\"'<>)\]]+",
    re.I,
)
_OFFERUP_VERIFY_LINK_RE = re.compile(
    r'https?://[^\s"\'<>]+(?:offerup\.com|offerup\.co)[^\s"\'<>]*',
    re.I,
)
_HREF_RE = re.compile(r'href\s*=\s*["\'](https?://[^"\']+)["\']', re.I)
_CODE_RE = re.compile(r'\b(\d{4,8})\b')
_ANYMESSAGE_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _log_cb(log: Optional[Callable[[str], None]], msg: str) -> None:
    if log:
        log(msg)


def generate_signup_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(max(10, length)))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
        ):
            return pwd


def _check_stop(should_stop: Optional[Callable[[], bool]]) -> None:
    if should_stop and should_stop():
        raise RuntimeError("Остановлено")


def _sleep_stop(
    seconds: float,
    should_stop: Optional[Callable[[], bool]] = None,
    step: float = 0.15,
) -> None:
    """Пауза с проверкой стопа (для кнопки «Стоп» онбординга)."""
    if seconds <= 0:
        _check_stop(should_stop)
        return
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _check_stop(should_stop)
        time.sleep(min(step, max(0.04, deadline - time.monotonic())))


def _pause(
    seconds: float,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    """Укороченная пауза онбординга (~2.5× быстрее)."""
    scaled = max(0.04, float(seconds) * 0.38)
    if should_stop:
        _sleep_stop(scaled, should_stop)
    else:
        time.sleep(scaled)


def maybe_typo_display_name(name: str, enabled: bool) -> str:
    """Случайная опечатка в имени (~50%), иначе как в настройках."""
    s = (name or "").strip()
    if not enabled or len(s) < 4:
        return s
    if random.random() < 0.5:
        return s
    letters = [(i, c) for i, c in enumerate(s) if c.isalpha()]
    if not letters:
        return s
    idx, ch = random.choice(letters)
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    repl = random.choice(alphabet.replace(ch.lower(), "")[:6] or alphabet[:6])
    if ch.isupper():
        repl = repl.upper()
    chars = list(s)
    chars[idx] = repl
    return "".join(chars)


def resolve_signup_display_name(cfg: Dict[str, Any]) -> str:
    name = str(cfg.get("signup_name") or "").strip()
    if cfg.get("use_buyer_name"):
        buyer = str(cfg.get("buyer_name") or "").strip()
        if buyer:
            name = buyer
    if cfg.get("name_typo_enabled"):
        name = maybe_typo_display_name(name, True)
    return name or "Alex Smith"


def split_display_name(name: str) -> Tuple[str, str]:
    parts = (name or "").strip().split(None, 1)
    if not parts:
        return "Alex", "Smith"
    if len(parts) == 1:
        return parts[0], "User"
    return parts[0], parts[1]


# ── AnyMessage Shop API ─────────────────────────────────────────────


def anymessage_get(
    url_path: str,
    params: Dict[str, str],
    timeout: float = 60.0,
    retries: int = 4,
) -> dict:
    """Запрос к AnyMessage только с хоста, без системного HTTP-прокси."""
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = ANYMESSAGE_API_BASE.rstrip("/") + url_path + "?" + q
    last_err: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "MumuPaster/1.0 (direct)",
            },
        )
        try:
            with _ANYMESSAGE_NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise RuntimeError(f"AnyMessage HTTP {e.code}: {raw[:300]}") from e
        except urllib.error.URLError as e:
            last_err = e
            if attempt + 1 < retries:
                time.sleep(min(8.0, 1.5 * (attempt + 1)))
                continue
            raise RuntimeError(f"AnyMessage network error: {e}") from e
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            head = raw.lstrip()[:32].lower()
            if head.startswith("<!doctype") or head.startswith("<html"):
                return {"status": "success", "message": raw, "value": None}
            raise RuntimeError(f"AnyMessage invalid JSON: {raw[:200]}")
        if not isinstance(data, dict):
            raise RuntimeError("AnyMessage response is not an object")
        return data
    if last_err:
        raise RuntimeError(f"AnyMessage network error: {last_err}") from last_err
    raise RuntimeError("AnyMessage: запрос не выполнен")


def anymessage_getmessage_once(
    token: str,
    activation_id: str,
    *,
    preview_html: bool = False,
) -> dict:
    """getmessage без preview=1 — ответ JSON, HTML в поле message."""
    params = {"token": token, "id": activation_id}
    if preview_html:
        params["preview"] = "1"
    return anymessage_get("/email/getmessage", params)


def _anymessage_getmessage_resilient(
    token: str,
    activation_id: str,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    try:
        return anymessage_getmessage_once(token, activation_id)
    except RuntimeError as e:
        msg = str(e).lower()
        if "network" in msg or "timeout" in msg or "10060" in msg:
            _log_cb(log, f"AnyMessage: сеть, повтор позже… ({e})")
            return None
        raise


def anymessage_reorder_email(
    token: str,
    activation_id: str,
    *,
    site: str = ANYMESSAGE_SITE,
    email: str = "",
) -> Tuple[str, str]:
    """Перезаказ той же почты (reorder) — тот же id или новый."""
    params: Dict[str, str] = {"token": token, "id": str(activation_id)}
    data = anymessage_get("/email/reorder", params)
    if data.get("status") != "success":
        if email:
            data = anymessage_get(
                "/email/reorder",
                {"token": token, "email": email, "site": site},
            )
        if data.get("status") != "success":
            err = data.get("value") or data.get("message") or str(data)
            raise RuntimeError(f"AnyMessage reorder failed: {err}")
    new_id = str(data.get("id") or activation_id).strip()
    new_email = str(data.get("email") or email).strip()
    return new_id, new_email


def anymessage_api_request(
    path: str,
    params: Dict[str, str],
    *,
    timeout: float = 60.0,
) -> dict:
    """Произвольный GET к AnyMessage API (для UI вкладки «Почты»)."""
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    return anymessage_get(p, params, timeout=timeout)


def anymessage_order_email(
    token: str,
    site: str = ANYMESSAGE_SITE,
    domain: str = "gmx.com",
) -> Tuple[str, str]:
    data = anymessage_get(
        "/email/order",
        {"token": token, "site": site, "domain": domain},
    )
    if data.get("status") != "success":
        err = data.get("value") or data.get("message") or str(data)
        raise RuntimeError(f"AnyMessage order failed: {err}")
    activation_id = str(data.get("id") or "").strip()
    email = str(data.get("email") or "").strip()
    if not activation_id or not email:
        raise RuntimeError("AnyMessage order: empty id/email")
    return activation_id, email


def _message_blob_from_api(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    msg = str(data.get("message") or "").strip()
    val = str(data.get("value") or "").strip()
    if msg and (msg.lstrip().lower().startswith("<!doctype") or msg.lstrip().lower().startswith("<html")):
        return html_module.unescape(msg)
    if val and (val.lstrip().lower().startswith("<!doctype") or val.lstrip().lower().startswith("<html")):
        return html_module.unescape(val)
    parts = [p for p in (msg, val) if p and p not in ("wait message", "null", "None")]
    if parts:
        return html_module.unescape("\n".join(parts))
    return html_module.unescape(json.dumps(data, ensure_ascii=False))


def _normalize_offerup_url(url: str) -> str:
    u = html_module.unescape((url or "").strip())
    u = u.replace("\\/", "/")
    u = u.rstrip(").,;]\"'")
    return u


def extract_offerup_link_from_message(html_or_text: str) -> str:
    blob = html_module.unescape(html_or_text or "")
    blob = blob.replace("\\/", "/")

    best_confirm = ""
    for m in _OFFERUP_CONFIRM_EMAIL_RE.finditer(blob):
        u = _normalize_offerup_url(m.group(0))
        if len(u) > len(best_confirm):
            best_confirm = u
    if best_confirm:
        return best_confirm

    for m in _HREF_RE.finditer(blob):
        url = _normalize_offerup_url(m.group(1))
        if "confirm-email" in url.lower():
            if len(url) > len(best_confirm):
                best_confirm = url
    if best_confirm:
        return best_confirm

    for pat in (_OFFERUP_VERIFY_LINK_RE, _HREF_RE):
        for m in pat.finditer(blob):
            url = _normalize_offerup_url(m.group(1) if pat is _HREF_RE else m.group(0))
            if "offerup" in url.lower():
                return url
    return ""


def extract_verification_code_from_message(html_or_text: str) -> str:
    blob = html_module.unescape(html_or_text or "")
    low = blob.lower()
    # Явные метки кода в письме OfferUp
    for pat in (
        r'(?:code|verification|verify|код)[^\d]{0,40}(\d{4,8})',
        r'(\d{4,8})[^\d]{0,20}(?:code|verification)',
    ):
        m = re.search(pat, low, re.I)
        if m:
            return m.group(1)
    # Крупный код в HTML (часто единственное 4–6 цифр)
    candidates = _CODE_RE.findall(blob)
    candidates = [c for c in candidates if 4 <= len(c) <= 8]
    if not candidates:
        return ""
    # Предпочитаем 6-значный (типичный OTP)
    six = [c for c in candidates if len(c) == 6]
    if six:
        return six[0]
    return candidates[0]


def anymessage_wait_verification_link(
    token: str,
    activation_id: str,
    timeout_sec: float = 180.0,
    poll_sec: float = 10.0,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> str:
    deadline = time.monotonic() + timeout_sec
    poll_n = 0
    while time.monotonic() < deadline:
        _check_stop(should_stop)
        poll_n += 1
        data = _anymessage_getmessage_resilient(token, activation_id, log=log)
        if data is None:
            _sleep_stop(poll_sec, should_stop)
            continue
        if data.get("status") == "success":
            blob = _message_blob_from_api(data)
            link = extract_offerup_link_from_message(blob)
            if link:
                _log_cb(log, f"AnyMessage: ссылка найдена {link[:80]}")
                return link
            if poll_n <= 2 or poll_n % 3 == 0:
                _log_cb(log, "AnyMessage: письмо есть, ссылка OfferUp пока не найдена…")
        err = str(data.get("value") or "")
        if err == "wait message":
            if poll_n <= 1 or poll_n % 3 == 0:
                _log_cb(log, "AnyMessage: ждём письмо…")
        elif err and poll_n <= 3:
            _log_cb(log, f"AnyMessage: {err}")
        _sleep_stop(poll_sec, should_stop)
    raise RuntimeError("AnyMessage: таймаут ожидания письма OfferUp")


def anymessage_wait_verification_code(
    token: str,
    activation_id: str,
    timeout_sec: float = 120.0,
    poll_sec: float = 10.0,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> str:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        _check_stop(should_stop)
        data = _anymessage_getmessage_resilient(token, activation_id, log=log)
        if data is None:
            _sleep_stop(poll_sec, should_stop)
            continue
        if data.get("status") == "success":
            blob = _message_blob_from_api(data)
            code = extract_verification_code_from_message(blob)
            if code:
                _log_cb(log, f"AnyMessage: CODE {code}")
                return code
            _log_cb(log, "AnyMessage: письмо есть, CODE не распознан — ждём…")
        err = str(data.get("value") or "")
        if err == "wait message":
            _log_cb(log, "AnyMessage: ждём CODE…")
        elif err:
            _log_cb(log, f"AnyMessage: {err}")
        _sleep_stop(poll_sec, should_stop)
    return ""


def offerup_ui_needs_email_verification(serial: str) -> bool:
    from app import dump_ui_serial, _ui_visible_texts

    root = dump_ui_serial(serial)
    if root is None:
        return False
    blob = " ".join(_ui_visible_texts(root)).lower()
    markers = (
        "is this you",
        "verification code",
        "enter code",
        "confirm it's you",
        "confirm it’s you",
        "verify",
        "resend",
        "can't receive",
        "cant receive",
        "код",
    )
    return any(m in blob for m in markers)


def _offerup_tap_resend_in_app(serial: str, log: Optional[Callable[[str], None]] = None) -> bool:
    if _wait_tap_text(
        serial,
        ("Resend", "resend", "Отправить снова", "Send again"),
        5.0,
        log=log,
        resource_substrings=("resend", "send_again"),
    ):
        return True
    from app import dump_ui_serial, _offerup_find_clickable, _offerup_label

    root = dump_ui_serial(serial)
    if root is None:
        return False

    def pred(n: ET.Element) -> bool:
        return "resend" in _offerup_label(n).lower()

    xy = _offerup_find_clickable(root, pred)
    if xy:
        _tap(serial, xy[0], xy[1])
        return True
    return False


def _offerup_tap_cant_receive(serial: str, log: Optional[Callable[[str], None]] = None) -> bool:
    return _wait_tap_text(
        serial,
        ("Can't receive", "Cant receive", "cannot receive", "Не получил", "не приш"),
        6.0,
        log=log,
    )


def _offerup_enter_verification_code(serial: str, code: str, log: Optional[Callable[[str], None]] = None) -> bool:
    from app import input_text_serial

    if not code:
        return False
    _log_cb(log, f"OfferUp: ввод CODE {code}")
    if not _type_in_edittext(serial, code, 0):
        sw, sh = _screen_wh(serial)
        _tap_frac(serial, 0.5, 0.42, sw, sh)
        _pause(0.2)
    if not input_text_serial(code, serial):
        return False
    _pause(0.4)
    if _wait_tap_text(
        serial,
        ("Verify", "Submit", "Continue", "Next", "Подтверд"),
        4.0,
        log=log,
        resource_substrings=("verify", "submit", "button.next", "confirmation"),
    ):
        return True
    from app import run_adb_serial

    run_adb_serial(serial, ["shell", "input", "keyevent", "66"])
    return True


def handle_offerup_email_verification(
    serial: str,
    token: str,
    activation_id: str,
    email: str = "",
    *,
    prefer_code: bool = False,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[dict]:
    """Resend в OfferUp → reorder AnyMessage → getmessage с ПК (без прокси) → CODE или ссылка."""
    if not token or not activation_id:
        return None
    if not offerup_ui_needs_email_verification(serial):
        return None

    _log_cb(log, "OfferUp: экран верификации — Resend + reorder почты")
    aid = str(activation_id)
    em = email
    _offerup_tap_resend_in_app(serial, log=log)
    _pause(0.38)
    try:
        aid, em = anymessage_reorder_email(token, aid, email=em)
        _log_cb(log, f"AnyMessage: reorder → {em or aid}")
    except Exception as e:
        _log_cb(log, f"AnyMessage reorder: {e}")

    if prefer_code:
        code = anymessage_wait_verification_code(token, aid, timeout_sec=90, log=log)
        if code:
            _offerup_enter_verification_code(serial, code, log=log)
            return {"type": "code", "code": code, "activation_id": aid, "email": em}
        _log_cb(log, "CODE не пришёл — Can't receive → Resend → reorder")
        if _offerup_tap_cant_receive(serial, log=log):
            _pause(0.8)
            _offerup_tap_resend_in_app(serial, log=log)
            _pause(0.38)
            try:
                aid, em = anymessage_reorder_email(token, aid, email=em)
            except Exception as e:
                _log_cb(log, f"reorder повтор: {e}")
            code = anymessage_wait_verification_code(token, aid, timeout_sec=90, log=log)
            if code:
                _offerup_enter_verification_code(serial, code, log=log)
                return {"type": "code", "code": code, "activation_id": aid, "email": em}

    link = ""
    try:
        link = anymessage_wait_verification_link(token, aid, timeout_sec=60, poll_sec=10.0, log=log)
    except Exception:
        link = ""
    if link:
        _open_url_chrome(serial, link, log=log)
        return {"type": "link", "verify_url": link, "activation_id": aid, "email": em}
    return {"type": "pending", "activation_id": aid, "email": em}


# ── APK install ───────────────────────────────────────────────────


def _extract_offerup_apks(apks_path: str, log: Optional[Callable[[str], None]] = None) -> List[str]:
    if not os.path.isfile(apks_path):
        raise FileNotFoundError(apks_path)
    tmp = tempfile.mkdtemp(prefix="mumu_offerup_apks_")
    with zipfile.ZipFile(apks_path, "r") as zf:
        zf.extractall(tmp)
    paths = []
    for root, _, files in os.walk(tmp):
        for fn in files:
            if fn.lower().endswith(".apk"):
                paths.append(os.path.join(root, fn))
    if not paths:
        raise RuntimeError("В .apks нет APK файлов")
    base = [p for p in paths if os.path.basename(p) == "base.apk"]
    rest = sorted(p for p in paths if p not in base)
    ordered = (base + rest) if base else sorted(paths)
    _log_cb(log, f"OfferUp APK: {len(ordered)} split(s)")
    return ordered


def install_proxy_apk(
    serial: str,
    apk_path: str,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    from app import adb_connect_serial, run_adb_serial

    _check_stop(should_stop)
    path = apk_path or _default_proxy_apk()
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    adb_connect_serial(serial)
    _log_cb(log, f"Установка Proxy: {os.path.basename(path)}")
    r = run_adb_serial(serial, ["install", "-r", path])
    if not r or r.returncode != 0:
        err = ((r.stderr or "") + (r.stdout or "")).strip() if r else "adb failed"
        raise RuntimeError(f"Proxy install failed: {err[:400]}")
    _check_stop(should_stop)


def install_offerup_apks(
    serial: str,
    apks_path: str,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    from app import adb_connect_serial, run_adb_serial

    _check_stop(should_stop)
    path = apks_path or _default_offerup_apks()
    apk_list = _extract_offerup_apks(path, log=log)
    adb_connect_serial(serial)
    _log_cb(log, "Установка OfferUp (install-multiple)…")
    r = run_adb_serial(serial, ["install-multiple", "-r"] + apk_list)
    if not r or r.returncode != 0:
        err = ((r.stderr or "") + (r.stdout or "")).strip() if r else "adb failed"
        raise RuntimeError(f"OfferUp install failed: {err[:400]}")
    _check_stop(should_stop)


# ── UI helpers (mirror app.py patterns) ───────────────────────────


def _parse_bounds(bounds: str) -> Optional[Tuple[int, int, int, int]]:
    from app import parse_bounds

    return parse_bounds(bounds)


def _node_center(node: ET.Element) -> Optional[Tuple[int, int]]:
    pr = _parse_bounds(node.get("bounds") or "")
    if not pr:
        return None
    l, t, r, b = pr
    return (l + r) // 2, (t + b) // 2


def _screen_wh(serial: str, root: Optional[ET.Element] = None) -> Tuple[int, int]:
    from app import _adb_wm_size_wh, screen_size, dump_ui_serial

    w, h = _adb_wm_size_wh(serial)
    if w and h:
        return w, h
    if root is None:
        root = dump_ui_serial(serial)
    if root is not None:
        return screen_size(root)
    return 1080, 1920


def _tap(serial: str, x: int, y: int) -> None:
    from app import invalidate_ui_dump_cache, run_adb_serial

    run_adb_serial(serial, ["shell", "input", "tap", str(x), str(y)])
    invalidate_ui_dump_cache(serial)


def _tap_frac(serial: str, xf: float, yf: float, sw: int = 0, sh: int = 0) -> None:
    if not sw or not sh:
        sw, sh = _screen_wh(serial)
    _tap(serial, int(sw * xf), int(sh * yf))


def _find_clickable_by_text(
    root: ET.Element,
    *needles: str,
    sh: int = 0,
    y_min_frac: float = 0.0,
    y_max_frac: float = 0.82,
) -> Optional[Tuple[int, int]]:
    from app import _offerup_find_clickable, _offerup_label

    if not sh:
        sh = 1920
    y_min = int(sh * y_min_frac)
    y_max = int(sh * y_max_frac)
    lows = [n.lower() for n in needles if n]

    def pred(n: ET.Element) -> bool:
        blob = _offerup_label(n).lower()
        if not any(nl in blob for nl in lows):
            return False
        pr = _parse_bounds(n.get("bounds") or "")
        if not pr:
            return True
        cy = (pr[1] + pr[3]) // 2
        return y_min <= cy <= y_max

    return _offerup_find_clickable(root, pred)


def _find_label_tap(
    root: ET.Element,
    *needles: str,
    sh: int = 1920,
    sw: int = 1080,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
    exact: bool = False,
) -> Optional[Tuple[int, int]]:
    """Тап по тексту на экране (даже если узел не clickable)."""
    from app import _offerup_label

    y_min, y_max = int(sh * y_min_frac), int(sh * y_max_frac)
    x_min, x_max = int(sw * x_min_frac), int(sw * x_max_frac)
    lows = [n.lower() for n in needles if n]
    for node in root.iter("node"):
        blob = _offerup_label(node).strip()
        if not blob:
            continue
        low = blob.lower()
        if exact:
            if low not in lows:
                continue
        elif not any(nl in low for nl in lows):
            continue
        pr = _parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        cx = (pr[0] + pr[2]) // 2
        cy = (pr[1] + pr[3]) // 2
        if cx < x_min or cx > x_max or cy < y_min or cy > y_max:
            continue
        return (cx, cy)
    return None


def _find_resource_id_tap(
    root: ET.Element,
    *substrings: str,
    sh: int = 1080,
    sw: int = 1920,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
    require_clickable: bool = True,
) -> Optional[Tuple[int, int]]:
    from app import _offerup_find_tap_by_resource_id

    return _offerup_find_tap_by_resource_id(
        root,
        *substrings,
        scr_w=sw,
        scr_h=sh,
        y_min_frac=y_min_frac,
        y_max_frac=y_max_frac,
        x_min_frac=x_min_frac,
        x_max_frac=x_max_frac,
        require_clickable=require_clickable,
    )


def _wait_tap_resource_id(
    serial: str,
    *substrings: str,
    timeout: float,
    log: Optional[Callable[[str], None]] = None,
    poll: float = 0.10,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
    require_clickable: bool = True,
) -> bool:
    if not substrings:
        return False
    sw, sh = _screen_wh(serial)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        from app import dump_ui_serial

        root = dump_ui_serial(serial)
        if root is not None:
            xy = _find_resource_id_tap(
                root,
                *substrings,
                sh=sh,
                sw=sw,
                y_min_frac=y_min_frac,
                y_max_frac=y_max_frac,
                x_min_frac=x_min_frac,
                x_max_frac=x_max_frac,
                require_clickable=require_clickable,
            )
            if xy:
                _log_cb(log, f"Тап resource-id «{substrings[0]}» {xy}")
                _tap(serial, xy[0], xy[1])
                return True
        _pause(poll)
    return False


def _find_phrase_tap(
    root: ET.Element,
    phrase: str,
    sh: int,
    sw: int,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
) -> Optional[Tuple[int, int]]:
    from app import _offerup_label

    low_phrase = phrase.lower()
    y_min, y_max = int(sh * y_min_frac), int(sh * y_max_frac)
    x_min, x_max = int(sw * x_min_frac), int(sw * x_max_frac)
    for node in root.iter("node"):
        blob = _offerup_label(node).strip().lower()
        if low_phrase not in blob:
            continue
        pr = _parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        cx = (pr[0] + pr[2]) // 2
        cy = (pr[1] + pr[3]) // 2
        if cx < x_min or cx > x_max or cy < y_min or cy > y_max:
            continue
        return (cx, cy)
    return None


def _wait_tap_phrase(
    serial: str,
    phrase: str,
    timeout: float,
    log: Optional[Callable[[str], None]] = None,
    poll: float = 0.10,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
) -> bool:
    sw, sh = _screen_wh(serial)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        from app import dump_ui_serial

        root = dump_ui_serial(serial)
        if root is not None:
            xy = _find_phrase_tap(
                root,
                phrase,
                sh,
                sw,
                y_min_frac=y_min_frac,
                y_max_frac=y_max_frac,
                x_min_frac=x_min_frac,
                x_max_frac=x_max_frac,
            )
            if xy:
                _log_cb(log, f"Тап «{phrase}» {xy}")
                _tap(serial, xy[0], xy[1])
                return True
        _pause(poll)
    return False


def _wait_tap_text(
    serial: str,
    needles: Tuple[str, ...],
    timeout: float,
    log: Optional[Callable[[str], None]] = None,
    poll: float = 0.10,
    y_min_frac: float = 0.0,
    y_max_frac: float = 0.82,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
    exact: bool = False,
    resource_substrings: Tuple[str, ...] = (),
) -> bool:
    from app import dump_ui_serial

    sw, sh = _screen_wh(serial)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root = dump_ui_serial(serial)
        if root is not None:
            if resource_substrings:
                xy_rid = _find_resource_id_tap(
                    root,
                    *resource_substrings,
                    sh=sh,
                    sw=sw,
                    y_min_frac=y_min_frac,
                    y_max_frac=y_max_frac,
                    x_min_frac=x_min_frac,
                    x_max_frac=x_max_frac,
                )
                if xy_rid:
                    _log_cb(log, f"Тап resource-id «{resource_substrings[0]}» {xy_rid}")
                    _tap(serial, xy_rid[0], xy_rid[1])
                    return True
            xy = _find_label_tap(
                root,
                *needles,
                sh=sh,
                sw=sw,
                y_min_frac=y_min_frac,
                y_max_frac=y_max_frac,
                x_min_frac=x_min_frac,
                x_max_frac=x_max_frac,
                exact=exact,
            )
            if not xy:
                xy = _find_clickable_by_text(
                    root, *needles, sh=sh, y_min_frac=y_min_frac, y_max_frac=y_max_frac,
                )
            if xy:
                _log_cb(log, f"Тап «{needles[0]}» {xy}")
                _tap(serial, xy[0], xy[1])
                return True
        _pause(poll)
    return False


def require_ui_step(
    serial: str,
    step_id: str,
    message: str,
    success_fn: Callable[[], bool],
    action_fn: Callable[[], bool],
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    *,
    auto_rounds: int = 2,
) -> None:
    """Выполнить action_fn; если success_fn() ложно — пауза оператора (кнопка «Продолжить»)."""
    from app import OPERATOR_PAUSE_ON_FAIL, operator_pause_and_wait

    attempts: List[dict] = []
    for r in range(max(1, int(auto_rounds))):
        _check_stop(should_stop)
        t0 = time.monotonic()
        try:
            acted = bool(action_fn())
        except Exception as e:
            acted = False
            attempts.append({"round": r + 1, "error": str(e)[:200]})
        else:
            attempts.append({"round": r + 1, "action_ok": acted, "sec": round(time.monotonic() - t0, 2)})
        _pause(0.12)
        if success_fn():
            return

    if not OPERATOR_PAUSE_ON_FAIL:
        raise RuntimeError(message)

    operator_pause_and_wait(
        serial,
        step_id,
        message,
        attempts,
        should_stop=should_stop,
        success_recheck=success_fn,
        log_func=log,
    )


def _clear_focused_field(serial: str) -> None:
    from app import run_adb_serial

    for _ in range(14):
        run_adb_serial(serial, ["shell", "input", "keyevent", "67"])
    _pause(0.05)


def _superproxy_ui_blob(serial: str) -> str:
    from app import dump_ui_serial, _ui_visible_texts

    root = dump_ui_serial(serial)
    if root is None:
        return ""
    return " ".join(_ui_visible_texts(root)).lower()


def _superproxy_on_edit_form(serial: str) -> bool:
    if len(_superproxy_form_edittexts(serial)) >= 2:
        return True
    blob = _superproxy_ui_blob(serial)
    has_server = "сервер" in blob or " server" in blob
    has_port = "порт" in blob
    return has_server and has_port and "протокол" in blob


def _superproxy_blocking_dialog_visible(serial: str) -> bool:
    blob = _superproxy_ui_blob(serial)
    if "предупрежден" in blob:
        return True
    if ("отмен" in blob or "cancel" in blob) and ("ok" in blob or "ок" in blob):
        return True
    if "запрос" in blob and "подключ" in blob:
        return True
    if "vpn" in blob and ("ok" in blob or "ок" in blob):
        return True
    return False


# OK справа внизу (предупреждение Super Proxy + системный VPN-диалог)
_SUPERPROXY_OK_BOTTOM_FRACS: Tuple[Tuple[float, float], ...] = (
    (0.78, 0.915),
    (0.78, 0.88),
    (0.82, 0.87),
    (0.75, 0.92),
    (0.82, 0.91),
    (0.80, 0.93),
)


def _superproxy_vpn_dialog_visible(serial: str) -> bool:
    blob = _superproxy_ui_blob(serial)
    return ("запрос" in blob and "подключ" in blob) or "vpndialogs" in blob


def _superproxy_accept_vpn(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    from app import dump_ui_serial, run_adb_serial

    _log_cb(log, "Super Proxy: ждём VPN «Запрос на подключение»…")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        _check_stop(should_stop)
        if _superproxy_vpn_dialog_visible(serial):
            break
        _pause(0.22)

    if not _superproxy_vpn_dialog_visible(serial):
        return

    sw, sh = _screen_wh(serial)
    root = dump_ui_serial(serial)
    if root is not None:
        xy = _find_resource_id_tap(
            root,
            "android:id/button1",
            "vpndialogs:id/button1",
            sh=sh,
            sw=sw,
            y_min_frac=0.70,
            y_max_frac=0.98,
            x_min_frac=0.45,
        )
        if xy:
            _log_cb(log, f"Super Proxy: VPN OK {xy} (resource-id)")
            _tap(serial, xy[0], xy[1])
            _pause(0.4)
            if not _superproxy_vpn_dialog_visible(serial):
                return
        xy = _find_dialog_ok_right(root, sw, sh)
        if xy:
            _log_cb(log, f"Super Proxy: VPN OK {xy} (UI)")
            _tap(serial, xy[0], xy[1])
            _pause(0.4)
            if not _superproxy_vpn_dialog_visible(serial):
                return

    if _superproxy_vpn_dialog_visible(serial):
        _log_cb(log, "Super Proxy: VPN OK — тап (0.78, 0.88)")
        _tap_frac(serial, 0.78, 0.88, sw, sh)
        _pause(0.35)
    if _superproxy_vpn_dialog_visible(serial):
        run_adb_serial(serial, ["shell", "input", "keyevent", "66"])
        _pause(0.25)


def _find_dialog_ok_right(root: ET.Element, sw: int, sh: int) -> Optional[Tuple[int, int]]:
    """OK справа внизу (не «Отмена» слева)."""
    from app import _offerup_find_clickable, _offerup_label

    y_lo = int(sh * 0.70)
    x_min = int(sw * 0.48)

    btn_hits: List[Tuple[int, int, int]] = []
    for node in root.iter("node"):
        rid = (node.get("resource-id") or "").lower()
        if "button1" not in rid and "button2" not in rid:
            continue
        if node.get("clickable", "false") != "true":
            continue
        pr = _parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        cx = (pr[0] + pr[2]) // 2
        cy = (pr[1] + pr[3]) // 2
        if cx >= x_min and cy >= y_lo:
            btn_hits.append((cx, cy, pr[2]))
    if btn_hits:
        btn_hits.sort(key=lambda t: t[2], reverse=True)
        return (btn_hits[0][0], btn_hits[0][1])

    hits: List[Tuple[int, int, int]] = []
    for node in root.iter("node"):
        text = (node.get("text") or "").strip().lower()
        desc = (node.get("content-desc") or "").strip().lower()
        label = text or desc
        if label not in ("ok", "ок"):
            continue
        pr = _parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        cx = (pr[0] + pr[2]) // 2
        cy = (pr[1] + pr[3]) // 2
        if cx < x_min or cy < y_lo:
            continue
        hits.append((cx, cy, pr[2]))

    if hits:
        hits.sort(key=lambda t: t[2], reverse=True)
        return (hits[0][0], hits[0][1])

    bottom_click: List[Tuple[int, int, int]] = []
    for node in root.iter("node"):
        if node.get("clickable", "false") != "true":
            continue
        blob = _offerup_label(node).lower()
        if "отмен" in blob or "cancel" in blob:
            continue
        pr = _parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        cx = (pr[0] + pr[2]) // 2
        cy = (pr[1] + pr[3]) // 2
        if cx < x_min or cy < y_lo:
            continue
        bottom_click.append((cx, cy, pr[2]))
    if bottom_click:
        bottom_click.sort(key=lambda t: t[2], reverse=True)
        return (bottom_click[0][0], bottom_click[0][1])

    def pred(n: ET.Element) -> bool:
        blob = _offerup_label(n).lower()
        if "отмен" in blob or "cancel" in blob:
            return False
        if blob.strip() not in ("ok", "ок"):
            return False
        pr = _parse_bounds(n.get("bounds") or "")
        if pr and (pr[0] + pr[2]) // 2 < x_min:
            return False
        if pr and (pr[1] + pr[3]) // 2 < y_lo:
            return False
        return True

    return _offerup_find_clickable(root, pred)


def _superproxy_try_tap_dialog_ok_once(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
    label: str = "OK",
    attempts: Optional[List[dict]] = None,
) -> bool:
    from app import dump_ui_serial

    sw, sh = _screen_wh(serial)
    if not _superproxy_blocking_dialog_visible(serial):
        return True

    root = dump_ui_serial(serial)
    if root is not None:
        xy = _find_dialog_ok_right(root, sw, sh)
        if xy:
            _log_cb(log, f"Super Proxy: {label} {xy} (UI)")
            _tap(serial, xy[0], xy[1])
            _pause(0.35)
            if attempts is not None:
                attempts.append({"method": "ui_ok", "xy": list(xy), "label": label})
            if not _superproxy_blocking_dialog_visible(serial):
                return True

    if _superproxy_blocking_dialog_visible(serial):
        for xf, yf in _SUPERPROXY_OK_BOTTOM_FRACS[:3]:
            _log_cb(log, f"Super Proxy: {label} — тап ({xf:.2f}, {yf:.2f})")
            _tap_frac(serial, xf, yf, sw, sh)
            _pause(0.32)
            if attempts is not None:
                attempts.append({"method": "frac", "xf": xf, "yf": yf, "label": label})
            if not _superproxy_blocking_dialog_visible(serial):
                return True
    return not _superproxy_blocking_dialog_visible(serial)


def _superproxy_tap_dialog_ok_right(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
    label: str = "OK",
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    """OK в диалоге Super Proxy; при неудаче — пауза оператора."""
    if not _superproxy_blocking_dialog_visible(serial):
        return True

    def success() -> bool:
        return not _superproxy_blocking_dialog_visible(serial)

    attempts: List[dict] = []

    def action() -> bool:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            _check_stop(should_stop)
            if _superproxy_try_tap_dialog_ok_once(serial, log=log, label=label, attempts=attempts):
                return True
            _pause(0.22)
        return success()

    if success():
        return True

    from app import OPERATOR_PAUSE_ON_FAIL

    if not OPERATOR_PAUSE_ON_FAIL:
        action()
        return success()

    require_ui_step(
        serial,
        "superproxy_dialog_ok",
        f"Не удалось нажать «{label}» в диалоге Super Proxy. Нажмите OK вручную на эмуляторе.",
        success,
        action,
        log=log,
        should_stop=should_stop,
        auto_rounds=1,
    )
    return success()


def _superproxy_dismiss_blocking_dialogs(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
    max_rounds: int = 2,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    for i in range(max_rounds):
        if not _superproxy_blocking_dialog_visible(serial):
            return
        _log_cb(log, "Super Proxy: закрываем диалог (OK)…")
        _superproxy_tap_dialog_ok_right(
            serial,
            log=log,
            label="OK" if i == 0 else "OK повтор",
            should_stop=should_stop,
        )
        _pause(0.35)


def _superproxy_wait_form(
    serial: str,
    timeout_sec: float = 6.0,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _superproxy_on_edit_form(serial):
            return True
        _pause(0.2)
    return False


def _superproxy_tap_add_proxy(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    """Кнопка «Добавить прокси» на списке (не нижняя вкладка «Прокси»)."""
    from app import dump_ui_serial, _offerup_find_clickable, _offerup_label

    sw, sh = _screen_wh(serial)
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        root = dump_ui_serial(serial)
        if root is not None:

            def pred(n: ET.Element) -> bool:
                blob = _offerup_label(n).lower().strip()
                if not blob:
                    return False
                if ("добавить" in blob or "add" in blob) and (
                    "прокси" in blob or "proxy" in blob
                ):
                    pr = _parse_bounds(n.get("bounds") or "")
                    if pr:
                        cy = (pr[1] + pr[3]) // 2
                        if cy > int(sh * 0.93):
                            return False
                    return True
                return False

            xy = _offerup_find_clickable(root, pred)
            if xy:
                _log_cb(log, f"Тап «Добавить прокси» {xy}")
                _tap(serial, xy[0], xy[1])
                return True
        _pause(0.3)

    for xf, yf in ((0.82, 0.86), (0.5, 0.88), (0.78, 0.84)):
        _log_cb(log, f"Super Proxy: тап по FAB ({xf:.2f}, {yf:.2f})")
        _tap_frac(serial, xf, yf, sw, sh)
        _pause(0.45)
        if _superproxy_on_edit_form(serial):
            return True
    return _wait_tap_text(
        serial,
        ("Добавить прокси", "Add proxy", "ДОБАВИТЬ ПРОКСИ"),
        3.0,
        log=log,
        y_max_frac=0.9,
    )


def _superproxy_after_add_proxy(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    """После «Добавить прокси»: ждём диалог → OK внизу справа → форма."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _superproxy_on_edit_form(serial) or len(_superproxy_form_edittexts(serial)) >= 2:
            return True
        if _superproxy_blocking_dialog_visible(serial):
            break
        _pause(0.25)

    if _superproxy_blocking_dialog_visible(serial):
        _superproxy_tap_dialog_ok_right(
            serial, log=log, label="OK после «Добавить»", should_stop=should_stop,
        )
    _pause(0.35)

    if _superproxy_wait_form(serial, 5.0, log=log):
        return True
    return len(_superproxy_form_edittexts(serial)) >= 2


def _superproxy_tap_start_button(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    from app import dump_ui_serial, _offerup_find_clickable, _offerup_label

    sw, sh = _screen_wh(serial)
    deadline = time.monotonic() + 6.0

    def pred(n: ET.Element) -> bool:
        blob = _offerup_label(n).lower()
        if "старт" not in blob and "start" not in blob:
            return False
        pr = _parse_bounds(n.get("bounds") or "")
        if pr:
            cy = (pr[1] + pr[3]) // 2
            if cy < sh * 0.45 or cy > sh * 0.84:
                return False
        return True

    while time.monotonic() < deadline:
        root = dump_ui_serial(serial)
        if root is not None:
            xy = _offerup_find_clickable(root, pred)
            if xy:
                _log_cb(log, f"Super Proxy: Старт {xy}")
                _tap(serial, xy[0], xy[1])
                return True
        _pause(0.3)
    _log_cb(log, "Super Proxy: Старт — тап по центру (0.5, 0.72)")
    _tap_frac(serial, 0.5, 0.72, sw, sh)
    return True


def _superproxy_form_edittexts(serial: str) -> List[Tuple[int, int, int]]:
    """EditText в форме (y, center_x, center_y), без нижней навигации."""
    from app import dump_ui_serial

    root = dump_ui_serial(serial)
    if root is None:
        return []
    _, sh = _screen_wh(serial, root)
    y_lo, y_hi = int(sh * 0.14), int(sh * 0.78)
    out: List[Tuple[int, int, int]] = []
    for node in root.iter("node"):
        cls = (node.get("class") or "").lower()
        if "edittext" not in cls:
            continue
        pr = _parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        cy = (pr[1] + pr[3]) // 2
        if cy < y_lo or cy > y_hi:
            continue
        cx = (pr[0] + pr[2]) // 2
        out.append((cy, cx, cy))
    out.sort(key=lambda t: t[0])
    return out


def _superproxy_fill_server_port(
    serial: str,
    host: str,
    port: str,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    from app import input_text_serial, run_adb_serial

    edits = _superproxy_form_edittexts(serial)
    sw, sh = _screen_wh(serial)
    if len(edits) >= 2:
        if len(edits) >= 3:
            server_xy = (edits[1][1], edits[1][2])
            port_xy = (edits[2][1], edits[2][2])
        else:
            server_xy = (edits[0][1], edits[0][2])
            port_xy = (edits[1][1], edits[1][2])
    else:
        _log_cb(log, "Super Proxy: ввод Сервер/Порт по координатам")
        server_xy = (int(sw * 0.5), int(sh * 0.38))
        port_xy = (int(sw * 0.5), int(sh * 0.46))

    def _fill_field(xy: Tuple[int, int], value: str, label: str) -> None:
        _log_cb(log, f"Super Proxy: {label} → {value}")
        _tap(serial, xy[0], xy[1])
        _pause(0.2)
        for _ in range(10):
            run_adb_serial(serial, ["shell", "input", "keyevent", "67"])
        _pause(0.06)
        if not input_text_serial(value, serial):
            raise RuntimeError(f"Super Proxy: не ввели {label}")

    _fill_field(server_xy, host, "Сервер")
    _pause(0.2)
    _fill_field(port_xy, port, "Порт")


def _wait_ui_contains(
    serial: str,
    needle: str,
    timeout: float,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    from app import dump_ui_serial, _ui_visible_texts

    low = needle.lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _check_stop(should_stop)
        root = dump_ui_serial(serial)
        if root is not None:
            blob = " ".join(_ui_visible_texts(root)).lower()
            if low in blob:
                return True
        _sleep_stop(0.5, should_stop)
    return False


def _type_in_edittext(serial: str, text: str, index: int = 0) -> bool:
    from app import dump_ui_serial, input_text_serial

    root = dump_ui_serial(serial)
    if root is None:
        return False
    edits: List[Tuple[int, Tuple[int, int]]] = []
    for node in root.iter("node"):
        cls = (node.get("class") or "").lower()
        if "edittext" in cls or node.get("focusable") == "true" and node.get("clickable") == "true":
            xy = _node_center(node)
            if xy:
                edits.append((xy[1], xy))
    if not edits:
        return False
    edits.sort(key=lambda x: x[0])
    if index >= len(edits):
        index = 0
    xy = edits[index][1]
    _tap(serial, xy[0], xy[1])
    _pause(0.25)
    return input_text_serial(text, serial)


def _launch_package(serial: str, package: str) -> None:
    from app import adb_connect_serial, run_adb_serial

    adb_connect_serial(serial)
    run_adb_serial(serial, ["shell", "am", "force-stop", package])
    _pause(0.3)
    run_adb_serial(
        serial,
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
    )
    _pause(0.7)


def _open_url_chrome(serial: str, url: str, log: Optional[Callable[[str], None]] = None) -> None:
    from app import adb_connect_serial, run_adb_serial, OFFERUP_CHROME_PACKAGE

    url = (url or "").strip()
    if not url or "offerup.com" not in url.lower():
        raise RuntimeError("Chrome: пустая или неверная ссылка верификации")
    if "confirm-email" in url.lower() and "&token=" not in url:
        raise RuntimeError(f"Chrome: ссылка обрезана (нет token): {url[:120]}")

    _log_cb(log, f"Chrome: verify ({len(url)} симв.): {url}")
    adb_connect_serial(serial)
    shell_url = url.replace("'", "'\"'\"'")
    shell_cmd = (
        "am start --user 0 -a android.intent.action.VIEW "
        f"-d '{shell_url}' -p {OFFERUP_CHROME_PACKAGE}"
    )
    r = run_adb_serial(serial, ["shell", shell_cmd])
    err = ""
    if r:
        err = ((r.stderr or "") + (r.stdout or "")).strip()
    if not r or r.returncode != 0 or "Error" in err:
        from app import offerup_open_listing_in_chrome

        _log_cb(log, "Chrome: am start не удался, fallback…")
        ok = offerup_open_listing_in_chrome(
            url,
            chrome_package=OFFERUP_CHROME_PACKAGE,
            serial=serial,
            chrome_sleep=0.9,
            log_func=log,
        )
        if not ok:
            raise RuntimeError("Не удалось открыть ссылку в Chrome")


def _chrome_page_blob(serial: str) -> str:
    from app import dump_ui_serial, _ui_visible_texts

    root = dump_ui_serial(serial)
    if root is None:
        return ""
    return " ".join(_ui_visible_texts(root)).lower()


def _chrome_email_confirmed_visible(serial: str) -> bool:
    blob = _chrome_page_blob(serial)
    if not blob:
        return False
    return (
        "email confirmed" in blob
        or "e-mail confirmed" in blob
        or ("подтвержд" in blob and "email" in blob)
        or ("подтвержд" in blob and "почт" in blob)
    )


def _chrome_reload_page(serial: str, url: str, log: Optional[Callable[[str], None]] = None) -> None:
    from app import run_adb_serial

    url = (url or "").strip()
    _log_cb(log, "Chrome: обновление страницы (F5)…")
    run_adb_serial(serial, ["shell", "input", "keyevent", "82"])
    _pause(0.15)
    run_adb_serial(serial, ["shell", "input", "keyevent", "61"])
    _pause(0.08)
    run_adb_serial(serial, ["shell", "input", "keyevent", "61"])
    _pause(0.08)
    run_adb_serial(serial, ["shell", "input", "keyevent", "66"])
    _pause(0.35)
    if not _chrome_email_confirmed_visible(serial) and url:
        _open_url_chrome(serial, url, log=log)


def wait_chrome_email_confirmed(
    serial: str,
    verify_url: str,
    *,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    reload_after_sec: float = 20.0,
    max_wait_sec: float = 120.0,
) -> None:
    """Ждём «Email confirmed» в Chrome; если долго грузит — обновляем страницу."""
    deadline = time.monotonic() + max_wait_sec
    last_reload = time.monotonic()
    while time.monotonic() < deadline:
        _check_stop(should_stop)
        if _chrome_email_confirmed_visible(serial):
            _log_cb(log, "Chrome: Email confirmed — почта подтверждена")
            return
        if mumu_emulator_startup_failed(serial):
            raise RuntimeError("MuMu: «Ошибка запуска» во время подтверждения почты")
        now = time.monotonic()
        if now - last_reload >= reload_after_sec:
            _chrome_reload_page(serial, verify_url, log=log)
            last_reload = now
        _pause(0.45)
    raise RuntimeError("Chrome: не дождались «Email confirmed» на странице")


# ── Super Proxy flow ────────────────────────────────────────────────


def setup_super_proxy(
    serial: str,
    proxy_host: str,
    proxy_port: str,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    _check_stop(should_stop)
    host = (proxy_host or "").strip()
    port = (proxy_port or "").strip()
    if not host or not port:
        raise RuntimeError("Укажите IP и порт прокси")

    _log_cb(log, "Super Proxy: запуск…")
    _launch_package(serial, PROXY_PACKAGE)
    _sleep_stop(1.0, should_stop)
    _check_stop(should_stop)
    _superproxy_dismiss_blocking_dialogs(serial, log=log, should_stop=should_stop)

    if not _superproxy_on_edit_form(serial):
        _log_cb(log, "Super Proxy: «Добавить прокси»…")

        def _add_ok() -> bool:
            return _superproxy_on_edit_form(serial) or len(_superproxy_form_edittexts(serial)) >= 2

        def _add_action() -> bool:
            if _superproxy_tap_add_proxy(serial, log=log):
                return bool(_superproxy_after_add_proxy(serial, log=log, should_stop=should_stop))
            return _add_ok()

        from app import OPERATOR_PAUSE_ON_FAIL

        if OPERATOR_PAUSE_ON_FAIL:
            require_ui_step(
                serial,
                "superproxy_add_proxy",
                "Не удалось открыть «Добавить прокси». Нажмите кнопку вручную.",
                _add_ok,
                _add_action,
                log=log,
                should_stop=should_stop,
            )
        else:
            if not _superproxy_tap_add_proxy(serial, log=log):
                raise RuntimeError("Кнопка «Добавить прокси» не найдена")
            if not _superproxy_after_add_proxy(serial, log=log, should_stop=should_stop):
                raise RuntimeError("Super Proxy: форма Сервер/Порт не открылась")

    _superproxy_fill_server_port(serial, host, port, log=log)
    _pause(0.15)
    def _save_ok() -> bool:
        return _superproxy_on_edit_form(serial) or not _superproxy_blocking_dialog_visible(serial)

    def _save_action() -> bool:
        if _wait_tap_text(
            serial, ("Save", "save", "сохран"), 2.0, log=log, y_min_frac=0.0, y_max_frac=0.18,
        ):
            return True
        sw, sh = _screen_wh(serial)
        _tap_frac(serial, 0.92, 0.07, sw, sh)
        _log_cb(log, "Super Proxy: сохранить (иконка справа сверху)")
        return True

    from app import OPERATOR_PAUSE_ON_FAIL

    if OPERATOR_PAUSE_ON_FAIL:
        require_ui_step(
            serial,
            "superproxy_save",
            "Не удалось нажать «Сохранить» в Super Proxy. Сохраните профиль вручную.",
            lambda: True,
            _save_action,
            log=log,
            should_stop=should_stop,
        )
    else:
        _save_action()
    _pause(0.35)

    _check_stop(should_stop)

    def _start_ok() -> bool:
        blob = _superproxy_ui_blob(serial)
        return "подключ" in blob or "connected" in blob or not _superproxy_on_edit_form(serial)

    def _start_action() -> bool:
        return _superproxy_tap_start_button(serial, log=log)

    if OPERATOR_PAUSE_ON_FAIL:
        require_ui_step(
            serial,
            "superproxy_start",
            "Не удалось нажать «Старт» в Super Proxy. Запустите прокси вручную.",
            _start_ok,
            _start_action,
            log=log,
            should_stop=should_stop,
        )
    else:
        _superproxy_tap_start_button(serial, log=log)
    _check_stop(should_stop)
    _superproxy_accept_vpn(serial, log=log, should_stop=should_stop)

    _log_cb(log, "Super Proxy: включён")


# ── OfferUp signup flow ───────────────────────────────────────────

_OFFERUP_SIGNUP_CATEGORIES: Tuple[str, ...] = (
    "Furniture",
    "Electronics",
    "Home goods",
    "Video games",
    "Home improvement",
    "Cars & trucks",
    "Sports",
    "Fashion",
    "Appliances",
    "Tools",
    "Toys",
    "Books",
)


def _offerup_find_zip_field(
    root: ET.Element,
    sh: int,
) -> Optional[Tuple[int, int]]:
    """Поле «Enter ZIP code» под «Or», не кнопка «Get my location»."""
    from app import _offerup_label

    y_min = int(sh * 0.52)
    y_max = int(sh * 0.72)
    for node in root.iter("node"):
        blob = _offerup_label(node).lower()
        if not blob:
            continue
        if "get my location" in blob or "get location" in blob:
            continue
        if "enter zip" not in blob and "edittext" not in (node.get("class") or "").lower():
            continue
        xy = _node_center(node)
        if xy and y_min <= xy[1] <= y_max:
            return xy
    for node in root.iter("node"):
        cls = (node.get("class") or "").lower()
        if "edittext" not in cls:
            continue
        xy = _node_center(node)
        if xy and y_min <= xy[1] <= y_max:
            return xy
    return None


def _offerup_fill_zip_and_next(
    serial: str,
    zip_code: str,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    _check_stop(should_stop)
    from app import dump_ui_serial, input_text_serial

    sw, sh = _screen_wh(serial)
    if not (
        _wait_ui_contains(serial, "enter zip", 8.0, should_stop=should_stop)
        or _wait_ui_contains(serial, "searching", 8.0, should_stop=should_stop)
    ):
        _log_cb(log, "OfferUp: экран ZIP не найден, пропуск")
        return

    _log_cb(log, f"OfferUp: ввод ZIP {zip_code}")
    try:
        from mumu_rid_optimizations import offerup_onboarding_tap_next, offerup_onboarding_tap_zip

        if offerup_onboarding_tap_zip(serial, zip_code, log=log):
            _pause(0.35)
            _log_cb(log, "OfferUp: ждём проверку ZIP…")
            for _ in range(10):
                _check_stop(should_stop)
                blob = _superproxy_ui_blob(serial)
                if any(
                    m in blob
                    for m in ("kentucky", "кентукки", "cromwell", "кромвель", ",")
                ):
                    break
                _sleep_stop(0.25, should_stop)
            if offerup_onboarding_tap_next(serial, log=log):
                _pause(0.6)
                return
    except ImportError:
        pass

    field_xy: Optional[Tuple[int, int]] = None
    root = dump_ui_serial(serial)
    if root is not None:
        field_xy = _offerup_find_zip_field(root, sh)

    if not field_xy:
        field_xy = (int(sw * 0.5), int(sh * 0.58))
        _log_cb(log, f"OfferUp: поле ZIP по координатам {field_xy}")

    _tap(serial, field_xy[0], field_xy[1])
    _pause(0.3)
    _clear_focused_field(serial)
    if not input_text_serial(zip_code, serial):
        raise RuntimeError(f"OfferUp: не ввели ZIP {zip_code}")

    _pause(0.35)
    enter_xy: Optional[Tuple[int, int]] = None
    root = dump_ui_serial(serial)
    if root is not None:
        enter_xy = _find_label_tap(
            root,
            "Enter",
            sh=sh,
            sw=sw,
            y_min_frac=0.50,
            y_max_frac=0.64,
            x_min_frac=0.72,
            x_max_frac=0.96,
            exact=True,
        )
        if not enter_xy:
            enter_xy = _find_phrase_tap(
                root,
                "enter",
                sh,
                sw,
                y_min_frac=0.50,
                y_max_frac=0.64,
                x_min_frac=0.72,
                x_max_frac=0.96,
            )
    if not enter_xy:
        enter_xy = (field_xy[0] + int(sw * 0.32), field_xy[1])
    _log_cb(log, f"OfferUp: Enter {enter_xy}")
    _tap(serial, enter_xy[0], enter_xy[1])
    _pause(0.25)
    from app import run_adb_serial

    run_adb_serial(serial, ["shell", "input", "keyevent", "66"])

    _log_cb(log, "OfferUp: ждём проверку ZIP…")
    resolved = False
    for _ in range(10):
        _check_stop(should_stop)
        blob = _superproxy_ui_blob(serial)
        if any(
            m in blob
            for m in (
                "kentucky",
                "кентукки",
                "cromwell",
                "кромвель",
                ",",
            )
        ):
            resolved = True
            break
        _sleep_stop(0.25, should_stop)
    if not resolved:
        _sleep_stop(1.2, should_stop)

    if not _wait_tap_text(
        serial,
        ("Next",),
        5.0,
        log=log,
        y_min_frac=0.86,
        y_max_frac=0.99,
        exact=True,
        resource_substrings=("button.next", ".next", "signup.next", "onboarding.next"),
    ):
        _tap_frac(serial, 0.5, 0.945, sw, sh)
        _log_cb(log, "OfferUp: Next по координатам")
    _pause(0.6)


def _offerup_pick_categories(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    from app import dump_ui_serial

    try:
        from mumu_rid_optimizations import offerup_onboarding_skip_categories

        if offerup_onboarding_skip_categories(serial, log=log):
            _pause(0.35)
            return
    except ImportError:
        pass

    if not (
        _wait_ui_contains(serial, "personalize", 5.0)
        or _wait_ui_contains(serial, "categories", 5.0)
        or _wait_ui_contains(serial, "select 3", 5.0)
    ):
        return

    sw, sh = _screen_wh(serial)
    _log_cb(log, "OfferUp: выбор категорий (3–4)…")
    root = dump_ui_serial(serial)
    picks: List[Tuple[int, int]] = []
    if root is not None:
        for name in _OFFERUP_SIGNUP_CATEGORIES:
            xy = _find_label_tap(
                root,
                name,
                sh=sh,
                sw=sw,
                y_min_frac=0.20,
                y_max_frac=0.78,
            )
            if xy:
                picks.append(xy)

    random.shuffle(picks)
    count = random.randint(3, min(4, len(picks))) if picks else 0
    if count == 0:
        for xf, yf in ((0.32, 0.36), (0.68, 0.36), (0.50, 0.44), (0.35, 0.48)):
            _tap_frac(serial, xf, yf, sw, sh)
            _pause(0.3)
    else:
        for xy in picks[:count]:
            _tap(serial, xy[0], xy[1])
            _pause(0.32)

    _pause(0.25)
    if not _wait_tap_text(
        serial,
        ("Apply",),
        2.5,
        log=log,
        y_min_frac=0.88,
        y_max_frac=0.99,
        exact=True,
        resource_substrings=("apply", "button.apply", "categories.apply"),
    ):
        _tap_frac(serial, 0.5, 0.94, sw, sh)
        _log_cb(log, "OfferUp: Apply — координаты")
    _pause(0.35)


def _offerup_find_signup_button(
    root: ET.Element, sh: int, sw: int,
) -> Optional[Tuple[int, int]]:
    """Зелёная кнопка «Sign up» под логотипом (выше «Log in», не заголовок)."""
    from app import _offerup_find_clickable, _offerup_label

    y_min, y_max = int(sh * 0.28), int(sh * 0.44)
    hits: List[Tuple[int, int, int]] = []

    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        desc = (node.get("content-desc") or "").strip()
        blob = (text or desc).lower()
        if blob != "sign up":
            continue
        if "/" in text or "/" in desc or "log in" in blob:
            continue
        xy = _node_center(node)
        if xy and y_min <= xy[1] <= y_max:
            hits.append((xy[1], xy[0], xy[1]))

    if hits:
        hits.sort(key=lambda t: t[0])
        return (hits[0][1], hits[0][2])

    def pred(n: ET.Element) -> bool:
        blob = _offerup_label(n).strip().lower()
        if blob != "sign up":
            return False
        pr = _parse_bounds(n.get("bounds") or "")
        if pr and (pr[1] + pr[3]) // 2 < y_min:
            return False
        return True

    xy = _offerup_find_clickable(root, pred)
    if xy and y_min <= xy[1] <= y_max:
        return xy

    login_y: Optional[int] = None
    for node in root.iter("node"):
        t = (node.get("text") or "").strip().lower()
        if t == "log in":
            xy = _node_center(node)
            if xy:
                login_y = xy[1]
                break
    if login_y is not None:
        return (sw // 2, max(y_min, login_y - int(sh * 0.09)))

    return None


def _offerup_on_signup_form(serial: str) -> bool:
    blob = _superproxy_ui_blob(serial)
    return "email address" in blob or "agree & sign" in blob or (
        "name" in blob and "password" in blob
    )


def _offerup_signup_edittexts(
    root: ET.Element, sh: int, sw: int,
) -> List[Tuple[int, int]]:
    """EditText формы регистрации (не верхняя панель / не поиск)."""
    y_min, y_max = int(sh * 0.30), int(sh * 0.76)
    x_min = int(sw * 0.18)
    out: List[Tuple[int, Tuple[int, int]]] = []
    for node in root.iter("node"):
        cls = (node.get("class") or "").lower()
        if "edittext" not in cls:
            continue
        xy = _node_center(node)
        if not xy:
            continue
        if xy[1] < y_min or xy[1] > y_max or xy[0] < x_min:
            continue
        out.append((xy[1], xy))
    out.sort(key=lambda t: t[0])
    return [xy for _, xy in out]


def _offerup_field_below_label(
    root: ET.Element,
    label_parts: Tuple[str, ...],
    sh: int,
    sw: int,
    y_lo_frac: float,
    y_hi_frac: float,
) -> Optional[Tuple[int, int]]:
    from app import _offerup_label

    y_lo, y_hi = int(sh * y_lo_frac), int(sh * y_hi_frac)
    label_y: Optional[int] = None
    for node in root.iter("node"):
        blob = _offerup_label(node).lower()
        if not any(p in blob for p in label_parts):
            continue
        xy = _node_center(node)
        if xy and y_lo <= xy[1] <= y_hi:
            label_y = xy[1]
            break
    if label_y is None:
        return None
    best: Optional[Tuple[int, int, int]] = None
    for node in root.iter("node"):
        if "edittext" not in (node.get("class") or "").lower():
            continue
        xy = _node_center(node)
        if not xy or xy[1] <= label_y or xy[0] < int(sw * 0.18):
            continue
        dy = xy[1] - label_y
        if dy > int(sh * 0.14):
            continue
        if best is None or dy < best[0]:
            best = (dy, xy[0], xy[1])
    if best:
        return (best[1], best[2])
    return None


_OFFERUP_SIGNUP_FIELD_FRAC: Dict[str, Tuple[float, float]] = {
    "name": (0.52, 0.38),
    "email": (0.52, 0.50),
    "password": (0.52, 0.63),
}


def _offerup_fill_signup_field(
    serial: str,
    field: str,
    value: str,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    from app import dump_ui_serial, input_text_serial

    sw, sh = _screen_wh(serial)
    labels = {
        "name": ("name",),
        "email": ("email address", "email"),
        "password": ("password",),
    }
    y_ranges = {
        "name": (0.30, 0.46),
        "email": (0.44, 0.58),
        "password": (0.56, 0.72),
    }
    y_lo, y_hi = y_ranges[field]

    rid_patterns = {
        "name": ("name", "fullname", "displayname", "first_name"),
        "email": ("email", "identifier", "username"),
        "password": ("password", "passwd"),
    }

    root = dump_ui_serial(serial)
    xy: Optional[Tuple[int, int]] = None
    if root is not None:
        xy = _find_resource_id_tap(
            root,
            *rid_patterns.get(field, ()),
            sh=sh,
            sw=sw,
            y_min_frac=y_lo,
            y_max_frac=y_hi,
            require_clickable=False,
        )
        if not xy:
            xy = _offerup_field_below_label(root, labels[field], sh, sw, y_lo, y_hi)
        if not xy:
            edits = _offerup_signup_edittexts(root, sh, sw)
            idx = {"name": 0, "email": 1, "password": 2}.get(field, 0)
            if idx < len(edits):
                xy = edits[idx]

    if not xy:
        xf, yf = _OFFERUP_SIGNUP_FIELD_FRAC[field]
        xy = (int(sw * xf), int(sh * yf))

    _log_cb(log, f"OfferUp: поле {field} → {value[:40]}{'…' if len(value) > 40 else ''} @ {xy}")
    _tap(serial, xy[0], xy[1])
    _pause(0.35)
    _clear_focused_field(serial)
    if not input_text_serial(value, serial):
        raise RuntimeError(f"OfferUp: не ввели {field}")
    _pause(0.3)


def _offerup_fill_signup_form(
    serial: str,
    full_name: str,
    email: str,
    password: str,
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    _check_stop(should_stop)
    if not _wait_ui_contains(serial, "email address", 12.0, should_stop=should_stop):
        if not _offerup_on_signup_form(serial):
            raise RuntimeError("OfferUp: форма регистрации не открылась")
    _pause(0.5)
    _offerup_fill_signup_field(serial, "name", full_name, log=log)
    _check_stop(should_stop)
    _offerup_fill_signup_field(serial, "email", email, log=log)
    _check_stop(should_stop)
    _offerup_fill_signup_field(serial, "password", password, log=log)
    _check_stop(should_stop)

    sw, sh = _screen_wh(serial)
    if not _wait_tap_phrase(
        serial,
        "agree & sign up",
        5.0,
        log=log,
        y_min_frac=0.80,
        y_max_frac=0.93,
    ):
        if not _wait_tap_text(
            serial,
            ("Agree",),
            3.0,
            log=log,
            y_min_frac=0.80,
            y_max_frac=0.93,
            exact=False,
            resource_substrings=("agree", "signup", "sign_up", "submit"),
        ):
            _tap_frac(serial, 0.5, 0.87, sw, sh)
            _log_cb(log, "OfferUp: Agree & Sign up — координаты")


def _offerup_auth_email_then_signup(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
) -> None:
    """1) Continue with email → 2) Sign up (как на скринах)."""
    from app import dump_ui_serial

    sw, sh = _screen_wh(serial)
    _log_cb(log, "OfferUp: Continue with email…")
    _email_rid_ok = False
    try:
        from mumu_rid_optimizations import offerup_tap_rid

        _email_rid_ok = offerup_tap_rid(
            serial,
            "email_button",
            log=log,
            timeout=4.0,
            y_min_frac=0.58,
            y_max_frac=0.82,
        )
    except ImportError:
        pass
    if not _email_rid_ok and not _wait_tap_resource_id(
        serial,
        "continue_with_email",
        "continuewithemail",
        "continue.email",
        "auth.email",
        timeout=3.0,
        log=log,
        y_min_frac=0.58,
        y_max_frac=0.82,
    ) and not _wait_tap_phrase(
        serial,
        "continue with email",
        12.0,
        log=log,
        y_min_frac=0.58,
        y_max_frac=0.82,
    ):
        _tap_frac(serial, 0.5, 0.72, sw, sh)
        _log_cb(log, "OfferUp: Continue with email — координаты")
    _pause(0.7)

    _log_cb(log, "OfferUp: Sign up…")
    try:
        from mumu_rid_optimizations import offerup_tap_rid

        if offerup_tap_rid(
            serial,
            "signup_button",
            log=log,
            timeout=5.0,
            y_min_frac=0.25,
            y_max_frac=0.50,
        ):
            _pause(0.6)
            if _offerup_on_signup_form(serial):
                return
    except ImportError:
        pass
    signup_fracs = ((0.5, 0.35), (0.5, 0.34), (0.5, 0.38))
    deadline = time.monotonic() + 12.0
    tapped = False
    while time.monotonic() < deadline:
        if _offerup_on_signup_form(serial):
            return
        root = dump_ui_serial(serial)
        if root is not None:
            xy = _offerup_find_signup_button(root, sh, sw)
            if xy:
                _log_cb(log, f"OfferUp: Sign up {xy}")
                _tap(serial, xy[0], xy[1])
                tapped = True
                _pause(0.6)
                if _offerup_on_signup_form(serial):
                    return
        _pause(0.28)

    for xf, yf in signup_fracs:
        _log_cb(log, f"OfferUp: Sign up — тап ({xf:.2f}, {yf:.2f})")
        _tap_frac(serial, xf, yf, sw, sh)
        _pause(0.55)
        if _offerup_on_signup_form(serial):
            return

    if not tapped:
        _log_cb(log, "OfferUp: Sign up — не открылся экран регистрации")


_MUMU_STORE_MARKERS = (
    "mumu store",
    "магазин mumu",
    "mumu market",
    "app center",
    "центр приложений",
    "netease game center",
    "game center",
    "download apps",
    "скачать приложения",
    "recommended apps",
    "рекомендуемые",
    "mumu play",
    "netease",
)

_MUMU_STORE_PKG_FRAGMENTS = (
    "mumu.store",
    "netease.mumu",
    "mumu.android",
    "mumu.launcher",
)


def _mumu_store_ui_blob(serial: str) -> str:
    from app import dump_ui_serial, _ui_visible_texts

    root = dump_ui_serial(serial)
    if root is None:
        return ""
    parts = list(_ui_visible_texts(root))
    try:
        for el in root.iter():
            pkg = (el.attrib.get("package") or "").lower()
            if pkg:
                parts.append(pkg)
    except Exception:
        pass
    return " ".join(parts).lower()


def mumu_store_likely_visible(serial: str) -> bool:
    blob = _mumu_store_ui_blob(serial)
    if not blob:
        return False
    if "ошибка запуска" in blob or "startup error" in blob:
        return True
    if any(m in blob for m in _MUMU_STORE_MARKERS):
        return True
    return any(p in blob for p in _MUMU_STORE_PKG_FRAGMENTS)


def dismiss_mumu_store_popup(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
    *,
    force: bool = False,
) -> bool:
    """Закрыть магазин MuMu / ошибку запуска. Возвращает True, если что-то закрывали."""
    from app import run_adb_serial

    blob = _mumu_store_ui_blob(serial)
    if not blob and not force:
        return False
    if "ошибка запуска" in blob or "startup error" in blob:
        _log_cb(log, "MuMu: «Ошибка запуска» — Back")
        run_adb_serial(serial, ["shell", "input", "keyevent", "4"])
        _pause(0.25)
        return True
    if not force and not any(m in blob for m in _MUMU_STORE_MARKERS):
        if not any(p in blob for p in _MUMU_STORE_PKG_FRAGMENTS):
            return False

    _log_cb(log, "MuMu: закрываем магазин…")
    for pkg in MUMU_STORE_PACKAGES:
        run_adb_serial(serial, ["shell", "am", "force-stop", pkg])
    run_adb_serial(serial, ["shell", "input", "keyevent", "4"])
    _pause(0.2)
    run_adb_serial(serial, ["shell", "input", "keyevent", "4"])
    _wait_tap_text(
        serial,
        (
            "Close",
            "Закрыть",
            "Skip",
            "Пропустить",
            "Not now",
            "Не сейчас",
            "Later",
            "Позже",
            "Cancel",
            "Отмена",
            "×",
            "✕",
            "Dismiss",
        ),
        2.0,
        log=log,
        y_max_frac=0.92,
    )
    run_adb_serial(serial, ["shell", "input", "keyevent", "3"])
    _pause(0.15)
    run_adb_serial(serial, ["shell", "input", "keyevent", "4"])
    return True


def dismiss_mumu_store_aggressive(
    serial: str,
    log: Optional[Callable[[str], None]] = None,
    *,
    rounds: int = 6,
) -> None:
    """Несколько попыток закрыть магазин (после launch ADB часто всплывает снова)."""
    for i in range(max(1, int(rounds))):
        if not mumu_store_likely_visible(serial) and i > 0:
            break
        dismiss_mumu_store_popup(serial, log=log, force=(i == 0))
        _pause(0.35)


def run_offerup_signup(
    serial: str,
    cfg: Dict[str, Any],
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, str]:
    _check_stop(should_stop)
    zip_code = str(cfg.get("zip_code") or "42333").strip()
    display_name = resolve_signup_display_name(cfg)
    _log_cb(log, f"Имя регистрации: {display_name}")
    first, last = split_display_name(display_name)
    password = str(cfg.get("signup_password") or "").strip() or generate_signup_password()

    token = str(cfg.get("anymessage_token") or "").strip()
    domain = str(cfg.get("anymessage_domain") or "gmx.com").strip()
    if not token:
        raise RuntimeError("Нужен токен AnyMessage (ANYMESSAGE_TOKEN)")

    _log_cb(log, "AnyMessage: заказ почты…")
    activation_id, email = anymessage_order_email(token, domain=domain)
    _log_cb(log, f"Почта: {email} (id={activation_id})")

    _log_cb(log, "OfferUp: запуск…")
    _launch_package(serial, OFFERUP_PACKAGE)
    sw, sh = _screen_wh(serial)

    _offerup_fill_zip_and_next(serial, zip_code, log=log, should_stop=should_stop)
    _check_stop(should_stop)
    _offerup_pick_categories(serial, log=log)
    _check_stop(should_stop)

    # Account → Sign in
    if not _wait_tap_text(
        serial,
        ("Account",),
        8.0,
        log=log,
        y_min_frac=0.90,
        y_max_frac=0.99,
    ):
        _tap_frac(serial, 0.90, 0.965, sw, sh)
    _pause(0.6)
    _offerup_auth_email_then_signup(serial, log=log)

    full_name = f"{first} {last}".strip()
    _log_cb(log, f"OfferUp: регистрация {full_name}")
    _offerup_fill_signup_form(
        serial, full_name, email, password, log=log, should_stop=should_stop,
    )
    _pause(0.75)

    _log_cb(log, "AnyMessage: ждём письмо верификации…")
    poll_sec = float(cfg.get("email_poll_sec") or 10.0)
    verify_url = anymessage_wait_verification_link(
        token,
        activation_id,
        timeout_sec=float(cfg.get("email_wait_sec") or 180),
        poll_sec=poll_sec,
        log=log,
        should_stop=should_stop,
    )
    _open_url_chrome(serial, verify_url, log=log)
    _pause(0.6)
    wait_chrome_email_confirmed(
        serial,
        verify_url,
        log=log,
        should_stop=should_stop,
        reload_after_sec=float(cfg.get("chrome_reload_sec") or 20.0),
        max_wait_sec=float(cfg.get("chrome_confirm_wait_sec") or 120.0),
    )

    return {
        "email": email,
        "password": password,
        "name": full_name,
        "activation_id": activation_id,
        "verify_url": verify_url,
        "email_confirmed": True,
        "onboarding_complete": True,
    }


# ── Full pipeline ─────────────────────────────────────────────────


def run_onboarding_pipeline(
    serial: str,
    cfg: Dict[str, Any],
    log: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    _check_stop(should_stop)
    skip_proxy = bool(cfg.get("skip_proxy_setup") or cfg.get("proxy_already_configured"))

    if cfg.get("install_apks", True):
        if not skip_proxy:
            install_proxy_apk(
                serial,
                str(cfg.get("proxy_apk") or _default_proxy_apk()),
                log=log,
                should_stop=should_stop,
            )
        _check_stop(should_stop)
        install_offerup_apks(
            serial,
            str(cfg.get("offerup_apks") or _default_offerup_apks()),
            log=log,
            should_stop=should_stop,
        )

    _check_stop(should_stop)
    if skip_proxy:
        _log_cb(log, "Super Proxy: пропуск — прокси уже настроен, старт с OfferUp")
    else:
        setup_super_proxy(
            serial,
            str(cfg.get("proxy_host") or DEFAULT_PROXY_HOST),
            str(cfg.get("proxy_port") or ""),
            log=log,
            should_stop=should_stop,
        )

    _check_stop(should_stop)
    result = run_offerup_signup(serial, cfg, log=log, should_stop=should_stop)
    result["serial"] = serial
    return result


# ── MuMuManager (optional) ────────────────────────────────────────


def find_mumu_manager_exe() -> str:
    try:
        from app import MUMU_MANAGER_EXE

        if MUMU_MANAGER_EXE and os.path.isfile(MUMU_MANAGER_EXE):
            return MUMU_MANAGER_EXE
    except Exception:
        pass
    env = (os.environ.get("MUMU_MANAGER_EXE") or "").strip()
    if env and os.path.isfile(env):
        return env
    try:
        from app import get_adb, ADB_PATH

        for base in (get_adb(), ADB_PATH):
            if base and os.path.isfile(base):
                shell_dir = os.path.dirname(os.path.abspath(base))
                for name in ("MuMuManager.exe", "mumumanager.exe"):
                    p = os.path.join(shell_dir, name)
                    if os.path.isfile(p):
                        return p
    except Exception:
        pass
    candidates = [
        r"D:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
        r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
        r"E:\MuMuPlayerGlobal\nx_device\12.0\shell\MuMuManager.exe",
        r"E:\MuMuPlayerGlobal\nx_main\MuMuManager.exe",
        r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\MuMuManager.exe",
        r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\MuMuManager.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ""


def _parse_mumu_json_blob(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def parse_create_vm_indices(output: str) -> List[int]:
    """Ответ create: {"5": {"errcode": 0, "errmsg": ""}}."""
    data = _parse_mumu_json_blob(output)
    indices: List[int] = []
    for key, val in data.items():
        if not str(key).isdigit():
            continue
        if isinstance(val, dict) and int(val.get("errcode", -1)) == 0:
            indices.append(int(key))
    return sorted(set(indices))


def mumu_manager_run(args: List[str], timeout: float = 180.0) -> dict:
    exe = find_mumu_manager_exe()
    if not exe:
        raise RuntimeError(
            "MuMuManager.exe не найден. Задайте MUMU_MANAGER_EXE или положите рядом с adb.exe в shell."
        )
    cmd = [exe] + list(args)
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    return {
        "ok": r.returncode == 0,
        "returncode": r.returncode,
        "stdout": r.stdout or "",
        "stderr": r.stderr or "",
        "output": out,
        "command": " ".join(cmd),
    }


def mumu_set_vertical(vmindex: int, log: Optional[Callable[[str], None]] = None) -> None:
    _log_cb(log, f"MuMu #{vmindex}: вертикальный режим (phone.1)")
    res = mumu_manager_run(
        ["setting", "-v", str(vmindex), "-k", "resolution_mode", "-val", MUMU_VERTICAL_RESOLUTION],
        timeout=60,
    )
    if not res["ok"]:
        _log_cb(log, f"setting resolution_mode: {res['output'][:200]}")


def mumu_rename_player(vmindex: int, name: str, log: Optional[Callable[[str], None]] = None) -> None:
    nm = (name or "").strip()
    if not nm:
        return
    _log_cb(log, f"MuMu #{vmindex}: имя «{nm[:40]}»")
    mumu_manager_run(["rename", "-v", str(vmindex), "-n", nm[:80]], timeout=60)


def mumu_launch_player(vmindex: int, log: Optional[Callable[[str], None]] = None) -> None:
    _log_cb(log, f"MuMu #{vmindex}: запуск…")
    res = mumu_manager_run(["control", "-v", str(vmindex), "launch"], timeout=120)
    if not res["ok"]:
        raise RuntimeError(f"MuMu launch #{vmindex} failed: {res['output'][:400]}")


def mumu_shutdown_player(vmindex: int, log: Optional[Callable[[str], None]] = None) -> None:
    _log_cb(log, f"MuMu #{vmindex}: остановка…")
    mumu_manager_run(["control", "-v", str(vmindex), "shutdown"], timeout=90)


def mumu_get_player_info(vmindex: int) -> dict:
    res = mumu_manager_run(["info", "-v", str(vmindex)], timeout=45)
    data = _parse_mumu_json_blob(res.get("output") or "")
    if data:
        return data
    return {}


def mumu_session_label(vmindex: int) -> str:
    """Имя как в списке MuMu: Android device-N (без rename)."""
    info = mumu_get_player_info(vmindex)
    name = str(info.get("name") or "").strip()
    if name and "android" in name.lower():
        return name
    return f"Android device-{vmindex}"


def mumu_apply_vertical_after_boot(
    vmindex: int,
    log: Optional[Callable[[str], None]] = None,
) -> str:
    """
    Сначала обычный первый запуск (без смены resolution до boot),
    затем shutdown → phone.1 → снова launch.
    """
    _log_cb(log, f"MuMu #{vmindex}: первый запуск (дефолтные настройки)…")
    mumu_launch_player(vmindex, log=log)
    _pause(4.0)
    adb = wait_adb_for_vm(vmindex, timeout_sec=180, log=log)
    if not adb:
        _log_cb(log, f"MuMu #{vmindex}: ADB на первом запуске не найден")
        return ""
    _log_cb(log, f"MuMu #{vmindex}: ADB ок, переключаем в вертикальный режим…")
    mumu_shutdown_player(vmindex, log=log)
    _pause(1.6)
    mumu_set_vertical(vmindex, log=log)
    _pause(0.55)
    mumu_launch_player(vmindex, log=log)
    _pause(3.0)
    return wait_adb_for_vm(vmindex, timeout_sec=120, log=log)


def wait_adb_for_vm(
    vmindex: int,
    *,
    timeout_sec: float = 120.0,
    log: Optional[Callable[[str], None]] = None,
) -> str:
    from app import discover_mumu_ports

    needles = [
        f"android device-{vmindex}".lower(),
        f"device-{vmindex}",
        f"android device {vmindex}".lower(),
    ]
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        items = discover_mumu_ports(probe=True)
        for it in items:
            if it.get("reachable") is not True:
                continue
            sess = (it.get("session") or "").lower()
            addr = str(it.get("address") or "")
            if not addr:
                continue
            for n in needles:
                if n and n in sess:
                    _log_cb(log, f"ADB для #{vmindex}: {addr} ({sess[:60]})")
                    return addr
        _pause(0.32)
    return ""


def probe_adb_for_vm(vmindex: int, cached_addrs: Optional[List[str]] = None) -> str:
    """Быстрая проверка ADB: сначала adb devices, без полного scan портов."""
    from app import ports_from_adb_devices, probe_adb_reachable

    needles = [
        f"android device-{vmindex}".lower(),
        f"device-{vmindex}",
    ]
    adb_dev, adb_sess = ports_from_adb_devices()
    candidates = list(adb_dev.keys())
    if cached_addrs:
        for a in cached_addrs:
            if a not in candidates:
                candidates.append(a)
    for addr in candidates:
        sess = (adb_sess.get(addr) or "").lower()
        for n in needles:
            if n in sess and probe_adb_reachable(addr):
                return addr
    return ""


def mumu_launch_wait_adb(
    vmindex: int,
    *,
    vertical: bool = True,
    dismiss_store: bool = True,
    log: Optional[Callable[[str], None]] = None,
    timeout_sec: float = 55.0,
    adb_progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    if vertical:
        mumu_set_vertical(vmindex, log=log)
    mumu_launch_player(vmindex, log=log)
    deadline = time.monotonic() + timeout_sec
    adb = ""
    reported = ""
    last_dismiss = 0.0
    while time.monotonic() < deadline:
        cand = probe_adb_for_vm(vmindex)
        if cand:
            adb = cand
            if adb != reported:
                reported = adb
                _log_cb(log, f"MuMu #{vmindex}: ADB {adb}")
                if adb_progress_cb:
                    try:
                        adb_progress_cb(adb)
                    except Exception:
                        pass
            if dismiss_store and (time.monotonic() - last_dismiss) >= 0.9:
                dismiss_mumu_store_popup(adb, log=log)
                dismiss_mumu_store_aggressive(adb, log=log, rounds=3)
                last_dismiss = time.monotonic()
            break
        _pause(0.14)
    if adb and dismiss_store:
        dismiss_mumu_store_aggressive(adb, log=log, rounds=5)
        if mumu_emulator_startup_failed(adb):
            _log_cb(
                log,
                f"MuMu #{vmindex}: на экране «Ошибка запуска» — перезапустите эмулятор в MuMu Player",
            )
    elif not adb:
        _log_cb(log, f"MuMu #{vmindex}: ADB не найден за {int(timeout_sec)} с")
    return adb


def mumu_emulator_startup_failed(serial: str) -> bool:
    """Экран «Ошибка запуска» MuMu — Android не загрузился."""
    if not serial:
        return False
    try:
        from app import dump_ui_serial, _ui_visible_texts

        root = dump_ui_serial(serial)
        if root is None:
            return False
        blob = " ".join(_ui_visible_texts(root)).lower()
    except Exception:
        return False
    return "ошибка запуска" in blob or "startup error" in blob


def mumu_create_instances(count: int = 1, start_index: Optional[int] = None) -> dict:
    """Создать эмулятор(ы) через MuMuManager create."""
    n = max(1, min(int(count), 10))
    attempts: List[List[str]] = []
    if start_index is not None:
        attempts.append(["create", "-v", str(start_index), "-n", str(n)])
    attempts.append(["create", "-n", str(n)])
    attempts.append(["create", "--number", str(n)])

    last_err = ""
    for args in attempts:
        res = mumu_manager_run(args, timeout=180)
        out = res["output"]
        indices = parse_create_vm_indices(out)
        if res["ok"] and indices:
            return {
                "ok": True,
                "command": res["command"],
                "output": out[:2000],
                "indices": indices,
            }
        if res["ok"] and not indices and start_index is not None:
            indices = list(range(int(start_index), int(start_index) + n))
            return {
                "ok": True,
                "command": res["command"],
                "output": out[:2000],
                "indices": indices,
            }
        last_err = out[:800] or f"exit {res['returncode']}"
    raise RuntimeError(f"MuMuManager create failed: {last_err}")


def mumu_create_and_prepare_sessions(
    proxy_ports: List[int],
    proxy_host: str = DEFAULT_PROXY_HOST,
    signup_names: Optional[List[str]] = None,
    *,
    launch: bool = True,
    vertical: bool = True,
    log: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int, int, dict, List[dict]], None]] = None,
) -> List[dict]:
    """Создать N профилей (имя MuMu = Android device-N), запустить, найти ADB."""
    ports = [int(p) for p in proxy_ports if p is not None]
    if not ports:
        raise RuntimeError("Выберите хотя бы один порт прокси")
    host = (proxy_host or DEFAULT_PROXY_HOST).strip()

    created: List[dict] = []
    for i in range(len(ports)):
        _log_cb(log, f"Создание профиля {i + 1}/{len(ports)}…")
        info = mumu_create_instances(count=1)
        indices = info.get("indices") or []
        if not indices:
            raise RuntimeError("create не вернул index: " + str(info.get("output", ""))[:300])
        vm = int(indices[0])
        mumu_label = mumu_session_label(vm)
        signup = ""
        if signup_names and i < len(signup_names):
            signup = str(signup_names[i] or "").strip()

        _log_cb(log, f"MuMu #{vm}: {mumu_label}")
        _pause(0.18)

        row = {
            "vmindex": vm,
            "mumu_name": mumu_label,
            "adb_port": "",
            "proxy_host": host,
            "proxy_port": ports[i],
            "signup_name": signup,
            "name": signup,
            "launch_ok": False,
            "phase": "created",
        }
        created.append(row)
        if progress_cb:
            try:
                progress_cb(i + 1, len(ports), row, list(created))
            except Exception:
                pass

        adb = ""
        if launch:
            row["phase"] = "launching"
            if progress_cb:
                try:
                    progress_cb(i + 1, len(ports), row, list(created))
                except Exception:
                    pass

            def _adb_partial(addr: str) -> None:
                row["adb_port"] = addr
                row["launch_ok"] = bool(addr)
                row["phase"] = "adb" if addr else "launching"
                if progress_cb:
                    try:
                        progress_cb(i + 1, len(ports), row, list(created))
                    except Exception:
                        pass

            adb = mumu_launch_wait_adb(
                vm,
                vertical=vertical,
                dismiss_store=True,
                log=log,
                timeout_sec=48.0,
                adb_progress_cb=_adb_partial,
            )
            if not adb:
                adb = wait_adb_for_vm(vm, timeout_sec=22, log=log)
                if adb:
                    row["adb_port"] = adb
                    row["launch_ok"] = True
                    row["phase"] = "adb"
                    dismiss_mumu_store_aggressive(adb, log=log, rounds=5)
                    if progress_cb:
                        try:
                            progress_cb(i + 1, len(ports), row, list(created))
                        except Exception:
                            pass
            elif adb:
                row["adb_port"] = adb
                row["launch_ok"] = True
                row["phase"] = "adb"
                dismiss_mumu_store_aggressive(adb, log=log, rounds=4)

        row["phase"] = "ready" if adb else ("launching" if launch else "created")
        row["adb_port"] = adb or row.get("adb_port") or ""
        row["launch_ok"] = bool(adb)
        if progress_cb:
            try:
                progress_cb(i + 1, len(ports), row, list(created))
            except Exception:
                pass
    return created
