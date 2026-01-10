import json
import logging
import os
import re
import traceback
import weakref
from dataclasses import dataclass
from typing import Any

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm

from ..terminal_interface.components.activity_stream import emit_activity
from ..terminal_interface.components.network_status import get_network_status
from ..terminal_interface.components.ui_events import EventType, UIEvent, get_event_bus
from ..terminal_interface.utils.display_markdown_message import display_markdown_message
from .render_message import render_message

# Module logger
logger = logging.getLogger(__name__)


# System message cache to avoid rebuilding every iteration
@dataclass(slots=True)
class _SysMsgCacheEntry:
    key: tuple[Any, ...]
    value: str
    interp_ref: weakref.ref | None = None


_system_message_cache: dict[int, _SysMsgCacheEntry] = {}


# Intent refiner instance cache (lazy-loaded)
@dataclass(slots=True)
class _IntentRefinerCacheEntry:
    refiner: Any
    interp_ref: weakref.ref | None = None


_intent_refiner_cache: dict[int, _IntentRefinerCacheEntry] = {}

# Headless detection is stable per-process. Don't redo expensive/fragile checks every cache miss.
_IS_HEADLESS: bool | None = None


def _detect_headless() -> bool:
    global _IS_HEADLESS
    if _IS_HEADLESS is not None:
        return _IS_HEADLESS
    try:
        import pyautogui

        pyautogui.size()  # fails in headless
        _IS_HEADLESS = False
    except Exception:
        _IS_HEADLESS = True
    return _IS_HEADLESS


def _weakref_or_none(obj: Any) -> weakref.ref | None:
    """Best-effort weakref to protect against id() reuse after GC."""
    try:
        return weakref.ref(obj)
    except TypeError:
        return None


def _get_refined_message(interpreter, content: str) -> str:
    """
    Refine user message content using IntentRefiner if enabled.
    Returns original content if refinement is disabled or fails.
    """
    if not getattr(interpreter, "enable_intent_refiner", False):
        return content

    # Lazy-load refiner (cached per interpreter instance)
    interpreter_id = id(interpreter)
    entry = _intent_refiner_cache.get(interpreter_id)
    if entry is not None:
        if entry.interp_ref is None or entry.interp_ref() is interpreter:
            try:
                return entry.refiner.refine(content) or content
            except Exception as e:
                logger.debug(f"IntentRefiner failed (non-blocking): {e}")
                return content
        # id() reused after GC, discard stale entry
        _intent_refiner_cache.pop(interpreter_id, None)

    try:
        from .intent_refiner import IntentRefiner

        refiner = IntentRefiner(interpreter)
        _intent_refiner_cache[interpreter_id] = _IntentRefinerCacheEntry(
            refiner=refiner,
            interp_ref=_weakref_or_none(interpreter),
        )
        # Hard cap to avoid unbounded growth on long-lived processes
        if len(_intent_refiner_cache) > 64:
            _intent_refiner_cache.clear()
        return refiner.refine(content) or content
    except Exception as e:
        logger.debug(f"IntentRefiner init/refine failed (non-blocking): {e}")
        return content


def _build_system_message(interpreter):
    """
    Build the system message with caching based on dependencies.
    Returns cached version if dependencies haven't changed.
    """
    # Build cache key from dependencies (exclude id; we store per-interpreter id already)
    lang_messages = tuple(
        getattr(lang, "system_message", "")
        for lang in interpreter.computer.terminal.languages
        if hasattr(lang, "system_message")
    )
    cache_key = (
        interpreter.system_message,
        lang_messages,
        interpreter.custom_instructions,
        interpreter.computer.import_computer_api,
        interpreter.computer.system_message
        if interpreter.computer.import_computer_api
        else "",
        _detect_headless(),
    )

    interpreter_id = id(interpreter)
    entry = _system_message_cache.get(interpreter_id)
    if entry is not None and entry.key == cache_key:
        if entry.interp_ref is None or entry.interp_ref() is interpreter:
            return entry.value
        # id() reused after GC
        _system_message_cache.pop(interpreter_id, None)

    # Build system message using parts (faster, avoids quadratic string appends)
    parts: list[str] = []
    base = getattr(interpreter, "system_message", "") or ""
    parts.append(base)

    for lang_msg in lang_messages:
        if lang_msg:
            parts.append(lang_msg)

    if interpreter.custom_instructions:
        parts.append(interpreter.custom_instructions)

    if interpreter.computer.import_computer_api and interpreter.computer.system_message:
        # Avoid duplicates by equality, not substring containment.
        if interpreter.computer.system_message not in parts:
            parts.append(interpreter.computer.system_message)

    if _detect_headless():
        parts.append(
            "IMPORTANT: This is a HEADLESS environment (no X11/display). "
            "Do NOT call computer.display.view(), computer.screenshot(), "
            "computer.mouse, computer.keyboard, or any GUI functions - they will fail."
        )

    system_message = "\n\n".join(p for p in parts if p)

    # Cache (bounded)
    _system_message_cache[interpreter_id] = _SysMsgCacheEntry(
        key=cache_key,
        value=system_message,
        interp_ref=_weakref_or_none(interpreter),
    )
    if len(_system_message_cache) > 128:
        _system_message_cache.clear()

    return system_message


