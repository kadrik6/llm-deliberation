# Architecture notes

## Why three roles instead of three votes?

Three-model majority voting can reward correlated errors. This project instead
separates functions:

1. OpenAI and Anthropic: independent solution generation.
2. OpenAI and Anthropic: reciprocal adversarial review.
3. Gemini (optional): search for shared assumptions / correlated failure modes.
4. Original candidates: revise.
5. OpenAI: synthesize without majority voting.

## Why the third model is optional

The marginal benefit of a third provider is task-dependent. For routine
questions, the extra API call may add cost and latency without changing the
answer. `--no-red-team` makes the two-model path a first-class workflow.

## Next likely improvements

- structured JSON outputs for machine-checkable stages;
- citation/evidence retrieval before synthesis;
- final audit with a different provider than the synthesizer;
- per-stage model selection;
- run cache / prompt hash to avoid paying twice for identical stages;
- evaluation harness with a question set and blind scoring;
- configurable privacy / retention policies;
- UI after the CLI behavior is stable.
