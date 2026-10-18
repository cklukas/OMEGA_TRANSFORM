<!-- Copyright (c) 2026 by C. Klukas -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Model notes

Most models run on Ollama as they are pulled. Some uploads, especially
community uploads of less common models, are packaged in a way that breaks
OMEGA TRANSFORM's model requests. `omega models` catches these before a
benchmark: such a model ends up as "broken" or "failed to load". This page
records what was wrong with each of them and, where a fix was possible, how the
model was made usable, step by step, so the results can be reproduced.

## LLM-jp-4 8B (National Institute of Informatics, Japan)

The upload `ki-krugle-jp/llm-jp-4-8b-thinking:q4_k_m` is a community GGUF
conversion of [llm-jp/llm-jp-4-8b-thinking](https://huggingface.co/llm-jp/llm-jp-4-8b-thinking).
As pulled, every chat request failed with HTTP 500, so the model check marked
it "failed to load".

### What is wrong with the upload

The model itself loads and generates normally (about 37 tokens per second).
The failure comes from how the upload is packaged and how Ollama 0.40 serves
it:

1. **A Japanese-only system prompt.** The upload's Modelfile sets a system
   prompt that tells the model to think and answer in Japanese only, and to
   write its reasoning in a "thought" channel before the answer in a "final"
   channel (`<|channel|>thought<|message|>…<|end|><|start|>assistant<|channel|>final<|message|>…`).
2. **A stop token that cuts off the answer.** `<|start|>` is a stop token, but
   the final answer begins right after `<|start|>assistant`, so generation
   stops before the answer.
3. **A parser that rejects the output.** For chat requests (`/api/chat`),
   Ollama does not use the Modelfile's template. It renders the prompt with the
   chat template embedded in the GGUF file and checks the answer with a strict
   parser for that template's format. The model's channel output does not match
   it, so Ollama discards the answer and returns HTTP 500 ("The model produced
   output that does not match the expected peg-native format"). Ollama's server
   log names the chosen path for each model: `msg="template selection" …
   selected=gguf_chat_template`.
4. **Undecoded byte tokens.** The conversion's tokenizer emits line breaks as
   byte-fallback tokens, which reach the client as the literal text `<0x0A>`
   instead of a newline. With no line breaks, no edit block can be parsed.

### How it was made usable

1. Pull the upload:

   ```bash
   ollama pull ki-krugle-jp/llm-jp-4-8b-thinking:q4_k_m
   ```

2. Write a Modelfile for a local variant. The repository keeps it as
   `bench/modelfiles/llm-jp-4-8b-omega.Modelfile`:

   ```text
   FROM ki-krugle-jp/llm-jp-4-8b-thinking:q4_k_m
   TEMPLATE """{{- range .Messages }}<|start|>{{ .Role }}<|message|>{{ .Content }}<|end|>{{ end }}<|start|>assistant<|channel|>final<|message|>"""
   PARSER passthrough
   SYSTEM """You are a helpful assistant."""
   PARAMETER stop <|end|>
   PARAMETER stop <|return|>
   PARAMETER stop <|start|>
   PARAMETER num_ctx 32768
   ```

   - `TEMPLATE` renders the conversation in the model's own format and ends the
     prompt with the opened "final" channel, so the model answers directly
     instead of first writing a thought channel (defects 1 and 2).
   - `PARSER passthrough` makes Ollama render chat requests with this template
     and return the output unchanged, instead of using the GGUF's embedded
     template and its strict parser (defect 3). The instruction is not in
     Ollama's Modelfile reference, but Ollama 0.40 accepts it; the server log
     then shows `selected=parser renderer="" parser=passthrough`.
   - `SYSTEM` replaces the Japanese-only system prompt.

3. Create the variant. This reuses the downloaded weights and copies nothing:

   ```bash
   ollama create llm-jp-4-8b-omega:q4_k_m -f bench/modelfiles/llm-jp-4-8b-omega.Modelfile
   ```

4. The byte tokens (defect 4) are decoded by OMEGA TRANSFORM itself: its
   Ollama client turns runs such as `<0x0A>` or `<0xE3><0x81><0x82>` back into
   the characters they encode before an answer is parsed. That helps any model
   with the same conversion problem, so it needs no step here.

5. Check the variant:

   ```bash
   python3 -m omega_transform models --models llm-jp-4-8b-omega:q4_k_m
   ```

   It loads, answers the greeting and returns edit blocks that OMEGA can
   parse. Its verdict is "ok" (37 tokens per second).

### What this variant is, and is not

- It answers without a reasoning phase: the template skips the thought channel.
  It therefore runs only in the benchmark pass without reasoning, even though
  the model was trained to reason first.
- Whether its edits apply is up to the model. In the check, it misspelled a
  function name it was asked to copy exactly.
- The benchmark records it as `llm-jp-4-8b-omega:q4_k_m`, with the release
  date of the original Hugging Face repository.

## GigaChat 3.1 Lightning (Sber, Russia)

The upload `Bored/GigaChat3.1-10B-A1.8B-q4_K_M` loads and runs fast (about 70
tokens per second), but the model check marks it "broken": asked to reply with
exactly the word "hello", it answers "hi", and it does not follow the edit
format. These are the model's own answers, not a packaging fault, so it is not
benchmarked.

## YandexGPT 5 Lite (Yandex, Russia)

`yandex/YandexGPT-5-Lite-8B-instruct-GGUF` passes the model check, but did not
follow the edit format in the check's edit request. It is benchmarked anyway;
expect many of its answers to end as edit errors.
