"""
MumuPaster — Flask-сервер.
Запуск: python app.py
Затем открыть в браузере: http://127.0.0.1:5000

Файлы в папке (без подпапок): app.py, index.html (весь UI внутри), mumu_state.json.
Зависимость: pip install flask

Локальная LLM (Inbox): форма «Локальная ИИ» → mumu_settings.json рядом с app.py.
Открыть объявление сразу в OfferUp без Chrome: галка в форме или OFFERUP_TRY_APP_URL_FIRST в app.py / env.
"""

import hashlib
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import base64
import functools
import json
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Flask, request, jsonify, send_from_directory

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_WS_COLLAPSE_RE = re.compile(r"\s+")


def _collapse_ws(s: str) -> str:
    return _WS_COLLAPSE_RE.sub(" ", (s or "").strip())

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ============================================================
# КОНФИГУРАЦИЯ — редактируйте этот блок
# ============================================================

# Полный путь к adb.exe вашей версии MuMu.
ADB_PATH = r"E:\MuMuPlayerGlobal\nx_device\12.0\shell\adb.exe"

# Порт ADB эмулятора: MuMu → Настройки → ADB отладка.
# Стандартные: 16384, 16416, 7555, 5555
ADB_PORT = "127.0.0.1:16416"

# Ограничить поиск поля ввода одним приложением (None — любое).
# Пример: "org.telegram.messenger"
TARGET_PACKAGE = None

# Один шаблон по умолчанию. {ссылка} → короткая x.gd ссылка.
MESSAGE_TEMPLATE = "{ссылка}"
# Несколько шаблонов подряд в один чат (если не None — имеет приоритет над MESSAGE_TEMPLATE).
# Пример: MESSAGE_TEMPLATES = ["Первая строка: {ссылка}", "Вторая строка без ссылки"]
MESSAGE_TEMPLATES: Optional[List[str]] = None

# API-ключ x.gd (сервис сокращения ссылок)
XGD_API_KEY = "86aabe9261f3d81ba8d90fc6ca008279"

# OfferUp (вкладка в UI; парсер — api documentation.txt). Пусто = только из формы.
OFFERUP_PARSER_API_KEY = (os.environ.get("OFFERUP_PARSER_API_KEY") or "").strip()
OFFERUP_ADS_URL = (os.environ.get("OFFERUP_ADS_URL") or "http://vvsproject.xyz/ads/offerup").strip()
OFFERUP_CHROME_PACKAGE = (os.environ.get("OFFERUP_CHROME_PACKAGE") or "com.android.chrome").strip()
# Дополнительные пакеты Chrome (через запятую в OFFERUP_CHROME_PACKAGE_FALLBACKS), затем типичные варианты на эмуляторах.
_OFFERUP_CHROME_FALLBACK_ENV = (os.environ.get("OFFERUP_CHROME_PACKAGE_FALLBACKS") or "").strip()
# Сначала открыть listing через OfferUp (am start VIEW -p com.offerup), без Chrome; если Ask не появился — обычный Chrome.
OFFERUP_TRY_APP_URL_FIRST = False  # поставьте True в этом файле, либо задайте переменную окружения OFFERUP_TRY_APP_URL_FIRST=1
if (os.environ.get("OFFERUP_TRY_APP_URL_FIRST") or "").strip():
    OFFERUP_TRY_APP_URL_FIRST = os.environ.get("OFFERUP_TRY_APP_URL_FIRST", "").strip().lower() in ("1", "true", "yes", "on")
OFFERUP_POLL_UI_SEC = 0.20
OFFERUP_OPEN_WAIT_SEC = 20.0
OFFERUP_ASK_WAIT_SEC = 12.0
OFFERUP_TEMPLATE_WAIT_SEC = 12.0
OFFERUP_WAIT_AFTER_TEMPLATE_SEC = 0.8
OFFERUP_VERIFY_EMAIL_AFTER_TEMPLATE = False
OFFERUP_TEMPLATE_LABELS: List[str] = [
    "Hi, is this still available?",
    "Hi, I'd like to buy this",
]

# Link API providers: kartatai (legacy grac API) | ephedrine (haronrent createAd).
LINK_API_PROVIDER = (os.environ.get("LINK_API_PROVIDER") or "kartatai").strip().lower()
# Kartatai
LINK_API_URL = (os.environ.get("LINK_API_URL") or "https://grac.k7r4q9p2z1x1.cfd/api/protected").strip()
LINK_API_KEY = (os.environ.get("LINK_API_KEY") or "c1D2e3F4g5H6i7J8k9L0m1N2o3P4q5").strip()
LINK_API_SERVICE = (os.environ.get("LINK_API_SERVICE") or "offerup2_usa").strip()
LINK_API_USER_ID = (os.environ.get("LINK_API_USER_ID") or "8326401217").strip()
LINK_API_TIMEOUT_SEC = 45
LINK_API_MIN_INTERVAL_SEC = 1.4
LINK_API_RETRY_COUNT = 3
# Ephedrine (@Ephedrine_bot API — POST /api/v1/createAd)
EPHEDRINE_API_BASE = (os.environ.get("EPHEDRINE_API_BASE") or "https://haronrent.xyz").strip().rstrip("/")
EPHEDRINE_API_TOKEN = (os.environ.get("EPHEDRINE_API_TOKEN") or "").strip()
EPHEDRINE_SERVICE_CODE = (os.environ.get("EPHEDRINE_SERVICE_CODE") or "offerup_usa").strip()
EPHEDRINE_VERSION = (os.environ.get("EPHEDRINE_VERSION") or "2").strip()
EPHEDRINE_DOMAIN_ID = os.environ.get("EPHEDRINE_DOMAIN_ID", "").strip()
EPHEDRINE_PROFILE_ID = os.environ.get("EPHEDRINE_PROFILE_ID", "").strip()
# Onboarding: Super Proxy + OfferUp signup + AnyMessage
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_apk_in_app_dir(
    preferred_name: str,
    *,
    name_hint: str = "",
    ext: str = ".apk",
) -> str:
    """Ищет APK/APKS в каталоге MumuPaster2 (рядом с app.py)."""
    preferred = os.path.join(APP_DIR, preferred_name)
    if os.path.isfile(preferred):
        return preferred
    hint = (name_hint or "").strip().lower()
    if os.path.isdir(APP_DIR):
        matches: List[str] = []
        for fn in os.listdir(APP_DIR):
            low = fn.lower()
            if not low.endswith(ext.lower()):
                continue
            if hint and hint not in low:
                continue
            matches.append(fn)
        if matches:
            matches.sort(key=lambda x: (len(x), x.lower()))
            return os.path.join(APP_DIR, matches[0])
    return preferred


PROXY_APK_PATH = (
    os.environ.get("PROXY_APK_PATH")
    or _resolve_apk_in_app_dir("Proxy.apk", name_hint="proxy", ext=".apk")
).strip()
OFFERUP_APKS_PATH = (
    os.environ.get("OFFERUP_APKS_PATH")
    or _resolve_apk_in_app_dir("OfferUp_2026.18.0.apks", name_hint="offerup", ext=".apks")
).strip()
MUMU_MANAGER_EXE = (os.environ.get("MUMU_MANAGER_EXE") or "").strip()
ANYMESSAGE_TOKEN = (os.environ.get("ANYMESSAGE_TOKEN") or "").strip()
ANYMESSAGE_DOMAIN = (os.environ.get("ANYMESSAGE_DOMAIN") or "gmx.com").strip()
ANYMESSAGE_SITE = (os.environ.get("ANYMESSAGE_SITE") or "offerup.com").strip()
ONBOARDING_ZIP_CODE = (os.environ.get("ONBOARDING_ZIP_CODE") or "42333").strip()
ONBOARDING_SIGNUP_NAME = (os.environ.get("ONBOARDING_SIGNUP_NAME") or "Alex Smith").strip()
DEFAULT_ONBOARDING_PROXY_HOST = (os.environ.get("ONBOARDING_PROXY_HOST") or "192.168.0.103").strip()
ONBOARDING_PROXY_PORTS = list(range(60000, 60016))
OFFERUP_CONVERSATION_COOLDOWN_SEC = float(os.environ.get("OFFERUP_CONVERSATION_COOLDOWN_SEC") or "72")
OFFERUP_REPLY_WAIT_SEC = 38.0
OFFERUP_REPLY_POLL_SEC = 2.5
OFFERUP_LIMIT_WAIT_SEC = 72.0
OFFERUP_INBOX_BATCH_EVERY = 3
OFFERUP_INBOX_MAX_REPLIES = 4
OFFERUP_INBOX_MAX_SCROLLS = 10
OFFERUP_INBOX_OPEN_WAIT_SEC = 0.5
OFFERUP_INBOX_NAV_READY_SEC = 5.0  # макс. ожидание нижней панели после запуска OfferUp
OFFERUP_INBOX_NAV_SETTLE_SEC = 3.0  # пауза перед первой проверкой нижней панели (MuMu грузит UI медленно)
OFFERUP_INBOX_SCAN_TIMEOUT_SEC = 0.0  # 0 = без лимита на скан Inbox
# Пауза между шагами Inbox scan (тап, свайп, открытие чата).
OFFERUP_INBOX_STEP_DELAY_SEC = 0.35
OFFERUP_INBOX_LIST_TOP_Y_MIN = 200
OFFERUP_INBOX_ROW_GROUP_Y_GAP = 110
# Тап по Inbox в нижней навигации (5 кнопок: Home | Inbox | Post | Listings | Account).
OFFERUP_INBOX_NAV_TAP_X_FRAC = 0.28
OFFERUP_INBOX_NAV_TAP_Y_FRAC = 0.955
# Зона Post внизу по центру — блокируем случайные тапы (промах в «+ Post»).
OFFERUP_NAV_BAR_Y_FRAC = 0.84
OFFERUP_POST_NAV_X_MIN_FRAC = 0.34
OFFERUP_POST_NAV_X_MAX_FRAC = 0.66
OFFERUP_INBOX_NAV_X_MIN_FRAC = 0.08
OFFERUP_INBOX_NAV_X_MAX_FRAC = 0.44
# Home — крайняя левая кнопка нижней панели (случайные тапы по строкам Inbox).
OFFERUP_HOME_NAV_X_MAX_FRAC = 0.22
OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC = 0.93
OFFERUP_CHAT_BUYER_X_FRAC = 0.52

# Ответ на «как это работает» — слово «Iinк» должно быть буквально как здесь (латиница + кириллическая «к»).
OFFERUP_HOW_IT_WORKS_REPLY = "Вы просто должны подтвердить это по Iin\u043a."
OFFERUP_INBOX_GENERIC_QUESTION_FALLBACK = "Thanks — I'll clarify and reply here in a moment."
OFFERUP_ONLY_CASH_REPLY = "No"
OFFERUP_CALL_DECLINE_REPLY = "Sorry, I can't call."

# --- Локальная ИИ (ответы в OfferUp Inbox при авторежиме) — настройка в этом файле ---
# True = всегда предлагать LLM при скане Inbox (если заданы URL и модель). Галка в UI всё ещё может дополнительно включить прогон.
LOCAL_LLM_ENABLED = False
# Ollama: http://127.0.0.1:11434/api/chat  |  LM Studio: http://127.0.0.1:1234/v1/chat/completions
LOCAL_LLM_CHAT_URL = "http://127.0.0.1:11434/api/chat"
LOCAL_LLM_MODEL = "llama3.2"
# "ollama" или "openai" (включая LM Studio, DeepSeek, Groq…)
LOCAL_LLM_API_STYLE = "ollama"
LOCAL_LLM_PROVIDER = "ollama"
LOCAL_LLM_API_KEY = ""
LOCAL_LLM_TIMEOUT_SEC = 90.0

LLM_PROVIDER_PRESETS: Dict[str, dict] = {
    "ollama": {
        "label": "Ollama (локально, бесплатно)",
        "url": "http://127.0.0.1:11434/api/chat",
        "api_style": "ollama",
        "model": "llama3.2",
        "vision_model": "moondream",
        "vision_models": ["moondream", "llava", "llava:13b", "qwen2.5vl", "bakllava"],
        "needs_api_key": False,
        "hint": "Текст локально. Vision: moondream (лёгкая) или llava; для скорина лучше Groq/Gemini.",
    },
    "groq": {
        "label": "Groq (облако, free tier + vision)",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "api_style": "openai",
        "model": "llama-3.3-70b-versatile",
        "vision_model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "vision_models": [
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "meta-llama/llama-4-maverick-17b-128e-instruct",
        ],
        "needs_api_key": True,
        "hint": "console.groq.com — текст Llama 3.3; vision: Llama 4 Scout (замена снятой llama-3.2-vision).",
    },
    "gemini": {
        "label": "Google Gemini (облако, free tier + vision)",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "api_style": "openai",
        "model": "gemini-2.0-flash",
        "vision_model": "gemini-2.0-flash",
        "vision_models": ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"],
        "needs_api_key": True,
        "hint": "aistudio.google.com/apikey — одна модель для текста и vision (OpenAI API).",
    },
    "openai": {
        "label": "OpenAI (облако, vision)",
        "url": "https://api.openai.com/v1/chat/completions",
        "api_style": "openai",
        "model": "gpt-4o-mini",
        "vision_model": "gpt-4o-mini",
        "vision_models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
        "needs_api_key": True,
        "hint": "platform.openai.com/api-keys — gpt-4o-mini для текста и скринов.",
    },
    "mistral": {
        "label": "Mistral (облако, Pixtral vision)",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "api_style": "openai",
        "model": "mistral-small-latest",
        "vision_model": "pixtral-12b-2409",
        "vision_models": ["pixtral-12b-2409", "pixtral-large-latest", "mistral-small-latest"],
        "needs_api_key": True,
        "hint": "console.mistral.ai — Pixtral для vision, mistral-small для текста.",
    },
    "deepseek": {
        "label": "DeepSeek (облако, текст)",
        "url": "https://api.deepseek.com/chat/completions",
        "api_style": "openai",
        "model": "deepseek-chat",
        "vision_model": "",
        "vision_models": [],
        "needs_api_key": True,
        "hint": "Только текст. Vision — выберите Groq/Gemini/OpenAI или укажите свой vision URL.",
    },
    "openrouter": {
        "label": "OpenRouter (облако, :free + vision)",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "api_style": "openai",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "vision_model": "google/gemini-2.0-flash-001",
        "vision_models": [
            "google/gemini-2.0-flash-001",
            "google/gemini-2.0-flash-lite-001",
            "qwen/qwen-2.5-vl-72b-instruct",
        ],
        "needs_api_key": True,
        "hint": "openrouter.ai/keys — :free модели; для vision укажите gemini или llama-vision.",
    },
    "lmstudio": {
        "label": "LM Studio (локально)",
        "url": "http://127.0.0.1:1234/v1/chat/completions",
        "api_style": "openai",
        "model": "",
        "vision_model": "",
        "vision_models": [],
        "needs_api_key": False,
        "hint": "LM Studio → Local Server → OpenAI API; vision — отдельная multimodal модель.",
    },
    "custom": {
        "label": "Свой URL",
        "url": "",
        "api_style": "openai",
        "model": "",
        "vision_model": "",
        "vision_models": [],
        "needs_api_key": False,
        "hint": "Любой OpenAI-compatible endpoint; ключ — при необходимости.",
    },
}

_llm_e = os.environ.get
if (_llm_e("LOCAL_LLM_ENABLED") or "").strip():
    LOCAL_LLM_ENABLED = _llm_e("LOCAL_LLM_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
if (_llm_e("LOCAL_LLM_CHAT_URL") or "").strip():
    LOCAL_LLM_CHAT_URL = _llm_e("LOCAL_LLM_CHAT_URL", "").strip()
if (_llm_e("LOCAL_LLM_MODEL") or "").strip():
    LOCAL_LLM_MODEL = _llm_e("LOCAL_LLM_MODEL", "").strip()
if (_llm_e("LOCAL_LLM_API_STYLE") or "").strip():
    LOCAL_LLM_API_STYLE = _llm_e("LOCAL_LLM_API_STYLE", "ollama").strip().lower()
if (_llm_e("LOCAL_LLM_PROVIDER") or "").strip():
    LOCAL_LLM_PROVIDER = _llm_e("LOCAL_LLM_PROVIDER", "ollama").strip().lower()
if (_llm_e("LOCAL_LLM_API_KEY") or "").strip():
    LOCAL_LLM_API_KEY = _llm_e("LOCAL_LLM_API_KEY", "").strip()
if (_llm_e("LOCAL_LLM_TIMEOUT_SEC") or "").strip():
    try:
        LOCAL_LLM_TIMEOUT_SEC = float(_llm_e("LOCAL_LLM_TIMEOUT_SEC", "90"))
    except (TypeError, ValueError):
        LOCAL_LLM_TIMEOUT_SEC = 90.0
LOCAL_LLM_TIMEOUT_SEC = max(5.0, min(float(LOCAL_LLM_TIMEOUT_SEC), 600.0))
LOCAL_LLM_VISION_TIMEOUT_SEC = 45.0
if (_llm_e("LOCAL_LLM_VISION_TIMEOUT_SEC") or "").strip():
    try:
        LOCAL_LLM_VISION_TIMEOUT_SEC = float(_llm_e("LOCAL_LLM_VISION_TIMEOUT_SEC", "45"))
    except (TypeError, ValueError):
        LOCAL_LLM_VISION_TIMEOUT_SEC = 45.0
LOCAL_LLM_VISION_TIMEOUT_SEC = max(8.0, min(float(LOCAL_LLM_VISION_TIMEOUT_SEC), 180.0))

# Vision (Ollama: llava, moondream…; облако: gemini-2.0-flash, gpt-4o-mini, llama-4-scout…)
LOCAL_LLM_VISION_MODEL = (os.environ.get("LOCAL_LLM_VISION_MODEL") or "").strip()
# Отдельный endpoint/ключ для vision (пусто = те же, что для текста)
LOCAL_LLM_VISION_CHAT_URL = (os.environ.get("LOCAL_LLM_VISION_CHAT_URL") or "").strip()
LOCAL_LLM_VISION_API_KEY = (os.environ.get("LOCAL_LLM_VISION_API_KEY") or "").strip()
LOCAL_LLM_VISION_API_STYLE = (os.environ.get("LOCAL_LLM_VISION_API_STYLE") or "").strip()
LOCAL_LLM_VISION_PROVIDER = (os.environ.get("LOCAL_LLM_VISION_PROVIDER") or "").strip()
LOCAL_LLM_SCREEN_ASSIST = True
if (os.environ.get("LOCAL_LLM_SCREEN_ASSIST") or "").strip():
    LOCAL_LLM_SCREEN_ASSIST = os.environ.get("LOCAL_LLM_SCREEN_ASSIST", "").strip().lower() in ("1", "true", "yes", "on")
# False = vision recovery предлагает действия в UI; True = выполнять launch/dismiss без спроса (как раньше).
LOCAL_LLM_SCREEN_ASSIST_AUTO = False
if (os.environ.get("LOCAL_LLM_SCREEN_ASSIST_AUTO") or "").strip():
    LOCAL_LLM_SCREEN_ASSIST_AUTO = os.environ.get("LOCAL_LLM_SCREEN_ASSIST_AUTO", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
# Авто «Разрешить» для карточек коуча / vision recovery (вкладка ИИ-коуч).
COACH_PENDING_AUTO_APPROVE = False
if (os.environ.get("COACH_PENDING_AUTO_APPROVE") or "").strip():
    COACH_PENDING_AUTO_APPROVE = os.environ.get("COACH_PENDING_AUTO_APPROVE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
# Пауза онбординга при неудачном тапе; продолжение — кнопка «Продолжить» в UI.
OPERATOR_PAUSE_ON_FAIL = True
OPERATOR_PAUSE_LLM_DUMP = False
if (os.environ.get("OPERATOR_PAUSE_ON_FAIL") or "").strip():
    OPERATOR_PAUSE_ON_FAIL = os.environ.get("OPERATOR_PAUSE_ON_FAIL", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
if (os.environ.get("OPERATOR_PAUSE_LLM_DUMP") or "").strip():
    OPERATOR_PAUSE_LLM_DUMP = os.environ.get("OPERATOR_PAUSE_LLM_DUMP", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
LOCAL_LLM_USE_LISTING_IMAGE_INBOX = True
if (os.environ.get("LOCAL_LLM_USE_LISTING_IMAGE_INBOX") or "").strip():
    LOCAL_LLM_USE_LISTING_IMAGE_INBOX = os.environ.get("LOCAL_LLM_USE_LISTING_IMAGE_INBOX", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

LOCAL_LLM_INBOX_SYSTEM_PROMPT = """You help an OfferUp inbox automation tool.
Reply with ONLY one JSON object (no markdown code fences, no commentary). Schema:
{"intent":"none","send_link":true,"text_replies":[],"ask_operator":false,"operator_message":""}

CRITICAL ROLE (never violate):
- You write text_replies AS THE BUYER (the person purchasing the item). The other person is the SELLER.
- The SELLER ships/mails the item TO the buyer. The buyer does NOT ship anything to the seller.
- NEVER write: "where should I send the item", "when should I ship", "I'll send it to you", "your address to ship to", or any wording where YOU (buyer) are sending the goods.
- If discussing shipping, ask the seller to ship TO you (e.g. "Can you ship to …?" / "Shipping to … works for me") or give YOUR delivery address from buyer_address_on_profile.

Field rules:
- intent: one of pickup_only, negative, question, positive, neutral, none.
  Use "none" to keep the server's regex_classifier_hint (do not override).
- send_link: true ONLY when the seller clearly agreed to SHIP / shipping / mail the item (yes to ship, ok I can ship, will ship, etc.). Then send the checkout link next. Set send_link false while you are still persuading them to ship, or if a link was already sent, or if unsure. Never duplicate links.
- SHIPPING HAS ABSOLUTE PRIORITY over meet/pickup: if the seller offers meet AND/OR ship, or asks pickup vs ship, text_replies must push shipping first (friendly, offer to pay shipping). send_link false on that turn.
- If the seller refuses shipping after you already asked (pickup only, can't ship, local only): text_replies[0] must be exactly "Let's meet tomorrow!" and send_link true (checkout link with template follows).
- If seller says only cash / cash only: intent negative, text_replies empty (server sends "No" and blocks thread).
- If seller asks to call / phone / give number to call: text_replies[0] must be exactly "Sorry, I can't call."
- NEVER reply if the seller has not sent any message yet (only our template visible) — intent "none", empty text_replies.
- text_replies: array of 0 to 2 plain English strings ONLY (not objects, not {"text":"..."} — just the message text). If intent is question, put the answer in text_replies[0]. Never put URLs or http(s) links in text_replies.
- ask_operator: true ONLY for serious edge cases (threats, fraud accusation, legal demand, impossible to interpret). NEVER use ask_operator for normal buyer questions: location/state/ZIP/city («are you in California?»), shipping, price, condition, availability — answer those in text_replies with intent question.
- Location/state questions: intent question, send_link false, text_replies[0] = short English answer. Use buyer_address_on_profile when they ask where to ship or where you are (e.g. «Yes, please ship to …»). Do NOT ask the operator.
- operator_message: required when ask_operator is true; otherwise empty string."""

_LOCAL_LLM_PROMPT_BASE: str = LOCAL_LLM_INBOX_SYSTEM_PROMPT

# Всегда добавляется к system prompt (даже если промпт переопределён в mumu_ui_config.json).
_LOCAL_LLM_INBOX_BUYER_ROLE_RULES = (
    "\n\nROLE REMINDER: text_replies = messages from BUYER to SELLER. Buyer receives shipment; seller sends. "
    "Forbidden phrases in text_replies: \"where should I send\", \"when should I ship\", \"I'll mail it to you\".\n"
)

# Адрес/имя из формы «Адрес покупателя» / Link API (передаётся в JSON user message).
_LOCAL_LLM_INBOX_ADDRESS_RULES = (
    "\n\nThe JSON user message includes buyer_name_on_profile and buyer_address_on_profile "
    "(buyer's SHIPPING/DELIVERY address — where the seller should mail the item TO the buyer).\n"
    "- If the seller asks for your address, ZIP, or where to ship/mail the item TO you: "
    "put buyer_address_on_profile verbatim in text_replies (one short friendly sentence). "
    "Example: \"Please ship to 123 Main St, City, ST 12345\" — use ONLY the profile address, never invent.\n"
    "- If buyer_address_on_profile is empty: say you will confirm your shipping address shortly — do NOT invent cities/ZIPs.\n"
    "- If the seller mentions they are in another state (e.g. \"I'm in Arizona\"): acknowledge and ask if they can ship to "
    "buyer_address_on_profile, or whether shipping is possible — still as the buyer.\n"
    "- If the seller only asks your name, use buyer_name_on_profile when set.\n"
)
_LOCAL_LLM_INBOX_IMAGE_RULES = (
    "\n\nIf the user message JSON has listing_image_attached=true, you also see the listing thumbnail image. "
    "When the seller asks which color/variant/version to ship, answer in text_replies in short English: point to the photo "
    "(e.g. \"the blue one on the first photo\", \"the item on the left in the picture\"). Do not invent colors not visible.\n"
)

LOCAL_LLM_SCREEN_ASSIST_SYSTEM = """You analyze ONE adb screencap from an Android phone inside MuMu emulator (NOT a desktop web browser).
Reply with ONLY one JSON object (no markdown). Schema:
{"situation":"offerup_inbox|offerup_foreground|offerup_chat|offerup_account|chrome|android_home|other_app|blank_screen|unknown","offerup_missing_or_background":false,"recovery_actions":["none"],"note":""}

Rules:
- Describe ONLY the phone screen. Ignore any PC/browser UI — it is not in this image.
- offerup_inbox: OfferUp open AND bottom tab Inbox selected (message list visible).
- offerup_foreground: OfferUp open but Home/Post/Listings/Account tab or main feed, NOT Inbox list.
- offerup_chat: inside a single chat thread.
- offerup_account: Account/Profile tab.
- If OfferUp not visible: android_home/other_app, offerup_missing_or_background=true, recovery_actions=["launch_offerup_app"].
- If OfferUp open but NOT offerup_inbox: recovery_actions=["open_inbox"] (not launch).
- Popup blocking: add dismiss_popup.
- unknown ONLY if unreadable.
- note: max 120 chars, Russian only.

recovery_actions: launch_offerup_app | open_inbox | dismiss_popup | none."""

LOCAL_LLM_SCREEN_ASSIST_SYSTEM_MOONDREAM = (
    LOCAL_LLM_SCREEN_ASSIST_SYSTEM
    + "\n\nIMPORTANT: Output must start with { and end with }. No words before or after the JSON."
)

LOCAL_LLM_COACH_SYSTEM_PROMPT_BASE = (
    "Ты коуч MumuPaster: управляешь OfferUp в эмуляторе MuMu через блок coach_actions (JSON).\n"
    "Ответ оператору: СТРОГО русский. Текст продавцу в OfferUp: ТОЛЬКО английский.\n\n"
    "ПРОФИЛЬ ПОКУПАТЕЛЯ (блок ниже в system):\n"
    "- buyer_name и shipping_address — это ПОКУПАТЕЛЬ (оператор), не продавец.\n"
    "- Продавец просит address / zip / where to ship → send_chat_message с shipping_address из профиля (дословно).\n"
    "- shipping_address пуст → коротко по-английски, что адрес пришлёте скоро; не выдумывай город.\n\n"
    "КОМАНДЫ ОПЕРАТОРА (распознавай смысл):\n"
    "- «напиши William …» / «ответь Benjamin» / «кидаю ник William» → open_inbox_chat + send_chat_message.\n"
    "- «отправь ссылку NAME» → open_inbox_chat + send_template_link.\n"
    "- «нажми Inbox» / «открой чат с X» → tap_rid или open_inbox_chat.\n\n"
    "ДОСТАВКА (ship) — приоритет над meet/pickup:\n"
    "- meet+ship → убеди на ship (send_chat_message), send_template_link пока false.\n"
    "- отказ от ship → «Let's meet tomorrow!» + send_template_link.\n"
    "- согласие на ship → send_template_link.\n\n"
    "ЭКРАНЫ:\n"
    "- inbox → open_inbox_chat (по имени), не tap_text по имени в списке.\n"
    "- chat → send_chat_message / send_template_link / tap_rid chat_send.\n"
    "- offerup_other → open_inbox_tab (tap_rid inbox_tab) или launch_offerup_app.\n\n"
    "coach_actions (обязателен, если нужно нажать/написать в OfferUp):\n"
    "```coach_actions\n"
    "[{\"type\":\"open_inbox_chat\",\"seller\":\"William\"},{\"type\":\"send_chat_message\",\"value\":\"Hi! Can you ship?\"}]\n"
    "```\n\n"
    "Типы действий:\n"
    "- open_inbox_chat — поле seller (имя продавца из Inbox).\n"
    "- send_chat_message — value: английский текст в чат.\n"
    "- send_template_link — шаблон с checkout-ссылкой (x.gd).\n"
    "- tap_rid — кнопка по resource-id OfferUp: {\"type\":\"tap_rid\",\"rid_key\":\"inbox_tab\"} "
    "или {\"type\":\"tap_rid\",\"resource_id\":\"DiscussionFooter.ChatInput.SendButton\"}.\n"
    "  Предпочитай tap_rid вместо tap_text, когда есть rid_key в списке ниже.\n"
    "- chat_back, open_inbox_tab, press_back, launch_offerup_app, dismiss_popup.\n"
    "- tap_text — только если нет подходящего rid_key.\n"
    "Все нужные шаги — одним массивом, без лишних действий.\n"
)
LOCAL_LLM_COACH_SYSTEM_PROMPT = LOCAL_LLM_COACH_SYSTEM_PROMPT_BASE
DEFAULT_COACH_TRAINING_RULES: List[str] = [
    "Если оператор просит написать конкретному человеку — всегда open_inbox_chat с его именем, затем send_chat_message.",
    "Адрес для доставки бери только из shipping_address в профиле; не придумывай ZIP/город.",
    "Для вкладок и кнопок OfferUp используй tap_rid (inbox_tab, chat_send, chat_back, msg_input).",
]
COACH_TRAINING_RULES: List[str] = list(DEFAULT_COACH_TRAINING_RULES)
LOCAL_LLM_EXTRA_TRAINING_RULES: List[str] = []

LOCAL_LLM_COACH_VISION_DESCRIBE = (
    "Опиши ТОЛЬКО экран Android-телефона на скриншоте (OfferUp или другое приложение). "
    "На картинке НЕТ браузера MumuPaster и текста «Авто-анализ». "
    "Ответ: 2–4 предложения, ТОЛЬКО русский язык."
)

_COACH_ACTIONS_BLOCK_RE = re.compile(r"```coach_actions\s*([\s\S]*?)```", re.IGNORECASE)
_COACH_VISION_CACHE_TTL_SEC = 16.0
_coach_vision_cache_lock = threading.RLock()
_coach_vision_cache: Dict[str, dict] = {}


def _coach_screen_context_for_port(port: str) -> str:
    """Последний screen_analyze_ui из trace (только если нет свежего скрина в чате)."""
    port = (port or "").strip()
    with _local_llm_trace_lock:
        for row in reversed(_local_llm_traces):
            if row.get("kind") != "screen_analyze_ui":
                continue
            meta = row.get("meta") or {}
            row_port = str(meta.get("port") or "").strip()
            if port and row_port and row_port != port:
                continue
            raw = (row.get("raw_model_reply") or "").strip()
            if not raw:
                continue
            ts = row.get("ts") or ""
            return (
                "\n\n--- Справка vision (JSON, может устареть"
                + (f", порт {port}" if port else "")
                + (f", {ts}" if ts else "")
                + ") ---\n"
                + raw[:4000]
                + "\n---\n"
            )
    return ""


def _cyrillic_letter_ratio(text: str) -> float:
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    return cyr / len(letters)


def _situation_is_offerup_inbox(situation: object) -> bool:
    return str(situation or "").strip().lower() == "offerup_inbox"


def _situation_offerup_open_not_inbox(situation: object) -> bool:
    s = str(situation or "").strip().lower()
    if s == "offerup_inbox":
        return False
    return s.startswith("offerup_") or s in ("offerup_foreground", "offerup_chat", "offerup_account")


def _recovery_merge_key(recovery_actions: List[str]) -> str:
    low = sorted(str(a).strip().lower() for a in (recovery_actions or []) if str(a).strip())
    if not low or all(a in ("none", "") for a in low):
        return ""
    if any("launch" in a or "offerup" in a for a in low):
        return "recovery:launch_offerup"
    if any("inbox" in a for a in low):
        return "recovery:open_inbox"
    if any("dismiss" in a for a in low):
        return "recovery:dismiss_popup"
    return "recovery:" + "|".join(low[:4])


def _coach_merge_key(coach_actions: List[dict]) -> str:
    parts: List[str] = []
    for act in coach_actions or []:
        if not isinstance(act, dict):
            continue
        t = str(act.get("type") or "").strip().lower()
        if t in ("tap_frac", "tap"):
            parts.append(f"tap:{act.get('x')}:{act.get('y')}")
        else:
            parts.append(t)
    return "coach:" + ("|".join(parts) if parts else "actions")


def _upsert_pending_ai_action(port: str, merge_key: str, payload: dict) -> Tuple[int, bool]:
    """Одна карточка на merge_key; при повторе — только обновление ts."""
    global _pending_ai_actions_next_id
    port = (port or "").strip()
    mk = (merge_key or "").strip()
    if not port or not mk:
        return 0, False
    with _pending_ai_actions_lock:
        for x in _pending_ai_actions:
            if str(x.get("port") or "").strip() == port and str(x.get("merge_key") or "") == mk:
                x["ts"] = time.strftime("%H:%M:%S")
                for k, v in payload.items():
                    if k != "id":
                        x[k] = v
                return int(x.get("id") or 0), False
        rid = int(_pending_ai_actions_next_id)
        _pending_ai_actions_next_id += 1
        row = dict(payload)
        row["id"] = rid
        row["ts"] = time.strftime("%H:%M:%S")
        row["port"] = port
        row["merge_key"] = mk
        _pending_ai_actions.append(row)
        if len(_pending_ai_actions) > _PENDING_AI_ACTIONS_CAP:
            del _pending_ai_actions[: len(_pending_ai_actions) - _PENDING_AI_ACTIONS_CAP]
        return rid, True


def _auto_execute_pending_row(row: dict, port: str) -> List[str]:
    log_lines: List[str] = []

    def _log(m: str) -> None:
        log_lines.append(m)

    coach_acts = row.get("coach_actions") or []
    if isinstance(coach_acts, list) and coach_acts:
        seller_hint = ""
        for a in coach_acts:
            if isinstance(a, dict) and str(a.get("seller") or a.get("name") or "").strip():
                seller_hint = str(a.get("seller") or a.get("name") or "").strip()
                break
        ui_state, _ = _coach_ui_offerup_state(port, "")
        coach_acts = _coach_expand_actions(coach_acts, ui_state, seller_hint)
        exec_res = _execute_coach_actions(
            coach_acts,
            port,
            _log,
            seller_hint=seller_hint,
            ui_state=ui_state,
        )
        if not _coach_actions_user_success(exec_res, coach_acts):
            tail = "; ".join(_log[-4:]) if _log else "действие не выполнено на эмуляторе"
            append_coach_feed_from_inbox(f"[коуч] авто-разрешить не удалось на {port}: {tail}")
    else:
        acts = row.get("recovery_actions") or []
        _execute_recovery_actions(acts if isinstance(acts, list) else [], port, _log)
    return log_lines


def _coach_cached_vision_obj(port: str) -> Optional[dict]:
    port = (port or "").strip()
    if not port:
        return None
    with _coach_vision_cache_lock:
        row = _coach_vision_cache.get(port)
        if not row:
            return None
        if time.monotonic() - float(row.get("mono") or 0) > _COACH_VISION_CACHE_TTL_SEC:
            return None
        obj = row.get("obj")
        return obj if isinstance(obj, dict) else None


def _coach_store_vision_obj(port: str, obj: Optional[dict]) -> None:
    port = (port or "").strip()
    if not port or not obj:
        return
    with _coach_vision_cache_lock:
        _coach_vision_cache[port] = {"mono": time.monotonic(), "obj": dict(obj)}


def _coach_emulator_blocked(serial: Optional[str]) -> Tuple[bool, str, str]:
    """(blocked, code, message_ru) — MuMu не готов к работе коуча."""
    port = (serial or ADB_PORT or "").strip()
    if not port:
        return True, "no_port", "Порт MuMu не выбран. В ⚙ нажмите «Найти порты MuMu» и выберите сессию."
    if not probe_adb_reachable(port):
        return (
            True,
            "adb_offline",
            "Нет связи ADB с эмулятором. Запустите MuMu (Android Device-N), дождитесь загрузки, затем снова «Найти порты».",
        )
    root = dump_ui_serial(port)
    if root is None:
        return True, "no_ui", "Эмулятор не отвечает (UI dump пуст). Перезапустите окно MuMu."
    low = " ".join(_ui_visible_texts(root)).lower()
    if "ошибка запуска" in low or "startup error" in low:
        return (
            True,
            "startup_error",
            "На экране MuMu «Ошибка запуска» — Android не загрузился. "
            "Закройте эмулятор, в MuMu Player перезапустите или удалите и создайте профиль заново. "
            "Коуч и vision не могут работать, пока это не исправлено.",
        )
    return False, "", ""


def _coach_is_bad_tap_label(label: str, buyer_name: str = "") -> bool:
    s = (label or "").strip().lower()
    if not s:
        return True
    if s.startswith("you:") or s == "you" or s.startswith("you "):
        return True
    if s in ("inbox", "navigate up", "back"):
        return True
    bn = (buyer_name or "").strip().lower()
    if bn and (s == bn or s.startswith(bn + " ")):
        return True
    return False


def _coach_ui_offerup_state(serial: Optional[str], buyer_name: str = "") -> Tuple[str, str]:
    """inbox | chat | offerup_other | no_offerup | emulator_blocked | unknown + описание UI."""
    blocked, _code, msg = _coach_emulator_blocked(serial)
    if blocked:
        return "emulator_blocked", msg
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return "unknown", ""
    texts_joined = " ".join(_ui_visible_texts(root))
    low = texts_joined.lower()
    summary = _ui_texts_summary_from_root(root, 1200)
    if "com.offerup" not in low and "offerup" not in low:
        if not any("offerup" in (n.get("package") or "").lower() for n in root.iter("node")):
            return "no_offerup", summary
    if _offerup_chat_has_message_compose(root):
        seller = _offerup_infer_chat_name(root, "")
        if seller and not _coach_is_bad_tap_label(seller, buyer_name):
            summary = f"Открыт чат с «{seller}». " + summary[:900]
        else:
            summary = "Открыт чат OfferUp (поле Message). " + summary[:900]
        return "chat", summary
    if _offerup_ui_looks_like_inbox_open(root, texts_joined):
        return "inbox", summary
    return "offerup_other", summary


def _coach_ui_inbox_state(serial: Optional[str]) -> Tuple[str, str]:
    """Совместимость: делегирует в _coach_ui_offerup_state."""
    return _coach_ui_offerup_state(serial)


def _inbox_row_name_matches(row_name: str, query: str) -> bool:
    n = _normalize_inbox_match_name(query)
    rn = _normalize_inbox_match_name(row_name)
    if not n or not rn or _is_bad_captured_offerup_name(n) or _is_bad_captured_offerup_name(rn):
        return False
    n_core = n.rstrip(". ")
    rn_core = rn.rstrip(". ")
    if rn == n or rn in n or n in rn:
        return True
    if len(n_core) >= 3 and rn_core.startswith(n_core):
        return True
    if len(rn_core) >= 3 and n_core.startswith(rn_core):
        return True
    q_tokens = [t for t in re.split(r"[\s.]+", n) if len(t) >= 2]
    rn_tokens = [t for t in re.split(r"[\s.]+", rn) if len(t) >= 2]
    if len(q_tokens) >= 2 and all(t in rn for t in q_tokens):
        return True
    if q_tokens and len(q_tokens[0]) >= 3 and rn.startswith(q_tokens[0]):
        return True
    if q_tokens and rn_tokens and q_tokens[0] == rn_tokens[0]:
        return True
    if q_tokens and len(q_tokens[0]) >= 4 and rn_core.startswith(q_tokens[0][: max(4, len(q_tokens[0]) - 1)]):
        return True
    if q_tokens and rn_tokens and q_tokens[-1] == rn_tokens[-1] and len(q_tokens[-1]) >= 3:
        return True
    return False


def _coach_inbox_row_names(serial: Optional[str], limit: int = 16) -> List[str]:
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return []
    return [str(r.get("name") or "").strip() for r in _offerup_collect_inbox_rows(root)[:limit] if r.get("name")]


def _offerup_rid_map_for_api() -> dict:
    try:
        from mumu_rid_optimizations import OFFERUP_RID_MAP
    except Exception:
        return {}
    return dict(sorted(OFFERUP_RID_MAP.items()))


def _offerup_rid_map_prompt_lines() -> str:
    rid_map = _offerup_rid_map_for_api()
    if not rid_map:
        return ""
    lines = ["Доступные rid_key для tap_rid:"]
    for key, rid in rid_map.items():
        lines.append(f"  - {key}: {rid}")
    return "\n".join(lines)


def _parse_training_rules_field(raw: object) -> List[str]:
    if isinstance(raw, list):
        return [_collapse_ws(str(x)) for x in raw if _collapse_ws(str(x))]
    if isinstance(raw, str):
        return [_collapse_ws(x) for x in raw.splitlines() if _collapse_ws(x)]
    return []


def _coach_training_rules_block() -> str:
    rules: List[str] = []
    for src in (COACH_TRAINING_RULES, LOCAL_LLM_EXTRA_TRAINING_RULES):
        for r in src or []:
            t = _collapse_ws(str(r))
            if t and t not in rules:
                rules.append(t)
    if not rules:
        return ""
    return "\n\n--- Обучение оператора (следуй всегда) ---\n" + "\n".join(f"- {x}" for x in rules) + "\n"


def _effective_coach_system_prompt() -> str:
    return (LOCAL_LLM_COACH_SYSTEM_PROMPT or LOCAL_LLM_COACH_SYSTEM_PROMPT_BASE).strip()


def _build_coach_system_prompt(
    *,
    buyer_name: str,
    buyer_address: str,
    ui_state: str,
    adb_port: str,
    screen_summary: str,
    seller_hint: str,
    include_screen: bool,
    b64: bool,
) -> str:
    profile_block = (
        "\n\n--- Профиль покупателя (аккаунт оператора, не собеседник) ---\n"
        + f"buyer_name: {buyer_name or '(пусто)'}\n"
        + f"shipping_address: {buyer_address or '(пусто)'}\n"
    )
    coach_sys = _effective_coach_system_prompt() + profile_block
    coach_sys += _coach_training_rules_block()
    rid_lines = _offerup_rid_map_prompt_lines()
    if rid_lines:
        coach_sys += "\n\n--- " + rid_lines + "\n"
    coach_sys += f"\n\n--- Состояние OfferUp (авто): {ui_state} ---\n"
    if ui_state == "chat":
        coach_sys += (
            "Оператор в открытом чате. Не требуй Inbox. Для ответа — send_chat_message или tap_rid chat_send.\n"
        )
    elif ui_state == "inbox":
        coach_sys += (
            "Список Inbox. open_inbox_chat с именем продавца, затем send_chat_message / send_template_link.\n"
        )
        if adb_port:
            vis = _coach_inbox_row_names(adb_port, 14)
            if vis:
                coach_sys += "Видимые продавцы в Inbox: " + ", ".join(vis) + "\n"
            root_in = dump_ui_serial(adb_port)
            if root_in is not None:
                for row in _offerup_collect_inbox_rows(root_in)[:8]:
                    sn = str(row.get("snippet") or "").strip()
                    if sn:
                        coach_sys += f"  · {row.get('name', '?')}: {sn[:120]}\n"
    if seller_hint:
        coach_sys += f"Имя продавца из запроса оператора: «{seller_hint}»\n"
    if screen_summary:
        coach_sys += "\n\n--- Описание экрана (только телефон) ---\n" + screen_summary + "\n"
    elif not b64 and include_screen:
        coach_sys += (
            "\n\n--- Экран ---\nСкриншот не получен (adb или vision). Не описывай приложение от себя.\n"
        )
    elif not include_screen:
        coach_sys += "\n\n--- Экран ---\nОператор не прикреплял скрин к этому сообщению.\n"
    else:
        coach_sys += _coach_screen_context_for_port(adb_port)
    return coach_sys


def _coach_parse_operator_request(user_text: str) -> dict:
    """Разбор «напиши William …», «кидаю ник X», адрес, ссылка."""
    t = (user_text or "").strip()
    out: dict = {
        "seller": "",
        "message_en": "",
        "want_link": False,
        "want_address_reply": False,
    }
    if not t:
        return out
    low = t.lower()
    if re.search(r"\b(ссылк|link|x\.gd|шаблон|template)\b", low):
        out["want_link"] = True
    if re.search(
        r"\b(адрес|address|zip|почтов|ship to|where to ship|куда отправ|shipping address)\b",
        low,
    ):
        out["want_address_reply"] = True
    for pat in (
        r"(?:напиши|написать|отправь|отправить|отпиши|отписать|ответь|ответить|reply to|write to)\s+"
        r"(?:ему\s+|ей\s+|продавцу\s+)?([A-Za-z][\w.'-]{1,40})",
        r"(?:ник|seller|продавец|имя)\s*[:\s]+([A-Za-z][\w.'-]{1,40})",
        r"(?:чат с|chat with|open)\s+([A-Za-z][\w.'-]{1,40})",
    ):
        m = re.search(pat, t, re.I)
        if m:
            out["seller"] = _collapse_ws(m.group(1)).strip(" .,'\"")[:60]
            break
    msg_m = re.search(
        r"(?:скажи|текст|message|напиши ему|write)\s*[:\-—]\s*(.+)$",
        t,
        re.I,
    )
    if msg_m:
        out["message_en"] = _collapse_ws(msg_m.group(1)).strip(" «»\"'")[:420]
    if not out["message_en"]:
        qm = re.search(r"[«\"']([^«»\"']{2,420})[«»\"']", t)
        if qm:
            out["message_en"] = _collapse_ws(qm.group(1))[:420]
    if not out["message_en"]:
        after_name = re.search(
            r"(?:напиши|ответь|отправь)\s+[A-Za-z][\w.'-]{1,40}\s+(.+)$",
            t,
            re.I,
        )
        if after_name:
            tail = _collapse_ws(after_name.group(1))
            if tail and not re.match(r"^(ссылк|link|шаблон)", tail, re.I):
                out["message_en"] = tail[:420]
    return out


def _coach_resolve_seller_hint(user_text: str, serial: Optional[str]) -> str:
    parsed = _coach_parse_operator_request(user_text)
    if parsed.get("seller"):
        return str(parsed["seller"])
    t = (user_text or "").strip()
    if not t:
        return ""
    for pat in (
        r"(?:ответь|ответить|напиши|write to|reply to|open chat with)\s+([A-Za-z][\w\s.'-]{1,38})",
        r"(?:чат с|chat with)\s+([A-Za-z][\w\s.'-]{1,38})",
    ):
        m = re.search(pat, t, re.I)
        if m:
            hint = _collapse_ws(m.group(1)).strip(" .,'\"")
            if hint and len(hint) >= 2:
                return hint[:60]
    low = t.lower()
    if serial:
        for name in _coach_inbox_row_names(serial, 24):
            if name and name.lower() in low:
                return name
    return ""


def _coach_swipe_inbox_list(serial: Optional[str], log_func: Optional[Callable[[str], None]] = None) -> None:
    root = dump_ui_serial(serial) if serial else dump_ui()
    scr_w, scr_h = screen_size(root) if root is not None else (0, 0)
    if not scr_h:
        return
    sx = str(scr_w // 2 or 320)
    y1 = str(int(scr_h * 0.78))
    y2 = str(int(scr_h * 0.34))
    if serial:
        run_adb_serial(serial, ["shell", "input", "swipe", sx, y1, sx, y2, "480"])
    else:
        run_adb(["shell", "input", "swipe", sx, y1, sx, y2, "480"])
    if log_func:
        log_func("coach: свайп Inbox вниз")
    time.sleep(0.38)


def _coach_find_inbox_row(serial: Optional[str], seller_name: str, log_func: Callable[[str], None]) -> Optional[dict]:
    seller = _collapse_ws(seller_name or "").strip()
    port = (serial or ADB_PORT or "").strip()
    if not port:
        return None
    for attempt in range(4):
        root = dump_ui_serial(port)
        if root is None:
            return None
        if not _offerup_ui_looks_like_inbox_open(root, " ".join(_ui_visible_texts(root))):
            offerup_open_inbox_tab(port, log_func)
            time.sleep(0.3)
            root = dump_ui_serial(port)
            if root is None:
                return None
        rows = _offerup_collect_inbox_rows(root)
        if seller:
            for row in rows:
                if _inbox_row_name_matches(str(row.get("name") or ""), seller):
                    return row
        elif len(rows) == 1:
            return rows[0]
        if attempt < 3:
            _coach_swipe_inbox_list(port, log_func)
    return None


def _coach_open_inbox_chat(serial: Optional[str], seller_name: str, log_func: Callable[[str], None]) -> bool:
    """Открыть чат из списка Inbox тапом по строке (как Inbox scan), не tap_text."""
    port = (serial or ADB_PORT or "").strip()
    if not port:
        log_func("coach: порт ADB не задан")
        return False
    seller = _collapse_ws(seller_name or "").strip()
    root = dump_ui_serial(port)
    if root is None:
        log_func("coach: UI dump недоступен")
        return False
    if _offerup_chat_has_message_compose(root):
        open_name = _offerup_infer_chat_name(root, "")
        if seller:
            if open_name and _inbox_row_name_matches(open_name, seller):
                log_func(f"coach: чат «{open_name}» уже открыт")
                return True
            log_func(
                f"coach: открыт чужой чат"
                + (f" («{open_name}»)" if open_name else "")
                + f", ищем «{seller}»"
            )
            _offerup_tap_chat_back(port, log_func)
            time.sleep(0.35)
        elif not seller:
            log_func("coach: чат уже открыт")
            return True
    target = _coach_find_inbox_row(port, seller, log_func)
    if target is None:
        vis = ", ".join(_coach_inbox_row_names(port, 8))
        log_func(f"coach: нет строки Inbox для «{seller or '?'}»" + (f" (видно: {vis})" if vis else ""))
        return False
    tx, ty = target["tap"]
    if not _offerup_tap_xy(port, (tx, ty), log_func=log_func):
        log_func("coach: тап по строке Inbox заблокирован (зона Post)")
        return False
    time.sleep(_COACH_OPEN_CHAT_WAIT_SEC)
    _offerup_dismiss_post_item_if_open(port, log_func)
    root2 = dump_ui_serial(port)
    if _offerup_chat_has_message_compose(root2):
        log_func(f"coach: открыт чат «{target.get('name', '?')}»")
        return True
    log_func("coach: после тапа поле Message не найдено")
    return False


def _chat_shows_we_asked_for_ship(serial: Optional[str]) -> bool:
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return False
    blob = " ".join(_ui_visible_texts(root)).lower()
    if "you:" not in blob:
        return False
    ship_markers = (
        "can you ship",
        "cover shipping",
        "ship it please",
        "shipping works great",
        "i will pay shipping",
        "i'll cover shipping",
    )
    return any(m in blob for m in ship_markers)


def _coach_ensure_chat_open(
    serial: Optional[str],
    seller_hint: str,
    log_func: Callable[[str], None],
) -> bool:
    hint = seller_hint.strip()
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is not None and _offerup_chat_has_message_compose(root):
        open_name = _offerup_infer_chat_name(root, "")
        if hint and open_name and not _inbox_row_name_matches(open_name, hint):
            log_func(f"coach: нужен чат «{hint}», открыт «{open_name}» — переключаю")
            _offerup_tap_chat_back(serial, log_func)
            time.sleep(0.35)
        else:
            return True
    if not hint and root is not None:
        texts = " ".join(_ui_visible_texts(root))
        if _offerup_ui_looks_like_inbox_open(root, texts):
            names = _coach_inbox_row_names(serial, 1)
            if names:
                hint = names[0]
    if hint:
        return _coach_open_inbox_chat(serial, hint, log_func)
    if root is not None and _offerup_ui_looks_like_inbox_open(root, " ".join(_ui_visible_texts(root))):
        log_func("coach: укажите имя продавца из Inbox")
    return False


def _coach_send_template_link_for_chat(
    serial: Optional[str],
    seller_hint: str,
    log_func: Callable[[str], None],
) -> bool:
    name = (seller_hint or "").strip()
    if not name and serial:
        root = dump_ui_serial(serial)
        name = _offerup_infer_chat_name(root, "") if root is not None else ""
    link_row = _find_pending_link_for_inbox_name(name) if name else None
    if not link_row:
        link_row = _find_fallback_pending_link_for_port(serial)
    if not link_row:
        log_func("coach: нет ссылки в очереди для отправки шаблона")
        return False
    link = str(link_row.get("fish_link") or link_row.get("url") or "")
    if not link:
        log_func("coach: пустая fish_link")
        return False
    if _offerup_chat_shows_buyer_sent_link(serial, link_row):
        mark_created_link_sent(link_row.get("created_link_id"))
        _remove_link_queue_item_by_created_id(link_row.get("created_link_id"))
        log_func("coach: ссылка уже в чате")
        return True
    if _send_xgd_template_link_to_current_chat(link, None, serial, log_func):
        mark_created_link_sent(link_row.get("created_link_id"))
        _remove_link_queue_item_by_created_id(link_row.get("created_link_id"))
        return True
    return False


def _coach_expand_actions(
    actions: List[dict],
    ui_state: str,
    seller_hint: str,
    user_text: str = "",
    buyer_address: str = "",
) -> List[dict]:
    parsed = _coach_parse_operator_request(user_text or "")
    hint = (seller_hint or parsed.get("seller") or "").strip()
    out: List[dict] = list(actions) if actions else []

    if not out and hint:
        msg = str(parsed.get("message_en") or "").strip()
        if parsed.get("want_link"):
            out = [
                {"type": "open_inbox_chat", "seller": hint},
                {"type": "send_template_link"},
            ]
        elif parsed.get("want_address_reply") and buyer_address:
            out = [
                {"type": "open_inbox_chat", "seller": hint},
                {
                    "type": "send_chat_message",
                    "value": f"Please ship to {buyer_address}. I'll cover shipping.",
                },
            ]
        elif msg:
            out = [
                {"type": "open_inbox_chat", "seller": hint},
                {"type": "send_chat_message", "value": msg},
            ]

    needs_chat = any(
        str(a.get("type") or "").strip().lower()
        in (
            "send_chat_message",
            "send_message",
            "send_template_link",
            "type_text",
        )
        for a in out
        if isinstance(a, dict)
    )
    expanded: List[dict] = []
    if ui_state == "inbox" and needs_chat and hint:
        if not any(
            str(a.get("type") or "").strip().lower() == "open_inbox_chat"
            for a in out
            if isinstance(a, dict)
        ):
            expanded.append(
                {
                    "type": "open_inbox_chat",
                    "seller": hint,
                    "label": f"открыть чат {hint}",
                }
            )
    expanded.extend(out)
    return expanded


def _ui_texts_summary_from_root(root: ET.Element, limit: int = 1200) -> str:
    seen: set = set()
    parts: List[str] = []
    for t in _ui_visible_texts(root):
        t = (t or "").strip()
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        parts.append(t)
        if sum(len(p) for p in parts) > limit:
            break
    return " | ".join(parts)[:limit]


def _user_asks_about_screen(text: str) -> bool:
    low = (text or "").lower()
    return any(
        k in low
        for k in (
            "экран",
            "скрин",
            "что на",
            "что ты вид",
            "видишь",
            "покаж",
            "что происходит",
            "что сейчас",
        )
    )


def _vision_screen_assist_analyze(
    port: str,
    b64: str,
    *,
    context: str = "",
    write_trace: bool = True,
    use_cache: bool = True,
) -> Optional[dict]:
    if use_cache:
        cached = _coach_cached_vision_obj(port)
        if cached:
            return cached
    if not _local_llm_vision_ready() or not b64:
        return None
    vision_model = (LOCAL_LLM_VISION_MODEL or "").strip()
    user_txt = "Контекст:\n" + (context or "Анализ экрана")[:1400] + "\n\nОтветь ТОЛЬКО JSON-объектом по схеме из system."

    def _log(_m: str) -> None:
        return None

    msgs = [
        {"role": "system", "content": _screen_assist_system_for_model(vision_model)},
        {"role": "user", "content": user_txt, "images": [b64]},
    ]
    raw = local_llm_chat_with_messages(
        msgs,
        _log,
        skip_trace=not write_trace,
        trace_kind="screen_analyze_ui",
        trace_meta={"context": (context or "")[:500], "port": port},
        model_override=vision_model,
    )
    if not raw:
        return None
    obj = _extract_json_object_from_assistant(raw)
    if not obj:
        if write_trace:
            _append_local_llm_trace(
                {
                    "kind": "screen_analyze_ui",
                    "error": "нет JSON",
                    "raw_model_reply": (raw or "")[:2000],
                    "meta": {"port": port, "model": vision_model[:80]},
                }
            )
        return None
    if write_trace:
        _append_local_llm_trace(
            {
                "kind": "screen_analyze_ui",
                "error": "",
                "raw_model_reply": json.dumps(obj, ensure_ascii=False)[:2000],
                "meta": {"port": port, "model": vision_model[:80]},
            }
        )
    if use_cache and obj:
        _coach_store_vision_obj(port, obj)
    return obj


def _coach_inbox_nav_action() -> List[dict]:
    return [{"type": "tap_text", "text": "Inbox", "label": "вкладка Inbox"}]


def notify_operator_help(serial: Optional[str], message: str) -> int:
    """Просьба нажать вручную — попадает в ленту коуча (UI покажет toast)."""
    port = (serial or ADB_PORT or "").strip()
    prefix = f"[{port}] " if port else ""
    t = (message or "").strip()
    if not t.lower().startswith("пожалуйста") and not t.startswith("⚠"):
        t = "Пожалуйста, " + t[0].lower() + t[1:] if t else "выполните действие на эмуляторе вручную"
    return append_coach_feed_from_inbox(prefix + t)


_operator_pause_lock = threading.RLock()
_operator_pause_resume = threading.Event()
_operator_pause_state: Dict[str, Any] = {
    "active": False,
    "serial": "",
    "step": "",
    "message": "",
    "dump": "",
    "ai_dump": "",
    "attempts": [],
    "ts": "",
}
_operator_ai_dumps: List[dict] = []
_operator_ai_dumps_next_id = 1
_OPERATOR_AI_DUMPS_CAP = 80


def _operator_ui_snippet(serial: Optional[str], max_chars: int = 2200) -> str:
    port = (serial or ADB_PORT or "").strip()
    if not port:
        return ""
    try:
        from mumu_onboarding import _superproxy_ui_blob

        blob = _superproxy_ui_blob(port)
        if blob:
            return blob[:max_chars]
    except Exception:
        pass
    root = dump_ui_serial(port)
    if root is None:
        return "(UI dump недоступен)"
    parts = list(_ui_visible_texts(root))
    text = " | ".join(str(p) for p in parts if str(p).strip())
    return (text or "(пустой UI)")[:max_chars]


def _format_operator_dump(
    step: str,
    message: str,
    attempts: Optional[List[dict]],
    ui_snippet: str,
) -> str:
    lines = [
        f"step: {step}",
        f"message: {message}",
        "",
        "attempts:",
    ]
    for i, att in enumerate(attempts or [], 1):
        lines.append(f"  {i}. {json.dumps(att, ensure_ascii=False)[:500]}")
    lines.extend(["", "ui:", ui_snippet[:2000]])
    return "\n".join(lines)


def _generate_operator_ai_dump(
    serial: str,
    step: str,
    message: str,
    attempts: Optional[List[dict]],
    ui_snippet: str,
    log_func: Optional[Callable[[str], None]] = None,
) -> str:
    if not OPERATOR_PAUSE_LLM_DUMP or not _local_llm_runtime_ready():
        return ""
    log = log_func or (lambda _m: None)
    user = (
        "Ты помощник разработчика Android-автоматизации (MuMu, adb, UI dump).\n"
        f"Шаг: {step}\n"
        f"Проблема: {message}\n"
        f"Попытки: {json.dumps(attempts or [], ensure_ascii=False)[:1400]}\n"
        f"UI: {ui_snippet[:1600]}\n\n"
        "Напиши AI-дамп для правки кода (mumu_onboarding.py / app.py): "
        "что не нажалось, чем пробовали, гипотеза, что добавить (tap_text / проверка UI). "
        "8–14 строк, русский, без markdown-заборов."
    )
    reply = local_llm_chat_with_messages(
        [
            {"role": "system", "content": "Краткий технический отчёт для разработчика автоматизации."},
            {"role": "user", "content": user},
        ],
        log,
        skip_trace=True,
        trace_kind="operator_dump",
        trace_meta={"serial": serial, "step": step},
    )
    return (reply or "").strip()[:4000]


def append_operator_ai_dump(entry: dict) -> int:
    global _operator_ai_dumps_next_id
    with _operator_pause_lock:
        rid = int(_operator_ai_dumps_next_id)
        _operator_ai_dumps_next_id += 1
        row = dict(entry)
        row["id"] = rid
        row.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        _operator_ai_dumps.append(row)
        if len(_operator_ai_dumps) > _OPERATOR_AI_DUMPS_CAP:
            del _operator_ai_dumps[: len(_operator_ai_dumps) - _OPERATOR_AI_DUMPS_CAP]
        return rid


def operator_pause_snapshot() -> dict:
    with _operator_pause_lock:
        return {
            "active": bool(_operator_pause_state.get("active")),
            "serial": str(_operator_pause_state.get("serial") or ""),
            "step": str(_operator_pause_state.get("step") or ""),
            "message": str(_operator_pause_state.get("message") or ""),
            "dump": str(_operator_pause_state.get("dump") or ""),
            "ai_dump": str(_operator_pause_state.get("ai_dump") or ""),
            "attempts": list(_operator_pause_state.get("attempts") or []),
            "ts": str(_operator_pause_state.get("ts") or ""),
        }


def operator_pause_and_wait(
    serial: str,
    step_id: str,
    message: str,
    attempts: Optional[List[dict]] = None,
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    success_recheck: Optional[Callable[[], bool]] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> None:
    """Остановить пайплайн до «Продолжить»; при success_recheck — ждать пока цель не достигнута."""
    port = (serial or ADB_PORT or "").strip()
    msg = (message or "Требуется действие на эмуляторе").strip()
    step = (step_id or "step").strip()[:80]

    def _stopped() -> bool:
        if should_stop and should_stop():
            return True
        try:
            if _onboarding_should_stop():
                return True
        except Exception:
            pass
        return False

    ai_dump_cached = ""
    while True:
        if _stopped():
            raise RuntimeError("Остановлено пользователем")

        ui_snippet = _operator_ui_snippet(port)
        dump = _format_operator_dump(step, msg, attempts, ui_snippet)
        if OPERATOR_PAUSE_LLM_DUMP and not ai_dump_cached:
            ai_dump_cached = _generate_operator_ai_dump(port, step, msg, attempts, ui_snippet, log_func)
        ai_dump = ai_dump_cached

        with _operator_pause_lock:
            _operator_pause_resume.clear()
            _operator_pause_state.update(
                {
                    "active": True,
                    "serial": port,
                    "step": step,
                    "message": msg,
                    "dump": dump,
                    "ai_dump": ai_dump,
                    "attempts": list(attempts or []),
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

        append_operator_ai_dump(
            {
                "serial": port,
                "step": step,
                "message": msg,
                "dump": dump,
                "ai_dump": ai_dump,
                "attempts": list(attempts or []),
            }
        )

        notify_operator_help(port, msg)
        _onboarding_log(f"⏸ Пауза [{step}]: {msg}")
        with _onboarding_lock:
            if _onboarding_job.get("state") in ("running", "stopping"):
                _onboarding_job["state"] = "paused"

        while True:
            if _stopped():
                with _operator_pause_lock:
                    _operator_pause_state["active"] = False
                raise RuntimeError("Остановлено пользователем")
            if _operator_pause_resume.wait(timeout=0.45):
                break

        with _operator_pause_lock:
            _operator_pause_state["active"] = False
        with _onboarding_lock:
            if _onboarding_job.get("state") == "paused":
                _onboarding_job["state"] = "running"
        _onboarding_log("▶ Продолжение после паузы оператора")

        if success_recheck is None or success_recheck():
            return
        msg = msg + " (цель шага ещё не достигнута — проверьте экран и нажмите «Продолжить» снова)"


def operator_continue() -> bool:
    with _operator_pause_lock:
        if not _operator_pause_state.get("active"):
            return False
        _operator_pause_resume.set()
    return True


def offerup_tap_by_ui_text(
    serial: Optional[str],
    *labels: str,
    log_func: Optional[Callable[[str], None]] = None,
    timeout: float = 2.8,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
    exact: bool = False,
) -> bool:
    """Тап только по тексту/content-desc из UI dump (без фиксированных координат)."""
    if not labels:
        return False
    port = (serial or ADB_PORT or "").strip()
    if not port:
        return False
    from mumu_onboarding import _wait_tap_text

    return _wait_tap_text(
        port,
        tuple(str(x) for x in labels if str(x).strip()),
        float(timeout),
        log=log_func,
        poll=_ONBOARDING_TAP_POLL_SEC,
        y_min_frac=y_min_frac,
        y_max_frac=y_max_frac,
        x_min_frac=x_min_frac,
        x_max_frac=x_max_frac,
        exact=exact,
    )


def _offerup_find_inbox_tab_xy_on_root(
    root: ET.Element,
    serial: Optional[str] = None,
) -> Optional[Tuple[int, int]]:
    """Координаты вкладки Inbox на уже полученном UI dump."""
    port = (serial or ADB_PORT or "").strip()
    sw, sh = _offerup_screen_wh(port, root)
    for rid_sub in (
        "tab-bar-widget.tab.inbox.touchable-opacity",
        "tab.inbox.touchable-opacity",
        "tab-bar-widget.tab.inbox",
        "tab.inbox",
    ):
        xy = _offerup_find_tap_by_resource_id(
            root,
            rid_sub,
            scr_w=sw,
            scr_h=sh,
            y_min_frac=0.84,
            y_max_frac=1.0,
            require_clickable=True,
            package_hint="com.offerup",
        )
        if xy and sw and sh and _offerup_in_inbox_nav_zone(xy[0], xy[1], sw, sh):
            return xy
    xy = _offerup_find_inbox_tab(root)
    if xy and sw and sh and _offerup_in_inbox_nav_zone(xy[0], xy[1], sw, sh):
        return xy
    return None


def _offerup_ui_has_bottom_nav(root: Optional[ET.Element]) -> bool:
    if root is None:
        return False
    return _offerup_find_inbox_tab_xy_on_root(root) is not None


def _offerup_tap_inbox_tab_from_root(
    serial: Optional[str],
    root: ET.Element,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    xy = _offerup_find_inbox_tab_xy_on_root(root, serial)
    if not xy:
        return False
    if log_func:
        log_func(f"Inbox tab: resource-id → {xy}")
    _offerup_tap_xy(serial, xy, allow_bottom_nav=True, log_func=log_func)
    return True


def _offerup_foreground_is_offerup(
    serial: Optional[str],
    root_hint: Optional[ET.Element] = None,
) -> bool:
    """OfferUp в фокусе: dumpsys; при пустом dumpsys — UI (панель или переданный root)."""
    fg = _adb_foreground_package(serial)
    if fg == "com.offerup":
        return True
    if fg and fg != "com.offerup":
        return False
    if root_hint is not None:
        return _offerup_root_shows_offerup_app(root_hint) or _offerup_ui_has_bottom_nav(root_hint)
    port = (serial or ADB_PORT or "").strip()
    if not port:
        return False
    root = dump_ui_serial(port)
    return _offerup_root_shows_offerup_app(root) or _offerup_ui_has_bottom_nav(root)


def _offerup_dump_nav_after_settle(
    serial: Optional[str],
    log_func: Callable[[str], None],
    should_stop: Optional[Callable[[], bool]] = None,
) -> Optional[ET.Element]:
    """Пауза 3 с + один uiautomator dump (OfferUp уже на экране)."""
    port = (serial or ADB_PORT or "").strip()
    if not port:
        return None
    _offerup_settle_before_nav_check(log_func, should_stop=should_stop)
    t0 = time.monotonic()
    root = dump_ui_serial(port)
    if root is not None and _offerup_ui_has_bottom_nav(root):
        if log_func:
            log_func(f"Inbox scan: нижняя панель видна (~{time.monotonic() - t0:.1f}s после паузы)")
        return root
    if log_func:
        log_func("Inbox scan: нижняя панель не видна в UI dump")
    return None


def _offerup_wait_bottom_nav_ready(
    serial: Optional[str],
    log_func: Callable[[str], None],
    timeout: float = OFFERUP_INBOX_NAV_READY_SEC,
    should_stop: Optional[Callable[[], bool]] = None,
    *,
    launching: bool = False,
) -> Optional[ET.Element]:
    """После monkey: пауза 3 с, затем poll до timeout (только при cold start)."""
    port = (serial or ADB_PORT or "").strip()
    if not port:
        return None
    if not launching:
        return _offerup_dump_nav_after_settle(serial, log_func, should_stop=should_stop)
    _offerup_settle_before_nav_check(log_func, should_stop=should_stop)
    if SmartWaiter is not None:
        waiter = SmartWaiter(port, poll=0.20)

        def _nav_ready(root: Optional[ET.Element]) -> bool:
            return root is not None and _offerup_ui_has_bottom_nav(root)

        root = waiter.wait_until(_nav_ready, timeout=float(timeout), should_stop=should_stop)
        if root is not None and log_func:
            log_func("Inbox scan: нижняя панель готова (SmartWaiter)")
        elif log_func:
            log_func(f"Inbox scan: нижняя панель не появилась за {int(timeout)}s")
        return root
    t0 = time.monotonic()
    deadline = t0 + max(0.5, float(timeout))
    attempt = 0
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return None
        attempt += 1
        root = dump_ui_serial(port)
        if root is not None and _offerup_ui_has_bottom_nav(root):
            if log_func:
                log_func(f"Inbox scan: нижняя панель готова (~{time.monotonic() - t0:.1f}s)")
            return root
        time.sleep(0.4)
    if log_func:
        log_func(f"Inbox scan: нижняя панель не появилась за {int(timeout)}s")
    return None


def _offerup_dismiss_blockers_from_root(
    serial: Optional[str],
    root: ET.Element,
    log_func: Callable[[str], None],
) -> bool:
    """Попапы/Post item — на уже загруженном dump, без цепочки повторных uiautomator."""
    blob = " ".join(_ui_visible_texts(root)).lower()
    if "add a location to your profile" in blob or "build local trust" in blob:
        for node in root.iter("node"):
            if node.get("clickable", "false") != "true":
                continue
            t = _collapse_ws((node.get("text") or "") + " " + (node.get("content-desc") or "")).lower()
            if "add this location" in t or "add location to profile" in t:
                pr = parse_bounds(node.get("bounds") or "")
                if pr:
                    _offerup_tap_xy(serial, ((pr[0] + pr[2]) // 2, (pr[1] + pr[3]) // 2), log_func=log_func)
                    time.sleep(0.25)
                    return True
    blob = " ".join(_ui_visible_texts(root)).lower()
    if _ITEM_REMOVED_MARKERS.search(blob):
        gi = _offerup_find_got_it(root)
        if gi:
            _offerup_tap_xy(serial, gi, log_func=log_func)
            time.sleep(0.25)
            return True
    popup_markers = ("premium", "members", "free trial", "per month", "early access", "inbox priority")
    if any(m in blob for m in popup_markers):
        xy = _offerup_find_dismiss_popup(root)
        if xy:
            _offerup_tap_xy(serial, xy, log_func=log_func)
            time.sleep(0.25)
            return True
    texts = " ".join(_ui_visible_texts(root))
    if _offerup_ui_is_post_item_compose(texts):
        xy = _offerup_find_top_left_close(root)
        if xy:
            log_func(f"Inbox scan: закрываю «Post an item» (крестик {xy})")
            _offerup_tap_xy(serial, xy, log_func=log_func)
            time.sleep(0.25)
        else:
            log_func("Inbox scan: «Post an item» — BACK")
            _offerup_keyevent(serial, "4")
            time.sleep(0.2)
        return True
    return False


def offerup_open_inbox_tab(
    serial: Optional[str],
    log_func: Optional[Callable[[str], None]] = None,
    root: Optional[ET.Element] = None,
) -> bool:
    """Вкладка Inbox — один dump (или переданный root), resource-id, короткий fallback по тексту."""
    port = (serial or ADB_PORT or "").strip()
    root_use = root
    if root_use is None and port:
        root_use = dump_ui_serial(port)
    if root_use is not None and _offerup_tap_inbox_tab_from_root(serial, root_use, log_func):
        return True
    if offerup_tap_by_ui_text(
        serial,
        "Inbox",
        log_func=log_func,
        timeout=1.2,
        y_min_frac=0.84,
        y_max_frac=1.0,
        exact=True,
    ):
        return True
    return offerup_tap_by_ui_text(
        serial,
        "Inbox",
        log_func=log_func,
        timeout=1.5,
        y_min_frac=0.84,
        y_max_frac=1.0,
        exact=False,
    )


def _parse_coach_actions_from_reply(reply: str) -> Tuple[str, List[dict]]:
    """Убрать блок coach_actions из текста для чата; вернуть список действий."""
    s = reply or ""
    m = _COACH_ACTIONS_BLOCK_RE.search(s)
    if not m:
        return s.strip(), []
    body = (m.group(1) or "").strip()
    display = (s[: m.start()] + s[m.end() :]).strip()
    actions: List[dict] = []
    try:
        parsed = json.loads(body)
        if isinstance(parsed, list):
            actions = [x for x in parsed if isinstance(x, dict)]
    except json.JSONDecodeError:
        pass
    return display, actions


def _coach_actions_user_success(result: dict, actions: List[dict]) -> bool:
    """Успех для оператора: сообщение/ссылка реально ушли, а не только «чат открыт»."""
    types = {str(a.get("type") or "").strip().lower() for a in actions if isinstance(a, dict)}
    if types & {"send_chat_message", "send_message"}:
        return bool(result.get("message_sent"))
    if types & {"send_template_link", "send_link", "send_xgd_template"}:
        return bool(result.get("link_sent"))
    if types & {"open_inbox_chat", "open_chat"}:
        return bool(result.get("chat_opened"))
    return int(result.get("steps_ok") or 0) > 0


def _execute_coach_actions(
    actions: List[dict],
    serial: Optional[str],
    log_func: Callable[[str], None],
    seller_hint: str = "",
    ui_state: str = "",
) -> dict:
    result: dict = {
        "steps_ok": 0,
        "message_sent": False,
        "chat_opened": False,
        "link_sent": False,
    }
    port = (serial or ADB_PORT or "").strip()
    if port and not probe_adb_reachable(port):
        log_func(f"coach: ADB offline ({port})")
        return result
    hint = (seller_hint or "").strip()
    for act in actions:
        if isinstance(act, dict) and str(act.get("seller") or act.get("name") or "").strip():
            hint = str(act.get("seller") or act.get("name") or "").strip()
            break
    needs_chat = any(
        str(a.get("type") or "").strip().lower()
        in ("send_chat_message", "send_message", "send_template_link", "type_text")
        for a in actions
        if isinstance(a, dict)
    )
    if needs_chat and ui_state in ("inbox", "offerup_other", ""):
        if _coach_ensure_chat_open(serial, hint, log_func):
            result["chat_opened"] = True
    for act in actions:
        if not isinstance(act, dict):
            continue
        t = str(act.get("type") or "").strip().lower()
        label = str(act.get("label") or "")[:80]
        if t in ("tap_frac", "tap", "swipe_frac"):
            needles = [str(act.get("text") or act.get("label") or "")]
            notify_operator_help(
                serial,
                f"нажмите «{needles[0]}» на эмуляторе (координаты отключены — только UI)",
            )
            log_func(f"coach: отклонён {t} — нужен tap_text")
            continue
        if t in ("tap_text", "tap_label", "tap_ui"):
            raw_labels = act.get("labels") or act.get("text") or act.get("label") or ""
            if isinstance(raw_labels, list):
                needles = [str(x) for x in raw_labels if str(x).strip()]
            else:
                needles = [str(raw_labels).strip()] if str(raw_labels).strip() else []
            needles = [n for n in needles if not _coach_is_bad_tap_label(n)]
            if not needles:
                log_func("coach: tap_text отклонён (you:/Inbox/back)")
                continue
            y_min = float(act.get("y_min_frac", 0.0))
            if y_min <= 0.0:
                y_min = 0.12
            y_max = float(act.get("y_max_frac", 1.0))
            ok = offerup_tap_by_ui_text(
                serial,
                *needles,
                log_func=log_func,
                timeout=float(act.get("timeout") or _COACH_ACTION_TAP_TIMEOUT_SEC),
                y_min_frac=y_min,
                y_max_frac=y_max,
                exact=bool(act.get("exact")),
            )
            if ok:
                result["steps_ok"] = int(result["steps_ok"]) + 1
                if label:
                    log_func(f"coach: {label}")
            else:
                human = " / ".join(needles)
                ob_active = False
                try:
                    with _onboarding_lock:
                        ob_active = _onboarding_job.get("state") in ("running", "paused")
                except Exception:
                    pass
                if OPERATOR_PAUSE_ON_FAIL and ob_active and serial:
                    operator_pause_and_wait(
                        serial,
                        "coach_tap:" + human[:40],
                        f"Коуч не смог нажать «{human}». Нажмите вручную на эмуляторе и «Продолжить».",
                        [{"method": "tap_text", "labels": needles}],
                        log_func=log_func,
                    )
                    result["steps_ok"] = int(result["steps_ok"]) + 1
                else:
                    notify_operator_help(serial, f"нажмите «{human}» на эмуляторе")
                log_func(f"coach: UI не нашёл «{human}»")
        elif t in ("open_inbox_chat", "open_chat"):
            seller = str(act.get("seller") or act.get("name") or act.get("text") or hint or "")
            if _coach_open_inbox_chat(serial, seller, log_func):
                result["chat_opened"] = True
                result["steps_ok"] = int(result["steps_ok"]) + 1
        elif t in ("chat_back", "back_to_inbox"):
            _offerup_tap_chat_back(serial, log_func)
            time.sleep(0.22)
            log_func("coach: chat_back")
            result["steps_ok"] = int(result["steps_ok"]) + 1
        elif t in ("open_inbox", "open_inbox_tab"):
            if offerup_open_inbox_tab(serial, log_func):
                log_func("coach: open_inbox_tab")
                result["steps_ok"] = int(result["steps_ok"]) + 1
        elif t == "press_back":
            if serial:
                run_adb_serial(serial, ["shell", "input", "keyevent", "4"])
            else:
                run_adb(["shell", "input", "keyevent", "4"])
            log_func("coach: press_back")
            result["steps_ok"] = int(result["steps_ok"]) + 1
        elif t in ("launch_offerup", "launch_offerup_app"):
            _offerup_launch_app(serial, log_func)
            result["steps_ok"] = int(result["steps_ok"]) + 1
        elif t == "dismiss_popup":
            _offerup_dismiss_if_popup(serial)
            log_func("coach: dismiss_popup")
            result["steps_ok"] = int(result["steps_ok"]) + 1
        elif t in ("tap_rid", "tap_resource_id", "resource_id", "rid_tap"):
            rid_key = str(act.get("rid_key") or act.get("key") or "").strip()
            rid_raw = str(
                act.get("resource_id") or act.get("rid") or act.get("value") or act.get("substring") or ""
            ).strip()
            ok_rid = False
            try:
                from mumu_rid_optimizations import OFFERUP_RID_MAP, offerup_tap_rid

                if rid_key:
                    ok_rid = offerup_tap_rid(
                        serial or ADB_PORT or "",
                        rid_key,
                        log_func=log_func,
                        timeout=float(act.get("timeout") or 3.5),
                    )
                elif rid_raw:
                    ok_rid = offerup_tap_by_resource_id(
                        serial,
                        rid_raw,
                        log_func=log_func,
                        timeout=float(act.get("timeout") or 3.5),
                    )
            except Exception as ex:
                log_func(f"coach: tap_rid error: {ex}")
            if ok_rid:
                result["steps_ok"] = int(result["steps_ok"]) + 1
            else:
                label = rid_key or rid_raw or "?"
                notify_operator_help(serial, f"не найден rid «{label}» на экране")
                log_func(f"coach: tap_rid не выполнен ({label})")
        elif t in ("send_chat_message", "send_message"):
            txt = str(act.get("value") or act.get("text") or "")[:500]
            if txt and serial:
                if not _offerup_chat_has_message_compose(dump_ui_serial(serial)):
                    if _coach_ensure_chat_open(serial, hint, log_func):
                        result["chat_opened"] = True
                if send_text_to_current_chat(txt, serial, log_func=log_func):
                    log_func(f"coach: send_chat_message ({len(txt)} chars)")
                    result["message_sent"] = True
                    result["steps_ok"] = int(result["steps_ok"]) + 1
                    _maybe_learn_inbox_from_coach_send(hint or "", txt)
                else:
                    log_func("coach: send_chat_message failed — текст не ушёл (проверьте Send)")
        elif t in ("send_template_link", "send_link", "send_xgd_template"):
            if not _offerup_chat_has_message_compose(dump_ui_serial(serial)):
                if _coach_ensure_chat_open(serial, hint, log_func):
                    result["chat_opened"] = True
            if _coach_send_template_link_for_chat(serial, hint, log_func):
                log_func("coach: send_template_link")
                result["link_sent"] = True
                result["steps_ok"] = int(result["steps_ok"]) + 1
        elif t == "type_text":
            txt = str(act.get("value") or act.get("text") or "")[:500]
            if txt and serial:
                root = dump_ui_serial(serial)
                if _offerup_chat_has_message_compose(root):
                    if send_text_to_current_chat(txt, serial, log_func=log_func):
                        log_func(f"coach: type_text→chat ({len(txt)} chars)")
                        result["message_sent"] = True
                        result["steps_ok"] = int(result["steps_ok"]) + 1
                else:
                    run_adb_serial(serial, ["shell", f"input text {_posix_quote(txt)}"])
                    log_func(f"coach: type_text ({len(txt)} chars)")
                    result["steps_ok"] = int(result["steps_ok"]) + 1
    return result

# Параметры GET …/ads/offerup (см. api documentation.txt). Неизвестные ключи с клиента отбрасываются.
OFFERUP_PARSER_QUERY_KEYS = frozenset(
    {
        "category",
        "price",
        "ads",
        "reviews",
        "sells",
        "buys",
        "publication",
        "registration",
        "blacklist",
        "country",
        "delivery",
        "phone",
        "email",
        "views",
        "limit",
    }
)
OFFERUP_PARSER_MAX_PARAM_LEN = 600
OFFERUP_PARSER_MAX_BLACKLIST_LEN = 2500
OFFERUP_CATEGORY_HELP = (
    "OfferUp: 1=бизнес, 2=детские, 3=дом и сад, 4=здоровье, 5=игрушки, 6=коллекции, "
    "7=одежда, 8=прочее, 9=свадьба, 10=спорт, 11=животные, 12=электроника"
)

# Категории OfferUp (документация парсера): id для API + имя в UI + подстроки для разбора ответа API.
OFFERUP_CATEGORIES: List[Dict[str, object]] = [
    {"id": 1, "name": "Бизнес", "match": ("бизнес", "business")},
    {"id": 2, "name": "Детские товары", "match": ("детск", "kids", "children")},
    {"id": 3, "name": "Дом и сад", "match": ("дом", "garden", "home")},
    {"id": 4, "name": "Здоровье и красота", "match": ("здоров", "красот", "beauty", "health")},
    {"id": 5, "name": "Игрушки и игры", "match": ("игруш", "toys", "games")},
    {"id": 6, "name": "Коллекционирование", "match": ("коллекц", "collect")},
    {"id": 7, "name": "Одежда", "match": ("одежд", "clothing", "apparel")},
    {"id": 8, "name": "Прочее", "match": ("прочее", "other", "misc")},
    {"id": 9, "name": "Свадебные товары", "match": ("свадеб", "wedding")},
    {"id": 10, "name": "Спорт и отдых", "match": ("спорт", "sport", "fitness")},
    {"id": 11, "name": "Товары для животных", "match": ("животн", "pet", "pets")},
    {"id": 12, "name": "Электроника", "match": ("электрон", "electronics", "electronic", "tech")},
]


def offerup_categories_for_api() -> List[Dict[str, object]]:
    return [{"id": c["id"], "name": c["name"]} for c in OFFERUP_CATEGORIES]


def offerup_normalize_name_to_id(raw: str) -> Optional[int]:
    s = (raw or "").strip().lower()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return n if 1 <= n <= 12 else None
    for c in OFFERUP_CATEGORIES:
        cid = int(c["id"])
        if s == str(cid):
            return cid
        nm = str(c["name"]).lower()
        if s == nm or nm in s or s in nm:
            return cid
        for m in c.get("match") or ():
            ms = str(m).lower()
            if ms in s or s in ms:
                return cid
    return None


def offerup_category_id_from_item(item: Optional[dict]) -> Optional[int]:
    if not item or not isinstance(item, dict):
        return None
    for key in (
        "category_id",
        "category",
        "offerup_category",
        "cat_id",
        "cat",
        "category_name",
        "category_title",
    ):
        if key not in item:
            continue
        v = item.get(key)
        if v is None:
            continue
        if isinstance(v, int) and 1 <= v <= 12:
            return v
        cid = offerup_normalize_name_to_id(str(v))
        if cid is not None:
            return cid
    return None


def offerup_category_id_from_filter_params(fp: Dict[str, str]) -> Optional[int]:
    raw = (fp.get("category") or "").strip()
    if not raw:
        return None
    first = raw.split(",")[0].strip()
    return offerup_normalize_name_to_id(first)


def offerup_resolve_category_for_template(
    item: Optional[dict],
    filter_params: Dict[str, str],
) -> Optional[int]:
    cid = offerup_category_id_from_item(item)
    if cid is not None:
        return cid
    return offerup_category_id_from_filter_params(filter_params)


def offerup_sanitize_template_rules(raw: object) -> List[dict]:
    if not isinstance(raw, list):
        return []
    out: List[dict] = []
    for x in raw[:24]:
        if not isinstance(x, dict):
            continue
        try:
            cid = int(x.get("category_id", -1))
        except (TypeError, ValueError):
            continue
        if cid < 0 or cid > 12:
            continue
        txt = str(x.get("text") or "").strip()
        if not txt:
            continue
        if len(txt) > 400:
            txt = txt[:400]
        out.append({"category_id": cid, "text": txt})
    return out


def offerup_pick_template_text(
    category_id: Optional[int],
    rules: List[dict],
) -> Tuple[str, str]:
    """Текст для тапа в Chrome и метка для лога."""
    if not rules:
        if not OFFERUP_TEMPLATE_LABELS:
            raise RuntimeError("Нет правил шаблонов и пуст OFFERUP_TEMPLATE_LABELS в app.py")
        idx = random.randrange(len(OFFERUP_TEMPLATE_LABELS))
        t = OFFERUP_TEMPLATE_LABELS[idx]
        return t, f"случайно из app.py [{idx}]"
    for r in rules:
        try:
            rid = int(r.get("category_id", -1))
        except (TypeError, ValueError):
            continue
        if rid == 0:
            continue
        txt = str(r.get("text") or "").strip()
        if not txt:
            continue
        if category_id is not None and rid == category_id:
            return txt, f"правило «{next((c['name'] for c in OFFERUP_CATEGORIES if int(c['id']) == rid), rid)}»"
    for r in rules:
        try:
            rid = int(r.get("category_id", -1))
        except (TypeError, ValueError):
            continue
        if rid != 0:
            continue
        txt = str(r.get("text") or "").strip()
        if txt:
            return txt, "правило «По умолчанию»"
    if not OFFERUP_TEMPLATE_LABELS:
        raise RuntimeError("Нет шаблонов в OFFERUP_TEMPLATE_LABELS и нет правил")
    idx = random.randrange(len(OFFERUP_TEMPLATE_LABELS))
    t = OFFERUP_TEMPLATE_LABELS[idx]
    return t, f"случайно из app.py [{idx}]"


# ============================================================
# ДОПОЛНИТЕЛЬНЫЕ ПУТИ ADB
# ============================================================
ADB_CANDIDATES = [
    r"E:\MuMuPlayerGlobal\nx_device\12.0\shell\adb.exe",
    r"D:\Program Files\Netease\MuMuPlayer\nx_device\12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer\nx_device\12.0\shell\adb.exe",
    r"C:\Program Files\MuMu\emulator\nemu\vtools\adb.exe",
    r"C:\Program Files (x86)\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"D:\Program Files\MuMu\emulator\nemu\vtools\adb.exe",
]

# ============================================================
# ТАЙМИНГИ (баланс скорости и стабильности мессенджера)
# ============================================================
POST_TAP_DELAY_SEC       = 0.04
POST_TEXT_DELAY_SEC      = 0.018
POST_INPUT_BEFORE_SEND   = 0.05
# Пауза после «Отправить» до следующего цикла (dump UI уже даёт паузу по ADB)
BETWEEN_MESSAGES_SEC     = 0.08
UI_DUMP_PATH             = "/sdcard/mumu_paster_ui.xml"
# Один вызов input text на Android часто падает/обрезает длинные строки — режем без %s
ADB_INPUT_TEXT_CHUNK     = 100

# Профили задержек для /api/send: timing = "fast" | "normal" | "stable"
# (tap после фокуса, после ввода символов, перед отправкой, между сообщениями)
TIMING_PROFILES: dict[str, Tuple[float, float, float, float]] = {
    "fast": (0.042, 0.016, 0.048, 0.09),
    "normal": (0.055, 0.025, 0.065, 0.12),
    "stable": (0.09, 0.04, 0.11, 0.32),
}


def sanitize_adb_timing_overrides(raw: object, fallback: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    if not isinstance(raw, dict):
        return fallback
    keys = ("post_tap", "post_text", "post_input_before_send", "between_messages")
    vals = list(fallback)
    for idx, key in enumerate(keys):
        if key not in raw or raw[key] in (None, ""):
            continue
        try:
            val = float(raw[key])
        except (TypeError, ValueError):
            continue
        vals[idx] = max(0.0, min(val, 30.0))
    return vals[0], vals[1], vals[2], vals[3]

# Доп. корни для поиска портов в конфигах MuMu (если пусто — только рядом с adb)
MUMU_CONFIG_EXTRA_ROOTS: List[str] = []

# Типичные порты MuMu (для проверки, если конфиг не дал результат)
MUMU_PORT_PROBE_LIST = [
    16384, 16416, 16448, 16480, 16512, 16544,
    21503, 21513, 21523,
    5555,
]
# Не MuMu-сессии, но часто висят в adb devices — скрываем из UI.
MUMU_HIDDEN_ADB_ADDRESSES = frozenset({"127.0.0.1:7555", "127.0.0.1:5555"})
# Максимум адресов для проверки shell (остальные только в списке без probe)
MAX_PORT_PROBES = 24

# ============================================================

app = Flask(__name__, static_folder=".", static_url_path="")

_ADB_RESOLVED: Optional[str] = None

# ── История отправленных ссылок (в рамках сессии) ──────────
_sent_urls: set[str] = set()


# Small runtime caches for hot ADB/UI paths. They deliberately have short TTLs:
# enough to avoid repeated filesystem scans and reconnect chatter, but not long
# enough to hide emulator/session changes from the UI.
_ADB_CONNECT_CACHE_TTL_SEC = 8.0
_ADB_CONNECT_CACHE: dict[str, float] = {}
_ADB_CONNECT_LOCK = threading.Lock()

_ADB_WM_SIZE_CACHE_TTL_SEC = 20.0
_ADB_WM_SIZE_CACHE: dict[str, tuple[float, Tuple[int, int]]] = {}
_ADB_WM_SIZE_CACHE_LOCK = threading.Lock()

_PORTS_CONFIG_CACHE_TTL_SEC = 20.0
_PORTS_CONFIG_CACHE_LOCK = threading.Lock()

_UI_DUMP_CACHE_LOCK = threading.RLock()
_UI_DUMP_CACHE: Dict[str, Tuple[float, ET.Element]] = {}
_PORTS_CONFIG_CACHE: dict[str, object] = {"ts": 0.0, "roots": (), "found": {}, "sessions": {}}

_DEVICE_LABEL_CACHE_TTL_SEC = 30.0
_DEVICE_LABEL_CACHE_LOCK = threading.Lock()
_DEVICE_PROFILE_LABEL_CACHE: dict[str, tuple[float, str]] = {}
_DEVICE_FALLBACK_LABEL_CACHE: dict[str, tuple[float, str]] = {}

_PERSISTENT_STATE_LAST_TEXT = ""
_UI_CONFIG_LAST_TEXT = ""


# ── ADB helpers ────────────────────────────────────────────

def get_adb() -> Optional[str]:
    global _ADB_RESOLVED
    if _ADB_RESOLVED and os.path.isfile(_ADB_RESOLVED):
        return _ADB_RESOLVED
    ordered = []
    env = (os.environ.get("MUMU_ADB") or "").strip()
    if env:
        ordered.append(env)
    for p in [ADB_PATH] + ADB_CANDIDATES:
        if p and p not in ordered:
            ordered.append(p)
    for p in ordered:
        if p and os.path.isfile(p):
            _ADB_RESOLVED = p
            return p
    w = shutil.which("adb")
    if w:
        _ADB_RESOLVED = w
        return w
    return None


def run_adb_serial(serial: str, args: list) -> Optional[subprocess.CompletedProcess]:
    adb = get_adb()
    if not adb:
        return None
    cmd = [adb, "-s", serial] + args
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=25
        )
    except Exception:
        return None
    if r and len(args) >= 2 and str(args[0]) == "shell":
        blob = " ".join(str(x) for x in args[1:]).lower()
        if any(x in blob for x in ("input tap", "input swipe", "input text", "input keyevent")):
            invalidate_ui_dump_cache(serial)
    return r


def run_adb(args: list) -> Optional[subprocess.CompletedProcess]:
    return run_adb_serial(ADB_PORT, args)


def adb_connect_serial(addr: str, *, force: bool = False) -> None:
    adb = get_adb()
    if not adb:
        return
    addr = (addr or "").strip()
    if not addr:
        return
    now = time.monotonic()
    if not force:
        with _ADB_CONNECT_LOCK:
            if now - _ADB_CONNECT_CACHE.get(addr, 0.0) < _ADB_CONNECT_CACHE_TTL_SEC:
                return
    try:
        r = subprocess.run(
            [adb, "connect", addr],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8
        )
    except Exception:
        return
    if r.returncode == 0:
        with _ADB_CONNECT_LOCK:
            _ADB_CONNECT_CACHE[addr] = time.monotonic()


def adb_connect():
    adb_connect_serial(ADB_PORT)


def _adb_wm_size_wh(serial: str) -> Tuple[int, int]:
    """Размер экрана из `wm size` — быстрее, чем uiautomator dump (для тапа по Inbox)."""
    addr = (serial or "").strip()
    if not addr:
        return (0, 0)
    now = time.monotonic()
    with _ADB_WM_SIZE_CACHE_LOCK:
        cached = _ADB_WM_SIZE_CACHE.get(addr)
        if cached and now - cached[0] < _ADB_WM_SIZE_CACHE_TTL_SEC:
            return cached[1]
    r = run_adb_serial(addr, ["shell", "wm", "size"])
    if not r or r.returncode != 0:
        return (0, 0)
    txt = (r.stdout or "").replace("×", "x")
    best: Tuple[int, int] = (0, 0)
    for m in re.finditer(r"(\d{2,5})\s*[xX]\s*(\d{2,5})", txt):
        w, h = int(m.group(1)), int(m.group(2))
        if w * h > best[0] * best[1]:
            best = (w, h)
    if best != (0, 0):
        with _ADB_WM_SIZE_CACHE_LOCK:
            _ADB_WM_SIZE_CACHE[addr] = (time.monotonic(), best)
    return best


def _truthy_envish(v: object, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if not s:
        return default
    return s in ("1", "true", "yes", "on")


def adb_screencap_png_base64(serial: Optional[str]) -> Optional[str]:
    """PNG с устройства → base64 для Ollama / OpenAI vision (без data: URL)."""
    adb = get_adb()
    if not adb:
        return None
    addr = (serial or ADB_PORT or "").strip()
    if not addr:
        return None
    adb_connect_serial(addr)
    cmd = [adb, "-s", addr, "exec-out", "screencap", "-p"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=22)
    except Exception:
        return None
    if p.returncode != 0 or not p.stdout:
        return None
    data = p.stdout
    if len(data) < 80 or len(data) > 5_500_000:
        return None
    if not (data[:8].startswith(b"\x89PNG") or data[:2] in (b"\xff\xd8",)):
        return None
    return base64.b64encode(data).decode("ascii")


def _http_get_binary_limited(url: str, max_bytes: int = 2_400_000) -> Optional[bytes]:
    u = (url or "").strip()
    if not u.startswith(("http://", "https://")):
        return None
    req = urllib.request.Request(u, headers={"User-Agent": "MumuPaster/1"})
    try:
        with urllib.request.urlopen(req, timeout=14) as resp:
            chunk = resp.read(max_bytes + 1)
    except Exception:
        return None
    if len(chunk) > max_bytes:
        return None
    return chunk if len(chunk) > 40 else None


def _listing_image_base64_for_llm(link_row: dict) -> Optional[str]:
    u = str(link_row.get("image") or link_row.get("photo") or "").strip()
    raw = _http_get_binary_limited(u)
    if not raw:
        return None
    return base64.b64encode(raw).decode("ascii")


# ── Поиск портов MuMu ─────────────────────────────────────

_PORT_IN_TEXT = re.compile(
    r"(?:127\.0\.0\.1|0\.0\.0\.0|localhost)\s*:\s*(\d{4,5})"
    r"|\"(?:adb|host|forward)[_A-Za-z]*[Pp]ort\"\s*:\s*(\d{4,5})"
    r"|adb\s+connect\s+[\d.]+\s*:\s*(\d{4,5})",
    re.I,
)


def _collect_config_roots() -> List[str]:
    roots: List[str] = []
    adb = get_adb()
    if adb:
        d = os.path.dirname(adb)
        for _ in range(6):
            roots.append(d)
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    roots.extend(p for p in MUMU_CONFIG_EXTRA_ROOTS if p)
    for guess in (
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "MuMuPlayerGlobal"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "MuMuPlayerGlobal"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "MuMu"),
    ):
        if guess and os.path.isdir(guess) and guess not in roots:
            roots.append(guess)
    seen: set[str] = set()
    out: List[str] = []
    for r in roots:
        r = os.path.abspath(r)
        if r in seen or not os.path.isdir(r):
            continue
        seen.add(r)
        out.append(r)
    return out


def _getprop_value(addr: str, prop: str) -> str:
    r = run_adb_serial(addr, ["shell", "getprop", prop])
    if not r or r.returncode != 0:
        return ""
    return (r.stdout or "").strip()


def _android_device_style_folder_label(dirpath: str) -> str:
    """
    Папки вроде MuMuPlayerGlobal-12.0-2 / …-12.0-3 → «Android device-2» (как в списке MuMu).
    """
    parts = dirpath.replace("/", os.sep).split(os.sep)
    for p in reversed(parts[-10:]):
        if not p or len(p) < 3:
            continue
        pl = p.lower()
        if pl in ("vms", "vm", "shell", "nx_device", "emulator", "configs", "12.0"):
            continue
        m = re.search(r"-(\d+)$", p)
        if not m:
            continue
        n = int(m.group(1))
        if not (1 <= n <= 64):
            continue
        if "mumu" in pl or "nemu" in pl or re.match(r"^s-\d", pl, re.I):
            return f"Android device-{n}"
    return ""


def _folder_session_hint(dirpath: str) -> str:
    """Имя папки VM или Android device-N по суффиксу папки MuMu."""
    from_folder = _android_device_style_folder_label(dirpath)
    if from_folder:
        return from_folder
    parts = dirpath.replace("/", os.sep).split(os.sep)
    for p in reversed(parts[-4:]):
        if not p:
            continue
        pl = p.lower()
        if pl in ("vms", "vm", "shell", "nx_device", "12.0", "emulator"):
            continue
        if re.match(r"^s-\d+-\d+$", pl, re.I):
            return p
        if re.match(r"^mumu[-_]?\d+$", pl, re.I) or "device" in pl or "player" in pl:
            return p.replace("_", " ")
    return ""


def _json_walk_ports_names(obj, acc: List[Tuple[int, str]], inherited_name: str = "") -> None:
    """Ищет в JSON пары (порт ADB, имя сессии/плеера)."""
    name_keys = (
        "player_name", "playerName", "playerDisplayName", "player_display_name",
        "name", "title", "vm_name", "displayName", "display_name",
        "nickName", "instanceName", "playerTitle", "remark", "vmName",
        "tabName", "instanceTitle", "vmDisplayName",
    )
    port_keys = (
        "adb_port", "adbPort", "host_port", "hostPort", "ADB_PORT",
        "forward_port", "forwardPort", "nemu_port", "adbHostPort",
        "adb_forward_port", "adbForwardPort", "hostForwardPort",
    )
    if isinstance(obj, dict):
        sub = inherited_name
        for nk in name_keys:
            v = obj.get(nk)
            if isinstance(v, str) and v.strip():
                sub = v.strip()
                break
        for pk in port_keys:
            v = obj.get(pk)
            port: Optional[int] = None
            if isinstance(v, int) and 1024 < v < 65535:
                port = v
            elif isinstance(v, str) and v.strip().isdigit():
                p = int(v.strip())
                if 1024 < p < 65535:
                    port = p
            if port is not None:
                acc.append((port, sub))
        for v in obj.values():
            _json_walk_ports_names(v, acc, sub)
    elif isinstance(obj, list):
        for v in obj:
            _json_walk_ports_names(v, acc, inherited_name)


def _extract_port_sessions_from_file(path: str, text: str) -> List[Tuple[int, str]]:
    """Порты из файла + подпись сессии из JSON или имени папки."""
    out: List[Tuple[int, str]] = []
    folder_hint = _folder_session_hint(os.path.dirname(path))
    seen_ports: set[int] = set()
    if path.lower().endswith(".json"):
        try:
            data = json.loads(text)
            tmp: List[Tuple[int, str]] = []
            root_player = ""
            if isinstance(data, dict):
                for k in (
                    "player_name", "playerName", "playerDisplayName", "displayName",
                    "name", "nickName", "player_display_name",
                ):
                    v = data.get(k)
                    if isinstance(v, str) and v.strip():
                        root_player = v.strip()
                        break
            _json_walk_ports_names(data, tmp, root_player)
            for port, name in tmp:
                label = (name.strip() or root_player or folder_hint).strip()
                out.append((port, label))
                seen_ports.add(port)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    for m in _PORT_IN_TEXT.finditer(text):
        for g in m.groups():
            if not g:
                continue
            port = int(g)
            if port < 1024 or port > 65535:
                continue
            if port in seen_ports:
                continue
            out.append((port, folder_hint))
            seen_ports.add(port)
    return out


def ports_from_config_files() -> tuple[dict[str, str], dict[str, str]]:
    """(addr -> source, addr -> session name if available)."""
    roots = tuple(_collect_config_roots())
    now = time.monotonic()
    with _PORTS_CONFIG_CACHE_LOCK:
        if (
            now - float(_PORTS_CONFIG_CACHE.get("ts") or 0.0) < _PORTS_CONFIG_CACHE_TTL_SEC
            and _PORTS_CONFIG_CACHE.get("roots") == roots
        ):
            return (
                dict(_PORTS_CONFIG_CACHE.get("found") or {}),
                dict(_PORTS_CONFIG_CACHE.get("sessions") or {}),
            )
    found: dict[str, str] = {}
    sessions: dict[str, str] = {}
    exts = (".json", ".xml", ".ini", ".conf", ".properties", ".txt", ".vm", ".nemu")
    max_file = 1_500_000
    max_files = 400
    scanned = 0
    for root in roots:
        depth0 = root.count(os.sep)
        for dirpath, _dirs, filenames in os.walk(root):
            if dirpath.count(os.sep) - depth0 > 7:
                continue
            for fn in filenames:
                if not fn.lower().endswith(exts):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    sz = os.path.getsize(path)
                except OSError:
                    continue
                if sz > max_file:
                    continue
                try:
                    with open(path, "rb") as f:
                        raw = f.read(max_file)
                except OSError:
                    continue
                text = raw.decode("utf-8", errors="ignore")
                for port, sess in _extract_port_sessions_from_file(path, text):
                    addr = f"127.0.0.1:{port}"
                    if addr not in found:
                        found[addr] = f"конфиг …{path[-48:]}"
                    if sess and not sessions.get(addr):
                        sessions[addr] = sess[:120]
                scanned += 1
                if scanned >= max_files:
                    with _PORTS_CONFIG_CACHE_LOCK:
                        _PORTS_CONFIG_CACHE.update({
                            "ts": time.monotonic(),
                            "roots": roots,
                            "found": dict(found),
                            "sessions": dict(sessions),
                        })
                    return found, sessions
    with _PORTS_CONFIG_CACHE_LOCK:
        _PORTS_CONFIG_CACHE.update({
            "ts": time.monotonic(),
            "roots": roots,
            "found": dict(found),
            "sessions": dict(sessions),
        })
    return found, sessions


def ports_from_adb_devices() -> tuple[dict[str, str], dict[str, str]]:
    """(addr -> источник, addr -> подпись). Не подставляем model: из adb — это модель телефона, не имя профиля MuMu."""
    adb = get_adb()
    out: dict[str, str] = {}
    sessions: dict[str, str] = {}
    if not adb:
        return out, sessions
    try:
        r = subprocess.run(
            [adb, "devices", "-l"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
    except Exception:
        return out, sessions
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        m = re.match(r"([\d.]+:\d+)\s+device(?:\s+(.+))?$", line)
        if not m:
            continue
        addr = m.group(1)
        out[addr] = "adb devices"
    return out, sessions


def _device_label_cache_get(cache: dict[str, tuple[float, str]], addr: str) -> Optional[str]:
    now = time.monotonic()
    with _DEVICE_LABEL_CACHE_LOCK:
        cached = cache.get(addr)
        if cached and now - cached[0] < _DEVICE_LABEL_CACHE_TTL_SEC:
            return cached[1]
    return None


def _device_label_cache_set(cache: dict[str, tuple[float, str]], addr: str, label: str) -> str:
    with _DEVICE_LABEL_CACHE_LOCK:
        cache[addr] = (time.monotonic(), label)
    return label


def session_profile_label_from_device(addr: str) -> str:
    cached = _device_label_cache_get(_DEVICE_PROFILE_LABEL_CACHE, addr)
    if cached is not None:
        return cached
    """
    Имя профиля без модели телефона: сначала свойства MuMu, затем device_name,
    если он не совпадает с ro.product.model (на MuMu часто дублируют модель).
    """
    model = _getprop_value(addr, "ro.product.model")
    props = (
        "ro.mumu.display_name",
        "persist.sys.mumu_name",
        "ro.mumu.vm_name",
        "persist.nemu.multi_instance_name",
        "ro.nemu.multi_instance_name",
        "ro.nemu.name",
        "persist.sys.nemu.display_name",
    )
    for prop in props:
        val = _getprop_value(addr, prop)
        if not val or val.lower() in ("unknown", "[unknown]", "null", "0", "(null)"):
            continue
        if model and val.lower() == model.lower():
            continue
        return _device_label_cache_set(_DEVICE_PROFILE_LABEL_CACHE, addr, val[:120])
    r0 = run_adb_serial(addr, ["shell", "settings", "get", "global", "device_name"])
    if r0 and r0.returncode == 0:
        val0 = (r0.stdout or "").strip()
        if val0 and val0.lower() not in ("null", "0", "unknown"):
            if model and val0.lower() == model.lower():
                return ""
            return _device_label_cache_set(_DEVICE_PROFILE_LABEL_CACHE, addr, val0[:120])
    return _device_label_cache_set(_DEVICE_PROFILE_LABEL_CACHE, addr, "")


def session_label_from_device(addr: str) -> str:
    cached = _device_label_cache_get(_DEVICE_FALLBACK_LABEL_CACHE, addr)
    if cached is not None:
        return cached
    """Имя/модель с устройства после подключения (fallback, если нет профиля в конфиге)."""
    prop_sets = [
        ["shell", "getprop", "ro.mumu.display_name"],
        ["shell", "getprop", "ro.mumu.vm_name"],
        ["shell", "getprop", "persist.sys.mumu_name"],
        ["shell", "getprop", "ro.nemu.short_model"],
        ["shell", "getprop", "ro.nemu.name"],
        ["shell", "getprop", "ro.product.model"],
        ["shell", "getprop", "ro.boot.serialno"],
    ]
    for args in prop_sets:
        r = run_adb_serial(addr, list(args))
        if not r or r.returncode != 0:
            continue
        val = (r.stdout or "").strip()
        if val and val not in ("unknown", "[unknown]"):
            return _device_label_cache_set(_DEVICE_FALLBACK_LABEL_CACHE, addr, val[:120])
    return _device_label_cache_set(_DEVICE_FALLBACK_LABEL_CACHE, addr, "")


def _filter_hidden_mumu_ports(items: List[dict]) -> List[dict]:
    return [x for x in items if str(x.get("address") or "").strip() not in MUMU_HIDDEN_ADB_ADDRESSES]


def probe_adb_reachable(addr: str) -> bool:
    adb = get_adb()
    if not adb:
        return False
    adb_connect_serial(addr)
    for attempt in range(2):
        try:
            r = subprocess.run(
                [adb, "-s", addr, "shell", "echo", "mumu_ok"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2.5
            )
        except Exception:
            return False
        if r.returncode == 0 and "mumu_ok" in (r.stdout or ""):
            return True
        if attempt == 0:
            adb_connect_serial(addr, force=True)
    return False


def discover_mumu_ports(*, probe: bool = True) -> List[dict]:
    merged: dict[str, str] = {}
    session_by_addr: dict[str, str] = {}

    adb_dev, adb_sess = ports_from_adb_devices()
    cfg_dev, cfg_sess = ports_from_config_files()
    merged.update(adb_dev)
    merged.update(cfg_dev)

    # Имя профиля из vm_config (Android device-2 …), не model: из adb devices -l
    for a, s in cfg_sess.items():
        if s:
            session_by_addr[a] = s[:120]
    for a, s in adb_sess.items():
        if s and a not in session_by_addr:
            session_by_addr[a] = s[:120]

    for p in MUMU_PORT_PROBE_LIST:
        addr = f"127.0.0.1:{p}"
        merged.setdefault(addr, "типичный порт MuMu")

    _probe_order = {p: i for i, p in enumerate(MUMU_PORT_PROBE_LIST)}

    def sort_key(a: str) -> Tuple[int, int]:
        try:
            port = int(a.rsplit(":", 1)[-1])
        except ValueError:
            port = 0
        src = merged.get(a, "")
        if src == "adb devices":
            pri = 0
        elif port in _probe_order:
            pri = 1 + _probe_order[port]
        else:
            pri = 1 + len(MUMU_PORT_PROBE_LIST)
        return (pri, port)

    addresses = sorted(merged.keys(), key=sort_key)
    probe_addrs = set(addresses[:MAX_PORT_PROBES]) if probe else set()

    # ── Параллельный probe: все адреса проверяются одновременно ─────────────
    reachable_map: dict[str, bool] = {}
    if probe and probe_addrs:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(len(probe_addrs), 16)) as _ex:
            _futures = {_ex.submit(probe_adb_reachable, a): a for a in probe_addrs}
            for _fut in as_completed(_futures):
                _a = _futures[_fut]
                try:
                    reachable_map[_a] = _fut.result()
                except Exception:
                    reachable_map[_a] = False

    # Для живых сессий подтягиваем имя профиля (тоже параллельно)
    _live = [a for a, ok in reachable_map.items() if ok and not session_by_addr.get(a)]
    if _live:
        from concurrent.futures import ThreadPoolExecutor
        def _fetch_label(addr: str):
            prof = session_profile_label_from_device(addr)
            if prof:
                return addr, prof[:120]
            lab = session_label_from_device(addr)
            return addr, (lab[:120] if lab else "")
        with ThreadPoolExecutor(max_workers=min(len(_live), 8)) as _ex2:
            for _addr, _label in _ex2.map(_fetch_label, _live):
                if _label:
                    session_by_addr[_addr] = _label

    items: List[dict] = []
    for addr in addresses:
        src = merged[addr]
        sess = session_by_addr.get(addr) or ""
        if not probe or addr not in probe_addrs:
            items.append({"address": addr, "source": src, "session": sess or None,
                          "reachable": None, "probed": False})
        else:
            ok = reachable_map.get(addr, False)
            items.append({"address": addr, "source": src, "session": sess or None,
                          "reachable": ok, "probed": True})

    def sort_key_item(x: dict) -> Tuple[int, str]:
        r = x["reachable"]
        pri = 0 if r is True else (1 if r is None else 2)
        return (pri, x["address"])

    items.sort(key=sort_key_item)
    return _filter_hidden_mumu_ports(items)


def _posix_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


@functools.lru_cache(maxsize=8192)
def parse_bounds(bounds: str) -> Optional[Tuple[int, int, int, int]]:
    m = _BOUNDS_RE.match(bounds or "")
    if not m:
        return None
    l, t, r, b = map(int, m.groups())
    if r <= l or b <= t:
        return None
    return l, t, r, b


def screen_size(root: ET.Element) -> Tuple[int, int]:
    w = h = 0
    for node in root.iter("node"):
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            _, _, r, b = pr
            w, h = max(w, r), max(h, b)
    return w, h


def _parse_uiautomator_dump_xml(raw: str) -> Optional[ET.Element]:
    xml = (raw or "").strip()
    if not xml or len(xml) < 30:
        return None
    if "dumped to:" in xml.lower() or "dumped to" in xml.lower():
        lines = xml.splitlines()
        xml_lines = [ln for ln in lines if ln.strip().startswith("<") or ln.strip().startswith("<?")]
        if xml_lines:
            xml = "\n".join(xml_lines)
        elif lines and "dumped" in lines[-1].lower():
            xml = "\n".join(lines[:-1])
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        return None


def invalidate_ui_dump_cache(serial: Optional[str] = None) -> None:
    """Сброс кэша UI dump после тапа/ввода — следующий poll увидит новый экран."""
    with _UI_DUMP_CACHE_LOCK:
        if serial:
            _UI_DUMP_CACHE.pop((serial or "").strip(), None)
        else:
            _UI_DUMP_CACHE.clear()


def _dump_ui_serial_uncached(serial: str) -> Optional[ET.Element]:
    """UI dump: exec-out /dev/stdout (1 ADB call), fallback — shell dump + cat."""
    r = run_adb_serial(serial, ["exec-out", "uiautomator", "dump", "/dev/stdout"])
    if r and r.returncode == 0 and r.stdout:
        root = _parse_uiautomator_dump_xml(r.stdout)
        if root is not None:
            return root
    r = run_adb_serial(serial, ["shell", "uiautomator", "dump", UI_DUMP_PATH])
    if not r or r.returncode != 0:
        return None
    r2 = run_adb_serial(serial, ["exec-out", "cat", UI_DUMP_PATH])
    if not r2 or r2.returncode != 0:
        return None
    return _parse_uiautomator_dump_xml(r2.stdout or "")


def dump_ui_serial(serial: str, *, fresh: bool = False) -> Optional[ET.Element]:
    """UI dump с коротким кэшем (~0.3 с): меньше повторных uiautomator между poll-циклами."""
    key = (serial or "").strip()
    if not key:
        return None
    now = time.monotonic()
    if not fresh:
        with _UI_DUMP_CACHE_LOCK:
            row = _UI_DUMP_CACHE.get(key)
            if row and now - float(row[0]) < _UI_DUMP_CACHE_TTL_SEC:
                return row[1]
    root = _dump_ui_serial_uncached(key)
    if root is not None:
        with _UI_DUMP_CACHE_LOCK:
            _UI_DUMP_CACHE[key] = (time.monotonic(), root)
    return root


def dump_ui() -> Optional[ET.Element]:
    return dump_ui_serial(ADB_PORT)


try:
    from adb_optimizer import SmartWaiter, configure_adb_optimizer, send_text_batch_short

    configure_adb_optimizer(run_adb_serial, dump_ui_serial, invalidate_ui_dump_cache)
except ImportError:
    SmartWaiter = None  # type: ignore
    send_text_batch_short = None  # type: ignore


_OFFERUP_CHROME_FG_MARKERS = (
    "com.android.chrome",
    "com.chrome.",
    "org.chromium",
    "com.google.android.apps.chrome",
    "com.android.browser",
)


def _parse_focus_package_from_dumpsys(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if (
            "mCurrentFocus" not in s
            and "mFocusedApp" not in s
            and "mResumedActivity" not in s
            and "topResumedActivity" not in s
        ):
            continue
        m = re.search(r"\s([a-z][a-z0-9_.]*)/[a-zA-Z0-9_.$]+", s)
        if m:
            return m.group(1).lower()
    return ""


def _adb_foreground_package(serial: Optional[str] = None) -> str:
    """Пакет в фокусе (~0.2–0.5 с). На MuMu «window windows» часто пустой — несколько dumpsys."""
    for cmd in (
        ["shell", "dumpsys", "window", "windows"],
        ["shell", "dumpsys", "activity", "activities"],
        ["shell", "dumpsys", "window"],
    ):
        r = run_adb_serial(serial, cmd) if serial else run_adb(cmd)
        if not r or not r.stdout:
            continue
        pkg = _parse_focus_package_from_dumpsys(r.stdout)
        if pkg:
            return pkg
    return ""


def _offerup_root_shows_offerup_app(root: Optional[ET.Element]) -> bool:
    if root is None:
        return False
    if not _offerup_ui_has_bottom_nav(root):
        return False
    return any((n.get("package") or "").lower() == "com.offerup" for n in root.iter("node"))


def _offerup_settle_before_nav_check(
    log_func: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    sec = float(OFFERUP_INBOX_NAV_SETTLE_SEC)
    if sec <= 0:
        return
    if log_func:
        log_func(f"Inbox scan: пауза {sec:.0f} с перед проверкой нижней панели…")
    deadline = time.monotonic() + sec
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return
        time.sleep(min(0.3, max(0.05, deadline - time.monotonic())))


def _offerup_fg_needs_ui_dump(pkg: str, *, after_open_tap: bool) -> bool:
    p = (pkg or "").strip().lower()
    if not p:
        return True
    if p == "com.offerup":
        return True
    if after_open_tap:
        return p == "com.offerup"
    for mark in _OFFERUP_CHROME_FG_MARKERS:
        if mark in p:
            return True
    return False


def find_edit_and_send(root: ET.Element) -> Optional[Tuple[Tuple[int,int], Tuple[int,int]]]:
    scr_w = scr_h = 0
    for node in root.iter("node"):
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            _, _, r, b = pr
            scr_w, scr_h = max(scr_w, r), max(scr_h, b)

    edit_xy: Optional[Tuple[int, int]] = None
    edit_rect: Optional[Tuple[int, int, int, int]] = None
    for node in root.iter("node"):
        rid = (node.get("resource-id") or "")
        if not any(
            tok in rid
            for tok in (
                "DiscussionScreen.FirstMessage.TextField.Input",
                "FirstMessage.TextField.Input",
                "DiscussionFooter.ChatInput.Input",
                "TextField.Input",
            )
        ):
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        l, t, r, b = pr
        edit_xy = ((l + r) // 2, (t + b) // 2)
        edit_rect = pr
        break

    if edit_rect is not None and edit_xy is not None:
        el, et, er, eb = edit_rect
        for node in root.iter("node"):
            rid = (node.get("resource-id") or "")
            if "DiscussionFooter.ChatInput.SendButton" not in rid and "ChatInput.SendButton" not in rid:
                continue
            if node.get("clickable", "false") != "true":
                continue
            pr = parse_bounds(node.get("bounds") or "")
            if not pr:
                continue
            l, t, r, b = pr
            if l >= er - 4:
                return edit_xy, ((l + r) // 2, (t + b) // 2)

    candidates = []
    for node in root.iter("node"):
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            _, _, r, b = pr
            scr_w, scr_h = max(scr_w, r), max(scr_h, b)

        pkg = node.get("package") or ""
        if TARGET_PACKAGE and pkg != TARGET_PACKAGE:
            continue
        if "EditText" not in (node.get("class") or ""):
            continue
        if node.get("enabled", "true") == "false":
            continue
        if not pr:
            continue
        l, t, r, b = pr
        area = (r - l) * (b - t)
        if area < 80:
            continue
        candidates.append((b, t, area, ((l+r)//2, (t+b)//2), pr))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[2]))
    _, _, _, edit_xy, edit_rect = candidates[-1]

    # Кнопка отправки справа от поля
    el, et, er, eb = edit_rect
    ey_mid = (et + eb) // 2
    half_h = max((eb - et) // 2 + 24, 40)

    scored = []
    for node in root.iter("node"):
        pkg = node.get("package") or ""
        if TARGET_PACKAGE and pkg != TARGET_PACKAGE:
            continue
        if node.get("clickable", "false") != "true":
            continue
        if node.get("enabled", "true") == "false":
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        l, t, r, b = pr
        cx, cy = (l+r)//2, (t+b)//2
        if abs(cy - ey_mid) > half_h:
            continue
        if l < er - 4:
            continue
        w, h = r-l, b-t
        rid   = (node.get("resource-id") or "").lower()
        cdesc = (node.get("content-desc") or "").lower()
        cls   = (node.get("class") or "").lower()
        score = cx
        for kw in ("send", "plane", "paper", "submit"):
            if kw in rid or kw in cdesc:
                score += 500
        if "отправ" in cdesc:
            score += 500
        if "imagebutton" in cls:
            score += 120
        if "fab" in rid:
            score += 150
        if 28 <= w <= 200 and 28 <= h <= 200:
            score += 60
        scored.append((score, cx, cy))

    if scored:
        scored.sort(key=lambda x: -x[0])
        _, bx, by = scored[0]
        send_xy = (bx, by)
    else:
        tx = (er + scr_w) // 2 if scr_w > er + 20 else min(scr_w - 24, er + 48)
        tx = max(er + 16, min(scr_w - 8, tx))
        ty = max(8, min(scr_h - 8, ey_mid))
        send_xy = (tx, ty)

    return edit_xy, send_xy


def tap(xy: Tuple[int, int]):
    run_adb(["shell", "input", "tap", str(xy[0]), str(xy[1])])
    invalidate_ui_dump_cache(ADB_PORT)


def input_text(text: str) -> bool:
    """
    Ввод через ADB: длинный текст режется на куски (лимит shell / input text).
    Перевод строки — отдельный keyevent Enter (66), без «url»-кодирования пробелов.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    for li, line in enumerate(lines):
        for i in range(0, len(line), ADB_INPUT_TEXT_CHUNK):
            chunk = line[i : i + ADB_INPUT_TEXT_CHUNK]
            if not chunk:
                continue
            r = run_adb(["shell", f"input text {_posix_quote(chunk)}"])
            if not r or r.returncode != 0:
                return False
            time.sleep(0.028)
        if li < len(lines) - 1:
            r2 = run_adb(["shell", "input", "keyevent", "66"])
            if not r2 or r2.returncode != 0:
                return False
            time.sleep(0.04)
    if len(text) > ADB_INPUT_TEXT_CHUNK:
        time.sleep(min(0.55, 0.00035 * len(text)))
    return True


def _input_text_delay_after_len(text_len: int) -> float:
    if text_len <= ADB_INPUT_TEXT_CHUNK:
        return 0.0
    return min(0.65, 0.00035 * text_len)


def _send_one_message_to_mumu(
    message: str,
    *,
    post_tap: float,
    post_text: float,
    post_input_before_send: float,
    serial: Optional[str] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    """Один цикл: фокус → ввод → свежий dump → Send (с повтором)."""
    msg = _collapse_ws(message or "")
    if not msg:
        return False
    log = log_func or _offerup_log
    pbs = max(float(post_input_before_send), 0.04)
    for attempt in range(1, 4):
        root = dump_ui_serial(serial) if serial else dump_ui()
        if root is None:
            time.sleep(0.2)
            continue
        taps = find_edit_and_send(root)
        if not taps:
            log(f"Поле/Send не найдены (попытка {attempt})")
            time.sleep(0.22)
            continue
        edit_xy, send_xy = taps
        if serial:
            run_adb_serial(serial, ["shell", "input", "tap", str(edit_xy[0]), str(edit_xy[1])])
        else:
            tap(edit_xy)
        time.sleep(post_tap)
        typed = input_text_serial(msg, serial) if serial else input_text(msg)
        if not typed:
            log(f"input text failed (попытка {attempt})")
            continue
        extra = _input_text_delay_after_len(len(msg))
        time.sleep(post_text + pbs + extra)
        root_after = dump_ui_serial(serial) if serial else dump_ui()
        sent = False
        if root_after is not None and _offerup_tap_send_button(serial, root_after, log_func=log):
            sent = True
        else:
            taps2 = find_edit_and_send(root_after) if root_after is not None else None
            if taps2:
                _, send_xy2 = taps2
                if serial:
                    run_adb_serial(
                        serial, ["shell", "input", "tap", str(send_xy2[0]), str(send_xy2[1])]
                    )
                else:
                    tap(send_xy2)
                sent = True
            else:
                if serial:
                    run_adb_serial(serial, ["shell", "input", "tap", str(send_xy[0]), str(send_xy[1])])
                else:
                    tap(send_xy)
        time.sleep(0.32)
        root_chk = dump_ui_serial(serial) if serial else dump_ui()
        if _chat_contains_sent_text(msg, root_chk):
            return True
        if sent:
            if serial:
                run_adb_serial(serial, ["shell", "input", "keyevent", "66"])
            else:
                run_adb(["shell", "input", "keyevent", "66"])
            time.sleep(0.3)
            root_chk = dump_ui_serial(serial) if serial else dump_ui()
            if _chat_contains_sent_text(msg, root_chk):
                return True
        log(f"Send не подтверждён (попытка {attempt}, {len(msg)} симв.)")
    return False


def _is_live_session_port(item: dict) -> bool:
    """Только реальные сессии: adb devices или ответивший на shell probe."""
    if item.get("source") == "adb devices":
        return True
    if item.get("reachable") is True:
        return True
    return False


def send_messages_to_mumu(
    messages: List[str],
    *,
    post_tap: Optional[float] = None,
    post_text: Optional[float] = None,
    post_input_before_send: Optional[float] = None,
    between_messages: Optional[float] = None,
) -> Tuple[Optional[str], int]:
    """
    Сообщения подряд в один чат. Перед каждым — свежий UI dump (иначе второе
    сообщение часто не попадает в поле после анимации отправки).
    Возвращает (код_ошибки_или_None, индекс_сообщения_при_сбое).
    """
    pt = post_tap if post_tap is not None else POST_TAP_DELAY_SEC
    ptx = post_text if post_text is not None else POST_TEXT_DELAY_SEC
    pbs = post_input_before_send if post_input_before_send is not None else POST_INPUT_BEFORE_SEND
    btw = between_messages if between_messages is not None else BETWEEN_MESSAGES_SEC
    if not messages:
        return None, 0
    for idx, message in enumerate(messages):
        if not _send_one_message_to_mumu(
            message,
            post_tap=pt,
            post_text=ptx,
            post_input_before_send=pbs,
        ):
            if not _collapse_ws(message or ""):
                continue
            return "input_text", idx
        if idx < len(messages) - 1:
            time.sleep(btw)
    return None, 0


# ── OfferUp (Chrome → OPEN → Ask → шаблон) ───────────────────

_offerup_lock = threading.Lock()
_offerup_job: dict = {"state": "idle", "log": [], "error": ""}

_inbox_manual_lock = threading.Lock()
_inbox_manual_state: Dict[str, Any] = {
    "running": False,
    "loop_mode": False,
    "stop_requested": False,
    "log": [],
    "error": "",
}

_inbox_abort_lock = threading.Lock()
_inbox_abort_flag = False

_local_llm_trace_lock = threading.Lock()
_local_llm_traces: List[dict] = []
_LOCAL_LLM_TRACE_CAP = 120

_link_queue_lock = threading.RLock()
_autorun_fetch_lock = threading.Lock()
_autorun_seen_ad_keys: set[str] = set()

_link_queue: List[dict] = []
_link_queue_next_id = 1

# ── История отправок ────────────────────────────────────────
_send_history_lock = threading.RLock()
_send_history: List[dict] = []
_send_history_next_id = 1

_created_links_lock = threading.RLock()
_created_links: List[dict] = []
_created_links_next_id = 1

_offerup_alerts_lock = threading.RLock()
_offerup_alerts: List[dict] = []
_offerup_alerts_next_id = 1

_coach_feed_lock = threading.RLock()
_coach_feed: List[dict] = []
_coach_feed_next_id = 1

_coach_chat_inject_lock = threading.RLock()
_coach_chat_inject_pending: List[dict] = []
_coach_chat_inject_next_id = 1

# После ask_operator: seller → последнее сообщение продавца (для обучения из ответа коуча).
_inbox_operator_lesson_lock = threading.RLock()
_inbox_operator_pending_lessons: Dict[str, str] = {}

_pending_ai_actions_lock = threading.RLock()
_pending_ai_actions: List[dict] = []
_pending_ai_actions_next_id = 1
_PENDING_AI_ACTIONS_CAP = 24

_link_api_rate_lock = threading.Lock()
_link_api_last_call = 0.0

APP_VERSION = "2.2.0"
APP_VERSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.txt")

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mumu_state.json")
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
MUMU_SETTINGS_PATH = os.path.join(_APP_DIR, "mumu_settings.json")
MUMU_UI_CONFIG_PATH = MUMU_SETTINGS_PATH  # единый файл настроек (старое имя — алиас)
MUMU_AI_TRAINING_PATH = os.path.join(_APP_DIR, "mumu_ai_training.json")

GITHUB_TRAINING_SYNC_ENABLED = True
GITHUB_TRAINING_REPO = "artemkasmolinstandoff-ship-it/offerup_update"
GITHUB_TRAINING_BRANCH = "main"
GITHUB_TRAINING_PATH = "mumu_ai_training.json"
GITHUB_TRAINING_TOKEN = ""
GITHUB_TRAINING_SYNC_INTERVAL_SEC = 600
GITHUB_TRAINING_LAST_SYNC_AT = ""
GITHUB_TRAINING_LAST_SYNC_SHA = ""
GITHUB_TRAINING_LAST_SYNC_ERROR = ""
GITHUB_TRAINING_LAST_SYNC_ADDED = 0
GITHUB_TRAINING_LAST_PUSH_AT = ""
GITHUB_TRAINING_LAST_PUSH_ERROR = ""
GITHUB_APP_LAST_PUBLISH_AT = ""
GITHUB_APP_LAST_PUBLISH_ERROR = ""

GITHUB_TRAINING_CLONE_DIR = os.path.join(_APP_DIR, ".github_training_repo")
GITHUB_APP_CLONE_DIR = os.path.join(_APP_DIR, ".mumupaster_app_git")
GITHUB_APP_VERSION_FILE = "version.txt"
GITHUB_APP_UPDATE_FILES: Tuple[str, ...] = (
    "app.py",
    "index.html",
    "adb_optimizer.py",
    "mumu_onboarding.py",
    "mumu_rid_optimizations.py",
    "version.txt",
)
_github_training_sync_lock = threading.RLock()
_github_training_push_lock = threading.Lock()
_github_push_timer: Optional[threading.Timer] = None
_github_training_bg_started = False

_DESKTOP_APP_PACKAGES: Tuple[Tuple[str, str], ...] = (
    ("com.offerup", "OfferUp"),
    ("com.scheler.superproxy", "Super Proxy"),
)
_LEGACY_UI_CONFIG_PATH = os.path.join(_APP_DIR, "mumu_ui_config.json")
_SETTINGS_RUNTIME_ONLY = frozenset(
    {
        "config_path",
        "local_llm_providers",
        "local_llm_text_ready",
        "local_llm_vision_ready",
        "local_llm_recommended_text",
        "local_llm_recommended_vision",
        "local_llm_api_key_set",
        "local_llm_vision_api_key_set",
        "local_llm_vision_same_as_text",
        "offerup_rid_map",
        "link_api_key_set",
        "ephedrine_api_token_set",
    }
)
_UI_SAVED_ADB: Dict[str, Any] = {}
_UI_SAVED_OFFERUP_TIMING: Dict[str, Any] = {}
_UI_SAVED_OFFERUP_INBOX: Dict[str, Any] = {}
_UI_SAVED_BUYER: Dict[str, Any] = {}
_UI_SAVED_PARSER_FILTERS: Dict[str, str] = {}


def _ui_config_offerup_timing_snapshot() -> dict:
    if _UI_SAVED_OFFERUP_TIMING:
        return dict(_UI_SAVED_OFFERUP_TIMING)
    return offerup_merge_action_timing({}, apply_jitter=False)


def _ui_config_offerup_inbox_snapshot() -> dict:
    if _UI_SAVED_OFFERUP_INBOX:
        return dict(_UI_SAVED_OFFERUP_INBOX)
    return offerup_sanitize_inbox_settings({})


def _apply_offerup_timing_globals(timing: dict) -> None:
    """Применить сохранённые тайминги OfferUp к глобальным константам."""
    global OFFERUP_POLL_UI_SEC, OFFERUP_OPEN_WAIT_SEC, OFFERUP_ASK_WAIT_SEC
    global OFFERUP_TEMPLATE_WAIT_SEC, OFFERUP_WAIT_AFTER_TEMPLATE_SEC
    global OFFERUP_LIMIT_WAIT_SEC, OFFERUP_CONVERSATION_COOLDOWN_SEC
    merged = offerup_merge_action_timing(timing, apply_jitter=False)
    OFFERUP_POLL_UI_SEC = float(merged.get("poll_ui", OFFERUP_POLL_UI_SEC))
    OFFERUP_OPEN_WAIT_SEC = float(merged.get("open_wait", OFFERUP_OPEN_WAIT_SEC))
    OFFERUP_ASK_WAIT_SEC = float(merged.get("ask_wait", OFFERUP_ASK_WAIT_SEC))
    OFFERUP_TEMPLATE_WAIT_SEC = float(merged.get("template_wait", OFFERUP_TEMPLATE_WAIT_SEC))
    OFFERUP_WAIT_AFTER_TEMPLATE_SEC = float(merged.get("after_template", OFFERUP_WAIT_AFTER_TEMPLATE_SEC))
    lim = merged.get("conversation_cooldown", merged.get("limit_wait", OFFERUP_CONVERSATION_COOLDOWN_SEC))
    OFFERUP_CONVERSATION_COOLDOWN_SEC = float(lim)
    OFFERUP_LIMIT_WAIT_SEC = float(lim)


def _apply_offerup_inbox_globals(inbox: dict) -> None:
    global OFFERUP_INBOX_BATCH_EVERY, OFFERUP_INBOX_MAX_REPLIES, OFFERUP_INBOX_MAX_SCROLLS
    global OFFERUP_INBOX_OPEN_WAIT_SEC, OFFERUP_INBOX_SCAN_TIMEOUT_SEC
    cfg = offerup_sanitize_inbox_settings(inbox)
    OFFERUP_INBOX_BATCH_EVERY = int(cfg["batch_every"])
    OFFERUP_INBOX_MAX_REPLIES = int(cfg["max_replies"])
    OFFERUP_INBOX_MAX_SCROLLS = int(cfg["max_scrolls"])
    OFFERUP_INBOX_OPEN_WAIT_SEC = float(cfg["open_wait"])
    OFFERUP_INBOX_SCAN_TIMEOUT_SEC = float(cfg["scan_timeout"])
_ui_config_lock = threading.Lock()
_email_accounts_lock = threading.Lock()
_email_accounts: Dict[str, dict] = {}
_mail_history_lock = threading.Lock()
_mail_history: List[dict] = []
_mail_history_next_id = 1
_inbox_blocked_sellers_lock = threading.RLock()
_inbox_blocked_sellers: set[str] = set()


def _is_bad_captured_offerup_name(name: object) -> bool:
    low = _collapse_ws(str(name or "")).lower()
    if not low:
        return False
    bad_exact = {
        "back",
        "navigate up",
        "navigate to profile",
        "more options",
        "options",
        "message",
        "messages",
        "notifications",
        "offerup",
        "offer up",
        "super proxy",
    }
    if low in bad_exact or low.startswith("navigate "):
        return True
    bad_fragments = (
        "mumu",
        "super proxy",
        "proxy",
        "магазин",
        "искать игры",
        "искать ",
        "приложен",
        "папка",
        "гаджет",
        "gadget",
        "folder",
        "android",
        "эмулятор",
        "emulator",
        "launcher",
        "play store",
        "google play",
    )
    if any(x in low for x in bad_fragments):
        return True
    if re.search(r"объектов:\s*\d+", low):
        return True
    return False


def _is_generic_link_seller_name(name: object) -> bool:
    low = _collapse_ws(str(name or "")).lower()
    if not low:
        return True
    if low in ("offerup", "offer up", "seller", "user", "unknown", "—", "-"):
        return True
    return _is_bad_captured_offerup_name(name)


def _lookup_created_link(created_id: object) -> Optional[dict]:
    try:
        cid = int(created_id or 0)
    except (TypeError, ValueError):
        return None
    if not cid:
        return None
    with _created_links_lock:
        for row in _created_links:
            if int(row.get("id") or 0) == cid:
                return dict(row)
    return None


def _effective_link_seller_name(row: dict) -> str:
    n = _collapse_ws(str(row.get("name") or ""))
    if n and not _is_generic_link_seller_name(n):
        return n
    cl = _lookup_created_link(row.get("created_link_id"))
    if cl:
        cn = _collapse_ws(str(cl.get("name") or ""))
        if cn and not _is_generic_link_seller_name(cn):
            return cn
    return n


def _pending_inbox_seller_names(limit: int = 24) -> List[str]:
    """Имена продавцов из неотправленных ссылок (новые в очереди — первыми)."""
    sent_ids = _pending_created_sent_ids()
    names: List[str] = []
    seen: set[str] = set()
    with _link_queue_lock:
        queue = list(reversed(_link_queue))
    for row in queue:
        cid = int(row.get("created_link_id") or 0)
        if cid and cid in sent_ids:
            continue
        nm = _effective_link_seller_name(row)
        if _is_generic_link_seller_name(nm) or _is_seller_inbox_blocked(nm):
            continue
        key = _normalize_inbox_match_name(nm)
        if key and key not in seen:
            seen.add(key)
            names.append(nm)
        if len(names) >= limit:
            return names
    with _created_links_lock:
        for row in reversed(_created_links):
            if int(row.get("id") or 0) in sent_ids:
                continue
            nm = _collapse_ws(str(row.get("name") or ""))
            if _is_generic_link_seller_name(nm) or _is_seller_inbox_blocked(nm):
                continue
            key = _normalize_inbox_match_name(nm)
            if key and key not in seen:
                seen.add(key)
                names.append(nm)
            if len(names) >= limit:
                break
    return names


def _sync_link_queue_names_from_created() -> None:
    with _link_queue_lock:
        for row in _link_queue:
            if not _is_generic_link_seller_name(row.get("name")):
                continue
            cl = _lookup_created_link(row.get("created_link_id"))
            if not cl:
                continue
            cn = _collapse_ws(str(cl.get("name") or ""))
            if cn and not _is_generic_link_seller_name(cn):
                row["name"] = cn


def _max_next_id(items: List[dict]) -> int:
    vals = []
    for x in items:
        try:
            vals.append(int(x.get("id") or 0))
        except (TypeError, ValueError):
            pass
    return (max(vals) + 1) if vals else 1


def save_persistent_state() -> None:
    global _PERSISTENT_STATE_LAST_TEXT
    data = {}
    with _link_queue_lock:
        data["link_queue"] = list(_link_queue)
        data["link_queue_next_id"] = int(_link_queue_next_id)
    with _send_history_lock:
        data["send_history"] = list(_send_history)
        data["send_history_next_id"] = int(_send_history_next_id)
    with _created_links_lock:
        data["created_links"] = list(_created_links)
        data["created_links_next_id"] = int(_created_links_next_id)
    with _offerup_alerts_lock:
        data["offerup_alerts"] = list(_offerup_alerts)
        data["offerup_alerts_next_id"] = int(_offerup_alerts_next_id)
    with _email_accounts_lock:
        data["email_accounts"] = dict(_email_accounts)
    with _mail_history_lock:
        data["mail_history"] = list(_mail_history)
        data["mail_history_next_id"] = int(_mail_history_next_id)
    with _inbox_blocked_sellers_lock:
        data["inbox_blocked_sellers"] = sorted(_inbox_blocked_sellers)
    try:
        raw = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return
    if raw == _PERSISTENT_STATE_LAST_TEXT and os.path.exists(STATE_PATH):
        return
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, STATE_PATH)
        _PERSISTENT_STATE_LAST_TEXT = raw
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def load_persistent_state() -> None:
    global _link_queue_next_id, _send_history_next_id, _created_links_next_id, _offerup_alerts_next_id
    global _mail_history_next_id
    if not os.path.isfile(STATE_PATH):
        return
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    with _link_queue_lock:
        _link_queue[:] = [x for x in (data.get("link_queue") or []) if isinstance(x, dict)]
        _link_queue_next_id = max(int(data.get("link_queue_next_id") or 1), _max_next_id(_link_queue))
    with _send_history_lock:
        _send_history[:] = [x for x in (data.get("send_history") or []) if isinstance(x, dict)]
        _send_history_next_id = max(int(data.get("send_history_next_id") or 1), _max_next_id(_send_history))
    with _created_links_lock:
        _created_links[:] = [x for x in (data.get("created_links") or []) if isinstance(x, dict)]
        _created_links_next_id = max(int(data.get("created_links_next_id") or 1), _max_next_id(_created_links))
    with _offerup_alerts_lock:
        _offerup_alerts[:] = [x for x in (data.get("offerup_alerts") or []) if isinstance(x, dict)]
        _offerup_alerts_next_id = max(int(data.get("offerup_alerts_next_id") or 1), _max_next_id(_offerup_alerts))
    with _email_accounts_lock:
        raw_accts = data.get("email_accounts") or {}
        _email_accounts.clear()
        if isinstance(raw_accts, dict):
            for k, v in raw_accts.items():
                if isinstance(k, str) and isinstance(v, dict):
                    _email_accounts[k.strip()] = dict(v)
    with _mail_history_lock:
        _mail_history[:] = [x for x in (data.get("mail_history") or []) if isinstance(x, dict)]
        _mail_history_next_id = max(int(data.get("mail_history_next_id") or 1), _max_next_id(_mail_history))
    with _inbox_blocked_sellers_lock:
        _inbox_blocked_sellers.clear()
        for nm in data.get("inbox_blocked_sellers") or []:
            key = _collapse_ws(str(nm)).lower()
            key = re.sub(r"\.{2,}$", "", key.replace("…", "...")).strip()
            if key:
                _inbox_blocked_sellers.add(key)
    # Older builds could capture UI chrome (Back/Message) as seller name — repair from created_links.
    with _created_links_lock:
        for row in _created_links:
            if _is_bad_captured_offerup_name(row.get("name")):
                row["name"] = row.get("title") or ""
    _sync_link_queue_names_from_created()
    with _created_links_lock:
        _created_name_by_id = {
            int(r.get("id") or 0): _collapse_ws(str(r.get("name") or ""))
            for r in _created_links
            if int(r.get("id") or 0)
        }
    with _link_queue_lock:
        for row in _link_queue:
            if _is_bad_captured_offerup_name(row.get("name")) or _is_generic_link_seller_name(row.get("name")):
                cid = int(row.get("created_link_id") or 0)
                fixed = _created_name_by_id.get(cid, "")
                row["name"] = fixed if fixed and not _is_generic_link_seller_name(fixed) else ""
    with _send_history_lock:
        for row in _send_history:
            if _is_bad_captured_offerup_name(row.get("seller_name")):
                row["seller_name"] = ""


def _mumu_ui_config_snapshot() -> dict:
    return {
        "local_llm_enabled": bool(LOCAL_LLM_ENABLED),
        "local_llm_chat_url": LOCAL_LLM_CHAT_URL,
        "local_llm_model": LOCAL_LLM_MODEL,
        "local_llm_vision_model": LOCAL_LLM_VISION_MODEL,
        "local_llm_screen_assist": bool(LOCAL_LLM_SCREEN_ASSIST),
        "local_llm_screen_assist_auto": bool(LOCAL_LLM_SCREEN_ASSIST_AUTO),
        "coach_pending_auto_approve": bool(COACH_PENDING_AUTO_APPROVE),
        "operator_pause_on_fail": bool(OPERATOR_PAUSE_ON_FAIL),
        "operator_pause_llm_dump": bool(OPERATOR_PAUSE_LLM_DUMP),
        "anymessage_token": ANYMESSAGE_TOKEN or "",
        "anymessage_domain": ANYMESSAGE_DOMAIN,
        "anymessage_site": ANYMESSAGE_SITE,
        "local_llm_use_listing_image_inbox": bool(LOCAL_LLM_USE_LISTING_IMAGE_INBOX),
        "local_llm_api_style": LOCAL_LLM_API_STYLE,
        "local_llm_provider": LOCAL_LLM_PROVIDER,
        "local_llm_api_key_set": bool(_llm_api_key()),
        "local_llm_providers": _llm_providers_for_api(),
        "local_llm_timeout_sec": float(LOCAL_LLM_TIMEOUT_SEC),
        "local_llm_system_prompt": LOCAL_LLM_INBOX_SYSTEM_PROMPT,
        "coach_system_prompt": _effective_coach_system_prompt(),
        "coach_training_rules": list(COACH_TRAINING_RULES),
        "local_llm_extra_training_rules": list(LOCAL_LLM_EXTRA_TRAINING_RULES),
        "offerup_rid_map": _offerup_rid_map_for_api(),
        "local_llm_text_ready": bool(_local_llm_runtime_ready()),
        "local_llm_vision_ready": bool(_local_llm_vision_ready()),
        "local_llm_recommended_text": ["llama3.2", "qwen2.5:7b", "mistral", "gemma2:9b"],
        "local_llm_recommended_vision": [
            "gemini-2.0-flash",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "gpt-4o-mini",
            "pixtral-12b-2409",
            "moondream",
            "llava",
        ],
        "local_llm_vision_timeout_sec": float(LOCAL_LLM_VISION_TIMEOUT_SEC),
        "local_llm_vision_chat_url": LOCAL_LLM_VISION_CHAT_URL or "",
        "local_llm_vision_api_style": LOCAL_LLM_VISION_API_STYLE or "",
        "local_llm_vision_provider": LOCAL_LLM_VISION_PROVIDER or "",
        "local_llm_vision_api_key_set": bool((LOCAL_LLM_VISION_API_KEY or "").strip()),
        "local_llm_vision_same_as_text": not (LOCAL_LLM_VISION_CHAT_URL or "").strip(),
        "default_adb_port": str(_UI_SAVED_ADB.get("default_adb_port") or ADB_PORT or "").strip(),
        "ports_show_all": bool(_UI_SAVED_ADB.get("ports_show_all")),
        "timing_profile": str(_UI_SAVED_ADB.get("timing_profile") or "normal"),
        "adb_timing": dict(
            _UI_SAVED_ADB.get("adb_timing")
            or {
                "post_tap": POST_TAP_DELAY_SEC,
                "post_text": POST_TEXT_DELAY_SEC,
                "post_input_before_send": POST_INPUT_BEFORE_SEND,
                "between_messages": BETWEEN_MESSAGES_SEC,
            }
        ),
        "server_shorten": bool(_UI_SAVED_ADB.get("server_shorten", True)),
        "offerup_timing": _ui_config_offerup_timing_snapshot(),
        "offerup_inbox": _ui_config_offerup_inbox_snapshot(),
        "offerup_try_app_url_first": bool(OFFERUP_TRY_APP_URL_FIRST),
        "mumu_manager_exe": MUMU_MANAGER_EXE or "",
        "link_api_provider": LINK_API_PROVIDER,
        "link_api_url": LINK_API_URL,
        "link_api_key_set": bool((LINK_API_KEY or "").strip()),
        "link_api_user_id": LINK_API_USER_ID,
        "link_api_service": LINK_API_SERVICE,
        "ephedrine_api_base": EPHEDRINE_API_BASE,
        "ephedrine_api_token_set": bool((EPHEDRINE_API_TOKEN or "").strip()),
        "ephedrine_service_code": EPHEDRINE_SERVICE_CODE,
        "ephedrine_version": EPHEDRINE_VERSION,
        "ephedrine_domain_id": EPHEDRINE_DOMAIN_ID,
        "ephedrine_profile_id": EPHEDRINE_PROFILE_ID,
        "buyer_name": str(_UI_SAVED_BUYER.get("buyer_name") or ""),
        "buyer_address": str(_UI_SAVED_BUYER.get("buyer_address") or ""),
        "randomize_name": bool(_UI_SAVED_BUYER.get("randomize_name")),
        "randomize_address": bool(_UI_SAVED_BUYER.get("randomize_address")),
        "github_training_sync_enabled": bool(GITHUB_TRAINING_SYNC_ENABLED),
        "github_training_repo": (GITHUB_TRAINING_REPO or "").strip(),
        "github_training_branch": (GITHUB_TRAINING_BRANCH or "main").strip(),
        "github_training_path": (GITHUB_TRAINING_PATH or "mumu_ai_training.json").strip(),
        "github_training_token_set": bool((GITHUB_TRAINING_TOKEN or "").strip()),
        "github_training_sync_interval_sec": int(GITHUB_TRAINING_SYNC_INTERVAL_SEC),
        "github_training_last_sync_at": GITHUB_TRAINING_LAST_SYNC_AT or "",
        "github_training_last_sync_sha": (GITHUB_TRAINING_LAST_SYNC_SHA or "")[:12],
        "github_training_last_sync_error": GITHUB_TRAINING_LAST_SYNC_ERROR or "",
        "github_training_last_sync_added": int(GITHUB_TRAINING_LAST_SYNC_ADDED or 0),
        "github_training_last_push_at": GITHUB_TRAINING_LAST_PUSH_AT or "",
        "github_training_last_push_error": GITHUB_TRAINING_LAST_PUSH_ERROR or "",
        "github_app_last_publish_at": GITHUB_APP_LAST_PUBLISH_AT or "",
        "github_app_last_publish_error": GITHUB_APP_LAST_PUBLISH_ERROR or "",
        "github_git_available": bool(shutil.which("git")),
        "app_version": read_app_version(),
        "ai_training_file": os.path.basename(MUMU_AI_TRAINING_PATH),
        "ai_training_rules_count": len(_load_ai_training_file_raw().get("rules") or []),
        "offerup_parser_filters": dict(_UI_SAVED_PARSER_FILTERS),
        "config_path": os.path.basename(MUMU_SETTINGS_PATH),
        "settings_file": os.path.basename(MUMU_SETTINGS_PATH),
    }


def _apply_link_api_settings_from_body(body: dict) -> None:
    global LINK_API_PROVIDER, LINK_API_URL, LINK_API_KEY, LINK_API_USER_ID, LINK_API_SERVICE
    global EPHEDRINE_API_BASE, EPHEDRINE_API_TOKEN, EPHEDRINE_SERVICE_CODE, EPHEDRINE_VERSION
    global EPHEDRINE_DOMAIN_ID, EPHEDRINE_PROFILE_ID
    if "link_api_provider" in body:
        LINK_API_PROVIDER = normalize_link_api_provider(body.get("link_api_provider"))
    if "link_api_url" in body:
        u = body.get("link_api_url")
        if isinstance(u, str):
            LINK_API_URL = u.strip()[:2000]
    if "link_api_key" in body:
        k = body.get("link_api_key")
        if isinstance(k, str):
            ks = k.strip()[:500]
            if ks or not (LINK_API_KEY or "").strip():
                LINK_API_KEY = ks
    if "link_api_user_id" in body:
        uid = body.get("link_api_user_id")
        if isinstance(uid, str):
            LINK_API_USER_ID = uid.strip()[:80]
    if "link_api_service" in body:
        svc = body.get("link_api_service")
        if isinstance(svc, str) and svc.strip():
            LINK_API_SERVICE = svc.strip()[:120]
    if "ephedrine_api_base" in body:
        eb = body.get("ephedrine_api_base")
        if isinstance(eb, str) and eb.strip():
            EPHEDRINE_API_BASE = eb.strip().rstrip("/")[:500]
    if "ephedrine_api_token" in body:
        et = body.get("ephedrine_api_token")
        if isinstance(et, str):
            ets = et.strip()[:500]
            if ets or not (EPHEDRINE_API_TOKEN or "").strip():
                EPHEDRINE_API_TOKEN = ets
    if "ephedrine_service_code" in body:
        esc = body.get("ephedrine_service_code")
        if isinstance(esc, str) and esc.strip():
            EPHEDRINE_SERVICE_CODE = esc.strip()[:120]
    if "ephedrine_version" in body:
        ev = body.get("ephedrine_version")
        if isinstance(ev, str) and ev.strip():
            EPHEDRINE_VERSION = ev.strip()[:8]
    if "ephedrine_domain_id" in body:
        ed = body.get("ephedrine_domain_id")
        EPHEDRINE_DOMAIN_ID = str(ed).strip()[:32] if ed not in (None, "") else ""
    if "ephedrine_profile_id" in body:
        ep = body.get("ephedrine_profile_id")
        EPHEDRINE_PROFILE_ID = str(ep).strip()[:32] if ep not in (None, "") else ""


def _migrate_legacy_settings_file() -> None:
    if os.path.isfile(MUMU_SETTINGS_PATH):
        return
    if not os.path.isfile(_LEGACY_UI_CONFIG_PATH):
        return
    try:
        with open(_LEGACY_UI_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            save_mumu_ui_config_to_disk(data)
    except Exception:
        pass


def _load_settings_file_raw() -> dict:
    _migrate_legacy_settings_file()
    if not os.path.isfile(MUMU_SETTINGS_PATH):
        return {}
    try:
        with open(MUMU_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _settings_disk_payload() -> dict:
    """Полный снимок для записи на диск (включая API-ключи)."""
    snap = _mumu_ui_config_snapshot()
    out = {k: v for k, v in snap.items() if k not in _SETTINGS_RUNTIME_ONLY}
    out["local_llm_api_key"] = _llm_api_key()
    out["local_llm_vision_api_key"] = (LOCAL_LLM_VISION_API_KEY or "").strip()
    out["link_api_key"] = LINK_API_KEY or ""
    out["ephedrine_api_token"] = EPHEDRINE_API_TOKEN or ""
    return out


def _persist_settings_merge(patch: Optional[dict] = None) -> None:
    """Слить patch в файл; без patch — сохранить текущее состояние целиком."""
    base = _load_settings_file_raw()
    if patch:
        for k, v in patch.items():
            if k in _SETTINGS_RUNTIME_ONLY:
                continue
            base[k] = v
    else:
        base = _settings_disk_payload()
    save_mumu_ui_config_to_disk(base)


def _ingest_ui_config_extras(data: dict) -> None:
    """ADB / OfferUp тайминги / отдельный vision API из ui-config."""
    global LOCAL_LLM_VISION_CHAT_URL, LOCAL_LLM_VISION_API_KEY, LOCAL_LLM_VISION_API_STYLE
    global LOCAL_LLM_VISION_PROVIDER, LOCAL_LLM_VISION_MODEL
    global POST_TAP_DELAY_SEC, POST_TEXT_DELAY_SEC, POST_INPUT_BEFORE_SEND, BETWEEN_MESSAGES_SEC
    global ADB_PORT, _UI_SAVED_ADB, _UI_SAVED_OFFERUP_TIMING, _UI_SAVED_OFFERUP_INBOX
    global _UI_SAVED_BUYER, _UI_SAVED_PARSER_FILTERS
    if "local_llm_vision_chat_url" in data:
        v = data.get("local_llm_vision_chat_url")
        LOCAL_LLM_VISION_CHAT_URL = v.strip()[:2000] if isinstance(v, str) else ""
    if "local_llm_vision_api_key" in data:
        vk = data.get("local_llm_vision_api_key")
        LOCAL_LLM_VISION_API_KEY = vk.strip()[:500] if isinstance(vk, str) else ""
    if "local_llm_vision_api_style" in data:
        vs = data.get("local_llm_vision_api_style")
        if isinstance(vs, str) and vs.strip():
            s = vs.strip().lower()
            if s in ("ollama", "openai", "openai_compatible", "lmstudio"):
                LOCAL_LLM_VISION_API_STYLE = "ollama" if s == "ollama" else "openai"
    if "local_llm_vision_provider" in data:
        vp = data.get("local_llm_vision_provider")
        if isinstance(vp, str) and vp.strip().lower() in LLM_PROVIDER_PRESETS:
            LOCAL_LLM_VISION_PROVIDER = vp.strip().lower()
    if "local_llm_vision_model" in data and data.get("local_llm_vision_model") is not None:
        vm = data.get("local_llm_vision_model")
        LOCAL_LLM_VISION_MODEL = vm.strip()[:200] if isinstance(vm, str) else ""
    adb_blob: Dict[str, Any] = {}
    if "default_adb_port" in data:
        p = str(data.get("default_adb_port") or "").strip()
        adb_blob["default_adb_port"] = p[:80]
        if p:
            ADB_PORT = p[:80]
    if "ports_show_all" in data:
        adb_blob["ports_show_all"] = bool(data["ports_show_all"])
    if "timing_profile" in data:
        adb_blob["timing_profile"] = str(data.get("timing_profile") or "normal")[:32]
    if "server_shorten" in data:
        adb_blob["server_shorten"] = bool(data["server_shorten"])
    if "adb_timing" in data and isinstance(data.get("adb_timing"), dict):
        fb = (POST_TAP_DELAY_SEC, POST_TEXT_DELAY_SEC, POST_INPUT_BEFORE_SEND, BETWEEN_MESSAGES_SEC)
        pt, ptx, pbs, btw = sanitize_adb_timing_overrides(data["adb_timing"], fb)
        POST_TAP_DELAY_SEC, POST_TEXT_DELAY_SEC, POST_INPUT_BEFORE_SEND, BETWEEN_MESSAGES_SEC = pt, ptx, pbs, btw
        adb_blob["adb_timing"] = {
            "post_tap": pt,
            "post_text": ptx,
            "post_input_before_send": pbs,
            "between_messages": btw,
        }
    if adb_blob:
        _UI_SAVED_ADB.update(adb_blob)
    if "offerup_timing" in data and isinstance(data.get("offerup_timing"), dict):
        _UI_SAVED_OFFERUP_TIMING = dict(data["offerup_timing"])
        _apply_offerup_timing_globals(_UI_SAVED_OFFERUP_TIMING)
    if "offerup_inbox" in data and isinstance(data.get("offerup_inbox"), dict):
        _UI_SAVED_OFFERUP_INBOX = dict(data["offerup_inbox"])
        _apply_offerup_inbox_globals(_UI_SAVED_OFFERUP_INBOX)
    if "buyer_name" in data:
        _UI_SAVED_BUYER["buyer_name"] = str(data.get("buyer_name") or "").strip()[:120]
    if "buyer_address" in data:
        _UI_SAVED_BUYER["buyer_address"] = str(data.get("buyer_address") or "").strip()[:300]
    if "randomize_name" in data:
        _UI_SAVED_BUYER["randomize_name"] = bool(data.get("randomize_name"))
    if "randomize_address" in data:
        _UI_SAVED_BUYER["randomize_address"] = bool(data.get("randomize_address"))
    if "offerup_parser_filters" in data and isinstance(data.get("offerup_parser_filters"), dict):
        _UI_SAVED_PARSER_FILTERS = offerup_sanitize_parser_filters(data["offerup_parser_filters"])


def load_mumu_ui_config_from_disk() -> None:
    global LOCAL_LLM_ENABLED, LOCAL_LLM_CHAT_URL, LOCAL_LLM_MODEL, LOCAL_LLM_API_STYLE, LOCAL_LLM_TIMEOUT_SEC
    global LOCAL_LLM_PROVIDER, LOCAL_LLM_API_KEY
    global LOCAL_LLM_INBOX_SYSTEM_PROMPT, OFFERUP_TRY_APP_URL_FIRST
    global LOCAL_LLM_VISION_MODEL, LOCAL_LLM_SCREEN_ASSIST, LOCAL_LLM_SCREEN_ASSIST_AUTO
    global LOCAL_LLM_USE_LISTING_IMAGE_INBOX, COACH_PENDING_AUTO_APPROVE
    global OPERATOR_PAUSE_ON_FAIL, OPERATOR_PAUSE_LLM_DUMP
    global ANYMESSAGE_TOKEN, ANYMESSAGE_DOMAIN, ANYMESSAGE_SITE
    global MUMU_MANAGER_EXE
    global LOCAL_LLM_COACH_SYSTEM_PROMPT, COACH_TRAINING_RULES, LOCAL_LLM_EXTRA_TRAINING_RULES
    global LOCAL_LLM_VISION_CHAT_URL, LOCAL_LLM_VISION_API_KEY, LOCAL_LLM_VISION_API_STYLE, LOCAL_LLM_VISION_PROVIDER
    data = _load_settings_file_raw()
    if not data:
        return
    with _ui_config_lock:
        if "local_llm_enabled" in data:
            LOCAL_LLM_ENABLED = bool(data["local_llm_enabled"])
        u = data.get("local_llm_chat_url")
        if isinstance(u, str) and u.strip():
            LOCAL_LLM_CHAT_URL = u.strip()[:2000]
        m = data.get("local_llm_model")
        if isinstance(m, str) and m.strip():
            LOCAL_LLM_MODEL = m.strip()[:200]
        st = data.get("local_llm_api_style")
        if isinstance(st, str) and st.strip():
            s = st.strip().lower()
            if s in ("ollama", "openai", "openai_compatible", "lmstudio"):
                LOCAL_LLM_API_STYLE = s
        prov = data.get("local_llm_provider")
        if isinstance(prov, str) and prov.strip():
            p = prov.strip().lower()
            if p in LLM_PROVIDER_PRESETS:
                LOCAL_LLM_PROVIDER = p
        elif isinstance(data.get("local_llm_chat_url"), str):
            LOCAL_LLM_PROVIDER = _infer_llm_provider_from_url(data.get("local_llm_chat_url") or "")
        key = data.get("local_llm_api_key")
        if isinstance(key, str):
            LOCAL_LLM_API_KEY = key.strip()[:500]
        if "local_llm_timeout_sec" in data:
            try:
                LOCAL_LLM_TIMEOUT_SEC = max(5.0, min(float(data["local_llm_timeout_sec"]), 600.0))
            except (TypeError, ValueError):
                pass
        sp = data.get("local_llm_system_prompt")
        if isinstance(sp, str) and sp.strip():
            LOCAL_LLM_INBOX_SYSTEM_PROMPT = sp.strip()[:12000]
        if "offerup_try_app_url_first" in data:
            OFFERUP_TRY_APP_URL_FIRST = bool(data["offerup_try_app_url_first"])
        mm = data.get("mumu_manager_exe")
        if isinstance(mm, str) and mm.strip():
            if not (os.environ.get("MUMU_MANAGER_EXE") or "").strip():
                MUMU_MANAGER_EXE = mm.strip()[:500]
        if "local_llm_vision_model" in data:
            vvm = data.get("local_llm_vision_model")
            if isinstance(vvm, str):
                LOCAL_LLM_VISION_MODEL = vvm.strip()[:200]
            else:
                LOCAL_LLM_VISION_MODEL = ""
        if not (LOCAL_LLM_VISION_MODEL or "").strip():
            _preset_vm = (LLM_PROVIDER_PRESETS.get(LOCAL_LLM_PROVIDER) or {}).get("vision_model") or ""
            if str(_preset_vm).strip():
                LOCAL_LLM_VISION_MODEL = str(_preset_vm).strip()[:200]
        if "local_llm_screen_assist" in data:
            LOCAL_LLM_SCREEN_ASSIST = bool(data["local_llm_screen_assist"])
        if "local_llm_screen_assist_auto" in data:
            LOCAL_LLM_SCREEN_ASSIST_AUTO = bool(data["local_llm_screen_assist_auto"])
        if "local_llm_use_listing_image_inbox" in data:
            LOCAL_LLM_USE_LISTING_IMAGE_INBOX = bool(data["local_llm_use_listing_image_inbox"])
        if "coach_pending_auto_approve" in data:
            COACH_PENDING_AUTO_APPROVE = bool(data["coach_pending_auto_approve"])
        if "operator_pause_on_fail" in data:
            OPERATOR_PAUSE_ON_FAIL = bool(data["operator_pause_on_fail"])
        if "operator_pause_llm_dump" in data:
            OPERATOR_PAUSE_LLM_DUMP = bool(data["operator_pause_llm_dump"])
        if "coach_system_prompt" in data:
            csp = data.get("coach_system_prompt")
            if csp is None or (isinstance(csp, str) and not csp.strip()):
                LOCAL_LLM_COACH_SYSTEM_PROMPT = LOCAL_LLM_COACH_SYSTEM_PROMPT_BASE
            elif isinstance(csp, str):
                LOCAL_LLM_COACH_SYSTEM_PROMPT = csp.strip()[:16000]
        if "coach_training_rules" in data:
            parsed_rules = _parse_training_rules_field(data.get("coach_training_rules"))
            if parsed_rules:
                COACH_TRAINING_RULES = parsed_rules
        if "local_llm_extra_training_rules" in data:
            parsed_extra = _parse_training_rules_field(data.get("local_llm_extra_training_rules"))
            if parsed_extra:
                LOCAL_LLM_EXTRA_TRAINING_RULES = parsed_extra
        global GITHUB_TRAINING_SYNC_ENABLED, GITHUB_TRAINING_REPO, GITHUB_TRAINING_BRANCH
        global GITHUB_TRAINING_PATH, GITHUB_TRAINING_TOKEN, GITHUB_TRAINING_SYNC_INTERVAL_SEC
        global GITHUB_TRAINING_LAST_SYNC_AT, GITHUB_TRAINING_LAST_SYNC_SHA, GITHUB_TRAINING_LAST_SYNC_ERROR
        global GITHUB_TRAINING_LAST_SYNC_ADDED, GITHUB_TRAINING_LAST_PUSH_AT, GITHUB_TRAINING_LAST_PUSH_ERROR
        gh_repo = data.get("github_training_repo")
        if isinstance(gh_repo, str):
            GITHUB_TRAINING_REPO = gh_repo.strip()[:300]
        gh_br = data.get("github_training_branch")
        if isinstance(gh_br, str) and gh_br.strip():
            GITHUB_TRAINING_BRANCH = gh_br.strip()[:120]
        gh_path = data.get("github_training_path")
        if isinstance(gh_path, str) and gh_path.strip():
            GITHUB_TRAINING_PATH = gh_path.strip().lstrip("/")[:300]
        gh_tok = data.get("github_training_token")
        if isinstance(gh_tok, str):
            tgs = gh_tok.strip()[:500]
            if tgs or not (GITHUB_TRAINING_TOKEN or "").strip():
                GITHUB_TRAINING_TOKEN = tgs
        if "github_training_sync_enabled" in data:
            GITHUB_TRAINING_SYNC_ENABLED = bool(data["github_training_sync_enabled"])
        if "github_training_sync_interval_sec" in data:
            try:
                GITHUB_TRAINING_SYNC_INTERVAL_SEC = max(60, min(int(data["github_training_sync_interval_sec"]), 86400))
            except (TypeError, ValueError):
                pass
        if isinstance(data.get("github_training_last_sync_at"), str):
            GITHUB_TRAINING_LAST_SYNC_AT = data["github_training_last_sync_at"][:40]
        if isinstance(data.get("github_training_last_sync_sha"), str):
            GITHUB_TRAINING_LAST_SYNC_SHA = data["github_training_last_sync_sha"][:80]
        if isinstance(data.get("github_training_last_sync_error"), str):
            GITHUB_TRAINING_LAST_SYNC_ERROR = data["github_training_last_sync_error"][:500]
        if "github_training_last_sync_added" in data:
            try:
                GITHUB_TRAINING_LAST_SYNC_ADDED = max(0, int(data["github_training_last_sync_added"]))
            except (TypeError, ValueError):
                pass
        tok = data.get("anymessage_token")
        if isinstance(tok, str) and tok.strip():
            if not (os.environ.get("ANYMESSAGE_TOKEN") or "").strip():
                ANYMESSAGE_TOKEN = tok.strip()
        dom = data.get("anymessage_domain")
        if isinstance(dom, str) and dom.strip():
            ANYMESSAGE_DOMAIN = dom.strip()
        site = data.get("anymessage_site")
        if isinstance(site, str) and site.strip():
            ANYMESSAGE_SITE = site.strip()
        _apply_link_api_settings_from_body(data)
        _ingest_ui_config_extras(data)


def _offerup_serial_key(serial: Optional[str] = None) -> str:
    return (serial or ADB_PORT or "").strip()


def get_offerup_email_account(serial: Optional[str] = None) -> Optional[dict]:
    key = _offerup_serial_key(serial)
    if not key:
        return None
    with _email_accounts_lock:
        row = _email_accounts.get(key)
        return dict(row) if isinstance(row, dict) else None


def _mail_history_find_locked(activation_id: str, email: str) -> Optional[dict]:
    aid = str(activation_id or "").strip()
    em = str(email or "").strip().lower()
    for row in reversed(_mail_history):
        if aid and str(row.get("activation_id") or "").strip() == aid:
            return row
        if em and str(row.get("email") or "").strip().lower() == em:
            return row
    return None


def _extract_mail_cost(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("cost", "price", "amount"):
        v = data.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    val = data.get("value")
    if isinstance(val, dict):
        return _extract_mail_cost(val)
    return ""


def _upsert_mail_history(
    *,
    activation_id: str,
    email: str = "",
    site: str = "",
    serial: str = "",
    cost: str = "",
    status: str = "",
    code: str = "",
    link: str = "",
    last_json: Optional[dict] = None,
    error: str = "",
) -> dict:
    """Карточка почты для вкладки «Почты» (как список ссылок)."""
    global _mail_history_next_id
    aid = str(activation_id or "").strip()
    em = str(email or "").strip()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _mail_history_lock:
        row = _mail_history_find_locked(aid, em)
        if row is None:
            rid = int(_mail_history_next_id)
            _mail_history_next_id += 1
            row = {
                "id": rid,
                "activation_id": aid,
                "email": em,
                "site": (site or ANYMESSAGE_SITE).strip(),
                "serial": (serial or "").strip(),
                "cost": (cost or "").strip(),
                "status": (status or "ordered").strip(),
                "code": (code or "").strip(),
                "link": (link or "").strip(),
                "error": (error or "").strip(),
                "last_json": dict(last_json) if isinstance(last_json, dict) else {},
                "created_at": now,
                "updated_at": now,
            }
            _mail_history.append(row)
        else:
            if aid:
                row["activation_id"] = aid
            if em:
                row["email"] = em
            if site:
                row["site"] = site.strip()
            if serial:
                row["serial"] = serial.strip()
            if cost:
                row["cost"] = cost.strip()
            if status:
                row["status"] = status.strip()
            if code:
                row["code"] = code.strip()
            if link:
                row["link"] = link.strip()
            if error:
                row["error"] = error.strip()
            if isinstance(last_json, dict):
                row["last_json"] = dict(last_json)
            row["updated_at"] = now
        return dict(row)


def _mailbox_items_for_ui() -> List[dict]:
    with _mail_history_lock:
        items = sorted(_mail_history, key=lambda x: str(x.get("updated_at") or ""), reverse=True)
        out = [dict(x) for x in items]
    seen_aids = {str(x.get("activation_id") or "").strip() for x in out if x.get("activation_id")}
    with _email_accounts_lock:
        for serial, acct in _email_accounts.items():
            if not isinstance(acct, dict):
                continue
            aid = str(acct.get("activation_id") or "").strip()
            if not aid or aid in seen_aids:
                continue
            seen_aids.add(aid)
            out.append(
                {
                    "id": 0,
                    "activation_id": aid,
                    "email": str(acct.get("email") or "").strip(),
                    "site": ANYMESSAGE_SITE,
                    "serial": str(serial or "").strip(),
                    "cost": "",
                    "status": "saved",
                    "code": "",
                    "link": str(acct.get("verify_url") or "").strip(),
                    "error": "",
                    "last_json": {},
                    "created_at": "",
                    "updated_at": "",
                }
            )
    return out


def set_offerup_email_account(serial: Optional[str], data: dict) -> None:
    key = _offerup_serial_key(serial)
    if not key or not isinstance(data, dict):
        return
    with _email_accounts_lock:
        row = dict(_email_accounts.get(key) or {})
        for fld in ("email", "activation_id", "token", "verify_url"):
            if fld in data and data[fld] is not None:
                row[fld] = data[fld]
        if "template_sends" in data:
            try:
                row["template_sends"] = int(data["template_sends"])
            except (TypeError, ValueError):
                pass
        _email_accounts[key] = row
    save_persistent_state()


def bump_offerup_template_sends(serial: Optional[str] = None) -> int:
    key = _offerup_serial_key(serial)
    if not key:
        return 0
    with _email_accounts_lock:
        row = dict(_email_accounts.get(key) or {})
        n = int(row.get("template_sends") or 0) + 1
        row["template_sends"] = n
        _email_accounts[key] = row
    save_persistent_state()
    return n


def _offerup_maybe_email_verification_after_template(
    serial: Optional[str],
    log_func: Callable[[str], None],
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    if should_stop and should_stop():
        return
    port = _offerup_serial_key(serial)
    if not port:
        return
    acct = get_offerup_email_account(port)
    if not acct or not str(acct.get("activation_id") or "").strip():
        return
    import mumu_onboarding as ob

    token = str(acct.get("token") or ANYMESSAGE_TOKEN or "").strip()
    if not token:
        log_func("AnyMessage: нет токена для верификации после шаблона")
        return
    sends = int(acct.get("template_sends") or 0)
    prefer_code = sends >= 1
    log_func(
        f"OfferUp: проверка почты (отправок шаблона: {sends}, site={ANYMESSAGE_SITE})…"
    )
    try:
        result = ob.handle_offerup_email_verification(
            port,
            token,
            str(acct.get("activation_id") or ""),
            str(acct.get("email") or ""),
            prefer_code=prefer_code,
            log=log_func,
        )
    except Exception as e:
        log_func(f"Верификация почты: {e}")
        return
    if not result:
        return
    upd = {
        "activation_id": result.get("activation_id"),
        "email": result.get("email"),
    }
    if result.get("type") == "code":
        log_func(f"Верификация CODE: {result.get('code')}")
    elif result.get("type") == "link":
        log_func("Верификация: открыта ссылка из письма")
    set_offerup_email_account(port, upd)


def save_mumu_ui_config_to_disk(data: dict) -> None:
    global _UI_CONFIG_LAST_TEXT
    try:
        raw = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return
    if raw == _UI_CONFIG_LAST_TEXT and os.path.exists(MUMU_SETTINGS_PATH):
        return
    tmp = MUMU_SETTINGS_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, MUMU_SETTINGS_PATH)
        _UI_CONFIG_LAST_TEXT = raw
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def apply_mumu_ui_config_from_body(body: object, *, persist: bool = True) -> Optional[str]:
    global LOCAL_LLM_ENABLED, LOCAL_LLM_CHAT_URL, LOCAL_LLM_MODEL, LOCAL_LLM_API_STYLE, LOCAL_LLM_TIMEOUT_SEC
    global LOCAL_LLM_PROVIDER, LOCAL_LLM_API_KEY
    global LOCAL_LLM_INBOX_SYSTEM_PROMPT, OFFERUP_TRY_APP_URL_FIRST
    global LOCAL_LLM_VISION_MODEL, LOCAL_LLM_SCREEN_ASSIST, LOCAL_LLM_SCREEN_ASSIST_AUTO, LOCAL_LLM_USE_LISTING_IMAGE_INBOX
    global LOCAL_LLM_VISION_CHAT_URL, LOCAL_LLM_VISION_API_KEY, LOCAL_LLM_VISION_API_STYLE, LOCAL_LLM_VISION_PROVIDER
    global COACH_PENDING_AUTO_APPROVE, OPERATOR_PAUSE_ON_FAIL, OPERATOR_PAUSE_LLM_DUMP
    global ANYMESSAGE_TOKEN, ANYMESSAGE_DOMAIN, ANYMESSAGE_SITE
    global LOCAL_LLM_COACH_SYSTEM_PROMPT, COACH_TRAINING_RULES, LOCAL_LLM_EXTRA_TRAINING_RULES
    if not isinstance(body, dict):
        return "Нужен JSON-объект"
    with _ui_config_lock:
        if "local_llm_enabled" in body:
            LOCAL_LLM_ENABLED = bool(body["local_llm_enabled"])
        u = body.get("local_llm_chat_url")
        if u is not None:
            if not isinstance(u, str) or not u.strip():
                return "local_llm_chat_url не может быть пустым"
            LOCAL_LLM_CHAT_URL = u.strip()[:2000]
        m = body.get("local_llm_model")
        if m is not None:
            if not isinstance(m, str) or not m.strip():
                return "local_llm_model не может быть пустым"
            LOCAL_LLM_MODEL = m.strip()[:200]
        st = body.get("local_llm_api_style")
        if st is not None:
            s = str(st).strip().lower()
            if s not in ("ollama", "openai", "openai_compatible", "lmstudio"):
                return "local_llm_api_style: ollama или openai"
            LOCAL_LLM_API_STYLE = s
        if "local_llm_provider" in body:
            p = str(body.get("local_llm_provider") or "").strip().lower()
            if p and p not in LLM_PROVIDER_PRESETS:
                return (
                    "local_llm_provider: ollama, groq, gemini, openai, mistral, "
                    "deepseek, openrouter, lmstudio, custom"
                )
            if p:
                LOCAL_LLM_PROVIDER = p
        if body.get("local_llm_apply_provider_preset") and LOCAL_LLM_PROVIDER in LLM_PROVIDER_PRESETS:
            _sync_llm_provider_preset(
                LOCAL_LLM_PROVIDER,
                fill_vision_if_empty=bool(body.get("local_llm_fill_vision", True)),
                target="text",
            )
        if body.get("local_llm_apply_vision_preset") and LOCAL_LLM_VISION_PROVIDER in LLM_PROVIDER_PRESETS:
            _sync_llm_provider_preset(
                LOCAL_LLM_VISION_PROVIDER,
                fill_vision_if_empty=True,
                target="vision",
            )
        if "local_llm_api_key" in body:
            k = body.get("local_llm_api_key")
            if k is None:
                LOCAL_LLM_API_KEY = ""
            elif isinstance(k, str):
                ks = k.strip()[:500]
                if ks or not _llm_api_key():
                    LOCAL_LLM_API_KEY = ks
            else:
                return "local_llm_api_key — строка"
        if "local_llm_timeout_sec" in body:
            try:
                LOCAL_LLM_TIMEOUT_SEC = max(5.0, min(float(body["local_llm_timeout_sec"]), 600.0))
            except (TypeError, ValueError):
                return "local_llm_timeout_sec — число"
        if "local_llm_system_prompt" in body:
            sp = body.get("local_llm_system_prompt")
            if sp is None or (isinstance(sp, str) and not sp.strip()):
                LOCAL_LLM_INBOX_SYSTEM_PROMPT = _LOCAL_LLM_PROMPT_BASE
            elif isinstance(sp, str):
                LOCAL_LLM_INBOX_SYSTEM_PROMPT = sp.strip()[:12000]
            else:
                return "local_llm_system_prompt — строка"
        if "offerup_try_app_url_first" in body:
            OFFERUP_TRY_APP_URL_FIRST = bool(body["offerup_try_app_url_first"])
        if "local_llm_vision_model" in body:
            v = body.get("local_llm_vision_model")
            if v is None or (isinstance(v, str) and not v.strip()):
                LOCAL_LLM_VISION_MODEL = ""
            elif isinstance(v, str):
                LOCAL_LLM_VISION_MODEL = v.strip()[:200]
            else:
                return "local_llm_vision_model — строка или пусто"
        if "local_llm_screen_assist" in body:
            LOCAL_LLM_SCREEN_ASSIST = bool(body["local_llm_screen_assist"])
        if "local_llm_screen_assist_auto" in body:
            LOCAL_LLM_SCREEN_ASSIST_AUTO = bool(body["local_llm_screen_assist_auto"])
        if "local_llm_use_listing_image_inbox" in body:
            LOCAL_LLM_USE_LISTING_IMAGE_INBOX = bool(body["local_llm_use_listing_image_inbox"])
        if "coach_pending_auto_approve" in body:
            COACH_PENDING_AUTO_APPROVE = bool(body["coach_pending_auto_approve"])
        if "operator_pause_on_fail" in body:
            OPERATOR_PAUSE_ON_FAIL = bool(body["operator_pause_on_fail"])
        if "operator_pause_llm_dump" in body:
            OPERATOR_PAUSE_LLM_DUMP = bool(body["operator_pause_llm_dump"])
        if "coach_system_prompt" in body:
            csp = body.get("coach_system_prompt")
            if csp is None or (isinstance(csp, str) and not csp.strip()):
                LOCAL_LLM_COACH_SYSTEM_PROMPT = LOCAL_LLM_COACH_SYSTEM_PROMPT_BASE
            elif isinstance(csp, str):
                LOCAL_LLM_COACH_SYSTEM_PROMPT = csp.strip()[:16000]
            else:
                return "coach_system_prompt — строка"
        if "coach_training_rules" in body:
            COACH_TRAINING_RULES = _parse_training_rules_field(body.get("coach_training_rules"))
        if "local_llm_extra_training_rules" in body:
            LOCAL_LLM_EXTRA_TRAINING_RULES = _parse_training_rules_field(
                body.get("local_llm_extra_training_rules")
            )
            for rule in LOCAL_LLM_EXTRA_TRAINING_RULES:
                add_ai_training_rule(rule, source="settings")
        global GITHUB_TRAINING_SYNC_ENABLED, GITHUB_TRAINING_REPO, GITHUB_TRAINING_BRANCH
        global GITHUB_TRAINING_PATH, GITHUB_TRAINING_TOKEN, GITHUB_TRAINING_SYNC_INTERVAL_SEC
        if "github_training_repo" in body:
            gr = body.get("github_training_repo")
            if isinstance(gr, str):
                GITHUB_TRAINING_REPO = gr.strip()[:300]
        if "github_training_branch" in body:
            gb = body.get("github_training_branch")
            if isinstance(gb, str) and gb.strip():
                GITHUB_TRAINING_BRANCH = gb.strip()[:120]
        if "github_training_path" in body:
            gp = body.get("github_training_path")
            if isinstance(gp, str) and gp.strip():
                GITHUB_TRAINING_PATH = gp.strip().lstrip("/")[:300]
        if "github_training_token" in body:
            gt = body.get("github_training_token")
            if isinstance(gt, str):
                gts = gt.strip()[:500]
                if gts or not (GITHUB_TRAINING_TOKEN or "").strip():
                    GITHUB_TRAINING_TOKEN = gts
        if "github_training_sync_enabled" in body:
            GITHUB_TRAINING_SYNC_ENABLED = bool(body["github_training_sync_enabled"])
        if "github_training_sync_interval_sec" in body:
            try:
                GITHUB_TRAINING_SYNC_INTERVAL_SEC = max(60, min(int(body["github_training_sync_interval_sec"]), 86400))
            except (TypeError, ValueError):
                pass
        if "anymessage_token" in body:
            t = body.get("anymessage_token")
            if isinstance(t, str):
                ANYMESSAGE_TOKEN = t.strip()[:500]
        if "anymessage_domain" in body:
            d = body.get("anymessage_domain")
            if isinstance(d, str) and d.strip():
                ANYMESSAGE_DOMAIN = d.strip()[:80]
        if "anymessage_site" in body:
            s = body.get("anymessage_site")
            if isinstance(s, str) and s.strip():
                ANYMESSAGE_SITE = s.strip()[:120]
        if "mumu_manager_exe" in body:
            mm = body.get("mumu_manager_exe")
            if isinstance(mm, str):
                MUMU_MANAGER_EXE = mm.strip()[:500]
        _apply_link_api_settings_from_body(body)
        if "local_llm_vision_chat_url" in body:
            vu = body.get("local_llm_vision_chat_url")
            if vu is None or (isinstance(vu, str) and not vu.strip()):
                LOCAL_LLM_VISION_CHAT_URL = ""
            elif isinstance(vu, str):
                LOCAL_LLM_VISION_CHAT_URL = vu.strip()[:2000]
            else:
                return "local_llm_vision_chat_url — строка или пусто"
        if "local_llm_vision_api_key" in body:
            vk = body.get("local_llm_vision_api_key")
            if vk is None:
                LOCAL_LLM_VISION_API_KEY = ""
            elif isinstance(vk, str):
                vks = vk.strip()[:500]
                if vks or not (LOCAL_LLM_VISION_API_KEY or "").strip():
                    LOCAL_LLM_VISION_API_KEY = vks
            else:
                return "local_llm_vision_api_key — строка"
        if "local_llm_vision_api_style" in body:
            vs = body.get("local_llm_vision_api_style")
            if vs is None or (isinstance(vs, str) and not str(vs).strip()):
                LOCAL_LLM_VISION_API_STYLE = ""
            elif isinstance(vs, str):
                s = vs.strip().lower()
                if s not in ("ollama", "openai", "openai_compatible", "lmstudio"):
                    return "local_llm_vision_api_style: ollama или openai"
                LOCAL_LLM_VISION_API_STYLE = "ollama" if s == "ollama" else "openai"
            else:
                return "local_llm_vision_api_style — строка"
        if "local_llm_vision_provider" in body:
            vp = str(body.get("local_llm_vision_provider") or "").strip().lower()
            if vp and vp not in LLM_PROVIDER_PRESETS:
                return "local_llm_vision_provider — как local_llm_provider"
            if vp:
                LOCAL_LLM_VISION_PROVIDER = vp
        _ingest_ui_config_extras(body)
        if persist:
            _persist_settings_merge()
    return None


load_persistent_state()

# ── Авторежим ──────────────────────────────────────────────
_autorun_lock = threading.Lock()
_autorun_state: dict = {
    "running": False,
    "stop_requested": False,
    "count": 0,
    "errors": 0,
    "log": [],
    "current_port": "",
}


def offerup_sanitize_parser_filters(raw: object) -> Dict[str, str]:
    """Только известные имена query-параметров парсера (api documentation.txt)."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        ks = str(k).strip().lower()
        if ks not in OFFERUP_PARSER_QUERY_KEYS:
            continue
        vs = str(v).strip()
        if not vs:
            continue
        lim = OFFERUP_PARSER_MAX_BLACKLIST_LEN if ks == "blacklist" else OFFERUP_PARSER_MAX_PARAM_LEN
        if len(vs) > lim:
            vs = vs[:lim]
        out[ks] = vs
    return out


def offerup_merge_ads_url_with_filters(filter_params: Dict[str, str]) -> str:
    base = OFFERUP_ADS_URL.strip()
    parts = urllib.parse.urlsplit(base)
    q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=False))
    for k, v in filter_params.items():
        q[k] = v
    if "limit" not in q:
        q["limit"] = "1"
    new_query = urllib.parse.urlencode(q)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def offerup_sanitize_chrome_package(s: str) -> str:
    s = (s or "").strip()
    if not s or len(s) > 200:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_.]*$", s):
        return ""
    return s


def offerup_merge_action_timing(overrides: object, apply_jitter: bool = False) -> Dict[str, float]:
    """Тайминги сценария Chrome (секунды).
    apply_jitter=True — добавляет случайное отклонение ±15% к «человеческим» паузам,
    чтобы несколько сессий не выглядели идентично.
    """
    base: Dict[str, float] = {
        "poll_ui": float(OFFERUP_POLL_UI_SEC),
        "open_wait": float(OFFERUP_OPEN_WAIT_SEC),
        "ask_wait": float(OFFERUP_ASK_WAIT_SEC),
        "template_wait": float(OFFERUP_TEMPLATE_WAIT_SEC),
        "after_template": float(OFFERUP_WAIT_AFTER_TEMPLATE_SEC),
        "chrome_sleep": 1.0,
        "after_open_sleep": 0.6,
        "after_ask_sleep": 0.22,
        "limit_wait": float(OFFERUP_LIMIT_WAIT_SEC),
        "conversation_cooldown": float(OFFERUP_CONVERSATION_COOLDOWN_SEC),
    }
    ov: Dict[str, Any] = {}
    if isinstance(overrides, dict):
        ov = dict(overrides)
        # UI шлёт conversation_cooldown_sec; старые сохранения — только limit_wait
        if ov.get("conversation_cooldown_sec") not in (None, ""):
            ov["conversation_cooldown"] = ov["conversation_cooldown_sec"]
        elif ov.get("conversation_cooldown") in (None, "") and ov.get("limit_wait") not in (None, ""):
            try:
                ov["conversation_cooldown"] = float(ov["limit_wait"])
            except (TypeError, ValueError):
                pass
    if not ov:
        return _apply_jitter(base) if apply_jitter else base
    for key in list(base.keys()):
        if key not in ov or ov[key] is None or ov[key] == "":
            continue
        try:
            val = float(ov[key])
        except (TypeError, ValueError):
            continue
        if key == "poll_ui":
            val = max(0.15, min(val, 5.0))
        else:
            val = max(0.2, min(val, 900.0))
        base[key] = val
    return _apply_jitter(base) if apply_jitter else base


# Ключи, к которым применяется jitter (паузы ожидания и сна, но не poll_ui и wait-таймауты)

def offerup_sanitize_inbox_settings(raw: object) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}

    def num(key: str, default: float, lo: float, hi: float) -> float:
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(v, hi))

    return {
        "batch_every": int(num("batch_every", OFFERUP_INBOX_BATCH_EVERY, 1, 50)),
        "max_replies": int(num("max_replies", OFFERUP_INBOX_MAX_REPLIES, 1, 20)),
        "max_scrolls": int(num("max_scrolls", OFFERUP_INBOX_MAX_SCROLLS, 0, 40)),
        "open_wait": num("open_wait", OFFERUP_INBOX_OPEN_WAIT_SEC, 0.2, 60),
        "scan_timeout": num("scan_timeout", OFFERUP_INBOX_SCAN_TIMEOUT_SEC, 0, 600),
        "screen_assist": bool(_truthy_envish(raw.get("screen_assist"), LOCAL_LLM_SCREEN_ASSIST)),
        "use_listing_photo": bool(_truthy_envish(raw.get("use_listing_photo"), LOCAL_LLM_USE_LISTING_IMAGE_INBOX)),
    }

_JITTER_KEYS = frozenset({"chrome_sleep", "after_open_sleep", "after_ask_sleep", "after_template"})
# Диапазон jitter: ±15% относительно базового значения
_JITTER_FACTOR = 0.15


def _apply_jitter(t: Dict[str, float]) -> Dict[str, float]:
    """Добавляет случайное отклонение к паузам — каждый вызов даёт уникальный набор."""
    out = dict(t)
    for key in _JITTER_KEYS:
        if key not in out:
            continue
        base = out[key]
        lo = base * (1.0 - _JITTER_FACTOR)
        hi = base * (1.0 + _JITTER_FACTOR)
        out[key] = round(random.uniform(lo, hi), 3)
    return out


def _offerup_log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    with _offerup_lock:
        _offerup_job.setdefault("log", []).append(line)


def _inbox_manual_log_line(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    with _inbox_manual_lock:
        lst = _inbox_manual_state.setdefault("log", [])
        lst.append(line)
        if len(lst) > 450:
            del lst[: len(lst) - 400]


def _inbox_worker_should_stop() -> bool:
    if force_stop_is_set():
        return True
    if inbox_scan_abort_is_set():
        return True
    with _inbox_manual_lock:
        return bool(_inbox_manual_state.get("stop_requested"))
    return False


def _offerup_inbox_run_once(
    serial: Optional[str],
    inbox_cfg: Dict[str, Any],
    message_templates: List[str],
    local_llm_inbox: bool,
    buyer_name: str,
    buyer_address: str,
) -> int:
    return offerup_process_inbox_replies(
        serial,
        _inbox_manual_log_line,
        should_stop=_inbox_worker_should_stop,
        max_replies=int(inbox_cfg["max_replies"]),
        max_scrolls=int(inbox_cfg["max_scrolls"]),
        open_wait=float(inbox_cfg["open_wait"]),
        scan_timeout=float(inbox_cfg["scan_timeout"]),
        message_templates=message_templates or None,
        local_llm_inbox=local_llm_inbox,
        buyer_name_on_profile=buyer_name,
        buyer_address_on_profile=buyer_address,
        screen_assist=bool(inbox_cfg.get("screen_assist", True)),
        use_listing_photo=bool(inbox_cfg.get("use_listing_photo", True)),
    )


def _offerup_inbox_manual_worker(
    serial: Optional[str],
    inbox_cfg: Dict[str, Any],
    message_templates: List[str],
    local_llm_inbox: bool,
    buyer_name: str,
    buyer_address: str,
    *,
    loop_mode: bool = False,
) -> None:
    """Фон: один проход Inbox или цикл до stop."""
    global ADB_PORT, _ADB_RESOLVED
    desk_hint = mumu_desktop_missing_apps_hint(serial)
    if desk_hint:
        enqueue_coach_chat_message(desk_hint, kind="desktop")
        append_coach_feed_from_inbox(desk_hint)
    try:
        if serial:
            ADB_PORT = str(serial).strip()
            _ADB_RESOLVED = None
        port_label = serial or ADB_PORT or "default"
        if loop_mode:
            interval = max(15.0, float(inbox_cfg.get("batch_every", 30) or 30))
            _inbox_manual_log_line(f"Inbox (цикл): старт, порт {port_label}, интервал {interval:.0f} с")
            pass_n = 0
            while not _inbox_worker_should_stop():
                pass_n += 1
                _inbox_manual_log_line(f"Inbox (цикл): проход #{pass_n}")
                handled = _offerup_inbox_run_once(
                    serial, inbox_cfg, message_templates, local_llm_inbox, buyer_name, buyer_address
                )
                _inbox_manual_log_line(f"Inbox (цикл): проход #{pass_n} — обработано: {handled}")
                if _inbox_worker_should_stop():
                    break
                end = time.monotonic() + interval
                while time.monotonic() < end and not _inbox_worker_should_stop():
                    time.sleep(0.45)
            _inbox_manual_log_line("Inbox (цикл): остановлен")
        else:
            _inbox_manual_log_line("Inbox (разово): старт, порт " + port_label)
            handled = _offerup_inbox_run_once(
                serial, inbox_cfg, message_templates, local_llm_inbox, buyer_name, buyer_address
            )
            _inbox_manual_log_line(f"Inbox (разово): готово, обработано: {handled}")
    except Exception as e:
        err = str(e)
        with _inbox_manual_lock:
            _inbox_manual_state["error"] = err
        _inbox_manual_log_line("Ошибка: " + err)
    finally:
        with _inbox_manual_lock:
            _inbox_manual_state["running"] = False
            _inbox_manual_state["loop_mode"] = False
            _inbox_manual_state["stop_requested"] = False


def _offerup_node_center(node: ET.Element) -> Optional[Tuple[int, int]]:
    pr = parse_bounds(node.get("bounds") or "")
    if not pr:
        return None
    l, t, r, b = pr
    return (l + r) // 2, (t + b) // 2


def _offerup_find_clickable(
    root: ET.Element, pred: Callable[[ET.Element], bool]
) -> Optional[Tuple[int, int]]:
    best: Optional[Tuple[int, int, int]] = None
    for node in root.iter("node"):
        if node.get("clickable", "false") != "true":
            continue
        if node.get("enabled", "true") == "false":
            continue
        if not pred(node):
            continue
        xy = _offerup_node_center(node)
        if not xy:
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        l, t, r, b = pr
        area = max(1, (r - l) * (b - t))
        if best is None or area < best[0]:
            best = (area, xy[0], xy[1])
    if not best:
        return None
    return best[1], best[2]


def _offerup_resource_id(node: ET.Element) -> str:
    return (node.get("resource-id") or "").strip()


def _offerup_find_tap_by_resource_id(
    root: ET.Element,
    *substrings: str,
    scr_w: int = 0,
    scr_h: int = 0,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
    require_clickable: bool = True,
    package_hint: str = "",
) -> Optional[Tuple[int, int]]:
    """Тап по узлу, у которого resource-id содержит любой из substrings (без учёта регистра)."""
    if not substrings:
        return None
    lows = [s.lower() for s in substrings if s]
    if not lows:
        return None
    if not scr_w or not scr_h:
        scr_w, scr_h = _offerup_screen_wh(None, root)
    y_min = int(scr_h * y_min_frac) if scr_h else 0
    y_max = int(scr_h * y_max_frac) if scr_h else 99999
    x_min = int(scr_w * x_min_frac) if scr_w else 0
    x_max = int(scr_w * x_max_frac) if scr_w else 99999
    pkg_hint = (package_hint or "").lower()
    best: Optional[Tuple[int, int, int]] = None
    for node in root.iter("node"):
        rid = _offerup_resource_id(node).lower()
        if not rid or not any(s in rid for s in lows):
            continue
        if pkg_hint and pkg_hint not in (node.get("package") or "").lower():
            continue
        if require_clickable and node.get("clickable", "false") != "true":
            continue
        if node.get("enabled", "true") == "false":
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        cx = (pr[0] + pr[2]) // 2
        cy = (pr[1] + pr[3]) // 2
        if cx < x_min or cx > x_max or cy < y_min or cy > y_max:
            continue
        area = max(1, (pr[2] - pr[0]) * (pr[3] - pr[1]))
        if best is None or area < best[0]:
            best = (area, cx, cy)
    if not best:
        return None
    return best[1], best[2]


def offerup_tap_by_resource_id(
    serial: Optional[str],
    *resource_substrings: str,
    log_func: Optional[Callable[[str], None]] = None,
    timeout: float = 2.8,
    y_min_frac: float = 0.0,
    y_max_frac: float = 1.0,
    x_min_frac: float = 0.0,
    x_max_frac: float = 1.0,
    require_clickable: bool = True,
    package_hint: str = "",
) -> bool:
    port = (serial or ADB_PORT or "").strip()
    if not port or not resource_substrings:
        return False
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        root = dump_ui_serial(port)
        if root is not None:
            sw, sh = _offerup_screen_wh(port, root)
            xy = _offerup_find_tap_by_resource_id(
                root,
                *resource_substrings,
                scr_w=sw,
                scr_h=sh,
                y_min_frac=y_min_frac,
                y_max_frac=y_max_frac,
                x_min_frac=x_min_frac,
                x_max_frac=x_max_frac,
                require_clickable=require_clickable,
                package_hint=package_hint,
            )
            if xy:
                if log_func:
                    log_func(f"Тап resource-id «{resource_substrings[0]}» {xy}")
                _offerup_tap_xy(port, xy, log_func=log_func)
                return True
        time.sleep(0.07)
    return False


def _offerup_label(node: ET.Element) -> str:
    t = (node.get("text") or "").strip()
    d = (node.get("content-desc") or "").strip()
    return (t + " " + d).strip()


_POPUP_DISMISS_SINGLE_CHAR_RE = re.compile(r"^[×✕✗xXхХ]$")

_ITEM_REMOVED_MARKERS = re.compile(
    r"\b(item|listing)\s+removed\b|no\s+longer\s+available|this\s+item\s+is\s+gone|"
    r"объявлени[ея]\s+удален|товар\s+удал",
    re.I,
)


def _offerup_screen_dims(root: ET.Element) -> Tuple[int, int]:
    scr_w = scr_h = 0
    for node in root.iter("node"):
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            _, _, r, b = pr
            scr_w, scr_h = max(scr_w, r), max(scr_h, b)
    return scr_w, scr_h


def _offerup_find_got_it(root: ET.Element) -> Optional[Tuple[int, int]]:
    best: Optional[Tuple[int, int, int]] = None
    for node in root.iter("node"):
        if node.get("clickable", "false") != "true":
            continue
        if node.get("enabled", "true") == "false":
            continue
        raw = ((node.get("text") or "") + " " + (node.get("content-desc") or "")).strip().lower()
        if not raw:
            continue
        score = 0
        if "got it" in raw or raw == "ok" or "okay" == raw:
            score = 300
        elif "понятно" in raw or "ясно" in raw:
            score = 280
        elif "dismiss" in raw and len(raw) < 24:
            score = 120
        if score < 120:
            continue
        xy = _offerup_node_center(node)
        if not xy:
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        l, t, r, b = pr
        area = max(1, (r - l) * (b - t))
        if best is None or score > best[0] or (score == best[0] and area < best[3]):
            best = (score, xy[0], xy[1], area)
    if not best:
        return None
    return best[1], best[2]


def _offerup_topright_tap(root: ET.Element, serial: Optional[str]) -> bool:
    scr_w, scr_h = _offerup_screen_dims(root)
    if scr_w < 200 or scr_h < 200:
        return False
    cx, cy = int(scr_w * 0.92), int(scr_h * 0.08)
    _offerup_log(f"Premium fallback: tap top-right ({cx}, {cy})")
    if serial:
        run_adb_serial(serial, ["shell", "input", "tap", str(cx), str(cy)])
    else:
        run_adb(["shell", "input", "tap", str(cx), str(cy)])
    time.sleep(0.35)
    return True


def _offerup_dismiss_item_removed(serial: Optional[str]) -> bool:
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return False
    blob = " ".join(_ui_visible_texts(root)).lower()
    if not _ITEM_REMOVED_MARKERS.search(blob):
        return False
    gi = _offerup_find_got_it(root)
    if gi:
        _offerup_log(f"Item removed / недоступно — «Got it» {gi}")
        if serial:
            run_adb_serial(serial, ["shell", "input", "tap", str(gi[0]), str(gi[1])])
        else:
            tap(gi)
        time.sleep(0.45)
        return True
    xy = _offerup_find_dismiss_popup(root)
    if xy:
        _offerup_log(f"Item removed — закрытие крестиком {xy}")
        if serial:
            run_adb_serial(serial, ["shell", "input", "tap", str(xy[0]), str(xy[1])])
        else:
            tap(xy)
        time.sleep(0.4)
        return True
    return False


def _offerup_find_dismiss_popup(root: ET.Element) -> Optional[Tuple[int, int]]:
    """
    Ищет крестик закрытия рекламного попапа OfferUp («Premium members» и т.п.).
    Критерии: кнопка ×/✕/Close в верхнем правом углу экрана.
    """
    scr_w = scr_h = 0
    candidates = []
    for node in root.iter("node"):
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            _, _, r, b = pr
            scr_w, scr_h = max(scr_w, r), max(scr_h, b)
        if node.get("clickable", "false") != "true":
            continue
        if node.get("enabled", "true") == "false":
            continue
        if not pr:
            continue
        l, t, r, b = pr
        cx, cy = (l + r) // 2, (t + b) // 2
        w, h = r - l, b - t
        # Должна быть в верхней трети экрана и в правой половине
        if scr_h and cy > scr_h * 0.45:
            continue
        if scr_w and cx < scr_w * 0.45:
            continue
        text  = (node.get("text") or "").strip()
        rid   = (node.get("resource-id") or "").lower()
        cdesc = (node.get("content-desc") or "").lower()
        score = 0
        # Ключевые слова крестика
        for kw in ("close", "dismiss", "cancel", "×", "✕", "✗", "x"):
            if kw in text.lower() or kw in rid or kw in cdesc:
                score += 200
        if _POPUP_DISMISS_SINGLE_CHAR_RE.match(text):
            score += 400
        # Маленькая кнопка — типичный крестик
        if 24 <= w <= 100 and 24 <= h <= 100:
            score += 80
        if score < 80:
            continue
        candidates.append((score, cx, cy))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1], candidates[0][2]


def _offerup_dismiss_location_profile(serial: Optional[str] = None) -> bool:
    """Один раз: «Add a location to your profile» → зелёная «Add this location to profile»."""
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return False
    blob = " ".join(_ui_visible_texts(root)).lower()
    if "add a location to your profile" not in blob and "add this location to profile" not in blob:
        if "build local trust" not in blob:
            return False
    needles = (
        "add this location to profile",
        "add location to profile",
    )

    def _tap_node(node: ET.Element) -> bool:
        if node.get("clickable", "false") != "true":
            return False
        t = _collapse_ws((node.get("text") or "") + " " + (node.get("content-desc") or "")).lower()
        if not any(n in t for n in needles):
            return False
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            return False
        l, top, r, b = pr
        cx, cy = (l + r) // 2, (top + b) // 2
        _offerup_log(f"Попап локации → tap ({cx}, {cy})")
        if serial:
            run_adb_serial(serial, ["shell", "input", "tap", str(cx), str(cy)])
        else:
            tap((cx, cy))
        time.sleep(0.35)
        return True

    for node in root.iter("node"):
        if _tap_node(node):
            return True
    try:
        from mumu_onboarding import _wait_tap_text

        if _wait_tap_text(
            serial or ADB_PORT or "",
            ("Add this location to profile", "Add this location"),
            4.0,
            log=_offerup_log,
        ):
            time.sleep(0.3)
            return True
    except Exception:
        pass
    return False


def _offerup_dismiss_if_popup(
    serial: Optional[str] = None,
    root: Optional[ET.Element] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    """Один uiautomator dump (или переданный root) — все попапы listing/inbox."""
    log = log_func or _offerup_log
    root_use = root
    if root_use is None:
        root_use = dump_ui_serial(serial) if serial else dump_ui()
    if root_use is None:
        return False
    if _offerup_dismiss_blockers_from_root(serial, root_use, log):
        return True
    popup_markers = ("premium", "members", "free trial", "per month", "early access", "inbox priority")
    page_text = " ".join(
        ((n.get("text") or "") + " " + (n.get("content-desc") or "")).lower()
        for n in root_use.iter("node")
    )
    if not any(m in page_text for m in popup_markers):
        return False
    xy = _offerup_find_dismiss_popup(root_use)
    if not xy:
        if _offerup_topright_tap(root_use, serial):
            return True
        if serial:
            run_adb_serial(serial, ["shell", "input", "keyevent", "4"])
        else:
            run_adb(["shell", "input", "keyevent", "4"])
        log("Попап (без крестика) — нажат Back")
        time.sleep(0.35)
        return True
    log(f"Попап закрыт крестиком {xy}")
    if serial:
        run_adb_serial(serial, ["shell", "input", "tap", str(xy[0]), str(xy[1])])
    else:
        tap(xy)
    time.sleep(0.35)
    return True


def _offerup_wait_if_limit_popup(
    serial: Optional[str],
    cooldown_sec: float,
    log_func: Optional[Callable[[str], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return False
    text = " ".join(_ui_visible_texts(root)).lower()
    if not re.search(r"\b(conversation|conservation)\s+limit\b", text):
        return False
    log = log_func or _offerup_log
    cd = max(1.0, min(float(cooldown_sec or OFFERUP_CONVERSATION_COOLDOWN_SEC), 3600.0))
    log("Лимит переписок: ищу «Got it»…")
    gi = _offerup_find_got_it(root)
    if gi:
        log(f"«Got it» {gi}")
        if serial:
            run_adb_serial(serial, ["shell", "input", "tap", str(gi[0]), str(gi[1])])
        else:
            tap(gi)
        time.sleep(0.55)
    else:
        log("«Got it» не найден — пробую закрыть диалог")
        _offerup_dismiss_if_popup(serial)
    log(f"Пауза после лимита переписок: {int(cd)} с")
    deadline = time.monotonic() + cd
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return True
        time.sleep(min(0.5, deadline - time.monotonic()))
    return True


def _ui_visible_texts(root: Optional[ET.Element]) -> List[str]:
    if root is None:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for node in root.iter("node"):
        for key in ("text", "content-desc"):
            v = _collapse_ws(node.get(key) or "")
            if v and len(v) <= 500 and v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _text_fingerprint(texts: List[str]) -> set[str]:
    return {_collapse_ws(t).lower() for t in texts if t.strip()}


_NEGATIVE_REPLY_RE = re.compile(
    r"\b(no|nope|nah|not available|sold|already sold|unavailable|don't have|do not have|out of stock|нет|не в наличии|продан|продано)\b",
    re.I,
)
_INBOX_SOLD_SNIPPET_RE = re.compile(
    r"this item has been sold|item has been sold|item is no longer available|listing has ended",
    re.I,
)
_INBOX_ROUTINE_QUESTION_RE = re.compile(
    r"\b(are you in|where are you|which state|what state|ship|shipping|mail|transaction|pending|payment|"
    r"paypal|venmo|zelle|still available|how much|price|chicago|california|texas|florida)\b",
    re.I,
)
# Только явные вопросы про товар/фото/вариант — не любой «?» (иначе «Yes, when would you like to meet?» → «первое фото»).
_STRICT_PRODUCT_QUESTION_RE = re.compile(
    r"\b(which\s+(one|item|color|colour|size|version|variant|photo|picture|of\s+these|is\s+it)|"
    r"what\s+(item|one|color|colour|size|version|photo|picture|is\s+this)|"
    r"which\s+is|what\s+is\s+the\s+(item|size|color|colour)|"
    r"какой\s+(цвет|размер|вариант|товар)|какого\s+размера|который\s+(из|вариант))\b",
    re.I,
)
_POSITIVE_REPLY_RE = re.compile(
    r"\b(yes|yeah|yep|available|still available|ok|okay|sure|да|есть|в наличии|конечно)\b",
    re.I,
)


_PICKUP_ONLY_REPLY_RE = re.compile(
    r"\b(only\s+pick\s*up|pick\s*up\s+only|pickup\s+only|local\s+pickup\s+only|no\s+ship(?:ping)?|can't\s+ship|cannot\s+ship|won't\s+ship|will\s+not\s+ship)\b",
    re.I,
)
_ONLY_CASH_REPLY_RE = re.compile(
    r"\b(only\s+cash|cash\s+only|cash\s+accepted\s+only|cash\s+transactions?\s+only)\b",
    re.I,
)
_CALL_REQUEST_RE = re.compile(
    r"\b(call\s+me|give\s+me\s+a\s+call|can\s+you\s+call|could\s+you\s+call|"
    r"phone\s+me|my\s+number\s+is|call\s+you|give\s+(me\s+)?your\s+number|"
    r"звоните|позвоните|позвони|наберите)\b",
    re.I,
)
_BUYER_TEMPLATE_MARKERS = (
    "hi, i'd like to buy",
    "hi, i would like to buy",
    "i'd like to buy this",
    "i would like to buy this",
)


_INBOX_LOCATION_Q_RE = re.compile(
    r"\b(where\s+are\s+you|where\s+you\s+located|located|which\s+state|what\s+state|"
    r"are\s+you\s+in|zip\s*code|what\s+city|from\s+what\s+city|california|calif\w*|"
    r"texas|florida|new\s+york|nevada|arizona|colorado|washington|oregon)\b",
    re.I,
)


def _shipping_address_reply(buyer_address: str) -> str:
    addr = (buyer_address or "").strip()
    if addr:
        return f"Could you ship to {addr}? I'll cover shipping."
    return "I'll send my shipping address shortly."


def _inbox_fallback_location_reply(snippet: str, buyer_address: str) -> str:
    return _shipping_address_reply(buyer_address)


_WRONG_LOCATION_CITY_RE = re.compile(
    r"\b(chicago|houston|dallas|miami|atlanta|boston|seattle|denver|phoenix|"
    r"california|texas|florida|arizona|nevada|new york|los angeles)\b",
    re.I,
)


def _fix_reply_if_invented_location(text: str, buyer_address: str) -> str:
    """Если модель выдумала город/штат — только адрес из настроек."""
    t = _collapse_ws(text or "")
    addr = (buyer_address or "").strip()
    if not t or not addr:
        return t
    low = t.lower()
    addr_low = addr.lower()
    if re.search(r"\byes\b.*\b(i'?m\s+)?in\b", low) and addr_low not in low:
        return _shipping_address_reply(addr)
    m = _WRONG_LOCATION_CITY_RE.search(low)
    if m and m.group(1).lower() not in addr_low:
        return _shipping_address_reply(addr)
    return t


def _is_seller_apology_not_refusal(text: str) -> bool:
    low = (text or "").lower()
    if re.search(r"\boh\s+no\b", low) and re.search(r"\bsorry\b", low):
        return True
    if "sorry" in low and not re.search(
        r"\b(can'?t|cannot|won'?t|no ship|no shipping|not ship|pickup only|cash only)\b", low
    ):
        return True
    return False


def _inbox_row_is_sold_notice(row: dict) -> bool:
    snip = _collapse_ws(str(row.get("snippet") or ""))
    name = _collapse_ws(str(row.get("name") or "")).lower()
    if _INBOX_SOLD_SNIPPET_RE.search(snip):
        return True
    if name in ("offerup", "offer up") and "sold" in snip.lower():
        return True
    return False


def _remember_inbox_operator_lesson(seller_name: str, seller_message: str) -> None:
    key = _normalize_inbox_match_name(seller_name)
    sm = _collapse_ws(seller_message)[:240]
    if not key or not sm:
        return
    with _inbox_operator_lesson_lock:
        _inbox_operator_pending_lessons[key] = sm


def _record_operator_inbox_lesson(seller_message: str, buyer_reply: str, seller_name: str = "") -> None:
    global LOCAL_LLM_EXTRA_TRAINING_RULES
    sm = _collapse_ws(seller_message)[:200]
    br = _collapse_ws(buyer_reply)[:220]
    if not sm or not br or len(br) < 4:
        return
    rule = f"If seller says «{sm}», reply: «{br}»"
    rules = list(LOCAL_LLM_EXTRA_TRAINING_RULES or [])
    if rule in rules:
        return
    if add_ai_training_rule(rule, source="inbox"):
        append_coach_feed_from_inbox(f"Запомнил правило Inbox: {rule[:120]}…")


def _maybe_learn_inbox_from_coach_send(seller_hint: str, message_text: str) -> None:
    key = _normalize_inbox_match_name(seller_hint)
    txt = _collapse_ws(message_text)
    if not key or not txt:
        return
    with _inbox_operator_lesson_lock:
        snippet = _inbox_operator_pending_lessons.pop(key, None)
    if snippet:
        _record_operator_inbox_lesson(snippet, txt, seller_hint)


def _llm_decision_apply_ask_operator_guard(
    decision: Optional[dict],
    snippet: str,
    buyer_address: str,
    log_func: Callable[[str], None],
) -> Optional[dict]:
    """Не блокировать чат из-за ask_operator, если можно ответить по правилам."""
    if not decision or not decision.get("ask_operator"):
        return decision
    out = dict(decision)
    intent_raw = str(out.get("intent") or "").strip().lower()
    raw_tr = _llm_decision_text_replies(out)
    if raw_tr:
        out["ask_operator"] = False
        out["operator_message"] = ""
        if not intent_raw or intent_raw == "none":
            out["intent"] = "question"
        log_func("Inbox LLM: ask_operator снят — есть text_replies от модели")
        return out
    rk = classify_offerup_reply(snippet)
    if (
        intent_raw == "question"
        or rk == "question"
        or _INBOX_LOCATION_Q_RE.search(snippet or "")
        or _INBOX_ROUTINE_QUESTION_RE.search(snippet or "")
    ):
        if _INBOX_LOCATION_Q_RE.search(snippet or ""):
            fb = _inbox_fallback_location_reply(snippet, buyer_address)
        else:
            fb = OFFERUP_INBOX_GENERIC_QUESTION_FALLBACK
        out["ask_operator"] = False
        out["operator_message"] = ""
        out["intent"] = "question"
        out["send_link"] = False
        out["text_replies"] = [fb]
        log_func("Inbox LLM: ask_operator заменён автоответом покупателю")
        return out
    return decision


def classify_offerup_reply(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return "none"
    if _ONLY_CASH_REPLY_RE.search(s):
        return "only_cash"
    if _CALL_REQUEST_RE.search(s):
        return "call_request"
    if _PICKUP_ONLY_REPLY_RE.search(s):
        return "pickup_only"
    if _is_seller_apology_not_refusal(s):
        return "neutral"
    if _NEGATIVE_REPLY_RE.search(s) and not re.search(r"\boh\s+no\b", s, re.I):
        return "negative"
    # Сначала согласие/доступность — до «?», иначе встреча с yes попадает под шаблон вопроса.
    if _POSITIVE_REPLY_RE.search(s):
        return "positive"
    if _STRICT_PRODUCT_QUESTION_RE.search(s):
        return "question"
    if "?" in s:
        return "neutral"
    return "neutral"


def wait_for_offerup_reply(
    serial: Optional[str],
    baseline: set[str],
    *,
    timeout_sec: float = OFFERUP_REPLY_WAIT_SEC,
    poll_sec: float = OFFERUP_REPLY_POLL_SEC,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Tuple[str, str]:
    deadline = time.monotonic() + max(1.0, timeout_sec)
    ignored = {"ask", "send", "open", "message", "type a message", "write a message"}
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            return "stopped", ""
        root = dump_ui_serial(serial) if serial else dump_ui()
        texts = _ui_visible_texts(root)
        fresh: List[str] = []
        for t in texts:
            fp = _collapse_ws(t).lower()
            if not fp or fp in baseline or fp in ignored:
                continue
            if len(fp) < 2 or fp.startswith("http"):
                continue
            fresh.append(t)
        if fresh:
            reply = fresh[-1]
            return classify_offerup_reply(reply), reply
        time.sleep(poll_sec)
    return "timeout", ""


def input_text_serial(text: str, serial: str) -> bool:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    for li, line in enumerate(lines):
        for i in range(0, len(line), ADB_INPUT_TEXT_CHUNK):
            chunk = line[i : i + ADB_INPUT_TEXT_CHUNK]
            if not chunk:
                continue
            r = run_adb_serial(serial, ["shell", f"input text {_posix_quote(chunk)}"])
            if not r or r.returncode != 0:
                return False
            time.sleep(0.028)
        if li < len(lines) - 1:
            r2 = run_adb_serial(serial, ["shell", "input", "keyevent", "66"])
            if not r2 or r2.returncode != 0:
                return False
            time.sleep(0.04)
    if len(text) > ADB_INPUT_TEXT_CHUNK:
        time.sleep(min(0.55, 0.00035 * len(text)))
    return True


def _offerup_confirm_send_after_compose(
    serial: Optional[str],
    expected_text: str = "",
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    """После выбора шаблона-пилюли: нажать Send, если сообщение ещё не ушло."""
    needle = _collapse_ws(expected_text or "")
    log = log_func or _offerup_log
    for attempt in range(1, 5):
        root = dump_ui_serial(serial) if serial else dump_ui()
        if root is None:
            time.sleep(0.22)
            continue
        if needle and _chat_contains_sent_text(needle, root):
            if attempt > 1:
                log("Сообщение уже в чате")
            return True
        if _offerup_tap_send_button(serial, root, log_func=log):
            time.sleep(0.38)
        else:
            taps = find_edit_and_send(root)
            if taps:
                _, send_xy = taps
                if serial:
                    run_adb_serial(serial, ["shell", "input", "tap", str(send_xy[0]), str(send_xy[1])])
                else:
                    tap(send_xy)
                time.sleep(0.35)
        root_chk = dump_ui_serial(serial) if serial else dump_ui()
        if not needle or _chat_contains_sent_text(needle, root_chk):
            return True
        if serial:
            run_adb_serial(serial, ["shell", "input", "keyevent", "66"])
        else:
            run_adb(["shell", "input", "keyevent", "66"])
        time.sleep(0.28)
    return False


def _chat_ui_blob(root: Optional[ET.Element]) -> str:
    if root is None:
        return ""
    return " ".join(_collapse_ws(t).lower() for t in _ui_visible_texts(root))


def _chat_contains_sent_text(text: str, root: Optional[ET.Element]) -> bool:
    blob = _chat_ui_blob(root)
    if not blob:
        return False
    t = _collapse_ws(text).lower()
    if not t:
        return False
    if t in blob:
        return True
    for n in (60, 45, 30):
        if len(t) >= n and t[:n] in blob:
            return True
    return False


def _offerup_tap_send_button(
    serial: Optional[str],
    root: ET.Element,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    xy = _offerup_find_tap_by_resource_id(
        root,
        "DiscussionFooter.ChatInput.SendButton",
        "ChatInput.SendButton",
        y_min_frac=0.52,
        package_hint="com.offerup",
    )
    if not xy:
        return False
    if log_func:
        log_func(f"Inbox scan: Send → {xy}")
    if serial:
        run_adb_serial(serial, ["shell", "input", "tap", str(xy[0]), str(xy[1])])
    else:
        tap(xy)
    return True


def send_text_to_current_chat(
    text: str,
    serial: Optional[str] = None,
    root: Optional[ET.Element] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    """Ввод + Send с повтором: после клавиатуры координаты Send часто съезжают."""
    msg = _collapse_ws(text or "")
    if not msg:
        return False
    log = log_func or _offerup_log
    for attempt in range(1, 4):
        root_use = root if attempt == 1 and root is not None else (
            dump_ui_serial(serial) if serial else dump_ui()
        )
        if root_use is None:
            continue
        taps = find_edit_and_send(root_use)
        if not taps:
            log(f"Inbox scan: поле ввода/Send не найдены (попытка {attempt})")
            continue
        edit_xy, send_xy = taps
        if (
            serial
            and send_text_batch_short is not None
            and len(msg) <= 90
            and "\n" not in msg
        ):
            if send_text_batch_short(serial, msg, edit_xy, send_xy):
                time.sleep(0.30)
                root_chk = dump_ui_serial(serial, fresh=True)
                if _chat_contains_sent_text(msg, root_chk):
                    if attempt > 1:
                        log(f"Inbox scan: сообщение в чате (batch, попытка {attempt})")
                    return True
        if serial:
            run_adb_serial(serial, ["shell", "input", "tap", str(edit_xy[0]), str(edit_xy[1])])
        else:
            tap(edit_xy)
        time.sleep(0.14)
        if serial:
            if not input_text_serial(msg, serial):
                continue
        elif not input_text(msg):
            continue
        time.sleep(0.22)
        root_after = dump_ui_serial(serial) if serial else dump_ui()
        sent_tap = False
        if root_after is not None and _offerup_tap_send_button(serial, root_after, log_func=log):
            sent_tap = True
        else:
            taps2 = find_edit_and_send(root_after) if root_after is not None else None
            if taps2:
                _, send_xy2 = taps2
                if serial:
                    run_adb_serial(
                        serial, ["shell", "input", "tap", str(send_xy2[0]), str(send_xy2[1])]
                    )
                else:
                    tap(send_xy2)
                sent_tap = True
            elif serial:
                run_adb_serial(serial, ["shell", "input", "tap", str(send_xy[0]), str(send_xy[1])])
            else:
                tap(send_xy)
        time.sleep(0.32)
        root_chk = dump_ui_serial(serial) if serial else dump_ui()
        if _chat_contains_sent_text(msg, root_chk):
            if attempt > 1:
                log(f"Inbox scan: сообщение в чате (попытка {attempt})")
            return True
        if sent_tap:
            if serial:
                run_adb_serial(serial, ["shell", "input", "keyevent", "66"])
            else:
                run_adb(["shell", "input", "keyevent", "66"])
            time.sleep(0.28)
            root_chk = dump_ui_serial(serial) if serial else dump_ui()
            if _chat_contains_sent_text(msg, root_chk):
                return True
    log(f"Inbox scan: текст набран, но в чате не видно — Send не сработал: {msg[:50]}…")
    return False


PICKUP_ONLY_RESPONSE = "can you ship it please? i will pay shipping"
SHIP_PREFERENCE_REPLY = "Shipping works great for me! Can you ship it? I'll cover shipping."
MEET_FALLBACK_REPLY = "Let's meet tomorrow!"
_MEET_OR_SHIP_OFFER_RE = re.compile(
    r"\b(meet\s*(up)?|meeting|pick\s*up|pickup|local\s+pick|ship(?:ping)?|can\s+you\s+ship|willing\s+to\s+ship)\b",
    re.I,
)
_COACH_OPEN_CHAT_WAIT_SEC = 0.28
_COACH_ACTION_TAP_TIMEOUT_SEC = 2.2
_ONBOARDING_TAP_POLL_SEC = 0.10
_UI_DUMP_CACHE_TTL_SEC = 0.25


def _llm_api_key() -> str:
    return (LOCAL_LLM_API_KEY or os.environ.get("LOCAL_LLM_API_KEY") or "").strip()


def _llm_vision_api_key() -> str:
    return (LOCAL_LLM_VISION_API_KEY or _llm_api_key() or "").strip()


def _llm_effective_vision_url() -> str:
    return (LOCAL_LLM_VISION_CHAT_URL or LOCAL_LLM_CHAT_URL or "").strip()


def _llm_effective_vision_api_style() -> str:
    vs = (LOCAL_LLM_VISION_API_STYLE or "").strip().lower()
    if vs in ("ollama", "openai", "openai_compatible", "lmstudio"):
        return "ollama" if vs == "ollama" else "openai"
    vurl = _llm_effective_vision_url()
    turl = (LOCAL_LLM_CHAT_URL or "").strip()
    if vurl and vurl != turl:
        return "ollama" if "11434" in vurl else "openai"
    return (LOCAL_LLM_API_STYLE or "ollama").strip().lower()


def _llm_vision_provider() -> str:
    vp = (LOCAL_LLM_VISION_PROVIDER or "").strip().lower()
    if vp:
        return vp
    if (LOCAL_LLM_VISION_CHAT_URL or "").strip():
        return _infer_llm_provider_from_url(LOCAL_LLM_VISION_CHAT_URL)
    return (LOCAL_LLM_PROVIDER or "custom").strip().lower()


def _llm_vision_needs_key() -> bool:
    if (LOCAL_LLM_VISION_CHAT_URL or "").strip():
        return bool(_llm_provider_preset(_llm_vision_provider()).get("needs_api_key"))
    return _llm_provider_needs_key()


def _llm_provider_preset(provider: Optional[str] = None) -> dict:
    p = (provider or LOCAL_LLM_PROVIDER or "custom").strip().lower()
    return dict(LLM_PROVIDER_PRESETS.get(p) or LLM_PROVIDER_PRESETS["custom"])


def _llm_provider_needs_key(provider: Optional[str] = None) -> bool:
    return bool(_llm_provider_preset(provider).get("needs_api_key"))


def _infer_llm_provider_from_url(url: str) -> str:
    low = (url or "").lower()
    if "11434" in low:
        return "ollama"
    if "deepseek.com" in low:
        return "deepseek"
    if "groq.com" in low:
        return "groq"
    if "openrouter.ai" in low:
        return "openrouter"
    if "generativelanguage.googleapis.com" in low or "googleapis.com" in low:
        return "gemini"
    if "api.openai.com" in low or "openai.com/v1" in low:
        return "openai"
    if "mistral.ai" in low:
        return "mistral"
    if ":1234" in low or "lmstudio" in low:
        return "lmstudio"
    if "127.0.0.1" in low or "localhost" in low:
        return "ollama" if "11434" in low else "lmstudio"
    return "custom"


def _llm_providers_for_api() -> List[dict]:
    out: List[dict] = []
    for pid, preset in LLM_PROVIDER_PRESETS.items():
        out.append(
            {
                "id": pid,
                "label": preset.get("label") or pid,
                "url": preset.get("url") or "",
                "api_style": preset.get("api_style") or "openai",
                "model": preset.get("model") or "",
                "vision_model": preset.get("vision_model") or "",
                "vision_models": list(preset.get("vision_models") or []),
                "needs_api_key": bool(preset.get("needs_api_key")),
                "hint": preset.get("hint") or "",
            }
        )
    return out


def _sync_llm_provider_preset(
    provider: Optional[str],
    *,
    fill_vision_if_empty: bool = True,
    target: str = "text",
) -> None:
    """Подставить URL/модели из пресета (target: text | vision | both)."""
    global LOCAL_LLM_CHAT_URL, LOCAL_LLM_MODEL, LOCAL_LLM_API_STYLE, LOCAL_LLM_VISION_MODEL
    global LOCAL_LLM_VISION_CHAT_URL, LOCAL_LLM_VISION_API_STYLE, LOCAL_LLM_VISION_PROVIDER
    p = (provider or LOCAL_LLM_PROVIDER or "custom").strip().lower()
    preset = LLM_PROVIDER_PRESETS.get(p)
    if not preset:
        return
    url = (preset.get("url") or "").strip()
    style = (preset.get("api_style") or "openai").strip().lower()
    api_style = "ollama" if style == "ollama" else "openai"
    model = (preset.get("model") or "").strip()
    vm = (preset.get("vision_model") or "").strip()
    if target in ("text", "both"):
        if url:
            LOCAL_LLM_CHAT_URL = url[:2000]
        LOCAL_LLM_API_STYLE = api_style
        if model:
            LOCAL_LLM_MODEL = model[:200]
        LOCAL_LLM_PROVIDER = p
    if target in ("vision", "both"):
        if url:
            LOCAL_LLM_VISION_CHAT_URL = url[:2000]
        LOCAL_LLM_VISION_API_STYLE = api_style
        LOCAL_LLM_VISION_PROVIDER = p
        if fill_vision_if_empty or not (LOCAL_LLM_VISION_MODEL or "").strip():
            if vm:
                LOCAL_LLM_VISION_MODEL = vm[:200]
            elif p in ("deepseek", "lmstudio", "custom"):
                pass


def _llm_request_headers(
    *,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "User-Agent": "MumuPaster/1"}
    key = (api_key if api_key is not None else _llm_api_key()).strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    prov = (provider or LOCAL_LLM_PROVIDER or "").strip().lower()
    if prov == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/mumupaster"
        headers["X-Title"] = "MumuPaster"
    return headers


def _local_llm_runtime_ready() -> bool:
    if not LOCAL_LLM_CHAT_URL or not LOCAL_LLM_MODEL:
        return False
    if _llm_provider_needs_key() and not _llm_api_key():
        return False
    return True


def _local_llm_vision_ready() -> bool:
    if not _llm_effective_vision_url() or not (LOCAL_LLM_VISION_MODEL or "").strip():
        return False
    if _llm_vision_needs_key() and not _llm_vision_api_key():
        return False
    return True


def _effective_local_llm_inbox(body_or_ui_flag: bool) -> bool:
    return bool((LOCAL_LLM_ENABLED or body_or_ui_flag) and _local_llm_runtime_ready())


def _screen_assist_system_for_model(model: str) -> str:
    m = (model or "").strip().lower()
    if "moondream" in m:
        return LOCAL_LLM_SCREEN_ASSIST_SYSTEM_MOONDREAM
    return LOCAL_LLM_SCREEN_ASSIST_SYSTEM


def _extract_json_object_from_assistant(raw: str) -> Optional[dict]:
    s = (raw or "").strip()
    if not s:
        return None
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.I)
    if m:
        s = m.group(1).strip()
    i0 = s.find("{")
    i1 = s.rfind("}")
    if i0 < 0 or i1 <= i0:
        return None
    try:
        out = json.loads(s[i0 : i1 + 1])
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def _normalize_llm_intent(raw: object) -> Optional[str]:
    if raw is None:
        return None
    v = str(raw).strip().lower().replace("-", "_")
    if not v or v == "none":
        return None
    allowed = ("pickup_only", "negative", "question", "positive", "neutral")
    return v if v in allowed else None


_BUYER_WROTE_AS_SELLER_RE = re.compile(
    r"where should i send|when should i send|where (?:do|should) i ship|i(?:'|')?ll send (?:it|the item)|"
    r"send (?:it|the item) to you|your address (?:to|for) (?:ship|mail)|when (?:can|should) i mail",
    re.I,
)


def _coerce_llm_reply_text(item: object) -> str:
    """Только текст для чата — без dict/JSON обёрток от модели."""
    if item is None:
        return ""
    if isinstance(item, dict):
        for key in ("text", "message", "content", "reply", "body", "reply_text"):
            if key in item:
                got = _coerce_llm_reply_text(item.get(key))
                if got:
                    return got
        return ""
    if isinstance(item, (list, tuple)):
        parts = [_coerce_llm_reply_text(x) for x in item]
        return _collapse_ws(" ".join(p for p in parts if p)).strip()
    s = _collapse_ws(str(item)).strip()
    if not s:
        return ""
    if (s.startswith("{") and s.endswith("}")) or (
        s.startswith("{'") or s.startswith('{"')
    ):
        try:
            import ast

            parsed = ast.literal_eval(s)
            if isinstance(parsed, dict):
                got = _coerce_llm_reply_text(parsed)
                if got:
                    return got
        except (ValueError, SyntaxError, TypeError):
            pass
        m = re.search(
            r"""['"]text['"]\s*:\s*(['"])(.+?)\1""",
            s,
            re.I | re.S,
        )
        if m:
            return _collapse_ws(m.group(2)).strip()
    return s


def _sanitize_llm_reply_messages(
    msgs: List[str],
    *,
    buyer_address_on_profile: str = "",
    seller_message: str = "",
) -> List[str]:
    out: List[str] = []
    addr = _collapse_ws(buyer_address_on_profile or "").strip()
    for m in msgs:
        t = _coerce_llm_reply_text(m)[:420]
        if not t:
            continue
        low = t.lower()
        if low.startswith("http://") or low.startswith("https://"):
            continue
        if "http://" in low or "https://" in low:
            continue
        if _BUYER_WROTE_AS_SELLER_RE.search(t):
            continue
        fixed = _fix_reply_if_invented_location(t, addr)
        out.append(fixed)
        if len(out) >= 1:
            break
    out = _dedupe_inbox_outgoing_messages(out)
    if not out and addr and re.search(
        r"\b(address|zip|ship to|mail to|where.*ship|arizona|location|state)\b",
        (seller_message or ""),
        re.I,
    ):
        out.append(_shipping_address_reply(addr))
    return out


def _append_local_llm_trace(entry: dict) -> None:
    row = dict(entry)
    row.setdefault("ts", time.strftime("%H:%M:%S"))
    with _local_llm_trace_lock:
        _local_llm_traces.append(row)
        if len(_local_llm_traces) > _LOCAL_LLM_TRACE_CAP:
            del _local_llm_traces[: len(_local_llm_traces) - _LOCAL_LLM_TRACE_CAP]


def _sanitize_llm_chat_messages(msgs: object) -> Optional[List[dict]]:
    if not isinstance(msgs, list) or not msgs:
        return None
    out: List[dict] = []
    for m in msgs[-40:]:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            continue
        content = str(m.get("content") or "")
        if len(content) > 14000:
            content = content[:14000]
        out.append({"role": role, "content": content})
    return out if out else None


_last_local_llm_error: str = ""


def _local_llm_error_hint(err: str, *, vision: bool) -> str:
    low = (err or "").lower()
    if not vision:
        return ""
    if any(x in low for x in ("allocate", "cuda", "vram", "load model", "runner", "memory")):
        return (
            " Vision-модель не загрузилась (часто нехватка VRAM). "
            "Попробуйте moondream: `ollama pull moondream` и укажите в ⚙, или закройте другие GPU-процессы."
        )
    return ""


def _parse_llm_error_body(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        j = json.loads(s)
        if isinstance(j, dict):
            return str(j.get("error") or j.get("message") or s)[:800]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return s[:800]


def _messages_for_ollama_api(msgs: List[dict]) -> List[dict]:
    out: List[dict] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user").strip().lower()
        if role not in ("system", "user", "assistant"):
            continue
        d: Dict[str, Any] = {"role": role, "content": str(m.get("content") or "")}
        imgs = m.get("images")
        if isinstance(imgs, list) and imgs:
            d["images"] = [str(x) for x in imgs[:6] if str(x).strip()]
        out.append(d)
    return out


def _messages_for_openai_vision_api(msgs: List[dict]) -> List[dict]:
    out: List[dict] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user").strip().lower()
        if role not in ("system", "user", "assistant"):
            continue
        imgs = m.get("images")
        if isinstance(imgs, list) and imgs and role == "user":
            parts: List[dict] = [{"type": "text", "text": str(m.get("content") or "")}]
            for im in imgs[:4]:
                s = str(im).strip()
                if not s:
                    continue
                mime = "image/png"
                try:
                    dec = base64.b64decode(s, validate=False)
                    if dec.startswith(b"\xff\xd8"):
                        mime = "image/jpeg"
                except Exception:
                    pass
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{s}"}})
            out.append({"role": "user", "content": parts})
        else:
            out.append({"role": role, "content": str(m.get("content") or "")})
    return out


def local_llm_chat_with_messages(
    messages: List[dict],
    log_func: Callable[[str], None],
    *,
    skip_trace: bool = False,
    trace_kind: str = "llm",
    trace_meta: Optional[dict] = None,
    model_override: Optional[str] = None,
) -> Optional[str]:
    global _last_local_llm_error
    _last_local_llm_error = ""
    has_images = any(isinstance(m, dict) and m.get("images") for m in messages)
    if has_images:
        chat_url = _llm_effective_vision_url()
        api_style = _llm_effective_vision_api_style()
        model = (model_override or LOCAL_LLM_VISION_MODEL or "").strip()
        headers = _llm_request_headers(api_key=_llm_vision_api_key(), provider=_llm_vision_provider())
    else:
        chat_url = (LOCAL_LLM_CHAT_URL or "").strip()
        api_style = (LOCAL_LLM_API_STYLE or "ollama").strip().lower()
        model = (model_override or LOCAL_LLM_MODEL or "").strip()
        headers = _llm_request_headers()
    if not chat_url or not model:
        return None
    if not messages:
        return None
    if api_style in ("openai", "openai_compatible", "lmstudio"):
        api_msgs = _messages_for_openai_vision_api(messages) if has_images else messages
        payload: Dict[str, Any] = {
            "model": model,
            "messages": api_msgs,
            "temperature": 0.25,
            "max_tokens": 900 if trace_kind in ("coach_chat", "coach_chat_ru_retry") else 500,
        }
    else:
        api_msgs = _messages_for_ollama_api(messages)
        opts: Dict[str, Any] = {"temperature": 0.1 if has_images else 0.25}
        if trace_kind in ("coach_chat", "coach_chat_ru_retry"):
            opts["num_predict"] = 380
        elif has_images and trace_kind in ("screen_analyze_ui", "screen_recovery", "screen_assist"):
            opts["num_predict"] = 220
        payload = {
            "model": model,
            "messages": api_msgs,
            "stream": False,
            "options": opts,
        }
        if has_images and trace_kind in ("screen_analyze_ui", "screen_recovery", "screen_assist"):
            payload["format"] = "json"
    try:
        data = json.dumps(payload).encode("utf-8")
    except (TypeError, ValueError) as e:
        log_func(f"LLM: could not serialize request: {e}")
        return None
    req = urllib.request.Request(chat_url, data=data, headers=headers, method="POST")
    req_timeout = float(LOCAL_LLM_VISION_TIMEOUT_SEC if has_images else LOCAL_LLM_TIMEOUT_SEC)
    try:
        with urllib.request.urlopen(req, timeout=req_timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        j = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        err = _parse_llm_error_body(body) or str(e)
        err = err + _local_llm_error_hint(err, vision=bool(has_images))
        _last_local_llm_error = err[:900]
        log_func(f"LLM: HTTP {e.code}: {err[:240]}")
        if not skip_trace:
            _append_local_llm_trace(
                {"kind": trace_kind, "error": err[:800], "raw_model_reply": body[:2000], "meta": trace_meta or {}}
            )
        return None
    except Exception as e:
        err = str(e) + _local_llm_error_hint(str(e), vision=bool(has_images))
        _last_local_llm_error = err[:900]
        log_func(f"LLM: request failed: {err[:240]}")
        if not skip_trace:
            _append_local_llm_trace(
                {"kind": trace_kind, "error": err[:800], "raw_model_reply": "", "meta": trace_meta or {}}
            )
        return None
    if isinstance(j, dict) and j.get("error"):
        err = str(j["error"]) + _local_llm_error_hint(str(j["error"]), vision=bool(has_images))
        _last_local_llm_error = err[:900]
        log_func(f"LLM: API error: {err[:240]}")
        if not skip_trace:
            _append_local_llm_trace(
                {"kind": trace_kind, "error": err[:800], "raw_model_reply": "", "meta": trace_meta or {}}
            )
        return None
    if api_style in ("openai", "openai_compatible", "lmstudio"):
        content = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    else:
        content = (j.get("message") or {}).get("content") or ""
    text = str(content).strip()
    if not skip_trace:
        meta = dict(trace_meta or {})
        meta["n_messages"] = len(messages)
        meta["has_image"] = bool(has_images)
        meta["model"] = model[:120]
        _append_local_llm_trace(
            {
                "kind": trace_kind,
                "error": "",
                "raw_model_reply": (text or "")[:20000],
                "meta": meta,
            }
        )
    return text or None


def local_llm_chat_completion(
    system_prompt: str,
    user_prompt: str,
    log_func: Callable[[str], None],
) -> Optional[str]:
    return local_llm_chat_with_messages(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        log_func,
        skip_trace=True,
    )


def local_llm_offerup_inbox_decide(
    snippet: str,
    link_row: dict,
    row: dict,
    regex_kind: str,
    log_func: Callable[[str], None],
    buyer_name_on_profile: str = "",
    buyer_address_on_profile: str = "",
    use_listing_photo: bool = True,
    serial: Optional[str] = None,
    root: Optional[ET.Element] = None,
) -> Optional[dict]:
    img_b64: Optional[str] = None
    if use_listing_photo and LOCAL_LLM_USE_LISTING_IMAGE_INBOX and _local_llm_vision_ready():
        img_b64 = _listing_image_base64_for_llm(link_row)
        if img_b64:
            log_func("Inbox LLM: к объявлению прикреплено фото для vision-модели")
    user_payload = json.dumps(
        {
            "seller_message": snippet,
            "chat_name_hint": str(row.get("name") or ""),
            "listing_title": str(link_row.get("title") or link_row.get("name") or ""),
            "regex_classifier_hint": regex_kind,
            "buyer_name_on_profile": (buyer_name_on_profile or "").strip(),
            "buyer_address_on_profile": (buyer_address_on_profile or "").strip(),
            "listing_image_attached": bool(img_b64),
            "link_already_visible_in_chat": bool(
                serial and _offerup_chat_shows_buyer_sent_link(serial, link_row, root=root)
            ),
        },
        ensure_ascii=False,
    )
    system_full = (
        LOCAL_LLM_INBOX_SYSTEM_PROMPT
        + _LOCAL_LLM_INBOX_BUYER_ROLE_RULES
        + _LOCAL_LLM_INBOX_ADDRESS_RULES
        + _coach_training_rules_block()
    )
    if not (buyer_address_on_profile or "").strip():
        log_func("Inbox LLM: buyer_address_on_profile пуст — задайте «Адрес покупателя» в UI")
    if img_b64:
        system_full += _LOCAL_LLM_INBOX_IMAGE_RULES
    if img_b64 and (LOCAL_LLM_VISION_MODEL or "").strip():
        raw = local_llm_chat_with_messages(
            [
                {"role": "system", "content": system_full},
                {"role": "user", "content": user_payload, "images": [img_b64]},
            ],
            log_func,
            skip_trace=True,
            model_override=LOCAL_LLM_VISION_MODEL,
        )
    else:
        raw = local_llm_chat_completion(system_full, user_payload, log_func)
    obj = _extract_json_object_from_assistant(raw or "") if raw else None
    _append_local_llm_trace(
        {
            "kind": "inbox_llm",
            "seller_message": (snippet or "")[:1200],
            "regex_kind": regex_kind,
            "listing_title": str(link_row.get("title") or link_row.get("name") or "")[:300],
            "buyer_address_set": bool((buyer_address_on_profile or "").strip()),
            "listing_image": bool(img_b64),
            "raw_model_reply": (raw or "")[:20000],
            "parsed_decision": obj,
            "parse_ok": obj is not None,
            "error": "" if obj else ("no_json" if raw else "no_response"),
        }
    )
    if not raw:
        return None
    if not obj:
        log_func("Inbox LLM: could not parse JSON from model output")
        return None
    obj = _normalize_llm_inbox_decision(obj)
    log_func(
        "Inbox LLM: "
        + json.dumps(
            {
                "intent": obj.get("intent"),
                "send_link": obj.get("send_link"),
                "ask_operator": obj.get("ask_operator"),
                "n_replies": len(obj.get("text_replies") or []),
            },
            ensure_ascii=False,
        )
    )
    return obj


def _llm_decision_text_replies(decision: Optional[dict]) -> List[str]:
    if not isinstance(decision, dict):
        return []
    out: List[str] = []
    for key in ("text_replies", "replies", "messages"):
        raw = decision.get(key)
        if isinstance(raw, list):
            for x in raw:
                t = _coerce_llm_reply_text(x)
                if t:
                    out.append(t)
            if out:
                return out
    for key in ("reply_text", "message", "text"):
        v = decision.get(key)
        t = _coerce_llm_reply_text(v)
        if t:
            return [t]
    return []


def _normalize_llm_inbox_decision(obj: Optional[dict]) -> Optional[dict]:
    if not isinstance(obj, dict):
        return obj
    norm = dict(obj)
    raw_tr = obj.get("text_replies")
    if isinstance(raw_tr, list):
        cleaned = [_coerce_llm_reply_text(x) for x in raw_tr]
        norm["text_replies"] = [x for x in cleaned if x]
    return norm


def _dedupe_inbox_outgoing_messages(msgs: List[str]) -> List[str]:
    """Один и тот же текст не отправляем дважды подряд (LLM иногда дублирует)."""
    out: List[str] = []
    seen: set = set()
    for m in msgs:
        t = _collapse_ws(m or "")
        if not t:
            continue
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _send_inbox_text_messages(msgs: List[str], serial: Optional[str], log_func: Callable[[str], None]) -> bool:
    msgs = _dedupe_inbox_outgoing_messages(msgs)
    if not msgs:
        return False
    for idx, m in enumerate(msgs):
        if not send_text_to_current_chat(m, serial=serial, log_func=log_func):
            log_func(f"Inbox scan: failed to send LLM text message {idx + 1}/{len(msgs)}")
            return False
        if idx < len(msgs) - 1:
            time.sleep(max(BETWEEN_MESSAGES_SEC, 0.12))
    return True


_ALERT_REASON_RU = {
    "negative_reply": "Продавец отказал или негативный ответ",
    "llm_asks_operator": "ИИ просит подсказку оператора",
    "could_not_answer_question": "Не удалось ответить на вопрос",
    "could_not_send_fish_link": "Не удалось отправить ссылку",
    "local_llm_hold_link": "ИИ: ссылку пока не отправлять",
    "meet_fallback_failed": "Не удалось отправить meet fallback",
    "pickup_only_shipping_requested": "Только самовывоз — запрошена доставка",
}


def add_offerup_alert(item: Optional[dict], ad_url: str, link_row: Optional[dict], reason: str, reply_text: str, port: Optional[str]) -> dict:
    global _offerup_alerts_next_id
    lq = link_row or {}
    name = lq.get("name") or (_first_item_text(item, ["seller", "seller_name", "username", "title", "name"]) if item else "OfferUp")
    reason_ru = _ALERT_REASON_RU.get(reason, reason)
    append_coach_feed_from_inbox(
        f"⚠ [{name}] {reason_ru}"
        + (f" — «{str(reply_text or '')[:100]}»" if reply_text else "")
    )
    with _offerup_alerts_lock:
        row = {
            "id": _offerup_alerts_next_id,
            "created_at": time.strftime("%H:%M:%S"),
            "name": name or "OfferUp",
            "reason": reason,
            "reply_text": reply_text,
            "fish_link": lq.get("fish_link", ""),
            "search_link": lq.get("search_link", ""),
            "search_id": lq.get("search_id", ""),
            "ad_url": ad_url,
            "port": port or "",
        }
        _offerup_alerts_next_id += 1
        _offerup_alerts.append(row)
        if len(_offerup_alerts) > 100:
            del _offerup_alerts[:-100]
    save_persistent_state()
    return row


def offerup_auto_wait_and_send_link(
    item: Optional[dict],
    ad_url: str,
    link_row: Optional[dict],
    serial: Optional[str],
    log_func: Callable[[str], None],
    should_stop: Optional[Callable[[], bool]] = None,
) -> str:
    if not link_row or not (link_row.get("fish_link") or link_row.get("url")):
        log_func("Auto-reply: no fish_link, skipping")
        return "no_link"
    cid0 = int(link_row.get("created_link_id") or 0)
    if cid0 and cid0 in _pending_created_sent_ids():
        log_func("Auto-reply: link already marked sent, skipping")
        return "no_link"
    time.sleep(1.2)
    baseline = _text_fingerprint(_ui_visible_texts(dump_ui_serial(serial) if serial else dump_ui()))
    log_func(f"Auto-reply: waiting for seller reply up to {int(OFFERUP_REPLY_WAIT_SEC)}s")
    kind, reply = wait_for_offerup_reply(serial, baseline, should_stop=should_stop)
    if kind == "pickup_only":
        log_func("Auto-reply: pickup-only detected, asking for shipping and creating alert")
        if not send_text_to_current_chat(PICKUP_ONLY_RESPONSE, serial=serial):
            add_offerup_alert(item, ad_url, link_row, "could_not_answer_pickup_only", reply, serial)
            return "alert"
        add_offerup_alert(item, ad_url, link_row, "pickup_only_shipping_requested", reply, serial)
        return "alert"
    if kind == "question":
        log_func("Auto-reply: question detected, generic reply (not «first photo»)")
        if not send_text_to_current_chat(OFFERUP_INBOX_GENERIC_QUESTION_FALLBACK, serial=serial):
            add_offerup_alert(item, ad_url, link_row, "could_not_answer_question", reply, serial)
            return "alert"
        time.sleep(1.0)
        baseline = _text_fingerprint(_ui_visible_texts(dump_ui_serial(serial) if serial else dump_ui()))
        kind, reply = wait_for_offerup_reply(serial, baseline, timeout_sec=OFFERUP_REPLY_WAIT_SEC, should_stop=should_stop)
    if kind == "negative":
        log_func("Auto-reply: negative reply detected, link was NOT sent: " + reply[:120])
        add_offerup_alert(item, ad_url, link_row, "negative_reply", reply, serial)
        return "alert"
    if kind in ("positive", "neutral"):
        if _snippet_asks_how_it_works(reply):
            if send_text_to_current_chat(OFFERUP_HOW_IT_WORKS_REPLY, serial=serial):
                log_func("Auto-reply: ответ «как это работает» (Iinк)")
                return "answered_question"
            add_offerup_alert(item, ad_url, link_row, "could_not_send_how_it_works", reply, serial)
            return "alert"
        if _offerup_chat_shows_buyer_sent_link(serial, link_row):
            log_func("Auto-reply: ссылка уже видна в чате — помечаем sent, не дублируем")
            mark_created_link_sent(link_row.get("created_link_id"))
            return "sent"
        link = link_row.get("fish_link") or link_row.get("url")
        if send_text_to_current_chat(str(link), serial=serial):
            mark_created_link_sent(link_row.get("created_link_id"))
            log_func("Auto-reply: fish_link sent")
            return "sent"
        add_offerup_alert(item, ad_url, link_row, "could_not_send_fish_link", reply, serial)
        return "alert"
    if kind == "stopped":
        return "stopped"
    log_func("Auto-reply: no seller reply, fish_link left in created links")
    return "timeout"


def _offerup_screen_wh(serial: Optional[str], root: Optional[ET.Element] = None) -> Tuple[int, int]:
    port = (serial or ADB_PORT or "").strip()
    sw, sh = _adb_wm_size_wh(port) if port else (0, 0)
    if (not sw or not sh) and root is not None:
        sw, sh = screen_size(root)
    return int(sw or 0), int(sh or 0)


def _offerup_in_post_nav_zone(x: int, y: int, sw: int, sh: int) -> bool:
    if sw <= 0 or sh <= 0:
        return False
    if y < int(sh * OFFERUP_NAV_BAR_Y_FRAC):
        return False
    xf = x / float(sw)
    return OFFERUP_POST_NAV_X_MIN_FRAC <= xf <= OFFERUP_POST_NAV_X_MAX_FRAC


def _offerup_in_home_nav_zone(x: int, y: int, sw: int, sh: int) -> bool:
    if sw <= 0 or sh <= 0:
        return False
    if y < int(sh * OFFERUP_NAV_BAR_Y_FRAC):
        return False
    return (x / float(sw)) <= OFFERUP_HOME_NAV_X_MAX_FRAC


def _offerup_in_inbox_nav_zone(x: int, y: int, sw: int, sh: int) -> bool:
    if sw <= 0 or sh <= 0:
        return False
    if y < int(sh * OFFERUP_NAV_BAR_Y_FRAC):
        return False
    xf = x / float(sw)
    return OFFERUP_INBOX_NAV_X_MIN_FRAC <= xf <= OFFERUP_INBOX_NAV_X_MAX_FRAC


def _offerup_tap_xy(
    serial: Optional[str],
    xy: Tuple[int, int],
    *,
    allow_bottom_nav: bool = False,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    """Тап с блокировкой нижней панели (Post). allow_bottom_nav=True — только для кнопки Inbox."""
    x, y = int(xy[0]), int(xy[1])
    sw, sh = _offerup_screen_wh(serial)
    if sw and sh:
        if _offerup_in_home_nav_zone(x, y, sw, sh) and not allow_bottom_nav:
            if log_func:
                log_func(f"Inbox scan: tap blocked — зона Home ({x},{y})")
            return False
        if _offerup_in_post_nav_zone(x, y, sw, sh) and not allow_bottom_nav:
            if log_func:
                log_func(f"Inbox scan: tap blocked — зона Post ({x},{y}), не Inbox")
            return False
        if y >= int(sh * OFFERUP_NAV_BAR_Y_FRAC) and not allow_bottom_nav:
            if log_func:
                log_func(f"Inbox scan: tap blocked — нижняя навигация ({x},{y})")
            return False
    if serial:
        run_adb_serial(serial, ["shell", "input", "tap", str(x), str(y)])
        invalidate_ui_dump_cache(serial)
    else:
        tap((x, y))
    return True


def _offerup_ui_is_post_item_compose(texts_joined: str) -> bool:
    low = (texts_joined or "").lower()
    return "post an item" in low and ("add photos" in low or "add photo" in low or "title" in low)


def _offerup_find_top_left_close(root: ET.Element) -> Optional[Tuple[int, int]]:
    """Крестик закрытия «Post an item» и похожих полноэкранных форм (верхний левый угол)."""
    scr_w, scr_h = _offerup_screen_wh(None, root)
    if not scr_w or not scr_h:
        scr_w, scr_h = screen_size(root)
    candidates: List[Tuple[int, int, int]] = []
    for node in root.iter("node"):
        if node.get("clickable", "false") != "true":
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        l, t, r, b = pr
        cx, cy = (l + r) // 2, (t + b) // 2
        if scr_w and cx > scr_w * 0.28:
            continue
        if scr_h and cy > scr_h * 0.22:
            continue
        text = (node.get("text") or "").strip()
        cdesc = (node.get("content-desc") or "").lower()
        rid = (node.get("resource-id") or "").lower()
        score = 0
        if text in ("×", "✕", "✗", "X", "x") or _POPUP_DISMISS_SINGLE_CHAR_RE.match(text):
            score += 300
        if "close" in cdesc or "close" in rid or "navigate up" in cdesc:
            score += 180
        w, h = r - l, b - t
        if 20 <= w <= 120 and 20 <= h <= 120:
            score += 60
        if score >= 60:
            candidates.append((score, cx, cy))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1], candidates[0][2]


def _offerup_dismiss_post_item_if_open(serial: Optional[str], log_func: Callable[[str], None]) -> bool:
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return False
    texts = " ".join(_ui_visible_texts(root))
    if not _offerup_ui_is_post_item_compose(texts):
        return False
    xy = _offerup_find_top_left_close(root)
    if xy:
        log_func(f"Inbox scan: закрываю «Post an item» (крестик {xy})")
        _offerup_tap_xy(serial, xy, log_func=log_func)
        time.sleep(0.4)
        return True
    log_func("Inbox scan: «Post an item» — BACK")
    _offerup_keyevent(serial, "4")
    time.sleep(0.35)
    return True


def _offerup_keyevent(serial: Optional[str], key: str) -> None:
    if serial:
        run_adb_serial(serial, ["shell", "input", "keyevent", str(key)])
    else:
        run_adb(["shell", "input", "keyevent", str(key)])


def _offerup_launch_app(serial: Optional[str], log_func: Callable[[str], None]) -> bool:
    """Перезапуск OfferUp: force-stop + monkey (стабильный способ на MuMu)."""
    if serial:
        adb_connect_serial(serial)
        run_adb_serial(serial, ["shell", "input", "keyevent", "224"])
        run_adb_serial(serial, ["shell", "am", "force-stop", "com.offerup"])
        time.sleep(0.2)
        r = run_adb_serial(
            serial,
            ["shell", "monkey", "-p", "com.offerup", "-c", "android.intent.category.LAUNCHER", "1"],
        )
    else:
        adb_connect()
        run_adb(["shell", "input", "keyevent", "224"])
        run_adb(["shell", "am", "force-stop", "com.offerup"])
        time.sleep(0.2)
        r = run_adb(
            ["shell", "monkey", "-p", "com.offerup", "-c", "android.intent.category.LAUNCHER", "1"],
        )
    ok = bool(r and r.returncode == 0)
    log_func("Inbox scan: OfferUp restarted" if ok else "Inbox scan: could not launch OfferUp")
    return ok


def _offerup_find_inbox_tab(root: ET.Element) -> Optional[Tuple[int, int]]:
    scr_w, scr_h = _offerup_screen_wh(None, root)
    xy = _offerup_find_tap_by_resource_id(
        root,
        "tab-bar-widget.tab.inbox.touchable-opacity",
        "tab.inbox.touchable-opacity",
        scr_w=scr_w,
        scr_h=scr_h,
        y_min_frac=0.84,
        y_max_frac=1.0,
        package_hint="com.offerup",
    )
    if xy and scr_w and scr_h and _offerup_in_inbox_nav_zone(xy[0], xy[1], scr_w, scr_h):
        return xy

    def pred(n: ET.Element) -> bool:
        label = _offerup_label(n).strip().lower()
        return "inbox" in label
    xy = _offerup_find_clickable(root, pred)
    if xy and scr_w and scr_h and _offerup_in_inbox_nav_zone(xy[0], xy[1], scr_w, scr_h):
        return xy
    return None


def _offerup_tap_bottom_inbox_nav(serial: Optional[str], log_func: Callable[[str], None]) -> None:
    """Inbox — только по тексту/UI, без фиксированных координат."""
    if offerup_open_inbox_tab(serial, log_func):
        return
    log_func("Inbox scan: вкладка Inbox не найдена в UI")
    notify_operator_help(serial, "нажмите вкладку Inbox в нижней панели OfferUp")


def _offerup_open_inbox(
    serial: Optional[str],
    log_func: Callable[[str], None],
    should_stop: Optional[Callable[[], bool]] = None,
    open_wait: float = OFFERUP_INBOX_OPEN_WAIT_SEC,
    *,
    restart_app: bool = True,
    prefetched_root: Optional[ET.Element] = None,
) -> bool:
    if serial:
        adb_connect_serial(serial)
    else:
        adb_connect()

    t_total = time.monotonic()
    root: Optional[ET.Element] = None

    if prefetched_root is not None and _offerup_ui_has_bottom_nav(prefetched_root):
        root = prefetched_root
    elif restart_app:
        fg = _adb_foreground_package(serial)
        if _offerup_foreground_is_offerup(serial):
            log_func("Inbox scan: OfferUp на экране — один dump после паузы")
            root = _offerup_dump_nav_after_settle(serial, log_func, should_stop=should_stop)
            if root is None:
                log_func("Inbox scan: панель не видна — force-stop и запуск")
                if not _offerup_launch_app(serial, log_func):
                    return False
                root = _offerup_wait_bottom_nav_ready(
                    serial,
                    log_func,
                    timeout=OFFERUP_INBOX_NAV_READY_SEC,
                    should_stop=should_stop,
                    launching=True,
                )
        else:
            log_func(
                f"Inbox scan: OfferUp не в фокусе ({fg or 'неизвестно'}) — запуск приложения"
            )
            if not _offerup_launch_app(serial, log_func):
                return False
            root = _offerup_wait_bottom_nav_ready(
                serial,
                log_func,
                timeout=OFFERUP_INBOX_NAV_READY_SEC,
                should_stop=should_stop,
                launching=True,
            )
    else:
        log_func("Inbox scan: OfferUp на экране — без force-stop")
        root = _offerup_dump_nav_after_settle(serial, log_func, should_stop=should_stop)
        if root is None:
            if not _offerup_launch_app(serial, log_func):
                return False
            root = _offerup_wait_bottom_nav_ready(
                serial,
                log_func,
                timeout=OFFERUP_INBOX_NAV_READY_SEC,
                should_stop=should_stop,
                launching=True,
            )

    if root is None:
        log_func("Inbox scan: UI OfferUp недоступен (нет нижней панели)")
        return False

    t_tap = time.monotonic()
    _offerup_dismiss_blockers_from_root(serial, root, log_func)
    if not _offerup_tap_inbox_tab_from_root(serial, root, log_func):
        offerup_open_inbox_tab(serial, log_func, root=root)
    log_func(f"Inbox scan: тап Inbox за {time.monotonic() - t_tap:.1f}s")

    time.sleep(max(0.25, min(float(open_wait), 0.45)))
    root_chk = dump_ui_serial(serial) if serial else dump_ui()
    texts_joined = " ".join(_ui_visible_texts(root_chk)) if root_chk is not None else ""
    if _offerup_ui_looks_like_inbox_open(root_chk, texts_joined):
        log_func(
            f"Inbox scan: inbox list open (всего {time.monotonic() - t_total:.1f}s)"
        )
        time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
        return True
    log_func("Inbox scan: inbox list not detected after open wait")
    return False


def _offerup_offerup_is_foreground(
    root: Optional[ET.Element],
    serial: Optional[str] = None,
) -> bool:
    """OfferUp на экране: сначала dumpsys, затем dump (панель или пакет com.offerup)."""
    if serial and not _offerup_foreground_is_offerup(serial):
        return False
    if root is None:
        return bool(serial and _offerup_foreground_is_offerup(serial))
    if _offerup_ui_has_bottom_nav(root):
        return True
    return any((n.get("package") or "").lower() == "com.offerup" for n in root.iter("node"))


def _offerup_try_open_inbox_without_restart(
    serial: Optional[str],
    log_func: Callable[[str], None],
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    """OfferUp уже запущен — без force-stop; из чата выходим в список Inbox."""
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return False
    if not _offerup_offerup_is_foreground(root, serial=serial):
        return False
    if _offerup_ensure_inbox_list_before_scan(serial, log_func, should_stop=should_stop):
        log_func("Inbox scan: список Inbox уже на экране")
        return True
    return _offerup_open_inbox(
        serial,
        log_func,
        should_stop=should_stop,
        open_wait=OFFERUP_INBOX_OPEN_WAIT_SEC,
        restart_app=False,
    )


def _offerup_chat_has_message_compose(root: Optional[ET.Element]) -> bool:
    if root is None:
        return False
    if find_edit_and_send(root) is not None:
        return True
    for node in root.iter("node"):
        cls = (node.get("class") or "").lower()
        if "edittext" not in cls:
            continue
        hint = _collapse_ws((node.get("text") or "") + " " + (node.get("content-desc") or "")).lower()
        if "message" in hint or hint in ("", "type a message"):
            return True
    return False


def _offerup_chat_exit_reason(root: Optional[ET.Element], texts_joined: str) -> Optional[str]:
    """Причина выйти из чата стрелкой: Item sold или нет поля ввода."""
    low = (texts_joined or "").lower()
    if "item sold" in low or "this item has been sold" in low:
        return "item_sold"
    if "search for similar items" in low and "sold" in low:
        return "item_sold"
    if root is None:
        return None
    in_chat = (
        low.count("message") >= 1
        or "navigate up" in low
        or ("back" in low and "inbox" not in low)
        or "safety tip" in low
    )
    if in_chat and not _offerup_chat_has_message_compose(root):
        return "no_compose"
    return None


def _offerup_tap_chat_back(
    serial: Optional[str],
    log_func: Callable[[str], None],
    root: Optional[ET.Element] = None,
) -> bool:
    """Назад из чата → лента Inbox. root=уже полученный dump (без лишнего uiautomator)."""
    root_use = root
    if root_use is None:
        root_use = dump_ui_serial(serial) if serial else dump_ui()
    if root_use is not None:
        xy = _offerup_find_chat_back_xy(root_use)
        if xy:
            log_func(f"Inbox scan: tap ← CloseButton {xy}")
            if serial:
                run_adb_serial(serial, ["shell", "input", "tap", str(xy[0]), str(xy[1])])
            else:
                tap(xy)
            time.sleep(0.22)
            return True
    log_func("Inbox scan: CloseButton не в dump — keyevent BACK")
    _offerup_keyevent(serial, "4")
    time.sleep(0.25)
    return False


def _offerup_try_vision_screen_recovery(
    serial: Optional[str],
    context: str,
    log_func: Callable[[str], None],
) -> None:
    """Скриншот + vision-модель: перезапуск OfferUp / снятие попапа при рассинхроне UI и логов."""
    if not _local_llm_vision_ready():
        log_func("screen assist: задайте модель зрения (форма «Локальная ИИ») и URL API")
        return
    b64 = adb_screencap_png_base64(serial)
    if not b64:
        log_func("screen assist: не удалось снять экран (adb screencap)")
        return
    ctx = (context or "")[:1400]
    user_txt = "Контекст автоматизации (лог/ошибка):\n" + ctx
    vision_model = (LOCAL_LLM_VISION_MODEL or "").strip()
    msgs = [
        {"role": "system", "content": _screen_assist_system_for_model(vision_model)},
        {"role": "user", "content": user_txt + "\n\nОтветь ТОЛЬКО JSON.", "images": [b64]},
    ]
    raw = local_llm_chat_with_messages(
        msgs,
        log_func,
        skip_trace=False,
        trace_kind="screen_recovery",
        trace_meta={"context": ctx[:500]},
        model_override=LOCAL_LLM_VISION_MODEL,
    )
    if not raw:
        log_func("screen assist: пустой ответ vision-модели")
        return
    obj = _extract_json_object_from_assistant(raw)
    if not obj:
        log_func("screen assist: в ответе модели нет JSON")
        return
    snap = {
        "situation": obj.get("situation"),
        "offerup_missing_or_background": obj.get("offerup_missing_or_background"),
        "recovery_actions": obj.get("recovery_actions"),
        "note": (str(obj.get("note") or "")[:160]),
    }
    log_func("screen assist: " + json.dumps(snap, ensure_ascii=False))
    actions = obj.get("recovery_actions") or []
    if not isinstance(actions, list):
        actions = []
    acts_str = [str(a) for a in actions]
    if bool(obj.get("offerup_missing_or_background")) and not any(
        "launch" in str(a).lower() or "offerup" in str(a).lower() for a in acts_str
    ):
        acts_str.append("launch_offerup_app")
    elif _situation_offerup_open_not_inbox(obj.get("situation")) and not any(
        "inbox" in str(a).lower() for a in acts_str
    ):
        acts_str = [a for a in acts_str if str(a).lower() not in ("none", "launch_offerup_app")]
        acts_str.append("open_inbox")
    if LOCAL_LLM_SCREEN_ASSIST_AUTO:
        if _execute_recovery_actions(acts_str, serial, log_func):
            append_coach_feed_from_inbox("Screen assist: действия выполнены автоматически.")
        else:
            log_func("screen assist: действия не выполнялись (recovery_actions / none)")
        return
    rid, created = _queue_pending_screen_recovery(
        serial,
        context=ctx,
        situation=obj.get("situation"),
        note=str(obj.get("note") or ""),
        recovery_actions=acts_str,
        screen_preview_b64=b64,
    )
    if rid:
        note_h = str(obj.get("note") or "")[:120]
        if created:
            append_coach_feed_from_inbox(
                f"ИИ: нужны действия на экране ({serial or ADB_PORT}): {', '.join(acts_str)}. "
                f"Подтвердите во вкладке «ИИ-коуч». {note_h}"
            )
        log_func(f"screen assist: ожидает подтверждения оператора (id={rid})")
    else:
        log_func("screen assist: действия не требуют подтверждения (none)")


def _looks_like_inbox_noise(s: str) -> bool:
    low = s.strip().lower()
    if not low:
        return True
    noise = {
        "inbox", "home", "post", "listings", "account", "try for free", "for sale", "services", "jobs",
        "get faster responses.", "get faster responses", "premium", "notifications", "search offerup",
        "back", "message", "messages", "send", "delivered", "read", "navigate up", "more options",
        "options", "type a message", "safety tip", "active",
    }
    if low in noise:
        return True
    if low.startswith("navigate ") or low.startswith("you:"):
        return True
    if "start for free" in low or "early item access" in low or "faster responses" in low:
        return True
    return False


def _offerup_ui_is_discussion_chat(root: Optional[ET.Element]) -> bool:
    """Экран переписки OfferUp (не лента Inbox) — по resource-id чата."""
    if root is None:
        return False
    for node in root.iter("node"):
        rid = _offerup_resource_id(node).lower()
        if not rid:
            continue
        if any(
            tok in rid
            for tok in (
                "discussionnavigationheader",
                "discussionscreen",
                "discussionfooter",
                "discussionprofileheader",
            )
        ):
            return True
    return _offerup_chat_has_message_compose(root)


def _offerup_find_chat_back_xy(root: ET.Element) -> Optional[Tuple[int, int]]:
    """Кнопка «назад» в шапке чата (uidumps: DiscussionNavigationHeader.CloseButton)."""
    xy = _offerup_find_tap_by_resource_id(
        root,
        "DiscussionNavigationHeader.CloseButton",
        "DiscussionNavigationHeader.CloseButtonAndroid",
        y_max_frac=0.22,
        package_hint="com.offerup",
    )
    if xy:
        return xy
    for node in root.iter("node"):
        if node.get("clickable", "false") != "true":
            continue
        cd = (node.get("content-desc") or "").strip().lower()
        txt = (node.get("text") or "").strip().lower()
        label = cd or txt
        if label in ("navigate up", "back") or label.startswith("back to"):
            pr = parse_bounds(node.get("bounds") or "")
            if pr:
                l, t, r, b = pr
                return (l + r) // 2, (t + b) // 2
    scr_w, scr_h = screen_size(root)
    for node in root.iter("node"):
        if "ImageButton" not in (node.get("class") or ""):
            continue
        pr_ib = parse_bounds(node.get("bounds") or "")
        if not pr_ib:
            continue
        l, t, r, b = pr_ib
        cx, cy = (l + r) // 2, (t + b) // 2
        if cx < (scr_w * 0.18 if scr_w else 160) and cy < (scr_h * 0.12 if scr_h else 180):
            return cx, cy
    return None


def _offerup_ui_is_chat_screen(root: Optional[ET.Element], texts_joined: str) -> bool:
    """Открыт диалог (не список Inbox) — поле ввода + нет ленты переписок."""
    if root is None:
        return False
    if _offerup_ui_is_discussion_chat(root):
        return True
    if not _offerup_chat_has_message_compose(root):
        return False
    low = (texts_joined or "").lower()
    rows = _offerup_collect_inbox_rows(root)
    if rows:
        return False
    if _offerup_infer_chat_name(root, ""):
        return True
    if "type a message" in low or "safety tip" in low:
        return True
    return bool(re.search(r"\b(delivered|message sent|read)\b", low))


def _offerup_ui_looks_like_inbox_list_screen(root: Optional[ET.Element], texts_joined: str) -> bool:
    if root is None:
        return False
    if _offerup_ui_is_discussion_chat(root):
        return False
    if _offerup_ui_is_chat_screen(root, texts_joined):
        return False
    # Надёжная проверка через resource-id (из диагностики OfferUp)
    for node in root.iter("node"):
        rid = (node.get("resource-id") or "").lower()
        if "inboxheader" in rid or "inbox-screen" in rid or "messages-tab" in rid:
            return True
        if "tab.inbox" in rid:
            return True
    rows = _offerup_collect_inbox_rows(root)
    if rows:
        return True
    low = (texts_joined or "").lower()
    if "inbox" in low and "you:" in low:
        return True
    if "inbox" in low and re.search(
        r"\b(\d+\s*(minute|minutes|min|hour|hours|day|days)\s+ago|an hour ago|yesterday|today)\b",
        low,
    ):
        return True
    return False


def _offerup_ensure_inbox_list_before_scan(
    serial: Optional[str],
    log_func: Callable[[str], None],
    should_stop: Optional[Callable[[], bool]] = None,
) -> bool:
    """Список Inbox на экране: из чата — назад, затем вкладка Inbox."""
    if should_stop and should_stop():
        return False
    for _ in range(3):
        root = dump_ui_serial(serial) if serial else dump_ui()
        texts_joined = " ".join(_ui_visible_texts(root)) if root is not None else ""
        if _offerup_ui_looks_like_inbox_list_screen(root, texts_joined):
            return True
        if root is not None and (
            _offerup_ui_is_discussion_chat(root) or _offerup_ui_is_chat_screen(root, texts_joined)
        ):
            log_func("Inbox scan: экран чата — возврат к списку Inbox")
            _offerup_tap_chat_back(serial, log_func, root=root)
            time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
            continue
        _offerup_dismiss_if_popup(serial)
        _offerup_dismiss_post_item_if_open(serial, log_func)
        offerup_open_inbox_tab(serial, log_func)
        time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
    return False


def _looks_like_inbox_time(s: str) -> bool:
    low = s.strip().lower()
    return bool(
        re.search(
            r"\b(\d+\s*(minute|minutes|min|hour|hours|hr|hrs|day|days|m|h|d)\s*(ago)?|"
            r"an hour ago|yesterday|today|just now|now|am|pm)\b",
            low,
        )
    )


def _offerup_inbox_list_nodes(root: ET.Element) -> Tuple[List[dict], int, int]:
    scr_w = scr_h = 0
    nodes: List[dict] = []
    for node in root.iter("node"):
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            _, _, r, b = pr
            scr_w, scr_h = max(scr_w, r), max(scr_h, b)
        txt = _collapse_ws(node.get("text") or node.get("content-desc") or "")
        if not txt or _looks_like_inbox_noise(txt):
            continue
        if not pr:
            continue
        l, t, r, b = pr
        cy = (t + b) // 2
        nav_cut = int(scr_h * OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC) if scr_h else 9999
        top_cut = OFFERUP_INBOX_LIST_TOP_Y_MIN
        if scr_h and (cy < top_cut or cy > nav_cut):
            continue
        nodes.append({"text": txt, "bounds": pr, "cx": (l + r) // 2, "cy": cy})
    nodes.sort(key=lambda x: (x["cy"], x["cx"]))
    return nodes, scr_w, scr_h


# ── Парсер Inbox через resource-id (надёжный метод) ─────────

_INBOX_MSG_TITLE_RE = re.compile(r"messages?-tab\.message-(\d+)\.title$", re.I)
_INBOX_MSG_CONTENT_RE = re.compile(r"messages?-tab\.message-(\d+)\.content$", re.I)
_INBOX_MSG_ROW_RE = re.compile(r"messages?-tab\.message-(\d+)$", re.I)
_INBOX_ROW_BUTTON_RE = re.compile(r"messages?-tab\.message-(\d+)$|ucl\.touchable-opacity$", re.I)


def _offerup_parse_inbox_rows_by_resource_id(root: ET.Element) -> List[dict]:
    """
    Надёжный парсер Inbox через resource-id OfferUp.
    Структура каждой строки (из диагностики):
      - message-N.title    → имя продавца
      - message-N.content  → текст сообщения (может начинаться с "You:")
      - message-N.date     → время
      - кнопка ucl.touchable-opacity → вся строка (для тапа), content_desc содержит всё
    """
    scr_w = scr_h = 0
    for node in root.iter("node"):
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            _, _, r, b = pr
            scr_w, scr_h = max(scr_w, r), max(scr_h, b)

    # Собираем по индексу сообщения
    by_idx: Dict[int, dict] = {}

    for node in root.iter("node"):
        rid = (node.get("resource-id") or "").strip()
        if not rid:
            continue

        # title (имя продавца)
        m = _INBOX_MSG_TITLE_RE.search(rid)
        if m:
            idx = int(m.group(1))
            txt = _collapse_ws(node.get("text") or "")
            if txt:
                by_idx.setdefault(idx, {})["name"] = txt
                pr = parse_bounds(node.get("bounds") or "")
                if pr:
                    l, t, r, b = pr
                    by_idx[idx]["title_cy"] = (t + b) // 2
            continue

        # content (текст сообщения)
        m = _INBOX_MSG_CONTENT_RE.search(rid)
        if m:
            idx = int(m.group(1))
            txt = _collapse_ws(node.get("text") or "")
            if txt:
                by_idx.setdefault(idx, {})["content"] = txt
                pr = parse_bounds(node.get("bounds") or "")
                if pr:
                    l, t, r, b = pr
                    by_idx[idx]["content_cy"] = (t + b) // 2
                    by_idx[idx]["content_cx"] = (l + r) // 2
            continue

        # кнопка-строка (для тапа) — ucl.touchable-opacity с content_desc
        # resource-id может быть просто "ucl.touchable-opacity", берём по порядку через cy
        if rid.endswith("ucl.touchable-opacity"):
            pr = parse_bounds(node.get("bounds") or "")
            if not pr:
                continue
            l, t, r, b = pr
            cy = (t + b) // 2
            if scr_h and (cy < OFFERUP_INBOX_LIST_TOP_Y_MIN or cy > int(scr_h * OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC)):
                continue
            cdesc = _collapse_ws(node.get("content-desc") or "")
            # Тап по центру левой части строки (аватар + имя), но не слишком правый
            btn_cx = max(l + 60, min((l + r) // 3, (l + r) // 2))
            # Ищем к какому сообщению относится эта кнопка (по ближайшему cy)
            best_idx = None
            best_dist = 9999
            for idx, info in by_idx.items():
                title_cy = info.get("title_cy", 0)
                if title_cy and abs(title_cy - cy) < best_dist:
                    best_dist = abs(title_cy - cy)
                    best_idx = idx
            if best_idx is not None and best_dist < 300:
                by_idx[best_idx]["tap_cy"] = cy
                by_idx[best_idx]["tap_cx"] = btn_cx
                if cdesc and not by_idx[best_idx].get("cdesc"):
                    by_idx[best_idx]["cdesc"] = cdesc
                # Если .content нода не дала snippet — пробуем извлечь из content_desc
                # Формат: "Navigate to view your profile image, ИМЯ, ВРЕМЯ, ТЕКСТ"
                if cdesc and not by_idx[best_idx].get("content"):
                    parts = [p.strip() for p in cdesc.split(",")]
                    # Пропускаем "Navigate to view your profile image", имя, время
                    skip_done = 0
                    for part in parts:
                        low = part.lower()
                        if "navigate" in low or "profile image" in low:
                            continue
                        if _looks_like_inbox_time(part):
                            skip_done += 1
                            continue
                        name_in_idx = _collapse_ws(by_idx[best_idx].get("name") or "")
                        if name_in_idx and _collapse_ws(part).lower() == name_in_idx.lower():
                            continue
                        # Это сниппет
                        candidate = _collapse_ws(part)
                        if len(candidate) >= 3 and not candidate.lower().startswith("navigate"):
                            by_idx[best_idx]["content"] = candidate
                            break

    # Строим результат
    rows: List[dict] = []
    nav_cut = int(scr_h * OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC) if scr_h else 9999

    for idx in sorted(by_idx.keys()):
        info = by_idx[idx]
        name = _collapse_ws(info.get("name") or "")
        if not name or _is_bad_captured_offerup_name(name) or len(name) < 2:
            continue

        content = _collapse_ws(info.get("content") or "")
        if _INBOX_SOLD_SNIPPET_RE.search(content):
            continue
        # Если content начинается с "You:" — мы уже ответили, пропускаем
        if content.lower().startswith("you:"):
            continue

        # tap координаты
        tap_cy = info.get("tap_cy") or info.get("title_cy") or info.get("content_cy") or 0
        tap_cx = info.get("tap_cx") or (int(scr_w * 0.35) if scr_w else 360)
        if scr_h and (tap_cy < OFFERUP_INBOX_LIST_TOP_Y_MIN or tap_cy > nav_cut):
            continue

        rows.append({
            "name": name,
            "snippet": content,  # пустая строка тоже OK — откроем чат
            "tap": (tap_cx, tap_cy),
            "needs_open_for_reply": not bool(content),
        })

    rows.sort(key=lambda r: r["tap"][1])
    return rows


def _looks_like_listing_title_preview(text: str) -> bool:
    t = _collapse_ws(text or "")
    if not t or len(t) < 8:
        return False
    low = t.lower()
    if low.startswith("you:"):
        return False
    title_markers = (
        "for sale", "obo", "firm price", "available", "pick up only",
        "womens ", "women's ", "mens ", "men's ", "shoes", "bags", "machines",
    )
    if any(m in low for m in title_markers) and len(t) > 28:
        return True
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 12:
        upper = sum(1 for c in letters if c.isupper())
        if upper / len(letters) >= 0.82 and len(t) >= 24:
            return True
    if t.isupper() and len(t) >= 18:
        return True
    return False


def _looks_like_valid_inbox_preview(text: str) -> bool:
    t = _collapse_ws(text or "")
    if not t or len(t) < 2 or len(t) > 220:
        return False
    if t.lower().startswith("you:"):
        return False
    if _looks_like_inbox_time(t) or _looks_like_inbox_noise(t):
        return False
    if _looks_like_listing_title_preview(t):
        return False
    if re.match(r"^\$?\d+([.,]\d+)?\s*$", t):
        return False
    return True


def _offerup_pick_row_snippet_from_texts(
    texts: List[str],
    name: str,
    qname: str = "",
) -> str:
    match_name = qname or name
    snippet = ""
    for t in texts:
        if _looks_like_inbox_time(t) or _looks_like_inbox_noise(t):
            continue
        if t == name or _inbox_row_name_matches(t, match_name):
            continue
        if t.lower().startswith("you:"):
            return ""
        if _looks_like_valid_inbox_preview(t):
            snippet = t
    return snippet


def _offerup_collect_inbox_rows_from_nodes(
    nodes: List[dict],
    scr_w: int,
    scr_h: int,
) -> List[dict]:
    groups: List[List[dict]] = []
    gap = OFFERUP_INBOX_ROW_GROUP_Y_GAP
    for n in nodes:
        if not groups or n["cy"] - groups[-1][-1]["cy"] > gap:
            groups.append([n])
        else:
            groups[-1].append(n)
    rows: List[dict] = []
    top_cut = OFFERUP_INBOX_LIST_TOP_Y_MIN
    for gi, g in enumerate(groups):
        vals = [x["text"] for x in g]
        clean = [
            v for v in vals
            if not _looks_like_inbox_time(v)
            and not _looks_like_inbox_noise(v)
        ]
        if not clean:
            continue
        tail_sorted = sorted(g, key=lambda x: x["cy"])
        tail_texts = [n["text"].strip().lower() for n in tail_sorted if n.get("text")]
        if tail_texts and tail_texts[-1].startswith("you:"):
            continue
        name = clean[0]
        snippet = ""
        for v in clean[1:]:
            if v != name and not v.lower().startswith("you:"):
                snippet = v
        if not snippet:
            for n in tail_sorted:
                t = n["text"]
                if _looks_like_inbox_time(t) or _looks_like_inbox_noise(t):
                    continue
                if t == name or _inbox_row_name_matches(t, name):
                    continue
                if t.lower().startswith("you:"):
                    continue
                snippet = t
                break
        if not snippet:
            extra: List[str] = []
            base_y = int(sum(x["cy"] for x in g) / len(g))
            for n in nodes:
                if abs(n["cy"] - base_y) > 220:
                    continue
                extra.append(n["text"])
            snippet = _offerup_pick_row_snippet_from_texts(extra, name)
        if not snippet and gi + 1 < len(groups):
            nxt = [x["text"] for x in groups[gi + 1]]
            snippet = _offerup_pick_row_snippet_from_texts(nxt, name)
        if _is_bad_captured_offerup_name(name) or len(name.strip()) < 2:
            continue
        if snippet and name == snippet:
            continue
        y = int(sum(x["cy"] for x in g) / len(g))
        tap_x = min(x["cx"] for x in g)
        if scr_w:
            tap_x = min(tap_x, int(scr_w * 0.38))
        if scr_h:
            y = min(y, max(top_cut + 20, int(scr_h * OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC)))
        rows.append({
            "name": name,
            "snippet": snippet,
            "tap": (tap_x, y),
            "needs_open_for_reply": not bool(snippet),
        })
    return rows


def _offerup_merge_inbox_row_lists(*lists: List[dict]) -> List[dict]:
    """Склеивает строки одного продавца; приоритет — запись с превью и полным именем из очереди."""
    out: List[dict] = []

    def _score(row: dict) -> Tuple[int, int]:
        sn = 1 if str(row.get("snippet") or "").strip() else 0
        nm = len(str(row.get("name") or ""))
        return (sn, nm)

    for rows in lists:
        for row in rows or []:
            rname = str(row.get("name") or "")
            if not rname.strip():
                continue
            merged = False
            for i, ex in enumerate(out):
                if not _inbox_row_name_matches(str(ex.get("name") or ""), rname):
                    continue
                merged = True
                if _score(row) >= _score(ex):
                    keep = dict(row)
                    if not keep.get("snippet") and ex.get("snippet"):
                        keep["snippet"] = ex["snippet"]
                    if len(str(ex.get("name") or "")) > len(str(keep.get("name") or "")):
                        keep["name"] = ex["name"]
                    out[i] = keep
                elif not ex.get("snippet") and row.get("snippet"):
                    ex["snippet"] = row["snippet"]
                    ex["needs_open_for_reply"] = False
                break
            if not merged:
                out.append(dict(row))
    return out


def _offerup_inbox_row_already_replied(row: dict) -> bool:
    snip = str(row.get("snippet") or "").strip().lower()
    if snip.startswith("you:"):
        return True
    return False


def _offerup_find_inbox_row_by_seller(
    root: ET.Element,
    seller_name: str,
    nodes: Optional[List[dict]] = None,
    scr_w: int = 0,
    scr_h: int = 0,
) -> Optional[dict]:
    """Найти строку Inbox по имени продавца (прямой поиск по UI dump)."""
    seller = _collapse_ws(seller_name or "").strip()
    if not seller or _is_generic_link_seller_name(seller):
        return None
    if nodes is None:
        nodes, scr_w, scr_h = _offerup_inbox_list_nodes(root)
    hits: List[dict] = []
    for n in nodes or []:
        if _inbox_row_name_matches(n.get("text") or "", seller):
            hits.append(n)
    if not hits:
        for node in root.iter("node"):
            txt = _collapse_ws(node.get("text") or node.get("content-desc") or "")
            if not txt or len(txt) > 80:
                continue
            if not _inbox_row_name_matches(txt, seller):
                continue
            if _is_bad_captured_offerup_name(txt):
                continue
            pr = parse_bounds(node.get("bounds") or "")
            if not pr:
                continue
            l, t, r, b = pr
            cy = (t + b) // 2
            if scr_h and (cy < 70 or cy > int(scr_h * 0.91)):
                continue
            hits.append({"text": txt, "bounds": pr, "cx": (l + r) // 2, "cy": cy})
    if not hits:
        return None
    name_node = min(hits, key=lambda n: len(n.get("text") or ""))
    band_y = name_node["cy"]
    band_texts = [n["text"] for n in (nodes or []) if abs(n["cy"] - band_y) <= OFFERUP_INBOX_ROW_GROUP_Y_GAP + 100]
    snippet = _offerup_pick_row_snippet_from_texts(band_texts, name_node.get("text") or seller, seller)
    if not snippet:
        wide = [n["text"] for n in (nodes or []) if abs(n["cy"] - band_y) <= 240]
        snippet = _offerup_pick_row_snippet_from_texts(wide, name_node.get("text") or seller, seller)
    if not snippet:
        return None
    y = name_node["cy"]
    tap_x = name_node["cx"]
    if scr_w:
        tap_x = min(tap_x, int(scr_w * 0.38))
    top_cut = OFFERUP_INBOX_LIST_TOP_Y_MIN
    if scr_h:
        y = min(y, max(top_cut + 20, int(scr_h * OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC)))
    return {
        "name": seller,
        "snippet": snippet,
        "tap": (tap_x, y),
        "needs_open_for_reply": not bool(snippet),
    }


def _offerup_collect_inbox_rows(root: ET.Element) -> List[dict]:
    """
    Парсит строки Inbox. Сначала пробует resource-id метод (надёжный),
    при неудаче — fallback на текстовый парсер.
    """
    if _offerup_ui_is_discussion_chat(root):
        return []
    # Метод 1: resource-id (OfferUp использует message-N.title / message-N.content)
    rid_rows = _offerup_parse_inbox_rows_by_resource_id(root)
    if rid_rows:
        return rid_rows

    # Fallback: старый текстовый парсер
    nodes, scr_w, scr_h = _offerup_inbox_list_nodes(root)
    if not nodes:
        return []
    raw = _offerup_collect_inbox_rows_from_nodes(nodes, scr_w, scr_h)
    rows: List[dict] = []
    for row in raw:
        if _offerup_inbox_row_already_replied(row):
            continue
        sn = _collapse_ws(str(row.get("snippet") or ""))
        if sn and _looks_like_valid_inbox_preview(sn):
            row["snippet"] = sn
            row["needs_open_for_reply"] = False
            rows.append(row)
    rows.sort(key=lambda r: int((r.get("tap") or (0, 9999))[1]))
    return rows


def _offerup_ensure_back_from_chat(
    serial: Optional[str],
    log_func: Callable[[str], None],
    root: Optional[ET.Element] = None,
) -> None:
    """Всегда выходим из чата, если на экране Discussion* (не полагаться только на compose)."""
    root_use = root
    if root_use is None:
        root_use = dump_ui_serial(serial) if serial else dump_ui()
    if root_use is None:
        _offerup_keyevent(serial, "4")
        time.sleep(0.25)
        return
    texts = " ".join(_ui_visible_texts(root_use))
    if not _offerup_ui_is_discussion_chat(root_use) and _offerup_ui_looks_like_inbox_list_screen(
        root_use, texts
    ):
        return
    log_func("Inbox scan: выход из чата → лента Inbox")
    _offerup_back_to_inbox(serial, log_func, root=root_use)
    time.sleep(0.2)


def _offerup_ui_looks_like_inbox_open(root: Optional[ET.Element], texts_joined: str) -> bool:
    """Эвристика: экран списка сообщений Inbox."""
    return _offerup_ui_looks_like_inbox_list_screen(root, texts_joined)


def _pending_created_sent_ids() -> set[int]:
    with _created_links_lock:
        return {int(x.get("id") or 0) for x in _created_links if x.get("sent")}


_COACH_FEED_CAP = 40


def append_coach_feed_from_inbox(text: str) -> int:
    """Сообщение для блока «чат с ИИ» в веб-UI (оператор)."""
    global _coach_feed_next_id
    t = _collapse_ws(text or "")[:2000]
    if not t:
        return 0
    with _coach_feed_lock:
        rid = int(_coach_feed_next_id)
        _coach_feed_next_id += 1
        _coach_feed.append({"id": rid, "ts": time.strftime("%H:%M:%S"), "content": t})
        if len(_coach_feed) > _COACH_FEED_CAP:
            del _coach_feed[: len(_coach_feed) - _COACH_FEED_CAP]
        return rid


def enqueue_coach_chat_message(text: str, *, kind: str = "inbox") -> int:
    """Сообщение в основной чат «ИИ-коуч» (coach-msgs), забирается poll API."""
    global _coach_chat_inject_next_id
    t = _collapse_ws(text or "")[:2000]
    if not t:
        return 0
    with _coach_chat_inject_lock:
        rid = int(_coach_chat_inject_next_id)
        _coach_chat_inject_next_id += 1
        _coach_chat_inject_pending.append(
            {"id": rid, "text": t, "kind": kind, "ts": time.strftime("%H:%M:%S")}
        )
        return rid


def coach_chat_inject_for_poll() -> List[dict]:
    with _coach_chat_inject_lock:
        items = list(_coach_chat_inject_pending)
        _coach_chat_inject_pending.clear()
        return items


def append_coach_inbox_question(
    seller_name: str,
    seller_message: str,
    operator_hint: str = "",
) -> int:
    """Вопрос от Inbox для оператора — в чат «ИИ-коуч» и ленту."""
    sn = _collapse_ws(seller_name)[:80] or "продавец"
    sm = _collapse_ws(seller_message)[:500]
    hint = _collapse_ws(operator_hint) or "Как ответить продавцу?"
    text = (
        f"📩 Inbox — {sn} написал:\n«{sm}»\n\n"
        f"{hint}\n\n"
        f"Напишите в чате, например:\n«напиши {sn} …»"
    )
    enqueue_coach_chat_message(text, kind="inbox")
    return append_coach_feed_from_inbox(text)


def coach_feed_for_poll() -> List[dict]:
    with _coach_feed_lock:
        return list(_coach_feed)


def clear_coach_feed() -> None:
    with _coach_feed_lock:
        _coach_feed.clear()


def _training_rule_fingerprint(rule: str) -> str:
    return hashlib.sha256(_collapse_ws(rule).lower().encode("utf-8")).hexdigest()[:16]


def _load_ai_training_file_raw() -> dict:
    if not os.path.isfile(MUMU_AI_TRAINING_PATH):
        return {"schema": 1, "rule_ids": [], "rules": []}
    try:
        with open(MUMU_AI_TRAINING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("schema", 1)
            data.setdefault("rule_ids", [])
            data.setdefault("rules", [])
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"schema": 1, "rule_ids": [], "rules": []}


def _save_ai_training_file(data: dict) -> None:
    payload = {
        "schema": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rule_ids": list(data.get("rule_ids") or []),
        "rules": list(data.get("rules") or []),
    }
    tmp = MUMU_AI_TRAINING_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MUMU_AI_TRAINING_PATH)


def _training_rules_from_json_payload(data: object) -> List[str]:
    if isinstance(data, list):
        return [_collapse_ws(str(x)) for x in data if _collapse_ws(str(x))]
    if isinstance(data, dict):
        raw = data.get("rules")
        if isinstance(raw, list):
            return [_collapse_ws(str(x)) for x in raw if _collapse_ws(str(x))]
    return []


def add_ai_training_rule(
    rule: str,
    *,
    source: str = "local",
    notify: bool = True,
    push_github: bool = True,
) -> bool:
    """Добавить правило, если ещё нет (по fingerprint). Возвращает True если новое."""
    global LOCAL_LLM_EXTRA_TRAINING_RULES
    r = _collapse_ws(rule)[:500]
    if not r or len(r) < 8:
        return False
    fp = _training_rule_fingerprint(r)
    data = _load_ai_training_file_raw()
    ids = list(data.get("rule_ids") or [])
    rules = list(data.get("rules") or [])
    if fp in ids:
        return False
    ids.append(fp)
    rules.append(r)
    if len(rules) > 120:
        rules = rules[-120:]
        ids = ids[-120:]
    data["rule_ids"] = ids
    data["rules"] = rules
    _save_ai_training_file(data)
    merged = list(LOCAL_LLM_EXTRA_TRAINING_RULES or [])
    if r not in merged:
        merged.append(r)
    LOCAL_LLM_EXTRA_TRAINING_RULES = merged[-80:]
    try:
        _persist_settings_merge({"local_llm_extra_training_rules": LOCAL_LLM_EXTRA_TRAINING_RULES})
    except Exception:
        pass
    if notify:
        append_coach_feed_from_inbox(f"Обучение (+{source}): {r[:90]}…")
    if push_github:
        schedule_github_training_push(f"add:{source}")
    return True


def import_ai_training_rules(
    rules: List[str],
    *,
    source: str = "import",
    notify: bool = False,
    push_github: bool = False,
) -> int:
    added = 0
    for raw in rules or []:
        if add_ai_training_rule(
            str(raw or ""),
            source=source,
            notify=notify,
            push_github=False,
        ):
            added += 1
    if added and notify:
        append_coach_feed_from_inbox(f"Обучение ({source}): добавлено {added} правил")
    if added and push_github:
        schedule_github_training_push(f"import:{source}")
    return added


def merge_ai_training_file_into_memory() -> int:
    """При старте: подтянуть mumu_ai_training.json в LOCAL_LLM_EXTRA_TRAINING_RULES."""
    global LOCAL_LLM_EXTRA_TRAINING_RULES
    data = _load_ai_training_file_raw()
    added = 0
    seen = {_training_rule_fingerprint(x) for x in (LOCAL_LLM_EXTRA_TRAINING_RULES or [])}
    merged = list(LOCAL_LLM_EXTRA_TRAINING_RULES or [])
    for r in data.get("rules") or []:
        rs = _collapse_ws(str(r))
        if not rs:
            continue
        fp = _training_rule_fingerprint(rs)
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(rs)
        added += 1
    LOCAL_LLM_EXTRA_TRAINING_RULES = merged[-80:]
    return added


def _merge_training_payloads(local: dict, remote: dict) -> dict:
    """Объединить правила: remote + local, без дублей (по fingerprint)."""
    ids = list(local.get("rule_ids") or [])
    rules = list(local.get("rules") or [])
    seen = set(ids)
    for r in _training_rules_from_json_payload(remote):
        fp = _training_rule_fingerprint(r)
        if fp in seen:
            continue
        seen.add(fp)
        ids.append(fp)
        rules.append(r)
    cap = 200
    if len(rules) > cap:
        rules = rules[-cap:]
        ids = ids[-cap:]
    return {
        "schema": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rule_ids": ids,
        "rules": rules,
    }


def _github_fetch_remote_training_json(
    owner: str,
    repo: str,
    branch: str,
    path: str,
    token: str,
) -> Tuple[dict, str]:
    """Скачать JSON с GitHub; пустой dict если файла ещё нет."""
    try:
        text, sha = _github_fetch_training_text(owner, repo, branch, path, token)
        if not (text or "").strip():
            return {"schema": 1, "rules": []}, sha
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload, sha
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"schema": 1, "rules": []}, ""
    except json.JSONDecodeError:
        pass
    except Exception:
        pass
    return {"schema": 1, "rules": []}, ""


def _github_merge_local_with_remote_and_save(
    owner: str,
    repo: str,
    branch: str,
    path: str,
    token: str,
) -> Tuple[str, int]:
    """Pull+merge в локальный файл. Возвращает JSON-текст и число новых с remote."""
    local = _load_ai_training_file_raw()
    remote, _sha = _github_fetch_remote_training_json(owner, repo, branch, path, token)
    local_fps = {_training_rule_fingerprint(r) for r in (local.get("rules") or [])}
    merged = _merge_training_payloads(local, remote)
    added_from_remote = sum(
        1 for r in (merged.get("rules") or [])
        if _training_rule_fingerprint(r) not in local_fps
    )
    _save_ai_training_file(merged)
    merge_ai_training_file_into_memory()
    text = json.dumps(
        {
            "schema": 1,
            "updated_at": merged.get("updated_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "rule_ids": merged.get("rule_ids") or [],
            "rules": merged.get("rules") or [],
        },
        ensure_ascii=False,
        indent=2,
    )
    return text, added_from_remote


def _ai_training_file_json_text() -> str:
    data = _load_ai_training_file_raw()
    payload = {
        "schema": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rule_ids": list(data.get("rule_ids") or []),
        "rules": list(data.get("rules") or []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_github_repo_ref(repo_raw: str) -> Tuple[str, str]:
    """owner/repo из slug или URL GitHub."""
    s = (repo_raw or "").strip()
    if not s:
        return "", ""
    s = s.rstrip("/")
    if "github.com" in s.lower():
        try:
            p = urllib.parse.urlparse(s)
            parts = [x for x in (p.path or "").split("/") if x]
            if len(parts) >= 2:
                return parts[0], parts[1]
        except Exception:
            pass
    if "/" in s:
        owner, repo = s.split("/", 1)
        repo = repo.split("/")[0]
        repo = repo.replace(".git", "").strip()
        return owner.strip(), repo.strip()
    return "", ""


def _github_http_request(
    url: str,
    token: str = "",
    *,
    accept: str = "application/vnd.github+json",
    method: str = "GET",
    data: Optional[bytes] = None,
) -> Tuple[int, dict, bytes]:
    headers = {
        "User-Agent": "MumuPaster2-TrainingSync/1.0",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    tok = (token or "").strip()
    if tok:
        if tok.startswith("ghp_") or tok.startswith("gho_"):
            headers["Authorization"] = f"token {tok}"
        else:
            headers["Authorization"] = f"Bearer {tok}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read()
            code = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as e:
        body = e.read()
        code = int(e.code or 0)
    try:
        meta = json.loads(body.decode("utf-8", errors="replace")) if body else {}
    except json.JSONDecodeError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return code, meta, body


def _github_error_text(meta: dict, code: int, *, for_write: bool = False) -> str:
    msg = str((meta or {}).get("message") or f"HTTP {code}").strip()
    docs = str((meta or {}).get("documentation_url") or "").strip()
    parts = [msg]
    errs = (meta or {}).get("errors")
    if isinstance(errs, list):
        for item in errs[:3]:
            if isinstance(item, dict) and item.get("message"):
                parts.append(str(item["message"]))
    if code == 403 and for_write:
        parts.append(
            "Нет прав записи. Fine-grained PAT: Repository access → этот репозиторий; "
            "Permissions → Contents: Read and write. Или Classic PAT со scope «repo»."
        )
    elif code == 404:
        parts.append("Репозиторий/ветка/файл не найден — проверьте owner/repo и ветку main.")
    if docs:
        parts.append(docs)
    return " — ".join(parts)[:500]


_GIT_EXE_CACHE: Optional[str] = None


def _resolve_git_exe() -> str:
    global _GIT_EXE_CACHE
    if _GIT_EXE_CACHE and os.path.isfile(_GIT_EXE_CACHE):
        return _GIT_EXE_CACHE
    found = shutil.which("git")
    if not found:
        for candidate in (
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files\Git\bin\git.exe",
            r"C:\Program Files (x86)\Git\cmd\git.exe",
        ):
            if os.path.isfile(candidate):
                found = candidate
                break
    _GIT_EXE_CACHE = found or ""
    return _GIT_EXE_CACHE


def _git_available() -> bool:
    return bool(_resolve_git_exe())


def _run_git(args: List[str], cwd: str, *, timeout: int = 120) -> Tuple[bool, str]:
    git = _resolve_git_exe()
    if not git:
        return False, "git не найден (установите Git for Windows)"
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        r = subprocess.run(
            [git] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "git timeout"
    except Exception as e:
        return False, str(e)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, out


def _github_clone_url(owner: str, repo: str, token: str) -> str:
    tok = (token or "").strip()
    if tok:
        return f"https://x-access-token:{urllib.parse.quote(tok, safe='')}@github.com/{owner}/{repo}.git"
    return f"https://github.com/{owner}/{repo}.git"


def _github_ensure_git_clone(owner: str, repo: str, branch: str, token: str) -> Tuple[bool, str]:
    clone_dir = GITHUB_TRAINING_CLONE_DIR
    git_dir = os.path.join(clone_dir, ".git")
    url = _github_clone_url(owner, repo, token)
    if not os.path.isdir(git_dir):
        if os.path.isdir(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)
        os.makedirs(os.path.dirname(clone_dir), exist_ok=True)
        ok, out = _run_git(["clone", "--branch", branch, url, clone_dir], os.path.dirname(clone_dir))
        if not ok:
            ok2, out2 = _run_git(["clone", url, clone_dir], os.path.dirname(clone_dir))
            if not ok2:
                return False, out2 or out
            _run_git(["checkout", "-B", branch], clone_dir)
        return True, ""
    _run_git(["remote", "set-url", "origin", url], clone_dir)
    ok, out = _run_git(["fetch", "origin"], clone_dir)
    if not ok:
        return False, out
    _run_git(["checkout", branch], clone_dir)
    ok, out = _run_git(["pull", "--rebase", "origin", branch], clone_dir)
    return ok, out


def _github_push_via_api(
    owner: str,
    repo: str,
    branch: str,
    path: str,
    token: str,
    text: str,
    message: str,
) -> dict:
    file_sha = ""
    try:
        _t, file_sha = _github_fetch_training_text(owner, repo, branch, path, token)
    except Exception:
        file_sha = ""
    path_q = urllib.parse.quote(path.lstrip("/"))
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path_q}"
    body_obj: dict = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if file_sha and len(file_sha) == 40:
        body_obj["sha"] = file_sha
    body = json.dumps(body_obj).encode("utf-8")
    code, meta, _raw = _github_http_request(url, token, method="PUT", data=body)
    if code not in (200, 201):
        return {"ok": False, "error": _github_error_text(meta, code, for_write=True), "http_code": code, "method": "api"}
    commit = meta.get("commit") if isinstance(meta, dict) else {}
    sha = ""
    if isinstance(commit, dict):
        sha = str(commit.get("sha") or "")[:12]
    return {"ok": True, "method": "api", "sha": sha}


def _github_push_via_git(
    owner: str,
    repo: str,
    branch: str,
    path: str,
    token: str,
    text: str,
    message: str,
) -> dict:
    ok, err = _github_ensure_git_clone(owner, repo, branch, token)
    if not ok:
        return {"ok": False, "error": err, "method": "git"}
    clone_dir = GITHUB_TRAINING_CLONE_DIR
    rel = path.lstrip("/").replace("\\", "/")
    target = os.path.join(clone_dir, rel)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(text)
    _run_git(["config", "user.email", "mumupaster@local"], clone_dir)
    _run_git(["config", "user.name", "MumuPaster2"], clone_dir)
    _run_git(["add", rel], clone_dir)
    ok, out = _run_git(["commit", "-m", message], clone_dir)
    if not ok and "nothing to commit" in out.lower():
        return {"ok": True, "skipped": True, "reason": "nothing_to_commit", "method": "git"}
    if not ok:
        return {"ok": False, "error": out, "method": "git"}
    ok, out = _run_git(["push", "origin", f"HEAD:{branch}"], clone_dir)
    if not ok:
        return {"ok": False, "error": out, "method": "git"}
    return {"ok": True, "method": "git"}


def _github_publish_app_bundle_via_git(
    owner: str,
    repo: str,
    branch: str,
    token: str,
    message: str,
) -> dict:
    """Один коммит со всеми файлами приложения (если API отказал в записи)."""
    ok, err = _github_ensure_app_repo_clone(owner, repo, branch, token)
    if not ok:
        return {"ok": False, "error": err, "method": "git"}
    clone_dir = GITHUB_APP_CLONE_DIR
    copied: List[str] = []
    for rel in GITHUB_APP_UPDATE_FILES:
        rel_norm = rel.replace("\\", "/")
        src = os.path.join(_APP_DIR, rel_norm)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(clone_dir, rel_norm)
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel_norm)
    if not copied:
        return {"ok": False, "error": "Нет локальных файлов для публикации", "method": "git"}
    _run_git(["config", "user.email", "mumupaster@local"], clone_dir)
    _run_git(["config", "user.name", "MumuPaster2"], clone_dir)
    _run_git(["add"] + copied, clone_dir)
    ok_c, out_c = _run_git(["commit", "-m", message], clone_dir)
    if not ok_c and "nothing to commit" not in out_c.lower():
        return {"ok": False, "error": out_c or "git commit failed", "method": "git"}
    ok_p, out_p = _run_git(["push", "origin", f"HEAD:{branch}"], clone_dir)
    if not ok_p:
        hint = _github_error_text({}, 403, for_write=True) if "403" in out_p else out_p
        return {"ok": False, "error": (out_p or hint)[:500], "method": "git"}
    return {"ok": True, "method": "git", "pushed_files": copied}


def github_check_repo_access() -> dict:
    """Проверка: виден ли репозиторий и есть ли push через API."""
    owner, repo = _parse_github_repo_ref(GITHUB_TRAINING_REPO)
    if not owner or not repo:
        return {"ok": False, "error": "github_training_repo не задан"}
    token = (GITHUB_TRAINING_TOKEN or "").strip()
    branch = (GITHUB_TRAINING_BRANCH or "main").strip() or "main"
    url = f"https://api.github.com/repos/{owner}/{repo}"
    code, meta, _raw = _github_http_request(url, token)
    if code == 404:
        return {"ok": False, "error": _github_error_text(meta, code), "http_code": code}
    if code == 401:
        return {"ok": False, "error": "Неверный или просроченный token", "http_code": code}
    if code != 200:
        return {"ok": False, "error": _github_error_text(meta, code), "http_code": code}
    perms = meta.get("permissions") if isinstance(meta.get("permissions"), dict) else {}
    can_push = bool(perms.get("push"))
    can_admin = bool(perms.get("admin"))
    write_ok = can_push or can_admin
    hint = ""
    if not write_ok:
        hint = (
            "Скачивание может работать без API-записи, но пуш — нет. "
            "Для Fine-grained PAT: Contents Read and write + выбран этот репозиторий."
        )
    return {
        "ok": True,
        "repo": f"{owner}/{repo}",
        "branch": branch,
        "can_push": write_ok,
        "permissions": perms,
        "hint": hint,
        "git_available": _git_available(),
        "token_type": "fine_grained" if token.startswith("github_pat_") else "classic_or_other",
    }


def schedule_github_training_push(reason: str = "update") -> None:
    """Отложенная отправка на GitHub (1 с), чтобы схлопнуть несколько правил подряд."""
    global _github_push_timer

    if not (GITHUB_TRAINING_REPO or "").strip():
        return
    if not (GITHUB_TRAINING_TOKEN or "").strip():
        return

    def _fire() -> None:
        try:
            github_push_training_file(reason=reason)
        except Exception:
            pass

    with _github_push_lock:
        if _github_push_timer:
            _github_push_timer.cancel()
        _github_push_timer = threading.Timer(1.0, _fire)
        _github_push_timer.daemon = True
        _github_push_timer.start()


def github_push_training_file(*, reason: str = "update", force: bool = False) -> dict:
    """Отправить локальный mumu_ai_training.json в GitHub (git или API)."""
    global GITHUB_TRAINING_LAST_PUSH_AT, GITHUB_TRAINING_LAST_PUSH_ERROR, GITHUB_TRAINING_LAST_SYNC_SHA

    if not (GITHUB_TRAINING_REPO or "").strip():
        err = "github_training_repo не задан"
        GITHUB_TRAINING_LAST_PUSH_ERROR = err
        return {"ok": False, "error": err}
    token = (GITHUB_TRAINING_TOKEN or "").strip()
    if not token:
        err = "Нужен GitHub token (Settings → Developer settings → token с правом repo)"
        GITHUB_TRAINING_LAST_PUSH_ERROR = err
        return {"ok": False, "error": err}

    owner, repo = _parse_github_repo_ref(GITHUB_TRAINING_REPO)
    if not owner or not repo:
        err = "Неверный репозиторий (owner/repo)"
        GITHUB_TRAINING_LAST_PUSH_ERROR = err
        return {"ok": False, "error": err}
    branch = (GITHUB_TRAINING_BRANCH or "main").strip() or "main"
    path = (GITHUB_TRAINING_PATH or "mumu_ai_training.json").strip() or "mumu_ai_training.json"

    if not _github_training_sync_lock.acquire(blocking=False):
        return {"ok": False, "skipped": True, "reason": "sync_in_progress"}

    try:
        text, merged_remote = _github_merge_local_with_remote_and_save(
            owner, repo, branch, path, token
        )
        msg = f"MumuPaster training ({reason})"
        result: dict
        if _git_available():
            result = _github_push_via_git(owner, repo, branch, path, token, text, msg)
            if not result.get("ok"):
                result = _github_push_via_api(owner, repo, branch, path, token, text, msg)
        else:
            result = _github_push_via_api(owner, repo, branch, path, token, text, msg)
        if result.get("ok"):
            GITHUB_TRAINING_LAST_PUSH_AT = time.strftime("%Y-%m-%d %H:%M:%S")
            GITHUB_TRAINING_LAST_PUSH_ERROR = ""
            try:
                _t, remote_sha = _github_fetch_training_text(owner, repo, branch, path, token)
                if remote_sha:
                    GITHUB_TRAINING_LAST_SYNC_SHA = remote_sha
            except Exception:
                pass
            try:
                _persist_settings_merge(
                    {
                        "github_training_last_push_at": GITHUB_TRAINING_LAST_PUSH_AT,
                        "github_training_last_push_error": "",
                        "github_training_last_sync_sha": GITHUB_TRAINING_LAST_SYNC_SHA,
                    }
                )
            except Exception:
                pass
            if not result.get("skipped"):
                append_coach_feed_from_inbox(
                    f"GitHub: обучение отправлено ({result.get('method') or 'ok'})"
                    + (f", +{merged_remote} с remote" if merged_remote else "")
                )
            result["merged_from_remote"] = merged_remote
        else:
            err = str(result.get("error") or "push failed")[:500]
            GITHUB_TRAINING_LAST_PUSH_ERROR = err
            try:
                _persist_settings_merge({"github_training_last_push_error": err})
            except Exception:
                pass
        return result
    finally:
        _github_training_sync_lock.release()


def _github_fetch_training_text(
    owner: str,
    repo: str,
    branch: str,
    path: str,
    token: str,
) -> Tuple[str, str]:
    """Текст JSON + sha/etag для пропуска неизменённого файла."""
    branch_q = urllib.parse.quote(branch or "main")
    path_q = urllib.parse.quote(path.lstrip("/"))
    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path_q}?ref={branch_q}"
    try:
        _code, meta, _raw = _github_http_request(api_url, token)
        if isinstance(meta, dict) and meta.get("type") == "file":
            sha = str(meta.get("sha") or "")
            enc = str(meta.get("content") or "").replace("\n", "")
            if enc:
                text = base64.b64decode(enc).decode("utf-8", errors="replace")
                return text, sha
    except urllib.error.HTTPError as e:
        if e.code not in (403, 404) and (token or "").strip():
            raise
    except Exception:
        if (token or "").strip():
            raise
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path.lstrip('/')}"
    _code2, _meta2, body = _github_http_request(raw_url, token, accept="application/json")
    text = body.decode("utf-8", errors="replace")
    digest = hashlib.sha256(body).hexdigest()
    return text, digest


def github_sync_training_rules(*, force: bool = False) -> dict:
    """Скачать mumu_ai_training.json с GitHub и добавить только новые правила."""
    global GITHUB_TRAINING_LAST_SYNC_AT, GITHUB_TRAINING_LAST_SYNC_SHA
    global GITHUB_TRAINING_LAST_SYNC_ERROR, GITHUB_TRAINING_LAST_SYNC_ADDED

    if not GITHUB_TRAINING_SYNC_ENABLED and not force:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    owner, repo = _parse_github_repo_ref(GITHUB_TRAINING_REPO)
    if not owner or not repo:
        err = "Укажите репозиторий: owner/repo (например user/MumuTraining)"
        GITHUB_TRAINING_LAST_SYNC_ERROR = err
        return {"ok": False, "error": err}
    branch = (GITHUB_TRAINING_BRANCH or "main").strip() or "main"
    path = (GITHUB_TRAINING_PATH or "mumu_ai_training.json").strip() or "mumu_ai_training.json"
    token = (GITHUB_TRAINING_TOKEN or "").strip()

    if not _github_training_sync_lock.acquire(blocking=False):
        return {"ok": False, "skipped": True, "reason": "sync_in_progress"}

    try:
        text, remote_sha = _github_fetch_training_text(owner, repo, branch, path, token)
        if remote_sha and remote_sha == (GITHUB_TRAINING_LAST_SYNC_SHA or "") and not force:
            return {
                "ok": True,
                "skipped": True,
                "reason": "unchanged",
                "sha": remote_sha[:12],
            }
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            err = f"Неверный JSON в {path}: {e}"
            GITHUB_TRAINING_LAST_SYNC_ERROR = err
            return {"ok": False, "error": err}
        rules = _training_rules_from_json_payload(payload)
        if not rules:
            GITHUB_TRAINING_LAST_SYNC_AT = time.strftime("%Y-%m-%d %H:%M:%S")
            GITHUB_TRAINING_LAST_SYNC_SHA = remote_sha
            GITHUB_TRAINING_LAST_SYNC_ADDED = 0
            GITHUB_TRAINING_LAST_SYNC_ERROR = ""
            return {"ok": True, "added": 0, "rules_in_file": 0, "reason": "empty_or_new_file"}
        added = import_ai_training_rules(rules, source="github", notify=False, push_github=False)
        merge_ai_training_file_into_memory()
        GITHUB_TRAINING_LAST_SYNC_AT = time.strftime("%Y-%m-%d %H:%M:%S")
        GITHUB_TRAINING_LAST_SYNC_SHA = remote_sha
        GITHUB_TRAINING_LAST_SYNC_ADDED = added
        GITHUB_TRAINING_LAST_SYNC_ERROR = ""
        try:
            _persist_settings_merge(
                {
                    "github_training_last_sync_at": GITHUB_TRAINING_LAST_SYNC_AT,
                    "github_training_last_sync_sha": GITHUB_TRAINING_LAST_SYNC_SHA,
                    "github_training_last_sync_error": "",
                    "github_training_last_sync_added": added,
                    "github_training_repo": GITHUB_TRAINING_REPO,
                    "github_training_branch": GITHUB_TRAINING_BRANCH,
                    "github_training_path": GITHUB_TRAINING_PATH,
                }
            )
        except Exception:
            pass
        if added:
            append_coach_feed_from_inbox(
                f"GitHub обучение: +{added} новых правил из {owner}/{repo} ({path})"
            )
        return {
            "ok": True,
            "added": added,
            "rules_in_file": len(rules),
            "sha": (remote_sha or "")[:12],
            "repo": f"{owner}/{repo}",
            "branch": branch,
            "path": path,
        }
    except urllib.error.HTTPError as e:
        err = f"GitHub HTTP {e.code}: {e.reason}"
        GITHUB_TRAINING_LAST_SYNC_ERROR = err
        try:
            _persist_settings_merge({"github_training_last_sync_error": err})
        except Exception:
            pass
        return {"ok": False, "error": err}
    except Exception as e:
        err = str(e)[:500]
        GITHUB_TRAINING_LAST_SYNC_ERROR = err
        try:
            _persist_settings_merge({"github_training_last_sync_error": err})
        except Exception:
            pass
        return {"ok": False, "error": err}
    finally:
        _github_training_sync_lock.release()


def _github_training_sync_loop() -> None:
    while True:
        interval = max(60, int(GITHUB_TRAINING_SYNC_INTERVAL_SEC or 600))
        time.sleep(interval)
        if not GITHUB_TRAINING_SYNC_ENABLED:
            continue
        if not (GITHUB_TRAINING_REPO or "").strip():
            continue
        try:
            github_sync_training_rules()
        except Exception:
            pass


def start_github_training_sync_background() -> None:
    global _github_training_bg_started
    if _github_training_bg_started:
        return
    _github_training_bg_started = True
    t = threading.Thread(target=_github_training_sync_loop, name="github-training-sync", daemon=True)
    t.start()


def read_app_version() -> str:
    try:
        if os.path.isfile(APP_VERSION_PATH):
            with open(APP_VERSION_PATH, "r", encoding="utf-8") as f:
                v = f.read().strip().splitlines()[0].strip()
                if v:
                    return v[:32]
    except OSError:
        pass
    return APP_VERSION


def write_app_version(ver: str) -> str:
    v = _collapse_ws((ver or "").strip())[:32]
    if not v:
        return read_app_version()
    with open(APP_VERSION_PATH, "w", encoding="utf-8") as f:
        f.write(v + "\n")
    return v


def _parse_app_version_tuple(ver: str) -> Tuple[int, ...]:
    parts: List[int] = []
    for x in re.split(r"[.\-]", (ver or "").strip()):
        if x.isdigit():
            parts.append(int(x))
        elif parts:
            break
    return tuple(parts) if parts else (0,)


def _app_version_newer(remote: str, local: str) -> bool:
    return _parse_app_version_tuple(remote) > _parse_app_version_tuple(local)


def _github_fetch_file_text(
    owner: str,
    repo: str,
    branch: str,
    path: str,
    token: str,
) -> Tuple[str, str]:
    text, sha = _github_fetch_training_text(owner, repo, branch, path, token)
    return text, sha


def _github_ensure_app_repo_clone(owner: str, repo: str, branch: str, token: str) -> Tuple[bool, str]:
    clone_dir = GITHUB_APP_CLONE_DIR
    git_dir = os.path.join(clone_dir, ".git")
    url = _github_clone_url(owner, repo, token)
    if not os.path.isdir(git_dir):
        if os.path.isdir(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)
        parent = os.path.dirname(clone_dir)
        os.makedirs(parent, exist_ok=True)
        ok, out = _run_git(["clone", "--branch", branch, url, clone_dir], parent)
        if not ok:
            ok2, out2 = _run_git(["clone", url, clone_dir], parent)
            if not ok2:
                return False, out2 or out
            _run_git(["checkout", "-B", branch], clone_dir)
        return True, ""
    _run_git(["remote", "set-url", "origin", url], clone_dir)
    ok, out = _run_git(["fetch", "origin"], clone_dir)
    if not ok:
        return False, out
    _run_git(["checkout", branch], clone_dir)
    ok, out = _run_git(["pull", "--rebase", "origin", branch], clone_dir)
    return ok, out


def app_update_check() -> dict:
    """Проверить version.txt на GitHub."""
    owner, repo = _parse_github_repo_ref(GITHUB_TRAINING_REPO)
    if not owner or not repo:
        return {"ok": False, "error": "github_training_repo не задан"}
    branch = (GITHUB_TRAINING_BRANCH or "main").strip() or "main"
    token = (GITHUB_TRAINING_TOKEN or "").strip()
    local = read_app_version()
    try:
        remote_text, _sha = _github_fetch_file_text(
            owner, repo, branch, GITHUB_APP_VERSION_FILE, token
        )
        remote = _collapse_ws(remote_text.splitlines()[0] if remote_text else "")
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "local_version": local}
    if not remote:
        return {
            "ok": True,
            "local_version": local,
            "remote_version": "",
            "update_available": False,
            "reason": "no_remote_version",
        }
    return {
        "ok": True,
        "local_version": local,
        "remote_version": remote,
        "update_available": _app_version_newer(remote, local),
        "repo": f"{owner}/{repo}",
        "branch": branch,
    }


def app_update_apply() -> dict:
    """Скачать новые файлы приложения из GitHub (git pull + копирование)."""
    owner, repo = _parse_github_repo_ref(GITHUB_TRAINING_REPO)
    if not owner or not repo:
        return {"ok": False, "error": "github_training_repo не задан"}
    branch = (GITHUB_TRAINING_BRANCH or "main").strip() or "main"
    token = (GITHUB_TRAINING_TOKEN or "").strip()
    if not token:
        return {"ok": False, "error": "Нужен GitHub token (один общий для команды — scope repo)"}

    chk = app_update_check()
    if not chk.get("ok"):
        return chk
    if not chk.get("update_available"):
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_latest",
            "local_version": chk.get("local_version"),
            "remote_version": chk.get("remote_version"),
        }

    backup_dir = os.path.join(_APP_DIR, ".backup", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(backup_dir, exist_ok=True)
    updated: List[str] = []

    if _git_available():
        ok, err = _github_ensure_app_repo_clone(owner, repo, branch, token)
        if not ok:
            return {"ok": False, "error": err or "git clone failed"}
        for rel in GITHUB_APP_UPDATE_FILES:
            src = os.path.join(GITHUB_APP_CLONE_DIR, rel.replace("\\", "/"))
            if not os.path.isfile(src):
                continue
            dst = os.path.join(_APP_DIR, rel.replace("\\", "/"))
            if os.path.isfile(dst):
                shutil.copy2(dst, os.path.join(backup_dir, os.path.basename(dst)))
            shutil.copy2(src, dst)
            updated.append(rel)
    else:
        for rel in GITHUB_APP_UPDATE_FILES:
            try:
                text, _sha = _github_fetch_file_text(owner, repo, branch, rel, token)
            except Exception:
                continue
            if not text:
                continue
            dst = os.path.join(_APP_DIR, rel.replace("\\", "/"))
            if os.path.isfile(dst):
                shutil.copy2(dst, os.path.join(backup_dir, os.path.basename(dst)))
            with open(dst, "w", encoding="utf-8") as f:
                f.write(text)
            updated.append(rel)

    if not updated:
        return {"ok": False, "error": "Не найдены файлы приложения в репозитории"}

    new_ver = read_app_version()
    append_coach_feed_from_inbox(
        f"Обновление приложения: {chk.get('local_version')} → {new_ver} ({len(updated)} файлов). Перезапустите app.py"
    )
    return {
        "ok": True,
        "updated_files": updated,
        "backup_dir": backup_dir,
        "local_version": chk.get("local_version"),
        "remote_version": chk.get("remote_version"),
        "new_version": new_ver,
        "restart_required": True,
    }


def app_update_publish(*, new_version: str = "", commit_message: str = "") -> dict:
    """Опубликовать файлы приложения на GitHub через API (только token, без входа в аккаунт)."""
    global GITHUB_APP_LAST_PUBLISH_AT, GITHUB_APP_LAST_PUBLISH_ERROR

    if not (GITHUB_TRAINING_REPO or "").strip():
        err = "github_training_repo не задан"
        GITHUB_APP_LAST_PUBLISH_ERROR = err
        return {"ok": False, "error": err}
    token = (GITHUB_TRAINING_TOKEN or "").strip()
    if not token:
        err = "Нужен GitHub token (scope repo) — вставьте в настройках, вход в браузер не нужен"
        GITHUB_APP_LAST_PUBLISH_ERROR = err
        return {"ok": False, "error": err}

    owner, repo = _parse_github_repo_ref(GITHUB_TRAINING_REPO)
    if not owner or not repo:
        err = "Неверный репозиторий (owner/repo)"
        GITHUB_APP_LAST_PUBLISH_ERROR = err
        return {"ok": False, "error": err}
    branch = (GITHUB_TRAINING_BRANCH or "main").strip() or "main"

    ver_in = _collapse_ws((new_version or "").strip())[:32]
    ver = write_app_version(ver_in) if ver_in else read_app_version()
    msg = _collapse_ws((commit_message or "").strip())[:200]
    if not msg:
        msg = f"MumuPaster2 {ver}"

    access = github_check_repo_access()

    pushed: List[str] = []
    errors: List[str] = []
    api_forbidden = False

    if _git_available():
        git_res = _github_publish_app_bundle_via_git(owner, repo, branch, token, msg)
        if git_res.get("ok"):
            pushed = list(git_res.get("pushed_files") or GITHUB_APP_UPDATE_FILES)
        else:
            errors.append("git: " + str(git_res.get("error") or "failed")[:200])

    if not pushed:
        for rel in GITHUB_APP_UPDATE_FILES:
            rel_norm = rel.replace("\\", "/")
            local_path = os.path.join(_APP_DIR, rel_norm)
            if not os.path.isfile(local_path):
                errors.append(f"{rel_norm}: нет локально")
                continue
            try:
                with open(local_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError as e:
                errors.append(f"{rel_norm}: {e}")
                continue
            res = _github_push_via_api(owner, repo, branch, rel_norm, token, text, msg)
            if res.get("ok"):
                pushed.append(rel_norm)
            else:
                err_s = str(res.get("error") or "push failed")
                if res.get("http_code") == 403:
                    api_forbidden = True
                errors.append(f"{rel_norm}: {err_s}")

    if not pushed:
        err = "; ".join(errors[:5]) or "Не удалось отправить файлы"
        if api_forbidden or (access.get("ok") and not access.get("can_push")):
            err = (access.get("hint") or err)[:500]
        GITHUB_APP_LAST_PUBLISH_ERROR = err[:500]
        return {"ok": False, "error": err, "version": ver, "errors": errors, "access": access}

    GITHUB_APP_LAST_PUBLISH_AT = time.strftime("%Y-%m-%d %H:%M:%S")
    GITHUB_APP_LAST_PUBLISH_ERROR = ""
    try:
        _persist_settings_merge(
            {
                "github_app_last_publish_at": GITHUB_APP_LAST_PUBLISH_AT,
                "github_app_last_publish_error": "",
            }
        )
    except Exception:
        pass
    append_coach_feed_from_inbox(
        f"Опубликовано на GitHub {owner}/{repo}@{branch}: v{ver}, файлов {len(pushed)}"
    )
    return {
        "ok": True,
        "version": ver,
        "pushed_files": pushed,
        "errors": errors,
        "repo": f"{owner}/{repo}",
        "branch": branch,
        "method": "api",
    }


def _adb_package_installed(serial: Optional[str], package: str) -> bool:
    pkg = (package or "").strip()
    if not pkg:
        return False
    try:
        if serial:
            r = run_adb_serial(serial, ["shell", "pm", "path", pkg])
        else:
            r = run_adb(["shell", "pm", "path", pkg])
        out = (r.stdout or "") if r else ""
        return bool(r and r.returncode == 0 and "package:" in out)
    except Exception:
        return False


def mumu_desktop_missing_apps_hint(serial: Optional[str]) -> Optional[str]:
    """Если OfferUp / Super Proxy не установлены — подсказка оператору."""
    if not serial:
        serial = ADB_PORT
    if not serial:
        return None
    missing: List[str] = []
    for pkg, label in _DESKTOP_APP_PACKAGES:
        if not _adb_package_installed(serial, pkg):
            missing.append(label)
    if not missing:
        return None
    return (
        "На эмуляторе не установлены: "
        + ", ".join(missing)
        + ". Установите приложения (иконки на рабочем столе MuMu), затем продолжайте."
    )


def pending_ai_actions_for_poll() -> List[dict]:
    with _pending_ai_actions_lock:
        return [
            {
                "id": x.get("id"),
                "ts": x.get("ts"),
                "port": x.get("port"),
                "kind": x.get("kind") or "recovery",
                "context": x.get("context"),
                "situation": x.get("situation"),
                "note": x.get("note"),
                "recovery_actions": x.get("recovery_actions"),
                "coach_actions": x.get("coach_actions") or [],
                "has_preview": bool(x.get("screen_preview_b64")),
            }
            for x in _pending_ai_actions
        ]


def _queue_pending_coach_actions(
    serial: Optional[str],
    *,
    context: str,
    coach_actions: List[dict],
    screen_preview_b64: Optional[str],
    merge_key: Optional[str] = None,
) -> Tuple[int, bool]:
    acts = [x for x in (coach_actions or []) if isinstance(x, dict)]
    if not acts:
        return 0, False
    port = (serial or ADB_PORT or "").strip()
    mk = (merge_key or "").strip() or _coach_merge_key(acts)
    payload = {
        "kind": "coach",
        "context": (context or "")[:1400],
        "coach_actions": acts,
        "recovery_actions": [],
        "screen_preview_b64": (screen_preview_b64 or "")[:400000] if screen_preview_b64 else "",
    }
    rid, created = _upsert_pending_ai_action(port, mk, payload)
    if rid and COACH_PENDING_AUTO_APPROVE:
        row_copy: Optional[dict] = None
        with _pending_ai_actions_lock:
            row = next((x for x in _pending_ai_actions if int(x.get("id") or 0) == rid), None)
            if row:
                row_copy = dict(row)
                _pending_ai_actions[:] = [x for x in _pending_ai_actions if int(x.get("id") or 0) != rid]
        if row_copy:
            _auto_execute_pending_row(row_copy, port)
        return 0, created
    return rid, created


def _queue_pending_screen_recovery(
    serial: Optional[str],
    *,
    context: str,
    situation: object,
    note: str,
    recovery_actions: List[str],
    screen_preview_b64: Optional[str],
) -> Tuple[int, bool]:
    acts = [str(a).strip() for a in (recovery_actions or []) if str(a).strip()]
    low = [a.lower() for a in acts]
    if not acts or all(a in ("none", "") for a in low):
        return 0, False
    if not any("launch" in a or "offerup" in a or "dismiss" in a or "inbox" in a for a in low):
        return 0, False
    port = (serial or ADB_PORT or "").strip()
    mk = _recovery_merge_key(acts)
    payload = {
        "kind": "recovery",
        "context": (context or "")[:1400],
        "situation": situation,
        "note": (note or "")[:200],
        "recovery_actions": acts,
        "coach_actions": [],
        "screen_preview_b64": (screen_preview_b64 or "")[:400000] if screen_preview_b64 else "",
    }
    rid, created = _upsert_pending_ai_action(port, mk, payload)
    if rid and COACH_PENDING_AUTO_APPROVE:
        row_copy: Optional[dict] = None
        with _pending_ai_actions_lock:
            row = next((x for x in _pending_ai_actions if int(x.get("id") or 0) == rid), None)
            if row:
                row_copy = dict(row)
                _pending_ai_actions[:] = [x for x in _pending_ai_actions if int(x.get("id") or 0) != rid]
        if row_copy:
            _auto_execute_pending_row(row_copy, port)
        return 0, created
    return rid, created


def _execute_recovery_actions(actions: List[str], serial: Optional[str], log_func: Callable[[str], None]) -> bool:
    if not isinstance(actions, list):
        return False
    low_actions = [str(a).lower() for a in actions]
    did = False
    if any("dismiss" in a for a in low_actions):
        _offerup_dismiss_if_popup(serial)
        log_func("recovery: dismiss_popup")
        did = True
    if any("launch" in a or "offerup" in a for a in low_actions):
        _offerup_launch_app(serial, log_func)
        log_func("recovery: launch_offerup_app")
        did = True
    if any("inbox" in a for a in low_actions):
        if offerup_open_inbox_tab(serial, log_func):
            log_func("recovery: open_inbox (UI)")
            did = True
        else:
            notify_operator_help(serial, "нажмите вкладку Inbox в OfferUp")
            log_func("recovery: open_inbox — нужна помощь оператора")
    return did


def _resolve_adb_port_for_api(body: dict, *, query_port: str = "") -> Tuple[Optional[str], Optional[str]]:
    global ADB_PORT, _ADB_RESOLVED
    port = (body.get("adb_port") or query_port or ADB_PORT or "").strip()
    if not port:
        return None, "Укажите adb_port или выберите порт MuMu в настройках"
    if port != ADB_PORT:
        ADB_PORT = port
        _ADB_RESOLVED = None
    adb_connect()
    return port, None


def inbox_scan_abort_request() -> None:
    global _inbox_abort_flag
    with _inbox_abort_lock:
        _inbox_abort_flag = True


def inbox_scan_abort_clear() -> None:
    global _inbox_abort_flag
    with _inbox_abort_lock:
        _inbox_abort_flag = False


def inbox_scan_abort_is_set() -> bool:
    with _inbox_abort_lock:
        return bool(_inbox_abort_flag)


_force_stop_lock = threading.Lock()
_force_stop_flag = False


def force_stop_is_set() -> bool:
    with _force_stop_lock:
        return bool(_force_stop_flag)


def force_stop_clear() -> None:
    global _force_stop_flag
    with _force_stop_lock:
        _force_stop_flag = False
    inbox_scan_abort_clear()


def force_stop_request(*, log_line: Optional[str] = None) -> None:
    """Принудительно остановить Inbox, авторежим, онбординг и одиночный OfferUp."""
    global _force_stop_flag
    with _force_stop_lock:
        _force_stop_flag = True
    inbox_scan_abort_request()
    with _inbox_manual_lock:
        _inbox_manual_state["stop_requested"] = True
    with _autorun_lock:
        _autorun_state["stop_requested"] = True
    with _onboarding_lock:
        _onboarding_job["stop_requested"] = True
        st = _onboarding_job.get("state")
        if st in ("running", "stopping"):
            _onboarding_job["state"] = "stopping"
            _onboarding_job.setdefault("log", []).append(
                time.strftime("%H:%M:%S ") + "Принудительная остановка…"
            )
    with _offerup_lock:
        if _offerup_job.get("state") == "running":
            _offerup_job["state"] = "stopping"
            _offerup_job.setdefault("log", []).append(
                time.strftime("%H:%M:%S ") + "Принудительная остановка…"
            )
    if log_line:
        _inbox_manual_log_line(log_line)


def force_stop_offerup_apps(ports: Optional[List[str]] = None) -> None:
    seen: set[str] = set()
    for p in ports or []:
        ps = str(p or "").strip()
        if not ps or ps in seen:
            continue
        seen.add(ps)
        try:
            _offerup_force_stop_app(ps)
        except Exception:
            pass
    if not seen:
        ap = (ADB_PORT or "").strip()
        if ap:
            try:
                _offerup_force_stop_app(ap)
            except Exception:
                pass
        else:
            try:
                _offerup_force_stop_app(None)
            except Exception:
                pass


def _inbox_rows_stable_fingerprint(rows: List[dict]) -> str:
    """Без «N min ago» — иначе fp всегда меняется и скролл не останавливается."""
    parts: List[str] = []
    for r in rows:
        name = str(r.get("name") or "")
        sn = str(r.get("snippet") or "")
        sn = re.sub(r"\b\d{1,2}:\d{2}\s*(am|pm)?\b", "", sn, flags=re.I)
        sn = re.sub(
            r"\b(\d+\s*(minute|minutes|min|hour|hours|day|days)\s+ago|an hour ago|yesterday|today)\b",
            "",
            sn,
            flags=re.I,
        )
        sn = _collapse_ws(sn)[:56]
        parts.append(f"{name.lower()}:{sn.lower()}")
    return "|".join(parts)


def _snippet_asks_how_it_works(snippet: str) -> bool:
    s = _collapse_ws(snippet or "").lower()
    if not s:
        return False
    if "как это работает" in s:
        return True
    if "how does this work" in s or "how does it work" in s:
        return True
    return False


def _offerup_joined_visible_texts_lower(
    serial: Optional[str],
    root: Optional[ET.Element] = None,
) -> str:
    root_use = root
    if root_use is None:
        root_use = dump_ui_serial(serial) if serial else dump_ui()
    if root_use is None:
        return ""
    parts = [_collapse_ws(t).lower() for t in _ui_visible_texts(root_use)]
    return " ".join(x for x in parts if x)


def _offerup_chat_shows_buyer_sent_link(
    serial: Optional[str],
    link_row: dict,
    root: Optional[ET.Element] = None,
) -> bool:
    """Экран чата: уже виден URL из очереди (x.gd или полная ссылка) — не дублировать отправку."""
    blob = _offerup_joined_visible_texts_lower(serial, root=root)
    if not blob:
        return False
    candidates: List[str] = []
    for key in ("fish_link", "url", "bot_url"):
        u = str(link_row.get(key) or "").strip()
        if u:
            candidates.append(u.lower())
    for c in candidates:
        if not c:
            continue
        if c in blob:
            return True
        if len(c) > 40 and c[-40:] in blob:
            return True
        if len(c) > 40 and c[:40] in blob:
            return True
        try:
            pu = urllib.parse.urlparse(c)
            host = (pu.netloc or "").split("@")[-1].lower()
            if "x.gd" in host or host == "x.gd":
                tail = (pu.path or "").strip("/")
                if tail and tail in blob:
                    return True
        except Exception:
            pass
    return False


def _created_link_row_by_id(created_id: object) -> Optional[dict]:
    try:
        cid = int(created_id or 0)
    except (TypeError, ValueError):
        return None
    if not cid:
        return None
    with _created_links_lock:
        for row in _created_links:
            if int(row.get("id") or 0) == cid:
                return dict(row)
    return None


def _normalize_inbox_match_name(name: str) -> str:
    s = _collapse_ws(name or "").lower()
    s = s.replace("…", "...")
    for ch in ("\u2019", "\u2018", "\u00b4", "`", "\u02bc"):
        s = s.replace(ch, "'")
    s = re.sub(r"[^a-z0-9'.@\s-]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\.{2,}$", "", s).strip()
    return s


def _inbox_name_tokens(name: str) -> List[str]:
    n = _normalize_inbox_match_name(name)
    return [t for t in re.split(r"[\s.']+", n) if len(t) >= 2]


def _inbox_last_name_token(name: str) -> str:
    toks = _inbox_name_tokens(name)
    return toks[-1] if toks else ""


def _link_queue_pending_rows(port: Optional[str] = None) -> List[dict]:
    sent_ids = _pending_created_sent_ids()
    out: List[dict] = []
    with _link_queue_lock:
        rows = list(reversed(_link_queue))
    for row in rows:
        cid = int(row.get("created_link_id") or 0)
        if cid and cid in sent_ids:
            continue
        if port:
            rp = str(row.get("port") or "").strip()
            if rp and rp != str(port).strip():
                continue
        out.append(dict(row))
    return out


def _link_queue_names_hint(port: Optional[str] = None, limit: int = 10) -> str:
    names: List[str] = []
    for row in _link_queue_pending_rows(port=None):
        nm = _effective_link_seller_name(row)
        if _is_generic_link_seller_name(nm):
            continue
        rp = str(row.get("port") or "").strip()
        label = f"{nm}@{rp}" if rp else nm
        if label not in names:
            names.append(label)
        if len(names) >= limit:
            break
    return ", ".join(names[:limit])


def _row_matches_inbox_seller(row: dict, inbox_name: str) -> bool:
    rn = _effective_link_seller_name(row)
    if _is_generic_link_seller_name(rn):
        return False
    if _inbox_row_name_matches(inbox_name, rn) or _inbox_row_name_matches(rn, inbox_name):
        return True
    for key in ("title", "listing_title", "seller_name"):
        alt = _collapse_ws(str(row.get(key) or ""))
        if alt and _inbox_row_name_matches(inbox_name, alt):
            return True
    in_toks = _inbox_name_tokens(inbox_name)
    row_toks = _inbox_name_tokens(rn)
    if len(in_toks) >= 2 and len(row_toks) >= 2 and in_toks[0] == row_toks[0]:
        if in_toks[-1] == row_toks[-1] or (
            len(in_toks[-1]) >= 3 and in_toks[-1] == row_toks[-1][: len(in_toks[-1])]
        ):
            return True
    return False


def _find_pending_link_for_inbox_name(
    name: str,
    port: Optional[str] = None,
) -> Optional[dict]:
    n = _normalize_inbox_match_name(name)
    if not n or _is_bad_captured_offerup_name(n):
        return None
    sent_ids = _pending_created_sent_ids()

    def _pick(rows: List[dict]) -> Optional[dict]:
        for row in rows:
            cid = int(row.get("created_link_id") or 0)
            if cid and cid in sent_ids:
                continue
            if _row_matches_inbox_seller(row, name):
                return dict(row)
        ln = _inbox_last_name_token(name)
        if len(ln) >= 3:
            hits = []
            for row in rows:
                cid = int(row.get("created_link_id") or 0)
                if cid and cid in sent_ids:
                    continue
                rn = _effective_link_seller_name(row)
                if _is_generic_link_seller_name(rn):
                    continue
                if _inbox_last_name_token(rn) == ln:
                    hits.append(dict(row))
            if len(hits) == 1:
                return hits[0]
        return None

    if port:
        hit = _pick(_link_queue_pending_rows(port=port))
        if hit:
            return hit
    hit = _pick(_link_queue_pending_rows(port=None))
    if hit:
        return hit
    sent_ids = _pending_created_sent_ids()
    with _created_links_lock:
        for cl in reversed(_created_links):
            cid = int(cl.get("id") or 0)
            if cid and cid in sent_ids:
                continue
            cn = _normalize_inbox_match_name(str(cl.get("name") or ""))
            if _is_generic_link_seller_name(cn):
                continue
            if _inbox_row_name_matches(name, cn):
                with _link_queue_lock:
                    for row in reversed(_link_queue):
                        if int(row.get("created_link_id") or 0) == cid:
                            return dict(row)
                return {
                    "created_link_id": cid,
                    "name": str(cl.get("name") or ""),
                    "url": cl.get("fish_link") or cl.get("url") or "",
                    "fish_link": cl.get("fish_link") or "",
                    "bot_url": cl.get("bot_url") or "",
                    "search_link": cl.get("search_link") or "",
                    "search_id": cl.get("search_id") or "",
                    "link_provider": cl.get("link_provider") or "kartatai",
                    "source_url": cl.get("ad_url") or "",
                    "port": cl.get("port") or "",
                }
    return None


def _find_fallback_pending_link_for_port(port: Optional[str]) -> Optional[dict]:
    sent_ids = _pending_created_sent_ids()
    port_s = (port or "").strip()
    with _link_queue_lock:
        candidates = list(reversed(_link_queue))
    same_port: List[dict] = []
    any_port: List[dict] = []
    for row in candidates:
        cid = int(row.get("created_link_id") or 0)
        if cid and cid in sent_ids:
            continue
        any_port.append(row)
        if port_s and str(row.get("port") or "").strip() == port_s:
            same_port.append(row)
    pool = same_port or any_port
    if len(pool) == 1:
        return dict(pool[0])
    return None


def _remove_link_queue_item_by_created_id(created_id: object) -> None:
    try:
        cid = int(created_id or 0)
    except (TypeError, ValueError):
        return
    if not cid:
        return
    with _link_queue_lock:
        _link_queue[:] = [x for x in _link_queue if int(x.get("created_link_id") or 0) != cid]
    save_persistent_state()


def _offerup_try_capture_seller_from_chat(
    serial: Optional[str],
    created_id: object,
    log_func: Callable[[str], None],
) -> None:
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return
    name = _offerup_infer_chat_name(root, "")
    if name and not _is_generic_link_seller_name(name):
        _update_pending_link_display_name(created_id, name)
        log_func(f"OfferUp: имя продавца «{name}» сохранено в ссылке")


def _update_pending_link_display_name(created_id: object, name: str) -> None:
    clean = _collapse_ws(name or "")
    if not clean or _is_generic_link_seller_name(clean):
        return
    try:
        cid = int(created_id or 0)
    except (TypeError, ValueError):
        cid = 0
    if not cid:
        return
    with _link_queue_lock:
        for row in _link_queue:
            if int(row.get("created_link_id") or 0) == cid:
                row["name"] = clean
    with _created_links_lock:
        for row in _created_links:
            if int(row.get("id") or 0) == cid:
                row["name"] = clean
    save_persistent_state()


def _offerup_infer_chat_name(root: Optional[ET.Element], sent_template: str = "") -> str:
    if root is None:
        return ""
    sent_low = _collapse_ws(sent_template or "").lower()
    for txt in _ui_visible_texts(root):
        s = _collapse_ws(txt)
        low = s.lower()
        if not s:
            continue
        if low in ("message", "messages", "delivered", "ask", "send", "open", "back", "navigate up", "more options", "options"):
            continue
        if low.startswith("back") or low.endswith("back"):
            continue
        if sent_low and sent_low[:28] and sent_low[:28] in low:
            continue
        if low.startswith("active ") or low.startswith("you:") or low.startswith("hi, "):
            continue
        if re.search(r"\$?\d+[,.]?\d*$", s) or re.search(r"\b(am|pm)\b", low):
            continue
        if "," in s and re.search(r"\b[A-Z]{2}\b", s):
            continue
        if 2 <= len(s) <= 40:
            return s
    return ""


def _offerup_back_to_inbox(
    serial: Optional[str],
    log_func: Callable[[str], None],
    root: Optional[ET.Element] = None,
) -> None:
    root_passed = root
    for attempt in range(2):
        root_use = root_passed if attempt == 0 and root_passed is not None else (
            dump_ui_serial(serial) if serial else dump_ui()
        )
        if root_use is None:
            break
        texts = " ".join(_ui_visible_texts(root_use))
        if _offerup_ui_looks_like_inbox_list_screen(root_use, texts) and not _offerup_ui_is_discussion_chat(
            root_use
        ):
            return
        if not _offerup_ui_is_discussion_chat(root_use) and not _offerup_chat_has_message_compose(root_use):
            return
        _offerup_tap_chat_back(serial, log_func, root=root_use)
        time.sleep(0.28)
    root_chk = dump_ui_serial(serial) if serial else dump_ui()
    if root_chk is not None and _offerup_ui_is_discussion_chat(root_chk):
        log_func("Inbox scan: всё ещё в чате — вкладка Inbox")
        offerup_open_inbox_tab(serial, log_func)
        time.sleep(0.35)


def _send_xgd_template_link_to_current_chat(
    link: str,
    templates: Optional[List[str]],
    serial: Optional[str],
    log_func: Callable[[str], None],
) -> bool:
    ok, short_or_err = shorten_url(link)
    if not ok:
        log_func(f"Inbox scan: x.gd shorten failed: {short_or_err}")
        return False
    messages = _build_messages(templates or _normalize_templates_from_body({}), short_or_err)
    if not messages:
        messages = [short_or_err]
    for idx, message in enumerate(messages):
        if not send_text_to_current_chat(message, serial=serial):
            log_func(f"Inbox scan: failed to send templated message {idx + 1}/{len(messages)}")
            return False
        if idx < len(messages) - 1:
            time.sleep(BETWEEN_MESSAGES_SEC)
    return True


def _seller_inbox_block_key(name: str) -> str:
    return _normalize_inbox_match_name(name)


def _is_seller_inbox_blocked(name: str) -> bool:
    key = _seller_inbox_block_key(name)
    if not key:
        return False
    with _inbox_blocked_sellers_lock:
        return key in _inbox_blocked_sellers


def _block_seller_inbox_forever(
    name: str,
    link_row: dict,
    log_func: Callable[[str], None],
    reason: str = "",
) -> None:
    key = _seller_inbox_block_key(name)
    if not key:
        return
    with _inbox_blocked_sellers_lock:
        _inbox_blocked_sellers.add(key)
    cid = int(link_row.get("created_link_id") or 0)
    if cid:
        mark_created_link_sent(cid)
    _remove_link_queue_item_by_created_id(link_row.get("created_link_id"))
    save_persistent_state()
    why = f" ({reason})" if reason else ""
    log_func(f"Inbox scan: {name}: тред закрыт — больше не открываем{why}")


def _looks_like_buyer_chat_line(text: str) -> bool:
    low = _collapse_ws(text or "").lower()
    if low.startswith("you:"):
        return True
    for m in _BUYER_TEMPLATE_MARKERS:
        if m in low:
            return True
    if low.startswith("got it,") or low.startswith("thanks") and "i'll" in low:
        return True
    return False


def _parse_chat_messages_from_root(
    root: ET.Element,
    seller_name: str = "",
) -> List[dict]:
    scr_w, scr_h = screen_size(root)
    seller_key = _normalize_inbox_match_name(seller_name)
    out: List[dict] = []
    for node in root.iter("node"):
        txt = _collapse_ws((node.get("text") or "") + " " + (node.get("content-desc") or ""))
        if not txt or len(txt) < 2 or len(txt) > 400:
            continue
        if _looks_like_inbox_noise(txt) or _looks_like_inbox_time(txt):
            continue
        low = txt.lower()
        if low in ("delivered", "read", "message", "send", "message..."):
            continue
        if seller_key and _normalize_inbox_match_name(txt) == seller_key:
            continue
        if re.match(r"^\d{1,2}:\d{2}\s*(am|pm)?$", low):
            continue
        if "active in the past" in low or low.startswith("active "):
            continue
        if "," in txt and re.search(r"\b[A-Z]{2}\b", txt) and len(txt) < 40:
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        l, t, r, b = pr
        cy = (t + b) // 2
        if scr_h and (cy < int(scr_h * 0.12) or cy > int(scr_h * 0.92)):
            continue
        cx = (l + r) // 2
        is_buyer = _looks_like_buyer_chat_line(txt)
        if not is_buyer and scr_w and cx >= int(scr_w * OFFERUP_CHAT_BUYER_X_FRAC):
            is_buyer = True
        out.append({"cy": cy, "text": txt, "side": "buyer" if is_buyer else "seller"})
    out.sort(key=lambda x: x["cy"])
    return out


def _last_seller_message_in_chat(
    serial: Optional[str],
    seller_name: str = "",
    root: Optional[ET.Element] = None,
) -> str:
    root_use = root
    if root_use is None:
        root_use = dump_ui_serial(serial) if serial else dump_ui()
    if root_use is None:
        return ""
    msgs = _parse_chat_messages_from_root(root_use, seller_name)
    seller_lines = [m["text"] for m in msgs if m.get("side") == "seller"]
    return seller_lines[-1] if seller_lines else ""


def _seller_has_message_needing_inbox_reply(
    serial: Optional[str],
    seller_name: str,
    root: Optional[ET.Element] = None,
) -> bool:
    root_use = root
    if root_use is None:
        root_use = dump_ui_serial(serial) if serial else dump_ui()
    if root_use is None:
        return False
    msgs = _parse_chat_messages_from_root(root_use, seller_name)
    if not msgs:
        return False
    seller_msgs = [m for m in msgs if m.get("side") == "seller"]
    if not seller_msgs:
        return False
    last = msgs[-1]
    return last.get("side") == "seller"


def _infer_reply_from_open_chat(row: dict, serial: Optional[str]) -> str:
    name = str(row.get("name") or "")
    return _last_seller_message_in_chat(serial, name)


def _process_open_inbox_chat(
    row: dict,
    link_row: dict,
    serial: Optional[str],
    log_func: Callable[[str], None],
    message_templates: Optional[List[str]] = None,
    local_llm_inbox: bool = False,
    buyer_name_on_profile: str = "",
    buyer_address_on_profile: str = "",
    use_listing_photo: bool = True,
    root_chat: Optional[ET.Element] = None,
) -> str:
    root0 = root_chat if root_chat is not None else (dump_ui_serial(serial) if serial else dump_ui())
    texts0 = " ".join(_ui_visible_texts(root0)) if root0 is not None else ""
    exit_reason = _offerup_chat_exit_reason(root0, texts0)
    if exit_reason:
        name0 = str(row.get("name") or link_row.get("name") or "OfferUp")
        log_func(f"Inbox scan: {name0}: чат недоступен ({exit_reason}) — выход")
        return "skipped"
    name = str(row.get("name") or link_row.get("name") or "OfferUp")
    if _is_seller_inbox_blocked(name):
        log_func(f"Inbox scan: {name}: в чёрном списке Inbox — пропуск")
        return "skipped"
    snippet = str(row.get("snippet") or "").strip()
    if snippet.lower().startswith("you:"):
        log_func(f"Inbox scan: {name}: последнее сообщение наше — ждём ответа продавца")
        return "skipped"
    if not _looks_like_valid_inbox_preview(snippet):
        snippet = _last_seller_message_in_chat(serial, name, root=root0)
        if snippet and not _looks_like_valid_inbox_preview(snippet):
            snippet = ""
    if not snippet:
        log_func(f"Inbox scan: {name}: продавец ещё не написал — не отвечаем")
        return "skipped"
    if not _seller_has_message_needing_inbox_reply(serial, name, root=root0):
        log_func(f"Inbox scan: {name}: в чате нет нового сообщения от продавца — пропуск")
        return "skipped"
    if not snippet:
        snippet = _last_seller_message_in_chat(serial, name, root=root0)
    cid = int(link_row.get("created_link_id") or 0)

    if _ONLY_CASH_REPLY_RE.search(snippet):
        if send_text_to_current_chat(OFFERUP_ONLY_CASH_REPLY, serial=serial):
            _block_seller_inbox_forever(name, link_row, log_func, reason="only cash")
            return "blocked"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "only_cash_reply_failed", snippet, serial)
        return "alert"

    if _CALL_REQUEST_RE.search(snippet):
        if send_text_to_current_chat(OFFERUP_CALL_DECLINE_REPLY, serial=serial):
            log_func(f"Inbox scan: {name}: отказ от звонка")
            return "answered_question"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "call_decline_failed", snippet, serial)
        return "alert"

    if _snippet_asks_how_it_works(snippet):
        if send_text_to_current_chat(OFFERUP_HOW_IT_WORKS_REPLY, serial=serial):
            log_func(f"Inbox scan: {name}: ответ «как это работает» (Iinк)")
            return "answered_question"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "could_not_send_how_it_works", snippet, serial)
        return "alert"

    if cid and cid in _pending_created_sent_ids():
        log_func(f"Inbox scan: {name}: ссылка уже помечена как отправленная (created_links.sent) — пропуск")
        _remove_link_queue_item_by_created_id(cid)
        return "skipped"
    cr = _created_link_row_by_id(cid)
    if cr and cr.get("sent"):
        log_func(f"Inbox scan: {name}: created link уже sent — пропуск дубликата")
        _remove_link_queue_item_by_created_id(cid)
        return "skipped"
    if _offerup_chat_shows_buyer_sent_link(serial, link_row, root=root0):
        log_func(f"Inbox scan: {name}: в чате уже виден наш URL — помечаем sent, повторно не шлём")
        mark_created_link_sent(cid)
        _remove_link_queue_item_by_created_id(cid)
        return "skipped"

    regex_kind = classify_offerup_reply(snippet)
    kind = regex_kind
    pre_link_msgs: List[str] = []
    block_link = False
    decision: Optional[dict] = None

    if regex_kind in ("neutral", "positive", "none") and _MEET_OR_SHIP_OFFER_RE.search(snippet):
        pre_link_msgs = [SHIP_PREFERENCE_REPLY]
        block_link = True

    if local_llm_inbox and _local_llm_runtime_ready():
        decision = local_llm_offerup_inbox_decide(
            snippet,
            link_row,
            row,
            regex_kind,
            log_func,
            buyer_name_on_profile=buyer_name_on_profile,
            buyer_address_on_profile=buyer_address_on_profile,
            use_listing_photo=use_listing_photo,
            serial=serial,
            root=root0,
        )
        if decision:
            decision = _llm_decision_apply_ask_operator_guard(
                decision, snippet, buyer_address_on_profile, log_func
            )
            ao = bool(decision and decision.get("ask_operator"))
            om = _collapse_ws(str((decision or {}).get("operator_message") or ""))
            if ao:
                om2 = om or "Как ответить продавцу?"
                _remember_inbox_operator_lesson(name, snippet)
                append_coach_inbox_question(name, snippet, om2)
                add_offerup_alert(
                    None,
                    str(link_row.get("source_url") or ""),
                    link_row,
                    "llm_asks_operator",
                    om2,
                    serial,
                )
                log_func(f"Inbox scan: {name}: вопрос в чате «ИИ-коуч» и алерт")
                return "alert"
            ni = _normalize_llm_intent(decision.get("intent"))
            if ni:
                kind = ni
            elif str(decision.get("intent") or "").strip().lower() == "none":
                if _is_seller_apology_not_refusal(snippet):
                    kind = "neutral"
                elif regex_kind == "negative" and _is_seller_apology_not_refusal(snippet):
                    kind = "neutral"
            # send_link=false без явного negative/pickup часто даёт ложные «стоп ссылки» — не блокируем линк только из осторожности.
            if decision.get("send_link") is False and ni in ("negative", "pickup_only"):
                block_link = True
            raw_tr = _llm_decision_text_replies(decision)
            if raw_tr:
                pre_link_msgs = _sanitize_llm_reply_messages(
                    raw_tr,
                    buyer_address_on_profile=buyer_address_on_profile,
                    seller_message=snippet,
                )
            if raw_tr and not pre_link_msgs:
                log_func(
                    f"Inbox scan: {name}: LLM text_replies отброшены (роль продавца или пусто) — "
                    "проверьте адрес в настройках"
                )

    if kind == "pickup_only":
        link = str(link_row.get("fish_link") or link_row.get("url") or "")
        if pre_link_msgs:
            if not _send_inbox_text_messages(pre_link_msgs[:1], serial, log_func):
                add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "local_llm_pickup_preface_failed", snippet, serial)
                return "alert"
            time.sleep(0.2)
            if _chat_shows_we_asked_for_ship(serial):
                if send_text_to_current_chat(MEET_FALLBACK_REPLY, serial=serial):
                    time.sleep(0.22)
                    if link and _send_xgd_template_link_to_current_chat(link, message_templates, serial, log_func):
                        mark_created_link_sent(link_row.get("created_link_id"))
                        _remove_link_queue_item_by_created_id(link_row.get("created_link_id"))
                        log_func(f"Inbox scan: {name}: meet fallback + templated link sent")
                        return "sent"
                add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "meet_fallback_failed", snippet, serial)
                return "alert"
            log_func(f"Inbox scan: {name}: pickup_only — ответ LLM отправлен (без дубля)")
            return "answered_question"
        if _chat_shows_we_asked_for_ship(serial):
            if send_text_to_current_chat(MEET_FALLBACK_REPLY, serial=serial):
                time.sleep(0.22)
                if link and _send_xgd_template_link_to_current_chat(link, message_templates, serial, log_func):
                    mark_created_link_sent(link_row.get("created_link_id"))
                    _remove_link_queue_item_by_created_id(link_row.get("created_link_id"))
                    log_func(f"Inbox scan: {name}: meet fallback + templated link sent")
                    return "sent"
            add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "meet_fallback_failed", snippet, serial)
            return "alert"
        if send_text_to_current_chat(SHIP_PREFERENCE_REPLY, serial=serial):
            log_func(f"Inbox scan: {name}: shipping priority reply sent (шаблон)")
            return "answered_question"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "could_not_answer_pickup_only", snippet, serial)
        return "alert"
    if kind == "only_cash":
        if send_text_to_current_chat(OFFERUP_ONLY_CASH_REPLY, serial=serial):
            _block_seller_inbox_forever(name, link_row, log_func, reason="only cash")
            return "blocked"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "only_cash_reply_failed", snippet, serial)
        return "alert"
    if kind == "call_request":
        if send_text_to_current_chat(OFFERUP_CALL_DECLINE_REPLY, serial=serial):
            log_func(f"Inbox scan: {name}: отказ от звонка")
            return "answered_question"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "call_decline_failed", snippet, serial)
        return "alert"
    if kind == "negative":
        if _is_seller_apology_not_refusal(snippet):
            sorry_reply = "No worries! Can you ship to my address? I'll cover shipping."
            if buyer_address_on_profile:
                sorry_reply = f"No worries — can you ship to {buyer_address_on_profile}? I'll cover shipping."
            if send_text_to_current_chat(sorry_reply, serial=serial):
                log_func(f"Inbox scan: {name}: извинение продавца — предложили доставку")
                return "answered_question"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "negative_reply", snippet, serial)
        log_func(f"Inbox scan: {name}: negative reply, alert created")
        return "alert"
    if kind == "question":
        q_reply = pre_link_msgs[0] if pre_link_msgs else OFFERUP_INBOX_GENERIC_QUESTION_FALLBACK
        if send_text_to_current_chat(q_reply, serial=serial):
            log_func(f"Inbox scan: {name}: answered question ({'LLM' if pre_link_msgs else 'default'})")
            return "answered_question"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "could_not_answer_question", snippet, serial)
        return "alert"
    link = str(link_row.get("fish_link") or link_row.get("url") or "")
    if kind in ("positive", "neutral") or kind == "none":
        if kind == "none" and decision and decision.get("send_link") is False and not pre_link_msgs:
            log_func(f"Inbox scan: {name}: LLM intent=none, send_link=false — без ответа продавца не пишем")
            return "skipped"
        if block_link and kind in ("positive", "neutral"):
            add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "local_llm_hold_link", snippet, serial)
            log_func(f"Inbox scan: {name}: LLM set send_link=false, link not sent")
            return "alert"
        text_sent = False
        if pre_link_msgs and not (kind == "none" and decision and decision.get("send_link") is False):
            if not _send_inbox_text_messages(pre_link_msgs, serial, log_func):
                add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "local_llm_pre_reply_failed", snippet, serial)
                return "alert"
            text_sent = True
            time.sleep(0.35)
        elif pre_link_msgs and kind == "none":
            log_func(f"Inbox scan: {name}: LLM send_link=false — текст не отправляем")
            return "skipped"
        if decision and decision.get("send_link") is False and kind in ("positive", "neutral"):
            log_func(f"Inbox scan: {name}: LLM send_link=false — ссылку не отправляем")
            if text_sent:
                return "answered_question"
            add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "local_llm_hold_link", snippet, serial)
            return "alert"
        if _offerup_chat_shows_buyer_sent_link(serial, link_row, root=root0):
            log_func(f"Inbox scan: {name}: после текста ссылка уже в чате — не дублируем")
            mark_created_link_sent(link_row.get("created_link_id"))
            _remove_link_queue_item_by_created_id(link_row.get("created_link_id"))
            return "skipped"
        if link and _send_xgd_template_link_to_current_chat(link, message_templates, serial, log_func):
            mark_created_link_sent(link_row.get("created_link_id"))
            _remove_link_queue_item_by_created_id(link_row.get("created_link_id"))
            log_func(f"Inbox scan: {name}: templated x.gd link sent")
            return "sent"
        add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "could_not_send_fish_link", snippet, serial)
        return "alert"
    add_offerup_alert(None, str(link_row.get("source_url") or ""), link_row, "inbox_unhandled_kind", snippet, serial)
    log_func(f"Inbox scan: {name}: unhandled reply kind after LLM: {kind}")
    return "alert"


def offerup_process_inbox_replies(
    serial: Optional[str],
    log_func: Callable[[str], None],
    should_stop: Optional[Callable[[], bool]] = None,
    max_replies: int = OFFERUP_INBOX_MAX_REPLIES,
    max_scrolls: int = OFFERUP_INBOX_MAX_SCROLLS,
    open_wait: float = OFFERUP_INBOX_OPEN_WAIT_SEC,
    scan_timeout: float = OFFERUP_INBOX_SCAN_TIMEOUT_SEC,
    message_templates: Optional[List[str]] = None,
    local_llm_inbox: bool = False,
    buyer_name_on_profile: str = "",
    buyer_address_on_profile: str = "",
    screen_assist: bool = True,
    use_listing_photo: bool = True,
) -> int:
    if should_stop and should_stop():
        return 0

    def merged_stop() -> bool:
        if force_stop_is_set():
            return True
        if should_stop and should_stop():
            return True
        return inbox_scan_abort_is_set()

    log_func(f"Inbox scan: start, max {max_replies}")
    _inbox_scan_t0 = time.monotonic()
    if local_llm_inbox and _local_llm_runtime_ready():
        log_func("Inbox scan: local LLM assist is ON for this run")
    log_func("Inbox scan: открываю Inbox…")
    if serial:
        adb_connect_serial(serial)
    root_prefetch = _offerup_dump_nav_after_settle(serial, log_func, should_stop=merged_stop)
    opened = False
    if root_prefetch is not None:
        log_func("Inbox scan: OfferUp на экране — пробуем без force-stop")
        opened = _offerup_open_inbox(
            serial,
            log_func,
            should_stop=merged_stop,
            open_wait=open_wait,
            restart_app=False,
            prefetched_root=root_prefetch,
        )
    else:
        fg = _adb_foreground_package(serial)
        log_func(f"Inbox scan: панель не видна ({fg or 'не в фокусе'}) — запуск OfferUp")
    if not opened:
        log_func("Inbox scan: перезапуск OfferUp (force-stop + monkey)…")
        opened = _offerup_open_inbox(
            serial,
            log_func,
            should_stop=merged_stop,
            open_wait=open_wait,
            restart_app=True,
        )
    if not opened and screen_assist and _local_llm_vision_ready():
        log_func("Inbox scan: не удалось открыть Inbox — пробуем ИИ по скриншоту (vision)")
        _offerup_try_vision_screen_recovery(serial, "Inbox scan: could not open Inbox (first attempt)", log_func)
        time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
        if _offerup_foreground_is_offerup(serial):
            opened = _offerup_open_inbox(
                serial,
                log_func,
                should_stop=merged_stop,
                open_wait=open_wait,
                restart_app=False,
            )
        if not opened:
            opened = _offerup_open_inbox(
                serial,
                log_func,
                should_stop=merged_stop,
                open_wait=open_wait,
                restart_app=True,
            )
    if not opened:
        log_func("Inbox scan: could not open Inbox")
        return 0
    if not _offerup_ensure_inbox_list_before_scan(serial, log_func, should_stop=merged_stop):
        log_func("Inbox scan: не удалось показать список Inbox")
        return 0
    # Reset deadline AFTER inbox opens. scan_timeout=0 → без лимита.
    scan_deadline: Optional[float] = None
    if float(scan_timeout) > 0:
        scan_deadline = time.monotonic() + max(3.0, float(scan_timeout))
        log_func(f"Inbox scan: лимит скана {int(scan_timeout)} с")
    else:
        log_func("Inbox scan: лимит скана отключён (scan_timeout=0)")
    processed = 0
    seen: set[str] = set()
    last_page_fp = ""
    unchanged_pages = 0
    empty_swipes = 0
    for scroll_idx in range(max(0, int(max_scrolls)) + 1):
        if merged_stop():
            log_func("Inbox scan: остановлено (стоп или авторежим)")
            break
        if scan_deadline is not None and time.monotonic() > scan_deadline:
            log_func(f"Inbox scan: timeout {int(scan_timeout)}s, continue autorun")
            break
        _offerup_dismiss_if_popup(serial)
        _offerup_dismiss_post_item_if_open(serial, log_func)
        root = dump_ui_serial(serial) if serial else dump_ui()
        if root is None:
            break
        rows = _offerup_collect_inbox_rows(root)
        if not rows:
            empty_swipes += 1
            nodes_dbg, _, _ = _offerup_inbox_list_nodes(root)
            sample = "; ".join(n["text"][:32] for n in nodes_dbg[:10])
            if sample:
                log_func(f"Inbox scan: UI в ленте (отладка): {sample}")
            log_func(
                f"Inbox scan: на экране {scroll_idx + 1} нет строк с ответом продавца "
                f"(только You: или заголовок объявления)"
            )
        else:
            empty_swipes = 0
        page_fp = _inbox_rows_stable_fingerprint(rows)
        if page_fp and page_fp == last_page_fp:
            unchanged_pages += 1
        else:
            unchanged_pages = 0
        last_page_fp = page_fp
        preview = "; ".join(f"{r.get('name','?')} -> {str(r.get('snippet',''))[:36]}" for r in rows[:6])
        log_func(
            f"Inbox scan: ответы продавцов (сверху вниз) {len(rows)} на экране {scroll_idx + 1}"
            + (f" | {preview}" if preview else "")
        )
        opened_any = False
        for row in rows:
            if processed >= max_replies:
                _offerup_ensure_back_from_chat(serial, log_func)
                return processed
            if scan_deadline is not None and time.monotonic() > scan_deadline:
                log_func(f"Inbox scan: timeout {int(scan_timeout)}s, continue autorun")
                _offerup_ensure_back_from_chat(serial, log_func)
                return processed
            key = _normalize_inbox_match_name(str(row.get("name") or ""))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            sname = str(row.get("name") or "")
            snip = str(row.get("snippet") or "")
            if _is_bad_captured_offerup_name(sname):
                continue
            if _inbox_row_is_sold_notice(row):
                log_func(f"Inbox scan: skip {sname or 'OfferUp'} — объявление продано («{snip[:50]}»)")
                lr_sold = _find_pending_link_for_inbox_name(sname, port=serial) if sname else None
                if lr_sold:
                    _remove_link_queue_item_by_created_id(lr_sold.get("created_link_id"))
                continue
            if _is_seller_inbox_blocked(sname):
                log_func(f"Inbox scan: skip {sname} — ранее закрыт")
                continue
            link_row = _find_pending_link_for_inbox_name(sname, port=serial)
            if not link_row:
                qhint = _link_queue_names_hint(serial)
                log_func(
                    f"Inbox scan: skip {sname} — в Inbox есть ответ «{snip[:40]}», "
                    f"но нет ссылки в очереди с таким именем"
                    + (f" | в очереди: {qhint}" if qhint else " | очередь пуста")
                )
                append_coach_feed_from_inbox(
                    f"[Inbox] {sname} написал «{snip[:60]}» — но ссылки в очереди нет. "
                    f"Сначала откройте объявление через Chrome (вкладка OfferUp) чтобы создать ссылку."
                    + (f" В очереди сейчас: {qhint}." if qhint else "")
                )
                continue
            if merged_stop():
                _offerup_ensure_back_from_chat(serial, log_func)
                return processed
            try:
                tx, ty = row["tap"]
                log_func(f"Inbox scan: tap {sname} → ({tx},{ty})")
                if not _offerup_tap_xy(serial, (tx, ty), log_func=log_func):
                    log_func(f"Inbox scan: skip tap {sname} — зона Post/навигации")
                    continue
                opened_any = True
                root_chat = None
                if SmartWaiter is not None and serial:

                    def _chat_open(root: Optional[ET.Element]) -> bool:
                        if root is None:
                            return False
                        return bool(
                            _offerup_find_chat_back_xy(root)
                            or _offerup_chat_has_message_compose(root)
                        )

                    root_chat = SmartWaiter(serial, poll=0.15).wait_until(
                        _chat_open,
                        timeout=4.0,
                        should_stop=merged_stop,
                    )
                else:
                    _chat_open_deadline = time.monotonic() + 4.0
                    while time.monotonic() < _chat_open_deadline:
                        time.sleep(0.3)
                        root_chat = dump_ui_serial(serial) if serial else dump_ui()
                        if root_chat is None:
                            continue
                        if _offerup_find_chat_back_xy(root_chat) or _offerup_chat_has_message_compose(
                            root_chat
                        ):
                            break
                if root_chat is None:
                    log_func(f"Inbox scan: {sname}: чат не открылся (нет UI dump)")
                    continue
                _offerup_dismiss_blockers_from_root(serial, root_chat, log_func)
                if merged_stop():
                    return processed
                texts_chat = " ".join(_ui_visible_texts(root_chat))
                exit_reason = _offerup_chat_exit_reason(root_chat, texts_chat)
                if exit_reason:
                    log_func(f"Inbox scan: {sname}: чат недоступен ({exit_reason})")
                    continue
                result = _process_open_inbox_chat(
                    row,
                    link_row,
                    serial,
                    log_func,
                    message_templates=message_templates,
                    local_llm_inbox=local_llm_inbox,
                    buyer_name_on_profile=buyer_name_on_profile,
                    buyer_address_on_profile=buyer_address_on_profile,
                    use_listing_photo=use_listing_photo,
                    root_chat=root_chat,
                )
                if result in ("sent", "answered_question", "alert", "blocked"):
                    processed += 1
            finally:
                _offerup_ensure_back_from_chat(serial, log_func)
        if processed >= max_replies:
            break
        if empty_swipes >= 2 and scroll_idx >= 1:
            log_func("Inbox scan: пустой список после свайпов — конец ленты")
            break
        if unchanged_pages >= 2:
            log_func("Inbox scan: список не меняется после свайпа — стоп скролла")
            break
        _offerup_ensure_back_from_chat(serial, log_func)
        _offerup_ensure_inbox_list_before_scan(serial, log_func, should_stop=merged_stop)
        scr_w, scr_h = screen_size(root) if root is not None else (0, 0)
        if not scr_h:
            break
        sx = str(scr_w // 2 or 320)
        y1 = str(int(scr_h * 0.78))
        y2 = str(int(scr_h * 0.34))
        if serial:
            run_adb_serial(serial, ["shell", "input", "swipe", sx, y1, sx, y2, "520"])
        else:
            run_adb(["shell", "input", "swipe", sx, y1, sx, y2, "520"])
        log_func(f"Inbox scan: swipe {sx},{y1}->{sx},{y2}")
        time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
        if merged_stop():
            break
        if not opened_any and scroll_idx >= 1 and not rows:
            break
    log_func(f"Inbox scan: processed {processed} за {time.monotonic() - _inbox_scan_t0:.1f}s")
    return processed


def _offerup_inbox_fallback_by_seller_names(
    serial: Optional[str],
    log_func: Callable[[str], None],
    should_stop: Callable[[], bool],
    max_replies: int,
    message_templates: Optional[List[str]] = None,
    local_llm_inbox: bool = False,
    buyer_name_on_profile: str = "",
    buyer_address_on_profile: str = "",
    use_listing_photo: bool = True,
    scan_deadline: float = 0.0,
) -> int:
    """Если парсер ленты пустой — ищем каждого продавца из очереди по имени в UI."""
    sellers = _pending_inbox_seller_names(16)
    if not sellers:
        return 0
    log_func("Inbox scan: fallback — поиск по именам: " + ", ".join(sellers[:8]))
    done = 0
    for sname in sellers:
        if done >= max_replies or should_stop():
            break
        if scan_deadline and time.monotonic() > scan_deadline:
            break
        _offerup_ensure_inbox_list_before_scan(serial, log_func, should_stop=should_stop)
        row: Optional[dict] = None
        for swipe_i in range(4):
            root = dump_ui_serial(serial) if serial else dump_ui()
            if root is None:
                break
            row = _offerup_find_inbox_row_by_seller(root, sname)
            if row:
                break
            if swipe_i < 3:
                scr_w, scr_h = screen_size(root)
                if scr_h:
                    sx = str(scr_w // 2 or 320)
                    y1 = str(int(scr_h * 0.78))
                    y2 = str(int(scr_h * 0.34))
                    if serial:
                        run_adb_serial(serial, ["shell", "input", "swipe", sx, y1, sx, y2, "520"])
                    else:
                        run_adb(["shell", "input", "swipe", sx, y1, sx, y2, "520"])
                    time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
        if not row:
            log_func(f"Inbox scan: fallback — «{sname}» не найден на экране")
            continue
        link_row = _find_pending_link_for_inbox_name(sname, port=serial)
        if not link_row:
            log_func(f"Inbox scan: fallback — «{sname}» нет в Link-очереди")
            continue
        tx, ty = row["tap"]
        if not _offerup_tap_xy(serial, (tx, ty), log_func=log_func):
            continue
        time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
        _offerup_dismiss_post_item_if_open(serial, log_func)
        root_chat = dump_ui_serial(serial) if serial else dump_ui()
        texts_chat = " ".join(_ui_visible_texts(root_chat)) if root_chat is not None else ""
        exit_reason = _offerup_chat_exit_reason(root_chat, texts_chat)
        if exit_reason:
            log_func(f"Inbox scan: fallback {sname}: {exit_reason}")
            _offerup_tap_chat_back(serial, log_func, root=root_chat)
            time.sleep(0.28)
            continue
        result = _process_open_inbox_chat(
            row,
            link_row,
            serial,
            log_func,
            message_templates=message_templates,
            local_llm_inbox=local_llm_inbox,
            buyer_name_on_profile=buyer_name_on_profile,
            buyer_address_on_profile=buyer_address_on_profile,
            use_listing_photo=use_listing_photo,
            root_chat=root_chat,
        )
        if result in ("sent", "answered_question", "alert"):
            done += 1
        time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
        _offerup_back_to_inbox(serial, log_func)
        time.sleep(OFFERUP_INBOX_STEP_DELAY_SEC)
    return done


def _offerup_parent_map(root: ET.Element) -> Dict[ET.Element, ET.Element]:
    parents: Dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent
    return parents


def _offerup_open_label_score(combo: str) -> int:
    """Чем выше — тем вероятнее это CTA «Open in app» на offerup.com."""
    combo = _collapse_ws(combo or "")
    if not combo or len(combo) > 72:
        return 0
    low = combo.lower()
    compact = re.sub(r"[\s\-_]+", "", combo.upper())
    if compact in ("OPEN", "ÖFFNEN", "ОТКРЫТЬ"):
        return 1200
    if re.fullmatch(r"open", low, re.I):
        return 1100
    if re.search(r"\bopen\b", low) and len(combo) <= 24:
        return 950
    phrases = (
        ("open in app", 1000),
        ("open in the", 920),
        ("open offerup", 980),
        ("open the app", 900),
        ("view in app", 880),
        ("view in offerup", 880),
        ("continue in app", 870),
        ("continue in offerup", 870),
        ("launch offerup", 860),
        ("get the app", 820),
        ("get offerup", 820),
        ("use the app", 800),
        ("use offerup", 800),
        ("открыть в приложении", 900),
        ("открыть offerup", 880),
        ("открыть", 750),
    )
    for phrase, pts in phrases:
        if phrase in low:
            return pts
    if "open" in low and "offerup" in low and len(combo) < 48:
        return 850
    if "open" in low and "app" in low and len(combo) < 40:
        return 840
    if compact == "INSTALL":
        return 0
    if "install" in low and "offerup" in low and len(combo) < 56:
        return 0
    return 0


def _offerup_clickable_center_upward(
    node: ET.Element,
    parents: Dict[ET.Element, ET.Element],
    *,
    max_up: int = 8,
) -> Optional[Tuple[int, int]]:
    cur: Optional[ET.Element] = node
    for _ in range(max_up):
        if cur is None:
            break
        if cur.get("clickable", "false") == "true" and cur.get("enabled", "true") != "false":
            xy = _offerup_node_center(cur)
            if xy:
                return xy
        cur = parents.get(cur)
    return None


def _offerup_find_open_only(root: ET.Element) -> Optional[Tuple[int, int]]:
    """Кнопка OPEN / «open in app» в Chrome WebView (в т.ч. текст на дочернем узле)."""
    parents = _offerup_parent_map(root)
    scr_w, scr_h = _offerup_screen_dims(root)
    best: Optional[Tuple[int, int, int]] = None  # score, y_bottom, xy

    def _consider(node: ET.Element, label: str, base_score: int) -> None:
        nonlocal best
        if base_score <= 0:
            return
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            pr = None
        pkg = (node.get("package") or "").lower()
        score = base_score
        if pr:
            l, t, r, b = pr
            if scr_h > 0 and b > scr_h * 0.55:
                score += 120
            if scr_w > 0 and abs((l + r) // 2 - scr_w // 2) < scr_w * 0.35:
                score += 60
        if any(x in pkg for x in ("chrome", "chromium", "webview", "browser")):
            score += 80
        xy: Optional[Tuple[int, int]] = None
        if node.get("clickable", "false") == "true":
            xy = _offerup_node_center(node)
        else:
            xy = _offerup_clickable_center_upward(node, parents)
        if not xy:
            return
        yb = pr[3] if pr else xy[1]
        if best is None or score > best[0] or (score == best[0] and yb > best[1]):
            best = (score, yb, xy[0], xy[1])

    for node in root.iter("node"):
        label = _offerup_label(node)
        score = _offerup_open_label_score(label)
        if score > 0:
            _consider(node, label, score)
        for child in node:
            cl = _offerup_label(child)
            cs = _offerup_open_label_score(cl)
            if cs > 0:
                _consider(node, cl, cs + 40)

    if best:
        return best[2], best[3]

    def pred(n: ET.Element) -> bool:
        return _offerup_open_label_score(_offerup_label(n)) >= 800

    return _offerup_find_clickable(root, pred)


def _offerup_find_open_heuristic(root: ET.Element) -> Optional[Tuple[int, int]]:
    """Если offerup.com виден, но OPEN не в дереве — тап по типичному баннеру снизу."""
    blob = " ".join(_ui_visible_texts(root)).lower()
    if "offerup" not in blob:
        return None
    if not any(
        x in blob
        for x in (
            "open",
            "install",
            "app",
            "continue",
            "view in",
            "listing",
            "message seller",
            "ask",
        )
    ):
        return None
    scr_w, scr_h = _offerup_screen_dims(root)
    if scr_w < 200 or scr_h < 400:
        return None
    for y_frac in (0.90, 0.84, 0.78, 0.72):
        xy = (scr_w // 2, int(scr_h * y_frac))
        return xy
    return None


def _offerup_find_install_offerup_web(root: ET.Element) -> Optional[Tuple[int, int]]:
    """Первый заход: на offerup.com в Chrome часто зелёная INSTALL вместо OPEN."""

    def pred(n: ET.Element) -> bool:
        pkg = (n.get("package") or "").lower()
        if "vending" in pkg:
            return False
        if not any(x in pkg for x in ("chrome", "chromium", "webview")):
            return False
        t = (n.get("text") or "").strip()
        d = (n.get("content-desc") or "").strip()
        combo = (t + " " + d).strip()
        if not combo:
            return False
        low = combo.lower()
        compact = re.sub(r"\s+", "", combo.upper())
        if compact == "INSTALL":
            return True
        if re.fullmatch(r"install", t.strip(), re.I) or re.fullmatch(r"install", d.strip(), re.I):
            return True
        if "install" in low and "offerup" in low and len(combo) < 56:
            return True
        return False

    return _offerup_find_clickable(root, pred)


def _offerup_find_chrome_launch_cta(root: ET.Element) -> Tuple[Optional[Tuple[int, int]], str]:
    """OPEN приоритетнее INSTALL (первый запуск в браузере). Возвращает (xy, \"OPEN\"|\"INSTALL\"|\"HEURISTIC\"|\"\")."""
    xy = _offerup_find_open_only(root)
    if xy:
        return xy, "OPEN"
    xy = _offerup_find_install_offerup_web(root)
    if xy:
        return xy, "INSTALL"
    xy = _offerup_find_open_heuristic(root)
    if xy:
        return xy, "HEURISTIC"
    return None, ""


def _offerup_find_open(root: ET.Element) -> Optional[Tuple[int, int]]:
    """Chrome/CCT: OPEN или (если первый раз) INSTALL на веб-странице OfferUp."""
    xy, _ = _offerup_find_chrome_launch_cta(root)
    return xy


def _offerup_find_ask(root: ET.Element, *, native_offerup_only: bool = False) -> Optional[Tuple[int, int]]:
    """Message Seller / Ask — SuggestedMessagePill или TextField.Input по resource-id."""
    pkg_filter = "com.offerup" if native_offerup_only else ""

    for node in root.iter("node"):
        rid = (node.get("resource-id") or "")
        if "SuggestedMessagePill" not in rid:
            continue
        if pkg_filter and pkg_filter not in (node.get("package") or ""):
            continue
        if node.get("clickable", "false") != "true":
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            l, t, r, b = pr
            return (l + r) // 2, (t + b) // 2

    for node in root.iter("node"):
        rid = (node.get("resource-id") or "")
        if "FirstMessage.TextField.Input" not in rid and "TextField.Input" not in rid:
            continue
        if pkg_filter and pkg_filter not in (node.get("package") or ""):
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            l, t, r, b = pr
            return (l + r) // 2, (t + b) // 2

    for node in root.iter("node"):
        rid = (node.get("resource-id") or "")
        if rid != "DiscussionScreen":
            continue
        if pkg_filter and pkg_filter not in (node.get("package") or ""):
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if pr:
            l, t, r, b = pr
            return (l + r) // 2, (t + b) // 2

    def pred(n: ET.Element) -> bool:
        if pkg_filter and pkg_filter not in (n.get("package") or ""):
            return False
        t = (n.get("text") or "").strip().lower()
        d = (n.get("content-desc") or "").strip().lower()
        return t == "ask" or d == "ask"

    return _offerup_find_clickable(root, pred)


def _offerup_template_match_variants(needle: str) -> List[str]:
    n = _collapse_ws(needle or "")
    if not n:
        return []
    low = n.lower()
    keys: List[str] = [low]
    if len(low) > 72:
        keys.append(low[:72])
    if len(low) > 42:
        keys.append(low[:42])
    for phrase in (
        "offerup#",
        "paid for your item",
        "review the transaction",
        "seller link",
        "through offerup",
    ):
        if phrase in low:
            keys.append(phrase)
    out: List[str] = []
    seen: set[str] = set()
    for k in keys:
        k = k.strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _offerup_find_template(root: ET.Element, needle: str) -> Optional[Tuple[int, int]]:
    variants = _offerup_template_match_variants(needle)
    if not variants:
        return None
    best_pill: Optional[Tuple[int, int, int]] = None
    for node in root.iter("node"):
        rid = (node.get("resource-id") or "")
        if "SuggestedMessagePill" not in rid:
            continue
        if node.get("clickable", "false") != "true":
            continue
        label = _collapse_ws(_offerup_label(node)).lower()
        if not label:
            continue
        score = 0
        for v in variants:
            if v == label:
                score = max(score, 1000 + len(v))
            elif v in label or label in v:
                score = max(score, 500 + len(v))
        if score <= 0:
            continue
        pr = parse_bounds(node.get("bounds") or "")
        if not pr:
            continue
        l, t, r, b = pr
        area = max(1, (r - l) * (b - t))
        if best_pill is None or score > best_pill[0] or (score == best_pill[0] and area < best_pill[1]):
            best_pill = (score, area, (l + r) // 2, (t + b) // 2)
    if best_pill:
        return best_pill[2], best_pill[3]

    def pred(n: ET.Element) -> bool:
        lab = _offerup_label(n).lower()
        return any(v in lab for v in variants)

    return _offerup_find_clickable(root, pred)


def _offerup_apply_template_message(
    serial: Optional[str],
    tmpl: str,
    timing: Dict[str, float],
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    """Тап по пилюле шаблона или ручной ввод + обязательный Send."""
    log = log_func or _offerup_log
    tapped = _offerup_wait_tap(
        lambda root: _offerup_find_template(root, tmpl),
        timing["template_wait"],
        "Template",
        timing["poll_ui"],
        serial=serial,
        should_stop=should_stop,
        log_func=log,
    )
    if not tapped:
        log("Пилюля шаблона не найдена (длинный текст?) — ввод вручную")
        return send_text_to_current_chat(tmpl, serial, log_func=log)
    time.sleep(0.45)
    if _offerup_confirm_send_after_compose(serial, tmpl, log_func=log):
        return True
    log("После пилюли Send не сработал — повторный ввод")
    return send_text_to_current_chat(tmpl, serial, log_func=log)


def _offerup_wait_tap(
    finder: Callable[[ET.Element], Optional[Tuple[int, int]]],
    timeout_sec: float,
    step_name: str,
    poll_sec: float,
    serial: Optional[str] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    log = log_func or _offerup_log
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if should_stop and should_stop():
            log(f"{step_name} - stopped")
            return False
        root = dump_ui_serial(serial) if serial else dump_ui()
        if root is not None:
            xy = finder(root)
            if xy:
                log(f"{step_name} -> tap {xy}")
                if serial:
                    run_adb_serial(serial, ["shell", "input", "tap", str(xy[0]), str(xy[1])])
                else:
                    tap(xy)
                return True
        time.sleep(poll_sec)
    log(f"{step_name} - timeout {timeout_sec:.0f}s")
    return False


def _offerup_force_stop_app(serial: Optional[str] = None) -> None:
    """Force-stop OfferUp app to prevent stale state accumulation after many iterations."""
    offerup_pkg = "com.offerup"
    if serial:
        run_adb_serial(serial, ["shell", "am", "force-stop", offerup_pkg])
    else:
        run_adb(["shell", "am", "force-stop", offerup_pkg])
    time.sleep(0.3)


def _offerup_wait_open_or_app(
    timeout_sec: float,
    poll_sec: float,
    serial: Optional[str] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    log_func: Optional[Callable[[str], None]] = None,
    listing_url: str = "",
    *,
    skip_open_cta: bool = False,
) -> Tuple[str, Optional[Tuple[int, int]]]:
    """OPEN/INSTALL в Chrome или нативный listing (Ask). Возвращает (state, ask_xy).
    skip_open_cta=True (OFFERUP_TRY_APP_URL_FIRST): не искать OPEN/INSTALL в браузере, только Ask.
    """
    log = log_func or _offerup_log
    t0 = time.monotonic()
    deadline = t0 + timeout_sec
    tapped_open = False
    chrome_scrolls = 0
    chrome_scroll_max = 5
    tried_app_deep_link = False
    idle_sleep = max(0.28, min(float(poll_sec), 0.45))
    listing_url = (listing_url or "").strip()
    skip_open = bool(skip_open_cta or OFFERUP_TRY_APP_URL_FIRST)
    if skip_open:
        log("Режим «сначала OfferUp app»: кнопку OPEN в Chrome не ищем — ждём только Ask")

    def _finish_with_ask(ask_xy: Tuple[int, int], *, after_cta: bool) -> Tuple[str, Tuple[int, int]]:
        return "already_app", ask_xy

    while time.monotonic() < deadline:
        if should_stop and should_stop():
            log("OPEN/app - stopped")
            return "stopped", None
        fg = _adb_foreground_package(serial)
        if not skip_open and not tapped_open and _offerup_fg_is_browser(fg) and chrome_scrolls < chrome_scroll_max:
            elapsed = time.monotonic() - t0
            if elapsed > 3.5 + chrome_scrolls * 4.0:
                _offerup_chrome_scroll_page(serial, log)
                chrome_scrolls += 1
                invalidate_ui_dump_cache(serial)
        if not _offerup_fg_needs_ui_dump(fg, after_open_tap=tapped_open and not skip_open):
            time.sleep(idle_sleep)
            continue
        root = dump_ui_serial(serial) if serial else dump_ui()
        if root is None:
            time.sleep(idle_sleep)
            continue
        if skip_open:
            ask_xy = _offerup_find_ask(root, native_offerup_only=True) or _offerup_find_ask(root)
            if ask_xy:
                _offerup_dismiss_blockers_from_root(serial, root, log)
                ask_xy = _offerup_find_ask(root, native_offerup_only=True) or _offerup_find_ask(root) or ask_xy
                elapsed = time.monotonic() - t0
                log(f"OfferUp listing (Ask {ask_xy}) без OPEN ({elapsed:.1f}s)")
                return "already_app", ask_xy
            if listing_url and not tried_app_deep_link and time.monotonic() - t0 > max(6.0, timeout_sec * 0.35):
                tried_app_deep_link = True
                log("Ask не виден — повторный am start в OfferUp app")
                ok_app, msg_app = _offerup_run_am_view(listing_url, "com.offerup", serial=serial)
                log("am start -p com.offerup: " + (msg_app[:280] if msg_app else "—"))
            time.sleep(idle_sleep)
            continue
        if not tapped_open:
            elapsed = time.monotonic() - t0
            if (
                listing_url
                and not tried_app_deep_link
                and elapsed > max(8.0, timeout_sec * 0.45)
                and _offerup_fg_is_browser(fg)
            ):
                tried_app_deep_link = True
                log("OPEN не найден — пробуем открыть listing в приложении OfferUp")
                ok_app, msg_app = _offerup_run_am_view(listing_url, "com.offerup", serial=serial)
                log("am start -p com.offerup: " + (msg_app[:320] if msg_app else "—"))
                if ok_app:
                    t_app = time.monotonic() + 12.0
                    while time.monotonic() < t_app:
                        root_app = dump_ui_serial(serial) if serial else dump_ui()
                        if root_app is not None:
                            ask_xy = _offerup_find_ask(root_app, native_offerup_only=True)
                            if ask_xy:
                                log(f"OfferUp app listing (Ask {ask_xy}) без кнопки OPEN")
                                return _finish_with_ask(ask_xy, after_cta=False)
                        time.sleep(0.4)
            open_xy, cta_label = _offerup_find_chrome_launch_cta(root)
            if open_xy:
                log(f"{cta_label or 'CTA'} -> tap {open_xy}")
                if serial:
                    run_adb_serial(serial, ["shell", "input", "tap", str(open_xy[0]), str(open_xy[1])])
                else:
                    tap(open_xy)
                tapped_open = True
                pause = 1.0 if cta_label == "INSTALL" else 0.55
                if cta_label == "HEURISTIC":
                    pause = 0.85
                time.sleep(pause)
                continue
            ask_xy = _offerup_find_ask(root, native_offerup_only=True)
            if ask_xy:
                _offerup_dismiss_blockers_from_root(serial, root, log)
                ask_xy = _offerup_find_ask(root, native_offerup_only=True) or ask_xy
                elapsed = time.monotonic() - t0
                log(
                    f"Native OfferUp listing visible (Ask {ask_xy}); skipping OPEN ({elapsed:.1f}s)"
                )
                return _finish_with_ask(ask_xy, after_cta=False)
        else:
            ask_xy = _offerup_find_ask(root)
            if ask_xy:
                _offerup_dismiss_blockers_from_root(serial, root, log)
                ask_xy = _offerup_find_ask(root) or ask_xy
                elapsed = time.monotonic() - t0
                log(f"OfferUp after OPEN, Ask {ask_xy} ({elapsed:.1f}s)")
                return _finish_with_ask(ask_xy, after_cta=True)
        time.sleep(idle_sleep)
    elapsed = time.monotonic() - t0
    if skip_open:
        log(f"Ask не найден ({elapsed:.0f}s / limit {timeout_sec:.0f}s), OPEN не проверялся (app-first)")
    elif tapped_open:
        log(f"OPEN/INSTALL tapped but Ask missing ({elapsed:.0f}s / limit {timeout_sec:.0f}s)")
    else:
        root_dbg = dump_ui_serial(serial) if serial else dump_ui()
        if root_dbg is not None:
            hints = []
            for node in root_dbg.iter("node"):
                lab = _offerup_label(node)
                sc = _offerup_open_label_score(lab)
                if sc >= 700:
                    hints.append(lab[:48])
            if hints:
                log("OPEN candidates on screen: " + " | ".join(hints[:6]))
        log(f"OPEN/app timeout ({elapsed:.0f}s / limit {timeout_sec:.0f}s)")
    return "timeout", None


def _offerup_tap_ask_button(
    serial: Optional[str],
    ask_xy: Optional[Tuple[int, int]],
    timing: Dict[str, float],
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    """Тап Ask: сразу по координатам из wait_open, иначе короткий poll (без повторных 3× dump dismiss)."""
    log = log_func or _offerup_log
    if ask_xy:
        log(f"Ask -> tap {ask_xy}")
        if serial:
            run_adb_serial(serial, ["shell", "input", "tap", str(ask_xy[0]), str(ask_xy[1])])
        else:
            tap(ask_xy)
        return True
    return _offerup_wait_tap(
        _offerup_find_ask,
        min(float(timing.get("ask_wait", OFFERUP_ASK_WAIT_SEC)), 8.0),
        "Ask",
        max(float(timing.get("poll_ui", OFFERUP_POLL_UI_SEC)), 0.25),
        serial=serial,
        should_stop=should_stop,
        log_func=log,
    )

def _offerup_interpret_am_start(r: Optional[subprocess.CompletedProcess]) -> Tuple[bool, str]:
    """am start часто возвращает 0 даже при Error: в stderr — проверяем текст."""
    if not r:
        return False, "adb: нет ответа"
    text = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    if r.returncode != 0:
        return False, (text[:400] if text else f"exit {r.returncode}")
    low = text.lower()
    err_markers = (
        "error:",
        "exception",
        "unable to resolve",
        "does not exist",
        "unknown package",
        "not installed",
        "no activity found",
        "security exception",
    )
    if any(m in low for m in err_markers):
        return False, text[:400]
    return True, text[:220] if text else "ok"


_CHROME_PKG_PROBE_CACHE: Dict[str, Tuple[float, List[str]]] = {}


def _offerup_list_browser_packages_on_device(serial: Optional[str] = None) -> List[str]:
    key = (serial or ADB_PORT or "").strip() or "__default__"
    now = time.monotonic()
    cached = _CHROME_PKG_PROBE_CACHE.get(key)
    if cached and now - cached[0] < 300.0:
        return list(cached[1])
    r = run_adb_serial(serial, ["shell", "pm", "list", "packages"]) if serial else run_adb(
        ["shell", "pm", "list", "packages"]
    )
    found: List[str] = []
    if r and r.stdout:
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            pkg = line.split(":", 1)[-1].strip()
            low = pkg.lower()
            if (
                "chrome" in low
                or "chromium" in low
                or low == "com.android.browser"
                or low.endswith(".browser")
            ):
                found.append(pkg)
    prefer = (
        "com.android.chrome",
        "com.chrome.beta",
        "org.chromium.chrome.stable",
        "com.google.android.apps.chrome",
        "com.android.browser",
    )
    ordered: List[str] = []
    seen: set[str] = set()
    for p in prefer:
        if p in found and p not in seen:
            seen.add(p)
            ordered.append(p)
    for p in sorted(found):
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    _CHROME_PKG_PROBE_CACHE[key] = (now, ordered)
    return ordered


def _offerup_chrome_packages_to_try(primary: str, serial: Optional[str] = None) -> List[str]:
    installed = _offerup_list_browser_packages_on_device(serial)
    parts: List[str] = []
    if (primary or "").strip():
        parts.append((primary or "").strip())
    parts.extend(installed)
    parts.append(OFFERUP_CHROME_PACKAGE.strip())
    if _OFFERUP_CHROME_FALLBACK_ENV:
        parts.extend(x.strip() for x in _OFFERUP_CHROME_FALLBACK_ENV.split(",") if x.strip())
    parts.extend(
        [
            "com.android.chrome",
            "com.chrome.beta",
            "org.chromium.chrome.stable",
            "com.google.android.apps.chrome",
            "com.android.browser",
        ]
    )
    seen: set[str] = set()
    out: List[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _offerup_fg_is_browser(pkg: str) -> bool:
    p = (pkg or "").strip().lower()
    if not p:
        return False
    return any(m in p for m in _OFFERUP_CHROME_FG_MARKERS) or p.endswith(".browser")


def _offerup_chrome_scroll_page(serial: Optional[str], log_func: Optional[Callable[[str], None]] = None) -> None:
    """Прокрутка страницы OfferUp в браузере — кнопка OPEN часто ниже fold."""
    root = dump_ui_serial(serial) if serial else dump_ui()
    if root is None:
        return
    scr_w, scr_h = screen_size(root)
    if scr_h < 200:
        return
    sx = str(scr_w // 2 or 320)
    y1 = str(int(scr_h * 0.72))
    y2 = str(int(scr_h * 0.38))
    if log_func:
        log_func(f"Chrome: swipe {sx},{y1}->{y2} (искать OPEN)")
    if serial:
        run_adb_serial(serial, ["shell", "input", "swipe", sx, y1, sx, y2, "420"])
    else:
        run_adb(["shell", "input", "swipe", sx, y1, sx, y2, "420"])
    time.sleep(0.55)


def _offerup_run_am_view(url: str, package: Optional[str], serial: Optional[str] = None) -> Tuple[bool, str]:
    # URL в одинарных кавычках — иначе shell на устройстве режет по '&' в query string.
    shell_url = (url or "").replace("'", "'\"'\"'")
    shell_cmd = f"am start --user 0 -a android.intent.action.VIEW -d '{shell_url}'"
    if package:
        shell_cmd += f" -p {package}"
    cmd = ["shell", shell_cmd]
    r = run_adb_serial(serial, cmd) if serial else run_adb(cmd)
    return _offerup_interpret_am_start(r)


def offerup_open_listing_in_chrome(
    url: str,
    chrome_package: Optional[str] = None,
    chrome_sleep: float = 2.8,
    serial: Optional[str] = None,
    log_func: Optional[Callable[[str], None]] = None,
) -> bool:
    log = log_func or _offerup_log
    if serial:
        adb_connect_serial(serial)
    else:
        adb_connect()
    if not get_adb():
        log("adb not found / not connected - check ADB_PATH and port")
        return False
    primary = (chrome_package or "").strip() or OFFERUP_CHROME_PACKAGE
    if serial:
        run_adb_serial(serial, ["shell", "input", "keyevent", "224"])
    else:
        run_adb(["shell", "input", "keyevent", "224"])
    time.sleep(0.35)

    if OFFERUP_TRY_APP_URL_FIRST:
        log("OFFERUP_TRY_APP_URL_FIRST: try listing URL inside OfferUp app (skip Chrome if Ask appears)")
        ok_a, msg_a = _offerup_run_am_view(url, "com.offerup", serial=serial)
        log("am start -p com.offerup: " + (msg_a[:420] if msg_a else "—"))
        if ok_a:
            tdead = time.monotonic() + 26.0
            while time.monotonic() < tdead:
                root = dump_ui_serial(serial) if serial else dump_ui()
                if root is not None and _offerup_find_ask(root, native_offerup_only=True):
                    log("OfferUp opened listing without Chrome")
                    return True
                time.sleep(0.45)
            log("App-first: Ask not seen in time, falling back to Chrome")
        if serial:
            run_adb_serial(serial, ["shell", "am", "force-stop", "com.offerup"])
        else:
            run_adb(["shell", "am", "force-stop", "com.offerup"])
        time.sleep(0.35)

    # Force-stop Chrome before each open — prevents freeze after many iterations
    for pkg in _offerup_chrome_packages_to_try(primary, serial=serial):
        if serial:
            run_adb_serial(serial, ["shell", "am", "force-stop", pkg])
        else:
            run_adb(["shell", "am", "force-stop", pkg])
    time.sleep(0.5)
    log("Chrome force-stopped, opening URL...")

    tried: List[str] = []
    opened = False
    opened_pkg = ""
    for p in _offerup_chrome_packages_to_try(primary, serial=serial):
        ok, msg = _offerup_run_am_view(url, p, serial=serial)
        tried.append(f"-p {p}: {msg}")
        log("am start " + tried[-1][:400])
        if not ok:
            continue
        t_verify = time.monotonic() + 5.0
        fg_ok = False
        while time.monotonic() < t_verify:
            fg = _adb_foreground_package(serial)
            if _offerup_fg_is_browser(fg) or fg == "com.offerup":
                fg_ok = True
                opened_pkg = fg or p
                break
            time.sleep(0.35)
        if fg_ok:
            opened = True
            log(f"Браузер в фокусе: {opened_pkg or p}")
            break
        tried.append(f"-p {p}: am start ok, но браузер не в фокусе")

    if not opened:
        ok2, msg2 = _offerup_run_am_view(url, None, serial=serial)
        tried.append(f"without -p: {msg2}")
        log("am start " + tried[-1][:400])
        if ok2:
            t_verify = time.monotonic() + 5.0
            while time.monotonic() < t_verify:
                fg = _adb_foreground_package(serial)
                if _offerup_fg_is_browser(fg) or fg == "com.offerup":
                    opened = True
                    log(f"Браузер в фокусе (без -p): {fg}")
                    break
                time.sleep(0.35)

    if not opened:
        installed = _offerup_list_browser_packages_on_device(serial)
        hint = (
            " Установите Chrome в MuMu или укажите Chrome pkg в настройках "
            f"(сейчас: {primary!r}). На устройстве: "
            + (", ".join(installed[:6]) if installed else "браузеры не найдены")
        )
        log(
            "Link did not open."
            + hint
            + " Log: "
            + " | ".join(tried)[-950:]
        )
        return False

    wait_sec = max(1.0, min(float(chrome_sleep), 45.0))
    log(f"Pause after opening {wait_sec:.1f}s")
    time.sleep(wait_sec)

    rfocus = run_adb_serial(serial, ["shell", "dumpsys", "window", "windows"]) if serial else run_adb(["shell", "dumpsys", "window", "windows"])
    if rfocus and rfocus.stdout:
        for line in (rfocus.stdout or "").splitlines():
            s = line.strip()
            if "mCurrentFocus" in s or "mFocusedApp" in s:
                log("Window focus: " + s[:240])
                break
    return True

def offerup_fetch_first_ad_with_item(
    api_key: str, filter_params: Optional[Dict[str, str]] = None
) -> Tuple[str, Optional[dict]]:
    merged = offerup_sanitize_parser_filters(filter_params or {})
    q = offerup_merge_ads_url_with_filters(merged)
    req = urllib.request.Request(q, headers={"api-key": api_key})
    try:
        kwargs: dict = {"timeout": 45}
        if urllib.parse.urlparse(q).scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        with urllib.request.urlopen(req, **kwargs) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API HTTP {e.code}") from e
    if not isinstance(data, dict):
        raise RuntimeError("Ответ API не JSON-объект")

    def sk(k: str) -> int:
        return int(k) if str(k).isdigit() else 0

    for key in sorted(data.keys(), key=sk):
        item = data[key]
        if isinstance(item, dict):
            u = (item.get("ad_url") or item.get("url") or "").strip()
            if u:
                return u, item
    raise RuntimeError("В ответе нет ad_url")


def offerup_item_unique_key(item: Optional[dict], url: str) -> str:
    if isinstance(item, dict):
        for key in ("search_id", "id", "ad_id", "listing_id", "offerup_id", "post_id"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
    return (url or "").strip().lower()


def offerup_add_blacklist_keys(filter_params: Dict[str, str], keys: List[str]) -> Dict[str, str]:
    out = dict(filter_params or {})
    clean = [str(x).strip() for x in keys if str(x).strip()]
    if not clean:
        return out
    existing = str(out.get("blacklist") or "").strip()
    merged = [existing] if existing else []
    merged.extend(clean)
    out["blacklist"] = ",".join(merged)[-OFFERUP_PARSER_MAX_BLACKLIST_LEN:]
    return out


def offerup_fetch_unseen_ad_with_item(
    api_key: str,
    filter_params: Optional[Dict[str, str]] = None,
    exclude_keys: Optional[set[str]] = None,
) -> Tuple[str, Optional[dict], str]:
    exclude = set(exclude_keys or set())
    merged = offerup_add_blacklist_keys(
        offerup_sanitize_parser_filters(filter_params or {}),
        sorted(exclude),
    )
    q = offerup_merge_ads_url_with_filters(merged)
    req = urllib.request.Request(q, headers={"api-key": api_key})
    try:
        kwargs: dict = {"timeout": 45}
        if urllib.parse.urlparse(q).scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        with urllib.request.urlopen(req, **kwargs) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API HTTP {e.code}") from e
    if not isinstance(data, dict):
        raise RuntimeError("API response is not a JSON object")

    def sk(k: str) -> int:
        return int(k) if str(k).isdigit() else 0

    first_duplicate = ""
    for key in sorted(data.keys(), key=sk):
        item = data[key]
        if not isinstance(item, dict):
            continue
        u = (item.get("ad_url") or item.get("url") or "").strip()
        if not u:
            continue
        item_key = offerup_item_unique_key(item, u)
        if item_key in exclude:
            first_duplicate = first_duplicate or item_key
            continue
        return u, item, item_key
    if first_duplicate:
        raise RuntimeError("Parser returned only already reserved ads")
    raise RuntimeError("No ad_url in API response")

def _first_item_text(item: Optional[dict], keys: List[str]) -> str:
    if not isinstance(item, dict):
        return ""
    for key in keys:
        v = item.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


# ── Профиль покупателя (имя и адрес для Link API) ──────────

_BUYER_FIRST_NAMES = [
    "James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles",
    "Christopher","Daniel","Matthew","Anthony","Mark","Donald","Steven","Paul","Andrew","Joshua",
    "Kenneth","Kevin","Brian","George","Timothy","Ronald","Edward","Jason","Jeffrey","Ryan",
    "Jacob","Gary","Nicholas","Eric","Jonathan","Stephen","Larry","Justin","Scott","Brandon",
    "Benjamin","Samuel","Raymond","Gregory","Frank","Alexander","Patrick","Jack","Dennis","Jerry",
    "Emma","Olivia","Ava","Isabella","Sophia","Mia","Charlotte","Amelia","Harper","Evelyn",
    "Abigail","Emily","Elizabeth","Mila","Ella","Avery","Sofia","Camila","Aria","Scarlett",
    "Victoria","Madison","Luna","Grace","Chloe","Penelope","Layla","Riley","Zoey","Nora",
    "Lily","Eleanor","Hannah","Lillian","Addison","Aubrey","Ellie","Stella","Natalie","Zoe",
]
_BUYER_LAST_NAMES = [
    "Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
    "Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin",
    "Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson",
    "Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores",
    "Green","Adams","Nelson","Baker","Hall","Rivera","Campbell","Mitchell","Carter","Roberts",
]
_BUYER_STREETS = [
    "Main St","Oak Ave","Maple Dr","Cedar Ln","Pine St","Elm St","Washington Blvd","Lake Dr",
    "Hill Rd","Park Ave","Church St","View Dr","Forest Ln","Sunset Blvd","River Rd","Valley Dr",
    "Spring St","Grove Ave","Ridge Rd","Mill Rd","Meadow Ln","Highland Ave","Lincoln Ave",
    "Jefferson St","Franklin Ave","Madison Ave","Monroe St","Jackson Blvd","Adams St","Harrison Rd",
]
_BUYER_CITIES = [
    ("New York","NY"),("Los Angeles","CA"),("Chicago","IL"),("Houston","TX"),("Phoenix","AZ"),
    ("Philadelphia","PA"),("San Antonio","TX"),("San Diego","CA"),("Dallas","TX"),("San Jose","CA"),
    ("Austin","TX"),("Jacksonville","FL"),("Fort Worth","TX"),("Columbus","OH"),("Charlotte","NC"),
    ("Indianapolis","IN"),("San Francisco","CA"),("Seattle","WA"),("Denver","CO"),("Nashville","TN"),
    ("Oklahoma City","OK"),("El Paso","TX"),("Las Vegas","NV"),("Memphis","TN"),("Louisville","KY"),
    ("Portland","OR"),("Baltimore","MD"),("Milwaukee","WI"),("Albuquerque","NM"),("Tucson","AZ"),
]


def generate_random_buyer_name() -> str:
    first = random.choice(_BUYER_FIRST_NAMES)
    last = random.choice(_BUYER_LAST_NAMES)
    return f"{first} {last}"


def generate_random_buyer_address() -> str:
    num = random.randint(100, 9999)
    street = random.choice(_BUYER_STREETS)
    city, state = random.choice(_BUYER_CITIES)
    zip_code = f"{random.randint(10000, 99999)}"
    return f"{num} {street}, {city}, {state} {zip_code}"


def resolve_buyer_name(buyer_name: Optional[str], randomize: bool) -> str:
    s = (buyer_name or "").strip()
    if s:
        return s
    if randomize:
        return generate_random_buyer_name()
    return ""


def resolve_buyer_address(buyer_address: Optional[str], randomize: bool) -> str:
    s = (buyer_address or "").strip()
    if s:
        return s
    if randomize:
        return generate_random_buyer_address()
    return ""


def _normalize_link_price(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.search(r"\d+(?:[.,]\d+)?", s)
    if not m:
        return s
    return m.group(0).replace(",", ".")


def _coalesce_setting(value: object, default: str) -> str:
    s = str(value or "").strip()
    return s or default


def normalize_link_api_provider(value: object) -> str:
    s = str(value or "").strip().lower()
    if s in ("ephedrine", "eph", "haron", "haronrent"):
        return "ephedrine"
    return "kartatai"


def _parse_optional_positive_int(value: object) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        n = int(value)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def build_link_api_payload(
    item: Optional[dict],
    fallback_url: str,
    api_key: str,
    user_id: str,
    buyer_name: str = "",
    buyer_address: str = "",
) -> dict:
    title = _first_item_text(item, ["title", "name"])
    image = _first_item_text(item, ["image_url", "photo", "image", "thumbnail"])
    price = _normalize_link_price(_first_item_text(item, ["price", "amount"]))
    description = _first_item_text(item, ["description", "desc", "body"])
    if fallback_url and fallback_url not in description:
        description = (description + "\n" if description else "") + fallback_url

    payload = {
        "api_key": api_key,
        "title": title or "OfferUp item",
        "service": LINK_API_SERVICE,
        "userId": user_id,
    }
    optional = {
        "photo": image,
        "price": price,
        "name": buyer_name,
        "address": buyer_address,
        "description": description,
    }
    for key, value in optional.items():
        if value:
            payload[key] = value
    return payload


def create_link_api_link(
    item: Optional[dict],
    ad_url: str,
    api_key: str,
    api_url: Optional[str] = None,
    user_id: Optional[str] = None,
    buyer_name: str = "",
    buyer_address: str = "",
) -> dict:
    global _link_api_last_call
    url = _coalesce_setting(api_url, LINK_API_URL)
    uid = _coalesce_setting(user_id, LINK_API_USER_ID)
    if not (url.startswith("http://") or url.startswith("https://")):
        raise RuntimeError("Link API URL must start with http:// or https://")
    if not uid:
        raise RuntimeError("Link API Telegram ID is empty")
    payload = build_link_api_payload(item, ad_url, api_key, uid, buyer_name=buyer_name, buyer_address=buyer_address)
    parsed_url = urllib.parse.urlparse(url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    kwargs: dict = {"timeout": LINK_API_TIMEOUT_SEC}
    if urllib.parse.urlparse(url).scheme == "https":
        kwargs["context"] = ssl.create_default_context()

    last_error = ""
    for attempt in range(1, LINK_API_RETRY_COUNT + 1):
        with _link_api_rate_lock:
            wait = LINK_API_MIN_INTERVAL_SEC - (time.monotonic() - _link_api_last_call)
            if wait > 0:
                time.sleep(wait)
            _link_api_last_call = time.monotonic()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Origin": origin,
                "Referer": origin + "/",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, **kwargs) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise RuntimeError("Link API response is not an object")
            return data
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            last_error = f"Link API HTTP {e.code}: {raw[:300]}"
            if e.code not in (408, 425, 429, 500, 502, 503, 504) or attempt >= LINK_API_RETRY_COUNT:
                raise RuntimeError(last_error) from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            last_error = str(e)
            if attempt >= LINK_API_RETRY_COUNT:
                raise RuntimeError("Link API failed: " + last_error) from e
        time.sleep(min(8.0, 1.6 * attempt))
    raise RuntimeError("Link API failed: " + last_error)


def build_ephedrine_create_ad_payload(
    item: Optional[dict],
    fallback_url: str,
    service_code: str,
    buyer_name: str = "",
    buyer_address: str = "",
    version: Optional[str] = None,
    domain_id: Optional[int] = None,
    profile_id: Optional[int] = None,
) -> dict:
    title = _first_item_text(item, ["title", "name"])
    image = _first_item_text(item, ["image_url", "photo", "image", "thumbnail"])
    price = _normalize_link_price(_first_item_text(item, ["price", "amount"]))
    description = _first_item_text(item, ["description", "desc", "body"])
    if fallback_url and fallback_url not in description:
        description = (description + "\n" if description else "") + fallback_url

    payload: dict = {"serviceCode": service_code}
    ver = str(version if version is not None else EPHEDRINE_VERSION).strip()
    if ver:
        payload["version"] = ver
    optional = {
        "title": title,
        "price": price,
        "photo": image,
        "about": description,
        "name": buyer_name,
        "address": buyer_address,
    }
    for key, value in optional.items():
        if value:
            payload[key] = value
    if profile_id is not None:
        payload["profileId"] = profile_id
    if domain_id is not None:
        payload["domainId"] = domain_id
    return payload


def create_ephedrine_ad_link(
    item: Optional[dict],
    ad_url: str,
    bearer_token: str,
    base_url: Optional[str] = None,
    service_code: Optional[str] = None,
    buyer_name: str = "",
    buyer_address: str = "",
    version: Optional[str] = None,
    domain_id: Optional[int] = None,
    profile_id: Optional[int] = None,
) -> dict:
    global _link_api_last_call
    base = _coalesce_setting(base_url, EPHEDRINE_API_BASE).rstrip("/")
    token = str(bearer_token or "").strip()
    sc = _coalesce_setting(service_code, EPHEDRINE_SERVICE_CODE)
    if not (base.startswith("http://") or base.startswith("https://")):
        raise RuntimeError("Ephedrine API base URL must start with http:// or https://")
    if not token:
        raise RuntimeError("Ephedrine API token is empty")
    if not sc:
        raise RuntimeError("Ephedrine serviceCode is empty (getServices)")

    dom_id = domain_id if domain_id is not None else _parse_optional_positive_int(EPHEDRINE_DOMAIN_ID)
    prof_id = profile_id if profile_id is not None else _parse_optional_positive_int(EPHEDRINE_PROFILE_ID)
    payload = build_ephedrine_create_ad_payload(
        item, ad_url, sc,
        buyer_name=buyer_name, buyer_address=buyer_address,
        version=version, domain_id=dom_id, profile_id=prof_id,
    )
    url = base + "/api/v1/createAd"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    kwargs: dict = {"timeout": LINK_API_TIMEOUT_SEC}
    if urllib.parse.urlparse(url).scheme == "https":
        kwargs["context"] = ssl.create_default_context()

    last_error = ""
    for attempt in range(1, LINK_API_RETRY_COUNT + 1):
        with _link_api_rate_lock:
            wait = LINK_API_MIN_INTERVAL_SEC - (time.monotonic() - _link_api_last_call)
            if wait > 0:
                time.sleep(wait)
            _link_api_last_call = time.monotonic()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer " + token,
                "User-Agent": "MumuPaster/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, **kwargs) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise RuntimeError("Ephedrine response is not an object")
            if not data.get("status"):
                msg = str(data.get("message") or "createAd failed").strip()
                raise RuntimeError(msg or "Ephedrine createAd failed")
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            ad_link = str(inner.get("adUrl") or inner.get("url") or "").strip()
            ad_id = inner.get("adId")
            if not ad_link:
                raise RuntimeError("Ephedrine createAd: empty adUrl in response")
            sid = str(ad_id).strip() if ad_id is not None else ""
            return {
                "message": ad_link,
                "fish_link": ad_link,
                "search_link": "",
                "search_id": sid,
                "ad_id": ad_id,
                "link_provider": "ephedrine",
                "ephedrine_message": str(data.get("message") or "").strip(),
            }
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            last_error = f"Ephedrine HTTP {e.code}: {raw[:300]}"
            if e.code not in (408, 425, 429, 500, 502, 503, 504) or attempt >= LINK_API_RETRY_COUNT:
                raise RuntimeError(last_error) from e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            last_error = str(e)
            if attempt >= LINK_API_RETRY_COUNT:
                raise RuntimeError("Ephedrine API failed: " + last_error) from e
        time.sleep(min(8.0, 1.6 * attempt))
    raise RuntimeError("Ephedrine API failed: " + last_error)


def create_link_for_item(
    provider: str,
    item: Optional[dict],
    ad_url: str,
    *,
    kartatai_key: str = "",
    kartatai_url: Optional[str] = None,
    kartatai_user_id: Optional[str] = None,
    ephedrine_token: str = "",
    ephedrine_base: Optional[str] = None,
    ephedrine_service_code: Optional[str] = None,
    ephedrine_version: Optional[str] = None,
    ephedrine_domain_id: Optional[int] = None,
    ephedrine_profile_id: Optional[int] = None,
    buyer_name: str = "",
    buyer_address: str = "",
) -> dict:
    p = normalize_link_api_provider(provider)
    if p == "ephedrine":
        return create_ephedrine_ad_link(
            item, ad_url, ephedrine_token,
            base_url=ephedrine_base, service_code=ephedrine_service_code,
            buyer_name=buyer_name, buyer_address=buyer_address,
            version=ephedrine_version, domain_id=ephedrine_domain_id,
            profile_id=ephedrine_profile_id,
        )
    data = create_link_api_link(
        item, ad_url, kartatai_key, kartatai_url, kartatai_user_id,
        buyer_name=buyer_name, buyer_address=buyer_address,
    )
    data["link_provider"] = "kartatai"
    return data


def link_credentials_configured(provider: str, kartatai_key: str, ephedrine_token: str) -> bool:
    p = normalize_link_api_provider(provider)
    if p == "ephedrine":
        return bool(str(ephedrine_token or EPHEDRINE_API_TOKEN).strip())
    return bool(str(kartatai_key or LINK_API_KEY).strip())


def link_api_queue_url(data: dict) -> str:
    v = data.get("message")
    return str(v).strip() if v else ""


def link_api_queue_name(item: Optional[dict], data: dict) -> str:
    seller = _first_item_text(
        item,
        [
            "seller", "seller_name", "username", "user", "owner", "owner_name",
            "display_name", "profile_name", "posted_by", "seller_username",
        ],
    )
    title = _first_item_text(item, ["title", "listing_title", "item_title"])
    if not seller and isinstance(item, dict):
        extra = _collapse_ws(str(item.get("name") or ""))
        if extra and extra != title and not _is_generic_link_seller_name(extra):
            if not re.match(r"^\$?\d", extra):
                seller = extra
    sid = str(data.get("search_id") or "").strip()
    if sid.startswith("#search"):
        sid = ""
    return seller or title or sid or ""


def _extract_link_fields(data: dict) -> dict:
    """Извлечь все полезные поля из ответа Link API (Kartatai или Ephedrine)."""
    provider = normalize_link_api_provider(data.get("link_provider") or "kartatai")
    fish = str(data.get("fish_link") or "").strip()
    bot = str(data.get("bot_url") or data.get("message") or "").strip()
    if provider == "ephedrine":
        if not fish:
            fish = bot
        bot = ""
    fields = {
        "bot_url": bot,
        "fish_link": fish,
        "search_link": str(data.get("search_link") or "").strip(),
        "search_id": str(data.get("search_id") or "").strip(),
        "link_provider": provider,
    }
    if data.get("ad_id") is not None:
        fields["ad_id"] = data.get("ad_id")
    return fields


def add_created_link_item(item: Optional[dict], data: dict, source_url: str, port: Optional[str] = None) -> dict:
    global _created_links_next_id
    fields = _extract_link_fields(data)
    title = _first_item_text(item, ["title", "name"]) if item else ""
    price = _normalize_link_price(_first_item_text(item, ["price", "amount"])) if item else ""
    image = _first_item_text(item, ["image_url", "photo", "image", "thumbnail"]) if item else ""
    with _created_links_lock:
        row = {
            "id": _created_links_next_id,
            "name": link_api_queue_name(item, data) or title or "",
            "title": title or "",
            "price": price,
            "image": image,
            "ad_url": source_url,
            "url": fields["fish_link"] or fields["bot_url"],
            "bot_url": fields["bot_url"],
            "fish_link": fields["fish_link"],
            "search_link": fields["search_link"],
            "search_id": fields["search_id"],
            "link_provider": fields.get("link_provider") or "kartatai",
            "port": port or "",
            "created_at": time.strftime("%H:%M:%S"),
            "sent": False,
        }
        if fields.get("ad_id") is not None:
            row["ad_id"] = fields["ad_id"]
        _created_links_next_id += 1
        _created_links.append(row)
        if len(_created_links) > 500:
            del _created_links[:-500]
    save_persistent_state()
    return row


def mark_created_link_sent(created_id: object) -> None:
    try:
        cid = int(created_id or 0)
    except (TypeError, ValueError):
        return
    if not cid:
        return
    with _created_links_lock:
        for row in _created_links:
            if int(row.get("id") or 0) == cid:
                row["sent"] = True
                row["sent_at"] = time.strftime("%H:%M:%S")
                break
    save_persistent_state()


def add_link_queue_item(item: Optional[dict], data: dict, source_url: str, port: Optional[str] = None) -> Optional[dict]:
    global _link_queue_next_id
    created = add_created_link_item(item, data, source_url, port=port)
    main_link = created.get("fish_link") or created.get("bot_url")
    if not main_link:
        return None
    with _link_queue_lock:
        row = {
            "id": _link_queue_next_id,
            "created_link_id": created.get("id"),
            "name": created.get("name") or created.get("title") or "",
            "url": main_link,
            "bot_url": created.get("bot_url", ""),
            "fish_link": created.get("fish_link", ""),
            "search_link": created.get("search_link", ""),
            "search_id": created.get("search_id", ""),
            "link_provider": created.get("link_provider") or "kartatai",
            "source_url": source_url,
            "port": port or "",
            "created_at": created.get("created_at") or time.strftime("%H:%M:%S"),
        }
        if created.get("ad_id") is not None:
            row["ad_id"] = created.get("ad_id")
        _link_queue_next_id += 1
        _link_queue.append(row)
        if len(_link_queue) > 200:
            del _link_queue[:-200]
    _sync_link_queue_names_from_created()
    save_persistent_state()
    return row


def add_send_history_item(
    item: Optional[dict],
    ad_url: str,
    link_queue_row: Optional[dict],
    port: Optional[str] = None,
) -> dict:
    """Запись в историю отправок (объявление + все ссылки бота)."""
    global _send_history_next_id
    title = _first_item_text(item, ["title", "name"]) if item else ""
    price = _normalize_link_price(_first_item_text(item, ["price", "amount"])) if item else ""
    image = _first_item_text(item, ["image_url", "photo", "image", "thumbnail"]) if item else ""
    seller = _first_item_text(item, ["seller", "seller_name", "username", "user"]) if item else ""
    lq = link_queue_row or {}
    with _send_history_lock:
        row = {
            "id": _send_history_next_id,
            "title":       title or "OfferUp",
            "price":       price,
            "image":       image,
            "seller_name":  lq.get("name") or seller,
            "ad_url":      ad_url,
            "bot_url":     lq.get("bot_url", ""),
            "fish_link":   lq.get("fish_link", ""),
            "search_link": lq.get("search_link", ""),
            "search_id":   lq.get("search_id", ""),
            "port":        port or "",
            "created_at":  time.strftime("%H:%M:%S"),
        }
        _send_history_next_id += 1
        _send_history.append(row)
        if len(_send_history) > 500:
            del _send_history[:-500]
    save_persistent_state()
    return row


def _offerup_log_link_queued_for_inbox(
    queued_row: Optional[dict],
    log_func: Callable[[str], None],
) -> None:
    """fish_link не шлём из текущего чата — только Inbox (авторежим / ручной скан)."""
    if not queued_row or not (queued_row.get("fish_link") or queued_row.get("url")):
        return
    log_func(
        "Ссылка в очереди — отправка после ответа продавца только через Inbox "
        "(авторежим или «Просмотреть Inbox»)."
    )


def offerup_run_flow(
    ad_url: str,
    timing: Optional[dict] = None,
    chrome_pkg_override: Optional[str] = None,
    template_rules: Optional[List[dict]] = None,
    category_id: Optional[int] = None,
    serial: Optional[str] = None,
    *,
    item_meta: Optional[dict] = None,
    queued_row: Optional[dict] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    rules = offerup_sanitize_template_rules(template_rules or [])
    tmpl, src = offerup_pick_template_text(category_id, rules)
    _offerup_log(f"Шаблон ({src}): " + (tmpl[:70] + "…" if len(tmpl) > 70 else tmpl))
    t = offerup_merge_action_timing(timing, apply_jitter=True)
    _offerup_log(
        f"Тайминги (±{int(_JITTER_FACTOR*100)}% jitter): "
        f"chrome={t['chrome_sleep']:.2f}s  open={t['after_open_sleep']:.2f}s  "
        f"ask={t['after_ask_sleep']:.2f}s  after={t['after_template']:.2f}s"
    )
    pkg = offerup_sanitize_chrome_package(chrome_pkg_override or "") or OFFERUP_CHROME_PACKAGE
    _offerup_log("URL: " + (ad_url[:100] + "…" if len(ad_url) > 100 else ad_url))
    # Force-stop OfferUp before each run to prevent stale state after many iterations
    _offerup_force_stop_app(serial)
    if not offerup_open_listing_in_chrome(ad_url, chrome_package=pkg, chrome_sleep=t["chrome_sleep"], serial=serial):
        raise RuntimeError("Could not open Chrome / listing")
    open_state, ask_xy = _offerup_wait_open_or_app(
        t["open_wait"],
        t["poll_ui"],
        serial=serial,
        should_stop=should_stop,
        listing_url=ad_url,
        skip_open_cta=OFFERUP_TRY_APP_URL_FIRST,
    )
    if open_state == "timeout":
        if OFFERUP_TRY_APP_URL_FIRST:
            raise RuntimeError("OfferUp app: кнопка Ask не появилась (OPEN в Chrome не проверялся)")
        raise RuntimeError("Neither OPEN nor OfferUp app screen was found (Ask did not appear)")
    if open_state == "already_app":
        time.sleep(t["after_open_sleep"])
    if not _offerup_tap_ask_button(serial, ask_xy, t, should_stop=should_stop):
        raise RuntimeError("Ask button was not found")
    time.sleep(t["after_ask_sleep"])
    _offerup_dismiss_if_popup(serial)
    _offerup_wait_if_limit_popup(
        serial,
        t.get("conversation_cooldown", OFFERUP_CONVERSATION_COOLDOWN_SEC),
    )
    if not _offerup_apply_template_message(
        serial, tmpl, t, should_stop=should_stop, log_func=_offerup_log
    ):
        raise RuntimeError("Template message was not sent: " + tmpl[:48])
    _offerup_log(f"Пауза {t['after_template']:.0f} с…")
    time.sleep(t["after_template"])
    if queued_row and queued_row.get("created_link_id"):
        _offerup_try_capture_seller_from_chat(serial, queued_row["created_link_id"], _offerup_log)
    eff_serial = serial or ADB_PORT
    if eff_serial:
        bump_offerup_template_sends(eff_serial)
        if OFFERUP_VERIFY_EMAIL_AFTER_TEMPLATE:
            _offerup_maybe_email_verification_after_template(
                eff_serial, _offerup_log, should_stop=should_stop,
            )
    _offerup_log_link_queued_for_inbox(queued_row, _offerup_log)
    _offerup_log("Готово.")


def _offerup_worker(
    ad_url: str,
    from_api: bool,
    api_key: str,
    link_api_provider: str,
    link_api_key: str,
    link_api_url: str,
    link_api_user_id: str,
    ephedrine_token: str,
    ephedrine_base: str,
    ephedrine_service_code: str,
    ephedrine_version: str,
    ephedrine_domain_id: Optional[int],
    ephedrine_profile_id: Optional[int],
    filter_params: Optional[dict],
    timing_overrides: Optional[dict],
    chrome_package: str,
    template_rules: Optional[List[dict]],
    manual_template_category_id: Optional[int],
    buyer_name: str = "",
    buyer_address: str = "",
) -> None:
    try:
        _offerup_log("Сценарий OfferUp запущен")
        fp = offerup_sanitize_parser_filters(filter_params or {})
        if from_api and fp:
            _offerup_log("Фильтры парсера: " + json.dumps(fp, ensure_ascii=False)[:500])
        url = ad_url.strip()
        item_meta: Optional[dict] = None
        queued_row: Optional[dict] = None
        if from_api:
            if not api_key:
                raise RuntimeError("Нужен api-key парсера (в форме или OFFERUP_PARSER_API_KEY в app.py)")
            url, item_meta = offerup_fetch_first_ad_with_item(api_key, fp)
            if link_credentials_configured(link_api_provider, link_api_key, ephedrine_token):
                link_data = create_link_for_item(
                    link_api_provider, item_meta, url,
                    kartatai_key=link_api_key, kartatai_url=link_api_url, kartatai_user_id=link_api_user_id,
                    ephedrine_token=ephedrine_token, ephedrine_base=ephedrine_base,
                    ephedrine_service_code=ephedrine_service_code, ephedrine_version=ephedrine_version,
                    ephedrine_domain_id=ephedrine_domain_id, ephedrine_profile_id=ephedrine_profile_id,
                    buyer_name=buyer_name, buyer_address=buyer_address,
                )
                queued_row = add_link_queue_item(item_meta, link_data, url)
                prov = normalize_link_api_provider(link_api_provider)
                if queued_row:
                    _offerup_log(
                        f"{prov}: queued {queued_row['name']} | "
                        f"fish={queued_row.get('fish_link', '—')[:60]}"
                    )
                else:
                    _offerup_log(f"{prov}: response has no link URL")
            else:
                _offerup_log("Link API: credentials empty, link was not created")
            _offerup_log("Объявление с API получено")
        if not url.startswith("http"):
            raise RuntimeError("Нужна ссылка http(s)")
        cid = offerup_resolve_category_for_template(item_meta, fp)
        if cid is None and manual_template_category_id is not None:
            m = manual_template_category_id
            if 1 <= m <= 12:
                cid = m
                _offerup_log(f"Категория для шаблона (вручную): {m}")
        elif cid is not None:
            nm = next((c["name"] for c in OFFERUP_CATEGORIES if int(c["id"]) == cid), str(cid))
            _offerup_log(f"Категория для шаблона: {nm} ({cid})")
        rules = offerup_sanitize_template_rules(template_rules or [])
        cpkg = offerup_sanitize_chrome_package(chrome_package)
        offerup_run_flow(
            url,
            timing=timing_overrides,
            chrome_pkg_override=cpkg or None,
            template_rules=rules,
            category_id=cid,
            serial=ADB_PORT,
            item_meta=item_meta,
            queued_row=queued_row,
        )
        add_send_history_item(item_meta, url, queued_row, port=None)
        with _offerup_lock:
            _offerup_job["state"] = "done"
    except Exception as e:
        with _offerup_lock:
            _offerup_job["state"] = "error"
            _offerup_job["error"] = str(e)
            _offerup_job.setdefault("log", []).append("Ошибка: " + str(e))


# ── x.gd API ───────────────────────────────────────────────

def shorten_url(long_url: str) -> Tuple[bool, str]:
    """Возвращает (ok, короткая_ссылка_или_ошибка)."""
    api = (
        f"https://xgd.io/V1/shorten?url={urllib.parse.quote(long_url, safe='')}"
        f"&key={urllib.parse.quote(XGD_API_KEY, safe='')}&analytics=false"
    )
    try:
        req = urllib.request.Request(
            api,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://xgd.io/",
                "Origin": "https://xgd.io",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") == 200 and data.get("shorturl"):
            short = re.sub(r"^https?://", "", data["shorturl"])
            return True, short
        return False, data.get("message", "Неизвестная ошибка x.gd")
    except Exception as e:
        return False, str(e)


# ── Flask routes ────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


def _normalize_templates_from_body(body: dict) -> List[str]:
    """Список шаблонов из JSON: templates[] или template, иначе из конфига."""
    raw = body.get("templates")
    if isinstance(raw, list):
        out = [str(x).strip() for x in raw if str(x).strip()]
        if out:
            return out
    t_one = body.get("template")
    if t_one is not None and str(t_one).strip():
        return [str(t_one).strip()]
    if MESSAGE_TEMPLATES:
        return [str(x).strip() for x in MESSAGE_TEMPLATES if str(x).strip()]
    if MESSAGE_TEMPLATE is not None and str(MESSAGE_TEMPLATE).strip():
        return [str(MESSAGE_TEMPLATE).strip()]
    return []


def _build_messages(templates: List[str], short_url: str) -> List[str]:
    messages: List[str] = []
    for tpl in templates:
        if "{ссылка}" in tpl:
            messages.append(tpl.replace("{ссылка}", short_url))
        else:
            messages.append(tpl)
    return [m for m in messages if m.strip()]


@app.route("/api/send", methods=["POST"])
def api_send():
    """
    Body JSON:
      long_url, adb_port,
      short_url — опционально: уже сокращённая ссылка (хост/путь), если
        сокращение делает браузер (прокси антика). Иначе сервер вызывает x.gd.
      templates: ["шаблон1 {ссылка}", "шаблон2"]  — по очереди в один чат
      или template: "один шаблон"
      timing: "fast" | "normal" | "stable" — задержки ADB (по умолчанию normal)
    """
    global ADB_PORT, _ADB_RESOLVED

    body = request.get_json(force=True, silent=True) or {}
    long_url = (body.get("long_url") or "").strip()
    short_client = (body.get("short_url") or "").strip()
    short_client = re.sub(r"^https?://", "", short_client, flags=re.I)
    adb_port = (body.get("adb_port") or "").strip()

    if not long_url and not short_client:
        return jsonify(ok=False, error="Передайте long_url или short_url"), 400

    if adb_port and adb_port != ADB_PORT:
        ADB_PORT = adb_port
        _ADB_RESOLVED = None

    if short_client:
        short_url = short_client
    else:
        ok, result = shorten_url(long_url)
        if not ok:
            return jsonify(ok=False, step="shorten", error=result), 502
        short_url = result

    key = "https://" + short_url
    if key in _sent_urls:
        return jsonify(ok=False, error="Эта ссылка уже была отправлена в этой сессии", short_url=short_url), 409

    templates_in = _normalize_templates_from_body(body)
    messages = _build_messages(templates_in, short_url) if templates_in else [short_url]
    if not messages:
        return jsonify(ok=False, error="Нет ни одного непустого сообщения после шаблонов"), 400

    adb_connect()

    timing_key = str(body.get("timing") or "normal").strip().lower()
    tprof = TIMING_PROFILES.get(timing_key) or TIMING_PROFILES["normal"]
    post_tap, post_text, p_before_send, between_msg = sanitize_adb_timing_overrides(
        body.get("adb_timing") or body.get("timing_overrides"),
        tprof,
    )

    err, fail_idx = send_messages_to_mumu(
        messages,
        post_tap=post_tap,
        post_text=post_text,
        post_input_before_send=p_before_send,
        between_messages=between_msg,
    )
    if err:
        hint = "Откройте чат в MuMu и повторите."
        n = len(messages)
        pos = f"({fail_idx + 1}/{n})"
        if err == "ui_dump":
            return jsonify(ok=False, step=err, error=f"UI dump {pos}. {hint}"), 500
        if err == "find_input":
            return jsonify(ok=False, step=err, error=f"Поле ввода не найдено {pos}. {hint}"), 500
        return jsonify(ok=False, step=err, error=f"adb input text не выполнился {pos}"), 500

    _sent_urls.add(key)
    return jsonify(ok=True, short_url=short_url, messages=messages, count=len(messages))


@app.route("/api/settings", methods=["GET"])
def api_settings():
    """Возвращает текущие настройки для отображения в UI."""
    if MESSAGE_TEMPLATES:
        tpl_default = [str(x) for x in MESSAGE_TEMPLATES if str(x).strip()]
    else:
        tpl_default = [MESSAGE_TEMPLATE or "{ссылка}"]
    with _ui_config_lock:
        llm_snap = _mumu_ui_config_snapshot()
    return jsonify(
        adb_path=ADB_PATH,
        adb_port=ADB_PORT,
        adb_found=bool(get_adb()),
        template=MESSAGE_TEMPLATE or "{ссылка}",
        templates_default=tpl_default,
        xgd_key=XGD_API_KEY,
        timing_profiles=list(TIMING_PROFILES.keys()),
        timing_profile_values={
            k: {
                "post_tap": v[0],
                "post_text": v[1],
                "post_input_before_send": v[2],
                "between_messages": v[3],
            }
            for k, v in TIMING_PROFILES.items()
        },
        local_llm=llm_snap,
    )


@app.route("/api/ui-config", methods=["GET"])
def api_ui_config_get():
    with _ui_config_lock:
        snap = _mumu_ui_config_snapshot()
    snap["local_llm_system_prompt_default"] = _LOCAL_LLM_PROMPT_BASE
    snap["coach_system_prompt_default"] = LOCAL_LLM_COACH_SYSTEM_PROMPT_BASE
    return jsonify(ok=True, config=snap)


@app.route("/api/ui-config", methods=["POST"])
def api_ui_config_post():
    body = request.get_json(force=True, silent=True) or {}
    err = apply_mumu_ui_config_from_body(body)
    if err:
        return jsonify(ok=False, error=err), 400
    with _ui_config_lock:
        snap = _mumu_ui_config_snapshot()
    snap["local_llm_system_prompt_default"] = _LOCAL_LLM_PROMPT_BASE
    snap["coach_system_prompt_default"] = LOCAL_LLM_COACH_SYSTEM_PROMPT_BASE
    return jsonify(ok=True, config=snap)


@app.route("/api/local-llm/providers", methods=["GET"])
def api_local_llm_providers():
    return jsonify(ok=True, providers=_llm_providers_for_api(), current=LOCAL_LLM_PROVIDER)


def _llm_ping_openai_compatible(
    url: str,
    model: str,
    api_key: str,
    provider: str,
    *,
    label: str = "",
) -> Tuple[bool, str]:
    ping_payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
            "max_tokens": 8,
            "temperature": 0,
        }
    ).encode("utf-8")
    headers = _llm_request_headers(api_key=api_key, provider=provider)
    try:
        req = urllib.request.Request(url, data=ping_payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=min(25.0, LOCAL_LLM_TIMEOUT_SEC)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        j = json.loads(raw)
        if isinstance(j, dict) and j.get("error"):
            return False, str(j["error"])[:400]
        prov_label = (_llm_provider_preset(provider).get("label") or provider or label or "API").strip()
        return True, f"{prov_label}: OK, модель «{model}»"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        err = _parse_llm_error_body(body) or str(e)
        return False, f"HTTP {e.code}: {err[:320]}"
    except Exception as e:
        return False, str(e)[:400]


@app.route("/api/local-llm/ping", methods=["GET", "POST"])
def api_local_llm_ping():
    """Проверка текстового и vision API (POST — значения из формы без сохранения)."""
    body = request.get_json(force=True, silent=True) if request.method == "POST" else {}
    if not isinstance(body, dict):
        body = {}
    if body:
        apply_mumu_ui_config_from_body(
            {
                k: body[k]
                for k in (
                    "local_llm_provider",
                    "local_llm_chat_url",
                    "local_llm_model",
                    "local_llm_api_style",
                    "local_llm_api_key",
                    "local_llm_vision_model",
                    "local_llm_vision_chat_url",
                    "local_llm_vision_provider",
                    "local_llm_vision_api_key",
                    "local_llm_vision_api_style",
                )
                if k in body
            },
            persist=False,
        )

    def _s(key: str, fallback: str = "") -> str:
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        return fallback

    text_url = _s("local_llm_chat_url", LOCAL_LLM_CHAT_URL or "")
    text_model = _s("local_llm_model", LOCAL_LLM_MODEL or "")
    text_provider = _s("local_llm_provider", LOCAL_LLM_PROVIDER or "custom").lower()
    text_key = (_s("local_llm_api_key", "") or _llm_api_key()).strip()
    text_style = _s("local_llm_api_style", LOCAL_LLM_API_STYLE or "ollama").lower()

    vision_url = _s("local_llm_vision_chat_url", LOCAL_LLM_VISION_CHAT_URL or "") or text_url
    vision_model = _s("local_llm_vision_model", LOCAL_LLM_VISION_MODEL or "")
    vision_provider = _s("local_llm_vision_provider", LOCAL_LLM_VISION_PROVIDER or "") or text_provider
    vision_key = (_s("local_llm_vision_api_key", "") or _llm_vision_api_key() or text_key).strip()
    vision_style = _s("local_llm_vision_api_style", LOCAL_LLM_VISION_API_STYLE or "") or text_style
    if not vision_style:
        vision_style = "ollama" if "11434" in vision_url else "openai"

    if not text_url:
        return jsonify(ok=False, error="Задайте API URL (текст) в настройках ИИ"), 400
    if not text_model:
        return jsonify(ok=False, error="Задайте текстовую модель"), 400
    if _llm_provider_needs_key(text_provider) and not text_key:
        return jsonify(
            ok=False,
            error=f"Нужен API-ключ для {_llm_provider_preset(text_provider).get('label', text_provider)}",
            text_ready=False,
            vision_ready=False,
        ), 400

    parts: List[str] = []
    models: List[str] = [text_model]

    if text_style == "ollama" or "11434" in text_url:
        tags_url = text_url.replace("/api/chat", "/api/tags").replace("/api/generate", "/api/tags")
        if "/api/" not in tags_url:
            base = text_url.rstrip("/").rsplit("/", 1)[0] if text_url.endswith("/chat") else text_url.rstrip("/")
            tags_url = base + "/api/tags" if base else "http://127.0.0.1:11434/api/tags"
        try:
            req = urllib.request.Request(tags_url, headers={"User-Agent": "MumuPaster/1"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            n = len(data.get("models") or [])
            parts.append(f"Ollama (текст): OK, моделей: {n}")
        except Exception as e:
            return jsonify(ok=False, error=f"Ollama: {e}", text_ready=False, vision_ready=False), 502
    else:
        ok_t, msg_t = _llm_ping_openai_compatible(text_url, text_model, text_key, text_provider, label="текст")
        if not ok_t:
            return jsonify(ok=False, error=f"Текст: {msg_t}", text_ready=False, vision_ready=False), 502
        parts.append(f"Текст — {msg_t}")

    vision_ready = bool(vision_model and vision_url)
    if vision_model:
        if vision_url.rstrip("/") == text_url.rstrip("/") and vision_model == text_model and vision_key == text_key:
            parts.append(f"Vision — та же модель «{vision_model}»")
            models.append(vision_model + " (vision)")
        elif vision_style == "ollama" or "11434" in vision_url:
            parts.append(f"Vision — Ollama «{vision_model}» (отдельный URL)")
            models.append(vision_model + " (vision)")
        else:
            if _llm_provider_needs_key(vision_provider) and not vision_key:
                parts.append("Vision — нет API-ключа")
                vision_ready = False
            else:
                ok_v, msg_v = _llm_ping_openai_compatible(
                    vision_url, vision_model, vision_key, vision_provider, label="vision"
                )
                if not ok_v:
                    return jsonify(
                        ok=False,
                        error=f"Vision: {msg_v}",
                        text_ready=True,
                        vision_ready=False,
                        message=" | ".join(parts),
                    ), 502
                parts.append(f"Vision — {msg_v}")
                models.append(vision_model + " (vision)")
                vision_ready = True

    text_ready = bool(text_url and text_model and (text_key or not _llm_provider_needs_key(text_provider)))
    return jsonify(
        ok=True,
        message=" | ".join(parts),
        models=models,
        text_model=text_model,
        vision_model=vision_model,
        provider=text_provider,
        vision_provider=vision_provider,
        text_ready=text_ready,
        vision_ready=vision_ready,
    )


@app.route("/api/local-llm/trace", methods=["GET"])
def api_local_llm_trace_get():
    try:
        lim = int(request.args.get("limit") or "60")
    except (TypeError, ValueError):
        lim = 60
    lim = max(1, min(lim, 120))
    with _local_llm_trace_lock:
        items = list(_local_llm_traces[-lim:])
    return jsonify(ok=True, items=list(reversed(items)))


@app.route("/api/local-llm/trace", methods=["DELETE"])
def api_local_llm_trace_clear():
    with _local_llm_trace_lock:
        _local_llm_traces.clear()
    return jsonify(ok=True)


@app.route("/api/local-llm/rid-map", methods=["GET"])
def api_local_llm_rid_map():
    return jsonify(ok=True, rid_map=_offerup_rid_map_for_api())


@app.route("/api/local-llm/chat", methods=["POST"])
def api_local_llm_chat():
    global ADB_PORT, _ADB_RESOLVED
    if not _local_llm_runtime_ready():
        err = "Задайте провайдер, URL и модель ИИ (⚙ → ИИ)"
        if _llm_provider_needs_key() and not _llm_api_key():
            err = f"Нужен API-ключ для {_llm_provider_preset().get('label', LOCAL_LLM_PROVIDER)}"
        return jsonify(ok=False, error=err), 400
    body = request.get_json(force=True, silent=True) or {}
    msgs_in = body.get("messages")
    clean = _sanitize_llm_chat_messages(msgs_in)
    if not clean:
        return jsonify(ok=False, error="Нужен messages: [{role, content}, ...]"), 400
    buyer_name = str(body.get("buyer_name") or "").strip()[:500]
    buyer_address = str(body.get("buyer_address") or "").strip()[:2000]
    adb_port = (body.get("adb_port") or ADB_PORT or "").strip()
    include_screen = bool(body.get("include_screen"))
    adb_live = bool(adb_port and probe_adb_reachable(adb_port))

    last_user = ""
    for m in reversed(clean):
        if m.get("role") == "user":
            last_user = str(m.get("content") or "").strip()
            break

    if adb_port and adb_port != ADB_PORT:
        ADB_PORT = adb_port
        _ADB_RESOLVED = None
    if adb_port:
        adb_connect()

    blocked, block_code, block_msg = _coach_emulator_blocked(adb_port)
    if blocked:
        return jsonify(
            ok=True,
            reply=block_msg,
            pending_id=0,
            coach_actions_queued=0,
            screen_status=block_code,
            adb_live=False,
        )

    ui_state, screen_summary = ("unknown", "")
    if adb_port:
        ui_state, screen_summary = _coach_ui_offerup_state(adb_port, buyer_name)

    pending_id = 0
    screen_status = "ok" if adb_live else "offline"
    if adb_port and ui_state == "emulator_blocked":
        return jsonify(
            ok=True,
            reply=screen_summary or block_msg,
            pending_id=0,
            coach_actions_queued=0,
            screen_status="startup_error",
            adb_live=adb_live,
        )

    if adb_port and ui_state == "no_offerup":
        acts = ["launch_offerup_app"]
        pending_id, _ = _queue_pending_screen_recovery(
            adb_port,
            context=last_user or "OfferUp не открыт",
            situation="no_offerup",
            note=screen_summary[:200],
            recovery_actions=acts,
            screen_preview_b64=None,
        )
        auto = COACH_PENDING_AUTO_APPROVE
        reply = (
            "OfferUp не открыт. "
            + ("Запускаю автоматически." if auto else "Нажмите «Разрешить» — запущу OfferUp.")
        )
        return jsonify(ok=True, reply=reply, pending_id=pending_id, coach_actions_queued=0, inbox_required=True)

    b64: Optional[str] = None
    screen_obj: Optional[dict] = None
    need_vision = (
        include_screen
        and adb_live
        and _local_llm_vision_ready()
        and adb_port
        and ui_state in ("unknown", "offerup_other", "no_offerup")
        and (_user_asks_about_screen(last_user) or len(screen_summary) < 35)
    )
    if need_vision:
        b64 = adb_screencap_png_base64(adb_port)
        if b64:
            screen_obj = _vision_screen_assist_analyze(
                adb_port,
                b64,
                context=last_user or "coach",
                write_trace=True,
                use_cache=True,
            )
            if screen_obj:
                note_ru = str(screen_obj.get("note") or "").strip()
                if note_ru:
                    screen_summary = note_ru if len(note_ru) >= len(screen_summary) else (screen_summary + " | " + note_ru)[:1400]

    seller_hint = _coach_resolve_seller_hint(last_user, adb_port)
    if not seller_hint and ui_state == "chat" and adb_port:
        root_ch = dump_ui_serial(adb_port)
        if root_ch is not None:
            seller_hint = _offerup_infer_chat_name(root_ch, "") or ""
    coach_sys = _build_coach_system_prompt(
        buyer_name=buyer_name,
        buyer_address=buyer_address,
        ui_state=ui_state,
        adb_port=adb_port,
        screen_summary=screen_summary,
        seller_hint=seller_hint,
        include_screen=include_screen,
        b64=bool(b64),
    )

    if ui_state == "chat":
        user_turn = last_user or "Помоги ответить в открытом чате OfferUp."
    else:
        user_turn = last_user or "Помоги с OfferUp Inbox."
    full_msgs: List[dict] = [
        {"role": "system", "content": coach_sys},
        {"role": "user", "content": user_turn},
    ]

    def _noop_log(_msg: str) -> None:
        return None

    reply = local_llm_chat_with_messages(
        full_msgs,
        _noop_log,
        skip_trace=False,
        trace_kind="coach_chat",
        trace_meta={"last_user": last_user[:1200], "has_screen": bool(b64), "text_only": True},
        model_override=None,
    )
    if reply and _cyrillic_letter_ratio(reply) < 0.28 and "```coach_actions" not in (reply or "").lower():
        reply2 = local_llm_chat_with_messages(
            [
                {
                    "role": "system",
                    "content": "Перепиши ТОЛЬКО на русском, кратко. Сохрани ```coach_actions``` если есть.",
                },
                {"role": "user", "content": (reply or "")[:2500]},
            ],
            _noop_log,
            skip_trace=True,
            trace_kind="coach_chat_ru_retry",
        )
        if reply2 and _cyrillic_letter_ratio(reply2) > _cyrillic_letter_ratio(reply):
            reply = reply2
    if not reply:
        err = _last_local_llm_error or "Нет ответа модели — проверьте провайдер, ключ и модель в ⚙"
        return jsonify(ok=False, error=err), 502
    display_reply, coach_actions = _parse_coach_actions_from_reply(reply)
    coach_actions = _coach_expand_actions(
        coach_actions,
        ui_state,
        seller_hint,
        user_text=last_user,
        buyer_address=buyer_address,
    )
    exec_log: List[str] = []
    executed = 0
    coach_ok = False
    if coach_actions and adb_port and adb_live:
        exec_res = _execute_coach_actions(
            coach_actions,
            adb_port,
            exec_log.append,
            seller_hint=seller_hint,
            ui_state=ui_state,
        )
        coach_ok = _coach_actions_user_success(exec_res, coach_actions)
        executed = int(exec_res.get("steps_ok") or 0)
        if coach_ok:
            display_reply = (display_reply or "").strip()
            if display_reply:
                display_reply += "\n\n"
            if exec_res.get("message_sent"):
                display_reply += "✓ Сообщение отправлено в OfferUp."
            elif exec_res.get("link_sent"):
                display_reply += "✓ Шаблон со ссылкой отправлен."
            elif exec_res.get("chat_opened"):
                display_reply += "✓ Чат открыт на эмуляторе."
            else:
                display_reply += "✓ Действия выполнены на эмуляторе."
        else:
            fail_tail = "; ".join(exec_log[-3:]) if exec_log else "проверьте Inbox и порт MuMu"
            display_reply = (display_reply or "").strip()
            if display_reply:
                display_reply += "\n\n"
            display_reply += f"⚠ На эмуляторе не выполнено: {fail_tail}"
            pending_id, _ = _queue_pending_coach_actions(
                adb_port,
                context=last_user[:800] or "coach chat",
                coach_actions=coach_actions,
                screen_preview_b64=b64,
            )
    elif coach_actions and adb_port:
        pending_id, _ = _queue_pending_coach_actions(
            adb_port,
            context=last_user[:800] or "coach chat",
            coach_actions=coach_actions,
            screen_preview_b64=b64,
        )
    return jsonify(
        ok=True,
        reply=display_reply or reply,
        pending_id=pending_id,
        coach_actions_queued=len(coach_actions),
        coach_actions_executed=executed,
        coach_actions_ok=coach_ok,
        coach_exec_log=exec_log[-12:],
        screen_status=screen_status,
        ui_state=ui_state,
        adb_live=adb_live,
        screen_attached=bool(include_screen and adb_live and b64),
    )


@app.route("/api/ports", methods=["GET"])
def api_ports():
    """
    Поиск портов MuMu: adb devices, конфиги рядом с adb, типичные порты.
    ?probe=0 — не проверять shell (быстрее, только кандидаты).
    ?sessions_only=1 — только сессии (adb devices и ответившие на probe),
       без «мёртвых» портов из vm_config и т.п. (по умолчанию 1).
    После сканирования восстанавливается соединение на текущий ADB_PORT.
    """
    probe = request.args.get("probe", "1") != "0"
    sessions_only = request.args.get("sessions_only", "1") != "0"
    try:
        items = discover_mumu_ports(probe=probe)
    except Exception as e:
        return jsonify(ok=False, error=str(e), items=[], current_port=ADB_PORT, max_probes=MAX_PORT_PROBES), 500
    if sessions_only:
        items = [x for x in items if _is_live_session_port(x)]
    adb_connect()
    return jsonify(
        ok=True,
        items=items,
        current_port=ADB_PORT,
        max_probes=MAX_PORT_PROBES,
        sessions_only=sessions_only,
    )


@app.route("/api/history", methods=["DELETE"])
def api_history_clear():
    _sent_urls.clear()
    return jsonify(ok=True)


@app.route("/api/offerup/config", methods=["GET"])
def api_offerup_config():
    return jsonify(
        ok=True,
        ads_url=OFFERUP_ADS_URL,
        chrome_package=OFFERUP_CHROME_PACKAGE,
        templates=OFFERUP_TEMPLATE_LABELS,
        parser_key_configured=bool(OFFERUP_PARSER_API_KEY),
        link_api_provider=LINK_API_PROVIDER,
        link_providers=["kartatai", "ephedrine"],
        link_key_configured=link_credentials_configured("kartatai", LINK_API_KEY, ""),
        ephedrine_token_configured=link_credentials_configured("ephedrine", "", EPHEDRINE_API_TOKEN),
        link_api_url=LINK_API_URL,
        link_api_user_id=LINK_API_USER_ID,
        ephedrine_api_base=EPHEDRINE_API_BASE,
        ephedrine_service_code=EPHEDRINE_SERVICE_CODE,
        ephedrine_version=EPHEDRINE_VERSION,
        parser_filter_param_names=sorted(OFFERUP_PARSER_QUERY_KEYS),
        offerup_categories=offerup_categories_for_api(),
        timing_defaults={
            "poll_ui": OFFERUP_POLL_UI_SEC,
            "open_wait": OFFERUP_OPEN_WAIT_SEC,
            "ask_wait": OFFERUP_ASK_WAIT_SEC,
            "template_wait": OFFERUP_TEMPLATE_WAIT_SEC,
            "after_template": OFFERUP_WAIT_AFTER_TEMPLATE_SEC,
            "chrome_sleep": 1.0,
            "after_open_sleep": 0.6,
            "after_ask_sleep": 0.22,
            "conversation_cooldown_sec": OFFERUP_CONVERSATION_COOLDOWN_SEC,
            "limit_wait": OFFERUP_LIMIT_WAIT_SEC,
        },
        offerup_try_app_url_first=OFFERUP_TRY_APP_URL_FIRST,
        inbox_defaults={
            "batch_every": OFFERUP_INBOX_BATCH_EVERY,
            "max_replies": OFFERUP_INBOX_MAX_REPLIES,
            "max_scrolls": OFFERUP_INBOX_MAX_SCROLLS,
            "open_wait": OFFERUP_INBOX_OPEN_WAIT_SEC,
            "scan_timeout": OFFERUP_INBOX_SCAN_TIMEOUT_SEC,
            "screen_assist": bool(LOCAL_LLM_SCREEN_ASSIST),
            "use_listing_photo": bool(LOCAL_LLM_USE_LISTING_IMAGE_INBOX),
        },
        local_llm={
            "enabled_default": LOCAL_LLM_ENABLED,
            "chat_url_configured": bool(LOCAL_LLM_CHAT_URL),
            "model": LOCAL_LLM_MODEL,
            "vision_model": LOCAL_LLM_VISION_MODEL,
            "vision_ready": bool(_local_llm_vision_ready()),
            "api_style": LOCAL_LLM_API_STYLE,
            "timeout_sec": LOCAL_LLM_TIMEOUT_SEC,
            "hint": "Текстовая модель — JSON Inbox. Модель зрения (llava / qwen2.5vl и т.д.) — скриншот + фото объявления; задайте local_llm_vision_model в форме или mumu_ui_config.json.",
        },
    )


@app.route("/api/offerup/status", methods=["GET"])
def api_offerup_status():
    with _link_queue_lock:
        queue = list(_link_queue)
    with _offerup_alerts_lock:
        alerts = list(reversed(_offerup_alerts[-20:]))
    with _inbox_manual_lock:
        inbox_scan = {
            "running": bool(_inbox_manual_state.get("running")),
            "loop_mode": bool(_inbox_manual_state.get("loop_mode")),
            "stop_requested": bool(_inbox_manual_state.get("stop_requested")),
            "log": list(_inbox_manual_state.get("log") or []),
            "error": str(_inbox_manual_state.get("error") or ""),
        }
    with _offerup_lock:
        return jsonify(
            ok=True,
            state=_offerup_job.get("state", "idle"),
            log=list(_offerup_job.get("log") or []),
            error=str(_offerup_job.get("error") or ""),
            link_queue=queue,
            alerts=alerts,
            coach_feed=coach_feed_for_poll(),
            coach_chat_inject=coach_chat_inject_for_poll(),
            pending_ai_actions=pending_ai_actions_for_poll(),
            inbox_scan=inbox_scan,
        )


@app.route("/api/coach/feed", methods=["GET"])
def api_coach_feed_get():
    return jsonify(ok=True, items=coach_feed_for_poll())


@app.route("/api/coach/feed", methods=["DELETE"])
def api_coach_feed_clear():
    clear_coach_feed()
    return jsonify(ok=True)


@app.route("/api/ai-training", methods=["GET"])
def api_ai_training_get():
    data = _load_ai_training_file_raw()
    return jsonify(
        ok=True,
        path=MUMU_AI_TRAINING_PATH,
        rules=data.get("rules") or [],
        rule_ids=data.get("rule_ids") or [],
        count=len(data.get("rules") or []),
    )


@app.route("/api/ai-training", methods=["POST"])
def api_ai_training_post():
    body = request.get_json(silent=True) or {}
    rules_in = body.get("rules")
    if not isinstance(rules_in, list):
        return jsonify(ok=False, error="Ожидается JSON { \"rules\": [ \"...\", ... ] }"), 400
    rules = [_collapse_ws(str(x)) for x in rules_in if _collapse_ws(str(x))]
    added = import_ai_training_rules(rules, source="import", notify=True, push_github=True)
    merge_ai_training_file_into_memory()
    return jsonify(ok=True, added=added, total=len(_load_ai_training_file_raw().get("rules") or []))


@app.route("/api/ai-training/rule", methods=["POST"])
def api_ai_training_rule_post():
    body = request.get_json(silent=True) or {}
    rule = str(body.get("rule") or body.get("text") or "").strip()
    if not rule:
        return jsonify(ok=False, error="Пустое правило"), 400
    is_new = add_ai_training_rule(rule, source="api")
    return jsonify(ok=True, added=is_new)


@app.route("/api/ai-training/github-push", methods=["POST"])
def api_ai_training_github_push():
    body = request.get_json(silent=True) or {}
    if body.get("github_training_repo"):
        global GITHUB_TRAINING_REPO
        GITHUB_TRAINING_REPO = str(body.get("github_training_repo") or "").strip()[:300]
    tok = str(body.get("github_training_token") or "").strip()
    if tok:
        global GITHUB_TRAINING_TOKEN
        GITHUB_TRAINING_TOKEN = tok[:500]
        try:
            _persist_settings_merge({"github_training_token": GITHUB_TRAINING_TOKEN})
        except Exception:
            pass
    return _json_api_result(github_push_training_file(reason="manual", force=True))


@app.route("/api/ai-training/github-sync", methods=["POST"])
def api_ai_training_github_sync():
    body = request.get_json(silent=True) or {}
    if body.get("github_training_repo"):
        global GITHUB_TRAINING_REPO
        GITHUB_TRAINING_REPO = str(body.get("github_training_repo") or "").strip()[:300]
    if body.get("github_training_branch"):
        global GITHUB_TRAINING_BRANCH
        GITHUB_TRAINING_BRANCH = str(body.get("github_training_branch") or "main").strip()[:120]
    if body.get("github_training_path"):
        global GITHUB_TRAINING_PATH
        GITHUB_TRAINING_PATH = str(body.get("github_training_path") or "").strip().lstrip("/")[:300]
    tok = str(body.get("github_training_token") or "").strip()
    if tok:
        global GITHUB_TRAINING_TOKEN
        GITHUB_TRAINING_TOKEN = tok[:500]
    return _json_api_result(github_sync_training_rules(force=True))


def _apply_github_body_settings(body: dict) -> None:
    global GITHUB_TRAINING_REPO, GITHUB_TRAINING_BRANCH, GITHUB_TRAINING_PATH, GITHUB_TRAINING_TOKEN
    if body.get("github_training_repo"):
        GITHUB_TRAINING_REPO = str(body.get("github_training_repo") or "").strip()[:300]
    if body.get("github_training_branch"):
        GITHUB_TRAINING_BRANCH = str(body.get("github_training_branch") or "main").strip()[:120]
    if body.get("github_training_path"):
        GITHUB_TRAINING_PATH = str(body.get("github_training_path") or "").strip().lstrip("/")[:300]
    tok = str(body.get("github_training_token") or "").strip()
    if tok:
        GITHUB_TRAINING_TOKEN = tok[:500]
        try:
            _persist_settings_merge({"github_training_token": GITHUB_TRAINING_TOKEN})
        except Exception:
            pass


def _json_api_result(result: dict):
    payload = dict(result or {})
    payload["ok"] = bool(payload.get("ok"))
    return jsonify(payload)


@app.route("/api/app-update/check", methods=["GET", "POST"])
def api_app_update_check():
    body = request.get_json(silent=True) or {}
    if request.method == "POST":
        _apply_github_body_settings(body)
    return _json_api_result(app_update_check())


@app.route("/api/app-update/apply", methods=["POST"])
def api_app_update_apply():
    body = request.get_json(silent=True) or {}
    _apply_github_body_settings(body)
    return _json_api_result(app_update_apply())


@app.route("/api/app-update/publish", methods=["POST"])
def api_app_update_publish():
    body = request.get_json(silent=True) or {}
    _apply_github_body_settings(body)
    new_ver = str(body.get("version") or body.get("new_version") or "").strip()
    msg = str(body.get("commit_message") or body.get("message") or "").strip()
    return _json_api_result(app_update_publish(new_version=new_ver, commit_message=msg))


@app.route("/api/github/check-access", methods=["GET", "POST"])
def api_github_check_access():
    body = request.get_json(silent=True) or {}
    if body:
        _apply_github_body_settings(body)
    return _json_api_result(github_check_repo_access())


@app.route("/api/mumu/uidump", methods=["GET"])
def api_mumu_uidump():
    """Диагностика UI dump: узлы, парсер Inbox (текст + resource-id)."""
    global ADB_PORT, _ADB_RESOLVED
    port = (request.args.get("adb_port") or ADB_PORT or "").strip()
    if not port:
        return jsonify(ok=False, error="Укажите adb_port"), 400
    if port != ADB_PORT:
        ADB_PORT = port
        _ADB_RESOLVED = None
    adb_connect()
    root = dump_ui_serial(port)
    if root is None:
        return jsonify(ok=False, error="UI dump недоступен"), 502

    scr_w, scr_h = screen_size(root)
    nodes_raw: List[dict] = []
    for node in root.iter("node"):
        txt = _collapse_ws(node.get("text") or "")
        cdesc = _collapse_ws(node.get("content-desc") or "")
        pr = parse_bounds(node.get("bounds") or "")
        if not txt and not cdesc and not _offerup_resource_id(node):
            continue
        entry: dict = {
            "text": txt,
            "content_desc": cdesc,
            "class": (node.get("class") or "").split(".")[-1],
            "resource_id": _offerup_resource_id(node),
            "package": node.get("package") or "",
            "clickable": node.get("clickable") == "true",
            "bounds": node.get("bounds") or "",
        }
        if pr:
            l, t, r, b = pr
            cy = (t + b) // 2
            cx = (l + r) // 2
            entry["cx"] = cx
            entry["cy"] = cy
            entry["w"] = r - l
            entry["h"] = b - t
            nav_cut = int(scr_h * OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC) if scr_h else 9999
            entry["in_inbox_zone"] = bool(
                scr_h and OFFERUP_INBOX_LIST_TOP_Y_MIN <= cy <= nav_cut
            )
            entry["noise"] = _looks_like_inbox_noise(txt or cdesc)
            entry["time_like"] = _looks_like_inbox_time(txt or cdesc)
            entry["valid_preview"] = _looks_like_valid_inbox_preview(txt or cdesc)
        nodes_raw.append(entry)

    inbox_nodes, _, _ = _offerup_inbox_list_nodes(root)
    raw_rows = _offerup_collect_inbox_rows_from_nodes(inbox_nodes, scr_w, scr_h)
    final_rows = _offerup_collect_inbox_rows(root)
    rid_rows = _offerup_parse_inbox_rows_by_resource_id(root)
    screen_hint = "unknown"
    for node in root.iter("node"):
        rid = _offerup_resource_id(node)
        if rid == "DiscussionScreen":
            screen_hint = "offerup_chat"
            break
        if "messages-tab" in rid.lower():
            screen_hint = "offerup_inbox"
            break
        if "OnboardingSearchLocationScreen" in rid:
            screen_hint = "offerup_zip"
            break
        if "OnboardingBuyerInterestSelectionScreen" in rid:
            screen_hint = "offerup_categories"
            break
        if "auth-landing-screen" in rid or "email-landing-screen" in rid:
            screen_hint = "offerup_auth"

    return jsonify(
        ok=True,
        adb_port=port,
        screen_wh=[scr_w, scr_h],
        screen_hint=screen_hint,
        offerup_inbox_list_tap_max_y_frac=OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC,
        inbox_list_top_y_min=OFFERUP_INBOX_LIST_TOP_Y_MIN,
        nav_cut=int(scr_h * OFFERUP_INBOX_LIST_TAP_MAX_Y_FRAC) if scr_h else 9999,
        all_nodes=nodes_raw,
        inbox_parser_nodes=inbox_nodes,
        raw_rows_before_filter=raw_rows,
        inbox_rid_rows=rid_rows,
        final_rows=final_rows,
        captured_at=time.strftime("%H:%M:%S"),
    )


@app.route("/api/mumu/screen", methods=["GET"])
def api_mumu_screen_get():
    global ADB_PORT, _ADB_RESOLVED
    port = (request.args.get("adb_port") or ADB_PORT or "").strip()
    if not port:
        return jsonify(ok=False, error="Укажите adb_port"), 400
    if port != ADB_PORT:
        ADB_PORT = port
        _ADB_RESOLVED = None
    adb_connect()
    b64 = adb_screencap_png_base64(port)
    if not b64:
        return jsonify(ok=False, error="Не удалось снять экран (adb screencap)"), 502
    return jsonify(ok=True, adb_port=port, image_base64=b64, captured_at=time.strftime("%H:%M:%S"))


@app.route("/api/mumu/screen/analyze", methods=["POST"])
def api_mumu_screen_analyze():
    """Ручной анализ экрана vision-моделью; при необходимости — в очередь на подтверждение."""
    if not _local_llm_vision_ready():
        return jsonify(ok=False, error="Задайте vision-модель и URL в настройках ИИ"), 400
    body = request.get_json(force=True, silent=True) or {}
    port, err = _resolve_adb_port_for_api(body, query_port=(request.args.get("adb_port") or "").strip())
    if err:
        return jsonify(ok=False, error=err), 400
    context = str(body.get("context") or "Ручной запрос оператора из UI").strip()[:1400]
    quiet = bool(body.get("quiet"))
    b64 = adb_screencap_png_base64(port)
    if not b64:
        return jsonify(ok=False, error="Не удалось снять экран"), 502

    obj = _vision_screen_assist_analyze(
        port, b64, context=context, write_trace=not quiet, use_cache=bool(quiet)
    )
    if not obj:
        err = _last_local_llm_error or "Пустой ответ / нет JSON — попробуйте llava или qwen2.5vl"
        return jsonify(ok=False, error=err), 502
    snap = {
        "situation": obj.get("situation"),
        "offerup_missing_or_background": obj.get("offerup_missing_or_background"),
        "recovery_actions": obj.get("recovery_actions"),
        "note": (str(obj.get("note") or "")[:200]),
    }
    actions = obj.get("recovery_actions") or []
    if not isinstance(actions, list):
        actions = []
    acts_str = [str(a) for a in actions]
    if bool(obj.get("offerup_missing_or_background")) and not any(
        "launch" in str(a).lower() or "offerup" in str(a).lower() for a in acts_str
    ):
        acts_str.append("launch_offerup_app")
    elif _situation_offerup_open_not_inbox(obj.get("situation")) and not any("inbox" in str(a).lower() for a in acts_str):
        acts_str = [a for a in acts_str if str(a).lower() not in ("none", "launch_offerup_app")]
        acts_str.append("open_inbox")
    pending_id = 0
    if LOCAL_LLM_SCREEN_ASSIST and not LOCAL_LLM_SCREEN_ASSIST_AUTO:
        pending_id, _ = _queue_pending_screen_recovery(
            port,
            context=context,
            situation=obj.get("situation"),
            note=str(obj.get("note") or ""),
            recovery_actions=acts_str,
            screen_preview_b64=b64,
        )
    elif LOCAL_LLM_SCREEN_ASSIST and LOCAL_LLM_SCREEN_ASSIST_AUTO:
        _execute_recovery_actions(acts_str, port, lambda m: None)
    return jsonify(ok=True, analysis=snap, pending_id=pending_id, adb_port=port)


@app.route("/api/ai/pending", methods=["GET"])
def api_ai_pending_list():
    return jsonify(ok=True, items=pending_ai_actions_for_poll())


@app.route("/api/ai/pending/<int:action_id>/approve", methods=["POST"])
def api_ai_pending_approve(action_id: int):
    global ADB_PORT, _ADB_RESOLVED
    with _pending_ai_actions_lock:
        row = next((x for x in _pending_ai_actions if int(x.get("id") or 0) == action_id), None)
        if not row:
            return jsonify(ok=False, error="Действие не найдено или уже обработано"), 404
        _pending_ai_actions[:] = [x for x in _pending_ai_actions if int(x.get("id") or 0) != action_id]
    port = str(row.get("port") or ADB_PORT)
    if port and port != ADB_PORT:
        ADB_PORT = port
        _ADB_RESOLVED = None
    adb_connect()
    coach_acts = row.get("coach_actions") or []
    acts = row.get("recovery_actions") or []
    log_lines: List[str] = []

    def _log(m: str) -> None:
        log_lines.append(m)

    coach_ok = True
    if isinstance(coach_acts, list) and coach_acts:
        seller_hint = ""
        for a in coach_acts:
            if isinstance(a, dict) and str(a.get("seller") or a.get("name") or "").strip():
                seller_hint = str(a.get("seller") or a.get("name") or "").strip()
                break
        ui_state, _ = _coach_ui_offerup_state(port, "")
        coach_acts = _coach_expand_actions(coach_acts, ui_state, seller_hint)
        exec_res = _execute_coach_actions(
            coach_acts,
            port,
            _log,
            seller_hint=seller_hint,
            ui_state=ui_state,
        )
        coach_ok = _coach_actions_user_success(exec_res, coach_acts)
        summary = json.dumps(coach_acts, ensure_ascii=False)[:400]
    else:
        _execute_recovery_actions(acts if isinstance(acts, list) else [], port, _log)
        summary = ", ".join(str(a) for a in (acts if isinstance(acts, list) else []))
    if coach_ok:
        append_coach_feed_from_inbox(f"Оператор подтвердил действия #{action_id} на {port}: {summary}")
    else:
        tail = "; ".join(log_lines[-4:]) if log_lines else "не удалось"
        append_coach_feed_from_inbox(f"Подтверждение #{action_id} на {port} — ошибка: {tail}")
    err_msg = ""
    if not coach_ok and log_lines:
        err_msg = log_lines[-1]
    return jsonify(
        ok=coach_ok,
        error=err_msg or (None if coach_ok else "Действие не выполнено на эмуляторе"),
        log=log_lines,
        adb_port=port,
        coach_actions_ok=coach_ok,
    )


@app.route("/api/ai/pending/<int:action_id>/reject", methods=["POST"])
def api_ai_pending_reject(action_id: int):
    with _pending_ai_actions_lock:
        before = len(_pending_ai_actions)
        _pending_ai_actions[:] = [x for x in _pending_ai_actions if int(x.get("id") or 0) != action_id]
        removed = before != len(_pending_ai_actions)
    if not removed:
        return jsonify(ok=False, error="Действие не найдено"), 404
    append_coach_feed_from_inbox(f"Оператор отклонил предложение ИИ #{action_id}.")
    return jsonify(ok=True)


@app.route("/api/offerup/inbox-scan", methods=["POST"])
def api_offerup_inbox_scan():
    """Inbox: POST с loop_mode=true — цикл до stop; повторный POST при running — остановка."""
    body = request.get_json(force=True, silent=True) or {}

    force_standalone = bool(body.get("force_standalone"))
    if not force_standalone:
        with _offerup_lock:
            if _offerup_job.get("state") == "running":
                return jsonify(ok=False, error="Дождитесь окончания сценария OfferUp или остановите его"), 409
        with _autorun_lock:
            if _autorun_state.get("running"):
                return jsonify(ok=False, error="Остановите авторежим перед ручным просмотром Inbox (или включите «Inbox параллельно»)"), 409

    loop_mode = bool(body.get("loop_mode", body.get("continuous", True)))

    with _inbox_manual_lock:
        if _inbox_manual_state.get("running"):
            was_loop = bool(_inbox_manual_state.get("loop_mode"))
            force_stop_request(log_line="Inbox: принудительная остановка…")
            stop_ports: List[str] = []
            pr = body.get("ports") or []
            if isinstance(pr, str):
                pr = [p.strip() for p in pr.split(",") if p.strip()]
            stop_ports = [str(p).strip() for p in pr if str(p).strip()]
            if not stop_ports:
                sp = (body.get("adb_port") or ADB_PORT or "").strip()
                if sp:
                    stop_ports = [sp]
            force_stop_offerup_apps(stop_ports or None)
            return jsonify(ok=True, action="stop", loop_mode=was_loop)

    ports_raw = body.get("ports") or []
    if isinstance(ports_raw, str):
        ports_raw = [p.strip() for p in ports_raw.split(",") if p.strip()]
    ports = [str(p).strip() for p in ports_raw if str(p).strip()]
    if not ports:
        single = (body.get("adb_port") or "").strip()
        if single:
            ports = [single]
    port = ports[0] if ports else None
    if not port:
        return jsonify(ok=False, error="Нет порта ADB: отметьте профиль MuMu или укажите adb_port"), 400

    force_stop_clear()
    with _inbox_manual_lock:
        _inbox_manual_state["running"] = True
        _inbox_manual_state["loop_mode"] = loop_mode
        _inbox_manual_state["stop_requested"] = False
        _inbox_manual_state["log"] = []
        _inbox_manual_state["error"] = ""

    buyer_name_raw = (body.get("buyer_name") or "").strip()
    buyer_address_raw = (body.get("buyer_address") or "").strip()
    randomize_name = bool(body.get("randomize_name"))
    randomize_address = bool(body.get("randomize_address"))
    buyer_name = resolve_buyer_name(buyer_name_raw, randomize_name)
    buyer_address = resolve_buyer_address(buyer_address_raw, randomize_address)

    inbox_cfg = offerup_sanitize_inbox_settings(body.get("inbox_settings") or body.get("offerup_inbox") or {})
    message_templates = _normalize_templates_from_body(body)
    local_llm_inbox_flag = bool(body.get("local_llm_inbox"))
    llm_inbox_on = _effective_local_llm_inbox(local_llm_inbox_flag)

    th = threading.Thread(
        target=_offerup_inbox_manual_worker,
        args=(port, inbox_cfg, message_templates, llm_inbox_on, buyer_name, buyer_address),
        kwargs={"loop_mode": loop_mode},
        daemon=True,
    )
    th.start()
    return jsonify(ok=True, action="start", loop_mode=loop_mode, port=port)


@app.route("/api/offerup/inbox-scan/stop", methods=["POST"])
def api_offerup_inbox_scan_stop():
    """Остановить текущий проход Inbox (ручной или внутри авторежима)."""
    body = request.get_json(force=True, silent=True) or {}
    force_stop_request(log_line="Inbox: принудительная остановка (stop)…")
    ports: List[str] = []
    pr = body.get("ports") or []
    if isinstance(pr, str):
        pr = [p.strip() for p in pr.split(",") if p.strip()]
    ports = [str(p).strip() for p in pr if str(p).strip()]
    if not ports:
        sp = (body.get("adb_port") or ADB_PORT or "").strip()
        if sp:
            ports = [sp]
    force_stop_offerup_apps(ports or None)
    return jsonify(ok=True)


@app.route("/api/force-stop", methods=["POST"])
def api_force_stop():
    """Принудительно остановить все фоновые задачи (Inbox, авторежим, онбординг, OfferUp)."""
    body = request.get_json(force=True, silent=True) or {}
    force_stop_request(log_line="Принудительная остановка всех задач…")
    ports: List[str] = []
    pr = body.get("ports") or []
    if isinstance(pr, str):
        pr = [p.strip() for p in pr.split(",") if p.strip()]
    ports = [str(p).strip() for p in pr if str(p).strip()]
    if not ports:
        sp = (body.get("adb_port") or "").strip()
        if sp:
            ports = [sp]
    with _autorun_lock:
        cp = str(_autorun_state.get("current_port") or "").strip()
        if cp:
            ports.append(cp)
    force_stop_offerup_apps(ports or None)
    return jsonify(ok=True, ports=ports)


@app.route("/api/link-queue/<int:item_id>", methods=["DELETE"])
def api_link_queue_delete(item_id: int):
    with _link_queue_lock:
        before = len(_link_queue)
        _link_queue[:] = [x for x in _link_queue if int(x.get("id") or 0) != item_id]
    save_persistent_state()
    return jsonify(ok=True, removed=(len(_link_queue) != before))


@app.route("/api/offerup/start", methods=["POST"])
def api_offerup_start():
    """Chrome → OPEN → Ask → текст шаблона (правила по категории или случайный из app.py)."""
    global ADB_PORT, _ADB_RESOLVED

    body = request.get_json(force=True, silent=True) or {}
    ad_url = (body.get("ad_url") or "").strip()
    # Пустое поле ссылки → первое объявление с API; иначе → открыть эту ссылку в Chrome.
    from_api = not bool(ad_url)
    api_key = (body.get("api_key") or OFFERUP_PARSER_API_KEY or "").strip()
    link_api_provider = normalize_link_api_provider(body.get("link_api_provider") or LINK_API_PROVIDER)
    link_api_key = (body.get("link_api_key") or LINK_API_KEY or "").strip()
    link_api_url = _coalesce_setting(body.get("link_api_url"), LINK_API_URL)
    link_api_user_id = _coalesce_setting(body.get("link_api_user_id"), LINK_API_USER_ID)
    ephedrine_token = (body.get("ephedrine_api_token") or body.get("ephedrine_token") or EPHEDRINE_API_TOKEN or "").strip()
    ephedrine_base = _coalesce_setting(body.get("ephedrine_api_base"), EPHEDRINE_API_BASE)
    ephedrine_service_code = _coalesce_setting(body.get("ephedrine_service_code"), EPHEDRINE_SERVICE_CODE)
    ephedrine_version = _coalesce_setting(body.get("ephedrine_version"), EPHEDRINE_VERSION)
    ephedrine_domain_id = _parse_optional_positive_int(body.get("ephedrine_domain_id"))
    ephedrine_profile_id = _parse_optional_positive_int(body.get("ephedrine_profile_id"))
    adb_port = (body.get("adb_port") or "").strip()
    if adb_port and adb_port != ADB_PORT:
        ADB_PORT = adb_port
        _ADB_RESOLVED = None

    if from_api and not api_key:
        return jsonify(ok=False, error="Поле ссылки пустое — нужен парсер: api-key в форме или OFFERUP_PARSER_API_KEY в app.py"), 400

    filter_params = offerup_sanitize_parser_filters(body.get("parser_filters") or body.get("filters"))
    timing_raw = body.get("offerup_timing") or body.get("timing_offerup")
    chrome_package = offerup_sanitize_chrome_package(str(body.get("chrome_package") or ""))
    template_rules = offerup_sanitize_template_rules(body.get("template_rules"))
    manual_cat: Optional[int] = None
    raw_mc = body.get("manual_template_category_id")
    if raw_mc is not None and str(raw_mc).strip() != "":
        try:
            manual_cat = int(raw_mc)
        except (TypeError, ValueError):
            manual_cat = None
        if manual_cat is not None and (manual_cat < 1 or manual_cat > 12):
            manual_cat = None

    buyer_name_raw = (body.get("buyer_name") or "").strip()
    buyer_address_raw = (body.get("buyer_address") or "").strip()
    randomize_name = bool(body.get("randomize_name"))
    randomize_address = bool(body.get("randomize_address"))
    buyer_name = resolve_buyer_name(buyer_name_raw, randomize_name)
    buyer_address = resolve_buyer_address(buyer_address_raw, randomize_address)

    with _offerup_lock:
        if _offerup_job.get("state") == "running":
            return jsonify(ok=False, error="Сценарий уже выполняется"), 409
        _offerup_job["state"] = "running"
        _offerup_job["log"] = []
        _offerup_job["error"] = ""

    th = threading.Thread(
        target=_offerup_worker,
        args=(
            ad_url,
            from_api,
            api_key,
            link_api_provider,
            link_api_key,
            link_api_url,
            link_api_user_id,
            ephedrine_token,
            ephedrine_base,
            ephedrine_service_code,
            ephedrine_version,
            ephedrine_domain_id,
            ephedrine_profile_id,
            filter_params,
            timing_raw,
            chrome_package,
            template_rules,
            manual_cat,
            buyer_name,
            buyer_address,
        ),
        daemon=True,
    )
    th.start()
    return jsonify(ok=True)


def _autorun_log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    with _autorun_lock:
        _autorun_state.setdefault("log", []).append(line)
        if len(_autorun_state["log"]) > 500:
            _autorun_state["log"] = _autorun_state["log"][-300:]


def _autorun_worker(
    ports: List[str],
    ad_url: str,
    from_api: bool,
    api_key: str,
    link_api_provider: str,
    link_api_key: str,
    link_api_url: str,
    link_api_user_id: str,
    ephedrine_token: str,
    ephedrine_base: str,
    ephedrine_service_code: str,
    ephedrine_version: str,
    ephedrine_domain_id: Optional[int],
    ephedrine_profile_id: Optional[int],
    filter_params: Optional[dict],
    timing_overrides: Optional[dict],
    chrome_package: str,
    template_rules: Optional[List[dict]],
    manual_cat: Optional[int],
    delay_between_sec: float,
    buyer_name: str = "",
    buyer_address: str = "",
    randomize_name: bool = False,
    randomize_address: bool = False,
    inbox_settings: Optional[dict] = None,
    message_templates: Optional[List[str]] = None,
    local_llm_inbox_flag: bool = False,
) -> None:
    """Autorun loop. Each selected port is processed on its own ADB serial."""
    rules = offerup_sanitize_template_rules(template_rules or [])
    cpkg = offerup_sanitize_chrome_package(chrome_package)
    inbox_cfg = offerup_sanitize_inbox_settings(inbox_settings or {})
    xgd_templates = [str(x).strip() for x in (message_templates or []) if str(x).strip()]
    llm_inbox_on = _effective_local_llm_inbox(local_llm_inbox_flag)

    def _stop_requested() -> bool:
        if force_stop_is_set():
            return True
        if inbox_scan_abort_is_set():
            return True
        with _autorun_lock:
            return bool(_autorun_state.get("stop_requested"))

    def _sleep_interruptible(seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if _stop_requested():
                return
            time.sleep(min(0.2, deadline - time.monotonic()))

    def _log_port(port: str, msg: str) -> None:
        _autorun_log(f"[{port}] {msg}")

    def _run_one(port: str) -> bool:
        _log_port(port, "iteration start")
        try:
            if _stop_requested():
                return False
            fp = offerup_sanitize_parser_filters(filter_params or {})
            url = (ad_url or "").strip()
            item_meta: Optional[dict] = None
            queued_row: Optional[dict] = None
            iter_name = resolve_buyer_name(buyer_name, randomize_name)
            iter_addr = resolve_buyer_address(buyer_address, randomize_address)
            if from_api:
                if not api_key:
                    raise RuntimeError("api-key is required")
                with _autorun_fetch_lock:
                    url, item_meta, item_key = offerup_fetch_unseen_ad_with_item(api_key, fp, _autorun_seen_ad_keys)
                    _autorun_seen_ad_keys.add(item_key)
                _log_port(port, "reserved ad: " + url[:80])
                if link_credentials_configured(link_api_provider, link_api_key, ephedrine_token):
                    link_data = create_link_for_item(
                        link_api_provider, item_meta, url,
                        kartatai_key=link_api_key, kartatai_url=link_api_url, kartatai_user_id=link_api_user_id,
                        ephedrine_token=ephedrine_token, ephedrine_base=ephedrine_base,
                        ephedrine_service_code=ephedrine_service_code, ephedrine_version=ephedrine_version,
                        ephedrine_domain_id=ephedrine_domain_id, ephedrine_profile_id=ephedrine_profile_id,
                        buyer_name=iter_name, buyer_address=iter_addr,
                    )
                    queued_row = add_link_queue_item(item_meta, link_data, url, port=port)
                    prov = normalize_link_api_provider(link_api_provider)
                    if queued_row:
                        _log_port(
                            port,
                            f"{prov} queued: {queued_row['name']} | fish={queued_row.get('fish_link', '—')[:60]}",
                        )
                    else:
                        _log_port(port, f"{prov}: response has no link URL")
            if not url.startswith("http"):
                raise RuntimeError("No http(s) URL")
            cid = offerup_resolve_category_for_template(item_meta, fp)
            if cid is None and manual_cat is not None and 1 <= manual_cat <= 12:
                cid = manual_cat
            tmpl, src = offerup_pick_template_text(cid, rules)
            _log_port(port, f"template ({src}): {tmpl[:50]}")
            t = offerup_merge_action_timing(timing_overrides, apply_jitter=True)
            _log_port(
                port,
                f"timing: chrome={t['chrome_sleep']:.2f}s open={t['after_open_sleep']:.2f}s "
                f"ask={t['after_ask_sleep']:.2f}s after={t['after_template']:.2f}s",
            )
            pkg = cpkg or OFFERUP_CHROME_PACKAGE
            # Force-stop OfferUp before each iteration to prevent stale state
            _offerup_force_stop_app(port)
            if not offerup_open_listing_in_chrome(
                url,
                chrome_package=pkg,
                chrome_sleep=t["chrome_sleep"],
                serial=port,
                log_func=lambda m: _log_port(port, m),
            ):
                raise RuntimeError("Chrome/listing did not open")
            if _stop_requested():
                return False
            open_state, ask_xy = _offerup_wait_open_or_app(
                t["open_wait"],
                t["poll_ui"],
                serial=port,
                should_stop=_stop_requested,
                log_func=lambda m: _log_port(port, m),
                listing_url=url,
                skip_open_cta=OFFERUP_TRY_APP_URL_FIRST,
            )
            if open_state == "timeout":
                if OFFERUP_TRY_APP_URL_FIRST:
                    raise RuntimeError("OfferUp app: Ask не появился (OPEN в Chrome не проверялся)")
                raise RuntimeError("Neither OPEN nor OfferUp app screen was found (Ask did not appear)")
            if open_state == "stopped":
                return False
            if open_state == "already_app":
                _sleep_interruptible(t["after_open_sleep"])
            if _stop_requested():
                return False
            if not _offerup_tap_ask_button(
                port,
                ask_xy,
                t,
                should_stop=_stop_requested,
                log_func=lambda m: _log_port(port, m),
            ):
                if _stop_requested():
                    return False
                raise RuntimeError("Ask was not found")
            _sleep_interruptible(t["after_ask_sleep"])
            if _stop_requested():
                return False
            _offerup_dismiss_if_popup(port)
            _offerup_wait_if_limit_popup(
                port,
                t.get("conversation_cooldown", OFFERUP_CONVERSATION_COOLDOWN_SEC),
                log_func=lambda m: _log_port(port, m),
                should_stop=_stop_requested,
            )
            if _stop_requested():
                return False
            if not _offerup_apply_template_message(
                port,
                tmpl,
                t,
                should_stop=_stop_requested,
                log_func=lambda m: _log_port(port, m),
            ):
                if _stop_requested():
                    return False
                raise RuntimeError("Template message was not sent: " + tmpl[:40])
            _sleep_interruptible(t["after_template"])
            if queued_row and queued_row.get("created_link_id"):
                _offerup_try_capture_seller_from_chat(
                    port,
                    queued_row["created_link_id"],
                    lambda m: _log_port(port, m),
                )
            if _stop_requested():
                return False
            _offerup_log_link_queued_for_inbox(
                queued_row,
                lambda m: _log_port(port, m),
            )
            add_send_history_item(item_meta, url, queued_row, port=port)
            with _autorun_lock:
                _autorun_state["count"] = _autorun_state.get("count", 0) + 1
                done_count = _autorun_state["count"]
            _log_port(port, f"OK iteration #{done_count}")
            if done_count % int(inbox_cfg["batch_every"]) == 0:
                handled = offerup_process_inbox_replies(
                    port,
                    lambda m: _log_port(port, m),
                    should_stop=_stop_requested,
                    max_replies=int(inbox_cfg["max_replies"]),
                    max_scrolls=int(inbox_cfg["max_scrolls"]),
                    open_wait=float(inbox_cfg["open_wait"]),
                    scan_timeout=float(inbox_cfg["scan_timeout"]),
                    message_templates=xgd_templates,
                    local_llm_inbox=llm_inbox_on,
                    buyer_name_on_profile=iter_name,
                    buyer_address_on_profile=iter_addr,
                    screen_assist=bool(inbox_cfg.get("screen_assist", True)),
                    use_listing_photo=bool(inbox_cfg.get("use_listing_photo", True)),
                )
                _log_port(port, f"Inbox scan after #{done_count}: handled {handled}")
            return True
        except Exception as e:
            with _autorun_lock:
                _autorun_state["errors"] = _autorun_state.get("errors", 0) + 1
            msg = str(e)
            _log_port(port, f"error: {msg}")
            if bool(inbox_cfg.get("screen_assist", True)) and _local_llm_vision_ready():
                _offerup_try_vision_screen_recovery(port, f"Autorun exception: {msg}", lambda m: _log_port(port, m))
            return False

    try:
        active_ports = [p for p in ports if p] or [ADB_PORT]
        _autorun_log("Autorun started: " + ", ".join(active_ports))
        while not _stop_requested():
            with _autorun_lock:
                _autorun_state["current_port"] = ", ".join(active_ports)
            threads: List[threading.Thread] = []
            for port in active_ports:
                th = threading.Thread(target=_run_one, args=(port,), daemon=True)
                threads.append(th)
                th.start()
            while any(th.is_alive() for th in threads):
                if _stop_requested():
                    _autorun_log("Stop requested; waiting for active steps to exit...")
                    break
                time.sleep(0.2)
            for th in threads:
                th.join(timeout=0.2)
            if _stop_requested():
                _autorun_log("Stopped by request.")
                break
            _sleep_interruptible(delay_between_sec)
    finally:
        with _autorun_lock:
            _autorun_state["running"] = False
            _autorun_state["stop_requested"] = False
            _autorun_state["current_port"] = ""
        _autorun_log("Autorun finished.")

@app.route("/api/offerup/autorun/start", methods=["POST"])
def api_offerup_autorun_start():
    """Запуск авторежима — бесконечный цикл по всем выбранным портам."""
    global ADB_PORT, _ADB_RESOLVED
    body = request.get_json(force=True, silent=True) or {}

    with _autorun_lock:
        if _autorun_state.get("running"):
            return jsonify(ok=False, error="Авторежим уже запущен"), 409

    ports_raw = body.get("ports") or []
    if isinstance(ports_raw, str):
        ports_raw = [p.strip() for p in ports_raw.split(",") if p.strip()]
    ports = [str(p).strip() for p in ports_raw if str(p).strip()]
    if not ports:
        single = (body.get("adb_port") or ADB_PORT or "").strip()
        if single:
            ports = [single]
    if not ports:
        return jsonify(ok=False, error="Нет порта ADB: отметьте профиль MuMu или укажите adb_port"), 400

    ad_url = (body.get("ad_url") or "").strip()
    from_api = not bool(ad_url)
    api_key = (body.get("api_key") or OFFERUP_PARSER_API_KEY or "").strip()
    link_api_provider = normalize_link_api_provider(body.get("link_api_provider") or LINK_API_PROVIDER)
    link_api_key = (body.get("link_api_key") or LINK_API_KEY or "").strip()
    link_api_url = _coalesce_setting(body.get("link_api_url"), LINK_API_URL)
    link_api_user_id = _coalesce_setting(body.get("link_api_user_id"), LINK_API_USER_ID)
    ephedrine_token = (body.get("ephedrine_api_token") or body.get("ephedrine_token") or EPHEDRINE_API_TOKEN or "").strip()
    ephedrine_base = _coalesce_setting(body.get("ephedrine_api_base"), EPHEDRINE_API_BASE)
    ephedrine_service_code = _coalesce_setting(body.get("ephedrine_service_code"), EPHEDRINE_SERVICE_CODE)
    ephedrine_version = _coalesce_setting(body.get("ephedrine_version"), EPHEDRINE_VERSION)
    ephedrine_domain_id = _parse_optional_positive_int(body.get("ephedrine_domain_id"))
    ephedrine_profile_id = _parse_optional_positive_int(body.get("ephedrine_profile_id"))
    if from_api and not api_key:
        return jsonify(ok=False, error="Нужен api-key (или OFFERUP_PARSER_API_KEY в app.py)"), 400
    if from_api and not link_credentials_configured(link_api_provider, link_api_key, ephedrine_token):
        if link_api_provider == "ephedrine":
            return jsonify(ok=False, error="Нужен токен Ephedrine (Bearer) или EPHEDRINE_API_TOKEN в app.py"), 400
        return jsonify(ok=False, error="Нужен ключ Kartatai (или LINK_API_KEY в app.py)"), 400

    filter_params = offerup_sanitize_parser_filters(body.get("parser_filters") or body.get("filters"))
    timing_raw = body.get("offerup_timing") or body.get("timing_offerup")
    chrome_package = offerup_sanitize_chrome_package(str(body.get("chrome_package") or ""))
    template_rules = offerup_sanitize_template_rules(body.get("template_rules") or [])
    manual_cat: Optional[int] = None
    raw_mc = body.get("manual_template_category_id")
    if raw_mc is not None and str(raw_mc).strip():
        try:
            v = int(raw_mc)
            if 1 <= v <= 12:
                manual_cat = v
        except (TypeError, ValueError):
            pass

    buyer_name_raw = (body.get("buyer_name") or "").strip()
    buyer_address_raw = (body.get("buyer_address") or "").strip()
    randomize_name = bool(body.get("randomize_name"))
    randomize_address = bool(body.get("randomize_address"))
    buyer_name = resolve_buyer_name(buyer_name_raw, randomize_name)
    buyer_address = resolve_buyer_address(buyer_address_raw, randomize_address)

    try:
        delay = float(body.get("delay_between_sec") or 2.0)
        delay = max(0.0, min(delay, 3600.0))
    except (TypeError, ValueError):
        delay = 2.0
    inbox_settings = offerup_sanitize_inbox_settings(body.get("inbox_settings") or body.get("offerup_inbox") or {})
    message_templates = _normalize_templates_from_body(body)

    local_llm_inbox_flag = bool(body.get("local_llm_inbox"))

    adb_in = (body.get("adb_port") or "").strip()
    if adb_in and adb_in != ADB_PORT:
        ADB_PORT = adb_in
        _ADB_RESOLVED = None

    force_stop_clear()
    with _autorun_lock:
        _autorun_state["running"] = True
        _autorun_state["stop_requested"] = False
        _autorun_state["count"] = 0
        _autorun_state["errors"] = 0
        _autorun_state["log"] = []
        _autorun_state["current_port"] = ""

    th = threading.Thread(
        target=_autorun_worker,
        args=(ports, ad_url, from_api, api_key, link_api_provider, link_api_key, link_api_url, link_api_user_id,
              ephedrine_token, ephedrine_base, ephedrine_service_code, ephedrine_version,
              ephedrine_domain_id, ephedrine_profile_id, filter_params,
              timing_raw, chrome_package, template_rules, manual_cat, delay,
              buyer_name, buyer_address, randomize_name, randomize_address, inbox_settings, message_templates,
              local_llm_inbox_flag),
        daemon=True,
    )
    th.start()
    return jsonify(ok=True, ports=ports)


@app.route("/api/offerup/autorun/stop", methods=["POST"])
def api_offerup_autorun_stop():
    body = request.get_json(force=True, silent=True) or {}
    force_stop_request(log_line="Авторежим: принудительная остановка…")
    ports: List[str] = []
    with _autorun_lock:
        cp = str(_autorun_state.get("current_port") or "").strip()
        if cp:
            ports.append(cp)
    pr = body.get("ports") or []
    if isinstance(pr, str):
        pr = [p.strip() for p in pr.split(",") if p.strip()]
    ports.extend(str(p).strip() for p in pr if str(p).strip())
    if not ports:
        sp = (body.get("adb_port") or ADB_PORT or "").strip()
        if sp:
            ports = [sp]
    force_stop_offerup_apps(ports or None)
    return jsonify(ok=True)


@app.route("/api/offerup/autorun/status", methods=["GET"])
def api_offerup_autorun_status():
    with _link_queue_lock:
        queue = list(_link_queue)
    with _offerup_alerts_lock:
        alerts = list(reversed(_offerup_alerts[-20:]))
    with _inbox_manual_lock:
        inbox_scan = {
            "running": bool(_inbox_manual_state.get("running")),
            "loop_mode": bool(_inbox_manual_state.get("loop_mode")),
            "stop_requested": bool(_inbox_manual_state.get("stop_requested")),
            "log": list(_inbox_manual_state.get("log") or []),
            "error": str(_inbox_manual_state.get("error") or ""),
        }
    with _autorun_lock:
        return jsonify(
            ok=True,
            running=_autorun_state.get("running", False),
            stop_requested=_autorun_state.get("stop_requested", False),
            count=_autorun_state.get("count", 0),
            errors=_autorun_state.get("errors", 0),
            current_port=_autorun_state.get("current_port", ""),
            log=list(_autorun_state.get("log") or []),
            link_queue=queue,
            alerts=alerts,
            coach_feed=coach_feed_for_poll(),
            coach_chat_inject=coach_chat_inject_for_poll(),
            pending_ai_actions=pending_ai_actions_for_poll(),
            inbox_scan=inbox_scan,
        )

@app.route("/api/state/reload", methods=["POST"])
def api_state_reload():
    """Перечитать mumu_state.json (если ссылки пропали в UI, а файл на диске есть)."""
    load_persistent_state()
    with _created_links_lock:
        n = len(_created_links)
    return jsonify(ok=True, created_links_count=n)


@app.route("/api/created-links", methods=["GET"])
def api_created_links():
    with _created_links_lock:
        return jsonify(ok=True, items=list(reversed(_created_links)))


@app.route("/api/created-links/clear", methods=["DELETE"])
def api_created_links_clear():
    with _created_links_lock:
        _created_links.clear()
    save_persistent_state()
    return jsonify(ok=True)


@app.route("/api/offerup/alerts/clear", methods=["DELETE"])
def api_offerup_alerts_clear():
    with _offerup_alerts_lock:
        _offerup_alerts.clear()
    save_persistent_state()
    return jsonify(ok=True)

@app.route("/api/send-history", methods=["GET"])
def api_send_history():
    with _send_history_lock:
        return jsonify(ok=True, items=list(reversed(_send_history)))


@app.route("/api/send-history/clear", methods=["DELETE"])
def api_send_history_clear():
    with _send_history_lock:
        _send_history.clear()
    save_persistent_state()
    return jsonify(ok=True)


_onboarding_lock = threading.Lock()
_onboarding_job: Dict[str, Any] = {
    "state": "idle",
    "log": [],
    "error": "",
    "result": None,
    "results": [],
    "queue_total": 0,
    "queue_done": 0,
}

_mumu_create_lock = threading.RLock()
_mumu_create_job: Dict[str, Any] = {
    "state": "idle",
    "log": [],
    "sessions": [],
    "total": 0,
    "done": 0,
    "error": "",
    "warning": "",
}


def _onboarding_log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    with _onboarding_lock:
        _onboarding_job.setdefault("log", []).append(line)
        if len(_onboarding_job["log"]) > 400:
            _onboarding_job["log"] = _onboarding_job["log"][-250:]


def _onboarding_should_stop() -> bool:
    with _onboarding_lock:
        return bool(_onboarding_job.get("stop_requested"))


def _onboarding_finish_stopped() -> None:
    with _onboarding_lock:
        _onboarding_job["state"] = "stopped"
        _onboarding_job.setdefault("log", []).append("Остановлено пользователем")


def _onboarding_worker(serial: str, cfg: dict) -> None:
    try:
        import mumu_onboarding as ob

        result = ob.run_onboarding_pipeline(
            serial, cfg, log=_onboarding_log, should_stop=_onboarding_should_stop,
        )
        if isinstance(result, dict) and result.get("activation_id"):
            set_offerup_email_account(
                serial,
                {
                    "email": result.get("email"),
                    "activation_id": result.get("activation_id"),
                    "token": cfg.get("anymessage_token"),
                    "verify_url": result.get("verify_url"),
                    "template_sends": 0,
                },
            )
        with _onboarding_lock:
            _onboarding_job["state"] = "done"
            _onboarding_job["result"] = result
        if isinstance(result, dict) and result.get("email_confirmed"):
            _onboarding_log("✓ Email confirmed — онбординг завершён для этой сессии")
    except RuntimeError as e:
        if "Остановлено" in str(e):
            _onboarding_finish_stopped()
            return
        with _onboarding_lock:
            _onboarding_job["state"] = "error"
            _onboarding_job["error"] = str(e)
            _onboarding_job.setdefault("log", []).append("Ошибка: " + str(e))
    except Exception as e:
        with _onboarding_lock:
            _onboarding_job["state"] = "error"
            _onboarding_job["error"] = str(e)
            _onboarding_job.setdefault("log", []).append("Ошибка: " + str(e))


def _onboarding_batch_worker(jobs: List[dict]) -> None:
    import mumu_onboarding as ob

    results: List[dict] = []
    try:
        for i, job in enumerate(jobs):
            if _onboarding_should_stop():
                _onboarding_finish_stopped()
                break
            serial = str(job.get("serial") or "").strip()
            cfg = job.get("cfg") or {}
            with _onboarding_lock:
                _onboarding_job["queue_done"] = i
            _onboarding_log(f"——— Сессия {i + 1}/{len(jobs)}: {serial} ———")
            try:
                if not probe_adb_reachable(serial):
                    raise RuntimeError(f"ADB недоступен: {serial}")
                result = ob.run_onboarding_pipeline(
                    serial, cfg, log=_onboarding_log, should_stop=_onboarding_should_stop,
                )
                if isinstance(result, dict) and result.get("activation_id"):
                    set_offerup_email_account(
                        serial,
                        {
                            "email": result.get("email"),
                            "activation_id": result.get("activation_id"),
                            "token": cfg.get("anymessage_token"),
                            "verify_url": result.get("verify_url"),
                            "template_sends": 0,
                        },
                    )
                results.append({"ok": True, "serial": serial, "result": result})
                if isinstance(result, dict) and result.get("email_confirmed"):
                    _onboarding_log("✓ Email confirmed — сессия завершена: " + serial)
            except RuntimeError as e:
                if "Остановлено" in str(e):
                    _onboarding_finish_stopped()
                    break
                results.append({"ok": False, "serial": serial, "error": str(e)})
                _onboarding_log(f"Ошибка сессии: {e}")
            except Exception as e:
                results.append({"ok": False, "serial": serial, "error": str(e)})
                _onboarding_log(f"Ошибка сессии: {e}")
        with _onboarding_lock:
            if _onboarding_job.get("state") == "running":
                _onboarding_job["state"] = "done"
            _onboarding_job["results"] = results
            _onboarding_job["queue_done"] = len(jobs)
            if results:
                _onboarding_job["result"] = results[-1].get("result")
    except Exception as e:
        with _onboarding_lock:
            _onboarding_job["state"] = "error"
            _onboarding_job["error"] = str(e)


@app.route("/api/onboarding/config", methods=["GET"])
def api_onboarding_config():
    import mumu_onboarding as ob

    acct = get_offerup_email_account(ADB_PORT)
    return jsonify(
        ok=True,
        proxy_apk_path=PROXY_APK_PATH,
        offerup_apks_path=OFFERUP_APKS_PATH,
        anymessage_token_configured=bool(ANYMESSAGE_TOKEN),
        anymessage_token=ANYMESSAGE_TOKEN or "",
        anymessage_token_default=ANYMESSAGE_TOKEN or "",
        anymessage_domain=ANYMESSAGE_DOMAIN,
        anymessage_site=ANYMESSAGE_SITE,
        zip_code=ONBOARDING_ZIP_CODE,
        signup_name=ONBOARDING_SIGNUP_NAME,
        default_proxy_host=DEFAULT_ONBOARDING_PROXY_HOST,
        proxy_port_presets=ONBOARDING_PROXY_PORTS,
        mumu_manager_found=bool(ob.find_mumu_manager_exe()),
        mumu_manager_path=ob.find_mumu_manager_exe(),
        email_account=acct,
    )


@app.route("/api/onboarding/start", methods=["POST"])
def api_onboarding_start():
    global ADB_PORT, _ADB_RESOLVED
    body = request.get_json(force=True, silent=True) or {}
    serial = (body.get("adb_port") or body.get("serial") or ADB_PORT or "").strip()
    if not serial:
        return jsonify(ok=False, error="Укажите adb_port / выберите сессию MuMu"), 400

    with _onboarding_lock:
        if _onboarding_job.get("state") == "running":
            return jsonify(ok=False, error="Онбординг уже выполняется"), 409
        _onboarding_job["state"] = "running"
        _onboarding_job["stop_requested"] = False
        _onboarding_job["log"] = []
        _onboarding_job["error"] = ""
        _onboarding_job["result"] = None
        _onboarding_job["results"] = []
        _onboarding_job["queue_total"] = 1
        _onboarding_job["queue_done"] = 0

    if serial != ADB_PORT:
        ADB_PORT = serial
        _ADB_RESOLVED = None

    cfg = _onboarding_cfg_from_body(body)
    if not cfg.get("skip_proxy_setup"):
        if not cfg["proxy_host"] or not cfg["proxy_port"]:
            return jsonify(ok=False, error="Укажите proxy_host и proxy_port"), 400
    if not cfg["anymessage_token"]:
        return jsonify(ok=False, error="Нужен токен AnyMessage (ANYMESSAGE_TOKEN)"), 400

    th = threading.Thread(target=_onboarding_worker, args=(serial, cfg), daemon=True)
    th.start()
    return jsonify(ok=True, serial=serial)


def _onboarding_cfg_from_body(body: dict) -> dict:
    skip_proxy = body.get("skip_proxy_setup") is True or body.get("proxy_already_configured") is True
    return {
        "proxy_apk": (body.get("proxy_apk") or PROXY_APK_PATH).strip(),
        "offerup_apks": (body.get("offerup_apks") or OFFERUP_APKS_PATH).strip(),
        "proxy_host": (body.get("proxy_host") or DEFAULT_ONBOARDING_PROXY_HOST).strip(),
        "proxy_port": str(body.get("proxy_port") or "").strip(),
        "zip_code": (body.get("zip_code") or ONBOARDING_ZIP_CODE).strip(),
        "signup_name": (body.get("signup_name") or ONBOARDING_SIGNUP_NAME).strip(),
        "buyer_name": str(body.get("buyer_name") or "").strip(),
        "use_buyer_name": body.get("use_buyer_name", True) is not False,
        "name_typo_enabled": bool(body.get("name_typo_enabled")),
        "signup_password": (body.get("signup_password") or "").strip(),
        "anymessage_token": (body.get("anymessage_token") or ANYMESSAGE_TOKEN).strip(),
        "anymessage_domain": (body.get("anymessage_domain") or ANYMESSAGE_DOMAIN).strip(),
        "anymessage_site": (body.get("anymessage_site") or ANYMESSAGE_SITE).strip(),
        "email_wait_sec": body.get("email_wait_sec") or 180,
        "email_poll_sec": body.get("email_poll_sec") or 10,
        "install_apks": body.get("install_apks", True) is not False,
        "skip_proxy_setup": skip_proxy,
        "proxy_already_configured": skip_proxy,
    }


@app.route("/api/onboarding/start-batch", methods=["POST"])
def api_onboarding_start_batch():
    global ADB_PORT, _ADB_RESOLVED
    body = request.get_json(force=True, silent=True) or {}
    sessions = body.get("sessions") or []
    if not isinstance(sessions, list) or not sessions:
        return jsonify(ok=False, error="Нужен массив sessions"), 400

    with _onboarding_lock:
        if _onboarding_job.get("state") == "running":
            return jsonify(ok=False, error="Онбординг уже выполняется"), 409
        _onboarding_job["state"] = "running"
        _onboarding_job["stop_requested"] = False
        _onboarding_job["log"] = []
        _onboarding_job["error"] = ""
        _onboarding_job["result"] = None
        _onboarding_job["results"] = []
        _onboarding_job["queue_total"] = len(sessions)
        _onboarding_job["queue_done"] = 0

    jobs: List[dict] = []
    for s in sessions:
        if not isinstance(s, dict):
            continue
        serial = str(s.get("adb_port") or s.get("serial") or "").strip()
        if not serial:
            continue
        cfg = _onboarding_cfg_from_body({**body, **s})
        if not cfg.get("skip_proxy_setup") and not cfg["proxy_port"]:
            return jsonify(ok=False, error=f"Нет proxy_port для {serial}"), 400
        jobs.append({"serial": serial, "cfg": cfg})
    if not jobs:
        return jsonify(ok=False, error="Нет валидных sessions (adb_port)"), 400
    if not jobs[0]["cfg"].get("anymessage_token"):
        return jsonify(ok=False, error="Нужен токен AnyMessage"), 400

    th = threading.Thread(target=_onboarding_batch_worker, args=(jobs,), daemon=True)
    th.start()
    return jsonify(ok=True, count=len(jobs))


@app.route("/api/onboarding/stop", methods=["POST"])
def api_onboarding_stop():
    body = request.get_json(force=True, silent=True) or {}
    force_stop_request()
    with _onboarding_lock:
        st = _onboarding_job.get("state")
        if st in ("running", "stopping"):
            _onboarding_job["state"] = "stopping"
            _onboarding_job.setdefault("log", []).append(
                time.strftime("%H:%M:%S ") + "Принудительная остановка…"
            )
    ports: List[str] = []
    sp = (body.get("adb_port") or ADB_PORT or "").strip()
    if sp:
        ports = [sp]
    force_stop_offerup_apps(ports or None)
    return jsonify(ok=True)


@app.route("/api/onboarding/status", methods=["GET"])
def api_onboarding_status():
    with _onboarding_lock:
        return jsonify(
            ok=True,
            state=_onboarding_job.get("state", "idle"),
            stop_requested=bool(_onboarding_job.get("stop_requested")),
            log=list(_onboarding_job.get("log") or []),
            error=str(_onboarding_job.get("error") or ""),
            result=_onboarding_job.get("result"),
            results=list(_onboarding_job.get("results") or []),
            queue_total=int(_onboarding_job.get("queue_total") or 0),
            queue_done=int(_onboarding_job.get("queue_done") or 0),
            operator_pause=operator_pause_snapshot(),
            operator_pause_on_fail=bool(OPERATOR_PAUSE_ON_FAIL),
            operator_pause_llm_dump=bool(OPERATOR_PAUSE_LLM_DUMP),
        )


@app.route("/api/operator/continue", methods=["POST"])
def api_operator_continue():
    if not operator_continue():
        return jsonify(ok=False, error="Нет активной паузы"), 409
    return jsonify(ok=True)


@app.route("/api/operator/dumps", methods=["GET"])
def api_operator_dumps():
    with _operator_pause_lock:
        dumps = list(_operator_ai_dumps[-40:])
    return jsonify(ok=True, dumps=dumps, pause=operator_pause_snapshot())


def _mumu_create_log(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + (msg or "")
    with _mumu_create_lock:
        _mumu_create_job.setdefault("log", []).append(line)
        if len(_mumu_create_job["log"]) > 200:
            _mumu_create_job["log"] = _mumu_create_job["log"][-120:]


def _mumu_create_prepare_params(body: dict) -> Tuple[Optional[dict], Optional[str]]:
    proxy_ports = body.get("proxy_ports") or []
    if not isinstance(proxy_ports, list) or not proxy_ports:
        return None, "Выберите порты прокси (proxy_ports)"
    try:
        ports = [int(p) for p in proxy_ports]
    except (TypeError, ValueError):
        return None, "Некорректные proxy_ports"
    proxy_host = (body.get("proxy_host") or DEFAULT_ONBOARDING_PROXY_HOST).strip()
    names_in = body.get("names") or []
    names: List[str] = []
    if isinstance(names_in, list):
        names = [str(x or "").strip() for x in names_in]
    base_name = str(body.get("signup_name") or body.get("buyer_name") or "").strip()
    use_buyer = body.get("use_buyer_name", True) is not False
    if use_buyer and not base_name:
        base_name = str(body.get("buyer_name") or ONBOARDING_SIGNUP_NAME).strip()
    typo = bool(body.get("name_typo_enabled"))
    import mumu_onboarding as ob

    if typo or use_buyer:
        filled = []
        for i in range(len(ports)):
            nm = names[i] if i < len(names) and names[i] else base_name
            if typo or (use_buyer and nm):
                cfg = {
                    "signup_name": nm or base_name,
                    "buyer_name": base_name,
                    "use_buyer_name": use_buyer,
                    "name_typo_enabled": typo,
                }
                nm = ob.resolve_signup_display_name(cfg)
            filled.append(nm)
        names = filled
    elif not names and base_name:
        names = [base_name] * len(ports)
    return {
        "ports": ports,
        "proxy_host": proxy_host,
        "names": names,
        "launch": body.get("launch", True) is not False,
        "vertical": body.get("vertical", True) is not False,
    }, None


def _mumu_create_batch_worker(params: dict) -> None:
    import mumu_onboarding as ob

    ports = params["ports"]
    try:

        def _progress(done: int, total: int, _row: dict, sessions: List[dict]) -> None:
            with _mumu_create_lock:
                _mumu_create_job["done"] = int(done)
                _mumu_create_job["total"] = int(total)
                _mumu_create_job["sessions"] = list(sessions)

        sessions = ob.mumu_create_and_prepare_sessions(
            ports,
            proxy_host=params["proxy_host"],
            signup_names=params.get("names") or None,
            launch=bool(params.get("launch")),
            vertical=bool(params.get("vertical")),
            log=_mumu_create_log,
            progress_cb=_progress,
        )
        missing = [s for s in sessions if not s.get("adb_port")]
        with _mumu_create_lock:
            _mumu_create_job["sessions"] = list(sessions)
            _mumu_create_job["done"] = len(ports)
            _mumu_create_job["total"] = len(ports)
            _mumu_create_job["state"] = "done"
            _mumu_create_job["warning"] = (
                "Не все ADB порты найдены — укажите вручную в таблице или «Найти порты»"
                if missing
                else ""
            )
    except Exception as e:
        with _mumu_create_lock:
            _mumu_create_job["state"] = "error"
            _mumu_create_job["error"] = str(e)
            _mumu_create_log("Ошибка: " + str(e))


@app.route("/api/mumu/create-batch", methods=["POST"])
def api_mumu_create_batch():
    body = request.get_json(force=True, silent=True) or {}
    params, err = _mumu_create_prepare_params(body)
    if err:
        return jsonify(ok=False, error=err), 400
    assert params is not None
    with _mumu_create_lock:
        if _mumu_create_job.get("state") == "running":
            return jsonify(
                ok=True,
                started=False,
                already_running=True,
                done=int(_mumu_create_job.get("done") or 0),
                total=int(_mumu_create_job.get("total") or 0),
            )
        _mumu_create_job.clear()
        _mumu_create_job.update(
            {
                "state": "running",
                "log": [],
                "sessions": [],
                "total": len(params["ports"]),
                "done": 0,
                "error": "",
                "warning": "",
            }
        )
    threading.Thread(target=_mumu_create_batch_worker, args=(params,), daemon=True).start()
    return jsonify(ok=True, started=True, total=len(params["ports"]))


@app.route("/api/mumu/create-batch/status", methods=["GET"])
def api_mumu_create_batch_status():
    with _mumu_create_lock:
        return jsonify(
            ok=True,
            state=str(_mumu_create_job.get("state") or "idle"),
            log=list(_mumu_create_job.get("log") or []),
            sessions=list(_mumu_create_job.get("sessions") or []),
            done=int(_mumu_create_job.get("done") or 0),
            total=int(_mumu_create_job.get("total") or 0),
            error=str(_mumu_create_job.get("error") or ""),
            warning=str(_mumu_create_job.get("warning") or ""),
        )


@app.route("/api/mumu/create", methods=["POST"])
def api_mumu_create():
    body = request.get_json(force=True, silent=True) or {}
    try:
        count = int(body.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, 10))
    start_idx = body.get("start_vmindex")
    try:
        start_idx = int(start_idx) if start_idx is not None else None
    except (TypeError, ValueError):
        start_idx = None
    try:
        import mumu_onboarding as ob

        info = ob.mumu_create_instances(count=count, start_index=start_idx)
        indices = info.get("indices") or []
        launched = []
        for vm in indices:
            try:
                vm_i = int(vm)
                time.sleep(1.5)
                adb = ob.mumu_launch_wait_adb(vm_i, vertical=True, dismiss_store=True)
                launched.append({
                    "vmindex": vm_i,
                    "mumu_name": ob.mumu_session_label(vm_i),
                    "adb_port": adb,
                })
            except Exception:
                launched.append({"vmindex": vm, "mumu_name": f"Android device-{vm}", "adb_port": ""})
        return jsonify(
            ok=True,
            created=indices,
            launched=launched,
            command=info.get("command"),
            output=info.get("output"),
            hint="Эмуляторы запущены; ADB подтянется в таблице сессий",
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.route("/api/onboarding/discovered-sessions", methods=["GET"])
def api_onboarding_discovered_sessions():
    """Уже запущенные MuMu-сессии (adb) для онбординга."""
    items = discover_mumu_ports(probe=True)
    live = [x for x in items if x.get("reachable") is True]
    out = []
    for it in live:
        sess = str(it.get("session") or "")
        vmindex = None
        m = re.search(r"device[- ]?(\d+)", sess, re.I)
        if m:
            try:
                vmindex = int(m.group(1))
            except ValueError:
                pass
        out.append(
            {
                "adb_port": it.get("address"),
                "mumu_name": sess or it.get("address"),
                "vmindex": vmindex,
            }
        )
    return jsonify(ok=True, sessions=out)


def _anymessage_token_from_body(body: dict) -> str:
    return str(body.get("token") or body.get("anymessage_token") or ANYMESSAGE_TOKEN or "").strip()


@app.route("/api/anymessage/accounts", methods=["GET"])
def api_anymessage_accounts():
    with _email_accounts_lock:
        rows = [
            {"serial": k, **v}
            for k, v in _email_accounts.items()
            if isinstance(v, dict)
        ]
    return jsonify(
        ok=True,
        accounts=rows,
        mailbox=_mailbox_items_for_ui(),
        token_configured=bool(ANYMESSAGE_TOKEN),
        site=ANYMESSAGE_SITE,
        domain=ANYMESSAGE_DOMAIN,
    )


@app.route("/api/anymessage/order", methods=["POST"])
def api_anymessage_order():
    body = request.get_json(force=True, silent=True) or {}
    token = _anymessage_token_from_body(body)
    if not token:
        return jsonify(ok=False, error="Нужен токен AnyMessage"), 400
    import mumu_onboarding as ob

    site = str(body.get("site") or ANYMESSAGE_SITE).strip()
    domain = str(body.get("domain") or ANYMESSAGE_DOMAIN).strip()
    serial = str(body.get("adb_port") or body.get("serial") or "").strip()
    try:
        aid, email = ob.anymessage_order_email(token, site=site, domain=domain)
        if serial:
            set_offerup_email_account(
                serial,
                {"activation_id": aid, "email": email, "token": token},
            )
        _upsert_mail_history(
            activation_id=aid,
            email=email,
            site=site,
            serial=serial,
            status="waiting",
            last_json={"status": "success", "id": aid, "email": email},
        )
        save_persistent_state()
        return jsonify(ok=True, id=aid, email=email, raw_status="success")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.route("/api/anymessage/reorder", methods=["POST"])
def api_anymessage_reorder():
    body = request.get_json(force=True, silent=True) or {}
    token = _anymessage_token_from_body(body)
    if not token:
        return jsonify(ok=False, error="Нужен токен AnyMessage"), 400
    import mumu_onboarding as ob

    activation_id = str(body.get("id") or body.get("activation_id") or "").strip()
    email = str(body.get("email") or "").strip()
    serial = str(body.get("adb_port") or body.get("serial") or "").strip()
    if not activation_id and not email and serial:
        acct = get_offerup_email_account(serial) or {}
        activation_id = str(acct.get("activation_id") or "")
        email = str(acct.get("email") or "")
    if not activation_id and not email:
        return jsonify(ok=False, error="Нужен id или email"), 400
    site = str(body.get("site") or ANYMESSAGE_SITE).strip()
    try:
        new_id, new_email = ob.anymessage_reorder_email(
            token, activation_id or "0", site=site, email=email,
        )
        if serial:
            set_offerup_email_account(
                serial,
                {"activation_id": new_id, "email": new_email, "token": token},
            )
        _upsert_mail_history(
            activation_id=new_id,
            email=new_email,
            site=site,
            serial=serial,
            status="waiting",
            last_json={"status": "success", "id": new_id, "email": new_email},
        )
        save_persistent_state()
        return jsonify(ok=True, id=new_id, email=new_email)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.route("/api/anymessage/getmessage", methods=["POST"])
def api_anymessage_getmessage():
    body = request.get_json(force=True, silent=True) or {}
    token = _anymessage_token_from_body(body)
    activation_id = str(body.get("id") or body.get("activation_id") or "").strip()
    if not token or not activation_id:
        return jsonify(ok=False, error="Нужны token и id"), 400
    import mumu_onboarding as ob

    try:
        data = ob.anymessage_getmessage_once(
            token,
            activation_id,
            preview_html=bool(body.get("preview")),
        )
        link = ob.extract_offerup_link_from_message(ob._message_blob_from_api(data))
        code = ob.extract_verification_code_from_message(ob._message_blob_from_api(data))
        serial = str(body.get("adb_port") or body.get("serial") or "").strip()
        site = str(body.get("site") or ANYMESSAGE_SITE).strip()
        st = "received" if (link or code) else "waiting"
        if isinstance(data, dict) and str(data.get("status") or "").lower() not in ("success", ""):
            st = str(data.get("status") or st)
        _upsert_mail_history(
            activation_id=activation_id,
            email=str(body.get("email") or "").strip(),
            site=site,
            serial=serial,
            cost=_extract_mail_cost(data),
            status=st,
            code=code or "",
            link=link or "",
            last_json=data if isinstance(data, dict) else {},
        )
        save_persistent_state()
        return jsonify(ok=True, data=data, link=link or "", code=code or "")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.route("/api/anymessage/proxy", methods=["POST"])
def api_anymessage_proxy():
    """Произвольный GET: path=/email/..., params={...}."""
    body = request.get_json(force=True, silent=True) or {}
    token = _anymessage_token_from_body(body)
    path = str(body.get("path") or "").strip()
    if not token or not path:
        return jsonify(ok=False, error="Нужны token и path"), 400
    params = dict(body.get("params") or {})
    params["token"] = token
    import mumu_onboarding as ob

    try:
        data = ob.anymessage_api_request(path, {k: str(v) for k, v in params.items() if v is not None})
        return jsonify(ok=True, data=data)
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.route("/api/onboarding/reorder-email", methods=["POST"])
def api_onboarding_reorder_email():
    """Перезаказ той же почты AnyMessage (reorder) для offerup.com."""
    global ADB_PORT, _ADB_RESOLVED
    body = request.get_json(force=True, silent=True) or {}
    serial = (body.get("adb_port") or body.get("serial") or ADB_PORT or "").strip()
    if not serial:
        return jsonify(ok=False, error="Укажите adb_port"), 400
    token = (body.get("anymessage_token") or ANYMESSAGE_TOKEN or "").strip()
    if not token:
        return jsonify(ok=False, error="Нужен токен AnyMessage"), 400

    acct = get_offerup_email_account(serial) or {}
    activation_id = str(body.get("activation_id") or acct.get("activation_id") or "").strip()
    email = str(body.get("email") or acct.get("email") or "").strip()
    if not activation_id and not email:
        return jsonify(ok=False, error="Нет activation_id / email (сначала онбординг или order)"), 400

    if serial != ADB_PORT:
        ADB_PORT = serial
        _ADB_RESOLVED = None

    try:
        import mumu_onboarding as ob

        new_id, new_email = ob.anymessage_reorder_email(
            token, activation_id or "0", email=email,
        )
        set_offerup_email_account(
            serial,
            {
                "activation_id": new_id,
                "email": new_email,
                "token": token,
            },
        )
        return jsonify(
            ok=True,
            serial=serial,
            activation_id=new_id,
            email=new_email,
            site=ANYMESSAGE_SITE,
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 400


@app.route("/api/send-history/<int:item_id>/resend", methods=["POST"])
def api_send_history_resend(item_id: int):
    """??????? fish_link ?? ??????? ??? ????????? ?????? ???????? ?? ??????? x.gd -> MuMu."""
    with _send_history_lock:
        row = next((x for x in _send_history if x.get("id") == item_id), None)
    if not row:
        return jsonify(ok=False, error="?????? ?? ???????"), 404
    return jsonify(ok=True, row=row, fish_link=row.get("fish_link") or row.get("url") or "")


load_mumu_ui_config_from_disk()
try:
    merge_ai_training_file_into_memory()
except Exception:
    pass
try:
    if GITHUB_TRAINING_SYNC_ENABLED and (GITHUB_TRAINING_REPO or "").strip():
        github_sync_training_rules(force=True)
    start_github_training_sync_background()
except Exception:
    pass


if __name__ == "__main__":
    print("=" * 52)
    print("  MumuPaster запущен")
    print("  Открой в браузере: http://127.0.0.1:5000")
    print("=" * 52)
    adb = get_adb()
    if adb:
        print(f"  adb найден: {adb}")
        adb_connect()
    else:
        print("  [!] adb не найден — проверь ADB_PATH в app.py")
    app.run(debug=False, port=5000, threaded=True)
