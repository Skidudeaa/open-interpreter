import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import logging
import sys
import time
import uuid

import requests
import tokentrim as tt

from .run_text_llm import run_text_llm

# from .run_function_calling_llm import run_function_calling_llm
from .run_tool_calling_llm import run_tool_calling_llm
from .utils.convert_to_openai_messages import convert_to_openai_messages

# ARCHITECTURE: Lazy-load litellm for ~300ms cold start savings
# WHY: litellm import is expensive (loads tokenizers, model configs, etc.)
# TRADEOFF: First LLM call pays the import cost vs every startup pays it
# Note: litellm in DEV mode will load .env files from the current directory
# and all parent directories. This can lead to unexpected API keys being loaded
# if there are .env files in parent folders.
_litellm = None


def _get_litellm():
    """Lazy-load litellm module on first use."""
    global _litellm
    if _litellm is None:
        import litellm

        litellm.suppress_debug_info = True
        litellm.REPEATED_STREAMING_CHUNK_LIMIT = 99999999

        # WHY: Gemini 3.x drops thought-signatures in streaming mode (litellm
        # 1.80), which makes multi-turn tool calling 400 after the first run.
        # Patch here so it's applied before the first completion regardless of
        # which model is configured.
        from ._gemini_streaming_patch import apply_gemini_streaming_thinking_patch

        apply_gemini_streaming_thinking_patch()

        _litellm = litellm
    return _litellm


# Create or get the logger
logger = logging.getLogger("LiteLLM")


class SuppressDebugFilter(logging.Filter):
    def filter(self, record):
        # Suppress only the specific message containing the keywords
        if "cost map" in record.getMessage():
            return False  # Suppress this log message
        return True  # Allow all other messages


