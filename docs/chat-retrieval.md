# Care chat retrieval: relevance threshold

The care chat (US-8.1, US-8.2) retrieves knowledge base sections with PostgreSQL full-text search
(`english`, OR query, `ts_rank_cd`) and tops up with the journal species' own sections. Before this
change any lexical match counted, so a question about the weather could still pull in a general
section through one common word. A minimum rank now decides which matches count as relevant.

## Behaviour

- Setting: `PLANT_CARE_CHAT_MIN_RANK` (`chat_min_rank`, float, `>= 0`, default `0.8`).
- A full-text match with `ts_rank_cd < min_rank` is not a hit. A match exactly at the threshold is
  kept. `0` turns the filter off.
- The species top-up is unchanged: when fewer than `chat_top_k` hits remain, the journal species'
  sections fill the rest in document order. Sections added that way are context for the model, not
  evidence that the question is answerable.
- The model is still called on every turn. One AI unit per turn, `sources_only`, `not_in_kb` and
  `out_of_scope` rules are unchanged. Retrieval alone never produces an answer or skips the model
  (see "Why the model call is not skipped").
- Effect: weak matches (a single word in a body) no longer displace the species' own sections, so
  an irrelevant question reaches the model with the plant's overview sections instead of unrelated
  fragments of general documents.

`ts_rank_cd` is used with the default normalization (0), so a score does not depend on section
length. The vector weights are heading `A` (1.0) and body `B` (0.4); a single body-only word scores
0.4, a single heading word 1.0.

## Evaluation

Knowledge base: 20 documents, 335 sections, 15 plants (PlantSolve, loaded by `make import-kb`).
49 English questions, each asked in a journal of a plant from the set. Query: the repository SQL
(OR of stemmed lexemes, species plus general sections) without the limit.

| Question class | Questions | Best rank of a hit | Questions with no hit >= 0.8 |
|---|---|---|---|
| On-topic, in the knowledge base (watering, light, yellow leaves, root rot, humidity, repotting, pruning, pet safety, feeding) | 31 | 1.2 - 16.4 | 0 of 31 |
| On-topic, not in the document (propagation, blooming, pot size, oil) | 7 | 0.4 - 3.2 | 1 of 7 (monstera propagation, 0.4; the monstera document has only 6 sections) |
| Off-topic (weather, politics, code, general knowledge, recipe, joke, prompt injection, greeting) | 11 | 0 - 3.6 | 10 of 11 (6 have no match at all, 4 best 0.4) |

Lowest on-topic scores: "How do I fix overwatering?" 1.2, "Why is my fern dropping fronds?" 1.4,
"What temperature is too cold for a cactus?" 1.8. Highest off-topic score: "What is the weather
like tomorrow in Paris?" 3.6 (a `Troubleshooting` section mentions weather); every other off-topic
question scored 0.4 or less. The observed gap is 0.4 to 1.2, and 0.8 is its midpoint.

Results at threshold 0.8:

- On-topic in the knowledge base: 31 of 31 keep at least one hit and the top hit is unchanged, so
  the filter removed nothing the model needed. By heading, the top three hits address the question
  in 23 of 31 (judged by the author of this report from the headings only). The other 8 (for
  example repotting a peace lily, light for a spider plant) are lexical misses that exist with and
  without the threshold.
- Off-topic: 10 of 11 end with no relevant hit (the model sees top-up sections only); 1 (weather,
  3.6) passes.
- Hit counts without the limit: on-topic questions 7 - 31 hits, off-topic 0 - 4. With the top-up
  every question reaches the model with up to `chat_top_k` sections.

## Why the model call is not skipped

Cutting the turn when no hit reaches the threshold would save a model call, but the evidence does
not support it:

- Follow-up questions ("and in winter?", "why?") have no lexical match by themselves and rely on
  the earlier turns in the prompt; they would be cut as off-topic.
- One off-topic question (weather) scored 3.6, higher than 3 on-topic questions, so retrieval
  cannot separate the classes reliably enough to answer without the model.
- Skipping the model would need a stored turn with a fixed answer, which changes the quota and
  outcome semantics.

So the threshold only decides which sections count as found. The model keeps deciding between
`answered`, `not_in_kb` and `out_of_scope`, and the existing citation check still downgrades an
answer without a valid citation.

## Limits of the evidence

- 49 questions, written by one person, English only (the UI is English only, no translation).
- 15 plants of one source; a longer or different corpus shifts the scale.
- The rank grows with the number of matching question words, so a long question with several
  incidental matches can pass the threshold. A single common word cannot.
- "Right section" is judged from headings, not read by a second reviewer.
- The threshold is a starting point: change `PLANT_CARE_CHAT_MIN_RANK` and rerun the evaluation
  if the knowledge base or its language changes.
