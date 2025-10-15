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

# ----- Auto-launch Ollama Mistral -----
def is_port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except:
        return False

def start_ollama_mistral():
    if not is_port_open("127.0.0.1", 11434):
        try:
            subprocess.Popen(
                ["ollama", "run", "mistral"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print("⏳ Starting Ollama Mistral...")
        except Exception as e:
            print("⚠️ Failed to start Ollama:", e)
            return False

    for i in range(30):
        if is_port_open("127.0.0.1", 11434):
            print("✅ Ollama Mistral is ready!")
            return True
        time.sleep(1)

    print("⚠️ Ollama Mistral did not start in time.")
    return False

# ----- Global Stop Flag and pending close -----
stop_flag = threading.Event()
pending_close_window = None

# ----- Persistent Memory -----
MEMORY_FILE = "memory.json"
PERSONAL_INFO = {"name": "Suraj"}

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            messages = json.load(f)
        return messages
    return [{"role": "system", "content": "You are a concise, helpful local assistant, Answer concisely, confidently, and to the point. Nothing extra."}] 

def save_memory(messages):
    system_messages = [m for m in messages if m["role"] == "system"]
    history = [m for m in messages if m["role"] != "system"][-20:]
    with open(MEMORY_FILE, "w") as f:
        json.dump(system_messages + history, f, indent=2)

memory = load_memory()

if not any(m.get("role") == "system" for m in memory):
    memory.insert(0, {
        "role": "system",
        "content": (
            f"You are JARVIS, a helpful and intelligent AI assistant for {PERSONAL_INFO['name']}. "
            "Do not introduce yourself or explain how you will respond. "
            "Reply naturally, concisely, and directly. "
            "If the user gives a system command (like open, close, minimize), execute it silently."
        )
    })
 

# ----- Arduino Setup -----
try:
    arduino = serial.Serial('COM5', 9600, timeout=1)
    time.sleep(2)
    print("✅ Arduino connected")
except Exception as e:
    arduino = None
    print("⚠️ Arduino not connected:", e)

def send_arduino_command(cmd):
    if arduino and arduino.is_open:
        arduino.write((cmd + "\n").encode())

# ----- TTS Setup -----
tts_queue = queue.Queue()
current_playback = None

async def speak_text(text):
    global current_playback
    if stop_flag.is_set():
        return
    padded_text = "\u200b" + text
    communicate = edge_tts.Communicate(padded_text, voice="en-GB-RyanNeural", rate="-5%")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmpfile:
        path = tmpfile.name
    await communicate.save(path)
    await asyncio.sleep(0.05)
    if stop_flag.is_set():
        os.remove(path)
        return
    try:
        data, samplerate = sf.read(path)
        current_playback = sd.play(data, samplerate=samplerate, blocking=False)
        while sd.get_stream().active:
            if stop_flag.is_set():
                sd.stop()
                break
            await asyncio.sleep(0.05)
    except Exception as e:
        print("TTS playback error:", e)
    finally:
        try:
            os.remove(path)
        except:
            pass
        current_playback = None

def tts_worker():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while True:
        text = tts_queue.get()
        if not text or len(text.strip()) < 1:
            tts_queue.task_done()
            continue
        try:
            if stop_flag.is_set():
                tts_queue.task_done()
                continue
            loop.run_until_complete(speak_text(text.strip()))
        except Exception as e:
            print("TTS error:", e)
        tts_queue.task_done()

def queue_tts(text):
    if text and len(text.strip()) >= 1:
        tts_queue.put(text.strip())

threading.Thread(target=tts_worker, daemon=True).start()

# ----- Local helpers -----
def read_file_content(filename):
    try:
        with open(filename, "r") as f:
            return f.read()
    except Exception as e:
        return f"⚠️ Error reading file: {e}"

def write_file_content(filename, new_content):
    try:
        with open(filename, "w") as f:
            f.write(new_content)
        return f"✅ File '{filename}' updated successfully."
    except Exception as e:
        return f"⚠️ Error writing file: {e}"

# ----- WhatsApp Desktop (background) -----
def send_whatsapp_running(name, message):
    try:
        wa_windows = [w for w in gw.getAllWindows() if "WhatsApp" in w.title]
        if not wa_windows:
            return "⚠️ WhatsApp is not running."
        wa_window = wa_windows[0]
        try:
            if wa_window.isMinimized:
                wa_window.restore()
                time.sleep(0.3)
            wa_window.activate()
        except:
            pass
        time.sleep(0.3)
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
        return f"⚠️ Error sending message: {e}"

# ----- Commands dictionary -----
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

# ----- Window management helpers -----
def find_window_by_name(name):
    if not name:
        return None
    name_lower = name.lower()
    for w in gw.getAllWindows():
        try:
            if name_lower in w.title.lower():
                return w
        except:
            continue
    return None

def safe_activate_window(window):
    try:
        if window.isMinimized:
            window.restore()
            time.sleep(0.15)
        window.activate()
        return True
    except:
        try:
            window.restore()
            window.activate()
            return True
        except:
            return False

# ----- Enhanced command handler (with YouTube/video support & confirmations) -----
pending_close_window = None
pending_system_action = None
current_file = None  # tracks currently open file

def handle_local_command(command):
    global pending_close_window, pending_system_action, current_file, gw 
    command = (command or "").strip()
    command_lower = command.lower()

    # ----- Window Close Confirmation -----
    if pending_close_window:
        if command_lower in ("yes", "y", "yeah", "yep", "sure", "confirm","yes please", "ok"):
            w = pending_close_window
            pending_close_window = None
            try:
                w.close()
                return f"✅ Closed: {w.title}"
            except Exception as e:
                return f"⚠️ Could not close window: {e}"
        elif command_lower in ("no", "n", "nah", "cancel", "stop"):
            title = pending_close_window.title if pending_close_window else "window"
            pending_close_window = None
            return f"❎ Cancelled closing {title}."
        else:
            return "⚠️ Please answer 'yes' or 'no'."

    # ----- System Action Confirmation -----
    if pending_system_action:
        if command_lower in ("yes", "y", "yeah", "sure", "ok", "confirm"):
            action = pending_system_action
            pending_system_action = None
            if action == "shutdown":
                os.system("shutdown /s /t 5")
                return "🛑 Shutting down in 5 seconds..."
            elif action == "restart":
                os.system("shutdown /r /t 5")
                return "🔄 Restarting in 5 seconds..."
            elif action == "log off":
                os.system("shutdown /l")
                return "🚪 Logging off..."
        elif command_lower in ("no", "n", "nah", "cancel"):
            action = pending_system_action
            pending_system_action = None
            return f"❎ Cancelled {action}."
        else:
            return "⚠️ Please answer 'yes' or 'no'."

    # ----- Date & Time -----
    if "time" in command_lower:
        return f"The current time is {datetime.now().strftime('%H:%M:%S')}."
    if "date" in command_lower:
        return f"Today's date is {datetime.now().strftime('%A, %d %B %Y')}."

    # ----- System Commands with Confirmation -----
    if "shutdown" in command_lower:
        pending_system_action = "shutdown"
        return "Are you sure you want to shut down? (yes/no)"
    if "restart" in command_lower:
        pending_system_action = "restart"
        return "Are you sure you want to restart? (yes/no)"
    if "log off" in command_lower:
        pending_system_action = "log off"
        return "Are you sure you want to log off? (yes/no)"

    # ----- File Creation -----
    if "create a file" in command_lower:
        try:
            # Extract everything after "create a file"
            m = re.search(r"create a file(?: called| named)?\s*(.*)", command_lower)
            if m:
                filename = m.group(1).strip().replace(" ", "_")

                # If user didn't provide extension, default to .txt
                if not os.path.splitext(filename)[1]:
                    # try to detect from keywords
                    if "python" in filename:
                        filename += ".py"
                    elif "javascript" in filename or "js" in filename:
                        filename += ".js"
                    elif "html" in filename:
                        filename += ".html"
                    elif "css" in filename:
                        filename += ".css"
                    else:
                        filename += ".txt"

                file_path = os.path.join(os.getcwd(), filename)

                # Create the file
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("")

                current_file = file_path  # Track current file

                # Open in default editor (VS Code if installed)
                try:
                    subprocess.Popen(["code", file_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except:
                    os.startfile(file_path)

                return f"✅ Created and opened file: {filename}"
            else:
                return "⚠️ Please say the file name after 'create a file'."
        except Exception as e:
            return f"⚠️ Failed to create file: {e}"


    # ----- Smart Write / Add to File using AI -----
    if "write" in command_lower or "add" in command_lower:
        if not current_file:
            return "⚠️ No file currently open. Please create a file first."
        try:
            text_to_write = re.sub(r"^(write|add)\s*", "", command, flags=re.IGNORECASE)
            ext = os.path.splitext(current_file)[1].lower()
            lang_map = {".py":"Python", ".js":"JavaScript", ".html":"HTML", ".css":"CSS", ".txt":"text"}
            lang = lang_map.get(ext, "text")

            with open(current_file, "a", encoding="utf-8") as f:
                for chunk in ollama.chat(
                    model="mistral",
                    messages=[{"role":"user","content":f"Write {lang} content for: {text_to_write}"}],
                    stream=True
                ):
                    if "message" in chunk and "content" in chunk["message"]:
                        part = chunk["message"]["content"]
                        f.write(part)
                        f.flush()
            return f"🤖 Finished writing AI-generated {lang} to {os.path.basename(current_file)}."
        except Exception as e:
            return f"⚠️ Could not write with AI: {e}" 


    # ----- Window Management -----
    if re.match(r"^(close|quit|exit)( window)?", command_lower):
        target = re.search(r"(?:close|quit|exit)(?: window)?(?:\s+(.*))?", command_lower)
        target_name = target.group(1).strip() if target and target.group(1) else None
        w = find_window_by_name(target_name) if target_name else gw.getActiveWindow()
        if not w:
            return f"⚠️ No window found matching '{target_name or 'active window'}'."
        pending_close_window = w
        return f"Are you sure you want to close '{w.title}'? (yes/no)" 

    # Minimize / maximize / restore / switch
        # ----- Minimize window -----
    if "minimize" in command_lower:
        m = re.search(r"minimize(?: window)?(?:\s+(.*))?", command_lower)
        target = m.group(1).strip() if m and m.group(1) else None
        w = find_window_by_name(target) if target else gw.getActiveWindow()
        if w:
            try:
                import pygetwindow as gw
                import win32gui, win32con
                hwnd = win32gui.FindWindow(None, w.title)
                if hwnd:
                    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                    return f"🟡 Minimized: {w.title}"
                else:
                    return f"⚠️ Could not find window handle for '{w.title}'."
            except Exception as e:
                return f"⚠️ Could not minimize: {e}"
        return f"⚠️ No window found matching '{target}'."
 

    if "maximize" in command_lower:
        m = re.search(r"maximize(?: window)?(?:\s+(.*))?", command_lower)
        target = m.group(1).strip() if m and m.group(1) else None
        w = find_window_by_name(target) if target else gw.getActiveWindow()
        if w:
            try:
                w.maximize()
                return f"Maximized: {w.title}"
            except Exception as e:
                return f"⚠️ Could not maximize: {e}"
        return f"⚠️ No window found matching '{target}'."

    if "restore" in command_lower:
        m = re.search(r"restore(?: window)?(?:\s+(.*))?", command_lower)
        target = m.group(1).strip() if m and m.group(1) else None
        w = find_window_by_name(target) if target else gw.getActiveWindow()
        if w:
            ok = safe_activate_window(w)
            return f"Restored/activated: {w.title}" if ok else f"⚠️ Could not activate {w.title}"
        return f"⚠️ No window found matching '{target}'."

    if command_lower.startswith("switch to "):
        target = command_lower.replace("switch to ", "").strip()
        if not target:
            return "⚠️ Use: switch to <window name>"
        w = find_window_by_name(target)
        if w:
            ok = safe_activate_window(w)
            return f"Switched to: {w.title}" if ok else f"⚠️ Could not activate {w.title}"
        return f"⚠️ No window found matching '{target}'."

    # Tab controls
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

    # ----- Volume Control -----
    if "volume" in command_lower:
        if "up" in command_lower or "increase" in command_lower:
            pyautogui.press("volumeup")
            return "Volume increased."
        elif "down" in command_lower or "decrease" in command_lower:
            pyautogui.press("volumedown")
            return "Volume decreased."
        elif "mute" in command_lower or "off" in command_lower:
            pyautogui.press("volumemute")
            return "Volume muted."
        elif "unmute" in command_lower or "on" in command_lower:
            pyautogui.press("volumemute")
            return "Volume unmuted."

    # ----- Brightness Control (supports percentages) -----
    if "brightness" in command_lower:
        try:
            # percentage like "set brightness to 60%"
            m = re.search(r"(\d{1,3})\s*%", command_lower)
            if m:
                val = int(m.group(1))
                val = max(0, min(100, val))
                sbc.set_brightness(val)
                return f"Brightness set to {val}%."
            # textual commands
            try:
                current = sbc.get_brightness(display=0)[0]
            except Exception:
                current = None
            if "up" in command_lower or "increase" in command_lower:
                if current is None:
                    return "⚠️ Could not read brightness to increase."
                new = min(100, current + 10)
                sbc.set_brightness(new)
                return f"Brightness increased to {new}%."
            elif "down" in command_lower or "decrease" in command_lower:
                if current is None:
                    return "⚠️ Could not read brightness to decrease."
                new = max(0, current - 10)
                sbc.set_brightness(new)
                return f"Brightness decreased to {new}%."
        except Exception as e:
            return f"⚠️ Could not adjust brightness: {e}"

    # ----- Website Launch (multi-word supported) -----
    if command_lower.startswith("open "):
        site_mappings = {
            "google": "https://www.google.com",
            "youtube": "https://www.youtube.com",
            "youtube music": "https://music.youtube.com",
            "gmail": "https://mail.google.com",
            "github": "https://github.com",
            "google docs": "https://docs.google.com/document",
            "google drive": "https://drive.google.com",
            "spotify": "https://open.spotify.com",
            "chatgpt": "https://chat.openai.com",
            "facebook": "https://www.facebook.com",
            "instagram": "https://www.instagram.com",
            "youtube studio": "https://studio.youtube.com"
        }
        # check mapping keys (longer names first)
        for key in sorted(site_mappings.keys(), key=lambda k: -len(k)):
            if key in command_lower:
                webbrowser.open(site_mappings[key])
                return f"Opening {key.title()}..."
        # fallback: open last word as site
        parts = command.split(" ", 1)
        if len(parts) > 1:
            target = parts[1].strip()
            if " " in target:
                site = target.replace(" ", "")
            else:
                site = target
            if "." not in site:
                site = site + ".com"
            webbrowser.open(f"https://{site}")
            return f"Opening {site}..."

    # ----- Mouse Automation -----
    if "mouse" in command_lower:
        if "click" in command_lower:
            pyautogui.click()
            return "Mouse clicked."
        elif "double" in command_lower:
            pyautogui.doubleClick()
            return "Double click done."
        elif "move" in command_lower:
            m = re.search(r"mouse move\s+(-?\d+)\s+(-?\d+)", command_lower)
            if m:
                dx = int(m.group(1)); dy = int(m.group(2))
                pyautogui.moveRel(dx, dy, duration=0.3)
                return f"Mouse moved by ({dx},{dy})."
            pyautogui.move(100, 0, duration=0.5)
            return "Mouse moved slightly."

    # ----- Keyboard Automation -----
    if command_lower.startswith("type "):
        text = command[len("type "):]
        pyautogui.typewrite(text, interval=0.03)
        return f"Typed: {text}"
    if command_lower.startswith("press "):
        key = command[len("press "):].strip()
        keys = key.split()
        if len(keys) == 1:
            pyautogui.press(keys[0])
            return f"Pressed key: {keys[0]}"
        else:
            pyautogui.hotkey(*keys)
            return f"Pressed hotkey: {' + '.join(keys)}"

    # ----- Scrolling -----
    if "scroll" in command_lower:
        if "up" in command_lower:
            m = re.search(r"scroll up\s+(-?\d+)", command_lower)
            n = int(m.group(1)) if m else 500
            pyautogui.scroll(n)
            return f"Scrolled up {n}."
        elif "down" in command_lower:
            m = re.search(r"scroll down\s+(-?\d+)", command_lower)
            n = int(m.group(1)) if m else 500
            pyautogui.scroll(-n)
            return f"Scrolled down {n}."

    # ----- Screenshot -----
    if "screenshot" in command_lower or "take a screenshot" in command_lower:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"screenshot_{timestamp}.png"
        try:
            pyautogui.screenshot(filename)
            return f"📸 Screenshot saved as {filename}"
        except Exception as e:
            return f"⚠️ Could not take screenshot: {e}"

    # ----- Arduino Commands -----
    if "light" in command_lower or "lights" in command_lower:
        if "on" in command_lower:
            send_arduino_command("ON")
            return "Light turned ON"
        elif "off" in command_lower:
            send_arduino_command("OFF")
            return "Light turned OFF"

    # ----- WhatsApp Background -----
    if "whatsapp" in command_lower:
        parts = command.split(" ", 2)
        if len(parts) < 3:
            return "⚠️ Use: whatsapp <name> <message>"
        name = parts[1]
        message = parts[2]
        return send_whatsapp_running(name, message)

    # ----- System Info -----
    if "system info" in command_lower:
        return f"CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%, Disk: {psutil.disk_usage('/').percent}%"

    # ----- Website / Video Play -----
    m_play = re.search(r"play (.+) on (youtube|youtube music|spotify|google|github|google docs|google drive)", command_lower)
    if m_play:
        query = m_play.group(1).strip()
        site_key = m_play.group(2).strip()
        site_mappings = {
            "youtube": "https://www.youtube.com/results?search_query={}",
            "youtube music": "https://music.youtube.com/search?q={}",
            "spotify": "https://open.spotify.com/search/{}",
            "google": "https://www.google.com/search?q={}",
            "github": "https://github.com/search?q={}",
            "google docs": "https://docs.google.com/document/u/0/",
            "google drive": "https://drive.google.com/drive/u/0/search?q={}"
        }
        if site_key in site_mappings:
            url = site_mappings[site_key].format(query.replace(" ", "+"))
            webbrowser.open(url)
            return f"Opening {site_key.title()} with '{query}'..."

    # Regular open site commands
    if command_lower.startswith("open "):
        site_mappings = {
            "google": "https://www.google.com",
            "youtube": "https://www.youtube.com",
            "youtube music": "https://music.youtube.com",
            "gmail": "https://mail.google.com",
            "github": "https://github.com",
            "google docs": "https://docs.google.com/document",
            "google drive": "https://drive.google.com",
            "spotify": "https://open.spotify.com",
            "chatgpt": "https://chat.openai.com",
            "facebook": "https://www.facebook.com",
            "instagram": "https://www.instagram.com",
            "youtube studio": "https://studio.youtube.com"
        }
        for key in sorted(site_mappings.keys(), key=lambda k: -len(k)):
            if key in command_lower:
                webbrowser.open(site_mappings[key])
                return f"Opening {key.title()}..."
        parts = command.split(" ", 1)
        if len(parts) > 1:
            target = parts[1].strip()
            if " " in target:
                site = target.replace(" ", "")
            else:
                site = target
            if "." not in site:
                site = site + ".com"
            webbrowser.open(f"https://{site}")
            return f"Opening {site}..."

    # Fallback to local_commands
    for key in local_commands:
        if key in command_lower:
            try:
                result = local_commands[key]()
                return result if isinstance(result, str) else f"{key.replace('open ', 'Opening ').capitalize()}..."
            except Exception as e:
                return f"Error: {e}"

    return None

# ----- GUI Setup -----
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")
app = ctk.CTk()
app.title("🤖 JARVIS")
app.geometry("750x550")
app.resizable(False, False)

bg_frame = ctk.CTkFrame(app, corner_radius=0, width=750, height=550, fg_color="#1e1e2f")
bg_frame.pack(fill="both", expand=True)

chat_frame = ctk.CTkFrame(bg_frame, corner_radius=15, fg_color="#2b2b3a", width=710, height=420)
chat_frame.place(x=20, y=20)

chat_box = ctk.CTkTextbox(chat_frame, wrap="word", font=("Segoe UI", 13),
                          width=680, height=400, fg_color="#2b2b3a", text_color="white",
                          corner_radius=10)
chat_box.pack(padx=10, pady=10, fill="both", expand=True)
chat_box.insert("end", "⏳ Starting JARVIS...\n")
chat_box.configure(state="disabled")

typing_label = ctk.CTkLabel(bg_frame, text="", font=("Segoe UI", 12, "italic"), text_color="#a0a0a0")
typing_label.place(x=20, y=450)

input_frame = ctk.CTkFrame(bg_frame, fg_color="#2b2b3a", corner_radius=15, width=710, height=50)
input_frame.place(x=20, y=480)

entry = ctk.CTkEntry(input_frame, font=("Segoe UI", 13), width=360, corner_radius=10)
entry.pack(side="left", padx=(10,5), pady=5, fill="x", expand=True)

send_button = ctk.CTkButton(input_frame, text="Send", width=80, corner_radius=10,
                            fg_color="#4caf50", hover_color="#66bb6a")
send_button.pack(side="left", padx=(5,5), pady=5)

stop_button = ctk.CTkButton(input_frame, text="⏹ Stop", width=80, corner_radius=10,
                            fg_color="#f44336", hover_color="#e57373")
stop_button.pack(side="left", padx=(5,5), pady=5)

# ----- Thread-safe GUI functions -----
def safe_insert(text, role="assistant"):
    def insert():
        chat_box.configure(state="normal")
        if role=="user":
            chat_box.insert("end", f"\n🧑 You: {text}\n")
        else:
            chat_box.insert("end", f"\n🤖 JARVIS: {text}\n")
        chat_box.configure(state="disabled")
        chat_box.see("end")
    app.after(0, insert)

def append_partial(text, start=False):
    def insert():
        chat_box.configure(state="normal")
        if start:
            chat_box.insert("end", f"\n🤖 JARVIS: {text}")
        else:
            chat_box.insert("end", text)
        chat_box.configure(state="disabled")
        chat_box.see("end")
    app.after(0, insert)

def set_typing(status=True):
    text = "🤖 JARVIS is typing..." if status else ""
    app.after(0, lambda: typing_label.configure(text=text))

# ----- Continuous Speech Recognition -----
recognizer = sr.Recognizer()
def callback(recognizer, audio):
    if stop_flag.is_set():
        return
    try:
        text = recognizer.recognize_google(audio, language="en-IN")
        safe_insert(text, role="user")
        get_response_from_input(text)
    except sr.UnknownValueError:
        pass
    except sr.RequestError as e:
        safe_insert(f"⚠️ Speech recognition error: {e}", role="assistant")

threading.Thread(target=lambda: recognizer.listen_in_background(sr.Microphone(), callback, phrase_time_limit=5), daemon=True).start()

# ----- Assistant Logic -----
def get_response():
    user_input = entry.get().strip()
    if not user_input:
        return
    entry.delete(0, "end")
    get_response_from_input(user_input)

def get_response_from_input(user_input):
    if stop_flag.is_set():
        return
    local_reply = handle_local_command(user_input)
    if local_reply:
        safe_insert(user_input, role="user")
        safe_insert(local_reply, role="assistant")
        queue_tts(local_reply)
        return

    safe_insert(user_input, role="user")
    set_typing(True)

    def stream_response_thread():
        nonlocal user_input
        prompt = memory + [{'role':'user','content':user_input}]
        reply = ""
        buffer = ""
        append_partial("", start=True)
        try:
            response = ollama.chat(model='mistral', messages=prompt, stream=True)
            for chunk in response:
                if stop_flag.is_set(): break
                part = chunk['message']['content']
                reply += part
                append_partial(part)
                buffer += part
                sentences = re.split(r'(?<=[.!?])\s+', buffer)
                for sentence in sentences[:-1]:
                    queue_tts(sentence)
                buffer = sentences[-1]
            if buffer.strip() and not stop_flag.is_set():
                queue_tts(buffer)
            memory.append({'role':'user','content':user_input})
            memory.append({'role':'assistant','content':reply})
            save_memory(memory)
        except Exception as e:
            safe_insert(f"⚠️ Error: {e}")
        finally:
            set_typing(False)

    threading.Thread(target=stream_response_thread, daemon=True).start()

entry.bind("<Return>", lambda event: get_response())
send_button.configure(command=get_response)

# ----- Stop Button -----
def stop_action():
    stop_flag.set()
    try: sd.stop()
    except: pass
    while not tts_queue.empty():
        try: tts_queue.get_nowait(); tts_queue.task_done()
        except: break
    safe_insert("⏹ Operation stopped.")
    def clear_flag(): time.sleep(0.6); stop_flag.clear()
    threading.Thread(target=clear_flag, daemon=True).start()

stop_button.configure(command=stop_action)

# ----- Start GUI -----
chat_box.configure(state="normal")
chat_box.configure(state="disabled")

if not start_ollama_mistral():
    safe_insert("⚠️ Could not start Ollama Mistral. Please check manually.")

print("✅ JARVIS running with Edge TTS, Arduino, WhatsApp Desktop (background), date/time & system controls.")
app.mainloop()
  