# Claude Code / Agent SDK trong ứng dụng web: session, stream và usage

> Trạng thái: nghiên cứu hoàn tất ngày 2026-10-04.
> Phạm vi nguồn: chỉ dùng tài liệu, cookbook và source code chính thức của Anthropic. Phần đánh giá ArcReel đối chiếu với implementation ở commit `HEAD`; working tree tại thời điểm nghiên cứu đang có thay đổi chưa commit loại bỏ Agent Runtime, nên tài liệu này không giả định các file runtime cũ vẫn còn trong checkout cuối cùng.
> Baseline ArcReel đã commit: `claude-agent-sdk==0.2.139`, Claude Code CLI 2.1.233 (theo `uv.lock` và `_cli_version.py` được ghi lại trong nghiên cứu auto-memory).

## Kết luận ngắn

Kiến trúc phù hợp nhất cho ArcReel là **hybrid session**:

1. React gửi message bằng REST có idempotency key; FastAPI trả ngay app `session_id`, `turn_id` và user entry đã được chấp nhận.
2. Một actor duy nhất sở hữu `ClaudeSDKClient` của mỗi session đang nóng và serialize `query` / `interrupt` / `disconnect`.
3. Trình duyệt chỉ nhận schema event do ArcReel sở hữu qua SSE; event bền vững có `seq`, hỗ trợ `Last-Event-ID`. Token delta chỉ là preview trong RAM, complete message và event log mới là nguồn sự thật.
4. SDK transcript được mirror vào `SessionStore` bền vững để một host khác có thể `resume`; UI event log là read model riêng, không dùng transcript làm thẳng timeline.
5. Usage được ghi theo **snapshot lũy kế của session**, rồi tính delta cho từng turn. Không cộng trực tiếp `total_cost_usd` của các `ResultMessage` sau `resume`.

Hai quyết định kiến trúc cũ của ArcReel rất mạnh và nên giữ nếu khôi phục Agent Runtime: per-session actor (ADR-0028) và tách SDK transcript khỏi UI event log có cursor (ADR-0029/0048). Hai chỗ cần sửa trước khi nâng SDK là identity của session và mô hình ghi usage.

## Những implementation chính thức đáng tham khảo

### 1. Anthropic hosting cookbook: FastAPI + SSE tối thiểu

