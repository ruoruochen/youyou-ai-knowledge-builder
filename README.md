# youyou-ai-knowledge-builder

Build an independent, Obsidian-compatible knowledge base through AI conversation. Say “记下来” (save this) to save selected knowledge; the agent can then retrieve, connect, edit, and reversibly archive it.

[中文说明](README.zh-CN.md) · [Execution instructions](SKILL.md) · [Iteration history](ITERATIONS.md)

## Start with one sentence

Send this to Cola or another agent with local file and command tools:

> Use this skill to build my own AI knowledge base: https://github.com/ruoruochen/youyou-ai-knowledge-builder

The agent reads the complete repository, checks dependencies, chooses a permitted persistent directory, protects known existing vaults, and creates **one empty daily-use instance**. You do not need to provide UUIDs, command flags, test mode, or an acceptance plan. If no persistent directory is authorized, the host may request directory access.

Cola's Projects documentation describes file editing and command execution. This workflow reads and runs the repository; it does not imply installation from the Cola skill marketplace or a completed Cola device test. See [official Projects documentation](https://docs.colaos.ai/zh/coding-cola/) and the [Cola adapter](references/cola.md).

## Everyday use

- “记下来：…” — save only the content you explicitly request.
- “Find my earlier thoughts about …” — search owned knowledge.
- “Edit this note …”, “Archive this”, “Restore this” — manage selected notes.
- “Continue using this knowledge base” — resume the verified instance in the same conversation.
- “Create another knowledge base” — create a separate instance.

For a new conversation or agent, provide the generated **handoff.json** and ask it to continue. The agent reads the full root and UUID from that file. If several candidates exist, it asks which one; it never guesses from a name or timestamp.

## Behavior

- Ordinary discussion is read-only. Explicit save intent authorizes a new note; editing, archiving, and restoring authorize only the requested action.
- Projects, assets, and skills/templates are owned knowledge. Resources and the inspiration inbox are external. Owned retrieval never silently expands.
- Notes support create, list/read/search, edit, reversible archive/restore, and version rollback.
- Today/daily/pending navigation links saved personal content. Pending decisions stay distinct from adopted actions.
- Each instance has its own directory, stable UUID, runtime, notes, history, lock, and reminder state.
- The agent supplies root + full UUID for every operation. Wrong IDs, runtimes, copied-path mismatches, and escaped/symlink paths are rejected.
- Setup does not import old notes, alter global routing, or register real scheduled jobs. It verifies an empty instance without adding sample notes.

## Optional agent testing

Ask separately:

> Use this skill to run an isolation acceptance test and verify that same-name knowledge bases do not affect each other.

Only that testing workflow creates synthetic fixtures and runs multiple instances. See [testing details](references/testing.md).

## Requirements and installation

The agent needs local file access, command execution, Python 3.9+, and PyYAML; some systems also need tzdata. It checks the environment first and resolves dependencies only in a permitted environment.

Clone/download the **whole repository** into a user-permitted directory. Codex can install the complete folder at `~/.codex/skills/youyou-ai-knowledge-builder` under its installation rules; other agents can read SKILL.md directly. Copying only the entrypoint loses required code and references. Supporting guides and default vault labels are in Chinese.

## Verification and limits

The runtime passed 36 regression tests and 35 integration checks on macOS with Python 3.9.6. An earlier independent agent without the original conversation also completed same-name CRUD/isolation acceptance. These results do not establish Cola, Windows, Linux, or all-agent compatibility.

The guards are not an operating-system sandbox. Stronger isolation requires host filesystem restrictions. Successful file setup does not imply Obsidian UI registration, cloud sync, migration, or delivered reminders.

See [operating rules](references/operating-rules.md), [API](references/api.md), and [scheduling](references/scheduling.md).
