---
name: staging-intake
description: Process files from staging into proper entities. Use when user says "process staging", "what's in staging", "intake this", "turn this into an entity", or similar. Analyzes files, infers entity types, and creates entities using scripts.
author: Nick Silhacek
version: 0.3.0
last_edited: 2026-03-06
---

# Staging Intake

Transform files into structured entities. Staging is the intake valve — everything enters here before becoming part of the system.

## Workflow

1. **Inventory** — list staging folder contents
2. **Analyze** — infer type, extract metadata, identify relationships
3. **Present** — show analysis to user for confirmation
4. **Create** — run create-entity.py for each confirmed file, move file into entity folder
5. **Report** — summarize what was created

## The Staging Folder

Location: `/staging/`

Files sit here until the user asks to process them. Sources:
- Files manually dropped by user
- Content captured by agents from conversations

## Processing Steps

### Step 1: Inventory Staging

List all files in `/staging/`. Note file names, types, sizes, dates. Report to user what was found.

### Step 2: Analyze Each File

For each staged file:

1. **Determine content type** by extension (.md, .pdf, .docx, etc.)
2. **Extract metadata** — title/name (from filename or document heading), dates
3. **Infer entity type** — compare against `_system/schema/types.yaml`. Common patterns:
   - `.md` files → usually `note`
   - `.docx`, `.pdf` → usually `document`
   - Files named `meeting-*` or `notes-*` → consider `meeting` or `note`
   - When uncertain → ask the user or default to `note`
4. **Identify potential relationships** — scan for mentions of existing entity names (use `query.py find` to check). Note as suggestions, not automatic links.

### Step 3: Present to User

Show analysis and ask for confirmation:

```
Found 3 files in staging:

1. project-proposal.docx
   → Suggested type: document
   → Mentions "Q1 Campaign" (exists as project entity)

2. ideas.md
   → Suggested type: note

3. unknown-format.xyz
   → Unable to determine type
   → Suggest: skip, or tell me what type to use

How would you like to proceed?
```

Options: confirm all, modify specific items, process one at a time, skip certain files.

### Step 4: Create Entities

For each confirmed file, use `create-entity.py`:

```bash
substrate entity create \
  --type document \
  --name "Project Proposal" \
  --description "Q1 project proposal document imported from staging." \
  --relates_to PROJECT_UUID \
  --attr source_file=project-proposal.docx
```

Then move the file into the entity folder:
```bash
mv staging/project-proposal.docx entities/document/{shard-path}/{uuid}/
```

The script output shows the created path — use it to know where to move the file.

**Description quality matters** — don't just say "imported from staging." Use the file content, filename, and conversation context to write a meaningful description. If context is truly limited: `"Document imported from staging. [awaiting context]"`

### Step 5: Report Results

```
Created 2 entities:
- "Project Proposal" (document) [abc12345]
  → relates_to Q1 Campaign
- "Ideas" (note) [def67890]

Skipped 1 file (unknown format)
```

## Handling Special Cases

**Unknown types** — ask the user. Don't guess.

**Multi-entity files** (e.g., a file containing a task list):
- Ask: "This file contains 5 items that could be individual tasks. Create them separately, or keep as one document?"
- If separate: create an entity for each, link them via `belongs_to`
- If combined: create a single entity

**Duplicate detection** — if file content matches an existing entity, notify the user. Offer to skip or create anyway.

**Unreadable files** — report to user, offer to skip. Don't delete; leave for manual handling.

## Mid-Session Organic Processing

When a staging file gets consumed mid-session (not through a formal intake pass — e.g., a file is dropped in staging, used as context, and an entity is created from it during conversation), clear the staging file immediately after the entity is created. Don't wait for a formal intake pass. The rule is the same: file moves into the entity folder, staging is left clean.

## Principles

- **Be conservative** — it's better to ask than to guess wrong
- **Don't auto-create relationships** without user confirmation
- **Suggest, don't assume** — relationship inference is a suggestion, not a command
- **Users can always retype or relink** after creation
- **Leave staging clean** — a file in staging means "not yet processed." The moment it's been processed into an entity, it should be gone from staging.
