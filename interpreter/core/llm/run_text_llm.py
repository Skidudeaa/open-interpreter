def run_text_llm(llm, params, _retry_attempted=False):
    ## Setup

    if llm.execution_instructions:
        try:
            # Add the system message
            params["messages"][0]["content"] += "\n" + llm.execution_instructions
        except Exception:
            print('params["messages"][0]', params["messages"][0])
            raise

    ## Convert output to LMC format

    inside_code_block = False
    accumulated_chunks = (
        []
    )  # Use list for O(n) accumulation instead of O(n²) string concat
    language = None
    chunk_count = 0
    empty_chunk_count = 0
    any_content_yielded = False

    for chunk in llm.completions(**params):
        if llm.interpreter.verbose:
            print("Chunk in coding_llm", chunk)

        if "choices" not in chunk or len(chunk["choices"]) == 0:
            # This happens sometimes - track it
            empty_chunk_count += 1
            if empty_chunk_count > 10 and chunk_count == 0:
                # If we get many empty chunks with no real content, warn
                if llm.interpreter.verbose:
                    print("Warning: Received many empty chunks from LLM")
            continue

        content = chunk["choices"][0]["delta"].get("content", "")

        if content is None:
            empty_chunk_count += 1
            continue

        chunk_count += 1

        accumulated_chunks.append(content)
        accumulated_block = "".join(accumulated_chunks)  # O(n) join when needed

        if accumulated_block.endswith("`"):
            # We might be writing "```" one token at a time.
            continue

        # Did we just enter a code block?
        if "```" in accumulated_block and not inside_code_block:
            inside_code_block = True
            accumulated_block = accumulated_block.split("```")[1]
            # Reset chunks to just the content after the code block marker
            accumulated_chunks = [accumulated_block]

        # Did we just exit a code block?
        if inside_code_block and "```" in accumulated_block:
            return

        # If we're in a code block,
        if inside_code_block:
            # If we don't have a `language`, find it
            if language is None and "\n" in accumulated_block:
                language = accumulated_block.split("\n")[0]

                # Default to python if not specified
                if language == "":
                    if not llm.interpreter.os:
                        language = "python"
                    elif not llm.interpreter.os:
                        # OS mode does this frequently. Takes notes with markdown code blocks
                        language = "text"
                else:
                    # Removes hallucinations containing spaces or non letters.
                    language = "".join(char for char in language if char.isalpha())

            # If we do have a `language`, send it out
            if language:
                any_content_yielded = True
                yield {
                    "type": "code",
                    "format": language,
                    "content": content.replace(language, ""),
                }

        # If we're not in a code block, send the output as a message
        if not inside_code_block:
            any_content_yielded = True
            yield {"type": "message", "content": content}

    # If no content was received at all, try refining and retrying once
    if not any_content_yielded and not _retry_attempted:
        if getattr(llm.interpreter, "enable_intent_refiner", False):
            try:
                from ..intent_refiner import IntentRefiner

                refiner = IntentRefiner(llm.interpreter)
                # Find the last user message
                user_msg = next(
                    (
                        m
                        for m in reversed(params["messages"])
                        if m.get("role") == "user"
                    ),
                    None,
                )
                if user_msg and user_msg.get("content"):
                    original = user_msg["content"]
                    if isinstance(original, str):
                        refined = refiner.refine(original)
                        if refined != original:
                            user_msg["content"] = refined
                            yield {
                                "type": "message",
                                "content": "[LLM refused - retrying with refined prompt...]\n",
                            }
                            # Recursive retry (once)
                            for chunk in run_text_llm(
                                llm, params, _retry_attempted=True
                            ):
                                yield chunk
                            return
            except Exception as e:
                if llm.interpreter.verbose:
                    print(f"[Intent refinement retry failed: {e}]")

        # If we still have no content (retry failed or not enabled), show warning
        if chunk_count == 0 and empty_chunk_count > 0:
            yield {
                "type": "message",
                "content": "[LLM returned no content. This may be a connection issue or the model declined to respond. Please try again.]",
            }
