"""
Resource-id / content-desc helpers for OfferUp onboarding and SuperProxy.
Used by mumu_onboarding.py; core UI functions live in app.py.
"""

from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

# OfferUp resource-id map (from uidumps/)
OFFERUP_RID_MAP = {
    "signup_button": "email-landing-screen.signup.button",
    "login_button": "email-landing-screen.login.button",
    "cancel_button": "email-landing-screen.navigation-bar.cancel.touchable-opacity",
    "cancel_button_v2": "auth-landing-screen.cancel.button",
    "email_button": "auth-landing-screen.login-buttons.Email",
    "google_button": "auth-landing-screen.login-buttons.Google",
    "zip_input": "OnboardingSearchLocationScreen.ZIPCode.InputInput",
    "zip_enter_btn": "OnboardingSearchLocationScreen.ZIPCode.InputEnterBtn",
    "gps_button": "OnboardingSearchLocationScreen.GPSButton",
    "next_button": "OnboardingSearchLocationScreen.NextButton",
    "skip_categories": "OnboardingBuyerInterestSelectionScreen.Skip",
    "interest_pill": "OnboardingBuyerInterestSelectionScreen.InterestPill",
    "discussion_screen": "DiscussionScreen",
    "msg_input": "DiscussionScreen.FirstMessage.TextField.Input",
    "chat_input": "DiscussionFooter.ChatInput.Input",
    "chat_send": "DiscussionFooter.ChatInput.SendButton",
    "suggested_messages": "DiscussionScreen.FirstMessage.SuggestedMessages",
    "template_pill": "FirstMessage.SuggestedMessagePill",
    "chat_back": "DiscussionNavigationHeader.CloseButton",
    "chat_back_android": "DiscussionNavigationHeader.CloseButtonAndroid",
    "chat_menu": "DiscussionNavigationHeader.MenuButton",
    "seller_name_field": "DiscussionProfileHeader.ProfileCard.Name",
    "inbox_tab": "tab-bar-widget.tab.inbox.touchable-opacity",
    "inbox_tab_short": "tab.inbox.touchable-opacity",
}

SUPERPROXY_CDESC = {
    "add_proxy": "Добавить прокси",
    "import_proxy": "Импорт прокси",
    "start": "Старт",
    "ok": "ОК",
    "cancel": "Отмена",
    "tab_proxies": "Прокси Вкладка 1 из 3",
    "tab_log": "Журнал Вкладка 2 из 3",
    "tab_settings": "Настройки Вкладка 3 из 3",
    "vpn_ok": "android:id/button1",
    "vpn_cancel": "android:id/button2",
}


def _posix_quote(s: str) -> str:
    s = s or ""
    if not s:
        return "''"
    if "'" not in s:
        return f"'{s}'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def offerup_tap_rid(
    serial: str,
    rid_key: str,
    log_func: Optional[Callable[[str], None]] = None,
    timeout: float = 3.0,
    *,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
) -> bool:
    """Tap by resource-id substring from OFFERUP_RID_MAP or raw rid string."""
    from app import (
        ADB_PORT,
        _offerup_find_tap_by_resource_id,
        _offerup_tap_xy,
        dump_ui_serial,
        invalidate_ui_dump_cache,
        run_adb_serial,
    )

    rid = OFFERUP_RID_MAP.get(rid_key, rid_key)
    port = (serial or ADB_PORT or "").strip()
    if not port:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root = dump_ui_serial(port)
        if root is None:
            time.sleep(0.08)
            continue
        xy = _offerup_find_tap_by_resource_id(
            root,
            rid,
            y_min_frac=y_min_frac,
            y_max_frac=y_max_frac,
        )
        if xy:
            if log_func:
                log_func(f"tap rid {rid_key} → {xy}")
            _offerup_tap_xy(port, xy, log_func=log_func)
            return True
        time.sleep(0.08)
    if log_func:
        log_func(f"tap rid {rid_key} — не найден ({timeout:.1f}s)")
    return False


def offerup_onboarding_tap_zip(
    serial: str,
    zip_code: str,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    from app import _offerup_find_tap_by_resource_id, dump_ui_serial, invalidate_ui_dump_cache, run_adb_serial

    port = (serial or "").strip()
    root = dump_ui_serial(port)
    if root is None:
        return False
    zip_xy = _offerup_find_tap_by_resource_id(
        root, "OnboardingSearchLocationScreen.ZIPCode.InputInput", require_clickable=False
    )
    if not zip_xy:
        if log_func:
            log_func("ZIP: поле не найдено по resource-id")
        return False
    run_adb_serial(port, ["shell", "input", "tap", str(zip_xy[0]), str(zip_xy[1])])
    invalidate_ui_dump_cache(port)
    time.sleep(0.12)
    run_adb_serial(port, ["shell", "input", "keyevent", "KEYCODE_CTRL_A"])
    run_adb_serial(port, ["shell", f"input text {_posix_quote(zip_code)}"])
    time.sleep(0.15)
    root2 = dump_ui_serial(port)
    enter_xy = (
        _offerup_find_tap_by_resource_id(
            root2, "OnboardingSearchLocationScreen.ZIPCode.InputEnterBtn"
        )
        if root2 is not None
        else None
    )
    if enter_xy:
        run_adb_serial(port, ["shell", "input", "tap", str(enter_xy[0]), str(enter_xy[1])])
        if log_func:
            log_func(f"ZIP: {zip_code}, Enter (resource-id)")
        return True
    run_adb_serial(port, ["shell", "input", "keyevent", "66"])
    if log_func:
        log_func(f"ZIP: {zip_code}, keyevent Enter")
    return True


def offerup_onboarding_tap_next(
    serial: str,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    return offerup_tap_rid(serial, "next_button", log_func=log_func, timeout=4.0, y_min_frac=0.75)


def offerup_onboarding_skip_categories(
    serial: str,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    return offerup_tap_rid(serial, "skip_categories", log_func=log_func, timeout=4.0)


def superproxy_tap(
    serial: str,
    action: str,
    log_func: Optional[Callable[[str], None]] = None,
    timeout: float = 5.0,
) -> bool:
    from app import dump_ui_serial, parse_bounds, run_adb_serial

    val = SUPERPROXY_CDESC.get(action, action)
    port = (serial or "").strip()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root = dump_ui_serial(port)
        if root is None:
            time.sleep(0.08)
            continue
        if val.startswith("android:id/"):
            for node in root.iter("node"):
                rid = (node.get("resource-id") or "")
                if rid == val:
                    pr = parse_bounds(node.get("bounds") or "")
                    if pr:
                        cx, cy = (pr[0] + pr[2]) // 2, (pr[1] + pr[3]) // 2
                        if log_func:
                            log_func(f"SuperProxy {action} → {cx},{cy}")
                        run_adb_serial(port, ["shell", "input", "tap", str(cx), str(cy)])
                        invalidate_ui_dump_cache(port)
                        return True
        for node in root.iter("node"):
            cd = (node.get("content-desc") or "").strip()
            if cd == val and node.get("clickable", "false") == "true":
                pr = parse_bounds(node.get("bounds") or "")
                if pr:
                    cx, cy = (pr[0] + pr[2]) // 2, (pr[1] + pr[3]) // 2
                    if log_func:
                        log_func(f"SuperProxy {action} → {cx},{cy}")
                    run_adb_serial(port, ["shell", "input", "tap", str(cx), str(cy)])
                    invalidate_ui_dump_cache(port)
                    return True
        time.sleep(0.08)
    if log_func:
        log_func(f"SuperProxy {action} — не найдено ({timeout:.1f}s)")
    return False
