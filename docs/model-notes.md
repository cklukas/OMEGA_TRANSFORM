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

## Underdog Saluki 27B (Conway Research, USA)

[ConwayResearch/Underdog-Saluki-27B-1.0](https://huggingface.co/ConwayResearch/Underdog-Saluki-27B-1.0)
is a 2-bit build of Alibaba's Qwen3.8-27B (Apache 2.0, October 2026), made from
ISTA-DASLab's Qwen3.8-27B-GSQ-RCO-GGUF and tuned, according to its model card, to
keep tool calling intact. It is not in Ollama's library: the repository holds
only a GGUF file (7.9 GB; Ollama reports the quantization as `IQ2_XS`) and an
optional vision add-on, which the benchmark does not use.

Nothing is wrong with the packaging. The model card's own recipe works, and
Ollama takes the chat template and the reasoning mode from the GGUF
(`template source gguf_chat_template`; capabilities `tools` and `thinking`), so
no template, parser or system prompt needs replacing as it did for LLM-jp-4.
The steps, to reproduce the benchmarked model:

1. Download the weights and check them against the SHA-256 that Hugging Face
   lists for the file (`4a673518b11b1c4445f9b9a9d3c40356f5ba39f6a6260dcb25f3fbfd475d9efb`):

   ```bash
   hf download ConwayResearch/Underdog-Saluki-27B-1.0 Underdog-Saluki-27B-1.0-IQ2-mix.gguf --local-dir .
   shasum -a 256 Underdog-Saluki-27B-1.0-IQ2-mix.gguf
   ```

2. Create the model from the file. `bench/modelfiles/underdog-saluki-27b-iq2-mix.Modelfile`
   holds a single line, `FROM ./Underdog-Saluki-27B-1.0-IQ2-mix.gguf`, which is
   relative to the Modelfile: run the command with the Modelfile in the folder
   of the download.

   ```bash
   ollama create underdog-saluki-27b:iq2-mix -f underdog-saluki-27b-iq2-mix.Modelfile
   ```

3. Check it, like any other model:

   ```bash
   python3 -m omega_transform models --models underdog-saluki-27b:iq2-mix
   ```

The benchmark records it as `underdog-saluki-27b:iq2-mix`, with the creation
date of the Hugging Face repository as its release date. Its card warns that with
thinking on, the model often reasons at length before answering.

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

## Sarvam 30B (Sarvam AI, India)

`frob/sarvam-30b` passes the model check and is benchmarked, but it reasons
before every answer even when reasoning is switched off (`think: false`).
Ollama passes the flag on, yet the model writes a long reasoning text anyway
and spends most of each answer on it: in the earlier benchmark version (8,192
tokens per answer without reasoning), its answers without reasoning averaged
7,500 to 8,200 generated tokens, close to or at that limit, at 40 to 65 tokens
per second. Its results without reasoning are therefore, in effect, results
with reasoning; the current budget gives both modes the same 24,576 tokens.

## EXAONE 4.0 32B (LG AI Research, South Korea)

`ingu627/exaone4.0:32b` works without reasoning, but not with it. With
reasoning switched on (`think: true`), the upload does not separate the model's
reasoning from its answer: the reasoning arrives as answer text rather than in
Ollama's separate reasoning field. OMEGA TRANSFORM limits the length of an
answer to stop runaway output, and the reasoning alone exceeds that limit. In its
first benchmark run with reasoning, all three calls were cut off at 10,504
characters, after five to six minutes each, before any edit was written. EXAONE
is therefore benchmarked only without reasoning.

## Nemotron 3.5 Lightning 30B (NVIDIA, USA)

`nemotron-3.5-lightning:30b-mlx` works well without reasoning, but with
reasoning switched on, its reasoning is usually longer than OMEGA TRANSFORM
allowed in the earlier benchmark version, where the runaway guard stopped an
answer whose reasoning exceeded four times the answer limit (about 41,000
characters in these examples): with reasoning, this stopped every call of the
run on most examples, so the model never wrote an edit there. The same applied,
less often, to Sarvam 30B, Underdog Saluki 27B and LFM3. The current budget
allows eight times the answer limit (about 84,000 characters) and 24,576 tokens
per answer, so that these models can finish their reasoning.

## LFM3 1.7B (Liquid AI, USA)

LFM3-1.7B is the text model of Liquid AI's LFM3 family (announced
2026-10-27; Hugging Face repository
[LiquidAI/LFM3-1.7B](https://huggingface.co/LiquidAI/LFM3-1.7B), LFM Open
License v1.0). At 1.7B parameters it is the smallest model in the benchmark.
It supports reasoning and is benchmarked in both passes, in two builds:

- `lfm3:latest` from Ollama's library is an MLX build (NVFP4, the same as
  `lfm3:1.7b-nvfp4`; 1.7 GB). MLX runs only on Apple Silicon: elsewhere Ollama
  refuses the pull with "this model requires MLX support, but the MLX runtime
  is not available". It needs Ollama 0.40.12 or newer; older versions refuse
  the pull with "requires a newer version of Ollama" (HTTP 412). Benchmarked
  on the Mac only.
- `hf.co/LiquidAI/LFM3-1.7B-GGUF:Q4_K_M` is Liquid AI's own GGUF (1.1 GB),
  pulled from Hugging Face. Ollama 0.40.12 downloads it but cannot load it
  ("unknown model architecture: 'lfm3'"): its GGUF runner, llama.cpp, gained
  LFM3 only after that release
  ([ollama/ollama#19347](https://github.com/ollama/ollama/issues/19347),
  [#19344](https://github.com/ollama/ollama/pull/19344)). This build runs on
  both machines once a newer Ollama is installed, so it is the one that
  compares the Mac and Windows.