[`claude-cookbooks/claude_agent_sdk/hosting/server.py`](https://github.com/anthropics/claude-cookbooks/blob/main/claude_agent_sdk/hosting/server.py) là ví dụ gần ArcReel nhất:

- `POST /sessions/{external_session_id}/messages` trả `EventSourceResponse`;
- server giữ map `external_session_id -> SDK session_id` vì SDK tự sinh ID;
- turn đầu học SDK ID từ `ResultMessage`, các turn sau truyền `resume=`;
- mỗi SDK message được đẩy thành SSE `message`, sau cùng có `done`, exception thành `error`;
- session ID được validate, request body có giới hạn, auth phải đặt ở gateway;
- source tự ghi rõ race của hai first-turn đồng thời: khóa chỉ lúc ghi map là chưa đủ; production phải serialize toàn bộ read-create-write hoặc dùng primitive như Redis `SET NX`.

Đây là baseline tốt để hiểu luồng, nhưng không nên copy nguyên vào production. Nó cố ý stream raw SDK types, không có durable replay, không có turn model và map session chỉ là JSON file. ArcReel cần giữ adapter event riêng, event log và per-session serialization của mình.

[Hosting guide chính thức](https://code.claude.com/docs/en/agent-sdk/hosting) mô tả Agent SDK là một subprocess system, không phải stateless HTTP SDK: một active session tương ứng một `claude` process, cwd và transcript local. HTTP/WebSocket là trách nhiệm của app host; subprocess không mở network listener. Guide phân biệt bốn pattern:

| Pattern | Vòng đời | Phù hợp |
|---|---|---|
| Ephemeral | một prompt, một process/container | batch job không follow-up |
| Long-running | process giữ session nóng | chat có lưu lượng liên tục |
| Hybrid | idle thì dừng, khi có message thì hydrate + resume | ArcReel, vì session dài nhưng thưa |
| Multi-agent container | nhiều subprocess trong một container | agent phối hợp, phải cô lập cwd/config |

### 2. Session browser cookbook: lịch sử là một product surface

[Building a Session Browser](https://platform.claude.com/cookbook/claude-agent-sdk-05-building-a-session-browser) dùng API chính thức để list session, đọc message không cần spawn agent, rename/tag/filter, fork rồi resume. Mỗi conversation được ghi thành JSONL dưới bucket suy ra từ `cwd`; `list_sessions(directory=...)`, `get_session_messages(...)` và `fork_session(...)` là primitives cho sidebar.

Mẫu này xác nhận hai điểm:

- `resume` phải dùng ID cụ thể cho multi-user/multi-session; `continue` chỉ lấy session mới nhất trong cùng directory và không an toàn làm identity của web app;
- fork tạo transcript mới và giữ session gốc. UI không nên “sửa ngược” một append-only timeline tại chỗ.

[Sessions guide](https://code.claude.com/docs/en/agent-sdk/sessions) cũng phân biệt rõ: `ClaudeSDKClient` giữ context trong cùng Python process; `resume` phục hồi một session cụ thể qua restart; session lưu conversation chứ không lưu filesystem. Artifact/cwd cần chiến lược persistence khác.

### 3. SessionStore adapters: contract bền vững cho multi-host

[Session storage guide](https://code.claude.com/docs/en/agent-sdk/session-storage) định nghĩa contract `append(key, entries)` và `load(key)`, với `list_sessions`, `list_session_summaries`, `delete`, `list_subkeys` là optional. Entry là opaque JSON; adapter phải giữ đúng thứ tự và trả lại deep-equal data. `subpath` chứa transcript của subagent/sidecar và phải được coi như opaque suffix.

[Các adapter S3, Redis và Postgres chính thức](https://github.com/anthropics/claude-agent-sdk-typescript/tree/main/examples/session-stores) cho thấy ba storage model. Với Postgres, source dùng một row JSONB cho mỗi transcript entry và một sequence tăng dần trong key `(project_key, session_id, subpath)`. Anthropic ghi rõ đây là reference implementation, không phải package production-maintained.

Các chi tiết production quan trọng từ contract:

- SDK ghi local trước rồi mirror theo batch; store là mirror, không thay thế local write path.
- `append` có thể được retry và cùng entry có thể đến lại; dedupe theo `entry.uuid`.
- sau ba lần thử thất bại, SDK phát system message `mirror_error`, bỏ batch và vẫn chạy agent; production phải alert vì resume có thể mất context.
- summary fold phải được serialize bằng transaction, compare-and-swap hoặc per-session lock.
- `listSubkeys` cần có nếu muốn resume đầy đủ subagent transcripts.
- retention là trách nhiệm của app/store.
- conformance suite chính thức nên là test gate của custom adapter.

Implementation `DbSessionStore` đã commit của ArcReel đi đúng hướng: row-per-entry, order bằng `seq`, uuid dedupe, summary fold trong transaction và lưu subpath. Đây là phần nên tái sử dụng thay vì tự viết lại theo cookbook đơn giản.

### 4. Managed Agents: tham chiếu cho event contract, không phải drop-in replacement

[Managed Agents event stream](https://platform.claude.com/docs/en/managed-agents/events-and-streaming) dùng event bền vững làm authoritative record và delta chỉ làm live preview. Client bỏ qua preview vẫn nhận được record hoàn chỉnh. Session chuyển giữa running/idle, có thể gửi tiếp `user.message` khi idle.

Đây là mô hình rất phù hợp để ArcReel học theo: **preview không phải record**. Nếu ArcReel không cần tự host CLI, Managed Agents là lựa chọn cần đánh giá riêng; còn nếu cần toolset nội bộ, cwd/project files và sandbox do ArcReel kiểm soát, self-hosted Agent SDK vẫn hợp lý hơn.

## Session lifecycle đề xuất cho ArcReel

### Identity: app ID và SDK ID phải là hai field khác nhau

Nên có một `session_id` do ArcReel sinh trước first turn và một `sdk_session_id` nullable/unique được điền khi nhận init/result:

```text
React session_id ──► agent_sessions.id
                         │
                         └── sdk_session_id ──► Agent SDK resume / transcript
```

Lý do:

- auth/ownership, idempotency, stream subscription và cancel cần identity ổn định trước khi SDK init;
- không cần key swap trong memory map;
- SDK ID không trở thành public API contract của ArcReel;
- migration sang SDK/runtime khác không đổi URL và foreign keys;
- first-turn failure có thể tồn tại như session `failed` có chẩn đoán, rồi dọn theo TTL, thay vì “không có session để hỏi lỗi”.

_Điểm này mâu thuẫn ADR-0030_, vốn chọn `sdk_session_id` làm public identity và chỉ tạo row sau init để tránh orphan. Nên mở lại ADR-0030 nếu Agent Runtime được phục hồi. “Empty orphan” có thể xử lý bằng `state=starting`, `expires_at` và cleanup; lợi ích của app-owned identity lớn hơn chi phí một row chưa có SDK ID. Cookbook chính thức cũng dùng external ID tách khỏi SDK ID.

### Tách session state và turn state

Một conversation có nhiều turn; “completed” của một turn không có nghĩa session không thể chat tiếp. Không nên dùng một status field cho cả hai.

```text
session: creating -> idle <-> active -> deleted
                          \-> degraded

turn: accepted -> running -> awaiting_input -> succeeded
                         \-> cancelled
                         \-> failed
```

Mỗi session chỉ có tối đa một turn `running|awaiting_input`. Database constraint/advisory lock bảo vệ invariant; actor queue là bảo vệ trong process. Khi scale nhiều replica, cần session affinity hoặc distributed lease để hai host không cùng resume và ghi vào một transcript.

### Warm actor + durable resume

- Session hoạt động thường xuyên: giữ một `ClaudeSDKClient` trong actor, follow-up dùng cùng client.
- Session idle: sau TTL, actor `disconnect`; transcript đã mirror vào DB.
- Message mới: lấy distributed lease, dựng actor mới với `resume=sdk_session_id` + `session_store`.
- Crash/redeploy: host khác hydrate từ store và resume cùng cwd/project key.
- `cwd`, `CLAUDE_CONFIG_DIR`, settings và auto-memory phải tách theo tenant/project. Hosting guide khuyên `setting_sources=[]`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` và config directory riêng nếu không chủ động muốn chia sẻ các nguồn đó.

Per-session `SessionActor` của ArcReel ở ADR-0028 phù hợp với mô hình này. `query`, `interrupt` và `disconnect` phải đi qua cùng command queue; không gọi `ClaudeSDKClient` từ nhiều asyncio task độc lập.

### API đề xuất

```http
POST /api/projects/{project}/agent/sessions
Idempotency-Key: <uuid>
-> 201 { session_id, state: "creating" }

POST /api/projects/{project}/agent/sessions/{session_id}/turns
Idempotency-Key: <uuid>
{ content, attachments }
-> 202 { session_id, turn_id, user_entry, stream_url }

GET /api/projects/{project}/agent/sessions/{session_id}/events?after=<seq>
Last-Event-ID: <seq>
-> text/event-stream

POST /api/projects/{project}/agent/sessions/{session_id}/turns/{turn_id}/interrupt
-> 202 { turn_id, state: "cancelling" }

GET /api/projects/{project}/agent/sessions/{session_id}
GET /api/projects/{project}/agent/sessions/{session_id}/entries?after=<seq>
DELETE /api/projects/{project}/agent/sessions/{session_id}
```

Có thể gộp create-session với first-turn để giảm round trip, nhưng response vẫn phải trả app `session_id` ngay. Idempotency key phải unique trong `(user_id, project_id, operation)`; không chỉ `(sdk_session_id, key)` vì lúc first turn SDK ID chưa tồn tại.

## Streaming message đề xuất

### SDK stream có hai lớp

[Streaming output guide](https://code.claude.com/docs/en/agent-sdk/streaming-output) mô tả:

- mặc định SDK phát complete `AssistantMessage` cho mỗi non-empty content block và cuối cùng `ResultMessage`;
- bật `include_partial_messages=True` sẽ thêm raw `StreamEvent`;
- text token nằm ở `content_block_delta` + `text_delta`;
- tool JSON input đến thành nhiều `input_json_delta.partial_json`, chỉ parse khi block stop;
- một API response có thể phát nhiều `AssistantMessage` cùng `message_id`;
- partial stream chỉ thuộc main session; subagent token-level delta không được forward, complete message mới có `parent_tool_use_id` để gắn vào subagent;
- event order là stream start/deltas, complete block message, block stop, tool execution/turn tiếp theo, rồi final result.

Vì vậy browser không nên nhận raw SDK union. SDK thêm message type theo phiên bản; raw payload làm frontend coupling với cả SDK và CLI.

### Event envelope do ArcReel sở hữu

```json
{
  "schema_version": 1,
  "session_id": "ses_...",
  "turn_id": "turn_...",
  "seq": 184,
  "event_id": "evt_...",
  "type": "assistant.completed",
  "occurred_at": "2026-10-04T10:00:00Z",
  "payload": {}
}
```

Tập event tối thiểu:

| Event | Bền vững | Mục đích |
|---|---:|---|
| `turn.accepted` | có | user message authoritative, idempotent |
| `turn.started` | có | lifecycle |
| `assistant.preview.delta` | không | typewriter UI; mất khi crash là chấp nhận được |
| `assistant.completed` | có | text/thinking block hoàn chỉnh |
| `tool.started` | có | tool ID, name, parent/subagent |
| `tool.completed` / `tool.failed` | có | result đã scrub |
| `approval.requested` / `input.requested` | có | pause có thể phục hồi |
| `usage.snapshot` | có | snapshot cuối turn hoặc checkpoint đáng tin cậy |
| `turn.completed` / `turn.failed` / `turn.cancelled` | có | terminal record |
| `runtime.warning` | có | ví dụ `mirror_error` |

Preview delta nên có `draft_id`/`message_id` và ordinal nội bộ, nhưng không cần chiếm durable `seq`. Khi `assistant.completed` đến, UI thay draft đúng identity, không so text để dedupe. Tool input delta là untrusted/incomplete JSON; không render như argument authoritative trước block stop.

### SSE replay protocol

Mẫu tốt nhất của ArcReel đã commit là event log append-only với `(session_id, seq)`, `SSE id = seq`, `Last-Event-ID` ưu tiên hơn query cursor, và một subscription barrier để không có khe giữa DB replay với live broadcast. Nên giữ nguyên nguyên tắc ADR-0048:

1. subscribe live trước;
2. đọc DB entries `seq > cursor`;
3. phát replay;
4. phát live entries, bỏ entry có `seq <= last_sent_seq`;
5. heartbeat chỉ giữ connection, không thay đổi projection;
6. overflow làm đóng stream để client reconnect/replay, không silently drop event critical.

POST message và GET SSE nên tách rời. Streaming trực tiếp response của POST đơn giản như cookbook nhưng reconnect, multi-tab, background execution và recover-after-refresh khó hơn nhiều.

Frontend reducer phải là pure projection `(snapshot, ordered events) -> view`. Sau reconnect, complete entry từ DB thay preview trong RAM. Không lưu từng token delta vào DB vì write amplification rất lớn.

## Token và cost accounting đúng

### Ý nghĩa của từng field

[Cost tracking guide](https://code.claude.com/docs/en/agent-sdk/cost-tracking) là nguồn sự thật hiện tại:

| Field | Scope | Dùng cho |
|---|---|---|
| `AssistantMessage.usage` + `message_id` | một model step; có thể lặp cùng ID | input/cache diagnostics; dedupe theo message ID |
| `ResultMessage.usage` | main loop của turn/call | output token cuối cùng của main loop |
| `ResultMessage.model_usage` | lũy kế theo model, gồm subagent | breakdown tốt nhất mà Agent SDK cung cấp |
| `ResultMessage.total_cost_usd` | estimate lũy kế | budget/telemetry gần đúng |
| Usage & Cost API / Console | billing authority | đối soát hóa đơn |

Các quy tắc dễ sai:

- Parallel tool calls có thể tạo nhiều `AssistantMessage` cùng API message ID và cùng usage; chỉ đếm một lần.
- `AssistantMessage.output_tokens` là placeholder ở `message_start`; output thực đọc từ result hoặc `message_delta.usage` khi preview.
- `ResultMessage.usage` không gồm subagent. Muốn whole-tree thì dùng `model_usage`/`total_cost_usd`.
- Cache creation/read tokens phải giữ thành cột riêng; không chỉ cộng vào `input_tokens` rồi mất cấu phần giá.
- Success và error result đều có thể đã tốn tiền, phải ghi cả hai.
- `error_max_budget_usd`: `usage` có thể bỏ response vượt ngưỡng nhưng `total_cost_usd`/`model_usage` có tính response đó.
- crash `error_during_execution` có thể trả các field bằng 0. Khi đó chỉ có thể fallback từ result trước hoặc dedup assistant usage; output/subagent/USD có thể không phục hồi đầy đủ.

`total_cost_usd` và `model_usage[*].costUSD` là **client-side estimate** từ bảng giá bundle theo SDK/CLI, không phải số billing authoritative. Pricing đổi, model lạ hoặc billing rule ngoài client có thể làm lệch. Không dùng các số này để xuất hóa đơn cho user; cần reconciliation bằng Usage and Cost API/Console nếu có billing thật.

### Breaking semantic khi nâng Claude Code

Từ Claude Code 2.1.277, khi `resume` hoặc fork, total/cost usage cũ được restore từ transcript. Result mới là **cumulative total của toàn session**, không phải chỉ riêng lần `query()` mới. Cộng các result sẽ double-count. Trước 2.1.277, resumed process bắt đầu total từ 0 nên mỗi call gần với per-call cost.

ArcReel đã commit đang pin CLI 2.1.233. Logic cũ ghi một `api_calls` row cho mỗi result nhưng lấy thẳng `total_cost_usd`; nó chỉ đúng theo semantic cũ. Nâng SDK/CLI mà không migration sẽ làm usage page tăng lũy tiến sai.

Ngoài ra, [cost guide](https://code.claude.com/docs/en/agent-sdk/cost-tracking) ghi `modelUsage.costBasis` cần Claude Code >=2.1.246 và data-residency multiplier trong Python cần Agent SDK >=0.2.144. Baseline ArcReel 0.2.139/CLI 2.1.233 chưa có các guarantee đó. Upgrade phải pin cả SDK và bundled CLI, chạy characterization test bằng transcript cố định, không chỉ dựa semver của Python package.

### Mô hình ghi đề xuất: cumulative snapshot + delta

Không nên coi một agent turn là một provider API call. Một turn có nhiều model steps, subagents và tool rounds; SDK không cung cấp toàn bộ chúng như các hàng provider-call độc lập. Điều này xung đột với ADR-0076 nếu `api_calls` được định nghĩa nghiêm ngặt là “một request thật tới provider”. Nên thêm ledger riêng cho agent run/turn, rồi usage page hợp nhất ở read model.

```text
agent_usage_snapshots
  id
  session_id                 FK app session
  sdk_session_id
  turn_id                    UNIQUE
  accounting_epoch           increment sau /clear hoặc reset
  result_subtype
  terminal_reason
  main_loop_usage_json
  model_usage_json
  cumulative_cost_usd_estimate
  previous_snapshot_id
  turn_cost_usd_estimate     cumulative - previous cumulative trong cùng epoch
  input_tokens
  output_tokens
  cache_creation_input_tokens
  cache_read_input_tokens
  sdk_version
  cli_version
  pricing_basis
  raw_result_json
  started_at / finished_at / duration_ms / duration_api_ms
```

Thuật toán settle một turn:

1. lock row session/accounting cursor;
2. persist raw result kể cả error;
3. xác định epoch và snapshot trước;
4. với mỗi model, tính delta trên `inputTokens`, `outputTokens`, cache fields và `costUSD`;
5. nếu counter giảm, model key thay đổi bất thường hoặc version semantic không biết, đánh dấu `accounting_state=needs_reconciliation` thay vì clamp âm rồi giả vờ chính xác;
6. lưu cumulative snapshot và derived turn delta trong cùng transaction;
7. emit `usage.snapshot` event;
8. update usage read model.

`/clear`, conversation reset hoặc một session mới phải mở `accounting_epoch` mới. Ghi version vào row để migration biết semantic nào đang áp dụng. Không cộng `ResultMessage.usage` với `model_usage`: chúng là hai view chồng lấp, không phải hai khoản chi.

Nếu vẫn cần dùng `api_calls`, chỉ nên ghi một aggregate row có `purpose=assistant_turn`, đánh dấu rõ `unit=agent_turn_aggregate`, và lưu `turn_cost` đã delta; không gọi nó là provider request. Tốt hơn là giữ `api_calls` đúng nghĩa và dùng bảng `agent_usage_snapshots` riêng.

### Budget và guardrail

- `max_budget_usd` là fail-safe, không phải quota tuyệt đối: [official example](https://github.com/anthropics/claude-agent-sdk-python/blob/main/examples/max_budget_usd.py) ghi budget được kiểm tra sau mỗi API call và có thể vượt tối đa thêm một call.
- đặt `max_turns` cho main agent và từng subagent;
- admission trước turn dựa trên quota do ArcReel quản lý;
- cancel/interrupt khi user yêu cầu nhưng vẫn settle result cuối nếu có;
- alert khi không có trustworthy result sau crash;
- giới hạn concurrency theo user/project và tài nguyên subprocess, không chỉ theo request count HTTP.

## Data model tổng thể

```text
agent_sessions
  id (app-owned PK)
  sdk_session_id (nullable unique)
  user_id / project_id
  state
  cwd_key / config_key
  lease_owner / lease_expires_at
  accounting_epoch
  last_event_seq
  created_at / last_activity_at / deleted_at

agent_turns
  id
  session_id
  idempotency_key
  user_entry_id
  state
  result_subtype / terminal_reason / error_json
  accepted_at / started_at / finished_at

agent_session_entries
  project_key / sdk_session_id / subpath / seq
  uuid / payload / mtime
  -- SDK transcript, opaque, dùng cho resume

agent_session_event_log
  session_id / seq
  event_id / turn_id / type / payload
  client_key / created_at
  -- UI read source, app-owned schema

agent_usage_snapshots
  session_id / turn_id / accounting_epoch
  cumulative fields / delta fields / versions / raw result
```

Constraints cần có:

- unique `(session_id, idempotency_key)` cho turn thường và unique `(user_id, project_id, idempotency_key)` cho create+first-turn;
- unique nullable `sdk_session_id`;
- unique `(session_id, seq)` cho event log;
- unique `(project_key, sdk_session_id, subpath, uuid)` khi uuid khác null cho transcript dedupe;
- tối đa một active turn/session bằng partial unique index hoặc lease transaction;
- FK ownership phải được kiểm tra trước mọi get/stream/interrupt/delete.

## Đánh giá implementation ArcReel đã commit

### Phần nên giữ

- `SessionActor` độc quyền client và serialize tất cả protocol calls: đúng với subprocess/session model chính thức.
- idle eviction + resume từ DB transcript: đúng hybrid pattern.
- `DbSessionStore`: uuid dedupe, subpath, transaction summary fold và ordered load đều bám sát contract mới.
- UI event log riêng với monotonic seq, POST idempotency key, `Last-Event-ID` và replay barrier: production-grade hơn cookbook.
- draft preview trong memory, complete entry thay theo `message_id`: cùng nguyên tắc với Managed Agents.
- credential inject theo session thay vì global process env và cwd/profile isolation: đúng hướng multi-tenant.

### Phần cần đổi

1. **Session identity:** thay SDK ID public + key swap bằng app ID + nullable SDK ID; mở lại ADR-0030.
2. **Usage unit:** không backfill mỗi SDK result như một provider call với raw cumulative total. Thêm usage snapshot/delta ledger; mở lại cách áp dụng ADR-0076 cho assistant.
3. **Token columns:** giữ raw input, output, cache-create, cache-read tách biệt. Logic cũ cộng cache vào `input_tokens`, làm mất pricing semantics.
4. **Version-aware accounting:** test cả semantic trước/sau CLI 2.1.277; record SDK/CLI version trên usage row.
5. **Raw SDK event boundary:** tiếp tục normalize ở server; không để frontend switch trực tiếp trên SDK dataclass names.
6. **Distributed ownership:** in-process actor/lock là đủ cho một FastAPI process, chưa đủ cho nhiều replica. Thêm lease hoặc sticky router trước khi scale ngang.
7. **Mirror durability:** `mirror_error` phải thành runtime warning/metric và session `degraded`; không chỉ log.
8. **Crash accounting:** raw result, prior snapshots và assistant step IDs phải còn để recovery/reconciliation.

## Lộ trình triển khai ít rủi ro

1. Freeze/pin SDK + CLI, thêm characterization fixtures cho init, text/tool stream, subagent, interrupt, error, resume và cost cumulative.
2. Chốt app-owned session/turn schema và migration; nếu giữ ADR-0030 thì phải ghi rõ vì sao chấp nhận first-turn identity gap.
3. Tái dùng/khôi phục `DbSessionStore` và chạy SDK conformance suite bên cạnh unit/integration test của ArcReel.
4. Xây actor + lease lifecycle trước, chưa bật partial delta.
5. Xây durable event log + replay; frontend chỉ render app events.
6. Thêm partial text/tool preview, giới hạn queue và drop chỉ preview khi backpressure.
7. Thêm usage snapshot/delta và reconciliation; sau đó mới hiện token/cost ở UI.
8. Cuối cùng mới bật subagent fan-out, approval/input và multi-replica routing.

## Nguồn chính

- [Claude Agent SDK: Hosting](https://code.claude.com/docs/en/agent-sdk/hosting)
- [Claude Agent SDK: Sessions](https://code.claude.com/docs/en/agent-sdk/sessions)
- [Claude Agent SDK: Session storage](https://code.claude.com/docs/en/agent-sdk/session-storage)
- [Claude Agent SDK: Streaming output](https://code.claude.com/docs/en/agent-sdk/streaming-output)
- [Claude Agent SDK: Cost tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
- [Claude Cookbook: Hosting your agent](https://platform.claude.com/cookbook/claude-agent-sdk-07-hosting-the-agent)
- [Claude Cookbook: Building a Session Browser](https://platform.claude.com/cookbook/claude-agent-sdk-05-building-a-session-browser)
- [Official FastAPI + SSE hosting example](https://github.com/anthropics/claude-cookbooks/blob/main/claude_agent_sdk/hosting/server.py)
- [Official SessionStore reference adapters](https://github.com/anthropics/claude-agent-sdk-typescript/tree/main/examples/session-stores)
- [Official Python budget example](https://github.com/anthropics/claude-agent-sdk-python/blob/main/examples/max_budget_usd.py)
- [Managed Agents: Events and streaming](https://platform.claude.com/docs/en/managed-agents/events-and-streaming)
- [Claude Agent SDK Python changelog](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md)
