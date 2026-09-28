# AiTao — Command Reference

> Auto-generated from the CLI by `./aitao.sh docs`. Do not edit by hand.

All commands are invoked through `./aitao.sh <command>`. Append `--help` (or `help`) to any command for its built-in help.

## Contents

- [Top-level commands](#top-level-commands)
- [`api`](#api) — API server management
- [`config`](#config) — AiTao configuration management.
- [`db`](#db) — LanceDB management (semantic vector database).
- [`extract`](#extract) — Extract and preview text from documents
- [`index`](#index) — Document indexing pipeline.
- [`license`](#license) — Manage your AiTao Premium license.
- [`lifecycle`](#lifecycle) — Service lifecycle management
- [`mcp`](#mcp) — Model Context Protocol (MCP) server — expose AiTao tools to AI assistants.
- [`models`](#models) — Model management commands
- [`ms`](#ms) — Meilisearch management (full-text search engine).
- [`queue`](#queue) — Document processing queue.
- [`scan`](#scan) — Scan configured folders and discover new files.
- [`search`](#search) — Hybrid search across your documents (text + semantic).
- [`worker`](#worker) — Background document processing worker.

## Top-level commands

### `dashboard`

Show AiTao dashboard — all services, models, index and errors at a glance.

```
./aitao.sh dashboard
```

### `docs`

Generate the CLI command reference (docs/COMMANDS.md) from the live CLI.

```
./aitao.sh docs [options]
```

- `--output`, `-o` — Output Markdown path

### `init`

Configure AiTao for the first time — interactive wizard.

```
./aitao.sh init
```

### `restart`

Restart core AiTao services (Meilisearch, Worker, API).

```
./aitao.sh restart [options]
```

- `--verbose`, `-v` — Verbose output

### `start`

Start core AiTao services (Meilisearch, Worker, API).

```
./aitao.sh start [options]
```

- `--verbose`, `-v` — Verbose output

### `status`

Show AiTao system status.

```
./aitao.sh status
```

### `stop`

Stop core AiTao services (Worker, Meilisearch, API).

```
./aitao.sh stop [options]
```

- `--verbose`, `-v` — Verbose output

### `test`

Run unit tests.

```
./aitao.sh test [options]
```

- `--verbose`, `-v` — Verbose output

### `version`

Show AiTao version.

```
./aitao.sh version
```

## `api`

API server management

### `api help`

Show this help message and exit.

```
./aitao.sh api help
```

### `api start`

Start the API server.

```
./aitao.sh api start [options]
```

- `--skip-pull` — Skip automatic model downloads

### `api status`

Show API server status.

```
./aitao.sh api status
```

### `api stop`

Stop the API server.

```
./aitao.sh api stop
```

## `config`

AiTao configuration management.

### `config edit`

Open config file in default editor.

```
./aitao.sh config edit
```

### `config help`

Show this help message and exit.

```
./aitao.sh config help
```

### `config show`

Show current configuration.

```
./aitao.sh config show [section]
```

- `section` — argument *(optional)*

### `config validate`

Validate configuration file.

```
./aitao.sh config validate
```

## `db`

LanceDB management (semantic vector database).

### `db clear`

Clear all documents from the database.

```
./aitao.sh db clear [options]
```

- `--yes`, `-y` — Skip confirmation

### `db help`

Show this help message and exit.

```
./aitao.sh db help
```

### `db search`

Perform a semantic search in the database.

```
./aitao.sh db search <query> [options]
```

- `query` — argument
- `--limit`, `-n` — Number of results

### `db stats`

Show detailed database statistics.

```
./aitao.sh db stats
```

### `db status`

Show LanceDB status.

```
./aitao.sh db status
```

## `extract`

Extract and preview text from documents

### `extract batch`

Extract text from multiple files in a directory.

```
./aitao.sh extract batch <directory> [options]
```

- `directory` — argument
- `--recursive`, `-r` — Scan recursively
- `--limit`, `-l` — Maximum files to process

### `extract file`

Extract text from a single file.

```
./aitao.sh extract file <file_path> [options]
```

- `file_path` — argument
- `--preview`, `-p` — Characters to preview (0=full)
- `--metadata`, `-m` — Show metadata only
- `--json`, `-j` — Output as JSON

### `extract help`

Show this help message and exit.

```
./aitao.sh extract help
```

### `extract test`

Run a quick test of the extraction system.

```
./aitao.sh extract test
```

### `extract types`

Show all supported file types for extraction.

```
./aitao.sh extract types
```

## `index`

Document indexing pipeline.

### `index batch`

Index all supported files in a directory.

```
./aitao.sh index batch <directory> [options]
```

- `directory` — argument
- `--recursive`, `-r` — Scan subdirectories
- `--force`, `-f` — Re-index existing documents
- `--limit`, `-l` — Maximum files to process (0=unlimited)

### `index delete`

Delete a document from both search indexes.

```
./aitao.sh index delete <file_path> [options]
```

- `file_path` — argument
- `--yes`, `-y` — Skip confirmation

### `index file`

Index a single file into LanceDB and Meilisearch.

```
./aitao.sh index file <file_path> [options]
```

- `file_path` — argument
- `--force`, `-f` — Re-index even if exists
- `--json`, `-j` — Output as JSON

### `index help`

Show this help message and exit.

```
./aitao.sh index help
```

### `index reindex`

Re-index every document already in the index (force rebuild).

```
./aitao.sh index reindex [options]
```

- `--yes`, `-y` — Skip confirmation

### `index status`

Show indexing statistics from both databases.

```
./aitao.sh index status
```

### `index test`

Run a quick test of the indexing pipeline.

```
./aitao.sh index test
```

## `license`

Manage your AiTao Premium license.

### `license activate`

Activate a Premium license.

```
./aitao.sh license activate <key_or_file>
```

- `key_or_file` — argument

### `license deactivate`

Remove the installed license (reverts to Core edition).

```
./aitao.sh license deactivate
```

### `license help`

Show this help message and exit.

```
./aitao.sh license help
```

### `license status`

Show the status of the installed license.

```
./aitao.sh license status
```

## `lifecycle`

Service lifecycle management

### `lifecycle restart`

Restart all AiTao services.

```
./aitao.sh lifecycle restart [options]
```

- `--verbose`, `-v` — Verbose output
- `--skip-scan` — Skip initial filesystem scan

### `lifecycle start`

Start all AiTao services.

```
./aitao.sh lifecycle start [options]
```

- `--verbose`, `-v` — Verbose output
- `--skip-scan` — Skip initial filesystem scan

### `lifecycle status`

Show status of all AiTao services.

```
./aitao.sh lifecycle status
```

### `lifecycle stop`

Stop all AiTao services.

```
./aitao.sh lifecycle stop [options]
```

- `--verbose`, `-v` — Verbose output

## `mcp`

Model Context Protocol (MCP) server — expose AiTao tools to AI assistants.

### `mcp config`

Print a Claude Desktop or VS Code MCP configuration snippet.

```
./aitao.sh mcp config [options]
```

- `--transport`, `-t` — Transport: stdio | sse
- `--port`, `-p` — Port (SSE only).

### `mcp help`

Show this help message and exit.

```
./aitao.sh mcp help
```

### `mcp serve`

Start the AiTao MCP server.

```
./aitao.sh mcp serve [options]
```

- `--transport`, `-t` — Transport protocol: stdio | sse | http
- `--host`, `-H` — Bind address (SSE / HTTP only).
- `--port`, `-p` — Port to listen on (SSE / HTTP only).
- `--daemon`, `-d` — Run in background (SSE / HTTP only). Returns the terminal immediately.

### `mcp status`

Check if the AiTao MCP server (SSE/HTTP) is running.

```
./aitao.sh mcp status [options]
```

- `--host`, `-H` — SSE server host to probe.
- `--port`, `-p` — SSE server port to probe.

### `mcp stop`

Stop the background MCP server (SSE / HTTP daemon).

```
./aitao.sh mcp stop
```

## `models`

Model management commands

### `models add`

Add a model — use `ollama pull` directly.

```
./aitao.sh models add
```

### `models check`

Check all installed models for template issues.

```
./aitao.sh models check
```

### `models fix`

Fix broken model templates.

```
./aitao.sh models fix [model] [options]
```

- `model` — argument *(optional)*
- `--validate` — Run validation test after fixing

### `models help`

Show this help message and exit.

```
./aitao.sh models help
```

### `models pull`

Download a model — use `ollama pull` directly.

```
./aitao.sh models pull
```

### `models remove`

Remove a model — use `ollama rm` directly.

```
./aitao.sh models remove
```

### `models status`

Show models available in Ollama and the configured default.

```
./aitao.sh models status
```

### `models validate`

Validate models by testing them with a prompt.

```
./aitao.sh models validate [model]
```

- `model` — argument *(optional)*

## `ms`

Meilisearch management (full-text search engine).

### `ms help`

Show this help message and exit.

```
./aitao.sh ms help
```

### `ms prune`

Delete every Meilisearch index except the canonical one (US-19).

```
./aitao.sh ms prune [options]
```

- `--yes`, `-y` — Skip confirmation

### `ms rebuild`

Rebuild the search index from scratch.

```
./aitao.sh ms rebuild [options]
```

- `--yes`, `-y` — Skip confirmation

### `ms restart`

Restart Meilisearch server.

```
./aitao.sh ms restart
```

### `ms start`

Start Meilisearch server via brew services.

```
./aitao.sh ms start
```

### `ms status`

Show Meilisearch server status.

```
./aitao.sh ms status
```

### `ms stop`

Stop Meilisearch server via brew services.

```
./aitao.sh ms stop
```

### `ms upgrade`

Upgrade Meilisearch to latest version.

```
./aitao.sh ms upgrade [options]
```

- `--yes`, `-y` — Skip confirmation

## `queue`

Document processing queue.

### `queue add`

Add a file to the processing queue.

```
./aitao.sh queue add <file_path> [options]
```

- `file_path` — argument
- `--type`, `-t` — Task type (index, ocr, translate)
- `--priority`, `-p` — Priority (high, normal, low)

### `queue cancel`

Cancel a pending task.

```
./aitao.sh queue cancel <task_id>
```

- `task_id` — argument

### `queue clear`

Clear completed tasks from the queue.

```
./aitao.sh queue clear [options]
```

- `--all`, `-a` — Clear all tasks (not just completed)
- `--force`, `-f` — Skip confirmation

### `queue failures`

List files that persistently failed to index (with reason and retry count).

```
./aitao.sh queue failures [options]
```

- `--limit`, `-n` — Maximum rows to show
- `--given-up`, `-g` — Only files that exhausted their retries

### `queue help`

Show this help message and exit.

```
./aitao.sh queue help
```

### `queue info`

Show detailed information about a task.

```
./aitao.sh queue info <task_id>
```

- `task_id` — argument

### `queue list`

List tasks in the queue.

```
./aitao.sh queue list [status_arg] [options]
```

- `status_arg` — argument *(optional)*
- `--pending`, `-p` — Show only pending tasks
- `--completed`, `-c` — Show only completed tasks
- `--failed`, `-f` — Show only failed tasks
- `--limit`, `-n` — Maximum number of tasks to show

### `queue retry`

Retry failed tasks (up to max retries).

```
./aitao.sh queue retry
```

### `queue status`

Show queue status, including the file(s) currently being indexed.

```
./aitao.sh queue status [options]
```

- `--watch`, `-w` — Live view — refresh until Ctrl-C

## `scan`

Scan configured folders and discover new files.

### `scan clear`

Clear scanner state (force full rescan on next run).

```
./aitao.sh scan clear [options]
```

- `--yes`, `-y` — Skip confirmation

### `scan help`

Show this help message and exit.

```
./aitao.sh scan help
```

### `scan paths`

Show configured scan paths.

```
./aitao.sh scan paths
```

### `scan reindex`

Re-index documents present in Meilisearch but missing from LanceDB.

```
./aitao.sh scan reindex [options]
```

- `--missing-vectors` — Re-queue documents present in Meilisearch but missing from LanceDB vectors
- `--dry-run`, `-n` — Show what would be re-queued without submitting tasks

### `scan run`

Scan filesystem for new and modified documents, and queue them for indexing.

```
./aitao.sh scan run [paths] [options]
```

- `paths` — argument *(optional)*
- `--no-hash` — Skip SHA256 hash computation (faster)
- `--dry-run`, `-n` — Scan but don't update state

### `scan status`

Show scanner state and statistics.

```
./aitao.sh scan status
```

## `search`

Hybrid search across your documents (text + semantic).

### `search help`

Show this help message and exit.

```
./aitao.sh search help
```

### `search modes`

Explain available search modes.

```
./aitao.sh search modes
```

### `search run`

Perform hybrid search across indexed documents.

```
./aitao.sh search run <query> [options]
```

- `query` — argument
- `--limit`, `-l` — Maximum results
- `--mode`, `-m` — Search mode: hybrid, semantic, fulltext
- `--category`, `-c` — Filter by category
- `--language`, `-L` — Filter by language (en, fr, zh, etc.)
- `--path`, `-p` — Filter by path substring
- `--verbose`, `-v` — Show detailed output with scores

### `search test`

Run a quick search test with sample queries.

```
./aitao.sh search test
```

## `worker`

Background document processing worker.

### `worker help`

Show this help message and exit.

```
./aitao.sh worker help
```

### `worker logs`

Show worker logs.

```
./aitao.sh worker logs [options]
```

- `--lines`, `-n` — Number of lines to show
- `--follow`, `-f` — Follow log output

### `worker restart`

Restart the background worker.

```
./aitao.sh worker restart
```

### `worker run-once`

Process one task from the queue (for testing).

```
./aitao.sh worker run-once
```

### `worker start`

Start the background worker.

```
./aitao.sh worker start [options]
```

- `--foreground`, `-f` — Run in foreground (blocking)

### `worker status`

Show worker status.

```
./aitao.sh worker status
```

### `worker stop`

Stop the background worker.

```
./aitao.sh worker stop [options]
```

- `--force`, `-f` — Force kill if not responding
