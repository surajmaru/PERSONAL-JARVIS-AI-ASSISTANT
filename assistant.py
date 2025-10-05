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
import serial  # <-- for Arduino

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

# ----- Global Stop Flag -----
stop_flag = threading.Event()

# ----- Persistent Memory -----
MEMORY_FILE = "memory.json"
PERSONAL_INFO = {"name": "Suraj"}

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r") as f:
            messages = json.load(f)
        return messages
    return [{"role": "system", "content": (
        "You are a concise, helpful local assistant. Respond briefly, friendly, and accurately and answer in very short."
    )}]

def save_memory(messages):
    system_messages = [m for m in messages if m["role"] == "system"]
    history = [m for m in messages if m["role"] != "system"][-20:]
    with open(MEMORY_FILE, "w") as f:
        json.dump(system_messages + history, f, indent=2)

memory = load_memory()

if not any(m.get("role") == "system" and "Suraj" in m.get("content", "") for m in memory):
    memory.insert(0, {
        "role": "system",
        "content": (
            f"You are JARVIS, a highly intelligent and professional AI assistant. " 
            f"Your user is {PERSONAL_INFO['name']}. "
            "Respond only to the question asked, keep answers concise, precise, answer in short and to the point and nothing extra"
            "and avoid unnecessary commentary. Speak in a confident, calm, and helpful manner and dont over share things, answer in very short."
        )
    })

# ----- Arduino Setup -----
try:
    arduino = serial.Serial('COM5', 9600, timeout=1) 
    time.sleep(2)
    print("✅ Arduino connected")
except:
    arduino = None
    print("⚠️ Arduino not connected")

def send_arduino_command(cmd):
    if arduino and arduino.is_open:
        arduino.write((cmd + "\n").encode())

# ----- TTS Setup -----
tts_queue = queue.Queue()
current_playback = None  # reference to currently playing audio

async def speak_text(text):
    global current_playback
    if stop_flag.is_set():
        return
    padded_text = "\u200b" + text
    communicate = edge_tts.Communicate(padded_text, voice="en-GB-RyanNeural", rate="-5%")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmpfile:
        path = tmpfile.name
    await communicate.save(path)
    await asyncio.sleep(0.1)
    if stop_flag.is_set():
        os.remove(path)
        return
    data, samplerate = sf.read(path)
    current_playback = sd.play(data, samplerate=samplerate, blocking=False)
    while sd.get_stream().active:
        if stop_flag.is_set():
            sd.stop()
            break
        await asyncio.sleep(0.05)
    os.remove(path)
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

# ----- Local Commands -----
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

local_commands = {
    "open notepad": lambda: os.system("start notepad"),
    "open calculator": lambda: os.system("start calc"),
    "open cmd": lambda: os.system("start cmd"),
    "open chrome": lambda: os.system("start chrome"),
    "open vs code": lambda: os.system("start code"),
    "open vscode": lambda: os.system("start code"),
    "open youtube": lambda: webbrowser.open("https://www.youtube.com"),
    "open spotify web": lambda: webbrowser.open("https://open.spotify.com"),
    "play rickrolled": lambda: webbrowser.open("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"),
    "open udemy": lambda: webbrowser.open("https://www.udemy.com/"),
    "shutdown": lambda: os.system("shutdown /s /t 5"),
    "restart": lambda: os.system("shutdown /r /t 5"),
    "log off": lambda: os.system("shutdown /l"),
    "create file": lambda filename="newfile.txt": open(filename, "w").close(),
    "delete file": lambda filename: os.remove(filename),
    "read file": lambda filename="main.py": read_file_content(filename),
    "update file": lambda filename, content="": write_file_content(filename, content),
    "system info": lambda: f"CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%, Disk: {psutil.disk_usage('/').percent}%"
}

def handle_local_command(command):
    command_lower = command.lower()
    if "weather" in command_lower:
        match = re.search(r'weather (in|at)?\s*([a-zA-Z\s]*)', command_lower)
        city = match.group(2).strip() if match else ""
        city = city if city else "London"
        try:
            url = f"https://wttr.in/{city}?format=3"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                return res.text
            return f"⚠️ Could not fetch weather for {city}."
        except:
            return f"⚠️ Could not fetch weather for {city}."
    if "light" in command_lower or "lights" in command_lower:
        if "on" in command_lower:
            send_arduino_command("ON")
            return "Light turned ON"
        elif "off" in command_lower:
            send_arduino_command("OFF")
            return "Light turned OFF"
    for key in local_commands:
        if key in command_lower:
            if key in ["create file", "delete file", "read file", "update file"]:
                parts = command_lower.split(key)
                args = parts[-1].strip().split(" ", 1)
                filename = args[0] if args[0] else "newfile.txt"
                extra = args[1] if len(args) > 1 else ""
                try:
                    if key == "update file":
                        return local_commands[key](filename, extra)
                    else:
                        return local_commands[key](filename)
                except Exception as e:
                    return f"Error: {e}"
            elif key == "system info":
                return local_commands[key]()
            else:
                local_commands[key]()
                if key in ["shutdown", "restart", "log off"]:
                    return f"{key.capitalize()} command executed."
                return f"{key.replace('open ', 'Opening ').capitalize()}..."
    return None

# ----- Date and Time -----
def get_time_date(query=""):
    now = datetime.now()
    query = query.lower()
    if "time" in query:
        return f"The current time is {now.strftime('%H:%M:%S')}."
    elif "date" in query:
        return f"Today's date is {now.strftime('%A, %d %B %Y')}."
    return None

# ----- GUI -----
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

# ----- Thread-safe inserts -----
def safe_insert(text, role="assistant"):
    def insert():
        chat_box.configure(state="normal")
        if role == "user":
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

def start_listening():
    recognizer.listen_in_background(sr.Microphone(), callback, phrase_time_limit=5)

threading.Thread(target=start_listening, daemon=True).start()

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

    special_reply = get_time_date(user_input)
    if special_reply:
        safe_insert(user_input, role="user")
        safe_insert(special_reply, role="assistant")
        queue_tts(special_reply)
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
        prompt = memory + [{'role': 'user', 'content': user_input}]
        reply = ""
        buffer = ""
        append_partial("", start=True)  # Add "🤖 JARVIS:" at start
        try:
            response = ollama.chat(model='mistral', messages=prompt, stream=True)
            for chunk in response:
                if stop_flag.is_set():
                    break
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
    if current_playback:
        sd.stop()
    queue_tts("")  # clear queue
    safe_insert("⏹ Operation stopped.", role="assistant")
    stop_flag.clear()

stop_button.configure(command=stop_action)

# ----- Start GUI -----
chat_box.configure(state="normal")
chat_box.configure(state="disabled")

if not start_ollama_mistral():
    safe_insert("⚠️ Could not start Ollama Mistral. Please check manually.")

print("✅ Assistant is running with Edge TTS (streaming), Arduino light control, date/time/weather support.")
app.mainloop()
 