# Stratus QA Automation Platform — Documentation

Welcome. This is the home of the design and usage docs for the Stratus
BackOffice automated testing platform.

## Pick your path

| If you are… | Start here |
|---|---|
| A **Manual QA** who needs to test a screen | [Quickstart for QA](quickstart-for-qa.md) |
| A **Lead / Manager** evaluating the approach | [Architecture & Design](architecture.md) |
| An **Automation Engineer** extending the framework | [Architecture & Design](architecture.md) → §10 Pattern Engine |
| A **Developer** whose screen has a failing test | [Quickstart for QA](quickstart-for-qa.md) → "Reading a failure" |
| **New to the project entirely** | [Glossary](glossary.md), then [Architecture & Design](architecture.md) |

## What is this platform, in one paragraph

Stratus BackOffice has hundreds of screens. Hand-writing one test file per
screen does not scale and turns into a museum of broken code within a year.
This platform takes a different shape: **each screen is described in one
small YAML file by a QA person (no coding), and a small library of generic
"pattern runners" automatically generates ~25 tests per screen**, covering
UI clicks, API calls, and SQL Server database verification. When a screen
doesn't fit any standard pattern, a Python escape hatch is always available.

## Documents in this folder

- [Architecture & Design](architecture.md) — the full design, with diagrams
- [Quickstart for QA](quickstart-for-qa.md) — how to add a screen in 30 minutes
- [Glossary](glossary.md) — terms used throughout
