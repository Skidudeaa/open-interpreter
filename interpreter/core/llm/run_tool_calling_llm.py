import os
import re

from .utils.merge_deltas import merge_deltas
from .utils.parse_partial_json import parse_partial_json

tool_schema = {
    "type": "function",
    "function": {
        "name": "execute",
        "description": "Executes code on the user's machine **in the users local environment** and returns the output",
        "parameters": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "The programming language (required parameter to the `execute` function)",
                    "enum": [
                        # This will be filled dynamically with the languages OI has access to.
                    ],
                },
                "code": {
                    "type": "string",
                    "description": "The code to execute (required)",
                },
            },
            "required": ["language", "code"],
        },
    },
}


def process_messages(messages):
    processed_messages = []
    last_tool_id = 0

    i = 0
    while i < len(messages):
        message = messages[i]

        if message.get("function_call"):
            last_tool_id += 1
            tool_id = f"toolu_{last_tool_id}"

            # Convert function_call to tool_calls
            function = message.pop("function_call")
            message["tool_calls"] = [
                {"id": tool_id, "type": "function", "function": function}
            ]
            processed_messages.append(message)

            # Process the next message if it's a function response
            if i + 1 < len(messages) and messages[i + 1].get("role") == "function":
                next_message = messages[i + 1].copy()
                next_message["role"] = "tool"
                next_message["tool_call_id"] = tool_id
                processed_messages.append(next_message)
                i += 1  # Skip the next message as we've already processed it
            else:
                # Add an empty tool response if there isn't one
                processed_messages.append(
                    {"role": "tool", "tool_call_id": tool_id, "content": ""}
                )

        elif message.get("role") == "function":
            # This handles orphaned function responses
            last_tool_id += 1
            tool_id = f"toolu_{last_tool_id}"

            # Add a tool call before this orphaned tool response
            # NOTE: arguments must be valid JSON for Gemini/LiteLLM compatibility
            processed_messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": "execute",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            )

            # Process the function response
            message["role"] = "tool"
            message["tool_call_id"] = tool_id
            processed_messages.append(message)

        else:
            # For non-tool-related messages, just add them as is
            processed_messages.append(message)

        i += 1

    return processed_messages


