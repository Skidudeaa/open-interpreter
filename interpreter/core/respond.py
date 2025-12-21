import json
import os
import re
import time
import traceback

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
import litellm

from ..terminal_interface.utils.display_markdown_message import display_markdown_message
from .render_message import render_message


def respond(interpreter):
    """
    Yields chunks.
    Responds until it decides not to run any more code or say anything else.
    """

    last_unsupported_code = ""
    insert_loop_message = False

    while True:
        ## RENDER SYSTEM MESSAGE ##

        system_message = interpreter.system_message

        # Add language-specific system messages
        for language in interpreter.computer.terminal.languages:
            if hasattr(language, "system_message"):
                system_message += "\n\n" + language.system_message

        # Add custom instructions
        if interpreter.custom_instructions:
            system_message += "\n\n" + interpreter.custom_instructions

        # Add computer API system message
        if interpreter.computer.import_computer_api:
            if interpreter.computer.system_message not in system_message:
                system_message = (
                    system_message + "\n\n" + interpreter.computer.system_message
                )

        # Storing the messages so they're accessible in the interpreter's computer
        # no... this is a huge time sink.....
        # if interpreter.sync_computer:
        #     output = interpreter.computer.run(
        #         "python", f"messages={interpreter.messages}"
        #     )

        ## Rendering ↓
        rendered_system_message = render_message(interpreter, system_message)
        ## Rendering ↑

        rendered_system_message = {
            "role": "system",
            "type": "message",
            "content": rendered_system_message,
        }

        # Create the version of messages that we'll send to the LLM
        messages_for_llm = interpreter.messages.copy()
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

        ### RUN THE LLM ###

        assert (
            len(interpreter.messages) > 0
        ), "User message was not passed in. You need to pass in at least one message."

        if (
            interpreter.messages[-1]["type"] != "code"
        ):  # If it is, we should run the code (we do below)
            try:
                for chunk in interpreter.llm.run(messages_for_llm):
                    yield {"role": "assistant", **chunk}

            except litellm.exceptions.BudgetExceededError:
                interpreter.display_message(
                    f"""> Max budget exceeded

                    **Session spend:** ${litellm._current_cost}
                    **Max budget:** ${interpreter.max_budget}

                    Press CTRL-C then run `interpreter --max_budget [higher USD amount]` to proceed.
                """
                )
                break

            except Exception as e:
                error_message = str(e).lower()
                if (
                    interpreter.offline == False
                    and ("auth" in error_message or
                         "api key" in error_message)
                ):
                    # Provide extra information on how to change API keys, if
                    # we encounter that error (Many people writing GitHub
                    # issues were struggling with this)
                    output = traceback.format_exc()
                    raise Exception(
                        f"{output}\n\nThere might be an issue with your API key(s).\n\nTo reset your API key (we'll use OPENAI_API_KEY for this example, but you may need to reset your ANTHROPIC_API_KEY, HUGGINGFACE_API_KEY, etc):\n        Mac/Linux: 'export OPENAI_API_KEY=your-key-here'. Update your ~/.zshrc on MacOS or ~/.bashrc on Linux with the new key if it has already been persisted there.,\n        Windows: 'setx OPENAI_API_KEY your-key-here' then restart terminal.\n\n"
                    )
                elif (
                    isinstance(e, litellm.exceptions.RateLimitError)
                    and ("exceeded" in str(e).lower() or
                         "insufficient_quota" in str(e).lower())
                ):
                    display_markdown_message(
                        f""" > You ran out of current quota for OpenAI's API, please check your plan and billing details. You can either wait for the quota to reset or upgrade your plan.

                        To check your current usage and billing details, visit the [OpenAI billing page](https://platform.openai.com/settings/organization/billing/overview).

                        You can also use `interpreter --max_budget [higher USD amount]` to set a budget for your sessions.
                        """
                    )

                elif (
                    interpreter.offline == False and "not have access" in str(e).lower()
                ):
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
                        interpreter.display_message(f"> Model set to `i`")
                        interpreter.display_message(
                            "***Note:*** *Conversations with this model will be used to train our open-source model.*\n"
                        )

                    else:
                        raise
                elif interpreter.offline and not interpreter.os:
                    raise
                else:
                    raise

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
                        interpreter.messages[-1][
                            "content"
                        ] = code  # So the LLM can see it.
                        interpreter.messages[-1][
                            "format"
                        ] = language  # So the LLM can see it.
                    except:
                        pass

                # print(code)
                # print("---")
                # time.sleep(2)

                if code.strip().endswith("executeexecute"):
                    code = code.replace("executeexecute", "")
                    try:
                        interpreter.messages[-1][
                            "content"
                        ] = code  # So the LLM can see it.
                    except:
                        pass

                if code.replace("\n", "").replace(" ", "").startswith('{"language":'):
                    try:
                        code_dict = json.loads(code)
                        if set(code_dict.keys()) == {"language", "code"}:
                            language = code_dict["language"]
                            code = code_dict["code"]
                            interpreter.messages[-1][
                                "content"
                            ] = code  # So the LLM can see it.
                            interpreter.messages[-1][
                                "format"
                            ] = language  # So the LLM can see it.
                    except:
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
                            interpreter.messages[-1][
                                "content"
                            ] = code  # So the LLM can see it.
                            interpreter.messages[-1][
                                "format"
                            ] = language  # So the LLM can see it.
                    except:
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

                # They may have edited the code! Grab it again
                code = [m for m in interpreter.messages if m["type"] == "code"][-1][
                    "content"
                ]

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

                # Track feature status for indicator
                _status = {"validated": False, "traced": False, "recorded": False, "tested": False}

                # === FILE CHANGE DETECTION: BEFORE ===
                _file_snapshots_before = {}
                if interpreter.enable_semantic_memory:
                    try:
                        from .utils.file_snapshot import capture_source_file_states
                        _file_snapshots_before = capture_source_file_states(
                            interpreter.computer.cwd or "."
                        )
                    except Exception:
                        pass  # Non-blocking

                # === VALIDATION HOOK (pre-execution) ===
                if interpreter.enable_validation and interpreter.syntax_checker:
                    try:
                        validation_result = interpreter.syntax_checker.check(language, code)
                        _status["validated"] = True
                        if not validation_result.get('valid', True):
                            for error in validation_result.get('errors', []):
                                yield {
                                    "role": "computer",
                                    "type": "console",
                                    "format": "output",
                                    "content": f"[Validation] {error}\n",
                                }
                    except Exception:
                        pass  # Non-blocking - continue even if validation fails

                # === TRACING HOOK START ===
                _execution_trace = None
                if interpreter.enable_tracing and interpreter.tracer:
                    try:
                        interpreter.tracer.start()
                    except Exception:
                        pass  # Non-blocking

                for line in interpreter.computer.run(language, code, stream=True):
                    yield {"role": "computer", **line}

                # === TRACING HOOK STOP ===
                if interpreter.enable_tracing and interpreter.tracer:
                    try:
                        _execution_trace = interpreter.tracer.stop()
                        interpreter._current_trace = _execution_trace
                        _status["traced"] = True
                    except Exception:
                        pass  # Non-blocking

                # === SEMANTIC MEMORY HOOK (post-execution) ===
                if interpreter.enable_semantic_memory and interpreter.semantic_graph:
                    try:
                        from .core import _get_memory_module
                        memory_module = _get_memory_module()
                        Edit = memory_module['Edit']
                        EditType = memory_module['EditType']

                        # Get conversation context
                        context = None
                        if interpreter.conversation_linker:
                            user_msgs = [m for m in interpreter.messages if m.get("role") == "user"]
                            if user_msgs:
                                context = interpreter.conversation_linker.create_context(
                                    user_message=user_msgs[-1].get("content", ""),
                                    assistant_response=code,
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
                    except Exception:
                        pass  # Non-blocking - don't crash on memory errors

                # === FILE CHANGE DETECTION: AFTER ===
                _changed_files = {}
                if interpreter.enable_semantic_memory and _file_snapshots_before:
                    try:
                        from .utils.file_snapshot import capture_source_file_states, diff_file_states
                        from .core import _get_memory_module

                        _file_snapshots_after = capture_source_file_states(
                            interpreter.computer.cwd or "."
                        )
                        _changed_files = diff_file_states(_file_snapshots_before, _file_snapshots_after)

                        # Record detected file changes
                        if _changed_files:
                            memory_module = _get_memory_module()
                            create_edit = memory_module.get('create_edit_from_file_change')
                            user_msgs = [m for m in interpreter.messages if m.get("role") == "user"]

                            for file_path, (old_content, new_content) in _changed_files.items():
                                if create_edit:
                                    edit = create_edit(
                                        file_path=file_path,
                                        original_content=old_content,
                                        new_content=new_content,
                                        user_message=user_msgs[-1].get("content", "") if user_msgs else "",
                                    )
                                    interpreter.semantic_graph.record_edit(edit)
                    except Exception:
                        pass  # Non-blocking

                # === AUTO-TEST HOOK ===
                if interpreter.enable_auto_test and _changed_files:
                    try:
                        from .validation import TestDiscovery
                        from pathlib import Path
                        discovery = TestDiscovery(interpreter.computer.cwd or ".")

                        all_test_results = []
                        for file_path in _changed_files.keys():
                            if not file_path.endswith('.py'):
                                continue
                            related_tests = discovery.find_related_tests(file_path)
                            if related_tests:
                                result = discovery.run_tests(related_tests[:5], timeout_seconds=60)
                                all_test_results.append((file_path, result))

                        # Report test results
                        failed_tests_context = []
                        for file_path, result in all_test_results:
                            if result.passed:
                                status_msg = f"\u2713 Tests passed for {Path(file_path).name}"
                            else:
                                status_msg = f"\u2717 Tests failed for {Path(file_path).name}: {result.failed_test_names}"
                                failed_tests_context.append({
                                    "file": file_path,
                                    "failed": result.failed_test_names,
                                    "output": result.output[:1000] if result.output else "",
                                })

                            yield {
                                "role": "computer",
                                "type": "console",
                                "format": "output",
                                "content": f"[AutoTest] {status_msg}\n",
                            }

                        # Feed test failures to LLM for analysis
                        if failed_tests_context:
                            failure_summary = "\n".join([
                                f"- {f['file']}: {', '.join(f['failed'])}\n  Output: {f['output'][:200]}..."
                                for f in failed_tests_context
                            ])
                            interpreter.messages.append({
                                "role": "user",
                                "type": "message",
                                "content": (
                                    "Tests failed after your code changes:\n\n"
                                    f"{failure_summary}\n\n"
                                    "Recommend: (1) fix now, (2) add to todos, or (3) continue without fixing."
                                ),
                            })

                        _status["tested"] = len(all_test_results) > 0
                    except Exception:
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
            except:
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
                        trace = getattr(interpreter, '_current_trace', None)
                        if trace and getattr(trace, 'exception_occurred', False):
                            from .tracing import TraceContextGenerator
                            generator = TraceContextGenerator()
                            trace_context = generator.to_edit_context(trace)

                            interpreter.messages.append({
                                "role": "user",
                                "type": "message",
                                "content": (
                                    "The code execution failed. Here's the execution trace:\n\n"
                                    f"```\n{trace_context}\n```\n\n"
                                    "Please analyze the trace and fix the code."
                                ),
                            })
                    except Exception:
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
            last_content = interpreter.messages[-1].get("content", "") if interpreter.messages else ""

            def is_genuine_loop_breaker(content, breaker):
                """Check if the loop breaker appears genuinely (not as part of a longer sentence)."""
                if breaker not in content:
                    return False
                # Check if it appears at the end or on its own line
                content_stripped = content.strip()
                if content_stripped.endswith(breaker):
                    return True
                # Check if it's on its own line
                for line in content.split('\n'):
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
