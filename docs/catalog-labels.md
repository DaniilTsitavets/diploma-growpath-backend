# Catalog labels

The catalog filter (`GET /v1/items?label=...`) and the labels shown on each item come from two sources:

1. **Derived labels**, computed from the PlantSolve profile of the plant (open data, CC BY 4.0) by fixed rules.
2. **Hand-set labels**, which PlantSolve cannot tell: only `rare` (a commercial judgement, set in
   `services/commerce/app/seed.py`).

## How the labels reach the catalog

Each service owns its database, so plant-care cannot write commerce's labels. The flow is:

1. The plant-care import (`make import-kb`) computes the labels of each plant and stores them in
   `plant_profiles.labels`; `GET /v1/plants/{slug}` returns them as `labels`.
2. `make catalog-labels` (`python -m app.knowledge_import --write-catalog-labels PATH`) fetches the same records and
   writes the table `{plant_slug: [labels]}` to `services/commerce/app/catalog_labels.json`. It needs the internet and
   no database, writes nothing unless every plant was fetched and converted, and a rerun on unchanged data changes no
   byte. The file is committed; refresh it with the make target and commit the diff.
3. The commerce seed (`python -m app.seed`, run on every start) reads the file and syncs the labels of each item:
   missing ones are inserted and any label that is neither hand-set nor in the file is removed. A missing or malformed
   file stops the seed; a unit test checks that every pack has an entry.

A label is a lowercase-kebab word of at most 40 characters; an item carries at most 10.

## Rules

The fields are those of the PlantSolve plant record. A field that is missing or unreadable gives no label.

| Label | Rule | PlantSolve field |
|---|---|---|
| `beginner`, `intermediate`, `advanced` | the difficulty, when it is one of these three words | `plantHero.difficulty` |
| `low-maintenance` | difficulty is `beginner` and the plant tolerates missed watering (`droughtTolerance` is `medium` or `high`) | `plantHero.difficulty`, `plantHero.droughtTolerance` |
| `low-light` | the plant is documented to live in low light (`lightMin` is `low`) | `plantHero.lightMin` |
| `bright-light` | the dimmest light it lives in is bright (`lightMin` is `bright` or `full`) | `plantHero.lightMin` |
| `direct-sun` | `lightMin` is `full`, or `plantHero.light` starts with "Full sun" | `plantHero.lightMin`, `plantHero.light` |
| `drought-tolerant` | `droughtTolerance` is `high` | `plantHero.droughtTolerance` |
| `likes-moisture` | `droughtTolerance` is `low` and `drynessThreshold` is `25` (water when the top inch is dry) | `plantHero.droughtTolerance`, `plantHero.drynessThreshold` |
| `pet-safe` | `petSafety` is `safe`; when it is `unknown` or missing, both `petSafe.cat` and `petSafe.dog` are true | `plantHero.petSafety`, `petSafe` |
| `toxic-to-pets` | `petSafety` is `toxic`; when it is `unknown` or missing, `petSafe.cat` or `petSafe.dog` is false | `plantHero.petSafety`, `petSafe` |
| `humidity-lover` | recommended humidity has a lower bound of 60% or more, or an upper bound of 80% or more | `parameters.humidity.recommended`, else `plantHero.humidity` |
| `flowering` | the flag is `true` | `plantHero.flowering` |
| `compact` | the top of the first measurement in `matureSize` is at most 65 cm | `plantHero.matureSize` |
| `large` | the top of the first measurement in `matureSize` is at least 180 cm | `plantHero.matureSize` |
| `air-purifying` | the tag is present | `taxonomyTags` |
| `pollinator-friendly` | the tag is present | `taxonomyTags` |
| `fragrant` | the tag is present | `taxonomyTags` |
| `succulent` | the tag `succulent` is present | `taxonomyTags` |
| `herb` | the tag `herbs` is present | `taxonomyTags` |
| `edible` | the tag `edible-plants` is present | `taxonomyTags` |
| `hanging` | the tag `hanging-plants` or `trailing-vine` is present | `taxonomyTags` |
| `rare` | **hand-set**, never derived | none |

Notes on the rules:

- `matureSize` is free text. The first number with a unit (`cm`, `in`, `inch`, `inches`, `ft`, `feet`) is read, the
  larger end of a range is used and inches and feet are converted to centimetres; text without a measurement
  ("Varies widely") gives neither `compact` nor `large`.
- Tags are compared lowercased with spaces turned into dashes; tags outside the list above (`indoor-plants`,
  `vastu`, ...) are ignored. `taxonomyTags` is missing for some plants, so those get fewer of the last six labels.
- `bright-light` and `direct-sun` are not exclusive; a plant of medium light gets neither.
- The humidity bounds are those of the watering facts (`rh_min_pct`, `rh_max_pct`, see [watering.md](watering.md)).

## Current table

The result of the last run of `make catalog-labels` is `services/commerce/app/catalog_labels.json`; the labels an item
shows are that entry plus its hand-set labels.
