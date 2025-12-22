"""
Cross-Platform Voice Output - Text-to-speech support.

Supports:
- macOS: Native 'say' command
- Windows: SAPI (via pyttsx3)
- Linux: espeak or pyttsx3
"""

import os
import platform
import subprocess
import threading
from typing import Optional

# Track current voice process
_voice_process: Optional[subprocess.Popen] = None
_voice_lock = threading.Lock()


def speak(text: str, voice: str = None, async_speak: bool = True) -> bool:
    """
    Speak text using platform-appropriate TTS.

    Args:
        text: Text to speak
        voice: Optional voice name (platform-specific)
        async_speak: If True, speak in background thread

    Returns:
        True if speech was initiated successfully
    """
    global _voice_process

    # Sanitize text for shell safety
    sanitized = (
        text.replace("\\", "\\\\")
        .replace("\n", " ")
        .replace('"', '\\"')
        .replace("'", "\\'")
    )

    system = platform.system()

    if async_speak:
        thread = threading.Thread(target=_speak_sync, args=(sanitized, voice, system))
        thread.daemon = True
        thread.start()
        return True
    else:
        return _speak_sync(sanitized, voice, system)


def _speak_sync(text: str, voice: str, system: str) -> bool:
    """Synchronous speech implementation."""
    global _voice_process

    # Stop any ongoing speech
    stop_speaking()

    with _voice_lock:
        try:
            if system == "Darwin":
                # macOS: Use built-in 'say' command
                return _speak_macos(text, voice)
            elif system == "Windows":
                # Windows: Try pyttsx3 or PowerShell
                return _speak_windows(text, voice)
            else:
                # Linux: Try espeak, then pyttsx3
                return _speak_linux(text, voice)
        except Exception:
            return False


def _speak_macos(text: str, voice: str = None) -> bool:
    """macOS speech using 'say' command."""
    global _voice_process

    voice = voice or "Fred"
    cmd = ["osascript", "-e", f'say "{text}" using "{voice}"']

    try:
        _voice_process = subprocess.Popen(cmd)
        return True
    except FileNotFoundError:
        return False


def _speak_windows(text: str, voice: str = None) -> bool:
    """Windows speech using PowerShell SAPI or pyttsx3."""
    global _voice_process

    # Try pyttsx3 first (better quality)
    if _try_pyttsx3(text, voice):
        return True

    # Fallback to PowerShell SAPI
    ps_script = f'''
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.Speak("{text}")
    '''

    try:
        _voice_process = subprocess.Popen(
            ["powershell", "-Command", ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except FileNotFoundError:
        return False


def _speak_linux(text: str, voice: str = None) -> bool:
    """Linux speech using espeak or pyttsx3."""
    global _voice_process

    # Try espeak first (most common)
    try:
        _voice_process = subprocess.Popen(
            ["espeak", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except FileNotFoundError:
        pass

    # Try espeak-ng
    try:
        _voice_process = subprocess.Popen(
            ["espeak-ng", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except FileNotFoundError:
        pass

    # Fallback to pyttsx3
    return _try_pyttsx3(text, voice)


def _try_pyttsx3(text: str, voice: str = None) -> bool:
    """Try to use pyttsx3 for speech."""
    try:
        import pyttsx3

        engine = pyttsx3.init()

        if voice:
            voices = engine.getProperty('voices')
            for v in voices:
                if voice.lower() in v.name.lower():
                    engine.setProperty('voice', v.id)
                    break

        engine.say(text)
        engine.runAndWait()
        return True

    except ImportError:
        return False
    except Exception:
        return False


def stop_speaking():
    """Stop any ongoing speech."""
    global _voice_process

    with _voice_lock:
        if _voice_process and _voice_process.poll() is None:
            _voice_process.terminate()
            try:
                _voice_process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                _voice_process.kill()
            _voice_process = None


def is_speaking() -> bool:
    """Check if speech is currently in progress."""
    global _voice_process

    with _voice_lock:
        if _voice_process:
            return _voice_process.poll() is None
    return False


def get_available_voices() -> list:
    """Get list of available voices on this platform."""
    system = platform.system()

    if system == "Darwin":
        # macOS: List voices from say command
        try:
            result = subprocess.run(
                ["say", "-v", "?"],
                capture_output=True,
                text=True
            )
            voices = []
            for line in result.stdout.split("\n"):
                if line.strip():
                    # Format: "VoiceName    language  # description"
                    parts = line.split()
                    if parts:
                        voices.append(parts[0])
            return voices
        except Exception:
            return ["Fred", "Samantha", "Alex"]

    elif system == "Windows":
        # Try to get voices from pyttsx3
        try:
            import pyttsx3
            engine = pyttsx3.init()
            return [v.name for v in engine.getProperty('voices')]
        except Exception:
            return ["Default"]

    else:
        # Linux
        return ["default"]


def check_tts_available() -> bool:
    """Check if any TTS engine is available."""
    system = platform.system()

    if system == "Darwin":
        # macOS always has 'say'
        return True

    elif system == "Windows":
        # Windows has SAPI built-in
        return True

    else:
        # Linux: Check for espeak or pyttsx3
        for cmd in ["espeak", "espeak-ng"]:
            try:
                subprocess.run(
                    [cmd, "--version"],
                    capture_output=True,
                    check=True
                )
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue

        # Try pyttsx3
        try:
            import pyttsx3
            return True
        except ImportError:
            pass

        return False
