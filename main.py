import sys
import traceback

log = open("jarvis_error.log", "w", encoding="utf-8")
sys.stdout = log
sys.stderr = log
import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import re
import threading
import time
import json
from datetime import datetime
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen, WATCH_FRAME_INTERVAL
from actions.youtube_video     import youtube_video

# Autonomous Self Engineering
from self_engineer.autonomous import (
    TriggerDetector,
    AutonomousOrchestrator,
)
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_graph        import code_graph
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.research_mode     import research_mode
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from actions.web_search        import _startup_news_india as _fetch_news_sync
from memory.config_manager     import (
    get_brief_enabled, get_audio_device, save_audio_device,
)
from core                      import audio_devices
from core                      import wake_word
from core.api_errors           import is_invalid_api_key_error, is_project_access_blocked_error
from self_engineer.orchestrator import SelfEngineer
from core.plugin_loader        import discover_plugins


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

# ── Rest-mode phrase detection ────────────────────────────────────────────────
# The wake word ("jarvis") is caught locally and offline — see core/wake_word.py.
# Going back TO rest doesn't need a second local detector: once JARVIS is awake,
# mic audio is already flowing to Gemini as normal, so this just pattern-matches
# Gemini's own live transcription of what you said. Deliberately loose (needs
# "jarvis" + any rest-ish word, in any order) so it catches "Jarvis, take some
# rest", "Jarvis go to rest mode", "okay Jarvis, that'll be all", etc. without
# needing an exact phrase.
_JARVIS_NAME_RE = re.compile(r"\bjarvis\b", re.IGNORECASE)
_REST_WORD_RE   = re.compile(
    r"\b(rest|sleep|stand\s*down|that('|\u2019)?ll\s+be\s+all|go\s+to\s+rest\s+mode)\b",
    re.IGNORECASE,
)