class Llm:
    """
    A stateless LMC-style LLM with some helpful properties.
    """

    def __init__(self, interpreter):
        # Add the filter to the logger
        logger.addFilter(SuppressDebugFilter())

        # Store a reference to parent interpreter
        self.interpreter = interpreter

        # OpenAI-compatible chat completions "endpoint"
        self.completions = fixed_litellm_completions

        # Settings
        # WHY: Gemini 3.5 Flash as default - fast, capable, cost-effective
        # TRADEOFF: Optimized for speed over maximum capability
        self.model = "gemini/gemini-3.5-flash"
        self.temperature = 0.0

        self.supports_vision = None  # Will try to auto-detect
        self.vision_renderer = (
            self.interpreter.computer.vision.query
        )  # Will only use if supports_vision is False

        self.supports_functions = None  # Will try to auto-detect
        self.execution_instructions: str | bool = (
            "To execute code on the user's machine, write a markdown code block. Specify the language after the ```. You will receive the output. Use any programming language."  # If supports_functions is False, this will be added to the system message. Can be set to False to disable.
        )

        # Optional settings
        self.context_window = None
        self.max_tokens = None
        self.api_base = None
        self.api_key = None
        self.api_version = None
        self._is_loaded = False

        # Budget manager powered by LiteLLM
        self.max_budget = None

    def run(self, messages):
        """
        We're responsible for formatting the call into the llm.completions object,
        starting with LMC messages in interpreter.messages, going to OpenAI compatible messages into the llm,
        respecting whether it's a vision or function model, respecting its context window and max tokens, etc.

        And then processing its output, whether it's a function or non function calling model, into LMC format.
        """

        if not self._is_loaded:
            self.load()

        if (
            self.max_tokens is not None
            and self.context_window is not None
            and self.max_tokens > self.context_window
        ):
            print(
                "Warning: max_tokens is larger than context_window. Setting max_tokens to be 0.2 times the context_window."
            )
            self.max_tokens = int(0.2 * self.context_window)

        # Assertions
        assert (
            messages[0]["role"] == "system"
        ), "First message must have the role 'system'"
        for msg in messages[1:]:
            assert (
                msg["role"] != "system"
            ), "No message after the first can have the role 'system'"

        model = self.model
        if model in [
            "claude-3.5",
            "claude-3-5",
            "claude-3.5-sonnet",
            "claude-3-5-sonnet",
        ]:
            model = "claude-3-5-sonnet-20240620"
            self.model = "claude-3-5-sonnet-20240620"
        # Setup our model endpoint
        if model == "i":
            model = "openai/i"
            if not hasattr(self.interpreter, "conversation_id"):  # Only do this once
                self.context_window = 7000
                self.api_key = "x"
                self.max_tokens = 1000
                self.api_base = "https://api.openinterpreter.com/v0"
                self.interpreter.conversation_id = str(uuid.uuid4())

        # Detect function support
        if self.supports_functions is None:
            try:
                if _get_litellm().supports_function_calling(model):
                    self.supports_functions = True
                else:
                    self.supports_functions = False
            except Exception:
                self.supports_functions = False

        # Detect vision support
        if self.supports_vision is None:
            try:
                if _get_litellm().supports_vision(model):
                    self.supports_vision = True
                else:
                    self.supports_vision = False
            except Exception:
                self.supports_vision = False

        # Trim image messages if they're there (O(n) filtering instead of O(n²) removals)
        image_messages = [msg for msg in messages if msg["type"] == "image"]
        if self.supports_vision:
            if self.interpreter.os:
                # Keep only the last two images if the interpreter is running in OS mode
                if len(image_messages) > 2:
                    keep_images = {id(img) for img in image_messages[-2:]}
                    removed_count = len(image_messages) - 2
                    messages = [
                        m
                        for m in messages
                        if m["type"] != "image" or id(m) in keep_images
                    ]
                    if self.interpreter.verbose:
                        print(f"Removed {removed_count} image message(s)!")
            else:
                # Delete all the middle ones (leave only the first and last 2 images) from messages_for_llm
                if len(image_messages) > 3:
                    # Keep first image and last 2 images
                    keep_images = {
                        id(img) for img in [image_messages[0]] + image_messages[-2:]
                    }
                    removed_count = len(image_messages) - 3
                    messages = [
                        m
                        for m in messages
                        if m["type"] != "image" or id(m) in keep_images
                    ]
                    if self.interpreter.verbose:
                        print(f"Removed {removed_count} image message(s)!")
                # Idea: we could set detail: low for the middle messages, instead of deleting them
        elif not self.supports_vision and self.vision_renderer:
            for img_msg in image_messages:
                if img_msg["format"] != "description":
                    self.interpreter.display_message("\n  *Viewing image...*\n")

                    if img_msg["format"] == "path":
                        precursor = f"The image I'm referring to ({img_msg['content']}) contains the following: "
                        if self.interpreter.computer.import_computer_api:
                            postcursor = f"\nIf you want to ask questions about the image, run `computer.vision.query(path='{img_msg['content']}', query='(ask any question here)')` and a vision AI will answer it."
                        else:
                            postcursor = ""
                    else:
                        precursor = "Imagine I have just shown you an image with this description: "
                        postcursor = ""

                    try:
                        image_description = self.vision_renderer(lmc=img_msg)
                        ocr = self.interpreter.computer.vision.ocr(lmc=img_msg)

                        # It would be nice to format this as a message to the user and display it like: "I see: image_description"

                        img_msg["content"] = (
                            precursor
                            + image_description
                            + "\n---\nI've OCR'd the image, this is the result (this may or may not be relevant. If it's not relevant, ignore this): '''\n"
                            + ocr
                            + "\n'''"
                            + postcursor
                        )
                        img_msg["format"] = "description"

                    except ImportError:
                        print(
                            "\nTo use local vision, run `pip install 'open-interpreter[local]'`.\n"
                        )
                        img_msg["format"] = "description"
                        img_msg["content"] = ""

        # Convert to OpenAI messages format
        messages = convert_to_openai_messages(
            messages,
            function_calling=self.supports_functions,
            vision=self.supports_vision,
            shrink_images=self.interpreter.shrink_images,
            interpreter=self.interpreter,
        )

        system_message = messages[0]["content"]
        messages = messages[1:]

        # === CONTEXT COMPACTION ===
        # Intelligent context management: generate technical flow for old messages
        # instead of just deleting them (which tokentrim does as fallback)
        if getattr(self.interpreter, "enable_context_compaction", False):
            try:
                from ..context.compaction import ContextCompactor

                compactor = ContextCompactor(self.interpreter)
                messages = compactor.compact(messages, system_message)
            except Exception as e:
                # Non-blocking: fall through to tokentrim
                import logging

                logging.getLogger(__name__).debug(
                    f"Context compaction failed (falling back to tokentrim): {e}"
                )

        # Trim messages (fallback for compaction or primary if compaction disabled)
        try:
            if self.context_window and self.max_tokens:
                trim_to_be_this_many_tokens = (
                    self.context_window - self.max_tokens - 25
                )  # arbitrary buffer
                messages = tt.trim(
                    messages,
                    system_message=system_message,
                    max_tokens=trim_to_be_this_many_tokens,
                )
            elif self.context_window and not self.max_tokens:
                # Just trim to the context window if max_tokens not set
                messages = tt.trim(
                    messages,
                    system_message=system_message,
                    max_tokens=self.context_window,
                )
            else:
                try:
                    messages = tt.trim(
                        messages, system_message=system_message, model=model
                    )
                except Exception:
                    if len(messages) == 1:
                        if self.interpreter.in_terminal_interface:
                            self.interpreter.display_message("""
**We were unable to determine the context window of this model.** Defaulting to 8000.

If your model can handle more, run `interpreter --context_window {token limit} --max_tokens {max tokens per response}`.

Continuing...
                            """)
                        else:
                            self.interpreter.display_message("""
**We were unable to determine the context window of this model.** Defaulting to 8000.

If your model can handle more, run `self.context_window = {token limit}`.

Also please set `self.max_tokens = {max tokens per response}`.

Continuing...
                            """)
                    messages = tt.trim(
                        messages, system_message=system_message, max_tokens=8000
                    )
        except Exception:
            # If we're trimming messages, this won't work.
            # If we're trimming from a model we don't know, this won't work.
            # Better not to fail until `messages` is too big, just for frustrations sake, I suppose.

            # Reunite system message with messages
            messages = [{"role": "system", "content": system_message}] + messages

            pass

        # If there should be a system message, there should be a system message!
        # Empty system messages appear to be deleted :(
        if system_message == "":
            if messages[0]["role"] != "system":
                messages = [{"role": "system", "content": system_message}] + messages

        ## Start forming the request

        params = {
            "model": model,
            "messages": messages,
            "stream": True,
        }

        # Optional inputs
        if self.api_key:
            params["api_key"] = self.api_key
        if self.api_base:
            params["api_base"] = self.api_base
        if self.api_version:
            params["api_version"] = self.api_version
        if self.max_tokens:
            params["max_tokens"] = self.max_tokens
        if self.temperature:
            params["temperature"] = self.temperature
        if hasattr(self.interpreter, "conversation_id"):
            params["conversation_id"] = self.interpreter.conversation_id

        # Set some params directly on LiteLLM
        if self.max_budget:
            _get_litellm().max_budget = self.max_budget
        if self.interpreter.verbose:
            _get_litellm().set_verbose = True

        if (
            self.interpreter.debug and False  # DISABLED
        ):  # debug will equal "server" if we're debugging the server specifically
            print("\n\n\nOPENAI COMPATIBLE MESSAGES:\n\n\n")
            for message in messages:
                if len(str(message)) > 5000:
                    print(str(message)[:200] + "...")
                else:
                    print(message)
                print("\n")
            print("\n\n\n")

        if self.supports_functions:
            # yield from run_function_calling_llm(self, params)
            yield from run_tool_calling_llm(self, params)
        else:
            yield from run_text_llm(self, params)

    # If you change model, set _is_loaded to false
    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, value):
        self._model = value
        self._is_loaded = False

    def load(self):
        if self._is_loaded:
            return

        if self.model.startswith("ollama/") and ":" not in self.model:
            self.model = self.model + ":latest"

        self._is_loaded = True

        if self.model.startswith("ollama/"):
            model_name = self.model.replace("ollama/", "")
            api_base = getattr(self, "api_base", None) or os.getenv(
                "OLLAMA_HOST", "http://localhost:11434"
            )
            names = []
            try:
                # List out all downloaded ollama models. Will fail if ollama isn't installed
                response = requests.get(f"{api_base}/api/tags")
                if response.ok:
                    data = response.json()
                    names = [
                        model["name"]
                        for model in data["models"]
                        if "name" in model and model["name"]
                    ]

            except Exception as e:
                print(str(e))
                self.interpreter.display_message(
                    f"> Ollama not found\n\nPlease download Ollama from [ollama.com](https://ollama.com/) to use `{model_name}`.\n"
                )
                exit()

            # Download model if not already installed
            if model_name not in names:
                self.interpreter.display_message(f"\nDownloading {model_name}...\n")
                requests.post(f"{api_base}/api/pull", json={"name": model_name})

            # Get context window if not set
            if self.context_window is None:
                response = requests.post(
                    f"{api_base}/api/show", json={"name": model_name}
                )
                model_info = response.json().get("model_info", {})
                context_length = None
                for key in model_info:
                    if "context_length" in key:
                        context_length = model_info[key]
                        break
                if context_length is not None:
                    self.context_window = context_length
            if self.max_tokens is None:
                if self.context_window is not None:
                    self.max_tokens = int(self.context_window * 0.2)

            # Send a ping, which will actually load the model
            model_name = model_name.replace(":latest", "")
            print(f"Loading {model_name}...\n")

            old_max_tokens = self.max_tokens
            self.max_tokens = 1
            self.interpreter.computer.ai.chat("ping")
            self.max_tokens = old_max_tokens

            self.interpreter.display_message("*Model loaded.*\n")

        # Validate LLM should be moved here!!

        if self.context_window is None:
            try:
                model_info = _get_litellm().get_model_info(model=self.model)
                self.context_window = model_info["max_input_tokens"]
                if self.max_tokens is None:
                    self.max_tokens = min(
                        int(self.context_window * 0.2), model_info["max_output_tokens"]
                    )
            except Exception:
                pass

        # Emit model change event for UI updates
        try:
            from interpreter.terminal_interface.components.ui_events import (
                EventType,
                UIEvent,
                get_event_bus,
            )

            get_event_bus().emit(
                UIEvent(
                    type=EventType.SYSTEM_MODEL_CHANGE,
                    data={
                        "model": self.model,
                        "context_window": self.context_window,
                    },
                    source="llm",
                )
            )
        except ImportError:
            pass  # UI not available


