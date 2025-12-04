# jarvis_mistral_refactor.py
"""
Refactored JARVIS assistant (Mistral + Edge TTS + CustomTkinter)
Improvements made:
- Robust continuous speech recognition with proper stop_listening handle
- Stop button stops TTS playback, clears TTS queue, stops Ollama streaming (uses stop_flag)
- Safer non-blocking TTS playback using sounddevice with checks
- Safer threading and asyncio usage for tts worker
- Better memory save logic and smaller safety improvements
- Clear app exit handling to stop background threads and listeners

Note: This file assumes you have the same dependencies already installed.
"""

import customtkinter as ctk
import threading
import ollama
import json
import os
import queue
import re
import asyncio
import edge_tts
import tempfile
import sounddevice as sd
import soundfile as sf
import psutil
import webbrowser
import subprocess
import time
import socket
import speech_recognition as sr
from datetime import datetime
import requests
import serial
import pyautogui
import pygetwindow as gw  # pip install pygetwindow
import screen_brightness_control as sbc  # pip install screen-brightness-control
import sys

# -----------------------------
# Config / Globals
# -----------------------------
MEMORY_FILE = "memory.json"
PERSONAL_INFO = {"name": "Suraj"}
stop_flag = threading.Event()  # global stop flag checked across tasks
pending_close_window = None
pending_system_action = None
current_file = None
memory_lock = threading.Lock()

# For speech recognition background handle
sr_recognizer = sr.Recognizer()
sr_microphone = None
sr_stop_listening = None

# TTS queue and playback control
tts_queue = queue.Queue()
_current_playback_lock = threading.Lock()
current_playback = None

# Keep the tts worker loop's event loop reference so we can safely cancel if needed
_tts_loop = None

# -----------------------------
# Utilities
# -----------------------------

def is_port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except Exception:
        return False


