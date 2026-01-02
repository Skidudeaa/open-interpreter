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

# Enable intent refinement (the "Un-Steering" feature)
interpreter.enable_intent_refiner = True

# Use Mistral Small Creative as the refiner (can be overridden with OI_UNSTEER_MODEL env var)
interpreter.intent_refiner_model = "openrouter/mistralai/mistral-small-creative"

# Optionally adjust custom instructions to encourage less conservative behavior
interpreter.custom_instructions += """

When executing tasks, prefer direct and efficient approaches over overly cautious ones.
Focus on what the user actually wants to accomplish rather than potential edge cases.
"""
