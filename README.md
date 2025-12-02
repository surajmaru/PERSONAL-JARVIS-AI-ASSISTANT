# 🧠 Jarvis – My Personal AI Assistant (Python + Ollama + TTS + STT)

## Hey! This is Jarvis, my personal AI assistant — my own little version of Iron Man’s J.A.R.V.I.S. 😄
It runs completely offline, powered by Ollama with the Mistral model, so everything happens locally and stays private.

Jarvis listens, thinks, and talks back instantly using Edge TTS for natural, smooth voice responses. It supports both voice and text input, replies in real time, and feels super responsive — no long delays or robotic speech.

## 🚀 What It Can Do

Real-time conversation (voice + text)
Real-time speech recognition (STT)
Ultra-smooth voice output with Coqui TTS
Keyboard fallback when STT fails
Continuous conversation loop ("listening → LLM → speaking → listening")
Opens apps, gives system info, and answers questions
Easy to extend with tools (time, web search, device control, etc.)
Build-ready GUI version with CustomTkinter (optional)
Has memory, real-time streaming speech, and basic smart home control
Works completely offline — fast and private

## 🧠 How To Run

Ollama
Install Ollama (Windows/Linux/Mac):
https://ollama.com/download
Pull a model:
ollama pull mistral

## 🛠 Requirements

Python packages
pip install ollama
pip install speechrecognition
pip install sounddevice
pip install numpy
pip install pyaudio
pip install TTS
pip install colorama

## Run Jarvis:
python assistant.py

## Example Usage

You: Jarvis, what's the weather today?
Jarvis (STT): Recognized speech...
Jarvis (LLM): Generating word-by-word...
Jarvis (TTS): Speaking in real-time...

💡 About
Jarvis is my take on building a real-time, offline AI assistant that actually feels alive — fast, private, and smart.
It already supports memory, smooth streaming voice output, and smart home control, and I’m always working to make it even better.