def _is_rest_command(text: str) -> bool:
    return bool(text) and bool(_JARVIS_NAME_RE.search(text)) and bool(_REST_WORD_RE.search(text))

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "research_mode",
        "description": (
            "Manages research sessions using the JARVIS Research Workspace. "
            "Use action='start' with query/depth to begin research, 'cancel' to stop, "
            "'status' to check a session, 'history' to list past sessions, 'summary' to read the executive summary, "
            "'export' to save session state, 'open_paper' or 'open_image' to inspect one result, "
            "'list_sources' to read out every source's title and URL for the session (use this whenever the "
            "user asks to see/read/list the sources or references, including when the AI-written summary was "
            "unavailable — the source list still exists even then), 'open_sources' to actually open several "
            "source URLs in the browser at once (use this for 'open all the sources'/'open them all' requests; "
            "pass max_open to control how many, default 5), "
            "'save_session' to persist the current session, 'restore_session' to resume one, "
            "'enter_mode'/'exit_mode' to control research mode state."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | cancel | status | history | summary | export | open_paper | open_image | list_sources | open_sources | save_session | restore_session | enter_mode | exit_mode"},
                "query": {"type": "STRING", "description": "Research topic or query"},
                "depth": {"type": "STRING", "description": "quick | standard | deep"},
                "session_id": {"type": "STRING", "description": "Research session id"},
                "paper_id": {"type": "STRING", "description": "Paper id to open"},
                "image_id": {"type": "STRING", "description": "Image id to open"},
                "path": {"type": "STRING", "description": "Export file path"},
                "n": {"type": "NUMBER", "description": "Number of history entries to list"},
                "max_open": {"type": "NUMBER", "description": "For action='open_sources': max number of source URLs to open at once (default 5)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "ONE-SHOT screen or webcam analysis — capture a single frame, analyze it, then stop. "
            "Use when the user says: read my screen, analyze my screen, see my screen, look at my screen, "
            "what is on screen, look at camera, etc. "
            "Do NOT use for continuous watching — use screen_watch with action='start' instead. "
            "You have NO visual ability without this tool or screen_watch. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "screen_watch",
        "description": (
            "Continuous live screen watching (like screen sharing) — frames stream in RAM, never saved to disk. "
            "action='start': user says watch my screen, read my screen continuously, start screen watch, "
            "keep watching my screen, see my screen live. "
            "action='stop': user says stop watching, stop screen, stop reading screen, stop screen watch."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | stop"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. "
            "IMPORTANT for closing apps: when action is 'close_app' and the user named a specific "
            "app (e.g. 'close chrome', 'quit discord'), you MUST put that app's name in the 'value' "
            "field — this closes the real running app by process, not just the focused window. "
            "Only omit 'value' for a bare 'close this window' with no app named. "
            "Use action 'is_running' with the app name in 'value' to check whether an app is "
            "currently open. Use action 'check_permissions' (no value needed) to diagnose why "
            "system controls (volume, brightness, WiFi, dark mode, minimize/maximize) might be "
            "failing — it reports missing OS permissions/admin rights with the exact fix."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "Must be one of these EXACT strings (do not invent variants like "
                        "'minimize_window' or 'volume' — use the exact spelling below): "
                        "volume_up | volume_down | volume_set | mute | unmute | toggle_mute | "
                        "brightness_up | brightness_down | sleep_display | pause_video | "
                        "close_window | close_app | full_screen | minimize | maximize | "
                        "snap_left | snap_right | switch_window | show_desktop | task_manager | "
                        "focus_search | refresh_page | close_tab | new_tab | next_tab | prev_tab | "
                        "go_back | go_forward | zoom_in | zoom_out | zoom_reset | find_on_page | "
                        "scroll_up | scroll_down | scroll_top | scroll_bottom | page_up | page_down | "
                        "copy | paste | cut | undo | redo | select_all | save | enter | escape | "
                        "screenshot | lock_screen | open_settings | file_explorer | open_run | "
                        "dark_mode | toggle_wifi | restart | shutdown | type_text | press_key | "
                        "reload_n | is_running | check_permissions. "
                        "For volume_set, put the 0-100 number in 'value'. "
                        "If truly unsure which action fits, leave 'action' empty and put the "
                        "user's request in 'description' instead — a fallback detector will map it."
                    ),
                },
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
    "Controls any web browser. Use for opening websites, searching the web, "
    "clicking elements, filling forms, scrolling, screenshots, navigation, and any web-based task. "

    "IMPORTANT BROWSER RULES: "
    "Use Google Chrome as the default browser for all websites and searches. "
    "Use Brave Browser ONLY when the request is related to YouTube, such as playing videos, opening YouTube, searching YouTube, or watching YouTube content. "

    "If the user explicitly asks for a specific browser (for example Edge, Firefox, Brave, Chrome, Opera, etc.), always respect the user's choice and set the browser parameter accordingly. "

    "Always include the 'browser' parameter whenever opening or controlling a browser. "

    "Do NOT call this tool as a fallback right after browser_agent reports it couldn't finish, was blocked, or needs the user — browser_control opens a real, visible browser window, which defeats the whole point of using the headless browser_agent. In that case just relay browser_agent's message to the user instead."
),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
    "type": "STRING",
    "description": "open | list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"
},
                "path": {
    "type": "STRING",
    "description": "Absolute folder or file path (example: D:\\Games, E:\\Projects, C:\\Users\\Aditya\\Downloads) or shortcuts like desktop, downloads, documents, home"
},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": (
            "Controls the Windows/macOS/Linux virtual desktop and the Desktop "
            "folder's appearance: wallpaper, organize, clean, list, stats, "
            "show_desktop (Win+D — minimizes all windows, touches no files), "
            "open_desktop (opens the Desktop folder in the file manager). "
            "Do NOT use this to move/copy a file onto the Desktop — that is "
            "file_controller's 'move' or 'copy' action with destination='desktop'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | show_desktop | open_desktop | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "self_engineer",
        "description": (
            "JARVIS's supervised self-improvement system. Analyzes JARVIS's own source code for "
            "weaknesses, proposes fixes, tests and benchmarks them in an isolated sandbox, and "
            "reports the results. NOTHING is ever changed on disk without the owner explicitly "
            "approving a specific patch ID after reviewing its report. Use 'analyze' to scan for "
            "weaknesses, 'weaknesses' to list findings, 'propose' to generate and test a patch for "
            "one weakness, 'report' to show a patch's full report, 'approve'/'reject' to decide on "
            "a proposed patch, and 'history' to show past changes. NEVER call 'approve' unless the "
            "user has explicitly said to apply/approve that specific patch."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "analyze | weaknesses | propose | report | approve | reject | history"
                },
                "weakness_id": {"type": "STRING", "description": "Weakness ID for 'propose' (from 'weaknesses' output)"},
                "patch_id":    {"type": "STRING", "description": "Patch ID for 'report' / 'approve' / 'reject'"},
                "category":    {"type": "STRING", "description": "Optional filter for 'weaknesses', e.g. unused_import, large_function, bare_except"},
                "reason":      {"type": "STRING", "description": "Optional reason text for 'reject'"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_graph",
        "description": (
            "Codebase Knowledge Engine — a read-only map of how JARVIS's own source files "
            "depend on each other. Use 'stats' for an overview, 'dependencies' to see what a "
            "file imports, 'dependents' to see what imports a file, 'impact' to see the full "
            "blast radius of changing a file (every module affected directly or indirectly), "
            "'symbol' to find which file defines a function/class by name, 'symbols' to list "
            "everything a file defines, 'cycles' to check for circular imports, and 'build' to "
            "force a rebuild after files changed. Never modifies any file — use this before "
            "proposing or reasoning about a code change, to know what else it could break."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "build | stats | dependencies | dependents | impact | symbol | symbols | cycles"
                },
                "module": {"type": "STRING", "description": "File path or module name, e.g. 'actions/file_controller.py' or 'self_engineer.security'"},
                "symbol_name": {"type": "STRING", "description": "Function or class name, for the 'symbol' action"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact OR ongoing discussion topic to long-term "
            "memory, so it survives restarts and reconnects — not just this conversation. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, future "
            "plans, OR a substantial topic/task you're currently helping with (a bug "
            "you're debugging together, a trip being planned, homework being worked "
            "through). Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "conversation — an ongoing topic/task in progress right now (a bug "
                        "being debugged, a trip being planned, a subject being studied) — "
                        "use this so the next conversation can pick the thread back up; "
                        "update the SAME key again as the topic progresses instead of "
                        "creating near-duplicate keys | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name, jarvis_volume_bug)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister, 'fixed the action-name mismatch, verifying with user')"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

# --- Plugin system ---


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self._asst_name     = "JARVIS"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._screen_watch_active  = False   # continuous screen-share mode
        self._screen_watch_task: asyncio.Task | None = None
        self._interrupted          = False   # True while draining audio after user interrupt

        # ── Wake word / rest mode ────────────────────────────────────────
        # Starts awake (matches the old always-on behaviour) so a first-time
        # user isn't confused by silence. Say "Jarvis, take some rest" /
        # "go to rest mode" to put it to sleep; say "Jarvis" (or "Jarvis
        # wake up") to bring it back. While resting, mic audio is NOT sent
        # to Gemini at all — only the local wake-word detector below sees it.
        self._awake            = True
        self._wake_detector    = wake_word.WakeWordDetector(BASE_DIR)
        if not self._wake_detector.available:
            print(f"[JARVIS] ⚠️ {self._wake_detector.reason}")
            # Logged to the on-screen activity panel too, once, after the UI
            # callbacks below are wired up (see the end of __init__).
            self._wake_word_warning = self._wake_detector.reason
        else:
            self._wake_word_warning = None
        self._resumption_handle: str | None = None  # Live API session-resumption handle —
                                                      # MUST survive reconnects; this is what
                                                      # lets a fresh WebSocket resume the SAME
                                                      # server-side conversation instead of
                                                      # starting a blank one. Never reset this
                                                      # in the per-reconnect transient-state block.
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_stop_remote    = self._stop_remote_access
        self.ui.on_status_query   = self._remote_status
        self.ui.on_interrupt      = self.interrupt
        self.ui.on_plugin_list       = self._plugin_list_for_ui
        self.ui.on_plugin_toggle     = self._toggle_plugin
        self.ui.on_news_topics_list  = self._news_topics_for_ui
        self.ui.on_news_topic_add    = self._add_news_topic_ui
        self.ui.on_news_topic_remove = self._remove_news_topic_ui
        self.ui.on_audio_devices     = self._audio_devices_for_ui
        self.ui.on_audio_device_save = self._save_audio_device
        # Give background workers (plugins running on their own threads —
        # pomodoro, shopping_agent, research_mode, etc.) a way to make JARVIS
        # actually speak once they finish. Those workers only ever receive
        # `player=self.ui` (the JarvisUI facade), which has no speak() of its
        # own — without this, `player.speak(...)` silently no-ops and the
        # only trace of completion is a line in the activity log.
        self.ui.speak = self.speak
        audio_devices.prefetch()
        self._turn_done_event: asyncio.Event | None = None
        self._pending_tool_tasks: set = set()   # in-flight background tool-call tasks
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._self_engineer    = SelfEngineer(project_root=BASE_DIR)
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        
        # Autonomous Self Engineering
        self._trigger_detector = TriggerDetector(use_ai=True)
        self._autonomous_orchestrator = AutonomousOrchestrator(
            project_root=BASE_DIR,
            dashboard_callback=self._broadcast_engineering_status if self._dashboard else None
        )
        self._engineering_in_progress = False

        # Drop-in plugins (plugins/*.py) — discovered once here and cached for
        # the process lifetime. Their tool declarations are merged into the
        # Gemini tool list in _build_config(), and unmatched tool calls fall
        # through to self.plugins.run(...) in _execute_tool(). Enable/disable
        # state is re-read from config on every call, so toggling a plugin in
        # the Plugin Manager overlay doesn't require a restart.
        self._core_tool_names = {t["name"] for t in TOOL_DECLARATIONS}
        self.plugins = discover_plugins(
            BASE_DIR / "plugins", self._core_tool_names, logger=self._log_plugin,
        )

        if self._wake_word_warning:
            self.ui.write_log(f"SYS: {self._wake_word_warning}")

    def _log_plugin(self, message: str) -> None:
        print(f"[Plugins] {message}")
        self.ui.write_log(f"SYS: {message}")

    def _plugin_list_for_ui(self) -> list[dict]:
        """Called by the Plugin Manager overlay to populate its list."""
        return self.plugins.list_for_ui()

    def _toggle_plugin(self, name: str, enabled: bool) -> None:
        """Called by the Plugin Manager overlay when a checkbox is flipped.
        No restart needed — plugin_loader re-reads this on every run() call."""
        from memory.config_manager import save_plugin_enabled
        save_plugin_enabled(name, enabled)

    def _news_topics_for_ui(self) -> list[dict]:
        """Called by the News Topics overlay to populate its list."""
        from plugins.news_provider import list_topics
        return list_topics()

    def _add_news_topic_ui(self, topic: str) -> None:
        """Called by the News Topics overlay's ADD button."""
        from plugins.news_provider import add_topic
        add_topic(topic)

    def _remove_news_topic_ui(self, topic: str) -> None:
        """Called by the News Topics overlay's ✕ button on a row."""
        from plugins.news_provider import remove_topic
        remove_topic(topic)

    def _audio_devices_for_ui(self):
        """Called by the Audio Devices overlay to populate/refresh its dropdowns.
        Returns (inputs, outputs, current_input, current_output)."""
        inputs = audio_devices.list_devices("input", refresh=True)
        outputs = audio_devices.list_devices("output", refresh=True)
        return inputs, outputs, get_audio_device("input"), get_audio_device("output")

    def _save_audio_device(self, input_name: str, output_name: str) -> None:
        """Called by the Audio Devices overlay's Apply button. Takes effect on
        the next mic/speaker stream open — currently only at process start."""
        save_audio_device("input", input_name)
        save_audio_device("output", output_name)

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        print("=" * 60)
        print("REMOTE GENERATED")
        print("URL    :", url)
        print("KEY    :", key)
        print("AUTO   :", f"{url}/auto-login?key={key}")
        print("MANUAL :", manual)
        print("=" * 60)
        return url, key, f"{url}/auto-login?key={key}", manual

    def _stop_remote_access(self) -> None:
        """Called from the Qt thread when the user presses STOP REMOTE ACCESS."""
        if self._dashboard is None or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._dashboard.async_stop_remote_access(), self._loop
        )
        self.ui.write_log("SYS: Remote access stopped — all devices disconnected.")

    def _remote_status(self) -> dict | None:
        """Called from the Qt thread (via the QR overlay's 1s poll timer) — cheap, synchronous."""
        if self._dashboard is None:
            return None
        try:
            return self._dashboard.get_status()
        except Exception:
            return None

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING" if self._awake else "RESTING")

    def _on_wake_detected(self) -> None:
        """Runs on the asyncio loop (scheduled from the mic callback thread).
        Flips JARVIS awake and gives a short spoken acknowledgement so the
        person knows it heard them, instead of just silently starting to
        listen properly."""
        self.ui.write_log("SYS: Wake word heard — listening.")
        self.ui.set_state("LISTENING")
        if self.session:
            asyncio.ensure_future(self._send_wake_greeting())

    async def _send_wake_greeting(self) -> None:
        try:
            await self.session.send_client_content(
                turns={"parts": [{
                    "text": (
                        "(System note: the user just said your wake word. "
                        "Acknowledge in ONE short sentence, e.g. 'Yes?' or "
                        "'I'm listening.' Nothing else.)"
                    )
                }]},
                turn_complete=True,
            )
        except Exception as e:
            print(f"[JARVIS] Wake greeting failed: {e}")

    def _go_to_rest(self) -> None:
        self._awake = False
        self.ui.write_log("SYS: Going to rest — say \"Jarvis\" to wake me up.")
        self.ui.set_state("RESTING")

    def interrupt(self) -> None:
        """Stop JARVIS mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[JARVIS] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def _stop_screen_watch(self, *, log: bool = True) -> None:
        """Stop continuous screen watch and cancel the background frame task."""
        self._screen_watch_active = False
        task = self._screen_watch_task
        self._screen_watch_task = None
        if task and not task.done():
            task.cancel()
        if log:
            self.ui.write_log("SYS: Screen watch stopped.")
            print("[Vision] ⏹ Screen watch stopped")

    async def _start_screen_watch(self) -> str:
        if self._screen_watch_active:
            return "Screen watch is already running."
        if not self.session:
            return "Cannot start screen watch — session not ready."

        self._screen_watch_active = True
        self.ui.write_log("SYS: Screen watch started.")
        print("[Vision] ▶ Screen watch started")

        try:
            await self.session.send_client_content(
                turns={"parts": [{"text": (
                    "[SCREEN_WATCH] Live screen sharing is now active. "
                    "You will receive periodic screen frames in real time (in memory only). "
                    "Describe what you see, notice changes, and answer the user's questions about their screen. "
                    "Stay concise unless asked for detail."
                )}]},
                turn_complete=True,
            )
        except Exception as e:
            self._stop_screen_watch(log=False)
            return f"Could not start screen watch: {e}"

        self._screen_watch_task = asyncio.create_task(self._screen_watch_loop())
        return "Live screen watch started. Frames are streaming now."

    async def _screen_watch_loop(self) -> None:
        """Capture screen frames in RAM and push them into the main Gemini Live session."""
        loop = asyncio.get_event_loop()
        try:
            while self._screen_watch_active:
                if not self.session:
                    await asyncio.sleep(0.25)
                    continue
                if self._vision_busy:
                    await asyncio.sleep(0.1)
                    continue
                try:
                    img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                    await self.session.send_realtime_input(
                        media={"data": img_b, "mime_type": mime_t}
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[Vision] ⚠️ Watch frame error: {e}")
                await asyncio.sleep(WATCH_FRAME_INTERVAL)
        except asyncio.CancelledError:
            pass
        finally:
            self._screen_watch_active = False
            self._screen_watch_task = None

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "JARVIS").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            self._asst_name = "JARVIS"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        _addr = (f"ADDRESS: Always call the user '{_user_name}'."
                 if _user_name
                 else "ADDRESS: When speaking Turkish → always say \"efendim\". "
                      "When speaking English → say \"sir\". Never mix languages.")
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        parts = [time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS + self.plugins.get_tool_declarations()}],
            # Pass back whatever handle we last captured from the server (None on
            # the very first connect of the process). Without this, every
            # reconnect — including the ones DevAgent's long tool calls can
            # trigger — opens a brand-new, contextless conversation even though
            # session_resumption is "enabled".
            session_resumption=types.SessionResumptionConfig(handle=self._resumption_handle),
            # Without this, a long-running conversation silently drops its
            # earliest turns once the Live session's context window fills up
            # — this is the "forgets what we were just talking about" bug.
            # With it, the server compresses/summarizes older turns instead
            # of discarding them once the turn history passes trigger_tokens,
            # shrinking it back down to target_tokens. This is separate from
            # (and in addition to) session_resumption above, which only
            # protects against *reconnects* losing context, not a single
            # long session slowly running out of room.
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=32000,
                sliding_window=types.SlidingWindow(target_tokens=16000),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
            # Fixes the single biggest cause of "JARVIS isn't responding":
            # with the SDK's defaults, the server's voice-activity detector
            # sometimes waits too long to decide you've stopped talking (or
            # doesn't trigger at all on a quiet mic / soft voice), so the
            # turn never completes and JARVIS just... doesn't answer. Turning
            # sensitivity up and shortening the silence window makes it both
            # start and end turns much more reliably.
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                    prefix_padding_ms=20,
                    silence_duration_ms=500,
                ),
            ),
        )

    def _run_self_engineer(self, args: dict) -> str:
        """
        Synchronous dispatch for the self_engineer tool (runs in an executor —
        analysis/sandboxing/git are all blocking I/O and CPU work). Every
        action here either reads/reports state, or requires the action to be
        explicitly 'approve'/'reject' with a patch_id the owner has already
        seen a report for — nothing here writes to the real project without
        that explicit step.
        """
        eng = self._self_engineer
        action = (args.get("action") or "").strip().lower()

        if action == "analyze":
            weaknesses = eng.analyze()
            by_cat: dict[str, int] = {}
            for w in weaknesses:
                by_cat[w.category] = by_cat.get(w.category, 0) + 1
            summary = ", ".join(f"{cat}: {n}" for cat, n in sorted(by_cat.items()))
            return f"Analyzed source tree — {len(weaknesses)} weaknesses found. By category: {summary or 'none'}."

        if action == "weaknesses":
            category = args.get("category") or None
            weaknesses = eng.list_weaknesses(category=category)
            if not weaknesses:
                return "No weaknesses on file for that filter. Run 'analyze' first."
            lines = [
                f"{w.id} [{w.severity.value}] {w.category} — {w.file}:{w.line_start} — {w.description}"
                for w in weaknesses[:25]
            ]
            more = f" (+{len(weaknesses) - 25} more)" if len(weaknesses) > 25 else ""
            return "\n".join(lines) + more

        if action == "propose":
            weakness_id = args.get("weakness_id", "")
            if not weakness_id:
                return "Missing weakness_id. Call 'weaknesses' first to get an ID."
            try:
                patch = eng.propose(weakness_id)
            except ValueError as e:
                return str(e)
            return (
                f"Patch {patch.id} generated — status: {patch.status.value}.\n"
                f"{eng.get_report(patch.id)}"
            )

        if action == "report":
            patch_id = args.get("patch_id", "")
            if not patch_id:
                return "Missing patch_id."
            return eng.get_report(patch_id)

        if action == "approve":
            patch_id = args.get("patch_id", "")
            if not patch_id:
                return "Missing patch_id."
            return eng.approve(patch_id)

        if action == "reject":
            patch_id = args.get("patch_id", "")
            if not patch_id:
                return "Missing patch_id."
            return eng.reject(patch_id, args.get("reason", ""))

        if action == "history":
            entries = eng.history()
            if not entries:
                return "No self-engineering history yet."
            return "\n".join(f"{e['ts']} — {e['patch_id']}: {e['note']} ({e['summary']})" for e in entries)

        return f"Unknown self_engineer action: '{action}'. Use analyze | weaknesses | propose | report | approve | reject | history."
    
    def _broadcast_engineering_status(self, status: dict) -> None:
        """Broadcast engineering status to dashboard."""
        if self._dashboard:
            # Store status for dashboard API endpoint
            self._dashboard._engineering_status = status
            # Broadcast via WebSocket
            asyncio.run_coroutine_threadsafe(
                self._dashboard.broadcast({"type": "engineering", "data": status}),
                self._loop
            )
    
    async def _run_autonomous_self_engineer(self, request: str, context: dict) -> str:
        """
        Run the autonomous self-engineering pipeline.
        
        This is triggered when a self-improvement request is detected.
        """
        if self._engineering_in_progress:
            return "Self-engineering is already in progress. Please wait for the current operation to complete."
        
        self._engineering_in_progress = True
        self.ui.write_log("SYS: Autonomous self-engineering started...")
        
        try:
            result = await self._autonomous_orchestrator.process_request(request, context)
            
            if result["success"]:
                self.ui.write_log(f"SYS: Self-engineering completed successfully. Modified {len(result.get('modified_files', []))} files.")
                return f"Self-improvement completed successfully. {len(result.get('modified_files', []))} files were modified."
            else:
                self.ui.write_log(f"SYS: Self-engineering failed: {result.get('error', 'Unknown error')}")
                return f"Self-improvement could not be completed: {result.get('error', 'Unknown error')}"
                
        except Exception as e:
            self.ui.write_log(f"SYS: Self-engineering error: {e}")
            return f"Self-improvement encountered an error: {e}"
            
        finally:
            self._engineering_in_progress = False

    async def _run_tool_calls(self, function_calls, owning_session) -> None:
        """
        Executes one batch of tool calls (as received in a single
        response.tool_call message) and sends the responses back — without
        blocking the _receive_audio pump loop that called us.

        `owning_session` is the exact session object that was active when
        this tool call arrived. If a reconnect happens while a long tool
        (e.g. dev_agent) is still running, self.session will have moved on
        to a new connection by the time we finish — sending a response tied
        to the old call id on the new session is invalid, so we just drop it
        in that case. The tool's real-world side effects (files written,
        project built, etc.) already happened and are unaffected; only the
        stale round-trip reply to the model is skipped. The task/project
        state itself lives on disk (memory/, desktop project folders), not
        in this reply, so nothing about "did the project finish" is lost —
        the model will pick the finished project back up via the resumed
        conversation and/or a follow-up status question.
        """
        fn_responses = []
        for fc in function_calls:
            print(f"[JARVIS] 📞 {fc.name}")
            fr = await self._execute_tool(fc)
            fn_responses.append(fr)

        if self.session is not owning_session:
            print("[JARVIS] ⚠️ Session changed while tool call was running — "
                  "dropping stale tool response (tool side effects already happened).")
            return

        try:
            await owning_session.send_tool_response(function_responses=fn_responses)
        except Exception as e:
            # The connection may have died the instant the tool finished —
            # this is exactly the race that used to surface as "SYS: JARVIS
            # online" right after DevAgent completed. It's now harmless: the
            # outer reconnect loop will bring the session back up using the
            # cached resumption handle, preserving conversation context.
            print(f"[JARVIS] ⚠️ Could not deliver tool response (session likely closing): {e}")

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        # Check for autonomous self-engineering trigger
        # Detect if this tool call indicates a self-improvement request
        trigger_type, trigger_confidence = self._trigger_detector.detect_from_tool_call(name, args)
        
        # Also check if args contain self-improvement language
        args_text = " ".join(str(v) for v in args.values())
        text_trigger, text_confidence = self._trigger_detector.detect_from_text(args_text)
        
        # Use the higher confidence detection
        if text_confidence > trigger_confidence:
            trigger_type = text_trigger
            trigger_confidence = text_confidence
        
        # If self-improvement intent detected with sufficient confidence
        if trigger_type is not None and trigger_confidence >= 0.50:
            print(f"[JARVIS] 🤖 Self-improvement trigger detected: {trigger_type.value} (confidence: {trigger_confidence:.2f})")
            
            # Build context for the orchestrator
            context = {
                "tool_name": name,
                "tool_args": args,
                "trigger_type": trigger_type.value,
            }
            
            # Run autonomous pipeline
            result = await self._run_autonomous_self_engineer(args_text, context)
            
            # Return the result
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": result}
            )

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_graph":
                r = await loop.run_in_executor(None, lambda: code_graph(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in the user's own language, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "screen_watch":
                action = (args.get("action") or "").strip().lower()
                if action == "start":
                    result = await self._start_screen_watch()
                elif action == "stop":
                    self._stop_screen_watch()
                    result = "Screen watch stopped."
                else:
                    result = f"Unknown screen_watch action: '{action}'. Use start or stop."

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)

            elif name == "research_mode":
                r = await loop.run_in_executor(None, lambda: research_mode(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "self_engineer":
                result = await loop.run_in_executor(None, lambda: self._run_self_engineer(args))

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            elif self.plugins.has(name):
                r = await loop.run_in_executor(
                    None, lambda: self.plugins.run(name, args, player=self.ui, session_memory=None)
                )
                result = r or "Done."

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            # Local wake-word check happens BEFORE anything is sent anywhere —
            # while resting, no audio leaves this machine. Only runs while
            # asleep; once awake there's nothing left for it to detect.
            if not self._awake and self._wake_detector.available:
                if self._wake_detector.feed(indata[:, 0]):
                    self._awake = True
                    loop.call_soon_threadsafe(self._on_wake_detected)

            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if (
                not jarvis_speaking
                and not self.ui.muted
                and not self._phone_active
                and self._awake
            ):
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        # Mic device open is retried in place instead of letting a transient
        # failure (device busy, unplugged, driver hiccup) tear down the whole
        # Gemini Live session and force a full reconnect — that reconnect is
        # itself a common cause of "JARVIS stopped responding" reports, since
        # it drops the in-flight turn and takes several seconds to recover.
        _attempt = 0
        while True:
            try:
                _in_device = audio_devices.resolve(get_audio_device("input"), "input")
                with sd.InputStream(
                    samplerate=SEND_SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    blocksize=CHUNK_SIZE,
                    device=_in_device,
                    callback=callback,
                ):
                    print("[JARVIS] 🎤 Mic stream open")
                    _attempt = 0
                    while True:
                        await asyncio.sleep(0.1)
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:
                _attempt += 1
                delay = min(2 * _attempt, 10)
                print(f"[JARVIS] ❌ Mic error ({e}) — retrying in {delay}s (attempt {_attempt})")
                if _attempt == 1:
                    self.ui.write_log(f"ERR: Microphone problem ({e}) — retrying...")
                elif _attempt == 5:
                    self.ui.write_log(
                        "ERR: Microphone still failing after several attempts — "
                        "check it's plugged in and not in use by another app, "
                        "or pick a different one in Audio Devices settings."
                    )
                await asyncio.sleep(delay)

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    # Server periodically hands us a fresh resumption handle.
                    # Cache it — this is what makes the NEXT reconnect (if any)
                    # resume this same conversation instead of a blank one.
                    if response.session_resumption_update:
                        sru = response.session_resumption_update
                        if sru.resumable and sru.new_handle:
                            self._resumption_handle = sru.new_handle

                    # Server warns us the connection is about to be closed
                    # (e.g. hitting the max session duration while DevAgent is
                    # still running a long build/fix loop). We can't stop that,
                    # but we log it — the outer reconnect loop will pick the
                    # cached handle above back up automatically.
                    if response.go_away:
                        print(f"[JARVIS] ⚠️ Server GoAway — time_left={response.go_away.time_left}")

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            # Rest-mode trigger. Only honoured if the local wake
                            # detector is actually available — without it there
                            # would be no way to wake JARVIS back up afterwards.
                            if (
                                full_in
                                and self._awake
                                and self._wake_detector.available
                                and _is_rest_command(full_in)
                            ):
                                self._go_to_rest()

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "jarvis",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until JARVIS finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        # Run tool execution in the background instead of
                        # `await`-ing it inline here. Long-running tools (e.g.
                        # dev_agent, which can chain several minutes of LLM
                        # planning + subprocess builds/tests) used to block
                        # THIS coroutine — the one reading `session.receive()`.
                        # While blocked, we stopped observing session_resumption_update
                        # / go_away messages from the server for the whole
                        # duration, so if the connection died while we were
                        # stuck awaiting DevAgent, we found out only after it
                        # finished, with a stale handle. Dispatching as a task
                        # keeps this loop pumping the whole time.
                        current_session = self.session
                        tg_task = asyncio.ensure_future(
                            self._run_tool_calls(response.tool_call.function_calls, current_session)
                        )
                        self._pending_tool_tasks.add(tg_task)
                        tg_task.add_done_callback(self._pending_tool_tasks.discard)
        except Exception as e:
            print(f"[JARVIS] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        _out_device = audio_devices.resolve(get_audio_device("output"), "output")
        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
            device=_out_device,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync)

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user, mention it is {time_str}, and say you are fetching today's news now. "
            f"One short sentence only. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = f" Respond in {lang}." if lang else ""

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # If turn_complete didn't fire (timeout), give a small buffer
                if not turn_waited:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=4.0)
                except Exception:
                    news_text = ""

                if not self.session:
                    return

                if news_text and len(news_text) > 60:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — today in India", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"parts": [{"text": p2}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing phase 2 failed: {e}")

        asyncio.create_task(_deliver_news())

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory = await asyncio.to_thread(load_memory)
                prompt = self._proactive.build_prompt(memory)
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted and self._awake:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    def _on_phone_disconnected(self) -> None:
        self.ui.write_log("SYS: Phone disconnected from Remote Dashboard.")

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer

            print("=" * 60)
            print("Creating Dashboard...")
            print("=" * 60)

            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_disconnect_callback(self._on_phone_disconnected)

            asyncio.create_task(self._dashboard.serve())

            print("=" * 60)
            print("Dashboard Task Created")
            print("=" * 60)

            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())

        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            try:
                print("[JARVIS] Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._stop_screen_watch(log=False)
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print("[JARVIS] Connected.")
                    self.ui.set_state("LISTENING" if self._awake else "RESTING")
                    # Only announce a fresh boot on the very first connect of the
                    # process. Every later (re)connect that carries a resumption
                    # handle is continuing the SAME conversation — surfacing
                    # "JARVIS online" there is exactly the misleading, memory-
                    # wiping-looking message this fix removes.
                    if self._resumption_handle:
                        self.ui.write_log("SYS: JARVIS reconnected — conversation resumed.")
                    else:
                        self.ui.write_log("SYS: JARVIS online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_proactive_mode())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled)
                    if not self._briefing_sent and get_brief_enabled():
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                err_str = str(e)
                print(f"[JARVIS] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Invalid API key — stop hammering the API, prompt re-configuration
                if is_invalid_api_key_error(err_str):
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] New API key saved — reconnecting...")
                    self._conn_backoff = 3
                    continue

                # Gemini project access is blocked by Google / project policy.
                # This is not a transient network issue, and retrying forever keeps
                # the assistant stuck in 'sleeping' mode.
                if is_project_access_blocked_error(err_str):
                    self.ui.write_log(
                        "ERR: Gemini project access is blocked — JARVIS cannot connect until "
                        "the Google project is enabled or the key/project is corrected."
                    )
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[JARVIS] Project access issue cleared — reconnecting...")
                    self._conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[JARVIS] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def main():
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc(file=log)
        log.flush()
        raise