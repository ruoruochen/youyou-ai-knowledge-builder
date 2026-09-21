# youyou-ai-knowledge-builder

Build an independent, Obsidian-compatible AI knowledge base from conversation. An agent can create a fresh instance, read its instructions, manage Markdown notes, and verify isolation without needing the original setup conversation.

[中文说明](README.zh-CN.md) · [Execution instructions](SKILL.md) · [Iteration history](ITERATIONS.md)

## Use cases

- Create a personal AI knowledge base with explicit-save conversations.
- Test whether another local agent can set it up from scratch.
- Repeat a setup on the same computer while keeping existing vaults separate.

## Behavior

- Each instance has its own directory, stable UUID, runtime, notes, history, lock, and reminder state. Matching display names do not imply matching instances.
- Every note operation requires its instance root and full UUID. Wrong IDs, wrong runtimes, copied-path mismatches, and escaped/symlink paths are rejected.
- Ordinary conversation is read-only. Save only after an explicit “记下来” (save this) request.
- Projects, assets, and skills/templates are owned knowledge. Resources and the inspiration inbox contain external inputs. Owned retrieval never silently expands.
- Notes support create, list/read/search, edit, reversible archive/restore, and version rollback. Today/daily/pending navigation links saved personal content.
- Pending decisions stay distinct from adopted actions. Reminder checks deduplicate unchanged items; test instances cannot bind live scheduled jobs.

## Requirements and installation

Python 3.9+ and PyYAML are required. Some systems also need tzdata. Run doctor before creating files. Obsidian is optional for file operations; UI registration and real scheduling depend on the host agent's available tools.

Clone or download the **whole repository** into a directory the user permits:

```text
git clone https://github.com/ruoruochen/youyou-ai-knowledge-builder.git <PERMITTED_SKILL_DIRECTORY>
```

For Codex, the complete folder can be installed as `~/.codex/skills/youyou-ai-knowledge-builder`, subject to that environment's installation rules. Other agents can read SKILL.md directly; copying only that file loses required code and references.

## Example agent request

> Read this repository's SKILL.md. Create two test instances with the same display name inside my designated test directory. Pass my existing vault root as --protect. Use authorized synthetic data to verify CRUD in the first instance, keep the second empty, and verify it remains unchanged. Do not edit global rules, access the protected vault's contents, or create real scheduled jobs. Report both complete instance IDs and paths.

Use concrete permitted directories and protected roots when invoking the agent. To continue later, provide the returned root and UUID and say to resume rather than create again.

## Command entry points

From this skill directory, replace placeholders with actual allowed values:

```text
python -B scripts/builder.py doctor --timezone <IANA_TIMEZONE>
python -B scripts/builder.py create --parent <ABSOLUTE_PARENT> --name <DISPLAY_NAME> --mode test --timezone <IANA_TIMEZONE> --protect <EXISTING_VAULT_ROOT>
python -B scripts/selftest.py --work <PERMITTED_TEST_DIRECTORY> --protect <EXISTING_VAULT_ROOT>
```

The test runner retains fixtures only inside a new run under --work. Never point that work directory at a real vault.

## Verification and limits

The current runtime passed 36 regression tests and 35 integration checks on macOS with Python 3.9.6. An independent agent without the original conversation also created two same-name instances and completed CRUD while leaving the second unchanged. A relocated archive was tested by creating and reading a fresh empty instance.

Windows, Linux, and every third-party agent have not been individually verified. The guards are not an operating-system sandbox; use host filesystem restrictions for stronger protection. No cloud sync, old-vault migration, automatic Obsidian registration, or real reminder delivery is implied by successful file-level setup.

See [operating rules](references/operating-rules.md), [API](references/api.md), and [scheduling adapter](references/scheduling.md) for details. These supporting guides and the default vault labels are currently in Chinese.