def run_tool_calling_llm(llm, request_params):
    ## Setup

    # Add languages OI has access to
    tool_schema["function"]["parameters"]["properties"]["language"]["enum"] = [
        i.name.lower() for i in llm.interpreter.computer.terminal.languages
    ]
    tools = [tool_schema]

    # Add MCP tools if bridge is connected
    # WHY: Enables external tool integration via Model Context Protocol
    # TRADEOFF: Slight startup overhead vs access to external tool ecosystem
    mcp_bridge = getattr(llm.interpreter, "_mcp_bridge", None)
    if mcp_bridge and mcp_bridge.get_connected_servers():
        try:
            mcp_tools = mcp_bridge.get_tool_definitions()
            for mcp_tool in mcp_tools:
                # Wrap in OpenAI function format
                tools.append(
                    {
                        "type": "function",
                        "function": mcp_tool,
                    }
                )
        except Exception:
            pass  # Non-blocking: continue without MCP tools

    request_params["tools"] = tools

    request_params["messages"] = process_messages(request_params["messages"])

    # # This makes any role: tool have the ID of the last tool call
    # last_tool_id = 0
    # for i, message in enumerate(request_params["messages"]):
    #     if "function_call" in message:
    #         last_tool_id += 1
    #         function = message.pop("function_call")
    #         message["tool_calls"] = [
    #             {
    #                 "id": "toolu_" + str(last_tool_id),
    #                 "type": "function",
    #                 "function": function,
    #             }
    #         ]
    #     if message["role"] == "function":
    #         if i != 0 and request_params["messages"][i - 1]["role"] == "tool":
    #             request_params["messages"][i]["content"] += message["content"]
    #             message = None
    #         else:
    #             message["role"] = "tool"
    #             message["tool_call_id"] = "toolu_" + str(last_tool_id)
    # request_params["messages"] = [m for m in request_params["messages"] if m != None]

    # This adds an empty tool response for any tool call without a tool response
    # new_messages = []
    # for i, message in enumerate(request_params["messages"]):
    #     new_messages.append(message)
    #     if "tool_calls" in message:
    #         tool_call_id = message["tool_calls"][0]["id"]
    #         if not any(
    #             m
    #             for m in request_params["messages"]
    #             if m.get("role") == "tool" and m.get("tool_call_id") == tool_call_id
    #         ):
    #             new_messages.append(
    #                 {"role": "tool", "tool_call_id": tool_call_id, "content": ""}
    #             )
    # request_params["messages"] = new_messages

    # messages = request_params["messages"]
    # for i in range(len(messages)):
    #     if messages[i]["role"] == "user" and isinstance(messages[i]["content"], list):
    #         # Found an image from the user
    #         image_message = messages[i]
    #         j = i + 1
    #         while j < len(messages) and messages[j]["role"] == "tool":
    #             # Move the image down until it's after all the role: tools
    #             j += 1
    #         messages.insert(j, image_message)
    #         del messages[i]
    # request_params["messages"] = messages

    # Add OpenAI's recommended function message
    # request_params["messages"][0][
    #     "content"
    # ] += "\nUse ONLY the function you have been provided with — 'execute(language, code)'."

    ## Convert output to LMC format

    accumulated_deltas = {}
    language = None
    code = ""
    function_call_detected = False
    accumulated_review = ""
    review_category = None
    buffer = ""
    any_content_yielded = False  # Track if we got any real content
    mcp_tool_call = None  # Track MCP tool calls (non-execute functions)
    # Gemini 3.x attaches a thoughtSignature to function-call parts that MUST be
    # replayed next turn or the API 400s. Capture it here; respond.py stamps it
    # onto the stored code message and convert_to_openai_messages replays it.
    captured_thinking_blocks = None
    # Reset any signature left over from a previous request on this llm instance.
    llm._gemini_thinking_blocks = None

    for chunk in llm.completions(**request_params):
        if "choices" not in chunk or len(chunk["choices"]) == 0:
            # This happens sometimes
            continue

        delta = chunk["choices"][0]["delta"]

        # Capture thought-signatures BEFORE the tool_calls->function_call rewrite
        # below discards them. The block arrives complete (signature is a whole
        # base64 string), so keep the latest non-empty list.
        _tb = getattr(delta, "thinking_blocks", None)
        if _tb:
            captured_thinking_blocks = _tb

        # Convert tool call into function call, which we have great parsing logic for below
        if "tool_calls" in delta and delta["tool_calls"]:
            _fn = delta["tool_calls"][0].function if delta["tool_calls"] else None
            # A real tool call opens with a function name; continuations carry
            # only argument fragments. Some litellm versions (<=1.80) emit a
            # phantom nameless tool_call (arguments "{}", index -1) on
            # claude-sonnet-5 streams before any real call — treating it as one
            # flips function_call_detected and swallows all later text content.
            if _fn and (_fn.name or function_call_detected):
                function_call_detected = True
                delta = {
                    "function_call": {
                        "name": _fn.name,
                        "arguments": _fn.arguments,
                    }
                }

        # Accumulate deltas
        accumulated_deltas = merge_deltas(accumulated_deltas, delta)

        # Track empty content deltas for end-of-stream debug logging
        # WHY: Moved from per-chunk to end-of-stream to reduce log spam
        # TRADEOFF: Less granular debugging vs cleaner output

        if "content" in delta and delta["content"]:
            if function_call_detected:
                # More content after a code block? This is a code review by a judge layer.

                # print("Code safety review:", delta["content"])

                if review_category is None:
                    accumulated_review += delta["content"]

                    if "<unsafe>" in accumulated_review:
                        review_category = "unsafe"
                    if "<warning>" in accumulated_review:
                        review_category = "warning"
                    if "<safe>" in accumulated_review:
                        review_category = "safe"

                if review_category is not None:
                    for tag in [
                        "<safe>",
                        "</safe>",
                        "<warning>",
                        "</warning>",
                        "<unsafe>",
                        "</unsafe>",
                    ]:
                        delta["content"] = delta["content"].replace(tag, "")

                    if re.search("</.*>$", accumulated_review):
                        buffer += delta["content"]
                        continue
                    elif buffer:
                        yield {
                            "type": "review",
                            "format": review_category,
                            "content": buffer + delta["content"],
                        }
                        buffer = ""
                    else:
                        yield {
                            "type": "review",
                            "format": review_category,
                            "content": delta["content"],
                        }
                        buffer = ""

            else:
                any_content_yielded = True
                yield {"type": "message", "content": delta["content"]}

        if (
            accumulated_deltas.get("function_call")
            and "name" in accumulated_deltas["function_call"]
            and (
                accumulated_deltas["function_call"]["name"] == "python"
                or accumulated_deltas["function_call"]["name"] == "functions"
            )
        ):
            if language is None:
                language = "python"

            # Pull the code string straight out of the "arguments" string
            code_delta = accumulated_deltas["function_call"]["arguments"][len(code) :]
            # Update the code
            code = accumulated_deltas["function_call"]["arguments"]
            # Yield the delta
            if code_delta:
                any_content_yielded = True
                yield {
                    "type": "code",
                    "format": language,
                    "content": code_delta,
                }

        if (
            accumulated_deltas.get("function_call")
            and "arguments" in accumulated_deltas["function_call"]
            and accumulated_deltas["function_call"]["arguments"]
        ):
            if "arguments" in accumulated_deltas["function_call"]:
                arguments = accumulated_deltas["function_call"]["arguments"]
                arguments = parse_partial_json(arguments)

                if arguments:
                    if (
                        language is None
                        and "language" in arguments
                        and "code"
                        in arguments  # <- This ensures we're *finished* typing language, as opposed to partially done
                        and arguments["language"]
                    ):
                        language = arguments["language"]

                    if language is not None and "code" in arguments:
                        # Calculate the delta (new characters only)
                        code_delta = arguments["code"][len(code) :]
                        # Update the code
                        code = arguments["code"]
                        # Yield the delta
                        if code_delta:
                            any_content_yielded = True
                            yield {
                                "type": "code",
                                "format": language,
                                "content": code_delta,
                            }
                else:
                    if llm.interpreter.verbose:
                        print("Arguments not a dict.")

        # MCP tool call detection: function_call with name NOT execute/python/functions
        # WHY: Route non-code tools to MCP bridge for execution
        if (
            accumulated_deltas.get("function_call")
            and "name" in accumulated_deltas["function_call"]
            and accumulated_deltas["function_call"]["name"]
            not in ("execute", "python", "functions")
        ):
            func_name = accumulated_deltas["function_call"]["name"]
            func_args = accumulated_deltas["function_call"].get("arguments", "")
            # Only capture when we have complete arguments (valid JSON)
            try:
                parsed_args = parse_partial_json(func_args)
                if parsed_args and isinstance(parsed_args, dict):
                    mcp_tool_call = {
                        "name": func_name,
                        "arguments": parsed_args,
                    }
            except Exception:
                pass  # Not yet complete, keep accumulating

    # Expose captured Gemini thought-signatures so respond.py can attach them to
    # the assistant's code message for round-tripping on the next request.
    if captured_thinking_blocks:
        llm._gemini_thinking_blocks = captured_thinking_blocks

    # Yield MCP tool call if detected
    # WHY: Allow respond.py to execute MCP tools and feed results back to LLM
    if mcp_tool_call:
        any_content_yielded = True
        yield {
            "type": "mcp_tool",
            "format": "call",
            "content": mcp_tool_call,
        }

    if os.getenv("INTERPRETER_REQUIRE_AUTHENTICATION", "False").lower() == "true":
        print("function_call_detected", function_call_detected)
        print("accumulated_review", accumulated_review)
        if function_call_detected and not accumulated_review:
            print("WTF!!!!!!!!!")
            # import pdb
            # pdb.set_trace()
            raise Exception("Judge layer required but did not run.")

    # Debug logging: Log once at end-of-stream if no content was yielded
    # WHY: Moved from per-chunk to reduce log spam while still supporting debugging
    # GUARD: Only log if BOTH debug_empty_responses AND verbose are True
    # This prevents confusing messages from appearing to regular users
    if (
        not any_content_yielded
        and getattr(llm.interpreter, "debug_empty_responses", False)
        and getattr(llm.interpreter, "verbose", False)
    ):
        import logging

        logger = logging.getLogger(__name__)
        try:
            keys = (
                list(accumulated_deltas.keys())
                if hasattr(accumulated_deltas, "keys")
                else ["unknown"]
            )
        except Exception:
            keys = ["unknown"]
        logger.debug(f"LLM returned empty content, accumulated delta keys: {keys}")

    # Empty response retry: If LLM returned only thinking/reasoning blocks with no content,
    # try refining the prompt and retrying once
    # WHY: Gemini 3+ and other thinking models return thinking_blocks or reasoning_content
    # but may not produce visible content. Check all known thinking field locations.
    has_thinking_only = not any_content_yielded and (
        accumulated_deltas.get("reasoning_content")
        or accumulated_deltas.get("thinking_blocks")
        or (accumulated_deltas.get("provider_specific_fields") or {}).get(
            "thinking_blocks"
        )
    )
    retry_succeeded = False
    if has_thinking_only:
        if getattr(llm.interpreter, "enable_intent_refiner", False):
            try:
                from ..intent_refiner import IntentRefiner

                refiner = IntentRefiner(llm.interpreter)
                # Find the last user message
                user_msg = next(
                    (
                        m
                        for m in reversed(request_params["messages"])
                        if m.get("role") == "user"
                    ),
                    None,
                )
                if user_msg and user_msg.get("content"):
                    original = user_msg["content"]
                    # Handle both string content and list content (vision)
                    if isinstance(original, str):
                        refined = refiner.refine(original)
                        if refined != original:
                            user_msg["content"] = refined
                            yield {
                                "type": "message",
                                "content": "[LLM refused - retrying with refined prompt...]\n",
                            }
                            # Recursive retry (once)
                            for chunk in run_tool_calling_llm(llm, request_params):
                                yield chunk
                            retry_succeeded = (
                                True  # Retry yielded (may or may not have content)
                            )
            except Exception as e:
                # Non-blocking - if refinement fails, just continue
                if llm.interpreter.verbose:
                    print(f"[Intent refinement retry failed: {e}]")

    # Final fallback: Extract thinking content or inform the user
    # WHY: Gemini 3+ thinking models often put the answer in thinking_blocks but leave content empty
    # TRADEOFF: Showing reasoning may be verbose, but better than "try rephrasing"
    if has_thinking_only and not retry_succeeded:
        # Extract reasoning from thinking blocks
        thinking_content = (
            accumulated_deltas.get("reasoning_content")
            or accumulated_deltas.get("thinking_blocks")
            or (accumulated_deltas.get("provider_specific_fields") or {}).get(
                "thinking_blocks"
            )
        )

        if thinking_content:
            # Convert list to string if needed (Gemini returns list of dicts)
            if isinstance(thinking_content, list):
                thinking_content = "\n".join(
                    str(t.get("thinking", t) if isinstance(t, dict) else t)
                    for t in thinking_content
                )
            thinking_str = str(thinking_content).strip()

            # Yield thinking content as the response if substantive
            if thinking_str and len(thinking_str) > 20:
                yield {"type": "message", "content": thinking_str}
                return

        # Only show fallback if no usable thinking content
        yield {
            "type": "message",
            "content": "[Model returned empty response. Try rephrasing your request.]\n",
        }