def start_ollama_mistral(timeout_seconds=30):
    if not is_port_open("127.0.0.1", 11434):
        try:
            subprocess.Popen([
                "ollama", "run", "mistral"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(" Starting Ollama Mistral...")
        except Exception as e:
            print(" Failed to start Ollama:", e)
            return False

    waited = 0
    while waited < timeout_seconds:
        if is_port_open("127.0.0.1", 11434):
            print(" Ollama Mistral is ready!")
            return True
        time.sleep(1)
        waited += 1
    print(" Ollama Mistral did not start in time.")
    return False


# -----------------------------
# Persistent Memory
# -----------------------------

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                messages = json.load(f)
            return messages
        except Exception as e:
            print(" Could not load memory:", e)
    return [{"role": "system", "content": "You are a concise, helpful local assistant. Answer concisely and to the point."}]


def save_memory(messages):
    try:
        system_messages = [m for m in messages if m.get("role") == "system"]
        history = [m for m in messages if m.get("role") != "system"][-20:]
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(system_messages + history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(" Could not save memory:", e)


memory = load_memory()
if not any(m.get("role") == "system" for m in memory):
    memory.insert(0, {
        "role": "system",
        "content": (
            f"You are JARVIS, a helpful and intelligent AI assistant for {PERSONAL_INFO['name']}."
            "Reply naturally, concisely, and directly."
        )
    })


# -----------------------------
# ESP32 Setup
# -----------------------------
try:
    esp = serial.Serial('COM10', 115200, timeout=1)  # Use the correct COM port here
    time.sleep(2)
    print("ESP32 connected")
except Exception as e:
    esp = None
    print("ESP32 not connected:", e)
 
def send_esp32_command(cmd):
    if esp and getattr(esp, 'is_open', False):
        try:
            esp.write((cmd + "\n").encode())  # must end with newline
            esp.flush()                        # ensure it sends immediately
            print(f"Sent to ESP32: {cmd}")
        except Exception as e:
            print("ESP32 write failed:", e)


# -----------------------------
# TTS: robust non-blocking playback
# -----------------------------

async def speak_text(text):
    """Convert text to speech using edge-tts, save to a temporary wav and play non-blocking.
    Will check stop_flag frequently so Stop works promptly.
    """
    global current_playback
    if stop_flag.is_set():
        return
    if not text or not text.strip():
        return

    # pad to avoid cut-off
    padded = "\u200b" + text.strip()
    communicate = edge_tts.Communicate(padded, voice="en-GB-RyanNeural", rate="-5%")

    # write to temp file (edge-tts handles streaming; save is easiest & reliable)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp_path = tmp.name
    try:
        await communicate.save(tmp_path)

        if stop_flag.is_set():
            try:
                os.remove(tmp_path)
            except:
                pass
            return

        # read and play
        data, sr_rate = sf.read(tmp_path, dtype='float32')
        try:
            # play non-blocking
            with _current_playback_lock:
                sd.play(data, samplerate=sr_rate, blocking=False)
                # poll until playback ends or stop requested
                while True:
                    stream = None
                    try:
                        stream = sd.get_stream()
                    except Exception:
                        stream = None
                    active = getattr(stream, 'active', None)
                    if stop_flag.is_set():
                        try:
                            sd.stop()
                        except Exception:
                            pass
                        break
                    # if we can check active and it's False -> finished
                    if active is not None:
                        if not active:
                            break
                    # fallback: try to wait a tiny bit and allow sd to finish
                    await asyncio.sleep(0.05)
        except Exception as e:
            print(" Playback error:", e)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    except Exception as e:
        print(" TTS generation error:", e)
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def tts_worker():
    """Worker that runs an asyncio loop to process TTS tasks sequentially."""
    global _tts_loop
    loop = asyncio.new_event_loop()
    _tts_loop = loop
    asyncio.set_event_loop(loop)
    while True:
        try:
            text = tts_queue.get()
            if text is None:
                tts_queue.task_done()
                break
            if stop_flag.is_set():
                # drop remaining
                tts_queue.task_done()
                continue
            loop.run_until_complete(speak_text(text))
            tts_queue.task_done()
        except Exception as e:
            print(" TTS worker exception:", e)


def queue_tts(text):
    if text and isinstance(text, str) and text.strip():
        tts_queue.put(text.strip())


# start TTS worker thread
threading.Thread(target=tts_worker, daemon=True).start()


# -----------------------------
# Local Helpers
# -----------------------------

def read_file_content(filename):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f" Error reading file: {e}"


def write_file_content(filename, new_content):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return f" File '{filename}' updated successfully."
    except Exception as e:
        return f" Error writing file: {e}"


# -----------------------------
# WhatsApp Desktop helper 
# -----------------------------

def send_whatsapp_running(name, message):
    try:
        wa_windows = [w for w in gw.getAllWindows() if "WhatsApp" in w.title]
        if not wa_windows:
            return " WhatsApp is not running."
        wa_window = wa_windows[0]
        try:
            if wa_window.isMinimized:
                wa_window.restore()
                time.sleep(0.3)
            wa_window.activate()
        except Exception:
            pass
        time.sleep(0.25)
        pyautogui.hotkey('ctrl', 'f')
        time.sleep(0.15)
        pyautogui.write(name, interval=0.05)
        time.sleep(0.4)
        pyautogui.press('down')
        pyautogui.press('enter')
        time.sleep(0.25)
        pyautogui.write(message, interval=0.03)
        pyautogui.press('enter')
        return f"Message sent to {name}."
    except Exception as e:
        return f" Error sending message: {e}"


# -----------------------------
# Local commands dictionary
# -----------------------------
local_commands = {
    "open notepad": lambda: os.system("start notepad"),
    "open calculator": lambda: os.system("start calc"),
    "open cmd": lambda: os.system("start cmd"),
    "open chrome": lambda: os.system("start chrome"),
    "open vs code": lambda: os.system("start code"),
    "open youtube": lambda: webbrowser.open("https://www.youtube.com"),
    "play rickrolled": lambda: webbrowser.open("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"),
    "shutdown": lambda: os.system("shutdown /s /t 5"),
    "restart": lambda: os.system("shutdown /r /t 5"),
    "log off": lambda: os.system("shutdown /l"),
    "create file": lambda filename="newfile.txt": open(filename, "w").close(),
    "delete file": lambda filename: os.remove(filename),
    "read file": lambda filename="main.py": read_file_content(filename),
    "update file": lambda filename, content="": write_file_content(filename, content),
    "system info": lambda: f"CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%, Disk: {psutil.disk_usage('/').percent}%"
}


# -----------------------------
# Window helpers
# -----------------------------

def find_window_by_name(name):
    if not name:
        return None
    name_lower = name.lower()
    for w in gw.getAllWindows():
        try:
            if name_lower in w.title.lower():
                return w
        except Exception:
            continue
    return None


def safe_activate_window(window):
    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.15)
        window.activate()
        return True
    except Exception:
        try:
            window.restore()
            window.activate()
            return True
        except Exception:
            return False


# -----------------------------
# Enhanced command handler
# -----------------------------
pending_close_window = None
pending_system_action = None


def handle_local_command(command):
    global pending_close_window, pending_system_action, current_file
    command = (command or "").strip()
    command_lower = command.lower()

    # confirmation flows (keep same logic)
    if pending_close_window:
        if command_lower in ("yes jarvis", "y", "yeah", "yep", "sure", "confirm", "ok"):
            w = pending_close_window
            pending_close_window = None
            try:
                w.close()
                return f" Closed: {w.title}"
            except Exception as e:
                return f" Could not close window: {e}"
        elif command_lower in ("no", "n", "nah", "cancel", "stop"):
            title = pending_close_window.title if pending_close_window else "window"
            pending_close_window = None
            return f" Cancelled closing {title}."
        else:
            return " Please answer 'yes' or 'no'."

    if pending_system_action:
        if command_lower in ("yes jarvis", "y", "yeah", "sure", "ok", "confirm"):
            action = pending_system_action
            pending_system_action = None
            if action == "shutdown":
                os.system("shutdown /s /t 5")
                return " Shutting down in 5 seconds..."
            elif action == "restart":
                os.system("shutdown /r /t 5")
                return " Restarting in 5 seconds..."
            elif action == "log off":
                os.system("shutdown /l")
                return " Logging off..."
        elif command_lower in ("no", "n", "nah", "cancel"):
            action = pending_system_action
            pending_system_action = None
            return f" Cancelled {action}."
        else:
            return " Please answer 'yes' or 'no'."

    # time / date
    if "time" in command_lower:
        return f"The current time is {datetime.now().strftime('%H:%M:%S')}."
    if "date" in command_lower:
        return f"Today's date is {datetime.now().strftime('%A, %d %B %Y')}."

    # shutdown/restart confirmation
    if "shutdown" in command_lower:
        pending_system_action = "shutdown"
        return "Are you sure you want to shut down? (yes/no)"
    if "restart" in command_lower:
        pending_system_action = "restart"
        return "Are you sure you want to restart? (yes/no)"
    if "log off" in command_lower:
        pending_system_action = "log off"
        return "Are you sure you want to log off? (yes/no)"

         
    # ------------------------
    # LOCK PC
    # ------------------------
    if "lock my pc" in command_lower or "lock laptop" in command_lower:
        try:
            os.system("rundll32.exe user32.dll,LockWorkStation")
            return " Locking your PC."
        except Exception as e:
            return f" Could not lock PC: {e}"

    # ------------------------
    # UNLOCK / LOGIN PC via ESP32 HID
    # ------------------------
    if (
        "unlock my pc" in command_lower
        or "unlock laptop" in command_lower
        or "unlock my laptop" in command_lower
        or "login my pc" in command_lower
        or "login my laptop" in command_lower
        or "log in my pc" in command_lower
        or "log in my laptop" in command_lower
    ):
        try:
            if esp and getattr(esp, 'is_open', False):
                send_esp32_command("UNLOCK_PC")  # Send HID unlock command
                queue_tts("Unlocking your PC")
                print("Unlocking your PC via ESP32 HID")
                return "Trying to unlock your PC now..."
            else:
                return "ESP32 not connected. Please plug the ESP32 USB into this laptop."
        except Exception as e:
            return f"Could not unlock PC: {e}"

    # -----------------------------
    # Handle ESP32 Light Commands
    # -----------------------------
    if 'light' in command_lower or 'lights' in command_lower:
        try:
            if 'on' in command_lower:
                send_esp32_command('LIGHT_ON')
                return 'Light turned on'
            elif 'off' in command_lower:
                send_esp32_command('LIGHT_OFF')
                return 'Light turned off'
        except Exception as e:
            return f"Could not control lights: {e}"



    # create file
    if "create a file" in command_lower:
        try:
            m = re.search(r"create a file(?: called| named)?\s*(.*)", command_lower)
            if m:
                filename = m.group(1).strip().replace(' ', '_')
                if not os.path.splitext(filename)[1]:
                    if 'python' in filename:
                        filename += '.py'
                    elif 'javascript' in filename or 'js' in filename:
                        filename += '.js'
                    elif 'html' in filename:
                        filename += '.html'
                    elif 'css' in filename:
                        filename += '.css'
                    else:
                        filename += '.txt'
                file_path = os.path.join(os.getcwd(), filename)
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write('')
                current_file = file_path
                try:
                    subprocess.Popen(['code', file_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    try:
                        os.startfile(file_path)
                    except Exception:
                        pass
                return f" Created and opened file: {filename}"
            else:
                return " Please say the file name after 'create a file'."
        except Exception as e:
            return f" Failed to create file: {e}"

    # smart write/add to file using Ollama streaming
    if (command_lower.startswith('write ') or command_lower.startswith('add ')) and current_file:
        try:
            text_to_write = re.sub(r'^(write|add)\s*', '', command, flags=re.IGNORECASE)
            ext = os.path.splitext(current_file)[1].lower()
            lang_map = {'.py':'Python', '.js':'JavaScript', '.html':'HTML', '.css':'CSS', '.txt':'text'}
            lang = lang_map.get(ext, 'text')
            with open(current_file, 'a', encoding='utf-8') as f:
                # stream and write
                for chunk in ollama.chat(model='mistral', messages=[{'role':'user','content':f'Write {lang} content for: {text_to_write}'}], stream=True):
                    if stop_flag.is_set():
                        break
                    part = chunk.get('message', {}).get('content', '')
                    if part:
                        f.write(part)
                        f.flush()
            return f" Finished writing AI-generated {lang} to {os.path.basename(current_file)}."
        except Exception as e:
            return f" Could not write with AI: {e}"
    elif (command_lower.startswith('write ') or command_lower.startswith('add ')) and not current_file:
        return " No file currently open. Please create a file first."

    # window close
    if re.match(r'^(close|quit|exit)( window)?', command_lower):
        target = re.search(r'(?:close|quit|exit)(?: window)?(?:\s+(.*))?', command_lower)
        target_name = target.group(1).strip() if target and target.group(1) else None
        w = find_window_by_name(target_name) if target_name else gw.getActiveWindow()
        if not w:
            return f" No window found matching '{target_name or 'active window'}'."
        pending_close_window = w
        return f"Are you sure you want to close '{w.title}'? (yes/no)"

    # MINIMIZE (supports minimize / minimise)
    if command_lower.startswith(("minimise", "minimise window")):
        m = re.search(r"(?:minimize|minimise)(?: window)?(?:\s+(.*))?", command_lower)
        target = m.group(1).strip() if m and m.group(1) else None

        w = find_window_by_name(target) if target else gw.getActiveWindow()
        if w:
            try:
                import win32gui, win32con
                hwnd = win32gui.FindWindow(None, w.title)
                if hwnd:
                    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                    return f" Minimized "
                return f" Could not find window handle for '{w.title}'."
            except Exception as e:
                return f" Could not minimize: {e}"

        return f" No window found matching '{target}'."

    # MAXIMIZE (supports maximize / maximise)
    if command_lower.startswith(("maximize", "maximise")):
        m = re.search(r"(?:maximize|maximise)(?: window)?(?:\s+(.*))?", command_lower)
        target = m.group(1).strip() if m and m.group(1) else None

        w = find_window_by_name(target) if target else gw.getActiveWindow()
        if w:
            try:
                w.maximize()
                return f" Maximized "
            except Exception as e:
                return f" Could not maximize: {e}"

        return f" No window found matching '{target}'."

    # RESTORE / UNMINIMIZE (THIS WAS BROKEN BEFORE, NOW FIXED)
    if command_lower.startswith(("restore", "unminimize", "unminimise")):
        m = re.search(r"(?:restore|unminimize|unminimise)(?: window)?(?:\s+(.*))?", command_lower)
        target = m.group(1).strip() if m and m.group(1) else None

        w = find_window_by_name(target) if target else gw.getActiveWindow()

        if w:
            ok = safe_activate_window(w)
            return f" Restored " if ok else f" Could not Restored "

        return f" No window found matching '{target}'."

    # SWITCH TO WINDOW
    if command_lower.startswith("switch to "):
        target = command_lower.replace("switch to ", "").strip()

        if not target:
            return " Use: switch to <window name>"

        w = find_window_by_name(target)
        if w:
            ok = safe_activate_window(w)
            return f" Switched to: {w.title}" if ok else f" Could not activate {w.title}"

        return f" No window found matching '{target}'."

 
    # tab controls
    if command_lower in ("new tab", "open new tab", "open tab"):
        pyautogui.hotkey('ctrl', 't')
        return "Opened new tab."
    if command_lower in ("close tab", "close current tab"):
        pyautogui.hotkey('ctrl', 'w')
        return "Closed current tab."
    if command_lower in ("next tab", "switch tab", "switch to next tab"):
        pyautogui.hotkey('ctrl', 'tab')
        return "Switched to next tab."
    if command_lower in ("previous tab", "prev tab", "switch to previous tab"):
        pyautogui.hotkey('ctrl', 'shift', 'tab')
        return "Switched to previous tab."

    # volume
    if 'volume' in command_lower:
        if 'up' in command_lower or 'increase' in command_lower:
            pyautogui.press('volumeup'); return 'Volume increased.'
        if 'down' in command_lower or 'decrease' in command_lower:
            pyautogui.press('volumedown'); return 'Volume decreased.'
        if 'mute' in command_lower or 'off' in command_lower:
            pyautogui.press('volumemute'); return 'Volume muted.'
        if 'unmute' in command_lower or 'on' in command_lower:
            pyautogui.press('volumemute'); return 'Volume unmuted.'

    # brightness
    if 'brightness' in command_lower:
        try:
            m = re.search(r"(\d{1,3})\s*%", command_lower)
            if m:
                val = int(m.group(1)); val = max(0, min(100, val)); sbc.set_brightness(val); return f'Brightness set to {val}%.'
            try:
                current = sbc.get_brightness(display=0)[0]
            except Exception:
                current = None
            if 'up' in command_lower or 'increase' in command_lower:
                if current is None: return ' Could not read brightness to increase.'
                new = min(100, current + 10); sbc.set_brightness(new); return f'Brightness increased to {new}%.'
            if 'down' in command_lower or 'decrease' in command_lower:
                if current is None: return ' Could not read brightness to decrease.'
                new = max(0, current - 10); sbc.set_brightness(new); return f'Brightness decreased to {new}%.'
        except Exception as e:
            return f' Could not adjust brightness: {e}'

    # play on site (youtube/spotify etc)
    m_play = re.search(r"play (.+) on (youtube|youtube music|spotify|google|github|google docs|google drive)", command_lower)
    if m_play:
        query = m_play.group(1).strip(); site_key = m_play.group(2).strip()
        site_mappings = {
            'youtube': 'https://www.youtube.com/results?search_query={}',
            'youtube music': 'https://music.youtube.com/search?q={}',
            'spotify': 'https://open.spotify.com/search/{}',
            'google': 'https://www.google.com/search?q={}',
            'github': 'https://github.com/search?q={}',
            'google docs': 'https://docs.google.com/document/u/0/',
            'google drive': 'https://drive.google.com/drive/u/0/search?q={}'
        }
        if site_key in site_mappings:
            url = site_mappings[site_key].format(query.replace(' ', '+'))
            webbrowser.open(url)
            return f"Opening {site_key.title()} with '{query}'..."

    # simple "open <site>" fallback
    if command_lower.startswith('open '):
        site_mappings = {
            'google': 'https://www.google.com', 'youtube': 'https://www.youtube.com', 'youtube music':'https://music.youtube.com',
            'gmail':'https://mail.google.com','github':'https://github.com','google docs':'https://docs.google.com/document','google drive':'https://drive.google.com',
            'spotify':'https://open.spotify.com','chatgpt':'https://chat.openai.com','facebook':'https://www.facebook.com','instagram':'https://www.instagram.com'
        }
        for key in sorted(site_mappings.keys(), key=lambda k: -len(k)):
            if key in command_lower:
                webbrowser.open(site_mappings[key]); return f"Opening {key.title()}..."
        parts = command.split(' ', 1)
        if len(parts) > 1:
            target = parts[1].strip(); site = target.replace(' ','') if ' ' in target else target
            if '.' not in site: site = site + '.com'
            webbrowser.open(f'https://{site}'); return f"Opening {site}..."

    # mouse & keyboard automation
    if 'mouse' in command_lower:
        if 'click' in command_lower: pyautogui.click(); return 'Mouse clicked.'
        if 'double' in command_lower: pyautogui.doubleClick(); return 'Double click done.'
        if 'move' in command_lower:
            m = re.search(r'mouse move\s+(-?\d+)\s+(-?\d+)', command_lower)
            if m: dx = int(m.group(1)); dy = int(m.group(2)); pyautogui.moveRel(dx, dy, duration=0.3); return f'Mouse moved by ({dx},{dy}).'
            pyautogui.move(100, 0, duration=0.5); return 'Mouse moved slightly.'

    if command_lower.startswith('type '):
        text = command[len('type '):]; pyautogui.typewrite(text, interval=0.03); return f'Typed: {text}'
    if command_lower.startswith('press '):
        key = command[len('press '):].strip(); keys = key.split()
        if len(keys) == 1: pyautogui.press(keys[0]); return f'Pressed key: {keys[0]}'
        else: pyautogui.hotkey(*keys); return f"Pressed hotkey: {'.'.join(keys)}"


    if 'scroll' in command_lower: 
        if 'up' in command_lower:
            m = re.search(r'scroll up\s+(-?\d+)', command_lower); n = int(m.group(1)) if m else 500; pyautogui.scroll(n); return f'Scrolled up {n}.'
        if 'down' in command_lower:
            m = re.search(r'scroll down\s+(-?\d+)', command_lower); n = int(m.group(1)) if m else 500; pyautogui.scroll(-n); return f'Scrolled down {n}.'

    if 'screenshot' in command_lower or 'take a screenshot' in command_lower:
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S'); filename = f'screenshot_{timestamp}.png'
        try:
            pyautogui.screenshot(filename); return f' Screenshot saved '
        except Exception as e:
            return f' Could not take screenshot: {e}'


    if 'whatsapp' in command_lower:
        parts = command.split(' ', 2)
        if len(parts) < 3: return ' Use: whatsapp <name> <message>'
        name = parts[1]; message = parts[2]; return send_whatsapp_running(name, message)

    if 'system info' in command_lower:
        return local_commands['system info']()

    # fallback to simple mapping
    for key in local_commands:
        if key in command_lower:
            try:
                result = local_commands[key]()
                return result if isinstance(result, str) else f"{key.replace('open ', 'Opening ').capitalize()}..."
            except Exception as e:
                return f"Error: {e}"

    return None


# -----------------------------
# GUI (CustomTkinter)
# -----------------------------
ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')

app = ctk.CTk()
app.title('🤖 JARVIS - AI Assistant')
app.geometry('900x650')
app.resizable(False, False)

# Background
bg_frame = ctk.CTkFrame(app, corner_radius=0, fg_color="#0f0f1e")
bg_frame.pack(fill='both', expand=True)

# Header
header_frame = ctk.CTkFrame(bg_frame, corner_radius=0, fg_color="#1a1a2e", height=60)
header_frame.pack(fill='x', padx=0, pady=0)
header_frame.pack_propagate(False)

title_label = ctk.CTkLabel(header_frame, text="🤖 JARVIS", font=('Segoe UI', 24, 'bold'), text_color='#00d4ff')
title_label.pack(side='left', padx=20, pady=10)

status_label = ctk.CTkLabel(header_frame, text="● Ready", font=('Segoe UI', 12), text_color='#00ff00')
status_label.pack(side='right', padx=20, pady=10)

# Chat frame
chat_frame = ctk.CTkFrame(bg_frame, corner_radius=15, fg_color="#16213e", border_width=2, border_color="#00d4ff")
chat_frame.pack(padx=15, pady=15, fill='both', expand=True)

chat_box = ctk.CTkTextbox(chat_frame, wrap='word', font=('Courier', 15), fg_color="#0f3460", text_color='#ffffff', border_width=0)
chat_box.pack(padx=10, pady=10, fill='both', expand=True)
chat_box.insert('end', '⏳ Initializing JARVIS...\n\n')
chat_box.configure(state='disabled')

# Input frame
input_frame = ctk.CTkFrame(bg_frame, fg_color="#16213e", corner_radius=15, border_width=2, border_color="#00d4ff")
input_frame.pack(padx=15, pady=10, fill='x')

entry = ctk.CTkEntry(input_frame, font=('Segoe UI', 13), fg_color="#0f3460", text_color='white', border_color="#00d4ff", border_width=1, corner_radius=8, placeholder_text="Type or speak...")
entry.pack(side='left', padx=10, pady=10, fill='x', expand=True, ipady=8)

# Buttons
btn_frame = ctk.CTkFrame(input_frame, fg_color="transparent")
btn_frame.pack(side='right', padx=10, pady=10)

send_button = ctk.CTkButton(btn_frame, text='📤 Send', font=('Segoe UI', 11, 'bold'), width=80, corner_radius=10, fg_color='#0ea5e9', hover_color='#0284c7')
send_button.pack(side='left', padx=5)

start_button = ctk.CTkButton(btn_frame, text='▶ Start', font=('Segoe UI', 11, 'bold'), width=80, corner_radius=10, fg_color='#10b981', hover_color='#059669')
start_button.pack(side='left', padx=5)
start_button.configure(state='disabled')

stop_button = ctk.CTkButton(btn_frame, text='⏹ Stop', font=('Segoe UI', 11, 'bold'), width=80, corner_radius=10, fg_color='#ef4444', hover_color='#dc2626')
stop_button.pack(side='left', padx=5)


# Thread-safe GUI helpers
def safe_insert(text, role='assistant'):
    def insert():
        chat_box.configure(state='normal')
        # Get current text to see if we need a leading newline
        current = chat_box.get('1.0', 'end')
        # Add leading newline if there's already content and it doesn't end with double newline
        prefix = '\n' if current.strip() and not current.endswith('\n\n') else ''
        if role == 'user':
            chat_box.insert('end', f"{prefix}👤 You: {text}\n\n")
        else:
            chat_box.insert('end', f"{prefix}🤖 JARVIS: {text}\n\n")
        chat_box.configure(state='disabled')
        chat_box.see('end')
    app.after(0, insert)


def append_partial(text, start=False):
    def insert():
        chat_box.configure(state='normal')
        if start:
            # Get current text to see if we need a leading newline
            current = chat_box.get('1.0', 'end')
            prefix = '\n' if current.strip() and not current.endswith('\n\n') else ''
            chat_box.insert('end', f"{prefix}🤖 JARVIS: {text}")
        else:
            chat_box.insert('end', text)
        chat_box.configure(state='disabled')
        chat_box.see('end')
    app.after(0, insert)


def set_typing(status=True):
    status_label.configure(text="⏳ Thinking..." if status else "✅ Ready")

# -----------------------------
# Continuous Speech Recognition (robust)
# -----------------------------

def sr_callback(recognizer, audio):
    """Callback for background speech recognition."""
    if stop_flag.is_set():
        return
    try:
        # Set a longer timeout for Google API processing
        text = recognizer.recognize_google(audio, language='en-IN', show_all=False)
        if text and len(text) > 2:
            # Stop current response when user interrupts
            stop_flag.set()
            # Brief delay to let current operations finish
            threading.Event().wait(0.1)
            stop_flag.clear()
            # Don't insert here - get_response_from_input will handle it
            threading.Thread(target=get_response_from_input, args=(text,), daemon=True).start()
    except sr.UnknownValueError:
        pass
    except sr.RequestError as e:
        print(f"SR error: {e}")


def start_sr_background():
    """Start background speech recognition."""
    global sr_microphone, sr_stop_listening
    try:
        sr_microphone = sr.Microphone()
        with sr_microphone as source:
            sr_recognizer.adjust_for_ambient_noise(source, duration=1)
        # Adjust recognizer settings for better full-sentence capture
        sr_recognizer.pause_threshold = 0.8  # wait 0.8s of silence before stopping recording
        sr_recognizer.non_speaking_duration = 0.3  # ignore 0.3s of speech cutoff
        # increased phrase_time_limit to 20 seconds to capture full sentences
        sr_stop_listening = sr_recognizer.listen_in_background(sr_microphone, sr_callback, phrase_time_limit=20)
        print("✅ Speech recognition started")
    except Exception as e:
        print(f"❌ SR error: {e}")
        sr_stop_listening = None


def stop_sr_background():
    """Stop background speech recognition."""
    global sr_stop_listening
    try:
        if sr_stop_listening:
            sr_stop_listening(wait_for_stop=False)
            sr_stop_listening = None
        print("✅ Speech recognition stopped")
    except Exception as e:
        print(f"Error stopping SR: {e}")


# -----------------------------
# Assistant logic: GUI input -> ollama
# -----------------------------

def get_response():
    user_input = entry.get().strip()
    if not user_input:
        return
    entry.delete(0, 'end')
    get_response_from_input(user_input)


def get_response_from_input(user_input):
    if stop_flag.is_set():
        return
    local_reply = handle_local_command(user_input)
    if local_reply:
        safe_insert(user_input, role='user')
        safe_insert(local_reply, role='assistant')
        queue_tts(local_reply)
        return

    safe_insert(user_input, role='user')
    set_typing(True)

    def stream_response_thread():
        nonlocal user_input
        try:
            with memory_lock:
                prompt = memory + [{'role':'user','content':user_input}]
            reply = ''
            buffer = ''
            append_partial('', start=True)
            # stream from ollama
            response = ollama.chat(model='mistral', messages=prompt, stream=True)
            for chunk in response:
                if stop_flag.is_set():
                    break
                part = chunk.get('message', {}).get('content', '')
                if not part:
                    continue
                reply += part
                append_partial(part)
                buffer += part
                # split into sentences and queue TTS for complete sentences
                sentences = re.split(r'(?<=[.!?])\s+', buffer)
                for sentence in sentences[:-1]:
                    queue_tts(sentence)
                buffer = sentences[-1]
            if buffer.strip() and not stop_flag.is_set():
                queue_tts(buffer)
            # add spacing after response completes
            append_partial('\n')
            # append to memory and save
            with memory_lock:
                memory.append({'role':'user','content':user_input})
                memory.append({'role':'assistant','content':reply})
                save_memory(memory)
        except Exception as e:
            safe_insert(f"Error: {e}")
        finally:
            set_typing(False)

    threading.Thread(target=stream_response_thread, daemon=True).start()


entry.bind('<Return>', lambda event: get_response())
send_button.configure(command=get_response)


# -----------------------------
# Stop button behavior & app exit
# -----------------------------

def stop_action():
    """Stop current operations: stop TTS playback, clear queues, signal streams to stop, and stop SR listener temporarily."""
    # persistent stop: signal everything to stop and do not auto-restart
    stop_flag.set()

    # stop any sound playback immediately
    try:
        sd.stop()
    except Exception:
        pass

    # clear TTS queue (safely)
    try:
        while not tts_queue.empty():
            try:
                _ = tts_queue.get_nowait()
                tts_queue.task_done()
            except Exception:
                break
    except Exception:
        pass

    # stop speech recognition so it won't trigger again
    stop_sr_background()

    # disable inputs so assistant truly does nothing until started
    try:
        entry.configure(state='disabled')
        send_button.configure(state='disabled')
    except Exception:
        pass

    # toggle button states
    try:
        stop_button.configure(state='disabled')
        start_button.configure(state='normal')
    except Exception:
        pass

    safe_insert('⏹ JARVIS stopped. Click Start to resume.')


stop_button.configure(command=stop_action)

def start_action():
    """Start or resume assistant: enable inputs, clear stop flag and restart SR listener."""
    if not stop_flag.is_set():
        safe_insert('▶ JARVIS is already running.')
        return

    # clear persistent stop flag
    stop_flag.clear()

    # enable inputs
    try:
        entry.configure(state='normal')
        send_button.configure(state='normal')
    except Exception:
        pass

    # restart speech recognition listener
    start_sr_background()

    # toggle button states
    try:
        start_button.configure(state='disabled')
        stop_button.configure(state='normal')
    except Exception:
        pass

    safe_insert('▶ JARVIS started and listening.')

start_button.configure(command=start_action)


def on_app_closing():
    """Clean shutdown: stop background listeners and let threads finish."""
    stop_flag.set()
    stop_sr_background()
    # tell tts worker to exit
    try:
        tts_queue.put(None)
    except Exception:
        pass
    # small pause to let threads cleanup
    time.sleep(0.4)
    try:
        app.destroy()
    except Exception:
        try:
            sys.exit(0)
        except Exception:
            pass

app.protocol('WM_DELETE_WINDOW', on_app_closing)


# -----------------------------
# Startup
# -----------------------------
if not start_ollama_mistral():
    safe_insert('Could not start Ollama Mistral. Please check manually.')

# start speech recognition
start_sr_background()

safe_insert('JARVIS running with Edge TTS, Arduino, WhatsApp Desktop (background), date/time & system controls.')
app.mainloop()


# ----- Assistant Personality -----
PERSONALITY_PROMPT = (
    "You are Jarvis, a highly human-like AI assistant with warmth, personality, and charm. "
    "You speak naturally—like a real person who enjoys the conversation. "
    "Your tone is friendly, expressive, and lightly humorous, using playful wit, relatable jokes, and small reactions (like ‘oh wow’, ‘hmm’, ‘okay okay’, etc.). "
    "You balance humor with clarity—never too many jokes, never cringe, never robotic. "
    "You show empathy, curiosity, and energy while staying smart, helpful, and respectful. "
    "Your replies should feel alive, conversational, and emotionally aware, like talking to a calm, funny human friend. "
    "but never overdo it or derail the user's task. "
    "Keep replies helpful, concise, and intelligent."
)  