# Extension to language mapping for file diff display
_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}


def _detect_language(file_path: str) -> str:
    """Detect language from file extension for syntax highlighting."""
    ext = os.path.splitext(file_path)[1].lower()
    return _EXTENSION_TO_LANGUAGE.get(ext, "text")


def respond(interpreter):
    """
    Yields chunks.
    Responds until it decides not to run any more code or say anything else.
    """

    last_unsupported_code = ""
    insert_loop_message = False
    loop_message = ""  # Initialized here, assigned in loop body

    while True:
        # ========= AGENT ORCHESTRATION (guarded) =========
        # Only route to agents on a *fresh user message*.
        # Otherwise loop-mode and post-code messages will keep re-triggering agents.
        if (
            interpreter.enable_agents
            and hasattr(interpreter, "agent_orchestrator")
            and interpreter.agent_orchestrator is not None
            and interpreter.messages
            and interpreter.messages[-1].get("role") == "user"
            and interpreter.messages[-1].get("type") == "message"
        ):
            try:
                latest_task = interpreter.messages[-1].get("content", "") or ""
                if latest_task:
                    if getattr(interpreter, "enable_intent_refiner", False):
                        latest_task = _get_refined_message(interpreter, latest_task)

                    from .agents.orchestrator import AgentRole, WorkflowType

                    workflow = interpreter.agent_orchestrator._detect_workflow(
                        latest_task
                    )
                    if workflow in (
                        WorkflowType.EXPLORE,
                        WorkflowType.EDIT,
                        WorkflowType.VALIDATE,
                    ):
                        logger.debug(
                            f"Routing to agent orchestrator: workflow={workflow.value}"
                        )

                        from .agents import base_agent
                        from .agents.live_status import run_with_live_status

                        prev_active = getattr(base_agent, "_INTERPRETER_ACTIVE", False)
                        base_agent._INTERPRETER_ACTIVE = True
                        try:
                            result = run_with_live_status(
                                interpreter.agent_orchestrator,
                                latest_task,
                                workflow=workflow,
                                auto_apply=interpreter.auto_run,
                                plain_text=getattr(
                                    interpreter, "plain_text_display", False
                                ),
                            )
                        finally:
                            base_agent._INTERPRETER_ACTIVE = prev_active

                        if result.success:
                            # For EXPLORE workflow, Scout's synthesized content IS the answer
                            if workflow == WorkflowType.EXPLORE:
                                scout_result = result.agent_results.get(AgentRole.SCOUT)
                                if scout_result and scout_result.content:
                                    yield {
                                        "role": "assistant",
                                        "type": "message",
                                        "content": scout_result.content,
                                    }
                                    return

                            # For other workflows (EDIT, VALIDATE), use summary + context
                            yield {
                                "role": "assistant",
                                "type": "message",
                                "content": result.get_summary()
                                + "\n\n"
                                + result.final_context,
                            }
                            return
                        logger.warning(f"Agent workflow failed: {result.errors}")
            except Exception as e:
                logger.warning(f"Agent orchestration failed, falling back to LLM: {e}")
                # Fall through

        # ========= BUILD LLM MESSAGES =========
        system_message = _build_system_message(interpreter)

        ## Rendering ↓
        rendered_system_message = render_message(interpreter, system_message)
        ## Rendering ↑

        rendered_system_message = {
            "role": "system",
            "type": "message",
            "content": rendered_system_message,
        }

        # IMPORTANT: copy dicts too. Shallow list copy mutates interpreter.messages (gross).
        messages_for_llm = [m.copy() for m in interpreter.messages]

        # Intent refinement: refine the last user message if enabled
        # This strips safety-trigger phrasing before the main LLM sees it
        if getattr(interpreter, "enable_intent_refiner", False):
            for msg in reversed(messages_for_llm):
                if msg.get("role") == "user" and msg.get("type") == "message":
                    original_content = msg.get("content", "")
                    if original_content:
                        msg["content"] = _get_refined_message(
                            interpreter, original_content
                        )
                    break  # Only refine the last user message

        messages_for_llm = [rendered_system_message] + messages_for_llm

        if insert_loop_message:
            messages_for_llm.append(
                {
                    "role": "user",
                    "type": "message",
                    "content": loop_message,
                }
            )
            # Yield two newlines to separate the LLMs reply from previous messages.
            yield {"role": "assistant", "type": "message", "content": "\n\n"}
            insert_loop_message = False

        # (agent orchestration moved earlier + guarded)

        ### RUN THE LLM ###

        assert len(interpreter.messages) > 0, (
            "User message was not passed in. You need to pass in at least one message."
        )

        if (
            interpreter.messages[-1]["type"] != "code"
        ):  # If it is, we should run the code (we do below)
            # Emit activity for LLM thinking
            user_messages = [m for m in interpreter.messages if m.get("role") == "user"]
            if user_messages:
                last_msg = user_messages[-1].get("content", "")[:40]
                emit_activity(
                    "think",
                    "Thinking about response",
                    last_msg + "..."
                    if len(user_messages[-1].get("content", "")) > 40
                    else last_msg,
                )

            # Network status tracking (ensure we always end_request)
            network_status = get_network_status()
            network_status.start_request()
            _req_ok = False

            try:
                for chunk in interpreter.llm.run(messages_for_llm):
                    yield {"role": "assistant", **chunk}

                _req_ok = True

            except litellm.exceptions.BudgetExceededError:
                network_status.set_error("Budget exceeded")
                interpreter.display_message(
                    f"""> Max budget exceeded

                    **Session spend:** ${litellm._current_cost}
                    **Max budget:** ${interpreter.max_budget}

                    Press CTRL-C then run `interpreter --max_budget [higher USD amount]` to proceed.
                """
                )
                break

            except Exception as e:
                network_status.set_error(str(e)[:100])
                error_message = str(e).lower()
                if not interpreter.offline and (
                    "auth" in error_message or "api key" in error_message
                ):
                    # Provide extra information on how to change API keys, if
                    # we encounter that error (Many people writing GitHub
                    # issues were struggling with this)
                    output = traceback.format_exc()
                    raise Exception(
                        f"{output}\n\nThere might be an issue with your API key(s).\n\nTo reset your API key (we'll use OPENAI_API_KEY for this example, but you may need to reset your ANTHROPIC_API_KEY, HUGGINGFACE_API_KEY, etc):\n        Mac/Linux: 'export OPENAI_API_KEY=your-key-here'. Update your ~/.zshrc on MacOS or ~/.bashrc on Linux with the new key if it has already been persisted there.,\n        Windows: 'setx OPENAI_API_KEY your-key-here' then restart terminal.\n\n"
                    ) from e
                elif isinstance(e, litellm.exceptions.RateLimitError) and (
                    "exceeded" in str(e).lower()
                    or "insufficient_quota" in str(e).lower()
                ):
                    display_markdown_message(
                        """ > You ran out of current quota for OpenAI's API, please check your plan and billing details. You can either wait for the quota to reset or upgrade your plan.

                        To check your current usage and billing details, visit the [OpenAI billing page](https://platform.openai.com/settings/organization/billing/overview).

                        You can also use `interpreter --max_budget [higher USD amount]` to set a budget for your sessions.
                        """
                    )

                elif not interpreter.offline and "not have access" in str(e).lower():
                    # Check for invalid model in error message and then fallback.
                    if (
                        "invalid model" in error_message
                        or "model does not exist" in error_message
                    ):
                        provider_message = f"\n\nThe model '{interpreter.llm.model}' does not exist or is invalid. Please check the model name and try again.\n\nWould you like to try Open Interpreter's hosted `i` model instead? (y/n)\n\n  "
                    elif "groq" in error_message:
                        provider_message = f"\n\nYou do not have access to {interpreter.llm.model}. Please check with Groq for more details.\n\nWould you like to try Open Interpreter's hosted `i` model instead? (y/n)\n\n  "
                    else:
                        provider_message = f"\n\nYou do not have access to {interpreter.llm.model}. If you are using an OpenAI model, you may need to add a payment method and purchase credits for the OpenAI API billing page (this is different from ChatGPT Plus).\n\nhttps://platform.openai.com/account/billing/overview\n\nWould you like to try Open Interpreter's hosted `i` model instead? (y/n)\n\n"

                    print(provider_message)

                    response = input()
                    print("")  # <- Aesthetic choice

                    if response.strip().lower() == "y":
                        interpreter.llm.model = "i"
                        interpreter.display_message("> Model set to `i`")
                        interpreter.display_message(
                            "***Note:*** *Conversations with this model will be used to train our open-source model.*\n"
                        )

                    else:
                        raise
                elif interpreter.offline and not interpreter.os:
                    raise
                else:
                    raise
            finally:
                network_status.end_request(success=_req_ok)

        ### RUN CODE (if it's there) ###

        if interpreter.messages[-1]["type"] == "code":
            if interpreter.verbose:
                print("Running code:", interpreter.messages[-1])

            try:
                # What language/code do you want to run?
                language = interpreter.messages[-1]["format"].lower().strip()
                code = interpreter.messages[-1]["content"]

                if code.startswith("`\n"):
                    code = code[2:].strip()
                    if interpreter.verbose:
                        print("Removing `\n")
                    interpreter.messages[-1]["content"] = code  # So the LLM can see it.

                # A common hallucination
                if code.startswith("functions.execute("):
                    edited_code = code.replace("functions.execute(", "").rstrip(")")
                    try:
                        code_dict = json.loads(edited_code)
                        language = code_dict.get("language", language)
                        code = code_dict.get("code", code)
                        interpreter.messages[-1]["content"] = (
                            code  # So the LLM can see it.
                        )
                        interpreter.messages[-1]["format"] = (
                            language  # So the LLM can see it.
                        )
                    except Exception:
                        pass

                # print(code)
                # print("---")
                # time.sleep(2)

                if code.strip().endswith("executeexecute"):
                    code = code.replace("executeexecute", "")
                    try:
                        interpreter.messages[-1]["content"] = (
                            code  # So the LLM can see it.
                        )
                    except Exception:
                        pass

                if code.replace("\n", "").replace(" ", "").startswith('{"language":'):
                    try:
                        code_dict = json.loads(code)
                        if set(code_dict.keys()) == {"language", "code"}:
                            language = code_dict["language"]
                            code = code_dict["code"]
                            interpreter.messages[-1]["content"] = (
                                code  # So the LLM can see it.
                            )
                            interpreter.messages[-1]["format"] = (
                                language  # So the LLM can see it.
                            )
                    except Exception:
                        pass

                if code.replace("\n", "").replace(" ", "").startswith("{language:"):
                    try:
                        code = code.replace("language: ", '"language": ').replace(
                            "code: ", '"code": '
                        )
                        code_dict = json.loads(code)
                        if set(code_dict.keys()) == {"language", "code"}:
                            language = code_dict["language"]
                            code = code_dict["code"]
                            interpreter.messages[-1]["content"] = (
                                code  # So the LLM can see it.
                            )
                            interpreter.messages[-1]["format"] = (
                                language  # So the LLM can see it.
                            )
                    except Exception:
                        pass

                if (
                    language == "text"
                    or language == "markdown"
                    or language == "plaintext"
                ):
                    # It does this sometimes just to take notes. Let it, it's useful.
                    # In the future we should probably not detect this behavior as code at all.
                    real_content = interpreter.messages[-1]["content"]
                    interpreter.messages[-1] = {
                        "role": "assistant",
                        "type": "message",
                        "content": f"```\n{real_content}\n```",
                    }
                    continue

                # Is this language enabled/supported?
                if interpreter.computer.terminal.get_language(language) is None:
                    output = f"`{language}` disabled or not supported."

                    yield {
                        "role": "computer",
                        "type": "console",
                        "format": "output",
                        "content": output,
                    }

                    # Let the response continue so it can deal with the unsupported code in another way. Also prevent looping on the same piece of code.
                    if code != last_unsupported_code:
                        last_unsupported_code = code
                        continue
                    else:
                        break

                # Is there any code at all?
                if code.strip() == "":
                    yield {
                        "role": "computer",
                        "type": "console",
                        "format": "output",
                        "content": "Code block was empty. Please try again, be sure to write code before executing.",
                    }
                    continue

                # Yield a message, such that the user can stop code execution if they want to
                try:
                    yield {
                        "role": "computer",
                        "type": "confirmation",
                        "format": "execution",
                        "content": {
                            "type": "code",
                            "format": language,
                            "content": code,
                        },
                    }
                except GeneratorExit:
                    # The user might exit here.
                    # We need to tell python what we (the generator) should do if they exit
                    break

                # They may have edited the code! Grab it again (O(1) avg via reverse scan)
                code = None
                for m in reversed(interpreter.messages):
                    if m.get("type") == "code":
                        code = m["content"]
                        break
                if code is None:
                    code = interpreter.messages[-1]["content"]  # Fallback

                # don't let it import computer — we handle that!
                if interpreter.computer.import_computer_api and language == "python":
                    code = code.replace("import computer\n", "pass\n")
                    code = re.sub(
                        r"import computer\.(\w+) as (\w+)", r"\2 = computer.\1", code
                    )
                    code = re.sub(
                        r"from computer import (.+)",
                        lambda m: "\n".join(
                            f"{x.strip()} = computer.{x.strip()}"
                            for x in m.group(1).split(", ")
                        ),
                        code,
                    )
                    code = re.sub(r"import computer\.\w+\n", "pass\n", code)
                    # If it does this it sees the screenshot twice (which is expected jupyter behavior)
                    if any(
                        code.strip().split("\n")[-1].startswith(text)
                        for text in [
                            "computer.display.view",
                            "computer.display.screenshot",
                            "computer.view",
                            "computer.screenshot",
                        ]
                    ):
                        code = code + "\npass"

                # sync up some things (is this how we want to do this?)
                interpreter.computer.verbose = interpreter.verbose
                interpreter.computer.debug = interpreter.debug
                interpreter.computer.emit_images = interpreter.llm.supports_vision
                interpreter.computer.max_output = interpreter.max_output

                # sync up the interpreter's computer with your computer
                try:
                    if interpreter.sync_computer and language == "python":
                        computer_dict = interpreter.computer.to_dict()
                        if "_hashes" in computer_dict:
                            computer_dict.pop("_hashes")
                        if "system_message" in computer_dict:
                            computer_dict.pop("system_message")
                        computer_json = json.dumps(computer_dict)
                        sync_code = f"""import json\ncomputer.load_dict(json.loads('''{computer_json}'''))"""
                        interpreter.computer.run("python", sync_code)
                except Exception as e:
                    if interpreter.debug:
                        raise
                    print(str(e))
                    print("Failed to sync iComputer with your Computer. Continuing...")

                ## ↓ CODE IS RUN HERE

                # Emit activity for code execution
                code_preview = code[:30].replace("\n", " ").strip()
                emit_activity(
                    "execute",
                    f"Running {language} code",
                    code_preview + "..." if len(code) > 30 else code_preview,
                )

                # Track feature status for indicator
                _status = {
                    "validated": False,
                    "traced": False,
                    "recorded": False,
                    "tested": False,
                    "committed": False,
                }

                # === FILE CHANGE DETECTION: BEFORE ===
                # Capture file states if semantic memory OR file diff display is enabled
                _file_snapshots_before = {}
                _should_detect_file_changes = (
                    interpreter.enable_semantic_memory
                    or getattr(interpreter, "show_file_diffs", False)
                )
                if _should_detect_file_changes:
                    try:
                        from .utils.file_snapshot import capture_source_file_states

                        _file_snapshots_before = capture_source_file_states(
                            interpreter.computer.cwd or "."
                        )
                    except Exception as e:
                        logger.debug(
                            f"File snapshot capture failed (non-blocking): {e}"
                        )
                        pass  # Non-blocking

                # === VALIDATION HOOK (pre-execution) ===
                if interpreter.enable_validation and interpreter.syntax_checker:
                    try:
                        # Emit start event for UI feedback
                        event_bus = get_event_bus()
                        event_bus.emit(
                            UIEvent(
                                type=EventType.VALIDATION_START,
                                data={"language": language, "code_length": len(code)},
                                source="respond",
                            )
                        )

                        validation_result = interpreter.syntax_checker.check(
                            language, code
                        )
                        _status["validated"] = True
                        is_valid = validation_result.get("valid", True)
                        errors = validation_result.get("errors", [])

                        # Emit end event with result
                        event_bus.emit(
                            UIEvent(
                                type=EventType.VALIDATION_END,
                                data={"valid": is_valid, "error_count": len(errors)},
                                source="respond",
                            )
                        )

                        if not is_valid:
                            for error in errors:
                                yield {
                                    "role": "computer",
                                    "type": "console",
                                    "format": "output",
                                    "content": f"[Validation] {error}\n",
                                }
                    except Exception as e:
                        logger.debug(f"Validation failed (non-blocking): {e}")
                        pass  # Non-blocking - continue even if validation fails

                # === EXECUTION WITH OPTIONAL TRACING ===
                # Using context manager pattern for tracer (start/stop via __enter__/__exit__)
                _execution_trace = None
                _trace_ctx = None

                # Setup tracing context if enabled
                if interpreter.enable_tracing and interpreter.tracer:
                    try:
                        # Emit start event for UI feedback
                        event_bus = get_event_bus()
                        event_bus.emit(
                            UIEvent(
                                type=EventType.TRACING_START,
                                data={"language": language, "code_length": len(code)},
                                source="respond",
                            )
                        )

                        _trace_ctx = interpreter.tracer.trace(code, language)
                        _trace_ctx.__enter__()
                    except Exception:
                        _trace_ctx = None  # Non-blocking - continue without tracing

                try:
                    for line in interpreter.computer.run(language, code, stream=True):
                        yield {"role": "computer", **line}
                finally:
                    # Complete tracing if it was started
                    if _trace_ctx is not None:
                        try:
                            _trace_ctx.__exit__(None, None, None)
                            _execution_trace = _trace_ctx.trace
                            interpreter._current_trace = _execution_trace
                            _status["traced"] = True

                            # Emit tracing end event
                            event_bus = get_event_bus()
                            call_count = (
                                len(_execution_trace.call_graph.calls)
                                if _execution_trace.call_graph
                                else 0
                            )
                            event_bus.emit(
                                UIEvent(
                                    type=EventType.TRACING_END,
                                    data={
                                        "success": not _execution_trace.exception_occurred,
                                        "call_count": call_count,
                                        "exception": _execution_trace.exception_type
                                        if _execution_trace.exception_occurred
                                        else None,
                                    },
                                    source="respond",
                                )
                            )
                        except Exception as e:
                            logger.debug(
                                f"Tracing completion failed (non-blocking): {e}"
                            )
                            pass  # Non-blocking

                # === SEMANTIC MEMORY HOOK (post-execution) ===
                if interpreter.enable_semantic_memory and interpreter.semantic_graph:
                    try:
                        from .core import _get_memory_module

                        memory_module = _get_memory_module()
                        Edit = memory_module["Edit"]
                        EditType = memory_module["EditType"]

                        # Get conversation context
                        context = None
                        if interpreter.conversation_linker:
                            user_msgs = [
                                m
                                for m in interpreter.messages
                                if m.get("role") == "user"
                            ]
                            if user_msgs:
                                context = (
                                    interpreter.conversation_linker.create_context(
                                        user_message=user_msgs[-1].get("content", ""),
                                        assistant_response=code,
                                    )
                                )

                        # Record the code execution
                        edit = Edit(
                            file_path=None,  # Script execution, not file edit
                            original_content="",
                            new_content=code,
                            edit_type=EditType.OTHER,
                            language=language,
                            conversation_context=context,
                        )
                        interpreter.semantic_graph.record_edit(edit)
                        _status["recorded"] = True

                        # Emit memory record event for UI feedback
                        event_bus = get_event_bus()
                        event_bus.emit(
                            UIEvent(
                                type=EventType.MEMORY_RECORD,
                                data={"type": "code_execution", "language": language},
                                source="respond",
                            )
                        )
                    except Exception as e:
                        logger.debug(
                            f"Semantic memory recording failed (non-blocking): {e}"
                        )
                        pass  # Non-blocking - don't crash on memory errors

                # === FILE CHANGE DETECTION: AFTER ===
                _changed_files = {}
                if _file_snapshots_before:
                    try:
                        from .utils.file_snapshot import (
                            capture_source_file_states,
                            diff_file_states,
                        )

                        _file_snapshots_after = capture_source_file_states(
                            interpreter.computer.cwd or "."
                        )
                        _changed_files = diff_file_states(
                            _file_snapshots_before, _file_snapshots_after
                        )

                        # Emit FILE_CHANGE events for UI diff display
                        if _changed_files and getattr(
                            interpreter, "show_file_diffs", False
                        ):
                            event_bus = get_event_bus()
                            for file_path, (
                                old_content,
                                new_content,
                            ) in _changed_files.items():
                                event_bus.emit(
                                    UIEvent(
                                        type=EventType.FILE_CHANGE,
                                        data={
                                            "file_path": file_path,
                                            "old_content": old_content,
                                            "new_content": new_content,
                                            "language": _detect_language(file_path),
                                        },
                                        source="respond",
                                    )
                                )

                        # Record detected file changes to semantic memory
                        if (
                            _changed_files
                            and interpreter.enable_semantic_memory
                            and interpreter.semantic_graph
                        ):
                            from .core import _get_memory_module

                            memory_module = _get_memory_module()
                            create_edit = memory_module.get(
                                "create_edit_from_file_change"
                            )
                            user_msgs = [
                                m
                                for m in interpreter.messages
                                if m.get("role") == "user"
                            ]

                            # Collect all edits for batch processing
                            edits_to_commit = []

                            for file_path, (
                                old_content,
                                new_content,
                            ) in _changed_files.items():
                                if create_edit:
                                    edit = create_edit(
                                        file_path=file_path,
                                        original_content=old_content,
                                        new_content=new_content,
                                        user_message=user_msgs[-1].get("content", "")
                                        if user_msgs
                                        else "",
                                    )
                                    interpreter.semantic_graph.record_edit(edit)
                                    edits_to_commit.append(edit)

                            # === AUTO-COMMIT HOOK ===
                            if interpreter.auto_commit and edits_to_commit:
                                try:
                                    from .validation.auto_commit import (
                                        batch_auto_commit,
                                    )

                                    commit_hash = batch_auto_commit(
                                        edits=edits_to_commit,
                                        project_root=interpreter.computer.cwd or ".",
                                    )

                                    if commit_hash:
                                        # Update all edits with the commit hash
                                        for edit in edits_to_commit:
                                            edit.git_commit_hash = commit_hash
                                            interpreter.semantic_graph.update_edit_commit_hash(
                                                edit.id, commit_hash
                                            )

                                        # Emit commit event for UI feedback
                                        event_bus = get_event_bus()
                                        event_bus.emit(
                                            UIEvent(
                                                type=EventType.GIT_COMMIT,
                                                data={
                                                    "commit_hash": commit_hash,
                                                    "files_count": len(edits_to_commit),
                                                },
                                                source="respond",
                                            )
                                        )
                                        _status["committed"] = True
                                except Exception as commit_error:
                                    logger.debug(
                                        f"Auto-commit failed (non-blocking): {commit_error}"
                                    )
                    except Exception as e:
                        logger.debug(
                            f"File change detection failed (non-blocking): {e}"
                        )
                        pass  # Non-blocking

                # === AUTO-TEST HOOK ===
                if interpreter.enable_auto_test and _changed_files:
                    try:
                        from pathlib import Path

                        from .validation import TestDiscovery

                        # Emit test start event for UI feedback
                        event_bus = get_event_bus()
                        changed_py_files = [
                            f for f in _changed_files.keys() if f.endswith(".py")
                        ]
                        event_bus.emit(
                            UIEvent(
                                type=EventType.TEST_START,
                                data={"files_changed": len(changed_py_files)},
                                source="respond",
                            )
                        )

                        discovery = TestDiscovery(interpreter.computer.cwd or ".")

                        all_test_results = []
                        for file_path in _changed_files.keys():
                            if not file_path.endswith(".py"):
                                continue
                            related_tests = discovery.find_related_tests(file_path)
                            if related_tests:
                                result = discovery.run_tests(
                                    related_tests[:5], timeout_seconds=60
                                )
                                all_test_results.append((file_path, result))

                        # Report test results
                        failed_tests_context = []
                        for file_path, result in all_test_results:
                            if result.passed:
                                status_msg = (
                                    f"\u2713 Tests passed for {Path(file_path).name}"
                                )
                            else:
                                status_msg = f"\u2717 Tests failed for {Path(file_path).name}: {result.failed_test_names}"
                                failed_tests_context.append(
                                    {
                                        "file": file_path,
                                        "failed": result.failed_test_names,
                                        "output": result.output[:1000]
                                        if result.output
                                        else "",
                                    }
                                )

                            yield {
                                "role": "computer",
                                "type": "console",
                                "format": "output",
                                "content": f"[AutoTest] {status_msg}\n",
                            }

                        # Feed test failures to LLM for analysis
                        if failed_tests_context:
                            failure_summary = "\n".join(
                                [
                                    f"- {f['file']}: {', '.join(f['failed'])}\n  Output: {f['output'][:200]}..."
                                    for f in failed_tests_context
                                ]
                            )
                            interpreter.messages.append(
                                {
                                    "role": "user",
                                    "type": "message",
                                    "content": (
                                        "Tests failed after your code changes:\n\n"
                                        f"{failure_summary}\n\n"
                                        "Recommend: (1) fix now, (2) add to todos, or (3) continue without fixing."
                                    ),
                                }
                            )

                        _status["tested"] = len(all_test_results) > 0

                        # Emit test end event with results
                        passed_count = sum(1 for _, r in all_test_results if r.passed)
                        failed_count = len(all_test_results) - passed_count
                        event_bus.emit(
                            UIEvent(
                                type=EventType.TEST_END,
                                data={
                                    "tests_run": len(all_test_results),
                                    "passed": passed_count,
                                    "failed": failed_count,
                                },
                                source="respond",
                            )
                        )
                    except Exception as e:
                        logger.debug(f"Auto-test failed (non-blocking): {e}")
                        pass  # Non-blocking

                # === STATUS INDICATOR (post-execution) ===
                if any(_status.values()):
                    status_parts = []
                    if _status["validated"]:
                        status_parts.append("\u2713 validated")
                    if _status["traced"]:
                        status_parts.append("\u2713 traced")
                    if _status["recorded"]:
                        status_parts.append("\u2713 recorded")
                    if _status["tested"]:
                        status_parts.append("\u2713 tested")
                    if _status["committed"]:
                        status_parts.append("\u2713 committed")
                    yield {
                        "role": "computer",
                        "type": "status",
                        "format": "features",
                        "content": " | ".join(status_parts),
                    }

                ## ↑ CODE IS RUN HERE

                # sync up your computer with the interpreter's computer
                try:
                    if interpreter.sync_computer and language == "python":
                        # sync up the interpreter's computer with your computer
                        result = interpreter.computer.run(
                            "python",
                            """
                            import json
                            computer_dict = computer.to_dict()
                            if '_hashes' in computer_dict:
                                computer_dict.pop('_hashes')
                            if "system_message" in computer_dict:
                                computer_dict.pop("system_message")
                            print(json.dumps(computer_dict))
                            """,
                        )
                        result = result[-1]["content"]
                        interpreter.computer.load_dict(
                            json.loads(result.strip('"').strip("'"))
                        )
                except Exception as e:
                    if interpreter.debug:
                        raise
                    print(str(e))
                    print("Failed to sync your Computer with iComputer. Continuing.")

                # yield final "active_line" message, as if to say, no more code is running. unhighlight active lines
                # (is this a good idea? is this our responsibility? i think so — we're saying what line of code is running! ...?)
                yield {
                    "role": "computer",
                    "type": "console",
                    "format": "active_line",
                    "content": None,
                }

            except KeyboardInterrupt:
                break  # It's fine.
            except Exception:
                error_output = traceback.format_exc()
                yield {
                    "role": "computer",
                    "type": "console",
                    "format": "output",
                    "content": error_output,
                }

                # === TRACE FEEDBACK TO LLM ===
                if interpreter.enable_trace_feedback and interpreter.enable_tracing:
                    try:
                        trace = getattr(interpreter, "_current_trace", None)
                        if trace and getattr(trace, "exception_occurred", False):
                            from .tracing import TraceContextGenerator

                            generator = TraceContextGenerator()
                            trace_context = generator.to_edit_context(trace)

                            interpreter.messages.append(
                                {
                                    "role": "user",
                                    "type": "message",
                                    "content": (
                                        "The code execution failed. Here's the execution trace:\n\n"
                                        f"```\n{trace_context}\n```\n\n"
                                        "Please analyze the trace and fix the code."
                                    ),
                                }
                            )
                    except Exception as e:
                        logger.debug(f"Trace feedback failed (non-blocking): {e}")
                        pass  # Non-blocking

        else:
            ## LOOP MESSAGE
            # This makes it utter specific phrases if it doesn't want to be told to "Proceed."

            loop_message = interpreter.loop_message
            if interpreter.os:
                loop_message = loop_message.replace(
                    "If the entire task I asked for is done,",
                    "If the entire task I asked for is done, take a screenshot to verify it's complete, or if you've already taken a screenshot and verified it's complete,",
                )
            loop_breakers = interpreter.loop_breakers

            # Check if the assistant's response contains a loop breaker
            # Use stricter matching: the phrase must appear on its own line or at end
            last_content = (
                interpreter.messages[-1].get("content", "")
                if interpreter.messages
                else ""
            )

            def is_genuine_loop_breaker(content, breaker):
                """Check if the loop breaker appears genuinely (not as part of a longer sentence)."""
                if breaker not in content:
                    return False
                # Check if it appears at the end or on its own line
                content_stripped = content.strip()
                if content_stripped.endswith(breaker):
                    return True
                # Check if it's on its own line
                for line in content.split("\n"):
                    if line.strip() == breaker:
                        return True
                return False

            has_loop_breaker = any(
                is_genuine_loop_breaker(last_content, task_status)
                for task_status in loop_breakers
            )

            if (
                interpreter.loop
                and interpreter.messages
                and interpreter.messages[-1].get("role", "") == "assistant"
                and not has_loop_breaker
            ):
                # Remove past loop_message messages
                interpreter.messages = [
                    message
                    for message in interpreter.messages
                    if message.get("content", "") != loop_message
                ]
                # Combine adjacent assistant messages, so hopefully it learns to just keep going!
                combined_messages = []
                for message in interpreter.messages:
                    if (
                        combined_messages
                        and message["role"] == "assistant"
                        and combined_messages[-1]["role"] == "assistant"
                        and message["type"] == "message"
                        and combined_messages[-1]["type"] == "message"
                    ):
                        combined_messages[-1]["content"] += "\n" + message["content"]
                    else:
                        combined_messages.append(message)
                interpreter.messages = combined_messages

                # Send model the loop_message:
                insert_loop_message = True

                continue

            # Doesn't want to run code. We're done!
            break

    return
