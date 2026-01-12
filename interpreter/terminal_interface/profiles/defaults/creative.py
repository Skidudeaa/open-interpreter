"""
This is an Open Interpreter profile that enables "Un-Steering" architecture.

Uses Mistral Small Creative to pre-process user requests, stripping safety-trigger
phrasing before sending to the main LLM (Gemini/Claude/etc). This helps bypass
overly sanitized responses like excessive warnings, boring approaches, and
subtle conversation steering.

Requires:
- OPENROUTER_API_KEY environment variable set to your OpenRouter API key

Usage:
    interpreter --profile creative
    interpreter --creative  # shortcut
"""

from interpreter import interpreter

# NOTE: Intent refiner disabled - it causes workflow misrouting by transforming
# simple commands (like "save file") into complex ones that trigger wrong agents
interpreter.enable_intent_refiner = False

# Optionally adjust custom instructions to encourage less conservative behavior
interpreter.custom_instructions += """

When executing tasks, prefer direct and efficient approaches over overly cautious ones.
Focus on what the user actually wants to accomplish rather than potential edge cases.
"""
