# Task 12 Report: `mcp_list_resources` and `mcp_read_resource` client nodes

**Status:** DONE
**Commit:** ef531d6
**Branch:** feat/node-expansion-plan

## Test summary

2 new tests added and passing; full suite 50/50 passed, 0 failures.

## What was done

1. Added `list_resources` and `read_resource` methods to `_LoopbackSession` in `apps/api/tests/test_mcp_server.py` (after `call_tool`).
2. Added `test_mcp_list_resources_loopback` and `test_mcp_read_resource_loopback` test functions at the end of the file — confirmed failing before implementation.
3. Added `mcp_list_resources` and `mcp_read_resource` `@node`-decorated async functions to `packages/nodes/noodle_nodes/ai_v2/mcp.py` after `mcp_list_tools`.
4. Both new tests pass; no regressions in the existing 48 tests.

## Concerns

None.
