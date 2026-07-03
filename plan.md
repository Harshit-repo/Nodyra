# nodyra Node Expansion Plan

## Roadmap overview

This plan covers six workstreams, ordered by impact:

| # | Workstream | Nodes | Complexity | Priority |
|---|---|---|---|---|
| 1 | Voice Agent Platform | 8 nodes + 1 WS infra | High | P0 (user ask) |
| 2 | Voice AI Service Integrations | 4 providers (Vapi, Retell, Bland, ElevenLabs) | Medium | P1 |
| 3 | Twilio Media Streams (WebSocket) | 2 nodes + WS infra upgrade | High | P1 |
| 4 | New Trigger Nodes | 7 triggers | Medium | P2 |
| 5 | New AI / LLM Nodes | 12 nodes | Low–Med | P2 |
| 6 | Python Native Package Nodes | 10 nodes | Medium | P3 |

Total: **43 new nodes**, **1 WebSocket infrastructure change**, **4 provider integrations**.

> **Status snapshot (2026-06-19):** A significant body of work has landed *outside* this plan. See [§ Already Built](#already-built-out-of-plan) at the end of this document. None of the six workstreams above are started yet. Phase 0 (security prerequisite — SEC-1 ProviderTransport SSRF) **must ship before** any Workstream 2 nodes reach production; see Implementation Strategy below.

---

## Workstream 1 — Voice Agent Platform

### Architecture

```
Twilio Voice Call Trigger → [optional: Whisper Transcribe*] → AI Chat Model
                                                                  ↓
Twilio Voice Respond ← OpenAI TTS* ← [optional: Voice Gather DTMF/Speech]
                                                     ↑
                                              (loops back for multi-turn)
```

\* `openai_whisper_transcribe` and `openai_tts` already exist.

### Node 1.1 — Twilio Voice Call Trigger

**ID:** `twilio_voice_call_trigger`  
**Role:** `trigger`  
**Category:** `Communication` (new) or `Triggers`  
**Icon:** `brand:twilio`

**How it works:**

This is a **provider trigger** (following the `ProviderTriggerSpec` pattern from `integrations_v2`). When a workflow is published, the trigger registers a Twilio webhook URL for incoming voice calls. Twilio POSTs call metadata to nodyra's callback URL on every inbound call.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | Twilio Account SID + Auth Token (reuses existing `twilio_sms` credential type) |
| `twilio_phone_number` | string | required | The Twilio phone number (E.164) to listen on. Triggers a Twilio API lookup to find the phone number SID, then updates the voice URL. |
| `initial_greeting` | string | `""` | Text or TwiML spoken when the call is answered. If empty, uses the downstream `Twilio Voice Respond` node output. |
| `gather_speech` | boolean | `true` | Whether to enable speech/DTMF input collection after the greeting. |
| `speech_timeout` | integer | `5` | Seconds of silence before speech input is considered complete. |
| `max_speech_duration` | integer | `60` | Max call duration in seconds. Call hangs up after this. |
| `speech_language` | string | `"en-US"` | Language for Twilio's built-in speech recognition (if not routing through Whisper). |
| `recording_enabled` | boolean | `false` | Record the call (stored as a Twilio recording artifact). |
| `recording_channels` | string | `"mono"` | `"mono"` or `"dual"`. Dual records caller and agent on separate channels. |
| `transcribe_callback` | boolean | `false` | Request Twilio transcription after call ends. |
| `status_callback` | boolean | `false` | Send call status events (ringing, answered, completed) to nodyra. |
| `response_mode` | string | `"sync"` | `"sync"` (block until entire workflow completes, then respond) or `"async"` (immediate `<Say>` + webhook on gather). |

**Lifecycle hooks (ProviderTriggerSpec):**

```python
# activate: Called when workflow is published
def activate_voice_webhook(context: ProviderTriggerActivationContext):
    # 1. Look up phone_number_sid via Twilio IncomingPhoneNumbers API
    # 2. PATCH the phone number's voice_url to context.callback_url
    # 3. PATCH voice_method to "POST"
    # 4. Return subscription with external_id = phone_number_sid

# deactivate: Called when workflow is disabled
def deactivate_voice_webhook(context: ProviderTriggerDeactivationContext):
    # PATCH phone number's voice_url back to empty/previous value

# handle_event: Validates incoming webhook from Twilio
def handle_voice_event(request: ProviderTriggerRequest, params: dict):
    # 1. Validate X-Twilio-Signature header using auth_token
    # 2. Extract: CallSid, From, To, CallStatus, Direction
    # 3. Return ProviderTriggerEvent with dedupe_key = f"twilio_call:{CallSid}"
    # 4. If CallStatus == "completed", skip (not a new call)
```

**Output payload (injected by runtime):**

```python
{
    "CallSid": "CA...",
    "From": "+1234567890",
    "To": "+1987654321",
    "CallStatus": "ringing",
    "Direction": "inbound",
    "FromCity": "New York",
    "FromState": "NY",
    "FromCountry": "US",
    "Called": "+1987654321",
    "caller_country": "US"
}
```

**Edge cases:**

- **Simultaneous calls:** Each call creates a separate run (deduped by CallSid). Twilio handles queuing.
- **Caller hangs up mid-workflow:** Twilio sends a `CallStatus=completed` webhook. The runtime should cancel in-progress runs for that CallSid. Need `call_status_callback` handling.
- **Phone number not found:** `activate` fails with a clear error. Workflow stays in "error" deployment state.
- **Phone number already has a voice URL:** `activate` should warn (via `node_debug`) but overwrite. On deactivate, restore previous URL only if it was a nodyra URL.
- **Twilio signature validation failure:** Return HTTP 403; log security event.
- **Workflow timeout:** If `response_mode="sync"` and the workflow takes > 15s (Twilio's TCP timeout), the call drops. Document that `sync` mode requires fast workflows or use `async` for complex ones.
- **Duplicate calls:** Dedup on `CallSid` + `CallStatus != "completed"`.

**Credentials:**

Reuses the existing `twilio_sms` credential type (`account_sid` + `auth_token`). The field set is identical. No new credential type needed.

> **Implementation note:** The Twilio v2 integration already exists at `packages/nodes/nodyra_nodes/integrations_v2/providers/twilio/` (SMS operations only). Voice trigger and voice operation nodes should be added to that same provider module rather than creating a separate file.

**Twilio API calls:**

| Operation | Endpoint | When |
|---|---|---|
| List incoming phone numbers | `GET /2010-04-01/Accounts/{sid}/IncomingPhoneNumbers.json?PhoneNumber={twilio_phone_number}` | `activate` |
| Set voice URL | `POST /2010-04-01/Accounts/{sid}/IncomingPhoneNumbers/{pn_sid}.json` with `VoiceUrl` + `VoiceMethod` | `activate` |
| Clear voice URL | `POST ...` with empty `VoiceUrl` | `deactivate` |

---

### Node 1.2 — Twilio Voice Respond

**ID:** `twilio_voice_respond`  
**Role:** `executable`  
**Category:** `Communication`  
**Icon:** `brand:twilio`  
**Usable as tool:** `false`

This node constructs a TwiML response and returns it as the workflow's HTTP response to Twilio. It is the **last node** in the voice pipeline — whatever it outputs becomes the `<Response>` sent back to Twilio.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `action` | string | `"say"` | `"say"`, `"play"`, `"gather"`, `"hangup"`, `"redirect"`, `"dial"`, `"raw"` |
| `message` | string | `""` | Text to speak (for `action="say"`). Falls back to wired input if empty. |
| `voice` | string | `"alice"` | Twilio voice: `alice`, `bob` (en-US), `Polly.*` voices. |
| `language` | string | `"en-US"` | Language for Polly voices. |
| `loop` | integer | `1` | Repeat count for `<Say>` / `<Play>`. |
| `audio_url` | string | `""` | URL to play audio from (for `action="play"`). Must be publicly accessible. |
| `gather_input` | string | `"speech dtmf"` | Input types for `<Gather>`. |
| `gather_timeout` | integer | `5` | Seconds to wait for input. |
| `gather_num_digits` | integer | `0` | Max DTMF digits (`0` = unlimited). |
| `gather_action_url` | string | `""` | URL to POST gathered input to (if not looping back to the trigger). |
| `redirect_url` | string | `""` | TwiML URL to redirect to. |
| `dial_number` | string | `""` | E.164 number to dial for call forwarding. |
| `dial_timeout` | integer | `30` | Ring timeout for forwarded call. |
| `dial_caller_id` | string | `""` | Caller ID for forwarded call. |

**Output:** Returns TwiML XML string in `{"main": "<Response>...</Response>"}`.

**Edge cases:**

- **Empty message:** If `action="say"` and message is empty despite wired input, responds with `<Hangup/>`.
- **SSRF protection:** `audio_url` and `redirect_url` are validated via `assert_public_http_url()`.
- **Message too long:** Twilio `<Say>` has a 4KB text limit. Truncate with ellipsis and log warning via `node_debug`.
- **Artifact audio:** If the wired input is an artifact ref with `kind="audio"` (from OpenAI TTS), generate a presigned URL, set `action="play"` automatically, and use the artifact as `audio_url`.
- **TwiML injection:** Escape `<`, `>`, `&` in message text to prevent XML injection. Use `xml.sax.saxutils.escape()`.
- **Dial failure:** If `dial_timeout` expires, Twilio calls `action` URL on the `<Dial>` verb. This node doesn't handle that — the trigger catches `DialCallStatus=failed` status callbacks.

---

### Node 1.3 — Twilio Voice Gather

**ID:** `twilio_voice_gather`  
**Role:** `executable`  
**Category:** `Communication`  
**Icon:** `brand:twilio`  
**Usable as tool:** `false`

Collects DTMF digits or speech input from the caller. Designed to sit in the middle of a voice pipeline: gathers input → routes to Whisper (if using speech) or passes DTMF digits → feeds response to AI Chat Model → sends back via Twilio Voice Respond.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `input_type` | string | `"dtmf"` | `"dtmf"`, `"speech"`, `"both"` |
| `prompt` | string | `""` | What to say before listening. E.g. "Press 1 for sales, 2 for support." |
| `num_digits` | integer | `0` | Max DTMF digits (`0` = unlimited). For menus, set to 1. |
| `finish_on_key` | string | `"#"` | DTMF key that ends input. |
| `speech_timeout` | integer | `5` | Seconds of silence before speech is final. |
| `speech_language` | string | `"en-US"` | Language hint for speech recognition. |
| `speech_hints` | string | `""` | Comma-separated words/phrases to bias recognition towards. |
| `max_speech_duration` | integer | `15` | Max speech recording duration in seconds. After this, stops and processes. |
| `action_url` | string | `""` | URL to POST gathered input to. If empty, returns gathered data as output. |
| `method` | string | `"POST"` | HTTP method for `action_url`. |

**Output:**
```python
{
    "Digits": "1234",              # DTMF input
    "SpeechResult": "hello world",  # Speech recognition result
    "Confidence": 0.95,             # Speech confidence (0-1)
    "InputType": "dtmf",            # Which input was received
    "CallSid": "CA...",             # Propagated from trigger
}
```

**Flow integration:**

When placed between the trigger and an AI model:
1. Trigger fires → Gather prompts the caller → caller speaks/presses
2. Gather output → Whisper Transcribe (if speech + using OpenAI whisper instead of Twilio's built-in)
3. Whisper output → AI Chat Model (with conversation history from AI Buffer Memory)
4. AI Chat Model output → TTS → Twilio Voice Respond → caller hears response
5. Twilio Voice Respond redirects back to step 1 (loop until hangup)

**Edge cases:**

- **No input received:** After `speech_timeout` seconds, returns `Digits=""` / `SpeechResult=""`. The downstream AI can handle "silence" gracefully.
- **DTMF overflow:** If caller enters more than `num_digits`, Twilio truncates. The node passes through whatever Twilio sends.
- **Speech confidence too low:** If `Confidence` < 0.3 (Twilio's threshold), log warning. The node still passes the result — the AI model can ask "I didn't catch that, could you repeat?"
- **Language mismatch:** `speech_language` must match what the caller speaks. Provide choices for common languages. For multilingual agents, recommend routing through Whisper instead.

---

### Node 1.4 — Voicemail Detect

**ID:** `voicemail_detect`  
**Role:** `executable`  
**Category:** `Communication`  
**Icon:** `brand:twilio`

Detects whether an outbound call was answered by a human or voicemail using Twilio's `AnsweredBy` parameter and/or AMD (Answering Machine Detection).

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `amd_enabled` | boolean | `true` | Use Twilio's Answering Machine Detection (adds ~1s latency, extra cost). |
| `voicemail_behavior` | string | `"skip"` | `"skip"` (end workflow early), `"leave_message"` (proceed but flag as voicemail), `"hangup"`. |

**Output:**
```python
{
    "AnsweredBy": "human" | "machine" | "unknown",
    "CallSid": "CA...",
    "Duration": 1.2,  # seconds before answer detection
}
```

**Edge cases:**

- **AMD false positive:** Twilio's AMD is ~90% accurate. For critical use cases, recommend `voicemail_behavior="leave_message"` and let the AI handle it.
- **AMD not available:** Some countries don't support AMD. Parameter should be conditionally disabled based on `From` country code (document limitation).

---

### Node 1.5 — Twilio Outbound Call

**ID:** `twilio_outbound_call`  
**Role:** `executable`  
**Category:** `Communication`  
**Icon:** `brand:twilio`  
**Tool side-effecting:** `true`

Initiates an outbound voice call via Twilio. Useful for appointment reminders, notifications, or proactive outreach.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | Twilio Account SID + Auth Token |
| `to` | string | required | Destination phone number (E.164). Falls back to wired input. |
| `from_phone` | string | required | Twilio phone number to call from (must be owned or verified). |
| `twiml_url` | string | `""` | URL that returns TwiML to execute. If empty, uses a nodyra webhook URL. |
| `status_callback` | string | `""` | URL for call status updates. |
| `timeout` | integer | `30` | Ring timeout in seconds. |
| `caller_id` | string | `""` | Override caller ID (for verified numbers). |
| `machine_detection` | string | `"Enable"` | `"Enable"` or `"DetectMessageEnd"` for AMD. |
| `record` | boolean | `false` | Record the call. |

**Edge cases:**

- **Invalid/blocked number:** Twilio returns error 13223/13224. Node raises `RuntimeError` with clear message.
- **Rate limiting:** Twilio allows 1 call/second per account by default. Node should handle HTTP 429 with exponential backoff (reuse `ProviderTransport` retry logic).
- **Concurrent calls:** Each outbound call creates a new child run via `call_workflow` context var for status tracking.

---

### Node 1.6 — Text to Speech (Filesystem)

**ID:** `text_to_speech_file`  
**Role:** `executable`  
**Category:** `AI`  
**Output kind:** `artifact`

Generates TTS audio and stores it as a nodyra artifact. Unlike `openai_tts` which returns inline base64, this node caches the audio file for reuse across calls.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | OpenAI API key |
| `text` | string | `""` | Text to synthesize. |
| `voice` | string | `"alloy"` | OpenAI voice. |
| `model` | string | `"tts-1"` | OpenAI TTS model. |
| `format` | string | `"mp3"` | `mp3`, `opus`, `aac`, `flac`, `wav`, `pcm`. |
| `speed` | number | `1.0` | Playback speed (0.25 – 4.0). |
| `cache_key` | string | `""` | If set, stores artifact with a stable key for reuse. Useful for static greetings. |

**Edge cases:**

- **Cached artifact:** If `cache_key` is set and an artifact with that key already exists in the same workflow, skip API call and return existing ref. Requires artifact store to support key-based lookup (minor artifact store enhancement).
- **Text too long:** OpenAI TTS has a 4096 character limit. Chunk text and concatenate audio files (requires `ffmpeg` system requirement for concatenation).

---

### Node 1.7 — Speech to Text (Filesystem)

**ID:** `speech_to_text_file`  
**Role:** `executable`  
**Category:** `AI`  
**Input kind:** `artifact`

Transcribes audio from a nodyra artifact (e.g., a recorded call, uploaded file). Wraps `openai_whisper_transcribe` with direct artifact input support.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | OpenAI API key |
| `artifact_input` | string | `""` | Reference to an audio artifact. Falls back to wired input if it's an artifact ref. |
| `model` | string | `"whisper-1"` | Whisper model. |
| `language` | string | `""` | ISO-639-1 language hint. |
| `response_format` | string | `"verbose_json"` | `"json"`, `"text"`, `"srt"`, `"verbose_json"`, `"vtt"`. |
| `prompt` | string | `""` | Guiding text for the transcription (vocabulary, context). |
| `temperature` | number | `0.0` | Sampling temperature. Higher = more creative. |

**Edge cases:**

- **File too large:** Whisper API limit is 25 MB. If artifact exceeds this, raise clear error. For larger files, implement chunked transcription (split audio, transcribe segments, merge).
- **Unsupported format:** Whisper supports `flac`, `m4a`, `mp3`, `mp4`, `mpeg`, `mpga`, `oga`, `ogg`, `wav`, `webm`. Validate artifact content_type or extension.
- **Artifact not audio:** Check `artifact["kind"] == "audio"`. If not, raise descriptive error.

---

### Node 1.8 — Call Status Handler

**ID:** `twilio_call_status_handler`  
**Role:** `executable`  
**Category:** `Communication`  
**Icon:** `brand:twilio`

Handles Twilio call status callback webhooks. Parses `CallStatus` (queued, ringing, in-progress, completed, busy, failed, no-answer, canceled) and routes to appropriate output branches.

**Outputs:** `["completed", "failed", "no_answer", "busy", "in_progress"]`

Each port emits the full Twilio status payload when the associated status is received.

**Edge cases:**

- **Multiple statuses for same call:** Twilio sends multiple callbacks per call. The node emits on the first matching port, then subsequent callbacks for the same `CallSid` flow to a `"subsequent"` output port.
- **Delayed callbacks:** Twilio retries status callbacks for up to 24h. The `CallDuration` field distinguishes stale callbacks from fresh ones.

---

## Workstream 2 — Voice AI Service Integrations

These are dedicated voice AI platforms that handle the entire real-time audio pipeline (STT → LLM → TTS) internally. nodyra acts as the orchestration layer.

### Node 2.1 — Vapi.ai Integration

**Integration spec:** `IntegrationSpec(id="vapi", name="Vapi", ...)`  
**Provider:** `integrations_v2/providers/vapi/`

Vapi provides a managed voice agent infrastructure. The integration has these operations:

| Operation | ID | Description |
|---|---|---|
| **Start Call** | `vapi_start_call` | Initiate an outbound voice call with a Vapi assistant |
| **Get Call** | `vapi_get_call` | Fetch call details and transcript |
| **List Calls** | `vapi_list_calls` | List recent calls |
| **End Call** | `vapi_end_call` | Terminate an active call |
| **Create Assistant** | `vapi_create_assistant` | Create/update a Vapi assistant configuration |
| **Upload File** | `vapi_upload_file` | Upload a file (e.g., knowledge base) for the assistant |

**Vapi Voice Call Trigger (ProviderTriggerSpec):**
- Poll-based trigger: polls `GET /call` endpoint every 10s for new completed calls
- Outputs full call transcript, summary, recording URL, and structured data extracted by the assistant

**Credentials:** Vapi private API key (`cred_single("vapi", "api_key", "Vapi API key")`)

**Edge cases:**

- **Webhook vs poll:** Vapi supports both webhook callbacks and REST polling. Start with polling (simpler, no public URL needed). Add webhook trigger later.
- **Assistant config:** The `assistant` config object in Vapi is complex (voice, model, first message, system prompt, tools, transcriber, server URL for custom functions). Expose as a JSON editor with schema validation.
- **Concurrent calls:** Vapi handles concurrency; nodyra creates one run per call completion (deduped by `call.id`).

---

### Node 2.2 — Retell.ai Integration

**Integration spec:** `IntegrationSpec(id="retell", name="Retell AI", ...)`  
**Provider:** `integrations_v2/providers/retell/`

| Operation | ID | Description |
|---|---|---|
| **Create Call** | `retell_create_call` | Start an outbound or inbound-originated call |
| **Get Call** | `retell_get_call` | Fetch call details, transcript, recording |
| **List Calls** | `retell_list_calls` | List recent calls |
| **Create Agent** | `retell_create_agent` | Define a voice agent (LLM, voice, language, etc.) |
| **List Agents** | `retell_list_agents` | List configured agents |
| **Create Phone Number** | `retell_create_phone_number` | Purchase/configure a phone number for inbound calls |

**Credentials:** Retell API key (`cred_single("retell", "api_key", "Retell API key")`)

---

### Node 2.3 — Bland.ai Integration

**Integration spec:** `IntegrationSpec(id="bland", name="Bland AI", ...)`  
**Provider:** `integrations_v2/providers/bland/`

| Operation | ID | Description |
|---|---|---|
| **Send Call** | `bland_send_call` | Initiate outbound call |
| **Get Call** | `bland_get_call` | Fetch call details |
| **List Calls** | `bland_list_calls` | List recent calls |
| **Stop Call** | `bland_stop_call` | End active call |
| **Analyze Call** | `bland_analyze_call` | Get AI analysis of call outcomes |

**Credentials:** Bland API key (`cred_single("bland", "api_key", "Bland API key")`)

---

### Node 2.4 — ElevenLabs Conversational AI Integration

**Integration spec:** `IntegrationSpec(id="elevenlabs_convai", name="ElevenLabs ConvAI", ...)`  
**Provider:** `integrations_v2/providers/elevenlabs_convai/`

| Operation | ID | Description |
|---|---|---|
| **Create Agent** | `elevenlabs_create_agent` | Create/update a conversational AI agent |
| **Get Agent** | `elevenlabs_get_agent` | Get agent configuration |
| **List Agents** | `elevenlabs_list_agents` | List all agents |
| **Get Conversation** | `elevenlabs_get_conversation` | Get transcript and metadata |
| **List Conversations** | `elevenlabs_list_conversations` | List recent conversations |
| **Get Signed URL** | `elevenlabs_get_signed_url` | Generate a signed WebSocket URL for embedding the voice widget |

**Credentials:** ElevenLabs API key (`cred_single("elevenlabs", "api_key", "ElevenLabs API key")`)

This is separate from the standalone ElevenLabs TTS node (below, §5.4) which does simple text-to-speech.

---

## Workstream 3 — Twilio Media Streams (WebSocket)

### Background

Twilio Media Streams sends bidirectional raw audio over a WebSocket connection during a live call. This enables sub-100ms latency voice agents where audio frames flow in real time: caller speaks → audio frames → Whisper/Deepgram → LLM → TTS → audio frames → caller hears.

nodyra currently uses WebSockets only for internal infrastructure (run events, remote runner agents). A **node-level WebSocket** capability is needed.

### Infrastructure change: Node WebSocket support

**What's needed in `nodyra.runtime` / `nodyra.context`:**

1. **`ws_connect` context function:** A `ContextVar` that nodes call to open a WebSocket connection. The runtime provides a managed connection with lifecycle tracking.

```python
# In nodyra/context.py
node_ws_connect: ContextVar[Callable[[str, dict], WebSocketConnection] | None] = (
    ContextVar("node_ws_connect", default=None)
)
```

2. **`WebSocketConnection` protocol:**
```python
class WebSocketConnection(Protocol):
    async def send(self, data: bytes | str) -> None: ...
    async def recv(self) -> bytes | str: ...
    async def close(self) -> None: ...
```

3. **Runtime implementation:** The runtime server (`nodyra_runtime/server.py`) opens and manages WebSocket connections on behalf of nodes. Connections are tracked and cleaned up when the node run finishes or times out.

4. **Media Streams specific:** For Twilio Media Streams, audio arrives as `mu-law` (μ-law) encoded 8kHz audio chunks in JSON messages. Need a **codec utility** for μ-law ↔ PCM16 conversion. **`audioop` was removed in Python 3.13** (runtime is 3.13.7); use `audioop-lts` (PyPI drop-in backport, `Requirements: ["audioop-lts>=0.2"]`) or route through `pydub` + `ffmpeg`.

---

### Node 3.1 — Twilio Media Streams Start

**ID:** `twilio_media_streams_start`  
**Role:** `supplier` (provides a stream object consumed by downstream nodes)  
**Category:** `Communication`  
**Icon:** `brand:twilio`  
**Input kind:** `any`  
**Output kind:** `ai_stream` (new `PortDataKind`)

Opens a WebSocket connection to Twilio Media Streams and manages the bidirectional audio stream.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | Twilio Account SID + Auth Token |
| `stream_url` | string | `"wss://media.twilio.com/v1/Streams/{CallSid}"` | WebSocket URL from the trigger's TwiML `<Connect><Stream>` |
| `call_sid` | string | `""` | Twilio CallSid. Falls back to trigger output. |
| `audio_format` | string | `"mulaw"` | Audio encoding: `"mulaw"` (μ-law, Twilio default) or `"pcm16"`. |
| `sample_rate` | integer | `8000` | Audio sample rate in Hz. |
| `inbound_track` | string | `"both"` | `"inbound"`, `"outbound"`, or `"both"` — which audio streams to receive. |
| `timeout` | integer | `300` | Max stream duration in seconds. After this, closes connection. |

**Output:**
Returns a stream adapter object that downstream nodes (Whisper Realtime, TTS Realtime) can consume. The adapter abstracts the WebSocket connection and provides:

```python
class TwilioMediaStreamAdapter:
    call_sid: str
    stream_sid: str
    
    async def send_audio(self, chunk: bytes) -> None:
        """Send audio chunk to the caller (TTS output)."""
    
    async def recv_audio(self) -> bytes:
        """Receive next audio chunk from caller."""
    
    async def send_mark(self, label: str) -> None:
        """Send a mark event (for synchronization)."""
    
    async def close(self) -> None:
        """Close the media stream."""
```

**TwiML integration:**

This node integrates with `Twilio Voice Respond`. When `action="start_stream"`, the Respond node generates:
```xml
<Response>
  <Connect>
    <Stream url="wss://{nodyra_host}/ws/media-stream/{run_id}">
      <Parameter name="call_sid" value="{CallSid}"/>
    </Stream>
  </Connect>
</Response>
```

**nodyra must relay:** Twilio connects to nodyra's WebSocket. nodyra's runtime relays audio frames to the node. The node processes them (STT → LLM → TTS) and sends audio back.

**Edge cases:**

- **Connection drops:** If Twilio disconnects (caller hangs up), the node receives a `close` event. Clean up resources, mark the run as partial.
- **Audio format mismatch:** If Twilio sends μ-law but the node expects PCM16, convert transparently.
- **Silence detection:** The stream may contain long periods of silence. Implement VAD (Voice Activity Detection) using `webrtcvad` Python package to skip silence frames and save LLM token costs.
- **Overlapping speech:** Twilio Media Streams does NOT natively handle barge-in (caller interrupting the bot). The node must detect when the user starts speaking during TTS playback and interrupt (implement via mark events + audio energy detection).
- **Latency budget:** End-to-end latency (speech → response) must stay under 300ms for natural conversation. Profile each component. Consider local models (Ollama/whisper.cpp) for production deployments.

---

### Node 3.2 — Deepgram Realtime STT

**ID:** `deepgram_realtime_stt`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `brand:deepgram`  
**Input kind:** `ai_stream`  
**Requirements:** `["deepgram-sdk>=3.0"]`

Connects to Deepgram's streaming speech-to-text API. Receives audio chunks from a media stream supplier and outputs interim + final transcripts.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | Deepgram API key |
| `model` | string | `"nova-2"` | Deepgram model. |
| `language` | string | `"en-US"` | Language code. |
| `interim_results` | boolean | `true` | Emit interim (partial) transcriptions for low-latency UX. |
| `smart_format` | boolean | `true` | Auto-format numbers, dates, punctuation. |
| `diarize` | boolean | `false` | Speaker diarization (who said what). |
| `utterance_end_ms` | integer | `1000` | Silence duration that marks utterance end. |

**Outputs:** `["interim", "final"]`
- `interim`: Emits partial transcripts (for real-time display)
- `final`: Emits completed utterances (for LLM processing)

**Edge cases:**

- **Connection lost mid-utterance:** Buffer audio locally, reconnect, replay.
- **Rate limiting:** Deepgram free tier is limited. Implement credential validation on `activate`.

---

## Workstream 4 — New Trigger Nodes

### Trigger 4.1 — IMAP Email Trigger

**ID:** `imap_email_trigger`  
**Role:** `trigger`  
**Category:** `Triggers`  
**Icon:** `mail`  
**Requirements:** N/A (stdlib `imaplib`, `email`)

**Pattern:** Poll-based provider trigger. Polls IMAP server every N seconds for new (unseen) emails.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | `cred_multi("imap", "IMAP credentials", ["host", "port", "username", "password", "tls"])` |
| `mailbox` | string | `"INBOX"` | IMAP mailbox to monitor. |
| `poll_interval` | integer | `60` | Seconds between polls. |
| `mark_as_read` | boolean | `true` | Mark fetched emails as seen. |
| `include_attachments` | boolean | `false` | Download and store attachments as artifacts. |
| `attachment_max_size` | integer | `10485760` | Max attachment size in bytes (default 10 MB). |
| `search_criteria` | string | `"UNSEEN"` | IMAP search string. E.g. `'(UNSEEN FROM "boss@example.com")'`. |
| `max_emails_per_poll` | integer | `10` | Maximum emails to process per poll. |

**Output payload (per-email):**
```python
{
    "message_id": "<abc123@mail.example.com>",
    "from": "Alice <alice@example.com>",
    "to": ["bob@example.com"],
    "cc": [],
    "subject": "Meeting tomorrow",
    "date": "2026-06-19T10:30:00Z",
    "body_text": "...",
    "body_html": "<html>...</html>",
    "attachments": [
        {"filename": "report.pdf", "artifact_ref": {...}, "content_type": "application/pdf", "size": 12345}
    ],
    "headers": {"X-Priority": "1", ...}
}
```

**Edge cases:**

- **Connection failure:** Exponential backoff on reconnect. Max 5 retries, then mark subscription as errored.
- **IMAP IDLE:** For low-latency, support IMAP IDLE mode (push notifications from server). Fall back to polling if server doesn't support IDLE.
- **Encoding:** Email subjects and bodies may be in exotic charsets (`=?UTF-8?B?...?=`). Decode using `email.header.decode_header()`.
- **Large attachments:** Skip attachments exceeding `attachment_max_size`. Log warning via `node_debug`. Don't download the attachment body at all (use IMAP `BODY.PEEK[1]` partial fetch).
- **Duplicate emails:** Dedup on `message_id`. If IMAP server renumbers UIDs (some do on folder rebuild), use `Message-ID` header as fallback dedup key.
- **TLS/STARTTLS:** Support both direct TLS (port 993) and STARTTLS (port 143). Detect based on port or explicit `tls` credential field.
- **OAuth2:** Not in v1. Document that Gmail users should use the Gmail provider trigger instead.

---

### Trigger 4.2 — File Watcher Trigger

**ID:** `file_watcher_trigger`  
**Role:** `trigger`  
**Category:** `Triggers`  
**Icon:** `folder`  
**Requirements:** `["watchfiles>=0.20"]`

Watches a directory for file changes (create, modify, delete) using OS-level file system events (`watchfiles` wraps `notify` on Linux, `FSEvents` on macOS, `ReadDirectoryChangesW` on Windows).

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `directory` | string | required | Absolute path to watch. |
| `patterns` | string | `"*"` | Glob patterns, comma-separated. E.g. `"*.csv, *.json"`. |
| `events` | string | `"create"` | Comma-separated events: `"create"`, `"modify"`, `"delete"`. |
| `recursive` | boolean | `true` | Watch subdirectories. |
| `debounce_ms` | integer | `1000` | Ignore duplicate events within this window (ms). |
| `include_content` | boolean | `false` | Read and include file content in the trigger payload. |
| `encoding` | string | `"utf-8"` | File encoding for text files. |
| `max_file_size` | integer | `52428800` | Skip files larger than this (bytes). Default 50 MB. |

**Output:**
```python
{
    "event": "create",
    "path": "/data/incoming/report.csv",
    "filename": "report.csv",
    "extension": ".csv",
    "size": 12345,
    "modified_at": "2026-06-19T10:30:00Z",
    "content": "..."  # if include_content=True
}
```

**Edge cases:**

- **Mount point watch:** Watched directory may be on a network mount that doesn't support OS events. `watchfiles` falls back to polling. Document this behavior.
- **Atomic writes:** Some apps write to a temp file then rename. This generates a `create` event for the temp file + `modify` → `rename`. Use `debounce_ms` to coalesce.
- **Mass file creation:** If 10,000 files are dropped at once, create one run per file. Add `max_events_per_batch` to prevent runaway execution.
- **Symlinks:** Follow or ignore? Parameter: `follow_symlinks` (default `false`).
- **Directory removed:** If the watched directory is deleted, the trigger goes into error state with a clear message.
- **Permission denied:** Gracefully skip files the runner can't read. Log warning.

---

### Trigger 4.3 — WebSocket Trigger

**ID:** `websocket_trigger`  
**Role:** `trigger`  
**Category:** `Triggers`  
**Icon:** `globe`

Fires on incoming WebSocket messages. Useful for real-time data streams, chat interfaces, IoT device feeds.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | string | `"ws"` | URL path for the WebSocket endpoint. |
| `auth_type` | string | `"none"` | `"none"`, `"token"` (query param), `"header"`. |
| `auth_token` | credential | — | Token for authentication. |
| `message_format` | string | `"json"` | Expected message format: `"json"`, `"text"`, `"binary"`. |
| `response_enabled` | boolean | `false` | Send a response back to the client after processing. |
| `max_message_size` | integer | `262144` | Max message size in bytes (default 256 KB). |
| `rate_limit` | integer | `100` | Max messages per minute per connection. `0` = unlimited. |

**Infrastructure:** nodyra already supports WebSocket connections at `ws://<host>/ws/runs/{run_id}`. This trigger requires a new WS endpoint at `ws://<host>/ws/triggers/{path}` that proxies messages to the trigger system.

**Edge cases:**

- **Connection lifecycle:** When a client connects, fire the trigger with `{"event": "connected", "client_id": "..."}`. When they disconnect, fire with `{"event": "disconnected"}`. Downstream nodes can branch on `event` field.
- **Binary messages:** If `message_format="binary"`, store the raw bytes as an artifact, emit the artifact ref.
- **Backpressure:** If the workflow is slower than message arrival, buffer messages. If buffer exceeds `max_message_size * 10`, drop oldest messages and emit a `"backpressure"` event.

---

### Trigger 4.4 — Kafka Trigger

**ID:** `kafka_trigger`  
**Role:** `trigger`  
**Category:** `Triggers`  
**Icon:** `brand:kafka`  
**Requirements:** `["kafka-python>=2.0", "confluent-kafka>=2.0"]`

Consumes messages from Apache Kafka topics.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | `cred_multi("kafka", "Kafka credentials", ["bootstrap_servers", "security_protocol", "sasl_mechanism", "sasl_username", "sasl_password"])` |
| `topic` | string | required | Kafka topic to consume from. |
| `consumer_group` | string | `"nodyra"` | Consumer group ID. |
| `auto_offset_reset` | string | `"latest"` | `"latest"` or `"earliest"`. |
| `max_poll_records` | integer | `10` | Max records per poll. |
| `value_format` | string | `"json"` | `"json"`, `"text"`, `"avro"`. |
| `schema_registry_url` | string | `""` | Confluent Schema Registry URL (for Avro). |
| `include_metadata` | boolean | `true` | Include Kafka metadata (topic, partition, offset, timestamp) in output. |

**Edge cases:**

- **Consumer group rebalance:** Expect rebalances during deployment. No special handling needed — `confluent-kafka` handles it.
- **Schema registry auth:** If using Avro + schema registry, need separate credentials for the registry. Add to credential fields.
- **Poison pill messages:** If a message fails processing, don't commit the offset. Let it retry. Set `max_retries` on the trigger to avoid infinite loops.

---

### Trigger 4.5 — MQTT Trigger

**ID:** `mqtt_trigger`  
**Role:** `trigger`  
**Category:** `Triggers`  
**Icon:** `wifi`  
**Requirements:** `["paho-mqtt>=2.0"]`

Subscribes to MQTT topics for IoT and sensor data.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | `cred_multi("mqtt", "MQTT credentials", ["broker_url", "port", "username", "password", "tls"])` |
| `topic` | string | required | MQTT topic to subscribe to (supports wildcards `+` and `#`). |
| `qos` | integer | `1` | Quality of Service: `0`, `1`, or `2`. |
| `client_id` | string | `""` | MQTT client ID. Auto-generated if empty. |

**Edge cases:**

- **Broker disconnect:** Auto-reconnect with backoff. `paho-mqtt` handles this.
- **Large messages:** MQTT max message size depends on broker. If message exceeds limit, the broker may disconnect the client. Guard with `max_message_size` parameter.
- **Retained messages:** MQTT retained messages are replayed on subscribe. Fire immediately with `retained=True` flag.

---

### Trigger 4.6 — Postgres LISTEN Trigger

**ID:** `postgres_listen_trigger`  
**Role:** `trigger`  
**Category:** `Triggers`  
**Icon:** `brand:postgresql`  
**Requirements:** `["psycopg[binary]>=3.0"]`

Triggers on Postgres `NOTIFY` events. Applications can `NOTIFY nodyra_events, '{"type": "order_created", "id": 123}'` and nodyra fires a workflow.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | Postgres connection string |
| `channel` | string | `"nodyra_events"` | Postgres LISTEN channel. |
| `payload_format` | string | `"json"` | Expected payload format. |

**Edge cases:**

- **Connection drops:** `psycopg` async connection handles reconnection. Subscribe to `CHANNEL_UNLISTENED` event and re-LISTEN.
- **Large payloads:** Postgres NOTIFY payload limit is 8000 bytes. If applications need larger payloads, they should send an ID and the workflow can fetch full data via Postgres Query node.

---

### Trigger 4.7 — S3 Event Trigger

**ID:** `s3_event_trigger`  
**Role:** `trigger`  
**Category:** `Triggers`  
**Icon:** `brand:aws`

Fires on S3 bucket events (object created, deleted, etc.) via S3 Event Notifications → SQS polling.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | `cred_multi("aws", "AWS credentials", ["access_key_id", "secret_access_key", "region"])` |
| `queue_url` | string | required | SQS queue URL that receives S3 events. |
| `event_types` | string | `"s3:ObjectCreated:*"` | Filter by event type. Comma-separated: `"s3:ObjectCreated:*, s3:ObjectRemoved:*"`. |
| `bucket_filter` | string | `""` | Only process events from this bucket. |
| `prefix_filter` | string | `""` | Only process objects under this prefix. |

**Edge cases:**

- **SQS visibility timeout:** If workflow processing time exceeds visibility timeout, the message becomes visible again and another run starts. Set `timeout_seconds` on the trigger node to less than SQS visibility timeout.
- **Multiple events per object:** S3 may send multiple `ObjectCreated` events for a single upload (multipart). Dedup by object key + event time window.

---

## Workstream 5 — New AI / LLM Nodes

### Node 5.1 — Ollama Chat Model

**ID:** `ollama_chat_model`  
**Role:** `supplier` (provides `ChatModelAdapter` compatible with AI v2 pipeline)  
**Category:** `AI`  
**Icon:** `brand:ollama`  
**Requirements:** `["ollama>=0.4"]`

Runs local LLMs via Ollama. Zero API costs, fully private.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | `"http://localhost:11434"` | Ollama server URL. |
| `model` | string | `"llama3.2"` | Model name (must be pulled on the server). |
| `temperature` | number | `0.7` | Sampling temperature. |
| `top_p` | number | `1.0` | Nucleus sampling. |
| `max_tokens` | integer | `2048` | Max output tokens. |
| `keep_alive` | string | `"5m"` | Keep model loaded in memory after request. |
| `num_ctx` | integer | `4096` | Context window size. |
| `format` | string | `""` | Force output format: `""` (free), `"json"` (JSON mode). |

**Edge cases:**

- **Ollama server unreachable:** Raise clear error at connection time. Suggest `ollama serve` command.
- **Model not pulled:** Ollama auto-pulls. But if pull fails (network, disk space), raise descriptive error.
- **GPU support:** Ollama auto-detects GPU. The node should surface GPU info in `node_debug` output (model load message includes backend: CUDA, ROCm, Metal, CPU).
- **Streaming:** Use Ollama's streaming API (`stream=True`) and emit tokens via `emit_chunk`.

---

### Node 5.2 — Ollama Embeddings

**ID:** `ollama_embeddings`  
**Role:** `supplier` (provides `EmbeddingModelAdapter`)  
**Category:** `AI`  
**Icon:** `brand:ollama`

Local embedding generation. Alternative to OpenAI Embeddings.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | `"http://localhost:11434"` | Ollama server URL. |
| `model` | string | `"nomic-embed-text"` | Embedding model name. |
| `keep_alive` | string | `"5m"` | Keep model loaded. |

---

### Node 5.3 — ElevenLabs TTS

**ID:** `elevenlabs_tts`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `brand:elevenlabs`  
**Requirements:** `["elevenlabs>=1.0"]`

High-quality text-to-speech with voice cloning support.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | ElevenLabs API key |
| `text` | string | `""` | Text to synthesize. |
| `voice_id` | string | required | ElevenLabs voice ID or `"premade:Adam"`, `"premade:Antoni"`, etc. |
| `model` | string | `"eleven_multilingual_v2"` | TTS model. |
| `stability` | number | `0.5` | Voice stability (0-1). |
| `similarity_boost` | number | `0.75` | Voice similarity boost (0-1). |
| `style` | number | `0.0` | Style exaggeration (0-1). |
| `output_format` | string | `"mp3_44100_128"` | Audio format and quality. |

**Edge cases:**

- **Voice ID loading:** Expose as `load_options="elevenlabs_voices"` dynamic option that fetches the user's voices from the ElevenLabs API. Fall back to premade voices list.
- **Rate limits:** ElevenLabs free tier has ~10k chars/month. Paid tiers have higher limits. Surface quota info via node_debug.
- **SSRF:** Validate that the API base URL passes `assert_public_http_url()`.

---

### Node 5.4 — Azure AI Speech

**ID:** `azure_ai_speech`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `brand:azure`  
**Requirements:** `["azure-cognitiveservices-speech>=1.35"]`

Enterprise STT + TTS with compliance certifications (SOC2, HIPAA, etc.).

**Both STT and TTS in one node.** Switchable via `mode` parameter.

**Parameters (shared):**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | `cred_multi("azure_speech", "Azure Speech credentials", ["region", "api_key"])` |
| `mode` | string | `"stt"` | `"stt"` or `"tts"`. |

**STT mode:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `audio_input` | — | — | Wired artifact or base64 audio. |
| `language` | string | `"en-US"` | Recognition language. |
| `profanity` | string | `"masked"` | Profanity handling: `"masked"`, `"removed"`, `"raw"`. |
| `detailed` | boolean | `false` | Return word-level timestamps + confidence. |

**TTS mode:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `text` | string | `""` | Text to speak. |
| `voice` | string | `"en-US-JennyNeural"` | Neural voice name. |
| `style` | string | `"neutral"` | Speaking style (cheerful, sad, angry, etc.). |
| `output_format` | string | `"audio-16khz-128kbitrate-mono-mp3"` | Audio format. |
| `ssml` | boolean | `false` | Parse text as SSML. |

---

### Node 5.5 — AI Named Entity Recognition

**ID:** `ai_named_entity_recognition`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `tag`

Extracts named entities (people, organizations, locations, dates, etc.) from text using an LLM or a dedicated NER model.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `backend` | string | `"llm"` | `"llm"` (uses configured AI Chat Model supplier) or `"spacy"`. |
| `text` | string | `""` | Text to analyze. Falls back to wired input. |
| `entity_types` | string | `"PERSON, ORG, GPE, DATE, MONEY, PRODUCT"` | Entity types to extract (for LLM mode). |
| `spacy_model` | string | `"en_core_web_trf"` | spaCy model (for `backend="spacy"`). |
| `chunk_size` | integer | `2000` | Max characters per LLM call (text is chunked). |

**Output:**
```python
{
    "entities": [
        {"text": "Alice Smith", "type": "PERSON", "start": 0, "end": 11},
        {"text": "Acme Corp", "type": "ORG", "start": 25, "end": 34},
        {"text": "2026-06-19", "type": "DATE", "start": 50, "end": 60}
    ],
    "text": "..."
}
```

---

### Node 5.6 — AI Summarizer

**ID:** `ai_summarizer`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `file-text`

Dedicated text summarization. Supports both extractive (select existing sentences) and abstractive (rewrite with LLM).

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `text` | string | `""` | Text to summarize. Falls back to wired input. |
| `mode` | string | `"abstractive"` | `"abstractive"` (LLM rewrite) or `"extractive"` (sentence scoring). |
| `summary_length` | string | `"medium"` | `"short"` (~1 sentence), `"medium"` (~3-5), `"long"` (~1 paragraph). |
| `focus` | string | `""` | What to emphasize in the summary (e.g. "financial results", "key decisions"). |
| `language` | string | `""` | Output language. Empty = same as input. |

---

### Node 5.7 — AI Sentiment Analysis

**ID:** `ai_sentiment_analysis`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `thumbs-up`

Analyzes sentiment of text using a configurable LLM backend.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `text` | string | `""` | Text to analyze. |
| `granularity` | string | `"document"` | `"document"` (overall) or `"sentence"` (per-sentence). |
| `aspects` | string | `""` | Comma-separated aspects to extract sentiment for (e.g. `"price, quality, service"`). |
| `output_format` | string | `"label"` | `"label"` (positive/negative/neutral), `"score"` (-1 to 1 float), or `"detailed"` (both + explanation). |

---

### Node 5.8 — AI Semantic Search

**ID:** `ai_semantic_search`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `search`

Searches documents by meaning rather than keywords. Uses embeddings + vector similarity.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | string | required | Search query. |
| `documents` | — | wired | List of documents (strings or `{id, text}` dicts). |
| `embedding_supplier` | — | wired | Embedding model supplier node connection. |
| `top_k` | integer | `5` | Number of results. |
| `min_score` | number | `0.0` | Minimum similarity score (0-1). `0` = no threshold. |

---

### Node 5.9 — HuggingFace Inference

**ID:** `huggingface_inference`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `brand:huggingface`  
**Requirements:** `["huggingface-hub>=0.20"]`

Runs any HuggingFace model via the Serverless Inference API or a dedicated Inference Endpoint.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `credentials` | credential | required | HuggingFace API token |
| `model` | string | required | Model ID (e.g. `"google/flan-t5-xl"`, `"bigcode/starcoder"`). |
| `task` | string | `"text-generation"` | `"text-generation"`, `"text-classification"`, `"summarization"`, `"translation"`, `"image-classification"`, `"automatic-speech-recognition"`, etc. |
| `inputs` | string | `""` | Model inputs. Falls back to wired input. |
| `parameters` | key-value | `{}` | Additional model parameters (temperature, max_length, etc.). |
| `endpoint_url` | string | `""` | If set, uses a dedicated Inference Endpoint instead of Serverless API. |

---

### Node 5.10 — AI Batch Processor

**ID:** `ai_batch_processor`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `layers`

Processes large datasets through an LLM efficiently by batching requests.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `items` | — | wired | List of items to process. |
| `prompt_template` | string | required | Jinja2 template. `{{ item }}` is each element. |
| `batch_size` | integer | `10` | Items per LLM call. |
| `concurrency` | integer | `3` | Parallel API calls. |
| `output_field` | string | `"result"` | Field name for the LLM output in each item. |
| `continue_on_error` | boolean | `true` | If an item fails, continue with remaining items. |

---

### Node 5.11 — AI Image Classifier

**ID:** `ai_image_classifier`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `image`

Classifies images using vision models (GPT-4V, Claude Vision, etc.).

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `images` | — | wired | Image artifacts or base64 strings. |
| `labels` | string | required | Comma-separated classification labels. |
| `model_supplier` | — | wired | Chat model supplier with vision support. |
| `multi_label` | boolean | `false` | Allow multiple labels per image. |
| `prompt` | string | `""` | Additional context for classification. |

---

### Node 5.12 — AI Code Review

**ID:** `ai_code_review`  
**Role:** `executable`  
**Category:** `AI`  
**Icon:** `code`

Reviews code for bugs, security issues, style, and best practices using an LLM.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `code` | string | `""` | Source code to review. |
| `language` | string | `""` | Programming language. Auto-detected if empty. |
| `review_aspects` | string | `"bugs, security, performance, style"` | What to check for. |
| `severity_threshold` | string | `"medium"` | Minimum issue severity to report: `"low"`, `"medium"`, `"high"`, `"critical"`. |

---

## Workstream 6 — Python Native Package Nodes

### Node 6.1 — Pandas Transform

**ID:** `pandas_transform`  
**Role:** `executable`  
**Category:** `Data`  
**Requirements:** `["pandas>=2.0"]`

Perform DataFrame operations with visual configuration (no code required).

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `operation` | string | `"filter"` | `"filter"`, `"select_columns"`, `"group_by"`, `"sort"`, `"merge"`, `"pivot"`, `"melt"`, `"fill_na"`, `"drop_duplicates"`, `"apply"`. |
| `filter_column` | string | `""` | Column to filter on. |
| `filter_operator` | string | `"=="` | `"=="`, `"!="`, `">"`, `"<"`, `">="`, `"<="`, `"contains"`, `"is_null"`, `"not_null"`, `"between"`, `"in"`. |
| `filter_value` | string | `""` | Comparison value. |
| `columns` | string | `""` | Comma-separated column list (for select, group_by, sort). |
| `aggregations` | key-value | `{}` | Aggregation specs (for group_by): `{"sales": "sum", "qty": "mean"}`. |
| `sort_ascending` | boolean | `true` | Sort direction. |
| `pivot_index` | string | `""` | Pivot index column. |
| `pivot_columns` | string | `""` | Pivot column. |
| `pivot_values` | string | `""` | Pivot value column. |
| `fill_value` | string | `""` | Fill NA value. |
| `output_as` | string | `"dataset"` | `"dataset"` (nodyra dataset format) or `"json"`. |

**Edge cases:**

- **Large DataFrames:** If input exceeds memory, fall back to chunked processing via `pandas.read_csv(chunksize=...)`.
- **Type coercion:** `filter_value` is a string from the UI. Auto-detect numeric/boolean types before comparison.
- **Pivot edge cases:** Duplicate pivot index+column combinations cause `ValueError`. Catch and suggest `aggfunc`.

---

### Node 6.2 — NumPy Array Operations

**ID:** `numpy_array_ops`  
**Role:** `executable`  
**Category:** `Data`  
**Requirements:** `["numpy>=1.26"]`

Perform array math operations with visual configuration.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `operation` | string | `"stats"` | `"stats"` (mean/median/std/min/max/percentile), `"math"` (add/sub/mul/div by scalar), `"reshape"`, `"transpose"`, `"concat"`, `"dot_product"`, `"clip"`, `"normalize"`. |
| `axis` | integer | `0` | Axis for operation. `None` = flattened. |
| `scalar` | number | `0` | Scalar value for `math` operations. |
| `new_shape` | string | `""` | Comma-separated dimensions for `reshape`. |
| `stats_list` | string | `"mean, std, min, max"` | Statistics to compute. |
| `percentile` | number | `50` | Percentile value (0-100). |

---

### Node 6.3 — Matplotlib Chart Generator

**ID:** `matplotlib_chart`  
**Role:** `executable`  
**Category:** `Charts`  
**Output kind:** `artifact`  
**Requirements:** `["matplotlib>=3.8"]`

Generates publication-quality charts programmatically.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `chart_type` | string | `"line"` | `"line"`, `"bar"`, `"scatter"`, `"histogram"`, `"boxplot"`, `"heatmap"`, `"pie"`. |
| `x_column` | string | `""` | Column for X axis. |
| `y_columns` | string | `""` | Comma-separated columns for Y axis (multiple = overlaid). |
| `title` | string | `""` | Chart title. |
| `x_label` | string | `""` | X axis label. |
| `y_label` | string | `""` | Y axis label. |
| `figsize` | string | `"10,6"` | Figure size as `width,height` in inches. |
| `color_scheme` | string | `"default"` | `"default"`, `"viridis"`, `"plasma"`, `"dark"`, `"pastel"`. |
| `output_format` | string | `"png"` | `"png"`, `"svg"`, `"pdf"`. |
| `dpi` | integer | `150` | Output resolution. |

**Edge cases:**

- **Large datasets:** For scatter plots with >10k points, automatically downsample or use `rasterized=True`.
- **Empty data:** Generate chart with "No data" watermark instead of crashing.
- **Non-numeric columns:** Skip non-numeric columns for math-based plots (histogram, boxplot). For pie charts, treat as categorical.

---

### Node 6.4 — Pydantic Schema Validation

**ID:** `pydantic_validate`  
**Role:** `executable`  
**Category:** `Data`  
**Requirements:** `["pydantic>=2.0"]`

Validates data against a Pydantic schema. Use cases: API response validation, data quality gates, type coercion.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `schema` | string | required | Pydantic model code (Python class definition). E.g. `class User(BaseModel): name: str; age: int`. |
| `data` | — | wired | JSON/dict data to validate. |
| `strict` | boolean | `false` | Strict mode (no type coercion). |
| `on_error` | string | `"raise"` | `"raise"` (stop workflow), `"filter"` (remove invalid items, continue), `"flag"` (add `_valid` boolean). |

**Outputs:** `["valid", "invalid"]`

**Edge cases:**

- **Schema execution security:** The `schema` parameter contains arbitrary Python code. This MUST run in the same sandbox as the Code node (AST validation + process isolation). Reject schemas with imports of `os`, `subprocess`, `socket`, etc.
- **Large datasets:** Validate items one at a time. If `on_error="filter"`, return only valid items.

---

### Node 6.5 — OpenCV Image Processing

**ID:** `opencv_process`  
**Role:** `executable`  
**Category:** `Image`  
**Requirements:** `["opencv-python>=4.8"]`  
**System requirements:** `SystemRequirement(name="opencv", apt="libgl1-mesa-glx", brew="libgl1", ...)`

Advanced image processing beyond the existing image nodes (which use Pillow).

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `operation` | string | `"detect_faces"` | `"detect_faces"`, `"detect_edges"`, `"blur"`, `"sharpen"`, `"threshold"`, `"contours"`, `"warp_perspective"`, `"color_balance"`, `"denoise"`, `"background_removal"`. |
| `image` | — | wired | Image artifact or base64. |
| `threshold_value` | integer | `127` | Threshold value (0-255). |
| `blur_kernel` | integer | `5` | Blur kernel size (odd number). |
| `cascade_file` | string | `"haarcascade_frontalface_default"` | OpenCV cascade classifier for face detection. |
| `output_format` | string | `"png"` | Output image format. |

---

### Node 6.6 — SciPy Statistical Functions

**ID:** `scipy_stats`  
**Role:** `executable`  
**Category:** `Data`  
**Requirements:** `["scipy>=1.12"]`

Advanced statistical functions beyond the existing `statistical_analysis` nodes.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `test` | string | `"ttest"` | `"ttest"`, `"chi2"`, `"anova"`, `"mannwhitney"`, `"ks"`, `"pearsonr"`, `"spearmanr"`, `"zscore"`, `"describe"`, `"normaltest"`. |
| `data` | — | wired | Input data (array or dict with `samples` key). |
| `samples_column` | string | `""` | For two-sample tests: column to split by. |
| `alpha` | number | `0.05` | Significance level. |

---

### Node 6.7 — spaCy NLP Pipeline

**ID:** `spacy_nlp`  
**Role:** `executable`  
**Category:** `AI`  
**Requirements:** `["spacy>=3.7"]`  
**System requirements:** `SystemRequirement(name="spacy_model", note="Run: python -m spacy download en_core_web_sm")`

Run full NLP pipelines locally: tokenization, POS tagging, NER, dependency parsing, lemmatization.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | string | `"en_core_web_sm"` | spaCy model name. |
| `text` | string | `""` | Text to process. |
| `components` | string | `"ner, pos, dep, lemma"` | Pipeline components to run. |

**Output:**
```python
{
    "tokens": [...],
    "entities": [...],
    "sentences": [...],
    "noun_phrases": [...],
    "dependency_tree": {...}
}
```

---

### Node 6.8 — NetworkX Graph Operations

**ID:** `networkx_graph_ops`  
**Role:** `executable`  
**Category:** `Data`  
**Requirements:** `["networkx>=3.2"]`

Graph and network analysis: shortest path, centrality, clustering, community detection.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `operation` | string | `"shortest_path"` | Operation type. |
| `graph_data` | — | wired | Edge list or adjacency matrix. |
| `source` | string | `""` | Source node ID. |
| `target` | string | `""` | Target node ID. |
| `weight_column` | string | `""` | Edge weight column. |

---

### Node 6.9 — BeautifulSoup Web Scraper

**ID:** `beautifulsoup_scrape`  
**Role:** `executable`  
**Category:** `API`  
**Requirements:** `["beautifulsoup4>=4.12", "lxml>=5.0"]`

Structured HTML scraping with CSS selectors. More flexible than the existing `browser_scrape` node.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `html` | — | wired | HTML content (from HTTP Request or Browser Scrape). |
| `selectors` | key-value | `{}` | Map of field names to CSS selectors. E.g. `{"title": "h1", "price": ".price-tag span"}`. |
| `extract` | string | `"text"` | What to extract: `"text"`, `"html"`, `"attribute:attr_name"`. |
| `multiple` | boolean | `false` | Return list of matches instead of first match. |
| `strip` | boolean | `true` | Strip whitespace. |

---

### Node 6.10 — SymPy Symbolic Math

**ID:** `sympy_math`  
**Role:** `executable`  
**Category:** `Data`  
**Requirements:** `["sympy>=1.12"]`

Symbolic mathematics: equation solving, calculus, linear algebra, simplification.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `operation` | string | `"solve"` | `"solve"`, `"simplify"`, `"expand"`, `"factor"`, `"diff"`, `"integrate"`, `"limit"`, `"matrix_ops"`, `"latex"`. |
| `expression` | string | required | Mathematical expression. E.g. `"x**2 + 2*x + 1"`. |
| `variable` | string | `"x"` | Variable name. |

---

## Implementation Strategy

### Phase 0 — Security Prerequisite ✅ DONE

~~SEC-1 (HIGH): `ProviderTransport` bypassed the SSRF guard.~~ **Fixed.** `ProviderTransport.request` now routes through `safe_request` with per-redirect-hop revalidation (`transport.py:205`). The ⚠️ markers in the [Already Built](#already-built-out-of-plan) table are resolved by this fix.

Still open from the same audit (lower priority, not blocking):
- **SEC-2** — raw `requests` calls in legacy `saas.py` / `communication.py` nodes (pre-v2). Sweep + funnel through `safe_request`.
- **SEC-3** — consolidate three SSRF helpers (`http_security.py`, `file_nodes.py:_ssrf_safe_fetch`, `transport.py`) into one egress module.
- **SEC-4** — `postgres_nodes.py` allows arbitrary connection hosts (acceptable for single-tenant; document or gate for hosted).
- **O2** — `_all_workflows()` test-webhook lookup lacks `active` filter (`triggers.py:901`).
- **S1** — `decrypt_data(strict=False)` returns `{}` on failure; audit callers on exec paths use `strict=True`.

### Phase 1 — Foundation (weeks 1–2)

1. **Twilio credential field alignment** — verify existing `twilio_sms` credential type includes all fields needed for voice operations (`account_sid`, `auth_token`). No new credential type needed.
2. **WebSocket infrastructure** — implement `node_ws_connect` in the runtime.
3. **Provider trigger voice pattern** — implement `Twilio Voice Call Trigger` + `Twilio Voice Respond` as the proof-of-concept voice pipeline.

### Phase 2 — Voice MVP (weeks 3–4)

4. **Twilio Voice Gather**, **Voicemail Detect**, **Twilio Outbound Call**, **Call Status Handler**.
5. **Text to Speech (Filesystem)**, **Speech to Text (Filesystem)**.
6. End-to-end voice agent workflow test: trigger → gather → whisper → chat → TTS → respond.

### Phase 3 — Voice AI Services (weeks 5–6)

7. **Vapi.ai** integration (full provider: operations + poll trigger).
8. **Retell.ai** integration.
9. **Bland.ai** integration.
10. **ElevenLabs ConvAI** integration.
11. **ElevenLabs TTS** standalone node.

### Phase 4 — Media Streams (weeks 7–8)

12. **Twilio Media Streams Start** node.
13. **Deepgram Realtime STT** node.
14. Implement μ-law ↔ PCM16 codec, VAD, barge-in detection.
15. Media Streams latency optimization.

### Phase 5 — Triggers (weeks 9–10)

16. **IMAP Email Trigger**, **File Watcher Trigger**, **WebSocket Trigger**, **Postgres LISTEN Trigger**.
17. **Kafka Trigger**, **MQTT Trigger**, **S3 Event Trigger** (as community demand/priority warrants).

### Phase 6 — AI Nodes (weeks 11–12)

18. **Ollama Chat Model**, **Ollama Embeddings**, **ElevenLabs TTS**, **Azure AI Speech**.
19. **AI Named Entity Recognition**, **AI Summarizer**, **AI Sentiment Analysis**, **AI Semantic Search**.
20. **HuggingFace Inference**, **AI Batch Processor**, **AI Image Classifier**, **AI Code Review**.

### Phase 7 — Python Native Packages (weeks 13–14)

21. **Pandas Transform**, **NumPy Array Ops**, **Matplotlib Chart Generator**, **Pydantic Validate**.
22. **OpenCV Process**, **SciPy Stats**, **spaCy NLP**, **NetworkX Graph**, **BeautifulSoup Scraper**, **SymPy Math**.

---

## Testing Strategy

Each node requires:

| Test type | Coverage target | Example |
|---|---|---|
| **Unit tests** | Every output branch, every error case | `test_twilio_voice_respond_generates_twiml_for_each_action()` |
| **Integration tests** | Node in a graph with upstream/downstream nodes | `test_voice_agent_pipeline_end_to_end()` |
| **Edge case tests** | Empty input, missing credentials, invalid params, network errors | `test_whisper_transcribe_handles_oversized_file()` |
| **Provider trigger lifecycle tests** | activate → webhook → deactivate | `test_twilio_voice_trigger_activates_and_handles_call()` |
| **Security tests** | SSRF prevention, credential leakage, XML injection | `test_twilio_voice_respond_escapes_twiml_injection()` |
| **Performance tests** | Throughput, memory for large inputs | `test_pandas_transform_handles_1M_rows()` |

---

## Category Updates

Two new categories are needed in `apps/web/src/categories.ts`:

```typescript
export const CATEGORY_COLORS: Record<string, string> = {
  // ... existing ...
  Communication: "#ff7043",  // deep orange — for voice/SMS/email nodes
  "AI Voice": "#00bfa5",     // teal — for voice AI service integrations
};
```

---

## Documentation Per Node

Each node must include:
1. **Docstring** — single paragraph of what the node does (used for `description` in the manifest)
2. **Parameter descriptions** — every param gets a `"description"` in the param meta
3. **Placeholders** — realistic example values for all string params
4. **Choices** — all enum params must list valid options
5. **Output schema comment** — in the function docstring or as a module-level comment

---

## Rollback / Migration

- All new nodes use unique `id` values. No existing nodes are deprecated or renamed.
- The `twilio_sms` credential type is reused for voice — no migration needed.
- New categories "Communication" and "AI Voice" are additive. Existing nodes stay in their current categories.

---

## Already Built (Out of Plan)

Work that landed in the working tree before the plan was finalised. Not part of the six workstreams above but needs the same review/test bar before shipping.

### New v2 Integration Providers (16)

All use the `integrations_v2` + `ProviderTransport` pattern. SEC-1 SSRF fix is already in `transport.py` — ⚠️ markers below are for audit traceability only, not open issues.

| Provider | Directory |
|---|---|
| Asana | `integrations_v2/providers/asana/` |
| Calendly | `integrations_v2/providers/calendly/` |
| ClickUp | `integrations_v2/providers/clickup/` |
| GitLab | `integrations_v2/providers/gitlab/` ⚠️ self-hosted `server_url` SSRF (SEC-1) |
| HubSpot | `integrations_v2/providers/hubspot/` |
| Mailchimp | `integrations_v2/providers/mailchimp/` ⚠️ datacenter URL SSRF (SEC-1) |
| Microsoft Teams | `integrations_v2/providers/microsoft_teams/` |
| OpenAI v2 | `integrations_v2/providers/openai_v2/` |
| Pipedrive | `integrations_v2/providers/pipedrive/` |
| SendGrid | `integrations_v2/providers/sendgrid/` |
| Shopify | `integrations_v2/providers/shopify/` ⚠️ shop domain SSRF (SEC-1) |
| Supabase | `integrations_v2/providers/supabase/` ⚠️ `project_url` SSRF (SEC-1) |
| Telegram | `integrations_v2/providers/telegram/` |
| Trello | `integrations_v2/providers/trello/` |
| WooCommerce | `integrations_v2/providers/woocommerce/` ⚠️ `store_url` SSRF (SEC-1) |
| Zoom | `integrations_v2/providers/zoom/` |

### New Node Modules (9)

| Module | Description |
|---|---|
| `data_transform_nodes.py` | Data filter/sort/group_by/select/fill_na/unique/rename/drop/sample |
| `image_nodes.py` | Image resize/convert/crop/filter/rotate/flip/thumbnail (Pillow) |
| `postgres_nodes.py` | Postgres query node — see SEC-4 note on connection-host egress |
| `text_processing_nodes.py` | Text manipulation utilities |
| `archive_nodes.py` | Archive/zip utilities |
| `crypto_extra_nodes.py` | Extra crypto/hashing utilities |
| `system_extra_nodes.py` | System/OS utility nodes |
| `regex_nodes.py` | Regex match/replace/split nodes |
| `pdf_nodes.py` | PDF parsing/extraction nodes |