def _needs_nonstreaming_for_signature(params) -> bool:
    """True for Gemini tool-calling requests, which must run non-streaming.

    WHY: Gemini 3.x requires the thoughtSignature on each function call to be
    replayed next turn. litellm 1.80 only surfaces that signature in the
    NON-streaming response — for Open Interpreter's request shape, streaming
    drops it entirely (verified: 0/6 streamed vs 6/6 non-streamed). Without it
    the following turn fails with HTTP 400 "missing a thought_signature".
    ARCHITECTURE: Only tool-calling Gemini requests are affected; plain Gemini
    chat (no tools) keeps streaming, as do all non-Gemini providers.
    TRADEOFF: Loses token-by-token display for Gemini tool turns (the code block
    appears once, after the model responds) in exchange for correctness.
    """
    model = str(params.get("model", "")).lower()
    return (
        "gemini" in model and bool(params.get("tools")) and bool(params.get("stream"))
    )


def _gemini_nonstreaming_chunks(litellm, params):
    """Run a Gemini request non-streaming, re-emit as OI streaming chunks.

    Yields ModelResponseStream chunks shaped exactly like litellm's streaming
    output so run_tool_calling_llm consumes them unchanged — but sourced from a
    non-streaming completion so the thoughtSignature (thinking_blocks) is present.
    """
    from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices

    ns_params = dict(params)
    ns_params["stream"] = False
    response = litellm.completion(**ns_params)
    message = response.choices[0].message
    finish_reason = response.choices[0].finish_reason

    content = getattr(message, "content", None)
    thinking_blocks = getattr(message, "thinking_blocks", None)
    reasoning_content = getattr(message, "reasoning_content", None)

    raw_tool_calls = getattr(message, "tool_calls", None)
    delta_tool_calls = None
    if raw_tool_calls:
        delta_tool_calls = [
            {
                "index": i,
                "id": getattr(tc, "id", None),
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for i, tc in enumerate(raw_tool_calls)
        ]

    # WHY: run_tool_calling_llm rewrites `delta` to the function call when
    # tool_calls are present, dropping any content on the same chunk. Emit text
    # first as its own chunk so a leading explanation isn't lost.
    if content and delta_tool_calls:
        yield ModelResponseStream(
            choices=[StreamingChoices(index=0, delta=Delta(content=content))]
        )
        content = None

    delta = Delta(
        content=content,
        tool_calls=delta_tool_calls,
        thinking_blocks=thinking_blocks,
        reasoning_content=reasoning_content,
    )
    chunk = ModelResponseStream(
        choices=[StreamingChoices(index=0, delta=delta, finish_reason=finish_reason)]
    )
    # Carry usage so the token meter still updates.
    usage = getattr(response, "usage", None)
    if usage is not None:
        chunk.usage = usage
    yield chunk


def fixed_litellm_completions(**params):
    """
    Just uses a dummy API key, since we use litellm without an API key sometimes.
    Hopefully they will fix this!
    """
    litellm = _get_litellm()

    if "local" in params.get("model", ""):
        # Kinda hacky, but this helps sometimes
        params["stop"] = ["<|assistant|>", "<|end|>", "<|eot_id|>"]

    if params.get("model") == "i" and "conversation_id" in params:
        litellm.drop_params = (
            False  # If we don't do this, litellm will drop this param!
        )
    else:
        litellm.drop_params = True

    params["model"] = params["model"].replace(":latest", "")

    # Add timeout if not already set (120 seconds default)
    if "timeout" not in params:
        params["timeout"] = 120

    # Run completion
    attempts = 4
    first_error = None
    _last_error = None  # noqa: F841 - kept for debugging purposes

    params["num_retries"] = 0

    # Gemini tool-calling must run non-streaming so the thoughtSignature is
    # preserved for the next turn (see _needs_nonstreaming_for_signature).
    use_nonstreaming = _needs_nonstreaming_for_signature(params)

    for attempt in range(attempts):
        try:
            chunk_received = False
            chunk_source = (
                _gemini_nonstreaming_chunks(litellm, params)
                if use_nonstreaming
                else litellm.completion(**params)
            )
            for chunk in chunk_source:
                chunk_received = True
                yield chunk

            if not chunk_received:
                raise Exception("LLM returned empty response - no chunks received")

            return  # If the completion is successful, exit the function
        except KeyboardInterrupt:
            print("Exiting...")
            sys.exit(0)
        except Exception as e:
            _last_error = e  # noqa: F841 - kept for debugging purposes
            if attempt == 0:
                # Store the first error
                first_error = e

            error_str = str(e).lower()

            # Check for timeout errors
            if "timeout" in error_str or "timed out" in error_str:
                print(
                    f"Request timed out (attempt {attempt + 1}/{attempts}). Retrying..."
                )
                # Increase timeout for next attempt
                params["timeout"] = min(params.get("timeout", 120) * 1.5, 300)

            if (
                isinstance(e, litellm.exceptions.AuthenticationError)
                and "api_key" not in params
            ):
                print(
                    "LiteLLM requires an API key. Trying again with a dummy API key. In the future, if this fixes it, please set a dummy API key to prevent this message. (e.g `interpreter --api_key x` or `self.api_key = 'x'`)"
                )
                # So, let's try one more time with a dummy API key:
                params["api_key"] = "x"
            if attempt == 1:
                # Try turning up the temperature?
                params["temperature"] = params.get("temperature", 0.0) + 0.1

            # Exponential backoff between retries
            if attempt < attempts - 1:
                wait_time = min(2**attempt, 8)
                time.sleep(wait_time)

    if first_error is not None:
        raise first_error  # If all attempts fail, raise the first error
